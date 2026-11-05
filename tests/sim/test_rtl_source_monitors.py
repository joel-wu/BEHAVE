"""Whole-source histories use the existing safety state and observation phases."""

from dataclasses import replace

import pytest
import z3

from behave.sim import api
from behave.sim.core.ready_valid_product import ConcreteAlgebra
from behave.sim.core.reset_monitor import ResetRestorationConfig, ResetRestorationProvider
from behave.sim.rtl.atomic_monitors import AtomicMonitorProduct, protocol_monitor_components
from behave.sim.rtl.protocol_relation import ProtocolIntervalRelation
from behave.sim.rtl.source_confluence import compare_histories
from behave.sim.rtl.source_task import SourceTaskRelation

from .test_rtl_source_history import HISTORY_SOURCE, _encode, _initial, _simple_contract
from .test_rtl_source_memory_relation import _prepare
from .test_rtl_source_scheduler import _names

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('terminal', [False, True])
def test_monitor_prefix_reuses_source_control_boundaries(tmp_path, private, terminal):
    from behave.sim.symbolic.exact_solve import z3_check
    from behave.sim.core.audit import AuditCheckStatus
    from behave.sim.core.query import SolverAnswer
    from .test_rtl_source_request_domain import _request

    source = HISTORY_SOURCE.replace('saved<=a;',
        'saved<=a; if(a==3) $fatal(1,"stop");') if terminal else HISTORY_SOURCE
    machine, _, _, template = _prepare(tmp_path, source, initialization=True)
    contract = _simple_contract(reset_policy='clears')
    relation = SourceTaskRelation(template, machine, contract, (), complete_state=True,
        context=z3.Context() if private else None, monitors=_monitors(contract))
    request, _, _ = _request(relation, {'saved': 2, 'ov': 1, 'y': 2})
    pair = relation.compare_request(request)
    calls = []

    # Boundary links are definitions, never assumptions that execution lives
    # or completes. They hold even before any source/domain premise is proved.
    definitions = tuple(pair._control_definitions())
    assert definitions
    solver = z3.Solver(ctx=relation.context)
    solver.add(z3.Not(z3.And(*definitions)))
    assert solver.check() == z3.unsat

    def solve(label, roots):
        result = z3_check(roots, 30_000, _owned_context=relation.context)
        calls.append((label, result.answer))
        return result

    assert pair.audit(solve, prefix_proofs=True).status is AuditCheckStatus.AGREED
    monitor = [(label, answer) for label, answer in calls
               if label.startswith('confluence:monitor[')]
    assert monitor
    assert all(label.endswith(':prefix') and answer is SolverAnswer.UNSAT
               for label, answer in monitor), monitor
    assert [(label, answer) for label, answer in calls if label.startswith('confluence:protocol')] == [
        ('confluence:protocol:prefix', SolverAnswer.UNSAT)]

UNSTABLE = '''module M(input clk,rst,iv,input [1:0] a,output ir,
  output ov,output [1:0] y,input ordy);
  assign ir=1; assign ov=iv; assign y=a;
endmodule'''


def _monitors(contract, *, fields=None):
    parts = ProtocolIntervalRelation(contract).parts
    _, components = protocol_monitor_components(contract,
        tuple((domain, *part) for domain, part in sorted(parts.items())), 4)
    fields = {} if fields is None else fields
    resets = (('c', ResetRestorationConfig(1, tuple(fields))),) if fields else ()
    return AtomicMonitorProduct(components, resets, fields)


