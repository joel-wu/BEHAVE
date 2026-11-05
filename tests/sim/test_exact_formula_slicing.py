"""Exhaustive checks for the frontend-neutral definitional slicing seam."""

import itertools

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic.exact_formula import (
    ExactFormula,
    FormulaDefinition,
    SliceFallbackCode,
    SliceMode,
    _FormulaConstruction,
    _finish_steps,
)


def _answer(assertions):
    solver = z3.Solver()
    solver.add(*assertions)
    return solver.check()


@pytest.mark.parametrize("kind", ["boolean", "integer", "bitvector"])
def test_definition_builder_preserves_sort_context_and_equation(kind):
    context = z3.Context()
    rhs = {"boolean": lambda: z3.Bool("input", ctx=context),
           "integer": lambda: z3.Int("input", ctx=context) + 1,
           "bitvector": lambda: z3.BitVec("input", 7, ctx=context) + 1}[kind]()
    definition = FormulaDefinition.define("field", "output", rhs)
    supplied = FormulaDefinition("field", z3.Const("output", rhs.sort()), rhs)
    assert definition.label == supplied.label
    assert definition.lhs.ctx == context
    assert definition.lhs.sort().eq(rhs.sort())
    assert definition.rhs is rhs
    assert definition.assertion.eq(supplied.assertion)
    with pytest.raises(AttributeError):
        definition.rhs = rhs


@pytest.mark.parametrize("label,symbol,rhs,error", [
    ("", "out", z3.BoolVal(True), ValueError),
    ("field", "", z3.BoolVal(True), ValueError),
    ("field", "out", 1, TypeError),
])
def test_definition_builder_checks_its_inputs(label, symbol, rhs, error):
    with pytest.raises(error):
        FormulaDefinition.define(label, symbol, rhs)


def _answers_for_boundary(formula, sliced, boundary, values):
    answers = []
    for row in itertools.product(values, repeat=len(boundary)):
        pin = tuple(atom == z3.IntVal(value)
                    for atom, value in zip(boundary, row))
        answers.append((
            _answer(formula.full_constraints + pin + (sliced.target,)),
            _answer(sliced.constraints + pin + (sliced.target,)),
        ))
    return answers


def test_backward_closure_drops_only_unobservable_definitions():
    u, v = z3.Ints("slice_drop_u slice_drop_v")
    guard = z3.Bool("slice_drop_guard")
    selected = z3.Int("slice_drop_selected")
    hit = z3.Bool("slice_drop_hit")
    dead0, dead1 = z3.Ints("slice_drop_dead0 slice_drop_dead1")
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("guard", guard, u > 0),
            FormulaDefinition("selected", selected, z3.If(guard, u, v)),
            FormulaDefinition("hit", hit, selected == 1),
            FormulaDefinition("dead-0", dead0, u + 20),
            FormulaDefinition("dead-1", dead1, dead0 * 2),
        ),
        boundary_atoms=(u, v),
    )

    sliced = formula.slice(hit)

    assert sliced.mode is SliceMode.SLICED
    assert sliced.kept_definitions == ("guard", "selected", "hit")
    assert sliced.dropped_definitions == ("dead-0", "dead-1")
    assert formula.dependency_cone(hit) == frozenset(
        {"guard", "selected", "hit"})
    assert formula.dependency_roots(hit) == frozenset({"hit"})
    answers = _answers_for_boundary(formula, sliced, (u, v), (-1, 0, 1))
    assert answers and all(full == reduced for full, reduced in answers)


def test_backward_closure_visits_a_shared_expression_dag_once():
    boundary = z3.Bool("slice_shared_boundary")
    shared = boundary
    for index in range(30):
        guard = z3.Bool(f"slice_shared_guard_{index}")
        shared = z3.If(guard, shared, shared)
    result = z3.Bool("slice_shared_result")
    formula = ExactFormula(
        definitions=(FormulaDefinition("result", result, shared),),
        boundary_atoms=(boundary, *(
            z3.Bool(f"slice_shared_guard_{index}") for index in range(30))),
    )

    sliced = formula.slice(result)

    assert sliced.mode is SliceMode.SLICED
    assert sliced.kept_definitions == ("result",)


def test_one_formula_compiles_its_static_dependency_graph_once(monkeypatch):
    u = z3.BitVec("slice_compiled_u", 8)
    x = z3.BitVec("slice_compiled_x", 8)
    first = z3.Bool("slice_compiled_first")
    second = z3.Bool("slice_compiled_second")
    calls = []
    original = ExactFormula._dependencies

    def observed(self, *args, **kwargs):
        calls.append(args[-1])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ExactFormula, "_dependencies", observed)
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("x", x, u + 1),
            FormulaDefinition("first", first, x == 1),
            FormulaDefinition("second", second, x == 2),
        ),
        restrictions=(u <= 10,),
        boundary_atoms=(u,),
    )
    construction_calls = tuple(calls)
    full = formula.full_constraints

    formula.slice(first)
    after_first = tuple(calls)
    formula.slice(second)

    assert construction_calls == ()  # Registration shares its expression-DAG audit.
    assert after_first == construction_calls + ("query target",)
    assert tuple(calls) == after_first + ("query target",)
    assert formula.full_constraints is full


