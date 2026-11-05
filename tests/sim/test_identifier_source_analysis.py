"""ID legality is explicit, order-audited and native-audited, not a safety hit."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import z3

from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.refusal import Refused
from behave.sim.rtl.analysis_job import _SourceAnalysis
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.observation_audit import compare_native_history
from behave.sim.rtl.protocol_relation import SymbolicRequest
from behave.sim.rtl.source_confluence import compare_histories
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_task import SourceTaskRelation
from behave.sim.rtl.task_request import ReadyValidRequestCodec

from .test_rtl_public_protocol_analysis import _fixture
from .test_rtl_source_memory_relation import _prepare
from .test_rtl_source_monitors import _monitors
from .test_rtl_source_native_audit import _audit

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver, pytest.mark.cosim]


@pytest.fixture(scope="module", params=[False, True], ids=["default-context", "private-context"])
def identified(tmp_path_factory, request):
    root = tmp_path_factory.mktemp("identified_source")
    contract, source, _, _ = _fixture("reordered", False)
    source = source.replace("module dut(", "module M(")
    machine, plan, _, template = _prepare(root, source, initialization=True)
    context = z3.Context() if request.param else None
    catalogue = manifest_of(plan.declarations, filename="design.sv")
    relation = SourceTaskRelation(template, machine, contract, tuple(catalogue), context=context,
        complete_state=True, occurrences=True, monitors=_monitors(contract))
    artifact = RTLArtifact(root / "design.sv", "M", root / "native")
    initial = relation.initial_state(dict.fromkeys(machine.program.fields))
    layout, binding = initial.request_layout(machine.program, artifact.identity)
    request = SymbolicRequest(ReadyValidRequestCodec(contract, 2, 1, initial=layout), 7, context=context)
    encoded = relation.encode_request(request, initial, binding)
    fixed = [request.lengths["in"] == 2, request.queues["in"][0][0] == False,
             request.queues["in"][0][1] == 1, request.queues["in"][1][1] == 2,
             *(value == 0 for value in binding.values())]
    for i in range(7):
        fixed.extend((request.resets["clk"][i] == (i == 0),
                      request.actions[i].controls["out_ready"] == True,
                      request.actions[i].offers["in"] == (i in (1, 2))))
    return relation, request, encoded, fixed, artifact, layout, catalogue


def test_duplicate_id_is_present_in_unfiltered_domain_but_not_a_legal_query(identified):
    relation, request, encoded, fixed, _, _, _ = identified
    # The worker reads the owner's template only to choose between the bound
    # request history (no direct interval) and a direct encoding; this fixture
    # supplies the bound history.
    owner = SimpleNamespace(contract=relation.contract, machine=relation.machine,
        template=SimpleNamespace(direct=None),
        trace_domain=lambda _horizon, **_scope: None,
        request_coordinates=lambda _horizon: request,
        bind_request=lambda _: (relation, request, encoded))
    exact = _SourceAnalysis(owner, 7)
    solver = z3.Solver(ctx=relation.context)
    solver.add(*encoded.constraints, *fixed, request.queues["in"][1][0] == False)
    assert solver.check() == z3.sat
    model = solver.model()
    assert any(z3.is_true(model.eval(term)) for term in encoded.environment_assumptions.values())
    assert not any(z3.is_true(model.eval(term)) for term in encoded.domain_faults.values())
    solver.add(*exact.constraints)
    assert solver.check() == z3.unsat
    solver = z3.Solver(ctx=relation.context)
    solver.add(*exact.constraints, *fixed, request.queues["in"][1][0] == True)
    assert solver.check() == z3.sat  # Legal out-of-order responses must remain in the domain.


def test_id_legality_cannot_disappear_from_confluence_or_native_audit(identified):
    relation, request, encoded, fixed, artifact, layout, catalogue = identified
    solver = z3.Solver(ctx=relation.context)
    solver.add(*encoded.constraints, *fixed, request.queues["in"][1][0] == True)
    assert solver.check() == z3.sat
    rows = request.rows(solver.model())
    stimulus = request.codec.decode(rows)
    trace = artifact.exact_trace(relation.contract, stimulus,
        observe_names=("in_ready", "out_valid", "out_id", "out_data"),
        state_names=tuple(layout.widths), state_widths=dict(layout.widths), startup_state=layout.decode(rows))
    output_ids = [dict(record.accepted)["out"][0] for record in trace.records
                  if dict(record.accepted).get("out") is not None]
    assert output_ids == [1, 0]
    comparison = compare_native_history(relation, request, encoded, rows, trace, catalogue)
    audit, _ = _audit(comparison)
    assert audit.status is AuditCheckStatus.AGREED, audit.as_dict()
    assumptions = list(encoded.monitor_assumptions)
    assumptions[2] = {name: z3.BoolVal(True, ctx=relation.context) for name in assumptions[2]}
    assert assumptions[2]
    changed = replace(encoded, monitor_assumptions=tuple(assumptions))
    pair = compare_histories(encoded, changed, tuple(relation.machine.program.fields))
    assert any("monitor.assumptions" in name for name in pair.differences)
    comparison = compare_native_history(relation, request, changed, rows, trace, catalogue)
    audit, _ = _audit(comparison)
    assert audit.status is not AuditCheckStatus.AGREED
    with pytest.raises(Refused, match="every definedness predicate"):
        compare_native_history(relation, request, replace(encoded, monitor_assumptions=()),
                               rows, trace, catalogue)
