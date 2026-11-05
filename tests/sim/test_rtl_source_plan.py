"""Real CST/Yosys origin checks; these do not install a production observer.

Only fixed, test-owned HDL is parsed directly. Submitted RTL must keep parsing,
compilation and observation inside the production tool judge.
"""

import copy
import json
import os
import re
import signal
import subprocess
import sys
from collections import Counter
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import behave
from behave.sim.rtl.catalogue import goal_id_of, occurrence_key_of, sites_of
from behave.sim.rtl.native_events import prepare_native_events
from behave.sim.rtl.site_target import rtlil_name, site_hits
from behave.sim.rtl.source_bindings import bind_source_events
from behave.sim.rtl.source_origins import compiler_origins
from behave.sim.rtl.source_plan import plan_source

pytestmark = pytest.mark.eda("slang", "yosys")
FIXTURES = {
    "inline": """module M(input clk, a, output reg q);
        always @(posedge clk) if (a) q <= 1; else q <= 0;
    endmodule""",
    "unrolled": """module M(input clk, input [3:0] a, output reg [3:0] q);
        integer i;
        always @(posedge clk) begin
            for (i=0; i<4; i=i+1)
                if (a[i]) q[i] <= 1'b1; else q[i] <= 1'b0;
        end
    endmodule""",
    "helper": """module M(input clk, input [3:0] a, output reg [7:0] q);
        function automatic [3:0] adjust(input [3:0] v);
            if (v[0]) adjust = v + 1; else adjust = v - 1;
        endfunction
        always @(posedge clk) q <= {adjust(a), adjust(a + 1)};
    endmodule""",
    "instances": """module Inner #(parameter K=1)(input clk, input [3:0] a, output reg [3:0] q);
        always @(posedge clk) if (a[0]) q <= a + K; else q <= a - K;
    endmodule
    module M(input clk, input [3:0] a, output [7:0] q);
        Inner #(1) left(clk,a,q[3:0]);
        Inner #(2) right(clk,a,q[7:4]);
    endmodule""",
    "blocking": """module M(input clk, input a, output reg [3:0] q);
        always @(posedge clk) begin
            q = q + 1;
            if (q[0]) q = 4; else if (a) q = 6; else q = 8;
        end
    endmodule""",
    "dynamic-write": """module M(input clk, input [1:0] index, input value, output reg [3:0] q);
        always @(posedge clk) q[index] <= value;
    endmodule""",
    "constant-if": """module M(input clk, input [3:0] a, output reg [3:0] q);
        always @(posedge clk) if (1'b0) q <= a; else q <= 4;
    endmodule""",
    "priority-case": """module M(input clk, input [2:0] a, output reg [3:0] q);
        always @(posedge clk) casez (a)
            3'b1??: q <= 1;
            3'b?1?, 3'b001: q <= 2;
        endcase
    endmodule""",
    "comment": """module M(input clk, a, output reg q);
        // leading trivia and same-line comments must not eat the annotation
        always @(posedge clk) /* before */ if (a) q <= 1; // taken
    endmodule""",
    "initial": """module M(input clk, input [3:0] a, output reg [3:0] q = 7);
        initial begin if (1) q = 8; else q = 0; end
        always @(posedge clk) if (a[0]) q <= a; else q <= 4;
    endmodule""",
    "always-ff": """module M(input clk, a, output logic q);
        always_ff @(posedge clk) if (a) q <= 1; else q <= 0;
    endmodule""",
    "unique-case": """module M(input clk, input [1:0] a, output reg q);
        always @(posedge clk) unique case (a) 0:q<=0; 1:q<=1; endcase
    endmodule""",
    "priority-keyword-case": """module M(input clk, input [1:0] a, output reg q);
        always @(posedge clk) priority case (a) 0:q<=0; default:q<=1; endcase
    endmodule""",
    "unicode-comment": """module M(input clk, a, output reg q);
        /* π is UTF-8, columns still count bytes */ always @(posedge clk) if (a) q <= 1;
    endmodule""",
}


