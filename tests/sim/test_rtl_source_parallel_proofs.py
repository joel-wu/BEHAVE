"""Speculative conditional answers become evidence only in dependency order."""

from threading import Event, Lock, get_ident
from types import SimpleNamespace
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import gc
import weakref

import pytest
import z3

from behave.sim import api
from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check, z3_check_finite
from behave.sim.analysis_budget import AnalysisStopped, analysis_scope, check_analysis
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.query import SolverAnswer
from behave.sim.cpu_admission import reserve_cpus
from behave.sim.rtl.source_confluence import SourceOrderComparison
from behave.sim.rtl.source_proofs import order_proof_scope
from behave.sim.rtl.source_proofs import _OrderProofs

pytestmark = pytest.mark.solver


@pytest.mark.parametrize('bad', [False, True])
def test_product_field_name_cannot_impersonate_a_relaxed_proof(monkeypatch, bad):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    pair = _pair({'startup.x': z3.BoolVal(False),
                  'transactions.hits[0].custom:prefix': z3.BoolVal(bad)})
    serial = pair.audit(_solve, prefix_proofs=True)
    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint):
        with order_proof_scope(pair, _solve, lambda: None) as parallel:
            actual = pair.audit(parallel, prefix_proofs=True)
        assert pool.snapshot()['reserved'] == 0
    assert actual.as_dict() == serial.as_dict()
    assert (actual.status is AuditCheckStatus.AGREED) is not bad


@pytest.mark.parametrize('prefix_sat', [False, True])
def test_full_history_is_translated_only_after_a_sat_prefix(monkeypatch, prefix_sat):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    x = z3.Bool('prefix_needs_complete_domain')
    pair = _pair({'startup.x': x if prefix_sat else z3.BoolVal(False),
                  'slot[0].phase[0].x': z3.BoolVal(False)}, (z3.Not(x),))
    translated = []
    original = _OrderProofs._translate

    def translate(self, context, jobs):
        translated.extend(label for label, _ in jobs)
        return original(self, context, jobs)

    monkeypatch.setattr(_OrderProofs, '_translate', translate)
    expected = pair.audit(_solve, prefix_proofs=True)
    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint):
        with order_proof_scope(pair, _solve, lambda: None) as parallel:
            actual = pair.audit(parallel, prefix_proofs=True)
        assert pool.snapshot()['reserved'] == 0
    assert actual.as_dict() == expected.as_dict()
    assert actual.status is AuditCheckStatus.AGREED
    assert translated.count('confluence:startup:prefix') == 1
    assert translated.count('confluence:startup') == int(prefix_sat)
    if prefix_sat:
        assert translated.index('confluence:startup:prefix') < translated.index('confluence:startup')


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('head', ['unsat', 'unknown', 'error'])
@pytest.mark.parametrize('bad', [False, True])
def test_ready_fallback_uses_idle_lane_without_early_consumption(monkeypatch, private, head, bad):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    ctx = z3.Context() if private else z3.main_ctx()
    x = z3.BitVec('prefetched_input', 8, ctx=ctx)
    first, second = 'confluence:startup', 'confluence:slot[0]'
    jobs = [
        (first, ((first + ':prefix', (z3.BoolVal(False, ctx=ctx),)),)),
        (second, ((second + ':prefix', (x == 3,)),
                  (second, (x == 3, x == (3 if bad else 4))))),
    ]
    pair = SimpleNamespace(left=SimpleNamespace(alive=z3.BoolVal(True, ctx=ctx)),
        _local_source_steps=False, order_queries=lambda **kwargs: iter(jobs))
    fallback_done, lock, active, calls = Event(), Lock(), set(), []
    peak = 0
    original_translate = z3.ModelRef.translate

    def translate(model, target):
        with lock:
            assert model.ctx not in active, 'model translation raced its fallback solver'
        return original_translate(model, target)

    monkeypatch.setattr(z3.ModelRef, 'translate', translate)

    def solve(label, roots, **kwargs):
        nonlocal peak
        context = kwargs['context']
        with lock:
            assert context not in active
            active.add(context)
            peak = max(peak, len(active))
            calls.append(label)
        try:
            if label == first + ':prefix':
                assert fallback_done.wait(5), 'ready fallback waited for the blocked head'
                if head == 'error':
                    raise TimeoutError('head proof failed')
                if head == 'unknown':
                    return ExactCheckResult(SolverAnswer.UNKNOWN, reason='head proof stopped')
            if label == second and head == 'error':
                fallback_done.set()
                raise RuntimeError('unconsumed fallback error must not replace the head error')
            answer = _solve(label, roots, **kwargs)
            if label == second:
                fallback_done.set()
            return answer
        finally:
            with lock:
                active.remove(context)

    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint), ExitStack() as resources:
        runner = _OrderProofs(pair, solve, lambda: None, 2, resources)
        try:
            if head == 'error':
                with pytest.raises(TimeoutError, match='head proof failed'):
                    runner(first + ':prefix', jobs[0][1][0][1])
            else:
                result = runner(first + ':prefix', jobs[0][1][0][1])
                assert result.answer is (SolverAnswer.UNKNOWN if head == 'unknown' else SolverAnswer.UNSAT)
                assert second in runner.prefetched  # Executed, not consumed/certified.
                if head == 'unsat':
                    assert runner(second + ':prefix', jobs[1][1][0][1]).answer is SolverAnswer.SAT
                    full = runner(second, jobs[1][1][1][1])
                    assert full.answer is (SolverAnswer.SAT if bad else SolverAnswer.UNSAT)
                    if bad:
                        assert all(z3.is_true(full.model.eval(term)) for term in jobs[1][1][1][1])
        finally:
            runner.close()
            resources.close()
        assert not runner.prefetched and not runner.pending and not active
        assert peak == 2 and calls.count(second) == 1
        assert pool.snapshot()['reserved'] == 0 and not pool.snapshot()['quarantined']


