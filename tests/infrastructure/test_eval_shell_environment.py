"""The documented public tools really run in the solver's shared-runtime sandbox."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.resources import files
from textwrap import indent

import pytest

from behave.agent.local_workspace import LocalWorkspace
from behave.agent.prompting import render, role_messages
from behave.core.storage import DirectoryStore
from behave.task_execution.solver_settings import prompt_path, system_variables


def test_eval_document_index_names_existing_public_guides():
    index = render(prompt_path("joint", "environment"))
    system, user = role_messages("solver", user_template="shared/task/user", system_template=prompt_path("joint", "system"),
                                 **system_variables("joint"),
                                 task_id="example", spec_path="spec.md",
                                 public_files_section="", environment_docs=index)
    assert index in user["content"]
    assert index not in system["content"]
    root = files("behave.agent").joinpath("prompts/shared/guides")
    names = {entry.name for entry in root.iterdir() if entry.name.endswith(".md")}
    solver_names = {"development.md", "behavior.md", "fp_lib.md", "memory.md"}
    assert names == solver_names | {"hls.md"}
    assert "/docs/hls.md" not in index
    assert not files("behave.agent").joinpath("prompts/drafts/memory.md").is_file()
    assert not files("behave.agent").joinpath("prompts/drafts/streaming.md").is_file()
    assert not files("behave.agent").joinpath("prompts/drafts/clocks.md").is_file()
    assert "/docs/streaming.md" not in index
    assert "/docs/clocks.md" not in index
    for name in solver_names:
        assert f"/docs/{name}" in index
        body = root.joinpath(name).read_text().strip()
        assert body
        assert body not in user["content"] and body not in system["content"]
    assert "${environment_docs}" in files("behave.agent").joinpath("prompts/shared/task/user.md").read_text()


@pytest.mark.eda
def test_eval_shell_reuses_runtime_without_publishing_sim_code():
    from behave.sim.rtl.tool_isolation import default_limits
    from behave.sim.rtl.tool_runtime import freeze_runtime
    from behave.task_execution.shell import development_shell

    runtime = freeze_runtime("development")
    runner = development_shell(default_limits())
    again = development_shell(default_limits())
    assert freeze_runtime("development") is runtime
    assert runner.template == again.template
    targets = {item.target for item in runner.template.read_only}
    assert {"/docs/development.md", "/docs/behavior.md", "/docs/fp_lib.md", "/docs/memory.md"} <= targets
    assert "/runtime/site/fp_lib.py" in targets
    assert not any("behave" in name or "rtl-tool-job" in name for name in targets)
    assert not any(name.startswith("/workspace") for name in targets)
    binaries = {item.target for item in runner.template.binaries}
    assert {"/tools/verilator", "/tools/yosys", "/tools/cocotb-config",
            "/tools/make", "/usr/bin/g++", "/bin/bash"} <= binaries
    assert "/tools/z3" not in binaries


@pytest.mark.eda
@pytest.mark.isolation
def test_real_eval_shell_cocotb_repair_synthesis_and_documentation(tmp_path, publication):
    from test_attempts import Scripted, call, setup

    from behave.data.published import load_published_task
    from behave.sim.rtl.tool_isolation import default_limits
    from behave.task_execution.capture import load_submission
    from behave.task_execution.shell import development_shell
    from behave.task_execution.solver import run_solver

    runner = development_shell(default_limits())
    guide = files("behave.agent").joinpath("prompts/shared/guides/development.md").read_text()
    makefile = guide.split("```make\n", 1)[1].split("\n```", 1)[0]
    create = """set -e
cat /docs/behavior.md /docs/development.md
python -c 'import importlib.util; assert importlib.util.find_spec("behave") is None; import cocotb; print(cocotb.__version__)'
verilator --version
yosys -V
cat > rtl.sv <<'SV'
module top(input wire [7:0] x, output wire [7:0] y);
helper h(.x(x), .y(y));
endmodule
SV
cat > helper.sv <<'SV'
module helper(input wire [7:0] x, output logic [7:0] y);
always_comb begin
    if (x == 0) y = 8'd2;
    else y = x + 8'd2;
end
endmodule
SV
cat > behavior.py <<'PY'
class Model:
    def reset(self): pass
    def process(self, x): return {"y": (x + 1) & 255}
PY
cat > testbench.py <<'PY'
import cocotb
from cocotb.triggers import Timer
from behavior import Model

@cocotb.test()
async def check(dut):
    model = Model()
    model.reset()
    for x in (0, 1, 2, 63, 127, 128, 254, 255):
        dut.x.value = x
        await Timer(1, unit="ns")
        assert int(dut.y.value) == model.process(x)["y"]
