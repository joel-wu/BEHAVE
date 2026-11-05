"""Pure-function proof abstraction must never manufacture exact evidence."""

from types import SimpleNamespace

import pytest
import z3

from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check
from behave.sim.symbolic.step_template import StepTemplate
from behave.sim.core.query import SolverAnswer
from behave.sim.core.refusal import Refused
from behave.sim.rtl.analysis_job import _SourceAnalysis
from behave.sim.rtl.source_confluence import SourceOrderComparison


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('selectors', [False, True])
def test_arithmetic_cut_shares_dag_decoding_between_its_passes(monkeypatch, private, selectors):
    from collections import Counter
    from behave.sim.rtl.source_domains import _transition_proof_roots

    context = z3.Context() if private else z3.main_ctx()
    a, b = z3.BitVecs('shared_cut_a shared_cut_b', 32, ctx=context)
    value = a * b
    for index in range(64):
        value = z3.If(z3.Bool(f'shared_cut_{index}', ctx=context), value + a, value - b)
    roots = (value == 0, value != 0, a == a)
    calls, original = Counter(), z3.ExprRef.children

    def counted(term):
        calls[term.get_id()] += 1
        return original(term)

    with monkeypatch.context() as patch:
        patch.setattr(z3.ExprRef, 'children', counted)
        relaxed = _transition_proof_roots(roots, check=lambda: None, data_selectors=selectors)
    assert calls and max(calls.values()) == 1
    solver = z3.Solver(ctx=context)
    assert solver.check(relaxed[2]) == z3.sat
    assert solver.check(relaxed[0], relaxed[1]) == z3.unsat


@pytest.mark.parametrize('simplified', [False, True])
@pytest.mark.parametrize('unsigned', [False, True])
def test_local_cut_keeps_predicated_counter_updates(simplified, unsigned):
    from behave.sim.rtl.source_domains import _transition_proof_roots

    raw, payload = z3.BitVecs('predicated_count_raw predicated_count_payload', 8)
    active = z3.Bool('predicated_count_active')
    zero = raw == 0
    value = z3.If(zero, z3.ZeroExt(30, z3.If(z3.And(active, zero),
        z3.BitVecVal(1, 2), z3.BitVecVal(0, 2))), z3.ZeroExt(24, raw))
    for index in range(26):
        payload = z3.If(z3.Bool(f'payload_choice_{index}'), payload * raw, payload + 1)
        over_floor = z3.UGT(value, 1) if unsigned else value > 1
        value = z3.If(z3.And(active, z3.Extract(0, 0, payload) == 0, over_floor),
                      value - 1, value)
    below_floor = z3.ULT(value, 1) if unsigned else value < 1
    roots = (z3.And(active, below_floor), z3.And(z3.Not(active), value == 0),
             z3.And(active, value == 1))
    if simplified:
        roots = tuple(z3.simplify(root) for root in roots)
    bounds = []
    bad, dormant, reachable = _transition_proof_roots(roots, check=lambda: None,
        bounds=bounds, data_selectors=True)
    solver = z3.Solver()
    solver.add(*bounds)
    assert solver.check(bad) == z3.unsat
    assert solver.check(dormant) == z3.sat  # Activation is not assumed globally.
    assert solver.check(reachable) == z3.sat


def test_comparison_control_slice_is_shared_and_stops_at_data_operations():
    from behave.sim.rtl.source_domains import _comparison_control_values

    visits = []
    for count in (16, 256):
        x, y = z3.BitVecs(f'control_slice_x_{count} control_slice_y_{count}', 32)
        payload = z3.If(z3.Bool(f'control_slice_data_{count}'), x * y, x + y)
        value = z3.BitVecVal(1, 32)
        comparisons = []
        for index in range(count):
            value = z3.If(z3.Bool(f'control_slice_{count}_{index}'), value + 1, value)
            comparisons.append(value < 100)
        comparisons.append(z3.Extract(0, 0, payload ^ x) < 0)
        calls = []
        kept = _comparison_control_values(comparisons, lambda: calls.append(1))
        assert value.get_id() in kept
        assert payload.get_id() not in kept
        visits.append(len(calls))
    assert visits[1] <= visits[0] * 16


@pytest.mark.parametrize('width', [8, 12, 32])
@pytest.mark.parametrize('private', [False, True])
def test_control_truncation_retains_guarded_range_without_assuming_an_input_bound(width, private):
    from behave.sim.rtl.source_domains import _transition_proof_roots

    ctx = z3.Context() if private else z3.main_ctx()
    raw = z3.BitVec('truncated_control', width, ctx=ctx)
    enabled = z3.Bool('truncated_enable', ctx=ctx)
    limit = (1 << (width - 2)) + 3
    clamped = z3.If(z3.ULT(raw, limit), raw, z3.BitVecVal(limit - 1, width, ctx=ctx))
    index = z3.Extract(width - 2, 0, clamped)
    # The raw input ranges over the full carrier. Its active branch, not a
    # global input assumption or joined interval, bounds the truncated index.
    bad = z3.UGE(index, limit)
    roots = (z3.And(enabled, bad), z3.And(enabled, index == limit - 1),
             z3.And(z3.Not(enabled), z3.UGE(raw, limit)))
    bounds = []
    cut = _transition_proof_roots(roots, check=lambda: None, bounds=bounds, data_selectors=True)
    assert all(a.eq(b) for a, b in zip(roots, cut, strict=True))
    assert not bounds
    solver = z3.Solver(ctx=ctx)
    assert solver.check(cut[0]) == z3.unsat
    assert solver.check(cut[1]) == z3.sat
    assert solver.check(cut[2]) == z3.sat
    # Truncation itself must not be interpreted as clamping.
    unsafe = z3.UGE(z3.Extract(width - 2, 0, raw), limit)
    relaxed, = _transition_proof_roots((unsafe,), check=lambda: None, bounds=[], data_selectors=True)
    assert solver.check(relaxed) == z3.sat


