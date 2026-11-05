"""Only receipted private copies may be released, never live or retained models."""

import json
from pathlib import Path

import pytest

from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError
from behave.runtime import models as staging


@pytest.fixture
def model(tmp_path):
    source, target, receipt = tmp_path / "retained", tmp_path / "private", tmp_path / "state/copy.json"
    (source / "sub").mkdir(parents=True)
    (source / "empty").mkdir()
    (source / "config.json").write_text("{}")
    (source / "sub/weights").write_bytes(b"weights")
    return source, target, receipt


def release(model, *, cleanup=CleanupState.COMPLETE):
    _, target, receipt = model
    return staging.release_model(target, receipt=receipt, cleanup=cleanup)


def staged(model):
    source, target, receipt = model
    assert staging.stage_model(source, target, receipt=receipt) == target
    return json.loads(receipt.read_text())


def test_verified_copy_release_preserves_source_and_records_inventory(model, monkeypatch):
    source, target, receipt = model
    record = staged(model)
    assert record["state"] == "verified"
    assert (target / "sub/weights").read_bytes() == b"weights"
    unlink = Path.unlink

    def recorded_unlink(path, *args, **kwargs):
        if path.is_relative_to(target):
            evidence = json.loads(receipt.read_text())
            assert evidence["state"] == "releasing"
            assert set(evidence["release_inventory"]) == {"config.json", "sub/weights"}
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", recorded_unlink)
    result = release(model)
    assert result["state"] == "released"
    assert not target.exists() and (source / "sub/weights").read_bytes() == b"weights"
    assert release(model) == result  # Idempotent after completion.
    assert receipt.exists()


@pytest.mark.parametrize("cleanup", [CleanupState.UNKNOWN, CleanupState.INCOMPLETE, "complete", None])
def test_uncertain_or_untyped_cleanup_keeps_model(model, cleanup):
    _, target, receipt = model
    staged(model)
    with pytest.raises((RecordError, TypeError)):
        release(model, cleanup=cleanup)
    assert (target / "sub/weights").read_bytes() == b"weights"
    assert json.loads(receipt.read_text())["state"] == "verified"


@pytest.mark.parametrize("damage", ["source", "local", "extra", "missing", "directory", "host", "symlink"])
def test_changed_or_unowned_content_cannot_be_deleted(model, monkeypatch, damage):
    source, target, receipt = model
    staged(model)
    if damage in {"source", "local"}:
        ((source if damage == "source" else target) / "sub/weights").write_bytes(b"changed")
    elif damage == "extra":
        (target / "new-file").write_bytes(b"not a staged model member")
    elif damage == "missing":
        (target / "sub/weights").rename(target.parent / "retained-missing-file")
    elif damage == "directory":
        (target / "sub").rename(target / "original-sub")
        (target / "sub").mkdir()
    elif damage == "host":
        monkeypatch.setattr(staging, "_transport_host", lambda: "different-host:boot")
    elif damage == "symlink":
        (target / "sub/weights").rename(target.parent / "retained-member")
        (target / "sub/weights").symlink_to(source / "sub/weights")
    with pytest.raises((ValueError, OSError)):
        release(model)
    assert (target / "config.json").exists()
    assert json.loads(receipt.read_text())["state"] == "verified"


def test_partial_unlink_can_resume_without_recopying(model, monkeypatch):
    _, target, receipt = model
    staged(model)
    unlink = Path.unlink

    def fail_second(path, *args, **kwargs):
        if path == target / "sub/weights":
            raise OSError("fixture unlink interruption")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_second)
        with pytest.raises(OSError, match="unlink interruption"):
            release(model)
    assert not (target / "config.json").exists()
    assert json.loads(receipt.read_text())["state"] == "releasing"
    assert release(model)["state"] == "released"
    assert not target.exists()


def test_failed_release_receipt_does_not_unlink(model, monkeypatch):
    _, target, _ = model
    staged(model)
    monkeypatch.setattr(staging, "write_json", lambda *a, **kw: (_ for _ in ()).throw(OSError("receipt failure")))
    with pytest.raises(OSError, match="receipt failure"):
        release(model)
    assert (target / "config.json").exists() and (target / "sub/weights").exists()


def test_corrupt_local_readback_is_retained_and_not_verified(model, monkeypatch):
    source, target, receipt = model
    monkeypatch.setattr(staging, "digest", lambda path: ("bad digest", 2))
    with pytest.raises(ValueError, match="verification failed"):
        staging.stage_model(source, target, receipt=receipt)
    assert json.loads(receipt.read_text())["state"] == "interrupted"
    with pytest.raises(ValueError, match="unverified"):
        release(model)
    assert target.exists()


