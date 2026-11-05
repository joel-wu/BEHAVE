"""A local query binds its exact cone, never guesses unobserved RTL history."""

import pytest

from behave.sim import api
from behave.sim.symbolic import ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.generation import StimulusField
from behave.sim.rtl.source_relation import SourceIntervalTemplate

from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _state
from .test_rtl_source_trace import _contract

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


def _prepared(machine, goals):
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    return env, SourceIntervalTemplate(machine.procedure, env).relation(machine, goals)


@pytest.mark.parametrize('unreadable', [False, True])
def test_monitor_prefix_is_concrete_resumable_and_never_publishes_partial_state(unreadable):
    from .test_rtl_source_history import _simple_contract

    binding = object.__new__(SourceTraceBindings)
    binding.contract = _simple_contract(reset_policy='keeps')
    binding.seed = api.Stimulus({'in': [(1,)]}, [
        api.EventSlot(offers=('in',)), api.EventSlot()])
    binding.trace = api.AuditedTrace(binding.seed, records=[api.SlotRecord(
        outputs={'ov': 1, 'y': None if unreadable else 1}, accepted={'in': (1,) if i == 0 else None})
        for i in range(2)])
    binding.atomic = False
    binding.ports, binding.pins = {'ordy': 'ready'}, ({'ready': 0},) * 2
    binding._monitors = binding._monitor_prefix = None
    if unreadable:
        with pytest.raises(Refused, match='prefix-unreadable'):
            binding.monitor_boundary(1, check=lambda: None)
        assert binding._monitor_prefix is None
        return
    first = binding.monitor_boundary(1, check=lambda: None)
    assert first.protocol['c', 'out'].stalled
    assert first.protocol['c', 'out'].held == (1,)
    calls = []
    def cancelled():
        calls.append(True)
        if len(calls) == 2:
            raise TimeoutError('cancel after stepping, before publishing')
    with pytest.raises(TimeoutError):
        binding.monitor_boundary(2, check=cancelled)
    assert binding._monitor_prefix == (1, first)
    resumed = binding.monitor_boundary(2, check=lambda: None)
    binding._monitor_prefix = None
    assert resumed == binding.monitor_boundary(2, check=lambda: None)
    assert binding.monitor_boundary(1, check=lambda: None) == first


@pytest.mark.parametrize('terminal', [False, True])
def test_unobserved_history_remains_required_even_when_only_failure_reads_it(tmp_path, terminal):
    use = 'if(x==9) $fatal(1,"history"); if(a==9) y=a;' if terminal else 'if(x==9) y=a;'
    source = f'''module M(input clk,rst,input [3:0] a,output reg [3:0] y);
      always @(posedge clk) begin reg [3:0] x; {use} x=a; end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    goal = _goal(source, plan, 'a==9' if terminal else 'x==9')
    env, relation = _prepared(machine, (goal,))
    x = _names(machine)['x']
    assert x in relation.source.state_names
    seed = api.Stimulus({'input': [(0,), (1,)]}, [
        api.EventSlot(offers=('input',), controls={'rst': 0}) for _ in range(2)])
    trace = api.AuditedTrace(seed, records=[api.SlotRecord(
        accepted={'input': (a,)}, outputs={'a': a, 'y': 0, 'x': 9}, state={'y': 0}) for a in (0, 1)])
    with pytest.raises(Refused, match='rtl-source-trace-boundary-unavailable'):
        SourceTraceBindings(machine, _contract('rising'), seed, trace).at(
            (StimulusField('input', 1, 0, 1),), state_names=relation.source.state_names)
    # A missing retained state is not turned into a solver-controlled input.
    state = _state(machine, {'clk': 0, 'rst': 0, 'a': 0, 'y': 0, 'x': 0})
    state.pop(x)
    names = _names(machine)
    answer = query_source_interval(machine, env, state,
        {names['clk']: 1, names['rst']: 0, names['a']: 0}, (goal,),
        mutable_inputs=(names['a'],), event_bound=machine.event_bound, _relation=relation,
        timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.UNSUPPORTED


def test_projected_nba_boundary_requires_its_actual_target_payload(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) q<=a;
    endmodule''')
    write, _, _ = machine.queue_edges[0]
    assert machine.boundary_fields((write.first,)) == {write.target}
    with pytest.raises(ValueError, match='every retained'):
        machine.boundary({}, state_names=(write.first,))
    state = machine.boundary({write.target: 7}, state_names=(write.first,))
    assert state[write.first] == 7 and write.data not in state
    assert machine.boundary_is_quiet(state)


