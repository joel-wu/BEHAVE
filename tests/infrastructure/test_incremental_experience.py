"""Bounded Analyst deltas and short consolidation, including cold recovery."""

from dataclasses import replace

import pytest
from test_attempts import Scripted, call
from test_improvement_acquisition import configuration, material
from test_role_process import job_for
from test_slime_training_plan import inputs  # noqa: F401

from behave.agent.journal import load_chain
from behave.agent.lifetime import AttemptControl
from behave.agent.model import ModelReply
from behave.agent.prompting import render
from behave.agent.run_store import JournalDirectory
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.role import input_closure
from behave.improve.acquisition import (
    AcquisitionConfiguration,
    acquire_materials,
    acquisition_view,
    load_acquired_materials,
    load_acquisition_report,
    run_acquisition,
)
from behave.improve.experience import (
    ExperiencePolicy,
    delta_batches,
    delta_files,
    experience_files,
    experience_snapshot,
    load_experience,
    merge_payload,
)
from behave.improve.experience_merge import adopt_legacy_memory, batch_configuration, compact_legacy_memory


def snapshot(store, value, *, delta=False, limit=2048):
    files = delta_files(value, limit) if delta else experience_files(value)
    for file in files:
        store.put(file.data)
    return store.put(experience_snapshot(files).to_bytes())


def merging(store, role, notes, *, policy=None, prior=None):
    policy = policy or ExperiencePolicy()
    inputs = replace(role.inputs, experience_mode="merge-v1", experience_policy=policy.as_dict(),
        experience=prior, experience_proposals=tuple(notes), task_ids=(),
        guidance=store.put(render("synthesis/source_acquirer/consolidate").encode()))
    return AcquisitionConfiguration(replace(role, inputs=inputs))


def test_delta_submission_preserves_prior_and_supports_empty_notes(tmp_path, publication):
    store, role, rows, _ = configuration(tmp_path, publication)
    prior = snapshot(store, {"construction": "Previous supported lesson.", "solver": ""})
    inputs = replace(role.inputs, experience_mode="delta-v1", experience_policy=ExperiencePolicy().as_dict(),
                     experience=prior)
    valid = material(rows[0]["id"])
    valid.pop("experience")
    valid["experience_delta"] = {"construction": "", "solver": ""}
    invalid = {**valid, "experience_delta": {"construction": "x" * 2049, "solver": ""}}
    backend = Scripted(call("submit_materials", invalid), call("submit_materials", valid))
    journal = JournalDirectory(tmp_path / "analyst").journal("role")
    result = run_acquisition(inputs=inputs, reader=store, journal=journal, backend=backend, spec=role.attempt)
    assert result.reason == "final_action"
    assert "experience-size-limit" in str(backend.requests[1].messages)
    head, refs = journal.seal()
    exported = DirectoryStore(tmp_path / "cold-analyst")
    exported.publish(journal.objects, (*refs, *input_closure(replace(role, inputs=inputs), store)))
    record = load_acquired_materials(inputs, exported, head)
    assert "experience" not in record and "experience_delta" in record
    assert delta_batches(exported, prior, (BlobRef.from_dict(record["experience_delta"]),), ExperiencePolicy()) == ()
    assert load_experience(exported, prior)[0].data == b"Previous supported lesson."


def test_twenty_deltas_are_batched_without_copying_or_truncating_memory(tmp_path):
    store, policy = DirectoryStore(tmp_path / "objects"), ExperiencePolicy()
    prior = snapshot(store, {"construction": "p" * policy.memory_bytes, "solver": ""})
    notes = tuple(snapshot(store, {"construction": str(i) + "x" * 1900, "solver": ""}, delta=True) for i in range(20))
    batches = delta_batches(store, prior, (*notes, notes[0]), policy)
    assert len(batches) == 2
    assert tuple(ref for batch in batches for ref in batch) == notes
    for batch in batches:
        payload = merge_payload(store, prior, batch, policy)
        assert len(payload) <= policy.input_bytes
        parsed = parse_json(payload)
        assert len(parsed["prior"]["construction"]) == policy.memory_bytes
        assert len(parsed["changes"]) == len(batch)
    with pytest.raises(RecordError, match="fit"):
        delta_batches(store, snapshot(store, {"construction": "p" * (policy.memory_bytes + 1), "solver": ""}), notes, policy)


