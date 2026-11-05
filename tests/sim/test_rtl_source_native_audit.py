"""Whole native histories must match, not just their final output or goal OR."""

from dataclasses import replace

import pytest
import z3

from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.query import SolverAnswer
from behave.sim.core.refusal import Refused
from behave.sim.core.stimulus import AuditedTrace
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.protocol_relation import SymbolicRequest
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_task import SourceTaskRelation
from behave.sim.rtl.source_trace import read_source_history, read_source_trace
from behave.sim.rtl.task_request import ReadyValidRequestCodec

from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract
from .test_rtl_source_memory_relation import _prepare

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver, pytest.mark.cosim]


@pytest.mark.parametrize('unknown', [False, True])
def test_local_repair_uses_first_cumulative_miss_and_never_completes_unknowns(unknown):
    from behave.sim.rtl.observation_audit import SourceNativeComparison

    bit = z3.Bool('unassigned_phase_observation') if unknown else z3.BoolVal(False)
    comparison = SourceNativeComparison(None, (), {}, {},
        member_observations=((z3.BoolVal(True),), (bit,), (bit,), (bit,)))
    solver = z3.Solver()
    assert solver.check() == z3.sat
    assert comparison.first_missed_phase(solver.model(), check=lambda: None) == (None if unknown else 1)
    with pytest.raises(InterruptedError):
        comparison.first_missed_phase(solver.model(), check=lambda: (_ for _ in ()).throw(InterruptedError()))


@pytest.mark.parametrize('selected', [(0,), (1,), (2,), (3,), (4, 6)])
def test_local_native_relation_selection_is_physical_and_request_owned(selected):
    from behave.sim.rtl.observation_audit import NativeHistoryBindings

    native = object.__new__(NativeHistoryBindings)
    member, cover = object(), object()
    native.cover_relation, native.cover_phases = cover, frozenset(selected)
    phases = [native.phase_relation(member)]
    phases.extend(native.phase_relation(member, slot, position)
                  for slot in range(2) for position in range(3))
    assert all(value is (cover if index in selected else member) for index, value in enumerate(phases))
    native.cover_phases = frozenset()
    assert native.phase_relation(member) is member
    assert native.phase_relation(member, 0, 1) is member


def test_local_prefix_cannot_accept_an_unfinished_scheduler(tmp_path):
    from behave.sim.rtl.observation_audit import NativeHistoryBindings
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from .test_rtl_source_relation import _pins, _relation
    from .test_rtl_source_scheduler import _lower, _names, _state

    machine, _ = _lower(tmp_path, 'module M(input a,output logic q); wire b; '
        'assign b=a; always_comb if(b) q=a; else q=0; endmodule')
    names = _names(machine)
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'b': 0, 'q': 0}), ())
    cover = replace(relation, direct=NativeCoverSourceCompiler(machine))
    partial = cover.encode(state, _pins(machine, env, {names['a']: 1}),
                           event_bound=1, require_quiescence=False)
    solver = z3.Solver()
    solver.add(*partial.constraints, partial.unfinished)
    assert solver.check() == z3.sat
    native = object.__new__(NativeHistoryBindings)
    native.check, native.leaves, native.ports = lambda: None, (), ()
    native.cover_relation, native.cover_phases = cover, frozenset({2})
    assert native.phase_event_bound(1000, 0, 1) == len(machine.actions)
    assert native.phase_event_bound(1, 0, 1) == 1
    assert native.phase_event_bound(1000, 0, 0) == 1000
    bound = native.bind_phase(partial, 0, 1)
    solver.reset()
    solver.add(*bound.constraints)
    assert solver.check() == z3.unsat  # A short prefix is no complete native witness.


