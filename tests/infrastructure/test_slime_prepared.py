"""Prepared IPC preserves training inputs without another source-audit pass."""

import json
from types import SimpleNamespace

import pytest
from test_slime_application import configured

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.train.slime_data import ScoredDataSource
from behave.train.slime_launch import preset
from behave.train.slime_rollout import SlimeBatch, load_batch, prepare_recorded_batches, save_batch


def fixture_batch():
    return SlimeBatch(({
        "tokens": [10, 11, 12, 13, 14], "loss_mask": [1, 0, 1], "response_length": 3,
        "rollout_log_probs": [-0.1, 0.0, -0.0], "teacher_log_probs": [-0.2, 0.0, -0.3],
        "train_metadata": {"values": [1, 0, 2.5]},
        "metadata": {"turns": [{"turn": 0}, {"turn": 2}], "behave_credit": {"value": 0.5}},
    },), ({"reward": 1, "credit": 0.5, "retained_turns": 2},), preset("grpo"))


def test_prepared_roundtrip_preserves_masks_signals_and_candidate_credit(tmp_path):
    batch = fixture_batch()
    ref = BlobRef.of(b"fixture")
    path = tmp_path / "prepared.json"
    save_batch(path, batch, head=ref, manifest=ref, plan=ref)
    restored = load_batch(path, head=ref, manifest=ref, plan=ref, recipe=batch.recipe)
    assert restored == batch
    assert canonical_json(restored.payloads) == canonical_json(batch.payloads)
    assert canonical_json(restored.candidates) == canonical_json(batch.candidates)
    with pytest.raises(FileExistsError):
        save_batch(path, batch, head=ref, manifest=ref, plan=ref)


@pytest.mark.parametrize("key", ["head", "manifest", "plan", "recipe"])
def test_prepared_batch_remains_bound_to_its_producer_inputs(tmp_path, key):
    batch = fixture_batch()
    ref = BlobRef.of(b"fixture")
    args = {"head": ref, "manifest": ref, "plan": ref, "recipe": batch.recipe}
    path = tmp_path / "prepared.json"
    save_batch(path, batch, head=ref, manifest=ref, plan=ref)
    args[key] = preset("ppo") if key == "recipe" else BlobRef.of(b"different")
    with pytest.raises(ValueError, match="another report"):
        load_batch(path, **args)


def recorded_source(tmp_path):
    original = configured(tmp_path)
    reader = DirectoryStore(original.binding["objects"])
    row = {"schema": "behave.slime-scored-round/1", "round": 4,
        "objects": str(tmp_path / "original_objects"), "head": BlobRef.of(b"head").as_dict(),
        "manifest": original.binding["manifest"], "endpoint": original.binding["endpoint"],
        "policy": original.binding["endpoint"]["tokens"]["checkpoint"]}
    ref = reader.put(canonical_json(row))
    original.binding["recorded_rounds"] = [ref.as_dict(), ref.as_dict()]
    original.path.write_bytes(canonical_json(original.binding))
    return original, row, ref


def test_prepare_distinct_receipt_once_before_native_model_start(tmp_path, monkeypatch):
    from behave.train import slime_rollout as slime_rollout_module

    source, row, reference = recorded_source(tmp_path)
    calls = []
    monkeypatch.setattr(slime_rollout_module, "round_plan", lambda *a, **kw: SimpleNamespace(to_bytes=lambda: b"plan"))
    def prepare(reader, head, **kwargs):
        calls.append(head)
        return fixture_batch()
    monkeypatch.setattr(slime_rollout_module, "from_training_report", prepare)
    timings = prepare_recorded_batches(tmp_path)
    assert len(calls) == len(timings) == 1
    assert timings[0]["candidates"] == 1 and timings[0]["segments"] == 1
    assert prepare_recorded_batches(tmp_path) == []
    assert len(calls) == 1
    binding = json.loads(source.path.read_bytes())
    path = binding["prepared_batches"][reference.sha256]
    restored = load_batch(path, head=BlobRef.from_dict(row["head"]),
        manifest=BlobRef.from_dict(row["manifest"]), plan=BlobRef.of(b"plan"), recipe=preset("grpo"))
    assert restored == fixture_batch()