@pytest.mark.parametrize("generated", [False, True])
def test_bulk_cones_reuse_adjacency_but_keep_every_exact_closure(generated, monkeypatch):
    u = z3.BitVec("bulk_u", 16)
    chain = [u]
    definitions = []
    for i in range(32):
        atom = z3.BitVec(f"bulk_x{i}", 16)
        definitions.append(FormulaDefinition(f"x{i}", atom, chain[-1] + 1))
        chain.append(atom)
    hits = tuple(z3.Bool(f"bulk_hit{i}") for i in range(16))
    definitions.extend(FormulaDefinition(f"hit{i}", hit, chain[-1] == i)
                       for i, hit in enumerate(hits))
    constructor = ExactFormula._from_generated_qfbv if generated else ExactFormula
    formula = constructor(definitions=definitions, boundary_atoms=(u,),
                          restrictions=(chain[1] != 0,))
    calls = []
    original = ExactFormula._definition_dependencies
    def observe(self, atom):
        calls.append(atom)
        return original(self, atom)
    monkeypatch.setattr(ExactFormula, "_definition_dependencies", observe)
    separate = tuple(formula.dependency_cone(hit) for hit in hits)
    separate_calls = len(calls)
    calls.clear()
    assert formula.dependency_cones(hits) == separate
    assert len(calls) == len(set(calls))
    assert len(calls) <= len(definitions) < separate_calls
    for i, selected in enumerate(separate):
        assert f"hit{i}" in selected
        assert all(f"hit{j}" not in selected for j in range(16) if j != i)
    # A fresh bulk call does not inherit an externally mutable proof cache.
    calls.clear()
    assert formula.dependency_cones(hits) == separate
    assert calls


@pytest.mark.parametrize("generated", [False, True])
def test_slice_reuses_registered_shared_rhs_dag(generated, monkeypatch):
    u = z3.BitVec("linear_closure_u", 16)
    shared = u
    for i in range(128):
        shared = (shared ^ i) + i
    atoms = tuple(z3.BitVec(f"linear_closure_x{i}", 16) for i in range(64))
    constructor = ExactFormula._from_generated_qfbv if generated else ExactFormula
    formula = constructor(
        definitions=tuple(FormulaDefinition(str(i), atom, shared + i)
                          for i, atom in enumerate(atoms)),
        boundary_atoms=(u,))
    target = z3.Or([atom == 3 for atom in atoms])
    expected = formula._needed_definitions(target, direct_cache={})
    visits = []
    original = z3.ExprRef.children
    def children(term):
        visits.append(term.get_id())
        return original(term)
    monkeypatch.setattr(z3.ExprRef, "children", children)
    assert formula._needed_definitions(target) == expected
    # Only the fresh target enters Z3. Both closure passes reuse registered edges.
    assert shared.get_id() not in visits
    assert len(visits) < 150
    selected = formula.slice(target)
    assert len(selected.kept_definitions) == len(atoms)
    assert selected.mode is SliceMode.SLICED


@pytest.mark.parametrize("generated", [False, True])
def test_linear_slice_closure_keeps_restrictions_and_transitive_dependencies(generated):
    u, a, b, spare = z3.BitVecs("linear_u linear_a linear_b linear_spare", 8)
    constructor = ExactFormula._from_generated_qfbv if generated else ExactFormula
    formula = constructor(definitions=(
        FormulaDefinition("a", a, u + 1), FormulaDefinition("b", b, a + 1),
        FormulaDefinition("spare", spare, u * 2)),
        restrictions=(b != 0,), boundary_atoms=(u,))
    for target in (z3.BoolVal(True), a == 1, spare == 2, z3.And(a == 1, spare == 2)):
        assert formula._needed_definitions(target) == \
            formula._needed_definitions(target, direct_cache={})
    assert formula.slice(a == 1).kept_definitions == ("a", "b")
    assert formula.slice(a == 1).dropped_definitions == ("spare",)


def test_linear_reachability_still_has_an_independent_completeness_check(monkeypatch):
    u, a, b = z3.BitVecs("linear_fault_u linear_fault_a linear_fault_b", 8)
    formula = ExactFormula._from_generated_qfbv(definitions=(
        FormulaDefinition("a", a, u + 1), FormulaDefinition("b", b, a + 1)),
        boundary_atoms=(u,))
    walk = ExactFormula._walk_registered
    def omit_transitive_definition(self, *args, **kwargs):
        found = walk(self, *args, **kwargs)
        return found - {a.get_id()} if kwargs.get("expand") else found
    monkeypatch.setattr(ExactFormula, "_walk_registered", omit_transitive_definition)
    selected = formula.slice(b == 3)
    assert selected.mode is SliceMode.FULL
    assert selected.fallback.code is SliceFallbackCode.INCOMPLETE_CLOSURE


def test_registered_dependency_graph_is_closed_and_shared_across_queries():
    u = z3.BitVec("registered_graph_u", 8)
    values = tuple(z3.BitVec(f"registered_graph_{i}", 8) for i in range(16))
    shared = u
    for i in range(96):
        shared = (shared + i) ^ u
    formula = ExactFormula._from_generated_qfbv(
        definitions=tuple(FormulaDefinition(str(i), value, shared + i)
                          for i, value in enumerate(values)),
        boundary_atoms=(u,))
    graph = formula._dependency_edges
    before = dict(graph)
    assert len(graph) < 400  # a shared DAG, not sixteen expanded RHS copies
    for value in values:
        target = value == 7
        assert formula._needed_definitions(target) == \
            formula._needed_definitions(target, direct_cache={})
    alien = z3.BitVec("registered_graph_fresh_unknown", 8)
    assert formula.slice(alien == 7).fallback.code is SliceFallbackCode.UNKNOWN_ATOM
    assert formula._dependency_edges is graph and dict(graph) == before
    with pytest.raises(TypeError):
        graph[u.get_id()] = ()


def test_missing_registered_edge_falls_back_instead_of_dropping_a_dependency(monkeypatch):
    u, a = z3.BitVecs("registered_missing_u registered_missing_a", 8)
    formula = ExactFormula._from_generated_qfbv(
        definitions=(FormulaDefinition("a", a, u + 1),), boundary_atoms=(u,))
    walk = ExactFormula._walk_registered
    def missing_node(self, roots, by_id, edges, **kwargs):
        incomplete = {key: value for key, value in edges.items() if key != u.get_id()}
        return walk(self, roots, by_id, incomplete, **kwargs)
    monkeypatch.setattr(ExactFormula, "_walk_registered", missing_node)
    selected = formula.slice(a == 2)
    assert selected.mode is SliceMode.FULL
    assert selected.fallback.code is SliceFallbackCode.INCOMPLETE_CLOSURE
    assert selected.constraints == formula.full_constraints


