"""Deferred call fragments, not yet admission of the report scheduler.

Use original compiler-bound actions and the shared IR interpreter/encoder.
These tests deliberately change source storage between encounter and dispatch.
They do not fake report maturity or label this a public search acceptance gate.
"""

from copy import deepcopy

import pytest

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.core.refusal import Refused
from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_declarations import _nodes
from behave.sim.rtl.source_ir import SourceCompiler, _Lowerer, _sequence
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_observation_frontend import _installed
from .test_rtl_source_ir import _value_leaves

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


def _action(tmp_path, declarations, call, *, delay='#0', prepare=True):
    source = f'''module M(input clk,input [3:0] a,output logic [7:0] q);
      {declarations}
      always @(posedge clk) begin assert {delay}(0) else {call}; end
    endmodule'''
    (tmp_path / 'design.sv').write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, 'M', run=_installed)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    compiler = SourceCompiler(source, ast, plan, filename='design.sv')
    context, = plan.declarations.contexts
    lowerer = _Lowerer(compiler, context, namespace='', storage_name=None)
    lowerer.frames.append({})
    assertion, = [node for node in _nodes(ast) if node.get('kind') == 'ImmediateAssertion']
    # No new native or query capability is claimed by fragment construction.
    with pytest.raises(Refused, match='deferred'):
        compiler.lower(context)
    action = assertion['ifFalse']
    if not prepare:
        return lowerer, action, source
    phases = lowerer.capture_action(action)
    names = {compiler.symbols[identity]['name']: name for identity, name in lowerer.storage.items()}
    return lowerer, phases, names, source


def _zero(kind):
    return [_zero(kind.elem) for _ in range(kind.cells)] if isinstance(kind, types.Array) else (
        False if kind == types.BOOLEAN else 0)


def _state(lowerer, phases, names, **values):
    assert set(values) == set(names)
    return {**{names[key]: value for key, value in values.items()},
            **{name: _zero(lowerer.types[name]) for name in phases.captures}}


def _domain(value):
    return (ranges._CellRange(tuple(_domain(cell) for cell in value)) if isinstance(value, list)
            else ranges.Range(int(value), int(value)))


def _step(lowerer, commands, state):
    import z3

    proc = ir.Procedure(list(state), [], [name for name in lowerer.types if name not in state],
                        [], _sequence(commands), types=lowerer.types)
    env = {name: _domain(value) for name, value in state.items()}
    lowerer.domain_budget.prove(proc, env, lowerer.access_bounds,
        divisors=lowerer.divisor_bounds,
        predicates={**lowerer.control_domains, **lowerer.defined.obligations})
    post, _, hits, error = interp.run_step(proc, {}, state)
    encoded = encoder.encode_step(proc, env=env)
    solver = z3.Solver()
    solver.add(encoded.constraint)
    for name, value in state.items():
        solver.add(*(term == cell for term, cell in _value_leaves(encoded.pre[name], value)))
    assert solver.check() == z3.sat
    model = solver.model()
    assert encoder.error_name(model.eval(encoded.error), encoded.error_codes) == error
    assert {goal for goal, term in encoded.hits.items() if z3.is_true(model.eval(term))} == hits
    for name, value in post.items():
        for term, cell in _value_leaves(encoded.post[name], value):
            actual = model.eval(term, model_completion=True)
            assert (z3.is_true(actual) == cell if z3.is_bool(actual)
                    else actual.as_long() == cell % (1 << actual.size()))
    return post, hits, error


@pytest.mark.parametrize('delay', ['#0', 'final'])
@pytest.mark.parametrize('signed', ['', 'signed'])
def test_value_captures_but_action_reads_live_source_storage(tmp_path, delay, signed):
    lowerer, phases, names, _ = _action(tmp_path, f'''
      logic [3:0] live;
      task automatic report(input logic {signed} [3:0] value);
        q=int'(value)+int'(live);
      endtask''', 'report(a)', delay=delay)
    assert len(phases.captures) == 1
    state = _state(lowerer, phases, names, a=13, live=1, q=0)
    captured, hits, error = _step(lowerer, phases.encounter, state)
    assert not error and captured[names['q']] == 0
    action_marks = {cmd.goal for cmd in ir.walk(_sequence(phases.execution)) if isinstance(cmd, ir.Mark)}
    assert action_marks and hits.isdisjoint(action_marks)
    captured.update({names['a']: 2, names['live']: 7})
    post, hits, error = _step(lowerer, phases.execution, captured)
    assert not error and post[names['q']] == (4 if signed else 20)
    assert action_marks <= hits