@pytest.mark.parametrize('fault', ['none', 'unfinished', 'domain', 'memory', 'mismatch'])
def test_native_cover_membership_is_existential_but_complete(fault):
    from types import SimpleNamespace
    from behave.sim.rtl.observation_audit import SourceNativeComparison

    choice = z3.Bool('native_order_choice')
    encoded = SimpleNamespace(alive=z3.BoolVal(True), unfinished=z3.BoolVal(fault == 'unfinished'),
        domain_faults={'domain': z3.BoolVal(fault == 'domain')},
        source=SimpleNamespace(memory=SimpleNamespace(conflicts=(z3.BoolVal(fault == 'memory'),))))
    comparison = SourceNativeComparison(encoded, (choice,) if fault == 'mismatch' else (),
                                         {'native_event': choice}, {})
    calls = []

    def solve(phase, roots):
        calls.append(phase)
        return z3_check(roots, 30_000)

    answer = comparison.admit_member(solve)
    assert answer.status is (AuditCheckStatus.AGREED if fault == 'none' else AuditCheckStatus.INCONSISTENT)
    assert calls == ['native-cover-membership']
    # An unknown member proof cannot admit a trace or grant a goal result.
    answer = comparison.admit_member(lambda *_: ExactCheckResult(SolverAnswer.UNKNOWN, reason='budget'))
    assert answer.status is AuditCheckStatus.REFUSED


@pytest.mark.parametrize('extension', ['zero', 'signed', 'unknown'])
@pytest.mark.parametrize('observed', [3, 13])
def test_native_binding_of_named_payload_preserves_only_explicit_carrier_wiring(extension, observed):
    from behave.sim.symbolic.step_template import _bind_successors
    from behave.sim.core.construction_checkpoint import finish_construction
    from behave.sim.rtl.observation_audit import NativeHistoryBindings

    word = z3.BitVec('payload_to_observe', 4)
    payload = word + 1
    high = z3.BitVec('unknown_carrier_high', 4)
    def wrap(value):
        return (z3.ZeroExt(4, value) if extension == 'zero'
                else z3.SignExt(4, value) if extension == 'signed'
                else z3.Concat(high, value))
    value = z3.If(word == 2, wrap(payload), wrap(word + 2))
    roots = []
    named = finish_construction(_bind_successors(value, roots, {}, 'observed', wires=True), None)
    native = object.__new__(NativeHistoryBindings)
    native.check = lambda: None
    bound = native._bind_value(named, ('q',), {('q',): (4, observed)}, roots, named.ctx)
    if extension == 'zero':
        assert z3.is_bv_value(bound)
    solver = z3.Solver()
    solver.add(*roots)
    assert solver.check() == z3.sat
    solver.push()
    solver.add(bound != value)
    assert solver.check() == z3.unsat
    solver.pop()
    # Explicit sign extension still depends on the observed payload. Unknown
    # high bits must remain free, never assumed zero from a physical width.
    if extension == 'unknown':
        solver.add(high == 10, bound == (0xa0 | observed))
        assert solver.check() == z3.sat
    else:
        expected = observed | (0xf0 if extension == 'signed' and observed >= 8 else 0)
        solver.add(bound != expected)
        assert solver.check() == z3.unsat


def test_native_boundary_cut_keeps_unobserved_carrier_bits_and_original_equations():
    import weakref
    from types import SimpleNamespace
    from behave.sim.rtl.observation_audit import NativeHistoryBindings
    from behave.sim.rtl.source_relation import SourceIntervalEncoding

    native = object.__new__(NativeHistoryBindings)
    native.check = lambda: None
    native.native = SimpleNamespace(states=({'q': 3},))
    native.leaves, native.ports = (('q', ('q',), 4),), ()
    value = z3.BitVec('wider_carrier', 8)
    private = z3.BitVec('private_unobserved', 8)
    phase = SourceIntervalEncoding((), {}, {}, {'q': value, 'private': private}, {}, (),
        z3.BoolVal(True), z3.BitVecVal(0, 8), {}, z3.BoolVal(True), z3.BoolVal(True))
    cut = native.bind_phase(phase)
    owner = weakref.ref(native)
    del native
    assert owner() is None  # No recursive closure may retain a whole AST arena.
    assert cut.state['private'] is private
    solver = z3.Solver()
    solver.add(*cut.constraints, value == 0xa3)
    assert solver.check() == z3.sat
    assert solver.model().eval(cut.state['q']).as_long() == 0xa3
    solver.push()
    solver.add(cut.state['q'] != value)
    assert solver.check() == z3.unsat
    solver.pop()
    invalid = z3.Solver()
    invalid.add(*cut.constraints, value == 0xa4)
    assert invalid.check() == z3.unsat


