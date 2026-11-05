"""Resource knobs have one authority; semantic constants are not knobs."""

import ast
import importlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from behave.core import runtime_defaults as defaults
from behave.sim.rtl import tool_protocol, tool_runtime


def test_production_waits_have_no_literal_positive_timeouts():
    """Execution/cleanup waits use caller policy, not a private numeric timer.

    This deliberately covers blocking API timeouts, not numeric constants in
    RTL/IR semantics or nonblocking wait(0). Behavioral tests below still check
    that configured values actually reach the owning process boundaries.
    """
    root = Path(defaults.__file__).parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        for call in ast.walk(ast.parse(path.read_text())):
            if not isinstance(call, ast.Call):
                continue
            budgets = [arg.value for arg in call.keywords if arg.arg in {
                "timeout", "timeout_s", "timeout_ms", "wall_seconds", "cleanup_seconds",
            }]
            if (isinstance(call.func, ast.Attribute)
                    and call.func.attr in {"wait", "join"} and call.args):
                budgets.append(call.args[0])
            for value in budgets:
                if (isinstance(value, ast.Constant)
                        and type(value.value) in (int, float) and value.value > 0):
                    offenders.append(f"{path.relative_to(root)}:{call.lineno}")
    assert not offenders, "Private process timeouts: " + ", ".join(offenders)


def test_search_budget_shares_are_not_private_numeric_multipliers():
    root = Path(defaults.__file__).parents[1] / "sim"
    offenders = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [part.id for target in targets for part in ast.walk(target)
                     if isinstance(part, ast.Name)]
            if not any("budget" in name or "timeout" in name for name in names) or node.value is None:
                continue
            for term in ast.walk(node.value):
                if (isinstance(term, ast.BinOp) and isinstance(term.op, ast.Mult)
                        and any(isinstance(value, ast.Constant) and type(value.value) is float
                                and 0 < value.value < 1 for value in (term.left, term.right))):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not offenders, "Private budget shares: " + ", ".join(offenders)


def test_sim_validation_scripts_do_not_override_runtime_with_private_timeouts():
    """A witness chooses inputs/expected values, not an implicit replay quota."""
    root = Path(__file__).parents[2]
    offenders = []
    for scope in (root / "tools", root / "scripts"):
        for path in scope.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                for arg in node.keywords:
                    if (arg.arg in {"timeout_s", "cleanup_seconds"}
                            and isinstance(arg.value, ast.Constant)
                            and type(arg.value.value) in (int, float) and arg.value.value > 0):
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not offenders, "Private validation timeouts: " + ", ".join(offenders)

    # Old witness modules embedded per-task wall limits; all consumers now
    # use runtime policy. Keep the retired knobs out of future task fixtures.
    assert not [str(path.relative_to(root)) for path in (root / "tests/dataset").glob("*.py")
                if "PER_STIMULUS_S" in path.read_text()]


def test_sim_failure_values_have_no_private_repr_truncation():
    """Structured evidence is not a UI preview with a per-call character cap."""
    root = Path(defaults.__file__).parents[1]
    offenders = []
    for scope in ("sim", "isolation"):
        for path in (root / scope).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == "repr"):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not offenders, "Truncated failure values: " + ", ".join(offenders)


def test_typed_refusals_do_not_cut_structured_evidence():
    root = Path(defaults.__file__).parents[1] / "sim"
    offenders = []
    for path in root.rglob("*.py"):
        for call in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "Refusal"):
                continue
            for node in ast.walk(call):
                if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)
                        and any(isinstance(bound, ast.Constant) and type(bound.value) is int
                                for bound in ast.walk(node.slice))):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not offenders, "Truncated structured evidence: " + ", ".join(offenders)


