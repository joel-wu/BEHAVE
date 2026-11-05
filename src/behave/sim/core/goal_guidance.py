"""Finite semantic witnesses, separate from goal-policy composition.

This module searches only the declared finite relation. Its witnesses are not
artifact stimuli or concrete artifact evidence; the generation bridge owns
realization and replay. Public record aliases remain in core.goal_provider.
"""

from .catalogue import CatalogueManifest, catalogue_subset
from .finite_goal import DeclarativeGoalSemantics, _text, _value_image, _value_key
from .frozen import Frozen
from .refusal import Refusal, Refused


class DeclarativeGuidanceStep(Frozen):
    """One complete finite observation/event assignment for semantic guidance.

    A guidance row does not run a private search and does not own a budget.  It is
    immutable data consumed by a production generation bridge: values for every
    predicate and one event for every monitor at one semantic step.  Keeping the row
    complete lets the provider replay it through the declarative concrete-step
    semantics before it is registered.
    """

    __slots__ = ("monitor_events", "predicate_observations")

    def __init__(self, predicate_observations, monitor_events):
        if type(predicate_observations) not in (tuple, list) or any(
                type(row) is not tuple or len(row) != 2
                or type(row[0]) is not str or not row[0]
                or type(row[1]) not in (tuple, list)
                for row in predicate_observations):
            raise TypeError(
                "declarative guidance predicates are exact goal/field rows")
        predicates = []
        for goal, fields in predicate_observations:
            fields = tuple(fields)
            if any(type(row) is not tuple or len(row) != 2
                   or type(row[0]) is not str or not row[0]
                   for row in fields):
                raise TypeError(
                    "declarative guidance predicate fields are exact name/value rows")
            names = tuple(name for name, _value in fields)
            if len(set(names)) != len(names):
                raise ValueError(
                    "a declarative guidance predicate names one field twice")
            for _name, value in fields:
                _value_key(value)
            predicates.append((goal, tuple(sorted(fields))))
        predicates = tuple(sorted(predicates))
        if len({goal for goal, _fields in predicates}) != len(predicates):
            raise ValueError(
                "a declarative guidance step names one predicate twice")

        if type(monitor_events) not in (tuple, list) or any(
                type(row) is not tuple or len(row) != 2
                or type(row[0]) is not str or not row[0]
                for row in monitor_events):
            raise TypeError(
                "declarative guidance monitor events are exact monitor/value rows")
        events = tuple(sorted(monitor_events, key=lambda row: row[0]))
        if len({key for key, _value in events}) != len(events):
            raise ValueError(
                "a declarative guidance step names one monitor twice")
        for _key, value in events:
            _value_key(value)
        self.predicate_observations = predicates
        self.monitor_events = events
        self._freeze()

    def concrete_inputs(self):
        """Fresh exact dictionaries accepted by ``concrete_step``."""

        return (
            {goal: dict(fields)
             for goal, fields in self.predicate_observations},
            dict(self.monitor_events),
        )

    def as_dict(self):
        return {
            "predicates": [
                {"goal": goal,
                 "fields": [
                     {"name": name, "value": _value_image(value)}
                     for name, value in fields]}
                for goal, fields in self.predicate_observations
            ],
            "monitor_events": [
                {"monitor": key, "event": _value_image(event)}
                for key, event in self.monitor_events
            ],
        }


class DeclarativeGuidanceWitness(Frozen):
    """A shortest deterministic finite semantic trace reaching one goal."""

    __slots__ = ("goal", "steps")

    def __init__(self, goal, steps):
        self.goal = _text(goal, "declarative-guidance goal")
        if type(steps) not in (tuple, list) or not steps or any(
                type(step) is not DeclarativeGuidanceStep for step in steps):
            raise TypeError(
                "a declarative guidance witness needs nonempty exact steps")
        self.steps = tuple(steps)
        self._freeze()

    def as_dict(self):
        return {
            "goal": self.goal,
            "steps": [step.as_dict() for step in self.steps],
        }


def _first_monitor_path(monitor, goal):
    """Canonical shortest event sequence from ``initial`` to ``goal``."""

    frontier = [(monitor.initial, ())]
    seen = {_value_key(monitor.initial)}
    while frontier:
        following = []
        for state, path in frontier:
            for event in monitor.events:
                stepped = monitor.step(state, event)
                candidate = path + (event,)
                if goal in stepped.goals:
                    return candidate
                key = _value_key(stepped.state)
                if key not in seen:
                    seen.add(key)
                    following.append((stepped.state, candidate))
        frontier = following
    return None


