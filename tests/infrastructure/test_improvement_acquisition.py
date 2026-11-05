"""Model-free diagnosis -> library lookup -> ordinary synthesis source handoff."""

from dataclasses import replace

import pytest
from syn_shell import fixture_shell, shell_call
from test_attempts import Scripted, call
from test_improvement_feedback import inputs as diagnostic_inputs
from test_improvement_library import prepared

from behave.agent.attempt import AttemptSpec
from behave.agent.journal import Event, load_chain
from behave.agent.model import ModelReply
from behave.agent.run_store import JournalDirectory
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.role import RoleJob, input_closure
from behave.data.selection_records import SourceInventory
from behave.improve.acquisition import load_acquired_materials, run_acquisition
from behave.improve.acquisition_records import AcquisitionInputs
from behave.improve.experience import experience_files, load_experience
from behave.improve.feedback import export_feedback
from behave.improve.library import import_library


def configuration(tmp_path, publication):
    job, store, _, request = diagnostic_inputs(tmp_path / "diagnosis", publication, ("fault",))
    root, library_store, catalog, rows = prepared(tmp_path / "library")
    library = import_library(root, catalog, library_store)
    for path in library_store.directory.iterdir():
        store.put(path.read_bytes())
    bundle = export_feedback(request, store, approved_manifest=request.training_manifest)
    for file in bundle.files:
        store.put(file.data)
    feedback = store.put(WorkspaceSnapshot(tuple(file.reference for file in bundle.files)).to_bytes())
    guidance = store.put(b"Acquire source material for reusable capabilities, not benchmark answers.")
    inputs = AcquisitionInputs(library, feedback, guidance)
    spec = AttemptSpec("acquisition-r0", inputs.role, job.attempt.model_policy)
    role = RoleJob(inputs, spec, job.model, shell_limits=job.command_limits)
    return store, role, rows, bundle.facts["observations"][0]["id"]


def material(identifier, *, observation=None):
    return {"materials": [{"id": identifier, "rationale": "Relevant saturation source.",
                          "objective": "Preserve signed saturation and rounding order.",
                          "observation_ids": [] if observation is None else [observation]}],
            "experience": {"construction": "Retain explicit signed saturation semantics.",
                           "solver": "Check signed boundary values and rounding order."}}


def test_prior_construction_history_is_mounted_and_survives_transfer(tmp_path, publication):
    from behave.improve.acquisition import acquisition_view
    from behave.improve.construction import HISTORY_PATH

    store, role, _, _ = configuration(tmp_path, publication)
    data = canonical_json({"schema": "behave.construction-history/1", "rounds": [
        {"round_id": "previous", "tasks": [{"task_id": "old-task", "findings": ["Fix invalid return shape"]}]}]})
    history = store.put(data)
    role = replace(role, inputs=replace(role.inputs, construction_history=history))
    assert RoleJob.from_bytes(role.to_bytes()) == role
    closure = input_closure(role, store)
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(store, closure)
    _, _, files, _ = acquisition_view(role.inputs, exported)
    assert next(file.data for file in files if file.reference.path == HISTORY_PATH) == data
    backend = Scripted(shell_call("read", {"path": HISTORY_PATH}),
        shell_call("write", {"path": HISTORY_PATH, "content": "overwrite"}),
        call("submit_materials", {"materials": [], "experience": {"construction": "Use explicit returns", "solver": ""}}))
    journal = JournalDirectory(tmp_path / "attempt").journal("role")
    run_acquisition(inputs=role.inputs, reader=exported, journal=journal, backend=backend,
                    spec=role.attempt, development_tools=(fixture_shell(),))
    assert "Fix invalid return shape" in repr(backend.requests[1].messages)
    assert "read-only" in repr(backend.requests[2].messages) or "readonly" in repr(backend.requests[2].messages)
    assert exported.read(history) == data


