"""Source locations have independent entry oracles, not RTLIL action counts."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from behave.sim.rtl.native_events import prepare_declared_native_events
from behave.sim.rtl.source_declarations import declare_source
from behave.sim.rtl.source_goals import manifest_of, occurrence_of
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _replay_trace, _trace
from .test_rtl_observation_frontend import _installed
from .test_rtl_source_declarations import _documents, _text
from .test_rtl_source_plan import _run

pytestmark = pytest.mark.eda("slang", "verilator", "make")


def _observation_documents(tmp_path, source):
    from behave.sim.rtl.shared_frontend import parse_observation_source

    (tmp_path / "design.sv").write_text(source)
    parsed = parse_observation_source(tmp_path, "M", run=_installed)
    assert parsed is not None
    documents, _ = parsed
    return documents


def _prepare_clocked_replay(tmp_path, source, plan):
    """Compile a clk/a/q fixture once; each call executes both binaries afresh.

    Native observations use the production occurrence-only plan, independent of
    solver availability or symbolic loop iteration bounds.
    """
    observation = tmp_path / "observation"
    observation.mkdir()
    from behave.sim.rtl.shared_frontend import parse_observation_source

    (observation / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(observation, "M", run=_installed)
    native_plan = plan_source(source, cst, semantic_document=ast,
                              sensitivity_document=sensitivity, enumerate_iterations=False)
    assert manifest_of(native_plan.declarations, filename="design.sv").digest() == manifest_of(
        plan.declarations, filename="design.sv").digest()
    collector, manifest = _collector(native_plan)
    words = (collector.bindings.event_count + 63) // 64
    driver = f"""
      if(behave_native_events_begin_v1(0)) return 1;
      dut.clk=0; dut.a=0; dut.eval();
      uint64_t startup_bits[{words}]={{}},startup_calls=0;
      if(behave_native_events_end_v1(0,startup_bits,{words},&startup_calls)) return 2;
      for(int i=0;i<argc-1;i++) {{
        if(behave_native_events_begin_v1(i+1)) return 1;
        dut.clk=0; dut.a=std::stoul(argv[i+1]); dut.eval();
        dut.clk=1; dut.eval();
        uint64_t bits[{words}]={{}},calls=0;
        if(behave_native_events_end_v1(i+1,bits,{words},&calls)) return 2;
        std::cout<<unsigned(dut.q);
        for(auto word:bits) std::cout<<" "<<word;
        std::cout<<" "<<calls<<"\\n";
      }}
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain_baseline = _trace(tmp_path / "plain", source, flat, driver,
                            event_words=words, run_args=("0",))
    native_baseline = _trace(tmp_path / "native", native_plan.native(scoped=True).text, collector,
                             driver, native=True, event_words=words, run_args=("0",))

    def observations(plain, native):
        assert [row[0] for row in plain] == [row[0] for row in native]
        hits = [collector.bindings.decode({event for event in range(collector.bindings.event_count)
                                          if row[1] >> event & 1}).goals for row in native]
        assert all(observed <= manifest.keys() for observed in hits)
        return [row[0] for row in native], hits

    def replay(values):
        args = tuple(map(str, values))
        plain = _replay_trace(tmp_path / "plain", event_words=words, run_args=args)
        native = _replay_trace(tmp_path / "native", event_words=words, run_args=args)
        return observations(plain, native)

    return *observations(plain_baseline, native_baseline), replay


def _collector(plan):
    manifest = manifest_of(plan.declarations, filename="design.sv")
    collector = prepare_declared_native_events(plan, filename="design.sv",
        toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=1000)
    collector.check_catalogue(manifest)
    return collector, manifest


def _occurrence_meanings(source, plan):
    """Decode every declared occurrence to readable test-oracle coordinates."""
    scopes = {context.key: ".".join(context.scope) for context in plan.declarations.contexts}
    return {
        occurrence_of(site, occurrence, outcome, filename="design.sv"):
        (scopes[occurrence.context], site.schema, _text(source, site), outcome)
        for occurrence in plan.declarations.occurrences
        for site in (plan.declarations.sites[occurrence.site],)
        for outcome in site.outcomes
    }


