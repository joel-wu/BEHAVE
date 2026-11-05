"""Per-elaboration native types, original occurrences, and local-query parity."""

import json
from dataclasses import replace

import pytest

from behave.sim.core.observation_goal import GoalCondition, GoalField
from behave.sim.core.operation_goal import OperationGoal, OperationRule
from behave.sim.core.refusal import Refused
from behave.sim.rtl.source_elaboration import parameter_partition
from behave.sim.rtl.source_integral import integral_type, integral_value_facts, typed_symbols
from behave.sim.rtl.source_ir import SourceCompiler
from behave.sim.rtl.source_operation_events import CaptureType, _capture_type

from .test_rtl_combinational_events import _trace
from .test_rtl_operation_rules import _plans
from .test_rtl_source_ir import _encode, _run_activation
from .test_rtl_source_locations import _collector, _occurrence_meanings


def test_parameter_partition_distinguishes_width_and_rejects_ambiguous_outcomes():
    guards = parameter_partition(({"P": (8, "4")}, {"P": (16, "4")}), ("small", "wide"))
    assert "$bits(P ) == 8" in guards[0][0]
    assert "$bits(P ) == 16" in guards[1][0]
    with pytest.raises(ValueError, match="distinguishing compiler parameters"):
        parameter_partition(({"P": (8, "4")}, {"P": (8, "4")}), ("small", "wide"))


def test_scalar_type_parameter_facts_do_not_invent_storage_coordinates():
    symbols = typed_symbols({"integral_types": [
        {"type_addr": "42", "width": 4, "signed": True, "four_state": False}]})
    assert _capture_type("42", symbols) == CaptureType(4, True, False)
    with pytest.raises(Refused, match="rtl-source-ir-type-unsupported"):
        integral_type("42", symbols)


def test_alias_facts_link_to_compiler_layout_not_a_width_based_substitute():
    symbols = typed_symbols({
        "design": {"kind": "PackedArrayType", "addr": 43, "range": "[2:5]",
                   "elementType": {"kind": "ScalarType", "addr": 44, "isSigned": True}},
        "integral_types": [
            {"type_addr": 42, "canonical_type_addr": 43, "width": 4, "signed": True, "four_state": True},
            {"type_addr": 43, "canonical_type_addr": 43, "width": 4, "signed": True, "four_state": True},
            {"type_addr": 44, "canonical_type_addr": 44, "width": 1, "signed": True, "four_state": True},
        ]})
    value = integral_type("42", symbols)
    assert (value.width, value.signed, value.left, value.right) == (4, True, 2, 5)
    assert _capture_type("42", symbols) == CaptureType(4, True, True)


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.solver
def test_propagated_explicit_and_assignment_conversions_remain_distinct(tmp_path):
    source = '''module M(input clk, input logic signed[3:0] a, output logic[31:0] q);
      typedef logic [7:0] U;
      always @(posedge clk) begin
        q[7:0] <= a+8'd1;
        q[15:8] <= U'(a)+8'd1;
        q[23:16] <= a;
        q[31:24] <= 8'(unsigned'(a))+8'd1;
      end
    endmodule'''
    _, plan = _plans(tmp_path / "parse", source, _rules())
    collector, _ = _collector(plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, _DRIVER)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, _DRIVER, native=True)
    ast = json.loads((tmp_path / "parse" / "frontend.ast.json").read_text())
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    context, = plan.declarations.contexts
    lowered = compiler.lower(context)
    names = {compiler.symbols[identity]["name"]: target for target, identity in lowered.storage}
    encoded = _encode(lowered)
    for x in range(16):
        signed = x-16 if x >= 8 else x
        expected = ((x+1)<<24) | ((signed&255)<<16) | (((signed+1)&255)<<8) | (x+1)
        assert original[x][0] == native[x][0] == expected
        _, pending, ir_hits = _run_activation(lowered, names, {"a": signed, "q": 0, "clk": 0}, encoded)
        assert pending[lowered.deferred[0].data] == expected
        observed = collector.bindings.decode({i for i in range(collector.bindings.event_count)
                                              if native[x][1] >> i & 1}).occurrences
        assert ir_hits == set(observed)


def _rules():
    return tuple(OperationRule("add", OperationGoal(name, GoalCondition("eq", GoalField("result"), value)))
                 for name, value in (("zero", 0), ("seventeen", 17), ("negative", -1)))


