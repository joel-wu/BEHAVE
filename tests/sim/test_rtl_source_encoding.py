"""Native source events and their symbolic conditions share exact coordinates."""

from dataclasses import replace

import pytest

from behave.sim.core.refusal import Refusal
from behave.sim.rtl.source_bindings import EventBindings, bind_source_events, bind_source_execution
from behave.sim.rtl.source_goals import goal_of
from behave.sim.rtl.source_plan import plan_source
from behave.sim.rtl.source_projection import project_source

from .test_rtl_loop_entry_events import HELPER_SOURCE
from .test_rtl_native_loops import LOOPS, _expected_invocations
from .test_rtl_source_declarations import _documents
from .test_rtl_source_plan import _compile, _run

pytestmark = [pytest.mark.eda("slang", "yosys"), pytest.mark.solver]


def _prepared(tmp_path, source, *, scoped=False):
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    contexts = None
    if scoped:
        work = tmp_path / "annotated"
        _run(work, "yosys", "-Q", "-T", "-p",
             "read_verilog -sv design.sv; hierarchy -top M; flatten -scopename; write_rtlil scoped.rtlil")
        contexts = (work / "scoped.rtlil").read_text()
    execution = bind_source_execution(plan, original, annotated, filename="design.sv", scoped=contexts)
    # Observation uses the same binder, without allocating encoding metadata.
    assert execution.native == bind_source_events(plan, original, annotated, filename="design.sv", scoped=contexts)
    return plan, original, annotated, execution


def _declared(execution, inputs, *, encoding=None, active_events=None):
    encoding = execution.encoding if encoding is None else encoding
    plan = encoding.plan
    projection = project_source(plan, execution.native, filename="design.sv")
    terms = projection.goal_predicates(encoding, inputs, active_events=active_events)
    return {(index, outcome): terms[goal_of(site, outcome, filename="design.sv").id]
            for index, site in enumerate(plan.declarations.sites) if site.schema == "loop-activation"
            for outcome in site.outcomes}


def _location(plan, invocation):
    """Entered-statement callbacks are declared and observed natively, but have
    no compiler control binding, so the encoding refuses them by design. The
    handwritten oracles here number only the control events."""
    return plan.events[plan.invocations[invocation].source_event].role == "location"


class Inputs:
    def __init__(self):
        import z3

        self.a, self.q = z3.BitVecs("a q", 4)

    def value_var(self, name):
        return {"a": (self.a, 4), "q": (self.q, 4)}.get(name, (None, None))

    def holds(self, term, a, q=0):
        import z3

        assert z3.is_bool(term), term
        return z3.is_true(z3.simplify(z3.substitute(
            term, (self.a, z3.BitVecVal(a, 4)), (self.q, z3.BitVecVal(q, 4)))))


@pytest.mark.parametrize("name", LOOPS)
def test_all_literal_loop_events_match_the_independent_oracle(tmp_path, name):
    plan, _, _, execution = _prepared(tmp_path, LOOPS[name])
    inputs = Inputs()
    encoding = execution.encoding
    controls = [event.ordinal for event in plan.events if event.role != "location"]
    events = [event for event in range(execution.native.event_count)
              if not _location(plan, encoding.invocations[event])]
    terms = {event: encoding.predicate_for(event, inputs, active_events=(("rising", "clk"),))
             for event in events}
    declared = _declared(execution, inputs, active_events=(("rising", "clk"),))
    expected = {(index, outcome) for index, site in enumerate(plan.declarations.sites)
                if site.schema == "loop-activation" for outcome in ("zero", "nonzero")}
    assert set(declared) == expected
    for a in range(16):
        actual = {plan.invocations[encoding.invocations[event]] for event, term in terms.items()
                  if inputs.holds(term, a)}
        assert actual == {replace(call, source_event=controls[call.source_event])
                          for call in _expected_invocations(name, a)}
        assert {outcome for (_, outcome), term in declared.items() if inputs.holds(term, a)} == {
            "zero" if name == "zero" else "nonzero"}
    assert all(not inputs.holds(encoding.predicate_for(event, inputs, active_events=()), 3)
               for event in events)


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("stateful", [False, True])
def test_guarded_loop_encoding_matches_actual_native_events(tmp_path, stateful):
    from behave.sim.rtl.native_events import prepare_native_events

    from .test_rtl_combinational_events import _trace

    guard = "q[0]" if stateful else "a[0]"
    source = f"""module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) begin
        if({guard}) begin for(int i=0;i<2;i++) q<=a; end
        else begin for(int j=0;j<0;j++) q<=0; end
        q<=a;
      end
    endmodule"""
    plan, original, annotated, execution = _prepared(tmp_path, source)
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=100)
    trace = _trace(tmp_path / "native", plan.native().text, collector, '''
      for (unsigned a=0; a<16; a++) {
        if (behave_native_events_begin_v1(a)) return 1;
        dut.clk=0; dut.a=a; dut.eval(); dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if (behave_native_events_end_v1(a,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    ''', native=True)
    inputs = Inputs()
    encoded = execution.encoding
    declared = _declared(execution, inputs)
    loops = [index for index, site in enumerate(plan.declarations.sites) if site.schema == "loop-activation"]
    located = sum(1 << event for event in range(execution.native.event_count)
                  if _location(plan, encoded.invocations[event]))
    previous = 0
    for a, (q, bits, calls) in enumerate(trace):
        # process, branch, loop entry, plus one callback per entered statement
        assert q == a and calls == 3 + (bits & located).bit_count()
        guard_value = (previous if stateful else a) & 1
        assert {key for key, term in declared.items() if inputs.holds(term, a, previous)} == {
            (loops[0], "nonzero") if guard_value else (loops[1], "zero")}
        predicted = sum(1 << event for event in range(execution.native.event_count)
                        if not located >> event & 1
                        and inputs.holds(encoded.predicate_for(event, inputs), a, previous))
        assert predicted == bits & ~located
        previous = q


