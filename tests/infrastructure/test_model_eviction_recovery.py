"""A stale publication receipt never authorizes loss of the last model copy."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from behave.runtime import models as archive


@pytest.fixture
def published(tmp_path):
    source = tmp_path / "model"
    source.mkdir()
    (source / "weights").write_bytes(b"weights")
    (source / "config").write_bytes(b"{}")
    files = [{"path": "weights", "size_bytes": 7}, {"path": "config", "size_bytes": 2}]
    receipt = archive.publish_model(source, files, checkpoint={"sha256": "saved"},
                                    staging=tmp_path / "transport", destination=tmp_path / "cold")
    return source, receipt


@pytest.mark.parametrize("fault", ["missing", "changed", "replaced", "linked"])
def test_unavailable_or_changed_cold_copy_retains_all_hot_files(published, fault):
    source, receipt = published
    cold = Path(receipt["archive"])
    if fault == "missing":
        cold.rename(cold.with_suffix(".retained"))
    elif fault == "changed":
        with cold.open("r+b") as stream:
            stream.write(b"X")  # Same size is not the same verified object.
    else:
        retained = cold.with_suffix(".retained")
        cold.rename(retained)
        if fault == "replaced":
            cold.write_bytes(retained.read_bytes())
        else:
            cold.symlink_to(retained)
    with pytest.raises((ValueError, OSError)):
        archive.evict_model(source, receipt)
    assert (source / "weights").read_bytes() == b"weights"
    assert (source / "config").read_bytes() == b"{}"
    assert not archive.marker(source).exists()


def test_new_receipt_eviction_does_not_read_or_list_cold_payloads(published, monkeypatch):
    source, receipt = published
    monkeypatch.setattr(archive, "digest", lambda _: pytest.fail("unexpected payload read"))
    original = Path.rglob

    def selected_hot_only(path, *args, **kwargs):
        assert path == source, "must not enumerate a cold directory"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rglob", selected_hot_only)
    assert archive.evict_model(source, receipt)
    assert archive.evict_model(source, receipt)  # Repeated release is idempotent.
    assert archive.cold_model(source)["retained_identities"] == receipt["retained_identities"]


@pytest.mark.parametrize("legacy", [False, True])
def test_partial_release_reuses_exact_inventory_and_retained_proof(published, monkeypatch, legacy):
    source, receipt = published
    if legacy:
        receipt.pop("retained_identities")
    digest, unlink = archive.digest, Path.unlink
    reads = []

    def counted(path):
        reads.append(Path(path))
        return digest(path)

    def interrupted(path, *args, **kwargs):
        if path.parent == source:
            proof = archive.cold_model(source)
            assert proof["hot_identities"] == receipt["hot_identities"]
            assert proof["retained_identities"]
            if path.name == "config":
                raise OSError("unlink interrupted")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(archive, "digest", counted)
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(OSError, match="unlink interrupted"):
            archive.evict_model(source, receipt)
    assert reads == ([Path(receipt["archive"])] if legacy else [])
    assert not (source / "weights").exists()
    assert (source / "config").read_bytes() == b"{}"
    # Retry with the caller's original legacy receipt, not a freshly fetched one.
    monkeypatch.setattr(archive, "digest", lambda _: pytest.fail("retry must reuse retained proof"))
    assert archive.evict_model(source, receipt)
    assert not source.exists()
    assert archive.restore_model(source, destination=source.parent / "private").is_dir()


@pytest.mark.parametrize("after_commit", [False, True])
def test_failed_local_inventory_commit_does_not_unlink(published, monkeypatch, after_commit):
    source, receipt = published
    write = archive._write

    def interrupted(path, value):
        if path == archive.marker(source):
            if after_commit:
                write(path, value)
            raise OSError("marker sync interrupted")
        return write(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(archive, "_write", interrupted)
        with pytest.raises(OSError, match="marker sync interrupted"):
            archive.evict_model(source, receipt)
    assert (source / "weights").read_bytes() == b"weights"
    assert (source / "config").read_bytes() == b"{}"
    assert archive.evict_model(source, receipt)


def test_cold_copy_lost_between_attempts_keeps_remaining_hot_files(published, monkeypatch):
    source, receipt = published
    unlink = Path.unlink

    def interrupted(path, *args, **kwargs):
        if path == source / "config":
            raise OSError("interrupted")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupted)
        with pytest.raises(OSError, match="interrupted"):
            archive.evict_model(source, receipt)
    cold = Path(receipt["archive"])
    cold.rename(cold.with_suffix(".retained"))
    with pytest.raises(OSError):
        archive.evict_model(source, receipt)
    assert (source / "config").read_bytes() == b"{}"


def test_retained_copy_rechecked_after_marker_before_first_unlink(published, monkeypatch):
    source, receipt = published
    write = archive._write

    def changed(path, value):
        write(path, value)
        if path == archive.marker(source):
            with Path(receipt["archive"]).open("r+b") as stream:
                stream.write(b"X")

    monkeypatch.setattr(archive, "_write", changed)
    with pytest.raises(ValueError, match="before unlink"):
        archive.evict_model(source, receipt)
    assert (source / "weights").read_bytes() == b"weights"
    assert (source / "config").read_bytes() == b"{}"


@pytest.mark.parametrize("fault", ["corrupt", "changed_during_read"])
def test_legacy_cold_verification_rejects_bad_or_changing_payload(published, monkeypatch, fault):
    source, receipt = published
    receipt.pop("retained_identities")
    cold = Path(receipt["archive"])
    digest = archive.digest

    def changed(path):
        result = digest(path)
        with Path(path).open("r+b") as stream:
            stream.write(b"X")
        return result

    if fault == "corrupt":
        with cold.open("r+b") as stream:
            stream.write(b"X")
    else:
        monkeypatch.setattr(archive, "digest", changed)
    with pytest.raises(ValueError, match="verification failed"):
        archive.evict_model(source, receipt)
    assert (source / "weights").read_bytes() == b"weights"
    assert not archive.marker(source).exists()


@pytest.mark.parametrize("fault", ["missing_proof_member", "overlapping_hot", "rebound_archive"])
def test_mismatched_retained_identity_never_releases_hot_files(published, fault):
    source, receipt = published
    if fault == "missing_proof_member":
        receipt["retained_identities"] = {}
    elif fault == "overlapping_hot":
        receipt.update(archive=str(source / "weights"), bytes=7)
        receipt["retained_identities"] = {str(source / "weights"): archive._identity(source / "weights")}
    else:
        archive._write(archive.marker(source), receipt)
        alternative = source.parent / "alternative.tar"
        alternative.write_bytes(Path(receipt["archive"]).read_bytes())
        receipt.update(archive=str(alternative), retained_identities={str(alternative): archive._identity(alternative)})
    with pytest.raises(ValueError):
        archive.evict_model(source, receipt)
    assert (source / "weights").read_bytes() == b"weights"
    assert (source / "config").read_bytes() == b"{}"


@pytest.mark.parametrize("corrupt", [False, True])
def test_imported_directory_uses_explicit_member_proofs(tmp_path, monkeypatch, corrupt):
    cold, source = tmp_path / "retained", tmp_path / "hot"
    cold.mkdir()
    source.mkdir()
    for root in (cold, source):
        (root / "weights").write_bytes(b"weights")
    receipt = {"state": "verified", "hot_source": str(source), "retained_directory": str(cold),
               "identity": {"schema": "behave.cold-hf-directory/1", "checkpoint": None,
                            "files": [{"path": "weights", "size_bytes": 7}]},
               "sha256_members": {"weights": hashlib.sha256(b"weights").hexdigest()},
               "hot_identities": {"weights": archive._identity(source / "weights")}}
    if corrupt:
        (cold / "weights").write_bytes(b"corrupt")
        with pytest.raises(ValueError, match="verification failed"):
            archive.evict_model(source, receipt)
        assert (source / "weights").read_bytes() == b"weights"
        return
    assert archive.evict_model(source, receipt)
    assert archive.cold_model(source)["retained_identities"] == {str(cold / "weights"): archive._identity(cold / "weights")}
    assert archive.restore_model(source) == source
    # The import was verified once; a later hot release needs only identities.
    monkeypatch.setattr(archive, "digest", lambda _: pytest.fail("import proof should be reusable"))
    assert archive.evict_model(source, archive.cold_model(source))
    assert (cold / "weights").read_bytes() == b"weights"


def test_publication_binds_identity_before_and_after_cold_verification(tmp_path, monkeypatch):
    source = tmp_path / "hot"
    source.mkdir()
    (source / "weights").write_bytes(b"weights")
    cold = tmp_path / "cold"
    digest = archive.digest

    def changed(path):
        result = digest(path)
        if Path(path).parent == cold:
            with Path(path).open("r+b") as stream:
                stream.write(b"X")
        return result

    monkeypatch.setattr(archive, "digest", changed)
    with pytest.raises(ValueError, match="readback mismatch"):
        archive.publish_model(source, [{"path": "weights", "size_bytes": 7}], checkpoint=None,
                              staging=tmp_path / "transport", destination=cold)
    assert not list(cold.glob("*.json"))
    assert (source / "weights").read_bytes() == b"weights"


def test_abrupt_eviction_process_exit_keeps_recovery_inventory(published):
    source, receipt = published
    code = '''
import json, os, pathlib, sys
from behave.runtime import models
source, receipt = pathlib.Path(sys.argv[1]), json.loads(sys.argv[2])
unlink = pathlib.Path.unlink
def abrupt(path, *args, **kwargs):
    result = unlink(path, *args, **kwargs)
    if path == source / 'weights':
        os._exit(91)
    return result
pathlib.Path.unlink = abrupt
models.evict_model(source, receipt)
'''
    child = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(source), json.dumps(receipt)],
                           cwd=source.parent, capture_output=True, timeout=20, check=False)
    assert child.returncode == 91, child.stderr.decode(errors="replace")
    assert archive.cold_model(source)["retained_identities"] == receipt["retained_identities"]
    assert not (source / "weights").exists()
    assert (source / "config").read_bytes() == b"{}"
    assert archive.evict_model(source, receipt)
    assert archive.restore_model(source, destination=source.parent / "private").is_dir()


def test_protected_model_requires_no_cold_access(published, monkeypatch):
    source, receipt = published
    monkeypatch.setattr(archive, "_retained_model", lambda *args: pytest.fail("protected model must be untouched"))
    assert archive.evict_model(source, receipt, protected=[source]) is False
    assert (source / "weights").read_bytes() == b"weights"


def test_repeated_publication_rejects_changed_committed_cold_file(published):
    source, receipt = published
    with Path(receipt["archive"]).open("r+b") as stream:
        stream.write(b"X")
    with pytest.raises(ValueError, match="retained model changed"):
        archive.publish_model(source, receipt["identity"]["files"], checkpoint=receipt["identity"]["checkpoint"],
                              staging=source.parent / "transport", destination=source.parent / "cold")
    assert (source / "weights").read_bytes() == b"weights"
