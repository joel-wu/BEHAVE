"""Optional passive observations cannot change concrete scoring or invent evidence."""

import json

import pytest

from behave.sim.core.refusal import Refused
from behave.sim.core.record_value import thaw_record_value
from behave.sim.core.stimulus import AuditedTrace, EventSlot, SlotRecord, Stimulus
from behave.sim.rtl.initial_state import RTLInitialState
from behave.sim.rtl.source_boundary_capture import HISTORY_VERSION, VERSION, source_history_view
from behave.sim.rtl.startup_state import RTLStartupState


def _trace():
    startup = RTLStartupState(RTLInitialState.from_values('a'*64,
        {'saved': 2, 'derived': 2}, {'saved': 0, 'derived': 0}), [('a', 2, 0)])
    return AuditedTrace(Stimulus(slots=[EventSlot()]), records=[SlotRecord(outputs={'saved': 3})],
        artifact='a'*64, query_boundaries={
            'version': VERSION, 'fields': [['saved', 2], ['derived', 2]], 'samples': [],
            'history': dict(version=HISTORY_VERSION, startup=startup.as_dict(), refusal=None,
                widths={'saved': 2, 'derived': 2}, initial={'saved': 3, 'derived': 1},
                sampled=[{'saved': 3, 'derived': 1}], states=[{'saved': 2, 'derived': 1}])})


def test_passive_history_roundtrip_projects_only_the_exact_owner_layout():
    original = _trace()
    raw = AuditedTrace.from_dict(json.loads(json.dumps(original.as_dict())))
    before = raw.as_dict()
    view = source_history_view(raw, ('saved',))
    assert raw.as_dict() == before == original.as_dict()
    assert raw.initialization is None and not raw.initial_state and not raw.records[0].state
    assert dict(view.initial_state) == {'saved': 3}
    assert dict(view.records[0].state) == {'saved': 2}
    assert dict(view.records[0].outputs) == {'saved': 3}
    assert RTLStartupState.from_dict(thaw_record_value(view.initialization)).registers.values() == {'saved': 0}
    assert source_history_view(view, ('saved',)) is view  # Explicit replay path is untouched.


@pytest.mark.parametrize('fault', ['absent', 'old-version', 'extra-field', 'capture-failed',
    'no-startup', 'foreign-artifact', 'width', 'missing-field', 'unknown', 'overflow',
    'missing-slot', 'missing-control', 'contradict-output', 'contradict-state'])
def test_partial_or_conflicting_history_never_supplies_an_exact_witness(fault):
    image = _trace().as_dict()
    history = image['query_boundaries']['history']
    if fault == 'absent':
        image.pop('query_boundaries')
    elif fault == 'old-version':
        image['query_boundaries']['version'] = 'rtl-source-boundaries/4'
    elif fault == 'extra-field':
        history['guess'] = True
    elif fault == 'capture-failed':
        history['refusal'] = {'kind': 'test-unavailable', 'why': 'not observed'}
    elif fault == 'no-startup':
        history['startup'] = None
    elif fault == 'foreign-artifact':
        image['artifact'] = 'b'*64
    elif fault == 'width':
        history['widths']['saved'] = 3
    elif fault == 'missing-field':
        history['initial'].pop('saved')
    elif fault == 'unknown':
        history['initial']['saved'] = None
    elif fault == 'overflow':
        history['states'][0]['saved'] = 4
    elif fault == 'missing-slot':
        history['states'] = []
    elif fault == 'missing-control':
        history['sampled'] = []
    elif fault == 'contradict-output':
        image['records'][0]['outputs'] = [['saved', 1]]
    elif fault == 'contradict-state':
        image['records'][0]['state'] = [['saved', 1]]
    raw = AuditedTrace.from_dict(image)
    before = raw.as_dict()
    with pytest.raises(Refused):
        source_history_view(raw, ('saved',))
    assert raw.as_dict() == before  # Optional analysis never edits scoring evidence.


def test_unobserved_required_storage_is_not_zero_filled():
    with pytest.raises(Refused, match='omits required compiler storage'):
        source_history_view(_trace(), ('saved', 'other'))


