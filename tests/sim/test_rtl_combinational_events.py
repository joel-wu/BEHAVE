"""Native combinational activations, not synthetic per-slot predicate hits.

The source observer collects actual events, independently of whether a symbolic
product can encode that settling history. Real Driver/public tests live beside
these raw-source oracles.
All HDL here is test-owned; submitted HDL still runs inside the tool judge.
"""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from behave.sim.rtl.catalogue import occurrence_key_of, sites_of
from behave.sim.rtl.native_events import VERILATOR_FLAGS, prepare_native_events
from behave.sim.rtl.source_bindings import bind_source_events
from behave.sim.rtl.source_origins import compiler_origins
from behave.sim.rtl.trigger_support import driver_triggers, unsupported_triggers

from .test_rtl_execution_policy import _contract
from .test_rtl_source_bindings import _documents
from .test_rtl_source_plan import _run

pytestmark = pytest.mark.eda("slang", "yosys", "verilator", "make")


def _source(process):
    return ("module M(input a,b,output reg q); " + process
            + " if(a) q=b; else q=~b; endmodule")


def _trace(tmp_path, source, collector, driver, *, native=False, event_words=None, preamble="",
           model_name="TOP", run_args=(), compiler_flags=None):
    tmp_path.mkdir()
    (tmp_path / "design.sv").write_text(source)
    (tmp_path / "collector.cpp").write_text(collector.collector_source())
    (tmp_path / "probe.cpp").write_text('''#include "VM.h"
#include "verilated.h"
#include <cstdint>
#include <iostream>
extern "C" int behave_native_events_begin_v1(uint64_t);
extern "C" int behave_native_events_end_v1(uint64_t,uint64_t*,uint64_t,uint64_t*);
extern "C" int behave_native_events_finish_v1();
''' + preamble + '''
int main(int argc, char** argv) {
    Verilated::commandArgs(argc,argv); VM dut(''' + json.dumps(model_name) + ''');
''' + driver + '''
    dut.final(); return behave_native_events_finish_v1();
}''')
    _run(tmp_path, "verilator", "--cc", "--exe", "--build", "-j", "1", "-Wno-fatal", "--assert",
         *(compiler_flags if compiler_flags is not None else VERILATOR_FLAGS if native else ()),
         "--top-module", "M", "design.sv", "probe.cpp", "collector.cpp", timeout_s=90)
    return _replay_trace(tmp_path, event_words=event_words, run_args=run_args)


def _replay_trace(tmp_path, *, event_words=None, run_args=()):
    """Run an already built test fixture with new inputs, never cached evidence."""
    run = subprocess.run([str(tmp_path / "obj_dir/VM"), *run_args], cwd=tmp_path, capture_output=True,
                         text=True, timeout=10, check=True)
    rows = [tuple(map(int, row.split())) for row in run.stdout.splitlines()]
    if event_words is None:
        return rows
    assert all(len(row) == event_words + 2 for row in rows)
    return [(row[0], sum(word << (64 * index) for index, word in enumerate(row[1:-1])), row[-1])
            for row in rows]


def test_compile_cost_controls_preserve_outputs_and_callback_intervals(tmp_path):
    from .test_rtl_source_locations import _collector
    from .test_rtl_source_net_drivers import _prepare

    source = '''module M(input [7:0] a,output logic [7:0] q);
      logic [7:0] left,right,sum;
      assign left=a+1;
      assign right=a^8'h35;
      assign sum=left+right;
      always_comb begin
        if(sum[3]) q=sum^8'h81;
        else q=sum+3;
      end
    endmodule'''
    plan, _, _ = _prepare(tmp_path, source)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    driver = '''
      const unsigned inputs[]={0,1,1,2,127,128,255,0};
      for(unsigned i=0;i<8;++i) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=inputs[i]; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    '''
    observed = plan.native(scoped=True).text
    former = tuple(flag for flag in VERILATOR_FLAGS
                   if flag not in {'-fno-dedup', '-fno-reorder'})
    before = _trace(tmp_path / 'optimized', observed, collector, driver,
                    compiler_flags=former)
    after = _trace(tmp_path / 'bounded_compile', observed, collector, driver,
                   compiler_flags=VERILATOR_FLAGS)
    expected = []
    for value in (0,1,1,2,127,128,255,0):
        total = ((value+1)+(value^0x35)) & 255
        expected.append((total^0x81) if total & 8 else (total+3) & 255)
    assert [row[0] for row in after] == expected
    assert before == after  # Outputs, exact event bitmaps and callback counts.
    assert after[2][1:] == (0, 0)  # Re-evaluation is not a new source event.


