"""Public native-cover worker and its independently confirmed witnesses."""

import time

import pytest
import z3

from behave.sim import api
from behave.sim.analysis_budget import analysis_scope
from behave.sim.core.audit import AuditedWitness
from behave.sim.core.refusal import Refusal
from behave.sim.rtl import analysis_job

from . import test_rtl_source_worker_task as task_fixtures

task = task_fixtures.task
pytestmark = [pytest.mark.cosim, pytest.mark.solver]


@pytest.fixture(autouse=True)
def budget():
    with analysis_scope(120):
        yield


@pytest.fixture(scope='module')
def direct(task):
    _, context, _, _, _, _ = task
    admitted = api.admit(context.contract, api.realization_of_rtl_source(
        context.artifact.rtl_path.read_text(), 'direct source regression',
        top='M', contract=context.contract))
    return analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 6,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity})


def _value(reply, operation):
    value = analysis_job.decode_analysis_reply(reply, operation)
    assert not isinstance(value, Refusal), value.as_dict()
    return value


def _assert_original_roots(actual, original):
    """Keep the original base; extra preparation facts must follow from it."""
    assert len(actual) >= len(original)
    assert all(z3.eq(a, b) for a, b in zip(actual, original))
    extra = actual[len(original):]
    if extra:
        solver = z3.Solver(ctx=original[0].ctx)
        solver.set(timeout=30_000)
        solver.add(*original, z3.Not(z3.And(*extra)))
        assert solver.check() == z3.unsat


def test_terminal_receipts_reuse_fixed_syntax_but_recheck_native_evidence(tmp_path, monkeypatch):
    from behave.sim.core.failure import RunFailure
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl.observation_audit import NativeEvidence
    from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract

    source = HISTORY_SOURCE.replace('if(saved==1 && a==2) y<=3; else y<=a;',
        'unique case(a) 0: y<=0; 1: y<=1; endcase')
    contract = _simple_contract(reset_policy='clears')
    context, *_ = task_fixtures._worker(tmp_path, source, contract, 4)
    admitted = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract))
    worker = analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 4,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity})
    _value(worker.binding(), 'bind')
    exact, request = worker.exact, worker.exact.request
    solver = z3.Solver()
    solver.add(*request.constraints, request.lengths['in'] == 1,
        request.queues['in'][0][0] == 2,
        *(term == 0 for term in exact.source.initial_binding.values()))
    for i in range(4):
        solver.add(request.resets['c'][i] == (i == 0),
            request.actions[i].controls['ordy'], request.actions[i].offers['in'] == (i > 0))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    with pytest.raises(Refused) as stopped:
        context.source_trace(request.codec.decode(rows),
            startup_state=exact.source.initial_layout.decode(rows))
    image = thaw_record_value(stopped.value.refusal.detail['failure'])
    assert RunFailure.from_dict(image).kind == 'rtl-source-failure'
    # Exercise the complete fallback too: syntax reuse cannot rely on the
    # optional chosen-order proposal being available or matching this receipt.
    monkeypatch.setattr(NativeEvidence, 'propose_member', lambda *a, **k: None)
    calls, bind = [], exact.source.bind_request

    def tracked(*args, **kwargs):
        calls.append(kwargs)
        return bind(*args, **kwargs)

    monkeypatch.setattr(exact.source, 'bind_request', tracked)

    def audit(record):
        return worker.call('audit-request', {'rows': rows, 'trace': record,
            'domain': exact.domain.digest()})

    original = _value(audit(image), 'audit-request')
    image['reason']['why'] += ' (new diagnostic receipt)'
    assert _value(audit(image), 'audit-request') == original
    assert len(calls) == 1
    image['reason']['detail']['source_evidence']['stimulus'] = '0' * 64
    invalid = analysis_job.decode_analysis_reply(audit(image), 'audit-request')
    assert isinstance(invalid, Refusal), invalid
    assert len(calls) == 1


def test_binding_coordinates_and_initial_support_do_not_encode_a_transition(task, monkeypatch):
    from behave.sim.symbolic.step_template import StepTemplate
    from behave.sim.rtl.exact_job_instance import PreparedSourceExact

    _, context, *_ = task

    def premature(*_args, **_kwargs):
        pytest.fail('metadata binding must not construct an untargeted transition')

    monkeypatch.setattr(StepTemplate, '__init__', premature)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    exact = analysis_job._SourceAnalysis(source, 6)
    assert source.template._source is None
    assert set(source.initial.bits) == set(source.machine.program.fields)
    assert set(source.goal_names) == {goal for _, _, goal in source.machine.occurrences}
    assert exact.request is source.request_coordinates(6)


@pytest.mark.parametrize('operation', ('record-audit', 'audit-request', 'query-start'))
def test_witness_limit_is_a_typed_refusal_and_keeps_session_usable(direct, monkeypatch, operation):
    from behave.sim.core.audit import AuditedWitnessLimitError

    worker = analysis_job.PreparedAnalysis.__new__(analysis_job.PreparedAnalysis)
    worker.exact, worker.task, worker._query_cursor = direct.exact, direct.task, None
    original = worker.exact.call
    def limited(*args, **kwargs):
        raise AuditedWitnessLimitError(123)
    with monkeypatch.context() as patch:
        patch.setattr(worker.exact, 'call', limited)
        payload = {name: None for name in analysis_job._OPERATION_FIELDS[operation]}
        reply = worker.call(operation, payload)
        result = analysis_job.decode_analysis_reply(reply, operation)
        assert isinstance(result, Refusal)
        assert result.kind == 'analysis-audited-witness-evidence-too-large'
        assert result.detail['node_limit'] == 123
        assert worker._query_cursor is None
    assert worker.exact.call == original
    _value(worker.binding(), 'bind')


def test_monitor_prefix_query_keeps_native_witness_authority(task, direct, monkeypatch):
    from behave.sim.rtl.analysis_query import GrowingGoalQuery
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance

    _, context, *_ = task
    goal = next(name for name in direct.exact.source.complete if name.endswith('.scenario'))
    original, proposals = GrowingGoalQuery.propose, []

    def propose(owner, goals, **kwargs):
        result = original(owner, goals, **kwargs)
        if goal in goals:
            proposals.append(result)
        return result

    monkeypatch.setattr(GrowingGoalQuery, 'propose', propose)
    proxy = object.__new__(RTLIsolatedAnalysisInstance)
    proxy._domain, proxy._context, proxy._pending_native = direct.exact.domain, context, None
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call',
        lambda self, operation, **extra: _value(direct.call(operation, extra), operation))
    verdict = proxy.query([goal], 6, timeout_ms=30_000)
    assert proposals and goal in verdict.reachable
    assert not verdict.unreachable and not verdict.undecided
    witness = proxy.audit_request(verdict.reachable[goal], proxy._domain.digest())
    assert type(witness) is AuditedWitness and goal in witness.hits


def test_public_one_shot_and_session_share_native_cover(task, direct):
    from behave.sim.worker import run_job_strict

    _, context, *_ = task
    admitted = api.admit(context.contract, api.realization_of_rtl_source(
        context.artifact.rtl_path.read_text(), top='M', contract=context.contract))
    payload = {**context.source_payload(), 'horizon': 6,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity}
    session = _value(direct.binding(), 'bind')
    assert direct.exact.source.template.direct is not None
    fresh = _value(analysis_job.run_analysis_job({**payload, 'operation': 'bind'}), 'bind')
    assert fresh == session
    if task[0] == 'scalar':
        isolated = _value(run_job_strict('rtl_exact_analysis',
            {**payload, 'operation': 'bind'}, timeout_s=120), 'bind')
        assert isolated == session


def test_complete_query_without_prefix_hints_keeps_native_authority(task, direct, monkeypatch):
    from behave.sim.rtl import source_member
    from behave.sim.rtl.analysis_query import GrowingGoalQuery
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance

    _, context, *_ = task
    scenario = next(name for name in direct.exact.source.complete if name.endswith('.scenario'))
    source_goal = next(name for name, goal in context.frontend_catalogue.items()
                       if goal.family == 'location')
    goals = [scenario, source_goal, 'monitor.output-without-transaction']
    monkeypatch.setattr(source_member, 'source_query_prefixes', lambda *_a, **_k: iter(()))
    monkeypatch.setattr(GrowingGoalQuery, 'propose', lambda *_a, **_k: (None, None, []))
    proxy = object.__new__(RTLIsolatedAnalysisInstance)
    proxy._domain, proxy._context, proxy._pending_native = direct.exact.domain, context, None
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call',
        lambda self, operation, **extra: _value(direct.call(operation, extra), operation))
    verdict = proxy.query(goals, 6, timeout_ms=30_000)
    assert not verdict.undecided
    assert {scenario, source_goal} <= set(verdict.reachable)
    assert 'monitor.output-without-transaction' in verdict.unreachable
    for goal in (scenario, source_goal):
        witness = proxy.audit_request(verdict.reachable[goal], proxy._domain.digest())
        assert type(witness) is AuditedWitness and goal in witness.hits


