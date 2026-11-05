"""Typed source read preservation; no fabricated per-slot activation."""

from dataclasses import replace

import pytest

from behave.sim.rtl.native_events import prepare_native_events
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_source_declarations import _documents
from .test_rtl_source_plan import _compile

pytestmark = pytest.mark.eda("slang")


def _plan(directory, source):
    cst, ast = _documents(directory, source)
    return plan_source(source, cst, semantic_document=ast)


def test_reads_share_runtime_closure_but_do_not_change_declared_goals(tmp_path):
    source = '''module M(input logic [3:0] a, output logic [3:0] q);
      parameter K=2;
      function automatic logic [3:0] f(input logic [3:0] x);
        return x-x;
      endfunction
      function automatic logic [3:0] unused(input logic [3:0] x);
        return x+x;
      endfunction
      always_comb begin
        q=f(a) + K;
        q[0]=a[a-a];
        q=q + $bits(unused(a));
      end
    endmodule'''
    plan = _plan(tmp_path, source)
    assert not plan.native_issues
    reads = [source.encode()[r.start:r.end].decode() for r in plan.declarations.dependency_reads]
    assert reads == ["x", "x", "a", "a[a-a]", "a", "a", "q"]
    native = plan.native().text
    assert "q[0]=" in native  # A target is still an lvalue.
    assert "$bits(unused(a))" in native  # No evaluation added to a type query.
    assert "return x+x;" in native  # No traversal into uncalled code.
    assert "type(a[$left(a)])" in native
    assert all(site.schema == "location" for site in plan.declarations.sites)


@pytest.mark.parametrize("process", ["always_comb", "always @*", "always @(*)"])
def test_implicit_processes_have_reads_but_clocked_processes_need_no_anchor(tmp_path, process):
    source = f'''module M(input clk, input [3:0] a, output reg [3:0] q,r);
      {process} q=a-a;
      always @(posedge clk) r=a-a;
    endmodule'''
    plan = _plan(tmp_path, source)
    assert len(plan.declarations.dependency_reads) == 2
    assert "r=a-a;" in plan.native().text


def test_output_arguments_and_unpacked_array_bases_remain_lvalues(tmp_path):
    source = '''module M(input logic [3:0] a, output logic [3:0] q);
      logic [3:0] mem [0:3];
      task automatic store(input logic [3:0] x, output logic [3:0] y);
        y=x-x;
      endtask
      always_comb begin
        store(a,q);
        mem[a[1:0]]=q;
        q=mem[a[1:0]];
      end
    endmodule'''
    plan = _plan(tmp_path, source)
    assert not plan.native_issues
    native = plan.native().text
    # Source calls also forward observation context. The output argument must
    # remain the original lvalue, regardless of those additional arguments.
    assert "store(__behave_native_event_reads::__behave_native_event_read#(type(a))::value(a),q" in native
    reads = [source.encode()[r.start:r.end].decode() for r in plan.declarations.dependency_reads]
    assert "mem" not in reads and "mem[a[1:0]]" in reads
    # Validate the generated program's assignment and call types independently.
    _documents(tmp_path / "native-parse", native)


def test_native_read_metadata_does_not_change_the_source_catalogue(tmp_path):
    from behave.sim.rtl.source_goals import manifest_of

    source = '''module M(input logic [3:0] a, output logic [3:0] q);
      always_comb if(a-a) q=3; else q=1;
    endmodule'''
    declarations = _plan(tmp_path, source).declarations
    assert declarations.dependency_reads
    site, = declarations.sites_for("control-choice")
    assert site.schema == "control-choice" and site.outcomes == ("true", "false")
    manifest = manifest_of(declarations, filename="design.sv")
    # Even an unavailable observer cannot shrink the source denominator.
    unavailable = replace(declarations, dependency_reads=(), read_issues=("unavailable",))
    assert manifest.digest() == manifest_of(unavailable, filename="design.sv").digest()
    assert len(manifest) == 5  # two outcomes plus three statement entries


@pytest.mark.eda("yosys", "verilator", "make")
@pytest.mark.parametrize("shape", [
    "signed", "wide", "selected", "helper", "return-helper", "counter", "parameters", "post-index", "enum"])
