"""A terminal source event keeps its prefix, not a fictional normal successor."""

import pytest

from behave.sim.symbolic import interp, ranges
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval

from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _state, _step

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]

SOURCE = '''module M(input clk,input [3:0] a,output reg [3:0] q,r);
  always @(posedge clk) begin
    q=1;
    if(a==9) $fatal(1,"stop");
    q=2;
  end
  always @(negedge clk) if(a==9) r=3;
endmodule'''


@pytest.fixture(params=[
    ('if(a==9) $fatal(1,"stop");', '$fatal', 'RTLFatal'),
    ('if(a==9) $error("stop");', '$error', 'RTLError'),
    ('assert(a!=9);', 'assert', 'RTLAssertion'),
    ('assert(a!=9) q=4;', 'assert', 'RTLAssertion'),
    ('assert(a!=9) else $error("stop");', '$error', 'RTLError'),
    ('if(a==9) $fatal(1,"stop %d",a[0] && a[1]);', '$fatal', 'RTLFatal'),
    ('if(a==9) $error("stop %d",a ? a[0] && a[1] : a[2]);', '$error', 'RTLError'),
    ('if(a==9) fail_here();', '$fatal', 'RTLFatal'),
    ('if(a==9) $fatal(1,"value=%0d",q++);', '$fatal', 'RTLFatal', 2),
    ('if(a==9) $error("value=%0d",++q);', '$error', 'RTLError', 2),
    ('if(a==9) $fatal(1,"value=%0d",value());', '$fatal', 'RTLFatal', 2,
     'function automatic int value; if(a[0]) q++; else q+=2; return q; endfunction'),
    ('if(a==9) $fatal(1,"value=%0d",a[1] ? value() : advance());', '$fatal', 'RTLFatal', 2,
     ('function automatic int value; q=3; $error("inner"); return q; endfunction '
      'function automatic int advance; q++; return q; endfunction')),
    ('if(a==9) $fatal(1,"outer=%0d",value());', '$error', 'RTLError', 3,
     'function automatic int value; q=3; $error("inner"); return q; endfunction'),
    ('for(int i=0;i<2;i++) q+=1; if(a==9) $fatal(1,"outer=%0d",value());',
     '$error', 'RTLError', 3,
     ('function automatic int value; if(a[0]) q=3; else q=4; '
      '$error("inner"); return q; endfunction')),
    ('if(a==9) $fatal(1,"value=%0d",q++," flags=%0d",a[0] && a[1]);', '$fatal', 'RTLFatal', 2),
    ('if(a==9) $fatal(1,"value=%0d scope=%m",q++);', '$fatal', 'RTLFatal', 2),
    ('if(a==9) $error(q++);', '$error', 'RTLError', 2),
    ('if(a==9) $fatal(1,"wide=%032h real=%0.2f literal=%%",{124\'h123,q++},2.5);', '$fatal', 'RTLFatal', 2),
])
def failure_source(request):
    statement, text, code, *extra = request.param
    prefix = extra[0] if extra else 1
    source = SOURCE.replace('if(a==9) $fatal(1,"stop");', statement)
    if len(extra) == 2:
        source = source.replace('  always', f'  {extra[1]}\n  always', 1)
    if 'fail_here' in statement:
        source = source.replace('  always', '  task automatic fail_here; $fatal(1,"helper"); endtask\n  always', 1)
    return source, text, code, prefix


def test_terminal_transition_records_failure_and_prefix_but_not_suffix(tmp_path, failure_source):
    source, text, code, prefix = failure_source
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "r": 0})
    state, _, _, inputs, _ = _step(machine, state, 0, clk=1, a=9)
    process, = [i + 1 for i, flag in enumerate(machine.pending) if state[flag]]
    after, _, marks, error = interp.run_step(machine.procedure, {**inputs, "action": process}, state)
    hits = {goal for mark, _, goal in machine.occurrences if mark in marks}
    assert error == code
    assert after[names["q"]] == prefix
    assert _goal(source, plan, text, "failure", "artifact-failure") in hits
    assert _goal(source, plan, "q=1", "entry", "location") in hits
    assert _goal(source, plan, "q=2", "entry", "location") not in hits
    assert _goal(source, plan, "r=3", "entry", "location") not in hits


