"""Public task -> supervised solver -> immutable submission, without a judge."""

import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from behave.agent import AttemptInterrupted, AttemptJournal, AttemptSpec
from behave.agent.process_model import ModelCommand
from behave.agent.trajectory import trajectory
from behave.core import runtime_defaults as defaults
from behave.core.records import RecordError, parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task
from behave.isolation.model import ResourceLimits
from behave.task_execution.capture import load_submission
from behave.task_execution.solver import SolverJob, run_solver_process

ROOT = str(Path(sys.modules["behave"].__file__).resolve().parent.parent)
BROKER = r'''
import sys
from behave.agent.model import ModelReply, ModelRequest, ToolCall, Charge
from behave.agent.process_model import encode_model_outcome
from behave.core.records import parse_json, canonical_json
request = ModelRequest.from_dict(parse_json(sys.stdin.buffer.read()))
assert b'PRIVATE_' not in canonical_json(request.as_dict())
'''


def setup(tmp_path, publication, program, *, shell=True):
    public = load_published_task(publication.reader, publication.ref, "fixture_counter").public
    cmd = ModelCommand((sys.executable, "-I", "-B", "-c",
                        "import sys;sys.path.insert(0,sys.argv[1]);exec(sys.argv[2])", ROOT, BROKER + program),
                       "solver-fixture/1")
    spec = AttemptSpec("solver-a", "solver", {"model": "fixture", "transport": cmd.identity},
                       request_timeout_seconds=10)
    job = SolverJob(public, spec, cmd,
                    ResourceLimits(wall_seconds=60, processes=defaults.SHELL_PROCESSES) if shell else None)
    objects = DirectoryStore(tmp_path / "objects")
    return job, AttemptJournal(tmp_path / "events", objects), AttemptJournal(tmp_path / "supervision", objects)


@pytest.mark.isolation
def test_public_solver_edits_and_submits_under_outer_supervision(tmp_path, publication, monkeypatch):
    program = r'''
if request.turn == 0:
    calls = (ToolCall('b','run_command',{'command':"printf 'class M: pass\\n' > agent.py"}),
             ToolCall('r','run_command',{'command':"printf 'module top; endmodule\\n' > top.sv"}))
else:
    calls = (ToolCall('s','submit',{
        'behavior': {'files':['agent.py'], 'entry_file':'agent.py', 'entry_symbol':'M'},
        'rtl': {'files':['top.sv'], 'entry_file':'top.sv', 'entry_symbol':'top'},
        'testbench': None,
    }),)
sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('', calls, charge=Charge('0.01','USD','fixture'))))
'''
    job, journal, supervisor = setup(tmp_path, publication, program)
    outcome = run_solver_process(job, journal=journal, supervision=supervisor)
    assert outcome.status == "awaiting_judgment" and outcome.score is None
    submission = load_submission(journal.objects, outcome.submission)
    assert submission.behavior.artifact.entry_symbol == "M"
    assert trajectory(journal).cost_by_currency() == {"USD": "0.02"}
    # The fixture broker's own source contains the word PRIVATE_ in its guard;
    # check actual private payloads, not that self-referential marker.
    assert all(secret not in job.to_bytes() for secret in (
        b"PRIVATE_BEHAVIOR_MUST_NOT_EXECUTE", b"PRIVATE_HELPER = 7",
        b"PRIVATE_REFERENCE_RTL", b"PRIVATE_RUN_POLICY", b"PRIVATE_JUDGE_POLICY",
    ))
    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("completed attempt must not relaunch"))
    assert run_solver_process(job, journal=journal, supervision=supervisor) == outcome
    with pytest.raises(RecordError, match="replay differs"):
        run_solver_process(replace(job, log_bytes=1000), journal=journal, supervision=supervisor)


@pytest.mark.isolation
def test_normal_no_submission_is_zero_but_cancel_is_not(tmp_path, publication):
    job, journal, supervisor = setup(tmp_path, publication,
                                    "sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('done')))")
    outcome = run_solver_process(job, journal=journal, supervision=supervisor)
    assert outcome.status == "no_submission" and outcome.score == 0
    other_journal = AttemptJournal(tmp_path / "other-events", journal.objects)
    other_supervisor = AttemptJournal(tmp_path / "other-supervision", journal.objects)
    with pytest.raises(AttemptInterrupted, match="solver-worker-cancelled"):
        run_solver_process(job, journal=other_journal, supervision=other_supervisor, cancelled=lambda: True)
    assert not other_journal.load()
    with pytest.raises(AttemptInterrupted, match="solver-worker-cancelled"):
        run_solver_process(job, journal=other_journal, supervision=other_supervisor)


