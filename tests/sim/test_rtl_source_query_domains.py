"""Operation definedness belongs to the complete recorded local input domain."""

import pytest

from behave.sim.symbolic import ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_scheduler import lower_source_scheduler

from .test_rtl_source_net_drivers import _prepare
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _names, _state, _step, _settle

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.parametrize('guard,safe', [
    ('a<11', True),
    ('a<11 && en', True),
    ('a<11 && ((a<4 && d<8) || (a>=4 && d<3))', True),
    ('!(a>=11) && (en || d==0)', True),
    ('a<11 && (en ? d<8 : d<3)', True),
    ('a<12 && ((a<4 && d<8) || (a>=4 && d<3))', False),
    ('a<11 || en', False),
    ('a>=11 || !en', False),
])
def test_guarded_array_index_uses_unconditional_source_path_facts(tmp_path, guard, safe):
    from types import SimpleNamespace

    from behave.sim.rtl.source_domains import prove_transition_domains
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.symbolic import ir

    rtl = f'''module M(input clk,en,input [3:0] a,d,output [3:0] y);
      reg [3:0] values[0:10]; assign y=values[0];
      always @(posedge clk) if({guard}) values[a]=d;
    endmodule'''
    plan, ast, sensitivity = _prepare(tmp_path, rtl)
    machine = lower_source_scheduler(rtl, ast, plan, sensitivity, filename='design.sv',
                                     defer_domains=True, initialization=True)
    program = machine.program
    faults = {node.goal for process in program.processes
              for node in ir.walk(process.procedure.body) if isinstance(node, ir.Fail)}
    facts = program.control_facts({}, events=faults, check=lambda: None)
    assert (faults <= facts.absent_events) is safe
    source = SimpleNamespace(machine=machine, template=SourceIntervalTemplate(
        machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges}))
    checks = machine.domain_checks
    proved = prove_transition_domains(source, check=lambda: None)
    assert (faults <= proved) is safe
    assert machine.domain_checks == checks


@pytest.mark.parametrize('signed', [False, True])
def test_source_path_exclusions_ignore_suggested_storage_ranges(tmp_path, signed):
    from behave.sim.rtl.source_program import lower_source_program

    rtl = f'''module M(input clk,en,input {'signed' if signed else ''} [3:0] a,
        output [3:0] y);
      reg [3:0] values[0:10]; assign y=values[0];
      always @(posedge clk) if(en) values[a]=1;
    endmodule'''
    plan, ast, _ = _prepare(tmp_path, rtl)
    program = lower_source_program(rtl, ast, plan, filename='design.sv',
        defer_domains=True, connect_ports=True, initialization=True, native_assignments=True)
    faults = {mark for process in program.processes for mark, _ in process.domain_checks}
    assert faults
    for process in program.processes:
        process.procedure.state_ranges = {name: ranges.Range(0, 0)
                                          for name in process.procedure.state}
    assert not program.control_facts({}, events=faults, check=lambda: None).absent_events


@pytest.mark.parametrize('width,expected', [(8, True), (4, False)])
@pytest.mark.parametrize('conditional', [False, True])
def test_typed_helper_copy_clamp_uses_shared_path_ranges(tmp_path, width, expected, conditional):
    from types import SimpleNamespace

    from behave.sim.rtl.source_domains import _register_range_candidates
    from behave.sim.rtl.source_program import lower_source_program

    rtl = f'''module M(input clk,en,input signed [7:0] a,output reg signed [7:0] x);
      function automatic signed [7:0] clamp(input signed [7:0] v);
        reg signed [{width - 1}:0] result;
        begin
          result=v;
          if(v>10) result=10;
          else if(v<1) result=1;
          clamp=result;
        end
      endfunction
      always @(posedge clk) {'if(en)' if conditional else ''} x<=clamp(a);
    endmodule'''
    plan, ast, _sensitivity = _prepare(tmp_path, rtl)
    program = lower_source_program(rtl, ast, plan, filename='design.sv',
        defer_domains=True, connect_ports=True, initialization=True, native_assignments=True)
    name = next(name for name, field in program.fields.items() if field.name == 'x')
    facts = program.control_facts({name: ranges.Range(0, 10)}, check=lambda: None)
    assert (name in facts.preserved_ranges) is expected
    if expected:
        candidates = _register_range_candidates(SimpleNamespace(program=program), check=lambda: None)
        assert candidates[name] == ranges.Range(1, 10)


@pytest.mark.parametrize('body,upper', [
    ('if(en) x<=10;', 10),
    ('if(en) x<=10; else x<=20;', 20),
    ('if(en) x<=10; if(a[0]) x<=20;', 20),
    ('if(en) x<=10; else x<=a;', None),
    ('if(en) begin x<=20; x<=10; end', 20),
    ('if(1\'b0) x<=a; else if(en) x<=10;', 10),
])
def test_enqueue_range_proposals_ignore_inactive_carriers_not_other_writes(tmp_path, body, upper):
    from types import SimpleNamespace

    from behave.sim.rtl.source_domains import _register_range_candidates
    from behave.sim.rtl.source_program import lower_source_program

    rtl = f'''module M(input clk,en,input [7:0] a,output reg [7:0] x);
      always @(posedge clk) begin {body} end
    endmodule'''
    plan, ast, _sensitivity = _prepare(tmp_path, rtl)
    program = lower_source_program(rtl, ast, plan, filename='design.sv',
        defer_domains=True, connect_ports=True, initialization=True, native_assignments=True)
    name = next(name for name, field in program.fields.items() if field.name == 'x')
    proposed = _register_range_candidates(SimpleNamespace(program=program), check=lambda: None)
    assert (proposed[name].hi if name in proposed else None) == upper


@pytest.mark.parametrize('storage,cell', [('', ''), ('[3:2]', '[3]'), ('[3:2][0:1]', '[3][1]')])
@pytest.mark.parametrize('assignment', ['=', '<='])
@pytest.mark.parametrize('body,expected', [
    ('if(en) qCELL OP 10;', (10, 10)),
    ('if(en) qCELL OP 10; else qCELL OP 20;', (10, 20)),
    ('if(en) begin qCELL OP 20; qCELL OP 10; end', (10, 20)),
    ('if(en) qCELL OP -7; else qCELL OP 3;', (-7, 3)),
    ('if(en) qCELL OP a;', None),
    ("if(1'b0) qCELL OP a; else if(en) qCELL OP 10;", (10, 10)),
])
def test_typed_write_proposals_share_scalar_and_array_transfer(
        tmp_path, storage, cell, assignment, body, expected):
    from types import SimpleNamespace
    from behave.sim.rtl.source_domains import _register_range_candidates
    from behave.sim.rtl.source_program import lower_source_program

    body = body.replace('CELL', cell).replace('OP', assignment)
    rtl = f'''module M(input clk,en,input signed [7:0] a,output y);
      reg signed [7:0] q{storage}; assign y=q{cell}[0];
      always @(posedge clk) begin {body} end
    endmodule'''
    plan, ast, _ = _prepare(tmp_path, rtl)
    program = lower_source_program(rtl, ast, plan, filename='design.sv',
        defer_domains=True, connect_ports=True, initialization=True, native_assignments=True)
    name = next(name for name, field in program.fields.items() if field.name == 'q')
    proposals = _register_range_candidates(SimpleNamespace(program=program), check=lambda: None)
    if assignment == '=' and cell == '[3][1]':
        # Recursive blocking stores copy an enclosing row through a temporary.
        # Its untouched cells keep the old domain: no narrow proposal is safer
        # than pretending that the entire row contains only the new leaf.
        assert name not in proposals
    elif expected is not None:
        assert proposals[name] == ranges.Range(*expected)
    else:
        assert name not in proposals


