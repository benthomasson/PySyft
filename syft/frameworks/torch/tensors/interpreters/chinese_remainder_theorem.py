import torch
import math
import itertools

from syft.frameworks.torch.tensors.interpreters.abstract import AbstractTensor
from syft.frameworks.torch.tensors.interpreters.precision import FixedPrecisionTensor
from syft.frameworks.torch.overload_torch import overloaded


class CRTTensor(AbstractTensor):
    """ A CRT tensor is a tensor whose values are represented as their remainders modulo several pairwise coprime numbers.
    The true tensor values lie in the Zq field where q is the product of all the modulos.
    A CRT tensor then represent a modular system of equations:
        x = a_0 mod f_0
        ...
        x = a_n mod f_n
    The Chineses Remainder Theorem (that also gives his name to this tensor) assert that exactly one x in Zq with q = f_0 * ... * f_n
    satisfies this system. This x is the real value represented by the tensor.
    This tensor makes it possible to represent big numbers and to avoid overflows while manipulating them.
    It also makes additions, subtractions, and multiplications of huge numbers quite efficient.
    """

    def __init__(
        self,
        residues: dict = None,
        base=None,
        precision_fractional=None,
        owner=None,
        id=None,
        tags: set = None,
        description: str = None,
    ):
        super().__init__(owner=owner, id=id, tags=tags, description=description)

        # Check that all the modulos are pairwise coprime
        if residues is not None:
            for pair in itertools.combinations(residues.keys(), r=2):
                assert (
                    math.gcd(pair[0], pair[1]) == 1
                ), f"{pair[0]} and {pair[1]} are not coprime, you cannot build a CRTTensor with these as modulos"

        # Check that all the residues have the same precision
        self.base = base
        self.precision_fractional = precision_fractional
        if residues is not None:
            b = next(iter(residues.values())).child.base
            prec_frac = next(iter(residues.values())).child.precision_fractional
            for r in residues.values():
                assert r.child.base == b
                assert r.child.precision_fractional == prec_frac

            if base is not None:
                assert b == base
            if precision_fractional is not None:
                assert prec_frac == precision_fractional
            self.base = b
            self.precision_fractional = prec_frac

        # Check that all the shapes are the same while filling tensor
        if residues is not None:
            res_shape = next(iter(residues.values())).shape
            self.child = {}
            for f, r in residues.items():
                assert isinstance(r.child, FixedPrecisionTensor)
                assert f == r.child.field
                self.child[f] = r
                assert (
                    r.shape == res_shape
                ), "All residue tensors of CRTTensor must have the same shape"

    def get_class_attributes(self):
        return {"base": self.base, "precision_fractional": self.precision_fractional}

    def __str__(self):
        type_name = type(self).__name__
        out = f"[" f"{type_name}]"
        for mod, res in self.child.items():
            out += f"\n\t -> {mod}: {res}"
        return out

    def __repr__(self):
        return self.__str__()

    @property
    def shape(self) -> torch.Size:
        return next(iter(self.child.values())).shape

    @overloaded.method
    def eq(self, self_, other_):
        assert (
            self_.keys() == other_.keys()
        ), "Cannot compare 2 CRT that don't have the same modulos"

        result = torch.ones(self.shape).fix_prec(
            base=self.base, precision_fractional=self.precision_fractional
        )
        for mod in self_.keys():
            result *= (self_[mod] == other_[mod]).long()

        dict_result = {}
        for mod in self_.keys():
            result.field = mod
            dict_result[mod] = result

        return dict_result

    __eq__ = eq

    @overloaded.method
    def add(self, self_, other):
        assert self_.keys() == other.keys(), "Cannot add 2 CRT that don't have the same modulos"

        result = {}

        for mod in self_.keys():
            result[mod] = self_[mod] + other[mod]

        return result

    __add__ = add

    @overloaded.method
    def sub(self, self_, other):
        assert (
            self_.keys() == other.keys()
        ), "Cannot subtract 2 CRT that don't have the same modulos"

        result = {}

        for mod in self_.keys():
            result[mod] = self_[mod] - other[mod]

        return result

    __sub__ = sub

    @overloaded.method
    def mul(self, self_, other):
        assert (
            self_.keys() == other.keys()
        ), "Cannot multiply 2 CRT that don't have the same modulos"

        result = {}

        for mod in self_.keys():
            result[mod] = self_[mod] * other[mod]

        return result

    __mul__ = mul

    @overloaded.method
    def sum(self, self_, *args, **kwargs):
        result = {}

        for mod in self_.keys():
            result[mod] = self_[mod].sum() % mod  # TODO why % mod is needed?

        return result

    def solve_system(self):
        """ Build the tensor in Zq with q = prod(self.child.keys())
        satisfying the modular system represented by the tensor.
        The algorithm consists in:
        1) Compute N = prod(self.child.keys())
        2) yi = N / ni where the ni's are modulos in the system
        3) zi = yi^(-1) mod ni (we know zi exists because all the original modulos are pairwise coprime
        4) The result is x = sum(ai * yi * zi) where ai is the residue modulo ni in the system
        """
        N = 1
        for mod in self.child.keys():
            N *= mod

        def modular_inverse(a, b):
            """ Computes the modular inverse x = a^(-1) mod b
            with Euclid's extended algorithm.
            """
            b0 = b
            x0, x1 = 0, 1
            if b == 1:
                return 1
            while a > 1:
                q = a // b
                a, b = b, a % b
                x0, x1 = x1 - q * x0, x0
            if x1 < 0:
                x1 += b0
            return x1

        res = 0
        for mod, ai in self.child.items():
            ai = (ai.float_prec() % mod).long()
            yi = N // mod
            zi = modular_inverse(yi, mod)
            res += ai * yi * zi

        return FixedPrecisionTensor(
            field=N, base=self.base, precision_fractional=self.precision_fractional
        ).on(res % N)

    @staticmethod
    @overloaded.module
    def torch(module):
        def add(self, other):
            return self.__add__(other)

        module.add = add

        def sub(self, other):
            return self.__sub__(other)

        module.sub = sub

        def mul(self, other):
            return self.__mul__(other)

        module.mul = mul

        def sum(self, *args, **kwargs):
            return self.sum(*args, **kwargs)

        module.sum = sum
