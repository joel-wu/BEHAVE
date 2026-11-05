"""Score checkpoint, independent analyzer lifetime, and native handoff tests."""

import time
from dataclasses import replace

import pytest
from test_data_checks import comparison
from test_judge import real_task, run, setup

from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.task_execution.analysis import AnalysisOptions, AnalysisReport
from behave.task_execution.comparison import ComparisonRequest
from behave.task_execution.judge import PhaseWatch, ScoreCheckpoint
from behave.task_execution.judge_policy import JudgePolicy
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.score_progress import validate_decision
from behave.task_execution.simulator_judge import SimulatorJudge


class ReplayJudge:
    identity = SimulatorJudge.identity

    def evaluate(self, request, objects):
        pytest.fail("cold replay must not dispatch another score or analysis")


def scored(objects, score=1):
    return ComponentJudgment(score, "simulator-concrete-score", evidence=(objects.put(b"native evidence"),),
                             detail={"evaluation": BlobRef.of(b"evaluation").sha256})


@pytest.mark.parametrize("digest", [None, True, 0, "", "a" * 63, "a" * 65,
                                  "A" * 64, "g" * 64, "a" * 64 + "\n"])
def test_score_and_analysis_reject_the_same_malformed_evaluation_digest(digest):
    result = ComponentJudgment(1, "native", detail={"evaluation": digest})
    reference = BlobRef.of(b"request-or-policy")
    for construct in (
        lambda: validate_decision(result),
        lambda: ScoreCheckpoint(reference, result, 1),
        lambda: AnalysisReport(digest, reference, "analysis-timeout"),
    ):
        with pytest.raises(RecordError) as found:
            construct()
        assert found.value.code == "content-digest-invalid"


def test_policy_report_and_judgment_bind_one_evaluation():
    options = AnalysisOptions(wall_timeout_seconds=60)
    policy = JudgePolicy("cmd", "authority", search_seconds=0, analysis=options)
    assert JudgePolicy.from_bytes(policy.to_bytes()) == policy
    assert policy.to_bytes() != replace(policy, analysis=None).to_bytes()
    report = AnalysisReport(BlobRef.of(b"eval").sha256, BlobRef.of(b"policy"), "analysis-timeout")
    result = ComponentJudgment(1, "native", detail={"evaluation": report.concrete}, analysis=report)
    assert ComponentJudgment.from_dict(result.as_dict()) == result
    assert result.score == 1 and not result.retryable and report.status == "unavailable"
    with pytest.raises(ValueError):
        replace(result, detail={"evaluation": BlobRef.of(b"other").sha256})
    with pytest.raises(ValueError):
        replace(report, code="analysis-returned")
    for field in options.as_dict():
        with pytest.raises(ValueError):
            replace(options, **{field: True})


def test_checkpoint_is_exclusive_bound_and_phase_budget_not_reset(tmp_path, monkeypatch):
    import behave.task_execution.judge as checkpoint_module
    objects = DirectoryStore(tmp_path / "objects")
    request = BlobRef.of(b"request")
    policy = JudgePolicy("cmd", "authority", search_seconds=0, score_timeout_seconds=10, analysis=AnalysisOptions(3))
    checkpoint = ScoreCheckpoint(request, scored(objects), 109_000_000_000)
    checkpoint.install(tmp_path)
    with pytest.raises(ValueError, match="duplicate"):
        checkpoint.install(tmp_path)
    with pytest.raises(ValueError, match="another dispatch"):
        ScoreCheckpoint.read(tmp_path, BlobRef.of(b"other"))
    monkeypatch.setattr(checkpoint_module.time, "monotonic", lambda: 110.)
    watch = PhaseWatch(tmp_path, request, policy, 100.)
    assert not watch() and watch.checkpoint == checkpoint
    monkeypatch.setattr(checkpoint_module.time, "monotonic", lambda: 112.)
    assert watch() and watch.stop_reason == "analysis-timeout"
    # A ten-second scoring budget cannot be borrowed by analysis (or vice versa).
    empty = tmp_path / "empty"
    empty.mkdir()
    watch = PhaseWatch(empty, request, policy, 100.)
    assert watch() and watch.stop_reason == "judge-worker-timeout"


