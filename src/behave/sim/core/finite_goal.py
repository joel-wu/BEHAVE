"""Finite goal definitions shared by concrete and symbolic evaluation.

Predicates and total monitors own their value carriers and hit rules. They know
neither provider registration nor score, generation, or bounded verdict policy.
The caller supplies the symbolic algebra; this module does not import a solver.
"""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from .frozen import Frozen
from .refusal import Refusal, Refused

__all__ = ['DeclarativeConcreteStep', 'DeclarativeGoalSemantics', 'DeclarativeSymbolicStep', 'FiniteAlgebra', 'FiniteMonitor', 'FiniteMonitorEncoding', 'FiniteMonitorStep', 'FiniteMonitorTransition', 'FinitePredicate', 'FinitePredicateEncoding', 'FiniteVariable']


def _text(value, what):
    """A public/user identifier carried verbatim rather than parsed by the core."""

    if type(value) is not str or not value:
        raise TypeError(f"{what} must be a nonempty exact string")
    return value


def _canonical(value):
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False)
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise TypeError(
            f"goal-provider identity is not finite strict JSON: {exc}") from None


def _value_key(value):
    """A typed canonical key for the deliberately small finite value carrier."""

    kind = type(value)
    if value is None:
        return ("none",)
    if kind is bool:
        return ("bool", value)
    if kind is int:
        return ("int", value)
    if kind is str:
        return ("str", value)
    if kind is tuple:
        return ("tuple", tuple(_value_key(item) for item in value))
    raise TypeError(
        "a declarative finite value is None, bool, int, str, or a tuple of "
        f"those; got {kind.__name__}")


def _value_image(value):
    key = _value_key(value)
    if key[0] == "tuple":
        return {"type": "tuple", "items": [_value_image(item) for item in value]}
    return {"type": key[0], "value": None if value is None else value}


def _value_from_image(image):
    """Decode the typed finite carrier at the public JSON boundary."""
    if type(image) is not dict:
        raise TypeError("a finite value image is an exact mapping")
    if image.get("type") == "tuple":
        if set(image) != {"type", "items"} or type(image["items"]) is not list:
            raise TypeError("a finite tuple image contains type and items")
        return tuple(_value_from_image(item) for item in image["items"])
    kinds = {"none": type(None), "bool": bool, "int": int, "str": str}
    if set(image) != {"type", "value"} or image["type"] not in kinds \
            or type(image["value"]) is not kinds[image["type"]]:
        raise TypeError("a finite scalar image must preserve its exact type")
    return image["value"]


def _exact_equal(left, right):
    return _value_key(left) == _value_key(right)


class FiniteAlgebra(Protocol):
    """Operations needed to encode a finite predicate or monitor symbolically."""

    def literal(self, value): ...

    def equal(self, left, right): ...

    def land(self, *values): ...

    def lor(self, *values): ...

    def ite(self, condition, when_true, when_false): ...


class FiniteVariable(Frozen):
    """One named finite carrier used by a declarative predicate."""

    __slots__ = ("name", "values")

    def __init__(self, name, values):
        # Variable names belong to the task/frontend namespace.  Unlike provider and
        # schema registry keys, the core carries rather than parses them; underscores,
        # capitals and other frontend spellings are therefore legitimate.
        self.name = _text(name, "finite-predicate variable")
        if type(values) not in (tuple, list) or not values:
            raise TypeError("a finite variable needs a nonempty exact value sequence")
        values = tuple(values)
        keys = tuple(_value_key(value) for value in values)
        if len(set(keys)) != len(keys):
            raise ValueError("a finite variable contains the same typed value twice")
        self.values = tuple(value for _key, value in sorted(
            zip(keys, values), key=lambda pair: repr(pair[0])))
        self._freeze()

    def contains(self, value):
        return any(_exact_equal(value, member) for member in self.values)

    def as_dict(self):
        return {"name": self.name,
                "values": [_value_image(value) for value in self.values]}


