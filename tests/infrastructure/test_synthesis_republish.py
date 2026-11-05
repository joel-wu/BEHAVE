"""Internal republication reuses real admission evidence, not old dispositions."""

from dataclasses import replace

import pytest
from syn_shell import fixture_shell
from test_release_admission import candidate

from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import parse_json
from behave.data.admission import admit_package
from behave.data.publication import publish_release
from behave.data.published import ReleaseManifest, load_published_task
from tools.synthesis_republish import prepare, republish


def original_publication(tmp_path, monkeypatch, **kwargs):
    c = candidate(tmp_path / "candidate", monkeypatch, **kwargs)
    policy = c.store.put(replace(c.policy, require_license_metadata=True).to_bytes())
    source = JournalDirectory(tmp_path / "source")
    result = publish_release(release_id="old", policy=policy, packages=(c.ref,), reader=c.store, destination=source)
    assert ReleaseManifest.from_bytes(source.objects.read(result.release)).metadata["included"] == 0
    return source, result


def test_republish_removes_only_the_configured_license_gate(tmp_path, monkeypatch):
    source, original = original_publication(tmp_path, monkeypatch)
    destination = tmp_path / "new"
    result = republish(source.directory, {"require_license_metadata": False}, "internal", destination)
    assert result["summary"]["included"] == 1 and result["summary"]["pending"] == 0
    release = BlobRef.from_dict(result["release"])
    task = load_published_task(JournalDirectory(destination).objects, release, "identity")
    assert task.manifest.task_id == "identity"
    assert source.seal("publication")[0] == original.head
    assert ReleaseManifest.from_bytes(source.objects.read(original.release)).metadata["included"] == 0
    assert republish(source.directory, {"require_license_metadata": False}, "internal", destination) == result
    with pytest.raises(ValueError, match="new destination"):
        republish(source.directory, {"require_license_metadata": True}, "internal", destination)


def test_unresolved_review_is_not_approved_by_changing_license_policy(tmp_path, monkeypatch):
    source, _ = original_publication(tmp_path, monkeypatch, conclusion="unclear")
    result = republish(source.directory, {"require_license_metadata": False}, "still-pending", tmp_path / "new")
    assert result["summary"]["included"] == 0 and result["summary"]["pending"] == 1


def test_republication_reuses_owned_evidence_files(tmp_path, monkeypatch):
    from behave.core.storage import DirectoryStore
    from behave.data.publication import load_publication

    source, _ = original_publication(tmp_path, monkeypatch)

    def no_payload_copy(*args, **kwargs):
        raise AssertionError("owned republication must not copy every payload again")

    monkeypatch.setattr(DirectoryStore, "publish", no_payload_copy)
    destination = JournalDirectory(tmp_path / "linked")
    result = republish(source.directory, {"require_license_metadata": False}, "linked", destination.directory)
    release = BlobRef.from_dict(result["release"])
    task = load_published_task(destination.objects, release, "identity")
    content = task.manifest.spec.content
    before = (source.objects.directory / content.sha256).stat()
    after = (destination.objects.directory / content.sha256).stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    assert load_publication(destination.objects, BlobRef.from_dict(result["head"])).release == release


def test_known_task_defect_is_explicitly_deferred_and_retains_its_package(tmp_path, monkeypatch):
    first = candidate(tmp_path / "first", monkeypatch)
    other = candidate(tmp_path / "other", monkeypatch, task_id="other")
    admission = admit_package(package=other.ref, policy=other.policy_ref, reader=other.store)
    first.store.publish(other.store, admission.closure)
    source = JournalDirectory(tmp_path / "source")
    original = publish_release(release_id="original", policy=first.policy_ref,
        packages=(first.ref, other.ref), reader=first.store, destination=source)
    destination = tmp_path / "new"
    result = republish(source.directory, {"require_license_metadata": False}, "deferred", destination,
                       deferred={"identity": "A source-fidelity issue requires repair."})
    assert result["summary"]["requested"] == result["summary"]["included"] == 1
    selection = parse_json((destination / "selection.json").read_bytes())
    assert selection["source_release"] == original.release.as_dict()
    assert selection["deferred"] == result["deferred"]
    assert source.objects.read(BlobRef.from_dict(result["deferred"][0]["package"]))
    with pytest.raises(ValueError, match="not in the source"):
        prepare(source.directory, {"require_license_metadata": False}, "bad-id", {"unknown": "typo"})
