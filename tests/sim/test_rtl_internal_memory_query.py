"""Implicit protocol queries bind finite internal RAM to native word history."""

import pytest

from behave.sim import api
from behave.sim.core.monitor import monitor_for
from behave.sim.engines import probing
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.rtl.artifact import RTLArtifact


def _ram_source(shape, *, dual_write=False):
    declarations, words, address = {
        "ascending": ("[2:5]", ["[2]", "[3]", "[4]", "[5]"], "[2+a[1:0]]"),
        "descending": ("[5:2]", ["[2]", "[3]", "[4]", "[5]"], "[2+a[1:0]]"),
        "nested": ("[4:5][3:2]", ["[4][3]", "[4][2]", "[5][3]", "[5][2]"],
                   "[4+a[1]][3-a[0]]"),
    }[shape]
    reset = " ".join(f"cells{word}<=4'd{value};" for word, value in zip(words, (2, 9, 12, 15)))
    other_address = "[4+a[3]][3-a[2]]" if shape == "nested" else "[2+a[3:2]]"
    second_write = f"cells{other_address} <= a ^ 4'hf;" if dual_write else ""
    return f"""module M(input clk,rst,in_valid,out_ready,input [3:0] a,
      output in_ready,output logic out_valid,output logic [3:0] y);
      logic signed [3:0] cells{declarations};
      assign in_ready=!out_valid || out_ready;
      always @(posedge clk) begin
        if(rst) begin out_valid<=0; y<=0; {reset} end
        else if(in_valid && in_ready) begin
          cells{address}<=a;
          {second_write}
          y<=cells{address};
          out_valid<=(a!=cells{words[1]});
        end else if(out_ready) out_valid<=0;
      end
    endmodule"""


def _contract():
    scalar = api.FiniteType(4)
    return api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", scalar, "a")],
            api.ready_valid("in", "in_valid", "in_ready"), "c",
            kind=api.Kind.TRANSACTION, response_deadline=2),
        api.Endpoint("out", "out", [api.Field("y", scalar, "y")],
            api.ready_valid("out", "out_valid", "out_ready"), "c",
            kind=api.Kind.TRANSACTION, answers_per_transaction=1)],
        [api.ClockDomain("c", "clk", "rst")])


@pytest.mark.cosim
@pytest.mark.parametrize("dual_write", [False, True])
@pytest.mark.parametrize("shape", ["ascending", "descending", "nested"])
def test_internal_ram_monitor_query_replays_from_actual_history(tmp_path, shape, dual_write):
    source = _ram_source(shape, dual_write=dual_write)
    path = tmp_path / "design.sv"
    path.write_text(source)
    contract = _contract()
    seed = api.Stimulus(queues={"in": [(5,), (1,)]}, slots=[
        api.EventSlot(offers=("in",) if i in (1, 4) else (), reset=i == 0,
                      controls={"rst": int(i == 0), "out_ready": 1}) for i in range(8)])
    artifact = RTLArtifact(path, "M", tmp_path / "native")
    manifest = artifact.frontend_catalogue(expected_identity=artifact.identity)
    goal = "monitor.response-deadline"
    provider = RTLGenerationProvider.from_artifact(
        artifact, contract, api.GenerationSpec(8, 2, 1, 3), manifest,
        targetable_goals=(goal,), workdir=tmp_path / "query", timeout_ms=5000)
    selection = provider.select(seed)
    assert type(selection) is api.ProbeSelection, selection
    assert goal not in selection.replay.hits
    assert len(selection.requests) == 2
    result = provider.probe(seed, selection.requests[-1])
    assert result.outcome is probing.Outcome.SAT, result.detail
    # A colliding second NBA wins. The prior transaction overwrote the initial 9;
    # a query which invents initial/reset state rather than binding native
    # history gets this wrong.
    stored = 10 if dual_write else 5
    assert dict(result.candidate.queues)["in"] == ((5,), (stored,))
    assert result.candidate.slots == seed.slots
    trace = artifact.trace(contract, result.candidate)
    assert type(trace) is api.AuditedTrace, trace
    assert any(hit.goal == goal and 4 <= hit.slot < 8
               for hit in monitor_for(contract).check(result.candidate, trace).hits)
    assert path.read_text() == source

