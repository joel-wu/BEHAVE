"""A timestamped one-clock run keeps event, reset and latency semantics."""

from time import monotonic

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.core.latency_monitor import LatencyMonitor
from behave.sim.core.refusal import Refused
from behave.sim.rtl.exact_product import global_schedule_for_stimuli, stimulus_for_global_schedule

from .test_baseline_public_sequence import _contract
from .test_rtl_transaction_goal_loop import _confirm, _provider, _second_question, _seed


def declared_case(*, cancel=False, period=2):
    base = _contract()
    rule = api.LatencyGoal("paired", api.LatencyEvent("in", ("a",)),
        api.LatencyEvent("out", ("y",)), (api.LatencyBin("wanted", 4, 4),),
        api.LatencyTimebase("global-time"), reset_domains=("clk",) if cancel else ())
    contract = api.TaskContract(base.endpoints, base.clocks, latency_goals=(rule,))
    old = _seed(7, reset=True)
    schedule = GlobalSchedule((ClockTiming("clk", period, clock="clk", reset="rst"),), [
        GlobalEventSlot(period * i, event=api.EventSlot(ticks=("clk",),
            resets=("clk",) if row.reset else (), offers=row.offers,
            controls=dict(row.controls))) for i, row in enumerate(old.slots)])
    seed = api.Stimulus(queues=dict(old.queues), slots=[entry.event for entry in schedule.slots],
                        clock_schedule=schedule)
    return contract, seed


def test_single_clock_schedule_admission_uses_named_reset_events():
    contract, seed = declared_case()
    assert seed.faults_against(contract) == []


def test_single_clock_schedule_roundtrip_retains_time_and_events():
    contract, seed = declared_case()
    schedule = global_schedule_for_stimuli(contract, (seed,))
    assert schedule.as_dict() == seed.clock_schedule.as_dict()
    decoded = stimulus_for_global_schedule(schedule, dict(seed.queues), source="roundtrip")
    assert decoded.clock_schedule.as_dict() == schedule.as_dict()
    assert decoded.slots == seed.slots
    assert decoded.queues == seed.queues


def test_implicit_slots_cannot_invent_a_global_timebase():
    contract, _ = declared_case()
    with pytest.raises(Refused, match="rtl-exact-global-time-unbound"):
        global_schedule_for_stimuli(contract, (_seed(7, reset=True),))


def _interleaved_case():
    contract = _contract()
    schedule = GlobalSchedule((ClockTiming("clk", 2, clock="clk", reset="rst"),), [
        GlobalEventSlot(time, event=api.EventSlot(
            ticks=("clk",) if time % 2 == 0 else (),
            resets=("clk",) if time in (0, 3) else (),
            offers=("in",) if time == 6 else (),
            controls={"rst": int(time in (0, 3)), "out_ready": 1}))
        for time in range(9)])
    return contract, api.Stimulus(
        {"in": ((7,),)}, [slot.event for slot in schedule.slots], clock_schedule=schedule)


def test_control_only_events_do_not_release_or_accept_inputs():
    from behave.sim.core.atomic import AtomicDriver
    from behave.sim.core.driver import Driver
    from behave.sim.core.input_queue import input_completion_environment
    from behave.sim.core.monitor import monitor_for

    contract, seed = _interleaved_case()
    assert not seed.faults_against(contract)
    with pytest.raises(Refused, match="driver-needs-one-clock-domain"):
        Driver(contract, seed)
    driver = AtomicDriver(contract, seed)
    for index in range(len(seed.slots)):
        drive = driver.drive(index)
        driver.observe(index, drive, {
            "in_ready": 1, "out_valid": int(index == 6), "y": 7})
    trace = driver.trace()
    assert [(index, dict(row.accepted).get("in")) for index, row in enumerate(trace.records)
            if dict(row.accepted).get("in") is not None] == [(6, (7,))]
    assert input_completion_environment(seed, contract).drain_slots == (8,)
    report = monitor_for(contract).check(seed, trace)
    assert not report.violations and not report.assumption_violations


def test_reference_reset_and_tick_follow_events_not_record_count():
    from behave.sim.core.reference import expected_run_of
    from tests.support.reference import load

    source = b'''class M:
    def reset(self):
        self.n = 0
    def tick(self):
        self.n += 1
    def process(self, a):
        return {"y": a + self.n}
'''
    contract, seed = _interleaved_case()
    bundle = load({"behavior.py": source}, {"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(
        source.decode(), contract, file="behavior.py", model="M"), reference=bundle)
    # A deliberately wrong candidate output must not enter reference execution.
    trace = api.AuditedTrace(seed, records=[api.SlotRecord(
        accepted={"in": (7,), "out": (255,)} if index == 6 else {})
        for index in range(len(seed.slots))])
    outputs, memory = expected_run_of(task, bundle, trace)
    assert outputs == {"out": [(8,)]}
    assert memory.memories == ()


def test_replay_plan_selects_explicit_events_not_number_of_clocks():
    from behave.sim.rtl.execution_policy import execution_policy
    from behave.sim.rtl.replay_plan import ATOMIC_DRIVER_PLAN_VERSION, DRIVER_PLAN_VERSION, ReplayPlan

    contract, seed = declared_case()
    plan = {"version": ATOMIC_DRIVER_PLAN_VERSION, "contract": contract,
            "stimuli": [seed], "watch": [], "record_names": [], "initial_state": None,
            "execution_policy": execution_policy(), "state_fields": [], "artifact": "test"}
    assert ReplayPlan.from_dict(plan).atomic
    implicit = {key: value for key, value in plan.items() if key not in ("state_fields", "artifact")}
    implicit.update(version=DRIVER_PLAN_VERSION, state_names=[])
    with pytest.raises(RuntimeError, match="another clock product"):
        ReplayPlan.from_dict(implicit)
    with pytest.raises(RuntimeError, match="another clock product"):
        ReplayPlan.from_dict({**plan, "stimuli": [seed, _seed(7)]})


