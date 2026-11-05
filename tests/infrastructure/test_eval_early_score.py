import json
import shutil
import socket
from types import SimpleNamespace

import pytest
from test_rejudge import local_rollout as fixture_rollout  # noqa: F401

from tools.eval_review_trial import write_json


def test_explicit_completed_arm_can_score_while_other_models_run(tmp_path):
    from tools.eval_campaign import score_directories

    write_json(tmp_path / "campaign.json", {"configuration": {"models": [{"id": "a"}, {"id": "b"}]},
        "runs": [{"name": "done", "model": "a"}, {"name": "active", "model": "b"}]})
    write_json(tmp_path / "runs/done/runtime.json", {"state": "archived"})
    write_json(tmp_path / "runs/active/runtime.json", {"state": "running"})
    assert score_directories(tmp_path, run_name="done") == [tmp_path / "runs/done"]
    with pytest.raises(ValueError, match="not finished"):
        score_directories(tmp_path, run_name="active")
    with pytest.raises(ValueError, match="not finished"):
        score_directories(tmp_path)
    with pytest.raises(ValueError, match="belong"):
        score_directories(tmp_path, run_name="../outside")


def test_queued_score_waits_for_archive_and_existing_retry(tmp_path, monkeypatch):
    from tools import eval_campaign as campaign

    write_json(tmp_path / "campaign.json", {"runs": [{"name": "active"}]})
    source = tmp_path / "runs/active"
    retry = source / "active_infra_retry"
    write_json(source / "runtime.json", {"state": "running"})
    write_json(retry / "selection.json", {"rollout": ["task"]})
    write_json(retry / "runtime.json", {"state": "running"})
    waiting = []

    def advance(_seconds):
        pending = (source, retry)[len(waiting)]
        waiting.append(pending)
        write_json(pending / "runtime.json", {"state": "archived"})

    monkeypatch.setattr(campaign, "Event", lambda: SimpleNamespace(wait=advance))
    assert campaign.score_directories(tmp_path, run_name="active", wait_ready=True) == [source]
    assert waiting == [source, retry]


def test_queued_unfinished_arm_requests_node_only_after_archive(tmp_path, monkeypatch):
    import os
    import sys

    from test_runtime_support import repository

    from behave.runtime import snapshots
    from tools import eval_campaign as campaign

    write_json(tmp_path / "campaign.json", {"runs": [{"name": "active"}]})
    write_json(tmp_path / "runs/active/runtime.json", {"state": "running"})
    source, _ = repository(tmp_path, monkeypatch)
    snapshots.freeze(source, tmp_path / "snapshots/score/checkout")
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda directory, argv, **kw:
                        {"directory": directory, "argv": argv, **kw})
    launch = campaign.submit(tmp_path, 123, phase="score", run_name="active", cpus=64, wait_ready=True)
    assert launch["metadata"]["wait_ready"] is True
    assert "--cpus-per-task=64" in launch["argv"]
    events = []
    monkeypatch.setattr(campaign, "score_directories", lambda root, **kw:
                        events.append(("ready", root, kw)))
    monkeypatch.setattr(os, "execvp", lambda name, args: events.append(("exec", name, args)))
    monkeypatch.setattr(sys, "argv", ["-c", *launch["argv"][5:]])
    exec(launch["argv"][4], {})  # noqa: S102 -- execute the emitted waiter; execvp is intercepted
    assert events[0] == ("ready", tmp_path, {"run_name": "active", "wait_ready": True})
    assert events[1][:2] == ("exec", "srun")
    assert str(tmp_path / "snapshots/score/checkout") in events[1][2]


def test_wait_ready_requires_a_known_explicit_scoring_arm(tmp_path):
    from tools import eval_campaign as campaign

    write_json(tmp_path / "campaign.json", {"runs": [{"name": "active"}]})
    with pytest.raises(ValueError, match="--phase score"):
        campaign.submit(tmp_path, 123, phase="hosted", run_name="active", wait_ready=True)
    with pytest.raises(ValueError, match="--run"):
        campaign.submit(tmp_path, 123, phase="score", wait_ready=True)
    with pytest.raises(ValueError, match="belong"):
        campaign.submit(tmp_path, 123, phase="score", run_name="unknown", wait_ready=True)
    with pytest.raises(ValueError, match="explicit"):
        campaign.score_directories(tmp_path, wait_ready=True)


