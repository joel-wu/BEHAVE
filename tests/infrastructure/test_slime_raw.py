"""Raw hooks exercise native input/cursor contracts without fake RL rewards."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.core.artifacts import BlobRef, FileRef
from behave.core.records import RecordError, canonical_json
from behave.train.slime_data import (
    RawDataSource,
    convert_samples,
    generate,
    native_contract,
    read_binding,
    validate_args,
)
from behave.train.slime_launch import SlimeLayout
from behave.train.training_data import RawCorpus, RawEncoding, RawSegment


def configured(tmp_path, monkeypatch, *, order="cycle"):
    segments = tuple(RawSegment(FileRef(f"source/{i}.txt", BlobRef.of(str(i).encode())), 0,
                                (100, i, i + 1, 101)) for i in range(5))
    corpus = RawCorpus(BlobRef.of(b"materials"), RawEncoding(BlobRef.of(b"tokenizer"), "tokenizers/fixture", 100, 101, 4), segments)
    monkeypatch.setattr("behave.train.slime_data.load_raw_corpus", lambda reader, reference: corpus)
    binding = {"schema": "behave.slime-raw-binding/1", "objects": str(tmp_path / "objects"),
        "corpus": BlobRef.of(corpus.to_bytes()).as_dict(), "run_directory": str(tmp_path / "run"),
        "sampling": {"sequences_per_update": 2, "order": order, "seed": "repeatable"}}
    path = tmp_path / "binding.json"
    path.write_bytes(canonical_json(binding))
    args = SimpleNamespace(**native_contract(), behave_rollout_binding=str(path),
        behave_rollout_binding_sha256=BlobRef.of(path.read_bytes()).sha256,
        rollout_batch_size=2, save_interval=2, num_rollout=3,
        save=str(tmp_path / "checkpoints"), load=str(tmp_path / "checkpoints"))
    return RawDataSource(args)


@pytest.mark.parametrize("order", ["cycle", "shuffle"])
def test_raw_batch_native_checkpoint_recovery_and_exact_cursor(tmp_path, monkeypatch, order):
    source = configured(tmp_path, monkeypatch, order=order)
    source.load(-1)
    seen = []
    for step in range(3):
        batch = source.batch(step)
        seen.append(batch.indices)
        assert batch.indices == tuple(int(i) for i in source.sampling.select(source.sequence_ids, step))
        # A cold process cannot infer whether the unsaved native update ran.
        with pytest.raises(ValueError, match="pending"):
            RawDataSource(source.args).load(step)
        if step in (1, 2):
            path = Path(source.args.save)
            directory = path / f"iter_{step:07d}"
            directory.mkdir(parents=True)
            (directory / "state.pt").write_bytes(f"opaque Raw checkpoint {step}".encode())
            (path / "latest_checkpointed_iteration.txt").write_text(str(step))
            source.save(step)
            source = RawDataSource(source.args)
            source.load(step)
    if order == "cycle":
        assert seen == [(0, 1), (2, 3), (4, 0)]
    assert [event.kind for event in source.journal.load()] == [
        "raw_batch_ready", "raw_batch_ready", "checkpoint_saved", "raw_batch_ready", "checkpoint_saved"]
    with pytest.raises(ValueError, match="stopping round"):
        source.batch(3)


def sample(segment, index=10):
    return SimpleNamespace(tokens=list(segment.tokens), response_length=segment.targets,
        loss_mask=[1] * segment.targets, reward=None, rollout_log_probs=None, index=index, rollout_id=index,
        metadata={"behave_raw": {"document": segment.document.as_dict(), "offset": segment.offset,
            "tokens": BlobRef.of(canonical_json(list(segment.tokens))).as_dict(),
            "corpus": BlobRef.of(b"corpus").as_dict(), "segment_index": 0}})


def test_native_conversion_has_no_reward_and_rejects_altered_learning_inputs():
    segment = RawSegment(FileRef("source/raw.txt", BlobRef.of(b"source")), 0, (1, 2, 3, 4))
    args = SimpleNamespace(**native_contract())
    converted = convert_samples(args, [sample(segment)])
    assert converted["response_lengths"] == [3]
    assert "rewards" not in converted and "rollout_log_probs" not in converted
    for field, value in (("reward", 0.0), ("response_length", 2), ("loss_mask", [0, 1, 1]),
                         ("tokens", [1, 2, 3, 5]), ("rollout_id", 99)):
        changed = sample(segment)
        setattr(changed, field, value)
        with pytest.raises(RecordError):
            convert_samples(args, [changed])
    with pytest.raises(RecordError, match="order"):
        convert_samples(args, [sample(segment), sample(segment, 12)])


def test_raw_conversion_does_not_revalidate_startup_configuration(monkeypatch):
    monkeypatch.setattr("behave.train.slime_data.validate_args", lambda _: pytest.fail("duplicate startup validation"))
    segment = RawSegment(FileRef("source/raw.txt", BlobRef.of(b"source")), 0, (1, 2, 3))
    assert convert_samples(None, [sample(segment)])["tokens"] == [[1, 2, 3]]


def test_raw_generation_uses_the_data_source_interface():
    calls = []

    def batch(step):
        calls.append(step)
        return SimpleNamespace(output=lambda: "native batch")

    assert generate(None, 9, SimpleNamespace(batch=batch)) == "native batch"
    assert calls == [9]


@pytest.mark.parametrize("name,value", [("loss_type", "policy_loss"), ("compute_advantages_and_returns", True),
    ("calculate_per_token_loss", False), ("use_critic", True), ("use_rollout_logprobs", True),
    ("custom_reward_post_process_path", "custom.reward")])
def test_raw_contract_cannot_silently_become_rl(name, value):
    args = SimpleNamespace(**{**native_contract(), name: value})
    with pytest.raises(ValueError, match=name):
        validate_args(args)


def test_training_only_layout_has_no_inference_gpu_reservation():
    layout = SlimeLayout(actor_gpus_per_node=8, tensor_parallel=4, context_parallel=2,
                         rollout_gpus=0, rollout_gpus_per_engine=3, colocate=False, gpus_per_node=8)
    assert layout.required_gpus == 8
    assert "0" == layout.arguments()[layout.arguments().index("--rollout-num-gpus") + 1]
    with pytest.raises(ValueError):
        replace(layout, rollout_gpus=-1)
