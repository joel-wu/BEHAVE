"""Direct source construction preserves events, not just settled output bits."""

from dataclasses import replace

import pytest
import z3

from behave.sim.core.refusal import Refused
from behave.sim.rtl.source_direct import DirectSourceCompiler
from behave.sim.symbolic.step_template import _tree

from .test_rtl_source_relation import _pins, _relation
from .test_rtl_source_scheduler import _lower, _names, _state

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('memory', [False, True])
def test_phase_batch_is_exact_on_failed_prefixes_and_inactive_actions(tmp_path, monkeypatch, private_context, memory):
    from behave.sim.symbolic.encoder import widen
    from behave.sim.symbolic.step_template import StepTemplate, _leaves

    other = ('''reg [2:0] cells[0:1]; always @(posedge other_clk) begin
                cells[a[0]]<=a; r<=cells[a[0]]; end''' if memory else
             'always @(posedge other_clk) r<=a^3;')
    machine, _ = _lower(tmp_path, '''module M(input clk, other_clk, input [2:0] a,
        output reg [2:0] q, r, s);
      always @(posedge clk) begin q=a; if(a==3) q='x; q=q+1'b1; end
      always @(negedge clk) s<=a;
    ''' + other + ' endmodule', defer_domains=True)
    phases = {(frozenset(roots), None): None for roots in ((), (0,), (1,), (2,))}
    compiler = DirectSourceCompiler(machine)
    ordinary = {phase: compiler._template(phase[0], driving=True) for phase in phases}
    batched = DirectSourceCompiler(machine)
    calls = []
    initialize = StepTemplate.__init__

    def counted(self, procedure, env, **kwargs):
        calls.append(procedure)
        initialize(self, procedure, env, **kwargs)

    monkeypatch.setattr(StepTemplate, '__init__', counted)
    batched._compile_phases(phases)
    assert len(calls) == 1
    batched._compile_phases(phases)
    assert len(calls) == 1  # Only syntax is cached; no answer or native state.
    context = z3.Context() if private_context else z3.main_ctx()
    failures = []
    for variant, (phase, (shared_machine, shared_template)) in enumerate(phases.items()):
        old_machine, old_template = ordinary[phase]
        assert shared_machine.procedure.semantics.error_codes == old_machine.procedure.semantics.error_codes
        slow = old_template.source.translate(context).instantiate(tag='ordinary')
        fast = shared_template.source.translate(context).instantiate(slow.pre, tag='shared')
        differences = [slow.ok != fast.ok, slow.error != fast.error]
        for field in ('state_post', 'post', 'hits'):
            assert getattr(slow, field).keys() == getattr(fast, field).keys()
            for name, value in getattr(slow, field).items():
                other = getattr(fast, field)[name]
                for left, right in zip(_leaves(_tree(value)), _leaves(_tree(other)), strict=True):
                    if z3.is_bv(left):
                        left, right = widen(left, right)
                    differences.append(left != right)
        solver = z3.Solver(ctx=context)
        solver.add(slow.constraint, fast.constraint)
        assert solver.check() == z3.sat
        solver.push()
        solver.add(z3.Or(*differences))
        assert solver.check() == z3.unsat
        solver.pop()
        solver.add(z3.Not(fast.ok))
        failures.append(solver.check() == z3.sat)
        selector, = fast.pre.keys() - slow.pre.keys()
        assert z3.is_bv_value(fast.pre[selector])
        assert fast.pre[selector].as_long() == variant
        solver = z3.Solver(ctx=context)
        solver.add(fast.constraint, fast.pre[selector] != variant)
        assert solver.check() == z3.unsat  # The solver cannot choose another plan.
    assert any(failures)  # Comparing failure states was not vacuous.


def test_unrepresentable_shared_phase_keeps_original_encoding(tmp_path, monkeypatch):
    from behave.sim.symbolic.encoder import EncodingError

    machine, _ = _lower(tmp_path, '''module M(input clk, input [2:0] a,
        output reg [2:0] q); always @(posedge clk) q<=a; endmodule''')
    compiler = DirectSourceCompiler(machine)
    original = compiler._compile_template
    attempts = []

    def compile(commands, *, selector=None, normalize=True):
        attempts.append(selector)
        if selector is not None:
            raise EncodingError('combined phase carrier unavailable', kind='range')
        return original(commands, normalize=normalize)

    monkeypatch.setattr(compiler, '_compile_template', compile)
    phases = {(frozenset(roots), None): None for roots in ((), (0,))}
    compiler._compile_phases(phases)
    assert attempts[0] is not None and attempts[1:] == [None, None]
    assert all(template.source.procedure.inputs == machine.procedure.inputs
               for _, template in phases.values())


def test_shared_phase_query_compiles_only_its_requested_observations(tmp_path, monkeypatch):
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.symbolic.step_template import StepTemplate
    from .test_rtl_source_query import _goal

    rtl = '''module M(input clk, other_clk, input [7:0] a, b,
        output reg [7:0] q, r);
      always @(posedge clk) if(a==2) q<=a; else q<=0;
      always @(posedge other_clk) if(b*b==17) r<=b*b*b; else r<=b+1;
    endmodule'''
    machine, plan = _lower(tmp_path, rtl)
    goal = _goal(rtl, plan, 'a==2')
    original = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges})
    owner = original.relation(machine, (goal,))
    phases = {(frozenset(roots), None): None for roots in ((), (0,), (1,))}
    compiler = DirectSourceCompiler(machine)
    calls, initialize = [], StepTemplate.__init__
    selections, select = [], StepTemplate.selected.__func__

    def counted(self, procedure, env, **kwargs):
        calls.append(procedure)
        initialize(self, procedure, env, **kwargs)

    def selected(cls, procedure, env, marks, **kwargs):
        selections.append(frozenset(marks))
        return select(cls, procedure, env, marks, **kwargs)

    monkeypatch.setattr(StepTemplate, '__init__', counted)
    monkeypatch.setattr(StepTemplate, 'selected', classmethod(selected))
    compiler._compile_phases(phases, owner=owner)
    assert len(calls) == 1
    shared = next(iter(phases.values()))[1]._specialization[0]
    assert shared._source is None
    assert calls[0] is not shared.procedure  # Encoded a sliced IR, not the full family.
    requested = {mark for mark, _, other in machine.occurrences if other == goal}
    requested.update(mark for mark, _ in machine.domain_checks)
    # Already-controlled marks may be retained for free by the shared slicer;
    # they must not be roots that demand another computation.
    assert selections == [frozenset(requested)]
    for direct, template in phases.values():
        relation = template.relation(direct, owner.goal_names, state_reads=owner.source.state_names)
        assert relation.goal_names == (goal,)
        assert template._source is None
    assert len(calls) == 1  # Every physical phase shares the selected preparation.


def test_broader_phase_consumer_retains_separate_encoding_fallback(tmp_path, monkeypatch):
    from behave.sim.rtl.source_relation import SourceIntervalTemplate
    from behave.sim.symbolic.step_template import StepTemplate
    from behave.sim.symbolic.encoder import EncodingError
    from .test_rtl_source_query import _goal

    rtl = '''module M(input clk, input [1:0] a, output reg [1:0] q);
      always @(posedge clk) if(a==2) q<=a; else q<=0;
    endmodule'''
    machine, plan = _lower(tmp_path, rtl)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges})
    owner = template.relation(machine, (_goal(rtl, plan, 'a==2'),))
    compiler = DirectSourceCompiler(machine)
    phases = {(frozenset(roots), None): None for roots in ((), (0,))}
    compiler._compile_phases(phases, owner=owner)
    assert all('_direct_phase' in view.procedure.inputs for _, view in phases.values())
    initialize, attempted = StepTemplate.__init__, []

    def unsupported_full_family(self, procedure, env, **kwargs):
        if '_direct_phase' in procedure.inputs:
            attempted.append(procedure)
            raise EncodingError('broader shared image needs separate carriers', kind='width')
        initialize(self, procedure, env, **kwargs)

    monkeypatch.setattr(StepTemplate, '__init__', unsupported_full_family)
    compiler._compile_phases(phases)  # Full-state consumer after a selected one.
    assert attempted
    for _, view in phases.values():
        assert view.procedure.inputs == machine.procedure.inputs
        assert view.source.state_names == tuple(machine.procedure.state)


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('width', [2, 7])
def test_successful_cut_commutes_independent_fallible_actions(tmp_path, reverse, width):
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    actions = ["assign y=a^1;", "always_comb begin q=b+1; if(b==3) q='x; end"]
    if reverse:
        actions.reverse()
    machine, _ = _lower(tmp_path, f'''module M(input [{width-1}:0] a,b,
        output [{width-1}:0] y, output reg [{width-1}:0] q);
        {' '.join(actions)} endmodule''', defer_domains=True, initialization=True)
    ordinary = DirectSourceCompiler.for_settled_state(machine)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
        ordinary.plan(roots, all_active=True)
    direct = DirectSourceCompiler.for_settled_state(machine, successful_only=True)
    assert direct.plan(roots, all_active=True)
    env = {**machine.procedure.state_ranges, **machine.input_ranges}
    full = SourceIntervalTemplate(machine.procedure, env).normalized().relation(
        machine, (), complete_state=True)
    small = replace(full, direct=direct)
    initial = full.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    slow = full.encode(initial.state, None, event_bound=machine.event_bound,
        require_quiescence=False, tag='scheduler')
    fast = small.encode(initial.state, None, event_bound=machine.event_bound,
        require_quiescence=False, tag='successful')
    solver = z3.Solver()
    solver.add(*slow.constraints, *fast.constraints, z3.Not(slow.unfinished))
    assert solver.check() == z3.sat
    for expected in (True, False):
        solver.push()
        solver.add(fast.continuable == expected)
        assert solver.check() == z3.sat  # Failure was not removed/assumed away.
        solver.pop()
    differences = [slow.continuable != fast.continuable]
    differences.extend(z3.And(slow.continuable, slow.state[name] != fast.state[name])
                       for name in machine.program.fields)
    solver.push()
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat
    solver.pop()
    # Failure-prefix payloads really can differ. This is why the relaxation
    # is opt-in for successful boundaries, never a native/event replacement.
    solver.add(z3.Not(fast.continuable), z3.Not(slow.continuable),
        z3.Or(*(slow.state[name] != fast.state[name] for name in machine.program.fields)))
    assert solver.check() == z3.sat


def test_successful_cut_admits_closed_fallible_dataflow_not_event_prefixes(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,
        output reg [1:0] q, output [1:0] y);
      always_comb begin q=a; if(a==3) q='x; end
      assign y=q+1;
    endmodule''', defer_domains=True, initialization=True)
    direct = DirectSourceCompiler.for_settled_state(machine, successful_only=True)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert direct.plan(roots, all_active=True)
    with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
        DirectSourceCompiler.for_settled_state(machine).plan(roots, all_active=True)


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('width', [2, 5])
def test_successful_fallible_dag_covers_all_original_successes(tmp_path, reverse, width):
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    actions = ['always_comb r=a;', "always_comb begin q=r+1; if(r==3) q='x; end", 'assign y=q+1;']
    if reverse:
        actions.reverse()
    machine, _ = _lower(tmp_path, f'''module M(input [{width-1}:0] a,
        output reg [{width-1}:0] r,q,output [{width-1}:0] y);
        {' '.join(actions)} endmodule''', defer_domains=True, initialization=True)
    env = {**machine.procedure.state_ranges, **machine.input_ranges}
    full = SourceIntervalTemplate(machine.procedure, env).normalized().relation(machine, (), complete_state=True)
    initial = full.initial_state(machine, dict.fromkeys(machine.program.fields), declarations=True)
    compiler = DirectSourceCompiler.for_settled_state(machine, successful_only=True)
    assert compiler.certifies_fixed_points()
    slow = full.encode(initial.state, None, event_bound=machine.event_bound,
        require_quiescence=False, tag='scheduler')
    fast = replace(full, direct=compiler).encode(initial.state, None, event_bound=machine.event_bound,
        require_quiescence=False, tag='successful')
    solver = z3.Solver()
    solver.add(*slow.constraints, *fast.constraints, z3.Not(slow.unfinished))
    solver.push()
    solver.add(slow.continuable)
    assert solver.check() == z3.sat
    solver.add(z3.Or(z3.Not(fast.continuable),
        *(slow.state[name] != fast.state[name] for name in machine.program.fields)))
    assert solver.check() == z3.unsat
    solver.pop()
    # Failure order is deliberately NOT a certificate of this optional view.
    # Ordinary source/native evidence must still use the original relation.
    solver.add(z3.Not(slow.continuable), fast.continuable)
    assert solver.check() == z3.sat


def test_successful_fallible_dag_requires_guard_sensitivity(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,b,output reg [1:0] q);
      always @(a) begin q=a; if(b==3) q='x; end
    endmodule''', defer_domains=True, initialization=True)
    compiler = DirectSourceCompiler.for_settled_state(machine, successful_only=True)
    assert not compiler._regions()