class FinitePredicate(Frozen):
    """A total Boolean predicate represented by its finite satisfying rows."""

    __slots__ = ("accepting", "goal", "variables")

    def __init__(self, goal, variables, accepting=()):
        self.goal = _text(goal, "finite-predicate goal")
        if type(variables) not in (tuple, list) or not variables:
            raise TypeError("a finite predicate needs a nonempty variable sequence")
        variables = tuple(variables)
        if any(type(item) is not FiniteVariable for item in variables):
            raise TypeError("finite-predicate variables must be exact FiniteVariable records")
        names = tuple(item.name for item in variables)
        if len(set(names)) != len(names):
            raise ValueError("a finite-predicate variable is named twice")
        order = tuple(sorted(range(len(variables)), key=lambda index: names[index]))
        self.variables = tuple(variables[index] for index in order)
        if type(accepting) not in (tuple, list):
            raise TypeError("finite-predicate accepting rows must be an exact sequence")
        rows = []
        for row in accepting:
            if type(row) not in (tuple, list) or len(row) != len(variables):
                raise TypeError("a finite-predicate row has the wrong exact arity")
            row = tuple(row[index] for index in order)
            for variable, value in zip(self.variables, row):
                if not variable.contains(value):
                    raise ValueError(
                        f"finite-predicate row value is outside {variable.name}")
            rows.append(row)
        keys = tuple(tuple(_value_key(value) for value in row) for row in rows)
        if len(set(keys)) != len(keys):
            raise ValueError("a finite predicate contains one satisfying row twice")
        self.accepting = tuple(row for _key, row in sorted(
            zip(keys, rows), key=lambda pair: repr(pair[0])))
        self._freeze()

    def holds(self, observation):
        if type(observation) is not dict:
            raise TypeError("a finite-predicate observation must be an exact dict")
        expected = tuple(variable.name for variable in self.variables)
        if set(observation) != set(expected):
            raise ValueError("a finite-predicate observation has missing or extra fields")
        row = tuple(observation[name] for name in expected)
        for variable, value in zip(self.variables, row):
            if not variable.contains(value):
                raise ValueError(
                    f"finite-predicate observation is outside {variable.name}")
        key = tuple(_value_key(value) for value in row)
        return any(key == tuple(_value_key(value) for value in candidate)
                   for candidate in self.accepting)

    def symbolic(self, terms, algebra: FiniteAlgebra):
        if type(terms) is not dict:
            raise TypeError("finite-predicate symbolic terms must be an exact dict")
        names = tuple(variable.name for variable in self.variables)
        if set(terms) != set(names):
            raise ValueError("finite-predicate symbolic terms have the wrong fields")
        valid = algebra.land(*(algebra.lor(*(
            algebra.equal(terms[variable.name], algebra.literal(value))
            for value in variable.values)) for variable in self.variables))
        rows = tuple(algebra.land(*(
            algebra.equal(terms[variable.name], algebra.literal(value))
            for variable, value in zip(self.variables, row)))
            for row in self.accepting)
        return FinitePredicateEncoding(valid, algebra.lor(*rows))

    def as_dict(self):
        return {
            "goal": self.goal,
            "variables": [variable.as_dict() for variable in self.variables],
            "accepting": [[_value_image(value) for value in row]
                          for row in self.accepting],
        }


class FinitePredicateEncoding(Frozen):
    """Symbolic predicate hit together with its finite-carrier constraint."""

    __slots__ = ("hit", "valid")

    def __init__(self, valid, hit):
        self.valid = valid
        self.hit = hit
        self._freeze()


class FiniteMonitorTransition(Frozen):
    """One row of a total finite-state goal monitor."""

    __slots__ = ("event", "goals", "next_state", "state")

    def __init__(self, state, event, next_state, goals=()):
        _value_key(state)
        _value_key(event)
        _value_key(next_state)
        if type(goals) not in (tuple, list) or any(
                type(goal) is not str or not goal for goal in goals):
            raise TypeError("finite-monitor emitted goals need nonempty exact strings")
        if len(set(goals)) != len(goals):
            raise ValueError("a finite-monitor transition emits one goal twice")
        self.state = state
        self.event = event
        self.next_state = next_state
        self.goals = tuple(sorted(goals))
        self._freeze()

    def as_dict(self):
        return {
            "state": _value_image(self.state),
            "event": _value_image(self.event),
            "next_state": _value_image(self.next_state),
            "goals": list(self.goals),
        }


class FiniteMonitorStep(Frozen):
    """Concrete result of one declarative monitor transition."""

    __slots__ = ("goals", "state")

    def __init__(self, state, goals):
        _value_key(state)
        self.state = state
        self.goals = tuple(goals)
        self._freeze()


class FiniteMonitorEncoding(Frozen):
    """Symbolic monitor step and the constraint that its inputs are in-domain."""

    __slots__ = ("hits", "state", "valid")

    def __init__(self, valid, state, hits):
        if type(hits) not in (tuple, list) or any(
                type(row) is not tuple or len(row) != 2 or type(row[0]) is not str
                for row in hits):
            raise TypeError("symbolic monitor hits are exact goal/term pairs")
        self.valid = valid
        self.state = state
        self.hits = tuple(hits)
        self._freeze()


