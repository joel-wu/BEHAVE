"""The shared solver receives the complete, unchanged source interval."""

from itertools import accumulate

import pytest
import z3

from behave.sim.symbolic import exact_solve
from behave.sim.symbolic import ranges
from behave.sim.symbolic.step_template import StepTemplate
from behave.sim.core.query import SolverAnswer
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_relation import SourceIntervalTemplate

from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _state

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver,
              pytest.mark.usefixtures("search_lifetime")]

SOURCE = '''module M(input clk,input [3:0] a,output reg [3:0] q,r);
  always @(posedge clk) begin
    if(a==9) $fatal(1,"stop");
    q<=a;
  end
  always @(negedge clk) if(a==q) r=1; else r=2;
endmodule'''


def _constants(terms):
    found, seen, pending = {}, set(), list(terms)
    while pending:
        term = pending.pop()
        if term.get_id() not in seen:
            seen.add(term.get_id())
            if z3.is_const(term) and term.decl().kind() == z3.Z3_OP_UNINTERPRETED:
                found[term.get_id()] = term
            pending.extend(term.children())
    return found


def _inline_named_controls(roots, reference):
    """Substitute the interval's named liveness/error successors into its roots.

    The relation defines each control successor once (``name == term``) instead
    of inlining its growing prefix. Every name absent from ``reference`` must be
    such a total definition; the result mentions only the step encodings' terms.
    """
    named = set(_constants(roots)) - set(_constants(reference))
    definitions, pending = {}, list(reversed(roots))
    while pending:
        term = pending.pop()
        if z3.is_and(term):
            pending.extend(reversed(term.children()))
        elif z3.is_eq(term) and term.arg(0).get_id() in named:
            definitions.setdefault(term.arg(0).get_id(), (term.arg(0), term.arg(1)))
    assert set(definitions) == named
    pairs = []
    for name, term in definitions.values():
        pairs.append((name, z3.substitute(term, *pairs) if pairs else term))
    return tuple(z3.substitute(root, *pairs) if pairs else root for root in roots)


@pytest.mark.parametrize("failure", [False, True])
def test_complete_interval_terms_are_preserved_in_shared_solver(tmp_path, monkeypatch, failure):
    machine, plan = _lower(tmp_path, SOURCE)
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 3, "r": 0})
    goal = (_goal(SOURCE, plan, "$fatal", "failure", "artifact-failure") if failure
            else _goal(SOURCE, plan, "a==q"))
    low = {names["clk"]: 0, names["a"]: 0}
    phases = (low, {**low, names["clk"]: 1}, low)
    bounds = machine.phase_event_bounds(state, phases, (names['a'],), machine.event_bound)
    assert bounds == (1, 3, 2)
    phase_ends = set(accumulate(bounds))
    encodings, calls = [], []
    original_encode, original_solve = StepTemplate.encode_steps, exact_solve.z3_check_qfbv

    def encode(template, *args, **kwargs):
        value = yield from original_encode(template, *args, **kwargs)
        encodings.append(value)
        return value

    def solve(assertions, timeout_ms, memory_mb=None, **options):
        assertions = tuple(assertions)
        assert options['_owned_context'] == assertions[0].ctx
        assert assertions[0].ctx != z3.main_ctx()
        calls.append(timeout_ms)
        assert 0 < timeout_ms <= 5000
        # Reconstruct the previous mathematical query from the very same step
        # expressions. Prove every root equal, not just satisfiability or one model.
        expected, hits, phases, alive = [], [], [], z3.BoolVal(True, ctx=assertions[0].ctx)
        for step, encoding in enumerate(encodings):
            expected.append(z3.Implies(alive, z3.And(encoding.constraint,
                z3.Or(z3.Not(encoding.ok), encoding.post["accepted"][0] == 1))))
            hits.append(z3.And(alive, encoding.hits[goal]))
            alive = z3.And(alive, encoding.ok)
            if step + 1 in phase_ends:
                expected.append(z3.Implies(alive, encoding.post["quiescent"][0] == 1))
                phases.append(z3.Or(*hits))
                hits = []
        expected.append(z3.Or(z3.Or(*phases)))
        assert len(encodings) == sum(bounds)
        assert len(assertions) == len(expected)
        for actual, wanted in zip(_inline_named_controls(assertions, expected), expected, strict=True):
            prover = z3.Solver(ctx=actual.ctx)
            prover.add(actual != wanted)
            assert prover.check() == z3.unsat
        return original_solve(assertions, timeout_ms, memory_mb, **options)

    monkeypatch.setattr(StepTemplate, "encode_steps", encode)
    monkeypatch.setattr(exact_solve, "z3_check_qfbv", solve)
    answer = query_source_interval(machine, env, state, low, (goal,), mutable_inputs=(names["a"],),
        following_inputs=({**low, names["clk"]: 1}, low), event_bound=machine.event_bound,
        timeout_ms=5000)
    assert len(calls) == 1
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert (answer.candidate[names["a"]] == 9) is failure


