"""RTL runtime policies bind installed code, not the host's import caches."""

from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest

import behave
from behave.isolation import IsolationUnavailable
from behave.isolation.linux import _verify_source
from behave.sim.rtl import runtime_dependencies as dependencies
from behave.sim.rtl import tool_isolation as tools
from behave.sim.rtl import tool_protocol, tool_runtime


@pytest.fixture
def runtime_site(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "batch"))
    site = tmp_path / "site-packages"
    dependency = site / "cocotb"
    dependency.mkdir(parents=True)
    (dependency / "__init__.py").write_text("VALUE = 1\n")
    (dependency / "native.so").write_bytes(b"native-extension")
    cache = dependency / "__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-311.pyc").write_bytes(b"old cache")
    for name in ("pygpi", "cocotb_tools", "find_libpython", "unrelated"):
        root = site / name
        root.mkdir()
        (root / "__init__.py").write_text("")
    package = tmp_path / "behave"
    package.mkdir()
    (package / "__init__.py").write_text("")
    monkeypatch.setattr(behave, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(tool_runtime, "python_runtime", lambda: ((), (), ()))
    monkeypatch.setattr(tool_runtime, "_compiler_version", lambda: "no-host-runtime")
    monkeypatch.setattr(tool_runtime, "eda_root", lambda: tmp_path / "eda")

    def find_spec(name):
        root = site / name
        if not root.is_dir():
            return None
        spec = ModuleSpec(name, None, origin=str(root / "__init__.py"), is_package=True)
        spec.submodule_search_locations = [str(root)]
        return spec

    monkeypatch.setattr(dependencies, "find_spec", find_spec)
    return dependency


def _site_mount():
    _binaries, mounts, _masked = tool_runtime._runtime("simulation")
    return next(mount for mount in mounts if mount.target == "/runtime/site/cocotb")


def _verify(mount):
    _verify_source(
        mount.source, mount.sha256, allow_symlinks=mount.allow_symlinks,
        reuse_attestation=mount.reuse_attestation)


def test_host_bytecode_writes_do_not_invalidate_an_rtl_policy(runtime_site):
    first = _site_mount()
    (runtime_site / "__pycache__" / "__init__.cpython-311.pyc").write_bytes(
        b"a concurrent host import replaced its cache")

    # Before the fix this fails at the actual launch guard with input-changed.
    _verify(first)
    second = _site_mount()
    assert second.sha256 == first.sha256
    assert second.source == first.source
    image = Path(first.source)
    assert not (image / "__pycache__").exists()
    assert (image / "native.so").read_bytes() == b"native-extension"


@pytest.mark.parametrize("filename", ("__init__.py", "native.so"))
def test_installed_code_changes_get_a_new_rtl_policy(runtime_site, filename, tmp_path, monkeypatch):
    first = _site_mount()
    (runtime_site / filename).write_bytes(b"changed semantic input")
    assert _site_mount() == first
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "updated-batch"))
    second = _site_mount()

    assert second.sha256 != first.sha256
    _verify(first)
    _verify(second)
    assert (Path(first.source) / filename).read_bytes() \
        != (Path(second.source) / filename).read_bytes()


def test_tampered_rtl_runtime_image_is_still_refused(runtime_site):
    mount = _site_mount()
    image = Path(mount.source) / "__init__.py"
    image.chmod(0o644)
    image.write_bytes(b"tampered frozen code")
    with pytest.raises(IsolationUnavailable) as caught:
        _verify(mount)
    assert caught.value.refusal.code == "isolation-input-changed"


@pytest.mark.parametrize("profile,names", [
    ("simulation", {"cocotb", "pygpi", "cocotb_tools"}),
    ("eda", {"cocotb", "pygpi", "cocotb_tools", "find_libpython"}),
    ("source-events", {"cocotb", "pygpi", "cocotb_tools", "find_libpython"}),
    ("elaboration", set()),
    ("catalogue", set()),
    ("source-catalogue", set()),
    ("source-observation", set()),
])
def test_only_declared_package_roots_are_scanned(runtime_site, monkeypatch, profile, names):
    selected = []
    original = dependencies.python_package

    def freeze(source, target):
        path = Path(source)
        assert path.parent == runtime_site.parent
        assert path.name in names, "a stage scanned an undeclared dependency"
        selected.append(path.name)
        return original(source, target)

    monkeypatch.setattr(dependencies, "python_package", freeze)
    first = dependencies.python_dependency_mounts(profile)
    assert set(selected) == names
    assert {mount.target for mount in first} == {f"/runtime/site/{name}" for name in names}
    assert all(Path(mount.source).parent.name == "images" for mount in first)
    (runtime_site.parent / "unrelated" / "__init__.py").write_text("CHANGED = True\n")
    assert dependencies.python_dependency_mounts(profile) == first


def test_simulation_does_not_mount_the_python_environment(runtime_site):
    _binaries, mounts, _masked = tool_runtime._runtime("simulation")
    assert {mount.target for mount in mounts if mount.target.startswith("/runtime/site")} \
        == {"/runtime/site/cocotb", "/runtime/site/pygpi", "/runtime/site/cocotb_tools"}
    assert all(Path(mount.source) != runtime_site.parent for mount in mounts)