@pytest.mark.cosim
@pytest.mark.parametrize("explicit", (False, True), ids=("propagated", "explicit"))
@pytest.mark.parametrize("wrong", (False, True), ids=("correct", "wrong-output"))
def test_public_search_preserves_conversion_kind(tmp_path, explicit, wrong):
    from behave.sim import api
    from behave.sim.core.goal_provider import RTL_LOCAL_GUIDANCE_POLICY

    target = 255 if explicit else 15
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(4, signed=True), "a")],
                     api.settled("in"), None, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(8), "y")],
                     api.settled("out"), None, kind=api.Kind.TRANSACTION, answers_per_transaction=1)], [],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))],
        operation_rules=(OperationRule("add", OperationGoal("target",
            GoalCondition("eq", GoalField("result"), target))),))
    expected = "(a+1)&255" if explicit else "(a&15)+1"
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": ('class M:\n def reset(self): pass\n def process(self,a): return {"y":'
                         + expected + '}\n').encode()}, config={"class": "M"})
    operand = "U'(a)" if explicit else "a"
    source = f'''module M(input logic signed [3:0] a, output logic [7:0] y);
      typedef logic [7:0] U;
      always_comb y={operand}+8'd{1+wrong};
    endmodule'''
    path = tmp_path / "design.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / "search")
                      if p.policy == RTL_LOCAL_GUIDANCE_POLICY)
    assert len(providers) == 1
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=3))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "command", baseline=baseline, reference=reference,
        providers=providers, search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True),
        run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED
    assert result.decision.authoritative, result.evaluation.as_dict()
    assert result.decision.passed is not wrong
    if wrong:
        assert result.evaluation.mismatch_count
        # A checked baseline counterexample ends the negative command. The
        # skipped workload is explicit, not evidence of a complete passing run.
        assert result.evaluation.failure_witness is not None
        assert result.evaluation.unexecuted and not result.evaluation.complete
        assert result.generated.search_summary["stop_reason"] == "baseline-failed"
        return
    assert result.evaluation.complete, result.evaluation.as_dict()
    goal, = [g.id for g in handle.frontend_catalogue.values() if g.family == "operation-event"]
    search, = result.generated.searches
    feedback = search.telemetry["baseline_coverage"]
    assert goal in feedback["unhit"] and goal in feedback["targetable"]
    baseline_ids = {seed.identity_bytes for seed in baseline.stimuli}
    assert all(seed.depth("in") == 100 for seed in baseline.stimuli)
    witnesses = [run for run in result.evaluation.results if run.stimulus.identity_bytes not in baseline_ids
                 and any(name == goal and hit is True for row in run.trace.records for name, hit in row.hits)]
    assert witnesses
    # Both conversions require the same signed input (-2), but produce
    # different output/goal values. Check the native transfer, not a solver flag.
    for run in witnesses:
        assert any(dict(row.accepted).get("in") == (-2,)
                   and dict(row.accepted).get("out") == (target,) for row in run.trace.records)