@pytest.mark.parametrize('shape,cell', [('[3:2]', '[3]'), ('[3:2][0:1]', '[3][1]')])
@pytest.mark.parametrize('body,extra,whole,preserved', [
    ('if(en) qCELL<=1;', '', True, True),
    ('qCELL<=15; qCELL<=1;', '', True, True),
    ('qCELL<=1; qCELL<=15;', '', True, False),
    ('qCELL<=a;', '', True, False),
    ('qCELL[3]<=1; qCELL<=1;', '', False, False),
    ('qCELL<=1; qCELL[3]<=1;', '', False, False),
    ('qCELL<=1;', 'always @(negedge clk) qCELL<=15;', True, False),
    ('qCELL<=1;', 'always @(negedge clk) qCELL=15;', True, False),
])
def test_array_queue_bounds_keep_untouched_cells_and_all_writers(
        tmp_path, shape, cell, body, extra, whole, preserved):
    from behave.sim.rtl.source_program import lower_source_program

    rtl = f'''module M(input clk,en,input [3:0] a,output y);
      reg [3:0] q{shape}; assign y=q{cell}>2;
      always @(posedge clk) begin {body.replace('CELL', cell)} end
      {extra.replace('CELL', cell)}
    endmodule'''
    plan, ast, _ = _prepare(tmp_path, rtl)
    program = lower_source_program(rtl, ast, plan, filename='design.sv',
        defer_domains=True, connect_ports=True, initialization=True, native_assignments=True)
    name = next(name for name, field in program.fields.items() if field.name == 'q')
    writes = [write for process in program.processes for write in process.deferred
              if write.target == name]
    assert writes and all(write.whole is whole for write in writes)
    facts = program.control_facts({name: ranges.Range(0, 2)}, check=lambda: None)
    assert (name in facts.preserved_ranges) is preserved
    # A scalar leaf envelope is uniform. A heterogeneous product must not be
    # silently replaced by its hull and then reported as preserved.
    assert not program.control_facts({name: program.domain(name)},
                                    check=lambda: None).preserved_ranges


@pytest.mark.parametrize('case', ['safe', 'bad-reset', 'partial-reset', 'bad-step',
                                 'reset-prefix', 'failure-prefix', 'no-reset', 'unknown'])
@pytest.mark.parametrize('assignment', ['<=', '='])
def test_array_reset_ranges_are_proved_for_every_cell_not_assumed(tmp_path, case, assignment):
    from types import SimpleNamespace

    from behave.sim.core.query import SolverAnswer
    from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check_finite
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_domains import _register_range_candidates, prove_reset_goals
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from .test_rtl_source_history import _simple_contract

    rtl = '''module M(input clk,rst,iv,input [1:0] a,output ir,ov,
        output reg [1:0] y,input ordy);
      reg [3:0] state[3:2];
      assign ir=!rst; assign ov=iv;
      always @(posedge clk) begin
        if(rst) begin state[3]<=0; state[2]<=0; y<=0; end
        else begin
          if(state[3]==2) state[3]<=0; else state[3]<=state[3]+1;
          if(iv) begin
            if(state[2]==2) state[2]<=0; else state[2]<=state[2]+1;
          end
          if(state[2]>2) y<=a; else y<=0;
        end
      end
    endmodule'''
    if case == 'bad-reset':
        rtl = rtl.replace('state[3]<=0; state[2]<=0;', 'state[3]<=15; state[2]<=0;')
    elif case == 'partial-reset':
        rtl = rtl.replace('state[3]<=0; state[2]<=0;', 'state[2]<=0;')
    elif case == 'bad-step':
        rtl = rtl.replace('if(state[3]==2) state[3]<=0;', 'if(state[3]==2) state[3]<=15;')
    elif case == 'reset-prefix':
        rtl = rtl.replace('state[3]<=0; state[2]<=0;',
                          'if(state[2]>2) y<=a; state[3]<=0; state[2]<=0;')
    elif case == 'failure-prefix':
        rtl = rtl.replace('if(state[2]>2) y<=a;',
                          'if(state[2]<2) begin y<=a; $fatal(1); end')
    rtl = rtl.replace('<=', assignment)
    plan, ast, sensitivity = _prepare(tmp_path, rtl)
    machine = lower_source_scheduler(rtl, ast, plan, sensitivity, filename='design.sv',
        defer_domains=True, initialization=True, native_assignments=True)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    contract = _simple_contract(reset_policy='clears')
    source = SimpleNamespace(machine=machine, template=template, contract=contract,
        initial=template.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True))
    request = SimpleNamespace(codec=ReadyValidRequestCodec(contract, 100, 0 if case == 'no-reset' else 2))
    target = _goal(rtl, plan, 'state[2]<2' if case == 'failure-prefix' else 'state[2]>2', 'true')
    candidates = _register_range_candidates(machine, check=lambda: None)
    assert candidates[_names(machine)['state']] == ranges.Range(0, 2)
    if case == 'safe':
        for process in machine.program.processes:
            queues = [write for write in process.deferred if write.target in candidates]
            if not queues:
                continue
            env = {**process.procedure.state_ranges,
                   **{name: bound for name, bound in candidates.items()
                      if name in process.procedure.state_ranges}}
            post = ranges.procedure_post(process.procedure, env, source_paths=True)
            for write in queues:
                assert post[write.data].hi <= candidates[write.target].hi, (
                    write.target, post[write.data], candidates[write.target])
    calls = []

    def solve(label, roots, **_options):
        calls.append(label)
        if case == 'unknown':
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='fixture interruption')
        return z3_check_finite(roots, 30_000, _owned_context=roots[0].ctx, _prefer_finite=False)

    proved = prove_reset_goals(source, request, (target,), solve=solve, check=lambda: None)
    assert proved == ({target} if case == 'safe' else set()), calls
    if case == 'safe':
        assert {'source-goal:reset-range', 'source-goal:range-startup',
                'source-goal:range-reset'} <= set(calls)
    assert source.machine is machine and source.template is template


@pytest.mark.parametrize('body,extra,expected', [
    ('x<=y; y<=x;', '', {'x', 'y'}),
    ('x<=y; y<=15;', '', set()),
    ('y<=x; x<=15;', '', set()),
    ('x<=0; y<=15;', '', {'x'}),
    ('x<=0; y<=0;', 'always @(negedge clk) x=15;', {'y'}),
    ('x<=0; y<=0;', 'always @(negedge clk) x[3]<=1;', {'y'}),
    ('x<=0; y<=0;', 'always @(negedge clk) x<=15;', {'y'}),
    ('x<=0; y<=0;', 'always @(negedge clk) x<=1;', {'x', 'y'}),
    ('x<=a; y<=x;', '', set()),
    ('x<=15; x<=0; y<=x;', '', {'x', 'y'}),
])
def test_source_queue_ranges_check_all_writers_and_rejected_dependencies(tmp_path, body, extra, expected):
    from behave.sim.rtl.source_program import lower_source_program

    rtl = f'''module M(input clk,input [3:0] a,output reg [3:0] x,y,output reg z);
      always @(posedge clk) begin {body} end
      always @(posedge clk) if(x>2) begin z<=a[0]; $fatal(1); end else z<=0;
      {extra}
    endmodule'''
    plan, ast, sensitivity = _prepare(tmp_path, rtl)
    # Test the source-program owner before scheduler admission: overlapping
    # writers must not become a valid range certificate even when the current
    # scheduler independently refuses those programs. Its gate stays intact.
    program = lower_source_program(rtl, ast, plan, filename='design.sv',
        defer_domains=True, connect_ports=True, initialization=True, native_assignments=True)
    names = {field.name: name for name, field in program.fields.items()}
    proposed = {names[name]: ranges.Range(0, 2) for name in ('x', 'y', 'a')}
    goal = _goal(rtl, plan, 'x>2', 'true')
    marks = {mark for process in program.processes for mark, _, owner in process.occurrences
             if owner == goal}
    assert marks
    facts = program.control_facts(proposed, events=marks, check=lambda: None)
    assert set(facts.preserved_ranges) == {names[name] for name in expected}
    assert facts.absent_events == (marks if 'x' in expected else set())


@pytest.mark.parametrize('case', ['safe', 'bad-reset', 'bad-step', 'no-reset',
                                 'startup', 'reset-prefix', 'failure-prefix', 'unknown'])
