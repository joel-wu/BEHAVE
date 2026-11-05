"""Mechanical dependency direction for the simulator's public boundary."""

from __future__ import annotations

import ast
import inspect
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from behave.sim import api

ROOT = Path(__file__).resolve().parents[2] / "src" / "behave"
SIM = ROOT / "sim"
FORBIDDEN_FROM_SIM = {"agent", "cli", "data", "eval", "train", "ppa", "task_execution"}


@pytest.mark.parametrize("module", (
    "behave.sim.generation_protocol", "behave.sim.query_plan",
    "behave.sim.generation_feedback",
))
def test_search_contracts_work_without_execution_or_solver_imports(module):
    program = f"""
import importlib, sys
class NoExecution:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {{'behave.sim.generation', 'behave.sim.command',
                        'behave.sim.evaluation', 'behave.sim.search_parallel', 'z3'}} \
                or fullname.startswith('behave.sim.engines'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, NoExecution())
importlib.import_module({module!r})
from behave.sim.generation_protocol import (
    ProbeSelection, QueryPlan, QueryTemplate, ReplayFeedback, StimulusField)
digest = 'a' * 64
field = StimulusField('input', 0, 0, 1)
template = QueryTemplate(('g',), 'site', (field,), 0, catalogue_digest=digest)
plan = QueryPlan((template,), digest)
restored = QueryPlan.from_dict(plan.as_dict())
assert restored.as_dict() == plan.as_dict()
feedback = ReplayFeedback('b' * 64, digest, 'c' * 64, ('g',))
selection = ProbeSelection(restored, digest, replay=feedback)
assert selection.requests[0].identity == template.request().identity
assert selection.as_dict()['replay'] == feedback.as_dict()
"""
    subprocess.run([sys.executable, "-c", program], check=True, capture_output=True, timeout=30)


def test_search_contract_legacy_exports_are_identical_not_wrappers():
    from behave.sim import generation, generation_feedback, generation_protocol, query_plan

    for module, names in (
        (generation, ("StimulusField", "ProbeRequest", "ProbeSelection",
                      "GenerationProvider", "stimulus_bytes")),
        (query_plan, ("QueryTemplate", "QueryPlan")),
        (generation_feedback, ("ReplayFeedback",)),
    ):
        for name in names:
            assert getattr(module, name) is getattr(generation_protocol, name)
            assert getattr(module, name).__module__ == generation_protocol.__name__
    for name in ("StimulusField", "ProbeRequest", "ProbeSelection", "GenerationProvider"):
        assert getattr(api, name) is getattr(generation_protocol, name)


def test_production_search_records_depend_on_contract_owner():
    old_owners = {
        "behave.sim.generation": {"StimulusField", "ProbeRequest", "ProbeSelection",
                                  "GenerationProvider", "stimulus_bytes"},
        "behave.sim.query_plan": {"QueryTemplate", "QueryPlan"},
        "behave.sim.generation_feedback": {"ReplayFeedback"},
    }
    for path in SIM.rglob("*.py"):
        module = "behave.sim." + ".".join(path.relative_to(SIM).with_suffix("").parts)
        package = module.removesuffix(".__init__") if path.name == "__init__.py" \
            else module.rsplit(".", 1)[0]
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom):
                continue
            owner = (importlib.util.resolve_name("." * node.level + (node.module or ""), package)
                     if node.level else node.module)
            forbidden = old_owners.get(owner, set())
            assert not forbidden.intersection(alias.name for alias in node.names), (path, node.lineno)


@pytest.mark.parametrize("path,retired", [
    ("behavior/exact_service.py", {"_decode_verdict"}),
    ("rtl/analysis_job.py", {"_query_image", "decode_query_verdict"}),
])
def test_query_verdict_records_have_no_second_backend_owner(path, retired):
    tree = ast.parse((SIM / path).read_text())
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not retired & names


@pytest.mark.parametrize("path", ["behavior/exact_service.py", "rtl/analysis.py"])
def test_exact_frontends_decode_the_shared_verdict_record(path):
    tree = ast.parse((SIM / path).read_text())
    assert any(isinstance(node, ast.Call) and ast.unparse(node.func) == "Verdict.from_record"
               for node in ast.walk(tree))


@pytest.mark.parametrize("path,retired", [
    ("baseline.py", {"_controls"}),
    ("generation.py", {"_refusal"}),
    ("core/analysis.py", {"_refusal"}),
    ("behavior/audit.py", {"_typed_equal", "_ir_value_equal"}),
    ("behavior/exact_solver.py", {"_qfbv"}),
    ("behavior/analysis_job.py", {"_replay_image", "_query_image"}),
    ("core/stimulus.py", {"_pairs"}),
    ("engines/rtl/generation_job.py", {"_request_from_dict"}),
    ("command.py", {"_bind_analysis_capability", "_catalogue_from_exact", "_catalogue_from_analysis_request"}),
    ("public_handle.py", {"_rtl_analysis_request_for_providers"}),
])
def test_pure_forwarding_shims_do_not_return_as_second_owners(path, retired):
    tree = ast.parse((SIM / path).read_text())
    definitions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    uses = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not retired & (definitions | uses)


