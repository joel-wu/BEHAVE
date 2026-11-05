"""Unexecuted X/Z expressions do not destroy a defined local source query."""

from dataclasses import replace

import pytest

from behave.sim.symbolic import ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_integral import four_state_literal, integer_literal
from behave.sim.rtl.source_scheduler import lower_source_scheduler

from .test_rtl_source_net_drivers import _prepare
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _names, _state

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


SOURCE = '''module M(input clk, reset, input [1:0] a, output logic [2:0] q);
  localparam [2:0] UNKNOWN = 3'bx0z;
  always @(posedge clk) begin
    if(reset) q<=0;
    else case(q)
      0,1,2: if(a==3) q<=2; else q<=1;
      default: q<=VALUE;
    endcase
  end
endmodule'''


def _case(tmp_path, value="'x"):
    source = SOURCE.replace('VALUE', value)
    plan, ast, sensitivity = _prepare(tmp_path, source)
    with pytest.raises(Refused) as error:
        lower_source_scheduler(source, ast, plan, sensitivity, filename='design.sv')
    assert error.value.refusal.kind == 'rtl-source-ir-four-state-domain-unproved'
    assert error.value.refusal.detail['domain_proof'] == 'sat'
    machine = lower_source_scheduler(source, ast, plan, sensitivity,
        filename='design.sv', defer_domains=True)
    assert machine.domain_checks
    assert {g for _, _, g in machine.occurrences}.isdisjoint(m for m, _ in machine.domain_checks)
    env = ranges.fixpoint(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges})
    return source, plan, machine, env


def _query(machine, env, target, *, q=0, reset=0, following_inputs=()):
    names = _names(machine)
    state = _state(machine, {'clk': 0, 'reset': 0, 'a': 0, 'q': q})
    return query_source_interval(machine, env, state,
        {names['clk']: 1, names['reset']: reset, names['a']: 0}, (target,),
        mutable_inputs=(names['a'],), following_inputs=following_inputs,
        event_bound=machine.event_bound, timeout_ms=30_000)


@pytest.mark.parametrize('value', ["'x", "3'b1xz", "UNKNOWN", "'z"])
def test_unknown_evaluation_is_proved_absent_not_replaced_with_a_value(tmp_path, value):
    source, plan, machine, env = _case(tmp_path, value)
    target = _goal(source, plan, 'q<=2', 'entry', 'location')
    default = _goal(source, plan, f'q<={value}', 'entry', 'location')
    for q in (0, 1, 2):
        answer = _query(machine, env, target, q=q)
        assert answer.outcome is probing.Outcome.SAT, answer.detail
        assert answer.candidate[_names(machine)['a']] == 3
    # The unsafe branch remains catalogued and receives a scoped query.
    answer = _query(machine, env, default)
    assert answer.outcome is probing.Outcome.LOCAL_UNSAT, answer.detail
    # The requested goal cannot hide an invalid initial state/domain.
    answer = _query(machine, env, target, q=7)
    assert answer.outcome is probing.Outcome.UNSUPPORTED, answer.detail
    assert 'four-state-domain-unproved' in answer.detail and answer.candidate is None
    # An actual reset, not an assumed state invariant, excludes that branch.
    answer = _query(machine, env, target, q=7, reset=1)
    assert answer.outcome is probing.Outcome.LOCAL_UNSAT, answer.detail


@pytest.mark.parametrize('value', ["1'bx", "4'b1xz0", "8'hxF", "-4'sbx", "3'b???"])
def test_unknown_constant_detection_does_not_make_integer_parser_accept_it(value):
    assert four_state_literal(value)
    with pytest.raises(Refused, match='literal-unsupported'):
        integer_literal(value)


@pytest.mark.parametrize('value', ['0', "4'hf", "-4'sd2", 'not-a-literal'])
def test_unknown_constant_detector_is_not_a_general_literal_fallback(value):
    assert not four_state_literal(value)


