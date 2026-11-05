"""Source activations include changes that disappear before the consumer runs."""

import subprocess
from dataclasses import replace

import pytest

from behave.sim.rtl.source_scheduling import (
    PendingActivations,
    activation_guard,
    blocking_write_parts,
    blocking_write_watch,
)
from behave.sim.rtl.source_statement_events import RESUME_DECLARATION

from .test_rtl_combinational_events import _trace
from .test_rtl_source_locations import _collector, _occurrence_meanings
from .test_rtl_source_net_drivers import _prepare
from .test_rtl_source_plan import _run

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


@pytest.mark.parametrize("guards", [None, {("x", "p0"): "A", ("x", "p1"): "B"},
                                   {("x", "p0"): None, ("x", "p1"): "B"}])
def test_write_fanout_shares_only_the_effect_free_post_write_comparison(guards):
    declarations, before, after = blocking_write_parts(
        7, (("x", "p0"), ("x", "p1")), enabled=guards)
    assert "bit __behave_native_event_write_7_0_changed;" in declarations
    assert len(before) == 1
    assert len(after) == 3
    assert sum(statement.count("!==") for statement in after) == 1
    assert after[0].startswith("__behave_native_event_write_7_0_changed=")
    assert all("__behave_native_event_write_7_0_changed" in statement for statement in after[1:])
    if guards is not None and all(guards.values()):
        assert "((A) || (B)) &&" in after[0]
        assert "? (x )" in before[0]
    else:
        assert after[0] == "__behave_native_event_write_7_0_changed=((x ) !== __behave_native_event_write_7_0)"
    if guards is not None:
        assert "(B) &&" in after[2]


def _assert_entries(tmp_path, source, values, statements, *, reference_source=None,
                    reference_statements=None, unique_events=False, settle_startup=False,
                    expected_uninstrumented=None, enumerate_iterations=True):
    """Compare output values and instance-qualified entries with original HDL.

    The reference only prints at the requested original statements. It does not
    reuse the source planner, sensitivity analysis or notification generator.
    """
    plan, _, _ = _prepare(tmp_path, source, enumerate_iterations=enumerate_iterations)
    collector, _ = _collector(plan)
    reference = source if reference_source is None else reference_source
    for index, statement in enumerate(statements if reference_statements is None else reference_statements):
        assert reference.count(statement) == 1
        reference = reference.replace(statement,
            f'begin $display("ENTRY %m {index}"); {statement} end')
    samples = "\n".join(f'a={value}; #1; $display("SAMPLE %0d",q);' for value in values)
    if settle_startup:
        samples = '#1; $display("BEGIN"); ' + samples
    reference += ("\nmodule tb; logic [7:0] a=0; wire [31:0] q; M dut(a,q); "
                  f"initial begin {samples} $finish; end endmodule")
    (tmp_path / "reference.sv").write_text(reference)
    _run(tmp_path, "iverilog", "-g2012", "-s", "tb", "-o", "reference", "reference.sv")
    run = subprocess.run(["vvp", "reference"], cwd=tmp_path, capture_output=True,
                         text=True, check=True, timeout=10)
    expected, pending = [], set()
    for line in run.stdout.splitlines():
        if line == "BEGIN":
            pending = set()
        elif line.startswith("ENTRY "):
            _, scope, index = line.split()
            assert scope.startswith("tb.dut")
            pending.add(("M" + scope.removeprefix("tb.dut"), statements[int(index)]))
        elif line.startswith("SAMPLE "):
            expected.append((int(line.split()[1]), pending))
            pending = set()
    assert len(expected) == len(values), run.stdout
    driver = """
      const unsigned values[]={VALUES};
      for(unsigned i=0;i<sizeof(values)/sizeof(values[0]);i++) {
        const uint64_t interval=i+INTERVAL_OFFSET;
        if(behave_native_events_begin_v1(interval)) return 1;
        dut.a=values[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(interval,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """.replace("VALUES", ",".join(map(str, values))).replace("INTERVAL_OFFSET", str(int(settle_startup)))
    if settle_startup:
        driver = """
          if(behave_native_events_begin_v1(0)) return 1;
          dut.a=0; dut.eval();
          uint64_t startup_bits=0,startup_calls=0;
          if(behave_native_events_end_v1(0,&startup_bits,1,&startup_calls)) return 2;
        """ + driver
    assert collector.word_count == 1
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    # Compare unchanged HDL under the same timing/optimization switches.
    # --timing matters for incomplete explicit event lists even without probes.
    original = _trace(tmp_path / "original", source, flat, driver, native=True)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    if unique_events:
        # For fixtures with at most one execution of each lexical event per
        # sample, duplicate callbacks must not hide behind the hit bitmap.
        assert all(calls == bits.bit_count() for _, bits, calls in native), native
    expected_values = [row[0] for row in expected]
    assert [row[0] for row in native] == expected_values
    assert [row[0] for row in original] == (
        expected_values if expected_uninstrumented is None else expected_uninstrumented)
    meanings = _occurrence_meanings(source, plan)
    for sample, ((_, bits, _), (_, entries)) in enumerate(zip(native, expected, strict=True)):
        seen = {index for index in range(collector.bindings.event_count) if bits >> index & 1}
        hits = collector.bindings.decode(seen).occurrences
        actual = {(scope, text) for key in hits
                  for scope, schema, text, _ in (meanings[key],)
                  if schema == "location" and text in statements}
        assert actual == entries, {
            "sample": sample, "input": values[sample], "expected": entries,
            "actual": actual, "native": native,
        }
    return expected


