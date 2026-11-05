"""Constant case matching and native qualified-case failure predicates.

Read compiler values, not source spelling or RTLIL dash patterns. X remains an
exact non-match in casez; only the wildcard selected by the HDL construct is
ignored. Uniqueness counts matching items, not labels within one item.
Ordinary expression lowering owns selector definedness and effects.
"""

import re
from collections import ChainMap
from dataclasses import dataclass

from ..symbolic import ir
from ..symbolic import ranges
from .source_integral import four_state_literal, integer_literal, integral_type


def constant_labels_exhaustive(kind, rows, *, bounds=None):
    """Prove coverage of the compiler's complete defined integral value domain.

    Optional bounds describe the typed selector expression over unrestricted
    operands (including widening conversions), not a reachable-state invariant.
    No state samples, observed inputs or enum-value restriction are premises.
    Duplicate/out-of-domain labels cannot fill a missing value. Effectful or
    nonconstant labels keep the original ordered dispatch and fallback.
    """
    low, high = kind.interval if bounds is None else bounds
    values = set()
    for row in rows:
        for effects, expression in row:
            if effects:
                return False
            bound = ranges.bounds_of_expression(expression, {})
            if bound is None or bound.lo is None or bound.lo != bound.hi:
                return False
            if low <= bound.lo <= high:
                values.add(bound.lo)
    return len(values) == high - low + 1


@dataclass(frozen=True, slots=True)
class CasePattern:
    width: int
    value: int
    mask: int
    impossible: bool = False

    def predicate(self, selector):
        return (ir.Lit(False) if self.impossible else
                ir.Op('eq', ir.Op('and', selector, ir.Lit(self.mask)), ir.Lit(self.value)))

    def overlaps(self, other):
        return not (self.impossible or other.impossible or
                    (self.value ^ other.value) & self.mask & other.mask)

    @property
    def size(self):
        return 0 if self.impossible else 1 << (self.width - self.mask.bit_count())


def constant_pattern(lowerer, label, condition, width):
    literal = label
    while literal.get('kind') == 'Conversion':
        literal = literal['operand']
    if literal.get('kind') not in {'IntegerLiteral', 'UnbasedUnsizedIntegerLiteral'}:
        return lowerer.unsupported(label, 'wildcard labels need a compiler-resolved literal')
    if integral_type(label['type'], lowerer.symbols).width != width:
        return lowerer.unsupported(label, 'wildcard labels need the compiler common comparison width')
    serialized = label.get('constant') if literal is not label else label['value']
    if serialized is None:
        return lowerer.unsupported(label, 'wildcard conversion needs its compiler constant value')
    full = (1 << width) - 1
    if not four_state_literal(serialized):
        return CasePattern(width, integer_literal(serialized) & full, full)
    match = re.fullmatch(r"(\d+)'[sS]?([bBoOhH])([0-9a-fA-F_xXzZ?]+)", serialized)
    if match is None or int(match[1]) != width:
        return lowerer.unsupported(label, 'wildcard pattern needs its fully sized compiler value')
    radix = {'b': 1, 'o': 3, 'h': 4}[match[2].lower()]
    digits = match[3].replace('_', '').lower()
    bits = ''.join(digit * radix if digit in 'xz?' else
                   f'{int(digit, 1 << radix):0{radix}b}' for digit in digits)
    # SVInt serialization may omit leading digits; X/Z extends itself, while
    # an ordinary leading digit is zero-extended in this unsigned bit pattern.
    fill = bits[0] if bits[0] in 'xz?' else '0'
    bits = bits.rjust(width, fill)[-width:]
    wildcards = 'xz?' if condition == 'WildcardXOrZ' else 'z?'
    impossible = 'x' in bits and 'x' not in wildcards
    value = mask = 0
    for bit in bits:
        value <<= 1
        mask <<= 1
        if bit not in wildcards:
            mask |= 1
            value |= bit == '1'
    return CasePattern(width, value, mask, impossible)