@pytest.mark.parametrize('outside_writer', ['none', 'blocking', 'nba', 'environment'])
def test_fixed_point_certificate_checks_all_bit_owners(tmp_path, outside_writer):
    machine, _ = _lower(tmp_path, '''module M(input clk,input [3:0] a,
        output [3:0] y,output reg q);
      assign y=a^4'b1010;
      always @(posedge clk) q<=~q;
    endmodule''')
    names = _names(machine)
    if outside_writer in {'blocking', 'nba'}:
        processes = tuple(replace(p, write_masks=(*p.write_masks,
            (names['y'], int(outside_writer == 'blocking'), int(outside_writer == 'nba'))))
            if any(name == names['q'] for name, _, _ in p.write_masks) else p
            for p in machine.program.processes)
        machine = replace(machine, program=replace(machine.program, processes=processes))
    elif outside_writer == 'environment':
        machine = replace(machine, drive_inputs=(*machine.drive_inputs, (names['y'], 'extra_pin')))
    compiler = DirectSourceCompiler.for_settled_state(machine)
    assert compiler._regions()
    assert compiler.certifies_fixed_points() is (outside_writer == 'none')
    assert compiler.certifies_fixed_points() is (outside_writer == 'none')
    assert not DirectSourceCompiler(machine).certifies_fixed_points()


@pytest.mark.parametrize('initial_failure,runtime_failure', [('$fatal(1,"initial")', '$error("runtime")'),
                                                         ('$error("initial")', '$fatal(1,"runtime")')])
def test_direct_phases_keep_one_error_alphabet(tmp_path, initial_failure, runtime_failure):
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    source = f'''module M(input clk,input [1:0] a,output reg [1:0] q);
      initial begin if(0) {initial_failure}; q=0; end
      always @(posedge clk) begin if(a==1) {runtime_failure}; q=2; end
    endmodule'''
    machine, _ = _lower(tmp_path, source, initialization=True)
    names = _names(machine)
    env = {**machine.procedure.state_ranges, **machine.input_ranges}
    compiler = DirectSourceCompiler(machine)
    relation = SourceIntervalTemplate(machine.procedure, env, direct=compiler).relation(
        machine, (), complete_state=True)
    initial = relation.initial_state(machine, dict.fromkeys(machine.program.fields, 0), declarations=True)
    start = relation.settle(initial.state, event_bound=machine.event_bound)
    failed = relation.encode(start.state, _pins(machine, env, {names['clk']: 1, names['a']: 1}),
        event_bound=machine.event_bound, alive=start.alive, error=start.error)
    stopped = relation.encode(failed.state, _pins(machine, env, {names['clk']: 0, names['a']: 2}),
        event_bound=machine.event_bound, alive=failed.alive, error=failed.error)
    code = 'RTLFatal' if runtime_failure.startswith('$fatal') else 'RTLError'
    expected = machine.procedure.semantics.error_codes.index(code)
    solver = z3.Solver()
    solver.add(*start.constraints, *failed.constraints, *stopped.constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(start.error != 0, failed.alive, failed.error != expected,
                     stopped.alive, stopped.error != expected))
    assert solver.check() == z3.unsat
    assert all(template.source.procedure.semantics.error_codes == machine.procedure.semantics.error_codes
               for _, template in compiler._templates.values())


@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('observation', ['control', 'payload', 'source-event'])
def test_compiled_phase_closure_drops_only_unobserved_datapath(tmp_path, monkeypatch, failure, enabled, observation):
    from types import SimpleNamespace
    from behave.sim.symbolic.encoder import widen
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalTemplate, SourceObservation

    source = '''module M(input clk, reset, a, input [7:0] d,
                        output logic valid, output logic [7:0] q);
      logic held; wire [7:0] mixed; assign mixed = q * 3 + 1;
      always_ff @(posedge clk) begin
        if(reset) begin held <= 0; q <= 0; end
        else begin held <= a; q <= mixed + d; FAULT end
      end
      always_ff @(negedge clk) if(held) valid <= 1; else valid <= 0;
    endmodule'''.replace('FAULT', "if(a) q <= 'x;" if failure else '')
    machine, _ = _lower(tmp_path, source, defer_domains=True)
    names = _names(machine)
    _, state, env = _relation(machine, _state(machine,
        {'clk': 0, 'reset': 0, 'a': 0, 'd': 0, 'q': 0, 'mixed': 1, 'held': 0, 'valid': 0},
        startup=True), ())
    state[names['q']] = z3.BitVec('initial_payload', state[names['q']].size())
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    goals = tuple({goal for _, key, goal in machine.occurrences
                   if key.path[1] == 'control-choice' and key.role == 'true'
                   and source[key.path[2]:key.path[3]].startswith('if(held)')}) \
        if observation == 'source-event' else ()
    assert len(goals) == int(observation == 'source-event')
    reads = (names['valid'], names['q']) if observation == 'payload' else (names['valid'],)
    owner = template.relation(machine, goals, state_reads=reads)
    phases = []
    for slot in range(2):
        pins = _pins(machine, env, {name: 0 for name, _ in machine.drive_inputs})
        for field in ('a', 'd', 'reset'):
            name = names[field]
            pins[name] = z3.BitVec(f'input_{slot}_{field}', pins[name].size())
        phases.extend((pins, {**pins, names['clk']: pins[names['clk']] + 1}, pins))
    bounds = (machine.event_bound,) * len(phases)
    with owner.direct.request_scope(SimpleNamespace(state=state), SourceObservation.GOAL_PREFIX):
        startup = owner.settle(state, event_bound=machine.event_bound, require_quiescence=False)
        preparations, relation = [], SourceIntervalTemplate.relation
        def counted(self, *args, **kwargs):
            preparations.append(self)
            return relation(self, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(SourceIntervalTemplate, 'relation', counted)
            selected = owner.direct.project_phases(owner, startup.state, zip(phases, bounds),
                state_reads=reads)
        assert preparations
        assert len(preparations) == len({id(template) for template in preparations})
        if selected is None:
            # A potentially failing macro may keep its complete boundary;
            # never force a smaller cone by discarding the failure prefix.
            assert failure or observation == 'payload'
            selected = owner
            common = set(owner.source.state_names)
        else:
            common = selected.direct._phase_projection[1]
            assert {names['valid'], names['held']} <= common
            if observation == 'payload':
                assert {names['q'], names['mixed']} <= common
            elif not failure:
                assert not {names['q'], names['mixed']} & common
        assert owner.direct._phase_projection is None
        full = owner.encode_phases(startup.state, phases, event_bounds=bounds,
            alive=startup.alive, error=startup.error, enabled=z3.BoolVal(enabled),
            require_quiescence=False, tag='full')
        small = selected.encode_phases(startup.state, phases, event_bounds=bounds,
            alive=startup.alive, error=startup.error, enabled=z3.BoolVal(enabled),
            require_quiescence=False, tag='small')
    solver = z3.Solver()
    solver.add(*startup.constraints, *full.constraints, *small.constraints)
    assert solver.check() == z3.sat
    if failure and enabled:
        solver.push()
        solver.add(z3.Or(*small.domain_faults.values()))
        assert solver.check() == z3.sat
        solver.pop()
    different = []
    for left, right in zip(full.phases, small.phases, strict=True):
        assert set(right.state) == set(common)
        assert set(left.domain_faults) == set(right.domain_faults)
        assert set(left.hits) == set(right.hits) == set(goals)
        different.extend((left.alive != right.alive, left.error != right.error,
                          left.unfinished != right.unfinished,
                          *(z3.Distinct(*widen(left.state[name], value)) if z3.is_bv(value)
                            else left.state[name] != value for name, value in right.state.items()),
                          *(left.hits[name] != value for name, value in right.hits.items()),
                          *(left.domain_faults[name] != value for name, value in right.domain_faults.items())))
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('width', [8, 64])
@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
def test_whole_request_empty_nba_masks_preserve_original_phases(tmp_path, width, failure, enabled):
    from types import SimpleNamespace
    from behave.sim.symbolic.encoder import widen
    from behave.sim.rtl.source_relation import SourceIntervalTemplate, SourceObservation

    source = f'''module M(input clk,go,input [1:0] index,value,
        output logic [{width-1}:0] q, output logic flag);
      always @(posedge clk) begin
        flag<=go;
        if(go) begin
          q[index*2+:2]<=value;
          if(value==3) q[index*2+:2]<=0;
          {"if(index==3) q<='x;" if failure else ''}
        end
      end
    endmodule'''
    machine, _ = _lower(tmp_path, source, defer_domains=True)
    names = _names(machine)
    _, state, env = _relation(machine, _state(machine, dict.fromkeys(names, 0), startup=True), ())
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=DirectSourceCompiler(machine)).normalized()
    owner = template.relation(machine, goals, complete_state=True)
    ordinary = replace(owner, direct=DirectSourceCompiler(machine))
    phases = []
    for slot in range(2):
        pins = _pins(machine, env, {name: 0 for name, _ in machine.drive_inputs})
        for field in ('go', 'index', 'value'):
            name = names[field]
            pins[name] = z3.BitVec(f'input_{slot}_{field}', pins[name].size())
        phases.extend((pins, {**pins, names['clk']: pins[names['clk']] + 1}, pins))
    options = dict(event_bounds=(machine.event_bound,) * len(phases),
        enabled=z3.BoolVal(enabled), require_quiescence=False)
    slow = ordinary.encode_phases(state, phases, tag='original', **options)
    with owner.direct.request_scope(SimpleNamespace(state=state), SourceObservation.GOAL_PREFIX):
        assert owner.direct._empty_queue_scope
        fast = owner.encode_phases(state, phases, tag='empty_queues', **options)
    assert not owner.direct._empty_queue_scope
    solver = z3.Solver()
    solver.add(*slow.constraints, *fast.constraints)
    assert solver.check() == z3.sat
    if failure and enabled:
        solver.push()
        solver.add(z3.Or(*fast.domain_faults.values()))
        assert solver.check() == z3.sat
        solver.pop()
    differences = []
    for left, right in zip(slow.phases, fast.phases, strict=True):
        differences.extend((left.alive != right.alive, left.error != right.error,
            left.unfinished != right.unfinished,
            *(left.hits[name] != value for name, value in right.hits.items()),
            *(left.domain_faults[name] != value for name, value in right.domain_faults.items()),
            *(z3.Distinct(*widen(left.state[name], value)) if z3.is_bv(value)
              else left.state[name] != value for name, value in right.state.items())))
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('memory', [False, True])
def test_commit_guard_does_not_couple_independent_queues(tmp_path, memory):
    from behave.sim.symbolic import ir
    from behave.sim.symbolic.encoder import widen
    from behave.sim.symbolic.step_template import _leaves

    other = ('''reg [3:0] cells[0:1]; always @(posedge clk) begin
                cells[a[0]]<=a; r<=cells[a[0]]; end''' if memory else
             'always @(posedge clk) if(a[0]) r<=a;')
    machine, _ = _lower(tmp_path, '''module M(input clk,input [3:0] a,
        output reg [3:0] q,r);
      always @(posedge clk) q<=a;
    ''' + other + ' endmodule')
    compiler = DirectSourceCompiler(machine)
    enabled, body = machine.actions[machine.commit_action]
    ordinary = ir.If(enabled, body, ir.Skip())
    reduced = compiler._action_command(machine.commit_action, False)
    compiler.closes_plan = False  # Test the action alone, on arbitrary entries.
    prefix = ir.Assign('accepted', ir.Lit(True))
    _, slow_template = compiler._compile_template([prefix, ordinary])
    _, fast_template = compiler._compile_template([prefix, reduced])
    slow = slow_template.source.instantiate(tag='old_commit')
    fast = fast_template.source.instantiate(slow.pre, tag='local_commit')
    different = [slow.ok != fast.ok, slow.error != fast.error]
    for name in slow.state_post:
        for left, right in zip(_leaves(_tree(slow.state_post[name])),
                               _leaves(_tree(fast.state_post[name])), strict=True):
            different.append(z3.Distinct(*widen(left, right)))
    solver = z3.Solver()
    solver.add(slow.constraint, fast.constraint)
    assert solver.check() == z3.sat
    solver.add(z3.Or(*different))
    assert solver.check() == z3.unsat


def test_empty_nba_scope_does_not_authorize_noncanonical_cuts(tmp_path):
    from types import SimpleNamespace
    from behave.sim.rtl.source_relation import SourceIntervalTemplate, SourceObservation

    machine, _ = _lower(tmp_path, '''module M(input clk, output logic [7:0] q);
      always @(posedge clk) q[0]<=1; endmodule''')
    _, state, _ = _relation(machine, _state(machine, dict(clk=0, q=0), startup=True), ())
    owner = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=DirectSourceCompiler(machine)).normalized().relation(machine, (), complete_state=True)
    compiler = owner.direct
    roots = frozenset(range(len(machine.program.processes)))
    with compiler.request_scope(SimpleNamespace(state=state), SourceObservation.GOAL_PREFIX):
        assert compiler._empty_queue_scope
        scoped = compiler.relation(owner, roots, driving=True)
    write, _, _ = machine.queue_edges[0]
    changed = {**state, write.mask: z3.BitVecVal(1, state[write.mask].size())}
    with compiler.request_scope(SimpleNamespace(state=changed), SourceObservation.GOAL_PREFIX):
        assert not compiler._empty_queue_scope
        arbitrary = compiler.relation(owner, roots, driving=True)
        assert arbitrary is not scoped


