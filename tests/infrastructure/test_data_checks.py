"""Construction checks share the real score operation, not the solver reward."""

from dataclasses import replace

import pytest
from test_judge import real_task, setup

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptInterrupted
from behave.core.artifacts import BlobRef, FileData, FileRef, LoadedSource
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, parse_json
from behave.data import checks
from behave.data.checks import ConcreteCheck, check_sources
from behave.task_execution.analysis import AnalysisOptions
from behave.task_execution.capture import FrozenSubmission
from behave.task_execution.comparison import ComparisonInputs
from behave.task_execution.judgment import ComponentJudgment


def comparison(tmp_path, *, component="behavior", real=False, **changes):
    task, submission, journal = (real_task if real else setup)(tmp_path, **changes)
    frozen = FrozenSubmission.from_bytes(journal.objects.read(submission))
    selected = getattr(frozen, component)
    source = LoadedSource(selected.artifact, tuple(FileData(ref, journal.objects.read(ref.content))
                                                  for ref in selected.files))
    return ComparisonInputs(component, source, task.reference_behavior, task.contract,
                            task.run_policy, task.judge_policy), journal


def replace_source(source, data):
    file = FileData(FileRef(source.artifact.entry_file, BlobRef.of(data)), data)
    return LoadedSource(replace(source.artifact, files=(file.reference,)), (file,))


@pytest.mark.parametrize("visible", [False, True])
def test_comparison_reference_visibility_survives_worker_transport(tmp_path, visible):
    from behave.task_execution.comparison import ComparisonRequest
    from behave.task_execution.judge import _restore, _stage

    inputs, journal = comparison(tmp_path)
    inputs = replace(inputs, reference_visible=visible)
    row = inputs.as_dict()
    assert ("reference_visible" in row) == visible
    packet = _stage(ComparisonRequest(inputs, tmp_path), journal.objects)
    restored = _restore(packet, journal.objects, tmp_path / "worker")
    assert restored.inputs == inputs
    assert restored.inputs.reference_visible is visible
    assert ComparisonInputs.from_dict({key: value for key, value in row.items()
        if key != "reference_visible"}, journal.objects).reference_visible is False


def test_construction_check_freezes_inputs_and_replays_without_execution(tmp_path, monkeypatch):
    inputs, journal = comparison(tmp_path)
    calls = []
    def compare(got, *, objects, workdir, **kwargs):
        assert workdir.parent.as_posix() == "/tmp"
        calls.append(got)
        return ComponentJudgment(1, "fixture", evidence=(objects.put(b"fixture evidence"),))
    monkeypatch.setattr(checks, "compare_sources", compare)
    result = check_sources(inputs, journal=journal)
    assert result.outcome == "passed" and ConcreteCheck.from_dict(result.as_dict()) == result
    assert check_sources(inputs, journal=journal) == result and calls == [inputs]
    assert ComparisonInputs.from_dict(parse_json(journal.objects.read(result.inputs)), journal.objects) == inputs
    for event in journal.load():
        for ref in event.artifacts:
            assert journal.objects.read(ref) is not None
    changed = replace(inputs, source=replace_source(inputs.source, b"class New: pass\n"))
    with pytest.raises(RecordError, match="replay differs"):
        check_sources(changed, journal=journal)
    # Publication/rollout identities and the second candidate are not fabricated.
    assert set(inputs.as_dict()) == {"schema", "component", "source", "reference_behavior",
                                     "contract", "run_policy", "judge_policy"}


@pytest.mark.parametrize("outcome", [ComponentJudgment(0, "mismatch"),
    ComponentJudgment(None, "reference-broken", "reference", retryable=True)])
def test_failed_or_unavailable_check_never_retries_itself(tmp_path, monkeypatch, outcome):
    inputs, journal = comparison(tmp_path)
    calls = []
    def compare(*args, **kwargs):
        calls.append(True)
        return outcome
    monkeypatch.setattr(checks, "compare_sources", compare)
    report = check_sources(inputs, journal=journal)
    assert report.outcome == ("failed" if outcome.score == 0 else "unavailable")
    assert check_sources(inputs, journal=journal) == report and len(calls) == 1


def test_construction_check_cannot_enable_analysis(tmp_path, monkeypatch):
    inputs, journal = comparison(tmp_path)
    policy = replace(inputs.policy, analysis=AnalysisOptions(wall_timeout_seconds=60))
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: pytest.fail("must not dispatch"))
    with pytest.raises(ValueError, match="concrete-only"):
        check_sources(replace(inputs, judge_policy=policy.to_bytes()), journal=journal)
    assert not journal.load()


def test_check_cancellation_before_dispatch_is_a_replayable_decline(tmp_path, monkeypatch):
    inputs, journal = comparison(tmp_path)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: pytest.fail("must not dispatch"))
    report = check_sources(inputs, journal=journal, cancelled=lambda: True)
    assert report.outcome == "unavailable" and report.result.cleanup.quiescent
    assert check_sources(inputs, journal=journal) == report
    assert "check_requested" not in [event.kind for event in journal.load()]


