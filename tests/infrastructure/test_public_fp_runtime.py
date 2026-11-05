"""Public FP source, development shell and final cocotb use one implementation."""

from importlib.resources import files
from pathlib import Path

import pytest

from behave.agent.local_workspace import LocalWorkspace
from behave.core.artifacts import BlobRef, FileData, FileRef, SourceArtifact
from behave.core.storage import DirectoryStore
from behave.sim import api
from behave.sim.rtl.runtime_dependencies import python_dependency_mounts
from behave.sim.rtl.tool_isolation import default_limits
from behave.task_execution.shell import development_shell

MODEL = b'''from numeric import negate

class Model:
    def reset(self):
        pass
    def process(self, x):
        return {"y": negate(x)}
'''
NUMERIC = b'''from fp_lib import fp32_neg

def negate(x):
    return fp32_neg(x)
'''
RTL = b'''module top(input wire [31:0] x, output wire [31:0] y);
assign y = x ^ 32'h80000000;
endmodule
'''
TB = b'''import importlib.util
from pathlib import Path

import cocotb
from cocotb.triggers import Timer
import fp_lib
from behavior import Model

@cocotb.test()
async def check(dut):
    assert importlib.util.find_spec("behave") is None
    assert fp_lib.__file__ == "/runtime/site/fp_lib.py"
    assert "def fp32_neg(" in Path(fp_lib.__file__).read_text()
    model = Model()
    model.reset()
    for x, expected in ((0, 0x80000000), (1, 0x80000001),
                        (0x80000001, 1), (0x3f800000, 0xbf800000),
                        (0x7f800000, 0xff800000), (0x7f800001, 0xff800001)):
        dut.x.value = x
        await Timer(1, unit="ns")
        assert model.process(x)["y"] == expected
        assert int(dut.y.value) == expected
'''


def source_files():
    return tuple(FileData(FileRef(name, BlobRef.of(data)), data) for name, data in (
        ("behavior.py", MODEL), ("numeric.py", NUMERIC), ("rtl.sv", RTL), ("testbench.py", TB)))


def test_public_fp_mount_equals_the_dependency_captured_by_sim():
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
        {"behavior.py": MODEL, "numeric.py": NUMERIC}, "behavior.py", "Model"))
    dependencies = dict(prepared.dependencies)
    assert set(dependencies) == {"fp_lib.py"}
    mounts = [next(m for m in python_dependency_mounts(profile)
                   if m.target == "/runtime/site/fp_lib.py")
              for profile in ("development", "testbench-build", "testbench")]
    assert mounts[0] == mounts[1] == mounts[2]
    assert Path(mounts[0].source).read_bytes() == dependencies["fp_lib.py"]


@pytest.mark.eda
@pytest.mark.isolation
def test_public_fp_shell_then_final_tb_without_submitting_the_library(tmp_path):
    workspace_dir = tmp_path / "development"
    workspace_dir.mkdir()
    workspace = LocalWorkspace(DirectoryStore(tmp_path / "objects"), workspace_dir)
    selected = source_files()
    workspace.seed(selected)
    guide = files("behave.agent").joinpath("prompts/shared/guides/development.md").read_text()
    makefile = guide.split("```make\n", 1)[1].split("\n```", 1)[0]
    workspace.write("Makefile", makefile.replace(" $(PWD)/helper.sv", "").encode())
    runner = development_shell(default_limits())
    result = runner.execute(workspace, {"command": """set -e
test -r /docs/fp_lib.md
test -r /runtime/site/fp_lib.py
test ! -w /runtime/site/fp_lib.py
python -c 'from fp_lib import fp32_add; assert fp32_add(1, 1) == 2'
make
"""})
    assert result.content["exit_code"] == 0, result.content

    references = {file.reference.path: file.reference for file in selected}
    rtl = SourceArtifact((references["rtl.sv"],), "rtl.sv", "top")
    tb = SourceArtifact(tuple(references[name] for name in
                              ("testbench.py", "behavior.py", "numeric.py")), "testbench.py", "check")
    # Fresh namespace, selected files only, not the developer's Makefile/build.
    final = api.run_cocotb_test(selected, rtl=rtl, testbench=tb, workdir=tmp_path / "final")
    assert (final.status, final.comparison) == ("completed", "passed"), final
    assert final.line_coverage is not None
    assert not any(file.reference.path == "fp_lib.py" for file in selected)
