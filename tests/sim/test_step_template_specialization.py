"""Fixed inputs remain explicit premises, including across projection/contexts."""

import pytest
import z3

from behave.sim.symbolic import encoder, ir, ranges, typecheck as types
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.symbolic.step_template import StepTemplate, _leaves
from behave.sim.core.contract import FiniteType
from behave.sim.core.value_type import ValueType

pytestmark = pytest.mark.solver


def _template():
    proc = ir.Procedure(('q',), ('mode', 'x', 'pair'), (), (), ir._sequence((
        ir.If(ir.Op('eq', ir.Var('mode'), ir.Lit(0)), ir.Assign('q', ir.Var('x')),
              ir._sequence((
                  ir.If(ir.Op('eq', ir.Var('x'), ir.Lit(-3)), ir.Fail('RTLFatal', 'failure'), ir.Skip()),
                  ir.Assign('q', ir.Op('xor', ir.Var('q'), ir.Sel(ir.Var('pair'), ir.Lit(1)))),
                  ir.Mark('scheduled')))),
        ir.If(ir.Op('eq', ir.Var('q'), ir.Lit(2)), ir.Mark('two'), ir.Skip()),
    )), initial={'q': 0}, types={'pair': types.Array(2)},
        signature=ProcedureSignature((('mode', FiniteType(2)), ('x', FiniteType(4, signed=True)),
                                      ('pair', ValueType.array(2, FiniteType(4, signed=True)))), ()))
    env = {'mode': ranges.Range(0, 3), 'x': ranges.Range(-8, 7), 'pair': ranges.Range(-8, 7),
           'q': ranges.Range(-128, 127)}
    return StepTemplate(proc, env)


def _same_fixed_relation(template, view, fixed):
    context = template.context
    arguments = [z3.FreshConst(term.sort()) for shape in template._parameters.values()
                 for term in _leaves(shape)]
    original = z3.substitute_vars(template._image, *arguments)
    specialized = z3.substitute_vars(view._image, *arguments)
    kinds = dict(template.procedure.semantics.input_types)
    expected, offset = [], 0
    for name, shape in template._parameters.items():
        leaves = tuple(_leaves(shape))
        if name in fixed:
            values = encoder.encode_concrete_value(fixed[name], template.environment[name], name, kind=kinds[name])
            for parameter, variable, value in zip(leaves, arguments[offset:offset + len(leaves)],
                                                    _leaves(values), strict=True):
                value = value if value.ctx == context else value.translate(context)
                expected.append(variable == z3.SignExt(parameter.size() - value.size(), value))
        offset += len(leaves)
    bound = z3.And(z3.BoolVal(True, ctx=context), *expected)
    ci = template._layout['constraint']
    solver = z3.Solver(ctx=context)
    solver.add(specialized.arg(ci) != z3.And(original.arg(ci), bound))
    assert solver.check() == z3.unsat
    solver = z3.Solver(ctx=context)
    solver.add(original.arg(ci), bound)
    assert solver.check() == z3.sat  # the comparison is not vacuous
    solver.add(z3.Or(*(original.arg(index) != specialized.arg(index)
                      for index in range(original.num_args()) if index != ci)))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('fixed', [{}, {'mode': 0}, {'mode': 1}, {'mode': 0, 'x': -3}, {'pair': [-3, 2]}])
@pytest.mark.parametrize('private_context', [False, True])
def test_every_root_and_complete_input_language_are_equivalent(fixed, private_context):
    template = _template()
    if private_context:
        template = template.translate(z3.Context())
    view = template.specialize_inputs(fixed)
    _same_fixed_relation(template, view, fixed)
    # Specialization can precede or follow grouping/dependency-closed projection.
    options = {'outputs': (), 'state_reads': ()}
    projected = template.project({'any': ('two', 'scheduled')}, **options)
    _same_fixed_relation(projected, view.project({'any': ('two', 'scheduled')}, **options), fixed)
    _same_fixed_relation(projected, projected.specialize_inputs(fixed), fixed)