def test_shared_solver_unknown_is_not_a_local_unreachability_claim(tmp_path, monkeypatch):
    machine, plan = _lower(tmp_path, SOURCE)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 3, "r": 0})
    goal = _goal(SOURCE, plan, "a==9")

    def timed_out(*args, **kwargs):
        return exact_solve.ExactCheckResult(SolverAnswer.UNKNOWN, reason="timeout")

    monkeypatch.setattr(exact_solve, "z3_check_qfbv", timed_out)
    answer = query_source_interval(machine, env, state, {names["clk"]: 1, names["a"]: 0},
        (goal,), mutable_inputs=(names["a"],), event_bound=machine.event_bound)
    assert answer.outcome is probing.Outcome.UNKNOWN
    assert answer.detail == "timeout" and answer.candidate is None


def test_source_queries_release_their_contexts_without_retaining_query_terms(tmp_path, monkeypatch):
    import gc
    import weakref

    machine, plan = _lower(tmp_path, SOURCE)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 3, "r": 0})
    specializations, specialize = [], StepTemplate.specialize_inputs

    def prepare(self, values):
        specializations.append(values)
        return specialize(self, values)

    monkeypatch.setattr(StepTemplate, 'specialize_inputs', prepare)
    template = SourceIntervalTemplate(machine.procedure, env)
    goal = _goal(SOURCE, plan, "a==9")
    contexts, translate = [], StepTemplate.translate

    def record(self, context):
        if len(contexts) % 2:
            assert contexts[-1]() is context
        contexts.append(weakref.ref(context))
        assert context != self.context
        return translate(self, context)

    monkeypatch.setattr(StepTemplate, 'translate', record)
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        for _ in range(3):
            answer = query_source_interval(machine, env, state, {names["clk"]: 1, names["a"]: 0},
                (goal,), mutable_inputs=(names["a"],), event_bound=machine.event_bound,
                _relation=template.relation(machine, (goal,)))
            assert answer.outcome is probing.Outcome.SAT
            assert answer.candidate[names['a']] == 9
            assert all(context() is None for context in contexts)
    finally:
        if enabled:
            gc.enable()
        gc.collect()
    # Both immutable phase views enter the same short-lived query context.
    assert len(contexts) == 6
    assert specializations == [{'action': 0}]


def test_canceled_source_context_does_not_poison_the_prepared_template(tmp_path, monkeypatch):
    machine, plan = _lower(tmp_path, SOURCE)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 3, "r": 0})
    template = SourceIntervalTemplate(machine.procedure, env)
    goal = _goal(SOURCE, plan, "a==9")
    original, calls = exact_solve.z3_check_qfbv, []

    def interrupted_first(assertions, timeout_ms, **options):
        context = options['_owned_context']
        assert context != template.source.context
        calls.append(True)
        if len(calls) == 1:
            context.interrupt()
            return exact_solve.ExactCheckResult(SolverAnswer.UNKNOWN, reason='timeout')
        return original(assertions, timeout_ms, **options)

    monkeypatch.setattr(exact_solve, 'z3_check_qfbv', interrupted_first)
    for outcome in (probing.Outcome.UNKNOWN, probing.Outcome.SAT):
        answer = query_source_interval(machine, env, state, {names['clk']: 1, names['a']: 0},
            (goal,), mutable_inputs=(names['a'],), event_bound=machine.event_bound,
            _relation=template.relation(machine, (goal,)))
        assert answer.outcome is outcome
    assert answer.candidate[names['a']] == 9


