"""Explicit source waits retain their events, not the body-read sensitivity."""

import pytest

from .test_rtl_source_activation_scheduling import _assert_entries

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


@pytest.mark.parametrize("event,expression", [
    ("a", "a"), ("a[0], a[1]", "a[1:0]"), ("a[0] or a[1]", "a[1:0]"),
])
def test_explicit_change_control_observes_only_declared_events(tmp_path, event, expression):
    source = f"""module M(input [7:0] a,output logic [31:0] q=0);
      always @({event}) q={expression};
    endmodule"""
    _assert_entries(tmp_path, source, [0,1,5,5,6,2,0], (f"q={expression};",), settle_startup=True)


@pytest.mark.parametrize("event", ["a[0], a[1]", "a[0] or a[1]"])
def test_partial_bus_event_records_the_uninstrumented_backend_divergence(tmp_path, event):
    # The pinned backend widens the uninstrumented wait to the entire a bus.
    # Record that discrepancy explicitly: this is NOT a transparency pass.
    # Native values and statement entries must still match independent Icarus
    # source execution, including changes only to the omitted bits.
    source = f"""module M(input [7:0] a,output logic [31:0] q=0);
      always @({event}) q=a;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,5,5,6,2,0], ("q=a;",),
                           settle_startup=True, expected_uninstrumented=[0,1,5,5,6,2,0])
    assert [value for value, _ in rows] == [0,1,1,1,6,6,0]


def test_computed_reset_preserves_edges_and_native_entries(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q=0);
      wire reset_all=a[1] | a[2];
      always @(posedge a[0] or posedge reset_all)
        if(reset_all) q<=0; else q<=a>>3;
    endmodule"""
    _assert_entries(tmp_path, source, [0,24,25,25,26,24,41,44,40,41],
                    ("q<=0;", "q<=a>>3;"))


def test_state_generated_edge_records_actual_cascaded_activation(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q=0);
      logic gated=0;
      always @(posedge a[0]) gated<=a[1];
      always @(posedge gated) q<=a>>2;
    endmodule"""
    _assert_entries(tmp_path, source, [0,18,19,19,16,17,26,27,24],
                    ("gated<=a[1];", "q<=a>>2;"))
