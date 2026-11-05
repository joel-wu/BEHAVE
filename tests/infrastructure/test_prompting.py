"""Prompt relocation preserves messages, packaging, and attempt identity."""

import json
from pathlib import Path
from string import Template

import pytest
from test_attempts import Scripted, setup

from behave.agent import prompting
from behave.agent.loop import run_attempt
from behave.agent.model import ModelReply
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, parse_json
from behave.data.published import load_published_task
from behave.task_execution.capture import load_submission, submission_tool
from behave.task_execution.solver import run_solver
from behave.task_execution.solver_settings import development_instructions, prompt_path, render_prompt, system_variables

BASELINE = json.loads((Path(__file__).parent / "fixtures/prompts_before_centralization.json").read_text())
LAYOUT = json.loads((Path(__file__).parent / "fixtures/prompts_before_task_layout.json").read_text())
MOVED = {row["previous"].removesuffix(".md"): name.removesuffix(".md") for name, row in LAYOUT.items()}
ROLE_TEMPLATES = {role: "synthesis/" + role for role in (
    "task_author", "spec_reader", "rtl_author", "scope_author", "scope_reviewer", "source_reviewer")}
ROLE_TEMPLATES.update({role: "synthesis/legacy/" + role for role in ("source_discoverer", "source_selector")})
ROLE_VALUES = {
    role: {key: value for name in (role + "/system", role + "/user")
           for key, value in BASELINE[name]["values"].items()}
    for role in sorted({name.split("/")[0] for name in BASELINE if name.endswith("/system")})
}
ROLE_VALUES["solver"] = {
    "task_id": "fixture_counter", "spec_path": "spec.md",
    "public_files_section": prompting.render("shared/task/public_files", paths="- `include/interface.bin`") + "\n\n",
    "environment_docs": "",
}
ROLE_VALUES["ppa"] = {**ROLE_VALUES["solver"],
    "development": development_instructions("rtl_sim"), "instructions": "fixture PPA objective"}


@pytest.mark.parametrize("role", ROLE_VALUES)
def test_prompt_roles_load_both_messages(role):
    system = ({"system_template": prompt_path("joint", "system"),
               "user_template": "shared/task/user", **system_variables("joint")}
              if role == "solver" else {"user_template": "shared/task/user"} if role == "ppa" else {})
    messages = prompting.role_messages(ROLE_TEMPLATES.get(role, role), **system, **ROLE_VALUES[role])
    assert [m["role"] for m in messages] == ["system", "user"]
    for message in messages:
        name = role + "/" + message["role"]
        if name == "solver/system":
            name = prompt_path("joint", "system")
            values = system_variables("joint")
        elif name == "ppa/system":
            values = {key: ROLE_VALUES["ppa"][key] for key in ("development", "instructions")}
        elif name == "ppa/user":
            name = "solver/user"
            values = {key: ROLE_VALUES["ppa"][key] for key in ROLE_VALUES["solver"]}
        else:
            values = ROLE_VALUES["solver"] if name == "solver/user" else BASELINE[name]["values"]
        assert message["content"] == prompting.render(MOVED.get(name, name), **values)


@pytest.mark.parametrize("name", ["../solver/system", "/solver/system", "solver//system", "solver/system.md", ""])
def test_prompt_paths_do_not_escape_package(name):
    with pytest.raises(ValueError, match="template identifier"):
        prompting.render(name)


def test_prompt_missing_template_has_no_fallback():
    with pytest.raises(FileNotFoundError):
        prompting.render("unknown/system")


def test_prompt_variables_are_strict_and_nonrecursive(monkeypatch):
    system = {"system_template": prompt_path("joint", "system"), **system_variables("joint")}
    with pytest.raises(ValueError, match="missing"):
        prompting.role_messages("solver", user_template="shared/task/user", **system)
    with pytest.raises(ValueError, match="unexpected"):
        prompting.role_messages("solver", user_template="shared/task/user", **system, **ROLE_VALUES["solver"], instructions="unexpected")
    with pytest.raises(TypeError, match="strings"):
        prompting.role_messages("solver", user_template="shared/task/user", **system, **{**ROLE_VALUES["solver"], "task_id": {}})
    text = "${instructions} $price {a,b}\n中文"
    assert prompting.role_messages("ppa", user_template="shared/task/user",
        **{**ROLE_VALUES["ppa"], "development": "shared", "instructions": text})[0]["content"] == "shared\n\n" + text
    monkeypatch.setattr(prompting, "_template", lambda name: Template("Literal $$ and ${value}"))
    assert prompting.render("fixture", value=text) == "Literal $ and " + text


def test_prompt_loader_rejects_malformed_templates(tmp_path, monkeypatch):
    root = tmp_path / "prompts"
    root.mkdir()
    (root / "bad.md").write_text("${unfinished\n")
    monkeypatch.setattr(prompting, "files", lambda package: tmp_path)
    with pytest.raises(ValueError, match="invalid prompt placeholders"):
        prompting.render("bad")


def test_prompt_loader_does_not_cache_text_or_use_cwd(tmp_path, monkeypatch):
    expected = prompting.render("shared/runtime/output_limit")
    root = tmp_path / "prompts" / "shared" / "runtime"
    root.mkdir(parents=True)
    (root / "output_limit.md").write_text("not a package resource\n")
    monkeypatch.chdir(tmp_path)
    assert prompting.render("shared/runtime/output_limit") == expected
    monkeypatch.setattr(prompting, "files", lambda package: tmp_path)
    assert prompting.render("shared/runtime/output_limit") == "not a package resource"
    (root / "output_limit.md").write_text("changed\n\n")
    assert prompting.render("shared/runtime/output_limit") == "changed\n"


