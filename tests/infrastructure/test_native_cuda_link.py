"""Native JIT driver linkage without model loading or GPU allocation."""

import time
from pathlib import Path

import pytest
from test_native_torch import native_process

from behave.isolation.service import run_service
from behave.train.slime_launch import environment

pytestmark = [pytest.mark.native_training, pytest.mark.isolation]


def test_native_cuda_driver_linkage(tmp_path):
    spec = native_process()
    env = {**spec.environment, **environment(Path(spec.argv[0]).resolve().parents[1], tmp_path,
                                            cuda_visible_devices="0")}
    source = tmp_path / "driver.cpp"
    binary = tmp_path / "driver"
    source.write_text('extern "C" int cuDriverGetVersion(int*);\n'
                      'int main() { int version = 0; return cuDriverGetVersion(&version); }\n')
    for argv in (("/usr/bin/c++", str(source), "-lcuda", "-o", str(binary)), (str(binary),)):
        result = run_service(argv, cwd=tmp_path, environment=env, network="none",
                             deadline=time.monotonic() + 30, output_bytes=1 << 20)
        assert result.status == "exited" and result.returncode == 0 and result.cleanup_state.quiescent, (
            result.status, result.stdout, result.stderr)