@pytest.mark.parametrize('argument', ['a++', 'bump()'])
def test_argument_effects_execute_once_before_action_entry(tmp_path, argument):
    # Use internal storage for the effectful argument, not a Driver input.
    argument = argument.replace('a', 'value')
    lowerer, phases, names, _ = _action(tmp_path, '''
      logic [3:0] value;
      function automatic logic [3:0] bump(); return value++; endfunction
      task automatic report(input logic [3:0] v); q=v; endtask''', f'report({argument})')
    state = _state(lowerer, phases, names, value=3, q=0)
    captured, _, error = _step(lowerer, phases.encounter, state)
    assert not error and captured[names['value']] == 4 and captured[names['q']] == 0
    captured[names['value']] = 9
    post, _, error = _step(lowerer, phases.execution, captured)
    assert not error and post[names['value']] == 9 and post[names['q']] == 3


def test_selected_reference_captures_address_not_late_value(tmp_path):
    lowerer, phases, names, _ = _action(tmp_path, '''
      logic index;
      logic [7:0] cells[0:1];
      task automatic report(ref logic [7:0] v); q=v; v=v+1; endtask''', 'report(cells[index])')
    assert len(phases.captures) == 1
    state = _state(lowerer, phases, names, index=0, cells=[3, 5], q=0)
    captured, _, error = _step(lowerer, phases.encounter, state)
    assert not error and captured[names['cells']] == [3, 5]
    captured.update({names['index']: 1, names['cells']: [9, 11]})
    post, _, error = _step(lowerer, phases.execution, captured)
    assert not error and post[names['q']] == 9 and post[names['cells']] == [10, 11]


def test_array_input_is_a_recursive_value_capture(tmp_path):
    lowerer, phases, names, _ = _action(tmp_path, '''
      logic [7:0] cells[0:1];
      task automatic report(input logic [7:0] v[0:1]); q=v[0]+v[1]; endtask''', 'report(cells)')
    state = _state(lowerer, phases, names, cells=[3, 5], q=0)
    captured, _, error = _step(lowerer, phases.encounter, state)
    assert not error
    captured[names['cells']] = [9, 11]
    post, _, error = _step(lowerer, phases.execution, captured)
    assert not error and post[names['q']] == 8


def test_reference_arguments_preserve_aliasing_at_dispatch(tmp_path):
    lowerer, phases, names, _ = _action(tmp_path, '''
      logic [7:0] value;
      task automatic report(ref logic [7:0] x,y); x=x+1; q=y; endtask''',
      'report(value,value)')
    assert not phases.captures  # references are storage identities, not values
    state = _state(lowerer, phases, names, value=3, q=0)
    captured, _, error = _step(lowerer, phases.encounter, state)
    assert not error and captured == state
    captured[names['value']] = 9
    post, _, error = _step(lowerer, phases.execution, captured)
    assert not error and post[names['value']] == post[names['q']] == 10


def test_reference_selector_effect_is_not_repeated_at_dispatch(tmp_path):
    lowerer, phases, names, _ = _action(tmp_path, '''
      logic index;
      logic [7:0] cells[0:1];
      function automatic logic next_index(); return index++; endfunction
      task automatic report(ref logic [7:0] v); q=v; endtask''', 'report(cells[next_index()])')
    state = _state(lowerer, phases, names, index=0, cells=[3, 5], q=0)
    captured, early, error = _step(lowerer, phases.encounter, state)
    assert not error and captured[names['index']] == 1 and early
    captured[names['cells']] = [9, 11]
    post, late, error = _step(lowerer, phases.execution, captured)
    assert not error and post[names['index']] == 1 and post[names['q']] == 9
    assert early.isdisjoint(late)


