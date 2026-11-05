"""CLI uses the same fixed coordinator; fixtures do not invoke paid providers."""

import subprocess
import sys
from dataclasses import replace

import pytest
from test_synthesis import reader_replies, setup
from test_synthesis_process import fixed
from test_synthesis_source_review import prepared, review_reply

from behave.agent.admission import AdmissionPolicy
from behave.agent.model import ModelReply
from behave.core.records import RecordError, parse_json
from behave.data.synthesis import SynthesisConfiguration


def configuration(tmp_path):
    options, _, _ = setup(tmp_path)
    fixed(options, [[ModelReply("No task draft yet.")]], [[]])
    config = SynthesisConfiguration(options["plan"], model_admission=AdmissionPolicy("fixture", 1))
    path = tmp_path / "configuration.json"
    path.write_bytes(config.to_bytes())
    assert SynthesisConfiguration.from_bytes(config.to_bytes()) == config
    return options, config, path


def command(path, source, run, *args):
    completed = subprocess.run([sys.executable, "-B", "-m", "behave.data", "--configuration", str(path),
        "--source-objects", str(source), "--run-directory", str(run), *args], capture_output=True, timeout=90, check=False)
    return completed, parse_json(completed.stdout)


def test_dry_run_checks_content_without_creating_run_or_model_call(tmp_path):
    options, config, path = configuration(tmp_path)
    run = tmp_path / "untouched"
    completed, result = command(path, options["reader"].directory, run, "--dry-run")
    assert completed.returncode == 0, completed.stderr
    assert result["status"] == "validated" and result["task_id"] == config.plan.task_id
    assert not run.exists()
    completed, result = command(path, tmp_path / "missing", run, "--dry-run")
    assert completed.returncode == 1 and result["status"] == "unavailable"
    assert not run.exists() and "score" not in result


def test_command_explains_bad_json_without_echoing_input(tmp_path):
    path = tmp_path / "configuration.json"
    path.write_text("private-configuration-canary")
    run = tmp_path / "untouched"

    completed, result = command(path, tmp_path / "objects", run, "--dry-run")

    assert completed.returncode == 1
    assert result == {"status": "unavailable", "code": "record-json-invalid"}
    assert b"RecordError:" in completed.stderr
    assert b"private-configuration-canary" not in completed.stdout + completed.stderr
    assert not run.exists()


def test_dry_run_dispatches_no_process(tmp_path, monkeypatch, capsys):
    from behave.data import __main__ as entry
    from behave.data.__main__ import main

    options, _, path = configuration(tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("dry-run must not launch a process"))
    monkeypatch.setattr(entry, "command_scope", lambda **kw: pytest.fail("dry-run installed process controls"))
    run = tmp_path / "untouched"

    assert main(["--configuration", str(path), "--source-objects", str(options["reader"].directory),
                 "--run-directory", str(run), "--dry-run"]) == 0
    output = capsys.readouterr()
    assert parse_json(output.out.encode())["status"] == "validated"
    assert output.err == "" and not run.exists()


@pytest.mark.parametrize("failure", [None, ValueError("fixture execution failed"), RuntimeError("fixture bug")])
def test_execution_restores_signal_handlers_and_reports_errors(tmp_path, monkeypatch, capsys, failure):
    import signal

    from behave.data import __main__ as entry

    options, _, path = configuration(tmp_path)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def discover(**kwargs):
        assert all(signal.getsignal(sig) != handler for sig, handler in previous.items())
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert kwargs["control"].cancelled()
        if failure is not None:
            raise failure
        return {"status": "completed", "sources": []}

    # A source-stage result takes an early return in main, still inside finally.
    from behave.improve import acquisition as acquisition_module

    monkeypatch.setattr(acquisition_module.AcquisitionConfiguration, "from_bytes", lambda payload: object())
    monkeypatch.setattr(acquisition_module, "acquire_materials", discover)
    arguments = ["--stage", "acquisition", "--configuration", str(path), "--source-objects",
                 str(options["reader"].directory), "--run-directory", str(tmp_path / "command")]
    if type(failure) is RuntimeError:
        with pytest.raises(RuntimeError, match="fixture bug"):
            entry.main(arguments)
        assert capsys.readouterr().out == ""
    else:
        assert entry.main(arguments) == (0 if failure is None else 1)
        output = capsys.readouterr()
        result = parse_json(output.out.encode())
        if failure is None:
            assert result == {"status": "completed", "sources": []}
            assert output.err == ""
        else:
            assert result == {"status": "unavailable", "code": "synthesis-command-invalid"}
            assert output.err.strip() == "ValueError: fixture execution failed"
    assert {sig: signal.getsignal(sig) for sig in previous} == previous


