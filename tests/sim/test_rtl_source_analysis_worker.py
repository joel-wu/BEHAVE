"""Diagnostic all-orders worker: whole source histories, never seed cones."""

import copy
import time

import pytest
import z3

from behave.sim import api
from behave.sim.core.audit import AuditedWitness, witness_replay_check
from behave.sim.core.instance import Verdict
from behave.sim.core.refusal import Refusal
from behave.sim.core.reset_monitor import ResetRestorationProvider
from behave.sim.rtl import analysis_job

from . import test_rtl_source_worker_task as task_fixtures
from .test_rtl_source_native_audit import _audit

task = task_fixtures.task

pytestmark = [pytest.mark.cosim, pytest.mark.solver]


@pytest.fixture(autouse=True)
def bounded_operation_scope(request):
    from behave.sim.analysis_budget import analysis_scope

    with analysis_scope(getattr(request, 'param', 120)):
        yield


@pytest.fixture(scope='module')
def worker(task):
    _, context, _, _, _, _ = task
    source = context.artifact.rtl_path.read_text()
    admitted = api.admit(context.contract, api.realization_of_rtl_source(
        source, 'source query regression', top='M', contract=context.contract))
    payload = {**context.source_payload(), 'horizon': 6,
               'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity}
    return payload, analysis_job.PreparedAnalysis(payload, direct_source=False)


def _value(reply, operation):
    value = analysis_job.decode_analysis_reply(reply, operation)
    assert not isinstance(value, Refusal), value.as_dict()
    return value


def test_source_solver_strategy_uses_compiler_types(worker):
    from behave.sim.rtl.source_values import ArrayType

    _, session = worker
    source = session.exact.source
    has_arrays = any(isinstance(kind, ArrayType) for kind in source.machine.program.kinds.values())
    assert session.exact._prefer_finite is (not source.contract.memories and not has_arrays)


@pytest.fixture(scope='module')
def native(task, worker):
    _, context, prepared, _, request, encoded = task
    solver = z3.Solver(ctx=encoded.alive.ctx)
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, request.lengths['in'] == 1,
        request.queues['in'][0][0] == 1, *(term == 0 for term in prepared.initial_binding.values()))
    for i in range(6):
        solver.add(request.resets['c'][i] == (i in (0, 4)),
                   request.actions[i].controls['ordy'], request.actions[i].offers['in'] == (i == 1))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    trace = context.source_trace(request.codec.decode(rows), startup_state=prepared.initial_layout.decode(rows))
    return rows, trace


def test_source_worker_audits_native_witness_and_independent_replay(task, worker, native):
    _, context, prepared, _, _, _ = task
    payload, session = worker
    rows, trace = native
    operation = {'rows': rows, 'trace': trace.as_dict(), 'domain': session.exact.domain.digest()}
    witness = AuditedWitness.from_record(_value(session.call('audit-request', operation), 'audit-request'))
    assert witness.inputs == tuple(rows)
    assert len(witness.states) == 7 and len(witness.hit_sets) == 6
    assert witness.states[0]['source.goal_prefix'][0]['phase'] == 'startup'
    assert witness.initial_hits == set(witness.states[0]['source.goal_prefix'][0]['goals'])
    assert all(len(state['source.goal_prefix']) == 3 for state in witness.states[1:])
    assert all(state['source.observation_scope'] == 'goal-prefix/1' for state in witness.states)
    assert any(witness.outputs)
    # Independently request a fresh native execution; do not replay the retained image.
    layout = context.prepare_source()
    again = context.artifact.exact_trace(context.contract, trace.stimulus,
        observe_names=layout.outputs, state_names=layout.state_names,
        state_widths=dict(prepared.initial_layout.widths),
        startup_state=prepared.initial_layout.decode(rows))
    assert again is not trace
    replay = analysis_job.decode_replay(_value(session.call('replay-source',
        {'rows': rows, 'trace': again.as_dict()}), 'replay-source'))
    assert witness_replay_check(witness, replay).status.value == 'agreed'
    if task[0] == 'scalar':
        from behave.sim.worker import run_job_strict

        # A witness for the diagnostic all-orders relation must not silently
        # cross into the public native-cover relation, even on the same RTL.
        isolated = analysis_job.decode_analysis_reply(run_job_strict('rtl_exact_analysis',
            {**payload, 'operation': 'audit-request', **operation}, timeout_s=120), 'audit-request')
        assert isinstance(isolated, Refusal)
        assert isolated.kind == 'rtl-analysis-request-domain-mismatch'


def test_short_native_record_reuses_startup_and_extends_through_source(task, worker, native, monkeypatch):
    from types import SimpleNamespace
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance
    from behave.sim.rtl.source_boundary_capture import source_history_prefix

    mode, context, *_ = task
    if context.schedule is not None:
        pytest.skip('explicit physical schedules are not extrapolated')
    _, session = worker
    _, original = native
    short = source_history_prefix(original, 4)
    before = short.as_dict()
    parent = object.__new__(RTLIsolatedAnalysisInstance)
    parent._domain, parent._context = session.exact.domain, context
    calls = []

    def call(_self, operation, **payload):
        assert operation == 'record-audit'
        fresh = api.AuditedTrace.from_dict(payload['raw_trace'])
        assert len(fresh.records) == 6 and fresh.stimulus.slots[:4] == short.stimulus.slots
        assert all(not slot.offers for slot in fresh.stimulus.slots[4:])
        assert fresh.initialization == original.initialization
        calls.append(fresh)
        return _value(session.call(operation, payload), operation)

    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call', call)
    result = parent.audit_record(api.RunResult.not_compared(short.stimulus, short,
        api.RunFailure.verifier('test-short', api.Refusal('uncompared', 'no inherited score'))),
        SimpleNamespace(contract=context.contract))
    assert result.as_dict()['disposition'] == 'admitted', result.as_dict()
    assert len(calls) == 1 and short.as_dict() == before


def test_source_native_tries_admitted_confluence_with_certified_fallback(worker, native, monkeypatch):
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.observation_audit import SourceNativeComparison

    _, session = worker
    _value(session.binding(), 'bind')
    pair = session.exact._admitted_comparison
    rows, trace = native
    monkeypatch.setattr(session.exact._native, 'encoding', None)
    monkeypatch.setattr(session.exact._native, 'agreement', None)
    # This test requires a fresh audit, not the complete witness retained by
    # the module-scoped worker after an earlier identical native record.
    monkeypatch.setattr(session.exact._native, 'witness', None)
    original = type(pair).native_literals
    seen = []
    def literals(self, encoding, model, fixed, **kwargs):
        result = original(self, encoding, model, fixed, **kwargs)
        seen.append(result)
        return result
    monkeypatch.setattr(type(pair), 'native_literals', literals)
    certify = SourceNativeComparison._certificate
    certificates, phases = [], []
    def certificate(self, solve, defects, check, facts=None):
        def tracked(phase, assertions):
            phases.append(phase)
            return solve(phase, assertions)
        result = certify(self, tracked, defects, check, facts)
        certificates.append((facts, result))
        return result
    monkeypatch.setattr(SourceNativeComparison, '_certificate', certificate)
    witness = _value(session.call('audit-request', {'rows': rows, 'trace': trace.as_dict(),
                        'domain': session.exact.domain.digest()}), 'audit-request')
    assert witness and seen and len(certificates) == len(seen)
    assert all(facts is selected and result.answer is SolverAnswer.UNSAT
               for selected, (facts, result) in zip(seen, certificates, strict=True))
    # Replica/literal propagation is optional. If it declines the model,
    # agreement still requires an actual certificate, not assumed facts.
    if any(item is None for item in seen):
        assert {'native-source-certificate', 'native-certificate'} & set(phases)
    assert session.exact._native.encoding is pair.left