@pytest.mark.parametrize('stage', ['translation', 'encoding', 'decoding'])
def test_source_construction_cancellation_is_local_and_next_query_works(tmp_path, monkeypatch, stage):
    import time

    from behave.sim.engines.rtl import source_query

    machine, plan = _lower(tmp_path, SOURCE)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    names = _names(machine)
    state = _state(machine, {'clk': 0, 'a': 0, 'q': 3, 'r': 0})
    template = SourceIntervalTemplate(machine.procedure, env)
    goal = _goal(SOURCE, plan, 'a==9')
    relation = template.relation(machine, (goal,))
    owner, method = {'translation': (StepTemplate, 'translate'),
                     'encoding': (StepTemplate, 'encode_steps'),
                     'decoding': (source_query.local_ir, '_model_value')}[stage]
    original, clock, slow = getattr(owner, method), [10.0], [True]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(time, 'perf_counter', lambda: clock[0])

    def interrupted(*args, **kwargs):
        if slow[0]:
            slow[0] = False
            clock[0] += .2
            raise z3.Z3Exception('canceled')
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, method, interrupted)
    for expected in (probing.Outcome.UNKNOWN, probing.Outcome.SAT):
        answer = query_source_interval(machine, env, state, {names['clk']: 1, names['a']: 0},
            (goal,), mutable_inputs=(names['a'],), event_bound=machine.event_bound,
            timeout_ms=100, _relation=relation)
        assert answer.outcome is expected, answer.detail
        if expected is probing.Outcome.UNKNOWN:
            assert answer.candidate is None and answer.goal is None
    assert answer.candidate[names['a']] == 9


def test_late_source_candidate_is_not_published(tmp_path, monkeypatch):
    import time

    from behave.sim.engines.rtl import source_query

    machine, plan = _lower(tmp_path, SOURCE)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    names = _names(machine)
    state = _state(machine, {'clk': 0, 'a': 0, 'q': 3, 'r': 0})
    goal = _goal(SOURCE, plan, 'a==9')
    relation = SourceIntervalTemplate(machine.procedure, env).relation(machine, (goal,))
    original, clock = source_query.local_ir._model_value, [10.0]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(time, 'perf_counter', lambda: clock[0])

    def late(*args, **kwargs):
        value = original(*args, **kwargs)
        clock[0] += .2
        return value

    monkeypatch.setattr(source_query.local_ir, '_model_value', late)
    answer = query_source_interval(machine, env, state, {names['clk']: 1, names['a']: 0},
        (goal,), mutable_inputs=(names['a'],), event_bound=machine.event_bound,
        timeout_ms=100, _relation=relation)
    assert answer.outcome is probing.Outcome.UNKNOWN
    assert answer.candidate is None and answer.goal is None


@pytest.mark.parametrize('late', ['cancelled', 'sat', 'unsat'])
def test_multislot_source_query_uses_the_same_deadline_owner(monkeypatch, late):
    import time

    from behave.sim.engines.rtl import source_task_query

    clock, first = [10.0], [True]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])

    def query(machine, template, binding, request, segment, context, remaining, result, construction):
        assert context is not z3.main_ctx()
        assert 99 <= remaining() <= 100
        if first[0]:
            first[0] = False
            clock[0] += .2
            if late == 'cancelled':
                raise z3.Z3Exception('canceled')
            if late == 'unsat':
                return result(probing.Outcome.LOCAL_UNSAT)
        return result(probing.Outcome.SAT, goal='g', candidate={'a': 9})

    monkeypatch.setattr(source_task_query, '_query_source_task', query)
    for expected in (probing.Outcome.UNKNOWN, probing.Outcome.SAT):
        answer = source_task_query.query_source_task(None, None, None, None, None, timeout_ms=100)
        assert answer.outcome is expected
        if expected is probing.Outcome.UNKNOWN:
            assert answer.goal is None and answer.candidate is None