@pytest.mark.parametrize("generated", [False, True])
def test_bulk_cones_keep_unknown_atom_fallback_local_to_its_target(generated):
    u, x = z3.BitVecs("bulk_fallback_u bulk_fallback_x", 8)
    alien = z3.Bool("bulk_fallback_alien")
    constructor = ExactFormula._from_generated_qfbv if generated else ExactFormula
    formula = constructor(definitions=(FormulaDefinition("x", x, u + 1),),
                          boundary_atoms=(u,))
    targets = (x == 1, alien, x == 2)
    assert formula.dependency_cones(targets) == (
        frozenset({"x"}), None, frozenset({"x"}))
    selected = formula.slice(alien)
    assert selected.mode is SliceMode.FULL
    assert selected.fallback.code is SliceFallbackCode.UNKNOWN_ATOM


@pytest.mark.parametrize("generated", [False, True])
def test_bulk_cones_do_not_hide_cyclic_definitions(generated):
    a, b = z3.BitVecs("bulk_cycle_a bulk_cycle_b", 8)
    constructor = ExactFormula._from_generated_qfbv if generated else ExactFormula
    formula = constructor(definitions=(FormulaDefinition("a", a, b + 1),
                                       FormulaDefinition("b", b, a + 1)))
    assert formula.dependency_cones((a == 0, b == 0)) == (None, None)
    assert formula.slice(a == 0).fallback.code is SliceFallbackCode.CYCLIC_DEFINITIONS


def test_generated_qfbv_registers_the_extension_of_every_slice(
        monkeypatch):
    u = z3.BitVec("slice_generated_u", 8)
    x = z3.BitVec("slice_generated_x", 8)
    hit = z3.Bool("slice_generated_hit")
    dead = z3.BitVec("slice_generated_dead", 8)
    calls = []
    ordered = []
    walks = []
    original = ExactFormula._dependencies
    original_ordered = ExactFormula._ordered_dependency_steps
    original_walk = ExactFormula._walk_registered

    def observed(self, *args, **kwargs):
        calls.append(args[-1])
        return original(self, *args, **kwargs)

    def observed_ordered(self, *args, **kwargs):
        ordered.append(args[-1])
        return original_ordered(self, *args, **kwargs)

    def observed_walk(self, *args, **kwargs):
        walks.append(kwargs["expand"])
        return original_walk(self, *args, **kwargs)

    monkeypatch.setattr(ExactFormula, "_dependencies", observed)
    monkeypatch.setattr(ExactFormula, "_walk_registered", observed_walk)
    monkeypatch.setattr(
        ExactFormula, "_ordered_dependency_steps", observed_ordered)
    formula = ExactFormula._from_generated_qfbv(
        definitions=(
            FormulaDefinition("x", x, u + 1),
            FormulaDefinition("hit", hit, x == 3),
            FormulaDefinition("dead", dead, u + 9),
        ),
        restrictions=(u <= 10,),
        boundary_atoms=(u,),
    )

    assert ordered == [
        "definition 'x'", "definition 'hit'", "definition 'dead'",
        "hard restriction 0",
    ]
    assert calls == []
    selected = formula.slice(hit)

    assert selected.mode is SliceMode.SLICED
    assert selected.kept_definitions == ("x", "hit")
    assert selected.dropped_definitions == ("dead",)
    assert formula.sliced_sat_extends
    assert formula.is_qfbv_with(hit)
    assert calls == ["query target"]
    assert walks[-2:] == [True, False]


@pytest.mark.parametrize('generated', [False, True])
def test_formula_registration_reuses_a_shared_term_dag(monkeypatch, generated):
    u = z3.BitVec("slice_generated_shared_u", 16)
    shared = u
    for value in range(64):
        shared = (shared + value) ^ z3.BitVecVal(value, 16)
    outputs = tuple(
        z3.BitVec(f"slice_generated_shared_{index}", 16)
        for index in range(12))
    growth = []
    original = ExactFormula._ordered_dependency_steps

    def observed(self, *args, **kwargs):
        memo = args[-2]
        before = len(memo)
        result = yield from original(self, *args, **kwargs)
        growth.append(len(memo) - before)
        return result

    monkeypatch.setattr(
        ExactFormula, "_ordered_dependency_steps", observed)
    constructor = ExactFormula._from_generated_qfbv if generated else ExactFormula
    constructor(
        definitions=tuple(
            FormulaDefinition(f"shared-{index}", output, shared)
            for index, output in enumerate(outputs)),
        boundary_atoms=(u,),
    )

    assert growth[0] > 0
    assert growth[1:] == [0] * (len(outputs) - 1)


@pytest.mark.parametrize('private_context', [False, True])
def test_generic_shared_registration_keeps_forward_integer_array_dependencies(private_context):
    ctx = z3.Context() if private_context else z3.main_ctx()
    u, later, out, unused = z3.Ints('generic_u generic_later generic_out generic_unused', ctx=ctx)
    array = z3.Array('generic_array', z3.IntSort(ctx=ctx), z3.IntSort(ctx=ctx))
    function = z3.Function('generic_function', z3.IntSort(ctx=ctx), z3.IntSort(ctx=ctx))
    common = z3.Select(array, u) + function(u)
    formula = ExactFormula(definitions=(
        FormulaDefinition('out', out, later + common),
        FormulaDefinition('later', later, u + common),
        FormulaDefinition('unused', unused, common * 2)),
        restrictions=(u >= 0, u <= 2, later <= 20),
        boundary_atoms=(u, array), external_functions=(function,))
    selected = formula.slice(out == 7)
    assert selected.mode is SliceMode.SLICED
    assert selected.kept_definitions == ('out', 'later')
    assert selected.dropped_definitions == ('unused',)
    assert not formula.sliced_sat_extends  # No new authority for generic SAT.
    for value in range(3):
        full, cut = z3.Solver(ctx=ctx), z3.Solver(ctx=ctx)
        full.add(*formula.full_constraints, selected.target, u == value)
        cut.add(*selected.query_assertions, u == value)
        assert full.check() == cut.check()


