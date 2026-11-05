"""Cancellation is not a candidate failure, including queued final verification."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from test_judge import ScriptedJudge, setup
from test_testbench_reports import POLICY, ReportBackend, inputs

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.task_execution.judge import judge_submission
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.testbench import observe_testbench


def test_cancel_between_components_preserves_first_score_and_replays(tmp_path):
    task, submission, journal = setup(tmp_path)
    cancelled = Event()
    class Judge(ScriptedJudge):
        def evaluate(self, request, objects):
            result = super().evaluate(request, objects)
            cancelled.set()
            return result
    judge = Judge(ComponentJudgment(1, "match"))
    result = judge_submission(task=task, submission=submission, journal=journal, backend=judge,
                              cancelled=cancelled.is_set)
    assert result.behavior.score == 1 and result.rtl.score is None and result.score is None
    assert result.rtl.code == "judge-cancelled" and len(judge.requests) == 1
    assert judge_submission(task=task, submission=submission, journal=journal, backend=ScriptedJudge()) == result


def test_waiting_for_verifier_capacity_is_cancellable_without_dispatch(tmp_path):
    task, submission, journal = setup(tmp_path)
    pool = ResourcePool(AdmissionPolicy("final-verification", 1))
    waiting = Event()
    cancelled = Event()
    def check():
        waiting.set()
        return cancelled.is_set()
    with ThreadPoolExecutor(max_workers=1) as workers, pool.acquire():
        future = workers.submit(judge_submission, task=task, submission=submission, journal=journal,
                                backend=ScriptedJudge(), admission=pool, cancelled=check)
        assert waiting.wait(5)
        cancelled.set()
        result = future.result(timeout=5)
    assert result.score is None
    assert not any(event.kind == "judge_requested" for event in journal.load())
    assert pool.counts == (0, 0)
    assert judge_submission(task=task, submission=submission, journal=journal,
                             backend=ScriptedJudge(), admission=pool) == result


def test_cancelled_tb_never_dispatches_and_does_not_change_judge(tmp_path):
    task, public, submission, judge_journal, tb_journal = inputs(tmp_path)
    judgment = judge_submission(task=task, submission=submission, journal=judge_journal,
                                backend=ScriptedJudge(ComponentJudgment(1, "ok"), ComponentJudgment(1, "ok")))
    before = judge_journal.load()
    got = observe_testbench(task=public, submission=submission, policy=POLICY, journal=tb_journal,
                            backend=ReportBackend(), cancelled=lambda: True)
    assert got.observation.code == "testbench-cancelled" and got.observation.coverage is None
    assert judge_journal.load() == before and judgment.score == 1
    assert observe_testbench(task=public, submission=submission, policy=POLICY, journal=tb_journal,
                             backend=ReportBackend()) == got


def test_cancel_propagates_to_fixed_service_and_is_not_retried(tmp_path, monkeypatch):
    from test_cocotb_backend import POLICY as COCOTB_POLICY
    from test_cocotb_backend import submitted

    from behave.isolation import service
    from behave.task_execution.cocotb_worker import CocotbBackend

    _, public, submission, _, journal = submitted(tmp_path)
    count = 0
    def cancel_after_launch(*args, **kwargs):
        nonlocal count
        count += 1
        assert callable(kwargs["cancelled"])
        assert not kwargs["cancelled"]()
        return service.ServiceResult("cancelled", None, b"", b"", .01)
    monkeypatch.setattr(service, "run_service", cancel_after_launch)
    result = observe_testbench(task=public, submission=submission, policy=COCOTB_POLICY, journal=journal,
                               backend=CocotbBackend())
    assert count == 1 and result.observation.code == "testbench-worker-cancelled"
    assert not result.observation.retryable
