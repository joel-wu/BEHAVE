"""Run-owned memory survives computation reset, not a fresh environment."""

import pytest

from behave.sim.behavior.isolated import IsolatedModel, Limits
from behave.sim.behavior.reference_memory import ReferenceMemory, preserve_external_memory
from behave.sim.core.memory import MemoryEnvironment, MemoryInterface, MemoryPort, MemoryRun


def memory_fixture():
    interface = MemoryInterface("ram", 3, 4, "clk", [MemoryPort("rw")])
    run = MemoryRun("ram", initial={2: 5}, ready={None: [1] * 4})
    return interface, run


@pytest.mark.parametrize("latency", [1, 2, 5])
def test_reset_cancels_only_its_domain_even_without_a_clock_edge(latency):
    from behave.sim.core.memory_runtime import MemoryRuntime

    interfaces = [MemoryInterface(name, 3, 4, name, [MemoryPort("rw")])
                  for name in ("a", "b")]
    environment = MemoryEnvironment([MemoryRun(
        name, initial={2: 5, 3: 9}, read_latency=latency,
        ready={None: [1] * 12}) for name in ("a", "b")])
    runtime = MemoryRuntime(interfaces, environment)

    def signals(write=False, address=2, value=14):
        return {f"{name}_{pin}": word for name in ("a", "b") for pin, word in
                (("req_valid", 1), ("req_addr", address),
                 ("req_we", int(write)), ("req_wdata", value))}

    runtime.commit(runtime.prepare(0, signals(write=True)))
    runtime.commit(runtime.prepare(1, signals()))
    before = runtime.snapshot()
    driven = runtime.drive(2, reset_domains={"a"})
    assert all(driven[f"a_{pin}"] == 0
               for pin in ("req_ready", "resp_valid", "resp_rdata"))
    # A would write 7 if reset failed to take priority. Neither clock ticks.
    runtime.commit(runtime.prepare(2, signals(write=True, value=7),
                                   active_domains=(), reset_domains={"a"}))
    after = runtime.snapshot()
    assert after.state("a").cells == before.state("a").cells
    assert after.state("a").writes == before.state("a").writes == ((2, 14),)
    assert after.state("a").pending == ((None, ()),)
    assert after.state("a").responses == ((None, None),)
    assert after.state("b") == before.state("b")
    # A new address produces only its own response, never the canceled word 14.
    runtime.commit(runtime.prepare(3, signals(address=3)))
    for slot in range(4, 4 + latency):
        driven = runtime.drive(slot)
        if driven["a_resp_valid"]:
            assert driven["a_resp_rdata"] == 9
            break
        idle = {pin: 0 if pin.endswith("req_valid") else value
                for pin, value in signals().items()}
        runtime.commit(runtime.prepare(slot, idle))
    else:
        pytest.fail("the post-reset read never responded")


def test_atomic_dut_reset_cancels_reads_but_retains_committed_writes():
    from behave.sim import api
    from behave.sim.core.atomic import run_atomic_in_memory
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
    from behave.sim.core.memory import MemoryEnvironmentState
    from behave.sim.core.record_value import thaw_record_value

    memory = MemoryInterface("ram", 3, 4, "a", [MemoryPort("rw")])
    environment = MemoryEnvironment([MemoryRun(
        "ram", initial={2: 5}, read_latency=2, ready={None: [1] * 4})])
    contract = api.TaskContract([api.Endpoint(
        "out", "out", [api.Field("data", api.FiniteType(4), "data")],
        api.ready_valid("out", "valid", "ready"), clock="b", kind=api.Kind.TRANSACTION)],
        [api.ClockDomain("a", "a_clk", "a_rst"),
         api.ClockDomain("b", "b_clk", "b_rst")], memories=(memory,))
    schedule = GlobalSchedule([
        ClockTiming(name, 1, clock=name + "_clk", reset=name + "_rst") for name in ("a", "b")],
        [GlobalEventSlot(i, event=api.EventSlot(
            ticks=("a", "b"), resets=("a",) if i == 2 else (),
            controls={"a_rst": int(i == 2), "b_rst": 0, "ready": 1})) for i in range(4)])
    stimulus = api.Stimulus(slots=[slot.event for slot in schedule.slots],
                           clock_schedule=schedule, memory_environment=environment)

    class Device:
        def atomic_step(self, slot, drive):
            return {"valid": 0, "data": 0, "ram_req_valid": int(slot.time < 2),
                    "ram_req_addr": 2, "ram_req_we": int(slot.time == 0), "ram_req_wdata": 14}

    trace = run_atomic_in_memory(contract, stimulus, Device())
    reset_state = MemoryEnvironmentState.from_dict(thaw_record_value(
        dict(trace.records[2].environment)["external-memory"])).state("ram")
    assert dict(reset_state.cells)[2] == dict(reset_state.writes)[2] == 14
    assert dict(reset_state.pending)[None] == ()
    assert dict(reset_state.responses)[None] is None
    assert MemoryEnvironmentState.from_dict(thaw_record_value(
        dict(trace.records[3].environment)["external-memory"])).state("ram") == reset_state


