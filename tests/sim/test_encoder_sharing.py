"""Exact SSA sharing retains command results, occurrences and step identity."""

import gc
import itertools
import weakref

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic.exact_formula import FormulaDefinition
from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types


def _wide_boundary_step(proc, env, *, width=212, **kwargs):
    from behave.sim.symbolic.plan import FiniteCarrier, OccurrenceCarriers, plan_encoding

    planned = plan_encoding(proc, env)
    carriers = OccurrenceCarriers({
        key: FiniteCarrier(carrier.lo, carrier.hi, width)
        for key, carrier in planned.occurrence_carriers.items()})
    return encoder._encode_step(proc, env=env, plan=planned,
        occurrence_carriers=carriers, scalar_width=width, **kwargs)


@pytest.mark.parametrize('low,high', [(0, 5), (-5, 2), (3, 3),
                                    (-2147483647, 520200)])
@pytest.mark.parametrize('nested', [False, True])
def test_fresh_boundary_carriers_preserve_full_restricted_domain(monkeypatch, low, high, nested):
    kind = types.Array(2, types.Array(2)) if nested else types.Array(2)
    proc = ir.Procedure(('m',), ('a',), (), ('y',),
        ir.Assign('y', ir.Var('a')), types={'m': kind},
        initial={'m': [[0, 0], [0, 0]] if nested else [0, 0]})
    env = {'m': ranges.Range(low, high), 'a': ranges.Range(-2, 5)}
    original = encoder._symbolic_value

    def wide(*args, **kwargs):
        kwargs.pop('leaf_width', None)
        return original(*args, **kwargs)

    with monkeypatch.context() as control:
        control.setattr(encoder, '_symbolic_value', wide)
        old = _wide_boundary_step(proc, env, tag='old_boundary')
    new = _wide_boundary_step(proc, env, tag='new_boundary')
    substitutions, equal_values = [], []
    for name in proc.state + proc.inputs:
        before = tuple(encoder._flatten_tree(old.pre[name]))
        after = tuple(encoder._flatten_tree(new.pre[name]))
        bits = ranges.encoding_width(env[name])
        for left, right in zip(before, after, strict=True):
            assert left.size() == right.size() == 212
            assert z3.is_app_of(right, z3.Z3_OP_SIGN_EXT)
            atom = right.arg(0)
            assert atom.size() == bits
            substitutions.append((atom, z3.Extract(bits - 1, 0, left)))
            equal_values.append(left == right)
    # Every old legal assignment has a new representation. This checks more
    # than equivalence under coupled models, which could hide lost inputs.
    restored = z3.substitute(z3.And(*new.restrictions, *equal_values), *substitutions)
    solver = z3.Solver()
    solver.add(*old.restrictions, z3.Not(restored))
    assert solver.check() == z3.unsat
    # Conversely, the new interval is not rounded to its physical carrier.
    for name in proc.state + proc.inputs:
        for leaf in encoder._flatten_tree(new.pre[name]):
            solver = z3.Solver()
            solver.add(*new.restrictions,
                       z3.Or(leaf < env[name].lo, leaf > env[name].hi))
            assert solver.check() == z3.unsat
    solver = z3.Solver()
    solver.add(old.constraint, new.constraint, *equal_values)
    assert solver.check() == z3.sat
    solver.add(z3.Or(old.ok != new.ok, old.error != new.error,
                     old.post['y'] != new.post['y']))
    assert solver.check() == z3.unsat
    formula = new.exact_formula()
    assert {term.get_id() for term in formula.boundary_atoms} == {
        atom.get_id() for atom, _ in substitutions}
    assert not formula.sliced_sat_extends  # Generic callers retain the full audit.
    generated = new._query_formula(checkpoint=None)
    assert generated.sliced_sat_extends
    for registered in (formula, generated):
        assert registered.slice(new.post['y'] == 3).fallback is None


