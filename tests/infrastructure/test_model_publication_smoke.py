"""Initial-checkpoint handoff fixture; fake tensors, not live-model acceptance."""

import os
import shutil
from pathlib import Path

import pytest
from test_serving import load_tool, spec

from behave.core.artifacts import DirectoryReader
from behave.core.records import canonical_json, parse_json
from behave.train.checkpoint_storage import load_weights
from behave.train.checkpoints import load_checkpoint


@pytest.fixture
def model(tmp_path):
    directory = tmp_path / "cache"
    directory.mkdir()
    payloads = {"config.json": b'{}', "model.safetensors": b"fixture tensors",
                "tokenizer.json": b'{}', "tokenizer_config.json": b'{"chat_template":"fixture"}'}
    for name, data in payloads.items():
        (directory / name).write_bytes(data)
    return directory, payloads


@pytest.mark.parametrize("indexed", [False, True])
def test_fixture_publishes_exact_initial_bytes_without_cache_fallback(tmp_path, monkeypatch, model, indexed):
    tool = load_tool(monkeypatch, "model_publication_smoke")
    from model_weight_input import WeightInput

    source, payloads = model
    if indexed:
        index = canonical_json({"weight_map": {"weight": "model.safetensors"}})
        payloads["model.safetensors.index.json"] = index
        (source / "model.safetensors.index.json").write_bytes(index)
    # Existing HF-style symlinks are captured as bytes, not exported as links.
    original = source / "model.safetensors"
    original.rename(tmp_path / "blob")
    original.symlink_to(tmp_path / "blob")
    result = tool.publish_fixture(source, "declared-revision", tuple(payloads), tmp_path / "capture")
    selected = WeightInput.read(Path(result["weights_input"]))
    reader = DirectoryReader(selected.objects)
    checkpoint, pending = load_checkpoint(reader, selected.checkpoint, contract=selected.contract)
    assert pending is None
    assert checkpoint.state.cursor == {"acceptance_only": True, "optimizer_updates": 0}
    assert result["optimizer_updates"] == 0 and result["bytes"] == sum(map(len, payloads.values()))
    assert selected.contract.adapter == "cached-weight-acceptance-only/1"
    publication, files = load_weights(reader, selected.publication, checkpoint=selected.checkpoint,
                                     contract=selected.contract, component="model")
    assert publication.policy == checkpoint.state.policy
    assert {file.path for file in files} == set(payloads)
    policy = parse_json(reader.read(publication.policy))
    assert policy["declared_revision"] == "declared-revision"
    assert policy["files"] == {file.path: file.content.as_dict() for file in files}
    shutil.rmtree(source)
    (tmp_path / "blob").unlink()
    bound = selected.stage(spec(model_path="/no/cache/fallback"))
    try:
        assert bound.model_revision == selected.publication.sha256
        assert {f.relative_to(bound.model_path).as_posix(): f.read_bytes()
                for f in Path(bound.model_path).rglob("*") if f.is_file()} == payloads
    finally:
        shutil.rmtree(bound.model_path)


@pytest.mark.parametrize("bad", ["fifo", "directory", "bad-index", "missing-shard",
                                  "duplicate", "parent", "reserved", "relative", "no-config"])
def test_bad_capture_never_opens_an_output_or_publishes_a_root(tmp_path, monkeypatch, model, bad):
    tool = load_tool(monkeypatch, "model_publication_smoke")
    source, payloads = model
    names = tuple(payloads)
    if bad in {"fifo", "directory"}:
        (source / "model.safetensors").unlink()
        if bad == "fifo":
            os.mkfifo(source / "model.safetensors")
        else:
            (source / "model.safetensors").mkdir()
    elif bad in {"bad-index", "missing-shard"}:
        names += ("model.safetensors.index.json",)
        data = b"invalid" if bad == "bad-index" else canonical_json({"weight_map": {"w": "missing.safetensors"}})
        (source / "model.safetensors.index.json").write_bytes(data)
    elif bad == "duplicate":
        names += (names[0],)
    elif bad == "parent":
        names += ("../escape",)
    elif bad == "reserved":
        names += ("acceptance",)
    elif bad == "relative":
        source = Path("relative")
    elif bad == "no-config":
        names = ("model.safetensors",)
    with pytest.raises((ValueError, TypeError)):
        tool.publish_fixture(source, "revision", names, tmp_path / "capture")
    assert not (tmp_path / "capture").exists()


def test_interrupted_capture_has_no_usable_publication_input(tmp_path, monkeypatch, model):
    tool = load_tool(monkeypatch, "model_publication_smoke")
    from behave.train import checkpoints as checkpoints_module

    source, payloads = model
    original = checkpoints_module.store_state_file

    def mutate(stream, *args, **kwargs):
        captured = original(stream, *args, **kwargs)
        (source / kwargs["path"]).write_bytes(b"changed during capture")
        return captured

    monkeypatch.setattr(checkpoints_module, "store_state_file", mutate)
    with pytest.raises(ValueError, match="source changed"):
        tool.publish_fixture(source, "revision", tuple(payloads), tmp_path / "capture")
    assert not (tmp_path / "capture/weights.json").exists()
    assert not (tmp_path / "capture/result.json").exists()


