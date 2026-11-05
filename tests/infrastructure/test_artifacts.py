from __future__ import annotations

import json
import math
import os
import pickle
import sys
from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from behave.core.artifacts import (
    BlobRef,
    DirectoryReader,
    FileData,
    FileRef,
    LoadedSource,
    SourceArtifact,
    file_references,
    file_set,
    read_verified,
    validate_sha256_digest,
)
from behave.core.records import RecordError, canonical_json, freeze_json, parse_json, thaw_json


@pytest.mark.parametrize("payload", [
    b'{"k":1,"k":2}', b'{"nested":{"k":1,"k":2}}', b'{"k":NaN}', b'{"k":Infinity}',
    b'{"k":1e9999}', b'[]', b'{} garbage', b'\xff', b'{"s":"\\ud800"}',
    b'{"\\ud800":0}',
])
def test_non_json_or_ambiguous_records_are_rejected(payload):
    with pytest.raises(RecordError):
        parse_json(payload)


def test_json_freeze_is_recursive_and_does_not_erase_scalar_types():
    source = {"values": [True, 1, 1.0, None], "nested": {"name": "μ"}}
    frozen = freeze_json(source)
    source["values"].append(99)
    source["nested"]["name"] = "changed"
    with pytest.raises(TypeError):
        frozen["nested"]["name"] = "also changed"
    image = thaw_json(frozen)
    assert image == {"values": [True, 1, 1.0, None], "nested": {"name": "μ"}}
    assert [type(item) for item in image["values"]] == [bool, int, float, type(None)]
    assert canonical_json({"v": True}) != canonical_json({"v": 1})
    assert parse_json(canonical_json(frozen)) == image


@pytest.mark.parametrize("value", [math.nan, math.inf, {1: "bad key"}, {"x": object()}])
def test_json_value_domain_is_closed(value):
    with pytest.raises(RecordError):
        freeze_json(value)


@pytest.mark.parametrize("kind", ["list", "dict", "mixed", "proxy"])
def test_cyclic_metadata_is_an_invalid_value_not_a_depth_budget(kind):
    if kind == "list":
        cycle = []
        cycle.append(cycle)
    elif kind == "dict":
        cycle = {}
        cycle["self"] = cycle
    elif kind == "mixed":
        items = []
        cycle = {"items": (items,)}
        items.append(cycle)
    else:
        source = {}
        cycle = MappingProxyType(source)
        source["self"] = cycle
    with pytest.raises(RecordError) as found:
        freeze_json(cycle)
    assert found.value.code == "record-value-invalid"


def test_shared_acyclic_values_are_not_mistaken_for_cycles():
    shared = {"values": [True, 1, 1.0, "μ"]}
    image = {"left": shared, "right": shared}
    frozen = freeze_json(image)
    assert thaw_json(frozen) == image
    assert canonical_json(frozen) == canonical_json(image)


def test_json_beyond_64_levels_roundtrips_without_a_custom_depth_budget():
    payload = b'{"nested":' + b'[' * 128 + b'1' + b']' * 128 + b'}'
    parsed = parse_json(payload)
    assert canonical_json(freeze_json(parsed)) == payload


def test_interpreter_recursion_exhaustion_remains_a_typed_error():
    depth = sys.getrecursionlimit() + 1
    value = 0
    for _ in range(depth):
        value = [value]
    payload = b'{"nested":' + b'[' * depth + b'0' + b']' * depth + b'}'
    for operation in (lambda: freeze_json(value), lambda: canonical_json(value),
                      lambda: parse_json(payload)):
        with pytest.raises(RecordError) as found:
            operation()
        assert found.value.code == "record-json-invalid"
        assert isinstance(found.value.__cause__, RecursionError)


def test_parse_validates_without_serializing_the_record_again(monkeypatch):
    from behave.core import records

    def unexpected_encoding(*args, **kwargs):
        pytest.fail("reading a record must not encode it again")

    monkeypatch.setattr(records.json, "dumps", unexpected_encoding)
    assert parse_json(b'{"values":[true,1,1.0,null],"s":"\\u03bc\\n"}') == {
        "values": [True, 1, 1.0, None], "s": "μ\n",
    }
    for payload in (b'{"x":1e9999}', b'{"s":"\\ud800"}', b'{"\\ud800":0}'):
        with pytest.raises(RecordError):
            parse_json(payload)