@pytest.mark.parametrize('private_context', [False, True])
def test_control_folding_removes_constants_without_assuming_success(private_context):
    from behave.sim.rtl.source_relation import _define_control

    context = z3.Context() if private_context else None
    true = z3.BoolVal(True, ctx=context)
    enabled, success = z3.Bools('enabled success', ctx=context)
    definitions = []
    assert z3.is_true(_define_control(z3.And(true, true), definitions, 'constant'))
    assert _define_control(z3.And(true, enabled), definitions, 'identity').eq(enabled)
    assert definitions == []
    output = _define_control(z3.And(enabled, success), definitions, 'symbolic')
    solver = z3.Solver(ctx=context)
    solver.add(*definitions, output != z3.And(enabled, success))
    assert solver.check() == z3.unsat
    solver = z3.Solver(ctx=context)
    solver.add(*definitions, enabled, z3.Not(success), z3.Not(output))
    assert solver.check() == z3.sat


def test_native_cover_keeps_competing_orders_and_transient_hits(tmp_path):
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler

    machine, _ = _lower(tmp_path, '''module M(input a,output logic y);
      wire b; assign b=a; always_comb if(b) y=a; else y=0;
    endmodule''')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, _ = _relation(machine, _state(machine, {'a': 1, 'b': 0, 'y': 0}, startup=True), goals)
    old = relation.settle(state, event_bound=machine.event_bound)
    cover = replace(relation, direct=NativeCoverSourceCompiler(machine)).settle(
        state, event_bound=machine.event_bound)
    assert len(cover.actions) == len(old.actions) > 1
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*old.constraints, *cover.constraints,
               *(left == right for left, right in zip(old.actions, cover.actions, strict=True)))
    assert solver.check() == z3.sat
    differences = [old.alive != cover.alive, old.error != cover.error,
                   *(old.state[name] != cover.state[name] for name in machine.program.fields),
                   *(old.hits[goal] != cover.hits[goal] for goal in goals)]
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('occurrences', [False, True])
def test_inactive_goals_use_value_cut_without_erasing_faults(tmp_path, failure, occurrences):
    from types import SimpleNamespace
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalTemplate, SourceObservation

    # Reconvergent source ordering is observable in general. The selected
    # dead branch, however, is false for every entry, not just this sample.
    body = "if(a) y='x;" if failure else ''
    source = '''module M(input a,output logic y);
      wire b; assign b=a;
      always_comb begin if(1'b0) y=1; else y=b; BODY end
    endmodule'''.replace('BODY', body)
    machine, _ = _lower(tmp_path, source, defer_domains=True)
    goals = tuple({goal for _, key, goal in machine.occurrences
                   if key.path[1] == 'control-choice' and key.role == 'true'
                   and source[key.path[2]:key.path[3]].startswith("if(1'b0)")})
    assert len(goals) == 1
    _, state, _ = _relation(machine, _state(machine, {'a': 1, 'b': 0, 'y': 0}, startup=True), goals)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    relation = template.relation(machine, goals, state_reads=machine.program.fields,
                                 occurrences=occurrences)
    assert relation.source.literal_false_hits(goals) == set(goals)
    original = replace(relation, direct=None).settle(state,
        event_bound=machine.event_bound, require_quiescence=False)
    with relation.direct.request_scope(SimpleNamespace(state=state), SourceObservation.GOAL_PREFIX):
        optimized = relation.settle(state, event_bound=machine.event_bound, require_quiescence=False)
    assert set(optimized.hits) == set(original.hits) == set(goals)
    assert set(optimized.occurrences) == set(original.occurrences)
    assert all(z3.is_false(z3.simplify(hit)) for hit in optimized.hits.values())
    if failure:
        assert set(optimized.domain_faults) == set(original.domain_faults)
        solver = z3.Solver()
        solver.add(*optimized.constraints, z3.Or(*optimized.domain_faults.values()))
        assert solver.check() == z3.sat  # A false goal cannot conceal an X/Z exit.
    else:
        assert len(optimized.actions) == 1 < len(original.actions)
        solver = z3.Solver()
        solver.add(*original.constraints, *optimized.constraints)
        assert solver.check() == z3.sat
        solver.add(z3.Or(original.alive != optimized.alive, original.error != optimized.error,
            original.unfinished != optimized.unfinished,
            *(original.hits[g] != optimized.hits[g] for g in goals),
            *(original.state[name] != optimized.state[name] for name in machine.program.fields)))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('driven', [False, True])
@pytest.mark.parametrize('failure', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
def test_native_cover_reuses_count_bound_without_assuming_quiescence(tmp_path, driven, failure, enabled):
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler

    body = 'if(a) $fatal(1,"bad");' if failure else ''
    machine, _ = _lower(tmp_path, '''module M(input a,output logic y);
      wire b; assign b=a; always_comb begin BODY if(b) y=a; else y=0; end
    endmodule'''.replace('BODY', body))
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'b': 0, 'y': 0},
                                                         startup=not driven), goals)
    if driven:
        pins = _pins(machine, env, {_names(machine)['a']: 1})
    else:
        pins = None
        name = _names(machine)['a']
        state[name] = z3.ZeroExt(state[name].size()-1, z3.BitVec('free_input', 1))
    # The caller may allow more events than this compiler proves necessary.
    # Keep the unrestricted longer scheduler as an independent padding oracle.
    bound = machine.event_bound + 5
    original = relation.encode(state, pins, event_bound=bound,
        require_quiescence=False, enabled=z3.BoolVal(enabled), tag='original_bound')
    cover = replace(relation, direct=NativeCoverSourceCompiler(machine)).encode(
        state, pins, event_bound=bound, require_quiescence=False,
        enabled=z3.BoolVal(enabled), tag='cover_bound')
    assert len(cover.actions) > 1  # Exercise the event-order fallback, not a macro.
    assert len(cover.actions) <= machine.event_bound < len(original.actions)
    assert z3.is_true(cover.quiescent)
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*original.constraints)
    assert solver.check() == z3.sat
    solver.push()
    solver.add(original.unfinished)
    assert solver.check() == z3.unsat  # No quiescence premise in this oracle.
    solver.pop()
    solver.add(*cover.constraints, *(a == b for a, b in zip(
        original.actions[:len(cover.actions)], cover.actions, strict=True)))
    solver.add(z3.Or(original.unfinished != cover.unfinished, original.alive != cover.alive,
        original.error != cover.error, *(original.hits[g] != cover.hits[g] for g in goals),
        # Independent encodings leave post-failure private successors free.
        # Surviving boundaries, all prefix hits and the failure itself agree.
        z3.And(original.alive, z3.Or(*(original.state[name] != cover.state[name]
                                      for name in original.state)))))
    assert solver.check() == z3.unsat
    short = replace(relation, direct=NativeCoverSourceCompiler(machine)).encode(
        state, pins, event_bound=1, require_quiescence=False, tag='short_bound')
    solver = z3.Solver()
    solver.add(*short.constraints, short.unfinished)
    assert solver.check() == z3.sat  # A real truncation must remain visible.


@pytest.mark.parametrize('symbolic', [False, True])
def test_native_cover_does_not_count_unowned_initial_nba_as_empty(tmp_path, symbolic):
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler

    machine, _ = _lower(tmp_path, '''module M(input clk,a,output logic y);
      logic q; wire b; always @(posedge clk) q<=a;
      assign b=q; always_comb if(b) y=a; else y=0;
    endmodule''')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, _ = _relation(machine,
        _state(machine, dict(clk=0, a=1, q=0, b=0, y=0), startup=True), goals)
    assert machine.queue_edges
    for write, _, _ in machine.queue_edges:
        old = state[write.valid]
        value = z3.Const('pending_' + write.valid, old.sort()) if symbolic else (
            z3.BoolVal(True) if z3.is_bool(old) else z3.BitVecVal(1, old.size()))
        state[write.valid] = value
    original = relation.encode(state, None, event_bound=machine.event_bound,
        require_quiescence=False, tag='queued_original')
    cover = replace(relation, direct=NativeCoverSourceCompiler(machine)).encode(
        state, None, event_bound=machine.event_bound,
        require_quiescence=False, tag='queued_cover')
    assert len(cover.actions) > 1
    assert not z3.is_true(cover.quiescent)
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*original.constraints, *cover.constraints,
        *(a == b for a, b in zip(original.actions, cover.actions, strict=True)))
    assert solver.check() == z3.sat
    solver.add(z3.Or(original.unfinished != cover.unfinished,
        original.alive != cover.alive, original.error != cover.error,
        *(original.hits[g] != cover.hits[g] for g in goals)))
    assert solver.check() == z3.unsat


def _compare(machine, values, driven, *, startup=False, symbolic_state=(), symbolic_pins=(),
             settled_state=False, marks=None):
    from behave.sim.symbolic.encoder import widen
    from behave.sim.rtl.source_values import ArrayType
    from behave.sim.symbolic.step_template import _leaves

    def symbolic(value, kind, tag):
        if isinstance(kind, ArrayType):
            return [symbolic(cell, kind.element, f'{tag}_{index}')
                    for index, cell in enumerate(_tree(value))]
        extend = z3.SignExt if kind.signed else z3.ZeroExt
        return extend(value.size() - kind.width, z3.BitVec(tag, kind.width))

    goals = () if settled_state else tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    if marks is not None:
        goals = tuple(dict.fromkeys(goal for mark, _, goal in machine.occurrences if mark in marks))
    relation, state, env = _relation(machine, _state(machine, values, startup=startup), goals)
    for field in symbolic_state:
        name = _names(machine)[field]
        kind = machine.program.kinds[name]
        state[name] = symbolic(state[name], kind, f'initial_{field}')
    pins = None if driven is None else _pins(machine, env,
        {_names(machine)[name]: value for name, value in driven.items()})
    for field in symbolic_pins:
        name = _names(machine)[field]
        kind = machine.program.kinds[name]
        pins[name] = symbolic(pins[name], kind, f'driven_{field}')
    compiler = (DirectSourceCompiler.for_settled_state(machine) if settled_state
                else DirectSourceCompiler(machine))
    if marks is not None:
        compiler = compiler.observing(marks)
    direct = replace(relation, direct=compiler)
    old = relation.encode(state, pins, event_bound=machine.event_bound, tag='old')
    new = direct.encode(state, pins, event_bound=machine.event_bound, tag='direct')
    solver = z3.Solver()
    solver.set(timeout=30_000)
    solver.add(*old.constraints, *new.constraints)
    assert solver.check() == z3.sat
    changed = [old.alive != new.alive, old.error != new.error]
    changed.append(z3.And(old.alive, old.quiescent != new.quiescent))
    changed.extend(old.hits[name] != new.hits[name] for name in goals)
    for name in (*machine.program.fields, *machine.pending,
                 *(write.valid for write, _, _ in machine.queue_edges)):
        left, right = _tree(old.state[name]), _tree(new.state[name])
        if isinstance(left, list):
            changed.extend(a != b for a, b in zip(_leaves(left), _leaves(right), strict=True))
        elif name not in machine.program.fields:
            changed.append((left != 0) != (right != 0))
        else:
            left, right = widen(left, right)
            changed.append(left != right)
    solver.add(z3.Or(*changed))
    assert solver.check() == z3.unsat
    assert len(new.actions) == 1  # The entire settling interval is one formula.
    assert z3.is_false(z3.simplify(new.unfinished))
    return new


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('mux', [False, True])
def test_settled_state_cut_preserves_reconvergent_values(tmp_path, reverse, mux):
    statements = ['assign b=a;', 'assign c=a;', 'assign r=b^c;',
                  'assign q=en ? r : a;' if mux else 'assign q=r;']
    if reverse:
        statements.reverse()
    machine, _ = _lower(tmp_path, '''module M(input en,input [1:0] a,output [1:0] q);
      wire [1:0] b,c,r;
    ''' + '\n'.join(statements) + ' endmodule')
    _compare(machine, dict(en=0, a=0, b=0, c=0, r=0, q=0), dict(en=1, a=1),
        symbolic_state=('en', 'a', 'b', 'c', 'r', 'q'), symbolic_pins=('en', 'a'), settled_state=True)


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('consumer', [
    'assign q=b ? a : 0;',
    'always_comb if(b) q=a; else q=0;',
    'always_comb begin q=0; if(b) q=a; end',
    'always_comb begin q=a; if(b) q=q+1; end',
    'always_comb begin q=b; q=0; end',
])
def test_query_observation_view_preserves_values_and_selected_marks(tmp_path, reverse, consumer):
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    statements = ['assign b=|a;', consumer, 'always_comb if(t) flag=1; else flag=0;']
    if reverse:
        statements.reverse()
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,input t,
      output logic [1:0] q,output logic flag); wire b;
    ''' + '\n'.join(statements) + ' endmodule')
    compiler = DirectSourceCompiler(machine)
    q = _names(machine)['q']
    unobserved = {node.goal for i, process in enumerate(machine.program.processes)
                  if q in compiler.effects[i][1] for node in ir.walk(process.activation)
                  if isinstance(node, ir.Mark)}
    marks = {mark for mark, _, _ in machine.occurrences} - unobserved
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert compiler._settling_order(roots) is None
    view = compiler.observing(marks)
    assert view._settling_order(roots) is not None
    assert compiler._settling_order(roots) is None  # No cache/view contamination.
    assert compiler.observing(marks) is view
    all_marks = {mark for mark, _, _ in machine.occurrences}
    assert view.observing(all_marks)._settling_order(roots) is None
    values = dict.fromkeys(_names(machine), 0)
    _compare(machine, values, None, startup=True, symbolic_state=tuple(values), marks=marks)

    # The public query relation selects marks through its normal catalogue;
    # a full observation view must retain the changing mux's event orders.
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges}, direct=compiler).normalized()
    goals = tuple(dict.fromkeys(goal for mark, _, goal in machine.occurrences if mark in marks))
    projected = template.relation(machine, goals, complete_state=True)
    assert projected.direct is view
    complete = template.relation(machine, tuple(dict.fromkeys(
        goal for _, _, goal in machine.occurrences)), complete_state=True, occurrences=True)
    assert complete.direct is compiler


@pytest.mark.parametrize('consumer', [
    'always_comb if(b) q=a;',
    'always_comb begin q=b; q=0; end always @(posedge q) flag=~flag;',
    'always_comb begin q=b; q=0; end always_comb if(q) flag=1; else flag=0;',
    "always_comb begin q=b; if(a) q='x; q=0; end",
])
def test_unobserved_writes_keep_latches_edges_selected_guards_and_failures(tmp_path, consumer):
    from behave.sim.symbolic import ir

    machine, _ = _lower(tmp_path, f'''module M(input a,output logic q,flag);
      wire b; assign b=a; {consumer} endmodule''', defer_domains=True)
    compiler = DirectSourceCompiler(machine)
    q = _names(machine)['q']
    hidden = {node.goal for i, process in enumerate(machine.program.processes)
              if q in compiler.effects[i][1] for node in ir.walk(process.activation)
              if isinstance(node, ir.Mark)}
    marks = {mark for mark, _, _ in machine.occurrences} - hidden
    view = compiler.observing(marks)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert view._settling_order(roots) is None
    assert not view._regions()


def test_unobserved_final_constants_do_not_erase_notification_cycles(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(output logic q,r);
      always @(r) begin q=1; q=0; end
      always @(q) begin r=1; r=0; end
    endmodule''')
    assert machine.event_bound is None
    with pytest.raises(Refused, match='rtl-direct-event-regions-unsupported'):
        DirectSourceCompiler(machine)
    # Independently exercise the DAG check, even if a future bound provider
    # supplies a finite exploration allowance for this source cycle.
    view = DirectSourceCompiler(replace(machine, event_bound=len(machine.actions))).observing(set())
    roots = frozenset(range(len(machine.program.processes)))
    assert view._settling_order(roots) is None


