"""Shared filesystem state, native shell editing and final submission."""

from dataclasses import replace

import pytest

from behave.agent.commands import shell_commands
from behave.agent.workspace import Workspace, WorkspaceSnapshot
from behave.core.artifacts import BlobRef, FileData, FileRef
from behave.core.storage import DirectoryStore
from behave.isolation import ResourceLimits, WritableRoot


def test_shell_tool_renders_the_configured_command_timeout():
    runner = shell_commands(ResourceLimits(wall_seconds=37))
    assert "wall-time limit of 37 seconds" in runner.tool().description
    assert "Command output is included in the conversation." in runner.tool().description
    assert "save output to a workspace log" in runner.tool().description
    assert "elapsed wall time" in runner.tool().description
    changed = type(runner)(replace(runner.template, limits=replace(runner.template.limits, wall_seconds=91)),
                           writable_workspace=True)
    assert "wall-time limit of 91 seconds" in changed.tool().description


def test_workspace_import_preserves_edits_links_empty_dirs_and_executable_scripts(tmp_path):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"), read_only_paths=("spec.md",))
    data = b"read-only task"
    workspace.seed((FileData(FileRef("spec.md", BlobRef.of(data)), data),))
    workspace.write("remove.py", b"old")
    with workspace.materialize() as root:
        (root / "remove.py").unlink()
        (root / "empty").mkdir()
        (root / "src").mkdir()
        (root / "src/model.py").write_bytes(b"class M: pass\n")
        (root / "model.py").symlink_to("src/model.py")
        (root / "package").symlink_to("src", target_is_directory=True)
        (root / "outside").symlink_to("/etc/passwd")
        (root / "check.sh").write_text("#!/bin/bash\nexit 0\n")
        (root / "check.sh").chmod(0o755)
        workspace.import_directory(root)
    ref, _ = workspace.checkpoint()
    restored = Workspace(workspace.store)
    restored.restore(ref)
    assert restored.read("model.py") == b"class M: pass\n"
    assert restored.read("package/model.py") == b"class M: pass\n"
    assert restored.snapshot().file("outside") is None
    assert "remove.py" not in {file.path for file in restored.snapshot().files}
    assert WorkspaceSnapshot.from_bytes(restored.snapshot().to_bytes()) == restored.snapshot()
    with restored.materialize() as root:
        assert (root / "empty").is_dir()
        assert (root / "check.sh").stat().st_mode & 0o111
        assert (root / "model.py").is_symlink()
        assert (root / "model.py").read_bytes() == b"class M: pass\n"


def test_shell_mount_and_writeback_reuse_existing_supervisor(tmp_path, monkeypatch):
    from behave.agent import commands
    from behave.isolation import IsolationResult, IsolationStatus

    workspace = Workspace(DirectoryStore(tmp_path / "objects"), read_only_paths=("spec.md",))
    data = b"task"
    workspace.seed((FileData(FileRef("spec.md", BlobRef.of(data)), data),))
    # The fixture only substitutes the launcher. It still prepares the actual runtime.
    runner = shell_commands()
    def run(spec):
        assert spec.argv[-2:] == ("-c", "printf edited > rtl.sv")
        assert spec.cwd == "/workspace"
        root = next(mount.source for mount in spec.writable if mount.target == "/workspace")
        from pathlib import Path
        assert any(mount.target == "/workspace/spec.md" for mount in spec.read_only)
        Path(root, "rtl.sv").write_bytes(b"edited")
        return IsolationResult(IsolationStatus.EXITED, spec.digest, "fixture", exit_code=7)
    monkeypatch.setattr(commands, "run", run)
    result = runner.execute(workspace, {"command": "printf edited > rtl.sv"})
    assert result.content["exit_code"] == 7
    assert workspace.read("rtl.sv") == b"edited"


def test_writable_mount_locator_is_not_a_semantic_policy_identity():
    first = WritableRoot("/workspace", 1024, "/tmp/task-a")
    second = replace(first, source="/tmp/task-b")
    assert first.identity() == second.identity()
    assert first.identity() != replace(first, source=None).identity()


def test_checkpointed_shell_tmp_survives_calls_without_entering_source_snapshots(tmp_path, monkeypatch):
    from pathlib import Path

    from behave.agent import commands
    from behave.isolation import IsolationResult, IsolationStatus

    objects = DirectoryStore(tmp_path / "objects")
    first, second = Workspace(objects), Workspace(objects)
    runner = shell_commands()
    roots = []

    def execute(spec):
        mounts = {mount.target: Path(mount.source) for mount in spec.writable}
        root, temporary = mounts["/workspace"], mounts["/tmp"]
        roots.append((root, temporary))
        if len(roots) == 1:
            (temporary / "self_check.py").write_text("print('check')\n")
        elif len(roots) == 2:
            assert (temporary / "self_check.py").read_text() == "print('check')\n"
            (root / "result.txt").write_text("checked\n")
        else:
            assert not (temporary / "self_check.py").exists()
        return IsolationResult(IsolationStatus.EXITED, spec.digest, "fixture",
                               exit_code=1 if len(roots) == 1 else 0)

    monkeypatch.setattr(commands, "run", execute)
    assert runner.execute(first, {"command": "write script; exit 1"}).content["exit_code"] == 1
    assert runner.execute(first, {"command": "run saved script"}).content["exit_code"] == 0
    assert runner.execute(second, {"command": "check private scratch"}).content["exit_code"] == 0
    assert roots[0][0] != roots[1][0]
    assert roots[0][1] == roots[1][1] != roots[2][1]
    assert first.paths() == ("result.txt",)
    assert second.paths() == ()
    reference, _ = first.checkpoint()
    restored = Workspace(objects)
    restored.restore(reference)
    assert restored.read("result.txt") == b"checked\n"
    assert not (restored.temporary_directory / "self_check.py").exists()


