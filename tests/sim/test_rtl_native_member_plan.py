"""A compact member proposal is legal, not a replacement for all source orders."""

from dataclasses import replace

import pytest
import z3

from behave.sim.symbolic import interp
from behave.sim.rtl.source_direct import DirectSourceCompiler, NativeCoverSourceCompiler, NativeMemberSourceCompiler

from .test_rtl_source_relation import _pins, _relation
from .test_rtl_source_scheduler import _lower, _names, _state

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('memory', (False, True))
@pytest.mark.parametrize('private', (False, True))
def test_member_does_not_recompute_unused_input_effects(tmp_path, monkeypatch, memory, private):
    from behave.sim.rtl import source_direct

    body = 'always_comb if(a==3) q=7; else q=a;'
    values = {'a': 0, 'q': 0}
    if memory:
        body = ('logic [2:0] cells[0:1]; always_comb begin '
                'cells[0]=a; cells[1]=7; q=cells[a==3]; end')
        values['cells'] = [0, 0]
    machine, _ = _lower(tmp_path, 'module M(input [2:0] a,output logic [2:0] q);'
                        + body + ' endmodule')
    names = _names(machine)
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, values), goals)
    if private:
        context = z3.Context()
        relation = relation.translate(context)
        state = {name: [v.translate(context) for v in value] if isinstance(value, list)
                 else value.translate(context) for name, value in state.items()}
    compiler = NativeMemberSourceCompiler(machine)
    relation = replace(relation, direct=compiler)

    def unexpected(*args, **kwargs):
        pytest.fail('chosen-order proposal recomputed unused commutativity facts')

    # Constructor access summaries remain required; only per-drive refinement
    # is irrelevant to this proposal. The universal compiler keeps its checks.
    monkeypatch.setattr(source_direct, 'conditional_accesses', unexpected)
    cached = None
    for value in (1, 3, 7, 2):
        pins = _pins(machine, env, {names['a']: value})
        if private:
            pins = {name: term.translate(context) for name, term in pins.items()}
        result = relation.encode(state, pins, event_bound=machine.event_bound)
        solver = z3.Solver(ctx=relation.source.context)
        solver.add(*result.constraints, result.quiescent)
        assert solver.check() == z3.sat
        solver.add(result.state[names['q']] != (7 if value == 3 else value))
        assert solver.check() == z3.unsat
        current = tuple((key, id(view)) for key, view in
                        (compiler._action_views if memory else compiler._relations).items())
        assert current and (cached is None or current == cached)
        cached = current
    assert not compiler._context_effects


@pytest.mark.parametrize('memory', (False, True))
@pytest.mark.parametrize('private', (False, True))
def test_reused_member_view_rechecks_changed_values(tmp_path, memory, private):
    from behave.sim.rtl.exact_job_instance import PreparedSourceExact
    from behave.sim.rtl.source_relation import SourceObservation

    body = 'always_comb q=a;'
    values = {'a': 0, 'q': 0}
    if memory:
        body = 'logic [2:0] cells[0:1]; always_comb begin cells[0]=a; cells[1]=a; q=cells[a[0]]; end'
        values['cells'] = [0, 0]
    machine, _ = _lower(tmp_path, 'module M(input [2:0] a,output logic [2:0] q);'
                        + body + ' endmodule')
    relation, state, env = _relation(machine, _state(machine, values), ())
    if private:
        ctx = z3.Context()
        relation = relation.translate(ctx)
        state = {name: [v.translate(ctx) for v in value] if isinstance(value, list)
                 else value.translate(ctx) for name, value in state.items()}
    prepared = object.__new__(PreparedSourceExact)
    prepared.machine, prepared._native_views = machine, None
    names, compiler = _names(machine), None
    for value in (1, 7, 2, 0):
        view = prepared._native_interval(relation, member=True)
        assert compiler is None or view.direct is compiler
        compiler = view.direct
        pins = _pins(machine, env, {names['a']: value})
        if private:
            pins = {name: term.translate(ctx) for name, term in pins.items()}
        with compiler.request_scope(None, SourceObservation.PHASE_EVENTS):
            result = view.encode(state, pins, event_bound=machine.event_bound)
            compiler._context_effects['record-local-test'] = value
        assert not compiler._context_effects
        solver = z3.Solver(ctx=view.source.context)
        solver.add(*result.constraints, result.quiescent)
        assert solver.check() == z3.sat
        solver.add(result.state[names['q']] != value)
        assert solver.check() == z3.unsat  # Not a previous record's output.
    with pytest.raises(InterruptedError):
        with compiler.request_scope(None, SourceObservation.PHASE_EVENTS):
            compiler._context_effects['partial'] = True
            raise InterruptedError('record cancelled')
    assert not compiler._context_effects