def test_settled_state_cut_does_not_silently_repair_unsettled_wires(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,output [1:0] q);
      wire [1:0] b,c,r; assign b=a; assign c=a; assign r=b^c; assign q=r;
    endmodule''')
    relation, state, env = _relation(machine, _state(machine, dict(a=0, b=0, c=0, r=0, q=3)), ())
    pins = _pins(machine, env, {_names(machine)['a']: 1})
    old = relation.encode(state, pins, event_bound=machine.event_bound)
    new = replace(relation, direct=DirectSourceCompiler.for_settled_state(machine)).encode(
        state, pins, event_bound=machine.event_bound)
    solver = z3.Solver()
    solver.add(*old.constraints)
    assert solver.check() == z3.sat  # A quiet scheduler alone is not a fixed point.
    solver = z3.Solver()
    solver.add(*new.constraints)
    assert solver.check() == z3.unsat  # The restricted view explicitly rejects this cut.


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('startup', [False, True])
@pytest.mark.parametrize('consumer', [
    'assign q=b ? c : a;',
    'always_comb if(b) q=c; else q=a;',
    'always_comb begin logic choose; choose=b; if(choose) q=c; else q=a; end',
])
def test_state_only_changing_guards_preserve_every_settled_value(tmp_path, reverse, startup, consumer):
    statements = ['assign b=|a;', 'assign c=a^1;', consumer]
    if reverse:
        statements.reverse()
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,output logic [1:0] q);
      wire b; wire [1:0] c;
    ''' + '\n'.join(statements) + ' endmodule')
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    # This is not permission to reorder source observations. The very same
    # RTL still requires event-sensitive treatment if its marks are observed.
    assert DirectSourceCompiler(machine)._settling_order(roots) is None
    assert DirectSourceCompiler.for_settled_state(machine)._settling_order(roots) is not None
    values = {name: 0 for name in _names(machine)}
    _compare(machine, values, None if startup else {'a': 1},
        startup=startup, symbolic_state=tuple(values),
        symbolic_pins=() if startup else ('a',), settled_state=True)


@pytest.mark.parametrize('consumer', [
    'always_comb if(b) q=a;',
    'always @(posedge b) q=a;',
])
def test_state_only_view_does_not_accept_latches_or_edges(tmp_path, consumer):
    machine, _ = _lower(tmp_path, f'''module M(input a,output logic q);
      wire b; assign b=a; {consumer} endmodule''')
    compiler = DirectSourceCompiler.for_settled_state(machine)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert compiler._settling_order(roots) is None
    assert not compiler._regions()


@pytest.mark.parametrize('startup', [False, True])
@pytest.mark.parametrize('write', [
    'q=b; q=0;',
    'q=0; if(sel) q=b;',
    'q=b; if(sel) q=q+1;',
])
def test_state_only_total_writes_preserve_all_orders(tmp_path, startup, write):
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,input sel,
        output logic [1:0] q,output wire [1:0] r);
      wire [1:0] b; assign b=a;
      always_comb begin ''' + write + ''' end
      assign r=q^1;
    endmodule''')
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    # Final-state equivalence is not permission to reorder source observations.
    assert DirectSourceCompiler(machine)._settling_order(roots) is None
    assert DirectSourceCompiler.for_settled_state(machine)._settling_order(roots) is not None
    values = {name: 0 for name in _names(machine)}
    _compare(machine, values, None if startup else {'a': 1, 'sel': 1},
        startup=startup, symbolic_state=tuple(values),
        symbolic_pins=() if startup else ('a', 'sel'), settled_state=True)


def test_state_only_total_writes_keep_edge_observers_outside_the_dag(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input a,output logic q,r);
      wire b; assign b=a;
      always_comb begin q=b; q=0; end
      always @(posedge q) r=~r;
    endmodule''')
    # The pulse can change persistent state although q's final value is zero.
    compiler = DirectSourceCompiler.for_settled_state(machine)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert compiler._settling_order(roots) is None
    assert not compiler._regions()


@pytest.mark.parametrize('offset', [0, 2])
@pytest.mark.parametrize('startup', [False, True])
def test_state_only_lane_slices_keep_precise_sensitivity(tmp_path, offset, startup):
    machine, _ = _lower(tmp_path, f'''module M(input [3:0] a,input [1:0] valid,
        output logic [1:0] q,output wire [1:0] r);
      wire [3:0] bus; assign bus=a;
      wire [1:0] en; assign en=valid;
      always_comb begin q=0; if(en[{offset//2}]) q=bus[{offset}+:2]; end
      assign r=q^1;
    endmodule''')
    compiler = DirectSourceCompiler.for_settled_state(machine)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert compiler._settling_order(roots) is not None
    assert set(compiler._regions()) == roots
    values = {name: 0 for name in _names(machine)}
    _compare(machine, values, None if startup else {'a': 1, 'valid': 1},
        startup=startup, symbolic_state=tuple(values),
        symbolic_pins=() if startup else ('a', 'valid'), settled_state=True)


def test_settled_state_view_cannot_claim_source_events(tmp_path):
    machine, _ = _lower(tmp_path, 'module M(input a,output q); assign q=a; endmodule')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, dict(a=0, q=0)), goals)
    pins = _pins(machine, env, {_names(machine)['a']: 1})
    direct = replace(relation, direct=DirectSourceCompiler.for_settled_state(machine))
    with pytest.raises(ValueError, match='source-event'):
        direct.encode(state, pins, event_bound=machine.event_bound)


def test_empty_fixed_point_keeps_private_context_with_array_first(tmp_path):
    machine, _ = _lower(tmp_path, 'module M(input clk,a,output reg q); always @(posedge clk) q<=a; endmodule')
    _, state, _ = _relation(machine, _state(machine, dict(clk=0, a=0, q=0)), ())
    context = z3.Context()
    state = {'array': [z3.BitVec('cell', 2, ctx=context)],
             **{name: value.translate(context) for name, value in state.items()}}
    compiler = DirectSourceCompiler.for_settled_state(machine)
    assert not compiler._regions()
    condition = compiler.boundary_condition(state)
    assert z3.is_true(condition) and condition.ctx == context


@pytest.mark.parametrize('regions', [2, 12])
@pytest.mark.parametrize('private_context', [False, True])
def test_fixed_points_compile_only_used_regions_and_grow_exactly(
        tmp_path, monkeypatch, regions, private_context):
    from behave.sim.symbolic.step_template import StepTemplate

    outputs = [f'y{i}' for i in range(regions)]
    machine, _ = _lower(tmp_path, 'module M(input [3:0] a,output [3:0] '
        + ','.join(outputs) + ');'
        + ''.join(f"assign {name}=a ^ 4'd{i};" for i, name in enumerate(outputs))
        + 'endmodule')
    names = _names(machine)
    _, state, _ = _relation(machine, _state(machine, dict.fromkeys(names, 0)), ())
    context = z3.Context() if private_context else None
    state = {name: z3.BitVec('cut_' + name, value.size(), ctx=context)
             for name, value in state.items()}
    compiler = DirectSourceCompiler.for_settled_state(machine)
    assert len(compiler._regions()) == regions
    constructed = []
    initialize = StepTemplate.__init__

    def counted(self, procedure, environment, **kwargs):
        constructed.append(procedure)
        initialize(self, procedure, environment, **kwargs)

    monkeypatch.setattr(StepTemplate, '__init__', counted)
    all_outputs = {names[name] for name in outputs}
    seen = set()
    for selected in ((), (0,), (0,), (1,), tuple(range(regions)), (0,)):
        seen.update(selected)
        kept = {names[outputs[i]] for i in selected}
        cut = {name: value for name, value in state.items()
               if name not in all_outputs or name in kept}
        predicate = compiler.boundary_condition(cut)
        expected = z3.And(*[
            state[names[outputs[i]]] == z3.ZeroExt(
                state[names[outputs[i]]].size() - 4,
                z3.Extract(3, 0, state[names['a']]) ^ z3.BitVecVal(i, 4, ctx=context))
            for i in selected]) if selected else z3.BoolVal(True, ctx=context)
        solver = z3.Solver(ctx=context)
        solver.add(predicate != expected)
        assert solver.check() == z3.unsat
        assert len(constructed) == len(seen)
        if len(selected) == regions:
            full_predicate = predicate
    assert len(constructed) == regions
    assert len({id(procedure) for procedure in constructed}) == regions
    # A compiler can move to a fresh query context without recompiling source
    # or retaining a template per historical context.
    other_context = z3.Context() if context is None else z3.main_ctx()
    moved = {name: value.translate(other_context) for name, value in state.items()}
    predicate = compiler.boundary_condition(moved)
    solver = z3.Solver(ctx=other_context)
    solver.add(predicate != full_predicate.translate(other_context))
    assert solver.check() == z3.unsat
    assert len(constructed) == regions


@pytest.mark.parametrize('assignment', ['=', '<='])
def test_single_process_keeps_transient_writes_and_nba_commit(tmp_path, assignment):
    source = f'''module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) begin
        q {assignment} a;
        if(a==1) q {assignment} 2;
        q {assignment} 0;
      end
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    _compare(machine, {'clk': 0, 'a': 0, 'q': 3}, {'clk': 1, 'a': 1})


def test_coactive_nba_processes_read_old_registers(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,input [3:0] a,output reg [3:0] q,r);
      always @(posedge clk) q<=a;
      always @(posedge clk) r<=q;
    endmodule''')
    _compare(machine, {'clk': 0, 'a': 0, 'q': 3, 'r': 0}, {'clk': 1, 'a': 5})