@pytest.mark.parametrize("producer", [
    "x=a; x=0;",
    "x+=a; x-=a;",
    "{x,z}={a,a}; {x,z}=0;",
])
def test_primitive_writes_notify_all_subscribers(tmp_path, producer):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0,z=0; logic [7:0] left,right;
      always_comb begin {producer} end
      always_comb left=x;
      always_comb right=x;
      always_comb q=left+right;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,2,2,0,3], ("left=x;", "right=x;"))
    assert [bool(entries) for _, entries in rows] == [True,True,False,True,False,False,True]


def test_latched_clock_gate_keeps_native_edges_and_combinational_fanout(tmp_path):
    source = '''module M(input [7:0] a,output wire [31:0] q);
      wire clock_level=a[0], reset_n=a[1], gate_off=a[2];
      logic gate_enable=1;
      logic [3:0] state=0;
      always_latch begin
        if(!reset_n) gate_enable<=1;
        else if(!clock_level) gate_enable<=!gate_off;
      end
      wire gated_clock=clock_level && gate_enable;
      always_ff @(posedge gated_clock or negedge reset_n) begin
        if(!reset_n) state<=0;
        else if(a[3]) state<=a[7:4];
      end
      wire empty=~|state;
      wire ready=reset_n && gate_enable;
      assign q={26'b0,ready,empty,state};
    endmodule'''
    # Toggle gating only while low; changing the data at a held-high clock is
    # not a new edge. Re-enable, then check asynchronous reset with fanout.
    values = [0,2,0x3a,0x3b,0x5b,0x5a,0x5e,0x5f,0x9f,0x9e,0x9a,0x9b,0x99,0]
    _assert_entries(tmp_path, source, values,
                    ('gate_enable<=1;', 'gate_enable<=!gate_off;', 'state<=0;', 'state<=a[7:4];'),
                    settle_startup=True)


@pytest.mark.parametrize('assignment', ['=', '<='])
def test_explicit_value_wait_keeps_cancelled_changes_without_startup(tmp_path, assignment):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      logic [7:0] x=0;
      always @(a) begin x{assignment}a; x{assignment}0; end
      always @(x) q=x;
    endmodule'''
    rows = _assert_entries(tmp_path, source, [0,1,0,2,2,0,3], ('q=x;',),
                           settle_startup=True, unique_events=True)
    assert [bool(entries) for _, entries in rows] == [False,True,False,True,False,False,True]


@pytest.mark.parametrize("event", [
    "x", "(x,y,z)", "(x or y or z)", "(x,y or z)",
    "(x, /* π: preserve the event list */ y, z // close on the next line\n)",
    "(posedge x, negedge y or posedge z)",
])
def test_explicit_event_lists_preserve_original_outputs_and_entries(tmp_path, event):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      logic x=0,y=0,z=0;
      always @(a) begin x<=a[0]; y<=a[1]; z<=a[2]; end
      always @{event} q={{29'b0,z,y,x}};
    endmodule'''
    _assert_entries(tmp_path, source, [0,1,1,0,2,2,0,4,4,0,7,0],
                    ("q={29'b0,z,y,x};",), settle_startup=True, unique_events=True)


