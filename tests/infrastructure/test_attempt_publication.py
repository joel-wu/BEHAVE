from __future__ import annotations

from dataclasses import replace

import pytest

from behave.agent.attempt import AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.loop import run_attempt
from behave.agent.model import ModelReply, ToolCall
from behave.agent.publication import publish_attempt
from behave.agent.tools import file_tools
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef, FileData, FileRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task
from behave.task_execution.capture import FrozenSubmission, load_submission
from behave.task_execution.solver import run_solver
from behave.task_execution.submission import ArtifactSelection, SubmissionRequest


class Broker:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return next(self.replies)


def test_completed_attempt_publication_retries_without_model_or_tool_execution(tmp_path, monkeypatch):
    source = AttemptJournal(tmp_path / "local/events", DirectoryStore(tmp_path / "local/objects"))
    destination = AttemptJournal(tmp_path / "nfs/events", DirectoryStore(tmp_path / "nfs/objects"))
    spec = AttemptSpec("publication", "role", {})
    backend = Broker([ModelReply("", (ToolCall("w", "write_file", {"path": "a", "content": "hello"}),)),
                      ModelReply("done")])
    result = run_attempt(journal=source, backend=backend, spec=spec,
                         initial_messages=({"role": "user", "content": "work"},), tools=file_tools())
    original = destination.append

    def interrupt(*args, **kwargs):
        event = original(*args, **kwargs)
        if event.sequence == 2:
            raise OSError("publication interrupted")
        return event

    monkeypatch.setattr(destination, "append", interrupt)
    with pytest.raises(OSError, match="publication interrupted"):
        publish_attempt(source, destination)
    assert destination.load()[-1].kind != "terminal"
    monkeypatch.setattr(destination, "append", original)
    assert publish_attempt(source, destination, workers=8) == result.head
    assert publish_attempt(source, destination) == result.head
    assert [e.to_bytes() for e in destination.load()] == [e.to_bytes() for e in source.load()]
    assert backend.calls == 2
    recovered = run_attempt(journal=destination, backend=Broker([]), spec=spec,
                            initial_messages=({"role": "user", "content": "work"},), tools=file_tools())
    assert recovered == result


def test_publication_rejects_active_attempt_and_conflicting_destination(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    source = AttemptJournal(tmp_path / "source", store)
    destination = AttemptJournal(tmp_path / "dest", store)
    first = source.append((), "initialized", {"version": 1})
    with pytest.raises(RecordError, match="completed"):
        publish_attempt(source, destination)
    source.append((first,), "terminal", {})
    destination.append((), "initialized", {"version": 2})
    with pytest.raises(RecordError, match="prefix"):
        publish_attempt(source, destination)


def test_publication_appends_to_one_history_instead_of_copying_each_prefix(tmp_path, monkeypatch):
    source = AttemptJournal(tmp_path / "local/events", DirectoryStore(tmp_path / "local/objects"))
    destination = AttemptJournal(tmp_path / "nfs/events", DirectoryStore(tmp_path / "nfs/objects"))
    events = []
    for index in range(4):
        events.append(source.append(events, "event", {"index": index}))
    events.append(source.append(events, "terminal", {}))
    destination.append((), "event", {"index": 0})
    histories, lengths = [], []
    original = destination.append

    def observe(expected, *args, **kwargs):
        histories.append(expected)
        lengths.append(len(expected))
        return original(expected, *args, **kwargs)

    monkeypatch.setattr(destination, "append", observe)
    assert publish_attempt(source, destination) == BlobRef.of(events[-1].to_bytes())
    assert lengths == [1, 2, 3, 4]
    assert all(history is histories[0] for history in histories)
    assert [event.to_bytes() for event in destination.load()] == [event.to_bytes() for event in events]


def test_artifact_copy_failure_does_not_publish_terminal_and_retry_preserves_bytes(tmp_path, monkeypatch):
    source = AttemptJournal(tmp_path / "local/events", DirectoryStore(tmp_path / "local/objects"))
    destination = AttemptJournal(tmp_path / "nfs/events", DirectoryStore(tmp_path / "nfs/objects"))
    ref = source.objects.put(b"artifact")
    first = source.append((), "initialized", {}, (ref,))
    terminal = source.append((first,), "terminal", {})
    original = destination.objects.put

    def interrupted(data):
        original(data)
        raise OSError("fixture artifact copy interrupted")

    monkeypatch.setattr(destination.objects, "put", interrupted)
    with pytest.raises(OSError, match="fixture artifact copy interrupted"):
        publish_attempt(source, destination)
    assert destination.load() == ()
    monkeypatch.setattr(destination.objects, "put", original)
    assert publish_attempt(source, destination) == BlobRef.of(terminal.to_bytes())
    assert destination.load() == (first, terminal)
    assert destination.objects.read(ref) == b"artifact"


def test_live_journal_appends_do_not_reread_every_previous_artifact(tmp_path, monkeypatch):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "events", store)
    assert journal.load() == ()
    refs = tuple(store.put(str(i).encode()) for i in range(40))
    calls = []
    original = store.read

    def read(ref):
        calls.append(ref)
        return original(ref)

    monkeypatch.setattr(store, "read", read)
    events = ()
    for ref in refs:
        events = (*events, journal.append(events, "event", {}, (ref,)))
    # Owned producers already published these immutable bytes. Appending the
    # references must not read them back; cold recovery authenticates them all.
    assert calls == []
    assert journal.load() == events
    assert calls == list(refs)