def _run(directory, *argv, env=None, timeout_s=30):
    # Fixed-fixture compiler probes still own their complete subprocess tree.
    # Killing only make on timeout leaves its C++ compiler running into later tests.
    if argv[0] == "verilator":
        from behave.core.paths import eda_root
        from behave.sim.rtl.native_dependencies import SIM_VERILATOR_EXECUTABLE

        eda = eda_root()
        argv = (str(eda / "libexec" / SIM_VERILATOR_EXECUTABLE), *argv[1:])
        env = dict(os.environ if env is None else env,
                   VERILATOR_ROOT=str(eda / "share/verilator"))
    with subprocess.Popen(argv, cwd=directory, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, env=env, start_new_session=True) as process:
        try:
            output, _ = process.communicate(timeout=timeout_s)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise
    assert process.returncode == 0, output


def _parse(directory, source):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "design.sv").write_text(source)
    _run(directory, "slang", "--parse-only", "--cst-json", "tree.json", "design.sv")
    return json.loads((directory / "tree.json").read_text())


def _compile(directory, source, filename="design.sv"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(source)
    _run(directory, "yosys", "-Q", "-T", "-p",
         f"read_verilog -sv {filename}; hierarchy -top M; flatten; write_rtlil design.rtlil")
    return (directory / "design.rtlil").read_text()


@pytest.mark.parametrize("name", FIXTURES)
def test_one_plan_retains_compiler_identity_and_all_control_origins(tmp_path, name):
    source = FIXTURES[name]
    document = _parse(tmp_path, source)
    plan = plan_source(source, document)
    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    controls = compiler_origins(plan, original, annotated, filename="design.sv")
    assert controls
    assert {c.origin for c in controls} <= {c.key for c in plan.controls}
    retained = Counter(c.origin for c in controls)
    if name in {"unrolled", "helper", "instances"}:
        assert max(retained.values()) == (4 if name == "unrolled" else 2)
    # Whole-document equality in compiler_origins is deliberately stronger than
    # this nonempty catalogue check; equal counts alone would not preserve goals.
    sites = sites_of(original)
    assert {goal_id_of(s) for s in sites} and {occurrence_key_of(s) for s in sites}
    assert all(e.origin in {c.key for c in plan.controls}
               or e.role in {"loop-zero", "loop-nonzero"} for e in plan.events)
    if name == "helper":
        with pytest.raises(ValueError, match="helper occurrence contexts"):
            plan.require_compiler_binding()
    native = plan.native()
    assert "__behave_native_event_hit" in native.text
    document.clear()
    assert plan.compiler.text == annotated_source(plan)
    with pytest.raises(FrozenInstanceError):
        plan.controls = ()


def annotated_source(plan):
    # Rebuilding from immutable edits has no dependency on the original CST.
    return type(plan.compiler)(plan.compiler.source, plan.compiler.insertions).text


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("attribute", ["", "(* parallel_case *)"])
@pytest.mark.parametrize("qualifier,selector,arms,fails", [
    ("unique", 3, "0:q=1; 1:q=2;", True),
    ("priority", 3, "0:q=1; 1:q=2;", True),
    ("unique0", 3, "0:q=1; 1:q=2;", False),
    ("unique", 3, "2'b1?:q=1; 2'b?1:q=2;", True),
])
def test_qualified_case_native_observation_preserves_original_checks(
        tmp_path, attribute, qualifier, selector, arms, fails):
    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_bindings import _documents

    source = f"""module M(input clk, input [1:0] a, output reg [1:0] q);
      always @(posedge clk) {attribute} {qualifier} casez(a) {arms} endcase
    endmodule"""
    plan, original, annotated = _documents(tmp_path, source)
    collector = prepare_native_events(plan, original, annotated, filename="design.sv",
        toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=100)
    driver = f"""
      if (behave_native_events_begin_v1(0)) return 1;
      dut.clk=0; dut.a={selector}; dut.eval(); dut.clk=1; dut.eval();
      uint64_t bits=0,calls=0;
      if (behave_native_events_end_v1(0,&bits,1,&calls)) return 2;
      std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    """
    for name, text in (("plain", source), ("native", plan.native().text)):
        if fails:
            with pytest.raises(subprocess.CalledProcessError) as caught:
                _trace(tmp_path / name, text, collector, driver, native=name == "native")
            assert "Assertion failed" in caught.value.stdout
        else:
            rows = _trace(tmp_path / name, text, collector, driver, native=name == "native")
            assert len(rows) == 1


def test_source_plan_rejects_nonroundtrip_macro_and_reserved_names(tmp_path):
    source = FIXTURES["inline"]
    doc = _parse(tmp_path / "plain", source)
    with pytest.raises(ValueError, match="exact source"):
        plan_source(source + " ", doc)
    bad = copy.deepcopy(doc)
    bad["syntaxTrees"].append(copy.deepcopy(bad["syntaxTrees"][0]))
    with pytest.raises(ValueError, match="exactly one"):
        plan_source(source, bad)
    for name in ("behave_control_origin", "__behave_native_event_hit", "\\behave_control_origin "):
        reserved = source.replace("input clk, a,", "input clk, a, " + name + ",")
        with pytest.raises(ValueError, match="reserved namespace"):
            plan_source(reserved, _parse(tmp_path / "reserved", reserved))
    macro = "`define P a\n" + source.replace("if (a)", "if (`P)")
    with pytest.raises(ValueError, match="concrete tokens|exact source"):
        plan_source(macro, _parse(tmp_path / "macro", macro))


@pytest.mark.parametrize("prefix", [
    "`timescale 1ns/1ps\n", "`timescale 10 ns / 1 ps\n// π\n",
    "`default_nettype none\n", "`resetall\n", "`celldefine\n",
])
def test_concrete_directives_preserve_source_offsets(tmp_path, prefix):
    from .test_rtl_source_bindings import _documents

    source = prefix + FIXTURES["inline"]
    if "`celldefine" in prefix:
        source += "\n`endcelldefine\n"
    plan, original, annotated = _documents(tmp_path, source)
    assert plan.native().source == source
    assert plan.native().text.count(prefix) == 1
    assert bind_source_events(plan, original, annotated, filename="design.sv").occurrences


def test_implicit_latch_needs_compiler_sensitivity_for_native_observation(tmp_path):
    source = "module M(input a, output reg q); always_latch if(a) q=1; else q=0; endmodule"
    plan = plan_source(source, _parse(tmp_path, source))
    with pytest.raises(ValueError, match="supported event control"):
        plan.native()


def test_explicit_change_control_retains_its_source_wait(tmp_path):
    source = "module M(input a, output reg q); always @(a) if(a) q=1; else q=0; endmodule"
    plan = plan_source(source, _parse(tmp_path, source))
    assert "always @(a)" in plan.native().text
    # Actual activation/output equivalence is checked against Icarus by
    # test_rtl_source_explicit_events, not inferred from successful rewriting.


def test_compiler_identity_mutations_are_not_normalized_away(tmp_path):
    source = FIXTURES["inline"]
    plan = plan_source(source, _parse(tmp_path, source))
    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    assert compiler_origins(plan, original, annotated, filename="design.sv")
    mutants = [
        annotated.replace('"control_0"', '"unknown"'),
        "\n".join(line for line in annotated.split("\n") if "behave_control_origin" not in line),
        annotated.replace("sync posedge", "sync negedge"),
        annotated.replace("case 1'1", "case 1'0"),
        annotated.replace('"design.sv:', '"other.sv:'),
    ]
    for mutant in mutants:
        assert mutant != annotated
        with pytest.raises(ValueError):
            compiler_origins(plan, original, mutant, filename="design.sv")


def test_initialization_is_not_a_runtime_structural_goal(tmp_path):
    rtlil = _compile(tmp_path, FIXTURES["initial"])
    sites = sites_of(rtlil)
    assert sites
    assert all(s.triggers == (("posedge", "\\clk"),) for s in sites), [s.as_dict() for s in sites]


def test_hierarchy_source_fields_are_projected_without_ignoring_them(tmp_path):
    source = """module Child(input a, output reg q); always_comb q=a; endmodule
module M(input a,b,output q,r); Child left(a,q); Child right(b,r); endmodule
"""
    plan = plan_source(source, _parse(tmp_path, source))
    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    assert 'attribute \\module_src ' in annotated and 'attribute \\cell_src ' in annotated
    controls = compiler_origins(plan, original, annotated, filename="design.sv")
    assert len([control for control in controls if control.kind == "process"]) == 2
    # A wrong hierarchy coordinate is still an error. The fix maps all compiler
    # provenance fields; it does not remove scopeinfo records from comparison.
    for attribute in ("module_src", "cell_src"):
        mutant = annotated.replace(f'attribute \\{attribute} "design.sv:',
                                   f'attribute \\{attribute} "other.sv:', 1)
        with pytest.raises(ValueError, match="outside the source plan"):
            compiler_origins(plan, original, mutant, filename="design.sv")


def test_two_known_origins_cannot_be_swapped_or_merged(tmp_path):
    source = FIXTURES["blocking"]
    plan = plan_source(source, _parse(tmp_path, source))
    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    assert compiler_origins(plan, original, annotated, filename="design.sv")
    a, b = [c.key for c in plan.controls if c.kind == "ConditionalStatement"]
    swapped = annotated.replace(f'"{a}"', '"SWAP"').replace(f'"{b}"', f'"{a}"').replace('"SWAP"', f'"{b}"')
    merged = annotated.replace(f'"{b}"', f'"{a}"')
    for mutant in (swapped, merged):
        with pytest.raises(ValueError, match="exact source control"):
            compiler_origins(plan, original, mutant, filename="design.sv")


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("transport", ["diagnostic", "bounded", "cocotb",
                                       pytest.param("judge", marks=pytest.mark.isolation)])
def test_same_plan_native_events_and_original_state_outputs(tmp_path, transport):
    fixtures = Path(__file__).with_name("fixtures") / "source_events"
    source = (fixtures / "design.sv").read_text()
    plan = plan_source(source, _parse(tmp_path / "parse", source))
    filename = "design.sv"
    original = _compile(tmp_path / "original-il", source, filename)
    annotated = _compile(tmp_path / "annotated-il", plan.compiler.text, filename)
    bound = bind_source_events(plan, original, annotated, filename=filename)
    collector = prepare_native_events(
        plan, original, annotated, filename=filename, toolchain_sha256="1" * 64,
        execution_sha256="2" * 64, callback_limit=1000)
    native = plan.native().text
    if transport == "judge":
        from behave.sim.rtl.native_events import prepare_declared_native_events
        from behave.sim.rtl.native_preparation import PreparedNativeEvents, prepare_source_observer
        from behave.sim.rtl.shared_frontend import parse_observation_source
        from behave.sim.rtl.source_goals import manifest_of

        from .test_rtl_observation_frontend import _installed
        from .test_rtl_tool_isolation import _needs_namespace

        _needs_namespace()
        # The public observer has migrated to source occurrences. Compare its
        # entire binding with a separate direct source preparation; the old
        # compiled-action projection below remains a diagnostic, not authority.
        direct = tmp_path / "direct-source"
        direct.mkdir()
        (direct / "design.sv").write_text(source)
        (cst, ast), sensitivity = parse_observation_source(direct, "M", run=_installed)
        public_plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity,
                                  enumerate_iterations=False)
        # Match the production cocotb launcher, which constructs Vtop("").
        public_collector = prepare_declared_native_events(public_plan, filename=filename,
            toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=1000,
            scope_root="")
        prepared = prepare_source_observer(source, top="M", execution_sha256="2" * 64,
            callback_limit=1000, catalogue=manifest_of(public_plan.declarations, filename=filename),
            scope_root="")
        assert type(prepared) is PreparedNativeEvents, prepared.as_dict()
        assert prepared.plan.bindings == public_collector.bindings
        # Preserve the explicit process/event control, including old-value NBA
        # reads and coincident reset. Only its body receives callbacks.
        assert "initial forever" not in prepared.source
        collector, native = prepared.plan, prepared.source
    sites = sites_of(original)
    traces = {}
    for name, text in (("original", source), ("native", native)):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "design.sv").write_text(text)
        if transport in {"cocotb", "judge"}:
            from behave.sim.harness import generate_makefile

            (directory / "rtl.sv").write_text(text)
            (directory / "collector.cpp").write_text(collector.collector_source())
            (directory / "collector-plan.json").write_text(json.dumps(collector.as_dict()))
            (directory / "collector_tb.py").write_bytes((fixtures / "collector_tb.py").read_bytes())
            package_file = Path(behave.__file__).resolve()
            (directory / "expected-package.txt").write_text(
                "/runtime/behave/behave/__init__.py" if transport == "judge" else str(package_file))
            environment = {**os.environ, "PYTHONPATH": str(package_file.parent.parent),
                           "PYGPI_PYTHON_BIN": sys.executable, "COCOTB_RANDOM_SEED": "20260909"}
            generate_makefile(directory, "M", tb_module="collector_tb", coverage=False,
                              native_events=transport == "judge")
            if transport == "judge":
                from behave.sim.rtl.tool_isolation import run_verilator_workspace
                from behave.sim.rtl.tool_protocol import command_result

                with (directory / "Makefile").open("a") as makefile:
                    makefile.write("\nEXTRA_ARGS += -Wno-WIDTH -Wno-fatal\n")
                built, replayed = run_verilator_workspace(
                    directory, top_module="M", test_module="collector_tb",
                    role="native-observer-oracle", outputs=("native-trace.json",),
                    compile_timeout_s=90, simulate_timeout_s=30)
                (directory / "tool-build.json").write_text(json.dumps(built.as_dict(), indent=2))
                (directory / "tool-replay.json").write_text(json.dumps(replayed.as_dict(), indent=2))
                assert all(row["returncode"] == 0 for row in built.commands), built.as_dict()
                assert command_result(replayed, "native-observer-oracle:simulate")["returncode"] == 0, replayed.as_dict()
                assert (directory / "native-trace.json").exists(), replayed.as_dict()
            else:
                # This fixed-fixture transport deliberately runs outside the
                # tool judge. Use the same CPU-bounded main at its host path;
                # /runtime is a mount owned by the isolated production build.
                main = package_file.parent / "sim" / "rtl" / "cocotb_main.cpp"
                makefile = directory / "Makefile"
                makefile.write_text("EXTRA_ARGS += collector.cpp\n" + makefile.read_text().replace(
                    "/runtime/cocotb-main.cpp", str(main)))
                _run(directory, "make", "-j", "1",
                     "PYTHON_BIN=" + sys.executable, env=environment, timeout_s=90)
            recorded = json.loads((directory / "native-trace.json").read_text())
            traces[name] = {phase: (q, seen, frozenset(hits))
                            for phase, (q, seen, hits) in recorded.items()}
            continue
        probe = "collector_probe.cpp" if transport == "bounded" else "probe.cpp"
        (directory / "probe.cpp").write_bytes((fixtures / probe).read_bytes())
        sources, arguments = [], []
        if transport == "bounded":
            (directory / "collector.cpp").write_text(collector.collector_source())
            sources = ["collector.cpp"]
            arguments = [collector.identity, str(collector.word_count)]
        _run(directory, "verilator", "--cc", "--exe", "--build", "-j", "1", "-Wno-fatal",
             "--top-module", "M", "--Mdir", "build", "design.sv", "probe.cpp", *sources,
             timeout_s=90)
        result = subprocess.run([str(directory / "build/VM"), *arguments], cwd=directory, text=True,
                                capture_output=True, timeout=10, check=True)
        trace = {}
        for line in result.stdout.splitlines():
            assert line.startswith("ROW "), line
            _, phase, q, seen, *hits = line.split()
            assert phase not in trace
            trace[phase] = (int(q), int(seen), frozenset(map(int, hits)))
        traces[name] = trace

    states = {
        "init": (7, 0), "reset": (0, 0), "held-reset": (0, 0), "release": (0, 0),
        "clock-1": (5, 9), "inactive-1": (5, 9), "clock-2": (7, 6),
        "inactive-2": (7, 6), "clock-3": (9, 6), "inactive-3": (9, 6),
        "coincident-reset": (0, 9),
    }

    def select(role, text):
        chosen = [e.ordinal for e in plan.events if e.role == role
                  and source.encode()[e.start:e.end].decode().strip() == text]
        assert len(chosen) == 1, (role, text, chosen)
        return chosen[0]

    p_q, p_seen = [e.ordinal for e in plan.events if e.role == "process"]
    no_match, = [e.ordinal for e in plan.events if e.role == "case-no-match"]
    outer_else, = [e.ordinal for e in plan.events if e.role == "if-false"
                   and source.encode()[e.start:e.end].decode().startswith("if (take)")]
    case_entry, = [e.ordinal for e in plan.events if e.role == "if-false"
                   and source.encode()[e.start:e.end].decode().startswith("case (sel)")]
    branch = select("if-true", "q <= 5;")
    reset = select("if-true", "q <= 0;")
    q_reset = {p_q, reset}
    q_five = {p_q, outer_else, branch}
    q_two = {p_q, outer_else, select("if-false", "q <= q + 2;")}
    hits = dict.fromkeys(states, frozenset())
    hits.update({
        "reset": q_reset,
        "clock-1": q_five | {p_seen, select("if-true", "seen <= 9;")},
        "clock-2": q_two | {p_seen, case_entry, select("case-arm", "seen <= q + 1;")},
        "clock-3": q_two | {p_seen, case_entry, no_match},
        "coincident-reset": q_reset | {p_seen, case_entry, select("case-arm", "seen <= q;")},
    })
    assert len(plan.events) == 12
    assert {phase: row[:2] for phase, row in traces["original"].items()} == states
    assert all(not row[2] for row in traces["original"].values())
    if transport == "judge":
        _check_judged_source_fixture(source, public_collector, traces["native"])
        legacy = {(event.role, event.start, event.end, event.choice): event.ordinal for event in plan.events}
        projection = {event.ordinal: legacy[key] for event in public_plan.events
                      if (key := (event.role, event.start, event.end, event.choice)) in legacy}
        traces["native"] = {phase: (q, seen, frozenset(projection[event] for event in observed
                                                      if event in projection))
                            for phase, (q, seen, observed) in traces["native"].items()}

    def check(trace):
        assert set(trace) == set(states)
        for phase, (q, seen, observed) in trace.items():
            assert (q, seen) == states[phase]
            assert observed == hits[phase], phase

    check(traces["native"])
    # Independently evaluate the original catalogue on literal event-time
    # valuations. In particular, clock-1 reads clk=1 and OLD q=0, not either
    # endpoint snapshot; coincident reset reads OLD q=9 in the second process.
    # This fixture has exactly one generated predicate, q==0. Validate its
    # literal fixture wiring rather than guessing meaning from a generated name.
    generated, = {rtlil_name(s.signal) for s in sites if s.kind == "case-arm" and s.signal.startswith("$")}
    cell, = [(kind, body) for kind, body in re.findall(
        r"^  cell (\S+) [^\n]+\n(.*?)^  end$", original, re.MULTILINE | re.DOTALL)
        if f"    connect \\Y {generated}\n" in body]
    assert cell[0] == "$eq"
    assert "    connect \\A \\q\n" in cell[1] and "    connect \\B 0\n" in cell[1]
    event_values = {
        "reset": (0, 1, 7, 3, {("rising", "rst")}),
        "clock-1": (1, 0, 0, 3, {("rising", "clk")}),
        "clock-2": (1, 0, 5, 1, {("rising", "clk")}),
        "clock-3": (1, 0, 7, 3, {("rising", "clk")}),
        "coincident-reset": (1, 1, 9, 0, {("rising", "rst"), ("rising", "clk")}),
    }
    expected_occurrences, expected_goals = {}, {}
    for phase in states:
        clk, rst, old_q, sel, edges = event_values.get(phase, (0, 0, 0, 0, set()))
        values = {"clk": clk, "rst": rst, "q": old_q, "sel": sel,
                  "take": int(clk and old_q == 0), generated: int(old_q == 0)}
        active = []
        for site in sites:
            goals, issues = site_hits([site], values,
                                     widths={"clk": 1, "rst": 1, "q": 8, "sel": 2,
                                             "take": 1, generated: 1}, active_events=edges)
            assert not issues
            if goals:
                active.append(site)
        expected_occurrences[phase] = frozenset(occurrence_key_of(s) for s in active)
        expected_goals[phase] = frozenset(goal_id_of(s) for s in active)

    def check_projection(projector):
        assert {b.occurrence for b in projector.occurrences} == {occurrence_key_of(s) for s in sites}
        for phase, (_, _, observed) in traces["native"].items():
            projected = projector.decode(observed)
            assert projected.occurrences == expected_occurrences[phase], phase
            assert projected.goals == expected_goals[phase], phase

    check_projection(bound)
    # The same full-domain checker detects loss of an action or a wrong entry
    # binding even though the original and instrumented DUT outputs still agree.
    action, = [b for b in bound.occurrences if b.event == reset and b.occurrence.role == "entry"]
    for altered in (None, replace(action, event=p_q, ancestors=())):
        mutant = replace(bound, occurrences=tuple(
            b if b != action else altered for b in bound.occurrences
            if b != action or altered is not None))
        with pytest.raises(AssertionError):
            check_projection(mutant)
    for phase, add, remove in [("clock-1", set(), {branch}),
                               ("clock-1", {select("if-false", "q <= q + 2;")}, set()),
                               ("held-reset", {reset}, set())]:
        mutant = dict(traces["native"])
        q, seen, observed = mutant[phase]
        mutant[phase] = (q, seen, observed.difference(remove).union(add))
        with pytest.raises(AssertionError):
            check(mutant)