def test_pending_proof_owner_retains_translated_roots_after_worker_returns():
    references = []
    pair = _pair({'startup.x': z3.BoolVal(False),
                  'slot[0].phase[0].x': z3.BoolVal(False)})

    def solve(phase, roots, **kwargs):
        references.append(weakref.ref(roots[0]))
        return ExactCheckResult(SolverAnswer.UNSAT)

    with ExitStack() as resources:
        runner = _OrderProofs(pair, solve, lambda: None, 2, resources)
        runner.pool = ThreadPoolExecutor(max_workers=2)
        try:
            runner._fill()
            for pending in runner.pending:
                pending[-1].result(timeout=5)
            # Join the actual threads, not only the Future notification. The
            # owner must still retain roots used by a later model translation.
            runner.pool.shutdown(wait=True)
            gc.collect()
            assert references and all(reference() is not None for reference in references)
        finally:
            runner.close()


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('case', ['empty', 'duplicates', 'contradiction', 'array'])
def test_packed_translation_preserves_every_root_and_model(private, case):
    context = z3.Context() if private else z3.main_ctx()
    x = z3.BitVec('packed_x', 8, ctx=context)
    memory = z3.Array('packed_memory', x.sort(), x.sort())
    roots = {
        'empty': (),
        'duplicates': (x == 19, x == 19, x != 20),
        'contradiction': (x == 19, x == 20),
        'array': (x == 19, z3.Select(memory, x) == 31,
                  z3.Select(z3.Store(memory, x, x + 1), x) == 20),
    }[case]
    pair = _pair({'startup.x': z3.BoolVal(False, ctx=context)})
    with ExitStack() as resources:
        runner = _OrderProofs(pair, _solve, lambda: None, 2, resources)
        target = z3.Context()
        bound = runner._translate(target, (('query', roots),))
    assert len(bound[0][1]) == 1
    transferred = bound[0][1][0]
    # Check exact original syntax, including ordering and duplicate roots.
    expected = z3.And(*roots) if roots else z3.BoolVal(True, ctx=context)
    assert transferred.ctx == target
    assert transferred.translate(context).eq(expected)
    answer = z3_check(bound[0][1], 1000, _owned_context=target)
    if case == 'contradiction':
        assert answer.answer is SolverAnswer.UNSAT
    else:
        assert answer.answer is SolverAnswer.SAT
        model = answer.model.translate(context)
        assert all(z3.is_true(model.eval(term, model_completion=False)) for term in roots)


