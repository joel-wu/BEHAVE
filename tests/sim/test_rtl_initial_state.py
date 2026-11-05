"""Source replay records the state before the first slot, not after its reset."""

from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
from behave.sim.rtl.artifact import RTLArtifact

pytestmark = pytest.mark.cosim


def initial_fixture(tmp_path, atomic, asynchronous, active_low, edge,
                    *, declared_initial=True, width=3, primary_domain="a"):
    clocks = [api.ClockDomain(
        primary_domain, "clk", "rst", reset_hold_cycles=1,
        reset_active_low=active_low, edge=edge)]
    if atomic:
        clocks.append(api.ClockDomain(
            "b", "b_clk", "b_rst", reset_hold_cycles=1,
            reset_active_low=active_low, edge=edge))
    contract = api.TaskContract(
        clocks=clocks,
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("data", api.FiniteType(3), "data")],
                api.ready_valid(api.Direction.IN, "in_valid", "in_ready"),
                clock=primary_domain, kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("value", api.FiniteType(width), "value")],
                api.ready_valid(api.Direction.OUT, "out_valid", "out_ready"),
                clock=primary_domain, kind=api.Kind.TRANSACTION),
        ],
    )
    clock_edge = "posedge" if edge == "rising" else "negedge"
    reset_edge = "negedge" if active_low else "posedge"
    reset_test = "!rst" if active_low else "rst"
    sensitivity = f"{clock_edge} clk"
    if asynchronous:
        sensitivity += f" or {reset_edge} rst"
    extra_ports = ", input logic b_clk, b_rst, output logic [2:0] b_value" if atomic else ""
    extra_body = ""
    if atomic:
        b_sensitivity = f"{clock_edge} b_clk"
        if asynchronous:
            b_sensitivity += f" or {reset_edge} b_rst"
        b_reset_test = "!b_rst" if active_low else "b_rst"
        extra_body = f"""
    {"initial b_value = 3'd3;" if declared_initial else ""}
    always @({b_sensitivity})
        if ({b_reset_test}) b_value <= 0; else b_value <= b_value + 1'b1;
"""
    rtl = tmp_path / "top.sv"
    rtl.write_text(f"""
module top(
    input logic clk, rst, in_valid, out_ready,
    input logic [2:0] data,
    output logic in_ready, out_valid,
    output logic [{width - 1}:0] value{extra_ports}
);
    assign in_ready = 1'b1;
    assign out_valid = 1'b1;
    {"initial value = 3'd5;" if declared_initial else ""}
    always @({sensitivity})
        if ({reset_test}) value <= 0; else value <= value + 1'b1;
    {extra_body}
endmodule
""")
    domains = tuple(clock.name for clock in clocks)
    slots = [api.EventSlot(
        controls={"out_ready": 1, "rst": reset ^ int(active_low),
                  **({"b_rst": reset ^ int(active_low)} if atomic else {})},
        reset=bool(reset) if not atomic else False,
        ticks=domains, resets=domains if atomic and reset else (),
    ) for reset in (1, 0)]
    schedule = None
    if atomic:
        schedule = GlobalSchedule(
            [ClockTiming(clock.name, 10, clock=clock.clock, reset=clock.reset,
                         edge=edge, reset_active_low=active_low)
             for clock in clocks],
            [GlobalEventSlot(index * 10, event=slot)
             for index, slot in enumerate(slots)],
        )
    stimulus = api.Stimulus(queues={"in": []}, slots=slots, clock_schedule=schedule)
    assert not stimulus.faults_against(contract)
    initial = {"value": 5, **({"b_value": 3} if atomic else {})}
    artifact = RTLArtifact(
        rtl, "top", tmp_path / "replay", timeout_s=180,
    )
    return artifact, contract, stimulus, initial


@pytest.mark.parametrize("atomic", [False, True], ids=["single", "atomic"])
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("active_low", [False, True], ids=["reset-high", "reset-low"])
@pytest.mark.parametrize("edge", ["rising", "falling"])
def test_initial_state_precedes_the_first_reset(
        tmp_path, atomic, asynchronous, active_low, edge):
    artifact, contract, stimulus, initial = initial_fixture(
        tmp_path, atomic, asynchronous, active_low, edge)
    trace = artifact.exact_trace(
        contract, stimulus, observe_names=("value",),
        state_names=tuple(initial), state_widths={name: 3 for name in initial},
    )

    assert dict(trace.initial_state) == initial
    assert [dict(record.state) for record in trace.records] == [
        {name: 0 for name in initial}, {name: 1 for name in initial},
    ]

    # Inspection must not change the pins, transfers or hits used by plain replay.
    plain = artifact.trace(contract, stimulus)
    observed_rows = [record.as_dict() for record in trace.records]
    plain_rows = [record.as_dict() for record in plain.records]
    for row in observed_rows + plain_rows:
        row.pop("state", None)
    for observed, plain in zip(observed_rows, plain_rows):
        # Exact replay also carries internal pre-clock register readings. Compare
        # every original public observation, not just accepted transaction data.
        pins = dict(observed["outputs"])
        observed["outputs"] = [[name, pins[name]] for name, _value in plain["outputs"]]
    assert observed_rows == plain_rows


