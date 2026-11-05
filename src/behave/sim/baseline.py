"""Versioned, backend-neutral construction of the common generation baseline.

The baseline is not a weak substitute for either exact provider.  It is the finite,
deterministic seed set from which both Appendix A.3 searches start.  Its values come
only from the task's recursive finite types, and its schedules come only from the task
contract plus the frozen run policy.  Solver discoveries and replay hits cannot alter
it.
"""

from __future__ import annotations

import hashlib
import json

from behave.core import runtime_defaults as defaults

from .core.clock_schedule import (
    ClockTiming,
    GlobalEventSlot,
    GlobalSchedule,
    validate_external_bindings,
)
from .core.frozen import Frozen
from .core.memory import MemoryEnvironment, MemoryRun
from .core.memory_sampling import (
    MEMORY_IMAGE_MODES,
    MEMORY_SAMPLING_VERSION,
    memory_image_mode,
    sampled_memory_initial,
)
from .core.protocol import registered_adapter
from .core.record_value import (
    FrozenValueMap,
    freeze_record_value,
    thaw_record_value,
)
from .core.refusal import Refusal, Refused
from .core.runtime_schedule import environment_controls
from .core.stimulus import EventSlot, Stimulus
from .generation_defaults import (
    DEFAULT_BASELINE_CASES,
    DEFAULT_SEQUENCE_LENGTH,
    DEFAULT_STIMULUS_SEED,
)
from .stimulus_patterns import PATTERN_VERSION, PatternPlan, pattern_case
from .stimulus_set import GenerationSpec, stimulus_admissible

__all__ = [
    "BASELINE_VERSION",
    "BaselinePolicy",
    "BaselineSet",
    "build_baseline",
]


BASELINE_VERSION = "generation-baseline/13"
_CLOCK_PERIODS = defaults.SIM_BASELINE_CLOCK_PERIODS
_MEMORY_READ_LATENCY = defaults.SIM_BASELINE_MEMORY_READ_LATENCY
_MEMORY_STALL_RATE = defaults.SIM_BASELINE_MEMORY_STALL_RATE
_BASELINE_SET = object()


def _materialize_baseline(contract, generation_spec, proposed, *, realization=None):
    """Resolve the two public command inputs to one exact baseline record.

    The public command constructors accept either a baseline already built for this
    run or the policy from which to build it.  Raw stimulus sequences deliberately do
    not enter this boundary: they have no version, construction policy or provenance
    identity to bind to the command.
    """

    if type(proposed) is BaselineSet:
        return proposed
    if type(proposed) is BaselinePolicy:
        return build_baseline(
            contract, realization=realization, policy=proposed)
    raise TypeError(
        "a public simulator command baseline is an exact BaselineSet or "
        "BaselinePolicy")


def _digest(body):
    image = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False)
    return hashlib.sha256(image.encode("utf-8")).hexdigest()


class BaselinePolicy(Frozen):
    """The complete deterministic policy used to construct baseline members."""

    __slots__ = ("case_limit", "max_queue_depth", "seed")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("BaselinePolicy is a closed policy record")

    def __init__(self, *, seed=DEFAULT_STIMULUS_SEED,
                 case_limit=DEFAULT_BASELINE_CASES,
                 max_queue_depth=DEFAULT_SEQUENCE_LENGTH):
        if type(seed) is not str or not seed:
            raise TypeError("a baseline seed is a nonempty exact string")
        for name, value in (
                ("case_limit", case_limit),
                ("max_queue_depth", max_queue_depth)):
            if type(value) is not int or value < 1:
                raise ValueError(f"baseline {name} is a positive exact integer")
        self.seed = seed
        self.case_limit = case_limit
        self.max_queue_depth = max_queue_depth
        self._freeze()

    @property
    def identity(self):
        return f"{BASELINE_VERSION}:{_digest(self.as_dict())}"

    def as_dict(self):
        return {
            "version": BASELINE_VERSION,
            "patterns": PATTERN_VERSION,
            "seed": self.seed,
            "case_limit": self.case_limit,
            "max_queue_depth": self.max_queue_depth,
            "clock_periods": list(_CLOCK_PERIODS),
            "memory_read_latency": _MEMORY_READ_LATENCY,
            "memory_stall_rate": _MEMORY_STALL_RATE,
            "memory_sampling": MEMORY_SAMPLING_VERSION,
            "memory_image_modes": list(MEMORY_IMAGE_MODES),
        }