def test_source_file_alias_is_copied_not_linked(model):
    source, target, receipt = model
    alias = source.parent / "blob"
    (source / "sub/weights").rename(alias)
    (source / "sub/weights").symlink_to(alias)
    staged(model)
    assert not (target / "sub/weights").is_symlink()
    assert json.loads(receipt.read_text())["files"]["sub/weights"]["source"] == str(alias)
    release(model)
    assert alias.read_bytes() == b"weights" and (source / "sub/weights").is_symlink()


def test_source_directory_alias_is_not_followed(model):
    source, target, receipt = model
    (source / "alias").symlink_to(source / "sub", target_is_directory=True)
    with pytest.raises(ValueError, match="unlinked"):
        staging.stage_model(source, target, receipt=receipt)
    assert not target.exists()


@pytest.mark.parametrize("where", ["source", "destination"])
def test_receipt_cannot_be_inside_model(model, where):
    source, target, _ = model
    receipt = (source if where == "source" else target) / "receipt.json"
    with pytest.raises(ValueError, match="separate"):
        staging.stage_model(source, target, receipt=receipt)
    assert not target.exists()


def test_receipt_parent_alias_cannot_write_into_retained_model(model):
    source, target, receipt = model
    receipt.parent.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="unlinked"):
        staging.stage_model(source, target, receipt=receipt)
    assert not (source / receipt.name).exists() and not target.exists()


def test_release_refuses_replaced_receipt_parent(model):
    _, target, receipt = model
    staged(model)
    saved = receipt.parent.with_name("original-state")
    receipt.parent.rename(saved)
    receipt.parent.symlink_to(saved, target_is_directory=True)
    with pytest.raises(ValueError, match="unlinked"):
        release(model)
    assert (target / "sub/weights").exists()


def test_staging_never_overwrites_existing_copy_or_receipt(model):
    source, target, receipt = model
    original = staged(model)
    with pytest.raises(ValueError, match="already exists"):
        staging.stage_model(source, target, receipt=receipt)
    with pytest.raises(FileExistsError):
        staging.stage_model(source, target, receipt=receipt.with_name("second.json"))
    assert json.loads(receipt.read_text()) == original


def test_one_source_payload_read_and_no_payload_read_during_release(model, monkeypatch):
    source, target, _ = model
    open_file, opens = staging.os.open, []

    def observed_open(path, *args, **kwargs):
        if Path(path).is_relative_to(source):
            opens.append(Path(path))
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(staging.os, "open", observed_open)
    staged(model)
    assert sorted(opens) == sorted([source / "config.json", source / "sub/weights"])
    monkeypatch.setattr(staging, "digest", lambda path: pytest.fail("release must not reread weights"))
    release(model)
    assert len(opens) == 2 and not target.exists()


@pytest.mark.parametrize("kind", ["file", "directory", "destination", "source"])
def test_tampered_receipt_cannot_escape_private_model(model, tmp_path, kind):
    source, target, receipt = model
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    record = staged(model)
    if kind == "file":
        record["files"]["../outside"] = record["files"]["config.json"]
    elif kind == "directory":
        record["directories"][".."] = staging._directory(tmp_path)
    elif kind == "destination":
        record["destination"] = str(tmp_path)
    else:
        record["files"]["config.json"]["source"] = str(target / "config.json")
        record["files"]["config.json"]["source_identity"] = staging._identity(target / "config.json")
    receipt.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        release(model)
    assert outside.read_bytes() == b"keep"
    assert (target / "config.json").exists() and (source / "config.json").exists()


def test_source_mutation_during_copy_is_not_verified(model, monkeypatch):
    source, target, receipt = model
    digest = staging.digest

    def changed(path):
        result = digest(path)
        (source / path.relative_to(target)).write_bytes(b"mutation")
        return result

    monkeypatch.setattr(staging, "digest", changed)
    with pytest.raises(ValueError, match="changed during staging|verification failed"):
        staging.stage_model(source, target, receipt=receipt)
    assert json.loads(receipt.read_text())["state"] == "interrupted"
    assert target.exists()


def test_replaced_directory_after_inventory_cannot_authorize_unlink(model, monkeypatch):
    _, target, receipt = model
    staged(model)
    write_json = staging.write_json

    def replace_parent(path, record, **kwargs):
        write_json(path, record, **kwargs)
        if record.get("state") == "releasing":
            target.rename(target.with_name("original-private"))
            target.mkdir()
            (target / "config.json").write_bytes(b"unrelated")

    monkeypatch.setattr(staging, "write_json", replace_parent)
    with pytest.raises(ValueError, match="before unlink"):
        release(model)
    assert (target / "config.json").read_bytes() == b"unrelated"
    assert (target.with_name("original-private") / "sub/weights").read_bytes() == b"weights"
    assert json.loads(receipt.read_text())["state"] == "releasing"
