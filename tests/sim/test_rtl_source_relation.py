"""Symbolic source histories are not fixed baseline states or settled signals."""

from dataclasses import replace
from itertools import product

import pytest
import z3

from behave.sim.symbolic import ranges
from behave.sim.symbolic.encoder import encode_concrete_value
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_relation import SourceIntervalTemplate

from .test_rtl_source_event_bounds import _histories
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _state

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


@pytest.mark.parametrize('normalized', (False, True))
@pytest.mark.parametrize('specialized', (False, True))
def test_local_observation_cannot_replace_history_state(normalized, specialized, monkeypatch):
    from behave.sim.symbolic import ir, step_template
    from behave.sim.core.contract import FiniteType
    from behave.sim.symbolic.procedure_contract import ProcedureSignature

    proc = ir.Procedure(('x',), ('action',), (), (), ir.Seq(
        ir.If(ir.Op('eq', ir.Var('x'), ir.Var('action')), ir.Mark('hit'), ir.Skip()),
        ir.Assign('x', ir.Var('action'))), initial={'x': 0},
        signature=ProcedureSignature((('action', FiniteType(2)),), ()))
    owner = SourceIntervalTemplate(proc, dict.fromkeys(('x', 'action'), ranges.Range(0, 3)))
    if normalized:
        owner = owner.normalized()
    if specialized:
        owner = owner.specialize_inputs({'action': 1})
    local = owner.local_observations(('hit',))
    assert local.state_names == () and owner._marks is None and owner._source is None
    history = owner.observations(('hit',))
    assert history.state_names == ('x',)
    def cannot_rebuild(*args, **kwargs):
        pytest.fail('an existing history already owns the local roots')
    with monkeypatch.context() as patch:
        patch.setattr(step_template.StepTemplate, 'selected', cannot_rebuild)
        warm = owner.local_observations(('hit',))
    assert warm.state_names == () and owner.observations(('hit',)) is history
    cold = local.encode()
    for template in (warm, history):
        encoded = template.encode(cold.pre)
        solver = z3.Solver()
        solver.add(cold.constraint, encoded.constraint)
        assert solver.check() == z3.sat
        solver.add(cold.hits['hit'] != encoded.hits['hit'])
        assert solver.check() == z3.unsat
    if specialized:
        wrong = local.encode({'action': z3.BitVecVal(2, local._parameters['action'].size())})
        solver = z3.Solver()
        solver.add(wrong.constraint)
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('normalized', [False, True])
def test_specialized_source_views_share_demanded_encoding_without_forcing_full_image(monkeypatch, normalized):
    from behave.sim.symbolic import ir, step_template
    from behave.sim.core.contract import FiniteType
    from behave.sim.symbolic.procedure_contract import ProcedureSignature

    proc = ir.Procedure(('x', 'y'), ('action',), (), (), ir.Seq(
        ir.If(ir.Op('eq', ir.Var('x'), ir.Var('action')), ir.Mark('x'), ir.Skip()),
        ir.If(ir.Op('eq', ir.Var('y'), ir.Lit(2)), ir.Mark('y'), ir.Skip())),
        initial={'x': 0, 'y': 0}, signature=ProcedureSignature((('action', FiniteType(2)),), ()))
    calls, initialize = [], step_template.StepTemplate.__init__

    def counted(self, procedure, env, **kwargs):
        calls.append(procedure)
        initialize(self, procedure, env, **kwargs)

    monkeypatch.setattr(step_template.StepTemplate, '__init__', counted)
    owner = SourceIntervalTemplate(proc, dict.fromkeys((*proc.state, 'action'), ranges.Range(0, 3)))
    views = tuple(owner.specialize_inputs({'action': value}) for value in (0, 1))
    if normalized:
        views = tuple(view.normalized() for view in views)
    assert not calls
    for value, view in enumerate(views):
        observed = view.observations(('x',))
        assert observed is view.observations(('x',))
        assert observed.state_names == ('x',)
        assert 'y' not in observed.hit_names
        assert len(calls) == 1
        assert owner._source is None and view._source is None
        encoded = observed.instantiate()
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.hits['x'] != (encoded.pre['x'] == value))
        assert solver.check() == z3.unsat
        incompatible = observed.instantiate({'action': z3.BitVecVal(2, encoded.pre['action'].size())})
        solver = z3.Solver()
        solver.add(incompatible.constraint)
        assert solver.check() == z3.unsat

    # A broader request and a complete consumer are not trapped in the first
    # cone. The original owner and the sibling's prepared image stay valid.
    assert 'y' in views[0].observations(('y',)).hit_names
    assert len(calls) == 2
    assert 'y' in views[1].source.hit_names and len(calls) == 3
    conflicting = views[0].specialize_inputs({'action': 1}).observations(('x',)).instantiate()
    solver = z3.Solver()
    solver.add(conflicting.constraint)
    assert solver.check() == z3.unsat
    with pytest.raises(ValueError, match='inputs, not state'):
        owner.specialize_inputs({'x': 0})


@pytest.mark.parametrize('normalized', [False, True])
def test_source_owner_reuses_validation_but_checks_each_new_projection(monkeypatch, normalized):
    from behave.sim.symbolic import ir, step_template
    from behave.sim.core.contract import FiniteType
    from behave.sim.symbolic import validation
    from behave.sim.symbolic.procedure_contract import ProcedureSignature

    proc = ir.Procedure(('x', 'y'), ('action',), (), (), ir.Seq(
        ir.If(ir.Op('eq', ir.Var('x'), ir.Lit(1)), ir.Mark('x'), ir.Skip()),
        ir.If(ir.Op('eq', ir.Var('y'), ir.Lit(2)), ir.Mark('y'), ir.Skip())),
        initial={'x': 0, 'y': 0}, signature=ProcedureSignature((('action', FiniteType(2)),), ()))
    checked, validate = [], validation._procedure_validation
    def record(procedure, **kwargs):
        checked.append(procedure)
        return validate(procedure, **kwargs)
    monkeypatch.setattr(validation, '_procedure_validation', record)
    monkeypatch.setattr(step_template, '_procedure_validation', record)
    owner = SourceIntervalTemplate(proc, dict.fromkeys((*proc.state, 'action'), ranges.Range(0, 3)))
    if normalized:
        owner = owner.normalized()
    first = owner.observations(('x',))
    assert owner.observations(('x',)) is first
    second = owner.observations(('y',))
    assert second is not first
    assert len(checked) == 3 and checked[0] is proc
    assert all(projection is not proc for projection in checked[1:])
    assert checked[1] is not checked[2]
    # Full and drive images share the original owner's validation too, even
    # after a different selected image was prepared. No global IR cache.
    assert owner.source and owner.drive and len(checked) == 3