def test_canonical_bytes_match_the_existing_standard_library_format():
    value = {"z": [True, 1, 1.0, -0.0, None], "a": {"μ": "text\n", "": []}}
    expected = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert canonical_json(value) == canonical_json(freeze_json(value)) == expected


def test_json_encoding_and_reading_do_not_build_detached_graphs(monkeypatch):
    from behave.core import records

    shared = {"values": [True, 1, 1.0, None, "μ"]}
    value = MappingProxyType({"left": shared, "right": (shared,)})
    expected = canonical_json(value)

    def no_copy(*args, **kwargs):
        pytest.fail("serialization must not freeze/thaw the entire evidence graph")

    monkeypatch.setattr(records, "freeze_json", no_copy)
    monkeypatch.setattr(records, "thaw_json", no_copy)
    assert canonical_json(value) == expected
    assert parse_json(expected) == {"left": shared, "right": [shared]}


@pytest.mark.parametrize("value", [float('nan'), {1: 0}, {"x": object()}, {"x": '\ud800'}])
def test_no_copy_encoder_keeps_the_closed_value_domain(value):
    with pytest.raises(RecordError):
        canonical_json(value)


def test_no_copy_encoder_still_rejects_cycles():
    value = []
    value.append(value)
    with pytest.raises(RecordError) as found:
        canonical_json(value)
    assert found.value.code == "record-value-invalid"


def test_encoder_capacity_failure_remains_a_typed_error(monkeypatch):
    from behave.core import records

    failure = ValueError("interpreter integer conversion limit")

    def cannot_encode(*args, **kwargs):
        raise failure

    monkeypatch.setattr(records.json, "dumps", cannot_encode)
    with pytest.raises(RecordError) as found:
        canonical_json({"number": 1})
    assert found.value.code == "record-json-invalid"
    assert found.value.__cause__ is failure


def test_record_error_keeps_its_reason_in_trusted_worker_transport():
    original = RecordError("record-key-duplicate", "duplicate JSON key 'a'")
    restored = pickle.loads(pickle.dumps(original))
    assert type(restored) is RecordError
    assert restored.code == original.code
    assert str(restored) == str(original)


@pytest.mark.parametrize("size", [-1, True, 1.0, "1"])
def test_blob_size_is_an_exact_nonnegative_integer(size):
    with pytest.raises(RecordError):
        BlobRef("0" * 64, size)


@pytest.mark.parametrize("digest", ["A" * 64, "../secret", "0" * 63, "0" * 65,
                                  "g" * 64, "0" * 64 + "\n", b"0" * 64, None, True, 0])
def test_blob_digest_cannot_be_a_path_or_alias(digest):
    with pytest.raises(RecordError) as direct:
        validate_sha256_digest(digest)
    with pytest.raises(RecordError) as wrapped:
        BlobRef(digest, 0)
    assert direct.value.code == wrapped.value.code == "content-digest-invalid"
    assert str(direct.value) == str(wrapped.value)


@pytest.mark.parametrize("digest", ["0" * 64, "0123456789abcdef" * 4])
def test_digest_validation_does_not_compute_a_new_hash(monkeypatch, digest):
    from behave.core import artifacts

    def unexpected_hash(*args, **kwargs):
        pytest.fail("format validation must not hash content")

    monkeypatch.setattr(artifacts.hashlib, "sha256", unexpected_hash)
    assert validate_sha256_digest(digest) is None
    assert BlobRef(digest, 7).as_dict() == {"sha256": digest, "size_bytes": 7}


def test_directory_content_is_reverified_not_trusted_by_filename(tmp_path):
    payload = b"source"
    ref = BlobRef.of(payload)
    path = tmp_path / ref.sha256
    path.write_bytes(payload)
    reader = DirectoryReader(tmp_path)
    assert reader.read(ref) == payload
    path.write_bytes(b"forged")
    with pytest.raises(RecordError) as found:
        reader.read(ref)
    assert found.value.code == "content-identity-mismatch"
    with pytest.raises(FrozenInstanceError):
        ref.size_bytes = 99