@pytest.mark.parametrize('physical_width', [8, 32])
@pytest.mark.parametrize('observed', [0, 3, -1, -4, 4, -5])
def test_native_boundary_accepts_narrow_signed_carriers_only_with_full_width_equality(
        physical_width, observed):
    from behave.sim.rtl.observation_audit import NativeHistoryBindings

    native = object.__new__(NativeHistoryBindings)
    native.check = lambda: None
    value = z3.BitVec('range_narrowed_native_carrier', 3)
    bits = observed % (1 << physical_width)
    roots = []
    bound = native._bind_value(value, ('q',), {('q',): (physical_width, bits)},
                               roots, value.ctx)
    assert bound.size() == value.size()
    solver = z3.Solver()
    solver.add(*roots)
    assert solver.check() == (z3.sat if -4 <= observed < 4 else z3.unsat)
    # A value that cannot fit the signed carrier must not be truncated into
    # an accepted observation. The complete physical equality remains a root.
    solver.add(bound != value)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('bits', [-1, 256, True, '0'])
def test_native_boundary_keeps_invalid_physical_observation_refusal(bits):
    from behave.sim.rtl.observation_audit import NativeHistoryBindings

    native = object.__new__(NativeHistoryBindings)
    native.check = lambda: None
    value = z3.BitVec('invalid_native_bits', 3)
    with pytest.raises(Refused) as caught:
        native._bind_value(value, ('q',), {('q',): (8, bits)}, [], value.ctx)
    assert caught.value.refusal.kind == 'rtl-source-native-value-unreadable'
    assert caught.value.refusal.detail['field'] == ('q',)


@pytest.mark.parametrize('carrier_width,physical_width', [(1, 32), (3, 8), (8, 8), (12, 4)])
@pytest.mark.parametrize('value', [-7, -1, 0, 1, 5, 127])
def test_native_projection_uses_signed_carrier_value_not_its_storage_width(
        carrier_width, physical_width, value):
    from behave.sim.rtl.symbolic_state import physical_bits

    context = z3.Context()
    carrier = z3.BitVecVal(value, carrier_width, ctx=context)
    expected = carrier.as_signed_long() % (1 << physical_width)
    projected = z3.simplify(physical_bits(carrier, physical_width))
    assert projected.ctx == context and projected.size() == physical_width
    assert projected.as_long() == expected


def test_native_member_prefix_observation_follows_complete_startup():
    from types import SimpleNamespace
    from behave.sim.rtl.observation_audit import SourceNativeComparison
    from behave.sim.rtl.source_member import SourceMemberPlan
    from .test_rtl_source_member import _interval

    a, b = z3.BitVecs('startup_first startup_last', 1)
    first = _interval((a,), (z3.Or(a == 0, a == 1),))
    last = _interval((b,), (b == a,))
    history = SimpleNamespace(startup=SimpleNamespace(phases=(first, last)),
        source=SimpleNamespace(slots=()), member_plan=SourceMemberPlan((), (first, last)))
    comparison = SourceNativeComparison(history, (), {}, {}, member_observations=((b == 1,),))
    prefixes = list(comparison.member_prefixes((), check=lambda: None))
    assert len(prefixes) == 2
    assert z3_check((*prefixes[0], b == 0), 1000).answer is SolverAnswer.SAT
    assert z3_check((*prefixes[1], b == 0), 1000).answer is SolverAnswer.UNSAT
    assert z3_check(prefixes[1], 1000).answer is SolverAnswer.SAT
    assert list(replace(comparison, member_observations=()).member_prefixes((), check=lambda: None)) == []


