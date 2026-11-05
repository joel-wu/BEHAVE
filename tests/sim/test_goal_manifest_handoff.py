"""Source factories carry declarations forward rather than rebuilding them."""

import ast
from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.sim.behavior import catalogue, lowering, source_plan
from behave.sim.symbolic import ir
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.instrumented import load_source
from behave.sim.symbolic.interp import run_step
from behave.sim.core.catalogue import CatalogueManifest, Goal, catalogue_subset
from behave.sim.core.refusal import Refused

from .test_behavior_catalogue_manifest import SOURCE, _schema


def _capture_plans(monkeypatch, *, trees=None):
    plans = []
    prepare = source_plan.plan_source

    def record(model, *args, **kwargs):
        if trees is not None:
            trees.append((model.tree, ast.dump(model.tree, include_attributes=True)))
        plan = prepare(model, *args, **kwargs)
        plans.append(plan)
        return plan

    monkeypatch.setattr(source_plan, "plan_source", record)
    return plans


def test_two_pass_result_retains_the_first_declaration_and_native_semantics(monkeypatch):
    trees = []
    plans = _capture_plans(monkeypatch, trees=trees)
    result = lowering.lower_source(SOURCE, input_widths=[2], bound=3)
    assert len(plans) == 2
    assert result.catalogue is plans[0].manifest
    assert plans[1].manifest == result.catalogue
    # Both range passes intentionally share the read-only parse. Require its
    # preservation, not an extra parse solely to obtain different node IDs.
    assert plans[0].safety_sites[0].node is plans[1].safety_sites[0].node
    assert trees[0][0] is trees[1][0]
    assert all(ast.dump(tree, include_attributes=True) == before for tree, before in trees)
    assert not hasattr(result.proc, "source_plan")

    namespace, _sites, native_hits = load_source(SOURCE)
    native = namespace["M"]()
    for value in range(4):
        native_hits.clear()
        expected = native.process(value)
        _state, output, hits, error = run_step(result.proc, {"in_a": value})
        assert not error
        assert output.transactions == [expected]
        assert hits == set(native_hits)


def test_failed_rough_pass_does_not_lose_the_original_declaration(monkeypatch):
    plans = _capture_plans(monkeypatch)
    lower = lowering.lower_procedure
    calls = []

    def rough_failure(*args, **kwargs):
        proc = lower(*args, **kwargs)
        calls.append(proc)
        if len(calls) == 1:
            raise LoweringError(proc, "fixture: rough range unavailable", code="rough-range")
        return proc

    monkeypatch.setattr(lowering, "lower_procedure", rough_failure)
    result = lowering.lower_source(SOURCE, input_widths=[2], bound=3)
    assert len(calls) == len(plans) == 2
    assert result.proc is calls[1]
    assert result.catalogue is plans[0].manifest


def test_range_pass_cannot_replace_the_goal_denominator(monkeypatch):
    prepare = source_plan.plan_source
    calls = []

    def changed(*args, **kwargs):
        plan = prepare(*args, **kwargs)
        calls.append(plan)
        if len(calls) == 2:
            return replace(plan, manifest=catalogue_subset(
                plan.manifest, tuple(plan.manifest)[1:]))
        return plan

    monkeypatch.setattr(source_plan, "plan_source", changed)
    with pytest.raises(LoweringError) as caught:
        lowering.lower_source(SOURCE, input_widths=[2])
    assert caught.value.code == "goals-declaration-changed"


@pytest.mark.parametrize("factory", ("schema", "task"))
def test_production_factory_never_rebuilds_a_source_catalogue(monkeypatch, factory):
    from behave.sim.behavior.instance import lower
    from behave.sim.behavior.task_boundary import lower_task_behavior

    from .test_api_path import contract

    results = []
    lower_source = lowering.lower_source

    def record(*args, **kwargs):
        result = lower_source(*args, **kwargs)
        results.append(result)
        return result

    def no_rebuild(*args, **kwargs):
        raise AssertionError("the factory must retain lowering's declared manifest")

    monkeypatch.setattr(lowering, "lower_source", record)
    monkeypatch.setattr(catalogue, "source_frontend_catalogue", no_rebuild)
    if factory == "schema":
        instance = lower(SOURCE, _schema(), bound=3)
    else:
        instance, _boundary = lower_task_behavior(SOURCE, "behavior.py", contract(), 3)
    assert len(results) == 1
    assert instance.frontend_catalogue_manifest() is results[0].catalogue