@pytest.mark.parametrize('normalized', [False, True])
def test_source_groups_reuse_controlled_observations_but_keep_query_views_separate(
        tmp_path, monkeypatch, normalized):
    from behave.sim.symbolic.step_template import StepTemplate

    rtl = '''module M(input [3:0] a, output reg [3:0] q);
        always_comb if (a == 7) q = 1; else q = 2;
        endmodule'''
    machine, plan = _lower(tmp_path, rtl)
    proc = machine.procedure
    template = SourceIntervalTemplate(proc, {**proc.state_ranges, **machine.input_ranges})
    if normalized:
        template = template.normalized()
    yes, no = (_goal(rtl, plan, 'a == 7', event) for event in ('true', 'false'))
    selected, calls = StepTemplate.selected.__func__, []

    def record(cls, *args, **kwargs):
        calls.append(args[2])
        return selected(cls, *args, **kwargs)

    monkeypatch.setattr(StepTemplate, 'selected', classmethod(record))
    first = template.relation(machine, (yes,))
    second = template.relation(machine, (no,))
    again = template.relation(machine, (yes,))
    assert len(calls) == 1
    assert first.goal_names == again.goal_names == (yes,)
    assert second.goal_names == (no,)
    assert no not in first.source.hit_names and yes not in second.source.hit_names
    assert template._source is None  # Never eagerly encode the whole scheduler.
    private = template.relation(machine, (yes,), context=z3.Context())
    assert private.source.context != first.source.context and len(calls) == 1
    for before, after in ((first.source, again.source), (first.drive, again.drive)):
        left = before.encode()
        right = after.encode(left.pre)
        solver = z3.Solver(ctx=before.context)
        solver.add(left.constraint, right.constraint, z3.Or(
            left.hits[yes] != right.hits[yes], left.ok != right.ok, left.error != right.error))
        assert solver.check() == z3.unsat


def test_observation_reuse_replaces_only_complete_images_and_respects_boundary(monkeypatch):
    from behave.isolation.preparation_control import PreparationCancelled
    from behave.sim.symbolic import ir, step_template
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    proc = ir.Procedure(('x', 'y'), ('a',), (), (), ir.Seq(
        ir.If(ir.Op('eq', ir.Var('x'), ir.Var('a')), ir.Mark('x'), ir.Mark('not_x')),
        ir.If(ir.Op('eq', ir.Var('y'), ir.Var('a')), ir.Mark('y'), ir.Skip())),
        initial={'x': 0, 'y': 0}, signature=ProcedureSignature((('a', FiniteType(4)),), ()))
    owner = SourceIntervalTemplate(proc, dict.fromkeys(('x', 'y', 'a'), ranges.Range(0, 15)))
    first = owner.observations(('x',))
    assert owner.observations(('not_x',)) is first and 'y' not in first.hit_names
    def cancel(*args, **kwargs):
        raise PreparationCancelled('different cone')
    with monkeypatch.context() as patch:
        patch.setattr(step_template.StepTemplate, 'selected', cancel)
        with pytest.raises(PreparationCancelled, match='different cone'):
            owner.observations(('y',))
        assert owner.observations(('x',)) is first
    different = owner.observations(('x',), state_reads=('y',))
    assert different is not first and 'y' in different.state_names
    assert owner.observations(('x',)) is different
    assert owner.observations(('y',)) is not different
    assert owner.normalized()._marks is None


@pytest.mark.parametrize('normalized', [False, True])
def test_observation_superset_reuses_only_available_roots(monkeypatch, normalized):
    from behave.sim.symbolic import ir, step_template
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    proc = ir.Procedure(('x', 'y', 'z'), ('a',), (), ('q', 'r'), ir.Seq(
        ir.Seq(ir.Assign('x', ir.Op('xor', ir.Var('x'), ir.Var('a'))),
               ir.Assign('q', ir.Var('x'))),
        ir.Seq(ir.Assign('r', ir.Var('y')),
            ir.If(ir.Op('eq', ir.Var('x'), ir.Var('y')), ir.Mark('yes'), ir.Mark('no')))),
        initial={'x': 0, 'y': 0, 'z': 0}, signature=ProcedureSignature(
            (('a', FiniteType(4)),), (('q', FiniteType(4)), ('r', FiniteType(4)))))
    env = dict.fromkeys(('x', 'y', 'z', 'a'), ranges.Range(0, 15))
    owner = SourceIntervalTemplate(proc, env)
    if normalized:
        owner = owner.normalized()
    broad = owner.observations(('yes',), outputs=('q', 'r'), state_reads=('y',))
    cold = SourceIntervalTemplate(proc, env)
    if normalized:
        cold = cold.normalized()
    expected = cold.observations(('no',), outputs=('q',), state_reads=('x',))

    def cannot_rebuild(*args, **kwargs):
        raise AssertionError('a covered boundary must not rebuild')

    with monkeypatch.context() as patch:
        patch.setattr(step_template.StepTemplate, 'selected', cannot_rebuild)
        for outputs, reads in [(('q',), ('x',)), (('r', 'q'), ('y',)), ((), ())]:
            assert owner.observations(('no',), outputs=outputs, state_reads=reads) is broad
        # Same selected roots as a cold construction, including success/error.
        projected = broad.project({'no': ('no',)}, outputs=('q',), state_reads=('x',))
        left = expected.encode()
        right = projected.encode(left.pre)
        solver = z3.Solver(ctx=left.constraint.ctx)
        solver.add(left.constraint, right.constraint, z3.Or(
            left.hits['no'] != right.hits['no'], left.post['q'] != right.post['q'],
            left.state_post['x'] != right.state_post['x'],
            left.ok != right.ok, left.error != right.error))
        assert solver.check() == z3.unsat
    # A new required state root is not silently served by the old projection.
    assert 'z' not in broad.state_names
    wider = owner.observations(('no',), outputs=('q',), state_reads=('z',))
    assert wider is not broad and 'z' in wider.state_names


