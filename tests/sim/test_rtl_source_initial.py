"""Whole-domain source queries must retain physical initial support and queue sizes."""

import pytest
import z3

from behave.sim import api
from behave.sim.symbolic import interp, ranges
from behave.sim.symbolic.step_template import _leaves
from behave.sim.core.memory import MemoryEnvironmentState
from behave.sim.rtl.protocol_relation import ProtocolBoundary
from behave.sim.rtl.source_relation import SourceIntervalTemplate
from behave.sim.rtl.source_task import SourceTaskRelation

from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract
from .test_rtl_source_memory_relation import _encoded_state, _prepare
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _names, _settle, _state, _step

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('count', [1, 128])
def test_fixed_initial_image_batches_all_cells_without_changing_free_support(private, count, monkeypatch):
    from behave.sim.symbolic.functional_array import FunctionalArray
    from behave.sim.rtl.source_initial import SourceInitialState

    context = z3.Context() if private else None
    cells = [z3.BitVec(f'physical_{i}', 8, ctx=context) for i in range(count)]
    free = z3.BitVec('private_unbound', 8, ctx=context)
    carriers = [z3.SignExt(8, cell) for cell in cells]
    initial = SourceInitialState({'memory': [cells], 'private': free},
        {'memory': [FunctionalArray.from_cells(carriers)], 'private': z3.SignExt(8, free)})
    bindings = tuple((cell, z3.BitVecVal((i * 31 + 128) % 256, 8, ctx=context))
                     for i, cell in enumerate(cells))
    expected = [z3.simplify(z3.substitute(term, *bindings)) for term in carriers]
    original, calls = z3.substitute, []

    def substitute(term, *pairs):
        calls.append(len(pairs))
        return original(term, *pairs)

    monkeypatch.setattr(z3, 'substitute', substitute)
    fixed = initial.fixed(bindings)
    assert calls == [count]
    assert isinstance(fixed.state['memory'][0], FunctionalArray)
    assert all(value.eq(pair[1]) for value, pair in zip(fixed.bits['memory'][0], bindings, strict=True))
    for index, want in enumerate(expected):
        got = fixed.state['memory'][0].select(z3.BitVecVal(index, 32, ctx=context))
        assert z3.simplify(got).eq(want)
    assert fixed.bits['private'].eq(free)
    assert fixed.state['private'].eq(z3.simplify(initial.state['private']))
    # Rewriting a member must not mutate the shared constructor.
    assert all(left.eq(right) for left, right in zip(initial.bits['memory'][0], cells, strict=True))

SOURCE = '''module M(input clk,input signed [2:0] a,
  output reg signed [2:0] q);
  reg signed [2:0] mem [2:1];
  always @(posedge clk) begin
    mem[2]<=a; mem[1]<=mem[2]; q<=mem[1];
  end
endmodule'''


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('recursive_array', [False, True])
def test_all_physical_patterns_have_the_same_startup_as_native_boundaries(
        tmp_path, private_context, recursive_array):
    source = (SOURCE.replace('mem [2:1]', 'mem [2:1][0:1]')
              .replace('mem[2]<=a; mem[1]<=mem[2]; q<=mem[1];',
                       'mem[2][0]<=a; mem[1][1]<=mem[2][0]; q<=mem[1][0];')
              if recursive_array else SOURCE)
    machine, _, env, template = _prepare(tmp_path, source)
    names = _names(machine)
    # A wider proved implementation carrier must not widen physical support.
    env = {**env, names['q']: ranges.Range(-8, 7)}
    template = SourceIntervalTemplate(machine.procedure, env)
    context = z3.Context() if private_context else None
    relation = template.relation(machine, (), context=context, complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields))
    assert set(initial.state) == set(machine.procedure.state)
    assert all(term.size() == 3 for term in _leaves(initial.bits[names['mem']]))
    assert len(initial.bits[names['mem']]) == 2
    solver = z3.Solver(ctx=context)
    for pattern in range(8):
        # Every signed native pattern, nonuniform arrays and both clock levels.
        raw = {names['clk']: pattern % 2, names['a']: pattern,
               names['q']: 7-pattern, names['mem']: [pattern, 7-pattern]}
        if recursive_array:
            raw[names['mem']] = [raw[names['mem']], [7-pattern, pattern]]
        solver.push()
        for name, value in raw.items():
            solver.add(*(term == bits for term, bits in zip(
                _leaves(initial.bits[name]), _leaves(value), strict=True)))
        assert solver.check() == z3.sat
        assert initial.values(solver.model()) == raw
        concrete = {name: machine.program.kinds[name].decode(value) for name, value in raw.items()}
        expected = _encoded_state(machine, env, machine.boundary(concrete, startup=True))
        differences = [term != (want if want.ctx == term.ctx else want.translate(term.ctx)) for name in expected
                       for term, want in zip(_leaves(initial.state[name]), _leaves(expected[name]), strict=True)]
        solver.add(z3.Or(*differences))
        assert solver.check() == z3.unsat
        solver.pop()
    # An implementation carrier is wider than its physical type; extra values
    # cannot leak into the initial domain, even without any range assumptions.
    q = initial.state[names['q']]
    assert q.size() > 3
    solver.add(z3.Or(q < -4, q > 3))
    assert solver.check() == z3.unsat