@pytest.mark.parametrize('path,forbidden', [
    ('analysis_report.py', {'behavior', 'rtl', 'engines'}),
    ('analysis_service.py', {'command', 'public_handle', 'analysis_report'}),
])
def test_analysis_binding_and_reporting_have_one_way_dependencies(path, forbidden):
    for node in ast.walk(ast.parse((SIM / path).read_text())):
        if isinstance(node, ast.ImportFrom):
            parts = (node.module or '').split('.')
            if not node.module:
                parts += [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            parts = [part for alias in node.names for part in alias.name.split('.')]
        else:
            continue
        assert not forbidden.intersection(parts), (path, node.lineno)


@pytest.mark.parametrize("module", ("behave.sim.core.resources",))
def test_step_and_resource_owners_do_not_eagerly_load_solver_or_engines(module):
    program = f"""
import importlib, sys
importlib.import_module({module!r})
assert 'z3' not in sys.modules
assert not any(name.startswith('behave.sim.engines') for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", program], check=True, capture_output=True, timeout=30)


def test_direct_test_worker_and_file_transport_are_not_production_interfaces():
    from behave.sim import worker

    for retired in ("_where", "_run_direct_test_job", "_kill_group"):
        assert not hasattr(worker, retired)
    assert not hasattr(worker, "subprocess")
    for argv in ([], ["job.json", "out.json"], ["--stdio", "extra"]):
        with pytest.raises(SystemExit, match="worker --stdio"):
            worker.main(argv)


def test_scoring_does_not_keep_the_old_count_only_verdict():
    from importlib.util import find_spec

    from behave.sim.evaluation_policy import verdict

    assert find_spec("behave.sim.verdict") is None
    assert callable(verdict)


def test_harness_owns_build_rendering_without_a_second_replay_driver():
    from importlib.util import find_spec

    from behave.core import runtime_defaults
    from behave.sim import harness

    assert set(harness.__all__) == {"TEMPLATE_DIR", "generate_makefile", "render_template"}
    for name in ("generate_replay_tb", "generate_replay_tb_multiclock", "run_replay",
                 "_domain_specs", "DOMAIN_PERIODS_NS", "MULTICLOCK_REPLAY_VERSION"):
        assert not hasattr(harness, name), name
    for name in ("HANDSHAKE_DELAY_MAX", "HANDSHAKE_DELAY_WITNESSES", "STREAM_STALL_PREFIX",
                 "CLOCK_PERIODS_NS"):
        assert not hasattr(runtime_defaults, "SIM_LEGACY_" + name), name
    for name in ("replay_tb.py.tmpl", "replay_tb_multiclock.py.tmpl"):
        assert not (harness.TEMPLATE_DIR / name).exists(), name
    assert find_spec("behave.sim.core.witness") is None


def test_unused_state_growth_job_and_heuristic_are_retired():
    from importlib.util import find_spec

    from behave.core import runtime_defaults
    from behave.sim import job_isolation, worker

    assert find_spec("behave.sim.state_bounds") is None
    assert "state_growth" not in worker.JOBS
    assert "state_growth" not in job_isolation._JOB_POLICIES
    assert not hasattr(worker, "_job_state_growth")
    assert not hasattr(runtime_defaults, "SIM_LEGACY_STATE_HORIZONS")
    assert not hasattr(runtime_defaults, "SIM_LEGACY_STATE_SEED")


def test_retired_rtl_batch_policy_does_not_leave_a_second_target_selector():
    from behave.core import runtime_defaults
    from behave.sim import job_isolation, worker
    from behave.sim.rtl import site_target

    assert not hasattr(runtime_defaults, "SIM_RTL_RANDOM_SECONDS")
    for name in ("RTLModelTargetSummary", "fallback_targets"):
        assert not hasattr(site_target, name)
        assert name not in site_target.__all__
    assert not hasattr(worker, "_job_rtl_model_targets")
    for registry in (worker.JOBS, job_isolation._JOB_POLICIES):
        assert "rtl_model_targets" not in registry
    # The active source-query session remains.
    assert "rtl_source_generation_session" in job_isolation._JOB_POLICIES


def test_retired_probe_report_does_not_remove_real_reset_monitoring():
    from importlib.util import find_spec

    from behave.core import runtime_defaults
    from behave.sim.rtl.artifact import RTLArtifact

    assert find_spec("behave.sim.reset_probe") is None
    assert find_spec("behave.sim.core.reset_monitor") is not None
    assert callable(RTLArtifact.reset_audit)
    for suffix in ("DIAGNOSTIC_SECONDS", "REPORT_RESERVE_SECONDS", "SOLVER_SECONDS",
                   "FALLBACK_WITNESSES", "STREAM_WITNESSES"):
        assert not hasattr(runtime_defaults, "SIM_LEGACY_" + suffix)


def test_result_types_do_not_export_or_import_execution_orchestration():
    script = """
import sys
from behave.sim import authority
for name in ('prepare', 'evaluate_problem', 'evaluate_admitted', '_Admitted',
             '_reference', 'canonical_set', 'stimuli_from_witnesses'):
    assert not hasattr(authority, name), name
for module in ('command', 'production_generation', 'rtl.artifact', 'stimulus_set'):
    assert 'behave.sim.' + module not in sys.modules, module
assert authority.RESULT_VERSION == 'authority/104'
assert authority.Outcome.CONCRETE.authoritative
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_state_declarations_do_not_load_ir_emitters_or_execution_owners():
    script = """
import sys
from behave.sim.behavior import lower_state
assert callable(lower_state.declare_state)
assert callable(lower_state.bind_entry)
assert callable(lower_state.bind_input_views)
assert callable(lower_state.contract_at_dynamic_path)
for name in ('behavior.lowering', 'behavior.lower_graph', 'command',
             'production_generation', 'rtl.artifact'):
    assert 'behave.sim.' + name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_return_initialization_does_not_load_lowerer_or_execution_owners():
    script = """
import sys
from behave.sim.behavior import lower_returns
assert callable(lower_returns.initialize_outputs)
assert callable(lower_returns.zero_shape)
assert callable(lower_returns.zero_type)
for name in ('behavior.lowering', 'behavior.lower_graph', 'command',
             'production_generation', 'rtl.artifact'):
    assert 'behave.sim.' + name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_optional_sequence_has_one_owner_without_a_lowerer_mixin():
    script = """
import importlib.util
import sys
from behave.sim.behavior import lower_returns, source_options
assert not hasattr(source_options, 'OptionSequence')
assert importlib.util.find_spec('behave.sim.behavior.lower_option_sequences') is None
for name in ('at', 'literal', 'allocate', 'assign', 'store'):
    assert callable(getattr(lower_returns.OptionSequence, name)), name
for name in ('behavior.lowering', 'behavior.lower_graph', 'command', 'production_generation'):
    assert 'behave.sim.' + name not in sys.modules, name
from behave.sim.behavior.lowering import _Lowerer
assert all(base.__name__ != 'OptionSequenceLowering' for base in _Lowerer.__mro__)
for name in ('_option_sequence_literal', '_new_option_sequence',
             '_option_sequence_copy', '_option_sequence_store'):
    assert not hasattr(_Lowerer, name), name
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_mapping_operations_do_not_require_inherited_lowerer_methods():
    script = """
import sys
from behave.sim.behavior import lower_mappings
assert not hasattr(lower_mappings, 'MappingLowering')
for name in ('allocate', 'contains', 'subscript', 'store', 'call'):
    assert callable(getattr(lower_mappings.MappingBuilder, name)), name
for name in ('behavior.lowering', 'behavior.lower_graph', 'command', 'production_generation'):
    assert 'behave.sim.' + name not in sys.modules, name
from behave.sim.behavior.lowering import _Lowerer
assert all(base.__name__ != 'MappingLowering' for base in _Lowerer.__mro__)
for name in ('_new_mapping_builder', '_mapping_receiver', '_mapping_integer',
             '_mapping_arguments', '_mapping_find', '_mapping_read',
             '_mapping_store', '_mapping_call', '_mapping_replace'):
    assert not hasattr(_Lowerer, name), name
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_leaf_value_operations_do_not_require_lowerer_mixins():
    script = """
import sys
from behave.sim.behavior import lower_bytes, lower_ordered_scalars
assert not hasattr(lower_bytes, 'ByteLowering')
assert not hasattr(lower_ordered_scalars, 'OrderedScalarLowering')
for name in ('construct', 'from_bytes', 'to_bytes', 'check_store'):
    assert callable(getattr(lower_bytes, name)), name
for name in ('coerce', 'compare'):
    assert callable(getattr(lower_ordered_scalars.OrderedScalar, name)), name
for name in ('is_local', 'bind'):
    assert callable(getattr(lower_ordered_scalars, name)), name
for name in ('behavior.lowering', 'behavior.lower_graph', 'command', 'production_generation'):
    assert 'behave.sim.' + name not in sys.modules, name
from behave.sim.behavior.lowering import _Lowerer
for name in ('_byte_mode', '_checked_byte', '_byte_result', '_byte_constructor',
             '_integer_from_bytes', '_integer_to_bytes', '_require_byte_store_range',
             '_ordered_local', '_as_ordered_scalar', '_assign_ordered_scalar',
             '_ordered_comparison'):
    assert not hasattr(_Lowerer, name), name
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_static_reference_facts_do_not_depend_on_the_source_emitter_mixin():
    script = """
import ast
import sys
from pathlib import Path
from behave.sim.behavior import flow_facts, lower_static_refs
assert not hasattr(flow_facts, '_StaticReferenceLowering')
for name in ('bound', 'bind', 'assign', 'install', 'merge_bound'):
    assert callable(getattr(lower_static_refs._StaticRef, name)), name
for name in ('value_key', 'payload_shape', 'disjunction', 'numeric_equal',
             'integer_membership', 'reference_result', 'payload_encodable', 'choose_payload'):
    assert callable(getattr(lower_static_refs, name)), name
for name in ('behavior.lowering', 'behavior.lower_graph', 'command', 'production_generation'):
    assert 'behave.sim.' + name not in sys.modules, name
from behave.sim.behavior.lowering import _Lowerer
retired = {'_static_value_key', '_static_tag', '_bound_static_ref', '_bind_static_ref',
             '_assign_static_ref', '_install_static_ref', '_merge_bound_static_refs',
             '_table_payload_shape', '_or', '_python_numeric_equal', '_integer_membership',
             '_choose_table_payload', '_static_collection', '_static_reference_result',
             '_static_payload_encodable'}
for name in retired:
    assert not hasattr(_Lowerer, name), name
for path in Path(lower_static_refs.__file__).parent.glob('*.py'):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Attribute):
            assert node.attr not in retired, (path.name, node.lineno, node.attr)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_finite_source_rules_use_explicit_context_without_a_lowerer_mixin():
    script = """
import ast
import sys
from pathlib import Path
from behave.sim.behavior import lower_static_refs
assert not hasattr(lower_static_refs, '_StaticReferenceLowering')
for name in ('iterable', 'length'):
    assert callable(getattr(lower_static_refs._StaticRef, name)), name
for name in ('is_reference', 'possible_values', 'requires_reference', 'lower_reference',
             'selector', 'lookup', 'read_path', 'key_equal', 'equal_sources'):
    assert callable(getattr(lower_static_refs, name)), name
for name in ('behavior.lowering', 'behavior.lower_graph', 'command', 'production_generation'):
    assert 'behave.sim.' + name not in sys.modules, name
from behave.sim.behavior import lowering, lower_returns
from behave.sim.behavior.lower_graph import GraphLowerer
assert lowering._Lowerer.__bases__ == (object,)
assert GraphLowerer.__bases__ == (lowering._Lowerer,)
retired = {'_table', '_finite_key_equal', '_closed_static_literal',
           '_static_requires_reference', '_is_character_constructor',
           '_character_reference', '_character_ordinal', '_static_ref_possible',
           '_static_source_values', '_static_collection_result', '_static_get',
           '_static_none_predicate', '_static_equality', '_static_equal_values',
           '_root_static_ref', '_static_key', '_lower_static_selector',
           '_tuple_key_value', '_select_static_ref', '_static_ifexp', '_static_value',
           '_static_iterable', '_static_length', '_table_path_parts', '_table_path'}
for name in retired:
    assert not hasattr(lowering._Lowerer, name), name
# The source planner has its own, different _static_iterable rule. Check the
# migrated emitter and its actual consumers, not unrelated same-spelled APIs.
for module in (lower_static_refs, lowering, lower_returns):
    for node in ast.walk(ast.parse(Path(module.__file__).read_text())):
        if isinstance(node, ast.Attribute):
            assert node.attr not in retired, (module.__name__, node.lineno, node.attr)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_final_contexts_do_not_load_lowerer_or_execution_owners():
    script = """