@pytest.mark.parametrize("text,family,outcome", [
    ("$fatal", "artifact-failure", probing.Outcome.SAT),
    ("q=1", "location", probing.Outcome.SAT),
    ("q=2", "location", probing.Outcome.SAT),
    ("r=3", "location", probing.Outcome.LOCAL_UNSAT),
])
def test_interval_failure_stops_later_activations_and_drives(tmp_path, failure_source, text, family, outcome):
    source, failure_text, _, _ = failure_source
    text = failure_text if text == '$fatal' else text
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "r": 0})
    target = _goal(source, plan, text, "failure" if family == "artifact-failure" else "entry", family)
    answer = query_source_interval(machine, env, state, {names["clk"]: 1, names["a"]: 0},
        (target,), mutable_inputs=(names["a"],), event_bound=machine.event_bound,
        following_inputs=({names["clk"]: 0, names["a"]: 0},), timeout_ms=60_000)
    assert answer.outcome is outcome, answer.detail
    if outcome is probing.Outcome.SAT:
        assert answer.goal == target
        if family == "artifact-failure":
            assert answer.candidate[names["a"]] == 9
        elif text == "q=2":
            assert answer.candidate[names["a"]] != 9


@pytest.mark.parametrize('arguments', ['q++, q', 'q++, q++'])
def test_conflicting_terminal_arguments_do_not_acquire_an_evaluation_order(tmp_path, arguments):
    from behave.sim.core.refusal import Refused

    with pytest.raises(Refused, match="evaluation order"):
        _lower(tmp_path, SOURCE.replace('$fatal(1,"stop")', '$fatal(1,"%d %d",' + arguments + ')'))


@pytest.mark.eda('verilator', 'make')
@pytest.mark.parametrize('actions', ['else;', 'begin end else;', 'q+=7; else;', 'else begin end'])
def test_recovering_failure_catalogue_is_complete(tmp_path, record_property, actions):
    from .test_rtl_bounded_source_loops import _assert_closure

    source = ("module M(input clk,input [3:0] a,output reg [7:0] q); "
              f"always @(posedge clk) begin q=a; assert(q++) {actions} "
              "if(1'b0) q=99; q+=3; end endmodule")
    _assert_closure(tmp_path, record_property, source, range(16),
                    lambda a: a+4+(7 if a and actions.startswith('q+=7') else 0))