def test_comparison_control_slice_honors_cancellation_and_binders():
    from behave.sim.rtl.source_domains import _comparison_control_values

    x = z3.BitVec('control_slice_bound', 8)
    assert _comparison_control_values((z3.ForAll(x, x < 1),), lambda: None) == set()
    def stop():
        raise InterruptedError('cancelled control slice')
    with pytest.raises(InterruptedError, match='cancelled control slice'):
        _comparison_control_values((x < 1,), stop)


@pytest.mark.parametrize('private', [False, True])
def test_protocol_control_cut_restores_all_roots_and_keeps_wiring(monkeypatch, private):
    from behave.sim.rtl.source_stability import _control_proof_roots

    ctx = z3.Context() if private else z3.main_ctx()
    a, b, old, new = z3.BitVecs('control_a control_b control_old control_new', 8, ctx=ctx)
    enable = z3.Bool('control_enable', ctx=ctx)
    data = ((a + b) ^ z3.RotateLeft(a, 3)) + a * b
    roots = (new == z3.If(enable, data, old), z3.Extract(3, 0, data) == 7,
             z3.SignExt(2, data), z3.Not(enable), new != old)
    cuts, fresh = {}, z3.FreshConst
    def capture(sort, **options):
        value = fresh(sort, **options)
        cuts[value.get_id()] = value
        return value
    monkeypatch.setattr(z3, 'FreshConst', capture)
    relaxed = _control_proof_roots(roots, check=lambda: None)
    assert len(cuts) == 1
    cut, = cuts.values()
    assert all(z3.substitute(image, (cut, data)).eq(original)
               for original, image in zip(roots, relaxed, strict=True))
    assert z3_check(tuple(term for term in relaxed if z3.is_bool(term)), 1000).answer is SolverAnswer.UNSAT
    # An actual overwrite remains possible. The relaxation must not certify it.
    bad = _control_proof_roots((new == data, z3.Not(enable), new != old), check=lambda: None)
    assert z3_check(bad, 1000).answer is SolverAnswer.SAT


def test_protocol_control_cut_does_not_walk_discarded_data_cones(monkeypatch):
    from behave.sim.rtl.source_stability import _control_proof_roots

    a, b = z3.BitVecs('control_cost_a control_cost_b', 32)
    visits, children = [], z3.ExprRef.children
    for count in (16, 2048):
        data = a
        for index in range(count):
            data = (data + index) ^ z3.RotateLeft(data, 1)
        calls = []
        def counted(term):
            calls.append(term.get_id())
            return children(term)
        with monkeypatch.context() as patch:
            patch.setattr(z3.ExprRef, 'children', counted)
            roots = _control_proof_roots((data == b, z3.Extract(7, 0, data) != 0), check=lambda: None)
        visits.append(len(calls))
        assert roots[0].arg(0).eq(roots[1].arg(0).arg(0))
    # Keep the top XOR, but never traverse either discarded arithmetic cone.
    # Work is independent of cone depth, not a magic allowed node count.
    assert visits[0] == visits[1]


def test_protocol_control_cut_keeps_binders_and_obeys_cancellation():
    from behave.sim.rtl.source_stability import _control_proof_roots

    a = z3.BitVec('control_bound_a', 8)
    root = z3.ForAll(a, a + 1 != a)
    assert _control_proof_roots((root,), check=lambda: None)[0] is root
    def stop():
        raise InterruptedError('owner cancelled')
    with pytest.raises(InterruptedError, match='owner cancelled'):
        _control_proof_roots((root,), check=stop)


def test_protocol_control_cut_retains_one_bit_boolean_logic():
    from behave.sim.rtl.source_stability import _control_proof_roots

    valid, ready = z3.BitVecs('control_bit_valid control_bit_ready', 1)
    roots = ((~valid | ready) == 0, valid == 1, ready == 0)
    result = _control_proof_roots(roots, check=lambda: None)
    assert all(before is after for before, after in zip(roots, result, strict=True))


@pytest.mark.parametrize('width', [1, 8, 256])
@pytest.mark.parametrize('overwrite', [False, True])
def test_protocol_bitmix_cut_preserves_holds_not_overwrites(monkeypatch, width, overwrite):
    from behave.sim.rtl.source_stability import _control_proof_roots

    a, b, old, new = z3.BitVecs('bitmix_a bitmix_b bitmix_old bitmix_new', width)
    enable = z3.Bool('bitmix_enable')
    data = (a ^ b) ^ z3.RotateLeft(a & b, 1)
    roots = (new == (data if overwrite else z3.If(enable, data, old)),
             z3.Extract(0, 0, data) == 0, z3.Not(enable), new != old)
    fresh, cuts = z3.FreshConst, []
    def capture(sort, **options):
        value = fresh(sort, **options)
        cuts.append(value)
        return value
    monkeypatch.setattr(z3, 'FreshConst', capture)
    relaxed = _control_proof_roots(roots, check=lambda: None)
    cut, = cuts
    assert all(z3.substitute(after, (cut, data)).eq(before)
               for before, after in zip(roots, relaxed, strict=True))
    solver = z3.Solver()
    solver.add(*relaxed)
    assert solver.check() == (z3.sat if overwrite else z3.unsat)


