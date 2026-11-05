"""Historical source screening evidence and its retained scope provenance."""

from dataclasses import replace

import pytest
from support.source_archive_fixture import archive_source_run, historical_job
from test_attempts import Scripted, call
from test_role_process import Unavailable, job_for

from behave.agent.admission import AdmissionPolicy
from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.model import ModelReply
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, FileRef, SourceArtifact
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.drafts import SourceRecord, load_scope, scope_closure
from behave.data.role import RoleJob, ScopeInputs, SelectionInputs, input_closure
from behave.data.scope import prepare_scope
from behave.data.selection import (
    SelectionConfiguration,
    SelectionResult,
    inspected_spans,
    load_selection,
    run_selection,
    select_source,
    selected_scope_inputs,
    selection_closure,
    selection_summary,
    selection_view,
    validate_selection,
)
from behave.data.selection_records import SourceInventory, SourceSelection, SourceSpan, content_key


def seed(tmp_path, bodies=(b"def f(x):\n    return x + 1\n", b"def f(x):\n    return 0\n")):
    store = DirectoryStore(tmp_path / "source-objects")
    sources = []
    for number, body in enumerate(bodies):
        artifact = SourceArtifact((FileRef("original.py", store.put(body)),), "original.py", "f")
        source = SourceRecord(f"source-{number}", f"https://example.org/repo{number}", "v1", None, artifact)
        sources.append(store.put(source.to_bytes()))
    inventory = store.put(SourceInventory(tuple(sources)).to_bytes())
    guide = store.put(b"Retain substantive computation; report uncertainty without silently removing candidates.")
    return store, SelectionInputs(inventory, guide)


def assessments(count=2, selected=0, *, end=10):
    return {"assessments": [{"candidate": n, "disposition": "selected" if n == selected else "unreviewed",
        "rationale": "Inspected the entry function." if n == selected else "Not inspected in this attempt.",
        "citations": [{"path": "original.py", "start": 0, "end": end}] if n == selected else []} for n in range(count)]}


def page(number, offset=0):
    return call("read_source", {"document": number, "offset": offset})


def role(store, inputs, tmp_path, replies, *, max_turns=60):
    journal = JournalDirectory(tmp_path / "role").journal("selector")
    spec = AttemptSpec("source-screen", inputs.role, {"model": "script"}, max_turns=max_turns)
    backend = Scripted(*replies)
    result = run_selection(inputs=inputs, reader=store, journal=journal, spec=spec, backend=backend)
    return result, journal, backend, spec


def test_read_before_selection_cold_replay_and_explicit_unreviewed(tmp_path):
    store, inputs = seed(tmp_path)
    result, journal, backend, spec = role(store, inputs, tmp_path, [
        call("submit_selection", assessments()), page(0), call("submit_selection", assessments()),
        page(1), call("submit_selection", assessments())])
    assert result.reason == "final_action" and result.turns == 5
    assert "selection-evidence-mismatch" in str(backend.requests[-1].messages)
    decision = SourceSelection.from_bytes(journal.objects.read(BlobRef.from_dict(thaw_json(result.final_action)["selection"])))
    assert [a.disposition for a in decision.assessments] == ["selected", "unreviewed"]
    view = selection_view(journal.objects, inputs)
    assert list(inspected_spans(journal.objects, journal.load(), view)) == [(0, view.sources[0].selected.files[0])]
    # The exact same loop replays all prior tool/model calls without executing handlers again.
    assert run_selection(inputs=inputs, reader=store, journal=journal, spec=spec, backend=Scripted()) == result


def test_readonly_tools_no_host_paths_no_source_execution(tmp_path):
    store, inputs = seed(tmp_path, (b"raise RuntimeError('must never execute source')\n",))
    result, journal, backend, _ = role(store, inputs, tmp_path, [
        call("write_file", {"path": "inventory.json", "content": "{}"}),
        call("run_command", {"args": ["python", "original.py"]}),
        call("read_file", {"path": "/etc/passwd"}), page(50), page(1),
        call("submit_selection", assessments(count=1))])
    assert result.reason == "final_action"
    for request in backend.requests:
        assert {t["name"] for t in request.tools} == {"read_file", "list_files", "read_source", "submit_selection"}
    assert "tool-not-available" in str(backend.requests[-1].messages)
    assert "artifact-path-invalid" in str(backend.requests[-1].messages)
    assert journal.load()[-1].kind == "terminal"