@pytest.mark.parametrize('low,high', [(0, 5), (-5, 2), (-2147483647, 520200)])
def test_restricted_array_copies_keep_narrow_physical_carriers(low, high):
    from behave.sim.symbolic.functional_array import FunctionalArray
    from behave.sim.symbolic.uniform_array import UniformArray

    proc = ir.Procedure(('m',), (), (), (), ir.Skip(),
        types={'m': types.Array(4)}, initial={'m': UniformArray(4, 0)})
    result = _wide_boundary_step(proc, {'m': ranges.Range(low, high)})
    # Non-power-of-two intervals still need exact component restrictions; the
    # fallback must not restore horizon-wide free atoms inside each cell.
    assert isinstance(result.pre['m'], list)
    before = FunctionalArray.from_cells(result.pre['m'])
    after = before
    enc = encoder._Encoder(None, {}, 'bounded_copies')
    enc.compact_arrays = True
    index = z3.BitVec('bounded_copy_address', 4)
    for turn in range(7):
        value = result.pre['m'][turn % 4]
        guard = z3.Bool(f'bounded_copy_guard{turn}')
        before = before.store(index + turn, value, guard)
        after = enc.bind_value(after.store(index + turn, value, guard))
    assert after.element_width == 212
    assert after._base.storage_width <= ranges.encoding_width(ranges.Range(low, high))
    # Arbitrary signed addresses include invalid reads/writes. No interval
    # assumption is needed to prove that physical packing preserves values.
    read = z3.BitVec('bounded_copy_read', 4)
    solver = z3.SolverFor('QF_BV')
    solver.add(*enc.defs)
    assert solver.check() == z3.sat
    solver.add(before.select(read) != after.select(read))
    assert solver.check() == z3.unsat


def test_fresh_width_optimization_does_not_narrow_supplied_state():
    proc = ir.Procedure(('s',), (), (), ('y',), ir.Assign('y', ir.Var('s')),
                        initial={'s': 0})
    state = z3.BitVec('supplied_full_state', 24)
    result = _wide_boundary_step(proc, {'s': ranges.Range(0, 2)},
        pre={'s': state}, width=24)
    assert result.pre['s'].eq(state)
    solver = z3.Solver()
    solver.add(result.constraint, state == 7, result.post['y'] == 7, result.ok)
    assert solver.check() == z3.sat


def test_identical_terms_share_only_within_one_encoding():
    enc = encoder._Encoder(None, {}, "shared")
    a, b = z3.BitVecs("sharing_a sharing_b", 8)
    first = enc.bind(a + b)
    assert z3.eq(enc.bind(a + b), first)
    assert not z3.eq(enc.bind(a - b), first)
    predicate = enc.bind(a == b)
    assert z3.eq(enc.bind(a == b), predicate)
    assert len(enc.definitions) == 3
    assert enc.definitions[0].rhs.eq(a + b)
    other = encoder._Encoder(None, {}, "other")
    assert not z3.eq(other.bind(a + b), first)
    assert len(other.definitions) == 1
    for width in (4, 16):
        term = z3.BitVec("sharing_a", width) + 1
        assert enc.bind(term).size() == width
    assert len(enc.definitions) == 5
    assert z3.is_bv_value(enc.bind(a - a))
    assert enc.bind(a - a).as_long() == 0
    assert z3.eq(enc.bind(z3.If(a == b, a, a)), a)
    assert len(enc.definitions) == 5


def _seq(*commands):
    body = ir.Skip()
    for command in reversed(commands):
        body = ir.Seq(command, body)
    return body


def _procedure(shared_syntax=False):
    shared = ir.Op("add", ir.Var("s"), ir.Var("a"))

    def common():
        # Both shared IR nodes and independently constructed expressions cease
        # to mean the same value when a state write changes their operand.
        return shared if shared_syntax else ir.Op("add", ir.Var("s"), ir.Var("a"))

    return ir.Procedure(("s",), ("a",), ("x", "z", "i"), ("y",), _seq(
        ir.Assign("x", common()), ir.Assign("z", common()),
        ir.If(ir.Op("eq", common(), ir.Lit(1)), ir.Mark("same"), ir.Skip()),
        ir.For("i", ir.Lit(2), _seq(
            ir.If(ir.Op("eq", common(), ir.Lit(1)), ir.Mark("same"), ir.Skip()),
            ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(2)),
                  ir.Fail("DivZero", "failure"), ir.Skip()),
            ir.Assign("s", ir.Op("sub", ir.Lit(1), ir.Var("s"))),
            ir.Assign("z", common()))),
        ir.Assign("y", ir.Op("add", ir.Var("x"), ir.Var("z")))),
        initial={"s": 0},
        types={name: types.INTEGER for name in ("s", "a", "x", "z", "i", "y")})


