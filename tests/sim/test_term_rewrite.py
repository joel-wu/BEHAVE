"""Comparison reduction is an equality proof, not an input-domain restriction."""

import itertools
import operator

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import encoder, interp, ir
from behave.sim.symbolic.term_rewrite import _ComparisonRewriter


def test_array_hull_is_reused_for_the_same_immutable_state(monkeypatch):
    from behave.sim.symbolic.functional_array import FunctionalArray

    array = FunctionalArray.uniform(32, z3.BitVec('memo_source', 64) & 15)
    proof = _ComparisonRewriter([])
    original, calls = FunctionalArray.range_cases, []

    def counted(value, *, guarded=True):
        assert not guarded
        calls.append(value)
        return original(value, guarded=guarded)

    monkeypatch.setattr(FunctionalArray, 'range_cases', counted)
    for _ in range(20):
        assert proof.array_bounds(array) == (0, 15)
    assert calls == [array]
    changed = array.store(z3.BitVecVal(0, 8), z3.BitVecVal(-3, 64), z3.BoolVal(True))
    assert proof.array_bounds(changed) == (-3, 15)
    assert len(calls) == 2


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('kind', ['unsigned', 'signed', 'unknown-arm', 'exhausted'])
def test_array_state_binding_preserves_proven_storage_and_all_values(private, kind):
    from behave.sim.symbolic.functional_array import FunctionalArray

    context = z3.Context() if private else z3.main_ctx()
    enc = encoder._Encoder(None, {}, 'array_storage')
    enc.compact_arrays = True
    wide = 32
    x = z3.BitVec('array_source', wide, ctx=context)
    index = z3.BitVec('array_index', 5, ctx=context)
    guard = z3.Bool('array_guard', ctx=context)
    value = (z3.SignExt(wide - 4, z3.Extract(3, 0, x)) if kind == 'signed'
             else x & 7)
    if kind == 'unknown-arm':
        value = z3.If(guard, value, x)
    if kind == 'exhausted':
        enc._comparison_rewriter = _ComparisonRewriter(enc.definitions, work_limit=0)
    value = enc.bind(value)
    original = FunctionalArray.uniform(4, z3.BitVecVal(0, wide, ctx=context))
    for turn in range(9):
        original = original.store(index + turn, value, guard)
    actual = enc.bind_value(original)
    expected_bits = 3 if kind == 'unsigned' else 4 if kind == 'signed' else wide
    assert actual._base.storage_width == expected_bits
    assert actual.element_width == wide
    assert actual._base.value.size() == 4 * expected_bits
    # Compare all entry assignments and in-/out-of-bounds reads. The defining
    # equations, including the narrowed packed image, remain authoritative.
    read = z3.BitVec('array_read', 5, ctx=context)
    solver = z3.Solver(ctx=context)
    solver.add(*enc.defs)
    assert solver.check() == z3.sat
    solver.add(actual.select(read) != original.select(read))
    assert solver.check() == z3.unsat


def test_shared_array_keeps_masked_carrier_without_a_membership_solver():
    from behave.sim.symbolic import ranges
    from behave.sim.symbolic.functional_array import FunctionalArray
    from behave.sim.symbolic.plan import FiniteCarrier
    from behave.sim.symbolic.closure_proof import _carrier_leaf_memberships

    enc = encoder._Encoder(None, {}, 'array_mask_carrier')
    enc.compact_arrays = True
    index, word = z3.BitVec('array_mask_index', 8), z3.BitVec('array_mask_word', 212)
    value = enc.bind(word & 15)
    array = FunctionalArray.uniform(16, z3.BitVecVal(0, 212))
    for turn in range(33):
        array = enc.bind_value(array.store(index + turn, value, z3.BoolVal(True)))
    definitions = {d.lhs.get_id(): d.rhs for d in enc.definitions}
    checks = _carrier_leaf_memberships(array, FiniteCarrier.of(ranges.Range(0, 15)),
                                      definitions, _coarse_only=True)
    assert checks and all(z3.is_true(member) for _, member in checks)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('selection_evidence', [False, True])