def test_declaration_refusals_preserve_complete_invalid_values():
    from behave.sim.core.contract import ResetPolicy, _declared_enum
    from behave.sim.core.memory import MemoryPort
    from behave.sim.core.refusal import Refused

    invalid = "unrecognized-value-" * 40
    for construct in (lambda: _declared_enum(ResetPolicy, invalid, "bad-reset", "invalid reset"),
                      lambda: MemoryPort(invalid)):
        with pytest.raises(Refused) as caught:
            construct()
        assert caught.value.refusal.as_dict()["detail"]["got"] == repr(invalid)


def test_refusal_repr_preserves_reason():
    from behave.sim.core.refusal import Refusal

    reason = "Complete failure evidence. " * 10
    assert repr(reason) in repr(Refusal("failure", reason))


def test_isolated_boundary_error_is_shared_without_changing_existing_imports():
    from behave.sim.behavior.isolated import Broken as SourceBroken
    from behave.sim.core.failure import Broken

    assert SourceBroken is Broken
    error = SourceBroken("original error", code="worker-memory-limit", detail="full detail")
    assert str(error) == "original error"
    assert (error.code, error.detail) == ("worker-memory-limit", "full detail")


# Explicit regression inventory: replacing one alias with a numeric value must
# fail even if it happens to equal today's default.
ALIASES = (
    ('behave.sim.behavior.source_sequences', '_CONTRACT_LOOP_SUMMARY_MIN_TRIPS',
     'SIM_BEHAVIOR_LOOP_SUMMARY_MIN_TRIPS'),
    ('behave.sim.behavior.budgets', 'UNROLL_LIMIT', 'SIM_BEHAVIOR_UNROLL_LIMIT'),
    ('behave.sim.behavior.budgets', 'HELPER_ACTIVATION_LIMIT', 'SIM_BEHAVIOR_HELPER_ACTIVATIONS'),
    ('behave.sim.behavior.budgets', 'SEMANTIC_IR_NODES', 'SIM_BEHAVIOR_SEMANTIC_IR_NODES'),
    ('behave.sim.symbolic.capabilities', 'TERM_BUDGET', 'SIM_BEHAVIOR_ENCODING_NODES'),
    ('behave.sim.symbolic.capabilities', 'REPRESENTATION_DEPTH', 'SIM_BEHAVIOR_REPRESENTATION_DEPTH'),
    ('behave.sim.behavior.budgets', 'SOURCE_PLAN_NODES', 'SIM_BEHAVIOR_SOURCE_PLAN_NODES'),
    ('behave.sim.behavior.budgets', 'SOURCE_TEXT_CHARS', 'SIM_BEHAVIOR_SOURCE_TEXT_CHARS'),
    ('behave.sim.symbolic.capabilities', 'SCALAR_BITS', 'SIM_BEHAVIOR_SCALAR_BITS'),
    ('behave.sim.behavior.budgets', 'ROM_EXPANSION_NODES', 'SIM_BEHAVIOR_ROM_EXPANSION_NODES'),
    ('behave.sim.symbolic.capabilities', 'STATE_ROUNDS', 'SIM_BEHAVIOR_STATE_ROUNDS'),
    ('behave.sim.symbolic.capabilities', 'STATE_MAGNITUDE', 'SIM_BEHAVIOR_STATE_MAGNITUDE'),
    ('behave.sim.behavior.construction', '_CACHE_WEIGHT_LIMIT', 'SIM_CONSTRUCTION_CACHE_BYTES'),
    ('behave.sim.behavior.construction', '_SHARE_ROOT_LIMIT', 'SIM_CONSTRUCTION_SHARE_ROOTS'),
    ('behave.sim.behavior.construction', '_SHARE_PAIR_LIMIT', 'SIM_CONSTRUCTION_SHARE_PAIRS'),
    ('behave.sim.behavior.construction', '_MAX_CONCURRENT_BUILDS', 'SIM_CONSTRUCTION_WORKERS'),
    ('behave.sim.behavior.native_values', 'DEFAULT_WIRE_BYTES', 'SIM_NATIVE_VALUE_WIRE_BYTES'),
    ('behave.sim.behavior.native_values', 'DEFAULT_WIRE_NODES', 'SIM_NATIVE_VALUE_WIRE_NODES'),
    ('behave.sim.behavior.isolated', '_MAX_PROTOCOL_BYTES', 'SIM_SOURCE_REPLY_BYTES'),
    ('behave.sim.symbolic.term_bounds', '_CONTROL_PROPOSITION_BUDGET', 'SIM_BEHAVIOR_CONTROL_PROOF_NODES'),
    ('behave.sim.symbolic.closure_proof', '_POSITION_STRUCTURAL_PROOF_WORK', 'SIM_BEHAVIOR_POSITION_PROOF_NODES'),
    ('behave.sim.symbolic.term_rewrite', '_PROOF_WORK', 'SIM_BEHAVIOR_REWRITE_PROOF_NODES'),
    ('behave.sim.symbolic.ranges', '_POINTWISE_LOOP_COMBINATION_LIMIT', 'SIM_BEHAVIOR_RANGE_COMBINATIONS'),
    ('behave.sim.symbolic.ssa', '_PATH_PARTITION_LIMIT', 'SIM_BEHAVIOR_SSA_PATHS'),
    ('behave.sim.behavior.while_plan', '_EXIT_PATH_LIMIT', 'SIM_BEHAVIOR_LOOP_EXIT_PATHS'),
    ('behave.sim.behavior.atomic_exact', '_FUNCTIONAL_ARRAY_MIN_CELLS', 'SIM_BEHAVIOR_FUNCTIONAL_ARRAY_MIN_CELLS'),
    ('behave.sim.core.audit', 'AUDITED_WITNESS_MAX_NODES', 'SIM_AUDITED_WITNESS_NODES'),
    ('behave.sim.core.memory', 'MEMORY_FULL_STATE_MAX_CELLS', 'SIM_MEMORY_FULL_STATE_CELLS'),
    ('behave.sim.core.query_cache', '_MAX_ANSWERS', 'SIM_ANALYSIS_ANSWER_CACHE_ENTRIES'),
    ('behave.sim.symbolic.exact_solve', '_MAX_MEASURED_SHAPE_ASSERTIONS', 'SIM_ANALYSIS_SHAPE_TELEMETRY_ASSERTIONS'),
    ('behave.sim.cpu_admission', '_WIRE_LIMIT', 'SIM_CPU_ADMISSION_WIRE_BYTES'),
    ('behave.sim.public_handle', '_MAX_BEHAVIOR_SOURCE_BYTES', 'SIM_SOURCE_BYTES'),
    ('behave.sim.engines.behavior.generation_job', '_CONTEXT_WIRE_MAX_BYTES', 'SIM_GENERATION_CONTEXT_WIRE_BYTES'),
    ('behave.sim.engines.behavior.generation_job', '_CONTEXT_IMAGE_MAX_BYTES', 'SIM_GENERATION_CONTEXT_IMAGE_BYTES'),
    ('behave.sim.rtl.catalogue', 'MAX_ARMS_PER_SWITCH', 'SIM_RTL_SWITCH_ARMS'),
    ('behave.sim.rtl.catalogue', 'MAX_STRUCTURAL_GOALS', 'SIM_RTL_STRUCTURAL_GOALS'),
    ('behave.sim.rtl.catalogue', 'MAX_STRUCTURAL_OCCURRENCES', 'SIM_RTL_STRUCTURAL_OCCURRENCES'),
    ('behave.sim.rtl.source_loops', 'MAX_EVENTS', 'SIM_RTL_SOURCE_EVENTS'),
    ('behave.sim.rtl.tool_protocol', '_DIAGNOSTIC_BYTES', 'SIM_EDA_DIAGNOSTIC_BYTES'),
    ('behave.isolation.content_attestation', '_FREEZE_ATTEMPTS', 'ISOLATION_ATTESTATION_ATTEMPTS'),
    ('behave.isolation.content_attestation', '_MANIFEST_WORKERS', 'ISOLATION_ATTESTATION_WORKERS'),
    ('behave.isolation.linux', '_STATUS_BYTES', 'ISOLATION_STATUS_BYTES'),
)

