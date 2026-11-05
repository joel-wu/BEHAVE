"""Whole physical intervals preserve source occurrences, samples and history."""

from itertools import product

import pytest
import z3

from behave.sim import api
from behave.sim.symbolic import ranges
from behave.sim.symbolic.encoder import encode_concrete_value
from behave.sim.symbolic.step_template import _leaves
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_relation import SourceIntervalTemplate

from .test_rtl_source_event_bounds import _histories
from .test_rtl_source_query import _goal
from .test_rtl_source_relation import _pins
from .test_rtl_source_scheduler import _lower, _names, _state

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


def _prepare(machine, values, goals, *, startup=False):
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    relation = SourceIntervalTemplate(proc, env).relation(
        machine, goals, complete_state=True, occurrences=True)
    kinds = dict(proc.semantics.state_types)
    pre = {name: encode_concrete_value(value, env[name], name, kind=kinds[name])
           for name, value in _state(machine, values, startup=startup).items()}
    return relation, pre, env


def _true(model, terms):
    return frozenset(name for name, term in terms.items() if z3.is_true(model.eval(term)))


@pytest.mark.parametrize('active_high', [False, True])
@pytest.mark.parametrize('clock_edge', [False, True])
@pytest.mark.parametrize('private', [False, True])
def test_off_edge_reset_specializes_only_the_executing_body(tmp_path, active_high, clock_edge, private):
    from dataclasses import replace
    from behave.sim.core.construction_checkpoint import finish_construction
    from behave.sim.rtl.source_direct import DirectSourceCompiler
    from behave.sim.rtl.source_relation import _HistoryControlFacts
    from behave.sim.symbolic.term_bounds import _SyntacticClosureProof

    test, edge = ('rst', 'posedge') if active_high else ('!rst', 'negedge')
    source = f'''module M(input clk,rst,input [7:0] a,output reg [7:0] q);
      always @(posedge clk or {edge} rst) begin
        if({test}) q<=0;
        else begin if(q>=8) q<=a*a; else q<=q+1; if(a==3) $fatal(1); end
      end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    goals = (_goal(source, plan, 'q>=8'),
             _goal(source, plan, '$fatal', 'failure', 'artifact-failure'))
    ordinary, initial, env = _prepare(machine,
        {'clk': 0, 'rst': int(not active_high), 'a': 0, 'q': 7}, goals)
    if private:
        context = z3.Context()
        ordinary = ordinary.translate(context)
        initial = {name: value.translate(context) for name, value in initial.items()}
    context = ordinary.source.context
    optimized = replace(ordinary, direct=DirectSourceCompiler(machine))
    pins = _pins(machine, env, {names['clk']: int(clock_edge), names['rst']: 0, names['a']: 0})
    pins = {name: value if value.ctx == context else value.translate(context) for name, value in pins.items()}
    for name, width in (('rst', 1), ('a', 8)):
        value = z3.BitVec(f'reset_drive_{name}', width, ctx=context)
        pins[names[name]] = z3.ZeroExt(pins[names[name]].size() - width, value)
    # The same process can run from reset alone or from either trigger. A
    # cached template for one activation must not supply the other's premise.
    opposite = {**pins, names['clk']: z3.BitVecVal(
        int(not clock_edge), pins[names['clk']].size(), ctx=context)}
    optimized.encode_phases(initial, (opposite,), event_bounds=(machine.event_bound,))
    facts = _HistoryControlFacts()
    before = ordinary.encode_phases(initial, (pins,), event_bounds=(machine.event_bound,))
    after = finish_construction(optimized.encode_phases_steps(
        initial, (pins,), event_bounds=(machine.event_bound,), _facts=facts))
    solver = z3.Solver(ctx=context)
    solver.add(*before.constraints, *after.constraints)
    assert solver.check() == z3.sat
    solver.push()
    solver.add(z3.Or(before.alive != after.alive, before.error != after.error,
                     *(before.hits[key] != after.hits[key] for key in goals),
                     z3.And(before.alive, z3.Or(
                         *(before.state[names[name]] != after.state[names[name]]
                           for name in ('q', 'rst'))))))
    assert solver.check() == z3.unsat
    solver.pop()
    if clock_edge:
        solver.add(after.hits[goals[1]])
        assert solver.check() == z3.sat  # The clock alternative must not assume reset.
    else:
        assert z3.is_false(z3.simplify(after.hits[goals[1]]))
        proof = facts.proof or _SyntacticClosureProof({})
        assert proof.coarse_signed_hull(after.state[names['q']]).hi == 7


@pytest.mark.parametrize('required', [False, True])
@pytest.mark.parametrize('changed', ['none', 'state', 'alive', 'enable'])
def test_control_facts_require_the_same_continuing_phase(required, changed):
    from types import SimpleNamespace
    from behave.sim.rtl.source_relation import _HistoryControlFacts

    facts = _HistoryControlFacts()
    first = facts.begin({}, None, None)
    enable, alive, continuation = z3.Bools('scope_enable scope_alive scope_continuation')
    state = {'q': z3.BitVec('scope_q', 8)}
    phase = SimpleNamespace(state=state, alive=alive, continuable=continuation)
    facts.finish(phase, enable, required)
    next_enable = enable if required else continuation
    if changed == 'state':
        state = dict(state)  # Native replacement is deliberately conservative.
    elif changed == 'alive':
        alive = z3.Bool('unrelated_alive')
    elif changed == 'enable':
        next_enable = z3.Bool('unrelated_enable')
    assert (facts.begin(state, alive, next_enable) is first) == (changed == 'none')


@pytest.mark.parametrize('direct', [False, True])
@pytest.mark.parametrize('required', [False, True])
@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('threshold', [2, 16])
def test_scoped_counter_history_preserves_failure_and_prunes_early_deep_branch(
        tmp_path, direct, required, private, threshold):
    from dataclasses import replace
    from behave.sim.core.construction_checkpoint import finish_construction
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_relation import _HistoryControlFacts

    source = f'''module M(input clk,stop,inc,input [7:0] a,output reg [7:0] q);
      reg [7:0] n;
      always @(posedge clk) begin
        if(stop) $fatal(1);
        if(n>={threshold}) q<=a;
        n<=n+inc;
      end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    goals = (_goal(source, plan, f'n>={threshold}'),
             _goal(source, plan, '$fatal', 'failure', 'artifact-failure'))
    relation, initial, env = _prepare(machine,
        {'clk': 0, 'stop': 0, 'inc': 0, 'a': 0, 'q': 0, 'n': 0}, goals)
    if direct:
        relation = replace(relation, direct=NativeCoverSourceCompiler(machine))
    if private:
        context = z3.Context()
        relation = relation.translate(context)
        initial = {name: value.translate(context) for name, value in initial.items()}
    context = relation.source.context
    facts = _HistoryControlFacts()
    states, alive, error, enabled = [initial, initial], [None, None], [None, None], [None, None]
    constraints = []
    for step in range(6):
        pins = _pins(machine, env, {names['clk']: step % 2, names['stop']: 0,
                                  names['inc']: 0, names['a']: 0})
        pins = {name: value if value.ctx == context else value.translate(context)
                for name, value in pins.items()}
        for name in ('stop', 'inc', 'a'):
            width = 8 if name == 'a' else 1
            value = z3.FreshConst(z3.BitVecSort(width, ctx=context), name)
            pins[names[name]] = z3.ZeroExt(pins[names[name]].size() - width, value)
        rows = []
        for side in (0, 1):
            row = finish_construction(relation.encode_phases_steps(states[side], (pins,),
                event_bounds=(machine.event_bound,), alive=alive[side], error=error[side],
                enabled=enabled[side], require_quiescence=required, tag=f'scope_{side}_{step}',
                _facts=facts if side else None))
            rows.append(row)
            constraints.extend(row.constraints)
            states[side], alive[side], error[side] = row.state, row.alive, row.error
            enabled[side] = None if required else row.continuable
        solver = z3.Solver(ctx=context)
        solver.add(*constraints)
        assert solver.check() == z3.sat
        left, right = rows
        differences = [left.alive != right.alive, left.error != right.error,
                       *(left.hits[goal] != right.hits[goal] for goal in goals),
                       z3.And(left.alive, z3.Or(*(left.state[names[name]] != right.state[names[name]]
                                                  for name in ('n', 'q'))))]
        solver.add(z3.Or(*differences))
        assert solver.check() == z3.unsat
        if threshold == 16:
            assert z3.is_false(z3.simplify(right.hits[goals[0]]))
    if threshold == 2:
        # Pruning must not erase the branch once the counter can reach it.
        solver = z3.Solver(ctx=context)
        solver.add(*constraints, right.hits[goals[0]])
        assert solver.check() == z3.sat


