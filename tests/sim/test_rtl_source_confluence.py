"""Internal-order independence must include state, events and terminal behavior."""

from dataclasses import replace

import pytest
import z3

from behave.sim import api
from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.memory import MemoryEnvironment, MemoryEnvironmentState, MemoryInterface, MemoryPort, MemoryRun
from behave.sim.core.query import SolverAnswer
from behave.sim.rtl.protocol_relation import ProtocolBoundary
from behave.sim.rtl.source_task import SourceTaskRelation

from .test_rtl_source_history import HISTORY_SOURCE, _initial, _simple_contract
from .test_rtl_source_memory_relation import _prepare
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _names
from .test_rtl_source_task_relation import SOURCE as MEMORY_SOURCE
from .test_rtl_source_task_relation import _contract as memory_contract

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


def _audit(pair, *, domain=(), unknown=None):
    calls = []
    base = (*pair.constraints, *domain)
    lemmas, facts = [], []

    def solve(phase, assertions):
        premises = [*lemmas, *facts] if phase.startswith('confluence:') else []
        assert len(assertions) == len(base) + len(premises) + (phase != 'feasibility')
        assert all(term is before for term, before in zip(assertions, base))
        assert all(term.eq(before) for term, before in zip(assertions[len(base):], premises))
        calls.append(phase)
        if phase == unknown or unknown == 'confluence' and phase.startswith('confluence:'):
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='test solver budget exhausted')
        answer = z3_check(assertions, 30_000, _owned_context=pair.left.alive.ctx)
        if phase.startswith('confluence:') and answer.answer is SolverAnswer.UNSAT:
            # Structural equality is enough here: production constructs the
            # same negation only after this checked result is returned.
            lemmas.append(z3.Not(assertions[-1]))
        elif phase != 'feasibility' and answer.answer is SolverAnswer.UNSAT:
            facts.append(z3.Not(assertions[-1]))
        return answer

    return pair.audit(solve, domain=domain), calls


def _all_phases(pair):
    return ['feasibility', 'completeness', 'definedness', 'memory',
            *(name for name, _ in pair._confluence_obligations())]


@pytest.mark.parametrize('unknown', [None, 'completeness', 'definedness', 'memory'])
def test_domain_facts_are_available_only_after_their_full_proofs(unknown):
    from types import SimpleNamespace

    from behave.sim.rtl.source_confluence import SourceOrderComparison

    pending, unencoded, conflict = z3.Bools('pending unencoded conflict')
    base = tuple(z3.Not(term) for term in (pending, unencoded, conflict))
    side = SimpleNamespace(alive=z3.BoolVal(True), unfinished=pending,
        domain_faults={'unencoded': unencoded}, constraints=base,
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=(conflict,))))
    pair = SourceOrderComparison(side, side, {'startup.state.x': z3.BoolVal(False)})
    calls, proved = [], []

    def solve(phase, assertions):
        calls.append(phase)
        if not phase.startswith('confluence:'):
            assert len(assertions) == len(pair.constraints) + (phase != 'feasibility')
        else:
            assert len(proved) == 3
            actual = assertions[len(pair.constraints):-1]
            assert len(actual) == 3
            assert all(a.eq(b) for a, b in zip(actual, proved))
        if phase == unknown:
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='unproved prerequisite')
        answer = z3_check(assertions, 10_000)
        if phase in ('completeness', 'definedness', 'memory'):
            assert answer.answer is SolverAnswer.UNSAT
            proved.append(z3.Not(assertions[-1]))
        return answer

    result = pair.audit(solve)
    assert result.status is (AuditCheckStatus.AGREED if unknown is None else AuditCheckStatus.REFUSED)
    if unknown is not None:
        assert calls[-1] == unknown
        assert not any(phase.startswith('confluence:') for phase in calls)


def _comparison(tmp_path, source, needle=None, *, steps=2, private=False,
                full_catalogue=False, symbolic_initial=False, variable_queue=False):
    machine, plan, env, template = _prepare(tmp_path, source)
    context = z3.Context() if private else None
    contract = _simple_contract()
    goals = () if needle is None else (_goal(source, plan, needle),)
    if full_catalogue:
        from behave.sim.rtl.source_goals import manifest_of

        goals = tuple(manifest_of(plan.declarations, filename='design.sv'))
    relation = SourceTaskRelation(template, machine, contract, goals,
                                  context=context, complete_state=True, occurrences=True)
    queues = {'in':tuple((z3.BitVec(f'input_{i}', 2, ctx=context),) for i in range(steps))}
    actions = relation.protocol.symbolic_actions(steps)
    slots = [api.EventSlot(controls={'rst':0, 'ordy':0})]*steps
    initial = _initial(machine, env)
    if symbolic_initial:
        name = _names(machine)['saved']
        initial = relation.source.relation.initial_state(machine,
            {**dict.fromkeys(machine.program.fields, 0), name: None}).state
    lengths = {'in': z3.Int('length', ctx=context)} if variable_queue else None
    pair = relation.compare_orders(initial, ProtocolBoundary.initial(contract),
        queues, slots, None, memory_state=MemoryEnvironmentState.initial((), None),
        actions=actions, queue_lengths=lengths, startup=True)
    solver = z3.Solver(ctx=context)
    solver.add(*pair.constraints)
    return pair, solver, queues, actions, goals, _names(machine)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('variable_queue', [False, True])
