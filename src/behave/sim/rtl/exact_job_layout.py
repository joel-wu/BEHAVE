"""Finite layout record shared by isolated RTL exact jobs."""

from __future__ import annotations

from ..core.frozen import Frozen
from ..core.refusal import Refusal, Refused
from .exact_job_instance import PreparedSourceExact

__all__ = ["RTLGenerationLayout", "decode_layout_reply", "layout_job"]


_LAYOUT_VERSION = "rtl-generation-layout/1"


class RTLGenerationLayout(Frozen):
    """The source observations needed outside an isolated exact worker."""

    __slots__ = ("outputs", "state_names")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("RTLGenerationLayout is a closed worker-boundary record")

    def __init__(self, outputs, state_names):
        outputs = tuple(outputs)
        state_names = tuple(state_names)
        for name, values in (("outputs", outputs), ("state_names", state_names)):
            if any(type(value) is not str or not value for value in values):
                raise TypeError(f"generation layout {name} are nonempty exact strings")
            if len(set(values)) != len(values) or values != tuple(sorted(values)):
                raise ValueError(f"generation layout {name} are sorted and distinct")
        self.outputs = outputs
        self.state_names = state_names
        self._freeze()

    def as_dict(self):
        return {
            "version": _LAYOUT_VERSION,
            "status": "ok",
            "outputs": list(self.outputs),
            "state_names": list(self.state_names),
        }


def _refusal_reply(refusal):
    if type(refusal) is not Refusal:
        raise TypeError("an exact-layout refusal is an exact Refusal")
    return {
        "version": _LAYOUT_VERSION,
        "status": "refused",
        "refusal": refusal.as_dict(),
    }


def decode_layout_reply(value):
    if type(value) is not dict or value.get("version") != _LAYOUT_VERSION:
        raise ValueError("an isolated RTL layout reply has the supported version")
    if value.get("status") == "refused":
        if set(value) != {"version", "status", "refusal"}:
            raise ValueError("a refused RTL layout reply has no extra fields")
        refusal = value["refusal"]
        if type(refusal) is not dict or set(refusal) not in (
                {"kind", "why"}, {"kind", "why", "detail"}):
            raise TypeError(
                "an RTL layout refusal has kind, why, and optional detail")
        if any(type(refusal[name]) is not str or not refusal[name].strip()
               for name in ("kind", "why")):
            raise TypeError("RTL layout refusal kind and why are nonempty strings")
        if "detail" in refusal and type(refusal["detail"]) is not dict:
            raise TypeError("RTL layout refusal detail is an exact mapping")
        return Refusal(
            refusal["kind"], refusal["why"], **refusal.get("detail", {}))
    if value.get("status") != "ok" or set(value) != {
            "version", "status", "outputs", "state_names"}:
        raise ValueError("a successful RTL layout reply has its complete field set")
    if type(value["outputs"]) is not list \
            or type(value["state_names"]) is not list:
        raise TypeError("RTL layout outputs and state_names are exact lists")
    got = RTLGenerationLayout(value["outputs"], value["state_names"])
    if got.as_dict() != value:
        raise ValueError("an RTL layout reply is not canonical")
    return got


def layout_job(payload):
    """Prepare the source instance and publish only its finite source layout."""

    from ..symbolic.validation import EncodingError

    try:
        if type(payload) is not dict or "source_layout" not in payload:
            return _refusal_reply(Refusal(
                "rtl-generation-layout-not-source",
                "an isolated RTL layout job prepares the compiler-owned source image"))
        prepared = PreparedSourceExact(payload)
        return RTLGenerationLayout(
            sorted(field.port for field in prepared.machine.program.fields.values()
                   if field.port is not None and not field.input),
            sorted(prepared.initial_layout.widths)).as_dict()
    except Refused as exc:
        return _refusal_reply(exc.refusal)
    except EncodingError as exc:
        # This is a reported encoding limit, not a worker launch/crash error.
        # Preserve the typed reason without guessing from diagnostic text.
        reason = exc.kind if exc.kind in {
            "range", "shape", "type", "width", "capability", "resource", "goal",
        } else "operation"
        return _refusal_reply(Refusal(
            f"rtl-exact-encoding-{reason}-unavailable",
            "the RTL frontend cannot encode the requested source layout",
            encoding_kind=reason, error=f"EncodingError: {exc}"))