@pytest.mark.parametrize('direct', [False, True])
@pytest.mark.parametrize('terminal', [False, True])
def test_completed_definitions_require_successful_physical_phases(tmp_path, direct, terminal):
    from dataclasses import replace
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler

    update = '$fatal(1);' if terminal else 'q<=a;'
    source = f'''module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) if(a==2) {update} else q<=0;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    target = _goal(source, plan, 'a==2')
    relation, pre, env = _prepare(machine, {'clk': 0, 'a': 0, 'q': 0}, (target,))
    if direct:
        relation = replace(relation, direct=NativeCoverSourceCompiler(machine))
    phases = tuple(_pins(machine, env, {names['clk']: clk, names['a']: 0}) for clk in (0, 1, 0))
    for pins in phases:
        pins[names['a']] = None
    encoded = relation.encode_phases(pre, phases, event_bounds=(machine.event_bound,) * 3,
        held_inputs=(names['a'],), require_quiescence=False)
    solver = z3.Solver()
    solver.add(*encoded.constraints, encoded.continuable)
    assert solver.check() == z3.sat
    definitions = tuple(eq for phase in encoded.phases for step in phase.step_definitions for eq in step)
    solver.add(z3.Not(z3.And(*definitions)))
    assert solver.check() == z3.unsat
    if terminal:
        # Failure-prefix observation is deliberately NOT restricted to success.
        solver = z3.Solver()
        solver.add(*encoded.constraints, encoded.hits[target], z3.Not(encoded.continuable))
        assert solver.check() == z3.sat


def test_helper_calls_retain_distinct_occurrences_of_the_same_goal(tmp_path):
    source = '''module M(input clk,mode,input [3:0] a,b,output reg [3:0] q);
      function automatic [3:0] f(input [3:0] v);
        if(v==9) return 13; return v;
      endfunction
      always @(posedge clk) q <= mode ? f(a) : f(b);
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    target = _goal(source, plan, 'v==9')
    initial = {'clk': 0, 'mode': 0, 'a': 0, 'b': 0, 'q': 0}
    relation, pre, env = _prepare(machine, initial, (target,))
    pins = _pins(machine, env, {names[name]: int(name == 'clk')
                               for name in initial if name != 'q'})
    pins.update({names[name]: None for name in ('a', 'b', 'mode')})
    encoded = relation.encode(pre, pins, event_bound=machine.event_bound)
    assert len(encoded.occurrences) == 2
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    solver.push()
    solver.add(encoded.hits[target] != z3.Or(*encoded.occurrences.values()))
    assert solver.check() == z3.unsat
    solver.pop()
    for a, b, mode in product((0, 9), (0, 9), (0, 1)):
        image = {names['clk']: 1, names['a']: a, names['b']: b, names['mode']: mode}
        histories = _histories(machine, _state(machine, initial), (image,), (machine.event_bound,))
        expected = {frozenset(key for mark, key, goal in machine.occurrences
                              if goal == target and mark in marks)
                    for _state_image, marks, failed in histories if not failed}
        assert len(expected) == 1
        expected, = expected
        solver.push()
        solver.add(*(encoded.inputs[name] == value for name, value in image.items()))
        assert solver.check() == z3.sat
        assert _true(solver.model(), encoded.occurrences) == expected
        solver.add(z3.Or(*(term != (key in expected) for key, term in encoded.occurrences.items())))
        assert solver.check() == z3.unsat
        solver.pop()