def test_nba_history_is_order_independent_for_all_input_and_ready_choices(tmp_path, private, variable_queue):
    pair, solver, _queues, _actions, _goals, _storage_names = _comparison(
        tmp_path, HISTORY_SOURCE, 'saved==1', private=private,
        full_catalogue=True, symbolic_initial=True, variable_queue=variable_queue)
    assert solver.check() == z3.sat  # An empty domain must not count as a proof.
    solver.push()
    solver.add(pair.unfinished)
    assert solver.check() == z3.unsat
    solver.pop()
    solver.add(pair.different)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
def test_source_domain_audit_checks_all_obligations_without_a_baseline(tmp_path, private):
    pair, *_ = _comparison(tmp_path, HISTORY_SOURCE, 'saved==1', private=private,
                           symbolic_initial=True, variable_queue=True, full_catalogue=True)
    audit, calls = _audit(pair)
    assert audit.status is AuditCheckStatus.AGREED
    assert calls == _all_phases(pair)


@pytest.mark.parametrize('spurious', [False, True])
def test_registered_start_done_source_domain_audits_both_valid_and_bad_artifacts(tmp_path, spurious):
    from behave.sim.core.observation_product import TransactionProduct
    from behave.sim.rtl.atomic_monitors import AtomicMonitorProduct, protocol_monitor_components
    from behave.sim.rtl.protocol_relation import ProtocolIntervalRelation
    from behave.sim.rtl.source_goals import manifest_of

    from .test_protocol_adapters import _contract as adapter_contract
    from .test_rtl_public_analysis_path import START_DONE_RTL, START_DONE_SPURIOUS_RTL

    source = START_DONE_SPURIOUS_RTL if spurious else START_DONE_RTL
    top = 'public_start_done_spurious' if spurious else 'public_start_done'
    machine, plan, _, template = _prepare(tmp_path, source.replace(top, 'M'))
    contract, steps = adapter_contract('start-done'), 5
    parts = ProtocolIntervalRelation(contract).parts
    _, components = protocol_monitor_components(contract,
        tuple((name, *part) for name, part in sorted(parts.items())), steps)
    relation = SourceTaskRelation(template, machine, contract,
        tuple(manifest_of(plan.declarations, filename='design.sv')),
        complete_state=True, occurrences=True,
        monitors=AtomicMonitorProduct(components, (), {}), transactions=TransactionProduct(contract))
    # No baseline state: physical registers are free, followed by declared reset.
    values = {name: 0 if field.input else None for name, field in machine.program.fields.items()}
    state = relation.initial_state(values)
    endpoint, = contract.inputs()
    queues = {endpoint.name: ((z3.BitVec('payload', 8),),)}
    slots = [api.EventSlot(reset=i == 0) for i in range(steps)]
    pair = relation.compare_orders(state.state, ProtocolBoundary.initial(contract), queues, slots, None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True,
        actions=relation.protocol.symbolic_actions(steps))
    audit, calls = _audit(pair)
    assert audit.status is AuditCheckStatus.AGREED, (audit.as_dict(), calls)
    assert calls == _all_phases(pair)
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    assert set(catalogue) <= pair.left.hits.keys()
    answers = {}
    for goal in catalogue:
        checked = z3_check((*pair.constraints, pair.left.hits[goal]), 30_000,
                           _owned_context=pair.left.alive.ctx)
        assert checked.answer in (SolverAnswer.SAT, SolverAnswer.UNSAT), (goal, checked.reason)
        answers[goal] = checked.answer
    assert len(answers) == len(catalogue) and SolverAnswer.SAT in answers.values()


def test_empty_source_domain_cannot_certify_order_independence(tmp_path):
    pair, *_ = _comparison(tmp_path, HISTORY_SOURCE, steps=1)
    audit, calls = _audit(pair, domain=(z3.BoolVal(False, ctx=pair.left.alive.ctx),))
    assert audit.status is AuditCheckStatus.REFUSED
    assert audit.kind == 'rtl-source-empty-domain'
    assert calls == ['feasibility']


