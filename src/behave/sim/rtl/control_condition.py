"""Ordered RTL control predicates, independent of public goal projection.

Both retained RTLIL sites and source-event bindings use these conditions. They
describe one process activation, not a signal-snapshot observation of execution.
"""

from dataclasses import dataclass

from ..core.refusal import Refusal
from .case_match import _condition
from .sigspec import rtlil_name, signal_term


def triggered(triggers, events=None, *, include_combinational=True):
    if any(edge == "always" for edge, _ in triggers):
        return include_combinational
    if events is None:
        return True
    active = {("posedge" if edge == "rising" else "negedge" if edge == "falling" else edge,
               rtlil_name(signal)) for edge, signal in events}
    return any((edge, rtlil_name(signal)) in active for edge, signal in triggers)


@dataclass(frozen=True, slots=True)
class ControlCondition:
    triggers: tuple[tuple[str, str], ...]
    levels: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    enabled: bool = True

    @classmethod
    def of_site(cls, site):
        levels = [(site.read_signal(signal), label, siblings)
                  for (signal, label), siblings in zip(site.activation, site.activation_siblings)]
        if site.kind != "location":
            levels.append((site.read_signal(site.signal), site.outcome, site.siblings))
        return cls(site.scheduled_triggers, tuple(levels))

    def predicate(self, explorer, *, target):
        """Encode the complete ordered control path, or return its exact refusal."""
        import z3

        if not self.enabled:
            return z3.BoolVal(False)
        parts = []
        for signal, label, siblings in self.levels:
            try:
                var, width, missing = signal_term(explorer, signal)
            except (TypeError, ValueError) as exc:
                return Refusal("site-signal-expression-unsupported", str(exc),
                               site=target, signal=str(signal))
            if var is None:
                return Refusal(
                    "site-signal-absent",
                    "the control predicate needs a signal absent from the relation",
                    site=target, signal=missing)
            term, why = _condition(var, width, label, siblings)
            if term is None:
                return Refusal("site-label-not-constant", why, site=target,
                               signal=str(signal), label=str(label),
                               siblings=[str(s) for s in siblings])
            parts.append(term)
        return z3.And(*parts) if len(parts) > 1 else parts[0] if parts else z3.BoolVal(True)
