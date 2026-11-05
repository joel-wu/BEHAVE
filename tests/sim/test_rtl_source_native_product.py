"""Native protocol/monitor products cannot be replaced by matching outputs."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import z3

from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.observation_product import TransactionProduct
from behave.sim.core.refusal import Refused
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.observation_audit import compare_native_history, native_protocol_history
from behave.sim.rtl.protocol_relation import SymbolicRequest
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_task import SourceTaskRelation
from behave.sim.rtl.task_request import ReadyValidRequestCodec

from .test_rtl_source_history import HISTORY_SOURCE
from .test_rtl_source_memory_relation import _prepare
from .test_rtl_source_monitors import _monitors
from .test_rtl_source_native_audit import _audit
from .test_rtl_source_scheduler import _names
from .test_rtl_source_transactions import _contract

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver, pytest.mark.cosim]


@pytest.mark.parametrize('phase', ['startup', 'control', 'clock', 'advance'])
@pytest.mark.parametrize('member', [False, True])
def test_terminal_native_prefix_retains_actual_transfers_and_stutters(tmp_path, phase, member):
    from behave.sim.symbolic.exact_solve import z3_check
    from behave.sim.core.audit import AuditedWitness
    from behave.sim.core.failure import RunFailure
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.rtl.native_events import NativeEventPlan
    from behave.sim.rtl.observation_binding import ObservationBinding
    from behave.sim.rtl.source_relation import SourceObservation
    from behave.sim.rtl.source_trace import read_failed_source_history, read_source_history
    from .test_rtl_source_history import _simple_contract

    trigger = {'startup': 'always_comb if(!rst && saved==0)',
               'control': 'always_comb if(!rst && iv && a==2)',
               'clock': 'always @(posedge clk) if(!rst && iv && a==2)',
               'advance': 'always @(negedge clk) if(!rst && iv && a==2)'}[phase]
    source = HISTORY_SOURCE.replace('endmodule', f'{trigger} $fatal(1,"stop"); endmodule')
    machine, plan, _, template = _prepare(tmp_path, source, initialization=True)
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    contract = _simple_contract(reset_policy='clears')
    names = _names(machine)
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
        complete_state=True, occurrences=True,
        monitors=_monitors(contract, fields={names['saved']: 2}), transactions=TransactionProduct(contract))
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact.identity)
    request = SymbolicRequest(ReadyValidRequestCodec(contract, 2, 1, initial=layout), 6)
    encoded = relation.encode_request(request, initial, binding)
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, request.lengths['in'] == 2,
        request.queues['in'][0][0] == 1, request.queues['in'][1][0] == 2,
        *(term == 0 for term in binding.values()))
    for i in range(6):
        solver.add(request.resets['c'][i] == (i == 0),
                   request.actions[i].controls['ordy'], request.actions[i].offers['in'] == (i > 0))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    stimulus = request.codec.decode(rows)
    with pytest.raises(Refused) as stopped:
        artifact.exact_trace(contract, stimulus, observe_names=('y', 'ov', 'ir'),
            state_names=tuple(layout.widths), state_widths=dict(layout.widths),
            startup_state=layout.decode(rows))
    failure = RunFailure.from_dict(thaw_record_value(stopped.value.refusal.detail['failure']))
    assert failure.kind == 'rtl-source-failure', failure.as_dict()
    assert failure.reason.detail['phase'] == phase
    native_plan = NativeEventPlan.from_evidence(thaw_record_value(failure.reason.detail['source_evidence']['plan']))
    observation = ObservationBinding(artifact.identity, contract.digest, catalogue.digest(),
                                     native_plan.identity, '1'*64)
    comparison = compare_native_history(relation, request, encoded, rows, failure, catalogue,
                                        observation=observation)
    # Before a fatal event, independent processes may legally execute in
    # different orders. The production source-cover path checks membership of
    # the actual native ordering, not that every ordering has identical hits.
    audited = comparison.admit_member(lambda _, roots: z3_check(roots, 30_000))
    assert audited.status is AuditCheckStatus.AGREED, audited.as_dict()
    # Fixed inputs need no fabricated native samples. The same complete
    # membership and negative controls below also apply to this smaller image.
    original_image = comparison.native_image
    original_interval = relation.source.relation
    if member:
        from behave.sim.rtl.source_direct import NativeMemberSourceCompiler

        relation.source.relation = replace(relation.source.relation,
            direct=NativeMemberSourceCompiler(machine))
    encoded = relation.encode_request(request.fixed(rows), initial, binding)
    comparison = compare_native_history(relation, request, encoded, rows, failure, catalogue,
                                        observation=observation)
    assert comparison.native_image == original_image
    audited = comparison.admit_member(lambda _, roots: z3_check(roots, 30_000))
    if member and audited.status is AuditCheckStatus.INCONSISTENT:
        # A single legal order need not reproduce the native startup order.
        # Its miss is not a negative result: the complete fallback must agree.
        relation.source.relation = original_interval
        encoded = relation.encode_request(request.fixed(rows), initial, binding)
        comparison = compare_native_history(relation, request, encoded, rows, failure, catalogue,
                                            observation=observation)
        assert comparison.native_image == original_image
        audited = comparison.admit_member(lambda _, roots: z3_check(roots, 30_000))
    assert audited.status is AuditCheckStatus.AGREED, audited.as_dict()
    witness = AuditedWitness(domain='terminal-native-test', inputs=rows, **comparison.native_image)
    assert witness.terminal_error.startswith('rtl-source-failure:')
    assert len(witness.states) == 7 and len(witness.outputs) == 6
    stopped_at = next(i for i, state in enumerate(witness.states) if 'terminal.error' in state)
    assert all(state == witness.states[stopped_at] for state in witness.states[stopped_at:])
    assert all(not batch for batch in witness.outputs[stopped_at:])
    if phase in {'clock', 'advance'}:
        assert witness.outputs[stopped_at-1] == ({'out': {'y': 1}},)
    elif phase == 'startup':
        assert stopped_at == 0 and witness.initial_hits and not any(witness.hit_sets)
    cumulative = compare_native_history(relation, request, encoded, rows, failure, catalogue,
        observation=observation, observation_scope=SourceObservation.GOAL_PREFIX)
    assert cumulative.admit_member(lambda _, roots: z3_check(roots, 30_000)).status is AuditCheckStatus.AGREED
    other = AuditedWitness(domain=witness.domain, inputs=rows, **cumulative.native_image)
    assert (other.outputs, other.hits, other.terminal_error) == (witness.outputs, witness.hits, witness.terminal_error)

    # Positive source events alone cannot turn a still-live model into a
    # terminal witness, even when every recorded failure bit is unchanged.
    def live(group, index):
        phases = list(group.phases)
        phases[index] = replace(phases[index], alive=z3.BoolVal(True),
                               error=z3.BitVecVal(0, phases[index].error.size()))
        return replace(group, phases=tuple(phases))
    if phase == 'startup':
        wrong = replace(encoded, startup=live(encoded.startup, -1))
    else:
        slots = list(encoded.source.slots)
        stopped_slot = failure.reason.detail['completed_steps']
        slots[stopped_slot] = live(slots[stopped_slot], ('control', 'clock', 'advance').index(phase))
        wrong = replace(encoded, source=replace(encoded.source, slots=tuple(slots)))
    rejected = compare_native_history(relation, request, wrong, rows, failure, catalogue,
                                      observation=observation)
    assert rejected.admit_member(lambda _, roots: z3_check(roots, 30_000)).status is AuditCheckStatus.INCONSISTENT
    trace, _ = read_failed_source_history(failure, stimulus, layout, catalogue, observation=observation)
    with pytest.raises(Refused, match='run-incomplete'):
        read_source_history(stimulus, trace, layout, catalogue, observation=observation)
    # Attribution is not membership: reject changed initial choices, unbound
    # event plans, launch failures and changed pre-clock physical samples.
    for change in ('initialization', 'identity', 'origin', 'sample'):
        if change == 'sample' and phase not in {'clock', 'advance'}:
            continue
        image = failure.as_dict()
        detail = image['reason']['detail']
        if change == 'initialization':
            detail['replay_prefix']['initialization'] = None
        elif change == 'identity':
            detail['source_evidence']['stimulus'] = '0'*64
        elif change == 'origin':
            image['origin'], image['disposition'] = 'verifier', 'no-result'
        else:
            detail['replay_prefix']['pending_sample']['y'] = 3
        try:
            rejected = compare_native_history(relation, request, encoded, rows,
                RunFailure.from_dict(image), catalogue, observation=observation)
        except Refused:
            continue
        result = rejected.admit_member(lambda _, roots: z3_check(roots, 30_000))
        assert result.status is AuditCheckStatus.INCONSISTENT, (change, result.as_dict())


@pytest.fixture(scope='module', params=[False, True], ids=['default-context', 'private-context'])
def witness(tmp_path_factory, request):
    root = tmp_path_factory.mktemp('native_product')
    machine, plan, _, template = _prepare(root, HISTORY_SOURCE, initialization=True)
    contract = _contract()
    context = z3.Context() if request.param else None
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    names = _names(machine)
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue), context=context,
        complete_state=True, occurrences=True,
        monitors=_monitors(contract, fields={names['saved']:2}), transactions=TransactionProduct(contract))
    artifact = RTLArtifact(root/'design.sv', 'M', root/'native')
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact.identity)
    request = SymbolicRequest(ReadyValidRequestCodec(contract, 2, 1, initial=layout), 7, context=context)
    encoding = relation.encode_request(request, initial, binding)
    solver = z3.Solver(ctx=context)
    solver.set(timeout=30_000)
    solver.add(*encoding.constraints, request.lengths['in'] == 2,
        request.queues['in'][0][0] == 0, request.queues['in'][1][0] == 1,
        *(term == 0 for term in binding.values()))
    for i in range(7):
        solver.add(request.resets['c'][i] == (i in (0, 5)),
                   request.actions[i].controls['ordy'] == (i != 2),
                   request.actions[i].offers['in'] == (i in (1, 3)))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    stimulus = request.codec.decode(rows)
    trace = artifact.exact_trace(contract, stimulus, observe_names=('y', 'ov', 'ir'),
        state_names=tuple(layout.widths), state_widths=dict(layout.widths), startup_state=layout.decode(rows))
    return relation, request, encoding, rows, trace, catalogue


def test_native_monitor_and_transaction_history_agree(witness):
    relation, request, encoded, rows, trace, catalogue = witness
    comparison = compare_native_history(relation, request, encoded, rows, trace, catalogue)
    audit, calls = _audit(comparison)
    assert audit.status is AuditCheckStatus.AGREED, audit.as_dict()
    assert calls[-1] == 'agreement'
    for prefix in ('protocol[', 'monitor[', 'transactions[', 'terminal_'):
        assert any(name.startswith(prefix) for name in comparison.differences)
    assert any(name.startswith('transactions[7].latency.') for name in comparison.differences)


def test_first_hit_projection_preserves_complete_native_audit(witness, monkeypatch):
    from behave.sim.rtl import observation_audit
    from behave.sim.rtl.source_relation import SourceObservation

    relation, request, encoded, rows, trace, catalogue = witness
    options = dict(observation_scope=SourceObservation.GOAL_PREFIX)
    current = compare_native_history(relation, request, encoded, rows, trace, catalogue, **options)
    cumulative = {}

    def original_requirements(terms, observed, seen=None):
        assert seen is not None
        image = cumulative.setdefault(id(seen), {})
        seen.update(observed)
        for name, term in terms.items():
            image[name] = z3.Or(image.get(name, z3.BoolVal(False, term.ctx)), term)
            yield name, image[name], name in seen

    with monkeypatch.context() as patch:
        patch.setattr(observation_audit, '_event_requirements', original_requirements)
        original = compare_native_history(relation, request, encoded, rows, trace, catalogue, **options)
    assert current.native_image == original.native_image
    assert len(current.differences) < len(original.differences)
    for name, value in original.differences.items():
        if '.hit:' not in name and '.occurrence:' not in name:
            assert value.eq(current.differences[name])
    solver = z3.Solver(ctx=encoded.alive.ctx)
    solver.add(z3.Xor(original.different, current.different))
    assert solver.check() == z3.unsat
    audit, _calls = _audit(current)
    assert audit.status is AuditCheckStatus.AGREED, audit.as_dict()


def test_phase_observation_batches_preserve_the_complete_ordered_audit_image(witness, monkeypatch):
    from behave.sim.rtl import observation_audit

    relation, request, encoded, rows, trace, catalogue = witness
    difference, built = observation_audit.scalar_difference, []

    def counted(term, value, literals):
        built.append((term.ctx, term.get_id(), type(value), value))
        return difference(term, value, literals)

    monkeypatch.setattr(observation_audit, 'scalar_difference', counted)
    comparison = compare_native_history(relation, request, encoded, rows, trace, catalogue)
    assert len(built) == len(set(built)) < len(comparison.differences)
    phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
    assert len(comparison.member_observations) == len(phases)
    total = 0
    for index, actual in enumerate(comparison.member_observations):
        expected = tuple(z3.Not(term) for name, term in comparison.differences.items()
                         if name.startswith(f'phase[{index}].'))
        assert len(actual) == len(expected) > 2
        assert all(left.eq(right) for left, right in zip(actual, expected, strict=True))
        total += len(actual)
    assert total == sum(name.startswith('phase[') for name in comparison.differences)


@pytest.mark.parametrize('part', ['pointer', 'released', 'adapter', 'monitor', 'transaction', 'goal',
                                'terminal_hits', 'terminal_assumptions', 'terminal_unresolved'])
def test_hidden_product_corruption_cannot_pass_native_audit(witness, part):
    relation, request, encoded, rows, trace, catalogue = witness
    if part in ('pointer', 'released', 'adapter'):
        field = {'pointer':'pointers', 'released':'released', 'adapter':'adapters'}[part]
        states = list(getattr(encoded.protocol, field))
        key = next(iter(states[-1]))
        states[-1] = {**states[-1], key:states[-1][key]+1}
        encoded = replace(encoded, protocol=replace(encoded.protocol, **{field:tuple(states)}))
    elif part == 'monitor':
        states = list(encoded.monitor_states)
        state = states[-1]
        key = next(iter(state.reset))
        states[-1] = replace(state, reset={**state.reset, key:replace(state.reset[key],
            reference_valid=z3.Not(state.reset[key].reference_valid))})
        encoded = replace(encoded, monitor_states=tuple(states))
    elif part == 'transaction':
        states = list(encoded.transaction_states)
        previous = states[-1]['observations']['out']
        states[-1] = {**states[-1], 'observations':{**states[-1]['observations'],
            'out':{**previous, 'seen':z3.Not(previous['seen'])}}}
        encoded = replace(encoded, transaction_states=tuple(states))
    elif part == 'goal':
        hits = list(encoded.transaction_hits)
        hits[-1] = {**hits[-1], 'observation:zero':z3.Not(hits[-1]['observation:zero'])}
        encoded = replace(encoded, transaction_hits=tuple(hits))
    else:
        terms = getattr(encoded, part)
        encoded = replace(encoded, **{part:{key:z3.Not(value) for key, value in terms.items()}})
    audit, calls = _audit(compare_native_history(relation, request, encoded, rows, trace, catalogue))
    assert audit.status is AuditCheckStatus.INCONSISTENT, audit.as_dict()
    assert calls[-1] == 'membership'


def test_driver_reconstruction_does_not_trust_recorded_transfers(witness):
    relation, request, _, rows, trace, _ = witness
    stimulus = request.codec.decode(rows)
    states, records = native_protocol_history(relation.contract, stimulus, trace)
    changed = list(trace.records)
    from behave.sim.core.stimulus import SlotRecord

    row = changed[1].as_dict()
    row['accepted'] = []
    changed[1] = SlotRecord.from_dict(row)
    other = native_protocol_history(relation.contract, stimulus,
        SimpleNamespace(records=changed, completion=trace.completion))
    assert states == other[0]
    assert records == other[1]
    assert records[1].accepted != changed[1].accepted


def test_missing_monitor_definedness_is_not_silently_ignored(witness):
    relation, request, encoded, rows, trace, catalogue = witness
    with pytest.raises(Refused, match='every definedness predicate'):
        compare_native_history(relation, request, replace(encoded, monitor_faults=()),
                               rows, trace, catalogue)


def test_monitor_definedness_is_in_the_native_audit_image(witness):
    relation, request, encoded, rows, trace, catalogue = witness
    comparison = compare_native_history(relation, request, encoded, rows, trace, catalogue)
    assert any('.monitor.faults.' in name for name in comparison.differences)
    faults = list(encoded.monitor_faults)
    faults[0] = {name: z3.BoolVal(True, ctx=encoded.alive.ctx) for name in faults[0]}
    assert faults[0]
    changed = replace(encoded, monitor_faults=tuple(faults))
    comparison = compare_native_history(relation, request, changed, rows, trace, catalogue)
    audit, _ = _audit(comparison)
    assert audit.status is not AuditCheckStatus.AGREED, audit.as_dict()


@pytest.mark.parametrize('field', ['monitor_states', 'monitor_hits', 'transaction_states', 'transaction_hits'])
def test_missing_product_boundaries_are_not_silently_ignored(witness, field):
    relation, request, encoded, rows, trace, catalogue = witness
    with pytest.raises(Refused, match='every boundary'):
        compare_native_history(relation, request, replace(encoded, **{field:()}), rows, trace, catalogue)


@pytest.mark.parametrize('field', ['terminal_hits', 'terminal_assumptions', 'terminal_unresolved'])
@pytest.mark.parametrize('missing', [None, {}])
def test_missing_terminal_predicates_are_not_silently_ignored(witness, field, missing):
    relation, request, encoded, rows, trace, catalogue = witness
    with pytest.raises(Refused, match='every terminal predicate'):
        compare_native_history(relation, request, replace(encoded, **{field:missing}), rows, trace, catalogue)


def test_protocol_reconstruction_rejects_unreadable_evidence(witness):
    relation, request, _, rows, trace, _ = witness
    records = list(trace.records)
    records[0] = SimpleNamespace(outputs=(('ov', None),))
    with pytest.raises(Refused, match='readable physical pin'):
        native_protocol_history(relation.contract, request.codec.decode(rows),
            SimpleNamespace(records=records, completion=trace.completion))