@pytest.fixture(scope='module', params=[False, True], ids=['default-context', 'private-context'])
def witness(tmp_path_factory, request):
    root = tmp_path_factory.mktemp('native_history')
    source = HISTORY_SOURCE.replace('reg [1:0] saved;',
        'reg [1:0] saved; reg [1:0] returns;\n'
        'always @(negedge clk) if(saved==2) returns<=returns+1;')
    machine, plan, _, template = _prepare(root, source, initialization=True)
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    contract = _simple_contract()
    artifact = RTLArtifact(root/'design.sv', 'M', root/'native')
    context = z3.Context() if request.param else None
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue), context=context,
                                  complete_state=True, occurrences=True)
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact.identity)
    request = SymbolicRequest(ReadyValidRequestCodec(contract, 2, 1, initial=layout), 5, context=context)
    encoded = relation.encode_request(request, initial, binding)
    solver = z3.Solver(ctx=context)
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, request.lengths['in'] == 2,
               request.queues['in'][0][0] == 1, request.queues['in'][1][0] == 2)
    solver.add(*(term == 0 for term in binding.values()))
    solver.add(*(action.controls['ordy'] for action in request.actions),
               *(action.offers['in'] for action in request.actions[1:]),
               *(z3.Not(term) for term in request.resets['c'][1:]))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    trace = artifact.exact_trace(contract, request.codec.decode(rows),
        observe_names=('y', 'ov', 'ir'), state_names=tuple(layout.widths),
        state_widths=dict(layout.widths), startup_state=layout.decode(rows))
    return relation, request, rows, trace, catalogue


def _comparison(witness, *, trace=None):
    relation, request, rows, original, catalogue = witness
    return relation.compare_native(request, rows, original if trace is None else trace, catalogue)


@pytest.mark.parametrize('fault,kind', [
    ('monitor.reset-policy-unresolved[c][out]', 'rtl-monitor-reset-policy-undeclared'),
    ('monitor.test-domain', 'rtl-source-operation-domain-unencoded'),
])
def test_native_monitor_domain_fault_is_not_a_source_contradiction(witness, monkeypatch, fault, kind):
    from copy import copy
    from behave.sim.core.ready_valid_product import ConcreteAlgebra
    from .test_rtl_source_monitors import _monitors

    original, *record = witness
    relation = copy(original)
    relation.monitors = _monitors(relation.contract)
    owner = type(relation.monitors)
    step = owner.step

    def with_fault(self, *args, **kwargs):
        result = step(self, *args, **kwargs)
        if kwargs.get('algebra') is ConcreteAlgebra:
            kwargs['faults'][fault] = True
        return result

    monkeypatch.setattr(owner, 'step', with_fault)
    with pytest.raises(Refused) as failure:
        _comparison((relation, *record))
    assert failure.value.refusal.kind == kind
    assert failure.value.refusal.as_dict()['detail']['faults'] == [fault]


@pytest.mark.parametrize('selected', [(0,), (1, 2, 3), 'all'])
def test_local_order_repairs_compose_with_complete_native_protocol_history(witness, selected):
    from copy import copy
    from behave.sim.rtl.observation_audit import NativeHistoryBindings, compare_native_history
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler, NativeMemberSourceCompiler

    original, request, rows, trace, catalogue = witness
    relation = copy(original)
    relation.source = copy(original.source)
    interval = relation.source.relation
    relation.source.relation = replace(interval, direct=NativeMemberSourceCompiler(relation.machine))
    cover = replace(interval, direct=NativeCoverSourceCompiler(relation.machine))
    initial = relation.initial_state(dict.fromkeys(relation.machine.program.fields))
    _, initialization = initial.request_layout(relation.machine.program, request.codec.initial.artifact)
    selected = range(1 + 3 * len(trace.records)) if selected == 'all' else selected
    native = NativeHistoryBindings(relation.machine, request, rows, trace, catalogue,
                                  cover_relation=cover, cover_phases=selected)
    encoded = relation.encode_request(request.fixed(rows), initial, initialization, native=native)
    comparison = compare_native_history(relation, request, encoded, rows, trace, catalogue)
    result = comparison.admit_member(lambda _, roots: z3_check(roots, 30_000))
    assert result.status is AuditCheckStatus.AGREED, result.as_dict()
    assert not cover.direct._prefix_scope  # Request permissions do not leak to another query.


