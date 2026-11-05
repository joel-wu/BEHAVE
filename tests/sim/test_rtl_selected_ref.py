"""Entry-bound references retain storage aliases and visible native effects."""

import pytest

from .test_rtl_bounded_source_loops import _assert_closure
from .test_rtl_source_activation_scheduling import _assert_entries
from .test_rtl_observation_frontend import _installed

pytestmark = [pytest.mark.eda('slang', 'verilator', 'make'), pytest.mark.solver]


def test_cancelling_writes_through_overlapping_formals_are_not_silently_observed(tmp_path):
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_plan import plan_source

    source = '''module M(input [7:0] a,output logic [31:0] q);
      int storage;
      task automatic touch(ref int x,y); x=y++; endtask
      always @(posedge a[0]) touch(storage,storage);
      always_comb q=storage;
    endmodule'''
    (tmp_path / 'design.sv').write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, 'M', run=_installed)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    assert any('repeated source writes in one expression' in issue for issue in plan.native_issues)


@pytest.mark.parametrize('index', ['ix', 'ix&1', 'index_value()'])
def test_selected_ref_captures_index_before_nonlocal_mutation(tmp_path, record_property, index):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      int ix,calls;
      logic [7:0] cells[0:1];
      function automatic int index_value();
        calls=calls+1;
        return ix;
      endfunction
      function automatic [7:0] f(input [3:0] v,ref logic [7:0] x);
        ix=1; x=x+v;
        if(1'b0) return 0;
        // This nonlocal read must see the write through the selected alias.
        return cells[0];
      endfunction
      always @(posedge clk) begin
        logic [7:0] result;
        cells[0]=3; cells[1]=5; ix=0; calls=0;
        result=f(a,cells[{index}]);
        q=result+cells[0]+4*cells[1]+32*calls;
      end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 2*a+26+(32 if index == 'index_value()' else 0))


