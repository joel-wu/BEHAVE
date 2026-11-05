"""Per-occurrence predicate oracle, not a native execution-event observer.

This deliberately simple evaluator tests the symbolic and batched evaluators.
A predicate on a supplied valuation does not prove that a source process ran;
production catalogue records therefore expose no ``hits(values)`` method.
The circuit-site fixture builder also stays here: its synthesized-action
catalogue is not interchangeable with an admitted artifact's source events.
"""

from behave.sim.rtl.case_match import matches
from behave.sim.rtl.sigspec import signal_value


def _value_of(signal, values):
    try:
        value, missing = signal_value(signal, values)
    except (TypeError, ValueError):
        return None
    return None if missing is not None else value


def activation_holds(site, values):
    for (signal, label), siblings in zip(site.activation, site.activation_siblings):
        value = _value_of(site.read_signal(signal), values)
        if value is None:
            return None
        matched, _why = matches(label, siblings, value)
        if matched is not True:
            return matched
    return True


def occurrence_holds(site, values):
    active = activation_holds(site, values)
    if active is not True:
        return active
    if site.kind == "location":
        return True
    value = _value_of(site.read_signal(site.signal), values)
    if value is None:
        return None
    matched, _why = matches(site.outcome, site.siblings, value)
    return matched
