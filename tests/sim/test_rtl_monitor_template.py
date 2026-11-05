"""The instantiated monitor must be the shared rule for every supplied state."""

from dataclasses import fields, is_dataclass, replace
from itertools import product

import pytest
import z3

from behave.sim.core.ready_valid_product import (
    OrderedReadyValidConfig,
    OrderedReadyValidEvent,
    OrderedReadyValidState,
    ordered_ready_valid_initial,
    ordered_ready_valid_step,
)
from behave.sim.rtl.monitor_template import define_monitor_state
from behave.sim.rtl.task_symbolic import Z3Algebra


@pytest.mark.parametrize('answers', [None, 1, 2])
def test_native_monitor_constants_keep_every_field_and_private_context(answers):
    from behave.sim.core.ready_valid_product import ConcreteAlgebra, ordered_ready_valid_state_image
    from behave.sim.rtl.monitor_template import constant_monitor_state

    context = z3.Context()
    config = OrderedReadyValidConfig(3, 1, answers, reset_policy='keeps')
    native = ordered_ready_valid_initial(config, ConcreteAlgebra, (0,))
    native = ordered_ready_valid_step(config, native, OrderedReadyValidEvent(
        False, 7, True, True, False, (13,)), ConcreteAlgebra).state
    shape = ordered_ready_valid_initial(config, Z3Algebra(context), (z3.BitVecVal(0, 8, ctx=context),))
    lifted = constant_monitor_state(native, shape)
    def read(term):
        assert term.ctx == context
        return z3.is_true(term) if z3.is_bool(term) else term.as_long()
    assert ordered_ready_valid_state_image(lifted, read) == ordered_ready_valid_state_image(native)
    with pytest.raises(ValueError, match='declared carrier'):
        constant_monitor_state(replace(native, held=(256,)), shape)


@pytest.mark.parametrize('private', [False, True])
@pytest.mark.parametrize('identified', [False, True])
@pytest.mark.parametrize('policy', [None, 'clears', 'keeps'])
def test_monitor_frames_preserve_all_fields_for_arbitrary_histories(private, identified, policy):
    from behave.sim.rtl.atomic_monitors import AtomicMonitorState
    from behave.sim.rtl.symbolic_state import image_terms

    ctx = z3.Context() if private else None
    config = OrderedReadyValidConfig(3, 1, 2, 2, 1, policy,
                                    identifier_index=0 if identified else None)
    original, event = unconstrained(config, (z3.BitVecSort(8, ctx=ctx),), 'frame', ctx=ctx)
    if identified:
        original = replace(original, keys=tuple(z3.BitVec(f'key_{i}', 8, ctx=ctx) for i in range(3)))
        event = replace(event, input_key=(z3.BitVec('request_key', 8, ctx=ctx),))
    framed = original
    algebra, definitions, differences = Z3Algebra(ctx), [], []
    for slot in range(3):
        item = replace(event, slot=slot, reset=z3.Bool(f'reset_{slot}', ctx=ctx),
                       input_accepted=z3.Bool(f'accepted_{slot}', ctx=ctx),
                       output_ready=z3.Bool(f'ready_{slot}', ctx=ctx))
        before = ordered_ready_valid_step(config, original, item, algebra)
        after = ordered_ready_valid_step(config, framed, item, algebra)
        # MappingProxyType and dataclass layouts are the production shape.
        compact, equations = define_monitor_state(AtomicMonitorState({('clock', 'out'): after.state}, {}))
        framed = compact.protocol[('clock', 'out')]
        definitions.extend(equations)
        lhs = AtomicMonitorState({('clock', 'out'): before.state}, {}).image()
        differences.extend(x != y for x, y in zip(image_terms(lhs), image_terms(compact.image()), strict=True))
        for name in ('hits', 'opportunities', 'details'):
            differences.extend(x != y for x, y in zip(image_terms(getattr(before, name)),
                                                      image_terms(getattr(after, name)), strict=True))
        original = before.state
    solver = z3.Solver(ctx=ctx)
    solver.add(*definitions)
    assert solver.check() == z3.sat  # Definitions must not create an empty domain.
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat
    assert all(z3.is_const(term) for term in image_terms(compact.image()))


def assert_same(left, right, *, normalize=False):
    if is_dataclass(left):
        assert type(left) is type(right)
        for field in fields(left):
            assert_same(getattr(left, field.name), getattr(right, field.name), normalize=normalize)
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            assert_same(left[key], right[key], normalize=normalize)
    elif isinstance(left, tuple):
        for old, new in zip(left, right, strict=True):
            assert_same(old, new, normalize=normalize)
    else:
        # Identical syntax is stronger than an equivalence under selected inputs.
        if normalize:
            # Python's reflected numeral comparison can spell 0 == 1 rather
            # than 1 == 0 after binding constants; this is not a changed rule.
            assert z3.is_true(z3.simplify(left == right)), (left, right)
        else:
            assert left.eq(right), (left, right)


def unconstrained(config, sorts, prefix, *, ctx=None):
    def boolean(name):
        return z3.Bool(f"{prefix}_{name}", ctx=ctx)

    def integer(name):
        return z3.Int(f"{prefix}_{name}", ctx=ctx)

    state = OrderedReadyValidState(
        accepted_count=integer("accepted_count"),
        pending=tuple(boolean(f"pending_{i}") for i in range(config.queue_capacity)),
        owed=tuple(integer(f"owed_{i}") for i in range(config.queue_capacity)),
        accepted_at=tuple(integer(f"accepted_at_{i}") for i in range(config.queue_capacity)),
        offered=tuple(boolean(f"offered_{i}") for i in range(config.queue_capacity)),
        reported=tuple(boolean(f"reported_{i}") for i in range(config.queue_capacity)),
        stalled=boolean("stalled"),
        held=tuple(z3.Const(f"{prefix}_held_{i}", sort) for i, sort in enumerate(sorts)))
    event = OrderedReadyValidEvent(
        reset=boolean("reset"), slot=13, input_accepted=boolean("accepted"),
        output_valid=boolean("valid"), output_ready=boolean("ready"),
        output_payload=tuple(z3.Const(f"{prefix}_out_{i}", sort)
                             for i, sort in enumerate(sorts)))
    return state, event


CONFIGURATIONS = list(product((1, 3), (None, 1, 3), (None, 0, 4), (None, 1)))

