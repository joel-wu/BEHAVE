"""Compiler-owned construction choices, including physical hierarchy/arrays."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import z3

from behave.sim.core.refusal import Refused
from behave.sim.rtl.initial_state import RTLInitialState
from behave.sim.rtl.replay_startup import _physical_choice, decode_input_aliases
from behave.sim.rtl.source_handles import physical_name, state_paths

from .test_rtl_source_memory_relation import _prepare


def test_compiler_paths_preserve_hierarchy_native_overrides_and_array_order():
    fields = {('child', 'mem'): {'left': 2, 'right': 1, 'element':
              {'left': -1, 'right': 0, 'element': 3}}, 'q': 3,
              ('blk', 't'): 8, 'literal[1]': 7}
    paths = state_paths(['child.mem[2][-1]', 'child.mem[1][0]', 'child.mem[2]',
                         'child.mem[3][-1]', 'blk.t', 'q', 'literal[1]'], fields,
                        [(('blk', 't'), ('native_block', 't'))])
    assert paths == {'child.mem[2][-1]': ('child', 'mem', 2, -1),
                     'child.mem[1][0]': ('child', 'mem', 1, 0),
                     'child.mem[2]': ('child', 'mem', 1, -1),
                     'child.mem[3][-1]': None, 'blk.t': ('native_block', 't'),
                     'q': ('q',), 'literal[1]': ('literal[1]',)}
    assert physical_name(('gen', 2, 'child', 'mem', -1)) == 'gen[2].child.mem[-1]'
    assert state_paths(['blk.t'], fields, [(('blk', 't'), None)]) == {'blk.t': None}
    assert state_paths(['blk.t'], fields, [[['blk', 't'], ['real', 't']]]) == {'blk.t': ('real', 't')}
    assert state_paths(['child.q'], {('child', 'q'): 1, 'child.q': 1}) == {'child.q': None}


def test_native_deposit_uses_the_same_segmented_path_as_observation():
    choice = RTLInitialState.from_values('a'*64, {'state': 3}, {'state': 5})
    physical = _physical_choice(choice, {'state': range(3)}, {'state': ('child', 'mem', -1)})
    assert physical.fields == (('child.mem[-1]', 3, 5),)


@pytest.mark.parametrize('rows', [False, [0], [[['u','a'], 2, 'a']],
    [[[], 2, 'a', 0]], [[['u', False], 2, 'a', 0]], [[['u','a'], True, 'a', 0]],
    [[['u','a'], 2, '', 0]], [[['u','a'], 2, 'a', -1]],
    [[['u','a'], 2, 'a', 0], [['u','a'], 2, 'b', 0]]])
def test_startup_alias_metadata_is_closed(rows):
    with pytest.raises(ValueError):
        decode_input_aliases(rows)


def test_startup_alias_metadata_distinguishes_unavailable_from_empty():
    assert decode_input_aliases(None) is None
    assert decode_input_aliases([]) == ()
    assert decode_input_aliases([[['u', 1, 'a'], 2, 'a', 3]]) == ((('u', 1, 'a'), 2, 'a', 3),)


@pytest.mark.parametrize('fault', [None, 'alias', 'pin', 'width', 'late', 'unknown-alias'])
def test_passive_startup_capture_reads_actual_storage_without_writes(monkeypatch, fault):
    from cocotb import utils
    from behave.sim.rtl import register_init
    from behave.sim.rtl.initial_request import InitialStateCoordinates
    from behave.sim.rtl.replay_startup import capture_startup_state

    class Handle:
        def __init__(self, width, value):
            self.width, self._value = width, value
        def __len__(self):
            return self.width
        @property
        def value(self):
            return self._value
        @value.setter
        def value(self, _value):
            pytest.fail('passive capture wrote a simulator pin')

    class DUT(dict):
        _name = 'M'

    layout = InitialStateCoordinates('a'*64, {'saved': 3}, startup_pins={'a': 3})
    dut = DUT(a=Handle(2 if fault == 'width' else 3, 4 if fault == 'pin' else 5))
    calls = []
    def read(top, shape, *, inputs=False, aliases=False):
        assert top == 'M'
        calls.append((shape.fields, inputs, aliases))
        return ({'a': 5} if inputs else {'child.a': 0 if fault == 'alias' else 2}
                if aliases else {'child.saved': 6})
    monkeypatch.setattr(utils, 'get_sim_time', lambda **kw: int(fault == 'late'))
    monkeypatch.setattr(register_init, 'read_registers', read)
    monkeypatch.setattr(register_init, 'deposit_registers',
                        lambda *a, **kw: pytest.fail('passive capture deposited storage'))
    arguments = dict(paths={'saved': ('child', 'saved')},
                     input_aliases=None if fault == 'unknown-alias' else [(('child', 'a'), 2, 'a', 1)])
    if fault:
        with pytest.raises(Refused):
            capture_startup_state(layout, dut, {'saved': Handle(3, 0)}, **arguments)
        if fault == 'late':
            assert not calls
    else:
        got = capture_startup_state(layout, dut, {'saved': Handle(3, 0)}, **arguments)
        assert got.registers.values() == {'saved': 6}  # Not the stale VPI mirror or shape's zero.
        assert got.pins.values() == {'a': 5}
        assert [row[1:] for row in calls] == [(False, False), (True, False), (False, True)]


@pytest.mark.cosim
@pytest.mark.solver
@pytest.mark.eda('slang')
@pytest.mark.parametrize('mode', ['scalar', 'declaration', 'two-state', 'initial-block',
                                  'hierarchy', 'falling', 'release-edge'])
def test_native_passive_capture_does_not_change_the_baseline(tmp_path, monkeypatch, record_property, mode):
    import json
    import shutil
    from behave.sim import api, harness
    from behave.sim.rtl import tool_isolation
    from behave.sim.rtl.tool_protocol import command_stream
    from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract
    from .test_rtl_source_worker_task import _worker
    from .test_rtl_source_native_layout_request import SOURCE as HIERARCHY

    source = HIERARCHY if mode == 'hierarchy' else HISTORY_SOURCE
    if mode == 'declaration':
        source = source.replace('reg [1:0] saved;', 'reg [1:0] saved=3;')
    if mode == 'two-state':
        source = source.replace('reg [1:0] saved;', 'bit [1:0] saved=3;')
    if mode == 'initial-block':
        source = source.replace('reg [1:0] saved;', 'reg [1:0] saved; initial saved=2;')
    # This fixture cancels outstanding work on reset. The symbolic request
    # can reset after accepting input, so declare that existing RTL behavior.
    contract = _simple_contract(reset_policy=api.ResetPolicy.CLEARS)
    if mode in ('falling', 'release-edge'):
        source = source.replace('posedge clk', 'negedge clk').replace('!rst &&', 'rst &&').replace(
            'if(rst)', 'if(!rst)')
        image = contract.as_dict()
        image['clocks'][0].update(edge='falling', reset_active_low=True)
        contract = api.TaskContract.from_dict(image)
    if mode == 'release-edge':
        source = source.replace('reg [1:0] saved;',
            'reg [1:0] saved; reg release_seen=0;\n'
            'always @(posedge clk) release_seen<=1;')
    schedule = None
    if mode in ('falling', 'release-edge'):
        from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
        schedule = GlobalSchedule([ClockTiming('c', 2, clock='clk', reset='rst',
                                               edge='falling', reset_active_low=True)],
            [GlobalEventSlot(2*i, event=api.EventSlot(ticks=('c',), resets=('c',) if i == 0 else (),
                offers=('in',) if i == 1 else (), controls={'rst':int(i != 0), 'ordy':1}))
             for i in range(5)])
    context, prepared, _relation, _request, _encoded = _worker(tmp_path, source, contract, 5, schedule=schedule)
    artifact, layout = context.artifact, prepared.initial_layout
    # The worker's finite domain declares one reset slot (GenerationSpec),
    # independent of the public helper's default reset length.
    stimulus = api.reset_then(contract, [(1,)], controls={'ordy': 1}, drain=2,
                              reset_hold_cycles=1)
    if schedule is not None:
        from behave.sim.rtl.exact_product import stimulus_for_global_schedule
        stimulus = stimulus_for_global_schedule(schedule, {'in': ((1,),)},
                                                  source='passive startup audit')
    options = dict(observe_names=tuple(layout.widths), state_names=tuple(layout.widths),
                   state_widths=dict(layout.widths))
    ordinary = artifact.traces_per_process(contract, [stimulus], workers=1, **options)[0]
    assert type(ordinary) is api.AuditedTrace, ordinary.as_dict()
    make, render, execute = harness.generate_makefile, harness.render_template, tool_isolation.run_workspace
    captured = []

    def with_adapter(where, *args, **kwargs):
        # Link the same trusted adapter for readback, without asking replay to
        # install a choice. Both runs remain ordinary concrete replay.
        shutil.copyfile(harness.TEMPLATE_DIR/'initial_state.cpp.tmpl', where.parent/'initial_state.cpp')
        return make(where, *args, **{**kwargs, 'initial_state': True})

    def with_probe(name, **kwargs):
        original = render(name, **kwargs)
        if name != 'driver_tb.py.tmpl':
            return original
        return original + f'''
import json
import pickle
from behave.sim.rtl import register_init
from behave.sim.rtl.initial_request import InitialStateCoordinates
from behave.sim.rtl.replay_startup import capture_startup_state
from behave.sim.rtl.source_handles import path_handle
from behave.sim.core.refusal import Refused
_original_replay = replay
async def replay(dut, directory, *, period_ns):
    plan = pickle.loads((directory / 'plan.pickle').read_bytes())
    paths = dict(plan['state_paths'])
    layout = InitialStateCoordinates({artifact.identity!r}, {dict(layout.widths)!r},
                                     startup_pins={dict(layout.startup_pins)!r})
    def no_write(*args, **kwargs):
        raise AssertionError('capture must not deposit storage')
    register_init.deposit_registers = no_write
    try:
        receipt = capture_startup_state(layout, dut,
            {{name: path_handle(dut, path) for name, path in paths.items()}}, paths=paths,
            input_aliases={artifact._observation.native.startup_aliases!r}).as_dict()
    except Refused as exc:
        receipt = {{'refusal': exc.refusal.as_dict()}}
    print('PASSIVE_STARTUP=' + json.dumps(receipt), flush=True)
    await _original_replay(dut, directory, period_ns=period_ns)
'''

    def with_log(*args, **kwargs):
        result = execute(*args, **kwargs)
        if any(command.identifier == 'verilator-simulate' for group in args[1] for command in group.commands):
            for line in command_stream(result, 'verilator-simulate', 'stdout').decode().splitlines():
                if line.startswith('PASSIVE_STARTUP='):
                    captured.append(json.loads(line.split('=', 1)[1]))
        return result

    monkeypatch.setattr(harness, 'generate_makefile', with_adapter)
    monkeypatch.setattr(harness, 'render_template', with_probe)
    monkeypatch.setattr(tool_isolation, 'run_workspace', with_log)
    observed = artifact.traces_per_process(contract, [stimulus], workers=1, **options)[0]
    assert type(observed) is api.AuditedTrace, observed.as_dict()
    assert observed.as_dict() == ordinary.as_dict()
    assert len(captured) == 1
    record_property('capture', json.dumps(captured[0], sort_keys=True))
    record_property('settled_state', json.dumps(dict(observed.initial_state), sort_keys=True))
    assert 'refusal' not in captured[0], captured[0]
    from behave.sim.rtl.startup_state import RTLStartupState
    receipt = RTLStartupState.from_dict(captured[0])
    receipt.check_binding(artifact.identity, tuple(layout.widths), dict(layout.widths), contract)
    assert set(receipt.registers.values()) == set(layout.widths)
    if mode == 'release-edge':
        # Parking a falling-edge clock is itself a rising edge, before slot 0.
        # The receipt must not replace the actual pre-HDL zero with its successor.
        assert receipt.registers.values()['release_seen'] == 0
        assert dict(observed.initial_state)['release_seen'] == 1
    if mode != 'hierarchy':
        from .test_rtl_source_native_audit import _audit

        assert receipt.registers.values()['saved'] == 0
        assert dict(observed.initial_state)['saved'] == (
            2 if mode == 'initial-block' else 3 if mode in ('declaration', 'two-state') else 0)
        # Use only the actually read receipt. This test wires the retained
        # diagnostic into the existing record audit, never a guessed initial
        # value or a copy of the independent replay's later state.
        raw = api.AuditedTrace.from_dict({**observed.as_dict(), 'initialization': captured[0]})
        relation, request, encoded = prepared.bind_request(len(stimulus.slots))
        rows = request.codec.encode(stimulus, initial_state=receipt)
        audited, phases = _audit(relation.compare_native(request, rows, raw, context.frontend_catalogue,
                                                        observation=prepared.observation))
        assert audited.status.value == 'agreed', audited.as_dict()
        assert phases == ['feasibility', 'completeness', 'definedness', 'memory', 'membership', 'agreement']
        record_property('native_audit', audited.status.value)
        if mode == 'release-edge':
            from behave.sim.rtl.observation_audit import compare_native_history

            # Omitting the release interval (the old model) must fail even when
            # all later slots and their final outputs remain unchanged.
            omitted = replace(encoded, startup=encoded.startup.phases[0])
            rejected, _ = _audit(compare_native_history(relation, request, omitted,
                rows, raw, context.frontend_catalogue, observation=prepared.observation))
            assert rejected.status.value == 'inconsistent', rejected.as_dict()
            record_property('omitted_startup_release', rejected.status.value)
        # The production record auditor consumes the original passive receipt.
        # It must not require another replay of an already completed baseline.
        monkeypatch.setattr(harness, 'generate_makefile', make)
        monkeypatch.setattr(harness, 'render_template', render)
        monkeypatch.setattr(tool_isolation, 'run_workspace', execute)
        from behave.sim.rtl.analysis_job import decode_analysis_reply
        from behave.sim.worker import run_job_strict

        admitted = api.admit(contract, api.realization_of_rtl_source(
            source, 'passive startup record audit', top='M', contract=contract))
        reply = run_job_strict('rtl_exact_analysis', {
            **context.source_payload(), 'horizon': len(stimulus.slots),
            'admission': admitted.as_dict(), 'admission_digest': admitted.handoff_identity,
            'operation': 'record-audit', 'stimulus': stimulus.as_dict(),
            'raw_trace': raw.as_dict(),
        }, timeout_s=120)
        record = decode_analysis_reply(reply, 'record-audit')
        assert not isinstance(record, api.Refusal), record.as_dict()
        assert record['disposition'] == 'admitted', record
        record_property('isolated_record_audit', record['disposition'])


SOURCE = '''module Cell(input clk,input [2:0] a,output reg [2:0] q=3);
  reg [2:0] mem [2:1][-1:0];
  always @(posedge clk) begin
    mem[2][-1]<=a; mem[1][0]<=mem[2][-1]; q<=mem[1][0];
  end
endmodule
module M(input clk,input [2:0] a,output wire [2:0] y);
  Cell child(clk,a,y);
endmodule'''


@pytest.mark.eda('slang')
@pytest.mark.solver
def test_layout_is_derived_from_compiler_storage_not_a_flat_register_list(tmp_path):
    machine, _, _, template = _prepare(tmp_path, SOURCE, initialization=True)
    initial = template.relation(machine, (), complete_state=True).initial_state(
        machine, dict.fromkeys(machine.program.fields), declarations=True)
    layout, binding = initial.request_layout(machine.program, 'a'*64)
    assert dict(layout.widths) == {name: 3 for name in
        ('child.q', 'child.mem[2][-1]', 'child.mem[2][0]', 'child.mem[1][-1]', 'child.mem[1][0]')}
    assert dict(layout.startup_pins) == {'clk': 1, 'a': 3}
    assert set(binding) == set(layout.bounds)
    q = next(name for name, field in machine.program.fields.items() if field.name == 'q')
    assert not z3.is_bv_value(binding['initial.rtl.child.q'])
    assert z3.simplify(initial.state[q]).as_long() == 3
    mem = next(name for name, field in machine.program.fields.items() if field.name == 'mem')
    assert binding['initial.rtl.child.mem[2][-1]'] is initial.bits[mem][0][0]
    assert binding['initial.rtl.child.mem[1][0]'] is initial.bits[mem][1][1]
    # Read-only nets and continuous port transfers are retained in the model;
    # they cannot be deposited using the native writable-register interface.
    assert len(initial.bits) > len(layout.startup_pins) + 2


@pytest.mark.eda('slang')
@pytest.mark.solver
def test_layout_keeps_constants_and_private_state_and_checks_cancellation(tmp_path):
    source = '''module M(input clk,input [2:0] a,output logic [2:0] y);
      bit [2:0] saved; logic [2:0] derived;
      assign derived=a; always @(posedge clk) begin saved<=a; y<=saved+derived; end
    endmodule'''
    machine, _, _, template = _prepare(tmp_path, source, initialization=True)
    initial = template.relation(machine, (), complete_state=True).initial_state(
        machine, dict.fromkeys(machine.program.fields), declarations=True)
    layout, binding = initial.request_layout(machine.program, 'a'*64)
    assert dict(layout.widths) == {'saved': 3, 'y': 3}
    assert z3.simplify(binding['initial.rtl.saved']).as_long() == 0
    derived = next(name for name, field in machine.program.fields.items() if field.name == 'derived')
    assert z3.is_const(initial.bits[derived]) and not z3.is_bv_value(initial.bits[derived])
    assert initial.bits.keys() == machine.program.fields.keys()
    with pytest.raises(ValueError, match='every compiler'):
        replace(initial, bits={}).request_layout(machine.program, 'a'*64)
    def stop():
        raise TimeoutError('cancelled')
    with pytest.raises(TimeoutError, match='cancelled'):
        initial.request_layout(machine.program, 'a'*64, check=stop)
    field = next(name for name, field in machine.program.fields.items() if field.name == 'saved')
    program = replace(machine.program, fields={**machine.program.fields,
                      field: replace(machine.program.fields[field], observation=None)})
    with pytest.raises(Refused, match='native path'):
        initial.request_layout(program, 'a'*64)


def test_colliding_compiler_paths_cannot_share_a_request_coordinate():
    from behave.sim.rtl.source_initial import SourceInitialState
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_storage import SourceStorage

    fields = {'s0': SourceStorage(('x',), 'q', False, observation=('child', 'q')),
              's1': SourceStorage(('y',), 'child.q', False, observation='child.q')}
    program = SimpleNamespace(fields=fields, bindings=fields, processes=(),
        kinds=dict.fromkeys(fields, IntegralType(3, False)),
        compiler=SimpleNamespace(symbols={key: {'kind': 'Variable'} for key in fields}))
    initial = SourceInitialState({key: z3.BitVec(key, 3) for key in fields}, {})
    with pytest.raises(Refused, match='share one native'):
        initial.request_layout(program, 'a'*64)
