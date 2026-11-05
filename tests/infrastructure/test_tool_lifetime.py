"""The inner command deadline includes inherited pipes, not only its leader."""

import pytest

from behave.isolation import IsolationStatus
from behave.sim.rtl.tool_isolation import default_limits, run_workspace
from behave.sim.rtl.tool_protocol import ToolCommand, ToolGroup, command_result, command_stream


@pytest.mark.isolation
def test_exited_leader_with_live_pipe_writer_does_not_hang_the_guest(tmp_path):
    program = (
        "import os,subprocess,sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\n"
        "print('leader exited; child holds stdout', flush=True)\n"
        "os._exit(0)\n"
    )
    result = run_workspace(
        tmp_path, (ToolGroup((ToolCommand("pipe-owner", ("/runtime/python/bin/python", "-c", program), timeout_s=2),)),),
        role="pipe-owner", runtime_profile="simulation", limits=default_limits(wall_seconds=15),
    )
    assert result.status is IsolationStatus.COMPLETED, result.as_dict()
    row = command_result(result, "pipe-owner")
    assert row["returncode"] == 0 and row["timed_out"] and "output_limited" not in row
    assert b"leader exited; child holds stdout" in command_stream(result, "pipe-owner", "stdout")