@pytest.mark.cosim
@pytest.mark.solver
@pytest.mark.eda('slang')
@pytest.mark.parametrize('mode', ['scalar', 'array', 'declaration', 'unused', 'falling', 'unavailable'])
def test_ordinary_native_run_automatically_persists_complete_passive_history(
        tmp_path, monkeypatch, record_property, mode):
    from behave.sim import api, harness
    from behave.sim.rtl.analysis_job import decode_analysis_reply
    from behave.sim.worker import run_job_strict
    from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract
    from .test_rtl_source_worker_task import _worker

    source = HISTORY_SOURCE
    if mode == 'array':
        source = source.replace('reg [1:0] saved;', 'reg [1:0] saved[2:1];')
        source = source.replace('saved<=0;', 'saved[2]<=0; saved[1]<=0;')
        source = source.replace('saved==1', '(saved[2]^saved[1])==1')
        source = source.replace('saved<=a;', 'saved[2]<=a; saved[1]<=saved[2];')
    if mode == 'declaration':
        source = source.replace('reg [1:0] saved;', 'reg [1:0] saved=3;')
    if mode == 'unused':
        source = source.replace('reg [1:0] saved;', 'reg [1:0] saved; reg [3:0] spare;')
    contract = _simple_contract()
    schedule = None
    if mode == 'falling':
        from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
        source = source.replace('posedge clk', 'negedge clk').replace('!rst &&', 'rst &&').replace(
            'if(rst)', 'if(!rst)')
        source = source.replace('reg [1:0] saved;',
            'reg [1:0] saved; reg release_seen=0; always @(posedge clk) release_seen<=1;')
        image = contract.as_dict()
        image['clocks'][0].update(edge='falling', reset_active_low=True)
        contract = api.TaskContract.from_dict(image)
        schedule = GlobalSchedule([ClockTiming('c', 2, clock='clk', reset='rst',
                                               edge='falling', reset_active_low=True)],
            [GlobalEventSlot(2*i, event=api.EventSlot(ticks=('c',), resets=('c',) if i == 0 else (),
                offers=('in',) if i == 1 else (), controls={'rst': int(i != 0), 'ordy': 1}))
             for i in range(5)])
    context, prepared, _, _, _ = _worker(tmp_path, source, contract, 5, schedule=schedule)
    stimulus = api.reset_then(contract, [(1,)], controls={'ordy': 1}, drain=2, reset_hold_cycles=1)
    if schedule is not None:
        from behave.sim.rtl.exact_product import stimulus_for_global_schedule
        stimulus = stimulus_for_global_schedule(schedule, {'in': ((1,),)}, source='automatic capture')
    ordinary = context.artifact.traces_per_process(contract, [stimulus], workers=1)[0]
    assert type(ordinary) is api.AuditedTrace, ordinary.as_dict()
    if mode == 'unavailable':
        render = harness.render_template
        def missing_readback(name, **kwargs):
            text = render(name, **kwargs)
            if name == 'driver_tb.py.tmpl':
                text += '''
from behave.sim.rtl import replay_startup
from behave.sim.core.refusal import Refusal, Refused
def unavailable(*args, **kwargs):
    raise Refused(Refusal('test-passive-readback-unavailable', 'readback unavailable'))
replay_startup.capture_startup_state = unavailable
'''
            return text
        monkeypatch.setattr(harness, 'render_template', missing_readback)
    observed = context.artifact.traces_per_process(
        contract, [stimulus], workers=1, _capture_query_boundaries='history')[0]
    assert type(observed) is api.AuditedTrace, observed.as_dict()
    wire = json.loads(json.dumps(observed.as_dict()))
    assert wire['query_boundaries']['history']['version'] == HISTORY_VERSION
    stripped = dict(wire)
    stripped['query_boundaries'] = dict(stripped['query_boundaries'])
    stripped['query_boundaries'].pop('history')
    assert stripped == ordinary.as_dict()
    raw = api.AuditedTrace.from_dict(wire)  # Use persisted bytes, not a injected startup fixture.
    if mode == 'unavailable':
        with pytest.raises(Refused, match='passive construction capture was unavailable'):
            source_history_view(raw, prepared.initial_layout.widths)
        assert wire['query_boundaries']['history']['refusal']['kind'] == 'test-passive-readback-unavailable'
        return
    view = source_history_view(raw, prepared.initial_layout.widths)
    receipt = RTLStartupState.from_dict(thaw_record_value(view.initialization))
    assert set(receipt.registers.values()) == set(prepared.initial_layout.widths)
    if mode == 'unused':
        assert prepared.initial_layout.widths['spare'] == 4
        captured = RTLStartupState.from_dict(wire['query_boundaries']['history']['startup'])
        assert receipt.registers.values()['spare'] == captured.registers.values()['spare']
    if mode == 'declaration':
        assert receipt.registers.values()['saved'] == 0
        assert dict(view.initial_state)['saved'] == 3
    if mode == 'falling':
        assert receipt.registers.values()['release_seen'] == 0
        assert dict(view.initial_state)['release_seen'] == 1
    admitted = api.admit(contract, api.realization_of_rtl_source(
        source, 'automatic passive capture audit', top='M', contract=contract))
    reply = run_job_strict('rtl_exact_analysis', {
        **context.source_payload(), 'horizon': len(stimulus.slots),
        'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity,
        'operation': 'record-audit', 'stimulus': stimulus.as_dict(),
        'raw_trace': raw.as_dict(),
    }, timeout_s=120)
    result = decode_analysis_reply(reply, 'record-audit')
    assert not isinstance(result, api.Refusal), result.as_dict()
    assert result['disposition'] == 'admitted', result
    record_property('automatic_capture_audit', result['disposition'])