def test_packed_translation_honors_cancellation():
    pair = _pair({'startup.x': z3.BoolVal(False)})
    def cancel():
        raise InterruptedError('cancel packed proof')
    with ExitStack() as resources:
        runner = _OrderProofs(pair, _solve, cancel, 2, resources)
        with pytest.raises(InterruptedError, match='cancel packed proof'):
            runner._translate(z3.Context(), (('query', pair.constraints),))


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('case', ['false', 'single', 'sat', 'unsat'])
def test_nested_prefix_packets_preserve_atoms_and_solver_preparation(private, case):
    context = z3.Context() if private else z3.main_ctx()
    x = z3.BitVec('nested_prefix_x', 8, ctx=context)
    term = {'false': z3.BoolVal(False, ctx=context), 'single': x == 19,
            'sat': z3.And(x == 19, x != 20),
            'unsat': z3.And(x == 19, x == 20)}[case]
    pair = _pair({'startup.x': z3.BoolVal(False, ctx=context)})
    phase, seen = 'confluence:slot[0]:prefix', []

    def solve(label, roots, **kwargs):
        seen.append(kwargs.get('preparation'))
        return _solve(label, roots, **kwargs)

    with ExitStack() as resources:
        runner = _OrderProofs(pair, solve, lambda: None, 1, resources)
        target = z3.Context()
        bound = runner._translate(target, ((phase, (z3.And(term),)),))
        answer = runner._physical(target, bound)[phase]
        runner.close()
    assert answer.answer is (SolverAnswer.UNSAT if case in {'false', 'unsat'} else SolverAnswer.SAT)
    assert (seen[0] is not None) is (case in {'sat', 'unsat'})


@pytest.mark.parametrize('faulty', [False, True])
@pytest.mark.parametrize('product_fields', [False, True])
def test_parallel_array_proofs_match_serial_with_private_contexts(monkeypatch, faulty, product_fields):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    context = z3.Context()
    memory = z3.Array('memory', z3.BitVecSort(4, ctx=context), z3.BitVecSort(16, ctx=context))
    address, other = z3.BitVecs('address other', 4, ctx=context)
    value = z3.BitVec('value', 16, ctx=context)
    differences = {}
    for index in range(6):
        expected = z3.If(address == other,
                         value ^ int(faulty and index == 3), z3.Select(memory, other))
        name = (f'memory.responses.channel[{index}].payload[0]' if product_fields
                else f'slot[0].phase[{index}].memory')
        differences[name] = (
            z3.Select(z3.Store(memory, address, value), other) != expected)
    pair = _pair(differences)
    phase = pair.left.source.slots[0].phases[0]
    pair.left.source.slots[0].phases = (phase,) * 6
    serial = pair.audit(_solve, prefix_proofs=True)
    with api.CpuPool(4) as pool, api.cpu_scope(pool.endpoint):
        with order_proof_scope(pair, _solve, lambda: None) as parallel:
            actual = pair.audit(parallel, prefix_proofs=True)
        assert pool.snapshot()['reserved'] == 0
    assert actual.as_dict() == serial.as_dict()
    assert (actual.status is AuditCheckStatus.AGREED) is not faulty


