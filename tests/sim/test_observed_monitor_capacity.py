"""A complete concrete ledger bounds queue slots by its accepted requests."""
from dataclasses import replace
from itertools import product
from unittest.mock import patch

import pytest

from behave.sim.core import monitor as monitor_module
from behave.sim.core.ledger import ChannelSlot, Ledger, SlotFact
from behave.sim.core.monitor import Monitor
from tests.sim.ordered_monitor_oracle import DenseOrderedOracle
from tests.sim.test_api_path import contract


def ledger_for(task, events, *, prefix=2, truncated=False):
    rows = [SlotFact(i, True, {}) for i in range(prefix)]
    for index, (accepted, valid, ready, payload) in enumerate(events, prefix):
        rows.append(SlotFact(index, False, {
            'in': ChannelSlot('in', transferred=accepted, payload=(0,)),
            'out': ChannelSlot('out', offered=valid, ready=ready,
                               transferred=valid and ready, payload=(payload,)),
        }))
    return Ledger(task, rows, truncated=truncated)


def report_image(report):
    return {name: (dict(report.opportunities) if name == 'opportunities'
                   else tuple(row.as_dict() if hasattr(row, 'as_dict') else row
                              for row in getattr(report, name)))
            for name in report.__slots__}


def dense_report(task, ledger):
    class DenseMonitor(Monitor):
        def ordered_channel_config(self, capacity):
            return super().ordered_channel_config(max(1, len(ledger)))
    with patch.object(monitor_module, 'OrderedConcreteMonitor', DenseOrderedOracle):
        return DenseMonitor(task)._finite_ordered_check(ledger)


@pytest.mark.parametrize('answers', [1, 2])
def test_all_short_event_paths_preserve_complete_dense_report(answers):
    task = contract(answers=answers, max_outstanding=1, deadline=1)
    monitor = Monitor(task)
    events = tuple(product((False, True), repeat=4))
    for path in product(events, repeat=3):
        ledger = ledger_for(task, path)
        assert report_image(monitor._finite_ordered_check(ledger)) == report_image(
            dense_report(task, ledger))


def test_capacity_counts_acceptances_not_horizon_or_declared_in_flight_limit(monkeypatch):
    task = contract(answers=2, max_outstanding=1, deadline=2)
    original = monitor_module.OrderedConcreteMonitor
    capacities = []

    class RecordingMonitor(original):
        def step(self, event):
            capacities.append(self.config.queue_capacity)
            return super().step(event)

    monkeypatch.setattr(monitor_module, 'OrderedConcreteMonitor', RecordingMonitor)
    for horizon in (64, 128, 256):
        ledger = ledger_for(task, [(index < 4, False, True, 0)
                                  for index in range(horizon)], prefix=0)
        capacities.clear()
        report = Monitor(task)._finite_ordered_check(ledger)
        assert capacities == [4] * horizon
        assert any(row.goal == 'monitor.outstanding-exceeded' for row in report.violations)
        assert sum(row.goal == 'monitor.response-deadline' for row in report.violations) == 4


@pytest.mark.parametrize('answers', [1, 3, None])
def test_no_acceptance_keeps_unsolicited_output_stability_and_terminal_evidence(answers):
    task = contract(answers=answers)
    ledger = ledger_for(task, [(False, True, False, 3), (False, True, False, 4),
                               (False, False, True, 4), (False, True, True, 5)])
    report = Monitor(task)._finite_ordered_check(ledger)
    assert report_image(report) == report_image(dense_report(task, ledger))
    assert any(row.goal == 'monitor.sink-stability' for row in report.violations)
    assert any(row.goal == 'monitor.output-without-transaction'
               for row in report.violations) == (answers is not None)


def test_rejected_fast_path_stays_rejected():
    task = contract(answers=1)
    valid = ledger_for(task, [(True, False, True, 0)])
    variants = [Ledger(task, valid.slots, truncated=True),
                Ledger(task, [*valid.slots, SlotFact(3, True, {})]),
                ledger_for(task, [(None, False, True, 0)])]
    for ledger in variants:
        assert Monitor(task)._finite_ordered_check(ledger) is None