class BaselineSet(Frozen):
    """One canonical baseline and the schedule provenance of every member."""

    __slots__ = ("contract", "generation_spec", "policy", "provenance", "stimuli")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("BaselineSet is a closed generation-boundary record")

    def __init__(self, policy, stimuli, provenance, *, contract, generation_spec,
                 _token=None):
        if _token is not _BASELINE_SET:
            raise TypeError("a BaselineSet is produced only by build_baseline()")
        if type(policy) is not BaselinePolicy:
            raise TypeError("a baseline set needs an exact BaselinePolicy")
        if type(contract) is not str or not contract:
            raise TypeError("a baseline set needs its task-contract identity")
        if generation_spec is not None and type(generation_spec) is not GenerationSpec:
            raise TypeError("a baseline set needs its exact GenerationSpec")
        stimuli = tuple(stimuli)
        if not stimuli or any(type(item) is not Stimulus for item in stimuli):
            raise TypeError("a baseline is a nonempty sequence of exact stimuli")
        bodies = tuple(_stimulus_bytes(item) for item in stimuli)
        if bodies != tuple(sorted(bodies)) or len(set(bodies)) != len(bodies):
            raise ValueError("baseline stimuli are exact-byte sorted and unique")
        provenance = dict(provenance or {})
        expected = {item.fingerprint for item in stimuli}
        if set(provenance) != expected:
            raise ValueError("baseline provenance covers exactly the baseline members")
        for fingerprint, rows in provenance.items():
            if type(rows) not in (tuple, list) or not rows:
                raise TypeError(
                    f"baseline provenance for {fingerprint[:12]} is nonempty")
            if any(type(row) is not dict for row in rows):
                raise TypeError("baseline provenance rows are exact dictionaries")
        self.policy = policy
        self.contract = contract
        self.generation_spec = generation_spec
        self.stimuli = stimuli
        self.provenance = tuple(
            (fingerprint, tuple(
                freeze_record_value(
                    row, f"baseline.provenance.{fingerprint}[{index}]")
                for index, row in enumerate(provenance[fingerprint])))
            for fingerprint in sorted(provenance))
        if any(type(row) is not FrozenValueMap
               for _, rows in self.provenance for row in rows):
            raise TypeError("baseline provenance must freeze to exact mappings")
        self._freeze()

    def provenance_dict(self):
        return {fingerprint: [thaw_record_value(row) for row in rows]
                for fingerprint, rows in self.provenance}

    def as_dict(self):
        return {
            "version": BASELINE_VERSION,
            "contract": self.contract,
            "generation_spec": None if self.generation_spec is None else {
                "identity": self.generation_spec.identity,
                **self.generation_spec.as_dict(),
            },
            "policy": self.policy.as_dict(),
            "policy_identity": self.policy.identity,
            "stimuli": [item.as_dict() for item in self.stimuli],
            "provenance": self.provenance_dict(),
        }


def _stimulus_bytes(stimulus):
    return stimulus.identity_bytes


def _number(policy, salt, modulus):
    # Keep the external-memory policy's seed unchanged by payload-pattern v1.
    payload = f"{policy.seed}\0{salt}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest(), "big") % modulus


def _transactions(contract, policy, depths):
    endpoints = tuple(contract.inputs())
    plan = PatternPlan(field.type for endpoint in endpoints for field in endpoint.fields)
    for case in range(policy.case_limit):
        queues = {endpoint.name: [] for endpoint in endpoints}
        depth = max(depths.values())
        for index, row in enumerate(plan.rows(case, depth, seed=policy.seed)):
            start = 0
            for endpoint in endpoints:
                end = start + len(endpoint.fields)
                if index < depths[endpoint.name]:
                    queues[endpoint.name].append(row[start:end])
                start = end
        for endpoint in endpoints:
            if endpoint.framing is not None:
                queues[endpoint.name] = endpoint.framing.shape(
                    queues[endpoint.name], tuple(field.name for field in endpoint.fields))
        yield case, queues


def _single_clock_schedule(contract, spec):
    endpoints = tuple(endpoint.name for endpoint in contract.inputs())
    if not contract.clocks:
        if spec.reset_hold_cycles or spec.drain:
            raise Refused(Refusal(
                "generation-baseline-settled-timing",
                "clockless settled sampling requires zero reset and drain slots"))
        return tuple(EventSlot(offers=endpoints) for _ in range(spec.horizon)), None
    domain = contract.clocks[0]
    body_end = spec.horizon - spec.drain
    slots = []
    for index in range(spec.horizon):
        in_reset = index < spec.reset_hold_cycles
        body = not in_reset and index < body_end
        slots.append(EventSlot(
            offers=endpoints if body else (),
            reset=in_reset,
            controls=environment_controls(
                contract, index, (domain.name,) if in_reset else (),
                None if index >= body_end else spec.stall_every),
        ))
    return tuple(slots), None