def test_checkpoint_keeps_large_decided_result_without_a_private_size_cap(tmp_path):
    objects = DirectoryStore(tmp_path / "objects")
    base = scored(objects)
    result = replace(base, detail={**dict(base.detail), "diagnostic": "x" * ((1 << 20) + 1)})
    checkpoint = ScoreCheckpoint(BlobRef.of(b"request"), result, time.monotonic_ns())
    checkpoint.install(tmp_path)
    assert (tmp_path / "score.json").stat().st_size > 1 << 20
    assert ScoreCheckpoint.read(tmp_path, checkpoint.request) == checkpoint


@pytest.mark.parametrize("score", [None, 0, 1])
def test_final_baseline_checkpoint_stops_without_waiting_for_deadline(tmp_path, monkeypatch, score):
    import behave.task_execution.judge as checkpoint_module

    objects = DirectoryStore(tmp_path / "objects")
    request = BlobRef.of(b"request")
    policy = JudgePolicy("cmd", "authority", search_seconds=0, score_timeout_seconds=10)
    result = scored(objects)
    result = (replace(result, score=None, code="reference-timeout", origin="reference")
              if score is None else replace(result, score=score))
    checkpoint = ScoreCheckpoint(request, result, 109_000_000_000)
    checkpoint.install(tmp_path)
    watch = PhaseWatch(tmp_path, request, policy, 100.)
    monkeypatch.setattr(checkpoint_module.time, "monotonic", lambda: 109.5)
    assert watch() and watch.checkpoint == checkpoint
    assert watch.stop_reason is None
    monkeypatch.setattr(checkpoint_module.time, "monotonic", lambda: 110.)
    assert watch() and watch.stop_reason is None


def test_no_result_checkpoint_does_not_start_analysis(tmp_path, monkeypatch):
    import behave.task_execution.judge as checkpoint_module

    objects = DirectoryStore(tmp_path / "objects")
    request = BlobRef.of(b"request")
    policy = JudgePolicy("cmd", "authority", score_timeout_seconds=10, analysis=AnalysisOptions(50))
    result = replace(scored(objects), score=None, code="reference-timeout", origin="reference")
    checkpoint = ScoreCheckpoint(request, result, 109_000_000_000)
    checkpoint.install(tmp_path)
    watch = PhaseWatch(tmp_path, request, policy, 100.)
    monkeypatch.setattr(checkpoint_module.time, "monotonic", lambda: 110.)
    assert watch() and watch.stop_reason is None
    assert watch.checkpoint == checkpoint


@pytest.mark.parametrize("analysis", [None, AnalysisOptions(10)])
@pytest.mark.parametrize("cleanup", [CleanupState.COMPLETE, CleanupState.INCOMPLETE])
def test_no_result_survives_teardown_without_becoming_a_score(tmp_path, monkeypatch, analysis, cleanup):
    from behave.isolation import service
    from behave.task_execution import judge

    inputs, journal = comparison(tmp_path)
    inputs = replace(inputs, judge_policy=replace(inputs.policy, analysis=analysis).to_bytes())
    workdir = tmp_path / "worker"
    workdir.mkdir()
    published = []
    completed = []

    def worker(argv, **kwargs):
        local = DirectoryStore(workdir / "objects")
        result = replace(scored(local), score=None, code="reference-timeout", origin="reference")
        completed.append(result)
        request = BlobRef(argv[-2], int(argv[-1]))
        ScoreCheckpoint(request, result, time.monotonic_ns()).install(workdir)
        assert kwargs["cancelled"]()
        status = "timeout" if cleanup.quiescent else "cleanup-incomplete"
        return service.ServiceResult(status, -9, b"terminal evidence", b"", .1)

    monkeypatch.setattr(service, "run_service", worker)
    result = judge.run_worker(
        ComparisonRequest(inputs, workdir, on_decided=published.append), journal.objects)
    assert result.score is None and result.analysis is None
    assert result.code == "reference-timeout" and result.origin == "reference"
    assert result.cleanup is cleanup and not published
    assert result.evidence[:len(completed[0].evidence)] == completed[0].evidence
    for ref in result.closure:
        assert journal.objects.read(ref) is not None


