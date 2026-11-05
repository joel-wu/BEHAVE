"""Compiler-derived event bounds preserve all legal histories and query inputs."""

from itertools import product

import pytest
import z3

from behave.sim.symbolic import exact_solve
from behave.sim.symbolic import interp, ranges
from behave.sim.symbolic.uniform_array import UniformArray
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_relation import SourceIntervalRelation, SourceIntervalTemplate
from behave.sim.rtl.source_scheduler import SourceScheduler, _event_bound

from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _state

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


def test_dependency_bound_keeps_separate_blocking_and_nba_wakeups():
    graph = ((), ((0, 2),), ((0, 1), (1, 1)))
    assert _event_bound(graph, (True, False, True), {0}) == 11
    assert _event_bound(graph, (True, False, True), set()) == 1
    assert _event_bound(graph, (True, False, True), {2}) == 3
    assert _event_bound((((1, 1),), ((0, 1),)), (False, False), {0}) is None


def test_phase_bounds_use_both_clock_edges_and_never_grow_the_given_domain(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,input [1:0] a,output reg [1:0] q,r);
      always @(posedge clk) q<=a;
      always @(negedge clk) r=q;
    endmodule''')
    names = _names(machine)
    state = _state(machine, {'clk': 0, 'a': 0, 'q': 0, 'r': 0})
    low = {names['clk']: 0, names['a']: 0}
    phases = (low, {**low, names['clk']: 1}, low)
    assert machine.phase_event_bounds(state, phases, {names['a']}, 20) == (1, 3, 2)
    assert machine.phase_event_bounds(state, phases, {names['a']}, 2) == (1, 2, 2)
    assert machine.phase_event_bounds(state, phases, {names['a']}, 1) == (1, 1, 1)
    # A mutable clock may wake either edge initially, then stays the same
    # solver value, regardless of the recorded clock images in later phases.
    assert machine.phase_event_bounds(state, phases, {names['clk'], names['a']}, 20) == (4, 1, 1)


@pytest.mark.parametrize('declaration,initial,unread,read', [
    ('input [3:0] a', 0, 8, 1),
    ('input signed [3:0] a', 0, -8, -7),
    ('input [1:0] a[1:0]', [0, 0], [0, 3], [1, 0]),
])
def test_compiler_read_masks_agree_with_scalar_signed_and_array_packing(
        tmp_path, declaration, initial, unread, read):
    selected = 'a[1][0]' if '[1:0]' in declaration.split('a')[-1] else 'a[0]'
    machine, _ = _lower(tmp_path, f'''module M({declaration},output reg q);
      always_comb q={selected}; endmodule''')
    names = _names(machine)
    state = _state(machine, {'a': initial, 'q': 0})
    pins = lambda value: {names['a']: value}
    assert machine.phase_event_bounds(state, (pins(unread),), (), 10) == (1,)
    assert machine.phase_event_bounds(state, (pins(read),), (), 10) == (2,)
    assert machine.phase_event_bounds(state, (pins(initial), pins(read)), {names['a']}, 10) == (2, 1)


@pytest.mark.parametrize('stored', [0, 3])
def test_quiet_drive_keeps_state_observed_only_by_a_later_clock_phase(tmp_path, stored):
    source = '''module M(input clk,input [1:0] a,output reg [1:0] q,r);
      always @(posedge clk) if(q==3 && a==2) r=1; else r=0;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {'clk': 0, 'a': 0, 'q': stored, 'r': 0})
    low = {names['clk']: 0, names['a']: 0}
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    # The drive itself has no hit and does not read q. Its successor must still
    # carry q into the later activation; a separate per-phase dependency slice
    # must not replace that recorded value with a fresh symbolic state.
    result = query_source_interval(machine, env, state, low,
        (_goal(source, plan, 'q==3 && a==2'),), mutable_inputs=(names['a'],),
        following_inputs=({**low, names['clk']: 1},), event_bound=machine.event_bound,
        timeout_ms=10_000)
    assert result.outcome is (probing.Outcome.SAT if stored == 3 else probing.Outcome.LOCAL_UNSAT)
    if stored == 3:
        assert result.candidate[names['a']] == 2


CASES = [
    ('always_comb q=a; always @(q) if(q==3) r=1; else r=0;', {}, 'q==3'),
    ('always @(posedge clk) q<=a; always @(negedge clk) if(a==q) r=1; else r=0;', {}, 'a==q'),
    ('always @(posedge clk) q=a; always @(posedge clk) if(q==a) r=1; else r=0;', {}, 'q==a'),
    (('reg pulse; always @(posedge clk) begin pulse<=a[0]; pulse<=0; end '
      'always @(posedge pulse) if(a==3) q=1; else q=0; '
      'always @(negedge clk) r=q;'), {'pulse': 0}, 'a==3'),
    (('always @(posedge clk) begin if(a==3) $fatal(1,"stop"); q<=a; end '
      'always @(negedge clk) r=q;'), {}, 'a==3'),
    (('reg [1:0] x; always_comb begin x=a; if(r==3) q=1; else q=0; end '
      'always_comb r=x;'), {'x': 0}, 'r==3'),
]