def test_fixed_premise_survives_free_inputs_conflicts_and_respecialization():
    template = _template()
    fixed = template.specialize_inputs({'mode': 0})
    encoded = fixed.encode()
    solver = z3.Solver()
    solver.add(encoded.constraint)
    assert solver.check() == z3.sat
    solver.push()
    solver.add(encoded.pre['mode'] != 0)
    assert solver.check() == z3.unsat
    solver.pop()
    conflicting = fixed.encode({'mode': z3.BitVecVal(1, 3)})
    solver = z3.Solver()
    solver.add(conflicting.constraint)
    assert solver.check() == z3.unsat
    conflicting = fixed.specialize_inputs({'mode': 1}).encode()
    solver = z3.Solver()
    solver.add(conflicting.constraint)
    assert solver.check() == z3.unsat
    _same_fixed_relation(template, fixed.specialize_inputs({'x': -3}), {'mode': 0, 'x': -3})


def test_only_declared_typed_input_values_can_be_specialized():
    template = _template()
    for values in ({'q': 0}, {'missing': 0}):
        with pytest.raises(ValueError, match='binds inputs'):
            template.specialize_inputs(values)
    for values in ({'x': 8}, {'mode': True}, {'pair': [2]}):
        with pytest.raises(encoder.EncodingError):
            template.specialize_inputs(values)


@pytest.mark.parametrize('private_context', [False, True])
def test_omitted_specialized_input_is_bound_without_a_model_guess(private_context):
    view = _template().specialize_inputs({'mode': 1, 'pair': [-3, 2]}).normalized()
    if private_context:
        view = view.translate(z3.Context())
    view = view.project({'any': ('two', 'scheduled')}, outputs=(), state_reads=())
    encoded = view.encode()
    assert z3.is_bv_value(encoded.pre['mode'])
    assert encoded.pre['mode'].as_long() == 1
    assert [cell.as_signed_long() for cell in encoded.pre['pair']] == [-3, 2]
    assert all(cell.ctx == view.context for cell in _leaves(encoded.pre['pair']))
    wrong = view.encode({'mode': z3.BitVecVal(0, 3, ctx=view.context)})
    solver = z3.Solver(ctx=view.context)
    solver.add(wrong.constraint)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('go', [False, True])
def test_boolean_and_nested_inputs_keep_recursive_type_and_binding(go):
    kind = types.Array(2, types.Array(2, types.BOOLEAN))
    domain = ValueType.array(2, ValueType.array(2, ValueType.boolean()))
    proc = ir.Procedure((), ('go', 'flags'), (), (),
        ir.If(ir.Op('land', ir.Var('go'), ir.Sel(ir.Sel(ir.Var('flags'), ir.Lit(1)), ir.Lit(0))),
              ir.Mark('target'), ir.Skip()),
        types={'go': types.BOOLEAN, 'flags': kind},
        signature=ProcedureSignature((('go', ValueType.boolean()), ('flags', domain)), ()))
    template = StepTemplate(proc, {'go': ranges.Range(0, 1), 'flags': ranges.Range(0, 1)})
    fixed = {'go': go, 'flags': [[False, True], [True, False]]}
    view = template.specialize_inputs(fixed)
    _same_fixed_relation(template, view, fixed)
    _same_fixed_relation(template.project({}, outputs=()), view.project({}, outputs=()), fixed)
    with pytest.raises(encoder.EncodingError):
        template.specialize_inputs({'go': int(go)})
    with pytest.raises(encoder.EncodingError):
        template.specialize_inputs({'flags': [[False, True], [1, False]]})


@pytest.mark.eda('slang')
@pytest.mark.parametrize('body', [
    'always_comb if(a==3) q=a; else q=0;',
    'always @(posedge clk) q<=a; always @(negedge clk) if(a==q) r=1; else r=0;',
    'always @(posedge clk) q=a; always @(posedge clk) if(q==a) r=1; else r=0;',
    ('reg pulse; always @(posedge clk) begin pulse<=a[0]; pulse<=0; end '
     'always @(posedge pulse) if(a==3) q=1; else q=0;'),
    'always @(posedge clk) begin if(a==3) $fatal(1,"stop"); q<=a; end',
])
def test_source_drive_preserves_every_root_for_all_states_not_only_quiescent_ones(tmp_path, body):
    from .test_rtl_source_scheduler import _lower

    machine, _ = _lower(tmp_path,
        f'module M(input clk,input [1:0] a,output reg [1:0] q,r); {body} endmodule')
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    template = StepTemplate(proc, env)
    _same_fixed_relation(template, template.specialize_inputs({'action': 0}), {'action': 0})