@pytest.mark.parametrize('same', [False, True])
def test_selected_ref_formals_share_live_storage(tmp_path, record_property, same):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      int ix;
      function automatic [7:0] f(input [3:0] v,ref logic [7:0] x,y,
                                  const ref logic [7:0] z);
        ix=1; x=v; y=y+3;
        if(1'b0) return 0;
        if(z==6) x=x+7;
        return z;
      endfunction
      function automatic [7:0] g(input [3:0] v);
        logic [7:0] cells[0:1]='{{2,5}};
        logic [7:0] result;
        ix=0;
        result=f(v,cells[ix],cells[{'ix' if same else '1'}],cells[ix]);
        return result+cells[0]+4*cells[1];
      endfunction
      always @(posedge clk) q=g(a);
    endmodule'''
    def expected(a):
        value = a+(3 if same else 0)
        value += 7 if value == 6 else 0
        return 2*value+(20 if same else 32)
    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.parametrize('depth', [1, 2, 3])
def test_selected_ref_nested_calls_keep_outer_binding(tmp_path, record_property, depth):
    helpers = '''function automatic [7:0] f0(input [3:0] v,ref logic [7:0] x);
      ix=1; x=x+v;
      if(1'b0) return 0;
      return x;
    endfunction'''
    for n in range(1, depth+1):
        helpers += f'''\nfunction automatic [7:0] f{n}(input [3:0] v,ref logic [7:0] x);
          logic [7:0] value=f{n-1}(v,x);
          x=x+1;
          return value+x;
        endfunction'''
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      int ix;
      {helpers}
      function automatic [7:0] g(input [3:0] v);
        logic [7:0] cells[0:1]='{{3,5}};
        logic [7:0] result;
        ix=0; result=f{depth}(v,cells[ix]);
        return result+cells[0]+4*cells[1];
      endfunction
      always @(posedge clk) q=g(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: (depth+2)*(a+3)+depth*(depth+1)//2+depth+20)


def test_multidimensional_ref_captures_each_subscript(tmp_path, record_property):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      int row,col;
      function automatic [7:0] f(input [3:0] v,ref logic [7:0] x);
        row=1; col=0; x=x+v;
        if(1'b0) return 0;
        return x;
      endfunction
      function automatic [7:0] g(input [3:0] v);
        logic [7:0] cells[0:1][0:1]='{'{1,2},'{3,4}};
        logic [7:0] result;
        row=0; col=1; result=f(v,cells[row][col]);
        return result+cells[0][0]+2*cells[0][1]+4*cells[1][0]+8*cells[1][1];
      endfunction
      always @(posedge clk) q=g(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16), lambda a: 3*a+51)


@pytest.mark.eda('iverilog', 'vvp')
@pytest.mark.parametrize('index_call', [False, True])
def test_original_and_observed_ref_match_independent_procedural_oracle(tmp_path, index_call):
    shared = '''int ix,calls;
      logic [7:0] cells[0:1];
      function automatic int index_value();
        calls=calls+1; return ix;
      endfunction'''
    initialize = 'ix=0;calls=0;cells[0]=3;cells[1]=5;'
    result = 'q=cells[0]+4*cells[1]+32*calls;'
    index = 'index_value()' if index_call else 'ix'
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      {shared}
      task automatic f(input [3:0] v,ref logic [7:0] x);
        ix=1; x=x+v;
        if(v==7) x=x+2;
      endtask
      always @(posedge a[0]) begin
        {initialize} f(a[4:1],cells[{index}]); {result}
      end
    endmodule'''
    # Icarus lacks ref ports. This oracle binds the selected index explicitly;
    # it does not rewrite the function source used by either native build.
    reference = f'''module M(input [7:0] a,output logic [31:0] q=0);
      {shared}
      int bound;
      always @(posedge a[0]) begin
        {initialize} bound={index}; ix=1; cells[bound]=cells[bound]+a[4:1];
        if(a[4:1]==7) cells[bound]=cells[bound]+2;
        {result}
      end
    endmodule'''
    values = [value for a in range(16) for value in (a<<1, (a<<1)|1)]
    _assert_entries(tmp_path, source, values, (result,), reference_source=reference)


@pytest.mark.eda('iverilog', 'vvp')
@pytest.mark.parametrize('process', ['always @({term})', 'always_comb', 'always @*'])
@pytest.mark.parametrize('element', [0, 1])
@pytest.mark.parametrize('nested', [False, True])
def test_ref_writes_notify_another_process_even_when_restored(tmp_path, process, element, nested):
    forward = '''task automatic forward(input [7:0] v,ref logic [7:0] x);
      pulse(v,x);
    endtask''' if nested else ''
    consumer = process.format(term=f'cells[{element}]')
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      int ix;
      logic [7:0] cells[0:1]='{{0,0}};
      task automatic pulse(input [7:0] v,ref logic [7:0] x);
        ix=1; x=v; x=0;
      endtask
      {forward}
      always @(posedge a[0]) begin ix=0; {'forward' if nested else 'pulse'}(a,cells[ix]); end
      {consumer} q=cells[{element}];
    endmodule'''
    # Split always_comb's oracle storage: Icarus widens an implicit array
    # element read to the whole array. For @*, retain the whole-array rule.
    # Neither original nor observed RTL consumes the oracle's replacement.
    reference = f'''module M(input [7:0] a,output logic [31:0] q=0);
      logic [7:0] cell0=0,cell1=0;
      always @(posedge a[0]) begin cell0=a; cell0=0; end
      {process.format(term=f'cell{element}')} q=cell{element};
    endmodule'''
    reference_statement = f'q=cell{element};'
    if process == 'always @*':
        reference = f'''module M(input [7:0] a,output logic [31:0] q=0);
          logic [7:0] cells[0:1];
          initial begin cells[0]=0; cells[1]=0; end
          always @(posedge a[0]) begin cells[0]=a; cells[0]=0; end
          always @* q=cells[{element}];
        endmodule'''
        reference_statement = f'q=cells[{element}];'
    rows = _assert_entries(tmp_path, source, [0,1,0,3,3,0,5], (f'q=cells[{element}];',),
                            reference_source=reference,
                            reference_statements=(reference_statement,), settle_startup=True)
    changed = element == 0 or process == 'always @*'
    assert [bool(entries) for _, entries in rows] == [False,changed,False,changed,False,False,changed]


@pytest.mark.eda('iverilog', 'vvp')
def test_ref_notifications_select_the_actual_call_target(tmp_path):
    source = '''module M(input [7:0] a,output wire [31:0] q);
      logic [7:0] x[0:1]='{0,0},z[0:1]='{0,0};
      logic [15:0] left=0,right=0;
      task automatic pulse(input [7:0] v,ref logic [7:0] target);
        target=v; target=0;
      endtask
      always @(posedge a[0]) if(a[1]) pulse(a,x[0]); else pulse(a,z[0]);
      always @(x[0]) left=x[0];
      always @(z[0]) right=z[0];
      assign q={left,right};
    endmodule'''
    reference = source[:source.index('task automatic')] + '''
      always @(posedge a[0])
        if(a[1]) begin x[0]=a; x[0]=0; end
        else begin z[0]=a; z[0]=0; end
      always @(x[0]) left=x[0];
      always @(z[0]) right=z[0];
      assign q={left,right};
    endmodule'''
    rows = _assert_entries(tmp_path, source, [0,1,0,3,3,0,5],
                           ('left=x[0];','right=z[0];'), reference_source=reference,
                           settle_startup=True)
    assert [len(entries) for _, entries in rows] == [0,1,0,1,0,0,1]


@pytest.mark.eda('iverilog', 'vvp')
def test_ref_notifications_keep_each_physical_instance(tmp_path):
    source = '''module Cell(input [3:0] a,output logic [15:0] q=0);
      logic [7:0] storage=0;
      task automatic pulse(input [3:0] v,ref logic [7:0] x);
        x=v; x=0;
      endtask
      always @(posedge a[0]) pulse(a,storage);
      always @(storage) q=storage;
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      Cell low(a[3:0],q[15:0]), high(a[7:4],q[31:16]);
    endmodule'''
    first, last = source.index('task automatic'), source.index('endtask')+len('endtask')
    reference = source[:first] + source[last:]
    reference = reference.replace('pulse(a,storage);', 'begin storage=a; storage=0; end')
    rows = _assert_entries(tmp_path, source, [0,1,0,16,16,0,17], ('q=storage;',),
                           reference_source=reference, settle_startup=True)
    assert [len(entries) for _, entries in rows] == [0,1,0,1,0,0,2]


@pytest.mark.eda('iverilog', 'vvp')
def test_unpacked_struct_ref_keeps_native_storage_alias(tmp_path):
    source = '''module M(input [7:0] a,output logic [31:0] q=0);
      typedef struct {logic [7:0] x; logic [7:0] y;} pair_t;
      pair_t pair;
      int marker;
      task automatic f(input [3:0] v,ref logic [7:0] x);
        marker=1; x=x+v;
      endtask
      always @(posedge a[0]) begin
        pair.x=3; pair.y=5; f(a[4:1],pair.x);
        q=pair.x+4*pair.y+32*marker;
      end
    endmodule'''
    # Icarus lacks unpacked structs and ref ports; use separate scalar storage
    # for the independent arithmetic/control oracle, not for either native run.
    reference = '''module M(input [7:0] a,output logic [31:0] q=0);
      logic [7:0] x,y;
      int marker;
      always @(posedge a[0]) begin
        x=3; y=5; marker=1; x=x+a[4:1];
        q=x+4*y+32*marker;
      end
    endmodule'''
    values = [value for a in range(16) for value in (a<<1, (a<<1)|1)]
    _assert_entries(tmp_path, source, values, ('q=pair.x+4*pair.y+32*marker;',),
                    reference_source=reference, reference_statements=('q=x+4*y+32*marker;',))
