"""Deep state needs related inputs, not independently mutated final commands."""

from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.engines import probing
from behave.sim.engines.rtl.generation import RTLGenerationProvider, RTLScheduleSegment, _joint_segments
from behave.sim.generation import StimulusField
from behave.sim.query_plan import widening_windows
from behave.sim.rtl.artifact import RTLArtifact


def test_shared_window_ladder_includes_late_entries_and_full_epoch():
    for size in (0, 1, 2, 3, 10, 100, 1000):
        windows = tuple(widening_windows(range(size)))
        assert len(windows) < 2 * max(1, size)
        assert len(windows) == len(set(windows))
        assert all(len(w) > 1 and w == tuple(range(w[0], w[-1]+1)) for w in windows)
        if size > 1:
            assert tuple(range(size)) in windows
        if size > 3:
            assert (2, 3) in windows


def test_joint_windows_preserve_reset_epochs_framing_and_coincident_inputs():
    contract = SimpleNamespace(inputs=lambda: (
        SimpleNamespace(name='in', fields=(SimpleNamespace(name='a'), SimpleNamespace(name='last')),
                        framing=SimpleNamespace(fields=('last',))),))
    seed = api.Stimulus({'in': [(0, 0)] * 6}, [api.EventSlot(reset=i in (0, 4)) for i in range(8)])
    segments = tuple(RTLScheduleSegment(str(i), seed.fingerprint, slot, slot+1, i,
        (StimulusField('in', i, 0, slot), StimulusField('in', i, 1, slot)))
        for i, slot in enumerate((1, 2, 3, 5, 6, 7)))
    windows = tuple(_joint_segments(contract, seed, segments))
    assert {(w.start, w.stop) for w in windows} == {(1, 3), (1, 4), (5, 7), (5, 8)}
    assert all(f.field == 0 for w in windows for f in w.mutable_fields)
    assert all(not w.start < 4 < w.stop for w in windows)
    duplicate = RTLScheduleSegment('coincident', seed.fingerprint, 2, 3, 1,
                                  (StimulusField('other', 0, 0, 2),))
    paired = tuple(_joint_segments(contract, seed, (*segments[:2], duplicate, *segments[2:])))
    assert any({f.endpoint for f in w.mutable_fields} == {'in', 'other'} for w in paired)


def test_fixed_marker_only_presentation_stays_in_native_prefix():
    contract = SimpleNamespace(inputs=lambda: (
        SimpleNamespace(name='header', fields=(SimpleNamespace(name='last'),),
                        framing=SimpleNamespace(fields=('last',))),
        SimpleNamespace(name='in', fields=(SimpleNamespace(name='a'),), framing=None)))
    seed = api.Stimulus({'header': [(1,)], 'in': [(0,), (0,)]}, [api.EventSlot()] * 5)
    segments = tuple(RTLScheduleSegment(str(i), seed.fingerprint, i+1, i+2, i,
        (StimulusField(endpoint, transaction, 0, i+1),))
        for i, (endpoint, transaction) in enumerate((('header', 0), ('in', 0), ('in', 1))))
    windows = tuple(_joint_segments(contract, seed, segments))
    assert len(windows) == 1
    assert (windows[0].start, windows[0].stop) == (2, 4)
    assert {f.endpoint for f in windows[0].mutable_fields} == {'in'}


