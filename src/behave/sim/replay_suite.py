"""Isolated sequences followed by continuous, state-preserving workloads.

Both phases are ordinary stimuli consumed by the same replay/assessment service.
Concatenation changes the workload, not the meaning of a transaction. It never
joins runs with different initial states or invents a finite clock horizon.
"""

from dataclasses import dataclass, replace

from .core.refusal import Refusal, Refused
from .core.stimulus import Stimulus, canonical_json

REPLAY_SUITE_VERSION = "replay-suite/3"


@dataclass(frozen=True, slots=True)
class ReplaySuite:
    contract: str
    isolated: tuple[Stimulus, ...]
    continuous: tuple[Stimulus, ...]
    members: tuple[tuple[str, tuple[str, ...]], ...]
    required: tuple[Stimulus, ...] | None = None

    @property
    def required_stimuli(self):
        return self.stimuli if self.required is None else self.required

    @property
    def stimuli(self):
        return self.isolated + self.continuous

    @property
    def phases(self):
        return (("isolated", self.isolated), ("continuous", self.continuous))

    def as_dict(self):
        return {"version": REPLAY_SUITE_VERSION, "contract": self.contract,
                "isolated": [s.fingerprint for s in self.isolated],
                "continuous": [s.fingerprint for s in self.continuous],
                "required": [s.fingerprint for s in self.required_stimuli],
                "members": {key: list(values) for key, values in self.members}}


def build_replay_suite(contract, stimuli, *, baseline=None):
    """Keep each request isolated, then concatenate compatible input queues.

    Each continuous workload gets a fresh reset/reference and receptive outputs.
    Reset duration, observation tail and initial state are preserved. The caller
    must supply runtime requests: a fixed schedule cannot be concatenated while
    silently discarding its resets, control events or exact-analysis domain.
    """
    isolated = tuple(stimuli)
    if not isolated or any(type(s) is not Stimulus for s in isolated):
        raise TypeError("a replay suite needs a nonempty sequence of Stimulus records")
    if any(s.replay_policy is None for s in isolated):
        raise Refused(Refusal("replay-suite-fixed-schedule",
                              "continuous replay requires transaction-driven requests"))
    groups, seen = {}, set()
    memory_images = {}
    for stimulus in isolated:
        faults = stimulus.faults_against(contract)
        if faults:
            raise Refused(faults[0])
        key = stimulus.identity_bytes
        if key in seen:
            raise ValueError("deduplicate the input pool before constructing a replay suite")
        seen.add(key)
        policy = replace(stimulus.replay_policy, stall_every=0)
        environment = stimulus.memory_environment
        if environment is not None and environment not in memory_images:
            # The same immutable RAM image is shared by many baseline members.
            # Encode it once, retaining exact bytes rather than grouping by a
            # digest or constructing a detached cell list for every request.
            memory_images[environment] = canonical_json(environment.as_dict())
        key = (canonical_json({"initial_state": stimulus.initial_state,
                               "memory_policy": stimulus.memory_policy,
                               "policy": policy.as_dict()}),
               None if environment is None else memory_images[environment])
        groups.setdefault(key, []).append(stimulus)

    continuous, members = [], []
    for group in groups.values():
        first = group[0]
        request = Stimulus(
            queues={endpoint.name: tuple(row for s in group for row in dict(s.queues)[endpoint.name])
                    for endpoint in contract.inputs()},
            replay_policy=replace(first.replay_policy, stall_every=0),
            initial_state=first.initial_state, memory_policy=first.memory_policy,
            memory_environment=first.memory_environment,
            source="continuous-replay")
        members.append((request.fingerprint, tuple(s.fingerprint for s in group)))
        # With one already-receptive member, the two phases are the same run.
        # Do not multiply identical evidence or count its goals twice.
        if request.identity_bytes not in seen:
            continuous.append(request)
            seen.add(request.identity_bytes)
    required = None
    if baseline is not None:
        fixed = build_replay_suite(contract, baseline)
        if not {s.identity_bytes for s in fixed.isolated}.issubset(
                s.identity_bytes for s in isolated):
            raise ValueError("baseline must belong to the isolated pool")
        required = fixed.stimuli
        # Keep the fixed baseline workload even when Search expands the pool.
        # Its completed record is reused; the expanded workload is additional.
        combined = {s.identity_bytes: s for s in (*fixed.continuous, *continuous)}
        isolated_keys = {s.identity_bytes for s in isolated}
        continuous = [s for key, s in combined.items() if key not in isolated_keys]
        members = list(dict((*fixed.members, *members)).items())
    return ReplaySuite(contract.digest, isolated, tuple(continuous), tuple(members), required)


def workload_window(contract, trace, *, expected_outputs):
    """Read start/finish boundaries from a single-clock runtime replay.

    Start is the first offer, including any initial DUT stall. Finish follows
    all required input/output transfers. Later observations still affect score,
    but not the workload's duration, including final external-memory comparison.
    This function neither replays nor scores.
    """
    request = trace.request
    if len(contract.clocks) != 1 or request is None:
        raise ValueError("workload timing requires a single-clock runtime trace")
    targets = {e.name: request.depth(e.name) for e in contract.inputs()}
    if set(expected_outputs) != {e.name for e in contract.outputs()} or any(
            type(n) is not int or n < 0 for n in expected_outputs.values()):
        raise ValueError("expected_outputs must count each declared output endpoint")
    targets.update(expected_outputs)
    counts = dict.fromkeys(targets, 0)
    start = finish = None
    for index, (slot, record) in enumerate(zip(trace.stimulus.slots, trace.records, strict=True)):
        if start is None and slot.offers and not slot.reset:
            start = index
        for name in record.transfers:
            if name in counts:
                counts[name] += 1
        if start is not None and finish is None and all(counts[n] >= v for n, v in targets.items()):
            finish = index + 1
    return {"start_slot": start, "finish_slot": finish,
            "work_slots": None if finish is None else finish - start,
            "observation_slots": None if finish is None else len(trace.records) - finish}
