"""Legacy construction tools through the shared agent loop, not ordinary Solver."""

from dataclasses import replace
from pathlib import Path
from shlex import quote
from tempfile import TemporaryDirectory

import pytest
from test_attempts import Scripted, call
from test_judge import BEHAVIOR, RTL

from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.local_workspace import LocalWorkspace
from behave.agent.loop import run_attempt
from behave.agent.model import ModelReply, ToolCall
from behave.agent.tools import ToolUnavailable, file_tools
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task
from behave.task_execution.capture import load_submission, submission_tool
from behave.task_execution.cocotb_worker import CocotbBackend, CocotbRun
from behave.task_execution.development import cocotb_tool
from behave.task_execution.solver import bind_solver_policy, solver_input_files, solver_outcome
from behave.task_execution.submission import ArtifactSelection, SubmissionRequest
from behave.task_execution.tb_report import RTLCoverage, TBObservation
from behave.task_execution.testbench import TBPolicy


def run_development_fixture(*, task, spec, backend, journal, development_tools, workspace_parent=None):
    """Exercise still-used construction tools without reintroducing them to Solver."""
    spec = bind_solver_policy(task, spec)
    with TemporaryDirectory(dir=workspace_parent) as directory:
        workspace = LocalWorkspace(journal.objects, Path(directory), read_only_paths=spec.read_only_paths)
        result = run_attempt(journal=journal, backend=backend, spec=spec,
            initial_messages=({"role": "user", "content": "fixture development"},),
            initial_files=solver_input_files(task), workspace=workspace,
            tools=(*file_tools(), *development_tools, submission_tool(task)))
    return solver_outcome(task, journal, result)

POLICY = TBPolicy(CocotbBackend.identity, CocotbBackend.collector, CocotbBackend.metric,
                  max_retries=0, timeout_seconds=240, log_bytes=64 << 10)
RTL_SELECTION = ArtifactSelection(("top.sv",), "top.sv", "M")
TB_SELECTION = ArtifactSelection(("tb.py", "model.py"), "tb.py", "check_identity")
ARGUMENTS = {"rtl": RTL_SELECTION.as_dict(), "testbench": TB_SELECTION.as_dict()}
TB = '''import os
from pathlib import Path
import cocotb
from cocotb.triggers import Timer
from model import M

@cocotb.test()
async def check_identity(dut):
    assert os.environ.get("BEHAVE_DEVELOPMENT_SECRET") is None
    assert not Path(PRIVATE_ROOT).exists()
    assert not Path("unselected.txt").exists()
    Path("scratch-marker").write_text("not a workspace edit")
    model = M()
    model.reset()
    dut.rst.value = 0
    dut.in_valid.value = 1
    dut.out_ready.value = 1
    dut.in_a.value = 7
    await Timer(10, unit="ns")
    assert int(dut.out_y.value) == model.process(7)["y"], "expected identity"
'''


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize("shell_edits", [False, True])
def test_real_cocotb_agent_repairs_then_submits_and_cold_replay_does_not_run_again(publication, tmp_path, monkeypatch, shell_edits):
    from behave.agent.commands import shell_commands
    from behave.task_execution import development as development_module

    executed = []
    real_run = development_module.run_cocotb

    def record_run(job, **kwargs):
        executed.append(job)
        return real_run(job, **kwargs)

    monkeypatch.setattr(development_module, "run_cocotb", record_run)
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "cas"))
    spec = AttemptSpec("cocotb-edit-test-submit", "solver", {"model": "scripted"})
    tool = cocotb_tool(POLICY)
    monkeypatch.setenv("BEHAVE_DEVELOPMENT_SECRET", "not-visible-to-cocotb")
    wrong = RTL.replace(b"assign out_y = in_a;", b"assign out_y = in_a + 1'b1;")
    assert wrong != RTL
    initial = {"model.py": BEHAVIOR.decode(), "top.sv": wrong.decode(),
               "tb.py": TB.replace("PRIVATE_ROOT", repr(str(publication.root))), "unselected.txt": "not selected"}
    submission = SubmissionRequest(ArtifactSelection(("model.py",), "model.py", "M"), RTL_SELECTION, TB_SELECTION)
    tools = (tool, shell_commands().tool()) if shell_edits else (tool,)

    def edit(files):
        if shell_edits:
            code = f"from pathlib import Path; files = {files!r}; [Path(p).write_text(c) for p, c in files.items()]"
            return call("run_command", {"command": "python -c " + quote(code)})
        return ModelReply("", tuple(ToolCall(name, "write_file", {"path": name, "content": content})
                                   for name, content in files.items()))

    class Broker(Scripted):
        def generate(self, request):
            assert "not-visible-to-cocotb" not in repr(request)
            assert "PRIVATE_BEHAVIOR" not in repr(request)
            if len(self.requests) in (2, 4):
                result = request.messages[-2]["content"]
                expected = "failed" if len(self.requests) == 2 else "completed"
                assert result["status"] == expected, result
                assert "score" not in result and "reward" not in result
                assert result["coverage"]["metric"] == "rtl-line-coverage"
                assert 0 <= result["coverage"]["covered"] <= result["coverage"]["total"]
                if expected == "failed":
                    assert "expected identity" in result["console"]
            return super().generate(request)

    backend = Broker(edit(initial),
                     call("run_cocotb", ARGUMENTS),
                     edit({"top.sv": RTL.decode()}),
                     call("run_cocotb", ARGUMENTS), call("submit", submission.as_arguments()))
    result = run_development_fixture(task=public, spec=spec, backend=backend, journal=journal, development_tools=tools)
    assert result.status == "awaiting_judgment" and result.score is None
    assert result.attempt.turns == len(backend.requests) == 5
    frozen = load_submission(journal.objects, result.submission)
    assert journal.objects.read(frozen.rtl.files[0].content) == RTL
    workspace = Workspace(journal.objects)
    workspace.restore(frozen.workspace)
    assert "scratch-marker" not in {file.path for file in workspace.snapshot().files}
    requests = [event for event in journal.load() if event.kind == "tool_requested"
                and event.payload["call"]["name"] == "run_cocotb"]
    assert len(requests) == 2
    checks = [event for event in journal.load() if event.kind == "tool_settled" and event.payload["turn"] in (1, 3)]
    origins = []
    assert len(executed) == 2
    for request, settled, job in zip(requests, checks, executed):
        assert request.payload["before"] is None and settled.payload["after"] is None
        content = parse_json(journal.objects.read(BlobRef.from_dict(thaw_json(settled.payload["result"]))))
        assert "inputs" not in content and "workspace" not in content
        assert job.purpose == "development" and job.dispatch == 0
        assert {file.reference.path for file in job.files} == {"top.sv", "tb.py", "model.py"}
        origins.append(job.origin)
    assert origins[0] != origins[1]
    assert not (journal.objects.directory / BlobRef.of(wrong).sha256).exists()
    assert not any(event.kind.startswith(("judge_", "tb_")) for event in journal.load())

    def forbidden(*args, **kwargs):
        pytest.fail("committed development checks must not rerun")
    monkeypatch.setattr("behave.task_execution.development.run_cocotb", forbidden)
    assert run_development_fixture(task=public, spec=spec, backend=Scripted(), journal=journal,
                      development_tools=tools) == result


