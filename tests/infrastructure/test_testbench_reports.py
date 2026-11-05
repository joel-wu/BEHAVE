"""Report-layer fixtures only; no DUT/TB execution or measured coverage claim."""

from dataclasses import fields as dataclass_fields
from dataclasses import replace

import pytest
from test_judge import ScriptedJudge, run, setup

from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import AttemptJournal
from behave.core.artifacts import BlobRef, FileData, FileRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import PublicTaskInputs
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.tb_report import RTLCoverage, TBObservation
from behave.task_execution.testbench import TBPolicy, TBRequest, observe_testbench


def inputs(tmp_path, **changes):
    task, submission, score_journal = setup(tmp_path, testbench=b"submitted TB", **changes)
    spec = FileData(FileRef("spec.md", BlobRef.of(b"public spec")), b"public spec")
    public = PublicTaskInputs(task.task_id, task.release, task.revision, "spec.md", task.contract.reference.path,
                             (spec, task.contract))
    return task, public, submission, score_journal, AttemptJournal(tmp_path / "tb-events", score_journal.objects)


class ReportBackend:
    identity = "fixture-tb/1"

    def __init__(self, *actions):
        self.actions = list(actions)
        self.requests = []

    def observe(self, request, objects):
        self.requests.append(request)
        assert type(request.task) is PublicTaskInputs
        assert {field.name for field in dataclass_fields(TBRequest)} == {
            "task", "submission", "behavior", "rtl", "testbench", "policy", "dispatch", "workdir", "cancelled"}
        assert callable(request.cancelled)
        assert b"class Identity" not in b"".join(file.data for file in request.task.files)
        assert request.workdir.parent.as_posix() == "/tmp" and request.workdir.is_dir()
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action(request, objects) if callable(action) else action


POLICY = TBPolicy(ReportBackend.identity, "fixture-collector/1", "fixture-line-points")


def measured(request, objects):
    rtl = objects.put(canonical_json(request.rtl.artifact.as_dict()))
    points = objects.put(b"fixture evidence, not a real simulation")
    return TBObservation("completed", "fixture", "passed", RTLCoverage(
        rtl, POLICY.collector, POLICY.metric, 3, 10, points))


def observe(public, submission, journal, backend, policy=POLICY):
    return observe_testbench(task=public, submission=submission, policy=policy, journal=journal, backend=backend)


@pytest.mark.parametrize("outcome", [measured, TBObservation("failed", "assertion", "failed"),
                                     TBObservation("unavailable", "unsupported")])
def test_tb_sidecar_never_changes_scores_and_replays_without_running(tmp_path, outcome):
    task, public, submission, score_journal, tb_journal = inputs(tmp_path)
    score = run(task, submission, score_journal, ScriptedJudge(
        ComponentJudgment(1, "fixture"), ComponentJudgment(0, "fixture")))
    score_bytes = canonical_json(score.as_dict())
    events_before = score_journal.load()
    backend = ReportBackend(outcome)
    report = observe(public, submission, tb_journal, backend)
    recorded_inputs = parse_json(tb_journal.objects.read(report.inputs))
    assert {file["path"] for file in recorded_inputs["public_files"]} == {public.spec_path}
    assert "contract_path" not in recorded_inputs
    assert len(backend.requests) == 1
    assert "score" not in report.as_dict() and "reward" not in report.as_dict()
    assert canonical_json(score.as_dict()) == score_bytes and score.score == .5
    assert score_journal.load() == events_before
    assert observe(public, submission, tb_journal, ReportBackend()).as_dict() == report.as_dict()
    assert not backend.requests[0].workdir.exists()


@pytest.mark.parametrize("component", ["behavior", "rtl", "testbench"])
def test_missing_components_do_not_run_and_do_not_invent_zero_coverage(tmp_path, component):
    from behave.task_execution.capture import CapturedComponent, FrozenSubmission

    _, public, submission, _, journal = inputs(tmp_path)
    frozen = FrozenSubmission.from_bytes(journal.objects.read(submission))
    frozen = replace(frozen, **{component: CapturedComponent(None, (), ())})
    report = observe(public, journal.objects.put(frozen.to_bytes()), journal, ReportBackend())
    assert report.observation.status == "unavailable"
    assert report.observation.coverage is None
    assert journal.load()[1].payload["missing"] == (component,)


def test_tb_retry_uses_the_same_three_frozen_artifacts(tmp_path):
    _, public, submission, _, journal = inputs(tmp_path)
    backend = ReportBackend(TBObservation("unavailable", "worker", retryable=True), measured)
    report = observe(public, submission, journal, backend)
    first, second = backend.requests
    assert first.dispatch == 0 and second.dispatch == 1
    for name in ("task", "submission", "behavior", "rtl", "testbench", "policy"):
        assert getattr(first, name) == getattr(second, name)
    assert first.workdir != second.workdir
    assert report.observation.coverage.fraction == .3