@pytest.mark.parametrize('fault, code', [
    ('cycle', SliceFallbackCode.CYCLIC_DEFINITIONS),
    ('unknown', SliceFallbackCode.UNKNOWN_ATOM),
    ('protected', SliceFallbackCode.RAW_TERM)])
def test_shared_generic_registration_cannot_hide_bad_unused_definitions(fault, code):
    u, out, dead, other = z3.Ints('bad_shared_u bad_shared_out bad_shared_dead bad_shared_other')
    common = u + 1
    invalid = dead + common if fault == 'cycle' else other + common
    formula = ExactFormula(definitions=(FormulaDefinition('out', out, common),
        FormulaDefinition('unused', dead, invalid)), boundary_atoms=(u,),
        protected_terms=(other,) if fault == 'protected' else ())
    selected = formula.slice(out == 3)
    assert selected.mode is SliceMode.FULL
    assert selected.fallback.code is code
    assert selected.constraints == formula.full_constraints


def test_generic_registration_does_not_expand_shared_definition_dependencies(monkeypatch):
    u, base = z3.Ints('linear_generic_u linear_generic_base')
    common = base
    for index in range(256):
        common = z3.If(u > index, common + 1, common - 1)
    outputs = tuple(z3.Int(f'linear_generic_out{i}') for i in range(128))
    calls = []
    original = ExactFormula._walk_registered_steps

    def observed(self, roots, *args, **kwargs):
        roots = tuple(roots)
        calls.append(roots)
        return (yield from original(self, roots, *args, **kwargs))

    monkeypatch.setattr(ExactFormula, '_walk_registered_steps', observed)
    formula = ExactFormula(definitions=(FormulaDefinition('base', base, u), *(
        FormulaDefinition(str(i), out, common + i) for i, out in enumerate(outputs))),
        restrictions=tuple(common >= i for i in range(64)), boundary_atoms=(u,))
    assert len(calls) == 2  # One restriction union and its closure, not one per RHS.
    assert not formula.sliced_sat_extends
    selected = formula.slice(outputs[0] == 7)
    assert selected.kept_definitions == ('base', '0')
    assert len(selected.dropped_definitions) == len(outputs) - 1


@pytest.mark.parametrize('cycle', (False, True))
def test_generic_long_forward_chain_uses_iterative_complete_cycle_audit(cycle):
    u = z3.Int('long_forward_u')
    atoms = tuple(z3.Int(f'long_forward_{i}') for i in range(1500))
    definitions = tuple(FormulaDefinition(str(i), atom,
        atoms[i + 1] + 1 if i + 1 < len(atoms) else atoms[0] if cycle else u)
        for i, atom in enumerate(atoms))
    formula = ExactFormula(definitions=definitions, boundary_atoms=(u,))
    selected = formula.slice(atoms[0] == 0)
    if cycle:
        assert selected.mode is SliceMode.FULL
        assert selected.fallback.code is SliceFallbackCode.CYCLIC_DEFINITIONS
    else:
        assert len(selected.kept_definitions) == len(atoms)
        assert selected.fallback is None
    assert not formula.sliced_sat_extends


def test_generated_order_audit_reads_each_nodes_edges_only_once(monkeypatch):
    u = z3.BitVec("slice_order_edges_input", 8)
    x = z3.BitVec("slice_order_edges_x", 8)
    result = z3.BitVec("slice_order_edges_result", 8)
    shared = u + 1
    for index in range(32):
        shared = (shared + index) ^ shared
    definitions = (
        FormulaDefinition("x", x, u + 1),
        FormulaDefinition("result", result, shared + x),
    )
    formula = ExactFormula._from_generated_qfbv(
        definitions=definitions, boundary_atoms=(u,))
    original = z3.ExprRef.children
    visits = {}

    def children(term):
        key = term.get_id()
        visits[key] = visits.get(key, 0) + 1
        return original(term)

    monkeypatch.setattr(z3.ExprRef, "children", children)
    memo = {}
    for definition in definitions:
        _finish_steps(formula._ordered_dependency_steps(
            definition.rhs, formula._definition_by_id,
            formula._boundary_ids, formula._protected_ids,
            formula._external_function_ids, formula._definition_rank_by_id,
            memo, definition.label), lambda: None)
    assert memo[definitions[-1].rhs.get_id()] == 0  # reads x, not result
    assert visits and set(visits.values()) == {1}


def test_generated_qfbv_falls_back_if_a_reached_definition_is_not_ordered():
    u = z3.BitVec("slice_generated_order_u", 8)
    x = z3.BitVec("slice_generated_order_x", 8)
    later = z3.BitVec("slice_generated_order_later", 8)
    formula = ExactFormula._from_generated_qfbv(
        definitions=(
            FormulaDefinition("x", x, later + 1),
            FormulaDefinition("later", later, u + 1),
        ),
        boundary_atoms=(u,),
    )

    selected = formula.slice(x == 3)

    assert selected.mode is SliceMode.FULL
    assert selected.fallback.code is SliceFallbackCode.CYCLIC_DEFINITIONS


@pytest.mark.parametrize('generated', [False, True])
def test_restrictions_share_the_definition_dag_and_retain_all_roots(monkeypatch, generated):
    u, v, needed, dead = z3.BitVecs("restriction_u restriction_v restriction_needed restriction_dead", 8)
    shared = u + v
    for index in range(12):
        shared = (shared + index) ^ u
    definitions = (FormulaDefinition("needed", needed, shared),
                   FormulaDefinition("dead", dead, u + 7))
    restrictions = (needed == v, shared <= 7, shared >= 0)
    visits = {}
    children = z3.ExprRef.children

    def counted(term):
        identity = term.get_id()
        visits[identity] = visits.get(identity, 0) + 1
        return children(term)

    monkeypatch.setattr(z3.ExprRef, "children", counted)
    constructor = ExactFormula._from_generated_qfbv if generated else ExactFormula
    formula = constructor(
        definitions=definitions, restrictions=restrictions, boundary_atoms=(u, v))
    # Generic formulas also run their independent static QF_BV shape check.
    # Neither pass revisits a shared subtree once per definition/restriction.
    assert visits and max(visits.values()) <= (1 if generated else 2)
    selected = formula.slice(u == 1)
    assert selected.mode is SliceMode.SLICED
    assert selected.kept_definitions == ("needed",)
    assert selected.dropped_definitions == ("dead",)
    assert all(term in selected.constraints for term in restrictions)
    for a, b in itertools.product(range(4), repeat=2):
        full, cut = z3.Solver(), z3.Solver()
        full.add(*formula.full_constraints, selected.target, u == a, v == b)
        cut.add(*selected.query_assertions, u == a, v == b)
        assert full.check() == cut.check()


