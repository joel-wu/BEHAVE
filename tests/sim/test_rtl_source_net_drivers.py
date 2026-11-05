"""Net declaration assignments share the explicit driver's source semantics."""

from dataclasses import replace

import pytest

from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_declarations import _nodes
from behave.sim.rtl.source_ir import SourceCompiler
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_observation_frontend import _installed
from .test_rtl_source_ir import _encode, _run_activation
from .test_rtl_source_locations import _collector, _text

pytestmark = [pytest.mark.eda("slang", "verilator", "make"), pytest.mark.solver]


def _prepare(tmp_path, source, *, enumerate_iterations=True):
    (tmp_path / "design.sv").write_text(source)
    parsed = parse_observation_source(tmp_path, "M", run=_installed)
    assert parsed is not None
    (cst, ast), sensitivity = parsed
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity,
                       enumerate_iterations=enumerate_iterations)
    assert not plan.native_issues
    assert (tmp_path / "design.sv").read_text() == source
    return plan, ast, sensitivity


@pytest.mark.parametrize("expression,expected", [
    ("a", lambda a: a),
    ("a-a", lambda a: 0),
    ("f(0)", lambda a: 255),
    ("a[0] ? f(a) : f(a+1)", lambda a: ((a if a & 1 else a+1) + 1) & 255),
    ("a[0] && f(a)", lambda a: int(bool(a & 1) and bool((a+1) & 255))),
    ("a[0] || f(a)", lambda a: int(bool(a & 1) or bool((a-1) & 255))),
])
def test_net_driver_original_native_ir_and_smt_agree(tmp_path, expression, expected):
    source = f"""module M(input [7:0] a,input noise,output wire [7:0] q);
      function automatic [7:0] f(input [7:0] v);
        if(v[0]) f=v+1; else f=v-1;
      endfunction
      wire [7:0] w={expression};
      assign q=w;
    endmodule"""
    plan, ast, sensitivity = _prepare(tmp_path, source)
    assert sum(node.get("kind") == "ContinuousAssign" for node in _nodes(ast)) == 1
    net_row, = [row for row in sensitivity if row["procedure"] == "NetInitializer"]
    assert source[net_row["start"]:net_row["end"]] == "w=" + expression
    assert {read["symbol"] for read in net_row["reads"]} == (set() if expression == "f(0)" else {"M.a"})
    assert {_text(source, site) for site in plan.declarations.sites_for("location")} >= {
        "w=" + expression, "q=w"}
    context, = [item for item in plan.declarations.contexts if item.process_start == source.index("w=" + expression)]
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    lowered = compiler.lower(context)
    names = {compiler.symbols[identity]["name"]: name for name, identity in lowered.storage}
    encoded = _encode(lowered)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    driver = """
      const unsigned values[]={0,0,1,1,2,255,254,0,0};
      for(unsigned i=0;i<9;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.noise=i&1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver)
    observed = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    values = [0, 0, 1, 1, 2, 255, 254, 0, 0]
    net_occurrences = {occurrence for _, occurrence, _ in lowered.occurrences}
    old = 0
    for index, (value, before, after) in enumerate(zip(values, original, observed, strict=True)):
        assert before == (expected(value), 0, 0)
        assert after[0] == before[0]
        active = index == 0 or expression != "f(0)" and value != values[index-1]
        seen = {bit for bit in range(collector.bindings.event_count) if after[1] >> bit & 1}
        hits = collector.bindings.decode(seen).occurrences & net_occurrences
        if active:
            state = {name: value if name == "a" else old for name in names}
            post, _, symbolic_hits = _run_activation(lowered, names, state, encoded)
            assert post[names["w"]] == expected(value)
            assert hits == symbolic_hits
        else:
            assert not hits  # no callback invented for VPI or unrelated-input activity
        old = expected(value)


def test_multiple_declarators_share_helper_source_not_invocation_identity(tmp_path):
    source = """module M(input [7:0] a,b,output wire [7:0] q);
      function automatic [7:0] f(input [7:0] v);
        if(v[0]) f=v+1; else f=v-1;
      endfunction
      wire [7:0] left=f(a), right=f(b);
      assign q=left+right;
    endmodule"""
    plan, ast, sensitivity = _prepare(tmp_path, source)
    rows = [row for row in sensitivity if row["procedure"] == "NetInitializer"]
    assert {source[row["start"]:row["end"]] for row in rows} == {"left=f(a)", "right=f(b)"}
    assert {tuple(read["symbol"] for read in row["reads"]) for row in rows} == {("M.a",), ("M.b",)}
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    entries = []
    for row in rows:
        context, = [item for item in plan.declarations.contexts if item.process_start == row["start"]]
        lowered = compiler.lower(context)
        entries.append({occurrence for _, occurrence, _ in lowered.occurrences})
    assert not entries[0] & entries[1]
    assert len(plan.declarations.contexts) == 3
    _collector(plan)  # Every declared occurrence has a native producer.


def test_net_function_captured_reads_use_compiler_scope(tmp_path):
    source = """module M(input [7:0] a,b,output wire [7:0] q);
      function automatic [7:0] f(input [7:0] v); f=v+b; endfunction
      wire [7:0] w=f(a);
      assign q=w;
    endmodule"""
    plan, ast, rows = _prepare(tmp_path, source)
    net, = [row for row in rows if row["procedure"] == "NetInitializer"]
    assert {read["symbol"] for read in net["reads"]} == {"M.a", "M.b"}
    context, = [item for item in plan.declarations.contexts if item.process_start == net["start"]]
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    lowered = compiler.lower(context)
    names = {compiler.symbols[identity]["name"]: name for name, identity in lowered.storage}
    encoded = _encode(lowered)
    collector, _ = _collector(plan)
    driver = """
      const unsigned a[]={2,2,2,3}, b[]={0,1,1,1};
      for(unsigned i=0;i<4;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=a[i]; dut.b=b[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    occurrences = {occurrence for _, occurrence, _ in lowered.occurrences}
    for i, (a, b, before, after) in enumerate(zip([2, 2, 2, 3], [0, 1, 1, 1], original, native, strict=True)):
        assert before == (a+b, 0, 0) and after[0] == before[0]
        seen = {bit for bit in range(collector.bindings.event_count) if after[1] >> bit & 1}
        actual = collector.bindings.decode(seen).occurrences & occurrences
        if i == 2:
            assert not actual
        else:
            post, _, hits = _run_activation(lowered, names, {"a": a, "b": b, "w": 0}, encoded)
            assert post[names["w"]] == a+b and actual == hits


@pytest.mark.parametrize("form", ["net", "assign-before", "assign-after"])
def test_net_clock_interaction_preserves_original_scheduling(tmp_path, form):
    process = "always @(posedge clk) if(take) q<=5; else q<=2;"
    if form == "net":
        statements = ["wire take=clk && q==0;", process]
    else:
        driver_source = "assign take=clk && q==0;"
        statements = ["wire take;", *([driver_source, process] if form == "assign-before"
                                      else [process, driver_source])]
    source = "module M(input clk,output reg [3:0] q=0); " + " ".join(statements) + " endmodule"
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    driver = """
      for(unsigned i=0;i<6;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.clk=i&1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert [row[0] for row in original] == [0, 5, 5, 2, 2, 2]
    assert [row[0] for row in native] == [row[0] for row in original]
    for step, (_, bits, _) in enumerate(native):
        seen = {bit for bit in range(collector.bindings.event_count) if bits >> bit & 1}
        hits = collector.bindings.decode(seen).occurrences
        outcomes = {key.role for key in hits if key.path[1] == "control-choice"}
        assert outcomes == ({"true" if step == 1 else "false"} if step & 1 else set())


@pytest.mark.parametrize("declaration", [
    "wire [7:0] w=a", "wire signed [3:0] w=a", "wire [7:0] \\w.special =a",
    "wire (strong1, pull0) [7:0] w=a",
])
def test_net_declared_type_and_identifier_are_preserved(tmp_path, declaration):
    name = "\\w.special " if "special" in declaration else "w"
    source = f"module M(input [7:0] a,output wire [7:0] q); {declaration}; assign q={name}; endmodule"
    plan, ast, rows = _prepare(tmp_path, source)
    net, = [node for node in _nodes(ast) if node.get("kind") == "Net" and "initializer" in node]
    assert net["name"] in {"w", "w.special"}
    collector, _ = _collector(plan)
    assert declaration.split("=", 1)[0] + "=" in plan.native().text
    row, = [item for item in rows if item["procedure"] == "NetInitializer"]
    context, = [item for item in plan.declarations.contexts if item.process_start == row["start"]]
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    lowered = compiler.lower(context)
    names = {compiler.symbols[identity]["name"]: storage for storage, identity in lowered.storage}
    encoded = _encode(lowered)
    driver = """
      const unsigned values[]={0,7,8,15,255};
      for(unsigned i=0;i<5;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    net_occurrences = {occurrence for _, occurrence, _ in lowered.occurrences}
    for value, before, after in zip([0, 7, 8, 15, 255], original, native, strict=True):
        expected = (value & 15) - (16 if value & 8 else 0) if "signed" in declaration else value
        assert before == (expected & 255, 0, 0) and after[0] == before[0]
        post, _, hits = _run_activation(lowered, names,
            {key: value if key == "a" else 0 for key in names}, encoded)
        assert post[names[net["name"]]] == expected
        seen = {bit for bit in range(collector.bindings.event_count) if after[1] >> bit & 1}
        assert collector.bindings.decode(seen).occurrences & net_occurrences == hits