def test_protected_task_files_cannot_be_edited_deleted_or_changed_by_restore(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    protected = Workspace(store, read_only_paths=("task.toml",))
    data = b"original"
    protected.seed((FileData(FileRef("task.toml", BlobRef.of(data)), data),))
    for operation in (lambda: protected.write("task.toml", b"forged"), lambda: protected.delete("task.toml")):
        with pytest.raises(RecordError) as error:
            operation()
        assert error.value.code == "workspace-read-only"
    different = Workspace(store)
    different.write("task.toml", b"forged")
    ref, _ = different.checkpoint()
    with pytest.raises(RecordError) as error:
        protected.restore(ref)
    assert error.value.code == "workspace-read-only-changed"
    assert protected.read("task.toml") == b"original"


def test_removed_file_tool_does_not_edit_inputs_and_final_turn_submission_wins_budget_stop(publication, tmp_path):
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    request = SubmissionRequest(None, None)
    backend = Broker([
        ModelReply("", (ToolCall("edit", "write_file", {"path": "spec.md", "content": "changed"}),)),
        ModelReply("", (ToolCall("submit", "submit", request.as_arguments()),)),
    ])
    got = run_solver(task=public, spec=AttemptSpec("last-turn", "solver", {}, max_turns=2),
                     backend=backend, journal=journal)
    assert got.attempt.turns == 2 and got.attempt.reason == "final_action"
    assert got.status == "awaiting_judgment"  # Explicit all-absent != no submission.
    submission = load_submission(journal.objects, got.submission)
    workspace = Workspace(journal.objects)
    workspace.restore(submission.workspace)
    assert workspace.read("spec.md") == b"PUBLIC: count the accepted transfers."
    assert public.contract_path not in {file.path for file in workspace.snapshot().files}
    assert submission.behavior.selection is None and submission.rtl.selection is None


def test_capture_rejects_claimed_bytes_or_missing_files_that_disagree_with_snapshot(publication, tmp_path):
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    spec = AttemptSpec("capture", "solver", {})
    selection = ArtifactSelection(("model.py",), "model.py", "M")
    request = SubmissionRequest(selection, None)
    backend = Broker([ModelReply("", (ToolCall("submit", "submit", request.as_arguments()),))])
    outcome = run_solver(task=public, spec=spec, backend=backend, journal=journal)
    original = load_submission(journal.objects, outcome.submission)
    assert original.behavior.missing == ("model.py",)
    forged_file = FileRef("model.py", journal.objects.put(b"class M: pass"))
    component = replace(original.behavior, files=(forged_file,), missing=())
    forged = replace(original, behavior=component)
    with pytest.raises(RecordError, match="workspace"):
        load_submission(journal.objects, journal.objects.put(forged.to_bytes()))
    row = parse_json(original.to_bytes())
    row["passed"] = True
    with pytest.raises(RecordError):
        FrozenSubmission.from_bytes(canonical_json(row))


def test_capture_shares_one_snapshot_and_later_edits_do_not_change_submission(publication, tmp_path, monkeypatch):
    from behave.task_execution import capture

    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    original_files = {"model.py": b"class M: pass", "top.sv": b"module top; endmodule", "tb.py": b"# original TB"}
    for path, data in original_files.items():
        workspace.write(path, data)
    request = SubmissionRequest(
        ArtifactSelection(("model.py", "helper.py"), "model.py", "M"),
        ArtifactSelection(("top.sv",), "top.sv", "top"),
        ArtifactSelection(("tb.py",), "tb.py", "test_top"),
    )
    snapshots = []
    component = capture._component

    def observe(snapshot, selection):
        snapshots.append(snapshot)
        return component(snapshot, selection)

    monkeypatch.setattr(capture, "_component", observe)
    result = capture.submission_tool(public).handler(workspace, request.as_arguments())
    assert result.terminal
    assert len(snapshots) == 3 and all(snapshot is snapshots[0] for snapshot in snapshots)
    reference = BlobRef.from_dict(dict(result.content["submission"]))

    workspace.write("model.py", b"# edited after submission")
    workspace.write("helper.py", b"# created after submission")
    workspace.delete("top.sv")
    frozen = load_submission(workspace.store, reference)
    assert frozen.behavior.missing == ("helper.py",)
    assert frozen.behavior.artifact is None
    assert frozen.rtl.artifact.entry_symbol == "top"
    assert frozen.testbench.artifact.entry_symbol == "test_top"
    for captured in (frozen.behavior, frozen.rtl, frozen.testbench):
        for file in captured.files:
            assert workspace.store.read(file.content) == original_files[file.path]


@pytest.mark.parametrize("mutation", ["bytes", "missing"])
def test_capture_record_comparison_rejects_false_contents_and_false_absence(publication, tmp_path, mutation):
    from behave.task_execution.capture import submission_tool

    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    workspace.write("model.py", b"class M: pass")
    request = SubmissionRequest(ArtifactSelection(("model.py",), "model.py", "M"), None)
    result = submission_tool(public).handler(workspace, request.as_arguments())
    reference = BlobRef.from_dict(dict(result.content["submission"]))
    original = load_submission(workspace.store, reference)
    if mutation == "bytes":
        file = FileRef("model.py", workspace.store.put(b"# different content"))
        component = replace(original.behavior, files=(file,))
    else:
        component = replace(original.behavior, files=(), missing=("model.py",))
    forged = workspace.store.put(replace(original, behavior=component).to_bytes())
    with pytest.raises(RecordError) as found:
        load_submission(workspace.store, forged)
    assert found.value.code == "submission-workspace-mismatch"