def test_queued_score_waits_for_predecessor_before_requesting_node_resources(tmp_path, monkeypatch):
    import os
    import sys

    from test_runtime_support import repository

    from behave.runtime import jobs, snapshots
    from tools import eval_campaign as campaign

    source, _ = repository(tmp_path, monkeypatch)
    snapshots.freeze(source, tmp_path / "snapshots/score/checkout")
    monkeypatch.setattr(campaign, "score_directories", lambda *_a, **_k: [])
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda directory, argv, **kw:
                        {"directory": directory, "argv": argv, **kw})
    predecessor = tmp_path / "score_ready_previous_control"
    launch = campaign.submit(tmp_path, 123, phase="score", run_name="next", cpus=64, after=predecessor)
    assert launch["metadata"]["after"] == str(predecessor)
    assert "--cpus-per-task=64" in launch["argv"]
    code = launch["argv"][4]
    monkeypatch.setattr(sys, "argv", ["-c", *launch["argv"][5:]])
    events = []
    monkeypatch.setattr(jobs, "wait_control", lambda path: events.append(("wait", path)))
    monkeypatch.setattr(os, "execvp", lambda name, args: events.append(("exec", name, args)))
    exec(code, {})  # noqa: S102 -- execute the emitted waiter; execvp is intercepted
    assert events[0] == ("wait", predecessor)
    assert events[1][0:2] == ("exec", "srun")

    events.clear()

    def failed(_):
        raise RuntimeError("predecessor did not finish")

    monkeypatch.setattr(jobs, "wait_control", failed)
    with pytest.raises(RuntimeError, match="predecessor"):
        exec(code, {})  # noqa: S102 -- failing predecessor must prevent the intercepted execvp
    assert not events


def test_campaign_score_uses_current_baseline_and_skips_finished_score(tmp_path, monkeypatch):
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy
    from tools import eval_campaign as campaign

    original = JudgePolicy("old-command", "old-authority", search_seconds=123)
    (tmp_path / "release").mkdir()
    (tmp_path / "release/judge_policy.json").write_bytes(original.to_bytes())
    source, local = tmp_path / "runs/done", tmp_path / "local_original"
    local.mkdir()
    write_json(source / "runtime.json", {"state": "archived", "host": socket.gethostname(),
                                         "local_directory": str(local)})
    write_json(source / "settings.json", {"judge_workers": 50})
    monkeypatch.setattr(campaign, "score_directories", lambda *a, **kw: [source])
    monkeypatch.setenv("SLURM_CPUS_PER_TASK", "96")
    monkeypatch.setattr(campaign.os, "sched_getaffinity", lambda _: set(range(192)))

    class Pool:
        def __init__(self, capacity):
            assert capacity == 96
            self.endpoint = SimpleNamespace(as_dict=lambda: {"capacity": 96})

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def snapshot(self):
            return {}

    monkeypatch.setattr(api, "CpuPool", Pool)
    calls = []

    def score(output, name, **kw):
        policy = JudgePolicy.from_bytes(kw["judge_policy"].read_bytes())
        assert policy.command_version == api.COMMAND_VERSION and policy.authority_version == api.RESULT_VERSION
        assert policy.search_seconds == 0 and policy.analysis is None
        assert (policy.sequence_length, policy.baseline_cases) == (original.sequence_length, original.baseline_cases)
        assert kw["stage_from"] == local and kw["workers"] == 50
        calls.append(output)
        write_json(source / "run/judgments/baseline/runtime.json", {"state": "archived"})

    monkeypatch.setattr("behave.eval.scoring.score_review", score)
    campaign.score(tmp_path, run_name="done")
    campaign.score(tmp_path, run_name="done")
    assert calls == [source]


@pytest.mark.usefixtures("fixture_rollout")
def test_scoring_stages_from_local_rollout_and_publishes_only_new_results(tmp_path, publication, monkeypatch):
    from test_eval_score_saved import saved_run

    from behave.core.storage import DirectoryStore
    from behave.task_execution.rejudge import judgment_run
    from behave.task_execution.run_store import RunStore
    from tools.eval_review_trial import score

    local, _, original = saved_run(tmp_path, publication, monkeypatch)
    archive = tmp_path / "archive"
    shutil.copytree(local, archive)
    new, published = [], []
    objects = archive / "run/objects"
    before = {path.name: path.read_bytes() for path in objects.iterdir()}
    publish = DirectoryStore.publish

    def record_publish(self, source, refs, **kwargs):
        if self.directory == objects:
            published.extend(refs)
        return publish(self, source, refs, **kwargs)

    def rejudge(*, directory, name, **kw):
        assert kw["reader"].authority.directory == local / "inputs"
        assert kw["reader"].read(publication.task.spec.content) == publication.reader.read(publication.task.spec.content)
        assert len(kw["selection"]) == 3
        run = RunStore(directory, objects=kw["objects"])
        new.append(run.objects.put(b"new judgment"))
        judgment_run(run, name).directory.mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(summary={})

    monkeypatch.setattr("behave.task_execution.rejudge.rejudge_batch", rejudge)
    monkeypatch.setattr(DirectoryStore, "publish", record_publish)
    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", dict)
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "batch-runtime"))
    score(archive, "baseline", stage_from=local, workers=2, retry_testbench=False)
    # Sealing an existing report can materialize its head, not its history.
    # Publication reuses that immutable head and writes only the new judgment.
    assert set(published) == {*new, original.head}
    assert {path.name for path in objects.iterdir()} - before.keys() == {ref.sha256 for ref in new}
    assert all((objects / name).read_bytes() == data for name, data in before.items())
    assert RunStore(archive / "run").objects.read(new[0]) == b"new judgment"
    assert json.loads((archive / "run/judgments/baseline/runtime.json").read_text())["state"] == "archived"
