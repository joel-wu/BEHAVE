"""Resolve RTL structural occurrences against samples and symbolic state.

The catalogue retains control structure before synthesis removes it. ``case_match``
owns arm and priority semantics; ``sigspec`` owns wire, slice and concatenation
readings. This module combines them with each occurrence's enclosing activation
and event conditions.

A missing signal or unreadable predicate is an explicit refusal, never an
unguarded or similarly named fallback target.
"""

from __future__ import annotations

from ..core.refusal import Refusal
from .case_match import match_rule, matches
from .control_condition import ControlCondition
from .sigspec import rtlil_name

__all__ = [
    "coverage_from_occurrences",
    "coverage_occurrences_of",
    "coverage_of",
    "match_rule",
    "matches",
    "predicate_for",
    "rtlil_name",
    "site_hits",
]


def site_hits(
        sites, values, widths=None, *, active_events=None, occurrence_goals=None):
    """Which of ``sites`` ran, given one reader's sampled signal values.

    ``values`` and optional declared ``widths`` use the source/trace spelling
    (`rtlil_name`), not the SMT2 writer's name encoding. Widths are needed only
    when a switch concatenates whole wires. ``active_events`` is the slot's finite
    ``(edge, clock-pin)`` set. Atomic
    schedules provide it so a process on an inactive clock contributes no hit; the
    single-transition path leaves it ``None`` because its step already denotes the
    admitted process event.

    Returns public goal IDs.  Elaborated copies are evaluated independently and then
    ORed: one true occurrence hits the source goal, while an undecidable occurrence
    prevents a miss unless another occurrence already proves the hit.
    """
    from .site_sampling import SiteSampler

    return SiteSampler(sites, occurrence_goals=occurrence_goals).sample(
        values, widths, active_events=active_events)


def predicate_for(explorer, site):
    """``site`` as a z3 `Bool` over the current state, or a `Refusal`.

    The conjunction is the activation **and** the outcome, in that order and never one
    without the other: dropping the activation is exactly the defect P1.7 names, and a
    caller that gets a formula back cannot tell whether the guard is in it, so it is
    never optional.
    """
    return ControlCondition.of_site(site).predicate(explorer, target=site.target)


def coverage_occurrences_of(explorer, sites, occurrence_goals=None):
    """Every elaborated occurrence predicate, grouped by its public goal."""

    from .catalogue import goal_id_of

    grouped = {}
    for site in (sites.values() if hasattr(sites, "values") else sites):
        goal = (
            occurrence_goals[site.target]
            if occurrence_goals is not None else goal_id_of(site)
        )
        grouped.setdefault(goal, []).append(
            (site, predicate_for(explorer, site)))
    return {goal: tuple(rows) for goal, rows in grouped.items()}


def coverage_of(explorer, sites, occurrence_goals=None):
    """Public goal predicates and refusals, OR-ing each goal's occurrences."""
    return coverage_from_occurrences(
        coverage_occurrences_of(explorer, sites, occurrence_goals))


def coverage_from_occurrences(occurrences):
    """Aggregate already-resolved occurrences without rebuilding their predicates."""
    import z3

    resolved, refused = {}, {}
    for goal, rows in occurrences.items():
        terms = [term for _site, term in rows if not isinstance(term, Refusal)]
        failures = [term for _site, term in rows if isinstance(term, Refusal)]
        predicate = None
        if terms:
            predicate = z3.simplify(
                z3.Or(*terms) if len(terms) > 1 else terms[0])
        if failures and not (predicate is not None and z3.is_true(predicate)):
            refused[goal] = failures[0]
        elif predicate is not None:
            resolved[goal] = predicate
    return resolved, refused
