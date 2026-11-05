"""Caller loop coordinates survive local and nested helper activation frames."""

import subprocess
from dataclasses import replace
from itertools import product

import pytest

from behave.sim.rtl.catalogue import occurrence_key_of, sites_of
from behave.sim.rtl.native_events import prepare_declared_native_events
from behave.sim.rtl.source_bindings import bind_source_events
from behave.sim.rtl.source_loops import StaticLoop, invocation_index
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_source_declarations import _documents
from .test_rtl_source_plan import _compile, _run

pytestmark = pytest.mark.eda("slang")


@pytest.mark.eda("iverilog", "vvp")
@pytest.mark.parametrize("declaration,ranges", [
    ("integer", (range(-3, 4, 2), range(4, -2, -2))),
    ("integer", (range(-2147483647, 1000000000, 1000000000), range(3, 5))),
    ("integer", (range(4, 4), range(2))),
    ("logic [3:0]", (range(8, 12), range(15, 7, -2))),
    ("logic signed [3:0]", (range(-3, 4, 2), range(4, -2, -2))),
])
def test_compact_index_matches_enumeration_and_four_state_default(tmp_path, declaration, ranges):
    loops = tuple(StaticLoop(i, i + 1, f"i{i}", values) for i, values in enumerate(ranges))
    expression = invocation_index(17, loops, ("i0", "i1"))
    arms = "\n".join(f"{{32'h{x & 0xffffffff:08x},32'h{y & 0xffffffff:08x}}}: old={17 + n};"
                     for n, (x, y) in enumerate(product(*ranges)))
    # Include valid, off-stride, out-of-range and X coordinates. The former
    # exact case decoder is an independent four-state execution oracle.
    axes = [tuple(values) + (values.start - 1, values.stop, 2147483647) for values in ranges]
    drives = [f"i0=32'h{x & 0xffffffff:08x}; i1=32'h{y & 0xffffffff:08x}; #1; "
              '$display("%h %h",old,new_id);' for x, y in product(*axes)]
    drives.append("i0='x; i1=2; #1; $display(\"%h %h\",old,new_id);")
    source = (f"module M; {declaration} i0,i1; logic[31:0] old; wire[31:0] new_id=" + expression
              + "; always @* case({32'(i0),32'(i1)})\n" + arms + "\ndefault: old=32'hffffffff; endcase\n"
              + "initial begin\n" + "\n".join(drives) + "\n$finish; end endmodule")
    (tmp_path / "design.sv").write_text(source)
    _run(tmp_path, "iverilog", "-g2012", "-s", "M", "-o", "probe", "design.sv")
    output = subprocess.run(["vvp", "probe"], cwd=tmp_path, check=True,
                            capture_output=True, text=True, timeout=10).stdout
    rows = [line.split() for line in output.splitlines() if not line.startswith("design.sv:")]
    assert len(rows) == len(drives)
    assert all(old == new_id for old, new_id in rows), output
    assert rows[-1] == ["ffffffff", "ffffffff"]


def _source(helper, body="for(i=0;i<4;i++) q[i]<=f(a[i]);"):
    return ("module M(input clk,input [3:0] a,output reg [3:0] q); integer i; "
            + helper + " always @(posedge clk) begin " + body + " end endmodule")


HELPER = "function automatic f(input v); if(v) f=1; else f=0; endfunction\n"


@pytest.mark.parametrize("helper,call,expected", [
    (HELPER, "f(a[i])", True),
    (HELPER.replace("f=1", "f=i[0]"), "f(a[i])", True),  # nonlocal read
    (HELPER.replace("f=1;", "begin i=4; f=1; end"), "f(a[i])", False),
    (HELPER.replace("input v", "inout reg v"), "f(q[i])", False),
    (HELPER.replace("input v", "ref logic [3:0] v"), "f(q)", False),
    (HELPER.replace("automatic ", ""), "f(a[i])", False),
    (HELPER.replace("f=1", "f=$signed(v)"), "f(a[i])", True),
    (HELPER.replace("f=1", "f=$urandom"), "f(a[i])", False),
    (HELPER.replace("f=1", "f=$test$plusargs(\"flag\")"), "f(a[i])", False),
    (HELPER.replace("f=1;", "begin f=1; $fatal; end"), "f(a[i])", False),
    (HELPER + "function automatic g(input v); g=f(v); endfunction", "g(a[i])", True),
    (HELPER.replace("f=1;", "begin i=4; f=1; end")
     + "function automatic g(input v); g=f(v); endfunction", "g(a[i])", False),
    # An inner block's same-spelled local must not conceal a write to module i.
    (HELPER.replace("if(v)", "begin begin : inner integer i; i=0; end i=4; if(v)")
     .replace("endfunction", "end endfunction"), "f(a[i])", False),
    (HELPER.replace("if(v)", "begin integer i; i=0; if(v)")
     .replace("endfunction", "end endfunction"), "f(a[i])", True),
    (HELPER.replace("if(v)", "integer j = (i=4); if(v)"), "f(a[i])", False),
    (HELPER.replace("if(v)", "static integer j; j=4; if(v)"), "f(a[i])", False),
    (HELPER.replace("f=1;", "begin v=0; f=1; end"), "f(a[i])", True),
    # The callee can be frame-local while a caller's argument changes its counter.
    (HELPER, "f(a[i++])", False),
    (HELPER, "f((i=3))", False),
])
def test_literal_loop_requires_resolved_transitive_call_effects(tmp_path, helper, call, expected):
    source = _source(helper, "for(i=0;i<4;i++) q[i]<=" + call + ";")
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    if expected:
        assert not plan.native_issues, plan.native_issues
        assert plan.declarations.frame_local_calls
        plan.native()
    else:
        with pytest.raises(ValueError, match="loop occurrence contexts|helper occurrence contexts"):
            plan.native()


