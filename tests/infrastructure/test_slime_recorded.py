"""Recorded batches reuse the scored native path without calling a model."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_slime_application import configured
from test_slime_training_plan import inputs

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.train.slime_data import ScoredDataSource
from behave.train.slime_rollout import read_binding


def test_recorded_preparation_preserves_canonical_binding_identity(tmp_path):
    from tools.slime_recorded_job import prepare

    original, local, output = (tmp_path / name for name in ("original", "local", "output"))
    old_plan = original / "plan"
    old_plan.mkdir(parents=True)
    local.mkdir()
    output.mkdir()
    source = configured(old_plan)
    DirectoryStore(old_plan / "inputs").put(b"original evidence")
    config = {"layout": {"rollout_gpus": 8, "colocate": True}, "checkpoint_interval": 5,
              "native_arguments": ["--clip-grad", "1", "--sglang-mem-fraction-static", "0.7"]}
    for name, value in (("configuration.json", config), ("training_selection.json", {}),
                        ("source_split.json", {})):
        (old_plan / name).write_text(json.dumps(value))
    receipt_path = old_plan / "rounds/native/request_00000000/response.json"
    receipt_path.parent.mkdir(parents=True)
    # An archive may still name a dead worker's scratch paths.
    source.binding["run_directory"] = "/tmp/lost-worker/plan/rounds"
    (old_plan / "binding.json").write_bytes(canonical_json(source.binding))
    receipt = {"head": BlobRef.of(b"score").as_dict(),
               "objects": "/tmp/lost-worker/plan/rounds/round_00000000/objects",
               "prepared_batch": "/tmp/lost-worker/plan/rounds/round_00000000/prepared_batch.json"}
    receipt_path.write_text(json.dumps(receipt))
    plan, result = prepare(output, local, {"source_plan": str(old_plan), "updates": 1, "resume_updates": 1,
                           "preparation_workers": 2, "preparation_start_method": "spawn", "cpu_threads": 4,
                           "kernel_cache": str(tmp_path / "native-kernels")},
                           {"native_python": "/native/python"})
    path = plan / "binding.json"
    assert path.read_bytes() == canonical_json(read_binding(path))
    source.args.behave_rollout_binding = str(path)
    source.args.behave_rollout_binding_sha256 = BlobRef.of(path.read_bytes()).sha256
    recorded = ScoredDataSource(source.args)
    assert len(recorded.binding["recorded_rounds"]) == 2
    assert result["checkpoint_interval"] == 5
    assert recorded.binding["preparation_workers"] == 2 and result["cpu_threads"] == 4
    assert result["kernel_cache"] == str(tmp_path / "native-kernels")
    assert result["layout"] == {"rollout_gpus": 0, "colocate": False}
    assert result["native_arguments"][:2] == ["--clip-grad", "1"]
    assert not any(value.startswith("--sglang-") for value in result["native_arguments"])
    assert plan == output / "artifacts/plan"
    assert not (local / "plan").exists()
    assert json.loads(receipt_path.read_text()) == receipt
    restored = json.loads((output / "recorded_input.json").read_text())
    assert restored["head"] == receipt["head"]
    assert restored["objects"] == str(old_plan / "rounds/round_00000000/objects")
    assert restored["prepared_batch"] == str(old_plan / "rounds/round_00000000/prepared_batch.json")
    assert list(recorded.binding["prepared_batches"].values()) == [restored["prepared_batch"]]


def test_recorded_submit_uses_shared_inputs_without_old_worker_runtime(tmp_path, inputs, monkeypatch):
    from tools.slime_recorded_job import submit

    config, _, _, _ = inputs
    job = tmp_path / "source-job"
    job.mkdir()
    (job / "configuration.json").write_text(json.dumps(config))
    # Deliberately no runtime.json, old host or local_directory.
    monkeypatch.setattr("behave.runtime.snapshots.freeze", lambda *a, **kw: None)
    seen = {}

    def detach(directory, argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return {"submitted": True}

    monkeypatch.setattr("behave.runtime.jobs.submit", detach)
    output = tmp_path / "new-job"
    assert submit(output, job, 27419, 90, 1, 16, "spawn", 8) == {"submitted": True}
    selection = json.loads((output / "selection.json").read_text())
    assert selection["source_plan"] == str(job / "artifacts/plan")
    assert selection["model_path"] == config["model_path"]
    assert "original_host" not in selection and "original_local" not in selection
    assert "--cpus-per-task=90" in seen["argv"] and seen["wall_seconds"] is None
    override = tmp_path / "recovery-job"
    submit(override, job, 27419, 90, 1, 16, "spawn", 8,
           source_plan=tmp_path / "recovered-plan", model_path=tmp_path / "shared-model",
           kernel_cache=tmp_path / "native-kernels")
    selected = json.loads((override / "selection.json").read_text())
    assert selected["source_plan"] == str(tmp_path / "recovered-plan")
    assert selected["model_path"] == str(tmp_path / "shared-model")
    assert selected["kernel_cache"] == str(tmp_path / "native-kernels")
    runtime = tmp_path / "maintained.json"
    runtime.write_text(json.dumps({**config, "tokenize_workers": 2, "checkpoint_interval": 5}))
    output = tmp_path / "cold-probe"
    submit(output, job, 27419, 90, 1, 16, "spawn", 8,
           configuration=runtime, group="mixed-task", resume_updates=1)
    assert (output / "source_configuration.json").read_bytes() == runtime.read_bytes()
    selected = json.loads((output / "selection.json").read_text())
    assert selected["group"] == "mixed-task" and selected["resume_updates"] == 1


def test_group_probe_keeps_complete_candidates_and_original_loss_data():
    from behave.train.slime_launch import preset
    from behave.train.slime_rollout import SlimeBatch
    from tools.slime_recorded_job import select_group

    candidates = tuple({"rollout_id": i, "group_id": "a" if i < 2 else "b", "reward": i % 2}
                       for i in range(4))
    parts = tuple({"index": i, "rollout_id": rid, "tokens": [i, 7, 8], "loss_mask": [0, 1],
                   "rollout_log_probs": [-1., -2.], "metadata": {"behave_credit": {"value": float(rid % 2)}}}
                  for i, rid in enumerate([0, 1, 2, 2, 3]))
    batch = SlimeBatch(parts, candidates, preset("grpo"), group_selection={"update_groups": 2})
    subset = select_group(batch, "b")
    assert subset.candidates == candidates[2:]
    assert [part["rollout_id"] for part in subset.payloads] == [2, 2, 3]
    assert [part["index"] for part in subset.payloads] == [0, 1, 2]
    for old, new in zip(parts[2:], subset.payloads, strict=True):
        for key in ("tokens", "loss_mask", "rollout_log_probs", "metadata"):
            assert old[key] == new[key]
    assert subset.group_selection is None and batch.group_selection == {"update_groups": 2}
    assert [part["index"] for part in parts] == list(range(5))
    with pytest.raises(ValueError, match="no group"):
        select_group(batch, "missing")


def test_recorded_cold_resume_uses_new_phase_and_previous_full_checkpoint(tmp_path, monkeypatch):
    from behave.core.lifecycle import CleanupState
    from tools import slime_recorded_job

    output, local, model = [tmp_path / name for name in ("output", "worker", "model")]
    for path in (output, local, model):
        path.mkdir()
    (model / "weights").write_bytes(b"source")
    (output / "selection.json").write_text(json.dumps({"cpus": 90, "updates": 1, "resume_updates": 1,
        "preparation_workers": 16, "cpu_threads": 8, "source_plan": "original", "model_path": str(model)}))
    install = output / "artifacts/installation"
    install.mkdir(parents=True)
    (install / "installed.json").write_text(json.dumps({"preparation_seconds": 1,
        "base_native_python": "/native/bin/python", "native_python": "/overlay/bin/python"}))
    plan = output / "artifacts/plan"
    plan.mkdir()
    seen = []

    def learn(**kwargs):
        seen.append(kwargs)
        directory = kwargs["run"].directory
        directory.mkdir(parents=True)
        (directory / "stdout").write_text("step 0: {'train/step': 0, 'train/grad_norm': 1.0}\n")
        (directory / "stderr").write_text("")
        (plan / f"hf_{kwargs['updates']-1}").mkdir()
        return {"status": "completed", "checkpoint": {"step": kwargs["updates"]-1}}

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1,2,3,4,5,6,7")
    monkeypatch.setattr(slime_recorded_job, "prepare", lambda *args: (plan, {
        "model_path": str(local / "weights"), "slime_source": "/native/slime", "cpu_threads": 8}))
    monkeypatch.setattr("behave.train.slime_rollout.prepare_recorded_batches", lambda *args: [])
    monkeypatch.setattr("behave.train.slime_launch.environment", lambda *args, **kw: {})
    monkeypatch.setattr(slime_recorded_job, "mkdtemp", lambda **kw: str(local / "cache"))
    monkeypatch.setattr("behave.train.slime_stage.run_learning", learn)
    monkeypatch.setattr("behave.isolation.service.run_service", lambda *a, **kw: SimpleNamespace(
        status="exited", returncode=0, cleanup_state=CleanupState.COMPLETE,
        stdout=b'[{"changed_elements":1},{"changed_elements":2}]', stderr=b""))
    assert slime_recorded_job.execute(output, local) == 0
    assert [(call["updates"], call["load"]) for call in seen] == [
        (1, local / "weights"), (2, output / "artifacts/checkpoints")]
    assert [call["run"].directory.name for call in seen] == ["recorded", "resume"]
    assert all(call["seconds"] is None and call["cleanup_seconds"] is None for call in seen)
    status = json.loads((output / "runtime.json").read_text())
    assert status["native_update_and_cold_resume"] is True
    assert status["weight_change"] == {"changed_elements": 2}
    assert status["learned_accuracy_measured"] is False


def test_recorded_failure_keeps_logs_and_timing_on_shared_storage(tmp_path, monkeypatch):
    from tools import slime_recorded_job

    output, local = tmp_path / "output", tmp_path / "worker"
    output.mkdir()
    local.mkdir()
    model = tmp_path / "shared-model"
    model.mkdir()
    (model / "weights").write_bytes(b"working model")
    (output / "selection.json").write_text(json.dumps({"cpus": 90, "updates": 1,
        "preparation_workers": 16, "cpu_threads": 8, "source_plan": str(tmp_path / "source-plan"),
        "model_path": str(model)}))
    installation = output / "artifacts/installation"
    installation.mkdir(parents=True)
    (installation / "installed.json").write_text(json.dumps({"preparation_seconds": 1,
        "base_native_python": "/native/bin/python", "native_python": "/overlay/bin/python"}))
    plan = output / "artifacts/plan"
    plan.mkdir()
    prepared = []

    def prepare_batch(directory):
        assert directory == plan
        prepared.append(True)
        return []

    def learn(**kwargs):
        assert prepared and kwargs["save"] == output / "artifacts/checkpoints"
        assert kwargs["load"] == local / "weights"
        assert (local / "weights/weights").read_bytes() == b"working model"
        assert kwargs["seconds"] is kwargs["cleanup_seconds"] is None
        run = kwargs["run"].directory
        run.mkdir(parents=True)
        (run / "stdout").write_text("training began\n")
        (run / "stderr").write_text("interrupted\n")
        timing = Path(kwargs["environment"]["BEHAVE_TRAIN_TIMING_DIRECTORY"])
        assert timing == output / "artifacts/timing"
        timing.mkdir()
        (timing / "rank0.jsonl").write_text('{"stage":"forward"}\n')
        return {"status": "interrupted", "checkpoint": None}

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1,2,3,4,5,6,7")
    monkeypatch.setattr(slime_recorded_job, "prepare", lambda *args: (plan, {
        "model_path": str(local / "weights"), "slime_source": "/native/slime", "cpu_threads": 8,
        "process_limit": None}))
    monkeypatch.setattr("behave.train.slime_rollout.prepare_recorded_batches", prepare_batch)
    monkeypatch.setattr("behave.train.slime_launch.environment", lambda *a, **kw: {})
    monkeypatch.setattr(slime_recorded_job, "mkdtemp", lambda **kw: str(local / "cache"))
    monkeypatch.setattr("behave.train.slime_stage.run_learning", learn)
    with pytest.raises(RuntimeError, match="did not complete"):
        slime_recorded_job.execute(output, local)
    local.rename(tmp_path / "retired-worker")
    assert (plan / "learning/recorded/stdout").read_text() == "training began\n"
    assert (output / "artifacts/timing/rank0.jsonl").is_file()
    assert not (output / "artifacts/final_hf").exists()
    runtime = json.loads((output / "runtime.json").read_text())
    assert runtime["state"] == "interrupted" and runtime["report"]["checkpoint"] is None


def test_recorded_round_keeps_original_policy_and_uses_shared_batch(tmp_path, monkeypatch):
    from behave.train import slime_data as slime_data_module

    original = configured(tmp_path)
    binding = original.binding
    inputs = DirectoryStore(binding["objects"])
    endpoint = binding["endpoint"]
    row = {"schema": "behave.slime-scored-round/1", "round": 4,
        "objects": str(tmp_path / "recorded"), "head": BlobRef.of(b"head").as_dict(),
        "manifest": binding["manifest"], "endpoint": endpoint,
        "policy": endpoint["tokens"]["checkpoint"]}
    reference = inputs.put(canonical_json(row))
    binding["recorded_rounds"] = [reference.as_dict()]
    original.path.write_bytes(canonical_json(binding))
    original.args.behave_rollout_binding_sha256 = BlobRef.of(original.path.read_bytes()).sha256
    source = ScoredDataSource(original.args)
    observed = []

    def expected(*args, **kwargs):
        assert kwargs["round_id"] == 4
        assert kwargs["policy"].as_dict() == row["policy"]
        return SimpleNamespace(to_bytes=lambda: b"original plan")

    def batch(reader, head, **kwargs):
        observed.append(head)
        assert kwargs["expected_plan"] == BlobRef.of(b"original plan")
        assert kwargs["approved_manifest"].as_dict() == binding["manifest"]
        return SimpleNamespace(candidates=({"retained_turns": 5},), payloads=({}, {}),
                               rollout_output=lambda: "shared native output")

    monkeypatch.setattr(slime_data_module, "round_plan", expected)
    monkeypatch.setattr(slime_data_module, "from_training_report", batch)
    monkeypatch.setattr(slime_data_module, "engine_versions", lambda *a, **kw: pytest.fail("no serving in replay"))
    monkeypatch.setattr(slime_data_module, "run_service", lambda *a, **kw: pytest.fail("no rollout in replay"))
    source.load(-1)
    assert source.generate(0) == "shared native output"
    assert observed == [BlobRef.from_dict(row["head"])]
    event = source.journal.load()[-1]
    assert event.kind == "round_scored"
    assert event.payload["recorded_round"] == 4
    assert event.payload["retained_turns"] == 5 and event.payload["training_segments"] == 2
    with pytest.raises(ValueError, match="already used"):
        source.generate(0)


@pytest.mark.parametrize("recorded", [[], None, "path"])
def test_recorded_binding_needs_explicit_receipt_sequence(tmp_path, recorded):
    source = configured(tmp_path)
    binding = {**source.binding, "recorded_rounds": recorded}
    source.path.write_bytes(canonical_json(binding))
    with pytest.raises(ValueError, match="recorded_rounds"):
        read_binding(source.path)
