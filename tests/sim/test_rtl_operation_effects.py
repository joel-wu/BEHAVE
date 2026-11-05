"""Bound operand effects: original values/state, native occurrences and IR/SMT."""

import json
from dataclasses import replace

import pytest

from behave.sim.rtl.source_declarations import _nodes
from behave.sim.rtl.source_goals import goal_of
from behave.sim.rtl.source_ir import lower_source_process

from .test_rtl_combinational_events import _trace
from .test_rtl_operation_rules import _plans, _rule
from .test_rtl_source_ir import _encode, _run_activation
from .test_rtl_source_locations import _collector

_HELPERS = '''
  function automatic [15:0] advance_x(input [3:0] v);
    x++; return {8'b0,x}+v;
  endfunction
  function automatic [15:0] advance_y(input [3:0] v);
    y++; return {8'b0,y}+v;
  endfunction
  function automatic [15:0] relay(input [3:0] v);
    return advance_x(v);
  endfunction
  function automatic [15:0] read_x(input [3:0] v);
    return {8'b0,x}+v;
  endfunction
'''


@pytest.mark.eda("slang")
@pytest.mark.parametrize("expression,why", [
    ("(x++)+x", "conflicting source storage"),
    ("advance_x(a)+read_x(b)", "conflicting source storage"),
    ("relay(a)+advance_x(b)", "conflicting source storage"),
    ("advance_x(a)+$urandom()", "unbound source effects"),
])
def test_unsequenced_conflicts_and_unknown_effects_retain_declared_goals(tmp_path, expression, why):
    source = f'''module M(input clk,input [3:0] a,b,output logic [15:0] q);
      logic[7:0] x=0,y=0;
      {_HELPERS}
      always @(posedge clk) q={expression};
    endmodule'''
    plain, observed = _plans(tmp_path / "parse", source, (_rule(),))
    assert plain.native().text
    assert observed.declarations.sites_for("operation-event")
    with pytest.raises(ValueError, match=why):
        observed.native()


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.solver
@pytest.mark.parametrize("expression,mode", [
    ("(x++)+a", "one"),
    ("(x++)+(y++)", "both"),
    ("advance_x(a)+advance_y(b)", "helpers"),
    ("relay(a)+(y++)", "transitive"),
    ("(a ? advance_x(0) : advance_y(0))+b", "conditional"),
    ("a && (advance_x(0)+b)", "short-circuit"),
])
def test_commuting_operands_preserve_state_values_and_per_activation_hits(tmp_path, expression, mode):
    source = f'''module M(input clk,en,input [3:0] a,b,output [31:0] q);
      logic[7:0] x=250,y=254; logic[15:0] value=0;
      {_HELPERS}
      always @(posedge clk) if(en) value={expression};
      assign q={{x,y,value}};
    endmodule'''
    _, plan = _plans(tmp_path / "parse", source, (_rule(),))
    assert not plan.native_issues
    collector, _ = _collector(plan)
    assert collector.bindings.event_count <= 64
    driver = '''
    for(unsigned pair=0;pair<256;pair++) for(unsigned en: {1u,0u}) {
      if(behave_native_events_begin_v1(2*pair+1-en)) return 1;
      dut.clk=0; dut.en=en; dut.a=pair&15; dut.b=pair>>4; dut.eval();
      dut.clk=1; dut.eval();
      uint64_t bits=0,calls=0;
      if(behave_native_events_end_v1(2*pair+1-en,&bits,1,&calls)) return 2;
      std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    }
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert [row[0] for row in plain] == [row[0] for row in native]
    ast = json.loads((tmp_path / "parse" / "frontend.ast.json").read_text())
    context, = [context for context in plan.declarations.contexts
                if context.process_start == source.index("always")]
    lowered = lower_source_process(source, ast, plan, context, filename="design.sv")
    symbols = {str(node["addr"]): node for node in _nodes(ast) if "addr" in node}
    names = {symbols[identity]["name"]: name for name, identity in lowered.storage}
    encoded = _encode(lowered)
    own_occurrences = {occurrence for _, occurrence, _ in lowered.occurrences}
    operation_text = "advance_x(0)+b" if mode == "short-circuit" else expression
    operation_start = source.index(operation_text, source.index("always"))
    operation_sites = {site.start for site in plan.declarations.sites_for("operation-event")
                       if site.start == operation_start}
    assert operation_sites == {operation_start}
    # Independent hit oracle for the outer addition, not native/IR agreement
    # alone. The disabled/short-circuited steps must not inherit an earlier hit.
    target_ids = {goal_of(site, "target", filename="design.sv").id
                  for site in plan.declarations.sites_for("operation-event")
                  if site.start == operation_start}
    target_occurrences = {occurrence for _, occurrence, goal in lowered.occurrences if goal in target_ids}
    assert target_occurrences
    x, y, value = 250, 254, 0
    for index, (output, bits, _) in enumerate(native):
        pair, enabled = index // 2, 1 - index % 2
        a, b = pair & 15, pair >> 4
        before = {"x": x, "y": y, "value": value, "a": a, "b": b, "en": enabled, "clk": 1}
        operation_result = None
        if enabled:
            old_x, old_y = x, y
            if mode not in {"conditional", "short-circuit"} or a:
                x = (x+1) & 255
            if mode in {"both", "helpers", "transitive"} or mode == "conditional" and not a:
                y = (y+1) & 255
            operation_result = {"one": old_x+a, "both": old_x+old_y,
                                "helpers": x+a+y+b, "transitive": x+a+old_y,
                                "conditional": (x if a else y)+b,
                                "short-circuit": x+b if a else None}[mode]
            value = int(bool(operation_result)) if mode == "short-circuit" else operation_result
        assert output == (x << 24) | (y << 16) | value
        post, _, query_hits = _run_activation(lowered, names, before, encoded)
        assert post[names["x"]] == x and post[names["value"]] == value
        if "y" in names:
            assert post[names["y"]] == y
        native_hits = collector.bindings.decode(
            {event for event in range(collector.bindings.event_count) if bits >> event & 1}).occurrences
        assert query_hits == set(native_hits) & own_occurrences
        assert bool(set(native_hits) & target_occurrences) == (operation_result == 18)