CASES = {
    "direct": (HELPER, "for(i=0;i<4;i++) q[i]<=f(a[i]);"),
    "descending": (HELPER, "for(i=3;i>=0;i--) q[i]<=f(a[i]);"),
    "nested_call": (HELPER + "function automatic g(input v); g=f(v); endfunction",
                    "for(i=0;i<4;i++) q[i]<=g(a[i]);"),
    "local_shadow": (
        ("function automatic f(input v); integer i; begin f=0; "
         "for(i=0;i<2;i++) if(v) f=1; else f=0; end endfunction"),
        "for(i=0;i<4;i++) q[i]<=f(a[i]);"),
    "caller_and_callee_loops": (
        HELPER + "function automatic g(input v); integer i; begin g=0; "
        "for(i=0;i<2;i++) g=f(v); end endfunction",
        "for(i=0;i<4;i++) q[i]<=g(a[i]);"),
    "same_call_different_depth": (
        HELPER + "function automatic g(input v); g=f(v); endfunction",
        "q<=0; q[0]<=g(a[0]); for(i=1;i<4;i++) q[i]<=g(a[i]);"),
}


@pytest.mark.eda("yosys")
@pytest.mark.parametrize("shape", ("direct", "descending", "nested_call", "local_shadow",
                                    "caller_and_callee_loops"))
def test_loop_call_keeps_the_existing_compiler_catalogue(tmp_path, shape):
    helper, body = CASES[shape]
    source = _source(helper, body)
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    bindings = bind_source_events(plan, original, annotated, filename="design.sv")
    assert {entry.occurrence for entry in bindings.occurrences} == {
        occurrence_key_of(site) for site in sites_of(original)}


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("shape", CASES)
def test_native_loop_call_values_and_exact_coordinates(tmp_path, shape):
    helper, body = CASES[shape]
    source = _source(helper, body)
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert not plan.native_issues, plan.native_issues
    collector = prepare_declared_native_events(plan, filename="design.sv",
        toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=len(plan.invocations))
    words = (collector.bindings.event_count + 63) // 64
    driver = f"constexpr unsigned event_words={words};" + """
      for(unsigned a=0;a<16;a++) {
        if(behave_native_events_begin_v1(a)) return 1;
        dut.clk=0; dut.a=a; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits[event_words]={},calls=0;
        if(behave_native_events_end_v1(a,bits,event_words,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" ";
        for(uint64_t word:bits) std::cout<<word<<" ";
        std::cout<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver, event_words=words)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver,
                    native=True, event_words=words)
    assert [row[0] for row in plain] == [row[0] for row in native] == list(range(16))
    assert all(bits == calls == 0 for _, bits, calls in plain)

    # Name events by their literal source construct, not the extracted binding.
    # Every iteration has its own bit even though public source goals aggregate.
    expected = {("process", (), ())}
    outer_call = "g(a[i])" if "g(a[i])" in body else "f(a[i])"
    outer_start = source.index(outer_call, source.index("always"))
    path = ((outer_start, outer_start + len(outer_call)),)
    if "g(a[i])" in body:
        inner = source.index("g=f(v)") + len("g=")
        path += ((inner, inner + len("f(v)")),)
    if shape == "same_call_different_depth":
        direct_start = source.index("g(a[0])")
        direct = ((direct_start, direct_start + len("g(a[0])")), path[-1])
        outer_values = (1, 2, 3)
    else:
        direct = None
        outer_values = (3, 2, 1, 0) if shape == "descending" else (0, 1, 2, 3)
    expected.add(("loop-nonzero", (), ()))
    for a, (_, bits, calls) in enumerate(native):
        want = set(expected)
        for i in outer_values:
            role = "if-true" if a & (1 << i) else "if-false"
            if shape in {"local_shadow", "caller_and_callee_loops"}:
                want.add(("loop-nonzero", (i,), path[:1]))
                want.update((role, (i, j), path) for j in (0, 1))
            else:
                want.add((role, (i,), path))
        if direct:
            want.add(("if-true" if a & 1 else "if-false", (), direct))
        observed = set()
        for index, invocation in enumerate(plan.invocations):
            if bits & (1 << index):
                event = plan.events[invocation.source_event]
                if event.role != "location":
                    observed.add((event.role, invocation.iterations, event.calls))
        assert observed == want, (a, observed, want)
        # The source-owned policy also reports statement entries. Each physical
        # invocation in these fixtures executes once, including those entries.
        assert calls == bits.bit_count()
