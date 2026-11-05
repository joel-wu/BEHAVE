"""Observe compiler subscriptions without packing array storage into integers.

A selected write changes only its selected value. Project each subscription
onto that value, instead of shifting a word-sized change mask into a RAM-sized
integer. Notifications still occur at each write, including restored values.
"""

from ..symbolic import ir, typecheck as types
from .source_values import ArrayType


def mask_spans(mask):
    """Half-open runs of compiler-owned set bits; no symbolic wide integer."""
    offset = 0
    while mask:
        low = (mask & -mask).bit_length() - 1
        mask >>= low
        offset += low
        width = (mask ^ (mask + 1)).bit_length() - 1
        yield offset, offset + width
        mask >>= width
        offset += width


def _clip(value, width):
    clipped = ir.Op("ite", ir.Op("lt", value, ir.Lit(0)), ir.Lit(0),
        ir.Op("ite", ir.Op("gt", value, ir.Lit(width)), ir.Lit(width), value))
    # The expression is in [0,width] for every integer input. Carry that fact
    # through joined branch ranges; this saturation is identically redundant.
    return ir.Op("usat", clipped, ir.Lit(width.bit_length()))


def _relative_mask(spans, offset, width):
    ones = (1 << width) - 1
    if isinstance(offset, ir.Lit):
        bits = 0
        for low, high in spans:
            first, last = max(low - offset.value, 0), min(high - offset.value, width)
            if first < last:
                bits |= ((1 << (last - first)) - 1) << first
        return ir.Lit(bits)
    parts = []
    for low, high in spans:
        first = _clip(ir.Op("sub", ir.Lit(low), offset), width)
        last = _clip(ir.Op("sub", ir.Lit(high), offset), width)
        parts.append(ir.Op("and", ir.Op("ushl", ir.Lit(ones), first, ir.Lit(width)),
            ir.Op("inv", ir.Op("ushl", ir.Lit(ones), last, ir.Lit(width)))))
    return ir._balanced_op("or", parts, ir.Lit(0))


def _cell_spans(kind, offset, spans):
    if not isinstance(offset, ir.Lit):
        return ((0, kind.cells),)
    cells = []
    for low, high in spans:
        low, high = max(0, low - offset.value), min(kind.width, high - offset.value)
        if low >= high:
            continue
        first, last = low // kind.element.width, (high - 1) // kind.element.width + 1
        if cells and first <= cells[-1][1]:
            cells[-1] = cells[-1][0], last
        else:
            cells.append((first, last))
    return cells


def notify_value(kind, old, new, subscriptions, pending, temp, *, offset=None):
    """Set subscriber flags for the changed selected value at a bit offset.

    Recursive arrays keep their shape. Whole-array assignments compare only
    subscribed cells when the offset is static; selected scalar writes never
    visit or pack the other cells. All generated shifts have leaf-word width.
    """
    def masks(kind, path):
        before, after = old, new
        for index in path:
            before, after = ir.Sel(before, index), ir.Sel(after, index)
        changed = ir.Op("uwrap", ir.Op("xor", before, after), ir.Lit(kind.width))
        return {"change": changed, "rise": ir.Op("and", changed, after),
                "fall": ir.Op("and", changed, before)}
    return notify_projection(kind, masks, subscriptions, pending, temp, offset=offset)


def notify_projection(kind, masks, subscriptions, pending, temp, *, offset=None):
    """Project leaf event masks, including queued history, onto subscriptions."""
    commands = []
    offset = ir.Lit(0) if offset is None else offset
    groups = {}
    for subscription in subscriptions:
        groups.setdefault((subscription.mask, subscription.edge), []).append(
            pending[subscription.process])
    for (mask, edge), destinations in groups.items():
        spans = tuple(mask_spans(mask))
        # A notification is inside one atomic write/commit. The projected
        # value is unchanged while consumers' pending flags are set, so equal
        # subscriptions share one scan. A fresh flag avoids propagating one
        # consumer's already-pending state to the others.
        target = destinations[0] if len(destinations) == 1 else temp(kind=types.BOOLEAN)
        if len(destinations) > 1:
            commands.append(ir.Assign(target, ir.Lit(False)))

        def observe(kind, path, offset, *, spans=spans, edge=edge, target=target):
            if isinstance(kind, ArrayType):
                body = ir.Skip()
                for first, last in _cell_spans(kind, offset, spans):
                    if last - first == 1 and isinstance(offset, ir.Lit):
                        index = ir.Lit(first)
                        position = ir.Lit(offset.value + first * kind.element.width)
                        part = observe(kind.element, (*path, index), position)
                    else:
                        counter = temp()
                        index = ir.Op("add", ir.Var(counter), ir.Lit(first)) if first else ir.Var(counter)
                        position = ir.Op("add", offset, ir.Op("mul", index, ir.Lit(kind.element.width)))
                        part = ir.For(counter, last - first, observe(kind.element, (*path, index), position))
                    body = ir.Seq(body, part)
                return body
            watched = _relative_mask(spans, offset, kind.width)
            changed = masks(kind, path)[edge]
            hit = ir.Op("truth", ir.Op("and", changed, watched))
            return ir.If(hit, ir.Assign(target, ir.Lit(True)), ir.Skip())

        commands.append(observe(kind, (), offset))
        if len(destinations) > 1:
            commands.append(ir.If(ir.Var(target), ir._sequence(
                [ir.Assign(name, ir.Lit(True)) for name in destinations]), ir.Skip()))
    return commands