def test_startup_drains_pending_work_without_a_new_drive_or_later_hit_credit(tmp_path):
    source = '''module M(input [1:0] a,output reg [1:0] q);
      always_comb if(a==0) q=3; else q=a;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    target = _goal(source, plan, 'a==0')
    relation, pre, env = _prepare(machine, {'a': 0, 'q': 0}, (target,), startup=True)
    pins = _pins(machine, env, {names['a']: 0})
    premature_drive = relation.encode(pre, pins, event_bound=machine.event_bound)
    solver = z3.Solver()
    solver.add(*premature_drive.constraints)
    assert solver.check() == z3.unsat  # An external drive cannot interrupt active work.
    startup = relation.settle(pre, event_bound=machine.event_bound)
    normal = relation.encode_phases(startup.state, (pins,), event_bounds=(machine.event_bound,),
                                    alive=startup.alive, error=startup.error)
    solver = z3.Solver()
    solver.add(*startup.constraints, *normal.constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(z3.Not(startup.hits[target]), normal.hits[target],
        startup.state[names['q']] != 3, normal.state[names['q']] != 3,
        z3.Not(startup.alive), z3.Not(normal.alive)))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('terminal', [False, True])
def test_return_edge_and_terminal_prefix_belong_to_the_same_slot(tmp_path, terminal):
    update = '$fatal(1,"stop");' if terminal else 'q<=a;'
    source = f'''module M(input clk,input [1:0] a,output reg [1:0] q,r);
      always @(posedge clk) if(a==2) {update} else q<=0;
      always @(negedge clk) if(q==2) r<=3; else r<=0;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    rising, falling = (_goal(source, plan, term) for term in ('a==2', 'q==2'))
    goals = (rising, falling)
    relation, pre, env = _prepare(machine, {'clk': 0, 'a': 0, 'q': 0, 'r': 0}, goals)
    phases = tuple(_pins(machine, env, {names['clk']: clk, names['a']: 2}) for clk in (0, 1, 0))
    encoded = relation.encode_phases(pre, phases, event_bounds=(machine.event_bound,) * 3)
    control, clock, advance = encoded.phases
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.sat
    model = solver.model()
    assert _true(model, control.hits) == frozenset()
    assert _true(model, clock.hits) == {rising}
    assert _true(model, advance.hits) == (frozenset() if terminal else {falling})
    solver.add(z3.Or(
        control.state[names['q']] != 0,
        clock.hits[falling], advance.hits[rising],
        encoded.alive == terminal,
        encoded.error != clock.error,
        encoded.hits[falling] == terminal,
        *(() if terminal else (encoded.state[names['r']] != 3,)),
    ))
    assert solver.check() == z3.unsat


