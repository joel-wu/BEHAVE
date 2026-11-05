"""Goal-local UNSAT proofs must not certify a cut-state counterexample."""

from dataclasses import replace
import time

import pytest
import z3

from behave.sim.symbolic.exact_solve import ExactCheckResult, z3_check_finite
from behave.sim.analysis_budget import analysis_scope
from behave.sim.core.query import SolverAnswer
from behave.sim.rtl.analysis_job import _SourceAnalysis
from behave.sim.rtl.exact_job_instance import PreparedSourceExact
from behave.sim.rtl.source_direct import DirectSourceCompiler
from behave.sim.rtl.source_stability import (
    COUNT_GOAL, GOAL, _boundary_candidates, prove_protocol_goals,
    prove_reset_restoration, prove_stability,
)

from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract
from .test_rtl_source_worker_task import _worker

pytestmark = [pytest.mark.cosim, pytest.mark.solver]

COMBINATIONAL = '''module M(input clk,rst,iv,input [1:0] a,output ir,
  output ov,output [1:0] y,input ordy);
  assign ir=!rst && ordy;
  assign ov=!rst && iv;
  assign y=a;
endmodule'''


@pytest.fixture(autouse=True)
def budget():
    with analysis_scope(120):
        yield


@pytest.mark.parametrize('mode', ['scalar', 'array', 'retained', 'input-dependent', 'two-step'])
@pytest.mark.parametrize('hold', [1, 2])
def test_reset_image_uses_all_owned_state_and_independent_input_histories(tmp_path, mode, hold):
    rtl = HISTORY_SOURCE
    if mode == 'array':
        rtl = rtl.replace('reg [1:0] saved;', 'reg [1:0] saved[2:1];')
        rtl = rtl.replace('saved<=0;', 'saved[1]<=0; saved[2]<=0;')
        rtl = rtl.replace('saved==1', '(saved[1]^saved[2])==1')
        rtl = rtl.replace('saved<=a;', 'saved[1]<=a; saved[2]<=saved[1];')
    elif mode == 'retained':
        rtl = rtl.replace('saved<=0;', 'saved<=saved;')
    elif mode == 'input-dependent':
        rtl = rtl.replace('saved<=0;', 'saved<=a;')
    elif mode == 'two-step':
        rtl = rtl.replace('y<=0;', 'y<=saved;')
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    source.reset_hold = hold
    request = source.request_coordinates(6)
    goal = 'monitor.reset-restoration.c.violation'
    calls = []

    def solve(label, roots, **options):
        answer = z3_check_finite(roots, 30_000)
        calls.append((label, answer.answer))
        return answer

    proved = prove_reset_restoration(source, request, (goal,), solve=solve, check=lambda: None)
    expected = mode in {'scalar', 'array'} or mode == 'two-step' and hold >= 2
    assert (goal in proved) is expected, calls
    assert calls[-1][1] is (SolverAnswer.UNSAT if expected else SolverAnswer.SAT)
    assert 'monitor.reset-restoration.c.scenario' not in proved


@pytest.mark.parametrize('mode', ['correct', 'broken'])
def test_reset_proof_keeps_complete_query_and_reachable_violation(tmp_path, mode):
    rtl = HISTORY_SOURCE if mode == 'correct' else HISTORY_SOURCE.replace('saved<=0;', 'saved<=saved;')
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    exact = _SourceAnalysis(source, 6)
    before = exact.domain.digest()
    goal = 'monitor.reset-restoration.c.violation'
    result = exact.call('query', {'goals': [goal], 'timeout_ms': 30_000},
                        started_ns=time.monotonic_ns())
    assert goal in result['unreachable' if mode == 'correct' else 'reachable'], result
    assert result['queries'][0]['solver_calls'] >= 1
    assert exact.domain.digest() == before


