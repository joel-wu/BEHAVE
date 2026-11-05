"""Controller placement and restart must not change worker ownership."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_eval_checkpoint_study import fixture

from behave.agent.attempt import AttemptInterrupted
from behave.eval import checkpoint as checkpoint_module
from behave.eval import controller, fleet
from behave.runtime import jobs, snapshots
from behave.runtime.records import write_json
from behave.task_execution.run_store import RunStore


def workspace(root, kind):
    fixture(root)
    write_json(root / "queue.json", {"checkpoints": []})
    if kind == "fleet":
        write_json(root / "fleet.json", {"archive_root": str(root), "code_directory": "code",
                                       "nodes": {}, "measurements": []})


@pytest.mark.parametrize("kind", ["checkpoint", "fleet", "fleet-override"])
@pytest.mark.parametrize("code", [None, 0, 7, 143])
@pytest.mark.parametrize("supervised", [False, True])
def test_frozen_controller_return_code_reaches_process_owner(tmp_path, kind, code, supervised):
    selected = "fleet" if kind == "fleet-override" else kind
    workspace(tmp_path, selected)
    snapshot = tmp_path / "code/snapshot"
    name = f"tools/eval_checkpoint_{'study' if selected == 'checkpoint' else 'fleet'}.py"
    script = tmp_path / "controller.py" if kind == "fleet-override" else snapshot / name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "from pathlib import Path\n"
        "def run(root):\n"
        "    Path(root, 'called.txt').write_text(__file__)\n"
        f"    return {code!r}\n")
    config = controller._configuration(tmp_path, selected)
    if kind == "fleet-override":
        config.update(controller_script=str(script),
                      controller_sha256=hashlib.sha256(script.read_bytes()).hexdigest())
    command, _ = controller._command(tmp_path, selected, config)
    expected = 0 if code is None else code
    if supervised:
        control = tmp_path / "control"
        write_json(control / "launch.json", {"argv": command, "cwd": str(tmp_path),
            "wall_seconds": 20, "cleanup_seconds": 5, "heartbeat_seconds": 1, "metadata": {}})
        jobs.supervise(control)
        result = jobs.status_of(control)
        assert result["state"] == ("completed" if expected == 0 else "failed")
        assert result["returncode"] == expected
    else:
        process = subprocess.run(command, capture_output=True, timeout=20, check=False)
        assert process.returncode == expected, process.stderr.decode()
        assert not (tmp_path / "control").exists()
    assert (tmp_path / "called.txt").read_text() == str(script)


@pytest.mark.parametrize("kind", ["checkpoint", "fleet"])
@pytest.mark.parametrize("state", ["submitted", "supervising", "failed", "completed", "unknown"])
def test_repeated_detached_launch_only_observes_existing_state(tmp_path, monkeypatch, kind, state):
    workspace(tmp_path, kind)
    write_json(tmp_path / "control/launch.json", {})
    monkeypatch.setattr("behave.runtime.jobs.status_of", lambda _: {"state": state})
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("must not resubmit"))
    module = checkpoint_module if kind == "checkpoint" else fleet
    assert module.launch(tmp_path) == {"state": state}
    assert not (tmp_path / "controller-entry").exists()


@pytest.mark.parametrize("kind", ["checkpoint", "fleet"])
def test_foreground_owner_blocks_a_new_detached_launch(tmp_path, monkeypatch, kind):
    workspace(tmp_path, kind)
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("foreground owner is live"))
    with RunStore(tmp_path / "controller-entry").owner(), pytest.raises(AttemptInterrupted, match="run-already-owned"):
        controller.launch(tmp_path, kind)
    assert not (tmp_path / "control").exists()


@pytest.mark.parametrize("job,node,allocation", [
    (None, None, "7"), ("7", None, "7"), ("8", "fixture-node", "7"),
    ("0", "fixture-node", "0"), ("-1", "fixture-node", "-1"),
])
def test_foreground_rejects_wrong_placement_before_accessing_run(tmp_path, monkeypatch, capsys, job, node, allocation):
    for key, value in (("SLURM_JOB_ID", job), ("SLURMD_NODENAME", node)):
        monkeypatch.delenv(key, raising=False)
        if value is not None:
            monkeypatch.setenv(key, value)
    monkeypatch.setattr(controller.os, "execv", lambda *a: pytest.fail("invalid placement"))
    assert controller.main(["checkpoint", str(tmp_path), "--allocation", allocation]) == 1
    assert "specified Slurm allocation" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["checkpoint", "fleet"])
def test_foreground_keeps_frozen_entry_and_never_detaches(tmp_path, monkeypatch, kind):
    from behave.eval.__main__ import main

    workspace(tmp_path, kind)
    monkeypatch.setenv("SLURM_JOB_ID", "7")
    monkeypatch.setenv("SLURMD_NODENAME", "fixture-node")
    calls = []
    monkeypatch.setattr(controller.os, "execv", lambda *a: calls.append(a))
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("must stay foreground"))
    assert main(["controller", kind, str(tmp_path), "--allocation", "7"]) == 0
    executable, command = calls[0]
    assert executable == sys.executable and command[:5] == [sys.executable, "-I", "-B", "-u", "-c"]
    assert str(tmp_path / "controller-entry") in command
    assert str(tmp_path / "code/snapshot") in command
    assert any(f"from tools.eval_checkpoint_{'study' if kind == 'checkpoint' else 'fleet'} import run;" in arg
               for arg in command)
    assert command[-1] == str(tmp_path)
    assert not (tmp_path / "control").exists()


@pytest.mark.parametrize("kind", ["checkpoint", "fleet"])
@pytest.mark.parametrize("evidence", ["empty", "submitted", "failed", "completed"])
def test_foreground_cannot_take_over_detached_evidence(tmp_path, monkeypatch, capsys, kind, evidence):
    workspace(tmp_path, kind)
    control = tmp_path / "control"
    control.mkdir()
    if evidence != "empty":
        write_json(control / "status.json", {"state": evidence})
    monkeypatch.setenv("SLURM_JOB_ID", "7")
    monkeypatch.setenv("SLURMD_NODENAME", "fixture-node")
    monkeypatch.setattr(controller.os, "execv", lambda *a: pytest.fail("unreconciled owner"))
    assert controller.main([kind, str(tmp_path), "--allocation", "7"]) == 1
    assert "explicit reconciliation" in capsys.readouterr().out


def test_checkpoint_queue_locks_all_stages_before_dispatch_and_releases_on_failure(tmp_path, monkeypatch):
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        workspace(root, "checkpoint")
    monkeypatch.setattr(checkpoint_module, "_generate", lambda *a, **kw: pytest.fail("another controller owns a stage"))
    with RunStore(roots[1] / "owner").owner(), pytest.raises(AttemptInterrupted, match="run-already-owned"):
        checkpoint_module.run_stages(roots, repository=tmp_path)
    with RunStore(roots[0] / "owner").owner():
        pass  # Failure to acquire the second stage released the first.
    monkeypatch.setattr(checkpoint_module, "_generate", lambda *a, **kw: 0)
    assert checkpoint_module.run_stages(roots, repository=tmp_path) == 0


@pytest.mark.parametrize("kind", ["checkpoint", "fleet"])
@pytest.mark.parametrize("exit_code", [0, 143])
def test_real_foreground_process_owns_lock_and_uses_frozen_code(tmp_path, kind, exit_code):
    workspace(tmp_path, kind)
    snapshot = tmp_path / "code/snapshot"
    name = f"tools/eval_checkpoint_{'study' if kind == 'checkpoint' else 'fleet'}.py"
    program = b'''from pathlib import Path
from behave.agent.attempt import AttemptInterrupted
from behave.task_execution.run_store import RunStore
def run(root):
    root = Path(root)
    import behave.task_execution.run_store as store
    assert Path(store.__file__).is_relative_to(root / 'code/snapshot')
    try:
        with RunStore(root / 'controller-entry').owner():
            raise AssertionError('foreground process lost its entry lock')
    except AttemptInterrupted:
        pass
    counter = root / 'fixture-calls.txt'
    counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
    return 0
'''
    program = program.replace(b"    return 0\n", f"    return {exit_code}\n".encode())
    snapshots.freeze(Path(__file__).resolve().parents[2], snapshot,
                     extra_files=(name,), overrides={name: program})
    env = {**os.environ, "SLURM_JOB_ID": "7", "SLURMD_NODENAME": "fixture-node"}
    command = [sys.executable, "-I", "-B", "-c",
               "from behave.eval.controller import main;raise SystemExit(main())",
               kind, str(tmp_path), "--allocation", "7"]
    with RunStore(tmp_path / "controller-entry").owner():
        process = subprocess.run(command, env=env, capture_output=True, timeout=20, check=False)
        assert process.returncode != 0 and b"run-already-owned" in process.stderr
        assert not (tmp_path / "fixture-calls.txt").exists()
    for count in (1, 2):
        # A later invocation can re-enter; the real queue decides what can resume.
        env["SLURM_RESTART_COUNT"] = str(count - 1)
        process = subprocess.run(command, env=env, capture_output=True, timeout=20, check=False)
        assert process.returncode == exit_code, process.stderr.decode()
        assert (tmp_path / "fixture-calls.txt").read_text() == str(count)
    assert not (tmp_path / "control").exists()
    # A detached owner appearing between CLI validation and exec is rejected
    # inside the frozen process's entry lock, not just by the parent preflight.
    original, _ = controller._command(tmp_path, kind, controller._configuration(tmp_path, kind))
    foreground = controller._foreground_command(tmp_path, tmp_path / "control", original)
    (tmp_path / "control").mkdir()
    process = subprocess.run(foreground, capture_output=True, timeout=20, check=False)
    assert process.returncode != 0 and b"no automatic takeover" in process.stderr
    assert (tmp_path / "fixture-calls.txt").read_text() == "2"
