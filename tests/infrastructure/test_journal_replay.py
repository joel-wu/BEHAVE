"""Complete journal recovery without per-append history copies or event quotas."""

import builtins

import pytest

from behave.agent import journal as journal_module
from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import AttemptJournal, load_chain
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore


def test_seal_uses_existing_journal_configuration_and_preserves_export_bytes(tmp_path, monkeypatch):
    run = JournalDirectory(tmp_path / "run")
    journal = run.journal("custom")
    assert journal.seal() == (None, ())
    artifact = run.objects.put(b"shared artifact")
    events = []
    for index in range(3):
        events.append(journal.append(events, "event", {"index": index}, (artifact,)))

    def no_reopen(*args, **kwargs):
        pytest.fail("export must keep the configured journal, not construct a new view")

    monkeypatch.setattr(AttemptJournal, "__init__", no_reopen)
    monkeypatch.setattr(run, "journal", no_reopen)
    original_load = journal.load
    loads = []

    def load(**kwargs):
        loads.append(journal.directory)
        return original_load(**kwargs)

    monkeypatch.setattr(journal, "load", load)
    head, closure = journal.seal()
    expected = tuple(BlobRef.of(event.to_bytes()) for event in events)
    assert head == expected[-1]
    assert closure == (artifact, *expected)
    assert loads == [journal.directory]
    assert journal.seal() == (head, closure)
    copied = DirectoryStore(tmp_path / "copy")
    copied.publish(run.objects, closure)
    assert load_chain(copied, head) == tuple(events)


def test_cached_tail_audit_reads_artifacts_but_seal_does_not(tmp_path, monkeypatch):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "journal", store)
    first_ref, second_ref = store.put(b"first"), store.put(b"second")
    first = journal.append_after(None, "opened", {}, (first_ref,))
    second = journal.append_after(first, "continued", {}, (second_ref,))
    real_read, seen = store.read, []

    def read(ref):
        seen.append(ref)
        return real_read(ref)

    monkeypatch.setattr(store, "read", read)
    assert journal.load_after(first) == (second,)
    assert seen == [second_ref]
    assert journal.load_after(second) == ()
    seen.clear()
    journal.seal()
    assert seen == []


def test_cached_tail_rejects_changed_head_and_gaps(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "journal", store)
    first = journal.append_after(None, "opened", {})
    second = journal.append_after(first, "continued", {})
    with pytest.raises(RecordError) as error:
        journal.append_after(first, "duplicate", {})
    assert error.value.code == "journal-conflict"
    target = journal.directory / "000000.json"
    target.chmod(0o600)
    target.write_bytes(second.to_bytes())
    with pytest.raises(RecordError) as error:
        journal.load_after(first)
    assert error.value.code == "journal-conflict"
    target.unlink()
    with pytest.raises(RecordError) as error:
        journal.load_after(second)
    assert error.value.code == "journal-sequence-invalid"


def test_seal_checks_event_metadata_before_publishing(tmp_path, monkeypatch):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "journal", store)
    artifact = store.put(b"artifact")
    first = journal.append((), "initialized", {}, (artifact,))
    journal.append((first,), "terminal", {})
    target = journal.directory / "000000.json"
    target.chmod(0o600)
    target.write_bytes(b"corrupt!")
    monkeypatch.setattr(store, "put", lambda *args: pytest.fail("invalid history must fail before export writes"))
    with pytest.raises(RecordError):
        journal.seal()


def test_live_append_cursor_and_seal_do_not_read_payloads(tmp_path, monkeypatch):
    store = DirectoryStore(tmp_path / "objects")
    artifact = store.put(b"large producer payload")
    journal = AttemptJournal(tmp_path / "journal", store)
    read = store.read
    def metadata_only(ref):
        assert ref != artifact, "live bookkeeping must not reread payloads"
        return read(ref)
    monkeypatch.setattr(store, "read", metadata_only)
    tape = Tape(journal)
    tape.record("created", {}, (artifact,))
    tape.record("finished", {}, (artifact,))
    head, _ = journal.seal()
    assert len(Tape(journal).events) == 2
    monkeypatch.setattr(store, "read", read)
    target = store.directory / artifact.sha256
    target.chmod(0o600)
    target.write_bytes(b"damaged")
    with pytest.raises(RecordError):
        load_chain(store, head)