import sys
from behave.sim.behavior import lower_context, lower_state
from behave.sim.behavior.source_values import SCALAR, ValueContract
assert callable(lower_context.collect_contexts)
assert callable(lower_context.initial_image)
assert callable(lower_context.bind_source_state)
inner = ValueContract.sequence('list', 0, 3, SCALAR)
outer = ValueContract.sequence('list', 2, 2, inner)
assert next(lower_context.dynamic_length_specs(outer))[0][0] is lower_state.SEQUENCE_ELEMENT
for name in ('behavior.lowering', 'behavior.lower_graph', 'command',
             'production_generation', 'rtl.artifact'):
    assert 'behave.sim.' + name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_retired_lowerer_helpers_are_not_compatibility_aliases():
    from behave.sim.behavior.lower_graph import GraphLowerer
    from behave.sim.behavior.lowering import _Lowerer

    for lowerer in (_Lowerer, GraphLowerer):
        assert not hasattr(lowerer, '_push_rename_overlay')
        assert not hasattr(lowerer, '_shape_ast')
    assert not hasattr(GraphLowerer, 'goto')


def test_source_preparation_does_not_load_lowerer_or_execution_owners():
    script = """
import sys
from behave.sim.behavior import lower_prepare
assert callable(lower_prepare.prepare_source)
assert callable(lower_prepare.plan_representation)
assert lower_prepare.PreparationCache().goal_manifest is None
for name in ('behavior.lowering', 'behavior.lower_graph', 'command',
             'production_generation', 'rtl.artifact'):
    assert 'behave.sim.' + name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_public_tool_records_do_not_load_runtime_or_execution_owners():
    script = """