@pytest.mark.parametrize("atomic", [False, True], ids=["single", "atomic"])
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("active_low", [False, True], ids=["reset-high", "reset-low"])
@pytest.mark.parametrize("edge", ["rising", "falling"])
def test_analysis_choice_precedes_reset_and_does_not_force_later_state(
        tmp_path, atomic, asynchronous, active_low, edge):
    from behave.sim.rtl.initial_state import RTLInitialState

    artifact, contract, stimulus, _ = initial_fixture(
        tmp_path, atomic, asynchronous, active_low, edge, declared_initial=False)
    selected = {"value": 6, **({"b_value": 2} if atomic else {})}
    widths = {name: 3 for name in selected}
    choice = RTLInitialState.from_values(artifact.identity, widths, selected)
    trace = artifact.exact_trace(
        contract, stimulus, observe_names=("value",),
        state_names=tuple(selected), state_widths=widths, initial_state=choice)
    assert dict(trace.initial_state) == selected
    assert [dict(record.state) for record in trace.records] == [
        {name: 0 for name in selected}, {name: 1 for name in selected}]
    assert trace.stimulus is stimulus
    assert not stimulus.initial_state  # the choice is not a queue/schedule policy
    natural = artifact.exact_trace(
        contract, stimulus, observe_names=("value",),
        state_names=tuple(selected), state_widths=widths)
    assert dict(natural.initial_state) != selected


@pytest.mark.parametrize("width", [1, 8, 9, 16, 17, 32, 33, 64, 65, 129])
def test_selected_source_register_storage_widths(tmp_path, width):
    from behave.sim.rtl.initial_state import RTLInitialState

    artifact, contract, stimulus, _ = initial_fixture(
        tmp_path, False, False, False, "rising",
        declared_initial=False, width=width)
    values = {"value": (1 << (width - 1)) | 1}
    widths = {"value": width}
    trace = artifact.exact_trace(
        contract, stimulus, observe_names=("value",), state_names=("value",),
        state_widths=widths,
        initial_state=RTLInitialState.from_values(artifact.identity, widths, values))
    assert dict(trace.initial_state) == values
    assert [dict(row.state) for row in trace.records] == [{"value": 0}, {"value": 1}]


def test_initialization_cannot_write_a_combinational_mirror(tmp_path):
    from behave.sim.core.failure import FailureOrigin
    from behave.sim.rtl.initial_state import RTLInitialState

    artifact, contract, stimulus, _ = initial_fixture(
        tmp_path, False, False, False, "rising", declared_initial=False)
    choice = RTLInitialState.from_values(
        artifact.identity, {"out_valid": 1}, {"out_valid": 0})
    result, = artifact.traces_per_process(
        contract, [stimulus], state_names=("out_valid",),
        state_widths={"out_valid": 1}, _initial_state=choice)
    assert result.origin is FailureOrigin.VERIFIER
    assert not result.determines_score
    assert result.reason.kind == "rtl-initial-state-register-unavailable"


@pytest.mark.parametrize("observed", [False, True], ids=["plain", "native-observed"])
def test_selected_state_resolves_hierarchy_and_array_elements(tmp_path, observed):
    from behave.sim.rtl.initial_state import RTLInitialState

    artifact, contract, stimulus, _ = initial_fixture(
        tmp_path, False, False, False, "rising", declared_initial=False)
    Path(artifact.rtl_path).write_text("""
module cell_state(input logic clk, rst, output logic [2:0] q);
  always @(posedge clk) if (rst) q <= 0; else q <= q + 1'b1;
endmodule
module top(input logic clk, rst, in_valid, out_ready, input logic [2:0] data,
           output logic in_ready, out_valid, output logic [2:0] value);
  logic [2:0] memory [0:1];
  wire [2:0] child_value;
  cell_state child(clk, rst, child_value);
  assign in_ready = 1;
  assign out_valid = 1;
  assign value = child_value ^ memory[0] ^ memory[1];
  always @(posedge clk) begin
    if (rst) begin memory[0] <= 0; memory[1] <= 0; end
    else begin memory[0] <= memory[0] + 1'b1; memory[1] <= memory[1] + 1'b1; end
  end
endmodule
""")
    selected = {"child.q": 6, "memory[0]": 5, "memory[1]": 3}
    widths = dict.fromkeys(selected, 3)
    if observed:
        binding = artifact.bind_observation(contract)
        assert binding.native
    trace = artifact.exact_trace(
        contract, stimulus, observe_names=("value",),
        state_names=iter(selected), state_widths=widths,
        initial_state=RTLInitialState.from_values(artifact.identity, widths, selected))
    assert dict(trace.initial_state) == selected
    assert [dict(row.state) for row in trace.records] == [
        dict.fromkeys(selected, 0), dict.fromkeys(selected, 1)]


@pytest.mark.parametrize("atomic", [False, True], ids=["single", "atomic"])
def test_hdl_ports_may_shadow_cocotb_handle_attributes(tmp_path, atomic):
    import re

    artifact, contract, stimulus, initial = initial_fixture(
        tmp_path, atomic, False, False, "rising")
    names = {"clk": "_id", "rst": "_name", "data": "_value", "value": "_path"}

    def rename(value):
        if isinstance(value, dict):
            image = {names.get(key, key): rename(item) for key, item in value.items()}
            if isinstance(image.get("controls"), list):
                image["controls"].sort()
            return image
        if isinstance(value, list):
            return [rename(item) for item in value]
        return names.get(value, value) if isinstance(value, str) else value

    source = Path(artifact.rtl_path)
    source.write_text(re.sub(
        r"\b(?:clk|rst|data|value)\b", lambda m: names[m[0]], source.read_text()))
    contract = api.TaskContract.from_dict(rename(contract.as_dict()))
    stimulus = api.Stimulus.from_dict(rename(stimulus.as_dict()))
    initial = rename(initial)
    trace = artifact.exact_trace(
        contract, stimulus, observe_names=("_path",), state_names=tuple(initial),
        state_widths={name: 3 for name in initial})
    assert dict(trace.initial_state) == initial
    assert [dict(row.state) for row in trace.records] == [
        {name: 0 for name in initial}, {name: 1 for name in initial}]
