"""Original simulator results vs typed operation capture, not compiler-only tests."""

import subprocess

import pytest

from behave.sim.core.refusal import Refused
from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_declarations import _nodes
from behave.sim.rtl.source_edits import Insertion, Replacement, SourceEdits
from behave.sim.rtl.source_integral import typed_symbols
from behave.sim.rtl.source_operation_events import _BINARY, _UNARY, CaptureType, OperationCapture

from .test_rtl_observation_frontend import _installed
from .test_rtl_source_plan import _run


def capture_expression(source, node, symbols, span, *, name, callback, scope="0"):
    """Independent whole-expression capture oracle, never a production route.

    It exposes actual operand values for the arithmetic oracles below. Runtime
    SourcePlan instead composes token edits with all structural/read probes;
    replacing a whole expression there would destroy those probes.
    """
    raw, definitions = source.encode(), []

    def children(value):
        if isinstance(value, list):
            for child in value:
                yield from children(child)
        elif isinstance(value, dict):
            if "source_file_start" in value:
                yield value
            else:
                for key, child in value.items():
                    if key not in {"type", "elementType", "netType"}:
                        yield from children(child)

    def render(current, root=False):
        kind, operation = current.get("kind"), current.get("op")
        eager = ((kind == "BinaryOp" and operation in _BINARY)
                 or (kind == "UnaryOp" and operation in _UNARY))
        if eager:
            operands = ((current["left"], current["right"]) if kind == "BinaryOp"
                        else (current["operand"],))
            arguments = tuple(render(operand) for operand in operands)
            capture = OperationCapture.from_node(current, symbols)
            function = name if root else f"{name}_value_{len(definitions)}"
            definitions.append(capture.function(function, callback if root else None))
            return capture.call(function, arguments, scope=scope)
        assert not root
        start, end = span(current)
        edits = []
        for key, value in current.items():
            if key in {"type", "elementType", "netType"}:
                continue
            for child in children(value):
                first, last = span(child)
                if (first, last) == (start, end):
                    return render(child)
                edits.append((first, last, render(child)))
        parts, cursor = [], start
        for first, last, text in sorted(edits):
            assert cursor <= first <= last <= end
            parts.extend((raw[cursor:first].decode(), text))
            cursor = last
        parts.append(raw[cursor:end].decode())
        return "".join(parts)

    return render(node, root=True), "".join(definitions)


@pytest.mark.parametrize("kind,operation", [
    ("BinaryOp", "LogicalAnd"), ("BinaryOp", "LogicalOr"),
    ("UnaryOp", "Postincrement"), ("UnaryOp", "Predecrement"),
    ("Assignment", None), ("ConditionalOp", None),
])
def test_lazy_and_mutating_operations_do_not_use_an_eager_capture(kind, operation):
    with pytest.raises(Refused) as caught:
        OperationCapture.from_node({"kind": kind, "op": operation}, {})
    assert caught.value.refusal.kind == "rtl-operation-capture-unsupported"


def _signed4(x):
    return x - 16 if x >= 8 else x


def _divide(a, b):
    return 0 if b == 0 else abs(a) // abs(b) * (-1 if (a < 0) != (b < 0) else 1)


# Expected values are mathematical oracles, not re-evaluation of generated HDL.
_CASES = (
    ("a + b", lambda a, b: a + b, lambda a, b: (a, b)),
    ("sa + sb", lambda a, b: _signed4(a) + _signed4(b), lambda a, b: (_signed4(a), _signed4(b))),
    ("sa + b", lambda a, b: a + b, lambda a, b: (a, b)),
    ("sa >>> b", lambda a, b: _signed4(a) >> b, lambda a, b: (_signed4(a), b)),
    ("a << b", lambda a, b: a << b, lambda a, b: (a, b)),
    ("a ** b", lambda a, b: a ** b, lambda a, b: (a, b)),
    ("sa < b", lambda a, b: int(a < b), lambda a, b: (a, b)),
    ("sa < sb", lambda a, b: int(_signed4(a) < _signed4(b)), lambda a, b: (_signed4(a), _signed4(b))),
    ("sa / sb", lambda a, b: _divide(_signed4(a), _signed4(b)), lambda a, b: (_signed4(a), _signed4(b))),
    ("sa % sb", lambda a, b: 0 if not b else _signed4(a) - _divide(_signed4(a), _signed4(b)) * _signed4(b),
     lambda a, b: (_signed4(a), _signed4(b))),
    ("~a", lambda a, b: ~a, lambda a, b: (a, 0)),
    ("!a", lambda a, b: int(not a), lambda a, b: (a, 0)),
    ("^a", lambda a, b: a.bit_count() % 2, lambda a, b: (a, 0)),
    ("-sa", lambda a, b: -_signed4(a), lambda a, b: (_signed4(a), 0)),
    ("sa * sb", lambda a, b: _signed4(a) * _signed4(b), lambda a, b: (_signed4(a), _signed4(b))),
    ("(sa * sb) + a", lambda a, b: a * b + a, lambda a, b: (a * b, a)),
    ("(sa * sb) + sa", lambda a, b: _signed4(a) * _signed4(b) + _signed4(a),
     lambda a, b: (_signed4(a) * _signed4(b), _signed4(a))),
    ("8'(sa) + b", lambda a, b: (_signed4(a) % 256) + b, lambda a, b: (_signed4(a) % 256, b)),
    ("$signed(a) + sb", lambda a, b: _signed4(a) + _signed4(b), lambda a, b: (_signed4(a), _signed4(b))),
    ("(a ? sa*sb : sa+sb) + a", lambda a, b: (a * b if a else a + b) + a,
     lambda a, b: (a * b if a else a + b, a)),
    ("(sa >>> b) + a", lambda a, b: (a >> b) + a, lambda a, b: (a >> b, a)),
    ("(a && (sa+sb)) + b", lambda a, b: int(bool(a) and bool((a + b) % 16)) + b,
     lambda a, b: (int(bool(a) and bool((a + b) % 16)), b)),
)


