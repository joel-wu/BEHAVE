"""Historical discovery evidence remains bounded, replayable and not approval."""

from dataclasses import replace

import pytest
from support.source_archive_fixture import archive_source_run, historical_job
from test_attempts import Scripted, call
from test_role_process import Unavailable
from test_source_fetch import SOURCE, URL, downloaded, fake_service, selection

from behave.agent.admission import AdmissionPolicy
from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.model import ModelReply
from behave.agent.run_store import JournalDirectory
from behave.core.records import RecordError, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.discovery import (
    DiscoveryConfiguration,
    discover_sources,
    discovery_inventory,
    fetched_sources,
    load_discovery,
    run_discovery,
)
from behave.data.ingest import FetchPolicy
from behave.data.role import DiscoveryInputs, SelectionInputs
from behave.data.selection import selection_view
from behave.data.selection_records import SourceInventory
from behave.isolation.service import ServiceResult


def seed(tmp_path, *, max_fetches=3):
    objects = DirectoryStore(tmp_path / "source")
    inputs = DiscoveryInputs(objects.put(b"Fixture-only guidance. Do not execute downloaded code."),
                             objects.put(FetchPolicy(("sources.example",), total_bytes=1 << 20).to_bytes()), max_fetches)
    return objects, inputs


def fetch(plan=None):
    return call("fetch_source", {"plan": parse_json((plan or selection()).to_bytes())})


def read(candidate=0, path="model.c", offset=0):
    return call("read_source", {"candidate": candidate, "path": path, "offset": offset})


def run(tmp_path, objects, inputs, replies):
    journal = JournalDirectory(tmp_path / "attempt").journal("discovery_role")
    spec = AttemptSpec("discovery", inputs.role, {"model": "script"})
    backend = Scripted(*replies)
    result = run_discovery(inputs=inputs, reader=objects, journal=journal, backend=backend, spec=spec)
    return result, journal, spec, backend


def test_live_fetch_read_revise_and_cold_replay_preserve_all_sources(tmp_path, monkeypatch):
    objects, inputs = seed(tmp_path)
    calls = fake_service(monkeypatch, {URL: downloaded()})
    other = replace(selection(), source_id="new-selection", entry_symbol="other")
    result, journal, spec, backend = run(tmp_path, objects, inputs, [fetch(), read(), fetch(other),
        call("list_sources", {}), call("submit_sources", {})])
    assert result.reason == "final_action" and result.turns == 5
    assert calls == [URL, URL]
    sources, attempts, count = fetched_sources(journal.objects, journal.load(), inputs)
    assert len(sources) == len(attempts) == count == 2
    assert thaw_json(result.final_action) == {"sources": [s.as_dict() for s in sources]}
    assert backend.requests[2].messages[-2]["content"]["content"] == SOURCE.decode()
    assert all({t["name"] for t in request.tools} == {
        "fetch_source", "read_source", "list_sources", "submit_sources"} for request in backend.requests)
    assert run_discovery(inputs=inputs, reader=journal.objects, journal=journal, spec=spec,
                         backend=Scripted()) == result
    assert calls == [URL, URL]  # No HTTP or model dispatch on completed replay.


def test_rejected_and_transient_fetches_do_not_disappear(tmp_path, monkeypatch):
    from behave.data.ingest.fetch_http import Hop

    objects, inputs = seed(tmp_path)
    forbidden = replace(selection(), files=(replace(selection().files[0], url="https://other.example/code"),))
    calls = fake_service(monkeypatch, {URL: Hop("fetch-http-unavailable", "8.8.8.8", 503)})
    result, journal, _, _ = run(tmp_path, objects, inputs, [fetch(forbidden), fetch(), call("submit_sources", {})])
    sources, attempts, _ = fetched_sources(journal.objects, journal.load(), inputs)
    assert not sources and [a.status for a in attempts] == ["rejected", "unavailable"]
    assert calls == [URL] and thaw_json(result.final_action) == {"sources": []}


def test_unknown_fetch_cleanup_interrupts_instead_of_submitting_sources(tmp_path, monkeypatch):
    objects, inputs = seed(tmp_path)
    fake_service(monkeypatch, {URL: downloaded()},
                 result=ServiceResult("cleanup-incomplete", None, b"", b"", .01))
    with pytest.raises(AttemptInterrupted, match="cleanup-unconfirmed"):
        run(tmp_path, objects, inputs, [fetch(), call("submit_sources", {})])
    journal = JournalDirectory(tmp_path / "attempt").journal("discovery_role")
    assert journal.load()[-1].kind == "tool_settled"
    assert journal.load()[-1].artifacts  # Uncertain service evidence is retained.