@pytest.mark.parametrize('shared_syntax', [False, True])
def test_full_results_and_each_occurrence_match_unshared_encoding_and_replay(monkeypatch, shared_syntax):
    proc = _procedure(shared_syntax)
    env = {"s": ranges.Range(0, 1), "a": ranges.Range(0, 2)}
    pre = {name: z3.BitVec(f"sharing_pre_{name}", ranges.encoding_width(env[name]))
           for name in ("s", "a")}
    shared = encoder.encode_step(proc, pre=pre, env=env, tag="cse")

    class WithoutConstructionSharing(dict):
        def get(self, key, default=None):
            return default

    original_init = encoder._Encoder.__init__

    def unshared_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._scalar_terms = WithoutConstructionSharing()

    def without_sharing(self, term):
        # Deliberately unnormalized equations: the comparison checks both
        # rewriting and sharing, not two variants of the new normalizer.
        if z3.is_const(term):
            return term
        self._names += 1
        label = encoder._internal_symbol("legacy", self.tag, self._names)
        name = z3.Bool(label) if z3.is_bool(term) else z3.BitVec(label, term.size())
        definition = FormulaDefinition(label, name, term)
        self.definitions.append(definition)
        self.defs.append(definition.assertion)
        # Disable term normalization/sharing, not the ownership metadata used
        # by control encoding. Both variants must know their emitted equations.
        self._guarded_reads.define(name, term)
        return name

    with monkeypatch.context() as legacy:
        legacy.setattr(encoder._Encoder, "__init__", unshared_init)
        legacy.setattr(encoder._Encoder, "bind", without_sharing)
        legacy.setattr(encoder._Encoder, '_op', encoder._Encoder._encode_operation)
        plain = encoder.encode_step(proc, pre=pre, env=env, tag="plain")
    assert len(shared.definitions) < len(plain.definitions)
    solver = z3.SolverFor("QF_BV")
    solver.set(timeout=10_000)
    solver.add(shared.constraint, plain.constraint, pre["s"] >= 0, pre["s"] <= 1)
    differences = [shared.ok != plain.ok, shared.error != plain.error]
    for name in shared.final:
        left, right = encoder.widen(shared.final[name], plain.final[name])
        differences.append(left != right)
    assert shared.hit_occurrences.keys() == plain.hit_occurrences.keys()
    for goal in shared.hits:
        differences.append(shared.hits[goal] != plain.hits[goal])
        differences.extend(a != b for a, b in zip(
            shared.hit_occurrences[goal], plain.hit_occurrences[goal], strict=True))
    solver.push()
    solver.add(z3.Or(differences))
    assert solver.check() == z3.unsat
    solver.pop()
    for state, value in itertools.product(range(2), range(3)):
        values = {"s": state, "a": value}
        native = interp._Interp(values)
        hits, failed = set(), False
        try:
            native.run(proc.body, hits)
        except interp.Halt:
            failed = True
        solver.push()
        solver.add(pre["s"] == state, pre["a"] == value)
        assert solver.check() == z3.sat
        model = solver.model()
        assert z3.is_true(model.eval(shared.ok)) == (not failed)
        assert {goal for goal, hit in shared.hits.items()
                if z3.is_true(model.eval(hit))} == hits
        for name, expected in values.items():
            assert model.eval(shared.final[name]).as_signed_long() == expected
        solver.pop()


def test_repetition_does_not_grow_the_definition_set():
    enc = encoder._Encoder(None, {}, "many")
    a = z3.BitVec("sharing_many", 8)
    first = enc.bind(a + 1)
    for _ in range(1024):
        assert z3.eq(enc.bind(a + 1), first)
    assert len(enc.defs) == len(enc.definitions) == len(enc._bound_terms) == 1