def test_initial_support_requires_explicit_fields_and_native_shapes(tmp_path):
    machine, _, _, template = _prepare(tmp_path, SOURCE)
    relation = template.relation(machine, (), complete_state=True)
    names = _names(machine)
    values = dict.fromkeys(machine.program.fields)
    with pytest.raises(ValueError, match='every physical field'):
        relation.initial_state(machine, {})
    for invalid in (-1, 8, True):
        with pytest.raises(ValueError, match='every observed bit'):
            relation.initial_state(machine, {**values, names['q']: invalid})
    with pytest.raises(ValueError, match='complete compiler array shape'):
        relation.initial_state(machine, {**values, names['mem']: [0]})
    initial = relation.initial_state(machine, {**values, names['mem']: [None, 7]})
    assert z3.is_bv_value(initial.bits[names['mem']][1])
    assert initial.bits[names['mem']][1].as_long() == 7


@pytest.mark.parametrize('private_context', [False, True])
def test_declaration_initializers_bind_ports_signed_arrays_and_two_state_defaults(tmp_path, private_context):
    source = '''module M(input clk,input [3:0] a,output logic [3:0] q=4'h9);
      logic signed [3:0] r=-2;
      logic [3:0] mem[2:1][0:1]='{'{4'd3,4'd5},'{4'd8,4'd10}};
      bit [3:0] z;
      logic [3:0] free_value;
      always @(posedge clk) begin
        q<=r+mem[2][0]+mem[1][1]+z+free_value; r<=a;
      end
    endmodule'''
    machine, _, _, template = _prepare(tmp_path, source)
    names = _names(machine)
    relation = template.relation(machine, (), complete_state=True,
                                 context=z3.Context() if private_context else None)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    solver = z3.Solver(ctx=relation.source.context)
    expected = {'q': 9, 'r': 14, 'mem': [[3, 5], [8, 10]], 'z': 0}
    for free_value in range(16):
        solver.push()
        solver.add(initial.bits[names['free_value']] == free_value)
        assert solver.check() == z3.sat
        for name, want in expected.items():
            assert [solver.model().eval(term).as_long() & 15
                    for term in _leaves(initial.state[names[name]])] == list(_leaves(want))
        assert solver.model().eval(initial.state[names['r']]).as_long() == (
            (1 << initial.state[names['r']].size()) - 2)
        solver.pop()
    pre_hdl = relation.initial_state(machine,
        {**dict.fromkeys(machine.program.fields), names['q']: 0}, declarations=True)
    assert pre_hdl.bits[names['q']].as_long() == 0
    assert z3.simplify(pre_hdl.state[names['q']]).as_long() == 9
    with pytest.raises(api.Refused, match='initial-support-conflict'):
        relation.initial_state(machine,
            {**dict.fromkeys(machine.program.fields), names['z']: 1}, declarations=True)


