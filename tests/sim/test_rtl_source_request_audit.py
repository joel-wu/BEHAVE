"""Public request coordinates must cover private construction as well as order."""

import pytest
import z3

from behave.sim.core.audit import AuditCheckStatus
from behave.sim.rtl.initial_request import InitialStateCoordinates
from behave.sim.rtl.protocol_relation import SymbolicRequest
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_task import SourceTaskRelation
from behave.sim.rtl.task_request import ReadyValidRequestCodec

from .test_rtl_source_confluence import _all_phases, _audit
from .test_rtl_source_history import HISTORY_SOURCE, _simple_contract
from .test_rtl_source_memory_relation import _prepare

pytestmark = [pytest.mark.eda('slang'), pytest.mark.solver]


def _pair(tmp_path, source=HISTORY_SOURCE, *, private=False):
    machine, plan, _, template = _prepare(tmp_path, source, initialization=True)
    context = z3.Context() if private else None
    relation = SourceTaskRelation(template, machine, _simple_contract(),
        tuple(manifest_of(plan.declarations, filename='design.sv')),
        context=context, complete_state=True, occurrences=True)
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, _ = initial.request_layout(machine.program, 'a'*64)
    request = SymbolicRequest(ReadyValidRequestCodec(relation.contract, 2, 1, initial=layout),
                              3, context=context)
    return relation, request


@pytest.mark.parametrize('private', [False, True])
def test_full_request_audit_includes_independent_private_construction(tmp_path, private):
    relation, request = _pair(tmp_path, private=private)
    pair = relation.compare_request(request)
    for history in (pair.left, pair.right):
        assert len(history.sample_frames) == len(history.source.slots)
        assert all(len(frame) == 1 + (index > 0) + len(relation.protocol.observed_widths)
                   for index, frame in enumerate(history.sample_frames))
        roots = {term.get_id() for term in history.constraints}
        assert all(term.get_id() in roots for frame in history.sample_frames for term in frame)
    audit, calls = _audit(pair)
    assert audit.status is AuditCheckStatus.AGREED, audit
    assert calls == _all_phases(pair)
    assert any('initial' in str(term) and 'left' in str(term) for term in pair.left.constraints)
    assert any('initial' in str(term) and 'right' in str(term) for term in pair.right.constraints)


def test_private_construction_that_changes_startup_cannot_be_ignored(tmp_path):
    # ir is continuously driven, so native register construction cannot deposit
    # it. Reading its pre-settle value in initial makes that private choice
    # observable, even if every later transaction/reset produces equal outputs.
    source = HISTORY_SOURCE.replace('reg [1:0] saved;',
        'reg [1:0] saved; initial saved=ir ? 1 : 2;')
    relation, request = _pair(tmp_path, source)
    pair = relation.compare_request(request)
    audit, calls = _audit(pair)
    assert audit.status is AuditCheckStatus.REFUSED
    assert calls == ['feasibility', 'completeness', 'definedness', 'memory', 'confluence:startup']
    solver = z3.Solver()
    solver.add(*pair.constraints, pair.different)
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    assert request.codec.decode(rows)


def test_source_request_comparison_rejects_an_incomplete_layout(tmp_path):
    relation, request = _pair(tmp_path)
    old = request.codec.initial
    partial = InitialStateCoordinates(old.artifact, {}, startup_pins=old.startup_pins)
    request = SymbolicRequest(ReadyValidRequestCodec(relation.contract, 2, 1, initial=partial), 3)
    with pytest.raises(ValueError, match='complete compiler layout'):
        relation.compare_request(request)
    with pytest.raises(ValueError, match='complete construction coordinates'):
        relation.compare_request(None)
