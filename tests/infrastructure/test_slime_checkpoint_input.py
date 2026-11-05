"""Native-to-serving file handoff; the optimizer and tensor values are fixtures."""

import json
from pathlib import Path

import pytest
from test_slime_training_job import job
from test_slime_training_plan import inputs

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from tools.slime_checkpoint_input import prepare
from tools.slime_training_job import execute


@pytest.fixture
def completed(job):
    output, local, _ = job
    assert execute(str(output), str(local)) == 0
    # The native worker fixture writes one fake shard, not a loadable model.
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (output / "artifacts/final_hf" / name).write_text("{}")
    return output


def test_handoff_binds_native_history_and_hf_metadata(completed, monkeypatch):
    original = Path.open

    def no_weight_reads(path, *args, **kwargs):
        assert path.suffix != ".safetensors", "handoff must not reread weight tensors"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_weight_reads)
    value = prepare(completed, workers=2)
    assert value["step"] == 2 and len(value["learning_heads"]) == 2
    assert value["weights"]["checkpoint"] == value["checkpoint"]
    assert value["model_revision"] == BlobRef.of(canonical_json(value["weights"])).sha256
    assert {file["path"] for file in value["weights"]["files"]} == {
        "config.json", "tokenizer.json", "tokenizer_config.json", "weight.safetensors"}
    assert value["weights"]["schema"] == "behave.native-hf-handoff/2"
    assert all(set(file) == {"path", "size_bytes"} for file in value["weights"]["files"])
    assert prepare(completed, workers=1) == value
    monkeypatch.setattr(Path, "open", original)
    (completed / "artifacts/final_hf/weight.safetensors").write_bytes(b"different fixture weights")
    assert prepare(completed, workers=1)["model_revision"] != value["model_revision"]


def test_single_phase_formal_job_can_supply_syn_checkpoint(job, monkeypatch):
    output, local, _ = job
    path = output / "configuration.json"
    config = json.loads(path.read_text())
    config["resume_updates"] = 0
    path.write_text(json.dumps(config))
    monkeypatch.setattr("behave.train.slime_checkpoint.scored_rounds",
                        lambda _: [{"round": i} for i in range(config["initial_updates"])])
    assert execute(str(output), str(local)) == 0
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
        (output / "artifacts/final_hf" / name).write_text("{}")
    value = prepare(output)
    assert value["step"] == 1 and len(value["learning_heads"]) == 1
    assert not (output / "artifacts/plan/learning/resume").exists()


def test_handoff_uses_completed_receipt_without_rereading_logs(completed, monkeypatch):
    original = Path.read_text

    def no_log_reads(path, *args, **kwargs):
        assert path.name not in {"stdout", "stderr"}
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", no_log_reads)
    assert prepare(completed)["step"] == 2


@pytest.mark.parametrize("state", ["preparing", "training", "interrupted"])
def test_running_or_failed_job_cannot_supply_a_checkpoint(tmp_path, state):
    (tmp_path / "runtime.json").write_text(json.dumps({"state": state}))
    with pytest.raises(ValueError, match="has not completed"):
        prepare(tmp_path, workers=1)


@pytest.mark.parametrize("change", ["checkpoint", "acceptance", "phase", "weights"])
def test_inconsistent_or_missing_handoff_evidence_is_rejected(completed, change):
    artifacts = completed / "artifacts"
    if change == "checkpoint":
        (artifacts / "checkpoint/latest_checkpointed_iteration.txt").write_text("1")
    elif change == "acceptance":
        path = artifacts / "plan/result.json"
        row = json.loads(path.read_text())
        row["native_cycle_completed"] = False
        path.write_text(json.dumps(row))
    elif change == "phase":
        path = artifacts / "plan/resume.result.json"
        row = json.loads(path.read_text())
        row["report"]["stop_update"] += 1
        path.write_text(json.dumps(row))
    else:
        (artifacts / "final_hf/model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {"w": "missing.safetensors"}}))
    with pytest.raises(ValueError):
        prepare(completed, workers=1)
