"""The local acquisition corpus is not a capability to read the workspace."""

from dataclasses import replace
from pathlib import Path

import pytest

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.drafts import SourceRecord
from behave.data.selection_records import SourceInventory
from behave.improve.library import (
    LibraryIndex,
    LookupPolicy,
    SourceLibrary,
    import_library,
    open_library,
    select_library,
)
from tools.dataset_splits import current_path


def prepared(tmp_path):
    root = tmp_path / "resources"
    (root / "source").mkdir(parents=True)
    (root / "notices").mkdir()
    license_text = b"Fixture licence evidence, not a legal clearance."
    notice = BlobRef.of(license_text)
    notice_path = f"notices/{notice.sha256}.txt"
    (root / notice_path).write_bytes(license_text)
    rows = []
    for index, body in enumerate((b"// saturation rounding\nint clamp(int x) { return x; }\n",
                                   "// Unicode context: 渐进下溢\nfloat gradual(float x) { return x; }\n".encode())):
        ref = BlobRef.of(body)
        path = f"source/{ref.sha256}.txt"
        (root / path).write_bytes(body)
        rows.append({"id": ref.sha256, "sha256": ref.sha256, "bytes": ref.size_bytes, "path": path,
            "origins": [{"repository": "fixture://upstream", "file": f"src/file{index}.c", "revision": "v1",
                "license": "", "repository_license_summary": "fixture", "notice_revision_basis": "Current upstream only",
                "notices": [notice_path], "storage": "embedded_source"}]})
    (root / "catalog.jsonl").write_bytes(b"\n".join(canonical_json(row) for row in rows) + b"\n")
    (tmp_path / "hidden_behavior.py").write_bytes(b"PRIVATE_GOLDEN_MUST_NOT_BE_READ")
    store = DirectoryStore(tmp_path / "objects")
    catalog = BlobRef.of((root / "catalog.jsonl").read_bytes())
    return root, store, catalog, rows


@pytest.mark.parametrize("alias", [False, True])
def test_prepared_library_pins_configured_owner_without_enumeration(tmp_path, monkeypatch, alias):
    from behave.improve.input_stores import Inputs

    owner = tmp_path / "owner"
    root, original, catalog, _ = prepared(owner)
    reference = import_library(root, catalog, original)
    selection = canonical_json({"library": reference.as_dict(), "source": str(root)})
    (owner / "selection.json").write_bytes(selection)
    configured = tmp_path / "library" if alias else owner
    if alias:
        configured.symlink_to(owner, target_is_directory=True)

    def no_listing(*args, **kwargs):
        pytest.fail("opening a configured library must not enumerate object stores")

    monkeypatch.setattr(Path, "iterdir", no_listing)
    monkeypatch.setattr(Path, "rglob", no_listing)
    selected, objects = open_library(configured)
    assert selected == reference and objects.directory == owner / "objects"
    if alias:
        configured.unlink()
        configured.symlink_to(tmp_path / "unavailable", target_is_directory=True)
    # Both selection and payload reads stay bound to the initially selected owner.
    index = LibraryIndex(objects, selected)
    assert index.library.catalog == catalog and len(index.units) == 2
    target = DirectoryStore(tmp_path / "published")
    Inputs(objects).publish(target, index.library.closure)
    assert LibraryIndex(target, selected).texts == index.texts
    assert (owner / "selection.json").read_bytes() == selection


@pytest.mark.parametrize("damage", ["blob_symlink", "objects_symlink", "owner_symlink", "corrupt_blob"])
def test_prepared_library_alias_does_not_relax_content_checks(tmp_path, damage):
    owner = tmp_path / "owner"
    root, objects, catalog, _ = prepared(owner)
    reference = import_library(root, catalog, objects)
    (owner / "selection.json").write_bytes(canonical_json({"library": reference.as_dict()}))
    alias = tmp_path / "library"
    alias.symlink_to(owner, target_is_directory=True)
    blob = objects.directory / catalog.sha256
    if damage == "blob_symlink":
        retained = tmp_path / "retained-catalog"
        blob.rename(retained)
        blob.symlink_to(retained)
    elif damage == "objects_symlink":
        retained = tmp_path / "retained-objects"
        objects.directory.rename(retained)
        objects.directory.symlink_to(retained, target_is_directory=True)
    elif damage == "owner_symlink":
        receipt = tmp_path / "receipt.json"
        receipt.write_bytes(canonical_json({"schema": "behave.object-store-owner/1", "owner": "../objects"}))
        (owner / "objects.owner.json").symlink_to(receipt)
    else:
        blob.chmod(0o600)
        blob.write_bytes(b"x" * catalog.size_bytes)
    with pytest.raises((RecordError, OSError)):
        selected, reader = open_library(alias)
        LibraryIndex(reader, selected)


