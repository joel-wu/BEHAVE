"""Shared path selection and host discovery, without running an EDA binary."""

import os
from pathlib import Path

import pytest

from behave.core import eda, paths


@pytest.mark.parametrize("marker", ["isolation", "cosim", "solver", "dataset"])
@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize("available", [False, True])
def test_capability_gates_use_markers_not_directory_keywords(
    monkeypatch, tmp_path, marker, marked, available,
):
    from types import SimpleNamespace

    from tests import conftest

    monkeypatch.setattr(conftest, "missing_tools", lambda: ())
    monkeypatch.setattr(conftest, "find_spec", lambda _: object() if available else None)
    monkeypatch.setattr(conftest, "problems_root", lambda: tmp_path if available else tmp_path / "absent")
    monkeypatch.setattr(conftest, "capabilities", lambda: object() if available else
                        conftest.IsolationRefusal("unavailable", "host denies isolation"))
    applied = []
    item = SimpleNamespace(
        nodeid=f"tests/{marker}/test_unit.py::test_policy",
        keywords={"isolation": True, "cosim": True, "solver": True, "dataset": True},
        get_closest_marker=lambda name: object() if marked and name == marker else None,
        iter_markers=lambda name: (),
        add_marker=applied.append,
    )
    conftest.pytest_collection_modifyitems(None, [item])
    assert [mark.name for mark in applied] == (["skip"] if marked and not available else [])


def _tools(directory, names=eda.REQUIRED_TOOLS):
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        tool = directory / name
        tool.write_text("#!/bin/sh\nexit 0\n")
        tool.chmod(0o755)
    return directory


@pytest.fixture
def host_tools(tmp_path, monkeypatch):
    managed = tmp_path / "managed"
    monkeypatch.setenv("BEHAVE_EDA_ROOT", str(managed))
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    interpreter_bin = tmp_path / "python-bin"
    monkeypatch.setattr(eda.sys, "executable", str(interpreter_bin / "python"))
    return managed / "bin", interpreter_bin


def test_child_environment_does_not_mutate_parent(host_tools, monkeypatch):
    managed, interpreter_bin = host_tools
    _tools(managed)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/operator/libraries")
    before = dict(os.environ)

    env = eda.tool_env()

    assert dict(os.environ) == before
    assert env["PATH"].split(os.pathsep) == [str(interpreter_bin), str(managed)]
    assert env["LD_LIBRARY_PATH"] == before["LD_LIBRARY_PATH"]
    assert env["VIRTUAL_ENV"] == eda.sys.prefix
    assert eda.tool_env() == env


def test_complete_operator_toolchain_is_not_replaced(host_tools, tmp_path, monkeypatch):
    managed, interpreter_bin = host_tools
    _tools(managed)
    operator_bin = _tools(tmp_path / "operator-bin")
    monkeypatch.setenv("PATH", str(operator_bin))

    assert eda.ensure_toolchain() is None
    assert os.environ["PATH"] == str(operator_bin)
    assert eda.tool_env()["PATH"].split(os.pathsep) == [str(interpreter_bin), str(operator_bin)]


def test_explicit_parent_discovery_adds_fallback_once(host_tools, tmp_path, monkeypatch):
    managed, _ = host_tools
    _tools(managed)
    partial = _tools(tmp_path / "partial", ("yosys",))
    monkeypatch.setenv("PATH", str(partial))

    assert eda.ensure_toolchain() == managed
    assert os.environ["PATH"].split(os.pathsep) == [str(managed), str(partial)]
    assert eda.ensure_toolchain() is None
    assert eda.missing_tools() == ()


def test_partial_managed_installation_still_reports_missing_tools(host_tools):
    managed, _ = host_tools
    _tools(managed, ("verilator",))

    assert eda.toolchain_bin() == managed
    assert eda.missing_tools() == ("yosys", "iverilog")
    assert eda.ensure_toolchain() is None
    assert os.environ["PATH"] == str(managed)


def test_missing_installation_is_not_created(host_tools):
    managed, interpreter_bin = host_tools

    assert eda.toolchain_bin() is None
    assert eda.ensure_toolchain() is None
    assert eda.missing_tools() == eda.REQUIRED_TOOLS
    assert eda.tool_env()["PATH"] == str(interpreter_bin)
    assert not managed.exists()


def test_interpreter_path_is_not_duplicated(host_tools, monkeypatch):
    managed, interpreter_bin = host_tools
    _tools(managed)
    monkeypatch.setenv("PATH", os.pathsep.join([str(managed), str(interpreter_bin), str(interpreter_bin)]))
    monkeypatch.setenv("VIRTUAL_ENV", "/operator/venv")

    env = eda.tool_env()

    assert env["PATH"].split(os.pathsep) == [str(interpreter_bin), str(managed)]
    assert env["VIRTUAL_ENV"] == "/operator/venv"


def test_unset_path_preserves_os_default_lookup(host_tools, tmp_path, monkeypatch):
    _, interpreter_bin = host_tools
    default_bin = _tools(tmp_path / "default-bin")
    monkeypatch.delenv("PATH")
    monkeypatch.setattr(os, "defpath", str(default_bin))

    assert eda.ensure_toolchain() is None
    assert eda.tool_env()["PATH"].split(os.pathsep) == [str(interpreter_bin), str(default_bin)]
    assert "PATH" not in os.environ