@pytest.mark.parametrize("invalid_form", ["oversize", "encoded_object"])
def test_compact_call_inline_only_and_two_turn_correction(tmp_path, publication, invalid_form):
    store, role, _, _ = configuration(tmp_path, publication)
    policy = ExperiencePolicy()
    prior = snapshot(store, {"construction": "Prior lesson.", "solver": ""})
    delta = snapshot(store, {"construction": "New evidence: task/t1/update18.", "solver": ""}, delta=True)
    config = batch_configuration(merging(store, role, [delta], prior=prior), prior, (delta,), 0)
    _, facts, files, closure = acquisition_view(config.job.inputs, store)
    assert facts == {"observations": []}
    assert {f.reference.path for f in files} == {"memory_input.json", "acquisition_guidance.md"}
    assert role.inputs.library not in closure and role.inputs.feedback not in closure
    extra = Tool("run_command", "not available", object_schema({}), lambda *_: ToolResult({}), "fixture")
    memory = {"construction": "Prior lesson; new scoped evidence task/t1/update18.", "solver": ""}
    invalid = ({"construction": "x" * 16385, "solver": ""} if invalid_form == "oversize" else
               canonical_json(memory).decode())
    backend = Scripted(call("submit_experience", {"experience": invalid}),
                       call("submit_experience", {"experience": memory}))
    journal = JournalDirectory(tmp_path / "compact").journal("role")
    result = run_acquisition(inputs=config.job.inputs, reader=store, journal=journal, backend=backend,
        spec=config.job.attempt, development_tools=(extra,))
    assert result.reason == "final_action"
    for request in backend.requests:
        assert {t["name"] for t in request.tools} == {"submit_experience"}
        assert "PRIVATE_BEHAVIOR_MUST_NOT_EXECUTE" not in str(request.messages)
    assert "Prior lesson." in str(backend.requests[0].messages)
    assert "New evidence: task/t1/update18." in str(backend.requests[0].messages)
    head, refs = journal.seal()
    exported = DirectoryStore(tmp_path / "cold-compact")
    exported.publish(journal.objects, (*refs, *closure))
    record = load_acquired_materials(config.job.inputs, exported, head)
    assert load_experience(exported, BlobRef.from_dict(record["experience"])) == experience_files(memory)
    settings = parse_json(exported.read(BlobRef.from_dict(thaw_json(load_chain(exported, head)[0].payload)["configuration"])))
    assert settings["spec"]["max_turns"] == 2 and settings["spec"].get("context_reset_message") is None
    assert run_acquisition(inputs=config.job.inputs, reader=exported, journal=journal, backend=Scripted(),
        spec=config.job.attempt, development_tools=(extra,)) == result


