"""Finite-domain strategy changes neither formulas, budgets nor evidence."""

import threading

import pytest
import z3

from behave.sim.symbolic import exact_solve as solve
from behave.sim.core.query import SolverAnswer


@pytest.mark.parametrize("owned", [False, True])
def test_finite_check_matches_smt_and_preserves_all_model_coordinates(owned):
    ctx = z3.Context()
    count = z3.Int("count", ctx=ctx)
    value = z3.BitVec("value", 3, ctx=ctx)
    offer = z3.Bool("offer", ctx=ctx)
    for expected in range(-2, 8):
        assertions = (
            count >= -1, count <= 4,
            count == z3.If(offer, 1, 0) + z3.BV2Int(value), count == expected,
        )
        oracle = z3.Solver(ctx=ctx)
        oracle.add(*assertions)
        got = solve.z3_check_finite(assertions, 10_000,
                                    _owned_context=ctx if owned else None)
        assert got.answer is (SolverAnswer.SAT if oracle.check() == z3.sat
                              else SolverAnswer.UNSAT)
        if got.answer is SolverAnswer.SAT:
            assert got.model.ctx == ctx
            assert all(z3.is_true(got.model.eval(a, model_completion=True))
                       for a in assertions)


@pytest.mark.parametrize("theory", ["array", "unbounded-int", "real", "quantifier"])
def test_finite_preference_does_not_refuse_other_theories(theory):
    x = z3.Int("unbounded")
    real = z3.Real("real")
    array = z3.Array("memory", z3.IntSort(), z3.IntSort())
    terms = {
        "array": (z3.Select(z3.Store(array, 2, 7), x) == 7, x == 2),
        "unbounded-int": (x > 2 ** 128,),
        "real": (real * 3 == 1,),
        "quantifier": (z3.ForAll(x, x + 1 > x),),
    }[theory]
    got = solve.z3_check_finite(terms, 10_000)
    assert got.answer is SolverAnswer.SAT
    assert got.model.ctx == terms[0].ctx
    contradicted = solve.z3_check_finite((*terms, z3.Not(terms[0])), 10_000)
    assert contradicted.answer is SolverAnswer.UNSAT


@pytest.mark.parametrize('owned', [False, True])
def test_array_strategy_keeps_smt_without_finite_expansion(monkeypatch, owned):
    ctx = z3.Context()
    memory = z3.Array('state', z3.BitVecSort(4, ctx), z3.BitVecSort(8, ctx))
    index = z3.BitVec('index', 4, ctx)
    terms = (index == 3, z3.Select(memory, index) == 71)
    monkeypatch.setattr(z3, 'SolverFor', lambda *a, **kw: pytest.fail('finite expansion'))
    result = solve.z3_check_finite(terms, 10_000, _owned_context=ctx if owned else None,
                                   _prefer_finite=False)
    assert result.answer is SolverAnswer.SAT
    assert result.model.ctx == ctx
    assert all(z3.is_true(result.model.eval(term)) for term in terms)
    assert solve.z3_check_finite((*terms, z3.Not(terms[0])), 10_000,
                                 _prefer_finite=False).answer is SolverAnswer.UNSAT