@pytest.mark.parametrize('value', [0, 1, 3, 7])
@pytest.mark.parametrize('kind', ['combinational', 'nba', 'failure'])
@pytest.mark.parametrize('driving', [False, True])
def test_member_macro_preserves_guarded_source_execution(tmp_path, value, kind, driving):
    source = '''module M(input clk,input [2:0] a,output logic [2:0] q);
      logic [2:0] b;
      always_comb if(b==3) q=7; else q=b;
      PRODUCER
    endmodule'''.replace('PRODUCER', {
        'combinational': 'always_comb b=a;',
        'nba': 'always @(posedge clk) b<=a;',
        'failure': 'always_comb begin b=a; if(a==3) b=\'x; end',
    }[kind])
    machine, _ = _lower(tmp_path, source, defer_domains=True)
    names = _names(machine)
    compiler = NativeMemberSourceCompiler(machine)
    values = {'clk': 0, 'a': 0 if driving else value, 'b': 0, 'q': 0}
    state = _state(machine, values, startup=not driving)
    relation, symbolic, env = _relation(machine, state, ())
    pins = {names['clk']: 1, names['a']: value} if driving else None
    encoded_pins = None if pins is None else _pins(machine, env, pins)
    roots = compiler.roots(symbolic, encoded_pins)
    plan = compiler.plan(roots, all_active=not driving)
    macro, _ = compiler._template(roots, driving=driving, all_active=not driving)
    inputs = {driven: (pins if driving else state)[name] for name, driven in machine.drive_inputs}
    inputs['action'] = 0
    expected, marks, failure = state, set(), None
    for action in ((0,) if driving else ()) + plan:
        expected, _, found, failure = interp.run_step(machine.procedure,
            {**inputs, 'action': action}, expected)
        marks.update(found)
        if failure:
            break
    actual, outputs, hits, error = interp.run_step(macro.procedure, inputs, state)
    assert actual == expected
    assert hits == marks and error == failure
    if not error:
        assert outputs.transactions[0]['quiescent'] == int(
            not any(actual[name] for name in compiler.quiet_flags))


def test_member_proposal_does_not_erase_pending_work(tmp_path, monkeypatch):
    machine, _ = _lower(tmp_path, '''module M(input a,output logic q);
      wire b; assign b=a; always_comb if(b) q=1; else q=0;
    endmodule''')
    relation, state, _ = _relation(machine,
        _state(machine, {'a': 1, 'b': 0, 'q': 0}, startup=True), ())
    compiler = NativeMemberSourceCompiler(machine)
    monkeypatch.setattr(compiler, 'plan', lambda *args, **kwargs: ())
    proposal = replace(relation, direct=compiler).settle(state,
        event_bound=machine.event_bound, require_quiescence=False)
    solver = z3.Solver()
    solver.add(*proposal.constraints, proposal.unfinished)
    assert solver.check() == z3.sat
    solver.add(proposal.quiescent)
    assert solver.check() == z3.unsat


