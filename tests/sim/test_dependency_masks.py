"""Packed scheduling cones preserve the old exact dependency sets and batches."""

import random
from types import MappingProxyType

import pytest
import z3

from behave.sim.symbolic.exact_formula import ExactFormula, FormulaDefinition
from behave.sim.behavior.exact_solver import _component_partitions


def labels(formula, mask):
    if mask is None:
        return None
    assert mask >= 0 and mask.bit_length() <= len(formula.definitions)
    return frozenset(item.label for i, item in enumerate(formula.definitions)
                     if mask & (1 << i))


def product(generated, seed):
    rng = random.Random(seed)
    u = z3.BitVec(f"mask_u_{seed}", 8)
    shared = (u + 3) ^ 7
    values = [shared]
    definitions = []
    for i in range(48):
        atom = z3.BitVec(f"mask_x_{seed}_{i}", 8)
        rhs = rng.choice(values) + rng.choice(values) + shared
        definitions.append(FormulaDefinition(str(i), atom, rhs))
        values.append(atom)
    if not generated:
        rng.shuffle(definitions)  # Generic registration need not be ordered.
    constructor = ExactFormula._from_generated_qfbv if generated else ExactFormula
    return constructor(definitions=definitions, restrictions=(values[12] != 0,),
                       boundary_atoms=(u,)), tuple(v == i for i, v in enumerate(values))


@pytest.mark.parametrize("generated", [False, True])
@pytest.mark.parametrize("seed", range(6))
def test_masks_match_native_adjacency_and_preserve_packing(generated, seed):
    formula, targets = product(generated, seed)
    targets += (targets[0], z3.BoolVal(True), z3.BoolVal(False))
    old = formula.dependency_cones(targets)
    masks = formula.dependency_masks(targets)
    assert tuple(labels(formula, mask) for mask in masks) == old
    assert masks[-3] == masks[0]  # Duplicate query roots are not new coordinates.
    assert formula.dependency_masks(tuple(reversed(targets))) == tuple(reversed(masks))
    groups = tuple((target,) for target in targets)
    for ceiling in (False, True):
        for preserve in (False, True):
            options = {"count_ceiling": ceiling, "preserve_largest_cone": preserve}
            packed = _component_partitions(formula, groups, 4, **options)
            reference = _component_partitions(formula, groups, 4,
                dependency_cones=formula.dependency_cones, **options)
            assert packed == reference


@pytest.mark.parametrize("generated", [False, True])
def test_mask_batch_reads_only_fresh_targets_after_registration(generated, monkeypatch):
    formula, targets = product(generated, 19)
    expected = formula.dependency_cones(targets)
    calls = []
    original = ExactFormula._dependencies

    def observe(self, *args):
        calls.append(args[-1])
        return original(self, *args)

    monkeypatch.setattr(ExactFormula, "_dependencies", observe)
    graph = formula._dependency_edges
    assert tuple(labels(formula, mask) for mask in formula.dependency_masks(targets)) == expected
    assert calls == ["query target"] * len(targets)
    assert formula._dependency_edges is graph
    assert formula.dependency_masks(()) == ()


def test_mask_fallback_is_local_to_an_unknown_target():
    formula, targets = product(True, 20)
    unknown = z3.Bool("mask_unknown")
    good = formula.dependency_masks(targets)
    assert formula.dependency_masks((unknown, *targets, unknown)) == (None, *good, None)
    assert ExactFormula().dependency_masks((z3.BoolVal(True), z3.BoolVal(False))) == (0, 0)
    with pytest.raises(TypeError, match="Boolean"):
        formula.dependency_masks((z3.BitVecVal(1, 8),))
    with pytest.raises(ValueError, match="context"):
        formula.dependency_masks((z3.BoolVal(True, ctx=z3.Context()),))


@pytest.mark.parametrize("cycle", [False, True])
def test_broken_registered_graph_never_reports_an_empty_cone(cycle):
    formula, _ = product(True, 21)
    graph = dict(formula._dependency_edges)
    atom = formula.definitions[0].lhs.get_id()
    if cycle:
        graph[atom] = (atom,)
    else:
        del graph[atom]
    object.__setattr__(formula, "_dependency_edges", MappingProxyType(graph))
    assert formula.dependency_masks((formula.definitions[0].lhs == 0,)) == (None,)


def test_independent_mask_audit_rejects_dropped_requester_bit(monkeypatch):
    u, a, b = z3.BitVecs("mask_check_u mask_check_a mask_check_b", 8)
    formula = ExactFormula(definitions=(FormulaDefinition("a", a, u + 1),
                                       FormulaDefinition("b", b, a + 1)),
                           boundary_atoms=(u,))
    original = ExactFormula._audit_dependency_requests

    def corrupt(self, indices, requests, order):
        requests[a.get_id()] = 0
        original(self, indices, requests, order)

    monkeypatch.setattr(ExactFormula, "_audit_dependency_requests", corrupt)
    assert formula.dependency_masks((b == 3,)) == (None,)


def test_base_registration_failure_and_custom_cone_authority_are_preserved():
    a, b = z3.BitVecs("mask_cycle_a mask_cycle_b", 8)
    formula = ExactFormula(definitions=(FormulaDefinition("a", a, b),
                                       FormulaDefinition("b", b, a)))
    assert formula.dependency_masks((a == 0, b == 0)) == (None, None)
    formula, targets = product(True, 22)
    groups = tuple((t,) for t in targets[:5])
    called = []

    def custom(targets):
        called.append(targets)
        return (None,) * len(targets)

    assert _component_partitions(formula, groups, 2, count_ceiling=False,
        preserve_largest_cone=False, dependency_cones=custom) == (
            groups[:2], groups[2:4], groups[4:])
    assert called == [targets[:5]]
