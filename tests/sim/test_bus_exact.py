"""Bus query products preserve native transfers, all goals, and held data."""

import itertools

import pytest
import z3

from behave.sim import api
from behave.sim.core.adapter_exact import adapter_exact_product
from behave.sim.core.monitor import Monitor
from behave.sim.core.protocol import adapter_program_for
from behave.sim.rtl.task_symbolic import Z3Algebra
from tests.sim.test_apb_protocol import _contract as apb_contract
from tests.sim.test_apb_protocol import _realization
from tests.sim.test_axi_lite_protocol import _contract as axil_contract
from tests.sim.test_axi_lite_protocol import _full_contract
from tests.sim.test_interface_composition import _contract as mixed_contract
from tests.sim.test_protocol_adapters import needs_verilator


def _value(term):
    term = z3.simplify(term)
    return z3.is_true(term) if z3.is_bool(term) else term.as_long()


def _encode(product, state, reset, presented, scheduled, signals):
    return product.symbolic(
        z3.IntVal(product.state_index(state)), z3.BoolVal(reset),
        {n: z3.BoolVal(v is not None) for n, v in presented.items()},
        {n: z3.BoolVal(bool(scheduled[n])) for n in product.scheduled_names},
        {n: z3.BoolVal(bool(signals[n])) if w == 1 else z3.BitVecVal(signals[n], w)
         for n, w in product.observed_widths.items()}, Z3Algebra())


@pytest.mark.parametrize("kind", ("apb", "read", "write"))
@pytest.mark.parametrize("limit", (None, 0, 2))
def test_all_control_rows_match_registered_concrete_program(kind, limit):
    contract = apb_contract(wait_limit=limit) if kind == "apb" else axil_contract(kind == "write", wait_limit=limit)
    program = adapter_program_for(contract)
    product = adapter_exact_product(program, contract)
    payload = tuple((i + 1) for i, _ in enumerate(contract.inputs()[0].fields))
    heads = {"in": payload}
    # Exhaust the finite control carrier, including independently accepted AW/W,
    # saturated waits, reset, stalls, every RESP code, and empty presentations.
    widths = product.observed_widths
    for index in range(product.state_count):
        state = product.state_at(index, heads.__getitem__)
        for reset, present in itertools.product((False, True), repeat=2):
            if present and not program.presentation_allowed(contract, state, "in"):
                continue
            presented = {"in": payload if present and not reset else None}
            for scheduled_bits in itertools.product((0, 1), repeat=len(product.scheduled_names)):
                scheduled = dict(zip(product.scheduled_names, scheduled_bits), rst=int(reset))
                slot = api.EventSlot(reset=reset, controls=scheduled)
                drive = program.drive(contract, state, slot, presented, scheduled)
                for values in itertools.product(*(range(1 << w) for w in widths.values())):
                    signals = {f.port: 3 & ((1 << f.type.width) - 1) for f in contract.outputs()[0].fields}
                    signals.update(zip(widths, values))
                    encoded = _encode(product, state, reset, presented, scheduled, signals)
                    admissible = not program.environment_violations(contract, state, slot, drive)
                    assert _value(encoded.valid) is admissible
                    if not admissible:
                        continue
                    native = program.step(contract, state, slot, drive, signals)
                    assert _value(encoded.state) == product.state_index(native.state)
                    assert {g for g, hit in encoded.hits.items() if _value(hit)} == {v.goal for v in native.violations}
                    assert _value(encoded.accepted["in"]) is ("in" in native.accepted_inputs)
                    assert _value(encoded.emitted["out"]) is ("out" in native.emitted_outputs)
                    assert {n: int(_value(v)) for n, v in encoded.drive.items()} == dict(drive.controls)
                    assert product.state_at(_value(encoded.state), heads.__getitem__) == dict(native.state)


@pytest.mark.parametrize("builder", (apb_contract, axil_contract, lambda: axil_contract(False),
                                      _full_contract, mixed_contract))
def test_every_declared_bus_monitor_has_local_and_exact_bindings(builder):
    contract = builder()
    policy = api.evaluation_policy(api.admit(contract, _realization(contract)), api.CatalogueManifest(()))
    policy.validate(api.CONCRETE_PHASE)
    policy.validate(api.EXACT_ANALYSIS_PHASE)


