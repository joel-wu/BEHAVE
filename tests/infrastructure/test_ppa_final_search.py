"""Final search uses public comparison before freezing the shared replay pool."""

from dataclasses import replace

import pytest

from behave.agent.attempt import AttemptSpec
from behave.ppa.finalize import compare_searches, finalized_report
from behave.ppa.performance import stimulus
from behave.ppa.prompting import render_objective
from behave.ppa.workflow import _union, run_search
from behave.task_execution.judge_policy import JudgePolicy
from test_ppa import FakeNative, experiment, fixture_task
from test_ppa_no_sim import NoSimBackend


def saved_no_sim(tmp_path):
    public, task, contract = fixture_task()
    backend = NoSimBackend(True)
    run_search(public=public, task=task, experiment=experiment(),
        spec=AttemptSpec("no-sim", "ppa", {"model": "fixture"}), backend=backend,
        native=FakeNative(), directory=tmp_path / "search", behavior_source="agent",
        sim_feedback=False, prompt=render_objective(100, sim_feedback=False))
    policy = replace(JudgePolicy.from_bytes(task.judge_policy), search_seconds=600)
    return replace(task, judge_policy=policy.to_bytes()), contract, backend


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("check_score", [0, 1, None])
def test_search_is_not_bypassed_or_overwritten_by_replay(tmp_path, workers, check_score):
    task, contract, backend = saved_no_sim(tmp_path)
    generated = stimulus(contract, replace(experiment().workloads[0], inputs=((213,),))).as_dict()
    calls = []

    class Native(FakeNative):
        def run(self, inputs, exp, mode, pool, objects):
            calls.append(mode)
            assert inputs.reference_behavior == task.reference_behavior
            assert inputs.reference_visible is False
            result = super().run(inputs, exp, mode, pool, objects)
            if mode == "check":
                assert inputs.replay_stimuli is None
                assert inputs.policy.search_seconds == 600
                result.update(score=check_score, pool=list(_union(pool, (generated,))))
            elif mode == "final":
                assert generated in pool
            return result

    native = Native()
    before = backend.step if hasattr(backend, "step") else backend.index
    options = dict(searches={"no-sim": tmp_path / "search"}, task=task,
                   experiment=experiment(), native=native, workers=(native,) * workers,
                   directory=tmp_path / "final")
    result = compare_searches(**options)
    assert calls == ["check", "final"]
    candidate, = result["candidates"].values()
    assert candidate["score"] == check_score
    assert candidate["verification"]["score"] == check_score
    assert [entry["mode"] for entry in candidate["history"]] == ["check", "final"]
    assert bool(result["frontiers"]["no-sim"]) == (check_score == 1)
    assert compare_searches(**options) == result
    assert calls == ["check", "final"]
    assert (backend.step if hasattr(backend, "step") else backend.index) == before


def test_recovery_keeps_search_pool_and_does_not_repeat_completed_check(tmp_path):
    task, contract, _backend = saved_no_sim(tmp_path)
    generated = stimulus(contract, replace(experiment().workloads[0], inputs=((213,),))).as_dict()
    calls = []

    class Native(FakeNative):
        stop = True

        def run(self, inputs, exp, mode, pool, objects):
            calls.append(mode)
            if mode == "final" and self.stop:
                raise RuntimeError("test interruption before replay")
            result = super().run(inputs, exp, mode, pool, objects)
            if mode == "check":
                result["pool"] = list(_union(pool, (generated,)))
            elif mode == "final":
                assert generated in pool
            return result

    native = Native()
    options = dict(searches={"no-sim": tmp_path / "search"}, task=task,
                   experiment=experiment(), native=native)
    with pytest.raises(RuntimeError, match="test interruption"):
        compare_searches(**options, directory=tmp_path / "stopped")
    native.stop = False
    result = compare_searches(**options, directory=tmp_path / "recovered",
                             reuse_final=tmp_path / "stopped")
    assert calls == ["check", "final", "final"]
    assert next(iter(result["candidates"].values()))["score"] == 1
    assert finalized_report(tmp_path / "recovered")[2] == result
