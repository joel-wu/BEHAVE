"""Recursive source storage: actual source indices, event masks and local queries."""

import pytest

from behave.sim.symbolic import ranges, typecheck as types
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.source_integral import IntegralType
from behave.sim.rtl.source_values import ArrayType

from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower, _names, _settle, _state, _step


def test_recursive_array_type_does_not_expand_storage_or_guess_missing_values():
    kind = ArrayType(4, 5, ArrayType(3, 1, IntegralType(8, True)))
    assert kind.ir_type == types.Array(2, types.Array(3))
    assert kind.decode([[255, 128, 1], [2, 3, 4]]) == [[-1, -128, 1], [2, 3, 4]]
    with pytest.raises(ValueError):
        kind.decode([[255, 128, 1], [2, None, 4]])
    with pytest.raises(ValueError):
        kind.decode([[255, 128, 1]])
    large = ArrayType(7, 1_000_006, IntegralType(32, False))
    assert large.domain.length == 1_000_000 and large.domain.runs == ()


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.solver
def test_nested_array_native_values_hits_and_symbolic_query_agree(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      logic signed [7:0] x[4:5][3:1];
      always @(posedge clk) begin
        for(int i=4;i<6;i++) for(int j=1;j<4;j++) x[i][j]=a+i-j;
        if(x[4][1]==12) q<=x[5][3]; else q<=x[4][1];
      end
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    assert machine.procedure.types[names["x"]] == types.Array(2, types.Array(3))
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    inputs = (0, 3, 9, 15, 1)
    actual, native_hits = replay(inputs)
    assert actual == [3, 6, 11, 18, 4]
    state = _state(machine, {"clk": 0, "a": 0, "q": 0,
                             "x": [[0, 0, 0], [0, 0, 0]]})
    for a, expected, reached in zip(inputs, actual, native_hits):
        state, _, hits, _, _ = _step(machine, state, 0, clk=0, a=a)
        state, during = _settle(machine, state)
        hits.update(during)
        state, _, _, _, _ = _step(machine, state, 0, clk=1, a=a)
        state, during = _settle(machine, state)
        hits.update(during)
        assert state[names["q"]] == expected
        assert state[names["x"]] == [[a+1, a+2, a+3], [a+2, a+3, a+4]]
        assert hits == reached

    initial = _state(machine, {"clk": 0, "a": 0, "q": 0,
                               "x": [[0, 0, 0], [0, 0, 0]]})
    goal = _goal(source, plan, "x[4][1]==12", "true", "control-choice")
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(machine, env, initial, {names["clk"]: 0, names["a"]: 0}, [goal],
        mutable_inputs=(names["a"],), following_inputs=({names["clk"]: 1, names["a"]: 0},),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer
    assert answer.candidate[names["a"]] == 9
    _, confirmed = replay((answer.candidate[names["a"]],))
    assert goal in confirmed[0]  # Solver SAT alone does not award a hit.


@pytest.mark.eda("slang")
@pytest.mark.solver
def test_array_write_masks_preserve_changed_then_restored_cell_events(tmp_path):
    source = """module M(input clk,input [3:0] a,output logic [7:0] q,r);
      logic [7:0] x[4:5][3:1];
      always @(posedge clk) begin x[4][1]=a; x[4][1]=0; end
      always_comb q=x[4][1];
      always_comb r=x[5][3];
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "r": 0,
                             "x": [[0, 0, 0], [0, 0, 0]]})
    state, _, _, _, _ = _step(machine, state, 0, clk=1, a=5)
    producer, = [i + 1 for i, flag in enumerate(machine.pending) if state[flag]]
    state, _, _, _, _ = _step(machine, state, producer)
    # Only [4][1]'s consumer is queued, despite its restored final value.
    assert state[names["x"]] == [[0, 0, 0], [0, 0, 0]]
    assert sum(bool(state[flag]) for flag in machine.pending) == 1
    state[names["q"]], state[names["r"]] = 7, 9
    settled, _ = _settle(machine, state)
    assert settled[names["q"]] == 0 and settled[names["r"]] == 9


@pytest.mark.eda("slang")
@pytest.mark.solver
def test_nested_assignment_pattern_and_array_copy_are_values(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      logic signed [7:0] x[3:2][1:0], copy[3:2][1:0];
      always @(posedge clk) begin
        x='{'{8'sd1,-8'sd2},'{8'sd3,8'sd4}};
        copy=x;
        x[3][0]=a;
        q=copy[3][0];
      end
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 0,
        "x": [[0, 0], [0, 0]], "copy": [[0, 0], [0, 0]]})
    state, _, _, _, _ = _step(machine, state, 0, clk=1, a=7)
    state, _ = _settle(machine, state)
    assert state[names["q"]] == 254
    assert state[names["x"]] == [[1, 7], [3, 4]]
    assert state[names["copy"]] == [[1, -2], [3, 4]]


@pytest.mark.eda("slang")
def test_array_definedness_is_not_filled_with_zero(tmp_path):
    source = ("module M(input clk,input [3:0] a,output reg [7:0] q); "
              "logic [7:0] x[4:5]; "
              "always @(posedge clk) q<=x[a]; endmodule")
    with pytest.raises(Refused, match="proved in-range"):
        _lower(tmp_path, source)


@pytest.mark.cosim
def test_clocked_array_query_uses_native_history_and_replays_only_its_proposal(tmp_path, monkeypatch):
    """Array query preparation alone is not a baseline-to-search acceptance gate."""
    from behave.sim import api
    from behave.sim.engines.rtl.source_boundary import SourceTraceBindings
    from behave.sim.generation import StimulusField
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.source_boundary_capture import boundaries_of

    source = """module M(input clk,rst,in_valid,input [3:0] a,
      output logic out_valid,output logic [3:0] y);
      logic signed [3:0] cells[4:5][3:2];
      always @(posedge clk) begin
        if(rst) begin
          out_valid<=0; y<=0;
          cells[4][3]<=0; cells[4][2]<=-1;
          cells[5][3]<=-2; cells[5][2]<=0;
        end else begin
          out_valid<=in_valid;
          if(in_valid) begin
            cells[4][3]<=a; cells[5][2]<=cells[4][3];
            if(a==9 && cells[4][3]==0 && cells[4][2]==-1) y<=a;
            else y<=a;
          end
        end
      end
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    scalar = api.FiniteType(4)
    contract = api.TaskContract([
        api.Endpoint("input", "in", [api.Field("a", scalar, "a")],
            api.sampled_edge("in", valid="in_valid"), "c", kind=api.Kind.TRANSACTION),
        api.Endpoint("output", "out", [api.Field("y", scalar, "y")],
            api.sampled_edge("out", valid="out_valid"), "c", kind=api.Kind.TRANSACTION,
            answers_per_transaction=1)],
        [api.ClockDomain("c", "clk", "rst", reset_restores=True)],
        protocol_instances=[api.ProtocolInstance("compute", ("input", "output"))])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b'class M:\n def reset(self): pass\n def process(self,a): return {"y":a}\n'},
        config={"class": "M"})
    admitted = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
        reference=reference)
    handle = api.prepare_rtl(admitted, api.RTLConfig(tmp_path / "design.sv", "M", tmp_path / "build"))
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=1))
    calls, run = [], RTLArtifact._traces_per_process

    def recorded(self, bound, seeds, *args, **kwargs):
        seeds = tuple(seeds)
        calls.append(seeds)
        return run(self, bound, seeds, *args, **kwargs)

    monkeypatch.setattr(RTLArtifact, "_traces_per_process", recorded)
    checked = handle.replay(baseline.stimuli, reference=reference).evaluation
    assert checked.complete and checked.passed, checked.as_dict()
    trace = checked.results[0].trace
    original = trace.as_dict()
    target = _goal(source, plan, "a==9", "true", "control-choice")
    assert not any(hit for row in trace.records for name, hit in row.hits if name == target)
    binding = SourceTraceBindings(machine, contract, trace.stimulus, trace)
    query = binding.at((StimulusField("input", 1, 0, binding.releases["input", 1]),))
    cells = next(name for name, field in machine.program.fields.items() if field.observation == "cells")
    assert boundaries_of(trace.query_boundaries)[query.slot]["cells"] == ((0, 15), (14, 0))
    assert query.state[cells] == [[0, -1], [-2, 0]]
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})

    def ask(state):
        return query_source_interval(machine, env, state, query.phases[0], (target,),
            mutable_inputs=query.mutable_inputs, following_inputs=query.phases[1:],
            event_bound=machine.event_bound, timeout_ms=30_000)

    answer = ask(query.state)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    # Holding a different actual history changes this local answer. Missing or
    # inconvenient array state must not become free input or a zero default.
    blocked = ask({**query.state, cells: [[1, -1], [-2, 0]]})
    assert blocked.outcome is probing.Outcome.LOCAL_UNSAT, blocked.detail
    assert len(calls) == 1
    proposal = query.proposal(answer.candidate)
    replayed = handle.replay((proposal,), reference=reference).evaluation
    assert replayed.complete and replayed.passed, replayed.as_dict()
    assert any(hit for row in replayed.results[0].trace.records for name, hit in row.hits if name == target)
    assert len(calls) == 2 and calls[-1] == (proposal,)
    assert trace.as_dict() == original