def test_multislot_source_native_error_before_deadline_is_not_a_timeout(monkeypatch):
    from behave.sim.engines.rtl import source_task_query

    def query(*args):
        raise z3.Z3Exception('sort mismatch')

    monkeypatch.setattr(source_task_query, '_query_source_task', query)
    with pytest.raises(z3.Z3Exception, match='sort mismatch'):
        source_task_query.query_source_task(None, None, None, None, None, timeout_ms=5000)


@pytest.mark.parametrize('reason', ['canceled', b'canceled', 'interrupted', b'interrupted'])
def test_native_cancellation_before_our_deadline_is_unknown_and_retryable(tmp_path, monkeypatch, reason):
    machine, plan = _lower(tmp_path, SOURCE)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    names = _names(machine)
    state = _state(machine, {'clk': 0, 'a': 0, 'q': 3, 'r': 0})
    goal = _goal(SOURCE, plan, 'a==9')
    relation = SourceIntervalTemplate(machine.procedure, env).relation(machine, (goal,))
    original, first = exact_solve.z3_check_qfbv, [True]

    def interrupted(*args, **kwargs):
        if first[0]:
            first[0] = False
            raise z3.Z3Exception(reason)
        return original(*args, **kwargs)

    monkeypatch.setattr(exact_solve, 'z3_check_qfbv', interrupted)
    for expected in (probing.Outcome.UNKNOWN, probing.Outcome.SAT):
        answer = query_source_interval(machine, env, state, {names['clk']: 1, names['a']: 0},
            (goal,), mutable_inputs=(names['a'],), event_bound=machine.event_bound,
            timeout_ms=5000, _relation=relation)
        assert answer.outcome is expected
        if expected is probing.Outcome.UNKNOWN:
            assert answer.candidate is None and answer.goal is None
            assert 'exhausted' not in answer.detail
    assert answer.candidate[names['a']] == 9


@pytest.mark.parametrize('reason', ['canceled', b'canceled', 'interrupted', b'interrupted'])
def test_multislot_native_cancellation_is_not_source_unavailable(monkeypatch, reason):
    from behave.sim.engines.rtl import source_task_query

    def query(*args):
        raise z3.Z3Exception(reason)

    monkeypatch.setattr(source_task_query, '_query_source_task', query)
    answer = source_task_query.query_source_task(None, None, None, None, None, timeout_ms=5000)
    assert answer.outcome is probing.Outcome.UNKNOWN
    assert answer.candidate is None and answer.goal is None


def test_native_construction_is_interrupted_and_next_source_query_can_solve(tmp_path, monkeypatch):
    machine, plan = _lower(tmp_path, SOURCE)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    names = _names(machine)
    state = _state(machine, {'clk': 0, 'a': 0, 'q': 3, 'r': 0})
    goal = _goal(SOURCE, plan, 'a==9')
    relation = SourceIntervalTemplate(machine.procedure, env).relation(machine, (goal,))
    original, calls = StepTemplate.encode_steps, []

    def encode(self, *args, **kwargs):
        if not calls:
            calls.append(True)
            # No local solver timeout: only the enclosing query can interrupt
            # this native operation during construction, before the real check.
            pigeons = [z3.BitVec(f'construction_p{i}', 8, ctx=self.context) for i in range(80)]
            solver = z3.Solver(ctx=self.context)
            solver.add(z3.Distinct(*pigeons), *(z3.ULT(p, 79) for p in pigeons))
            assert solver.check() == z3.unknown
        return (yield from original(self, *args, **kwargs))

    monkeypatch.setattr(StepTemplate, 'encode_steps', encode)
    for timeout_ms, expected in ((100, probing.Outcome.UNKNOWN), (5000, probing.Outcome.SAT)):
        answer = query_source_interval(machine, env, state, {names['clk']: 1, names['a']: 0},
            (goal,), mutable_inputs=(names['a'],), event_bound=machine.event_bound,
            timeout_ms=timeout_ms, _relation=relation)
        assert answer.outcome is expected, answer.detail
        if expected is probing.Outcome.UNKNOWN:
            assert answer.goal is None and answer.candidate is None
    assert calls == [True] and answer.candidate[names['a']] == 9
