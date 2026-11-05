"""Native implicit task calls retain compiler dependencies and source storage.

The untouched and instrumented Verilator executables run the same inputs. An
independent event simulator uses fixture-declared explicit event controls: its
implicit task-body convention need not match the selected execution backend.
No dependency analysis from Sim is reused to create those reference controls.
"""

import pytest

from .test_rtl_source_activation_scheduling import _assert_entries
from .test_rtl_source_net_drivers import _prepare

pytestmark = pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")


@pytest.mark.parametrize("process", ["always_comb", "always_latch"])
@pytest.mark.parametrize("lifetime", ["", "automatic "])
def test_nested_tasks_bind_hidden_reads_and_exclude_their_own_writes(tmp_path, process, lifetime):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      logic [7:0] temporary;
      task {lifetime}inner;
        temporary=a+3;
      endtask
      task {lifetime}outer;
        inner();
        q=temporary^8'h5;
      endtask
      {process} outer();
    endmodule'''
    plan, _, dependencies = _prepare(tmp_path, source)
    row, = dependencies
    assert row["inlined_task_dependencies"] is True
    assert {read["symbol"] for read in row["reads"]} == {"M.a"}
    native = plan.native().text
    assert native.count(f"task {lifetime}inner") == 1
    assert native.count(f"task {lifetime}outer") == 1
    reference = source.replace(f"{process} outer();", "always @(a) outer();")
    rows = _assert_entries(tmp_path, source, [1,1,4,5,7,0], ("q=temporary^8'h5;",),
        reference_source=reference, reference_statements=("outer();",), settle_startup=True)
    assert [value for value, _ in rows] == [1,1,2,13,15,6]
    assert [bool(entries) for _, entries in rows] == [True,False,True,True,True,True]


@pytest.mark.parametrize("lifetime", ["", "automatic "])
def test_task_partial_read_does_not_wake_for_unrelated_bits(tmp_path, lifetime):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      task {lifetime}put;
        q=a[0] ? 17 : 23;
      endtask
      always_comb put();
    endmodule'''
    # The selected backend evaluates the body at startup; this explicit-event
    # oracle starts with the fixture's known a=0 result before measuring hits.
    reference = source.replace("always_comb put();", "initial q=23; always @(a[0]) put();")
    rows = _assert_entries(tmp_path, source, [0,2,3,7,6,6,4,5], ("q=a[0] ? 17 : 23;",),
        reference_source=reference,
        reference_statements=("put();",), settle_startup=True)
    assert [bool(entries) for _, entries in rows] == [False,False,True,False,True,False,False,True]


def test_static_task_with_entry_state_is_not_silently_made_combinational(tmp_path):
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_plan import plan_source

    from .test_rtl_observation_frontend import _installed

    source = '''module M(input [7:0] a,output logic [31:0] q=0);
      task put(input [7:0] value);
        reg [31:0] count=0;
        count=count+value;
        q=count;
      endtask
      always_comb put(a);
    endmodule'''
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), rows = parse_observation_source(tmp_path, "M", run=_installed)
    assert rows[0]["task_frame_inputs"] == ["M.put.count"]
    assert rows[0]["inlined_task_dependencies"] is False
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=rows)
    with pytest.raises(ValueError, match="retained helper state"):
        plan.native()


@pytest.mark.parametrize("storage,body,statement", [
    ("reg [7:0] temporary;", "temporary=a; q=temporary;", "q=temporary;"),
    ("reg [7:0] temporary;", "temporary[0]=a[0]; q=temporary[0];", "q=temporary[0];"),
    ("reg [7:0] words[0:3]; integer i;",
     "for(i=0;i<4;i=i+1) words[i]=a+i; q=words[0]+words[3];", "q=words[0]+words[3];"),
])
def test_static_task_temporaries_are_not_blanket_rejected(tmp_path, storage, body, statement):
    source = f'''module M(input [7:0] a,output logic [31:0] q=0);
      task put;
        {storage}
        {body}
      endtask
      always_comb put();
    endmodule'''
    _, _, dependencies = _prepare(tmp_path, source)
    assert dependencies[0]["inlined_task_dependencies"] is True
    assert dependencies[0]["task_frame_inputs"] == []
    event = "a[0]" if "temporary[0]" in body else "a"
    reference = source.replace("always_comb put();", f"always @({event}) put();")
    _assert_entries(tmp_path, source, [1,1,2,3,0,255], (statement,),
        reference_source=reference, reference_statements=("put();",), settle_startup=True)


