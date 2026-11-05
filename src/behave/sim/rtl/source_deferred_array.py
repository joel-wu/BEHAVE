"""Word-local NBA summaries for recursive unpacked storage.

The scalar queue's first/last/written/changed semantics apply independently at
each leaf. Selected writes never pack or visit unrelated RAM cells. Commit is
a typed masked replacement; event subscriptions read only their projection.
"""

from dataclasses import dataclass

from ..symbolic import ir
from ..symbolic.uniform_array import UniformArray
from .source_deferred import DeferredWrite
from .source_integral import IntegralType
from .source_notifications import notify_projection
from .source_packed import merge_bits
from .source_values import ArrayType, zero_value


def _selected(name, path):
    value = ir.Var(name)
    for index in path:
        value = ir.Sel(value, index)
    return value


def _store(name, path, value):
    for depth in range(len(path) - 1, -1, -1):
        value = ir.Op("store", _selected(name, path[:depth]), path[depth], value)
    return ir.Assign(name, value)


def _unsigned(kind):
    return (ArrayType(kind.left, kind.right, _unsigned(kind.element)) if isinstance(kind, ArrayType)
            else IntegralType(kind.width, False))


@dataclass(frozen=True, slots=True, kw_only=True)
class ArrayDeferredWrite(DeferredWrite):
    rises: str
    falls: str

    @property
    def registers(self):
        return (*super(ArrayDeferredWrite, self).registers, self.rises, self.falls)

    @property
    def mask_kind(self):
        return _unsigned(self.array)

    def empty(self):
        def zero(kind):
            return UniformArray.uniform(kind.cells, zero(kind.element)) if isinstance(kind, ArrayType) else 0
        return zero(self.array)

    def initialize(self, temp=None):
        return (ir.Assign(self.data, ir.Var(self.target)), ir.Assign(self.first, ir.Var(self.target)),
                ir.Assign(self.valid, ir.Lit(False)), *self.clear_masks())

    def clear_masks(self):
        return [ir.Assign(name, zero_value(self.array))
                for name in (self.varied, self.mask, self.rises, self.falls)]

    def enqueue_selected(self, value, kind, path, mask, temp):
        if isinstance(kind, ArrayType):
            counter = temp()
            index = ir.Var(counter)
            return [ir.For(counter, kind.cells, ir._sequence(self.enqueue_selected(
                ir.Sel(value, index), kind.element, (*path, index), None, temp)))]
        if self.whole and mask is not None and not (
                isinstance(mask, ir.Lit) and mask.value == (1 << kind.width) - 1):
            raise ValueError('a whole-word deferred queue cannot enqueue a partial write')
        mask = ir.Lit((1 << kind.width) - 1) if mask is None else mask
        current = lambda name: _selected(name, path)
        put = lambda name, value: _store(name, path, value)
        wrap = lambda value: ir.Op("uwrap", value, ir.Lit(kind.width))
        repeated = ir.Op("and", mask, current(self.mask))
        first = ir.Op("and", mask, ir.Op("inv", current(self.mask)))
        different = ir.Op("and", repeated, ir.Op("xor", current(self.first), value))
        transition = ir.Op("and", repeated, ir.Op("xor", current(self.data), value))
        return [
            put(self.varied, wrap(ir.Op("or", current(self.varied), different))),
            put(self.rises, wrap(ir.Op("or", current(self.rises), ir.Op("and", transition, value)))),
            put(self.falls, wrap(ir.Op("or", current(self.falls), ir.Op("and", transition, current(self.data))))),
            put(self.first, merge_bits(kind, current(self.first), value, first)),
            put(self.data, merge_bits(kind, current(self.data), value, mask)),
            put(self.mask, wrap(ir.Op("or", current(self.mask), mask))),
            ir.Assign(self.valid, ir.Op("lor", ir.Var(self.valid), ir.Op("truth", mask)))]

    def event_masks(self, kind, path):
        current = lambda name: _selected(name, path)
        changed = ir.Op("and", current(self.mask),
            ir.Op("uwrap", ir.Op("xor", current(self.target), current(self.first)), ir.Lit(kind.width)))
        return {"change": ir.Op("or", changed, current(self.varied)),
                "rise": ir.Op("or", ir.Op("and", changed, current(self.first)), current(self.rises)),
                "fall": ir.Op("or", ir.Op("and", changed, current(self.target)), current(self.falls))}

    def notifications(self, subscriptions, pending, temp):
        return notify_projection(self.array, self.event_masks, subscriptions, pending, temp)

    def committed_value(self, current):
        leaf = self.array
        while isinstance(leaf, ArrayType):
            leaf = leaf.element
        return ir.Op("smerge" if leaf.signed else "umerge", current, ir.Var(self.data),
                     ir.Var(self.mask), ir.Lit(leaf.width))

    def commit(self, changed, temp):
        def visit(kind, path):
            if not isinstance(kind, ArrayType):
                return _store(changed, path, self.event_masks(kind, path)["change"])
            counter = temp()
            return ir.For(counter, kind.cells, visit(kind.element, (*path, ir.Var(counter))))
        return (ir.Assign(changed, zero_value(self.array)), ir.If(ir.Var(self.valid), ir._sequence([
            visit(self.array, ()), ir.Assign(self.target, self.committed_value(ir.Var(self.target)))]), ir.Skip()))