def test_complete_source_projection_keeps_identity_and_detects_missing_goals():
    plan = source_plan.plan_source(source_model(SOURCE), SOURCE)
    # Hand-built coverage-only components isolate the accounting rule.
    coverage = catalogue_subset(
        plan.manifest, (g for g in plan.manifest if plan.manifest[g].cls == "coverage"))
    ids = tuple(coverage)
    all_marks = ir.Procedure((), (), (), (), ir._sequence([ir.Mark(g) for g in ids]))
    assert catalogue._project_source_frontend(
        all_marks, coverage, complete=True) is coverage
    partial = ir.Procedure((), (), (), (), ir._sequence([ir.Mark(g) for g in ids[1:]]))
    with pytest.raises(Refused, match="behavior-frontend-catalogue-incomplete"):
        catalogue._project_source_frontend(partial, coverage, complete=True)
    assert set(catalogue._project_source_frontend(partial, coverage)) == set(ids[1:])


def test_analysis_limits_are_not_source_goals_and_names_do_not_classify_errors():
    manifest = CatalogueManifest([
        Goal("contains-capacity", "safety", family="divzero"),
        Goal("source-index", "safety", family="indexerror"),
    ])
    proc = ir.Procedure((), (), (), (), ir._sequence([
        ir.Fail("CapacityExceeded", "representation-limit"),
        ir.Fail("Unwound", "loop-limit"),
        ir.Fail("DivZero", "contains-capacity"),
        ir.Fail("IndexError", "source-index"),
    ]))
    assert catalogue._project_source_frontend(proc, manifest, complete=True) is manifest
    assert catalogue.check_source_producers(proc, manifest) == set(manifest)


def test_source_append_capacity_keeps_real_partial_operations_in_the_catalogue():
    source = """class M:
    def reset(self):
        pass
    def process(self, a):
        values = []
        for i in range(3):
            values.append(i)
        return {"y": values[a] // a}
"""
    result = lowering.lower_source(source, input_widths=[3], bound=1)
    failures = [command for command in ir.walk(result.proc.body)
                if isinstance(command, ir.Fail)]
    limits = {command.goal for command in failures if command.code == "CapacityExceeded"}
    assert limits and limits.isdisjoint(result.catalogue)
    assert {goal.family for goal in result.catalogue.values() if goal.cls == "safety"} == {
        "indexerror", "divzero"}
    namespace, _sites, _native_hits = load_source(source)
    native = namespace["M"]()
    for value, expected in ((0, "DivZero"), (1, ""), (3, "IndexError")):
        _state, outputs, hits, error = run_step(result.proc, {"a": value})
        assert error == expected
        assert hits <= set(result.catalogue)
        if expected:
            with pytest.raises({"DivZero": ZeroDivisionError, "IndexError": IndexError}[expected]):
                native.process(value)
        else:
            assert outputs.transactions == [native.process(value)]


def test_atomic_accounting_keeps_one_manifest_without_component_catalogues(monkeypatch):
    from behave.sim.behavior.atomic_exact import _atomic_frontend_manifest

    plan = source_plan.plan_source(source_model(SOURCE), SOURCE)
    manifest = catalogue_subset(
        plan.manifest, (g for g in plan.manifest if plan.manifest[g].cls == "coverage"))
    ids = tuple(manifest)

    def component(names):
        return SimpleNamespace(
            proc=ir.Procedure((), (), (), (), ir._sequence([ir.Mark(g) for g in names])),
            ready=None)

    def no_projection(*args, **kwargs):
        raise AssertionError("atomic accounting needs ID sets, not copied catalogues")

    monkeypatch.setattr(catalogue, "catalogue_subset", no_projection)
    # A shared helper occurrence may be produced in more than one component.
    components = {"a": component(ids[:2]), "b": component(ids[1:])}
    assert _atomic_frontend_manifest(manifest, components) is manifest
    components["a"] = component(ids[1:2])
    with pytest.raises(Refused, match="behavior-atomic-frontend-catalogue-incomplete"):
        _atomic_frontend_manifest(manifest, components)


@pytest.mark.solver
def test_atomic_schedule_rebuild_retains_the_prepared_full_source_manifest(monkeypatch):
    pytest.importorskip("z3")
    from behave.sim.behavior import atomic_exact

    from .test_behavior_atomic_exact import _instance

    plans = _capture_plans(monkeypatch)
    checked = []
    check = atomic_exact._atomic_frontend_manifest

    def record(manifest, components):
        checked.append(manifest)
        return check(manifest, components)

    monkeypatch.setattr(atomic_exact, "_atomic_frontend_manifest", record)
    instance = _instance()
    assert len(checked) == 2
    assert checked[0] is checked[1] is instance.frontend_catalogue_manifest()
    assert checked[0] is plans[0].manifest
