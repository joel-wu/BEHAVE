"""Release commands preserve admission, cold recovery and complete pending rows."""

from dataclasses import replace

import pytest
from test_infra_cli import command
from test_release_admission import candidate

from behave.agent.journal import Event, load_chain
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.publication import audit_release, load_publication, publish_release
from behave.data.published import ReleaseManifest
from behave.data.release_records import ReleaseRequest


def test_release_cli_reads_a_complete_large_selection(tmp_path, monkeypatch, capsys):
    from behave.cli import release

    refs = tuple(BlobRef.of(str(i).encode()) for i in range(50000))
    request = ReleaseRequest("large-selection", BlobRef.of(b"policy"), refs)
    path = tmp_path / "request.json"
    data = request.to_bytes()
    assert len(data) > 4 << 20
    path.write_bytes(data)
    seen = []

    def inspect(actual, reader):
        seen.append(actual)
        return ()

    monkeypatch.setattr(release, "audit_release", inspect)
    assert release.main(["--request", str(path), "--objects", str(tmp_path / "objects"), "--dry-run"]) == 0
    assert seen == [request]
    assert parse_json(capsys.readouterr().out.encode())["status"] == "audited"


@pytest.mark.parametrize("mode,outcome", [("acquired-training", "include"), ("reviewed-benchmark", "pending")])
def test_release_cli_preflight_publish_inspect_and_recovery(tmp_path, monkeypatch, mode, outcome):
    c = candidate(tmp_path / "candidate", monkeypatch, mode=mode)
    request = ReleaseRequest("fixture-release", c.policy_ref, (c.ref,))
    path = tmp_path / "request.json"
    path.write_bytes(request.to_bytes())
    destination = tmp_path / "published"
    args = ("release", "--request", path, "--objects", c.store.directory, "--destination", destination)
    audited = command(*args, "--dry-run")
    assert audited.returncode == 0, audited.stderr
    assert not destination.exists()
    (row,) = parse_json(audited.stdout)["dispositions"]
    assert row["outcome"] == outcome
    completed = command(*args)
    assert completed.returncode == 0, completed.stderr
    result = parse_json(completed.stdout)
    assert result["status"] == "committed" and result["summary"]["requested"] == 1
    assert result["summary"]["included"] == (outcome == "include")
    cold = command("release", "--request", path, "--objects", tmp_path / "offline", "--destination", destination)
    assert cold.returncode == 0 and parse_json(cold.stdout) == result
    head = tmp_path / "head.json"
    head.write_bytes(canonical_json(result["head"]))
    inspected = command("inspect", "--kind", "release", "--record", head, "--objects", destination / "objects")
    assert inspected.returncode == 0, inspected.stderr
    assert parse_json(inspected.stdout)["release"] == result["release"]
    path.write_bytes(replace(request, release_id="different-selection").to_bytes())
    assert command(*args).returncode == 1


def test_release_request_rejects_ambiguous_selection(tmp_path, monkeypatch):
    c = candidate(tmp_path, monkeypatch)
    request = ReleaseRequest("release", c.policy_ref, (c.ref,))
    assert ReleaseRequest.from_bytes(request.to_bytes()) == request
    with pytest.raises(ValueError, match="duplicate"):
        replace(request, packages=(c.ref, c.ref))
    with pytest.raises(RecordError, match="canonical"):
        ReleaseRequest.from_bytes(request.to_bytes() + b"\n")
    with pytest.raises(RecordError):
        audit_release(replace(request, approvals=(c.ref,)), c.store)


@pytest.mark.parametrize("mutation", ["manifest", "missing-chunk-content", "chunk-offset"])
def test_exported_publication_rederives_manifest_and_entire_closure(tmp_path, monkeypatch, mutation):
    c = candidate(tmp_path, monkeypatch)
    destination = JournalDirectory(tmp_path / "publication")
    result = publish_release(
        release_id="release", policy=c.policy_ref, packages=(c.ref,), reader=c.store, destination=destination
    )
    head, refs = destination.seal("publication")
    exported = DirectoryStore(tmp_path / "exported")
    exported.publish(destination.objects, refs)
    assert load_publication(exported, head) == result
    events = list(load_chain(exported, head))
    if mutation == "manifest":
        original = ReleaseManifest.from_bytes(exported.read(result.release))
        forged = replace(original, metadata={**thaw_json(original.metadata), "included": 200})
        ref = exported.put(forged.to_bytes())
        events[-1] = replace(events[-1], payload={"release": ref.as_dict()}, artifacts=(ref,))
    else:
        event = events[1]
        payload, refs = thaw_json(event.payload), event.artifacts
        if mutation == "missing-chunk-content":
            refs = refs[1:]
            payload.update(count=len(refs), digest=BlobRef.of(canonical_json([r.as_dict() for r in refs])).sha256)
        else:
            payload["offset"] = 1
        events[1] = replace(event, payload=payload, artifacts=refs)
    # All hashes and artifact references are valid; only semantic re-verification
    # can reject these rewritten reports.
    parent = None
    for event in events:
        parent = exported.put(Event(event.sequence, parent, event.kind, event.payload, event.artifacts).to_bytes())
    with pytest.raises(RecordError) as error:
        load_publication(exported, parent)
    assert error.value.code == "publication-evidence-mismatch"