@pytest.mark.parametrize("error", [OSError("publication interrupted"), RuntimeError("publication interrupted")])
def test_failed_checkpoint_publication_is_not_committed_or_retried(tmp_path, capsys, error):
    objects = DirectoryStore(tmp_path / "objects")
    request = BlobRef.of(b"request")
    policy = JudgePolicy("cmd", "authority", analysis=AnalysisOptions(wall_timeout_seconds=60))
    began = time.monotonic()
    checkpoint = ScoreCheckpoint(request, scored(objects), time.monotonic_ns())
    checkpoint.install(tmp_path)
    calls = []

    def publish(result):
        calls.append(result)  # A side effect may precede the publication error.
        raise error

    watch = PhaseWatch(tmp_path, request, policy, began, on_decided=publish)
    assert watch() and watch.stop_reason == "judge-checkpoint-publication-failed"
    assert watch.checkpoint is None
    watch.refresh()  # The post-supervisor collection must not retry the callback.
    assert watch() and calls == [checkpoint.result]
    assert capsys.readouterr().err.count("publication interrupted") == 1


@pytest.mark.parametrize(("analysis", "failure"), [
    (None, "missing-evidence"),
    (AnalysisOptions(wall_timeout_seconds=60), "missing-evidence"),
    (AnalysisOptions(wall_timeout_seconds=60), "callback"),
])
@pytest.mark.parametrize("cleanup", [CleanupState.COMPLETE, CleanupState.INCOMPLETE])
def test_failed_publication_cannot_fall_back_to_an_unpublished_final_score(
        tmp_path, monkeypatch, capsys, analysis, failure, cleanup):
    from behave.isolation import service
    from behave.task_execution import judge

    inputs, journal = comparison(tmp_path)
    inputs = replace(inputs, judge_policy=replace(inputs.policy, analysis=analysis).to_bytes())
    workdir = tmp_path / "worker"
    workdir.mkdir()
    calls = []

    def publish(result):
        calls.append(result)
        raise RuntimeError("score event write failed")

    def worker(argv, **kwargs):
        local = DirectoryStore(workdir / "objects")
        decided = scored(local)
        if failure == "missing-evidence":
            decided = replace(decided, evidence=(BlobRef.of(b"not stored"),))
        request = BlobRef(argv[-2], int(argv[-1]))
        ScoreCheckpoint(request, decided, time.monotonic_ns()).install(workdir)
        assert kwargs["cancelled"]()
        # A response file must not turn a failed score publication into success.
        (workdir / "response.json").write_bytes(canonical_json({
            "schema": judge.WORKER_SCHEMA, "request": request.as_dict(), "result": decided.as_dict()}))
        status = "exited" if cleanup.quiescent else "cleanup-incomplete"
        return service.ServiceResult(status, 0, b"", b"", .1)

    monkeypatch.setattr(service, "run_service", worker)
    result = judge.run_worker(ComparisonRequest(inputs, workdir, on_decided=publish), journal.objects)
    assert result.score is None and result.analysis is None and not result.retryable
    assert result.origin == "infrastructure" and result.cleanup is cleanup
    assert result.code == ("judge-checkpoint-publication-failed" if cleanup.quiescent
                           else "judge-worker-cleanup-incomplete")
    assert len(calls) == (1 if failure == "callback" else 0)
    assert "Traceback" in capsys.readouterr().err


