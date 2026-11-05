"""Finite exact transition tables compiled from registered protocol programs.

The concrete drivers execute :class:`~behave.sim.core.protocol.AdapterProgram`
directly.  Exact RTL analysis must not reproduce those protocol rules in a second
hand-written state machine.  This module evaluates the same registered program over
its declared finite control carrier once, freezes the resulting total table, and
provides an algebra-parametric symbolic lookup.

The table deliberately abstracts payload *values*, not payload presence.  A program
may use this boundary only when it explicitly declares that its control state,
accepted/emitted events and protocol violations do not depend on payload values.  The
actual payload terms still flow through the RTL product whenever a table row says that
an endpoint transferred.  Environment-owned assumption failures mark table rows
inadmissible; they are never encoded as goal hits.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass
from types import MappingProxyType

from .frozen import FrozenMap
from .physical_value import (
    PHYSICAL_VALUE_LAYOUT_VERSION,
    pack_value,
    zero_value,
)
from .protocol import ARTIFACT
from .refusal import Refusal, Refused
from .stimulus import EventSlot, canonical_json

__all__ = [
    "AdapterExactEncoding",
    "AdapterExactTable",
    "AdapterExactTransition",
]


def _value_key(value):
    """A typed, hashable key for the adapter's closed finite carrier."""

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
    if kind is dict or isinstance(value, FrozenMap):
        return (
            "map",
            tuple(sorted(
                ((name, _value_key(item)) for name, item in value.items()),
                key=lambda row: row[0])),
        )
    raise TypeError(
        f"{kind.__name__} is outside the finite adapter exact carrier")


def _plain(value):
    kind = type(value)
    if value is None or kind in (bool, int, str):
        return value
    if kind is tuple:
        return [_plain(item) for item in value]
    if kind is dict or isinstance(value, FrozenMap):
        return {name: _plain(item) for name, item in sorted(value.items())}
    raise TypeError(
        f"{kind.__name__} is outside the finite adapter exact carrier")


def _bit_rows(names):
    names = tuple(names)
    return tuple(tuple(zip(names, values))
                 for values in itertools.product((False, True), repeat=len(names)))


def _representative_payload(endpoint):
    """One admitted payload used only while compiling payload-independent control."""

    return tuple(zero_value(field.type) for field in endpoint.fields)


@dataclass(frozen=True)
class AdapterExactTransition:
    """One cell of the finite adapter control table's legal presentation domain."""

    state: int
    reset: bool
    presented: tuple
    scheduled: tuple
    observed: tuple
    drive: tuple
    environment_admissible: bool
    next_state: int
    accepted: tuple
    emitted: tuple
    goals: tuple

    def as_dict(self):
        return {
            "state": self.state,
            "reset": self.reset,
            "presented": dict(self.presented),
            "scheduled": dict(self.scheduled),
            "observed": dict(self.observed),
            "drive": dict(self.drive),
            "environment_admissible": self.environment_admissible,
            "next_state": self.next_state,
            "accepted": list(self.accepted),
            "emitted": list(self.emitted),
            "goals": list(self.goals),
        }


@dataclass(frozen=True)
class AdapterExactEncoding:
    """One symbolic table lookup and its finite-carrier constraint."""

    valid: object
    state: object
    drive: MappingProxyType
    accepted: MappingProxyType
    emitted: MappingProxyType
    hits: MappingProxyType