@pytest.mark.parametrize("phase", ["solver", "report"])
def test_directory_seal_delegates_to_the_same_journal(tmp_path, monkeypatch, phase):
    from behave.task_execution.run_store import RunStore

    seen = []
    result = (BlobRef.of(b"fixture head"), ())

    def seal(journal):
        seen.append(journal.directory)
        return result

    monkeypatch.setattr(AttemptJournal, "seal", seal)
    run = RunStore(tmp_path / "run")
    assert run.seal(phase) is result
    assert seen == [run.directory / phase]


def test_large_event_roundtrips_through_live_and_sealed_history(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "events", store)
    first = journal.append((), "initialized", {"text": "x" * ((1 << 20) + 1)})
    assert len(first.to_bytes()) > 1 << 20
    store.put(first.to_bytes())
    # Appending reads the previous event too; that path must not keep the old cap.
    second = journal.append((first,), "terminal", {})
    head = store.put(second.to_bytes())
    assert journal.load() == load_chain(store, head) == (first, second)


def test_no_event_cap_preserves_live_sealed_and_recovery_checks(tmp_path):
    run = JournalDirectory(tmp_path / "run")
    journal = run.journal("shared")
    artifact = run.objects.put(b"evidence")
    events = []
    for index in range(3):
        events.append(journal.append(events, "event", {"index": index}, (artifact,)))
    head, _ = journal.seal()
    assert journal.load() == load_chain(run.objects, head) == tuple(events)
    # Removing the count cap does not permit a missing/corrupt dependency.
    target = run.objects.directory / artifact.sha256
    target.chmod(0o600)
    target.write_bytes(b"damaged")
    with pytest.raises(RecordError):
        journal.load()
    with pytest.raises(RecordError):
        load_chain(run.objects, head)


def test_append_and_both_readers_share_the_complete_history(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "events", store)
    first = journal.append((), "initialized", {})
    store.put(first.to_bytes())
    second = journal.append((first,), "terminal", {})
    head = store.put(second.to_bytes())
    third = journal.append((first, second), "extra", {})
    head = store.put(third.to_bytes())
    assert journal.load() == (first, second, third)
    assert AttemptJournal(journal.directory, store).load() == (first, second, third)
    assert load_chain(store, head) == (first, second, third)


def test_journal_name_sort_handles_event_numbers_wider_than_the_padding(tmp_path, monkeypatch):
    # Exercise the loader's ordering on the width boundary without creating a
    # million files. The normal small history still goes through actual reads.
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "events", store)
    first = journal.append((), "initialized", {})
    second = journal.append((first,), "terminal", {})

    def checked_sort(names, *, key):
        assert builtins.sorted(["1000000.json", "999999.json"], key=key) == ["999999.json", "1000000.json"]
        return builtins.sorted(names, key=key)

    monkeypatch.setattr(journal_module, "sorted", checked_sort, raising=False)
    assert journal.load() == (first, second)


def test_tape_append_reuses_its_history_without_copying_or_scanning_it(tmp_path):
    class IndexedHistory(list):
        def __iter__(self):
            pytest.fail("appending must not copy or scan the history")

    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    tape = Tape(journal)
    history = tape.events = IndexedHistory()
    first = tape.record("initialized", {})
    assert tape.begin("model_requested", {"call_id": "a:0:0"}) is True
    last = tape.record("model_settled", {"call_id": "a:0:0"})
    tape.finish()
    assert tape.events is history and len(history) == 3
    assert journal.load()[0] == first and journal.load()[-1] == last


def test_tape_cold_recovery_consumes_existing_results_and_unknown_does_not_restart(tmp_path, monkeypatch):
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    live = Tape(journal)
    assert live.begin("model_requested", {"call_id": "first"}) is True
    settled = live.record("model_settled", {"call_id": "first"})
    assert live.begin("model_requested", {"call_id": "pending"}) is True
    restored = Tape(journal)
    monkeypatch.setattr(journal, "append", lambda *a, **kw: pytest.fail("old requests must not be appended"))
    assert restored.begin("model_requested", {"call_id": "first"}) is False
    assert restored.take("model_settled") == settled
    assert restored.begin("model_requested", {"call_id": "pending"}) is False
    with pytest.raises(AttemptInterrupted, match="attempt-outcome-unknown"):
        restored.take("model_settled")


@pytest.mark.parametrize("action", ["payload", "kind", "finish"])
def test_tape_recovery_rejects_divergent_or_unconsumed_history(tmp_path, action):
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "objects"))
    Tape(journal).record("initialized", {"value": 1})
    restored = Tape(journal)
    with pytest.raises(RecordError):
        if action == "payload":
            restored.record("initialized", {"value": 2})
        elif action == "kind":
            restored.take("terminal")
        else:
            restored.finish()