@pytest.mark.parametrize('active_low', [False, True])
@pytest.mark.parametrize('assignment', ['<=', '='])
def test_reset_ranges_exclude_events_not_arbitrary_or_failed_entries(
        tmp_path, monkeypatch, case, active_low, assignment):
    from types import SimpleNamespace

    from behave.sim import api
    from behave.sim.core.query import SolverAnswer
    from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check_finite
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_domains import prove_reset_goals, prove_transition_goals
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from .test_rtl_source_history import _simple_contract

    rtl = '''module M(input clk,rst,iv,input [1:0] a,output ir,ov,
        output reg [1:0] y,input ordy);
      reg [3:0] state;
      assign ir=!rst; assign ov=iv;
      always @(posedge clk) begin
        if(rst) begin state<=0; y<=0; end
        else begin
          case(state)
            0: state<=1;
            1: if(iv) state<=2;
            2: state<=0;
            default: state<=0;
          endcase
          if(state==1) y<=a;
        end
      end
    endmodule'''
    text, event = 'case(state)', 'default'
    if case == 'bad-reset':
        rtl = rtl.replace('state<=0; y<=0;', 'state<=15; y<=0;')
    elif case == 'bad-step':
        rtl = rtl.replace('state<=2;', 'state<=15;')
    elif case in {'startup', 'reset-prefix'}:
        text, event = 'state>2', 'true'
        statement = 'if(state>2) y<=a;'
        # Startup control coverage belongs to a combinational process; initial
        # setup statements are not public source-goal declarations.
        rtl = (rtl.replace('reg [3:0] state;',
                   'reg [3:0] state; reg [1:0] started;\n'
                   '      always @* if(state>2) started=a; else started=0;')
               if case == 'startup' else rtl.replace('state<=0; y<=0;', statement + ' state<=0; y<=0;'))
    elif case == 'failure-prefix':
        text, event = 'state==1', 'true'
        rtl = rtl.replace('if(state==1) y<=a;', 'if(state==1) begin y<=a; $fatal(1); end')
    rtl = rtl.replace('<=', assignment)
    contract = _simple_contract(reset_policy='clears')
    if active_low:
        rtl = rtl.replace('if(rst)', 'if(!rst)').replace('ir=!rst', 'ir=rst')
        contract = api.TaskContract(contract.endpoints,
            [api.ClockDomain('c', 'clk', 'rst', reset_active_low=True, reset_restores=True)])
    plan, ast, sensitivity = _prepare(tmp_path, rtl)
    machine = lower_source_scheduler(rtl, ast, plan, sensitivity, filename='design.sv',
        defer_domains=True, initialization=True, native_assignments=True)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    source = SimpleNamespace(machine=machine, template=template, contract=contract,
        initial=template.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True))
    request = SimpleNamespace(codec=ReadyValidRequestCodec(contract, 100, 0 if case == 'no-reset' else 2))
    target = _goal(rtl, plan, text, event)
    live = _goal(rtl, plan, 'state==1')
    calls = []

    def solve(label, roots, **options):
        calls.append(label)
        if case == 'unknown':
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='fixture interruption')
        return z3_check_finite(roots, 30_000, _owned_context=roots[0].ctx, _prefer_finite=False)

    assert not prove_transition_goals(source, (target,), solve=solve, check=lambda: None)
    calls.clear()
    if case in {'bad-reset', 'bad-step', 'no-reset', 'failure-prefix'}:
        def unexpected_history(*_args, **_kwargs):
            pytest.fail('selecting unhelpful range hypotheses must not compile a history template')
        monkeypatch.setattr(SourceIntervalTemplate, 'observations', unexpected_history)
    proved = prove_reset_goals(source, request, (target, live), solve=solve, check=lambda: None)
    assert proved == ({target} if case == 'safe' else set()), calls
    if case in {'bad-reset', 'bad-step', 'no-reset', 'failure-prefix'}:
        assert not calls, 'an unhelpful source transfer must not build an SMT preproof'
    assert source.machine is machine and source.template is template


@pytest.mark.parametrize('shape,cell', [('', ''), ('[2:1]', '[2]'), ('[2:1][0:1]', '[2][1]')])
@pytest.mark.parametrize('body,extra,preserved', [
    ('if(en) qCELL=1;', '', True),
    ('qCELL=1; qCELL=2;', '', True),
    ('qCELL[0]=1;', '', True),
    ('qCELL[3]=1;', '', False),
    ('qCELL=15; qCELL=1;', '', False),
    ('qCELL=15; $fatal(1);', '', False),
    ('qCELL=1; $fatal(1);', '', True),
    ('qCELL=a;', '', False),
    ("if(1'b0) qCELL=15; else qCELL=1;", '', True),
    ('qCELL=1;', 'always @(negedge clk) qCELL=15;', False),
    ('qCELL=1;', 'always @(negedge clk) qCELL<=2;', True),
    ('qCELL=1;', 'always @(negedge clk) qCELL<=15;', False),
])
def test_blocking_range_preservation_checks_all_writes_and_failed_prefixes(
        tmp_path, shape, cell, body, extra, preserved):
    from behave.sim.rtl.source_program import lower_source_program

    rtl = f'''module M(input clk,en,input [3:0] a,output y);
      reg [3:0] q{shape}; assign y=q{cell}>3;
      always @(posedge clk) begin {body.replace('CELL', cell)} end
      {extra.replace('CELL', cell)}
    endmodule'''
    plan, ast, _ = _prepare(tmp_path, rtl)
    program = lower_source_program(rtl, ast, plan, filename='design.sv',
        defer_domains=True, connect_ports=True, initialization=True, native_assignments=True)
    name = next(name for name, field in program.fields.items() if field.name == 'q')
    facts = program.control_facts({name: ranges.Range(0, 3)}, check=lambda: None)
    assert (name in facts.preserved_ranges) is preserved


@pytest.mark.parametrize('body,expected', [
    ('x=y; y=x;', {'x', 'y'}),
    ('x=y; y=15;', set()),
    ('y=x; x=15;', set()),
    ('x=0; y=15;', {'x'}),
    ('x=y; y=15; y=0;', set()),
])
def test_rejected_blocking_range_rechecks_dependent_transfers(tmp_path, body, expected):
    from behave.sim.rtl.source_program import lower_source_program

    rtl = f'''module M(input clk,output reg [3:0] x,y,output reg z);
      always @(posedge clk) begin {body} end
      always @(negedge clk) if(x>3) z=1; else z=0;
    endmodule'''
    plan, ast, _ = _prepare(tmp_path, rtl)
    program = lower_source_program(rtl, ast, plan, filename='design.sv',
        defer_domains=True, connect_ports=True, initialization=True, native_assignments=True)
    names = {field.name: name for name, field in program.fields.items()}
    goal = _goal(rtl, plan, 'x>3', 'true')
    marks = {mark for process in program.processes for mark, _, owner in process.occurrences
             if owner == goal}
    facts = program.control_facts({names[name]: ranges.Range(0, 3) for name in ('x', 'y')},
                                  events=marks, check=lambda: None)
    assert set(facts.preserved_ranges) == {names[name] for name in expected}
    assert facts.absent_events == (marks if 'x' in expected else set())


@pytest.mark.parametrize('body,preserved,absent', [
    ('q[i]=1; if(q[i]>3) z=1;', True, True),
    ('q[i]=15; if(q[i]>3) z=1;', False, False),
    ('q[i]=15; if(q[i]>3) z=1; q[i]=0;', False, False),
    ('q[i]=15; if(q[i]>3) begin z=1; $fatal(1); end', False, False),
])
def test_blocking_loop_preservation_observes_intermediate_writes_and_events(
        tmp_path, body, preserved, absent):
    from behave.sim.rtl.source_program import lower_source_program

    rtl = f'''module M(input clk,output reg z);
      reg [3:0] q[0:2];
      always @(posedge clk) begin
        for(integer i=0;i<3;i=i+1) begin {body} end
      end
    endmodule'''
    plan, ast, _ = _prepare(tmp_path, rtl, enumerate_iterations=False)
    program = lower_source_program(rtl, ast, plan, filename='design.sv',
        defer_domains=True, connect_ports=True, initialization=True, native_assignments=True)
    name = next(name for name, field in program.fields.items() if field.name == 'q')
    goal = _goal(rtl, plan, 'q[i]>3', 'true')
    marks = {mark for process in program.processes for mark, _, owner in process.occurrences
             if owner == goal}
    assert marks
    facts = program.control_facts({name: ranges.Range(0, 3)}, events=marks, check=lambda: None)
    assert (name in facts.preserved_ranges) is preserved
    assert facts.absent_events == (marks if absent else set())