def test_generated_restriction_with_undeclared_atom_uses_the_full_formula():
    u, secret, dead = z3.BitVecs("restriction_input restriction_unknown restriction_unused", 8)
    formula = ExactFormula._from_generated_qfbv(
        definitions=(FormulaDefinition("dead", dead, u + 1),),
        restrictions=(secret == 3,), boundary_atoms=(u,))
    selected = formula.slice(u == 2)
    assert selected.mode is SliceMode.FULL
    assert selected.fallback.code is SliceFallbackCode.UNKNOWN_ATOM
    assert selected.constraints == formula.full_constraints


def test_generated_qfbv_rejects_a_bad_definition_dropped_by_the_target():
    u = z3.BitVec("slice_generated_dropped_u", 8)
    hit = z3.Bool("slice_generated_dropped_hit")
    bad = z3.BitVec("slice_generated_dropped_bad", 8)
    formula = ExactFormula._from_generated_qfbv(
        definitions=(
            FormulaDefinition("hit", hit, u == 3),
            FormulaDefinition("bad", bad, bad + 1),
        ),
        boundary_atoms=(u,),
    )

    selected = formula.slice(hit)

    assert selected.mode is SliceMode.FULL
    assert selected.fallback.code is SliceFallbackCode.CYCLIC_DEFINITIONS
    assert not formula.sliced_sat_extends


def test_generated_qfbv_audits_the_vocabulary_of_dropped_definitions():
    u = z3.BitVec("slice_generated_vocab_u", 8)
    missing = z3.BitVec("slice_generated_vocab_missing", 8)
    hit = z3.Bool("slice_generated_vocab_hit")
    dead = z3.BitVec("slice_generated_vocab_dead", 8)
    formula = ExactFormula._from_generated_qfbv(
        definitions=(
            FormulaDefinition("hit", hit, u == 3),
            FormulaDefinition("dead", dead, missing + 1),
        ),
        boundary_atoms=(u,),
    )

    selected = formula.slice(hit)

    assert selected.mode is SliceMode.FULL
    assert selected.fallback.code is SliceFallbackCode.UNKNOWN_ATOM


def test_complete_selection_audits_only_its_target_vocabulary(monkeypatch):
    u = z3.BitVec("complete_generated_u", 8)
    x = z3.BitVec("complete_generated_x", 8)
    hit = z3.Bool("complete_generated_hit")
    dead = z3.BitVec("complete_generated_dead", 8)
    calls = []
    original = ExactFormula._dependencies

    def observed(self, *args, **kwargs):
        calls.append(args[-1])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ExactFormula, "_dependencies", observed)
    formula = ExactFormula._from_generated_qfbv(
        definitions=(
            FormulaDefinition("x", x, u + 1),
            FormulaDefinition("hit", hit, x == 3),
            FormulaDefinition("dead", dead, u + 9),
        ),
        boundary_atoms=(u,),
    )

    selected = formula.complete(hit)

    assert selected.mode is SliceMode.COMPLETE
    assert selected.constraints == formula.full_constraints
    assert selected.kept_definitions == ("x", "hit", "dead")
    assert selected.dropped_definitions == ()
    assert calls == ["query target"]


def test_qfbv_membership_reuses_the_static_formula_check():
    u = z3.BitVec("slice_qfbv_u", 8)
    x = z3.BitVec("slice_qfbv_x", 8)
    formula = ExactFormula(
        definitions=(FormulaDefinition("x", x, u + 1),),
        boundary_atoms=(u,),
    )

    assert formula.is_qfbv_with(x == 3)
    assert not formula.is_qfbv_with(z3.Int("slice_qfbv_integer") == 3)
    assert formula.contains_boundary_atom(u)
    assert not formula.contains_boundary_atom(x)


def test_hard_restrictions_are_roots_even_when_the_target_is_unrelated():
    u = z3.Int("slice_retain_u")
    policy = z3.Bool("slice_retain_policy")
    goal = z3.Bool("slice_retain_goal")
    unused = z3.Int("slice_retain_unused")
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("policy", policy, u >= 0),
            FormulaDefinition("goal", goal, u == 1),
            FormulaDefinition("unused", unused, u + 100),
        ),
        restrictions=(policy,),
        boundary_atoms=(u,),
    )

    sliced = formula.slice(goal)

    assert sliced.kept_definitions == ("policy", "goal")
    assert sliced.dropped_definitions == ("unused",)
    answers = _answers_for_boundary(formula, sliced, (u,), (-1, 0, 1))
    assert all(full == reduced for full, reduced in answers)