@pytest.mark.isolation
@pytest.mark.eda("slang")
@pytest.mark.parametrize("profile, executable", [
    ("source-catalogue", "/tools/slang"),
    ("source-observation", "/tools/behave-rtl-frontend"),
])
def test_source_frontends_run_without_build_tools_or_site_packages(tmp_path, profile, executable):
    binaries, mounts, _ = tool_runtime._runtime(profile)
    assert executable in {item.target for item in binaries}
    assert not any(item.target.startswith("/runtime/site/") for item in mounts)
    # Anonymous source-scope paths use Verilator's JSON-only layout witness.
    # That frontend dependency is not permission to include a C++ build stack.
    script = f"expected_verilator = {profile == 'source-observation'!r}\n" + """
from pathlib import Path
import importlib.util
import behave.sim.rtl.shared_frontend
import behave.sim.rtl._prepare_native_events
for name in ('cocotb', 'pytest', 'numpy', 'z3'):
    assert importlib.util.find_spec(name) is None, name
assert Path('/tools/verilator').exists() == expected_verilator
for name in ('yosys', 'gcc', 'g++', 'make', 'z3'):
    assert not Path('/tools', name).exists(), name
assert not Path('/usr/bin/gcc').exists()
print('source-only-runtime-ok')
"""
    execution = tool_protocol.require_completed(tools.run_workspace(
        tmp_path, (tool_protocol.ToolGroup((tool_protocol.ToolCommand(
            "imports", ("/runtime/python/bin/python", "-S", "-c", script)),)),),
        role="source-runtime-imports", runtime_profile=profile))
    assert execution.commands[0]["returncode"] == 0, execution.as_dict()


@pytest.mark.parametrize("shape", ["missing", "namespace", "ambiguous"])
def test_invalid_dependency_never_falls_back_to_site_packages(runtime_site, monkeypatch, shape):
    def find_spec(name):
        if shape == "missing":
            return None
        spec = ModuleSpec(name, None, origin=None if shape == "namespace" else "__init__.py")
        spec.submodule_search_locations = [str(runtime_site)] * (2 if shape == "ambiguous" else 1)
        return spec

    monkeypatch.setattr(dependencies, "find_spec", find_spec)
    with pytest.raises(FileNotFoundError, match="cocotb"):
        dependencies.python_dependency_mounts("simulation")


def test_packages_may_live_in_different_installation_roots(runtime_site, tmp_path, monkeypatch):
    original = dependencies.find_spec
    pygpi = tmp_path / "other-install" / "pygpi"
    pygpi.mkdir(parents=True)
    (pygpi / "__init__.py").write_text("VALUE = 2\n")

    def find_spec(name):
        spec = original(name)
        if name == "pygpi":
            spec.origin = str(pygpi / "__init__.py")
            spec.submodule_search_locations = [str(pygpi)]
        return spec

    monkeypatch.setattr(dependencies, "find_spec", find_spec)
    mounts = dependencies.python_dependency_mounts("simulation")
    assert len(mounts) == 3
    assert (Path(mounts[1].source) / "__init__.py").read_text() == "VALUE = 2\n"


def test_public_profiles_share_only_the_approved_fp_module(runtime_site):
    from behave.sim.behavior import LIBRARY_DIR

    mounts = [next(m for m in dependencies.python_dependency_mounts(profile)
                   if m.target == "/runtime/site/fp_lib.py")
              for profile in ("development", "testbench-build", "testbench")]
    assert mounts[0] == mounts[1] == mounts[2]
    assert Path(mounts[0].source).read_bytes() == (LIBRARY_DIR / "fp_lib.py").read_bytes()
    _, runtime, _ = tool_runtime._runtime("testbench")
    assert not any(m.target.startswith("/runtime/behave") for m in runtime)
    assert "/runtime/rtl-tool-job.py" in {m.target for m in runtime}


@pytest.mark.isolation
@pytest.mark.eda("verilator", "yosys")
@pytest.mark.parametrize("profile", ["simulation", "eda", "testbench-build", "testbench"])
def test_real_guest_imports_declared_dependencies_without_host_site(tmp_path, profile):
    # Exercise the production launcher and the installed native Cocotb extension,
    # not the host Python import path or a mocked mount. The production replay
    # tests additionally consume the package's makefiles and shared libraries.
    imports = "import cocotb, cocotb.simulator, pygpi.entry, cocotb_tools._coverage\n"
    imports += "cocotb_tools._coverage.start_cocotb_library_coverage([])\n"
    if profile in {"eda", "testbench-build"}:
        imports += "import cocotb_tools.config, find_libpython\n"
        imports += "assert find_libpython.find_libpython()\n"
    script = imports + """
import importlib.util
assert cocotb.__file__.startswith('/runtime/site/cocotb/')
for name in ('pytest', 'numpy', 'matplotlib', 'openai', 'z3'):
    assert importlib.util.find_spec(name) is None, name
print('declared-runtime-ok')
"""
    if profile in {"testbench-build", "testbench"}:
        script += """
from pathlib import Path
import fp_lib
assert importlib.util.find_spec('behave') is None
assert fp_lib.fp32_add(1, 1) == 2
assert fp_lib.__file__ == '/runtime/site/fp_lib.py'
assert 'def fp32_add(' in Path(fp_lib.__file__).read_text()
"""
    else:
        script += "import behave.sim.rtl.replay_runtime\n"
    executed = tool_protocol.require_completed(tools.run_workspace(
        tmp_path,
        (tool_protocol.ToolGroup((tool_protocol.ToolCommand(
            "imports", ("/runtime/python/bin/python", "-c", script)),)),),
        role="runtime-dependency-imports", runtime_profile=profile))
    assert len(executed.commands) == 1
    assert executed.commands[0]["returncode"] == 0, executed.as_dict()