_DRIVER = '''
    for(unsigned x=0;x<256;x++) {
      if(behave_native_events_begin_v1(x)) return 1;
      dut.clk=0;dut.a=x;dut.eval();dut.clk=1;dut.eval();
      uint64_t bits=0,calls=0;
      if(behave_native_events_end_v1(x,&bits,1,&calls)) return 2;
      std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    }
'''


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.solver
@pytest.mark.parametrize("signed", (False, True))
@pytest.mark.parametrize("helper", (False, True))
@pytest.mark.parametrize("mixed_state_kind", (False, True))
def test_module_specializations_keep_types_values_and_original_hit_scopes(tmp_path, signed, helper, mixed_state_kind):
    sign = "signed" if signed else "unsigned"
    body = "q <= a+W'(1);"
    declaration = ""
    if helper:
        declaration = """typedef T Value;
        function automatic Value increment(input Value x);
          int W=0;
          return x+2'sd1;
        endfunction"""
        body = "q <= increment(a);"
    narrow_type = f", .T(bit {sign} [3:0])" if mixed_state_kind else ""
    source = f"""module Cell #(parameter W=4, parameter type T=logic {sign} [W-1:0])
        (input clk, input T a, output T q);
      {declaration}
      always @(posedge clk) {body}
    endmodule
    module M(input clk, input [7:0] a, output [11:0] q);
      Cell #(.W(4){narrow_type}) narrow(clk,a[3:0],q[3:0]);
      Cell #(.W(8)) wide(clk,a,q[11:4]);
    endmodule"""
    _, plan = _plans(tmp_path / "parse", source, _rules())
    collector, _ = _collector(plan)
    assert collector.bindings.event_count <= 64
    meanings = _occurrence_meanings(source, plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, _DRIVER)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, _DRIVER, native=True)
    expected_values = [(((x+1)&255)<<4) | ((x+1)&15) for x in range(256)]
    assert [row[0] for row in plain] == [row[0] for row in native] == expected_values
    for x, (_, bits, _) in enumerate(native):
        decoded = collector.bindings.decode({i for i in range(collector.bindings.event_count) if bits >> i & 1})
        got = {(scope, outcome) for key in decoded.occurrences
               for scope, schema, _, outcome in (meanings[key],) if schema == "operation-event"}
        expected = set()
        for scope, width in (("M.narrow", 4), ("M.wide", 8)):
            value = (x+1) & ((1 << width)-1)
            if signed and value & (1 << (width-1)):
                value -= 1 << width
            for name, target in (("zero", 0), ("seventeen", 17), ("negative", -1)):
                if value == target:
                    expected.add((scope, name))
        assert got == expected, (x, got, expected)
    ast = json.loads((tmp_path / "parse" / "frontend.ast.json").read_text())
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    for instance, width in (("narrow", 4), ("wide", 8)):
        context, = [row for row in plan.declarations.contexts if row.scope == ("M", instance)]
        lowered = compiler.lower(context)
        names = {compiler.symbols[identity]["name"]: target for target, identity in lowered.storage}
        encoded = _encode(lowered)
        for x in (0, 14, 15, 16, 17, 126, 127, 254, 255):
            value = x & ((1<<width)-1)
            if signed and value & (1 << (width-1)):
                value -= 1 << width
            _, _, ir_hits = _run_activation(lowered, names, {"a": value, "q": 0, "clk": 0}, encoded)
            _, bits, _ = native[x]
            observed = collector.bindings.decode(
                {i for i in range(collector.bindings.event_count) if bits >> i & 1}).occurrences
            assert ir_hits == {key for key in observed if meanings[key][0] == f"M.{instance}"}


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.solver
@pytest.mark.parametrize("signed", (False, True))
@pytest.mark.parametrize("value_parameter", (False, True))
@pytest.mark.parametrize("logical", (False, True))
def test_state_kind_variants_preserve_computation_and_occurrence_hits(tmp_path, signed, value_parameter, logical):
    sign = "signed" if signed else "unsigned"
    # An untyped integral parameter is four-state even if its supplied value
    # was cast to bit. Use a typed parameter to exercise an actual type(P)
    # state-kind difference, as confirmed by the compiler below.
    parameter = (f"parameter type U=logic {sign} [3:0], parameter U P=0" if value_parameter
                 else f"parameter type T=logic {sign} [3:0]")
    alias = "typedef type(P) T;" if value_parameter else ""
    type_name = "type(P)" if value_parameter else "T"
    setting_b = "U(B), .P(B'(0))" if value_parameter else "T(B)"
    setting_l = "U(L), .P(L'(0))" if value_parameter else "T(L)"
    expression = "!a" if logical else "a+T'(1)"
    source = f'''module Cell #({parameter})(input clk, input {type_name} a, output {type_name} q);
      {alias}
      always @(posedge clk) q <= {expression};
    endmodule
    module M(input clk, input[3:0] a, output[7:0] q);
      typedef bit {sign} [3:0] B;
      typedef logic {sign} [3:0] L;
      Cell #(.{setting_b}) one(clk,a,q[3:0]);
      Cell #(.{setting_l}) two(clk,a,q[7:4]);
    endmodule'''
    rules = ((OperationRule("logical-not", OperationGoal("zero",
              GoalCondition("eq", GoalField("result"), True))),) if logical else _rules())
    _, plan = _plans(tmp_path / "parse", source, rules)
    assert len(plan.declarations.sites_for("operation-event")) == 1
    collector, _ = _collector(plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    # C++ writes to a narrow Verilator port must mask unused host-word bits.
    driver = _DRIVER.replace("dut.a=x;", "dut.a=x&15;")
    original = _trace(tmp_path / "original", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    # Numeric execution alone cannot distinguish bit and logic with known
    # inputs. Check the emitted computation types in each elaborated instance.
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from .test_rtl_observation_frontend import _installed

    (_, emitted), _ = parse_observation_source(tmp_path / "native", "M", run=_installed)
    emitted_symbols = typed_symbols(emitted)
    computation_types = {}

    def inspect(node, scope=()):
        if isinstance(node, dict):
            if node.get("kind") == "Instance":
                scope = (*scope, node["name"])
            if node.get("kind") == "Subroutine" and node.get("name", "").startswith("__behave_native_event_operation_"):
                results = [m for m in node.get("members", ())
                           if m.get("kind") == "Variable" and m.get("name") == "result"]
                if results:
                    result, = results
                    operands = tuple(a for a in node["arguments"] if a["name"] in {"v0", "v1"})
                    computation_types[scope[-1]] = tuple(integral_value_facts(v["type"], emitted_symbols)
                                                         for v in (*operands, result))
            for child in node.values():
                inspect(child, scope)
        elif isinstance(node, list):
            for child in node:
                inspect(child, scope)

    inspect(emitted["design"])
    assert computation_types == {instance: ((4, signed, state),) * (1 if logical else 2)
                                + ((1 if logical else 4, False if logical else signed, state),)
                                for instance, state in (("one", False), ("two", True))}
    meanings = _occurrence_meanings(source, plan)
    hits_by_input = []
    for x, (plain, observed) in enumerate(zip(original, native, strict=True)):
        result = int(not (x & 15)) if logical else (x+1) & 15
        assert plain[0] == observed[0] == result | (result << 4)
        hits = collector.bindings.decode(
            {i for i in range(collector.bindings.event_count) if observed[1] >> i & 1}).occurrences
        hits_by_input.append(hits)
        value = result-16 if signed and result >= 8 else result
        targets = (("zero", 1),) if logical else (("zero", 0), ("seventeen", 17), ("negative", -1))
        expected = {(f"M.{instance}", name) for instance in ("one", "two")
                    for name, target in targets
                    if value == target}
        assert {(scope, outcome) for key in hits
                for scope, schema, _, outcome in (meanings[key],) if schema == "operation-event"} == expected
    ast = json.loads((tmp_path / "parse" / "frontend.ast.json").read_text())
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    for instance in ("one", "two"):
        context, = [row for row in plan.declarations.contexts if row.scope == ("M", instance)]
        lowered = compiler.lower(context)
        names = {compiler.symbols[identity]["name"]: target for target, identity in lowered.storage}
        encoded = _encode(lowered)
        for x in range(16):
            value = x-16 if signed and x >= 8 else x
            _, _, ir_hits = _run_activation(lowered, names, {"a": value, "q": 0, "clk": 0}, encoded)
            assert ir_hits == {key for key in hits_by_input[x] if meanings[key][0] == f"M.{instance}"}
        import z3

        mark, occurrence = next((mark, occurrence) for mark, occurrence, _ in lowered.occurrences
                                if meanings[occurrence][1] == "operation-event"
                                and meanings[occurrence][3] == "zero")
        solver = z3.Solver()
        a = encoded.pre[names["a"]]
        solver.add(encoded.constraint, encoded.hits[mark],
                   a >= (-8 if signed else 0), a <= (7 if signed else 15))
        # This is one triggered activation, so its clock edge is a scheduler
        # precondition, not a read variable in this computation's IR.
        solver.add(encoded.pre[names["q"]] == 0)
        assert solver.check() == z3.sat
        witness = solver.model().eval(a, model_completion=True).as_long() & 15
        assert occurrence in hits_by_input[witness]


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.solver
@pytest.mark.parametrize("state_kinds", (("logic", "logic"), ("bit", "bit"),
                                        ("bit", "logic"), ("logic", "bit")))
@pytest.mark.parametrize("helper", (False, True))
@pytest.mark.parametrize("mixed_operand", (False, True))
def test_signed_type_parameters_keep_contextual_extension_and_hit_meaning(
        tmp_path, state_kinds, helper, mixed_operand):
    # With mixed_operand=True both inputs are signed, but only one operation
    # is signed. In the unsigned instance, 4'hf must extend to 8'h0f, NOT ff.
    unsigned_input = "signed" if mixed_operand else "unsigned"
    helper_source = ""
    body = "q <= a+R'(1);"
    if helper:
        helper_source = """function automatic R increment(input T x);
          int T=0;
          return x+R'(1);
        endfunction"""
        body = "q <= increment(a);"
    source = f'''module Cell #(parameter type T=logic[3:0], parameter type R=logic[7:0])
        (input clk, input T a, output R q);
      {helper_source}
      always @(posedge clk) {body}
    endmodule
    module M(input clk, input[7:0] a, output[15:0] q);
      Cell #(.T({state_kinds[0]} signed [3:0]), .R({state_kinds[0]} signed [7:0]))
          signed_cell(clk,a[3:0],q[7:0]);
      Cell #(.T({state_kinds[1]} {unsigned_input} [3:0]), .R({state_kinds[1]} unsigned [7:0]))
          unsigned_cell(clk,a[3:0],q[15:8]);
    endmodule'''
    _, plan = _plans(tmp_path / "parse", source, _rules())
    assert len(plan.declarations.sites_for("operation-event")) == 1
    collector, _ = _collector(plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, _DRIVER)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, _DRIVER, native=True)
    meanings = _occurrence_meanings(source, plan)
    observed_by_input = []
    for x, (plain, instrumented) in enumerate(zip(original, native, strict=True)):
        unsigned = x & 15
        signed = unsigned - 16 if unsigned >= 8 else unsigned
        assert plain[0] == instrumented[0] == ((unsigned+1)<<8) | ((signed+1)&255)
        hits = collector.bindings.decode(
            {i for i in range(collector.bindings.event_count) if instrumented[1] >> i & 1}).occurrences
        observed_by_input.append(hits)
        actual = {(scope, outcome) for key in hits
                  for scope, schema, _, outcome in (meanings[key],) if schema == "operation-event"}
        expected = {(f"M.{instance}_cell", name)
                    for instance, result in (("signed", signed+1), ("unsigned", unsigned+1))
                    for name, target in (("zero", 0), ("seventeen", 17), ("negative", -1))
                    if result == target}
        assert actual == expected, (x, actual, expected)
    ast = json.loads((tmp_path / "parse" / "frontend.ast.json").read_text())
    compiler = SourceCompiler(source, ast, plan, filename="design.sv")
    for instance in ("signed", "unsigned"):
        context, = [row for row in plan.declarations.contexts if row.scope == ("M", f"{instance}_cell")]
        lowered = compiler.lower(context)
        names = {compiler.symbols[identity]["name"]: target for target, identity in lowered.storage}
        encoded = _encode(lowered)
        for x in range(16):
            value = x - 16 if (instance == "signed" or mixed_operand) and x >= 8 else x
            _, _, ir_hits = _run_activation(lowered, names, {"a": value, "q": 0, "clk": 0}, encoded)
            assert ir_hits == {key for key in observed_by_input[x]
                               if meanings[key][0] == f"M.{instance}_cell"}
        # Query the missed negative-result goal with the input free, then
        # confirm the returned witness against the independent native run.
        import z3

        mark, = [mark for mark, occurrence, _ in lowered.occurrences
                 if meanings[occurrence][1] == "operation-event" and meanings[occurrence][3] == "negative"]
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.hits[mark])
        for name in ("q", "clk"):
            if name in names:
                solver.add(encoded.pre[names[name]] == 0)
        outcome = solver.check()
        assert outcome == (z3.sat if instance == "signed" else z3.unsat)
        if outcome == z3.sat:
            bits = solver.model().eval(encoded.pre[names["a"]], model_completion=True).as_long() & 15
            assert bits == 14
            assert any(meanings[key][0] == "M.signed_cell" and meanings[key][3] == "negative"
                       for key in observed_by_input[bits])