def test_dead_suffix_elision_preserves_each_possible_visit(monkeypatch):
    from behave.sim.symbolic.guarded_reads import GuardedReads

    proc = _procedure()
    env = {"s": ranges.Range(0, 1), "a": ranges.Range(0, 2)}
    with monkeypatch.context() as original:
        original.setattr(GuardedReads, "impossible", lambda self, guard: False)
        plain = encoder.encode_step(proc, env=env, tag="unpruned")
    reduced = encoder.encode_step(proc, pre=plain.pre, env=env, tag="pruned")
    solver = z3.SolverFor("QF_BV")
    solver.add(plain.constraint, reduced.constraint)
    assert solver.check() == z3.sat
    differences = [plain.ok != reduced.ok, plain.error != reduced.error]
    for name in plain.final:
        left, right = encoder.widen(plain.final[name], reduced.final[name])
        differences.append(left != right)
    assert plain.hits.keys() == reduced.hits.keys()

    def possible_visits(terms):
        retained = []
        for term in terms:
            solver.push()
            solver.add(term)
            answer = solver.check()
            solver.pop()
            assert answer in (z3.sat, z3.unsat)
            if answer == z3.sat:
                retained.append(term)
        return retained

    assert sum(map(len, reduced.hit_occurrences.values())) < sum(
        map(len, plain.hit_occurrences.values()))
    for goal in plain.hits:
        differences.append(plain.hits[goal] != reduced.hits[goal])
        # Omitted entries must be UNSAT under the complete equations, not
        # just absent in tested inputs. Every possible visit stays in order.
        differences.extend(a != b for a, b in zip(
            possible_visits(plain.hit_occurrences[goal]),
            possible_visits(reduced.hit_occurrences[goal]), strict=True))
    solver.add(z3.Or(differences))
    assert solver.check() == z3.unsat


def test_non_atomic_rhs_keeps_the_structural_proof_representation():
    enc = encoder._Encoder(None, {}, "shape")
    a, b = z3.BitVecs("shape_a shape_b", 8)
    original = z3.Extract(7, 0, z3.ZeroExt(8, a) + z3.ZeroExt(8, b))
    rewritten = z3.simplify(original)
    assert not z3.is_const(rewritten) and not rewritten.eq(original)
    name = enc.bind(original)
    assert enc.definitions[0].rhs.eq(original)
    assert enc.bind(original).eq(name)
    solver = z3.SolverFor("QF_BV")
    solver.set(timeout=10_000)
    solver.add(*enc.defs, name != rewritten)
    assert solver.check() == z3.unsat


def test_scalar_primitive_construction_shares_only_identical_typed_operands(monkeypatch):
    enc = encoder._Encoder(None, {"x": ranges.Range(-2, 1)})
    original = encoder._Encoder.fixed_wrap
    calls = []

    def wrapped(self, value, width, result_width, *, signed):
        calls.append((value, width, result_width, signed))
        return original(self, value, width, result_width, signed=signed)

    monkeypatch.setattr(encoder._Encoder, 'fixed_wrap', wrapped)
    a, b = z3.BitVecs('primitive_a primitive_b', 4)
    expression = ir.Op('uwrap', ir.Var('x'), ir.Lit(4))
    first = enc.expr(expression, {'x': a})
    assert enc.expr(ir.Op('uwrap', ir.Var('x'), ir.Lit(4)), {'x': a}) is first
    assert len(calls) == 1
    enc.expr(expression, {'x': b})
    enc.expr(ir.Op('swrap', ir.Var('x'), ir.Lit(4)), {'x': a})
    enc.expr(ir.Op('uwrap', ir.Var('x'), ir.Lit(3)), {'x': a})
    enc.scalar_width = 16
    wider = enc.expr(expression, {'x': a})
    assert len(calls) == 5 and wider.size() == 16
    enc.scalar_width = None
    assert enc.expr(expression, {'x': a}) is first
    # Invalid refined arguments still fail before any cached callback result.
    with pytest.raises(encoder.EncodingError):
        enc.expr(ir.Op('uwrap', ir.Var('x'), ir.Var('x')), {'x': a})
    other = encoder._Encoder(None, {'x': ranges.Range(-2, 1)})
    other.expr(expression, {'x': a})
    assert len(calls) == 6


def test_scalar_construction_keeps_optional_rewrite_history_current(monkeypatch):
    from behave.sim.symbolic.term_rewrite import _ComparisonRewriter

    enc = encoder._Encoder(None, {'x': ranges.Range(-2, 1)})
    a = z3.BitVec('primitive_rewrite_x', 4)
    expression = ir.Op('lt', ir.Var('x'), ir.Lit(0))
    counts = []
    monkeypatch.setattr(_ComparisonRewriter, 'rewrite',
                        lambda self, value: counts.append(len(self._definitions)) or value)
    first = enc.expr(expression, {'x': a})
    enc.bind(a + 1)
    assert enc.expr(expression, {'x': a}).eq(first)
    assert counts == [0, 1]


