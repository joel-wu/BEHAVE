"""Ordinary cocotb end-to-end fixtures; no dataset, model API, or training."""

import pytest
from test_judge import ScriptedJudge, run
from test_testbench_reports import inputs

from behave.core.artifacts import BlobRef, FileData
from behave.core.records import canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.task_execution.capture import load_submission
from behave.task_execution.cocotb_worker import CocotbBackend, CocotbJob, _stage, submitted_job
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.testbench import TBPolicy, TBRequest, observe_testbench

POLICY = TBPolicy(CocotbBackend.identity, CocotbBackend.collector, CocotbBackend.metric,
                  max_retries=0, timeout_seconds=240)
TB = b'''import cocotb
from cocotb.triggers import Timer
from model import M
from helpers.values import VALUES

@cocotb.test()
async def exercise(dut):
    assert open("vectors.txt").read().strip() == "0 1 127 255"
    model = M()
    model.reset()
    dut.rst.value = 0
    dut.in_valid.value = 1
    dut.out_ready.value = 1
    for a in VALUES:
        dut.in_a.value = a
        await Timer(10, unit="ns")
        assert int(dut.out_y.value) == model.process(a)["y"]
    CHECK

@cocotb.test()
async def unselected(dut):
    raise AssertionError("exact test selection did not apply")
'''


def submitted(tmp_path, check="assert True", **kwargs):
    task, public, _, score_journal, tb_journal = inputs(tmp_path, **kwargs)
    # Use the same capture path as the solver, including a package helper and
    # an ignored submitted Makefile. Never rewrite the user's test source.
    from behave.agent.workspace import Workspace
    from behave.task_execution.capture import submission_tool
    from behave.task_execution.submission import ArtifactSelection, SubmissionRequest

    workspace = Workspace(score_journal.objects)
    from test_judge import BEHAVIOR, RTL
    workspace.write("model.py", BEHAVIOR)
    top = RTL.replace(b"assign out_y = in_a;", b"identity_cell cell0(.a(in_a), .y(out_y));")
    workspace.write("top.sv", kwargs.get("rtl", top))
    workspace.write("rtl/cell.sv", b'''`include "width.svh"
module identity_cell(input [`W-1:0] a, output logic [`W-1:0] y);
always_comb begin
    if (a[7]) begin
        y = a;
    end else begin
        y = a;
    end
end
endmodule
''')
    workspace.write("rtl/width.svh", b"`define W 8\n")
    workspace.write("tests/tb.py", TB.replace(b"CHECK", check.encode()))
    workspace.write("tests/__init__.py", b"")
    workspace.write("helpers/__init__.py", b"")
    workspace.write("helpers/values.py", b"VALUES = (0, 1, 127, 255)\n")
    workspace.write("vectors.txt", b"0 1 127 255\n")
    workspace.write("Makefile", b"$(error a submitted Makefile must never be executed)\n")
    request = SubmissionRequest(
        ArtifactSelection(("model.py",), "model.py", "M"),
        ArtifactSelection(("top.sv", "rtl/cell.sv", "rtl/width.svh"), "top.sv", "M"),
        ArtifactSelection(("tests/tb.py", "tests/__init__.py", "helpers/__init__.py", "helpers/values.py", "vectors.txt", "Makefile"),
                          "tests/tb.py", "exercise"),
    )
    captured = submission_tool(public).handler(workspace, request.as_arguments())
    frozen = BlobRef.from_dict(dict(captured.content["submission"]))
    return task, public, frozen, score_journal, tb_journal