@pytest.mark.eda('verilator', 'make')
def test_terminal_catalogue_queries_match_original_and_observed_prefixes(
        tmp_path, record_property, failure_source):
    import json
    import subprocess
    from dataclasses import replace

    import z3

    from behave.sim.rtl.source_goals import manifest_of

    from .test_rtl_bounded_source_loops import _lower as lower_activation
    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_ir import _encode
    from .test_rtl_source_locations import _collector

    source, _, code, prefix = failure_source
    # One activation, with a genuine dead source branch retained in its catalogue.
    source = source.replace(',r);', ');').replace(
        '  always @(negedge clk) if(a==9) r=3;', '')
    source = source.replace('    q=2;', "    if(1'b0) q=7; q=2;")
    plan, lowered, names = lower_activation(tmp_path, source)
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    declaration = catalogue.as_dict()
    encoded = _encode(lowered)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    preamble = '''
      static void record_prefix(void* pointer) {
        auto* dut=static_cast<VM*>(pointer);
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(1,&bits,1,&calls)) std::exit(3);
        std::cout<<unsigned(dut->q)<<" "<<bits<<" "<<calls<<std::endl;
      }
    '''
    driver = '''
      if(behave_native_events_begin_v1(0)) return 2;
      dut.clk=0; dut.a=0; dut.eval();
      uint64_t bits=0,calls=0;
      if(behave_native_events_end_v1(0,&bits,1,&calls)) return 2;
      Verilated::addExitCb(record_prefix,&dut);
      if(behave_native_events_begin_v1(1)) return 2;
      dut.a=std::stoul(argv[1]); dut.clk=1; dut.eval();
      record_prefix(&dut);
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    for directory, text, binding in (
            ('plain', source, flat), ('native', plan.native(scoped=True).text, collector)):
        _trace(tmp_path / directory, text, binding, driver, native=True,
               preamble=preamble, run_args=('0',))

    def replay(a):
        rows = []
        messages = []
        for directory in ('plain', 'native'):
            run = subprocess.run([str(tmp_path/directory/'obj_dir/VM'), str(a)],
                                 capture_output=True, text=True, timeout=10, check=False)
            assert run.returncode == (1 if a == 9 else 0), run.stdout
            values = [tuple(map(int, line.split())) for line in run.stdout.splitlines()
                      if len(line.split()) == 3 and all(word.isdecimal() for word in line.split())]
            row, = values
            rows.append(row)
            messages.append([line.split('Assertion failed in ', 1)[1]
                             for line in run.stdout.splitlines() if 'Assertion failed in ' in line])
        assert rows[0][0] == rows[1][0] == (prefix if a == 9 else 2)
        assert messages[0] == messages[1]
        bits = rows[1][1]
        hits = collector.bindings.decode({i for i in range(collector.bindings.event_count)
                                          if bits >> i & 1}).goals
        return rows[1][0], hits

    observations = []
    for a in range(16):
        value, hits = replay(a)
        state = {name: a if source_name == 'a' else 0 for source_name, name in names.items()}
        after, _, marks, error = interp.run_step(lowered.procedure, {}, state)
        assert after[names['q']] == value
        assert error == (code if a == 9 else '')
        assert {goal for mark, _, goal in lowered.occurrences if mark in marks} == hits
        observations.append({'input': a, 'output': value, 'hits': sorted(hits), 'error': error})

    assert {goal for _, _, goal in lowered.occurrences} == set(catalogue)
    solver = z3.Solver()
    solver.add(encoded.constraint, *(encoded.pre[name] == 0 for source_name, name in names.items()
                                     if source_name != 'a'))
    solver.add(z3.ULE(encoded.pre[names['a']], 15))
    answers = []
    for goal in catalogue:
        solver.push()
        solver.add(z3.Or(*(encoded.hits[mark] for mark, _, key in lowered.occurrences if key == goal)))
        answer = solver.check()
        possible = any(goal in row['hits'] for row in observations)
        assert answer == (z3.sat if possible else z3.unsat)
        row = {'goal': goal, 'answer': str(answer)}
        if answer == z3.sat:
            a = solver.model().eval(encoded.pre[names['a']], model_completion=True).as_long()
            _, hits = replay(a)
            assert goal in hits
            row.update(witness=a, native_hits=sorted(hits))
        answers.append(row)
        solver.pop()
    assert catalogue.as_dict() == declaration
    (tmp_path/'goal_audit.json').write_text(json.dumps({
        'catalogue': declaration, 'queries': answers, 'observations': observations}, indent=2))
    record_property('local_goal_closure', {'declared': len(catalogue),
        'sat_replayed': sum(row['answer'] == 'sat' for row in answers),
        'local_unsat_retained': sum(row['answer'] == 'unsat' for row in answers)})


@pytest.mark.eda('iverilog', 'vvp')
@pytest.mark.parametrize('arguments', ['"value=%0d",q++', '"scope=%m value=%0d",q++',
                                    '"value=%0d",q++," a=%0d",a'])
@pytest.mark.parametrize('block', ['plain', 'nested', 'named', 'declared'])
def test_failure_argument_prefix_matches_independent_simulator(tmp_path, arguments, block):
    import subprocess

    from behave.sim.rtl.source_plan import plan_source

    from .test_rtl_source_declarations import _documents
    from .test_rtl_source_plan import _run

    source = SOURCE.replace(',r);', ');').replace(
        '  always @(negedge clk) if(a==9) r=3;', '')
    source = source.replace('$fatal(1,"stop")', f'$fatal(1,{arguments})')
    if block == 'nested':
        source = source.replace('if(a==9) $fatal', 'if(a==9) begin $fatal').replace('    q=2;', '    end q=2;')
    elif block == 'named':
        source = source.replace('always @(posedge clk) begin', 'always @(posedge clk) begin: original')
    elif block == 'declared':
        source = source.replace('always @(posedge clk) begin', 'always @(posedge clk) begin int original;')
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    # This oracle has no DPI transport; give the private observer a no-op sink.
    observed = plan.native().text
    import re
    observed, count = re.subn(
        r'import "DPI-C" __behave_native_event_hit = function void (__behave_native_flat_hit\([^;]*);',
        r'function automatic void \1; endfunction', observed)
    assert count == 1
    bench = '''
module TB;
  reg clk=0; reg [3:0] a=0; wire [3:0] q;
  M dut(clk,a,q);
  initial begin #1; a=9; clk=1; #1; $finish; end
  final $display("PREFIX %0d",q);
endmodule
'''
    for name, text in [('plain', source), ('native', observed)]:
        (tmp_path/f'{name}.sv').write_text(text + bench)
        _run(tmp_path, 'iverilog', '-g2012', '-s', 'TB', '-o', name, f'{name}.sv')
    logs = [subprocess.run(['vvp', str(tmp_path/name)], capture_output=True, text=True, timeout=10, check=False)
            for name in ('plain', 'native')]
    assert all(run.returncode == 1 for run in logs)
    assert all('PREFIX 2' in run.stdout for run in logs)
    # Strip filename/line only; source scope, payload and evaluated value agree.
    messages = [next(line.split(': ', 2)[-1] for line in run.stdout.splitlines()
                     if line.startswith('FATAL:')) for run in logs]
    assert messages[0] == messages[1]
