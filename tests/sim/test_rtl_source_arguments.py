"""Helper direction semantics agree with every native goal, not just outputs."""

import pytest

from behave.sim import api
from behave.sim.core.refusal import Refused

from .test_rtl_bounded_source_loops import _assert_closure, _lower

pytestmark = [pytest.mark.eda('slang', 'verilator', 'make'), pytest.mark.solver]


@pytest.mark.parametrize('direction', ['output', 'inout'])
@pytest.mark.parametrize('signed,width', [('', 4), ('signed', 4), ('signed', 8)])
def test_copy_arguments_keep_conversions_and_early_return(
        tmp_path, record_property, direction, signed, width):
    assign = 'v' if direction == 'output' else 'x+v'
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v, {direction} logic {signed} [{width-1}:0] x);
        x={assign};
        if(1'b0) return 0;
        if(v==5) return 17;
        x=x+1; return 3;
      endfunction
      function automatic [7:0] g(input [3:0] v);
        logic [7:0] actual=3;
        logic [7:0] result=f(v,actual);
        return actual+result;
      endfunction
      always @(posedge clk) q=g(a);
    endmodule'''

    def expected(a):
        value = ((3 if direction == 'inout' else 0) + a + (a != 5)) % (1 << width)
        if signed and value >= 1 << (width-1):
            value -= 1 << width
        return (value + (17 if a == 5 else 3)) % 256

    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.parametrize('direction', ['output', 'inout', 'ref'])
def test_copyout_indices_are_late_but_ref_indices_are_bound_on_entry(
        tmp_path, record_property, direction):
    update = 'v+13' if direction == 'output' else 'x+v+13'
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      int ix;
      function automatic [7:0] f(input [3:0] v, {direction} logic [7:0] x);
        ix=1; x={update};
        if(1'b0) return 0;
        return x;
      endfunction
      function automatic [7:0] g(input [3:0] v);
        logic [7:0] cells[0:1]='{{3,5}};
        logic [7:0] result;
        ix=0;
        result=f(v,cells[ix]);
        return cells[0]+cells[1]*4;
      endfunction
      always @(posedge clk) q=g(a);
    endmodule'''
    expected = (lambda a: a+36) if direction == 'ref' else (
        lambda a: 3+4*(a+(13 if direction == 'output' else 16)))
    _assert_closure(tmp_path, record_property, source, range(16), expected)


def test_selected_ref_nonlocal_write_has_complete_native_closure(tmp_path, record_property):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      int ix;
      function automatic [7:0] f(input [3:0] v, ref logic [7:0] x);
        ix=1; x=x+v+13;
        if(1'b0) return 0;
        return x;
      endfunction
      function automatic [7:0] g(input [3:0] v);
        logic [7:0] cells[0:1]='{3,5};
        logic [7:0] result;
        ix=0; result=f(v,cells[ix]); return cells[0]+cells[1]*4;
      endfunction
      always @(posedge clk) q=g(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: a+36)


@pytest.mark.parametrize('direction', ['output', 'inout'])
def test_copyback_selector_helper_keeps_caller_occurrences(tmp_path, record_property, direction):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      int ix;
      function automatic int index_value();
        if(ix==0) return 0;
        return 1;
      endfunction
      function automatic [7:0] put(input [3:0] v, {direction} logic [7:0] changed);
        ix=1; changed=v+13;
        if(1'b0) return 0;
        return changed;
      endfunction
      function automatic [7:0] g(input [3:0] v);
        logic [7:0] cells[0:1]='{{3,5}};
        logic [7:0] result;
        ix=0; result=put(v,cells[index_value()]);
        return cells[0]+cells[1]*4;
      endfunction
      always @(posedge clk) q=g(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: 3+4*(a+13))


def test_two_ref_formals_share_immediate_writes(tmp_path, record_property):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v, ref logic [7:0] x,y);
        x=v; y=y+1;
        if(1'b0) return 0;
        if(x==6) return x+20;
        return x;
      endfunction
      function automatic [7:0] g(input [3:0] v);
        logic [7:0] actual=9;
        logic [7:0] result=f(v,actual,actual);
        return actual+result;
      endfunction
      always @(posedge clk) q=g(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 2*(a+1)+(20 if a == 5 else 0))


@pytest.mark.parametrize('direction', ['inout', 'ref'])
def test_nested_helpers_preserve_the_callers_binding(tmp_path, record_property, direction):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v, {direction} logic [7:0] x);
        x=x+v;
        if(1'b0) return 0;
        return x;
      endfunction
      function automatic [7:0] g(input [3:0] v, {direction} logic [7:0] x);
        logic [7:0] result=f(v,x);
        return result+x;
      endfunction
      function automatic [7:0] h(input [3:0] v);
        logic [7:0] x=3;
        logic [7:0] result=g(v,x);
        return result+x;
      endfunction
      always @(posedge clk) q=h(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: 3*(a+3))


@pytest.mark.parametrize('direction', ['output', 'inout', 'ref'])
def test_helper_bindings_compose_with_boolean_and_loop_goals(tmp_path, record_property, direction):
    initialization = 'x=0;' if direction == 'output' else ''
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v, {direction} logic [7:0] x);
        {initialization}
        for(int i=0;i<(v&3);i++) begin
          x=x+1;
          if(i==1 && v==7) x=x+4;
          if(1'b0) return 0;
        end
        return x;
      endfunction
      function automatic [7:0] g(input [3:0] v);
        logic [7:0] actual=3;
        logic [7:0] result=f(v,actual);
        return actual+result;
      endfunction
      always @(posedge clk) q=g(a);
    endmodule'''
    base = 0 if direction == 'output' else 3
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 2*(base+(a&3)+4*(a==7)))


@pytest.mark.parametrize('body', ['if(v) x=1; return 0;', 'return 0;',
                                'x=x+1; return 0;', 'for(int i=0;i<v;i++) x=1; return 0;'])
def test_undefined_output_is_not_initialized_to_fabricated_zero(tmp_path, body):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v, output logic [7:0] x);
        {body}
      endfunction
      always @(posedge clk) q=f(a,q);
    endmodule'''
    with pytest.raises(Refused, match='rtl-source-ir-output-undefined'):
        _lower(tmp_path, source)


def test_conflicting_copyouts_do_not_get_an_invented_order(tmp_path):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(output logic [7:0] x,y);
        x=1; y=2; return 0;
      endfunction
      always @(posedge clk) q=f(q,q);
    endmodule'''
    with pytest.raises(Refused, match='operand effects'):
        _lower(tmp_path, source)


@pytest.mark.parametrize('shape', ['packed', 'ascending', 'array', 'array-reversed'])
def test_disjoint_copyouts_preserve_native_values_and_every_goal(tmp_path, record_property, shape):
    if shape.startswith('array'):
        declaration = "logic [3:0] cells[0:1];"
        actuals = 'cells[0],cells[1]' if shape == 'array' else 'cells[1],cells[0]'
        tail = 'q={cells[1],cells[0]};' if shape == 'array' else 'q={cells[0],cells[1]};'
    elif shape == 'ascending':
        declaration, actuals, tail = 'logic [0:7] bits;', 'bits[4:7],bits[0:3]', 'q=bits;'
    else:
        declaration, actuals, tail = '', 'q[3:0],q[7:4]', ''
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      {declaration}
      task automatic pair(input [3:0] v, output logic [3:0] x,y);
        x=v; y=15-v;
        if(v==7) y=v+1;
        if(1'b0) y=99;
      endtask
      always @(posedge clk) begin pair(a,{actuals}); {tail} end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: a+16*(15-a))


@pytest.mark.parametrize('actuals', ['q[3:0],q[5:2]', 'cells[0],cells[0]', 'cells[a&1],cells[0]'])
def test_copyouts_with_overlapping_or_unproved_destinations_still_refuse(tmp_path, actuals):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      logic [3:0] cells[0:1];
      task automatic pair(output logic [3:0] x,y); x=1; y=2; endtask
      always @(posedge clk) pair({actuals});
    endmodule'''
    with pytest.raises(Refused, match='operand effects'):
        _lower(tmp_path, source)


def test_copyout_order_cannot_change_another_actuals_selector(tmp_path):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      int ix;
      logic [3:0] cells[0:1];
      task automatic pair(output int index, output logic [3:0] item);
        index=1; item=7;
      endtask
      always @(posedge clk) begin ix=0; pair(ix,cells[ix]); end
    endmodule'''
    with pytest.raises(Refused, match='operand effects'):
        _lower(tmp_path, source)


@pytest.mark.parametrize('declaration,end', [('task automatic', 'endtask'),
                                         ('function automatic void', 'endfunction')])
def test_void_helpers_preserve_output_copy_and_return(tmp_path, record_property, declaration, end):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      {declaration} f(input [3:0] v, output logic [7:0] x);
        x=v;
        if(1'b0) return;
        if(v==5) return;
        x=x+1;
      {end}
      always @(posedge clk) f(a,q);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: a+(a!=5))


@pytest.mark.cosim
@pytest.mark.parametrize('direction', ['output', 'inout', 'ref'])
@pytest.mark.parametrize('selected', [False, True])
def test_public_search_finds_helper_event_and_native_replay_confirms_it(
        tmp_path, record_property, direction, selected):
    import json

    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower as lower_scheduler

    scratch = 'scratch[0:1]' if selected else 'scratch'
    initialize = 'scratch[0]=0; scratch[1]=0; ix=0;' if selected else 'scratch=0;'
    actual = 'scratch[ix]' if selected else 'scratch'
    source = f'''module M(input clk,rst,in_valid,input [15:0] a,
      output reg out_valid,output reg [15:0] y);
      logic [15:0] {scratch};
      int ix;
      function automatic [15:0] helper(input [15:0] v, {direction} logic [15:0] changed);
        {'ix=1;' if selected else ''}
        changed=v;
        if(1'b0) return 0;
        if(changed==16'hc35b) return changed;
        return v;
      endfunction
      always @(posedge clk) begin
        if(rst) begin y<=0; out_valid<=0; end
        else begin
          out_valid<=in_valid;
          if(in_valid) begin {initialize} y<=helper(a,{actual}); end
        end
      end
    endmodule'''
    kind = api.FiniteType(16)
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('a', kind, 'a')],
                     api.sampled_edge('in', valid='in_valid'), 'c', kind=api.Kind.TRANSACTION),
        api.Endpoint('out', 'out', [api.Field('y', kind, 'y')],
                     api.sampled_edge('out', valid='out_valid'), 'c', kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1)], [api.ClockDomain('c', 'clk', 'rst')],
        protocol_instances=[api.ProtocolInstance('compute', ('in', 'out'))])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': b'class M:\n    def reset(self):\n        pass\n'
                        b'    def process(self,a):\n        return {"y":a}\n'}, config={'class': 'M'})
    path = tmp_path/'design.sv'
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, 'M', tmp_path/'build'))
    _, plan = lower_scheduler(tmp_path, source)
    target = _goal(source, plan, "changed==16'hc35b")
    dead = _goal(source, plan, "1'b0")
    declared = handle.frontend_catalogue.as_dict()
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=3))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path/'result', baseline=baseline, reference=reference,
        providers=handle.generation_providers(reference, workdir=tmp_path/'search')[1:],
        search_policy=api.SearchPolicy(time_budget_s=60, max_probes=8), run_analysis=False))
    (tmp_path/'result.json').write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed, result.as_dict()
    search, = result.generated.searches
    before = set(search.telemetry['baseline_coverage']['hits'])
    after = set(search.telemetry['coverage']['hits'])
    confirmed = {goal for run in result.evaluation.results for row in run.trace.records
                 for goal, hit in row.hits if hit is True}
    assert target not in before and target in after
    assert after <= confirmed
    assert dead in handle.frontend_catalogue and dead not in after
    assert handle.frontend_catalogue.as_dict() == declared
    assert result.analysis is None
    record_property('helper_search', json.dumps({
        'baseline_hits': len(before), 'search_hits': len(after),
        'probes': search.telemetry['probes'], 'termination': search.telemetry['termination']}))