@pytest.mark.parametrize('private', [False, True])
def test_literal_events_keep_keys_and_exact_guards_without_repeated_false_nodes(monkeypatch, private):
    from behave.sim.rtl import symbolic_state
    from behave.sim.rtl.source_relation import _accumulate_guarded_hits

    ctx = z3.Context() if private else z3.main_ctx()
    alive, hit = z3.Bools('event_alive event_hit', ctx=ctx)
    false, true = z3.BoolVal(False, ctx=ctx), z3.BoolVal(True, ctx=ctx)
    rows = ({'never': false, 'always': true, 'conditional': hit},
            {'never': false, 'always': false, 'conditional': z3.Not(hit)})
    original, calls = symbolic_state.assertion_conjunction, []
    def conjoin(terms, **kwargs):
        terms = tuple(terms)
        calls.append(terms)
        assert not any(z3.is_false(term) or z3.is_true(term) for term in terms)
        return original(terms, **kwargs)
    monkeypatch.setattr(symbolic_state, 'assertion_conjunction', conjoin)
    result = {}
    for row in rows:
        _accumulate_guarded_hits(result, alive, row)
    assert result['never'] == [] and result['always'] == [alive]
    assert len(calls) == 2 and set(result) == set(rows[0])
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Or(*(z3.Or(*values) != z3.Or(*(z3.And(alive,row[name]) for row in rows))
                      for name,values in result.items() if values)))
    assert solver.check() == z3.unsat
    # A false event still exists in the catalogue; empty aggregation must use
    # the owner's context, not the default context of a zero-argument Or.
    never = z3.BoolVal(False, ctx=ctx)
    assert never.ctx == ctx
    assert z3.is_true(z3.simplify(never == z3.Or(*(z3.And(alive,row['never']) for row in rows))))


@pytest.mark.parametrize('private_context', [False, True])
def test_control_folding_preserves_atoms_without_rewriting_their_arithmetic(monkeypatch, private_context):
    from behave.sim.rtl.source_relation import _fold_control

    ctx = z3.Context() if private_context else None
    a, b = z3.Bools('a b', ctx=ctx)
    x, y = z3.BitVecs('x y', 32, ctx=ctx)
    atom = z3.UDiv(x * y + x, y | 1) == 17
    terms = (z3.And(True, a, atom), z3.Or(atom, z3.Not(atom)),
             z3.If(a, z3.And(atom, b), z3.Not(atom)),
             z3.Implies(a, z3.Xor(b, atom)), x == x,
             z3.BitVecVal(3, 4, ctx=ctx) != z3.BitVecVal(2, 4, ctx=ctx))
    simplify = z3.simplify
    calls = []

    def control_only(term, *args, **kwargs):
        pending = [term]
        while pending:
            node = pending.pop()
            assert not z3.is_bv(node) or z3.is_bv_value(node)
            pending.extend(node.children())
        calls.append(term)
        return simplify(term, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(z3, 'simplify', control_only)
        folded = tuple(_fold_control(term) for term in terms)
    assert calls
    assert z3.is_true(folded[1]) and z3.is_true(folded[4]) and z3.is_true(folded[5])
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Or(*(left != right for left, right in zip(terms, folded, strict=True))))
    assert solver.check() == z3.unsat
    # Error tags share the same control-definition path but are bit-vectors.
    error = z3.If(a, z3.If(False, x * y, z3.BitVecVal(3, 32, ctx=ctx)), x * y)
    folded_error = _fold_control(error)
    solver = z3.Solver(ctx=ctx)
    solver.add(error != folded_error)
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private_context', [False, True])
def test_control_folding_does_not_open_irrelevant_conditions(monkeypatch, private_context):
    from behave.sim.rtl.source_relation import _fold_control

    ctx = z3.Context() if private_context else None
    a, b, unused = z3.Bools('a b unused', ctx=ctx)
    x = z3.BitVec('x', 32, ctx=ctx)
    expensive = z3.And(unused, x * x == 17)
    terms = (
        z3.If(expensive, z3.And(a, True), z3.If(b, a, a)),
        z3.If(expensive, z3.If(b, x, x), x),
        z3.If(False, expensive, a),
        z3.If(True, x, z3.If(expensive, x + 1, x + 2)),
    )
    children = z3.ExprRef.children
    def visited(term):
        assert not term.eq(expensive), 'an irrelevant condition was expanded'
        return children(term)
    with monkeypatch.context() as patch:
        patch.setattr(z3.ExprRef, 'children', visited)
        folded = tuple(_fold_control(term) for term in terms)
    assert all(actual.eq(expected) for actual, expected in zip(folded, (a, x, a, x), strict=True))
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Or(*(left != right for left, right in zip(terms, folded, strict=True))))
    assert solver.check() == z3.unsat


def test_control_folding_retains_a_live_condition():
    from behave.sim.rtl.source_relation import _fold_control

    a, b = z3.Bools('a b')
    x, y = z3.BitVecs('x y', 32)
    terms = (z3.If(a, z3.And(b, True), z3.Not(b)), z3.If(a, x, y))
    solver = z3.Solver()
    solver.add(z3.Or(*(term != _fold_control(term) for term in terms)))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('depth', [8, 256])
