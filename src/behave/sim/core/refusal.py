"""Typed non-answers shared by simulator boundaries.

A :class:`Refusal` is a value for a caller that can continue without an answer;
:class:`Refused` stops a caller while retaining that same typed reason.  Neither is a
false result, an unreachable claim, or a truth value.  Stable ``kind`` values support
aggregation; human-readable ``why`` text is carried but never parsed.
"""

from __future__ import annotations

from .frozen import Frozen
from .record_value import FrozenValueMap, freeze_record_value, thaw_record_value

__all__ = ["Refusal", "Refused", "answered", "refusal_of"]


class Refused(RuntimeError):
    """Stop a caller while carrying the exact typed :class:`Refusal`."""

    def __init__(self, refusal):
        if type(refusal) is not Refusal:
            raise TypeError(
                f"Refused takes a Refusal, not {type(refusal).__name__}: the point is "
                f"that the kind is a value a counter can group by")
        super().__init__(f"{refusal.kind}: {refusal.why}")
        self.refusal = refusal

    def __reduce__(self):
        # Exception.args is display text, not the typed constructor argument.
        return type(self), (self.refusal,)


class Refusal(Frozen):
    """A question this could not answer, and why.

    ``kind`` is a short stable slug, because it is what counts are grouped by and a
    prose sentence is not a group. ``why`` is for a person, and follows the same rule as
    `Goal.provenance`: carried, printed, **never parsed**. The moment something branches
    on the text, the text is an interface and rewording it is a behaviour change --
    which already happened once here, in `instance.blockers` reading
    `"candidate" in source`.
    """

    __slots__ = ("detail", "kind", "why")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("Refusal is a closed simulator-boundary record")

    def __init__(self, kind, why, **detail):
        if not kind or not str(kind).strip():
            raise ValueError("a refusal needs a kind, since counts group by it")
        if not why or not str(why).strip():
            # The same rule `Basis` already enforced locally. "No" without a reason is
            # what a later edit flips back to "yes", because nothing records what was
            # being refused or on what grounds.
            raise ValueError(
                f"refusal {kind!r} must say why, so that whoever widens this later "
                f"knows what they are widening")
        self.kind = str(kind)
        self.why = str(why)
        #: Structured extras for a report. Not part of identity and not parsed.
        frozen = freeze_record_value(detail, "refusal.detail")
        if type(frozen) is not FrozenValueMap:
            raise TypeError("a refusal detail image is a mapping")
        self.detail = frozen
        self._freeze()

    def __bool__(self):
        """Reject the conversion of a non-answer into a false answer."""
        raise TypeError(
            f"a Refusal({self.kind!r}) is not a truth value: it means the question "
            f"had no answer, and treating it as False is exactly the conflation this "
            f"type exists to stop. Test it with `answered(x)` or `isinstance`. "
            f"Why: {self.why}")

    def as_dict(self):
        out = {"kind": self.kind, "why": self.why}
        if self.detail:
            out["detail"] = thaw_record_value(self.detail)
        return out

    def __eq__(self, other):
        return (type(other) is Refusal and self.kind == other.kind
                and self.why == other.why and self.detail == other.detail)

    def __hash__(self):
        return hash((self.kind, self.why))

    def __repr__(self):
        return f"Refusal({self.kind!r}, {self.why!r})"


def answered(value):
    """Whether ``value`` is an answer rather than a refusal.

    A function rather than `not isinstance(...)` at every call site, so that "is this an
    answer" is one expression everywhere and a second convention cannot grow beside it.
    `None` counts as an answer here on purpose: plenty of functions return `None` to mean
    "absent" rather than "unanswerable", and quietly reclassifying those would be this
    module inventing refusals nobody declared.
    """
    return not isinstance(value, Refusal)


def refusal_of(value):
    """``value`` if it is a `Refusal`, else None. For grouping without a type test."""
    return value if isinstance(value, Refusal) else None
