"""Definite source bits, separate from the total IR's private storage carrier.

A selected store needs a containing IR value even when the HDL has not assigned
its other cells. Scratch zeroes supply that carrier, never an HDL default: each
source read must first prove that its selected bits are defined. Predicates use
the existing activation-domain proof, including original branches and loops.
"""

from ..symbolic import ir
from ..core.refusal import Refusal
from .source_values import ArrayType, zero_value


def default_defined(node, symbols):
    """Only the compiler's two-state type supplies an implicit defined zero."""
    while True:
        if isinstance(node, str):
            node = symbols[node.split(' ', 1)[0]]
        node = symbols.get(str(node.get('addr')), node)
        if node.get('kind') == 'TypeAlias':
            node = node['target']
        elif node.get('kind') == 'FixedSizeUnpackedArrayType':
            node = node['elementType']
        else:
            return node.get('isFourState') is False


class DefinedStorage:
    def __init__(self, temp):
        self.temp = temp
        self.cells = {}
        self.obligations = {}
        self.private = set()

    def declare(self, name, kind, *, role='local', defined=False):
        mask = self.temp()
        self.private.add(mask)
        self.cells[name] = mask, kind, role
        return [ir.Assign(name, zero_value(kind)),
                ir.Assign(mask, ir.Lit((1 << kind.width)-1 if defined else 0))]

    @staticmethod
    def selected_mask(bound):
        offset = ir.Lit(0)
        for selection in bound.selections:
            stride = selection.owner.element.width if isinstance(selection.owner, ArrayType) else 1
            offset = ir.Op('add', offset, ir.Op('mul', selection.index, ir.Lit(stride)))
        bits = ir.Lit((1 << bound.value_type.width)-1)
        return ir.Op('shl', bits, offset)

    def read(self, bound):
        cell = self.cells.get(bound.name)
        if cell is None:
            return []
        mask, _, role = cell
        selected = self.selected_mask(bound)
        valid = ir.Op('eq', ir.Op('and', ir.Var(mask), selected), selected)
        check = ir.Assign(self.temp(boolean=True), valid)
        self.private.add(check.var)
        self.obligations[id(check)] = (valid, Refusal(
            f'rtl-source-ir-{role}-undefined',
            f'source {role} storage is not definitely assigned before use or exit',
            node_kind=bound.source.get('kind')))
        return [check]

    def write(self, bound):
        cell = self.cells.get(bound.name)
        if cell is None:
            return []
        mask, _, _ = cell
        if not bound.selections:
            return [ir.Assign(mask, ir.Lit((1 << bound.kind.width)-1))]
        return [ir.Assign(mask, ir.Op('or', ir.Var(mask), self.selected_mask(bound)))]

    def erase(self, body):
        """After domain proof, remove bookkeeping that no source value reads.

        Selectors and source effects already have their own ordered commands;
        these private assignments only consume their values. Keep all original
        storage writes, occurrence marks, failure commands and loop counts.
        """
        rewritten = {}
        for command in reversed(list(ir.walk(body))):
            if isinstance(command, ir.Assign) and command.var in self.private:
                result = ir.Skip()
            elif isinstance(command, ir.Seq):
                first, second = rewritten[id(command.first)], rewritten[id(command.second)]
                result = (second if isinstance(first, ir.Skip) else first
                          if isinstance(second, ir.Skip) else ir.Seq(first, second))
            elif isinstance(command, ir.If):
                yes, no = rewritten[id(command.then)], rewritten[id(command.orelse)]
                result = (ir.Skip() if isinstance(yes, ir.Skip) and isinstance(no, ir.Skip)
                          else ir.If(command.guard, yes, no))
            elif isinstance(command, ir.For):
                # Source activation loops have no CFG-derived flow metadata.
                result = ir.For(command.index, command.trip_count, rewritten[id(command.body)])
            else:
                result = command
            rewritten[id(command)] = result
        return rewritten[id(body)]