def _truth(model, term):
    value = model.eval(term, model_completion=True)
    return z3.is_true(value) if z3.is_bool(value) else value.as_long()


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('changed', [False, True])
def test_monitor_samples_narrow_signed_carriers_as_complete_physical_payloads(private, changed):
    from types import SimpleNamespace

    context = z3.Context() if private else None
    boolean = lambda value: z3.BoolVal(value, ctx=context)
    relation = object.__new__(SourceTaskRelation)
    relation.context, relation.contract = context, _simple_contract(reset_policy='clears')
    relation.protocol = ProtocolIntervalRelation(relation.contract, context=context)
    relation.monitors = _monitors(relation.contract)
    relation.monitor_fields = {}
    relation.source = SimpleNamespace(ports={'ov': 'ov', 'y': 'y'})
    frames = []
    for payload in (-1, 0 if changed else -1):
        # A one-bit signed mathematical carrier represents -1; the physical
        # two-bit output is 3. Its payload must remain visible during stalls.
        state = {'ov': z3.BitVecVal(1, 2, ctx=context),
                 'y': z3.BitVecVal(payload, 1, ctx=context)}
        sample = SimpleNamespace(state=state, continuable=boolean(True))
        frames.append(SimpleNamespace(phases=(sample,), state=state, continuable=boolean(True)))
    protocol = SimpleNamespace(
        accepted=({'in': boolean(True)}, {'in': boolean(False)}),
        emitted=({'out': boolean(False)}, {'out': boolean(True)}),
        drives=({'ordy': boolean(False)}, {'ordy': boolean(True)}))
    state = relation.monitors.initial(relation.protocol.algebra, relation._monitor_word)
    hits, roots = [], []
    for i, frame in enumerate(frames):
        state, observed, _, _, equations = relation._monitor_step(state, frame, i,
            api.EventSlot(ticks=('c',)), {**protocol.accepted[i], **protocol.emitted[i]},
            protocol.drives[i], {'c': boolean(False)})
        hits.append(observed)
        roots.extend(equations)
    solver = z3.Solver(ctx=context)
    solver.add(*roots)
    assert solver.check() == z3.sat
    assert _truth(solver.model(), hits[-1]['monitor.sink-stability']) is changed
    solver.add(hits[-1]['monitor.sink-stability'] != boolean(changed))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('policy', [None, 'clears', 'keeps'])
def test_goal_projection_preserves_domain_owners(policy):
    product = _monitors(_simple_contract(reset_policy=policy), fields={'register': 4})
    empty = product.project(())
    assert bool(empty.protocol) is (policy is None)
    assert empty.resets == () and not empty.state_widths
    safety = product.project(('monitor.sink-stability',))
    assert safety.protocol == tuple(replace(component, goals=('monitor.sink-stability',))
                                    for component in product.protocol)
    reset = product.project((ResetRestorationProvider.violation_goal('c'),))
    assert reset.resets == product.resets and reset.state_widths == product.state_widths
    scenario_goal = ResetRestorationProvider.scenario_goal('c')
    scenario = product.project((scenario_goal,))
    assert scenario.resets == (('c', ResetRestorationConfig(1, ())),)
    assert not scenario.state_widths
    assert scenario.reset_observations == {scenario_goal}
    assert scenario.project((scenario_goal,)) == scenario
    with pytest.raises(ValueError, match='discarded observations'):
        scenario.project((ResetRestorationProvider.violation_goal('c'),))


def test_goal_projection_cannot_remove_identifier_legality():
    product = _monitors(_simple_contract(reset_policy='clears'))
    component = product.protocol[0]
    identified = replace(component, config=replace(component.config, identifier_index=0))
    product = replace(product, protocol=(identified,))
    assert product.project(()).protocol == (replace(identified, goals=()),)


