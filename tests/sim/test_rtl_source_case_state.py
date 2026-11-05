"""Stateful case evaluation: one selector, ordered and conditional labels."""

import pytest

from .test_rtl_bounded_source_loops import _assert_closure
from .test_rtl_source_activation_scheduling import _assert_entries
from .test_rtl_source_ir import _encode, _lower, _native_agreement, _run_activation

pytestmark = [pytest.mark.eda('slang', 'verilator', 'make'), pytest.mark.solver]


@pytest.mark.eda('iverilog', 'vvp')
@pytest.mark.parametrize('selector', ['value', 'select_value()'])
def test_case_selector_is_captured_before_label_writes(tmp_path, selector):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      logic [7:0] value=0;
      int unsigned calls=0;
      function automatic logic [7:0] select_value();
        calls=calls+1; return value;
      endfunction
      function automatic logic [7:0] label_value();
        value=13; return 1;
      endfunction
      always @(posedge a[0]) begin
        value=a[4:1]; calls=0;
        case({selector})
          label_value():value=value+1;
          2:value=value+2;
          default:value=value+3;
        endcase
        q=value+(calls<<8);
      end
    endmodule'''
    values = [value for a in range(16) for value in (a << 1, (a << 1) | 1)]
    _assert_entries(tmp_path, source, values, ('q=value+(calls<<8);',))


@pytest.mark.eda('iverilog', 'vvp')
@pytest.mark.parametrize('width,signed', [(5, ''), (5, 'signed'), (65, ''), (65, 'signed')])
def test_captured_selector_keeps_width_and_signedness(tmp_path, width, signed):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      logic {signed} [{width-1}:0] value=0;
      function automatic logic {signed} [{width-1}:0] label_value();
        value=13; return -1;
      endfunction
      always @(posedge a[0]) begin
        value=$signed(a[4:1]);
        case(value)
          label_value():q=1;
          2:q=2;
          default:q=3;
        endcase
        q=q+(value[4:0]<<8);
      end
    endmodule'''
    values = [value for a in range(16) for value in (a << 1, (a << 1) | 1)]
    _assert_entries(tmp_path, source, values, ('q=q+(value[4:0]<<8);',))


@pytest.mark.parametrize('labels', [(1,), (1, 2), (1, 1), tuple(range(9))])
def test_every_state_writing_label_goal_has_a_local_answer(tmp_path, record_property, labels):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [3:0] f(input [3:0] x);
        q=q+1;
        if(1'b0) return 0;
        if(x==5) return 5;
        return x;
      endfunction
      always @(posedge clk) begin
        q=0;
        case(a)
          {','.join(f'f({label})' for label in labels)}:q=q+16;
          f(5):q=q+32;
          default:q=q+64;
        endcase
      end
    endmodule'''

    def expected(a):
        if a in labels:
            return labels.index(a) + 1 + 16
        return len(labels) + 1 + (32 if a == 5 else 64)

    _assert_closure(tmp_path, record_property, source, range(16), expected)


@pytest.mark.parametrize('selector', ['q', 'select_value()'])
def test_mutated_selector_goals_keep_the_original_value(tmp_path, record_property, selector):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] select_value();
        select_value=q; q=q+32;
      endfunction
      function automatic [7:0] label_value();
        q=13;
        if(1'b0) return 0;
        return 1;
      endfunction
      always @(posedge clk) begin
        q=a;
        case({selector})
          label_value():q=q+1;
          2:q=q+2;
          default:q=q+3;
        endcase
      end
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 14 if a == 1 else 15 if a == 2 else 16)


def test_label_nonblocking_writes_remain_deferred(tmp_path):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [3:0] f(input [3:0] x);
        q<=q+1;
        return x;
      endfunction
      always @(posedge clk) case(a)
        f(1),f(2):q<=q+16;
        f(5):q<=q+32;
        default:q<=q+64;
      endcase
    endmodule'''
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    write, = lowered.deferred
    for a in range(16):
        post, pending, _ = _run_activation(lowered, names, {'a': a, 'q': 7}, encoded)
        assert post[names['q']] == 7
        assert pending[write.valid] is True
        assert pending[write.data] == 7 + (16 if a in (1, 2) else 32 if a == 5 else 64)
    _native_agreement(tmp_path, source)


def test_output_formal_labels_preserve_selector_and_copyout(tmp_path, record_property):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [3:0] f(input [3:0] x, output logic [3:0] changed);
        changed=13;
        if(1'b0) return 0;
        return x;
      endfunction
      function automatic [7:0] g(input [3:0] x);
        logic [3:0] saved=x;
        case(saved)
          f(1,saved):return saved+1;
          2:return saved+2;
          default:return saved+3;
        endcase
      endfunction
      always @(posedge clk) q=g(a);
    endmodule'''
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: 14 if a == 1 else 15 if a == 2 else 16)
