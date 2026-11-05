"""One physical bit view for source values, writes, subscriptions and queries."""

import pytest

from behave.sim.symbolic import ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.search_policy import SearchBudget, SearchPolicy, search_scope

from .test_rtl_port_transfers import _drive, _paths
from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_scheduler import _lower

pytestmark = [pytest.mark.eda("slang", "verilator", "make"), pytest.mark.solver]


def _check_native_and_queries(tmp_path, source, values=range(16)):
    machine, plan = _lower(tmp_path, source)
    paths = _paths(machine)
    initial = machine.boundary({name: 0 for name in machine.program.fields}, startup=True)
    initial, _ = _drive(machine, initial, clk=0, a=0)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    observed, native_hits = replay(values)
    state = initial
    for value, expected, native in zip(values, observed, native_hits, strict=True):
        state, low = _drive(machine, state, clk=0, a=value)
        state, high = _drive(machine, state, clk=1)
        assert state[paths['q']] == expected
        assert low | high == native
    native = [replay((value,))[1][0] for value in range(16)]
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    # This helper is also imported by other test modules; their fixtures do
    # not inherit a defining module's autouse Search lifetime.
    with search_scope(SearchBudget(SearchPolicy()).direction(1)):
        for goal in manifest_of(plan.declarations, filename='design.sv'):
            answer = query_source_interval(machine, env, initial,
                {paths['clk']: 0, paths['a']: 0}, (goal,), mutable_inputs=(paths['a'],),
                event_bound=machine.event_bound,
                following_inputs=({paths['clk']: 1, paths['a']: 0},))
            reachable = any(goal in hits for hits in native)
            assert answer.outcome is (probing.Outcome.SAT if reachable else probing.Outcome.LOCAL_UNSAT), answer.detail
            if reachable:
                assert goal in replay((answer.candidate[paths['a']],))[1][0]
    return machine, plan


@pytest.mark.parametrize('parent,child,actual', [
    ('[3:0]', '[1:0]', 'a[2:1]'),
    ('[0:3]', '[0:1]', 'a[1:2]'),
    ('[3:0]', 'signed [1:0]', 'a[1+:2]'),
    ('[3:0]', '[0:1]', 'a[2-:2]'),
    ('[-1:-4]', '[-2:-3]', 'a[-2:-3]'),
])
def test_packed_port_views_keep_orientation_types_and_all_source_hits(tmp_path, parent, child, actual):
    source = f'''module C(input clk,input {child} data,output wire [1:0] result);
      logic [1:0] saved;
      initial saved=0;
      always @(posedge clk) if(data==3) saved<=data+1; else saved<=data;
      assign result=saved;
    endmodule
    module M(input clk,input {parent} a,output wire [3:0] q);
      C child(clk,{actual},q[2:1]);
      assign q[0]=0;
      assign q[3]=0;
    endmodule'''
    machine, _ = _check_native_and_queries(tmp_path, source)
    assert not any(p.context is None for p in machine.program.processes)
    assert ('child','data') not in _paths(machine)
    assert ('child','result') not in _paths(machine)


def test_nested_contiguous_ports_compose_physical_offsets(tmp_path):
    source = '''module C(input clk,input [1:0] data,output wire [1:0] result);
      logic [1:0] saved;
      initial saved=0;
      always @(posedge clk) if(data==3) saved<=1; else saved<=data;
      assign result=saved;
    endmodule
    module W(input clk,input [2:0] a,output [2:0] q);
      C inner(clk,a[2:1],q[1:0]);
      assign q[2]=0;
    endmodule
    module M(input clk,input [3:0] a,output wire [3:0] q);
      W outer(clk,a[3:1],q[3:1]);
      assign q[0]=0;
    endmodule'''
    machine, _ = _check_native_and_queries(tmp_path, source)
    views = machine.program.compiler.net_views
    fields = {symbol['name']: view.offset for identity, view in views.items()
              if (symbol := machine.program.compiler.symbols[identity])['name'] in {'data','result'}}
    assert fields == {'data': 2, 'result': 1}


def test_selected_clock_net_uses_its_real_edge_bit(tmp_path):
    source = '''module C(input trigger,data,output logic [3:0] result);
      initial result=0;
      always @(posedge trigger) if(data) result<=9; else result<=4;
    endmodule
    module M(input clk,input [3:0] a,output [3:0] q);
      C child(a[2],a[0],q);
    endmodule'''
    machine, _ = _check_native_and_queries(tmp_path, source, (0,1,5,1,4,0))
    assert any(s.mask == 4 and s.edge == 'rise' for s in machine.subscriptions)


def test_selected_input_ownership_is_not_lost(tmp_path):
    source = '''module C(input a); assign a=1; endmodule
    module M(input clk,input [3:0] a,output q); C c(a[1]); assign q=0; endmodule'''
    with pytest.raises(Refused, match='input-written'):
        _lower(tmp_path, source)


def test_overlapping_child_net_slices_keep_competing_writer_refusal(tmp_path):
    source = '''module C(input [1:0] a,output [1:0] q); assign q=a; endmodule
    module M(input [3:0] a,output [3:0] q);
      C left(a[1:0],q[2:1]); C right(a[3:2],q[1:0]); assign q[3]=0;
    endmodule'''
    with pytest.raises(Refused, match='competing-writers'):
        _lower(tmp_path, source)


@pytest.mark.parametrize('operator', ['=', '<='])
def test_concatenated_selected_writes_preserve_shared_parent_bits(tmp_path, operator):
    source = f'''module M(input clk,input [3:0] a,output logic [3:0] q);
      initial q=0;
      always @(posedge clk) begin
        if(a==3) {{q[1:0],q[3:2]}} {operator} a;
        else {{q[3:2],q[1:0]}} {operator} a;
      end
    endmodule'''
    _check_native_and_queries(tmp_path, source)


def test_nested_concatenation_and_net_views_share_assignment_lowering(tmp_path):
    source = '''module C(input a,b,output s,c);
      assign {{c},s}=a+b;
    endmodule
    module M(input clk,input [3:0] a,output [3:0] q);
      C left(a[0],a[1],q[0],q[1]);
      C right(a[2],a[3],q[2],q[3]);
    endmodule'''
    _check_native_and_queries(tmp_path, source)


def test_net_initializer_writes_only_its_collapsed_slice(tmp_path):
    source = '''module C(a,q);
      input [1:0] a;
      output [1:0] q;
      wire [1:0] q=a;
    endmodule
    module M(input clk,input [3:0] a,output [3:0] q);
      C child(a[2:1],q[2:1]);
      assign q[0]=a[0]; assign q[3]=a[3];
    endmodule'''
    _check_native_and_queries(tmp_path, source)


def test_packed_array_port_view_keeps_element_stride(tmp_path):
    source = '''module C(input [1:0] data,output wire [1:0] q);
      assign q={data[0],data[1]};
    endmodule
    module M(input clk,input [1:0][1:0] a,output [3:0] q);
      C child(a[1],q[2:1]);
      assign q[0]=0; assign q[3]=0;
    endmodule'''
    _check_native_and_queries(tmp_path, source)


def test_overlapping_concatenation_is_not_silently_reordered(tmp_path):
    source = '''module M(input clk,input [3:0] a,output logic [3:0] q);
      always @(posedge clk) {q[2:1],q[1:0]}=a;
    endmodule'''
    with pytest.raises(Refused, match='overlapping concatenation'):
        _lower(tmp_path, source)