@pytest.mark.parametrize('target', [1, 5])
@pytest.mark.parametrize('failure', [False, True])
def test_loop_event_exclusion_needs_no_register_hypothesis(tmp_path, target, failure):
    from behave.sim.rtl.source_program import lower_source_program

    condition = f'i=={target}'
    rtl = f'''module M(input clk,output reg z);
      always @(posedge clk) begin
        for(integer i=0;i<3;i=i+1) begin
          if({condition}) begin z=1; {'$fatal(1);' if failure else ''} end
        end
      end
    endmodule'''
    plan, ast, _ = _prepare(tmp_path, rtl, enumerate_iterations=False)
    program = lower_source_program(rtl, ast, plan, filename='design.sv',
        defer_domains=True, connect_ports=True, initialization=True, native_assignments=True)
    goal = _goal(rtl, plan, condition, 'true')
    marks = {mark for process in program.processes for mark, _, owner in process.occurrences
             if owner == goal}
    assert marks
    facts = program.control_facts({}, events=marks, check=lambda: None)
    assert facts.absent_events == (marks if target == 5 else set())


@pytest.mark.parametrize('case', ['safe', 'bad-reset', 'bad-step', 'unknown'])
@pytest.mark.parametrize('array', [False, True])
@pytest.mark.parametrize('assignment', ['=', '<='])
def test_signed_write_envelopes_keep_both_bounds_and_real_reset(tmp_path, case, array, assignment):
    from types import SimpleNamespace

    from behave.sim.core.query import SolverAnswer
    from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check_finite
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_domains import prove_reset_goals
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from .test_rtl_source_history import _simple_contract

    rtl = '''module M(input clk,rst,iv,input signed [7:0] a,output ir,ov,
        output reg [1:0] y,input ordy);
      reg signed [7:0] state;
      assign ir=!rst; assign ov=iv;
      function automatic signed [7:0] clamp(input signed [7:0] v);
        reg signed [7:0] result;
        begin
          result=v;
          if(v>10) result=10;
          else if(v< -10) result=-10;
          clamp=result;
        end
      endfunction
      always @(posedge clk) begin
        if(rst) begin state<=0; y<=0; end
        else begin
          if(iv) state<=clamp(a);
          if(state< -10) y<=1;
          else if(state>10) y<=2;
          else if(state<0) y<=3;
          else y<=0;
        end
      end
    endmodule'''
    if case == 'bad-reset':
        rtl = rtl.replace('state<=0;', 'state<=-11;')
    elif case == 'bad-step':
        rtl = rtl.replace('state<=clamp(a);', 'state<=a;')
    if array:
        rtl = rtl.replace('state', 'saved[0]').replace(
            'reg signed [7:0] saved[0];', 'reg signed [7:0] saved[0:0];')
    rtl = rtl.replace('<=', assignment)
    contract = _simple_contract(reset_policy='clears')
    plan, ast, sensitivity = _prepare(tmp_path, rtl)
    machine = lower_source_scheduler(rtl, ast, plan, sensitivity, filename='design.sv',
        defer_domains=True, initialization=True, native_assignments=True)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    source = SimpleNamespace(machine=machine, template=template, contract=contract,
        initial=template.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True))
    request = SimpleNamespace(codec=ReadyValidRequestCodec(contract, 100, 2))
    low, high, live = (_goal(rtl, plan, text.replace('state', 'saved[0]') if array else text, 'true')
                       for text in ('state< -10', 'state>10', 'state<0'))

    def solve(_label, roots, **_options):
        if case == 'unknown':
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='fixture interruption')
        return z3_check_finite(roots, 30_000, _owned_context=roots[0].ctx, _prefer_finite=False)

    proved = prove_reset_goals(source, request, (low, high, live), solve=solve, check=lambda: None)
    assert proved == ({low, high} if case == 'safe' else {high} if case == 'bad-reset' else set())


@pytest.mark.parametrize('case', ['safe', 'bad-reset', 'bad-step', 'reset-first-fault',
                                 'startup-first-fault', 'no-reset', 'unknown'])
@pytest.mark.parametrize('active_low', [False, True])
@pytest.mark.parametrize('request_kind', ['ready-valid', 'fixed-clock', 'fixed-stride'])
def test_reset_domain_bounds_need_real_startup_reset_and_induction(tmp_path, case, active_low, request_kind):
    from types import SimpleNamespace

    from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check_finite
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_domains import _register_range_candidates, prove_reset_domains
    from behave.sim.rtl.source_plan import plan_source
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from .test_rtl_observation_frontend import _installed
    from .test_rtl_source_history import _simple_contract

    rtl = '''module M(input clk,rst,iv,input [1:0] a,output ir,ov,
        output [1:0] y,input ordy);
      reg [3:0] position; reg [23:0] words;
      assign ir=!rst; assign ov=iv; assign y=words[1:0];
      always @(posedge clk) begin
        if(rst) begin position<=0; words<=0; end
        else if(iv) begin
          words[2*position+:2]<=a;
          if(position==11) position<=0; else position<=position+1;
        end
      end
    endmodule'''
    if case == 'bad-reset':
        rtl = rtl.replace('position<=0; words', 'position<=15; words')
    elif case == 'bad-step':
        rtl = rtl.replace('if(position==11) position<=0;', 'if(position==11) position<=12;')
    elif case == 'reset-first-fault':
        rtl = rtl.replace('position<=0; words<=0;', 'words[2*position+:2]<=0; position<=0;')
    elif case == 'startup-first-fault':
        # One startup action isolates its first invalid operation; concurrent
        # failing actions conservatively refuse this optional proof instead.
        rtl = (rtl.replace('output ir,ov', 'output reg ir,ov')
            .replace('output [1:0] y', 'output reg [1:0] y')
            .replace('assign ir=!rst; assign ov=iv; assign y=words[1:0];',
                'initial begin ir=!rst; ov=iv; y=words[2*position+:2]; end'))
    if request_kind != 'ready-valid':
        # Signed counters and offset comparisons use the same proved interval,
        # never a task-specific inferred array bound.
        rtl = rtl.replace('reg [3:0] position', 'integer position')
        if request_kind == 'fixed-clock':
            rtl = rtl.replace('position==11', 'position+1==12')
        else:
            terminal = 12 if case == 'bad-step' else 0
            rtl = rtl.replace(f'if(position==11) position<={terminal}; else position<=position+1;',
                f'if(position+4<12) position<=position+4; else position<={terminal};')
    contract = _simple_contract(reset_policy='clears')
    if active_low:
        rtl = rtl.replace('if(rst)', 'if(!rst)').replace('ir=!rst', 'ir=rst')
        from behave.sim import api
        contract = api.TaskContract(contract.endpoints,
            [api.ClockDomain('c', 'clk', 'rst', reset_active_low=True, reset_restores=True)])
    (tmp_path/'design.sv').write_text(rtl)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, 'M', run=_installed)
    plan = plan_source(rtl, cst, semantic_document=ast, sensitivity_document=sensitivity)
    machine = lower_source_scheduler(rtl, ast, plan, sensitivity, filename='design.sv',
        defer_domains=True, initialization=True, native_assignments=True)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    source = SimpleNamespace(machine=machine, template=template, contract=contract,
        initial=template.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True))
    reset_hold = 0 if case == 'no-reset' else 2
    if request_kind == 'ready-valid':
        codec = ReadyValidRequestCodec(contract, 100, reset_hold)
    else:
        from behave.sim.core.clock_schedule import ClockTiming, GlobalSchedule
        from behave.sim.rtl.atomic_request import AtomicRequestCodec
        timing = ClockTiming('c', 2, clock='clk', reset='rst', reset_active_low=active_low)
        schedule = GlobalSchedule.periodic((timing,), 4, reset_edges={'c': reset_hold})
        codec = AtomicRequestCodec(contract, 100, schedule)
    assert machine.domain_checks
    candidates = _register_range_candidates(machine, check=lambda: None)
    assert candidates[_names(machine)['position']] == ranges.Range(0, 11)
    calls = []

    def solve(label, roots, **options):
        calls.append(label)
        if case == 'unknown':
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='fixture interruption')
        return z3_check_finite(roots, 30_000, _owned_context=roots[0].ctx, _prefer_finite=False)

    proved = prove_reset_domains(source, SimpleNamespace(codec=codec), solve=solve, check=lambda: None)
    assert bool(proved) is (case == 'safe'), calls
    if case == 'safe':
        assert proved == {mark for mark, _ in machine.domain_checks}
        assert {'operation-domain:range-startup', 'operation-domain:range-reset',
                'operation-domain:range-step'} <= set(calls)
    elif case == 'no-reset':
        assert not calls
    # A request-specific proof must not mutate the unrestricted source owner.
    assert source.machine is machine and source.template is template
    assert machine.domain_checks