@pytest.mark.isolation
@pytest.mark.parametrize("failure", [None, "missing", "empty"])
def test_bootstrap_preserves_legacy_memory_and_replays_without_dispatch(tmp_path, publication, failure):
    store, role, _, _ = configuration(tmp_path, publication)
    old = {"construction": "scoped evidence\n" * 2000, "solver": "keep uncertain claim scoped"}
    prior = snapshot(store, old)
    config = merging(store, role, (), prior=prior)
    replies = ([ModelReply("not submitted"), ModelReply("not submitted")] if failure == "missing" else
        [call("submit_experience", {"experience": {"construction": "", "solver": ""} if failure == "empty" else
            {"construction": "scoped evidence", "solver": old["solver"]}})])
    job = job_for(config.job.inputs, replies)
    checkpoint = BlobRef.of(b"saved-model-at-update18").as_dict()
    config = replace(config, job=replace(job, attempt=replace(job.attempt,
        model_policy={**job.attempt.model_policy, "checkpoint": checkpoint})))
    run = JournalDirectory(tmp_path / "bootstrap")
    kwargs = dict(configuration=config, reader=store, objects=store, run=run)
    if failure:
        with pytest.raises(RecordError, match="old memory retained" if failure == "missing" else "cannot erase"):
            compact_legacy_memory(**kwargs)
    else:
        current, head = compact_legacy_memory(**kwargs)
        events = run.journal("acquisition").load()
        assert current != prior and head is not None
        assert compact_legacy_memory(**kwargs) == (current, head)
        assert run.journal("acquisition").load() == events
        adopted = DirectoryStore(tmp_path / "adopted")
        assert adopt_legacy_memory(configuration=config, reader=store, objects=adopted, head=head) == (current, head)
        assert load_acquisition_report(adopted, head)["materials"]["experience"] == current.as_dict()
        for mismatched in (
                replace(config, job=replace(config.job, inputs=replace(config.job.inputs, experience=current))),
                replace(config, job=replace(config.job, attempt=replace(config.job.attempt,
                    model_policy={**config.job.attempt.model_policy, "checkpoint": BlobRef.of(b"other model").as_dict()})))):
            with pytest.raises(RecordError, match="does not match"):
                adopt_legacy_memory(configuration=mismatched, reader=store, objects=adopted, head=head)
        bounded = replace(config, job=replace(config.job, inputs=replace(config.job.inputs, experience=current)))
        unused = JournalDirectory(tmp_path / "unused")
        assert compact_legacy_memory(configuration=bounded, reader=store, objects=store, run=unused) == (current, None)
        assert unused.journal("acquisition").load() == ()
    assert load_experience(store, prior) == experience_files(old)


@pytest.mark.isolation
def test_bootstrap_interruption_keeps_old_memory_and_resumes_in_fresh_attempt(tmp_path, publication):
    store, role, _, _ = configuration(tmp_path, publication)
    prior = snapshot(store, {"construction": "evidence\n" * 3000, "solver": ""})
    config = merging(store, role, (), prior=prior)
    config = replace(config, job=job_for(config.job.inputs,
        [call("submit_experience", {"experience": {"construction": "evidence", "solver": ""}})]))
    run = JournalDirectory(tmp_path / "bootstrap")
    kwargs = dict(configuration=config, reader=store, objects=store, run=run)
    with pytest.raises(RecordError, match="old memory retained"):
        compact_legacy_memory(**kwargs, control=AttemptControl(cancelled=lambda: True))
    before = run.journal("acquisition").load()
    current, head = compact_legacy_memory(**kwargs)
    assert current != prior
    assert load_acquisition_report(store, head)["status"] == "completed"
    assert run.journal("acquisition").load() == before
    assert (run.directory / "retries/001").is_dir()