PY
""" + "cat > Makefile <<'MAKE'\n" + makefile + "\nMAKE\nmake\n"
    repair = """set -e
sed -i "s/8'd2/8'd1/" helper.sv
make
verilator_coverage --write-info coverage.info coverage.dat
python - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET
root = ET.parse('results.xml').getroot()
assert list(root.iter('testcase'))
assert not list(root.iter('failure'))
assert not list(root.iter('error'))
coverage = Path('coverage.info').read_text()
assert 'SF:' in coverage and 'DA:' in coverage, coverage
print(coverage)
PY
yosys -p 'read_verilog -sv rtl.sv helper.sv; synth -top top; stat'
"""
    selection = {
        "rtl": {"files": ["rtl.sv", "helper.sv"], "entry_file": "rtl.sv", "entry_symbol": "top"},
        "behavior": {"files": ["behavior.py"], "entry_file": "behavior.py", "entry_symbol": "Model"},
        "testbench": {"files": ["testbench.py", "behavior.py"],
                      "entry_file": "testbench.py", "entry_symbol": "check"},
    }
    journal, spec = setup(tmp_path)
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    backend = Scripted(call("run_command", {"command": create}),
                       call("run_command", {"command": repair}), call("submit", selection))
    result = run_solver(task=public, spec=replace(spec, role="solver"),
                        backend=backend, journal=journal, command_tool=runner.tool(),
                        environment_docs=render(prompt_path("joint", "environment")))
    first = backend.requests[1].messages[-2]["content"]
    second = backend.requests[2].messages[-2]["content"]
    assert first["exit_code"] != 0, first
    assert "AssertionError" in first["stdout"], first
    assert second["exit_code"] == 0, second["stdout"] + second["stderr"]
    assert "ABC RESULTS" in second["stdout"], second
    assert "/docs/development.md" in backend.requests[0].messages[1]["content"]
    submission = load_submission(journal.objects, result.submission)
    assert {file.path for file in submission.rtl.files} == {"rtl.sv", "helper.sv"}
    assert not any(file.path.startswith("sim_build") for file in submission.testbench.files)


@pytest.mark.eda
@pytest.mark.isolation
def test_real_eval_shell_parallel_workspaces_share_only_runtime(tmp_path):
    from behave.sim.rtl.tool_isolation import default_limits
    from behave.task_execution.shell import development_shell

    runner = development_shell(default_limits())

    def run(index):
        directory = tmp_path / str(index)
        directory.mkdir()
        workspace = LocalWorkspace(DirectoryStore(directory / "objects"), directory)
        result = runner.execute(workspace, {"command":
            (f"set -e; printf '{index}' > same-name; verilator --version; "
            f"python -c 'from pathlib import Path; assert Path(\"same-name\").read_text() == \"{index}\"'; "
            "test -r /docs/development.md; test ! -w /docs/development.md")})
        assert result.content["exit_code"] == 0, result.content
        return workspace.read("same-name").decode()

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(run, range(2))) == ["0", "1"]


@pytest.mark.eda
@pytest.mark.isolation
def test_real_cocotb_readonly_example_from_public_guide(tmp_path):
    from behave.sim.rtl.tool_isolation import default_limits
    from behave.task_execution.shell import development_shell

    guide = files("behave.agent").joinpath("prompts/shared/guides/development.md").read_text()
    example = guide.split("```python\n", 1)[1].split("\n```", 1)[0]
    imports, example = example.split("\n\n", 1)
    makefile = guide.split("```make\n", 1)[1].split("\n```", 1)[0].replace(" $(PWD)/helper.sv", "")
    workspace = LocalWorkspace(DirectoryStore(tmp_path / "objects"), tmp_path / "rollout")
    workspace.write("Makefile", (makefile + "\n").encode())
    workspace.write("rtl.sv", b"""module top(input logic clk, rst,
        input logic [7:0] data_in, output logic [7:0] data_out);
    always_ff @(posedge clk) begin
        if (rst) data_out <= 0;
        else data_out <= data_in;
    end
endmodule
""")
    prefix = """import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, ReadOnly

@cocotb.test()
async def check(dut):
    dut.rst.value = 1
    dut.data_in.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst.value = 0
    values = (0, 1, 255, 128, 7, 0)
"""
    workspace.write("testbench.py", (imports + "\n" + prefix + indent(example, "    ") + "\n").encode())
    result = development_shell(default_limits()).execute(workspace, {"command": "make"})
    assert result.content["exit_code"] == 0, result.content["stdout"] + result.content["stderr"]
    from xml.etree import ElementTree as ET
    tree = ET.fromstring(workspace.read("results.xml"))
    assert len(list(tree.iter("testcase"))) == 1
    assert not list(tree.iter("failure")) and not list(tree.iter("error"))
