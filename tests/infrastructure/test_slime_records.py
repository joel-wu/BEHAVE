"""Training consumes its own token exports, without audit replay or regrouping."""

import json
import os
from dataclasses import replace

import pytest

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.task_execution.judgment import ComponentJudgment, SubmissionJudgment
from behave.task_execution.result import TaskResult
from behave.train.slime_launch import ALGORITHMS, preset
from behave.train.slime_rollout import load_recorded_batch, pack_report, prepare_batch
from behave.train.training_data import RolloutExamples, TokenTrace, TrainingCandidate, TrainingRequirements, TurnExample


def data(tmp_path):
    store = DirectoryStore(tmp_path / "objects")
    ref = store.put(b"fixture")
    requirements = TrainingRequirements("fixture/1", ref, ref, ref, ("old_log_probs",))
    candidates, rows = [], []
    for index, reward in enumerate((1, 0, 1, 0)):
        judgment = SubmissionJudgment(ref, None, ComponentJudgment(reward, "ok"), ref)
        result = TaskResult("task", ref, ref, f"candidate-{index}", ref, "final_action",
                            ref, judgment, None, None, ref)
        result_ref = store.put(result.to_bytes())
        first = TokenTrace(requirements.adapter, ref, ref, ref, ref, (10, 11), (12, 13), (1, 1),
                           {"old_log_probs": [-.5, -.25]})
        following = replace(first, input_ids=(10, 11, 12, 13, 14), response_ids=(15, 16))
        evicted = replace(first, input_ids=(20, 21), response_ids=(22, 23))
        turns = tuple(TurnExample(i, ref, ref, "eligible", None, trace)
                      for i, trace in enumerate((first, following, evicted)))
        turns += (TurnExample(3, ref, ref, "excluded", "output-length"),)
        # All-excluded candidates still contribute their reward to the group.
        if index == 3:
            turns = turns[-1:]
        examples = RolloutExamples(TrainingCandidate(result_ref, result), turns, requirements)
        candidates.append(examples)
        rows.append({"index": index, "group_id": "task", "task_id": "task", "attempt_id": result.attempt_id,
                     "error": None, "examples": examples.as_dict()})
    plan = store.put(canonical_json({"manifest": ref.as_dict(), "group_ids": ["task"] * 4}))
    report = {"plan": plan.as_dict(), "candidates": rows}
    return store, ref, report, tuple(candidates)


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_direct_report_preserves_existing_packing_and_credit(tmp_path, algorithm):
    store, _, report, candidates = data(tmp_path)
    recipe = preset(algorithm)
    expected = prepare_batch(candidates, group_ids=("task",) * 4, recipe=recipe, first_rollout=8, first_sample=20)
    actual = pack_report(report, store, recipe=recipe, first_rollout=8, first_sample=20, directory=tmp_path)
    assert actual == expected
    assert sum(sum(p["loss_mask"]) for p in actual.payloads) == 18
    assert len(actual.candidates) == 4


def test_multiprocess_matches_serial_and_uses_worker_processes(tmp_path):
    store, _, report, _ = data(tmp_path)
    serial = pack_report(report, store, recipe=preset("grpo"), directory=tmp_path / "serial")
    parallel = pack_report(report, store, recipe=preset("grpo"), workers=2,
                           start_method="spawn", directory=tmp_path / "parallel")
    assert serial == parallel
    timing = json.loads(next((tmp_path / "parallel").glob("*/timing.json")).read_text())
    assert timing["workers"] == 2
    assert all(row["pid"] != os.getpid() for row in timing["candidates"])


def test_one_missing_turn_keeps_other_tokens_candidate_reward_and_group_credit(tmp_path):
    store, _, report, candidates = data(tmp_path)
    original = prepare_batch(candidates, group_ids=("task",) * 4, recipe=preset("grpo"))
    turn = report["candidates"][0]["examples"]["turns"][0]
    turn.update(status="unavailable", reason="missing-native-logprobs", tokens=None)
    changed = replace(candidates[0], turns=(replace(candidates[0].turns[0],
        status="unavailable", reason="missing-native-logprobs", tokens=None), *candidates[0].turns[1:]))
    expected = prepare_batch((changed, *candidates[1:]), group_ids=("task",) * 4, recipe=preset("grpo"))
    actual = pack_report(report, store, recipe=preset("grpo"), workers=2, directory=tmp_path)
    assert actual == expected
    assert [row["reward"] for row in actual.candidates] == [row["reward"] for row in original.candidates]
    assert [row["credit"] for row in actual.candidates] == [row["credit"] for row in original.candidates]
    assert actual.candidates[0]["unavailable_turns"] == 1
    assert actual.candidates[0]["retained_turns"] == 2
    assert sum(sum(part["loss_mask"]) for part in actual.payloads) == 16
    first = next(part for part in actual.payloads if part["rollout_id"] == 0)
    # The missing response stays in the later turn's actual input context,
    # but receives no invented old probabilities or loss-bearing targets.
    assert first["tokens"] == [10, 11, 12, 13, 14, 15, 16]
    assert first["response_length"] == 2 and first["loss_mask"] == [1, 1]
    assert first["rollout_log_probs"] == [-.5, -.25]