class DeclarativeGuidanceAdapter(Frozen):
    """Replay-validated semantic witnesses from one declarative semantic table.

    There is exactly one finite witness per selected goal.  Predicate witnesses use
    the first canonical satisfying truth-table row.  Monitor witnesses use the first
    canonical shortest path through the deterministic total transition table.  The
    constructor concretely replays every witness through the same semantics that own
    concrete hits.  This table does not know a task's queue layout and cannot itself
    produce a stimulus; production guidance additionally requires a registered
    generation provider and task realizer.
    """

    VERSION = "declarative-local-guidance/1"
    __slots__ = (
        "catalogue", "implementation", "semantics", "witnesses",
    )

    def __init__(self, semantics, catalogue, targets=None):
        if type(semantics) is not DeclarativeGoalSemantics:
            raise TypeError(
                "declarative guidance needs exact DeclarativeGoalSemantics")
        if type(catalogue) is not CatalogueManifest:
            raise TypeError("declarative guidance needs an exact catalogue")
        if set(catalogue) != set(semantics.goals):
            raise Refused(Refusal(
                "goal-provider-guidance-semantics-catalogue-mismatch",
                "declarative guidance and its semantic table name different goals",
                declared=sorted(catalogue), semantic=list(semantics.goals)))
        if targets is None:
            targets = tuple(catalogue)
        elif type(targets) not in (tuple, list):
            raise TypeError(
                "declarative guidance targets must be a materialized sequence")
        else:
            targets = tuple(targets)
        if not targets or any(type(goal) is not str or not goal for goal in targets):
            raise Refused(Refusal(
                "goal-provider-guidance-catalogue-empty",
                "declarative local guidance must select a nonempty goal set"))
        if len(set(targets)) != len(targets):
            raise Refused(Refusal(
                "goal-provider-guidance-target-duplicate",
                "declarative local guidance names one goal more than once"))
        unknown = sorted(set(targets) - set(catalogue))
        if unknown:
            raise Refused(Refusal(
                "goal-provider-guidance-target-unregistered",
                "declarative local guidance targets goals outside its declaration",
                goals=unknown))

        selected = catalogue_subset(catalogue, targets)
        defaults = tuple((
            predicate.goal,
            tuple((variable.name, variable.values[0])
                  for variable in predicate.variables),
        ) for predicate in semantics.predicates)
        default_events = {
            monitor.key: monitor.events[0] for monitor in semantics.monitors
        }
        predicate_owners = {
            predicate.goal: predicate for predicate in semantics.predicates
        }
        monitor_owners = {
            goal: monitor for monitor in semantics.monitors
            for goal in monitor.goals
        }
        witnesses = []
        for goal in sorted(targets):
            owners = int(goal in predicate_owners) + int(goal in monitor_owners)
            if owners != 1:
                raise Refused(Refusal(
                    "goal-provider-guidance-target-ambiguous",
                    "a declarative guidance target needs exactly one semantic owner",
                    goal=goal, owners=owners))
            if goal in predicate_owners:
                predicate = predicate_owners[goal]
                if not predicate.accepting:
                    path = None
                else:
                    chosen = dict(defaults)
                    chosen[goal] = tuple(
                        (variable.name, value)
                        for variable, value in zip(
                            predicate.variables, predicate.accepting[0]))
                    path = (DeclarativeGuidanceStep(
                        tuple(chosen.items()), tuple(default_events.items())),)
            else:
                monitor = monitor_owners[goal]
                events = _first_monitor_path(monitor, goal)
                path = None if events is None else tuple(
                    DeclarativeGuidanceStep(
                        defaults,
                        tuple({**default_events, monitor.key: event}.items()))
                    for event in events)
            if path is None:
                raise Refused(Refusal(
                    "goal-provider-guidance-target-unreachable",
                    "the finite declarative semantics has no local witness for an "
                    "enabled guidance target",
                    goal=goal))
            witness = DeclarativeGuidanceWitness(goal, path)
            self._validate_witness(semantics, witness)
            witnesses.append(witness)

        self.semantics = semantics
        self.catalogue = selected
        self.implementation = (
            f"{self.VERSION}:{semantics.digest()}:{selected.digest()}")
        self.witnesses = tuple(witnesses)
        self._freeze()

    @staticmethod
    def _validate_witness(semantics, witness):
        states = dict(semantics.initial_monitor_states())
        reached = set()
        for step in witness.steps:
            predicates, events = step.concrete_inputs()
            concrete = semantics.concrete_step(predicates, states, events)
            reached.update(concrete.reached)
            states = dict(concrete.monitor_states)
        if witness.goal not in reached:
            raise Refused(Refusal(
                "goal-provider-guidance-witness-dishonest",
                "the derived declarative guidance witness does not concretely hit "
                "its target",
                goal=witness.goal))

    def select(self, goal):
        """Return the one finite local witness for ``goal``.

        The common scheduler owns request and solver budgets.  Selection here is a
        lookup in an immutable finite table and performs no retry or hidden search.
        """

        if type(goal) is not str or not goal:
            raise TypeError("declarative guidance selects a nonempty exact goal")
        for witness in self.witnesses:
            if witness.goal == goal:
                return witness
        raise Refused(Refusal(
            "goal-provider-guidance-target-unavailable",
            "the requested goal is outside this declarative guidance adapter",
            goal=goal, targets=list(self.catalogue)))

    def as_dict(self):
        return {
            "version": self.VERSION,
            "implementation": self.implementation,
            "semantics_digest": self.semantics.digest(),
            "catalogue_digest": self.catalogue.digest(),
            "witnesses": [witness.as_dict() for witness in self.witnesses],
        }