@pytest.mark.eda("slang", "verilator", "make")
def test_varying_signedness_type_witness_never_executes_mutating_helper(tmp_path):
    source = '''module Cell #(parameter type R=logic[7:0])
        (input clk, input logic signed [3:0] a, output R q, output logic[7:0] count=0);
      function automatic logic signed [3:0] next_value(input logic signed[3:0] x);
        count=count+1;
        return x;
      endfunction
      always @(posedge clk) q<=next_value(a)+R'(1);
    endmodule
    module M(input clk, input[7:0] a, output[31:0] q);
      Cell #(.R(logic signed[7:0])) signed_cell(clk,a[3:0],q[7:0],q[23:16]);
      Cell #(.R(logic unsigned[7:0])) unsigned_cell(clk,a[3:0],q[15:8],q[31:24]);
    endmodule'''
    _, plan = _plans(tmp_path / "parse", source, _rules())
    collector, _ = _collector(plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, _DRIVER)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, _DRIVER, native=True)
    for x, (plain, instrumented) in enumerate(zip(original, native, strict=True)):
        value = x & 15
        signed = value-16 if value >= 8 else value
        count = (x+1)&255
        assert plain[0] == instrumented[0] == (count<<24)|(count<<16)|((value+1)<<8)|((signed+1)&255)


