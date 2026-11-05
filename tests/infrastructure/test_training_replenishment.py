"""Whole-group replenishment reuses batch execution, packing and checkpoint state."""

import json
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest
from test_slime_training_plan import inputs

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.train.pool import ReplenishingGroups, SamplingPolicy, replenishment_limit, selected_indices, task_reserve


def native_fixture_program(requirements, body):
    # Explicit fixture evidence, never used as a generated-model accuracy result.
    return body + f'''
from behave.train.training_data import TokenTrace, TrainingRequirements
from behave.core.artifacts import BlobRef
policy = TrainingRequirements.from_bytes({canonical_json(requirements.as_dict())!r})
trace = TokenTrace(policy.adapter, BlobRef.of(canonical_json(request.as_dict())),
    policy.checkpoint, policy.tokenizer, policy.template, (10, 11), (12, 13), (1, 1),
    {{'old_log_probs': (-0.5, -0.5)}})
sys.stdout.buffer.write(encode_model_outcome(request,
    ModelReply('', calls, raw=canonical_json(trace.as_dict()), token_data=trace.as_dict())))
'''


class FixtureTokenVerifier:
    def __init__(self, requirements):
        self.identity = requirements.adapter

    def verify(self, trace, request, raw_response):
        from behave.train.training_data import TokenTrace

        assert trace == TokenTrace.from_dict(json.loads(raw_response))


@pytest.mark.parametrize("constant", [0, .5, 1])
def test_constant_groups_refill_but_seven_of_eight_is_kept(constant):
    race = ReplenishingGroups(tuple(g for g in "abc" for _ in range(8)), 1, 1)
    assert race.admit(range(24), 32) == tuple(range(8))
    for i in range(7):
        race.record(i, constant)
    assert race.admit(range(7, 24), 32) == (7,)
    race.record(7, constant)
    assert race.admit(range(8, 24), 32) == tuple(range(8, 16))
    for i in range(8, 16):
        race.record(i, 0 if i == 15 else 1)
    result = race.summary()
    assert result["status"] == "ready"
    assert result["selected_indices"] == list(range(8, 16))
    assert result["observations"][0]["rewards"] == [constant] * 8
    assert result["observations"][1]["rewards"] == [1] * 7 + [0]
    assert race.admit(range(16, 24), 32) == ()
    assert result["launched_candidates"] == 16
    assert result["planned_candidates"] == 24


def test_unavailable_tokens_and_late_cancelled_groups_do_not_become_mastery():
    race = ReplenishingGroups(("a", "a", "b", "b", "c", "c"), 1, 2)
    race.record(0, 1)
    race.record(1, 0, eligible=False)
    race.record(2, 1)
    race.record(3, 0)
    race.record(4, 0)
    race.record(5, 0)
    race.record(2, 1)
    summary = race.summary()
    assert summary["selected_groups"] == ["b"]
    assert [g["status"] for g in summary["observations"]] == ["unavailable", "selected"]
    assert summary["completion_order"] == ["a", "b", "c"]


def test_reserve_exhaustion_is_a_report_not_a_shortened_optimizer_batch():
    race = ReplenishingGroups(("a", "a", "b", "b"), 2, 2)
    for index, reward in enumerate((1, 0, 1, 1)):
        race.record(index, reward)
    assert race.summary()["status"] == "insufficient_groups"
    assert not race.stop.is_set()
    assert race.admit((), 4) == ()
    with pytest.raises(ValueError, match="no update"):
        selected_indices({"group_selection": race.summary()})