def test_explicit_wait_acknowledges_silent_nba_epochs_without_source_hits(tmp_path):
    source = '''module M(input [7:0] a,output logic [31:0] q=0);
      logic [7:0] x=0;
      always @(posedge a[0]) begin if(a[1]) x<=a; x<=0; end
      always @(x) q=x;
    endmodule'''
    values = [0,1,0,1,0,3,0,1,0,3,0,7]
    rows = _assert_entries(tmp_path, source, values, ('q=x;',),
                           settle_startup=True, unique_events=True)
    assert [bool(entries) for _, entries in rows] == [bool(a&1 and a&2) for a in values]


def test_wide_array_write_fanout_preserves_cancelled_source_activations(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      logic [159:0] x[0:1]; logic [31:0] left,right;
      initial begin x[0]=0; x[1]=0; end
      always_comb begin x[0]=a; x[0]=0; x[1]=a; x[1]=0; end
      always_comb left=x[0][31:0]+x[1][31:0];
      always_comb right=x[0][31:0]^x[1][31:0];
      always_comb q=left+right;
    endmodule"""
    # Both consumers read the whole array; no assumption about partial-array
    # sensitivity is needed. Reverting each element still schedules consumers.
    rows = _assert_entries(tmp_path, source, [0,1,0,2,2,0,3],
                           ("left=x[0][31:0]+x[1][31:0];", "right=x[0][31:0]^x[1][31:0];"))
    assert [len(entries) for _, entries in rows] == [2,2,0,2,0,0,2]


@pytest.mark.parametrize("step", ["i=i+1", "i++"])
@pytest.mark.parametrize("process", ["always_comb", "always @*"])
def test_loop_counter_self_writes_do_not_enqueue_the_running_process(tmp_path, step, process):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      integer i;
      logic [7:0] x=0;
      {process} begin
        x=0;
        for(i=0;i<2;{step}) x=x+a;
      end
      always_comb q=x;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,2,2,0,3], ("q=x;",))
    assert [value for value, _ in rows] == [0,2,0,4,4,0,6]


def test_partial_read_ranges_do_not_subscribe_to_unrelated_bits(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0; logic [3:0] low,high;
      always_comb begin x=a; x=0; end
      always_comb low=x[3:0];
      always_comb high=x[7:4];
      always_comb q=low+high;
    endmodule"""
    # Icarus widens implicit constant selects to the whole vector; its explicit
    # select events also go through net evaluation, losing cancelled pulses.
    # A test-owned split-vector model makes the two independent physical ranges
    # whole variables. No dependency or mutation code from Sim is reused. Both
    # real native builds still receive the original packed-vector source.
    reference = source.replace("logic [7:0] x=0;", "logic [3:0] ref_low=0,ref_high=0;")
    reference = reference.replace("x=a; x=0;",
        "ref_low=a[3:0]; ref_high=a[7:4]; ref_low=0; ref_high=0;")
    reference = reference.replace("low=x[3:0];", "low=ref_low;").replace("high=x[7:4];", "high=ref_high;")
    rows = _assert_entries(tmp_path, source, [0,1,0,16,16,0,17],
                           ("low=x[3:0];", "high=x[7:4];"), reference_source=reference,
                           reference_statements=("low=ref_low;", "high=ref_high;"))
    assert [len(entries) for _, entries in rows] == [2,1,0,1,0,0,2]


def test_repeated_instances_keep_their_own_pending_activations(tmp_path):
    source = """module leaf(input [3:0] a,output logic [3:0] q);
      logic [3:0] x=0;
      always_comb begin x=a; x=0; end
      always_comb q=x;
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      wire [3:0] lo,hi;
      leaf low(a[3:0],lo), high(a[7:4],hi);
      assign q=lo+hi;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,16,16,0,17], ("q=x;",))
    assert [len(entries) for _, entries in rows] == [2,1,0,1,0,0,2]


def test_specialized_write_ranges_preserve_cancelled_changes_and_parameter_scope(tmp_path):
    # Each consumer reads half its instance's vector. A union of the watch
    # ranges would spuriously activate the narrow consumer when only bit 2
    # changes. W in the producer is deliberately not the module parameter.
    producer = "x=a; x=0;"
    source = f"""module Cell #(parameter W=4)(input [7:0] a,output logic [15:0] q);
      logic [W-1:0] x=0,ignored;
      always_comb begin: producer int W=0; {producer} end
      always_comb q=x[W/2-1:0];
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      Cell #(.W(4)) narrow(a,q[15:0]);
      Cell #(.W(8)) wide(a,q[31:16]);
    endmodule"""
    # Same independent split-vector oracle as the non-parameterized range
    # test; the reference writes each half once, then cancels both writes.
    reference = source.replace("logic [W-1:0] x=0,ignored;",
        "logic [W/2-1:0] ref_low=0,ref_high=0; logic [W-1:0] ignored; "
        "localparam HALF=W/2;")
    reference = reference.replace(producer,
        "ref_low=a[HALF-1:0]; ref_high=a[2*HALF-1:HALF]; ref_low=0; ref_high=0;")
    reference = reference.replace("q=x[W/2-1:0];", "q=ref_low;")
    rows = _assert_entries(tmp_path, source, [0,1,0,4,4,0,5], ("q=x[W/2-1:0];",),
                           reference_source=reference, reference_statements=("q=ref_low;",))
    assert [value for value, _ in rows] == [0]*7
    assert [len(entries) for _, entries in rows] == [2,2,0,1,0,0,2]


def test_specialized_write_in_loop_headers_preserves_pending_activations(tmp_path):
    source = """module Cell #(parameter W=4)(input [7:0] a,output logic [15:0] q);
      integer x=4; logic [7:0] ignored;
      always @(posedge a[0]) begin: producer
        int W=0;
        for(x=0;x<4;x++) ignored=a;
      end
      always_comb q=x[W/2-1:0];
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      Cell #(.W(4)) narrow(a,q[15:0]);
      Cell #(.W(8)) wide(a,q[31:16]);
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,3,3,0,5], ("q=x[W/2-1:0];",))
    assert [value for value, _ in rows] == [4 << 16]*7
    # The counter returns to 4 after each edge, but both readers saw changes
    # during the header initialization/increments and must still be woken.
    assert [len(entries) for _, entries in rows] == [2,2,0,2,0,0,2]


