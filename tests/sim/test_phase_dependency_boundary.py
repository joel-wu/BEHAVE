"""Mechanical import direction between generation, replay, and analysis phases."""

from __future__ import annotations

import ast
from pathlib import Path

SIM = Path(__file__).resolve().parents[2] / "src" / "behave" / "sim"


def _package(path):
    return ["behave", "sim", *path.relative_to(SIM).with_suffix("").parts[:-1]]


def _imports(path):
    package = _package(path)
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package) - (node.level - 1)
                parts = package[:max(0, keep)]
                if node.module:
                    parts.extend(node.module.split("."))
                module = ".".join(parts)
            else:
                module = node.module or ""
            yield module
            yield from (module + "." + alias.name for alias in node.names)


def _forbidden(paths, prefixes):
    return [
        (str(path.relative_to(SIM)), module)
        for path in paths
        for module in _imports(path)
        if any(module == prefix or module.startswith(prefix + ".")
               for prefix in prefixes)
    ]


def test_common_generation_scheduler_has_no_replay_or_evidence_dependency():
    scheduler = SIM / "generation.py"
    forbidden = {
        "behave.sim.behavior",
        "behave.sim.core.closure",
        "behave.sim.core.compare",
        "behave.sim.core.conditional_evidence",
        "behave.sim.core.driver",
        "behave.sim.core.evidence",
        "behave.sim.core.reference",
        "behave.sim.core.run_record",
        "behave.sim.engines",
        "behave.sim.reset_probe",
        "behave.sim.rtl",
    }

    assert _forbidden((scheduler,), forbidden) == []


def test_replay_services_do_not_import_solver_or_closure_code():
    replay = (
        SIM / "evaluation.py",
        SIM / "core" / "driver.py",
        SIM / "core" / "reference.py",
        SIM / "behavior" / "isolated.py",
        SIM / "reference_isolated.py",
        SIM / "rtl" / "artifact.py",
        SIM / "rtl" / "batch_replay.py",
        SIM / "rtl" / "initial_state.py",
        SIM / "rtl" / "initial_request.py",
        SIM / "rtl" / "request_layout.py",
        SIM / "rtl" / "atomic_request.py",
        SIM / "rtl" / "atomic_monitors.py",
        SIM / "rtl" / "task_request.py",
        SIM / "rtl" / "replay_startup.py",
        SIM / "rtl" / "register_init.py",
        SIM / "rtl" / "source_handles.py",
        SIM / "rtl" / "source_trace.py",
        SIM / "rtl" / "replay_plan.py",
        SIM / "rtl" / "replay_clock.py",
        SIM / "rtl" / "replay_runtime.py",
        SIM / "rtl" / "native_observation.py",
        SIM / "rtl" / "native_events.py",
        SIM / "rtl" / "source_contexts.py",
        SIM / "rtl" / "source_bindings.py",
        SIM / "rtl" / "source_plan.py",
        SIM / "rtl" / "source_loops.py",
        SIM / "rtl" / "source_syntax.py",
        SIM / "rtl" / "source_calls.py",
        SIM / "rtl" / "elaboration.py",
        SIM / "rtl" / "trigger_support.py",
    )
    forbidden = {
        "behave.sim.symbolic.encoder",
        "behave.sim.behavior.residual",
        "behave.sim.core.closure",
        "behave.sim.core.query",
        "behave.sim.rtl.task_symbolic",
        "behave.sim.rtl.observation_audit",
        "z3",
    }

    assert _forbidden(replay, forbidden) == []


def test_production_replay_does_not_reconstruct_goal_hits_from_snapshots():
    assert not (SIM / "rtl" / "sampled_observation.py").exists()
    assert _forbidden((SIM / "rtl" / "replay_runtime.py",), {
        "behave.sim.rtl.site_sampling", "tests",
    }) == []