def test_selected_order_cannot_prove_an_unseen_event_unreachable(tmp_path):
    source = '''module M(input a,output logic q);
      wire b; assign b=a; always_comb if(b) q=a; else q=0;
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, _ = _relation(machine,
        _state(machine, {'a': 1, 'b': 0, 'q': 0}, startup=True), goals)
    member_owner = NativeMemberSourceCompiler(machine)
    member = replace(relation, direct=member_owner).settle(
        state, event_bound=machine.event_bound)
    cover_owner = NativeCoverSourceCompiler(machine)
    cover = replace(relation, direct=cover_owner).settle(state, event_bound=machine.event_bound)
    missing = []
    for goal in goals:
        solver = z3.Solver()
        solver.add(*member.constraints, member.hits[goal])
        if solver.check() == z3.unsat:
            solver.reset()
            solver.add(*cover.constraints, cover.hits[goal])
            if solver.check() == z3.sat:
                missing.append(goal)
    assert missing  # The other order sees b==0 before the producer runs.
    assert cover_owner.covers_native_orders and not member_owner.covers_native_orders


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('driving', [False, True])
@pytest.mark.parametrize('body', [
    'always_comb b=a; always_comb if(b==3) q=7; else q=b;',
    'always @(posedge clk) b<=a; always_comb if(b==3) q=7; else q=b;',
    "always_comb begin b=a; if(a==3) b='x; end always_comb q=b;",
    'always @(posedge clk) begin b[0]<=a[0]; b[2:1]<=a[2:1]; q<=b; end',
])
def test_shared_actions_equal_the_original_macro_for_all_inputs(tmp_path, private, driving, body, monkeypatch):
    from behave.sim.symbolic.encoder import widen
    from behave.sim.symbolic.step_template import _leaves, _tree

    machine, _ = _lower(tmp_path,
        'module M(input clk,input [2:0] a,output logic [2:0] q); logic [2:0] b;'
        + body + ' endmodule', defer_domains=True)
    names = _names(machine)
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine,
        _state(machine, {'clk': 0, 'a': 0, 'b': 0, 'q': 0}, startup=not driving), goals)
    context = z3.Context() if private else relation.source.context
    relation = relation.translate(context) if private else relation
    state = {name: term.translate(context) if private else term for name, term in state.items()}
    symbolic = z3.BitVec('member_symbolic_input', state[names['a']].size(), ctx=context)
    if driving:
        pins = _pins(machine, env, {names['clk']: 1, names['a']: 0})
        pins = {name: term.translate(context) if private else term for name, term in pins.items()}
        pins[names['a']] = symbolic
    else:
        pins = None
        state[names['a']] = symbolic
    old, new = NativeMemberSourceCompiler(machine), NativeMemberSourceCompiler(machine)
    monkeypatch.setattr(old, '_phase_effects', DirectSourceCompiler._phase_effects.__get__(old))
    monkeypatch.setattr(old, '_encode_plan_steps',
                        DirectSourceCompiler._encode_plan_steps.__get__(old))
    # Compare the two implementations, independently of the syntax cost
    # selector. Scalar fixtures normally use the compact interval image.
    monkeypatch.setattr(new, '_encode_plan_steps', new._encode_action_views_steps)
    enabled = z3.Bool('member_enabled', ctx=context)
    outputs = [replace(relation, direct=compiler).encode(state, pins,
        event_bound=machine.event_bound, enabled=enabled, require_quiescence=False)
        for compiler in (old, new)]
    left, right = outputs
    # Raw quiet bits are unobserved when no interval was entered (and after
    # failure). Compare the actual continuation/unfinished interface instead.
    different = [left.alive != right.alive, left.error != right.error,
                 left.entered != right.entered, left.unfinished != right.unfinished,
                 left.continuable != right.continuable]
    for field in ('hits', 'occurrences', 'domain_faults'):
        assert getattr(left, field).keys() == getattr(right, field).keys()
        different.extend(value != getattr(right, field)[name] for name, value in getattr(left, field).items())
    for name, value in left.state.items():
        for a, b in zip(_leaves(_tree(value)), _leaves(_tree(right.state[name])), strict=True):
            a, b = widen(a, b) if z3.is_bv(a) else (a, b)
            different.append(z3.And(left.alive, a != b))
    solver = z3.Solver(ctx=context)
    solver.add(*left.constraints, *right.constraints)
    for value in range(8):
        for enter in (False, True):
            solver.push()
            solver.add(symbolic == value, enabled == enter)
            assert solver.check() == z3.sat
            solver.pop()
    solver.add(z3.Or(*different))
    answer = solver.check()
    assert answer == z3.unsat, (solver.model().eval(symbolic), solver.model().eval(enabled),
        [(str(term), str(solver.model().eval(term, model_completion=True))) for term in different
         if z3.is_true(solver.model().eval(term, model_completion=True))])


def test_member_reuses_kernel_views_without_reencoding_ir(tmp_path, monkeypatch):
    from behave.sim.symbolic.step_template import StepTemplate

    machine, _ = _lower(tmp_path, '''module M(input [2:0] a,output logic [2:0] q);
      logic [2:0] b; always_comb b=a; always_comb q=b;
    endmodule''')
    names = _names(machine)
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'b': 0, 'q': 0}), ())
    compiler = NativeMemberSourceCompiler(machine)
    monkeypatch.setattr(compiler, '_encode_plan_steps', compiler._encode_action_views_steps)
    relation = replace(relation, direct=compiler)

    def unexpected(*args, **kwargs):
        pytest.fail('membership recompiled a compound source procedure')

    monkeypatch.setattr(StepTemplate, '__init__', unexpected)
    views = None
    for value in (1, 3, 7):
        encoded = relation.encode(state, _pins(machine, env, {names['a']: value}),
                                  event_bound=machine.event_bound)
        solver = z3.Solver()
        solver.add(*encoded.constraints)
        assert solver.check() == z3.sat
        assert solver.model().eval(encoded.state[names['q']]).as_long() == value
        current = {key: id(view) for key, view in compiler._action_views.items()}
        assert views is None or views == current
        views = current


def test_cancelled_member_view_is_not_cached(tmp_path, monkeypatch):
    from behave.isolation.preparation_control import PreparationCancelled
    from behave.sim.symbolic.step_template import StepTemplate

    machine, _ = _lower(tmp_path, 'module M(input a,output logic q); always_comb q=a; endmodule')
    names = _names(machine)
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'q': 0}), ())
    compiler = NativeMemberSourceCompiler(machine)
    monkeypatch.setattr(compiler, '_encode_plan_steps', compiler._encode_action_views_steps)
    original = StepTemplate.normalized

    def stopped(view):
        original(view)
        raise PreparationCancelled('view')

    monkeypatch.setattr(StepTemplate, 'normalized', stopped)
    with pytest.raises(PreparationCancelled):
        replace(relation, direct=compiler).encode(state, _pins(machine, env, {names['a']: 1}),
            event_bound=machine.event_bound)
    assert not compiler._action_views


@pytest.mark.parametrize('memory', (False, True))
def test_member_selects_and_reuses_construction_by_structure(tmp_path, monkeypatch, memory):
    from behave.sim.symbolic.step_template import StepTemplate

    declarations, values = ('logic [2:0] b;', {'b': 0})
    body = 'always_comb b=a; always_comb q=b;'
    if memory:
        declarations, values = ('logic [2:0] cells[0:1];', {'cells': [0, 0]})
        body = 'always_comb begin cells[0]=a; cells[1]=a; q=cells[a[0]]; end'
    machine, _ = _lower(tmp_path,
        'module M(input [2:0] a,output logic [2:0] q);' + declarations + body + ' endmodule')
    names = _names(machine)
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'q': 0, **values}), ())
    compiler = NativeMemberSourceCompiler(machine)
    assert compiler._reuse_actions is memory
    relation = replace(relation, direct=compiler)
    calls, initialize = [], StepTemplate.__init__
    def counted(*args, **kwargs):
        calls.append(None)
        initialize(*args, **kwargs)
    monkeypatch.setattr(StepTemplate, '__init__', counted)
    for value in (1, 3, 7):
        encoded = relation.encode(state, _pins(machine, env, {names['a']: value}),
                                  event_bound=machine.event_bound)
        solver = z3.Solver()
        solver.add(*encoded.constraints)
        assert solver.check() == z3.sat
        assert solver.model().eval(encoded.state[names['q']]).as_long() == value
    assert len(calls) == (0 if memory else 1)
    assert bool(compiler._action_views) is memory


@pytest.mark.parametrize('fault', ['unfinished', 'domain', 'memory', 'different', 'environment', 'none'])
@pytest.mark.parametrize('terminal', [False, True])
def test_native_proposal_checks_every_root_and_never_publishes_a_negative(monkeypatch, fault, terminal):
    from types import SimpleNamespace
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.core.query import SolverAnswer
    from behave.sim.core.failure import RunFailure
    from behave.sim.core.refusal import Refusal, Refused
    from behave.sim.rtl import analysis_job, observation_audit, source_member
    from behave.sim.core import analysis

    def refused(*args, **kwargs):
        raise Refused(Refusal('rtl-direct-coactive-dependency', 'coactive fixture'))

    compiler = SimpleNamespace(covers_native_orders=True, plan=refused,
        machine=SimpleNamespace(startup=(True,)))
    bit = lambda name: z3.BoolVal(fault == name)
    encoded = SimpleNamespace(unfinished=bit('unfinished'), domain_faults={'d': bit('domain')},
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=(bit('memory'),))),
        environment_assumptions={'e': bit('environment')})
    request = SimpleNamespace(constraints=(), fix=lambda rows: ())
    calls, remembered = [], []

    def bind(bound, **kwargs):
        calls.append(kwargs)
        return object(), request, encoded

    instance = object.__new__(analysis_job._SourceAnalysis)
    instance._native.member_proof = None
    instance.source = SimpleNamespace(template=SimpleNamespace(direct=compiler), bind_request=bind,
        frontend={}, observation=None, contract=object())
    instance.request = request
    instance.domain = SimpleNamespace(bound=2, digest=lambda: 'fixture-domain')
    instance._remember_member = remembered.append
    comparison = SimpleNamespace(constraints=(), different=bit('different'), native_image={},
                                 first_missed_phase=lambda *a, **k: None)
    monkeypatch.setattr(observation_audit, 'compare_native_history', lambda *a, **k: comparison)
    monkeypatch.setattr(analysis, 'audit_environment_assumptions', lambda *a: None)
    from behave.sim.core import audit
    monkeypatch.setattr(audit, 'AuditedWitness', lambda **kwargs: kwargs)
    observed = []

    def hint(model, histories, roots, **kwargs):
        observed.append(roots)
        return model if all(z3.is_true(z3.simplify(root)) for root in roots) else None

    monkeypatch.setattr(source_member, 'source_member_hint', hint)
    solver = z3.Solver()
    assert solver.check() == z3.sat
    trace = (RunFailure.candidate('fixture', Refusal('rtl-source-failure', 'fixture failure'))
        if terminal else SimpleNamespace(stimulus=()))
    result = instance._native.propose_member(instance.source, instance.request, instance.domain,
        instance.observation_scope, ({}, {}), trace,
        key=b'fixture-inputs', remember=instance._remember_member,
        solve=lambda *a: ExactCheckResult(SolverAnswer.SAT, model=solver.model()), check=lambda: None)
    assert observed and calls[0]['native_member'] is True
    assert (result is not None) == (fault == 'none')
    assert bool(remembered) == (fault == 'none')


@pytest.mark.parametrize('fault', ['none', 'output', 'unknown', 'unfinished', 'earlier-cut'])
def test_native_local_repair_keeps_whole_history_checks_and_solver_fallback(monkeypatch, fault):
    from types import SimpleNamespace
    from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check
    from behave.sim.core import analysis
    from behave.sim.core.query import SolverAnswer
    from behave.sim.core.refusal import Refusal, Refused
    from behave.sim.rtl import analysis_job, observation_audit, source_member, source_trace
    from behave.sim.rtl.source_member import SourceMemberPlan

    def refused(*args, **kwargs):
        raise Refused(Refusal('rtl-direct-coactive-dependency', 'coactive fixture'))

    compiler = SimpleNamespace(covers_native_orders=True, plan=refused,
        machine=SimpleNamespace(startup=(True,)))
    request = SimpleNamespace(constraints=(), fix=lambda rows: (), codec=SimpleNamespace(initial=None))
    calls, remembered, solving = [], [], []
    flag, false, true = z3.Bool('local_order_event'), z3.BoolVal(False), z3.BoolVal(True)

    def bind(bound, **kwargs):
        selected = frozenset(dict(kwargs['native_phase_actions']))
        calls.append(selected)
        encoded = SimpleNamespace(alive=true, unfinished=z3.BoolVal(bool(selected) and fault == 'unfinished'),
            domain_faults={}, source=SimpleNamespace(memory=SimpleNamespace(conflicts=()), slots=()),
            startup=None, environment_assumptions={},
            member_plan=SourceMemberPlan(((flag, z3.BoolVal(bool(selected))),), ()))
        observed = ((false if selected and fault == 'earlier-cut' else true,), (flag,), (flag,))
        comparison = observation_audit.SourceNativeComparison(encoded,
            () if selected else (z3.Not(flag),),
            {'event': z3.Not(flag), 'output': z3.BoolVal(fault == 'output'),
             'earlier': z3.BoolVal(bool(selected) and fault == 'earlier-cut')}, {},
            member_observations=observed)
        return comparison, request, encoded

    instance = object.__new__(analysis_job._SourceAnalysis)
    instance._native.member_proof = None
    instance.source = SimpleNamespace(template=SimpleNamespace(direct=compiler), bind_request=bind,
        frontend={}, observation=None, contract=object())
    instance.request, instance.domain = request, SimpleNamespace(bound=2, digest=lambda: 'fixture-domain')
    instance._remember_member = remembered.append
    monkeypatch.setattr(observation_audit, 'compare_native_history', lambda relation, *a, **k: relation)
    monkeypatch.setattr(analysis, 'audit_environment_assumptions', lambda *a: None)
    from behave.sim.core import audit
    monkeypatch.setattr(audit, 'AuditedWitness', lambda **kwargs: kwargs)
    monkeypatch.setattr(source_trace, 'read_source_history', lambda *a, **kw:
        SimpleNamespace(phases=((None, SimpleNamespace(goals=frozenset())),) * 3))
    monkeypatch.setattr(source_member, 'propose_native_phase_order',
        lambda machine, phases, index, *a, **kw: None if index == 0 or fault == 'unknown' else (1,))

    def solve(phase, roots):
        solving.append(phase)
        return z3_check(roots, 1000)

    result = instance._native.propose_member(instance.source, instance.request, instance.domain,
        instance.observation_scope, ({}, {}), SimpleNamespace(stimulus=()),
        key=b'fixture-inputs', remember=instance._remember_member, solve=solve, check=lambda: None)
    assert calls == ([frozenset()] if fault == 'unknown' else [frozenset(), frozenset({1})])
    assert (result is not None) == (fault == 'none')
    assert bool(remembered) == (fault == 'none')
    assert solving == ['native-order-environment']  # No symbolic schedule enumeration.


@pytest.mark.parametrize('private', [False, True])
def test_mixed_phases_retain_real_source_order_choices_only_at_selected_cut(tmp_path, private):
    machine, _ = _lower(tmp_path, '''module M(input a,output logic q);
      wire b; assign b=a; always_comb if(b) q=a; else q=0;
    endmodule''')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine,
        _state(machine, {'a': 0, 'b': 0, 'q': 0}), goals)
    if private:
        context = z3.Context()
        relation = relation.translate(context)
        state = {name: value.translate(context) for name, value in state.items()}
    names = _names(machine)
    pins = _pins(machine, env, {names['a']: 1})
    if private:
        pins = {name: value.translate(context) for name, value in pins.items()}
    member = replace(relation, direct=NativeMemberSourceCompiler(machine))
    cover = replace(relation, direct=NativeCoverSourceCompiler(machine))
    options = dict(event_bounds=(machine.event_bound,) * 2)
    chosen = member.encode_phases(state, (pins, pins), **options)
    from behave.sim.core.construction_checkpoint import finish_construction
    mixed = finish_construction(member.encode_phases_steps(state, (pins, pins),
        phase_relations=(cover, member), **options))
    missing = []
    for goal in goals:
        solver = z3.Solver(ctx=relation.source.context)
        solver.add(*chosen.constraints, chosen.phases[0].hits[goal])
        if solver.check() == z3.unsat:
            solver.reset()
            solver.add(*mixed.constraints, mixed.phases[0].hits[goal])
            if solver.check() == z3.sat:
                missing.append(goal)
    assert missing  # The alternate legal order sees b==0 before its producer.
    solver = z3.Solver(ctx=relation.source.context)
    solver.add(*mixed.constraints)
    assert solver.check() == z3.sat
    solver.add(mixed.state[names['q']] != 1)
    assert solver.check() == z3.unsat
    with pytest.raises(ValueError, match='each physical phase'):
        finish_construction(member.encode_phases_steps(state, (pins, pins),
            phase_relations=(cover,), **options))


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('deferred', [False, True])
def test_concrete_order_proposal_matches_original_symbolic_source(tmp_path, private, deferred):
    from types import SimpleNamespace
    from behave.sim.rtl.source_member import propose_native_phase_order

    assignment = '<=' if deferred else '='
    machine, _ = _lower(tmp_path, f'''module M(input a,output logic q);
      wire b; assign b=a; always_comb if(b) q{assignment}a; else q{assignment}0;
    endmodule''')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'b': 0, 'q': 0}), goals)
    names = _names(machine)
    pins = _pins(machine, env, {names['a']: 1})
    if private:
        context = z3.Context()
        relation = relation.translate(context)
        state = {name: term.translate(context) for name, term in state.items()}
        pins = {name: term.translate(context) for name, term in pins.items()}
    chosen = replace(relation, direct=NativeMemberSourceCompiler(machine)).encode(
        state, pins, event_bound=machine.event_bound)
    cover = replace(relation, direct=NativeCoverSourceCompiler(machine)).encode(
        state, pins, event_bound=machine.event_bound)
    solver = z3.Solver(ctx=relation.source.context)
    for goal in goals:
        solver.reset()
        solver.add(*chosen.constraints, chosen.hits[goal])
        if solver.check() == z3.unsat:
            solver.reset()
            solver.add(*cover.constraints, cover.hits[goal])
            if solver.check() == z3.sat:
                break
    else:
        pytest.fail('fixture needs a legal transient event missed by producer-first order')
    model = solver.model()
    expected = {goal for goal, term in cover.hits.items() if z3.is_true(model.eval(term))}
    phases = (SimpleNamespace(state=state), cover)
    actions = propose_native_phase_order(machine, phases, 1, model, expected, set(), check=lambda: None)
    assert actions is not None and all(action > 0 for action in actions)
    repaired = replace(relation, direct=NativeMemberSourceCompiler(machine, actions=actions)).encode(
        state, pins, event_bound=machine.event_bound)
    solver.reset()
    solver.add(*repaired.constraints, *(term == (goal in expected) for goal, term in repaired.hits.items()))
    assert solver.check() == z3.sat
    assert solver.model().eval(repaired.state[names['q']]).as_long() == 1
    # Wrong output/native bits cannot be fixed by merely choosing another order.
    wrong = replace(cover, state={**cover.state, names['q']: z3.BitVecVal(0,
        cover.state[names['q']].size(), ctx=relation.source.context)})
    assert propose_native_phase_order(machine, (phases[0], wrong), 1, model,
        expected, set(), check=lambda: None) is None
    assert propose_native_phase_order(machine, phases, 0, model,
        expected, set(), check=lambda: None) is None
    empty = replace(relation, direct=NativeMemberSourceCompiler(machine, actions=())).encode(
        state, pins, event_bound=machine.event_bound, require_quiescence=False)
    solver.reset()
    solver.add(*empty.constraints)
    assert solver.check() == z3.sat
    assert z3.is_true(solver.model().eval(empty.unfinished))
    for invalid in [(0,), (-1,), (True,), (len(machine.actions),), [1]]:
        with pytest.raises(ValueError, match='compiler-owned'):
            NativeMemberSourceCompiler(machine, actions=invalid)