def test_one_closure_carries_driver_memory_failure_and_monitor_dependencies():
    ints = z3.ArraySort(z3.IntSort(), z3.IntSort())
    queue = z3.Const("slice_chain_queue", ints)
    memory = z3.Const("slice_chain_memory", ints)
    index = z3.Int("slice_chain_index")
    offer, ready = z3.Bools("slice_chain_offer slice_chain_ready")
    payload = z3.Int("slice_chain_payload")
    accepted = z3.Bool("slice_chain_accepted")
    memory_next = z3.Const("slice_chain_memory_next", ints)
    observed = z3.Int("slice_chain_observed")
    failed, monitor, hit = z3.Bools(
        "slice_chain_failed slice_chain_monitor slice_chain_hit")
    dead = z3.Int("slice_chain_dead")
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("queue-data", payload, z3.Select(queue, index)),
            FormulaDefinition("driver-accept", accepted, z3.And(offer, ready)),
            FormulaDefinition(
                "memory-next", memory_next,
                z3.If(accepted, z3.Store(memory, index, payload), memory)),
            FormulaDefinition(
                "memory-observation", observed, z3.Select(memory_next, index)),
            FormulaDefinition("failure", failed, z3.And(accepted, payload < 0)),
            FormulaDefinition(
                "monitor", monitor, z3.And(accepted, observed == payload)),
            FormulaDefinition("hit", hit, z3.And(z3.Not(failed), monitor)),
            FormulaDefinition("dead", dead, index + 99),
        ),
        boundary_atoms=(queue, memory, index, offer, ready),
    )

    sliced = formula.slice(hit)

    assert sliced.mode is SliceMode.SLICED
    assert sliced.kept_definitions == (
        "queue-data", "driver-accept", "memory-next", "memory-observation",
        "failure", "monitor", "hit")
    assert sliced.dropped_definitions == ("dead",)
    for payload_value, old_value, offered, permitted in itertools.product(
            (-1, 1), (0, 1), (False, True), (False, True)):
        pins = (
            queue == z3.K(z3.IntSort(), z3.IntVal(payload_value)),
            memory == z3.K(z3.IntSort(), z3.IntVal(old_value)),
            index == 0,
            offer == offered,
            ready == permitted,
        )
        assert _answer(formula.full_constraints + pins + (hit,)) == \
            _answer(sliced.constraints + pins + (hit,))


def test_duplicate_definition_falls_back_to_the_complete_formula():
    u, x = z3.Ints("slice_duplicate_u slice_duplicate_x")
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("first", x, u),
            FormulaDefinition("second", x, u + 1),
        ),
        boundary_atoms=(u,),
    )

    sliced = formula.slice(x == 0)

    assert sliced.mode is SliceMode.FULL
    assert sliced.fallback.code is SliceFallbackCode.DUPLICATE_DEFINITION
    assert sliced.constraints == formula.full_constraints
    assert sliced.dropped_definitions == ()


def test_cyclic_definitions_fall_back_even_when_the_cycle_is_disconnected():
    u, x, y = z3.Ints("slice_cycle_u slice_cycle_x slice_cycle_y")
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("x", x, y + 1),
            FormulaDefinition("y", y, x + 1),
        ),
        boundary_atoms=(u,),
    )

    sliced = formula.slice(u == 0)

    assert sliced.mode is SliceMode.FULL
    assert sliced.fallback.code is SliceFallbackCode.CYCLIC_DEFINITIONS
    assert sliced.constraints == formula.full_constraints
    assert _answer(sliced.query_assertions) == z3.unsat


def test_raw_state_carrier_forces_full_formula_fallback():
    raw = z3.Int("slice_raw_state")
    x = z3.Int("slice_raw_x")
    formula = ExactFormula(
        definitions=(FormulaDefinition("x", x, raw + 1),),
        protected_terms=(raw,),
    )

    sliced = formula.slice(x == 2)

    assert sliced.mode is SliceMode.FULL
    assert sliced.fallback.code is SliceFallbackCode.RAW_TERM
    assert sliced.constraints == formula.full_constraints


def test_unregistered_atom_forces_full_formula_fallback():
    missing = z3.Int("slice_unknown_missing")
    x = z3.Int("slice_unknown_x")
    formula = ExactFormula(
        definitions=(FormulaDefinition("x", x, missing + 1),),
    )

    sliced = formula.slice(x == 2)

    assert sliced.mode is SliceMode.FULL
    assert sliced.fallback.code is SliceFallbackCode.UNKNOWN_ATOM
    assert "missing" in sliced.fallback.detail
    assert formula.dependency_cone(x == 2) is None


def test_disconnected_hard_unsat_is_never_sliced_away():
    u, dead = z3.Ints("slice_hard_unsat_u slice_hard_unsat_dead")
    formula = ExactFormula(
        definitions=(FormulaDefinition("dead", dead, u + 1),),
        restrictions=(z3.BoolVal(False),),
        boundary_atoms=(u,),
    )

    sliced = formula.slice(u == 0)

    assert sliced.mode is SliceMode.SLICED
    assert sliced.kept_definitions == ()
    assert sliced.dropped_definitions == ("dead",)
    assert z3.is_false(sliced.constraints[0])
    assert _answer(sliced.query_assertions) == z3.unsat
    assert _answer(formula.full_constraints + (sliced.target,)) == z3.unsat


def test_an_identically_false_target_needs_no_transition_constraints():
    boundary = z3.Bool("slice_false_boundary")
    value = z3.Bool("slice_false_value")
    formula = ExactFormula(
        definitions=(FormulaDefinition("value", value, boundary),),
        restrictions=(boundary,),
        boundary_atoms=(boundary,),
    )

    sliced = formula.slice(z3.And(value, z3.Not(value)))

    assert sliced.mode is SliceMode.SLICED
    assert sliced.constraints == ()
    assert z3.is_false(sliced.target)
    assert sliced.kept_definitions == ()
    assert sliced.dropped_definitions == ("value",)
    assert _answer(sliced.query_assertions) == z3.unsat


def test_unknown_accessor_around_a_raw_carrier_reports_the_carrier_boundary():
    state = z3.DeclareSort("SliceRawState")
    raw = z3.Const("slice_raw_carrier", state)
    accessor = z3.Function("slice_unregistered_accessor", state, z3.IntSort())
    x = z3.Int("slice_raw_accessor_x")
    formula = ExactFormula(
        definitions=(FormulaDefinition("x", x, accessor(raw)),),
        protected_terms=(raw,),
    )

    sliced = formula.slice(x == 0)

    assert sliced.mode is SliceMode.FULL
    assert sliced.fallback.code is SliceFallbackCode.RAW_TERM


