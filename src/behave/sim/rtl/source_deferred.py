"""Exact typed NBA queue summary, consumed at deferred commit.

For precommit value c and queued values v1,...,vn, the bits that change at least
once are (c xor v1) or OR_i(v1 xor vi). A bit is absent exactly when c and every
queued value agree on it. Keeping only vn loses changes restored before a
consumer runs. This summary retains those changes without expanding the queue.

For partial writes the same identity applies independently to each bit's first
and subsequent writes. Unwritten bits retain their value at commit, not enqueue.
Ordering between competing writers and scheduling consumers is not inferred
here. In particular, enqueueing never writes the source target.
"""

from dataclasses import dataclass

from ..symbolic import ir
from .source_packed import merge_bits
from .source_values import ArrayType


@dataclass(frozen=True, slots=True)
class DeferredWrite:
    target: str
    data: str
    valid: str
    first: str
    varied: str
    mask: str
    width: int
    signed: bool = False
    array: ArrayType | None = None
    # Certified over every write: each selected integral word is replaced
    # completely. For arrays this does NOT assert that every cell is written.
    # The generic/default queue permits partial and symbolic write masks.
    whole: bool = False

    @property
    def registers(self):
        return self.data, self.valid, self.first, self.varied, self.mask

    def wrap(self, value):
        return ir.Op("swrap" if self.signed else "uwrap", value, ir.Lit(self.width))

    def current_bits(self, temp):
        return [], ir.Var(self.target)

    def assign_bits(self, value, temp):
        return [ir.Assign(self.target, value)]

    def initialize(self, temp=None):
        before, current = self.current_bits(temp)
        return (*before, ir.Assign(self.data, current),
                ir.Assign(self.valid, ir.Lit(False)),
                ir.Assign(self.first, current),
                ir.Assign(self.varied, ir.Lit(0)),
                ir.Assign(self.mask, ir.Lit(0)))

    def enqueue(self, value, mask=None):
        # value has already been evaluated by the source expression lowerer.
        # Reusing this pure IR value must not evaluate the source RHS again.
        whole = mask is None or isinstance(mask, ir.Lit) and mask.value == (1 << self.width) - 1
        if self.whole and not whole:
            raise ValueError('a whole-word deferred queue cannot enqueue a partial write')
        mask = ir.Lit((1 << self.width) - 1) if mask is None else mask
        repeated = ir.Op("and", mask, ir.Var(self.mask))
        first = ir.Op("and", mask, ir.Op("inv", ir.Var(self.mask)))
        different = ir.Op("and", repeated, ir.Op("xor", ir.Var(self.first), value))
        return [ir.Assign(self.varied, ir.Op("or", ir.Var(self.varied), different)),
                ir.Assign(self.first, merge_bits(self, ir.Var(self.first), value, first)),
                ir.Assign(self.data, merge_bits(self, ir.Var(self.data), value, mask)),
                ir.Assign(self.mask, ir.Op("or", ir.Var(self.mask), mask)),
                ir.Assign(self.valid, ir.Op("truth", ir.Var(self.mask)))]

    def committed_value(self, current):
        return (self.wrap(ir.Var(self.data)) if self.whole else
                merge_bits(self, current, ir.Var(self.data), ir.Var(self.mask)))

    def commit(self, changed, temp=None):
        # Read target now, after all active commands, not when v1 was enqueued.
        before, current = self.current_bits(temp)
        different = ir.Op("and", ir.Var(self.mask),
                          ir.Op("xor", current, ir.Var(self.first)))
        mask = ir.Op("uwrap", ir.Op("or", different, ir.Var(self.varied)), ir.Lit(self.width))
        commands = self.assign_bits(self.committed_value(current), temp)
        body = ir.Assign(changed, mask)
        for command in commands:
            body = ir.Seq(body, command)
        for command in reversed(before):
            body = ir.Seq(command, body)
        return (ir.Assign(changed, ir.Lit(0)), ir.If(ir.Var(self.valid),
            body, ir.Skip()))