def test_fresh_native_member_avoids_a_second_decode_solve(worker, native, monkeypatch):
    from behave.sim.symbolic.exact_solve import z3_check_finite

    _, session = worker
    _value(session.binding(), 'bind')
    exact = session.exact
    assert exact.encoding is exact._admitted_comparison.left
    monkeypatch.setattr(exact, '_request_members', {})
    monkeypatch.setattr(exact._native, 'agreement', None)
    monkeypatch.setattr(exact._native, 'witness', None)
    rows, trace = native
    calls = []
    def solve(phase, roots):
        calls.append(phase)
        return z3_check_finite(roots, 30_000, _owned_context=exact.encoding.alive.ctx,
                               _prefer_finite=exact._prefer_finite)
    witness = exact._witness(rows, trace, solve=solve, check=lambda: None)
    assert witness and calls and exact._request_members
    assert exact._request_failure(rows, solve=lambda *_: pytest.fail('duplicate membership solve'),
                                  check=lambda: None) is None


@pytest.mark.parametrize('tampered', (False, True))
def test_query_member_handoff_saves_construction_not_native_agreement(worker, native, monkeypatch, tampered):
    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.core.audit import WitnessInconsistent
    from behave.sim.rtl.observation_audit import NativeProofCache

    _, session = worker
    _value(session.binding(), 'bind')
    exact = session.exact
    rows, trace = native
    monkeypatch.setattr(exact._native, 'agreement', None)
    monkeypatch.setattr(exact._native, 'proofs', NativeProofCache())
    monkeypatch.setattr(exact, '_pending_query_member', None)
    monkeypatch.setattr(exact._native, 'witness', None)
    answer = z3_check_finite((*exact.constraints, *exact.request.fix(rows)), 30_000,
        _owned_context=exact.encoding.alive.ctx, _prefer_finite=exact._prefer_finite)
    assert answer.answer.value == 'sat'
    exact._retain_query_member(rows, answer.model, lambda: None)
    assert exact._pending_query_member is not None
    phases = []
    def solve(phase, roots):
        phases.append(phase)
        assert not phase.startswith('feasibility'), 'the complete member must be handed off'
        return z3_check_finite(roots, 30_000, _owned_context=exact.encoding.alive.ctx,
                               _prefer_finite=exact._prefer_finite)
    if tampered:
        image = copy.deepcopy(trace.as_dict())
        image['records'][2]['state'] = dict(image['records'][2]['state'])
        image['records'][2]['state']['y'] ^= 1
        with pytest.raises(WitnessInconsistent):
            exact._witness(rows, api.AuditedTrace.from_dict(image), solve=solve, check=lambda: None)
    else:
        assert exact._witness(rows, trace, solve=solve, check=lambda: None)
    assert phases and 'native-source-certificate' in phases
    assert exact._pending_query_member is None


def test_native_proof_pool_reuses_earlier_input_after_another_fresh_replay(task, worker, native, monkeypatch):
    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.core.audit import WitnessInconsistent
    from behave.sim.rtl.observation_audit import NativeEvidence

    _, context, prepared, _, _, _ = task
    _, session = worker
    _value(session.binding(), 'bind')
    exact = session.exact
    rows, _ = native
    # The module-scoped worker may already have certified this same input.
    # Exercise a cold proof pool explicitly, then require cross-input reuse.
    monkeypatch.setattr(exact, '_native', NativeEvidence())
    # Change a complete public transaction field, not just its proof label.
    coordinate = (exact.request.codec.cells['in'][0][0] if exact.request.atomic
                  else exact.request.codec.cells[0][0])
    other = [{**row, coordinate: row[coordinate] ^ 1} for row in rows]
    calls, witnesses = [], []
    def solve(phase, roots):
        calls.append(phase)
        return z3_check_finite(roots, 30_000, _owned_context=exact.encoding.alive.ctx,
                               _prefer_finite=exact._prefer_finite)
    for index, inputs in enumerate((rows, other, rows)):
        calls.clear()
        trace = context.source_trace(exact.request.codec.decode(inputs),
                                     startup_state=prepared.initial_layout.decode(inputs))
        witnesses.append(exact._witness(inputs, trace, solve=solve, check=lambda: None))
        assert bool(calls) is (index != 2)
    assert witnesses[0] == witnesses[2] and witnesses[0] != witnesses[1]
    assert len(exact._native.proofs.entries) == 2
    # A tampered earlier observation still requires proof and fails. The
    # retained second input must not acquire this failed proof either.
    image = trace.as_dict()
    image['records'][2]['state'] = dict(image['records'][2]['state'])
    image['records'][2]['state']['y'] ^= 1
    calls.clear()
    with pytest.raises(WitnessInconsistent):
        exact._witness(rows, api.AuditedTrace.from_dict(image), solve=solve, check=lambda: None)
    assert calls and len(exact._native.proofs.entries) == 2


@pytest.mark.parametrize('mutation', ['sample', 'successor', 'payload', 'monitor'])
def test_source_worker_cannot_certify_tampered_native_evidence(task, worker, native, mutation):
    _, session = worker
    rows, trace = native
    image = trace.as_dict()
    records = image['records']
    for record in records:
        for field in ('outputs', 'state', 'accepted', 'monitor'):
            record[field] = dict(record[field])
    if mutation == 'sample':
        records[2]['outputs']['y'] ^= 1
    elif mutation == 'successor':
        records[2]['state']['y'] ^= 1
    elif mutation == 'payload':
        index = next(i for i, record in enumerate(records) if record['accepted'].get('out') is not None)
        records[index]['accepted']['out'][0] ^= 1
    else:
        records[2]['monitor']['forged'] = 1
    reply = session.call('audit-request', {'rows': rows, 'trace': image,
                                          'domain': session.exact.domain.digest()})
    assert reply['status'] == 'inconsistent', reply


def test_native_witness_reuse_requires_the_same_complete_record(worker, native, monkeypatch):
    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.core.audit import WitnessInconsistent

    _, session = worker
    exact = session.exact
    rows, trace = native
    # Exercise the independent full-proof fallback. The admitted-product
    # composition path has its own integration and adversarial tests.
    monkeypatch.setattr(exact, '_admitted_comparison', None)
    builds, phases, projected, environments = [], [], [], []
    from behave.sim.rtl import observation_audit
    from behave.sim.core import analysis as core_analysis
    project = observation_audit.compare_native_history
    environment = core_analysis.audit_environment_assumptions
    def observe(*args, **kwargs):
        result = project(*args, **kwargs)
        projected.append(result)
        return result
    def observe_environment(*args, **kwargs):
        environments.append(args)
        return environment(*args, **kwargs)
    monkeypatch.setattr(observation_audit, 'compare_native_history', observe)
    monkeypatch.setattr(core_analysis, 'audit_environment_assumptions', observe_environment)
    original = exact.relation.bind_native_request

    def bind(request, **kwargs):
        encoded = original(request, **kwargs)
        builds.append(encoded)
        return encoded

    def solve(phase, assertions):
        phases.append(phase)
        return z3_check_finite(assertions, 30_000, _owned_context=exact.encoding.alive.ctx,
                               _prefer_finite=exact._prefer_finite)

    monkeypatch.setattr(exact._native, 'encoding', None)
    monkeypatch.setattr(exact._native, 'agreement', None)
    monkeypatch.setattr(exact._native, 'witness', None)
    monkeypatch.setattr(exact.relation, 'bind_native_request', bind)
    first = exact._witness(rows, trace, solve=solve, check=lambda: None)
    expected = (['feasibility-source', 'native-source-certificate', 'native-certificate']
                if builds[0].monitor_constraints else ['feasibility', 'native-certificate'])
    # A fully validated fixed-request member can replace the source solve,
    # never either native correspondence certificate. A failed hint retains
    # the original path; helper tests check every root and both fallbacks.
    assert phases in (expected, ['feasibility-environment', *expected],
                      ['feasibility-environment', *expected[1:]])
    phases.clear()
    # A separately decoded complete record is equal without trusting its hash.
    again = exact._witness(rows, api.AuditedTrace.from_dict(trace.as_dict()),
                           solve=solve, check=lambda: None)
    assert again is first
    assert phases == []
    assert len(projected) == len(environments) == 1
    assert len(builds) == 1 and exact._native.encoding is builds[0]
    assert builds[0] is not exact.encoding

    image = trace.as_dict()
    image['records'][2]['state'] = dict(image['records'][2]['state'])
    image['records'][2]['state']['y'] ^= 1
    phases.clear()
    with pytest.raises(WitnessInconsistent, match='native-witness-inconsistent'):
        exact._witness(rows, api.AuditedTrace.from_dict(image), solve=solve, check=lambda: None)
    assert phases[-4:] == ['completeness', 'definedness', 'memory', 'membership']
    assert phases[:-4] in (expected, ['feasibility-environment', *expected],
                           ['feasibility-environment', *expected[1:]])
    assert len(builds) == 1
    assert len(projected) == 2
    # Failure never replaces the last successful proof, and cancellation is
    # checked even if the entire obligation was previously proved.
    assert exact._native.agreement is projected[0]
    assert exact._native.witness is None
    def cancel():
        raise InterruptedError('cancel retained native proof')
    with pytest.raises(InterruptedError):
        exact._witness(rows, trace, solve=solve, check=cancel)


