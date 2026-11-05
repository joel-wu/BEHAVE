"""Public PPA commands separate operator input and saved evidence from execution."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PPA_ENTRY_PROBE = r'''
import importlib.abc, json, sys
class NoExecution(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        forbidden = ('behave.ppa.workflow', 'behave.ppa.finalize', 'behave.ppa.worker',
                     'behave.agent.loop', 'behave.agent.process_model', 'behave.agent.providers',
                     'behave.sim', 'matplotlib', 'tools')
        if any(fullname == item or fullname.startswith(item + '.') for item in forbidden):
            raise AssertionError('read-only command imported execution: ' + fullname)
sys.meta_path.insert(0, NoExecution())
from behave.ppa.__main__ import main
raise SystemExit(main(json.loads(sys.argv[1])))
'''


def invoke(root, args):
    source = Path(__file__).resolve().parents[2] / "src"
    return subprocess.run([sys.executable, "-I", "-B", "-c",
        f"import sys; sys.path.insert(0, {str(source)!r})\n" + PPA_ENTRY_PROBE, json.dumps(args)],
        cwd=root, capture_output=True, text=True, timeout=15, check=False)


@pytest.mark.parametrize("command", [None, "prepare", "search", "compare", "report", "status", "study-report", "plot"])
def test_help_loads_no_model_simulator_or_plotting_stack(tmp_path, command):
    result = invoke(tmp_path, ([command] if command else []) + ["--help"])
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout and not list(tmp_path.iterdir())


COMMON = ["--objects", "unread", "--release", "unread", "--task", "fixture",
          "--experiment", "unread", "--liberty", "unread", "--sta", "unread", "--output", "untouched"]
INVALID_ARGUMENTS = [
    (["prepare", "--study", "unread", "--objects", "unread", "--release", "unread",
      "--output", "untouched", "--methods", "unread"], "--methods requires"),
    (["compare", *COMMON, "--search", "same", "a", "--search", "same", "b"], "labels must be unique"),
    (["compare", *COMMON, "--search", "one", "a", "--cpu-endpoint", "/same", "--cpu-endpoint", "/same"],
     "scheduler ingress"),
    (["search", *COMMON, "--provider", "unread", "--prompt", "unread",
      "--cpu-endpoint", "/one", "--cpu-endpoint", "/two"], "scheduler ingress"),
    (["study-report", "--campaign", "missing-separator", "--account", "unread"], "TASK=DIRECTORY"),
    (["plot", "unread", "untouched", "--label", "same", "one", "--label", "same", "two"],
     "labels must be unique"),
]


@pytest.mark.parametrize("arguments,message", INVALID_ARGUMENTS)
def test_argument_errors_precede_input_reads_and_execution_imports(tmp_path, arguments, message):
    result = invoke(tmp_path, arguments)
    assert result.returncode == 2, result.stderr
    assert message in result.stderr and not result.stdout
    assert not list(tmp_path.iterdir())


def report_archive(root):
    from behave.agent.run_store import JournalDirectory
    from behave.ppa.records import write_document

    run = JournalDirectory(root)
    manifest, refs = write_document(run.objects, {"experiment": {"stage": "rtl", "targets_ns": [100]}, "searches": {}})
    row = {"candidate": "c", "score": 1, "mapped_score": 0,
           "performance": {"source": "rtl", "score": 1, "time_ns": 80},
           "eda": {"status": "measured", "area_um2": 10, "timing": {"status": "met"}}}
    result, closure = write_document(run.objects, {"schema": "behave.ppa-comparison/2", "manifest": manifest.as_dict(),
        "candidates": {"c": row}, "members": {"one": ["c"]}, "final_service_calls": 1})
    run.journal("workflow").append((), "finished", {"result": result.as_dict()}, (*refs, *closure))
    return root


def test_completed_report_reads_verified_evidence_without_execution_imports(tmp_path):
    root = report_archive(tmp_path / "archive")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = invoke(tmp_path, ["report", str(root)])
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["methods"]["one"]["frontier"] == ["c"]
    assert report["methods"]["one"]["targets"][0]["candidate"] == "c"
    assert report["cost"] == {"final_service_calls": 1, "searches": {}}
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_unfinished_report_has_a_clear_error_and_does_not_create_state(tmp_path):
    result = invoke(tmp_path, ["report", "not-started"])
    assert result.returncode == 1, result.stderr
    error = json.loads(result.stdout)
    assert error["status"] == "unavailable" and error["code"] == "ppa-command-invalid"
    assert "not durably completed" in error["message"] and not result.stderr
    assert not list(tmp_path.iterdir())


def test_corrupt_report_preserves_evidence_and_the_storage_error(tmp_path):
    root = report_archive(tmp_path / "archive")
    artifact = next(path for path in (root / "objects").iterdir() if path.is_file())
    artifact.chmod(0o600)  # Deliberately corrupt only this test's immutable object.
    artifact.write_bytes(b"corrupt")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = invoke(tmp_path, ["report", str(root)])
    assert result.returncode == 1, result.stderr
    error = json.loads(result.stdout)
    assert error["status"] == "unavailable"
    assert error["code"] == "content-identity-mismatch" and error["message"]
    assert not result.stderr
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_campaign_status_does_not_import_execution_or_create_state(tmp_path):
    result = invoke(tmp_path, ["status", "not-started"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "directory": str(tmp_path / "not-started"), "state": "not-created", "execution_visible": False}
    assert not list(tmp_path.iterdir())


def test_historical_execution_names_reuse_report_implementations():
    from behave.ppa import finalize, records, report, workflow

    assert workflow.frontier is report.frontier
    assert workflow._union is finalize._union is records.union_pool
    assert finalize.finalized_report is records.finalized_report is report.finalized_report


AGENT_EXPORT_PROBE = r'''
import inspect, sys
import behave.agent as agent
from behave.agent import AttemptSpec, AttemptJournal, RecordedTurn, trajectory
assert 'behave.agent.loop' not in sys.modules
assert 'run_attempt' in dir(agent)
assert callable(trajectory) and not inspect.ismodule(trajectory)
try:
    agent.not_a_public_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown export was invented')
exports = {}
exec('from behave.agent import *', exports)
assert set(exports) - {'__builtins__'} == set(agent.__all__)
from behave.agent.loop import run_attempt
assert exports['run_attempt'] is agent.run_attempt is run_attempt
assert exports['trajectory'] is trajectory
'''


def test_agent_record_imports_preserve_public_exports_without_loading_the_loop(tmp_path):
    source = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run([sys.executable, "-I", "-B", "-c",
        f"import sys; sys.path.insert(0, {str(source)!r})\n" + AGENT_EXPORT_PROBE],
        cwd=tmp_path, capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr


PPA_FILE_PROBE = r'''
import contextlib, io, json, os, sys
from pathlib import Path
from behave.ppa.__main__ import main
from behave.ppa.planning import read_study
from behave.ppa.records import release_reference
root = Path(sys.argv[1])
root.mkdir()
fifo = root / 'input.fifo'
os.mkfifo(fifo)
for read in (read_study, release_reference):
    try:
        read(fifo)
    except ValueError as error:
        assert str(error) == 'operator input must be a regular file'
    else:
        raise AssertionError('special operator file was read')
out = io.StringIO()
with contextlib.redirect_stdout(out):
    assert main(['plot', str(fifo), str(root / 'plot')]) == 1
assert json.loads(out.getvalue())['message'] == 'operator input must be a regular file'
assert list(root.iterdir()) == [fifo]
'''


def test_special_operator_files_never_wait_for_writers(tmp_path):
    source = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run([sys.executable, "-I", "-B", "-c",
        f"import sys; sys.path.insert(0, {str(source)!r})\n" + PPA_FILE_PROBE, str(tmp_path / "probe")],
        capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stderr