def _multiple_items(matches):
    """At least two *items* match; labels in one item are alternatives.

    Combine (any, multiple) summaries in a balanced tree. This takes linear
    space and logarithmic depth, rather than constructing every pair of items.
    These are total predicates over a once-evaluated selector, not HDL effects.
    """
    layer = [(match, ir.Lit(False)) for match in matches]
    while len(layer) > 1:
        following = []
        for index in range(0, len(layer) - 1, 2):
            left, right = layer[index], layer[index + 1]
            following.append((ir.Op('lor', left[0], right[0]),
                ir.Op('lor', ir.Op('lor', left[1], right[1]),
                      ir.Op('land', left[0], right[0]))))
        if len(layer) % 2:
            following.append(layer[-1])
        layer = following
    return layer[0][1] if layer else ir.Lit(False)


def _qualified_checks(lowerer, node, matches, *, disjoint, exhaustive):
    """Return checks for the statement owner to place at native failure points.

    Priority's no-match action belongs inside the missing arm; unique checks
    run after dispatch. These predicates neither assume success nor execute
    effects. The owner retains early exits and source failure observations.
    """
    check = node.get('check')
    if check in {None, 'None'}:
        return []
    if check not in {'Unique', 'Unique0', 'Priority'}:
        return lowerer.unsupported(node, 'case check semantics are not implemented')
    predicates = []
    if not disjoint and check in {'Unique', 'Unique0'}:
        predicates.append((ir.Op('not', _multiple_items(matches)), 'overlap'))
    if check != 'Unique0' and not node.get('defaultCase') and not exhaustive:
        predicates.append((ir._balanced_op('lor', matches, ir.Lit(False)), 'no-match'))
    return predicates


def qualified_constant_check(lowerer, node, selector, items):
    if node.get('check') in {None, 'None'}:
        return []
    rows = []
    distinct, disjoint = set(), True
    for labels in items:
        row = set()
        for commands, value in labels:
            bound = ranges.bounds_of_expression(value, {})
            if commands or bound is None or bound.lo is None or bound.lo != bound.hi:
                return lowerer.unsupported(node, 'qualified cases need effect-free constant labels')
            row.add(bound.lo)
        disjoint &= not bool(distinct & row)
        distinct.update(row)
        rows.append(ir._balanced_op('lor', (ir.Op('eq', selector, ir.Lit(value))
                                            for value in sorted(row)), ir.Lit(False)))
    bound = ranges.bounds_of_expression(selector,
        ChainMap(lowerer.loop_ranges, lowerer.snapshot_ranges, lowerer.ranges))
    exhaustive = (bound is not None and bound.lo is not None and bound.hi is not None
        and sum(bound.lo <= value <= bound.hi for value in distinct) == bound.hi - bound.lo + 1)
    return _qualified_checks(lowerer, node, rows, disjoint=disjoint, exhaustive=exhaustive)


def qualified_pattern_check(lowerer, node, selector, patterns):
    if node.get('check') in {None, 'None'}:
        return []
    rows = [tuple(dict.fromkeys(row)) for row in patterns]
    disjoint = all(not left.overlaps(right)
        for index, row in enumerate(rows) for other in rows[index+1:]
        for left in row for right in other)
    # Cardinality is a sufficient proof only when every distinct cube is
    # disjoint, including cubes within one item. Overlapping alternatives are
    # legal; the shared predicate obligation handles their union exactly.
    flat = tuple(dict.fromkeys(pattern for row in rows for pattern in row))
    exhaustive = (bool(flat) and sum(pattern.size for pattern in flat) == 1 << flat[0].width
        and all(not left.overlaps(right)
                for index, left in enumerate(flat) for right in flat[index+1:]))
    matches = [ir._balanced_op('lor', (pattern.predicate(selector) for pattern in row),
                               ir.Lit(False)) for row in rows]
    return _qualified_checks(lowerer, node, matches, disjoint=disjoint, exhaustive=exhaustive)
