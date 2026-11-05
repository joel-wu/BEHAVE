"""NBA commits are source events even if the final stored value is restored."""

from dataclasses import replace
from copy import deepcopy
from itertools import product

import pytest

from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_observation_frontend import _installed
from .test_rtl_source_activation_scheduling import _assert_entries
from .test_rtl_source_locations import _collector, _occurrence_meanings
from .test_rtl_source_net_drivers import _prepare

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


@pytest.mark.parametrize('width,projection', ((65, 1), (128, 16), (252, 43)))
@pytest.mark.parametrize('restore', (False, True))
def test_narrow_projection_of_wide_nba_preserves_values_and_wakeups(tmp_path, width, projection, restore):
    tail = 'x<=0;' if restore else ''
    statement = f'q=x[{width-1} -: {projection}];'
    source = f'''module M(input [7:0] a,output logic [31:0] q);
      logic [{width-1}:0] x=0;
      always @(posedge a[0]) begin x<={{{width}{{a[1]}}}}; {tail} end
      always_comb {statement}
    endmodule'''
    _assert_entries(tmp_path, source, [0, 3, 3, 0, 1, 0, 3, 0, 3], (statement,))


@pytest.mark.parametrize('reverse_processes', (False, True))
def test_observer_identity_ignores_dependency_enumeration_order(tmp_path, reverse_processes):
    source = """module M(input [7:0] a,output logic [15:0] q,output wire [15:0] r);
      logic [7:0] x=0,y=0;
      always @(posedge a[0]) begin x<=a; y<=a+1; end
      always_comb q={x,y};
      assign r={y,x};
    endmodule"""
    (tmp_path / 'design.sv').write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, 'M', run=_installed)
    altered = deepcopy(sensitivity)
    if reverse_processes:
        altered.reverse()
    for row in altered:
        row['reads'].reverse()
    plans = [plan_source(source, cst, semantic_document=ast, sensitivity_document=rows,
                         enumerate_iterations=False) for rows in (sensitivity, altered)]
    left, right = plans
    assert left.declarations == right.declarations
    assert left.events == right.events
    assert left.native(scoped=True).text == right.native(scoped=True).text
    first, _ = _collector(left)
    second, _ = _collector(right)
    assert first.identity == second.identity


def test_same_projection_shares_history_but_not_consumer_acknowledgements(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q,r);
      logic [7:0] x=0;
      always @(posedge a[0]) begin x<=a; x<=0; end
      always_comb q=x;
      always_comb r=x;
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    native = plan.native(scoped=True).text
    assert native.count("bit [1:0] __behave_native_nba_0_commit=0;") == 1
    assert "function automatic void __behave_native_nba_" not in native
    rows = _assert_entries(tmp_path, source, [0,1,0,3,3,0,5], ("q=x;", "r=x;"))
    assert [len(entries) for _, entries in rows] == [2,2,0,2,0,0,2]


@pytest.mark.parametrize("bounds", ["0:3", "3:0"])
def test_array_fanout_packs_once_without_merging_subscribers(tmp_path, bounds):
    import re

    source = f"""module M(input [7:0] a,output wire [31:0] q);
      logic [7:0] x[{bounds}];
      initial for(int k=0;k<4;k++) x[k]=0;
      always @(posedge a[0]) x[a[2:1]]<=a;
      for(genvar k=0;k<4;k++) begin : g
        assign q[8*k +: 8]=x[k];
      end
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    native = plan.native(scoped=True).text
    assert len(re.findall(r"__behave_native_nba_write_\d+_pack_\d+_\d+_cursor=", native)) == 1
    calls = re.findall(r"<= __behave_native_history_update_\d+\(([^;]+)\);", native)
    assert len(calls) == 4  # All physical consumers still receive the event.
    assert len({tuple(call.split(", ")[:2]) for call in calls}) == 1
    assert len({tuple(call.split(", ")[2:]) for call in calls}) == 4
    collector, _ = _collector(plan)
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    driver = '''
      const unsigned values[]={0,1,0,3,0,5,0,7,7,0,129,0,67};
      for(unsigned i=0;i<13;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i];dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    assert collector.word_count == 1
    plain = _trace(tmp_path / "plain", source, flat, driver)
    observed = _trace(tmp_path / "observed", native, collector, driver, native=True)
    assert [row[0] for row in plain] == [row[0] for row in observed]


@pytest.mark.parametrize("tail", ["x[a[2:1]]<=0;", "x[a[2:1]]=0;"])
@pytest.mark.parametrize("width", [8, 97])
def test_shared_array_packing_keeps_restore_events_and_rebases(tmp_path, tail, width):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic [{width-1}:0] x[0:3];
      initial for(int k=0;k<4;k++) x[k]=0;
      always @(posedge a[0]) begin x[a[2:1]]<=a; {tail} end
      for(genvar k=0;k<4;k++) begin : g
        always_comb q[8*k +: 8]=x[k];
      end
    endmodule"""
    _assert_entries(tmp_path, source, [0,1,0,3,0,5,0,7,7,0,1],
                    ("q[8*k +: 8]=x[k];",))