def test_simultaneous_controls_share_one_guard_walk_without_cross_call_facts(monkeypatch, private, depth):
    from collections import Counter
    from behave.sim.rtl.source_relation import _fold_control, _fold_controls

    ctx = z3.Context() if private else z3.main_ctx()
    alive, shared = z3.Bools('shared_alive shared_guard', ctx=ctx)
    for i in range(depth):
        gate = z3.Bool(f'control_gate_{i}', ctx=ctx)
        shared = z3.If(gate, shared, z3.Not(shared))
    x, y = z3.BitVecs('shared_x shared_y', 32, ctx=ctx)
    terms = (z3.If(shared, x * y, x + y), z3.And(alive, shared))
    children = z3.ExprRef.children
    visits = Counter()
    def counted(term):
        visits[term.get_id()] += 1
        return children(term)
    with monkeypatch.context() as patch:
        patch.setattr(z3.ExprRef, 'children', counted)
        separate = tuple(_fold_control(term) for term in terms)
        old_visits = visits.copy()
        visits.clear()
        together = _fold_controls(terms)
    assert 2 * visits[shared.get_id()] == old_visits[shared.get_id()]
    assert sum(visits.values()) < sum(old_visits.values())
    # A suspended parent must not decode its immutable children again when
    # an arm or guard returns. Shared DAGs cost one structural read per node.
    assert max(visits.values()) == 1
    assert _fold_controls(()) == ()
    # Changed liveness and a negated guard must remain independent on the
    # next call. No proxy, observed value or previous answer is a premise.
    changed = (z3.Not(terms[1]), z3.If(alive, terms[0], x))
    folded = (*together, *_fold_controls(changed))
    solver = z3.Solver(ctx=ctx)
    solver.add(z3.Or(*(a != b for a, b in zip((*terms, *changed), folded, strict=True)),
                     *(a != b for a, b in zip(separate, together, strict=True))))
    assert solver.check() == z3.unsat


def test_source_goal_selection_precedes_full_scheduler_planning(tmp_path, monkeypatch):
    from behave.sim.symbolic import encoder, ir, plan

    rtl = '''module M(input [3:0] a, output reg [7:0] q);
        integer i, j;
        always_comb begin
            q = 0;
            for (i=0; i<16; i=i+1)
                for (j=0; j<16; j=j+1) q = q + a;
            if (a == 7) begin end
        end
        endmodule'''
    machine, observed = _lower(tmp_path, rtl)
    proc = machine.procedure
    env = {**proc.state_ranges, **machine.input_ranges}
    # Select the final input-only conditional, not the arithmetic loop goals.
    goal = _goal(rtl, observed, 'a == 7')
    selected_marks = {mark for mark, _, name in machine.occurrences if name == goal}
    assert selected_marks <= ir.goals_of(proc.body)
    monkeypatch.setattr(plan, 'TERM_BUDGET', 300)
    monkeypatch.setattr(encoder, 'TERM_BUDGET', 300)
    template = SourceIntervalTemplate(proc, env).normalized()
    assert template._source is None
    selected = template.relation(machine, (goal,))
    assert selected.goal_names == (goal,)
    assert template._source is None  # No unused whole-scheduler encoding.
    assert template.relation(machine, (goal,)) is selected
    with pytest.raises(encoder.EncodingError, match='encoding-expansion'):
        template.relation(machine, (goal,), complete_state=True)
    assert template.relation(machine, (goal,)) is selected


@pytest.mark.parametrize('private_context', [False, True])
def test_interval_continuation_reuses_only_its_own_immutable_condition(monkeypatch, private_context):
    from behave.sim.rtl.source_relation import SourceIntervalEncoding

    context = z3.Context() if private_context else None
    entered, alive, quiet = z3.Bools('entered alive quiet', ctx=context)
    interval = SourceIntervalEncoding((), {}, {}, {}, {}, (), alive,
        z3.BitVecVal(0, 8, ctx=context), {}, quiet, entered)
    calls, simplify = [], z3.simplify

    def counted(term, *args, **kwargs):
        calls.append(term)
        return simplify(term, *args, **kwargs)

    monkeypatch.setattr(z3, 'simplify', counted)
    result = interval.continuable
    assert all(interval.continuable is result for _ in range(20))
    assert len(calls) == 1
    cut = replace(interval, alive=z3.BoolVal(False, ctx=context))
    assert z3.is_false(cut.continuable)
    assert len(calls) == 2
    assert interval.continuable is result
    solver = z3.Solver(ctx=context)
    solver.add(result != z3.And(entered, alive, quiet))
    assert solver.check() == z3.unsat


def _relation(machine, state, goals):
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    relation = SourceIntervalTemplate(proc, env).relation(machine, goals, complete_state=True)
    kinds = dict(proc.semantics.state_types)
    pre = {name: encode_concrete_value(value, env[name], name, kind=kinds[name])
           for name, value in state.items()}
    return relation, pre, env


def _pins(machine, env, values):
    kinds = dict(machine.procedure.semantics.input_types)
    return {name: encode_concrete_value(values[name], env[driven], driven, kind=kinds[driven])
            for name, driven in machine.drive_inputs}


@pytest.mark.parametrize('memory', (False, True))
@pytest.mark.parametrize('held_boundary', (False, True))
def test_generated_member_layers_do_not_rediscover_source_history(tmp_path, monkeypatch, memory, held_boundary):
    from types import SimpleNamespace
    from behave.sim.rtl import monitor_template
    from behave.sim.rtl.source_member import SourceMemberPlan, source_member_hint

    body = ('logic [2:0] cells[0:1]; always_comb begin '
            'cells[0]=a; cells[1]=7; q=cells[a==3]; end' if memory else
            "always_comb q=a+1'b1;")
    machine, _ = _lower(tmp_path, 'module M(input [2:0] a, output logic [2:0] q);'
                        + body + ' endmodule')
    values = {'a': 0, 'q': 0, **({'cells': [0, 0]} if memory else {})}
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, values), goals)
    pins = _pins(machine, env, {name: 3 for name, _ in machine.drive_inputs})
    enabled = z3.Bool('member_enabled') if held_boundary else None
    phase = relation.encode(state, pins, event_bound=machine.event_bound,
                            enabled=enabled, require_quiescence=False)
    assert len(phase.member_layers) == len(phase.step_definitions)
    for groups, original in zip(phase.member_layers, phase.step_definitions):
        flattened = tuple(term for group in groups for term in group)
        assert len(flattened) == len(original)
        assert all(left.eq(right) for left, right in zip(flattened, original))
    def forbidden(*args, **kwargs):
        pytest.fail('a generated source interval already owns its completion order')
    monkeypatch.setattr(monitor_template, '_definition_layers', forbidden)
    model = z3.Model()
    if enabled is not None:
        model.update_value(enabled.decl(), z3.BoolVal(True))
    roots = (*phase.constraints, phase.state[_names(machine)['q']] == (7 if memory else 4))
    history = SimpleNamespace(member_plan=SourceMemberPlan((), (phase,)))
    completed = source_member_hint(model, (history,), roots, check=lambda: None)
    assert completed is not None
    assert all(z3.is_true(completed.eval(term, model_completion=False)) for term in roots)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('depth', [1, 16])
