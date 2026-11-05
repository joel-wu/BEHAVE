"""Observe a loop's dynamic entry without reevaluating its header.

The surrounding source plan owns event IDs and occurrence contexts. This small
rewrite only implements the language-independent entry rule: first body entry
emits nonzero; normal exit before any body entry emits zero. Neither exhaustion
after a body nor an early exit after a body can emit zero.
"""

from .source_edits import Insertion


def loop_entry_edits(loop_span, body_span, *, zero, nonzero, declarations=()):
    """Preserve the original header, body, break/continue and return statements.

    The observer-local bit is reset on every dynamic entry, including reentry
    from an outer loop or another helper invocation. It is not a DUT state bit
    or a synthesized replacement for the original loop condition.
    """
    start, end = loop_span
    body_start, body_end = body_span
    seen = f"__behave_native_event_loop_seen_{start}"
    return (
        Insertion(start, f"begin bit {seen}; " + " ".join(declarations) + f" {seen}=0; "),
        Insertion(body_start, f"begin if(!{seen}) begin {seen}=1; {nonzero} end "),
        Insertion(body_end, " end "),
        Insertion(end, f" if(!{seen}) begin {zero} end end "),
    )