def test_a_registered_accessor_declaration_is_shared_not_guessed_per_use():
    state = z3.DeclareSort("SliceExternalState")
    raw = z3.Const("slice_external_raw", state)
    accessor = z3.Function(
        "slice_external_accessor", state, z3.IntSort())
    selected = z3.Int("slice_external_selected")
    paired = z3.Int("slice_external_paired")
    dead = z3.Int("slice_external_dead")
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("selected", selected, accessor(raw)),
            FormulaDefinition("paired", paired, accessor(raw)),
            FormulaDefinition("dead", dead, accessor(raw) + 1),
        ),
        boundary_atoms=(raw,),
        external_functions=(accessor,),
    )

    sliced = formula.slice(selected != paired)

    assert sliced.mode is SliceMode.SLICED
    assert sliced.kept_definitions == ("selected", "paired")
    assert sliced.dropped_definitions == ("dead",)
    assert _answer(sliced.query_assertions) == z3.unsat


def test_an_unregistered_accessor_declaration_still_forces_full_fallback():
    state = z3.DeclareSort("SliceMissingAccessorState")
    raw = z3.Const("slice_missing_accessor_raw", state)
    accessor = z3.Function(
        "slice_missing_accessor", state, z3.IntSort())
    selected = z3.Int("slice_missing_accessor_selected")
    formula = ExactFormula(
        definitions=(
            FormulaDefinition("selected", selected, accessor(raw)),
        ),
        boundary_atoms=(raw,),
    )

    sliced = formula.slice(selected == 3)

    assert sliced.mode is SliceMode.FULL
    assert sliced.fallback.code is SliceFallbackCode.UNKNOWN_ATOM


def test_records_are_closed_and_results_are_immutable():
    u, x = z3.Ints("slice_closed_u slice_closed_x")
    definition = FormulaDefinition("x", x, u + 1)
    formula = ExactFormula(definitions=(definition,), boundary_atoms=(u,))
    sliced = formula.slice(x == 2)

    with pytest.raises(TypeError, match="closed"):
        class _NotADefinition(FormulaDefinition):
            pass
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        sliced.constraints = ()


@pytest.mark.parametrize("kind", ["valid", "unknown-atom", "forward-read"])
def test_every_registration_checkpoint_resumes_the_same_complete_audit(kind):
    u, x, spare, foreign = z3.BitVecs("resume_u resume_x resume_spare resume_foreign", 8)
    rhs = u + 1 if kind == "valid" else foreign if kind == "unknown-atom" else spare
    definitions = (FormulaDefinition("x", x, rhs), FormulaDefinition("spare", spare, u ^ 7))
    restrictions = (u <= 10,)
    checkpoints = []
    whole = ExactFormula._from_generated_qfbv(
        definitions=definitions, restrictions=restrictions, boundary_atoms=(u,),
        _checkpoint=lambda: checkpoints.append(1))
    count = len(checkpoints)
    expected = whole.slice(x == 3)

    class Paused(Exception):
        pass

    for pause_at in range(1, count + 1):
        owner = _FormulaConstruction(definitions, restrictions, (u,))
        calls = [0]

        def checkpoint():
            calls[0] += 1
            if calls[0] == pause_at:
                raise Paused

        with pytest.raises(Paused):
            owner.advance(checkpoint)
        assert owner.result is None
        resumed = owner.advance(checkpoint)
        assert resumed is owner.result
        assert resumed.sliced_sat_extends == whole.sliced_sat_extends
        assert all(z3.eq(a, b) for a, b in zip(
            resumed.full_constraints, whole.full_constraints, strict=True))
        selected = resumed.slice(x == 3)
        assert selected.mode is expected.mode
        assert selected.kept_definitions == expected.kept_definitions
        assert selected.dropped_definitions == expected.dropped_definitions
        assert (selected.fallback.code if selected.fallback else None) == (
            expected.fallback.code if expected.fallback else None)
        assert all(z3.eq(a, b) for a, b in zip(
            selected.query_assertions, expected.query_assertions, strict=True))
        with pytest.raises(AttributeError, match="cannot be reassigned"):
            resumed.restrictions = ()


def test_repeated_registration_pauses_do_not_rewalk_completed_dag_nodes(monkeypatch):
    u = z3.BitVec("resume_dag_u", 16)
    shared = u
    for value in range(64):
        shared = (shared + value) ^ shared
    outputs = [z3.BitVec(f"resume_dag_x{i}", 16) for i in range(12)]
    definitions = tuple(FormulaDefinition(str(i), output, shared + i)
                        for i, output in enumerate(outputs))
    children, visited = z3.ExprRef.children, []

    def observe(term):
        visited.append(term.get_id())
        return children(term)

    monkeypatch.setattr(z3.ExprRef, "children", observe)
    whole = ExactFormula._from_generated_qfbv(definitions=definitions, boundary_atoms=(u,))
    original_visits = tuple(visited)
    visited.clear()
    owner = _FormulaConstruction(definitions, (), (u,))

    class Paused(Exception):
        pass

    def checkpoint():
        remaining[0] -= 1
        if remaining[0] == 0:
            raise Paused

    pauses = 0
    for _ in range(200):
        remaining = [20]
        try:
            result = owner.advance(checkpoint)
            break
        except Paused:
            pauses += 1
            assert owner.result is None
    else:
        pytest.fail("registration never advanced past a bounded slice")
    assert pauses > 1 and tuple(visited) == original_visits
    assert all(z3.eq(a, b) for a, b in zip(
        result.full_constraints, whole.full_constraints, strict=True))


def test_failed_registration_cannot_publish_its_closed_iterator_as_a_formula():
    u, a, b = z3.BitVecs("broken_u broken_a broken_b", 8)
    owner = _FormulaConstruction((FormulaDefinition("duplicate", a, u + 1),
                                  FormulaDefinition("duplicate", b, u + 2)), (), (u,))
    for _ in range(2):
        with pytest.raises(ValueError, match="labels are distinct"):
            owner.advance(lambda: None)
        assert owner.result is None