def test_native_x_assignment_public_sat_has_a_real_source_hit(tmp_path, monkeypatch):
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance
    from behave.sim.rtl import source_member

    original_member = source_member.solve_fixed_source_member
    proposals = []
    def member(assertions, histories, environment, solve, **kwargs):
        result = original_member(assertions, histories, environment, solve, **kwargs)
        if kwargs.get('proposal') is not None:
            proposals.append(result)
            assert result.model is not None
            assert all(z3.is_true(result.model.eval(term, model_completion=False)) for term in assertions)
        return result
    monkeypatch.setattr(source_member, 'solve_fixed_source_member', member)

    source = "module M(input a,output logic y); always_comb if(a) y='x; else y=0; endmodule"
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(1), 'a')],
            api.settled('in'), None, kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(1), 'y')],
            api.settled('out'), None, kind='transaction', answers_per_transaction=1)], [])
    context, *_ = task_fixtures._worker(tmp_path, source, contract, 2)
    admitted = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract))
    worker = analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 2,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity})
    _value(worker.binding(), 'bind')
    branch = next(name for name, goal in context.frontend_catalogue.items()
                  if goal.family == 'control-choice' and goal.event == 'true')
    proxy = object.__new__(RTLIsolatedAnalysisInstance)
    proxy._domain, proxy._context, proxy._pending_native = worker.exact.domain, context, None
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call',
        lambda self, operation, **extra: _value(worker.call(operation, extra), operation))
    verdict = proxy.query([branch, 'monitor.input-completion'], 2, timeout_ms=30_000)
    assert set(verdict.reachable) == {branch} and not verdict.undecided
    assert set(verdict.unreachable) == {'monitor.input-completion'}
    rows = verdict.reachable[branch]
    witness = proxy.audit_request(rows, proxy._domain.digest())
    assert type(witness) is AuditedWitness and branch in witness.hits
    replay = proxy.replay(rows)
    assert not replay.broken and branch in replay.hits
    assert proposals, 'the public native confirmation must receive the query proposal'


@pytest.mark.parametrize('guard,guaranteed_hit', [('b==2 && a==2', True), ('b!=a', False)])
def test_coactive_native_cover_reaches_real_branch_and_excludes_protocol_fault(
        tmp_path, monkeypatch, guard, guaranteed_hit):
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance

    # The positive guard also names its driving pin, so it cannot be satisfied
    # solely by an arbitrary stale private wire at startup. Such a proposal
    # may legitimately fail native confirmation in the larger source cover.
    source = '''module M(input [1:0] a,output logic [1:0] y);
      wire [1:0] b; assign b=a;
      always_comb if(GUARD) y=3; else y=a;
    endmodule'''.replace('GUARD', guard)
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(2), 'a')],
            api.settled('in'), None, kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(2), 'y')],
            api.settled('out'), None, kind='transaction', answers_per_transaction=1)], [])
    context, *_ = task_fixtures._worker(tmp_path, source, contract, 2)
    admitted = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract))
    worker = analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 2,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity}, direct_source=True)
    _value(worker.binding(), 'bind')
    branch = next(name for name, goal in context.frontend_catalogue.items()
                  if goal.family == 'control-choice' and goal.event == 'true')
    proxy = object.__new__(RTLIsolatedAnalysisInstance)
    proxy._domain, proxy._context, proxy._pending_native = worker.exact.domain, context, None
    # A non-source query can use the existing state-only fixed-point compiler;
    # the branch query below must retain transient event orders separately.
    _, _, control = worker.exact.source.bind_request(2, goals=['monitor.input-completion'])
    # Check the encoded work, not which compiler owner happens to cache it.
    assert all(len(phase.actions) == 1 for slot in control.source.slots for phase in slot.phases)
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call',
        lambda self, operation, **extra: _value(worker.call(operation, extra), operation))
    verdict = proxy.query([branch, 'monitor.input-completion'], 2, timeout_ms=30000)
    assert set(verdict.unreachable) == {'monitor.input-completion'}
    assert set(verdict.reachable) | set(verdict.undecided) == {branch}
    if guaranteed_hit:
        assert set(verdict.reachable) == {branch} and not verdict.undecided
    elif branch in verdict.undecided:
        # The cover may choose stale private storage at startup. Whether a
        # particular proposal occurs natively is NOT fixed by the guard text
        # or Z3's model choice. A miss stays unknown; any reported hit must
        # survive the independent native replay below, not just the SMT query.
        assert verdict.undecided[branch] == 'rtl-source-proposal-not-native-reached'
    for call in verdict.queries:
        if call.answer is SolverAnswer.SAT:
            witness = proxy.audit_request(call.witness, proxy._domain.digest())
            assert type(witness) is AuditedWitness
            assert branch in witness.hits
            replay = proxy.replay(call.witness)
            assert not replay.broken
            assert branch in replay.hits


@pytest.mark.parametrize('changed_slot', [0, 1])
@pytest.mark.parametrize('proposal_available', [False, True])
def test_member_reuses_only_equal_proof_after_a_new_native_trace(
        tmp_path, monkeypatch, changed_slot, proposal_available):
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl import source_member

    source = '''module M(input [1:0] a,output logic [1:0] y);
      always_comb if(a==2) y=3; else y=a;
    endmodule'''
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(2), 'a')],
            api.settled('in'), None, kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(2), 'y')],
            api.settled('out'), None, kind='transaction', answers_per_transaction=1)], [])
    context, *_ = task_fixtures._worker(tmp_path, source, contract, 2)
    admitted = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract))
    worker = analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 2,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity}, direct_source=True)
    _value(worker.binding(), 'bind')
    branch = next(name for name, goal in context.frontend_catalogue.items()
                  if goal.family == 'control-choice' and goal.event == 'true')
    proxy = object.__new__(RTLIsolatedAnalysisInstance)
    proxy._domain, proxy._context, proxy._pending_native = worker.exact.domain, context, None
    monkeypatch.setattr(RTLIsolatedAnalysisInstance, '_raw_call',
        lambda self, operation, **extra: _value(worker.call(operation, extra), operation))
    rows = proxy.query([branch], 2, timeout_ms=30_000).reachable[branch]
    _, trace = proxy._source_payload(rows)
    constructions, native_runs = [], []
    bind = worker.exact.source.bind_request
    native = type(context).source_trace

    def tracked_bind(*args, **kwargs):
        if kwargs.get('native_member'):
            constructions.append(None)
        return bind(*args, **kwargs)

    def tracked_native(*args, **kwargs):
        native_runs.append(None)
        return native(*args, **kwargs)

    monkeypatch.setattr(worker.exact.source, 'bind_request', tracked_bind)
    monkeypatch.setattr(type(context), 'source_trace', tracked_native)
    # Exercise the optional member-order route on a small deterministic
    # native design. Only the route-selection guard is refused; source
    # construction, all original constraints and native checks remain real.
    compiler = worker.exact.source.template.direct
    original_plan = compiler.plan
    def select_member(*args, **kwargs):
        if kwargs.get('all_active'):
            raise Refused(Refusal('rtl-direct-coactive-dependency', 'controlled proposal route'))
        return original_plan(*args, **kwargs)
    monkeypatch.setattr(compiler, 'plan', select_member)
    if not proposal_available:
        monkeypatch.setattr(source_member, 'source_member_hint', lambda *args, **kwargs: None)
    worker.exact._native.member_proof = None
    worker.exact._native.member_miss = None
    worker.exact._native.witness = None  # Exercise construction, not the identical-record cache.
    payload = {'rows': list(rows), 'trace': trace.as_dict(), 'domain': worker.exact.domain.digest()}
    first = _value(worker.call('audit-request', payload), 'audit-request')
    assert constructions
    assert (worker.exact._native.member_proof is not None) == proposal_available
    count = len(constructions)
    for _ in range(2):
        replay = proxy.replay(rows)
        assert not replay.broken and branch in replay.hits
    assert len(native_runs) == 2  # Independent execution was NOT cached.
    assert len(constructions) == count

    changed = trace.as_dict()
    changed['records'][changed_slot]['outputs'] = dict(changed['records'][changed_slot]['outputs'])
    changed['records'][changed_slot]['outputs']['y'] ^= 1
    answer = worker.call('audit-request', {**payload, 'trace': changed})
    assert answer['status'] == 'inconsistent'
    assert answer['inconsistency']['kind'] == 'rtl-source-native-witness-inconsistent'
    assert len(constructions) > count and worker.exact._native.member_proof is None
    assert _value(worker.call('audit-request', payload), 'audit-request') == first