def _multi_clock_schedule(contract, realization, spec):
    if realization is None:
        raise Refused(Refusal(
            "generation-baseline-clock-binding-missing",
            "a multi-clock baseline needs the admitted artifact binding to prove that "
            "every scheduled clock and reset is environment driven"))
    timings = tuple(
        ClockTiming(
            domain.name,
            _CLOCK_PERIODS[index % len(_CLOCK_PERIODS)],
            phase=_CLOCK_PERIODS[index % len(_CLOCK_PERIODS)],
            edge=domain.edge,
            clock=domain.clock,
            reset=domain.reset,
            reset_active_low=domain.reset_active_low,
        )
        for index, domain in enumerate(contract.clocks)
    )
    primary_inputs = {
        name for name, port in realization.ports.items()
        if port.driven_by == "environment"
    }
    validate_external_bindings(timings, primary_inputs)
    base = GlobalSchedule.periodic(
        timings, spec.horizon,
        reset_edges={domain.name: spec.reset_hold_cycles
                     for domain in contract.clocks})
    by_domain = {}
    for endpoint in contract.inputs():
        by_domain.setdefault(endpoint.clock, []).append(endpoint.name)
    slots = []
    scheduled = []
    body_end = spec.horizon - spec.drain
    for index, original in enumerate(base.slots):
        # Reset is domain-local. A released domain still samples its inputs
        # while another domain completes its independent reset prefix.
        body = index < body_end
        offers = tuple(sorted(
            endpoint
            for domain in original.events
            for endpoint in by_domain.get(domain, ())
            if body and domain not in original.resets))
        event = EventSlot(
            offers=offers,
            ticks=original.events,
            resets=original.resets,
            controls=environment_controls(
                contract, index, original.resets,
                None if index >= body_end else spec.stall_every),
        )
        slots.append(event)
        scheduled.append(GlobalEventSlot(original.time, event=event))
    return tuple(slots), GlobalSchedule(timings, scheduled)


def _schedule_digest(slots, clock_schedule):
    if clock_schedule is not None:
        return clock_schedule.digest
    return _digest([slot.as_dict() for slot in slots])


def _memory_environment(contract, spec, policy, *, case):
    """One memory policy shared by the baseline, finite only for an explicit domain."""

    if not contract.memories:
        return None
    return MemoryEnvironment([
        MemoryRun.from_policy(
            memory,
            None if spec is None else spec.horizon,
            read_latency=_MEMORY_READ_LATENCY,
            stall_rate=_MEMORY_STALL_RATE,
            seed=_number(policy, f"external-memory:{memory.name}", 1 << 63),
            initial=sampled_memory_initial(memory, seed=policy.seed, case=case),
        )
        for memory in contract.memories
    ])


def _memory_provenance(contract, stimulus, cases):
    if stimulus.memory_environment is None:
        return {}
    return {
        "memory_environment": stimulus.memory_environment.digest,
        "memory_images": [{"case": case, "memories": {
            memory.name: (memory_image_mode(case) if memory.sampling else "raw")
            for memory in contract.memories}} for case in cases],
    }


def _baseline_queue_range(endpoint, slots, body_slots, *, multiclock):
    """Ask the registered adapter, rather than infer latency from protocol pins."""
    offers = sum(
        endpoint.name in slots[index].offers for index in body_slots)
    samples = sum(
        endpoint.clock in slot.ticks and endpoint.clock not in slot.resets
        if multiclock else not slot.reset for slot in slots)
    return registered_adapter(
        endpoint.adapter.name, endpoint.adapter.version).baseline_queue_range(
            endpoint, offers=offers, samples=samples)


