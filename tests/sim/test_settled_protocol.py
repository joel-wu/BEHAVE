"""Clockless RTL uses the same input, replay, matching and score authority."""

import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.core.driver import Driver
from tests.sim.test_protocol_adapters import _evaluate, _reference
from tests.sim.test_sampled_protocol import _Doubling


def contract():
    return api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(8), "in_a")],
                     api.settled("in"), None, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(8), "out_y")],
                     api.settled("out"), None, kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1)], [],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))])


def test_clockless_contract_and_steps_have_no_synthetic_clock_or_reset():
    c = contract()
    assert api.TaskContract.from_dict(c.as_dict()).digest == c.digest
    assert c.clocks == () and c.environment_controls() == ()
    stimulus = api.reset_then(c, [(3,), (3,), (5,)], drain=0)
    assert len(stimulus.slots) == 3
    assert all(not s.controls and not s.ticks and not s.reset for s in stimulus.slots)
    driver = Driver(c, stimulus)
    assert driver.clock is None and driver.controls(0) == {}
    baseline = api.build_baseline(c, api.GenerationSpec(3, 3, 0, 0),
                                 policy=api.BaselinePolicy(case_limit=4, max_queue_depth=3))
    assert baseline.stimuli
    assert all(len(dict(s.queues)["in"]) == 3 for s in baseline.stimuli)


def test_clockless_inputs_include_repeated_equal_samples_in_the_score():
    c = contract()
    reference = _reference()
    realization = api.Realization("device", [
        api.Port("in_a", 8, driven_by="environment"),
        api.Port("out_y", 8, driven_by="artifact")], identity="sampled-device",
        channel_ports={"in": ["in_a"], "out": ["out_y"]})
    task = api.admit(c, realization, reference=reference)
    stimulus = api.reset_then(c, [(3,), (3,), (5,)], drain=0)
    result = _evaluate(task, stimulus, _Doubling(), reference)
    assert result.passed, result.as_dict()
    assert api.transfers(result.records[0], "out") == [(6,), (6,), (10,)]
    from behave.sim.core.ledger import ledger_of
    from behave.sim.core.monitor import Monitor

    monitor = Monitor(c)
    ledger = ledger_of(c, stimulus, result.records[0])
    shared, general = monitor._finite_ordered_check(ledger), monitor._general_check(ledger)
    assert shared is not None
    for field in ('violations', 'unresolved', 'unchecked', 'assumption_violations'):
        assert getattr(shared, field) == getattr(general, field)


def test_clockless_rejects_reset_idle_drain_and_invented_clock():
    c = contract()
    with pytest.raises(api.Refused, match="settled-drain"):
        api.reset_then(c, [(1,)], drain=1)
    with pytest.raises(api.Refused, match="settled-timing"):
        api.reset_then(c, [(1,)], reset_hold_cycles=1)
    with pytest.raises(api.Refused, match="stimulus-not-of-this-contract"):
        Driver(c, api.Stimulus({"in": [(1,)]}, [api.EventSlot(reset=True)]))
    image = c.as_dict()
    image["endpoints"][0]["adapter"]["name"] = api.SAMPLED_EDGE
    with pytest.raises(api.Refused, match="contract-no-clock"):
        api.TaskContract.from_dict(image)


@pytest.mark.cosim
@pytest.mark.parametrize("wrong", [False, True])
def test_public_clockless_rtl_score_and_source_analysis(tmp_path, wrong):
    from tests.sim.test_rtl_public_analysis_path import _BaselineOnly

    expression = "(in_a << 1) ^ 8'h01" if wrong else "in_a << 1"
    source = f"""module clockless(input wire [7:0] in_a, output wire [7:0] out_y);
        assign out_y = {expression};
        endmodule
    """
    path = tmp_path / "clockless.sv"
    path.write_text(source)
    c, reference = contract(), _reference()
    task = api.admit(c, api.realization_of_rtl_source(
        source, "clockless fixture", top="clockless", contract=c), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(
        path, "clockless", tmp_path / "rtl", timeout_s=120),
        api.GenerationSpec(3, 3, 0, 0))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "invocation",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=3),
        providers=(_BaselineOnly(),), probe_budget=0,
        run_analysis=not wrong, reference=reference,
        analysis_wall_timeout_seconds=180 if not wrong else None))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    # A conclusive mismatch stops the suite; it need not exhaust every case.
    assert wrong or result.evaluation.complete
    assert result.evaluation.passed is not wrong, result.evaluation.as_dict()
    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted["authority"]["passed"] is not wrong
    if not wrong:
        assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()