@pytest.mark.isolation
@pytest.mark.parametrize("fail_second", [False, True])
def test_merge_commits_batches_and_carries_failure_to_next_round(tmp_path, publication, fail_second):
    store, role, _, _ = configuration(tmp_path, publication)
    policy = ExperiencePolicy(memory_bytes=64, delta_bytes=1024, input_bytes=10624)
    notes = tuple(snapshot(store, {"construction": str(i) + "x" * 900, "solver": ""}, delta=True) for i in range(20))
    config = merging(store, role, notes, policy=policy)
    assert len(delta_batches(store, None, notes, policy)) == 2
    process = job_for(config.job.inputs, [], guard=f'''
from behave.agent.model import ToolCall
data = parse_json(request.messages[1]["content"].encode())
assert {{t["name"] for t in request.tools}} == {{"submit_experience"}}
if data["prior"]["construction"] and {fail_second!r}:
    reply = ModelReply("cannot consolidate this batch")
else:
    reply = ModelReply("", (ToolCall("submit", "submit_experience", {{"experience": {{
        "construction": "second" if data["prior"]["construction"] else "first", "solver": ""}}}}),))
sys.stdout.buffer.write(encode_model_outcome(request, reply))
raise SystemExit(0)
''')
    config = replace(config, job=process)
    run = JournalDirectory(tmp_path / "merge")
    result = acquire_materials(configuration=config, reader=store, run=run)
    assert result["status"] == ("unfinished" if fail_second else "completed"), result
    assert bool(result["pending_experience"]) == fail_second
    memory = BlobRef.from_dict(result["materials"]["experience"])
    assert load_experience(run.objects, memory)[0].data == (b"first" if fail_second else b"second")
    head, refs = run.seal("acquisition")
    exported = DirectoryStore(tmp_path / "cold-merge")
    exported.publish(run.objects, refs)
    assert load_acquisition_report(exported, head) == result
    assert acquire_materials(configuration=config, reader=exported, run=run) == result
    if fail_second:
        pending = tuple(BlobRef.from_dict(ref) for ref in result["pending_experience"])
        recovered = job_for(replace(config.job.inputs, experience=memory, experience_proposals=pending),
            [call("submit_experience", {"experience": {"construction": "second", "solver": ""}})])
        retried = acquire_materials(configuration=replace(config, job=recovered), reader=exported,
                                   run=JournalDirectory(tmp_path / "next-round"))
        assert retried["status"] == "completed" and retried["pending_experience"] == []
        # The archived failure is immutable; recovery starts with the accepted
        # first batch and exactly the remaining deltas, not all twenty again.
        assert tuple(ref for batch in delta_batches(exported, memory, pending, policy) for ref in batch) == pending
        assert len(pending) < len(notes)


def test_bootstrap_hard_stop_fence_matches_saved_memory_and_checkpoint(tmp_path, publication):
    from behave.improve.memory_recovery import fence_memory, memory_child
    store, role, _, _ = configuration(tmp_path, publication)
    prior = snapshot(store, {"construction": "evidence" * 3000, "solver": ""})
    configured = batch_configuration(merging(store, role, (), prior=prior), prior, (), 0)
    checkpoint = BlobRef.of(b"checkpoint18").as_dict()
    configured = replace(configured, job=replace(configured.job, attempt=replace(configured.job.attempt,
        model_policy={**configured.job.attempt.model_policy, "checkpoint": checkpoint})))
    study, owner = tmp_path / "study", tmp_path / "owner"
    acquisition = study / "acquisitions/update_18/anchor"
    child = JournalDirectory(acquisition / "memory_bootstrap")
    reference = child.objects.put(configured.to_bytes())
    child.journal("acquisition").append([], "acquisition_started", {"configuration": reference.as_dict()}, (reference,))
    owner.mkdir()
    for path, value in ((owner / "configuration.json", {"self_improvement": {"study": str(study / "study.json")}}),
        (owner / "node_status.json", {"state": "stopped"}),
        (owner / "runtime.json", {"state": "interrupted", "local_directory": "/tmp/test-owner", "phase": "initial"}),
        (acquisition / "connection.json", {"control_package": "/tmp/test-owner/installed"})):
        path.write_bytes(canonical_json(value))
    learning = JournalDirectory(owner / "artifacts/plan/learning/initial")
    learning.journal("learning").append([], "learning_process_settled", {"cleanup": "complete"})
    request = acquisition / "request.json"
    request.write_bytes(canonical_json({"previous": {"experience": BlobRef.of(b"wrong").as_dict()},
                                       "parent_checkpoint": checkpoint}))
    with pytest.raises(RecordError, match="differs"):
        fence_memory(child.directory, owner)
    request.write_bytes(canonical_json({"previous": {"experience": prior.as_dict()}, "parent_checkpoint": checkpoint}))
    before = child.journal("acquisition").load()
    fence_memory(child.directory, owner)
    assert memory_child(child.directory, configured).directory == child.directory / "retries/001"
    assert child.journal("acquisition").load() == before


