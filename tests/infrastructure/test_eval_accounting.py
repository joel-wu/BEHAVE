"""Batch cost views use sealed provider facts, including unsuccessful candidates."""

from dataclasses import replace

import pytest
from test_accounting import PAID_STOP
from test_evaluation import Judge, plan_for
from test_task_workflow import STOP, setup

from behave.agent.admission import AdmissionPolicy
from behave.agent.journal import Event, load_chain
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.eval.report import load_evaluation_report, summarize
from behave.eval.runner import run_evaluation
from behave.task_execution.run_store import RunStore
from behave.train.training_data import load_candidate


def export_report(run, path):
    target = DirectoryStore(path)
    head, refs = run.seal("report")
    for ref in refs:
        target.put(run.objects.read(ref))
    return target, head


@pytest.mark.isolation
def test_batch_accounts_no_submission_and_failed_judging_then_verifies_offline(tmp_path, publication, monkeypatch):
    submitted, _ = setup(tmp_path / "submit", publication)
    stopped, _ = setup(tmp_path / "stop", publication, PAID_STOP)
    stopped = replace(stopped, attempt=replace(stopped.attempt, attempt_id="stopped"))
    plan = plan_for(submitted, stopped, model_admission=AdmissionPolicy("models", 1))
    root = RunStore(tmp_path / "batch")
    report = run_evaluation(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge(no_result=True))
    summary = report.as_dict()["summary"]
    assert (summary["scored"], summary["no_result"], summary["no_submission"]) == (1, 1, 1)
    assert [row["score"] for row in summary["candidates"]] == [None, 0]
    assert summary["billing"] == {
        "basis": "recorded-api-estimates", "known_estimates_by_currency": {"USD": "0.05"},
        "priced_dispatches": 3, "unpriced_dispatches": 0, "unresolved_dispatches": 0,
        "accounted_candidates": 2, "unavailable_candidates": 0, "fully_accounted": True}
    assert [row["billing"]["source"] for row in summary["candidates"]] == ["provider", "provider"]

    exported, head = export_report(root, tmp_path / "export")
    assert head == report.head
    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("offline reporting must never dispatch"))
    assert load_evaluation_report(exported, head) == report
    for row in summary["candidates"]:
        # The report shows a candidate with an unresolved judgment as None; the
        # training reward settles that component as zero: (0 + 1) / 2 here.
        assert load_candidate(exported, BlobRef.from_dict(row["result"])).reward == (
            0.5 if row["score"] is None else row["score"])
    assert run_evaluation(plan=plan, reader=publication.reader, directory=root.directory,
                          judge=Judge(no_result=True), cancelled=lambda: True) == report

    # Recompute from original rows, not derived fields or self-reported totals.
    rows = [thaw_json(event.payload)["row"] for event in load_chain(exported, head)
            if event.kind == "evaluation_case"]
    rows.sort(key=lambda row: [j.attempt.attempt_id for j in plan.jobs].index(row["attempt_id"]))
    assert summarize(plan, rows, exported) == summary
    from behave.task_execution.batch import read_batch_report, summarize_recorded

    assert summarize_recorded(plan, rows, exported) == summary
    assert read_batch_report(exported, head) == report
    changed_job = replace(submitted, attempt=replace(submitted.attempt, max_turns=submitted.attempt.max_turns + 1))
    changed_rows = [{**rows[0], "run_key": BlobRef.of(changed_job.to_bytes()).sha256}, rows[1]]
    with pytest.raises(RecordError, match="accounting execution"):
        summarize(replace(plan, jobs=(changed_job, stopped)), changed_rows, exported)

    terminal = load_chain(exported, head)[-1]
    forged_summary = {**summary, "billing": {**summary["billing"], "known_estimates_by_currency": {"USD": "0"}}}
    forged_ref = exported.put(canonical_json(forged_summary))
    forged_head = exported.put(replace(terminal, payload={"summary": forged_ref.as_dict()},
                                       artifacts=(forged_ref,)).to_bytes())
    with pytest.raises(RecordError) as caught:
        load_evaluation_report(exported, forged_head)
    assert caught.value.code == "evaluation-summary-mismatch"


@pytest.mark.isolation
def test_lost_reply_is_billed_once_without_a_training_turn(tmp_path, publication, monkeypatch):
    from behave.agent import local_rpc
    monkeypatch.setattr(local_rpc, "send_frame", lambda *a, **kw: None)
    job, _ = setup(tmp_path, publication, PAID_STOP)
    plan = plan_for(job, model_admission=AdmissionPolicy("models", 1))
    root = RunStore(tmp_path / "batch")
    report = run_evaluation(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge())
    assert report.summary["no_result"] == 1
    assert report.summary["billing"]["known_estimates_by_currency"] == {"USD": "0.03"}
    assert report.summary["billing"]["priced_dispatches"] == 1
    candidate = load_candidate(root.objects, BlobRef.from_dict(report.as_dict()["summary"]["candidates"][0]["result"]))
    assert not candidate.trajectory.turns
    assert load_evaluation_report(root.objects, report.head) == report


