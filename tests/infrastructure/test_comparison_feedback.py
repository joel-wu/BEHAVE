"""Shared public feedback: no private evidence reads or new executions."""

from dataclasses import replace

import pytest

from behave.core.artifacts import BlobRef, FileRef
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.improve.diagnostics import scoring_diagnostics
from behave.sim import api
from behave.task_execution.feedback import comparison_feedback
from behave.task_execution.judgment import ComponentJudgment, SubmissionJudgment
from behave.task_execution.score_progress import decision_matches
from behave.task_execution.simulator_judge import _failure
from tests.infrastructure.test_score_diagnostics import evaluation


def test_real_public_diagnostics_are_shared_without_private_evidence(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    images = api.evaluation_diagnostics(evaluation())
    refs = tuple(FileRef(path, store.put(canonical_json(value))) for path, value in images.items())
    baseline = {"status": "completed", "generated": 2, "completed": 2, "search_generated": 0}
    result = ComponentJudgment(0, "simulator-concrete-score", diagnostics=refs,
        evidence=(BlobRef.of(b"NOT-READABLE-PRIVATE-EVIDENCE"),), elapsed_seconds=4.25,
        detail={"evaluation": "private-id", "internal": "PRIVATE", "baseline": baseline})
    before = result.as_dict()
    reply, files = comparison_feedback(result, store, prefix="review/feedback")
    assert reply["matched"] is False and reply["status"] == "compared"
    assert set(reply) == {"status", "matched", "summary", "report"}
    assert "Baseline: 2/2 completed." in reply["summary"]
    assert "Elapsed: 4.25 s." in reply["summary"]
    assert "Affected tests: 2." in reply["summary"]
    assert "Test 0:" in reply["summary"]
    assert "expected [6], observed [9]" in reply["summary"]
    assert reply["report"] == "review/feedback/report.md"
    assert b"PRIVATE" not in canonical_json(reply) and "evaluation" not in reply
    report = files[-1].data.decode()
    assert "## Test 0" in report and "## Test 1" in report
    assert "expected [14], observed [21]" in report
    assert "[Inputs and schedule](runs/0/stimulus.json)" in report
    assert files[-1].reference.path == reply["report"]
    assert len(files) == len(refs) + 1
    for source, copied in zip(refs, files):
        assert source.content == copied.reference.content
        assert copied.data == store.read(source.content)
    assert result.as_dict() == before

    # Self-improvement uses exactly the same view as a caller mounting to rtl/.
    _, expected = comparison_feedback(result, store, prefix="rtl")
    judgment = SubmissionJudgment(BlobRef.of(b"submission"), None, result, BlobRef.of(b"head"))
    assert scoring_diagnostics(store, judgment) == expected


@pytest.mark.parametrize("score,origin", [(1, None), (0, "candidate"), (None, "infrastructure")])
def test_old_missing_diagnostics_do_not_invent_result_or_timing(score, origin):
    class NoReads:
        def read(self, ref):
            raise AssertionError("feedback must not walk private evidence")

    result = ComponentJudgment(score, "fixture", origin=origin, retryable=score is None,
                              evidence=(BlobRef.of(b"PRIVATE"),), detail={"error": "PRIVATE"})
    reply, files = comparison_feedback(result, NoReads())
    assert reply["matched"] is (None if score is None else score == 1)
    assert reply["status"] == ("no_result" if score is None else "compared")
    report = files[-1].data.decode()
    assert ("Retryable: yes" in report) == result.retryable
    assert "Elapsed:" not in report and "Baseline:" not in report
    assert "No detailed diagnostics were recorded." in report
    assert len(files) == 1 and files[0].reference.path == "report.md"
    assert "PRIVATE" not in repr(reply)


@pytest.mark.parametrize("origin", [api.FailureOrigin.CANDIDATE, api.FailureOrigin.REFERENCE])
def test_failure_preview_uses_public_reason_and_keeps_logs_in_files(tmp_path, origin):
    store = DirectoryStore(tmp_path / "objects")
    failure = api.RunFailure(origin, "compile", api.Refusal("bad-syntax", "bad declaration", stderr="FULL-LOG"))
    result = _failure(api, failure, objects=store)
    reply, files = comparison_feedback(result, store)
    assert f"{origin.value}/compile" in reply["summary"]
    assert ("bad declaration" in reply["summary"]) == (origin is api.FailureOrigin.CANDIDATE)
    assert "FULL-LOG" not in repr(reply)
    assert (b"FULL-LOG" in files[0].data) == (origin is api.FailureOrigin.CANDIDATE)
    assert reply["matched"] is (False if origin is api.FailureOrigin.CANDIDATE else None)


def test_memory_and_safety_preview_do_not_require_output_faults(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    images = {
        "summary.json": {"mismatch_count": 0, "memory_mismatch_count": 1, "runs": [
            {"index": 3, "stimulus": "runs/3/stimulus.json", "observations": "runs/3/observations.json"}]},
        "runs/3/stimulus.json": {"initial_memory": {"ram": [0]}},
        "runs/3/observations.json": {
            "comparison": {"faults": [], "memory_faults": [
                {"memory": "ram", "address": 1, "expected": 2, "observed": 3}]},
            "safety_hits": [{"slot": 5, "goals": ["protocol.hold"]}]},
    }
    result = ComponentJudgment(0, "mismatch", diagnostics=tuple(
        FileRef(path, store.put(canonical_json(value))) for path, value in images.items()))
    reply, files = comparison_feedback(result, store, prefix=".rtl/feedback")
    report = files[-1].data.decode()
    assert "Output mismatches:" not in report
    assert "ram[1]: expected 2, observed 3" in report
    assert "Safety failure at slot 5: protocol.hold" in report
    assert "Final-memory mismatches: 1" in reply["summary"]
    assert len(files) == 4


def test_state_alias_preview_uses_only_exported_candidate_paths(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    reason = api.Refusal("multiclock-state-aliased", "alias", path="state['snapshot'][0]",
                         first_path="state['mem']", value_type="list", traceback="PRIVATE")
    image = api.failure_diagnostic(api.RunFailure.verifier("candidate-replay", reason))
    result = ComponentJudgment(None, "judge-no-result", origin="verifier",
        diagnostics=(FileRef("failure.json", store.put(canonical_json(image))),))
    reply, files = comparison_feedback(result, store)
    for text in (reply["summary"], files[-1].data.decode()):
        assert "Shared state paths: state['mem'] and state['snapshot'][0]" in text
        assert "PRIVATE" not in text
    assert reply["matched"] is None


@pytest.mark.parametrize("visible", [False, True])
def test_per_run_reference_exception_is_actionable_only_when_reference_is_visible(tmp_path, visible):
    from behave.sim.core.run_record import RunResult

    original = evaluation()
    failure = api.RunFailure.reference("reference-completion", api.Refusal(
        "reference-completion-failed", "the reference could not determine the request's output count",
        error="TypeError", detail="Model.process() got an unexpected keyword argument 'in_x0'"))
    run = RunResult.failed_to_run(original.results[0].stimulus, failure)
    value = api.ConcreteEvaluation(original.task, (run,), catalogue=original.catalogue)
    before = value.fingerprint
    images = api.evaluation_diagnostics(value, reference_visible=visible)
    store = DirectoryStore(tmp_path / "objects")
    result = ComponentJudgment(None, failure.kind, origin="reference", diagnostics=tuple(
        FileRef(path, store.put(canonical_json(image))) for path, image in images.items()))
    reply, files = comparison_feedback(result, store)
    assert reply["matched"] is None
    for text in (reply["summary"], files[-1].data.decode()):
        assert ("TypeError" in text) == visible
        assert ("unexpected keyword argument 'in_x0'" in text) == visible
    assert value.fingerprint == before
    hidden, hidden_files = comparison_feedback(result, store, diagnostics_visible=False)
    assert hidden["summary"] == "UNAVAILABLE."
    assert len(hidden_files) == 1


@pytest.mark.parametrize("origin", [api.FailureOrigin.VERIFIER, api.FailureOrigin.INFRASTRUCTURE])
def test_visible_reference_does_not_expose_other_private_failure_details(origin):
    failure = api.RunFailure(origin, "worker", api.Refusal("fixture", "PRIVATE-WORKER-STATE"))
    assert "reason" not in api.failure_diagnostic(failure, reference_visible=True)


@pytest.mark.parametrize("visible", [False, True])
def test_explicit_replay_passes_reference_visibility_to_public_diagnostics(tmp_path, visible):
    from types import SimpleNamespace

    from behave.sim.core.run_record import RunResult
    from behave.task_execution.simulator_judge import _explicit_replay

    original = evaluation()
    failure = api.RunFailure.reference("reference-completion", api.Refusal(
        "reference-completion-failed", "output count unavailable", error="TypeError",
        detail="Model.process() got an unexpected keyword argument 'in_x0'"))
    run = RunResult.failed_to_run(original.results[0].stimulus, failure)
    value = api.ConcreteEvaluation(original.task, (run,), catalogue=original.catalogue)
    authority = SimpleNamespace(evaluation=value, authoritative=False, as_dict=lambda: {"fixture": True})
    def replay(*args, **kwargs):
        assert kwargs["stop_on_failure"] is True
        return authority

    handle = SimpleNamespace(replay=replay)
    inputs = SimpleNamespace(reference_visible=visible, replay_stimuli=canonical_json({
        "stimuli": [run.stimulus.as_dict()]}))
    store = DirectoryStore(tmp_path / "objects")
    result = _explicit_replay(api, handle, None, original.task.contract, inputs,
        SimpleNamespace(diagnostics=None), store, (), SimpleNamespace(finish=lambda: None))
    reply, _ = comparison_feedback(result, store)
    assert result.score is None
    assert result.detail.get("diagnostics_status") != "export_failed"
    assert ("unexpected keyword argument 'in_x0'" in reply["summary"]) == visible


def test_missing_preview_file_is_not_resolved_outside_public_allowlist(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    image = {"runs": [{"index": 0, "stimulus": "private-input", "observations": "private-trace"}]}
    result = ComponentJudgment(0, "mismatch", diagnostics=(FileRef("summary.json", store.put(canonical_json(image))),))
    reply, files = comparison_feedback(result, store)
    assert "First failure" not in reply["summary"]
    assert "Details were not exported." in files[-1].data.decode()
    assert "private-input" not in files[-1].data.decode()


@pytest.mark.parametrize("score,origin", [(1, None), (0, None), (None, "reference")])
def test_hidden_reference_uses_same_report_without_diagnostic_reads(score, origin):
    class NoReads:
        def read(self, ref):
            raise AssertionError("hidden diagnostics must not be loaded")

    private = BlobRef.of(b"private diagnostic")
    result = ComponentJudgment(score, "PRIVATE-CODE", origin=origin,
        diagnostics=(FileRef("failure.json", private),), elapsed_seconds=1.5,
        detail={"baseline": {"private": True}})
    reply, files = comparison_feedback(result, NoReads(), diagnostics_visible=False)
    label = "UNAVAILABLE" if score is None else "PASS" if score else "FAIL"
    assert reply["summary"] == label + "."
    assert len(files) == 1 and files[0].data.decode() == f"# Comparison report\n\n{label}.\n"
    assert "PRIVATE" not in repr(reply)


def test_passing_report_does_not_invent_failure_details(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    images = api.evaluation_diagnostics(evaluation(gain=2))
    result = ComponentJudgment(1, "simulator-concrete-score", diagnostics=tuple(
        FileRef(path, store.put(canonical_json(image))) for path, image in images.items()))
    reply, files = comparison_feedback(result, store)
    assert reply["summary"] == "PASS. Tests: 2 (complete)."
    assert len(files) == 2  # Original summary plus the one report entry.
    assert "## Test" not in files[-1].data.decode()


def test_feedback_reports_extra_replay_without_promoting_completed_baseline_to_pass(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    result = ComponentJudgment(None, 'judge-concrete-not-authoritative', origin='verifier', detail={
        'baseline': {'status': 'completed', 'generated': 41, 'completed': 41},
        'replay_phases': [
            {'phase': 'isolated', 'requested_runs': 41, 'completed_runs': 41, 'input_transactions': 4100},
            {'phase': 'continuous', 'requested_runs': 1, 'completed_runs': 0, 'input_transactions': 4100}]})
    reply, files = comparison_feedback(result, store)
    assert reply['status'] == 'no_result' and reply['matched'] is None
    assert 'Isolated replay: 41/41 completed, 4100 planned input transactions.' in reply['summary']
    assert 'Continuous replay: 0/1 completed, 4100 planned input transactions.' in files[-1].data.decode()
    hidden, _ = comparison_feedback(result, store, diagnostics_visible=False)
    assert hidden['summary'] == 'UNAVAILABLE.'


def test_long_sequence_retains_all_differences_without_inlining_them(tmp_path):
    from tests.sim.test_api_path import Doubling, _evaluate, _ref, contract, realization, schedule

    store = DirectoryStore(tmp_path / "objects")
    task = api.admit(contract(), realization(), reference=_ref())
    value = _evaluate(task, [schedule(range(1, 101))], Doubling(gain=3), reference=_ref())
    images = api.evaluation_diagnostics(value)
    result = ComponentJudgment(0, "simulator-concrete-score", diagnostics=tuple(
        FileRef(path, store.put(canonical_json(image))) for path, image in images.items()))
    reply, files = comparison_feedback(result, store)
    original = parse_json(files[0].data)
    assert len(original["queues"][0][1]) == 100
    assert len(images["runs/0/observations.json"]["comparison"]["faults"]) == 100
    assert "Output mismatches: 100" in reply["summary"]
    assert "output 0:" in reply["summary"] and "output 99:" not in reply["summary"]
    assert "output 99:" not in files[-1].data.decode()
    assert {file.reference.path: file.data for file in files[:-1]} == {
        path: canonical_json(image) for path, image in images.items()}


def test_judgment_timing_roundtrip_and_score_checkpoint_extension():
    old = ComponentJudgment(1, "match")
    assert "elapsed_seconds" not in old.as_dict()
    assert ComponentJudgment.from_dict(old.as_dict()) == old
    timed = replace(old, elapsed_seconds=3.5)
    assert ComponentJudgment.from_dict(timed.as_dict()) == timed
    assert decision_matches(old, timed)
    assert not decision_matches(old, replace(timed, score=0))
    assert not decision_matches(old, replace(timed, detail={"changed": True}))


@pytest.mark.parametrize("score,origin", [(1, None), (None, "infrastructure")])
def test_worker_records_elapsed_time_without_parsing_receipts(monkeypatch, score, origin):
    from behave.task_execution import judge as judge_module

    times = iter((10., 13.5))
    monkeypatch.setattr(judge_module.time, "monotonic", lambda: next(times))
    original = ComponentJudgment(score, "fixture", origin=origin)
    def execute(request, objects, began):
        assert began == 10.
        return original
    monkeypatch.setattr(judge_module, "_run_worker", execute)
    result = judge_module.run_worker(None, None)
    assert result.elapsed_seconds == 3.5
    assert replace(result, elapsed_seconds=None) == original
