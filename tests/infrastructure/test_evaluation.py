from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock

import pytest
from test_judge import ScriptedJudge
from test_task_workflow import STOP, setup

from behave.agent.admission import AdmissionPolicy
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError
from behave.eval.plan import EvaluationPlan
from behave.eval.runner import run_evaluation
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.run_store import RunStore
from behave.task_execution.testbench import TBPolicy
from behave.train.training_data import load_candidate


class Judge:
    identity = "thread-safe-fixture-judge/1"

    def __init__(self, no_result=False):
        self.requests = []
        self.lock = Lock()
        self.no_result = no_result

    def evaluate(self, request, objects):
        with self.lock:
            self.requests.append(request)
        if request.component == "rtl" and self.no_result:
            return ComponentJudgment(None, "verifier-unavailable", "infrastructure")
        return ComponentJudgment(int(request.component == "behavior"), "fixture")


def plan_for(*jobs, **changes):
    return EvaluationPlan("fixture-eval", jobs, Judge.identity, 2,
                          AdmissionPolicy("final-verification", 1), **changes)


@pytest.mark.isolation
def test_batch_keeps_denominators_and_can_export_without_candidate_directories(tmp_path, publication, monkeypatch):
    submitted, _ = setup(tmp_path / "submit", publication)
    stopped, _ = setup(tmp_path / "stop", publication, STOP)
    stopped = replace(stopped, attempt=replace(stopped.attempt, attempt_id="stopped"))
    plan = plan_for(submitted, stopped)
    assert EvaluationPlan.from_bytes(plan.to_bytes(), publication.reader) == plan
    root = RunStore(tmp_path / "eval")
    judge = Judge(no_result=True)
    got = run_evaluation(plan=plan, reader=publication.reader, directory=root.directory, judge=judge)
    summary = got.as_dict()["summary"]
    assert (summary["requested"], summary["scored"], summary["no_result"], summary["no_submission"]) == (2, 1, 1, 1)
    assert summary["mean_on_scored"] == 0
    assert summary["mean"] == .25
    assert [row["attempt_id"] for row in summary["candidates"]] == ["solver-a", "stopped"]
    assert len(judge.requests) == 2
    # A historical summary remains valid. New derived final metrics do not
    # alter the original nullable outcomes used to select rejudging work.
    from behave.agent.journal import load_chain
    from behave.core.records import canonical_json
    from behave.task_execution.batch import load_batch_report

    legacy = {key: value for key, value in summary.items() if key != "mean"}
    legacy["candidates"] = [{key: value for key, value in row.items() if key != "final_score"}
                            for row in summary["candidates"]]
    ref = root.objects.put(canonical_json(legacy))
    terminal = load_chain(root.objects, got.head)[-1]
    legacy_head = root.objects.put(replace(terminal, payload={"summary": ref.as_dict()}, artifacts=(ref,)).to_bytes())
    assert load_batch_report(root.objects, legacy_head).as_dict()["summary"] == legacy
    # Root CAS contains the complete sealed history. Training does not open the
    # live solver directory, substitute text tokens or interpret TB status.
    for row in summary["candidates"]:
        candidate = load_candidate(root.objects, BlobRef.from_dict(row["result"]))
        assert candidate.reward == row["final_score"]
        assert candidate.result.score == row["score"]
        assert candidate.result.attempt_id == row["attempt_id"]
        assert all(turn.training_status == "unavailable" for turn in candidate.trajectory.turns)
    from behave.task_execution import batch as runner
    monkeypatch.setattr(runner, "execute_task", lambda **kw: pytest.fail("finished batch must not dispatch"))
    assert run_evaluation(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge(),
                          cancelled=lambda: True) == got
    with pytest.raises(RecordError, match="replay differs"):
        run_evaluation(plan=replace(plan, workers=1), reader=publication.reader, directory=root.directory, judge=Judge())


@pytest.mark.isolation
def test_batch_case_commits_survive_failure_before_summary(tmp_path, publication, monkeypatch):
    from behave.agent.replay import Tape
    from behave.task_execution import batch as runner
    job, _ = setup(tmp_path, publication, STOP)
    plan = plan_for(job)
    root = RunStore(tmp_path / "batch")
    original = Tape.record
    class Crash(BaseException):
        pass
    def record(self, kind, *args, **kwargs):
        if kind == "evaluation_finished":
            raise Crash()
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(Tape, "record", record)
    with pytest.raises(Crash):
        run_evaluation(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge())
    assert root.journal("report").load()[-1].kind == "evaluation_case"
    monkeypatch.setattr(Tape, "record", original)
    monkeypatch.setattr(runner, "execute_task", lambda **kw: pytest.fail("candidate was already committed"))
    assert run_evaluation(plan=plan, reader=publication.reader, directory=root.directory,
                          judge=Judge()).summary["no_submission"] == 1


@pytest.mark.isolation
def test_tb_unavailability_is_separate_from_shared_score(tmp_path, publication):
    from test_testbench_reports import POLICY, ReportBackend
    job, _ = setup(tmp_path, publication)
    plan = plan_for(job, testbench=POLICY)
    root = RunStore(tmp_path / "batch")
    report = run_evaluation(plan=plan, reader=publication.reader, directory=root.directory,
                            judge=Judge(), testbench=ReportBackend())
    row = report.as_dict()["summary"]["candidates"][0]
    assert row["score"] == .5 and row["testbench"] is not None
    assert b'tb-components-missing' in read_verified(root.objects, BlobRef.from_dict(row["testbench"]))


