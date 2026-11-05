"""Real role supervision with scripted model transport; no GPU or model service."""

from dataclasses import replace

import pytest
from test_attempts import call
from test_improvement_acquisition import configuration, material
from test_improvement_library import prepared
from test_role_process import Unavailable, job_for

from behave.agent.admission import AdmissionPolicy
from behave.agent.attempt import AttemptInterrupted
from behave.agent.model import ModelReply
from behave.agent.run_store import JournalDirectory
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileRef
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.improve.acquisition import AcquisitionConfiguration, acquire_materials, load_acquisition_report
from behave.improve.library import LibraryIndex, SourceLibrary, import_library
from behave.improve.materials import (
    FrozenMaterials,
    MaterialRequest,
    construction_briefs,
    freeze_materials,
    load_materials,
    proposal_briefs,
    raw_documents,
    source_briefs,
    supplement_materials,
)
from behave.isolation.model import ResourceLimits


def test_committed_brief_formatting_preserves_parallel_proposals():
    library = BlobRef.of(b"approved library")
    first = {"id": "same-source", "objective": "first task", "rationale": "reset handling",
             "observation_ids": ["observation-1"]}
    second = {**first, "objective": "second task", "proposal_id": "explicit-proposal"}
    third = {**first, "id": "other-source", "objective": "third task"}
    reports = ({"materials": {"materials": [first, second]}},
               {"materials": {"materials": [third]}})
    proposals = proposal_briefs(library, iter(reports))
    assert tuple(proposals) == ("a0-p0", "a0-p1", "a1-p0")
    for key, item in zip(proposals, (first, second, third), strict=True):
        assert parse_json(proposals[key]) == {
            "schema": "behave.construction-brief/2", "library": library.as_dict(),
            "source_id": item["id"], "proposal_id": item.get("proposal_id", key),
            **{name: item[name] for name in ("objective", "rationale", "observation_ids")}}
    sources = source_briefs(library, iter(reports))
    assert tuple(sources) == ("same-source", "other-source")
    assert [item["objective"] for item in parse_json(sources["same-source"])["objectives"]] == [
        "first task", "second task"]


def material_request(store, role, heads):
    files = WorkspaceSnapshot.from_bytes(store.read(role.inputs.feedback))
    facts = parse_json(store.read(files.file("feedback.json").content))
    return MaterialRequest(facts["round_id"], BlobRef.from_dict(facts["checkpoint"]), role.inputs.library,
                           role.inputs.feedback, role.model.identity, tuple(heads))


@pytest.mark.parametrize("change", [None, "head", "unfinished", "model", "feedback"])
def test_freeze_reuses_settled_reports_without_reading_role_payloads(
        tmp_path, publication, monkeypatch, change):
    from behave.agent.journal import AttemptJournal

    store, role, rows, _ = configuration(tmp_path, publication)
    config = store.put(AcquisitionConfiguration(role).to_bytes())
    # The coordinator already owns this settled stage. Its large role payloads
    # must not be touched again when selecting the source union.
    opaque_payload = store.put(b"model and tool payload already settled")
    journal = AttemptJournal(tmp_path / "acquisition", store)
    journal.append((), "acquisition_started", {"configuration": config.as_dict()}, (config, opaque_payload))
    head = journal.seal()[0]
    request = material_request(store, role, (head,))
    report = {"head": head.as_dict(), "status": "completed", "materials": material(rows[0]["id"])}
    expected = LibraryIndex(store, request.library).units[rows[0]["id"]].content

    def no_replay(*args):
        pytest.fail("live material handoff replayed settled Analyst evidence")

    monkeypatch.setattr("behave.improve.materials.load_acquisition_report", no_replay)
    monkeypatch.setattr("behave.improve.materials.input_closure", no_replay)
    original_read = store.read

    def read(reference):
        assert reference != opaque_payload, "live handoff reread a model/tool payload"
        return original_read(reference)

    monkeypatch.setattr(store, "read", read)
    if change == "head":
        report["head"] = BlobRef.of(b"another stage").as_dict()
    elif change == "unfinished":
        report["status"] = "unfinished"
    elif change == "model":
        request = replace(request, model_transport="another model")
    elif change == "feedback":
        feedback = WorkspaceSnapshot.from_bytes(store.read(role.inputs.feedback))
        alternative = store.put(replace(feedback, files=(*feedback.files,
            FileRef("extra.txt", store.put(b"different context")))).to_bytes())
        request = replace(request, feedback=alternative)
    if change is None:
        assert freeze_materials(request, store, acquisition_reports=(report,)).documents == (expected,)
    else:
        with pytest.raises(RecordError):
            freeze_materials(request, store, acquisition_reports=(report,))