def test_typed_dependency_reads_preserve_values_and_change_events(tmp_path, shape):
    bodies = {
        "signed": ("input signed [3:0] a", "output reg signed [31:0] q", "",
                   "always_comb q=(a-a)+a;"),
        "wide": ("input [3:0] a", "output reg [31:0] q", "",
                 "always_comb q=(a-a)+(a<<20);"),
        "selected": ("input [3:0] a", "output reg [3:0] q", "",
                     "always_comb q=(a[1:0]-a[1:0]) + a[a-a];"),
        "helper": ("input [3:0] a", "output reg [3:0] q",
                   "function automatic [3:0] f(input [3:0] v); f=v-v; endfunction",
                   "always_comb q=f(a);"),
        "return-helper": ("input [3:0] a", "output reg [3:0] q",
                          "function automatic [3:0] f(input [3:0] v); return v-v; endfunction",
                          "always_comb q=f(a);"),
        "counter": ("input [3:0] a", "output reg [3:0] q", "integer i;",
                    "always_comb for(i=0;i<4;i++) q[i]=a[i]^a[i];"),
        "parameters": ("input [3:0] a", "output wire [3:0] q", "",
                       "Inner #(1) u(a[0],q[0]); Inner #(3) v(a[3:1],q[3:1]);"),
        "post-index": ("input [3:0] a", "output reg [3:0] q", "integer i;",
                       "always_comb begin i=a[0]; q=a[(i++) +: 2]; q=q+(i<<2); end"),
        "enum": ("input [3:0] a", "output reg [3:0] q",
                 "typedef enum logic [3:0] {ZERO=0, ONE=1} Mode; Mode mode;",
                 "always_comb begin mode=a[0] ? ONE : ZERO; q=(mode-mode)+mode; end"),
    }
    inp, out, helper, body = bodies[shape]
    source = f"module M({inp},{out}); {helper} {body} endmodule"
    if shape == "parameters":
        source += '''
        module Inner #(parameter W=2)(input [W-1:0] a, output reg [W-1:0] q);
          always_comb q=a-a;
        endmodule'''
    plan = _plan(tmp_path, source)
    assert not plan.native_issues
    # These value/dependency checks need no hierarchy-sensitive goal projection.
    # Event context projection is tested separately by the native context suite.
    if shape in {"parameters", "post-index", "enum", "return-helper"}:
        from behave.sim.rtl.native_events import prepare_declared_native_events
        collector = prepare_declared_native_events(plan, filename="design.sv",
            toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=1000)
        native_text = plan.native(scoped=True).text
    else:
        original = _compile(tmp_path / "original", source)
        annotated = _compile(tmp_path / "annotated", plan.compiler.text)
        collector = prepare_native_events(plan, original, annotated, filename="design.sv",
            toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=1000)
        native_text = plan.native().text
    driver = r'''
      for (unsigned i=0;i<32;++i) {
        if (behave_native_events_begin_v1(i)) return 1;
        dut.a=i/2; dut.eval();
        uint64_t bits=0,calls=0;
        if (behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\n";
      }
    '''
    plain_collector = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, plain_collector, driver)
    native = _trace(tmp_path / "native", native_text, collector, driver, native=True)
    assert [q for q, _, _ in native] == [q for q, _, _ in plain]
    if shape == "signed":
        assert [q for q, _, _ in native] == [
            ((i // 2) if i < 16 else (i // 2) - 16) & 0xffffffff for i in range(32)]
    elif shape == "wide":
        assert [q for q, _, _ in native] == [(i // 2) << 20 for i in range(32)]
    elif shape == "post-index":
        assert [q for q, _, _ in native] == [
            ((a >> (a & 1)) & 3) + (((a & 1) + 1) << 2) for a in range(16) for _ in range(2)]
    elif shape == "enum":
        assert [q for q, _, _ in native] == [a & 1 for a in range(16) for _ in range(2)]
    assert all(bits == calls == 0 for _, bits, calls in native[1::2])
    assert all(calls > 0 for _, _, calls in native[::2]), native
