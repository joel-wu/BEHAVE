"""Closed frontend state/clock ownership records for atomic RTL exact analysis."""

from __future__ import annotations

import copy

import pytest

from behave.sim import api
from behave.sim.rtl._yosys_state_ownership import state_ownership_document
from behave.sim.rtl.state_ownership import RTLStateOwnership
from behave.sim.rtl.tool_protocol import OutputNormalizer


def _contract(*, b_edge="falling"):
    return api.TaskContract(
        endpoints=(),
        clocks=(
            api.ClockDomain(
                "a", "a_clk", "a_rst", reset_restores=True, edge="rising"),
            api.ClockDomain(
                "b", "b_clk", "b_rst", reset_restores=True, edge=b_edge),
        ),
        source="state ownership parser test",
    )


@pytest.mark.cosim
@pytest.mark.parametrize("width", [3, 5, 9])
def test_ownership_preserves_original_storage_width_even_when_top_bit_is_unused(tmp_path, width):
    from behave.sim.rtl.state_ownership import read_state_ownership_with_execution

    source = f'''module top(input a_clk,a_rst,a,output q);
      logic [{width-1}:0] history;
      always @(posedge a_clk) begin
        if(a_rst) history<='0;
        else history<={{history[{width-2}:0],a}};
      end
      assign q=history[{width-2}];
    endmodule'''
    path = tmp_path / "design.sv"
    path.write_text(source)
    contract = api.TaskContract(endpoints=(), clocks=(
        api.ClockDomain("a", "a_clk", "a_rst", reset_restores=True),))
    ownership, _ = read_state_ownership_with_execution(path, "top", contract)
    assert dict(ownership.widths) == {"history": width}


@pytest.mark.cosim
@pytest.mark.parametrize("restoring", [True, False])
def test_native_reset_monitor_checks_state_without_assuming_restoration(tmp_path, restoring):
    from .test_reset_restoration_monitor import _contract

    clear = "if(rst || (in_valid && in_ready && !a)) y<=0; else " if restoring else ""
    source = f'''module M(input clk,rst,a,in_valid,out_ready,
      output in_ready,output reg out_valid,output reg y=0);
      assign in_ready=!rst && (!out_valid || out_ready);
      always @(posedge clk) begin
        if(rst) out_valid<=0;
        else if(in_ready) out_valid<=in_valid;
        {clear}if(!rst && in_valid && in_ready) y<=a;
      end
    endmodule'''
    contract = _contract()
    path = tmp_path / "design.sv"
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b'class M:\n def reset(self): pass\n def process(self,a): return {"y":a}\n'},
        config={"class": "M"})
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    slots = [api.EventSlot(reset=i in (0, 1, 5, 6), offers=("in",) if i==2 else (),
                          controls={"rst": int(i in (0, 1, 5, 6)), "out_ready": 1})
             for i in range(8)]
    stimulus = api.Stimulus({"in": [(1,)]}, slots)
    scenario = "monitor.reset-restoration.clk.scenario"
    violation = "monitor.reset-restoration.clk.violation"
    if not restoring:
        # Payload 0 leaves y equal to its reset value, so only payload 1 exposes it.
        seed = api.Stimulus({"in": [(0,)]}, slots)
        baseline = handle.replay((seed,), reference=reference).evaluation
        assert baseline.complete and baseline.passed, baseline.as_dict()
        assert violation not in baseline.hits
    evaluation = handle.replay((stimulus,), reference=reference).evaluation
    assert evaluation.complete, evaluation.as_dict()
    assert evaluation.passed is restoring, evaluation.as_dict()
    assert scenario in evaluation.hits
    assert (violation in evaluation.safety_hits) is (not restoring)
    owned = handle._artifact.state_ownership(contract)
    assert "y" in owned.names
    if not restoring:
        assert next(field for field in owned.fields if field.name == "y").reset is None