def test_control_candidates_follow_shared_aliases_with_linear_work():
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_stability import _control_constants

    measured = []
    for count in (8, 2048):
        body, locals_ = [], []
        previous = 'phase'
        for index in range(count):
            name = f'alias_{index}'
            locals_.append(name)
            body.extend((ir.Assign(name, ir.Op('uwrap', ir.Var(previous), ir.Lit(9))),
                         ir.If(ir.Op('eq', ir.Var(name), ir.Lit(257)), ir.Skip(), ir.Skip())))
            previous = name
        # A mutable temporary is not a single-assignment register alias.
        body.extend((ir.Assign('changed', ir.Var('phase')), ir.Assign('changed', ir.Lit(0)),
                     ir.If(ir.Op('eq', ir.Var('changed'), ir.Lit(42)), ir.Skip(), ir.Skip())))
        process = SimpleNamespace(write_masks=(('phase', 0, 511), ('flag', 0, 1)),
            procedure=SimpleNamespace(locals=(*locals_, 'changed')), activation=ir._sequence(body))
        machine = SimpleNamespace(program=SimpleNamespace(processes=(process,), kinds={
            'phase': IntegralType(9, False), 'flag': IntegralType(1, True)}))
        calls = 0
        def check():
            nonlocal calls
            calls += 1
        assert _control_constants(machine, ('phase', 'flag'), check=check) == (
            ('flag', -1), ('flag', 0), ('phase', 257))
        measured.append(calls)
    assert measured[1] <= measured[0] * (2048 // 8)


@pytest.mark.parametrize('width', [1, 2, 8, 65])
@pytest.mark.parametrize('private', [False, True])
def test_protocol_control_cut_preserves_packed_masks_at_any_width(width, private):
    from behave.sim.rtl.source_stability import _control_proof_roots

    context = z3.Context() if private else z3.main_ctx()
    ready, busy = z3.BitVecs('mask_ready mask_busy', width, ctx=context)
    all_bits = (1 << width) - 1
    # A control bit can have a widened IR carrier; packed flags can be wide.
    # Neither may become unrelated fresh values merely because size != 1.
    roots = (ready == all_bits, busy == 0, (ready & ~busy) != all_bits)
    relaxed = _control_proof_roots(roots, check=lambda: None)
    assert all(a is b for a, b in zip(roots, relaxed, strict=True))
    solver = z3.Solver(ctx=context)
    solver.add(*relaxed)
    assert solver.check() == z3.unsat
    # Real changed output remains possible; the environment is not assumed ready.
    solver = z3.Solver(ctx=context)
    solver.add(*_control_proof_roots((ready == 0, busy == 0,
                                    (ready & ~busy) != all_bits), check=lambda: None))
    assert solver.check() == z3.sat


def _template(*, extra=None, width=8):
    q, r, action = (z3.Var(i, z3.BitVecSort(width)) for i in range(3))
    large = ((q + r) * (q - r)) ^ ((q & r) << 2)
    roots = [large, large == 3, z3.If(action == 1, large, q)]
    if extra is not None:
        roots.append(extra(q, r, action))
    template = StepTemplate.__new__(StepTemplate)
    template.procedure = SimpleNamespace(state=('q', 'r'), inputs=('action',))
    template._parameters = dict(zip(('q', 'r', 'action'), z3.BitVecs('q r action', width)))
    template._image = z3.Function('abstraction_image', *(term.sort() for term in roots), z3.BoolSort())(*roots)
    template._array_image = None
    return template


def _variables(term):
    result, pending = set(), [term]
    while pending:
        value = pending.pop()
        if z3.is_var(value):
            result.add(z3.get_var_index(value))
        elif z3.is_app(value):
            pending.extend(value.children())
    return result


def test_abstraction_has_an_exact_interpretation_and_preserves_all_dependencies(monkeypatch):
    template = _template()
    original, replacements = template._image, []
    substitute = z3.substitute

    def capture(expression, *pairs):
        replacements.extend(pairs)
        return substitute(expression, *pairs)

    monkeypatch.setattr(z3, 'substitute', capture)
    abstract = template.abstract_state_functions(('q', 'r'), min_nodes=4)
    assert abstract is not None and abstract is not template
    assert template._image is original and replacements
    for exact, opaque in replacements:
        assert _variables(exact) == _variables(opaque) == {0, 1}
        assert {z3.get_var_index(arg) for arg in opaque.children()} == {0, 1}
        assert opaque.sort() == exact.sort()
    assert len({opaque.decl().get_id() for _, opaque in replacements}) == len(replacements)
    # Choosing each function's interpretation as its old expression recovers
    # every original state/event/constraint root simultaneously.
    restored = substitute(abstract._image, *((new, old) for old, new in replacements))
    assert restored.eq(original)
    assert abstract._image.arg(2).arg(0).eq(original.arg(2).arg(0))  # Scheduler guard unchanged.


@pytest.mark.parametrize('kind', ['free', 'function', 'array', 'quantifier'])
def test_non_scalar_or_nonclosed_root_is_not_turned_into_a_pure_function(kind):
    def extra(q, r, action):
        if kind == 'free':
            return q + z3.BitVec('outside_boundary', 8)
        if kind == 'function':
            return z3.Function('outside_function', q.sort(), q.sort())(q)
        if kind == 'array':
            return z3.Select(z3.Array('outside_memory', q.sort(), q.sort()), q)
        x = z3.BitVec('bound', 8)
        return z3.ForAll(x, x == q)
    template = _template(extra=extra)
    abstract = template.abstract_state_functions(('q', 'r'), min_nodes=1)
    assert abstract is not None
    assert abstract._image.arg(3).eq(template._image.arg(3))


def test_proof_abstraction_is_optional_and_cancellable():
    template = _template()
    assert template.abstract_state_functions(()) is None
    assert template.abstract_state_functions(('q', 'r'), min_nodes=10000) is None
    with pytest.raises(ValueError, match='state'):
        template.abstract_state_functions(('action',))
    with pytest.raises(ValueError, match='positive'):
        template.abstract_state_functions(('q',), min_nodes=0)
    def cancel():
        raise InterruptedError('proof budget')
    with pytest.raises(InterruptedError, match='proof budget'):
        template.abstract_state_functions(('q', 'r'), check=cancel)


def test_cost_selection_keeps_small_logic_exact_but_includes_wide_nonlinear_datapaths():
    assert _template().abstract_state_functions(('q', 'r')) is None
    wide = _template(width=64)
    assert wide.abstract_state_functions(iter(('q', 'r'))) is not None


def test_local_arithmetic_abstraction_recovers_original_roots_and_shares_terms(monkeypatch):
    from behave.sim.rtl.source_domains import _transition_proof_roots

    ctx = z3.Context()
    a, b, c = z3.BitVecs('a b c', 17, ctx=ctx)
    roots = (a * b == b * a, (a / b) + z3.UDiv(a, b) == c,
             z3.SRem(a, b) == z3.URem(a, b), (a % b) == c,
             z3.If(a * b == c, (a * b) * c, a))
    cuts, fresh = {}, z3.FreshConst
    def capture(sort, **options):
        value = fresh(sort, **options)
        cuts[value.get_id()] = value
        return value
    monkeypatch.setattr(z3, 'FreshConst', capture)
    bounds = []
    opaque = _transition_proof_roots(roots, check=lambda: None, bounds=bounds)
    assert opaque[0].arg(0).eq(opaque[4].arg(0).arg(0))
    assert not opaque[0].arg(0).eq(opaque[0].arg(1))  # No invented algebraic relation.
    assert len(cuts) == 7  # Three products, one sum of divs, two rem, one mod.
    interpretations = {}
    # Giving each fresh value its original term restores every root at once.
    def restore(original, abstract):
        if abstract.get_id() in cuts:
            assert original.sort().eq(abstract.sort())
            prior = interpretations.setdefault(abstract.get_id(), original)
            assert prior.eq(original)
            return original
        if not z3.is_app(original):
            assert original.eq(abstract)
            return abstract
        assert original.sort().eq(abstract.sort())
        assert original.num_args() == abstract.num_args()
        args = [restore(x, y) for x, y in zip(original.children(), abstract.children())]
        assert original.decl().eq(abstract.decl())
        return original.decl()(*args)
    assert all(restore(a, b).eq(a) for a, b in zip(roots, opaque))
    restored_bounds = [z3.substitute(bound, *((cuts[k], v) for k, v in interpretations.items()))
                       for bound in bounds]
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Not(z3.And(z3.BoolVal(True, ctx=ctx), *restored_bounds)))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('width', [8, 17, 128])
def test_arithmetic_cuts_preserve_shared_bit_wiring(width):
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b = z3.BitVecs('wire_cut_a wire_cut_b', width)
    product = a * b
    low = z3.Extract(width - 2, 0, product)
    high = z3.Extract(width - 1, width - 1, product)
    roots = (product, low, z3.SignExt(3, product), z3.ZeroExt(3, product),
             z3.Concat(high, low))
    bounds = []
    cut, *images = _transition_proof_roots(roots, check=lambda: None, bounds=bounds)
    assert z3.is_const(cut) and cut.decl().kind() == z3.Z3_OP_UNINTERPRETED
    # There is one nonlinear value, not independent values for each of its
    # physical views. Restoring that value recovers ALL original roots.
    for original, image in zip(roots[1:], images, strict=True):
        assert z3.substitute(image, (cut, product)).eq(original)
    solver = z3.Solver()
    solver.add(*bounds, cut == 3, images[0] != 3)
    assert solver.check() == z3.unsat
    solver = z3.Solver()
    solver.add(*bounds, cut == 3, images[0] == 3)
    assert solver.check() == z3.sat  # A live model is not erased by the wiring.