def test_reset_image_unknown_never_supplies_a_proof(tmp_path):
    context, *_ = _worker(tmp_path, HISTORY_SOURCE, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    goal = 'monitor.reset-restoration.c.violation'
    calls = []

    def unknown(label, roots, **options):
        calls.append(label)
        return ExactCheckResult(SolverAnswer.UNKNOWN, reason='injected solver uncertainty')

    assert not prove_reset_restoration(source, source.request_coordinates(6), (goal,),
        solve=unknown, check=lambda: None)
    assert calls == ['reset:constant-image']


def test_proved_reset_image_does_not_hide_an_unencoded_exit(tmp_path, monkeypatch):
    from behave.sim.core.refusal import Refused

    context, *_ = _worker(tmp_path, HISTORY_SOURCE, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    exact = _SourceAnalysis(source, 6)
    original = source.bind_request
    batches = []

    def with_escape(horizon, *, goals=None):
        relation, request, encoded = original(horizon, goals=goals)
        batches.append(goals)
        return relation, request, replace(encoded, monitor_faults=(
            {'test-domain-fault': z3.BoolVal(True, ctx=encoded.alive.ctx)},))

    monkeypatch.setattr(source, 'bind_request', with_escape)
    goal = 'monitor.reset-restoration.c.violation'
    with pytest.raises(Refused) as error:
        exact.call('query', {'goals': [goal], 'timeout_ms': 30_000}, started_ns=time.monotonic_ns())
    assert error.value.refusal.kind == 'rtl-source-operation-domain-unencoded'
    assert batches and all(goal not in batch for batch in batches)


@pytest.fixture(scope='module', params=[
    'registered', 'combinational', 'overwrite', 'drop-valid', 'ready-dependent', 'late-error'])
def design(tmp_path_factory, request):
    kind = request.param
    rtl = COMBINATIONAL if kind in {'combinational', 'ready-dependent'} else HISTORY_SOURCE
    if kind == 'overwrite':
        rtl = rtl.replace('iv && ir', 'iv')
    elif kind == 'drop-valid':
        rtl = rtl.replace('else if(ordy) ov<=0;', 'else ov<=0;')
    elif kind == 'ready-dependent':
        rtl = rtl.replace('assign y=a;', 'assign y=a ^ {1\'b0,ordy};')
    elif kind == 'late-error':
        rtl = rtl.replace('reg [1:0] saved;', 'reg [1:0] saved; reg [3:0] count;')
        rtl = rtl.replace('if(rst) begin', 'if(rst) begin count<=0;')
        rtl = rtl.replace('else if(iv && ir) begin', 'else if(count==9) begin y<=y+1; count<=count+1; end\n'
                          '    else if(iv && ir) begin count<=count+1;')
    context, *_ = _worker(tmp_path_factory.mktemp(kind), rtl, _simple_contract(reset_policy='clears'), 6)
    with analysis_scope(120):
        source = PreparedSourceExact(context.source_payload(), direct=True)
        exact = _SourceAnalysis(source, 6)
    return kind, exact


def test_only_safe_designs_get_a_local_proof(design):
    kind, exact = design
    checks = []

    def solve(label, roots, **options):
        answer = z3_check_finite(roots, 30_000, _prefer_finite=options.get('prefer_finite', False))
        checks.append((label, answer.answer))
        return answer

    proved = prove_stability(exact.source, exact.request, solve=solve, check=lambda: None)
    assert proved is (kind in {'registered', 'combinational'}), checks
    windows = [answer for label, answer in checks
               if label.removesuffix(':data-cut') == 'stability:window']
    assert windows
    # Even a bug activated after more cycles than this test's finite horizon
    # must prevent the arbitrary-state proof. Local SAT is never published.
    assert windows[-1] is (SolverAnswer.UNSAT if proved else SolverAnswer.SAT)


@pytest.mark.parametrize('overwrite', [False, True])
def test_register_boundary_does_not_require_a_hidden_datapath_fixed_point(tmp_path, overwrite):
    rtl = HISTORY_SOURCE.replace('reg [1:0] saved;', '''reg [1:0] saved;
      reg [1:0] datapath;
      always_comb begin datapath=0;
        for(integer i=0;i<4;i++) datapath=datapath+a;
      end''').replace('else y<=a;', 'else y<=datapath;')
    if overwrite:
        rtl = rtl.replace('iv && ir', 'iv')
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    exact = _SourceAnalysis(source, 6)
    writes = {source.machine.program.fields[name].name
              for process in _boundary_candidates(source.machine)
              for name, blocking, _ in process.write_masks if blocking}
    assert writes == {'ir'}

    def solve(_label, roots, **options):
        return z3_check_finite(roots, 30_000, _prefer_finite=options.get('prefer_finite', False))

    # The omitted computation is still executed, including its source-domain
    # guards. A real overwrite during backpressure must remain unproved.
    assert prove_stability(source, exact.request, solve=solve, check=lambda: None) is (not overwrite)


@pytest.mark.parametrize('overwrite', [False, True])
@pytest.mark.parametrize('cells', [2, 8])
def test_static_array_datapath_uses_the_same_protocol_proof(tmp_path, cells, overwrite):
    from types import SimpleNamespace
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from .test_rtl_source_scheduler import _lower

    wiring = f'wire [1:0] lanes[0:{cells - 1}];\n' + '\n'.join(
        f'assign lanes[{i}] = ' + ('a;' if i == 0 else f'lanes[{i - 1}] ^ saved;')
        for i in range(cells))
    rtl = HISTORY_SOURCE.replace('reg [1:0] saved;', 'reg [1:0] saved;\n' + wiring)
    rtl = rtl.replace('else y<=a;', f'else y<=lanes[{cells - 1}];')
    if overwrite:
        rtl = rtl.replace('iv && ir', 'iv')
    machine, _ = _lower(tmp_path, rtl, initialization=True)
    direct = DirectSourceCompiler(machine)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges}, direct=direct).normalized()
    relation = template.relation(machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    contract = _simple_contract(reset_policy='clears')
    source = SimpleNamespace(machine=machine, contract=contract, template=template,
                             relation=relation, initial=initial, queue_bound=6)
    request = SimpleNamespace(codec=ReadyValidRequestCodec(contract, 6, 2))
    labels = []

    def solve(label, roots, **options):
        labels.append(label)
        return z3_check_finite(roots, 30_000, _prefer_finite=options.get('prefer_finite', False))

    assert prove_stability(source, request, solve=solve, check=lambda: None) is (not overwrite)
    assert not any(label.startswith('boundary:') for label in labels), labels
    assert any(label.startswith('stability:window') for label in labels), labels


@pytest.mark.parametrize('registers', [2, 12])
@pytest.mark.parametrize('connected', [False, True])
def test_protocol_projection_omits_only_unobserved_register_successors(
        tmp_path, monkeypatch, registers, connected):
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    unrelated = '\n'.join(
        f'reg [1:0] unused_{i}; always @(posedge clk) '
        f'unused_{i} <= unused_{i} * unused_{i} + a;'
        for i in range(registers))
    rtl = HISTORY_SOURCE.replace('endmodule', unrelated + '\nendmodule')
    if connected:
        rtl = rtl.replace('else if(iv && ir) begin',
            'else if(ov && !ordy) begin y<=unused_0; end\n'
            '    else if(iv && ir) begin')
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    exact = _SourceAnalysis(source, 6)
    original, views = SourceIntervalTemplate.relation, []

    def relation(self, machine, *args, **kwargs):
        result = original(self, machine, *args, **kwargs)
        if self is source.template and machine is source.machine:
            views.append(result)
        return result

    monkeypatch.setattr(SourceIntervalTemplate, 'relation', relation)

    def solve(_label, roots, **options):
        return z3_check_finite(roots, 30_000, _prefer_finite=options.get('prefer_finite', False))

    assert prove_stability(source, exact.request, solve=solve, check=lambda: None) is (not connected)
    assert len(views) == 2  # Payload window and its separate control-induction cone.
    physical = source.machine.program.fields
    retained = {physical[name].name for name in views[0].source.state_names if name in physical}
    assert {name for name in retained if name.startswith('unused_')} == (
        {'unused_0'} if connected else set())


@pytest.mark.parametrize('overwrite', [False, True])
def test_protocol_lemma_uses_values_without_ordering_irrelevant_marks(tmp_path, monkeypatch, overwrite):
    from types import SimpleNamespace

    from behave.sim.core.refusal import Refused
    from behave.sim.symbolic.step_template import StepTemplate
    from behave.sim.rtl.source_direct import DirectSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalRelation, SourceIntervalTemplate
    from behave.sim.rtl.task_request import ReadyValidRequestCodec

    from .test_rtl_source_scheduler import _lower

    rtl = HISTORY_SOURCE.replace('assign ir=!rst && (!ov || ordy);', '''
      wire room; assign room=~ov | ordy;
      assign ir=room ? ~rst : 1'b0;''')
    if overwrite:
        rtl = rtl.replace('iv && ir', 'iv')
    machine, _ = _lower(tmp_path, rtl, initialization=True)
    direct = DirectSourceCompiler(machine)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
        direct.plan(roots, all_active=True)

    # Test the safety lemma itself, not whole-catalogue admission. Its source
    # event limitation above must remain explicit even when the lemma succeeds.
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges}, direct=direct).normalized()
    relation = template.relation(machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    contract = _simple_contract(reset_policy='clears')
    source = SimpleNamespace(machine=machine, contract=contract, template=template,
                             relation=relation, initial=initial, queue_bound=6)
    request = SimpleNamespace(codec=ReadyValidRequestCodec(contract, 6, 2))
    original = SourceIntervalRelation.encode_steps

    def no_scheduler_expansion(self, *args, **kwargs):
        assert self.direct is not None or self.source.procedure is not machine.procedure
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SourceIntervalRelation, 'encode_steps', no_scheduler_expansion)
    constructed = {id(process.procedure): 0 for process in _boundary_candidates(machine)}
    initialize = StepTemplate.__init__

    def count_boundary_templates(self, procedure, environment, **kwargs):
        if id(procedure) in constructed:
            constructed[id(procedure)] += 1
        initialize(self, procedure, environment, **kwargs)

    monkeypatch.setattr(StepTemplate, '__init__', count_boundary_templates)
    checks = []

    def solve(label, roots, **options):
        answer = z3_check_finite(roots, 30_000, _prefer_finite=options.get('prefer_finite', False))
        checks.append((label, answer.answer))
        return answer

    proved = prove_protocol_goals(source, request, (GOAL, COUNT_GOAL), solve=solve, check=lambda: None)
    assert (GOAL in proved) is (not overwrite), checks
    if not overwrite:
        assert COUNT_GOAL in proved, checks
    windows = [answer for label, answer in checks
               if label.removesuffix(':data-cut') == 'stability:window']
    assert windows[-1] is (SolverAnswer.SAT if overwrite else SolverAnswer.UNSAT)
    assert constructed and set(constructed.values()) == {1}