def test_symbolic_holds_keep_exact_named_boundaries_and_existing_action_frames(tmp_path, private, depth):
    machine, _ = _lower(tmp_path, '''module M(input [2:0] a, output logic [2:0] q);
      always_comb q = a + 1'b1;
    endmodule''')
    names = _names(machine)
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'q': 1}), goals)
    context = z3.Context() if private else state[names['q']].ctx
    if private:
        relation = relation.translate(context)
        state = {name: term.translate(context) for name, term in state.items()}
    solver = z3.Solver(ctx=context)
    expected = z3.BitVecVal(1, state[names['q']].size(), ctx=context)
    for index in range(depth):
        enabled = z3.Bool(f'hold_enabled_{index}', ctx=context)
        value = index % 7
        pins = _pins(machine, env, {name: value for name, _ in machine.drive_inputs})
        pins = {name: term if term.ctx == context else term.translate(context)
                for name, term in pins.items()}
        phase = relation.encode(state, pins, event_bound=machine.event_bound,
                                enabled=enabled, tag=f'hold_{index}')
        # Definitions name the hold result without adding a scheduler action
        # or a fresh action frame. Quiescence has its existing final root.
        assert len(phase.step_definitions) == len(phase.actions) == machine.event_bound
        assert len(phase.constraints) == machine.event_bound + 1
        assert z3.is_const(phase.state[names['q']])
        solver.add(*phase.constraints)
        expected = z3.If(enabled, z3.BitVecVal(value + 1, expected.size(), ctx=context), expected)
        solver.push()
        solver.add(phase.state[names['q']] != expected)
        assert solver.check() == z3.unsat
        solver.pop()
        assert solver.check() == z3.sat
        state = phase.state


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('failure', [False, True])
def test_named_entry_is_total_and_matches_inline_failure_and_hold(tmp_path, monkeypatch, private, failure):
    from behave.sim.rtl import source_relation

    body = 'if(a==3) q=1\'bx; else q=a;' if failure else 'q=a;'
    machine, _ = _lower(tmp_path, 'module M(input [1:0] a, output logic [1:0] q);'
                        'always_comb begin ' + body + ' end endmodule', defer_domains=True)
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'q': 0}), goals)
    context = z3.Context() if private else z3.main_ctx()
    relation = relation.translate(context)
    state = {name: value if value.ctx == context else value.translate(context)
             for name, value in state.items()}
    pins = {name: term if term.ctx == context else term.translate(context) for name, term in
            _pins(machine, env, {name: 3 for name, _ in machine.drive_inputs}).items()}
    alive, enabled = z3.Bools('entry_alive entry_enabled', ctx=context)
    options = dict(event_bound=machine.event_bound, alive=alive, enabled=enabled,
                   require_quiescence=False)
    named = relation.encode(state, pins, tag='named', **options)
    define = source_relation._define_control
    def inline(term, definitions, tag):
        return term if tag.endswith('_entered') else define(term, definitions, tag)
    with monkeypatch.context() as patch:
        patch.setattr(source_relation, '_define_control', inline)
        original = relation.encode(state, pins, tag='inline', **options)
    assert z3.is_const(named.entered)
    assert named.step_definitions[0][0].eq(named.entered == z3.And(alive, enabled))
    solver = z3.Solver(ctx=context)
    solver.add(*named.constraints, *original.constraints,
               *(a == b for a, b in zip(named.actions, original.actions, strict=True)))
    for a, e in product((False, True), repeat=2):
        solver.push()
        solver.add(alive == a, enabled == e)
        assert solver.check() == z3.sat
        solver.add(z3.Or(named.entered != original.entered, named.alive != original.alive,
            named.error != original.error, named.continuable != original.continuable,
            named.unfinished != original.unfinished,
            *(named.hits[g] != original.hits[g] for g in goals),
            # As in the public interval contract, a dead execution has no
            # defined successor payload. Its error/events still must agree.
            z3.And(named.alive, z3.Or(*(named.state[k] != original.state[k]
                                       for k in named.state)))))
        assert solver.check() == z3.unsat
        solver.pop()


def test_continuation_does_not_inline_prior_entry_conditions(tmp_path):
    machine, _ = _lower(tmp_path, 'module M(input a, output logic q); always_comb q=a; endmodule')
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'q': 0}), ())
    pins = _pins(machine, env, {name: 1 for name, _ in machine.drive_inputs})
    enabled, alive = z3.Bool('first_enable'), None
    for index in range(32):
        phase = relation.encode(state, pins, event_bound=machine.event_bound,
            enabled=enabled, alive=alive, require_quiescence=False, tag=f'phase{index}')
        assert z3.is_const(phase.entered)
        # The predecessor is retained by an explicit equation, not recursively
        # copied into the next continuation. No whole-history simplification.
        assert len(phase.member_layers) == len(phase.actions)
        enabled, alive, state = phase.continuable, phase.alive, phase.state


def test_range_candidates_are_analysis_metadata_not_transition_constraints(tmp_path):
    machine, _ = _lower(tmp_path, 'module M(input a, output logic q); always_comb q = a; endmodule')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'q': 0}), goals)
    pins = _pins(machine, env, {name: 1 for name, _ in machine.drive_inputs})
    exact = relation.encode(state, pins, event_bound=machine.event_bound, require_quiescence=False)
    assert exact.state_range_candidates == machine.procedure.state_ranges
    assert exact.state_range_candidates is not machine.procedure.state_ranges
    # A deliberately false proposed range changes no executable expression.
    invalid = replace(exact, state_range_candidates={name: ranges.Range(9, 9) for name in exact.state})
    assert invalid.constraints is exact.constraints and invalid.state is exact.state
    search = relation.encode(state, pins, event_bound=machine.event_bound)
    assert search.state_range_candidates is None