def _yosys_document():
    return {
        "creator": "pytest",
        "modules": {
            "top": {
                "ports": {
                    "a_clk": {"direction": "input", "bits": [1]},
                    "b_clk": {"direction": "input", "bits": [2]},
                    "a_rst": {"direction": "input", "bits": [4]},
                    "b_rst": {"direction": "input", "bits": [5]},
                    "a_q": {"direction": "output", "bits": [10, 11]},
                    "constant_status": {"direction": "output", "bits": ["0"]},
                },
                "cells": {
                    "$a": {
                        "type": "$adff",
                        "parameters": {
                            "CLK_POLARITY": "1", "ARST_POLARITY": "1"},
                        "connections": {
                            "CLK": [1], "ARST": [4],
                            "D": [20, 21], "Q": [10, 11]},
                    },
                    "$b": {
                        "type": "$sdffe",
                        "parameters": {
                            "CLK_POLARITY": "0", "SRST_POLARITY": "1",
                            "WIDTH": "10"},
                        "connections": {
                            "CLK": [2], "SRST": [5], "EN": [3],
                            "D": [22, 23], "Q": [12, 13]},
                    },
                },
                "netnames": {
                    "a_q": {"hide_name": 0, "bits": [10, 11], "attributes": {}},
                    "hidden_b_q": {
                        "hide_name": 0, "bits": [12, 13], "attributes": {}},
                    "$auto_alias": {
                        "hide_name": 1, "bits": [10, 11], "attributes": {}},
                    "constant_status": {
                        "hide_name": 0, "bits": ["0"], "attributes": {}},
                },
            },
        },
    }


def test_two_clock_state_is_owned_independently_of_source_observability():
    frontend = state_ownership_document(_yosys_document(), "top")
    ownership = RTLStateOwnership.from_frontend(
        frontend, _contract(), expected_top="top")

    assert frontend["issues"] == []
    assert ownership.names == ("a_q", "hidden_b_q")
    assert ownership.observable_names == ("a_q",)
    assert dict(ownership.by_domain()) == {
        "a": ("a_q",), "b": ("hidden_b_q",),
    }
    assert [field.edge for field in ownership.fields] == ["rising", "falling"]
    assert RTLStateOwnership.from_dict(ownership.as_dict()) == ownership
    assert RTLStateOwnership.from_dict(ownership.as_dict()).digest == ownership.digest


@pytest.mark.parametrize("empty", [("a",), ("b",), ("a", "b")])
def test_complete_empty_clock_partitions_are_not_missing_observation(empty):
    document = _yosys_document()
    for domain in empty:
        document["modules"]["top"]["cells"].pop("$" + domain)
    frontend = state_ownership_document(document, "top")
    assert frontend["issues"] == []
    contract = _contract()
    ownership = RTLStateOwnership.from_frontend(frontend, contract, expected_top="top")
    assert ownership.validate_contract(contract) is ownership
    partitions = ownership.by_domain(contract)
    assert set(partitions) == {"a", "b"}
    assert {domain for domain, fields in partitions.items() if not fields} == set(empty)
    assert RTLStateOwnership.from_dict(ownership.as_dict()).by_domain(contract) == partitions
    frontend["issues"] = [{"kind": "unsupported-state-cell", "cell": "hidden-latch"}]
    with pytest.raises(api.Refused, match="unsupported-state-cell"):
        RTLStateOwnership.from_frontend(frontend, contract, expected_top="top")