@pytest.mark.parametrize("process", ["always_comb", "always @*", "always @(*)"])
def test_native_combinational_inputs_and_repeated_evaluations(tmp_path, process):
    source = _source(process)
    plan, original, annotated = _documents(tmp_path, source)
    bound = bind_source_events(plan, original, annotated, filename="design.sv")
    assert not plan.native_issues
    assert [(e.role, e.choice) for e in plan.events] == [
        ("process", None), ("if-true", "true"), ("if-false", "false")]
    sites = sites_of(original)
    assert {b.occurrence for b in bound.occurrences} == {occurrence_key_of(s) for s in sites}
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
                                      toolchain_sha256="1" * 64, execution_sha256="2" * 64,
                                      callback_limit=100)
    driver = '''
    const unsigned inputs[][2]={{0,0},{0,0},{0,1},{1,1},{1,1},{1,0},{1,0}};
    for (unsigned i=0;i<7;++i) {
        if (behave_native_events_begin_v1(i)) return 1;
        dut.a=inputs[i][0]; dut.b=inputs[i][1]; dut.eval();
        uint64_t bits=0,calls=0;
        if (behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    }
'''
    plain = _trace(tmp_path / "plain", source, collector, driver)
    native = _trace(tmp_path / "native", plan.native().text, collector, driver, native=True)
    assert [row[0] for row in plain] == [1, 1, 0, 1, 1, 0, 0]
    assert [row[0] for row in native] == [row[0] for row in plain]
    assert all(bits == calls == 0 for _, bits, calls in plain)
    # Dump the full native sequence on disagreement: repeated evals must not
    # silently be assumed equivalent to a fresh semantic process activation.
    assert [bits for _, bits, _ in native] == [5, 0, 5, 3, 0, 3, 0], native
    # Initial settling may execute the process more than once. Subsequent
    # single input changes activate it once; identical inputs do not activate it.
    assert native[0][2] >= 2 and native[0][2] % 2 == 0
    assert [calls for _, _, calls in native[1:]] == [0, 2, 2, 0, 2, 0]
    assert not any(unsupported_triggers(site, driver_triggers(_contract())) for site in sites)


@pytest.mark.parametrize("separator", [" ", "\n", " /* π */\n"])
def test_implicit_process_anchor_is_exact_not_a_nearby_source_range(tmp_path, separator):
    source = _source("always_comb" + separator)
    plan, original, annotated = _documents(tmp_path, source)
    controls = compiler_origins(plan, original, annotated, filename="design.sv")
    process, = [c for c in controls if c.kind == "process"]
    origin = plan.controls[0]
    expected = source.encode().index(b"always_comb") + len(b"always_comb")
    assert origin.compiler_span == (expected, expected)
    line, column = plan.compiler.original_position(expected)
    assert process.src == f"design.sv:{line}.{column}-{line}.{column}"
    for anchor in ((origin.start, origin.end), (expected + 1, expected + 1)):
        changed = replace(origin, compiler_span=anchor)
        with pytest.raises(ValueError, match="exact source control"):
            compiler_origins(replace(plan, controls=(changed, *plan.controls[1:])),
                             original, annotated, filename="design.sv")