@pytest.mark.parametrize("module,name,setting", ALIASES)
def test_limit_aliases_read_the_central_authority(module, name, setting):
    loaded = importlib.import_module(module)
    assert getattr(loaded, name) == getattr(defaults, setting)
    tree = ast.parse(inspect.getsource(loaded))
    value = next(node.value for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == name for t in node.targets))
    assert ast.unparse(value) == "defaults." + setting


def test_retired_private_cleanup_and_record_size_limits_stay_removed():
    from behave.isolation import content_attestation

    assert not hasattr(defaults, 'SIM_SOURCE_CLEANUP_SECONDS')
    assert not hasattr(defaults, 'ISOLATION_PROCESS_REAP_SECONDS')
    assert not hasattr(defaults, 'ISOLATION_ATTESTATION_RECORD_BYTES')
    assert not hasattr(content_attestation, '_MAX_RECORD_BYTES')


def test_resource_configuration_propagates_when_a_fresh_process_starts():
    script = """
from behave.core import runtime_defaults as d
d.SIM_BEHAVIOR_SCALAR_BITS = 2048
d.SIM_BEHAVIOR_SOURCE_PLAN_NODES = 321
d.SIM_RTL_STRUCTURAL_GOALS = 1234
d.SIM_GENERATION_CACHE_ENTRIES = 17
d.SIM_GENERATION_CACHE_BYTES = 4567
d.SIM_SOURCE_BYTES = 9012
d.SIM_NATIVE_VALUE_WIRE_NODES = 3456
d.ISOLATION_ATTESTATION_ATTEMPTS = 7
from behave.sim.behavior import budgets, native_values, source_bundle
from behave.sim.rtl import catalogue
from behave.sim import public_handle, generation_cache
from behave.isolation import content_attestation, content
from inspect import signature
assert budgets.SCALAR_BITS == budgets.SHIFT_LIMIT == 2048
assert budgets.as_dict()['SOURCE_PLAN_NODES']['value'] == 321
assert catalogue.MAX_STRUCTURAL_GOALS == 1234
assert native_values.DEFAULT_WIRE_NODES == 3456
assert content_attestation._FREEZE_ATTEMPTS == 7
assert public_handle._MAX_BEHAVIOR_SOURCE_BYTES == 9012
assert signature(generation_cache.GenerationContextCache).parameters['max_entries'].default == 17
assert signature(generation_cache.GenerationContextCache).parameters['max_bytes'].default == 4567
"""
    completed = subprocess.run([sys.executable, "-B", "-c", script],
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                               capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr


def test_source_child_reads_namespace_budget_from_trusted_runtime():
    from behave.sim.behavior import isolated

    bindings = [node.value for node in ast.walk(ast.parse(isolated._CHILD))
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "_image_nodes"
                        for target in node.targets)]
    assert len(bindings) == 1
    assert ast.unparse(bindings[0]) == "_runtime_defaults.SIM_SOURCE_NAMESPACE_NODES"