@pytest.mark.parametrize('private', [False, True])
def test_source_interval_boolean_construction_preserves_the_exact_ast(tmp_path, monkeypatch, private):
    from behave.sim.rtl import symbolic_state
    from behave.sim.rtl.source_relation import SourceObservation, source_event_images

    machine, _ = _lower(tmp_path, 'module M(input a, output logic q); always_comb q = a; endmodule')
    goals = tuple(dict.fromkeys(goal for _, _, goal in machine.occurrences))
    relation, state, env = _relation(machine, _state(machine, {'a': 0, 'q': 0}), goals)
    pins = _pins(machine, env, {name: 1 for name, _ in machine.drive_inputs})
    if private:
        context = z3.Context()
        relation = relation.translate(context)
        state = {name: value.translate(context) for name, value in state.items()}
        pins = {name: value.translate(context) for name, value in pins.items()}
    conjunction, disjunction = symbolic_state.assertion_conjunction, symbolic_state.assertion_disjunction
    calls = [0, 0]

    def conjoin(terms, **kwargs):
        terms = tuple(terms)
        result = conjunction(terms, **kwargs)
        assert result.eq(z3.And(*terms))
        calls[0] += 1
        return result

    def disjoin(terms, **kwargs):
        terms = tuple(terms)
        result = disjunction(terms, **kwargs)
        assert result.eq(z3.Or(*terms))
        calls[1] += 1
        return result

    monkeypatch.setattr(symbolic_state, 'assertion_conjunction', conjoin)
    monkeypatch.setattr(symbolic_state, 'assertion_disjunction', disjoin)
    phases = relation.encode_phases(state, (pins, pins),
        event_bounds=(machine.event_bound,) * 2, require_quiescence=False)
    images = list(source_event_images(phases.phases, SourceObservation.GOAL_PREFIX))
    assert len(images) == 2 and calls[0] > 0 and calls[1] > 0


def test_source_relation_reuses_only_the_same_complete_projection(tmp_path, monkeypatch):
    from behave.sim.symbolic.step_template import StepTemplate

    source = '''module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) if(a==3) q<=a; else q<=0;
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    proc = machine.procedure
    template = SourceIntervalTemplate(proc, {**proc.state_ranges, **machine.input_ranges})
    goals = sorted({goal for _, _, goal in machine.occurrences})
    project, calls = StepTemplate.project, []

    def counted(self, *args, **kwargs):
        calls.append(self)
        return project(self, *args, **kwargs)

    monkeypatch.setattr(StepTemplate, 'project', counted)
    first = template.relation(machine, goals)
    assert template.relation(machine, reversed(goals)) is first
    assert len(calls) == 3  # IR selection, then the source and drive views.
    for options in ({'complete_state': True}, {'occurrences': True},
                    {'state_reads': (_names(machine)['q'],)}):
        selected = template.relation(machine, goals, **options)
        assert selected is not first
        assert template.relation(machine, goals, **options) is selected
    assert len(calls) == 9
    assert template.relation(machine, goals[:-1]).goal_names != first.goal_names
    # The procedure can be shared by a different scheduler view. Its occurrence
    # and drive bindings must not acquire the prior machine's selected relation.
    other = replace(machine)
    assert template.relation(other, goals) is not template.relation(machine, goals)
    assert len(calls) == 15


@pytest.mark.parametrize('normalized', [False, True])
def test_live_native_and_query_views_survive_alternating_consumers(tmp_path, normalized):
    from behave.sim.rtl.exact_job_instance import PreparedSourceExact
    from behave.sim.rtl.source_direct import NativeCoverSourceCompiler

    machine, _ = _lower(tmp_path, '''module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) if(a==3) q<=a; else q<=0;
    endmodule''')
    proc = machine.procedure
    template = SourceIntervalTemplate(proc, {**proc.state_ranges, **machine.input_ranges},
                                      direct=NativeCoverSourceCompiler(machine))
    if normalized:
        template = template.normalized()
    goals = sorted({goal for _, _, goal in machine.occurrences})
    full = template.relation(machine, goals, complete_state=True, occurrences=True)
    prepared = object.__new__(PreparedSourceExact)
    prepared.machine, prepared._native_views = machine, None
    native = prepared._native_interval(full, member=True)
    query = template.relation(machine, goals[:1])
    assert query is not full and query.goal_names != full.goal_names

    # The full native observer and the projected query remain live owners.
    # Switching consumers must not rebuild either immutable transition image
    # or replace the native compiler's already prepared syntax.
    for _ in range(3):
        current = template.relation(machine, goals, complete_state=True, occurrences=True)
        assert current is full
        assert prepared._native_interval(current, member=True) is native
        assert template.relation(machine, goals[:1]) is query


def test_unused_source_views_are_not_kept_alive_by_reuse(tmp_path):
    import gc
    import weakref

    machine, _ = _lower(tmp_path, '''module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) if(a==3) q<=a; else q<=0;
    endmodule''')
    proc = machine.procedure
    template = SourceIntervalTemplate(proc, {**proc.state_ranges, **machine.input_ranges})
    goals = sorted({goal for _, _, goal in machine.occurrences})
    first = template.relation(machine, goals)
    retired = weakref.ref(first)
    current = template.relation(machine, goals[:1])
    del first
    gc.collect()
    assert retired() is None
    assert template.relation(machine, goals[:1]) is current
    # Derived templates own new formulas; neither normalization nor a changed
    # input premise may recover a relation from the original owner's index.
    for view in (template.normalized(), template.specialize_inputs({'action': 0})):
        assert view.relation(machine, goals[:1]) is not current


def test_source_relation_cancellation_does_not_publish_a_partial_projection(tmp_path, monkeypatch):
    from behave.sim.symbolic.step_template import StepTemplate

    source = '''module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) if(a==3) q<=a; else q<=0;
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    proc = machine.procedure
    template = SourceIntervalTemplate(proc, {**proc.state_ranges, **machine.input_ranges})
    goals = sorted({goal for _, _, goal in machine.occurrences})
    complete = template.relation(machine, goals)
    project, calls = StepTemplate.project, []

    def interrupted(self, *args, **kwargs):
        calls.append(self)
        if len(calls) == 2:
            raise TimeoutError('cancelled second view')
        return project(self, *args, **kwargs)

    monkeypatch.setattr(StepTemplate, 'project', interrupted)
    with pytest.raises(TimeoutError, match='cancelled second view'):
        template.relation(machine, goals[:-1])
    assert template.relation(machine, goals) is complete
    assert template.relation(machine, goals[:-1]).goal_names != complete.goal_names
    # Both attempts share the complete observation image; each projects only
    # the source and drive views, without a third IR reconstruction.
    assert len(calls) == 4