def test_scalar_lookup_does_not_construct_equality_formulas(monkeypatch):
    enc = encoder._Encoder(None, {'x': ranges.Range(-2, 1)})
    expression = ir.Op('uwrap', ir.Var('x'), ir.Lit(4))
    first = enc.expr(expression, {'x': z3.BitVec('primitive_lookup', 4)})
    calls = []
    original = z3.BitVecRef.__eq__

    def compared(left, right):
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(z3.BitVecRef, '__eq__', compared)
    for _ in range(20):
        # New Python wrappers for the identical native AST still share.
        assert enc.expr(expression, {'x': z3.BitVec('primitive_lookup', 4)}) is first
    assert calls == []


def test_scalar_operand_identity_is_context_qualified_and_retained():
    enc = encoder._Encoder(None, {'x': ranges.Range(-2, 1)})
    expression = ir.Op('uwrap', ir.Var('x'), ir.Lit(4))
    contexts = (z3.Context(), z3.Context())
    operands = [z3.BitVec('primitive_context', 4, ctx=ctx) for ctx in contexts]
    # Independent contexts can assign the same native ID to different ASTs.
    assert operands[0].get_id() == operands[1].get_id()
    references = [weakref.ref(value) for value in operands]
    results = [enc.expr(expression, {'x': value}) for value in operands]
    assert all(result.ctx is ctx for result, ctx in zip(results, contexts))
    del operands
    gc.collect()
    assert all(reference() is not None for reference in references)
    del enc
    gc.collect()
    assert all(reference() is None for reference in references)


def test_expression_ranges_share_the_computed_tree_only_until_a_write(monkeypatch):
    value = ir.Var("s")
    common = ir.Op("add", value, ir.Lit(1))
    root = ir.Op("mul", common, ir.Lit(3))
    enc = encoder._Commands(None, {"s": ranges.Range(0, 2)})
    calls = []
    original = ranges._ranges_of_tree

    def walk(expr, *args):
        calls.append(expr)
        return original(expr, *args)

    monkeypatch.setattr(ranges, "_ranges_of_tree", walk)
    assert enc.rng(root) == ranges.Range(3, 9)
    assert enc.rng(common) == ranges.Range(1, 3)
    assert enc.rng(value) == ranges.Range(0, 2)
    assert len(calls) == 1
    enc._rebind("s", ranges.Range(10, 11), z3.BoolVal(True))
    assert enc.rng(root) == ranges.Range(33, 36)
    assert enc.rng(common) == ranges.Range(11, 12)
    assert len(calls) == 2