def test_initial_values_follow_elaborated_owners_not_field_names(tmp_path):
    source = '''module Child #(parameter V=1)(input clk,input [3:0] a,output logic [3:0] q=V);
      always @(posedge clk) q<=a;
    endmodule
    module M(input clk,input [3:0] a,output [3:0] x,y);
      Child #(.V(3)) left(clk,a,x);
      Child #(.V(11)) right(clk,a,y);
      if(0) begin initial $fatal(1); end
    endmodule'''
    machine, _, _, template = _prepare(tmp_path, source)
    relation = template.relation(machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    solver = z3.Solver()
    assert solver.check() == z3.sat
    children = {field.observation: solver.model().eval(initial.state[name]).as_long()
                for name, field in machine.program.fields.items()
                if field.name == 'q'}
    assert children == {('left', 'q'): 3, ('right', 'q'): 11}
    # Parent nets are continuous propagation, not aliases of child variables.
    assert all(not z3.is_bv_value(initial.bits[name]) for name, field in machine.program.fields.items()
               if field.name in {'x', 'y'})


@pytest.mark.parametrize('through_parameter', [False, True])
def test_recursive_two_state_defaults_and_constant_function_initialization(tmp_path, through_parameter):
    source = '''module M(input clk,input [3:0] a,output reg [3:0] y);
      typedef bit signed [2:0] Small;
      Small memory[1:0][2:3];
      function automatic [3:0] value(input [3:0] v); return v^4'ha; endfunction
      logic [3:0] q=value(3);
      always @(posedge clk) begin y<=q+memory[0][3]; q<=a; end
    endmodule'''
    if through_parameter:
        source = source.replace('logic [3:0] q=value(3);',
                                'localparam [3:0] Q=value(3); logic [3:0] q=Q;')
    machine, _, _, template = _prepare(tmp_path, source)
    names = _names(machine)
    relation = template.relation(machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    solver = z3.Solver()
    assert solver.check() == z3.sat
    values = initial.values(solver.model())
    assert values[names['memory']] == [[0, 0], [0, 0]]
    assert solver.model().eval(initial.state[names['q']]).as_long() == 9


@pytest.mark.parametrize('declarations,expected', [
    ("logic signed [3:0] q=$signed(4'b1110)+1;", 15),
    ("logic [3:0] q=(4'd15+4'd2)>>1;", 0),
    ('''function automatic [3:0] choose(input [3:0] v);
          if(v[0]) return v+3; else return v-1;
        endfunction
        logic [3:0] q=choose(3);''', 6),
])
def test_closed_initializer_proofs_preserve_typed_operations(tmp_path, declarations, expected):
    source = f'''module M(input clk,input [3:0] a,output [3:0] y);
      {declarations}
      always @(posedge clk) q<=a;
      assign y=q;
    endmodule'''
    machine, _, _, template = _prepare(tmp_path, source)
    relation = template.relation(machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    assert not z3.is_bv_value(initial.bits[_names(machine)['q']])
    assert z3.simplify(initial.state[_names(machine)['q']]).as_long() == expected


@pytest.mark.parametrize('declaration,code', [
    ('logic [3:0] q=a;', 'rtl-source-initializer-unencoded'),
    ('''logic [3:0] g=0;
        function automatic [3:0] value(); g=g+1; return 9; endfunction
        logic [3:0] q=value();''', 'rtl-source-initializer-unencoded'),
    ("logic [3:0] q=4'bx;", 'rtl-source-ir-four-state-domain-unproved'),
    ('logic [3:0] q; initial q=7;', 'rtl-source-initial-process-unencoded'),
])
def test_unencoded_initialization_is_not_silently_replaced_with_free_values(tmp_path, declaration, code):
    source = f'''module M(input clk,input [3:0] a,output [3:0] y);
      {declaration}
      always @(posedge clk) q<=a;
      assign y=q;
    endmodule'''
    machine, _, _, template = _prepare(tmp_path, source)
    relation = template.relation(machine, (), complete_state=True)
    with pytest.raises(api.Refused) as caught:
        relation.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    assert caught.value.refusal.kind == code


@pytest.mark.parametrize('helper', [False, True])
def test_initial_process_runs_once_and_does_not_restart_on_reset(tmp_path, helper):
    source = '''module M(input clk,rst,input [3:0] a,output reg [3:0] q,r);
      reg [3:0] saved=2;
      initial begin q=saved+1; r=5; end
      always @(posedge clk) begin
        if(rst) begin q<=0; r<=0; end
        else begin q<=a; r<=r+1; end
      end
    endmodule'''
    if helper:
        source = source.replace('initial begin q=saved+1;', '''
          function automatic [3:0] increment(input [3:0] v); return v+1; endfunction
          initial begin q=increment(saved);''')
    machine, _, _, template = _prepare(tmp_path, source, initialization=True)
    names = _names(machine)
    relation = template.relation(machine, (), complete_state=True)
    raw = dict.fromkeys(machine.program.fields, 0)
    raw[names['saved']] = None
    initial = relation.initial_state(machine, raw, declarations=True)
    startup = relation.settle(initial.state, event_bound=machine.event_bound)
    solver = z3.Solver()
    solver.add(*startup.constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(startup.state[names['q']] != 3, startup.state[names['r']] != 5))
    assert solver.check() == z3.unsat
    state = _state(machine, {'clk':0, 'rst':0, 'a':0, 'q':0, 'r':0, 'saved':2}, startup=True)
    state, _ = _settle(machine, state)
    assert (state[names['q']], state[names['r']]) == (3, 5)
    for clk, rst, a, expected in [(1,0,9,(9,6)), (0,1,0,(9,6)), (1,1,0,(0,0)),
                                  (0,0,0,(0,0)), (1,0,7,(7,1))]:
        state, _, _, _, _ = _step(machine, state, 0, clk=clk, rst=rst, a=a)
        state, _ = _settle(machine, state)
        assert (state[names['q']], state[names['r']]) == expected
        assert all(not state[flag] for flag, process in zip(machine.pending, machine.program.processes)
                   if process.initial_process is not None)


@pytest.mark.parametrize('body', ['#1 q=3;', '@(posedge clk) q=3;'])
def test_initial_process_suspension_is_not_rewritten_as_immediate(tmp_path, body):
    from behave.sim.core.refusal import Refused

    with pytest.raises(Refused) as caught:
        _prepare(tmp_path, f'module M(input clk,output reg [1:0] q); initial {body} endmodule',
                 initialization=True)
    assert caught.value.refusal.kind.startswith('rtl-source-ir-')


@pytest.mark.parametrize('severity', ['$fatal(1,"shape")', '$error("shape")'])
@pytest.mark.parametrize('bad', [False, True])
@pytest.mark.parametrize('helper', [False, True])
def test_construction_failure_retains_terminal_prefix_without_step_goal(tmp_path, severity, bad, helper):
    body = f'if (BAD) {severity};'
    function = f'task automatic check_shape; {body} endtask' if helper else ''
    source = f'''module M #(parameter BAD={int(bad)})(output reg [2:0] q);
      {function}
      initial begin q=1; {'check_shape();' if helper else body} q=2; end
    endmodule'''
    machine, plan, _, template = _prepare(tmp_path, source, initialization=True)
    assert not plan.declarations.sites  # Initial construction is outside the step catalogue.
    assert not machine.occurrences
    relation = template.relation(machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields, 0), declarations=True)
    result = relation.settle(initial.state, event_bound=machine.event_bound)
    solver = z3.Solver()
    solver.add(*result.constraints)
    assert solver.check() == z3.sat  # Failure is not filtered from the allowed domain.
    fault = z3.Or(*result.domain_faults.values())
    model = solver.model()
    assert z3.is_true(model.eval(fault)) is bad
    assert z3.is_true(model.eval(result.alive)) is not bad
    if not bad:  # A terminal interval has no usable symbolic successor.
        assert model.eval(result.state[_names(machine)['q']]).as_long() == 2
    solver.add(fault != bad)
    assert solver.check() == z3.unsat
    state = _state(machine, {'q': 0}, startup=True)
    post, _, marks, error = interp.run_step(machine.procedure, {'action': 1}, state)
    assert post[_names(machine)['q']] == (1 if bad else 2)
    assert bool(error) is bad
    assert bool(set(marks) & dict(machine.domain_checks).keys()) is bad


def test_construction_failure_runs_argument_effect_before_stopping(tmp_path):
    source = '''module M(output reg [2:0] q);
      function automatic integer tap; q=3; return 0; endfunction
      initial begin q=1; $fatal(1,"value %d",tap()); q=2; end
    endmodule'''
    machine, _, _, template = _prepare(tmp_path, source, initialization=True)
    relation = template.relation(machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields, 0), declarations=True)
    result = relation.settle(initial.state, event_bound=machine.event_bound)
    solver = z3.Solver()
    solver.add(*result.constraints)
    assert solver.check() == z3.sat
    model = solver.model()
    assert z3.is_true(model.eval(z3.Or(*result.domain_faults.values())))
    assert z3.is_false(model.eval(result.alive))
    state = _state(machine, {'q': 0}, startup=True)
    post, _, _, error = interp.run_step(machine.procedure, {'action': 1}, state)
    assert error
    assert post[_names(machine)['q']] == 3  # Argument runs; the following q=2 does not.


def test_construction_failure_does_not_invent_argument_order(tmp_path):
    from behave.sim.core.refusal import Refused

    source = '''module M(output reg [2:0] q);
      initial begin q=1; $error("%d %d",q++,q++); end
    endmodule'''
    with pytest.raises(Refused, match='qualified evaluation order'):
        _prepare(tmp_path, source, initialization=True)


def test_initial_process_order_is_symbolic_not_selected_by_source_order(tmp_path):
    source = '''module M(output reg [1:0] q);
      initial q=1;
      initial q=2;
    endmodule'''
    machine, _, _, template = _prepare(tmp_path, source, initialization=True)
    relation = template.relation(machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    startup = relation.settle(initial.state, event_bound=machine.event_bound)
    q = startup.state[_names(machine)['q']]
    solver = z3.Solver()
    solver.add(*startup.constraints)
    for value in range(4):
        solver.push()
        solver.add(q == value)
        assert solver.check() == (z3.sat if value in (1, 2) else z3.unsat)
        solver.pop()


def test_initial_nba_commits_and_notifies_but_cross_process_nba_order_is_not_invented(tmp_path):
    source = '''module M(output reg [1:0] q,r);
      initial begin q<=1; q<=2; end
      always @(q) r=q;
    endmodule'''
    machine, _, _, template = _prepare(tmp_path, source, initialization=True)
    relation = template.relation(machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields, 0), declarations=True)
    startup = relation.settle(initial.state, event_bound=machine.event_bound)
    solver = z3.Solver()
    solver.add(*startup.constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(*(startup.state[name] != 2 for name in machine.program.fields)))
    assert solver.check() == z3.unsat
    with pytest.raises(api.Refused, match='competing-initial-nba'):
        _prepare(tmp_path, source.replace('initial begin q<=1; q<=2; end',
                                          'initial q<=1; initial q<=2;'), initialization=True)


@pytest.mark.parametrize('private_context', [False, True])
def test_history_hit_equation_covers_every_initial_value_and_queue_length(tmp_path, private_context):
    machine, plan, _, template = _prepare(tmp_path, HISTORY_SOURCE)
    goal, contract = _goal(HISTORY_SOURCE, plan, 'saved==1'), _simple_contract()
    context = z3.Context() if private_context else None
    relation = SourceTaskRelation(template, machine, contract, (goal,), context=context, complete_state=True)
    names = _names(machine)
    initial = relation.source.relation.initial_state(machine,
        {**dict.fromkeys(machine.program.fields, 0), names['saved']: None})
    first, second = (z3.BitVec(name, 2, ctx=context) for name in ('first', 'second'))
    length = z3.Int('length', ctx=context)
    encoded = relation.encode(initial.state, ProtocolBoundary.initial(contract),
        {'in': ((first,), (second,))},
        [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})]*2, None,
        memory_state=MemoryEnvironmentState.initial((), None),
        queue_lengths={'in': length}, startup=True)
    solver = z3.Solver(ctx=context)
    solver.add(*encoded.constraints)
    expected = z3.Or(z3.And(length >= 1, initial.bits[names['saved']] == 1, first == 2),
                     z3.And(length >= 2, first == 1, second == 2))
    assert solver.check() == z3.sat
    solver.push()
    solver.add(z3.Or(encoded.hits[goal] != expected, z3.Not(encoded.alive),
        encoded.protocol.accepted[0]['in'] != (length >= 1),
        encoded.protocol.accepted[1]['in'] != (length >= 2)))
    assert solver.check() == z3.unsat
    solver.pop()
    for count in (0, 1, 2):
        solver.push()
        solver.add(length == count)
        assert solver.check() == z3.sat
        solver.add(encoded.hits[goal])
        assert solver.check() == (z3.unsat if count == 0 else z3.sat)
        solver.pop()
    solver.add(z3.Or(length < 0, length > 2))
    assert solver.check() == z3.unsat


