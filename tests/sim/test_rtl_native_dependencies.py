"""ELF loader selection, not a recursive snapshot of the tool distribution."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from behave.isolation.runtime_environment import RuntimeEnvironment
from behave.sim.rtl import native_dependencies as dependencies


@pytest.mark.parametrize("programs", [("first", "second"), ("verilator_bin",)])
def test_loader_closure_is_local_shared_and_excludes_unrelated_files(tmp_path, monkeypatch, programs):
    eda = tmp_path / "eda"
    libraries = eda / "lib"
    libraries.mkdir(parents=True)
    loader = libraries / "ld-linux-x86-64.so.2"
    loader.write_bytes(b"loader")
    library = libraries / "libneeded.so"
    library.write_bytes(b"library")
    (libraries / "python").mkdir()
    os.mkfifo(libraries / "python" / "must-not-be-scanned")
    calls = []

    def resolve(argv, **kwargs):
        calls.append(argv[-1])
        assert argv[:-1] == [str(loader), "--inhibit-cache", "--inhibit-rpath", "",
                             "--library-path", str(libraries), "--list"]
        assert kwargs["check"] and kwargs["timeout"] > 0
        return SimpleNamespace(stdout=f"libneeded.so => {library} (0x1)\n{loader} (0x2)\n")

    monkeypatch.setattr(dependencies.subprocess, "run", resolve)
    batch = RuntimeEnvironment(tmp_path / "batch")
    with batch.activate():
        mounts = dependencies.eda_library_mounts(eda, programs)
    resolved = tuple(dependencies.SIM_VERILATOR_EXECUTABLE if name == "verilator_bin" else name
                     for name in programs)
    assert set(calls) == {str(eda / "libexec" / name) for name in resolved}
    assert {m.target for m in mounts} == {"/opt/eda/lib/" + p.name for p in (loader, library)}
    assert all(m.prepared_runtime and Path(m.source).is_relative_to(tmp_path / "batch/images") for m in mounts)
    with RuntimeEnvironment(batch.directory).activate():
        assert dependencies.eda_library_mounts(eda, programs) == mounts
    assert len(calls) == len(programs)


def test_compiler_revision_is_checked_once_per_runtime_without_stock_fallback(tmp_path, monkeypatch):
    from behave.isolation import binary
    from behave.sim.core.refusal import Refused

    eda = tmp_path / "eda"
    (eda / "lib").mkdir(parents=True)
    loader = eda / "lib/ld-linux-x86-64.so.2"
    loader.write_bytes(b"loader")
    actual = "Verilator 5.051 devel"
    calls = []

    def version(argv, **kwargs):
        calls.append(argv)
        assert argv == [str(loader), "--library-path", str(eda / "lib"),
                        str(eda / "libexec" / dependencies.SIM_VERILATOR_EXECUTABLE), "--version"]
        return subprocess.CompletedProcess(argv, 0, actual + "\n", "")

    executable = binary(sys.executable, "/opt/eda/libexec/verilator_bin")
    monkeypatch.setattr(dependencies.subprocess, "run", version)
    monkeypatch.setattr(dependencies, "binary", lambda *args, **kwargs: executable)
    directory = tmp_path / "runtime"
    with RuntimeEnvironment(directory).activate():
        with pytest.raises(Refused, match="rtl-verilator-version-mismatch"):
            dependencies.checked_sim_verilator(eda)
        assert not list(directory.glob("*.json"))
        actual += " " + dependencies.SIM_VERILATOR_MARKER
        first = dependencies.checked_sim_verilator(eda)
        assert dependencies.checked_sim_verilator(eda) is first
    with RuntimeEnvironment(directory).activate():
        assert dependencies.checked_sim_verilator(eda) == executable
    assert len(calls) == 2


def test_dependency_resolution_failure_does_not_publish_a_profile(tmp_path, monkeypatch):
    eda = tmp_path / "eda"
    (eda / "lib").mkdir(parents=True)
    (eda / "lib" / "ld-linux-x86-64.so.2").write_bytes(b"loader")

    def failed(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr="missing dependency")

    monkeypatch.setattr(dependencies.subprocess, "run", failed)
    batch = RuntimeEnvironment(tmp_path / "batch")
    with batch.activate(), pytest.raises(RuntimeError, match="cannot resolve managed EDA dependencies"):
        dependencies.eda_library_mounts(eda, ("tool",))
    assert not list(batch.directory.glob("*.json"))