@pytest.mark.parametrize("watched_scope,other_scope", [
    ("consumer", "consumer"), ("consumer", "other"), ("changed", "other")])
def test_specialized_write_with_no_consumer_does_not_notify_other_instance(tmp_path, watched_scope, other_scope):
    source = """module Cell #(parameter WATCH=1)(input [7:0] a,output logic [15:0] q);
      logic [7:0] x=0;
      always_comb begin x=a; x=0; end
      if(WATCH) begin: WATCHED_SCOPE always_comb q=x; end
      else begin: OTHER_SCOPE always_comb q=a; end
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      Cell #(.WATCH(1)) watched(a,q[15:0]);
      Cell #(.WATCH(0)) unwatched(a,q[31:16]);
    endmodule""".replace("WATCHED_SCOPE", watched_scope).replace("OTHER_SCOPE", other_scope)
    rows = _assert_entries(tmp_path, source, [0,1,0,2,2,0,3], ("q=x;", "q=a;"))
    assert [value for value, _ in rows] == [value << 16 for value in (0,1,0,2,2,0,3)]
    assert [len(entries) for _, entries in rows] == [2,2,1,2,0,1,2]


@pytest.mark.parametrize("named", [False, True])
def test_nested_conditional_subscriber_preserves_physical_instance_ownership(tmp_path, named):
    consumer = "begin: consumer" if named else "begin"
    other = "begin: other" if named else "begin"
    source = f"""module Cell(input [3:0] a,output wire [15:0] y);
      for(genvar WATCH=0;WATCH<2;WATCH++) begin: lanes
        logic [3:0] x=0,value;
        always_comb begin x=a; x=0; end
        if(WATCH) {consumer} always_comb value=x; end
        else {other} always_comb value=a; end
      end
      assign y={{8'b0,lanes[0].value,lanes[1].value}};
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      Cell low(a[3:0],q[15:0]);
      Cell high(a[7:4],q[31:16]);
    endmodule"""
    inputs = [0,1,0,16,16,0,17]
    rows = _assert_entries(tmp_path, source, inputs, ("value=x;", "value=a;"), unique_events=True)
    assert [value for value, _ in rows] == [((a & 15) << 4) | ((a >> 4) << 20) for a in inputs]
    # Identical parameter values in two Cell copies cannot select one absolute
    # first-instance target. Only the instance whose input changed is activated.
    assert [len(entries) for _, entries in rows] == [4,2,1,2,0,1,4]