def test_generated_mixed_and_unsupported_state_fail_closed():
    generated = _yosys_document()
    generated["modules"]["top"]["cells"]["$a"]["connections"]["CLK"] = [99]
    got = state_ownership_document(generated, "top")
    assert "generated-clock" in {row["kind"] for row in got["issues"]}

    mixed = _yosys_document()
    mixed["modules"]["top"]["cells"]["$b"]["connections"]["Q"] = [11, 13]
    got = state_ownership_document(mixed, "top")
    assert "mixed-state-bit" in {row["kind"] for row in got["issues"]}

    unsupported = _yosys_document()
    unsupported["modules"]["top"]["cells"]["$a"]["type"] = "$dlatch"
    got = state_ownership_document(unsupported, "top")
    assert "unsupported-state-cell" in {
        row["kind"] for row in got["issues"]}

    generated_reset = _yosys_document()
    generated_reset["modules"]["top"]["cells"]["$a"]["connections"]["ARST"] = [
        "0"]
    got = state_ownership_document(generated_reset, "top")
    assert "generated-reset" in {row["kind"] for row in got["issues"]}

    duplicate = _yosys_document()
    duplicate["modules"]["top"]["cells"]["$duplicate"] = copy.deepcopy(
        duplicate["modules"]["top"]["cells"]["$a"])
    got = state_ownership_document(duplicate, "top")
    assert "duplicate-state-bit" in {row["kind"] for row in got["issues"]}


def test_data_dependent_synchronous_clear_preserves_clock_not_reset_guarantee():
    source = _yosys_document()
    source["modules"]["top"]["cells"]["$b"]["connections"]["SRST"] = [99]
    got = state_ownership_document(source, "top")
    assert not got["issues"]
    field = next(f for f in got["fields"] if f["name"] == "hidden_b_q")
    assert field["clock"] == "b_clk"
    assert field["reset"] is None and field["reset_active_low"] is None
    owned = RTLStateOwnership.from_frontend(got, _contract(), expected_top="top")
    assert owned.by_domain()["b"] == ("hidden_b_q",)
    assert RTLStateOwnership.from_dict(owned.as_dict()).validate_contract(_contract()) == owned


def test_reset_controls_with_non_restoring_semantics_are_parsed_then_refused():
    enable_over_reset = _yosys_document()
    cell = enable_over_reset["modules"]["top"]["cells"]["$b"]
    cell["type"] = "$sdffce"
    cell["parameters"]["EN_POLARITY"] = "1"
    got = state_ownership_document(enable_over_reset, "top")
    assert "reset-semantics-unsupported" in {
        row["kind"] for row in got["issues"]}

    set_clear = _yosys_document()
    cell = set_clear["modules"]["top"]["cells"]["$a"]
    cell["type"] = "$dffsr"
    cell["connections"].pop("ARST")
    cell["connections"].update({"SET": [30, 31], "CLR": [32, 33]})
    cell["parameters"].pop("ARST_POLARITY")
    cell["parameters"].update({"SET_POLARITY": "1", "CLR_POLARITY": "0"})
    got = state_ownership_document(set_clear, "top")
    assert "reset-semantics-unsupported" in {
        row["kind"] for row in got["issues"]}


def test_state_fields_are_a_disjoint_cover_and_reset_is_part_of_ownership():
    overlapping = _yosys_document()
    b_cell = overlapping["modules"]["top"]["cells"]["$b"]
    b_cell["type"] = "$adffe"
    b_cell["connections"].pop("SRST")
    b_cell["connections"]["ARST"] = [4]
    b_cell["connections"]["CLK"] = [1]
    b_cell["parameters"].pop("SRST_POLARITY")
    b_cell["parameters"]["ARST_POLARITY"] = "1"
    b_cell["parameters"]["CLK_POLARITY"] = "1"
    overlapping["modules"]["top"]["netnames"] = {
        "left": {"hide_name": 0, "bits": [10, 11], "attributes": {}},
        "right": {"hide_name": 0, "bits": [11, 12, 13], "attributes": {}},
    }
    got = state_ownership_document(overlapping, "top")
    assert "overlapping-state-nets" in {
        row["kind"] for row in got["issues"]}

    no_reset = _yosys_document()
    cell = no_reset["modules"]["top"]["cells"]["$a"]
    cell["type"] = "$dff"
    cell["parameters"].pop("ARST_POLARITY")
    cell["connections"].pop("ARST")
    document = state_ownership_document(no_reset, "top")
    owned = RTLStateOwnership.from_frontend(document, _contract(), expected_top="top")
    assert owned.fields[0].reset is None
    assert dict(owned.widths) == {"a_q": 2, "hidden_b_q": 2}

    only_b_claims = api.TaskContract(
        endpoints=(),
        clocks=(
            api.ClockDomain("a", "a_clk", "a_rst", edge="rising"),
            api.ClockDomain(
                "b", "b_clk", "b_rst", reset_restores=True,
                edge="falling"),
        ),
    )
    ownership = RTLStateOwnership.from_frontend(
        document, only_b_claims, expected_top="top")
    assert ownership.fields[0].reset is None
    assert ownership.validate_contract(only_b_claims) is ownership