def _fixture():
    ports = ",".join(f"output logic [15:0] q{i}" for i in range(len(_CASES)))
    assignments = "\n".join(f"q{i} <= ({expression});" for i, (expression, _, _) in enumerate(_CASES))
    return f"""module M(input clk, en, input logic [3:0] a,b,
      input logic signed [3:0] sa,sb, {ports});
      always @(posedge clk) if(en) begin
        {assignments}
      end
    endmodule
"""


def _compile_and_run(where, source, observed):
    where.mkdir()
    (where / "design.sv").write_text(source)
    print_values = "".join(f' << " " << unsigned(dut.q{i})' for i in range(len(_CASES)))
    (where / "probe.cpp").write_text("""#include "VM.h"
#include "verilated.h"
#include <iostream>
#include <cstdint>
extern "C" void record_operation(int site, uint64_t left, uint64_t right,
                                 uint64_t result, unsigned scope) {
  std::cout << "event " << site << " " << left << " " << right
            << " " << result << " " << scope << "\\n";
}
int main(int argc,char** argv) {
  Verilated::commandArgs(argc,argv); VM dut;
  for(unsigned a=0;a<16;++a) for(unsigned b=0;b<16;++b) {
    // Every pair has an enabled event followed by a skipped activation.
    for (unsigned enabled: {1u,0u}) {
      std::cout << "input " << a << " " << b << " " << enabled << "\\n";
      dut.clk=0; dut.en=enabled; dut.a=a; dut.b=b; dut.sa=a; dut.sb=b;
      dut.eval(); dut.clk=1; dut.eval();
      std::cout << "output" @PRINT_VALUES@ << "\\n";
    }
  }
  dut.final(); return 0;
}
""".replace("@PRINT_VALUES@", print_values))
    _run(where, "verilator", "--cc", "--exe", "--build", "-j", "1", "-Wno-fatal",
         "--top-module", "M", "design.sv", "probe.cpp", timeout_s=90)
    output = subprocess.run([str(where / "obj_dir/VM")], check=True, capture_output=True,
                            text=True, timeout=10).stdout
    rows, events, current = [], [], None
    for line in output.splitlines():
        fields = line.split()
        if fields[0] == "input":
            current = tuple(map(int, fields[1:]))
        elif fields[0] == "event":
            assert observed
            events.append((current, tuple(map(int, fields[1:]))))
        else:
            assert fields[0] == "output"
            rows.append((current, tuple(map(int, fields[1:]))))
    return rows, events