def test_helper_writes_use_bound_storage_not_same_named_locals(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0,ignored;
      function automatic [7:0] tweak(input [7:0] x);
        x=x+1; return x;
      endfunction
      function automatic [7:0] pulse(input [7:0] v);
        x=tweak(v); x=0; return v;
      endfunction
      always_comb ignored=pulse(a);
      always_comb q=x;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,0,255,1,255,255,2], ("q=x;",))
    assert [bool(entries) for _, entries in rows] == [True,False,False,True,False,False,True]


@pytest.mark.parametrize("body,statement", [
    ("""logic [7:0] x=0;
        always_comb begin x=a; x=0; end
        if (1) begin: consumer always_comb q=x; end""", "q=x;"),
    ("""if (1) begin: producer
          logic [7:0] x=0;
          always_comb begin x=a; x=0; end
        end
        always_comb q=producer.x;""", "q=producer.x;"),
    ("""if (1) begin: storage logic [7:0] x=0; end
        always_comb begin storage.x=a; storage.x=0; end
        if (1) begin: consumer always_comb q=storage.x; end""", "q=storage.x;"),
    ("""if (1) begin: storage logic [7:0] x=0; end
        if (1) begin: writer
          always_comb begin storage.x=a; storage.x=0; end
        end
        if (1) begin: consumer always_comb q=storage.x; end""", "q=storage.x;"),
])
def test_cross_scope_write_notifications(tmp_path, body, statement):
    source = f"module M(input [7:0] a,output logic [31:0] q); {body} endmodule"
    rows = _assert_entries(tmp_path, source, [0,1,0,2,2,0,3], (statement,))
    assert [bool(entries) for _, entries in rows] == [True,True,False,True,False,False,True]


def test_cross_scope_notifications_keep_repeated_parent_instances_separate(tmp_path):
    source = """module producer(input [3:0] a);
      logic [3:0] x=0;
      always_comb begin x=a; x=0; end
    endmodule
    module cell_model(input [3:0] a,output logic [15:0] q);
      producer writer(a);
      always_comb q=writer.x;
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      cell_model low(a[3:0],q[15:0]), high(a[7:4],q[31:16]);
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,16,16,0,17], ("q=writer.x;",))
    assert [len(entries) for _, entries in rows] == [2,1,0,1,0,0,2]


@pytest.mark.parametrize("assignment", ["=", "<="])
def test_child_writer_notifies_parent_observer_without_shadowing(tmp_path, assignment):
    source = f"""module Leaf(input [3:0] a,output logic [15:0] q);
      if(1) begin: owner
        logic [3:0] x=0;
        if(1) begin: writer
          always @(posedge a[0]) begin x{assignment}a; x{assignment}0; end
        end
        always_comb q=x;
      end
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      Leaf low(a[3:0],q[15:0]);
      Leaf high(a[7:4],q[31:16]);
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,16,0,17], ("q=x;",))
    assert [value for value, _ in rows] == [0]*6
    assert [len(entries) for _, entries in rows] == [2,1,0,1,0,2]