@pytest.mark.parametrize("alias_bits", [[10, 12, 13], [10, 11, 12, 13]])
@pytest.mark.parametrize("same_clock", [False, True])
@pytest.mark.parametrize("reverse_bits", [False, True])
def test_complete_storage_carriers_precede_cross_register_aliases(
        alias_bits, same_clock, reverse_bits):
    document = _yosys_document()
    module = document["modules"]["top"]
    if same_clock:
        b = copy.deepcopy(module["cells"]["$a"])
        b["connections"].update(D=[22, 23], Q=[12, 13])
        module["cells"]["$b"] = b
    module["netnames"] = {
        "z_left": {"hide_name": 0, "bits": [11, 10] if reverse_bits else [10, 11]},
        "z_right": {"hide_name": 0, "bits": [12, 13]},
        "a_concat": {"hide_name": 0, "bits": alias_bits},
        "$hidden_duplicate": {"hide_name": 1, "bits": [10, 11]},
    }
    got = state_ownership_document(document, "top")
    assert got["issues"] == []
    assert [(row["name"], row["width"]) for row in got["fields"]] == [
        ("z_left", 2), ("z_right", 2)]
    assert {row["clock"] for row in got["fields"]} == (
        {"a_clk"} if same_clock else {"a_clk", "b_clk"})


def test_clock_edge_and_task_domain_binding_are_not_inferred_or_coerced():
    document = state_ownership_document(_yosys_document(), "top")
    with pytest.raises(api.Refused, match="edge-mismatch"):
        RTLStateOwnership.from_frontend(
            document, _contract(b_edge="rising"), expected_top="top")

    undeclared = copy.deepcopy(document)
    undeclared["fields"][0]["clock"] = "other_clk"
    with pytest.raises(api.Refused, match="undeclared-clock"):
        RTLStateOwnership.from_frontend(
            undeclared, _contract(), expected_top="top")

    wrong_reset = copy.deepcopy(document)
    wrong_reset["fields"][0]["reset"] = "b_rst"
    with pytest.raises(api.Refused, match="reset-mismatch"):
        RTLStateOwnership.from_frontend(
            wrong_reset, _contract(), expected_top="top")

    wrong_polarity = copy.deepcopy(document)
    wrong_polarity["fields"][0]["reset_active_low"] = True
    with pytest.raises(api.Refused, match="reset-mismatch"):
        RTLStateOwnership.from_frontend(
            wrong_polarity, _contract(), expected_top="top")


def test_ownership_wire_schema_rejects_extra_fields_and_coerced_values():
    document = state_ownership_document(_yosys_document(), "top")
    ownership = RTLStateOwnership.from_frontend(
        document, _contract(), expected_top="top")
    image = ownership.as_dict()
    image["extra"] = 1
    with pytest.raises(TypeError, match="exact wire schema"):
        RTLStateOwnership.from_dict(image)

    image = ownership.as_dict()
    image["fields"][0]["observable"] = 1
    with pytest.raises(TypeError, match="exact Boolean"):
        RTLStateOwnership.from_dict(image)

    OutputNormalizer(
        "ownership.json", "rtl-state-clock-ownership/1", (("top", "top"),))
    with pytest.raises(ValueError, match="one identifier named top"):
        OutputNormalizer(
            "ownership.json", "rtl-state-clock-ownership/1", (("top", "a.b"),))

