"""Exact symbolic terms for the shared external-memory initialization.

The protocol and its concrete integer definition live in :mod:`sim.core.memory`.
This module is the single solver-dependent realization consumed by both Behavior
and RTL exact products.
"""

from __future__ import annotations

import z3

from ..core.memory import memory_junk_parameters

__all__ = ["memory_junk_term"]


def _unsigned_width(term, width):
    if not z3.is_bv(term):
        raise TypeError("a symbolic memory address is a bit-vector")
    if term.size() < width:
        return z3.ZeroExt(width - term.size(), term)
    if term.size() > width:
        return z3.Extract(width - 1, 0, term)
    return term


def memory_junk_term(memory_name, address, data_width, *, result_width=None):
    """The exact QF_BV image of ``memory_junk_value`` at ``address``."""

    first, second, offset, shift = memory_junk_parameters(
        memory_name, data_width)
    value = _unsigned_width(address, data_width)
    # Coerce constants in the address term's context and exact bit width.
    value = (value ^ offset) * first
    value = value ^ z3.LShR(value, shift)
    value = value * second + offset
    if result_width is None or result_width == data_width:
        return value
    if type(result_width) is not int or result_width < data_width:
        raise ValueError(
            "a memory junk result carrier cannot truncate its data value")
    return z3.ZeroExt(result_width - data_width, value)