def test_position_carriers_reuse_the_plan_not_the_mutable_range_environment(monkeypatch):
    from types import SimpleNamespace

    from behave.sim.symbolic.plan import ExpressionPosition, _expression_roots

    value = ir.Var("s")
    command = ir.Assign("x", value)
    enc = encoder._Commands(None, {"s": ranges.Range(-50, 50)})
    enc.command_numbers = {id(command): 3}
    # Use the IR plan's actual role; assignments and stores need not share it.
    role = _expression_roots(command)[0][0]
    enc.occurrence_carriers = {
        ExpressionPosition(3, role): SimpleNamespace(range=ranges.Range(2, 4))}
    original, calls = enc._prime_expression, []

    def prime(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(enc, "_prime_expression", prime)
    enc._prime_command(command)
    assert enc.rng(value) == ranges.Range(2, 4)
    enc._range[id(value)] = ranges.Range(-100, 100)
    enc.env["s"] = ranges.Range(70, 80)
    enc._prime_command(command)
    assert enc.rng(value) == ranges.Range(2, 4)
    assert len(calls) == 1


def test_shared_expression_dag_encodes_once_per_node_not_per_tree_path(monkeypatch):
    expression = ir.Var('x')
    depth = 12
    for _ in range(depth):
        expression = ir.Op('swrap', ir.Op('add', expression, expression), ir.Lit(17))
    enc = encoder._Encoder(None, {'x': ranges.Range(-128, 127)})
    x = z3.BitVec('dag_input', 8)
    original = encoder._Encoder._encode_operation
    calls = []

    def operation(self, expr, rho):
        calls.append(expr)
        return original(self, expr, rho)

    monkeypatch.setattr(encoder._Encoder, '_encode_operation', operation)
    value = enc.expr(expression, {'x': x})
    assert len(calls) == 2 * depth
    assert enc._expression_terms is None
    expected = z3.SignExt(9, x)
    for _ in range(depth):
        expected = expected + expected
    left, right = encoder.widen(value, expected)
    solver = z3.SolverFor('QF_BV')
    solver.add(left != right)
    assert solver.check() == z3.unsat


def test_expression_reuse_ends_before_changed_binding_guard_carrier_or_syntax():
    common = ir.Op('add', ir.Var('x'), ir.Lit(1))
    expression = ir.Op('add', common, common)
    enc = encoder._Encoder(None, {'x': ranges.Range(-4, 3)})
    x = z3.BitVec('dag_scope_x', 4)
    guard = z3.Bool('dag_scope_guard')
    conditional = enc.bind(z3.If(guard, x, z3.BitVecVal(2, 4)))

    enc._read_guard = guard
    first = enc.expr(expression, {'x': conditional})
    enc._read_guard = z3.Not(guard)
    second = enc.expr(expression, {'x': conditional})
    enc._read_guard = None
    third = enc.expr(expression, {'x': z3.BitVecVal(3, 4)})
    common.args = (ir.Var('x'), ir.Lit(2))
    changed = enc.expr(expression, {'x': z3.BitVecVal(3, 4)})
    enc.scalar_width = 16
    wider = enc.expr(expression, {'x': z3.BitVecVal(3, 4)})
    assert wider.size() == 16
    assert enc._expression_terms is None
    lhs, rhs = encoder.widen(first, 2 * z3.SignExt(4, x) + 2)
    solver = z3.SolverFor('QF_BV')
    solver.add(*enc.defs, x >= -4, x <= 3,
               z3.Or(lhs != rhs, second != 6, third != 8,
                               changed != 10, wider != 10))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('error', [encoder._EncodingPaused, RuntimeError])
def test_interrupted_expression_discards_its_local_reuse_before_retry(monkeypatch, error):
    common = ir.Op('add', ir.Var('x'), ir.Lit(1))
    expression = ir.Op('add', common, common)
    enc = encoder._Encoder(None, {'x': ranges.Range(0, 3)})
    x = z3.BitVecVal(2, 4)
    original = enc._encode_operation
    calls = 0

    def operation(expr, rho):
        nonlocal calls
        calls += 1
        value = original(expr, rho)
        if expr is expression:
            raise error('interrupt after constructing the shared subtree')
        return value

    with monkeypatch.context() as scoped:
        scoped.setattr(enc, '_encode_operation', operation)
        with pytest.raises(error):
            enc.expr(expression, {'x': x})
    assert calls == 2 and enc._expression_terms is None
    value = enc.expr(expression, {'x': z3.BitVecVal(3, 4)})
    assert z3.simplify(value).as_signed_long() == 8


def test_expression_fault_collection_keeps_repeated_occurrences():
    bad = ir.Op('ushl', ir.Var('x'), ir.Lit(-1), ir.Lit(8))
    enc = encoder._Encoder(None, {'x': ranges.Range(0, 3)})
    enc.collect = True
    enc.expr(ir.Op('add', bad, bad), {'x': z3.BitVecVal(2, 4)})
    assert [fault.kind for fault in enc.faults] == ['type', 'type', 'range']
    assert enc.faults[0].message == enc.faults[1].message
    assert enc._expression_terms is None


def test_repeated_array_subexpression_retains_its_exact_selection_evidence(monkeypatch):
    from behave.sim.symbolic.closure_proof import _SyntacticClosureProof
    from behave.sim.symbolic.functional_array import FunctionalArray

    array = FunctionalArray.symbolic(3, 'dag_array', 4, -8, 7)
    index = z3.BitVec('dag_array_index', 4)
    common = ir.Op('add', ir.Sel(ir.Var('array'), ir.Var('index')), ir.Lit(1))
    expression = ir.Op('add', common, common)
    env = {'array': ranges.Range(-8, 7), 'index': ranges.Range(-8, 7)}
    rho = {'array': array, 'index': index}
    enc = encoder._Encoder(None, env)
    result = enc.expr(expression, rho)
    assert len(enc.array_selections) == 1
    with monkeypatch.context() as old:
        old.setattr(encoder._Encoder, '_op', encoder._Encoder._encode_operation)
        plain = encoder._Encoder(None, env)
        expected = plain.expr(expression, rho)
    assert len(plain.array_selections) == 2
    proof = _SyntacticClosureProof({}, array_selections=enc.array_selections)
    selected = enc.array_selections[0].term
    bounds = proof.coarse_signed_hull(selected)
    solver = z3.SolverFor('QF_BV')
    left, right = encoder.widen(result, expected)
    solver.add(*enc.defs, *plain.defs,
               z3.Or(left != right, selected < bounds.lo, selected > bounds.hi))
    assert solver.check() == z3.unsat