def test_cancelled_native_construction_is_not_retained(worker, native, monkeypatch):
    _, session = worker
    exact = session.exact
    rows, trace = native
    monkeypatch.setattr(exact, '_admitted_comparison', None)
    calls = []

    def unfinished(request, **kwargs):
        calls.append(request)
        return object()

    def check():
        if calls:
            raise InterruptedError('native construction cancelled')

    monkeypatch.setattr(exact._native, 'encoding', None)
    monkeypatch.setattr(exact.relation, 'bind_native_request', unfinished)
    with pytest.raises(InterruptedError, match='native construction cancelled'):
        exact._witness(rows, trace, solve=lambda *args: pytest.fail('incomplete formula'), check=check)
    assert exact._native.encoding is None


@pytest.mark.parametrize('change,code', [
    ('artifact', 'artifact-mismatch'),
    ('incomplete', 'run-incomplete'),
    ('padded', 'run-incomplete'),
    ('state-overflow', 'state-unreadable'),
    ('state-negative', 'state-unreadable'),
])
def test_native_source_history_binds_artifact_completion_and_state(task, native, change, code):
    from behave.sim.rtl.source_trace import read_source_history

    _, context, _, _, request, _ = task
    _, trace = native
    image = trace.as_dict()
    if change == 'artifact':
        image['artifact'] = 'foreign-artifact'
    elif change in {'incomplete', 'padded'}:
        image['completion'] = change
    else:
        name, width = next(iter(request.codec.initial.widths.items()))
        state = dict(image['records'][0]['state'])
        state[name] = -1 if change == 'state-negative' else 1 << width
        image['records'][0]['state'] = list(state.items())
    with pytest.raises(api.Refused) as caught:
        read_source_history(trace.stimulus, api.AuditedTrace.from_dict(image),
                            request.codec.initial, context.frontend_catalogue)
    assert caught.value.refusal.kind == f'rtl-source-history-source-{code}'


@pytest.mark.parametrize('value', [None, -1, True, 'missing', 'overflow'])
def test_native_source_history_requires_typed_preclock_state(task, native, value):
    from behave.sim.rtl.source_trace import read_source_history

    _, context, _, _, request, _ = task
    _, trace = native
    image = trace.as_dict()
    name, width = next(iter(request.codec.initial.widths.items()))
    observed = dict(image['records'][0]['outputs'])
    if value == 'missing':
        observed.pop(name)
    else:
        observed[name] = (1 << width) if value == 'overflow' else value
    image['records'][0]['outputs'] = list(observed.items())
    with pytest.raises(api.Refused, match='source-sampled-state-unreadable'):
        read_source_history(trace.stimulus, api.AuditedTrace.from_dict(image),
                            request.codec.initial, context.frontend_catalogue)


def test_native_source_history_checks_false_positions_in_hit_catalogue(task, native):
    from behave.sim.core.hit_vector import HIT_VECTOR_ENCODING, HitVector
    from behave.sim.rtl.source_trace import read_source_history

    _, context, _, _, request, _ = task
    _, trace = native
    image = trace.as_dict()
    image['hit_encoding'] = HIT_VECTOR_ENCODING
    image['hit_catalogue'] = ['foreign-unhit-goal']
    for record in image['records']:
        record['hits'] = HitVector.from_names(('foreign-unhit-goal',)).as_compact_dict()
    # Keep native occurrences unchanged: even an all-false public bitmap must
    # belong to the same frozen catalogue, not an invented denominator.
    with pytest.raises(api.Refused, match='source-goal-shape'):
        read_source_history(trace.stimulus, api.AuditedTrace.from_dict(image),
                            request.codec.initial, context.frontend_catalogue)


@pytest.mark.parametrize('scope', ['foreign', 'phase-events'])
def test_source_worker_rejects_foreign_domain_before_solver(task, worker, native, monkeypatch, scope):
    from behave.sim.symbolic import exact_solve

    _, session = worker
    _value(session.binding(), 'bind')
    rows, trace = native
    monkeypatch.setattr(exact_solve, 'z3_check_finite', lambda *a, **kw: pytest.fail('foreign domain'))
    # A goal-prefix constructor cannot itself issue a phase-event domain.
    # Use the independently built phase-event fixture, not a forbidden cast.
    domain = ('foreign' if scope == 'foreign' else task[2].trace_domain(6).digest())
    assert domain != session.exact.domain.digest()
    reply = session.call('audit-request', {'rows': rows, 'trace': trace.as_dict(), 'domain': domain})
    assert reply['refusal']['kind'] == 'rtl-analysis-request-domain-mismatch'


@pytest.mark.parametrize('phase', ['feasibility', 'completeness', 'definedness', 'memory',
                                  'membership', 'agreement'])
def test_native_certificate_requires_every_audit_obligation(worker, native, monkeypatch, phase):
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.observation_audit import SourceNativeComparison

    _, session = worker
    _value(session.binding(), 'bind')
    # The module-scoped worker may already own this complete proved record.
    # This test exercises a NEW proof's fail-closed gates, not reuse of a
    # previously successful identical obligation (tested separately above).
    monkeypatch.setattr(session.exact._native, 'agreement', None)
    monkeypatch.setattr(session.exact, '_request_members', {})
    rows, trace = native
    original = SourceNativeComparison.audit
    phases = ['feasibility', 'native-source-certificate', 'native-certificate', 'completeness', 'definedness',
              'memory', 'membership', 'agreement']
    calls = []

    def audit(comparison, solve, **kwargs):
        def checker(obligation, assertions):
            calls.append(obligation)
            # A conclusive joint certificate proves the individual obligations.
            # Make it inconclusive before testing each fallback's fail-closed rule.
            if obligation in {'native-certificate', phase}:
                return ExactCheckResult(SolverAnswer.UNKNOWN, reason='native audit unavailable')
            return solve(obligation, assertions)
        return original(comparison, checker, **kwargs)

    monkeypatch.setattr(SourceNativeComparison, 'audit', audit)
    reply = session.call('audit-request', {'rows': rows, 'trace': trace.as_dict(),
                                          'domain': session.exact.domain.digest()})
    assert reply['status'] == 'refused'
    assert reply['refusal']['kind'] == 'rtl-source-native-check-unresolved'
    assert phase in reply['refusal']['why']
    assert calls == phases[:1 + phases.index(phase)]
    assert not session.exact._request_members  # A partial audit cannot credit decode.