@pytest.mark.parametrize('operation', ['query-next', 'query-close'])
def test_next_query_operation_releases_the_previous_native_proof(operation):
    worker = analysis_job.PreparedAnalysis.__new__(analysis_job.PreparedAnalysis)
    worker.exact = analysis_job._SourceAnalysis.__new__(analysis_job._SourceAnalysis)
    worker.exact._queries = analysis_job.GoalQueryCache()
    worker.exact._native.member_proof = object()
    worker.exact._native.member_miss = object()

    def cursor():
        assert worker.exact._native.member_proof is None
        assert worker.exact._native.member_miss is None
        return
        yield

    worker._query_cursor = cursor()
    assert _value(worker.call(operation, {}), operation) is None
    assert worker._query_cursor is None and worker.exact._native.member_proof is None
    assert worker.exact._native.member_miss is None


def test_binding_uses_one_history_without_preflight_solver(task, direct, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('direct binding must not construct or prove a history pair')
    monkeypatch.setattr(direct.exact.source, 'compare_request', forbidden)
    monkeypatch.setattr(direct.exact, '_domain_feasibility', forbidden)
    binding = _value(direct.binding(), 'bind')
    assert binding['domain']['frontend'] == 'rtl-source-task-native-cover/2'
    assert binding['domain']['schema']['source_query_semantics'] == 'native-cover/1'
    assert binding['domain']['schema']['source_value_semantics'] == 'native-variable-literals/1'
    assert direct.exact._comparison is None
    assert direct.exact.source._requests == {}  # No eager full observation history.
    assert direct.exact.relation.query_projection
    assert direct.exact.catalogue_manifest() == task[2].complete
    assert all(len(phase.actions) == 1 for slot in direct.exact.encoding.source.slots
               for phase in slot.phases)


def test_direct_domain_and_binding_do_not_expand_an_unused_history(task, monkeypatch):
    from behave.sim.rtl.exact_job_instance import PreparedSourceExact

    _, context, *_ = task
    source = PreparedSourceExact(context.source_payload(), direct=True)
    original = source.bind_request_steps
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail('domain metadata and structural binding must not unroll a history')

    monkeypatch.setattr(source, 'bind_request_steps', forbidden)
    exact = analysis_job._SourceAnalysis(source, 6)
    assert exact.request is source.request_coordinates(6)
    assert not source._requests and not source._query_requests
    binding = exact.call('bind', {}, started_ns=time.monotonic_ns())
    assert binding['domain_digest'] == exact.domain.digest()

    def tracked(horizon, *, goals=None, **options):
        calls.append(None if goals is None else frozenset(goals))
        return (yield from original(horizon, goals=goals, **options))

    monkeypatch.setattr(source, 'bind_request_steps', tracked)
    goal = 'monitor.sink-stability'
    value = exact.call('query', {'goals': [goal], 'timeout_ms': 30_000},
                       started_ns=time.monotonic_ns())
    assert value['queries']
    assert calls in ([frozenset()], [frozenset({goal})])
    assert '_base_history' not in exact.__dict__
    _, request, encoding = source.bind_request(6, goals=[goal])
    assert request is exact.request
    assert exact.domain.schema['excluded_environment_faults'] == sorted(encoding.terminal_assumptions)


def test_control_goal_projection_does_not_retain_output_payload(task):
    _, context, *_ = task
    source = analysis_job.PreparedSourceExact(context.source_payload(), direct=True)
    relation, _, _ = source.bind_request(6, goals=['monitor.output-without-transaction'])
    retained = {source.machine.program.fields[name].port
                for name in relation.source.relation.source.state_names
                if name in source.machine.program.fields}
    assert 'y' not in retained
    assert {'ir', 'ov'} <= retained


def test_narrower_query_batch_drops_prior_payload_but_keeps_exact_escape(task, monkeypatch):
    from behave.sim.rtl.source_task import SourceTaskRelation

    _, context, *_ = task
    source = analysis_job.PreparedSourceExact(context.source_payload(), direct=True)
    goal = 'monitor.output-without-transaction'
    wide, request, original = source.bind_request(6, goals=[goal, 'monitor.sink-stability'])

    def ports(relation):
        return {source.machine.program.fields[name].port
                for name in relation.source.relation.source.state_names
                if name in source.machine.program.fields}

    assert 'y' in ports(wide)
    encode = SourceTaskRelation.encode_request

    def replacement(relation, *args, **kwargs):
        assert 6 not in source._query_requests, 'retire the old cache before the new build'
        return encode(relation, *args, **kwargs)

    monkeypatch.setattr(SourceTaskRelation, 'encode_request', replacement)
    narrow, shared, projected = source.bind_request(6, goals=[goal])
    assert shared is request  # Public inputs, schedule and bound are unchanged.
    # Avoid rendering the entire symbolic history on the expected old-code
    # failure; object identity is the property under test.
    replaced_history = projected is not original
    assert replaced_history
    assert 'y' not in ports(narrow)
    assert source.bind_request(6, goals=[goal])[2] is projected
    solver = z3.Solver(ctx=projected.alive.ctx)
    solver.set(timeout=30_000)
    solver.add(*original.constraints, *projected.constraints)
    assert solver.check() == z3.sat
    changed = [original.hits[goal] != projected.hits[goal],
               original.alive != projected.alive,
               original.unfinished != projected.unfinished]
    for attr in ('domain_faults', 'environment_assumptions'):
        left, right = getattr(original, attr), getattr(projected, attr)
        assert left.keys() == right.keys()
        changed.extend(left[name] != right[name] for name in left)
    solver.add(z3.Or(*changed))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('already_bound', (False, True))
@pytest.mark.parametrize('explicit_lemma', (False, True))
def test_decode_reuses_fresh_domain_proof_but_native_audit_keeps_original_source(
        tmp_path, monkeypatch, already_bound, explicit_lemma):
    from behave.sim.core.stimulus import Stimulus
    from behave.sim.rtl import source_domains
    from behave.sim.rtl.startup_state import RTLStartupState
    from behave.sim.symbolic.exact_solve import z3_check_finite

    original_proof = source_domains.prove_transition_domains
    proved_domains = set()

    def proof(source, *, check, solve=None):
        if explicit_lemma and solve is None:
            def solve(_phase, roots, **options):
                return z3_check_finite(roots, 30_000, _owned_context=roots[0].ctx,
                                      _preparation=options['preparation'])
        proved = original_proof(source, solve=solve, check=check)
        proved_domains.update(proved)
        return proved

    monkeypatch.setattr(source_domains, 'prove_transition_domains', proof)

    # A dead guarded operation may be excluded structurally, by an explicit
    # lemma, or after live goals finish. Reuse depends on the actual proved
    # exclusions, not on which accelerator happened to establish them.
    source_text = '''module M(input [3:0] a,b,c,output logic [3:0] y);
      always_comb begin
        if(a<b && b<c && c<=a) y=a/b;
        else y=a;
      end
    endmodule'''
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field(n, api.FiniteType(4), n) for n in ('a','b','c')],
            api.settled('in'), None, kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(4), 'y')],
            api.settled('out'), None, kind='transaction', answers_per_transaction=1)], [])
    context, *_ = task_fixtures._worker(tmp_path, source_text, contract, 2)
    admitted = api.admit(contract, api.realization_of_rtl_source(source_text, top='M', contract=contract))
    worker = analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 2,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity})
    owner = worker.exact.source
    assert owner.machine.domain_checks
    def no_reset_preproof(*args, **kwargs):
        pytest.fail('optional inductive range proofs must not precede bounded queries')

    # An optional reset-induction proof is unnecessary for this combinational
    # source. Complete bounded queries and deferred transition proofs remain.
    monkeypatch.setattr(source_domains, 'prove_reset_domains', no_reset_preproof)
    prior = worker.exact._base_history if already_bound else None
    result = _value(worker.call('query', {'goals': list(owner.goal_names), 'timeout_ms': 30_000}), 'query')
    rows = next(q['witness'] for q in result['queries'] if q.get('witness') is not None)
    exact = worker.exact
    view = exact._membership_source
    if already_bound:
        assert view is owner and exact._base_history is prior
        bound = prior
    else:
        assert (view is not owner) is bool(proved_domains)
        assert {name for name, _ in view.machine.domain_checks} == (
            {name for name, _ in owner.machine.domain_checks} - proved_domains)
        assert '_base_history' not in exact.__dict__
        cached_goals, bound = view._query_requests[2]
    assert owner.machine.domain_checks  # Never mutate the native owner.
    original = owner.bind_request
    native_calls = []
    def native_only(horizon, **options):
        if options.get('native_history') is None:
            assert view is owner and options == {'goals': (), 'fixed_rows': rows}
            fixed = original(horizon, **options)
            assert fixed[1] is bound[1]  # The public request owner is shared.
            assert fixed is not bound  # A member is not an unrestricted query.
            return fixed
        assert options.get('native_history') is not None
        native_calls.append(options)
        return original(horizon, **options)
    monkeypatch.setattr(owner, 'bind_request', native_only)
    decoded = _value(worker.call('decode', {'rows': rows}), 'decode')
    if already_bound:
        assert exact._base_history is bound
    else:
        # Fixed membership neither builds an unrestricted second history nor
        # replaces the live query's reusable observation plan.
        assert '_base_history' not in exact.__dict__
        assert view._query_requests[2] == (cached_goals, bound)
    assert not native_calls  # Native observation construction belongs to audit.
    trace = context.source_trace(Stimulus.from_dict(decoded['stimulus']),
        startup_state=RTLStartupState.from_dict(decoded['initialization']))
    witness = _value(worker.call('audit-request', {'rows': rows, 'trace': trace.as_dict(),
        'domain': exact.domain.digest()}), 'audit-request')
    assert native_calls and witness
    assert exact.source is owner and owner.machine.domain_checks