@pytest.mark.parametrize("score", [0, 1])
@pytest.mark.parametrize("poll", [False, True])
def test_worker_publishes_decided_evidence_once_including_fast_exit(tmp_path, monkeypatch, score, poll):
    from behave.isolation import service
    from behave.task_execution import judge

    inputs, journal = comparison(tmp_path)
    inputs = replace(inputs, judge_policy=replace(inputs.policy, analysis=AnalysisOptions(wall_timeout_seconds=60)).to_bytes())
    workdir = tmp_path / "worker"
    workdir.mkdir()
    published = []
    original_publish = journal.objects.publish
    closures = []

    def copy_once(source, refs):
        refs = tuple(refs)
        closures.append(refs)
        original_publish(source, refs)

    monkeypatch.setattr(journal.objects, "publish", copy_once)

    def worker(argv, **kwargs):
        local = DirectoryStore(workdir / "objects")
        decided = scored(local, score)
        request = BlobRef(argv[-2], int(argv[-1]))
        ScoreCheckpoint(request, decided, time.monotonic_ns()).install(workdir)
        if poll:
            assert not kwargs["cancelled"]()
            assert published == [decided]
        policy = local.put(canonical_json(inputs.policy.analysis.as_dict()))
        final = replace(decided, analysis=AnalysisReport(decided.detail["evaluation"], policy,
                                                       "analysis-service-failed"))
        (workdir / "response.json").write_bytes(canonical_json({
            "schema": judge.WORKER_SCHEMA, "request": request.as_dict(), "result": final.as_dict()}))
        return service.ServiceResult("exited", 0, b"", b"", .1)

    monkeypatch.setattr(service, "run_service", worker)
    result = judge.run_worker(ComparisonRequest(inputs, workdir, on_decided=published.append), journal.objects)
    assert result.score == score and result.analysis.code == "analysis-service-failed"
    assert len(published) == 1
    assert closures == [published[0].closure, result.analysis.closure]


@pytest.mark.parametrize("failure", ["timeout", "cancelled", "output-limit", "failed", "cleanup-incomplete",
                                   "bad-final", "changed-score", "missing-analysis"])
@pytest.mark.parametrize("analysis", [None, AnalysisOptions(10)])
def test_post_decision_failure_retains_score_and_does_not_retry(tmp_path, monkeypatch, failure, analysis):
    from behave.isolation import service
    from behave.task_execution.judge import WORKER_SCHEMA as SCHEMA

    calls = []
    def fixture(argv, **kwargs):
        calls.append(argv)
        workdir = kwargs["cwd"]
        objects = DirectoryStore(workdir / "objects")
        request = BlobRef(argv[-2], int(argv[-1]))
        decided = scored(objects)
        ScoreCheckpoint(request, decided, time.monotonic_ns()).install(workdir)
        assert kwargs["cancelled"]() is (analysis is None)
        if failure in {"bad-final", "changed-score", "missing-analysis"}:
            final = decided if failure == "missing-analysis" else replace(decided, score=0)
            (workdir / "response.json").write_bytes(b"invalid" if failure == "bad-final" else canonical_json({
                "schema": SCHEMA, "request": request.as_dict(), "result": final.as_dict()}))
            return service.ServiceResult("exited", 0, b"", b"", .1)
        return service.ServiceResult("exited" if failure == "failed" else failure, -9, b"log", b"", .1)
    monkeypatch.setattr(service, "run_service", fixture)
    policy = JudgePolicy("cmd", "authority", search_seconds=0, max_retries=2, analysis=analysis)
    task, submission, journal = setup(tmp_path, policy=policy)
    result = run(task, submission, journal, SimulatorJudge())
    assert result.behavior.score == 1 and not result.behavior.retryable
    if analysis is None:
        assert result.behavior.analysis is None
    else:
        assert result.behavior.analysis.status == "unavailable"
    if failure == "cleanup-incomplete":
        assert len(calls) == 1 and result.rtl.code == "judge-previous-cleanup-incomplete"
    else:
        assert len(calls) == 2 and result.score == 1
    assert run(task, submission, journal, ReplayJudge()).as_dict() == result.as_dict()
    for event in journal.load():
        for ref in event.artifacts:
            assert journal.objects.read(ref) is not None


@pytest.mark.parametrize("analysis", [None, AnalysisOptions(wall_timeout_seconds=60)])
@pytest.mark.parametrize("shape", ["unbound", "future", "late", "fifo", "symlink"])
def test_invalid_checkpoint_never_authorizes_a_score(tmp_path, monkeypatch, shape, analysis):
    import os

    from behave.isolation import service
    def fixture(argv, **kwargs):
        workdir = kwargs["cwd"]
        objects = DirectoryStore(workdir / "objects")
        if shape == "fifo":
            os.mkfifo(workdir / "score.json")
        elif shape == "symlink":
            (workdir / "score.json").symlink_to("objects")
        else:
            request = BlobRef.of(b"wrong") if shape == "unbound" else BlobRef(argv[-2], int(argv[-1]))
            start = time.monotonic_ns() + (1_000_000_000 if shape == "future" else -2_000_000_000)
            ScoreCheckpoint(request, scored(objects), start).install(workdir)
        return service.ServiceResult("exited", 1, b"", b"", .1)
    monkeypatch.setattr(service, "run_service", fixture)
    task, submission, journal = setup(tmp_path, rtl=None, policy=JudgePolicy("cmd", "auth", search_seconds=0, analysis=analysis))
    result = run(task, submission, journal, SimulatorJudge())
    assert result.behavior.score is None and result.behavior.code == "judge-checkpoint-invalid"