def test_missing_prepared_library_is_not_created(tmp_path):
    absent = tmp_path / "absent"
    with pytest.raises(FileNotFoundError):
        open_library(absent)
    assert not absent.exists()


def test_prepared_library_respects_runtime_relocation_policy(tmp_path, monkeypatch):
    owner = tmp_path / "active/library"
    root, objects, catalog, _ = prepared(owner)
    reference = import_library(root, catalog, objects)
    (owner / "selection.json").write_bytes(canonical_json({"library": reference.as_dict()}))
    cold = tmp_path / "cold"
    profile = tmp_path / "locations.json"
    profile.write_bytes(canonical_json({"schema": "behave.runtime-locations/1",
        "mappings": [{"source": str(cold / "library"), "destination": str(owner)}],
        "deny_unmapped_roots": [str(cold)]}))
    monkeypatch.setenv("BEHAVE_RUNTIME_LOCATIONS", str(profile))
    selected, reader = open_library(cold / "library")
    assert selected == reference and reader.directory == owner / "objects"
    assert LibraryIndex(reader, selected).library.catalog == catalog
    with pytest.raises(ValueError, match="unmapped cold/legacy"):
        open_library(cold / "unmapped")


def test_source_lookup_pagination_and_existing_inventory_handoff(tmp_path):
    root, store, catalog, rows = prepared(tmp_path)
    ref = import_library(root, catalog, store)
    index = LibraryIndex(store, ref, LookupPolicy(page_items=1, page_characters=9))
    first = index.search("")
    assert first["total"] == 2 and first["next_offset"] == 1
    assert index.search("", 1)["next_offset"] is None
    assert index.search("rounding saturation")["results"][0]["id"] == rows[0]["id"]
    assert index.search("no-such-word")["total"] == 0
    page, parts = index.read(rows[1]["id"]), []
    while True:
        parts.append(page["content"])
        if page["next_offset"] is None:
            break
        page = index.read(rows[1]["id"], page["next_offset"])
    assert "".join(parts).encode() == (root / rows[1]["path"]).read_bytes()
    inventory_ref = index.inventory((rows[0]["id"],), store)
    inventory = SourceInventory.from_bytes(store.read(inventory_ref))
    source, = (SourceRecord.from_bytes(store.read(ref)) for ref in inventory.sources)
    assert source.origin == "fixture://upstream" and source.license is None
    assert source.selected.entry_file == rows[0]["path"]
    provenance = next(file for file in source.selected.files if file.path == "source_origins.json")
    assert parse_json(store.read(provenance.content))["origins"][0]["notice_revision_basis"] == "Current upstream only"
    assert all(b"PRIVATE_GOLDEN" not in path.read_bytes() for path in store.directory.iterdir())
    assert import_library(root, catalog, store) == ref


def test_shared_notice_is_read_once_per_index_and_still_verified(tmp_path):
    from collections import Counter
    from types import SimpleNamespace

    root, store, catalog, rows = prepared(tmp_path)
    reference = import_library(root, catalog, store)
    counts = Counter()

    def read(ref):
        counts[ref] += 1
        return store.read(ref)

    index = LibraryIndex(SimpleNamespace(read=read), reference)
    notice = index.library.units[0].notices[0]
    assert all(unit.notices == (notice,) for unit in index.library.units)
    assert counts[notice.content] == 1
    assert len(counts) == 5  # library, catalog, two sources and the shared notice
    assert all(count == 1 for count in counts.values())
    for row in rows:
        assert index.read(row["id"], notice=notice.path)["content"] == index.notice_texts[notice]
    # A new construction verifies pinned bytes again, not a global memory cache.
    corrupt = SimpleNamespace(read=lambda ref: b"corrupt" if ref == notice.content else store.read(ref))
    with pytest.raises(RecordError):
        LibraryIndex(corrupt, reference)