def _pair(differences, constraints=(), fault=None):
    ctx = next(iter(differences.values())).ctx
    constraints = constraints or (z3.BoolVal(True, ctx=ctx),)
    side = SimpleNamespace(alive=z3.BoolVal(True, ctx=ctx),
        unfinished=z3.BoolVal(False, ctx=ctx), constraints=constraints,
        domain_faults={} if fault is None else {'bad': fault},
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=()),
            slots=(SimpleNamespace(phases=(SimpleNamespace(constraints=constraints),)),)))
    return SourceOrderComparison(side, side, differences)


def _solve(phase, roots, *, context=None, preparation=None):
    return z3_check_finite(roots, 10_000, _owned_context=context or roots[0].ctx,
                          _prefer_finite=False, _preparation=preparation)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('bad', [False, True])
def test_parallel_answers_match_serial_and_release_cpu(monkeypatch, private, bad):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    ctx = z3.Context() if private else None
    x = z3.Bool('x', ctx=ctx)
    pair = _pair({'startup.x': x,
                  'slot[0].phase[0].x': z3.BoolVal(bad, ctx=ctx),
                  'terminal_unresolved.x': z3.BoolVal(False, ctx=ctx)}, (z3.Not(x),))
    calls = []

    def solve(phase, roots, **kwargs):
        calls.append((phase, roots[0].ctx, get_ident()))
        return _solve(phase, roots, **kwargs)

    serial = pair.audit(_solve, prefix_proofs=True)
    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint) as usage:
        with order_proof_scope(pair, solve, lambda: None) as parallel:
            actual = pair.audit(parallel, prefix_proofs=True)
        assert pool.snapshot()['reserved'] == 0
    assert actual.as_dict() == serial.as_dict()
    concurrent = [(context, tid) for phase, context, tid in calls if phase.startswith('confluence:')]
    assert all(context != pair.left.alive.ctx and tid != get_ident() for context, tid in concurrent)
    assert usage.as_dict()['reservations'][0]['workers'] == 2
    assert usage.as_dict()['reservations'][0]['released'] is True
    assert any(phase.endswith(':prefix') for phase, *_ in calls)
    assert any(phase == 'confluence:startup' for phase, *_ in calls)


def test_later_conditional_unsat_cannot_hide_earlier_counterexample(monkeypatch):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    x = z3.Bool('order_sensitive')
    pair = _pair({'startup.x': x, 'slot[0].phase[0].x': x})
    later_finished = Event()
    consumed, answers = [], {}

    def solve(phase, roots, **kwargs):
        if phase.removesuffix(':prefix') == 'confluence:startup':
            assert later_finished.wait(5)
        answer = _solve(phase, roots, **kwargs)
        answers[phase] = answer.answer
        if phase.removesuffix(':prefix') == 'confluence:slot[0]':
            later_finished.set()
        return answer

    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint):
        with order_proof_scope(pair, solve, lambda: None) as run:
            def consume(phase, roots):
                result = run(phase, roots)
                consumed.append(phase)
                return result
            audit = pair.audit(consume, prefix_proofs=True)
    assert answers['confluence:slot[0]'] is SolverAnswer.UNSAT
    assert audit.kind == 'rtl-source-order-sensitive'
    assert consumed[-1] == 'confluence:startup'
    assert 'confluence:slot[0]' not in consumed


