"""Explicit four-GPU Slime/Megatron acceptance, separate from generic DDP."""

import os
import time
from pathlib import Path

import pytest
from test_native_torch import native_process

from behave.core.lifecycle import CleanupState
from behave.core.records import parse_json
from behave.isolation.service import run_service
from behave.train.slime_launch import environment

pytestmark = [pytest.mark.native_training, pytest.mark.gpu_training, pytest.mark.isolation]


def test_native_slime_algorithms_and_qwen_tp4(tmp_path, record_testsuite_property):
    if os.environ.get("BEHAVE_TEST_TRAINING_GPUS") != "4":
        pytest.skip("Slime TP4 requires an explicitly allocated four-GPU lane")
    spec = native_process()
    devices = os.environ["CUDA_VISIBLE_DEVICES"]
    assert len(set(devices.split(","))) == 4
    prefix = Path(spec.argv[0]).resolve().parent.parent
    env = {**spec.environment, **environment(prefix, tmp_path, cuda_visible_devices=devices),
           "TORCHDYNAMO_DISABLE": "1", "CUBLAS_WORKSPACE_CONFIG": ":4096:8"}
    script = Path(__file__).with_name("support") / "slime_native_fixture.py"
    result = run_service((spec.argv[0], "-m", "torch.distributed.run", "--standalone", "--nproc-per-node=4",
                          str(script), str(tmp_path)), cwd=tmp_path, environment=env, network="host",
                         deadline=time.monotonic() + 720, output_bytes=4 << 20)
    assert result.status == "exited" and result.returncode == 0 and result.cleanup_state == CleanupState.COMPLETE, (
        result.stdout.decode() + "\n" + result.stderr.decode())
    prefix = b"BEHAVE_SLIME_RESULT "
    records = [parse_json(line[len(prefix):]) for line in result.stdout.splitlines() if line.startswith(prefix)]
    assert len(records) == 1 and len(records[0]["algorithms"]) == 6
    assert [(row["tp"], row["cp"]) for row in records[0]["qwen"]] == [(4, 1), (2, 2)]
    record_testsuite_property("slime_native", records[0])