@pytest.mark.isolation
def test_baseline_and_explicit_replay_publish_the_actual_service_decision(tmp_path):
    from behave.core.artifacts import FileData, LoadedSource
    from behave.task_execution.capture import FrozenSubmission
    from behave.task_execution.comparison import ComparisonInputs
    from behave.task_execution.simulator_judge import _SimulatorService

    task, submission, journal = real_task(tmp_path, rtl=None)
    frozen = FrozenSubmission.from_bytes(journal.objects.read(submission))
    source = LoadedSource(frozen.behavior.artifact, tuple(
        FileData(file, journal.objects.read(file.content)) for file in frozen.behavior.files))
    policy = replace(JudgePolicy.from_bytes(task.judge_policy), search_seconds=0, analysis=None)
    inputs = ComparisonInputs("behavior", source, task.reference_behavior, task.contract,
                              task.run_policy, policy.to_bytes())
    for name in ("baseline", "explicit"):
        workdir = tmp_path / name
        workdir.mkdir()
        decided = []
        result = _SimulatorService().evaluate(ComparisonRequest(inputs, workdir), journal.objects,
                                               on_decided=decided.append)
        assert result.score == 1 and decided == [result], result.as_dict()
        inputs = replace(inputs, replay_stimuli=journal.objects.read(
            BlobRef.from_dict(dict(result.detail["replay_pool"]))))


@pytest.mark.isolation
@pytest.mark.parametrize("score", [None, 0, 1])
def test_baseline_score_survives_slow_frame_teardown_after_decision(tmp_path, monkeypatch, score):
    from behave.isolation import service

    original = service.run_service
    # Reproduce the return boundary, not a slow candidate: the score and its
    # evidence are complete, but releasing a local evaluation graph stalls.
    script = '''import sys, time
sys.path.insert(0, sys.argv.pop(1))
from behave.task_execution import simulator_judge, judge
from behave.task_execution.judgment import ComponentJudgment
from behave.core.artifacts import BlobRef
class EvaluationGraph:
    def __del__(self):
        time.sleep(60)
def fixture(self, request, objects, *, on_decided):
    graph = EvaluationGraph()
    result = ComponentJudgment(SCORE, "fixture-score", origin="reference" if SCORE is None else None,
        evidence=(objects.put(b"decided"),),
        detail={"evaluation": BlobRef.of(b"evaluation").sha256})
    on_decided(result)
    return result
simulator_judge._SimulatorService.evaluate = fixture
judge.main()
'''.replace("SCORE", str(score))

    def inject(argv, **kwargs):
        return original((*argv[:4], script, *argv[5:]), **kwargs)

    monkeypatch.setattr(service, "run_service", inject)
    # This exercises post-decision cleanup, not a three-second cold-import
    # race under parallel tests. The destructor still exceeds this allowance;
    # the published checkpoint, not the deadline, must stop the worker.
    policy = JudgePolicy("cmd", "authority", search_seconds=0, max_retries=0,
                         score_timeout_seconds=15, analysis=None)
    task, submission, journal = setup(tmp_path, rtl=None, policy=policy)
    result = run(task, submission, journal, SimulatorJudge())
    assert result.behavior.score == score, result.as_dict()
    assert result.behavior.code == "fixture-score", result.as_dict()
    assert result.behavior.analysis is None
    assert result.behavior.cleanup is CleanupState.COMPLETE
    assert not result.behavior.retryable
    # Baseline returns after cleanup; the optional-analysis journal API is
    # unchanged and is not needed to read the same decided baseline score.
    assert not any(event.kind == "judge_score_decided" for event in journal.load())
    assert run(task, submission, journal, ReplayJudge()).as_dict() == result.as_dict()
    receipts = [parse_json(journal.objects.read(ref)) for ref in result.behavior.evidence
                if journal.objects.read(ref).startswith(b'{')]
    assert any(row.get("schema") == "behave.judge-worker-receipt/4"
               and row["status"] == "cancelled" for row in receipts)


