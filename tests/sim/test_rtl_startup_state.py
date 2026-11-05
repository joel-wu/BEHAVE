"""Construction witnesses must reach HDL initial blocks, not overwrite their result."""

import json
import pickle
from dataclasses import replace

import pytest
import z3

from behave.sim import api
from behave.sim.core.record_value import thaw_record_value
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.initial_state import RTLInitialState
from behave.sim.rtl.native_observation import _read_intervals
from behave.sim.rtl.replay_build import replay_build_scope
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_task import SourceTaskRelation
from behave.sim.rtl.startup_state import RTLStartupState

from .test_rtl_source_history import _encode, _simple_contract
from .test_rtl_source_memory_relation import _prepare
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _names

ARTIFACT = "a" * 64


def test_startup_record_is_distinct_complete_and_immutable():
    registers = RTLInitialState(ARTIFACT, [["q", 2, 1]])
    pins = [["a", 2, 2], ["clk", 1, 0], ["iv", 1, 0], ["ordy", 1, 0], ["rst", 1, 0]]
    choice = RTLStartupState(registers, pins)
    contract = _simple_contract()
    choice.check_binding(ARTIFACT, ("q",), {"q": 2}, contract)
    assert RTLStartupState.from_dict(choice.as_dict()).as_dict() == choice.as_dict()
    assert pickle.loads(pickle.dumps(choice)).as_dict() == choice.as_dict()
    pins[0][2] = 0
    assert choice.pins.values()["a"] == 2
    with pytest.raises(AttributeError):
        choice.pins = None
    with pytest.raises(ValueError, match="disjoint"):
        RTLStartupState(registers, [["q", 2, 1]])
    with pytest.raises(api.Refused):
        RTLStartupState(registers, []).check_binding(ARTIFACT, ("q",), {"q": 2}, contract)
    with pytest.raises(api.Refused):
        choice.check_binding("b" * 64, ("q",), {"q": 2}, contract)


@pytest.mark.parametrize('mutation', ['missing', 'changed', 'boolean', 'unrequested'])
def test_native_startup_receipt_is_required_only_for_the_requested_choice(mutation):
    from .test_trace_postprocessing import fixture

    plan, stimulus, payload = fixture()
    plan = replace(plan, identity=ARTIFACT)
    choice = RTLStartupState(RTLInitialState(plan.identity, []), [['clk', 1, 0]])
    image = choice.as_dict()
    requested = replace(plan, startup_state=choice)
    payload['traces'][0]['startup_state'] = image
    assert type(requested.decode(stimulus, json.dumps(payload).encode(), {})) is not api.RunFailure
    if mutation == 'missing':
        del payload['traces'][0]['startup_state']
    elif mutation == 'changed':
        image['registers']['artifact'] = 'c' * 64
    elif mutation == 'boolean':
        image['pins'][0][2] = False  # Python equality alone conflates False and 0.
    else:
        requested = plan
    failure = requested.decode(stimulus, json.dumps(payload).encode(), {})
    assert type(failure) is api.RunFailure
    assert not failure.determines_score
    assert failure.kind == ('rtl-artifact-structural-record-malformed' if mutation == 'unrequested'
                            else 'rtl-startup-state-unconfirmed')


def test_startup_pins_include_memory_responses_but_never_dut_requests():
    from behave.sim.core.memory import MemoryInterface, MemoryPort

    base = _simple_contract()
    contract = api.TaskContract(base.endpoints, base.clocks,
        memories=[MemoryInterface('ram', 2, 8, 'c', [MemoryPort('rw')])])
    registers = RTLInitialState(ARTIFACT, [])
    pins = [('a', 2, 0), ('clk', 1, 0), ('iv', 1, 0), ('ordy', 1, 0),
            ('ram_req_ready', 1, 1), ('ram_resp_rdata', 8, 201), ('ram_resp_valid', 1, 1), ('rst', 1, 0)]
    RTLStartupState(registers, pins).check_binding(ARTIFACT, (), {}, contract)
    with pytest.raises(api.Refused):
        RTLStartupState(registers, sorted([*pins, ('ram_req_addr', 2, 0)])).check_binding(
            ARTIFACT, (), {}, contract)


