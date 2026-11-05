import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from behave.core.artifacts import BlobRef, FileRef, SourceArtifact
from behave.core.records import RecordError, parse_json
from behave.core.storage import DirectoryStore
from behave.data.drafts import ScopeProposal, SourceRecord, load_scope
from behave.data.ingest import ingest_source, read_source


def selected(tmp_path):
    root = tmp_path / "checkout"
    (root / "src").mkdir(parents=True)
    data = {"src/model.c": b"int f(int a) { return a ^ 7; }\n", "LICENSE": b"fixture licence\n"}
    for path, payload in data.items():
        (root / path).write_bytes(payload)
    source = SourceRecord("xor", "https://example.invalid/upstream", "declared-revision", "fixture",
        SourceArtifact(tuple(FileRef(path, BlobRef.of(payload)) for path, payload in data.items()),
                       "src/model.c", "f"))
    return root, source


def test_import_composes_with_existing_scope_and_cold_retry(tmp_path):
    root, source = selected(tmp_path)
    objects = DirectoryStore(tmp_path / "objects")
    assert not objects.directory.exists()
    files = read_source(source, root)
    assert not objects.directory.exists()
    ref = ingest_source(source, root, objects)
    proposal = ScopeProposal(ref, "retain XOR", ("eight-bit XOR",), ("choose eight bits",))
    scope = objects.put(proposal.to_bytes())
    loaded, origin, selected_files = load_scope(objects, scope)
    assert (loaded, origin, selected_files) == (proposal, source, files)
    (root / "src/model.c").unlink()
    assert ingest_source(source, tmp_path / "removed-checkout", objects) == ref
    assert objects.read(ref) == source.to_bytes()


@pytest.mark.parametrize("kind", ["same-size-change", "resize", "missing", "extension-fallback"])
def test_changed_or_missing_selection_never_publishes_source(tmp_path, kind):
    root, source = selected(tmp_path)
    path = root / "src/model.c"
    if kind == "same-size-change":
        path.write_bytes(path.read_bytes().replace(b"7", b"8"))
    elif kind == "resize":
        path.write_bytes(b"different")
    elif kind == "extension-fallback":
        path.rename(path.with_suffix(".cpp"))
    else:
        path.unlink()
    objects = DirectoryStore(tmp_path / "objects")
    with pytest.raises(RecordError) as caught:
        ingest_source(source, root, objects)
    assert caught.value.code in {"source-import-changed", "source-import-unavailable"}
    assert not objects.directory.exists()


@pytest.mark.parametrize("kind", ["file", "directory", "root", "fifo"])
def test_symlinks_and_special_files_are_not_source_bytes(tmp_path, kind):
    root, source = selected(tmp_path)
    if kind == "root":
        link = tmp_path / "alias"
        link.symlink_to(root, target_is_directory=True)
        root = link
    elif kind == "directory":
        (root / "src").rename(root / "actual")
        (root / "src").symlink_to(root / "actual", target_is_directory=True)
    else:
        path = root / "src/model.c"
        path.unlink()
        if kind == "fifo":
            os.mkfifo(path)
        else:
            outside = tmp_path / "outside.c"
            outside.write_bytes(b"int f(int a) { return a ^ 7; }\n")
            path.symlink_to(outside)
    with pytest.raises(RecordError) as caught:
        read_source(source, root)
    assert caught.value.code in {"source-import-not-regular", "source-import-unavailable"}


def test_import_large_selected_source_without_workspace_quota(tmp_path):
    root, source = selected(tmp_path)
    payload = b"x" * (17 << 20)
    (root / "src/model.c").write_bytes(payload)
    source = replace(source, selected=replace(source.selected, files=tuple(
        FileRef(f.path, BlobRef.of(payload)) if f.path == "src/model.c" else f for f in source.selected.files)))
    objects = DirectoryStore(tmp_path / "objects")
    ref = ingest_source(source, root, objects)
    assert SourceRecord.from_bytes(objects.read(ref)) == source
    assert objects.read(BlobRef.of(payload)) == payload
    assert ingest_source(source, tmp_path / "removed-checkout", objects) == ref


def test_interrupted_import_is_not_a_record_and_retry_is_idempotent(tmp_path, monkeypatch):
    root, source = selected(tmp_path)
    objects = DirectoryStore(tmp_path / "objects")
    put = objects.put

    def interrupted(data):
        if data == source.to_bytes():
            raise OSError("fixture interrupted before source commit")
        return put(data)

    monkeypatch.setattr(objects, "put", interrupted)
    with pytest.raises(OSError):
        ingest_source(source, root, objects)
    assert not (objects.directory / BlobRef.of(source.to_bytes()).sha256).exists()
    monkeypatch.setattr(objects, "put", put)
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = tuple(pool.map(lambda _: ingest_source(source, root, objects), range(8)))
    assert len(set(refs)) == 1
    assert SourceRecord.from_bytes(objects.read(refs[0])) == source


def test_existing_incomplete_source_is_not_silently_healed(tmp_path):
    root, source = selected(tmp_path)
    objects = DirectoryStore(tmp_path / "objects")
    objects.put(source.to_bytes())
    with pytest.raises(RecordError):
        ingest_source(source, root, objects)
    assert len(tuple(objects.directory.iterdir())) == 1


def test_source_declarations_and_selectors_are_not_inferred(tmp_path):
    root, source = selected(tmp_path)
    objects = DirectoryStore(tmp_path / "objects")
    ref = ingest_source(source, root, objects)
    for changed in (replace(source, license=None), replace(source, revision=None),
                    replace(source, selected=replace(source.selected, entry_symbol="g"))):
        changed_ref = ingest_source(changed, root, objects)
        assert changed_ref != ref
        assert SourceRecord.from_bytes(objects.read(changed_ref)) == changed


def test_data_ingest_cli_dry_run_import_and_cold_read(tmp_path, capsys):
    from behave.data.__main__ import main

    root, source = selected(tmp_path)
    record = tmp_path / "source.json"
    record.write_bytes(source.to_bytes())
    target = tmp_path / "objects"
    argv = ["ingest", "--source-record", str(record), "--source-root", str(root), "--objects", str(target)]
    assert main([*argv, "--dry-run"]) == 0
    assert parse_json(capsys.readouterr().out.encode())["status"] == "validated"
    assert not target.exists()
    assert main(argv) == 0
    result = parse_json(capsys.readouterr().out.encode())
    assert result == {"status": "imported", "source": BlobRef.of(source.to_bytes()).as_dict(),
                      "files": 2, "publication_approved": False}
    (root / "src/model.c").unlink()
    assert main(argv) == 0
    capsys.readouterr()
    assert main([*argv, "--dry-run"]) == 1
    assert parse_json(capsys.readouterr().out.encode())["code"] == "source-import-unavailable"