@pytest.mark.parametrize('phase', ['feasibility', 'completeness', 'definedness', 'memory', 'confluence'])
def test_unresolved_source_obligation_stops_the_audit(tmp_path, phase):
    pair, *_ = _comparison(tmp_path, HISTORY_SOURCE, steps=1)
    audit, calls = _audit(pair, unknown=phase)
    assert audit.status is AuditCheckStatus.REFUSED
    assert audit.kind == 'rtl-source-domain-check-unresolved'
    assert phase in audit.why and 'budget exhausted' in audit.why
    assert calls[-1].split(':')[0] == phase


def test_source_audit_does_not_swallow_cancellation_or_accept_a_truthy_result(tmp_path):
    pair, *_ = _comparison(tmp_path, HISTORY_SOURCE, steps=1)

    def cancel(_phase, _assertions):
        raise TimeoutError('owning request deadline')

    with pytest.raises(TimeoutError, match='owning request deadline'):
        pair.audit(cancel)
    with pytest.raises(TypeError, match='exact solver result'):
        pair.audit(lambda _phase, _assertions: True)


def test_same_final_output_does_not_hide_order_sensitive_goal_hits(tmp_path):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output reg [1:0] y,input ordy);
      reg [1:0] saved;
      assign ir=!rst && ordy;
      always @(posedge clk) saved=a;
      always @(posedge clk) begin
        if(saved==1) y=2; else y=2;
        ov=iv;
      end
    endmodule'''
    pair, solver, queues, actions, goals, names = _comparison(tmp_path, source, 'saved==1', steps=1)
    solver.add(queues['in'][0][0] == 1, actions[0].offers['in'], actions[0].controls['ordy'])
    solver.push()
    solver.add(z3.Or(*(pair.left.state[names[n]] != pair.right.state[names[n]]
                       for n in ('saved', 'ov', 'y'))))
    assert solver.check() == z3.unsat
    solver.pop()
    solver.add(pair.different, pair.left.hits[goals[0]] != pair.right.hits[goals[0]])
    assert solver.check() == z3.sat
    assert any(z3.is_true(solver.model().eval(term))
               for name, term in pair.differences.items() if '.hits.' in name)
    audit, calls = _audit(pair)
    assert audit.status is AuditCheckStatus.REFUSED
    assert audit.kind == 'rtl-source-order-sensitive' and calls[-1].startswith('confluence:')


def test_reconvergent_nba_fanout_refuses_event_exactness_not_functional_correctness(tmp_path):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,ov,
      output [1:0] y,input ordy);
      reg busy;
      wire consumed,accepted;
      assign consumed=ov & ordy;
      assign ir=~busy | consumed;
      assign ov=busy;
      assign accepted=iv & ir;
      assign y=0;
      always @(posedge clk) busy<=1'b1;
    endmodule'''
    pair, solver, queues, actions, _goals, _names = _comparison(
        tmp_path, source, steps=1, full_catalogue=True)
    solver.add(queues['in'][0][0] == 0, actions[0].offers['in'], actions[0].controls['ordy'])
    solver.push()
    solver.add(z3.Or(*(term for name, term in pair.differences.items() if '.state.' in name)))
    assert solver.check() == z3.unsat
    solver.pop()
    solver.add(pair.different)
    assert solver.check() == z3.sat
    changed = [name for name, term in pair.differences.items()
               if z3.is_true(solver.model().eval(term))]
    assert changed and all('.hits.' in name or '.occurrences.' in name for name in changed)
    audit, _calls = _audit(pair)
    assert audit.kind == 'rtl-source-order-sensitive'
    assert 'first differing observation:' in audit.why
    assert 'not an artifact failure' in audit.why