def test_coincident_clocks_share_old_state_and_preserve_off_edge_reset(tmp_path):
    source = '''module M(input c0,c1,rst,input [1:0] a,output reg [1:0] q,r);
      always @(posedge c0 or posedge rst) if(rst) q<=0; else q<=a;
      always @(posedge c1) if(q==2) r<=3; else r<=q;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    target, reset_goal = (_goal(source, plan, text) for text in ('q==2', 'if(rst)'))
    relation, pre, env = _prepare(machine, {'c0': 0, 'c1': 0, 'rst': 0, 'a': 0, 'q': 2, 'r': 0},
                                  (target, reset_goal))
    for reset in (0, 1):
        pins = lambda level, reset=reset: _pins(machine, env, {names['c0']: level, names['c1']: level,
                                                  names['rst']: reset, names['a']: 1})
        encoded = relation.encode_phases(pre, tuple(pins(level) for level in (0, 1, 0)),
                                         event_bounds=(machine.event_bound,) * 3)
        solver = z3.Solver()
        solver.add(*encoded.constraints)
        assert solver.check() == z3.sat
        solver.add(z3.Or(encoded.hits[target] == bool(reset),
            encoded.phases[0].hits[reset_goal] != bool(reset),
            encoded.state[names['q']] != (0 if reset else 1),
            encoded.state[names['r']] != (0 if reset else 3)))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('held', [False, True])
def test_history_query_varies_only_inputs_not_explicitly_held(tmp_path, held):
    source = '''module M(input clk,input [1:0] a,output reg [1:0] q,r);
      always @(posedge clk) begin
        if(q==1 && a==2) r<=3;
        q<=a;
      end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    target = _goal(source, plan, 'q==1')
    relation, pre, env = _prepare(machine, {'clk': 0, 'a': 0, 'q': 0, 'r': 0}, (target,))
    phases = tuple(_pins(machine, env, {names['clk']: clk, names['a']: 0}) for clk in (1, 0, 1))
    for pins in phases:
        pins[names['a']] = None
    encoded = relation.encode_phases(pre, phases, event_bounds=(machine.event_bound,) * 3,
                                     held_inputs=(names['a'],) if held else ())
    solver = z3.Solver()
    solver.add(*encoded.constraints, encoded.hits[target])
    assert solver.check() == (z3.unsat if held else z3.sat)
    if not held:
        solver.add(z3.Or(encoded.phases[0].inputs[names['a']] != 1,
                         encoded.phases[2].inputs[names['a']] != 2,
                         encoded.state[names['r']] != 3))
        assert solver.check() == z3.unsat