@pytest.mark.parametrize("with_command", [True, False])
def test_runtime_tools_and_source_handoff_with_cold_replay(tmp_path, publication, with_command):
    store, role, rows, _ = configuration(tmp_path, publication)
    assert RoleJob.from_bytes(role.to_bytes()) == role
    closure = input_closure(role, store)
    assert role.inputs.library in closure and role.inputs.feedback in closure
    journal = JournalDirectory(tmp_path / "acquisition").journal("role")
    # This unit fixture checks tool injection; the isolation lane executes real Bash.
    tools = (Tool("run_command", "Runtime command fixture", object_schema({"command": {"type": "string"}}),
                  lambda *_: ToolResult({"stdout": "runtime-command-result"}), "fixture/1"),) if with_command else ()
    commands = [call("run_command", {"command": "pwd"})] if with_command else []
    backend = Scripted(*commands,
        call("submit_materials", material(rows[0]["id"])))
    result = run_acquisition(inputs=role.inputs, reader=store, journal=journal, backend=backend,
                             spec=role.attempt, development_tools=tools)
    expected_tools = {"submit_materials", *(tool.name for tool in tools)}
    assert all({tool["name"] for tool in request.tools} == expected_tools for request in backend.requests)
    if with_command:
        assert "runtime-command-result" in str(backend.requests[-1].messages)
    assert result.reason == "final_action"
    journal.seal()
    materials = load_acquired_materials(role.inputs, journal.objects, result.head)
    assert materials["materials"] == material(rows[0]["id"])["materials"]
    inventory = SourceInventory.from_bytes(journal.objects.read(BlobRef.from_dict(materials["inventory"])))
    assert len(inventory.sources) == 1
    # No original workspace or upstream checkout is needed for completed replay.
    exported = DirectoryStore(tmp_path / "export")
    head, references = journal.seal()
    exported.publish(journal.objects, tuple({*references, *closure}))
    assert load_acquired_materials(role.inputs, exported, head) == materials
    assert run_acquisition(inputs=role.inputs, reader=exported, journal=journal,
        backend=Scripted(), spec=role.attempt, development_tools=tools) == result
    with pytest.raises(RecordError):
        run_acquisition(inputs=replace(role.inputs, guidance=store.put(b"different guidance")), reader=store, journal=journal,
            backend=Scripted(), spec=role.attempt, development_tools=tools)


def test_batch_proposals_preserve_supporting_sources_after_transfer(tmp_path, publication):
    store, role, rows, _ = configuration(tmp_path, publication)
    arguments = material(rows[0]["id"])
    first = {**arguments["materials"][0], "proposal_id": "first",
             "supporting_source_ids": [rows[1]["id"]]}
    second = {**first, "proposal_id": "second", "objective": "Retain the function with a streaming interface.",
              "supporting_source_ids": []}
    arguments["materials"] = [first, second]
    journal = JournalDirectory(tmp_path / "batch").journal("role")
    result = run_acquisition(inputs=role.inputs, reader=store, journal=journal,
        backend=Scripted(call("submit_materials", arguments)), spec=role.attempt)
    assert result.reason == "final_action"
    head, closure = journal.seal()
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(journal.objects, tuple({*closure, *input_closure(role, store)}))
    record = load_acquired_materials(role.inputs, exported, head)
    assert record["materials"] == arguments["materials"]
    inventory = SourceInventory.from_bytes(exported.read(BlobRef.from_dict(record["inventory"])))
    assert len(inventory.sources) == 2  # Source union, not an extra proposal.


@pytest.mark.parametrize("change", ["unknown_observation", "unknown_source", "unknown_supporting_source", "empty_objective", "duplicate_source"])
def test_material_handoff_requires_valid_identity_and_objective(tmp_path, publication, change):
    store, role, rows, observation = configuration(tmp_path, publication)
    selected = material(rows[0]["id"], observation=observation)
    if change == "unknown_observation":
        selected["materials"][0]["observation_ids"] = ["unknown"]
    elif change == "unknown_source":
        selected["materials"][0]["id"] = "unknown"
    elif change == "unknown_supporting_source":
        selected["materials"][0]["supporting_source_ids"] = ["unknown"]
    elif change == "empty_objective":
        selected["materials"][0]["objective"] = ""
    else:
        selected["materials"] *= 2
    backend = Scripted(call("submit_materials", selected), ModelReply("stop"))
    journal = JournalDirectory(tmp_path / "bad").journal("role")
    result = run_acquisition(inputs=role.inputs, reader=store, journal=journal, backend=backend, spec=role.attempt)
    assert result.reason == "stopped"
    assert "error" in str(backend.requests[-1].messages)
    journal.seal()
    assert load_acquired_materials(role.inputs, journal.objects, result.head) is None