@pytest.mark.parametrize("change", [lambda coverage: replace(coverage, rtl=BlobRef.of(b"reference RTL")),
                                   lambda coverage: replace(coverage, collector="other/1"),
                                   lambda coverage: replace(coverage, metric="other-metric"),
                                   lambda coverage: replace(coverage, evidence=BlobRef.of(b"missing"))])
def test_unbound_or_missing_measurement_evidence_is_unavailable(tmp_path, change):
    _, public, submission, _, journal = inputs(tmp_path)
    def wrong(request, objects):
        result = measured(request, objects)
        return replace(result, coverage=change(result.coverage))
    report = observe(public, submission, journal, ReportBackend(wrong, wrong))
    assert report.observation.status == "unavailable"
    assert report.observation.coverage is None


def test_unexpected_tb_backend_error_does_not_become_test_failure(tmp_path):
    _, public, submission, _, journal = inputs(tmp_path)
    backend = ReportBackend(RuntimeError("private"))
    report = observe(public, submission, journal, backend)
    assert report.observation.status == "unavailable"
    assert len(backend.requests) == 1 and not report.observation.retryable
    assert not report.observation.cleanup.quiescent
    assert "private" not in str(report.as_dict())


def test_unknown_dispatch_requires_fencing(tmp_path):
    _, public, submission, _, journal = inputs(tmp_path)
    class Died(BaseException):
        pass
    with pytest.raises(Died):
        observe(public, submission, journal, ReportBackend(Died()))
    assert journal.load()[-1].kind == "tb_requested"
    with pytest.raises(AttemptInterrupted, match="unknown"):
        observe(public, submission, journal, ReportBackend(measured))


def test_tb_journal_is_not_the_score_journal(tmp_path):
    task, public, submission, score_journal, _ = inputs(tmp_path)
    result = run(task, submission, score_journal, ScriptedJudge(
        ComponentJudgment(1, "fixture"), ComponentJudgment(1, "fixture")))
    with pytest.raises(RecordError):
        observe(public, submission, score_journal, ReportBackend(measured))
    assert run(task, submission, score_journal, ScriptedJudge()).as_dict() == result.as_dict()


def test_changed_policy_or_public_inputs_cannot_reuse_a_report(tmp_path):
    _, public, submission, _, journal = inputs(tmp_path)
    observe(public, submission, journal, ReportBackend(measured))
    with pytest.raises(RecordError):
        observe(public, submission, journal, ReportBackend(), replace(POLICY, timeout_seconds=20))
    replacement = FileData(FileRef("spec.md", BlobRef.of(b"changed")), b"changed")
    altered = replace(public, files=(replacement, public.files[1]))
    with pytest.raises(RecordError):
        observe(altered, submission, journal, ReportBackend())


def test_tb_report_closure_can_be_copied_and_recovered(tmp_path):
    _, public, submission, _, journal = inputs(tmp_path)
    expected = observe(public, submission, journal, ReportBackend(measured))
    copy = AttemptJournal(tmp_path / "copy/events", DirectoryStore(tmp_path / "copy/objects"))
    prefix = ()
    for event in journal.load():
        copy.objects.publish(journal.objects, event.artifacts)
        prefix = (*prefix, copy.append(prefix, event.kind, dict(event.payload), event.artifacts))
    assert observe(public, submission, copy, ReportBackend()).as_dict() == expected.as_dict()


@pytest.mark.parametrize("covered,total", [(True, 1), (1, False), (-1, 2), (2, 1), (0, -1), (1., 2)])
def test_coverage_counts_are_exact_integers(covered, total):
    with pytest.raises(ValueError):
        RTLCoverage(BlobRef.of(b"rtl"), "collector", "metric", covered, total, BlobRef.of(b"measurement"))


def test_measured_zero_empty_denominator_and_missing_are_different():
    zero = RTLCoverage(BlobRef.of(b"rtl"), "collector", "metric", 0, 10, BlobRef.of(b"measurement"))
    assert zero.fraction == 0
    assert replace(zero, total=0).fraction is None
    assert TBObservation("unavailable", "not-run").coverage is None
    with pytest.raises(ValueError):
        TBObservation("unavailable", "not-run", coverage=zero)


def test_report_and_policy_roundtrip_without_score_fields():
    assert TBPolicy.from_bytes(POLICY.to_bytes()) == POLICY
    result = TBObservation("completed", "fixture", "failed")
    assert TBObservation.from_dict(result.as_dict()) == result
    for key in ("score", "reward"):
        with pytest.raises(ValueError):
            TBObservation.from_dict({**result.as_dict(), key: 1})
    row = parse_json(POLICY.to_bytes())
    with pytest.raises(ValueError):
        TBPolicy.from_bytes(canonical_json({**row, "timeout_seconds": 0}))
