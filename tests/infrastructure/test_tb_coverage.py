"""Eval forwards Sim's line measurements; it does not interpret coverage files."""

import pytest

from behave.core.records import parse_json
from behave.sim.cocotb import CocotbResult
from behave.task_execution.tb_report import RTLCoverage


@pytest.mark.parametrize("counts", [(2, 3), (0, 3), (0, 0), None])
@pytest.mark.parametrize("status,comparison", [("completed", "passed"), ("failed", "failed")])
def test_sim_measurement_forwarded_without_recounting(tmp_path, monkeypatch, counts, status, comparison):
    from test_cocotb_backend import POLICY, submitted

    from behave.core.artifacts import FileData, LoadedSource
    from behave.sim import api
    from behave.task_execution.capture import load_submission
    from behave.task_execution.cocotb_worker import _execute, submitted_job
    from behave.task_execution.testbench import TBRequest

    _, public, submission, _, journal = submitted(tmp_path)
    frozen = load_submission(journal.objects, submission)
    sources = [LoadedSource(c.artifact, tuple(FileData(f, journal.objects.read(f.content)) for f in c.files))
               for c in (frozen.behavior, frozen.rtl, frozen.testbench)]
    job = submitted_job(TBRequest(public, submission, *sources, POLICY, 0, tmp_path / "work"))
    # The integration must use Sim's counters, not parse this report again.
    report = (("submitted-output/coverage.info", b"opaque Sim report"),) if counts is not None else ()
    monkeypatch.setattr(api, "run_cocotb_test", lambda *a, **kw: CocotbResult(
        status, "tb-result", comparison, report, counts))
    run = _execute(job, journal.objects, tmp_path / "execution")
    assert (run.observation.status, run.observation.comparison) == (status, comparison)
    coverage = run.observation.coverage
    if counts is None:
        assert coverage is None
    else:
        assert (coverage.covered, coverage.total) == counts
        assert coverage.metric == "rtl-line-coverage"
        assert coverage.assurance == "submitted-process-diagnostic"
        assert RTLCoverage.from_dict(coverage.as_dict()) == coverage
        assert journal.objects.read(coverage.evidence) == report[0][1]
        assert coverage.fraction == (counts[0] / counts[1] if counts[1] else None)
    receipt = parse_json(journal.objects.read(run.observation.evidence[0]))
    assert receipt["coverage_status"] == ("measured" if counts is not None else "missing")


def test_point_policy_is_not_silently_relabelled_as_lines(tmp_path):
    from dataclasses import replace

    from test_cocotb_backend import POLICY, submitted

    from behave.task_execution.cocotb_worker import CocotbBackend
    from behave.task_execution.testbench import observe_testbench

    _, public, submission, _, journal = submitted(tmp_path)
    policy = replace(POLICY, collector="verilator-coverage-points/2", metric="verilator-points")
    got = observe_testbench(task=public, submission=submission, policy=policy, journal=journal, backend=CocotbBackend())
    assert got.observation.status == "unavailable"
    assert got.observation.coverage is None