@pytest.mark.parametrize('change', ['none', 'sample', 'successor', 'payload', 'phase'])
def test_specialized_native_membership_keeps_all_observation_obligations(witness, change):
    from behave.sim.rtl.observation_audit import NativeHistoryBindings, compare_native_history

    relation, request, rows, trace, catalogue = witness
    if change != 'none':
        image = trace.as_dict()
        if change == 'sample':
            image['records'][2]['outputs'] = dict(image['records'][2]['outputs'])
            image['records'][2]['outputs']['y'] ^= 1
        elif change == 'successor':
            image['records'][2]['state'] = dict(image['records'][2]['state'])
            image['records'][2]['state']['saved'] ^= 1
        elif change == 'payload':
            record = next(row for row in image['records'] if dict(row['accepted']).get('out') is not None)
            record['accepted'] = dict(record['accepted'])
            record['accepted']['out'][0] ^= 1
        else:
            intervals = image['source_evidence']['intervals']
            for i in range(2, len(intervals), 3):
                if intervals[i]['events'] != intervals[i+1]['events']:
                    for key in ('events', 'callbacks'):
                        intervals[i][key], intervals[i+1][key] = intervals[i+1][key], intervals[i][key]
                    break
            else:
                pytest.fail('fixture must have distinct clock and return events')
        trace = AuditedTrace.from_dict(image)
    fixed = request.fixed(rows)
    assert fixed is not request
    assert all(z3.is_const(value) and value.decl().kind() == z3.Z3_OP_UNINTERPRETED
               for value in request.valuations[0].values())
    assert all(z3.is_int_value(value) or z3.is_bv_value(value) or z3.is_true(value) or z3.is_false(value)
               for value in fixed.valuations[0].values())
    initial = relation.initial_state(dict.fromkeys(relation.machine.program.fields))
    _, initialization = initial.request_layout(relation.machine.program, request.codec.initial.artifact)
    native = NativeHistoryBindings(relation.machine, request, rows, trace, catalogue)
    encoded = relation.encode_request(fixed, initial, initialization, native=native)
    comparison = compare_native_history(relation, request, encoded, rows, trace, catalogue)
    result = comparison.admit_member(lambda _, roots: z3_check(roots, 30_000))
    assert result.status is (AuditCheckStatus.AGREED if change == 'none' else AuditCheckStatus.INCONSISTENT)


def _audit(comparison, unknown=None, *, joint=False):
    calls = []

    def solve(phase, assertions):
        calls.append(phase)
        if phase == 'native-certificate' and len(assertions) == 1:
            assert z3.is_false(assertions[0])
        else:
            assert all(term is before for term, before in zip(assertions, comparison.constraints))
            assert len(assertions) == len(comparison.constraints) + (phase != 'feasibility')
        if phase == unknown:
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='test budget exhausted')
        return z3_check(assertions, 30_000, _owned_context=comparison.encoding.alive.ctx)

    return comparison.audit(solve, joint=joint), calls


@pytest.mark.parametrize('joint', [False, True])
def test_complete_native_source_witness_includes_return_edges(witness, joint):
    comparison = _comparison(witness)
    audit, calls = _audit(comparison, joint=joint)
    assert audit.status is AuditCheckStatus.AGREED, audit
    assert calls == (['feasibility', 'native-certificate'] if joint else
                     ['feasibility', 'completeness', 'definedness', 'memory', 'membership', 'agreement'])
    _relation, request, rows, trace, catalogue = witness
    history = read_source_history(request.codec.decode(rows), trace, request.codec.initial, catalogue)
    assert len(history.phases) == 1 + 3 * len(rows)
    assert any(kind == 'advance' and phase.goals for kind, phase in history.phases)
    # The old clocked relation still may not consume this evidence. It did not
    # acquire return-edge support merely because the new reader preserves it.
    with pytest.raises(Refused, match='clock-return-relation-unsupported'):
        read_source_trace(trace.stimulus, trace, request.codec.initial, catalogue, prefix='legacy')


