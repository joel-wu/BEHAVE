import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from behave.sim.rtl.native_dependencies import (
    NATIVE_FRONTEND_EXECUTABLE,
    NATIVE_FRONTEND_REVISION,
    SIM_VERILATOR_EXECUTABLE,
    SIM_VERILATOR_MARKER,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "setup_eda.sh"
ARCH = {
    "x86_64": "linux-x64",
    "aarch64": "linux-arm64",
    "arm64": "linux-arm64",
}.get(platform.machine())
pytestmark = pytest.mark.skipif(ARCH is None, reason="setup_eda.sh supports x86-64 and arm64")


def _installation(tmp_path: Path, tools: tuple[str, ...]) -> Path:
    external = tmp_path / "external"
    installation = external / "oss-cad-suite"
    (installation / "bin").mkdir(parents=True)
    (installation / "libexec").mkdir()
    (installation / "VERSION").write_text("20260731", encoding="utf-8")
    (external / "eda.lock").write_text(
        f'release = "2026-07-31"\narch = "{ARCH}"\n', encoding="utf-8")
    for tool in tools:
        path = installation / "bin" / tool
        path.write_text("#!/bin/sh\necho version\n", encoding="utf-8")
        path.chmod(0o755)
    return external


def _check(external: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, BEHAVE_EDA_EXTERNAL_DIR=str(external))
    return subprocess.run(
        ["bash", str(SCRIPT), "--check"], text=True, capture_output=True,
        check=False, env=env)


def test_a_reused_installation_missing_a_required_tool_is_fatal(tmp_path):
    external = _installation(tmp_path, ("verilator",))
    got = _check(external)
    assert got.returncode != 0
    assert "missing executable yosys" in got.stderr


def test_a_reused_installation_with_an_unverified_architecture_is_fatal(tmp_path):
    external = _installation(tmp_path, ("verilator", "yosys", "iverilog"))
    native = external / "oss-cad-suite" / "libexec" / "verilator_bin"
    native.write_text("not an ELF executable\n", encoding="utf-8")
    got = _check(external)
    assert got.returncode != 0
    assert "architecture does not match" in got.stderr


@pytest.mark.parametrize("version,success", [
    (NATIVE_FRONTEND_REVISION, True),
    ("behave-rtl-frontend/13 slang-source=8acc660a2", False),
    ("behave-rtl-frontend/14 slang-source=other", False),
])
def test_native_frontend_check_does_not_install_or_accept_another_revision(tmp_path, version, success):
    prefix = tmp_path / "eda"
    executable = prefix / "libexec" / NATIVE_FRONTEND_EXECUTABLE
    executable.parent.mkdir(parents=True)
    executable.write_text(f"#!/bin/sh\necho '{version}'\n")
    executable.chmod(0o755)
    got = subprocess.run(
        ["bash", str(ROOT / "scripts/setup_rtl_frontend.sh"), "--check"],
        text=True, capture_output=True, check=False,
        env=dict(os.environ, BEHAVE_EDA_ROOT=str(prefix)))
    assert (got.returncode == 0) == success, got.stderr
    assert list(prefix.rglob("*")) == [executable.parent, executable]


def test_runtime_checks_native_revision_once_before_publication(tmp_path, monkeypatch):
    from behave.isolation import binary
    from behave.isolation.runtime_environment import RuntimeEnvironment
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl import native_dependencies as native

    calls = []
    actual = "behave-rtl-frontend/11 slang-source=8acc660a2"

    def version(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, actual + "\n", "")

    executable = binary(sys.executable, "/tools/behave-rtl-frontend")
    monkeypatch.setattr(native.subprocess, "run", version)
    monkeypatch.setattr(native, "binary", lambda *args, **kwargs: executable)
    directory = tmp_path / "runtime"
    with RuntimeEnvironment(directory).activate():
        with pytest.raises(Refused, match="rtl-native-frontend-version-mismatch"):
            native.checked_native_frontend(tmp_path)
        assert not list(directory.glob("*.json"))
        actual = native.NATIVE_FRONTEND_REVISION
        first = native.checked_native_frontend(tmp_path)
        assert native.checked_native_frontend(tmp_path) is first
    # A second worker consumes the prepared resource without probing again.
    with RuntimeEnvironment(directory).activate():
        assert native.checked_native_frontend(tmp_path) == executable
    assert len(calls) == 2


def test_install_check_reads_the_whole_version_output_without_sigpipe(tmp_path):
    external = _installation(tmp_path, ("verilator", "yosys", "iverilog"))
    installation = external / "oss-cad-suite"
    shutil.copyfile(sys.executable, installation / "libexec/verilator_bin")
    # A version banner can exceed the pipe buffer. Reading just its first line
    # under pipefail used to terminate --check with 141 despite valid tools.
    (installation / "bin/iverilog").write_text(
        "#!/bin/sh\necho version\nprintf '%0100000d\\n' 0\n")
    frontend = installation / "libexec" / NATIVE_FRONTEND_EXECUTABLE
    frontend.write_text(f"#!/bin/sh\necho '{NATIVE_FRONTEND_REVISION}'\n")
    frontend.chmod(0o755)
    compiler = installation / "libexec" / SIM_VERILATOR_EXECUTABLE
    compiler.write_text(f"#!/bin/sh\necho 'Verilator 5.051 devel {SIM_VERILATOR_MARKER}'\n")
    compiler.chmod(0o755)
    got = _check(external)
    assert got.returncode == 0, got.stderr
    assert "iverilog" in got.stdout and "version" in got.stdout


@pytest.mark.parametrize("version,success", [
    (f"Verilator 5.051 devel {SIM_VERILATOR_MARKER}", True),
    ("Verilator 5.051 devel", False),
    (f"Verilator 5.050 {SIM_VERILATOR_MARKER}", False),
])
def test_sim_compiler_install_reuses_only_the_qualified_executable(tmp_path, version, success):
    prefix = tmp_path / "eda"
    executable = prefix / "libexec" / SIM_VERILATOR_EXECUTABLE
    executable.parent.mkdir(parents=True)
    executable.write_text(f"#!/bin/sh\necho '{version}'\n")
    executable.chmod(0o755)
    stock = executable.with_name("verilator_bin")
    stock.write_text("stock compiler must remain intact\n")
    for arguments in (["--check"], []):
        got = subprocess.run(
            ["bash", str(ROOT / "scripts/setup_sim_verilator.sh"), *arguments],
            text=True, capture_output=True, check=False,
            env=dict(os.environ, BEHAVE_EDA_ROOT=str(prefix)))
        assert (got.returncode == 0) == success, got.stderr
        assert stock.read_text() == "stock compiler must remain intact\n"
        assert sorted(p.name for p in executable.parent.iterdir()) == sorted([
            "verilator_bin", SIM_VERILATOR_EXECUTABLE])


@pytest.mark.parametrize('valid', [True, False])
def test_native_install_checks_before_atomic_publication(tmp_path, valid):
    prefix = tmp_path / 'eda'
    executable = prefix / 'libexec' / NATIVE_FRONTEND_EXECUTABLE
    executable.parent.mkdir(parents=True)
    old = "#!/bin/sh\necho old\n"
    executable.write_text(old)
    executable.chmod(0o755)
    previous_revision = executable.parent / 'behave-rtl-frontend'
    previous_revision.write_text(old)
    fixture = tmp_path / 'built'
    version = NATIVE_FRONTEND_REVISION if valid else 'wrong revision'
    fixture.write_text(f"#!/bin/sh\necho '{version}'\n")
    fixture.chmod(0o755)
    commands = tmp_path / 'bin'
    commands.mkdir()
    cmake = commands / 'cmake'
    cmake.write_text('''#!/bin/sh
set -eu
if [ "$1" = "--install" ]; then
    mkdir -p "$4/libexec"
    cp "$FRONTEND_FIXTURE" "$4/libexec/behave-rtl-frontend"
fi
''')
    cmake.chmod(0o755)
    with executable.open() as previous:
        got = subprocess.run(['bash', str(ROOT / 'scripts/setup_rtl_frontend.sh')],
            capture_output=True, text=True, check=False,
            env=dict(os.environ, BEHAVE_EDA_ROOT=str(prefix),
                     BEHAVE_RTL_FRONTEND_BUILD_DIR=str(tmp_path / 'build'),
                     FRONTEND_FIXTURE=str(fixture), PATH=str(commands) + os.pathsep + os.defpath))
        assert (got.returncode == 0) == valid, got.stderr
        assert previous.read() == old
    assert executable.read_text() == (fixture.read_text() if valid else old)
    assert previous_revision.read_text() == old
    expected = ['behave-rtl-frontend', NATIVE_FRONTEND_EXECUTABLE]
    if valid:
        expected.append('.frontend-staging')
        assert not list((executable.parent / '.frontend-staging').iterdir())
    assert sorted(p.name for p in executable.parent.iterdir()) == sorted(expected)
