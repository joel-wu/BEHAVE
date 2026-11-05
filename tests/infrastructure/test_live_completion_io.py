"""Owned completion reads metadata/necessary records, not all payloads again."""

from types import SimpleNamespace

import pytest

from behave.agent.journal import AttemptJournal
from behave.agent.process_worker import completed_attempt
from behave.agent.trajectory import trajectory
from behave.core.storage import DirectoryStore


def test_owned_terminal_and_accounting_do_not_reread_unrelated_artifacts(tmp_path, monkeypatch):
    store = DirectoryStore(tmp_path / "objects")
    journal = AttemptJournal(tmp_path / "events", store)
    artifact = store.put(b"previous large workspace snapshot")
    event = journal.append((), "initialized", {}, (artifact,))
    journal.append((event,), "terminal", {
        "reason": "submitted", "turns": 0, "workspace": artifact.as_dict(), "final_action": None,
    }, (artifact,))
    read = store.read

    def no_payload(ref):
        assert ref != artifact, "completion must not audit old workspace payloads"
        return read(ref)

    monkeypatch.setattr(store, "read", no_payload)
    assert completed_attempt(SimpleNamespace(max_turns=60), journal, namespace="solver").turns == 0
    assert not trajectory(journal, verify_artifacts=False).turns
    with pytest.raises(AssertionError, match="old workspace"):
        trajectory(journal)  # Explicit full-artifact audit remains available.