def test_proposal_briefs_keep_supporting_sources_without_extra_tasks():
    library = BlobRef.of(b"approved library")
    item = {"id": "main", "proposal_id": "first", "supporting_source_ids": ["helper"],
            "objective": "First function", "rationale": "Exploration", "observation_ids": []}
    second = {**item, "proposal_id": "second", "objective": "Second function", "supporting_source_ids": []}
    reports = [{"materials": {"materials": [item, second]}}]
    proposals = proposal_briefs(library, reports)
    assert len(proposals) == 2
    assert parse_json(proposals["a0-p0"])["supporting_source_ids"] == ["helper"]
    assert parse_json(proposals["a0-p1"])["supporting_source_ids"] == []
    legacy = source_briefs(library, reports)
    assert tuple(legacy) == ("main",)
    assert parse_json(legacy["main"])["supporting_source_ids"] == ["helper"]


def supplemental_library(tmp_path, objects, bodies):
    root, _, _, templates = prepared(tmp_path)
    rows = []
    for body in bodies:
        content = BlobRef.of(body)
        path = f"source/{content.sha256}.txt"
        (root / path).write_bytes(body)
        rows.append({**templates[0], "id": content.sha256, "sha256": content.sha256,
                     "bytes": content.size_bytes, "path": path})
    catalog = b"\n".join(canonical_json(row) for row in rows) + b"\n"
    (root / "catalog.jsonl").write_bytes(catalog)
    return import_library(root, BlobRef.of(catalog), objects)


def test_supplement_materials_preserves_versions_and_deduplicates_raw_text(tmp_path, publication):
    store, role, _, _ = configuration(tmp_path, publication)
    request = material_request(store, role, ())
    store.put(request.to_bytes())
    initial = store.put(freeze_materials(request, store).to_bytes())
    assert parse_json(request.to_bytes())["schema"] == "behave.round-material-request/1"
    assert supplement_materials(materials=initial, libraries=(), objects=store) == initial

    first = supplement_materials(materials=initial, libraries=(role.inputs.library,), objects=store)
    existing = raw_documents(store, first)
    extra_text = b"// Supplemental upstream dependency\nint twice(int x) { return x + x; }\n"
    extra = supplemental_library(tmp_path / "extra", store, (existing[0].data, extra_text))
    final = supplement_materials(materials=first, libraries=(extra, extra), objects=store)
    assert raw_documents(store, initial) == ()
    assert raw_documents(store, first) == existing
    documents = raw_documents(store, final)
    assert [file.data for file in documents] == [file.data for file in existing] + [extra_text]
    assert supplement_materials(materials=final, libraries=(extra,), objects=store) == final
    frozen = load_materials(store, final)
    updated = MaterialRequest.from_bytes(store.read(frozen.request))
    assert parse_json(updated.to_bytes())["schema"] == "behave.round-material-request/2"
    assert updated.supplements == (role.inputs.library, extra)
    assert replace(updated, supplements=()) == request
    assert construction_briefs(store, final) == {}  # No invented acquisition rationale.

    from behave.improve.round_data import load_round_data, prepare_round_data

    run = JournalDirectory(tmp_path / "data")
    data = prepare_round_data(materials=final, publication=None, reader=store, run=run)
    head, closure = run.seal("round_data")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    assert load_round_data(exported, head) == data
    assert raw_documents(exported, data.materials) == documents
    extra_index = LibraryIndex(exported, extra)
    assert extra_index.notice_texts  # Attribution exports but is not a Raw sample.
    assert all(file.reference.path.startswith("source/") for file in documents)