def test_native_witness_preserves_startup_only_hits(tmp_path):
    from behave.sim.rtl.native_evidence_codec import pack, unpack

    from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract

    source = HISTORY_SOURCE.replace('assign ir=!rst && (!ov || ordy);',
        "assign ir=(a==3) ? 1'b0 : (!rst && (!ov || ordy));")
    context, prepared, _, request, _ = task_fixtures._worker(
        tmp_path, source, _simple_contract(reset_policy='clears'), 5)
    exact = analysis_job._SourceAnalysis(prepared, 5)
    coordinate = next(key for key, name, _ in prepared.initial_layout.pin_coordinates if name == 'a')
    solver = z3.Solver(ctx=exact.encoding.alive.ctx)
    solver.set(timeout=30_000)
    solver.add(*exact.constraints, *(term == (3 if name == coordinate else 0)
        for name, term in prepared.initial_binding.items()), request.lengths['in'] == 1,
        request.queues['in'][0][0] == 1)
    for i in range(5):
        solver.add(request.resets['c'][i] == (i == 0),
                   request.actions[i].controls['ordy'], request.actions[i].offers['in'] == (i == 1))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    trace = context.source_trace(request.codec.decode(rows), startup_state=prepared.initial_layout.decode(rows))
    operation = {'rows': rows, 'trace': trace.as_dict(), 'domain': exact.domain.digest()}
    witness = AuditedWitness.from_record(_value(
        analysis_job._run_bound_operation('audit-request', operation, exact, None), 'audit-request'))
    initial_only = witness.initial_hits - set().union(*witness.hit_sets)
    assert initial_only, 'the startup input must exercise goals absent from all later slots'
    assert initial_only <= witness.hits
    assert initial_only <= set(prepared.complete)
    # Initial/final blocks are outside the step-goal profile. This runtime
    # conditional instead executes at startup with a=3, then only sees 0 or 1.
    assert any('a==3' in source.encode()[dict(goal.provenance)['start']:dict(goal.provenance)['end']].decode()
               for key, goal in context.frontend_catalogue.items() if key in initial_only)
    # A well-formed but different startup occurrence cannot support this witness.
    changed = trace.as_dict()
    interval = changed['source_evidence']['intervals'][0]
    # Preserve a valid-width vector and callback count, but remove its true startup events.
    interval['events'] = pack(bytes(len(unpack(interval['events']))))
    interval['callbacks'] = 0
    refused = analysis_job._run_bound_operation('audit-request', {**operation, 'trace': changed}, exact, None)
    assert refused['status'] in {'refused', 'inconsistent'}


def test_query_environment_does_not_assume_the_dut_accepts_its_inputs(tmp_path):
    from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract

    context, prepared, _, request, _ = task_fixtures._worker(tmp_path,
        HISTORY_SOURCE.replace('assign ir=!rst && (!ov || ordy);', "assign ir=1'b0;"),
        _simple_contract(), 5)
    exact = analysis_job._SourceAnalysis(prepared, 5)
    outside = z3.Solver(ctx=exact.encoding.alive.ctx)
    outside.set(timeout=30_000)
    outside.add(*exact.encoding.constraints,
                exact.encoding.terminal_assumptions['environment.drain-missing'])
    assert outside.check() == z3.sat
    invalid_rows = request.rows(outside.model())
    refused = analysis_job._run_bound_operation('decode', {'rows': invalid_rows}, exact, None)
    assert refused['refusal']['kind'] == 'rtl-source-request-outside-domain'
    verdict = Verdict.from_record(_value(analysis_job._run_bound_operation('query',
        {'goals': ['monitor.input-completion'], 'timeout_ms': 60_000}, exact, None), 'query'))
    assert 'monitor.input-completion' in verdict.reachable
    rows = [dict(row) for row in verdict.reachable['monitor.input-completion']]
    trace = context.source_trace(request.codec.decode(rows), startup_state=prepared.initial_layout.decode(rows))
    witness = AuditedWitness.from_record(_value(analysis_job._run_bound_operation('audit-request',
        {'rows': rows, 'trace': trace.as_dict(), 'domain': exact.domain.digest()}, exact, None), 'audit-request'))
    assert 'monitor.input-completion' in witness.hits
    assert not any(dict(record.accepted).get('in') for record in trace.records)


def test_source_worker_bind_identity_is_complete_and_not_legacy(task, worker):
    _, context, prepared, _, _, _ = task
    _, session = worker
    binding = _value(session.binding(), 'bind')
    domain = prepared.trace_domain(6, observation_scope=session.exact.observation_scope)
    assert domain.digest() != prepared.trace_domain(6).digest()
    assert domain.schema['source_observation_scope'] == 'goal-prefix/1'
    assert binding['domain'] == domain.as_dict()
    assert binding['domain_digest'] == domain.digest()
    assert binding['catalogue'] == context.complete_catalogue.as_dict()
    assert binding['contract'] == domain.contract
    assert domain.initial['kind'] == 'pre-hdl-construction'
    assert domain.schema['source_image'] == prepared.image_digest
    assert not any(row['code'] == 'yosys-smt-fidelity' for row in binding['trusted_assumptions'])
    assert context.goal_capability_use is None  # A worker binding is not public admission.


def test_source_worker_queries_reset_safety_and_replays_real_sat_inputs(task, worker):
    mode, context, prepared, relation, request, _ = task
    _, session = worker
    violation = ResetRestorationProvider.violation_goal('c')
    scenario = ResetRestorationProvider.scenario_goal('c')
    reply = session.call('query', {'goals': [violation, scenario], 'timeout_ms': 60_000})
    verdict = Verdict.from_record(_value(reply, 'query'))
    assert not verdict.undecided
    assert scenario in verdict.reachable
    assert (violation in verdict.reachable) == (mode == 'broken-reset')
    assert (violation in verdict.unreachable) == (mode != 'broken-reset')
    sat = next(call for call in verdict.queries if call.answer.value == 'sat')
    decoded = _value(session.call('decode', {'rows': sat.witness}), 'decode')
    assert decoded['stimulus'] == request.codec.decode(sat.witness).as_dict()
    assert decoded['initialization'] == prepared.initial_layout.decode(sat.witness).as_dict()
    trace = context.source_trace(request.codec.decode(sat.witness),
                                 startup_state=prepared.initial_layout.decode(sat.witness))
    audit, _ = _audit(relation.compare_native(request, sat.witness, trace,
        context.frontend_catalogue, observation=prepared.observation))
    assert audit.status.value == 'agreed', audit.as_dict()


