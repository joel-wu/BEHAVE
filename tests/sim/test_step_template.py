"""Exact template instances preserve the plain encoder, with no frame aliases."""
from collections import Counter

import pytest
import z3

from behave.sim.symbolic import encoder, interp, ir, ranges, step_template
from behave.sim.symbolic.step_template import StepTemplate, _leaves, _tree

from .test_rtl_source_scheduler import _lower

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('normalized', [False, True])
@pytest.mark.parametrize('body', [
    'always @(posedge clk) begin cells[a[0]] <= a; q <= cells[!a[0]]; end',
    'always @(posedge clk) begin if(a==0) begin cells[0]<=0; cells[1]<=0; end '
    'else cells[a[0]][2:0]<=a[2:0]; q<=cells[a[0]]; end',
])
def test_indexed_array_state_preserves_source_transition_history(tmp_path, body, normalized):
    from behave.sim.symbolic.functional_array import FunctionalArray

    machine, _ = _lower(tmp_path, 'module M(input clk,input [3:0] a,output reg [3:0] q);'
        'reg [3:0] cells[0:1]; ' + body + ' endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    template = StepTemplate(proc, env)
    if normalized:
        template = template.normalized()
    left_state = right_state = None
    solver, differences, converted = z3.Solver(), [], []

    def project(value):
        if isinstance(value, FunctionalArray):
            converted.append(value.length)
            return value.indexed()
        return value

    for _ in range(3):
        left = template.instantiate(left_state)
        right = template.instantiate(left.pre if right_state is None else
            {**right_state, **{name: left.pre[name] for name in proc.inputs}})
        # Compare restrictions too, not only successful paths or a chosen action.
        differences.extend((left.constraint != right.constraint,
                            left.ok != right.ok, left.error != right.error))
        for field in ('state_post', 'post', 'hits'):
            for name, value in getattr(left, field).items():
                differences.extend(a != b for a, b in zip(
                    _leaves(value), _leaves(getattr(right, field)[name]), strict=True))
        left_state = left.state_post
        right_state = step_template._map_tree(right.state_post, project)
    assert converted, 'fixture must exercise an actual array boundary'
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('failure', [False, True])
def test_selected_template_plans_only_closed_observations(monkeypatch, failure):
    from behave.sim.symbolic import plan
    from .test_goal_query_cone_planning import _problem

    proc, env = _problem(failure=failure)
    monkeypatch.setattr(plan, 'TERM_BUDGET', 200)
    monkeypatch.setattr(encoder, 'TERM_BUDGET', 200)
    with pytest.raises(encoder.EncodingError, match='encoding-expansion'):
        StepTemplate(proc, env)
    selected = StepTemplate.selected(proc, env, ('target',))
    observed = selected.project({'target': ('target',)}, outputs=()).encode()
    solver = z3.Solver()
    solver.add(observed.constraint, observed.hits['target'])
    assert solver.check() == (z3.unsat if failure else z3.sat)
    if not failure:
        assert solver.model().eval(observed.pre['a']).as_long() == 3
    assert selected.procedure is proc
    assert not observed.state_post and not observed.post
    assert 'target' in ir.goals_of(proc.body)


def test_selected_template_keeps_relevant_loops_and_full_validation(monkeypatch):
    from behave.sim.symbolic import plan, typecheck as types
    from .test_goal_query_cone_planning import _problem

    monkeypatch.setattr(plan, 'TERM_BUDGET', 200)
    monkeypatch.setattr(encoder, 'TERM_BUDGET', 200)
    proc, env = _problem(relevant=True)
    with pytest.raises(encoder.EncodingError, match='encoding-expansion'):
        StepTemplate.selected(proc, env, ('target',))
    proc, env = _problem()
    proc.types['value'] = types.BOOLEAN
    with pytest.raises(encoder.EncodingError):
        StepTemplate.selected(proc, env, ('target',))


@pytest.mark.parametrize('late_failure', [False, True])
def test_selected_template_preserves_predecessors_and_failed_suffix(late_failure):
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    body = ir._sequence((
        ir.If(ir.Op('eq', ir.Var('x'), ir.Var('a')), ir.Mark('target'), ir.Skip()),
        ir.Assign('x', ir.Var('y')),
        ir.Assign('y', ir.Op('uwrap', ir.Op('add', ir.Var('a'), ir.Lit(1)), ir.Lit(4))),
        ir.Assign('unobserved', ir.Op('uwrap', ir.Op('mul', ir.Var('a'), ir.Var('a')), ir.Lit(4))),
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(7)), ir.Fail('Stop'), ir.Skip())
        if late_failure else ir.Skip()))
    proc = ir.Procedure(('x', 'y', 'unobserved'), ('a',), (), (), body,
        initial={'x': 0, 'y': 0, 'unobserved': 0},
        signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    env = {name: ranges.Range(0, 15) for name in (*proc.state, 'a')}
    full = StepTemplate(proc, env)
    selected = StepTemplate.selected(proc, env, ('target',)).project(
        {'target': ('target',)}, outputs=())
    assert set(selected.state_names) == {'x', 'y'}
    before_full = before_selected = None
    solver = z3.Solver()
    differences = []
    for _ in range(3):
        left = full.instantiate(before_full)
        right = selected.instantiate(left.pre if before_selected is None else
                                      {**before_selected, 'a': left.pre['a']})
        solver.add(left.constraint, right.constraint)
        differences.extend((left.hits['target'] != right.hits['target'],
                            left.ok != right.ok, left.error != right.error))
        for name in selected.state_names:
            a, b = encoder.widen(left.state_post[name], right.state_post[name])
            differences.append(a != b)
        before_full, before_selected = left.state_post, right.state_post
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('body', [
    'always_comb if(a==9) q=a+1; else q=0;',
    'always @(posedge clk) begin if(a==9) q<=a; else q<=q+1; end',
    'integer i; always_comb begin q=0; for(i=0;i<3;i=i+1) if(a==i) q=q+1; end',
    'reg [3:0] cells[2:3]; always_comb begin cells[2]=a; cells[3]=a+1; q=cells[2+a[0]]; end',
    ('reg signed [3:0] cells[2:3]; always @(posedge clk) begin cells[2+a[0]][1:0]<=a; '
     'cells[2+a[0]][3:2]<=-a; cells[3]<=0; q=cells[2]; end'),
    'always_comb begin assert(a!=9) else q=0; q=a; end',
    'always_comb begin q=0; if(a==9) $fatal(1,"stop"); q=a; end',
])
@pytest.mark.parametrize('named_successors', [False, True])
def test_template_matches_every_state_output_hit_and_failure(tmp_path, body, named_successors):
    machine, _ = _lower(tmp_path, 'module M(input clk,input [3:0] a,output reg [3:0] q);'
                                + body + ' endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    plain = encoder.encode_step(proc, env=env, tag='plain')
    template = StepTemplate(proc, env)
    compiled = (template.encode if named_successors else template.instantiate)(plain.pre)
    if not named_successors:
        assert not compiled.definitions
    different = [compiled.ok != plain.ok, compiled.error != plain.error]
    for name in ('state_post', 'post', 'hits'):
        for key, value in getattr(plain, name).items():
            different.extend(left != right for left, right in zip(
                _leaves(_tree(value)), _leaves(getattr(compiled, name)[key]), strict=True))
    solver = z3.Solver()
    solver.add(plain.constraint, compiled.constraint, z3.Or(*different))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('fields', [2, 32, 128])
@pytest.mark.parametrize('normalized', [False, True])
@pytest.mark.parametrize('private', [False, True])
def test_boundary_expressions_do_not_create_then_expand_successor_names(
        monkeypatch, fields, normalized, private):
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    names = tuple(f'x{i}' for i in range(fields))
    proc = ir.Procedure(names, ('a',), (), (), ir._sequence((
        ir.If(ir.Op('eq', ir.Var('a'), ir.Lit(255)), ir.Fail('BoundaryFailure'), ir.Skip()),
        *(ir.Assign(name, ir.Op('uwrap', ir.Op('add', ir.Var('a'), ir.Lit(i)), ir.Lit(8)))
          for i, name in enumerate(names)),
        ir.Mark('after'))), initial=dict.fromkeys(names, 0),
        signature=ProcedureSignature((('a', FiniteType(8)),), ()))
    template = StepTemplate(proc, dict.fromkeys((*names, 'a'), ranges.Range(0, 255)))
    if normalized:
        template = template.normalized()
    if private:
        template = template.translate(z3.Context())
    encoded = template.encode()

    def unnecessary(*_args, **_kwargs):
        pytest.fail('a boundary predicate allocated successor definitions')

    monkeypatch.setattr(step_template, '_bind_successors', unnecessary)
    direct = template.instantiate(encoded.pre)
    assert not direct.definitions and direct.pre.keys() == encoded.pre.keys()
    solver = z3.Solver(ctx=template.context)
    solver.add(encoded.constraint, direct.constraint)
    solver.add(z3.Or(encoded.ok != direct.ok, encoded.error != direct.error,
        encoded.hits['after'] != direct.hits['after'],
        *(encoded.state_post[name] != direct.state_post[name] for name in names)))
    assert solver.check() == z3.unsat
    domain = z3.Solver(ctx=template.context)
    domain.add(direct.constraint, direct.pre['a'] == -1)
    assert domain.check() == z3.unsat


def test_instances_with_identical_tags_are_independent_and_inputs_keep_their_domain(tmp_path):
    machine, _ = _lower(tmp_path, 'module M(input [3:0] a,output reg [3:0] q); always_comb q=a; endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    template = StepTemplate(proc, env)
    first, second = template.encode(tag='same'), template.encode(tag='same')
    name = next(driven for field, driven in machine.drive_inputs
                if machine.program.fields[field].port == 'a')
    solver = z3.Solver()
    solver.add(first.constraint, second.constraint, first.pre[name] == 1, second.pre[name] == 2)
    assert solver.check() == z3.sat
    solver.add(first.pre[name] == 16)
    assert solver.check() == z3.unsat


def test_supplied_state_is_not_constrained_by_an_inferred_range(tmp_path):
    machine, _ = _lower(tmp_path, 'module M(input [3:0] a,output reg [3:0] q); always_comb q=0; endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    q = next(name for name, field in machine.program.fields.items() if field.port == 'q')
    env[q] = ranges.Range(0, 3)  # Five is representable but outside this proposal.
    template = StepTemplate(proc, env)
    compiled = template.encode({q: z3.BitVecVal(5, ranges.encoding_width(env[q]))})
    solver = z3.Solver()
    solver.add(compiled.constraint, compiled.pre['action'] == 0, compiled.ok)
    assert solver.check() == z3.sat


def test_a_missing_state_carrier_is_a_range_refusal_not_a_key_error(tmp_path):
    machine, _ = _lower(tmp_path,
        'module M(input [3:0] a,output reg [3:0] q); always_comb q=a; endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    missing = next(name for name, field in machine.program.fields.items() if field.port == 'q')
    env.pop(missing)
    with pytest.raises(encoder.EncodingError, match=f'{missing} has no range') as error:
        StepTemplate(proc, env)
    assert error.value.kind == 'range'


@pytest.mark.parametrize('loop', [False, True])
@pytest.mark.parametrize('masked', [False, True])
def test_state_carriers_close_over_multiple_copies_without_truncating_values(loop, masked):
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    # Masked results need only three signed bits. Without the mask, the wider
    # input carrier must propagate through every link before reuse is safe.
    def value(name):
        return ir.Op('and', ir.Var(name), ir.Lit(3)) if masked else ir.Var(name)

    body = ir._sequence((
        ir.Assign('x', value('y')),
        ir.Assign('y', value('z')),
        ir.Assign('z', value('a')),
        ir.If(ir.Op('eq', ir.Var('x'), ir.Lit(2)), ir.Mark('two'), ir.Skip())))
    proc = ir.Procedure(('x', 'y', 'z'), ('a',), ('i',) if loop else (), (),
        ir.For('i', 1, body) if loop else body,
        initial={'x': 0, 'y': 0, 'z': 0},
        signature=ProcedureSignature((('a', FiniteType(10)),), ()))
    env = {'a': ranges.Range(0, 1023), **{name: ranges.Range(0, 3) for name in proc.state}}
    template = StepTemplate(proc, env)
    first = template.encode()
    for name in proc.state:
        assert first.pre[name].size() == first.state_post[name].size() == (3 if masked else 11)
    # A one-visit loop is exactly its body. Compare independently against
    # the non-loop encoder so its public plan still rejects wider carriers.
    plain_proc = ir.Procedure(proc.state, proc.inputs, (), (), body,
        initial=proc.initial, signature=proc.signature)
    plain = encoder.encode_step(plain_proc, pre=first.pre, env=env)
    solver = z3.Solver()
    solver.add(first.constraint, plain.constraint, z3.Or(
        *(first.state_post[name] != plain.state_post[name] for name in proc.state),
        first.hits['two'] != plain.hits['two'], first.ok != plain.ok, first.error != plain.error))
    assert solver.check() == z3.unsat
    state = {'x': 1, 'y': 2, 'z': 3}
    symbolic = {name: z3.BitVecVal(value, 3) for name, value in state.items()}
    for a in (1023, 512, 6, 9):
        state, _, marks, error = interp.run_step(proc, {'a': a}, state)
        assert error == ''
        encoded = template.encode({**symbolic, 'a': z3.BitVecVal(a, 11)})
        solver = z3.Solver()
        solver.add(encoded.constraint)
        assert solver.check() == z3.sat
        model = solver.model()
        assert {name for name, hit in encoded.hits.items() if z3.is_true(model.eval(hit))} == marks
        for name in proc.state:
            assert model.eval(encoded.state_post[name]).as_signed_long() == state[name]
        symbolic = {name: model.eval(value) for name, value in encoded.state_post.items()}
    with pytest.raises(encoder.EncodingError, match='exceeds the prepared transition carrier'):
        template.encode({'x': z3.BitVec('too_wide_for_template', 12)})


def test_loop_template_closes_array_leaf_carriers_without_changing_value_domains():
    from behave.sim.symbolic import typecheck as types
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    proc = ir.Procedure(('cells',), ('a',), ('i',), (),
        ir.For('i', 2, ir.Store('cells', ir.Var('i'), ir.Op('and', ir.Var('a'), ir.Lit(3)))),
        initial={'cells': [0, 0]}, types={'cells': types.Array(2)},
        signature=ProcedureSignature((('a', FiniteType(10)),), ()))
    env = ranges.fixpoint(proc, {'a': ranges.Range(0, 1023)})
    template = StepTemplate(proc, env)
    step = template.encode()
    assert env['cells'].hi == 3
    assert all(term.size() == 3 for term in _leaves(step.pre['cells']))
    solver = z3.Solver()
    solver.add(step.constraint, z3.Or(*(
        left != right for left, right in (
            encoder.widen(term, step.pre['a'] & 3) for term in _leaves(step.state_post['cells'])))))
    assert solver.check() == z3.unsat
    # Compacting the state representation must not change the input domain.
    solver = z3.Solver()
    solver.add(step.constraint, step.pre['a'] == -1)
    assert solver.check() == z3.unsat


def test_template_does_not_build_unused_ssa_assertions(tmp_path, monkeypatch):
    from behave.sim.symbolic.exact_formula import FormulaDefinition

    machine, _ = _lower(tmp_path,
        'module M(input [3:0] a,output reg [3:0] q);'
        'always_comb begin q=a&3; if(q<4) q=q+1; end endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    plain = encoder.encode_step(proc, env=env, tag='comparison_proof')

    def unnecessary(*_args, **_kwargs):
        pytest.fail('a template materialized an unused SSA assertion')

    monkeypatch.setattr(FormulaDefinition, 'assertion', property(unnecessary))
    compiled = StepTemplate(proc, env).encode(plain.pre)
    differences = [compiled.ok != plain.ok, compiled.error != plain.error]
    for field in ('state_post', 'post', 'hits'):
        for name, value in getattr(plain, field).items():
            differences.extend(a != b for a, b in zip(_leaves(_tree(value)),
                _leaves(getattr(compiled, field)[name]), strict=True))
    solver = z3.Solver()
    solver.add(plain.constraint, compiled.constraint, z3.Or(*differences))
    assert solver.check() == z3.unsat


def test_deferred_relation_is_complete_when_requested(tmp_path):
    machine, _ = _lower(tmp_path,
        'module M(input [3:0] a,output reg [3:0] q);'
        'always_comb begin q=a+1; if(a==9) q=13; end endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    plain = encoder.encode_step(proc, env=env, tag='eager')
    delayed = encoder._encode_step(proc, pre=plain.pre, env=env, tag='delayed',
                                   _defer_constraint=True)
    assert delayed.definitions and delayed._constraint is None
    constraint = delayed.constraint
    assert constraint is delayed.constraint
    different = [plain.ok != delayed.ok, plain.error != delayed.error]
    for field in ('state_post', 'post', 'hits'):
        for name, value in getattr(plain, field).items():
            different.extend(a != b for a, b in zip(_leaves(_tree(value)),
                _leaves(_tree(getattr(delayed, field)[name])), strict=True))
    solver = z3.Solver()
    solver.add(plain.constraint, constraint, z3.Or(*different))
    assert solver.check() == z3.unsat


def test_shared_dag_edges_are_read_once_during_definition_expansion(tmp_path, monkeypatch):
    from z3 import z3core as native

    machine, _ = _lower(tmp_path,
        'module M(input [3:0] a,output reg [3:0] q);'
        'always_comb begin q=a+1; if(q==9) q=q+1; else q=q+2; end endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    encoded = []
    visits = Counter()
    original_encode, original_arg = step_template._encode_step, native.Z3_get_app_arg

    def capture(*args, **kwargs):
        raw = original_encode(*args, **kwargs)
        encoded.append(raw)  # Keep all original ASTs alive, including unexported ones.
        return raw

    def child(ctx, term, index):
        if encoded:
            visits[native.Z3_get_ast_id(ctx, term), index] += 1
        return original_arg(ctx, term, index)

    monkeypatch.setattr(step_template, '_encode_step', capture)
    monkeypatch.setattr(native, 'Z3_get_app_arg', child)
    template = StepTemplate(proc, env)
    assert template._image is not None
    assert len(visits) > 10
    assert set(visits.values()) == {1}


def test_inlining_updates_typed_applications_without_recoercing_arguments(tmp_path, monkeypatch):
    from z3 import z3core as native
    from z3.z3 import _to_expr_ref

    machine, _ = _lower(tmp_path,
        'module M(input [7:0] a,output reg [7:0] q);'
        'always_comb begin q={a[3:0],a[7:4]}; '
        'if(a[0]) q=(q<<1)^a; else q=q+3; end endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    encoded, updated = [], []
    original_encode = step_template._encode_step
    original_update, original_call = native.Z3_update_term, z3.FuncDeclRef.__call__

    def capture(*args, **kwargs):
        raw = original_encode(*args, **kwargs)
        encoded.append(raw)
        return raw

    def update(ctx, term, count, children):
        result = original_update(ctx, term, count, children)
        context = encoded[0].ok.ctx
        value = _to_expr_ref(result, context)
        source = _to_expr_ref(term, context)
        # The native operation must retain the original declaration, including
        # extract/extension parameters, not just agree on a few input values.
        assert value.eq(original_call(source.decl(), *(
            _to_expr_ref(children[index], context) for index in range(count))))
        updated.append(value)
        return result

    def call(declaration, *children):
        if encoded:
            # Only the final root container is a new function application.
            assert str(declaration.name()) == '_step_fields'
        return original_call(declaration, *children)

    monkeypatch.setattr(step_template, '_encode_step', capture)
    monkeypatch.setattr(native, 'Z3_update_term', update)
    monkeypatch.setattr(z3.FuncDeclRef, '__call__', call)
    template = StepTemplate(proc, env)
    assert template._image is not None
    assert len(updated) > 10


@pytest.mark.parametrize('private_context', [False, True])
def test_native_definition_expansion_preserves_shared_roots_and_owns_results(private_context):
    import gc

    from behave.sim.symbolic.exact_formula import FormulaDefinition

    context = z3.Context() if private_context else z3.main_ctx()
    a, b = z3.BitVecs('inline_a inline_b', 8, ctx=context)
    cond = z3.Bool('inline_cond', ctx=context)
    expected = z3.If(cond, z3.Extract(7, 0, z3.SignExt(4, a) << 2), b)

    def build():
        x, y = z3.BitVecs('inline_x inline_y', 8, ctx=context)
        flag = z3.Bool('inline_flag', ctx=context)
        definitions = [FormulaDefinition('x', x, z3.Extract(7, 0, z3.SignExt(4, a) << 2)),
                       FormulaDefinition('y', y, z3.If(cond, x, b)),
                       FormulaDefinition('flag', flag, y < 3)]
        return step_template._inline_definitions(
            [y, y + y, flag, z3.BoolVal(True, ctx=context), a], definitions)

    roots, _ = build()  # Original definitions and the temporary owner are gone.
    gc.collect()
    wanted = [expected, expected + expected, expected < 3, z3.BoolVal(True, ctx=context), a]
    assert all(actual.eq(value) for actual, value in zip(roots, wanted, strict=True))


def test_definition_expansion_is_iterative_for_a_deep_shared_chain():
    from behave.sim.symbolic.exact_formula import FormulaDefinition

    first = z3.BitVec('inline_chain_input', 16)
    current = first
    definitions = []
    for index in range(1500):
        successor = z3.BitVec(f'inline_chain_{index}', 16)
        definitions.append(FormulaDefinition(str(index), successor, current + 1))
        current = successor
    roots, _ = step_template._inline_definitions([current, current], definitions)
    assert roots[0].eq(roots[1])
    assert z3.is_true(z3.simplify(roots[0] == first + 1500))


@pytest.mark.parametrize('body', [
    'always_comb begin if(a==9) q=a+1; else q=a-1; end',
    'reg [3:0] cells[2:3]; always_comb begin cells[2]=a; cells[3]=a+1; q=cells[2+a[0]]; end',
    'always_comb begin q=0; if(a==9) $fatal(1,"stop"); if(a==3) q=a; end',
])
@pytest.mark.parametrize('subset', ['one', 'none', 'all'])
def test_hit_projection_keeps_every_other_root_and_exact_transition(tmp_path, body, subset):
    machine, _ = _lower(tmp_path,
        'module M(input [3:0] a,output reg [3:0] q);' + body + ' endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    template = StepTemplate(proc, env)
    names = tuple(template._layout['hits'])
    assert len(names) > 1
    chosen = names if subset == 'all' else names[:1] if subset == 'one' else ()
    view = template.project({name: (name,) for name in chosen})
    assert tuple(template._layout['hits']) == names  # The worker template is untouched.
    assert set(view._layout['hits']) == set(chosen)
    assert view._parameters is template._parameters

    def roots(image, shape):
        if isinstance(shape, dict):
            return tuple(root for value in shape.values() for root in roots(image, value))
        if isinstance(shape, list):
            return tuple(root for value in shape for root in roots(image, value))
        return (image.arg(shape),)

    retained = [root for field in template._layout.keys() - {'hits'}
                for root in roots(template._image, template._layout[field])]
    retained.extend(template._image.arg(template._layout['hits'][name]) for name in chosen)
    assert view._image.num_args() == len({term.get_id() for term in retained})

    for field in template._layout.keys() - {'hits'}:
        assert all(a.eq(b) for a, b in zip(
            roots(template._image, template._layout[field]),
            roots(view._image, view._layout[field]), strict=True))
    for name in chosen:
        assert template._image.arg(template._layout['hits'][name]).eq(
            view._image.arg(view._layout['hits'][name]))

    full = template.encode()
    selected = view.encode(full.pre)
    different = [full.ok != selected.ok, full.error != selected.error]
    for field in ('state_post', 'post'):
        for name, value in getattr(full, field).items():
            different.extend(a != b for a, b in zip(_leaves(value),
                _leaves(getattr(selected, field)[name]), strict=True))
    different.extend(full.hits[name] != selected.hits[name] for name in chosen)
    solver = z3.Solver()
    solver.add(full.constraint, selected.constraint, z3.Or(*different))
    assert solver.check() == z3.unsat
    with pytest.raises(KeyError):
        template.project({'missing': ('not-a-declared-hit',)})


def test_template_keeps_shared_roots_without_grounding_its_parameters(tmp_path, monkeypatch):
    machine, _ = _lower(tmp_path,
        'module M(input [7:0] a,output reg [7:0] q);'
        'always_comb begin q=(a+0)&255; if(a==9) q=q+1; end endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    simplify, calls, roots = z3.simplify, [], []
    inline = step_template._inline_definitions

    def capture(*args, **kwargs):
        expanded, dependencies = inline(*args, **kwargs)
        roots.extend(expanded)
        return expanded, dependencies

    def record(term, *args, **kwargs):
        if z3.is_app(term) and str(term.decl().name()) == '_step_fields':
            calls.append(term)
        return simplify(term, *args, **kwargs)

    monkeypatch.setattr(z3, 'simplify', record)
    monkeypatch.setattr(step_template, '_inline_definitions', capture)
    template = StepTemplate(proc, env)
    assert calls == []
    parameters = [term for value in template._parameters.values() for term in _leaves(value)]
    restored = z3.substitute_vars(template._image, *parameters)
    assert len(roots) == restored.num_args()
    # Native substitution can canonicalize operand ordering. The invariant is
    # equivalence for every root, not byte identity after a second simplifier.
    solver = z3.Solver()
    solver.add(z3.Or(*(root != restored.arg(i) for i, root in enumerate(roots))))
    assert solver.check() == z3.unsat
    # Two distinct symbolic instantiations still have independent input atoms.
    left, right = template.encode(), template.encode()
    assert calls == []  # Rewriting is deferred to the selected query's solver.
    name = next(driven for field, driven in machine.drive_inputs
                if machine.program.fields[field].port == 'a')
    solver = z3.Solver()
    solver.add(left.constraint, right.constraint, left.pre[name] == 9, right.pre[name] == 3)
    assert solver.check() == z3.sat


@pytest.mark.parametrize('body', [
    'always_comb begin q=0; for(integer i=0;i<3;i++) if(a==i) q=q+1; end',
    ('function automatic [3:0] f(input [3:0] x); if(x==9) f=1; else f=0; endfunction '
     'always_comb q=f(a)+f(a+1);'),
    'always_comb begin q=0; if(a==9) $fatal(1,"stop"); if(a==3) q=a; end',
])
def test_grouped_hits_preserve_occurrence_disjunctions_and_the_complete_transition(tmp_path, body):
    machine, _ = _lower(tmp_path, 'module M(input [3:0] a,output reg [3:0] q);' + body + ' endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    template = StepTemplate(proc, env)
    groups = {}
    for mark, _, goal in machine.occurrences:
        groups.setdefault(goal, []).append(mark)
    groups['all'] = list(template._layout['hits'])
    groups['repeated'] = groups['all'] * 3
    groups['none'] = []
    selected = template.project(groups)
    assert selected._parameters is template._parameters
    full = template.encode()
    grouped = selected.encode(full.pre)
    assert set(grouped.hits) == set(groups)
    different = [full.ok != grouped.ok, full.error != grouped.error]
    for field in ('state_post', 'post'):
        for name, value in getattr(full, field).items():
            different.extend(a != b for a, b in zip(_leaves(value),
                _leaves(getattr(grouped, field)[name]), strict=True))
    different.extend(grouped.hits[goal] != z3.Or(*(full.hits[mark] for mark in members))
                     for goal, members in groups.items())
    solver = z3.Solver()
    solver.add(full.constraint, grouped.constraint, z3.Or(*different))
    assert solver.check() == z3.unsat
    assert z3.is_false(z3.simplify(grouped.hits['none']))
    with pytest.raises(KeyError):
        template.project({'missing': ['not-a-declared-hit']})


def test_positional_template_handles_an_empty_boundary():
    proc = ir.Procedure((), (), (), (), ir.Mark('empty'))
    template = StepTemplate(proc, {})
    encoded = template.encode()
    assert encoded.pre == {}
    solver = z3.Solver()
    solver.add(encoded.constraint, z3.Or(z3.Not(encoded.ok), z3.Not(encoded.hits['empty'])))
    assert solver.check() == z3.unsat


def test_positional_template_preserves_nested_cells_signed_values_and_boolean_state():
    from behave.sim.symbolic import typecheck as types
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    proc = ir.Procedure(('cells', 'flag'), ('a', 'b'), (), (), ir._sequence((
        ir.Store('cells', ir.Lit(1), ir.Op('arraylit', ir.Var('a'), ir.Var('b'))),
        ir.If(ir.Var('flag'), ir.Mark('flag'), ir.Skip()))),
        types={'cells': types.Array(2, types.Array(2)), 'flag': types.BOOLEAN},
        initial={'cells': [[1, -2], [3, -4]], 'flag': True},
        signature=ProcedureSignature((('a', FiniteType(5, signed=True)),
                                      ('b', FiniteType(5, signed=True))), ()))
    env = ranges.fixpoint(proc, {'a': ranges.Range(-16, 15), 'b': ranges.Range(-16, 15),
                                'cells': ranges.Range(-16, 15), 'flag': ranges.Range(0, 1)})
    compiled = StepTemplate(proc, env).encode()
    plain = encoder.encode_step(proc, pre=compiled.pre, env=env)
    different = [compiled.ok != plain.ok, compiled.error != plain.error]
    for field in ('state_post', 'post', 'hits'):
        for name, value in getattr(plain, field).items():
            different.extend(a != b for a, b in zip(_leaves(_tree(value)),
                _leaves(getattr(compiled, field)[name]), strict=True))
    solver = z3.Solver()
    solver.add(plain.constraint, compiled.constraint, z3.Or(*different))
    assert solver.check() == z3.unsat
    # Formal template positions must never leak into an asserted instance.
    pending = [compiled.constraint, compiled.ok, compiled.error,
               *_leaves(compiled.state_post['cells']), compiled.state_post['flag']]
    seen = set()
    while pending:
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        assert not z3.is_var(term)
        pending.extend(term.children())


@pytest.mark.parametrize('body', [
    'always_comb begin q=0; for(integer i=0;i<3;i++) if(a==i) q=q+1; end',
    'reg [3:0] cells[2:3]; always_comb begin cells[2]=a; cells[3]=a+1; q=cells[2+a[0]]; end',
    'always_comb begin q=0; if(a==9) $fatal(1,"stop"); if(a==3) q=a; end',
])
@pytest.mark.parametrize('signed', ['', 'signed '])
def test_translated_template_preserves_all_roots_and_independent_contexts(tmp_path, body, signed):
    machine, _ = _lower(tmp_path,
        f'module M(input {signed}[3:0] a,output reg [3:0] q);' + body + ' endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    template = StepTemplate(proc, env)
    original = template.encode(tag='original')
    for _ in range(2):
        context = z3.Context()
        moved = template.translate(context)
        assert moved.context == context and template.context != context
        assert moved._image.translate(template.context).eq(template._image)
        # Projection and empty disjunctions must retain the same context too.
        moved = moved.project({**{name: (name,) for name in moved._layout['hits']}, 'empty': iter(())})
        copy = moved.encode(original.pre, tag='copy')
        assert copy.ok.ctx == context
        assert all(term.ctx == context for value in copy.pre.values() for term in _leaves(value))
        differences = [original.ok != copy.ok.translate(template.context),
                       original.error != copy.error.translate(template.context)]
        for field in ('state_post', 'post', 'hits'):
            for name, value in getattr(original, field).items():
                differences.extend(a != b.translate(template.context) for a, b in zip(_leaves(value),
                    _leaves(getattr(copy, field)[name]), strict=True))
        solver = z3.Solver(ctx=template.context)
        solver.add(original.constraint, copy.constraint.translate(template.context), z3.Or(*differences))
        assert solver.check() == z3.unsat
        assert z3.is_false(z3.simplify(copy.hits['empty']))
