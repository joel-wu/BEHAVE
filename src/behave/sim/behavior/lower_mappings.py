"""Finite persistent integer maps, lowered to ordinary SolverIR arrays.

Storage grows with bounded source writes, never with the numeric key domain.
Live entries remain in insertion order; overwrite keeps its position and
deletion compacts the suffix. No new IR primitive or query backend is needed.
The carrier owns allocation and map operations. Receiver recognition and
source rebinding live here too; the state owner publishes persistent bindings.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from ..symbolic import ir, typecheck as types
from . import lower_static_refs
from ..symbolic import ranges
from .extract import LoweringError
from .source_calls import has_plain_positional_arity, record_items
from .source_options import OptionValue
from ..symbolic.value_contract import NONE, SCALAR, ValueContract


@dataclass(frozen=True)
class MappingBuilder:
    """One map's count and parallel key/value columns, not a lowerer mixin."""

    count: str
    keys: str
    values: str
    contract: ValueContract

    @property
    def capacity(self):
        return max(1, self.contract.maximum)

    @staticmethod
    def allocate(low, contract, node):
        if contract.element is not None and contract.element != SCALAR:
            raise LoweringError(node, "persistent maps need integer values",
                                code="mapping-value-unsupported")
        builder = MappingBuilder(low.storage.fresh(), low.storage.fresh(), low.storage.fresh(), contract)
        for variable, shape in ((builder.count, ()),
                                (builder.keys, (builder.capacity,)),
                                (builder.values, (builder.capacity,))):
            low.storage.require_shape(variable, shape, node)
            low.flow.contracts[variable] = ValueContract.fixed(shape)
            if shape:
                low.flow.lengths[variable] = shape
        low.storage.widen_carrier_range(builder.count, ranges.Range(0, contract.maximum))
        return builder

    def _find(self, low, key):
        terms = [ir.Op("land", ir.Op("lt", ir.Lit(i), ir.Var(self.count)),
                       ir.Op("eq", ir.Sel(ir.Var(self.keys), ir.Lit(i)), key))
                 for i in range(self.capacity)]
        index = ir.Lit(0)
        for i, matches in reversed(tuple(enumerate(terms))):
            index = ir.Op("ite", matches, ir.Lit(i), index)
        return low._capture(lower_static_refs.disjunction(terms)), low._capture(index)

    def contains(self, low, key, source):
        _integer(low, source, key)
        found, _index = self._find(low, key)
        return found

    def subscript(self, low, node, *, remove=False):
        """Read/delete share receiver-first argument evaluation and KeyError."""
        low.expr(node.value)
        key, = _arguments(low, node, (node.slice,))
        return self._read(low, node, key, remove=remove)

    def _read(self, low, node, key, *, default=None, remove=False):
        found, index = self._find(low, key)
        if default is None:
            low._guard(ir.Op("not", found), low._safety_goal(node, "indexerror"),
                       "KeyError", node)
        elif remove:
            low._discharge(low._safety_goal(node, "indexerror"),
                           "dict.pop with a default does not fail for a missing key")
        selected = ir.Sel(ir.Var(self.values), index)
        result = low._capture(selected if default is None else
                              ir.Op("ite", found, selected, default))
        if remove:
            def shifted(variable):
                return ir.Op("arraylit", *[
                    ir.Op("ite", ir.Op("land", found,
                                      ir.Op("ge", ir.Lit(i), index)),
                          ir.Sel(ir.Var(variable), ir.Lit(i + 1)),
                          ir.Sel(ir.Var(variable), ir.Lit(i)))
                    if i + 1 < self.capacity else ir.Sel(ir.Var(variable), ir.Lit(i))
                    for i in range(self.capacity)])
            low.before(ir._sequence([
                ir.Assign(self.keys, shifted(self.keys)),
                ir.Assign(self.values, shifted(self.values)),
                ir.Assign(self.count, ir.Op("sub", ir.Var(self.count),
                                           ir.Op("ite", found, ir.Lit(1), ir.Lit(0)))),
            ]))
        return result

    def store(self, low, target, value, source):
        # Python evaluates and retains the RHS before evaluating the target.
        value = low._capture(_integer(low, source, value))
        low.expr(target.value)
        key, = _arguments(low, target, (target.slice,))
        found, index = self._find(low, key)
        low._guard(ir.Op("land", ir.Op("not", found),
                        ir.Op("ge", ir.Var(self.count), ir.Lit(self.contract.maximum))),
                   low._safety_goal(target, "capacity"), "CapacityExceeded", target)
        low._discharge(low._safety_goal(target, "indexerror"),
                       "an integer dictionary assignment inserts or replaces its key")
        index = low._capture(ir.Op("ite", found, index, ir.Var(self.count)))
        low._record_range(self.keys, key, join=True)
        low._record_range(self.values, value, join=True)
        return ir._sequence([
            ir.Store(self.keys, index, key),
            ir.Store(self.values, index, value),
            ir.Assign(self.count, ir.Op("add", ir.Var(self.count),
                                       ir.Op("ite", found, ir.Lit(0), ir.Lit(1)))),
        ])

    def call(self, low, node):
        low.expr(node.func.value)
        name = node.func.attr
        if name == "clear" and has_plain_positional_arity(node, 0):
            low.before(ir.Assign(self.count, ir.Lit(0)))
            return OptionValue(ir.Lit(False), None, NONE)
        if name not in {"get", "pop"} or not has_plain_positional_arity(node, 1, 2):
            raise LoweringError(node, "unrepresented persistent map operation",
                                code="mapping-operation-unsupported")
        arguments = _arguments(low, node, node.args)
        if name == "get" and len(arguments) == 1:
            found, index = self._find(low, arguments[0])
            return OptionValue(found, low._capture(ir.Sel(ir.Var(self.values), index)),
                               ValueContract.option(SCALAR))
        return self._read(low, node, arguments[0],
                          default=arguments[1] if len(arguments) == 2 else None,
                          remove=name == "pop")


def receiver(low, node):
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self" and not low._name_is_local("self")):
        return low.storage.carrier(MappingBuilder, low.storage.state_name(node.attr))
    return None


def _integer(low, node, value):
    if (not isinstance(value, ir.EXPRS) or low._shape(value)
            or low._source_scalar_type_for_value(node, value) == types.BOOLEAN):
        raise LoweringError(node, "persistent map keys and values need integers",
                            code="mapping-value-unsupported")
    return value


def _arguments(low, node, arguments):
    # Rebinding a receiver while evaluating a key needs an object-identity
    # carrier. Do not silently turn the retained receiver into its new value.
    if any(low._effectful(argument) for argument in arguments):
        raise LoweringError(node, "an effectful map argument needs a retained receiver identity",
                            code="mapping-receiver-effects-unsupported")
    return tuple(low._capture(_integer(low, argument, low.expr(argument)))
                 for argument in arguments)


def replace(low, target, source):
    builder = receiver(low, target)
    pairs = record_items(source, is_builtin=low._spelling_is_builtin)
    if pairs != ():
        raise LoweringError(source, "map replacement needs a fresh empty dictionary",
                            code="mapping-replacement-unsupported")
    return low._define_state_attribute(target, ir.Assign(builder.count, ir.Lit(0)))