@pytest.mark.parametrize('change', ['sample', 'successor', 'payload', 'phase'])
@pytest.mark.parametrize('joint', [False, True])
def test_changed_native_history_is_not_admitted(witness, change, joint):
    _, _, _, trace, _ = witness
    image = trace.as_dict()
    if change == 'sample':
        image['records'][2]['outputs'] = dict(image['records'][2]['outputs'])
        image['records'][2]['outputs']['y'] ^= 1
    elif change == 'successor':
        image['records'][2]['state'] = dict(image['records'][2]['state'])
        image['records'][2]['state']['saved'] ^= 1
    elif change == 'payload':
        record = next(row for row in image['records'] if dict(row['accepted']).get('out') is not None)
        record['accepted'] = dict(record['accepted'])
        record['accepted']['out'][0] ^= 1
    else:
        intervals = image['source_evidence']['intervals']
        for i in range(2, len(intervals), 3):
            if intervals[i]['events'] != intervals[i+1]['events']:
                for key in ('events', 'callbacks'):
                    intervals[i][key], intervals[i+1][key] = intervals[i+1][key], intervals[i][key]
                break
        else:
            pytest.fail('fixture must have distinct clock and return events')
    changed = AuditedTrace.from_dict(image)
    comparison = _comparison(witness, trace=changed)
    audit, calls = _audit(comparison, joint=joint)
    assert audit.status is AuditCheckStatus.INCONSISTENT, audit
    assert calls[-1] == 'membership'


@pytest.mark.parametrize('phase', ['feasibility', 'completeness', 'definedness', 'memory', 'membership', 'agreement'])
def test_unknown_is_not_success_for_any_native_obligation(witness, phase):
    audit, calls = _audit(_comparison(witness), unknown=phase)
    assert audit.status is AuditCheckStatus.REFUSED
    assert calls[-1] == phase


@pytest.mark.parametrize('joint', (False, True))
def test_empty_formula_is_not_an_agreement_proof(witness, joint):
    comparison = _comparison(witness)
    comparison = replace(comparison, constraints=(*comparison.constraints,
        z3.BoolVal(False, ctx=comparison.encoding.alive.ctx)))
    audit, calls = _audit(comparison, joint=joint)
    assert audit.status is AuditCheckStatus.REFUSED
    assert calls == ['feasibility']


def test_native_projection_honors_cancellation(witness):
    relation, request, rows, trace, catalogue = witness

    def cancel():
        raise InterruptedError('cancel native projection')

    with pytest.raises(InterruptedError, match='cancel native projection'):
        relation.compare_native(request, rows, trace, catalogue, check=cancel)


def test_native_trace_cannot_relabel_its_contract(witness):
    _, _, _, trace, _ = witness
    image = trace.as_dict()
    image['domain'] = 'f' * 64
    with pytest.raises(Refused, match='rtl-source-native-contract-mismatch'):
        _comparison(witness, trace=AuditedTrace.from_dict(image))


@pytest.mark.parametrize('change', ['queue', 'control'])
def test_native_request_equivalence_does_not_accept_changed_inputs(witness, change):
    """Equivalent schedule representations do not permit a different request."""
    relation, request, original, trace, catalogue = witness
    rows = [dict(row) for row in original]
    if change == 'queue':
        coordinate = request.codec.cells[0][0]
        for row in rows:
            row[coordinate] ^= 1
    else:
        rows[1][request.codec.ready] ^= 1
    with pytest.raises(Refused, match='rtl-source-native-request-mismatch'):
        relation.compare_native(request, rows, trace, catalogue)


def test_native_trace_requires_complete_request_coordinates(witness):
    relation, _, rows, trace, catalogue = witness
    with pytest.raises(ValueError, match='complete construction coordinates'):
        relation.compare_native(None, rows, trace, catalogue)


