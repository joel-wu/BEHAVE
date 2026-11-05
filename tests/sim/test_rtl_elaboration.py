"""Production catalogue, observer and exact wires share concrete instances."""

import pytest

from behave.sim.rtl.elaboration import SOURCE_FILENAME, elaboration_script


def test_elaboration_prefix_is_closed_and_keeps_the_source_basename():
    assert elaboration_script("M") == "read_verilog -sv design.sv\nhierarchy -top M\nflatten\n"
    assert SOURCE_FILENAME == "design.sv"
    assert elaboration_script("M", scoped=True) == elaboration_script("M").replace(
        "flatten\n", "flatten -scopename\n")
    for bad in (None, True, "", "M; shell", "M\nread_verilog other.sv", "*"):
        with pytest.raises(ValueError):
            elaboration_script(bad)
    with pytest.raises(TypeError):
        elaboration_script("M", scoped=1)


def test_symbolic_wire_encoding_does_not_rename_concrete_handles():
    from behave.sim.rtl.sigspec import rtlil_name, symbolic_name

    raw = r"$flatten\left.$eq$design.sv:2$26_Y"
    assert rtlil_name(raw) == raw
    assert symbolic_name(raw) == "$flatten/left.$eq$design.sv:2$26_Y"
