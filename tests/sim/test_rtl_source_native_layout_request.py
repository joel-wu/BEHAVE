"""Automatically derived hierarchical construction survives native replay."""

import pytest
import z3

from behave.sim import api
from behave.sim.symbolic.functional_array import FunctionalArray
from behave.sim.core.record_value import thaw_record_value
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.native_observation import _read_intervals
from behave.sim.rtl.protocol_relation import SymbolicRequest
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_handles import physical_name
from behave.sim.rtl.source_task import SourceTaskRelation
from behave.sim.rtl.source_values import ArrayType
from behave.sim.rtl.task_request import ReadyValidRequestCodec

from .test_rtl_source_history import _simple_contract
from .test_rtl_source_memory_relation import _prepare

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver, pytest.mark.cosim]

SOURCE = '''module Cell(input clk,rst,accept,input [1:0] a,output reg [1:0] q=3);
  reg [1:0] mem [2:1][-1:0];
  always @(posedge clk) begin
    if(rst) begin
      q<=0; mem[2][-1]<=0; mem[2][0]<=0; mem[1][-1]<=0; mem[1][0]<=0;
    end else if(accept) begin
      mem[2][-1]<=a; mem[1][0]<=mem[2][-1]; q<=mem[1][0];
    end
  end
endmodule
module M(input clk,rst,iv,input [1:0] a,output ir,
  output reg ov,output [1:0] y,input ordy);
  assign ir=!rst && (!ov || ordy);
  Cell child(clk,rst,iv && ir,a,y);
  always @(posedge clk) begin
    if(rst) ov<=0; else if(iv && ir) ov<=1; else if(ordy) ov<=0;
  end
endmodule'''


@pytest.mark.parametrize('construction', [False, 'constant', 'port', 'slice'])
@pytest.mark.parametrize('initial_clock', [0, 1])
def test_hierarchical_array_construction_and_native_whole_history_agree(tmp_path, construction, initial_clock):
    source = SOURCE
    if construction:
        value = 'a' if construction in ('port', 'slice') else '2'
        source = source.replace('always @(posedge clk) begin',
            f'initial begin mem[2][-1]={value}; mem[2][0]=1; mem[1][-1]=2; mem[1][0]=3; end\n'
            '  always @(posedge clk) begin', 1)
    contract = _simple_contract()
    if construction == 'slice':
        source = source.replace('module M(input clk,rst,iv,input [1:0] a',
                                'module M(input clk,rst,iv,input [3:0] a')
        source = source.replace('Cell child(clk,rst,iv && ir,a,y);',
                                'Cell child(clk,rst,iv && ir,a[3:2],y);')
        contract = api.TaskContract([api.Endpoint('in', 'in',
            [api.Field('a', api.FiniteType(4), 'a')], api.ready_valid('in', 'iv', 'ir'),
            'c', kind='transaction'), *contract.outputs()], contract.clocks)
    machine, plan, _, template = _prepare(tmp_path, source, initialization=True)
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact.identity)
    request = SymbolicRequest(ReadyValidRequestCodec(contract, 2, 1, initial=layout), 4)
    encoded = relation.encode_request(request, initial, binding)
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*encoded.constraints, request.lengths['in'] == 2)
    # Nonzero, nonuniform construction. HDL initial blocks may replace this
    # support before the first sampled source boundary; the receipt is earlier.
    pins = {'clk': initial_clock, 'rst': 0, 'iv': 0, 'a': 10 if construction == 'slice' else 2, 'ordy': 1}
    for coordinate, name, _ in layout.pin_coordinates:
        solver.add(binding[coordinate] == pins[name])
    for i, (coordinate, name, _) in enumerate(layout.coordinates):
        solver.add(binding[coordinate] == (3 if name == 'child.q' else i % 4 if name != 'ov' else 0))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    stimulus, startup = request.codec.decode(rows), layout.decode(rows)
    trace = artifact.exact_trace(contract, stimulus, observe_names=('y', 'ov', 'ir'),
        state_names=tuple(layout.widths), state_widths=dict(layout.widths), startup_state=startup)
    startup.check_trace(trace)
    native = list(_read_intervals(thaw_record_value(trace.source_evidence), stimulus,
                                  catalogue, expected_plan=None, prefix=False))
    phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
    differences = {'unfinished': encoded.unfinished}
    for i, (phase, (_, observed)) in enumerate(zip(phases, native, strict=True)):
        differences.update({f'phase[{i}].hit:{key}': term != (key in observed.goals)
                            for key, term in phase.hits.items()})
        differences.update({f'phase[{i}].occurrence:{key}': term != (key in observed.occurrences)
                            for key, term in phase.occurrences.items()})

    def leaves(path, kind, value):
        if isinstance(kind, ArrayType):
            direction = 1 if kind.left <= kind.right else -1
            for position in range(kind.cells):
                item = (value.select(z3.BitVecVal(position, 32))
                        if isinstance(value, FunctionalArray) else value[position])
                yield from leaves((*path, kind.left + direction * position), kind.element, item)
        else:
            yield physical_name(path), value

    def compare_state(label, state, recorded):
        recorded = dict(recorded)
        compared = set()
        for name, field in machine.program.fields.items():
            path = field.observation
            if path is None:
                continue
            path = (path,) if isinstance(path, str) else path
            for key, term in leaves(path, machine.program.kinds[name], state[name]):
                if key in recorded:
                    compared.add(key)
                    differences[f'{label}:{key}'] = term != recorded[key]
        assert compared == set(layout.widths)

    compare_state('startup', encoded.startup.state, trace.initial_state)
    assert len(trace.records) == len(rows)
    output = next(name for name, field in machine.program.fields.items() if field.port == 'y')
    for i, record in enumerate(trace.records):
        compare_state(f'slot[{i}]', encoded.source.slots[i].state, record.state)
        differences[f'slot[{i}].output'] = encoded.source.slots[i].phases[0].state[output] != dict(record.outputs)['y']
        differences[f'slot[{i}].accepted'] = encoded.protocol.accepted[i]['in'] != (dict(record.accepted).get('in') is not None)
        differences[f'slot[{i}].emitted'] = encoded.protocol.emitted[i]['out'] != (dict(record.accepted).get('out') is not None)
    solver.add(*request.fix(rows))
    solver.push()
    solver.add(z3.Not(z3.Or(*differences.values())))
    assert solver.check() == z3.sat  # The native history belongs to the relation.
    solver.pop()
    solver.add(z3.Or(*differences.values()))
    status = solver.check()
    if construction in ('port', 'slice'):
        # A collapsed input net is one source storage, even if the native
        # compiler keeps separate mirrors. Its startup choice must reach all
        # mirrors before any HDL initial block can read them.
        from behave.sim.core.audit import AuditCheckStatus

        from .test_rtl_source_confluence import _audit

        audit, calls = _audit(relation.compare_request(request))
        assert audit.status is AuditCheckStatus.AGREED, audit
        assert calls[-1] == 'confluence:product'
    assert status == z3.unsat, [key for key, term in differences.items()
                               if status == z3.sat and z3.is_true(solver.model().eval(term))]
    from behave.sim.symbolic.exact_solve import z3_check
    from behave.sim.core.audit import AuditCheckStatus

    comparison = relation.compare_native(request, rows, trace, catalogue)
    audit = comparison.audit(lambda phase, assertions: z3_check(assertions, 30_000))
    assert audit.status is AuditCheckStatus.AGREED, audit