@pytest.mark.eda("slang", "verilator", "make")
def test_unobserved_construction_instance_keeps_its_own_operation_type(tmp_path):
    source = '''module Cell #(parameter RUN=1, parameter type R=logic signed[7:0])
        (input clk, input logic signed[3:0] a, output R q);
      function automatic R increment(input logic signed[3:0] x);
        return x+R'(1);
      endfunction
      initial q=increment(4'hf);
      if(RUN) always @(posedge clk) q<=increment(a);
    endmodule
    module M(input clk, input[7:0] a, output[15:0] q);
      Cell runtime_cell(clk,a[3:0],q[7:0]);
      Cell #(.RUN(0), .R(logic unsigned[7:0])) initial_cell(clk,a[3:0],q[15:8]);
    endmodule'''
    _, plan = _plans(tmp_path / "parse", source, _rules())
    collector, _ = _collector(plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, _DRIVER)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, _DRIVER, native=True)
    for x, (plain, instrumented) in enumerate(zip(original, native, strict=True)):
        value = x & 15
        signed = value-16 if value >= 8 else value
        assert plain[0] == instrumented[0] == (16<<8)|((signed+1)&255)
    # Construction contributes value specializations, never new goal instances.
    assert all(row.scope != ("M", "initial_cell") for row in plan.declarations.contexts)