@pytest.mark.isolation
def test_command_timeout_returns_feedback_and_next_turn_can_continue(tmp_path, publication):
    program = r'''
if request.turn == 0:
    reply = ModelReply('', (ToolCall('busy','run_command',{'command':'sleep 60'}),))
else:
    assert request.messages[-2]['content']['status'] == 'timed-out'
    reply = ModelReply('done')
sys.stdout.buffer.write(encode_model_outcome(request, reply))
'''
    job, journal, supervisor = setup(tmp_path, publication, program)
    job = replace(job, command_limits=replace(job.command_limits, wall_seconds=1))
    outcome = run_solver_process(job, journal=journal, supervision=supervisor)
    assert outcome.status == "no_submission" and outcome.attempt.turns == 2
    assert supervisor.load()[-1].payload["status"] == "exited"
    events = journal.load()
    assert run_solver_process(job, journal=journal, supervision=supervisor) == outcome
    assert journal.load() == events


@pytest.mark.isolation
def test_cancel_during_tool_dispatch_preserves_the_request_without_zero(tmp_path, publication):
    program = r'''
sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('',
    (ToolCall('busy','run_command',{'command':'sleep 60'}),))))
'''
    job, journal, supervisor = setup(tmp_path, publication, program, shell=True)

    def cancelled():
        return any(e.kind == "tool_requested" for e in journal.load())

    with pytest.raises(AttemptInterrupted, match="solver-worker-cancelled"):
        run_solver_process(job, journal=journal, supervision=supervisor, cancelled=cancelled)
    assert journal.load()[-1].kind == "tool_requested"
    assert supervisor.load()[-1].payload["status"] == "cancelled"


@pytest.mark.isolation
def test_committed_terminal_survives_later_supervisor_failure(tmp_path, publication, monkeypatch):
    from behave.isolation import service

    real = service.run_service

    def fail_after_completion(*args, **kwargs):
        assert kwargs["deadline"] is None
        got = real(*args, **kwargs)
        assert got.status == "exited" and got.returncode == 0
        return replace(got, status="cancelled", execution_status="cancelled")

    monkeypatch.setattr(service, "run_service", fail_after_completion)
    job, journal, supervisor = setup(tmp_path, publication,
                                    "sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('done')))")
    outcome = run_solver_process(job, journal=journal, supervision=supervisor)
    assert outcome.score == 0 and supervisor.load()[-1].payload["status"] == "cancelled"


def test_dangling_supervision_never_relaunches(tmp_path, publication, monkeypatch):
    from behave.isolation import service

    class Crash(BaseException):
        pass

    job, journal, supervisor = setup(tmp_path, publication, "pass")
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        run_solver_process(job, journal=journal, supervision=supervisor)
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("unknown launch must not be retried"))
    with pytest.raises(AttemptInterrupted, match="attempt-outcome-unknown"):
        run_solver_process(job, journal=journal, supervision=supervisor)


def test_solver_job_roundtrip_and_closed_profiles(tmp_path, publication):
    job, journal, _ = setup(tmp_path, publication, "pass")
    for file in job.task.files:
        journal.objects.put(file.data)
    assert SolverJob.from_bytes(job.to_bytes(), journal.objects) == job
    assert "cocotb_policy" not in parse_json(job.to_bytes())
    assert job.setting == "joint" and "setting" not in parse_json(job.to_bytes())
    rtl_only = replace(job, setting="rtl_only")
    assert parse_json(rtl_only.to_bytes())["setting"] == "rtl_only"
    assert SolverJob.from_bytes(rtl_only.to_bytes(), journal.objects) == rtl_only
    with pytest.raises(ValueError, match="unknown solver setting"):
        replace(job, setting="other")
    with pytest.raises(ValueError):
        replace(job, attempt=replace(job.attempt, model_policy={"transport": "other"}))