def test_state_ordering_race_is_detected_without_a_coverage_goal(tmp_path):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output ov,output reg [1:0] y,input ordy);
      reg [1:0] saved;
      assign ir=!rst && ordy; assign ov=iv;
      always @(posedge clk) saved=a;
      always @(posedge clk) y=saved;
    endmodule'''
    pair, solver, queues, actions, _goals, names = _comparison(tmp_path, source, steps=1)
    solver.add(queues['in'][0][0] == 1, actions[0].offers['in'], actions[0].controls['ordy'],
               pair.different, pair.left.state[names['y']] != pair.right.state[names['y']])
    assert solver.check() == z3.sat


@pytest.mark.parametrize('declared_ov', [False, True])
def test_initial_process_race_keeps_the_same_physical_initial_state(tmp_path, declared_ov):
    from behave.sim.rtl.source_goals import manifest_of

    source = HISTORY_SOURCE.replace('reg [1:0] saved;',
        'reg [1:0] saved; initial begin saved=1; ov=0; y=0; end')
    if declared_ov:
        source = source.replace('output reg ov,', 'output reg ov=0,')
    machine, plan, _env, template = _prepare(tmp_path, source, initialization=True)
    contract, names = _simple_contract(), _names(machine)
    goals = tuple(manifest_of(plan.declarations, filename='design.sv'))
    relation = SourceTaskRelation(template, machine, contract, goals,
                                  complete_state=True, occurrences=True)
    values = {**dict.fromkeys(machine.program.fields, 0),
              names['saved']: 2, names['y']: 3, names['ov']: None if declared_ov else 1}
    initial = relation.initial_state(values)
    pair = relation.compare_orders(initial.state, ProtocolBoundary.initial(contract),
        {'in': ()}, [api.EventSlot(controls={'rst': 0, 'ordy': 0})], None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True)
    solver = z3.Solver()
    solver.add(*pair.constraints)
    assert solver.check() == z3.sat
    solver.push()
    # Same initial fields and no offered input on both sides: no difference can
    # be attributed to changing the domain or comparing two unrelated samples.
    state_changes = [term for name, term in pair.differences.items() if '.state.' in name]
    solver.add(z3.Or(*state_changes))
    assert solver.check() == z3.unsat
    solver.pop()
    solver.add(pair.different)
    assert solver.check() == (z3.unsat if declared_ov else z3.sat)
    if not declared_ov:
        changed = [name for name, term in pair.differences.items()
                   if z3.is_true(solver.model().eval(term))]
        assert any(name.startswith('startup.hits.') for name in changed)
        assert all('.hits.' in name or '.occurrences.' in name for name in changed)


def test_failed_history_compares_first_error_not_unconstrained_successor(tmp_path):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output ov,output [1:0] y,input ordy);
      assign ir=!rst && ordy; assign ov=iv; assign y=a;
      always @(posedge clk) if(iv && a==1) $fatal(1,"bad");
    endmodule'''
    pair, solver, queues, actions, goals, _storage_names = _comparison(tmp_path, source, 'iv &&', steps=2)
    solver.add(queues['in'][0][0] == 1, actions[0].offers['in'], actions[0].controls['ordy'])
    assert solver.check() == z3.sat
    solver.push()
    solver.add(z3.Or(pair.left.alive, pair.unfinished, z3.Not(pair.left.hits[goals[0]])))
    assert solver.check() == z3.unsat
    solver.pop()
    solver.add(pair.different)
    assert solver.check() == z3.unsat
    audit, calls = _audit(pair)
    assert audit.status is AuditCheckStatus.AGREED and calls == _all_phases(pair)


@pytest.mark.parametrize('private', [False, True])
def test_domain_audit_keeps_safety_counterexamples_queryable(tmp_path, private):
    from .test_rtl_source_monitors import UNSTABLE, _monitors

    machine, _, env, template = _prepare(tmp_path, UNSTABLE)
    contract, context = _simple_contract(), z3.Context() if private else None
    relation = SourceTaskRelation(template, machine, contract, (),
        context=context, complete_state=True, monitors=_monitors(contract))
    pair = relation.compare_orders(_initial(machine, env), ProtocolBoundary.initial(contract),
        {'in': tuple((z3.BitVecVal(value, 2, ctx=context),) for value in (1, 2))},
        [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':int(i == 1)}) for i in range(2)], None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True)
    audit, calls = _audit(pair)
    assert audit.status is AuditCheckStatus.AGREED and calls == _all_phases(pair)
    solver = z3.Solver(ctx=context)
    solver.add(*pair.constraints, pair.left.hits['monitor.sink-stability'])
    assert solver.check() == z3.sat  # An analyzable domain is not a passing artifact.


def test_goal_cone_cannot_masquerade_as_full_state_correspondence(tmp_path):
    machine, plan, env, template = _prepare(tmp_path, HISTORY_SOURCE)
    contract = _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (_goal(HISTORY_SOURCE, plan, 'saved==1'),))
    with pytest.raises(ValueError, match='complete state'):
        relation.compare_orders(_initial(machine, env), ProtocolBoundary.initial(contract),
            {'in':()}, [api.EventSlot(controls={'rst':0, 'ordy':1})], None,
            memory_state=MemoryEnvironmentState.initial((), None), startup=True)


@pytest.mark.parametrize('private', [False, True])
def test_order_comparison_keeps_unfinished_startup_in_its_domain(tmp_path, private):
    source = HISTORY_SOURCE.replace('endmodule',
        'wire [1:0] mirror; assign mirror=a; endmodule')
    machine, _, env, template = _prepare(tmp_path, source)
    # An intentionally inadequate test bound, not a new runtime limit.
    machine = replace(machine, event_bound=1)
    context = z3.Context() if private else None
    contract = _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (),
                                  context=context, complete_state=True)
    pair = relation.compare_orders(_initial(machine, env), ProtocolBoundary.initial(contract),
        {'in': ((z3.BitVecVal(1, 2, ctx=context),),)},
        [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})], None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True)
    solver = z3.Solver(ctx=context)
    solver.add(*pair.constraints)
    assert solver.check() == z3.sat  # A short bound must not erase the domain.
    solver.add(z3.Or(z3.Not(pair.unfinished),
        z3.Not(pair.left.unfinished), z3.Not(pair.right.unfinished),
        *(row['in'] for history in (pair.left, pair.right)
          for row in history.protocol.accepted)))
    assert solver.check() == z3.unsat
    audit, calls = _audit(pair)
    assert audit.status is AuditCheckStatus.REFUSED
    assert audit.kind == 'rtl-source-event-bound-incomplete'
    assert calls == ['feasibility', 'completeness']


