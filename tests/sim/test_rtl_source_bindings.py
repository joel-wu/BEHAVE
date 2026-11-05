"""Complete occurrence projection, without installing a production observer."""

import hashlib
import json
import re
from collections import Counter
from dataclasses import replace

import pytest

from behave.sim.rtl._rtlil_sites import rtlil_site_document, rtlil_site_rows
from behave.sim.rtl.catalogue import goal_id_of, occurrence_key_of, sites_of
from behave.sim.rtl.source_bindings import bind_source_events
from behave.sim.rtl.source_origins import compiler_origins
from behave.sim.rtl.source_plan import plan_source
from tests.sim.test_rtl_source_plan import FIXTURES, _compile, _parse

EDA = pytest.mark.eda("slang", "yosys")
RTLIL = r'''module \M
  process $p
    assign $0\q \q
    assign $0\q 1'0
    switch \a
      case 1'1
        assign $0\q 1'1
        switch \b
          case 1'1
            assign $0\r 1'1
        end
    end
    sync posedge \clk
      update \q $0\q
      update \r $0\r
  end
end
'''


def test_region_side_table_preserves_the_old_catalogue_bytes():
    document = rtlil_site_document(RTLIL)
    rows = document["sites"]
    assert rows == rtlil_site_rows(RTLIL)
    # The /8 row adds wiring evidence. This fixture declares no wires, so that
    # evidence is empty. Retain the pre-region-refactor hash of every original
    # field rather than blessing arbitrary row changes with a new hash.
    assert all(row["input_aliases"] == [] for row in rows)
    original = [{key: value for key, value in row.items() if key != "input_aliases"}
                for row in rows]
    assert hashlib.sha256(json.dumps(original, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == (
        "91e8d46f356dc7cb6a599bf2014a248f2b043228a9f40ca0cc8c162451ca197c")
    assert [region["arms"] for region in document["regions"]] == [
        [], [[5, "1'1"]], [[5, "1'1"]], [[5, "1'1"], [8, "1'1"]],
        [[5, "1'1"], [8, "1'1"]], [[5, "no-match"]],
        [[5, "1'1"], [8, "no-match"]],
    ]
    assert {region["process"] for region in document["regions"]} == {2}
    initialized = RTLIL.replace("sync posedge \\clk", "sync always\n    sync init")
    assert rtlil_site_document(initialized) == {"sites": [], "regions": []}


def _documents(tmp_path, source):
    plan = plan_source(source, _parse(tmp_path, source))
    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    return plan, original, annotated


@EDA
@pytest.mark.parametrize("name", [name for name in FIXTURES if name not in {"unrolled", "helper", "instances"}])
def test_every_unexpanded_occurrence_binds_without_changing_public_goals(tmp_path, name):
    plan, original, annotated = _documents(tmp_path, FIXTURES[name])
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    sites = sites_of(original)
    assert {b.occurrence for b in bound.occurrences} == {occurrence_key_of(s) for s in sites}
    assert {b.goal for b in bound.occurrences} == {goal_id_of(s) for s in sites}
    assert all(plan.events[b.event].role != "assignment" for b in bound.occurrences)
    assert bound.decode([]).occurrences == bound.decode([]).goals == frozenset()
    assert bound.decode(range(len(plan.events))).occurrences == frozenset(occurrence_key_of(s) for s in sites)


@EDA
@pytest.mark.parametrize("body", [
    "default: q<=0; 2'b00: q<=1; 2'b01: q<=2;",
    "2'b00: ; 2'b01: q<=2;",
    "2'b00,2'b10: q<=1; 2'b01: q<=2;",
    "2'b00: begin if(a[1]) q<=2; else q<=3; end 2'b01: q<=2;",
    "/* before */ 2'b00: q<=1; // between\n 2'b01: q<=2;",
])
def test_case_anchors_empty_arms_and_default_order_are_exact(tmp_path, body):
    source = ("module M(input clk,input [1:0] a,output reg [1:0] q);\n"
              f"always @(posedge clk) case(a) {body} endcase\nendmodule")
    plan, original, annotated = _documents(tmp_path, source)
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    assert len(bound.occurrences) == len(sites_of(original))


@EDA
@pytest.mark.parametrize("qualifier", ["", "unique", "unique0", "priority"])
def test_exhaustive_enum_case_binds_compiler_generated_fallback(tmp_path, qualifier):
    source = f"""module M(input clk, input a, output reg q);
      typedef enum logic {{IDLE=0, BUSY=1}} State;
      State state;
      always @(posedge clk) {qualifier} case (state)
        IDLE: begin q<=0; if(a) state<=BUSY; end
        BUSY: begin q<=1; if(!a) state<=IDLE; end
      endcase
    endmodule"""
    plan, original, annotated = _documents(tmp_path, source)
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    assert len(bound.occurrences) == len(sites_of(original))
    if qualifier:
        # An invented default would suppress the original qualifier's no-match
        # check. Native no-match observation must leave that check intact.
        assert f"{qualifier} case (state)" in plan.native().text
        assert "default:" not in plan.native().text


@EDA
@pytest.mark.parametrize("command", [
    "if (1'b0) q<=1; else q<=2;",
    "if (1'b1) q<=1; else q<=2;",
    "case (1'b0) 1'b0: q<=1; 1'b1: q<=2; endcase",
    "case (1'b1) 1'b0: q<=1; 1'b1: q<=2; endcase",
    "case (1'b1) 1'b0: q<=1; 1'b1: q<=2; default: q<=0; endcase",
    "case (1'b0) 1'b0: q<=1; 1'b1: q<=2; default: q<=0; endcase",
    "if (1'b1) ; else q<=2;",
])
def test_constant_controls_preserve_the_retained_catalogue(tmp_path, command):
    source = ("module M(input clk,output reg [1:0] q); "
              f"always @(posedge clk) {command} endmodule")
    plan, original, annotated = _documents(tmp_path, source)
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    assert {b.occurrence for b in bound.occurrences} == {
        occurrence_key_of(s) for s in sites_of(original)}


@EDA
@pytest.mark.parametrize("name", ["helper", "instances"])
def test_one_source_event_is_never_broadcast_to_elaborated_copies(tmp_path, name):
    plan, original, annotated = _documents(tmp_path, FIXTURES[name])
    with pytest.raises(ValueError, match="occurrence contexts"):
        bind_source_events(plan, original, annotated, filename="design.sv")


@EDA
def test_a_single_remaining_copy_retains_its_proved_loop_context(tmp_path):
    source = FIXTURES["unrolled"].replace("i<4", "i<1")
    plan, original, annotated = _documents(tmp_path, source)
    origins = compiler_origins(plan, original, annotated, filename="design.sv")
    assert max(Counter(c.origin for c in origins).values()) == 1
    assert any(e.contexts for e in plan.events)
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    assert bound.event_count == 5  # process, zero/nonzero entry, two body arms
    assert [call.iterations for call in plan.invocations] == [(), (), (), (0,), (0,)]


@EDA
def test_effective_action_uses_region_entry_not_overwritten_source_statements(tmp_path):
    source = """module M(input clk, a, output reg [2:0] q);
      always @(posedge clk) begin
        q <= 1; q <= 2;
        if(a) begin q<=3; q<=4; end
      end
    endmodule"""
    plan, original, annotated = _documents(tmp_path, source)
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    sites = sites_of(original)
    locations = [s for s in sites if s.kind == "location"]
    assert len(locations) == 2
    assert all(not s.action.endswith(("3'001", "3'011")) for s in locations)
    assert source.count("q <=") + source.count("q<=") == 4
    assert all(e.role != "assignment" for e in plan.events)
    assert len(plan.events) == 3  # Process entry and the two if arms, not writes.
    parent, = [e.ordinal for e in plan.events if e.role == "process"]
    branch, = [e.ordinal for e in plan.events if e.choice == "true"]
    root = {occurrence_key_of(s) for s in locations if not s.activation}
    assert bound.decode([parent]).occurrences == root
    with pytest.raises(ValueError, match="enclosing control"):
        bound.decode([branch])
    for invalid in (True, -1, len(plan.events), 0.0, "0"):
        with pytest.raises(ValueError, match="unknown native"):
            bound.decode([invalid])


@EDA
def test_retained_anchors_cannot_hide_priority_or_completeness_mutations(tmp_path):
    plan, original, annotated = _documents(tmp_path, FIXTURES["priority-case"])
    bind_source_events(plan, original, annotated, filename="design.sv")
    pattern = re.compile(r'    attribute \\src "[^"\n]+"\n      case [^\n]+\n        assign [^\n]+\n')

    def mutate(text, delete=False):
        first, second = list(pattern.finditer(text))
        return (text[:first.start()] + ("" if delete else second[0])
                + text[first.end():second.start()] + first[0] + text[second.end():])

    # Mutate BOTH documents: byte equality alone passes, but source priority
    # must still hold. Deleting an occurrence from the annotated compilation
    # must fail against the unmodified original catalogue/document.
    with pytest.raises(ValueError, match="priority"):
        bind_source_events(plan, mutate(original), mutate(annotated), filename="design.sv")
    with pytest.raises(ValueError, match="changed the original"):
        bind_source_events(plan, original, mutate(annotated, delete=True), filename="design.sv")

    plan, original, annotated = _documents(tmp_path / "if", FIXTURES["inline"])
    with pytest.raises(ValueError, match="Boolean arm"):
        bind_source_events(plan, original.replace("case 1'1", "case 1'0"),
                           annotated.replace("case 1'1", "case 1'0"), filename="design.sv")


@EDA
def test_event_origin_alone_does_not_prove_its_control_ancestry(tmp_path):
    plan, original, annotated = _documents(tmp_path, FIXTURES["blocking"])
    bind_source_events(plan, original, annotated, filename="design.sv")
    nested = next(e for e in plan.events if e.choice == "true" and len(e.ancestors) == 2)
    damaged = replace(plan, events=tuple(
        replace(e, ancestors=e.ancestors[:1]) if e == nested else e for e in plan.events))
    with pytest.raises(ValueError, match="control ancestry"):
        bind_source_events(damaged, original, annotated, filename="design.sv")
