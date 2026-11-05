"""Settings change instructions and submission shape, not the solver loop."""

import json
from dataclasses import replace
from importlib.resources import files
from types import SimpleNamespace

import pytest
from test_attempts import Scripted, call, local_shell, setup

from behave.agent.prompting import render, role_messages
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef, FileData, FileRef
from behave.core.records import parse_json
from behave.data.published import load_published_task
from behave.data.spec_format import without_behavior_interface
from behave.task_execution.capture import load_submission
from behave.task_execution.solver import run_solver
from behave.task_execution.solver_settings import guide_names, prompt_path, render_prompt, system_variables

PUBLIC_SPEC = b'''# Counter
## Interface
### RTL interface
module top(input clk, rst, output [7:0] count);
### Behavior interface
class Counter:
    def process(self, increment): ...
#### Return value
Return a Python dictionary.
### Transaction protocol
Sample on each rising edge.
## Functional Behavior
Increment the counter.
## Reset Behavior
Reset clears the counter.
'''


@pytest.mark.parametrize("setting", ["joint", "rtl_only"])
def test_setting_routes_real_loop_messages_and_submission(tmp_path, publication, setting):
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    public = replace(public, files=tuple(
        FileData(FileRef(public.spec_path, BlobRef.of(PUBLIC_SPEC)), PUBLIC_SPEC)
        if file.reference.path == public.spec_path else file for file in public.files))
    description = render(prompt_path(setting, "submit"))
    arguments = json.loads(description.split("```json\n", 1)[1].split("\n```", 1)[0])
    journal, spec = setup(tmp_path)
    spec = replace(spec, role="solver", max_turns=7)
    # A fixture command writes the files used by the real submit example.
    names = {name for selection in arguments.values() for name in selection["files"]}
    backend = Scripted(call("run_command", {"command": "touch " + " ".join(sorted(names))}),
                       call("submit", arguments))
    environment = render(prompt_path(setting, "environment"))
    outcome = run_solver(task=public, spec=spec, backend=backend, journal=journal,
                         command_tool=local_shell(), setting=setting, environment_docs=environment)
    assert outcome.status == "awaiting_judgment"  # No Sim call or invented score.
    captured = load_submission(journal.objects, outcome.submission)
    assert captured.setting == setting
    assert captured.rtl.artifact is not None
    assert (captured.behavior.selection is not None) == (setting == "joint")
    assert (captured.testbench.selection is not None) == (setting == "joint")
    assert len(backend.requests) == 2
    for turn, request in enumerate(backend.requests, 1):
        assert [tool["name"] for tool in request.tools] == ["run_command", "submit"]
        submit = request.tools[1]
        assert set(submit["parameters"]["properties"]) == set(arguments)
        assert set(submit["parameters"]["required"]) == set(arguments)
        assert submit["description"] == description
        assert request.messages[0]["content"] == render_prompt(setting, "system")
        assert f"Runtime status: turn {turn} of 7." in request.messages[-1]["content"]
    if setting == "rtl_only":
        initial = "\n".join(message["content"] for message in backend.requests[0].messages)
        for word in ("verification", "testbench", "hidden reference", "Behavior", "/docs/behavior.md"):
            assert word not in initial
            assert word not in description
        for name in ("development", "fp_lib", "memory"):
            assert f"/docs/{name}.md" in initial
        assert "cocotb" in initial  # Public tools are available, not extra required artifacts.
        assert submit["parameters"]["properties"]["rtl"]["type"] == "object"
    configuration = parse_json(journal.objects.read(
        BlobRef.from_dict(dict(journal.load()[0].payload["configuration"]))))
    assert configuration["messages"] == list(backend.requests[0].messages[:-1])
    # Check the actual solver workspace, not just the prompt's spec filename.
    initial_workspace = Workspace(journal.objects)
    initial_workspace.restore(BlobRef.from_dict(configuration["workspace"]))
    expected_spec = (without_behavior_interface(PUBLIC_SPEC.decode()).encode()
                     if setting == "rtl_only" else PUBLIC_SPEC)
    assert initial_workspace.read(public.spec_path) == expected_spec
    assert initial_workspace.snapshot().file(public.contract_path) is None
    assert next(file.data for file in public.files if file.reference.path == public.spec_path) == PUBLIC_SPEC
    assert (b"class Counter" in expected_spec) == (setting == "joint")
    assert b"Reset clears the counter." in expected_spec


