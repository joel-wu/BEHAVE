"""Packed-bit value dependence must not manufacture or conceal feedback."""

from itertools import product

import pytest

from behave.sim.symbolic import interp, ir, typecheck as types
from behave.sim.rtl.source_effects import read_masks, single_write_dependencies
from behave.sim.rtl.source_integral import IntegralType
from behave.sim.rtl.source_packed import merge_bits

from .test_rtl_source_event_bounds import _freeze
from .test_rtl_source_scheduler import _lower, _names, _state


def test_syntax_read_masks_keep_slices_guards_and_saved_local_reads():
    kinds = {'x': IntegralType(8, False), 'n': IntegralType(2, False),
             's': IntegralType(8, True)}
    part = lambda name, offset, width: ir.Op('uwrap',
        ir.Op('shr', ir.Var(name), ir.Lit(offset)), ir.Lit(width))
    body = ir._sequence([
        ir.Assign('saved', part('x', 2, 4)),
        ir.If(part('n', 1, 1), ir.Assign('q', ir.Var('saved')), ir.Skip()),
        ir.Assign('saved', ir.Lit(0)),
    ])
    assert read_masks(body, kinds) == {'x': 0x3c, 'n': 2}
    assert read_masks(ir.Assign('q', part('s', 12, 1)), kinds) == {'s': 0x80}
    dynamic = ir.Op('uwrap', ir.Op('shr', ir.Var('x'), ir.Var('n')), ir.Lit(1))
    assert read_masks(ir.Assign('q', dynamic), kinds) == {'x': 0xff, 'n': 3}
    assert read_masks(ir.For('i', 10**9, body), kinds) == {'x': 0x3c, 'n': 2}


@pytest.mark.parametrize('signed', [False, True])
def test_syntax_read_masks_cover_every_controlled_result(signed):
    word = IntegralType(3, signed)
    kinds = {'x': word, 'n': IntegralType(2, False)}
    bits = ir.Op('uwrap', ir.Op('shr', ir.Var('x'), ir.Lit(1)), ir.Lit(2))
    body = ir._sequence([
        ir.Assign('q', ir.Lit(0)), ir.Assign('saved', bits),
        ir.If(ir.Op('truth', ir.Op('and', ir.Var('n'), ir.Lit(2))),
              ir.Assign('q', ir.Var('saved')), ir.Skip()),
    ])
    deps = read_masks(body, kinds)
    assert deps == {'x': 6, 'n': 2}
    proc = ir.Procedure(['q'], ['x', 'n'], ['saved'], [], body,
                       types={name: types.INTEGER for name in ('q', 'x', 'n', 'saved')})
    observed = {}
    for x, n in product(range(-4, 4) if signed else range(8), range(4)):
        post, _, _, error = interp.run_step(proc, {'x': x, 'n': n}, {'q': 0})
        assert not error
        key = (x & deps['x'], n & deps['n'])
        assert observed.setdefault(key, post['q']) == post['q']


@pytest.mark.parametrize('temporary', [False, True])
def test_packed_store_excludes_preserved_bits_but_keeps_the_read_bit(temporary):
    kinds = {name: IntegralType(4, False) for name in ('x', 'q')}
    merged = merge_bits(kinds['q'], ir.Var('q'), ir.Var('data'), ir.Lit(4))
    body = ir._sequence([
        ir.Assign('bit', ir.Op('uwrap', ir.Op('shr', ir.Var('q'), ir.Lit(1)), ir.Lit(1))),
        ir.Assign('data', ir.Op('ushl', ir.Var('bit'), ir.Lit(2), ir.Lit(4))),
        *([ir.Assign('merged', merged)] if temporary else []),
        ir.Assign('q', ir.Var('merged') if temporary else merged),
    ])
    assert single_write_dependencies(body, kinds, {'q': 4}) == {('q', 4): {('q', 2)}}


@pytest.mark.parametrize('terminal', [False, True])
def test_read_only_checks_preserve_written_value_dependencies(terminal):
    kinds = {name: IntegralType(3, False) for name in ('x', 'q', 'check')}
    event = ir.Fail('SourceDomain', 'check') if terminal else ir.Mark('check')
    guard = ir.If(ir.Op('truth', ir.Var('check')), ir.Seq(ir.Skip(), event), ir.Skip())
    body = ir.Seq(guard, ir.Assign('q', ir.Var('x')))
    assert single_write_dependencies(body, kinds, {'q': 7}) == {('q', 7): {('x', 7)}}
    # This certificate does not rewrite away either branch or its event.
    assert ir._parts(body)[0] is guard
    procedure = ir.Procedure(['q'], ['x', 'check'], [], [], body,
        types={name: types.INTEGER for name in kinds})
    for check in (0, 1):
        post, _, hits, error = interp.run_step(procedure, {'x': 5, 'check': check}, {'q': 0})
        assert ('check' in hits) == bool(check)
        assert bool(error) == bool(check and terminal)
        if not error:
            assert post['q'] == 5