def test_incremental_cursor_retains_each_batch_history_between_native_checks(task, monkeypatch):
    from behave.sim.core.query import collect_verdicts
    from behave.sim.core.instance import Verdict

    _, context, *_ = task
    admitted = api.admit(context.contract, api.realization_of_rtl_source(
        context.artifact.rtl_path.read_text(), 'incremental source regression',
        top='M', contract=context.contract))
    worker = analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 6,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity}, direct_source=True)
    _value(worker.binding(), 'bind')
    original = worker.exact.source.bind_request_steps
    constructions = []
    def tracked(horizon, **options):
        if options.get('native_history') is None and options.get('fixed_rows') is None:
            constructions.append(options.get('goals'))
        return original(horizon, **options)
    monkeypatch.setattr(worker.exact.source, 'bind_request_steps', tracked)
    goals = ['monitor.input-completion', 'monitor.output-without-transaction', 'monitor.sink-stability']
    image = _value(worker.call('query-start', {'goals': goals, 'timeout_ms': 30_000}), 'query-start')
    parts = []
    while image is not None:
        proposal = set(image) == {'native_proposal'}
        if proposal:
            requests = [image['native_proposal']]
        else:
            part = Verdict.from_record(image)
            parts.append(part)
            assert part.queries
            requests = [call.witness for call in part.queries if call.witness is not None]
        # Native work on this session must not reconstruct the query history.
        before_native = len(constructions)
        for rows in requests:
            decoded = _value(worker.call('prepare-replay', {'rows': rows}), 'prepare-replay')
            from behave.sim.core.stimulus import Stimulus
            from behave.sim.rtl.startup_state import RTLStartupState
            trace = context.source_trace(Stimulus.from_dict(decoded['stimulus']),
                startup_state=RTLStartupState.from_dict(decoded['initialization']))
            _value(worker.call('audit-request', {'rows': rows, 'trace': trace.as_dict(),
                'domain': worker.exact.domain.digest()}), 'audit-request')
        assert len(constructions) == before_native
        image = _value(worker.call('query-next', {}), 'query-next')
    result = collect_verdicts(parts)
    assert set(result.reachable) | set(result.unreachable) | set(result.undecided) == set(goals)
    histories = [tuple(item) for item in constructions if item is not None]
    # Ordinary completion and protocol properties now have distinct batches.
    # Native witness checks still must not rebuild either batch's history.
    assert len(histories) == 2
    assert histories[0] == ('monitor.input-completion',)
    assert set(histories[1]) <= set(goals[1:])
    assert worker._query_cursor is None
    with pytest.raises(ValueError, match='start before'):
        worker.call('query-next', {})


def test_direct_request_agrees_with_native_trace(task, direct):
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
    record = _value(direct.call('audit-request', {'rows': rows, 'trace': trace.as_dict(),
        'domain': direct.exact.domain.digest()}), 'audit-request')
    witness = AuditedWitness.from_record(record)
    assert witness.inputs == tuple(rows)
    if task[0] == 'scalar':
        from behave.sim.worker import run_job_strict

        admitted = api.admit(context.contract, api.realization_of_rtl_source(
            context.artifact.rtl_path.read_text(), top='M', contract=context.contract))
        isolated = _value(run_job_strict('rtl_exact_analysis', {
            **context.source_payload(), 'horizon': 6,
            'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity,
            'operation': 'audit-request', 'rows': rows, 'trace': trace.as_dict(),
            'domain': direct.exact.domain.digest()}, timeout_s=120), 'audit-request')
        assert AuditedWitness.from_record(isolated) == witness
    specialized = direct.exact._native.encoding
    assert all(id(specialized) != id(bound[2]) for bound in direct.exact.source._requests.values())
    repeated = _value(direct.call('audit-request', {'rows': rows, 'trace': trace.as_dict(),
        'domain': direct.exact.domain.digest()}), 'audit-request')
    assert repeated == record
    assert id(direct.exact._native.encoding) == id(specialized)
    assert direct.exact._native.relation.complete_state
    # Same request and cached encoding, but a different observed value must
    # neither reuse the old proof nor become a premise of literal propagation.
    changed = trace.as_dict()
    changed['records'][2]['outputs'] = dict(changed['records'][2]['outputs'])
    changed['records'][2]['outputs']['y'] ^= 1
    reply = direct.call('audit-request', {'rows': rows, 'trace': changed,
        'domain': direct.exact.domain.digest()})
    assert reply['status'] == 'inconsistent'
    assert reply['inconsistency']['kind'] == 'rtl-source-native-witness-inconsistent'
    # The rejected observation must not contaminate a later valid request.
    _value(direct.call('audit-request', {'rows': rows, 'trace': trace.as_dict(),
        'domain': direct.exact.domain.digest()}), 'audit-request')


@pytest.mark.parametrize('fault,kind', [
    ('test-domain-fault', 'rtl-source-operation-domain-unencoded'),
    ('monitor.reset-policy-unresolved[clk][out]', 'rtl-monitor-reset-policy-undeclared'),
])
@pytest.mark.parametrize('startup', [False, True])
def test_named_domain_fault_is_not_lost_as_generic_unknown(direct, monkeypatch, fault, kind, startup):
    from dataclasses import replace

    owner = type(direct.exact.source)
    bind = owner.bind_request_steps
    injected = []

    def with_fault(source, horizon, **options):
        relation, request, encoded = yield from bind(source, horizon, **options)
        injected.append(encoded)
        term = z3.BoolVal(True, ctx=encoded.alive.ctx)
        if startup:
            first, *rest = encoded.startup.phases
            first = replace(first, domain_faults={**first.domain_faults, fault: term})
            encoded = replace(encoded, startup=replace(encoded.startup, phases=(first, *rest)))
        else:
            encoded = replace(encoded, monitor_faults=({fault: term},))
        return relation, request, encoded

    monkeypatch.setattr(owner, 'bind_request_steps', with_fault)
    answer = direct.call('query', {'goals': ['monitor.input-completion'],
                                   'timeout_ms': 30_000})
    refusal = analysis_job.decode_analysis_reply(answer, 'query')
    assert injected
    assert isinstance(refusal, Refusal)
    assert refusal.kind == kind
    assert refusal.as_dict()['detail']['faults'] == [fault]
    assert refusal.as_dict()['detail']['startup_faults'] == ([fault] if startup else [])
    assert refusal.as_dict()['detail']['operations'] == {}


@pytest.mark.parametrize('streaming', [False, True])
def test_source_domain_refusal_retains_literal_and_span(tmp_path, monkeypatch, streaming):
    from behave.sim.symbolic.diagnostics import diagnostic_scope

    # Bare X-to-variable assignments now have a native storage encoding;
    # unknown arithmetic still requires the original operation-domain fault.
    source = "module M(input a,output logic y); always_comb if(a) y=~'x; else y=0; endmodule"
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(1), 'a')],
            api.settled('in'), None, kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(1), 'y')],
            api.settled('out'), None, kind='transaction', answers_per_transaction=1)], [])
    context, *_ = task_fixtures._worker(tmp_path, source, contract, 2)
    admitted = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract))
    worker = analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 2,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity}, direct_source=True)
    _value(worker.binding(), 'bind')
    # Both provider paths must preserve the actual fault and its source span.
    # Undefined arithmetic need not have a successful whole-history hint.
    if not streaming:
        monkeypatch.setattr(worker.exact._queries, 'start', lambda *a, **k: None)
    stages = []
    branch = next(name for name, goal in context.frontend_catalogue.items()
                  if goal.family == 'control-choice' and goal.event == 'true')
    with diagnostic_scope(stages.append):
        reply = worker.call('query', {'goals': [branch], 'timeout_ms': 30_000})
    refusal = analysis_job.decode_analysis_reply(reply, 'query')
    assert isinstance(refusal, Refusal)
    assert refusal.kind == 'rtl-source-operation-domain-unencoded'
    detail = refusal.as_dict()['detail']
    assert detail['faults'] and set(detail['faults']) == set(detail['operations'])
    for cause in detail['operations'].values():
        assert cause['kind'] == 'rtl-source-ir-four-state-domain-unproved'
        begin, end = cause['detail']['span']
        assert source[begin:end] == "'x"
        assert 'x' in cause['detail']['value'].lower()
    # Prefix and full queries deliberately share preparation. Observe their
    # actual phases, not the identity of that shared solver/cache object.
    calls = [row['phase'] for row in stages if row['event'] == 'started'
             and row['stage'][-1] == 'proof' and row.get('phase') in {'query', 'query-prefix'}]
    assert calls  # The refusal must come from a real source-domain query.


