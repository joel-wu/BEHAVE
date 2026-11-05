"""One real RTL artifact through the public score-to-analysis vertical."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.generation_cache import GenerationCacheTelemetry
from tests.support.device_boundary import evaluate_device
from tests.support.reference import load as load_test_reference

from .test_multi_endpoint_evaluation import _contract as endpoint_product_contract
from .test_multi_endpoint_evaluation import _reference as endpoint_product_reference
from .test_multi_endpoint_evaluation import _stimulus as endpoint_product_stimulus

pytestmark = pytest.mark.cosim


RTL = r"""
module public_exact_wire (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic a,
    output logic out_valid,
    input  logic out_ready,
    output logic y
);
    logic q;
    initial q = 1'b0;
    always_ff @(posedge clk) begin
        if (rst) q <= 1'b0;
        else begin
            case (a)
                1'b0: q <= q;
                1'b1: q <= q;
            endcase
        end
    end
    assign in_ready = out_ready;
    assign out_valid = in_valid;
    assign y = a ^ q;
endmodule
"""


RESET_RTL = r"""
module public_exact_reset_wire (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic a,
    output logic out_valid,
    input  logic out_ready,
    output logic y
);
    logic q;
    initial q = 1'b0;
    always_ff @(posedge clk) begin
        if (rst) q <= 1'b0;
        else q <= a;
    end
    assign in_ready = out_ready;
    assign out_valid = in_valid;
    assign y = a ^ q;
endmodule
"""


UNOWNED_RESET_RTL = r"""
module public_unowned_reset_wire (
    input  logic clk,
    input  logic rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic a,
    output logic out_valid,
    input  logic out_ready,
    output logic y
);
    (* keep = "true" *) logic unreset_state;
    initial unreset_state = 1'b0;
    always_ff @(posedge clk)
        unreset_state <= ~unreset_state;
    assign in_ready = out_ready;
    assign out_valid = in_valid;
    assign y = a;
endmodule
"""


ENDPOINT_PRODUCT_RTL = r"""
module public_exact_endpoint_product(
    input wire clk, input wire rst,
    input wire left_valid, output wire left_ready,
    input wire [7:0] left_data,
    input wire right_valid, output wire right_ready,
    input wire [7:0] right_data,
    output wire left_result_valid, input wire left_result_ready,
    output wire [7:0] left_result_data,
    output wire right_result_valid, input wire right_result_ready,
    output wire [7:0] right_result_data
);
    assign left_ready = left_result_ready && !rst;
    assign right_ready = right_result_ready && !rst;
    assign left_result_valid = left_valid && !rst;
    assign right_result_valid = right_valid && !rst;
    assign left_result_data = left_data + 8'd1;
    assign right_result_data = right_data + 8'd2;
endmodule
"""


START_DONE_RTL = r"""
module public_start_done(
    input wire clk, input wire rst,
    input wire start, input wire [7:0] in_a,
    output reg done, output reg [7:0] out_y
);
    reg busy;
    reg [7:0] saved;
    always @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            saved <= 8'd0;
            done <= 1'b0;
            out_y <= 8'd0;
        end else begin
            done <= 1'b0;
            if (busy) begin
                done <= 1'b1;
                out_y <= saved << 1;
                busy <= 1'b0;
            end
            if (start) begin
                saved <= in_a;
                busy <= 1'b1;
            end
        end
    end
endmodule
"""


START_DONE_SPURIOUS_RTL = r"""
module public_start_done_spurious(
    input wire clk, input wire rst,
    input wire start, input wire [7:0] in_a,
    output reg done, output reg [7:0] out_y
);
    reg busy;
    reg [7:0] saved;
    always @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            saved <= 8'd0;
            done <= 1'b0;
            out_y <= 8'd0;
        end else begin
            done <= 1'b1;
            if (busy) begin
                out_y <= saved << 1;
                busy <= 1'b0;
            end
            if (start) begin
                saved <= in_a;
                busy <= 1'b1;
            end
        end
    end
endmodule
"""


REFERENCE = r"""
class Identity:
    def reset(self):
        pass

    def process(self, a):
        return {"y": a}