def test_acquisition_does_not_gain_unconfigured_execution_or_hidden_material(tmp_path, publication):
    store, role, _, _ = configuration(tmp_path, publication)
    backend = Scripted(call("run_command", {"command": "cat /etc/passwd"}),
        shell_call('read', {"path": "../hidden_behavior.py"}),
        shell_call('read', {"path": "behavior.py"}),
        call("submit_materials", {"materials": [], "experience": {"construction": "", "solver": ""}}))
    journal = JournalDirectory(tmp_path / "readonly").journal("role")
    result = run_acquisition(inputs=role.inputs, reader=store, journal=journal, backend=backend, spec=role.attempt)
    assert result.reason == "final_action"
    journal.seal()
    for request in backend.requests:
        names = {tool["name"] for tool in request.tools}
        assert names == {"submit_materials"}
        assert "PRIVATE_BEHAVIOR_MUST_NOT_EXECUTE" not in str(request.messages)
    assert load_acquired_materials(role.inputs, journal.objects, result.head)["materials"] == []


@pytest.mark.parametrize("change,field,code,hint", [
    ("id", "materials[1].id", "acquisition-source-invalid", "library/catalog.jsonl"),
    ("supporting_source_ids", "materials[1].supporting_source_ids[0]", "acquisition-source-invalid", "library/catalog.jsonl"),
    ("observation_ids", "materials[1].observation_ids[0]", "acquisition-observation-invalid", "feedback.json"),
    ("proposal_id", "materials[1].proposal_id", "acquisition-proposal-duplicate", "distinct proposal_id"),
])
def test_submission_error_identifies_field_and_allows_whole_batch_correction(
        tmp_path, publication, change, field, code, hint):
    store, role, rows, observation = configuration(tmp_path, publication)
    valid = material(rows[0]["id"], observation=observation)
    first = {**valid["materials"][0], "proposal_id": "first", "supporting_source_ids": []}
    second = {**first, "proposal_id": "second"}
    corrected = {**valid, "materials": [first, second]}
    bad_value = "first" if change == "proposal_id" else "not-in-the-catalog"
    invalid = {**corrected, "materials": [first, {**second,
        change: [bad_value] if change.endswith("_ids") else bad_value}]}
    journal = JournalDirectory(tmp_path / "correction").journal("role")
    backend = Scripted(call("submit_materials", invalid), call("submit_materials", corrected))
    result = run_acquisition(inputs=role.inputs, reader=store, journal=journal,
                             backend=backend, spec=role.attempt)
    feedback = next(m["content"] for m in reversed(backend.requests[1].messages) if m["role"] == "tool")
    assert feedback["error"] == code
    assert all(value in feedback["message"] for value in (field, bad_value, hint))
    outcomes = [parse_json(journal.objects.read(BlobRef.from_dict(thaw_json(e.payload["result"]))))
                for e in journal.load() if e.kind == "tool_settled"]
    assert "materials" not in outcomes[0]  # No partial acceptance of the valid first item.
    assert result.reason == "final_action"
    journal.seal()
    assert load_acquired_materials(role.inputs, journal.objects, result.head)["materials"] == [first, second]


def test_submission_shape_error_preserves_existing_explanation(tmp_path, publication):
    store, role, _, _ = configuration(tmp_path, publication)
    invalid = {"materials": [], "experience": {"construction": "", "speller": ""}}
    backend = Scripted(call("submit_materials", invalid), ModelReply("stop"))
    journal = JournalDirectory(tmp_path / "bad-experience").journal("role")
    run_acquisition(inputs=role.inputs, reader=store, journal=journal, backend=backend, spec=role.attempt)
    feedback = next(m["content"] for m in reversed(backend.requests[1].messages) if m["role"] == "tool")
    assert feedback["error"] == "record-fields-invalid"
    assert "Missing: ['solver']" in feedback["message"]
    assert "Unexpected: ['speller']" in feedback["message"]


