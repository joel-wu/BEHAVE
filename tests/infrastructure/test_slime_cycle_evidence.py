"""Fixture receipt boundaries are testable without a three-hour GPU cycle."""

from types import SimpleNamespace

import pytest
from support import slime_cycle_evidence as evidence

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.providers.native_tokens import NativeChatTokens
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.train.slime_launch import preset


@pytest.fixture
def cycle(tmp_path, monkeypatch):
    manifest = BlobRef.of(b"approved")
    endpoint = LocalChatEndpoint("http://127.0.0.1:31000/v1/chat/completions", "fixture", "test",
        tokens=NativeChatTokens("sglang-slime", *(BlobRef.of(b"initial") for _ in range(3))))
    journal = JournalDirectory(tmp_path / "rounds/native").journal("rounds")
    objects = DirectoryStore(tmp_path / "rounds/native/objects")
    for index in range(3):
        receipt = objects.put(canonical_json({"round": index, "manifest": manifest.as_dict(),
            "head": BlobRef.of(str(index).encode()).as_dict(), "endpoint": endpoint.as_dict(),
            "objects": "/no-longer-present/old-scratch"}))
        for kind, extra in (("round_started", {}), ("round_scored", {
                "receipt": receipt.as_dict(), "retained_turns": 5}), ("checkpoint_saved", {})):
            journal.append(journal.load(), kind, {"round": index, **extra})
    calls = []

    def read_batch(reader, head, **kwargs):
        calls.append((reader.directory, head, kwargs))
        return SimpleNamespace(candidates=({"group_id": "task", "reward": 0, "credit": -1},
                                          {"group_id": "task", "reward": 1, "credit": 1}),
                               payloads=(None,) * 5)

    monkeypatch.setattr(evidence, "from_training_report", read_batch)
    return tmp_path, manifest, journal, calls


def test_frozen_journal_and_exported_store_keep_strict_wire_parser(cycle):
    root, manifest, journal, calls = cycle
    payload = journal.load()[1].payload["receipt"]
    with pytest.raises(RecordError):
        BlobRef.from_dict(payload)  # Wire validation is intentionally unchanged.
    assert BlobRef.from_dict(thaw_json(payload))
    result = evidence.scored_rounds(root, manifest=manifest, groups=("task", "task"), recipe=preset("grpo"))
    assert result == {"commits": [0, 1, 2], "rewards": [[0, 1]] * 3,
                      "credits": [[-1, 1]] * 3, "retained_turns": [5] * 3}
    assert [c[0] for c in calls] == [root / f"rounds/round_{i:08d}/objects" for i in range(3)]
    assert all(c[2]["approved_manifest"] == manifest for c in calls)


@pytest.mark.parametrize("fault", ["manifest", "group", "missing-round"])
def test_evidence_recheck_does_not_accept_incomplete_or_changed_cycle(cycle, fault):
    root, manifest, journal, _ = cycle
    groups = ("task", "task")
    if fault == "manifest":
        manifest = BlobRef.of(b"different")
    elif fault == "group":
        groups = ("task", "other")
    else:
        journal.append(journal.load(), "round_started", {"round": 3})
    with pytest.raises(AssertionError):
        evidence.scored_rounds(root, manifest=manifest, groups=groups, recipe=preset("grpo"))