@pytest.mark.isolation
def test_batch_preserves_priced_unknown_and_unpriced_usage(tmp_path, publication):
    program = """
from behave.agent.model import ModelFailure
sys.stdout.buffer.write(encode_model_outcome(request, ModelFailure('unknown','fixture-unknown',
                        charge=Charge('0.02','EUR','fixture'))))
"""
    unknown, _ = setup(tmp_path / "unknown", publication, program)
    unpriced, _ = setup(tmp_path / "unpriced", publication, STOP)
    unpriced = replace(unpriced, attempt=replace(unpriced.attempt, attempt_id="unpriced"))
    root = RunStore(tmp_path / "batch")
    plan = plan_for(unknown, unpriced, model_admission=AdmissionPolicy("models", 1))
    report = run_evaluation(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge())
    costs = report.summary["billing"]
    assert costs["known_estimates_by_currency"] == {"EUR": "0.02"}
    assert (costs["priced_dispatches"], costs["unpriced_dispatches"], costs["unresolved_dispatches"]) == (1, 1, 1)
    assert costs["fully_accounted"] is False and costs["unavailable_candidates"] == 0
    assert load_evaluation_report(root.objects, report.head) == report


def test_unavailable_case_is_not_free_and_sealed_report_has_complete_membership(tmp_path, publication, monkeypatch):
    from behave.task_execution import batch as batch_module
    from behave.task_execution import batch as runner

    def history(reader, head):
        return load_chain(reader, head)

    monkeypatch.setattr(batch_module, "load_chain", history)
    def unavailable(**kw):
        raise RecordError("fixture-storage-failure", "no sealed result")
    monkeypatch.setattr(runner, "execute_task", unavailable)
    job, _ = setup(tmp_path, publication, PAID_STOP)
    root = RunStore(tmp_path / "batch")
    report = run_evaluation(plan=plan_for(job), reader=publication.reader, directory=root.directory, judge=Judge())
    summary = report.as_dict()["summary"]
    assert summary["candidates"][0]["billing"] is None
    assert summary["billing"]["known_estimates_by_currency"] == {}
    assert summary["billing"]["unavailable_candidates"] == 1
    assert summary["billing"]["fully_accounted"] is False
    exported, head = export_report(root, tmp_path / "export")
    assert load_evaluation_report(exported, head) == report
    events = load_chain(exported, head)
    # Hash-consistent histories with repeated or missing candidates are invalid.
    for rows in ((), (events[1], events[1])):
        parent = exported.put(events[0].to_bytes())
        for index, event in enumerate((*rows, events[-1]), start=1):
            parent = exported.put(Event(index, parent, event.kind, event.payload, event.artifacts).to_bytes())
        with pytest.raises(RecordError):
            load_evaluation_report(exported, parent)
    bad_row = {**thaw_json(events[1].payload)["row"], "score": 0}
    with pytest.raises(RecordError, match="unavailable outcome"):
        summarize(plan_for(job), [bad_row], exported)


@pytest.mark.isolation
def test_recovery_uses_committed_fees_without_charging_the_candidate_again(tmp_path, publication, monkeypatch):
    from behave.agent.replay import Tape
    from behave.task_execution import batch as runner
    job, _ = setup(tmp_path, publication, PAID_STOP)
    second = replace(job, attempt=replace(job.attempt, attempt_id="second"))
    plan = plan_for(job, second)
    root = RunStore(tmp_path / "batch")
    record = Tape.record
    class Crash(BaseException):
        pass
    def before_summary(self, kind, *args, **kwargs):
        if kind == "evaluation_finished":
            raise Crash()
        return record(self, kind, *args, **kwargs)
    monkeypatch.setattr(Tape, "record", before_summary)
    with pytest.raises(Crash):
        run_evaluation(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge())
    monkeypatch.setattr(Tape, "record", record)
    monkeypatch.setattr(runner, "execute_task", lambda **kw: pytest.fail("case already committed"))
    report = run_evaluation(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge())
    assert report.summary["billing"]["priced_dispatches"] == 2
    assert report.summary["billing"]["known_estimates_by_currency"] == {"USD": "0.06"}
    assert load_evaluation_report(root.objects, report.head) == report