@pytest.mark.cosim
@pytest.mark.solver
@pytest.mark.parametrize('depth', [3, 10, 20])
def test_native_search_reaches_state_requiring_joint_command_sequence(tmp_path, depth):
    # Every zero input returns to the initial state. No single-input mutation of
    # this seed can reach the target; the exact window must build the history.
    source = f'''module M(input clk,rst,iv,input [4:0] a,output ir,
      output reg ov,output reg y,input ordy);
      reg [4:0] state;
      assign ir=!rst && (!ov || ordy);
      always @(posedge clk) begin
        if(rst) begin state<=0; ov<=0; y<=0; end
        else begin
          if(ov && ordy) ov<=0;
          if(iv && ir) begin
            ov<=1;
            if(state=={depth} && a==31) y<=1; else y<=0;
            if(state<{depth} && a==state+1) state<=state+1; else state<=0;
          end
        end
      end
    endmodule'''
    path = tmp_path / 'fsm.sv'
    path.write_text(source)
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(5), 'a')],
                     api.ready_valid('in', 'iv', 'ir'), 'c', kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(1), 'y')],
                     api.ready_valid('out', 'ov', 'ordy'), 'c', kind='transaction')],
        [api.ClockDomain('c', 'clk', 'rst', reset_restores=True)])
    artifact = RTLArtifact(path, 'M', tmp_path / 'native')
    manifest = artifact.frontend_catalogue()
    matches = [g for g in manifest.values() if g.family == 'control-choice' and g.event == 'true'
               and f'state=={depth} && a==31' in source[g.where('start'):g.where('end')]]
    goal = min(matches, key=lambda g: g.where('end')-g.where('start')).id
    provider = RTLGenerationProvider.from_artifact(artifact, contract,
        api.GenerationSpec(depth+4, depth+1, 1, 2), manifest,
        targetable_goals=(goal,), workdir=tmp_path / 'query', timeout_ms=60_000)
    seed = api.Stimulus({'in': [(0,)] * (depth+1)}, [api.EventSlot(
        reset=i==0, offers=('in',) if 1 <= i <= depth+1 else (),
        controls={'rst': int(i==0), 'ordy': 1}) for i in range(depth+4)])
    # Related probes share the same scope as the public generation scheduler.
    # A standalone probe closes its session and releases native trace buffers.
    with provider.search_scope(probe_budget=2):
        selection = provider.select(seed)
        assert isinstance(selection, api.ProbeSelection), selection
        assert goal not in selection.replay.hits
        single = next(r for r in selection.requests
                      if len(r.mutable_fields) == 1 and r.mutable_fields[0].transaction == depth)
        local = provider.probe(seed, single)
        assert local.outcome is probing.Outcome.LOCAL_UNSAT, local.detail
        joint = max(selection.requests, key=lambda r: len(r.mutable_fields))
        assert len(joint.mutable_fields) == depth+1
        assert joint.rank < single.rank  # Widening is not left behind every local query.
        answer = provider.probe(seed, joint)
        assert answer.outcome is probing.Outcome.SAT, answer.detail
        assert dict(answer.candidate.queues)['in'] == tuple((a,) for a in (*range(1, depth+1), 31))
        assert answer.candidate.slots == seed.slots
        assert answer.candidate.initial_state == seed.initial_state
        confirmed = provider.select(answer.candidate)
        assert isinstance(confirmed, api.ProbeSelection), confirmed
        assert goal in confirmed.replay.hits
    if depth == 10:
        from behave.sim.generation import generate
        from behave.sim.search_policy import SearchBudget, SearchPolicy, search_scope

        # Also let the common scheduler choose among ALL automatic source
        # coverage goals. It gets neither a desired command sequence nor a
        # special hint naming the deep goal.
        automatic = RTLGenerationProvider.from_artifact(artifact, contract,
            api.GenerationSpec(depth+4, depth+1, 1, 2), manifest,
            targetable_goals=tuple(g.id for g in manifest.values() if g.cls == 'coverage'),
            workdir=tmp_path / 'automatic', timeout_ms=60_000)
        with search_scope(SearchBudget(SearchPolicy(time_budget_s=120)).direction(1)):
            searched = generate((seed,), automatic, contract=contract,
                generation_spec=api.GenerationSpec(depth+4, depth+1, 1, 2), probe_budget=None)
        assert searched.generated, searched.telemetry
        assert goal in searched.telemetry['coverage']['hits'], searched.telemetry