SOURCE = '''module M(input clk,rst,iv,input [1:0] a,output ir,ov,
  output reg [1:0] y,input ordy);
  reg [1:0] saved, seen_saved, seen_a;
  assign ir=!rst && ordy; assign ov=iv;
  initial begin
    seen_saved=saved; seen_a=a;
    if(saved==3 && a==2) y=saved^a; else y=2;
  end
  always @(posedge clk) begin
    if(rst) begin saved<=0; y<=0; end
    else begin
      saved<=saved+1;
      if(saved==3) y<=a; else y<=2;
    end
  end
endmodule'''


@pytest.mark.cosim
@pytest.mark.eda('slang')
@pytest.mark.solver
def test_selected_startup_reads_match_native_state_and_every_source_hit(tmp_path):
    machine, plan, _env, template = _prepare(tmp_path, SOURCE, initialization=True)
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    contract, names = _simple_contract(), _names(machine)
    slots = [api.EventSlot(offers=('in',), controls={'rst': 0, 'ordy': 1}),
             api.EventSlot(reset=True, controls={'rst': 1, 'ordy': 1}),
             api.EventSlot(offers=('in',), controls={'rst': 0, 'ordy': 1})]
    values = [(3,), (1,)]
    stimulus = api.Stimulus({'in': values}, slots)
    widths = {'saved': 2, 'y': 2, 'seen_saved': 2, 'seen_a': 2}
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    selected_goal = _goal(SOURCE, plan, 'if(saved==3)')
    with replay_build_scope(artifact):
        for saved in (3, 1):
            pins = {'clk': 0, 'rst': 0, 'iv': 0, 'a': 2, 'ordy': 0}
            registers = {'saved': saved, 'y': 0, 'seen_saved': 0, 'seen_a': 0}
            choice = RTLStartupState(RTLInitialState.from_values(artifact.identity, widths, registers),
                [(name, 2 if name == 'a' else 1, value) for name, value in sorted(pins.items())])
            try:
                trace = artifact.exact_trace(contract, stimulus,
                    observe_names=tuple(widths), state_names=tuple(widths), state_widths=widths,
                    startup_state=choice)
            except api.Refused as exc:
                exc.add_note(json.dumps(exc.refusal.as_dict()))
                raise
            observed = {'seen_saved': saved, 'seen_a': 2}
            assert dict(trace.initial_state) == {'saved': saved, 'y': 1 if saved == 3 else 2, **observed}
            assert [dict(record.state) for record in trace.records] == [
                {'saved': (saved+1) % 4, 'y': 3 if saved == 3 else 2, **observed},
                {'saved': 0, 'y': 0, **observed}, {'saved': 1, 'y': 2, **observed}]
            raw = dict.fromkeys(machine.program.fields, 0)
            raw.update({names[name]: value for name, value in (pins | registers).items()})
            initial = relation.initial_state(raw)
            encoded = _encode(relation, initial.state, contract, slots, values, startup=True,
                              require_quiescence=False)
            native = list(_read_intervals(thaw_record_value(trace.source_evidence), stimulus,
                                         catalogue, expected_plan=None, prefix=False))
            assert any(selected_goal in observed.goals for _, observed in native) == (saved == 3)
            phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
            differences = [encoded.unfinished]
            for phase, (_, observed) in zip(phases, native, strict=True):
                differences.extend(term != (key in observed.goals) for key, term in phase.hits.items())
                differences.extend(term != (key in observed.occurrences)
                                   for key, term in phase.occurrences.items())
            for name, value in trace.initial_state:
                differences.append(encoded.startup.state[names[name]] != value)
            for i, record in enumerate(trace.records):
                differences.extend(encoded.source.slots[i].state[names[name]] != value
                                   for name, value in record.state)
            solver = z3.Solver()
            solver.add(*encoded.constraints)
            assert solver.check() == z3.sat
            solver.add(z3.Or(*differences))
            assert solver.check() == z3.unsat


