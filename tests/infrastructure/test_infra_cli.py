"""Implemented commands only; help and offline inspection never dispatch models."""

import subprocess
import sys
from dataclasses import replace

import pytest
from test_evaluation import Judge, plan_for
from test_task_workflow import STOP, setup

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.task_execution.batch import run_batch
from behave.task_execution.run_store import RunStore
from behave.task_execution.simulator_judge import SimulatorJudge


def command(*args):
    return subprocess.run([sys.executable, "-B", "-m", "behave.cli", *map(str, args)],
                          capture_output=True, timeout=30, check=False)


@pytest.mark.parametrize("large_plan", [False, True])
def test_eval_preflight_and_errors_do_not_create_runs(tmp_path, publication, large_plan):
    job, _ = setup(tmp_path, publication, STOP)
    plan = replace(plan_for(job), judge_backend=SimulatorJudge.identity)
    if large_plan:
        plan = replace(plan, run_id="x" * (64 << 20))
    path, run = tmp_path / "plan.json", tmp_path / "never-started"
    path.write_bytes(plan.to_bytes())
    options = ("eval", "--plan", path, "--task-objects", publication.reader.directory, "--run-directory", run, "--dry-run")
    result = command(*options)
    assert result.returncode == 0, result.stderr
    assert parse_json(result.stdout)["status"] == "validated" and not run.exists()
    path.write_bytes(b'{"secret-canary":"operator-private"}')
    result = command(*options)
    assert result.returncode == 1 and parse_json(result.stdout)["status"] == "unavailable"
    assert b"operator-private" not in result.stdout + result.stderr and not run.exists()


@pytest.mark.isolation
def test_offline_inspection_verifies_exported_batch_without_upstream(tmp_path, publication):
    job, _ = setup(tmp_path, publication, STOP)
    root = RunStore(tmp_path / "batch")
    report = run_batch(plan=plan_for(job), reader=publication.reader, directory=root.directory, judge=Judge())
    path = tmp_path / "head.json"
    path.write_bytes(canonical_json(report.head.as_dict()))
    result = command("inspect", "--kind", "eval", "--objects", root.objects.directory, "--record", path)
    assert result.returncode == 0, result.stderr
    assert parse_json(result.stdout) == report.as_dict()
    result = command("inspect", "--kind", "eval", "--objects", tmp_path / "absent", "--record", path)
    assert result.returncode == 1 and parse_json(result.stdout)["code"] == "content-unavailable"


def test_offline_inspection_reads_full_reference_and_never_dispatches(tmp_path, publication, monkeypatch, capsys):
    from behave.cli.inspect import main
    from behave.task_execution import batch as batch_module

    job, _ = setup(tmp_path, publication, STOP)
    root = RunStore(tmp_path / "batch")

    def unavailable(**kwargs):
        raise OSError("fixture candidate unavailable")

    # Produce a real sealed batch with a no-result row, without a model/Sim run.
    monkeypatch.setattr(batch_module, "execute_task", unavailable)
    report = run_batch(plan=plan_for(job), reader=publication.reader, directory=root.directory, judge=Judge())
    path = tmp_path / "head.json"
    path.write_bytes(canonical_json(report.head.as_dict()) + b" " * 8192)
    before = {p.relative_to(root.directory): p.read_bytes() for p in root.directory.rglob("*") if p.is_file()}

    def forbidden(*args, **kwargs):
        pytest.fail("offline inspection dispatched a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert main(["--kind", "eval", "--objects", str(root.objects.directory), "--record", str(path)]) == 0
    output = capsys.readouterr()
    assert parse_json(output.out.encode()) == report.as_dict()
    assert output.err == ""
    assert report.summary["no_result"] == 1 and report.summary["mean_on_scored"] is None
    assert before == {p.relative_to(root.directory): p.read_bytes() for p in root.directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize("option", ["--approved-manifest", "--native-endpoint"])
def test_non_training_inspection_rejects_unused_training_options(tmp_path, capsys, option):
    from behave.cli.inspect import main

    with pytest.raises(SystemExit) as error:
        main(["--kind", "eval", "--objects", str(tmp_path / "objects"), "--record", str(tmp_path / "head"),
              option, str(tmp_path / "unused")])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "only apply to training inspection" in output.err and output.out == ""
    assert not tuple(tmp_path.iterdir())


def test_inspection_failure_has_operator_diagnostics_without_dumping_input(tmp_path, capsys):
    from behave.cli.inspect import main

    path = tmp_path / "head.json"
    path.write_bytes(b"not json: secret-payload-canary")
    assert main(["--kind", "eval", "--objects", str(tmp_path / "objects"), "--record", str(path)]) == 1
    output = capsys.readouterr()
    assert parse_json(output.out.encode()) == {"status": "unavailable", "code": "record-json-invalid"}
    assert "invalid UTF-8 JSON record" in output.err
    assert "secret-payload-canary" not in output.out + output.err


def test_inspection_does_not_hide_unexpected_programming_errors(tmp_path, monkeypatch):
    from behave.cli import inspect

    path = tmp_path / "head.json"
    path.write_bytes(canonical_json(BlobRef.of(b"report").as_dict()))

    def fail(*args, **kwargs):
        raise RuntimeError("unexpected inspection bug")

    monkeypatch.setattr(inspect, "inspect_record", fail)
    with pytest.raises(RuntimeError, match="unexpected inspection bug"):
        inspect.main(["--kind", "eval", "--objects", str(tmp_path / "objects"), "--record", str(path)])


def test_entry_points_resolve_only_implemented_applications():
    import importlib
    import tomllib
    from pathlib import Path

    record = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    expected = {"behave-infra", "behave-eval", "behave-data", "behave-train", "behave-trainer", "behave-inspect", "behave-release"}
    assert expected <= record["project"]["scripts"].keys()
    for name in expected:
        module, function = record["project"]["scripts"][name].split(":")
        assert callable(getattr(importlib.import_module(module), function))