@pytest.mark.parametrize('overwrite,unknown', [(False, False), (True, False), (False, True)])
def test_protocol_arithmetic_cut_is_unsat_only(tmp_path, overwrite, unknown):
    rtl = HISTORY_SOURCE.replace('else y<=a;', 'else y<=a*saved;')
    if overwrite:
        rtl = rtl.replace('iv && ir', 'iv')
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    exact = _SourceAnalysis(source, 6)
    labels = []

    def solve(label, roots, **options):
        labels.append(label)
        if unknown:
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='unfinished optional proof')
        return z3_check_finite(roots, 30_000, _prefer_finite=False)

    assert prove_stability(source, exact.request, solve=solve, check=lambda: None) is (
        not overwrite and not unknown)
    assert any(label.endswith(':data-cut') for label in labels)


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('operation', ['add', 'sub', 'neg', 'scale', 'left', 'right', 'signed', 'rotate'])
def test_control_cut_preserves_linear_selection_identity(operation, private_context):
    from behave.sim.rtl.source_stability import _control_proof_roots

    context = z3.Context() if private_context else None
    before, after, data = z3.BitVecs('before after data', 8, ctx=context)
    def select(index):
        return {
            'add': lambda: index + 3,
            'sub': lambda: 3 - index,
            'neg': lambda: -index,
            'scale': lambda: 2 * index,
            'left': lambda: data << index,
            'right': lambda: z3.LShR(data, index),
            'signed': lambda: data >> index,
            'rotate': lambda: z3.RotateLeft(data, index),
        }[operation]()
    roots = (before == after, select(before) != select(after))
    solver = z3.Solver(ctx=context)
    solver.add(*_control_proof_roots(roots, check=lambda: None))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('storage', ['packed', 'array'])