def test_rom_shifts_use_scalar_capability_not_a_second_512_bit_limit():
    from behave.sim.behavior.source_rom import evaluate_frozen
    expr = ast.parse("1 << 600", mode="eval").body
    result = evaluate_frozen(expr, lambda _name: None, max_int_bits=1024)
    assert result.kind == "int" and result.payload == 1 << 600
    assert evaluate_frozen(expr, lambda _name: None, max_int_bits=512) is None
    assert evaluate_frozen(expr, lambda _name: None,
                           max_int_bits=1024, max_shift=512) is None


def test_tool_cleanup_policy_reaches_the_frozen_guest_request(tmp_path, monkeypatch):
    from behave.isolation import IsolationRefusal, IsolationResult, IsolationStatus, binary
    from behave.sim.rtl import tool_isolation as tools
    monkeypatch.setenv("BEHAVE_RUNTIME_DIR", str(tmp_path / "runtime-batch"))
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.setattr(tool_runtime, "_runtime", lambda _profile: (
        (binary(str(executable), "/runtime/python"),), (), ()))
    monkeypatch.setattr(defaults, "SIM_EDA_COMMAND_CLEANUP_SECONDS", 7)
    monkeypatch.setattr(defaults, "SIM_EDA_NORMALIZATION_RECURSION_LIMIT", 1234)
    seen = []
    def capture(spec):
        mount = next(m for m in spec.read_only if m.target == "/input/job.json")
        seen.append(json.loads(Path(mount.source).read_text()))
        return IsolationResult(
            IsolationStatus.REFUSED, spec.digest, "test",
            refusal=IsolationRefusal("inspection-only", "captured request"))
    monkeypatch.setattr(tools, "run", capture)
    tools.run_workspace(tmp_path, (
        tool_protocol.ToolGroup((tool_protocol.ToolCommand("probe", ("/runtime/python",)),)),),
        role="runtime-limits")
    assert len(seen) == 1
    assert seen[0]["version"] == "rtl-tool-job/7"
    assert seen[0]["cleanup_seconds"] == 7
    assert seen[0]["normalization_recursion_limit"] == 1234


