"""Real helper observations and the still-required public source search."""
import json
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.engines import probing
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.rtl.artifact import RTLArtifact


def _reached(trace):
    # HitVector iteration carries (goal, three-valued hit), not bare goal IDs.
    assert all(value is not None for row in trace.records for _, value in row.hits)
    return {goal for row in trace.records for goal, value in row.hits if value is True}


@pytest.fixture(params=[False, True], ids=['function', 'function-and-task'])
def helper(tmp_path, request):
    task = request.param
    source = '''module M(input clk,rst,in_valid,out_ready,input [3:0] in_data,
 output in_ready,output reg out_valid,output reg [3:0] out_data);
 function automatic [3:0] f(input [3:0] v);
   if(v[0]) f=v+1; else f=v-1;
 endfunction
 TASK
 assign in_ready=!out_valid || out_ready;
 always @(posedge clk) begin
   if(rst) begin out_valid<=0;out_data<=0;end
   else if(in_ready) begin
     out_valid<=in_valid;
     if(in_valid) begin
       if(in_data[3]) WRITE else WRITE
     end
   end
 end
endmodule'''
    source = source.replace('TASK', '''task automatic put(input [3:0] v);
 if(v[2]) out_data<=v; else out_data<=v;
 endtask''' if task else '').replace('WRITE', 'put(f(in_data));' if task else 'out_data<=f(in_data);')
    path = tmp_path/'design.sv'
    path.write_text(source)
    contract = api.TaskContract([
        api.Endpoint('in', 'in', [api.Field('data', api.FiniteType(4), 'in_data')],
                     api.ready_valid('in', 'in_valid', 'in_ready'), clock='clk', kind=api.Kind.TRANSACTION),
        api.Endpoint('out', 'out', [api.Field('data', api.FiniteType(4), 'out_data')],
                     api.ready_valid('out', 'out_valid', 'out_ready'), clock='clk', kind=api.Kind.TRANSACTION),
    ], [api.ClockDomain('clk', 'clk', 'rst')])
    artifact = RTLArtifact(path, 'M', tmp_path/'rtl')
    catalogue = artifact.frontend_catalogue(expected_identity=artifact.identity)
    binding = artifact.bind_observation(contract)
    assert binding.native_plan is not None, binding
    # A fixed fixture's source line identifies f's true arm, not a compiler
    # temporary name or a fabricated test goal. Both caller arms are retained.
    helper_start = source.index("if(v[0])")
    targets = tuple(sorted(goal.id for goal in catalogue.values()
                           if goal.family == "control-choice" and goal.event == "true"
                           and dict(goal.provenance)["start"] == helper_start))
    assert targets
    seed = api.Stimulus(queues={'in': [(0,)]}, slots=[
        *[api.EventSlot(reset=True, controls={'rst': 1, 'out_ready': 1}) for _ in range(3)],
        api.EventSlot(offers=('in',), controls={'rst': 0, 'out_ready': 1}),
        *[api.EventSlot(controls={'rst': 0, 'out_ready': 1}) for _ in range(2)],
    ])
    before = artifact.trace(contract, seed)
    assert type(before) is api.AuditedTrace, before
    assert _reached(before) and not set(targets).intersection(_reached(before))
    return SimpleNamespace(artifact=artifact, contract=contract, catalogue=catalogue,
                           binding=binding, targets=targets, seed=seed, baseline=before)


@pytest.mark.cosim
def test_helper_native_goals_follow_values_and_preserve_call_occurrences(tmp_path, helper):
    artifact, contract, seed = helper.artifact, helper.contract, helper.seed
    candidate = api.Stimulus(queues={"in": [(1,)]}, slots=seed.slots)
    after = artifact.trace(contract, candidate)
    assert type(after) is api.AuditedTrace, after
    assert set(helper.targets) <= _reached(after)
    for trace, expected in ((helper.baseline, 15), (after, 2)):
        assert [value for row in trace.records for name, value in row.accepted
                if name == "out" and value is not None] == [(expected,)]
        assert trace.source_evidence is not None
    # Both lexical call sites remain declared even though this input executes
    # only one of them. Concrete hits never redefine the source denominator.
    for target in helper.targets:
        assert sum(goal == target for _, goal in helper.catalogue.occurrences) == 2
    assert artifact.frontend_catalogue() is helper.catalogue
    assert artifact.observation_binding == helper.binding
    (tmp_path / "helper-native.json").write_text(json.dumps({
        "catalogue": helper.catalogue.as_dict(), "targets": helper.targets,
        "baseline": helper.baseline.as_dict(), "candidate": after.as_dict()}, indent=2))


@pytest.mark.cosim
def test_helper_goal_sat_is_independently_replayed(tmp_path, helper):
    artifact, contract, seed = helper.artifact, helper.contract, helper.seed
    catalogue, targets, binding = helper.catalogue, helper.targets, helper.binding
    provider = RTLGenerationProvider.from_artifact(artifact, contract,
        api.GenerationSpec(6, 1, 3, 2), catalogue,
        targetable_goals=targets, workdir=tmp_path/'search', timeout_ms=10000)
    selected = provider.select(seed)
    assert type(selected) is api.ProbeSelection and selected.requests, selected
    answer = provider.probe(seed, selected.requests[0])
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate.fingerprint != seed.fingerprint
    after = artifact.trace(contract, answer.candidate)
    assert type(after) is api.AuditedTrace, after
    assert set(targets).intersection(_reached(after))
    assert artifact.observation_binding == binding
