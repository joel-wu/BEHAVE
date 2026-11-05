"""Campaign summaries reuse verified records without skipping archive checks."""

import json
from collections import Counter

import pytest
from test_ppa_pool_records import campaign_evidence

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.ppa import campaign_report, records, report


def collect(inputs):
    data = json.loads(inputs.read_text())
    return campaign_report.collect_final(data["plan"], inputs.parent, data["outcomes"], data["pool"])


def test_shared_costs_preserve_report_aliases_and_search_labels(tmp_path):
    inputs = campaign_evidence(tmp_path / "campaign")
    run, _, final = records.finalized_report(inputs.parent / "runs/method/r00/final")
    manifest = records.read_document(run.objects, BlobRef.from_dict(final["manifest"]))
    before = json.dumps([final, manifest], sort_keys=True)
    assert report._search_cost is records.search_cost
    cost = records.comparison_cost(run.objects, final, manifest)
    assert cost == report.summary(inputs.parent / "runs/method/r00/final")["cost"]
    assert json.dumps([final, manifest], sort_keys=True) == before
    aliases = {**manifest, "searches": {"one": manifest["searches"]["search"],
                                       "two": manifest["searches"]["search"]}}
    assert records.comparison_cost(run.objects, final, aliases)["searches"] == {
        "one": cost["searches"]["search"], "two": cost["searches"]["search"]}
    with pytest.raises(ValueError, match="unsupported completed PPA report"):
        records.comparison_cost(run.objects, {**final, "schema": "unknown"}, manifest)


def test_aggregation_reads_each_finished_archive_and_manifest_once(tmp_path, monkeypatch):
    inputs = campaign_evidence(tmp_path / "campaign")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    opened, documents, payloads = [], Counter(), Counter()
    original_final, original_document, original_read = records.finalized_report, records.read_document, DirectoryStore.read

    def final(directory):
        opened.append(directory)
        return original_final(directory)

    def document(reader, reference):
        documents[reader.directory, reference] += 1
        return original_document(reader, reference)

    def read(reader, reference):
        payloads[reader.directory, reference] += 1
        return original_read(reader, reference)

    monkeypatch.setattr(records, "finalized_report", final)
    monkeypatch.setattr(report, "finalized_report", final)
    monkeypatch.setattr(records, "read_document", document)
    monkeypatch.setattr(report, "read_document", document)
    monkeypatch.setattr(DirectoryStore, "read", read)
    result = collect(inputs)
    assert len(opened) == 2 and set(Counter(opened).values()) == {1}
    assert set(documents.values()) == {1}
    # This payload is not used in the plot or costs, but is still authenticated
    # by the complete workflow closure, once per independent archive.
    evidence = BlobRef.of(b"retained measurement evidence")
    assert [count for (_, ref), count in payloads.items() if ref == evidence] == [1, 1]
    for cost in result["costs"].values():
        assert cost["final_service_calls"] == 1
        search = cost["searches"]["search"]
        assert {k: v for k, v in search.items() if k != "result"} == {
            "reason": "final_action", "turns": 7, "search_service_calls": 2,
            "final_service_calls": 1, "search_service_seconds": 2, "final_service_seconds": 2.5,
            "billing": {"cost_by_currency": {"USD": "0.125"},
                        "unpriced_dispatches": 0, "unresolved_dispatches": 1}}
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("part", ["result", "manifest", "pool", "search", "evidence"])
def test_next_collection_revalidates_each_archive_and_all_retained_evidence(tmp_path, part):
    inputs = campaign_evidence(tmp_path / "campaign")
    directory = inputs.parent / "runs/method/r01/final"
    run, identity, result = records.finalized_report(directory)
    manifest_ref = BlobRef.from_dict(result["manifest"])
    manifest = records.read_document(run.objects, manifest_ref)
    refs = {"result": identity, "manifest": manifest_ref,
            "pool": BlobRef.from_dict(manifest["pool_ref"]),
            "search": BlobRef.from_dict(manifest["searches"]["search"]),
            "evidence": BlobRef.of(b"retained measurement evidence")}
    assert len(collect(inputs)["points"]) == 6
    path = run.objects.directory / refs[part].sha256
    data = path.read_bytes()
    path.chmod(0o600)  # Fault injection into this test's immutable object only.
    path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
    with pytest.raises(RecordError, match="content"):
        collect(inputs)