@pytest.mark.parametrize('overwrite', [False, True])
def test_combinational_selection_holds_under_backpressure(tmp_path, storage, overwrite):
    rtl = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output [1:0] y,input ordy);
      reg [1:0] cells[0:3]; reg [1:0] index;
      wire [7:0] packed_cells={cells[3],cells[2],cells[1],cells[0]};
      assign ir=!rst && (!ov || ordy);
      assign y=packed_cells >> (2*index);
      always @(posedge clk) begin
        if(rst) begin
          ov<=0; index<=0;
          for(integer k=0;k<4;k=k+1) cells[k]<=0;
        end else begin
          if(ov && ordy) begin ov<=0; index<=index+1; end
          if(iv && ir) begin
            ov<=1; cells[0]<=a; cells[1]<=a^1;
            cells[2]<=a^2; cells[3]<=a^3;
          end
        end
      end
    endmodule'''
    if storage == 'array':
        rtl = rtl.replace('packed_cells >> (2*index)', 'cells[index]')
    if overwrite:
        rtl = rtl.replace('if(iv && ir)', 'if(iv)')
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    exact = _SourceAnalysis(source, 6)
    def solve(_label, roots, **options):
        return z3_check_finite(roots, 30_000, _prefer_finite=False)
    assert prove_stability(source, exact.request, solve=solve, check=lambda: None) is (not overwrite)


@pytest.fixture(scope='module', params=['safe', 'overwrite', 'bad-reset', 'low-reset'])
def serial_design(tmp_path_factory, request):
    rtl = '''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output reg [1:0] y,input ordy);
      reg working; reg [3:0] lane; reg [1:0] saved;
      assign ir=!rst && !working && (!ov || ordy);
      always @(posedge clk) begin
        if(rst) begin working<=0; ov<=0; lane<=0; saved<=0; y<=0; end
        else begin
          if(ov && ordy) ov<=0;
          if(iv && ir) begin working<=1; lane<=0; saved<=a; end
          else if(working) begin
            y<=saved+lane;
            if(lane==11) begin working<=0; ov<=1; lane<=0; end
            else lane<=lane+1;
          end
        end
      end
    endmodule'''
    kind = request.param
    contract = _simple_contract(reset_policy='clears')
    if kind == 'overwrite':
        rtl = rtl.replace('if(iv && ir)', 'if(iv)')
    elif kind == 'bad-reset':
        rtl = rtl.replace('working<=0; ov<=0;', 'working<=1; ov<=1;')
    elif kind == 'low-reset':
        rtl = rtl.replace('!rst &&', 'rst &&').replace('if(rst)', 'if(!rst)')
        image = contract.as_dict()
        image['clocks'][0].update(reset_active_low=True)
        contract = type(contract).from_dict(image)
    context, *_ = _worker(tmp_path_factory.mktemp('serial-' + kind), rtl, contract, 6)
    return kind, _SourceAnalysis(PreparedSourceExact(context.source_payload(), direct=True), 6)


def test_control_cube_needs_reset_and_induction(serial_design):
    kind, exact = serial_design
    checks = []

    def solve(label, roots, **_):
        result = z3_check_finite(roots, 30_000, _prefer_finite=False)
        checks.append((label.removesuffix(':data-cut'), result.answer))
        return result

    assert prove_stability(exact.source, exact.request, solve=solve, check=lambda: None) is (
        kind in {'safe', 'low-reset'}), checks
    assert checks[0][0] == 'stability:control-establishment'
    assert ('stability:control-establishment', SolverAnswer.UNSAT) in checks or kind == 'bad-reset'
    if kind in {'safe', 'low-reset'}:
        assert checks[-2:] == [('stability:control-preservation', SolverAnswer.UNSAT),
                               ('stability:window', SolverAnswer.UNSAT)]


@pytest.mark.parametrize('unproved', ['reset-prefix', 'control-establishment', 'control-preservation'])
def test_control_refinement_cannot_assume_a_missing_obligation(serial_design, unproved):
    from types import SimpleNamespace
    from behave.sim.rtl.task_request import ReadyValidRequestCodec

    kind, exact = serial_design
    if kind != 'safe':
        return
    request = (SimpleNamespace(codec=ReadyValidRequestCodec(exact.source.contract, 6, 0))
               if unproved == 'reset-prefix' else exact.request)

    def solve(label, roots, **_):
        if label.removesuffix(':data-cut') == 'stability:' + unproved:
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='test unproved control relation')
        return z3_check_finite(roots, 30_000, _prefer_finite=False)

    assert not prove_stability(exact.source, request, solve=solve, check=lambda: None)


@pytest.mark.parametrize('kind,width,idle,work,finish', [
    ('safe', 2, 0, 1, 2), ('safe', 9, 257, 44, 511),
    ('overwrite', 2, 0, 1, 2), ('bad-reset', 9, 257, 44, 511)])
def test_protocol_invariant_relates_valid_to_multibit_fsm(
        tmp_path, monkeypatch, kind, width, idle, work, finish):
    from behave.sim.rtl import source_stability

    abstract = source_stability._control_proof_roots
    abstractions = []
    def counted(roots, **options):
        abstractions.append(len(roots))
        return abstract(roots, **options)
    monkeypatch.setattr(source_stability, '_control_proof_roots', counted)
    rtl = f'''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output reg [1:0] y,input ordy);
      reg [{width-1}:0] phase; reg [1:0] saved;
      assign ir=!rst && phase=={idle} && (!ov || ordy);
      always @(posedge clk) begin
        if(rst) begin phase<={idle}; ov<=0; saved<=0; y<=0; end
        else begin
          if(ov && ordy) ov<=0;
          if(phase=={idle}) begin
            if(iv && ir) begin phase<={work}; saved<=a; end
          end else if(phase=={work}) begin y<=saved; phase<={finish}; end
          else if(phase=={finish}) begin phase<={idle}; ov<=1; end
        end
      end
    endmodule'''
    if kind == 'overwrite':
        rtl = rtl.replace('if(iv && ir)', 'if(iv)')
    elif kind == 'bad-reset':
        rtl = rtl.replace(f'if(rst) begin phase<={idle}; ov<=0;',
                          f'if(rst) begin phase<={work}; ov<=1;')
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    exact = _SourceAnalysis(PreparedSourceExact(context.source_payload(), direct=True), 6)
    checks = []

    def solve(label, roots, **_):
        result = z3_check_finite(roots, 30_000, _prefer_finite=False)
        checks.append((label.removesuffix(':data-cut'), result.answer))
        return result

    assert prove_stability(exact.source, exact.request, solve=solve, check=lambda: None) is (
        kind == 'safe'), checks
    assert checks[0][0] == 'stability:control-establishment'
    assert len(abstractions) == 1 + sum(label == 'stability:window' for label, _ in checks)
    if kind == 'safe':
        assert checks[-2:] == [('stability:control-preservation', SolverAnswer.UNSAT),
                              ('stability:window', SolverAnswer.UNSAT)]


@pytest.mark.parametrize('width', [16, 128])
@pytest.mark.parametrize('overwrite', [False, True])
def test_control_induction_precedes_payload_and_uses_its_own_cone(tmp_path, monkeypatch, width, overwrite):
    from types import SimpleNamespace
    from behave.sim.rtl.source_relation import SourceIntervalRelation, SourceIntervalTemplate
    from behave.sim.rtl.task_request import ReadyValidRequestCodec
    from .test_rtl_source_scheduler import _lower

    rtl = f'''module M(input clk,rst,iv,input [1:0] a,output ir,
      output reg ov,output reg [1:0] y,input ordy);
      reg working; reg [{width - 1}:0] payload;
      assign ir=!rst && !working && (!ov || ordy);
      always @(posedge clk) begin
        if(rst) begin working<=0; ov<=0; payload<=0; y<=0; end
        else begin
          if(ov && ordy) ov<=0;
          if(iv && ir) begin working<=1; payload<=a; end
          else if(working) begin
            y<=payload[1:0];
            payload<=(payload ^ (payload >> 3))+a;
            working<=0; ov<=1;
          end
        end
      end
    endmodule'''
    if overwrite:
        rtl = rtl.replace('if(iv && ir)', 'if(iv)')
    machine, _ = _lower(tmp_path, rtl, initialization=True)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=DirectSourceCompiler(machine)).normalized()
    relation = template.relation(machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    contract = _simple_contract(reset_policy='clears')
    source = SimpleNamespace(machine=machine, contract=contract, template=template,
                             relation=relation, initial=initial, queue_bound=6)
    request = SimpleNamespace(codec=ReadyValidRequestCodec(contract, 6, 2))
    fields, events = source.machine.program.fields, []
    original = SourceIntervalRelation.encode_phases

    def encode(self, state, phases, **options):
        retained = {fields[name].name for name in state if name in fields}
        events.append(('construct', options['tag'], retained))
        return original(self, state, phases, **options)

    def solve(label, roots, **_):
        events.append(('solve', label, None))
        return z3_check_finite(roots, 30_000)

    monkeypatch.setattr(SourceIntervalRelation, 'encode_phases', encode)
    assert prove_stability(source, request, solve=solve, check=lambda: None) is (not overwrite)
    control = [names for event, label, names in events
               if event == 'construct' and label == 'protocol_control']
    assert control and all('payload' not in names and 'working' in names for names in control)
    payload = next(i for i, (_, label, _) in enumerate(events) if label == 'protocol_stability')
    assert any(label.startswith('stability:control-preservation')
               for _, label, _ in events[:payload])
    assert all(label != 'stability:window' for _, label, _ in events[:payload])


def test_boundary_candidates_close_transitively_over_internal_wires(tmp_path):
    from .test_rtl_source_scheduler import _lower

    machine, _ = _lower(tmp_path, '''module M(input a,b,output y);
      wire x,z; assign y=x; assign x=z^1; assign z=a^b;
    endmodule''')
    assert len(_boundary_candidates(machine)) == 3


@pytest.mark.parametrize('overwrite', [False, True])
def test_protocol_property_precedes_unneeded_datapath_fixed_point(tmp_path, overwrite):
    rtl = HISTORY_SOURCE.replace('reg [1:0] saved;', '''reg [1:0] saved;
      reg [7:0] auxiliary;
      always_comb begin auxiliary=0;
        for(integer i=0;i<3;i++) auxiliary=auxiliary*3+a;
      end''').replace('iv && ir', 'iv && ir && auxiliary != 0')
    if overwrite:
        rtl = rtl.replace('iv && ir && auxiliary != 0', 'iv && auxiliary != 0')
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    exact = _SourceAnalysis(source, 6)
    labels = []

    def solve(label, roots, **options):
        labels.append(label)
        return z3_check_finite(roots, 30_000, _prefer_finite=False)

    assert prove_stability(source, exact.request, solve=solve, check=lambda: None) is (not overwrite)
    assert any(label.removesuffix(':data-cut') == 'stability:window' for label in labels)
    if not overwrite:
        assert not any(label.startswith('boundary:') for label in labels)


@pytest.mark.parametrize('overwrite', [False, True])
@pytest.mark.parametrize('dependent', [False, True])
def test_local_lemma_keeps_fallible_datapath_without_full_scheduler(
        tmp_path, monkeypatch, overwrite, dependent):
    # Independent checks and closed fallible dataflow both retain successful
    # boundaries. Neither can suppress a real output overwrite violation.
    # The ordinary native/event view must still keep failure-prefix ordering.
    rtl = HISTORY_SOURCE.replace('reg [1:0] saved;', '''reg [1:0] saved;
      reg [1:0] auxiliary;
      always_comb begin auxiliary=a+1; if(a==3) auxiliary='x; end''')
    if dependent:
        rtl = rtl.replace('reg [1:0] auxiliary;', '''reg [1:0] auxiliary;
          wire [1:0] product; assign product=auxiliary*a;''')
        rtl = rtl.replace('else y<=a;', 'else y<=product;')
    if overwrite:
        rtl = rtl.replace('iv && ir', 'iv')
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    exact = _SourceAnalysis(source, 6)
    original = type(source.relation).encode_steps

    def no_scheduler(self, *args, **kwargs):
        assert self.direct is not None or self.source.procedure is not source.machine.procedure
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(source.relation), 'encode_steps', no_scheduler)
    labels = []
    def solve(label, roots, **options):
        labels.append(label)
        return z3_check_finite(roots, 30_000, _prefer_finite=options.get('prefer_finite', False))
    assert prove_stability(source, exact.request, solve=solve, check=lambda: None) is (not overwrite)
    assert not any(label.startswith('boundary:') for label in labels)


