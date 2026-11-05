"""A trace projection reads generic journals, not an Eval application."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from behave.agent import trace_export
from behave.agent.model import ModelReply
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import DirectoryReader
from behave.core.records import canonical_json
from behave.eval import generation


@pytest.mark.parametrize("journal", ["solver", "agent", ("revisions", "r0", "author")])
def test_shared_export_is_read_only_and_keeps_legacy_output_bytes(tmp_path, journal):
    from tools import eval_review_trial

    owner = JournalDirectory(tmp_path / "source")
    record = owner.objects.put(canonical_json({"stdout": "result\n", "stderr": "", "exit_code": 0}))
    parts = (journal,) if isinstance(journal, str) else journal
    log = owner.journal(*parts)
    first = log.append([], "tool_requested", {"turn": 0, "call": {
        "name": "run_command", "arguments": {"command": "printf 'result\\n'"}}})
    second = log.append([first], "tool_settled", {"turn": 0, "result": record.as_dict()}, (record,))
    log.append([first, second], "terminal", {"reason": "final_action"})

    def files(root):
        return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}

    before = files(owner.directory)
    summaries = []
    for index, exporter in enumerate((trace_export.export_candidate, generation.export_candidate,
                                      eval_review_trial.export_candidate)):
        summaries.append(exporter(owner.directory, tmp_path / str(index), journal=journal))
    assert summaries == [{"status": "final_action", "turns": 0, "tool_calls": 1, "events": 3}] * 3
    assert files(tmp_path / "0") == files(tmp_path / "1") == files(tmp_path / "2")
    assert files(owner.directory) == before
    assert json.loads((tmp_path / "0/0001_tool_result.json").read_text())["stdout"] == "result\n"


def test_export_does_not_import_task_execution_or_eval(tmp_path):
    owner = JournalDirectory(tmp_path / "source")
    owner.journal("solver").append([], "terminal", {"reason": "final_action"})
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from behave.agent.trace_export import export_candidate
assert export_candidate(Path(sys.argv[2]), Path(sys.argv[3]))['status'] == 'final_action'
assert not any(name == 'tools' or name.startswith(('tools.', 'behave.eval', 'behave.task_execution'))
               for name in sys.modules)
""", str(Path(__file__).resolve().parents[2] / "src"), str(owner.directory), str(tmp_path / "trace")],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=20)
    assert result.returncode == 0, result.stderr


def test_missing_trace_does_not_create_run_or_projection(tmp_path):
    assert trace_export.export_candidate(tmp_path / "absent", tmp_path / "trace") == {
        "status": "waiting", "turns": 0, "tool_calls": 0}
    assert not list(tmp_path.iterdir())


def model_trace(tmp_path):
    owner = JournalDirectory(tmp_path / "source")
    raw = b'{"provider":"opaque response"}\n\x00'
    request = owner.objects.put(canonical_json({"turn": 0, "messages": []}))
    reply = owner.objects.put(canonical_json(ModelReply("完成", raw=raw).as_dict()))
    raw_ref = owner.objects.put(raw)
    log = owner.journal("solver")
    first = log.append([], "model_requested", {"request": request.as_dict()}, (request,))
    second = log.append([first], "model_settled", {
        "record": reply.as_dict(), "raw": raw_ref.as_dict(), "outcome": "reply"}, (reply, raw_ref))
    log.append([first, second], "terminal", {"reason": "final_action"})
    return owner, raw_ref, raw


@pytest.mark.parametrize("incremental", (False, True))
def test_retry_fills_missing_raw_even_after_reply_was_published(tmp_path, monkeypatch, incremental):
    owner, raw_ref, raw = model_trace(tmp_path)
    destination = tmp_path / "trace"
    export = generation.LiveTraceExporter().export if incremental else trace_export.export_candidate
    read = DirectoryReader.read

    def interrupted(reader, ref):
        if ref == raw_ref:
            raise OSError("fixture raw read interrupted")
        return read(reader, ref)

    with monkeypatch.context() as patch:
        patch.setattr(DirectoryReader, "read", interrupted)
        with pytest.raises(OSError, match="raw read interrupted"):
            export(owner.directory, destination)
    assert (destination / "0001_reply.json").is_file()
    assert not (destination / "0001_provider_raw.json").exists()
    assert export(owner.directory, destination)["status"] == "final_action"
    assert (destination / "0001_provider_raw.json").read_bytes() == raw
    assert read(owner.objects, raw_ref) == raw


@pytest.mark.parametrize("incremental", (False, True))
def test_retry_repairs_legacy_truncated_raw(tmp_path, incremental):
    owner, _, raw = model_trace(tmp_path)
    destination = tmp_path / "trace"
    trace_export.export_candidate(owner.directory, destination)
    raw_path = destination / "0001_provider_raw.json"
    raw_path.write_bytes(raw[:4])  # Older exporters wrote directly to the final name.
    export = generation.LiveTraceExporter().export if incremental else trace_export.export_candidate
    assert export(owner.directory, destination)["status"] == "final_action"
    assert raw_path.read_bytes() == raw


def test_complete_raw_payload_is_not_read_or_rewritten_again(tmp_path, monkeypatch):
    owner, raw_ref, _ = model_trace(tmp_path)
    destination = tmp_path / "trace"
    expected = trace_export.export_candidate(owner.directory, destination)
    path = destination / "0001_provider_raw.json"
    before = path.stat()
    read = DirectoryReader.read

    def no_raw_read(reader, ref):
        assert ref != raw_ref, "a complete raw projection needs no payload reread"
        return read(reader, ref)

    monkeypatch.setattr(DirectoryReader, "read", no_raw_read)
    assert trace_export.export_candidate(owner.directory, destination) == expected
    assert generation.LiveTraceExporter().export(owner.directory, destination) == expected
    after = path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.mark.parametrize("filename", ("0001_provider_raw.json", "trace.md"))
def test_failed_projection_publish_keeps_old_file_and_retries(tmp_path, monkeypatch, filename):
    owner, _, raw = model_trace(tmp_path)
    destination = tmp_path / "trace"
    destination.mkdir()
    target = destination / filename
    target.write_bytes(b"previous projection")
    replace = Path.replace

    def interrupted(path, other):
        if other == target:
            assert path != target and target.read_bytes() == b"previous projection"
            raise OSError("fixture projection publication interrupted")
        return replace(path, other)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", interrupted)
        with pytest.raises(OSError, match="publication interrupted"):
            trace_export.export_candidate(owner.directory, destination)
    assert target.read_bytes() == b"previous projection"
    assert not list(destination.glob(".*.tmp"))
    assert trace_export.export_candidate(owner.directory, destination)["status"] == "final_action"
    assert (destination / "0001_provider_raw.json").read_bytes() == raw
    assert "完成" in (destination / "trace.md").read_text()
