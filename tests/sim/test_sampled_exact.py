"""Sampled transfers keep native and symbolic monitor semantics identical."""

import itertools

import pytest
import z3

from behave.sim import api
from behave.sim.core.monitor import Monitor
from behave.sim.core.protocol import adapter_program_for
from behave.sim.core.protocol_exact import AdapterExactTable
from behave.sim.core.stimulus import EventSlot
from behave.sim.rtl.task_symbolic import Z3Algebra
from tests.sim.test_protocol_adapters import _evaluate
from tests.sim.test_sampled_protocol import _contract, _Doubling, _task


@pytest.mark.parametrize("input_valid,output_valid", tuple(itertools.product((False, True), repeat=2)))
def test_table_matches_every_sampled_control_row(input_valid, output_valid):
    contract = _contract(input_valid=input_valid, output_valid=output_valid)
    program = adapter_program_for(contract)
    table = AdapterExactTable(program, contract)
    # Every admitted control combination is compiled, independent of payload bits.
    expected_rows = (4 if input_valid else 3) * (2 if output_valid else 1)
    assert len(table.transitions) == expected_rows
    for row, value in itertools.product(table.transitions, (0, 37, 255)):
        present = dict(row.presented)["in"] and not row.reset
        controls = {"rst": int(row.reset)}
        slot = EventSlot(reset=row.reset, offers=("in",) if present else (), controls=controls)
        drive = program.drive(contract, table.state_at(row.state), slot,
                              (value,) if present else None, controls)
        signals = {"out_y": value, **{k: int(v) for k, v in row.observed}}
        native = program.step(contract, table.state_at(row.state), slot, drive, signals)
        assert row.accepted == tuple(k for k, v in native.accepted_inputs.items() if v is not None)
        assert row.emitted == tuple(native.emitted_outputs)
        assert row.environment_admissible
    assert not any(not row.reset and not dict(row.presented)["in"]
                   for row in table.transitions) or input_valid
    missing = table.symbolic(
        z3.IntVal(table.initial_state), z3.BoolVal(False), {"in": z3.BoolVal(False)},
        {}, {name: z3.BoolVal(False) for name in table.observed_names}, Z3Algebra())
    assert z3.is_true(z3.simplify(missing.valid)) is input_valid
    assert z3.is_false(z3.simplify(missing.accepted["in"]))


@pytest.mark.parametrize("input_valid,output_valid", tuple(itertools.product((False, True), repeat=2)))
@pytest.mark.parametrize("spurious", (False, True))
def test_shared_ordered_monitor_agrees_with_native_general_monitor(input_valid, output_valid, spurious):
    contract = _contract(input_valid=input_valid, output_valid=output_valid, deadline=2)
    task, reference = _task(contract)
    stimulus = api.reset_then(contract, [(2,), (37,), (7,)], drain=3 if input_valid else 0)
    result = _evaluate(task, stimulus, _Doubling(pipeline=output_valid, spurious=spurious), reference)
    monitor = Monitor(contract)
    from behave.sim.core.ledger import ledger_of

    ledger = ledger_of(contract, stimulus, result.records[0])
    shared = monitor._finite_ordered_check(ledger)
    assert shared is not None
    general = monitor._general_check(ledger)
    for report_field in ("violations", "unresolved"):
        project = lambda report, field=report_field: [(r.goal, r.slot, r.endpoint) for r in getattr(report, field)]
        assert project(shared) == project(general)