@pytest.mark.parametrize('low,high', [(0, 5), (-5, 2), (3, 5), (0, 0)])
def test_shared_array_keeps_exact_source_hull_through_indexed_copies(private, selection_evidence, low, high):
    from behave.sim.symbolic import ranges
    from behave.sim.symbolic.closure_proof import _SyntacticClosureProof
    from behave.sim.symbolic.functional_array import FunctionalArray
    from behave.sim.symbolic.plan import FiniteCarrier
    from behave.sim.symbolic.closure_proof import _carrier_leaf_memberships

    context = z3.Context() if private else z3.main_ctx()
    enc = encoder._Encoder(None, {}, 'array_copy_hull')
    enc.compact_arrays = True
    width = 32
    index = z3.BitVec('copy_index', 5, ctx=context)
    guard = z3.Bool('copy_guard', ctx=context)
    choice = z3.Bool('copy_value', ctx=context)
    value = enc.bind(z3.If(choice, z3.BitVecVal(low, width, ctx=context),
                          z3.BitVecVal(high, width, ctx=context)))
    original = FunctionalArray.uniform(4, z3.BitVecVal(low, width, ctx=context))
    actual = original
    for turn in range(9):
        original = original.store(index + turn, value, guard)
        actual = enc.bind_value(actual.store(index + turn, value, guard))

    def members(array, lo, hi):
        definitions = {d.lhs.get_id(): d.rhs for d in enc.definitions}
        proof = _SyntacticClosureProof(definitions,
            array_selections=enc.array_selections if selection_evidence else ())
        return _carrier_leaf_memberships(
            array, FiniteCarrier.of(ranges.Range(lo, hi)), proof=proof, _coarse_only=True)

    assert all(z3.is_true(check) for _, check in members(actual, low, high))
    for turn in range(9):
        source = index - turn
        copied = enc.bind(enc.expr(ir.Sel(ir.Var('cells'), ir.Var('index')),
                                  {'cells': actual, 'index': source}))
        original = original.store(index + turn, original.select(source), guard)
        actual = enc.bind_value(actual.store(index + turn, copied, guard))
    # Invalid reads contribute zero; the physical array carrier does not.
    checks = [check for _, check in members(actual, min(0, low), max(0, high))]
    if selection_evidence:
        # Production retains the encoded selection's source for exact ranges.
        # Without that provenance, a coarse proof may emit a real predicate;
        # it must not invent a zero-only hull for packed variable shifts.
        assert all(z3.is_true(check) for check in checks)
    solver = z3.Solver(ctx=context)
    solver.add(*enc.defs)
    assert solver.check() == z3.sat
    solver.push()
    solver.add(z3.Not(z3.And(checks)))
    assert solver.check() == z3.unsat
    solver.pop()
    read = z3.BitVec('copy_read', 5, ctx=context)
    solver.add(actual.select(read) != original.select(read))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("width", [3, 5, 8])
@pytest.mark.parametrize("kind", ["signed", "unsigned", "difference"])
def test_multiplication_carrier_is_equivalent_without_input_assumptions(width, kind):
    enc = encoder._Encoder(None, {}, "product_carrier")
    rewrite = _ComparisonRewriter(enc.definitions)
    a, b = z3.BitVecs(f"product_a_{width} product_b_{width}", width)
    wide = 2 * width + 3
    extend = z3.SignExt if kind == "signed" else z3.ZeroExt
    left, right = (extend(wide - width, value) for value in (a, b))
    if kind == "difference":
        left, right = left - right, right - left
    left, right = enc.bind(left), enc.bind(right)
    small = rewrite.multiplication_width(left, right)
    assert small == 2 * width + (kind != "signed")
    reduced = z3.SignExt(wide - small, z3.Extract(small - 1, 0, left)
                        * z3.Extract(small - 1, 0, right))
    solver = z3.SolverFor("QF_BV")
    solver.set(timeout=10_000)
    solver.add(*enc.defs, left * right != reduced)
    assert solver.check() == z3.unsat


def test_multiplication_keeps_unknown_inactive_values_and_modular_overflow():
    a, b = z3.BitVecs("product_unrestricted_a product_unrestricted_b", 16)
    rewrite = _ComparisonRewriter([])
    narrow = z3.ZeroExt(12, z3.Extract(3, 0, a))
    assert rewrite.multiplication_width(narrow, narrow) == 9
    assert rewrite.multiplication_width(a, b) == 16
    assert rewrite.multiplication_width(
        z3.If(z3.Bool("product_active"), narrow, a), narrow) == 16
    assert rewrite.multiplication_width(a + 1, b) == 16
    assert _ComparisonRewriter([], work_limit=0).multiplication_width(narrow, narrow) == 16
    # A narrow result alone does not justify truncating an unrestricted operand.
    assert rewrite.multiplication_width(a, z3.BitVecVal(0, 16)) == 16