@pytest.mark.parametrize('private', [False, True])
def test_reset_scenario_projection_matches_symbolic_multiclock_history(private):
    from behave.sim.rtl.task_symbolic import Z3Algebra

    context = z3.Context() if private else None
    algebra = Z3Algebra(context)
    full = AtomicMonitorProduct((),
        (('a', ResetRestorationConfig(2, ('x',))),
         ('b', ResetRestorationConfig(1, ('y',)))), {'x': 8, 'y': 5})
    goals = {ResetRestorationProvider.scenario_goal(domain) for domain in ('a', 'b')}
    projected = full.project(goals)
    assert not projected.state_widths
    def word(value, width):
        return value if z3.is_expr(value) else z3.BitVecVal(value, width, ctx=context)
    left, right = (product.initial(algebra, word) for product in (full, projected))
    differences, scenarios, violations = [], [], []
    for slot in range(8):
        event = api.EventSlot(ticks=('a', 'b') if slot % 2 == 0 else ('a',))
        options = dict(algebra=algebra, boolean=algebra.bool, word=word,
            completed=z3.Bool(f'completed_{slot}', ctx=context),
            reset_terms={domain: z3.Bool(f'reset_{domain}_{slot}', ctx=context) for domain in ('a', 'b')})
        successor = {name: z3.BitVec(f'value_{name}_{slot}', width, ctx=context)
                     for name, width in full.state_widths.items()}
        left, expected = full.step(left, slot, event, {}, {}, successor, **options)
        right, actual = projected.step(right, slot, event, {}, {}, {}, **options)
        assert set(actual) == goals
        differences.extend(actual[goal] != expected[goal] for goal in goals)
        scenarios.extend(actual.values())
        violations.extend(expected[ResetRestorationProvider.violation_goal(domain)] for domain in ('a', 'b'))
        for domain in ('a', 'b'):
            assert right.reset[domain].reference == ()
            differences.extend(getattr(left.reset[domain], field) != getattr(right.reset[domain], field)
                for field in ('reference_valid', 'reference_readable', 'seen_non_reset', 'reset_streak'))
    solver = z3.Solver(ctx=context)
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat
    # The oracle includes real scenario and bad-reset histories. Projection
    # does not turn an absent violation observation into a false proof.
    for possible in (scenarios, violations):
        solver = z3.Solver(ctx=context)
        solver.add(z3.Or(*possible))
        assert solver.check() == z3.sat


@pytest.mark.parametrize('policy', [None, 'clears', 'keeps'])
@pytest.mark.parametrize('private', [False, True])
def test_reset_preserves_declared_requests_and_exposes_undefined_domain(tmp_path, policy, private):
    # Accept, reset before the response, then keep quiet. No RTL deadlock or
    # wall-clock cutoff is needed to expose this finite obligation.
    source = UNSTABLE.replace('assign ov=iv;', 'assign ov=0;')
    machine, _, env, template = _prepare(tmp_path, source)
    base = _simple_contract()
    contract = api.TaskContract([
        api.Endpoint.from_dict({**base.inputs()[0].as_dict(),
                               'response_deadline': 1, 'reset_policy': policy}),
        *base.outputs()], base.clocks)
    context = z3.Context() if private else None
    relation = SourceTaskRelation(template, machine, contract, (), context=context,
                                  monitors=_monitors(contract))
    slots = [api.EventSlot(offers=('in',) if i == 0 else (), reset=i == 1,
                          controls={'rst': int(i == 1), 'ordy': 1}) for i in range(4)]
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(1,)], startup=True)
    solver = z3.Solver(ctx=context)
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat  # Undefined histories must remain in the domain.
    model = solver.model()
    fault = encoded.domain_faults['monitor.reset-policy-unresolved[c][out]'] if policy is None else z3.BoolVal(False, ctx=context)
    assert _truth(model, fault) is (policy is None)
    assert _truth(model, encoded.hits['monitor.response-deadline']) is (policy != 'clears')
    assert _truth(model, encoded.monitor_states[2].protocol[('c', 'out')].pending[0]) is (policy != 'clears')
    solver.add(fault != (policy is None))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_symbolic_ready_remains_free_and_stability_uses_preclock_payload(tmp_path, private_context):
    machine, _, env, template = _prepare(tmp_path, UNSTABLE)
    contract = _simple_contract()
    context = z3.Context() if private_context else None
    monitors = _monitors(contract)
    # No source goals or full-state option: monitor reads themselves close the cone.
    relation = SourceTaskRelation(template, machine, contract, (), context=context, monitors=monitors)
    actions = list(relation.protocol.symbolic_actions(2))
    ready = actions[0].controls['ordy']
    actions = [replace(action, offers={'in': z3.BoolVal(True, ctx=context)},
                       controls={'ordy': ready if i == 0 else z3.BoolVal(True, ctx=context)})
               for i, action in enumerate(actions)]
    slots = [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})]*2
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(1,), (2,)],
                      actions=actions, startup=True)
    solver = z3.Solver(ctx=context)
    solver.add(*encoded.constraints)
    for first_ready in (False, True):
        solver.push()
        solver.add(ready == first_ready)
        assert solver.check() == z3.sat
        model = solver.model()
        hit = encoded.hits['monitor.sink-stability']
        assert _truth(model, hit) is (not first_ready)
        solver.push()
        solver.add(hit == first_ready)
        assert solver.check() == z3.unsat
        solver.pop()
        # All latent monitor fields and per-slot hits agree with concrete rules.
        concrete = monitors.initial(ConcreteAlgebra, lambda value, width: bool(value) if width == 1 else value)
        for i, data in enumerate((1, 2)):
            event = api.EventSlot(ticks=('c',), controls={'ordy':int(first_ready) if i == 0 else 1})
            concrete, hits = monitors.step(concrete, i, event,
                {'in': True, 'out': first_ready if i == 0 else True},
                {'ov':True, 'y':data}, {}, algebra=ConcreteAlgebra, boolean=bool, word=lambda value, _: value)
            assert concrete.image() == encoded.monitor_states[i+1].image(lambda value, m=model: _truth(m, value))
            assert hits == {key:_truth(model, value) for key, value in encoded.monitor_hits[i].items()}
        solver.pop()


