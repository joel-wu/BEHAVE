"""Opt-in two-GPU native lane: no CPU fallback, no model downloads or RL run."""

import os
from dataclasses import replace
from pathlib import Path

import pytest
from test_distributed_training import ddp_cold_resume
from test_native_torch import EXPECTED_TORCH, invoke, native_process
from test_trainer_process import numerical  # noqa: F401
from test_training_checkpoints import fixture as selection_fixture  # noqa: F401

from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json
from behave.train.checkpoints import TrainingLedger, TrainingState
from behave.train.trainer import DistributedLaunch, TrainerExecutionError, execute_update

pytestmark = [pytest.mark.native_training, pytest.mark.gpu_training, pytest.mark.isolation]


def gpu_process():
    if int(os.environ.get("BEHAVE_TEST_TRAINING_GPUS", "0")) == 0:
        pytest.skip("GPU training requires an explicit allocated GPU validation lane")
    assert os.environ["BEHAVE_TEST_TRAINING_GPUS"] == "2"
    devices = os.environ["CUDA_VISIBLE_DEVICES"]
    assert len(set(devices.split(","))) == 2
    native = native_process()
    script = Path(__file__).with_name("support") / "ddp_trainer_fixture.py"
    return replace(native, argv=(native.argv[0], "-B", str(script)), wall_seconds=180,
                   environment={**native.environment, "CUDA_VISIBLE_DEVICES": devices,
                                "BEHAVE_FIXTURE_CUDA": "1", "CUBLAS_WORKSPACE_CONFIG": ":4096:8"},
                   distributed=DistributedLaunch(native.argv[0], 2))


def test_nccl_ddp_adamw_checkpoint_cold_resume(tmp_path, numerical, record_testsuite_property):  # noqa: F811
    evidence = ddp_cold_resume(tmp_path, numerical, gpu_process())
    assert evidence["equal"] and evidence["steps"] == 2 and evidence["ranks"] == 2
    assert evidence["backend"] == "nccl" and evidence["torch"] == EXPECTED_TORCH
    assert len({device["uuid"] for device in evidence["devices"]}) == 2
    record_testsuite_property("native_runtime", canonical_json(evidence).decode())


def test_nccl_rank_failure_leaves_pending_checkpoint_and_no_retry(tmp_path, numerical):  # noqa: F811
    source, contract, _, request = numerical
    process = gpu_process()
    initial = TrainingState.from_bytes(invoke(process, "initial", source.directory))
    contract = replace(contract, adapter="torch-ddp-fixture/1",
                       components=tuple(dict.fromkeys(file.component for file in initial.files)),
                       configuration=source.put(canonical_json({"optimizer": "adamw"})))
    process = replace(process, environment={**process.environment, "BEHAVE_FIXTURE_FAIL_RANK": "1"})
    run = JournalDirectory(tmp_path / "run")
    with TrainingLedger(run, contract) as ledger:
        before = ledger.initialize(initial, source)
        update = request(before.reference)
        with pytest.raises(TrainerExecutionError) as error:
            execute_update(ledger, update, source, process)
        receipt = parse_json(run.objects.read(error.value.receipt))
        assert b"fixture-nccl-rank-failed-after-update" in run.objects.read(BlobRef.from_dict(receipt["stderr"]))
        assert receipt["state"] is None and error.value.workspace is None
        assert ledger.checkpoint == before and ledger.pending is not None
        with pytest.raises(AttemptInterrupted, match="training-update-outcome-unknown"):
            execute_update(ledger, update, source, process)