@pytest.mark.parametrize('width', [4, 8, 17, 128])
@pytest.mark.parametrize('view', ['extract', 'concat', 'signed', 'unsigned'])
def test_negated_bit_views_keep_the_original_arithmetic_identity(width, view):
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b = z3.BitVecs('signed_wire_a signed_wire_b', width)
    product = a * b
    wire = {
        'extract': z3.Extract(width - 2, 0, product),
        'concat': z3.Concat(z3.Extract(width - 1, width - 1, product), product),
        'signed': z3.SignExt(1, product),
        'unsigned': z3.ZeroExt(1, product),
    }[view]
    # Lowering can spell the sign as unary negation or an all-ones product.
    # Both must observe the same bit view, not acquire an independent cut.
    roots = (product, wire, -wire, z3.BitVecVal(-1, wire.size()) * wire)
    bounds = []
    cut, image, unary, multiplied = _transition_proof_roots(
        roots, check=lambda: None, bounds=bounds)
    for original, abstract in zip(roots[1:], (image, unary, multiplied), strict=True):
        assert z3.substitute(abstract, (cut, product)).eq(original)
    solver = z3.Solver()
    solver.add(*bounds, z3.Or(unary != -image, multiplied != -image))
    assert solver.check() == z3.unsat
    # Sign-bit overflow remains the ordinary modular operation.
    solver = z3.Solver()
    solver.add(*bounds, cut == 1 << (width - 1))
    assert solver.check() == z3.sat


def test_local_arithmetic_sat_is_not_exact_and_unsat_control_remains_a_proof():
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b = z3.BitVecs('local_mul_a local_mul_b', 8)
    exact = a * b != b * a
    abstract, control = _transition_proof_roots(
        (exact, z3.And(a * b == 1, a < b, b < a)), check=lambda: None)
    assert z3_check((exact,), 1000).answer is SolverAnswer.UNSAT
    assert z3_check((abstract,), 1000).answer is SolverAnswer.SAT
    assert z3_check((control,), 1000).answer is SolverAnswer.UNSAT


@pytest.mark.parametrize('signed_left,signed_right', [(True, True), (True, False),
                                                    (False, True), (False, False)])
def test_nonwrapping_product_bounds_preserve_zero_and_every_original_value(signed_left, signed_right):
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b = z3.BitVecs('bounded_product_a bounded_product_b', 4)
    left = (z3.SignExt if signed_left else z3.ZeroExt)(5, a)
    right = (z3.SignExt if signed_right else z3.ZeroExt)(5, b)
    bounds = []
    cut, = _transition_proof_roots((left * right,), check=lambda: None, bounds=bounds)
    restored = [z3.substitute(bound, (cut, left * right)) for bound in bounds]
    assert z3_check((z3.Not(z3.And(*restored)),), 10000).answer is SolverAnswer.UNSAT
    # Independent interval cuts admitted this impossible product.
    assert z3_check((*bounds, a == 0, cut != 0), 1000).answer is SolverAnswer.UNSAT
    assert z3_check((*bounds, b == 0, cut != 0), 1000).answer is SolverAnswer.UNSAT
    # The relaxation still admits genuine original values of either sign.
    assert z3_check((*bounds, a == 15, b == 3, cut == left * right),
                    1000).answer is SolverAnswer.SAT


