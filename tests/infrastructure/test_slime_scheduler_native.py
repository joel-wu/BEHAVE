"""Native scheduler/Adam cold continuation without full-model allocation."""

import os
import time
from pathlib import Path

import pytest
from test_native_torch import native_process

from behave.core.records import parse_json
from behave.isolation.service import run_service
from behave.train.slime_launch import environment

pytestmark = [pytest.mark.native_training, pytest.mark.isolation]


def test_native_scheduler_retains_saved_horizon_and_adam(tmp_path):
    spec = native_process()
    root = Path(__file__).resolve().parents[2]
    profile = parse_json((root / "infra/model_runtimes/local-serving.json").read_bytes())["shared"]
    megatron = Path(profile["model_path"]).parents[6] / "external/megatron-upstream"
    env = {**spec.environment, **environment(Path(spec.argv[0]).resolve().parents[1], tmp_path,
                                            cuda_visible_devices="0", megatron_source=megatron)}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        spec.environment.get("PYTHONPATH"), env.get("PYTHONPATH"))))
    env["CUDA_VISIBLE_DEVICES"] = ""
    script = Path(__file__).with_name("support") / "slime_scheduler_fixture.py"
    result = run_service((spec.argv[0], "-B", str(script)), cwd=tmp_path, environment=env,
                         network="none", deadline=time.monotonic() + 180, output_bytes=1 << 20)
    assert result.status == "exited" and result.returncode == 0 and result.cleanup_state.quiescent, (
        result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace"))
    assert b"BEHAVE_SCHEDULER_RESUME_OK" in result.stdout