@pytest.mark.eda("slang", "verilator", "make")
def test_generated_blocking_writes_keep_operation_values_and_scoped_hits(tmp_path):
    source = '''module M(input [7:0] a, output [15:0] q);
      for(genvar k=4;k<=8;k+=4) begin: lanes
        logic [k-1:0] value;
        always_comb value=a[k-1:0]+k'(1);
        if(k==4) assign q[3:0]=value; else assign q[15:8]=value;
      end
      assign q[7:4]=0;
    endmodule'''
    plain, observed = _plans(tmp_path / "parse", source, _rules())
    driver = _DRIVER.replace("dut.clk=0;dut.a=x;dut.eval();dut.clk=1;dut.eval();", "dut.a=x;dut.eval();")
    collector, _ = _collector(observed)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver)
    expected_values = [(((x+1)&255)<<8) | ((x+1)&15) for x in range(256)]
    assert [row[0] for row in original] == expected_values
    for name, plan in (("plain", plain), ("observed", observed)):
        collector, _ = _collector(plan)
        native = _trace(tmp_path / name, plan.native(scoped=True).text, collector, driver, native=True)
        assert [row[0] for row in native] == expected_values
        if name == "plain":
            continue
        meanings = _occurrence_meanings(source, plan)
        for x, (_, bits, _) in enumerate(native):
            hits = collector.bindings.decode(
                {i for i in range(collector.bindings.event_count) if bits >> i & 1}).occurrences
            got = {(scope, outcome) for key in hits
                   for scope, schema, _, outcome in (meanings[key],) if schema == "operation-event"}
            expected = {(f"M.lanes[{width}]", outcome)
                        for width in (4, 8) for outcome, target in (("zero", 0), ("seventeen", 17))
                        if (x+1) & ((1 << width)-1) == target}
            assert got == expected, (x, got, expected)