@pytest.mark.parametrize("shape", ["assignment", "constant", "nested-choice", "literal-loop", "helper"])
def test_comb_contexts_preserve_outputs_and_actual_invocations(tmp_path, shape):
    header = "module M(input [3:0] a,output reg [3:0] q); "
    bodies = {
        "assignment": "always_comb q=a;",
        "constant": "always_comb q=7;",
        "nested-choice": "always_comb if(a[0]) begin if(a[1]) q=a+1; else q=a-1; end else q=0;",
        "literal-loop": "integer i; always_comb for(i=0;i<4;i++) if(a[i]) q[i]=1; else q[i]=0;",
        "helper": ("function automatic [3:0] adjust(input [3:0] v); "
                   "if(v[0]) adjust=v+1; else adjust=v-1; endfunction "
                   "always_comb q=adjust(a);"),
    }
    source = header + bodies[shape] + " endmodule"
    plan, original, annotated = _documents(tmp_path, source)
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
                                      toolchain_sha256="1" * 64, execution_sha256="2" * 64,
                                      callback_limit=100)
    if shape in {"assignment", "constant"}:
        assert [e.role for e in plan.events] == ["process"]
        assert all(site.kind == "location" for site in sites_of(original))
    driver = '''
    for (unsigned i=0;i<32;++i) {
        if (behave_native_events_begin_v1(i)) return 1;
        dut.a=i/2; dut.eval();
        uint64_t bits=0,calls=0;
        if (behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    }
'''
    plain = _trace(tmp_path / "plain", source, collector, driver)
    native = _trace(tmp_path / "native", plan.native().text, collector, driver, native=True)
    assert all(bits == calls == 0 for _, bits, calls in plain)
    assert len(native) == len(plain) == 32
    if shape == "assignment":
        # Independent reproduction: default splitting preserves every output,
        # but hoists the process callback to startup and loses later hits.
        split = _trace(tmp_path / "split", plan.native().text, collector, driver)
        assert [q for q, _, _ in split] == [q for q, _, _ in plain]
        assert split[0][1] == 1 and all(bits == 0 for _, bits, _ in split[1:])
    for index, ((q, bits, calls), (plain_q, _, _)) in enumerate(zip(native, plain, strict=True)):
        value = index // 2
        if shape in {"assignment", "constant"}:
            expected_q = value if shape == "assignment" else 7
            expected = {0}
        elif shape == "literal-loop":
            expected_q = value
            expected = {0, 2} | {3 + bit if value & (1 << bit) else 7 + bit for bit in range(4)}
        elif shape == "helper":
            expected_q = (value + (1 if value & 1 else -1)) & 15
            expected = {0, 1 if value & 1 else 2}
        else:
            expected_q = ((value + (1 if value & 2 else -1)) & 15) if value & 1 else 0
            expected = {0, 1, 2 if value & 2 else 3} if value & 1 else {0, 4}
        assert q == plain_q == expected_q
        if index % 2 or shape == "constant" and index:
            assert bits == calls == 0
        else:
            assert {i for i in range(collector.bindings.event_count) if bits & (1 << i)} == expected
            assert calls >= len(expected)


@pytest.mark.solver
def test_register_update_interval_keeps_both_combinational_arms(tmp_path):
    """Drive/settle and NBA/settle can take opposite arms in one interval."""
    import z3

    from behave.sim.rtl.site_target import predicate_for

    source = """module M(input clk,a,output reg q);
        reg s=0;
        always @(posedge clk) s<=a;
        always_comb if(a ^ s) q=1; else q=0;
    endmodule"""
    plan, original, annotated = _documents(tmp_path, source)
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
                                      toolchain_sha256="1" * 64, execution_sha256="2" * 64,
                                      callback_limit=100)
    assert [(e.role, e.choice) for e in plan.events] == [
        ("process", None), ("process", None), ("if-true", "true"), ("if-false", "false")]
    driver = '''
    for (unsigned i=0;i<5;++i) {
        if (behave_native_events_begin_v1(i)) return 1;
        if(i==0) { dut.clk=0; dut.a=0; }
        if(i==1) { dut.a=1; }
        if(i==3) { dut.clk=0; }
        if(i==4) { dut.a=0; }
        dut.eval(); unsigned before=dut.q;
        if(i==1 || i==4) { dut.clk=1; dut.eval(); }
        uint64_t bits=0,calls=0;
        if (behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<before<<" "<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    }
'''
    plain = _trace(tmp_path / "plain", source, collector, driver)
    native = _trace(tmp_path / "native", plan.native().text, collector, driver, native=True)
    expected_values = [(0, 0), (1, 0), (0, 0), (0, 0), (1, 0)]
    assert [row[:2] for row in plain] == expected_values
    assert [row[:2] for row in native] == expected_values
    assert all(bits == calls == 0 for _, _, bits, calls in plain)
    assert [bits for _, _, bits, _ in native] == [10, 15, 0, 0, 15], native

    # Identify the condition from the fixture's exact XOR cell wiring, not a
    # guessed generated-wire name. The symbolic local predicate is correct at
    # each activation; using it only at the final state loses the true arm.
    import re

    condition, = {site.signal for site in sites_of(original) if site.kind == "case-arm"}
    cell, = [body for body in re.findall(r"^  cell \$xor [^\n]+\n(.*?)^  end$", original,
                                         re.MULTILINE | re.DOTALL)
              if f"    connect \\Y {condition}\n" in body]
    assert "    connect \\A \\a\n" in cell and "    connect \\B \\s\n" in cell
    a, s = z3.BitVecs("comb_a comb_s", 1)

    class Activation:
        @staticmethod
        def value_var(signal):
            return (a ^ s, 1) if signal == condition.lstrip("\\") else (None, None)

    arms = [site for site in sites_of(original) if site.kind == "case-arm"]
    terms = {site.goal: predicate_for(Activation(), site) for site in arms}

    def active(values):
        av, sv = values
        return {goal for goal, term in terms.items() if z3.is_true(z3.simplify(
            z3.substitute(term, (a, z3.BitVecVal(av, 1)), (s, z3.BitVecVal(sv, 1)))))}

    native_goals = collector.bindings.decode({0, 1, 2, 3}).goals & set(terms)
    assert native_goals == active((1, 0)) | active((1, 1)) == set(terms)
    assert active((1, 1)) < native_goals
    # Concrete observation records both activations. This is deliberately not
    # evidence that the final-state predicate is an exact interval encoding.
    assert not any(unsupported_triggers(site, driver_triggers(_contract())) for site in sites_of(original))


