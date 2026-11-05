"""Typed packed values retain bit order, signed bits and source occurrences."""

from dataclasses import replace

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_combinational_events import _trace
from .test_rtl_source_ir import _encode, _lower, _run_activation
from .test_rtl_source_locations import _collector

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.parametrize("expression,expected", [
    ("{4'sb1110,a}", lambda a: 224 | a),
    ("{a,4'sb1110}", lambda a: (a << 4) | 14),
    ("{a[1:0],a[3:2],a}", lambda a: ((a & 3) << 6) | ((a >> 2) << 4) | a),
    ("{a[0] ? 4'sb1110 : 4'sb0011,a}", lambda a: ((14 if a & 1 else 3) << 4) | a),
])
def test_concatenation_keeps_self_determined_signed_operand_bits(tmp_path, expression, expected):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) q={expression}; endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(16):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 99}, encoded)
        assert post[names["q"]] == expected(a)


@pytest.mark.parametrize("declaration,selection,shift", [
    ("[11:4]", "9:6", 2), ("[4:11]", "6:9", 2),
    ("[11:4]", "6+:4", 2), ("[4:11]", "6+:4", 2),
    ("[11:4]", "9-:4", 2), ("[4:11]", "9-:4", 2),
    ("[1:0][3:0]", "1:1", 4), ("[0:1][3:0]", "0:0", 4),
])
def test_constant_part_select_uses_declared_direction_and_element_width(tmp_path, declaration, selection, shift):
    source = f"""module M(input clk,input {declaration} a,output reg [3:0] q);
      always @(posedge clk) q=a[{selection}]; endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in (0, 1, 15, 16, 42, 85, 127, 128, 170, 254, 255):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 7}, encoded)
        assert post[names["q"]] == (a >> shift) & 15


def test_undefined_dynamic_bits_are_not_zero_filled(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) q=a[a+:4]; endmodule"""
    with pytest.raises(Refused) as refused:
        _lower(tmp_path, source)
    assert refused.value.refusal.kind == "rtl-source-ir-packed-read-domain-unproved"