def test_source_worker_queries_the_entire_frozen_catalogue(task, worker):
    from behave.sim.rtl.source_goals import is_source_goal
    from behave.sim.rtl.source_trace import read_source_history

    _, context, prepared, _, request, _ = task
    _, session = worker
    goals = list(context.complete_catalogue)
    verdict = Verdict.from_record(_value(session.call('query',
        {'goals': goals, 'timeout_ms': 60_000}), 'query'))
    assert not verdict.undecided
    assert set(verdict.reachable) | verdict.unreachable == set(goals)
    for call in verdict.queries:
        timings = call.telemetry.timings.as_dict()
        assert timings['total_ns'] >= timings['solver_load_ns'] + timings['solve_ns']
        assert timings['solver_load_ns'] >= 0 and timings['solve_ns'] >= 0
        assert 'replay_audit_ns' not in timings  # Owned by the later independent audit.
        assert ('decode_ns' in timings) == (call.answer.value == 'sat')
    source_goals = {name for name, goal in context.frontend_catalogue.items() if is_source_goal(goal)}
    assert source_goals & set(verdict.reachable)
    # Check a source-event witness, not only the task-monitor predicates used
    # by the reset test. Startup events are not credited to a later slot.
    goal = min(source_goals & set(verdict.reachable))
    rows = [dict(row) for row in verdict.reachable[goal]]
    stimulus = request.codec.decode(rows)
    trace = context.source_trace(stimulus, startup_state=prepared.initial_layout.decode(rows))
    history = read_source_history(stimulus, trace, prepared.initial_layout,
        context.frontend_catalogue, observation=prepared.observation)
    assert any(goal in phase.goals for _, phase in history.phases)
    audited = AuditedWitness.from_record(_value(session.call('audit-request',
        {'rows': rows, 'trace': trace.as_dict(), 'domain': session.exact.domain.digest()}),
        'audit-request'))
    assert goal in audited.hits


def test_source_worker_never_reuses_target_answers(task, worker, monkeypatch):
    from behave.sim.symbolic import exact_solve
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.core.query import SolverAnswer

    _, session = worker
    _value(session.call('feasible', {'timeout_ms': 60_000}), 'feasible')
    operation = {'goals': ['monitor.input-completion'], 'timeout_ms': 60_000}
    previous = Verdict.from_record(_value(session.call('query', operation), 'query'))
    assert not previous.undecided
    original = exact_solve.z3_check_finite
    calls = []

    def unknown(assertions, timeout_ms, **kwargs):
        calls.append(timeout_ms)
        return ExactCheckResult(SolverAnswer.UNKNOWN, reason='injected unresolved target')

    monkeypatch.setattr(exact_solve, 'z3_check_finite', unknown)
    reply = session.call('query', {**operation, 'timeout_ms': 10_000})
    verdict = Verdict.from_record(_value(reply, 'query'))
    assert set(verdict.undecided) == {'monitor.input-completion'}
    assert not verdict.reachable and not verdict.unreachable
    assert verdict.queries[0].telemetry.provenance['source_domain_admission'] == 'reused'
    assert verdict.queries[0].solver_calls == 1
    # This request is already a singleton: UNKNOWN must not repeat the same
    # target, nor reuse the earlier successful answer.
    assert len(calls) == len(verdict.queries) == 1
    assert all(0 < cap <= 10_000 for cap in calls)
    assert all('solve_ns' not in call.telemetry.timings.as_dict() for call in verdict.queries)
    monkeypatch.setattr(exact_solve, 'z3_check_finite', original)
    assert _value(session.call('feasible', {'timeout_ms': 60_000}), 'feasible')['answer'] == 'feasible'


def test_source_worker_reuses_only_complete_domain_admission(task, monkeypatch, record_property):
    from behave.sim.symbolic import exact_solve

    exact = analysis_job._SourceAnalysis(task[2], 6)
    calls = []

    def counted(original):
        def checker(*args, **kwargs):
            calls.append(None)
            return original(*args, **kwargs)
        return checker

    for name in ('z3_check_finite', 'z3_check'):
        monkeypatch.setattr(exact_solve, name, counted(getattr(exact_solve, name)))
    measured = []
    for expected in ['proved', 'reused']:
        calls.clear()
        started = time.monotonic()
        reply = _value(analysis_job._run_bound_operation('feasible', {'timeout_ms': 60_000},
            exact, None), 'feasible')
        measured.append({'admission': expected, 'seconds': time.monotonic() - started})
        assert reply['answer'] == 'feasible'
        assert reply['telemetry']['provenance']['source_domain_admission'] == expected
        timing = reply['telemetry']['timings_ns']
        reused = reply['telemetry']['provenance']['feasibility_member'] == 'admission-request'
        assert reused == (exact._admitted_request is not None)
        assert timing['total_ns'] >= 0
        if reused:
            assert 'solver_load_ns' not in timing and 'solve_ns' not in timing
        else:
            assert timing['solver_load_ns'] >= 0 and timing['solve_ns'] >= 0
            assert timing['total_ns'] >= timing['solver_load_ns'] + timing['solve_ns']
        # Physical admission strategies may differ, but the first domain must
        # actually be proved and the retained full member needs no new solve.
        if expected == 'proved':
            assert calls
        else:
            assert len(calls) == (0 if reused else 1)
    record_property('domain_admission_operations', repr(measured))
    for goal in [ResetRestorationProvider.violation_goal('c'),
                 ResetRestorationProvider.scenario_goal('c')]:
        calls.clear()
        verdict = Verdict.from_record(_value(analysis_job._run_bound_operation('query',
            {'goals': [goal], 'timeout_ms': 60_000}, exact, None), 'query'))
        assert not verdict.undecided
        assert len(calls) == sum(call.solver_calls for call in verdict.queries) == 1
        assert verdict.queries[0].telemetry.provenance['source_domain_admission'] == 'reused'


def test_late_source_solver_retains_cost_but_not_answer_authority(worker, monkeypatch):
    from behave.sim.symbolic import exact_solve
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.core.query import SolverAnswer

    _, session = worker
    expired, calls = [False], []

    class Budget:
        def __init__(self, _timeout):
            pass

        def remaining_ms(self):
            return 0 if expired[0] else 1000

    def late(*_args, **_kwargs):
        calls.append('solve')
        expired[0] = True
        return ExactCheckResult(SolverAnswer.UNSAT, solver_load_ns=17, solve_ns=23)

    # Isolate query accounting from admission; no artificial solver answer is
    # used to admit a real domain or to produce a native witness.
    monkeypatch.setattr(session.exact, '_admit_domain', lambda *_args, **_kwargs: 'reused')
    monkeypatch.setattr(exact_solve, '_Budget', Budget)
    monkeypatch.setattr(exact_solve, 'z3_check_finite', late)
    verdict = Verdict.from_record(_value(session.call('query',
        {'goals': ['monitor.input-completion'], 'timeout_ms': 10_000}), 'query'))
    assert calls == ['solve']
    assert not verdict.reachable and not verdict.unreachable
    assert set(verdict.undecided) == {'monitor.input-completion'}
    first, = verdict.queries
    assert first.answer is SolverAnswer.UNKNOWN
    assert first.solver_calls == 1
    measured = first.telemetry.timings.as_dict()
    assert measured['solver_load_ns'] == 17 and measured['solve_ns'] == 23


def test_source_timing_failure_cannot_replace_a_solver_answer(worker, monkeypatch):
    _, session = worker
    operation = {'goals': ['monitor.input-completion'], 'timeout_ms': 60_000}
    before = Verdict.from_record(_value(session.call('query', operation), 'query'))

    def fail(*_args, **_kwargs):
        raise RuntimeError('injected telemetry failure')

    # Queries aggregate local, prefix and final solver costs directly in the
    # StageTimings carrier; the older single-answer helper is not this path.
    monkeypatch.setattr(analysis_job, 'StageTimings', fail)
    after = Verdict.from_record(_value(session.call('query', operation), 'query'))
    assert set(after.reachable) == set(before.reachable)
    assert after.unreachable == before.unreachable
    assert after.undecided == before.undecided
    # The public wrapper still attaches provenance, without failed timings.
    assert all(call.telemetry.timings is None for call in after.queries)
    # The same diagnostic failure must not veto reused feasibility either.
    feasible = _value(session.call('feasible', {'timeout_ms': 60_000}), 'feasible')
    assert feasible['answer'] == 'feasible' and feasible['telemetry'] is None