@pytest.mark.parametrize('correct_reset', [False, True])
def test_reset_restoration_is_history_dependent_and_uses_postslot_state(tmp_path, correct_reset):
    source = HISTORY_SOURCE if correct_reset else HISTORY_SOURCE.replace('saved<=0;', 'saved<=saved;')
    machine, _, env, template = _prepare(tmp_path, source)
    contract, names = _simple_contract(), _names(machine)
    relation = SourceTaskRelation(template, machine, contract, (),
        monitors=_monitors(contract, fields={names['saved']:2}))
    slots = [api.EventSlot(offers=('in',), reset=i!=1,
                          controls={'rst':int(i!=1), 'ordy':1}) for i in range(3)]
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(1,)], startup=True)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    scenario = ResetRestorationProvider.scenario_goal('c')
    violation = ResetRestorationProvider.violation_goal('c')
    solver.add(z3.Or(z3.Not(encoded.hits[scenario]), encoded.hits[violation] != (not correct_reset)))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('phase', [0, 1, 2])
def test_bound_cutoff_preserves_completed_sample_but_stops_future_monitors(tmp_path, monkeypatch, phase):
    source = HISTORY_SOURCE.replace('saved<=0;', 'saved<=saved;').replace(
        'endmodule', 'reg [1:0] tail; always @(negedge clk) tail<=saved; endmodule')
    machine, _, env, template = _prepare(tmp_path, source)
    original = type(machine).phase_event_bounds
    slot_index = 0

    def short_third_slot(self, *args):
        nonlocal slot_index
        bounds = list(original(self, *args))
        slot_index += 1
        if slot_index == 3:
            bounds[phase] = 1
        return tuple(bounds)

    monkeypatch.setattr(type(machine), 'phase_event_bounds', short_third_slot)
    # This fixture tests when a declared clearing reset is sampled, not an
    # unspecified request-ownership policy.
    contract, names = _simple_contract(reset_policy='clears'), _names(machine)
    relation = SourceTaskRelation(template, machine, contract, (), complete_state=True,
        monitors=_monitors(contract, fields={names['saved']:2}))
    slots = [api.EventSlot(offers=('in',), reset=i>=2 or i==0,
                          controls={'rst':int(i>=2 or i==0), 'ordy':1}) for i in range(5)]
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(1,)],
                      startup=True, require_quiescence=False)
    solver = z3.Solver()
    solver.add(*encoded.constraints, encoded.unfinished)
    assert solver.check() == z3.sat  # The unfinished domain was not filtered out.
    model = solver.model()
    before = encoded.monitor_states[2].image(lambda value: _truth(model, value))
    after = encoded.monitor_states[3].image(lambda value: _truth(model, value))
    assert before['monitor.reset-restoration'] == after['monitor.reset-restoration']
    if phase == 0:
        assert before['monitor.ready-valid'] == after['monitor.ready-valid']
    else:
        assert _truth(model, encoded.protocol.pointers[3]['in']) == 1
        # The reset was sampled before the unfinished clock/return interval.
        assert after['monitor.ready-valid']['c']['out']['accepted_count'] == 0
    for state in encoded.monitor_states[4:]:
        assert state.image(lambda value: _truth(model, value)) == after
    assert not any(_truth(model, value) for row in encoded.monitor_hits[3:] for value in row.values())
    violation = ResetRestorationProvider.violation_goal('c')
    solver.add(encoded.hits[violation])
    assert solver.check() == z3.unsat


