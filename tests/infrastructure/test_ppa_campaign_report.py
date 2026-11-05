"""Read-only campaign reports and optional plotting without cluster dispatch."""

import json
import sys
from copy import deepcopy

import pytest

from behave.ppa import campaign_report as reporting


def test_controller_keeps_reporting_aliases():
    from test_ppa_campaign import campaign

    assert campaign.collect_final is reporting.collect_final
    assert campaign.plot is reporting.plot
    assert campaign.status is reporting.status


def test_status_cli_does_not_prepare_an_unstarted_campaign(tmp_path, monkeypatch, capsys):
    from behave.ppa import __main__ as cli

    def forbidden(*args, **kwargs):
        raise AssertionError("status must not prepare tasks or start native work")

    monkeypatch.setattr("behave.data.published.load_published_task", forbidden)
    monkeypatch.setattr("behave.ppa.worker.NativePPA", forbidden)
    missing = tmp_path / "not-launched"
    assert cli.main(["status", str(missing)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "directory": str(missing), "state": "not-created", "execution_visible": False}
    assert not missing.exists()


def test_status_keeps_per_attempt_read_errors_without_hiding_other_runs(tmp_path, monkeypatch):
    from behave.ppa.campaign_inputs import publish

    publish(tmp_path / "plan.json", {"task": "fixture", "methods": [{"name": "method"}], "replicates": 2})
    damaged = tmp_path / "runs/method/r00/recovery.json"
    damaged.parent.mkdir(parents=True)
    damaged.write_text("invalid json")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = reporting.status(tmp_path)
    task = report["tasks"][tmp_path.name]
    assert task["planned"] == 2 and task["read_errors"] == 1
    assert "read_error" in task["attempts"][0]
    assert task["attempts"][1]["label"] == "method/r01"
    assert not task["attempts"][1]["search_complete"]
    assert not (tmp_path / "runs/method/r01").exists()
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("entry", ["package", "historical"])
def test_plot_cli_only_reads_report(tmp_path, monkeypatch, entry):
    from test_ppa_campaign import campaign

    from behave.ppa import __main__ as cli

    report = empty_report()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    before = path.read_bytes()
    calls = []

    def forbidden(*args, **kwargs):
        raise AssertionError("plot must not load tasks, start native work or submit jobs")

    monkeypatch.setattr("behave.data.published.load_published_task", forbidden)
    monkeypatch.setattr("behave.ppa.worker.NativePPA", forbidden)
    monkeypatch.setattr(campaign, "service", forbidden)
    monkeypatch.setattr(campaign, "submit", forbidden)
    target = reporting if entry == "package" else campaign
    monkeypatch.setattr(target, "plot", lambda *args, **kwargs: calls.append((args, kwargs)))
    argv = ["plot", str(path), str(tmp_path / "frontier"), "--label", "method", "Model", "--title", "Fixture"]
    monkeypatch.setattr(sys, "argv", ["ppa", *argv])
    assert (cli if entry == "package" else campaign).main() == 0
    assert calls == [((report, tmp_path / "frontier"), {"labels": {"method": "Model"}, "title": "Fixture"})]
    assert path.read_bytes() == before


def final_fixture(tmp_path, monkeypatch, *, mismatch=None):
    from behave.agent.run_store import JournalDirectory
    from behave.ppa import records
    from behave.ppa.records import write_document
    from behave.sim import api

    experiment = empty_report()["experiment"]
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(experiment))
    plan = {**empty_report()["plan"], "replicates": 3, "experiment": "/unused/search-experiment",
            "final_experiment": str(path)}
    row = {"candidate": "rtl", "score": 1,
           "eda": {"status": "measured", "area_um2": 1000, "timing": {"status": "met"}},
           "performance": {"source": "rtl", "score": 1, "time_ns": 4000}}
    candidates = {"rtl": row,
                  "mapped": {**row, "candidate": "mapped", "mapped_score": 1,
                             "performance": {"score": 1, "time_ns": 4000}},
                  "rejected": {**row, "candidate": "rejected", "mapped_score": 0,
                               "performance": {"score": 1, "time_ns": 4000}}}
    finals = {}
    for replica in ("r00", "r01"):
        directory = tmp_path / "runs/method" / replica / "final"
        run = JournalDirectory(directory)
        changed = replica == "r01"
        pool = [api.Stimulus().as_dict()] if changed and mismatch == "pool" else []
        pool_ref, _ = write_document(run.objects, pool)
        manifest = {"pool_ref": pool_ref.as_dict(),
                    "experiment": {} if changed and mismatch == "experiment" else experiment,
                    "native": "changed-build" if changed and mismatch == "native" else "same-build",
                    "searches": {}}
        manifest_ref, _ = write_document(run.objects, manifest)
        finals[directory] = (run, None, {"schema": "behave.ppa-comparison/2",
            "manifest": manifest_ref.as_dict(), "candidates": candidates, "final_service_calls": 3})
    monkeypatch.setattr(records, "finalized_report", lambda directory: finals[directory])
    outcomes = [{"label": "method/" + replica, "ok": replica != "r02"} for replica in ("r00", "r01", "r02")]
    return plan, outcomes