def test_wrapping_product_keeps_modular_semantics_without_linear_bounds():
    from behave.sim.rtl.source_domains import _nonwrapping_product_hulls, _transition_proof_roots
    from behave.sim.symbolic.closure_proof import _SyntacticClosureProof

    a, b = z3.BitVecs('wrapping_product_a wrapping_product_b', 4)
    assert _nonwrapping_product_hulls(a * b, _SyntacticClosureProof({})) is None
    bounds = []
    cut, = _transition_proof_roots((a * b,), check=lambda: None, bounds=bounds)
    assert z3_check((*bounds, a == 4, b == 3, cut == -4), 1000).answer is SolverAnswer.SAT
    restored = [z3.substitute(bound, (cut, a * b)) for bound in bounds]
    assert z3_check((z3.Not(z3.And(z3.BoolVal(True), *restored)),),
                    1000).answer is SolverAnswer.UNSAT


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('shared_selector', [False, True])
@pytest.mark.parametrize('active', [False, True])
def test_conditional_product_bounds_keep_narrow_arm_and_arbitrary_inactive_values(
        private, shared_selector, active):
    from behave.sim.rtl.source_domains import _transition_proof_roots

    ctx = z3.Context() if private else z3.main_ctx()
    a, b = z3.BitVecs('guarded_product_a guarded_product_b', 3, ctx=ctx)
    old_a, old_b = z3.BitVecs('guarded_old_a guarded_old_b', 9, ctx=ctx)
    guard = z3.Bool('guarded_product_guard', ctx=ctx)
    selected = guard if active else z3.Not(guard)
    left = z3.If(selected, z3.SignExt(6, a), old_a)
    right = z3.If(selected, z3.SignExt(6, b), old_b) if shared_selector else z3.SignExt(6, b)
    product = left * right
    bounds = []
    cut, = _transition_proof_roots((product,), check=lambda: None, bounds=bounds)
    restored = [z3.substitute(bound, (cut, product)) for bound in bounds]
    assert z3_check((z3.Not(z3.And(*restored)),), 10000).answer is SolverAnswer.UNSAT
    assert z3_check((*bounds, selected, a == 0, cut != 0), 1000).answer is SolverAnswer.UNSAT
    assert z3_check((*bounds, selected, cut > 16), 1000).answer is SolverAnswer.UNSAT
    # Do not turn an inactive old value or real modular overflow into a proof.
    assert z3_check((*bounds, z3.Not(selected), old_a == 128,
                    (old_b if shared_selector else b) == 3, cut == -128),
                   1000).answer is SolverAnswer.SAT


def test_conditional_product_bounds_do_not_pair_independent_guards():
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b = z3.BitVecs('independent_product_a independent_product_b', 4)
    old_a, old_b = z3.BitVecs('independent_old_a independent_old_b', 9)
    g, h = z3.Bools('independent_product_g independent_product_h')
    product = z3.If(g, z3.ZeroExt(5, a), old_a) * z3.If(h, z3.ZeroExt(5, b), old_b)
    bounds = []
    cut, = _transition_proof_roots((product,), check=lambda: None, bounds=bounds)
    assert not bounds
    assert z3_check((g, z3.Not(h), a == 4, old_b == 3, cut == 12), 1000).answer is SolverAnswer.SAT


def test_guarded_product_prefilter_proves_only_impossible_active_event():
    from behave.sim.rtl.source_domains import prove_transition_goals

    a, b = z3.BitVecs('guarded_group_a guarded_group_b', 3)
    old_a, old_b = z3.BitVecs('guarded_group_old_a guarded_group_old_b', 9)
    active = z3.Bool('guarded_group_active')
    value = z3.If(active, z3.SignExt(6, a), old_a) * z3.If(active, z3.SignExt(6, b), old_b)
    hits = {'outside': z3.And(active, value > 16),
            'zero': z3.And(active, value == 0),
            'overflow': z3.And(z3.Not(active), value == -128)}
    encoded = SimpleNamespace(constraint=z3.BoolVal(True), hits=hits)
    template = SimpleNamespace(literal_false_hits=lambda _: frozenset(),
        project_local=lambda _: SimpleNamespace(encode=lambda **_: encoded))
    source = SimpleNamespace(template=SimpleNamespace(local_observations=lambda _: template),
        machine=SimpleNamespace(program=SimpleNamespace(kinds={}),
                                occurrences=tuple((name, None, name) for name in hits)))
    assert prove_transition_goals(source, tuple(hits),
        solve=lambda phase, roots, **options: z3_check(roots, 1000),
        check=lambda: None) == {'outside'}
    assert encoded.hits is hits  # Possible local events retain the exact original history.


def test_nested_product_bounds_share_operand_cuts_and_restore_exactly():
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b, c = (z3.SignExt(10, term) for term in z3.BitVecs('nested_a nested_b nested_c', 3))
    inner, product = a * b, (a * b) * c
    bounds = []
    outer, first = _transition_proof_roots((product, inner), check=lambda: None, bounds=bounds)
    restored = [z3.substitute(bound, (outer, product), (first, inner)) for bound in bounds]
    assert z3_check((z3.Not(z3.And(*restored)),), 10000).answer is SolverAnswer.UNSAT
    assert z3_check((*bounds, first == 0, outer != 0), 1000).answer is SolverAnswer.UNSAT
    # No quadratic congruence matrix or variable-by-variable multiplication
    # was reintroduced in the bound constraints.
    seen, pending = set(), list(bounds)
    while pending:
        term = pending.pop()
        if term.get_id() in seen or not z3.is_app(term):
            continue
        seen.add(term.get_id())
        if z3.is_app_of(term, z3.Z3_OP_BMUL):
            assert any(z3.is_bv_value(arg) for arg in term.children())
        pending.extend(term.children())