@pytest.mark.parametrize("error", [OSError("lost supervisor"), AttemptInterrupted("unconfirmed-cancel")])
def test_unknown_check_cannot_relaunch_or_reuse_capacity(tmp_path, monkeypatch, error):
    inputs, journal = comparison(tmp_path)
    pool = ResourcePool(AdmissionPolicy("checks", 1))
    def interrupted(*args, **kwargs):
        raise error
    monkeypatch.setattr(checks, "compare_sources", interrupted)
    with pytest.raises(type(error)):
        check_sources(inputs, journal=journal, admission=pool)
    assert journal.load()[-1].kind == "check_requested"
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: pytest.fail("must not relaunch"))
    # Restarting the coordinator must not reset the old service's uncertainty.
    pool = ResourcePool(pool.policy)
    with pytest.raises(AttemptInterrupted, match="unknown"):
        check_sources(inputs, journal=journal, admission=pool)
    with pytest.raises(AttemptInterrupted, match="cleanup-unknown"), pool.acquire():
        pytest.fail("unconfirmed service must quarantine shared capacity")


def test_returned_cleanup_witness_is_retained_and_quarantines_capacity(tmp_path, monkeypatch):
    inputs, journal = comparison(tmp_path)
    pool = ResourcePool(AdmissionPolicy("checks", 1))
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(
        None, "worker-cleanup-incomplete", "infrastructure", cleanup=CleanupState.INCOMPLETE))
    report = check_sources(inputs, journal=journal, admission=pool)
    assert report.outcome == "unavailable" and report.result.cleanup is CleanupState.INCOMPLETE
    pool = ResourcePool(pool.policy)
    assert check_sources(inputs, journal=journal, admission=pool) == report
    with pytest.raises(AttemptInterrupted, match="cleanup-incomplete"), pool.acquire():
        pytest.fail("capacity cannot be reused")


@pytest.mark.isolation
@pytest.mark.parametrize("component", ["behavior", "rtl"])
@pytest.mark.parametrize("wrong", [False, True])
def test_real_construction_comparison_without_rollout_or_analysis(tmp_path, monkeypatch, component, wrong):
    inputs, journal = comparison(tmp_path, component=component, real=True)
    if wrong:
        body = inputs.source.files[0].data
        body = body.replace(b"'y': a", b"'y': a ^ 1") if component == "behavior" else body.replace(
            b"assign out_y = in_a;", b"assign out_y = in_a ^ 8'h01;")
        inputs = replace(inputs, source=replace_source(inputs.source, body))
    assert inputs.policy.analysis is None
    report = check_sources(inputs, journal=journal)
    assert report.result.score == (0 if wrong else 1), report.as_dict()
    assert report.outcome == ("failed" if wrong else "passed")
    native = parse_json(journal.objects.read(report.result.evidence[0]))
    assert native["result"]["analysis"] is None
    assert native["evaluation"]["accepted"] > 0
    assert native["result"]["decision"]["authority"]["passed"] is (not wrong)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: pytest.fail("must replay without simulator"))
    assert check_sources(inputs, journal=journal) == report


@pytest.mark.isolation
def test_real_broken_reference_is_unavailable_not_candidate_failure(tmp_path):
    inputs, journal = comparison(tmp_path, real=True)
    broken = inputs.reference_behavior.files[0].data.replace(
        b"return {'out_y': in_a}", b"raise ValueError('broken reference')")
    inputs = replace(inputs, reference_behavior=replace_source(inputs.reference_behavior, broken))
    report = check_sources(inputs, journal=journal)
    assert report.outcome == "unavailable" and report.result.score is None, report.as_dict()
    assert report.result.origin == "reference", report.as_dict()


@pytest.mark.isolation
@pytest.mark.parametrize("component", ["behavior", "rtl"])
def test_real_stateful_comparison_with_registered_output_and_backpressure(tmp_path, component):
    from behave.sim import api

    behavior = b'''class M:
    def reset(self): self.acc = 0
    def process(self, a):
        self.acc = (self.acc + a) & 255
        return {'y': self.acc}
'''
    reference = b'''class Identity:
    def reset(self): self.total = 0
    def process(self, in_a):
        self.total = (self.total + in_a) & 255
        return {'out_y': self.total}
'''
    rtl = b'''module M(input clk, rst, in_valid, input [7:0] in_a,
output in_ready, output reg out_valid, input out_ready, output reg [7:0] out_y);
reg [7:0] acc;
assign in_ready = !out_valid || out_ready;
always @(posedge clk) begin
    if (rst) begin acc <= 0; out_valid <= 0; out_y <= 0; end
    else if (in_ready) begin
        out_valid <= in_valid;
        if (in_valid) begin acc <= acc + in_a; out_y <= acc + in_a; end
    end
end
endmodule
'''
    inputs, journal = comparison(tmp_path, component=component, real=True, behavior=behavior, rtl=rtl)
    inputs = replace(inputs, reference_behavior=replace_source(inputs.reference_behavior, reference),
                     run_policy=canonical_json(api.GenerationSpec(12, 2, 3, 4, 2).as_dict()),
                     judge_policy=replace(inputs.policy, baseline_cases=4, sequence_length=2).to_bytes())
    report = check_sources(inputs, journal=journal)
    assert report.outcome == "passed", report.as_dict()
    native = parse_json(journal.objects.read(report.result.evidence[0]))
    assert native["evaluation"]["accepted"] > 0
    assert native["result"]["analysis"] is None