@pytest.mark.parametrize('active_low', [False, True])
@pytest.mark.parametrize('symbolic_reset', [False, True])
@pytest.mark.parametrize('reverse', [False, True])
def test_async_edge_excludes_inactive_datapath_dependencies(tmp_path, active_low, symbolic_reset, reverse):
    edge, guard = ('negedge', '!rst') if active_low else ('posedge', 'rst')
    statements = ['always_comb r=a;',
                  f'always @(posedge clk or {edge} rst) if({guard}) q<=0; else q<=r;']
    if reverse:
        statements.reverse()
    machine, _ = _lower(tmp_path, '''module M(input clk,rst,input [1:0] a,output reg [1:0] q);
      reg [1:0] r;
    ''' + '\n'.join(statements) + ' endmodule')
    # clk cannot rise in this phase. Even a SYMBOLIC reset can wake the FF
    # only at its asserted level. Its ordinary data branch cannot race r=a.
    _compare(machine, {'clk': 0, 'rst': int(active_low), 'a': 0, 'r': 0, 'q': 0},
             {'clk': 0, 'rst': int(not active_low), 'a': 1},
             symbolic_state=('a', 'r', 'q'), symbolic_pins=('rst',) if symbolic_reset else ())


def test_held_symbolic_pin_wakes_no_subscriber_without_simplification(tmp_path, monkeypatch):
    machine, _ = _lower(tmp_path, '''module M(input clk,input [1:0] a,output reg [1:0] q);
      reg [1:0] r;
      always_comb r=a;
      always @(posedge clk) q<=r;
    endmodule''')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    _, state, env = _relation(machine, _state(machine, {'clk': 0, 'a': 0, 'r': 0, 'q': 0}), goals)
    names = _names(machine)
    pins = _pins(machine, env, {names['clk']: 0, names['a']: 0})
    kind = machine.program.kinds[names['a']]
    extend = z3.SignExt if kind.signed else z3.ZeroExt
    pins[names['a']] = extend(pins[names['a']].size() - kind.width, z3.BitVec('held_a', kind.width))
    monkeypatch.setattr(z3, 'simplify', lambda *args, **kwargs: pytest.fail('a held pin term cannot change'))
    assert DirectSourceCompiler(machine)._driven_events(state, pins, dict(pins)) == ()


@pytest.mark.parametrize('sensitivity', ['posedge clk or posedge rst', 'posedge clk or rst'])
def test_alternative_trigger_cannot_assume_reset_branch(tmp_path, sensitivity):
    machine, _ = _lower(tmp_path, f'''module M(input clk,rst,a,output reg q);
      wire r; assign r=a;
      always @({sensitivity}) if(rst) q<=0; else q<=r;
    endmodule''')
    # A clock edge (or a falling change of an unqualified reset event) can
    # execute the ordinary branch concurrently with the combinational write.
    rising_clock = 'posedge rst' in sensitivity
    with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
        _compare(machine, {'clk': 0, 'rst': int(not rising_clock), 'a': 0, 'r': 0, 'q': 0},
                 {'clk': int(rising_clock), 'rst': 0, 'a': 1})


def test_held_control_plan_cache_cannot_authorize_another_branch(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,rst,a,output reg q);
      wire r; assign r=a;
      always @(posedge clk or posedge rst) if(rst) q<=0; else q<=r;
    endmodule''')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine,
        _state(machine, {'clk': 0, 'rst': 0, 'a': 0, 'r': 0, 'q': 0}), goals)
    compiler = DirectSourceCompiler(machine)
    direct = replace(relation, direct=compiler)
    pins = _pins(machine, env, {_names(machine)[name]: value for name, value in
                               {'clk': 1, 'rst': 1, 'a': 1}.items()})
    direct.encode(state, pins, event_bound=machine.event_bound)
    changed = {**pins, _names(machine)['rst']: z3.BitVecVal(0, pins[_names(machine)['rst']].size())}
    assert compiler.roots(state, pins) == compiler.roots(state, changed)
    with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
        direct.encode(state, changed, event_bound=machine.event_bound)


@pytest.mark.parametrize('nonblocking', [False, True])
@pytest.mark.parametrize('first_reset', [False, True])
def test_distinct_activation_facts_share_only_identical_compiled_plans(tmp_path, nonblocking, first_reset):
    assign = '<=' if nonblocking else '='
    machine, _ = _lower(tmp_path, f'''module M(input clk,rst,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) if(rst) q {assign} 0; else q {assign} a;
    endmodule''')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine,
        _state(machine, {'clk': 0, 'rst': 0, 'a': 0, 'q': 0}), goals)
    compiler = DirectSourceCompiler(machine)
    direct = replace(relation, direct=compiler)
    names = _names(machine)
    facts = []
    for reset in (first_reset, not first_reset):
        pins = _pins(machine, env, {names[k]: v for k, v in
                                  {'clk': 1, 'rst': int(reset), 'a': 3}.items()})
        events = compiler._driven_events(state, pins, None)
        facts.append(compiler._phase_effects(pins, events))
        old = relation.encode(state, pins, event_bound=machine.event_bound)
        new = direct.encode(state, pins, event_bound=machine.event_bound)
        solver = z3.Solver()
        solver.add(*old.constraints, *new.constraints)
        assert solver.check() == z3.sat
        solver.add(z3.Or(old.alive != new.alive, old.error != new.error,
            old.quiescent != new.quiescent,
            new.state[names['q']] != (0 if reset else 3),
            *(old.state[name] != new.state[name] for name in machine.program.fields),
            *(old.hits[goal] != new.hits[goal] for goal in goals)))
        assert solver.check() == z3.unsat
    assert facts[0] != facts[1]
    assert len(compiler._templates) == 1


@pytest.mark.parametrize('nonblocking', [False, True])
@pytest.mark.parametrize('value', [0, 3, 7])
def test_static_drive_certificate_does_not_repeat_contextual_datapath_analysis(tmp_path, monkeypatch, nonblocking, value):
    assign = '<=' if nonblocking else '='
    machine, _ = _lower(tmp_path, f'''module M(input clk,rst,input [2:0] a,
        output reg [2:0] q); always @(posedge clk)
        if(rst) q {assign} 0; else q {assign} a*a+1'b1; endmodule''')

    def unused(*args):
        pytest.fail('a certified unconditional schedule needs no held-input effect traversal')

    monkeypatch.setattr(DirectSourceCompiler, '_phase_effects', unused)
    _compare(machine, {'clk': 0, 'rst': 0, 'a': 0, 'q': 0},
             {'clk': 1, 'rst': 0, 'a': value}, symbolic_state=('q',))


def test_conditional_effects_keep_unknown_guards_and_live_failures():
    from behave.sim.symbolic import ir, ranges
    from behave.sim.rtl.source_effects import conditional_accesses

    body = ir.If(ir.Var('rst'), ir.Assign('q', ir.Lit(0)),
                 ir.Seq(ir.Assign('q', ir.Var('data')), ir.Fail('bad')))
    storage = {'rst', 'q', 'data'}
    assert conditional_accesses(body, storage, {'rst': ranges.Range(1, 1)}) == (
        frozenset({'rst'}), frozenset({'q'}), False)
    assert conditional_accesses(body, storage, {'rst': ranges.Range(0, 1)}) == (
        frozenset({'rst', 'data'}), frozenset({'q'}), True)


def test_combination_chain_runs_to_quiescence_not_one_activation(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input [3:0] a,output reg [3:0] q);
      logic [3:0] b;
      always_comb if(b==5) q=9; else q=0;
      always_comb b=a;
    endmodule''')
    _compare(machine, {'a': 0, 'b': 0, 'q': 0}, {'a': 5})
    # At startup BOTH processes wake. An order can observe the old b first;
    # accepting the settled q while dropping that source event is unsound.
    with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
        _compare(machine, {'a': 5, 'b': 0, 'q': 0}, None, startup=True)


@pytest.mark.parametrize('same_values', [False, True])
@pytest.mark.parametrize('reverse', [False, True])
def test_unconditional_startup_dag_keeps_values_and_all_events(tmp_path, same_values, reverse):
    assignments = ['always_comb q=b^c;', 'always_comb b=a;', 'always_comb c=a^1;']
    if reverse:
        assignments.reverse()
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,output reg [1:0] q);
      logic [1:0] b,c;
    ''' + '\n'.join(assignments) + ' endmodule')
    _compare(machine, {'a': 2, 'b': 2 if same_values else 0,
                      'c': 3 if same_values else 0, 'q': 1 if same_values else 0},
             None, startup=True)


def test_initial_constant_and_combinational_reader_have_exact_startup(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input a,output y);
      logic q; initial q=0; assign y=a^q;
    endmodule''', initialization=True)
    _compare(machine, {'a': 1, 'q': 1, 'y': 0}, None, startup=True)


def test_startup_dag_equivalence_for_every_initial_value_and_order(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input [3:0] a,output reg [3:0] q);
      logic [3:0] b,c;
      always_comb q=b^c;
      always_comb c=a^1;
      always_comb b=a;
    endmodule''')
    _compare(machine, {'a': 0, 'b': 0, 'c': 0, 'q': 0}, None, startup=True,
             symbolic_state=('a', 'b', 'c', 'q'))


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('hierarchical', [False, True])
def test_disjoint_packed_writers_preserve_all_startup_orders(tmp_path, reverse, hierarchical):
    # Each packed lane has its own writer; reading the preserved bits in a
    # lowered merge is not a dependence of the bits that writer changes.
    statements = ['assign b[0]=a;', 'assign b[1]=b[0]^a;', 'assign q=b[1];']
    if hierarchical:
        statements = ['Leaf x(a,b[0]);', 'Leaf y(b[0]^a,b[1]);', 'assign q=b[1];']
    if reverse:
        statements.reverse()
    source = ('module M(input a, output q); wire [1:0] b;'
              + '\n'.join(statements) + ' endmodule\n'
              + ('module Leaf(input leaf_input,output q); assign q=leaf_input; endmodule' if hierarchical else ''))
    machine, _ = _lower(tmp_path, source, defer_domains=True)
    values = dict.fromkeys(_names(machine), 0)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert DirectSourceCompiler(machine)._settling_order(roots) is not None
    _compare(machine, values, None, startup=True, symbolic_state=tuple(values))


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('dependent', [False, True])
def test_disjoint_packed_writers_preserve_driven_fixed_points(tmp_path, reverse, dependent):
    statements = ['assign b[0]=a;',
                  'assign b[1]=b[0]^a;' if dependent else 'assign b[1]=a^1;',
                  'assign q=b[1];']
    if reverse:
        statements.reverse()
    machine, _ = _lower(tmp_path, 'module M(input a,output q); wire [1:0] b;'
                        + '\n'.join(statements) + ' endmodule', defer_domains=True)
    values = dict.fromkeys(_names(machine), 0)
    _compare(machine, values, {'a': 1}, symbolic_state=tuple(values),
             symbolic_pins=('a',), settled_state=True)


@pytest.mark.parametrize('descending', [False, True])
@pytest.mark.parametrize('dependent', [False, True])
@pytest.mark.parametrize('startup', [False, True])
def test_static_array_writers_share_the_settled_dag(tmp_path, descending, dependent, startup):
    bounds = '1:0' if descending else '0:1'
    statements = ['assign b[0]=a;',
                  'assign b[1]=b[0]^a;' if dependent else 'assign b[1]=a^1;',
                  'assign q=b[1];']
    if descending:
        statements.reverse()
    machine, _ = _lower(tmp_path, f'module M(input [1:0] a,output [1:0] q); '
                        f'wire [1:0] b[{bounds}];' + '\n'.join(statements) + ' endmodule')
    compiler = DirectSourceCompiler.for_settled_state(machine)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert set(compiler._regions()) == roots
    assert compiler.certifies_fixed_points()
    # A final-value proof must not become a source-event ordering certificate.
    assert DirectSourceCompiler(machine)._settling_order(roots) is None
    values = dict(a=0, b=[0, 0] if dependent else ([1, 0] if descending else [0, 1]),
                  q=0 if dependent else 1)
    _compare(machine, values, None if startup else {'a': 1}, startup=startup,
        symbolic_state=('a',) if startup else (), symbolic_pins=() if startup else ('a',),
        settled_state=True)