@pytest.mark.parametrize("change", [
    "missing", "reorder", "duplicate", "selected_unseen", "wrong_file", "unseen_source", "span_past_eof",
    "bool_index", "bool_span", "made_up", "read_but_unreviewed",
])
def test_bad_dispositions_and_citations_do_not_end_attempt(tmp_path, change):
    store, inputs = seed(tmp_path)
    args = assessments()
    rows = args["assessments"]
    if change == "missing":
        rows.pop()
    elif change == "reorder":
        rows.reverse()
    elif change == "duplicate":
        rows[1]["candidate"] = 0
    elif change == "selected_unseen":
        rows[1] = {**rows[0], "candidate": 1}
    elif change == "wrong_file":
        rows[0]["citations"][0]["path"] = "LICENSE"
    elif change == "unseen_source":
        args = assessments(selected=1)
        args["assessments"][0].update(disposition="deferred")
    elif change == "span_past_eof":
        rows[0]["citations"][0]["end"] = 10000
    elif change == "bool_index":
        rows[0]["candidate"] = False
    elif change == "bool_span":
        rows[0]["citations"][0]["start"] = False
    elif change == "made_up":
        rows[0]["disposition"] = "approved"
    else:
        rows[0].update(disposition="unreviewed", citations=[])
    result, _, backend, _ = role(store, inputs, tmp_path, [page(1), call("submit_selection", args), ModelReply("stop")])
    assert result.reason == "stopped" and result.final_action is None
    assert "selection-" in str(backend.requests[-1].messages)


@pytest.mark.parametrize("chosen", [(), (0,), (0, 1)])
def test_selection_cardinality_and_record_identity(tmp_path, chosen):
    store, inputs = seed(tmp_path)
    args = assessments(selected=None)
    for number in chosen:
        args["assessments"][number] = assessments(selected=number)["assessments"][number]
    replies = [*(page(number + 1) for number in chosen), call("submit_selection", args)]
    result, journal, _, spec = role(store, inputs, tmp_path, replies)
    ref = BlobRef.from_dict(thaw_json(result.final_action)["selection"])
    packet = journal.objects.read(ref)
    decision = SourceSelection.from_bytes(packet)
    inventory = SourceInventory.from_bytes(store.read(inputs.inventory))
    assert decision.selected == tuple(inventory.sources[number] for number in chosen)
    expected_schema = "behave.source-selection/2" if len(chosen) > 1 else "behave.source-selection/1"
    assert parse_json(packet)["schema"] == expected_schema
    assert decision.to_bytes() == packet
    assert run_selection(inputs=inputs, reader=store, journal=journal, spec=spec, backend=Scripted()) == result
    if len(chosen) > 1:
        old = {**parse_json(packet), "schema": "behave.source-selection/1"}
        with pytest.raises(RecordError, match="canonical"):
            SourceSelection.from_bytes(canonical_json(old))


def test_batch_selection_export_and_each_scope_handoff(tmp_path):
    store, inputs = seed(tmp_path, (b"def f(x): return x\n", b"def f(x): return x + 1\n", b"unused\n"))
    args = assessments(count=3)
    args["assessments"][1] = assessments(count=3, selected=1)["assessments"][1]
    replies = [page(1), page(2), call("submit_selection", args)]
    config = SelectionConfiguration(historical_job(inputs))
    run = JournalDirectory(tmp_path / "selection-run")
    archive_source_run(config, store, run, replies)
    result = select_source(configuration=config, reader=Unavailable(), run=run)
    assert result.status == "selected"
    head, closure = run.seal("selection")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    inventory = SourceInventory.from_bytes(store.read(inputs.inventory))
    summary = selection_summary(exported, head)
    assert summary["sources"] == [source.as_dict() for source in inventory.sources[:2]]
    assert summary["source"] is None  # No implicit first winner for old report consumers.
    assert summary["dispositions"] == {"selected": 2, "rejected": 0, "deferred": 0, "unreviewed": 1}
    for source in inventory.sources[:2]:
        handed = selected_scope_inputs(exported, head, guidance=inputs.guidance, source=source)
        assert handed == ScopeInputs(source, inputs.guidance, selection=head)
        selected_closure = selection_closure(exported, head, source=source)
        assert head in selected_closure
        assert set(selected_closure) <= set(input_closure(job_for(handed, []), exported))
    with pytest.raises(RecordError, match="explicit source"):
        selected_scope_inputs(exported, head, guidance=inputs.guidance)
    with pytest.raises(RecordError, match="different source"):
        selected_scope_inputs(exported, head, guidance=inputs.guidance, source=inventory.sources[2])
    with pytest.raises(RecordError, match="different source"):
        selection_closure(exported, head, source=inventory.sources[2])
    assert select_source(configuration=config, reader=Unavailable(), run=run) == result