def test_unfinished_cover_remains_unknown_not_unreachable(direct, monkeypatch):
    from dataclasses import replace

    owner = type(direct.exact.source)
    bind = owner.bind_request_steps
    injected = []

    def unfinished(source, horizon, **options):
        relation, request, encoded = yield from bind(source, horizon, **options)
        injected.append(encoded)
        phase = replace(encoded.startup.phases[0],
            entered=z3.BoolVal(True, ctx=encoded.alive.ctx),
            alive=z3.BoolVal(True, ctx=encoded.alive.ctx),
            quiescent=z3.BoolVal(False, ctx=encoded.alive.ctx))
        startup = replace(encoded.startup, phases=(phase, *encoded.startup.phases[1:]))
        return relation, request, replace(encoded, startup=startup)

    monkeypatch.setattr(owner, 'bind_request_steps', unfinished)
    answer = _value(direct.call('query', {'goals': ['monitor.input-completion'],
                                        'timeout_ms': 30_000}), 'query')
    assert injected
    assert not answer['unreachable']
    assert answer['undecided'] == {'monitor.input-completion': 'rtl-direct-unencoded-continuation'}


@pytest.mark.parametrize('escape', [False, True])
@pytest.mark.parametrize('interrupted', [False, True])
def test_constant_goal_is_not_lost_behind_an_unknown_sibling(direct, monkeypatch, escape, interrupted):
    from dataclasses import replace
    from behave.sim.symbolic import exact_solve
    from behave.sim.core.query import SolverAnswer
    from behave.sim.core.refusal import Refused

    bind = direct.exact.source.bind_request
    never, hard = 'observation:zero', 'observation:pair'

    def image(horizon, *, goals=None):
        relation, request, encoded = bind(horizon, goals=goals)
        # Closed synthetic query images exercise routing independently of
        # whether this test artifact happens to contain a literal-false goal.
        encoded = replace(encoded, transaction_hits=tuple(
            {**row, never: z3.BoolVal(False, ctx=encoded.alive.ctx)}
            for row in encoded.transaction_hits), monitor_faults=(
                {'test-escape': z3.BoolVal(escape, ctx=encoded.alive.ctx)},))
        return relation, request, encoded

    calls = []
    def unknown(assertions, *args, **kwargs):
        calls.append(assertions)
        if interrupted:
            raise Refused(Refusal('test-budget-ended', 'hard sibling exhausted the enclosing allowance'))
        return exact_solve.ExactCheckResult(SolverAnswer.UNKNOWN, reason='test unresolved sibling')

    monkeypatch.setattr(direct.exact.source, 'bind_request', image)
    # This fixture injects a completed history. The streaming provider has its
    # own construction entrypoint and must not bypass that injected image.
    monkeypatch.setattr(direct.exact._queries, 'start', lambda *a, **k: None)
    monkeypatch.setattr(exact_solve, 'z3_check_finite', unknown)
    if interrupted:
        parts = direct.exact.call('query-start', {'goals': [never, hard], 'timeout_ms': 30_000},
                                  started_ns=time.monotonic_ns())
        try:
            if not escape:
                first = next(parts)
                assert set(first.unreachable) == {never}
                assert not calls  # Emitted before entering the hard solver.
            with pytest.raises(Refused, match='test-budget-ended'):
                next(parts)
        finally:
            parts.close()
        return
    value = _value(direct.call('query', {'goals': [never, hard], 'timeout_ms': 30_000}), 'query')
    assert hard in value['undecided']
    assert (never in value['unreachable']) is (not escape)
    singleton = next(item for item in value['queries'] if item['goals'] == [never])
    assert singleton['solver_calls'] == int(escape)
    assert len(calls) == (3 if escape else 1)


def test_direct_member_completes_without_whole_history_solver(direct):
    from behave.sim.rtl.source_member import source_member_hint

    exact = direct.exact
    request, encoded = exact.request, exact.encoding
    solver = z3.Solver(ctx=encoded.alive.ctx)
    solver.add(*request.constraints, request.lengths['in'] == 1,
               request.queues['in'][0][0] == 1)
    for i, action in enumerate(request.actions):
        reset = 'c' in request.slots[i].resets if request.atomic else i == 0
        solver.add(request.resets['c'][i] == reset,
                   action.controls['ordy'], action.offers['in'] == (i == 1))
    assert solver.check() == z3.sat
    # This solves only the environment. Completing the compiler-owned source
    # and monitor definitions must satisfy every original product assertion.
    member = source_member_hint(solver.model(), (encoded,), exact.constraints, check=lambda: None)
    assert member is not None
    assert all(z3.is_true(member.eval(term, model_completion=False)) for term in exact.constraints)


@pytest.mark.parametrize('successful_hint', [True, False])
def test_feasibility_reuses_forward_completion_with_full_solver_fallback(task, monkeypatch, successful_hint):
    from behave.sim.rtl import monitor_template, source_member

    _, context, *_ = task
    source = analysis_job.PreparedSourceExact(context.source_payload(), direct=True)
    exact = analysis_job._SourceAnalysis(source, 6)
    # This is a fresh instance: neither a recorded witness nor an earlier
    # admission is allowed to conceal the feasibility path under test.
    assert exact._admitted_request is None
    ordinary = monitor_template.solve_monitor_member
    calls = []

    def fallback(*args, **kwargs):
        calls.append(kwargs['phase'])
        if successful_hint:
            pytest.fail('a fully checked forward member needs no whole-history SAT')
        return ordinary(*args, **kwargs)

    monkeypatch.setattr(monitor_template, 'solve_monitor_member', fallback)
    if not successful_hint:
        monkeypatch.setattr(source_member, 'source_member_hint', lambda *args, **kwargs: None)
    result = exact.call('feasible', {'timeout_ms': 30_000}, started_ns=time.monotonic_ns())
    assert result['answer'] == 'feasible'
    assert len(result['witness']) == 6
    assert calls == ([] if successful_hint else ['feasibility'])
    # Completing an existential witness must not publish universal admission.
    assert exact._admitted_request is None


@pytest.mark.parametrize('successful_hint', [True, False])
def test_decode_uses_complete_fixed_request_member_before_full_solver(direct, monkeypatch, successful_hint):
    from behave.sim.rtl import monitor_template, source_member

    goal = direct.exact.source.relation.goal_names[0]
    result = _value(direct.call('query', {'goals': [goal], 'timeout_ms': 30_000}), 'query')
    rows = next(query['witness'] for query in result['queries'] if query['witness'] is not None)
    direct.exact._request_members.clear()
    retained = direct.exact._pending_query_member
    assert retained is not None
    bind = direct.exact._membership_source.bind_request
    constructions = []
    def fixed_membership(horizon, **kwargs):
        assert horizon == direct.exact.domain.bound
        assert kwargs == {'goals': (), 'fixed_rows': rows}
        bound = bind(horizon, **kwargs)
        assert bound[1] is direct.exact.request
        constructions.append(bound[2])
        return bound
    monkeypatch.setattr(direct.exact._membership_source, 'bind_request', fixed_membership)
    ordinary = monitor_template.solve_monitor_member
    complete_member = source_member.solve_fixed_source_member
    proposals = []
    def member(*args, **kwargs):
        proposals.append(kwargs.get('proposal'))
        answer = complete_member(*args, **kwargs)
        if answer.model is not None:
            assert all(z3.is_true(answer.model.eval(term, model_completion=False)) for term in args[0])
        return answer
    monkeypatch.setattr(source_member, 'solve_fixed_source_member', member)
    calls = []
    def fallback(*args, **kwargs):
        calls.append(kwargs['phase'])
        return ordinary(*args, **kwargs)
    monkeypatch.setattr(monitor_template, 'solve_monitor_member', fallback)
    if not successful_hint:
        monkeypatch.setattr(source_member, 'source_member_hint', lambda *args, **kwargs: None)
    decoded = _value(direct.call('decode', {'rows': rows}), 'decode')
    assert decoded['stimulus']
    assert calls == ([] if successful_hint else ['fixed-request'])
    assert len(proposals) == 1 and proposals[0] is retained[3]
    assert len(constructions) == 1
    assert direct.exact._pending_query_member is retained


