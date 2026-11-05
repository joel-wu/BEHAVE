"""Z3 constraints for the shared task-owned packed physical value codec."""

from __future__ import annotations

import z3

from ..core.physical_value import packed_leaves

__all__ = ["valid_packed_pattern"]


def valid_packed_pattern(term, value_type):
    """Exclude unused enumeration tags from one packed symbolic value."""

    clauses = []
    for leaf in packed_leaves(value_type):
        if leaf.value_type.kind != "enumeration":
            continue
        tags = tuple(tag for _member, tag in leaf.value_type.tag_map)
        if len(tags) == 1 << leaf.width:
            continue
        if z3.is_bool(term):
            if leaf.offset != 0 or leaf.width != 1:
                raise TypeError("a Boolean packed term cannot contain this leaf slice")
            raw = term
            choices = (raw == z3.BoolVal(bool(tag), ctx=term.ctx) for tag in tags)
        else:
            raw = z3.Extract(
                leaf.offset + leaf.width - 1, leaf.offset, term)
            choices = (
                raw == z3.BitVecVal(tag, leaf.width, ctx=term.ctx) for tag in tags)
        clauses.append(z3.Or(*choices))
    return z3.And(*clauses) if clauses else z3.BoolVal(True, ctx=term.ctx)