@pytest.mark.parametrize('target', ['q', 'saved'])
def test_a_conditional_value_write_still_requires_a_stronger_certificate(target):
    kinds = {name: IntegralType(3, False) for name in ('x', 'q', 'check')}
    body = ir._sequence([
        ir.Assign('saved', ir.Lit(0)),
        ir.If(ir.Op('truth', ir.Var('check')), ir.Assign(target, ir.Var('x')), ir.Skip()),
        ir.Assign('q', ir.Var('saved')),
    ])
    assert single_write_dependencies(body, kinds, {'q': 7}) == {}


@pytest.mark.parametrize('replacement', ['value', 'shift'])
def test_projected_temporary_keeps_its_old_value_after_operand_reassignment(replacement):
    kinds = {name: IntegralType(3, False) for name in ('x', 'y', 'q')}
    before = [ir.Assign('shift', ir.Lit(0)),
              ir.Assign('value', ir.Op('shr', ir.Var('x'), ir.Var('shift'))),
              ir.Assign('saved', ir.Op('or', ir.Var('value'), ir.Lit(0)))]
    changed = ir.Assign(replacement, ir.Var('y') if replacement == 'value' else ir.Lit(2))
    body = ir._sequence([*before, changed, ir.Assign('q', ir.Op('and', ir.Var('saved'), ir.Lit(1)))])
    deps = single_write_dependencies(body, kinds, {'q': 1})['q', 1]
    proc = ir.Procedure(['q'], ['x', 'y'], ['shift', 'value', 'saved'], [], body,
                        types={name: types.INTEGER for name in (*kinds, 'shift', 'value', 'saved')})
    observed = {}
    for x, y in product(range(8), repeat=2):
        inputs = {'x': x, 'y': y, 'q': 0}
        post, _, _, error = interp.run_step(proc, {'x': x, 'y': y}, {'q': 0})
        assert not error and post['q'] == x & 1
        key = tuple(inputs[name] & mask for name, mask in sorted(deps))
        assert observed.setdefault(key, post['q']) == post['q']


@pytest.mark.parametrize('signed', [False, True])
def test_full_width_temporary_chain_reuses_captured_dependencies(monkeypatch, signed):
    from behave.sim.rtl import source_effects

    original = source_effects.ranges.bounds_of_expression
    visits = 0

    def counted(*args, **kwargs):
        nonlocal visits
        visits += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(source_effects.ranges, 'bounds_of_expression', counted)
    word, count = IntegralType(16, signed), 128
    names = ['x', *[f't{index}' for index in range(count)]]
    body = ir._sequence([
        *[ir.Assign(name, word.wrap(ir.Op('or', ir.Var(previous), ir.Var(previous))))
          for name, previous in zip(names[1:], names)],
        ir.Assign('q', ir.Var(names[-1])),
    ])
    assert single_write_dependencies(body, {'x': word, 'q': word}, {'q': 65535}) == {
        ('q', 65535): {('x', 65535)}}
    assert visits <= 12 * count  # Structural work bound, not a wall-time quota.


@pytest.mark.parametrize('signed', [False, True])
@pytest.mark.parametrize('temporary', [False, True])
@pytest.mark.parametrize('expr', [
    ir.Op('shr', ir.Var('x'), ir.Lit(2)),
    ir.Op('shl', ir.Var('x'), ir.Lit(1)),
    ir.Op('ulshr', ir.Var('x'), ir.Lit(2), ir.Lit(3)),
    ir.Op('ushl', ir.Var('x'), ir.Lit(2), ir.Lit(3)),
    ir.Op('uwrap', ir.Var('x'), ir.Lit(2)),
    ir.Op('swrap', ir.Var('x'), ir.Lit(2)),
    ir.Op('and', ir.Var('x'), ir.Lit(2)),
    ir.Op('or', ir.Var('x'), ir.Lit(-2)),
    ir.Op('xor', ir.Var('x'), ir.Lit(3)),
    ir.Op('inv', ir.Var('x')),
    ir.Op('add', ir.Var('x'), ir.Lit(1)),
    ir.Op('mul', ir.Var('x'), ir.Lit(3)),
])
def test_reported_dependencies_cover_every_changed_output_bit(expr, signed, temporary):
    word = IntegralType(3, signed)
    kinds = {'x': word, 'q': IntegralType(3, False)}
    values = range(-4, 4) if signed else range(8)
    for mask in (1, 2, 4, 5, 7):
        merged = merge_bits(kinds['q'], ir.Var('q'), expr, ir.Lit(mask))
        body = (ir._sequence([ir.Assign('merged', merged), ir.Assign('q', ir.Var('merged'))])
                if temporary else ir.Assign('q', merged))
        dependencies = single_write_dependencies(body, kinds, {'q': mask})['q', mask]
        inputs = {name: 0 for name in kinds}
        proc = ir.Procedure(['q'], ['x'], [], [], body,
                            types={name: types.INTEGER for name in kinds})
        observed = {}
        for x, q in product(values, range(8)):
            inputs.update(x=x, q=q)
            key = tuple(inputs[name] & bits for name, bits in sorted(dependencies))
            post, _, _, error = interp.run_step(proc, {'x': x}, {'q': q})
            assert not error
            value = post['q'] & mask
            assert observed.setdefault(key, value) == value, (expr, mask, dependencies, inputs)