@pytest.mark.parametrize('case', ['safe', 'bad-index', 'partial-index', 'unknown'])
@pytest.mark.parametrize('array', [False, True])
def test_domain_proof_projects_irrelevant_latched_scratch(tmp_path, case, array):
    from types import SimpleNamespace

    from behave.sim.core.query import SolverAnswer
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_domains import prove_reset_domains
    from behave.sim.rtl.source_plan import plan_source
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check_finite
    from .test_rtl_observation_frontend import _installed
    from .test_rtl_source_history import _simple_contract

    rtl = '''module M(input clk,rst,iv,input [1:0] a,output ir,ov,
        output [1:0] y,input ordy);
      wire [1:0] request=a;
      reg [1:0] selected;
      integer scratch;
      reg [3:0] words;
      assign ir=!rst; assign ov=iv; assign y=words[1:0];
      always @* begin
        selected=0;
        if(request[0]) begin selected=1; scratch=7; end
      end
      always @(posedge clk) begin
        if(rst) words<=0;
        else if(iv) words[2*selected+:2]<=a;
      end
    endmodule'''
    if case == 'bad-index':
        rtl = rtl.replace('selected=1;', 'selected=2;')
    elif case == 'partial-index':
        rtl = rtl.replace('selected=0;', '')
    if array:
        rtl = (rtl.replace('reg [1:0] selected;', 'reg [1:0] selected [0:1]; integer i;')
            .replace('selected=', 'selected[0]=')
            .replace('2*selected+', '2*selected[0]+')
            .replace('selected[0]=0;', 'for(i=0;i<2;i=i+1) selected[i]=0;'))
    (tmp_path/'design.sv').write_text(rtl)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, 'M', run=_installed)
    plan = plan_source(rtl, cst, semantic_document=ast, sensitivity_document=sensitivity)
    machine = lower_source_scheduler(rtl, ast, plan, sensitivity, filename='design.sv',
        defer_domains=True, initialization=True, native_assignments=True)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    contract = _simple_contract(reset_policy='clears')
    source = SimpleNamespace(machine=machine, template=template, contract=contract,
        initial=template.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True))
    request = SimpleNamespace(codec=ReadyValidRequestCodec(contract, 100, 2))
    calls = []

    def solve(label, roots, **options):
        calls.append(label)
        if case == 'unknown':
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='fixture interruption')
        return z3_check_finite(roots, 30_000, _owned_context=roots[0].ctx, _prefer_finite=False)

    try:
        proved = prove_reset_domains(source, request, solve=solve, check=lambda: None)
    except Refused:
        if case == 'safe':
            raise
        proved = frozenset()
    assert bool(proved) is (case == 'safe'), calls
    if proved:
        assert proved == {mark for mark, _ in machine.domain_checks}
    assert source.machine is machine and machine.domain_checks


def test_reset_domain_cycle_does_not_erase_control_only_slots():
    from types import SimpleNamespace

    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
    from behave.sim.rtl.atomic_request import AtomicRequestCodec
    from behave.sim.rtl.source_domains import prove_reset_domains
    from .test_rtl_source_history import _simple_contract

    contract = _simple_contract(reset_policy='clears')
    timing = ClockTiming('c', 2, clock='clk', reset='rst')
    schedule = GlobalSchedule((timing,), (GlobalEventSlot(0, ('c',), ('c',)),
                                         GlobalEventSlot(1), GlobalEventSlot(2, ('c',))))
    source = SimpleNamespace(contract=contract, template=SimpleNamespace(direct=object()),
                             machine=SimpleNamespace(domain_checks=(('fault', 'index'),)))
    request = SimpleNamespace(codec=AtomicRequestCodec(contract, 100, schedule))
    def unexpected(*args, **kwargs):
        pytest.fail('unsupported cycle shape must retain the original exact query')
    assert not prove_reset_domains(source, request, solve=unexpected, check=unexpected)


def _transition_domain_fixture(*, reachable=False):
    from types import SimpleNamespace

    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.rtl.source_program import SourceControlFacts

    x, y, z = (ir.Var(name) for name in ('x', 'y', 'z'))
    failure = ir._sequence((ir.Mark('domain'), ir.Fail('bad', 'bad')))
    # A relational contradiction, not a literal-false source condition.
    guarded = (ir.If(ir.Op('lt', x, ir.Lit(0)), failure, ir.Skip()) if reachable else
        ir.If(ir.Op('lt', x, y),
            ir.If(ir.Op('lt', y, z), ir.If(ir.Op('le', z, x), failure, ir.Skip()), ir.Skip()), ir.Skip()))
    body = ir._sequence((ir.If(ir.Lit(False), ir.Mark('constant'), ir.Skip()),
                         guarded, ir.Mark('public')))
    procedure = ir.Procedure(('x', 'y', 'z'), (), (), (), body,
                             initial={'x': 0, 'y': 0, 'z': 0})
    # Suggested nonnegative ranges must NOT restrict the local lemma.
    template = SourceIntervalTemplate(procedure, {name: ranges.Range(0, 3) for name in procedure.state})
    # Isolate the optional SMT lemma here. Actual source transfer and its
    # unrestricted physical entry are exercised by the source fixtures above.
    program = SimpleNamespace(kinds={}, control_facts=lambda *args, **kwargs:
                              SourceControlFacts({}, frozenset()))
    return SimpleNamespace(machine=SimpleNamespace(program=program,
                           domain_checks=(('constant', None), ('domain', None))),
                           template=template)


@pytest.mark.parametrize('signed', [False, True])
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('context', [False, True])
def test_local_proof_uses_declared_storage_not_suggested_entry_ranges(signed, private, context):
    import z3
    from types import SimpleNamespace

    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.rtl.source_domains import prove_transition_domains, prove_transition_goals
    from behave.sim.rtl.source_integral import IntegralType

    source = _transition_domain_fixture(reachable=True)
    # The inferred range is [0, 3], carried in three signed bits. Declaring
    # unsigned two-bit storage rules out negatives; signed three-bit storage
    # permits them despite that same inferred range and a zero initial value.
    source.machine.program.kinds = {'x': IntegralType(3 if signed else 2, signed)}
    source.machine.occurrences = (('constant', None, 'constant'), ('domain', None, 'domain'))
    if context:
        template, ctx = source.template, z3.Context()
        source.template = SimpleNamespace(local_observations=lambda marks:
            template.local_observations(marks).translate(ctx))
    def solve(phase, roots, **options):
        return z3_check_finite(roots, 30_000, _preparation=options['preparation'],
                               _owned_context=roots[0].ctx)
    got = (prove_transition_domains(source, solve=solve, check=lambda: None) if private else
           prove_transition_goals(source, ('constant', 'domain'), solve=solve, check=lambda: None))
    assert got == ({'constant'} if signed else {'constant', 'domain'})


@pytest.mark.parametrize('bad', [False, True])
def test_local_physical_types_preserve_real_packed_select_faults(tmp_path, bad):
    from types import SimpleNamespace

    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.rtl.source_domains import prove_transition_domains
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from .test_rtl_source_scheduler import _lower

    # Constant table reads with guarded bank and unguarded lane coordinates
    # exercise old stored inputs, not only freshly driven input parameters.
    # Eight lanes fit; the variant's last legal lane really exceeds the table.
    count = 7 if bad else 8
    rtl = f'''module M(input clk, input en, input [1:0] bank, input [2:0] lane,
        output reg [1:0] q);
      localparam [{2*2*count-1}:0] TABLE = 1;
      always @(posedge clk)
        if(en && bank>=1 && bank<=2)
          q <= TABLE[(32'(bank-1)*{count}+32'(lane))*2+:2];
    endmodule'''
    machine, _ = _lower(tmp_path, rtl, defer_domains=True, initialization=True)
    assert machine.domain_checks
    source = SimpleNamespace(machine=machine, template=SourceIntervalTemplate(
        machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges}))
    original = machine.domain_checks
    proved = prove_transition_domains(source,
        solve=lambda phase, roots, **options: z3_check_finite(
            roots, 30_000, _preparation=options['preparation'], _owned_context=roots[0].ctx),
        check=lambda: None)
    assert proved == (frozenset() if bad else frozenset(mark for mark, _ in original))
    assert machine.domain_checks == original