@pytest.mark.parametrize("parallel", [1, 2])
def test_guest_group_forwards_the_same_cleanup_policy(monkeypatch, parallel):
    from behave.sim.rtl import _tool_job
    calls = []
    def command(row, log_name, cleanup_seconds):
        calls.append((row, log_name, cleanup_seconds))
        return {}
    monkeypatch.setattr(_tool_job, "_command", command)
    row = {"commands": [{"id": "a"}, {"id": "b"}],
           "parallel": parallel, "when_executable_glob": None}
    _tool_job._group(row, 123, 7)
    assert sorted(calls, key=lambda x: x[0]["id"]) == [
        ({"id": "a"}, "123-0", 7), ({"id": "b"}, "123-1", 7)]


@pytest.mark.parametrize("cleanup", [True, 0, -1, None, 1.5])
@pytest.mark.parametrize("field,description", [
    ("cleanup_seconds", "cleanup"),
    ("normalization_recursion_limit", "normalization recursion"),
])
def test_guest_rejects_invalid_limits_before_starting_any_tool(
        monkeypatch, cleanup, field, description):
    from types import SimpleNamespace

    from behave.sim.rtl import _tool_job

    job = {"version": "rtl-tool-job/7", "groups": [], "outputs": [], "output_trees": [],
           "preserve_outputs": [], "normalizers": [],
           "protocol_bytes": 1024, "cleanup_seconds": 5,
           "normalization_recursion_limit": 1234}
    job[field] = cleanup
    monkeypatch.setattr(_tool_job, "Path", lambda _path: SimpleNamespace(
        read_text=lambda **_kwargs: json.dumps(job)))
    with pytest.raises(ValueError, match=description + " budget"):
        _tool_job._main()


def test_verilator_parser_receives_the_job_recursion_budget(monkeypatch):
    from behave.sim.rtl import _tool_job

    applied = []
    monkeypatch.setattr(_tool_job.sys, "getrecursionlimit", lambda: 128)
    monkeypatch.setattr(_tool_job.sys, "setrecursionlimit", applied.append)
    tree = {"nodes": [{"type": "MODULE", "name": "M", "level": 1,
                       "members": [{"type": "VAR", "name": "a",
                                    "isPrimaryIO": True, "direction": "INPUT"}]}]}
    result = json.loads(_tool_job._normalize_output(
        "verilator-ports/1", json.dumps(tree).encode(), {"top": "M"},
        recursion_limit=321))
    assert result["top"] == "M"
    assert applied == [321]
