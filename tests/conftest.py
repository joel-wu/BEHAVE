"""Shared test configuration.

Optional capabilities are expressed as markers rather than inferred from file
names, so the same selection works across every component:

    solver      requires the z3 Python package
    eda         requires the external tools named by the marker
    isolation   requires the production namespace backend
    cosim       compatibility shorthand for complete EDA plus isolation
    dataset     requires the population under data/problems
    locked_env  validates the complete pinned eda+dev environment
    pipelines   requires optional external-method packages or pinned sources

Unavailable toolchain, solver and isolation capabilities skip their tests.
Pipeline dependencies are installed explicitly before selecting that lane;
missing packages or sources are errors, not a passing optional check.

The independently selectable development lanes are:

    pytest -m "not solver and not eda and not isolation and not cosim and not locked_env and not dataset and not pipelines"
    pytest -m "solver and not eda and not isolation and not cosim and not locked_env and not dataset and not pipelines"
    pytest -m "eda and not isolation and not cosim and not locked_env and not dataset and not pipelines" -n 1
    pytest -m "isolation and not cosim and not locked_env and not dataset and not pipelines" -n 1
    pytest -m "cosim and not locked_env and not dataset and not pipelines" -n 1
    pytest -m "locked_env and not dataset and not pipelines"
    pytest -m pipelines
    pytest -m dataset

The first six selections partition the non-dataset, non-pipeline tests. Markers are capability
requirements: a test in the isolation selection may also need its marked EDA
tools or solver. An empty selection is not a passing test run.
Selecting ``locked_env`` checks the installed environment against the lock file.
An incorrect environment fails that check rather than skipping it.

Choose an explicit worker count within the available allocation. Pytest workers
and the simulator processes each test starts share those resources.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from importlib.util import find_spec

import pytest

from behave.core.eda import REQUIRED_TOOLS, missing_tools
from behave.core.paths import problems_root
from behave.isolation import IsolationRefusal, capabilities
from behave.sim.behavior.isolated import _testing_worker_launcher
from behave.sim.worker import _testing_job_runner
from tests.support.worker import run_direct_job

# Names ``-m`` may use besides the markers registered in pyproject.toml.
_BUILTIN_MARKERS = frozenset({
    "filterwarnings", "parametrize", "skip", "skipif", "usefixtures", "xfail",
})
_BOOLEAN_WORDS = frozenset({"and", "or", "not", "True", "False", "None"})


def pytest_configure(config):
    """Reject a ``-m`` expression that names something no test can carry.

    An unregistered name in ``-m`` is not an error to pytest: it simply
    evaluates false for every test. ``-m "not slow"`` therefore selects the
    whole suite rather than reporting that no ``slow`` marker exists, which
    turns a mistyped filter into a silent run of everything the filter was
    meant to exclude.
    """
    expression = (config.option.markexpr or "").strip()
    if not expression:
        return
    registered = {entry.split(":", 1)[0].split("(", 1)[0].strip()
                  for entry in config.getini("markers")}
    known = registered | _BUILTIN_MARKERS | _BOOLEAN_WORDS
    unknown = sorted({name for name in re.findall(r"[A-Za-z_]\w*", expression)
                      if name not in known})
    if unknown:
        raise pytest.UsageError(
            f"-m names unregistered marker(s): {', '.join(unknown)}. "
            f"Registered: {', '.join(sorted(registered))}."
        )

# The dual engine defaults to eight fork workers, each holding its own loaded
# behavior model and z3 state. Combined with cosim-level parallelism this peaks
# in the multi-gigabyte range, which is unnecessary for short tests. Set before
# any test imports the engine, and inherited by cosim subprocesses.
os.environ.setdefault("DUAL_PARALLEL_WORKERS", "1")


@pytest.fixture(scope="session", autouse=True)
def _unit_isolation_backend():
    """Make the weaker unit backend an explicit pytest-owned capability.

    Namespace-capable hosts exercise production.  A restricted developer/CI sandbox
    injects the test-only direct worker only for unit semantics; the adversarial
    isolation suite capability-gates itself and runs production on the Slurm lane.
    """
    available = capabilities()
    if type(available) is not IsolationRefusal:
        yield
        return

    def launch(child_program):
        return subprocess.Popen(
            [sys.executable, "-u", "-c", child_program],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
            },
        )

    with _testing_worker_launcher(launch), \
            _testing_job_runner(run_direct_job):
        yield


@pytest.fixture
def search_lifetime():
    """The finite owning Search stage the public scheduler gives each direction.

    Standalone query primitives no longer invent a per-query timeout. Modules
    that call providers directly opt in with
    ``pytest.mark.usefixtures("search_lifetime")``.
    """
    from behave.sim.search_policy import SearchBudget, SearchPolicy, search_scope

    with search_scope(SearchBudget(SearchPolicy()).direction(1)):
        yield


def pytest_collection_modifyitems(config, items):
    absent_tools = missing_tools()
    isolation = capabilities()
    have_problems = problems_root().is_dir()
    have_solver = find_spec("z3") is not None

    if absent_tools:
        cosim_reason = (
            f"EDA toolchain unavailable: {', '.join(absent_tools)} "
            f"(run scripts/setup_eda.sh)")
    elif type(isolation) is IsolationRefusal:
        cosim_reason = (
            f"production RTL isolation unavailable: {isolation.code}: "
            f"{isolation.why}")
    else:
        cosim_reason = "cosim capability is available"
    skip_cosim = pytest.mark.skip(reason=cosim_reason)
    skip_solver = pytest.mark.skip(
        reason="z3-solver is unavailable (install behave[solver])")
    skip_isolation = pytest.mark.skip(
        reason=(
            "production isolation is available"
            if type(isolation) is not IsolationRefusal
            else f"production isolation unavailable: {isolation.code}: {isolation.why}"
        )
    )
    skip_dataset = pytest.mark.skip(reason=f"problem sets not found at {problems_root()}")

    for item in items:
        if not have_solver and item.get_closest_marker("solver") is not None:
            item.add_marker(skip_solver)
        for marker in item.iter_markers("eda"):
            required = tuple(marker.args) or REQUIRED_TOOLS
            invalid = [tool for tool in required
                       if not isinstance(tool, str) or not tool]
            if invalid:
                raise pytest.UsageError(
                    f"{item.nodeid}: eda marker arguments must be tool names")
            missing = tuple(tool for tool in required if shutil.which(tool) is None)
            if missing:
                item.add_marker(pytest.mark.skip(
                    reason=f"EDA tools unavailable: {', '.join(missing)} "
                           f"(run scripts/setup_eda.sh)"))
        if type(isolation) is IsolationRefusal and item.get_closest_marker("isolation") is not None:
            item.add_marker(skip_isolation)
        if (absent_tools or type(isolation) is IsolationRefusal) \
                and item.get_closest_marker("cosim") is not None:
            item.add_marker(skip_cosim)
        if not have_problems and item.get_closest_marker("dataset") is not None:
            item.add_marker(skip_dataset)