@pytest.mark.eda("slang", "verilator", "make")
def test_contextual_operand_widths_results_and_actual_execution_are_preserved(tmp_path):
    source = _fixture()
    original_path = tmp_path / "parse"
    original_path.mkdir()
    (original_path / "design.sv").write_text(source)
    parsed = parse_observation_source(original_path, "M", run=_installed)
    assert parsed is not None
    (_, document), _ = parsed
    symbols = typed_symbols(document)
    raw = source.encode()
    positions = SourceEdits(source, ())

    def span(node):
        assert node["source_file_start"] == node["source_file_end"] == "design.sv"
        return tuple(positions._source_lines[node[f"source_line_{end}"] - 1]
                     + node[f"source_column_{end}"] - 1 for end in ("start", "end"))

    nodes = [node for node in _nodes(document) if node.get("kind") in {"BinaryOp", "UnaryOp"}]
    replacements, definitions, plans = [], [], []
    for i, (expression, _expected, _operands) in enumerate(_CASES):
        candidates = [node for node in nodes if node["source_line_start"] == i + 4
                      and raw[slice(*span(node))].decode() == expression]
        node, = candidates
        capture = OperationCapture.from_node(node, symbols)
        plans.append(capture)
        operands = ((node["left"], node["right"]) if node["kind"] == "BinaryOp"
                    else (node["operand"],))
        name, callback = f"__capture_{i}", f"__report_{i}"
        # The callback uses wide signed carriers to expose the effective values.
        # It observes only; it never repeats the arithmetic or writes DUT state.
        right = "right" if len(operands) == 2 else "0"
        parameters = "input longint left," + (" input longint right," if len(operands) == 2 else "")
        definitions.append(f"""function automatic void {callback}(
          {parameters} input longint result, input int unsigned scope);
          record_operation({i}, left, {right}, result, scope);
        endfunction
""")
        expression, functions = capture_expression(
            source, node, symbols, span, name=name, callback=callback, scope="37")
        definitions.append(functions)
        replacements.append(Replacement(*span(node), expression))
    prefix = 'import "DPI-C" function void record_operation(input int site, input longint left, right, result, input int unsigned scope);\n'
    generated = SourceEdits(source, (Insertion(source.index("always"), prefix + "".join(definitions)),),
                            tuple(sorted(replacements, key=lambda edit: edit.start)))
    plain, plain_events = _compile_and_run(tmp_path / "plain", source, False)
    observed, events = _compile_and_run(tmp_path / "observed", generated.text, True)
    assert observed == plain
    assert not plain_events
    for (a, b, enabled), outputs in observed:
        assert outputs == tuple(expected(a, b) & 65535 for _, expected, _ in _CASES)
    assert len(events) == 256 * len(_CASES)
    by_input = {}
    for (a, b, enabled), (i, left, right, result, scope) in events:
        assert enabled == 1 and scope == 37
        by_input.setdefault((a, b), []).append(i)
        # Complete output comparison above is independent; these assertions also
        # check the observed result is the once-executed, typed operation value.
        result_type = plans[i].result
        bits = _CASES[i][1](a, b) % (1 << result_type.width)
        value = bits - (1 << result_type.width) if (
            result_type.signed and bits & (1 << (result_type.width-1))) else bits
        assert result == value % (1 << 64)
        assert (left, right) == tuple(value % (1 << 64) for value in _CASES[i][2](a, b))
    assert all(indices == list(range(len(_CASES))) for indices in by_input.values())


@pytest.mark.eda("iverilog", "vvp")
@pytest.mark.parametrize("four_state", (False, True))
def test_four_state_values_and_effectful_operands_are_not_recomputed(tmp_path, four_state):
    value_type = CaptureType(4, False, four_state=four_state)
    capture = OperationCapture("+", (value_type, value_type), value_type)
    common = """module M;
    logic [3:0] a,b,q;
    int left_calls=0, right_calls=0;
    function automatic logic [3:0] left(); left_calls++; return a; endfunction
    function automatic logic [3:0] right(); right_calls++; return b; endfunction
    function automatic void observe(input logic [3:0] l,r,value, input int unsigned scope);
      $display("values %b %b %b %d", l,r,value,scope);
    endfunction
    @DEFINITION@
    initial begin
      a=4'b10x1; b=1; @OPERATION@
      a=4'bz001; b=1; @OPERATION@
      a=3; b=7; @OPERATION@
      a=15; b=1; @OPERATION@
      $display("calls %d %d", left_calls, right_calls);
      $finish;
    end
    endmodule
"""
    if not four_state:
        common = common.replace("logic [3:0] a,b,q;", "bit [3:0] a,b,q;")
        common = common.replace("function automatic logic [3:0]", "function automatic bit [3:0]")
    outputs = []
    for name in ("plain", "observed"):
        observed = name == "observed"
        definition = capture.function("sample", "observe") if observed else ""
        operation = ("q=" + capture.call("sample", ("left()", "right()"), scope="37") + ";"
                     if observed else "q=left()+right(); observe(a,b,q,37);")
        source = common.replace("@DEFINITION@", definition).replace("@OPERATION@", operation)
        where = tmp_path / name
        where.mkdir()
        (where / "design.sv").write_text(source)
        _run(where, "iverilog", "-g2012", "-s", "M", "-o", "sim", "design.sv")
        result = subprocess.run(["vvp", "sim"], cwd=where, check=True, text=True,
                                capture_output=True, timeout=10)
        outputs.append([line.split() for line in result.stdout.splitlines()
                        if line.startswith(("values", "calls"))])
    assert outputs[0] == outputs[1] == [
        ["values", "10x1", "0001", "xxxx", "37"] if four_state
        else ["values", "1001", "0001", "1010", "37"],
        ["values", "z001", "0001", "xxxx", "37"] if four_state
        else ["values", "0001", "0001", "0010", "37"],
        ["values", "0011", "0111", "1010", "37"],
        ["values", "1111", "0001", "0000", "37"],
        ["calls", "4", "4"],
    ]