@pytest.mark.parametrize('ascending', [False, True])
def test_large_constant_table_encodes_only_the_selected_window(tmp_path, ascending):
    # More than the scalar carrier limit, but every selected word is 32 bits.
    width = 16384
    words = [(0x96a53c7e + index * 0x632be59b) & 0xffffffff for index in range(width // 32)]
    bits = sum(word << (32 * index) for index, word in enumerate(words))
    declaration = f'[0:{width-1}]' if ascending else f'[{width-1}:0]'
    selection = f'{width}-32-a*32+:32' if ascending else 'a*32+:32'
    source = f'''module M(input clk,input [8:0] a,output logic signed [63:0] q);
      localparam logic {declaration} TABLE = {width}'h{bits:x};
      always @(posedge clk) q=$signed(TABLE[{selection}]);
    endmodule'''
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in (0, 1, 255, 511):
        post, _, _ = _run_activation(lowered, names, {'a': a, 'q': 0}, encoded)
        expected = words[a] - (1 << 32) if words[a] & (1 << 31) else words[a]
        assert post[names['q']] == expected


def test_constant_table_keeps_out_of_range_selection_refusal(tmp_path):
    source = '''module M(input clk,input [5:0] a,output logic [7:0] q);
      localparam logic [31:0] TABLE=32'h96a53c7e;
      always @(posedge clk) q=TABLE[a+:8];
    endmodule'''
    with pytest.raises(Refused, match='packed-read-domain-unproved'):
        _lower(tmp_path, source)


@pytest.mark.eda('verilator', 'make')
@pytest.mark.parametrize('ascending', [False, True])
def test_constant_windows_keep_selector_effects_and_native_events(tmp_path, ascending):
    from .test_rtl_source_ir import _native_agreement

    declaration = '[0:130]' if ascending else '[130:0]'
    selection = '130-5-take(a)+:6' if ascending else 'take(a)+:6'
    source = f'''module M(input clk,input [3:0] a,output logic [7:0] q);
      localparam logic {declaration} TABLE=131'h5e496a53c7e192b4d5fca71235398e13b;
      function automatic int take(input [3:0] v);
        q=q+1; return v*8+3;
      endfunction
      always @(posedge clk) begin
        logic [5:0] selected;
        q=0;
        selected=TABLE[{selection}];
        q=q+selected;
      end
    endmodule'''
    _native_agreement(tmp_path, source, initial={'selected': 0})


def test_constant_selector_bounds_have_no_spurious_symbolic_occurrences(tmp_path):
    source = """module M #(parameter W=8)(input clk,input [7:0] a,output reg [3:0] q);
      always @(posedge clk) q=a[(W < 4 ? W : 4)-1:0]; endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in (0, 1, 15, 17, 255):
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, encoded)
        assert post[names["q"]] == a & 15


@pytest.mark.parametrize("literal,expected", [("-4'sd2", -2), ("-4'sd8", -8),
                                             ("4'shf", -1), ("4'hf", 15)])
def test_compiler_signed_integer_serialization(literal, expected):
    from behave.sim.rtl.source_integral import integer_literal

    assert integer_literal(literal) == expected


@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("operator", ["<<", "<<<", ">>", ">>>"])
def test_source_shifts_use_unsigned_count_bits_and_typed_left_value(tmp_path, signed, operator):
    qualifier = "signed" if signed else ""
    source = f"""module M(input clk,input {qualifier} [7:0] a,input signed [3:0] n,
      output reg {qualifier} [7:0] q); always @(posedge clk) q=a {operator} n; endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    values = (-128, -1, 0, 1, 127) if signed else (0, 1, 127, 128, 255)
    for a in values:
        for n in (-8, -1, 0, 1, 7):
            count = n & 15
            raw = ((a << count) if operator in {"<<", "<<<"} else
                   (a >> count) if operator == ">>>" and signed else (a & 255) >> count)
            expected = raw & 255
            if signed and expected >= 128:
                expected -= 256
            post, _, _ = _run_activation(lowered, names, {"a": a, "n": n, "q": 0}, encoded)
            assert post[names["q"]] == expected


@pytest.mark.parametrize("operator", ["&", "~&", "|", "~|", "^", "~^"])
def test_source_reductions_keep_signed_operand_bits(tmp_path, operator):
    source = f"""module M(input clk,input signed [3:0] a,output reg q);
      always @(posedge clk) q={operator}a; endmodule"""
    lowered, names, _, _ = _lower(tmp_path, source)
    encoded = _encode(lowered)
    for a in range(-8, 8):
        bits = a & 15
        expected = int(bits == 15) if "&" in operator else int(bits != 0) if "|" in operator else bits.bit_count() % 2
        if operator.startswith("~"):
            expected ^= 1
        post, _, _ = _run_activation(lowered, names, {"a": a, "q": 0}, encoded)
        assert post[names["q"]] == expected


@pytest.mark.eda("verilator", "make")
def test_packed_expression_hits_and_values_match_unmodified_native_execution(tmp_path):
    source = """module M(input clk,rst,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) begin
        q={a[3:2],a[1:0],4'sb1110};
        if(q[7:4]==9) q={a,4'sb0011};
      end endmodule"""
    lowered, names, cst, ast = _lower(tmp_path, source)
    encoded = _encode(lowered)
    from behave.sim.rtl.source_plan import plan_source

    plan = plan_source(source, cst, semantic_document=ast)
    collector, _ = _collector(plan)
    transport = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    driver = '''
      dut.rst=0;
      for(unsigned a=0;a<16;a++) {
        if(behave_native_events_begin_v1(a)) return 1;
        dut.clk=0; dut.a=a; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    plain = _trace(tmp_path / "plain", source, transport, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    for a, (untouched, observed) in enumerate(zip(plain, native, strict=True)):
        post, _, hits = _run_activation(lowered, names, {"a": a, "q": 0}, encoded)
        assert untouched[0] == observed[0] == post[names["q"]]
        events = {i for i in range(collector.bindings.event_count) if observed[1] & (1 << i)}
        assert collector.bindings.decode(events).occurrences == hits
