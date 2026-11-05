"""Native qualified-case failure callbacks preserve the compiler's action point."""

import subprocess

import pytest

from behave.sim.rtl.native_events import prepare_declared_native_events
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_source_declarations import _documents

pytestmark = pytest.mark.eda('slang', 'verilator', 'make')


@pytest.mark.parametrize('qualifier,selector,items,a,expected,fails', [
    ('unique', 'a', '0:q=3; 1:q=7;', 3, 0, True),
    ('priority', 'a', '0:q=3; 1:q=7;', 3, 0, True),
    ('unique0', 'a', '0:q=3; 1:q=7;', 3, 0, False),
    ('unique', 'a', '0:q=3; 0:q=7; default:q=9;', 0, 3, True),
    ('unique0', 'a', '0:q=3; 0:q=7;', 0, 3, True),
    ('priority', 'a', '0:q=3; 0:q=7; default:q=9;', 0, 3, False),
    ('unique', 'a', '0:q=3; default:q=9;', 3, 9, False),
    ('unique', 'a', 'default:;', 3, 0, False),
    ('unique', 'q', '0:q=3; 1:q=7;', 0, 3, True),
    ('unique', 'a', "2'b1?:q=3; 2'b?1:q=7; default:q=9;", 3, 3, True),
])
@pytest.mark.parametrize('enabled', [False, True])
def test_native_case_marker_is_only_called_by_the_original_failure_action(
        tmp_path, qualifier, selector, items, a, expected, fails, enabled):
    source = f'''module M(input clk,input [1:0] a,output reg [7:0] q=0);
      always @(posedge clk) begin
        {qualifier} casez({selector}) {items} endcase
      end
    endmodule'''
    cst, ast = _documents(tmp_path/'parse', source)
    plan = plan_source(source, cst, semantic_document=ast)
    collector = prepare_declared_native_events(plan, filename='design.sv',
        toolchain_sha256='1'*64, execution_sha256='2'*64, callback_limit=100)
    marker = '''function automatic void __behave_native_event_case_failure();
      $display("CASE_OBSERVED %0d",q);
    endfunction
    '''
    observed = source.replace('always @', marker+'always @').replace(
        'endcase', 'endcase __behave_native_event_case_failure();')
    driver = f'''
      Verilated::assertOn({'true' if enabled else 'false'});
      if (behave_native_events_begin_v1(0)) return 1;
      dut.clk=0; dut.a={a}; dut.eval(); dut.clk=1; dut.eval();
      uint64_t bits=0,calls=0;
      if (behave_native_events_end_v1(0,&bits,1,&calls)) return 2;
      std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    '''
    for name, text in (('plain',source),('observed',observed)):
        if enabled and fails:
            with pytest.raises(subprocess.CalledProcessError) as stopped:
                _trace(tmp_path/name, text, collector, driver)
            assert 'Assertion failed' in stopped.value.stdout
            assert stopped.value.stdout.count('CASE_OBSERVED') == (name == 'observed')
            if name == 'observed':
                assert f'CASE_OBSERVED {expected}\n' in stopped.value.stdout
        else:
            assert _trace(tmp_path/name, text, collector, driver) == [(expected,0,0)]


@pytest.mark.parametrize('transfer', ['return', 'break', 'continue'])
def test_native_case_marker_does_not_cross_an_early_exit(tmp_path, transfer):
    action = 'return;' if transfer == 'return' else transfer+';'
    case = f'unique case(a) 0:begin q=3; {action} end 0:q=7; default:q=9; endcase'
    if transfer != 'return':
        case = 'for(int i=0;i<1;i++) begin '+case+' end'
    source = f'''module M(input clk,input [1:0] a,output reg [7:0] q=0);
      function automatic void f(); {case} endfunction
      always @(posedge clk) f();
    endmodule'''
    cst, ast = _documents(tmp_path/'parse',source)
    plan = plan_source(source,cst,semantic_document=ast)
    collector = prepare_declared_native_events(plan,filename='design.sv',
        toolchain_sha256='1'*64,execution_sha256='2'*64,callback_limit=100)
    marker = '''function automatic void __behave_native_event_case_failure();
      $display("CASE_OBSERVED");
    endfunction
    '''
    observed = source.replace('function automatic void f()',marker+'function automatic void f()').replace(
        'endcase','endcase __behave_native_event_case_failure();')
    driver = '''
      if (behave_native_events_begin_v1(0)) return 1;
      dut.clk=0; dut.a=0; dut.eval(); dut.clk=1; dut.eval();
      uint64_t bits=0,calls=0;
      if (behave_native_events_end_v1(0,&bits,1,&calls)) return 2;
      std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    '''
    for name,text in (('plain',source),('observed',observed)):
        assert _trace(tmp_path/name,text,collector,driver) == [(3,0,0)]