@pytest.mark.parametrize("extra", ["yield_time_ms", "timeout_ms", "timeout", "max_output_chars"])
def test_shell_parameter_error_is_actionable_and_correction_executes_once(tmp_path, monkeypatch, extra):
    from pathlib import Path

    from test_attempts import Scripted, call, run, setup

    from behave.agent import commands
    from behave.agent.model import ModelReply
    from behave.isolation import IsolationResult, IsolationStatus

    journal, spec = setup(tmp_path)
    runner = shell_commands()
    invocations = []
    command = "printf 'saved\\n' > result.txt\ncat result.txt"

    def execute(policy):
        invocations.append(policy.argv)
        assert policy.argv[-2:] == ("-c", command)
        root = next(m.source for m in policy.writable if m.target == "/workspace")
        Path(root, "result.txt").write_bytes(b"saved\n")
        return IsolationResult(IsolationStatus.COMPLETED, policy.digest, "fixture", exit_code=0)

    monkeypatch.setattr(commands, "run", execute)
    backend = Scripted(call("run_command", {"command": command, extra: 1000}),
                       call("run_command", {"command": command}), ModelReply("done"))
    result = run(journal, spec, backend, tools=(runner.tool(),))
    feedback = backend.requests[1].messages[-2]["content"]
    assert feedback["error"] == "record-fields-invalid"
    assert feedback["executed"] is False
    assert "expected exactly the fields ['command']" in feedback["message"]
    assert f"Unexpected: ['{extra}']" in feedback["message"]
    assert backend.requests[2].messages[-2]["content"]["exit_code"] == 0
    assert len(invocations) == 1
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    assert workspace.read("result.txt") == b"saved\n"
    assert run(journal, spec, Scripted(), tools=(runner.tool(),)) == result
    assert len(invocations) == 1


@pytest.mark.isolation
def test_development_log_example_preserves_exit_status_and_log_between_turns(tmp_path):
    from importlib.resources import files

    from test_attempts import Scripted, call, run, setup

    from behave.agent.model import ModelReply

    journal, spec = setup(tmp_path)
    # Guides are mounted verbatim, not interpreted as prompt templates.
    guide = files("behave.agent").joinpath("prompts/shared/guides/development.md").read_text()
    example = guide.split("```bash\n", 1)[1].split("```", 1)[0]
    # Exercise the published script with a deterministic failing build, not a
    # second copy of the documentation's log-handling code.
    command = "make() { seq 120; printf 'build failed\\n' >&2; return 7; }\n" + example
    runner = shell_commands(ResourceLimits(processes=16, wall_seconds=30))
    backend = Scripted(call("run_command", {"command": command}),
                       call("run_command", {"command": "sed -n '1,3p' build.log"}), ModelReply("done"))
    result = run(journal, spec, backend, tools=(runner.tool(),))
    first = backend.requests[1].messages[-2]["content"]
    assert first["exit_code"] == 7 and first["status"] == "exited"
    assert first["stdout"].endswith("120\nbuild failed\n") and first["stderr"] == ""
    assert first["elapsed_seconds"] > 0
    second = backend.requests[2].messages[-2]["content"]
    assert second["exit_code"] == 0 and second["stdout"] == "1\n2\n3\n"
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    assert workspace.read("build.log") == (
        "".join(f"{i}\n" for i in range(1, 121)) + "build failed\n").encode()


@pytest.mark.isolation
def test_real_shell_standard_utilities(tmp_path):
    workspace = Workspace(DirectoryStore(tmp_path / "objects"))
    command = """set -e
seq 3 > numbers
nl -ba numbers | awk '{print $2}' > copied
cmp numbers copied
test "$(wc -l < copied)" -eq 3
test "$(stat -c %s copied)" -eq 6
test "$(realpath copied)" = /workspace/copied
printf 'copied\n' | xargs cat
od -An -tx1 copied
du -k copied
date -u +%Y
"""
    result = shell_commands(ResourceLimits(processes=16, wall_seconds=30)).execute(workspace, {"command": command})
    assert result.content["exit_code"] == 0, result.content
    assert workspace.read("copied") == b"1\n2\n3\n"