def test_terminal_debt_after_multiple_answers_matches_dense_report():
    task = contract(answers=3)
    ledger = ledger_for(task, [(True, False, True, 0), (True, True, True, 0),
                               (False, True, True, 0), (False, True, True, 0),
                               (False, True, False, 1)])
    report = Monitor(task)._finite_ordered_check(ledger)
    assert report_image(report) == report_image(dense_report(task, ledger))
    assert len(report.unresolved) == 1
    assert dict(report.unresolved[0].about)['owed'] == 3


@pytest.mark.solver
@pytest.mark.parametrize('answers', [1, 3])
def test_omitted_suffix_stays_zero_and_retained_transition_is_identical(answers):
    import z3

    from behave.sim.core.ready_valid_product import (
        OrderedReadyValidConfig,
        OrderedReadyValidEvent,
        ordered_ready_valid_initial,
        ordered_ready_valid_step,
    )
    from behave.sim.rtl.task_symbolic import Z3Algebra

    class SymbolicTime(Z3Algebra):
        @staticmethod
        def integer(value):
            return value if z3.is_int(value) else z3.IntVal(value)

    config = OrderedReadyValidConfig(2, 1, answers, 1, 2)
    dense_config = replace(config, capacity=5)
    algebra = SymbolicTime()
    state = replace(ordered_ready_valid_initial(config, algebra),
                    accepted_count=z3.Int('count'), stalled=z3.Bool('stalled'),
                    held=(z3.BitVec('held', 8),))
    arrays = {}
    for name in ('pending', 'owed', 'accepted_at', 'offered', 'reported'):
        constructor = z3.Int if name in ('owed', 'accepted_at') else z3.Bool
        arrays[name] = tuple(constructor(f'{name}_{i}') for i in range(2))
    state = replace(state, **arrays)
    dense = replace(ordered_ready_valid_initial(dense_config, algebra),
                    accepted_count=state.accepted_count, stalled=state.stalled, held=state.held,
                    **{name: values + (z3.IntVal(0) if name in ('owed', 'accepted_at')
                                       else z3.BoolVal(False),) * 3
                       for name, values in arrays.items()})
    event = OrderedReadyValidEvent(z3.Bool('reset'), z3.Int('slot'), z3.Bool('accept'),
                                   z3.Bool('valid'), z3.Bool('ready'), (z3.BitVec('payload', 8),))
    compact_step = ordered_ready_valid_step(config, state, event, algebra)
    dense_step = ordered_ready_valid_step(dense_config, dense, event, algebra)
    differences = [compact_step.state.accepted_count != dense_step.state.accepted_count,
                   compact_step.state.stalled != dense_step.state.stalled,
                   compact_step.state.held[0] != dense_step.state.held[0]]
    for name in arrays:
        left, right = getattr(compact_step.state, name), getattr(dense_step.state, name)
        differences.extend(a != b for a, b in zip(left, right))
        zero = 0 if name in ('owed', 'accepted_at') else False
        differences.extend(value != zero for value in right[2:])
    for name in ('hits', 'opportunities'):
        differences.extend(value != getattr(dense_step, name)[key]
                           for key, value in getattr(compact_step, name).items())
    differences.extend(a != b for a, b in zip(compact_step.details['deadline_hits'],
                                             dense_step.details['deadline_hits']))
    differences.extend(dense_step.details['deadline_hits'][2:])
    solver = z3.Solver()
    # The complete observed ledger bounds the number of acceptances, not just
    # outstanding requests. Its prefixes satisfy this even on faulty outputs.
    solver.add(0 <= state.accepted_count, state.accepted_count <= 2,
               z3.Implies(event.input_accepted, state.accepted_count < 2),
               z3.Or(*differences))
    assert solver.check() == z3.unsat