def test_shared_history_remains_private_to_each_physical_instance(tmp_path):
    source = """module Leaf(input [3:0] a,output logic [7:0] left,right);
      logic [7:0] x=0;
      always @(posedge a[0]) begin x<=a; x<=0; end
      always_comb left=x;
      always_comb right=x;
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      Leaf low(a[3:0],q[7:0],q[15:8]);
      Leaf high(a[7:4],q[23:16],q[31:24]);
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,16,0,17,17],
                           ("left=x;", "right=x;"))
    assert [len(entries) for _, entries in rows] == [4,2,0,2,0,4,0]


@pytest.mark.parametrize("body,expected", [
    ("x<=a; x<=0;", [True, True, False, True, False, False, True]),
    ("if(a[1]) x<=a; x<=0;", [True, False, False, True, False, False, False]),
])
def test_deferred_restore_preserves_the_consumers_pending_activation(tmp_path, body, expected):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0;
      always @(posedge a[0]) begin {body} end
      always_comb q=x;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0, 1, 0, 3, 3, 0, 5], ("q=x;",))
    assert [value for value, _ in rows] == [0]*7
    assert [bool(entries) for _, entries in rows] == expected


@pytest.mark.parametrize("body", [
    "x<=a; x<=a;",
    "x<=a; x<=0;",
    "if(a[1]) x<=a; x<=0;",
    "x<=a; x=a;",
    "x<=0; x=a;",
])
def test_deferred_epochs_use_the_precommit_value_and_actual_source_entries(tmp_path, body):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0;
      always @(posedge a[0]) begin {body} end
      always_comb if(x==0) q=0; else q=1;
    endmodule"""
    # The consumer must depend only on x; sampling a would also wake it before
    # the NBA and obscure precisely the commit boundary tested here. Pure NBA
    # batches activate it at most once; mixed blocking/NBA batches may visit
    # both branches, which the independent original-source trace must retain.
    _assert_entries(tmp_path, source, [0,1,0,3,0,1,0,5,5], ("q=0;", "q=1;"),
                    unique_events="x=a;" not in body)


