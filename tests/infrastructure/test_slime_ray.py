"""Real private Ray startup before the much more expensive model gate."""

import os
import time

import pytest
from test_native_torch import native_process

from behave.core.lifecycle import CleanupState
from behave.core.process_limits import process_limit
from behave.isolation.scratch import ScratchDirectory
from behave.isolation.service import run_service

pytestmark = [pytest.mark.native_training, pytest.mark.isolation]


def test_native_ray_private_loopback_and_remote_task(tmp_path):
    spec = native_process()
    state = [CleanupState.UNKNOWN]
    with ScratchDirectory(prefix="br-", cleanup=lambda: state[0]) as cache:
        code = """
import faulthandler, os, sys
from behave.train.slime_driver import CONTROL_ADDRESS, private_loopback
private_loopback(int(sys.argv[1]))
faulthandler.dump_traceback_later(90, repeat=True)
import ray
from ray._private.services import resolve_ip_for_localhost
assert resolve_ip_for_localhost(CONTROL_ADDRESS) == CONTROL_ADDRESS
ray.init(address='local', include_dashboard=False, num_cpus=2, num_gpus=0,
         _node_ip_address=CONTROL_ADDRESS, _temp_dir=sys.argv[2],
         object_store_memory=256 << 20)
try:
    assert ray.get_runtime_context().gcs_address.startswith(CONTROL_ADDRESS + ':')
    @ray.remote
    def worker():
        return os.getpid(), ray.get_runtime_context().gcs_address
    pid, address = ray.get(worker.remote(), timeout=30)
    assert pid != os.getpid() and address.startswith(CONTROL_ADDRESS + ':')
    print('BEHAVE_PRIVATE_RAY_OK', flush=True)
finally:
    ray.shutdown()
    faulthandler.cancel_dump_traceback_later()
"""
        result = run_service((spec.argv[0], "-B", "-c", code,
            str(os.stat("/proc/self/ns/net").st_ino), str(cache)), cwd=tmp_path,
            environment={**spec.environment, "RAY_USAGE_STATS_ENABLED": "0"}, network="none",
            deadline=time.monotonic() + 300, output_bytes=2 << 20, cleanup_seconds=30,
            process_limit=process_limit(None))
        state[0] = result.cleanup_state
        assert result.status == "exited" and result.returncode == 0 and state[0].quiescent, (
            result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace"))
        assert b"BEHAVE_PRIVATE_RAY_OK" in result.stdout