@pytest.mark.parametrize('horizon', [5, 6])
def test_another_worker_or_horizon_must_prove_its_domain_again(task, worker, monkeypatch, horizon):
    from behave.sim.symbolic import exact_solve
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.core.query import SolverAnswer

    _, session = worker
    _value(session.binding(), 'bind')
    if task[2].schedule is not None and horizon != len(task[2].schedule.slots):
        # A different horizon cannot silently shorten an explicitly fixed schedule.
        with pytest.raises(ValueError, match='horizon must equal the bound global schedule'):
            analysis_job._SourceAnalysis(task[2], horizon)
        return
    exact = analysis_job._SourceAnalysis(task[2], horizon)
    assert (exact.domain.digest() == session.exact.domain.digest()) == (horizon == 6)
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(None)
        return ExactCheckResult(SolverAnswer.UNKNOWN, reason='fresh domain needs admission')

    monkeypatch.setattr(exact_solve, 'z3_check_finite', unavailable)
    reply = analysis_job._run_bound_operation('feasible', {'timeout_ms': 60_000}, exact, None)
    assert reply['status'] == 'refused'
    assert reply['refusal']['kind'] == 'rtl-source-domain-check-unresolved'
    assert 'feasibility' in reply['refusal']['why'] and len(calls) == 2


def test_admitted_session_cannot_rebind_its_domain(worker, monkeypatch):
    from behave.sim.symbolic import exact_solve

    _, session = worker
    _value(session.binding(), 'bind')
    monkeypatch.setattr(exact_solve, 'z3_check_finite', lambda *a, **kw: pytest.fail('foreign fields'))
    for field in ('artifact', 'horizon', 'clock_schedule', 'memory_environment',
                  'admission', 'catalogue'):
        with pytest.raises(TypeError, match='exactly its declared fields'):
            session.call('feasible', {'timeout_ms': 60_000, field: None})
    with pytest.raises(ValueError, match='cannot bind again'):
        session.call('bind', {})


def test_admitted_domain_still_checks_cancellation(worker, monkeypatch):
    from behave.isolation.preparation_control import PreparationCancelled, preparation_scope
    from behave.sim.symbolic import exact_solve

    _, session = worker
    _value(session.binding(), 'bind')
    monkeypatch.setattr(exact_solve, 'z3_check_finite', lambda *a, **kw: pytest.fail('cancelled'))

    def cancelled():
        raise PreparationCancelled('test cancellation after domain admission')

    with preparation_scope(cancelled), pytest.raises(PreparationCancelled):
        session.call('query', {'goals': ['monitor.input-completion'], 'timeout_ms': 60_000})


def test_cancelled_domain_proof_is_not_retained(task, monkeypatch):
    from behave.isolation.preparation_control import PreparationCancelled, preparation_scope
    from behave.sim.core.audit import AuditCheck
    from behave.sim.rtl.source_confluence import SourceOrderComparison

    exact = analysis_job._SourceAnalysis(task[2], 6)
    cancelled = False
    calls = []

    def check():
        if cancelled:
            raise PreparationCancelled('test cancellation before retaining proof')

    def audit(comparison, solve, *, prefix_proofs, check):
        nonlocal cancelled
        assert prefix_proofs is True
        check()
        calls.append(comparison)
        cancelled = True
        return AuditCheck.agreed()

    monkeypatch.setattr(SourceOrderComparison, 'audit', audit)
    original = None
    for attempt in range(2):
        cancelled = False
        with preparation_scope(check), pytest.raises(PreparationCancelled):
            analysis_job._run_bound_operation('feasible', {'timeout_ms': 60_000}, exact, None)
        assert len(calls) == attempt + 1
        assert exact._admitted_comparison is None
        assert exact._admitted_request is None
        if original is None:
            original = exact._comparison
        assert exact._comparison is original
    # The immutable original syntax may survive cancellation. A temporary
    # inductive/abstract proof view may be rebuilt; neither is an admission.
    assert original is not None


def test_source_worker_closed_requests_and_absent_evidence_stay_refused(task, worker):
    _, session = worker
    reply = session.call('query', {'goals': ['not-a-goal'], 'timeout_ms': 1000})
    assert reply['refusal']['kind'] == 'rtl-source-query-goal-unknown'
    feasible = _value(session.call('feasible', {'timeout_ms': 60_000}), 'feasible')
    rows = feasible['witness']
    assert len(rows) == 6
    assert _value(session.call('decode', {'rows': rows}), 'decode')
    changed = copy.deepcopy(rows)
    changed[1][next(iter(task[2].initial_layout.bounds))] ^= 1
    assert session.call('decode', {'rows': changed})['status'] == 'refused'
    assert session.call('decode', {'rows': rows[:-1]})['refusal']['kind'] == 'rtl-analysis-request-horizon-mismatch'
    reply = session.call('step-audit', {'traces': [rows]})
    assert _value(reply, 'step-audit') == {'status': 'agreed'}
    assert session.call('step-audit', {'traces': []})['refusal']['kind'] == 'rtl-source-step-audit-empty'


def test_source_record_audit_checks_saved_execution_without_replaying(task, worker, native):
    _, context, prepared, _, request, _ = task
    _, session = worker
    rows, trace = native
    operation = {'stimulus': trace.stimulus.as_dict(), 'raw_trace': trace.as_dict()}
    receipt = _value(session.call('record-audit', operation), 'record-audit')
    assert receipt['disposition'] == 'admitted', receipt
    witness = AuditedWitness.from_record(receipt['witness'])
    assert witness.inputs == tuple(request.codec.encode(trace.stimulus,
        initial_state=prepared.initial_layout.decode(rows)))
    assert receipt['witness_ref'] == 'witness:' + witness.fingerprint
    assert set(receipt['audits']['audits']) == {'step_audit', 'source_audit'}
    for field in ('outputs', 'state'):
        changed = copy.deepcopy(operation)
        record = changed['raw_trace']['records'][2]
        record[field] = dict(record[field])
        record[field]['y'] ^= 1
        assert session.call('record-audit', changed)['status'] == 'inconsistent'
    changed = copy.deepcopy(operation)
    changed['raw_trace'].pop('initialization')
    receipt = _value(session.call('record-audit', changed), 'record-audit')
    assert receipt['disposition'] == 'refused'
    assert receipt['kind'] == 'rtl-record-construction-unrecorded'