@pytest.mark.parametrize('failure', ['unknown', 'exception'])
def test_early_stop_joins_speculative_workers(monkeypatch, failure):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    pair = _pair({'startup.x': z3.BoolVal(False),
                  'slot[0].phase[0].x': z3.BoolVal(False)})
    active, exited = Event(), Event()

    def solve(phase, roots, **kwargs):
        if phase == 'confluence:slot[0]':
            active.set()
            try:
                return _solve(phase, roots, **kwargs)
            finally:
                exited.set()
        if phase.removesuffix(':prefix') == 'confluence:startup':
            assert active.wait(5)
            if failure == 'exception':
                raise TimeoutError('physical solver exception')
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='physical solver stopped')
        return _solve(phase, roots, **kwargs)

    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint) as usage:
        def audit():
            with order_proof_scope(pair, solve, lambda: None) as run:
                return pair.audit(run, prefix_proofs=True)
        if failure == 'exception':
            with pytest.raises(TimeoutError, match='physical solver exception'):
                audit()
        else:
            assert audit().kind == 'rtl-source-domain-check-unresolved'
        assert exited.is_set() and pool.snapshot()['reserved'] == 0
        assert not pool.snapshot()['quarantined']
    assert usage.as_dict()['reservations'][0]['released'] is True


def test_definedness_failure_never_starts_parallel_proofs(monkeypatch):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    pair = _pair({'startup.x': z3.BoolVal(False)}, fault=z3.BoolVal(True))
    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint) as usage:
        with order_proof_scope(pair, _solve, lambda: None) as run:
            assert pair.audit(run, prefix_proofs=True).kind == 'rtl-source-operation-domain-unencoded'
    assert usage.as_dict()['reservations'] == []


def test_nested_cpu_owner_forces_serial_and_preserves_semantics(monkeypatch):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    pair = _pair({'startup.x': z3.BoolVal(False)})
    calls = []

    def solve(phase, roots, **kwargs):
        calls.append((get_ident(), kwargs))
        return _solve(phase, roots, **kwargs)

    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint) as usage, reserve_cpus(2):
        with order_proof_scope(pair, solve, lambda: None) as run:
            assert pair.audit(run, prefix_proofs=True).status is AuditCheckStatus.AGREED
    assert all(tid == get_ident() and not kwargs for tid, kwargs in calls)
    assert len(usage.as_dict()['reservations']) == 1


def test_parallel_workers_share_deadline_and_bounded_window(monkeypatch):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    pair = _pair({f'slot[{i}].phase[0].x': z3.BoolVal(False) for i in range(10)})
    entered, exited = [], []
    tick = Event()

    def solve(phase, roots, *, context=None, preparation=None):
        if context is None:
            return _solve(phase, roots)
        entered.append(phase)
        try:
            while True:
                check_analysis()  # The executor must propagate this owner's scope.
                tick.wait(.01)
        finally:
            exited.append(phase)

    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint) as usage:
        with pytest.raises(AnalysisStopped), analysis_scope(1):
            with order_proof_scope(pair, solve, check_analysis) as run:
                pair.audit(run, prefix_proofs=True)
        assert len(entered) == 2 and sorted(entered) == sorted(exited)
        assert pool.snapshot()['reserved'] == 0 and not pool.snapshot()['quarantined']
    assert usage.as_dict()['reservations'][0]['released'] is True


def test_source_prefix_workers_reuse_preparation_without_reusing_answers(monkeypatch):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    x = z3.Int('growing_proof_input')
    pair = _pair({f'slot[{i}].phase[0].x': x != x for i in range(6)})
    side = pair.left
    definitions = tuple(x <= i + 10 for i in range(7))
    side.constraints = (x >= 0, *definitions)
    side.source.slots = tuple(SimpleNamespace(phases=(SimpleNamespace(constraints=(term,)),))
                              for term in definitions)
    seen = {}

    def solve(phase, roots, *, context=None, preparation=None):
        answer = _solve(phase, roots, context=context, preparation=preparation)
        if preparation is not None:
            seen.setdefault(context, []).append((preparation, preparation._solver))
        return answer

    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint):
        with order_proof_scope(pair, solve, lambda: None) as run:
            assert pair.audit(run, prefix_proofs=True).status is AuditCheckStatus.AGREED
        assert pool.snapshot()['reserved'] == 0
    assert len(seen) == 2 and sum(map(len, seen.values())) == 6
    for lane in seen.values():
        assert len(lane) == 3
        assert all(preparation is lane[0][0] and solver is lane[0][1]
                   for preparation, solver in lane)