@pytest.mark.eda('slang')
@pytest.mark.parametrize('left,right', [(2, 0), (0, 2)])
def test_packed_chain_all_initial_values_and_schedules_have_a_finite_bound(tmp_path, left, right):
    step = 1 if left < right else -1
    source = f'''module M(input a,output y);
      wire [{left}:{right}] chain;
      assign chain[{left}]=a;
      for(genvar i=1;i<3;i=i+1) begin
        assign chain[{left}+{step}*i]=chain[{left}+{step}*(i-1)];
      end
      assign y=chain[{right}];
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    assert machine.event_bound is not None
    names = _names(machine)
    for a, chain, y in product(range(2), range(8), range(2)):
        initial = _state(machine, {'a': a, 'chain': chain, 'y': y}, startup=True)
        drive = {driven: initial[name] for name, driven in machine.drive_inputs}
        frontier = {_freeze(initial): initial}
        longest = 0
        while frontier:
            following = {}
            for state in frontier.values():
                if machine.boundary_is_quiet(state):
                    assert state[names['y']] == a
                    continue
                for action in range(1, machine.commit_action + 1):
                    post, outputs, _, error = interp.run_step(
                        machine.procedure, {**drive, 'action': action}, state)
                    assert not error
                    if outputs.transactions[0]['accepted']:
                        following[_freeze(post)] = post
            frontier = following
            longest += 1
            assert longest <= machine.event_bound


@pytest.mark.eda('slang')
@pytest.mark.parametrize('width', [2, 4, 8])
@pytest.mark.parametrize('initial', ['0', "1'b0"])
@pytest.mark.parametrize('defer_domains', [False, True])
def test_generated_ripple_carry_has_no_value_feedback(tmp_path, width, initial, defer_domains):
    machine, _ = _lower(tmp_path, f'''module M(input [{width-1}:0] x,y,
      output [{width}:0] sum);
      wire [{width}:0] carry;
      assign carry[0]={initial};
      for(genvar i=0;i<{width};i=i+1) begin: stages
        assign sum[i]=x[i]^y[i]^carry[i];
        assign carry[i+1]=(x[i]&y[i])|(x[i]&carry[i])|(y[i]&carry[i]);
      end
      assign sum[{width}]=carry[{width}];
    endmodule''', defer_domains=defer_domains)
    assert machine.event_bound is not None and machine.notification_caps, {
        'deferred_domains': defer_domains,
        'private_domain_checks': len(machine.domain_checks),
        'unproved_process_writes': [index for index, process in enumerate(machine.program.processes)
            if process.write_masks and not single_write_dependencies(process.activation,
                machine.program.kinds, {name: mask for name, mask, _ in process.write_masks})],
    }


@pytest.mark.eda('slang')
def test_a_genuine_packed_bit_feedback_cycle_is_not_certified(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(output [1:0] q);
      assign q[0]=~q[1];
      assign q[1]=q[0];
    endmodule''')
    assert machine.event_bound is None


@pytest.mark.cosim
@pytest.mark.solver
def test_packed_dependency_query_produces_a_natively_confirmed_new_hit(tmp_path):
    from behave.sim import api
    from behave.sim.symbolic import ranges
    from behave.sim.engines import probing
    from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
    from behave.sim.engines.rtl.source_query import query_source_interval
    from behave.sim.generation import StimulusField

    from .test_rtl_source_query import _goal
    from .test_rtl_source_trace import _contract

    source = '''module M(input [3:0] a,output reg [3:0] y);
      wire [3:0] chain;
      assign chain[0]=a[0];
      for(genvar i=1;i<4;i=i+1) begin
        assign chain[i]=chain[i-1];
      end
      always_comb if(chain==15) y=a; else y=a;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    assert machine.event_bound is not None
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': b'class M:\n def reset(self): pass\n def process(self,a): return {"y":a}\n'},
        config={'class': 'M'})
    contract = _contract()
    task = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(tmp_path/'design.sv', 'M', tmp_path/'build'))
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=1))
    checked = handle.replay(baseline.stimuli, reference=reference).evaluation
    assert checked.complete and checked.passed
    trace = checked.results[0].trace
    goal = _goal(source, plan, 'chain==15')
    assert not any(hit for row in trace.records for name, hit in row.hits if name == goal)
    binding = SourceTraceBindings(machine, contract, trace.stimulus, trace)
    query = binding.at((StimulusField('input', 1, 0, binding.releases['input', 1]),))
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, query.state, query.phases[0], (goal,),
        mutable_inputs=query.mutable_inputs, event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate[_names(machine)['a']] & 1
    replay = handle.replay((query.proposal(answer.candidate),), reference=reference).evaluation
    assert replay.complete and replay.passed
    assert any(hit for row in replay.results[0].trace.records for name, hit in row.hits if name == goal)