@pytest.mark.isolation
def test_operator_cancel_reuses_committed_batch(tmp_path, publication):
    store, role, _, _ = configuration(tmp_path, publication)
    policy = ExperiencePolicy(memory_bytes=64, delta_bytes=1024, input_bytes=10624)
    notes = tuple(snapshot(store, {"construction": str(i) + "x" * 900, "solver": ""}, delta=True) for i in range(20))
    config = merging(store, role, notes, policy=policy)
    process = job_for(config.job.inputs, [call("submit_experience", {"experience": {"construction": "accepted", "solver": ""}})])
    config, run = replace(config, job=process), JournalDirectory(tmp_path / "pause")
    control = AttemptControl(cancelled=lambda: any(e.kind == "memory_batch" for e in run.journal("acquisition").load()))
    partial = acquire_materials(configuration=config, reader=store, run=run, control=control)
    assert partial["status"] == "interrupted"
    saved = run.journal("acquisition").load()
    assert [e.kind for e in saved] == ["memory_started", "memory_batch"]
    result = acquire_materials(configuration=config, reader=store, run=run)
    assert result["status"] == "completed" and result["pending_experience"] == []
    assert run.journal("acquisition").load()[:len(saved)] == saved


@pytest.mark.isolation
def test_abrupt_parent_stop_fences_only_uncommitted_memory_batch(tmp_path, publication):
    """Injected dangling child plus confirmed owner cleanup, not a live-node kill."""
    from behave.agent.replay import Tape
    from behave.improve.memory_recovery import fence_memory, memory_child

    store, role, _, _ = configuration(tmp_path, publication)
    policy = ExperiencePolicy(memory_bytes=64, delta_bytes=1024, input_bytes=10624)
    notes = tuple(snapshot(store, {"construction": str(i) + "x" * 900, "solver": ""}, delta=True) for i in range(20))
    config = merging(store, role, notes, policy=policy)
    process = job_for(config.job.inputs, [call("submit_experience", {"experience": {"construction": "accepted", "solver": ""}})])
    config = replace(config, job=process)
    study, owner = tmp_path / "study", tmp_path / "job"
    acquisition = study / "acquisitions/update_18/anchor"
    run = JournalDirectory(acquisition / "round/batches/batch_000/experience")
    control = AttemptControl(cancelled=lambda: any(e.kind == "memory_batch" for e in run.journal("acquisition").load()))
    partial = acquire_materials(configuration=config, reader=store, run=run, control=control)
    assert partial["status"] == "interrupted"
    saved = run.journal("acquisition").load()
    prior = BlobRef.from_dict(partial["materials"]["experience"])
    batches = delta_batches(store, None, notes, policy)
    expected = batch_configuration(config, prior, batches[1], 1)
    child = JournalDirectory(run.directory / "batches/0001")
    child.objects.publish(run.objects, input_closure(expected.job, run.objects))
    ref = child.objects.put(expected.to_bytes())
    tape = Tape(child.journal("acquisition"))
    tape.record("acquisition_started", {"configuration": ref.as_dict()}, (ref,))
    tape.record("acquisition_requested", {"job": child.objects.put(expected.job.to_bytes()).as_dict()})
    # This durable request has no outcome, modelling the boundary left by an
    # abruptly lost supervisor. It must not be trusted as evidence of cleanup.
    child.journal("revisions", "r0", "source_acquirer_supervision").append([], "dangling_test_request", {})
    owner.mkdir()
    for path, value in ((owner / "configuration.json", {"self_improvement": {"study": str(study / "study.json")}}),
        (owner / "node_status.json", {"state": "stopped"}),
        (owner / "runtime.json", {"state": "interrupted", "local_directory": "/tmp/fixture-native", "phase": "initial"}),
        (acquisition / "request.json", {}), (acquisition / "connection.json", {"control_package": "/tmp/fixture-native/installed"})):
        path.write_bytes(canonical_json(value))
    with pytest.raises(ValueError, match="cleanup is not confirmed"):
        fence_memory(child.directory, owner)
    learning = JournalDirectory(owner / "artifacts/plan/learning/initial")
    learning.journal("learning").append([], "learning_process_settled", {"cleanup": "complete"})
    with pytest.raises(ValueError, match="unfinished memory owner"):
        fence_memory(run.directory, owner)  # Never discard the whole coordinator.
    with pytest.raises(ValueError, match="unfinished memory owner"):
        fence_memory(run.directory / "batches/0000", owner)  # Accepted first batch.
    record = fence_memory(child.directory, owner)
    assert record == fence_memory(child.directory, owner)
    assert memory_child(child.directory, expected).directory == child.directory / "retries/001"
    before = child.journal("acquisition").load()
    result = acquire_materials(configuration=config, reader=store, run=run)
    assert result["status"] == "completed"
    assert child.journal("acquisition").load() == before
    assert run.journal("acquisition").load()[:len(saved)] == saved
    head, refs = run.seal("acquisition")
    cold = DirectoryStore(tmp_path / "cold-fenced")
    cold.publish(run.objects, refs)
    assert load_acquisition_report(cold, head) == result