@pytest.mark.parametrize("loop_name", ["", ": lanes"])
@pytest.mark.parametrize("selection", [
    ("if (i==0) begin always_comb q[15:0]=x; end "
     "else begin always_comb q[31:16]=y; end"),
    ("case (i) 0: begin always_comb q[15:0]=x; end "
     "default: begin always_comb q[31:16]=y; end endcase"),
    ("if (i==0) begin: selected always_comb q[15:0]=x; end "
     "else begin: selected always_comb q[31:16]=y; end"),
])
def test_unnamed_generate_subscribers_keep_independent_activations(tmp_path, loop_name, selection):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic [3:0] x=0,y=0;
      always_comb begin x=a[3:0]; y=a[7:4]; x=0; y=0; end
      for(genvar i=0;i<2;i++) begin {loop_name}
        {selection}
      end
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,16,16,0,17],
                           ("q[15:0]=x;", "q[31:16]=y;"))
    # Values always settle back to zero. Entries must still record cancelled
    # changes, while a change in one nibble must not activate the other scope.
    assert [value for value, _ in rows] == [0]*7
    assert [len(entries) for _, entries in rows] == [2,1,0,1,0,0,2]


@pytest.mark.parametrize("name", ["", ": selected"])
def test_generate_alternatives_keep_independent_nba_queues(tmp_path, name):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic [3:0] x=0,y=0;
      always @(posedge a[0]) begin x<=a[3:0]; y<=a[7:4]; x<=0; y<=0; end
      for(genvar i=0;i<2;i++) begin: lanes
        if (i==0) begin {name} always_comb q[15:0]=x; end
        else begin {name} always_comb q[31:16]=y; end
      end
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,17,0,0,1],
                           ("q[15:0]=x;", "q[31:16]=y;"))
    assert [len(entries) for _, entries in rows] == [2,1,0,2,0,0,1]


def test_unnamed_generate_subscribers_keep_repeated_instances_separate(tmp_path):
    source = """module leaf(input [3:0] a,output logic [15:0] q);
      logic [3:0] x=0;
      always_comb begin x=a; x=0; end
      if (1) begin always_comb q=x; end
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      leaf low(a[3:0],q[15:0]), high(a[7:4],q[31:16]);
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,16,16,0,17], ("q=x;",))
    assert [len(entries) for _, entries in rows] == [2,1,0,1,0,0,2]


def test_index_side_effect_is_not_repeated_by_write_notifications(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0; integer index;
      always_comb begin index=0; x[index++]=a[0]; x[0]=0; end
      always_comb q=(index<<8)|x;
    endmodule"""
    # The original process reads only a[0]. Project that input in the reference
    # instead of relying on Icarus's widened implicit bit-select sensitivity.
    reference = source.replace("input [7:0] a", "input a").replace("=a[0];", "=a;")
    rows = _assert_entries(tmp_path, source, [0,1,0,2,2,0,3], ("q=(index<<8)|x;",),
                           reference_source=reference)
    assert [value for value, _ in rows] == [256]*7


@pytest.mark.parametrize("step", ["i=i+1", "i++"])
@pytest.mark.parametrize("bound", [0, 4])
def test_for_header_writes_keep_pending_activations_even_when_counter_is_restored(tmp_path, step, bound):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      integer i=4; logic [7:0] sink;
      always @(posedge a[0]) for(i=0;i<{bound};{step}) sink=a;
      always_comb q=i;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,3,3,0,5], ("q=i;",))
    assert [value for value, _ in rows] == [4, *([bound] * 6)]
    expected = [True,True,False,True,False,False,True] if bound else [True,True,False,False,False,False,False]
    assert [bool(entries) for _, entries in rows] == expected