def test_deferred_partial_writes_keep_disjoint_read_ranges_separate(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0; logic [3:0] low,high;
      always @(posedge a[0]) begin x[3:0]<=a[3:0]; x[3:0]<=0; end
      always_comb low=x[3:0];
      always_comb high=x[7:4];
      always_comb q=low+high;
    endmodule"""
    # See the split-variable oracle in the blocking-write range regression:
    # Icarus's implicit sensitivity widens constant selects to their base.
    reference = source.replace("logic [7:0] x=0;", "logic [3:0] lo=0,hi=0;")
    reference = reference.replace("x[3:0]", "lo").replace("x[7:4]", "hi")
    rows = _assert_entries(tmp_path, source, [0,1,0,17,0,3],
        ("low=x[3:0];", "high=x[7:4];"), reference_source=reference,
        reference_statements=("low=lo;", "high=hi;"))
    assert [len(entries) for _, entries in rows] == [2,1,0,1,0,1]


@pytest.mark.parametrize("rhs", ["a[0]", "low_bit(a)"])
@pytest.mark.parametrize("index,clear", [("i++", "0"), ("(i++)+1", "1")])
def test_deferred_index_evaluation_is_not_repeated(tmp_path, rhs, index, clear):
    source = """module M(input [7:0] a,output wire [31:0] q);
      logic [7:0] x=0,y; integer i=0;
      function automatic bit low_bit(input [7:0] v); return v[0]; endfunction
      always @(posedge a[0]) begin i=0; x[INDEX]<=RHS; x[CLEAR]<=0; end
      always_comb y=x;
      assign q=(i<<8)|y;
    endmodule""".replace("RHS", rhs).replace("INDEX", index).replace("CLEAR", clear)
    rows = _assert_entries(tmp_path, source, [0,1,0,3,0,5], ("y=x;",))
    assert [value for value, _ in rows] == [0,256,256,256,256,256]
    assert [bool(entries) for _, entries in rows] == [True,True,False,True,False,True]


def test_deferred_array_cells_preserve_shape_and_dynamic_selection(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x[2];
      initial begin x[0]=0; x[1]=0; end
      always @(posedge a[0]) begin x[a[1]]<=a; x[a[1]]<=0; end
      always_comb q=x[0]+x[1];
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,3,3,0,5], ("q=x[0]+x[1];",))
    assert [value for value, _ in rows] == [0]*7
    assert [bool(entries) for _, entries in rows] == [True,True,False,True,False,False,True]


def test_deferred_selector_capture_accepts_closed_numeric_system_calls(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      logic [63:0] x[2]; int i;
      initial begin x[0]=0; x[1]=0; end
      function automatic [63:0] value(input real v);
        return $realtobits($sin(v));
      endfunction
      always @(posedge a[0]) begin
        i=a[1]; x[i+0]<=value($itor(a[3:2])); x[i+0]<=0;
      end
      always_comb q=x[0][31:0]|x[1][31:0];
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,5,0,9,0,7,0,11],
                           ("q=x[0][31:0]|x[1][31:0];",))
    assert [value for value, _ in rows] == [0] * 8
    assert [bool(entries) for _, entries in rows] == [True,True,False,True,False,True,False,True]


@pytest.mark.parametrize("bounds", [
    ((1, 0), (0, 1)),
    ((3, 2), (-1, 0)),
    ((1, 0), (4, 5), (2, 1)),
], ids=["matrix", "nonzero-bounds", "tensor"])
@pytest.mark.parametrize("leaf_width", [8, 20, 32])
def test_deferred_multidimensional_arrays_preserve_committed_events(tmp_path, bounds, leaf_width):
    shape = "".join(f"[{left}:{right}]" for left, right in bounds)
    cells = ["x" + "".join(f"[{i}]" for i in indices)
             for indices in product(*(range(min(pair), max(pair) + 1) for pair in bounds))]
    indices = "".join(f"wire signed [31:0] index_{axis}={min(pair)}+((a>>{axis+1})&1);"
                      for axis, pair in enumerate(bounds))
    target = "x" + "".join(f"[index_{axis}]" for axis in range(len(bounds)))
    output = "q=" + "+".join(cells) + ";"
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic signed [{leaf_width-1}:0] x{shape};
      {indices}
      initial begin {''.join(cell+'=0;' for cell in cells)} end
      always @(posedge a[0]) begin {target}<=a; {target}<=0; end
      always_comb {output}
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,3,3,0,5,0,7], (output,))
    assert [value for value, _ in rows] == [0] * 9
    assert [bool(entries) for _, entries in rows] == [True,True,False,True,False,False,True,False,True]


@pytest.mark.parametrize("selector", ["1+(a[2:1]&1)", "(a[2:1]&1)-2"])
def test_deferred_unsized_arithmetic_index_has_an_exact_carrier(tmp_path, selector):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic signed [7:0] x[-2:2];
      initial begin for(integer i=-2;i<=2;i++) x[i]=0; end
      always @(posedge a[0]) begin x[{selector}]<=a; x[{selector}]<=0; end
      always_comb q=x[-2]+x[-1]+x[0]+x[1]+x[2];
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,3,3,0,5],
                           ("q=x[-2]+x[-1]+x[0]+x[1]+x[2];",))
    assert [value for value, _ in rows] == [0] * 7
    assert [bool(entries) for _, entries in rows] == [True,True,False,True,False,False,True]


def test_array_packing_uses_one_shape_traversal_for_all_operands():
    from behave.sim.rtl.source_deferred_events import _pack_projection

    declarations, packing, operands = _pack_projection(("mask", "value", "before"), 3, "copy")
    assert len(operands) == 3
    text = " ".join((*declarations, *packing))
    assert text.count("for(longint unsigned") == 3
    assert text.count("copy_cursor-=") == 1
    for dimension in range(1, 4):
        assert f"$size(mask,{dimension})" in text
        assert f"$left(mask,{dimension})" in text
        assert f"$right(mask,{dimension})" in text
    assert all("logic [$bits(mask)-1:0] " + operand in text for operand in operands)
    assert _pack_projection(("mask", "value"), 0, "copy") == (
        ('logic [$bits(mask)-1:0] copy_0;', 'logic [$bits(mask)-1:0] copy_1;'),
        ('copy_0={mask};', 'copy_1={value};'), ('copy_0', 'copy_1'))


def test_deferred_cross_scope_writes_keep_instances_separate(tmp_path):
    source = """module leaf(input [3:0] a,output wire [15:0] q);
      if(1) begin: storage logic [3:0] x=0; end
      always @(posedge a[0]) begin storage.x<=a; storage.x<=0; end
      if(1) begin: consumer logic [3:0] y; always_comb y=storage.x; end
      assign q=consumer.y;
    endmodule
    module M(input [7:0] a,output wire [31:0] q);
      leaf lo(a[3:0],q[15:0]),hi(a[7:4],q[31:16]);
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,16,0,17], ("y=storage.x;",))
    assert [len(entries) for _, entries in rows] == [2,1,0,1,0,2]


