"""Queue origins shared by Behavior and RTL selection over recorded transfers."""

from dataclasses import dataclass

from .core.contract import TaskContract
from .core.input_queue import InputQueueState, endpoint_is_active
from .core.record_value import record_pairs
from .core.refusal import Refusal, Refused
from .core.stimulus import AuditedTrace, Stimulus


@dataclass(frozen=True, slots=True)
class QueueOrigin:
    released: int
    presented: int


def queue_release_slots(
        contract: TaskContract, seed: Stimulus, accepted_rows,
) -> dict[tuple[str, int], int]:
    """Recover release coordinates without claiming when the DUT saw a row.

    Behavior steps use accepted inputs and need only their original queue cells.
    They need not reconstruct a physical adapter or have physical signal samples.
    """

    accepted_rows = tuple(accepted_rows)
    if len(accepted_rows) != len(seed.slots):
        raise Refused(Refusal(
            "generation-source-horizon-mismatch",
            "the concrete selection trace has a different slot count from its seed",
            slots=len(seed.slots), records=len(accepted_rows)))
    sources = {endpoint.name: endpoint for endpoint in contract.inputs()}
    state = InputQueueState.initial(sources)
    released = {}
    multi_clock = len(contract.clocks) > 1
    for slot, (scheduled, row) in enumerate(zip(seed.slots, accepted_rows)):
        if row is None:
            raise TypeError(f"accepted transfers at slot {slot} must be a mapping")
        accepted = dict(record_pairs(row, f"accepted transfers at slot {slot}"))
        active = {name for name, source in sources.items()
                  if endpoint_is_active(source, scheduled, multi_clock)}
        prepared = state.prepare(seed, scheduled, releasable=active)
        for name in sources:
            if prepared.released[name] > state.released[name]:
                released[name, state.released[name]] = slot
        state = state.commit(prepared, accepted)
    return released


def queue_origins(
        contract: TaskContract, seed: Stimulus, trace: AuditedTrace,
) -> dict[tuple[str, int], QueueOrigin]:
    """Recover RTL boundaries using the Driver's registered protocol program.

    Acceptance alone cannot recover first presentation: a busy adapter can hide
    a released queue head. Reuse adapter replay, including its state transitions,
    reset handling and clock projection, instead of interpreting busy/valid pins
    here. This processes existing records; it does not rerun the artifact.
    """
    releases, origins = {}, {}
    for _domain, row in protocol_trace_steps(contract, seed, trace):
        for key in row.releases():
            releases[key] = row.index
        for name, transaction in row.presentations():
            key = name, transaction
            origins.setdefault(key, QueueOrigin(releases[key], row.index))
    return origins


def protocol_trace_steps(contract, seed, trace):
    """Replay registered adapters, retaining each domain's global slot index.

    Both selection and source-query binding use these existing records. Domain
    iteration order is not a physical order between coincident clock edges.
    """
    from .core.atomic import atomic_adapter_programs, project_domain_trace
    from .core.protocol import adapter_program_for

    if len(trace.records) != len(seed.slots):
        raise Refused(Refusal(
            "generation-source-horizon-mismatch",
            "the concrete selection trace has a different slot count from its seed",
            slots=len(seed.slots), records=len(trace.records)))
    if len(contract.clocks) > 1 or seed.clock_schedule is not None:
        domains = (
            (view, program, *project_domain_trace(domain, view, seed, trace))
            for domain, (view, program) in atomic_adapter_programs(contract).items())
    else:
        domains = ((contract, adapter_program_for(contract), seed, trace, range(len(seed.slots))),)
    for view, program, local_seed, local_trace, indexes in domains:
        domain = view.clocks[0].name if view.clocks else ''
        for step in program.iter_replay(view, local_seed, local_trace, slot_indexes=indexes):
            yield domain, step