@pytest.mark.parametrize('latency', [1, 3])
def test_memory_successors_and_pending_responses_are_native_evidence(tmp_path, latency):
    from behave.sim.core.memory import MemoryEnvironment, MemoryEnvironmentState, MemoryRun
    from behave.sim.core.memory_trace import read_memory_successor

    from .test_rtl_source_task_relation import SOURCE, _contract

    machine, plan, _, template = _prepare(tmp_path, SOURCE, initialization=True)
    contract = _contract()
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact.identity)
    environment = MemoryEnvironment([MemoryRun('ram', initial={1: 201}, read_latency=latency,
                                               ready={None: [1]*5})])
    request = SymbolicRequest(ReadyValidRequestCodec(contract, 1, 1, initial=layout,
                              memory_environment=environment), 5)
    encoded = relation.encode_request(request, initial, binding)
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, request.lengths['in'] == 1,
               request.queues['in'][0][0] == 1, *(term == 0 for term in binding.values()),
               *(action.controls['ordy'] for action in request.actions),
               *(action.offers['in'] for action in request.actions[1:]),
               *(z3.Not(term) for term in request.resets['c'][1:]))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    outputs = tuple(field.port for field in machine.program.fields.values()
                    if field.port is not None and not field.input)
    trace = artifact.exact_trace(contract, request.codec.decode(rows), observe_names=outputs,
        state_names=tuple(layout.widths), state_widths=dict(layout.widths), startup_state=layout.decode(rows))
    comparison = relation.compare_native(request, rows, trace, catalogue)
    audit, calls = _audit(comparison)
    # This existing fixture has transient source hits sensitive to internal
    # ordering. Native membership is established, but that is not universal
    # correspondence. Do not turn its agreement refusal into an exact grant.
    assert audit.status is AuditCheckStatus.REFUSED, (audit.as_dict(), calls)
    assert audit.as_dict()['refusal']['kind'] == 'rtl-source-native-agreement'
    assert calls == ['feasibility', 'completeness', 'definedness', 'memory', 'membership', 'agreement']
    non_events = [term for name, term in comparison.differences.items()
                  if '.hit:' not in name and '.occurrence:' not in name]
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*comparison.constraints, z3.Or(*non_events))
    assert solver.check() == z3.unsat  # All states, pins, transfers and RAM agree in every order.

    state = MemoryEnvironmentState.initial(contract.memories, environment)
    for record in trace.records[:3]:
        state = read_memory_successor(state, dict(record.environment)['external-memory'])
    wrong = state.as_dict()
    wrong['memories'][0][1]['cells'][0][1] ^= 1
    image = trace.as_dict()
    image['records'][2]['environment'] = {'external-memory': wrong}
    corrupt = relation.compare_native(request, rows, AuditedTrace.from_dict(image), catalogue)
    audit, calls = _audit(corrupt)
    assert audit.status is AuditCheckStatus.INCONSISTENT, audit.as_dict()
    assert calls[-1] == 'membership'
    image = trace.as_dict()
    image['records'][2]['environment'] = []
    with pytest.raises(Refused, match='rtl-source-native-memory-missing'):
        relation.compare_native(request, rows, AuditedTrace.from_dict(image), catalogue)


