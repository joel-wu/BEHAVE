"""An uncounted monitor retains stability, not an all-zero horizon-sized queue."""
from dataclasses import asdict, replace
from itertools import product
from types import SimpleNamespace

import pytest

from behave.sim.core.ready_valid_product import (
    ConcreteAlgebra,
    OrderedReadyValidConfig,
    OrderedReadyValidEvent,
    ordered_ready_valid_initial,
    ordered_ready_valid_state_image,
    ordered_ready_valid_step,
)


def _dense(config):
    # The previous representation, with the identical transition rule. Restore
    # only its redundant capacity; never change a task event or obligation.
    return SimpleNamespace(**asdict(config), queue_capacity=config.capacity)


def _assert_equivalent(compact, dense, read=lambda value: value):
    left = ordered_ready_valid_state_image(compact, read)
    right = ordered_ready_valid_state_image(dense, read)
    for field in ('pending', 'owed', 'accepted_at', 'offered', 'reported'):
        assert left.pop(field) == ()
        assert not any(right.pop(field))
    assert left == right


@pytest.mark.parametrize('deadline,limit', [(None, None), (0, 1), (2, 4)])
def test_exhaustive_events_match_dense_representation(deadline, limit):
    config = OrderedReadyValidConfig(4, 1, None, limit, deadline)
    dense_config = _dense(config)
    algebra = ConcreteAlgebra()
    frontier = [(ordered_ready_valid_initial(config, algebra),
                 ordered_ready_valid_initial(dense_config, algebra))]
    for slot in range(3):
        following = {}
        for compact, dense in frontier:
            for reset, accepted, valid, ready, payload in product((False, True), repeat=5):
                event = OrderedReadyValidEvent(reset, slot, accepted, valid, ready,
                                               (int(payload),))
                got = ordered_ready_valid_step(config, compact, event, algebra)
                old = ordered_ready_valid_step(dense_config, dense, event, algebra)
                assert got.hits == old.hits
                assert got.opportunities == old.opportunities
                assert not any(old.details['deadline_hits'])
                assert got.details['deadline_hits'] == ()
                assert {k: v for k, v in got.details.items() if k != 'deadline_hits'} == {
                    k: v for k, v in old.details.items() if k != 'deadline_hits'}
                _assert_equivalent(got.state, old.state)
                following.setdefault(repr(got.state), (got.state, old.state))
        frontier = list(following.values())


@pytest.mark.solver
def test_symbolic_hits_and_states_equal_dense_for_all_events():
    import z3

    from behave.sim.rtl.task_symbolic import Z3Algebra

    config = OrderedReadyValidConfig(3, 1, None, 1, 0)
    dense_config = _dense(config)
    algebra = Z3Algebra()
    compact = ordered_ready_valid_initial(config, algebra, (z3.BitVecVal(0, 2),))
    dense = ordered_ready_valid_initial(dense_config, algebra, (z3.BitVecVal(0, 2),))
    differences = []
    for slot in range(3):
        reset, accepted, valid, ready = z3.Bools(f'r{slot} a{slot} v{slot} d{slot}')
        event = OrderedReadyValidEvent(reset, slot, accepted, valid, ready,
                                       (z3.BitVec(f'p{slot}', 2),))
        got = ordered_ready_valid_step(config, compact, event, algebra)
        old = ordered_ready_valid_step(dense_config, dense, event, algebra)
        for field in ('hits', 'opportunities'):
            for name, value in getattr(got, field).items():
                other = getattr(old, field)[name]
                differences.append(value != other)
        for field in ('accepted_count', 'stalled'):
            differences.append(getattr(got.state, field) != getattr(old.state, field))
        differences.extend(a != b for a, b in zip(got.state.held, old.state.held))
        for field in ('pending', 'owed', 'accepted_at', 'offered', 'reported'):
            assert getattr(got.state, field) == ()
            differences.extend(value != (False if z3.is_bool(value) else 0)
                               for value in getattr(old.state, field))
        differences.extend(old.details['deadline_hits'])
        compact, dense = got.state, old.state
    solver = z3.Solver()
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize('ratio', [1, 2, 7])
def test_counting_configurations_keep_the_original_layout(ratio):
    config = OrderedReadyValidConfig(8, 1, ratio, 4, 2)
    assert config.queue_capacity == 8
    assert len(ordered_ready_valid_initial(config, ConcreteAlgebra()).pending) == 8


@pytest.mark.solver
def test_empty_queue_invariant_is_inductive_not_a_three_slot_approximation():
    import z3

    from behave.sim.rtl.task_symbolic import Z3Algebra

    class SymbolicTimeAlgebra(Z3Algebra):
        @staticmethod
        def integer(value):
            # Production time is a concrete integer parameter. Quantify that
            # parameter here; all transition operations remain identical.
            return value if z3.is_int(value) else z3.IntVal(value)

    config = OrderedReadyValidConfig(5, 1, None, 2, 3)
    dense_config = _dense(config)
    algebra = SymbolicTimeAlgebra()
    stalled, reset, accepted, valid, ready = z3.Bools('s r a v d')
    held, payload = z3.BitVecs('held payload', 8)
    state = replace(ordered_ready_valid_initial(config, algebra, (held,)),
                    stalled=stalled)
    dense = replace(ordered_ready_valid_initial(dense_config, algebra, (held,)),
                    stalled=stalled)
    # No reachability assumption on the retained stability state, and no bound
    # on the time index. The omitted components alone carry the zero invariant.
    event = OrderedReadyValidEvent(reset, z3.Int('slot'), accepted, valid, ready,
                                   (payload,))
    got = ordered_ready_valid_step(config, state, event, algebra)
    old = ordered_ready_valid_step(dense_config, dense, event, algebra)
    differences = [got.state.accepted_count != 0,
                   old.state.accepted_count != 0,
                   got.state.stalled != old.state.stalled,
                   got.state.held[0] != old.state.held[0]]
    for field in ('hits', 'opportunities'):
        differences.extend(value != getattr(old, field)[name]
                           for name, value in getattr(got, field).items())
    for field in ('pending', 'owed', 'accepted_at', 'offered', 'reported'):
        differences.extend(value != (False if z3.is_bool(value) else 0)
                           for value in getattr(old.state, field))
    solver = z3.Solver()
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


def test_an_old_or_forged_queue_cannot_be_silently_discarded():
    config = OrderedReadyValidConfig(4, 1, None)
    old = ordered_ready_valid_initial(_dense(config), ConcreteAlgebra())
    event = OrderedReadyValidEvent(False, 0, False, True, True, (1,))
    with pytest.raises(ValueError, match='queue layout'):
        ordered_ready_valid_step(config, old, event, ConcreteAlgebra())


def test_uncounted_product_work_is_linear_in_slots_not_slots_squared():
    class CountedAlgebra:
        calls = 0

        def __getattr__(self, name):
            original = getattr(ConcreteAlgebra, name)
            def call(*args):
                self.calls += 1
                return original(*args)
            return call

    def work(horizon):
        config = OrderedReadyValidConfig(horizon, 1, None)
        algebra = CountedAlgebra()
        state = ordered_ready_valid_initial(config, algebra)
        setup = algebra.calls
        for slot in range(horizon):
            event = OrderedReadyValidEvent(False, slot, True, True, slot % 2 == 0, (1,))
            state = ordered_ready_valid_step(config, state, event, algebra).state
            assert state.pending == ()
        return algebra.calls - setup
    assert work(4096) == 8 * work(512)