def build_baseline(contract, generation_spec=None, *, realization=None, policy=None,
                   execution_policy=None):
    """Build the nonempty common baseline for one admitted finite trace domain."""

    from .core.contract import TaskContract

    if type(contract) is not TaskContract:
        raise TypeError("baseline construction needs the exact TaskContract")
    if generation_spec is not None and type(generation_spec) is not GenerationSpec:
        raise TypeError("baseline construction needs the frozen GenerationSpec")
    if policy is None:
        policy = BaselinePolicy()
    if type(policy) is not BaselinePolicy:
        raise TypeError("baseline construction needs an exact BaselinePolicy")
    if not contract.inputs():
        raise Refused(Refusal(
            "generation-baseline-no-input-endpoint",
            "the score is over accepted input transactions, so a baseline cannot be "
            "constructed for a task with no input endpoint"))

    if generation_spec is None:
        from .core.runtime_schedule import ReplayPolicy

        execution_policy = execution_policy or ReplayPolicy.for_contract(contract)
        if type(execution_policy) is not ReplayPolicy:
            raise TypeError("runtime baseline needs a ReplayPolicy")
        depths = {endpoint.name: policy.max_queue_depth for endpoint in contract.inputs()}
        from .core.sampled_runtime import SampledClockWindow

        sampled_window = SampledClockWindow.for_contract(contract, execution_policy)
        if sampled_window is not None:
            depths = sampled_window.baseline_depths(policy.max_queue_depth)
        members, cases = {}, {}
        for case, queues in _transactions(contract, policy, depths):
            memory_environment = _memory_environment(contract, None, policy, case=case)
            request = Stimulus(queues=queues, replay_policy=execution_policy,
                               memory_environment=memory_environment,
                               source=f"{policy.identity}/case/{case}")
            body = request.identity_bytes
            members.setdefault(body, request)
            cases.setdefault(body, []).append(case)
        provenance = {request.fingerprint: [{
            "schedule": "runtime-request", "of": policy.identity,
            "baseline_policy": policy.identity, "queue_depths": depths,
            "execution_policy": execution_policy.as_dict(),
            "cases": cases[body],
            "patterns": [{"case": case, **pattern_case(case).as_dict()}
                         for case in cases[body]],
            **_memory_provenance(contract, request, cases[body]),
        }] for body, request in sorted(members.items())}
        return BaselineSet(policy, tuple(members[body] for body in sorted(members)), provenance,
                           contract=contract.digest, generation_spec=None, _token=_BASELINE_SET)
    if execution_policy is not None:
        raise ValueError("choose a fixed generation domain or a runtime execution policy")

    if len(contract.clocks) <= 1:
        slots, clock_schedule = _single_clock_schedule(contract, generation_spec)
    else:
        slots, clock_schedule = _multi_clock_schedule(
            contract, realization, generation_spec)
    body_slots = tuple(
        index for index, slot in enumerate(slots)
        if slot.offers and index < generation_spec.horizon - generation_spec.drain)
    if not body_slots:
        raise Refused(Refusal(
            "generation-baseline-domain-uninhabitable",
            "the frozen schedule contains no released active slot in which an input "
            "transaction can be offered"))
    ranges = {
        endpoint.name: _baseline_queue_range(
            endpoint, slots, body_slots, multiclock=len(contract.clocks) > 1)
        for endpoint in contract.inputs()
    }
    minimum_depths = {name: lower for name, (lower, upper) in ranges.items()}
    per_endpoint = {name: upper for name, (lower, upper) in ranges.items()}
    bound = min(policy.max_queue_depth, generation_spec.queue_bound)
    if any(lower > bound for lower in minimum_depths.values()):
        raise Refused(Refusal(
            "generation-baseline-required-input-depth",
            "the fixed sampling schedule requires more input transactions than "
            "the declared baseline or generation bound permits",
            required=minimum_depths, bound=bound))
    depths = {name: min(bound, upper) for name, upper in per_endpoint.items()}
    if any(depth < 1 for depth in depths.values()):
        raise Refused(Refusal(
            "generation-baseline-domain-uninhabitable",
            "at least one input endpoint has no active offer slot in the frozen "
            "schedule",
            offers=per_endpoint))

    members = {}
    cases = {}
    for case, queues in _transactions(contract, policy, depths):
        memory_environment = _memory_environment(contract, generation_spec, policy, case=case)
        stimulus = Stimulus(
            queues=queues,
            slots=slots,
            source=f"{policy.identity}/case/{case}",
            clock_schedule=clock_schedule,
            memory_environment=memory_environment,
        )
        faults = stimulus_admissible(
            stimulus, contract,
            horizon=generation_spec.horizon,
            reset_hold_cycles=generation_spec.reset_hold_cycles,
            queue_bound=generation_spec.queue_bound,
        )
        if faults:
            raise Refused(Refusal(
                "generation-baseline-outside-domain",
                "the versioned baseline constructor produced a stimulus outside its "
                "own frozen domain",
                case=case, faults=faults))
        body = _stimulus_bytes(stimulus)
        members.setdefault(body, stimulus)
        cases.setdefault(body, []).append(case)

    stimuli = tuple(members[body] for body in sorted(members))
    schedule_digest = _schedule_digest(slots, clock_schedule)
    provenance = {
        stimulus.fingerprint: [{
            "schedule": "constructed-baseline",
            "of": policy.identity,
            "digest": schedule_digest,
            "slots": len(stimulus.slots),
            "baseline_policy": policy.identity,
            "queue_depths": depths,
            "patterns": [
                {"case": case, **pattern_case(case).as_dict()}
                for case in cases[body]
            ],
            **_memory_provenance(contract, stimulus, cases[body]),
            "cases": list(cases[body]),
        }]
        for body, stimulus in sorted(members.items())
    }
    return BaselineSet(
        policy, stimuli, provenance,
        contract=contract.digest, generation_spec=generation_spec,
        _token=_BASELINE_SET)