import sys
from behave.sim import api
from behave.sim.rtl import tool_protocol
for name in ('ToolCommand', 'ToolGroup', 'ToolExecution'):
    assert getattr(api, name) is getattr(tool_protocol, name), name
for name in ('tool_isolation', 'tool_runtime', 'tool_outputs', 'native_dependencies'):
    assert 'behave.sim.rtl.' + name not in sys.modules, name
assert tool_protocol.JOB_VERSION == 'rtl-tool-job/7'
assert tool_protocol.EXECUTION_VERSION == 'rtl-tool-execution/5'
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_flow_fact_algebra_does_not_load_lowerer_or_execution_owners():
    script = """
import sys
from behave.sim.behavior import flow_facts
assert flow_facts.join(()).unknown_ranges == frozenset()
for name in ('behavior.lowering', 'behavior.lower_graph', 'command',
             'production_generation', 'rtl.artifact'):
    assert 'behave.sim.' + name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_lowering_checks_do_not_import_construction_or_execution_owners():
    script = """
import sys
from behave.sim.behavior import lower_checks
assert callable(lower_checks.check_goals)
assert callable(lower_checks.check_step_aliases)
assert callable(lower_checks.check_value_storage)
assert callable(lower_checks.check_value_lengths)
for name in ('behavior.lowering', 'behavior.lower_graph', 'command',
             'production_generation', 'rtl.artifact'):
    assert 'behave.sim.' + name not in sys.modules, name
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_tool_record_consumers_import_the_record_owner():
    record_members = {
        'ToolCommand', 'ToolGroup', 'ToolExecution', 'ToolOutputLimit',
        'OutputNormalizer', 'ToolIsolationError', 'require_completed',
        'command_result', 'command_log', 'command_stream',
    }
    wrong = []
    for path in SIM.rglob('*.py'):
        for module, names in _imports(path):
            if module == 'behave.sim.rtl.tool_isolation':
                wrong.extend((str(path.relative_to(SIM)), name)
                             for name in names if name in record_members)
    assert wrong == []