def test_reward_variation_uses_final_components_not_nullable_raw_score(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    account = store.put(canonical_json({"turns": [{"exclusion": None}]}))
    race = ReplenishingGroups(("a", "a"), 1, 1)
    for index, judgment in enumerate((None, {"rtl": {"score": 1}, "behavior": {"score": None}})):
        ref = store.put(canonical_json({"accounting": account.as_dict(), "judgment": judgment}))
        race.observe(index, {"result": ref.as_dict(), "score": None, "error": None}, store)
    assert race.summary()["observations"][0]["rewards"] == [0, .5]
    assert race.summary()["selected_indices"] == [0, 1]


@pytest.mark.parametrize("order", ["cycle", "shuffle"])
def test_reserve_preserves_initial_sample_and_contains_only_approved_tasks(order):
    tasks = tuple("abcdefg")
    sampler = SamplingPolicy(2, order, "fixed")
    for update in range(4):
        reserve = task_reserve(tasks, sampler, update)
        assert reserve[:2] == sampler.select(tasks, update)
        assert len(reserve) == len(set(reserve)) == len(tasks)
        assert set(reserve) == set(tasks)
        assert reserve == task_reserve(tasks, SamplingPolicy.from_dict(sampler.as_dict()), update)
    assert replenishment_limit({"max_groups": None}, target=2, window=3, pool_size=7) == 7
    assert replenishment_limit({"max_groups": 4}, target=2, window=3, pool_size=7) == 4
    with pytest.raises(ValueError, match="max_groups"):
        replenishment_limit({"max_groups": 1}, target=2, window=3, pool_size=7)


@pytest.mark.parametrize("window,interrupt_reserve", [(1, False), (1, True), (2, False)])
def test_shared_batch_refills_before_slow_peers_finish_and_replays(
        tmp_path, publication, monkeypatch, window, interrupt_reserve):
    from test_evaluation import plan_for
    from test_task_workflow import setup

    from behave.task_execution import batch as batch_module
    from behave.task_execution.batch import read_batch_report, summarize_recorded
    from behave.task_execution.judgment import ComponentJudgment, SubmissionJudgment
    from behave.task_execution.result import TaskReport, TaskResult

    job, _ = setup(tmp_path, publication)
    jobs = tuple(replace(job, attempt=replace(job.attempt, attempt_id=f"candidate-{i}")) for i in range(8))
    plan = replace(plan_for(*jobs), workers=3, score_on_submit=False)
    started, replacement_started = [], Event()

    def execute(**kwargs):
        job, run = kwargs["job"], kwargs["run"]
        index = int(job.attempt.attempt_id.rsplit("-", 1)[1])
        started.append(index)
        if window == 2 and index in (2, 3):
            assert replacement_started.wait(5), "replacement waited for the entire previous batch"
        if index == 4:
            replacement_started.set()
        account = run.objects.put(canonical_json({"turns": [{"exclusion": None}], "charges": [],
            "unpriced_dispatches": 0, "unresolved_dispatches": 0, "billing_source": "fixture"}))
        head = run.objects.put(b"fixture submission and solver head")
        judgment = SubmissionJudgment(head, None, ComponentJudgment(int(index in (0, 1, 4)), "fixture"), head)
        result = TaskResult(job.task.task_id, job.task.release, job.task.revision, job.attempt.attempt_id,
                            head, "final_action", head, judgment, None, None, account)
        ref = run.objects.put(result.to_bytes())
        run.journal("task").append((), "finished", {"result": ref.as_dict()}, (account, head, ref))
        return TaskReport(result, ref, head)

    monkeypatch.setattr(batch_module, "execute_task", execute)
    race = ReplenishingGroups(tuple(g for g in "abcd" for _ in range(2)), 1, window)
    args = dict(plan=plan, reader=publication.reader, directory=tmp_path / "batch",
                report_summary=summarize_recorded, report_reader=read_batch_report)
    if interrupt_reserve:
        from behave.agent.replay import Tape

        record = Tape.record
        def interrupted(self, kind, payload, *args):
            event = record(self, kind, payload, *args)
            if kind == "evaluation_case" and payload["index"] == 6:
                raise InterruptedError("fixture crash after recording an unused reserve slot")
            return event
        monkeypatch.setattr(Tape, "record", interrupted)
        with pytest.raises(InterruptedError):
            batch_module.run_batch(**args, case_scheduler=race, cancelled=race.stop.is_set)
        monkeypatch.setattr(Tape, "record", record)
        race = ReplenishingGroups(race.group_ids, 1, window)
    report = batch_module.run_batch(**args, case_scheduler=race, cancelled=race.stop.is_set)
    assert replacement_started.is_set()
    assert race.summary()["selected_groups"] == ["c"]
    # Depending on the completion order, d may start before c finishes. No more
    # than three candidate processes and two unfinished groups are admitted.
    assert set(started) >= set(range(6))
    assert race.summary()["launched_candidates"] == len(started)
    assert len(report.summary["candidates"]) == 8
    if window == 1:
        assert set(started) == set(range(6))
        assert all(row["error"]["code"] == "candidate-not-admitted"
                   for row in report.summary["candidates"][6:])
    monkeypatch.setattr(batch_module, "execute_task", lambda **kw: pytest.fail("closed replay dispatched a candidate"))
    cold = ReplenishingGroups(race.group_ids, 1, window)
    assert batch_module.run_batch(**args, case_scheduler=cold, cancelled=cold.stop.is_set) == report
    assert cold.summary() == race.summary()


def test_native_and_control_share_the_same_reserve_and_observe_filtered_groups(tmp_path, publication, inputs):
    from test_slime_task_pool import completed_checkpoint, source_at

    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.train.pool import select_pool
    from behave.train.slime_data import ScoredDataSource
    from behave.train.slime_rollout import round_plan

    source = source_at(tmp_path, publication, inputs, tasks="abcde", replenishment={"max_groups": None})
    policy = SamplingPolicy.from_dict(source.binding["sampler"])
    selection = select_pool(source._pool_tasks, source._pool_state(()), policy,
                            0, include_reserve=True)
    store = DirectoryStore(source.binding["objects"])
    kwargs = dict(round_id=0, endpoint=LocalChatEndpoint.from_dict(source.binding["endpoint"]),
                  policy=BlobRef.of(b"fixture policy"), task_selection=selection)
    plan = round_plan(source.binding, store, **kwargs)
    assert len(plan.execution.jobs) == 15
    assert plan == round_plan(source.binding, store, **{**kwargs, "task_selection": json.loads(canonical_json(selection))})
    race = ReplenishingGroups(plan.group_ids, 1, 2)
    for i, reward in enumerate((1, 1, 1, 0, 0, 0, 1, 0, 1)):
        race.record(i, reward)
    batch = SimpleNamespace(group_selection=race.summary(), candidates=())
    fields, refs = source._pool_commit((), 0, plan, batch)
    state = json.loads(source.run.objects.read(refs[0]))
    assert state["tasks"]["a"]["category"] == "mastered"
    assert state["tasks"]["b"]["last_rewards"] == [0, 0, 0]
    assert state["tasks"]["c"]["last_rewards"] == [1, 0, 1]
    assert state["tasks"]["d"]["last_selected"] == -1
    assert state["tasks"]["e"]["observed_groups"] == 0
    # Same serialized state format as normal training and self-improvement.
    assert source._read_pool(source.run.objects, [SimpleNamespace(kind="round_scored", payload=fields)]) == state
    source.journal.append((), "round_scored", {"round": 0, "binding": source.identity.as_dict(), **fields}, refs)
    completed_checkpoint(source, 0)
    cold = ScoredDataSource(source.args)
    cold.load(0)
    assert cold._pool_state(cold.journal.load()) == state


def test_without_curriculum_reserve_honors_explicit_budget(tmp_path, publication, inputs):
    from test_slime_task_pool import source_at

    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.train.slime_rollout import round_plan

    source = source_at(tmp_path, publication, inputs, tasks="abcde", replenishment={"max_groups": 3})
    binding = {key: value for key, value in source.binding.items() if key != "curriculum"}
    plan = round_plan(binding, DirectoryStore(binding["objects"]), round_id=1,
        endpoint=LocalChatEndpoint.from_dict(binding["endpoint"]), policy=BlobRef.of(b"policy"))
    assert tuple(dict.fromkeys(plan.group_ids)) == ("c", "d", "a")
    assert len(plan.execution.jobs) == 9


@pytest.mark.isolation
@pytest.mark.parametrize("varied", [True, False])
def test_real_process_replenishment_seals_native_tokens_and_replays(tmp_path, publication, monkeypatch, varied):
    from test_evaluation import Judge
    from test_task_workflow import SUBMIT, setup
    from test_training_rollouts import plan_at

    from behave.agent.run_store import JournalDirectory
    from behave.task_execution import batch as batch_module
    from behave.train.learning_report import prepare_training_report
    from behave.train.rollouts import run_rollouts
    plan = plan_at(tmp_path, publication)
    submit, _ = setup(tmp_path / "submit", publication,
                      native_fixture_program(plan.requirements, SUBMIT.rsplit("sys.stdout.buffer.write(", 1)[0]))
    stop, _ = setup(tmp_path / "stop", publication, native_fixture_program(plan.requirements, "calls = ()\n"))
    jobs = tuple(replace(job, attempt=replace(job.attempt, attempt_id=f"candidate-{index}"))
                 for index, job in enumerate((submit, submit, submit, stop, stop, stop)
                                             if varied else (submit,) * 6))
    plan = replace(plan, execution=replace(plan.execution, jobs=jobs, workers=2),
                   group_ids=("constant", "constant", "mixed", "mixed", "unused", "unused"))
    verifier = FixtureTokenVerifier(plan.requirements)
    run = JournalDirectory(tmp_path / "actual")
    args = dict(plan=plan, approved_manifest=plan.manifest, reader=publication.reader,
                run=run, judge=Judge(), verifier=verifier, update_groups=1, active_groups=1)
    if not varied:
        args["on_prepared"] = lambda *_: pytest.fail("exhausted reserve was sent to the optimizer")
    report = run_rollouts(**args)
    assert report["group_selection"]["selected_indices"] == ([2, 3] if varied else [])
    assert report["group_selection"]["status"] == ("ready" if varied else "insufficient_groups")
    assert report["group_selection"]["observations"][0]["rewards"] == [.5, .5]
    head, _ = run.seal("training")
    prepared = prepare_training_report(run.objects, head, approved_manifest=plan.manifest, verifier=verifier)
    assert prepared.summary == report
    assert len(prepared.examples[2].eligible) == 2
    assert prepared.examples[3].candidate.reward == (0 if varied else .5)
    assert all(t.tokens.full_loss_mask == (0, 0, 1, 1)
               for example in prepared.examples if example is not None for t in example.eligible)
    monkeypatch.setattr(batch_module, "execute_task", lambda **kw: pytest.fail("cold replay dispatched a solver"))
    assert run_rollouts(**args) == report
