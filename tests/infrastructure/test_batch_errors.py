"""Coordinator errors are candidate-local; committed evidence is not discarded."""

from dataclasses import replace

import pytest
from test_batch_recovery import worker_owner  # noqa: F401 -- shared native/local worker lanes
from test_evaluation import Judge, plan_for
from test_task_workflow import setup

from behave.agent.model import Charge
from behave.core.artifacts import BlobRef, read_verified
from behave.task_execution import batch as batch_module, testbench
from behave.task_execution.accounting import load_accounting
from behave.task_execution.batch import load_batch_report
from behave.task_execution.run_store import RunStore

pytestmark = pytest.mark.usefixtures("worker_owner", "priced_local_worker")


@pytest.fixture
def priced_local_worker(monkeypatch):
    import test_execution_lifecycle

    scripted = test_execution_lifecycle.Scripted
    monkeypatch.setattr(test_execution_lifecycle, "Scripted", lambda *replies: scripted(*(
        replace(reply, charge=Charge("0.01", "USD", "fixture")) for reply in replies)))


@pytest.mark.parametrize("after_execution", [False, True])
def test_parent_exception_keeps_other_candidates_and_raw_evidence(
        tmp_path, publication, monkeypatch, after_execution):
    broken, _ = setup(tmp_path, publication)
    good = replace(broken, attempt=replace(broken.attempt, attempt_id="good"))
    plan = plan_for(broken, good)
    root = RunStore(tmp_path / "batch")
    execute = batch_module.execute_task
    committed = {}
    histories = {}

    def fail_one(**kwargs):
        job, run = kwargs["job"], kwargs["run"]
        if job == good or after_execution:
            committed[job.attempt.attempt_id] = execute(**kwargs)
            histories[job.attempt.attempt_id] = {
                phase: run.journal(phase).load() for phase in ("task", "solver", "supervision", "judge")}
        if job == broken:
            raise KeyError("PRIVATE_ADAPTER_DETAIL")
        return committed[job.attempt.attempt_id]

    monkeypatch.setattr(batch_module, "execute_task", fail_one)
    judge = Judge()
    report = batch_module.run_batch(plan=plan, reader=publication.reader, directory=root.directory, judge=judge)
    summary = report.as_dict()["summary"]
    bad_row, good_row = summary["candidates"]
    assert summary["requested"] == 2 and summary["scored"] == 1 and summary["no_result"] == 1
    assert bad_row["error"] == {"type": "KeyError", "code": "candidate-KeyError"}
    assert bad_row["result"] is None and bad_row["final_score"] == 0
    assert good_row["score"] == .5 and summary["mean"] == .25
    assert "PRIVATE_ADAPTER_DETAIL" not in str(summary)
    assert len(judge.requests) == (4 if after_execution else 2)
    # Missing final accounting is explicitly unavailable, never fabricated as $0.
    assert bad_row["billing"] is None and not summary["billing"]["fully_accounted"]
    assert summary["billing"]["unavailable_candidates"] == 1
    assert summary["billing"]["known_estimates_by_currency"] == good_row["billing"]["known_estimates_by_currency"]
    assert float(summary["billing"]["known_estimates_by_currency"]["USD"]) > 0
    for job in (broken, good) if after_execution else (good,):
        run = RunStore(root.directory / "candidates" / BlobRef.of(job.to_bytes()).sha256)
        result = committed[job.attempt.attempt_id].result
        assert result.submission is not None
        assert read_verified(run.objects, result.submission)
        assert load_accounting(run.objects, result).billing.charges
        for phase, events in histories[job.attempt.attempt_id].items():
            assert run.journal(phase).load() == events
    assert load_batch_report(root.objects, report.head) == report
    monkeypatch.setattr(batch_module, "execute_task", lambda **kw: pytest.fail("completed batch must not resample"))
    assert batch_module.run_batch(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge()) == report


def test_tb_parent_exception_preserves_scores_submissions_and_billing(
        tmp_path, publication, monkeypatch):
    from test_testbench_reports import POLICY, ReportBackend

    first, _ = setup(tmp_path, publication)
    second = replace(first, attempt=replace(first.attempt, attempt_id="second"))
    plan = plan_for(first, second, testbench=POLICY)
    root = RunStore(tmp_path / "batch")
    first_key = BlobRef.of(first.to_bytes()).sha256
    observe = testbench.observe_testbench

    def fail_after_observation(**kwargs):
        result = observe(**kwargs)
        if kwargs["journal"].directory.parent.name == first_key:
            raise KeyError("PRIVATE_TB_DETAIL")
        return result

    monkeypatch.setattr(testbench, "observe_testbench", fail_after_observation)
    judge = Judge()
    report = batch_module.run_batch(plan=plan, reader=publication.reader, directory=root.directory,
                             judge=judge, testbench=ReportBackend())
    summary = report.as_dict()["summary"]
    bad_row, good_row = summary["candidates"]
    assert summary["requested"] == summary["scored"] == 2 and summary["no_result"] == 0
    assert summary["mean"] == .5 and len(judge.requests) == 4
    assert bad_row["error"] is None and bad_row["testbench"] is None
    assert bad_row["testbench_error"] == {"type": "KeyError", "code": "testbench-KeyError"}
    assert good_row["testbench"] is not None and good_row["testbench_error"] is None
    assert summary["billing"]["fully_accounted"]
    assert summary["billing"]["unavailable_candidates"] == 0
    assert float(summary["billing"]["known_estimates_by_currency"]["USD"]) > 0
    assert "PRIVATE_TB_DETAIL" not in str(summary)
    assert RunStore(root.directory / "candidates" / first_key).journal("testbench").load()[-1].kind == "tb_finished"
    assert load_batch_report(root.objects, report.head) == report
    monkeypatch.setattr(batch_module, "execute_task", lambda **kw: pytest.fail("must not rerun after a TB error"))
    assert batch_module.run_batch(plan=plan, reader=publication.reader, directory=root.directory,
                           judge=Judge(), testbench=ReportBackend()) == report
