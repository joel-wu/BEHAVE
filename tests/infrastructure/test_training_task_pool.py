"""Task cooldowns preserve groups, score policy and checkpoint lineage."""


import pytest
from test_training_sampling import pool_plan

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.train.pool import (
    SamplingPolicy,
    TaskPoolPolicy,
    empty_state,
    observe_batch,
    select_pool,
    select_tasks,
    sync_state,
)

POLICY = TaskPoolPolicy(10, 2)


def tasks(plan):
    return tuple(dict.fromkeys((job.task.task_id, job.task.revision) for job in plan.execution.jobs))


def rows(**rewards):
    return tuple({"group_id": task, "reward": reward} for task, values in rewards.items() for reward in values)


def test_full_success_skips_ten_updates_and_then_rejoins_normally(tmp_path, publication):
    plan = select_tasks(pool_plan(tmp_path, publication), ("a",))
    before = empty_state()
    first = observe_batch(before, plan, rows(a=(1, 1, 1)), POLICY, 0)
    assert first["tasks"]["a"]["category"] == "mastered"
    assert first["tasks"]["a"]["cooldown_until"] == 11
    assert before == empty_state() and first["tasks"]["a"]["observed_groups"] == 1
    resumed = parse_json(canonical_json(first))
    sampler = SamplingPolicy(1, "cycle", "test")
    for update in range(1, 11):
        assert select_pool(tasks(plan), resumed, sampler, update) == ()
    assert select_pool(tasks(plan), resumed, sampler, 11) == ({"task_id": "a", "reason": "rotation"},)
    failed_retest = observe_batch(resumed, plan, rows(a=(1, 0, 1)), POLICY, 11)
    assert failed_retest["tasks"]["a"]["category"] == "active"
    assert failed_retest["tasks"]["a"]["cooldown_until"] == 0


def test_only_all_zero_groups_receive_the_two_update_cooldown(tmp_path, publication):
    plan = select_tasks(pool_plan(tmp_path, publication), ("a",))
    state = observe_batch(empty_state(), plan, rows(a=(0,) * 3), POLICY, 0)
    row = state["tasks"]["a"]
    assert row["category"] == "zero"
    assert row["cooldown_until"] == 3 and row["last_rewards"] == [0] * 3
    sampler = SamplingPolicy(1, "cycle", "test")
    for update in (1, 2):
        assert select_pool(tasks(plan), state, sampler, update) == ()
    assert select_pool(tasks(plan), state, sampler, 3)[0]["task_id"] == "a"
    partial = observe_batch(state, plan, rows(a=(.5,) * 3), POLICY, 3)
    assert partial["tasks"]["a"]["category"] == "active"
    assert partial["tasks"]["a"]["cooldown_until"] == 0


def test_cancelled_surplus_rotates_but_has_no_reward_observation(tmp_path, publication):
    plan = select_tasks(pool_plan(tmp_path, publication), ("a", "b"))
    state = observe_batch(empty_state(), plan, rows(a=(1, 1, 1)), POLICY, 0)
    assert state["tasks"]["a"]["observed_groups"] == 1
    b = state["tasks"]["b"]
    assert b["last_selected"] == 0 and b["last_observed"] == -1
    assert b["observed_groups"] == 0 and b["last_rewards"] == [] and b["cooldown_until"] == 0
    with pytest.raises(ValueError, match="complete"):
        observe_batch(state, plan, rows(b=(0, 0)), POLICY, 1)


def test_expired_tasks_have_no_quota_and_cooling_tasks_never_fill_short_pool(tmp_path, publication):
    plan = pool_plan(tmp_path, publication)
    state = sync_state(empty_state(), tasks(plan))
    for task in "ab":
        state["tasks"][task].update(category="mastered", cooldown_until=4, last_selected=0)
    sampler = SamplingPolicy(3, "cycle", "test")
    first = select_pool(tasks(plan), state, sampler, 1)
    assert [row["task_id"] for row in first] == list("cde")
    due = select_pool(tasks(plan), state, sampler, 4)
    assert {r["task_id"] for r in due} == set("cde")  # Unseen tasks still come first.
    for task in "cde":
        state["tasks"][task]["last_selected"] = 2
    assert [r["task_id"] for r in select_pool(tasks(plan), state, sampler, 4)][:2] == ["a", "b"]
    for task in "cde":
        state["tasks"][task].update(category="zero", cooldown_until=8, last_selected=2)
    assert select_pool(tasks(plan), state, sampler, 3) == ()
    assert select_pool(tasks(plan), state, sampler, 3, include_reserve=True) == ()