@pytest.mark.parametrize('local', [False, True])
def test_source_preparation_retires_before_non_source_contexts_are_reused(monkeypatch, local):
    monkeypatch.delenv('BEHAVE_JOB_CPU_LIMIT', raising=False)
    x = z3.Int('retired_source_input')
    false = z3.BoolVal(False)
    pair = _pair({**{f'slot[{i}].phase[0].x': false for i in range(6)},
                  'protocol.accepted[0].in': false, 'monitor.states[0].x': false,
                  'monitor.states[1].x': false, 'terminal.x': false})
    side = pair.left
    definitions = tuple(x <= i + 10 for i in range(7))
    side.constraints = (x >= 0, *definitions)
    side.monitor_constraints, side.monitor_frame_sizes = (), (1,)
    side.source.memory.arrays = {} if local else {'ram': object()}
    side.source.slots = tuple(SimpleNamespace(phases=(SimpleNamespace(constraints=(term,)),))
                              for term in definitions)
    source, other, preparations = set(), {}, []

    def solve(phase, roots, *, context=None, preparation=None):
        if preparation is not None:
            source.add(context)
            preparations.append(weakref.ref(preparation))
        elif context is not None:
            other.setdefault(phase.removesuffix(':prefix'), context)
        return _solve(phase, roots, context=context, preparation=preparation)

    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint):
        with order_proof_scope(pair, solve, lambda: None) as run:
            assert pair.audit(run, prefix_proofs=True).status is AuditCheckStatus.AGREED
            assert not run.preparations and not run.idle
        assert pool.snapshot()['reserved'] == 0
    gc.collect()
    assert len(source) == (0 if local else 2)
    assert len(preparations) == (0 if local else 6)
    assert all(reference() is None for reference in preparations)
    assert len(other) == len(set(other.values())) == (10 if local else 4)
    assert not source.intersection(other.values())


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('unknown', [False, True])
@pytest.mark.parametrize('overlapping', [False, True])
def test_local_prefix_reuses_only_consumed_unsat_with_complete_renamed_roots(private, unknown, overlapping):
    context = z3.Context() if private else z3.main_ctx()
    rows, calls = [], []
    shared = [z3.BitVec(f'overlapping_proof_{i}', 8, ctx=context) for i in range(3)]
    for index in range(8):
        x = z3.BitVec(f'local_proof_{index}', 8, ctx=context)
        # The last obligation is SAT: a different literal cannot reuse UNSAT.
        roots = (x == 3, x != (4 if index == 7 else 3))
        if overlapping:
            x, y = shared[index % 3], shared[(index + 1) % 3]
            roots = (x == y, x != (y + 1 if index == 7 else y))
        label = f'confluence:slot[{index}]'
        rows.append((label, ((label + ':prefix', roots), (label, roots))))
    pair = SimpleNamespace(left=SimpleNamespace(alive=z3.BoolVal(True, ctx=context)),
        _local_source_steps=True, order_queries=lambda **kwargs: iter(rows))

    def solve(label, roots, *, context=None, preparation=None):
        assert preparation is None
        calls.append(label)
        return (ExactCheckResult(SolverAnswer.UNKNOWN, reason='fixture') if unknown
                else _solve(label, roots, context=context))

    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint), ExitStack() as resources:
        runner = _OrderProofs(pair, solve, lambda: None, 2, resources)
        try:
            for label, jobs in rows:
                result = runner(label + ':prefix', jobs[0][1])
                expected = SolverAnswer.UNKNOWN if unknown else (
                    SolverAnswer.SAT if label == rows[-1][0] else SolverAnswer.UNSAT)
                assert result.answer is expected
                if result.answer is SolverAnswer.SAT:
                    assert runner(label, jobs[1][1]).answer is SolverAnswer.SAT
            # Only the initially scheduled pair and the changed last query
            # require actual solves. UNKNOWN supplies no reusable theorem.
            assert len(calls) == (8 if unknown else 4)
            assert not runner.preparations and not runner.idle
        finally:
            runner.close()
        assert not runner.proved_prefixes and not runner.reused_prefixes
    assert pool.snapshot()['reserved'] == 0


