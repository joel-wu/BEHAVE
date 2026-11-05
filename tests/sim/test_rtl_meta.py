import pytest

from behave.sim.core.contract import ClockDomain
from behave.sim.rtl_meta import RTLMeta


def _shape(meta):
    return {
        "module": meta.mod,
        "inputs": meta.inputs,
        "outputs": meta.outputs,
        "data_inputs": meta.data_inputs,
        "reset": meta.rst_name,
        "domains": [
            {
                "name": domain.name,
                "clock": domain.clk,
                "reset": domain.rst,
                "active_low": domain.rst_active_low,
                "inputs": domain.data_inputs,
                "outputs": domain.data_outputs,
            }
            for domain in meta.domains
        ],
    }


def test_normalized_ports_and_solver_header_have_one_metadata_semantics(tmp_path):
    inputs = {
        "w_clk": 1,
        "w_rstn": 1,
        "w_in_valid": 1,
        "w_in_data": 13,
        "r_clk": 1,
        "r_rstn": 1,
        "r_out_ready": 1,
    }
    outputs = {
        "w_in_ready": 1,
        "r_out_valid": 1,
        "r_out_data": 9,
    }
    smt2 = tmp_path / "model.smt2"
    rows = ["; yosys-smt2-module fifo"]
    rows.extend(f"; yosys-smt2-input {name} {width}"
                for name, width in inputs.items())
    rows.extend(f"; yosys-smt2-output {name} {width}"
                for name, width in outputs.items())
    smt2.write_text("\n".join(rows) + "\n", encoding="utf-8")

    normalized = RTLMeta.from_ports("fifo", inputs=inputs, outputs=outputs)
    solver_side = RTLMeta(smt2)

    assert _shape(normalized) == _shape(solver_side)
    assert normalized.smt2_path is None
    assert solver_side.smt2_path == str(smt2)


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"module": "", "inputs": {}, "outputs": {}}, "nonempty"),
        ({"module": "m", "inputs": {"a": True}, "outputs": {}},
         "invalid width"),
        ({"module": "m", "inputs": {"a": 1}, "outputs": {"a": 1}},
         "both inputs and outputs"),
    ],
)
def test_normalized_port_metadata_rejects_ambiguous_or_untyped_records(
    kwargs, message,
):
    with pytest.raises(ValueError, match=message):
        RTLMeta.from_ports(**kwargs)


def test_declared_clocks_do_not_inherit_reset_polarity_from_spelling():
    meta = RTLMeta.from_ports("m", inputs={
        "a_clk": 1, "a_rst": 1, "strobe": 1, "clear": 1}, outputs={})
    before = _shape(meta)
    clocks = (
        ClockDomain("first", "a_clk", "a_rst", reset_active_low=True),
        ClockDomain("second", "strobe", "clear", edge="falling"))

    domains = meta.declared_clock_domains(clocks)

    assert [(domain.name, domain.clk, domain.rst, domain.rst_active_low)
            for domain in domains] == [
                ("first", "a_clk", "a_rst", True), ("second", "strobe", "clear", False)]
    assert _shape(meta) == before  # Standalone legacy metadata is not reinterpreted.


@pytest.mark.parametrize("clocks", [
    (), ("clock",),
    (ClockDomain("a", "strobe", "clear"), ClockDomain("a", "other", "clear")),
    (ClockDomain("a", "strobe", "clear"), ClockDomain("b", "strobe", "clear")),
    (ClockDomain("a", "missing", "clear"),),
    (ClockDomain("a", "wide", "clear"),),
    (ClockDomain("a", "strobe", "wide"),),
])
def test_declared_clocks_require_unique_names_and_real_one_bit_ports(clocks):
    meta = RTLMeta.from_ports("m", inputs={
        "strobe": 1, "other": 1, "clear": 1, "wide": 8}, outputs={})
    with pytest.raises((TypeError, ValueError)):
        meta.declared_clock_domains(clocks)
