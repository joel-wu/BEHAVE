"""Event-fidelity acceptance tests, including currently unresolved defects.

Output agreement alone is insufficient: the source event must be caused by a
change to that process's inputs, not merely by evaluating an upstream process.
"""

import subprocess

import pytest

from .test_rtl_combinational_events import _judge_traces
from .test_rtl_source_plan import _run


def _internal_expectations(registered):
    values, events = [], []
    for i in range(32):
        if registered:
            # Rising noise at each odd sample captures a. The first edge
            # writes zero over zero, so it cannot activate q's process.
            values.append(max(0, (i - 1) // 2) if i % 2 == 0 else i // 2)
            producer = i % 2 == 1
            consumer = i == 0 or (producer and i > 1)
        else:
            # a changes every two samples; a>>1 only every four samples.
            values.append(i // 4)
            producer = i % 2 == 0
            consumer = i % 4 == 0
        events.append([event for event, active in enumerate((producer, consumer)) if active])
    return values, events


@pytest.mark.eda("iverilog", "vvp")
@pytest.mark.parametrize("registered", [False, True], ids=["chain", "register"])
def test_independent_internal_event_oracle(tmp_path, registered):
    producer = ('reg [3:0] middle=0; always @(posedge noise) begin '
                '$display("EVENT 0"); middle<=a; end' if registered else
                'reg [3:0] middle; always_comb begin $display("EVENT 0"); middle=a>>1; end')
    source = '''module M(input [3:0] a,input noise,output reg [3:0] q);
      PRODUCER
      always_comb begin $display("EVENT 1"); q=middle; end
    endmodule
    module tb;
      reg [3:0] a=0; reg noise=0; wire [3:0] q; integer i;
      M dut(a,noise,q);
      initial begin
        for(i=0;i<32;i=i+1) begin
          #1; a=i/2; noise=i%2;
          #1; $display("SAMPLE %0d",q);
        end
        $finish;
      end
    endmodule
    '''.replace("PRODUCER", producer)
    (tmp_path / "oracle.sv").write_text(source)
    _run(tmp_path, "iverilog", "-g2012", "-s", "tb", "-o", "oracle", "oracle.sv")
    result = subprocess.run(["vvp", "oracle"], cwd=tmp_path, capture_output=True,
                            text=True, check=True, timeout=10)
    values, events, pending = [], [], set()
    for row in result.stdout.splitlines():
        if row.startswith("EVENT "):
            pending.add(int(row.split()[1]))
        elif row.startswith("SAMPLE "):
            values.append(int(row.split()[1]))
            events.append(sorted(pending))
            pending.clear()
    assert (values, events) == _internal_expectations(registered)


@pytest.mark.eda("slang", "yosys", "verilator", "make")
@pytest.mark.isolation
@pytest.mark.parametrize("body", [
    "always_comb q=a;",
    "always_comb begin q=a; assert(q==a); end",
    "Inner child(a,q);",
], ids=["assignment", "assertion", "hierarchy"])
def test_input_events_survive_assertions_and_hierarchy(tmp_path, body):
    source = ("module Inner(input [3:0] a,output reg [3:0] q); always_comb q=a; endmodule\n"
              "module M(input [3:0] a,input noise,output wire [3:0] q); "
              + body + " endmodule")
    if not body.startswith("Inner"):
        source = source.replace("output wire [3:0] q", "output reg [3:0] q")
    _prepared, traces = _judge_traces(tmp_path, source)
    plain, native = traces["plain"]["samples"], traces["native"]["samples"]
    assert [q for q, _, _ in native] == [q for q, _, _ in plain] == [i // 2 for i in range(32)]
    assert [bool(events) for _, events, _ in native] == [i % 2 == 0 for i in range(32)]
    assert all(not events and not calls for _, events, calls in traces["native"]["idle"])


@pytest.mark.eda("slang", "yosys", "verilator", "make")
@pytest.mark.isolation
@pytest.mark.parametrize("body", [
    "reg [3:0] middle; always_comb middle=a>>1; always_comb q=middle;",
    "reg [3:0] middle=0; always @(posedge noise) middle<=a; always_comb q=middle;",
], ids=["chain", "register"])
def test_internal_value_changes_not_merely_upstream_evaluation(tmp_path, body):
    source = ("module M(input [3:0] a,input noise,output reg [3:0] q); "
              + body + " endmodule")
    prepared, traces = _judge_traces(tmp_path, source)
    # Each process reports its activation event and then its statement entry,
    # so a process owns an ancestor event plus its own (producer first).
    processes = [sorted({*row.events, *(event for group in row.ancestors for event in group)})
                 for row in prepared.plan.bindings.projections]
    assert len(processes) == 2
    assert prepared.plan.bindings.event_count == sum(map(len, processes)) == 4
    plain, native = traces["plain"]["samples"], traces["native"]["samples"]
    registered = "posedge" in body
    expected_values, expected = _internal_expectations(registered)
    expected = [sorted(event for process in active for event in processes[process])
                for active in expected]
    assert [q for q, _, _ in native] == [q for q, _, _ in plain] == expected_values
    assert [events for _, events, _ in native] == expected, traces["native"]
    assert all(not events and not calls for _, events, calls in traces["native"]["idle"])
