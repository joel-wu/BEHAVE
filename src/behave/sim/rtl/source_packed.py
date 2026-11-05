"""Pure packed-bit access shared by source assignments and deferred commits."""

from ..symbolic import ir
from ..symbolic.capabilities import SCALAR_BITS


def literal_window(value, offset, owner_width, selected_width):
    """Read an immutable packed value without carrying the whole ROM scalar.

    The caller must retain the original in-range selection obligation. Each
    cell contains two adjacent power-of-two words, so even an unaligned read
    fits in one cell. Padding the final cell does not admit out-of-range reads.
    This is a value representation only: no new source events or state.
    """
    if not isinstance(value, ir.Lit) or type(value.value) is not int:
        return None
    word_bits = 1 << (selected_width - 1).bit_length()
    if owner_width <= 2 * word_bits or 2 * word_bits > SCALAR_BITS:
        return None
    mask = (1 << (2 * word_bits)) - 1
    bits = value.value & ((1 << owner_width) - 1)
    cells = ir.Op('arraylit', *(ir.Lit((bits >> start) & mask)
                  for start in range(0, owner_width, word_bits)))
    index = ir.Op('shr', offset, ir.Lit(word_bits.bit_length() - 1))
    within = ir.Op('and', offset, ir.Lit(word_bits - 1))
    return ir.Op('shr', ir.Sel(cells, index), within)


def merge_bits(kind, previous, value, mask):
    """Typed replacement shared by blocking writes and scalar/array queues.

    Whole-word replacement needs no old payload. Apply the destination wrap
    here: dropping high preserved bits is equivalent only at this typed
    boundary. Earlier source evaluation and queue event summaries are unchanged.
    """
    whole = isinstance(mask, ir.Lit) and mask.value == (1 << kind.width) - 1
    return kind.wrap(value if whole else ir.Op("or",
        ir.Op("and", previous, ir.Op("inv", mask)), ir.Op("and", value, mask)))


def selection_offset(kind, index, count=1, *, descending_select=False):
    """Map declared packed coordinates to a physical least-significant bit."""
    low = ir.Op("sub", index, ir.Lit(count - 1)) if descending_select else index
    offset = (ir.Op("sub", low, ir.Lit(kind.right)) if kind.left >= kind.right else
              ir.Op("sub", ir.Lit(kind.right - count + 1), low))
    return ir.Op("mul", offset, ir.Lit(kind.element_width))
