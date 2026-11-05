"""Concrete and state-observing replay share one pre-edge RTL slot boundary."""

import pytest

from behave.sim import api
from behave.sim.core.stimulus import AuditedTrace
from behave.sim.rtl.artifact import RTLArtifact


@pytest.mark.cosim
@pytest.mark.parametrize("edge", ["rising", "falling"])
@pytest.mark.parametrize("observe_state", [False, True])
@pytest.mark.parametrize("reset_slots", [0, 2])
def test_each_slot_observes_then_commits_exactly_once(
        tmp_path, edge, observe_state, reset_slots):
    design = tmp_path / "rtl.sv"
    event = "posedge" if edge == "rising" else "negedge"
    design.write_text(f"""module M (
        input clk, input rst, input in_valid, input [7:0] a,
        input out_ready, output in_ready, output out_valid, output [7:0] y
    );
        reg [7:0] q = 8'd5;
        assign in_ready = out_ready;
        assign out_valid = in_valid;
        assign y = q;
        always @({event} clk)
            if (rst) q <= 8'd5;
            else if (in_valid && in_ready) q <= q + a;
    endmodule
    """)
    contract = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("a", api.FiniteType(8), "a")],
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("y", api.FiniteType(8), "y")],
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst", edge=edge)],
    )
    stimulus = api.Stimulus(
        queues={"in": [(2,), (3,), (4,)]},
        slots=([
            api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 0})
            for _ in range(reset_slots)
        ] + [api.EventSlot(
            offers=("in",), controls={"rst": 0, "out_ready": ready})
            for ready in (1, 0, 1, 1)]),
    )
    artifact = RTLArtifact(design, "M", tmp_path / "run")
    trace = artifact.traces_per_process(
        contract, [stimulus], workers=1,
        state_names=("q",) if observe_state else (),
        state_widths={"q": 8} if observe_state else None,
    )[0]
    assert type(trace) is AuditedTrace, trace.as_dict()
    records = trace.records[reset_slots:]
    assert [dict(record.accepted).get("in") for record in records] == [
        (2,), None, (3,), (4,)]
    assert [dict(record.accepted).get("out") for record in records] == [
        (5,), None, (7,), (10,)]
    if observe_state:
        assert dict(trace.initial_state) == {"q": 5}
        assert [dict(record.state) for record in trace.records] == (
            [{"q": 5}] * reset_slots
            + [{"q": 7}, {"q": 7}, {"q": 10}, {"q": 14}])