def test_source_explanation_does_not_require_executable_code(tmp_path):
    store, inputs = seed(tmp_path, (b"# Saturation\nClamp the scaled integer to the declared output range.\n",))
    result, journal, _, _ = role(store, inputs, tmp_path, [page(1), call("submit_selection", assessments(1))])
    decision = SourceSelection.from_bytes(journal.objects.read(
        BlobRef.from_dict(thaw_json(result.final_action)["selection"])))
    assert len(decision.selected) == 1


def test_paged_unicode_spans_need_full_coverage_and_no_gaps(tmp_path):
    body = ("中" * 40000).encode()
    store, inputs = seed(tmp_path, (body,))
    args = assessments(1, end=33000)
    result, journal, backend, _ = role(store, inputs, tmp_path, [
        page(1), page(1, 32768), call("submit_selection", args), page(1, 16384), call("submit_selection", args)])
    assert result.reason == "final_action"
    assert "fully covered" in str(backend.requests[-1].messages)
    decision = SourceSelection.from_bytes(journal.objects.read(BlobRef.from_dict(thaw_json(result.final_action)["selection"])))
    view = selection_view(journal.objects, inputs)
    spans = inspected_spans(journal.objects, journal.load(), view)
    validate_selection(decision, inputs, view, spans)
    assert len(spans[(0, view.sources[0].selected.files[0])]) == 3


def test_binary_and_empty_pages_are_not_inspection(tmp_path):
    store, inputs = seed(tmp_path, (b"\xff\0", b""))
    result, _, backend, _ = role(store, inputs, tmp_path, [page(1), page(2), page(2, 999),
        call("submit_selection", assessments(selected=None))])
    assert result.reason == "final_action"
    assert "review-document-not-text" in str(backend.requests[-1].messages)


def test_exact_content_groups_preserve_all_provenance_and_selectors(tmp_path):
    store, inputs = seed(tmp_path, (b"def f(): pass\n",) * 3)
    view = selection_view(store, inputs)
    rows = parse_json(view.index)["candidates"]
    assert [r["duplicate_of"] for r in rows] == [None, 0, 0]
    assert len(set(view.inventory.sources)) == 3
    first = view.sources[0]
    assert content_key(first) == content_key(replace(first, revision="other", license="other", origin="other"))
    assert content_key(first) != content_key(replace(first, selected=replace(first.selected, entry_symbol="g")))
    # Equal bytes in a different candidate do not imply that candidate's pages were read.
    result, _, _, _ = role(store, inputs, tmp_path, [page(1), call("submit_selection", assessments(3))])
    assert result.reason == "final_action"


def test_inventory_verifies_large_source_bodies_without_workspace_quotas(tmp_path):
    body = b"x" * (17 << 20)
    store, inputs = seed(tmp_path, (body,) * 4)
    reads = []

    def counted(ref):
        reads.append(ref)
        return store.read(ref)

    reader = type("Reader", (), {"read": staticmethod(counted)})()
    view = selection_view(reader, inputs)
    assert len(view.sources) == 4
    assert BlobRef.of(body) in reads
    assert sum(f.content.size_bytes for _, f in view.documents) > 64 << 20


def test_selection_cannot_be_reenabled_by_current_shell_limits(tmp_path):
    store, inputs = seed(tmp_path)
    with pytest.raises(ValueError, match="retired"):
        input_closure(job_for(inputs, []), store)


def test_closed_records_and_manifest_compatibility(tmp_path):
    store, inputs = seed(tmp_path)
    job = historical_job(inputs)
    config = SelectionConfiguration(job, AdmissionPolicy("selectors", 1))
    assert SelectionConfiguration.from_bytes(config.to_bytes()) == config
    assert RoleJob.from_bytes(job.to_bytes()) == job
    with pytest.raises(ValueError, match="retired"):
        input_closure(job, store)
    with pytest.raises(RecordError, match="canonical"):
        SelectionConfiguration.from_bytes(config.to_bytes() + b"\n")
    with pytest.raises(ValueError, match="unique"):
        SourceInventory((inputs.inventory, inputs.inventory))
    with pytest.raises(ValueError):
        SourceSpan(FileRef("x", BlobRef.of(b"x")), True, 2)
    bad = parse_json(job.to_bytes())
    bad["inputs"]["network"] = "host"
    with pytest.raises(RecordError):
        RoleJob.from_bytes(canonical_json(bad))
    old = historical_job(ScopeInputs(inputs.inventory, inputs.guidance))
    assert parse_json(old.to_bytes())["schema"] == "behave.construction-role-job/3"
    assert "selection" not in parse_json(old.to_bytes())["inputs"]


