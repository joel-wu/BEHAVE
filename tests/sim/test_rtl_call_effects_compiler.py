"""Independent native controls for helper argument effects and conversions."""

import pytest

from .test_rtl_source_activation_scheduling import _assert_entries

pytestmark = pytest.mark.eda('slang', 'verilator', 'make', 'iverilog', 'vvp')


@pytest.mark.parametrize('direction,uses_old', [('output', False), ('inout', False), ('inout', True)])
@pytest.mark.parametrize('formal_signed,actual_signed', [('', ''), ('signed', ''),
                                                       ('', 'signed'), ('signed', 'signed')])
@pytest.mark.parametrize('formal_width,actual_width', [(4, 8), (8, 4), (5, 5)])
def test_copyback_assignment_conversion_matches_independent_simulator(
        tmp_path, direction, uses_old, formal_signed, actual_signed, formal_width, actual_width):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      logic {actual_signed} [{actual_width-1}:0] actual;
      task automatic put(input [3:0] v, {direction} logic {formal_signed} [{formal_width-1}:0] changed);
        changed={'changed+1' if uses_old else '$signed(v)'};
      endtask
      always @(posedge a[0]) begin
        actual=$signed(a[4:1]); put(a[4:1],actual);
        q=actual;
      end
    endmodule'''
    values = [value for a in range(16) for value in (a << 1, (a << 1) | 1)]
    _assert_entries(tmp_path, source, values, ('q=actual;',))


@pytest.mark.parametrize('direction', ['output', 'inout'])
def test_copyback_subscript_uses_return_time_state(tmp_path, direction):
    value = '13' if direction == 'output' else 'changed+13'
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      int index;
      logic [7:0] cells[0:1];
      task automatic put({direction} logic [7:0] changed);
        index=1; changed={value};
      endtask
      always @(posedge a[0]) begin
        index=0; cells[0]=3; cells[1]=5;
        put(cells[index]);
        q=cells[0]+4*cells[1];
      end
    endmodule'''
    _assert_entries(tmp_path, source, [0,1,2,3], ('q=cells[0]+4*cells[1];',))


@pytest.mark.parametrize('direction', ['output', 'inout'])
def test_copyback_selector_calls_execute_at_each_assignment(tmp_path, direction):
    value = '13' if direction == 'output' else 'changed+13'
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      int index, calls;
      logic [7:0] cells[0:1];
      function automatic int index_value();
        calls=calls+1;
        if(index==0) return 0;
        return 1;
      endfunction
      task automatic put({direction} logic [7:0] changed);
        index=1; changed={value};
      endtask
      always @(posedge a[0]) begin
        index=0; calls=0; cells[0]=3; cells[1]=5;
        put(cells[index_value()]);
        q=(calls<<16)+cells[0]+4*cells[1];
      end
    endmodule'''
    _assert_entries(tmp_path, source, [0,1,2,3], ('q=(calls<<16)+cells[0]+4*cells[1];',))