@pytest.mark.eda("slang")
def test_nested_subscriber_references_are_compiler_bound_at_the_declaration_boundary(tmp_path):
    source = '''module M(input [7:0] a, output [31:0] q);
      for(genvar WATCH=0;WATCH<2;WATCH++) begin: lanes
        logic [7:0] x=0; logic [15:0] value;
        always_comb begin x=a; x=0; end
        if(WATCH) begin: consumer always_comb value=x; end
        else begin: other always_comb value=a; end
      end
      assign q={lanes[0].value,lanes[1].value};
    endmodule'''
    plain, observed = _plans(tmp_path / "parse", source, ())
    for plan in (plain, observed):
        assert plan.declarations.sites_for("location")
        assert not plan.native_issues
        assert plan.native().text
    rows = json.loads((tmp_path / "parse" / "frontend.sensitivity.json").read_text())
    references = {write["scope_reference"] for row in rows for read in row["reads"]
                  for write in read["write_references"] if write["scope_reference"]}
    assert references
    assert all(reference.startswith("\\M ") for reference in references)
    assert any("lanes[\\WATCH ]" in reference for reference in references)


@pytest.mark.eda("slang")
def test_local_shadow_does_not_replace_generate_index_in_subscriber_reference(tmp_path):
    source = '''module M(input [3:0] a, output [7:0] q);
      for(genvar k=0;k<2;k++) begin: lanes
        logic [3:0] x=0;
        always_comb begin: writer int k; k=0; x=a; x=k; end
        if(k==0) begin: consumer assign q[3:0]=x; end
        else begin: consumer assign q[7:4]=x; end
      end
    endmodule'''
    _plans(tmp_path / "parse", source, ())
    rows = json.loads((tmp_path / "parse" / "frontend.sensitivity.json").read_text())
    references = {write["scope_reference"] for row in rows for read in row["reads"]
                  for write in read["write_references"] if write["scope_reference"]}
    assert references
    assert all("[\\k ]" not in reference for reference in references)
    assert any("lanes[0]" in reference for reference in references)
    assert any("lanes[1]" in reference for reference in references)


@pytest.mark.eda("slang", "verilator", "make")
def test_equal_scalar_types_do_not_specialize_on_packed_index_direction(tmp_path):
    source = '''module Cell #(parameter type T=logic[3:0])(input clk, input T a, output T q);
      always @(posedge clk) q <= a+T'(1);
    endmodule
    module M(input clk, input[7:0] a, output[7:0] q);
      Cell #(.T(logic[0:3])) ascending(clk,a[3:0],q[3:0]);
      Cell #(.T(logic[3:0])) descending(clk,a[3:0],q[7:4]);
    endmodule'''
    _, plan = _plans(tmp_path / "parse", source, _rules())
    collector, _ = _collector(plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, _DRIVER)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, _DRIVER, native=True)
    assert [row[0] for row in plain] == [row[0] for row in native] == [((x+1)&15)*17 for x in range(256)]
    meanings = _occurrence_meanings(source, plan)
    for x, (_, bits, _) in enumerate(native):
        decoded = collector.bindings.decode({i for i in range(collector.bindings.event_count) if bits >> i & 1})
        got = {(scope, outcome) for key in decoded.occurrences
               for scope, schema, _, outcome in (meanings[key],) if schema == "operation-event"}
        expected = {(f"M.{name}", "zero") for name in ("ascending", "descending")} if x % 16 == 15 else set()
        assert got == expected


@pytest.mark.eda("slang", "verilator", "make")
def test_generate_constants_use_actual_values_not_iteration_positions(tmp_path):
    source = '''module M(input clk, input[7:0] a, output [11:0] q);
      for(genvar k=8;k>=4;k-=4) begin: lanes
        logic [k-1:0] value;
        always @(posedge clk) value<=a[k-1:0]+k'(1);
        if(k==4) assign q[3:0]=value; else assign q[11:4]=value;
      end
    endmodule'''
    _, plan = _plans(tmp_path / "parse", source, _rules())
    collector, _ = _collector(plan)
    assert collector.bindings.event_count <= 64
    meanings = _occurrence_meanings(source, plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, _DRIVER)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, _DRIVER, native=True)
    assert [row[0] for row in plain] == [row[0] for row in native]
    for x, (_, bits, _) in enumerate(native):
        decoded = collector.bindings.decode({i for i in range(collector.bindings.event_count) if bits >> i & 1})
        got = {(scope, outcome) for key in decoded.occurrences
               for scope, schema, _, outcome in (meanings[key],) if schema == "operation-event"}
        expected = {(f"M.lanes[{width}]", name) for width in (4, 8)
                    for name, target in (("zero", 0), ("seventeen", 17))
                    if (x+1) & ((1<<width)-1) == target}
        assert got == expected