@pytest.mark.parametrize('private', [False, True])
def test_complete_and_unfinished_orders_share_the_same_initial_domain(tmp_path, private):
    from .test_rtl_source_settling import _unfinished_prefixes

    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output ov,output reg [1:0] y,input ordy);
      reg [1:0] saved;
      assign ir=1; assign ov=0;
      always_comb saved=a;
      always_comb y=saved;
    endmodule'''
    machine, _, env, template = _prepare(tmp_path, source)
    machine = replace(machine, event_bound=4)
    values = {**dict.fromkeys(machine.program.fields, 0), _names(machine)['a']: 1}
    assert _unfinished_prefixes(machine, machine.boundary(values, startup=True), None, 4) == {False, True}
    context = z3.Context() if private else None
    contract = _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (),
                                  context=context, complete_state=True)
    pair = relation.compare_orders(_initial(machine, env, a=1), ProtocolBoundary.initial(contract),
        {'in': ()}, [api.EventSlot(controls={'rst':0, 'ordy':1})], None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True)
    solver = z3.Solver(ctx=context)
    solver.add(*pair.constraints, pair.left.startup.unfinished != pair.right.startup.unfinished)
    assert solver.check() == z3.sat
    solver.add(z3.Not(pair.unfinished))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
def test_order_comparison_keeps_input_dependent_unfinished_histories(tmp_path, monkeypatch, private):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output ov,output reg [1:0] y,input ordy);
      assign ir=1; assign ov=0;
      always @(a) begin if(a==1) y=3; else y=a; end
    endmodule'''
    machine, _, env, template = _prepare(tmp_path, source)
    original = type(machine).phase_event_bounds
    slot_number = 0

    def short_second_control(self, *args):
        nonlocal slot_number
        bounds = original(self, *args)
        slot_number += 1
        # Each independent history has three slots, sharing the same bound.
        return (1, *bounds[1:]) if slot_number % 3 == 2 else bounds

    monkeypatch.setattr(type(machine), 'phase_event_bounds', short_second_control)
    context = z3.Context() if private else None
    contract = _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (),
                                  context=context, complete_state=True)
    second = z3.BitVec('second_payload', 2, ctx=context)
    pair = relation.compare_orders(_initial(machine, env), ProtocolBoundary.initial(contract),
        {'in': ((z3.BitVecVal(0, 2, ctx=context),), (second,))},
        [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})]*3, None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True)
    solver = z3.Solver(ctx=context)
    solver.add(*pair.constraints)
    for value in range(4):
        solver.push()
        solver.add(second == value)
        assert solver.check() == z3.sat  # Do not silently restrict input to 0.
        solver.push()
        solver.add(pair.unfinished)
        assert solver.check() == (z3.sat if value else z3.unsat)
        solver.pop()
        solver.push()
        solver.add(pair.different)
        assert solver.check() == z3.unsat  # Agreement alone does not prove settling.
        solver.pop()
        solver.add(z3.Or(*(z3.Or(history.unfinished != (value != 0),
            z3.Not(history.alive), history.protocol.pointers[-1]['in'] != (1 if value else 2))
            for history in (pair.left, pair.right))))
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.mark.parametrize('private', [False, True])
def test_complete_array_storage_participates_in_order_comparison(tmp_path, private):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output reg [1:0] y,input ordy);
      reg [1:0] bank[0:1];
      assign ir=!rst && (!ov || ordy);
      always @(posedge clk) begin
        if(iv && ir) begin bank[a[0]]<=a; y<=bank[a[0]]; ov<=1; end
        else if(ordy) ov<=0;
      end
    endmodule'''
    machine, _plan, env, template = _prepare(tmp_path, source)
    context, contract = z3.Context() if private else None, _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (), context=context, complete_state=True)
    pair = relation.compare_orders(_initial(machine, env, bank=[0,0]), ProtocolBoundary.initial(contract),
        {'in':((z3.BitVec('first',2,ctx=context),), (z3.BitVec('second',2,ctx=context),))},
        [api.EventSlot(controls={'rst':0, 'ordy':0})]*2, None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True,
        actions=relation.protocol.symbolic_actions(2))
    solver = z3.Solver(ctx=context)
    solver.add(*pair.constraints)
    assert solver.check() == z3.sat
    solver.add(pair.different)
    assert solver.check() == z3.unsat
    assert any('.state.'+_names(machine)['bank'] in name for name in pair.differences)


@pytest.mark.parametrize('write', [False, True])
def test_external_memory_evidence_and_pending_responses_are_compared(tmp_path, write):
    source, contract = MEMORY_SOURCE, memory_contract()
    if write:
        source = source.replace('input ram_req_ready',
            'output ram_req_we,output [7:0] ram_req_wdata,input ram_req_ready').replace(
            'assign ram_req_addr=a;', 'assign ram_req_addr=a; assign ram_req_we=1; assign ram_req_wdata=201;')
        contract = api.TaskContract(contract.endpoints, contract.clocks,
            memories=(MemoryInterface('ram',2,8,'c',[MemoryPort('rw')]),))
    machine, plan, env, template = _prepare(tmp_path, source)
    context = z3.Context()
    relation = SourceTaskRelation(template, machine, contract,
        (_goal(source, plan, 'ram_resp_rdata==201'),), context=context, complete_state=True)
    environment = MemoryEnvironment([MemoryRun('ram', initial={1:201}, read_latency=2, ready={None:[1]*4})])
    pair = relation.compare_orders(_initial(machine, env), ProtocolBoundary.initial(contract),
        {'in':((z3.BitVec('address',2,ctx=context),),)},
        [api.EventSlot(controls={'rst':0, 'ordy':0})]*4, environment,
        memory_state=MemoryEnvironmentState.initial(contract.memories, environment), startup=True,
        actions=relation.protocol.symbolic_actions(4))
    solver = z3.Solver(ctx=context)
    solver.add(*pair.constraints)
    assert solver.check() == z3.sat
    solver.add(pair.different)
    assert solver.check() == z3.unsat
    assert any(name.startswith('memory.write_flags.') for name in pair.differences)
    assert any(name.startswith('memory.pending.') for name in pair.differences)


@pytest.mark.parametrize('private', [False, True])
def test_unencoded_array_domain_is_not_assumed_away_by_confluence(tmp_path, private):
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    from .test_rtl_source_scheduler import _lower

    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output ov,output [1:0] y,input ordy);
      reg [1:0] bank[0:1];
      assign ir=1; assign ov=iv; assign y=bank[a];
    endmodule'''
    machine, _ = _lower(tmp_path, source, defer_domains=True)
    assert machine.domain_checks
    env = {**machine.procedure.state_ranges, **machine.input_ranges}
    template = SourceIntervalTemplate(machine.procedure, env)
    context = z3.Context() if private else None
    contract = _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (),
                                  context=context, complete_state=True)
    cell = z3.BitVec('array_index', 2, ctx=context)
    pair = relation.compare_orders(_initial(machine, env, bank=[0, 0]),
        ProtocolBoundary.initial(contract), {'in': ((cell,),)},
        [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})], None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True)
    audit, calls = _audit(pair)
    assert audit.status is AuditCheckStatus.REFUSED
    assert audit.kind == 'rtl-source-operation-domain-unencoded'
    assert calls == ['feasibility', 'completeness', 'definedness']
    # An explicitly narrower declared domain can be checked independently;
    # the first audit must not impose this restriction itself.
    audit, calls = _audit(pair, domain=(z3.ULE(cell, 1),))
    assert audit.status is AuditCheckStatus.AGREED and calls == _all_phases(pair)