@pytest.mark.parametrize('calculation, retained', [
    ('x=a;', False),
    ('if(a[0]) x=a; else x=a+1;', False),
    ('if(!a[0]) x=a+1; else x=a;', False),
    ('if(a[0]) begin if(a[1]) x=a; else x=a+2; end else x=a+1;', False),
    ('if(a[0]) x=a; else if(a[1]) x=a+2; else x=a+1;', False),
    ('if(a[0]) x=a;', True),
    ('if(a[0]) begin if(a[1]) x=a; end else x=a+1;', True),
])
def test_complete_branch_overwrite_does_not_read_unobserved_history(tmp_path, calculation, retained):
    source = f'''module M(input clk,input [3:0] a,output reg [3:0] q);
      always @(posedge clk) begin reg [3:0] x;
        if(a!=0) begin {calculation} if(x==9) q=x; else q=0; end
      end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    _env, relation = _prepared(machine, (_goal(source, plan, 'x==9'),))
    x = _names(machine)['x']
    assert machine.program.fields[x].observation[-1] == 'x'
    assert (x in relation.source.state_names) is retained


@pytest.mark.cosim
@pytest.mark.parametrize('calculation, expected', [
    ('x=a;', 'a'),
    ('x=a; if(x>12) x=12;', 'min(a,12)'),
    ('if(a[0]) x=a; else x=a+1;', 'a if a&1 else a+1'),
    ('if(a[0]) begin if(a[1]) x=a; else x=a+2; end else x=a+1;',
     '(a if a&2 else a+2) if a&1 else a+1'),
    ('if(a[0]) x=a; else if(a[1]) x=a+2; else x=a+1;',
     'a if a&1 else (a+2 if a&2 else a+1)'),
    ('for(int i=0;i<2;i++) begin reg[3:0] inner; inner=a+i; x=inner; end',
     'a+1'),
])
def test_anonymous_temporary_baseline_to_query_to_native_hit(tmp_path, calculation, expected):
    source = f'''module M(input [3:0] a,output reg [3:0] y);
      always_comb begin reg [3:0] x; {calculation} if(x==9) y=x; else y=x; end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    goal = _goal(source, plan, 'x==9')
    env, relation = _prepared(machine, (goal,))
    x = _names(machine)['x']
    assert machine.program.fields[x].observation[-1] == 'x'
    assert x not in relation.source.state_names
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': ('class M:\n def reset(self): pass\n'
         ' def process(self,a): return {"y":(' + expected + ')&15}\n').encode()},
        config={'class': 'M'})
    contract = _contract()
    task = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(tmp_path/'design.sv', 'M', tmp_path/'build'))
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=1))
    checked = handle.replay(baseline.stimuli, reference=reference).evaluation
    assert checked.complete and checked.passed, checked.as_dict()
    trace = checked.results[0].trace
    before = trace.as_dict()
    assert not any(hit for row in trace.records for name, hit in row.hits if name == goal)
    binding = SourceTraceBindings(machine, contract, trace.stimulus, trace)
    fields = (StimulusField('input', 1, 0, binding.releases['input', 1]),)
    assert x in binding.at(fields).state  # Native compiler path, not an invented value.
    query = binding.at(fields, state_names=relation.source.state_names)
    assert x not in query.state
    answer = query_source_interval(machine, env, query.state, query.phases[0], (goal,),
        mutable_inputs=query.mutable_inputs, event_bound=machine.event_bound, _relation=relation,
        timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    proposal = query.proposal(answer.candidate)
    replayed = handle.replay((proposal,), reference=reference).evaluation
    assert replayed.complete and replayed.passed, replayed.as_dict()
    assert any(hit for row in replayed.results[0].trace.records for name, hit in row.hits if name == goal)
    assert trace.as_dict() == before


@pytest.mark.cosim
def test_retained_static_local_baseline_to_query_to_native_hit(tmp_path):
    source = '''module M(input clk,rst,input[3:0] a,output wire[3:0] y);
      assign y=a;
      always @(posedge clk) begin reg[3:0] x=3;
        if(rst) begin x=3; end
        else begin
          if(x==3 && a==9) x=a; else x=a;
        end
      end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    goal = _goal(source, plan, 'x==3 && a==9')
    env, relation = _prepared(machine, (goal,))
    x = _names(machine)['x']
    assert x in relation.source.state_names
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': b'class M:\n def reset(self): pass\n'
         b' def process(self,a): return {"y":a}\n'},
        config={'class': 'M'})
    contract = _contract('rising')
    task = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(tmp_path/'design.sv', 'M', tmp_path/'build'))
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=1))
    checked = handle.replay(baseline.stimuli, reference=reference).evaluation
    assert checked.complete and checked.passed, checked.as_dict()
    trace = checked.results[0].trace
    assert not any(hit for row in trace.records for name, hit in row.hits if name == goal)
    binding = SourceTraceBindings(machine, contract, trace.stimulus, trace)
    fields = (StimulusField('input', 0, 0, binding.releases['input', 0]),)
    query = binding.at(fields, state_names=relation.source.state_names)
    assert query.state[x] == 3  # Actual reset-retained storage, not a guessed zero.
    answer = query_source_interval(machine, env, query.state, query.phases[0], (goal,),
        mutable_inputs=query.mutable_inputs, following_inputs=query.phases[1:],
        event_bound=machine.event_bound, _relation=relation, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    replayed = handle.replay((query.proposal(answer.candidate),), reference=reference).evaluation
    assert replayed.complete and replayed.passed, replayed.as_dict()
    assert any(hit for row in replayed.results[0].trace.records for name, hit in row.hits if name == goal)
