"""Prepared lanes use the shared frozen scheduler, not a site-specific driver."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_eval_checkpoint_study import fixture

from behave.eval import controller
from behave.runtime import snapshots
from behave.runtime.records import write_json
from behave.task_execution.run_store import RunStore


def lane(root):
    for name in ("first", "next"):
        stage = root / name
        config = fixture(stage)
        config["archive_root"] = str(root)
        config["resources"]["allocation"] = 7
        write_json(stage / "configuration.json", config)
        write_json(stage / "queue.json", {"checkpoints": []})
    write_json(root / "stages.json", ["first", str(root / "next")])


@pytest.mark.parametrize("stages", [None, {}, [], [""], [" "], [1]])
def test_stage_list_must_be_nonempty_explicit_paths(tmp_path, stages):
    write_json(tmp_path / "stages.json", stages)
    with pytest.raises(ValueError, match="must list"):
        controller._configuration(tmp_path, "stages")
    assert not (tmp_path / "controller-entry").exists()


def test_stage_aliases_cannot_create_two_owners(tmp_path):
    lane(tmp_path)
    (tmp_path / "alias").symlink_to(tmp_path / "first", target_is_directory=True)
    write_json(tmp_path / "stages.json", ["first", "alias"])
    with pytest.raises(ValueError, match="duplicate stage"):
        controller._configuration(tmp_path, "stages")


@pytest.mark.parametrize("change", ["snapshot", "allocation", "boolean-allocation"])
def test_mixed_snapshots_or_allocations_rejected(tmp_path, change):
    lane(tmp_path)
    path = tmp_path / "next/configuration.json"
    config = controller.checkpoint_module.read(path)
    if change == "snapshot":
        config["code_directory"] = "other-code"
    else:
        config["resources"]["allocation"] = True if change == "boolean-allocation" else 8
    write_json(path, config)
    with pytest.raises(ValueError, match="one frozen snapshot and one allocation"):
        controller._configuration(tmp_path, "stages")


def test_foreground_stage_entry_pins_prepared_order_and_never_submits(tmp_path, monkeypatch):
    lane(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.glob("*/*.json")}
    monkeypatch.setenv("SLURM_JOB_ID", "7")
    monkeypatch.setenv("SLURMD_NODENAME", "fixture")
    calls = []
    monkeypatch.setattr(controller.os, "execv", lambda executable, argv: calls.append(argv))
    monkeypatch.setattr("behave.runtime.jobs.submit", lambda *a, **kw: pytest.fail("must stay foreground"))
    assert controller.main(["stages", str(tmp_path), "--allocation", "7"]) == 0
    command, = calls
    assert command[-3:] == [str(tmp_path), str(tmp_path / "first"), str(tmp_path / "next")]
    assert str(tmp_path / "code/snapshot") in command
    assert any("from tools.eval_checkpoint_study import run_stages" in value for value in command)
    assert all(path.read_bytes() == value for path, value in before.items())
    assert not (tmp_path / "control").exists()


def test_saved_allocation_must_match_enclosing_node(tmp_path, monkeypatch, capsys):
    lane(tmp_path)
    monkeypatch.setenv("SLURM_JOB_ID", "8")
    monkeypatch.setenv("SLURMD_NODENAME", "fixture")
    monkeypatch.setattr(controller.os, "execv", lambda *a: pytest.fail("wrong allocation"))
    assert controller.main(["stages", str(tmp_path), "--allocation", "8"]) == 1
    assert "do not belong" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["submitted", "failed", "completed"])
def test_stages_do_not_take_over_detached_records(tmp_path, monkeypatch, capsys, status):
    lane(tmp_path)
    write_json(tmp_path / "control/status.json", {"state": status})
    monkeypatch.setenv("SLURM_JOB_ID", "7")
    monkeypatch.setenv("SLURMD_NODENAME", "fixture")
    monkeypatch.setattr(controller.os, "execv", lambda *a: pytest.fail("unreconciled control"))
    assert controller.main(["stages", str(tmp_path), "--allocation", "7"]) == 1
    assert "explicit reconciliation" in capsys.readouterr().out


def test_stages_have_no_new_detached_launch_recipe(tmp_path):
    with pytest.raises(ValueError, match="foreground compute-node"):
        controller.launch(tmp_path, "stages")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("available", [True, False])
def test_real_frozen_stage_entry_owns_locks_and_records_liveness(tmp_path, available):
    lane(tmp_path)
    snapshot = tmp_path / "code/snapshot"
    name = "tools/eval_checkpoint_study.py"
    program = b'''from pathlib import Path
import json
from behave.agent.attempt import AttemptInterrupted
from behave.eval.checkpoint import run_stages as shared_run
from behave.task_execution.run_store import RunStore
def run_stages(roots, *, progress_directory):
    assert roots == [progress_directory / 'first', progress_directory / 'next']
    assert Path(__file__).is_relative_to(progress_directory / 'code/snapshot')
    assert json.loads((progress_directory / 'controller_node.json').read_text())['state'] == 'live'
    try:
        with RunStore(progress_directory / 'controller-entry').owner():
            raise AssertionError('lost entry lock')
    except AttemptInterrupted:
        pass
    result = shared_run(roots, repository=Path(__file__).parents[1], progress_directory=progress_directory)
    counter = progress_directory / 'calls.txt'
    counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))
    return result
'''
    snapshots.freeze(Path(__file__).resolve().parents[2], snapshot, extra_files=(name,),
                     overrides={name: program if available else b"def run(root): pass\n"})
    env = {**os.environ, "SLURM_JOB_ID": "7", "SLURMD_NODENAME": "fixture"}
    command = [sys.executable, "-I", "-B", "-c",
               "from behave.eval.controller import main;raise SystemExit(main())",
               "stages", str(tmp_path), "--allocation", "7"]

    def invoke():
        return subprocess.run(command, env=env, capture_output=True, timeout=30, check=False)

    if not available:
        result = invoke()
        assert result.returncode != 0 and b"run_stages" in result.stderr
        assert not (tmp_path / "calls.txt").exists()
        return  # No fallback to today's scheduler inside an older frozen snapshot.
    with RunStore(tmp_path / "controller-entry").owner():
        assert b"run-already-owned" in invoke().stderr
    with RunStore(tmp_path / "next/owner").owner():
        assert b"run-already-owned" in invoke().stderr
        assert not (tmp_path / "calls.txt").exists()
    for count in (1, 2):
        env["SLURM_RESTART_COUNT"] = str(count - 1)
        result = invoke()
        assert result.returncode == 0, result.stderr.decode()
        assert (tmp_path / "calls.txt").read_text() == str(count)
        assert controller.checkpoint_module.read(tmp_path / "controller_node.json")["state"] == "stopped"
        assert controller.checkpoint_module.read(tmp_path / "progress.json")["state"] == "completed"
    original, _ = controller._command(tmp_path, "stages", controller._configuration(tmp_path, "stages"))
    frozen = controller._foreground_command(tmp_path, tmp_path / "control", original)
    config_path = tmp_path / "next/configuration.json"
    config = controller.checkpoint_module.read(config_path)
    config["resources"]["allocation"] = 8
    write_json(config_path, config)
    result = subprocess.run(frozen, env=env, capture_output=True, timeout=30, check=False)
    assert result.returncode != 0 and b"changed before entry" in result.stderr
    assert (tmp_path / "calls.txt").read_text() == "2"
