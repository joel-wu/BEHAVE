"""Opt-in numerical packing check using the installed Slime loss implementation."""

import os
from pathlib import Path

import pytest
from test_native_torch import native_process

from behave.core.records import parse_json
from behave.isolation.service import run_service
from behave.train.slime_launch import environment

pytestmark = [pytest.mark.native_training, pytest.mark.gpu_training, pytest.mark.isolation]


def test_native_grpo_packing_preserves_loss_gradients_and_adam_update(tmp_path, record_testsuite_property):
    if os.environ.get("BEHAVE_TEST_TRAINING_GPUS") != "1":
        pytest.skip("packing check requires an explicitly allocated one-GPU lane")
    spec = native_process()
    prefix = Path(spec.argv[0]).resolve().parents[1]
    env = {**spec.environment, **environment(prefix, tmp_path / "cache",
        cuda_visible_devices=os.environ["CUDA_VISIBLE_DEVICES"]), "TORCHDYNAMO_DISABLE": "1"}
    script = Path(__file__).with_name("support") / "slime_packing_fixture.py"
    result = run_service((spec.argv[0], "-B", str(script), str(tmp_path / "measurement")),
        cwd=tmp_path, environment=env, network="host", deadline=None,
        output_bytes=None, cleanup_seconds=None)
    assert result.status == "exited" and result.returncode == 0 and result.cleanup_state.quiescent, (
        result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace"))
    marker = b"BEHAVE_PACKING_RESULT "
    rows = [parse_json(line[len(marker):]) for line in result.stdout.splitlines() if line.startswith(marker)]
    assert len(rows) == 1 and len(rows[0]["cases"]) == 2
    record_testsuite_property("packing", rows[0])