def test_multiplication_encoder_uses_small_circuit_but_retains_wide_boundary():
    from behave.sim.symbolic import ranges

    wide = 212
    env = {name: ranges.Range(-(1 << 100), (1 << 100) - 1)
           for name in ("x", "y")}
    enc = encoder._Encoder(None, env, "product_integration")
    enc.scalar_width = wide
    a, b = z3.BitVecs("product_input_a product_input_b", 8)
    delta = enc.bind(z3.ZeroExt(wide - 8, a) - z3.ZeroExt(wide - 8, b))
    value = enc.expr(ir.Op("mul", ir.Var("x"), ir.Var("y")), {"x": delta, "y": delta})
    assert value.size() == wide
    assert z3.is_app_of(value, z3.Z3_OP_SIGN_EXT)
    assert z3.is_app_of(value.arg(0), z3.Z3_OP_BMUL)
    assert value.arg(0).size() == 17
    # Wide frontend envelopes are intentionally not trusted by the rewrite.
    for left, right in itertools.product((0, 1, 127, 128, 254, 255), repeat=2):
        concrete = z3.substitute(value, (delta, z3.BitVecVal(left - right, wide)))
        assert z3.simplify(concrete).as_signed_long() == (left - right) ** 2


def test_dynamic_packed_index_keeps_bits_when_shift_range_crosses_word_width():
    from behave.sim.symbolic import ranges

    packed, count = z3.BitVec('packed_index_word', 44), z3.BitVec('packed_index_count', 6)
    index = z3.ZeroExt(27, z3.Extract(4, 0, z3.LShR(packed, z3.ZeroExt(38, count))))
    enc = encoder._Encoder(None, {'index': ranges.Range(0, 31)}, 'packed_index')
    enc.scalar_width = 32
    product = enc.expr(ir.Op('mul', ir.Var('index'), ir.Lit(1)), {'index': index})
    solver = z3.Solver()
    solver.add(*enc.defs, product != index)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("width", [3, 5, 8])
def test_division_carrier_preserves_signs_zero_and_minimum_over_minus_one(width):
    enc = encoder._Encoder(None, {}, "division_carrier")
    rewrite = _ComparisonRewriter(enc.definitions)
    a, b = z3.BitVecs(f"division_a_{width} division_b_{width}", width)
    left, right = (enc.bind(z3.SignExt(8, value)) for value in (a, b))
    small = rewrite.division_width(left, right)
    assert small == width + 1  # min / -1 remains positive in the wide result.
    original = encoder._Encoder.floor_div(left, right)
    reduced = z3.SignExt(original.size() - small, encoder._Encoder.floor_div(
        z3.Extract(small - 1, 0, left), z3.Extract(small - 1, 0, right)))
    solver = z3.Solver()
    solver.add(*enc.defs, original != reduced)
    assert solver.check() == z3.unsat


def test_division_carrier_keeps_guarded_unknowns_and_exhaustion_unrestricted():
    a, b = z3.BitVecs("division_unbounded_a division_unbounded_b", 65)
    rewrite = _ComparisonRewriter([])
    narrow = z3.ZeroExt(57, z3.Extract(7, 0, a))
    assert rewrite.division_width(narrow, z3.BitVecVal(3, 65)) == 9
    assert rewrite.division_width(z3.If(z3.Bool("division_active"), narrow, a), b) == 65
    assert _ComparisonRewriter([], work_limit=0).division_width(narrow, b) == 65


def test_division_carrier_is_used_by_the_encoder_without_narrowing_inputs():
    from behave.sim.symbolic import ranges

    a, b = z3.BitVecs("division_source_a division_source_b", 5)
    rho = {name: z3.SignExt(59, term) for name, term in (("a", a), ("b", b))}
    env = {name: ranges.Range(-(1 << 63), (1 << 63) - 1) for name in rho}
    enc = encoder._Encoder(None, env, "division_integration")
    result = enc.expr(ir.Op("fdiv", ir.Var("a"), ir.Var("b")), rho)
    expected = encoder._Encoder.floor_div(*(encoder._extend(rho[name], result.size())
                                           for name in ("a", "b")))
    solver = z3.Solver()
    solver.add(*enc.defs, result != expected)
    assert solver.check() == z3.unsat
    seen, divisions, pending = set(), [], [result, *enc.defs]
    while pending:
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        if z3.is_app(term):
            if term.decl().kind() in {z3.Z3_OP_BUDIV, z3.Z3_OP_BUDIV_I}:
                divisions.append(term.size())
            pending.extend(term.children())
    assert divisions == [6]


@pytest.mark.parametrize("width", [4, 8, 259])
@pytest.mark.parametrize("relation", [operator.lt, operator.le, operator.gt,
                                      operator.ge, operator.eq, operator.ne,
                                      z3.ULT, z3.ULE, z3.UGT, z3.UGE])