def _fake_clock(monkeypatch):
    now = [100.0]
    timers = []

    class Timer:
        daemon = False

        def __init__(self, seconds, callback):
            self.seconds, self.callback = seconds, callback
            self.cancelled = self.joined = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

        def join(self):
            self.joined = True

    monkeypatch.setattr(solve.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(solve.threading, "Timer", Timer)
    return now, timers


@pytest.mark.parametrize("outcome", ["unsupported", "timeout", "memory", "expired", "late-sat"])
def test_fallback_keeps_every_root_and_only_the_original_budget(monkeypatch, outcome):
    now, timers = _fake_clock(monkeypatch)
    ctx = z3.Context()
    x = z3.Int("counter", ctx=ctx)
    roots = (x == 3, x > 0, x == 3)
    calls = []

    def checked(assertions, timeout_ms, solver, memory_mb, **options):
        assert assertions == roots
        assert memory_mb == 17
        assert solver.ctx == ctx
        assert options["_result_context"] is None
        calls.append(timeout_ms)
        if len(calls) == 1:
            now[0] += 1 if outcome in {"expired", "late-sat"} else .25
            cause = {"timeout": solve.ExactUnknownCause.TIME_LIMIT,
                     "memory": solve.ExactUnknownCause.MEMORY_LIMIT}.get(
                         outcome, solve.ExactUnknownCause.OTHER)
            if outcome != "late-sat":
                return solve.ExactCheckResult(SolverAnswer.UNKNOWN,
                    reason=outcome, unknown_cause=cause, solver_load_ns=2, solve_ns=3)
        solver.add(*assertions)
        assert solver.check() == z3.sat
        return solve.ExactCheckResult(SolverAnswer.SAT, model=solver.model(),
                                      solver_load_ns=5, solve_ns=7)

    monkeypatch.setattr(solve, "_z3_check", checked)
    got = solve.z3_check_finite(roots, 1000, 17, _owned_context=ctx)
    assert calls == ([1000, 750] if outcome == "unsupported" else [1000])
    assert len(timers) == 1 and timers[0].seconds == 1
    assert timers[0].cancelled and timers[0].joined
    if outcome == "unsupported":
        assert got.answer is SolverAnswer.SAT
        assert got.model.eval(x).as_long() == 3
        assert (got.solver_load_ns, got.solve_ns) == (7, 10)
    else:
        assert got.answer is SolverAnswer.UNKNOWN
        assert got.unknown_cause is (
            solve.ExactUnknownCause.MEMORY_LIMIT if outcome == "memory"
            else solve.ExactUnknownCause.TIME_LIMIT)
        assert (got.solver_load_ns, got.solve_ns) == (
            (5, 7) if outcome == "late-sat" else (2, 3))


@pytest.mark.parametrize("interrupted", [True, False])
def test_native_exceptions_are_not_silently_downgraded(monkeypatch, interrupted):
    _, timers = _fake_clock(monkeypatch)
    ctx = z3.Context()

    def failure(*args, **kwargs):
        if interrupted:
            timers[0].callback()
        raise z3.Z3Exception("native failure")

    monkeypatch.setattr(solve, "_z3_check", failure)
    if interrupted:
        result = solve.z3_check_finite((z3.BoolVal(True, ctx),), 1000,
                                       _owned_context=ctx)
        assert result.answer is SolverAnswer.UNKNOWN
        assert result.unknown_cause is solve.ExactUnknownCause.TIME_LIMIT
    else:
        with pytest.raises(z3.Z3Exception, match="native failure"):
            solve.z3_check_finite((z3.BoolVal(True, ctx),), 1000,
                                   _owned_context=ctx)
    assert timers[0].cancelled and timers[0].joined


def test_finite_owner_cannot_retain_an_expired_proof(monkeypatch):
    ctx = z3.Context()
    interrupted = threading.Event()

    class Timer:
        daemon = False

        def __init__(self, seconds, callback):
            self.callback = callback

        def start(self):
            interrupted.set()
            self.callback()

        def cancel(self):
            pass

        def join(self):
            pass

    monkeypatch.setattr(solve.threading, "Timer", Timer)
    monkeypatch.setattr(solve, "_finite_check", lambda *a, **kw: pytest.fail("late check"))
    result = solve.z3_check_finite((z3.BoolVal(False, ctx),), 1, _owned_context=ctx)
    assert interrupted.is_set()
    assert result.answer is SolverAnswer.UNKNOWN
    assert result.unknown_cause is solve.ExactUnknownCause.TIME_LIMIT


@pytest.mark.parametrize('finite', [False, True])
def test_prepared_solver_checks_changed_requests_without_retaining_bindings(finite):
    ctx = z3.Context()
    x, y = z3.BitVecs('request state', 8, ctx=ctx)
    base = (y == x + 1, y == x + 1)  # Duplicates and order are retained.
    prepared = solve.SolverPreparation(base)
    solver = None
    for value in (3, 91, 3):
        roots = (*base, x == value)
        answer = solve.z3_check_finite(roots, 1000, _owned_context=ctx,
            _preparation=prepared, _prefer_finite=finite)
        assert answer.answer is SolverAnswer.SAT
        assert answer.model.eval(y).as_long() == value + 1
        assert all(z3.is_true(answer.model.eval(term)) for term in roots)
        if solver is not None:
            assert prepared._solver is solver
        solver = prepared._solver
        assert solver.num_scopes() == 0
        # QF_FD retains an equivalent bit-blasted base, not its surface syntax.
        if not finite:
            assert tuple(solver.assertions()) == base
    answer = solve.z3_check_finite((*base, x == 3, x == 4), 1000,
        _owned_context=ctx, _preparation=prepared, _prefer_finite=finite)
    assert answer.answer is SolverAnswer.UNSAT
    assert prepared._solver.num_scopes() == 0
    assert solve.z3_check_finite((*base, x == 4), 1000, _owned_context=ctx,
        _preparation=prepared, _prefer_finite=finite).answer is SolverAnswer.SAT


@pytest.mark.parametrize('change', ['different', 'shorter', 'reordered', 'foreign'])
def test_prepared_base_cannot_be_reused_for_another_formula(change):
    ctx = z3.Context()
    x = z3.BitVec('request', 8, ctx=ctx)
    base = (x == 3, z3.UGE(x, 1))
    prepared = solve.SolverPreparation(base)
    assert solve.z3_check_finite(base, 1000, _owned_context=ctx,
        _preparation=prepared).answer is SolverAnswer.SAT
    if change == 'foreign':
        other = z3.Context()
        with pytest.raises(ValueError, match='owned context'):
            solve.z3_check_finite((z3.BoolVal(True, other),), 1000,
                _owned_context=other, _preparation=prepared)
    else:
        roots = {'different': (x == 4, x == 4),
                 'shorter': (x == 4,),
                 'reordered': (base[1], x == 4)}[change]
        result = solve.z3_check_finite(roots, 1000, _owned_context=ctx,
            _preparation=prepared)
        assert result.answer is SolverAnswer.SAT
        assert result.model.eval(x).as_long() == 4
    assert prepared._solver is None


@pytest.mark.parametrize('preserve', [False, True])
def test_prepared_smt_retains_internal_checks_across_scopes_and_extensions(preserve):
    ctx = z3.Context()
    x, y, later = z3.BitVecs('persistent_x persistent_y persistent_later', 16, ctx=ctx)
    prepared = solve.SolverPreparation((y == x + 1,))
    for index, (value, expected) in enumerate(((3, 4), (7, 9), (11, 12)), start=1):
        roots = (*prepared.base, x == value, y == expected)
        answer = solve.z3_check_finite(roots, 10_000, _owned_context=ctx,
            _preparation=prepared, _prefer_finite=False, _preserve_equations=preserve)
        assert answer.answer is (SolverAnswer.UNSAT if index == 2 else SolverAnswer.SAT)
        if answer.answer is SolverAnswer.SAT:
            assert all(z3.is_true(answer.model.eval(term, model_completion=True)) for term in roots)
        # Reusing a tactic-backed wrapper passes object-identity tests but
        # constructs a fresh internal SMT solver for each check (count == 1).
        assert prepared._solver.statistics().get_key_value('num checks') == index
        assert prepared._solver.num_scopes() == 0
        prepared.extend((*prepared.base, later == y + 2))
    prepared.clear()


@pytest.mark.parametrize('finite', [False, True])
def test_growing_preparation_retains_only_present_roots_not_query_answers(finite):
    ctx = z3.Context()
    x, y = z3.BitVecs('growing_x growing_y', 8, ctx=ctx)
    prepared = solve.SolverPreparation((z3.UGE(x, 1),))

    def check(defect):
        return solve.z3_check_finite((*prepared.base, defect), 1000,
            _owned_context=ctx, _preparation=prepared, _prefer_finite=finite)

    assert check(x == 3).answer is SolverAnswer.SAT
    original = prepared._solver
    prepared.extend((y == x + 1, *prepared.base, y == x + 1))
    # Multiple pending extensions must all be loaded under the next deadline.
    prepared.extend((*prepared.base, z3.ULE(x, 10)))
    assert check(y != x + 1).answer is SolverAnswer.UNSAT
    assert prepared._solver is original and not prepared._pending
    result = check(x == 4)
    assert result.answer is SolverAnswer.SAT and result.model.eval(y).as_long() == 5
    # Removing a root invalidates the prepared solver, not the new query.
    prepared.extend((x == 0,))
    assert prepared._solver is None
    assert check(x == 0).answer is SolverAnswer.SAT


@pytest.mark.parametrize('kind', ['foreign', 'non_boolean', 'empty'])
def test_growing_preparation_rejects_an_invalid_boundary(kind):
    ctx = z3.Context()
    prepared = solve.SolverPreparation((z3.BoolVal(True, ctx),))
    changed = {'foreign': (z3.BoolVal(True, z3.Context()),),
               'non_boolean': (z3.IntVal(1, ctx),), 'empty': ()}[kind]
    with pytest.raises(ValueError, match='owned context'):
        prepared.extend(changed)


def test_pending_extension_is_retired_with_the_solver():
    ctx = z3.Context()
    x = z3.Int('retired_extension', ctx=ctx)
    prepared = solve.SolverPreparation((x >= 0,))
    solve.z3_check_finite((*prepared.base, x == 0), 1000,
        _owned_context=ctx, _preparation=prepared, _prefer_finite=False)
    prepared.extend((*prepared.base, x == 3))
    assert prepared._pending
    prepared.clear()
    assert not prepared._pending and prepared._solver is None
    # Clearing preparation does not drop any original formula root.
    assert solve.z3_check_finite((*prepared.base, x == 4), 1000,
        _owned_context=ctx, _preparation=prepared, _prefer_finite=False).answer is SolverAnswer.UNSAT


@pytest.mark.parametrize('outcome', ['unsupported', 'timeout', 'memory', 'late', 'interrupt', 'exception'])
def test_preparation_is_discarded_on_unknown_late_or_cancelled_check(monkeypatch, outcome):
    now, timers = _fake_clock(monkeypatch)
    ctx = z3.Context()
    x = z3.BitVec('request', 8, ctx=ctx)
    base = (z3.UGE(x, 1),)
    prepared = solve.SolverPreparation(base)
    roots = (*base, x == 7)
    real_check = solve._z3_check
    seen = []

    def checked(assertions, timeout_ms, solver, memory_mb, **options):
        seen.append(tuple(assertions))
        if len(seen) > 1:
            assert assertions == roots  # Unsupported hint falls back in full.
            return real_check(assertions, timeout_ms, solver, memory_mb, **options)
        if outcome in {'late', 'interrupt'}:
            result = real_check(assertions, timeout_ms, solver, memory_mb, **options)
            if outcome == 'late':
                now[0] += 2
            else:
                timers[0].callback()
            return result
        if outcome == 'exception':
            raise RuntimeError('injected cancellation')
        cause = {'timeout': solve.ExactUnknownCause.TIME_LIMIT,
                 'memory': solve.ExactUnknownCause.MEMORY_LIMIT}.get(
                     outcome, solve.ExactUnknownCause.OTHER)
        return solve.ExactCheckResult(SolverAnswer.UNKNOWN, reason=outcome,
            unknown_cause=cause, solver_load_ns=2, solve_ns=3)

    monkeypatch.setattr(solve, '_z3_check', checked)
    if outcome == 'exception':
        with pytest.raises(RuntimeError, match='injected cancellation'):
            solve.z3_check_finite(roots, 1000, _owned_context=ctx, _preparation=prepared)
    else:
        answer = solve.z3_check_finite(roots, 1000, _owned_context=ctx, _preparation=prepared)
        assert answer.answer is (SolverAnswer.SAT if outcome == 'unsupported' else SolverAnswer.UNKNOWN)
    assert prepared._solver is None
    assert timers[0].cancelled and timers[0].joined
    monkeypatch.setattr(solve, '_z3_check', real_check)
    fresh = solve.z3_check_finite((*base, x == 8), 1000,
        _owned_context=ctx, _preparation=prepared)
    assert fresh.answer is SolverAnswer.SAT
    assert fresh.model.eval(x).as_long() == 8


def test_preparation_rebuilds_when_memory_ceiling_or_strategy_changes():
    ctx = z3.Context()
    x = z3.BitVec('request', 8, ctx=ctx)
    base = (x == 7,)
    prepared = solve.SolverPreparation(base)
    previous = None
    for finite, memory, preserve in ((True, None, False), (False, None, False),
                                     (False, 4096, False), (False, None, False),
                                     (False, None, True), (False, None, False)):
        result = solve.z3_check_finite(base, 1000, memory, _owned_context=ctx,
            _preparation=prepared, _prefer_finite=finite, _preserve_equations=preserve)
        assert result.answer is SolverAnswer.SAT
        assert prepared._solver is not previous
        previous = prepared._solver


def test_array_preparation_preserves_smt_and_dynamic_memory_requests(monkeypatch):
    ctx = z3.Context()
    index = z3.BitVec('address', 4, ctx=ctx)
    memory = z3.Array('memory', z3.BitVecSort(4, ctx), z3.BitVecSort(8, ctx))
    base = (memory == z3.Store(z3.K(index.sort(), z3.BitVecVal(0, 8, ctx)), 3, 71),)
    prepared = solve.SolverPreparation(base)
    monkeypatch.setattr(z3, 'SolverFor', lambda *a, **kw: pytest.fail('finite expansion'))
    for address, value in ((3, 71), (5, 0), (3, 72), (3, 71)):
        roots = (*base, index == address, z3.Select(memory, index) == value)
        result = solve.z3_check_finite(roots, 1000, _owned_context=ctx,
            _preparation=prepared, _prefer_finite=False)
        assert result.answer is (SolverAnswer.UNSAT if value == 72 else SolverAnswer.SAT)
        if result.answer is SolverAnswer.SAT:
            assert all(z3.is_true(result.model.eval(term)) for term in roots)


@pytest.mark.parametrize('width', [4, 16, 64])
@pytest.mark.parametrize('preserve', [False, True])
def test_prepared_smt_restores_eliminated_coordinates_across_scopes_and_extensions(width, preserve):
    ctx = z3.Context()
    word = z3.BitVecSort(width, ctx)
    x, y = z3.Consts('prepared_x prepared_y', word)
    count, successor = z3.Ints('prepared_count prepared_successor', ctx=ctx)
    memory = z3.Array('prepared_memory', word, word)
    written = z3.Array('prepared_written', word, word)
    function = z3.Function('prepared_function', word, word)
    base = (y == x + 1, successor == count + 1,
            written == z3.Store(memory, x, y), function(x) == y)
    prepared = solve.SolverPreparation(base)
    previous = None
    for value, expected in ((3, 4), (9, 10), (3, 5), (3, 4)):
        roots = (*prepared.base, x == value, count == value,
                 z3.Select(memory, x) == 8, z3.Select(written, x) == expected)
        oracle = z3.Solver(ctx=ctx)
        oracle.add(*roots)
        wanted = SolverAnswer.SAT if oracle.check() == z3.sat else SolverAnswer.UNSAT
        answer = solve.z3_check_finite(roots, 10_000, _owned_context=ctx,
            _preparation=prepared, _prefer_finite=False, _preserve_equations=preserve)
        assert answer.answer is wanted
        if wanted is SolverAnswer.SAT:
            assert all(z3.is_true(answer.model.eval(term)) for term in roots)
            assert answer.model.eval(y).as_long() == value + 1
            assert answer.model.eval(successor).as_long() == value + 1
        if previous is not None:
            assert prepared._solver is previous
        previous = prepared._solver
        assert previous.num_scopes() == 0
        prepared.extend((*prepared.base, successor >= 0))
    prepared.clear()


@pytest.mark.parametrize('width', [4, 16, 64])
def test_shared_history_restores_dead_outputs_when_later_queries_observe_them(width):
    ctx = z3.Context()
    x, y, first, second, unused = z3.BitVecs('x y first second unused', width, ctx=ctx)
    base = (first == x * y + 1, second == first + x, unused == first - y)
    prepared = solve.SolverPreparation(base)
    wrap = (1 << width) - 1
    wanted = (3 * 5 + 1 + 3) & wrap
    for target in (z3.BoolVal(True, ctx),
                   z3.And(x == 3, y == 5, second == wanted),
                   z3.And(x == 3, y == 5, second != wanted),
                   z3.And(x == 3, y == 5, unused == ((3 * 5 + 1 - 5) & wrap))):
        roots = (*prepared.base, target)
        oracle = z3.Solver(ctx=ctx)
        oracle.add(*roots)
        expected = SolverAnswer.SAT if oracle.check() == z3.sat else SolverAnswer.UNSAT
        answer = solve.z3_check_finite(roots, 10_000, _owned_context=ctx,
            _preparation=prepared, _prefer_finite=False, _preserve_equations=True)
        assert answer.answer is expected
        if expected is SolverAnswer.SAT:
            assert all(z3.is_true(answer.model.eval(term, model_completion=True))
                       for term in roots)
        assert prepared._solver.num_scopes() == 0
        # A formerly unconstrained coordinate can become observable in a later
        # base extension. Neither eliminated values nor old targets are facts.
        prepared.extend((*base, unused != 0))
    prepared.clear()


@pytest.mark.parametrize('incremental', [False, True])
def test_shared_rewrites_keep_wrapping_arithmetic_and_original_models(incremental):
    ctx = z3.Context()
    x, y, carry, output = z3.BitVecs('x y carry output', 8, ctx=ctx)
    roots = (carry == x + y, output == carry + x,
             z3.Extract(7, 4, carry) == 0)
    solver = solve._smt_solver(ctx, preserve_equations=True, incremental=incremental)
    solver.add(*roots, x == 255, y == 2)
    for expected in (0, 1):
        solver.push()
        solver.add(output == expected)
        assert solver.check() == (z3.sat if expected == 0 else z3.unsat)
        if expected == 0:
            model = solver.model()
            assert all(z3.is_true(model.eval(root, model_completion=True)) for root in roots)
        solver.pop()


@pytest.mark.parametrize('failure', (None, 'solver.base-load', 'solver.base-extend', 'solver.push'))
def test_prepared_solver_reports_precheck_failure_stage(monkeypatch, failure):
    from behave.sim.symbolic.diagnostics import diagnostic_scope

    ctx = z3.Context()
    x = z3.Int('stage_preparation', ctx=ctx)
    prepared = solve.SolverPreparation((x >= 0,))
    events = []
    original = solve._smt_solver

    class ObservedSolver:
        def __init__(self, context, **options):
            self.inner = original(context, **options)

        def add(self, *roots):
            if failure == events[-1]['stage'][-1]:
                raise RuntimeError('precheck failed')
            return self.inner.add(*roots)

        def push(self):
            if failure == 'solver.push':
                raise RuntimeError('precheck failed')
            return self.inner.push()

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(solve, '_smt_solver', ObservedSolver)
    def run():
        for value in (1, 2):
            result = solve.z3_check_finite((*prepared.base, x == value), 1000,
                _owned_context=ctx, _preparation=prepared, _prefer_finite=False)
            assert result.answer is SolverAnswer.SAT
            prepared.extend((*prepared.base, x < 10))

    with diagnostic_scope(events.append):
        if failure:
            with pytest.raises(RuntimeError, match='precheck failed'):
                run()
        else:
            run()
    if failure:
        assert events[-1]['stage'][-1] == failure
        assert events[-1]['event'] == 'raised'
        assert prepared._solver is None
    else:
        completed = [e['stage'][-1] for e in events if e['event'] == 'returned']
        assert completed.count('solver.base-load') == 1
        assert completed.count('solver.base-extend') == 1
        assert completed.count('solver.push') == 2


def test_prepared_solver_has_current_limits_before_base_loading_and_push(monkeypatch):
    ctx = z3.Context()
    x = z3.BitVec('limited_preparation', 8, ctx=ctx)
    prepared = solve.SolverPreparation((x == 3,))
    original = solve._smt_solver
    limits, pushes = {}, []

    class ObservedSolver:
        def __init__(self, context, **options):
            self.inner = original(context, **options)

        def set(self, key, value):
            limits[key] = value
            return self.inner.set(key, value)

        def add(self, *roots):
            assert 0 < limits['timeout'] <= 2000
            assert limits['max_memory'] == 4096
            return self.inner.add(*roots)

        def push(self):
            pushes.append(dict(limits))
            return self.inner.push()

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(solve, '_smt_solver', ObservedSolver)
    for timeout in (2000, 1000):
        answer = solve.z3_check_finite(prepared.base, timeout, 4096,
            _owned_context=ctx, _preparation=prepared, _prefer_finite=False)
        assert answer.answer is SolverAnswer.SAT
        assert 0 < pushes[-1]['timeout'] <= timeout
    assert len(pushes) == 2


@pytest.mark.parametrize('message', ['out of memory', "b'out of memory'", 'max. memory exceeded'])
def test_preparation_memory_exception_is_unknown_not_a_source_refusal(monkeypatch, message):
    ctx = z3.Context()
    prepared = solve.SolverPreparation((z3.BoolVal(True, ctx),))

    def fail(*args, **kwargs):
        raise z3.Z3Exception(message)

    monkeypatch.setattr(prepared, 'check', fail)
    result = solve.z3_check_finite(prepared.base, 1000, _owned_context=ctx,
        _preparation=prepared, _prefer_finite=False)
    assert result.answer is SolverAnswer.UNKNOWN
    assert result.unknown_cause is solve.ExactUnknownCause.MEMORY_LIMIT
    assert result.model is None and prepared._solver is None


def test_unrelated_preparation_exception_is_not_hidden_as_unknown(monkeypatch):
    ctx = z3.Context()
    prepared = solve.SolverPreparation((z3.BoolVal(True, ctx),))

    def fail(*args, **kwargs):
        raise z3.Z3Exception('invalid solver parameter')

    monkeypatch.setattr(prepared, 'check', fail)
    with pytest.raises(z3.Z3Exception, match='invalid solver parameter'):
        solve.z3_check_finite(prepared.base, 1000, _owned_context=ctx,
            _preparation=prepared, _prefer_finite=False)


@pytest.mark.parametrize('outcome', ['sat', 'unsat', 'expired', 'interrupt', 'capacity', 'unrelated'])
@pytest.mark.parametrize('capacity_error', ['Overflow encountered when expanding vector', "b'table overflow'"])
def test_prepared_capacity_failure_retires_arena_and_checks_complete_roots(monkeypatch, outcome, capacity_error):
    import weakref

    now, timers = _fake_clock(monkeypatch)
    ctx = z3.Context()
    x, y = z3.BitVecs('capacity_x capacity_y', 8, ctx=ctx)
    base = (y == x + 1,)
    prepared = solve.SolverPreparation(base)
    roots = (*base, x == 3, y == (5 if outcome == 'unsat' else 4), x == 3)
    factory, checked = solve._smt_solver, solve._z3_check
    arenas, cold_calls = [], []

    class Exhausted:
        def set(self, *args, **options):
            pass

        def add(self, *terms):
            assert len(terms) == len(base) and all(a.eq(b) for a, b in zip(terms, base))

        def push(self):
            now[0] += 2 if outcome == 'expired' else 0.25
            if outcome == 'interrupt':
                timers[0].callback()
            raise z3.Z3Exception(capacity_error)

    def make(context, *, preserve_equations=False, incremental=False):
        if incremental:
            value = Exhausted()
            arenas.append(weakref.ref(value))
            return value
        assert arenas and all(ref() is None for ref in arenas)
        return factory(context, preserve_equations=preserve_equations)

    def check(terms, timeout, solver, memory, **options):
        assert solver.ctx != ctx
        assert options['_result_context'] == ctx
        cold_calls.append(tuple(term.translate(ctx) for term in terms))
        assert 0 < timeout <= (750 if len(cold_calls) == 1 else 1000)
        assert memory == 4096
        if outcome in ('capacity', 'unrelated'):
            raise z3.Z3Exception(capacity_error
                                if outcome == 'capacity' else 'invalid solver parameter')
        return checked(terms, timeout, solver, memory, **options)

    monkeypatch.setattr(solve, '_smt_solver', make)
    monkeypatch.setattr(solve, '_z3_check', check)

    def run(terms):
        return solve.z3_check_finite(terms, 1000, 4096, _owned_context=ctx,
            _preparation=prepared, _prefer_finite=False, _preserve_equations=True)

    if outcome == 'unrelated':
        with pytest.raises(z3.Z3Exception, match='invalid solver parameter'):
            run(roots)
    else:
        result = run(roots)
        expected = {'sat': SolverAnswer.SAT, 'unsat': SolverAnswer.UNSAT}.get(
            outcome, SolverAnswer.UNKNOWN)
        assert result.answer is expected
        if outcome == 'sat':
            assert all(z3.is_true(result.model.eval(term)) for term in roots)
        if outcome in ('expired', 'interrupt'):
            assert result.unknown_cause is solve.ExactUnknownCause.TIME_LIMIT
        if outcome == 'capacity':
            assert result.unknown_cause is solve.ExactUnknownCause.OTHER
            assert result.model is None
    assert prepared._solver is None and prepared._cold_only
    assert all(ref() is None for ref in arenas)
    assert bool(cold_calls) == (outcome not in ('expired', 'interrupt'))
    if cold_calls:
        assert len(cold_calls[0]) == len(roots)
        assert all(a.eq(b) for a, b in zip(cold_calls[0], roots))
    if outcome in ('sat', 'unsat'):
        prepared.extend((*base, x == 6))
        later = (*prepared.base, y == 7)
        assert run(later).answer is SolverAnswer.SAT
        assert len(arenas) == 1 and len(cold_calls) == 2
    assert all(timer.cancelled and timer.joined for timer in timers)


@pytest.mark.parametrize('satisfiable', [False, True])
@pytest.mark.parametrize('failure', ['retired', 'raised', 'unknown'])
def test_capacity_retirement_does_not_reuse_the_original_native_context(monkeypatch, satisfiable, failure):
    ctx = z3.Context()
    x, y = z3.BitVecs('retired_x retired_y', 16, ctx=ctx)
    roots = (y == x * x, x == 7, y == (49 if satisfiable else 48), x == 7)
    prepared = solve.SolverPreparation(roots[:1])
    prepared._cold_only = failure == 'retired'
    original = solve._z3_check
    contexts = []

    def check(assertions, timeout, solver, memory, **options):
        if solver.ctx == ctx and failure != 'retired':
            if failure == 'raised':
                raise z3.Z3Exception('table overflow')
            return solve.ExactCheckResult(SolverAnswer.UNKNOWN, reason='table overflow',
                unknown_cause=solve.ExactUnknownCause.OTHER)
        contexts.append(solver.ctx)
        assert solver.ctx != ctx, 'a retired arena must not receive another solve'
        assert len(assertions) == len(roots)
        assert all(a.translate(ctx).eq(b) for a, b in zip(assertions, roots))
        return original(assertions, timeout, solver, memory, **options)

    monkeypatch.setattr(solve, '_z3_check', check)
    for _ in range(2):
        answer = solve.z3_check_finite(roots, 1000, _owned_context=ctx,
            _preparation=prepared, _prefer_finite=False, _preserve_equations=True)
        assert answer.answer is (SolverAnswer.SAT if satisfiable else SolverAnswer.UNSAT)
        if satisfiable:
            assert answer.model.ctx == ctx
            assert all(z3.is_true(answer.model.eval(term)) for term in roots)
    assert contexts[0] != contexts[1]


def test_interrupt_during_watchdog_join_cannot_publish_sat_or_retain_preparation(monkeypatch):
    _, timers = _fake_clock(monkeypatch)
    ctx = z3.Context()
    x = z3.BitVec('request', 8, ctx=ctx)
    prepared = solve.SolverPreparation((x == 3,))
    real_check = solve._z3_check

    def checked(*args, **kwargs):
        result = real_check(*args, **kwargs)
        def join():
            if not timers[0].joined:
                timers[0].callback()
            timers[0].joined = True
        timers[0].join = join
        return result

    monkeypatch.setattr(solve, '_z3_check', checked)
    answer = solve.z3_check_finite(prepared.base, 1000, _owned_context=ctx,
        _preparation=prepared)
    assert answer.answer is SolverAnswer.UNKNOWN
    assert answer.unknown_cause is solve.ExactUnknownCause.TIME_LIMIT
    assert prepared._solver is None and prepared._interrupted