def _hits(source, manifest, collector, bits):
    seen = {index for index in range(collector.bindings.event_count) if bits >> index & 1}
    hits = collector.bindings.decode(seen).goals
    return {source.encode()[goal.where("start"):goal.where("end")].decode()
            for goal in manifest.values() if goal.family == "location" and goal.id in hits}


def test_declaration_owns_statements_initializers_and_continuous_drivers(tmp_path):
    source = """module M(input clk,a,output logic q,output wire r);
      function automatic logic f(input logic v);
        logic x=v;
        if (1'b0) return !x;
        return x;
      endfunction
      function automatic logic unused(input logic v); return !v; endfunction
      initial q=0;
      always @(posedge clk) q <= f(a);
      assign r=a;
    endmodule"""
    _, ast = _documents(tmp_path, source)
    declarations = declare_source(source, ast, filename="design.sv")
    assert {_text(source, site) for site in declarations.sites_for("location")} == {
        "x", "if (1'b0) return !x;", "return !x;", "return x;", "q <= f(a);", "r=a"}
    assert all(site.outcomes == ("entry",) for site in declarations.sites_for("location"))


def test_catalogue_preparation_needs_no_synthesis_or_native_event_binding(tmp_path):
    from behave.sim.core.catalogue import CatalogueManifest
    from behave.sim.rtl.shared_frontend import declare_catalogue

    source = """module M(input clk,a,output logic q);
      function automatic logic f(input logic v);
        if (1'b0) return !v;
        return v;
      endfunction
      always @(posedge clk) q <= f(a);
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    commands = []

    def run(where, *argv):
        command = (Path(argv[0]).name, *argv[1:])
        commands.append(command)
        _run(where, *command)
        return True

    assert declare_catalogue(tmp_path, "M", run=run)
    assert [command[0] for command in commands] == ["slang"]
    manifest = CatalogueManifest.from_dict(json.loads((tmp_path / "source-goals.json").read_text()))
    assert {goal.family for goal in manifest.values()} == {"location", "control-choice"}
    assert {goal.event for goal in manifest.values() if goal.family == "control-choice"} == {
        "true", "false"}
    assert all(goal.id.startswith("rtl-source:") for goal in manifest.values())
    assert not (tmp_path / "design.rtlil").exists()
    assert not (tmp_path / "native.sv").exists()
    assert (tmp_path / "design.sv").read_text() == source


@pytest.mark.parametrize('name', ['', ': outer'])
def test_process_entry_preserves_original_block_and_automatic_initializer_scope(tmp_path, name):
    from .test_rtl_source_expressions import _driver

    header = f'always @(posedge clk) begin{name}'
    source = f'''module M(input clk,rst,input [3:0] a,output logic [7:0] q);
      {header}
        automatic int first=a, second=first+1;
        q=second;
        if(a[0]) begin: nested q+=3; disable nested; q+=100; end
        q+=2;
      end
    endmodule'''
    cst, ast = _documents(tmp_path / 'parse', source)
    plan = plan_source(source, cst, semantic_document=ast)
    collector, manifest = _collector(plan)
    observed = plan.native(scoped=True).text
    # A wrapper outside this original block changes which block is the
    # process's outermost scope (and hence report cancellation ownership).
    assert header in observed
    assert observed.index('__behave_native_event_block_', observed.index(header)) < observed.index('first=a')
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / 'plain', source, flat, _driver())
    native = _trace(tmp_path / 'native', observed, collector, _driver(), native=True)
    for value, (before, after) in enumerate(zip(plain, native, strict=True)):
        assert before[0] == after[0] == value + 3 + (3 if value & 1 else 0)
        locations = _hits(source, manifest, collector, after[1])
        assert {'first', 'second', 'q=second;', 'q+=2;'} <= locations
        assert 'q+=100;' not in locations


@pytest.mark.parametrize("expression, called", [
    ("f(a)", lambda a: True),
    ("a[0] && f(a)", lambda a: bool(a & 1)),
    ("a[0] || f(a)", lambda a: not a & 1),
    ("f(0)", lambda a: True),
])
def test_statement_entries_follow_actual_lazy_and_constant_calls(tmp_path, expression, called):
    source = f"""module M(input clk,input [3:0] a,output reg [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        int x=v+1,y=x+2;
        if (v[0]) return y;
        return x;
      endfunction
      always @(posedge clk) q <= {expression};
    endmodule"""
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert not plan.native_issues
    collector, manifest = _collector(plan)
    assert collector.bindings.event_count <= 64
    driver = """
      for(unsigned a=0;a<16;a++) {
        if(behave_native_events_begin_v1(a)) return 1;
        dut.clk=0; dut.a=a; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert len(plain) == len(native) == 16
    for a, (before, after) in enumerate(zip(plain, native, strict=True)):
        value = 0 if expression == "f(0)" else a
        result = value + (3 if value & 1 else 1)
        expected = (int(bool(a & 1) and bool(result)) if "&&" in expression else
                    int(bool(a & 1) or bool(result)) if "||" in expression else result)
        assert before == (expected, 0, 0) and after[0] == expected
        locations = {f"q <= {expression};"}
        if called(a):
            locations |= {"x", "y", "if (v[0]) return y;",
                          "return y;" if value & 1 else "return x;"}
        assert _hits(source, manifest, collector, after[1]) == locations
        assert after[2] == (3 + 6 * called(a) if "&&" in expression or "||" in expression else 7)


def test_declaration_entries_preserve_types_initializer_order_and_static_state(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [15:0] q);
      function automatic [15:0] f(input [3:0] v);
        static int count=0;
        typedef logic signed [7:0] small_t;
        small_t x=-v,y=x-1;
        int values[2]='{x,y};
        count++;
        return {values[0][3:0],values[1][3:0],count[7:0]};
      endfunction
      always @(posedge clk) q <= f(a);
    endmodule"""
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    collector, manifest = _collector(plan)
    assert "count" not in {_text(source, site) for site in plan.declarations.sites_for("location")}
    driver = """
      for(unsigned a=0;a<4;a++) {
        if(behave_native_events_begin_v1(a)) return 1;
        dut.clk=0; dut.a=a; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    for a, (before, after) in enumerate(zip(plain, native, strict=True)):
        expected = ((-a & 15) << 12) | ((-a - 1 & 15) << 8) | (a + 1)
        assert before == (expected, 0, 0) and after[0] == expected
        assert _hits(source, manifest, collector, after[1]) == {
            "x", "y", "values", "count++;", "q <= f(a);",
            "return {values[0][3:0],values[1][3:0],count[7:0]};"}
        assert after[2] == 7


def test_combinational_location_entries_use_compiler_dependencies(tmp_path):
    from .test_rtl_continuous_events import _prepare

    source = """module M(input [3:0] a,b,input noise,output logic [7:0] q);
      function automatic [7:0] f(input [3:0] v);
        int x=v+b,y=x+1;
        return y;
      endfunction
      always_comb q=f(a);
    endmodule"""
    plan, collector, _ = _prepare(tmp_path, source)
    manifest = manifest_of(plan.declarations, filename="design.sv")
    collector.check_catalogue(manifest)
    inputs = [(0, 0, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1),
              (0, 1, 0), (1, 1, 0), (1, 3, 0), (1, 3, 0)]
    rows = ",".join("{" + ",".join(map(str, row)) + "}" for row in inputs)
    driver = f"const unsigned inputs[][3]={{{rows}}};" + """
      for(unsigned i=0;i<sizeof(inputs)/sizeof(inputs[0]);i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=inputs[i][0]; dut.b=inputs[i][1]; dut.noise=inputs[i][2]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert len(plain) == len(native) == len(inputs)
    for index, ((a, b, _), before, after) in enumerate(zip(inputs, plain, native, strict=True)):
        assert before == (a + b + 1, 0, 0) and after[0] == before[0]
        active = index == 0 or inputs[index - 1][:2] != (a, b)
        assert _hits(source, manifest, collector, after[1]) == (
            {"q=f(a);", "x", "y", "return y;"} if active else set())
        assert after[2] == (5 if active else 0)