def test_public_source_owner_reuses_a_native_prefix_without_another_execution(task, native, tmp_path, monkeypatch):
    from behave.sim.core.clock_schedule import GlobalSchedule
    from behave.sim.core.failure import RunFailure
    from behave.sim.core.goal_provider import EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY
    from behave.sim.core.run_record import RunResult
    from behave.sim.goal_policy import evaluation_goal_policy
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance
    from behave.sim.rtl.exact_job_context import RTLExactJobContext
    from behave.sim.rtl.source_boundary_capture import source_history_prefix
    from behave.sim.stimulus_set import GenerationSpec

    _, existing, prepared, _, _, _ = task
    rows, original = native
    prefix = source_history_prefix(original, 4)
    # Independent short execution is a TEST oracle, never production admission.
    again = existing.source_trace(prefix.stimulus,
                                  startup_state=prepared.initial_layout.decode(rows))
    assert prefix.records == again.records
    assert prefix.source_evidence == again.source_evidence
    schedule = existing.schedule
    if schedule is not None:
        schedule = GlobalSchedule(schedule.timings, schedule.slots[:4])
    spec = existing.generation_spec
    context = RTLExactJobContext(existing.artifact, existing.contract,
        GenerationSpec(4, spec.queue_bound, spec.reset_hold_cycles, spec.drain, spec.stall_every),
        existing.frontend_catalogue, existing.complete_catalogue,
        workdir=tmp_path/'prefix-context', clock_schedule=schedule)
    if existing.memory_environment is not None:
        context.bind_memory_environment(existing.memory_environment)
    policy = evaluation_goal_policy(context.contract, context.frontend_catalogue, artifact_kind='rtl')
    context.bind_goal_policy(policy, policy.bind_complete_implementation(
        EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY, policy.manifest))
    admitted = api.admit(context.contract, api.realization_of_rtl_source(
        context.artifact.rtl_path.read_text(), 'prefix reuse regression', top='M', contract=context.contract))
    context.bind_admitted_task(admitted)
    def no_replay(*args, **kwargs):
        pytest.fail('saved record admission must not launch native execution')
    monkeypatch.setattr(type(context.artifact), 'exact_trace', no_replay)
    def record(trace):
        return RunResult.not_compared(trace.stimulus, trace, RunFailure.verifier(
            'test-record', Refusal('not-compared', 'no scoring claim')))
    proxy = RTLIsolatedAnalysisInstance(context, 4)
    with proxy.execution_scope():
        long_receipt = proxy.audit_record(record(original), admitted).as_dict()
        short_receipt = proxy.audit_record(record(again), admitted).as_dict()
        assert long_receipt['disposition'] == short_receipt['disposition'] == 'admitted'
        assert long_receipt['witness'] == short_receipt['witness']
        changed = original.as_dict()
        changed['records'][2]['state'] = dict(changed['records'][2]['state'])
        changed['records'][2]['state']['y'] ^= 1
        assert proxy.audit_record(record(api.AuditedTrace.from_dict(changed)), admitted).as_dict()[
            'disposition'] == 'inconsistent'


@pytest.mark.parametrize('runtime_request', [False, True], ids=['explicit-history', 'runtime-request'])
@pytest.mark.parametrize('bounded_operation_scope', [180], indirect=True, ids=['public-budget'])
def test_public_source_owner_replays_new_requests_but_reuses_saved_records(
        task, native, monkeypatch, tmp_path, record_property, runtime_request):
    from behave.sim.core.failure import RunFailure
    from behave.sim.core.goal_provider import EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY
    from behave.sim.core.run_record import RunResult
    from behave.sim.goal_policy import evaluation_goal_policy
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance
    from behave.sim.rtl.exact_job_context import RTLExactJobContext
    from behave.sim.rtl.replay_build import ReplayBuildScope

    _, existing, _, _, _, _ = task
    rows, old_trace = native
    context = RTLExactJobContext(existing.artifact, existing.contract, existing.generation_spec,
        existing.frontend_catalogue, existing.complete_catalogue,
        workdir=tmp_path/'public-context', clock_schedule=existing.schedule)
    if existing.memory_environment is not None:
        context.bind_memory_environment(existing.memory_environment)
    context.replay._source_traces.update(existing.replay._source_traces)
    policy = evaluation_goal_policy(context.contract, context.frontend_catalogue, artifact_kind='rtl')
    context.bind_goal_policy(policy, policy.bind_complete_implementation(
        EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY, policy.manifest))
    admitted = api.admit(context.contract, api.realization_of_rtl_source(
        context.artifact.rtl_path.read_text(), 'public source owner', top='M', contract=context.contract))
    context.bind_admitted_task(admitted)
    saved_cache = dict(context.replay._source_traces)
    calls = []
    replayed = []
    builds = []
    timings = []
    original = type(context.artifact).exact_trace
    compile_build = ReplayBuildScope.compile
    operation = RTLIsolatedAnalysisInstance._raw_call

    def execute(artifact, *args, **kwargs):
        replayed.append((args[1], kwargs.get('startup_state')))
        started = time.monotonic()
        trace = original(artifact, *args, **kwargs)
        timings.append(('native', time.monotonic() - started))
        calls.append(trace)
        return trace

    monkeypatch.setattr(type(context.artifact), 'exact_trace', execute)

    def compile_once(scope, *args, **kwargs):
        result = compile_build(scope, *args, **kwargs)
        builds.append(result[1])
        return result

    monkeypatch.setattr(ReplayBuildScope, 'compile', compile_once)

    def measured(proxy, name, **kwargs):
        started = time.monotonic()
        try:
            return operation(proxy, name, **kwargs)
        finally:
            timings.append((name, time.monotonic() - started))
            record_property('operations', repr(timings))

    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call', measured)
    proxy = RTLIsolatedAnalysisInstance(context, 6)
    assert proxy.domain.initial['kind'] == 'pre-hdl-construction'
    with proxy.execution_scope():
        witness = proxy.audit_request(rows, proxy.domain.digest())
        assert type(witness) is AuditedWitness, witness.as_dict()
        assert len(calls) == 1 and calls[0] is not old_trace
        assert witness_replay_check(witness, proxy.replay(rows)).status.value == 'agreed'
        assert len(calls) == 2 and calls[1] is not calls[0]
        assert proxy.audit([rows]).status.value == 'agreed'
        request, raw_trace = old_trace.stimulus, old_trace
        if runtime_request:
            from behave.sim.core.runtime_schedule import ReplayPolicy

            schedule = old_trace.stimulus.clock_schedule
            request = api.Stimulus(dict(old_trace.stimulus.queues), replay_policy=ReplayPolicy(
                clock_timings=() if schedule is None else schedule.timings))
            image = old_trace.as_dict()
            image['request'] = request.as_dict()
            raw_trace = api.AuditedTrace.from_dict(image)
        raw = RunResult.not_compared(request, raw_trace, RunFailure.verifier(
            'test-raw', Refusal('test-not-compared', 'testing exact raw evidence, not scoring')))
        receipt = proxy.audit_record(raw, admitted).as_dict()
        assert receipt['disposition'] == 'admitted', receipt
        assert len(calls) == 2, 'record admission must not launch another native execution'
        assert set(receipt['audits']['audits']) == {'step_audit', 'source_audit'}
        assert replayed[-1][0] == old_trace.stimulus
        assert replayed[-1][1].as_dict() == replayed[0][1].as_dict()
    assert proxy._session is None
    assert context.replay._source_traces == saved_cache
    assert builds == [False, True]  # New requests run; saved evidence does not.
    record_property('build_reuse', repr(builds))


@pytest.mark.parametrize('hint_only', [False, True])
def test_source_step_audit_unknown_and_bad_rows_cannot_be_agreement(worker, native, monkeypatch, hint_only):
    from behave.sim.symbolic import exact_solve
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.source_member import SourceMemberPlan

    _, session = worker
    _value(session.binding(), 'bind')
    rows, _ = native
    monkeypatch.setattr(session.exact, '_request_members', {})
    original = exact_solve.z3_check_finite
    # Keep the expressions alive: Z3 may recycle IDs of temporary fixed roots.
    full_assertions = (*session.exact.constraints, *session.exact.request.fix(rows))
    full_roots = tuple(term.get_id() for term in full_assertions)
    calls = []

    def checker(assertions, timeout_ms, **kwargs):
        roots = tuple(term.get_id() for term in assertions)
        calls.append(roots)
        if not hint_only or roots != full_roots:
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='fixed request unavailable')
        return original(assertions, timeout_ms, **kwargs)

    monkeypatch.setattr(exact_solve, 'z3_check_finite', checker)
    answer = _value(session.call('step-audit', {'traces': [rows]}), 'step-audit')
    has_hint = bool(session.exact.encoding.monitor_constraints)
    has_source_hint = type(session.exact.encoding.member_plan) is SourceMemberPlan
    if hint_only:
        # BOTH the environment and monitor hints are inconclusive. Only the
        # actual complete-root SAT fallback can establish agreement.
        assert answer['status'] == 'agreed'
    else:
        assert answer['status'] == 'refused'
        assert answer['refusal']['kind'] == 'rtl-source-step-request-unresolved'
    assert len(calls) == 1 + has_hint + has_source_hint
    assert calls[-1] == full_roots
    assert all(roots != full_roots for roots in calls[:-1])
    calls.clear()
    rejected = session.call('step-audit', {'traces': [rows[:-1]]})
    assert rejected['refusal']['kind'] == 'rtl-analysis-request-horizon-mismatch'
    assert not calls