def test_recorded_consumer_does_not_reopen_original_evidence(tmp_path, monkeypatch):
    from behave.train import slime_data as slime_data_module

    original, row, reference = recorded_source(tmp_path)
    path = tmp_path / "prepared.json"
    save_batch(path, fixture_batch(), head=BlobRef.from_dict(row["head"]),
        manifest=BlobRef.from_dict(row["manifest"]), plan=BlobRef.of(b"plan"))
    original.binding["prepared_batches"] = {reference.sha256: str(path)}
    original.path.write_bytes(canonical_json(original.binding))
    original.args.behave_rollout_binding_sha256 = BlobRef.of(original.path.read_bytes()).sha256
    source = ScoredDataSource(original.args)
    monkeypatch.setattr(slime_data_module, "round_plan", lambda *a, **kw: SimpleNamespace(to_bytes=lambda: b"plan"))
    monkeypatch.setattr(slime_data_module, "from_training_report", lambda *a, **kw: pytest.fail("audit replay"))
    monkeypatch.setattr(slime_data_module, "run_service", lambda *a, **kw: pytest.fail("rollout dispatched"))
    monkeypatch.setattr(SlimeBatch, "rollout_output", lambda self: self)
    source.load(-1)
    assert source.generate(0) == fixture_batch()
    assert source.journal.load()[-1].payload["training_segments"] == 1


def test_raw_route_does_not_enter_task_preparation(tmp_path):
    (tmp_path / "binding.json").write_text(json.dumps({"schema": "behave.slime-raw-binding/1"}))
    assert prepare_recorded_batches(tmp_path) == []


@pytest.mark.isolation
def test_live_rollout_handoff_consumes_producer_objects_without_reverification(tmp_path, publication, monkeypatch):
    from dataclasses import replace

    from test_native_chat_tokens import native_training_run

    from behave.task_execution import batch as batch_module
    from behave.train import recorded, rollouts
    from behave.train import slime_rollout as slime_rollout_module
    from behave.train import training_data as training_data_module

    prepared = []
    def compact_export(plan, reader, report, verifier, **kwargs):
        assert kwargs["token_statuses"]
        forbidden = set()
        for row in report.summary["candidates"]:
            if row["result"] is not None:
                result = json.loads(reader.read(BlobRef.from_dict(dict(row["result"]))))
                account = json.loads(reader.read(BlobRef.from_dict(result["accounting"])))
                forbidden.update(BlobRef.from_dict(t["response"]) for t in account["turns"])
        class MetadataReader:
            def read(self, ref):
                assert ref not in forbidden, "end-of-round export reread a token payload"
                return reader.read(ref)
        result = recorded.record_rollouts(plan, MetadataReader(), report, verifier, **kwargs)
        return replace(result, reader=reader)
    monkeypatch.setattr(rollouts, "prepare_rollouts", compact_export)
    monkeypatch.setattr(training_data_module.NativeChatVerifier, "verify", lambda *a: pytest.fail("audit during rollout export"))
    monkeypatch.setattr(batch_module, "load_accounting", lambda *a: pytest.fail("accounting audit during rollout export"))
    run, manifest, verifier, result, endpoint = native_training_run(
        tmp_path, publication, on_prepared=prepared.append, update_groups=1)
    assert len(prepared) == 1 and prepared[0].summary == result
    assert result["group_selection"]["selected_indices"] == [0]
    monkeypatch.setattr(training_data_module.NativeChatVerifier, "verify", lambda *a: pytest.fail("second verification"))
    monkeypatch.setattr(training_data_module, "load_accounting", lambda *a: pytest.fail("second accounting traversal"))
    batch = slime_rollout_module.from_prepared_report(prepared[0], verifier=verifier, recipe=preset("ppo"))
    assert len(batch.payloads) == 3
    assert [p["metadata"]["behave_prefix_cache"]["cached_tokens"] for p in batch.payloads] == [0, 2, 2]
    assert all(p["rollout_log_probs"] == [-0.5] * 5 for p in batch.payloads)
    head, _ = run.seal("training")
    plan = BlobRef.of(prepared[0].plan.to_bytes())
    path = tmp_path / "batch.json"
    save_batch(path, batch, head=head, manifest=manifest, plan=plan)
    assert load_batch(path, head=head, manifest=manifest, plan=plan, recipe=batch.recipe) == batch