@pytest.mark.isolation
@pytest.mark.eda("verilator")
@pytest.mark.parametrize("check,status,comparison", [
    ("assert True", "completed", "passed"),
    ("assert False, 'intentional submitted assertion'", "failed", "failed"),
])
def test_real_cocotb_drives_submitted_behavior_and_rtl(tmp_path, check, status, comparison):
    task, public, submission, score_journal, journal = submitted(tmp_path, check)
    score = run(task, submission, score_journal, ScriptedJudge(
        ComponentJudgment(1, "fixture"), ComponentJudgment(1, "fixture")))
    before = score_journal.load()
    got = observe_testbench(task=public, submission=submission, policy=POLICY, journal=journal, backend=CocotbBackend())
    assert (got.observation.status, got.observation.comparison) == (status, comparison), got.as_dict()
    assert score.score == 1 and score_journal.load() == before
    assert got.observation.coverage is not None, got.as_dict()
    assert got.observation.coverage.assurance == "submitted-process-diagnostic"
    receipts = [parse_json(journal.objects.read(ref)) for ref in got.observation.evidence
                if journal.objects.read(ref).startswith(b'{"artifacts":')]
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["coverage_assurance"] == "submitted-process-diagnostic"
    assert "submitted-output/results.xml" in receipt["outputs"]
    xml = journal.objects.read(BlobRef.from_dict(receipt["outputs"]["submitted-output/results.xml"]))
    from xml.etree import ElementTree
    # Cocotb derives a distinct per-test seed from the configured base seed;
    # its regression XML retains the base, while cocotb.RANDOM_SEED changes.
    assert ElementTree.fromstring(xml).find(".//property[@name='random_seed']").attrib["value"] == "1"
    if status == "failed":
        assert b"intentional submitted assertion" in xml
        console = journal.objects.read(BlobRef.from_dict(receipt["outputs"]["console.txt"]))
        assert b"intentional submitted assertion" in console
    assert receipt["coverage_status"] == "measured"
    coverage = journal.objects.read(BlobRef.from_dict(receipt["outputs"]["submitted-output/coverage.info"]))
    assert b"SF:/work/submitted/rtl/cell.sv\n" in coverage
    lines = [row for row in coverage.decode().splitlines() if row.startswith("DA:")]
    assert got.observation.coverage.metric == "rtl-line-coverage"
    assert got.observation.coverage.total == len(lines) > 0
    assert got.observation.coverage.covered == sum(int(row.split(",")[1]) > 0 for row in lines) > 0
    raw = journal.objects.read(BlobRef.from_dict(receipt["outputs"]["submitted-output/coverage.dat"]))
    assert raw.startswith(b"# SystemC::Coverage-3")
    assert b"v_toggle" not in raw
    # Exactly one compilation, one simulation and one native report export.
    # The same prefix also names the separately retained stdout/stderr files.
    executions = [parse_json(journal.objects.read(BlobRef.from_dict(ref)))
                  for name, ref in receipt["outputs"].items()
                  if name.startswith("execution-") and name.endswith(".json")]
    assert [execution["role"] for execution in executions] == [
        "submitted-tb:compile", "submitted-tb:simulate", "submitted-tb:coverage"]
    assert observe_testbench(task=public, submission=submission, policy=POLICY,
                             journal=journal, backend=CocotbBackend()).as_dict() == got.as_dict()


@pytest.mark.isolation
@pytest.mark.eda("verilator")
def test_real_cocotb_compile_error_does_not_produce_a_pass(tmp_path):
    _, public, submission, _, journal = submitted(tmp_path, rtl=b"module M THIS IS NOT RTL;")
    got = observe_testbench(task=public, submission=submission, policy=POLICY, journal=journal, backend=CocotbBackend())
    assert got.observation.status == "failed", got.as_dict()
    assert got.observation.code == "rtl-tool-compile-failed"
    assert got.observation.comparison == "not_reported" and got.observation.coverage is None


def test_cocotb_packet_contains_only_public_and_submitted_bytes(tmp_path):
    _, public, submission, _, journal = submitted(tmp_path)
    from behave.core.artifacts import LoadedSource
    frozen = load_submission(journal.objects, submission)
    sources = [LoadedSource(component.artifact, tuple(
        FileData(file, journal.objects.read(file.content)) for file in component.files))
        for component in (frozen.behavior, frozen.rtl, frozen.testbench)]
    request = TBRequest(public, submission, *sources, POLICY, 0, tmp_path / "work")
    local = DirectoryStore(tmp_path / "worker-cas")
    job = submitted_job(request)
    packet = _stage(job, local)
    assert CocotbJob.from_bytes(packet, local) == job
    assert job.purpose == "submission" and job.origin == submission
    assert public.spec_path in {file.reference.path for file in job.files}
    assert public.contract_path not in {file.reference.path for file in job.files}
    assert "reference" not in packet.decode() and "golden" not in packet.decode()
    row = parse_json(packet)
    row["dispatch"] = True
    with pytest.raises(ValueError):
        CocotbJob.from_bytes(canonical_json(row), local)


@pytest.mark.parametrize("xml,expected", [
    (b"<testsuite><testcase name='x'/></testsuite>", "passed"),
    (b"<testsuite><testcase><failure/></testcase></testsuite>", "failed"),
    (b"<testsuite><testcase><error/></testcase></testsuite>", "failed"),
    (b"<testsuite><testcase><skipped/></testcase></testsuite>", "not_reported"),
    (b"<testsuite/>", "not_reported"), (b"broken", "not_reported"),
    (b'<!DOCTYPE x [<!ENTITY x "boom">]><x>&x;</x>', "not_reported"),
])
def test_junit_is_bounded_submitted_diagnostic(xml, expected):
    from behave.sim.cocotb import _reported_comparison
    assert _reported_comparison(xml) == expected
    assert _reported_comparison(xml * (1 << 20)) == "not_reported"


def test_cocotb_worker_timeout_is_unavailable_and_runtime_cache_is_shared(tmp_path, monkeypatch):
    from behave.isolation import service
    runtime = tmp_path / "shared-runtime"
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(runtime))
    _, public, submission, _, journal = submitted(tmp_path)
    def timeout(*args, **kwargs):
        import time
        assert kwargs["environment"]["TMPDIR"] == "/tmp"
        assert kwargs["environment"]["BEHAVE_RUNTIME_DIR"] == str(runtime)
        assert kwargs["environment"]["XDG_CACHE_HOME"] == str(kwargs["cwd"] / "cache")
        assert kwargs["environment"]["BEHAVE_WORK_DIR"] == str(kwargs["cwd"] / "sim-work")
        assert kwargs["cwd"].parent.as_posix() == "/tmp"
        assert POLICY.worker_timeout_seconds - 10 < kwargs["deadline"] - time.monotonic() <= POLICY.worker_timeout_seconds
        return service.ServiceResult("timeout", None, b"", b"", 1.)
    monkeypatch.setattr(service, "run_service", timeout)
    got = observe_testbench(task=public, submission=submission, policy=POLICY, journal=journal, backend=CocotbBackend())
    assert got.observation.status == "unavailable" and got.observation.code == "testbench-worker-timeout"
    assert got.observation.coverage is None


