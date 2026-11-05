"""Development selection must not weaken the complete release selection."""

import os
import subprocess
import sys
from pathlib import Path


def _collect(test_file, marker):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", "-o", "addopts=", "-m", marker,
         test_file],
        cwd=root, capture_output=True, text=True, timeout=60, check=False,
        env={key: value for key, value in os.environ.items()
             if key != "PYTEST_ADDOPTS"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {line for line in result.stdout.splitlines()
            if line.startswith(test_file + "::")}


def test_pure_checks_and_installed_lock_check_are_separate_release_members():
    test_file = "tests/sim/test_locked_environment.py"
    pure = _collect(test_file,
        "not solver and not eda and not isolation and not cosim "
        "and not locked_env and not dataset")
    locked = _collect(test_file, "locked_env and not dataset")
    release = _collect(test_file, "not dataset")
    assert pure
    assert locked == {
        test_file + "::test_the_active_verifier_environment_is_exactly_the_eda_dev_lock_closure"
    }
    assert pure.isdisjoint(locked)
    assert pure | locked == release


def test_exact_task_boundary_checks_are_selected_in_the_solver_lane():
    test_file = "tests/sim/test_behavior_task_boundary.py"
    solver = _collect(test_file, "solver and not dataset")
    pure = _collect(test_file,
        "not solver and not eda and not isolation and not cosim "
        "and not locked_env and not dataset")
    release = _collect(test_file, "not dataset")

    assert solver == {test_file + "::" + name for name in (
        "test_task_lowering_reconstructs_recursive_multiendpoint_outputs",
        "test_task_signature_represents_an_empty_bounded_output_batch",
        "test_task_lowering_uses_cfg_facts_inside_nested_data_dependent_loops",
        "test_semantic_batch_capacity_precedes_the_exact_representation_budget",
    )}
    assert pure.isdisjoint(solver)
    assert pure | solver == release


def test_concrete_atomic_fixtures_do_not_require_the_solver_test_module():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
sys.modules['z3'] = None
from tests.support.atomic_behavior import _contract, _transaction_stimulus
assert len(_contract().inputs()) == 2
assert len(_transaction_stimulus().slots) == 3
assert 'tests.sim.test_behavior_atomic_exact' not in sys.modules
"""], cwd=root, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_startup_analysis_cases_declare_the_solver_dependency():
    test_file = "tests/sim/test_behavior_startup_failures.py"
    solver = _collect(test_file, "solver and not dataset")
    assert solver == {
        test_file + "::test_source_startup_failure_reaches_a_persisted_zero[True-" + stage + "]"
        for stage in ("load", "constructor", "reset")
    } | {
        test_file + "::test_construction_authentication_belongs_after_score_persistence[True]"
    }