@pytest.mark.parametrize('reconvergent', [False, True])
@pytest.mark.parametrize('overwrite', [False, True])
@pytest.mark.parametrize('named_accept', [False, True])
def test_internal_memory_remains_arbitrary_in_local_protocol_proof(
        tmp_path, reconvergent, overwrite, named_accept):
    from behave.sim import api
    from behave.sim.rtl.source_relation import SourceObservation
    # Both the old word and a dynamic store matter. During backpressure a
    # register may retain either arbitrary memory data or any previous output;
    # neither reset-to-zero memory nor a baseline value is a proof premise.
    rtl = HISTORY_SOURCE.replace('reg [1:0] saved;', 'reg [1:0] saved[0:3];')
    rtl = rtl.replace('saved<=0;', 'for(integer i=0;i<4;i++) saved[i]<=0;')
    rtl = rtl.replace('if(saved==1 && a==2) y<=3; else y<=a;', 'y<=saved[a];')
    rtl = rtl.replace('saved<=a;', 'saved[a]<=a;')
    if overwrite:
        rtl = rtl.replace('iv && ir', 'iv')
    if reconvergent:
        rtl = rtl.replace('assign ir=!rst && (!ov || ordy);',
            'wire consumed; assign consumed=ov & ordy; assign ir=~ov | consumed;')
    if named_accept:
        # Closed wire DAGs are the admitted prefix constructor here. Keep
        # short-circuit occurrence ordering out of this protocol-proof test.
        rtl = rtl.replace('assign ir=!rst && (!ov || ordy);',
                          'assign ir=~rst & (~ov | ordy);')
        enable = 'iv' if overwrite else 'iv && ir'
        expression = 'iv' if overwrite else 'iv & ir'
        rtl = rtl.replace('reg [1:0] saved[0:3];',
            f'reg [1:0] saved[0:3]; wire accepted; assign accepted={expression};')
        rtl = rtl.replace(f'else if({enable})', 'else if(accepted)')
    image = _simple_contract(reset_policy='clears').as_dict()
    # Reset-restoration ownership is a separate frontend capability. This
    # fixture asks for protocol safety, with the same real reset transitions.
    image['clocks'][0]['reset_restores'] = False
    context, *_ = _worker(tmp_path, rtl, api.TaskContract.from_dict(image), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True,
                                 observation_scope=SourceObservation.GOAL_PREFIX)
    request = source.request_coordinates(6)
    calls = []

    def solve(label, roots, **options):
        answer = z3_check_finite(roots, 30_000, _prefer_finite=options.get('prefer_finite', False))
        calls.append((label, answer.answer))
        return answer

    proved = prove_protocol_goals(source, request, (GOAL, COUNT_GOAL), solve=solve, check=lambda: None)
    assert (GOAL in proved) is (not overwrite), calls
    if not overwrite:
        assert COUNT_GOAL in proved, calls
    windows = [answer for label, answer in calls
               if label.removesuffix(':data-cut') == 'stability:window']
    assert windows[-1] is (SolverAnswer.SAT if overwrite else SolverAnswer.UNSAT)
    assert source.relation.direct is source.template.direct
    assert not source.template.direct._prefix_scope