@pytest.mark.parametrize("setting", ["joint", "rtl_only"])
@pytest.mark.parametrize("extra_guides", [(), ("hls",)])
def test_setting_mounts_only_its_guides_and_reuses_the_tool_environment(monkeypatch, setting, extra_guides):
    from behave.isolation import IsolationSpec, ResourceLimits, WritableRoot, binary
    from behave.task_execution import shell

    limits = ResourceLimits()
    base = IsolationSpec(argv=("/bin/bash",), binaries=(binary("/bin/bash", "/bin/bash"),),
                         writable=(WritableRoot("/work", limits.file_bytes),), limits=limits)
    prepared = replace(base, environment=(("PUBLIC_LIBRARY", "shared"),))
    monkeypatch.setattr(shell, "shell_commands", lambda actual: SimpleNamespace(template=base))
    calls = []

    def prepare(template):
        calls.append(template)
        return prepared

    monkeypatch.setattr(shell.api, "prepare_development_environment", prepare)
    runner = shell.development_shell(limits, setting=setting, extra_guides=extra_guides)
    assert calls == [base]
    assert runner.template.binaries == prepared.binaries
    assert runner.template.environment == prepared.environment
    assert runner.template.limits == limits
    mounts = {mount.target: mount for mount in runner.template.read_only}
    assert set(mounts) == {f"/docs/{name}.md" for name in (*guide_names(setting), *extra_guides)}
    index = render(prompt_path(setting, "environment"))
    for name in guide_names(setting):
        assert f"/docs/{name}.md" in index
    for name in extra_guides:
        assert mounts[f"/docs/{name}.md"].source == str(
            files("behave.agent").joinpath("prompts", "shared", "guides", name + ".md"))
    shared = {"/docs/development.md", "/docs/fp_lib.md", "/docs/memory.md"}
    assert set(mounts) == (shared | ({"/docs/behavior.md"} if setting == "joint" else set())
                          | {f"/docs/{name}.md" for name in extra_guides})
    other = shell.development_shell(limits, setting="rtl_only" if setting == "joint" else "joint")
    other_mounts = {mount.target: mount for mount in other.template.read_only}
    for target in shared:
        assert mounts[target] == other_mounts[target]
        assert mounts[target].source == other_mounts[target].source


@pytest.mark.parametrize("name", ["missing_guide", "../reference/hls", "/etc/passwd"])
def test_extra_guides_must_be_named_shared_documents(name):
    from behave.isolation import ResourceLimits
    from behave.task_execution.shell import development_shell

    with pytest.raises(ValueError, match="Unknown shared development guide"):
        development_shell(ResourceLimits(), extra_guides=(name,))


def test_shared_text_is_not_duplicated_per_setting():
    root = files("behave.agent").joinpath("prompts")
    for setting in ("joint", "rtl_only"):
        directory = root.joinpath("solver", setting)
        assert {path.name for path in directory.iterdir()} == {"system.md", "environment.md", "submit.md"}
        messages = role_messages("solver", user_template="shared/task/user", system_template=prompt_path(setting, "system"),
                                 **system_variables(setting),
                                 task_id="same", spec_path="spec.md", public_files_section="", environment_docs="")
        assert messages[1]["content"] == render("shared/task/user", task_id="same", spec_path="spec.md",
                                                public_files_section="", environment_docs="")
    assert not root.joinpath("solver/system.md").is_file()
    assert not root.joinpath("solver/environment.md").is_file()
    assert not root.joinpath("tools/submit.md").is_file()


@pytest.mark.isolation
@pytest.mark.eda
def test_rtl_only_worker_runs_shell_and_submits_without_joint_docs(tmp_path, publication):
    from test_solver_process import setup as process_setup

    from behave.task_execution.solver import run_solver_process

    program = r'''
if request.turn == 0:
    assert [t['name'] for t in request.tools] == ['run_command', 'submit']
    assert set(request.tools[1]['parameters']['properties']) == {'rtl'}
    assert '/docs/fp_lib.md' in request.messages[1]['content']
    assert '/docs/development.md' in request.messages[1]['content']
    assert '/docs/memory.md' in request.messages[1]['content']
    assert '/docs/behavior.md' not in request.messages[1]['content']
    calls = (ToolCall('edit', 'run_command', {'command':
        "set -e\n"
        "test -r /docs/fp_lib.md\n"
        "test ! -e /docs/behavior.md\n"
        "test -r /docs/development.md; test ! -w /docs/development.md\n"
        "test -r /docs/memory.md; test ! -w /docs/memory.md\n"
        "test -r /runtime/site/fp_lib.py\n"
        "test -d /runtime/site/memory_driver\n"
        "python -c 'import cocotb, fp_lib; from memory_driver import MemoryDriver'\n"
        "yosys -V\n"
        "verilator --version\n"
        "printf 'module top; endmodule\n' > rtl.sv"}),)
else:
    assert request.messages[-2]['content']['exit_code'] == 0, request.messages[-2]
    calls = (ToolCall('submit', 'submit', {
        'rtl': {'files': ['rtl.sv'], 'entry_file': 'rtl.sv', 'entry_symbol': 'top'}}),)
sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('', calls)))
'''
    job, journal, supervisor = process_setup(tmp_path, publication, program)
    job = replace(job, setting="rtl_only")
    outcome = run_solver_process(job, journal=journal, supervision=supervisor)
    captured = load_submission(journal.objects, outcome.submission)
    assert captured.setting == "rtl_only"
    assert captured.rtl.artifact is not None
    assert captured.behavior.selection is None and captured.testbench.selection is None