def test_acquisition_stage_cli_preflight_has_no_execution_effect(tmp_path, publication, monkeypatch, capsys):
    from behave.data import __main__ as command
    from behave.improve.acquisition import AcquisitionConfiguration

    store, role, _, _ = configuration(tmp_path, publication)
    config = AcquisitionConfiguration(role)
    assert AcquisitionConfiguration.from_bytes(config.to_bytes()) == config
    path = tmp_path / "acquisition.json"
    path.write_bytes(config.to_bytes())
    monkeypatch.setattr(JournalDirectory, "__init__", lambda *a, **kw: pytest.fail("dry-run requested a writer"))
    run = tmp_path / "not_started"
    assert command.main(["--stage", "acquisition", "--configuration", str(path),
        "--source-objects", str(store.directory), "--run-directory", str(run), "--dry-run"]) == 0
    assert parse_json(capsys.readouterr().out.encode())["status"] == "validated"
    assert not run.exists()


def test_experience_survives_export_and_is_read_only_in_next_attempt(tmp_path, publication):
    store, role, rows, _ = configuration(tmp_path, publication)
    submission = material(rows[0]["id"])
    first = JournalDirectory(tmp_path / "first").journal("role")
    result = run_acquisition(inputs=role.inputs, reader=store, journal=first,
        backend=Scripted(call("submit_materials", submission)), spec=role.attempt)
    assert result.reason == "final_action"
    head, refs = first.seal()
    exported = DirectoryStore(tmp_path / "exported")
    exported.publish(first.objects, (*refs, *input_closure(role, store)))
    record = load_acquired_materials(role.inputs, exported, head)
    memory = BlobRef.from_dict(record["experience"])
    assert load_experience(exported, memory) == experience_files(submission["experience"])
    next_inputs = replace(role.inputs, experience=memory)
    assert AcquisitionInputs.from_dict(next_inputs.as_dict()) == next_inputs
    assert memory in input_closure(replace(role, inputs=next_inputs), exported)
    from behave.improve.acquisition import acquisition_view

    _, _, files, _ = acquisition_view(next_inputs, exported)
    assert {file.reference.path for file in files} >= {
        "experience/construction.md", "experience/solver.md"}
    revised = {"materials": [], "experience": {"construction": "Updated source-selection lesson.",
                                              "solver": submission["experience"]["solver"]}}
    second = JournalDirectory(tmp_path / "second").journal("role")
    backend = Scripted(call("submit_materials", revised))
    next_spec = replace(role.attempt, attempt_id="acquisition-r1")
    next_result = run_acquisition(inputs=next_inputs, reader=exported, journal=second,
        backend=backend, spec=next_spec)
    next_head, _ = second.seal()
    config_event = thaw_json(load_chain(second.objects, next_head)[0].payload)
    settings = parse_json(second.objects.read(BlobRef.from_dict(config_event["configuration"])))
    assert {"experience/construction.md", "experience/solver.md"} <= set(settings["spec"]["read_only_paths"])
    updated = load_acquired_materials(next_inputs, second.objects, next_result.head)
    assert updated["materials"] == []
    assert updated["experience"] != record["experience"]
    assert load_experience(exported, memory) == experience_files(submission["experience"])
    assert run_acquisition(inputs=next_inputs, reader=exported, journal=second,
        backend=Scripted(), spec=next_spec) == next_result


def test_experience_requires_text_views_without_a_size_or_line_limit():
    content = "A supported lesson.\n" * 2000
    files = experience_files({"construction": content, "solver": ""})
    assert files[0].data.decode() == content and files[1].data == b""
    with pytest.raises(TypeError, match="text"):
        experience_files({"construction": [], "solver": ""})
    with pytest.raises(RecordError):
        experience_files({"construction": "missing solver"})