def test_control_free_arithmetic_is_one_bounded_cut_without_cutting_selector():
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b = (z3.SignExt(27, v) for v in z3.BitVecs('region_a region_b', 5))
    score = a * b + a * a
    selected = z3.If(score > 0, score, z3.BitVecVal(0, 32))
    bounds = []
    cut, retained = _transition_proof_roots((score, selected), check=lambda: None, bounds=bounds)
    assert z3.is_const(cut) and z3.is_app_of(retained, z3.Z3_OP_ITE)
    assert retained.arg(1).eq(cut)
    assert retained.arg(0).eq(cut > 0)
    solver = z3.Solver()
    solver.add(z3.Not(z3.And(*(z3.substitute(b, (cut, score)) for b in bounds))))
    assert solver.check() == z3.unsat  # Cut bounds hold for every original input.
    solver = z3.Solver()
    solver.add(*bounds, cut > 512)
    assert solver.check() == z3.unsat  # Not an unconstrained full-width cut.


def test_arithmetic_region_and_distributed_negation_share_one_value():
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b, c, d = z3.BitVecs('signed_region_a signed_region_b signed_region_c signed_region_d', 8)
    score = a * b + c * d
    negative = -1 * (c * d) + -1 * (a * b)
    left, right = _transition_proof_roots((score, negative), check=lambda: None)
    assert z3.is_const(left) and right.eq(-left)
    solver = z3.Solver()
    solver.add(score != -negative)
    assert solver.check() == z3.unsat


def test_separately_observed_region_operands_keep_their_linear_relationship():
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b, c, d = z3.BitVecs('linked_a linked_b linked_c linked_d', 8)
    bounds = []
    score, first, second = _transition_proof_roots((a * b + c * d, a * b, c * d),
        check=lambda: None, bounds=bounds)
    solver = z3.Solver()
    solver.add(*bounds, score != first + second)
    assert solver.check() == z3.unsat


def test_flattened_subtraction_recovers_the_shared_arithmetic_region():
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b, c, d, x, y = z3.BitVecs('flat_a flat_b flat_c flat_d flat_x flat_y', 8)
    control = z3.If(z3.Bool('flat_flag'), x, y)
    score = a * b + c * d
    difference = z3.simplify(control - score)
    cut, result = _transition_proof_roots((score, difference), check=lambda: None)
    solver = z3.Solver()
    solver.add(result != control - cut)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('data_selectors', [False, True])
def test_empty_cut_frontier_preserves_roots_without_wrapping_or_rewriting(monkeypatch, private, data_selectors):
    from behave.sim.rtl.source_domains import _transition_proof_roots

    context = z3.Context() if private else z3.main_ctx()
    value = z3.BitVec('linear_input', 32, ctx=context)
    guard = z3.Bool('linear_guard', ctx=context)
    shared = value
    for index in range(64):
        shared = z3.RotateLeft(shared, 3) ^ (shared * -3 + index)
    roots = (shared == 0, z3.If(guard, shared == 1, shared == 2),
             z3.If(guard, z3.BitVecVal(1, 32, ctx=context), z3.BitVecVal(0, 32, ctx=context)) == 1)
    def unnecessary(*args):
        raise AssertionError('empty cut frontier must not wrap or rewrite the DAG')
    bounds = []
    with monkeypatch.context() as patch:
        patch.setattr(z3.ExprRef, 'children', unnecessary)
        result = _transition_proof_roots(roots, check=lambda: None, bounds=bounds,
                                         data_selectors=data_selectors)
    assert all(actual is original for actual, original in zip(result, roots, strict=True))
    assert not bounds


def test_local_arithmetic_abstraction_preserves_cancellation_and_empty_roots():
    from behave.sim.rtl.source_domains import _transition_proof_roots

    def cancel():
        raise InterruptedError('owner cancelled')
    assert _transition_proof_roots((), check=cancel) == ()
    with pytest.raises(InterruptedError, match='owner cancelled'):
        _transition_proof_roots((z3.BoolVal(True),), check=cancel)


@pytest.mark.parametrize('width', [4, 8, 17])
def test_local_data_selectors_have_an_exact_interpretation(monkeypatch, width):
    from behave.sim.rtl.source_domains import _transition_proof_roots

    a, b = z3.BitVecs('selector_a selector_b', width)
    flag, other = z3.Bools('selector_flag selector_other')
    inner = z3.If(flag, a + b, a - b)
    outer = z3.If(other, inner << 1, inner ^ a)
    literal_selector = z3.If(flag, z3.BitVecVal(1, width), z3.BitVecVal(0, width))
    # Whole-value comparisons now keep their scalar control relationships.
    control_roots = (outer == 0, inner == 0)
    control = _transition_proof_roots(control_roots, check=lambda: None,
                                    bounds=[], data_selectors=True)
    assert all(a.eq(b) for a, b in zip(control_roots, control))
    # Non-affine data computations still permit selector abstraction; wiring
    # alone must not disconnect an earlier branch from a control comparison.
    inner, outer = (z3.Extract(width - 2, 0, value ^ a) for value in (inner, outer))
    roots = (outer == 0, z3.If(inner == 0, outer == 1, outer == 2), literal_selector == 1)
    cuts, fresh = {}, z3.FreshConst

    def capture(sort, **options):
        value = fresh(sort, **options)
        cuts[value.get_id()] = value
        return value

    monkeypatch.setattr(z3, 'FreshConst', capture)
    bounds = []
    abstract = _transition_proof_roots(roots, check=lambda: None, bounds=bounds,
                                      data_selectors=True)
    assert abstract[2].eq(roots[2])  # Literal selectors still expose Boolean control.
    assert z3.is_app_of(abstract[1], z3.Z3_OP_ITE)  # Do not cut Boolean event guards.
    interpretations = {}

    def restore(exact, cut):
        if cut.get_id() in cuts:
            previous = interpretations.setdefault(cut.get_id(), exact)
            assert previous.eq(exact)
            return exact
        assert exact.decl().eq(cut.decl()) and exact.num_args() == cut.num_args()
        return cut.decl()(*(restore(x, y) for x, y in zip(exact.children(), cut.children())))

    assert all(restore(exact, cut).eq(exact) for exact, cut in zip(roots, abstract))
    assert len(interpretations) == 2
    restored_bounds = [z3.substitute(term, *((cuts[k], value) for k, value in interpretations.items()))
                       for term in bounds]
    assert z3_check((z3.Not(z3.And(z3.BoolVal(True), *restored_bounds)),), 1000).answer is SolverAnswer.UNSAT


