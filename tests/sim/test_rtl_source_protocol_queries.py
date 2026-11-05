"""Protocol searches reuse typed source intervals and actual held-output state."""

from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.engines import probing
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.generation import stimulus_bytes
from behave.sim.rtl.artifact import RTLArtifact

GOAL = 'monitor.sink-stability'
pytestmark = [pytest.mark.cosim, pytest.mark.solver]


def _contract():
    return api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', api.FiniteType(2), 'a')],
                     api.ready_valid('in', 'iv', 'ir'), 'c', kind='transaction'),
        api.Endpoint('out', 'out', [api.Field('y', api.FiniteType(2), 'y')],
                     api.ready_valid('out', 'ov', 'ordy'), 'c', kind='transaction')],
        [api.ClockDomain('c', 'clk', 'rst', reset_restores=False)])


def _boundary():
    from behave.sim.core.stimulus import SlotRecord
    from behave.sim.engines.rtl.source_boundary import SourceTraceBindings

    owner = object.__new__(SourceTraceBindings)
    owner.contract, owner.atomic = _contract(), False
    owner.seed = api.Stimulus({'in': [(0,), (0,)]}, [api.EventSlot(reset=i in (0, 3),
        offers=('in',) if i in (1, 2) else (), controls={'ordy': 0}) for i in range(4)])
    owner.trace = SimpleNamespace(records=[SlotRecord(outputs={'ov': int(i in (1, 2)), 'y': 0},
        accepted={'in': (0,)} if i in (1, 2) else {}) for i in range(4)])
    owner.ports = {'ordy': 'ready'}
    owner.pins = ({'ready': 0},) * 4
    owner._monitors, owner._monitor_prefix = None, None
    return owner


def test_monitor_boundary_keeps_prefix_reset_and_cancellation_atomic():
    owner = _boundary()
    held = owner.monitor_boundary(2, check=lambda: None)
    assert held.protocol['c', 'out'].stalled
    assert held.protocol['c', 'out'].held == (0,)
    previous = owner._monitor_prefix
    calls = 0

    def stop():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError('cancelled prefix')

    with pytest.raises(TimeoutError):
        owner.monitor_boundary(4, check=stop)
    assert owner._monitor_prefix is previous
    reset = owner.monitor_boundary(4, check=lambda: None)
    assert not reset.protocol['c', 'out'].stalled
    assert owner.monitor_boundary(2, check=lambda: None) == held


@pytest.mark.parametrize('pin,value', [('ov', None), ('ov', 2), ('y', None), ('y', -1), ('y', 4)])
def test_monitor_boundary_cannot_coerce_unknown_or_out_of_range_pins(pin, value):
    from behave.sim.core.stimulus import SlotRecord

    owner = _boundary()
    original = owner.trace.records[1]
    owner.trace.records[1] = SlotRecord(outputs={**dict(original.outputs), pin: value},
                                       accepted=original.accepted)
    with pytest.raises(api.Refused, match='prefix-unreadable'):
        owner.monitor_boundary(2, check=lambda: None)
    assert owner._monitor_prefix is None


@pytest.mark.parametrize('stable', [False, True])
def test_protocol_query_keeps_stall_from_native_prefix_without_circuit_frontend(tmp_path, stable):
    source = tmp_path / 'dut.sv'
    source.write_text('''module M(input clk,rst,iv,input [1:0] a,output ir,ov,
        output [1:0] y,input ordy);
        assign ir=!rst; assign ov=iv && !rst;
        assign y=''' + ("2'b00" if stable else 'a') + '; endmodule')
    contract = _contract()
    artifact = RTLArtifact(source, 'M', tmp_path / 'native')
    provider = RTLGenerationProvider.from_artifact(artifact, contract,
        api.GenerationSpec(5, 2, 1, 2), artifact.frontend_catalogue(),
        targetable_goals=(GOAL,), workdir=tmp_path / 'query', timeout_ms=60_000)
    seed = api.Stimulus({'in': [(0,), (0,)]}, [api.EventSlot(
        reset=i == 0, offers=('in',) if i in (1, 2) else (),
        controls={'rst': int(i == 0), 'ordy': int(i >= 2)}) for i in range(5)])
    selected = provider.select(seed)
    assert isinstance(selected, api.ProbeSelection), selected
    assert GOAL not in selected.replay.hits
    request = next(r for r in selected.requests if r.mutable_fields[0].transaction == 1)
    segment = provider._selected[stimulus_bytes(seed)][request.occurrence][0]
    assert segment.start == 2

    answer = provider.probe(seed, request)
    if stable:
        assert answer.outcome is probing.Outcome.LOCAL_UNSAT, answer.detail
        assert answer.candidate is None
    else:
        assert answer.outcome is probing.Outcome.SAT, answer.detail
        assert answer.goal == GOAL
        assert dict(answer.candidate.queues)['in'][0] == (0,)
        assert dict(answer.candidate.queues)['in'][1] != (0,)
        assert answer.candidate.slots == seed.slots
        confirmed = provider.select(answer.candidate)
        assert isinstance(confirmed, api.ProbeSelection), confirmed
        assert GOAL in confirmed.replay.hits