def test_old_material_only_receipts_remain_readable(tmp_path, publication):
    # Construct an archived v2 receipt using the existing journal format.
    from behave.improve import acquisition

    store, role, rows, _ = configuration(tmp_path, publication)
    journal = JournalDirectory(tmp_path / "old").journal("role")
    result = run_acquisition(inputs=role.inputs, reader=store, journal=journal, spec=role.attempt,
        backend=Scripted(call("submit_materials", material(rows[0]["id"]))))
    journal.seal()
    index, _, _, _ = acquisition.acquisition_view(role.inputs, journal.objects)
    legacy = acquisition._record(index, material(rows[0]["id"])["materials"])
    journal.objects.put(canonical_json(legacy))
    receipt = acquisition._receipt(legacy)
    parent = None
    for event in load_chain(journal.objects, result.head):
        payload = thaw_json(event.payload)
        if event.kind == "tool_requested" and payload["call"]["name"] == "submit_materials":
            payload["call"]["arguments"].pop("experience")
        elif event.kind == "tool_settled":
            payload["result"] = journal.objects.put(canonical_json(receipt)).as_dict()
        elif event.kind == "terminal":
            payload["final_action"] = receipt
        parent = journal.objects.put(Event(event.sequence, parent, event.kind, payload, event.artifacts).to_bytes())
    assert load_acquired_materials(role.inputs, journal.objects, parent) == legacy


@pytest.mark.parametrize("with_feedback", [False, True])
def test_parallel_experience_proposals_are_distinct_read_only_inputs(tmp_path, publication, with_feedback):
    from behave.improve.acquisition import acquisition_view
    from behave.improve.experience import experience_snapshot

    store, role, _, _ = configuration(tmp_path, publication)
    proposals = []
    for text in ("First worker lesson.", "Second worker lesson."):
        files = experience_files({"construction": text, "solver": text})
        for file in files:
            store.put(file.data)
        proposals.append(store.put(experience_snapshot(files).to_bytes()))
    snapshot = WorkspaceSnapshot.from_bytes(store.read(role.inputs.feedback))
    original = parse_json(store.read(snapshot.file("feedback.json").content))
    task_ids = tuple(group["task_id"] for group in original["tasks"] if group["complete"])
    inputs = replace(role.inputs, task_ids=task_ids if with_feedback else (), experience_proposals=tuple(proposals))
    assert AcquisitionInputs.from_dict(inputs.as_dict()) == inputs
    _, facts, files, closure = acquisition_view(inputs, store)
    assert bool(facts["observations"]) == with_feedback
    assert bool(facts["tasks"]) == with_feedback
    paths = {file.reference.path: file.data for file in files}
    assert any(path.startswith("observations/") for path in paths) == with_feedback
    assert all(b"PRIVATE_BEHAVIOR_MUST_NOT_EXECUTE" not in data for data in paths.values())
    assert paths["experience/proposals/0/solver.md"] == b"First worker lesson."
    assert paths["experience/proposals/1/solver.md"] == b"Second worker lesson."
    assert set(proposals) <= set(closure)
    journal = JournalDirectory(tmp_path / "consolidate").journal("role")
    reply = {"materials": [], "experience": {"construction": "Merged construction.", "solver": "Merged solver."}}
    checks = []
    if with_feedback:
        evidence = next(path for row in facts["observations"] for path in row["attachments"]
                        if b"CANDIDATE_CODE = 123" in paths[path])
        checks = [shell_call("read", {"path": evidence}, "inspect-evidence"),
                  shell_call("write", {"path": evidence, "content": "overwrite"}, "cannot-rewrite-evidence")]
    backend = Scripted(*checks, call("submit_materials", reply))
    tools = (fixture_shell(),) if with_feedback else ()
    result = run_acquisition(inputs=inputs, reader=store, journal=journal, spec=role.attempt,
        backend=backend, development_tools=tools)
    assert "CANDIDATE_CODE = 123" not in str(backend.requests[0].messages)
    if with_feedback:
        assert "CANDIDATE_CODE = 123" in str(backend.requests[1].messages)
        assert "read-only" in str(backend.requests[-1].messages) or "readonly" in str(backend.requests[-1].messages)
    head, refs = journal.seal()
    exported = DirectoryStore(tmp_path / "cold-memory")
    exported.publish(journal.objects, (*refs, *closure))
    record = load_acquired_materials(inputs, exported, head)
    assert load_experience(exported, BlobRef.from_dict(record["experience"])) == experience_files(reply["experience"])
    assert acquisition_view(inputs, exported)[1] == facts
    assert run_acquisition(inputs=inputs, reader=exported, journal=journal, spec=role.attempt,
        backend=Scripted(), development_tools=tools) == result