def test_bounds_masks_and_wrap_match_unrestricted_bitvector_semantics(width, relation):
    enc = encoder._Encoder(None, {}, "rewrite")
    rewrite = _ComparisonRewriter(enc.definitions)
    a, b = z3.BitVecs(f"rewrite_a_{width} rewrite_b_{width}", width)
    active = z3.Bool("rewrite_active")
    cases = [a, a & 3, z3.If(active, a & 3, z3.BitVecVal(0, width)),
             (a & 3) + 1, a + 1, a << b, a >> b,
             -(a & 3), z3.Extract(width - 1, 0, z3.ZeroExt(2, a) + 1)]
    differences = []
    for value, bound in itertools.product(cases, [-1, 0, 3, 4]):
        named = enc.bind(value)
        term = z3.If(relation(named, z3.BitVecVal(bound, width)),
                     z3.BitVecVal(7, width), z3.BitVecVal(-2, width))
        reduced = rewrite.rewrite(term)
        differences.append(term != reduced)
    solver = z3.SolverFor("QF_BV")
    solver.set(timeout=10_000)
    solver.add(*enc.defs, z3.Or(differences))
    # No constraints on inputs or active control, including signed overflow.
    assert solver.check() == z3.unsat


def test_guarded_aliases_prove_bounds_without_assuming_the_guard():
    enc = encoder._Encoder(None, {}, "guarded")
    rewrite = _ComparisonRewriter(enc.definitions)
    active = z3.Bool("guarded_active")
    index = z3.BitVecVal(0, 16)
    for j in range(32):
        index = enc.bind(z3.If(active, z3.BitVecVal(j, 16), index))
        term = z3.If(index >= 32, z3.BitVecVal(1, 16), z3.BitVecVal(0, 16))
        assert z3.is_bv_value(rewrite.rewrite(term))
        assert rewrite.rewrite(term).as_long() == 0
    # A later distinct SSA write must not reuse the earlier value's interval.
    bad = enc.bind(z3.If(active, z3.BitVecVal(32, 16), index))
    term = z3.If(bad >= 32, z3.BitVecVal(1, 16), z3.BitVecVal(0, 16))
    assert rewrite.rewrite(term).eq(term)
    solver = z3.Solver()
    solver.add(*enc.defs, rewrite.rewrite(term) == 1)
    assert solver.check() == z3.sat


def test_inactive_old_value_is_not_discarded():
    enc = encoder._Encoder(None, {}, "inactive")
    value = enc.bind(z3.If(z3.Bool("active"), z3.BitVecVal(2, 8),
                          z3.BitVecVal(-8, 8)))
    term = z3.If(value < 0, z3.BitVecVal(1, 8), z3.BitVecVal(0, 8))
    assert _ComparisonRewriter(enc.definitions).rewrite(term).eq(term)


@pytest.mark.parametrize("bounded", [False, True])
def test_completed_rewrite_is_reused_only_for_identical_ssa_operands(monkeypatch, bounded):
    enc = encoder._Encoder(None, {}, "repeat")
    a = z3.BitVec("repeat_a", 8)
    value = enc.bind(a & 3) if bounded else a
    term = z3.If(value >= 4, z3.BitVecVal(1, 8), z3.BitVecVal(0, 8))
    rewrite = _ComparisonRewriter(enc.definitions)
    result = rewrite.rewrite(term)
    later = enc.bind(a + 5)
    with monkeypatch.context() as guard:
        def repeated_proof(*_args):
            pytest.fail("an identical comparison repeated its completed SSA proof")
        guard.setattr(type(rewrite._proof), "coarse_signed_hull", repeated_proof)
        assert rewrite.rewrite(term) is result
    changed = z3.If(later >= 4, z3.BitVecVal(1, 8), z3.BitVecVal(0, 8))
    updated = rewrite.rewrite(changed)
    solver = z3.SolverFor("QF_BV")
    solver.add(*enc.defs, z3.Or(result != term, updated != changed))
    assert solver.check() == z3.unsat


def test_exhausted_proof_returns_the_original_term():
    enc = encoder._Encoder(None, {}, "exhausted")
    a = enc.bind(z3.BitVec("unbounded", 8) & 3)
    term = z3.If(a >= 4, z3.BitVecVal(1, 8), z3.BitVecVal(0, 8))
    rewrite = _ComparisonRewriter(enc.definitions, work_limit=0)
    assert rewrite.rewrite(term).eq(term)
    # Exhaustion is sticky and changes neither the original equations nor the
    # later query. Ordinary encoding/solving remains available.
    assert rewrite.rewrite(term).eq(term)
    solver = z3.Solver()
    solver.add(*enc.defs, term == 1)
    assert solver.check() == z3.unsat