def workspace(tmp_path):
    work = Workspace(DirectoryStore(tmp_path / "objects"))
    work.write("top.sv", RTL)
    work.write("tb.py", b"standalone TB; no Behavior import")
    work.write("unselected.txt", b"not visible")
    args = {"rtl": RTL_SELECTION.as_dict(),
            "testbench": ArtifactSelection(("tb.py",), "tb.py", "test").as_dict()}
    return work, args


def test_development_executes_once_without_behavior_or_submission_and_preserves_sources(tmp_path, monkeypatch):
    work, args = workspace(tmp_path)
    before = work.snapshot()
    jobs, paths = [], []
    def run(job, *, objects, workdir):
        jobs.append(job)
        paths.append(workdir)
        assert workdir.parent == Path("/tmp") and workdir.is_dir()
        assert {file.reference.path for file in job.files} == {"top.sv", "tb.py"}
        console = objects.put(b"assertion failed\n")
        return CocotbRun(TBObservation("failed", "tb-execution-failed", "failed", evidence=(console,)), console)
    monkeypatch.setattr("behave.task_execution.development.run_cocotb", run)
    tool = cocotb_tool(POLICY)
    got = tool.handler(work, args)
    assert got.content["status"] == "failed" and got.content["console"] == "assertion failed\n"
    assert not got.terminal and not tool.terminal and work.snapshot() == before
    assert len(jobs) == 1 and not paths[0].exists()
    assert "score" not in got.content and "submission" not in got.content
    work.write("tb.py", b"a corrected test")
    tool.handler(work, args)
    assert jobs[0].origin != jobs[1].origin and jobs[0].testbench != jobs[1].testbench


