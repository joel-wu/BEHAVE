"""Opt-in real PyTorch lane. Its absence is not a native-backend pass."""

import os
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from test_trainer_process import numerical  # noqa: F401 -- shared selection fixture
from test_training_checkpoints import fixture as selection_fixture  # noqa: F401

from behave.agent.run_store import JournalDirectory
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.isolation.service import run_service
from behave.train.checkpoint_storage import publish_weights, stage_weights
from behave.train.checkpoints import TrainingLedger, TrainingState, restore_ledger
from behave.train.trainer import TrainerProcess, execute_update

pytestmark = [pytest.mark.native_training, pytest.mark.isolation]
EXPECTED_TORCH = "2.11.0+cu129"


def native_process():
    python = os.environ.get("BEHAVE_NATIVE_TRAINER_PYTHON")
    if not python:
        pytest.skip("native training lane requires an explicit BEHAVE_NATIVE_TRAINER_PYTHON")
    assert Path(python).is_absolute() and Path(python).is_file()
    script = Path(__file__).with_name("support") / "torch_trainer_fixture.py"
    environment = {
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "CUDA_VISIBLE_DEVICES": "",
        }
    if not os.environ.get("BEHAVE_TEST_INSTALLED_PREFIX"):
        # Source-checkout fixtures expose only src/. Installed acceptance uses
        # a wheel in the native prefix, never the control ABI's site-packages.
        environment["PYTHONPATH"] = str(Path(sys.modules["behave"].__file__).resolve().parent.parent)
    return TrainerProcess((python, "-B", str(script)), environment, wall_seconds=120)


def invoke(spec, *args):
    result = run_service(
        (*spec.argv, *map(str, args)),
        cwd=Path("/tmp"),
        environment=spec.environment,
        network="none",
        deadline=time.monotonic() + spec.wall_seconds,
        output_bytes=4 << 20,
    )
    assert result.status == "exited" and result.returncode == 0 and result.cleanup_state == CleanupState.COMPLETE, (
        result.stderr.decode()
    )
    return result.stdout


@pytest.mark.parametrize("optimizer", ["sgd", "adamw"])
def test_native_state_dict_numerics_and_fail_closed(tmp_path, optimizer):
    record = parse_json(invoke(native_process(), "check", tmp_path / "objects", optimizer))
    assert record["torch"] == EXPECTED_TORCH and record["cuda_initialized"] is False
    assert record["negative_controls"] == ["optimizer", "scheduler", "rng", "sampler"]
    assert set(record["refusals"]) == {
        "missing",
        "policy",
        "hash",
        "version",
        "order",
        "unsafe_object",
        "rng_domains",
    }


def test_native_optimizer_through_ledger_cold_restore(tmp_path, numerical):  # noqa: F811
    source, contract, _, request = numerical
    spec = native_process()
    initial = TrainingState.from_bytes(invoke(spec, "initial", source.directory))
    contract = replace(
        contract,
        adapter="torch-cpu-fixture/1",
        components=tuple(f.component for f in initial.files),
        configuration=source.put(canonical_json({"optimizer": "adamw"})),
    )
    original = JournalDirectory(tmp_path / "original")
    with TrainingLedger(original, contract) as ledger:
        checkpoint = ledger.initialize(initial, source)
        for step in range(2):
            checkpoint = execute_update(
                ledger, request(checkpoint.reference, key=f"s{step}", exposure=f"e{step}"), source, spec
            )
        head, refs = ledger.seal()
    cold = DirectoryStore(tmp_path / "cold")
    cold.publish(original.objects, refs)
    restored = JournalDirectory(tmp_path / "restored")
    assert restore_ledger(cold, head, run=restored, contract=contract) == (checkpoint, None)
    with TrainingLedger(restored, contract) as ledger:
        for step in range(2, 4):
            update = request(checkpoint.reference, key=f"s{step}", exposure=f"e{step}")
            checkpoint = execute_update(ledger, update, source, spec)
        assert execute_update(ledger, update, source, replace(spec, argv=("/must/not/launch",))) == checkpoint
        head, refs = ledger.seal()
    compare = DirectoryStore(tmp_path / "compare")
    compare.publish(restored.objects, refs)
    expected = invoke(spec, "baseline", compare.directory)
    left, right = tmp_path / "expected.json", tmp_path / "actual.json"
    left.write_bytes(expected)
    right.write_bytes(checkpoint.state.to_bytes())
    result = parse_json(invoke(spec, "compare", compare.directory, left, right))
    assert result == {"equal": True, "torch": EXPECTED_TORCH, "steps": 4}


def test_native_update_publishes_matching_inference_tensors(tmp_path, numerical):  # noqa: F811
    from test_serving import spec as serving_spec

    source, contract, _, request = numerical
    process = native_process()
    initial = TrainingState.from_bytes(invoke(process, "initial-export", source.directory))
    contract = replace(contract, adapter="torch-cpu-export-fixture/1",
                       components=tuple(dict.fromkeys(f.component for f in initial.files)),
                       configuration=source.put(canonical_json({"optimizer": "adamw", "export_serving": True})))
    original_weights = next(f.content for f in initial.files if f.path == "model.safetensors")
    run = JournalDirectory(tmp_path / "trainer")
    with TrainingLedger(run, contract) as ledger:
        first = ledger.initialize(initial, source)
        checkpoint = execute_update(ledger, request(first.reference), source, process)
        head, _ = ledger.seal()
    assert checkpoint.state.policy != initial.policy
    assert next(f.content for f in checkpoint.state.files if f.path == "model.safetensors") != original_weights
    cold = DirectoryStore(tmp_path / "published")
    publication = publish_weights(run.objects, head, contract=contract, component="serving", objects=cold)
    bound = stage_weights(cold, publication, checkpoint=head, contract=contract, component="serving",
                          spec=serving_spec())
    try:
        state_file = tmp_path / "checkpoint.json"
        state_file.write_bytes(checkpoint.state.to_bytes())
        result = parse_json(invoke(process, "check-weights", cold.directory, state_file, bound.model_path))
        assert result["equal"] and result["step"] == 1 and result["torch"] == EXPECTED_TORCH
        assert result["cuda_initialized"] is False
        assert len(result["prediction"]) == 2
        assert {p.name for p in Path(bound.model_path).iterdir()} == {"config.json", "model.safetensors"}
    finally:
        shutil.rmtree(bound.model_path)
