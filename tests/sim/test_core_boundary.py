"""The core does not know which frontend it is talking to.

A boundary that lives only in the import lines is one that decays. This one
had already decayed once between the writing of the protocol and the moving of
the files: the schema, which is what both sides read widths from, was building
one side's interval type.

The check is on the text rather than on the loaded modules, because an import
inside a function is exactly how the last one got in.
"""
import ast
import pathlib

CORE = (pathlib.Path(__file__).resolve().parents[2]
        / "src" / "behave" / "sim" / "core")
FRONTENDS = ("behavior", "rtl")


def _imports(path):
    """Every module a file names, however it names it."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            # A relative import of `..behavior` has module "behavior" and a
            # level of two; an absolute one has the whole dotted path.
            yield ("." * node.level) + (node.module or "")


def test_the_core_imports_no_frontend():
    offenders = []
    for path in sorted(CORE.glob("*.py")):
        for name in _imports(path):
            bare = name.lstrip(".").split(".")
            if any(part in FRONTENDS for part in bare):
                offenders.append(f"{path.name} imports {name}")
    assert not offenders, offenders


def test_every_core_module_is_accounted_for():
    """Adding a core module is an explicit shared-semantics decision."""
    expected = {
        "__init__.py", "admission.py", "analysis.py", "apb.py", "atomic.py", "audit.py",
        "atomic_witness.py", "audit_plan.py", "axi_lite.py", "catalogue.py", "clock_schedule.py",
        "closure.py",
        "compare.py", "conditional_evidence.py", "contract.py", "domain.py",
        "driver.py", "evidence.py", "failure.py", "framing.py", "frozen.py",
        "finite_goal.py", "goal_guidance.py", "goal_provider.py",
        "hit_vector.py", "input_queue.py", "instance.py", "interface.py", "interface_product.py",
        "ledger.py", "memory.py", "memory_result.py", "memory_runtime.py", "memory_trace.py", "monitor.py", "ordered_monitor.py",
        "outcome.py",
        "product.py", "protocol.py", "protocol_exact.py", "query.py",
        "query_cache.py", "query_feedback.py", "query_telemetry.py", "ready_valid_product.py",
        "physical_value.py", "record_value.py",
        "reference.py", "reference_memory.py", "reference_observation.py", "reference_progress.py", "refusal.py", "reset_monitor.py",
        "resources.py",
        "run_record.py", "runtime_schedule.py", "schema.py", "stimulus.py",
        "supported.py", "value_type.py",
        # Shared concrete/exact adapter and legal-input semantics.
        "adapter_exact.py", "bus_control.py", "bus_exact.py", "input_framing.py",
        "memory_sampling.py", "sampled_runtime.py", "transaction_completion.py",
        # Language-independent declared predicates and ordered observations.
        "goal_predicate.py", "source_rules.py", "operation_goal.py", "operation_symbolic.py",
        "observation_goal.py", "observation_monitor.py", "observation_product.py",
        "observation_symbolic.py", "latency_goal.py", "latency_monitor.py", "latency_product.py",
        "transaction_monitor.py", "transaction_product.py",
        # Cancellation callbacks for resumable shared product construction.
        "construction_checkpoint.py",
    }
    assert {p.name for p in CORE.glob("*.py")} == expected
