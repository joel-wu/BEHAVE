"""Control-flow facts shared by Behavior's graph and term lowerers.

The algebra refines and joins independent snapshots. ``FlowState`` owns the
current mutable projection and its restoration/lifetime rules. Neither emits
IR, allocates physical variables, spends budgets or changes declarations.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from ..symbolic import ranges
from .lower_static_refs import (
    _STATIC_SCALAR,
    _StaticIssue,
    _StaticRef,
)
from ..symbolic.value_contract import join_contracts

__all__ = ["FlowFacts", "FlowState", "assume", "join", "with_bounds"]


@dataclass(frozen=True)
class FlowFacts:
    """Compile-time facts at one source control-flow point.

    These facts justify structural lowering decisions; they are not storage
    allocated by the lowering. Keeping them in one value makes a control-flow
    join one operation instead of a growing list of independently patched side
    tables.
    """

    lengths: dict
    carriers: dict
    contracts: dict
    ranges: dict
    bounds: dict
    unknown_ranges: frozenset
    static_values: dict


@dataclass(slots=True, eq=False)
class FlowState:
    """Mutable facts for the current source path, not procedure storage.

    Snapshots and restored tables never alias. The lowerer may refine individual
    entries as it emits a command; whole-path transitions belong here so the
    graph and term backends cannot restore different subsets accidentally.

    Structural bounds may be one-sided. Only finite ranges can justify a
    physical bit width; neither table owns the procedure's declarations.
    """

    lengths: dict = field(default_factory=dict)
    carriers: dict = field(default_factory=dict)
    contracts: dict = field(default_factory=dict)
    ranges: dict = field(default_factory=dict)
    bounds: dict = field(default_factory=dict)
    unknown_ranges: set = field(default_factory=set)
    static_values: dict = field(default_factory=dict)

    @classmethod
    def seeded(cls, finite):
        finite = dict(finite or {})
        return cls(ranges=finite, bounds={
            name: ranges.Bounds.of(value) for name, value in finite.items()})

    def snapshot(self):
        return FlowFacts(
            dict(self.lengths), dict(self.carriers), dict(self.contracts),
            dict(self.ranges), dict(self.bounds), frozenset(self.unknown_ranges),
            dict(self.static_values))

    def restore(self, facts):
        self.lengths = dict(facts.lengths)
        self.carriers = dict(facts.carriers)
        self.contracts = dict(facts.contracts)
        self.ranges = dict(facts.ranges)
        self.bounds = dict(facts.bounds)
        self.unknown_ranges = set(facts.unknown_ranges)
        self.static_values = dict(facts.static_values)

    def restore_expression(self, facts, option_names):
        """Fork numeric/static facts but retain expression carrier allocation.

        Both expression arms allocate values in one scope. Options alone also
        restore their presence contracts: keeping a previous arm's presence
        would confuse a tag/payload pair with a scalar or absent value.
        """
        self.ranges = dict(facts.ranges)
        self.bounds = dict(facts.bounds)
        self.unknown_ranges = set(facts.unknown_ranges)
        self.static_values = dict(facts.static_values)
        for name in option_names:
            if name in facts.contracts:
                self.contracts[name] = facts.contracts[name]

    def names(self):
        """Retain only names at a call boundary, without copying fact values."""
        return {item.name: frozenset(getattr(self, item.name))
                for item in fields(FlowFacts)}

    def retain_names(self, before, retained):
        """Discard dead helper facts; emitted IR storage is owned elsewhere."""
        for name in before:
            table = getattr(self, name)
            keep = before[name] | retained
            if name == "unknown_ranges":
                table.intersection_update(keep)
            else:
                for key in tuple(table):
                    if key not in keep:
                        del table[key]

    def merge(self, states, ranges_override=None):
        """Join paths; optional numeric transfer may update only live names."""
        joined = join(states)
        self.restore(joined)
        if ranges_override is None:
            return
        universe = set(joined.unknown_ranges)
        for state in states:
            universe.update(state.ranges)
            universe.update(state.bounds)
            universe.update(state.unknown_ranges)
        # Transfer visits dead compiler locals too. Exit facts determine name
        # lifetime; the transfer determines numeric values of still-live names.
        self.ranges = {name: value for name, value in ranges_override.items()
                       if name in universe}
        # This finite-width authority does not preserve old one-sided bounds.
        self.bounds = {name: ranges.Bounds.of(value)
                       for name, value in self.ranges.items()}
        self.unknown_ranges = universe - set(self.ranges)


def with_bounds(facts, updates):
    """Replace selected numeric facts, keeping both projections consistent.

    Callers own the proof and calculation of each replacement bound. This is
    neither a path intersection nor a join: an open bound removes its stale
    finite projection, while a finite bound clears that name's unknown marker.
    Unchanged facts are retained and all mutable tables belong to the result.
    """
    finite = dict(facts.ranges)
    bounds = dict(facts.bounds)
    unknown = set(facts.unknown_ranges)
    for name, bound in updates.items():
        bounds[name] = bound
        exact = bound.finite()
        if exact is None:
            finite.pop(name, None)
            unknown.add(name)
        else:
            finite[name] = exact
            unknown.discard(name)
    return FlowFacts(
        dict(facts.lengths), dict(facts.carriers), dict(facts.contracts),
        finite, bounds, frozenset(unknown), dict(facts.static_values))


def assume(facts, predicate, truth):
    """Refine structural bounds on one normal conditional arm."""
    narrowed = ranges.assume_bounds(predicate, truth, facts.bounds)
    if narrowed is None:
        return None
    finite = {}
    for name, value in narrowed.items():
        # Most live bindings are unchanged by this branch. Reuse only an
        # exactly equal finite projection; structural bounds can differ
        # from the encoder's previous finite facts.
        previous = facts.ranges.get(name)
        got = (previous if previous is not None
               and previous.lo == value.lo and previous.hi == value.hi
               else value.finite())
        if got is not None:
            finite[name] = got
    unknown = set(facts.unknown_ranges)
    unknown -= set(narrowed)
    return FlowFacts(
        dict(facts.lengths), dict(facts.carriers),
        dict(facts.contracts), finite, dict(narrowed),
        frozenset(unknown), dict(facts.static_values))


def _same_facts(states, field):
    """Exact facts present with one value on every reaching path."""
    if not states:
        return {}
    mappings = [getattr(state, field) for state in states]
    common = set(mappings[0])
    for mapping in mappings[1:]:
        common &= set(mapping)
    return {
        name: mappings[0][name]
        for name in common
        if all(mapping[name] == mappings[0][name]
               for mapping in mappings[1:])
    }


def join(states):
    """Facts sound for every normally reaching control-flow path.

    Shapes and Python carriers are exact facts, so disagreement drops the
    fact. Integer ranges are abstractions and therefore join by interval.
    A missing or explicitly unknown range on any path remains unknown.
    """
    if not states:
        return FlowFacts({}, {}, {}, {}, {}, frozenset(), {})

    lengths = _same_facts(states, "lengths")
    carriers = _same_facts(states, "carriers")
    # Like the source-contract interpreter, retain a binding contract only
    # when every reaching path defines the name.  A missing binding is not
    # an UNKNOWN value; the exact CFG/SSA layer owns the resulting
    # unassigned read.
    contract_names = set(states[0].contracts)
    for state in states[1:]:
        contract_names &= set(state.contracts)
    contracts = {}
    for name in contract_names:
        value = states[0].contracts[name]
        for state in states[1:]:
            value = join_contracts(value, state.contracts[name])
        contracts[name] = value
    range_names = set(states[0].ranges)
    for state in states[1:]:
        range_names &= set(state.ranges)
    unknown = set().union(*(state.unknown_ranges for state in states))
    all_range_names = set().union(*(state.ranges for state in states))
    unknown |= all_range_names - range_names
    ranges_out = {}
    for name in range_names - unknown:
        value = states[0].ranges[name]
        for state in states[1:]:
            other = state.ranges[name]
            if value is not other:
                value = value.join(other)
        ranges_out[name] = value
    bound_names = set(states[0].bounds)
    for state in states[1:]:
        bound_names &= set(state.bounds)
    bounds_out = {}
    # A one-sided bound can be present on every path while its finite
    # projection is absent on one of them.  The latter makes the name
    # unknown to the encoder, not to the structural domain.
    for name in bound_names:
        value = states[0].bounds[name]
        for state in states[1:]:
            other = state.bounds[name]
            if value is not other:
                value = value.join(other)
        bounds_out[name] = value
    static_values = _joined_static_values(states)
    return FlowFacts(
        lengths, carriers, contracts, ranges_out, bounds_out,
        frozenset(unknown), static_values)


def _joined_static_values(states):
    """Static-reference facts that survive an unlabelled flow join.

    The lowerer owns a stronger labelled conditional join: its guard can
    retain two compatible references as guarded alternatives. Loops and other
    joins have no single selector to attach, so a changing reference is a
    typed boundary rather than a guessed merge.
    """
    if not states:
        return {}
    names = set().union(*(state.static_values for state in states))
    out = {}
    for name in names:
        values = [state.static_values.get(name) for state in states]
        first = values[0]
        if first is not None and all(value == first for value in values):
            out[name] = first
            continue
        refs = [value for value in values
                if isinstance(value, _StaticRef)]
        issues = [value for value in values
                  if isinstance(value, _StaticIssue)]
        if refs:
            merged = _StaticRef.merge_bound(refs)
            if (merged is not None
                    and all(value is None or isinstance(value, _StaticRef)
                            for value in values)
                    and not any(value is None for value in values)):
                out[name] = merged
                continue
            if any(value == _STATIC_SCALAR for value in values) or issues:
                out[name] = _StaticIssue(
                    "static-collection-join-disagrees")
            elif any(value is None for value in values):
                out[name] = _StaticIssue(
                    "static-collection-uninitialized")
            else:
                out[name] = _StaticIssue(
                    "static-collection-join-disagrees")
        elif issues:
            out[name] = issues[0]
        elif any(value == _STATIC_SCALAR for value in values):
            out[name] = _STATIC_SCALAR
    return out