def test_reused_source_projection_keeps_query_state_and_context_independent(tmp_path):
    import weakref

    source = '''module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) if(q==3 && a==q) q<=0;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    proc, names = machine.procedure, _names(machine)
    env = {**proc.state_ranges, **machine.input_ranges}
    goal = _goal(source, plan, 'q==3')
    template = SourceIntervalTemplate(proc, env)
    relation = template.relation(machine, (goal,))
    for previous in (0, 3, 0):
        answer = query_source_interval(machine, env,
            _state(machine, {'clk': 0, 'a': 0, 'q': previous}),
            {names['clk']: 1, names['a']: 0}, (goal,), mutable_inputs=(names['a'],),
            event_bound=machine.event_bound, _relation=relation, timeout_ms=30_000)
        assert answer.outcome is (probing.Outcome.SAT if previous == 3
                                  else probing.Outcome.LOCAL_UNSAT)
        if previous == 3:
            assert answer.candidate[names['a']] == 3
        assert template.relation(machine, (goal,)) is relation
    for _ in range(2):
        context = z3.Context()
        translated = template.relation(machine, (goal,), context=context)
        assert translated is not relation and translated.source.context is context
        owner = weakref.ref(context)
        del translated, context
        assert owner() is None
    assert template.relation(machine, (goal,)) is relation


def test_source_interval_checks_its_deadline_during_template_instantiation(tmp_path, monkeypatch):
    from behave.sim.symbolic.step_template import StepTemplate

    source = '''module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) if(a==3) q<=a;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    target = _goal(source, plan, 'a==3')
    state = _state(machine, {'clk': 0, 'a': 0, 'q': 0})
    relation, pre, env = _relation(machine, state, (target,))
    pins = _pins(machine, env, {names['clk']: 1, names['a']: 0})
    entered, checks = False, []

    def checkpoint():
        checks.append(entered)
        if entered:
            raise TimeoutError('template interrupted')

    def suspended(_self, _pre=None, **_options):
        nonlocal entered
        entered = True
        yield
        pytest.fail('template resumed without its owning deadline checkpoint')

    monkeypatch.setattr(StepTemplate, 'encode_steps', suspended)
    with pytest.raises(TimeoutError, match='template interrupted'):
        relation.encode(pre, pins, event_bound=machine.event_bound, check=checkpoint)
    assert checks[-1] is True and False in checks


def test_symbolic_initial_storage_is_not_the_baseline_boundary(tmp_path):
    source = '''module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) if(q==3 && a==q) q<=0;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    target = _goal(source, plan, 'q==3')
    baseline = _state(machine, {'clk': 0, 'a': 0, 'q': 0})
    relation, pre, env = _relation(machine, baseline, (target,))
    q = z3.FreshConst(pre[names['q']].sort(), 'initial_q')
    pre[names['q']] = q
    pins = _pins(machine, env, {names['clk']: 1, names['a']: 0})
    pins[names['a']] = None
    encoded = relation.encode(pre, pins, event_bound=machine.event_bound)
    solver = z3.Solver()
    solver.add(*encoded.constraints, q >= 0, q <= 3, encoded.hits[target])
    assert solver.check() == z3.sat
    model = solver.model()
    assert model.eval(q).as_long() == model.eval(encoded.inputs[names['a']]).as_long() == 3
    solver.add(q == 0)
    assert solver.check() == z3.unsat
    # The same goal really is locally impossible at the baseline boundary.
    local = query_source_interval(machine, env, baseline,
        {names['clk']: 1, names['a']: 0}, (target,), mutable_inputs=(names['a'],),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert local.outcome is probing.Outcome.LOCAL_UNSAT


@pytest.mark.parametrize('assignment', ['=', '<='])
@pytest.mark.parametrize('terminal', [False, True])
def test_order_miter_compares_hits_and_failure_not_only_final_values(tmp_path, assignment, terminal):
    action = '$fatal(1,"stop");' if terminal else 'r=1;'
    source = f'''module M(input clk,input [1:0] d,output reg [1:0] q,r);
      always @(posedge clk) q {assignment} d;
      always @(posedge clk) begin if(q==1) {action} else r=2; r=0; end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    goal = _goal(source, plan, 'q==1')
    state = _state(machine, {'clk': 0, 'd': 0, 'q': 0, 'r': 0})
    relation, pre, env = _relation(machine, state, (goal,))
    pins = _pins(machine, env, {names['clk']: 1, names['d']: 1})
    left = relation.encode(pre, pins, event_bound=machine.event_bound, tag='left')
    right = relation.encode(pre, pins, event_bound=machine.event_bound, tag='right')
    solver = z3.Solver()
    solver.add(*left.constraints, *right.constraints)
    if terminal:
        solver.add(left.alive != right.alive)
    else:
        solver.add(left.alive, right.alive,
                   left.state[names['q']] == right.state[names['q']],
                   left.state[names['r']] == right.state[names['r']],
                   left.hits[goal] != right.hits[goal])
    assert solver.check() == (z3.sat if assignment == '=' else z3.unsat)


