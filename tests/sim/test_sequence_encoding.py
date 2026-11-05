"""Sequence traversal preserves execution, failure guards and occurrence hits."""

import pytest
import z3

from behave.sim.symbolic import encoder, interp, ir, plan, ranges, slicing
from behave.sim.engines import local_ir as exact

from .test_command_positions import sequence
from .test_local_query_sharing import problem

pytestmark = pytest.mark.solver


def original_sequence_rule(command_rule):
    """Pairwise completion rule, without identity or unchanged-guard reuse."""
    def command(self, c, state, reached):
        if not isinstance(c, ir.Seq):
            return (yield from command_rule(self, c, state, reached))
        yield from encoder._retry_encoding(self.checkpoint)
        if z3.is_false(reached) or self._guarded_reads.impossible(reached):
            return state
        yield from encoder._retry_encoding(self._prime_command, c)
        parts, pending = [], [c]
        while pending:
            part = pending.pop()
            if isinstance(part, ir.Seq):
                pending.extend((part.second, part.first))
            else:
                parts.append(part)
        before = state.ok
        under = reached
        for index, part in enumerate(parts):
            state = yield (part, state, under)
            if index + 1 < len(parts):
                under = reached if z3.eq(state.ok, before) else (
                    yield from encoder._retry_encoding(
                        self.bind, encoder._conj(reached, state.ok)))
        return state
    return command


@pytest.mark.parametrize("state", [0, 4])
@pytest.mark.parametrize("trips", [0, 2])
@pytest.mark.parametrize("failure_goals", [False, True])
@pytest.mark.parametrize("target", [None, "hit", "bad"])
def test_identity_and_guard_reuse_preserve_the_complete_relation(
        monkeypatch, state, trips, failure_goals, target):
    proc, env = problem(trips=trips, failure_goals=failure_goals)
    validation = encoder._procedure_validation(proc)
    prepared = plan.plan_encoding(proc, env, _procedure_validation=validation)
    keep = None if target is None else slicing.cone(
        slicing.linearize(proc.body), target).kept
    options = dict(env=env, pre=exact.pin_state(proc, env, {"s": state}),
                   plan=prepared, _procedure_validation=validation,
                   _plan_prevalidated=True, _analysis_keep=keep,
                   _analysis_goals=None if target is None else (target,))
    optimized = encoder._encode_step(proc, tag="optimized", **options)
    monkeypatch.setattr(encoder._Commands, "_command",
                        original_sequence_rule(encoder._Commands._command))
    reference = encoder._encode_step(proc, tag="reference", **options)
    solver = z3.Solver()
    solver.add(optimized.constraint, reference.constraint,
               optimized.pre["a"] == reference.pre["a"])
    differences = [optimized.ok != reference.ok, optimized.error != reference.error]
    differences.extend(optimized.final[n] != value for n, value in reference.final.items())
    assert optimized.hits.keys() == reference.hits.keys()
    differences.extend(optimized.hits[g] != value for g, value in reference.hits.items())
    assert optimized.hit_occurrences.keys() == reference.hit_occurrences.keys()
    for goal, terms in reference.hit_occurrences.items():
        assert len(optimized.hit_occurrences[goal]) == len(terms)
        differences.extend(left != right for left, right in
                           zip(optimized.hit_occurrences[goal], terms))
    solver.add(z3.Or(differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("association", ["left", "right", "balanced"])
def test_pruned_commands_do_not_enter_encoding_scopes(monkeypatch, association):
    dropped = [ir.Assign("unused", ir.Lit(i)) for i in range(100)]
    marks = [ir.Mark("first"), ir.Mark("last")]
    proc = ir.Procedure((), (), ("unused",), (),
                        sequence([marks[0], *dropped, marks[1]], association))
    entered = []
    original = encoder._Commands._scoped_command

    def scoped(self, command, state, reached):
        entered.append(id(command))
        return (yield from original(self, command, state, reached))

    monkeypatch.setattr(encoder._Commands, "_scoped_command", scoped)
    result = encoder._encode_step(proc, _analysis_keep=frozenset(map(id, marks)))
    assert not set(map(id, dropped)).intersection(entered)
    assert set(map(id, marks)) <= set(entered)
    assert all(z3.is_true(term) for term in result.hits.values())


@pytest.mark.parametrize("association", ["left", "right", "balanced"])
def test_unchanged_completion_guard_is_not_rebound_after_each_mark(monkeypatch, association):
    marks = [ir.Mark(f"g{i}") for i in range(100)]
    body = sequence([
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(1)),
              ir.Fail("Stopped", "bad"), ir.Skip()), *marks], association)
    proc = ir.Procedure((), ("a",), (), (), body)
    within_marks, binds = [False], []
    original_command, original_bind = encoder._Commands._command, encoder._Commands.bind

    def command(self, c, state, reached):
        if c is marks[0]:
            within_marks[0] = True
        if c is marks[-1]:
            within_marks[0] = False
        return (yield from original_command(self, c, state, reached))

    def bind(self, term, *args, **kwargs):
        if within_marks[0]:
            binds.append(term)
        return original_bind(self, term, *args, **kwargs)

    monkeypatch.setattr(encoder._Commands, "_command", command)
    monkeypatch.setattr(encoder._Commands, "bind", bind)
    encoded = encoder.encode_step(proc, env={"a": ranges.Range(0, 3)})
    assert not binds
    for value in range(4):
        _, _, hits, failure = interp.run_step(proc, {"a": value})
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == value)
        assert solver.check() == z3.sat
        model = solver.model()
        assert {g for g, term in encoded.hits.items()
                if z3.is_true(model.eval(term))} == hits
        assert z3.is_true(model.eval(encoded.ok)) == (not failure)