@pytest.mark.parametrize('collision', [False, True])
@pytest.mark.parametrize('capacity', [0, 1, 256])
def test_local_prefix_reuse_preserves_aliases_and_every_premise(monkeypatch, collision, capacity):
    monkeypatch.setattr('behave.sim.rtl.source_proofs.defaults.SIM_ANALYSIS_ANSWER_CACHE_ENTRIES', capacity)
    if collision:
        monkeypatch.setattr('behave.sim.rtl.source_proofs._proof_shape', lambda *args, **kwargs: 0)
    x, y, z = z3.BitVecs('reuse_alias_x reuse_alias_y reuse_alias_z', 8)
    roots = ((x == y, x != y), (x == y, x != z), (x != y,))
    rows = [(f'confluence:slot[{i}]', ((f'confluence:slot[{i}]:prefix', row),
                                     (f'confluence:slot[{i}]', row)))
            for i, row in enumerate(roots)]
    pair = SimpleNamespace(left=SimpleNamespace(alive=z3.BoolVal(True)),
        _local_source_steps=True, order_queries=lambda **kwargs: iter(rows))
    calls = []

    def solve(label, roots, **kwargs):
        calls.append(label)
        return _solve(label, roots, **kwargs)

    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint), ExitStack() as resources:
        runner = _OrderProofs(pair, solve, lambda: None, 1, resources)
        try:
            for i, (label, jobs) in enumerate(rows):
                answer = runner(label + ':prefix', jobs[0][1])
                assert len(runner.proved_prefixes) <= capacity
                assert answer.answer is (SolverAnswer.UNSAT if i == 0 else SolverAnswer.SAT)
                if i:
                    assert runner(label, jobs[1][1]).answer is SolverAnswer.SAT
            assert len(calls) == 5
        finally:
            runner.close()


@pytest.mark.parametrize('equivalent', [False, True])
def test_colliding_proof_cache_compares_at_most_one_candidate(monkeypatch, equivalent):
    from behave.sim.rtl import source_proofs

    compare = source_proofs._replica_coordinates
    comparisons, solves = [], []
    monkeypatch.setattr(source_proofs, '_proof_shape', lambda *args, **kwargs: 0)

    def recorded(pairs, **kwargs):
        comparisons.append(pairs)
        return compare(pairs, **kwargs)

    monkeypatch.setattr(source_proofs, '_replica_coordinates', recorded)
    rows = []
    for i in range(12):
        x = z3.BitVec(f'colliding_cache_{i}', 8)
        value = 0 if equivalent else i
        roots = (x == value, x != value)
        label = f'confluence:slot[{i}]'
        rows.append((label, ((label + ':prefix', roots), (label, roots))))
    pair = SimpleNamespace(left=SimpleNamespace(alive=z3.BoolVal(True)),
        _local_source_steps=True, order_queries=lambda **kwargs: iter(rows))

    def solve(label, roots, **kwargs):
        solves.append(label)
        return _solve(label, roots, **kwargs)

    with api.CpuPool(1) as pool, api.cpu_scope(pool.endpoint), ExitStack() as resources:
        runner = _OrderProofs(pair, solve, lambda: None, 1, resources)
        try:
            for label, jobs in rows:
                assert runner(label + ':prefix', jobs[0][1]).answer is SolverAnswer.UNSAT
                assert len(runner.proved_prefixes) == 1
            assert len(comparisons) == len(rows) - 1
            assert len(solves) == (1 if equivalent else len(rows))
        finally:
            runner.close()