@pytest.mark.parametrize("invalid_checkpoint", [False, True])
def test_pre_score_failure_cannot_hide_incomplete_cleanup(tmp_path, monkeypatch, invalid_checkpoint):
    from behave.isolation import service
    calls = []
    def fixture(argv, **kwargs):
        calls.append(argv)
        if invalid_checkpoint:
            (kwargs["cwd"] / "score.json").write_bytes(b"invalid")
        # The earlier phase failure is subordinate to the lifetime failure.
        kwargs["cancelled"].stop_reason = "judge-worker-timeout"
        return service.ServiceResult("cleanup-incomplete", None, b"", b"", .1)
    monkeypatch.setattr(service, "run_service", fixture)
    task, submission, journal = setup(tmp_path, policy=JudgePolicy("cmd", "auth", search_seconds=0, analysis=AnalysisOptions(wall_timeout_seconds=60)))
    result = run(task, submission, journal, SimulatorJudge())
    assert result.behavior.code == "judge-worker-cleanup-incomplete"
    assert result.rtl.code == "judge-previous-cleanup-incomplete" and len(calls) == 1


@pytest.mark.isolation
@pytest.mark.parametrize("failure", ["hang", "exception"])
def test_real_supervisor_stops_analysis_failure_and_preserves_decided_score(tmp_path, monkeypatch, failure):
    from behave.isolation import service
    original = service.run_service
    # Replace only the trusted service implementation in this test child. The
    # public worker, checkpoint transport and actual PID-namespace supervisor run
    # unchanged; no candidate-controlled fixture hook enters production.
    script = '''import sys, time
sys.path.insert(0, sys.argv.pop(1))
from behave.task_execution import simulator_judge, judge
from behave.task_execution.judgment import ComponentJudgment
from behave.core.artifacts import BlobRef
def fixture(self, request, objects, *, on_decided):
    on_decided(ComponentJudgment(1, "fixture-score", evidence=(objects.put(b"decided"),),
        detail={"evaluation": BlobRef.of(b"evaluation").sha256}))
    time.sleep(60)
simulator_judge._SimulatorService.evaluate = fixture
judge.main()
'''
    if failure == "exception":
        script = script.replace("time.sleep(60)", 'raise RuntimeError("worker fixture analysis crashed")')
    published_while_running = []
    def inject(argv, **kwargs):
        original_poll = kwargs["cancelled"]
        def observe():
            stopped = original_poll()
            events = journal.load()
            if events[-1].kind == "judge_score_decided":
                from behave.task_execution.score_progress import load_score_progress
                # This executes inside the real supervisor poll, before its
                # worker returns and before a settlement receipt exists.
                published_while_running.append(load_score_progress(
                    journal.objects, BlobRef.of(events[-1].to_bytes())))
            return stopped
        kwargs["cancelled"] = observe
        return original((*argv[:4], script, *argv[5:]), **kwargs)
    monkeypatch.setattr(service, "run_service", inject)
    policy = JudgePolicy("cmd", "authority", search_seconds=0, max_retries=0, analysis=AnalysisOptions(1))
    task, submission, journal = setup(tmp_path, rtl=None, policy=policy)
    result = run(task, submission, journal, SimulatorJudge())
    assert result.behavior.score == 1, result.as_dict()
    expected_code = "analysis-timeout" if failure == "hang" else "analysis-invalid-response"
    assert result.behavior.analysis.code == expected_code, result.as_dict()
    if failure == "hang":
        assert published_while_running and published_while_running[0]["score"] == 1
        assert published_while_running[0]["execution_settled"] is False
    # A crashing worker may exit between polls; final collection still publishes
    # its checkpoint once, without requiring a scheduling delay in the fixture.
    assert sum(event.kind == "judge_score_decided" for event in journal.load()) == 1
    documents = [parse_json(journal.objects.read(ref)) for ref in result.behavior.evidence
                 if journal.objects.read(ref).startswith(b'{')]
    receipts = [row for row in documents if row.get("schema") == "behave.judge-worker-receipt/4"]
    assert receipts and receipts[0]["status"] == ("cancelled" if failure == "hang" else "exited")
    if failure == "exception":
        stderr = journal.objects.read(BlobRef.from_dict(receipts[0]["stderr"]))
        assert b"Traceback" in stderr and b"RuntimeError: worker fixture analysis crashed" in stderr