def _package(path):
    parts = list(path.relative_to(ROOT.parent).with_suffix("").parts)
    return parts[:-1]


def _imports(path):
    tree = ast.parse(path.read_text(), filename=str(path))
    package = _package(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, ()
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package) - (node.level - 1)
                base = package[:max(0, keep)]
                if node.module:
                    base.extend(node.module.split("."))
                module = ".".join(base)
            else:
                module = node.module or ""
            yield module, tuple(alias.name for alias in node.names)


def _nonpublic_api_accesses(source):
    """Lint ordinary facade imports/accesses; this is not a Python sandbox."""
    tree = ast.parse(source)
    aliases, bad = {}, []
    public = set(api.__all__)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name == "behave.sim.api":
                    aliases[item.asname or "behave"] = item.name if item.asname else "behave"
        elif isinstance(node, ast.ImportFrom) and not node.level:
            for item in node.names:
                if node.module == "behave.sim" and item.name == "api":
                    aliases[item.asname or item.name] = "behave.sim.api"
                elif node.module == "behave.sim.api" and item.name not in public | {"*"}:
                    bad.append((node.lineno, item.name))

    def qualified(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, "")
        if isinstance(node, ast.Attribute):
            base = qualified(node.value)
            return base + "." + node.attr if base else ""
        return ""

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and qualified(node.value) == "behave.sim.api":
            if node.attr not in public:
                bad.append((node.lineno, node.attr))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "getattr" and len(node.args) >= 2
              and qualified(node.args[0]) == "behave.sim.api"):
            member = node.args[1]
            name = member.value if isinstance(member, ast.Constant) else "<dynamic>"
            if name not in public:
                bad.append((node.lineno, name))
    return bad


@pytest.mark.parametrize("source", (
    "from behave.sim.api import _evaluate",
    "from behave.sim import api; api._run()",
    "from behave.sim import api as simulator; simulator._bind_rtl_exact()",
    "import behave.sim.api as simulator; simulator.not_exported",
    "import behave.sim.api; behave.sim.api._evaluation",
    "from behave.sim import api; getattr(api, '_run')",
    "from behave.sim import api; getattr(api, member)",
))
def test_consumer_lint_rejects_nonpublic_facade_members(source):
    assert _nonpublic_api_accesses(source)


@pytest.mark.parametrize("source", (
    "from behave.sim.api import BaselinePolicy",
    "from behave.sim import api; api.BaselinePolicy(max_queue_depth=100)",
    "import behave.sim.api as simulator; simulator.CommandStatus.EVALUATED",
    "import behave.sim.api; behave.sim.api.handle_command(request)",
    "from behave.sim import api; getattr(api, 'DEFAULT_SEQUENCE_LENGTH')",
))
def test_consumer_lint_accepts_exported_facade_members(source):
    assert not _nonpublic_api_accesses(source)


def test_source_environment_comes_from_the_executor_vocabulary():
    from behave.sim.behavior.source_intrinsics import APPROVED_MODULES, BUILTIN_NAMES, CONCRETE_MODULES
    from behave.sim.behavior.source_syntax import FUTURE_FEATURES

    descriptor = api.behavior_source_environment()
    assert descriptor["builtins"] == sorted(BUILTIN_NAMES)
    assert descriptor["approved_modules"] == sorted(CONCRETE_MODULES)
    assert descriptor["exact_modules"] == sorted(APPROVED_MODULES)
    assert descriptor["future_features"] == sorted(FUTURE_FEATURES)
    assert descriptor["reference_adapter"] == api.ISOLATED_REFERENCE_ADAPTER
    json.dumps(descriptor)
    descriptor["builtins"].append("host_only_canary")
    assert "host_only_canary" not in api.behavior_source_environment()["builtins"]


