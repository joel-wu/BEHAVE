"""Pure case matching shares predicates without mutable match bookkeeping."""

import pytest

from behave.sim.symbolic import ir

from .test_rtl_source_ir import _encode, _lower, _native_agreement, _run_activation

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('labels', ['1', '1,2,3'])
def test_pure_case_items_evaluate_one_match_and_keep_their_catalogue(tmp_path, labels):
    source = f'''module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) case(a)
        {labels}: q=5;
        1: q=7;
        default: q=9;
      endcase
    endmodule'''
    lowered, names, _, _ = _lower(tmp_path, source)
    choices = [command for command in ir.walk(lowered.activation) if isinstance(command, ir.If)]
    # Constant-label lookup reads shared predicates directly. Unselectable
    # duplicate arms keep their declared occurrences beneath literal false;
    # neither lookup shape nor a named match-state variable is semantic.
    assert choices and not any(isinstance(command.guard, ir.Var) for command in choices)
    assert all(isinstance(command.guard, (ir.Lit, ir.Op)) for command in choices)
    enc = _encode(lowered)
    selected = {int(value) for value in labels.split(',')}
    declared = {key.role for _, key, _ in lowered.occurrences if key.path[1] == 'control-choice'}
    assert declared == {'arm_0', 'arm_1', 'default'}
    for a in range(16):
        post, _, hits = _run_activation(lowered, names, {'a': a, 'q': 0}, enc)
        assert post[names['q']] == (5 if a in selected else 9)
        assert {key.role for key in hits if key.path[1] == 'control-choice'} == {
            'arm_0' if a in selected else 'default'}


def test_large_flat_case_has_shallow_ir_and_preserves_first_match(tmp_path):
    items = '\n'.join(f"9'd{i}: q=9'd{i ^ 85};" for i in range(300))
    source = f'''module M(input clk,input [8:0] a,output reg [8:0] q);
      always @(posedge clk) case(a)
        {items}
        9'd299:q=9'd511;
        default:q=9'd510;
      endcase
    endmodule'''
    lowered, names, _, _ = _lower(tmp_path, source)
    ir.validate_procedure_representation(lowered.procedure, max_depth=32)
    encoded = _encode(lowered)
    for a in (0, 1, 127, 255, 256, 299, 300, 511):
        post, _, hits = _run_activation(lowered, names, {'a': a, 'q': 0}, encoded)
        assert post[names['q']] == (a ^ 85 if a < 300 else 510)
        assert {key.role for key in hits if key.path[1] == 'control-choice'} == {
            f'arm_{a}' if a < 300 else 'default'}


@pytest.mark.parametrize('items', [
    '1:q=3; 1:q=7; default:q=9;',
    'default:q=9; 1:q=3; 2:q=5;',
    '1:q=3; 2:q=5;',
    '1,2:q=3; 2,4:q=5; default:q=9;',
    "4'sb1111:q=3; 4'b1111:q=5; default:q=9;",
])
@pytest.mark.eda('verilator', 'make')
def test_case_priority_widths_and_no_match_preserve_native_outputs_and_hits(tmp_path, items):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) case(a) {items} endcase
    endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize('labels', ['f(1)', 'f(1),2'])
@pytest.mark.eda('verilator', 'make')
def test_case_label_helpers_preserve_call_occurrences_and_native_hits(tmp_path, labels):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [3:0] f(input [3:0] x);
        if(x[0]) return x;
        return x+1;
      endfunction
      always @(posedge clk) case(a)
        {labels}: q=16;
        5: q=32;
        default: q=64;
      endcase
    endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize('labels', ['f(1)', 'f(1),f(2)'])
@pytest.mark.eda('verilator', 'make')
def test_case_label_writes_preserve_native_outputs_and_hits(tmp_path, labels):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [3:0] f(input [3:0] x);
        q=q+1;
        return x;
      endfunction
      always @(posedge clk) begin
        q=0;
        case(a)
          {labels}: q=q+16;
          f(3): q=q+32;
          default: q=q+64;
        endcase
      end
    endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.eda('verilator', 'make')
def test_case_label_writing_selector_is_not_silently_reordered(tmp_path):
    source = '''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] label_value();
        q=13;
        return 1;
      endfunction
      always @(posedge clk) begin
        q=a;
        case(q)
          label_value(): q=q+1;
          2: q=q+2;
          default: q=q+3;
        endcase
      end
    endmodule'''
    _native_agreement(tmp_path, source)


@pytest.mark.parametrize('items', ['f(1):q=16; f(5):q=32;', 'f(1),f(2):q=16;'])
@pytest.mark.eda('verilator', 'make')
def test_later_label_callbacks_preserve_qualified_short_circuit_order(tmp_path, items):
    source = f'''module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [3:0] f(input [3:0] x);
        if(x[0]) return x;
        return x+1;
      endfunction
      always @(posedge clk) case(a)
        {items}
        default:q=64;
      endcase
    endmodule'''
    _native_agreement(tmp_path, source)
