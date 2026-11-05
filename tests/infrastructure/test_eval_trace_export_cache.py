"""Progress polling must not repeatedly render settled candidate histories."""

import pytest
from test_attempts import Scripted, call

from behave.agent.attempt import AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.loop import run_attempt
from behave.agent.model import ModelReply
from behave.agent.tools import file_tools
from behave.core.records import RecordError
from behave.eval import generation
from behave.task_execution.run_store import RunStore


def files(directory):
    return {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_unchanged_and_completed_traces_are_not_rendered_again(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "trace"
    journal = RunStore(source).journal("solver")
    first = journal.append([], "context_evicted", {"note": "first segment"})
    exporter = generation.LiveTraceExporter()
    render, calls = generation._export_events, []

    def counted(*args, **kwargs):
        calls.append(1)
        return render(*args, **kwargs)

    monkeypatch.setattr(generation, "_export_events", counted)
    running = exporter.export(source, target)
    assert running["status"] == "running" and len(calls) == 1
    for _ in range(5):
        assert exporter.export(source, target) == running
    assert len(calls) == 1
    journal.append([first], "terminal", {"reason": "stopped"})
    complete = exporter.export(source, target)
    assert complete["status"] == "stopped" and len(calls) == 2
    assert exporter.export(source, target, completed=True) == complete
    # Once both the solver trace and its batch case are committed, even the
    # journal head need not be reread. Large cached event prefixes are released.
    assert exporter._candidates[(source, target)][0] == ()
    monkeypatch.setattr(AttemptJournal, "load_after", lambda *a, **kw: pytest.fail("settled journal read"))
    copy = exporter.export(source, target, completed=True)
    copy["status"] = "caller mutation"
    assert exporter.export(source, target, completed=True) == complete
    assert len(calls) == 2


def test_waiting_candidate_is_not_created_or_permanently_cached(tmp_path):
    source, target = tmp_path / "source", tmp_path / "trace"
    exporter = generation.LiveTraceExporter()
    assert exporter.export(source, target, completed=True)["status"] == "waiting"
    assert not source.exists() and not target.exists()
    RunStore(source).journal("solver").append([], "terminal", {"reason": "final_action"})
    assert exporter.export(source, target, completed=True)["status"] == "final_action"


def test_export_failure_does_not_consume_the_new_tail(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "trace"
    journal = RunStore(source).journal("solver")
    first = journal.append([], "context_evicted", {"note": "prefix"})
    exporter = generation.LiveTraceExporter()
    exporter.export(source, target)
    journal.append([first], "terminal", {"reason": "final_action"})
    with monkeypatch.context() as patch:
        patch.setattr(generation, "_export_events", lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture write")))
        with pytest.raises(OSError, match="fixture write"):
            exporter.export(source, target, completed=True)
    assert exporter.export(source, target, completed=True)["status"] == "final_action"
    expected = tmp_path / "one-shot"
    generation.export_candidate(source, expected)
    assert files(target) == files(expected)


def test_live_cache_authenticates_its_previous_head(tmp_path):
    source, target = tmp_path / "source", tmp_path / "trace"
    journal = RunStore(source).journal("solver")
    journal.append([], "context_evicted", {"note": "prefix"})
    exporter = generation.LiveTraceExporter()
    exporter.export(source, target)
    head = source / "solver/000000.json"
    head.chmod(0o600)
    head.write_bytes(b"changed")
    with pytest.raises(RecordError, match="head changed"):
        exporter.export(source, target)


def test_incremental_export_matches_complete_model_and_tool_trace(tmp_path):
    original = RunStore(tmp_path / "original")
    run_attempt(journal=original.journal("solver"),
        backend=Scripted(call("write_file", {"path": "note.txt", "content": "hello"}),
                         call("read_file", {"path": "note.txt"}), ModelReply("Finished.")),
        spec=AttemptSpec("trace-cache", "fixture", {"model": "scripted"}), tools=file_tools(),
        initial_messages=({"role": "user", "content": "write and read"},))
    source = RunStore(tmp_path / "live")
    exporter = generation.LiveTraceExporter()
    events = original.journal("solver").load()
    prefix = []
    for event in events:
        source.objects.publish(original.objects, event.artifacts)
        prefix.append(source.journal("solver").append(prefix, event.kind, dict(event.payload), event.artifacts))
        exporter.export(source.directory, tmp_path / "incremental")
    got = exporter.export(source.directory, tmp_path / "incremental", completed=True)
    expected = generation.export_candidate(original.directory, tmp_path / "one-shot")
    assert got == expected
    assert files(tmp_path / "incremental") == files(tmp_path / "one-shot")
    # A fresh observer and a distinct export destination do not reuse old state.
    assert generation.LiveTraceExporter().export(source.directory, tmp_path / "fresh", completed=True) == got
    assert exporter.export(source.directory, tmp_path / "another", completed=True) == got
    assert files(tmp_path / "fresh") == files(tmp_path / "another") == files(tmp_path / "one-shot")


def test_progress_never_reads_payload_objects_or_writes_trace_files(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "trace"
    journal = RunStore(source).journal("solver")
    events = []
    for kind, payload in (("model_requested", {"request": {"not": "read"}}),
                          ("model_settled", {"outcome": "reply", "record": {"not": "read"}}),
                          ("tool_requested", {}), ("terminal", {"reason": "final_action"})):
        events.append(journal.append(events, kind, payload))
    monkeypatch.setattr(generation, "_export_events", lambda *a, **kw: pytest.fail("full trace render"))
    exporter = generation.LiveProgressExporter()
    row = exporter.export(source, target, completed=True)
    assert row == {"status": "final_action", "turns": 1, "tool_calls": 1, "events": 4}
    assert not target.exists()
    monkeypatch.setattr(AttemptJournal, "load_after", lambda *a, **kw: pytest.fail("settled journal reread"))
    assert exporter.export(source, target) == row


def test_final_progress_uses_report_without_reopening_candidates():
    inventory = [{"run_key": "a", "task": "one"}, {"run_key": "b", "task": "two"}]
    observed = [{"run_key": "a", "status": "running", "turns": 5}]
    summary = {"candidates": [{"run_key": "a", "status": "pending_judgment"},
                              {"run_key": "b", "status": "no_submission"}]}
    rows = generation.final_progress(inventory, observed, summary)
    assert [r["status"] for r in rows] == ["pending_judgment", "no_submission"]
    assert rows[0]["turns"] == 5
    assert "turns" not in rows[1]  # Do not invent counts for unobserved events.
    assert observed[0]["status"] == "running"


def test_slow_progress_observer_cannot_hold_cleanup():
    from threading import Event

    stopped = Event()

    class Delayed:
        def join(self, *, timeout):
            assert stopped.is_set() and timeout == 2

        def is_alive(self):
            return True

    assert generation.stop_progress_observer(Delayed(), stopped) is False
