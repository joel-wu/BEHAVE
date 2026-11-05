"""Power is a labeled ancillary estimate, never score or frontier authority."""

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from test_ppa import RTL, FakeNative, Scripted, experiment, file, fixture_task

from behave.agent.attempt import AttemptSpec
from behave.core.artifacts import BlobRef, LoadedSource, SourceArtifact
from behave.core.records import canonical_json
from behave.core.storage import DirectoryStore
from behave.ppa import power
from behave.ppa.config import Experiment, PowerPolicy
from behave.ppa.report import summary, target_results
from behave.ppa.workflow import eda_feedback, frontier, run_search

REPORT = "BEHAVE_POWER_BEGIN\nTotal 1.0e-6 2.0e-6 3.0e-9 3.003e-6 100.0%\nBEHAVE_POWER_END\n"


@pytest.mark.parametrize("name,value", [("input_activity", -1), ("input_activity", 2.01),
    ("input_activity", True), ("input_activity", float("nan")), ("input_duty", float("inf")),
    ("input_duty", -0.1), ("input_duty", 1.01), ("input_duty", False)])
def test_power_assumptions_are_finite_and_bounded(name, value):
    with pytest.raises(ValueError):
        PowerPolicy(**{name: value})


def test_power_policy_changes_the_frozen_experiment():
    exp = experiment()
    assert Experiment.from_dict(json.loads(canonical_json(exp.as_dict()))) == exp
    assert replace(exp, power=PowerPolicy(.2, .25)).identity != exp.identity
    old = exp.as_dict()
    old["schema"] = "behave.ppa-experiment/1"
    old.pop("power")
    with pytest.raises(ValueError):
        Experiment.from_dict(old)


def test_power_report_preserves_watts_and_explicit_assumptions():
    row = power.report(REPORT, PowerPolicy(), {"rst": 0})
    assert row["status"] == "estimated" and row["total_w"] == 3.003e-6
    assert row["leakage_w"] == 3e-9 and row["units"] == "W"
    assert row["assumptions"]["constant_inputs"] == {"rst": 0}
    assert row["assumptions"]["workload_activity"] is False
    zero = power.report(REPORT.replace("1.0e-6 2.0e-6 3.0e-9 3.003e-6", "0 0 0 0"), PowerPolicy(), {})
    assert zero["status"] == "estimated" and zero["total_w"] == 0


@pytest.mark.parametrize("output", ["", REPORT * 2, "Warning: missing power model\n" + REPORT,
    "Warning 160: missing activity\n" + REPORT, "Error 123: Tcl failure\n" + REPORT,
    "Error: Tcl failure\n" + REPORT, REPORT.replace("3.003e-6", "nan"),
    REPORT.replace("3.003e-6", "inf"), REPORT.replace("1.0e-6", "-1.0e-6"),
    REPORT.replace("3.003e-6", "4.0e-6"), REPORT.replace("BEHAVE_POWER_END", "truncated"),
    REPORT.replace("Total", "Sequential")])
def test_missing_bad_or_partial_power_is_not_zero(output):
    row = power.report(output, PowerPolicy(), {})
    assert row["status"] == "unavailable"
    assert all(row[k] is None for k in power.COMPONENTS)


def test_failed_power_tool_cannot_authorize_even_a_complete_report():
    assert power.report(REPORT, PowerPolicy(), {}, completed=False)["status"] == "unavailable"


def test_power_reset_is_inactive_without_changing_timing_constraints():
    from behave.ppa.eda import sta_script

    for name, idle in (("rst", 0), ("rst_n", 1)):
        script = power.commands(PowerPolicy(.2, .3), {name: idle})
        assert f"[get_ports {name}] -activity 0 -duty {idle}" in script
        assert "set_case_analysis" not in script
        assert "set_power_activity" not in sta_script("M", experiment().timing)
    with pytest.raises(ValueError):
        power.commands(PowerPolicy(), {"rst]; exec evil": 0})


def test_power_does_not_change_score_frontier_or_target_selection():
    row = {"candidate": "one", "score": 1, "mapped_score": 1,
           "eda": {"status": "measured", "area_um2": 10., "timing": {"status": "met"}},
           "performance": {"score": 1, "time_ns": 80.}}
    for value in (None, power.report("", PowerPolicy(), {}), power.report(REPORT, PowerPolicy(), {})):
        row["eda"]["power"] = value
        assert frontier([row]) == ["one"]
        selected = target_results([row], (100.,))[0]
        assert selected["candidate"] == "one" and selected["power"] == value
        assert row["score"] == 1