def test_source_selector_prefilter_sat_defers_the_original_goal():
    from behave.sim.rtl.source_domains import prove_transition_goals

    a = z3.BitVec('selector_original', 8)
    selected = z3.If(z3.Bool('selector_choice'), a, a)
    data = z3.BitVec('selector_payload', 8)
    low, expected = z3.Extract(0, 0, selected ^ data), z3.Extract(0, 0, a ^ data)
    hits = {'data': low != expected, 'control': z3.And(low == 1, low != 1)}
    encoded = SimpleNamespace(constraint=z3.BoolVal(True), hits=hits)
    template = SimpleNamespace(literal_false_hits=lambda _: frozenset(),
        project_local=lambda _: SimpleNamespace(encode=lambda **_: encoded))
    source = SimpleNamespace(template=SimpleNamespace(local_observations=lambda _: template),
        machine=SimpleNamespace(program=SimpleNamespace(kinds={}),
                                occurrences=tuple((name, None, name) for name in hits)))
    answers = []

    def solve(phase, roots, **options):
        answer = z3_check(roots, 1000)
        answers.append(answer.answer)
        return answer

    assert prove_transition_goals(source, tuple(hits), solve=solve, check=lambda: None) == {'control'}
    assert answers == [SolverAnswer.SAT, SolverAnswer.UNSAT]
    assert encoded.hits is hits
    assert z3_check((hits['data'],), 1000).answer is SolverAnswer.UNSAT




def test_inconclusive_arithmetic_groups_remain_for_the_original_history():
    from behave.sim.rtl.source_domains import prove_transition_goals

    a, b = z3.BitVecs('local_group_a local_group_b', 8)
    hits = {'arithmetic': a * b != b * a, 'control': z3.And(a < b, b < a)}
    encoded = SimpleNamespace(constraint=z3.BoolVal(True), hits=hits)
    template = SimpleNamespace(
        literal_false_hits=lambda _: frozenset(),
        project_local=lambda _: SimpleNamespace(encode=lambda **_: encoded))
    source = SimpleNamespace(template=SimpleNamespace(source=template, local_observations=lambda _: template),
        machine=SimpleNamespace(program=SimpleNamespace(kinds={}),
                                occurrences=tuple((name, None, name) for name in hits)))
    answers = []
    def solve(phase, roots, **options):
        result = z3_check(roots, 1000)
        answers.append(result.answer)
        return result
    proved = prove_transition_goals(source, tuple(hits), solve=solve, check=lambda: None)
    assert proved == {'control'}
    assert answers == [SolverAnswer.SAT, SolverAnswer.UNSAT]
    assert encoded.hits is hits and source.template.source is template
    assert z3_check((hits['arithmetic'],), 1000).answer is SolverAnswer.UNSAT


def _pair(roots, different):
    history = SimpleNamespace(constraints=roots, alive=z3.BoolVal(True),
        unfinished=z3.BoolVal(False), domain_faults={},
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=()), slots=()))
    return SourceOrderComparison(history, history, {'startup.state.q': different})


def _admission(*, impossible=False, actual_difference=False, abstract_difference=False):
    x, y = z3.Ints('proof_x proof_y')
    exact = _SourceAnalysis.__new__(_SourceAnalysis)
    exact.source = SimpleNamespace(template=SimpleNamespace(direct=None))
    exact.request = SimpleNamespace(constraints=(x >= 0, x <= 1), lengths={}, queues={},
        atomic=False, settled=False,
        rows=lambda model: [{'x': model.eval(x).as_long()}], fix=lambda rows: (x == rows[0]['x'],))
    roots = (*exact.request.constraints, y == x + 1, z3.BoolVal(not impossible))
    exact._comparison = _pair(roots, x == 1 if actual_difference else y != x + 1)
    exact._admitted_comparison = None
    abstract = _pair(exact.request.constraints, y != x + 1 if abstract_difference else z3.BoolVal(False))
    exact.relation = SimpleNamespace(abstract_order_comparison=lambda *_args, **_kwargs: abstract)
    calls = []
    def solve(phase, assertions):
        calls.append((phase, assertions))
        return z3_check(assertions, 10000)
    return exact, abstract, solve, calls


def test_abstract_universal_proof_uses_exact_membership_and_caches_only_complete_admission():
    exact, _, solve, calls = _admission()
    assert exact._admit_domain(solve, lambda: None, prove=solve) == 'proved'
    assert exact._admitted_comparison is exact._comparison
    assert any(name.startswith('abstract:confluence:') for name, _ in calls)
    membership = next(roots for name, roots in calls if name == 'environment-member')
    full = exact._comparison.constraints
    member = z3_check(membership, 10000)
    assert member.answer is SolverAnswer.SAT
    fixed = exact.request.fix(exact.request.rows(member.model))
    assert z3_check((z3.Xor(z3.And(*membership), z3.And(*full, *fixed)),),
                    10000).answer is SolverAnswer.UNSAT
    count = len(calls)
    assert exact._admit_domain(solve, lambda: None, prove=solve) == 'reused'
    assert len(calls) == count


@pytest.mark.parametrize('answer', [SolverAnswer.SAT, SolverAnswer.UNKNOWN])
def test_abstract_counterexample_or_unknown_falls_back_to_exact_formula(answer):
    exact, _, solve, calls = _admission(abstract_difference=True)
    def prove(phase, assertions):
        if answer is SolverAnswer.UNKNOWN:
            calls.append((phase, assertions))
            return ExactCheckResult(answer, reason='unsupported proof heuristic')
        return solve(phase, assertions)
    assert exact._admit_domain(solve, lambda: None, prove=prove) == 'proved'
    assert any(name.startswith('confluence:') for name, _ in calls)
    assert sum(name == 'environment-member' for name, _ in calls) == 1


def test_abstract_nonempty_domain_cannot_replace_exact_membership():
    exact, _, solve, _ = _admission(impossible=True)
    with pytest.raises(Refused, match='empty-domain'):
        exact._admit_domain(solve, lambda: None, prove=solve)
    assert exact._admitted_comparison is None