def test_public_generation_and_replay_have_distinct_owners():
    from behave.sim import api, evaluation, generation, goal_policy

    assert api.generate_stimuli is generation.generate_stimuli
    assert not hasattr(evaluation, "generate_stimuli")
    assert api.evaluation_policy is goal_policy.evaluation_policy
    assert api.evaluation_manifest is goal_policy.evaluation_manifest
    assert not hasattr(evaluation, "evaluation_manifest")
    assert _forbidden((SIM / "generation.py",), {
        "behave.sim.evaluation", "behave.sim.api",
    }) == []
    tree = ast.parse((SIM / "evaluation.py").read_text())
    assert not {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} & {
        "generate_stimuli", "generate_independently", "_bind_generated_set"}


def test_analysis_protocols_do_not_import_generation_adapters_or_runners():
    analysis = (
        SIM / "analysis_service.py",
        SIM / "core" / "analysis.py",
        SIM / "behavior" / "analysis.py",
        SIM / "behavior" / "analysis_job.py",
        SIM / "behavior" / "atomic_analysis.py",
        SIM / "behavior" / "transaction_analysis.py",
        SIM / "rtl" / "analysis.py",
        SIM / "rtl" / "analysis_job.py",
    )
    forbidden = {
        "behave.sim.engines",
        "behave.sim.generation",
        "behave.sim.generation_protocol",
    }

    assert _forbidden(analysis, forbidden) == []


def test_phase_services_do_not_depend_on_the_public_facade():
    services = tuple(SIM / name for name in (
        "command.py", "evaluation.py", "evaluation_policy.py",
        "analysis_service.py", "analysis_report.py", "preparation.py",
    ))
    assert _forbidden(services, {"behave.sim.api"}) == []


def test_term_proofs_do_not_depend_on_representation_planning():
    proofs = SIM / "symbolic" / "term_bounds.py"
    assert _forbidden((proofs,), {
        "behave.sim.symbolic.plan",
        "behave.sim.symbolic.encoder",
        "behave.sim.symbolic.exact_solve",
        "behave.sim.core.query",
    }) == []


def test_concrete_request_codecs_do_not_load_symbolic_products():
    import subprocess
    import sys

    script = '''
import importlib, sys
blocked = ('z3', 'behave.sim.symbolic',
           'behave.sim.rtl.protocol_relation', 'behave.sim.rtl.task_symbolic')
class NoSymbolicProducts:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(blocked):
            raise AssertionError(fullname)
sys.meta_path.insert(0, NoSymbolicProducts())
for name in ('request_layout', 'task_request', 'atomic_request', 'settled_request'):
    importlib.import_module('behave.sim.rtl.' + name)
from behave.sim.rtl.request_layout import coordinate_bounds
assert dict(coordinate_bounds([('field', (0, 7))])) == {'field': (0, 7)}
assert not any(name.startswith(blocked) for name in sys.modules)
'''
    result = subprocess.run((sys.executable, '-c', script), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_import_guard_sees_from_package_import_module():
    path = SIM / "_import_guard_test.py"
    # Parse synthetic source without adding a production module.
    from unittest.mock import patch

    with patch.object(Path, "read_text", return_value="from . import api as facade"):
        assert _forbidden((path,), {"behave.sim.api"}) == [
            (path.name, "behave.sim.api")]


def test_finite_goal_semantics_does_not_depend_on_provider_or_execution_policy():
    from behave.sim.core import finite_goal, goal_provider

    assert _forbidden((SIM / "core" / "finite_goal.py",), {
        "behave.sim.api", "behave.sim.generation", "behave.sim.evaluation",
        "behave.sim.core.goal_provider", "behave.sim.core.analysis",
        "behave.sim.core.query", "behave.sim.core.outcome", "z3",
    }) == []
    for name in finite_goal.__all__:
        assert getattr(goal_provider, name) is getattr(finite_goal, name)


def test_rtl_generation_and_analysis_share_construction_not_protocols():
    queries = tuple(_imports(SIM / "engines" / "rtl" / "generation_job.py"))
    analysis = tuple(_imports(SIM / "rtl" / "analysis_job.py"))
    context = tuple(_imports(SIM / "rtl" / "exact_job_context.py"))

    assert "behave.sim.rtl.exact_job_instance" in analysis
    assert "behave.sim.rtl.analysis_job" not in queries
    assert "behave.sim.rtl.exact_job_instance" not in queries
    assert "behave.sim.engines.rtl.generation_job" not in analysis
    assert "behave.sim.engines.rtl.generation_job" not in context