@pytest.mark.parametrize('width', [1, 4])
@pytest.mark.parametrize('signed', [False, True])
def test_local_storage_limits_keep_both_physical_endpoints(width, signed):
    from types import SimpleNamespace

    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_domains import prove_transition_goals
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    kind, x = IntegralType(width, signed), ir.Var('x')
    lo, hi = kind.interval
    predicates = {'below': ir.Op('lt', x, ir.Lit(lo)), 'above': ir.Op('gt', x, ir.Lit(hi)),
                  'lower': ir.Op('eq', x, ir.Lit(lo)), 'upper': ir.Op('eq', x, ir.Lit(hi))}
    body = ir._sequence([ir.If(condition, ir.Mark(name), ir.Skip())
                         for name, condition in predicates.items()])
    procedure = ir.Procedure(('x',), (), (), (), body, initial={'x': 0})
    source = SimpleNamespace(machine=SimpleNamespace(program=SimpleNamespace(kinds={'x': kind}),
        occurrences=tuple((name, None, name) for name in predicates)),
        template=SourceIntervalTemplate(procedure, {'x': ranges.Range(0, 63)}))
    assert prove_transition_goals(source, tuple(predicates),
        solve=lambda phase, roots, **options: z3_check_finite(
            roots, 30_000, _preparation=options['preparation'], _owned_context=roots[0].ctx),
        check=lambda: None) == {'below', 'above'}


@pytest.mark.parametrize('reachable', [False, True])
def test_transition_domain_preparation_does_not_encode_a_speculative_lemma(monkeypatch, reachable):
    from behave.sim.rtl.source_domains import prove_transition_domains
    from behave.sim.symbolic.step_template import StepTemplate

    source = _transition_domain_fixture(reachable=reachable)

    def unexpected(*args, **kwargs):
        pytest.fail('structural preparation must not construct an arbitrary-entry SMT query')

    # Selecting local roots is now part of compilation, before any arbitrary
    # entry is instantiated. Structural preparation must still stop there.
    monkeypatch.setattr(StepTemplate, 'encode_steps', unexpected)
    monkeypatch.setattr(StepTemplate, 'instantiate_steps', unexpected)
    assert prove_transition_domains(source, check=lambda: None) == {'constant'}
    # Even a genuinely impossible relational predicate is left for the
    # complete query; lack of an optional proof never becomes an assumption.
    assert source.machine.domain_checks == (('constant', None), ('domain', None))


@pytest.mark.parametrize('reachable', [False, True])
def test_transition_domain_lemma_uses_arbitrary_state_without_a_success_premise(reachable):
    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.source_domains import prove_transition_domains

    source = _transition_domain_fixture(reachable=reachable)
    calls = []

    def solve(label, roots, **options):
        assert label == 'operation-domain:transition'
        answer = z3_check_finite(roots, 30_000, _preparation=options['preparation'],
                                 _owned_context=roots[0].ctx)
        calls.append(answer.answer)
        return answer

    proved = prove_transition_domains(source, solve=solve, check=lambda: None)
    assert proved == ({'constant'} if reachable else {'constant', 'domain'})
    assert calls == [SolverAnswer.SAT if reachable else SolverAnswer.UNSAT]
    assert 'public' not in proved


@pytest.mark.parametrize('answer', ['sat', 'unknown'])
def test_incomplete_transition_domain_lemma_grants_only_literal_facts(answer):
    import z3

    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.source_domains import prove_transition_domains

    source = _transition_domain_fixture()
    original = source.template.source
    proved = prove_transition_domains(source,
        solve=lambda *args, **kwargs: ExactCheckResult(SolverAnswer(answer),
            model=z3.Model() if answer == 'sat' else None,
            reason='diagnostic interruption' if answer == 'unknown' else ''), check=lambda: None)
    assert proved == {'constant'}
    assert source.template.source is original
    assert source.machine.domain_checks == (('constant', None), ('domain', None))


def test_transition_domain_lemma_preserves_cancellation_and_skips_an_empty_domain():
    from behave.sim.rtl.source_domains import prove_transition_domains

    source = _transition_domain_fixture()
    def stop():
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError, match='cancelled'):
        prove_transition_domains(source, solve=None, check=stop)
    source.machine.domain_checks = ()
    assert not prove_transition_domains(source, solve=None, check=lambda: None)


@pytest.mark.parametrize('live_failure', [False, True])
def test_proved_domain_projection_preserves_the_unrestricted_transition(tmp_path, live_failure):
    from dataclasses import replace
    from types import SimpleNamespace
    import z3

    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.symbolic import ir
    from behave.sim.core.refusal import Refusal
    from behave.sim.symbolic.step_template import _leaves
    from behave.sim.rtl.source_domains import _elide_transition_domains, prove_transition_domains
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.rtl.source_scheduler import _compose_source_scheduler
    from .test_rtl_source_scheduler import _lower, _names

    original, _ = _lower(tmp_path, '''module M(input [3:0] a,b,c, output reg [3:0] q);
        always_comb q = a + b + c;
        endmodule''')
    names = _names(original)
    a, b, c = (ir.Var(names[name]) for name in ('a', 'b', 'c'))
    # This contradiction needs relational reasoning, not sampled values or
    # a range hint. Another failure remains reachable for arbitrary entry.
    faults = [ir.If(ir.Op('lt', a, b),
        ir.If(ir.Op('lt', b, c),
            ir.If(ir.Op('le', c, a), ir.Fail('PrivateFault', 'dead'), ir.Skip()),
            ir.Skip()), ir.Skip())]
    checks = [('dead', Refusal('test-domain', 'impossible domain exit'))]
    if live_failure:
        faults.append(ir.If(ir.Op('eq', a, ir.Lit(0)),
                            ir.Fail('OtherFault', 'live'), ir.Skip()))
        checks.append(('live', Refusal('test-domain', 'reachable domain exit')))
    process = original.program.processes[0]
    old = process.procedure
    proc = ir.Procedure(old.state, old.inputs, old.locals, old.outputs,
        ir._sequence([*faults, old.body]), types=old.types)
    proc.state_ranges = old.state_ranges
    process = replace(process, procedure=proc,
        activation=ir._sequence([*faults, process.activation]), domain_checks=tuple(checks))
    machine = _compose_source_scheduler(replace(original.program, processes=(process,)),
                                        original.subscriptions, original.startup)

    def template(owner):
        return SourceIntervalTemplate(owner.procedure,
            {**owner.procedure.state_ranges, **owner.input_ranges})

    before = template(machine)
    source = SimpleNamespace(machine=machine, template=before)
    proved = prove_transition_domains(source,
        solve=lambda _phase, roots, **options: z3_check_finite(
            roots, 30_000, _preparation=options['preparation'], _owned_context=roots[0].ctx),
        check=lambda: None)
    assert proved == {'dead'}
    projected = _elide_transition_domains(machine, proved, check=lambda: None)
    assert projected is not machine
    assert machine.domain_checks == tuple(checks)
    assert projected.domain_checks == (tuple(checks[1:]) if live_failure else ())
    assert projected.event_bound == machine.event_bound
    assert projected.occurrences == machine.occurrences
    left = before.source.encode(tag='original')
    right = template(projected).source.encode(left.pre, tag='projected')
    differences = [left.ok != right.ok, left.error != right.error]
    differences.extend(left.state_post[k] != right.state_post[k] for k in left.state_post)
    differences.extend(left.hits[k] != right.hits[k] for k in left.hits)
    differences.extend(a != b for k in left.post
        for a, b in zip(_leaves(left.post[k]), _leaves(right.post[k]), strict=True))
    solver = z3.Solver()
    solver.add(left.constraint, right.constraint, z3.Or(*differences))
    answer = solver.check()
    assert answer == z3.unsat, {
        'inputs': {k: str(solver.model().eval(v)) for k, v in left.pre.items()},
        'differences': [str(term) for term in differences
                        if z3.is_true(solver.model().eval(term))]}

    # Only query syntax/caches change; the original native owner and physical
    # request coordinates remain available, without adopting the query view.
    from behave.sim.rtl.exact_job_instance import PreparedSourceExact
    owner = object.__new__(PreparedSourceExact)
    owner.machine, owner.template = machine, before
    owner._coordinates = {3: object()}
    owner._requests, owner._query_requests = {3: object()}, {3: object()}
    owner._native_views = object()
    owner.__dict__['relation'] = object()
    view = owner._domain_query_view(proved, check=lambda: None)
    assert owner.machine is machine and owner.template is before
    assert view.machine is not machine and view.template is not before
    assert view._coordinates is owner._coordinates
    assert view._requests == view._query_requests == {}
    assert owner._requests and owner._query_requests and owner._native_views is not None
    assert view._native_views is None and 'relation' not in view.__dict__
    assert owner._domain_query_view((), check=lambda: None) is owner
    assert _elide_transition_domains(machine, (), check=lambda: None) is machine
    with pytest.raises(ValueError, match='private marks'):
        _elide_transition_domains(machine, {'not-this-machine'}, check=lambda: None)
    with pytest.raises(ValueError, match='private marks'):
        _elide_transition_domains(machine, {machine.occurrences[0][0]}, check=lambda: None)

    def cancelled():
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError, match='cancelled'):
        _elide_transition_domains(machine, proved, check=cancelled)