@pytest.mark.parametrize('private_context', [False, True])
@pytest.mark.parametrize('compact', [False, True])
def test_static_array_boundary_checks_every_cell_without_repacking_each_writer(
        tmp_path, monkeypatch, private_context, compact):
    from behave.sim.symbolic.functional_array import FunctionalArray

    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,output [1:0] q);
      wire [1:0] b[0:1]; assign b[0]=a; assign b[1]=a^2'd1;
      assign q=b[1]; endmodule''')
    names = _names(machine)
    _, state, _ = _relation(machine, _state(machine, dict(a=0, b=[0, 0], q=0)), ())
    context = z3.Context() if private_context else z3.main_ctx()
    state = {name: value.translate(context) if z3.is_expr(value) and value.ctx != context else value
             for name, value in state.items()}
    a, b0, b1, q = [z3.BitVec(name, 2, ctx=context) for name in ('a', 'b0', 'b1', 'q')]
    state[names['a']] = z3.ZeroExt(1, a)
    state[names['q']] = z3.ZeroExt(1, q)
    cells = [z3.ZeroExt(1, cell) for cell in (b0, b1)]
    state[names['b']] = FunctionalArray.from_cells(cells) if compact else cells
    compiler = DirectSourceCompiler.for_settled_state(machine)
    compiler.boundary_condition(state)  # Warm only the structural templates.
    pack, calls = FunctionalArray.from_cells, []

    def counted(_cls, values):
        calls.append(len(values))
        return pack(values)

    monkeypatch.setattr(FunctionalArray, 'from_cells', classmethod(counted))
    condition = compiler.boundary_condition(state)
    expected = z3.And(b0 == a, b1 == a ^ 1, q == b1)
    solver = z3.Solver(ctx=context)
    solver.add(condition != expected)
    assert solver.check() == z3.unsat
    assert calls == ([] if compact else [2])


def test_multiple_static_cells_in_one_process_union_their_masks(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a, output [1:0] q);
      logic [1:0] b[0:1];
      always_comb begin b[0]=a; b[1]=a^1; end
      assign q=b[0]^b[1]; endmodule''')
    compiler = DirectSourceCompiler.for_settled_state(machine)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert set(compiler._regions()) == roots
    assert compiler.certifies_fixed_points()
    _compare(machine, dict(a=0, b=[0, 1], q=1), {'a': 1}, symbolic_pins=('a',),
             settled_state=True)


@pytest.mark.parametrize('write', [
    'always_comb b[a[0]]=a;',
    'always_comb if(a[0]) b[0]=a;',
    "always_comb begin b[0]=a; if(a==3) b[0]='x; end",
    'assign b[0]=b[1]^a; assign b[1]=b[0];',
])
def test_array_writes_do_not_hide_dynamic_latched_conflicting_or_cyclic_work(tmp_path, write):
    machine, _ = _lower(tmp_path, 'module M(input [1:0] a,output [1:0] q); '
        'logic [1:0] b[0:1]; assign q=b[0];' + write + ' endmodule', defer_domains=True)
    # Cycles may have no finite compiler bound. This tests the structural
    # rejection even if a future provider supplies an exploration horizon.
    compiler = DirectSourceCompiler.for_settled_state(
        replace(machine, event_bound=machine.event_bound or len(machine.actions)))
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert compiler._settling_order(roots) is None


def test_static_array_overlapping_writers_remain_refused(tmp_path):
    with pytest.raises(Refused, match='rtl-source-scheduler-competing-writers'):
        _lower(tmp_path, '''module M(input [1:0] a,output [1:0] q);
          logic [1:0] b[0:1]; assign q=b[0];
          always @(a) b[0]=a; always @(a) b[0]=a^1; endmodule''')


@pytest.mark.parametrize('consumer', [
    'assign b[1]=b[0]^b[1];',  # A real bit-level cycle, not a merge's preserved bits.
    'always @(a) b[1]=b[0];',  # Missing sensitivity to the actual predecessor.
    "assign b[1]=a ? 1'bx : b[0];",  # A live failure must still block reordering.
])
def test_packed_region_keeps_cycles_missing_sensitivity_and_failures(tmp_path, consumer):
    machine, _ = _lower(tmp_path, 'module M(input a,output wire q); logic [1:0] b;'
                        + 'assign b[0]=a; assign q=b[1];' + consumer + ' endmodule',
                        defer_domains=True)
    compiler = DirectSourceCompiler.for_settled_state(machine)
    roots = frozenset(range(len(machine.program.processes)))
    assert compiler._settling_order(roots) is None
    b = _names(machine)['b']
    assert not {i for i, process in enumerate(machine.program.processes)
                if any(name == b and now & 2 for name, now, _ in process.write_masks)} & compiler._regions().keys()


@pytest.mark.parametrize('mux', ['assign q=sel ? b^c : b+c;',
                                 'always_comb if(sel) q=b^c; else q=b+c;'])
@pytest.mark.parametrize('reverse', [False, True])
def test_stable_guard_startup_preserves_all_events_for_every_value(tmp_path, mux, reverse):
    statements = [mux, 'assign b=a;', 'assign c=a^1;']
    if reverse:
        statements.reverse()
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,input sel,output logic [1:0] q);
      wire [1:0] b,c;
    ''' + '\n'.join(statements) + ' endmodule')
    compiler = DirectSourceCompiler(machine)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert compiler._settling_order(roots) is not None
    _compare(machine, {'a': 0, 'sel': 0, 'b': 0, 'c': 0, 'q': 0}, None, startup=True,
             symbolic_state=('a', 'sel', 'b', 'c', 'q'))


@pytest.mark.parametrize('consumer', [
    'always_comb if(b) q=a; else q=0;',
    'always_comb begin logic saved; saved=b; if(saved) q=a; else q=0; end',
    'always_comb if(a) q=b;',
    'always_comb begin q=b; q=0; end',
])
def test_changing_guards_latches_and_transient_writes_are_not_settling_dags(tmp_path, consumer):
    machine, _ = _lower(tmp_path, f'''module M(input a,output reg q);
      wire b; assign b=a; {consumer} endmodule''')
    compiler = DirectSourceCompiler(machine)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert compiler._settling_order(roots) is None


def test_observation_dependencies_keep_saved_values_and_branch_joins():
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_effects import observation_dependencies
    from behave.sim.rtl.source_ir import _sequence

    # Overwriting b does not change the already captured guard.
    saved = _sequence([ir.Assign('saved', ir.Var('b')), ir.Assign('b', ir.Lit(0)),
                      ir.If(ir.Var('saved'), ir.Mark('hit'), ir.Skip())])
    assert observation_dependencies(saved, {'a', 'b'}) == {'b'}
    # A constant in each branch still depends on which branch assigned it.
    joined = _sequence([ir.If(ir.Var('a'), ir.Assign('saved', ir.Lit(0)),
                              ir.Assign('saved', ir.Lit(1))),
                       ir.If(ir.Var('saved'), ir.Mark('hit'), ir.Skip())])
    assert observation_dependencies(joined, {'a', 'b'}) == {'a'}
    missing = ir.If(ir.Var('unbound'), ir.Mark('hit'), ir.Skip())
    assert observation_dependencies(missing, {'a', 'b'}) is None
    assert observation_dependencies(saved, {'a', 'b'}, marks={'hit'}) == {'b'}
    assert observation_dependencies(joined, {'a', 'b'}, marks={'hit'}) == {'a'}
    assert observation_dependencies(missing, {'a', 'b'}, marks=set()) == set()
    # Selecting no mark is not permission to ignore an unsupported effect.
    assert observation_dependencies(ir.Fail('fault'), {'a', 'b'}, marks=set()) is None


def test_single_write_inputs_keep_entry_values_and_branch_controls():
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_effects import single_write_inputs
    from behave.sim.rtl.source_ir import _sequence

    copied = _sequence([ir.Assign('saved', ir.Var('a')), ir.Assign('q', ir.Var('saved'))])
    assert single_write_inputs(copied, {'a', 'saved', 'q'}) == {'saved': {'a'}, 'q': {'a'}}
    old = _sequence([ir.Assign('saved', ir.Var('q')), ir.Assign('q', ir.Var('a')),
                     ir.Assign('r', ir.Var('saved'))])
    assert single_write_inputs(old, {'a', 'saved', 'q', 'r'}) == {
        'saved': {'q'}, 'q': {'a'}, 'r': {'q'}}
    joined = ir.If(ir.Var('a'), ir.Assign('q', ir.Lit(1)), ir.Assign('q', ir.Lit(0)))
    assert single_write_inputs(joined, {'a', 'q'}) == {'q': {'a'}}
    partial = ir.If(ir.Var('a'), ir.Assign('q', ir.Lit(1)), ir.Skip())
    assert single_write_inputs(partial, {'a', 'q'}) == {}
    repeated = _sequence([ir.Assign('q', ir.Var('a')), ir.Assign('q', ir.Lit(0))])
    assert single_write_inputs(repeated, {'a', 'q'}) == {}


def test_total_write_inputs_are_not_notification_or_latch_certificates():
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_effects import total_write_inputs, input_constant_writes
    from behave.sim.rtl.source_ir import _sequence

    storage = {'a', 'q'}
    overwrite = _sequence([ir.Assign('q', ir.Var('a')), ir.Assign('q', ir.Lit(0))])
    assert total_write_inputs(overwrite, storage) == {'q': set()}
    assert input_constant_writes(overwrite, storage, {'a'}) == set()
    latch = ir.If(ir.Var('a'), ir.Assign('q', ir.Lit(0)), ir.Skip())
    assert total_write_inputs(latch, storage) == {}
    saved = _sequence([ir.Assign('old', ir.Var('q')), ir.Assign('q', ir.Lit(0)),
                       ir.Assign('q', ir.Var('old'))])
    assert total_write_inputs(saved, storage) == {'q': {'q'}}


@pytest.mark.parametrize('cells', [1, 2, 7, 10**9])
@pytest.mark.parametrize('reverse', [False, True])
def test_total_array_values_use_one_cell_not_one_command_per_element(cells, reverse):
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_effects import total_write_inputs, single_write_inputs
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_values import ArrayType

    index = ir.Op('sub', ir.Lit(cells - 1), ir.Var('i')) if reverse else ir.Var('i')
    body = ir._sequence([
        ir.Assign('saved', index),
        ir.Assign('discarded', ir.Sel(ir.Var('q'), ir.Var('saved'))),
        ir.Store('q', ir.Var('saved'), ir.Lit(0)),
        ir.For('j', 3, ir.If(ir.Var('a'),
            ir.Store('q', ir.Var('saved'), ir.Op('add', ir.Sel(ir.Var('q'), index), ir.Var('b'))),
            ir.Skip())),
    ])
    loop = ir.For('i', cells, body)
    kinds = {'q': ArrayType(0, cells - 1, IntegralType(8, False))}
    assert total_write_inputs(loop, {'a', 'b', 'q'}, kinds=kinds) == {'q': {'a', 'b'}}
    assert single_write_inputs(loop, {'a', 'b', 'q'}) == {}


@pytest.mark.parametrize('initial', ['partial', 'copy', 'loop', 'conditional', 'saved-old'])
@pytest.mark.parametrize('guarded', [False, True])
@pytest.mark.parametrize('cells', [2, 10**9])
def test_partial_array_store_preserves_totality_and_entry_dependencies(initial, guarded, cells):
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_effects import total_write_inputs, single_write_inputs
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_values import ArrayType

    fill = ir.For('i', cells, ir.Store('q', ir.Var('i'), ir.Lit(0)))
    start = {'partial': ir.Skip(), 'copy': ir.Assign('q', ir.Var('seed')),
             'loop': fill, 'conditional': ir.If(ir.Var('init'), fill, ir.Skip()),
             'saved-old': ir._sequence([ir.Assign('old', ir.Var('q')), fill])}[initial]
    value = ir.Var('data')
    if initial == 'saved-old':
        value = ir.Op('add', value, ir.Sel(ir.Var('old'), ir.Lit(0)))
    store = ir.Store('q', ir.Var('index'), value)
    if guarded:
        store = ir.If(ir.Var('gate'), store, ir.Skip())
    command = ir._sequence([start, store, ir.Assign('out', ir.Sel(ir.Var('q'), ir.Lit(0)))])
    kinds = {'q': ArrayType(0, cells - 1, IntegralType(8, False))}
    expected = {'index', 'data'} | ({'gate'} if guarded else set())
    expected |= {'partial': {'q'}, 'copy': {'seed'}, 'loop': set(),
                 'conditional': {'q', 'init'}, 'saved-old': {'q'}}[initial]
    total = total_write_inputs(command, {'q', 'out'}, kinds=kinds)
    assert total == ({'q': expected, 'out': expected}
                     if initial in {'copy', 'loop', 'saved-old'} else {'out': expected})
    # Total final values cannot certify notification counts or one write per cell.
    assert single_write_inputs(command, {'q', 'out'}) == {}
    overwrite = ir._sequence([command, fill, ir.Assign('out', ir.Sel(ir.Var('q'), ir.Lit(0)))])
    assert total_write_inputs(overwrite, {'q', 'out'}, kinds=kinds) == {'q': set(), 'out': set()}


@pytest.mark.parametrize('loop', [False, True])
@pytest.mark.parametrize('reverse', [False, True])
def test_settling_uses_closed_query_state_not_unobserved_scratch(tmp_path, loop, reverse):
    from types import SimpleNamespace
    from behave.sim.symbolic.encoder import widen
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalTemplate, SourceObservation

    body = ('for(scratch=0;scratch<2;scratch=scratch+1) q=q^d;'
            if loop else 'scratch=d; q=scratch;')
    processes = ['assign unused=~d;', 'assign en=a;',
                 'always @* begin q=0; if(en) begin ' + body + ' end end']
    source = '''module M(input a, input [1:0] d, output logic [1:0] q);
      wire en; wire [1:0] unused; logic [1:0] scratch;
    ''' + '\n'.join(reversed(processes) if reverse else processes) + ' endmodule'
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    original, state, env = _relation(machine, _state(machine,
        dict.fromkeys(names, 0), startup=True), ())
    for name in names.values():
        kind = machine.program.kinds[name]
        state[name] = z3.ZeroExt(state[name].size() - kind.width,
                                 z3.BitVec(f'entry_{name}', kind.width))
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    owner = template.relation(machine, (), state_reads=(names['q'],))
    assert names['scratch'] not in owner.source.state_names
    compiler = DirectSourceCompiler.for_settled_state(machine, observation=owner)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    with pytest.raises(Refused):
        DirectSourceCompiler.for_settled_state(machine).plan(roots, all_active=True)
    assert compiler.plan(roots, all_active=True)
    narrow = replace(owner, direct=compiler)
    old = original.settle(state, event_bound=machine.event_bound, tag='old_start')
    with owner.direct.request_scope(SimpleNamespace(state=state), SourceObservation.GOAL_PREFIX):
        new = owner.settle(state, event_bound=machine.event_bound, tag='new_start')
        assert owner.direct._state_view._state_projection == (owner.source, owner.drive)
    assert len(new.actions) == 1
    solver = z3.Solver()
    solver.add(*old.constraints, *new.constraints)
    differences = []
    for turn in range(3):
        assert set(new.state) == set(owner.source.state_names)
        differences.extend((old.alive != new.alive, old.error != new.error,
                            old.unfinished != new.unfinished))
        for name, value in new.state.items():
            left, right = widen(old.state[name], value) if z3.is_bv(value) else (old.state[name], value)
            differences.append(left != right)
        if turn == 2:
            break
        pins = _pins(machine, env, {names['a']: turn, names['d']: 0})
        pins[names['d']] = z3.BitVec(f'drive_{turn}', pins[names['d']].size())
        old = original.encode(old.state, pins, event_bound=machine.event_bound,
                              alive=old.alive, error=old.error, tag=f'old_{turn}')
        new = narrow.encode(new.state, pins, event_bound=machine.event_bound,
                             alive=new.alive, error=new.error, tag=f'new_{turn}')
        solver.add(*old.constraints, *new.constraints)
    assert solver.check() == z3.sat
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat

    # Asking for the scratch's final value restores the original obligation.
    broad = template.relation(machine, (), state_reads=(names['q'], names['scratch']))
    assert names['scratch'] in broad.source.state_names
    with pytest.raises(Refused):
        DirectSourceCompiler.for_settled_state(machine, observation=broad).plan(roots, all_active=True)
    with pytest.raises(ValueError, match='different observation'):
        replace(broad, direct=compiler).settle(state, event_bound=machine.event_bound)


@pytest.mark.parametrize('use', ['output', 'failure', 'notification', 'source-event'])
def test_query_state_projection_keeps_observable_scratch(tmp_path, use):
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler
    from behave.sim.rtl.source_relation import SourceIntervalTemplate

    consumer = {'output': 'assign r=scratch;',
                'failure': 'always @* if(scratch==3) $fatal(1);',
                'notification': 'wire pulse; assign pulse=scratch[0]; always @(posedge pulse) r=~r;',
                'source-event': ''}[use]
    machine, _ = _lower(tmp_path, '''module M(input a,input [1:0] d,
        output logic [1:0] q,r); wire en; logic [1:0] scratch;
        assign en=a;
        always @* begin q=0; if(en) begin scratch=d; q=scratch; end end
        ''' + consumer + ' endmodule', defer_domains=True)
    names = _names(machine)
    template = SourceIntervalTemplate(machine.procedure,
        {**machine.procedure.state_ranges, **machine.input_ranges},
        direct=NativeCoverSourceCompiler(machine)).normalized()
    goals = tuple({goal for _, _, goal in machine.occurrences}) if use == 'source-event' else ()
    owner = template.relation(machine, goals,
        state_reads=tuple(names[name] for name in ('q', 'r') if name in names))
    if use != 'source-event':
        assert names['scratch'] in owner.source.state_names
        view = DirectSourceCompiler.for_settled_state(machine, observation=owner)
    else:
        view = owner.direct  # Source-event queries never acquire a state-only certificate.
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    with pytest.raises(Refused):
        view.plan(roots, all_active=True)


@pytest.mark.parametrize('variant', ['partial', 'conditional', 'cross_cell', 'wrapped', 'saved_old', 'carried_old'])
def test_array_value_summary_never_erases_real_old_value_dependencies(variant):
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_effects import total_write_inputs
    from behave.sim.rtl.source_integral import IntegralType
    from behave.sim.rtl.source_values import ArrayType

    i = ir.Var('i')
    commands = [ir.Store('q', i, ir.Lit(0))]
    if variant == 'conditional':
        commands = [ir.If(ir.Var('a'), commands[0], ir.Skip())]
    elif variant == 'cross_cell':
        commands.append(ir.Store('q', i, ir.Sel(ir.Var('q'), ir.Op('sub', ir.Lit(2), i))))
    elif variant == 'wrapped':
        commands = [ir.Store('q', ir.Op('uwrap', i, ir.Lit(1)), ir.Lit(0))]
    elif variant == 'saved_old':
        commands = [ir.Assign('saved', ir.Sel(ir.Var('q'), i)), *commands, ir.Store('q', i, ir.Var('saved'))]
    elif variant == 'carried_old':
        commands = [ir.Assign('saved', ir.Sel(ir.Var('q'), i)), *commands,
                    ir.Store('q', i, ir.Var('carry')), ir.Assign('carry', ir.Var('saved'))]
    loop = ir.For('i', 2 if variant == 'partial' else 3, ir._sequence(commands))
    kinds = {'q': ArrayType(0, 2, IntegralType(8, False))}
    got = total_write_inputs(loop, {'q', 'a', 'carry'}, kinds=kinds)
    if variant in {'saved_old', 'carried_old'}:
        assert 'q' in got['q']
    else:
        assert got == {}


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('startup', [False, True])
def test_total_array_initialization_settles_without_erasing_source_events(tmp_path, reverse, startup):
    header = 'i=1;i>=0;i--' if reverse else 'i=0;i<2;i++'
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,input sel,output wire [1:0] q);
      wire [1:0] b; assign b=a;
      logic [1:0] cells[0:1]; integer i;
      always_comb begin for(''' + header + ''') begin
        cells[i]=0; if(sel) cells[i]=b;
      end end
      assign q=cells[0]^cells[1];
    endmodule''')
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    view = DirectSourceCompiler.for_settled_state(machine)
    assert view._settling_order(roots) is not None
    assert DirectSourceCompiler(machine)._settling_order(roots) is None
    values = {name: [0, 0] if name == 'cells' else 0 for name in _names(machine)}
    _compare(machine, values, None if startup else {'a': 1, 'sel': 1},
             startup=startup, symbolic_state=tuple(values),
             symbolic_pins=() if startup else ('a', 'sel'), settled_state=True)