def test_helper_call_contexts_remain_distinct_in_symbolic_events(tmp_path):
    plan, _, _, execution = _prepared(tmp_path, HELPER_SOURCE)
    inputs = Inputs()
    controls = [event for event in range(execution.native.event_count)
                if not _location(plan, execution.encoding.invocations[event])]
    for a in range(16):
        predicted = {event for event in controls
                     if inputs.holds(execution.encoding.predicate_for(event, inputs), a)}
        assert predicted == {controls[index] for index in ({0, 1, 3} if a & 1 else {0, 4, 6})}


def test_zero_outer_expansion_keeps_inner_declared_outcomes(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) begin q<=a;
        for(int i=0;i<0;i++) for(int j=0;j<2;j++) q<=0;
      end
    endmodule"""
    plan, _, _, execution = _prepared(tmp_path, source)
    declared = _declared(execution, Inputs())
    assert len(declared) == 4
    outer, inner = sorted({index for index, _ in declared},
                          key=lambda index: plan.declarations.sites[index].start)
    assert {key for key, term in declared.items() if Inputs().holds(term, 3)} == {(outer, "zero")}
    # Losing a producer is not the same proof as a zero-sized expansion.
    missing = replace(plan, declared_events=tuple(b for b in plan.declared_events if b.site != inner))
    bad = _declared(execution, Inputs(), encoding=replace(execution.encoding, plan=missing))
    assert all(isinstance(bad[(inner, outcome)], Refusal) for outcome in ("zero", "nonzero"))


def test_missing_event_encoding_is_not_false_or_a_smaller_declaration(tmp_path):
    _, _, _, execution = _prepared(tmp_path, LOOPS["ascending"])
    encoding = replace(execution.encoding, conditions=(None,)*execution.native.event_count)
    declared = _declared(execution, Inputs(), encoding=encoding)
    assert len(declared) == 2
    assert all(isinstance(term, Refusal) and term.kind == "rtl-source-event-unencoded"
               for term in declared.values())
    dropped = replace(execution.encoding, conditions=(), invocations=())
    declared = _declared(replace(execution, native=EventBindings(0, ())), Inputs(), encoding=dropped)
    assert all(isinstance(term, Refusal) and term.kind == "rtl-source-occurrence-unbound"
               for term in declared.values())


def test_combinational_history_cannot_be_replaced_by_a_source_guard(tmp_path):
    source = LOOPS["ascending"].replace("always @(posedge clk)", "always_comb")
    _, _, _, execution = _prepared(tmp_path, source)
    declared = _declared(execution, Inputs())
    assert all(isinstance(term, Refusal) and term.kind == "rtl-source-event-interval-unsupported"
               for term in declared.values())


def test_module_instances_have_separate_event_conditions(tmp_path):
    import z3

    from .test_rtl_native_contexts import _documents as hierarchy_documents

    source = """module Inner(input clk,input a,output reg q);
      always @(posedge clk) if(a) for(int i=0;i<2;i++) q<=a;
    endmodule
    module M(input clk,a,b,output [1:0] q);
      Inner left(clk,a,q[0]); Inner right(clk,b,q[1]);
    endmodule"""
    plan, original, annotated, scoped = hierarchy_documents(tmp_path, source)
    execution = bind_source_execution(plan, original, annotated, filename="design.sv", scoped=scoped)
    a, b = z3.Bools("left_input right_input")

    class InstanceInputs:
        @staticmethod
        def value_var(name):
            return {"a": (a, 1), "b": (b, 1)}.get(name, (None, None))

    for av, bv in ((False, False), (False, True), (True, False), (True, True)):
        got = set()
        for event, coordinate in enumerate(execution.native.coordinates):
            source_event = plan.events[plan.invocations[coordinate.source_event].source_event]
            if source_event.role != "loop-nonzero":
                continue
            term = execution.encoding.predicate_for(event, InstanceInputs(), active_events=(("rising", "clk"),))
            assert z3.is_bool(term), term
            if z3.is_true(z3.simplify(z3.substitute(term, (a, z3.BoolVal(av)), (b, z3.BoolVal(bv))))):
                got.add(coordinate.scope)
        assert got == ({"TOP.M.left"} if av else set()) | ({"TOP.M.right"} if bv else set())
