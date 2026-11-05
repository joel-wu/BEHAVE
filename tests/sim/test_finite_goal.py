"""Backend-neutral finite goal semantics; usable without any solver package."""

import itertools
import pickle

from behave.sim.core import finite_goal, goal_provider


class ConcreteAlgebra:
    literal = staticmethod(lambda value: value)
    equal = staticmethod(lambda a, b: type(a) is type(b) and a == b)
    land = staticmethod(lambda *values: all(values))
    lor = staticmethod(lambda *values: any(values))
    ite = staticmethod(lambda condition, a, b: a if condition else b)


def _semantics():
    predicate = finite_goal.FinitePredicate(
        "value.hit", [finite_goal.FiniteVariable("v", (False, 0, 1, True))],
        [(True,), (1,)])
    monitor = finite_goal.FiniteMonitor(
        (0, 1), (False, True), 0, ("monitor.hit",), [
            finite_goal.FiniteMonitorTransition(
                state, event, int(event), ("monitor.hit",) if state and event else ())
            for state, event in itertools.product((0, 1), (False, True))])
    return finite_goal.DeclarativeGoalSemantics([predicate], [monitor])


def test_finite_goal_semantics_remains_one_definition_at_both_import_paths():
    for name in finite_goal.__all__:
        assert getattr(goal_provider, name) is getattr(finite_goal, name)
    semantics = _semantics()
    for value, state, event in itertools.product((False, 0, 1, True), (0, 1), (False, True)):
        monitor = semantics.monitors[0]
        arguments = ({"value.hit": {"v": value}}, {monitor.key: state}, {monitor.key: event})
        concrete = semantics.concrete_step(*arguments)
        symbolic = semantics.symbolic_step(*arguments, ConcreteAlgebra())
        assert symbolic.valid is True
        assert dict(symbolic.hit_terms) == dict(concrete.hit_values)
        assert dict(symbolic.monitor_states) == dict(concrete.monitor_states)


def test_old_import_path_pickles_still_resolve_to_the_same_finite_records():
    original = _semantics()
    encoded = pickle.dumps(original, protocol=2)
    legacy = encoded.replace(b"behave.sim.core.finite_goal\n", b"behave.sim.core.goal_provider\n")
    assert legacy != encoded
    for image in (encoded, legacy):
        restored = pickle.loads(image)
        assert type(restored) is finite_goal.DeclarativeGoalSemantics
        assert restored.digest() == original.digest()
        assert restored.as_dict() == original.as_dict()
