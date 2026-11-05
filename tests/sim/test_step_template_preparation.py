"""Transition preparation shares the caller's deadline, never a new budget."""

from types import SimpleNamespace

import pytest
import z3

from behave.isolation.preparation_control import PreparationCancelled, preparation_scope
from behave.sim.symbolic import ir, ranges, step_template
from behave.sim.behavior.signature import ProcedureSignature
from behave.sim.core.contract import FiniteType


def procedure(loop=False):
    body = ir._sequence((ir.Assign('q', ir.Op('and', ir.Op('add', ir.Var('q'), ir.Var('a')),
                                             ir.Lit(15))), ir.Mark('updated')))
    return ir.Procedure(('q',), ('a',), ('i',) if loop else (), (),
        ir.For('i', 4, body) if loop else body, initial={'q': 0},
        signature=ProcedureSignature((('a', FiniteType(4)),), ()))


@pytest.mark.parametrize('stage', ['plan_encoding', '_encode_step', '_inline_definitions'])
def test_deadline_reaches_inside_each_construction_stage(monkeypatch, stage):
    target = getattr(step_template, stage)
    active, calls = False, 0

    def entered(*args, **kwargs):
        nonlocal active
        active = True
        try:
            return target(*args, **kwargs)
        finally:
            active = False

    def check():
        nonlocal calls
        if active:
            calls += 1
            if calls == 8:
                raise PreparationCancelled(stage)

    monkeypatch.setattr(step_template, stage, entered)
    proc, env = procedure(loop=True), {'q': ranges.Range(0, 15), 'a': ranges.Range(0, 15)}
    with preparation_scope(check), pytest.raises(PreparationCancelled, match=stage):
        step_template.StepTemplate(proc, env)
    assert calls == 8  # Stop inside the operation, not at the next outer phase.
    # The failed scope/template owns no surviving stop or partial result.
    encoded = step_template.StepTemplate(proc, env).encode()
    solver = z3.Solver()
    solver.add(encoded.constraint, encoded.ok)
    assert solver.check() == z3.sat


@pytest.mark.parametrize('definitions', [False, True])
def test_dag_inlining_cancels_without_losing_original_terms(definitions):
    x = z3.BitVec('cancel_dag_input', 8)
    terms = [x]
    for i in range(40):
        terms.append(terms[-1] + i)
    roots = [terms[-1]]
    owned = ()
    if definitions:
        name = z3.BitVec('cancel_dag_output', 8)
        roots, owned = [name], (SimpleNamespace(lhs=name, rhs=terms[-1]),)
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == 12:
            raise PreparationCancelled('dag')

    with preparation_scope(check), pytest.raises(PreparationCancelled, match='dag'):
        step_template._inline_definitions(roots, owned, (x,))
    expanded, dependencies = step_template._inline_definitions(roots, owned, (x,))
    assert z3.eq(expanded[0], terms[-1]) and dependencies == (1,)


def test_normalization_checks_stop_after_native_rewrite_and_preserves_owner(monkeypatch):
    template = step_template.StepTemplate(procedure(),
        {'q': ranges.Range(0, 15), 'a': ranges.Range(0, 15)})
    original, simplify, rewritten = template._image, z3.simplify, False

    def rewrite(*args, **kwargs):
        nonlocal rewritten
        result = simplify(*args, **kwargs)
        rewritten = True
        return result

    def check():
        if rewritten:
            raise PreparationCancelled('normalization')

    monkeypatch.setattr(z3, 'simplify', rewrite)
    with preparation_scope(check), pytest.raises(PreparationCancelled, match='normalization'):
        template.normalized()
    assert template._image is original
    assert template.normalized()._image is not None


@pytest.mark.parametrize('stage', ['simplify', 'substitute', 'substitute_vars'])
def test_scalar_binding_checks_stop_after_native_work_and_keeps_owner(monkeypatch, stage):
    template = step_template.StepTemplate(procedure(),
        {'q': ranges.Range(0, 15), 'a': ranges.Range(0, 15)}).normalized()
    original, native, returned = template._image, getattr(z3, stage), False

    def completed(*args, **kwargs):
        nonlocal returned
        value = native(*args, **kwargs)
        returned = True
        return value

    def check():
        if returned:
            raise PreparationCancelled('scalar binding')

    with monkeypatch.context() as patch:
        patch.setattr(z3, stage, completed)
        with preparation_scope(check), pytest.raises(PreparationCancelled, match='scalar binding'):
            template.instantiate({'a': z3.BitVecVal(1, template._parameters['a'].size())})
    assert template._image is original and template._array_image is None
    assert template.instantiate().constraint is not None


def test_expired_scope_stops_before_ir_validation(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('expired preparation entered IR validation')

    def stop():
        raise PreparationCancelled('already expired')

    monkeypatch.setattr(step_template, '_procedure_validation', unexpected)
    with preparation_scope(stop), pytest.raises(PreparationCancelled, match='already expired'):
        step_template.StepTemplate(procedure(), {})


@pytest.mark.parametrize('selected', [False, True])
@pytest.mark.parametrize('wrong_owner', [False, True])
def test_template_rejects_foreign_or_noncompact_validation(selected, wrong_owner):
    from behave.sim.symbolic.validation import _procedure_validation

    proc = procedure()
    validation = _procedure_validation(procedure() if wrong_owner else proc,
                                       compact_arrays=wrong_owner)
    with pytest.raises(TypeError, match='another procedure|compact-array'):
        if selected:
            step_template.StepTemplate.selected(proc, {}, (), _validation=validation)
        else:
            step_template.StepTemplate(proc, {}, _validation=validation)
