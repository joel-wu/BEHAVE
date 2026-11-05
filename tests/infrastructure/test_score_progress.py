"""Durable scores during analysis, without treating pending workers as settled."""

import time
from dataclasses import replace

import pytest
from test_infra_cli import command
from test_judge import run, setup
from test_judge_analysis import ReplayJudge, scored

from behave.agent import AttemptInterrupted
from behave.agent.journal import AttemptJournal, load_chain
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.task_execution.analysis import AnalysisOptions
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.score_progress import load_score_progress
from behave.task_execution.simulator_judge import SimulatorJudge


def fixture_task(tmp_path):
    return setup(tmp_path, rtl=None, policy=JudgePolicy("cmd", "authority", search_seconds=0, analysis=AnalysisOptions(10)))


def test_worker_checkpoint_is_durable_before_analysis_returns(tmp_path, monkeypatch):
    from behave.isolation import service
    from behave.task_execution.judge import ScoreCheckpoint

    task, submission, journal = fixture_task(tmp_path)
    exported = DirectoryStore(tmp_path / "exported")
    captured = []

    def worker(argv, **kwargs):
        local = DirectoryStore(kwargs["cwd"] / "objects")
        ScoreCheckpoint(BlobRef(argv[-2], int(argv[-1])), scored(local), time.monotonic_ns()).install(kwargs["cwd"])
        assert not kwargs["cancelled"]()
        events = journal.load()
        assert events[-1].kind == "judge_score_decided"
        assert not any(e.kind == "judge_settled" for e in events)
        head = BlobRef.of(events[-1].to_bytes())
        closure = tuple(dict.fromkeys(ref for e in events for ref in (*e.artifacts, BlobRef.of(e.to_bytes()))))
        exported.publish(journal.objects, closure)
        captured.append((head, load_score_progress(exported, head)))
        # Repeated supervisor polls cannot publish a second event.
        assert not kwargs["cancelled"]() and journal.load() == events
        return service.ServiceResult("timeout", -9, b"", b"", .1)

    monkeypatch.setattr(service, "run_service", worker)
    result = run(task, submission, journal, SimulatorJudge())
    assert result.behavior.score == 1 and result.behavior.analysis.status == "unavailable"
    head, progress = captured[0]
    assert progress["score"] == 1 and progress["execution_settled"] is False
    assert progress["submission"] == submission.as_dict()
    assert load_score_progress(exported, head) == progress
    assert run(task, submission, journal, ReplayJudge()) == result
    reference = tmp_path / "head.json"
    reference.write_bytes(canonical_json(head.as_dict()))
    output = command("inspect", "--kind", "score", "--objects", exported.directory, "--record", reference)
    assert output.returncode == 0, output.stderr
    assert parse_json(output.stdout) == progress


def test_partial_score_publication_preserves_committed_event_without_callback_retry(tmp_path, monkeypatch, capsys):
    from behave.isolation import service
    from behave.task_execution.judge import ScoreCheckpoint

    task, submission, journal = fixture_task(tmp_path)
    original_put = journal.objects.put
    failures = []

    def put(data):
        # The event is already in the journal, but exporting its content object
        # fails once. The outer judge still owns that committed decision.
        if b'"kind":"judge_score_decided"' in data and not failures:
            failures.append(data)
            raise OSError("score head export interrupted")
        return original_put(data)

    def worker(argv, **kwargs):
        local = DirectoryStore(kwargs["cwd"] / "objects")
        ScoreCheckpoint(BlobRef(argv[-2], int(argv[-1])), scored(local), time.monotonic_ns()).install(kwargs["cwd"])
        assert kwargs["cancelled"]()
        assert kwargs["cancelled"]()  # Must not invoke the partially committed callback again.
        return service.ServiceResult("cancelled", -9, b"", b"", .1)

    monkeypatch.setattr(journal.objects, "put", put)
    monkeypatch.setattr(service, "run_service", worker)
    result = run(task, submission, journal, SimulatorJudge())
    assert len(failures) == 1
    assert result.behavior.score == 1 and result.behavior.cleanup.quiescent
    assert result.behavior.analysis.code == "analysis-score-handoff-failed"
    assert not result.behavior.retryable
    assert sum(event.kind == "judge_score_decided" for event in journal.load()) == 1
    assert run(task, submission, journal, ReplayJudge()) == result
    assert capsys.readouterr().err.count("OSError: score head export interrupted") == 1


