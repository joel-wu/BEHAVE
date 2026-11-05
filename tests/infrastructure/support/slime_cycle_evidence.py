"""Recheck a live-cycle fixture, including after its scratch tree is exported."""

from collections import Counter
from pathlib import Path

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.train.slime_launch import SlimeRecipe
from behave.train.slime_rollout import from_training_report
from behave.train.training_data import NativeChatVerifier


def scored_rounds(root: Path, *, manifest: BlobRef, groups: tuple[str, ...],
                  recipe: SlimeRecipe) -> dict:
    """Validate retained bytes without starting a model, scorer or optimizer.

    The caller supplies the trusted fixture and expected membership. Receipts
    retain original absolute scratch paths; the exported content-addressed
    stores are resolved from the fixture layout, never by rewriting receipts.
    """
    events = JournalDirectory(root / "rounds/native").journal("rounds").load()
    for kind in ("round_started", "round_scored", "checkpoint_saved"):
        assert [e.payload["round"] for e in events if e.kind == kind] == [0, 1, 2], kind
    native = DirectoryStore(root / "rounds/native/objects")
    rewards, credits, retained = [], [], []
    for event in events:
        if event.kind != "round_scored":
            continue
        # Journal payloads are immutable internal records, not JSON wire dicts.
        receipt = BlobRef.from_dict(thaw_json(event.payload["receipt"]))
        row = parse_json(read_verified(native, receipt))
        assert row["round"] == event.payload["round"]
        assert BlobRef.from_dict(row["manifest"]) == manifest
        objects = DirectoryStore(root / f"rounds/round_{row['round']:08d}/objects")
        batch = from_training_report(objects, BlobRef.from_dict(row["head"]),
            approved_manifest=manifest,
            verifier=NativeChatVerifier(LocalChatEndpoint.from_dict(row["endpoint"])), recipe=recipe)
        assert Counter(c["group_id"] for c in batch.candidates) == Counter(groups)
        assert len(batch.payloads) == event.payload["retained_turns"]
        rewards.append([c["reward"] for c in batch.candidates])
        credits.append([c["credit"] for c in batch.candidates])
        retained.append(len(batch.payloads))
    return {"commits": [0, 1, 2], "rewards": rewards, "credits": credits, "retained_turns": retained}