def _freeze(value):
    if type(value) is UniformArray:
        return _freeze(value.materialize())
    if isinstance(value, dict):
        return tuple((key, _freeze(child)) for key, child in sorted(value.items()))
    if isinstance(value, (tuple, list)):
        return tuple(map(_freeze, value))
    return value


def _histories(machine, state, phases, bounds):
    """Independent finite interpreter oracle, including every accepted action."""
    frontier = [(state, frozenset(), False)]
    for pins, count in zip(phases, bounds, strict=True):
        for offset in range(count):
            following = {}
            for current, hits, failed in frontier:
                if failed:
                    following[_freeze((current, tuple(sorted(hits)), failed))] = (current, hits, failed)
                    continue
                choices = (0,) if offset == 0 else range(machine.commit_action + 1)
                for action in choices:
                    inputs = {driven: (pins[name] if offset == 0 else current[name])
                              for name, driven in machine.drive_inputs}
                    post, outputs, marks, error = interp.run_step(
                        machine.procedure, {**inputs, 'action': action}, current)
                    if not error and outputs.transactions[0]['accepted'] != 1:
                        continue
                    if not error and offset == count - 1 and outputs.transactions[0]['quiescent'] != 1:
                        continue
                    # After a terminal failure the source query observes its
                    # prefix marks, not an unpublished partial successor state.
                    row = ({} if error else post, hits | frozenset(marks), bool(error))
                    following[_freeze((row[0], tuple(sorted(row[1])), row[2]))] = row
            frontier = list(following.values())
    return {_freeze((state, tuple(sorted(hits)), failed)) for state, hits, failed in frontier}


@pytest.mark.parametrize('body,extra,target', CASES)
def test_compressed_and_full_histories_match_all_small_inputs(tmp_path, body, extra, target):
    source = f'module M(input clk,input [1:0] a,output reg [1:0] q,r); {body} endmodule'
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    for previous_a, next_a in product(range(4), repeat=2):
        state = _state(machine, {'clk': 0, 'a': previous_a, 'q': 0, 'r': 0, **extra})
        low = {names['clk']: 0, names['a']: previous_a}
        recorded = (low, {**low, names['clk']: 1}, low)
        bounds = machine.phase_event_bounds(state, recorded, {names['a']}, machine.event_bound)
        assert sum(bounds) < 3 * machine.event_bound
        concrete = tuple({**pins, names['a']: next_a} for pins in recorded)
        assert _histories(machine, state, concrete, bounds) == _histories(
            machine, state, concrete, (machine.event_bound,) * 3)


@pytest.mark.parametrize('body,extra,target', CASES)
@pytest.mark.parametrize('limit', [2, 12])
def test_full_and_compressed_symbolic_input_languages_match(tmp_path, monkeypatch, body, extra, target, limit):
    source = f'module M(input clk,input [1:0] a,output reg [1:0] q,r); {body} endmodule'
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {'clk': 0, 'a': 0, 'q': 0, 'r': 0, **extra})
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    goal = _goal(source, plan, target)
    low = {names['clk']: 0, names['a']: 0}
    original_encode, original_check = SourceIntervalRelation.encode_steps, exact_solve.z3_check_qfbv
    languages = []

    for full, specialized in product((True, False), repeat=2):
        # Each ablation owns its projection; changing the template's drive
        # after selection must not silently reuse the preceding cached cone.
        template = SourceIntervalTemplate(machine.procedure, env)
        first = []

        def encode(relation, *args, _first=first, **kwargs):
            encoded = yield from original_encode(relation, *args, **kwargs)
            if not _first:
                _first.append(encoded)
            return encoded

        def check(assertions, *args, _first=first, **kwargs):
            solver = z3.Solver(ctx=assertions[0].ctx)
            solver.add(*assertions)
            language = set()
            for value in range(4):
                solver.push()
                solver.add(_first[0].inputs[names['a']] == value)
                answer = solver.check()
                assert answer in (z3.sat, z3.unsat)
                if answer == z3.sat:
                    language.add(value)
                solver.pop()
            languages.append(language)
            return original_check(assertions, *args, **kwargs)

        with monkeypatch.context() as patch:
            if not specialized:
                # Force the original kernel through the lazy drive's backing
                # slot; the read-only property is not an ablation control.
                patch.setattr(template, '_drive', template.source)
            if full:
                patch.setattr(SourceScheduler, 'phase_event_bounds',
                              lambda self, state, phases, mutable, limit: (limit,) * len(phases))
            patch.setattr(SourceIntervalRelation, 'encode_steps', encode)
            patch.setattr(exact_solve, 'z3_check_qfbv', check)
            answer = query_source_interval(machine, env, state, low, (goal,),
                mutable_inputs=(names['a'],), following_inputs=({**low, names['clk']: 1}, low),
                event_bound=limit, _relation=template.relation(machine, (goal,)),
                timeout_ms=10_000)
            assert answer.outcome in (probing.Outcome.SAT, probing.Outcome.LOCAL_UNSAT), answer.detail
    marks = {mark for mark, _, key in machine.occurrences if key == goal}
    expected = set()
    for value in range(4):
        phases = tuple({names['clk']: clock, names['a']: value} for clock in (0, 1, 0))
        if any(marks.intersection(reached) for _, reached, _ in
               _histories(machine, state, phases, (limit,)*3)):
            expected.add(value)
    assert len(languages) == 4 and all(language == expected for language in languages)