@pytest.mark.parametrize('consumer', ['wire pulse; assign pulse=cells[0]; always @(posedge pulse) q=~q;',
                                    'always_comb q=cells[0];'])
def test_array_final_values_do_not_hide_notification_cycles_or_edges(tmp_path, consumer):
    machine, _ = _lower(tmp_path, '''module M(input a,output logic q);
      logic cells[0:1]; integer i;
      always_comb for(i=0;i<2;i++) begin cells[i]=q; cells[i]=0; end
    ''' + consumer + ' endmodule')
    # Test the direct compiler's DAG certificate even when the ordinary
    # scheduler already withholds a finite bound for this notification cycle.
    view = DirectSourceCompiler.for_settled_state(replace(machine, event_bound=12))
    assert not view._regions()


@pytest.mark.parametrize('trip', [0, 1, 10**9, 'n'])
def test_scalar_loop_value_certificate_keeps_zero_trip_and_old_value_dependencies(trip):
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_effects import total_write_inputs, single_write_inputs, observation_dependencies
    from behave.sim.rtl.source_ir import _sequence

    count = ir.Var(trip) if isinstance(trip, str) else ir.Lit(trip)
    storage = {'a', 'b', 'q', 'n'}
    loop = ir.For('i', count, ir.If(ir.Var('b'), ir.Assign('q', ir.Var('a')), ir.Skip()))
    command = _sequence([ir.Assign('q', ir.Lit(0)), loop])
    inputs = {'a', 'b'} | ({'n'} if trip == 'n' else set())
    assert total_write_inputs(command, storage) == {'q': inputs}
    assert single_write_inputs(command, storage) == {}
    assert observation_dependencies(command, storage, marks=set()) is None
    assert total_write_inputs(loop, storage) == {}  # No guaranteed write.
    saved = _sequence([ir.Assign('old', ir.Var('q')), ir.Assign('q', ir.Lit(0)),
                      ir.For('i', count, ir.Assign('q', ir.Var('old')))])
    assert 'q' in total_write_inputs(saved, storage)['q']


@pytest.mark.parametrize('startup', [False, True])
@pytest.mark.parametrize('storage_index', [False, True])
def test_settled_scalar_storage_loop_uses_value_certificate_not_event_certificate(
        tmp_path, startup, storage_index):
    declare, index = ('integer i;', 'i') if storage_index else ('', 'integer i')
    machine, _ = _lower(tmp_path, f'''module M(input [1:0] a,
        output reg [1:0] q, output wire [1:0] r);
      {declare}
      always_comb begin q=0; for({index}=0;i<3;i=i+1) q=q+a; end
      assign r=q^1;
    endmodule''')
    ordinary = DirectSourceCompiler(machine)
    settled = DirectSourceCompiler.for_settled_state(machine)
    roots = frozenset(i for i, active in enumerate(machine.startup) if active)
    assert settled._settling_order(roots) is not None
    assert set(settled._regions()) == roots
    assert any(ordinary._settling_reads(i) is None for i in roots)
    values = {name: 0 for name in _names(machine)}
    _compare(machine, values, None if startup else {'a': 1},
             startup=startup, symbolic_state=tuple(values),
             symbolic_pins=() if startup else ('a',), settled_state=True)


def test_scalar_storage_loops_do_not_hide_notification_cycles(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(output logic q,r);
      always @(r) begin q=0; for(integer i=0;i<2;i++) q=i[0]; q=0; end
      always @(q) begin r=0; for(integer i=0;i<2;i++) r=i[0]; r=0; end
    endmodule''')
    view = DirectSourceCompiler.for_settled_state(
        replace(machine, event_bound=len(machine.actions)))
    roots = frozenset(range(len(machine.program.processes)))
    assert view._settling_order(roots) is None


def test_scalar_storage_loop_keeps_live_failures_out_of_fixed_points(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,output logic [1:0] q);
      always_comb begin q=0; for(integer i=0;i<3;i++) q=q+a;
        if(a==3) q='x;
      end
    endmodule''', defer_domains=True)
    view = DirectSourceCompiler.for_settled_state(machine)
    assert not view._regions()


@pytest.mark.parametrize('trip', [0, 1, 26, 10**9, 'n'])
def test_local_helper_loops_keep_entry_and_control_dependencies(trip):
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_effects import single_write_inputs, observation_dependencies
    from behave.sim.rtl.source_ir import _sequence

    count = ir.Var(trip) if isinstance(trip, str) else ir.Lit(trip)
    loop = ir.For('i', count, _sequence([
        ir.Assign('scratch', ir.Var('b')),
        ir.If(ir.Var('scratch'), ir.Assign('acc', ir.Var('scratch')), ir.Skip()),
        ir.If(ir.Var('acc'), ir.Mark('hit'), ir.Skip()),
    ]))
    command = _sequence([ir.Assign('acc', ir.Var('a')), loop,
                         ir.Assign('q', ir.Var('acc'))])
    expected = {'a', 'b'} | ({'n'} if trip == 'n' else set())
    storage = {'a', 'b', 'n', 'q'}
    assert single_write_inputs(command, storage) == {'q': expected}
    assert observation_dependencies(command, storage) == expected
    assert observation_dependencies(command, storage, marks=set()) == set()