def test_bad_or_mixed_plan_is_rejected_before_dispatch(tmp_path, publication):
    job, _ = setup(tmp_path, publication, STOP)
    with pytest.raises(ValueError, match="unique"):
        plan_for(job, job)
    for workers in (True, 0, -1):
        with pytest.raises(ValueError):
            replace(plan_for(job), workers=workers)
    with pytest.raises(ValueError, match='conflicting admission'):
        plan_for(job, model_admission=AdmissionPolicy('final-verification', 2))
    with pytest.raises(ValueError, match="backend/configuration"):
        run_evaluation(plan=plan_for(job), reader=publication.reader, directory=tmp_path / "bad",
                        judge=ScriptedJudge())
    with pytest.raises(ValueError, match="testbench configuration"):
        run_evaluation(plan=plan_for(job, testbench=TBPolicy("fixture", "fixture", "fixture")),
                        reader=publication.reader, directory=tmp_path / "bad", judge=Judge())


@pytest.mark.parametrize("preview", [0, -1, False, True, 1.5, "32768"])
def test_bad_preview_rejected_by_job_and_saved_plan(tmp_path, publication, preview):
    from behave.core.records import canonical_json, parse_json

    job, _ = setup(tmp_path, publication, STOP)
    with pytest.raises(ValueError, match="output_preview_bytes"):
        replace(job, output_preview_bytes=preview)
    saved = parse_json(plan_for(job).to_bytes())
    saved["jobs"][0]["output_preview_bytes"] = preview
    with pytest.raises(ValueError, match="output_preview_bytes"):
        EvaluationPlan.from_bytes(canonical_json(saved), publication.reader)


@pytest.mark.parametrize("rollouts", [1, 8])
def test_explicit_rollout_count_and_per_candidate_seeds_survive_plan_roundtrip(tmp_path, publication, rollouts):
    from behave.agent.model import ModelRequest
    from behave.agent.providers.chat_wire import request_body
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.core.records import parse_json

    job, _ = setup(tmp_path, publication, STOP)
    base = LocalChatEndpoint("http://127.0.0.1:12345/v1/chat/completions", "fixture", "fixture/1")
    jobs = []
    for index in range(rollouts):
        endpoint = replace(base, parameters={**base.parameters, "seed": 17 + index, "temperature": 0.7})
        jobs.append(replace(job, attempt=replace(job.attempt, attempt_id=f"task-r{index}",
                                                model_policy=endpoint.model_policy()), model=endpoint.command()))
    plan = plan_for(*jobs)
    restored = EvaluationPlan.from_bytes(plan.to_bytes(), publication.reader)
    assert restored == plan and len(restored.jobs) == rollouts
    seeds = []
    for selected in restored.jobs:
        endpoint = LocalChatEndpoint.from_dict(parse_json(selected.model.argv[-1].encode()))
        request = ModelRequest(f"{selected.attempt.attempt_id}:0:0", 0, 0, (), (), selected.attempt.model_policy, 10)
        packet = parse_json(request_body(endpoint, request))
        seeds.append(packet["seed"])
        assert packet["n"] == 1  # Each job owns a conversation, not one choice in a shared turn.
    assert seeds == list(range(17, 17 + rollouts))


@pytest.mark.isolation
def test_cancelled_batch_retains_all_requested_candidates_without_zeros(tmp_path, publication):
    job, _ = setup(tmp_path, publication, STOP)
    second = replace(job, attempt=replace(job.attempt, attempt_id="second"))
    judge = Judge()
    result = run_evaluation(plan=plan_for(job, second), reader=publication.reader,
                            directory=tmp_path / "batch", judge=judge, cancelled=lambda: True)
    assert (result.summary["requested"], result.summary["scored"], result.summary["no_result"]) == (2, 0, 2)
    assert result.summary["mean_on_scored"] is None and not judge.requests


@pytest.mark.isolation
def test_eval_model_admission_is_shared_by_supervised_workers_and_sealed_for_export(tmp_path, publication, monkeypatch):
    from test_chat_transport import config, reply, send, server
    from test_model_relay import wait_for

    from behave.agent.admission import shared_pools
    from behave.task_execution import batch as runner

    reached, release = Event(), Event()
    pools = {}
    def observe_pools(**policies):
        created = shared_pools(**policies)
        pools.update({p.policy.resource: p for p in created.values() if p is not None})
        return created
    monkeypatch.setattr(runner, "shared_pools", observe_pools)
    def answer(handler, observed, stopped):
        reached.set()
        release.wait(15)
        send(handler, reply())
    job, _ = setup(tmp_path, publication, STOP)
    root = RunStore(tmp_path / "batch")
    with server(answer) as (url, observed):
        endpoint = config(url)
        job = replace(job, model=endpoint.command(), attempt=replace(
            job.attempt, model_policy=endpoint.model_policy(), request_timeout_seconds=30))
        second = replace(job, attempt=replace(job.attempt, attempt_id="second"))
        plan = plan_for(job, second, model_admission=AdmissionPolicy("shared-model", 1))
        assert EvaluationPlan.from_bytes(plan.to_bytes(), publication.reader) == plan
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(run_evaluation, plan=plan, reader=publication.reader,
                                     directory=root.directory, judge=Judge())
            try:
                assert reached.wait(10)
                wait_for(lambda: pools["shared-model"].counts == (1, 1), seconds=10)
                assert len(observed) == 1
            finally:
                release.set()
            report = pending.result(30)
        assert report.summary["scored"] == report.summary["no_submission"] == 2
        assert len(observed) == 2 and pools["shared-model"].counts == (0, 0)
        for row in report.as_dict()["summary"]["candidates"]:
            candidate = load_candidate(root.objects, BlobRef.from_dict(row["result"]))
            assert len(candidate.trajectory.turns) == 1
            assert candidate.billing.cost_by_currency() == {"USD": "0.00006"}
        assert run_evaluation(plan=plan, reader=publication.reader, directory=root.directory, judge=Judge()) == report
        assert len(observed) == 2