def test_package_explicitly_exports_the_typed_api():
    from behave import sim

    assert sim.__all__ == ["PACKAGE_DIR", "api"]
    assert sim.api is api
    assert "behavior_source_environment" in api.__all__
    assert {"Refusal", "Refused"} <= set(api.__all__)
    raw_live_device = {
        "prepare_command", "run", "evaluate", "evaluate_generated",
    }
    assert raw_live_device.isdisjoint(api.__all__)
    assert all(not hasattr(api, name) for name in raw_live_device)
    legacy_source_pair = {
        "BehaviorComparisonRequest", "compare_behaviors",
        "prepare_behavior_comparison",
    }
    assert legacy_source_pair.isdisjoint(api.__all__)
    assert all(not hasattr(api, name) for name in legacy_source_pair)
    assert {
        "realization_of_behavior_source", "realization_of_rtl_source",
        "prepare_behavior", "prepare_rtl", "handle_command",
    } <= set(api.__all__)
    retired_exact_handle_names = {
        "BehaviorExactConfig", "BehaviorExactHandle", "RTLExactConfig",
        "RTLExactHandle", "prepare_behavior_exact", "prepare_rtl_exact",
    }
    assert retired_exact_handle_names.isdisjoint(api.__all__)
    assert all(not hasattr(api, name) for name in retired_exact_handle_names)
    assert {"Monitor", "ReferenceModel"}.isdisjoint(api.__all__)
    assert not hasattr(api, "Monitor")
    assert not hasattr(api, "ReferenceModel")
    assert hasattr(api.RTLHandle, "command_request")
    assert hasattr(api.BehaviorHandle, "command_request")
    assert inspect.signature(api.BehaviorHandle.command_request) == \
        inspect.signature(api.RTLHandle.command_request)
    assert inspect.signature(api.BehaviorHandle.replay) == \
        inspect.signature(api.RTLHandle.replay)
    assert "bind_behavior_exact" not in api.__all__
    assert "bind_rtl_exact" not in api.__all__
    assert not hasattr(api, "bind_behavior_exact")
    assert not hasattr(api, "bind_rtl_exact")


def test_facade_reexports_the_phase_implementations_without_wrappers():
    from behave.sim import analysis_service, authority, evaluation, generation, goal_policy, preparation

    assert "RESULT_VERSION" in api.__all__
    assert api.RESULT_VERSION == authority.RESULT_VERSION
    assert api.analyse is analysis_service.analyse
    assert api.prepare_behavior is preparation.prepare_behavior
    assert api.prepare_rtl is preparation.prepare_rtl
    assert api.generate_stimuli is generation.generate_stimuli
    assert api.evaluation_policy is goal_policy.evaluation_policy
    assert api.evaluation_manifest is goal_policy.evaluation_manifest
    for name in ("expected_for", "compare_run", "transfers"):
        assert getattr(api, name) is getattr(evaluation, name)


def test_test_fixtures_are_owned_by_test_support_not_the_public_facade():
    from behave.sim import evaluation
    from tests.support import device_boundary

    for name in ("_evaluate", "_evaluate_generated", "_run", "_evaluation",
                 "_batched", "_replay_note", "_prepare_device_command"):
        assert not hasattr(api, name), name
    assert device_boundary.evaluate_device is evaluation.evaluate
    assert device_boundary.evaluate_generated_device is evaluation.evaluate_generated
    assert device_boundary.run_device is evaluation.run
    assert device_boundary.prepare_device_command.__module__ == "tests.support.device_boundary"


def test_workflow_defaults_use_only_the_public_simulator_boundary():
    from behave.sim import generation_defaults
    from behave.task_execution.judge_policy import JudgePolicy

    names = ("DEFAULT_BASELINE_CASES", "DEFAULT_SEQUENCE_LENGTH", "DEFAULT_STIMULUS_SEED")
    assert set(names) <= set(api.__all__)
    for name in names:
        assert getattr(api, name) == getattr(generation_defaults, name)
    workflow = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=0)
    baseline = api.BaselinePolicy()
    assert (workflow.baseline_cases, workflow.sequence_length, workflow.seed) == (
        baseline.case_limit, baseline.max_queue_depth, baseline.seed)
    imports = [
        (module, names)
        for module, names in _imports(ROOT / "task_execution" / "judge_policy.py")
        if module.startswith("behave.sim")
    ]
    assert imports
    assert all(module == "behave.sim.api" or (module, names) == ("behave.sim", ("api",))
               for module, names in imports)


def test_public_generation_handoff_annotations_resolve_without_frontend_imports():
    from typing import get_type_hints

    hints = get_type_hints(api.generate_stimuli)
    assert hints["task"] is api.AdmittedTask
    assert hints["generation_spec"] == api.GenerationSpec | None
    assert hints["probe_budget"] == int | None
    assert hints["search_policy"] == api.SearchPolicy | None
    assert hints["baseline_policy"] == str | None
    assert hints["return"] is api.GeneratedStimulusSet
    assert hints["baseline"] == tuple[api.Stimulus, ...] | list[api.Stimulus]
    assert hints["providers"] == tuple[api.GenerationProvider, ...] | list[api.GenerationProvider]