@pytest.mark.parametrize("with_public_files", [True, False])
@pytest.mark.parametrize("with_environment_docs", [True, False])
def test_prompt_solver_uses_public_view_and_records_exact_rendered_messages(
        tmp_path, publication, with_public_files, with_environment_docs):
    from dataclasses import replace

    journal, spec = setup(tmp_path)
    published = load_published_task(publication.reader, publication.ref, publication.task.task_id)
    public = published.public
    if not with_public_files:
        public = replace(public, files=tuple(file for file in public.files
                                            if file.reference.path in {public.spec_path, public.contract_path}))
    backend = Scripted(ModelReply("done"))
    environment_docs = prompting.render(prompt_path("joint", "environment")) if with_environment_docs else ""
    run_solver(task=public, spec=replace(spec, role="solver"), backend=backend, journal=journal,
               environment_docs=environment_docs)
    reference = BlobRef.from_dict(dict(journal.load()[0].payload["configuration"]))
    configuration = parse_json(journal.objects.read(reference))
    assert list(backend.requests[0].messages[:-1]) == configuration["messages"]
    assert configuration["runtime_turn_status"] is True
    assert "Runtime status: turn 1 of" in backend.requests[0].messages[-1]["content"]
    message = configuration["messages"][1]["content"]
    assert message == prompting.render(
        "shared/task/user", task_id=public.task_id, spec_path=public.spec_path,
        public_files_section=ROLE_VALUES["solver"]["public_files_section"] if with_public_files else "",
        environment_docs=environment_docs)
    assert ("Additional public files in the workspace:" in message) == with_public_files
    assert ("- `include/interface.bin`" in message) == with_public_files
    assert "[]" not in message
    assert f"Read the task specification: `{public.spec_path}`." in message
    assert ("/docs/development.md" in message) == with_environment_docs
    assert public.release.sha256 not in message and public.revision.sha256 not in message
    workspace = Workspace(journal.objects)
    workspace.restore(BlobRef.from_dict(configuration["workspace"]))
    for file in public.files:
        if file.reference.path != public.contract_path:
            assert workspace.read(file.reference.path) == file.data
    assert public.contract_path not in {file.path for file in workspace.snapshot().files}
    with pytest.raises(RecordError, match="no workspace file"):
        workspace.read(public.contract_path)
    with workspace.materialize() as root:
        assert not (root / public.contract_path).exists()
        assert (root / public.spec_path).is_file()
    # Sim still receives the original contract through its independent view.
    contract = next(file for file in public.files if file.reference.path == public.contract_path)
    assert published.judge.contract == contract
    assert public.contract_path not in repr(configuration["messages"])
    assert "PRIVATE" not in repr(configuration["messages"])
    assert configuration["messages"][0]["content"] == render_prompt("joint", "system")
    submit = next(tool for tool in backend.requests[0].tools if tool["name"] == "submit")
    assert submit["description"] == prompting.render(prompt_path("joint", "submit"))
    assert set(submit["parameters"]["properties"]) == {"rtl", "behavior", "testbench"}
    assert set(submit["parameters"]["required"]) == {"rtl", "behavior", "testbench"}


def test_submit_description_example_captures_all_three_artifacts(tmp_path, publication):
    description = prompting.render(prompt_path("joint", "submit"))
    example = description.split("```json\n", 1)[1].split("\n```", 1)[0]
    arguments = json.loads(example)
    assert set(arguments) == {"rtl", "behavior", "testbench"}
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    journal, _ = setup(tmp_path)
    workspace = Workspace(journal.objects)
    for selection in arguments.values():
        for path in selection["files"]:
            workspace.write(path, f"example contents of {path}\n".encode())
    result = submission_tool(public).handler(workspace, arguments)
    assert result.terminal
    ref = BlobRef.from_dict(dict(result.content["submission"]))
    assert parse_json(journal.objects.read(ref))["schema"] == "behave.frozen-submission/1"
    captured = load_submission(journal.objects, ref)
    for name, selection in arguments.items():
        component = getattr(captured, name)
        assert component.artifact is not None
        assert {file.path for file in component.files} == set(selection["files"])
        assert component.selection.entry_file == selection["entry_file"]
        assert component.selection.entry_symbol == selection["entry_symbol"]


def test_prompt_edits_cannot_silently_resume_old_attempt(tmp_path):
    journal, spec = setup(tmp_path)
    messages = prompting.role_messages("solver", user_template="shared/task/user", system_template=prompt_path("joint", "system"),
                                      **system_variables("joint"), **ROLE_VALUES["solver"])
    first = run_attempt(journal=journal, spec=spec, backend=Scripted(ModelReply("done")), initial_messages=messages)
    no_calls = Scripted()
    assert run_attempt(journal=journal, spec=spec, backend=no_calls, initial_messages=messages) == first
    changed = ({**messages[0], "content": messages[0]["content"] + " updated"}, messages[1])
    with pytest.raises(RecordError, match="replay differs"):
        run_attempt(journal=journal, spec=spec, backend=no_calls, initial_messages=changed)
    assert no_calls.requests == []


def test_tool_declarations_read_edited_central_description(tmp_path, monkeypatch):
    from behave.agent.tools import file_tools

    root = tmp_path / "prompts" / "shared" / "tools" / "files"
    root.mkdir(parents=True)
    for name in ("read_file", "write_file", "delete_file", "list_files"):
        (root / (name + ".md")).write_text(name + " description\n")
    monkeypatch.setattr(prompting, "files", lambda package: tmp_path)
    assert file_tools()[0].declaration()["description"] == "read_file description"
    (root / "read_file.md").write_text("Edited read instruction\n")
    assert file_tools()[0].declaration()["description"] == "Edited read instruction"
