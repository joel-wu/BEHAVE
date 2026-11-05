"""Projection tests use real in-memory Sim records; EDA is a separate lane."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.core.artifacts import BlobRef, FileRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.improve.diagnostics import scoring_diagnostics
from behave.sim import api
from behave.sim.core.run_record import RunResult
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.judgment import ComponentJudgment, SubmissionJudgment
from behave.task_execution.simulator_judge import _failure, _with_diagnostics
from tests.sim.test_api_path import Doubling, _evaluate, _ref, contract, realization, schedule


def evaluation(*, gain=3):
    task = api.admit(contract(), realization(), reference=_ref())
    return _evaluate(task, [schedule([3, 5]), schedule([7])], Doubling(gain=gain), reference=_ref())


def test_score_diagnostics_preserve_mismatch_and_full_stimulus_without_private_state():
    value = evaluation()
    before = value.fingerprint, value.passed
    images = api.evaluation_diagnostics(value)
    summary = images["summary.json"]
    assert summary["affected_runs"] == 2 and summary["runs_omitted"] == 0
    assert images["runs/0/stimulus.json"] == value.results[0].stimulus.as_dict()
    events = images["runs/0/observations.json"]
    assert len(events["events"]) == len(value.results[0].trace.records)
    assert events["comparison"]["faults"][0]["expected"] == [6]
    assert events["comparison"]["faults"][0]["observed"] == [9]
    assert {"state", "source_evidence", "reference_source", "query_boundaries"}.isdisjoint(events)
    assert all("state" not in event for event in events["events"])
    assert (value.fingerprint, value.passed) == before


def test_score_diagnostics_runtime_limits_are_explicit_and_stimuli_are_never_cut():
    value = evaluation()
    images = api.evaluation_diagnostics(value, options=api.DiagnosticOptions(1, 1, 1))
    assert images["summary.json"]["runs_omitted"] == 1
    result = images["runs/0/observations.json"]
    assert result["events_omitted_before"] == result["event_count"] - 1
    assert result["events"][0]["slot"] == result["event_count"] - 1
    assert result["comparison"]["faults_omitted"] == result["comparison"]["fault_count"] - 1
    assert images["runs/0/stimulus.json"] == value.results[0].stimulus.as_dict()
    empty = api.evaluation_diagnostics(value, options=api.DiagnosticOptions(0, 0, 0))
    assert set(empty) == {"summary.json"} and empty["summary.json"]["runs_omitted"] == 2


def test_score_diagnostics_passing_run_has_summary_not_failure_cases():
    images = api.evaluation_diagnostics(evaluation(gain=2))
    assert set(images) == {"summary.json"} and images["summary.json"]["affected_runs"] == 0


def test_score_diagnostics_missing_extra_memory_and_safety_are_not_value_only():
    # A projection fixture, not a claim that these invented faults were scored.
    original = evaluation()
    trace = original.results[0].trace
    events = [SimpleNamespace(accepted=record.accepted, outputs=record.outputs,
              environment=record.environment, monitor=record.monitor,
              hits=(("safety.fixture", True),)) for record in trace.records]
    comparison = api.Comparison(faults=(("missing", "a:2", (4,), None), ("extra", "b:0", None, (5,))),
                                memory_faults=(("ram", 7, 8, 9),))
    run = SimpleNamespace(comparison=comparison, refused=None, stimulus=original.results[0].stimulus,
        trace=SimpleNamespace(fingerprint="candidate-trace", completion=trace.completion, records=events,
                              source_evidence="PRIVATE_REFERENCE_SOURCE", initial_state="PRIVATE_STATE"))
    value = SimpleNamespace(results=(run,), safety_hits=("safety.fixture",), fingerprint="fixture",
        task=original.task, accepted=original.accepted, complete=True, mismatch_count=2, memory_mismatch_count=1,
        catalogue={"safety.fixture": SimpleNamespace(family="protocol", event="stable")}, telemetry=())
    images = api.evaluation_diagnostics(value)
    projected = images["runs/0/observations.json"]
    assert projected["safety_hits"][0]["slot"] == 0
    assert projected["comparison"]["faults"][0] == {
        "kind": "missing", "position": "a:2", "expected": [4], "observed": None}
    assert projected["comparison"]["memory_faults"] == [
        {"memory": "ram", "address": 7, "expected": 8, "observed": 9}]
    assert projected["comparison"]["memory_comparison"] == "final-state"
    assert b"PRIVATE_" not in canonical_json(images)


@pytest.mark.parametrize("origin", list(api.FailureOrigin))
def test_score_diagnostics_failure_origin_does_not_leak_reference_logs(tmp_path, origin):
    options = {"policy": "fixture-limit"} if origin is api.FailureOrigin.RESOURCE_POLICY else {}
    failure = api.RunFailure(origin, "compile", api.Refusal("fixture", "SOURCE_SNIPPET", stderr="SOURCE_LOG"), **options)
    store = DirectoryStore(tmp_path / "objects")
    judgment = _failure(api, failure, objects=store)
    payload = store.read(judgment.diagnostics[0].content)
    assert (b"SOURCE_SNIPPET" in payload) == (origin is api.FailureOrigin.CANDIDATE)
    assert (b"SOURCE_LOG" in payload) == (origin is api.FailureOrigin.CANDIDATE)
    assert parse_json(payload)["phase"] == "compile"
    assert judgment.score == (0 if origin is api.FailureOrigin.CANDIDATE else None)


def test_score_diagnostics_failed_run_is_retained_without_inventing_a_comparison():
    value = evaluation()
    failure = api.RunFailure.reference("reference-output", api.Refusal("bad-output", "PRIVATE_SOURCE"))
    run = RunResult.failed_to_run(value.results[0].stimulus, failure)
    result = api.ConcreteEvaluation(value.task, (run,), catalogue=value.catalogue)
    images = api.evaluation_diagnostics(result)
    assert images["runs/0/observations.json"]["comparison"] is None
    assert images["runs/0/observations.json"]["failure"]["origin"] == "reference"
    assert b"PRIVATE_SOURCE" not in canonical_json(images)


def test_score_diagnostics_named_files_survive_records_and_feedback_without_evidence_walk(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    public = FileRef("failure.json", store.put(b'{"expected":6,"observed":9}'))
    private = store.put(b"PRIVATE_REFERENCE_IMPLEMENTATION")
    old = ComponentJudgment(0, "mismatch", evidence=(private,), detail={"private": "PRIVATE_TRACE"})
    assert ComponentJudgment.from_dict(old.as_dict()) == old and "diagnostics" not in old.as_dict()
    part = replace(old, diagnostics=(public,))
    assert ComponentJudgment.from_dict(part.as_dict()) == part and public.content in part.closure
    judgment = SubmissionJudgment(BlobRef.of(b"submission"), None, part, BlobRef.of(b"head"))
    files = scoring_diagnostics(store, judgment)
    assert {file.reference.path for file in files} == {"rtl/failure.json", "rtl/report.md"}
    assert b"PRIVATE" not in b"".join(file.data for file in files)
    assert b"No detailed diagnostics were recorded." in scoring_diagnostics(store, replace(judgment, rtl=old))[0].data
    partial = replace(part, detail={"diagnostics_status": "export_failed"})
    projected = scoring_diagnostics(store, replace(judgment, rtl=partial))
    assert b"Diagnostic export: export_failed." in projected[-1].data


def test_score_diagnostics_export_failure_never_erases_decided_score(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    part = ComponentJudgment(1, "simulator-concrete-score")
    def unavailable():
        raise OSError("export failed")
    result = _with_diagnostics(part, store, unavailable)
    assert result.score == 1 and result.code == part.code
    assert result.detail["diagnostics_status"] == "export_failed"


def test_score_diagnostics_policy_is_explicit_and_reads_existing_policy_bytes():
    policy = JudgePolicy("command", "authority", seed="seed", baseline_cases=2, sequence_length=3, search_seconds=0)
    assert "diagnostics" not in parse_json(policy.to_bytes())
    assert JudgePolicy.from_bytes(policy.to_bytes()) == policy
    changed = replace(policy, diagnostics=api.DiagnosticOptions(2, 10, 3))
    assert JudgePolicy.from_bytes(changed.to_bytes()) == changed
    with pytest.raises(ValueError):
        api.DiagnosticOptions(max_runs=-1)
