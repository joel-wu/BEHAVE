"""Optional self-improvement notes use the existing solver and recorded context."""

from dataclasses import replace

import pytest
from test_attempts import Scripted, call, local_shell, setup
from test_solver_process import setup as process_setup

from behave.agent.model import ModelReply
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, parse_json
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task
from behave.task_execution.solver import SolverJob, run_solver


def test_optional_experience_job_cold_roundtrip_preserves_default_bytes(tmp_path, publication):
    job, _, _ = process_setup(tmp_path, publication, "", shell=False)
    original = job.to_bytes()
    assert "solver_experience" not in parse_json(original)
    note = b"Check reset before testing normal transactions.\n"
    supplied = replace(job, solver_experience=note)
    store = DirectoryStore(tmp_path / "exported")
    for payload in supplied.input_blobs():
        store.put(payload)
    restored = SolverJob.from_bytes(supplied.to_bytes(), DirectoryStore(store.directory))
    assert restored == supplied
    assert restored.solver_experience == note
    assert replace(restored, solver_experience=None).to_bytes() == original
    assert restored.task == job.task  # Not part of the task or release.


@pytest.mark.parametrize("note", [None, b"", b"Check reset before normal transactions.\n"])
def test_solver_reads_pinned_notes_and_cold_replay_keeps_context(tmp_path, publication, note):
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    journal, spec = setup(tmp_path)
    spec = replace(spec, role="solver")
    command = "test ! -e experience/solver.md" if note is None else "cat experience/solver.md"
    backend = Scripted(call("run_command", {"command": command}), ModelReply("done"))
    options = dict(task=public, spec=spec, journal=journal, command_tool=local_shell(),
                   solver_experience=note)
    result = run_solver(backend=backend, **options)
    config = parse_json(journal.objects.read(BlobRef.from_dict(dict(journal.load()[0].payload["configuration"]))))
    workspace = Workspace(journal.objects)
    workspace.restore(BlobRef.from_dict(config["workspace"]))
    opening = backend.requests[0].messages[1]["content"]
    reply = backend.requests[1].messages[-2]["content"]
    assert reply["exit_code"] == 0
    if note is None:
        assert "experience/solver.md" not in opening
        assert workspace.snapshot().file("experience/solver.md") is None
    else:
        assert "experience/solver.md" in opening
        assert "read-only advice" in opening
        assert workspace.read("experience/solver.md") == note
        assert reply["stdout"].encode() == note
        assert "experience/solver.md" in config["spec"]["read_only_paths"]
    assert workspace.snapshot().file("experience/construction.md") is None
    assert run_solver(backend=Scripted(), **options) == result
    if note is not None:
        with pytest.raises(RecordError, match="replay differs"):
            run_solver(backend=Scripted(), **{**options, "solver_experience": b"Newer memory"})


@pytest.mark.isolation
@pytest.mark.eda
def test_solver_worker_mounts_only_readonly_solver_view(tmp_path, publication):
    from behave.task_execution.solver import run_solver_process

    program = r'''
if request.turn == 0:
    assert 'experience/solver.md' in request.messages[1]['content']
    calls = (ToolCall('check', 'run_command', {'command':
        "test -r experience/solver.md && test ! -w experience/solver.md && "
        "test ! -e experience/construction.md && cat experience/solver.md"}),)
else:
    assert request.messages[-2]['content']['exit_code'] == 0, request.messages[-2]
    assert request.messages[-2]['content']['stdout'] == 'Shared lesson\n'
    calls = ()
sys.stdout.buffer.write(encode_model_outcome(request, ModelReply('done', calls)))
'''
    job, journal, supervisor = process_setup(tmp_path, publication, program)
    job = replace(job, solver_experience=b"Shared lesson\n")
    result = run_solver_process(job, journal=journal, supervision=supervisor)
    assert result.status == "no_submission"
    assert run_solver_process(job, journal=journal, supervision=supervisor) == result
