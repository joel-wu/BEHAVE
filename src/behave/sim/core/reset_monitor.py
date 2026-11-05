"""One finite reset-restoration monitor, shared by replay and exact analysis.

The legacy reset probe compares two separate simulator invocations.  That can find an
order-dependent batch, but it cannot establish what reset does in a trace.  This
provider instead observes one trace.  It remembers the complete state after the
leading reset sequence, waits for ordinary execution, and checks the state after the
next complete reset sequence against that remembered state.

The transition is written once over a small algebra.  Concrete replay supplies Python
values; the RTL bounded product supplies Z3 terms.  In particular, the concrete and
symbolic meanings of the two goals cannot drift into parallel implementations.

The monitor's own reset policy is explicit: its reference state, readability flag and
``seen_non_reset`` flag are **retained** while design reset is asserted.  Only completion
of the monitored reset sequence consumes ``seen_non_reset``.  Clearing the monitor on
design reset would erase the very pre-reset history that makes the obligation meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .catalogue import COVERAGE, SAFETY, Goal
from .contract import TaskContract
from .frozen import Frozen

__all__ = [
    "ResetRestorationConfig",
    "ResetRestorationEvent",
    "ResetRestorationEvidence",
    "ResetRestorationProvider",
    "ResetRestorationState",
    "ResetRestorationStep",
    "leading_reset_edges",
    "reset_restoration_initial",
    "reset_restoration_state_image",
    "reset_restoration_step",
    "reset_schedule_scenarios",
    "reset_state_domains",
]


@dataclass(frozen=True)
class ResetRestorationConfig:
    """The finite carrier fixed for one run of one declared clock domain."""

    hold_cycles: int
    state_fields: tuple

    def __post_init__(self):
        if type(self.hold_cycles) is not int or self.hold_cycles < 1:
            raise ValueError(
                "a reset-restoration monitor needs a positive reset hold")
        if type(self.state_fields) is not tuple or any(
                type(name) is not str or not name for name in self.state_fields):
            raise TypeError(
                "reset-restoration state fields must be nonempty exact strings")
        if len(set(self.state_fields)) != len(self.state_fields):
            raise ValueError("a reset-restoration state field is named twice")


@dataclass(frozen=True)
class ResetRestorationState:
    """Finite monitor state; members may be concrete values or solver terms."""

    reference: tuple
    reference_valid: object
    reference_readable: object
    seen_non_reset: object
    reset_streak: object


@dataclass(frozen=True)
class ResetRestorationEvent:
    """Reset level and post-step state, with the domain's actual edge flag.

    An inactive step can interrupt a reset pulse, but cannot complete a hold or
    establish that the domain executed ordinary work.
    """

    reset: object
    state_after: tuple
    state_readable: object
    active: object = True


@dataclass(frozen=True)
class ResetRestorationStep:
    """One state update and its two goal bits."""

    state: ResetRestorationState
    scenario: object
    violation: object
    unresolved: object


@dataclass(frozen=True)
class ResetRestorationEvidence:
    """Concrete trace evidence, before the public monitor record is formatted."""

    hits: tuple
    violations: tuple
    unresolved: tuple
    opportunities: tuple


def leading_reset_edges(slots, *, clock_domain=None):
    """Count active edges in the first uninterrupted reset prefix.

    ``None`` is the single-clock slot representation. A named domain uses
    ``ticks`` and ``resets`` from the unprojected schedule. Idle control changes
    before its first edge are allowed; after that edge any release ends the
    prefix, even when the domain has no edge at the release step.
    """
    if clock_domain is None:
        from .stimulus import leading_reset_slots

        return leading_reset_slots(slots)
    held = 0
    for slot in slots:
        active = clock_domain in slot.ticks
        reset = clock_domain in slot.resets
        if not reset and (active or held):
            break
        held += int(active and reset)
    return held


def reset_restoration_initial(config, zero_state, algebra):
    """Initial monitor state for ``config``.

    ``zero_state`` supplies correctly sorted zero terms.  They are placeholders until
    the leading reset completes; ``reference_valid`` prevents them being interpreted as
    an observed design state.
    """

    if type(config) is not ResetRestorationConfig:
        raise TypeError("reset restoration needs the exact config type")
    zero_state = tuple(zero_state)
    if len(zero_state) != len(config.state_fields):
        raise ValueError("the reset reference has the wrong state arity")
    return ResetRestorationState(
        reference=zero_state,
        reference_valid=algebra.bool(False),
        reference_readable=algebra.bool(False),
        seen_non_reset=algebra.bool(False),
        reset_streak=algebra.integer(0),
    )


def reset_restoration_state_image(state, read=lambda value: value):
    """Return every finite reset-monitor component in one shared field layout."""

    if type(state) is not ResetRestorationState:
        raise TypeError("the reset monitor image needs its exact state type")
    if not callable(read):
        raise TypeError("the reset monitor image reader must be callable")
    return {
        "reference": tuple(read(value) for value in state.reference),
        "reference_valid": read(state.reference_valid),
        "reference_readable": read(state.reference_readable),
        "seen_non_reset": read(state.seen_non_reset),
        "reset_streak": read(state.reset_streak),
    }


def reset_restoration_step(config, state, event, algebra):
    """Advance the shared concrete/symbolic reset-restoration automaton."""

    if type(config) is not ResetRestorationConfig:
        raise TypeError("reset restoration needs the exact config type")
    if type(state) is not ResetRestorationState:
        raise TypeError("reset restoration needs the exact state type")
    if type(event) is not ResetRestorationEvent:
        raise TypeError("reset restoration needs the exact event type")
    if len(state.reference) != len(config.state_fields) \
            or len(event.state_after) != len(config.state_fields):
        raise ValueError("a reset-restoration event has the wrong state arity")

    false = algebra.bool(False)
    zero = algebra.integer(0)
    one = algebra.integer(1)
    hold = algebra.integer(config.hold_cycles)

    # Saturation is important: holding reset longer than the declared sequence is one
    # reset event, not a fresh opportunity on every additional edge.
    reset_streak = algebra.ite(
        event.reset,
        algebra.ite(
            event.active,
            algebra.ite(
                algebra.eq(state.reset_streak, hold),
                hold,
                algebra.add(state.reset_streak, one)),
            state.reset_streak),
        zero)
    completes = algebra.land(
        event.active, event.reset, algebra.eq(reset_streak, hold))
    capture = algebra.land(completes, algebra.lnot(state.reference_valid))
    scenario = algebra.land(
        completes, state.reference_valid, state.seen_non_reset)

    equal = algebra.land(*(algebra.eq(before, after)
                           for before, after in zip(
                               state.reference, event.state_after)))
    comparable = algebra.land(
        state.reference_readable, event.state_readable)
    violation = algebra.land(scenario, comparable, algebra.lnot(equal))
    unresolved = algebra.land(scenario, algebra.lnot(comparable))

    reference = tuple(
        algebra.ite(capture, observed, remembered)
        for remembered, observed in zip(state.reference, event.state_after))
    reference_valid = algebra.lor(state.reference_valid, capture)
    reference_readable = algebra.ite(
        capture, event.state_readable, state.reference_readable)

    # A completed post-work reset consumes the scenario.  Otherwise every ordinary
    # active edge after the baseline records that the domain executed since reset.
    seen_non_reset = algebra.ite(
        algebra.lor(capture, scenario),
        false,
        algebra.lor(
            state.seen_non_reset,
            algebra.land(state.reference_valid, event.active,
                         algebra.lnot(event.reset))))

    return ResetRestorationStep(
        state=ResetRestorationState(
            reference=reference,
            reference_valid=reference_valid,
            reference_readable=reference_readable,
            seen_non_reset=seen_non_reset,
            reset_streak=reset_streak,
        ),
        scenario=scenario,
        violation=violation,
        unresolved=unresolved,
    )


class ResetRestorationProvider(Frozen):
    """Task declaration plus the shared concrete/symbolic hit rule."""

    POLICY = "reset-restoration/3"
    RESET_POLICY: ClassVar = {
        "reference": "retain",
        "reference_readable": "retain",
        "seen_non_reset": "retain-until-sequence-completes",
        "reset_streak": "advance-on-active-reset-edge-clear-on-any-release",
    }

    __slots__ = ("contract", "domains")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("ResetRestorationProvider is closed and cannot be subclassed")

    def __init__(self, contract):
        if type(contract) is not TaskContract:
            raise TypeError("reset restoration needs the exact TaskContract")
        self.contract = contract
        self.domains = tuple(
            domain for domain in contract.clocks
            if domain.reset_restores is True)
        self._freeze()

    @staticmethod
    def scenario_goal(domain):
        return f"monitor.reset-restoration.{domain}.scenario"

    @staticmethod
    def violation_goal(domain):
        return f"monitor.reset-restoration.{domain}.violation"

    def goals(self):
        goals = {}
        for domain in self.domains:
            provenance = {
                "clock": domain.clock,
                "clock_domain": domain.name,
                "monitor_reset_policy": dict(self.RESET_POLICY),
                "reset": domain.reset,
                "reset_active_low": domain.reset_active_low,
            }
            scenario = self.scenario_goal(domain.name)
            violation = self.violation_goal(domain.name)
            goals[scenario] = Goal(
                scenario, COVERAGE, family="reset-scenario",
                provider="reset-restoration", event="reset-after-execution",
                policy=self.POLICY, evidence="trace", provenance=provenance)
            goals[violation] = Goal(
                violation, SAFETY, family="reset-restoration",
                provider="reset-restoration", event="state-not-restored",
                policy=self.POLICY, evidence="trace", provenance=provenance)
        return goals

    def single_domain(self):
        """The claimed domain supported by the current single-clock trace record."""

        if not self.domains:
            return None
        if len(self.contract.clocks) != 1 or len(self.domains) != 1:
            from .refusal import Refusal, Refused

            raise Refused(Refusal(
                "reset-restoration-multiclock-trace-unavailable",
                "the current trace's reset flag does not identify one domain in a "
                "multi-clock design, so it cannot carry a reset-restoration hit",
                claimed=[domain.name for domain in self.domains],
                clocks=[domain.name for domain in self.contract.clocks]))
        return self.domains[0]

    def check(self, stimulus, trace, *, state_fields=None, clock_domain=None):
        """Judge one concrete trace with the same transition used by exact analysis.

        ``state_fields`` is the frontend-owned complete state carrier.  Without it the
        scenario is still observable from the schedule, but the safety result is
        unresolved rather than inferred from whatever fields happened to be recorded.
        ``clock_domain`` selects a domain in an unprojected global schedule;
        otherwise every slot is a single-clock edge.
        """

        domain = self.single_domain()
        if domain is None:
            return ResetRestorationEvidence((), (), (), ())
        if clock_domain is not None and clock_domain != domain.name:
            raise ValueError("reset observation must use the provider's clock domain")

        from .ready_valid_product import ConcreteAlgebra
        hold = leading_reset_edges(stimulus.slots, clock_domain=clock_domain)
        if hold < 1:
            from .refusal import Refusal, Refused

            raise Refused(Refusal(
                "reset-restoration-sequence-empty",
                "a task claims reset restoration but this trace domain has no leading "
                "reset sequence", domain=domain.name))
        if state_fields is None:
            fields = ()
            complete_state_declared = False
        else:
            if type(state_fields) not in (tuple, list):
                raise TypeError("reset state fields must be an exact tuple or list")
            fields = tuple(state_fields)
            if any(type(name) is not str or not name for name in fields) \
                    or len(set(fields)) != len(fields):
                raise TypeError(
                    "reset state fields must be unique nonempty exact strings")
            complete_state_declared = True
        config = ResetRestorationConfig(hold, fields)
        algebra = ConcreteAlgebra()
        state = reset_restoration_initial(config, (0,) * len(fields), algebra)
        scenario_goal = self.scenario_goal(domain.name)
        violation_goal = self.violation_goal(domain.name)
        hits, violations, unresolved, opportunities = [], [], [], []

        observed_count = trace.observed_count
        for slot, (scheduled, record) in enumerate(
                zip(stimulus.slots, trace.records)):
            if slot >= observed_count:
                break
            image = dict(record.state)
            # A multi-clock trace carries the union of all domain-owned fields.  The
            # frontend-owned local list is still complete for this component; unrelated
            # fields from other domains are not a reason to make it unreadable.
            readable = (complete_state_declared
                        and set(fields).issubset(image)
                        and all(type(image[name]) in (bool, int)
                                for name in fields))
            observed = tuple(image[name] if readable else 0 for name in fields)
            before = state
            stepped = reset_restoration_step(
                config, state,
                ResetRestorationEvent(
                    reset=(bool(scheduled.reset) if clock_domain is None
                           else clock_domain in scheduled.resets),
                    active=(clock_domain is None or clock_domain in scheduled.ticks),
                    state_after=observed, state_readable=readable),
                algebra)
            state = stepped.state
            if not stepped.scenario:
                continue
            opportunities.extend((scenario_goal, violation_goal))
            hits.append((scenario_goal, slot))
            if stepped.violation:
                hits.append((violation_goal, slot))
                different = [
                    name for name, old, new in zip(
                        fields, before.reference, observed) if old != new]
                violations.append((
                    violation_goal, slot,
                    ("the complete reset sequence ended after ordinary execution and "
                     "the RTL state differs from the state recorded after the leading "
                     f"reset (first differing field {different[0]!r})")))
            elif stepped.unresolved:
                unresolved.append((
                    violation_goal, slot,
                    ("the reset-restoration scenario completed, but the trace does not "
                     "carry the frontend's complete finite RTL state both after the "
                     "leading reset and after this reset")))

        return ResetRestorationEvidence(
            tuple(hits), tuple(violations), tuple(unresolved),
            tuple(opportunities))


def reset_schedule_scenarios(contract, stimulus):
    """Project activation from a fixed schedule; never assert restoration.

    This is a query/preparation fact, not native execution evidence. The shared
    transition's scenario bit is independent of the state carrier/readability.
    Use its empty unreadable carrier to decide whether state capture is needed;
    preserve real active edges, intervening releases and leading hold length.
    """
    from .stimulus import uses_global_events

    return _reset_schedule_scenarios(contract, stimulus.slots,
                                    global_events=uses_global_events(contract, stimulus))


def reset_state_domains(contract, slots=None, *, global_events=False):
    """State carriers needed by restoration in the declared request domain.

    Omitted slots mean a symbolic future schedule: retain every claimed domain.
    Supplied slots must be the fixed, authenticated calendar, never one sampled
    execution. The shared automaton's scenario is independent of payloads and
    state/readability. If it cannot activate, neither can its violation, so its
    carrier may be empty while both goal predicates remain in the product.
    """
    provider = ResetRestorationProvider(contract)
    if slots is None:
        return frozenset(domain.name for domain in provider.domains)
    scenarios = {goal for goal, _ in _reset_schedule_scenarios(
        contract, tuple(slots), global_events=global_events)}
    return frozenset(domain.name for domain in provider.domains
                     if provider.scenario_goal(domain.name) in scenarios)


def _reset_schedule_scenarios(contract, slots, *, global_events):
    from .ready_valid_product import ConcreteAlgebra

    provider = ResetRestorationProvider(contract)
    algebra = ConcreteAlgebra()
    scenarios = []
    for domain in provider.domains:
        clock = domain.name if global_events else None
        hold = leading_reset_edges(slots, clock_domain=clock)
        config = ResetRestorationConfig(hold, ())
        state = reset_restoration_initial(config, (), algebra)
        for index, slot in enumerate(slots):
            stepped = reset_restoration_step(config, state, ResetRestorationEvent(
                reset=domain.name in slot.resets if global_events else bool(slot.reset),
                active=domain.name in slot.ticks if global_events else True,
                state_after=(), state_readable=False), algebra)
            state = stepped.state
            if stepped.scenario:
                scenarios.append((provider.scenario_goal(domain.name), index))
    return tuple(sorted(scenarios))