def test_coordinator_loss_after_score_never_restarts_an_unknown_worker(tmp_path):
    task, submission, journal = fixture_task(tmp_path)

    class CoordinatorLost(BaseException):
        pass

    class Judge:
        identity = SimulatorJudge.identity

        def evaluate(self, request, objects):
            request.on_decided(scored(objects))
            raise CoordinatorLost()

    with pytest.raises(CoordinatorLost):
        run(task, submission, journal, Judge())
    head = BlobRef.of(journal.load()[-1].to_bytes())
    before = journal.load()
    assert load_score_progress(journal.objects, head)["score"] == 1
    with pytest.raises(AttemptInterrupted, match="outcome-unknown"):
        run(task, submission, journal, ReplayJudge())
    assert journal.load() == before


@pytest.mark.parametrize("failure", ["changed", "exception", "duplicate", "unavailable"])
def test_post_score_backend_failure_preserves_decision_and_cleanup(tmp_path, failure):
    task, submission, journal = fixture_task(tmp_path)

    class Judge:
        identity = SimulatorJudge.identity

        def evaluate(self, request, objects):
            decided = scored(objects)
            request.on_decided(decided)
            if failure == "exception":
                raise RuntimeError("private diagnostic")
            if failure == "duplicate":
                request.on_decided(scored(objects, 0))
            if failure == "unavailable":
                return ComponentJudgment(None, "worker", "infrastructure", cleanup=CleanupState.UNKNOWN)
            return replace(decided, score=0)

    result = run(task, submission, journal, Judge())
    assert result.behavior.score == 1 and not result.behavior.retryable
    assert result.behavior.analysis.code == "analysis-score-handoff-failed"
    assert result.behavior.cleanup is (CleanupState.COMPLETE if failure == "changed" else CleanupState.UNKNOWN)
    assert "private diagnostic" not in str(result.as_dict())
    assert run(task, submission, journal, ReplayJudge()) == result
    assert sum(e.kind == "judge_requested" for e in journal.load()) == 1


@pytest.mark.parametrize("mutation", ["dispatch-inputs", "score-evidence", "phase", "component", "negative-dispatch"])
def test_rehashed_progress_corruption_is_rejected(tmp_path, mutation):
    task, submission, journal = fixture_task(tmp_path)

    class Judge:
        identity = SimulatorJudge.identity

        def evaluate(self, request, objects):
            result = scored(objects)
            request.on_decided(result)
            return result

    run(task, submission, journal, Judge())
    events = journal.load()
    decided = next(e for e in events if e.kind == "judge_score_decided")
    source = load_chain(journal.objects, BlobRef.of(decided.to_bytes()))
    forged = AttemptJournal(tmp_path / "forged", journal.objects)
    prefix = ()
    for event in source:
        payload, artifacts, kind = thaw_json(event.payload), event.artifacts, event.kind
        if kind == "judge_requested":
            if mutation == "dispatch-inputs":
                payload["inputs"] = BlobRef.of(b"wrong").as_dict()
            if mutation == "component":
                payload["component"] = "not-a-component"
            if mutation == "negative-dispatch":
                payload["dispatch"] = -1
            if mutation == "phase":
                kind = "judge_settled"
        if kind == "judge_score_decided" and mutation == "score-evidence":
            payload["result"]["evidence"] = [journal.objects.put(b"unlisted").as_dict()]
        changed = forged.append(prefix, kind, payload, artifacts)
        prefix += (changed,)
        head = journal.objects.put(changed.to_bytes())
    with pytest.raises(ValueError):
        load_score_progress(journal.objects, head)


def test_cold_settlement_cannot_change_the_published_score(tmp_path):
    task, submission, journal = fixture_task(tmp_path)

    class Judge:
        identity = SimulatorJudge.identity

        def evaluate(self, request, objects):
            result = scored(objects)
            request.on_decided(result)
            return result

    run(task, submission, journal, Judge())
    forged = AttemptJournal(tmp_path / "forged", journal.objects)
    prefix = ()
    for event in journal.load():
        payload = thaw_json(event.payload)
        if event.kind == "judge_settled":
            payload["result"]["score"] = 0
        changed = forged.append(prefix, event.kind, payload, event.artifacts)
        prefix += (changed,)
    with pytest.raises(ValueError, match="durable score"):
        run(task, submission, forged, ReplayJudge())