def test_deferred_storage_identity_is_not_a_dotted_display_name(tmp_path):
    source = r"""module leaf(input [7:0] a);
      logic [7:0] y=0;
      always @(posedge a[0]) y<=0;
    endmodule
    module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] \x.y =1;
      leaf x(a);
      always @(posedge a[0]) \x.y <=1;
      always_comb q=\x.y + x.y;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,3,0,5], (r"q=\x.y + x.y;",))
    assert [value for value, _ in rows] == [1]*6
    assert [bool(entries) for _, entries in rows] == [True,False,False,False,False,False]


@pytest.mark.parametrize("producer", [
    "always @(posedge a[0]) begin {x,z}<={a,a}; {x,z}<=0; end",
    "always @(posedge a[0]) for(integer k=0;k<8;k++) begin x[k]<=a[k]; x[k]<=0; end",
    """function automatic bit selected_bit(input [7:0] value,input integer index);
           return value[index];
         endfunction
         always @(posedge a[0]) for(integer k=0;k<8;k++) begin
           x[k]<=selected_bit(a,k); x[k]<=0;
         end""",
    """task automatic pulse(input [7:0] value);
           x<=value; x<=0;
         endtask
         always @(posedge a[0]) pulse(a);""",
])
def test_deferred_helpers_loops_and_concatenations_share_commit_notifications(tmp_path, producer):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0,z=0;
      {producer}
      always_comb q=x;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,3,3,0,5], ("q=x;",))
    assert [bool(entries) for _, entries in rows] == [True,True,False,True,False,False,True]


@pytest.mark.parametrize("driver,location", [
    ("assign q=x;", "q=x"),
    ("wire [31:0] w=x; assign q=w;", "w=x"),
])
def test_deferred_commits_notify_continuous_rhs_without_redundant_reads(tmp_path, driver, location):
    source = f"""module M(input [7:0] a,output wire [31:0] q);
      logic [7:0] x=0;
      always @(posedge a[0]) begin x<=a; x<=0; end
      {driver}
    endmodule"""
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    executable = """
      const unsigned values[]={0,1,0,3,3,0,5};
      for(unsigned i=0;i<7;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i]; dut.eval(); dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    original = _trace(tmp_path / "original", source, flat, executable)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, executable, native=True)
    assert [value for value, _, _ in native] == [value for value, _, _ in original] == [0]*7
    meanings = _occurrence_meanings(source, plan)
    present = []
    for _, bits, _ in native:
        hits = collector.bindings.decode({bit for bit in range(collector.bindings.event_count)
                                          if bits >> bit & 1}).occurrences
        present.append(any(meanings[key][1:3] == ("location", location) for key in hits))
    assert present == [True,True,False,True,False,False,True]


def test_interdependent_index_and_rhs_effects_are_not_reordered(tmp_path):
    source = """module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0; integer i=0;
      always @(posedge a[0]) x[i++]<=i;
      always_comb q=x;
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    assert any("interdependent NBA index/RHS effects" in issue for issue in plan.native_issues)
    # The source catalogue still exists; a missing scheduling certificate does
    # not erase its goals or turn the event into a fake observation.
    assert plan.declarations.sites_for("location")


@pytest.mark.parametrize("rhs", ["a", "other(a)"])
def test_nba_selector_helper_may_read_nonlocal_state(tmp_path, rhs):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0;
      wire [1:0] row=a[2:1];
      function automatic int base(); int b=row; return b; endfunction
      function automatic int position(input int lane); return base()+lane; endfunction
      function automatic logic other(input logic v); return v; endfunction
      always @(posedge a[0]) x[position(0)]<={rhs};
      always_comb q=x;
    endmodule"""
    rows = _assert_entries(tmp_path, source, [0,1,0,3,2,5,4,7], ("q=x;",))
    assert [value for value, _ in rows] == [0,1,1,3,3,7,7,15]


@pytest.mark.parametrize("selector,rhs", [
    ("position(0)", "i++"),
    ("i++", "position(0)"),
])
def test_nba_lifting_checks_transitive_nonlocal_helper_reads(tmp_path, selector, rhs):
    source = f"""module M(input [7:0] a,output logic [31:0] q);
      logic [7:0] x=0; integer i=0;
      function automatic int base(); int b=i; return b; endfunction
      function automatic int position(input int lane); return base()+lane; endfunction
      always @(posedge a[0]) x[{selector}]<={rhs};
      always_comb q=x;
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    assert any("interdependent NBA index/RHS effects" in issue for issue in plan.native_issues)