def test_successive_phases_can_have_independent_inputs_and_retain_state(tmp_path):
    source = '''module M(input clk,input [1:0] a,output reg [1:0] q,r);
      always @(posedge clk) begin
        if(q==1 && a==2) r<=3;
        q<=a;
      end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    target = _goal(source, plan, 'q==1')
    relation, pre, env = _relation(machine, _state(machine, {'clk': 0, 'a': 0, 'q': 0, 'r': 0}), (target,))
    first_pins = _pins(machine, env, {names['clk']: 1, names['a']: 0})
    first_pins[names['a']] = None
    first = relation.encode(pre, first_pins, event_bound=machine.event_bound, tag='first')
    low_pins = _pins(machine, env, {names['clk']: 0, names['a']: 0})
    low_pins[names['a']] = first.inputs[names['a']]
    low = relation.encode(first.state, low_pins, event_bound=machine.event_bound,
                          alive=first.alive, tag='low')
    second = relation.encode(low.state, first_pins, event_bound=machine.event_bound,
                             alive=low.alive, tag='second')
    solver = z3.Solver()
    solver.add(*first.constraints, *low.constraints, *second.constraints, second.hits[target])
    assert solver.check() == z3.sat
    model = solver.model()
    assert model.eval(first.inputs[names['a']]).as_long() == 1
    assert model.eval(second.inputs[names['a']]).as_long() == 2
    assert model.eval(second.state[names['r']]).as_long() == 3
    solver.add(first.inputs[names['a']] == second.inputs[names['a']])
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('direct', [False, True])
def test_failure_prefix_cannot_resume_on_next_drive(tmp_path, direct):
    source = '''module M(input clk,input [1:0] a,output reg [1:0] q);
      always @(posedge clk) begin
        if(a==1) $fatal(1,"stop");
        if(a==2) q<=3;
      end
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    failure = _goal(source, plan, '$fatal', 'failure', 'artifact-failure')
    later = _goal(source, plan, 'a==2')
    relation, pre, env = _relation(machine, _state(machine, {'clk': 0, 'a': 0, 'q': 0}), (failure, later))
    if direct:
        from dataclasses import replace
        from behave.sim.rtl.source_direct import DirectSourceCompiler

        relation = replace(relation, direct=DirectSourceCompiler(machine))
    first = relation.encode(pre, _pins(machine, env, {names['clk']: 1, names['a']: 1}),
                            event_bound=machine.event_bound, tag='failure')
    low = relation.encode(first.state, _pins(machine, env, {names['clk']: 0, names['a']: 2}),
                          event_bound=machine.event_bound, alive=first.alive, error=first.error, tag='low')
    next_drive = relation.encode(low.state, _pins(machine, env, {names['clk']: 1, names['a']: 2}),
        event_bound=machine.event_bound, alive=low.alive, error=low.error, tag='after_failure')
    solver = z3.Solver()
    solver.add(*first.constraints, *low.constraints, *next_drive.constraints, first.hits[failure])
    assert solver.check() == z3.sat
    solver.add(z3.Or(first.alive, next_drive.alive, first.hits[later], next_drive.hits[later],
                     first.error == 0, next_drive.error != first.error))
    assert solver.check() == z3.unsat


def test_omitted_pins_are_not_silently_freed(tmp_path):
    source = 'module M(input clk,input a,output reg q); always @(posedge clk) q<=a; endmodule'
    machine, _ = _lower(tmp_path, source)
    relation, pre, _ = _relation(machine, _state(machine, {'clk': 0, 'a': 0, 'q': 0}), ())
    with pytest.raises(ValueError, match='every physical input'):
        relation.encode(pre, {}, event_bound=machine.event_bound)


def test_omitted_state_is_not_silently_freed(tmp_path):
    source = 'module M(input clk,input a,output reg q); always @(posedge clk) q<=a; endmodule'
    machine, _ = _lower(tmp_path, source)
    relation, pre, env = _relation(machine, _state(machine, {'clk': 0, 'a': 0, 'q': 0}), ())
    names = _names(machine)
    pre.pop(names['q'])
    with pytest.raises(ValueError, match='every retained state variable'):
        relation.encode(pre, _pins(machine, env, {names['clk']: 1, names['a']: 1}),
                        event_bound=machine.event_bound)


@pytest.mark.parametrize('assignment', ['=', '<='])
@pytest.mark.parametrize('terminal', [False, True])
def test_all_small_initial_states_inputs_and_ordered_histories_match_interpreter(tmp_path, assignment, terminal):
    action = '$fatal(1,"stop");' if terminal else 'r=3;'
    source = f'''module M(input clk,input [1:0] d,output reg [1:0] q,r);
      always @(posedge clk) q {assignment} d;
      always @(posedge clk) if(q==2) {action} else r=q;
    endmodule'''
    machine, _ = _lower(tmp_path, source)
    assert machine.event_bound is not None
    names = _names(machine)
    goals = sorted({goal for _, _, goal in machine.occurrences})
    initial = {'clk': 0, 'd': 0, 'q': 0, 'r': 0}
    relation, pre, env = _relation(machine, _state(machine, initial), goals)
    q = z3.FreshConst(pre[names['q']].sort(), 'initial_q')
    pre[names['q']] = q
    pins = _pins(machine, env, {names['clk']: 1, names['d']: 0})
    pins[names['d']] = None
    encoded = relation.encode(pre, pins, event_bound=machine.event_bound)
    for previous, driven in product(range(4), repeat=2):
        histories = _histories(machine, _state(machine, {**initial, 'q': previous}),
            ({names['clk']: 1, names['d']: driven},), (machine.event_bound,))
        expected = set()
        for cells, marks, failed in histories:
            values = dict(cells)
            hits = {goal for mark, _, goal in machine.occurrences if mark in marks}
            expected.add((None if failed else (values[names['q']], values[names['r']]),
                          tuple(goal in hits for goal in goals), failed))
        solver = z3.Solver()
        solver.add(*encoded.constraints, q == previous, encoded.inputs[names['d']] == driven)
        observed = set()
        while True:
            status = solver.check()
            assert status in (z3.sat, z3.unsat)
            if status == z3.unsat:
                break
            model = solver.model()
            failed = not z3.is_true(model.eval(encoded.alive))
            state_terms = [] if failed else [encoded.state[names[key]] for key in ('q', 'r')]
            hit_terms = [encoded.hits[goal] for goal in goals]
            observed.add((None if failed else tuple(model.eval(term).as_long() for term in state_terms),
                          tuple(z3.is_true(model.eval(term)) for term in hit_terms), failed))
            solver.add(z3.Or(*(term != model.eval(term)
                              for term in [encoded.alive, *state_terms, *hit_terms])))
        assert observed == expected, (assignment, terminal, previous, driven, observed, expected)