@pytest.mark.parametrize('corrupt', (False, True))
def test_replay_preparation_has_no_proof_and_complete_native_audit_remains(
        task, direct, monkeypatch, corrupt):
    from copy import deepcopy
    from behave.sim.core.stimulus import Stimulus

    _, context, *_ = task
    exact = direct.exact
    goal = exact.source.relation.goal_names[0]
    result = _value(direct.call('query', {'goals': [goal], 'timeout_ms': 30_000}), 'query')
    rows = next(call['witness'] for call in result['queries'] if call.get('witness') is not None)
    exact._request_members.clear()
    exact._native.clear()
    exact._pending_query_member = None

    def premature(*_args, **_kwargs):
        pytest.fail('replay preparation must not prove membership before the native audit')

    monkeypatch.setattr(exact, '_request_failure', premature)
    decoded = _value(direct.call('prepare-replay', {'rows': rows}), 'prepare-replay')
    assert set(decoded) == {'stimulus', 'initialization'}
    assert not exact._request_members and exact._native.witness is None
    assert direct.call('prepare-replay', {'rows': rows[:-1]})['refusal']['kind'] == (
        'rtl-analysis-request-horizon-mismatch')
    trace = context.source_trace(Stimulus.from_dict(decoded['stimulus']),
        startup_state=exact.source.initial_layout.decode(rows), reuse=False)
    image = deepcopy(trace.as_dict())
    if corrupt:
        # Even a valid input image grants no authority to a changed output.
        image['records'][2]['outputs'] = dict(image['records'][2]['outputs'])
        image['records'][2]['outputs']['y'] ^= 1
    reply = direct.call('audit-request', {'rows': rows, 'trace': image,
                                         'domain': exact.domain.digest()})
    if corrupt:
        assert reply['status'] == 'inconsistent'
        assert not exact._request_members and exact._native.witness is None
    else:
        witness = AuditedWitness.from_record(_value(reply, 'audit-request'))
        assert witness.inputs == tuple(rows)
        assert exact._request_members and exact._native.witness is not None


@pytest.mark.parametrize('gated_payload', (False, True))
def test_completion_hint_requires_every_original_root_and_keeps_native_agreement(
        tmp_path, monkeypatch, gated_payload):
    from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract
    from behave.sim.symbolic import exact_solve

    # No input buffering: two final releases and a one-cycle receptive tail
    # leave one input pending. This tests the bounded monitor, not correctness.
    source = HISTORY_SOURCE.replace('!rst && (!ov || ordy)',
        '!rst && (a==0 || ordy)' if gated_payload else '!rst && ordy')
    context, *_ = task_fixtures._worker(tmp_path, source,
        _simple_contract(reset_policy='clears'), 6)
    admitted = api.admit(context.contract, api.realization_of_rtl_source(
        source, 'completion hint regression', top='M', contract=context.contract))
    worker = analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 6,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity}, direct_source=True)
    exact, goal = worker.exact, 'monitor.input-completion'
    _, request, encoded = exact.source.bind_request(6, goals=[goal])
    escape = z3.Or(encoded.unfinished, *encoded.domain_faults.values(), *encoded.source.memory.conflicts)
    roots = (*encoded.constraints, *(z3.Not(term) for term in encoded.environment_assumptions.values()),
             encoded.hits[goal], z3.Not(escape))
    model = exact._completion_hint(encoded, roots, lambda: None)
    assert model is not None
    if gated_payload:
        # The zero command never creates backpressure. A valid alternate
        # payload must reach the monitor through the unchanged source model.
        atom = next(iter(request.queues.values()))[0][0]
        assert model.eval(atom, model_completion=False).as_long() == 3
    assert all(z3.is_true(model.eval(term, model_completion=False)) for term in roots)
    assert exact._completion_hint(encoded, (*roots, z3.BoolVal(False)), lambda: None) is None

    def forbidden(*args, **kwargs):
        pytest.fail('a checked complete SAT member does not need whole-history SMT')

    with monkeypatch.context() as patch:
        patch.setattr(exact_solve, 'z3_check_finite', forbidden)
        value = _value(worker.call('query', {'goals': [goal], 'timeout_ms': 30_000}), 'query')
    assert set(value['reachable']) == {goal}
    assert value['queries'][0]['solver_calls'] == 0
    rows = value['reachable'][goal]
    request.fix(rows)
    trace = context.source_trace(request.codec.decode(rows), startup_state=exact.source.initial_layout.decode(rows))
    record = _value(worker.call('audit-request', {'rows': rows, 'trace': trace.as_dict(),
        'domain': exact.domain.digest()}), 'audit-request')
    assert goal in AuditedWitness.from_record(record).hits


def test_failed_completion_hint_preserves_unrestricted_solver_query(direct, monkeypatch):
    from behave.sim.symbolic import exact_solve
    from behave.sim.core.query import SolverAnswer

    exact, goal = direct.exact, 'monitor.input-completion'
    _, _, encoded = exact.source.bind_request(6, goals=[goal])
    escape = z3.Or(encoded.unfinished, *encoded.domain_faults.values(), *encoded.source.memory.conflicts)
    expected = (*encoded.constraints,
        *(z3.Not(term) for term in encoded.environment_assumptions.values()),
        z3.Or(z3.Or(encoded.hits[goal]), escape))
    calls = []

    def unresolved(assertions, *args, **kwargs):
        calls.append(tuple(assertions))
        return exact_solve.ExactCheckResult(SolverAnswer.UNKNOWN, reason='test unresolved goal')

    monkeypatch.setattr(exact, '_completion_hint', lambda *args: None)
    monkeypatch.setattr(exact_solve, 'z3_check_finite', unresolved)
    value = _value(direct.call('query', {'goals': [goal], 'timeout_ms': 30_000}), 'query')
    assert goal in value['undecided'] and not value['unreachable']
    assert calls
    for call in calls:
        # The fallback must retain every domain/source assertion verbatim.
        # Only its target may use consequences of those same assertions.
        _assert_original_roots(call[:-1], expected[:-1])
        oracle = z3.Solver(ctx=encoded.alive.ctx)
        oracle.set(timeout=30_000)
        oracle.add(*expected[:-1], call[-1] != expected[-1])
        assert oracle.check() == z3.unsat


@pytest.mark.parametrize('reset_values', [False, True])
def test_mixed_query_shares_semantic_batches_not_one_history_per_goal(direct, monkeypatch, reset_values):
    exact = direct.exact
    events = list(exact.source.relation.goal_names[:2])
    assert len(events) == 2
    controls = ['monitor.input-completion', 'monitor.output-without-transaction']
    goals = [events[0], controls[0], events[1], controls[1]]
    reset_goals = [goal for goal in exact.source.complete if goal.endswith('.violation')]
    if reset_values:
        assert reset_goals
        goals.extend(reset_goals)
        controls.extend(goal for goal in exact.source.complete if goal.endswith('.scenario'))
        goals.extend(controls[2:])
    owner = type(exact.source)
    bound = owner.bind_request_steps
    batches, requests = [], []

    def bind(source, horizon, **options):
        # Count each unrestricted batch when it starts, even if a prefix
        # yields a witness before the iterator reaches its final encoding.
        if options.get('goals') is not None and options.get('fixed_rows') is None:
            batches.append(tuple(options['goals']))
            requests.append(source.request_coordinates(horizon))
        return (yield from bound(source, horizon, **options))

    monkeypatch.setattr(owner, 'bind_request_steps', bind)
    before = exact.domain.digest()
    result = _value(direct.call('query', {'goals': goals, 'timeout_ms': 30_000}), 'query')
    assert set(result['reachable']) | set(result['unreachable']) | set(result['undecided']) == set(goals)
    assert len(batches) == 3 + int(reset_values)
    assert set(batches[0]) <= set(controls) - {'monitor.output-without-transaction'}
    assert set(batches[1]) == set(events)
    if reset_values:
        assert set(batches[2]) <= set(reset_goals)
        # A reset-image proof may remove state-value roots, but not the
        # whole-history escape check or the corresponding final verdict.
        assert set(reset_goals) - set(batches[2]) <= set(result['unreachable'])
    # The optional protocol proof cannot delay the ordinary source goals.
    # A locally proved property may still require an empty-root escape history.
    assert set(batches[-1]) <= {'monitor.output-without-transaction'}
    assert all(request is exact.request for request in requests)
    assert exact.domain.digest() == before