@pytest.mark.cosim
@pytest.mark.eda('slang')
@pytest.mark.solver
def test_solver_selected_construction_witness_reaches_runtime_goal(tmp_path):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,ov,
      output reg [1:0] y,input ordy);
      reg [1:0] seed, saved;
      assign ir=!rst && ordy; assign ov=iv;
      initial saved=seed^a;
      always @(posedge clk) begin
        if(rst) begin saved<=0; y<=0; end
        else if(saved==1) y<=3; else y<=a;
      end
    endmodule'''
    machine, plan, _env, template = _prepare(tmp_path, source, initialization=True)
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    contract, names = _simple_contract(), _names(machine)
    goal = _goal(source, plan, 'if(saved==1)')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    raw = dict.fromkeys(machine.program.fields, 0)
    raw.update({names['seed']: None, names['a']: None})
    initial = relation.initial_state(raw)
    slots = [api.EventSlot(offers=('in',), controls={'rst': 0, 'ordy': 1})]
    values = [(0,)]
    encoded = _encode(relation, initial.state, contract, slots, values, startup=True,
                      require_quiescence=False)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat  # Feasibility is not assumed from UNSAT below.
    solver.push()
    solver.add(encoded.unfinished)
    assert solver.check() == z3.unsat  # Every admitted initial choice settles.
    solver.pop()
    solver.add(encoded.hits[goal])
    assert solver.check() == z3.sat
    model = solver.model()
    selected = initial.values(model)
    assert selected[names['seed']] ^ selected[names['a']] == 1
    assert selected[names['saved']] == 0  # Not the post-construction value.

    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    widths = {'seed': 2, 'saved': 2, 'y': 2}
    registers = RTLInitialState.from_values(artifact.identity, widths,
        {name: selected[names[name]] for name in widths})
    choice = RTLStartupState(registers,
        [(name, 2 if name == 'a' else 1, selected[names[name]])
         for name in ('a', 'clk', 'iv', 'ordy', 'rst')])
    stimulus = api.Stimulus({'in': values}, slots)
    trace = artifact.exact_trace(contract, stimulus, observe_names=tuple(widths),
        state_names=tuple(widths), state_widths=widths, startup_state=choice)
    assert dict(trace.initial_state)['saved'] == 1
    assert dict(trace.records[0].state)['y'] == 3
    native = list(_read_intervals(thaw_record_value(trace.source_evidence), stimulus,
                                 catalogue, expected_plan=None, prefix=False))
    assert any(goal in observed.goals for _, observed in native)
    phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
    for phase, (_, observed) in zip(phases, native, strict=True):
        assert z3.is_true(model.eval(phase.alive))
        for key, term in phase.hits.items():
            assert z3.is_true(model.eval(term)) == (key in observed.goals)
        for key, term in phase.occurrences.items():
            assert z3.is_true(model.eval(term)) == (key in observed.occurrences)
    for phase, state in ((encoded.startup, trace.initial_state),
                         (encoded.source.slots[0], trace.records[0].state)):
        for name, value in state:
            assert model.eval(phase.state[names[name]]).as_long() == value


@pytest.mark.cosim
def test_startup_adapter_roundtrips_wide_and_recursive_storage(tmp_path):
    source = '''module M(input clk,rst,iv,input [1:0] a,output ir,ov,
      output reg [1:0] y,input ordy);
      reg [69:0] wide; reg [7:0] mem[2:1][0:1];
      assign ir=1; assign ov=iv;
      initial y=(wide==70'h200000000000000013 && mem[2][1]==8'hf9) ? 3 : 0;
      always @(posedge clk) if(rst) begin
        wide<=0; mem[2][0]<=0; mem[2][1]<=0; mem[1][0]<=0; mem[1][1]<=0; y<=0;
      end else y<=a;
    endmodule'''
    rtl = tmp_path/'wide.sv'
    rtl.write_text(source)
    artifact = RTLArtifact(rtl, 'M', tmp_path/'native')
    registers = {'wide': int('200000000000000013', 16), 'y': 0,
                 'mem[0]': 8, 'mem[1]': 249, 'mem[2]': 128, 'mem[3]': 255}
    widths = {name: 70 if name == 'wide' else 2 if name == 'y' else 8 for name in registers}
    choice = RTLStartupState(RTLInitialState.from_values(artifact.identity, widths, registers),
        [('a', 2, 0), ('clk', 1, 0), ('iv', 1, 0), ('ordy', 1, 0), ('rst', 1, 0)])
    contract = _simple_contract()
    stimulus = api.Stimulus({'in': []}, [api.EventSlot(reset=True, controls={'rst': 1, 'ordy': 1})])
    trace = artifact.exact_trace(contract, stimulus, observe_names=('y',),
        state_names=tuple(widths), state_widths=widths, startup_state=choice)
    assert dict(trace.initial_state) == {**registers, 'y': 3}
    assert dict(trace.records[0].state) == dict.fromkeys(registers, 0)