def test_new_tasks_and_changed_revisions_do_not_inherit_mastery(tmp_path, publication):
    plan = pool_plan(tmp_path, publication)
    original_tasks = tasks(plan)
    state = sync_state(empty_state(), original_tasks)
    for row in state["tasks"].values():
        row.update(category="mastered", cooldown_until=20, observed_groups=4, last_selected=2)
    pool = (original_tasks[0], ("b", BlobRef.of(b"revised b")), ("new", BlobRef.of(b"new task")))
    state = sync_state(state, pool)
    assert state["tasks"]["a"]["observed_groups"] == 4
    assert state["tasks"]["b"]["observed_groups"] == state["tasks"]["new"]["observed_groups"] == 0
    selection = select_pool(pool, state, SamplingPolicy(2, "cycle", "seed"), 3)
    assert [row["task_id"] for row in selection] == ["b", "new"]
    assert "c" in state["tasks"]  # Retained for explicit future replay, not selected.


@pytest.mark.parametrize("order", ["cycle", "shuffle"])
def test_rotation_cold_restore_and_complete_groups(tmp_path, publication, order):
    plan = pool_plan(tmp_path, publication)
    sampler = SamplingPolicy(2, order, "seed")
    state, seen = empty_state(), set()
    for update in range(4):
        selection = select_pool(tasks(plan), state, sampler, update)
        assert selection == select_pool(tasks(plan), parse_json(canonical_json(state)), sampler, update)
        ids = tuple(row["task_id"] for row in selection)
        selected = select_tasks(plan, ids)
        assert len(selected.execution.jobs) == 6
        seen.update(ids)
        state = observe_batch(state, selected, rows(**{task: (1, 0, 1) for task in ids}), POLICY, update)
    assert seen == set("abcde")


@pytest.mark.parametrize("field,value", [("mastery_cooldown_updates", -1),
    ("zero_cooldown_updates", True), ("zero_cooldown_updates", -1)])
def test_runtime_thresholds_are_explicit(field, value):
    with pytest.raises(ValueError):
        TaskPoolPolicy.from_dict({**POLICY.as_dict(), field: value})
    with pytest.raises(ValueError):
        TaskPoolPolicy.from_dict({})


def test_incomplete_priority_reorders_only_two_initial_slots_and_preserves_old_state(tmp_path, publication):
    from copy import deepcopy

    plan = pool_plan(tmp_path, publication)
    pool = tasks(plan)
    state = sync_state(empty_state(), pool)
    for task in "abc":
        state["tasks"][task].update(observed_groups=1, last_selected=0)
    for task in "de":
        state["tasks"][task]["last_selected"] = 5
    before = deepcopy(state)
    sampler = SamplingPolicy(3, "cycle", "test")
    assert [r["task_id"] for r in select_pool(pool, state, sampler, 6)] == list("abc")
    policy = TaskPoolPolicy(10, 2, 2)
    full = select_pool(pool, state, sampler, 6, policy=policy, include_reserve=True)
    assert [r["task_id"] for r in full] == list("deabc")
    assert select_pool(pool, state, sampler, 6, policy=policy) == full[:3]
    assert [r["reason"] for r in full] == ["incomplete-priority"] * 2 + ["rotation"] * 3
    assert state == before
    assert select_pool(pool, parse_json(canonical_json(state)), sampler, 6, policy=policy) == full[:3]


def test_cancelled_priority_survives_but_rotates_behind_other_incomplete_tasks(tmp_path, publication):
    plan = pool_plan(tmp_path, publication)
    state = sync_state(empty_state(), tasks(plan))
    for task in "abc":
        state["tasks"][task].update(observed_groups=1, last_selected=0)
    for task in "de":
        state["tasks"][task]["last_selected"] = 3
    policy, sampler = TaskPoolPolicy(10, 2, 1), SamplingPolicy(2, "cycle", "test")
    selected = select_pool(tasks(plan), state, sampler, 4, policy=policy)
    assert selected[0]["task_id"] == "d"
    state = observe_batch(state, select_tasks(plan, ("d", "a")), rows(a=(0, .5, 1)), policy, 4)
    assert state["tasks"]["d"]["observed_groups"] == 0
    assert select_pool(tasks(plan), state, sampler, 5, policy=policy)[0]["task_id"] == "e"
    state = observe_batch(state, select_tasks(plan, ("e",)), rows(e=(.5,) * 3), policy, 5)
    assert select_pool(tasks(plan), state, sampler, 6, policy=policy)[0]["task_id"] == "d"
    state["tasks"]["d"]["cooldown_until"] = 9
    assert "d" not in {r["task_id"] for r in select_pool(tasks(plan), state, sampler, 6,
                        policy=policy, include_reserve=True)}


def test_priority_never_forces_waiting_after_enough_reward_varying_groups():
    from behave.train.pool import ReplenishingGroups

    groups = ReplenishingGroups(("priority",) * 8 + ("a",) * 8 + ("b",) * 8, 2, 3)
    for index in range(8, 24):
        groups.record(index, index % 2)
    assert groups.stop.is_set()
    assert groups.summary()["selected_groups"] == ["a", "b"]
    assert groups.admit(tuple(range(8)), 8) == ()