def test_supporting_source_bundle_reaches_builder_and_reviewer_but_not_blind_b2(tmp_path, monkeypatch):
    from test_attempts import Scripted
    from test_synthesis import reader_replies, setup

    from behave.data import checks
    from behave.data.construction import reader_inputs
    from behave.data.drafts import load_draft, load_scope
    from behave.data.source import review_inputs
    from behave.data.synthesis import synthesize
    from behave.task_execution.judgment import ComponentJudgment

    root, _, catalog, rows = prepared(tmp_path / "library")
    options, author, _ = setup(tmp_path / "construction")
    store = options["reader"]
    index = LibraryIndex(store, import_library(root, catalog, store))
    index.inventory(tuple(row["id"] for row in rows), store)
    primary, supporting = (row["id"] for row in rows)
    bundle = index.source_record(primary, (supporting, supporting, primary))
    assert bundle == index.source_record(primary, (supporting,))
    assert bundle.selected.entry_file == rows[0]["path"]
    provenance = next(file for file in bundle.selected.files
                      if file.path == f"supporting_source_origins/{supporting}.json")
    assert parse_json(store.read(provenance.content))["unit"] == supporting
    scope, _, _ = load_scope(store, options["plan"].scope)
    scope = store.put(replace(scope, source=store.put(bundle.to_bytes())).to_bytes())
    options["plan"] = replace(options["plan"], scope=scope)
    monkeypatch.setattr(checks, "compare_sources", lambda *a, **k: ComponentJudgment(1, "fixture"))
    result = synthesize(**options, author_backend=Scripted(*author), reader_backend=Scripted(*reader_replies()))
    objects = options["run"].objects
    draft = load_draft(objects, result.drafts[-1])
    guidance = objects.put(b"Check source semantics.")
    _, _, reviewed = review_inputs(objects, result.drafts[-1], guidance)
    reviewed = {file.reference.path: file.data for file in reviewed}
    for file in bundle.selected.files:
        assert draft.file(".construction/source/" + file.path).data == store.read(file.content)
        assert reviewed["source/" + file.path] == store.read(file.content)
    _, blind = reader_inputs(objects, result.drafts[-1])
    assert not {file.content for file in bundle.selected.files} & {file.reference.content for file in blind}
    assert not any(file.reference.path.startswith(("source/", ".construction/source/")) for file in blind)


@pytest.mark.parametrize("operation", ["read", "selection", "notice", "offset", "path"])
def test_no_escape_through_model_arguments(tmp_path, operation):
    root, store, catalog, rows = prepared(tmp_path)
    index = LibraryIndex(store, import_library(root, catalog, store))
    with pytest.raises((RecordError, ValueError)):
        if operation == "read":
            index.read("../hidden_behavior.py")
        elif operation == "selection":
            index.inventory(("../hidden_behavior.py",), store)
        elif operation == "notice":
            index.read(rows[0]["id"], notice="../hidden_behavior.py")
        elif operation == "offset":
            index.search("", True)
        else:
            bad = rows[0] | {"path": "../hidden_behavior.py"}
            (root / "catalog.jsonl").write_bytes(canonical_json(bad))
            import_library(root, BlobRef.of((root / "catalog.jsonl").read_bytes()), store)


@pytest.mark.parametrize("change", ["source", "notice", "catalog", "symlink", "parent_symlink"])
def test_changed_or_symlinked_input_is_rejected(tmp_path, change):
    root, store, catalog, rows = prepared(tmp_path)
    source = root / rows[0]["path"]
    if change == "source":
        source.write_bytes(b"changed source")
    elif change == "notice":
        (root / rows[0]["origins"][0]["notices"][0]).write_bytes(b"changed notice")
    elif change == "catalog":
        (root / "catalog.jsonl").write_bytes(b"changed catalog")
    elif change == "symlink":
        source.unlink()
        source.symlink_to(tmp_path / "hidden_behavior.py")
    else:
        (root / "source").rename(root / "renamed")
        (root / "source").symlink_to(root / "renamed", target_is_directory=True)
    with pytest.raises(RecordError):
        import_library(root, catalog, store)