@pytest.mark.eda('verilator', 'make')
def test_sat_input_and_all_native_goals_match_unchanged_execution(tmp_path):
    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_locations import _collector
    from .test_rtl_source_scheduler import _settle, _step

    source, plan, machine, env = _case(tmp_path)
    target = _goal(source, plan, 'q<=2', 'entry', 'location')
    answer = _query(machine, env, target)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    a = answer.candidate[_names(machine)['a']]
    collector, manifest = _collector(plan)
    assert collector.word_count == 1
    driver = f'''
      if(behave_native_events_begin_v1(0)) return 1;
      dut.reset=1; dut.clk=0; dut.a=0; dut.eval(); dut.clk=1; dut.eval();
      dut.clk=0; dut.reset=0; dut.eval();
      uint64_t startup_bits=0,startup_calls=0;
      if(behave_native_events_end_v1(0,&startup_bits,1,&startup_calls)) return 2;
      const unsigned values[]={{0,1,2,{a}}};
      for(unsigned i=0;i<4;i++) {{
        if(behave_native_events_begin_v1(i+1)) return 1;
        dut.clk=0; dut.eval();
        dut.a=values[i]; dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i+1,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }}
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path/'original', source, flat, driver)
    observed = _trace(tmp_path/'observed', plan.native(scoped=True).text, collector, driver, native=True)
    assert [r[0] for r in original] == [r[0] for r in observed] == [1, 1, 1, 2]
    q = 0
    for value, row in zip((0, 1, 2, a), observed, strict=True):
        state = _state(machine, {'clk': 0, 'reset': 0, 'a': value, 'q': q})
        changed, _, hit, _, _ = _step(machine, state, 0, clk=1)
        after, following = _settle(machine, changed)
        native = collector.bindings.decode({i for i in range(collector.bindings.event_count)
                                           if row[1] >> i & 1}).goals
        assert native == hit | following
        assert native <= manifest.keys()
        assert (target in native) == (value == a)
        q = after[_names(machine)['q']]
        assert q == row[0]


@pytest.mark.eda('verilator', 'make')
@pytest.mark.parametrize('value,physical', [
    ("'x", 0), ("3'b1xx", 4), ('UNKNOWN', 0), ("3'sb1x0", 4),
])
def test_native_variable_assignment_matches_original_observer_and_relation(tmp_path, value, physical):
    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_locations import _collector
    from .test_rtl_source_scheduler import _settle, _step

    source = '''module M(input clk,reset,input [1:0] a,output logic [2:0] q);
      localparam [2:0] UNKNOWN=3'bx0x;
      always @(posedge clk)
        if(reset) q<=7;
        else if(a==0) q<=VALUE;
        else q<=q+1;
    endmodule'''.replace('VALUE', value)
    plan, ast, sensitivity = _prepare(tmp_path, source)
    machine = lower_source_scheduler(source, ast, plan, sensitivity,
        filename='design.sv', native_assignments=True)
    assert not machine.domain_checks
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    driver = '''
      if(behave_native_events_begin_v1(0)) return 1;
      dut.clk=0; dut.reset=1; dut.a=0; dut.eval();
      dut.clk=1; dut.eval(); dut.clk=0; dut.reset=0; dut.eval();
      uint64_t startup_bits=0,startup_calls=0;
      if(behave_native_events_end_v1(0,&startup_bits,1,&startup_calls)) return 2;
      const unsigned values[]={0,1,1,0,1};
      for(unsigned i=0;i<5;i++) {
        if(behave_native_events_begin_v1(i+1)) return 1;
        dut.a=values[i]; dut.clk=1; dut.eval();
        dut.clk=0; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i+1,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path/'original', source, flat, driver, native=True)
    observed = _trace(tmp_path/'observed', plan.native(scoped=True).text,
                      collector, driver, native=True)
    expected = [physical, (physical+1)&7, (physical+2)&7, physical, (physical+1)&7]
    assert [r[0] for r in original] == [r[0] for r in observed] == expected
    q = 7
    for a, row in zip((0,1,1,0,1), observed, strict=True):
        state = _state(machine, {'clk': 0, 'reset': 0, 'a': a, 'q': q})
        changed, _, hit, _, _ = _step(machine, state, 0, clk=1)
        after, following = _settle(machine, changed)
        native = collector.bindings.decode({i for i in range(collector.bindings.event_count)
                                           if row[1] >> i & 1}).goals
        assert native == hit | following
        q = after[_names(machine)['q']]
        assert q == row[0]


@pytest.mark.parametrize('rhs', ["3'bxxx+3'd1", "a ? 3'bxxx : 3'b000", "~3'bxxx",
                               "3'bzzz", "3'b1xz", "3'b???"])
def test_native_assignment_does_not_zero_unknown_operator_operands(tmp_path, rhs):
    source = f'module M(input clk,a,output logic [2:0] q); always @(posedge clk) q<={rhs}; endmodule'
    plan, ast, sensitivity = _prepare(tmp_path, source)
    with pytest.raises(Refused, match='four-state-domain-unproved'):
        lower_source_scheduler(source, ast, plan, sensitivity,
            filename='design.sv', native_assignments=True)


def test_native_assignment_does_not_claim_tristate_net_resolution(tmp_path):
    source = "module M(input a,output wire [2:0] q); assign q=3'bzzz; endmodule"
    plan, ast, sensitivity = _prepare(tmp_path, source)
    with pytest.raises(Refused, match='four-state-domain-unproved'):
        lower_source_scheduler(source, ast, plan, sensitivity,
            filename='design.sv', native_assignments=True)


@pytest.mark.parametrize('mode', ['1', 'unique'])
def test_native_assignment_rejects_a_different_materialization_profile(tmp_path, monkeypatch, mode):
    from behave.sim.rtl import native_events

    monkeypatch.setattr(native_events, 'VERILATOR_FLAGS', (*native_events.VERILATOR_FLAGS, '--x-assign', mode))
    source = "module M(input clk,output logic q); always @(posedge clk) q<=1'bx; endmodule"
    plan, ast, sensitivity = _prepare(tmp_path, source)
    with pytest.raises(Refused, match='four-state-domain-unproved'):
        lower_source_scheduler(source, ast, plan, sensitivity,
            filename='design.sv', native_assignments=True)


def test_native_x_queries_keep_every_initial_binary_state(tmp_path):
    source = SOURCE.replace('VALUE', "'x")
    plan, ast, sensitivity = _prepare(tmp_path, source)
    machine = lower_source_scheduler(source, ast, plan, sensitivity,
        filename='design.sv', defer_domains=True, native_assignments=True)
    assert not machine.domain_checks
    env = ranges.fixpoint(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges})
    default = _goal(source, plan, "q<='x", 'entry', 'location')
    normal = _goal(source, plan, 'q<=2', 'entry', 'location')
    for q in range(8):
        answer = _query(machine, env, default, q=q)
        assert answer.outcome is (probing.Outcome.SAT if q >= 3 else probing.Outcome.LOCAL_UNSAT)
        answer = _query(machine, env, normal, q=q)
        assert answer.outcome is (probing.Outcome.SAT if q < 3 else probing.Outcome.LOCAL_UNSAT)