@pytest.mark.parametrize('calendar', ['initial-reset-only', 'later-reset', 'symbolic'])
def test_batching_uses_all_event_occurrences_and_actual_reset_domain(calendar):
    from types import SimpleNamespace
    from behave.sim.symbolic import ir, ranges
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.symbolic.step_template import StepTemplate

    procedure = ir.Procedure((), ('a',), (), (), ir._sequence((
        ir.If(ir.Lit(False), ir.Mark('dead'), ir.Skip()),
        ir.If(ir.Lit(False), ir.Mark('mixed_dead'), ir.Skip()),
        ir.If(ir.Op('truth', ir.Var('a')), ir.Mark('mixed_live'), ir.Skip()),
    )), signature=ProcedureSignature((('a', api.FiniteType(1)),), ()))
    template = StepTemplate(procedure, {'a': ranges.Range(0, 1)})
    assert template.literal_false_hits(('dead', 'mixed_dead', 'mixed_live')) == {'dead', 'mixed_dead'}
    contract = api.TaskContract([], [api.ClockDomain('c', 'clk', 'rst', reset_restores=True)])
    resets = (True, False, calendar == 'later-reset')
    request = SimpleNamespace(atomic=calendar != 'symbolic', slots=tuple(
        api.EventSlot(ticks=('c',), resets=('c',) if reset else ()) for reset in resets))
    source = SimpleNamespace(contract=contract,
        template=SimpleNamespace(direct=object(), observations=lambda _marks: template),
        goal_names=('dead_goal', 'mixed_goal'),
        machine=SimpleNamespace(occurrences=(('dead', None, 'dead_goal'),
            ('mixed_dead', None, 'mixed_goal'), ('mixed_live', None, 'mixed_goal'))))
    batches = []

    def batch(goals, *args, **kwargs):
        batches.append(goals)
        yield from ()

    owner = SimpleNamespace(source=source, request=request, _query_batch_steps=batch)
    violation = 'monitor.reset-restoration.c.violation'
    goals = ('dead_goal', 'monitor.input-completion', 'mixed_goal', violation)
    assert list(analysis_job._SourceAnalysis._query_steps(
        owner, goals, object())) == []
    assert batches[0] == (('dead_goal', 'monitor.input-completion', violation)
        if calendar == 'initial-reset-only' else ('dead_goal', 'monitor.input-completion'))
    assert batches[1] == ('mixed_goal',)  # A false sibling does not erase a live occurrence.
    assert len(batches) == (2 if calendar == 'initial-reset-only' else 3)
    if calendar != 'initial-reset-only':
        assert batches[2] == (violation,)
    assert sorted(goal for group in batches for goal in group) == sorted(goals)


@pytest.mark.parametrize('domain_fault', [False, True])
def test_literal_false_source_goal_shares_control_history_without_hiding_escape(
        tmp_path, monkeypatch, domain_fault):
    from dataclasses import replace

    source = '''module M(input [1:0] a,output logic [1:0] y);
      always_comb if(1'b0) y=3; else y=a;
    endmodule'''
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(2), 'a')],
            api.settled('in'), None, kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(2), 'y')],
            api.settled('out'), None, kind='transaction', answers_per_transaction=1)], [])
    context, *_ = task_fixtures._worker(tmp_path, source, contract, 2)
    admitted = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract))
    worker = analysis_job.PreparedAnalysis({**context.source_payload(), 'horizon': 2,
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity}, direct_source=True)
    _value(worker.binding(), 'bind')
    branch = next(name for name, goal in context.frontend_catalogue.items()
                  if goal.family == 'control-choice' and goal.event == 'true')
    goals = [branch, 'monitor.input-completion']
    bind, batches = worker.exact.source.bind_request, []

    def captured(horizon, **options):
        relation, request, encoded = bind(horizon, **options)
        batches.append(tuple(options['goals']))
        if domain_fault:
            encoded = replace(encoded, monitor_faults=(
                {'injected-domain': z3.BoolVal(True, ctx=encoded.alive.ctx)},))
        return relation, request, encoded

    monkeypatch.setattr(worker.exact.source, 'bind_request', captured)
    reply = worker.call('query', {'goals': goals, 'timeout_ms': 30_000})
    # The proved dead source event needs no history root, and a literal-false
    # drain proves input completion locally; the one complete history is still
    # built, with no goal roots, so its escape is checked before either is UNSAT.
    assert batches == [()]
    if domain_fault:
        refused = analysis_job.decode_analysis_reply(reply, 'query')
        assert isinstance(refused, Refusal)
        assert refused.kind == 'rtl-source-operation-domain-unencoded'
    else:
        result = _value(reply, 'query')
        assert set(result['unreachable']) == set(goals)
        assert not result['reachable'] and not result['undecided']


@pytest.mark.parametrize('prefix_answer', ['unsat', 'unknown', 'invalid-extension'])
def test_prefix_failure_keeps_the_complete_original_goal_query(direct, monkeypatch, prefix_answer):
    from behave.sim.symbolic import exact_solve
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl import source_member

    exact = direct.exact
    goal = exact.source.relation.goal_names[0]
    _, _, encoded = exact.source.bind_request(6, goals=[goal])
    roots = (*encoded.constraints,
        *(z3.Not(term) for term in encoded.environment_assumptions.values()))
    real_solve = exact_solve.z3_check_finite
    calls = []
    original_environment = exact.request.constraints

    def prefixes(history, environment, *, check):
        check()
        assert history is encoded
        hint = (exact.request.full_queue_hint() if exact.request.atomic or exact.request.settled
                else (z3.Or(z3.BoolVal(False, ctx=encoded.alive.ctx),
                            *exact.request.drain_slots),))
        assert len(environment) == len(original_environment) + len(hint)
        assert all(z3.eq(actual, original) for actual, original in
                   zip(environment[:len(original_environment)], original_environment, strict=True))
        assert all(z3.eq(actual, expected) for actual, expected in
                   zip(environment[len(original_environment):], hint, strict=True))
        yield (), {goal: z3.BoolVal(True, ctx=encoded.alive.ctx)}, {}
        raise AssertionError('an unsuccessful prefix must fall back')

    monkeypatch.setattr(source_member, 'source_query_prefixes', prefixes)
    # Exercise the completed-history prefix/fallback pair being intercepted;
    # growing construction is covered independently by the streaming tests.
    monkeypatch.setattr(exact._queries, 'start', lambda *a, **k: None)
    if prefix_answer == 'invalid-extension':
        monkeypatch.setattr(source_member, 'source_member_hint', lambda *args, **kwargs: None)

    def solve(assertions, *args, **kwargs):
        calls.append(tuple(assertions))
        if len(assertions) == 1 and prefix_answer != 'invalid-extension':
            answer = SolverAnswer.UNSAT if prefix_answer == 'unsat' else SolverAnswer.UNKNOWN
            return exact_solve.ExactCheckResult(answer,
                reason='prefix only' if answer is SolverAnswer.UNKNOWN else '')
        return real_solve(assertions, *args, **kwargs)

    monkeypatch.setattr(exact_solve, 'z3_check_finite', solve)
    result = _value(direct.call('query', {'goals': [goal], 'timeout_ms': 30_000}), 'query')
    assert goal in result['reachable']
    assert not result['unreachable'] and not result['undecided']
    # An optional local lemma may precede the controlled prefix. Its failure
    # must still reach the identical unrestricted whole-history fallback.
    assert len(calls) >= 2 and len(calls[-2]) == 1
    _assert_original_roots(calls[-1][:-1], roots)
    assert exact.request.constraints is original_environment


def test_unsat_prefix_uses_complete_query_without_growing_proposals(direct, monkeypatch):
    from behave.sim.rtl import source_member

    exact = direct.exact
    goal = exact.source.relation.goal_names[0]
    original, advanced = source_member.source_query_prefixes, []

    def prefixes(history, environment, *, check):
        yield (z3.BoolVal(False, ctx=exact.request.context),), {
            goal: z3.BoolVal(True, ctx=exact.request.context)}, {}
        advanced.append(True)
        yield from original(history, environment, check=check)

    monkeypatch.setattr(source_member, 'source_query_prefixes', prefixes)
    result = _value(direct.call('query', {'goals': [goal], 'timeout_ms': 30_000}), 'query')
    assert not advanced and goal in result['reachable']
    assert not result['unreachable'] and not result['undecided']


@pytest.mark.parametrize('prefix_answer', ['unsat', 'unknown'])
def test_prefixes_share_caller_deadline_without_a_hidden_subbudget(direct, monkeypatch, prefix_answer):
    from behave.sim.symbolic import exact_solve
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl import source_member

    exact = direct.exact
    goal = exact.source.relation.goal_names[0]
    _, _, encoded = exact.source.bind_request(6, goals=[goal])
    roots = (*encoded.constraints,
        *(z3.Not(term) for term in encoded.environment_assumptions.values()))
    marker = z3.Bool('optional_prefix_budget_probe', ctx=encoded.alive.ctx)
    real_solve, real_clock = exact_solve.z3_check_finite, time.monotonic
    spent, limits, full = [0.0], [], []
    monkeypatch.setattr(time, 'monotonic', lambda: real_clock() + spent[0])

    def prefixes(*args, **kwargs):
        for _ in range(4):
            yield (marker,), {goal: z3.BoolVal(True, ctx=encoded.alive.ctx)}, {}

    def solve(assertions, timeout_ms, **kwargs):
        if assertions and z3.eq(assertions[0], marker):
            limits.append(timeout_ms)
            # Every proposal gets the actual remaining caller allowance,
            # not a fixed fraction and not a fresh 30 seconds per prefix.
            assert 20_000 < timeout_ms <= 30_000
            spent[0] += 1
            answer = SolverAnswer.UNSAT if prefix_answer == 'unsat' else SolverAnswer.UNKNOWN
            return exact_solve.ExactCheckResult(answer,
                reason='optional proposal timed out' if answer is SolverAnswer.UNKNOWN else '')
        if len(assertions) >= len(roots) + 1 and all(
                z3.eq(a, b) for a, b in zip(assertions, roots)):
            _assert_original_roots(assertions[:-1], roots)
            full.append(timeout_ms)
        return real_solve(assertions, timeout_ms, **kwargs)

    monkeypatch.setattr(source_member, 'source_query_prefixes', prefixes)
    monkeypatch.setattr(exact_solve, 'z3_check_finite', solve)
    # As in the fallback test: intercept the completed-history prefixes. A fresh
    # module fixture would otherwise stream a growing construction instead.
    monkeypatch.setattr(exact._queries, 'start', lambda *a, **k: None)
    result = _value(direct.call('query', {'goals': [goal], 'timeout_ms': 30_000}), 'query')
    assert len(limits) == 1
    assert full and 20_000 < full[-1] < limits[0]
    assert goal in result['reachable']
    assert not result['unreachable'] and not result['undecided']