def test_growing_product_audits_only_new_dag_nodes_and_keeps_new_restrictions(monkeypatch):
    u, v, x, y, spare = z3.BitVecs('extend_u extend_v extend_x extend_y extend_spare', 8)
    shared = u
    for i in range(64):
        shared = (shared + i) ^ (shared << 1)
    old = (FormulaDefinition('x', x, shared),)
    restriction = u <= 3
    prefix = ExactFormula._from_generated_qfbv(
        definitions=old, restrictions=(restriction,), boundary_atoms=(u,))
    added = (FormulaDefinition('y', y, x + v), FormulaDefinition('spare', spare, y ^ 7))
    # Framing restrictions can be interspersed with newly appended step bounds.
    restrictions = (v <= 3, restriction, spare == 5)
    definitions = old + added
    children, visited = z3.ExprRef.children, []

    def observe(term):
        visited.append(term.get_id())
        return children(term)

    monkeypatch.setattr(z3.ExprRef, 'children', observe)
    growing = _FormulaConstruction(definitions, restrictions, (u, v), prefix=prefix).advance(lambda: None)
    reused_visits = tuple(visited)
    assert shared.get_id() not in reused_visits
    visited.clear()
    cold = ExactFormula._from_generated_qfbv(
        definitions=definitions, restrictions=restrictions, boundary_atoms=(u, v))
    assert shared.get_id() in visited and len(reused_visits) < len(visited)
    assert growing.sliced_sat_extends
    for target in (u == 1, y == 2, spare == 5, z3.BoolVal(True)):
        a, b = growing.slice(target), cold.slice(target)
        assert a.mode == b.mode and a.kept_definitions == b.kept_definitions
        assert all(z3.eq(l, r) for l, r in zip(a.query_assertions, b.query_assertions, strict=True))
        # The new restriction must keep its definition chain even for u-only goals.
        assert 'spare' in a.kept_definitions
    for first, second in itertools.product(range(4), repeat=2):
        fixed = (u == first, v == second)
        assert _answer((*growing.slice(y == 2).query_assertions, *fixed)) == _answer(
            (*cold.full_constraints, y == 2, *fixed))
    # An immutable earlier view neither gains the new input nor its restrictions.
    assert not prefix.contains_boundary_atom(v)
    assert prefix.restrictions == (restriction,)
    assert prefix.slice(v == 1).fallback.code is SliceFallbackCode.UNKNOWN_ATOM


@pytest.mark.parametrize('change', ['remove-restriction', 'replace-restriction',
    'remove-boundary', 'replace-definition', 'define-old-boundary', 'old-definition-as-boundary',
    'unknown-restriction', 'unknown-definition', 'forward-definition', 'duplicate-definition'])
def test_growing_product_never_reuses_incompatible_roles_or_old_answers(change):
    u, v, x, y, alien = z3.BitVecs('grow_u grow_v grow_x grow_y grow_alien', 8)
    old = FormulaDefinition('x', x, u + 1)
    restriction = u == 0
    prefix = ExactFormula._from_generated_qfbv(
        definitions=(old,), restrictions=(restriction,), boundary_atoms=(u,))
    definitions = (old, FormulaDefinition('y', y, x + v))
    restrictions, boundary = (restriction, v <= 3), (u, v)
    if change == 'remove-restriction':
        restrictions = (v <= 3,)
    elif change == 'replace-restriction':
        restrictions = (u == 2, v <= 3)
    elif change == 'remove-boundary':
        boundary = (v,)
    elif change == 'replace-definition':
        definitions = (FormulaDefinition('x', x, u + 2), definitions[1])
    elif change == 'define-old-boundary':
        definitions += (FormulaDefinition('input', u, v),)
    elif change == 'old-definition-as-boundary':
        boundary += (x,)
    elif change == 'unknown-restriction':
        restrictions += (alien == 0,)
    elif change == 'unknown-definition':
        definitions = (old, FormulaDefinition('y', y, alien))
    elif change == 'forward-definition':
        definitions += (FormulaDefinition('early', alien, z3.BitVec('future', 8)),
                        FormulaDefinition('late', z3.BitVec('future', 8), v))
    else:
        definitions += (FormulaDefinition('again', x, v),)
    growing = _FormulaConstruction(definitions, restrictions, boundary, prefix=prefix).advance(lambda: None)
    cold = ExactFormula._from_generated_qfbv(
        definitions=definitions, restrictions=restrictions, boundary_atoms=boundary)
    assert growing.sliced_sat_extends == cold.sliced_sat_extends
    for target in (x == 3, y == 2):
        a, b = growing.slice(target), cold.slice(target)
        assert a.mode == b.mode and a.kept_definitions == b.kept_definitions
        assert (a.fallback.code if a.fallback else None) == (b.fallback.code if b.fallback else None)
        assert all(z3.eq(l, r) for l, r in zip(a.query_assertions, b.query_assertions, strict=True))
        assert _answer(a.query_assertions) == _answer(b.query_assertions)


def test_growing_product_registration_can_pause_at_every_boundary_without_mutating_prefix():
    u, v, x, y = z3.BitVecs('pause_grow_u pause_grow_v pause_grow_x pause_grow_y', 8)
    old = FormulaDefinition('x', x, u + 1)
    prefix = ExactFormula._from_generated_qfbv(definitions=(old,), boundary_atoms=(u,))
    definitions = (old, FormulaDefinition('y', y, x + v))
    restrictions = (y <= 3,)
    ticks = []
    expected = _FormulaConstruction(definitions, restrictions, (u, v), prefix=prefix).advance(
        lambda: ticks.append(1))

    class Paused(Exception):
        pass

    for point in range(1, len(ticks) + 1):
        owner = _FormulaConstruction(definitions, restrictions, (u, v), prefix=prefix)
        calls = [0]

        def checkpoint():
            calls[0] += 1
            if calls[0] == point:
                raise Paused

        with pytest.raises(Paused):
            owner.advance(checkpoint)
        result = owner.advance(lambda: None)
        assert result.sliced_sat_extends
        assert all(z3.eq(a, b) for a, b in zip(result.slice(y == 2).query_assertions,
                    expected.slice(y == 2).query_assertions, strict=True))
        assert prefix.definitions == (old,) and prefix.restrictions == ()
        assert not prefix.contains_boundary_atom(v)
