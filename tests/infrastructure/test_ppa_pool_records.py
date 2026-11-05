"""Pool identity and campaign evidence do not belong to the search executor."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from behave.ppa import records
from behave.sim import api


def test_pool_union_retains_semantics_order_and_last_record():
    first = api.Stimulus(source="first search").as_dict()
    later = api.Stimulus(source="later search").as_dict()
    distinct = api.Stimulus(initial_state={"count": 1}).as_dict()
    before = json.dumps([first, later, distinct], sort_keys=True)
    result = records.union_pool((distinct, first), (row for row in (later, distinct)))
    assert len(result) == 2 and any(row is later for row in result)
    fingerprints = [api.Stimulus.from_dict(row).fingerprint for row in result]
    assert fingerprints == sorted(set(fingerprints))
    assert json.dumps([first, later, distinct], sort_keys=True) == before
    assert records.union_pool() == ()


@pytest.mark.parametrize("malformed", [{"unexpected": 1}, {"source": 1}, {"slots": "not-a-list"}])
def test_pool_union_does_not_silently_drop_invalid_stimuli(malformed):
    with pytest.raises((TypeError, ValueError)):
        records.union_pool((api.Stimulus().as_dict(), malformed))


def campaign_evidence(root):
    from behave.agent.run_store import JournalDirectory

    root.mkdir()
    experiment = {"stage": "rtl", "targets_ns": [100]}
    experiment_file = root / "experiment.json"
    experiment_file.write_text(json.dumps(experiment))
    plan = {"experiment": str(experiment_file), "methods": [{"name": "method"}], "replicates": 3}
    pool = [api.Stimulus(source="retained suite").as_dict()]
    candidate = {"candidate": "rtl", "score": 1, "mapped_score": 0,
        "eda": {"status": "measured", "area_um2": 10, "timing": {"status": "met"}},
        "performance": {"source": "rtl", "score": 1, "time_ns": 80}}
    candidates = {"rtl": candidate,
        "mapped": {**candidate, "candidate": "mapped", "mapped_score": 1,
                   "performance": {"score": 1, "time_ns": 80}},
        "rejected": {**candidate, "candidate": "rejected", "mapped_score": 0,
                     "performance": {"score": 1, "time_ns": 80}}}
    for replica in ("r00", "r01"):
        run = JournalDirectory(root / "runs/method" / replica / "final")
        search, search_refs = records.write_document(run.objects, {
            "schema": "behave.ppa-search/2", "reason": "final_action", "turns": 7,
            "measurement_requests": 2,
            "billing": {"cost_by_currency": {"USD": "0.125"},
                        "unpriced_dispatches": 0, "unresolved_dispatches": 1},
            "candidates": [{"history": [{"final": False, "elapsed_seconds": 1.25},
                                        {"final": True, "elapsed_seconds": 2.5},
                                        {"final": False, "elapsed_seconds": .75}]}]})
        evidence = run.objects.put(b"retained measurement evidence")
        suite, suite_refs = records.write_document(run.objects, pool)
        manifest, manifest_refs = records.write_document(run.objects, {
            "experiment": experiment, "pool_ref": suite.as_dict(), "native": "frozen-build",
            "searches": {"search": search.as_dict()}})
        result, result_refs = records.write_document(run.objects, {
            "schema": "behave.ppa-comparison/2", "manifest": manifest.as_dict(), "candidates": candidates,
            "members": {"method": list(candidates)}, "final_service_calls": 1})
        run.journal("workflow").append((), "finished", {"result": result.as_dict()},
                                       (*suite_refs, *manifest_refs, *result_refs, *search_refs, evidence))
    outcomes = [{"label": f"method/{replica}", "ok": replica != "r02"} for replica in ("r00", "r01", "r02")]
    inputs = root / "aggregation.json"
    inputs.write_text(json.dumps({"plan": plan, "outcomes": outcomes, "pool": pool}))
    return inputs


AGGREGATION_PROBE = r'''
import json, sys
from pathlib import Path
from behave.ppa.campaign_report import collect_final
from behave.ppa import records, report as reporting
from collections import Counter
opened = []
original = records.finalized_report
def tracked(directory):
    opened.append(str(directory))
    return original(directory)
records.finalized_report = reporting.finalized_report = tracked
inputs = Path(sys.argv[1])
data = json.loads(inputs.read_text())
report = collect_final(data['plan'], inputs.parent, data['outcomes'], data['pool'])
assert report['outcomes'] == data['outcomes']
assert set(report['frontiers']['method']) == {f'{r}:{c}' for r in ('r00', 'r01') for c in ('rtl', 'mapped')}
assert len(report['points']) == 6
assert all(p['admitted'] == (p['candidate'] != 'rejected') for p in report['points'])
assert set(report['costs']) == {'method/r00', 'method/r01'}
assert all(cost['final_service_calls'] == 1 for cost in report['costs'].values())
assert len(opened) == 2 and set(Counter(opened).values()) == {1}
assert all(cost['searches']['search']['search_service_seconds'] == 2
           and cost['searches']['search']['final_service_seconds'] == 2.5
           and cost['searches']['search']['billing']['unresolved_dispatches'] == 1
           for cost in report['costs'].values())
'''


def test_campaign_aggregation_reads_real_archives(tmp_path):
    inputs = campaign_evidence(tmp_path / "campaign")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    source = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run([sys.executable, "-I", "-B", "-c",
        f"import sys; sys.path.insert(0, {str(source)!r})\n" + AGGREGATION_PROBE, str(inputs)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
