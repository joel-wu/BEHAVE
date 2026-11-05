"""Local execution staging and durable publication have independent owners."""

import tempfile
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.rtl import tool_isolation as tools
from behave.sim.rtl import tool_protocol, tool_runtime
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.replay_build import replay_build_scope
from tests.sim.test_rtl_tool_isolation import _command_row, _runtime


@pytest.fixture
def roots(tmp_path, monkeypatch):
    local, ambient, saved = [tmp_path / name for name in ("local", "ambient", "saved")]
    for directory in (local, ambient, saved):
        directory.mkdir()
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(local))
    monkeypatch.setenv("TMPDIR", str(ambient))
    monkeypatch.setattr(tempfile, "tempdir", str(ambient))
    monkeypatch.setenv("BEHAVE_WORK_DIR", str(saved))
    (saved / "receipt").write_bytes(b"keep durable evidence")
    yield local, ambient, saved
    assert list(ambient.iterdir()) == []
    assert list(local.iterdir()) == []
    assert (saved / "receipt").read_bytes() == b"keep durable evidence"


@pytest.mark.parametrize("fail", (False, True))
def test_build_scope_owns_nested_replay_and_unfinished_preparation(roots, fail):
    local, _, _ = roots
    artifact = object()
    paths = []
    try:
        with replay_build_scope(artifact) as owner:
            paths.append(owner.directory("compile-"))
            with replay_build_scope(artifact) as nested:
                assert nested is owner
                paths.append(nested.directory("invocation-"))
                for path in paths:
                    assert path.parent.parent == local
                    (path / "input").write_bytes(b"private")
                if fail:
                    raise OSError("interrupted preparation")
            assert all(path.is_dir() for path in paths)
    except OSError:
        assert fail
    assert len(set(paths)) == 2
    assert all(not path.exists() for path in paths)


def test_replay_input_preparation_uses_local_scope_even_before_compilation(roots, monkeypatch):
    from behave.sim import harness
    from tests.sim.test_api_path import contract

    local, _, saved = roots
    rtl = saved / "rtl.sv"
    rtl.write_text("module top; endmodule\n")
    artifact = RTLArtifact(rtl, "top", saved)
    monkeypatch.setattr(artifact, "frontend_catalogue", lambda **_: api.CatalogueManifest(()))
    seen = []

    def stop(where, *_args, **_kwargs):
        seen.append(where)
        assert where.parent.parent.parent == local
        assert (where / "driver_tb.py").is_file()
        raise OSError("stop before any compiler or simulator starts")

    monkeypatch.setattr(harness, "generate_makefile", stop)
    c = contract()
    stimulus = api.reset_then(c, [(1,)], drain=5, controls={"out_ready": 1})
    with pytest.raises(OSError, match="stop before"):
        artifact.traces_per_process(c, [stimulus], workers=1)
    assert len(seen) == 1 and not seen[0].exists()
    assert sorted(path.name for path in saved.iterdir()) == ["receipt", "rtl.sv"]


def test_tool_job_is_local_without_relocating_its_inputs_or_outputs(roots, monkeypatch):
    import json

    from behave.isolation import IsolationResult, IsolationStatus

    local, _, saved = roots
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(saved / "runtime-batch"))
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: _runtime(saved))
    seen = []

    def run(spec):
        mounts = {mount.target: mount for mount in spec.read_only}
        path = Path(mounts["/input/job.json"].source)
        seen.append(path)
        assert path.parent.parent == local
        assert Path(mounts["/input/tree"].source) == saved
        assert json.loads(path.read_bytes())["outputs"] == ["trace.json"]
        response = json.dumps({
            "version": tool_protocol.JOB_VERSION,
            "commands": [_command_row("probe")],
            "outputs": {"trace.json": "e30="},
            "trees": {},
        }).encode()
        return IsolationResult(IsolationStatus.COMPLETED, spec.digest, "fixture",
                               exit_code=0, stdout=response)

    monkeypatch.setattr(tools, "run", run)
    got = tools.run_workspace(saved, (tool_protocol.ToolGroup((tool_protocol.ToolCommand(
        "probe", ("/runtime/python",)),)),), outputs=("trace.json",), role="probe")
    assert got.status is IsolationStatus.COMPLETED, got.as_dict()
    assert got.outputs["trace.json"] == b"{}"
    assert (saved / "trace.json").read_bytes() == b"{}"
    assert len(seen) == 1 and not seen[0].exists()


@pytest.mark.parametrize("shared_runtime", (False, True))
def test_fresh_worker_uses_configured_local_cache_and_lock_roots(roots, shared_runtime):
    import os
    import subprocess
    import sys

    local, _, saved = roots
    # Cache roots are selected at worker startup, not mutated under active owners.
    with tempfile.TemporaryDirectory(prefix="worker-", dir=local) as worker:
        code = """
import os
from pathlib import Path
from behave.core.paths import scratch_root, work_root
from behave.isolation import package_image, content_attestation, python_package
from behave.isolation.runtime_environment import runtime_environment
from behave.sim.behavior.construction import _BUILD_SLOTS
assert scratch_root() == Path(os.environ['BEHAVE_SCRATCH_DIR'])
assert work_root() == Path(os.environ['BEHAVE_WORK_DIR'])
runtime = runtime_environment().directory
assert runtime.is_relative_to(scratch_root())
assert package_image._cache_root().parent == runtime
assert content_attestation._cache_directory().parent == scratch_root()
assert _BUILD_SLOTS._directory().parent == scratch_root()
source = scratch_root() / 'source'
source.mkdir()
(source / '__init__.py').write_text('VALUE = 7')
mount = python_package(str(source), '/runtime/test')
assert Path(mount.source).is_relative_to(scratch_root())
assert python_package(str(source), '/runtime/test').sha256 == mount.sha256
assert (Path(mount.source) / '__init__.py').read_text() == 'VALUE = 7'
"""
        environment = dict(os.environ, BEHAVE_SCRATCH_DIR=worker,
                           BEHAVE_WORK_DIR=str(saved))
        # Exercise a fresh batch, not a runtime inherited from the test runner.
        environment.pop("BEHAVE_RUNTIME_DIR", None)
        if shared_runtime:
            environment["BEHAVE_RUNTIME_DIR"] = str(Path(worker) / "batch")
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                                env=environment, timeout=20, check=False)
        assert result.returncode == 0, result.stderr.decode()