class FiniteMonitor(Frozen):
    """A total deterministic finite monitor, concrete and symbolically encodable."""

    __slots__ = ("_digest", "events", "goals", "initial", "states", "transitions")

    def __init__(self, states, events, initial, goals, transitions):
        if type(states) not in (tuple, list) or not states \
                or type(events) not in (tuple, list) or not events:
            raise TypeError("a finite monitor needs nonempty state and event sequences")
        states = tuple(states)
        events = tuple(events)
        state_keys = tuple(_value_key(value) for value in states)
        event_keys = tuple(_value_key(value) for value in events)
        if len(set(state_keys)) != len(state_keys) \
                or len(set(event_keys)) != len(event_keys):
            raise ValueError("finite-monitor states and events are typed sets")
        self.states = tuple(value for _key, value in sorted(
            zip(state_keys, states), key=lambda pair: repr(pair[0])))
        self.events = tuple(value for _key, value in sorted(
            zip(event_keys, events), key=lambda pair: repr(pair[0])))
        state_keys = tuple(_value_key(value) for value in self.states)
        event_keys = tuple(_value_key(value) for value in self.events)
        if _value_key(initial) not in set(state_keys):
            raise ValueError("finite-monitor initial state is outside its state set")
        self.initial = initial
        if type(goals) not in (tuple, list) or any(
                type(goal) is not str or not goal for goal in goals):
            raise TypeError("finite-monitor goals need nonempty exact strings")
        if len(set(goals)) != len(goals):
            raise ValueError("a finite-monitor goal is declared twice")
        self.goals = tuple(sorted(goals))
        if type(transitions) not in (tuple, list) or any(
                type(row) is not FiniteMonitorTransition for row in transitions):
            raise TypeError(
                "finite-monitor transitions must be exact transition records")
        rows = {}
        for row in transitions:
            key = _value_key(row.state), _value_key(row.event)
            if key in rows:
                raise ValueError("a finite-monitor transition cell is defined twice")
            if key[0] not in set(state_keys) or key[1] not in set(event_keys):
                raise ValueError("a finite-monitor transition starts outside its carrier")
            if _value_key(row.next_state) not in set(state_keys):
                raise ValueError("a finite-monitor transition leaves its state carrier")
            unknown = sorted(set(row.goals) - set(self.goals))
            if unknown:
                raise ValueError(
                    f"a finite-monitor transition emits undeclared goals {unknown!r}")
            rows[key] = row
        expected = {(state, event) for state in state_keys for event in event_keys}
        if set(rows) != expected:
            raise ValueError("a finite monitor must define every state/event transition")
        self.transitions = tuple(rows[key] for key in sorted(rows, key=repr))
        self._digest = hashlib.sha256(
            _canonical(self.as_dict()).encode("utf-8")).hexdigest()
        self._freeze()

    @property
    def key(self):
        """Stable identity used to bind one monitor's state and event terms."""

        return f"finite-monitor/{self._digest}"

    def digest(self):
        return self._digest

    def step(self, state, event):
        key = _value_key(state), _value_key(event)
        for row in self.transitions:
            if key == (_value_key(row.state), _value_key(row.event)):
                return FiniteMonitorStep(row.next_state, row.goals)
        raise ValueError("finite-monitor state or event is outside its carrier")

    def symbolic(self, state, event, algebra: FiniteAlgebra):
        guards = tuple((
            algebra.land(
                algebra.equal(state, algebra.literal(row.state)),
                algebra.equal(event, algebra.literal(row.event))),
            row,
        ) for row in self.transitions)
        next_state = algebra.literal(self.transitions[-1].next_state)
        for guard, row in reversed(guards[:-1]):
            next_state = algebra.ite(
                guard, algebra.literal(row.next_state), next_state)
        hits = tuple((goal, algebra.lor(*(
            guard for guard, row in guards if goal in row.goals)))
            for goal in self.goals)
        return FiniteMonitorEncoding(
            algebra.lor(*(guard for guard, _row in guards)), next_state, hits)

    def as_dict(self):
        return {
            "states": [_value_image(value) for value in self.states],
            "events": [_value_image(value) for value in self.events],
            "initial": _value_image(self.initial),
            "goals": list(self.goals),
            "transitions": [row.as_dict() for row in self.transitions],
        }

    @classmethod
    def from_dict(cls, image):
        required = {"states", "events", "initial", "goals", "transitions"}
        if type(image) is not dict or set(image) != required:
            raise TypeError("a finite monitor image has states, events, initial, goals and transitions")
        if any(type(image[key]) is not list for key in required - {"initial"}):
            raise TypeError("finite monitor carrier and transition images are lists")
        rows = []
        for row in image["transitions"]:
            if type(row) is not dict or set(row) != {"state", "event", "next_state", "goals"}:
                raise TypeError("a finite transition image has state, event, next_state and goals")
            rows.append(FiniteMonitorTransition(
                _value_from_image(row["state"]), _value_from_image(row["event"]),
                _value_from_image(row["next_state"]), row["goals"]))
        return cls([_value_from_image(value) for value in image["states"]],
                   [_value_from_image(value) for value in image["events"]],
                   _value_from_image(image["initial"]), image["goals"], rows)


