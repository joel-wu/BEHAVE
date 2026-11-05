"""Raw plan construction uses the same driver without launching native jobs."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_slime_training_plan import inputs

from behave.core.artifacts import BlobRef, FileRef
from behave.core.storage import DirectoryStore
from behave.train.slime_data import read_raw_binding as read_binding
from behave.train.slime_launch import native_arguments
from behave.train.training_data import RawCorpus, RawEncoding, RawSegment
from tools.slime_raw_plan import prepare


def raw_inputs(tmp_path, monkeypatch, inputs):
    config, _, _, _ = inputs
    config = {**config, "layout": {**config["layout"], "rollout_gpus": 0, "colocate": False},
        "sampler": {"sequences_per_update": 2, "order": "cycle", "seed": "raw"},
        "native_arguments": ["--bf16", "--optimizer", "adam", "--lr", "1e-6"]}
    tokenizer = BlobRef.of((Path(config["model_path"]) / "tokenizer.json").read_bytes())
    segments = tuple(RawSegment(FileRef(f"source/{i}.txt", BlobRef.of(str(i).encode())), 0, (1, i, 2))
                     for i in range(4))
    data = RawCorpus(BlobRef.of(b"verified materials"), RawEncoding(tokenizer, "tokenizers/fixture", 1, 2, 4096), segments)
    monkeypatch.setattr("behave.train.slime_data.load_raw_corpus", lambda *args: data)
    store = DirectoryStore(tmp_path / "raw-objects")
    return config, store, store.put(data.to_bytes()), data


def test_raw_plan_has_explicit_token_budget_and_no_generation_flags(tmp_path, monkeypatch, inputs):
    config, store, ref, data = raw_inputs(tmp_path, monkeypatch, inputs)
    output = tmp_path / "raw-plan"
    binding = prepare(output, config, store.directory, ref)
    assert binding == read_binding(output / "binding.json")
    selected = json.loads((output / "training_selection.json").read_text())
    assert selected["materials"] == data.materials.as_dict()
    assert selected["corpus_target_tokens"] == 8
    assert selected["native_training_started"] is False
    args = native_arguments(output, updates=2, load=tmp_path / "initial", cpus=16,
                            checkpoint_directory=tmp_path / "checkpoint")
    for key, value in {"--behave-learning-mode": "raw", "--seq-length": "4096",
        "--global-batch-size": "2", "--rollout-batch-size": "2", "--rollout-num-gpus": "0",
        "--loss-type": "sft_loss"}.items():
        assert args.count(key) == 1 and args[args.index(key) + 1] == value
    assert "--debug-train-only" in args
    assert not any(arg.startswith("--sglang-") for arg in args)
    assert not (output / "rounds").exists()


def test_raw_preparation_rejects_wrong_tokenizer_and_empty_material(tmp_path, monkeypatch, inputs):
    config, store, ref, data = raw_inputs(tmp_path, monkeypatch, inputs)
    output = tmp_path / "invalid"
    wrong = replace(data, encoding=replace(data.encoding, tokenizer=BlobRef.of(b"other tokenizer")))
    monkeypatch.setattr("behave.train.slime_data.load_raw_corpus", lambda *args: wrong)
    with pytest.raises(ValueError, match="tokenizer differs"):
        prepare(output, config, store.directory, ref)
    assert not output.exists()
    monkeypatch.setattr("behave.train.slime_data.load_raw_corpus", lambda *args: replace(data, segments=()))
    with pytest.raises(ValueError):
        prepare(output, config, store.directory, ref)
    assert not output.exists()


def test_raw_preparation_rejects_inference_reservation(tmp_path, monkeypatch, inputs):
    config, store, ref, _ = raw_inputs(tmp_path, monkeypatch, inputs)
    config["layout"].update(rollout_gpus=8, colocate=True)
    with pytest.raises(ValueError, match="no inference GPU"):
        prepare(tmp_path / "invalid", config, store.directory, ref)