def test_rewrite_follows_construction_cancellation_and_reuses_completed_proofs():
    enc = encoder._Encoder(None, {}, 'rewrite_checkpoint')
    cancelled, calls = True, 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if cancelled:
            raise TimeoutError('owning construction stopped')

    enc._checkpoint = checkpoint
    rewrite = enc.term_rewriter()
    assert rewrite._proof.work.remaining is None
    a = z3.BitVec('rewrite_checkpoint_input', 32) & 5
    with pytest.raises(TimeoutError, match='owning construction stopped'):
        rewrite.multiplication_width(a, a)
    assert not rewrite._exhausted  # Cancellation is not a sticky no-proof fact.
    cancelled = False
    assert rewrite.multiplication_width(a, a) == 6
    completed = calls
    for _ in range(100):
        assert rewrite.multiplication_width(a, a) == 6
    assert calls == completed  # No revisits to the already proved operand DAG.


@pytest.mark.parametrize("case", ["basic", "count-write", "nested-distinct",
                                  "nested-same", "index-write"])
def test_complete_results_match_unrewritten_encoder_and_interpreter(monkeypatch, case):
    from tests.sim.test_loop_index_specialization import command, encode, leaves

    body = command(case)
    pre = {name: z3.BitVec("rewrite_pre_" + name, 16)
           for name in ("enabled", "n", "stop")}
    pre.update({name: z3.BitVecVal(value, 16)
                for name, value in {"i": 6, "j": 5, "x": 1, "y": 7}.items()})
    pre["buf"] = [z3.BitVecVal(0, 16) for _ in range(3)]
    fast, after = encode(body, pre, "fast")
    with monkeypatch.context() as plain:
        plain.setattr(_ComparisonRewriter, "rewrite", lambda self, value: value)
        old, before = encode(body, pre, "original")
    solver = z3.SolverFor("QF_BV")
    solver.set(timeout=10_000)
    solver.add(*fast.defs, *old.defs, pre["enabled"] >= 0, pre["enabled"] <= 1,
               pre["n"] >= -1, pre["n"] <= 3, pre["stop"] >= -1, pre["stop"] <= 2)
    differences = [after.ok != before.ok, after.error != before.error]
    for name in pre:
        differences.extend(a != b for a, b in zip(leaves(after.rho[name]),
                                                  leaves(before.rho[name]), strict=True))
    differences.extend(after.hits[g] != before.hits[g] for g in after.hits)
    solver.push()
    solver.add(z3.Or(differences))
    assert solver.check() == z3.unsat
    solver.pop()
    for enabled, n, stop in itertools.product(range(2), range(-1, 4), range(-1, 3)):
        values = {"enabled": enabled, "n": n, "stop": stop,
                  "i": 6, "j": 5, "x": 1, "y": 7, "buf": [0] * 3}
        native, hits, failed = interp._Interp(values), set(), False
        try:
            native.run(body, hits)
        except interp.Halt:
            failed = True
        solver.push()
        solver.add(pre["enabled"] == enabled, pre["n"] == n, pre["stop"] == stop)
        assert solver.check() == z3.sat
        model = solver.model()
        assert z3.is_true(model.eval(after.ok)) == (not failed)
        assert {g for g, hit in after.hits.items() if z3.is_true(model.eval(hit))} == hits
        for name, value in values.items():
            assert [model.eval(t).as_signed_long() for t in leaves(after.rho[name])] \
                == (value if isinstance(value, list) else [value])
        solver.pop()


def test_ir_fixed_loop_index_check_is_false_but_catalogue_is_unchanged():
    from tests.sim.test_loop_index_specialization import encode

    body = ir.For("i", ir.Lit(3), ir.Seq(
        ir.Assign("copy", ir.Var("i")),
        ir.If(ir.Op("ge", ir.Var("copy"), ir.Lit(3)),
              ir.Fail("Stopped", "index-error"), ir.Mark("in-range"))))
    enc, state = encode(body, {"copy": z3.BitVecVal(0, 16)}, "index-proof")
    solver = z3.Solver()
    solver.add(*enc.defs, state.hits.get("index-error", z3.BoolVal(False)))
    assert solver.check() == z3.unsat
    assert any(isinstance(node, ir.Fail) and node.goal == "index-error"
               for node in ir.walk(body))