@pytest.mark.parametrize('private', [False, True])
def test_source_domain_checks_possible_memory_conflict_before_confluence(tmp_path, private):
    source = '''module M(input clk,rst,iv,input [1:0] a,output logic ir,
      output logic ov,output logic [1:0] y,input ordy,
      output logic ram__l_req_valid,ram__r_req_valid,
      output logic [1:0] ram__l_req_addr,ram__r_req_addr,
      output logic [1:0] ram__l_req_wdata,ram__r_req_wdata,
      input ram__l_req_ready,ram__r_req_ready);
      always_comb begin
        ir=1; ov=iv; y=a;
        ram__l_req_valid=iv; ram__r_req_valid=iv;
        ram__l_req_addr=a; ram__r_req_addr=1;
        ram__l_req_wdata=1; ram__r_req_wdata=2;
      end
    endmodule'''
    machine, _, env, template = _prepare(tmp_path, source)
    base, context = _simple_contract(), z3.Context() if private else None
    contract = api.TaskContract(base.endpoints, base.clocks,
        memories=(MemoryInterface('ram', 2, 2, 'c', [MemoryPort('w', 'l'), MemoryPort('w', 'r')]),))
    environment = MemoryEnvironment([MemoryRun('ram', initial={}, ready={'l':[1], 'r':[1]})])
    relation = SourceTaskRelation(template, machine, contract, (),
                                  context=context, complete_state=True)
    cell = z3.BitVec('write_address', 2, ctx=context)
    pair = relation.compare_orders(_initial(machine, env), ProtocolBoundary.initial(contract),
        {'in': ((cell,),)}, [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})], environment,
        memory_state=MemoryEnvironmentState.initial(contract.memories, environment), startup=True)
    audit, calls = _audit(pair)
    assert audit.status is AuditCheckStatus.REFUSED
    assert audit.kind == 'rtl-source-memory-conflict', (audit.as_dict(), calls)
    assert calls == ['feasibility', 'completeness', 'definedness', 'memory']
    audit, calls = _audit(pair, domain=(cell != 1,))
    assert audit.status is AuditCheckStatus.AGREED and calls == _all_phases(pair)