@pytest.mark.parametrize("kind", ["missing", "symlink", "fifo", "directory", "wrong-size"])
def test_directory_reader_refuses_noncontent_objects(tmp_path, kind):
    ref = BlobRef.of(b"x")
    target = tmp_path / ref.sha256
    if kind == "symlink":
        other = tmp_path / "other"
        other.write_bytes(b"x")
        target.symlink_to(other)
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "directory":
        target.mkdir()
    elif kind == "wrong-size":
        target.write_bytes(b"too long")
    with pytest.raises(RecordError):
        DirectoryReader(tmp_path).read(ref)


def test_reader_root_link_is_rejected_without_an_object_size_quota(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    ref = BlobRef.of(b"xx")
    (real / ref.sha256).write_bytes(b"xx")
    with pytest.raises(RecordError):
        DirectoryReader(link).read(ref)
    assert DirectoryReader(real).read(ref) == b"xx"


def test_a_custom_reader_cannot_bypass_content_verification():
    class WrongReader:
        def read(self, ref):
            return b"y"

    with pytest.raises(RecordError) as found:
        read_verified(WrongReader(), BlobRef.of(b"x"))
    assert found.value.code == "content-identity-mismatch"


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../x", "a/../x", "a//b", "a/./b", "a/", "a\\b", "."])
def test_file_paths_are_normalized_and_relative(path):
    with pytest.raises(RecordError):
        FileRef(path, BlobRef.of(b""))


@pytest.mark.parametrize("paths", [("x", "x"), ("x", "x/y"), ("x/y", "x/y/z")])
def test_file_tree_conflicts_are_rejected(paths):
    with pytest.raises(RecordError) as found:
        file_set(tuple(FileRef(path, BlobRef.of(b"")) for path in paths))
    assert found.value.code == "artifact-path-conflict"


def test_source_entry_is_explicit_and_bundles_are_sorted():
    files = tuple(FileRef(path, BlobRef.of(path.encode())) for path in ("z.py", "a.py"))
    artifact = SourceArtifact(files, "z.py", "ChosenModel")
    assert [item.path for item in artifact.files] == ["a.py", "z.py"]
    assert SourceArtifact.from_dict(artifact.as_dict()) == artifact
    with pytest.raises(RecordError) as found:
        SourceArtifact(files, "missing.py", "ChosenModel")
    assert found.value.code == "artifact-entry-missing"


def test_loaded_source_keeps_verified_bytes_without_reordering_or_loading_code():
    files = tuple(FileData(FileRef(path, BlobRef.of(path.encode())), path.encode()) for path in ("z.py", "a.py"))
    artifact = SourceArtifact(tuple(file.reference for file in files), "z.py", "ChosenModel")
    source = LoadedSource(artifact, files)
    assert source.files is files
    assert file_references(files) == artifact.files
    with pytest.raises(FrozenInstanceError):
        source.files = ()


@pytest.mark.parametrize("case,error,code", [
    ("artifact", RecordError, "publication-source-view-invalid"),
    ("missing", RecordError, "publication-source-view-invalid"),
    ("changed", RecordError, "publication-source-view-invalid"),
    ("mutable", TypeError, None),
    ("not_file_data", TypeError, None),
    ("duplicate", RecordError, "artifact-path-conflict"),
])
def test_loaded_source_preserves_source_and_byte_consistency_checks(case, error, code):
    item = FileData(FileRef("model.py", BlobRef.of(b"source")), b"source")
    artifact, files = SourceArtifact((item.reference,), "model.py", "Model"), (item,)
    if case == "artifact":
        artifact = None
    elif case == "missing":
        files = ()
    elif case == "changed":
        files = (FileData(FileRef("model.py", BlobRef.of(b"other")), b"other"),)
    elif case == "mutable":
        files = list(files)
    elif case == "not_file_data":
        files = (item.reference,)
    elif case == "duplicate":
        files = (item, item)
    with pytest.raises(error) as found:
        LoadedSource(artifact, files)
    if code is not None:
        assert found.value.code == code