def test_offline_search_is_lazy_and_page_sizes_are_not_corpus_limits(tmp_path):
    root, store, catalog, rows = prepared(tmp_path)
    ref = import_library(root, catalog, store)
    index = LibraryIndex(store, ref, LookupPolicy(1000, 1_000_000))
    assert "terms" not in index.__dict__
    assert index.read(rows[0]["id"])["next_offset"] is None
    assert len(index.search("")["results"]) == 2
    assert "terms" in index.__dict__
    assert len(LibraryIndex(store, ref, LookupPolicy(1, 1)).search("")["results"]) == 1


@pytest.mark.dataset
@pytest.mark.parametrize("snapshot", ["historical", "current"])
def test_prepared_seed50_library_uses_every_declared_unit(tmp_path, snapshot):
    base = (Path(__file__).resolve().parents[2] / "data/splits/self_improvement_seed50_v1"
            if snapshot == "historical" else current_path("self_improvement"))
    root = base / "resources"
    catalog = BlobRef.of((root / "catalog.jsonl").read_bytes())
    store = DirectoryStore(tmp_path / "objects")
    index = LibraryIndex(store, import_library(root, catalog, store))
    assert len(index.library.units) == parse_json((base / "manifest.json").read_bytes())["counts"]["unique_source_units"]
    assert index.search("quantization")["total"] > 0


def test_catalog_identity_binds_source_origins_and_notices(tmp_path):
    root, store, catalog, _ = prepared(tmp_path)
    reference = import_library(root, catalog, store)
    original = SourceLibrary.from_bytes(store.read(reference))
    first, *remaining = original.units
    changed = replace(first, origins=({**thaw_json(first.origins[0]), "file": "invented.py"},))
    forged = store.put(replace(original, units=(changed, *remaining)).to_bytes())
    with pytest.raises(RecordError, match="origins changed"):
        LibraryIndex(store, forged)
    changed = replace(first, notices=())
    forged = store.put(replace(original, units=(changed, *remaining)).to_bytes())
    with pytest.raises(RecordError, match="notice identities"):
        LibraryIndex(store, forged)


def test_library_selection_publishes_only_selected_sources_and_catalog(tmp_path):
    root, store, catalog, rows = prepared(tmp_path)
    original = import_library(root, catalog, store)
    selected = DirectoryStore(tmp_path / "selected")
    reference = select_library(store, original, (rows[0]["id"],), selected)
    index = LibraryIndex(selected, reference)
    assert set(index.units) == {rows[0]["id"]}
    assert index.search("gradual")["total"] == 0
    assert [parse_json(line) for line in selected.read(index.library.catalog).splitlines()] == [rows[0]]
    assert not (selected.directory / rows[1]["sha256"]).exists()
    assert not (selected.directory / catalog.sha256).exists()
    assert {path.name for path in selected.directory.iterdir()} == {ref.sha256 for ref in index.library.closure}
    assert len(LibraryIndex(store, original).units) == 2
    assert select_library(store, original, (rows[0]["id"],), selected) == reference


@pytest.mark.parametrize("selection", [(), ("missing",), ("duplicate", "duplicate")])
def test_library_selection_rejects_invalid_membership(tmp_path, selection):
    root, store, catalog, _ = prepared(tmp_path)
    reference = import_library(root, catalog, store)
    with pytest.raises((ValueError, RecordError)):
        select_library(store, reference, selection, DirectoryStore(tmp_path / "selected"))


def test_library_selection_identity_does_not_depend_on_requested_order(tmp_path):
    root, store, catalog, rows = prepared(tmp_path)
    reference = import_library(root, catalog, store)
    identifiers = tuple(row["id"] for row in rows)
    selected = DirectoryStore(tmp_path / "selected")
    assert select_library(store, reference, identifiers, selected) == select_library(
        store, reference, tuple(reversed(identifiers)), selected)