@pytest.mark.parametrize('has_counterexample', [False, True])
def test_proved_domain_facts_strengthen_prefix_without_hiding_disagreement(has_counterexample):
    from types import SimpleNamespace

    from behave.sim.rtl.source_confluence import SourceOrderComparison

    pending, difference = z3.Bools('prefix_pending prefix_difference')
    constraints = (z3.Not(pending),)
    side = SimpleNamespace(alive=z3.BoolVal(True), unfinished=pending,
        domain_faults={}, constraints=constraints,
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=()),
            slots=(SimpleNamespace(phases=(SimpleNamespace(constraints=constraints),)),)))
    pair = SourceOrderComparison(side, side, {'startup.state.x':
        z3.Or(pending, difference if has_counterexample else z3.BoolVal(False))})
    calls = []

    def solve(phase, assertions):
        calls.append(phase)
        if phase.endswith(':prefix'):
            # The later source equation is absent; only proved full-domain
            # facts and this disagreement remain. An independent difference
            # must still produce SAT, followed by the original-domain check.
            assert pair._prefix_constraints('confluence:startup', pair.constraints) == ()
            assert len(assertions) == 4
        return z3_check(assertions, 30_000, _owned_context=pending.ctx)

    audit = pair.audit(solve, prefix_proofs=True)
    assert calls[:4] == ['feasibility', 'completeness', 'definedness', 'memory']
    assert calls[4] == 'confluence:startup:prefix'
    if has_counterexample:
        assert calls[5:] == ['confluence:startup']
        assert audit.kind == 'rtl-source-order-sensitive'
    else:
        assert len(calls) == 5
        assert audit.status is AuditCheckStatus.AGREED


def _small_product(differences, *, constraints=()):
    from types import SimpleNamespace

    from behave.sim.rtl.source_confluence import SourceOrderComparison

    history = SimpleNamespace(alive=z3.BoolVal(True), unfinished=z3.BoolVal(False),
        constraints=constraints, domain_faults={},
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=())))
    return SourceOrderComparison(history, history, differences)


@pytest.mark.parametrize('differences', [
    {},
    {'startup.alive': z3.Bool('startup_difference'),
     'slot[0].phase[0].state.x': z3.Bool('first_difference'),
     'slot[12].phase[2].occurrences.x': z3.Bool('last_difference'),
     'protocol.accepted': z3.Bool('protocol_difference'),
     'memory.pending': z3.Bool('memory_difference'),
     'future_unknown_component': z3.Bool('unknown_difference')},
])
def test_confluence_partition_preserves_the_entire_disagreement(differences):
    pair = _small_product(differences)
    solver = z3.Solver()
    solver.add(pair.different != z3.Or(*(term for _, term in pair._confluence_obligations())))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('label', ['slot[9].phase[2].state.x', 'monitor.hits.bad',
                                 'transactions.states.x', 'memory.write_values.x',
                                 'terminal_unresolved.x', 'future_unknown_component'])
def test_late_disagreement_cannot_be_assumed_equal(label):
    pair = _small_product({'startup.alive': z3.BoolVal(False), label: z3.BoolVal(True)})
    audit, calls = _audit(pair)
    assert audit.kind == 'rtl-source-order-sensitive'
    assert calls[:5] == ['feasibility', 'completeness', 'definedness', 'memory', 'confluence:startup']
    assert calls[-1] in audit.why