class DeclarativeConcreteStep(Frozen):
    """One total concrete step of a declarative goal provider.

    ``hit_values`` retains both reached and non-reached rows so parity checks do not
    silently compare only positive evidence.  Monitor states are keyed by the digest of
    the immutable monitor table that produced them.
    """

    __slots__ = ("hit_values", "monitor_states", "reached")

    def __init__(self, hit_values, monitor_states):
        if type(hit_values) not in (tuple, list) or any(
                type(row) is not tuple or len(row) != 2
                or type(row[0]) is not str or not row[0]
                or type(row[1]) is not bool for row in hit_values):
            raise TypeError(
                "declarative concrete hits are exact goal/bool pairs")
        if type(monitor_states) not in (tuple, list) or any(
                type(row) is not tuple or len(row) != 2
                or type(row[0]) is not str or not row[0]
                for row in monitor_states):
            raise TypeError(
                "declarative concrete monitor states are exact key/value pairs")
        hit_values = tuple(sorted(hit_values))
        monitor_states = tuple(sorted(monitor_states, key=lambda row: row[0]))
        if len({goal for goal, _value in hit_values}) != len(hit_values):
            raise ValueError("a declarative concrete step reports one goal twice")
        if len({key for key, _value in monitor_states}) != len(monitor_states):
            raise ValueError("a declarative concrete step reports one monitor twice")
        for _key, value in monitor_states:
            _value_key(value)
        self.hit_values = hit_values
        self.monitor_states = monitor_states
        self.reached = tuple(goal for goal, hit in hit_values if hit)
        self._freeze()

    def as_dict(self):
        return {
            "hits": [
                {"goal": goal, "reached": reached}
                for goal, reached in self.hit_values
            ],
            "monitor_states": [
                {"monitor": key, "state": _value_image(state)}
                for key, state in self.monitor_states
            ],
        }


class DeclarativeSymbolicStep(Frozen):
    """Symbolic counterpart of :class:`DeclarativeConcreteStep`.

    ``valid`` is the conjunction of every finite predicate and monitor carrier.  Exact
    consumers must add it to their base formula; an out-of-carrier term is never treated
    as a legitimate negative hit.
    """

    __slots__ = ("hit_terms", "monitor_states", "valid")

    def __init__(self, valid, hit_terms, monitor_states):
        if type(hit_terms) not in (tuple, list) or any(
                type(row) is not tuple or len(row) != 2
                or type(row[0]) is not str or not row[0]
                for row in hit_terms):
            raise TypeError("declarative symbolic hits are exact goal/term pairs")
        if type(monitor_states) not in (tuple, list) or any(
                type(row) is not tuple or len(row) != 2
                or type(row[0]) is not str or not row[0]
                for row in monitor_states):
            raise TypeError(
                "declarative symbolic monitor states are exact key/term pairs")
        hit_terms = tuple(sorted(hit_terms, key=lambda row: row[0]))
        monitor_states = tuple(sorted(monitor_states, key=lambda row: row[0]))
        if len({goal for goal, _term in hit_terms}) != len(hit_terms):
            raise ValueError("a declarative symbolic step encodes one goal twice")
        if len({key for key, _term in monitor_states}) != len(monitor_states):
            raise ValueError("a declarative symbolic step encodes one monitor twice")
        self.valid = valid
        self.hit_terms = hit_terms
        self.monitor_states = monitor_states
        self._freeze()


