"""Exact-local RTL generation selects concretely and pins exact Probe boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.engines import probing
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.generation import stimulus_bytes
from behave.sim.stimulus_set import GenerationSpec

DESIGN = r"""
module ready_valid_wire (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic a,
    output logic out_valid,
    input logic out_ready,
    output logic y
);
    logic witness_state;
    initial witness_state = 1'b0;
    always @(posedge clk) begin
        if (rst) witness_state <= 1'b0;
        else witness_state <= witness_state;
    end
    assign in_ready = out_ready;
    assign out_valid = in_valid;
    assign y = a ^ witness_state;
endmodule
"""


def rtl_contract(*, answers_per_transaction=1):
    return api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("a", api.FiniteType(1), "a")],
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("y", api.FiniteType(1), "y")],
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION,
                answers_per_transaction=answers_per_transaction),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=3)])


GOAL = "tests.rtl.local-payload-is-one"
NEVER = "tests.rtl.local-never"


def _seed():
    return api.Stimulus(
        queues={"in": [(0,), (0,)]},
        slots=[
            *[api.EventSlot(
                controls={"rst": 1, "out_ready": 0}, reset=True)
              for _ in range(3)],
            # Transaction zero is first presented under back-pressure, then accepted.
            api.EventSlot(offers=("in",),
                          controls={"rst": 0, "out_ready": 0}),
            api.EventSlot(offers=("in",),
                          controls={"rst": 0, "out_ready": 1}),
            # Transaction one is first presented only after the pointer advances.
            api.EventSlot(offers=("in",),
                          controls={"rst": 0, "out_ready": 1}),
            api.EventSlot(controls={"rst": 0, "out_ready": 1}),
        ], source="rtl-local seed")


def _ready_state(pointer):
    return {
        "accepted_count": pointer,
        "pending": (False, False),
        "owed": (0, 0),
        "accepted_at": (0, 0),
        "offered": (False, False),
        "reported": (False, False),
        "stalled": False,
        "held": (0,),
    }


def _boundary(slot, pointer, released):
    return {
        "artifact.state": {"q": 0},
        "driver.pointer": pointer,
        "driver.released": released,
        "driver.slot": slot,
        "monitor.outstanding": 0,
        "monitor.ready-valid": _ready_state(pointer),
        "monitor.reset-restoration": None,
    }


@pytest.mark.cosim
def test_protocol_local_query_keeps_stalled_payload_before_its_interval(tmp_path, monkeypatch):
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.search_policy import SearchBudget, search_scope

    goal = 'monitor.sink-stability'
    source = DESIGN.replace('assign in_ready = out_ready;', 'assign in_ready = 1;')
    source = source.replace('assign out_valid = in_valid;', 'assign out_valid = !rst;')
    path = tmp_path / 'stalled.sv'
    path.write_text(source)
    artifact = RTLArtifact(path, 'ready_valid_wire', tmp_path / 'replay')
    contract = rtl_contract(answers_per_transaction=None)
    provider = RTLGenerationProvider.from_artifact(artifact, contract,
        GenerationSpec(7, 3, 3, 1), artifact.frontend_catalogue(),
        targetable_goals=(goal,), workdir=tmp_path / 'query')
    seed = api.Stimulus({'in': [(0,), (0,), (0,)]}, [
        *[api.EventSlot(reset=True, controls={'rst': 1, 'out_ready': 0}) for _ in range(3)],
        *[api.EventSlot(offers=('in',), controls={'rst': 0, 'out_ready': 0}) for _ in range(3)],
        api.EventSlot(controls={'rst': 0, 'out_ready': 1})])
    selection = provider.select(seed)
    assert isinstance(selection, api.ProbeSelection), selection
    assert goal not in selection.replay.hits
    request = next(r for r in selection.requests if r.mutable_fields[0].transaction == 1)
    segment = provider._selected[stimulus_bytes(seed)][request.occurrence][0]
    assert (segment.start, segment.stop) == (4, 5)
    lease = SearchBudget(api.SearchPolicy(time_budget_s=180)).direction(1)
    with search_scope(lease), provider.search_scope(probe_budget=1):
        answer = provider.probe(seed, request)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert dict(answer.candidate.queues)['in'] == ((0,), (1,), (0,))
    replay = provider.select(answer.candidate, targets=())
    assert isinstance(replay, api.ProbeSelection), replay
    assert goal in replay.replay.hits


@pytest.mark.cosim
@pytest.mark.parametrize('blocked', [False, True])
def test_source_protocol_query_keeps_the_real_terminal_input_obligation(tmp_path, monkeypatch, blocked):
    from behave.sim.core.monitor import INPUT_COMPLETION
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.search_policy import SearchBudget, search_scope

    source = DESIGN
    if blocked:
        source = source.replace('in_ready = out_ready', 'in_ready = out_ready & !a')
        source = source.replace('out_valid = in_valid', 'out_valid = in_valid & !a')
    path = tmp_path / 'completion.sv'
    path.write_text(source)
    artifact = RTLArtifact(path, 'ready_valid_wire', tmp_path / 'replay')
    contract = rtl_contract(answers_per_transaction=None)
    provider = RTLGenerationProvider.from_artifact(artifact, contract,
        GenerationSpec(6, 1, 3, 2), artifact.frontend_catalogue(),
        targetable_goals=(INPUT_COMPLETION,), workdir=tmp_path / 'query')
    seed = api.Stimulus({'in': [(0,)]}, [
        *[api.EventSlot(reset=True, controls={'rst': 1, 'out_ready': 1}) for _ in range(3)],
        api.EventSlot(offers=('in',), controls={'rst': 0, 'out_ready': 1}),
        *[api.EventSlot(controls={'rst': 0, 'out_ready': 1}) for _ in range(2)]])
    selection = provider.select(seed)
    assert isinstance(selection, api.ProbeSelection), selection
    request, = selection.requests
    assert INPUT_COMPLETION not in selection.replay.hits
    lease = SearchBudget(api.SearchPolicy(time_budget_s=180)).direction(1)
    with search_scope(lease), provider.search_scope(probe_budget=1):
        answer = provider.probe(seed, request)
    assert answer.outcome is (probing.Outcome.SAT if blocked else probing.Outcome.LOCAL_UNSAT), answer.detail
    if blocked:
        assert dict(answer.candidate.queues)['in'] == ((1,),)
        replay = provider.select(answer.candidate, targets=())
        assert isinstance(replay, api.ProbeSelection), replay
        assert INPUT_COMPLETION in replay.replay.hits
    else:
        assert answer.candidate is None


def test_public_analysis_rejects_foreign_domain_before_native_execution(monkeypatch):
    from behave.sim.core.domain import TraceDomain
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance

    proxy = object.__new__(RTLIsolatedAnalysisInstance)
    proxy._domain = TraceDomain(1, task_contract="contract", artifact="artifact")
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, "_source_payload",
                        lambda *_: pytest.fail("a foreign domain must not decode or replay"))
    outcome = proxy.audit_request([{}], "foreign")
    assert outcome.as_dict()["refusal"]["kind"] == "rtl-analysis-request-domain-mismatch"


@pytest.mark.parametrize("operation", [
    "query", "feasible", "audit-request", "record-audit", "replay-source",
])
def test_analysis_disagreement_survives_the_worker_envelope(monkeypatch, operation):
    from behave.sim.core.audit import WitnessInconsistent
    from behave.sim.rtl import analysis_job

    def disagree(*_args, **_kwargs):
        raise WitnessInconsistent(
            "source-disagrees", "two completed readings differ",
            components=["control-settled register state"])

    # Inject an observed contradiction at the shared worker operation boundary.
    monkeypatch.setattr(analysis_job, "_bound", disagree)
    reply = analysis_job.run_analysis_job({"operation": operation})
    assert reply["status"] == "inconsistent"
    with pytest.raises(WitnessInconsistent) as caught:
        analysis_job.decode_analysis_reply(reply, operation)
    assert caught.value.detail == {"components": ["control-settled register state"]}
    if operation in {"replay-source", "record-audit"}:
        from behave.sim.core.domain import TraceDomain
        from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance

        from .test_analysis_coordinator import _result, _Task

        result = _result()
        proxy = object.__new__(RTLIsolatedAnalysisInstance)
        proxy._session_active = False
        proxy._session_owner = None
        proxy._session = None
        proxy._domain = TraceDomain(
            1, task_contract=_Task.contract.digest, artifact=result.trace.artifact)
        proxy._context = SimpleNamespace(
            analysis_payload=dict, artifact=SimpleNamespace(timeout_s=10),
            generation_spec=SimpleNamespace(queue_bound=1),
            schedule=None, memory_environment=None,
            prepare_source=lambda: SimpleNamespace(state_names=()),
            source_trace=lambda _stimulus, **kwargs: result.trace)
        # This test injects a worker contradiction; native history projection
        # has its own integration tests and must not stop us before that call.
        monkeypatch.setattr("behave.sim.rtl.source_boundary_capture.source_history_view",
                            lambda trace, _names: trace)
        monkeypatch.setattr(RTLIsolatedAnalysisInstance, "_source_payload",
                            lambda _self, _rows: (None, SimpleNamespace(as_dict=dict)))
        monkeypatch.setattr("behave.sim.worker.run_job_strict", lambda *a, **kw: reply)
        if operation == "record-audit":
            from behave.sim.core.stimulus import AuditedTrace
            from behave.sim.core.run_record import RunResult
            from behave.sim.rtl.initial_state import RTLInitialState
            from behave.sim.rtl.startup_state import RTLStartupState

            image = result.trace.as_dict()
            image["artifact"] = "a" * 64
            proxy._domain = TraceDomain(1, task_contract=_Task.contract.digest, artifact=image["artifact"])
            image["initialization"] = RTLStartupState(
                RTLInitialState(image["artifact"], ()), ()).as_dict()
            result = RunResult.not_compared(result.stimulus, AuditedTrace.from_dict(image),
                result.comparison_failure or api.RunFailure.verifier(
                    "test-record", api.Refusal("test-not-compared", "raw audit fixture")))
        if operation == "replay-source":
            with pytest.raises(WitnessInconsistent):
                proxy.replay([{}])
        else:
            assert proxy.audit_record(result, _Task()).as_dict()["disposition"] \
                == "inconsistent"
    for added in ({"passed": True}, {"value": {}}, {"refusal": {}}):
        with pytest.raises(ValueError):
            analysis_job.decode_analysis_reply({**reply, **added}, operation)
    for invalid in ({}, {"kind": "x", "why": ""},
                    {"kind": "x", "why": "y", "detail": []}):
        with pytest.raises(TypeError):
            analysis_job.decode_analysis_reply({**reply, "inconsistency": invalid}, operation)

    # An unavailable worker still returns refusal, never manufactured disagreement.
    def unavailable(*_args, **_kwargs):
        raise api.Refused(api.Refusal("source-unavailable", "the replay did not run"))

    monkeypatch.setattr(analysis_job, "_bound", unavailable)
    refused = analysis_job.run_analysis_job({"operation": operation})
    assert type(analysis_job.decode_analysis_reply(refused, operation)) is api.Refusal


def test_isolated_analysis_protocol_is_named_and_strict():
    from behave.sim.job_isolation import _JOB_POLICIES
    from behave.sim.rtl.analysis import _decode_record_audit
    from behave.sim.rtl.analysis_job import (
        VERSION,
        decode_analysis_reply,
        decode_audit_check,
    )
    from behave.sim.worker import JOBS

    assert "rtl_exact_analysis" in JOBS
    assert _JOB_POLICIES["rtl_exact_analysis"].path_fields == ("relation_path",)
    assert _JOB_POLICIES["rtl_exact_analysis"].packages == ("z3",)

    envelope = {
        "version": VERSION,
        "operation": "step-audit",
        "status": "ok",
        "value": {"status": "agreed"},
    }
    value = decode_analysis_reply(envelope, "step-audit")
    assert decode_audit_check(value).status.value == "agreed"
    for field, payload in (
            ("solver", "escaped"),
            ("passed", False),
            ("evaluation", {"passed": False}),
            ("raw_records", [])):
        with pytest.raises((TypeError, ValueError)):
            decode_analysis_reply({**envelope, field: payload}, "step-audit")
    with pytest.raises((TypeError, ValueError)):
        decode_audit_check({"status": "agreed", "model": {}})

    # The ancillary worker has no protocol field through which it can rewrite the
    # concrete verdict, evaluation, or raw records.  Even a self-consistent refusal is
    # rejected when it smuggles one of those authority-owned fields.
    refused = {
        "disposition": "refused",
        "kind": "rtl-analysis-test-refusal",
        "why": "the test answer is unavailable",
    }
    for forbidden, payload in (
            ("passed", False),
            ("evaluation", {"passed": False}),
            ("raw_records", [])):
        with pytest.raises((TypeError, ValueError)):
            _decode_record_audit({**refused, forbidden: payload})


def test_isolated_analysis_verdict_uses_the_json_record_image():
    """A SAT trace keeps one meaning when tuple sequences cross JSON."""

    from behave.sim.core.instance import Verdict
    from behave.sim.core.query import QueryCall, SolverAnswer

    call = QueryCall(
        (GOAL,), SolverAnswer.SAT,
        witness=[{"x": 0}, {"x": 1}])
    verdict = Verdict(
        {GOAL: ({"x": 0}, {"x": 1})}, (), {}, queries=(call,))

    image = verdict.as_record()
    assert image["reachable"][GOAL] == [{"x": 0}, {"x": 1}]
    assert Verdict.from_record(image).as_record() == image


@pytest.mark.cosim
@pytest.mark.parametrize("answers", [1, None])
def test_public_monitor_probe_replays_a_new_hit_without_losing_source_goals(tmp_path, monkeypatch, answers):
    import json

    from behave.sim import run_assessment
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.search_policy import SearchBudget, search_scope

    checked = []
    original_check = run_assessment.check_run

    def check(stimulus, trace, **kwargs):
        checked.append(trace.fingerprint)
        return original_check(stimulus, trace, **kwargs)

    monkeypatch.setattr(run_assessment, "check_run", check)

    goal = "monitor.sink-stability"
    rtl = tmp_path / "stalled_data.sv"
    # A genuine bug: when ready rises, the held output changes. Its zero-input
    # baseline is legal; changing the input to one must expose the violation.
    rtl.write_text(DESIGN.replace("a ^ witness_state", "(a & out_ready) ^ witness_state"))
    artifact = RTLArtifact(rtl, "ready_valid_wire", tmp_path / "verilator")
    frozen = artifact.frontend_catalogue()
    spec = GenerationSpec(horizon=5, queue_bound=1, reset_hold_cycles=3, drain=0)
    provider = RTLGenerationProvider.from_artifact(
        artifact, rtl_contract(answers_per_transaction=answers), spec, frozen,
        workdir=tmp_path / "query", targetable_goals=(goal,))
    seed = api.Stimulus(queues={"in": [(0,)]}, slots=[
        *[api.EventSlot(controls={"rst": 1, "out_ready": 0}, reset=True) for _ in range(3)],
        api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 0}),
        api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
    ], source="local monitor baseline")
    selection = provider.select(seed)
    assert isinstance(selection, api.ProbeSelection), selection.as_dict()
    assert goal not in selection.replay.hits
    assert set(selection.replay.hits) & set(frozen)
    assert selection.requests and selection.requests[0].targets == (goal,)
    lease = SearchBudget(api.SearchPolicy(time_budget_s=180)).direction(1)
    with search_scope(lease), provider.search_scope(probe_budget=1):
        answer = provider.probe(seed, selection.requests[0])
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.scope is probing.Scope.RTL_LOCAL_HIT
    assert answer.goal == goal
    assert dict(answer.candidate.queues)["in"] == ((1,),)

    replay = provider.select(answer.candidate, targets=())
    assert isinstance(replay, api.ProbeSelection), replay.as_dict()
    assert replay.requests == ()
    assert goal in replay.replay.hits
    assert set(replay.replay.hits) & set(frozen)
    assert provider.catalogue_manifest().digest() == selection.catalogue_digest == replay.catalogue_digest
    assert artifact.frontend_catalogue() == frozen
    assert provider.select(answer.candidate, targets=()).replay is replay.replay
    assert len(checked) == len(set(checked)) == 2
    trace = provider._isolated._context.selection_trace(answer.candidate)
    (tmp_path / "local-query.json").write_text(json.dumps({
        "source_catalogue": frozen.as_dict(), "baseline": selection.as_dict(),
        "query": {"outcome": answer.outcome.value, "scope": answer.scope.value,
                  "goal": answer.goal, "solve_ms": answer.solve_ms},
        "candidate": answer.candidate.as_dict(), "replayed": replay.as_dict(),
        "native_trace": trace.as_dict(), "concrete_checks": len(checked),
    }, indent=2))