def test_supplement_materials_checks_declared_library_bytes(tmp_path, publication):
    store, role, _, _ = configuration(tmp_path, publication)
    request = material_request(store, role, ())
    store.put(request.to_bytes())
    initial = store.put(freeze_materials(request, store).to_bytes())
    library = SourceLibrary.from_bytes(store.read(role.inputs.library))
    first, *rest = library.units
    changed = replace(first, content=FileRef(first.content.path, store.put(b"changed source")))
    forged = store.put(replace(library, units=(changed, *rest)).to_bytes())
    with pytest.raises(RecordError, match="source identity or origins changed"):
        supplement_materials(materials=initial, libraries=(forged,), objects=store)
    assert raw_documents(store, initial) == ()


def test_empty_round_has_no_invented_raw_training_documents(tmp_path, publication):
    store, role, _, _ = configuration(tmp_path, publication)
    request = material_request(store, role, ())
    assert MaterialRequest.from_bytes(request.to_bytes()) == request
    store.put(request.to_bytes())
    frozen = freeze_materials(request, store)
    reference = store.put(frozen.to_bytes())
    assert load_materials(store, reference) == frozen
    assert raw_documents(store, reference) == ()
    # A generated answer in the researcher store cannot be smuggled into C_r.
    forged = store.put(replace(frozen, documents=(FileRef("behavior.py", store.put(b"generated answer")),)).to_bytes())
    with pytest.raises(RecordError, match="acquired source union"):
        raw_documents(store, forged)
    with pytest.raises(RecordError, match="another round"):
        freeze_materials(replace(request, round_id="another-round"), store)
    with pytest.raises(ValueError, match="twice"):
        replace(request, acquisitions=(reference, reference))