def test_public_refusal_is_a_closed_deeply_immutable_record():
    source = {"missing": ["local-guidance"], "where": {"phase": "release"}}
    refusal = api.Refusal("test-unavailable", "the test says why", **source)
    source["missing"].append("forged")
    source["where"]["phase"] = "changed"

    assert refusal.detail["missing"] == ("local-guidance",)
    assert refusal.as_dict()["detail"] == {
        "missing": ["local-guidance"], "where": {"phase": "release"}}
    with pytest.raises(TypeError):
        refusal.detail["missing"] += ("forged",)
    with pytest.raises(TypeError):
        refusal.detail["where"]["phase"] = "changed"
    with pytest.raises(TypeError, match="closed simulator-boundary"):
        class ForgedRefusal(api.Refusal):
            pass


def test_public_handles_expose_one_closed_command_shape():
    rtl = inspect.signature(api.RTLHandle.command_request).parameters
    behavior = inspect.signature(
        api.BehaviorHandle.command_request).parameters

    expected = (
        "self", "run_directory", "baseline", "providers", "probe_budget",
        "search_policy", "scoring_policy", "run_analysis", "capture_analysis", "secondary_pool_budget",
        "reference", "feasibility_timeout_ms", "query_timeout_ms", "batch",
        "analysis_wall_timeout_seconds")
    assert tuple(rtl) == expected
    assert tuple(behavior) == expected
    assert rtl["run_analysis"].default is False
    assert behavior["run_analysis"].default is False
    assert rtl["capture_analysis"].default is False
    assert behavior["capture_analysis"].default is False
    assert rtl["secondary_pool_budget"].default == 0
    assert behavior["secondary_pool_budget"].default == 0
    assert {"task", "artifact", "contract", "realization", "frontend_catalogue", "exact",
            "record_audit"}.isdisjoint(rtl)
    assert {"task", "artifact", "contract", "realization", "frontend_catalogue", "exact",
            "record_audit"}.isdisjoint(behavior)


def test_shared_score_path_does_not_import_the_legacy_behavior_pair_diagnostic():
    for path in (
            SIM / "api.py",
            SIM / "public_handle.py",
            SIM / "command.py",
            SIM / "behavior" / "artifact.py"):
        modules = {module for module, _names in _imports(path)}
        assert "behave.sim.behavior_comparison" not in modules


def test_importing_the_facade_does_not_import_frontend_implementation_types():
    program = r'''
import json
import sys
import tempfile

def no_temporary_io():
    raise AssertionError("public API import must not probe a writable directory")

tempfile.gettempdir = no_temporary_io
from behave.sim import api

print(json.dumps({
    "has_raw_live_device": any(name in api.__all__ for name in (
        "prepare_command", "run", "evaluate", "evaluate_generated")),
    "loaded": sorted(name for name in (
        "behave.sim.behavior.instance",
        "behave.sim.rtl.analysis",
        "behave.sim.rtl.artifact",
        "behave.sim.rtl.tool_isolation",
    ) if name in sys.modules),
}, sort_keys=True))
'''
    completed = subprocess.run(
        [sys.executable, "-c", program], check=True, text=True,
        capture_output=True)

    assert json.loads(completed.stdout) == {
        "has_raw_live_device": False, "loaded": []}


def test_simulator_does_not_depend_on_repository_applications():
    bad = []
    for path in SIM.rglob("*.py"):
        for module, _names in _imports(path):
            parts = module.split(".")
            if len(parts) >= 2 and parts[0] == "behave" \
                    and parts[1] in FORBIDDEN_FROM_SIM:
                bad.append((path.relative_to(ROOT), module))

    assert bad == []


def test_generation_scheduler_does_not_import_a_backend():
    """The policy owner consumes the protocol; engines implement it."""

    scheduler = SIM / "generation.py"
    imports = [module for module, _names in _imports(scheduler)]

    assert not any(module.startswith("behave.sim.engines") for module in imports)


def test_phase_owners_do_not_acquire_reverse_dependencies():
    """Keep generation, concrete replay, and bounded analysis as separate owners.

    These are semantic boundaries, not merely a preferred directory layout.  The
    common generation worklist may create stimuli but cannot replay them or infer a
    bounded status.  Concrete replay may execute an artifact but cannot ask a solver.
    Bounded analysis consumes frozen records and therefore cannot invoke generation or
    an engine runner.  Backend adapters remain free to implement their side of a phase
    behind the corresponding protocol.
    """

    generation = {
        module for module, _names in _imports(SIM / "generation.py")
    }
    forbidden_generation = {
        "behave.sim.analysis_report",
        "behave.sim.core.analysis",
        "behave.sim.core.closure",
        "behave.sim.core.conditional_evidence",
        "behave.sim.core.query",
        "behave.sim.witness_pool",
    }
    assert generation.isdisjoint(forbidden_generation)

    replay_files = (
        SIM / "core" / "driver.py",
        SIM / "core" / "atomic.py",
        SIM / "rtl" / "artifact.py",
    )
    forbidden_replay_prefixes = (
        "behave.sim.core.analysis",
        "behave.sim.core.closure",
        "behave.sim.core.query",
        "behave.sim.engines",
        "behave.sim.rtl.exact",
        "z3",
    )
    for path in replay_files:
        imports = {module for module, _names in _imports(path)}
        assert not any(
            module.startswith(forbidden_replay_prefixes)
            for module in imports
        ), path.relative_to(ROOT)

    analysis_imports = {
        module for module, _names in _imports(SIM / "core" / "analysis.py")
    }
    forbidden_analysis_prefixes = (
        "behave.sim.engines",
        "behave.sim.generation",
        "behave.sim.production_generation",
        "behave.sim.rtl.artifact",
    )
    assert not any(
        module.startswith(forbidden_analysis_prefixes)
        for module in analysis_imports
    )