def test_dispatcher_preserves_full_query_and_counts_proofs(design):
    kind, exact = design
    value = exact.call('query', {'goals': [GOAL], 'timeout_ms': 30_000}, started_ns=time.monotonic_ns())
    if kind in {'registered', 'combinational'}:
        assert value['unreachable'] == [GOAL]
        assert value['queries'][0]['solver_calls'] >= 1
    elif kind != 'late-error':
        assert GOAL in value['reachable'], value
        assert GOAL not in value['unreachable']
    else:
        assert value['unreachable'] == [GOAL]
    # At horizon six the late error may still be unreachable from the reset.
    # In either case the local SAT must not itself become a query witness.
    assert value['queries'][0]['solver_calls'] >= 1


def test_unknown_or_invalid_invariant_cannot_supply_a_proof(design, monkeypatch):
    monkeypatch.setattr(DirectSourceCompiler, 'certifies_fixed_points', lambda self: False)
    _, exact = design
    for answer in (SolverAnswer.UNKNOWN, SolverAnswer.SAT):
        calls = []
        solver = z3.Solver()
        assert solver.check() == z3.sat

        def solve(label, roots, **options):
            calls.append(label)
            return ExactCheckResult(answer,
                                    reason='injected unproved candidate' if answer is SolverAnswer.UNKNOWN else '',
                                    model=solver.model() if answer is SolverAnswer.SAT else None)

        assert not prove_stability(exact.source, exact.request, solve=solve, check=lambda: None)
        assert calls == ['boundary:establishment']


@pytest.mark.parametrize('phase', ['control', 'active', 'return'])
def test_every_preservation_obligation_is_required(design, phase, monkeypatch):
    monkeypatch.setattr(DirectSourceCompiler, 'certifies_fixed_points', lambda self: False)
    _, exact = design
    labels = []

    def solve(label, roots, **options):
        labels.append(label)
        if label == 'boundary:preservation:' + phase:
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='unproved preservation')
        return z3_check_finite(roots, 30_000, _prefer_finite=False)

    assert not prove_stability(exact.source, exact.request, solve=solve, check=lambda: None)
    assert labels[-1] == 'boundary:preservation:' + phase
    assert all(label.removesuffix(':data-cut') != 'stability:window' for label in labels)


def test_proven_stability_does_not_hide_an_unencoded_exit(design, monkeypatch):
    kind, exact = design
    if kind not in {'registered', 'combinational'}:
        return
    original = exact.source.bind_request

    def with_escape(horizon, *, goals=None):
        relation, request, encoded = original(horizon, goals=goals)
        return relation, request, replace(encoded, monitor_faults=(
            {'test-domain-fault': z3.BoolVal(True)},))

    monkeypatch.setattr(exact.source, 'bind_request', with_escape)
    from behave.sim.core.refusal import Refused

    with pytest.raises(Refused) as caught:
        exact.call('query', {'goals': [GOAL], 'timeout_ms': 30_000}, started_ns=time.monotonic_ns())
    assert caught.value.refusal.kind == 'rtl-source-operation-domain-unencoded'
    assert caught.value.refusal.as_dict()['detail']['faults'] == ['test-domain-fault']


