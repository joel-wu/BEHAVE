"""Explicit interrupted-update recovery, with no model or optimizer calls."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from test_native_updates import save_fixture
from test_slime_application import configured

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.train.slime_data import ScoredDataSource
from behave.train.trainer import NativeAnchor, active_events


def append(source, kind, step, **extra):
    return source.journal.append(source.journal.load(), kind,
        {"round": step, "binding": source.identity.as_dict(), **extra})


@pytest.mark.parametrize("ready", ["round_scored", "raw_batch_ready"])
def test_explicit_abandon_preserves_history_and_restarts_after_last_save(tmp_path, ready):
    source = configured(tmp_path)
    save_fixture(source, 0)
    source.native.ready_kind = ready
    first = source.run.objects.put(b"first scored receipt")
    later = source.run.objects.put(b"later policy receipt")
    append(source, ready, 1, receipt=first.as_dict())
    append(source, ready, 2, receipt=later.as_dict())
    append(source, "round_started", 3)
    original = source.journal.load()
    with pytest.raises(ValueError, match="pending"):
        source.load(0)
    with pytest.raises(ValueError, match="stopped"):
        source.native.abandon(reason="node restart", workers_stopped=False)
    assert source.journal.load() == original
    restored = source.native.abandon(reason="node restart", workers_stopped=True)
    assert restored["next_round"] == 1 and restored["resume_after"] == 0
    assert restored["reuse_receipt"] == (first.as_dict() if ready == "round_scored" else None)
    assert source.journal.load()[:-1] == original
    source.load(0)
    source.native.require_next(source.journal.load(), 1, None)
    with pytest.raises(ValueError, match="out of order"):
        source.native.require_next(source.journal.load(), 2, None)
    assert [e.payload["round"] for e in active_events(source.journal.load())
            if e.kind == "round_scored"] == [0]
    with pytest.raises(ValueError, match="no interrupted"):
        source.native.abandon(reason="duplicate request", workers_stopped=True)


def test_before_first_save_restarts_from_hf_without_erasing_rollouts(tmp_path):
    source = configured(tmp_path)
    append(source, "round_started", 0)
    result = source.native.abandon(reason="rollout worker stopped", workers_stopped=True)
    assert result["resume_after"] == -1 and result["next_round"] == 0
    assert result["checkpoint"] is result["reuse_receipt"] is None
    source.load(-1)
    source.native.require_next(source.journal.load(), 0, None)
    save_fixture(source, 0)
    head, _ = source.journal.seal()
    assert NativeAnchor.load(source.run.objects, head).step == 0
    assert [e.kind for e in source.journal.load()][:2] == ["round_started", "round_abandoned"]


def test_wrong_checkpoint_does_not_abandon_the_tail(tmp_path):
    source = configured(tmp_path)
    save_fixture(source, 0)
    append(source, "round_started", 1)
    before = source.journal.load()
    (Path(source.args.load) / "latest_checkpointed_iteration.txt").write_text("1")
    with pytest.raises(ValueError, match="marker"):
        source.native.abandon(reason="wrong load directory", workers_stopped=True)
    assert source.journal.load() == before


def test_recovered_full_batch_uses_prepared_data_without_rollout_or_rejudging(tmp_path, monkeypatch):
    source = configured(tmp_path)
    endpoint = source.binding["endpoint"]
    row = {"schema": "behave.slime-scored-round/1", "round": 0,
        "objects": str(tmp_path / "scored"), "head": BlobRef.of(b"head").as_dict(),
        "manifest": source.binding["manifest"], "endpoint": endpoint,
        "policy": endpoint["tokens"]["checkpoint"], "prepared_batch": str(tmp_path / "batch.json")}
    receipt = source.run.objects.put(canonical_json(row))
    append(source, "round_scored", 0, receipt=receipt.as_dict())
    source.native.abandon(reason="optimizer interrupted", workers_stopped=True)
    source = ScoredDataSource(source.args)
    source.load(-1)
    monkeypatch.setattr("behave.train.slime_data.run_service", lambda *a, **kw: pytest.fail("new rollout"))
    monkeypatch.setattr("behave.train.slime_data.engine_versions", lambda *a, **kw: pytest.fail("serving call"))
    monkeypatch.setattr("behave.train.slime_data.round_plan", lambda *a, **kw:
                        SimpleNamespace(to_bytes=lambda: b"same generating plan"))

    def prepared(path, **kwargs):
        assert path == row["prepared_batch"]
        assert kwargs["head"].as_dict() == row["head"]
        assert kwargs["plan"] == BlobRef.of(b"same generating plan")
        return SimpleNamespace(candidates=({"retained_turns": 2},), payloads=({},),
                               rollout_output=lambda: "original native targets")

    monkeypatch.setattr("behave.train.slime_data.load_batch", prepared)
    assert source.generate(0) == "original native targets"
    assert source.journal.load()[-1].kind == "round_scored"
    assert [e.payload["round"] for e in active_events(source.journal.load()) if e.kind == "round_scored"] == [0]


def test_recovery_cli_records_the_operator_decision(tmp_path, capsys):
    from behave.train.slime_recovery import main

    source = configured(tmp_path)
    (tmp_path / "configuration.json").write_bytes(canonical_json({"checkpoint_interval": 1}))
    append(source, "round_started", 0)
    assert main(["--plan", str(tmp_path), "--load", source.args.load, "--updates", "5",
                 "--reason", "confirmed process exit", "--workers-stopped"]) == 0
    assert '"next_round": 0' in capsys.readouterr().out
    assert source.journal.load()[-1].payload["reason"] == "confirmed process exit"