@pytest.mark.parametrize('reachable', [False, True])
def test_local_source_goals_have_no_entry_range_or_success_assumption(reachable):
    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.rtl.source_domains import prove_transition_goals

    source = _transition_domain_fixture(reachable=reachable)
    source.machine.occurrences = (
        ('constant', None, 'constant-event'), ('domain', None, 'event'),
        ('public', None, 'unselected-event'))
    calls = []
    def solve(phase, roots, **options):
        calls.append(phase)
        return z3_check_finite(roots, 30_000, _preparation=options['preparation'],
                               _owned_context=roots[0].ctx)
    proved = prove_transition_goals(source, ('constant-event', 'event', 'monitor.sink-stability'),
                                    solve=solve, check=lambda: None)
    assert proved == ({'constant-event'} if reachable else {'constant-event', 'event'})
    assert calls == ['source-goal:transition']


def test_one_live_occurrence_prevents_a_local_source_goal_proof():
    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.rtl.source_domains import prove_transition_goals

    source = _transition_domain_fixture()
    source.machine.occurrences = (('constant', None, 'event'), ('public', None, 'event'))
    assert not prove_transition_goals(source, ('event',),
        solve=lambda _phase, roots, **options: z3_check_finite(
            roots, 30_000, _preparation=options['preparation'],
            _owned_context=roots[0].ctx), check=lambda: None)


def test_unknown_local_source_goal_retains_the_full_query_and_catalogue():
    from behave.sim.symbolic.exact_solve import ExactCheckResult
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.source_domains import prove_transition_goals

    source = _transition_domain_fixture()
    occurrences = (('constant', None, 'constant-event'), ('domain', None, 'event'))
    source.machine.occurrences = occurrences
    template = source.template.source
    assert prove_transition_goals(source, ('constant-event', 'event'),
        solve=lambda *_, **options: ExactCheckResult(SolverAnswer.UNKNOWN, reason='interrupted'),
        check=lambda: None) == {'constant-event'}
    assert source.machine.occurrences is occurrences
    assert source.template.source is template
    assert not prove_transition_goals(source, ('monitor.sink-stability',),
                                     solve=None, check=lambda: None)


def test_local_source_goal_proof_honors_cancellation():
    from behave.sim.rtl.source_domains import prove_transition_goals

    def stop():
        raise InterruptedError('cancelled')
    with pytest.raises(InterruptedError, match='cancelled'):
        prove_transition_goals(_transition_domain_fixture(), ('event',), solve=None, check=stop)


@pytest.mark.parametrize('private', [False, True])
def test_mixed_local_groups_share_one_formula_without_pinning_a_sat_model(private):
    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.source_domains import prove_transition_domains, prove_transition_goals

    source = _transition_domain_fixture()
    source.machine.domain_checks += (('public', None),)
    source.machine.occurrences = tuple((name, None, name) for name in ('constant', 'domain', 'public'))
    calls, bases = [], []

    def solve(phase, roots, *, preparation):
        bases.append((roots[0], preparation))
        answer = z3_check_finite(roots, 30_000, _preparation=preparation,
                                 _owned_context=roots[0].ctx)
        calls.append(answer.answer)
        return answer

    got = (prove_transition_domains(source, solve=solve, check=lambda: None) if private else
           prove_transition_goals(source, ('constant', 'domain', 'public'), solve=solve, check=lambda: None))
    assert got == {'constant', 'domain'}
    assert type(got) is frozenset
    assert calls == [SolverAnswer.SAT, SolverAnswer.UNSAT]
    assert bases[0][0] is bases[1][0] and bases[0][1] is bases[1][1]
    assert bases[0][1]._solver is None  # No optional solver survives its owner.


def test_local_group_models_and_query_suffixes_do_not_restrict_later_groups():
    from types import SimpleNamespace

    from behave.sim.symbolic.exact_solve import z3_check_finite
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.source_domains import prove_transition_goals

    # Each group is possible, but no entry state hits both. A pinned model or
    # a retained previous target would incorrectly make the second UNSAT.
    x = ir.Var('x')
    procedure = ir.Procedure(('x',), (), (), (),
        ir.If(ir.Op('lt', x, ir.Lit(0)), ir.Mark('negative'), ir.Mark('nonnegative')),
        initial={'x': 0})
    source = SimpleNamespace(machine=SimpleNamespace(program=SimpleNamespace(kinds={}), occurrences=(
        ('negative', None, 'negative'), ('nonnegative', None, 'nonnegative'))),
        template=SourceIntervalTemplate(procedure, {'x': ranges.Range(-3, 3)}))
    answers = []

    def solve(phase, roots, *, preparation):
        answer = z3_check_finite(roots, 30_000, _preparation=preparation,
                                 _owned_context=roots[0].ctx)
        answers.append(answer.answer)
        return answer

    assert not prove_transition_goals(source, ('negative', 'nonnegative'),
        solve=solve, check=lambda: None)
    assert answers == [SolverAnswer.SAT, SolverAnswer.SAT]


def test_mixed_local_unknown_keeps_every_unproved_group_in_the_full_query():
    from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check_finite
    from behave.sim.core.query import SolverAnswer
    from behave.sim.rtl.source_domains import prove_transition_goals

    source = _transition_domain_fixture()
    source.machine.occurrences = tuple((name, None, name) for name in ('constant', 'domain', 'public'))
    calls = []

    def solve(phase, roots, *, preparation):
        calls.append(preparation)
        if len(calls) == 1:
            answer = z3_check_finite(roots, 30_000, _preparation=preparation,
                                     _owned_context=roots[0].ctx)
            assert answer.answer is SolverAnswer.SAT
            return answer
        return ExactCheckResult(SolverAnswer.UNKNOWN, reason='original owner interrupted')

    assert prove_transition_goals(source, ('constant', 'domain', 'public'),
        solve=solve, check=lambda: None) == {'constant'}
    assert len(calls) == 2 and calls[0] is calls[1] and calls[0]._solver is None


def test_local_group_cancellation_clears_preparation_without_returning_a_fact():
    from behave.sim.rtl.source_domains import prove_transition_domains

    retained = []
    def solve(phase, roots, *, preparation):
        retained.append(preparation)
        raise InterruptedError('cancel local groups')

    with pytest.raises(InterruptedError, match='cancel local groups'):
        prove_transition_domains(_transition_domain_fixture(), solve=solve, check=lambda: None)
    assert len(retained) == 1 and retained[0]._solver is None

SOURCE = """module M(input [1:0] a, input [2:0] limit, output logic [7:0] q);
  wire [2:0] count = DRIVER;
  logic [7:0] mem [0:3];
  integer i;
  always_comb begin
    for(i=0;i<4;i=i+1) mem[i] = a+i;
    for(i=0;i<4;i=i+1)
      if(i+1<count) mem[i+1] = mem[i+1]+mem[i];
    if(a==3) q=mem[3]; else q=0;
  end
endmodule
"""


