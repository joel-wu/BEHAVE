"""Training orchestration: native phases and durable outputs before completion."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_slime_stage import native_worker
from test_slime_training_plan import inputs  # noqa: F401

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.train.slime_launch import logged_arguments
from behave.train.slime_stage import load_learning_report, run_learning
from tools.eval_review_trial import write_json
from tools.slime_training_job import execute, submit


def test_local_staging_failure_is_reported_as_interrupted(tmp_path, monkeypatch):
    from behave.train import jobs as slime_training_job

    local = tmp_path / 'local'
    local.mkdir()
    output = tmp_path / 'job'
    output.mkdir()
    monkeypatch.setattr(slime_training_job, 'mkdtemp', lambda **kw: str(local))

    def fail(*args):
        raise RuntimeError('snapshot identity mismatch')

    monkeypatch.setattr('behave.runtime.snapshots.stage', fail)
    monkeypatch.setattr('behave.runtime.snapshots.install_runtime',
                        lambda *a: pytest.fail('must not install or start a trainer'))
    with pytest.raises(RuntimeError, match='snapshot identity mismatch'):
        slime_training_job._staged(output, {'native_python': sys.executable}, {})
    state = json.loads((output / 'runtime.json').read_text())
    assert state['state'] == 'interrupted'
    assert 'snapshot identity mismatch' in state['error'] and state['finished_at']
    assert not (output / 'artifacts/installation').exists()


def test_hf_report_parent_uses_latest_export_not_newer_than_native_save(tmp_path):
    from tools.slime_training_job import hf_comparison_parent

    for step in (4, 9, 19):
        (tmp_path / f"artifacts/plan/hf_{step}").mkdir(parents=True)
    assert hf_comparison_parent(tmp_path, 10) == tmp_path / "artifacts/plan/hf_9"
    assert hf_comparison_parent(tmp_path, 9) == tmp_path / "artifacts/plan/hf_9"


def test_hf_report_parent_survives_interruption_before_next_hf_milestone(tmp_path):
    from tools.slime_training_job import hf_comparison_parent

    old_hf = tmp_path / "previous/artifacts/plan/hf_9"
    old_hf.mkdir(parents=True)
    previous = tmp_path / "interrupted"
    write_json(previous / "continuation.json", {"hf_parent": str(old_hf)})
    assert hf_comparison_parent(previous, 10) == old_hf


def test_hf_report_parent_before_first_milestone_is_configured_base(tmp_path):
    from tools.slime_training_job import hf_comparison_parent

    base = tmp_path / "base"
    base.mkdir()
    write_json(tmp_path / "configuration.json", {"model_path": str(base)})
    assert hf_comparison_parent(tmp_path, 0) == base


def test_new_final_eval_task_is_rejected_before_job_submission(tmp_path, monkeypatch):
    from tools.dataset_splits import current_path

    task = (current_path() / "eval.txt").read_text().split()[0]
    stale = tmp_path / "old_training.txt"
    stale.write_text(task + "\n")
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("dispatch"))
    with pytest.raises(ValueError, match="current evaluation tasks"):
        submit(tmp_path / "job", 27419, tmp_path / "absent_config", tmp_path / "release",
               [task], stale, cpus=1)
    assert not (tmp_path / "job").exists()


def test_tasks_file_reuses_explicit_selection_entry(tmp_path, monkeypatch):
    from tools import slime_training_job

    selection = tmp_path / 'tasks.txt'
    selection.write_text('task_a\ntask_b\n')
    calls = []
    monkeypatch.setattr('behave.cli.training_job.submit', lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(sys, 'argv', ['slime_training_job', 'output', '--allocation', '27419',
        '--configuration', 'runtime.json', '--release', 'release', '--train-split', 'train.txt',
        '--tasks-file', str(selection), '--cpus', '90'])
    slime_training_job.main()
    assert calls[0][0][4] == ['task_a', 'task_b']
    assert calls[0][0][5] == Path('train.txt') and calls[0][1] == {'cpus': 90, 'repository': Path(__file__).resolve().parents[2]}


def test_cli_uses_the_same_entry_for_update_target_seed_and_resume(monkeypatch):
    from tools import slime_training_job

    calls = []
    monkeypatch.setattr("behave.cli.training_job.submit", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(sys, "argv", ["slime_training_job", "output", "--allocation", "29701",
        "--configuration", "runtime.json", "--release", "release", "--train-split", "train.txt",
        "--task", "task_a", "--cpus", "144", "--updates", "50", "--training-seed", "20260918",
        "--resume-from", "previous"])
    slime_training_job.main()
    assert calls[0][1] == {"repository": Path(__file__).resolve().parents[2], "cpus": 144, "updates": 50, "training_seed": 20260918,
                           "resume_from": Path("previous")}


def test_job_configuration_overrides_are_resolved_once_without_editing_profile(tmp_path):
    from tools.slime_training_job import job_configuration

    profile = Path(__file__).resolve().parents[2] / "infra/train/qwen27b_acceptance.json"
    path = tmp_path / "configuration.json"
    path.write_bytes(profile.read_bytes())
    before = path.read_bytes()
    config = job_configuration(path, updates=40, training_seed=20260920)
    assert (config["initial_updates"], config["resume_updates"]) == (40, 0)
    assert config["sampler"]["seed"] == "20260920"
    assert config["native_arguments"][config["native_arguments"].index("--seed") + 1] == "20260920"
    assert "seed" not in config["sampling"]
    assert path.read_bytes() == before
    assert config["sampling"] == json.loads(before)["sampling"]


@pytest.mark.parametrize("updates", [0, -1, True, 1.5])
def test_invalid_update_target_is_rejected_before_creating_job(tmp_path, inputs, monkeypatch, updates):
    configuration, release, split, task_id = inputs
    path = tmp_path / "configuration.json"
    write_json(path, configuration)
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **k: pytest.fail("dispatch"))
    with pytest.raises(ValueError, match="positive absolute"):
        submit(tmp_path / "job", 29701, path, release, [task_id], split, cpus=144, updates=updates)
    assert not (tmp_path / "job").exists()


@pytest.mark.parametrize("missing", ["--configuration", "--train-split"])
def test_training_job_requires_explicit_runtime_and_selection(monkeypatch, capsys, missing):
    from tools import slime_training_job

    options = {"--allocation": "27419", "--configuration": "runtime.json", "--train-split": "train.txt",
               "--release": "release", "--task": "task", "--cpus": "180"}
    monkeypatch.setattr("sys.argv", ["slime_training_job", "output", *(item for pair in options.items()
                        if pair[0] != missing for item in pair)])
    monkeypatch.setattr("behave.cli.training_job.submit", lambda *a, **k: pytest.fail("implicit training launch"))
    with pytest.raises(SystemExit) as error:
        slime_training_job.main()
    assert error.value.code == 2 and missing in capsys.readouterr().err


def test_native_log_redirection_keeps_large_output_out_of_the_supervisor(tmp_path):
    stdout, stderr = tmp_path / "stdout", tmp_path / "stderr"
    # This command exercises only the redirection bootstrap, not native training.
    args = logged_arguments(sys.executable, "json.tool", (), stdout, stderr)
    result = subprocess.run(args, input=b'{"hello": "world"}', capture_output=True)
    assert result.returncode == 0 and result.stdout == result.stderr == b""
    assert json.loads(stdout.read_text()) == {"hello": "world"}
    assert stderr.read_bytes() == b""
    result = subprocess.run(args, input=b"{}", capture_output=True)
    assert result.returncode != 0  # A second invocation cannot overwrite a phase's evidence.
    assert json.loads(stdout.read_text()) == {"hello": "world"}


def test_logged_module_preserves_multiprocessing_spawn_entry(tmp_path):
    module = tmp_path / "spawn_probe.py"
    module.write_text(
        "import multiprocessing as mp\n"
        "def worker():\n"
        "    print('spawned-worker', flush=True)\n"
        "if __name__ == '__main__':\n"
        "    child = mp.get_context('spawn').Process(target=worker)\n"
        "    child.start()\n"
        "    child.join()\n"
        "    raise SystemExit(child.exitcode)\n"
    )
    stdout, stderr = tmp_path / "stdout", tmp_path / "stderr"
    args = logged_arguments(sys.executable, "spawn_probe", (), stdout, stderr)
    result = subprocess.run(args, cwd=tmp_path, capture_output=True, timeout=20)
    assert result.returncode == 0, stderr.read_text()
    assert result.stdout == result.stderr == b""
    assert stdout.read_text() == "spawned-worker\n"


def test_detached_supervisor_can_use_the_allocation_lifetime(tmp_path, monkeypatch):
    from behave.runtime import jobs as supervisor

    calls = []

    class Process:
        pid = 42

        def wait(self, *, timeout):
            calls.append(timeout)
            return 0

    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *a, **k: Process())
    directory = tmp_path / "control"
    supervisor.submit(directory, ["/bin/true"], cwd=tmp_path, wall_seconds=None, metadata={})
    supervisor.supervise(directory)
    assert calls and all(timeout > 0 for timeout in calls)
    assert json.loads((directory / "launch.json").read_text())["wall_seconds"] is None
    assert json.loads((directory / "status.json").read_text())["state"] == "completed"


def test_node_heartbeat_survives_without_login_supervisor(tmp_path):
    import time

    from tools.dataset_validation_job import node_heartbeat

    path = tmp_path / "node_status.json"
    with node_heartbeat(path, interval=.01):
        first = json.loads(path.read_text())
        for _ in range(100):
            current = json.loads(path.read_text())
            if current["heartbeat_at"] != first["heartbeat_at"]:
                break
            time.sleep(.01)
        assert current["heartbeat_at"] > first["heartbeat_at"]
        assert current["state"] == "live"
    assert json.loads(path.read_text())["state"] == "stopped"


def test_monitor_write_failure_does_not_change_training_result(tmp_path, monkeypatch, capsys):
    from behave.runtime import jobs as supervisor

    def unavailable(*args):
        raise OSError("fixture storage unavailable")

    monkeypatch.setattr(supervisor, "_write", unavailable)
    with supervisor.node_heartbeat(tmp_path / "heartbeat.json", interval=1):
        pass
    assert "fixture storage unavailable" in capsys.readouterr().err


def test_old_heartbeat_is_explicitly_overdue_not_a_training_failure(tmp_path):
    from tools.dataset_validation_job import status_of

    write_json(tmp_path / "launch.json", {"metadata": {}, "heartbeat_seconds": 60})
    write_json(tmp_path / "status.json", {
        "state": "supervising", "heartbeat_at": "2000-01-01T00:00:00+00:00"})
    result = status_of(tmp_path)
    assert result["heartbeat_overdue"] is True and result["heartbeat_age_seconds"] > 60
    assert result["state"] == "supervising"  # Historical report, not inferred process failure.
    assert result["validation_passed"] is None


@pytest.mark.parametrize("cleanup_seconds", [None, 37])
def test_detached_supervisor_uses_explicit_cleanup_wait(tmp_path, monkeypatch, cleanup_seconds):
    from behave.runtime import jobs as supervisor

    waits, signals = [], []

    class Process:
        pid = 42

        def wait(self, *, timeout):
            waits.append(timeout)
            return -15

        def poll(self):
            return -15

    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(supervisor.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(supervisor.time, "monotonic", iter([0, 2, 2, 2, 3]).__next__)
    directory = tmp_path / "control"
    supervisor.submit(directory, ["/bin/true"], cwd=tmp_path, wall_seconds=1,
                      metadata={}, cleanup_seconds=cleanup_seconds)
    supervisor.supervise(directory)
    assert json.loads((directory / "launch.json").read_text())["cleanup_seconds"] == cleanup_seconds
    assert waits == [cleanup_seconds]
    assert len(signals) == 1
    status = json.loads((directory / "status.json").read_text())
    assert status["state"] == "timed-out" and status["cleanup_state"] == "complete"


@pytest.mark.parametrize("cleanup_seconds", [None, 37])
def test_training_submit_selects_the_existing_allocation_without_a_private_wall_cap(
        tmp_path, inputs, monkeypatch, cleanup_seconds):
    configuration, release, split, task_id = inputs
    configuration["cleanup_seconds"] = cleanup_seconds
    path = tmp_path / "configuration.json"
    write_json(path, configuration)
    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **kw: {})
    seen = {}

    def detached(directory, argv, **kwargs):
        seen.update(directory=directory, argv=argv, **kwargs)
        return {"submitted": True}

    monkeypatch.setattr("behave.runtime.jobs.submit", detached)
    assert submit(tmp_path / "job", 27419, path, release, [task_id], split, cpus=180) == {"submitted": True}
    assert seen["wall_seconds"] is None
    assert seen["cleanup_seconds"] == cleanup_seconds
    assert "--gpus=8" in seen["argv"] and "--cpus-per-task=180" in seen["argv"]
    assert not any(arg.startswith("--time=") for arg in seen["argv"])
    with pytest.raises(ValueError, match="training split"):
        submit(tmp_path / "heldout", 27419, path, release, ["heldout"], split, cpus=180)
    assert not (tmp_path / "heldout").exists()


def test_stage_setup_failure_publishes_error_and_retains_installation_log(tmp_path, monkeypatch):
    from behave.train import jobs as slime_training_job

    output, local = tmp_path / "job", tmp_path / "local"
    output.mkdir()
    local.mkdir()
    write_json(output / "configuration.json", {"native_python": "/native/python"})
    write_json(output / "selection.json", {"cpus": 90})
    monkeypatch.setattr("behave.core.resources.allocated_cpus", lambda cpus: (1, 3, cpus))
    affinities = []
    monkeypatch.setattr(slime_training_job.os, "sched_setaffinity", lambda pid, cpus: affinities.append((pid, cpus)))
    monkeypatch.setattr(slime_training_job, "mkdtemp", lambda **kw: str(local))
    monkeypatch.setattr("behave.runtime.snapshots.stage", lambda snapshot, code: code.mkdir())
    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit",
                        lambda limit: {"previous_soft": 300, "soft": -1, "hard": -1})

    def fail_install(code, directory, native_python):
        (directory / "installation.log").write_text("fixture locked export failure\n")
        raise RuntimeError("locked export failed")

    monkeypatch.setattr("behave.runtime.snapshots.install_runtime", fail_install)
    with pytest.raises(RuntimeError, match="locked export failed"):
        slime_training_job.staged(str(output))
    state = json.loads((output / "runtime.json").read_text())
    assert state["state"] == "interrupted" and state["elapsed_seconds"] >= 0
    assert "locked export failed" in state["error"]
    assert (output / "artifacts/installation/installation.log").read_text() == "fixture locked export failure\n"
    assert affinities == [(0, (1, 3, 90))]


@pytest.fixture
def job(tmp_path, inputs, monkeypatch):
    configuration, release, split, task_id = inputs
    output, local = tmp_path / "job", tmp_path / "local"
    output.mkdir()
    local.mkdir()
    write_json(output / "configuration.json", configuration)
    write_json(output / "selection.json", {"tasks": [task_id], "release": str(release), "cpus": 180})
    (output / "train.txt").write_bytes(split.read_bytes())
    write_json(output / "artifacts/installation/installed.json", {"native_python": sys.executable})
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1,2,3,4,5,6,7")
    monkeypatch.setattr("behave.train.slime_launch.environment", lambda *a, **k: {"fixture": "native"})
    monkeypatch.setattr("behave.train.slime_checkpoint.scored_rounds", lambda _: [{"round": i} for i in range(3)])
    # Preserve fixture-generated scratch inside pytest's own temporary directory.
    monkeypatch.setattr("behave.train.jobs.mkdtemp", lambda **k: str(tmp_path / "native-cache"))
    (tmp_path / "native-cache").mkdir()
    calls = []

    def service(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["deadline"] is None and kwargs["network"] == "none"
        assert callable(kwargs["cancelled"])
        result = SimpleNamespace(status="exited", returncode=0, cleanup_state=CleanupState.COMPLETE,
                                 elapsed_seconds=1., stdout=b"", stderr=b"")
        if "--num-rollout" in argv:
            final = int(argv[argv.index("--num-rollout") + 1]) - 1
            plan = output / "artifacts/plan"
            phase = "initial" if final == 1 else "resume"
            stage = plan / "learning" / phase
            (stage / "stdout").write_text("step 0: {'train/step': 0, 'train/grad_norm': 1.0}\n")
            (stage / "stderr").write_text("")
            hf = Path(argv[argv.index("--save-hf") + 1].format(rollout_id=final))
            hf.mkdir()
            (hf / "weight.safetensors").write_bytes(b"fixture weight")
            options = {"plan": plan, "load": Path(argv[argv.index("--load") + 1]),
                "save": Path(argv[argv.index("--save") + 1]), "updates": final + 1}
            return native_worker(options, configuration)(argv, **kwargs)
        else:
            result.stdout = json.dumps([{"changed_elements": i + 1}
                                       for i in range(len(argv[4:]) - 1)]).encode()
        return result

    monkeypatch.setattr("behave.isolation.service.run_service", service)
    monkeypatch.setattr("behave.train.slime_stage.run_service", service)
    return output, local, calls


@pytest.mark.parametrize("explicit", [False, True])
def test_megatron_checkout_reaches_native_environment(job, monkeypatch, explicit):
    output, local, _ = job
    config = json.loads((output / "configuration.json").read_text())
    expected = Path(config["slime_source"]).parent / "megatron-upstream"
    if explicit:
        expected = output / "independent-megatron-source"
        config["megatron_source"] = str(expected)
        write_json(output / "configuration.json", config)
    seen = []

    def capture_environment(*args, **kwargs):
        seen.append(kwargs["megatron_source"])
        return {"fixture": "native"}

    monkeypatch.setattr("behave.train.slime_launch.environment", capture_environment)
    assert execute(str(output), str(local)) == 0
    assert seen == [expected]


def test_zero_resume_updates_calls_same_learning_entry_once(job, monkeypatch):
    output, local, calls = job
    config = json.loads((output / 'configuration.json').read_text())
    config['resume_updates'] = 0
    write_json(output / 'configuration.json', config)
    monkeypatch.setattr('behave.train.slime_checkpoint.scored_rounds',
                        lambda _: [{'round': i} for i in range(config['initial_updates'])])
    assert execute(str(output), str(local)) == 0
    assert len(calls) == 2  # One native stage and the existing numerical report.
    assert calls[0][0][calls[0][0].index('--num-rollout') + 1] == '2'
    assert not (output / 'artifacts/plan/learning/resume').exists()
    assert len(calls[1][0][4:]) == 2
    result = json.loads((output / 'artifacts/plan/result.json').read_text())
    assert result['native_cycle_completed'] is True
    assert result['native_updates_verified'] is True
    assert result['native_update_and_cold_resume'] is False
    assert (output / 'artifacts/final_hf').resolve() == output / 'artifacts/plan/hf_1'


@pytest.mark.parametrize("milestones", [None, [5]])
def test_saved_job_continues_through_the_same_native_entry(job, tmp_path, monkeypatch, milestones):
    previous, local, _ = job
    assert execute(str(previous), str(local)) == 0
    native = JournalDirectory(previous / "artifacts/plan/rounds/native")
    saved = native.journal("rounds").load()[-1]
    resumed = tmp_path / "continued"
    next_local = tmp_path / "continued-local"
    next_local.mkdir()
    config = json.loads((previous / "configuration.json").read_text())
    config.update(initial_updates=5, resume_updates=0, checkpoint_interval=1, hf_checkpoint_interval=5)
    if milestones is not None:
        config["hf_checkpoint_updates"] = milestones
    config["sampler"] = {"order": "shuffle", "tasks_per_update": 1, "seed": "fixture"}
    write_json(resumed / "configuration.json", config)
    for name in ("selection.json", "train.txt"):
        (resumed / name).write_bytes((previous / name).read_bytes())
    write_json(resumed / "artifacts/installation/installed.json", {"native_python": sys.executable})
    write_json(resumed / "continuation.json", {"first_update": 3,
        "objects": str(native.objects.directory), "checkpoint": BlobRef.of(saved.to_bytes()).as_dict(),
        "load": str(previous / "artifacts/checkpoint"), "hf_parent": str(previous / "artifacts/plan/hf_2")})
    monkeypatch.setattr("behave.train.slime_checkpoint.scored_rounds", lambda _: [{"round": i} for i in (3, 4)])
    calls = []

    def service(argv, **kwargs):
        calls.append(argv)
        if "--num-rollout" not in argv:
            return SimpleNamespace(status="exited", returncode=0, cleanup_state=CleanupState.COMPLETE,
                                   stdout=b'[{"changed_elements": 1}]', stderr=b'')
        assert argv[argv.index("--load") + 1] == str(previous / "artifacts/checkpoint")
        assert argv[argv.index("--num-rollout") + 1] == "5"
        assert argv[argv.index("--save-interval") + 1] == "1"
        assert argv[argv.index("--behave-hf-checkpoint-interval") + 1] == "5"
        if milestones is not None:
            assert argv[argv.index("--behave-hf-checkpoint-updates") + 1] == "5"
        plan = resumed / "artifacts/plan"
        stage = plan / "learning/initial"
        (stage / "stdout").write_text("step 4: {'train/step': 4, 'train/grad_norm': 1.0}\n")
        (stage / "stderr").write_text("")
        (plan / "hf_4").mkdir()
        (plan / "hf_4/weight.safetensors").write_bytes(b"updated weights")
        return native_worker({"plan": plan, "load": previous / "artifacts/checkpoint",
            "save": resumed / "artifacts/checkpoint", "updates": 5}, config)(argv, **kwargs)

    monkeypatch.setattr("behave.isolation.service.run_service", service)
    monkeypatch.setattr("behave.train.slime_stage.run_service", service)
    assert execute(str(resumed), str(next_local)) == 0
    report = json.loads((resumed / "artifacts/plan/initial.result.json").read_text())["report"]
    assert (report["first_update"], report["stop_update"]) == (3, 5)
    assert len(calls) == 2  # One native continuation, then weight comparison.
    assert json.loads((resumed / "artifacts/plan/result.json").read_text())["native_update_and_cold_resume"]
    assert (previous / "artifacts/checkpoint/iter_0000002/state.pt").exists()


def test_resume_submission_preserves_frozen_code(job, tmp_path, monkeypatch):
    previous, local, _ = job
    assert execute(str(previous), str(local)) == 0
    selection = json.loads((previous / "selection.json").read_text())
    original = previous
    previous = tmp_path / "paused"
    native = JournalDirectory(original / "artifacts/plan/rounds/native")
    paused = JournalDirectory(previous / "artifacts/plan/rounds/native")
    events = []
    for event in native.journal("rounds").load():
        paused.objects.publish_owned(native.objects, event.artifacts)
        events.append(paused.journal("rounds").append(events, event.kind, dict(event.payload), event.artifacts))
    saved = events[-1]
    paused.journal("rounds").append(events, "round_started", {"round": 3})
    write_json(previous / "continuation.json", {"hf_parent": str(original / "artifacts/plan/hf_2")})
    assert not (paused.objects.directory / BlobRef.of(saved.to_bytes()).sha256).exists()
    freezes, dispatches = [], []
    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **k: freezes.append((a, k)))
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **k: dispatches.append((a, k)))
    target = tmp_path / "submitted"
    submit(target, 27421, original / "configuration.json", Path(selection["release"]), selection["tasks"],
           original / "train.txt", cpus=90, resume_from=previous)
    assert freezes[0][0][0] == previous / "snapshot/checkout"
    assert freezes[0][1]["overrides"] == {}
    continuation = json.loads((target / "continuation.json").read_text())
    assert continuation["first_update"] == 3
    assert continuation["load"] == str(original / "artifacts/checkpoint")
    assert continuation["hf_parent"] == str(original / "artifacts/plan/hf_2")
    assert continuation["checkpoint"] == BlobRef.of(saved.to_bytes()).as_dict()
    assert not (paused.objects.directory / BlobRef.of(saved.to_bytes()).sha256).exists()
    assert len(dispatches) == 1
    assert "--overlap" not in dispatches[0][0][1]


def test_resume_target_is_absolute_and_preserves_the_parent_checkpoint(job, tmp_path, monkeypatch):
    previous, local, _ = job
    assert execute(str(previous), str(local)) == 0
    selection = json.loads((previous / "selection.json").read_text())
    dispatches = []
    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **k: {})
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **k: dispatches.append((a, k)))
    arguments = (29701, previous / "configuration.json", Path(selection["release"]),
                 selection["tasks"], previous / "train.txt")
    for target in (2, 3):
        rejected = tmp_path / f"rejected-{target}"
        with pytest.raises(ValueError, match="parent's completed"):
            submit(rejected, *arguments, cpus=144, resume_from=previous, updates=target)
        assert not rejected.exists()
    output = tmp_path / "next"
    submit(output, *arguments, cpus=144, resume_from=previous, updates=5)
    config = json.loads((output / "configuration.json").read_text())
    assert (config["initial_updates"], config["resume_updates"]) == (5, 0)
    continuation = json.loads((output / "continuation.json").read_text())
    assert continuation["first_update"] == 3
    assert continuation["load"] == str(previous / "artifacts/checkpoint")
    assert (previous / "artifacts/checkpoint/iter_0000002/state.pt").is_file()
    assert len(dispatches) == 1


def inherited_native_job(job, tmp_path):
    original, local, _ = job
    assert execute(str(original), str(local)) == 0
    native = JournalDirectory(original / "artifacts/plan/rounds/native")
    events = native.journal("rounds").load()
    for event in events:
        native.objects.put(event.to_bytes())
    saved = next(e for e in reversed(events) if e.kind == "checkpoint_saved")
    checkpoint = BlobRef.of(saved.to_bytes()).as_dict()
    previous = tmp_path / "interrupted_before_first_update"
    write_json(previous / "continuation.json", {"job": str(original), "checkpoint": checkpoint,
        "objects": str(native.objects.directory), "load": saved.payload["native_directory"],
        "first_update": saved.payload["round"] + 1, "hf_parent": str(original / "artifacts/plan/hf_2")})
    write_json(previous / "artifacts/plan/binding.json", {"parent_checkpoint": checkpoint})
    return original, previous, checkpoint


def test_restart_before_first_update_keeps_inherited_native_and_feedback(job, tmp_path, monkeypatch):
    original, previous, checkpoint = inherited_native_job(job, tmp_path)
    selection = json.loads((original / "selection.json").read_text())
    config = json.loads((original / "configuration.json").read_text())
    config["self_improvement"] = {"study": str(tmp_path / "existing_study.json")}
    write_json(previous / "configuration.json", config)
    freezes, dispatches, feedback = [], [], []
    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **k: freezes.append((a, k)))
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **k: dispatches.append((a, k)))
    monkeypatch.setattr("behave.train.slime_acquisition.retain_legacy_feedback_bindings",
                        lambda parent, *a: feedback.append(parent))
    output = tmp_path / "resumed_inherited_native"
    submit(output, 123, previous / "configuration.json", Path(selection["release"]),
        selection["tasks"], original / "train.txt", cpus=144, resume_from=previous, updates=60)
    result = json.loads((output / "continuation.json").read_text())
    assert result["checkpoint"] == checkpoint
    assert result["first_update"] == 3
    assert result["load"] == str(original / "artifacts/checkpoint")
    assert feedback == [original]
    assert freezes[0][0][0] == previous / "snapshot/checkout"
    assert len(dispatches) == 1
    assert not list((previous / "artifacts/plan/rounds/native/rounds").glob("*.json"))


@pytest.mark.parametrize("damage", ["checkpoint", "first_update", "load"])
def test_inherited_native_resume_refuses_mismatched_identity(job, tmp_path, damage):
    from tools.slime_training_job import native_resume_prefix
    _, previous, _ = inherited_native_job(job, tmp_path)
    path = previous / "continuation.json"
    value = json.loads(path.read_text())
    value[damage] = None if damage != "first_update" else 4
    write_json(path, value)
    with pytest.raises(ValueError, match="inherited native checkpoint"):
        native_resume_prefix(previous)


@pytest.mark.parametrize('damage',[None,'checkpoint','cycle'])
def test_repeated_pre_update_restart_resolves_original_feedback_owner(job,tmp_path,damage):
    from tools.slime_training_job import native_resume_prefix
    original,previous,checkpoint=inherited_native_job(job,tmp_path)
    first=json.loads((previous/'continuation.json').read_text())
    newest=tmp_path/'second_pre_update_restart'
    inherited_binding={**json.loads((original/'artifacts/plan/binding.json').read_text()),
                       'schema':'behave.slime-rollout-binding/3','parent_checkpoint':checkpoint}
    write_json(previous/'artifacts/plan/binding.json',inherited_binding)
    write_json(newest/'continuation.json',{**first,'job':str(previous)})
    write_json(newest/'artifacts/plan/binding.json',inherited_binding)
    if damage:
        first['job']=str(newest) if damage=='cycle' else first['job']
        if damage=='checkpoint':first['checkpoint']=None
        write_json(previous/'continuation.json',first)
        with pytest.raises(ValueError,match='inherited feedback'):
            native_resume_prefix(newest)
    else:
        result=native_resume_prefix(newest)
        assert result['feedback_job']==original and result['completed']==3
        assert result['load']==str(original/'artifacts/checkpoint')


def test_real_job_writes_all_saved_states_to_shared_storage(job, monkeypatch):
    output, local, calls = job
    monkeypatch.setattr("shutil.rmtree",
                        lambda *a, **k: pytest.fail("training must not recursively delete scratch"))
    assert execute(str(output), str(local)) == 0
    assert len(calls) == 3
    for (args, _), expected in zip(calls[:2], (local / "weights", output / "artifacts/checkpoint"), strict=True):
        assert args[args.index("--load") + 1] == str(expected)
    assert [args[args.index("--num-rollout") + 1] for args, _ in calls[:2]] == ["2", "3"]
    assert (output / "artifacts/checkpoint/iter_0000002/state.pt").read_bytes() == b"opaque worker state 2"
    assert (output / "artifacts/checkpoint/iter_0000001").exists()
    assert (output / "artifacts/plan/hf_1").exists()
    assert (output / "artifacts/final_hf/weight.safetensors").exists()
    assert (output / "artifacts/final_hf").resolve().is_relative_to(output.resolve())
    result = json.loads((output / "artifacts/plan/result.json").read_text())
    assert result["native_update_and_cold_resume"] is True
    assert result["full_128k_capacity_verified"] is False
    runtime = json.loads((output / "runtime.json").read_text())
    assert runtime["state"] == "completed" and runtime["local_scratch_removed"] is False
    assert local.exists() and all(Path(path).exists() for path in runtime["retained_scratch"])
    assert runtime["scratch_release"]["state"] == "released"
    assert not (local / "weights").exists()
    staged = json.loads((output / "artifacts/staged_model.json").read_text())
    assert staged["state"] == "released" and Path(staged["source"]).is_dir()
    plan = output / "artifacts/plan"
    assert not (local / "plan").exists() and not (local / "checkpoints").exists()
    binding = json.loads((plan / "binding.json").read_text())
    assert Path(binding["run_directory"]).is_relative_to(output)
    assert Path(binding["objects"]).is_relative_to(output)
    assert json.loads((plan / "configuration.json").read_text())["native_python"] == sys.executable
    for args, kwargs in calls[:2]:
        assert "--custom-megatron-before-train-step-hook-path" not in args
        assert "BEHAVE_TRAIN_TIMING_DIRECTORY" not in kwargs["environment"]
    monkeypatch.setattr("behave.train.slime_stage.run_service", lambda *a, **k: pytest.fail("repeated update"))
    for phase, first, stop in (("initial", 0, 2), ("resume", 2, 3)):
        receipt = json.loads((output / f"artifacts/plan/{phase}.result.json").read_text())
        head = BlobRef.from_dict(receipt["head"])
        reader = DirectoryStore(output / f"artifacts/plan/learning/{phase}/objects")
        assert load_learning_report(reader, head) == receipt["report"]
        assert (receipt["report"]["first_update"], receipt["report"]["stop_update"]) == (first, stop)
        # The job actually used the common stage, including its recovery journal.
        _, kwargs = calls[0 if phase == "initial" else 1]
        report = run_learning(plan=plan, run=JournalDirectory(plan / "learning" / phase), updates=stop,
            load=local / "weights" if phase == "initial" else output / "artifacts/checkpoint",
            save=output / "artifacts/checkpoint",
            cpus=180, environment=kwargs["environment"], cleanup_seconds=kwargs["cleanup_seconds"])
        assert report == receipt["report"]


def test_training_job_phase_timing_is_explicit_and_saved_on_shared_storage(job):
    output, local, calls = job
    config_path = output / "configuration.json"
    config = json.loads(config_path.read_text())
    config.update(phase_timing=True, timing_microbatches=3)
    write_json(config_path, config)
    assert execute(str(output), str(local)) == 0
    for args, kwargs in calls[:2]:
        assert args[args.index("--custom-megatron-before-train-step-hook-path") + 1] == "behave.train.slime_hooks.timing_before_step"
        assert kwargs["environment"]["BEHAVE_TRAIN_TIMING_DIRECTORY"] == str(output / "artifacts/timing")
        assert kwargs["environment"]["BEHAVE_TRAIN_TIMING_MICROBATCHES"] == "3"
    prepared = json.loads((output / "artifacts/plan/configuration.json").read_text())
    assert prepared["phase_timing"] is True and prepared["timing_microbatches"] == 3


def test_native_failure_retains_evidence_and_does_not_start_a_resume(job, monkeypatch):
    output, local, calls = job

    def fail(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(status="exited", returncode=1, cleanup_state=CleanupState.COMPLETE,
                               elapsed_seconds=1., stdout=b"", stderr=b"native error")

    monkeypatch.setattr("behave.isolation.service.run_service", fail)
    monkeypatch.setattr("behave.train.slime_stage.run_service", fail)
    with pytest.raises(RuntimeError, match="initial"):
        execute(str(output), str(local))
    assert len(calls) == 1
    assert (output / "artifacts/plan/initial.result.json").exists()
    assert not (output / "artifacts/final_hf").exists()
    assert json.loads((output / "runtime.json").read_text())["state"] == "interrupted"


def test_completed_phase_survives_later_failure_and_loss_of_local_working_path(job, monkeypatch):
    output, local, _ = job
    from behave.isolation import service

    original = service.run_service

    def fail_resume(argv, **kwargs):
        if "--num-rollout" in argv and argv[argv.index("--num-rollout") + 1] == "3":
            # The first phase is durable before this later process settles.
            assert (output / "artifacts/plan/initial.result.json").is_file()
            assert (output / "artifacts/checkpoint/iter_0000001/state.pt").is_file()
            assert (output / "artifacts/plan/hf_1/weight.safetensors").is_file()
            return SimpleNamespace(status="exited", returncode=1, cleanup_state=CleanupState.COMPLETE,
                                   elapsed_seconds=1., stdout=b"", stderr=b"worker lost")
        return original(argv, **kwargs)

    monkeypatch.setattr("behave.train.slime_stage.run_service", fail_resume)
    with pytest.raises(RuntimeError, match="resume"):
        execute(str(output), str(local))
    # Retain rather than delete the simulated worker's files.
    local.rename(local.with_name("retired-local"))
    receipt = json.loads((output / "artifacts/plan/initial.result.json").read_text())
    reader = DirectoryStore(output / "artifacts/plan/learning/initial/objects")
    assert load_learning_report(reader, BlobRef.from_dict(receipt["head"])) == receipt["report"]
    assert (output / "artifacts/checkpoint/iter_0000001/state.pt").read_bytes() == b"opaque worker state 1"
    assert not (output / "artifacts/final_hf").exists()


def test_training_job_cannot_accept_process_success_without_checkpoint_evidence(job, monkeypatch):
    output, local, calls = job

    def empty_success(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(status="exited", returncode=0, cleanup_state=CleanupState.COMPLETE,
                               elapsed_seconds=1., stdout=b"", stderr=b"")

    monkeypatch.setattr("behave.train.slime_stage.run_service", empty_success)
    with pytest.raises(RecordError, match="no saved learning history") as error:
        execute(str(output), str(local))
    assert error.value.code == "learning-checkpoint-missing"
    assert len(calls) == 1 and local.exists()
    assert not (output / "artifacts/plan/learning/resume").exists()
    assert not (output / "artifacts/final_hf").exists()
    stage = JournalDirectory(output / "artifacts/plan/learning/initial")
    assert stage.journal("learning").load()[-1].kind == "learning_process_settled"


def test_training_job_passes_runtime_lifetime_to_common_stage(job, monkeypatch):
    output, local, calls = job
    config_path = output / "configuration.json"
    config = json.loads(config_path.read_text())
    config.update(learning_seconds=7200, cleanup_seconds=37)
    write_json(config_path, config)
    monkeypatch.setattr("behave.train.slime_stage.time.monotonic", lambda: 100)

    def stopped(argv, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status="timeout", returncode=None, cleanup_state=CleanupState.COMPLETE,
                               elapsed_seconds=1., stdout=b"", stderr=b"")

    monkeypatch.setattr("behave.train.slime_stage.run_service", stopped)
    with pytest.raises(RuntimeError, match="initial"):
        execute(str(output), str(local))
    assert len(calls) == 1
    assert calls[0]["deadline"] == 7300 and calls[0]["cleanup_seconds"] == 37
    stage = JournalDirectory(output / "artifacts/plan/learning/initial")
    request = stage.journal("learning").load()[0].payload
    assert request["seconds"] == 7200 and request["cleanup_seconds"] == 37
    receipt = json.loads((output / "artifacts/plan/initial.result.json").read_text())
    assert receipt["report"]["status"] == "interrupted" and receipt["report"]["checkpoint"] is None


def test_zero_gradient_cycle_is_archived_but_is_not_update_acceptance(job, monkeypatch):
    output, local, _ = job
    from behave.isolation import service

    original = service.run_service

    def zero_gradient(argv, **kwargs):
        result = original(argv, **kwargs)
        if "--num-rollout" in argv:
            for path in (output / "artifacts/plan/learning").glob("*/stdout"):
                path.write_text("step 0: {'train/step': 0, 'train/grad_norm': 0.0}\n")
        else:
            result.stdout = b'[null,null]'
        return result

    monkeypatch.setattr(service, "run_service", zero_gradient)
    monkeypatch.setattr("behave.train.slime_stage.run_service", zero_gradient)
    assert execute(str(output), str(local)) == 0
    result = json.loads((output / "artifacts/plan/result.json").read_text())
    assert result["native_cycle_completed"] is True
    assert result["native_update_and_cold_resume"] is False
    assert result["nonzero_gradients"] == {"initial": False, "resume": False}
    assert result["weight_changes"] == [None, None]
    assert local.exists()