def _check_judged_source_fixture(source, collector, trace):
    """Literal source expectations, including net transients within one interval."""
    clocks = {"clock-1", "clock-2", "clock-3", "coincident-reset"}
    changes = set(trace) - {"held-reset", "release"}
    for phase, (_q, _seen, events) in trace.items():
        expected = set()

        def entry(text, expected=expected):
            expected.add(("location", source.index(text), "entry"))

        def choice(text, outcome, expected=expected):
            expected.add(("control-choice", source.index(text), outcome))

        if phase in changes:
            entry("take = clk && q == 0")
            clk = phase in clocks
            expected.add(("boolean-expression", source.index("clk &&"), "true" if clk else "false"))
            if clk:
                expected.add(("boolean-expression", source.index("q == 0;"), "false"))
                # At these edges the same source expression also sees q==0,
                # before / after the NBA respectively. Endpoint values alone
                # cannot provide the complete source-event history.
                if phase in {"clock-1", "coincident-reset"}:
                    expected.add(("boolean-expression", source.index("q == 0;"), "true"))
        if phase in clocks | {"reset"}:
            entry("if (rst)")
            reset = phase in {"reset", "coincident-reset"}
            choice("if (rst)", "true" if reset else "false")
            if reset:
                entry("q <= 0;")
            else:
                entry("if (take)")
                choice("if (take)", "true" if phase == "clock-1" else "false")
                entry("q <= 5;" if phase == "clock-1" else "q <= q + 2;")
        if phase in clocks:
            entry("if (q == 0)")
            choice("if (q == 0)", "true" if phase == "clock-1" else "false")
            if phase == "clock-1":
                entry("seen <= 9;")
            else:
                entry("case (sel)")
                outcome = {"clock-2": "arm_1", "clock-3": "no-match", "coincident-reset": "arm_0"}[phase]
                choice("case (sel)", outcome)
                if phase != "clock-3":
                    entry("seen <= q + 1;" if phase == "clock-2" else "seen <= q;")
        observed = collector.bindings.decode(events).occurrences
        assert {(key.path[1], key.path[2], key.role) for key in observed} == expected, phase
        assert len(observed) == len(expected), phase