def _case(tmp_path, driver, *, condition="a==3"):
    source = SOURCE.replace("DRIVER", driver).replace("if(a==3)", f"if({condition})")
    plan, ast, sensitivity = _prepare(tmp_path, source, enumerate_iterations=False)
    with pytest.raises(Refused) as error:
        lower_source_scheduler(source, ast, plan, sensitivity, filename="design.sv")
    assert error.value.refusal.detail["domain_proof"] == "sat"
    machine = lower_source_scheduler(source, ast, plan, sensitivity,
        filename="design.sv", defer_domains=True)
    assert machine.domain_checks
    public = {goal for _mark, _key, goal in machine.occurrences}
    assert public.isdisjoint(mark for mark, _cause in machine.domain_checks)
    state = _state(machine, {"a": 0, "limit": 4, "count": 0,
                             "mem": [0]*4, "i": 0, "q": 0}, startup=True)
    state, _hits = _settle(machine, state)
    assert machine.boundary_is_quiet(state)
    # Match the prepared local worker: finite compiler-owned storage carriers,
    # not a newly inferred cross-activation invariant. These tests still prove
    # the full mutable input domain before asking about any public target.
    env = {**machine.procedure.state_ranges, **machine.input_ranges}
    return source, plan, machine, state, env


@pytest.mark.parametrize("driver", ["3'd4", "limit"])
@pytest.mark.eda("verilator", "make")
def test_recorded_safe_domain_is_proved_before_a_public_goal_query(tmp_path, driver):
    from dataclasses import replace

    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_locations import _collector

    source, plan, machine, state, env = _case(tmp_path, driver)
    names = _names(machine)
    target = _goal(source, plan, "q=mem[3]", "entry", "location")
    answer = query_source_interval(machine, env, state,
        {names["a"]: 0, names["limit"]: 4}, (target,), mutable_inputs=(names["a"],),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate[names["a"]] == 3
    changed, _, hits, _, _ = _step(machine, state, 0, a=3, limit=4)
    after, more_hits = _settle(machine, changed)
    assert target in hits | more_hits
    assert after[names["q"]] == 18
    collector, manifest = _collector(plan)
    assert collector.word_count == 1
    native_driver = f"""
      const unsigned values[]={{0,{answer.candidate[names['a']]}}};
      for(unsigned i=0;i<2;i++) {{
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.limit=4; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }}
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, native_driver)
    observed = _trace(tmp_path / "observed", plan.native(scoped=True).text,
                      collector, native_driver, native=True)
    assert [row[0] for row in original] == [row[0] for row in observed] == [0, 18]
    hits = [collector.bindings.decode({index for index in range(collector.bindings.event_count)
                                      if row[1] >> index & 1}).goals for row in observed]
    assert target not in hits[0] and target in hits[1]
    assert all(seen <= manifest.keys() for seen in hits)


def test_mutable_driver_is_not_replaced_by_its_baseline_value(tmp_path):
    source, plan, machine, state, env = _case(tmp_path, "limit")
    names = _names(machine)
    target = _goal(source, plan, "q=mem[3]", "entry", "location")
    answer = query_source_interval(machine, env, state,
        {names["a"]: 0, names["limit"]: 4}, (target,),
        mutable_inputs=(names["a"], names["limit"]),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.UNSUPPORTED, answer.detail
    assert "invalid operation in this local input domain" in answer.detail
    assert answer.candidate is None


def test_constant_driver_does_not_erase_an_invalid_supplied_boundary(tmp_path):
    source, plan, machine, state, env = _case(tmp_path, "3'd4")
    names = _names(machine)
    state[names["count"]] = 7
    target = _goal(source, plan, "q=mem[3]", "entry", "location")
    answer = query_source_interval(machine, env, state,
        {names["a"]: 0, names["limit"]: 4}, (target,), mutable_inputs=(names["a"],),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.UNSUPPORTED, answer.detail
    assert "invalid operation in this local input domain" in answer.detail


def test_goal_condition_cannot_hide_an_invalid_input_domain(tmp_path):
    source, plan, machine, state, env = _case(tmp_path, "limit", condition="a==3 && limit==0")
    names = _names(machine)
    target = _goal(source, plan, "q=mem[3]", "entry", "location")
    # All inputs satisfying this goal have safe accesses. Other allowed inputs
    # do not, so a goal-conjoined domain check would incorrectly accept it.
    answer = query_source_interval(machine, env, state,
        {names["a"]: 0, names["limit"]: 4}, (target,),
        mutable_inputs=(names["a"], names["limit"]),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.UNSUPPORTED, answer.detail
    assert "invalid operation in this local input domain" in answer.detail


def test_source_projection_omits_only_already_false_private_domain_images(tmp_path):
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    source, plan, machine, state, env = _case(tmp_path, 'limit')
    target = _goal(source, plan, 'q=mem[3]', 'entry', 'location')
    template = SourceIntervalTemplate(machine.procedure, env)
    names = {name for name, _cause in machine.domain_checks}
    blocked = template.source.literal_false_hits(names)
    assert blocked and names - blocked
    # The drive view is an exact specialization of the unrestricted source.
    assert blocked <= template.drive.literal_false_hits(names)
    relation = template.relation(machine, (target,))
    assert set(relation.domain_names) == names - blocked
    assert relation.goal_names == (target,)
    assert {name for name, _cause in machine.domain_checks} == names
    pins = _names(machine)
    answer = query_source_interval(machine, env, state,
        {pins['a']: 0, pins['limit']: 4}, (target,),
        mutable_inputs=(pins['a'], pins['limit']),
        event_bound=machine.event_bound, timeout_ms=30_000, _relation=relation)
    assert answer.outcome is probing.Outcome.UNSUPPORTED, answer.detail
    assert answer.candidate is None


def test_all_following_physical_phases_must_have_defined_operations(tmp_path):
    source, plan, machine, state, env = _case(tmp_path, "limit")
    names = _names(machine)
    target = _goal(source, plan, "q=mem[3]", "entry", "location")
    answer = query_source_interval(machine, env, state,
        {names["a"]: 0, names["limit"]: 4}, (target,),
        mutable_inputs=(names["a"],),
        following_inputs=({names["a"]: 0, names["limit"]: 7},),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.UNSUPPORTED, answer.detail
    assert "invalid operation in this local input domain" in answer.detail


def test_unfinished_domain_proof_is_unknown_not_a_goal_answer(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from behave.sim.symbolic import exact_solve
    from behave.sim.core.query import SolverAnswer

    source, plan, machine, state, env = _case(tmp_path, "3'd4")
    names = _names(machine)
    target = _goal(source, plan, "q=mem[3]", "entry", "location")
    calls = []

    def unfinished(constraints, timeout_ms, **kwargs):
        calls.append(timeout_ms)
        return SimpleNamespace(answer=SolverAnswer.UNKNOWN, reason="controlled timeout")

    monkeypatch.setattr(exact_solve, "z3_check_qfbv", unfinished)
    answer = query_source_interval(machine, env, state,
        {names["a"]: 0, names["limit"]: 4}, (target,),
        mutable_inputs=(names["a"],), event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.UNKNOWN
    assert answer.candidate is None and len(calls) == 1
    assert "source operation-domain proof: controlled timeout" == answer.detail


def test_invalid_prefix_is_not_hidden_by_a_nonquiescent_suffix(tmp_path):
    source = """module M(input clk,input [1:0] a,output logic [7:0] q,out);
      logic [7:0] mem [0:1];
      always @(posedge clk or q) if(clk) begin
        mem[0]=1; mem[1]=2;
        out=mem[a];
        q<=q+1;
      end
    endmodule"""
    plan, ast, sensitivity = _prepare(tmp_path, source, enumerate_iterations=False)
    machine = lower_source_scheduler(source, ast, plan, sensitivity,
        filename="design.sv", defer_domains=True)
    assert machine.event_bound is None  # The NBA update schedules itself again.
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "out": 0, "mem": [0, 0]})
    env = ranges.fixpoint(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges})
    target = _goal(source, plan, "out=mem[a]", "entry", "location")
    # With an ordinary marker, the later quiet-end requirement would erase the
    # whole invalid prefix. A private terminal fault must remain satisfiable.
    answer = query_source_interval(machine, env, state,
        {names["clk"]: 1, names["a"]: 0}, (target,), mutable_inputs=(names["a"],),
        event_bound=5, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.UNSUPPORTED, answer.detail
    assert "invalid operation in this local input domain" in answer.detail