@pytest.mark.cosim
@pytest.mark.parametrize("wrong", [False, True])
@pytest.mark.parametrize("process", ["always_comb", "always @*"])
def test_public_procedural_comb_baseline_observes_real_goals(tmp_path, wrong, process):
    from tests.support.reference import load

    source = f"""module M(input [7:0] in_a, output reg [7:0] out_y);
        {process} begin
            if(in_a[0]) out_y=((in_a << 1) + 8'd7) ^ 8'd{int(wrong)};
            else out_y=(in_a << 1) + 8'd7;
        end
    endmodule"""
    path = tmp_path / "M.sv"
    path.write_text(source)
    c = contract()
    reference = load({"behavior.py": b"""class M:
    def reset(self): pass
    def process(self, in_a): return {"out_y": (in_a*2 + 7) & 255}
"""}, config={"class": "M"})
    task = api.admit(c, api.realization_of_rtl_source(source, top="M", contract=c),
                     reference=reference)
    # Concrete length is 100, independently of these small analysis bounds.
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"),
                             api.GenerationSpec(3, 3, 0, 0))
    command = handle.command_request(
        run_directory=tmp_path / "invocation", reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=100),
        search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False,
        providers=handle.generation_providers(reference, workdir=tmp_path / "guidance",
                                             enable_local_guidance=False))
    result = api.handle_command(command)
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert wrong or result.evaluation.complete
    assert result.decision is not None
    assert result.evaluation.passed is not wrong, result.as_dict()
    assert set(result.evaluation.hits) & set(handle.frontend_catalogue)
    for run in result.evaluation.results:
        assert run.trace.source_evidence
        assert run.trace.accepted_count(["in"]) >= 100
        assert all(not slot.ticks and not slot.controls for slot in run.trace.stimulus.slots)


@pytest.mark.cosim
@pytest.mark.parametrize("wrong", [False, True])
def test_clockless_source_search_reaches_rare_branch_and_public_score(tmp_path, wrong):
    """Baseline misses 37; source guidance must find it and real RTL must replay it."""
    from tests.support.reference import load

    reference = load({"behavior.py": b"""
class M:
    def reset(self):
        pass
    def process(self, in_a):
        if in_a == 37:
            out_y = in_a ^ 17
        else:
            out_y = (in_a * 2) & 255
        return {"out_y": out_y}
"""}, config={"class": "M"})
    value = "8'd0" if wrong else "(in_a ^ 8'd17)"
    source = f"""module clockless(input wire [7:0] in_a, output wire [7:0] out_y);
        assign out_y = in_a == 8'd37 ? {value} : (in_a << 1);
    endmodule"""
    rtl_path = tmp_path / "clockless.sv"
    rtl_path.write_text(source)
    c = contract()
    spec = api.GenerationSpec(3, 3, 0, 0)
    # Include both zero and nonzero samples, so baseline actually activates the
    # continuous assignment before testing residual source-goal selection.
    policy = api.BaselinePolicy(case_limit=3, max_queue_depth=3)
    baseline = api.build_baseline(c, spec, policy=policy)
    assert all(37 not in [row[0] for row in dict(seed.queues)["in"]]
               for seed in baseline.stimuli)
    task = api.admit(c, api.realization_of_rtl_source(
        source, "clockless search fixture", top="clockless", contract=c), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(
        rtl_path, "clockless", tmp_path / "rtl", timeout_s=120), spec)
    providers = handle.generation_providers(reference, workdir=tmp_path / "search")
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "invocation", baseline=policy, providers=providers,
        probe_budget=2, reference=reference))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert wrong or result.evaluation.complete
    assert result.evaluation.passed is not wrong, result.evaluation.as_dict()
    seeds = [run.trace.stimulus for run in result.evaluation.results]
    assert any(37 in [row[0] for row in dict(seed.queues)["in"]] for seed in seeds)
    assert all(not slot.ticks and not slot.controls and not slot.reset
               for seed in seeds for slot in seed.slots)
    # This trusted-reference fixture has no Behavior source catalogue. Inspect
    # the RTL direction, not the separately reported unavailable B direction.
    prepared = providers[-1].prepared_provider()
    assert prepared is not None
    domains = prepared.execution_cost()["domains"]
    assert len(domains) == 1
    assert [row["op"] for row in domains[0]["execution"]["operations"]].count("open") == 1
