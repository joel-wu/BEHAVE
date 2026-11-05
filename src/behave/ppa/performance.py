"""PPA workloads and unit conversion of Sim's public completion windows."""

from dataclasses import replace

from .config import Workload


def endpoints(contract, work: Workload):
    from behave.sim import api

    ins, outs = tuple(contract.inputs()), tuple(contract.outputs())
    if len(contract.clocks) != 1 or len(ins) != 1 or len(outs) != 1 or contract.memories:
        raise ValueError("PPA pilot needs one clock, one input, one output, no external memory")
    left, right = ins[0], outs[0]
    if left.adapter.name != "ready-valid" or right.adapter.name != "ready-valid":
        raise ValueError("PPA pilot only measures declared ready/valid streams")
    if right.matching is not api.Matching.ORDERED:
        raise ValueError("PPA workload requires ordered responses")
    if any(len(row) != len(left.fields) for row in work.inputs):
        raise ValueError("workload input arity differs from the task")
    clock = contract.clocks[0]
    if clock.edge != "rising" or work.reset_cycles != clock.reset_hold_cycles:
        raise ValueError("pilot requires rising-edge measurement and the task's exact reset duration")
    if any(not field.type.admits(value) for row in work.inputs for field, value in zip(left.fields, row)):
        raise ValueError("workload value is outside its declared task type")
    return left, right


def stimulus(contract, work: Workload):
    from behave.sim import api

    left, _ = endpoints(contract, work)
    policy = replace(api.ReplayPolicy.for_contract(contract),
                     reset_hold_cycles=work.reset_cycles, stall_every=0)
    return api.Stimulus(queues={left.name: work.inputs}, replay_policy=policy)


def completion(window, work: Workload, period_ns: float):
    """Sim owns completion detection. One slot is one cycle in this profile."""
    cycles = window["work_slots"]
    return {"workload": work.identity, "start_edge": window["start_slot"],
            "finish_edge": window["finish_slot"], "cycles": cycles,
            "time_ns": None if cycles is None else cycles * period_ns,
            "status": "not-completed" if cycles is None else "completed"}
