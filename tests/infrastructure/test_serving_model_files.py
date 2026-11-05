"""Local weight availability, without importing a framework or loading tensors."""

import hashlib
import time

import pytest
from test_serving import spec

from behave.core.records import canonical_json
from behave.isolation.service import ServiceResult
from behave.serving import environment as provision


@pytest.fixture
def model(tmp_path, monkeypatch):
    root = tmp_path / "model"
    root.mkdir()
    (root / "config.json").write_bytes(b'{}\n')
    calls = []

    def inspect(argv, **kwargs):
        calls.append(argv)
        return ServiceResult("exited", 0, canonical_json({
            "runtime": "0.28.0", "torch": "2.13.0+cu130"}), b"", .01)

    monkeypatch.setattr(provision, "run_service", inspect)

    def check():
        return provision.inspect_installation(
            spec(model_path=str(root)), tmp_path, {}, deadline=time.monotonic() + 10)

    return root, check, calls


@pytest.mark.parametrize("layout", ["single", "indexed", "unindexed"])
@pytest.mark.parametrize("linked", [False, True])
def test_local_safetensors_layouts_need_no_index_for_a_single_file(model, layout, linked):
    root, check, calls = model
    names = ("model.safetensors",) if layout == "single" else (
        "model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors")
    for name in names:
        if linked:
            blob = root.parent / (name + ".blob")
            blob.write_bytes(b"fixture, not real tensor contents")
            (root / name).symlink_to(blob)
        else:
            (root / name).write_bytes(b"fixture, not real tensor contents")
    if layout == "indexed":
        index = {"metadata": {"total_size": 1}, "weight_map": {
            "layer.weight": names[0], "layer.bias": names[0], "other.weight": names[1]}}
        data = canonical_json(index)
        if linked:
            blob = root.parent / "index.blob"
            blob.write_bytes(data)
            (root / "model.safetensors.index.json").symlink_to(blob)
        else:
            (root / "model.safetensors.index.json").write_bytes(data)
        # The backend's index filters duplicate/consolidated weight files.
        (root / "model.safetensors").write_bytes(b"not selected by the index")
    result = check()
    assert result == {"runtime": "0.28.0", "torch": "2.13.0+cu130",
                      "config_sha256": hashlib.sha256(b'{}\n').hexdigest(),
                      "weight_shards": len(names), "weight_content_attested": False}
    assert len(calls) == 1  # Version probe only: no downloader or weight loader.


@pytest.mark.parametrize("index", [
    {}, {"weight_map": {}}, {"weight_map": []}, {"weight_map": {"x": []}},
    {"weight_map": {"x": "../outside.safetensors"}},
    {"weight_map": {"x": "/outside.safetensors"}},
    {"weight_map": {"x": "not-weights.txt"}},
    {"weight_map": {"x": "missing.safetensors"}},
    {"weight_map": {"x": "\x00.safetensors"}},
])
def test_bad_or_incomplete_index_never_falls_back_to_other_weights(model, index):
    root, check, _ = model
    (root / "model.safetensors").write_bytes(b"does not rescue a bad index")
    (root / "not-weights.txt").write_bytes(b"not a tensor file")
    (root / "model.safetensors.index.json").write_bytes(canonical_json(index))
    with pytest.raises(ValueError, match="local model"):
        check()


@pytest.mark.parametrize("kind", ["directory", "dangling"])
def test_valid_unindexed_weights_do_not_hide_an_invalid_neighbor(model, kind):
    root, check, _ = model
    (root / "model.safetensors").write_bytes(b"fixture tensors")
    bad = root / "another.safetensors"
    if kind == "directory":
        bad.mkdir()
    else:
        bad.symlink_to(root / "missing")
    with pytest.raises(ValueError, match="local model"):
        check()


@pytest.mark.parametrize("payload", [b"not json", b"[]", b"null", b'\xff'])
def test_malformed_index_is_not_ignored(model, payload):
    root, check, _ = model
    (root / "model.safetensors").write_bytes(b"no fallback")
    (root / "model.safetensors.index.json").write_bytes(payload)
    with pytest.raises(ValueError, match="local model"):
        check()


@pytest.mark.parametrize("entry", ["missing", "weight-directory", "broken-weight-link",
                                   "index-directory", "broken-index-link"])
def test_missing_or_nonregular_model_inputs_are_rejected(model, entry):
    root, check, _ = model
    if entry == "weight-directory":
        (root / "model.safetensors").mkdir()
    elif entry == "broken-weight-link":
        (root / "model.safetensors").symlink_to("absent")
    elif entry.startswith("index-") or entry == "broken-index-link":
        (root / "model.safetensors").write_bytes(b"no fallback past a bad index")
        index = root / "model.safetensors.index.json"
        if entry == "index-directory":
            index.mkdir()
        else:
            index.symlink_to("absent")
    with pytest.raises(ValueError, match="local model"):
        check()