@pytest.mark.parametrize("name,variable,relative", [
    ("data_root", "BEHAVE_DATA_DIR", "data"),
    ("work_root", "BEHAVE_WORK_DIR", "work"),
    ("eda_root", "BEHAVE_EDA_ROOT", "external/oss-cad-suite"),
])
def test_shared_roots_have_defaults_and_explicit_overrides(tmp_path, monkeypatch, name, variable, relative):
    root = getattr(paths, name)
    monkeypatch.delenv(variable, raising=False)
    assert root() == paths.repo_root() / relative

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(variable, "operator-choice")
    assert root() == tmp_path / "operator-choice"
    assert not root().exists()  # Resolving a setting does not create anything.


@pytest.mark.parametrize("layout", ["lib/python3.11/site-packages", "lib/site-packages"])
def test_installed_package_never_infers_virtualenv_as_repository(tmp_path, monkeypatch, layout):
    from behave.core.records import RecordError

    monkeypatch.delenv("BEHAVE_REPO_ROOT", raising=False)
    monkeypatch.setattr(paths, "__file__", str(tmp_path / layout / "behave/core/paths.py"))
    with pytest.raises(RecordError, match="BEHAVE_REPO_ROOT") as error:
        paths.repo_root()
    assert error.value.code == "repository-root-unavailable"


def test_frozen_source_defaults_need_no_git_or_repository_scan(tmp_path, monkeypatch):
    monkeypatch.delenv("BEHAVE_REPO_ROOT", raising=False)
    monkeypatch.setattr(paths, "__file__", str(tmp_path / "frozen/src/behave/core/paths.py"))
    assert paths.repo_root() == tmp_path / "frozen"
    assert not (tmp_path / "frozen").exists()


@pytest.mark.parametrize("name,variable", [
    ("data_root", "BEHAVE_DATA_DIR"), ("work_root", "BEHAVE_WORK_DIR"), ("eda_root", "BEHAVE_EDA_ROOT"),
])
def test_explicit_installed_paths_do_not_consult_source_checkout(tmp_path, monkeypatch, name, variable):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(variable, "explicit-target")
    monkeypatch.setattr(paths, "repo_root", lambda: pytest.fail("an explicit path must not need a checkout"))
    assert getattr(paths, name)() == tmp_path / "explicit-target"
    assert not (tmp_path / "explicit-target").exists()


def test_explicit_repository_root_anchors_defaults_but_not_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BEHAVE_REPO_ROOT", "deployment-assets")
    monkeypatch.delenv("BEHAVE_WORK_DIR", raising=False)
    monkeypatch.delenv("BEHAVE_EDA_ROOT", raising=False)
    monkeypatch.setenv("BEHAVE_DATA_DIR", "separate-data")
    assert paths.repo_root() == tmp_path / "deployment-assets"
    assert paths.work_root() == tmp_path / "deployment-assets/work"
    assert paths.eda_root() == tmp_path / "deployment-assets/external/oss-cad-suite"
    assert paths.data_root() == tmp_path / "separate-data"
    assert not (tmp_path / "deployment-assets").exists()


def test_installed_optional_tool_discovery_has_no_fake_prefix(tmp_path, monkeypatch):
    for name in ("BEHAVE_REPO_ROOT", "BEHAVE_EDA_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(paths, "__file__", str(tmp_path / "installed/behave/core/paths.py"))
    monkeypatch.setenv("PATH", "")
    assert eda.toolchain_bin() is None
    assert eda.missing_tools() == eda.REQUIRED_TOOLS
    assert os.environ["PATH"] == ""


@pytest.mark.parametrize("repository", [False, True])
@pytest.mark.parametrize("eda_prefix", [False, True])
def test_trusted_resource_environment_is_explicit_and_absolute(tmp_path, monkeypatch, repository, eda_prefix):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "__file__", str(tmp_path / "installed/behave/core/paths.py"))
    for name, enabled, value in (("BEHAVE_REPO_ROOT", repository, "assets"),
                                 ("BEHAVE_EDA_ROOT", eda_prefix, "tools")):
        if enabled:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BEHAVE_WORK_DIR", "durable-state")
    monkeypatch.setenv("BEHAVE_DATA_DIR", "private-data")
    monkeypatch.setenv("UNRELATED_PRIVATE_SETTING", "private")
    expected = {"BEHAVE_REPO_ROOT": str(tmp_path / "assets")} if repository else {}
    if eda_prefix or repository:
        expected["BEHAVE_EDA_ROOT"] = str(tmp_path / ("tools" if eda_prefix else "assets/external/oss-cad-suite"))
    assert paths.tool_resource_environment() == expected
    assert not (tmp_path / "assets").exists()


def test_problem_and_benchmark_roots_share_data_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_DATA_DIR", str(tmp_path))
    assert paths.problems_root() == tmp_path / "problems"
    assert paths.benchmarks_root() == tmp_path / "benchmarks"
    assert paths.problem_set("cvdp") == tmp_path / "problems" / "cvdp"


def test_scratch_ignores_ambient_tmpdir_and_accepts_explicit_setting(tmp_path, monkeypatch):
    monkeypatch.delenv("BEHAVE_SCRATCH_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "nfs-temp"))
    assert paths.scratch_root() == Path("/tmp")
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(tmp_path / "local-temp"))
    assert paths.scratch_root() == tmp_path / "local-temp"
