"""Lossless native payload packing; no model service, GPU or simulator."""

import random
from copy import deepcopy
from dataclasses import replace

import pytest

from behave.train.slime_launch import preset
from behave.train.slime_rollout import coalesce_payloads, prepare_batch, sample_payloads


def payload(turn, inputs, outputs, *, mask=None):
    return {
        "index": 20 + turn, "rollout_id": 7, "group_index": 2, "reward": .5,
        "weight_versions": ["policy"], "tokens": inputs + outputs,
        "response_length": len(outputs), "loss_mask": mask or [1] * len(outputs),
        "rollout_log_probs": [-.1 * (turn + 1)] * len(outputs),
        "metadata": {"turn": turn, "request": {"id": f"request-{turn}"},
                     "response": {"id": f"response-{turn}"}},
    }


def assert_exact(raw, packed):
    original = {item["metadata"]["turn"]: item for item in raw}
    seen = []
    for item in packed:
        prompt = len(item["tokens"]) - item["response_length"]
        full_mask = [0] * prompt + item["loss_mask"]
        assert len(full_mask) == len(item["tokens"])
        assert len(item["rollout_log_probs"]) == item["response_length"]
        assigned = [0] * len(full_mask)
        for span in item["metadata"]["turns"]:
            source = original[span["turn"]]
            start, length = span["response_start"], span["response_length"]
            assert item["tokens"][:start + length] == source["tokens"]
            assert full_mask[start:start + length] == source["loss_mask"]
            offset = start - prompt
            assert item["rollout_log_probs"][offset:offset + length] == source["rollout_log_probs"]
            assigned[start:start + length] = source["loss_mask"]
            assert span["request"] == source["metadata"]["request"]
            assert span["response"] == source["metadata"]["response"]
            seen.append(span["turn"])
        assert assigned == full_mask  # Tools, old foreign replies and prompts stay masked.
    assert sorted(seen) == sorted(original)  # No missing or duplicated original response.
    assert sum(map(lambda p: sum(p["loss_mask"]), raw)) == sum(map(lambda p: sum(p["loss_mask"]), packed))
    assert sum(len(p["tokens"]) for p in packed) <= sum(len(p["tokens"]) for p in raw)


def test_clean_prefixes_merge_all_losses_without_mutating_evidence():
    raw = (payload(0, [1], [2, 3]), payload(1, [1, 2, 3, 4], [5]),
           payload(2, [1, 2, 3, 4, 5, 6, 7], [8, 9], mask=[0, 1]))
    saved = deepcopy(raw)
    packed = coalesce_payloads(raw)
    assert raw == saved
    assert len(packed) == 1 and packed[0]["tokens"] == list(range(1, 10))
    assert packed[0]["loss_mask"] == [1, 1, 0, 1, 0, 0, 0, 1]
    assert_exact(raw, packed)


def test_evicted_and_rewritten_prefixes_are_separate_then_reuse_an_old_branch():
    raw = (payload(0, [1], [2]), payload(1, [1, 2, 3], [4]),
           payload(2, [1, 4, 5], [6]),  # Removed the first answer and its tool result.
           payload(3, [1, 2, 3, 4, 7], [8]))  # Return to the other branch.
    packed = coalesce_payloads(raw)
    assert len(packed) == 2
    assert [span["turn"] for span in packed[0]["metadata"]["turns"]] == [0, 1, 3]
    assert [p["index"] for p in packed] == [20, 21]
    assert_exact(raw, packed)


def test_identical_responses_from_distinct_calls_are_not_deduplicated():
    raw = (payload(0, [1], [2]), payload(1, [1], [2]))
    assert len(coalesce_payloads(raw)) == 2
    assert_exact(raw, coalesce_payloads(raw))


def test_optional_signals_are_preserved_and_missing_signals_do_not_become_fake_data():
    raw = (payload(0, [1], [2]), payload(1, [1, 2, 3], [4]))
    for item in raw:
        item["teacher_log_probs"] = [-.5]
        item["train_metadata"] = {"values": [.25]}
    packed = coalesce_payloads(raw)
    assert packed[0]["teacher_log_probs"] == [-.5, 0., -.5]
    assert packed[0]["train_metadata"] == {"values": [.25, 0., .25]}
    del raw[1]["teacher_log_probs"]
    assert len(coalesce_payloads(raw)) == 2


@pytest.mark.parametrize("seed", range(20))
def test_random_append_eviction_and_branch_histories(seed):
    rng, raw, histories = random.Random(seed), [], [[1, 2]]
    for turn in range(35):
        history = list(rng.choice(histories))
        inputs = history + [rng.randrange(3, 50) for _ in range(rng.randrange(4))]
        outputs = [rng.randrange(50, 90) for _ in range(rng.randrange(1, 6))]
        raw.append(payload(turn, inputs, outputs))
        histories.append(inputs + outputs)
        if rng.random() < .3:
            histories.append([1, 2] + outputs)
    assert_exact(raw, coalesce_payloads(tuple(raw)))


@pytest.mark.parametrize("algorithm,segments", [("grpo", 1), ("gspo", 2), ("ppo", 2)])
def test_recipe_integration_preserves_group_credit_and_other_objectives(tmp_path, monkeypatch, algorithm, segments):
    from test_slime_recipe import candidate, examples

    base = examples(tmp_path, monkeypatch)
    first = base.turns[0]
    second = replace(first, turn=1, tokens=replace(first.tokens,
        input_ids=first.tokens.input_ids + first.tokens.response_ids + (20,)))
    base = replace(base, turns=(first, second))
    other = candidate(base, "other", 0)
    batch = prepare_batch((base, other), group_ids=("group", "group"), recipe=preset(algorithm))
    assert batch.candidates[0]["retained_turns"] == 2
    assert batch.candidates[0]["training_segments"] == segments
    own = tuple(p for p in batch.payloads if p["rollout_id"] == 0)
    if algorithm == "grpo":
        assert_exact(sample_payloads(base, rollout_id=0, first_index=0), own)
    assert all(p["metadata"]["behave_credit"]["value"] == batch.candidates[0]["credit"] for p in own)