@pytest.mark.parametrize('independent_clocks', [False, True])
@pytest.mark.parametrize('stalled_second', [False, True])
def test_atomic_native_history_preserves_both_endpoint_products(tmp_path, independent_clocks, stalled_second):
    from behave.sim import api
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
    from behave.sim.rtl.atomic_request import AtomicRequestCodec

    clocks = ([api.ClockDomain('A', 'clk_A', 'rst_A'),
               api.ClockDomain('B', 'clk_B', 'rst_B', edge='falling')]
              if independent_clocks else [api.ClockDomain('A', 'clk_A', 'rst_A')])
    endpoints, ports, bodies = [], [f'input {c.clock},{c.reset}' for c in clocks], []
    for index, name in enumerate(('a', 'b')):
        clock = clocks[index] if independent_clocks else clocks[0]
        endpoints.extend((
            api.Endpoint('in_'+name, 'in', [api.Field('x', api.FiniteType(2), 'x_'+name)],
                         api.ready_valid('in', 'iv_'+name, 'ir_'+name), clock.name, kind='transaction'),
            api.Endpoint('out_'+name, 'out', [api.Field('y', api.FiniteType(2), 'y_'+name)],
                         api.ready_valid('out', 'ov_'+name, 'r_'+name), clock.name, kind='transaction')))
        ports.append(f'input iv_{name},r_{name},input [1:0] x_{name},output ir_{name},'
                     f'output reg ov_{name},output reg [1:0] y_{name}')
        edge = 'negedge' if clock.edge == 'falling' else 'posedge'
        bodies.append(f'''assign ir_{name}=!{clock.reset} && (!ov_{name} || r_{name});
          always @({edge} {clock.clock}) begin
            if({clock.reset}) begin ov_{name}<=0; y_{name}<=0; end
            else if(iv_{name} && ir_{name}) begin ov_{name}<=1; y_{name}<=x_{name}; end
            else if(r_{name}) ov_{name}<=0;
          end''')
    source = 'module M('+','.join(ports)+');\n'+'\n'.join(bodies)+'\nendmodule'
    if stalled_second:
        clock = clocks[1] if independent_clocks else clocks[0]
        source = source.replace(f'assign ir_b=!{clock.reset} && (!ov_b || r_b);', 'assign ir_b=0;')
    machine, plan, _, template = _prepare(tmp_path, source, initialization=True)
    contract = api.TaskContract(endpoints, clocks)
    names = tuple(c.name for c in clocks)
    ticks = (names, ('A',), ('B',), ('A',), names) if independent_clocks else (names,) * 5
    slots = [api.EventSlot(ticks=active, resets=names if i == 0 else (),
        offers=tuple(e.name for e in contract.inputs() if e.clock in active) if i in (1, 2) else (),
        controls={**{c.reset: int(i == 0) for c in clocks}, 'r_a':1, 'r_b':1})
        for i, active in enumerate(ticks)]
    times = (0, 4, 6, 8, 12) if independent_clocks else (0, 2, 4, 6, 8)
    schedule = GlobalSchedule([ClockTiming(c.name, 4 if c.name == 'A' else 6,
        clock=c.clock, reset=c.reset, edge=c.edge) for c in clocks] if independent_clocks else
        [ClockTiming('A', 2, clock='clk_A', reset='rst_A')],
        [GlobalEventSlot(time, event=slot) for time, slot in zip(times, slots, strict=True)])
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact.identity)
    request = SymbolicRequest(AtomicRequestCodec(contract, 1, schedule, initial=layout), 5)
    encoded = relation.encode_request(request, initial, binding)
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, *(term == 0 for term in binding.values()),
               *(length == 1 for length in request.lengths.values()))
    assert solver.check() == z3.sat
    from behave.sim.core.monitor import INPUT_COMPLETION, _input_completion

    assert solver.model().eval(encoded.protocol.pointers[-1]['in_a']).as_long() == 1
    assert solver.model().eval(encoded.protocol.pointers[-1]['in_b']).as_long() == int(not stalled_second)
    assert z3.is_true(solver.model().eval(encoded.hits[INPUT_COMPLETION])) is stalled_second
    rows = request.rows(solver.model())
    outputs = tuple(field.port for field in machine.program.fields.values()
                    if field.port is not None and not field.input)
    trace = artifact.exact_trace(contract, request.codec.decode(rows), observe_names=outputs,
        state_names=tuple(layout.widths), state_widths=dict(layout.widths), startup_state=layout.decode(rows))
    report = _input_completion(contract, trace.stimulus, trace)
    assert tuple(hit.endpoint for hit in report.hits) == (('in_b',) if stalled_second else ())
    assert not report.assumption_violations and not report.unresolved
    audit, _ = _audit(relation.compare_native(request, rows, trace, catalogue))
    assert audit.status is AuditCheckStatus.AGREED, audit.as_dict()
    for policy in ('initial_state', 'memory_policy'):
        image = trace.as_dict()
        image['stimulus'][policy] = [['unencoded-policy', 1]]
        # Changing the request first invalidates the native collector's
        # stimulus binding. Check that boundary, then independently exercise
        # the symbolic request check with an internally rebound record.
        with pytest.raises(Refused, match='rtl-source-history-source-native-evidence-invalid'):
            relation.compare_native(request, rows, AuditedTrace.from_dict(image), catalogue)
        image['source_evidence']['stimulus'] = AuditedTrace.from_dict(image).stimulus.fingerprint
        with pytest.raises(Refused, match='rtl-source-native-request-mismatch'):
            relation.compare_native(request, rows, AuditedTrace.from_dict(image), catalogue)