@pytest.mark.isolation
@pytest.mark.eda
@pytest.mark.solver
def test_real_behavior_and_rtl_analysis_are_separate_bound_results(tmp_path):
    task, submission, journal = real_task(tmp_path)
    # This is a positive handoff test, including independent native trace replay
    # and audit. Those phases exceeded 90s in the fixed-node profile. Keep the
    # declared total, with queries inheriting its remainder. The separate
    # hung-analysis test keeps its 1s deadline and explicit-cap tests stay intact.
    policy = replace(JudgePolicy.from_bytes(task.judge_policy), analysis=AnalysisOptions(300, batch=8))
    task = replace(task, judge_policy=policy.to_bytes())
    result = run(task, submission, journal, SimulatorJudge())
    assert result.score == 1, result.as_dict()
    for component in (result.behavior, result.rtl):
        assert component.analysis is not None and component.analysis.status == "returned", component.as_dict()
        native = parse_json(journal.objects.read(component.evidence[0]))
        assert native["result"]["analysis"] is None  # Score-only invocation stays sealed.
        assert component.analysis.concrete == component.detail["evaluation"]
        assert component.analysis.concrete == native["result"]["decision"]["authority"]["evaluation_fingerprint"]
        outcome = parse_json(journal.objects.read(component.analysis.outcome))
        assert outcome["concrete"] == component.analysis.concrete
        assert outcome["verdict"] == "classified" and outcome["per_goal"], outcome
        assert component.analysis.policy == BlobRef.of(canonical_json(policy.analysis.as_dict()))
    assert run(task, submission, journal, ReplayJudge()).as_dict() == result.as_dict()


@pytest.mark.isolation
def test_analysis_reuses_typed_result_without_generation_replay_or_score_rewrite(tmp_path, monkeypatch):
    from behave.core.artifacts import FileData, LoadedSource
    from behave.sim import api, command, evaluation, generation
    from behave.task_execution.capture import FrozenSubmission
    from behave.task_execution.comparison import ComparisonInputs, ComparisonRequest
    from behave.task_execution.simulator_judge import _SimulatorService

    task, submission, journal = real_task(tmp_path, rtl=None)
    policy = replace(JudgePolicy.from_bytes(task.judge_policy), analysis=AnalysisOptions(60, 1000, 1000, 8))
    task = replace(task, judge_policy=policy.to_bytes())
    frozen = FrozenSubmission.from_bytes(journal.objects.read(submission))
    source = LoadedSource(frozen.behavior.artifact, tuple(
        FileData(file, journal.objects.read(file.content)) for file in frozen.behavior.files))
    workdir = tmp_path / "execution"
    workdir.mkdir()
    request = ComparisonRequest(ComparisonInputs("behavior", source, task.reference_behavior, task.contract,
                                                 task.run_policy, task.judge_policy), workdir)
    decided = []
    def forbidden(*a, **kw):
        pytest.fail("post-score analysis re-entered generation or concrete evaluation")
    def checkpoint(result):
        decided.append(result)
        for module, name in ((command, "handle_command"), (generation, "generate_stimuli"),
                             (evaluation, "evaluate_generated")):
            monkeypatch.setattr(module, name, forbidden)
    analyzed = []
    original = api.analyse_command
    def observed(result, **kwargs):
        from pathlib import Path
        before = Path(result.record_path).read_bytes()
        outcome = original(result, **kwargs)
        assert Path(result.record_path).read_bytes() == before
        analyzed.append(result.evaluation.fingerprint)
        return outcome
    monkeypatch.setattr(api, "analyse_command", observed)
    result = _SimulatorService().evaluate(request, journal.objects, on_decided=checkpoint)
    assert len(decided) == 1 and replace(result, analysis=None) == decided[0], result.as_dict()
    assert result.analysis.status == "returned" and analyzed == [result.analysis.concrete]