@pytest.mark.parametrize("bad", [True, 0, "3"])
def test_invalid_fetch_budgets_refused(tmp_path, bad):
    _, inputs = seed(tmp_path)
    with pytest.raises(ValueError):
        replace(inputs, max_fetches=bad)


@pytest.mark.parametrize("relay", [False, True])
def test_archived_lifecycle_offline_replay_and_empty_discovery(tmp_path, monkeypatch, relay):
    objects, inputs = seed(tmp_path)
    calls = fake_service(monkeypatch, {})
    # Historical role replay fixture rejects the host before DNS.
    denied = replace(selection(), files=(replace(selection().files[0], url="https://denied.example/code"),))
    replies = [fetch(denied), call("submit_sources", {})]
    job = historical_job(inputs)
    config = DiscoveryConfiguration(job, AdmissionPolicy("discovery-model", 1) if relay else None)
    run = JournalDirectory(tmp_path / "supervisor")
    archive_source_run(config, objects, run, replies)
    report = discover_sources(configuration=config, reader=Unavailable(), run=run)
    assert not calls
    assert report["status"] == "completed" and report["sources"] == []
    assert report["acquisitions"][0]["code"] == "fetch-host-forbidden"
    assert report["billing"]["known_estimates_by_currency"] == {"USD": "0.02"}
    assert report["publication_approved"] is False
    assert discover_sources(configuration=config, reader=Unavailable(), run=run) == report
    head, closure = run.seal("discovery")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    assert load_discovery(exported, head) == report
    with pytest.raises(RecordError, match="nonempty"):
        discovery_inventory(exported, head)


def test_normal_stop_remains_unfinished_and_has_billing(tmp_path):
    objects, inputs = seed(tmp_path)
    config, run = DiscoveryConfiguration(historical_job(inputs)), JournalDirectory(tmp_path / "run")
    archive_source_run(config, objects, run, [ModelReply("stop")])
    report = discover_sources(configuration=config, reader=Unavailable(), run=run)
    assert report["status"] == "unfinished" and report["sources"] == []
    assert report["billing"]["known_estimates_by_currency"] == {"USD": "0.01"}


def test_discovery_inventory_requires_actual_complete_receipt(tmp_path):
    objects, inputs = seed(tmp_path)
    source = objects.put(b"not a source")
    plain = SourceInventory((source,))
    assert SourceInventory.from_bytes(plain.to_bytes()) == plain
    extended = replace(plain, discovery=objects.put(b"not a discovery"))
    assert SourceInventory.from_bytes(extended.to_bytes()) == extended
    inventory = objects.put(extended.to_bytes())
    with pytest.raises((RecordError, ValueError, TypeError)):
        selection_view(objects, SelectionInputs(inventory, inputs.guidance))


def test_coordinator_download_history_survives_selection_export(tmp_path, monkeypatch):
    from behave.agent.journal import load_chain
    from behave.core.artifacts import BlobRef

    objects, inputs = seed(tmp_path)
    calls = fake_service(monkeypatch, {URL: downloaded()})
    denied = replace(selection(), files=(replace(selection().files[0], url="https://other.example/code"),))
    replies = [fetch(denied), fetch(), read(), call("submit_sources", {})]
    config = DiscoveryConfiguration(historical_job(inputs))
    run = JournalDirectory(tmp_path / "run")
    archive_source_run(config, objects, run, replies)
    report = discover_sources(configuration=config, reader=Unavailable(), run=run)
    assert report["status"] == "completed" and len(report["sources"]) == 1
    assert [a["status"] for a in report["acquisitions"]] == ["rejected", "acquired"]
    assert calls == [URL]
    assert discover_sources(configuration=config, reader=Unavailable(), run=run) == report
    head = BlobRef.from_dict(report["head"])
    inventory = discovery_inventory(run.objects, head)
    inventory_ref = run.objects.put(inventory.to_bytes())
    view = selection_view(run.objects, SelectionInputs(inventory_ref, inputs.guidance))
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, view.closure)
    assert load_discovery(exported, head) == report
    assert selection_view(exported, SelectionInputs(inventory_ref, inputs.guidance)) == view
    # A hash-consistent rewritten summary cannot hide normal completion.
    events = load_chain(exported, head)
    forged = replace(events[-1], payload={**thaw_json(events[-1].payload), "status": "unfinished"})
    with pytest.raises(RecordError, match="outcome differs"):
        load_discovery(exported, exported.put(forged.to_bytes()))
