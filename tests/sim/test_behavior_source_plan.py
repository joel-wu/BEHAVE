"""A source declaration is independent of, then consumed by, its producers."""

import ast
from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.sim.behavior import goals, instrumented
from behave.sim.symbolic import ir
from behave.sim.behavior.extract import LoweringError, ScopeResolver, source_model
from behave.sim.behavior.source_plan import plan_source

from .test_goal_source_oracle import DECLARED, SOURCE, _event, _expected_hits


@pytest.mark.parametrize('declaration', ['def helper():', 'async def helper():', 'class Inner:'])
@pytest.mark.parametrize('conditional', [False, True])
def test_function_scan_excludes_nested_bodies_at_every_statement_depth(declaration, conditional):
    from behave.sim.behavior.extract import _walk_function_scope

    indent = '        ' if conditional else '    '
    source = ('def process(a):\n    own = a\n'
              + ('    if a:\n' if conditional else '')
              + indent + declaration + '\n'
              + indent + '    foreign = secret()\n'
              + '    deferred = lambda: secret()\n'
              + '    return own\n')
    scope = ast.parse(source).body[0]
    nodes = tuple(_walk_function_scope(scope))
    names = {node.id for node in nodes if isinstance(node, ast.Name)}
    assert names == {'own', 'a', 'deferred'}
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef, ast.Lambda)) for node in nodes)
    assert sum(isinstance(node, ast.Return) for node in nodes) == 1


def test_one_plan_keeps_the_handwritten_denominator_and_hook_indices(monkeypatch):
    plan = plan_source(source_model(SOURCE), SOURCE)
    assert {_event(goal) for goal in plan.manifest.values()} == DECLARED
    original, _sites = goals.instrument_source(SOURCE)

    def no_new_site(*args, **kwargs):
        raise AssertionError("instrumentation must use the declared Site objects")

    monkeypatch.setattr(goals, "Site", no_new_site)
    rewritten, reused = goals.instrument_source(SOURCE, plan=plan)
    assert reused is plan.sites
    assert ast.dump(rewritten, include_attributes=True) == ast.dump(original, include_attributes=True)
    namespace, reused, hits = instrumented.load_source(SOURCE, goal_plan=plan)
    assert reused is plan.sites
    for a in range(4):
        for b in range(4):
            hits.clear()
            namespace["M"]().process(a, b)
            assert {_event(plan.manifest[goal]) for goal in hits} == _expected_hits(a, b)


def test_prepared_resolver_is_reused_and_source_tree_is_not_rewritten(monkeypatch):
    model = source_model(SOURCE)
    resolver = ScopeResolver(model)
    original = ast.dump(model.tree, include_attributes=True)

    def no_resolver(*args, **kwargs):
        raise AssertionError("the caller already resolved this source")

    monkeypatch.setattr("behave.sim.behavior.extract.ScopeResolver", no_resolver)
    plan = plan_source(model, SOURCE, resolver=resolver)
    assert {_event(goal) for goal in plan.manifest.values()} == DECLARED
    assert ast.dump(model.tree, include_attributes=True) == original


def test_runtime_hooks_reuse_ids_without_coalescing_dynamic_visits(monkeypatch):
    plan = plan_source(source_model(SOURCE), SOURCE)
    namespace, _, hits = instrumented.load_source(SOURCE, goal_plan=plan)

    def no_dynamic_id(*args):
        pytest.fail("goal IDs must be constructed at source preparation, not each visit")

    monkeypatch.setattr(instrumented, "goal_id", no_dynamic_id)
    model = namespace["M"]()
    for a, b in ((0, 0), (1, 3), (3, 1)):
        hits.clear()
        first = model.process(a, b)
        visits = tuple(hits)
        assert {_event(plan.manifest[goal]) for goal in visits} == _expected_hits(a, b)
        assert model.process(a, b) == first
        # The standalone hook API still preserves every visit and its order.
        # Only the isolated per-call reply reduces these to a set.
        assert tuple(hits) == visits + visits
        assert all(left is right for left, right in zip(visits, hits[len(visits):]))


def test_manifest_builds_common_goal_records_directly(monkeypatch):
    def no_intermediate_goal(*args, **kwargs):
        raise AssertionError("do not construct a legacy record just to copy its fields")

    monkeypatch.setattr(goals, "Goal", no_intermediate_goal)
    plan = plan_source(source_model(SOURCE), SOURCE)
    assert {_event(goal) for goal in plan.manifest.values()} == DECLARED


@pytest.mark.parametrize("mutation", ("missing", "extra", "reorder", "source", "file"))
def test_instrumentation_cannot_silently_change_a_declared_plan(mutation):
    plan = plan_source(source_model(SOURCE), SOURCE)
    source, file = SOURCE, "behavior.py"
    if mutation == "missing":
        plan = replace(plan, sites=plan.sites[:-1])
    elif mutation == "extra":
        plan = replace(plan, sites=plan.sites + (plan.sites[-1],))
    elif mutation == "reorder":
        plan = replace(plan, sites=tuple(reversed(plan.sites)))
    elif mutation == "source":
        source = SOURCE.replace("return 9", "return 7")
    else:
        file = "other.py"
    with pytest.raises(ValueError, match="source site|source bytes"):
        goals.instrument_source(source, file=file, plan=plan)