def test_dependency_change_is_not_only_a_final_value_comparison(tmp_path):
    source = """module M(input [3:0] a,output logic [3:0] q);
      logic [3:0] x=0;
      always_comb begin x=a; x=0; end
      always_comb q=x;
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    values = [0, 1, 0, 2, 2, 0, 3]
    # The reference marker does not read signals, and is not generated by our
    # planner. x can change and return to zero during one producer activation;
    # an already scheduled consumer still executes once after that producer.
    reference = source.replace("always_comb q=x;", 'always_comb begin $display("ENTRY"); q=x; end')
    reference += """
    module tb;
      logic [3:0] a=0; wire [3:0] q; M dut(a,q);
      initial begin
        #1; $display("SAMPLE %0d",q);
        a=1; #1; $display("SAMPLE %0d",q);
        a=0; #1; $display("SAMPLE %0d",q);
        a=2; #1; $display("SAMPLE %0d",q);
        a=2; #1; $display("SAMPLE %0d",q);
        a=0; #1; $display("SAMPLE %0d",q);
        a=3; #1; $display("SAMPLE %0d",q);
        $finish;
      end
    endmodule
    """
    (tmp_path / "reference.sv").write_text(reference)
    _run(tmp_path, "iverilog", "-g2012", "-s", "tb", "-o", "reference", "reference.sv")
    run = subprocess.run(["vvp", "reference"], cwd=tmp_path, capture_output=True,
                         text=True, check=True, timeout=10)
    expected, pending = [], 0
    for line in run.stdout.splitlines():
        if line == "ENTRY":
            pending += 1
        elif line.startswith("SAMPLE "):
            assert line == "SAMPLE 0"
            expected.append(bool(pending))
            pending = 0
    assert expected == [True, True, False, True, False, False, True], run.stdout
    driver = """
      const unsigned values[]={0,1,0,2,2,0,3};
      for(unsigned i=0;i<7;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert len(original) == len(native) == len(values)
    assert [row[0] for row in original] == [row[0] for row in native] == [0] * len(values)
    actual = []
    for _, bits, _ in native:
        seen = {index for index in range(collector.bindings.event_count) if bits >> index & 1}
        hits = collector.bindings.decode(seen).occurrences
        actual.append(any(key.path[1:3] == ("location", source.index("q=x;")) for key in hits))
    assert actual == expected


@pytest.mark.parametrize("fanout", [1, 3, 128])
def test_pending_write_kernel_keeps_cancelled_changes_until_consumed(tmp_path, fanout):
    source = """module M(input [3:0] a,output logic [3:0] q);
      logic [3:0] x=0;
      always_comb begin x=a; x=0; end
      always_comb q=x;
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    producer = activation_guard(0, ("a",))
    pending = PendingActivations.for_processes(range(1, fanout+1))
    consumers = [activation_guard(index+1, ("x",), pending=pending.reference(index+1)) for index in range(fanout)]
    watches = tuple(("x", consumer.pending) for consumer in consumers)
    first = blocking_write_watch(0, watches)
    second = blocking_write_watch(1, watches)
    # Exercise the shared generation primitive before whole-plan integration.
    # Use the generated source-dependency controls, without an initial/forever
    # coroutine. A cancelled write must notify every actual consumer once.
    outputs = ",".join(f"output logic [3:0] q{index},output logic [7:0] entries{index}=0"
                       for index in range(fanout))
    observed = (f"module M(input [3:0] a,{outputs}); "
                "logic [3:0] x=0; " + RESUME_DECLARATION
                + pending.declaration + producer.declarations
                + " ".join(c.declarations for c in consumers)
                    + " always_comb " + producer.before + first[0] + "x=a;" + first[1]
                + second[0] + "x=0;" + second[1] + producer.after
                    + " ".join(" always_comb " + consumer.before
                           + f"q{index}=x; entries{index}=entries{index}+1;" + consumer.after
                           for index, consumer in enumerate(consumers)) + " endmodule")
    columns = '<<" "<<'.join(f'unsigned(dut.q{index})<<" "<<unsigned(dut.entries{index})'
                              for index in range(fanout))
    driver = """
      const unsigned values[]={0,1,0,2,2,0,3};
      for(unsigned i=0;i<7;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<COLUMNS<<"\\n";
      }
    """.replace("COLUMNS", columns)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    rows = _trace(tmp_path / "kernel", observed, flat, driver, native=True)
    assert rows == [(0, count) * fanout for count in [1, 2, 2, 3, 3, 3, 4]]
