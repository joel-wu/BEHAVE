"""The constant-size monitor is an exact quotient, not fewer safety checks."""

from dataclasses import asdict, replace
import random

import pytest
import z3

from behave.sim.core.ready_valid_product import (
    ConcreteAlgebra, CountedReadyValidState, OrderedReadyValidConfig,
    OrderedReadyValidEvent, OrderedReadyValidState,
    ordered_ready_valid_initial, ordered_ready_valid_state_image, ordered_ready_valid_step,
)
from behave.sim.rtl.symbolic_state import image_terms
from behave.sim.rtl.task_symbolic import Z3Algebra
from tests.sim.ordered_monitor_oracle import DenseConfig


def projected(state, algebra):
    return CountedReadyValidState(state.accepted_count, algebra.count(state.pending),
                                 state.stalled, state.held)


def invariant(state):
    return z3.And(state.accepted_count >= 0, *(
        z3.And(z3.Implies(pending, state.accepted_count > i),
               owed == z3.If(pending, 1, 0))
        for i, (pending, owed) in enumerate(zip(state.pending, state.owed, strict=True))))


@pytest.mark.parametrize('capacity', (1, 4))
@pytest.mark.parametrize('policy', (None, 'clears', 'keeps'))
@pytest.mark.parametrize('limit', (None, 1))
def test_inductive_equivalence_of_all_observations_and_next_state(capacity, policy, limit):
    # A private context catches accidental globals in both representations.
    ctx = z3.Context()
    algebra = Z3Algebra(ctx)
    config = OrderedReadyValidConfig(capacity, 1, 1, limit, reset_policy=policy)
    dense_config = DenseConfig(**asdict(config))
    integer = lambda name: z3.Int(name, ctx=ctx)
    boolean = lambda name: z3.Bool(name, ctx=ctx)
    dense = OrderedReadyValidState(
        integer('accepted'), tuple(boolean(f'pending_{i}') for i in range(capacity)),
        tuple(integer(f'owed_{i}') for i in range(capacity)),
        tuple(integer(f'at_{i}') for i in range(capacity)),
        tuple(boolean(f'offered_{i}') for i in range(capacity)),
        tuple(boolean(f'reported_{i}') for i in range(capacity)),
        boolean('stalled'), (z3.BitVec('held', 8, ctx=ctx),))
    event = OrderedReadyValidEvent(boolean('reset'), 3, boolean('accept'),
        boolean('valid'), boolean('ready'), (z3.BitVec('payload', 8, ctx=ctx),))
    before = ordered_ready_valid_initial(dense_config, algebra, (algebra.zero_like(dense.held[0]),))
    after = ordered_ready_valid_initial(config, algebra, (algebra.zero_like(dense.held[0]),))
    assert z3.is_true(z3.simplify(invariant(before)))
    for left, right in zip(image_terms(ordered_ready_valid_state_image(projected(before, algebra))),
                           image_terms(ordered_ready_valid_state_image(after)), strict=True):
        assert z3.is_true(z3.simplify(left == right))

    old = ordered_ready_valid_step(dense_config, dense, event, algebra)
    new = ordered_ready_valid_step(config, projected(dense, algebra), event, algebra)
    differences = [z3.Not(invariant(old.state))]
    for old_image, new_image in (
        (ordered_ready_valid_state_image(projected(old.state, algebra)),
         ordered_ready_valid_state_image(new.state)),
        (old.hits, new.hits), (old.opportunities, new.opportunities), (old.details, new.details)):
        differences.extend(left != right for left, right in
                           zip(image_terms(old_image), image_terms(new_image), strict=True))
    solver = z3.Solver(ctx=ctx)
    solver.set(timeout=10000)
    solver.add(invariant(dense))
    assert solver.check() == z3.sat  # Do not prove equivalence on an empty domain.
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('policy', (None, 'clears', 'keeps'))
def test_concrete_histories_include_capacity_exhaustion_and_resets(policy):
    algebra = ConcreteAlgebra()
    config = OrderedReadyValidConfig(3, 1, 1, 1, reset_policy=policy)
    dense_config = DenseConfig(**asdict(config))
    dense = ordered_ready_valid_initial(dense_config, algebra)
    counted = ordered_ready_valid_initial(config, algebra)
    rng = random.Random(123)
    # First overflow the lifetime acceptance bound, then exercise random traffic.
    events = [OrderedReadyValidEvent(False, i, True, True, True, (7,)) for i in range(8)]
    events += [OrderedReadyValidEvent(rng.randrange(19) == 0, i + 8,
        bool(rng.randrange(2)), bool(rng.randrange(2)), bool(rng.randrange(2)),
        (rng.randrange(4),)) for i in range(256)]
    for event in events:
        old = ordered_ready_valid_step(dense_config, dense, event, algebra)
        new = ordered_ready_valid_step(config, counted, event, algebra)
        assert (old.hits, old.opportunities, old.details) == (new.hits, new.opportunities, new.details)
        assert projected(old.state, algebra) == new.state
        dense, counted = old.state, new.state


@pytest.mark.parametrize('changes', ({'answers_per_transaction': None},
    {'answers_per_transaction': 2}, {'identifier_index': 0}, {'response_deadline': 0}))
def test_other_protocols_keep_the_full_model(changes):
    config = replace(OrderedReadyValidConfig(3, 1, 1), **changes)
    assert not config.counted
    assert type(ordered_ready_valid_initial(config, ConcreteAlgebra())) is OrderedReadyValidState
    with pytest.raises(ValueError, match='counted state'):
        ordered_ready_valid_step(config, CountedReadyValidState(0, 0, False, (0,)),
            OrderedReadyValidEvent(False, 0, False, False, False, (0,)), ConcreteAlgebra())

