"""The Z3 algebra shared by the RTL symbolic protocol, monitor and transaction products.

Monitors, adapters and declared transaction goals advance one finite transition
over either concrete values or these typed Z3 terms; this class supplies the
Z3 side, in an explicit context.
"""

from __future__ import annotations

import z3
from z3.z3types import Ast

__all__ = ["Z3Algebra"]


class Z3Algebra:
    """The shared ready/valid transition algebra over Z3 terms."""

    def __init__(self, context=None):
        self.context = context

    def bool(self, value):
        return z3.BoolVal(bool(value), ctx=self.context)

    def integer(self, value):
        return z3.IntVal(value, ctx=self.context)

    def literal(self, value):
        """Finite-table literal spelling shared with declarative goal semantics."""

        if type(value) is bool:
            return self.bool(value)
        if type(value) is int and not isinstance(value, bool):
            return self.integer(value)
        raise TypeError(
            f"the RTL symbolic algebra cannot encode {type(value).__name__}")

    @staticmethod
    def zero_like(value):
        if z3.is_bool(value):
            return z3.BoolVal(False, ctx=value.ctx)
        if z3.is_bv(value):
            return z3.BitVecVal(0, value.size(), ctx=value.ctx)
        if z3.is_int(value):
            return z3.IntVal(0, ctx=value.ctx)
        raise TypeError(f"the symbolic monitor cannot zero {value.sort()}")

    def land(self, *values):
        return self._connective(values, self.bool(True), z3.z3core.Z3_mk_and)

    def lor(self, *values):
        return self._connective(values, self.bool(False), z3.z3core.Z3_mk_or)

    @staticmethod
    def _connective(values, identity, constructor):
        """Build the same typed Boolean AST without general sort coercion.

        Monitor operations already produce typed terms. Keep argument order and
        the identity operand exactly as before; native Z3 still checks Boolean
        sorts. Contexts must match before handing native pointers to Z3.
        """
        terms = tuple(z3.BoolVal(value, ctx=identity.ctx) if type(value) is bool else value
                      for value in values) + (identity,)
        if any(not isinstance(term, z3.ExprRef) or term.ctx != identity.ctx for term in terms):
            raise z3.Z3Exception("Boolean terms must belong to the monitor's Z3 context")
        arguments = (Ast * len(terms))(*(term.as_ast() for term in terms))
        return z3.BoolRef(constructor(identity.ctx_ref(), len(terms), arguments), identity.ctx)

    def lnot(self, value):
        return z3.Not(self.bool(value) if type(value) is bool else value)

    @staticmethod
    def eq(left, right):
        return left == right

    equal = eq

    @staticmethod
    def gt(left, right):
        return left > right

    @staticmethod
    def add(left, right):
        return left + right

    @staticmethod
    def sub(left, right):
        return left - right

    @staticmethod
    def div(left, right):
        return left / right

    @staticmethod
    def mod(left, right):
        return left % right

    @staticmethod
    def ite(condition, when_true, when_false):
        return z3.If(condition, when_true, when_false)

    def count(self, values):
        terms = tuple(z3.If(value, self.integer(1), self.integer(0)) for value in values)
        # Z3's empty Sum returns a Python int, not an integer term. Keep the
        # empty finite collection in the same carrier as every other count.
        return z3.Sum(*terms) if terms else self.integer(0)