@pytest.mark.parametrize("builder", (_full_contract, mixed_contract))
def test_same_edge_composition_keeps_independent_state_and_held_payload(builder):
    contract = builder()
    program = adapter_program_for(contract)
    product = adapter_exact_product(program, contract)
    state = program.initial_state(contract)
    heads = {e.name: tuple(3 for _ in e.fields) for e in contract.inputs()}
    for slot_index in range(7):
        reset = slot_index == 4
        scheduled = {n: int(reset) if n == "rst" else int(slot_index % 2 == 0)
                     for n in contract.scheduled_controls()}
        slot = api.EventSlot(reset=reset, controls=scheduled)
        presented = {n: v if not reset and program.presentation_allowed(contract, state, contract.endpoint(n)) else None
                     for n, v in heads.items()}
        signals = {f.port: 2 & ((1 << f.type.width) - 1) for e in contract.outputs() for f in e.fields}
        signals.update({n: int(slot_index % 3 == 0) for n in product.observed_names})
        drive = program.drive(contract, state, slot, presented, scheduled)
        native = program.step(contract, state, slot, drive, signals)
        encoded = _encode(product, state, reset, presented, scheduled, signals)
        assert _value(encoded.valid)
        assert product.state_at(_value(encoded.state), heads.__getitem__) == dict(native.state)
        assert {n for n, v in encoded.accepted.items() if _value(v)} == {
            n for n, v in native.accepted_inputs.items() if v is not None}
        assert {n for n, v in encoded.emitted.items() if _value(v)} == set(native.emitted_outputs)
        assert {g for g, v in encoded.hits.items() if _value(v)} == {v.goal for v in native.violations}
        state = native.state


def test_wait_allowance_does_not_expand_the_formula_and_resp_is_not_abstracted():
    contract = axil_contract(wait_limit=1_000_000)
    product = adapter_exact_product(adapter_program_for(contract), contract)
    state, response = z3.Int("bus_state"), z3.BitVec("resp", 2)
    encoded = product.symbolic(state, z3.BoolVal(False), {"in": z3.BoolVal(False)},
        {"bready": z3.BoolVal(True)}, {"awready": z3.BoolVal(False), "wready": z3.BoolVal(False),
        "bvalid": z3.BoolVal(True), "bresp": response}, Z3Algebra())
    assert len(encoded.state.sexpr()) < 8000
    for code in range(4):
        solver = z3.Solver()
        solver.add(response == code, encoded.hits["monitor.axil.response-code"])
        assert solver.check() == (z3.sat if code == 1 else z3.unsat)