@pytest.mark.isolation
@pytest.mark.parametrize("empty", [False, True])
def test_supervised_acquisition_freeze_export_and_cold_recovery(tmp_path, publication, monkeypatch, empty):
    store, original, rows, observation = configuration(tmp_path, publication)
    replies = [call("run_command", {"command": "cat brief.md"})]
    if not empty:
        replies += [call("run_command", {"command": f"cat library/source/{rows[0]['id']}.txt"})]
    replies += [call("submit_materials", {"materials": [], "experience": {"construction": "", "solver": ""}}
                    if empty else {**material(rows[0]["id"]), "materials": [{
                        **material(rows[0]["id"])["materials"][0], "supporting_source_ids": [rows[1]["id"]]}]})]
    role = job_for(original.inputs, replies, guard="""
names = {tool['name'] for tool in request.tools}
assert names == {'run_command', 'submit_materials'}
if request.turn:
    assert any(message.get('role') == 'tool' and message['content'].get('exit_code') == 0
               for message in request.messages)
assert b'PRIVATE_BEHAVIOR_MUST_NOT_EXECUTE' not in canonical_json(list(request.messages))
""")
    role = replace(role, shell_limits=ResourceLimits(wall_seconds=20, processes=8))
    config = AcquisitionConfiguration(role, AdmissionPolicy("acquirers", 2))
    run = JournalDirectory(tmp_path / "stage")
    result = acquire_materials(configuration=config, reader=store, run=run)
    assert result["status"] == "completed", result
    assert result["publication_approved"] is False
    from behave.improve.experience import load_experience

    memory = BlobRef.from_dict(result["materials"]["experience"])
    assert {file.reference.path for file in load_experience(run.objects, memory)} == {
        "experience/construction.md", "experience/solver.md"}
    assert result["billing"]["known_estimates_by_currency"] == {"USD": "0.02" if empty else "0.03"}
    assert result["billing"]["unresolved_dispatches"] == 0
    head, closure = run.seal("acquisition")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    assert load_acquisition_report(exported, head) == result
    request = material_request(exported, role, (head,))
    # Acquisition deliberately has only a checkpoint identity, not permission
    # to read its weight/provenance object. The researcher adds that separate
    # controller input when exporting the round-freeze operation.
    exported.publish(store, (request.checkpoint,))
    exported.put(request.to_bytes())
    frozen = freeze_materials(request, exported)
    with monkeypatch.context() as live:
        live.setattr("behave.improve.materials.load_acquisition_report",
                     lambda *args: pytest.fail("live handoff repeated acquisition replay"))
        assert freeze_materials(request, exported, acquisition_reports=(result,)) == frozen
    frozen_ref = exported.put(frozen.to_bytes())
    briefs = construction_briefs(exported, frozen_ref)
    if empty:
        assert briefs == {}
    else:
        brief = parse_json(briefs[rows[0]["id"]])
        assert brief["objectives"][0]["objective"] == material(rows[0]["id"])["materials"][0]["objective"]
        assert brief["supporting_source_ids"] == [rows[1]["id"]]
        assert all(file.data != briefs[rows[0]["id"]] for file in raw_documents(exported, frozen_ref))
    reference = exported.put(frozen.to_bytes())
    assert FrozenMaterials.from_bytes(frozen.to_bytes()) == frozen
    documents = raw_documents(exported, reference)
    assert len(documents) == (0 if empty else 2)
    assert all(file.reference.path.startswith("source/") for file in documents)
    assert all(b"PRIVATE_" not in file.data and b"feedback" not in file.data for file in documents)
    from behave.improve.round_data import load_round_data, prepare_round_data

    data_run = JournalDirectory(tmp_path / "round-data")
    data = prepare_round_data(materials=reference, publication=None, reader=exported, run=data_run)
    data_head, data_closure = data_run.seal("round_data")
    data_export = DirectoryStore(tmp_path / "round-data-export")
    data_export.publish(data_run.objects, data_closure)
    assert load_round_data(data_export, data_head) == data
    assert raw_documents(data_export, data.materials) == documents
    # Admission is intentionally not an input: rejecting a generated task later
    # cannot remove its acquired material from the shared Raw/Task source set.
    assert "admission" not in MaterialRequest.__dataclass_fields__
    with pytest.raises(RecordError, match="unbound model transport"):
        freeze_materials(replace(request, model_transport="another-checkpoint-transport"), exported)
    monkeypatch.setattr("behave.isolation.service.run_service", lambda *a, **kw: pytest.fail("cold redispatch"))
    assert acquire_materials(configuration=config, reader=Unavailable(), run=run) == result
    assert load_materials(exported, reference) == frozen
    ledger = run.journal("revisions", "r0", "source_acquirer_provider")
    ledger.append(ledger.load(), "unexpected", {})
    with pytest.raises(RecordError):
        acquire_materials(configuration=config, reader=Unavailable(), run=run)


@pytest.mark.isolation
def test_stopped_acquisition_is_not_empty_success(tmp_path, publication):
    store, original, _, _ = configuration(tmp_path, publication)
    role = job_for(original.inputs, [ModelReply("stop")])
    run = JournalDirectory(tmp_path / "stopped")
    result = acquire_materials(configuration=AcquisitionConfiguration(role), reader=store, run=run)
    assert result["status"] == "unfinished" and result["materials"] is None
    head, _ = run.seal("acquisition")
    request = material_request(run.objects, role, (head,))
    run.objects.publish(store, (request.checkpoint,))
    with pytest.raises(RecordError, match="settle acquisition"):
        freeze_materials(request, run.objects)


def test_unknown_acquisition_worker_is_not_restarted(tmp_path, publication, monkeypatch):
    store, original, _, _ = configuration(tmp_path, publication)
    role = job_for(original.inputs, [ModelReply("stop")])
    config = AcquisitionConfiguration(role)
    run = JournalDirectory(tmp_path / "interrupted")

    def lost(**kwargs):
        raise AttemptInterrupted("owner-lost")

    monkeypatch.setattr("behave.data.role.supervise_worker", lost)
    with pytest.raises(AttemptInterrupted):
        acquire_materials(configuration=config, reader=store, run=run)
    monkeypatch.setattr("behave.data.role.supervise_worker", lambda **kw: pytest.fail("duplicate launch"))
    with pytest.raises(AttemptInterrupted):
        acquire_materials(configuration=config, reader=Unavailable(), run=run)
    assert run.journal("acquisition").load()[-1].kind == "acquisition_requested"