def test_task_write_restoration_still_notifies_downstream_process(tmp_path):
    source = '''module M(input [7:0] a,output logic [31:0] q=0);
      logic [7:0] x=0;
      task automatic pulse;
        x=a;
        x=0;
      endtask
      always_comb pulse();
      always_comb q=x;
    endmodule'''
    reference = source.replace("always_comb pulse();", "always @(a) pulse();")
    rows = _assert_entries(tmp_path, source, [0,1,0,2,2,0,3], ("q=x;",),
        reference_source=reference, settle_startup=True)
    assert [bool(entries) for _, entries in rows] == [False,True,False,True,False,False,True]


@pytest.mark.parametrize("declarations,body,expected", [
    ("logic [31:0] count=0; task put; count=count+a; q=count; endtask",
     "put();", "M.count"),
    ("task put(output reg [31:0] value); if(a[0]) return; value=a; endtask",
     "put(q);", "M.put.value"),
    ("task put(output reg [31:0] value); value[0]=a[0]; endtask",
     "put(q);", "M.put.value"),
    (("task set_value(output reg [7:0] dest,input reg [7:0] src); dest=src; endtask "
      "task put; reg [7:0] saved; set_value(saved,saved); q=saved; endtask"),
     "put();", "M.put.saved"),
    ("task put; reg [7:0] words[0:3]; words[a[1:0]]=a; q=words[0]; endtask",
     "put();", "M.put.words"),
    (("function [31:0] value; reg [31:0] saved; if(a[0]) saved=a; value=saved; endfunction "
      "task put; q=value(); endtask"), "put();", "M.value.saved"),
    (("integer i; task bump; i=i+1; endtask "
      "task put; reg [7:0] words[0:3]; "
      "for(i=0;i<4;i++) begin words[i]=a; bump(); end q=words[1]; endtask"),
     "put();", "M.put.words"),
])
def test_task_schedule_rejects_unproven_entry_state(tmp_path, declarations, body, expected):
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_plan import plan_source

    from .test_rtl_observation_frontend import _installed

    source = f"module M(input [7:0] a, output logic [31:0] q=0); {declarations} always_comb begin {body} end endmodule"
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), rows = parse_observation_source(tmp_path, "M", run=_installed)
    assert expected in rows[0]["task_frame_inputs"]
    assert rows[0]["inlined_task_dependencies"] is False
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=rows)
    with pytest.raises(ValueError, match="retained helper state"):
        plan.native()


@pytest.mark.parametrize("declarations,body,statement,expected", [
    ("logic [7:0] temporary; task put; temporary=temporary+1; q=temporary; endtask",
     "temporary=a; put();", "q=temporary;", [2, 2, 5, 6, 1, 0]),
    (("logic [7:0] temporary; task initialize; temporary=a; endtask "
      "task put; initialize(); q=temporary; endtask"),
     "put();", "q=temporary;", [1, 1, 4, 5, 0, 255]),
    ("logic [7:0] words[0:3]; task put; q=words[0]+words[3]; endtask",
     "for(int i=0;i<4;i++) words[i]=a+i; put();", "q=words[0]+words[3];", [5, 5, 11, 13, 3, 257]),
    ("task put(output reg [31:0] value); if(a[0]) begin value=17; return; end value=23; endtask",
     "put(q);", "put(q);", [17, 17, 23, 17, 23, 17]),
    ("logic [7:0] temporary=8'h80; task put; temporary[0]=a[0]; q=temporary; endtask",
     "put();", "q=temporary;", [129, 129, 128, 129, 128, 129]),
])
def test_call_summaries_preserve_caller_initialization_and_returns(
    tmp_path, declarations, body, statement, expected,
):
    source = f"module M(input [7:0] a, output logic [31:0] q=0); {declarations} always_comb begin {body} end endmodule"
    _, _, rows = _prepare(tmp_path, source)
    assert rows[0]["inlined_task_dependencies"] is True
    # Icarus rejects task return; plain/native backend equivalence remains the
    # required assertion for that form, with the equivalent branch-only oracle.
    reference = source.replace("always_comb", "always @(a)")
    reference = reference.replace(
        "if(a[0]) begin value=17; return; end value=23;",
        "if(a[0]) value=17; else value=23;",
    )
    results = _assert_entries(tmp_path, source, [1,1,4,5,0,255], (statement,),
        reference_source=reference,
        reference_statements=("put(q);" if body == "put(q);" else "put();",),
        settle_startup=True)
    assert [value for value, _ in results] == expected
