"""ABC progress survives a synthesis deadline without changing its commands."""

import io
import sys
from types import SimpleNamespace

import pytest

from behave.core.artifacts import LoadedSource, SourceArtifact
from behave.isolation import IsolationStatus
from behave.ppa import eda
from behave.ppa._abc_trace import run
from behave.ppa.workflow import eda_feedback
from test_ppa import RTL, experiment, file


def test_trace_preserves_arguments_output_and_nonzero_exit(tmp_path):
    output = io.BytesIO()
    log = tmp_path / "abc.log"
    code = run(["-c", "import sys; print('+ &fraig -x'); print('failure', file=sys.stderr); sys.exit(7)"],
               executable=sys.executable, log=log, output=output)
    assert code == 7
    assert b"+ &fraig -x" in output.getvalue() and b"failure" in output.getvalue()
    text = log.read_text()
    assert "] + &fraig -x" in text and "exit=7" in text


def test_checkpoint_is_optional_and_does_not_change_mapping_recipe():
    item = file("top.sv", RTL)
    source = LoadedSource(SourceArtifact((item.reference,), "top.sv", "M"), (item,))
    normal = eda.yosys_script(source, experiment().timing)
    diagnostic = eda.yosys_script(source, experiment().timing, checkpoint=True)
    assert diagnostic.replace("yosys write_rtlil pre_abc.il\n", "") == normal
    assert " -script " not in normal and " -dff" not in normal
    assert diagnostic.index("write_rtlil pre_abc.il") < diagnostic.index("abc -exe")


@pytest.mark.parametrize("status", [IsolationStatus.MEMORY_LIMIT, IsolationStatus.CPU_LIMIT,
    IsolationStatus.PROCESS_LIMIT, IsolationStatus.TIMED_OUT])
def test_resource_failure_reaches_agent(status):
    stage = eda.stage_status(SimpleNamespace(status=status, commands=()), "synthesis")
    assert stage["reason"] == status.value
    result = {"status": stage["status"], "area_um2": None, "timing": None, "log": "",
              "failed_stage": "synthesis", "stages": {"synthesis": stage}}
    public, files = eda_feedback(result, prefix="eda/resource")
    assert public["failure"]["reason"] == status.value
    assert status.value in files[0].data.decode()


def test_agent_sees_operation_reached_before_timeout():
    result = {"status": "timeout", "area_um2": None, "timing": None,
              "log": "[2.100s] + &fraig -x\n", "failed_stage": "synthesis",
              "stages": {"synthesis": {"reason": "tool-deadline", "budget_seconds": 1200,
                         "last_operation": "&fraig -x"}}}
    public, files = eda_feedback(result, prefix="eda/timeout")
    assert public["failure"]["last_operation"] == "&fraig -x"
    assert "Last ABC operation started: &fraig -x" in files[0].data.decode()
    assert files[1].data.decode() == result["log"]