def test_order_audit_includes_latent_monitor_fields_even_if_hits_are_equal(tmp_path):
    machine, _, env, template = _prepare(tmp_path, UNSTABLE)
    contract = _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (), complete_state=True,
                                  monitors=_monitors(contract))
    encoded = _encode(relation, _initial(machine, env), contract,
                      [api.EventSlot(controls={'rst':0, 'ordy':1})], [], startup=True)
    final = encoded.monitor_states[-1]
    states = dict(final.protocol)
    key = next(iter(states))
    states[key] = replace(states[key], stalled=z3.Not(states[key].stalled))
    changed = replace(encoded, monitor_states=(*encoded.monitor_states[:-1], replace(final, protocol=states)))
    audit = compare_histories(encoded, changed, tuple(machine.program.fields))
    solver = z3.Solver()
    solver.add(*audit.constraints, audit.different)
    assert solver.check() == z3.sat
    assert any('monitor.states' in name and _truth(solver.model(), value)
               for name, value in audit.differences.items())
    with pytest.raises(ValueError, match='monitor boundary'):
        _encode(relation, _initial(machine, env), contract,
                [api.EventSlot(controls={'rst':0, 'ordy':1})], [])


def test_terminal_failure_keeps_prior_safety_hit_and_reset_cannot_erase_it(tmp_path):
    source = UNSTABLE.replace('endmodule', 'always @(posedge clk) if(a==2) $fatal(1,"stop"); endmodule')
    machine, _, env, template = _prepare(tmp_path, source)
    contract = _simple_contract()
    relation = SourceTaskRelation(template, machine, contract, (), monitors=_monitors(contract))
    slots = [api.EventSlot(offers=('in',), reset=i==2,
                          controls={'rst':int(i==2), 'ordy':0}) for i in range(4)]
    encoded = _encode(relation, _initial(machine, env), contract, slots, [(1,), (2,), (3,)],
                      startup=True, require_quiescence=False)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    model = solver.model()
    assert not _truth(model, encoded.alive)
    assert not _truth(model, encoded.unfinished)
    assert _truth(model, encoded.hits['monitor.sink-stability'])
    stopped = encoded.monitor_states[2].image(lambda value: _truth(model, value))
    assert stopped['monitor.ready-valid']['c']['out']['accepted_count'] == 2
    for state in encoded.monitor_states[3:]:
        assert state.image(lambda value: _truth(model, value)) == stopped
    solver.add(z3.Or(*(value for row in encoded.monitor_hits[2:] for value in row.values())))
    assert solver.check() == z3.unsat
