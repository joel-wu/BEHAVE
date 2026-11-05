"""Rollout-local files survive calls, not attempts or durable publication."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_attempts import Scripted, call, setup

from behave.agent.attempt import AttemptInterrupted
from behave.agent.commands import shell_commands
from behave.agent.local_workspace import LocalWorkspace
from behave.agent.model import ModelFailure, ModelReply
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task
from behave.isolation import IsolationResult, IsolationStatus
from behave.task_execution.capture import load_submission
from behave.task_execution.solver import run_solver


def test_solver_reuses_directory_and_archives_only_submitted_files(tmp_path, publication, monkeypatch):
    from behave.agent import commands

    journal, spec = setup(tmp_path)
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    roots, inodes = [], []
    intermediate = b"intermediate RTL revision"
    compiled = b"generated compiler output" * 4096
    final = b"module top; endmodule\n"

    def execute(policy):
        root = Path(next(m.source for m in policy.writable if m.target == "/workspace"))
        roots.append(root)
        if len(roots) == 1:
            (root / "rtl.sv").write_bytes(intermediate)
            (root / "build").mkdir()
            (root / "build/cache.o").write_bytes(compiled)
            (root / "behavior.py").write_text("class M: pass\n")
            (root / "tb.py").write_text("# agent testbench\n")
            (root / "not-submitted.py").write_text("unused helper\n")
        else:
            assert (root / "build/cache.o").read_bytes() == compiled
            (root / "rtl.sv").write_bytes(final)
        inodes.append((root.stat().st_ino, (root / "build/cache.o").stat().st_ino))
        # No workspace content is copied into the durable store between calls.
        for data in (intermediate, compiled, final):
            assert not (journal.objects.directory / BlobRef.of(data).sha256).exists()
        return IsolationResult(IsolationStatus.EXITED, policy.digest, "fixture", exit_code=0)

    monkeypatch.setattr(commands, "run", execute)
    monkeypatch.setattr(Workspace, "import_directory", lambda *_: pytest.fail("no per-command import"))
    selections = {
        "rtl": {"files": ["rtl.sv"], "entry_file": "rtl.sv", "entry_symbol": "top"},
        "behavior": {"files": ["behavior.py"], "entry_file": "behavior.py", "entry_symbol": "M"},
        "testbench": {"files": ["tb.py"], "entry_file": "tb.py", "entry_symbol": "test"},
    }
    runner = shell_commands()
    arguments = {"task": public, "spec": replace(spec, role="solver"), "journal": journal,
                 "command_tool": runner.tool(), "workspace_parent": tmp_path}
    outcome = run_solver(**arguments, backend=Scripted(
        call("run_command", {"command": "first"}),
        call("run_command", {"command": "second"}), call("submit", selections)))
    assert roots[0] == roots[1] and inodes[0] == inodes[1]
    assert not roots[0].exists()
    assert not roots[0].parent.exists()  # Includes the private /tmp sibling.
    frozen = load_submission(journal.objects, outcome.submission)
    assert journal.objects.read(frozen.rtl.files[0].content) == final
    for data in (intermediate, compiled, b"unused helper\n"):
        assert not (journal.objects.directory / BlobRef.of(data).sha256).exists()
    settled = [e for e in journal.load() if e.kind == "tool_settled"]
    assert [e.payload["after"] is None for e in settled] == [True, True, False]
    # Completed history and selected sources replay after local files are gone.
    assert run_solver(**arguments, backend=Scripted()) == outcome
    assert len(roots) == 2


@pytest.mark.parametrize("failure", [False, True])
def test_unsubmitted_files_are_discarded_on_stop_or_interruption(tmp_path, publication, failure):
    journal, spec = setup(tmp_path)
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    roots = []
    payload = b"temporary build content"

    def build(workspace, arguments):
        roots.append(workspace.directory)
        workspace.write("build.o", payload)
        workspace.temporary_directory.joinpath("tool.log").write_bytes(payload)
        return ToolResult({"built": True})

    command = Tool("run_command", "fixture", object_schema({}), build, "fixture")
    args = {"task": public, "spec": replace(spec, role="solver", max_retries=0),
            "journal": journal, "command_tool": command, "workspace_parent": tmp_path}
    backend = Scripted(call("run_command", {}), ModelFailure("permanent", "fixture") if failure else ModelReply("done"))
    if failure:
        with pytest.raises(AttemptInterrupted):
            run_solver(**args, backend=backend)
        with pytest.raises(AttemptInterrupted, match="local-workspace-unavailable"):
            run_solver(**args, backend=Scripted())
    else:
        assert run_solver(**args, backend=backend).status == "no_submission"
    assert not roots[0].exists()
    assert not roots[0].parent.exists()
    assert not (journal.objects.directory / BlobRef.of(payload).sha256).exists()


def test_local_source_aliases_and_file_listing_do_not_archive_builds(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    store = DirectoryStore(tmp_path / "objects")
    workspace = LocalWorkspace(store, root)
    root = workspace.directory
    workspace.seed(())
    workspace.write("src/model.py", b"class M: pass\n")
    (root / "model.py").symlink_to("src/model.py")
    (root / "absolute").symlink_to("/workspace/src", target_is_directory=True)
    (root / "outside").symlink_to("/etc/passwd")
    (root / "cycle").symlink_to("cycle")
    assert workspace.read("model.py") == workspace.read("absolute/model.py")
    (root / "remove-alias.py").symlink_to("src/model.py")
    workspace.delete("remove-alias.py")
    assert not (root / "remove-alias.py").is_symlink()
    assert workspace.read("src/model.py") == b"class M: pass\n"
    with pytest.raises(RecordError, match="parent path"):
        workspace.write("src/model.py/child", b"bad path")
    with pytest.raises(RecordError, match="no workspace file"):
        workspace.read("src/model.py/child")
    with pytest.raises(RecordError, match="requires a file"):
        workspace.delete("src")
    for path in ("outside", "cycle"):
        with pytest.raises(RecordError, match="workspace"):
            workspace.read(path)
    assert "model.py" in workspace.paths()
    assert not store.directory.exists() or not any(store.directory.iterdir())
    workspace.archive(("model.py", "missing.py"))
    assert [file.path for file in workspace.snapshot().files] == ["model.py"]


def test_trusted_workers_share_runtime_directory(tmp_path, publication, monkeypatch):
    from test_solver_process import setup as solver_setup

    from behave.agent.process_worker import supervise_worker
    from behave.isolation.service import ServiceResult

    runtime = tmp_path / "runtime"
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(runtime))
    environments = []

    def run_service(*args, **kwargs):
        environments.append(kwargs["environment"])
        return ServiceResult("exited", 0, b"", b"", 0.)

    monkeypatch.setattr("behave.isolation.service.run_service", run_service)
    for index in range(2):
        job, journal, _ = solver_setup(tmp_path / str(index), publication, "pass")
        supervise_worker(namespace="solver", module="behave.task_execution.solver",
                         attempt=job.attempt, model=job.model, journal=journal,
                         reference=journal.objects.put(b"fixture"), log_bytes=1024,
                         ingresses=(None,))
    assert [env["BEHAVE_RUNTIME_DIR"] for env in environments] == [str(runtime)] * 2
    assert runtime.is_dir()


def test_development_relay_uses_local_sources_and_keeps_only_feedback(tmp_path, publication, monkeypatch):
    import time

    from test_cocotb_development import ARGUMENTS, POLICY, run_development_fixture
    from test_judge import RTL

    from behave.agent.admission import AdmissionPolicy, ResourcePool
    from behave.agent.journal import AttemptJournal
    from behave.core.records import parse_json
    from behave.task_execution import development as development_module
    from behave.task_execution.cocotb_worker import CocotbRun
    from behave.task_execution.development import cocotb_tool
    from behave.task_execution.tb_report import TBObservation

    journal, spec = setup(tmp_path)
    spec = replace(spec, role="solver")
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    ledger = AttemptJournal(tmp_path / "development", journal.objects)
    pool = ResourcePool(AdmissionPolicy("eda", 1))
    relay = development_module.DevelopmentRelay(spec=spec, policy=POLICY, solver=journal,
        journal=ledger, pool=pool, deadline=time.monotonic() + 30)
    staging, calls = [], []
    draft = b"# intermediate testbench\n"
    final = "# final testbench\n"

    def execute(job, objects):
        staging.append(objects.directory.parent)
        calls.append(job)
        assert {file.reference.path for file in job.files} == {"top.sv", "tb.py", "model.py"}
        assert not (journal.objects.directory / BlobRef.of(draft).sha256).exists()
        console = objects.put(b"fixture assertion failed")
        return CocotbRun(TBObservation("failed", "tb-execution-failed", "failed", evidence=(console,)), console)

    monkeypatch.setattr(development_module, "execute_cocotb", execute)
    monkeypatch.setattr(development_module, "exchange", lambda endpoint, packet, **kw:
                        relay.respond(packet, cancelled=lambda: False))
    backend = Scripted(call("write_file", {"path": "top.sv", "content": RTL.decode()}),
        call("write_file", {"path": "tb.py", "content": draft.decode()}),
        call("write_file", {"path": "model.py", "content": "class M: pass\n"}),
        call("write_file", {"path": "build/cache.o", "content": "not selected"}),
        call("run_cocotb", ARGUMENTS), call("write_file", {"path": "tb.py", "content": final}),
        call("submit", {**ARGUMENTS, "behavior": {"files": ["model.py"], "entry_file": "model.py", "entry_symbol": "M"}}))
    outcome = run_development_fixture(task=public, spec=spec, journal=journal, backend=backend,
        development_tools=(cocotb_tool(POLICY, executor=development_module.CocotbRelayClient("fixture")),),
        workspace_parent=tmp_path)
    frozen = load_submission(journal.objects, outcome.submission)
    assert any(journal.objects.read(file.content) == final.encode() for file in frozen.testbench.files)
    assert len(calls) == 1 and not staging[0].exists() and pool.counts == (0, 0)
    for data in (draft, b"not selected"):
        assert not (journal.objects.directory / BlobRef.of(data).sha256).exists()
    history = [parse_json(journal.objects.read(BlobRef.from_dict(dict(event.payload["result"]))))
               for event in journal.load() if event.kind == "tool_settled"]
    observation = next(row for row in history if "comparison" in row)
    assert observation["comparison"] == "failed" and observation["console"] == "fixture assertion failed"
    assert "inputs" not in observation and "workspace" not in observation
    assert [event.kind for event in ledger.load()] == ["development_initialized"]