def test_nested_cocotb_budgets_are_pinned_and_do_not_race_the_worker():
    from dataclasses import replace

    policy = replace(POLICY, compile_timeout_seconds=90, timeout_seconds=3)
    assert policy.worker_timeout_seconds == 213
    assert TBPolicy.from_bytes(policy.to_bytes()) == policy
    assert parse_json(policy.to_bytes())["schema"] == "behave.testbench-policy/3"
    assert policy.to_bytes() != replace(policy, compile_timeout_seconds=91).to_bytes()
    for value in (0, True, -1, 1.5):
        with pytest.raises(ValueError):
            replace(policy, compile_timeout_seconds=value)


def test_cocotb_internal_packets_are_not_capped_by_worker_or_transport(tmp_path, monkeypatch):
    import sys

    from test_cocotb_development import workspace

    from behave.isolation import service
    from behave.task_execution import _worker_transport as transport
    from behave.task_execution import cocotb_worker as worker
    from behave.task_execution.cocotb_worker import CocotbRun
    from behave.task_execution.development import job_from_snapshot
    from behave.task_execution.tb_report import TBObservation

    work, args = workspace(tmp_path)
    origin, _ = work.checkpoint()
    job = job_from_snapshot(origin, args, POLICY, work.store)
    # Whitespace tests packet ownership without a large design or file bundle.
    packet = job.to_bytes() + b" " * ((1 << 20) + 1)
    monkeypatch.setattr(CocotbJob, "to_bytes", lambda self: packet)
    root = tmp_path / "worker"
    local = DirectoryStore(root / "objects")
    assert worker._stage(job, local) == packet
    reference = BlobRef.of(packet)
    monkeypatch.setattr(sys, "argv", ["worker", str(root), reference.sha256, str(reference.size_bytes)])
    result = CocotbRun(TBObservation("completed", "x" * ((1 << 20) + 1)))
    monkeypatch.setattr(worker, "_execute", lambda *args: result)
    worker.main()
    response = (root / "response.json").read_bytes()
    assert parse_json(response)["result"] == result.as_dict()
    assert transport.read_record(root / "response.json")["result"] == result.as_dict()

    calls = []
    def completed(*args, **kwargs):
        calls.append(args)
        return service.ServiceResult("exited", 0, b"", b"", .1)

    monkeypatch.setattr(service, "run_service", completed)
    got = transport.exchange("testbench", packet, workdir=root, objects=work.store,
                             deadline=0, timeout_seconds=POLICY.worker_timeout_seconds, log_bytes=POLICY.log_bytes)
    assert len(calls) == 1 and got.code is None and got.result == result.as_dict()


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_worker_failure_keeps_traceback_in_shared_stderr_not_comparison(tmp_path, monkeypatch, error_type):
    import io
    import sys
    from contextlib import redirect_stderr

    from test_cocotb_development import workspace

    from behave.isolation import service
    from behave.task_execution import cocotb_worker as worker
    from behave.task_execution.development import job_from_snapshot

    work, args = workspace(tmp_path)
    origin, _ = work.checkpoint()
    job = job_from_snapshot(origin, args, POLICY, work.store)
    calls = []

    def broken_sim_service(*args):
        calls.append(args)
        raise error_type("fixture service failure")

    def in_process_service(argv, **kwargs):
        # Exercise main and the real transport's stderr persistence without EDA.
        stream = io.StringIO()
        with monkeypatch.context() as patch, redirect_stderr(stream):
            patch.setattr(sys, "argv", ["worker", *argv[-3:]])
            worker.main()
        return service.ServiceResult("exited", 0, b"", stream.getvalue().encode(), 0.)

    monkeypatch.setattr(worker, "_execute", broken_sim_service)
    monkeypatch.setattr(service, "run_service", in_process_service)
    result = worker.run_cocotb(job, objects=work.store, workdir=tmp_path / "worker")
    observation = result.observation
    assert len(calls) == 1
    assert observation.status == "unavailable" and observation.code == "tb-service-failed"
    assert observation.comparison == "not_reported" and observation.coverage is None
    assert observation.retryable and observation.cleanup.quiescent
    receipts = [parse_json(work.store.read(ref)) for ref in observation.evidence
                if b'"schema":"behave.testbench-worker-receipt/4"' in work.store.read(ref)]
    assert len(receipts) == 1
    stderr = work.store.read(BlobRef.from_dict(receipts[0]["stderr"]))
    assert b"Traceback (most recent call last)" in stderr and b"broken_sim_service" in stderr
    assert (error_type.__name__ + ": fixture service failure").encode() in stderr