@pytest.mark.isolation
@pytest.mark.eda
def test_default_solver_worker_provides_writable_bash(tmp_path, publication):
    program = r'''
if request.turn == 0:
    assert [t['name'] for t in request.tools] == ['run_command', 'submit']
    tool = next(t for t in request.tools if t['name'] == 'run_command')
    assert 'Bash' in tool['description']
    assert set(tool['parameters']['properties']) == {'command'}
    assert '/docs/development.md' in request.messages[1]['content']
    calls = (ToolCall('edit', 'run_command', {'command':
        "set -e; cat /docs/development.md; yosys -V; python -c 'import cocotb'; "
        "printf 'class M: pass\\n' > model.py"}),)
else:
    assert request.messages[-2]['content']['exit_code'] == 0
    calls = (ToolCall('submit', 'submit', {
        'behavior': {'files': ['model.py'], 'entry_file': 'model.py', 'entry_symbol': 'M'},
        'rtl': None, 'testbench': None}),)
sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('', calls)))
'''
    job, journal, supervisor = setup(tmp_path, publication, program)
    job = SolverJob(job.task, job.attempt, job.model)
    from behave.sim.rtl.tool_isolation import default_limits

    assert job.command_limits == default_limits()
    outcome = run_solver_process(job, journal=journal, supervision=supervisor)
    assert outcome.status == "awaiting_judgment"
    captured = load_submission(journal.objects, outcome.submission)
    assert journal.objects.read(captured.behavior.files[0].content) == b"class M: pass\n"


def test_solver_job_passes_large_public_sources_into_workspace(tmp_path, publication):
    from behave.agent.workspace import Workspace
    from behave.core.artifacts import BlobRef, FileData, FileRef

    job, journal, _ = setup(tmp_path, publication, "pass")
    payload = b"x" * (17 << 20)
    files = (*job.task.files, *(FileData(FileRef(f"source/{i}.bin", BlobRef.of(payload)), payload) for i in range(4)))
    job = replace(job, task=replace(job.task, files=files))
    for file in files:
        journal.objects.put(file.data)
    restored = SolverJob.from_bytes(job.to_bytes(), journal.objects)
    workspace = Workspace(journal.objects, read_only_paths=restored.attempt.read_only_paths)
    workspace.seed(restored.task.files)
    assert workspace.read("source/3.bin") == payload
    assert sum(f.content.size_bytes for f in workspace.snapshot().files) > 64 << 20


@pytest.mark.isolation
def test_configured_local_root_reaches_worker_and_model_without_moving_journals(tmp_path, publication, monkeypatch):
    with TemporaryDirectory(prefix="b-root-", dir="/tmp") as local:
        monkeypatch.setenv("BEHAVE_SCRATCH_DIR", local)
        # The shared batch runtime outlives per-attempt scratch.
        monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime"))
        program = (
            "import os\nfrom pathlib import Path\n"
            f"assert os.environ['BEHAVE_SCRATCH_DIR'] == {local!r}\n"
            f"assert os.environ['TMPDIR'] == {local!r}\n"
            f"assert Path.cwd().parent == Path({local!r})\n"
            "sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('done')))"
        )
        job, journal, supervisor = setup(tmp_path / "durable", publication, program, shell=False)
        outcome = run_solver_process(job, journal=journal, supervision=supervisor)
        assert outcome.status == "no_submission" and outcome.score == 0
        assert journal.directory.parent == tmp_path / "durable"
        assert journal.load()[-1].kind == "terminal"
        assert supervisor.load()[-1].payload["cleanup"] == "complete"
        assert not list(Path(local).iterdir())


def test_solver_log_budget_uses_shared_default_and_accepts_explicit_override(tmp_path, publication):
    job, journal, _ = setup(tmp_path, publication, "pass")
    assert job.log_bytes == defaults.LOG_RETAIN_BYTES
    configured = replace(job, log_bytes=(16 << 20) + 1)
    for file in job.task.files:
        journal.objects.put(file.data)
    assert SolverJob.from_bytes(configured.to_bytes(), journal.objects) == configured


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, None])
def test_solver_log_budget_requires_positive_integer(tmp_path, publication, limit):
    job, _, _ = setup(tmp_path, publication, "pass")
    with pytest.raises(ValueError, match="positive integer"):
        replace(job, log_bytes=limit)