def test_synthesis_configuration_preserves_shared_resource_identity(tmp_path):
    from behave.task_execution.cocotb_worker import CocotbBackend
    from behave.task_execution.testbench import TBPolicy

    _, config, _ = configuration(tmp_path)
    policy = AdmissionPolicy("eda", 1)
    author = replace(config.plan.processes.author, cocotb_policy=TBPolicy(
        CocotbBackend.identity, CocotbBackend.collector, CocotbBackend.metric, max_retries=0))
    plan = replace(config.plan, processes=replace(config.plan.processes, author=author))
    config = replace(config, plan=plan, check_admission=policy, development_admission=policy)
    pools = config.pools()
    assert pools["check_admission"] is pools["development_admission"]
    assert pools["model_admission"] is not pools["check_admission"]
    with pytest.raises(ValueError, match="conflicting"):
        replace(config, development_admission=replace(policy, concurrency=2))


@pytest.mark.isolation
def test_command_runs_then_recovers_without_upstream_and_keeps_billing(tmp_path):
    options, _, path = configuration(tmp_path)
    run = tmp_path / "command"
    first, result = command(path, options["reader"].directory, run)
    assert first.returncode == 0, first.stderr
    assert result["status"] == "unfinished"
    assert result["billing"] == {"cost_by_currency": {"USD": "0.01"},
                                 "unpriced_dispatches": 0, "unresolved_dispatches": 0}
    assert "review_package" in result and "score" not in result
    again, replayed = command(path, tmp_path / "upstream-is-gone", run)
    assert again.returncode == 0, again.stderr
    assert replayed == result
    assert not (run / "release").exists()


def test_configuration_refuses_callback_plan_and_unused_execution_pool(tmp_path):
    options, config, _ = configuration(tmp_path)
    with pytest.raises(ValueError, match="fixed process"):
        replace(config, plan=replace(options["plan"], processes=None))
    with pytest.raises(ValueError, match="Bash"):
        replace(config, development_admission=AdmissionPolicy("eda", 1)).preflight(options["reader"])
    with pytest.raises(RecordError, match="canonical"):
        SynthesisConfiguration.from_bytes(config.to_bytes() + b"\n")


@pytest.mark.isolation
def test_command_reaches_review_package_with_real_comparison_but_no_publication(tmp_path):
    options, author, _ = prepared(tmp_path, real=True)
    from test_synthesis_source_review import read_comparison

    fixed(options, [author], [reader_replies()], [[read_comparison(), review_reply()]])
    config = SynthesisConfiguration(options["plan"], model_admission=AdmissionPolicy("models", 1),
                                    check_admission=AdmissionPolicy("checks", 1))
    path = tmp_path / "configuration.json"
    path.write_bytes(config.to_bytes())
    run = tmp_path / "command"
    completed, result = command(path, options["reader"].directory, run)
    assert completed.returncode == 0, completed.stderr
    assert result["status"] == "review_ready", result
    assert result["billing"]["cost_by_currency"] == {"USD": "0.10"}
    assert not (run / "release").exists() and "score" not in result