@pytest.mark.cosim
@pytest.mark.parametrize('helper_copies', [False, True])
def test_every_native_phase_occurrence_and_sample_matches_the_shared_relation(tmp_path, helper_copies):
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.initial_state import RTLInitialState
    from behave.sim.rtl.native_observation import _read_intervals

    from .test_rtl_source_trace import _contract

    helper = '''function automatic [3:0] f(input [3:0] v);
      if(v==9) return 7; return v;
    endfunction''' if helper_copies else ''
    value = 'f(0)+f(0)+f(a)' if helper_copies else 'a'
    source = f'''module M(input clk,rst,input [3:0] a,output reg [3:0] y);
      reg [3:0] q;
      {helper}
      always @(posedge clk) if(rst) q<=0; else if(a==9) q<={value}; else q<={value};
      always @(negedge clk) if(rst) y<=0; else if(q==9) y<=q; else y<=q;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    if helper_copies:
        assert len({mark for mark, _, _ in machine.occurrences}) < len(machine.occurrences)
    manifest = manifest_of(plan.declarations, filename='design.sv')
    artifact = RTLArtifact(tmp_path / 'design.sv', 'M', tmp_path / 'native')
    stimulus = api.Stimulus({'input': [(9,), (2,), (9,)]}, [
        api.EventSlot(offers=('input',), controls={'rst': 0}) for _ in range(3)])
    widths = {'q': 4, 'y': 4}
    trace = artifact.exact_trace(_contract('rising'), stimulus,
        observe_names=('a', 'q', 'y'), state_names=tuple(widths), state_widths=widths,
        initial_state=RTLInitialState.from_values(artifact.identity, widths, dict.fromkeys(widths, 0)))
    native = list(_read_intervals(thaw_record_value(trace.source_evidence), stimulus,
                                  manifest, expected_plan=None, prefix=False))
    assert [phase for phase, _hits in native] == ['startup', *(['control', 'clock', 'advance'] * 3)]
    names = _names(machine)
    relation, pre, env = _prepare(machine, {'clk': 0, 'rst': 0, 'a': 0, 'q': 0, 'y': 0}, tuple(manifest))
    solver, differences = z3.Solver(), []
    alive = error = None
    for slot, record in enumerate(trace.records):
        phases = tuple(_pins(machine, env, {names['clk']: clk, names['rst']: 0, names['a']: 0})
                       for clk in (0, 1, 0))
        for pins in phases:
            pins[names['a']] = None
        encoded = relation.encode_phases(pre, phases, event_bounds=(machine.event_bound,) * 3,
            held_inputs=(names['a'],), alive=alive, error=error, tag=f'slot{slot}')
        solver.add(*encoded.constraints, encoded.phases[0].inputs[names['a']] == (9, 2, 9)[slot])
        for phase, (_name, observed) in zip(encoded.phases, native[1 + slot * 3:4 + slot * 3], strict=True):
            differences.extend(term != (key in observed.occurrences) for key, term in phase.occurrences.items())
            differences.extend(term != (goal in observed.goals) for goal, term in phase.hits.items())
            differences.append(z3.Not(phase.alive))
        differences.extend(encoded.phases[0].state[names[name]] != dict(record.outputs)[name]
                           for name in widths)
        differences.extend(encoded.state[names[name]] != dict(record.state)[name] for name in widths)
        assert record.hits.catalogue == tuple(sorted(manifest))
        public_hits = dict(record.hits)
        assert all(type(value) is bool for value in public_hits.values())
        differences.extend(term != public_hits.get(goal, False) for goal, term in encoded.hits.items())
        pre, alive, error = encoded.state, encoded.alive, encoded.error
    assert solver.check() == z3.sat
    # All enabled orderings agree, not merely the first satisfying assignment.
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


def test_recursive_storage_and_deferred_writes_survive_all_phases(tmp_path):
    source = '''module M(input clk,i,input [1:0] a,output reg [1:0] q,r);
      reg [1:0] mem[0:1];
      always @(posedge clk) begin mem[i]<=a; mem[1-i]<=mem[i]; end
      always @(negedge clk) begin if(mem[0]==3) q<=3; else q<=0; r<=mem[1]; end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    goals = tuple(manifest_of(plan.declarations, filename='design.sv'))
    initial = {'clk': 0, 'i': 0, 'a': 0, 'q': 0, 'r': 0, 'mem': [1, 2]}
    relation, pre, env = _prepare(machine, initial, goals)
    phases = tuple(_pins(machine, env, {names['clk']: clk, names['i']: 0, names['a']: 0})
                   for clk in (0, 1, 0))
    for pins in phases:
        pins[names['i']] = pins[names['a']] = None
    bounds = (machine.event_bound,) * 3
    encoded = relation.encode_phases(pre, phases, event_bounds=bounds,
                                     held_inputs=(names['i'], names['a']))
    for index, value in product(range(2), range(4)):
        concrete = tuple({names['clk']: clk, names['i']: index, names['a']: value} for clk in (0, 1, 0))
        histories = _histories(machine, _state(machine, initial), concrete, bounds)
        assert len(histories) == 1
        cells, marks, failed = next(iter(histories))
        assert not failed
        expected = dict(cells)
        reached = {key for mark, key, _goal in machine.occurrences if mark in marks}
        solver = z3.Solver()
        solver.add(*encoded.constraints, encoded.phases[0].inputs[names['i']] == index,
                   encoded.phases[0].inputs[names['a']] == value)
        assert solver.check() == z3.sat
        solver.add(z3.Or(z3.Not(encoded.alive),
            *(term != (key in reached) for key, term in encoded.occurrences.items()),
            *(encoded.state[names[key]] != expected[names[key]] for key in ('q', 'r')),
            *(actual != wanted for actual, wanted in zip(_leaves(encoded.state[names['mem']]),
                                                         expected[names['mem']], strict=True))))
        assert solver.check() == z3.unsat