@pytest.mark.cosim
@needs_verilator
@pytest.mark.parametrize("kind", ("apb", "read", "write", "mixed", "full"))
@pytest.mark.parametrize("helper", (False, True), ids=("expression", "early-return"))
def test_native_bus_query_finds_new_input_and_replays_same_goal(kind, helper, tmp_path):
    import json

    from behave.sim.engines import probing
    from behave.sim.engines.rtl.generation import RTLGenerationProvider
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.exact_product import global_schedule_for_stimuli, stimulus_for_global_schedule
    from behave.sim.search_policy import SearchBudget, SearchPolicy, search_scope

    if kind in ("apb", "mixed"):
        contract = mixed_contract() if kind == "mixed" else apb_contract()
        stream_ports = (", input [7:0] data, input data_valid, output data_ready, "
                        "output [7:0] result, output result_valid, input result_ready" if kind == "mixed" else "")
        stream_body = ("assign data_ready = result_ready; assign result_valid = data_valid; "
                       "assign result = data;" if kind == "mixed" else "")
        source = f"""module dut(input clk, rst, z_sel, a_enable, input [7:0] paddr,
output pready, output [7:0] prdata, output pslverr {stream_ports});
reg [1:0] waits;
always @(posedge clk) begin
  if (rst || !a_enable) waits <= 0;
  else waits <= waits + 1;
end
assign pready = paddr != 8'd37 || waits == 3;
assign prdata = paddr * 2;
assign pslverr = paddr >= 128;
{stream_body}
endmodule
"""
        goal = "monitor.apb.wait-limit"
        queues = {"in": [(7,)], **({"data": [(11,)]} if kind == "mixed" else {})}
        target_endpoint = "in"
        receptive = {"result_ready": 1} if kind == "mixed" else {}
    else:
        write = kind in ("write", "full")
        contract = _full_contract() if kind == "full" else axil_contract(write)
        request_ports = ("input awvalid, wvalid, input [31:0] awaddr, wdata, input [3:0] wstrb, "
                         "output awready, wready" if write else
                         "input arvalid, input [31:0] araddr, output arready")
        channel = "b" if write else "r"
        accept = "awvalid && wvalid" if write else "arvalid"
        addr = "awaddr" if write else "araddr"
        readies = "assign awready = 1; assign wready = 1;" if write else "assign arready = 1;"
        response_data_port = "" if write else ", output [31:0] rdata"
        response_data = "" if write else "assign rdata = held * 3;"
        if kind == "full":
            response_data_port = (", input arvalid, input [31:0] araddr, output arready, "
                                  "input rready, output rvalid, output [31:0] rdata, output [1:0] rresp")
            response_data = """reg read_pending;
reg [31:0] read_held;
always @(posedge clk) begin
  if (rst) begin read_pending <= 0; read_held <= 0; end
  else if (arvalid) begin read_pending <= 1; read_held <= araddr; end
  else if (rready) read_pending <= 0;
end
assign arready = 1; assign rvalid = read_pending;
assign rdata = read_held; assign rresp = 0;
"""
        source = f"""module dut(input clk, rst, {request_ports}, input {channel}ready,
output {channel}valid, output [1:0] {channel}resp {response_data_port});
reg pending;
reg [31:0] held;
always @(posedge clk) begin
  if (rst) begin pending <= 0; held <= 0; end
  else begin
    if ({accept}) begin pending <= 1; held <= {addr}; end
    else if ({channel}ready) pending <= 0;
  end
end
{readies}
assign {channel}valid = pending;
assign {channel}resp = held == 32'd37 ? 2'd1 : 2'd0;
{response_data}
endmodule
"""
        goal = "monitor.axil.response-code"
        target_endpoint = "write" if kind == "full" else "in"
        queues = {target_endpoint: [(7, 11, 15) if write else (7,)],
                  **({"read": [(11,)]} if kind == "full" else {})}
        receptive = {channel + "ready": 1, **({"rready": 1} if kind == "full" else {})}
    if helper:
        if kind in ("apb", "mixed"):
            source = source.replace("assign pready = paddr != 8'd37 || waits == 3;", """
function automatic can_reply(input [7:0] address, input [1:0] delay_count);
  if(address != 8'd37) return 1'b1;
  return delay_count == 3;
endfunction
assign pready = can_reply(paddr, waits);
""")
        else:
            source = source.replace(f"assign {channel}resp = held == 32'd37 ? 2'd1 : 2'd0;", f"""
function automatic [1:0] response(input [31:0] address);
  if(address == 32'd37) return 2'd1;
  return 2'd0;
endfunction
assign {channel}resp = response(held);
""")
    rtl = tmp_path / "dut.sv"
    rtl.write_text(source)
    artifact = RTLArtifact(rtl, "dut", tmp_path / "sim")
    original = api.Stimulus(queues, [api.EventSlot(
        reset=i == 0, offers=tuple(queues) if i == 1 else (), controls={"rst": int(i == 0), **receptive})
        for i in range(7)])
    schedule = global_schedule_for_stimuli(contract, (original,))
    baseline = stimulus_for_global_schedule(schedule, dict(original.queues), source="bus-test")
    monitor_goals = tuple(sorted(Monitor(contract).goals()))
    provider = RTLGenerationProvider.from_artifact(
        artifact, contract, api.GenerationSpec(7, 1, 1, 5, stall_every=0),
        artifact.frontend_catalogue(), workdir=tmp_path / "query",
        targetable_goals=monitor_goals, clock_schedule=schedule)
    selection = provider.select(baseline, targets=(goal,))
    assert isinstance(selection, api.ProbeSelection), selection.as_dict()
    assert goal not in selection.replay.hits
    assert selection.requests
    attempts = []
    goal_queries = {}
    lease = SearchBudget(SearchPolicy(time_budget_s=180)).direction(1)
    with search_scope(lease), provider.search_scope(
            probe_budget=len(selection.requests) * len(monitor_goals)):
        for request in selection.requests:
            answer = provider.probe(baseline, request)
            attempts.append(answer.outcome.value)
            if answer.outcome is probing.Outcome.SAT:
                break
        goal_queries[goal] = attempts
        for other_goal in monitor_goals:
            if other_goal == goal:
                continue
            selected = provider.select(baseline, targets=(other_goal,))
            assert isinstance(selected, api.ProbeSelection), selected.as_dict()
            assert selected.requests, other_goal
            outcomes = []
            for request in selected.requests:
                result = provider.probe(baseline, request)
                assert result.outcome in (probing.Outcome.SAT, probing.Outcome.LOCAL_UNSAT), result.detail
                outcomes.append(result.outcome.value)
                if result.outcome is probing.Outcome.SAT:
                    replay = provider.select(result.candidate, targets=())
                    assert isinstance(replay, api.ProbeSelection), replay.as_dict()
                    assert other_goal in replay.replay.hits
                    break
            goal_queries[other_goal] = outcomes
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert dict(answer.candidate.queues)[target_endpoint][0][0] == 37
    confirmed = provider.select(answer.candidate, targets=())
    assert isinstance(confirmed, api.ProbeSelection), confirmed.as_dict()
    assert goal in confirmed.replay.hits
    assert confirmed.catalogue_digest == selection.catalogue_digest
    (tmp_path / "bus-query.json").write_text(json.dumps({
        "protocol": kind, "baseline": selection.as_dict(),
        "query": {"outcome": answer.outcome.value, "scope": answer.scope.value,
                  "goal": answer.goal, "solve_ms": answer.solve_ms},
        "attempts": attempts, "goal_queries": goal_queries,
        "candidate": answer.candidate.as_dict(), "replayed": confirmed.as_dict(),
    }, indent=2))