def test_abstract_failure_does_not_hide_a_real_order_counterexample():
    exact, _, solve, _ = _admission(actual_difference=True, abstract_difference=True)
    with pytest.raises(Refused, match='order-sensitive'):
        exact._admit_domain(solve, lambda: None, prove=solve)
    assert exact._admitted_comparison is None


def test_proof_cancellation_never_caches_partial_admission_or_restarts_solver():
    exact, _, solve, calls = _admission()
    def cancel(_phase, _assertions):
        raise InterruptedError('same owner deadline')
    with pytest.raises(InterruptedError, match='same owner deadline'):
        exact._admit_domain(solve, lambda: None, prove=cancel)
    assert exact._admitted_comparison is None
    assert not any(name == 'completeness' for name, _ in calls)


def _induction_attempt(monkeypatch, exact, abstract, *, difference=False):
    stronger = _pair(abstract.constraints, z3.BoolVal(False))
    # The first conditional group succeeds; the second can fail. Its partial
    # proofs must not be transferred to the weaker, ordinary premise chain.
    stronger.differences['terminal.error'] = z3.BoolVal(difference)
    monkeypatch.setattr(SourceOrderComparison, 'inductive',
        lambda self, *, check: stronger if self is abstract else None)
    return stronger


@pytest.mark.parametrize('abstract_route', [False, True])
@pytest.mark.parametrize('answer', [SolverAnswer.SAT, SolverAnswer.UNKNOWN])
def test_incomplete_induction_discards_all_partial_proofs(monkeypatch, abstract_route, answer):
    exact, abstract, solve, calls = _admission()
    if not abstract_route:
        abstract = exact._comparison
        exact.relation.abstract_order_comparison = lambda *_args, **_kwargs: None
    stronger = _induction_attempt(monkeypatch, exact, abstract, difference=True)
    def owner(phase, assertions, **kwargs):
        if phase == 'inductive:confluence:product' and answer is SolverAnswer.UNKNOWN:
            calls.append((phase, assertions))
            return ExactCheckResult(answer, reason='inconclusive stronger invariant')
        return solve(phase, assertions)
    assert exact._admit_domain(owner, lambda: None, prove=owner) == 'proved'
    names = [name for name, _ in calls]
    assert 'inductive:confluence:startup' in names
    assert 'inductive:confluence:product' in names
    ordinary = 'abstract:' if abstract_route else ''
    for phase in ('completeness', 'definedness', 'memory', 'confluence:startup'):
        assert ordinary + phase in names
    assert names.count('environment-member') == 1
    assert exact._admitted_comparison is exact._comparison
    assert exact._admitted_comparison is not stronger


def test_complete_induction_retains_original_native_comparison(monkeypatch):
    exact, abstract, solve, calls = _admission()
    stronger = _induction_attempt(monkeypatch, exact, abstract)
    observations = ((z3.Int('native_left'), z3.Int('native_right'), z3.BoolVal(True)),)
    object.__setattr__(exact._comparison, '_observations', observations)
    assert exact._admit_domain(solve, lambda: None, prove=solve) == 'proved'
    assert exact._admitted_comparison is exact._comparison
    assert exact._admitted_comparison._observations is observations
    assert stronger._observations == ()
    assert any(name.startswith('inductive:confluence:') for name, _ in calls)
    assert not any(name.startswith('abstract:') for name, _ in calls)
    membership = next(roots for name, roots in calls if name == 'environment-member')
    model = z3_check(membership, 10000).model
    assert z3.is_true(model.eval(z3.And(*exact._comparison.constraints)))
    count = len(calls)
    assert exact._admit_domain(solve, lambda: None, prove=solve) == 'reused'
    assert len(calls) == count


def test_induction_failure_does_not_hide_original_order_sensitivity(monkeypatch):
    exact, abstract, solve, calls = _admission(actual_difference=True, abstract_difference=True)
    _induction_attempt(monkeypatch, exact, abstract, difference=True)
    with pytest.raises(Refused, match='order-sensitive'):
        exact._admit_domain(solve, lambda: None, prove=solve)
    assert exact._admitted_comparison is None
    assert any(name == 'confluence:startup' for name, _ in calls)


def test_induction_cancellation_does_not_restart_or_cache(monkeypatch):
    exact, abstract, solve, calls = _admission()
    _induction_attempt(monkeypatch, exact, abstract)
    def owner(phase, assertions):
        if phase == 'inductive:confluence:product':
            raise InterruptedError('same owner deadline')
        return solve(phase, assertions)
    with pytest.raises(InterruptedError, match='same owner deadline'):
        exact._admit_domain(owner, lambda: None, prove=owner)
    assert exact._admitted_comparison is None
    assert not any(name.startswith(('abstract:', 'confluence:')) for name, _ in calls)


@pytest.mark.parametrize('abstract_route', [False, True])
def test_induction_receipt_prefix_does_not_change_backend_selection(monkeypatch, abstract_route):
    from behave.sim.core.audit import AuditCheck

    exact, abstract, solve, calls = _admission()
    if not abstract_route:
        abstract = exact._comparison
        exact.relation.abstract_order_comparison = lambda *_args, **_kwargs: None
    stronger = _induction_attempt(monkeypatch, exact, abstract)
    phases = ('confluence:slot[0]:prefix', 'confluence:monitor[1]:prefix',
              'confluence:protocol:prefix', 'confluence:product:prefix')
    audit = SourceOrderComparison.audit
    def check_routes(self, owner, **kwargs):
        if self is not stronger:
            return audit(self, owner, **kwargs)
        owner('feasibility', self.constraints)
        for phase in phases:
            owner(phase, (z3.BoolVal(False),))
        return AuditCheck.agreed()
    monkeypatch.setattr(SourceOrderComparison, 'audit', check_routes)
    general = []
    def prove(phase, assertions, **kwargs):
        general.append(phase)
        return solve(phase, assertions)
    assert exact._admit_domain(solve, lambda: None, prove=prove) == 'proved'
    expected = phases if abstract_route else phases[2:]
    assert general == ['inductive:' + phase for phase in expected]
