"""Compiler-owned construction crosses the existing isolated layout boundary."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.analysis_budget import AnalysisStopped, analysis_scope
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.goal_policy import evaluation_goal_policy
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.exact_job_context import RTLExactJobContext
from behave.sim.rtl.exact_job_instance import PreparedSourceExact
from behave.sim.rtl.exact_job_layout import RTLGenerationLayout, decode_layout_reply, layout_job
from behave.sim.stimulus_set import GenerationSpec

from .test_rtl_source_history import _simple_contract
from .test_rtl_source_native_layout_request import SOURCE

pytestmark = [pytest.mark.cosim, pytest.mark.solver]


@pytest.mark.parametrize('kind', ['range', 'shape', 'type', 'width', 'capability',
                                'resource', 'goal', None, 'future-kind'])
def test_encoding_limit_is_a_typed_layout_refusal(monkeypatch, kind):
    from behave.sim.symbolic.encoder import EncodingError
    from behave.sim.rtl import exact_job_layout

    def fail(payload):
        raise EncodingError('planned loop expansion exceeds its reported capacity', kind)

    monkeypatch.setattr(exact_job_layout, 'PreparedSourceExact', fail)
    reply = layout_job({'source_layout': True})
    got = decode_layout_reply(reply)
    reason = kind if kind not in (None, 'future-kind') else 'operation'
    assert type(got) is Refusal
    assert got.kind == f'rtl-exact-encoding-{reason}-unavailable'
    assert got.detail['encoding_kind'] == reason
    assert got.detail['error'] == 'EncodingError: planned loop expansion exceeds its reported capacity'
    assert reply['status'] == 'refused'
    assert set(reply) == {'version', 'status', 'refusal'}


@pytest.mark.parametrize('exception', [ValueError('bad payload'), RuntimeError('bug')])
def test_unexpected_layout_errors_are_not_relabelled_as_encoding_limits(monkeypatch, exception):
    from behave.sim.rtl import exact_job_layout

    def fail(payload):
        raise exception

    monkeypatch.setattr(exact_job_layout, 'PreparedSourceExact', fail)
    with pytest.raises(type(exception), match=str(exception)):
        layout_job({'source_layout': True})


def _context(artifact, root):
    contract = _simple_contract()
    frontend = artifact.frontend_catalogue()
    complete = evaluation_goal_policy(contract, frontend, artifact_kind='rtl').manifest
    return RTLExactJobContext(artifact, contract, GenerationSpec(5, 2, 1, 0),
        frontend, complete, workdir=root)


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    root = tmp_path_factory.mktemp('source-worker-layout')
    rtl = root / 'design.sv'
    # Unreferenced storage is not permission to omit its startup coordinates.
    rtl.write_text(SOURCE.replace('reg [1:0] mem', 'reg [4:0] unused=17;\n  reg [1:0] mem'))
    artifact = RTLArtifact(rtl, 'M', root / 'artifact', timeout_s=120)
    artifact.bind_observation(_simple_contract())
    context = _context(artifact, root / 'context')
    with analysis_scope(120):
        layout = context.prepare_source()
    assert isinstance(layout, RTLGenerationLayout), getattr(layout, 'as_dict', lambda: layout)()
    return context, layout


def test_real_isolated_layout_never_builds_or_grants_the_circuit_product(prepared):
    context, layout = prepared
    assert layout.outputs == ('ir', 'ov', 'y')
    assert layout.state_names == ('child.mem[1][-1]', 'child.mem[1][0]',
        'child.mem[2][-1]', 'child.mem[2][0]', 'child.q', 'child.unused', 'ov')
    assert context.goal_capability_use is None
    assert context.prepare_source() is layout
    with pytest.raises(Refused, match='admission-context-unbound'):
        context.analysis_payload()


def test_worker_retains_startup_pins_initializers_and_full_occurrences(prepared):
    context, layout = prepared
    source = PreparedSourceExact(context.source_payload())
    assert source.initial_layout.widths == {
        **dict.fromkeys(('child.mem[1][-1]', 'child.mem[1][0]',
                        'child.mem[2][-1]', 'child.mem[2][0]', 'child.q'), 2),
        'child.unused': 5, 'ov': 1}
    assert source.initial_layout.startup_pins == {'clk': 1, 'rst': 1, 'iv': 1, 'a': 2, 'ordy': 1}
    assert set(source.initial_binding) == set(source.initial_layout.bounds)
    assert source.initial.bits.keys() == source.machine.program.fields.keys()
    assert source.relation.occurrence_names
    assert layout_job(context.source_payload()) == layout.as_dict()


def test_worker_binds_every_frozen_goal_without_circuit_or_seed(prepared):
    import z3
    from .test_rtl_source_native_audit import _audit

    context, _ = prepared
    source = PreparedSourceExact(context.source_payload())
    relation, request, encoded = source.bind_request(5)
    assert set(encoded.hits) == set(context.complete_catalogue)
    assert relation.complete_state and relation.monitors is not None
    assert relation.transactions is not None
    assert relation.monitor_native_names['child.mem[0]'] == 'child.mem[2][-1]'
    assert relation.monitor_native_names['child.mem[3]'] == 'child.mem[1][0]'
    assert source.bind_request(5) == (relation, request, encoded)
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, request.lengths['in'] == 2,
               request.queues['in'][0][0] == 1, request.queues['in'][1][0] == 2)
    # HDL declaration initializers constrain construction; they cannot be
    # contradicted just to choose zero for the other physical coordinates.
    declared = {'child.q':3, 'child.unused':17}
    for coordinate, name, _ in (*source.initial_layout.coordinates, *source.initial_layout.pin_coordinates):
        solver.add(source.initial_binding[coordinate] == declared.get(name, 0))
    for i in range(5):
        solver.add(request.resets['c'][i] == (i in (0, 4)),
                   request.actions[i].controls['ordy'], request.actions[i].offers['in'] == (i in (1, 2)))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    stimulus = request.codec.decode(rows)
    assert stimulus.depth('in') == 2
    trace = context.source_trace(stimulus, startup_state=source.initial_layout.decode(rows))
    result, _ = _audit(relation.compare_native(request, rows, trace, context.frontend_catalogue,
                                               observation=source.observation))
    assert result.status.value == 'agreed', result.as_dict()
    assert context.goal_capability_use is None


def test_startup_native_replay_consumes_source_layout_without_smt(prepared, monkeypatch):
    context, _layout = prepared
    source = PreparedSourceExact(context.source_payload())
    initial = source.initial_layout.decode([dict.fromkeys(source.initial_layout.bounds, 0)])
    stimulus = api.Stimulus({'in': [(1,), (2,)]},
        [api.EventSlot(reset=True, controls={'ordy': 1, 'rst': 1}),
         *(api.EventSlot(offers=('in',), controls={'ordy': 1, 'rst': 0}) for _ in range(4))])
    assert not stimulus.faults_against(context.contract)

    trace = context.source_trace(stimulus, startup_state=initial)
    initial.check_trace(trace)
    assert len(trace.records) == len(stimulus.slots)
    assert sum(dict(row.accepted).get('in') is not None for row in trace.records) == 2
    assert sum(dict(row.accepted).get('out') is not None for row in trace.records) == 2
    assert context.source_trace(stimulus, startup_state=initial) is trace


@pytest.mark.parametrize('change', ['extra', 'version', 'source', 'top', 'bytes'])
def test_source_image_is_closed_and_artifact_bound(prepared, tmp_path, change):
    payload = copy.deepcopy(prepared[0].source_payload())
    image = json.loads(Path(payload['relation_path']).read_bytes())
    if change == 'extra':
        image['trusted'] = True
    elif change == 'version':
        image['version'] = 'obsolete'
    elif change == 'source':
        image['source'] += '\n// changed'
    elif change == 'top':
        image['top'] = 'Other'
    body = json.dumps(image).encode()
    path = tmp_path / 'source.json'
    path.write_bytes(body)
    payload['relation_path'] = str(path)
    if change != 'bytes':
        payload['source_image_digest'] = hashlib.sha256(body).hexdigest()
    with pytest.raises((ValueError, Refused)):
        PreparedSourceExact(payload)


@pytest.mark.parametrize('change', ['flag', 'extra', 'observer', 'catalogue', 'complete'])
def test_context_binding_is_not_weakened_for_source_layout(prepared, change):
    payload = copy.deepcopy(prepared[0].source_payload())
    if change == 'flag':
        payload['source_layout'] = 1
    elif change == 'extra':
        payload['trust_native'] = True
    elif change == 'observer':
        payload['observation']['contract'] = '0' * 64
    elif change == 'catalogue':
        empty = CatalogueManifest(())
        payload['frontend_catalogue'] = empty.as_dict()
        payload['observation']['catalogue'] = empty.digest()
    else:
        payload['complete_catalogue'] = CatalogueManifest(()).as_dict()
    with pytest.raises((ValueError, TypeError, Refused)):
        PreparedSourceExact(payload)


def test_failed_preparation_does_not_retry_or_publish_a_layout(prepared, tmp_path, monkeypatch):
    context = _context(prepared[0].artifact, tmp_path)
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(kwargs)
        raise Refused(Refusal('test-frontend-unavailable', 'compiler did not finish'))

    monkeypatch.setattr('behave.sim.rtl.elaboration.source_documents', fail)
    refusal = context.prepare_source()
    assert refusal.kind == 'test-frontend-unavailable'
    assert context.prepare_source() is refusal and len(attempts) == 1
    with pytest.raises(RuntimeError, match='not prepared'):
        context.source_payload()


def test_analysis_deadline_is_not_restarted_after_source_compilation(prepared, tmp_path, monkeypatch):
    context = _context(prepared[0].artifact, tmp_path)
    image = json.loads(Path(prepared[0].source_payload()['relation_path']).read_bytes())
    calls = []
    clock = [0.0]
    monkeypatch.setattr('behave.sim.analysis_budget.time.monotonic', lambda: clock[0])

    def compile_source(*args, timeout_s, **kwargs):
        calls.append(timeout_s)
        clock[0] += 11
        return {name: image[name] for name in ('source', 'cst', 'ast', 'sensitivity')}

    def no_worker(*args, **kwargs):
        pytest.fail('expired analysis cannot launch a layout worker')

    monkeypatch.setattr('behave.sim.rtl.elaboration.source_documents', compile_source)
    monkeypatch.setattr('behave.sim.worker.run_job_strict', no_worker)
    with analysis_scope(10), pytest.raises(AnalysisStopped):
        context.prepare_source()
    assert calls == [10]
    # Cancellation is not published as a result. A retry under a new owner whose
    # deadline has also passed stops before a worker and does not recompile.
    with analysis_scope(1), pytest.raises(AnalysisStopped):
        clock[0] += 1
        context.prepare_source()
    assert calls == [10]
    with pytest.raises(RuntimeError, match='not prepared'):
        context.source_payload()


def test_cached_source_layout_rechecks_artifact_identity(prepared, monkeypatch):
    context = prepared[0]
    monkeypatch.setattr(RTLArtifact, 'identity', property(lambda self: '0' * 64))
    with pytest.raises(Refused, match='source-changed-after-context-binding'):
        context.prepare_source()
    with pytest.raises(Refused, match='source-changed-after-context-binding'):
        context.source_payload()