def test_timed_context_binds_memory_after_the_schedule(tmp_path):
    from behave.sim.core.catalogue import CatalogueManifest
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.exact_job_context import RTLExactJobContext

    from .test_rtl_memory_boundary import memory_run

    base, seed = declared_case()
    memory, environment, _ = memory_run(horizon=len(seed.slots))
    contract = api.TaskContract(base.endpoints, base.clocks, memories=(memory,))
    path = tmp_path / "unprepared.sv"
    path.write_text("module M; endmodule\n")
    artifact = RTLArtifact(path, "M", tmp_path / "rtl")
    context = RTLExactJobContext(artifact, contract,
        api.GenerationSpec(len(seed.slots), 2, 2, 0), CatalogueManifest(), CatalogueManifest(),
        workdir=tmp_path / "query", clock_schedule=seed.clock_schedule)
    assert context.memory_environment is None
    context.bind_memory_environment(environment)
    assert context.memory_environment is environment


@pytest.mark.isolation
@pytest.mark.parametrize("has_input", (False, True))
def test_behavior_ordinary_entry_keeps_named_reset_and_control_only_events(tmp_path, has_input):
    base, original = _interleaved_case()
    contract = base if has_input else api.TaskContract(base.outputs(), base.clocks)
    schedule = GlobalSchedule(original.clock_schedule.timings, [
        GlobalEventSlot(entry.time, event=api.EventSlot(
            ticks=entry.event.ticks, resets=entry.event.resets,
            controls=dict(entry.event.controls),
            offers=("in",) if has_input and entry.time in (2, 6) else ()))
        for entry in original.clock_schedule.slots])
    stimulus = api.Stimulus({"in": ((7,), (9,))} if has_input else {},
        [entry.event for entry in schedule.slots], clock_schedule=schedule)
    source = '''class M:
    def reset(self):
        self.n = 0
    def process(selfPARAMETER):
        self.n += 1
        return {"y": self.n}
'''.replace("PARAMETER", ", a" if has_input else "")
    path = tmp_path / "behavior.py"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_behavior_source(
        source, contract, file=path.name, model="M"))
    handle = api.prepare_behavior(task, api.BehaviorConfig(
        path, file=path.name, model="M"), bounded_analysis=False)
    trace = handle._artifact.trace(contract, stimulus)
    emitted = [(index, values) for index, row in enumerate(trace.records)
               for name, values in row.outputs if name == "out" and values]
    assert emitted == ([(2, ((1,),)), (6, ((1,),))] if has_input else
                       [(2, ((1,),)), (4, ((1,),)), (6, ((2,),)), (8, ((3,),))])


@pytest.mark.cosim
@pytest.mark.usefixtures("search_lifetime")
@pytest.mark.parametrize("cancel", (False, True))
@pytest.mark.parametrize("period", (2, 4))
def test_single_clock_native_latency_search_retains_timestamps(tmp_path, record_property, cancel, period):
    started = monotonic()
    contract, seed = declared_case(cancel=cancel, period=period)
    provider = _provider(tmp_path, contract, seed)
    goal = "latency:paired:wanted"
    catalogue = provider.catalogue_manifest().as_dict()
    selected, request = _second_question(provider, seed, goal)
    with provider.search_scope(probe_budget=1):
        answer = provider.probe(seed, request)
        if cancel or period != 2:
            assert answer.outcome.value == "local-unsat", answer.as_dict()
            assert goal not in selected.replay.hits
            record_property("local_negative", answer.as_dict())
        else:
            _confirm(provider, seed, selected, answer, goal, 7 ^ 85,
                     LatencyMonitor(contract, artifact_kind="rtl"), record_property, started)
    assert provider.catalogue_manifest().as_dict() == catalogue


@pytest.mark.cosim
def test_native_control_only_reset_matches_reference_without_an_extra_edge(tmp_path):
    from behave.sim.core.reference import expected_run_of
    from behave.sim.rtl.artifact import RTLArtifact

    source = '''class M:
    def reset(self):
        self.n = 0
    def tick(self):
        self.n += 1
    def process(self, a):
        result = {"y": a + self.n}
        self.n += 1
        return result
'''
    contract, seed = _interleaved_case()
    bundle = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode()}, config={"class": "M"})
    rtl = '''module M(input clk, rst, in_valid, out_ready,
        input [7:0] a, output in_ready, out_valid, output [7:0] y);
        reg [7:0] n = 0;
        assign in_ready = out_ready;
        assign out_valid = in_valid;
        assign y = a + n;
        always @(posedge clk or posedge rst)
            if (rst) n <= 0; else n <= n + 1;
    endmodule'''
    path = tmp_path / "counter.sv"
    path.write_text(rtl)
    task = api.admit(contract, api.realization_of_rtl_source(
        rtl, "test counter", top="M", contract=contract), reference=bundle)
    artifact = RTLArtifact(path, "M", tmp_path / "native")
    trace, = artifact.traces_per_process(
        contract, (seed,), state_names=("n",), state_widths={"n": 8})
    assert isinstance(trace, api.AuditedTrace), trace.as_dict()
    assert [dict(row.state)["n"] for row in trace.records] == [0, 0, 1, 0, 1, 1, 2, 2, 3]
    expected, _memory = expected_run_of(task, bundle, trace)
    actual = [dict(row.accepted)["out"] for row in trace.records
              if dict(row.accepted).get("out") is not None]
    assert expected == {"out": [(8,)]}
    assert actual == expected["out"]