@pytest.mark.isolation
def test_tool_judge_prepares_comb_events_with_exact_source_binding(tmp_path):
    from behave.sim.rtl.catalogue import catalogue_manifest_of
    from behave.sim.rtl.native_preparation import PreparedNativeEvents, prepare_source_observer
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_plan import plan_source

    from .test_rtl_observation_frontend import _installed
    from .test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    source = _source("always_comb")
    plan, original, annotated = _documents(tmp_path, source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    manifest, retained = catalogue_manifest_of(tmp_path / "design.sv", "M")
    assert {occurrence_key_of(site) for site in retained.values()} == {
        occurrence_key_of(site) for site in sites_of(original)}
    prepared = prepare_source_observer(source, top="M", execution_sha256="2" * 64,
                                      catalogue=manifest, callback_limit=100)
    assert type(prepared) is PreparedNativeEvents, prepared.as_dict()
    assert prepared.source == plan.native().text
    assert prepared.plan.bindings == bind_source_events(plan, original, annotated, filename="design.sv")
    prepared.plan.check_catalogue(manifest)
    assert not any(unsupported_triggers(site, driver_triggers(_contract())) for site in sites_of(original))


def _judge_traces(tmp_path, source, *, native_transform=None, compile_args=()):
    from behave.sim.rtl.native_preparation import PreparedNativeEvents, prepare_source_observer
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_goals import manifest_of
    from behave.sim.rtl.source_plan import plan_source

    from .test_rtl_observation_frontend import _installed
    from .test_rtl_tool_isolation import _needs_namespace

    _needs_namespace()
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    source_plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    prepared = prepare_source_observer(source, top="M", execution_sha256="2" * 64,
        catalogue=manifest_of(source_plan.declarations, filename="design.sv"),
        callback_limit=1000, scope_root="")
    assert type(prepared) is PreparedNativeEvents, prepared
    traces = _collector_traces(tmp_path, source, prepared.source, prepared.plan,
                              native_transform=native_transform, compile_args=compile_args)
    return prepared, traces


def _collector_traces(tmp_path, source, native_source, plan, *, native_transform=None, compile_args=()):
    """One judge harness for prepared plans and independently built test plans."""
    import json

    from behave.sim.harness import generate_makefile
    from behave.sim.rtl.tool_isolation import run_verilator_workspace
    from behave.sim.rtl.tool_protocol import command_result, command_stream

    traces = {}
    for name, text in (("plain", source), ("native", native_source)):
        work = tmp_path / name
        work.mkdir()
        if name == "native" and native_transform is not None:
            text = native_transform(work, text)
        (work / "rtl.sv").write_text(text)
        (work / "collector.cpp").write_text(plan.collector_source())
        (work / "collector-plan.json").write_text(json.dumps(plan.as_dict()))
        fixture = Path(__file__).parent / "fixtures/source_events/comb_collector_tb.py"
        (work / "comb_collector_tb.py").write_bytes(fixture.read_bytes())
        # Both link the same transport; the plain HDL never calls it.
        generate_makefile(work, "M", tb_module="comb_collector_tb",
                          coverage=False, native_events=True)
        if compile_args:
            makefile = work / "Makefile"
            makefile.write_text(makefile.read_text() + "\nEXTRA_ARGS += " + " ".join(compile_args) + "\n")
        built, replayed = run_verilator_workspace(
            work, top_module="M", test_module="comb_collector_tb",
            role="comb-event-oracle", outputs=("native-trace.json",),
            compile_timeout_s=90, simulate_timeout_s=30)
        assert all(row["returncode"] == 0 for row in built.commands), built.as_dict()
        assert command_result(replayed, "comb-event-oracle:simulate")["returncode"] == 0, replayed.as_dict()
        assert (work / "native-trace.json").is_file(), command_stream(
            replayed, "comb-event-oracle:simulate", "stdout").decode("utf-8", errors="replace")
        traces[name] = json.loads((work / "native-trace.json").read_text())
    return traces


@pytest.mark.isolation
@pytest.mark.parametrize("expression", ["a", "a-a", "a^a"])
def test_judge_makefile_and_cocotb_preserve_comb_assignment_events(tmp_path, expression):
    source = ("module M(input [3:0] a,input noise,output reg [3:0] q); always_comb q="
              + expression + "; endmodule")
    prepared, traces = _judge_traces(tmp_path, source)
    assert prepared.plan.bindings.event_count == 2
    plain, native = traces["plain"]["samples"], traces["native"]["samples"]
    assert [q for q, _, _ in plain] == [
        i // 2 if expression == "a" else 0 for i in range(32)]
    assert [q for q, _, _ in native] == [q for q, _, _ in plain]
    for name in ("plain", "native"):
        assert len(traces[name]["idle"]) == 31
        assert [q for q, _, _ in traces[name]["idle"]] == [q for q, _, _ in plain[:-1]]
    assert all(not events and calls == 0 for _, events, calls in plain + traces["plain"]["idle"])
    # The public launcher must meet the same source-activation rule as the
    # independent event-driven oracle below. Checking only even rows used to
    # conceal callbacks at unchanged a and during timer-only evaluation.
    expected = [[0, 1] if i % 2 == 0 else [] for i in range(32)]
    actual = {"samples": [events for _, events, _ in native],
              "idle": [events for _, events, _ in traces["native"]["idle"]]}
    assert actual == {"samples": expected, "idle": [[] for _ in range(31)]}, traces["native"]
    assert native[0][2] >= 1  # Startup may need multiple settle passes.
    assert [calls for _, _, calls in native[1:]] == [
        2 if i % 2 == 0 else 0 for i in range(1, 32)], native
    for index, (_value, events, _calls) in enumerate(native):
        observed = prepared.plan.bindings.decode(events).occurrences
        assert {(key.path[1:3], key.role) for key in observed} == (
            {(("location", source.index("q=")), "entry")} if index % 2 == 0 else set())
    assert all(calls == 0 for _, _, calls in traces["native"]["idle"])


@pytest.mark.isolation
@pytest.mark.parametrize("registered", [False, True], ids=["chain", "register-fed"])
def test_judge_reactivates_only_when_the_internal_read_value_changes(tmp_path, registered):
    producer = ("always @(posedge noise) middle <= a;" if registered else
                "always_comb middle = a >> 1;")
    source = ("module M(input [3:0] a,input noise,output reg [3:0] q);"
              "reg [3:0] middle=0; " + producer + " always_comb q=middle; endmodule")
    prepared, traces = _judge_traces(tmp_path, source)
    assert prepared.plan.bindings.event_count == 4
    plain, native = traces["plain"]["samples"], traces["native"]["samples"]
    values, events = [], []
    for index in range(32):
        if registered:
            values.append(max(index - 1, 0) // 2)
            events.append(([0, 1] if index % 2 else [])
                          + ([2, 3] if index == 0 or index >= 3 and index % 2 else []))
        else:
            values.append(index // 4)
            events.append(([0, 1] if index % 2 == 0 else []) + ([2, 3] if index % 4 == 0 else []))
    assert [q for q, _, _ in plain] == values
    assert [q for q, _, _ in native] == values
    assert [seen for _, seen, _ in native] == events
    for expected, (_value, observed, _calls) in zip(events, native, strict=True):
        hits = prepared.plan.bindings.decode(observed).occurrences
        locations = set()
        if 0 in expected:
            locations.add(source.index("middle <= a;" if registered else "middle = a >> 1;"))
        if 2 in expected:
            locations.add(source.index("q=middle;"))
        assert {(key.path[1], key.path[2], key.role) for key in hits} == {
            ("location", start, "entry") for start in locations}
    assert [calls for _, _, calls in native[1:]] == [len(seen) for seen in events[1:]]
    assert len(traces["native"]["idle"]) == 31
    assert all(not seen and calls == 0 for _, seen, calls in traces["native"]["idle"])


@pytest.mark.eda("iverilog", "vvp")
@pytest.mark.parametrize("expression", ["a", "a-a", "a^a"])
def test_independent_comb_activation_oracle(tmp_path, expression):
    """Changes to read values activate the process; timer/noise changes do not.

    The fixture-owned marker reads no HDL values and is not generated from our
    source plan or catalogue. This also checks that cancellation does not remove
    source sensitivity to a, even though the output is constant.
    """
    reference = '''module M(input [3:0] a,input noise,output reg [3:0] q);
      always_comb begin
        $display("EVENT");
        q=EXPRESSION;
      end
    endmodule
    module tb;
      reg [3:0] a=0; reg noise=0; wire [3:0] q; integer i;
      M dut(a,noise,q);
      initial begin
        for(i=0;i<32;i=i+1) begin
          #1; a=i/2; noise=i%2;
          #1; $display("SAMPLE %0d %0d",i,q);
        end
        $finish;
      end
    endmodule
    '''.replace("EXPRESSION", expression)
    (tmp_path / "reference.sv").write_text(reference)
    _run(tmp_path, "iverilog", "-g2012", "-s", "tb", "-o", "reference", "reference.sv")
    result = subprocess.run(["vvp", "reference"], cwd=tmp_path, capture_output=True,
                            text=True, check=True, timeout=10)
    rows, pending = [], 0
    for line in result.stdout.splitlines():
        if line == "EVENT":
            pending += 1
        elif line.startswith("SAMPLE "):
            _, index, value = line.split()
            assert int(index) == len(rows)
            rows.append((int(value), pending))
            pending = 0
    assert len(rows) == 32 and pending == 0, result.stdout
    assert [q for q, _ in rows] == [
        i // 2 if expression == "a" else 0 for i in range(32)]
    assert rows[0][1] >= 1
    assert [calls for _, calls in rows[1:]] == [
        1 if i % 2 == 0 else 0 for i in range(1, 32)], rows


@pytest.mark.eda("iverilog", "vvp")
@pytest.mark.parametrize(("body", "source_reactivates"), [
    ("q=a-a;", True), ("q=a^a;", True), ("if(1'b0) q=a; else q=7;", False)])
def test_typed_read_anchors_preserve_erased_source_dependencies(tmp_path, body, source_reactivates):
    """Native event agreement is necessary, not permission to open the phase gate."""
    from behave.sim.rtl.source_plan import plan_source

    from .test_rtl_source_declarations import _documents as typed_documents
    from .test_rtl_source_plan import _compile
    header = "module M(input [3:0] a,output reg [3:0] q); "
    source = header + "always_comb " + body + " endmodule"
    cst, ast = typed_documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
                                      toolchain_sha256="1" * 64, execution_sha256="2" * 64,
                                      callback_limit=100)
    driver = '''
    for (unsigned i=0;i<3;++i) {
        if (behave_native_events_begin_v1(i)) return 1;
        dut.a=i; dut.eval();
        uint64_t bits=0,calls=0;
        if (behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    }
'''
    native = _trace(tmp_path / "native", plan.native().text, collector, driver, native=True)
    # The independent hook does not read a or add a new sensitivity dependency.
    reference = (header + 'always_comb begin $display("EVENT %0t",$time); '
                 + body + ' end endmodule\n'
                 'module tb; reg [3:0] a=0; wire [3:0] q; M dut(a,q); '
                 'initial begin #1; a=1; #1; a=2; #1; $finish; end endmodule')
    (tmp_path / "reference.sv").write_text(reference)
    _run(tmp_path, "iverilog", "-g2012", "-s", "tb", "-o", "reference", "reference.sv")
    result = subprocess.run(["vvp", "reference"], cwd=tmp_path, capture_output=True,
                            text=True, check=True, timeout=10)
    times = [int(row.split()[1]) for row in result.stdout.splitlines() if row.startswith("EVENT ")]
    if source_reactivates:
        assert 1 in times and 2 in times  # Both changes execute the source process.
    else:
        # Calibration: this reference also removes a read in a statically dead
        # arm. It is not evidence of a native/reference disagreement.
        assert times == [0]
    assert native[0][1] & 1
    if source_reactivates:
        # The raw plan retains both process activation and statement entry.
        assert all(bits == 3 and calls == 2 for _, bits, calls in native[1:]), native
    else:
        assert all(bits == calls == 0 for _, bits, calls in native[1:]), native
    assert not any(unsupported_triggers(site, driver_triggers(_contract())) for site in sites_of(original))
