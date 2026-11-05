"""Versioned flat memory calls, complete byte coverage, and cold migration."""

from dataclasses import replace

import pytest
from test_attempts import Scripted, call
from test_chat_transport import call as wire_call
from test_chat_transport import reply, send, server
from test_improvement_acquisition import configuration
from test_incremental_experience import snapshot
from test_slime_training_plan import inputs  # noqa: F401

from behave.agent.lifetime import AttemptControl
from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.improve.acquisition import (
    AcquisitionConfiguration,
    load_acquired_materials,
    load_acquisition_report,
    material_submission_schema,
    run_acquisition,
)
from behave.improve.experience import ExperiencePolicy, load_experience, memory_job
from behave.improve.experience_merge import (
    adopt_legacy_memory,
    bootstrap_configuration,
    chunk_configuration,
    compact_legacy_memory,
    legacy_chunks,
    verify_pending_chunk,
)
from behave.improve.training_connection import connected_exchange, model_command


def local_config(tmp_path, publication, url="http://127.0.0.1:9/v1/chat/completions"):
    store, role, _, _ = configuration(tmp_path, publication)
    policy = ExperiencePolicy(version=2, memory_bytes=512, delta_bytes=128,
                              legacy_chunk_bytes=256, max_output_tokens=1024, request_seconds=30)
    prior = snapshot(store, {"construction": "Scope historical evidence.\n" * 21,
                             "solver": "Check numerical boundaries.\n" * 15})
    endpoint = LocalChatEndpoint(url, "fixture", "runtime", parameters={"temperature": 1,
        "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "xhigh"}})
    role = replace(role, model=endpoint.command(), inputs=replace(role.inputs,
        experience=prior, experience_mode="delta-v1", experience_policy=policy.as_dict()),
        attempt=replace(role.attempt, model_policy={**endpoint.model_policy(),
                        "checkpoint": BlobRef.of(b"checkpoint-18").as_dict()}))
    return store, AcquisitionConfiguration(role), policy, prior


def test_policy_versions_preserve_old_canonical_configuration():
    old = ExperiencePolicy().as_dict()
    assert "version" not in old and "max_output_tokens" not in old
    assert ExperiencePolicy.from_dict(old).as_dict() == old
    new = ExperiencePolicy(version=2).as_dict()
    assert ExperiencePolicy.from_dict(new).as_dict() == new
    with pytest.raises(ValueError, match="version"):
        ExperiencePolicy.from_dict({**new, "version": 3})


def test_fragments_cover_all_utf8_bytes_even_when_legacy_exceeds_input_budget(tmp_path, publication):
    store, config, policy, _ = local_config(tmp_path, publication)
    value = {"construction": ("中文🙂x" * 60 + "\n\n") * 280, "solver": "tail🙂" * 73}
    prior = snapshot(store, value)
    assert sum(len(t.encode()) for t in value.values()) > policy.input_bytes
    chunks = legacy_chunks(store, prior, policy)
    for name, text in value.items():
        selected = [(row, files) for row, files, _ in chunks if row["view"] == name]
        rebuilt, previous = b"", 0
        for row, files in selected:
            assert row["start"] == previous
            data = next(f.data for f in files if f.reference.path == f"experience/{name}.md")
            assert len(data) == row["end"] - row["start"] <= policy.legacy_chunk_bytes
            data.decode("utf-8")
            rebuilt += data
            previous = row["end"]
        assert rebuilt == text.encode()
    # Validate a normal-sized plan here; the pure partition test above covers
    # the large archive without creating hundreds of isolated fixture jobs.
    prepared = bootstrap_configuration(configuration=config, reader=store, objects=store)
    assert prepared == config


def test_compact_transport_is_isolated_from_analyst_and_training_profiles(tmp_path, publication, monkeypatch):
    store, config, policy, _ = local_config(tmp_path, publication)
    original = config.job.to_bytes()
    compact = memory_job(config.job, policy)
    endpoint = LocalChatEndpoint.from_dict(parse_json(compact.model.argv[-1].encode()))
    assert endpoint.parameters["max_tokens"] == policy.max_output_tokens
    assert endpoint.parameters["chat_template_kwargs"]["enable_thinking"] is False
    assert config.job.to_bytes() == original
    assert memory_job(compact, policy) == compact
    baseline = LocalChatEndpoint.from_dict(parse_json(config.job.model.argv[-1].encode()))
    profile = {"model": baseline.model, "runtime_revision": baseline.runtime_revision,
               "parameters": thaw_json(baseline.parameters)}
    checkpoint = config.job.attempt.model_policy["checkpoint"]
    command = model_command(tmp_path / "connection.json", profile, checkpoint)
    native = memory_job(replace(config.job, model=command, attempt=replace(config.job.attempt,
        model_policy={**thaw_json(config.job.attempt.model_policy), "transport": command.identity})), policy)
    approved = parse_json(native.model.argv[-1].encode())
    assert approved["profile"] == profile
    captured = []
    monkeypatch.setattr("behave.improve.training_connection.exchange", lambda ep, req: captured.append(ep))
    connected_exchange({"checkpoint": checkpoint, "endpoint": baseline.as_dict()}, approved, config.job.attempt)
    assert captured[-1].parameters["max_tokens"] == policy.max_output_tokens
    assert baseline.parameters["chat_template_kwargs"]["enable_thinking"] is True
    changed = replace(baseline, parameters={"temperature": 0})
    with pytest.raises(ValueError, match="changed its approved"):
        connected_exchange({"checkpoint": checkpoint, "endpoint": changed.as_dict()}, approved, config.job.attempt)


def test_flat_submission_rejects_nested_or_string_values_and_replays(tmp_path, publication):
    store, config, policy, prior = local_config(tmp_path, publication)
    chunks = legacy_chunks(store, prior, policy)
    bootstrap_configuration(configuration=config, reader=store, objects=store)
    guidance = store.put(b"Extract only concise scoped lessons.")
    configured = chunk_configuration(config, chunks[0][0], 0, len(chunks), guidance)
    schema = material_submission_schema(consolidation=True, flat=True)
    assert set(schema["properties"]) == {"construction", "solver"}
    notes = {"construction": "Keep claims scoped.", "solver": ""}
    backend = Scripted(call("submit_experience", {"experience": canonical_json(notes).decode()}),
                       call("submit_experience", notes))
    journal = JournalDirectory(tmp_path / "flat").journal("role")
    outcome = run_acquisition(inputs=configured.job.inputs, reader=store, journal=journal,
                              backend=backend, spec=configured.job.attempt)
    assert outcome.reason == "final_action"
    assert "record-fields-invalid" in str(backend.requests[-1].messages)
    head, _ = journal.seal()
    result = load_acquired_materials(configured.job.inputs, journal.objects, head)
    assert sum(len(f.data) for f in load_experience(journal.objects, BlobRef.from_dict(result["experience"]))) < 128


@pytest.mark.isolation
@pytest.mark.parametrize("pause", [False, True])
def test_real_migration_cold_replay_adoption_and_fragment_resume(tmp_path, publication, pause):
    def respond(handler, requests, stopped):
        body = requests[-1][2]
        assert body["max_tokens"] == 1024
        assert body["chat_template_kwargs"]["enable_thinking"] is False
        assert {t["function"]["name"] for t in body["tools"]} == {"submit_experience"}
        data = parse_json(body["messages"][1]["content"].encode())
        notes = ({"construction": "Keep source claims scoped.", "solver": "Check numerical boundaries."}
                 if data["changes"] else
                 {"construction": "Keep source claims scoped." if data["prior"]["construction"] else "",
                  "solver": "Check numerical boundaries." if data["prior"]["solver"] else ""})
        send(handler, reply([wire_call("saved", "submit_experience", notes)]))

    with server(respond) as (url, observed):
        store, config, policy, prior = local_config(tmp_path, publication, url)
        original = load_experience(store, prior)
        study = tmp_path / "study"
        root = study / "acquisitions/update18/anchor"
        run = JournalDirectory(root / "memory_bootstrap")
        kwargs = dict(configuration=config, reader=store, objects=store, run=run)
        if pause:
            def cancelled():
                return len(run.journal("acquisition").load(verify_artifacts=False)) >= 2
            with pytest.raises(RecordError, match="paused"):
                compact_legacy_memory(**kwargs, control=AttemptControl(cancelled=cancelled))
            assert len(observed) == 1
            saved = run.journal("acquisition").load()
            assert saved[-1].kind == "bootstrap_chunk"
            chunks = legacy_chunks(store, prior, policy)
            next_job = chunk_configuration(config, chunks[1][0], 1, len(chunks),
                BlobRef.from_dict(thaw_json(saved[0].payload)["extract_guidance"]))
            assert verify_pending_chunk(run, 1, BlobRef.of(next_job.to_bytes())) == config
            with pytest.raises(RecordError, match="next uncommitted"):
                verify_pending_chunk(run, 0, BlobRef.of(next_job.to_bytes()))
            # A killed owner may leave the next child opened before any model
            # settlement. Fence only that exact child after owner cleanup.
            from behave.improve.memory_recovery import fence_memory
            child = JournalDirectory(run.directory / "chunks/0001")
            reference = child.objects.put(next_job.to_bytes())
            child.journal("acquisition").append([], "acquisition_started",
                {"configuration": reference.as_dict()}, (reference,))
            before = child.journal("acquisition").load()
            owner = tmp_path / "owner"
            owner.mkdir()
            checkpoint = thaw_json(config.job.attempt.model_policy)["checkpoint"]
            for path, value in (
                (owner / "configuration.json", {"self_improvement": {"study": str(study / "study.json")}}),
                (owner / "node_status.json", {"state": "stopped"}),
                (owner / "runtime.json", {"state": "interrupted", "local_directory": "/tmp/memory-fixture", "phase": "initial"}),
                (root / "connection.json", {"control_package": "/tmp/memory-fixture/installed"}),
                (root / "request.json", {"previous": {"experience": prior.as_dict()}, "parent_checkpoint": checkpoint}),
            ):
                path.write_bytes(canonical_json(value))
            with pytest.raises(ValueError, match="cleanup is not confirmed"):
                fence_memory(child.directory, owner)
            learning = JournalDirectory(owner / "artifacts/plan/learning/initial")
            learning.journal("learning").append([], "learning_process_settled", {"cleanup": "complete"})
            fence_memory(child.directory, owner)
        current, head = compact_legacy_memory(**kwargs)
        count = len(legacy_chunks(store, prior, policy))
        assert len(observed) == count + 1
        if pause:
            assert child.journal("acquisition").load() == before
            assert (child.directory / "retries/001").is_dir()
        assert load_experience(store, prior) == original
        assert current != prior
        assert compact_legacy_memory(**kwargs) == (current, head)
        assert len(observed) == count + 1
        cold = DirectoryStore(tmp_path / "cold")
        cold.publish_owned(run.objects, run.seal("acquisition")[1])
        assert load_acquisition_report(cold, head)["chunks"] == count
        adopted = DirectoryStore(tmp_path / "adopted")
        assert adopt_legacy_memory(configuration=config, reader=cold, objects=adopted, head=head) == (current, head)
        assert load_experience(adopted, current) == load_experience(store, current)
        changed = replace(config, job=replace(config.job, attempt=replace(config.job.attempt,
            model_policy={**thaw_json(config.job.attempt.model_policy), "checkpoint": BlobRef.of(b"other").as_dict()})))
        with pytest.raises(RecordError, match="does not match"):
            adopt_legacy_memory(configuration=changed, reader=cold, objects=adopted, head=head)


@pytest.mark.isolation
def test_failed_fragment_never_publishes_a_partial_memory(tmp_path, publication):
    def respond(handler, requests, stopped):
        send(handler, reply([wire_call("bad", "submit_experience", {"construction": 7, "solver": ""})]))
    with server(respond) as (url, observed):
        store, config, policy, prior = local_config(tmp_path, publication, url)
        original = load_experience(store, prior)
        run = JournalDirectory(tmp_path / "memory_bootstrap")
        with pytest.raises(RecordError, match="original and accepted fragments retained"):
            compact_legacy_memory(configuration=config, reader=store, objects=store, run=run)
        assert len(observed) == policy.max_turns
        assert [e.kind for e in run.journal("acquisition").load()] == ["bootstrap_started"]
        assert load_experience(store, prior) == original


@pytest.mark.isolation
def test_all_empty_extractions_cannot_erase_legacy_experience(tmp_path, publication):
    def respond(handler, requests, stopped):
        send(handler, reply([wire_call("empty", "submit_experience", {"construction": "", "solver": ""})]))
    with server(respond) as (url, observed):
        store, config, policy, prior = local_config(tmp_path, publication, url)
        run = JournalDirectory(tmp_path / "memory_bootstrap")
        with pytest.raises(RecordError, match="cannot erase"):
            compact_legacy_memory(configuration=config, reader=store, objects=store, run=run)
        assert len(observed) == len(legacy_chunks(store, prior, policy))
        assert run.journal("acquisition").load()[-1].kind == "bootstrap_merge"
        assert sum(len(f.data) for f in load_experience(store, prior)) > policy.memory_bytes


@pytest.mark.isolation
@pytest.mark.parametrize("fail_merge", [False, True])
def test_v2_round_keeps_analyst_profile_and_cold_pending_handoff(
        tmp_path, publication, inputs, monkeypatch, fail_merge):
    from test_improvement_round import prepared

    from behave.agent.attempt import AttemptSpec
    from behave.data.role import RoleJob
    from behave.improve.acquisition_records import AcquisitionInputs
    from behave.improve.generation_batch import assigned_acquisitions
    from behave.improve.round import run_round
    from behave.improve.round_evidence import load_round
    from behave.improve.runtime_plan import continue_experience

    def respond(handler, requests, stopped):
        body = requests[-1][2]
        names = {tool["function"]["name"] for tool in body["tools"]}
        if "submit_experience" in names:
            assert body["max_tokens"] == 4096
            assert body["chat_template_kwargs"]["enable_thinking"] is False
            assert names == {"submit_experience"}
            send(handler, reply([wire_call("merged", "submit_experience",
                {"construction": 7 if fail_merge else "Prior lesson plus new scoped evidence.", "solver": ""})]))
        else:
            assert "submit_materials" in names
            assert body["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "xhigh"}
            assert body["max_tokens"] > 4096
            send(handler, reply([wire_call("proposal", "submit_materials", {"materials": [],
                "experience_delta": {"construction": "New scoped evidence.", "solver": ""}})]))

    with server(respond) as (url, observed):
        options, _ = prepared(tmp_path, monkeypatch, publication, inputs)
        objects, runtime = options["reader"], options["runtime"]
        prior = snapshot(objects, {"construction": "Prior lesson.", "solver": ""})
        endpoint = LocalChatEndpoint(url, "fixture", "runtime", parameters={"temperature": 1,
            "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "xhigh"}})
        model_policy = {**endpoint.model_policy(), "checkpoint": options["base_checkpoint"].as_dict()}
        role = RoleJob(AcquisitionInputs(options["library"], objects.put(b"unbound feedback"),
            objects.put(b"Select source-grounded tasks."), experience=prior, experience_mode="delta-v1",
            experience_policy=ExperiencePolicy(version=2, request_seconds=30).as_dict()),
            AttemptSpec("v2-analyst", "source_acquirer", model_policy, max_turns=2), endpoint.command(),
            shell_limits=runtime.diagnosis.jobs[0].command_limits)
        options["runtime"] = replace(runtime, model=endpoint.command(), model_policy=model_policy,
            acquisitions=(AcquisitionConfiguration(role), AcquisitionConfiguration(role)), workers=2,
            generation_only=True)
        result = run_round(**options)
        assert result["status"] == "completed"
        assert bool(result["experience_pending_changes"]) == fail_merge
        if fail_merge:
            assert result["experience"] == prior.as_dict()
        else:
            assert result["experience"] != prior.as_dict()
        count = len(observed)
        assert count == 2 + (2 if fail_merge else 1)
        assert run_round(**options) == result
        assert len(observed) == count
        run = options["run"]
        head, closure = run.seal("round")
        cold = DirectoryStore(tmp_path / "round-cold")
        cold.publish_owned(run.objects, closure)
        assert load_round(cold, head) == result
        continued = continue_experience(options["runtime"], result, cold, objects)
        pending = tuple(BlobRef.from_dict(ref) for ref in result["experience_pending_changes"])
        assigned = assigned_acquisitions(continued, 2, 1,
            BlobRef.from_dict(result["experience"]), BlobRef.from_dict(result["construction_history"]),
            objects, objects, pending)
        assert all(c.job.inputs.pending_experience == pending for c in assigned)
        assert all(c.job.model == endpoint.command() for c in assigned)