@pytest.mark.isolation
def test_archived_billing_export_to_current_scope_worker_and_no_cold_redispatch(tmp_path, monkeypatch):
    from test_scope_preparation import prepared
    from test_source_fetch import URL, downloaded, fake_service

    from behave.data.ingest import FetchPlan, FetchPolicy, RemoteFile, acquire_source

    _, _, _, scope_options = prepared(tmp_path, revise=False)
    store, scope_plan = scope_options["reader"], scope_options["plan"]
    original = SourceRecord.from_bytes(store.read(scope_plan.author.inputs.source))
    fetched = JournalDirectory(tmp_path / "fetch")
    fetch_plan = FetchPlan(original.source_id, "https://sources.example/project", original.revision, original.license,
        (RemoteFile("original.py", URL),), "original.py", original.selected.entry_symbol)
    with monkeypatch.context() as patch:
        fake_service(patch, {URL: downloaded(store.read(original.selected.files[0].content))})
        acquisition = acquire_source(fetch_plan, FetchPolicy(("sources.example",)), run=fetched)
    assert acquisition.status == "acquired"
    _, fetch_closure = fetched.seal("acquisition")
    store.publish(fetched.objects, fetch_closure)
    source = acquisition.source
    # An independently acquired copy may have the same source-content key but
    # different attribution. Do not forge a new identity under the old receipt.
    other = store.put(replace(original, source_id="second").to_bytes())
    inventory = store.put(SourceInventory((source, other)).to_bytes())
    inputs = SelectionInputs(inventory, scope_plan.author.inputs.guidance)
    replies = [call("read_file", {"path": "inventory.json"}), page(1), call("submit_selection", assessments())]
    config = SelectionConfiguration(historical_job(inputs), AdmissionPolicy("selectors", 2))
    run = JournalDirectory(tmp_path / "selection-run")
    archive_source_run(config, store, run, replies)
    result = select_source(configuration=config, reader=Unavailable(), run=run)
    assert result.status == "selected", result.to_bytes()
    assert SelectionResult.from_bytes(result.to_bytes()) == result
    head, closure = run.seal("selection")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    recovered, bill = load_selection(exported, head)
    assert recovered == result and bill.cost_by_currency() == {"USD": "0.03"}
    assert bill.unresolved_dispatches == bill.unpriced_dispatches == 0
    summary = selection_summary(exported, head)
    assert summary["source"] == source.as_dict()
    assert summary["dispositions"] == {"selected": 1, "rejected": 0, "deferred": 0, "unreviewed": 1}
    assert (summary["candidates"], summary["inspected"], summary["unreported"]) == (2, 1, 0)
    assert summary["publication_approved"] is False
    store.publish(exported, closure)
    handed = selected_scope_inputs(store, head, guidance=scope_plan.author.inputs.guidance)
    assert handed.source == source and handed.selection == head
    scope_options["plan"] = replace(scope_plan, author=replace(scope_plan.author, inputs=handed))
    scoped = prepare_scope(**scope_options)
    assert scoped.status == "review_ready", scoped.to_bytes()
    scoped_store = scope_options["run"].objects
    selected_scope = scoped.scopes[-1]
    scope, _, _ = load_scope(scoped_store, selected_scope)
    assert scope.selection == head
    assert set(closure) <= set(scope_closure(scoped_store, selected_scope))
    assert original.selected == SourceRecord.from_bytes(scoped_store.read(source)).selected
    lost_selection = scoped_store.put(replace(scope, parent=selected_scope, selection=None).to_bytes())
    with pytest.raises(RecordError, match="retain the source selection"):
        load_scope(scoped_store, lost_selection)
    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("no redispatch during cold recovery"))
    assert select_source(configuration=config, reader=Unavailable(), run=run) == result
    with pytest.raises(RecordError, match="different source"):
        selection_closure(exported, head, source=other)
    # Changed auxiliary ledgers cannot be silently accepted on local recovery.
    ledger = run.journal("revisions", "r0", "source_selector_provider")
    ledger.append(ledger.load(), "unexpected", {})
    with pytest.raises(RecordError):
        select_source(configuration=config, reader=Unavailable(), run=run)