@pytest.mark.parametrize("configured", [False, True])
def test_installed_solver_resource_paths_do_not_require_eda_for_model_call(tmp_path, publication, monkeypatch, configured):
    from test_attempts import Scripted

    from behave.agent.model import ModelReply
    from behave.core import paths
    from behave.isolation import service
    from behave.task_execution import solver as solver_module

    job, journal, supervisor = setup(tmp_path, publication, "pass", shell=False)
    monkeypatch.setattr(paths, "__file__", str(tmp_path / "installed/behave/core/paths.py"))
    monkeypatch.chdir(tmp_path)
    for name in ("BEHAVE_REPO_ROOT", "BEHAVE_EDA_ROOT"):
        monkeypatch.delenv(name, raising=False)
    if configured:
        monkeypatch.setenv("BEHAVE_REPO_ROOT", "assets")
    expected = {"BEHAVE_REPO_ROOT": str(tmp_path / "assets"),
                "BEHAVE_EDA_ROOT": str(tmp_path / "assets/external/oss-cad-suite")} if configured else {}
    backend = Scripted(ModelReply("done"))
    monkeypatch.setattr(solver_module, "ProcessModelBackend", lambda command: backend)

    def execute_worker(argv, **kwargs):
        env = kwargs["environment"]
        assert {key: env[key] for key in ("BEHAVE_REPO_ROOT", "BEHAVE_EDA_ROOT") if key in env} == expected
        assert env["BEHAVE_WORK_DIR"] == str(kwargs["cwd"] / "sim-work")
        with monkeypatch.context() as patch:
            patch.setattr(sys, "argv", ["solver-worker", *argv[-6:]])
            solver_module.worker_main()
        return service.ServiceResult("exited", 0, b"", b"", 0.)

    monkeypatch.setattr(service, "run_service", execute_worker)
    result = run_solver_process(job, journal=journal, supervision=supervisor)
    assert result.status == "no_submission" and len(backend.requests) == 1
    assert supervisor.load()[-1].payload["cleanup"] == "complete"


def _worker_argv(job, journal):
    for file in job.task.files:
        journal.objects.put(file.data)
    reference = journal.objects.put(job.to_bytes())
    return ["solver-worker", str(journal.directory), str(journal.objects.directory), reference.sha256,
            str(reference.size_bytes),
            "", ""]


@pytest.mark.parametrize("stage", ["storage", "input", "job", "journal", "tools", "loop"])
@pytest.mark.parametrize("typed", [False, True])
def test_solver_worker_startup_and_runtime_errors_share_boundary(tmp_path, publication, monkeypatch, capfd, stage, typed):
    from behave.task_execution import shell, solver as solver_module

    job, journal, _ = setup(tmp_path, publication, "pass", shell=stage != "loop")
    monkeypatch.setattr(sys, "argv", _worker_argv(job, journal))

    def fail(*args, **kwargs):
        if typed:
            raise AttemptInterrupted("fixture-interrupted")
        raise OSError("PRIVATE_EXCEPTION_DETAIL")

    target, name = {
        "storage": (solver_module, "DirectoryStore"),
        "input": (solver_module, "read_verified"),
        "job": (SolverJob, "from_bytes"),
        "journal": (solver_module, "AttemptJournal"),
        "tools": (shell, "development_shell"),
        "loop": (solver_module, "run_solver"),
    }[stage]
    monkeypatch.setattr(target, name, fail)
    with pytest.raises(SystemExit) as error:
        solver_module.worker_main()
    assert error.value.code == 2
    streams = capfd.readouterr()
    code = "fixture-interrupted" if typed else "solver-worker-OSError"
    assert parse_json(streams.out.encode()) == {"interruption": code}
    assert streams.err == "" and "PRIVATE_EXCEPTION_DETAIL" not in streams.out
    assert not journal.load()  # No normal terminal or candidate zero is fabricated.


@pytest.mark.parametrize("bad_argument", ["arity", "size"])
def test_solver_worker_invalid_launch_arguments_are_structured(tmp_path, publication, monkeypatch, capfd, bad_argument):
    from behave.task_execution import solver as solver_module

    job, journal, _ = setup(tmp_path, publication, "pass")
    argv = _worker_argv(job, journal)
    if bad_argument == "arity":
        argv.pop()
    else:
        argv[4] = "PRIVATE_INVALID_NUMBER"
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as error:
        solver_module.worker_main()
    assert error.value.code == 2
    streams = capfd.readouterr()
    assert parse_json(streams.out.encode()) == {"interruption": "solver-worker-ValueError"}
    assert streams.err == "" and "PRIVATE_INVALID_NUMBER" not in streams.out
    assert not journal.load()
