"""Sampling owners share a predicate, not their events or history semantics."""

from itertools import product

import pytest

from behave.sim import api
from behave.sim.core.goal_predicate import bind_predicate
from behave.sim.core.observation_goal import GoalCondition as C
from behave.sim.core.observation_goal import GoalField as F
from behave.sim.core.operation_goal import OperationGoal, OperationValue


def _endpoint():
    return api.Endpoint("sample", "in", (
        api.Field("value", api.FiniteType(4, signed=True), "value"),
        api.Field("flag", api.ValueType.boolean(), "flag")),
        api.settled("in"), None, kind=api.Kind.TRANSACTION)


def _operation(condition):
    return OperationGoal("bin", condition).bind((
        OperationValue("value", width=4, signed=True),
        OperationValue("flag", "boolean")))


@pytest.mark.solver
def test_sampling_owners_and_symbolic_predicates_match_independent_scalar_oracles():
    import z3

    from behave.sim.core.observation_symbolic import predicate_term

    cases = (
        (C("eq", F("value"), -3), lambda x, flag: x == -3),
        (C("ne", F("flag"), False), lambda x, flag: flag),
        (C("lt", F("value"), 0), lambda x, flag: x < 0),
        (C("le", F("value"), -3), lambda x, flag: x <= -3),
        (C("gt", F("value"), 7), lambda x, flag: False),
        (C("ge", F("value"), -(1 << 100)), lambda x, flag: True),
        (C("eq", F("value", bit=3), True), lambda x, flag: x < 0),
        (C("not", C("eq", F("value", bit=0), F("flag"))),
         lambda x, flag: bool(x & 1) != flag),
        (C("and", C("eq", F("flag"), True), C("or", C("le", F("value"), -2),
             C("eq", F("value", bit=1), True))),
         lambda x, flag: flag and (x <= -2 or bool(x & 2))),
    )
    endpoint = _endpoint()
    for condition, oracle in cases:
        operation = _operation(condition)
        transaction = api.TransactionGoal("bin", "sample", condition)
        _, native = transaction.bind((endpoint,))
        for value, flag in product(range(-8, 8), (False, True)):
            expected = oracle(value, flag)
            payload = (value, flag)
            symbolic = (z3.BitVecVal(value, 4), z3.BoolVal(flag))
            assert operation.observe(payload) is native(payload, None) is expected
            assert z3.is_true(z3.simplify(operation.symbolic_hit(True, symbolic))) is expected
            assert z3.is_true(z3.simplify(predicate_term(transaction, endpoint, symbolic, None))) is expected
            assert z3.is_false(z3.simplify(operation.symbolic_hit(False, symbolic)))


@pytest.mark.solver
def test_history_still_belongs_to_the_sampling_owner():
    import z3

    from behave.sim.core.observation_symbolic import predicate_term

    condition = C("and", C("eq", F("value", previous=True, bit=3), True),
                  C("eq", F("value", bit=3), False))
    rule = api.TransactionGoal("fall", "sample", condition)
    endpoint, native = rule.bind((_endpoint(),))
    for previous, current in product(range(-8, 8), repeat=2):
        expected = previous < 0 <= current
        assert native((current, False), (previous, False)) is expected
        symbolic = predicate_term(rule, endpoint,
            (z3.BitVecVal(current, 4), z3.BoolVal(False)),
            (z3.BitVecVal(previous, 4), z3.BoolVal(False)))
        assert z3.is_true(z3.simplify(symbolic)) is expected
    assert native((0, False), None) is None
    assert native((0, False), (None, False)) is None
    assert native((0, False), (16, False)) is None
    assert rule.family == "state-activity"
    # Operation completion still has no implicit previous-operation memory.
    with pytest.raises(ValueError, match="previous"):
        _operation(condition)


@pytest.mark.parametrize("combine,constant", (("or", True), ("and", False)))
def test_no_short_circuit_hides_unreadable_values_or_producer_errors(combine, constant):
    condition = C(combine, C("eq", constant, True), C("eq", F("value"), 0))
    operation = _operation(condition)
    _, transaction = api.TransactionGoal("bin", "sample", condition).bind((_endpoint(),))
    assert operation.observe((None, False)) is None
    assert transaction((None, False), None) is None
    assert transaction(("not an integer", False), None) is None
    with pytest.raises(TypeError, match="producer type"):
        operation.observe(("not an integer", False))
    # A producer's unused value does not affect an unrelated predicate.
    literal = C("eq", 0, 0)
    assert _operation(literal).observe((None, None)) is True
    assert api.TransactionGoal("true", "sample", literal).bind((_endpoint(),))[1](None, None) is True


def test_binding_is_once_and_execution_reads_every_named_leaf_in_order():
    bound, read = [], []

    def field(term):
        bound.append(term.name)

        def value(current, previous):
            read.append(term.name)
            return current[term.name]

        return int, value

    predicate = bind_predicate(C("and", C("eq", F("x"), 1),
                                 C("eq", F("y"), 2)), field)
    assert bound == ["x", "y"] and not read
    assert predicate({"x": 0, "y": None}) is None
    assert predicate({"x": 1, "y": 2}) is True
    assert bound == ["x", "y"] and read == ["x", "y", "x", "y"]


def test_unreadable_left_comparison_does_not_hide_right_producer_error():
    read = []

    def field(term):
        def value(current, previous):
            read.append(term.name)
            if term.name == "right":
                raise RuntimeError("producer failed")
            return current

        return int, value

    predicate = bind_predicate(C("eq", F("left"), F("right")), field)
    with pytest.raises(RuntimeError, match="producer failed"):
        predicate(None)
    assert read == ["left", "right"]


@pytest.mark.parametrize("condition", (
    C("eq", F("value"), True), C("lt", F("flag"), False),
    C("and", F("value"), True), C("not", F("value")),
))
def test_sampling_owners_reject_the_same_ill_typed_conditions(condition):
    with pytest.raises(TypeError):
        _operation(condition)
    with pytest.raises(TypeError):
        api.TransactionGoal("bad", "sample", condition).bind((_endpoint(),))