class AdapterExactTable:
    """A finite control transition compiled from one AdapterProgram.

    ``contract`` is a one-clock or clockless-settled :class:`TaskContract`, or
    the immutable per-domain view used by the atomic driver. They expose the
    same endpoint and scheduled-control interface consumed by an adapter program.
    Required-input samples without a payload are outside the environment domain;
    their absent table row makes the symbolic lookup inadmissible.
    """

    VERSION = "adapter-exact-control-table/7"

    def __init__(self, program, contract):
        if getattr(program, "exact_control_product", False) is not True:
            raise Refused(Refusal(
                "adapter-exact-control-product-unsupported",
                "this registered adapter does not declare a payload-independent "
                "finite control transition for exact RTL analysis",
                adapter=f"{getattr(program, 'name', '')}/v"
                        f"{getattr(program, 'version', '')}"))
        policy = getattr(program, "exact_control_product_policy", None)
        if type(policy) is not str or not policy or "/" not in policy:
            raise Refused(Refusal(
                "adapter-exact-payload-independence-unbound",
                "an exact adapter table needs the registered implementation's "
                "versioned payload-independence declaration",
                adapter=f"{getattr(program, 'name', '')}/v"
                        f"{getattr(program, 'version', '')}"))
        from .protocol import SETTLED

        settled = not contract.clocks and program.name == SETTLED
        if len(contract.clocks) != 1 and not settled:
            raise TypeError("an adapter exact table belongs to one clock or settled-sample domain")
        program.validate(contract)
        self.program = program
        self.contract = contract
        self.inputs = tuple(contract.inputs())
        self.outputs = tuple(contract.outputs())
        self.presented_names = tuple(endpoint.name for endpoint in self.inputs)
        reset_name = contract.clocks[0].reset if contract.clocks else None
        self.scheduled_names = tuple(
            name for name in contract.scheduled_controls() if name != reset_name)
        self.observed_names = tuple(sorted({
            name for endpoint in contract.endpoints
            for name in endpoint.adapter.artifact_controls
        }))
        self.drive_names = tuple(sorted({
            *((reset_name,) if reset_name is not None else ()),
            *(name for endpoint in contract.endpoints
              for name in endpoint.adapter.environment_controls),
        }))
        self.goal_names = tuple(sorted(
            program.monitor_goals_owned_by(contract, ARTIFACT)))
        self.payload_independence = policy

        states = tuple(program.state_space(contract))
        if not states:
            raise Refused(Refusal(
                "adapter-exact-state-space-empty",
                "an exact adapter product needs at least one finite state"))
        keys = tuple(_value_key(state) for state in states)
        if len(set(keys)) != len(keys):
            raise Refused(Refusal(
                "adapter-exact-state-space-not-a-set",
                "the registered adapter state space contains duplicate typed values"))
        self.states = states
        self.state_count = len(states)
        self._state_indexes = {key: index for index, key in enumerate(keys)}
        presentation = []
        for state_index, state in enumerate(states):
            row = []
            for endpoint in self.inputs:
                allowed = program.presentation_allowed(
                    contract, state, endpoint)
                if type(allowed) is not bool:
                    raise Refused(Refusal(
                        "adapter-exact-presentation-not-boolean",
                        "the registered adapter's finite presentation policy did "
                        "not return an exact Boolean",
                        state=state_index, endpoint=endpoint.name))
                row.append((endpoint.name, allowed))
            presentation.append(tuple(row))
        self.presentation = tuple(presentation)
        self.initial_state = self.state_index(program.initial_state(contract))
        self.elapsed_states = tuple(
            self.state_index(program.elapse(contract, state)) for state in states)
        self.transitions = self._compile()
        # Drive is computed before sampling in the concrete program. Keep its
        # lookup in that same causal order, rather than repeating each drive
        # under every possible observed ready/valid bit combination. The latter
        # is equivalent, but introduces artificial feedback into symbolic RTL.
        self._drive_rows = tuple({
            (row.state, row.reset, row.presented, row.scheduled): row
            for row in self.transitions
        }.values())
        image = self.as_dict()
        self._digest = hashlib.sha256(
            canonical_json(image).encode("utf-8")).hexdigest()

    @property
    def digest(self):
        return self._digest

    def state_index(self, state):
        try:
            return self._state_indexes[_value_key(state)]
        except KeyError:
            raise Refused(Refusal(
                "adapter-exact-transition-left-state-space",
                "the registered adapter transition returned a state outside its "
                "declared finite state space")) from None

    def state_at(self, index, request_value=None):
        if type(index) is not int or not 0 <= index < len(self.states):
            raise ValueError("an adapter exact state index is outside its carrier")
        return self.states[index]

    def held_requests(self, state):
        return {}

    @property
    def observed_widths(self):
        return dict.fromkeys(self.observed_names, 1)

    def presentation_allowed(self, state, endpoint, algebra):
        """Encode whether ``endpoint`` may be presented in this adapter state."""

        if type(endpoint) is not str or endpoint not in self.presented_names:
            raise ValueError("an exact presentation names one input endpoint")
        allowed_states = tuple(
            index for index, row in enumerate(self.presentation)
            if dict(row)[endpoint]
        )
        return algebra.lor(*(
            algebra.equal(state, algebra.literal(index))
            for index in allowed_states
        ))

    def _compile(self):
        clock = self.contract.clocks[0] if self.contract.clocks else None

        def reset_level(asserted):
            return int(not asserted if clock.reset_active_low else asserted)

        presentations = _bit_rows(self.presented_names)
        scheduled = _bit_rows(self.scheduled_names)
        observed = _bit_rows(self.observed_names)
        representatives = {
            endpoint.name: _representative_payload(endpoint)
            for endpoint in self.inputs
        }
        output_values = {
            field.port: pack_value(field.type, zero_value(field.type))
            for endpoint in self.outputs for field in endpoint.fields
        }
        rows = []
        for state_index, state in enumerate(self.states):
            for reset in ((False, True) if clock is not None else (False,)):
                for presented_row in presentations:
                    presented_bits = dict(presented_row)
                    presented = {
                        endpoint.name: (
                            representatives[endpoint.name]
                            if presented_bits[endpoint.name] and not reset else None)
                        for endpoint in self.inputs
                    }
                    # Plain sampled inputs cannot idle. This is the same declared
                    # presentation precondition enforced by the concrete adapter;
                    # absent rows have symbolic valid=False, never a made-up transfer.
                    if not reset and any(
                            self.program.requires_input_each_step(endpoint)
                            and presented[endpoint.name] is None
                            for endpoint in self.inputs):
                        continue
                    offers = tuple(
                        name for name, value in presented.items()
                        if value is not None)
                    for scheduled_row in scheduled:
                        scheduled_bits = dict(scheduled_row)
                        controls = {
                            **{name: int(value)
                               for name, value in scheduled_bits.items()},
                            **({clock.reset: reset_level(reset)} if clock is not None else {}),
                        }
                        slot = EventSlot(
                            offers=offers, controls=controls, reset=reset,
                            ticks=(clock.name,) if clock is not None else ())
                        drive = self.program.drive(
                            self.contract, state, slot, presented, controls)
                        environment_violations = self.program.environment_violations(
                            self.contract, state, slot, drive)
                        if set(drive.controls) != set(self.drive_names):
                            raise Refused(Refusal(
                                "adapter-exact-drive-shape-mismatch",
                                "the registered adapter drive and its declared "
                                "environment-control set differ",
                                missing=sorted(set(self.drive_names)
                                               - set(drive.controls)),
                                extra=sorted(set(drive.controls)
                                             - set(self.drive_names))))
                        for observed_row in observed:
                            signals = {
                                **output_values,
                                **{name: int(value)
                                   for name, value in observed_row},
                            }
                            transition = self.program.step(
                                self.contract, state, slot, drive, signals)
                            _checks, artifact_violations = \
                                self.program.artifact_events(
                                    self.contract, transition)
                            next_state = self.state_index(transition.state)
                            accepted = tuple(sorted(
                                name for name, payload
                                in transition.accepted_inputs.items()
                                if payload is not None))
                            emitted = tuple(sorted(
                                name for name, payload
                                in transition.emitted_outputs.items()
                                if payload is not None))
                            direct_goals = {
                                item.goal for item in artifact_violations}
                            goals = tuple(sorted(direct_goals))
                            unknown = sorted(set(goals) - set(self.goal_names))
                            if unknown:
                                raise Refused(Refusal(
                                    "adapter-exact-transition-goal-undeclared",
                                    "the registered adapter emitted a protocol goal "
                                    "outside its declaration",
                                    goals=unknown))
                            rows.append(AdapterExactTransition(
                                state_index, reset,
                                tuple(presented_row), tuple(scheduled_row),
                                tuple(observed_row),
                                tuple(sorted(
                                    (name, bool(value))
                                    for name, value in drive.controls.items())),
                                not environment_violations,
                                next_state, accepted, emitted, goals))
        return tuple(rows)

    def symbolic_elapse(self, state, algebra):
        """One inactive scheduled step, compiled from the same concrete program.

        Return the carrier guard as well as the successor: an unknown state must not
        select a default valid row. No protocol observation or goal is invented.
        """
        guards = [algebra.equal(state, algebra.literal(index))
                  for index in range(len(self.states))]
        successor = algebra.literal(-1)
        for guard, target in reversed(tuple(zip(guards, self.elapsed_states))):
            successor = algebra.ite(guard, algebra.literal(target), successor)
        return algebra.lor(*guards), successor

    def symbolic(self, state, reset, presented, scheduled, observed, algebra):
        """Encode one lookup through the table using ``algebra`` operations."""

        for label, got, expected in (
                ("presented", presented, self.presented_names),
                ("scheduled", scheduled, self.scheduled_names),
                ("observed", observed, self.observed_names)):
            if type(got) is not dict or set(got) != set(expected):
                raise ValueError(
                    f"adapter exact {label} terms have missing or extra fields")

        def literal(value):
            return algebra.literal(value)

        def guard(row, *, include_observed):
            tests = [
                algebra.equal(state, literal(row.state)),
                algebra.equal(reset, literal(row.reset)),
            ]
            tests.extend(algebra.equal(presented[name], literal(value))
                         for name, value in row.presented)
            tests.extend(algebra.equal(scheduled[name], literal(value))
                         for name, value in row.scheduled)
            if include_observed:
                tests.extend(algebra.equal(observed[name], literal(value))
                             for name, value in row.observed)
            return algebra.land(*tests)

        guards = tuple((guard(row, include_observed=True), row)
                       for row in self.transitions)
        drives = tuple((guard(row, include_observed=False), row)
                       for row in self._drive_rows)

        def choose(rows, value_of):
            term = literal(value_of(rows[-1][1]))
            for predicate, row in reversed(rows[:-1]):
                term = algebra.ite(predicate, literal(value_of(row)), term)
            return term

        valid = algebra.lor(*(
            guard for guard, row in drives
            if row.environment_admissible))
        drive = MappingProxyType({
            name: choose(drives, lambda row, key=name: dict(row.drive)[key])
            for name in self.drive_names
        })
        accepted = MappingProxyType({
            name: algebra.lor(*(guard for guard, row in guards
                                if name in row.accepted))
            for name in self.presented_names
        })
        emitted = MappingProxyType({
            endpoint.name: algebra.lor(*(guard for guard, row in guards
                                         if endpoint.name in row.emitted))
            for endpoint in self.outputs
        })
        hits = MappingProxyType({
            goal: algebra.lor(*(guard for guard, row in guards
                                if goal in row.goals))
            for goal in self.goal_names
        })
        return AdapterExactEncoding(
            valid, choose(guards, lambda row: row.next_state), drive,
            accepted, emitted, hits)

    def as_dict(self):
        return {
            "version": self.VERSION,
            "physical_value_layout": PHYSICAL_VALUE_LAYOUT_VERSION,
            "adapter": {
                "name": self.program.name,
                "version": self.program.version,
            },
            "payload_independence": self.payload_independence,
            "states": [_plain(state) for state in self.states],
            "initial": self.initial_state,
            "elapsed_states": list(self.elapsed_states),
            "presentation_allowed": [
                {name: value for name, value in row}
                for row in self.presentation
            ],
            "presented": list(self.presented_names),
            "scheduled": list(self.scheduled_names),
            "observed": list(self.observed_names),
            "drive": list(self.drive_names),
            "goals": list(self.goal_names),
            "transitions": [row.as_dict() for row in self.transitions],
        }