@pytest.mark.parametrize("status,comparison", [("completed", "passed"), ("failed", "failed")])
@pytest.mark.parametrize("counts", [None, (0, 0), (0, 6), (5, 6)])
def test_development_forwards_existing_line_coverage_without_rerunning(tmp_path, status, comparison, counts):
    work, args = workspace(tmp_path)
    before = work.snapshot()
    measured, calls = [], []

    def execute(job, objects):
        calls.append(job)
        coverage = None
        if counts is not None:
            rtl = objects.put(b"selected RTL bundle")
            report = objects.put(b"Verilator LCOV report")
            coverage = RTLCoverage(rtl, POLICY.collector, POLICY.metric, *counts, report)
        measured.append(coverage)
        return CocotbRun(TBObservation(status, "tb-result", comparison, coverage=coverage))

    got = cocotb_tool(POLICY, executor=execute).handler(work, args)
    assert len(calls) == 1 and work.snapshot() == before
    assert got.content["status"] == status and got.content["comparison"] == comparison
    if counts is None:
        assert got.content["coverage"] is None
    else:
        assert thaw_json(got.content["coverage"]) == measured[0].as_dict()
        assert measured[0].evidence in got.artifacts and measured[0].rtl in got.artifacts
    assert "score" not in got.content and "reward" not in got.content


def test_missing_dependency_and_policy_mismatch_do_not_execute(tmp_path, monkeypatch):
    work, args = workspace(tmp_path)
    work.delete("tb.py")
    def forbidden(*args, **kwargs):
        pytest.fail("invalid input must not reach execution")
    monkeypatch.setattr("behave.task_execution.development.run_cocotb", forbidden)
    with pytest.raises(RecordError) as error:
        cocotb_tool(POLICY).handler(work, args)
    assert error.value.code == "workspace-file-missing"
    for policy in (replace(POLICY, max_retries=1), replace(POLICY, backend="other"), replace(POLICY, metric="other")):
        with pytest.raises(ValueError):
            cocotb_tool(policy)
    assert cocotb_tool(POLICY).revision != cocotb_tool(replace(POLICY, seed=2)).revision


def test_worker_unavailability_retains_evidence_and_cannot_turn_into_no_submission_zero(publication, tmp_path, monkeypatch):
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "cas"))
    spec = AttemptSpec("failed-worker", "solver", {"model": "scripted"})
    calls, evidence = [], []
    def unavailable(job, *, objects, workdir):
        calls.append(job)
        ref = objects.put(b"worker unavailable receipt")
        evidence.append(ref)
        return CocotbRun(TBObservation("unavailable", "testbench-worker-timeout", retryable=True, evidence=(ref,)))
    monkeypatch.setattr("behave.task_execution.development.run_cocotb", unavailable)
    args = {"rtl": RTL_SELECTION.as_dict(), "testbench": ArtifactSelection(("tb.py",), "tb.py", "test").as_dict()}
    backend = Scripted(call("write_file", {"path": "top.sv", "content": RTL.decode()}),
                       call("write_file", {"path": "tb.py", "content": "TB"}), call("run_cocotb", args))
    for model in (backend, Scripted()):
        with pytest.raises(AttemptInterrupted, match="testbench-worker-timeout"):
            run_development_fixture(task=public, spec=spec, backend=model, journal=journal, development_tools=(cocotb_tool(POLICY),))
    assert len(calls) == 1 and journal.load()[-1].kind == "tool_settled"
    assert evidence[0] in journal.load()[-1].artifacts


def test_unsupported_entry_is_repairable_feedback_not_infrastructure(tmp_path, monkeypatch):
    work, args = workspace(tmp_path)
    monkeypatch.setattr("behave.task_execution.development.run_cocotb", lambda *args, **kwargs:
                        CocotbRun(TBObservation("failed", "tb-module-path-unsupported")))
    assert cocotb_tool(POLICY).handler(work, args).content["code"] == "tb-module-path-unsupported"
    with pytest.raises(ValueError):
        CocotbRun(TBObservation("failed", "test"), BlobRef.of(b"unbound console"))
    with pytest.raises(TypeError):
        ToolUnavailable("error", artifacts=("not a reference",))


@pytest.mark.parametrize("code", ["tb-module-path-unsupported", "a-new-sim-code"])
def test_development_uses_sim_status_not_an_error_code_table(tmp_path, monkeypatch, code):
    work, args = workspace(tmp_path)
    monkeypatch.setattr("behave.task_execution.development.run_cocotb", lambda *a, **kw:
                        CocotbRun(TBObservation("failed", code)))
    assert cocotb_tool(POLICY).handler(work, args).content["status"] == "failed"
    monkeypatch.setattr("behave.task_execution.development.run_cocotb", lambda *a, **kw:
                        CocotbRun(TBObservation("unavailable", code)))
    with pytest.raises(ToolUnavailable, match=code):
        cocotb_tool(POLICY).handler(work, args)