"""


def _contract(*, reset_restores=None):
    bit = api.FiniteType(1)
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN, [api.Field("a", bit, "a")],
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT, [api.Field("y", bit, "y")],
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION,
                answers_per_transaction=1),
        ],
        clocks=[api.ClockDomain(
            "clk", "clk", "rst", reset_hold_cycles=3,
            reset_restores=reset_restores)],
    )


class _BaselineOnly:
    name = "tests/public-rtl-analysis-baseline"
    policy = "tests/public-rtl-analysis-baseline/1"
    targetable_goals = ()

    @staticmethod
    def catalogue_manifest():
        return api.CatalogueManifest(())

    @staticmethod
    def cache_telemetry():
        return GenerationCacheTelemetry()

    @staticmethod
    def select(_seed):
        manifest = _BaselineOnly.catalogue_manifest()
        return api.ProbeSelection((), manifest.digest())

    @staticmethod
    def probe(_seed, _request):
        raise AssertionError("an empty selection has no probe")


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_real_rtl_public_command_persists_score_before_exact_analysis(tmp_path, asynchronous, monkeypatch):
    """A declared finite schedule through real replay and exact analysis.

    Runtime requests have no preassigned schedule; their later record-audit
    handoff is a separate capability, not covered by this fixed-domain test.
    """

    rtl_path = tmp_path / "public_exact_wire.sv"
    source = (RTL.replace("@(posedge clk)", "@(posedge clk or posedge rst)")
              if asynchronous else RTL)
    rtl_path.write_text(source)
    contract = _contract()
    # The capability under test is the production RTL frontend/exact service.  The
    # reference is trusted fixture code so its own worker does not duplicate that
    # isolation test or make this test depend on a second child protocol.
    reference = load_test_reference(
        {"behavior.py": REFERENCE.encode()}, config={"class": "Identity"},
        loader="public RTL exact fixture")
    task = api.admit(
        contract,
        api.realization_of_rtl_source(
            source, "public exact fixture", top="public_exact_wire",
            contract=contract),
        reference=reference)
    stimulus = api.reset_then(
        contract, [(0,), (1,)], controls={"out_ready": 1}, drain=1,
        source="public exact fixture")
    generation = api.GenerationSpec(
        horizon=len(stimulus.slots), queue_bound=2,
        reset_hold_cycles=3, drain=1)
    handle = api.prepare_rtl(
        task,
        api.RTLConfig(
            rtl_path, "public_exact_wire", tmp_path / "rtl-work",
            timeout_s=300,
            run_policy={"horizon": len(stimulus.slots)}),
        generation)
    assert handle.frontend_catalogue
    assert {goal.family for goal in handle.frontend_catalogue.values()} == {
        "control-choice", "location"}
    from behave.sim.rtl.source_goals import DECLARATION_VERSION, POLICY
    assert all(goal.policy == f"{POLICY}/{DECLARATION_VERSION}"
               for goal in handle.frontend_catalogue.values())
    request = handle.command_request(
        run_directory=tmp_path / "invocation",
        baseline=api.build_baseline(
            contract, generation, policy=api.BaselinePolicy(case_limit=8, max_queue_depth=2)),
        providers=(_BaselineOnly(),),
        probe_budget=0, run_analysis=True, reference=reference,
        analysis_wall_timeout_seconds=180)

    scope = request.as_dict()["analysis"]["request"]
    assert scope["analysis_bound_unit"] == "schedule-slots"
    assert scope["analysis_bound"] == generation.horizon > generation.queue_bound
    observation = scope["observation"]
    assert observation["native_plan"] and observation["preparation"]
    assert observation["version"] == "rtl-observation-binding/2"
    assert observation["artifact"] == task.realization.identity
    assert observation["contract"] == contract.digest
    assert observation["catalogue"] == handle.frontend_catalogue.digest()

    from behave.sim.rtl import analysis_transport

    sessions = []
    original_session = analysis_transport.RTLAnalysisSession

    def tracked_session(*args, **kwargs):
        session = original_session(*args, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(analysis_transport, "RTLAnalysisSession", tracked_session)
    result = api.handle_command(request)

    assert len(sessions) == 1, result.as_dict()
    assert sessions[0]._child is None  # The public invocation owns the lifetime.
    assert [row["op"] for row in sessions[0].timings].count("open") == 1
    operations = [row["op"] for row in sessions[0].timings]
    assert {"record-audit", "query-start", "query-next"} <= set(operations)
    assert not {"audit-request", "replay-source", "step-audit"} & set(
        operations[:operations.index("query-start")]), "saved records must not be replayed again"

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.evaluation.as_dict()
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.rtl.native_events import NativeEventPlan

    for run in result.evaluation.results:
        image = thaw_record_value(run.trace.source_evidence)
        assert NativeEventPlan.from_evidence(image["plan"]).identity == observation["native_plan"]
        history = thaw_record_value(run.trace.query_boundaries)['history']
        assert history['refusal'] is None
        assert history['startup'] is not None
        assert len(history['states']) == len(history['sampled']) == len(run.trace.records)
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, \
        result.analysis.as_dict()
    assert result.analysis.concrete == result.evaluation.fingerprint
    assert result.analysis.evidence["feasibility"]["established_by"] == \
        "audited-pool"
    assert result.analysis.evidence["feasibility"]["solver_calls"] == 0
    audits = result.analysis.evidence["record_audits"]
    assert any(row['disposition'] == 'admitted' for row in audits)
    assert any(row['disposition'] == 'not-selected' for row in audits)
    assert operations.count('record-audit') == sum(
        row['disposition'] == 'admitted' for row in audits)
    assert result.analysis.evidence["queries"]
    assert all(
        row["telemetry"]["backend"] == "rtl-z3"
        for row in result.analysis.evidence["queries"])
    for row in result.analysis.evidence["queries"]:
        from behave.sim.core.query_telemetry import QueryTelemetry
        from behave.sim.rtl.analysis_job import VERSION as ANALYSIS_VERSION

        telemetry = row["telemetry"]
        # Source-history queries no longer use the old circuit slicer. Validate
        # the actual closed telemetry contract, not invented slicing statistics.
        assert QueryTelemetry.from_dict(telemetry).as_dict() == telemetry
        assert telemetry['goals_asked']
        assert telemetry['horizon'] == generation.horizon
        assert telemetry['provenance']['backend_version'] == ANALYSIS_VERSION
        assert telemetry["peak_rss_bytes"] > 0
        assert telemetry["provenance"]["tool"] == "z3"

    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted["authority"]["evaluation"] == \
        json.loads(json.dumps(result.evaluation.as_evidence_dict()))
    assert persisted["authority"]["passed"] is True
    assert persisted["report"]["analysis"] == \
        json.loads(json.dumps(result.analysis.as_dict()))
    assert "passed" not in persisted["report"]["analysis"]


def test_production_rtl_reset_monitor_observes_the_frontend_state(tmp_path):
    """The concrete reset goal reads the same frontend-owned state as exact RTL."""

    rtl_path = tmp_path / "public_exact_reset_wire.sv"
    rtl_path.write_text(RESET_RTL)
    contract = _contract(reset_restores=True)
    reference = load_test_reference(
        {"behavior.py": REFERENCE.encode()}, config={"class": "Identity"},
        loader="public RTL reset fixture")
    task = api.admit(
        contract,
        api.realization_of_rtl_source(
            RESET_RTL, "public exact reset fixture",
            top="public_exact_reset_wire",
            contract=contract),
        reference=reference)

    held = {"rst": 1, "out_ready": 0}
    released = {"rst": 0, "out_ready": 1}
    slots = [
        *[api.EventSlot(reset=True, controls=held) for _ in range(3)],
        api.EventSlot(offers=("in",), controls=released),
        *[api.EventSlot(reset=True, controls=held) for _ in range(3)],
        api.EventSlot(controls=released),
    ]
    stimulus = api.Stimulus(
        {"in": ((1,),)}, slots, source="production RTL reset restoration")
    generation = api.GenerationSpec(
        horizon=len(slots), queue_bound=1,
        reset_hold_cycles=3, drain=1)
    handle = api.prepare_rtl(
        task,
        api.RTLConfig(
            rtl_path, "public_exact_reset_wire", tmp_path / "rtl-reset-work",
            timeout_s=300,
            run_policy={"horizon": len(slots)}),
        generation)

    evaluation = evaluate_device(
        task, (stimulus,), handle._artifact, reference=reference,
        catalogue=api.evaluation_manifest(task, handle.frontend_catalogue))

    scenario = "monitor.reset-restoration.clk.scenario"
    violation = "monitor.reset-restoration.clk.violation"
    assert evaluation.results[0].ran, evaluation.results[0].as_dict()
    assert evaluation.accepted == 1
    assert evaluation.passed, evaluation.as_dict()
    assert scenario in evaluation.hits
    assert violation not in evaluation.hits
    telemetry = dict(evaluation.telemetry)
    assert dict(telemetry["reset_state_capability"])["status"] == "available"
    assert not [
        item for _, item in telemetry["monitor_unresolved"]
        if dict(item)["goal"] == violation
    ]
    trace = evaluation.results[0].trace
    assert trace.initial_state
    assert all(record.state for record in trace.records)


@pytest.mark.parametrize("extra_idle", (False, True))
def test_clock_owned_state_without_reset_is_observed(tmp_path, extra_idle):
    """Clock ownership does not require a reset assignment to the state cell.

    This toggling cell restores after one intervening edge, but not two. The
    monitor must report the actual state, not call its reset behavior unknown.
    """

    rtl_path = tmp_path / "public_unowned_reset_wire.sv"
    rtl_path.write_text(UNOWNED_RESET_RTL)
    contract = _contract(reset_restores=True)
    reference = load_test_reference(
        {"behavior.py": REFERENCE.encode()}, config={"class": "Identity"},
        loader="public RTL unresolved reset fixture")
    task = api.admit(
        contract,
        api.realization_of_rtl_source(
            UNOWNED_RESET_RTL, "public unresolved reset fixture",
            top="public_unowned_reset_wire", contract=contract),
        reference=reference)
    held = {"rst": 1, "out_ready": 0}
    released = {"rst": 0, "out_ready": 1}
    slots = [
        *[api.EventSlot(reset=True, controls=held) for _ in range(3)],
        api.EventSlot(offers=("in",), controls=released),
        *([api.EventSlot(controls=released)] if extra_idle else []),
        *[api.EventSlot(reset=True, controls=held) for _ in range(3)],
        api.EventSlot(controls=released),
    ]
    stimulus = api.Stimulus(
        {"in": ((1,),)}, slots, source="unresolved reset state")
    handle = api.prepare_rtl(
        task,
        api.RTLConfig(
            rtl_path, "public_unowned_reset_wire",
            tmp_path / "rtl-unowned-reset-work", timeout_s=300,
            run_policy={"horizon": len(slots)}),
        api.GenerationSpec(
            horizon=len(slots), queue_bound=1,
            reset_hold_cycles=3, drain=1))

    evaluation = evaluate_device(
        task, (stimulus,), handle._artifact, reference=reference,
        catalogue=api.evaluation_manifest(task, handle.frontend_catalogue))

    violation = "monitor.reset-restoration.clk.violation"
    telemetry = dict(evaluation.telemetry)
    capability = dict(telemetry["reset_state_capability"])
    assert evaluation.passed is (not extra_idle), evaluation.as_dict()
    assert capability["status"] == "available"
    assert (violation in evaluation.hits) is extra_idle
    assert not [item for _, item in telemetry["monitor_unresolved"]
                if dict(item)["goal"] == violation]


@pytest.mark.parametrize(('run_analysis', 'declared'), [
    (False, False), (True, False), (True, True),
], ids=['baseline', 'unbound', 'exact'])
def test_public_exact_classifies_a_same_clock_multi_endpoint_product(
        tmp_path, run_analysis, declared):
    """The public handle reaches the global-schedule product, not the 1x1 fallback."""

    rtl_path = tmp_path / "public_exact_endpoint_product.sv"
    rtl_path.write_text(ENDPOINT_PRODUCT_RTL)
    original = endpoint_product_contract()
    endpoints = []
    for endpoint in original.endpoints:
        row = endpoint.as_dict()
        if endpoint.direction is api.Direction.OUT:
            row['answers_per_transaction'] = 1
        endpoints.append(api.Endpoint.from_dict(row))
    contract = api.TaskContract(endpoints, original.clocks, protocol_instances=[
        api.ProtocolInstance(name, [name, f'{name}_result'])
        for name in ('left', 'right')])
    reference = endpoint_product_reference()
    task = api.admit(
        contract,
        api.realization_of_rtl_source(
            ENDPOINT_PRODUCT_RTL, "public endpoint-product exact fixture",
            top="public_exact_endpoint_product", contract=contract),
        reference=reference)
    stimulus = endpoint_product_stimulus()
    generation = api.GenerationSpec(
        horizon=len(stimulus.slots), queue_bound=2,
        reset_hold_cycles=1, drain=1)
    baseline = api.BaselinePolicy(case_limit=1, max_queue_depth=1)
    schedule = None
    if declared:
        from behave.sim.rtl.exact_product import global_schedule_for_stimuli

        baseline = api.build_baseline(contract, generation, policy=baseline)
        schedule = global_schedule_for_stimuli(contract, baseline.stimuli)
    handle = api.prepare_rtl(
        task,
        api.RTLConfig(
            rtl_path, "public_exact_endpoint_product",
            tmp_path / "rtl-product-work", timeout_s=300,
            run_policy={"horizon": len(stimulus.slots)}),
        generation, analysis_clock_schedule=schedule)
    request = handle.command_request(
        run_directory=tmp_path / "endpoint-product-invocation",
        baseline=baseline,
        providers=(_BaselineOnly(),),
        probe_budget=0, run_analysis=run_analysis, reference=reference,
        analysis_wall_timeout_seconds=180 if run_analysis else None,
        feasibility_timeout_ms=30_000 if run_analysis else None,
        query_timeout_ms=30_000 if run_analysis else None)

    # Declare the domain before execution; the record is evidence, not authority
    # to invent an analysis schedule. Missing declarations cannot erase the score.
    if run_analysis:
        bound_schedule = request.as_dict()["analysis"]["request"]["clock_schedule"]
        assert (bound_schedule is not None) is declared
        if declared:
            assert bound_schedule['digest'] == schedule.digest
    result = api.handle_command(request)

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed, result.decision.as_dict()
    # Fixed domain: one input per stream. Runtime also adds a continuous workload.
    assert result.evaluation.accepted == (2 if declared else 4)
    assert result.evaluation.required_complete
    if not run_analysis:
        assert result.analysis is None
        return
    if not declared:
        assert result.analysis.verdict is api.Verdict.REFUSED
        assert 'rtl-exact-action-schedule-unbound' in json.dumps(result.analysis.as_dict())
        return
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, \
        result.analysis.as_dict()
    audits = result.analysis.evidence["record_audits"]
    assert audits and audits[0]["disposition"] == "admitted"
    assert result.analysis.evidence["binding"]["representation"].startswith(
        "rtl-source:")


@pytest.mark.parametrize(("top", "source", "passes"), (
    ("public_start_done", START_DONE_RTL, True),
    ("public_start_done_spurious", START_DONE_SPURIOUS_RTL, False),
))
@pytest.mark.parametrize('declared', [False, True], ids=['unbound', 'exact'])
def test_public_exact_uses_the_registered_start_done_product(
        tmp_path, top, source, passes, declared):
    """A non-ready/valid adapter reaches score and the same finite exact table."""

    from .test_protocol_adapters import _contract as adapter_contract
    from .test_protocol_adapters import _reference as adapter_reference

    rtl_path = tmp_path / f"{top}.sv"
    rtl_path.write_text(source)
    contract = adapter_contract("start-done")
    reference = adapter_reference()
    task = api.admit(
        contract,
        api.realization_of_rtl_source(
            source, "public start/done exact fixture", top=top,
            contract=contract),
        reference=reference)
    stimulus = api.reset_then(
        contract, [(3,)], drain=3, source="public start/done exact fixture")
    generation = api.GenerationSpec(
        horizon=len(stimulus.slots), queue_bound=1,
        reset_hold_cycles=3, drain=3)
    baseline = api.BaselinePolicy(case_limit=1, max_queue_depth=1)
    schedule = None
    if declared:
        from behave.sim.rtl.exact_product import global_schedule_for_stimuli

        baseline = api.build_baseline(contract, generation, policy=baseline)
        schedule = global_schedule_for_stimuli(contract, baseline.stimuli)
    handle = api.prepare_rtl(
        task,
        api.RTLConfig(
            rtl_path, top, tmp_path / f"{top}-work", timeout_s=300,
            run_policy={"horizon": len(stimulus.slots)}),
        generation, analysis_clock_schedule=schedule)
    request = handle.command_request(
        run_directory=tmp_path / f"{top}-invocation",
        baseline=baseline,
        providers=(_BaselineOnly(),),
        probe_budget=0, run_analysis=True, reference=reference,
        analysis_wall_timeout_seconds=180)

    result = api.handle_command(request)

    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.passed is passes
    violation = 'monitor.start-done.done-without-outstanding'
    assert (violation in result.evaluation.hits) is (not passes)
    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted['authority']['passed'] is passes
    assert persisted['report']['analysis'] == json.loads(json.dumps(result.analysis.as_dict()))
    if not declared:
        assert result.analysis.verdict is api.Verdict.REFUSED
        assert 'rtl-exact-action-schedule-unbound' in json.dumps(result.analysis.as_dict())
        return
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, \
        result.analysis.as_dict()
    assert result.analysis.evidence["record_audits"][0]["disposition"] == \
        "admitted"
    assert result.analysis.evidence["binding"]["representation"].startswith(
        "rtl-source:")
    if not passes:
        goals = result.analysis.as_dict()["per_goal"]
        assert goals["monitor.start-done.done-without-outstanding"]["status"] \
            == "reached"


@pytest.mark.parametrize(("latency", "declared", "writing"), (
    (1, True, False), (3, True, False), (1, False, False), (1, True, True)))
def test_public_exact_classifies_external_memory(tmp_path, latency, declared, writing):
    """RAM is a declared domain, not inferred from a successful baseline trace."""
    from behave.sim.core.audit import AuditedWitness
    from behave.sim.core.memory import MemoryEnvironment, MemoryInterface, MemoryPort, MemoryRun
    from behave.sim.core.outcome import AnalysisOutcome

    from .test_rtl_source_task_relation import _contract as memory_contract

    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output reg [7:0] y,input ordy,
      output ram_req_valid,output [1:0] ram_req_addr,
      input ram_req_ready,ram_resp_valid,input [7:0] ram_resp_rdata);
      assign ir=ram_req_ready;
      assign ram_req_valid=iv;
      assign ram_req_addr=a;
      always @(posedge clk) begin
        if(rst) begin ov<=0; y<=0; end
        else begin ov<=ram_resp_valid; if(ram_resp_valid) y<=ram_resp_rdata; end
      end
    endmodule'''
    reference_source = b'''class M:
 def reset(self): self.ram = [0] * 4
 def process(self, a): return {"y": self.ram[a]}
'''
    base = memory_contract()
    if writing:
        source = '''module M(input clk,rst,iv,input [1:0] a,input [7:0] value,output ir,
          output ov,output [7:0] y,input ordy,
          output ram_req_valid,output [1:0] ram_req_addr,
          output [7:0] ram_req_wdata,input ram_req_ready);
          assign ir=ordy && ram_req_ready;
          assign ov=iv && ram_req_ready;
          assign y=value;
          assign ram_req_valid=iv && ordy;
          assign ram_req_addr=a;
          assign ram_req_wdata=value;
        endmodule'''
        reference_source = b'''class M:
 def reset(self): self.ram = [0] * 4
 def process(self, a, value):
  self.ram[a] = value
  return {"y": value}
'''
        inp = base.inputs()[0]
        base = api.TaskContract([api.Endpoint(inp.name, inp.direction,
            [*inp.fields, api.Field("value", api.FiniteType(8), "value")],
            inp.adapter, inp.clock, kind=inp.kind), *base.outputs()], base.clocks,
            memories=(MemoryInterface("ram", 2, 8, "c", [MemoryPort("w")]),))
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": reference_source}, config={"class": "M"})
    # DUT reset cancels outstanding responses; it does not clear external RAM.
    endpoints = [api.Endpoint.from_dict({**endpoint.as_dict(),
        "reset_policy": api.ResetPolicy.CLEARS.value})
        if endpoint.direction is api.Direction.IN else endpoint for endpoint in base.endpoints]
    contract = api.TaskContract(endpoints, base.clocks, memories=base.memories,
        observation_goals=tuple(api.TransactionGoal(f"value-{value}", "out",
            api.GoalCondition("eq", api.GoalField("y"), value)) for value in (201, 202)))
    generation = api.GenerationSpec(horizon=8, queue_bound=1, reset_hold_cycles=1, drain=5)
    memory = MemoryEnvironment([MemoryRun("ram", initial={} if writing else {1: 201}, read_latency=latency,
        ready={None: [1] * generation.horizon})])
    path = tmp_path / "memory.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
        reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"), generation,
        analysis_memory_environment=memory if declared else None)
    baseline = api.build_baseline(contract, generation,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1))
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        baseline=baseline, providers=(_BaselineOnly(),), probe_budget=0, reference=reference,
        run_analysis=True, analysis_wall_timeout_seconds=600 if writing else 180))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.required_complete and result.evaluation.passed
    assert "observation:value-201" not in result.evaluation.hits
    image = result.analysis.as_dict()
    assert AnalysisOutcome.from_dict(image).as_dict() == image
    persisted = json.loads(Path(result.record_path).read_text())
    assert persisted["authority"]["passed"] is True
    assert persisted["authority"]["evaluation_fingerprint"] == image["concrete"]
    assert persisted["report"]["analysis"] == image
    if not declared:
        assert result.analysis.verdict is api.Verdict.REFUSED
        assert "rtl-exact-memory-environment-unbound" in json.dumps(image)
        assert not image["per_goal"]
        return
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, image
    assert set(image["per_goal"]) == set(result.evaluation.catalogue)
    hit = image["per_goal"]["observation:value-201"]
    assert hit["status"] == "reached"
    assert image["per_goal"]["observation:value-202"]["status"] == (
        "reached" if writing else "bounded-unreachable"), image
    row, = (row for row in image["evidence"]["witnesses"] if row["witness_ref"] == hit["support_ref"])
    assert row["origin"] == "bounded-query"
    witness = AuditedWitness.from_record(row["trace"])
    assert "observation:value-201" in witness.hits
    assert any(payload["y"] == 201 for batch in witness.outputs for endpoints in batch
               for name, payload in endpoints.items() if name == "out")
    if writing:
        memories = dict(witness.final_state["memory.environment"]["memories"])
        assert memories["ram"]["writes"]
        assert 201 in dict(memories["ram"]["cells"]).values()
    # Every native check, including RAM requests/responses and final memory, is
    # audited before a SAT result is allowed into the public goal partition.
    assert all(set(query["passed_audits"]) >= {"step_audit", "source_audit"}
               for query in image["evidence"]["queries"] if query["answer"] == "sat")


def test_public_exact_retains_stateless_reset_monitors_on_every_clock(tmp_path):
    """Known empty state is observable, including a clock with no endpoint."""
    from behave.sim.core.outcome import AnalysisOutcome

    base = _contract(reset_restores=True)
    contract = api.TaskContract(base.endpoints, (
        api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=1, reset_restores=True),
        api.ClockDomain("extra", "extra_clk", "extra_rst", reset_restores=True)))
    source = '''module M(input clk,rst,extra_clk,extra_rst,in_valid,a,out_ready,
      output in_ready,out_valid,y);
      assign in_ready=out_ready;
      assign out_valid=in_valid;
      assign y=a;
    endmodule'''
    path = tmp_path / "stateless.sv"
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b'''class Identity:
 def reset(self): pass
 def process_clk(self, a): return {"y": a}
 def process_extra(self): return None
'''}, config={"class": "Identity"})
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
        reference=reference)
    generation = api.GenerationSpec(horizon=6, queue_bound=1, reset_hold_cycles=1, drain=2)
    schedule = api.GlobalSchedule([
        api.ClockTiming(clock.name, 2, clock=clock.clock, reset=clock.reset)
        for clock in contract.clocks], [api.GlobalEventSlot(2 * index, event=api.EventSlot(
            ticks=("clk", "extra"), resets=("clk", "extra") if index in (0, 2) else (),
            offers=("in",) if index == 1 else (),
            controls={"rst": int(index in (0, 2)), "extra_rst": int(index in (0, 2)),
                      "out_ready": 1})) for index in range(generation.horizon)])
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"), generation,
        analysis_clock_schedule=schedule)
    baseline = api.build_baseline(contract, generation, realization=task.realization,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1))
    result = api.handle_command(handle.command_request(run_directory=tmp_path / "command",
        baseline=baseline, providers=(_BaselineOnly(),), probe_budget=0, reference=reference,
        run_analysis=True, analysis_wall_timeout_seconds=180))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.required_complete and result.evaluation.passed
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    image = result.analysis.as_dict()
    assert AnalysisOutcome.from_dict(image).as_dict() == image
    assert set(image["per_goal"]) == set(result.evaluation.catalogue)
    for domain in ("clk", "extra"):
        goals = image["per_goal"]
        assert goals[f"monitor.reset-restoration.{domain}.scenario"]["status"] == "reached"
        assert goals[f"monitor.reset-restoration.{domain}.violation"]["status"] == "bounded-unreachable"
    record = json.loads(Path(result.record_path).read_text())
    assert record["authority"]["passed"] is True
    assert record["report"]["analysis"] == image