@pytest.mark.isolation
@pytest.mark.parametrize("fail_merge", [False, True])
def test_incremental_round_cold_audit_and_pending_handoff(tmp_path, monkeypatch, publication, inputs, fail_merge):
    from test_improvement_round import prepared

    from behave.improve.generation_batch import assigned_acquisitions
    from behave.improve.round import run_round
    from behave.improve.round_evidence import load_round
    from behave.improve.runtime_plan import continue_experience

    options, _ = prepared(tmp_path, monkeypatch, publication, inputs)
    objects, runtime = options["reader"], options["runtime"]
    from behave.improve.acquisition_records import AcquisitionInputs
    role_inputs = AcquisitionInputs(options["library"], objects.put(b"unbound feedback"), objects.put(b"Review fixture"),
        experience_mode="delta-v1", experience_policy=ExperiencePolicy().as_dict())
    role = job_for(role_inputs, [], guard=f'''
from behave.agent.model import ToolCall
merging = "submit_experience" in {{t["name"] for t in request.tools}}
if merging and {fail_merge!r}:
    reply = ModelReply("no submission")
elif merging:
    data = parse_json(request.messages[1]["content"].encode())
    assert len(data["changes"]) == 1
    reply = ModelReply("", (ToolCall("submit", "submit_experience", {{"experience": {{"construction": "Merged.", "solver": ""}}}}),))
else:
    reply = ModelReply("", (ToolCall("submit", "submit_materials", {{"materials": [],
        "experience_delta": {{"construction": "New scoped lesson.", "solver": ""}}}}),))
sys.stdout.buffer.write(encode_model_outcome(request, reply))
raise SystemExit(0)
''')
    options["runtime"] = replace(runtime, model=role.model,
        model_policy={**runtime.model_policy, "transport": role.model.identity},
        acquisitions=(AcquisitionConfiguration(role), AcquisitionConfiguration(role)), workers=2, generation_only=True)
    result = run_round(**options)
    assert result["status"] == "completed"
    assert bool(result["experience_pending_changes"]) == fail_merge
    run = options["run"]
    head, refs = run.seal("round")
    exported = DirectoryStore(tmp_path / "cold-round")
    exported.publish(run.objects, refs)
    assert load_round(exported, head) == result
    assert run_round(**options) == result
    continued = continue_experience(options["runtime"], result, exported, objects)
    expected = tuple(BlobRef.from_dict(ref) for ref in result["experience_pending_changes"])
    assert all(c.job.inputs.pending_experience == expected for c in continued.acquisitions)
    assigned = assigned_acquisitions(continued, 2, 1,
        None if result.get("experience") is None else BlobRef.from_dict(result["experience"]),
        BlobRef.from_dict(result["construction_history"]), objects, objects, expected)
    assert all(c.job.inputs.pending_experience == expected for c in assigned)
