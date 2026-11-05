"""Stage handoffs, candidate-only diagnostics and runtime-selected Bash."""

from dataclasses import replace

import pytest
from syn_shell import fixture_shell, shell_call, written_content
from test_attempts import Scripted, call
from test_improvement_acquisition import configuration, material
from test_improvement_feedback import inputs as feedback_inputs
from test_role_process import job_for
from test_synthesis import setup

from behave.agent.attempt import AttemptSpec
from behave.agent.loop import run_attempt
from behave.agent.run_store import JournalDirectory
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileRef
from behave.core.records import canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.construction import reader_inputs, run_author
from behave.data.drafts import ScopeProposal, scope_closure
from behave.data.role import RoleJob
from behave.data.scope import scope_inputs, scope_review_inputs
from behave.data.source import review_inputs
from behave.improve.acquisition import AcquisitionConfiguration, acquire_materials, load_acquisition_report
from behave.improve.diagnostics import execution_diagnostics
from behave.isolation.model import ResourceLimits


def test_execution_diagnostics_only_exports_solver_visible_results(tmp_path):
    journal = JournalDirectory(tmp_path / "diagnostics").journal("solver")
    private = journal.objects.put(b"PRIVATE_JUDGE_REFERENCE")
    tools = (
        Tool("run_command", "fixture", object_schema({"command": {"type": "string"}}),
             lambda *_: ToolResult({"status": "completed", "exit_code": 1,
                                   "stderr": "candidate.sv: syntax error"}, (private,)), "fixture/1"),
        Tool("finish", "fixture", object_schema({}), lambda *_: ToolResult({"done": True}, terminal=True),
             "fixture/1", terminal=True),
    )
    result = run_attempt(journal=journal, backend=Scripted(call("run_command", {"command": "check candidate.sv"}),
        call("finish", {})), spec=AttemptSpec("diagnostic", "solver", {"model": "fixture"}),
        initial_files=(), tools=tools, initial_messages=())
    journal.seal()
    files = {file.reference.path: file.data for file in execution_diagnostics(journal.objects, result.head)}
    assert set(files) == {"efficiency.json", "tools.json"}
    assert parse_json(files["efficiency.json"])["tool_calls"] == {"run_command": 1, "finish": 1}
    row = parse_json(files["tools.json"])["executions"][0]
    assert row["arguments"]["command"] == "check candidate.sv"
    assert row["result"]["exit_code"] == 1
    for data in files.values():
        assert b"PRIVATE_JUDGE_REFERENCE" not in data
        assert private.sha256.encode() not in data
    assert execution_diagnostics(journal.objects, None) == ()


def test_feedback_cli_is_independent_reproducible_and_exports_only_safe_files(tmp_path, publication, capsys):
    from behave.improve.__main__ import main

    _, store, _, request = feedback_inputs(tmp_path / "input", publication, ("fault", "no_submission", "unavailable"))
    packet, approved, output = tmp_path / "request.json", tmp_path / "train.json", tmp_path / "export"
    packet.write_bytes(request.to_bytes())
    approved.write_bytes(store.read(request.training_manifest))
    argv = ["feedback", "--request", str(packet), "--approved-manifest", str(approved),
            "--source-objects", str(store.directory), "--output", str(output)]
    assert main([*argv, "--dry-run"]) == 0
    assert not output.exists()
    assert main(argv) == 0
    snapshot = WorkspaceSnapshot.from_bytes((output / "workspace.json").read_bytes())
    assert main(argv) == 0
    assert WorkspaceSnapshot.from_bytes((output / "workspace.json").read_bytes()) == snapshot
    contents = b"".join(path.read_bytes() for path in (output / "view").rglob("*") if path.is_file())
    assert b"CANDIDATE_CODE" in contents
    assert b"PRIVATE_BEHAVIOR_MUST_NOT_EXECUTE" not in contents
    assert b"UNSELECTED_PRIVATE_CONTEXT" not in contents
    exported = DirectoryStore(output / "objects")
    assert {path.name for path in exported.directory.iterdir()} == {
        file.content.sha256 for file in snapshot.files} | {BlobRef.of(snapshot.to_bytes()).sha256}
    assert not (output / "training").exists()


def test_construction_brief_reaches_scope_builder_and_review_but_not_b2(tmp_path):
    options, replies, _ = setup(tmp_path / "task", real=True)
    store, plan = options["reader"], options["plan"]
    contract = written_content(replies[1]).encode()
    body = b'{"objective":"ACQUISITION_TARGET: boundary rounding"}'
    brief = store.put(body)
    original = ScopeProposal.from_bytes(store.read(plan.scope))
    proposal = replace(original, contract=FileRef("contract.json", store.put(contract)), brief=brief)
    reference = store.put(proposal.to_bytes())
    assert ScopeProposal.from_bytes(proposal.to_bytes()) == proposal
    assert brief in scope_closure(store, reference)
    guide = store.put(b"Review intent and interface.")
    initial, closure = scope_inputs(store, proposal.source, guide, brief=brief)
    assert brief in closure and any(file.data == body for file in initial)
    assert any(file.data == body for file in scope_review_inputs(store, reference, guide)[1])
    journal = options["run"].journal("author")
    backend = Scripted(shell_call('read', {"path": ".construction/construction_brief.json"}), *replies)
    result = run_author(task_id=plan.task_id, scope=reference, reader=store, journal=journal,
        backend=backend, spec=plan.author, judge_policy=store.read(plan.judge_policy),
        development_tools=(fixture_shell(),))
    assert result.reason == "final_action"
    assert "ACQUISITION_TARGET" in repr(backend.requests[-1].messages)
    draft = BlobRef.from_dict(thaw_json(result.final_action)["draft"])
    loaded, public = reader_inputs(journal.objects, draft)
    assert all(b"ACQUISITION_TARGET" not in file.data for file in public)
    journal.objects.put(store.read(guide))
    _, _, review = review_inputs(journal.objects, draft, guide)
    assert any(file.data == body for file in review)


@pytest.mark.isolation
def test_acquisition_uses_shared_bash_with_readonly_inputs_and_writable_scratch(tmp_path, publication):
    store, original, rows, _ = configuration(tmp_path, publication)
    identifier = rows[0]["id"]
    command = (f"rg --files library; cp library/source/{identifier}.txt scratch.txt; "
               "test -s scratch.txt && test -r library/catalog.jsonl && "
               f"! (printf changed > library/source/{identifier}.txt)")
    role = job_for(original.inputs, [call("run_command", {"command": command}),
                                    call("submit_materials", material(identifier))], guard="""
names = {tool['name'] for tool in request.tools}
assert names == {'run_command', 'submit_materials'}
if request.turn:
    assert any(message.get('role') == 'tool' and message['content'].get('exit_code') == 0
               for message in request.messages)
""")
    role = replace(role, shell_limits=ResourceLimits(wall_seconds=20, processes=8))
    assert RoleJob.from_bytes(role.to_bytes()) == role
    with pytest.raises(ValueError, match="choose one"):
        replace(role, python_limits=ResourceLimits(wall_seconds=20))
    run = JournalDirectory(tmp_path / "acquire")
    result = acquire_materials(configuration=AcquisitionConfiguration(role), reader=store, run=run)
    assert result["status"] == "completed", result
    head, _ = run.seal("acquisition")
    assert load_acquisition_report(run.objects, head) == result