def test_producer_audit_cannot_drop_a_helper_by_omitting_its_inline_ledger():
    from behave.sim.behavior.lower_checks import _check_producers, safety_audit

    source = """class M:
    def helper(self, a):
        return 9 // a
    def process(self, a):
        return {'y': self.helper(a)}
"""
    model = source_model(source)
    plan = plan_source(model, source)
    assert len(plan.coverage_sites) == 2
    assert len(plan.safety_sites) == 1
    root = next(site for site in plan.coverage_sites if site.line == 5)
    body = ir.Mark(root.goals[0])
    with pytest.raises(LoweringError, match="declared goals") as caught:
        _check_producers(model.step, plan.coverage_sites, body)
    assert caught.value.code == "goals-unaccounted"
    # A reached partial operation still owes a guard/discharge, even if the
    # broken producer forgets to record that it inlined its containing helper.
    low = SimpleNamespace(inlined=(), pruned_functions=(), discharged={},
                          reached={id(site.node) for site in plan.safety_sites})
    (missing, unknown), discharged = safety_audit(plan.safety_sites, low, body)
    assert set(missing) == {site.id for site in plan.safety_sites}
    assert not unknown and not discharged


def test_actual_lowering_rejects_a_missing_helper_producer(monkeypatch):
    from behave.sim.symbolic.interp import run_step
    from behave.sim.behavior.lowering import _Lowerer, lower_procedure

    source = """class M:
    def reset(self):
        pass
    def helper(self, a):
        return 9 if a else 8
    def process(self, a):
        return {'y': self.helper(a)}
"""
    proc = lower_procedure(source, input_widths=[1])
    _state, output, _hits, error = run_step(proc, {"a": 1})
    assert not error
    assert output.transactions == [{"y": 9}]
    visits = []

    def omit_helper(self, node, fn, *, result_kind=None):
        visits.append(fn.name)
        return ir.Lit(0)  # well-typed result; neither marks nor inline ledger

    monkeypatch.setattr(_Lowerer, "_inline", omit_helper)
    with pytest.raises(LoweringError) as caught:
        lower_procedure(source, input_widths=[1])
    assert visits == ["helper"]
    assert caught.value.code == "goals-unaccounted"


def test_both_frontend_source_plans_import_without_a_solver_or_execution_backend():
    import json
    import subprocess
    import sys

    script = """import json, sys
from behave.sim.behavior.source_plan import plan_source
from behave.sim.behavior.extract import source_model
import behave.sim.rtl.source_plan
source = "class M:\\n def process(self, a): return {'y': a}"
plan_source(source_model(source), source)
print(json.dumps(sorted(sys.modules)))
"""
    reply = subprocess.run([sys.executable, "-B", "-c", script],
                           text=True, capture_output=True, check=True)
    loaded = set(json.loads(reply.stdout))
    assert not loaded & {
        "z3", "cocotb", "pyslang", "behave.sim.behavior.lowering",
        "behave.sim.symbolic.encoder", "behave.sim.behavior.isolated",
        "behave.sim.rtl.artifact", "behave.sim.rtl.elaboration",
    }


def test_child_program_is_syntactically_complete():
    from behave.sim.behavior.isolated import _CHILD

    ast.parse(_CHILD)


@pytest.mark.parametrize("definitions", [
    "if a:\n            def f(): return 8 // a\n        else:\n            def f(): return 4",
    "if a:\n            def f(): return 8 // a\n        else:\n            return 4",
    "def f(value=a): return 8 // value\n        if not a: return 4",
])
def test_catalogue_declares_exposed_local_bodies_without_claiming_inline_support(definitions):
    source = ("class M:\n    def process(self, a):\n        " + definitions +
              "\n        alias = f\n        return alias()\n")
    model = source_model(source)
    resolver = ScopeResolver(model)
    plan = plan_source(model, source, resolver=resolver)
    assert resolver.resolve_call(model.steps[None], "f")[0] == "opaque"
    bodies = [fn for fn in ast.walk(model.tree) if isinstance(fn, ast.FunctionDef) and fn.name == "f"]
    assert all(any(site.owner == (fn.name, fn.lineno, fn.col_offset)
                   for site in plan.coverage_sites) for fn in bodies)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    for value in (0, 1, 2):
        hits.clear()
        assert namespace["M"]().process(value) == (8 // value if value else 4)
        assert set(hits) <= set(plan.manifest)


def test_source_closure_keeps_lexical_shadowing_and_unreferenced_bodies_out():
    source = '''def f(): return 1 // 0
class M:
    def process(self, a):
        if a:
            def f(): return 5
            def unused(): return 1 // 0
            return f()
        return 4
'''
    model = source_model(source)
    plan = plan_source(model, source)
    assert {site.owner[0] for site in plan.coverage_sites} == {"process", "f"}
    assert not plan.safety_sites
    assert not any(site.line == 1 for site in plan.coverage_sites)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    assert namespace["M"]().process(1) == 5
    assert set(hits) <= set(plan.manifest)


def test_local_restore_uses_standard_attribute_binding_and_declared_goal_sites():
    source = '''class M:
    def __init__(self):
        self.total = 3
    def process(self, a):
        if a:
            snapshot = dict(self.__dict__)
            def restore():
                for name, value in snapshot.items():
                    setattr(self, name, value)
            self.total = a
            restore()
        return self.total
'''
    model = source_model(source)
    resolver = ScopeResolver(model)
    plan = plan_source(model, source, resolver=resolver)
    # Dynamic state access executes in Python; it does not acquire an exact
    # inlining certificate merely because its source sites are in the catalogue.
    assert resolver.resolve_call(model.steps[None], "restore")[0] == "opaque"
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    instance = namespace["M"]()
    for value in (0, 7, 0, 11):
        hits.clear()
        assert instance.process(value) == 3
        assert set(hits) <= set(plan.manifest)