class DeclarativeGoalSemantics(Frozen):
    """One immutable source for concrete and symbolic extension semantics."""

    __slots__ = ("_digest", "monitors", "predicates")

    def __init__(self, predicates=(), monitors=()):
        if type(predicates) not in (tuple, list) or any(
                type(item) is not FinitePredicate for item in predicates):
            raise TypeError("declarative predicates must be exact FinitePredicate records")
        if type(monitors) not in (tuple, list) or any(
                type(item) is not FiniteMonitor for item in monitors):
            raise TypeError("declarative monitors must be exact FiniteMonitor records")
        self.predicates = tuple(sorted(predicates, key=lambda item: item.goal))
        self.monitors = tuple(sorted(monitors, key=lambda item: item.key))
        goals = [item.goal for item in self.predicates]
        goals.extend(goal for monitor in self.monitors for goal in monitor.goals)
        if not goals:
            raise ValueError("declarative goal semantics cannot be empty")
        if len(set(goals)) != len(goals):
            duplicates = sorted({goal for goal in goals if goals.count(goal) > 1})
            raise Refused(Refusal(
                "goal-provider-declarative-owner-ambiguous",
                "one declarative goal has more than one finite semantic owner",
                goals=duplicates))
        self._digest = hashlib.sha256(
            _canonical(self.as_dict()).encode("utf-8")).hexdigest()
        self._freeze()

    @property
    def goals(self):
        return tuple(sorted(
            [item.goal for item in self.predicates]
            + [goal for monitor in self.monitors for goal in monitor.goals]))

    @property
    def monitor_keys(self):
        return tuple(monitor.key for monitor in self.monitors)

    def initial_monitor_states(self):
        """Canonical key/state rows from which concrete or symbolic replay starts."""

        return tuple((monitor.key, monitor.initial) for monitor in self.monitors)

    def _validate_step_inputs(self, predicate_values, monitor_states, monitor_events,
                              *, symbolic):
        description = "terms" if symbolic else "observations"
        for name, value in (
                (f"predicate {description}", predicate_values),
                ("monitor states", monitor_states),
                ("monitor events", monitor_events)):
            if type(value) is not dict:
                raise TypeError(f"declarative {name} must be an exact dict")
        predicate_goals = {predicate.goal for predicate in self.predicates}
        monitor_keys = set(self.monitor_keys)
        if set(predicate_values) != predicate_goals:
            raise ValueError(
                f"declarative predicate {description} have missing or extra goals")
        if set(monitor_states) != monitor_keys:
            raise ValueError("declarative monitor states have missing or extra monitors")
        if set(monitor_events) != monitor_keys:
            raise ValueError("declarative monitor events have missing or extra monitors")

    def concrete_step(self, predicate_observations, monitor_states, monitor_events):
        """Evaluate every declared predicate and monitor exactly once for one slot."""

        self._validate_step_inputs(
            predicate_observations, monitor_states, monitor_events,
            symbolic=False)
        hit_values = []
        next_states = []
        for predicate in self.predicates:
            hit_values.append((predicate.goal, predicate.holds(
                predicate_observations[predicate.goal])))
        for monitor in self.monitors:
            step = monitor.step(
                monitor_states[monitor.key], monitor_events[monitor.key])
            emitted = set(step.goals)
            hit_values.extend(
                (goal, goal in emitted) for goal in monitor.goals)
            next_states.append((monitor.key, step.state))
        return DeclarativeConcreteStep(hit_values, next_states)

    def symbolic_step(self, predicate_terms, monitor_states, monitor_events,
                      algebra: FiniteAlgebra):
        """Encode the same complete step and its finite-carrier constraint."""

        self._validate_step_inputs(
            predicate_terms, monitor_states, monitor_events, symbolic=True)
        valid, hit_terms, next_states = [], [], []
        for predicate in self.predicates:
            encoded = predicate.symbolic(
                predicate_terms[predicate.goal], algebra)
            valid.append(encoded.valid)
            hit_terms.append((predicate.goal, encoded.hit))
        for monitor in self.monitors:
            encoded = monitor.symbolic(
                monitor_states[monitor.key], monitor_events[monitor.key], algebra)
            valid.append(encoded.valid)
            hit_terms.extend(encoded.hits)
            next_states.append((monitor.key, encoded.state))
        # DeclarativeGoalSemantics cannot be empty, so there is always at least one
        # carrier constraint and no backend-specific truth literal is needed here.
        return DeclarativeSymbolicStep(
            algebra.land(*valid), hit_terms, next_states)

    def digest(self):
        return self._digest

    def as_dict(self):
        return {
            "version": "declarative-goal-semantics/1",
            "predicates": [item.as_dict() for item in self.predicates],
            "monitors": [item.as_dict() for item in self.monitors],
        }