@pytest.mark.parametrize("algorithm", ALGORITHMS)
@pytest.mark.parametrize("generic_reader", [False, True])
def test_response_references_preserve_all_tokens_masks_logprobs_and_credit(tmp_path, algorithm, generic_reader):
    from behave.train.learning_report import reference_tokens

    store, _, report, candidates = data(tmp_path)
    for row in report["candidates"]:
        for turn in row["examples"]["turns"]:
            if turn["tokens"] is not None:
                turn["response"] = store.put(canonical_json({"token_data": turn["tokens"]})).as_dict()
    compact = reference_tokens(report)
    assert "input_ids" not in canonical_json(compact).decode()

    class Reader:
        def read(self, ref):
            return store.read(ref)

    # Compare with the inline report after assigning the same response refs.
    expected = pack_report(report, store, recipe=preset(algorithm), workers=1,
                           directory=tmp_path / "inline")
    actual = pack_report(compact, Reader() if generic_reader else store, recipe=preset(algorithm),
                         workers=2, directory=tmp_path / "references")
    assert actual == expected
    assert sum(sum(p["loss_mask"]) for p in actual.payloads) == 18
    jobs = list((tmp_path / "references").glob("*/candidate_*.json"))
    jobs = [p for p in jobs if not p.name.endswith(".packed.json")]
    assert jobs and all("input_ids" not in p.read_text() for p in jobs)


def test_sdk_update_reads_selected_tokens_from_compact_response(tmp_path):
    from types import SimpleNamespace

    from behave.train.checkpoints import Evidence
    from behave.train.learning_report import reference_tokens
    from behave.train.update_records import ExampleUse, TrainerContract, UpdateRequest

    store, ref, report, _ = data(tmp_path)
    turn = report["candidates"][0]["examples"]["turns"][0]
    response = store.put(canonical_json({"token_data": turn["tokens"]}))
    turn["response"] = response.as_dict()
    evidence = Evidence(TrainerContract("fixture/1", ref, ref), None,
        reports={ref: reference_tokens(report)}, checkpoint=SimpleNamespace(reference=ref))
    request = UpdateRequest("step", ref, (ExampleUse(ref, 0, 0, "first"),), ref)
    prepared = evidence.prepare(store, request)
    assert TokenTrace.from_dict(thaw_json(prepared.examples[0]["tokens"])) == TokenTrace.from_dict(turn["tokens"])
    assert response in evidence.references


def test_retained_report_never_reads_requests_responses_or_accounting(tmp_path, monkeypatch):
    from behave.task_execution import accounting
    from behave.train import learning_report as audit
    from behave.train import training_data
    store, manifest, report, _ = data(tmp_path)
    saved = store.put(canonical_json(report))
    head = store.put(canonical_json({"kind": "training_finished", "payload": {"result": saved.as_dict()}}))
    def forbidden(*args, **kwargs):
        pytest.fail("historical audit replay")
    monkeypatch.setattr(audit, "prepare_training_report", forbidden)
    monkeypatch.setattr(accounting, "load_accounting", forbidden)
    monkeypatch.setattr(training_data.NativeChatVerifier, "verify", forbidden)
    allowed = {head, saved, BlobRef.from_dict(report["plan"]),
               *(BlobRef.from_dict(row["examples"]["result"]) for row in report["candidates"])}
    class Reader:
        def read(self, ref):
            assert ref in allowed
            return store.read(ref)
    batch = load_recorded_batch(Reader(), head, approved_manifest=manifest,
        expected_plan=BlobRef.from_dict(report["plan"]), recipe=preset("grpo"),
        first_rollout=0, first_sample=0, workers=1, start_method="spawn", directory=tmp_path)
    assert len(batch.payloads) == 6 and len(batch.candidates) == 4


@pytest.mark.parametrize("change,match", [("reward", "recorded result differ"), ("group", "recorded result differ"),
                                         ("mask", "response-aligned"), ("logprob", "response-aligned")])
def test_structural_errors_do_not_silently_change_training(tmp_path, change, match):
    store, _, report, _ = data(tmp_path)
    row = report["candidates"][0]
    if change == "reward":
        row["examples"]["reward"] = None
    elif change == "group":
        row["task_id"] = "another-task"
    elif change == "mask":
        row["examples"]["turns"][0]["tokens"]["response_mask"].pop()
    else:
        row["examples"]["turns"][0]["tokens"]["signals"]["old_log_probs"].pop()
    with pytest.raises(ValueError, match=match):
        pack_report(report, store, recipe=preset("grpo"), directory=tmp_path)
