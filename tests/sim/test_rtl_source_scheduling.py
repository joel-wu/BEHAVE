"""Compiler scheduling metadata and source-map integration, not a hit filter."""

import pytest

from behave.sim.rtl.source_plan import CALLBACK, plan_source
from behave.sim.rtl.source_scheduling import PendingActivations, sensitivities_from_document

from .test_rtl_source_plan import _parse


def test_pending_bits_keep_uniform_scope_names_and_independent_compiler_storage():
    pending = PendingActivations.for_processes([11, 3, 11, 19])
    assert pending.reference(3) == "__behave_native_event_pending_0"
    assert pending.reference(11) == "__behave_native_event_pending_1"
    assert pending.reference(19) == "__behave_native_event_pending_2"
    assert pending.declaration == "".join(
        f" bit __behave_native_event_pending_{index}=0;\n" for index in range(3))
    assert pending.owner_reference("M.g[3]." + pending.reference(3)) == (
        pending.owner_reference("M.g[3]." + pending.reference(19)))
    assert pending.owner_reference("M.g[3]." + pending.reference(3)) != (
        pending.owner_reference("M.g[4]." + pending.reference(3)))
    assert PendingActivations.for_processes([]).declaration == ""


def test_report_resume_is_inside_the_source_activation_guard():
    from behave.sim.rtl.source_deferred_events import DeferredRead
    from behave.sim.rtl.source_scheduling import activation_guard
    from behave.sim.rtl.source_statement_events import RESUME_DECLARATION, RESUME_FUNCTION

    guard = activation_guard(3, ('a[0]',), pending='pending',
                             deferred=(DeferredRead('epoch', 4),))
    assert guard.before.startswith('begin if (')
    assert guard.before.endswith(f' {RESUME_FUNCTION}();\n')
    assert 'endfunction' in RESUME_DECLARATION
    assert RESUME_FUNCTION not in guard.after
    assert 'epoch_seen=epoch_commit[1];' in guard.after


def _row(origin, *, procedure="AlwaysComb", triggers=("a",), scope="M"):
    return {"file": "design.sv", "start": origin.start, "end": origin.end,
            "procedure": procedure, "triggers": list(triggers), "scope": scope}


@pytest.mark.eda("slang")
@pytest.mark.parametrize(("process", "kind", "triggers", "expected"), [
    ("always_comb", "AlwaysComb", ("a",), "always_comb begin if ("),
    ("always_comb", "AlwaysComb", (), "initial"),
    ("always @*", "Always", ("a",), "always_comb begin if ("),
    ("always @(*)", "Always", ("a",), "always_comb begin if ("),
    ("always @*", "Always", (), "initial wait (1'b0)"),
    ("always_ff @(posedge clk)", "AlwaysFF", (), "always_ff @(posedge clk)"),
])
def test_wait_edits_share_original_probes_and_byte_coordinates(tmp_path, process, kind, triggers, expected):
    source = f"module M(input clk, a, output reg q); /* π */ {process} q=a; endmodule"
    cst = _parse(tmp_path, source)
    original = plan_source(source, cst)
    plan = plan_source(source, cst, sensitivity_document=[_row(
        original.controls[0], procedure=kind, triggers=triggers)])
    assert plan.event_waits
    assert plan.compiler == original.compiler
    assert plan.events == original.events
    assert plan.controls == original.controls
    for scoped in (False, True):
        native = plan.native(scoped=scoped)
        assert expected in native.text
        # The body remains the original bytes, with the same source coordinate.
        body = native.text.encode().index(b"q=a;")
        assert native.source_offset(body) == source.encode().index(b"q=a;")
        assert native.source_offset(body + 4) == source.encode().index(b"q=a;") + 4
        assert CALLBACK in native.text
        transport = "__behave_native_event_source_hit" if scoped else "__behave_native_flat_hit"
        assert transport in native.text
        if kind != "AlwaysFF" and triggers:
            pending = PendingActivations.for_processes([original.controls[0].start])
            assert pending.reference(original.controls[0].start) in native.text
            assert "@(a)" not in native.text and "initial forever" not in native.text
        if kind != "AlwaysFF" and not triggers:
            assert "forever" not in native.text