def test_other_schedules_are_left_to_the_original_query(design):
    from types import SimpleNamespace

    _, exact = design

    def forbidden(*args, **kwargs):
        pytest.fail('another schedule has no local stability proof')

    assert not prove_stability(exact.source, SimpleNamespace(codec=object()),
                               solve=forbidden, check=forbidden)


@pytest.fixture(scope='module', params=[
    'registered', 'low-reset', 'keeps', 'duplicate', 'spontaneous', 'bad-reset', 'late-output'])
def counting_design(tmp_path_factory, request):
    kind, rtl = request.param, HISTORY_SOURCE
    contract = _simple_contract(reset_policy='keeps' if kind == 'keeps' else 'clears')
    if kind == 'duplicate':
        rtl = rtl.replace('else if(ordy) ov<=0;', 'else if(ordy) ov<=ov;')
    elif kind == 'spontaneous':
        rtl = rtl.replace('else if(ordy) ov<=0;', 'else if(ordy) ov<=1;')
    elif kind == 'bad-reset':
        rtl = rtl.replace('ov<=0;', 'ov<=1;', 1)
    elif kind == 'late-output':
        rtl = rtl.replace('reg [1:0] saved;', 'reg [1:0] saved; reg [3:0] ticks;')
        rtl = rtl.replace('if(rst) begin', 'if(rst) begin ticks<=0;')
        rtl = rtl.replace('else if(iv && ir)', 'else begin ticks<=ticks+1; if(ticks==9) ov<=1;\n'
                          '    else if(iv && ir)').replace('else if(ordy) ov<=0;', 'else if(ordy) ov<=0; end')
    elif kind == 'low-reset':
        from behave.sim import api
        rtl = rtl.replace('!rst &&', 'rst &&').replace('if(rst)', 'if(!rst)')
        image = contract.as_dict()
        image['clocks'][0].update(reset_active_low=True)
        contract = api.TaskContract.from_dict(image)
    context, *_ = _worker(tmp_path_factory.mktemp('count-' + kind), rtl, contract, 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    return kind, context, _SourceAnalysis(source, 6)


@pytest.mark.parametrize('connected', [False, True])
@pytest.mark.parametrize('both', [False, True])
def test_count_window_omits_payload_but_keeps_control_dependencies(tmp_path, monkeypatch, connected, both):
    from behave.sim.rtl.source_relation import SourceIntervalRelation

    rtl = HISTORY_SOURCE.replace('reg [1:0] saved;', '''reg [1:0] saved;
      reg [15:0] datapath;
      always @(posedge clk) if(rst) datapath<=0;
        else datapath <= (datapath ^ {14'b0,a}) + 16'h1234;''')
    rtl = rtl.replace('y<=a;', 'y<=datapath[1:0];')
    if connected:
        rtl = rtl.replace('else if(ordy) ov<=0;', 'else if(datapath==16\'h1234) ov<=1; else if(ordy) ov<=0;')
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    windows = []
    original = SourceIntervalRelation.encode_phases

    def capture(self, state, phases, **options):
        windows.append((options['tag'], frozenset(state)))
        return original(self, state, phases, **options)

    monkeypatch.setattr(SourceIntervalRelation, 'encode_phases', capture)
    goals = (COUNT_GOAL, GOAL) if both else (COUNT_GOAL,)
    proved = prove_protocol_goals(source, source.request_coordinates(6), goals,
        solve=lambda _label, roots, **options: z3_check_finite(roots, 30_000), check=lambda: None)
    assert (COUNT_GOAL in proved) is (not connected)
    fields = source.machine.program.fields
    count = [names for tag, names in windows if tag == 'protocol_count']
    assert count
    for names in count:
        retained = {fields[name].name for name in names if name in fields}
        assert ('datapath' in retained) is connected
        assert 'y' not in retained
    if both:
        assert any('y' in {fields[name].name for name in names if name in fields}
                   for tag, names in windows if tag == 'protocol_stability')


def test_count_conservation_is_proved_not_assumed(counting_design):
    kind, _, exact = counting_design
    calls = []

    def solve(label, roots, **options):
        answer = z3_check_finite(roots, 30_000, _prefer_finite=options.get('prefer_finite', False))
        calls.append((label, answer.answer))
        return answer

    proved = prove_protocol_goals(exact.source, exact.request, (COUNT_GOAL,), solve=solve, check=lambda: None)
    assert (COUNT_GOAL in proved) is (kind in {'registered', 'keeps', 'low-reset'}), calls
    assert calls[-1][0].startswith('count:')
    assert calls[-1][1] is (SolverAnswer.UNSAT if proved else SolverAnswer.SAT)


def test_count_query_retains_actual_bounded_answer_and_native_witness(counting_design):
    from behave.sim.core.audit import AuditedWitness

    kind, context, exact = counting_design
    answer = exact.call('query', {'goals': [COUNT_GOAL], 'timeout_ms': 30_000}, started_ns=time.monotonic_ns())
    if kind in {'registered', 'keeps', 'low-reset', 'late-output'}:
        assert answer['unreachable'] == [COUNT_GOAL], answer
    else:
        rows = answer['reachable'][COUNT_GOAL]
        request = exact.request
        trace = context.source_trace(request.codec.decode(rows),
            startup_state=exact.source.initial_layout.decode(rows))
        audited = exact.call('audit-request', {'rows': rows, 'trace': trace.as_dict(),
            'domain': exact.domain.digest()}, started_ns=time.monotonic_ns())
        assert COUNT_GOAL in AuditedWitness.from_record(audited).hits


@pytest.mark.parametrize('obligation', ['count:establishment', 'count:preservation'])
def test_count_unknown_never_removes_the_goal(counting_design, obligation):
    kind, _, exact = counting_design
    if kind != 'registered':
        return
    labels = []

    def solve(label, roots, **options):
        labels.append(label)
        if label == obligation:
            return ExactCheckResult(SolverAnswer.UNKNOWN, reason='unproved conservation')
        return z3_check_finite(roots, 30_000, _prefer_finite=False)

    assert not prove_protocol_goals(exact.source, exact.request, (COUNT_GOAL,), solve=solve, check=lambda: None)
    assert labels[-1] == obligation


def test_count_proof_requires_real_reset_prefix_and_finite_driver(counting_design, monkeypatch):
    from types import SimpleNamespace
    from behave.sim.rtl import source_stability

    kind, _, exact = counting_design
    if kind != 'registered':
        return
    def solve(_label, roots, **options):
        return z3_check_finite(roots, 30_000, _prefer_finite=False)

    codec = type(exact.request.codec)(exact.source.contract, exact.source.queue_bound, 0)
    assert not prove_protocol_goals(exact.source, SimpleNamespace(codec=codec),
        (COUNT_GOAL,), solve=solve, check=lambda: None)
    # Removing the shared finite-queue guard must destroy the lemma, not let
    # the monitor silently discard an overflow acceptance and certify safety.
    monkeypatch.setattr(source_stability, 'input_is_present',
                        lambda pointer, released, active, algebra: active)
    assert not prove_protocol_goals(exact.source, exact.request, (COUNT_GOAL,), solve=solve, check=lambda: None)


def test_count_local_proof_still_queries_full_history_escapes(counting_design, monkeypatch):
    from behave.sim.core.refusal import Refused

    kind, _, exact = counting_design
    if kind != 'registered':
        return
    original = exact.source.bind_request
    def with_escape(horizon, *, goals=None):
        relation, request, encoded = original(horizon, goals=goals)
        return relation, request, replace(encoded, monitor_faults=(
            {'test-domain-fault': z3.BoolVal(True)},))
    monkeypatch.setattr(exact.source, 'bind_request', with_escape)
    with pytest.raises(Refused) as caught:
        exact.call('query', {'goals': [COUNT_GOAL], 'timeout_ms': 30_000}, started_ns=time.monotonic_ns())
    assert caught.value.refusal.kind == 'rtl-source-operation-domain-unencoded'
    assert caught.value.refusal.as_dict()['detail']['faults'] == ['test-domain-fault']


def test_protocol_goals_share_one_boundary_proof(counting_design, monkeypatch):
    monkeypatch.setattr(DirectSourceCompiler, 'certifies_fixed_points', lambda self: False)
    kind, _, exact = counting_design
    if kind != 'registered':
        return
    labels = []
    def solve(label, roots, **options):
        labels.append(label)
        return z3_check_finite(roots, 30_000, _prefer_finite=False)
    assert prove_protocol_goals(exact.source, exact.request, (COUNT_GOAL, GOAL),
        solve=solve, check=lambda: None) == {COUNT_GOAL, GOAL}
    assert [label for label in labels if label.startswith('boundary:')] == [
        'boundary:establishment', 'boundary:preservation:control',
        'boundary:preservation:active', 'boundary:preservation:return']


def test_compiled_fixed_points_match_all_original_smt_obligations(design, monkeypatch):
    _, exact = design
    ordinary = DirectSourceCompiler.certifies_fixed_points
    encode = DirectSourceCompiler._encode_plan_steps
    constructed = []
    def track(self, *args, **kwargs):
        constructed.append(kwargs.get('tag', ''))
        return encode(self, *args, **kwargs)
    monkeypatch.setattr(DirectSourceCompiler, '_encode_plan_steps', track)
    traces = []
    answers = []
    for certificate in (False, True):
        labels = []
        constructed.clear()
        monkeypatch.setattr(DirectSourceCompiler, 'certifies_fixed_points',
                            ordinary if certificate else lambda self: False)
        def solve(label, roots, **options):
            answer = z3_check_finite(roots, 30_000, _prefer_finite=options.get('prefer_finite', False))
            labels.append(label)
            # Independently check every obligation replaced by the structural
            # certificate, including on the deliberately unsafe artifacts.
            if label.startswith('boundary:'):
                assert answer.answer is SolverAnswer.UNSAT
            return answer
        answers.append(prove_stability(exact.source, exact.request,
                                       solve=solve, check=lambda: None))
        if certificate:
            assert not any(tag.startswith('stability_') for tag in constructed)
        else:
            assert 'stability_startup:0' in constructed or 'stability_startup' in constructed
        traces.append(labels)
    assert answers[0] == answers[1]
    assert [s for s in traces[0] if s.startswith('boundary:')] == [
        'boundary:establishment', 'boundary:preservation:control',
        'boundary:preservation:active', 'boundary:preservation:return']
    assert not any(s.startswith('boundary:') for s in traces[1])


def test_count_bad_sample_is_not_hidden_by_an_incomplete_clock_phase(tmp_path, monkeypatch):
    # This candidate offers an output but never accepts the matching input.
    rtl = COMBINATIONAL.replace('assign ir=!rst && ordy;', "assign ir=1'b0;")
    context, *_ = _worker(tmp_path, rtl, _simple_contract(reset_policy='clears'), 6)
    source = PreparedSourceExact(context.source_payload(), direct=True)
    exact = _SourceAnalysis(source, 6)
    ordinary = type(source.relation).encode_phases
    def incomplete(self, *args, **kwargs):
        encoded = ordinary(self, *args, **kwargs)
        last = replace(encoded.phases[-1], alive=z3.BoolVal(False))
        return replace(encoded, phases=(*encoded.phases[:-1], last))
    monkeypatch.setattr(type(source.relation), 'encode_phases', incomplete)
    def solve(_label, roots, **options):
        return z3_check_finite(roots, 30_000, _prefer_finite=False)
    assert not prove_protocol_goals(source, exact.request, (COUNT_GOAL,), solve=solve, check=lambda: None)