def test_late_unknown_does_not_publish_partial_domain_proof():
    pair = _small_product({'startup.alive': z3.BoolVal(False),
                          'slot[1].phase[0].state.x': z3.BoolVal(False),
                          'protocol.accepted': z3.BoolVal(False)})
    audit, calls = _audit(pair, unknown='confluence:slot[1]')
    assert audit.kind == 'rtl-source-domain-check-unresolved'
    assert calls[-1] == 'confluence:slot[1]'
    assert 'confluence:product' not in calls


def test_confluence_lemmas_do_not_leak_into_another_domain():
    value = z3.Bool('declared_domain_value')
    pair = _small_product({'startup.alive': value})
    admitted, _ = _audit(pair, domain=(z3.Not(value),))
    refused, _ = _audit(pair)
    assert admitted.status is AuditCheckStatus.AGREED
    assert refused.kind == 'rtl-source-order-sensitive'


@pytest.mark.parametrize('side', ['left', 'right'])
def test_mismatched_startup_images_are_never_silently_omitted(tmp_path, side):
    from behave.sim.rtl.source_confluence import compare_histories

    pair, *_ = _comparison(tmp_path, HISTORY_SOURCE, steps=1)
    histories = {'left': pair.left, 'right': pair.right}
    histories[side] = replace(histories[side], startup=None)
    with pytest.raises(ValueError, match='same startup boundary'):
        compare_histories(histories['left'], histories['right'], ())


@pytest.mark.parametrize('side', ['left', 'right'])
@pytest.mark.parametrize('image', ['responses', 'delays'])
def test_unmatched_memory_response_channels_are_never_silently_omitted(tmp_path, side, image):
    from behave.sim.rtl.source_confluence import compare_histories

    pair, *_ = _comparison(tmp_path, HISTORY_SOURCE, steps=1)
    histories = {'left': pair.left, 'right': pair.right}
    getattr(histories[side].source.memory, image)['unmatched_port'] = ()
    with pytest.raises(ValueError, match='same memory response channels'):
        compare_histories(histories['left'], histories['right'], ())


@pytest.mark.cosim
def test_symbolic_environment_witness_replays_native_history_hits(tmp_path):
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.initial_state import RTLInitialState
    from behave.sim.rtl.native_observation import _read_intervals
    from behave.sim.rtl.source_goals import manifest_of

    machine, plan, env, template = _prepare(tmp_path, HISTORY_SOURCE)
    catalogue, contract = manifest_of(plan.declarations, filename='design.sv'), _simple_contract()
    goal = _goal(HISTORY_SOURCE, plan, 'saved==1')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    actions = relation.protocol.symbolic_actions(4)
    queues = {'in':((z3.BitVec('first',2),), (z3.BitVec('second',2),))}
    encoded = relation.encode(_initial(machine, env), ProtocolBoundary.initial(contract), queues,
        [api.EventSlot(controls={'rst':0, 'ordy':0})]*4, None,
        memory_state=MemoryEnvironmentState.initial((), None), startup=True, actions=actions)
    solver = z3.Solver()
    # Force a real blocked-output interval followed by resumption. Clock/reset
    # remain declared, while the solver owns queue payloads and release times.
    solver.add(*encoded.constraints, encoded.hits[goal], encoded.protocol.pointers[-1]['in']==2,
        z3.Not(actions[1].controls['ordy']), actions[2].controls['ordy'], actions[3].controls['ordy'])
    assert solver.check() == z3.sat
    model = solver.model()
    values = {name:[tuple(model.eval(value, model_completion=True).as_long() for value in row)
                    for row in rows] for name, rows in queues.items()}
    slots = [api.EventSlot(offers=tuple(name for name, term in action.offers.items()
                                       if z3.is_true(model.eval(term, model_completion=True))),
        controls={'rst':0, **{pin:int(z3.is_true(model.eval(term, model_completion=True)))
                             for pin, term in action.controls.items()}}) for action in actions]
    stimulus = api.Stimulus(values, slots)
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    widths, initial = {'saved':2, 'ov':1, 'y':2}, {'saved':0, 'ov':0, 'y':0}
    trace = artifact.exact_trace(contract, stimulus, observe_names=tuple(widths),
        state_names=tuple(widths), state_widths=widths,
        initial_state=RTLInitialState.from_values(artifact.identity, widths, initial))
    native = list(_read_intervals(thaw_record_value(trace.source_evidence), stimulus,
                                  catalogue, expected_plan=None, prefix=False))
    phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
    assert any(goal in observed.goals for _label, observed in native)
    for phase, (_label, observed) in zip(phases, native, strict=True):
        assert {g for g, term in phase.hits.items() if z3.is_true(model.eval(term))} == set(observed.goals)
        assert {key for key, term in phase.occurrences.items() if z3.is_true(model.eval(term))} == set(observed.occurrences)
    assert len(trace.records) == 4