@pytest.mark.cosim
def test_solver_selected_initial_state_and_short_queue_replay_natively(tmp_path):
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.initial_state import RTLInitialState
    from behave.sim.rtl.native_observation import _read_intervals
    from behave.sim.rtl.source_goals import manifest_of

    machine, plan, _, template = _prepare(tmp_path, HISTORY_SOURCE)
    goal, contract = _goal(HISTORY_SOURCE, plan, 'saved==1'), _simple_contract()
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue),
                                  complete_state=True, occurrences=True)
    names = _names(machine)
    initial = relation.source.relation.initial_state(machine,
        {**dict.fromkeys(machine.program.fields, 0), names['saved']: None})
    slots = [api.EventSlot(offers=('in',), controls={'rst':0, 'ordy':1})]*2
    cells = (z3.BitVec('input0', 2), z3.BitVec('input1', 2))
    length = z3.Int('queue_length')
    encoded = relation.encode(initial.state, ProtocolBoundary.initial(contract),
        {'in': tuple((cell,) for cell in cells)}, slots, None,
        memory_state=MemoryEnvironmentState.initial((), None),
        queue_lengths={'in': length}, startup=True)
    solver = z3.Solver()
    solver.add(*encoded.constraints, length == 1, encoded.source.slots[0].hits[goal])
    assert solver.check() == z3.sat
    model = solver.model()
    raw = initial.values(model)
    assert raw[names['saved']] == 1  # Solver-selected, not copied from a seed.
    count = model.eval(length).as_long()
    rows = [(model.eval(cell, model_completion=True).as_long(),) for cell in cells[:count]]
    assert rows == [(2,)]
    artifact = RTLArtifact(tmp_path/'design.sv', 'M', tmp_path/'native')
    widths = {'saved':2, 'ov':1, 'y':2}
    stimulus = api.Stimulus({'in': rows}, slots)
    trace = artifact.exact_trace(contract, stimulus,
        observe_names=('saved', 'ov', 'y', 'ir'), state_names=tuple(widths), state_widths=widths,
        initial_state=RTLInitialState.from_values(artifact.identity, widths,
            {name: raw[names[name]] for name in widths}))
    observed = list(_read_intervals(thaw_record_value(trace.source_evidence), stimulus,
                                    catalogue, expected_plan=None, prefix=False))
    phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
    for phase, (_label, event) in zip(phases, observed, strict=True):
        assert z3.is_true(model.eval(phase.alive))
        for key, hit in phase.occurrences.items():
            assert z3.is_true(model.eval(hit)) == (key in event.occurrences)
        for key, hit in phase.hits.items():
            assert z3.is_true(model.eval(hit)) == (key in event.goals)
    for index, record in enumerate(trace.records):
        for name in widths:
            assert model.eval(encoded.source.slots[index].state[names[name]]).as_long() == dict(record.state)[name]
        assert z3.is_true(model.eval(encoded.protocol.accepted[index]['in'])) == (
            dict(record.accepted).get('in') is not None)