@pytest.mark.parametrize('stage', ['protocol', 'goal'])
def test_local_proofs_share_caller_deadline_without_a_hidden_subbudget(direct, monkeypatch, stage):
    from behave.sim.symbolic import exact_solve
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl import source_domains, source_member, source_stability

    exact = direct.exact
    # A local UNKNOWN must not classify the complete domain. It also must not
    # create a private deadline or restart the original operation's allowance.
    goals = ['monitor.sink-stability', 'observation:zero']
    marker = z3.Bool('optional_lemma_budget_probe', ctx=exact.source.relation.source.context)
    real_solve, real_clock = exact_solve.z3_check_finite, time.monotonic
    spent, limits, original_calls = [0.0], [], []
    monkeypatch.setattr(time, 'monotonic', lambda: real_clock() + spent[0])
    batch_steps, remaining = exact._query_batch_steps, []

    def batches(goals, scope, **kwargs):
        yield from batch_steps(goals, scope, **kwargs)
        remaining.append(scope.budget.remaining_ms())

    monkeypatch.setattr(exact, '_query_batch_steps', batches)

    def lemma(*args, solve, check):
        check()
        answer = solve('test-optional-local-proof', (marker,))
        assert answer.answer is SolverAnswer.UNKNOWN
        check()
        return frozenset()

    owners = {'protocol': (source_stability, 'prove_protocol_goals'),
              'goal': (source_domains, 'prove_transition_goals')}
    monkeypatch.setattr(*owners[stage], lemma)
    # Keep the final query visible rather than answering it from a SAT hint.
    def prefixes(*args, **kwargs):
        return iter(())
    monkeypatch.setattr(source_member, 'source_query_prefixes', prefixes)

    def solve(assertions, timeout_ms, **kwargs):
        if len(assertions) == 1 and z3.eq(assertions[0], marker):
            if not limits:
                assert 20_000 < timeout_ms <= 30_000
                spent[0] += 20
            else:
                # A second observation batch inherits the remaining deadline;
                # it does not receive a fresh operation or hidden subbudget.
                assert 0 < timeout_ms <= 10_000
            limits.append(timeout_ms)
            return exact_solve.ExactCheckResult(SolverAnswer.UNKNOWN, reason='incomplete local proof')
        original_calls.append((timeout_ms, bool(limits)))
        return real_solve(assertions, timeout_ms, **kwargs)

    monkeypatch.setattr(exact_solve, 'z3_check_finite', solve)
    result = _value(direct.call('query', {'goals': goals, 'timeout_ms': 30_000}), 'query')
    expected_calls = (2 if stage == 'goal' and exact.source.template.direct is not None else 1)
    assert len(limits) == expected_calls
    assert remaining and 0 < remaining[-1] <= 10_000
    # Literal closure after the last lemma may need no further SMT query.
    # Every actual query after it still receives only the remaining allowance.
    assert all(0 < timeout <= 10_000 for timeout, after in original_calls if after)
    assert 'observation:zero' in result['reachable']
    assert 'monitor.sink-stability' in result['unreachable']
    assert not result['undecided']


@pytest.mark.parametrize('refused', [False, True])
def test_transition_domain_preparation_is_solver_free_and_shared_per_query(direct, monkeypatch, refused):
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl import source_domains

    original, calls = source_domains.prove_transition_domains, []

    def prove(source, *, check, solve=None):
        assert solve is None  # No optional global SMT work before actual targets.
        calls.append(source)
        if refused:
            raise Refused(Refusal('test-local-domain-unavailable', 'keep the original escapes'))
        return original(source, solve=solve, check=check)

    monkeypatch.setattr(source_domains, 'prove_transition_domains', prove)
    goal = direct.exact.source.relation.goal_names[0]
    goals = ['monitor.sink-stability', goal]  # Separate monitor/source histories.
    for count in (1, 2):
        result = _value(direct.call('query', {'goals': goals, 'timeout_ms': 30_000}), 'query')
        assert len(calls) == count  # Reuse only within this query, not later requests.
        assert goal in result['reachable']
        assert 'monitor.sink-stability' in result['unreachable']
        assert not result['undecided']


@pytest.mark.parametrize('family', ['completion', 'control', 'reset', 'reset-scenario', 'source', 'transaction', 'batch'])
def test_projected_batch_matches_complete_history(direct, family):
    source = direct.exact.source
    # Test a freshly constructed projection, not a wider cached history left
    # by another parameter. Observation-batch changes have their own regression.
    source._query_requests.clear()
    full_relation, full_request, full = source.bind_request(6)
    compiled = dict(source.template.direct._templates)
    if family == 'completion':
        goals = ['monitor.input-completion']
    elif family == 'control':
        goals = ['monitor.output-without-transaction']
    elif family == 'reset':
        goals = [goal for goal in source.complete if 'reset' in goal]
    elif family == 'reset-scenario':
        goals = [goal for goal in source.complete if goal.endswith('.scenario')]
    elif family == 'source':
        goals = list(source.relation.goal_names[:2])
    elif family == 'transaction':
        goals = [goal for goal in source.complete if goal.startswith('observation:')]
    else:
        goals = list(source.complete)
    assert goals
    relation, request, local = source.bind_request(6, goals=goals)
    assert source.template.direct._templates == compiled
    assert request is full_request
    assert full_relation.complete_state and relation.query_projection
    if family == 'reset-scenario':
        assert not relation.monitors.state_widths
        assert not any(goal.endswith('.violation') for goal in local.hits)
    assert set(goals) <= set(local.hits) <= set(source.complete)
    assert local.occurrences == {}  # Full native occurrence history is not a query root.
    assert source.bind_request(6, goals=list(reversed(goals)))[2] is local
    solver = z3.Solver(ctx=local.alive.ctx)
    solver.set(timeout=30_000)
    solver.add(*local.constraints, *full.constraints)
    assert solver.check() == z3.sat
    changed = [local.hits[goal] != full.hits[goal] for goal in goals]
    changed.extend((local.alive != full.alive, local.unfinished != full.unfinished))
    for attr in ('domain_faults', 'environment_assumptions'):
        left, right = getattr(local, attr), getattr(full, attr)
        assert left.keys() == right.keys()
        changed.extend(left[name] != right[name] for name in left)
    solver.add(z3.Or(*changed))
    assert solver.check() == z3.unsat


def test_residual_queries_reuse_batch_and_solver_without_keeping_answers(direct, monkeypatch):
    from behave.sim.rtl import source_member
    from behave.sim.rtl.analysis_query import GrowingGoalQuery

    # Exercise the complete solver fallback deliberately. Monitor prefix
    # witnesses may now settle these goals without allocating that solver;
    # their independent native confirmation is tested separately above.
    monkeypatch.setattr(source_member, 'source_query_prefixes', lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(GrowingGoalQuery, 'propose', lambda *args, **kwargs: (None, None, []))
    goals = ['observation:zero', 'observation:pair']
    first = _value(direct.call('query', {'goals': goals, 'timeout_ms': 30_000}), 'query')
    assert not first['undecided']
    assert first['queries'][0]['telemetry']['timings_ns']['encode_unroll_ns'] >= 0
    assert all('encode_unroll_ns' not in query['telemetry']['timings_ns']
               for query in first['queries'][1:])
    query_model = direct.exact._queries.current
    encoded, preparation = query_model.encoding, query_model.preparation
    solver = preparation._solver
    assert solver is not None
    second = _value(direct.call('query', {'goals': list(reversed(goals)), 'timeout_ms': 30_000}), 'query')
    assert not second['undecided']
    assert direct.exact._queries.current is query_model
    assert preparation._solver is solver
    assert second['queries'] and all(set(query['goals']) <= set(goals) for query in second['queries'])
    smaller = _value(direct.call('query', {'goals': goals[:1], 'timeout_ms': 30_000}), 'query')
    assert not smaller['undecided']
    replaced_history = direct.exact._queries.current.encoding is not encoded
    assert replaced_history
    assert smaller['queries'] and all(query['goals'] == goals[:1] for query in smaller['queries'])