@pytest.mark.parametrize("finish", ["no_selection", "stopped", "turn_limit"])
def test_nonselection_is_not_a_failed_task_or_scope_handoff(tmp_path, finish):
    store, inputs = seed(tmp_path)
    replies = [call("submit_selection", assessments(selected=None))] if finish == "no_selection" else [ModelReply("done")]
    job = historical_job(inputs)
    if finish == "turn_limit":
        replies = [page(0)]
        job = replace(job, attempt=replace(job.attempt, max_turns=1))
    run = JournalDirectory(tmp_path / "selection-run")
    config = SelectionConfiguration(job)
    archive_source_run(config, store, run, replies)
    result = select_source(configuration=config, reader=Unavailable(), run=run)
    assert result.status == ("no_selection" if finish == "no_selection" else "unfinished")
    head, _ = run.seal("selection")
    assert load_selection(run.objects, head)[0] == result
    summary = selection_summary(run.objects, head)
    assert summary["unreported"] == (0 if finish == "no_selection" else 2)
    with pytest.raises(RecordError, match="actual selection"):
        selected_scope_inputs(run.objects, head, guidance=inputs.guidance)


def test_unknown_dispatch_is_not_retried_or_promoted(tmp_path, monkeypatch):
    store, inputs = seed(tmp_path)
    config = SelectionConfiguration(historical_job(inputs))
    run = JournalDirectory(tmp_path / "selection-run")
    import behave.data.role as processes
    with pytest.raises(AttemptInterrupted, match="fixture-owner-lost"):
        archive_source_run(config, store, run, [page(1)], pending=True)
    monkeypatch.setattr(processes, "supervise_worker", lambda **kw: pytest.fail("pending launch must not be repeated"))
    with pytest.raises(AttemptInterrupted, match="legacy-source-run-unfinished"):
        select_source(configuration=config, reader=Unavailable(), run=run)
    assert run.journal("selection").load()[-1].kind == "selection_requested"


@pytest.mark.parametrize("stage", ["selection", "discovery"])
def test_retired_cli_stage_is_rejected_without_creating_run(tmp_path, capsys, stage):
    from behave.data.__main__ import main

    store, inputs = seed(tmp_path)
    config = SelectionConfiguration(historical_job(inputs))
    path = tmp_path / "selection.json"
    path.write_bytes(config.to_bytes())
    target = tmp_path / "not-created"
    with pytest.raises(SystemExit) as error:
        main(["--stage", stage, "--configuration", str(path), "--source-objects", str(store.directory),
              "--run-directory", str(target), "--dry-run"])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    assert not target.exists()


def test_rejected_and_deferred_sources_are_reported_with_evidence(tmp_path):
    store, inputs = seed(tmp_path, (b"def f(x): return x\n",) * 3)
    args = assessments(3)
    args["assessments"][1].update(disposition="rejected", rationale="Duplicate retained computation.",
                                 citations=[{"path": "original.py", "start": 0, "end": 10}])
    args["assessments"][2].update(disposition="deferred", rationale="Need a separate scope assessment.")
    result, _, _, _ = role(store, inputs, tmp_path, [page(1), page(2), page(3), call("submit_selection", args)])
    assert result.reason == "final_action"


def test_offline_inspection_recomputes_pages_instead_of_trusting_report(tmp_path):
    store, inputs = seed(tmp_path)
    _, journal, _, _ = role(store, inputs, tmp_path, [page(1), ModelReply("stop")])
    view = selection_view(journal.objects, inputs)
    events = list(journal.load())
    index = next(n for n, e in enumerate(events) if e.kind == "tool_settled")
    row = thaw_json(events[index].payload)
    page_result = parse_json(journal.objects.read(BlobRef.from_dict(row["result"])))
    page_result["content"] = "forged page claims source is good"
    row["result"] = journal.objects.put(canonical_json(page_result)).as_dict()
    events[index] = replace(events[index], payload=row)
    with pytest.raises(RecordError, match="page differs"):
        inspected_spans(journal.objects, events, view)


def test_clean_worker_timeout_is_interruption_not_rejection(tmp_path, monkeypatch):
    store, inputs = seed(tmp_path)
    run = JournalDirectory(tmp_path / "timed-out")
    config = SelectionConfiguration(historical_job(inputs))
    archive_source_run(config, store, run, [], receipt={
        "schema": "behave.construction-settlement/1", "cleanup": "complete", "status": "timeout",
        "returncode": None, "elapsed_seconds": .1, "interruption": None, "supervision_errors": []})
    result = select_source(configuration=config, reader=Unavailable(), run=run)
    assert result.status == "interrupted" and result.decision is None
    head, _ = run.seal("selection")
    assert load_selection(run.objects, head)[0] == result
    assert selection_summary(run.objects, head)["unreported"] == 2
    monkeypatch.setattr("behave.isolation.service.run_service", lambda *a, **kw: pytest.fail("must not retry"))
    assert select_source(configuration=config, reader=Unavailable(), run=run) == result