def test_local_helper_loops_do_not_hide_unbound_or_physical_state():
    from behave.sim.symbolic import ir
    from behave.sim.rtl.source_effects import single_write_inputs, observation_dependencies
    from behave.sim.rtl.source_ir import _sequence

    storage = {'a', 'q'}
    # A branch-only initialization is not a definite write. The next mark
    # must retain the missing entry value, even inside a local loop.
    body = _sequence([ir.If(ir.Var('a'), ir.Assign('tmp', ir.Lit(1)), ir.Skip()),
                      ir.If(ir.Var('tmp'), ir.Mark('hit'), ir.Skip())])
    assert observation_dependencies(ir.For('i', 4, body), storage) is None
    for body in (ir.Assign('q', ir.Var('a')), ir.Store('local', ir.Lit(0), ir.Var('a'))):
        loop = ir.For('i', 4, body)
        command = _sequence([loop, ir.Assign('q', ir.Var('a'))])
        assert single_write_inputs(command, storage) == {}
        assert observation_dependencies(command, storage, marks=set()) is None
    # A zero-trip nested loop cannot initialize the value used afterward.
    nested = ir.For('i', 4, _sequence([
        ir.For('j', ir.Var('a'), ir.Assign('tmp', ir.Lit(1))),
        ir.If(ir.Var('tmp'), ir.Mark('hit'), ir.Skip())]))
    assert observation_dependencies(nested, storage) is None
    assert single_write_inputs(ir.For('q', 4, ir.Skip()), storage) == {}


def test_local_helper_loop_all_orders_keep_values_and_source_hits(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,output wire [1:0] q);
      function automatic [1:0] f(input [1:0] x);
        reg [1:0] acc;
        begin
          acc=x;
          for(integer i=0;i<3;i=i+1) begin
            if(acc[0]) acc=acc+1; else acc=acc+2;
          end
          f=acc;
        end
      endfunction
      wire [1:0] b; assign b=f(a); assign q=b;
    endmodule''')
    values = {name: 0 for name in _names(machine)}
    _compare(machine, values, None, startup=True, symbolic_state=tuple(values))


@pytest.mark.parametrize('reverse', [False, True])
def test_assigned_temporary_does_not_hide_source_events_or_create_self_cycle(tmp_path, reverse):
    parts = ['assign b=a;', 'always_comb begin logic chosen; chosen=sel; if(chosen) q=b; else q=0; end']
    if reverse:
        parts.reverse()
    machine, _ = _lower(tmp_path, '''module M(input sel,input [1:0] a,output logic [1:0] q);
      wire [1:0] b;
    ''' + '\n'.join(parts) + ' endmodule')
    # Unlike a changing guard, this guard only reads a held input. Both the
    # final state and every source mark must agree for every activation order.
    values = {name: 0 for name in _names(machine)}
    _compare(machine, values, None, startup=True, symbolic_state=tuple(values))


def test_private_initialization_follows_the_certified_dag(tmp_path):
    from types import SimpleNamespace

    machine, _ = _lower(tmp_path, '''module M(input a,output y);
      wire b,c; assign y=b^c; assign c=b^1; assign b=a;
    endmodule''')
    compiler = DirectSourceCompiler(machine)
    # All internal/output nets are private; only the physical input is bound.
    compiler.check_initial_layout(SimpleNamespace(widths={}))
    _compare(machine, {'a': 0, 'b': 0, 'c': 0, 'y': 0}, None, startup=True,
             symbolic_state=('a', 'b', 'c', 'y'))


def test_conditional_notification_cannot_initialize_a_private_value(tmp_path):
    from types import SimpleNamespace

    machine, _ = _lower(tmp_path, '''module M(output reg q);
      logic b; initial b=1; always @(posedge b) q=1;
    endmodule''', initialization=True)
    # With initial b=1 there is no edge, so q remains its private old value.
    with pytest.raises(Refused, match='rtl-direct-private-initialization-unsupported'):
        DirectSourceCompiler(machine).check_initial_layout(SimpleNamespace(widths={}))


@pytest.mark.parametrize('consumer', ['initial q=b;', 'always @(posedge b) q=a;'])
def test_startup_dag_cannot_certify_once_only_or_edge_readers(tmp_path, consumer):
    machine, _ = _lower(tmp_path, f'''module M(input a,output reg q);
      logic b; initial b=1; {consumer}
    endmodule''', initialization=True)
    compiler = DirectSourceCompiler(machine)
    roots = [i for i, active in enumerate(machine.startup) if active]
    assert compiler._settling_order(frozenset(roots)) is None
    if consumer.startswith('initial'):
        with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
            compiler.plan(roots, all_active=True)
    else:
        # One initial writer followed by its edge consumer has no competing
        # action. The original event-sensitive plan remains exact.
        _compare(machine, {'a': 1, 'b': 0, 'q': 0}, None, startup=True)


def test_startup_certificate_does_not_assume_symbolic_pending_is_true(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input a,output reg q);
      logic b; always_comb q=b; always_comb b=a;
    endmodule''')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, _ = _relation(machine,
        _state(machine, {'a': 1, 'b': 0, 'q': 0}, startup=True), goals)
    compiler = DirectSourceCompiler(machine)
    roots = tuple(range(len(machine.pending)))
    # Populate the stronger plan first: its cache must not leak that premise.
    assert compiler.plan(roots, all_active=True)
    state = {**state, machine.pending[0]: z3.If(z3.Bool('pending'), 1, 0)}
    with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
        replace(relation, direct=compiler).settle(state, event_bound=machine.event_bound)


def test_startup_cyclic_values_retain_the_scheduler_requirement(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(output reg q);
      logic b; always_comb q=b; always_comb b=q;
    endmodule''')
    with pytest.raises(Refused, match='rtl-direct-(coactive-dependency|event-regions-unsupported)'):
        _compare(machine, {'b': 0, 'q': 0}, None, startup=True)


def test_nba_commit_wakes_combinational_consumer(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,input [1:0] a,output reg [1:0] q,r);
      always @(posedge clk) q<=a;
      always_comb if(q==1) r=3; else r=0;
    endmodule''')
    _compare(machine, {'clk': 0, 'a': 0, 'q': 0, 'r': 0}, {'clk': 1, 'a': 1})


def test_blocking_race_is_refused_even_if_final_outputs_agree(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,input [1:0] d,output reg [1:0] q,r);
      always @(posedge clk) q=d;
      always @(posedge clk) begin if(q==1) r=1; else r=2; r=0; end
    endmodule''')
    with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
        _compare(machine, {'clk': 0, 'd': 0, 'q': 0, 'r': 0}, {'clk': 1, 'd': 1})


def test_bounded_index_guards_do_not_make_independent_processes_race(tmp_path, monkeypatch):
    machine, _ = _lower(tmp_path, '''module M(input [3:0] a,input b,output reg [3:0] q,output r);
      always_comb begin
        q=0;
        for(integer i=3;i>=0;i--) q[i]=a[i];
      end
      assign r=b;
    endmodule''', defer_domains=True)
    # Lowering may already discharge the bounded index checks. Either way,
    # independent activations must stay composable without repeated analysis.
    compiler = DirectSourceCompiler(machine)
    assert compiler._may_fail(0) is False

    def forbidden(*args, **kwargs):
        pytest.fail('a second interval must reuse the local failure classification')

    from behave.sim.symbolic import ranges
    with monkeypatch.context() as patch:
        patch.setattr(ranges, 'walk_post', forbidden)
        assert compiler._may_fail(0) is False
        assert compiler.plan((0, 1))
    _compare(machine, {'a': 0, 'b': 0, 'q': 0, 'r': 0}, None, startup=True,
             symbolic_state=('a', 'b', 'q', 'r'))


def test_live_index_failure_still_blocks_coactive_processes(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input [3:0] a,input [2:0] idx,input b,
      output reg q,output r);
      always_comb q=a[idx];
      assign r=b;
    endmodule''', defer_domains=True)
    compiler = DirectSourceCompiler(machine)
    assert compiler._may_fail(0) is True
    with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
        compiler.plan((0, 1), all_active=True)


@pytest.mark.parametrize('target', [1, 5])
def test_retained_loop_failure_cannot_get_a_failure_free_settling_certificate(tmp_path, target):
    machine, _ = _lower(tmp_path, f'''module M(input a,b,output reg q,output r);
      always_comb begin
        q=a;
        for(integer i=0;i<3;i++) if(i=={target}) $fatal(1);
      end
      assign r=b;
    endmodule''', defer_domains=True)
    compiler = DirectSourceCompiler(machine)
    assert compiler._may_fail(0) is (target == 1)
    if target == 1:
        with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
            compiler.plan((0, 1), all_active=True)
    else:
        assert compiler.plan((0, 1), all_active=True)


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('threshold', [3, 4])
def test_retained_loop_keeps_guarded_index_bounds_at_observation(tmp_path, nested, threshold):
    body = f'''index=a+i; if(index>={threshold}) index=index-3; q[index]=1'b1;'''
    if nested:
        body = 'for(integer j=0;j<2;j++) begin ' + body + ' end'
    machine, _ = _lower(tmp_path, '''module M(input [1:0] a,input b,
      output reg [2:0] q,output r);
      integer index;
      always_comb begin
        q=0;
        for(integer i=0;i<3;i++) begin ''' + body + ''' end
      end
      assign r=b;
    endmodule''', defer_domains=True)
    compiler = DirectSourceCompiler(machine)
    assert compiler._may_fail(0) is (threshold == 4)
    if threshold == 3:
        assert compiler.plan((0, 1), all_active=True)


def test_transitive_competitor_cannot_hide_a_transient_goal(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input a,b,output reg x,y,q);
      always @(a) x=a;
      always @(b) y=b;
      always @(y) begin if(x) q=1; else q=0; end
    endmodule''')
    # The two initially ready writers commute, but y's consumer may observe
    # old x before its writer executes. No whole-history SMT proof is invoked.
    with pytest.raises(Refused, match='rtl-direct-coactive-dependency'):
        _compare(machine, {'a': 0, 'b': 0, 'x': 0, 'y': 0, 'q': 0}, {'a': 1, 'b': 1})


def test_quiet_interval_preserves_state_and_has_no_events(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,input a,output reg q);
      always @(posedge clk) q<=a;
    endmodule''')
    _compare(machine, {'clk': 0, 'a': 1, 'q': 1}, {'clk': 0, 'a': 1})


def test_array_nba_preserves_old_cell_read(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,input a,output reg [1:0] q);
      reg [1:0] mem[0:1];
      always @(posedge clk) begin mem[a]<=3; q<=mem[a]; end
    endmodule''')
    _compare(machine, {'clk': 0, 'a': 0, 'q': 0, 'mem': [1, 2]}, {'clk': 1, 'a': 1})


def test_a_truncated_interval_cannot_silently_become_a_full_step(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,input a,output reg q);
      always @(posedge clk) q<=a;
    endmodule''')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, {'clk': 0, 'a': 1, 'q': 0}), goals)
    direct = replace(relation, direct=DirectSourceCompiler(machine))
    pins = _pins(machine, env, {_names(machine)['clk']: 1, _names(machine)['a']: 1})
    with pytest.raises(Refused, match='rtl-direct-interval-bound-insufficient'):
        direct.encode(state, pins, event_bound=1)


def test_external_drive_cannot_interrupt_pending_startup_work(tmp_path):
    machine, _ = _lower(tmp_path, 'module M(input a,output reg q); always_comb q=a; endmodule')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'q': 0}, startup=True), goals)
    pins = _pins(machine, env, {_names(machine)['a']: 1})
    for path in (relation, replace(relation, direct=DirectSourceCompiler(machine))):
        encoded = path.encode(state, pins, event_bound=machine.event_bound)
        solver = z3.Solver()
        solver.add(*encoded.constraints)
        assert solver.check() == z3.unsat


def test_startup_cannot_discard_an_already_queued_nba(tmp_path):
    machine, _ = _lower(tmp_path, 'module M(input clk,a,output reg q); always @(posedge clk) q<=a; endmodule')
    boundary = _state(machine, {'clk': 0, 'a': 1, 'q': 0})
    boundary[machine.queue_edges[0][0].valid] = True
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, _ = _relation(machine, boundary, goals)
    with pytest.raises(Refused, match='rtl-direct-initial-nba-unsupported'):
        replace(relation, direct=DirectSourceCompiler(machine)).settle(state, event_bound=machine.event_bound)


def test_illegal_drive_cannot_escape_through_a_failing_body(tmp_path):
    from behave.sim.symbolic import ir

    machine, _ = _lower(tmp_path, 'module M(input a,output reg q); always_comb q=a; endmodule')
    guard, drive = machine.actions[0]
    failure = ir.If(ir.Op('ne', ir.Var(dict(machine.drive_inputs)[_names(machine)['a']]), ir.Lit(0)),
                    ir.Fail('SourceDomain'), ir.Skip())
    machine = replace(machine, actions=((guard, ir.Seq(failure, drive)), *machine.actions[1:]))
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'q': 0}, startup=True), goals)
    direct = replace(relation, direct=DirectSourceCompiler(machine))
    pins = _pins(machine, env, {_names(machine)['a']: 1})
    encoded = direct.encode(state, pins, event_bound=machine.event_bound, require_quiescence=False)
    solver = z3.Solver()
    solver.add(*encoded.constraints)
    assert solver.check() == z3.unsat
