"""Terminal input goals distinguish DUT stalls, missing drain and empty work."""

from dataclasses import replace

import pytest
import z3

from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.monitor import INPUT_COMPLETION, _input_completion
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.observation_audit import compare_native_history
from behave.sim.rtl.protocol_relation import SymbolicRequest
from behave.sim.rtl.source_confluence import compare_histories
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_task import SourceTaskRelation
from behave.sim.rtl.task_request import ReadyValidRequestCodec

from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract
from .test_rtl_source_memory_relation import _prepare
from .test_rtl_source_native_audit import _audit

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver, pytest.mark.cosim]


@pytest.fixture(scope='module', params=[(mode, private) for mode in
    ('stall', 'complete', 'empty', 'no-drain', 'complete-no-drain') for private in (False, True)])
def terminal_case(tmp_path_factory, request):
    mode, private = request.param
    root = tmp_path_factory.mktemp('source_terminal')
    source = HISTORY_SOURCE if mode.startswith('complete') else HISTORY_SOURCE.replace(
        'assign ir=!rst && (!ov || ordy);', 'assign ir=0;')
    machine, plan, _, template = _prepare(root, source, initialization=True)
    context = z3.Context() if private else None
    contract = _simple_contract()
    catalogue = manifest_of(plan.declarations, filename='design.sv')
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue), context=context,
                                  complete_state=True, occurrences=True)
    artifact = RTLArtifact(root/'design.sv', 'M', root/'native')
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact.identity)
    request = SymbolicRequest(ReadyValidRequestCodec(contract, 1, 1, initial=layout), 4, context=context)
    encoding = relation.encode_request(request, initial, binding)
    solver = z3.Solver(ctx=context)
    solver.set(timeout=30_000)
    solver.add(*encoding.constraints, request.lengths['in'] == int(mode != 'empty'),
               *(term == 0 for term in binding.values()))
    for i in range(4):
        solver.add(request.resets['c'][i] == (i == 0), request.actions[i].controls['ordy'],
                   request.actions[i].offers['in'] == (i == (3 if mode.endswith('no-drain') else 1)))
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    stimulus = request.codec.decode(rows)
    trace = artifact.exact_trace(contract, stimulus, observe_names=('y', 'ov', 'ir'),
        state_names=tuple(layout.widths), state_widths=dict(layout.widths), startup_state=layout.decode(rows))
    return mode, relation, request, encoding, rows, trace, catalogue


def test_terminal_query_and_public_native_monitor_agree(terminal_case):
    mode, relation, request, encoded, rows, trace, catalogue = terminal_case
    report = _input_completion(relation.contract, trace.stimulus, trace)
    assert bool(report.hits) == (mode == 'stall')
    assert bool(report.assumption_violations) == (mode == 'no-drain')
    assert not report.unresolved
    comparison = compare_native_history(relation, request, encoded, rows, trace, catalogue)
    audit, _ = _audit(comparison)
    if mode == 'no-drain':
        # The monitors agree, but this request violates the environment
        # domain. It must not be admitted as an artifact-failure witness.
        assert audit.status is AuditCheckStatus.INCONSISTENT, audit.as_dict()
        assert audit.as_dict()['findings'] == [
            'the native history is outside the source relation',
            'environment.environment.drain-missing']
    else:
        assert audit.status is AuditCheckStatus.AGREED, audit.as_dict()
    solver = z3.Solver(ctx=relation.context)
    solver.add(*encoded.constraints, *request.fix(rows), encoded.hits[INPUT_COMPLETION])
    assert solver.check() == (z3.sat if mode == 'stall' else z3.unsat)


def test_terminal_evidence_is_part_of_order_comparison(terminal_case):
    _, relation, _, encoded, _, _, _ = terminal_case
    changed = replace(encoded, terminal_hits={INPUT_COMPLETION:z3.Not(encoded.terminal_hits[INPUT_COMPLETION])})
    comparison = compare_histories(encoded, changed, tuple(relation.machine.program.fields))
    solver = z3.Solver(ctx=relation.context)
    solver.add(*comparison.constraints, z3.Not(comparison.different))
    assert solver.check() == z3.unsat
    with pytest.raises(ValueError, match='same terminal boundary'):
        compare_histories(encoded, replace(encoded, terminal_hits=None), tuple(relation.machine.program.fields))