@pytest.mark.cosim
@pytest.mark.parametrize("drivers", [
    "always_comb begin cells[2]=a; cells[3]=a+1; end",
    "assign cells[2]=a; assign cells[3]=a+1;",
])
def test_public_search_uses_native_array_boundaries_without_replaying_baseline(tmp_path, record_property, drivers):
    import json

    from behave.sim import api
    from behave.sim.rtl.source_boundary_capture import boundaries_of

    source = """module M(input [15:0] a,output logic [15:0] y);
      logic [15:0] cells[2:3];
      always_comb begin cells[2]=a; cells[3]=a+1; end
      always_comb if(cells[3]==16'hc35c) y=cells[2]; else y=cells[2];
    endmodule"""
    source = source.replace("always_comb begin cells[2]=a; cells[3]=a+1; end", drivers)
    scalar = api.FiniteType(16)
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", scalar, "a")], api.settled("in"), None,
                     kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", scalar, "y")], api.settled("out"), None,
                     kind=api.Kind.TRANSACTION, answers_per_transaction=1)], [],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b'class M:\n    def reset(self):\n        pass\n'
                        b'    def process(self,a):\n        return {"y":a}\n'}, config={"class": "M"})
    path = tmp_path / "design.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    _machine, plan = _lower(tmp_path, source)
    target = _goal(source, plan, "cells[3]==16'hc35c", "true", "control-choice")
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=3))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "result", baseline=baseline, reference=reference,
        providers=handle.generation_providers(reference, workdir=tmp_path / "search")[1:],
        search_policy=api.SearchPolicy(time_budget_s=60, max_probes=4), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed
    baseline_ids = {seed.identity_bytes for seed in baseline.stimuli}
    seen, proposed, before, after = [], [], set(), set()
    for run in result.evaluation.results:
        hits = {goal for row in run.trace.records for goal, hit in row.hits if hit is True}
        after.update(hits)
        if run.stimulus.identity_bytes in baseline_ids:
            seen.append(run.stimulus.identity_bytes)
            before.update(hits)
            assert target not in hits
            observations = boundaries_of(run.trace.query_boundaries)
            assert observations
            assert all(len(row["cells"]) == 2 for row in observations.values())
            assert all(all(type(value) is int for value in row["cells"]) for row in observations.values())
        elif target in hits:
            proposed.append(run)
    assert set(seen) == baseline_ids and len(seen) == len(baseline_ids)
    assert proposed, result.generated.as_dict()
    assert result.analysis is None
    record_property("array_source_search", json.dumps({"baseline_members": len(seen),
        "baseline_lengths": sorted({len(rows) for seed in baseline.stimuli for _, rows in seed.queues}),
        "baseline_hits": len(before), "new_native_hits": len(after - before),
        "native_confirmed_inputs": len(proposed)}))