def test_repeated_encounters_need_distinct_frames(tmp_path):
    lowerer, phases, names, _ = _action(tmp_path, '''
      task automatic report(input logic [3:0] v); q=q*4+v; endtask''', 'report(a)')
    state = _state(lowerer, phases, names, a=2, q=0)
    first, _, _ = _step(lowerer, phases.encounter, state)
    first_frame = {name: deepcopy(first[name]) for name in phases.captures}
    first[names['a']] = 3
    second, _, _ = _step(lowerer, phases.encounter, first)
    second_frame = {name: deepcopy(second[name]) for name in phases.captures}
    second.update(first_frame)
    post, _, _ = _step(lowerer, phases.execution, second)
    assert post[names['q']] == 2
    post.update(second_frame)
    post, _, _ = _step(lowerer, phases.execution, post)
    assert post[names['q']] == 11


@pytest.mark.parametrize('severity', ['$error', '$fatal'])
def test_diagnostic_argument_hits_precede_late_failure(tmp_path, severity):
    prefix = '1,' if severity == '$fatal' else ''
    lowerer, phases, names, _ = _action(tmp_path, '''
      logic [3:0] value;
      function automatic int bump(); value++; return value; endfunction''',
      f'{severity}({prefix}"value %d",bump())')
    state = _state(lowerer, phases, names, value=3)
    captured, early, error = _step(lowerer, phases.encounter, state)
    assert not error and captured[names['value']] == 4 and early
    post, late, error = _step(lowerer, phases.execution, captured)
    assert error == ('RTLError' if severity == '$error' else 'RTLFatal')
    assert post[names['value']] == 4 and late and early.isdisjoint(late)


@pytest.mark.parametrize('call', ['report(bad())', '$error("outer %d",bad())'])
def test_inner_argument_failure_prevents_action_entry(tmp_path, call):
    lowerer, phases, names, _ = _action(tmp_path, '''
      function automatic int bad(); $fatal(1,"inner"); return 3; endfunction
      task automatic report(input int v); q=v; endtask''', call)
    state = _state(lowerer, phases, names, **({'q': 0} if 'q' in names else {}))
    _, hits, error = _step(lowerer, phases.encounter, state)
    assert error == 'RTLFatal' and hits
    later = {cmd.goal for cmd in ir.walk(_sequence(phases.execution))
             if isinstance(cmd, (ir.Mark, ir.Fail))}
    assert hits.isdisjoint(later)


@pytest.mark.parametrize('delay', ['#0', 'final'])
def test_explicit_empty_action_keeps_its_late_entry(tmp_path, delay):
    lowerer, phases, names, _ = _action(tmp_path, '', '', delay=delay)
    assert not phases.encounter and not phases.captures and not names
    post, hits, error = _step(lowerer, phases.execution, {})
    assert not error and not post and hits


def test_late_array_read_does_not_borrow_caller_loop_range(tmp_path):
    lowerer, action, _ = _action(tmp_path, '''
      int index;
      logic [7:0] cells[0:1];
      task automatic report(); q=cells[index]; endtask''', 'report()', prepare=False)
    identity, = [identity for identity, symbol in lowerer.symbols.items()
                 if symbol.get('name') == 'index' and symbol.get('kind') == 'Variable']
    name = lowerer.variable(identity)
    lowerer.loop_ranges[name] = ranges.Range(0, 1)
    phases = lowerer.capture_action(action)
    assert lowerer.loop_ranges[name] == ranges.Range(0, 1)
    assert lowerer.access_bounds  # dispatch index is not proved by that old range
    names = {lowerer.symbols[identity]['name']: name for identity, name in lowerer.storage.items()}
    state = _state(lowerer, phases, names, index=2, cells=[3, 5], q=0)
    with pytest.raises(Refused, match='array selection'):
        _step(lowerer, phases.execution, state)