def test_canonical_stimulus_construction_does_not_import_a_backend():
    """Canonicalization reads core records, not a facade or provider semantics."""

    imports = [module for module, _names in _imports(SIM / "stimulus_set.py")]

    assert "behave.sim.api" not in imports
    assert not any(module.startswith((
        "behave.sim.behavior", "behave.sim.rtl", "behave.sim.engines"
    )) for module in imports)


def test_public_stimulus_helpers_are_core_framing_exports():
    """The facade re-exports framing without owning a second implementation."""

    from behave.sim.core import framing

    assert api.reset_then is framing.reset_then
    assert api.framed is framing.framed
    assert api.idle_tail is framing.idle_tail


def test_behavior_atomic_schedule_binding_does_not_import_rtl():
    """Schedule identity is a core fact shared by the two exact backends."""

    behavior = [
        (module, names)
        for module, names in _imports(SIM / "behavior" / "atomic_analysis.py")
    ]
    rtl = [
        (module, names)
        for module, names in _imports(SIM / "rtl" / "exact_product.py")
    ]

    assert not any(module.startswith("behave.sim.rtl")
                   for module, _names in behavior)
    for imports in (behavior, rtl):
        assert any(
            module == "behave.sim.core.clock_schedule"
            and "unique_global_schedule" in names
            for module, names in imports)


def test_other_source_packages_do_not_bypass_sim_api():
    bad = []
    for path in ROOT.rglob("*.py"):
        if path.is_relative_to(SIM):
            continue
        for module, names in _imports(path):
            if module == "behave" and "sim" in names:
                bad.append((path.relative_to(ROOT), module, names))
            elif module == "behave.sim":
                if names != ("api",):
                    bad.append((path.relative_to(ROOT), module, names))
            elif module.startswith("behave.sim.") \
                    and module != "behave.sim.api":
                bad.append((path.relative_to(ROOT), module, names))

    assert bad == []


def test_other_source_packages_use_only_exported_sim_api_members():
    bad = []
    for path in ROOT.rglob("*.py"):
        if not path.is_relative_to(SIM):
            bad.extend((path.relative_to(ROOT), *finding)
                       for finding in _nonpublic_api_accesses(path.read_text()))
    assert bad == []


def test_retired_in_process_reference_adapter_cannot_execute_submitted_source():
    source = b"raise AssertionError('submitted source executed in verifier')\n"

    assert not hasattr(api, "register_loader"), \
        "the public API must not mint new in-process submitted-source adapters"
    with pytest.raises(api.Refused, match="reference-adapter-retired"):
        api.load_reference("transaction/1", {"behavior.py": source})


def test_public_isolated_reference_lazy_import_preserves_isolation_refusal():
    """The public facade needs no private import and never leaks ``Broken``.

    A fresh interpreter makes import order part of the test.  The named judge is made
    deterministically unavailable at its typed worker seam; the adapter must carry that
    exact refusal kind instead of parsing its text or falling back to parent parsing.
    """

    program = r'''
import json
import sys
from behave.sim import api
from behave.sim import worker

before = "behave.sim.reference_isolated" in sys.modules

def unavailable(*_args, **_kwargs):
    raise worker.WorkerFailed(
        "refused",
        "isolation-capability-missing: user namespaces are unavailable",
        code="isolation-capability-missing")

worker.run_job_strict = unavailable
try:
    api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b"class M:\n    def process(self, a):\n        return a\n"})
except api.FailureRaised as exc:
    print(json.dumps({
        "before": before,
        "after": "behave.sim.reference_isolated" in sys.modules,
        "origin": exc.failure.origin.value,
        "disposition": exc.failure.disposition.value,
        "kind": exc.failure.reason.kind,
        "detail": dict(exc.failure.reason.detail),
    }, sort_keys=True))
else:
    raise AssertionError("an unavailable isolation boundary was accepted")
'''
    completed = subprocess.run(
        [sys.executable, "-c", program], check=True, text=True,
        capture_output=True)
    got = json.loads(completed.stdout)

    assert got["before"] is False
    assert got["after"] is True
    assert got["origin"] == "infrastructure"
    assert got["disposition"] == api.FailureDisposition.NO_RESULT.value
    assert got["kind"] == "isolation-capability-missing"
    assert "isolation-capability-missing" in got["detail"]["worker_detail"]