def test_power_reaches_feedback_archive_summary_and_cold_replay(tmp_path):
    value = power.report(REPORT, PowerPolicy(), {"rst": 0})

    class Native(FakeNative):
        def run(self, *args):
            row = super().run(*args)
            if "eda" in row:
                row["eda"]["power"] = value
            return row

    public, task, _ = fixture_task()
    kwargs = {"public": public, "task": task, "experiment": experiment(),
              "spec": AttemptSpec("power", "ppa", {"model": "fake"}), "backend": Scripted(),
              "native": Native(), "directory": tmp_path, "prompt": "fixture draft"}
    result = run_search(**kwargs)
    assert run_search(**kwargs) == result
    feedback, files = eda_feedback(result["candidates"][0]["eda"],
        DirectoryStore(tmp_path / "objects"), prefix="eda/measured")
    assert feedback["power"] == {key: value[key] for key in ("status", "total_w", "units", "reason")}
    assert "assumptions" not in repr(feedback)
    report_data = json.loads(files[0].data.decode().split("```json\n")[1].split("\n```")[0])
    assert report_data["power"] == value
    report = summary(tmp_path)
    assert report["points"][0]["power"] == value
    assert report["methods"]["search"]["targets"][0]["power"] is None
    from behave.ppa.finalize import compare_searches
    compare_searches(searches={"search": tmp_path}, task=task, experiment=experiment(),
                     native=Native(), directory=tmp_path / "final")
    assert summary(tmp_path / "final")["methods"]["search"]["targets"][0]["power"] == value


@pytest.mark.eda
@pytest.mark.isolation
def test_native_power_estimate_scales_with_frequency_not_leakage(tmp_path, pytestconfig):
    from behave.ppa.worker import NativePPA
    from behave.task_execution.comparison import ComparisonInputs

    root = Path(os.environ.get("BEHAVE_PPA_ROOT", Path(__file__).resolve().parents[2] / "external/ppa-pilot"))
    if not (root / "sky130.lib").is_file():
        pytest.skip("explicit SKY130 pilot installation required")
    _, task, _ = fixture_task()
    f = file("top.sv", RTL.replace(b"assign out_y = in_a;", b"assign out_y = in_a ^ (in_a >> 1);"))
    source = LoadedSource(SourceArtifact((f.reference,), "top.sv", "M"), (f,))
    inputs = ComparisonInputs("rtl", source, task.reference_behavior, task.contract, task.run_policy, task.judge_policy)
    native = NativePPA(liberty=root / "sky130.lib", sta=root / "opensta/usr/bin/sta")
    store = DirectoryStore(tmp_path / "objects")
    rows = []
    for period in (20., 40.):
        exp = replace(experiment(), timing=replace(experiment().timing, period_ns=period))
        row = native.run(inputs, exp, "eda", (), store)
        assert row.get("eda", {}).get("status") == "measured", row
        got = row["eda"]["power"]
        assert got["status"] == "estimated", row["eda"]["log"]
        assert got["total_w"] > 0 and got["leakage_w"] > 0
        assert got["conditions"]["timing"]["period_ns"] == period
        assert got["conditions"]["liberty_sha256"] == native.library_ref.sha256
        rows.append(row)
    fast, slow = (row["eda"]["power"] for row in rows)
    assert fast["internal_w"] + fast["switching_w"] == pytest.approx(
        2 * (slow["internal_w"] + slow["switching_w"]), rel=1e-4)
    assert fast["leakage_w"] == pytest.approx(slow["leakage_w"], rel=1e-5)
    assert rows[0]["eda"]["area_um2"] == rows[1]["eda"]["area_um2"]
    # Preserve both raw tool output/evidence and parsed numbers outside /tmp.
    path = pytestconfig.getoption("xmlpath")
    if path:
        evidence = Path(path).resolve().parent / "ppa-power"
        evidence.mkdir(parents=True)
        copied = DirectoryStore(evidence / "objects")
        for row in rows:
            copied.publish(store, tuple(BlobRef.from_dict(ref) for ref in row["evidence"]))
        (evidence / "frequency.json").write_bytes(canonical_json(rows))


@pytest.mark.eda
@pytest.mark.isolation
def test_native_power_tcl_failure_does_not_erase_area_or_timing(tmp_path, monkeypatch):
    from behave.ppa import eda

    root = Path(os.environ.get("BEHAVE_PPA_ROOT", Path(__file__).resolve().parents[2] / "external/ppa-pilot"))
    if not (root / "sky130.lib").is_file():
        pytest.skip("explicit SKY130 pilot installation required")
    f = file("top.sv", RTL)
    source = LoadedSource(SourceArtifact((f.reference,), "top.sv", "M"), (f,))
    monkeypatch.setattr(power, "commands", lambda *a: "nonexistent_power_command\nexit\n")
    row = eda.measure(source, experiment().timing, liberty=root / "sky130.lib", sta=root / "opensta/usr/bin/sta",
        directory=tmp_path / "eda", objects=DirectoryStore(tmp_path / "objects"),
        power_policy=PowerPolicy(), constant_inputs={"rst": 0}, budget=experiment().eda_budget)
    assert row["status"] == "measured" and row["timing"]["status"] == "met", row
    assert row["area_um2"] > 0
    assert row["power"]["status"] == "unavailable" and row["power"]["total_w"] is None
