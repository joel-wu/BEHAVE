"""Temporary transition views release private solver contexts promptly."""

import gc
import weakref

import pytest
import z3

from behave.sim.symbolic import ir, ranges, typecheck as types
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.symbolic.step_template import StepTemplate
from behave.sim.symbolic.template_arrays import TemplateArrays
from behave.sim.core.contract import FiniteType

from .test_step_template_projection import _template

pytestmark = pytest.mark.solver


@pytest.fixture
def no_cyclic_collection():
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()
        gc.collect()


def _array_template(nested):
    initial = [[1, -2], [3, -4]] if nested else [1, -2]
    selected = ir.Sel(ir.Var("cells"), ir.Lit(0))
    if nested:
        selected = ir.Sel(selected, ir.Lit(0))
    value = ir.Op("arraylit", ir.Var("a"), ir.Lit(0)) if nested else ir.Var("a")
    procedure = ir.Procedure(("cells",), ("a",), (), (), ir._sequence((
        ir.If(ir.Op("lt", selected, ir.Lit(0)), ir.Mark("target"), ir.Skip()),
        ir.Store("cells", ir.Lit(1), value),
    )), initial={"cells": initial},
        types={"cells": types.Array(2, types.Array(2)) if nested else types.Array(2)},
        signature=ProcedureSignature((("a", FiniteType(5, signed=True)),), ()))
    return StepTemplate(procedure, {"cells": ranges.Range(-16, 15),
                                     "a": ranges.Range(-16, 15)})


@pytest.mark.parametrize("carrier", ("scalar", "array", "nested"))
def test_completed_template_has_no_constructor_closure_owning_it(no_cyclic_collection, carrier):
    def build():
        template = _template() if carrier == "scalar" else _array_template(carrier == "nested")
        return weakref.ref(template)

    assert build()() is None


@pytest.mark.parametrize("choice", (False, True))
def test_frozen_array_recipes_do_not_retain_construction_terms(no_cyclic_collection, choice):
    recipes = TemplateArrays()

    def build():
        value = z3.BitVecVal(0, 8)
        array = FunctionalArray.uniform(4, value)
        if choice:
            other = FunctionalArray.uniform(4, z3.BitVecVal(1, 8))
            array = FunctionalArray.choose(z3.Bool("choice"), array, other)
        recipes.term(array)
        recipes.freeze()
        return weakref.ref(value)

    assert build()() is None
    assert recipes.constructors  # Reusable recipes remain, but no original AST wrapper.


@pytest.mark.parametrize("carrier", ("scalar", "array", "nested"))
@pytest.mark.parametrize("operation", ("translate", "encode", "project"))
def test_temporary_view_has_no_recursive_closure_owning_its_context(operation, carrier):
    prepared = _template() if carrier == "scalar" else _array_template(carrier == "nested")
    contexts = []

    def query():
        context = z3.Context()
        contexts.append(weakref.ref(context))
        view = prepared.translate(context)
        if operation == "project":
            view = view.project({"hit": ("target",)}, outputs=())
        if operation != "translate":
            first = view.encode()
            second = view.encode(first.state_post)
            solver = z3.Solver(ctx=context)
            solver.add(first.constraint, second.constraint)
            assert solver.check() == z3.sat

    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        for _ in range(3):
            query()
            assert contexts[-1]() is None
    finally:
        if enabled:
            gc.enable()
        gc.collect()