def test_instance_reads_must_agree_not_be_unioned():
    class Origin:
        start, end = 10, 40
    left = _row(Origin, triggers=("b", "a", "a"), scope="M.left")
    right = _row(Origin, triggers=("a", "b"), scope="M.right")
    rows = sensitivities_from_document([left, right], filename="design.sv")
    assert rows[10, 40].triggers == ("a", "b")
    right["triggers"] = ["a"]
    with pytest.raises(ValueError, match="instance-dependent"):
        sensitivities_from_document([left, right], filename="design.sv")


def test_task_frame_qualification_must_hold_for_every_physical_instance():
    class Origin:
        start, end = 10, 40

    good = {**_row(Origin, scope="M.good"), "inlined_task_dependencies": True,
            "task_frame_inputs": []}
    stateful = {**_row(Origin, scope="M.stateful"), "inlined_task_dependencies": False,
                "task_frame_inputs": ["M.stateful.update.previous"]}
    for members in ([good, stateful], [stateful, good]):
        process = sensitivities_from_document(members, filename="design.sv")[10, 40]
        assert not process.inlined_task_dependencies
        assert process.task_frame_inputs == ("M.stateful.update.previous",)


def test_compiler_parameter_partition_is_disjoint_and_keeps_each_read_set():
    class Origin:
        start, end = 10, 40
    rows = [dict(_row(Origin, triggers=(f"a[{index}]",), scope=f"M.g[{index}]"),
                 instance_parameters=[{"reference": "\\k ", "width": 32, "bits": str(index)}])
            for index in range(2)]
    process = sensitivities_from_document(rows, filename="design.sv")[10, 40]
    assert [item.triggers for item in process.instances] == [("a[0]",), ("a[1]",)]
    assert "32'h0" in process.instances[0].selector
    assert "32'h1" in process.instances[1].selector
    # An identical elaboration environment cannot select two different reads.
    rows[1]["instance_parameters"] = rows[0]["instance_parameters"]
    with pytest.raises(ValueError, match="distinguishing compiler parameters"):
        sensitivities_from_document(rows, filename="design.sv")


def test_large_instance_partition_emits_separate_generated_statements():
    from behave.sim.rtl.source_scheduling import InstanceSensitivity, activation_guard

    instances = tuple(InstanceSensitivity(f"lane == {index}", (f"data[{index}]",))
                      for index in range(512))
    guard = activation_guard(3, (), instances=instances)
    # This is an emitter shape property, not a runtime source/output quota.
    # Each extra instance adds separate statements / disjuncts rather than
    # extending one preprocessor line without bound.
    assert guard.declarations.count("\n") >= len(instances)
    assert guard.before.count(".active()") == 1
    assert guard.after.count(".remember()") == 1
    for index in range(len(instances)):
        assert f"type(data[{index}] )" in guard.declarations
        assert f"lane == {index}" in guard.declarations


@pytest.mark.eda("slang")
def test_self_write_exemption_is_physical_and_blocking_only(tmp_path):
    from behave.sim.rtl.shared_frontend import parse_observation_source

    from .test_rtl_observation_frontend import _installed

    source = """module leaf(input clk,a, output reg q=0);
      reg x=0;
      always @* begin x=a; x=x+1; x=0; end
      always @* q<=q+x;
    endmodule
    module M(input clk,a,output [1:0] q);
      leaf left(clk,a,q[0]), right(clk,a,q[1]);
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    _, sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    own_x, other_x, own_nba = [], [], []
    for row in sensitivity:
        for read in row["reads"]:
            for write in read["write_references"]:
                symbol = source[write["start"]:write["end"]]
                if symbol == "q":
                    own_nba.append(write["self_blocking"])
                elif symbol == "x":
                    target = own_x if row["start"] == source.index("always @*") else other_x
                    target.append(write["self_blocking"])
    assert own_x and all(own_x)
    assert other_x and not any(other_x)
    assert own_nba and not any(own_nba)


@pytest.mark.eda("slang")
def test_missing_or_extra_compiler_process_is_not_silently_ignored(tmp_path):
    source = "module M(input a, output reg q); always_comb q=a; endmodule"
    cst = _parse(tmp_path, source)
    original = plan_source(source, cst)
    row = _row(original.controls[0])
    with pytest.raises(ValueError, match="no compiler sensitivity"):
        plan_source(source, cst, sensitivity_document=[])
    with pytest.raises(ValueError, match="unselected process"):
        plan_source(source, cst, sensitivity_document=[row, dict(row, start=1, end=2)])