def test_aggregation_keeps_replicates_failures_and_production_admission(tmp_path, monkeypatch):
    from behave.core.artifacts import BlobRef
    from behave.core.records import canonical_json

    plan, outcomes = final_fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    report = reporting.collect_final(plan, tmp_path, outcomes, ())
    assert report["schema"] == "behave.ppa-campaign-report/1"
    assert report["outcomes"] == outcomes
    assert report["experiment"] == empty_report()["experiment"]
    assert report["common_pool"] == BlobRef.of(canonical_json([])).as_dict()
    assert len(report["points"]) == 6
    assert set(report["frontiers"]["method"]) == {f"{r}:{c}" for r in ("r00", "r01") for c in ("rtl", "mapped")}
    assert set(report["costs"]) == {"method/r00", "method/r01"}
    assert all(cost == {"final_service_calls": 3, "searches": {}} for cost in report["costs"].values())
    for point in report["points"]:
        assert point["admitted"] == (point["candidate"] != "rejected")
        assert ("mapped_score" in point) == (point["candidate"] != "rtl")
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("mismatch, message", [
    ("pool", "correctness suite"), ("experiment", "measurement experiment"), ("native", "EDA/evaluator builds"),
])
def test_aggregation_refuses_incomparable_finals(tmp_path, monkeypatch, mismatch, message):
    plan, outcomes = final_fixture(tmp_path, monkeypatch, mismatch=mismatch)
    with pytest.raises(ValueError, match=message):
        reporting.collect_final(plan, tmp_path, outcomes, ())


def empty_report(mapping=None):
    experiment = {"timing": {"period_ns": 20}}
    if mapping is not None:
        experiment["mapping"] = mapping
    return {"plan": {"task": "fixture", "replicates": 1, "methods": [{"name": "method"}]},
            "experiment": experiment, "outcomes": [], "points": [], "frontiers": {"method": []}}


@pytest.mark.parametrize("mapping, label", [
    (None, "SKY130 HD"), ({"technology": "sky130hd"}, "SKY130 HD"),
    ({"technology": "nangate45"}, "Nangate45"), ({"technology": "future-library"}, "future-library"),
])
def test_plot_labels_recorded_technology(tmp_path, monkeypatch, mapping, label):
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots()
    monkeypatch.setattr(plt, "subplots", lambda **kwargs: (figure, axes))
    report = empty_report(mapping)
    before = deepcopy(report)
    reporting.plot(report, tmp_path / "frontier")
    assert f"{label} · 50 MHz" in axes.get_title()
    assert report == before


def test_plot_releases_figure_when_export_fails(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    before = set(plt.get_fignums())
    figure, axes = plt.subplots()
    monkeypatch.setattr(plt, "subplots", lambda **kwargs: (figure, axes))

    def failed(*args, **kwargs):
        raise OSError("fixture export failure")

    monkeypatch.setattr(figure, "savefig", failed)
    try:
        with pytest.raises(OSError, match="export failure"):
            reporting.plot(empty_report(), tmp_path / "frontier")
        assert set(plt.get_fignums()) == before
    finally:
        plt.close(figure)  # Keep the regression itself clean even on the old implementation.
