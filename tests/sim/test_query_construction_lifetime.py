"""Discarded or completed continuations do not wait for cyclic collection."""

import gc
import weakref

import pytest
import z3

from behave.sim.symbolic.exact_formula import FormulaDefinition, _FormulaConstruction
from behave.sim.symbolic import encoder
from behave.sim.engines import local_ir, probing

from .test_local_query_sharing import problem

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


def test_discarded_encoding_continuation_releases_its_owner(no_cyclic_collection):
    proc, env = problem()

    def paused():
        raise encoder._EncodingPaused

    def query():
        owner = encoder._StepConstruction()
        assert encoder._encode_step(proc, env=env, _construction=owner,
                                    _checkpoint=paused) is None
        return weakref.ref(owner)

    assert query()() is None


@pytest.mark.parametrize("complete", [False, True])
def test_registration_does_not_own_itself_through_its_checkpoint(no_cyclic_collection, complete):
    def query():
        x, y = z3.BitVecs("lifetime_x lifetime_y", 8)
        owner = _FormulaConstruction((FormulaDefinition("y", y, x + 1),), (), (x,))
        if complete:
            owner.advance(lambda: None)
        return weakref.ref(owner)

    assert query()() is None


def test_returned_formula_does_not_keep_its_query_owner(no_cyclic_collection, monkeypatch):
    monkeypatch.setattr(type(z3.Solver()), "check", lambda self: z3.unknown)

    def query():
        proc, env = problem()
        prepared, constructions = local_ir._LocalQueryPreparation(proc, env), {}
        answer = prepared.probe({"s": 0}, ("hit", "bad"), _constructions=constructions,
            timeout_ms=60_000)
        assert answer.outcome is probing.Outcome.UNKNOWN
        owner = next(iter(constructions.values()))
        assert owner.formula is not None
        return weakref.ref(owner), owner.formula

    owner, formula = query()
    assert owner() is None
    # A retained formula remains usable independently of registration ownership.
    assert formula.full_constraints