def test_workspace_link_cycles_and_repeated_directory_aliases():
    source = FileRef("model.py", BlobRef.of(b"model"))
    snapshot = WorkspaceSnapshot((source,), symlinks=(
        ("loop", "loop/child"), ("here", "."), ("root", "/workspace")))
    assert snapshot.file("loop/model.py") is None
    assert snapshot.file("here/here/model.py").content == source.content
    assert snapshot.file("root/model.py").content == source.content


@pytest.mark.isolation
def test_real_shell_edits_tests_and_submits_without_file_tools(tmp_path, publication):
    from test_attempts import Scripted, call, setup

    from behave.agent.model import ModelReply
    from behave.data.published import load_published_task
    from behave.task_execution.capture import load_submission
    from behave.task_execution.solver import run_solver

    journal, spec = setup(tmp_path)
    public = load_published_task(publication.reader, publication.ref, publication.task.task_id).public
    runner = shell_commands(ResourceLimits(processes=16, wall_seconds=30))
    first = """cat > behavior.py <<'PY'
class M:
    def process(self, x): return x + 1
PY
printf 'module top; endmodule\n' > rtl.sv
printf '# TB source\n' > tb.py
mkdir empty
ln -s behavior.py model_alias.py
printf '#!/bin/bash\necho script-ok\n' > check.sh
chmod +x check.sh
"""
    check = """sed -i 's/x + 1/x + 2/' behavior.py
python -c 'from behavior import M; assert M().process(3) == 5'
./check.sh
test -d empty && test -L model_alias.py
if printf changed > spec.md; then exit 9; fi
test ! -e task.toml
"""
    selections = {
        "rtl": {"files": ["rtl.sv"], "entry_file": "rtl.sv", "entry_symbol": "top"},
        "behavior": {"files": ["model_alias.py"], "entry_file": "model_alias.py", "entry_symbol": "M"},
        "testbench": {"files": ["tb.py"], "entry_file": "tb.py", "entry_symbol": "exercise"},
    }
    backend = Scripted(call("run_command", {"command": first}),
                       call("run_command", {"command": check}), call("submit", selections))
    result = run_solver(task=public, spec=replace(spec, role="solver"), backend=backend,
                        journal=journal, command_tool=runner.tool())
    output = backend.requests[2].messages[-2]["content"]
    assert output["exit_code"] == 0, output
    assert "script-ok" in output["stdout"]
    frozen = load_submission(journal.objects, result.submission)
    assert frozen.behavior.artifact is not None
    assert b"x + 2" in journal.objects.read(frozen.behavior.files[0].content)
    assert frozen.rtl.artifact is not None and frozen.testbench.artifact is not None
    # Restore the already committed tool results, never execute shell commands twice.
    assert run_solver(task=public, spec=replace(spec, role="solver"), backend=Scripted(ModelReply("unused")),
                      journal=journal, command_tool=runner.tool()) == result


@pytest.mark.isolation
@pytest.mark.parametrize("local", [False, True])
def test_real_shell_timeout_keeps_edits_and_kills_descendants(tmp_path, local):
    from behave.agent.local_workspace import LocalWorkspace

    objects = DirectoryStore(tmp_path / "objects")
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = LocalWorkspace(objects, root) if local else Workspace(objects)
    runner = shell_commands(ResourceLimits(processes=16, wall_seconds=1))
    result = runner.execute(workspace, {"command":
        "echo kept > before; echo partial; echo diagnostic >&2; (sleep 10; echo leaked > after) < before & wait"})
    assert result.content["status"] == "timed-out", result.content
    assert result.content["stdout"] == "partial\n"
    assert result.content["stderr"] == "diagnostic\n"
    assert result.content["elapsed_seconds"] >= 1
    assert workspace.read("before") == b"kept\n"
    assert "after" not in workspace.paths()


@pytest.mark.isolation
@pytest.mark.parametrize("local", [False, True])
def test_real_rollout_tmp_persists_across_commands_but_is_private_and_ephemeral(tmp_path, local):
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from behave.agent.local_workspace import LocalWorkspace

    objects = DirectoryStore(tmp_path / "objects")
    runner = shell_commands(ResourceLimits(processes=16, wall_seconds=5))
    with TemporaryDirectory(dir=tmp_path) as owned:
        root = Path(owned)
        first = LocalWorkspace(objects, root / "first") if local else Workspace(objects)
        second = LocalWorkspace(objects, root / "second") if local else Workspace(objects)
        result = runner.execute(first, {"command": "echo persistent > /tmp/tool.log; exit 1"})
        assert result.content["exit_code"] == 1
        result = runner.execute(first, {"command": "cat /tmp/tool.log; cp /tmp/tool.log final.txt"})
        assert result.content["exit_code"] == 0 and result.content["stdout"] == "persistent\n"
        result = runner.execute(second, {"command": "test ! -e /tmp/tool.log; test ! -e final.txt"})
        assert result.content["exit_code"] == 0
        first.archive(("final.txt",))
        assert first.paths() == ("final.txt",)
        assert [f.path for f in first.snapshot().files] == ["final.txt"]
        assert first.temporary_directory.joinpath("tool.log").read_text() == "persistent\n"
    assert not root.exists()  # Caller cleanup removes workspace and tmp together.