@pytest.mark.parametrize("mutation", ["rebind", "in-place", "delete"])
@pytest.mark.parametrize("raised", [False, True])
def test_reset_preserves_sparse_store_binding_and_write_evidence(mutation, raised):
    interface, run = memory_fixture()
    memory = ReferenceMemory(interface, run)
    memory[2] = 14
    fields = {"ram": memory, "control": 9}
    try:
        with preserve_external_memory(fields, ("ram",)):
            fields["control"] = 0
            if mutation == "rebind":
                fields["ram"] = [0] * 8
            elif mutation == "in-place":
                fields["ram"][:] = [0] * 8
            else:
                del fields["ram"]
            if raised:
                raise RuntimeError("source reset failed")
    except RuntimeError:
        assert raised
    assert fields["ram"] is memory
    assert memory._store == {2: 14}
    assert memory.writes == {2: 14}
    assert fields["control"] == 0
    assert ReferenceMemory(interface, run)[2] == 5


@pytest.mark.parametrize("representation", ["reference", "array"])
@pytest.mark.parametrize("reset_body", [
    "pass", "self.ram = [0] * 8", "self.ram[:] = [0] * 8", "del self.ram",
])
def test_isolated_reset_preserves_external_memory(representation, reset_body):
    source = f'''class M:
    def __init__(self):
        self.ram = [0] * 8
        self.count = 0
    def reset(self):
        {reset_body}
        self.count = 0
    def process(self, write, value):
        self.count += 1
        if write:
            self.ram[2] = value
        return {{"y": self.ram[2], "count": self.count}}
'''
    interface, run = memory_fixture()
    environment = MemoryEnvironment([run])
    with IsolatedModel(source, cls="M", strict_module=False,
                       limits=Limits(seconds=30, wall_seconds=15, memory_bytes=512 << 20)) as model:
        model._bind_external_memory((interface,), environment, 4, representation=representation)
        assert model.process(write=0, value=0) == {"y": 5, "count": 1}
        assert model.process(write=1, value=14) == {"y": 14, "count": 2}
        model.reset()
        assert model.process(write=0, value=0) == {"y": 14, "count": 1}
        # Only an explicit new environment reinstalls initial cells.
        model._bind_external_memory((interface,), environment, 4, representation=representation)
        model.reset()
        assert model.process(write=0, value=0) == {"y": 5, "count": 1}


@pytest.mark.solver
def test_source_audit_keeps_memory_between_reset_epochs(monkeypatch):
    from behave.sim import api
    from behave.sim.behavior import reference_memory
    from behave.sim.behavior.task_boundary import lower_task_behavior
    from behave.sim.core.audit import AuditCheckStatus

    source = '''class M:
    def reset(self):
        self.ram = [0] * 8
        self.count = 0
    def process(self, write, value):
        self.count = (self.count + 1) & 15
        if write:
            self.ram[2] = value
        return {"y": self.ram[2], "count": self.count}
'''
    memory, run = memory_fixture()
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("write", api.FiniteType(1), "write"),
            api.Field("value", api.FiniteType(4), "value")],
            api.ready_valid("in", "iv", "ir"), clock="clk"),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(4), "y"),
            api.Field("count", api.FiniteType(4), "count")],
            api.ready_valid("out", "ov", "orr"), clock="clk")],
        [api.ClockDomain("clk", "clk", "rst")], memories=(memory,))
    instance, _ = lower_task_behavior(source, "behavior.py", contract, 3,
        memory_environment=MemoryEnvironment([run]), memory_horizon=4)
    inputs = ({"write": 1, "value": 14}, {"write": 0, "value": 0},
              {"write": 1, "value": 9})
    observed = []
    checked = instance.source_audit(
        (inputs,), reset_indices=((1,),),
        observe_step=lambda trace, step, state, hits, outputs: observed.append(state))
    assert checked.status is AuditCheckStatus.AGREED, checked.as_dict()
    assert observed[1]["count"] == 0
    assert observed[1]["ram"][2] == 14
    # Reintroducing initial-memory reload must disagree with the real child.
    monkeypatch.setattr(reference_memory, "reset_state",
                        lambda initial, state, fields, **kw: dict(initial))
    checked = instance.source_audit((inputs,), reset_indices=((1,),))
    assert checked.status is AuditCheckStatus.INCONSISTENT, checked.as_dict()