def test_source_worker_exhausted_budget_never_launches_a_solver(task, worker, monkeypatch):
    from behave.sim.symbolic import exact_solve

    _, session = worker
    _value(session.binding(), 'bind')
    monkeypatch.setattr(exact_solve, 'z3_check_finite', lambda *a, **kw: pytest.fail('budget exhausted'))
    reply = analysis_job._run_bound_operation('query',
        {'goals': ['monitor.input-completion'], 'timeout_ms': 1}, session.exact, session.task,
        started_ns=time.monotonic_ns()-10_000_000)
    assert reply['status'] == 'refused'
    assert reply['refusal']['kind'] == 'rtl-source-analysis-budget-exhausted'


def test_real_isolated_source_worker_query_matches_session(task, worker):
    from behave.sim.worker import run_job_strict

    payload, session = worker
    goals = [ResetRestorationProvider.violation_goal('c'), ResetRestorationProvider.scenario_goal('c')]
    operation = {'goals': goals, 'timeout_ms': 60_000}
    fresh = _value(run_job_strict('rtl_exact_analysis',
        {**payload, 'operation': 'query', **operation}, timeout_s=120), 'query')
    reused = _value(session.call('query', operation), 'query')
    assert fresh['unreachable'] == reused['unreachable']
    assert fresh['reachable'].keys() == reused['reachable'].keys()
    assert fresh['undecided'] == reused['undecided'] == {}


@pytest.mark.parametrize('phase', ['feasibility', 'completeness', 'definedness', 'memory', 'confluence'])
@pytest.mark.parametrize('failure', ['unknown', 'contradiction'])
def test_source_query_cannot_skip_a_failed_domain_obligation(task, monkeypatch, phase, failure):
    from contextlib import nullcontext

    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl import source_proofs
    from behave.sim.rtl.source_confluence import SourceOrderComparison

    exact = analysis_job._SourceAnalysis(task[2], 6)
    # Inject at named admission gates, not physical solver-call positions:
    # any relaxed SAT can add a complete-domain recheck of the SAME gate.
    # Keep fabricated failure models on the owner context. Parallel proof
    # consumption/cancellation has its own independent oracle tests.
    monkeypatch.setattr(source_proofs, 'order_proof_scope',
                        lambda _pair, solve, _check: nullcontext(solve))
    stages = ['feasibility', 'completeness', 'definedness', 'memory', 'confluence']
    calls = []
    attempts = []

    def checker(solve, label, assertions):
        stage = label.split(':', 1)[0]
        calls.append(stage)
        correct = SolverAnswer.SAT if stage == 'feasibility' else SolverAnswer.UNSAT
        if stage == phase:
            if failure == 'unknown':
                # A timeout at any gate has no reachability meaning.
                return ExactCheckResult(SolverAnswer.UNKNOWN, reason=f'{stage} unavailable')
            if correct is SolverAnswer.SAT:
                return ExactCheckResult(SolverAnswer.UNSAT)
            solver = z3.Solver(ctx=exact.encoding.alive.ctx)
            assert solver.check() == z3.sat
            return ExactCheckResult(SolverAnswer.SAT, model=solver.model())
        if correct is SolverAnswer.SAT:
            return solve(label, assertions)  # Genuine, fully checked membership.
        return ExactCheckResult(correct)

    original = SourceOrderComparison.audit

    def audit(pair, solve, **options):
        attempts.append((pair, len(calls)))
        return original(pair, lambda label, roots: checker(solve, label, roots), **options)

    monkeypatch.setattr(SourceOrderComparison, 'audit', audit)
    for _ in range(2):
        calls.clear()
        attempts.clear()
        reply = analysis_job._run_bound_operation('query',
            {'goals': ['monitor.input-completion'], 'timeout_ms': 60_000}, exact, None)
        assert reply['status'] == 'refused'
        if failure == 'unknown':
            assert reply['refusal']['kind'] == 'rtl-source-domain-check-unresolved'
            assert phase in reply['refusal']['why']
        else:
            code = {'feasibility': 'empty-domain', 'completeness': 'event-bound-incomplete',
                    'definedness': 'operation-domain-unencoded', 'memory': 'memory-conflict',
                    'confluence': 'order-sensitive'}[phase]
            assert reply['refusal']['kind'] == f'rtl-source-{code}'
        assert exact._admitted_comparison is None
        assert exact._admitted_request is None
        assert attempts[-1][0] is exact._comparison
        # A failed induction/abstraction must reach the original exact gate.
        # Retained UNSAT premises may skip earlier gates on that fallback,
        # but no attempt may proceed beyond the injected failure.
        allowed = set(stages[:stages.index(phase)+1])
        assert set(calls) == allowed
        ends = [start for _, start in attempts[1:]] + [len(calls)]
        for (_, start), end in zip(attempts, ends, strict=True):
            observed = calls[start:end]
            assert observed and observed[-1] == phase
            assert set(observed) <= allowed


def test_source_decode_applies_hdl_initialization_after_physical_capture(tmp_path, monkeypatch):
    from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract

    context, prepared, _, _, _ = task_fixtures._worker(tmp_path,
        HISTORY_SOURCE.replace('reg [1:0] saved;', 'reg [1:0] saved=3;'),
        _simple_contract(reset_policy='clears'), 5)
    exact = analysis_job._SourceAnalysis(prepared, 5)
    value = exact.call('feasible', {'timeout_ms': 60_000}, started_ns=time.monotonic_ns())
    assert value['answer'] == 'feasible'
    from behave.sim.symbolic import exact_solve
    def repeated_solve(*_args, **_kwargs):
        raise AssertionError('the admitted full request already establishes feasibility')
    with monkeypatch.context() as patch:
        patch.setattr(exact_solve, 'z3_check_finite', repeated_solve)
        again = exact.call('feasible', {'timeout_ms': 60_000}, started_ns=time.monotonic_ns())
    assert again['witness'] == value['witness']
    assert again['telemetry']['provenance']['feasibility_member'] == 'admission-request'
    coordinate = next(key for key, name, _ in prepared.initial_layout.coordinates if name == 'saved')
    rows = value['witness']
    # The native constructor really contains zero before HDL assigns saved=3.
    # Decode must retain that receipt rather than pretending it observed 3.
    changed = [{**row, coordinate: 0} for row in rows]
    reply = analysis_job._run_bound_operation('decode', {'rows': changed}, exact, None)
    value = _value(reply, 'decode')
    assert next(row[2] for row in value['initialization']['registers']['fields'] if row[0] == 'saved') == 0
    stimulus = exact.request.codec.decode(changed)
    trace = context.source_trace(stimulus, startup_state=prepared.initial_layout.decode(changed), reuse=False)
    assert dict(trace.initial_state)['saved'] == 3
    witness = _value(analysis_job._run_bound_operation('audit-request',
        {'rows': changed, 'trace': trace.as_dict(), 'domain': exact.domain.digest()}, exact, None), 'audit-request')
    assert AuditedWitness.from_record(witness).inputs == tuple(changed)