@pytest.mark.parametrize("field,value,code", [
    ("rtl_symbol", "invalid top", "tb-top-unsupported"),
    ("tb_file", "test-bench.py", "tb-module-path-unsupported"),
    ("tb_symbol", "test.func", "tb-test-selector-unsupported"),
    ("rtl_file", "rtl dir/top.sv", "tb-rtl-path-unsupported"),
    ("rtl_file", "top.txt", "tb-rtl-language-unsupported"),
])
def test_public_sim_entry_failures_reach_development_without_starting_eda(tmp_path, monkeypatch, field, value, code):
    from behave.sim.rtl import tool_isolation
    from behave.task_execution.cocotb_worker import _execute

    entries = {"rtl_file": "top.sv", "rtl_symbol": "M", "tb_file": "tb.py", "tb_symbol": "test"}
    entries[field] = value
    work = Workspace(DirectoryStore(tmp_path / "objects"))
    work.write(entries["rtl_file"], RTL)
    work.write(entries["tb_file"], b"# entry validation must not execute this test")
    args = {"rtl": ArtifactSelection((entries["rtl_file"],), entries["rtl_file"], entries["rtl_symbol"]).as_dict(),
            "testbench": ArtifactSelection((entries["tb_file"],), entries["tb_file"], entries["tb_symbol"]).as_dict()}

    def forbidden(*a, **kw):
        pytest.fail("invalid selection must not launch EDA")

    monkeypatch.setattr(tool_isolation, "run_verilator_workspace", forbidden)
    tool = cocotb_tool(POLICY, executor=lambda job, store: _execute(job, store, tmp_path / "execution"))
    result = tool.handler(work, args)
    assert result.content["status"] == "failed" and result.content["code"] == code
    assert result.content["comparison"] == "not_reported"
    assert not (tmp_path / "execution").exists() and not result.terminal


@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize("body,code", [
    ("while True: pass", "tb-timeout"),
    # Output beyond the display budget must not kill the test: it reaches its
    # actual exception. Keep the producer finite; the loop above tests timeout.
    ("for _ in range(8): os.write(1, b'x' * 65536)\n    raise RuntimeError('after output')",
     "tb-execution-failed"),
    ("os._exit(0)", "tb-no-test-result"),
    ("subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True); os._exit(0)",
     "tb-timeout"),
    ("raise RuntimeError('broken candidate test')", "tb-execution-failed"),
])
def test_broken_cocotb_is_bounded_feedback_and_agent_can_continue(publication, tmp_path, body, code):
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    journal = AttemptJournal(tmp_path / "events", DirectoryStore(tmp_path / "cas"))
    spec = AttemptSpec("bad-tb", "solver", {"model": "scripted"})
    # Deadline probes stay short. The other cases test distinct exit evidence,
    # and must leave room for cocotb startup/traceback/XML shutdown under load.
    seconds = 3 if code == "tb-timeout" else 10
    policy = replace(POLICY, compile_timeout_seconds=90, timeout_seconds=seconds)
    args = {"rtl": RTL_SELECTION.as_dict(),
            "testbench": ArtifactSelection(("tb.py",), "tb.py", "test").as_dict()}
    tb = "import os, subprocess, sys\nimport cocotb\n@cocotb.test()\nasync def test(dut):\n    " + body + "\n"

    backend = Scripted(ModelReply("", (
        ToolCall("rtl", "write_file", {"path": "top.sv", "content": RTL.decode()}),
        ToolCall("tb", "write_file", {"path": "tb.py", "content": tb}),
    )), call("run_cocotb", args), call("write_file", {"path": "continue.txt", "content": "can repair"}),
        call("read_file", {"path": "continue.txt"}),
        ModelReply("stop without submitting"))
    result = run_development_fixture(task=public, spec=spec, backend=backend, journal=journal,
                        development_tools=(cocotb_tool(policy),))
    assert result.attempt.turns == len(backend.requests) == 5
    # Assert outside generate(): the model boundary deliberately sanitizes
    # backend exceptions and would hide the actual tool feedback on failure.
    feedback = backend.requests[2].messages[-2]["content"]
    assert (feedback["status"], feedback["code"]) == ("failed", code), feedback
    assert len(feedback["console"].encode()) <= policy.log_bytes
    assert len(feedback["console"].encode()) <= policy.log_bytes
    workspace = Workspace(journal.objects)
    workspace.restore(result.attempt.workspace)
    assert backend.requests[4].messages[-2]["content"]["content"] == "can repair"
    assert workspace.snapshot().file("continue.txt") is None
    assert not (journal.objects.directory / BlobRef.of(b"can repair").sha256).exists()
    assert result.status == "no_submission" and result.score == 0  # No submission, not a TB-derived score.
    assert run_development_fixture(task=public, spec=spec, backend=Scripted(), journal=journal,
                      development_tools=(cocotb_tool(policy),)) == result
