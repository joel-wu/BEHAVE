"""One frontend profile for initialized storage, with normal driver checks intact."""

import subprocess
from pathlib import Path

import pytest

from behave.sim.rtl.elaboration import elaboration_script
from behave.sim.rtl.native_events import prepare_native_events
from behave.sim.rtl.shared_frontend import SLANG_FLAGS, compile_source, parse_source
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_source_plan import _compile

pytestmark = pytest.mark.eda("slang")


def _run(where, executable, *args):
    return subprocess.run((Path(executable).name, *args), cwd=where,
                          check=False).returncode == 0


@pytest.mark.parametrize("observation", [False, True])
def test_package_without_timescale_uses_simulator_default(tmp_path, observation):
    from behave.sim.rtl.shared_frontend import parse_observation_source

    from .test_rtl_observation_frontend import _installed

    source = """package P; localparam int K=3; endpackage
      `timescale 10ns/1ns
      module M(input [3:0] a,output [3:0] q); assign q=a+P::K; endmodule
    """
    path = tmp_path / "design.sv"
    path.write_text(source)
    parsed = (parse_observation_source(tmp_path, "M", run=_installed) if observation
              else parse_source(tmp_path, "M", run=_run, typed=True))
    assert parsed is not None
    assert path.read_text() == source


@pytest.mark.parametrize("process", [
    "always_ff @(posedge clk) q<=a;", "always_comb q=a;",
])
def test_initial_driver_compatibility_preserves_typed_source(tmp_path, process):
    source = f"module M(input clk,a,output logic q); initial q=0; {process} endmodule"
    path = tmp_path / "design.sv"
    path.write_text(source)
    cst, ast = parse_source(tmp_path, "M", run=_run, typed=True)
    assert cst["syntaxTrees"] and ast
    assert path.read_text() == source


@pytest.mark.parametrize("processes", [
    "always_ff @(posedge clk) q<=a; always_ff @(posedge other) q<=b;",
    "always_comb q=a; always_comb q=b;",
])
def test_compatibility_does_not_disable_runtime_driver_conflicts(tmp_path, capfd, processes):
    source = f"module M(input clk,other,a,b,output logic q); initial q=0; {processes} endmodule"
    (tmp_path / "design.sv").write_text(source)
    assert parse_source(tmp_path, "M", run=_run, typed=True) is None
    assert "multiple-always-assigns" in capfd.readouterr().err


@pytest.mark.parametrize("local", [False, True])
def test_loop_driver_ownership_depends_on_declared_scope(tmp_path, capfd, local):
    declaration = "" if local else "integer i;"
    initializer = "int i=0" if local else "i=0"
    source = f"""module M(input clk, input [3:0] a,b, output logic [3:0] y,q);
        {declaration}
        always_comb for({initializer};i<4;i=i+1) y[i]=a[i];
        always_ff @(posedge clk) for({initializer};i<4;i=i+1) q[i]<=b[i];
    endmodule"""
    path = tmp_path / "design.sv"
    path.write_text(source)
    result = parse_source(tmp_path, "M", run=_run, typed=True)
    assert (result is not None) == local
    if not local:
        assert "multiple-always-assigns" in capfd.readouterr().err
    assert path.read_text() == source


@pytest.mark.parametrize("automatic", [False, True])
def test_function_local_driver_ownership_keeps_declared_lifetime(tmp_path, capfd, automatic):
    lifetime = "automatic" if automatic else ""
    source = f"""module M(input clk, input [3:0] a,b, output logic [3:0] y,q);
        function {lifetime} [3:0] f(input [3:0] value);
            reg [3:0] temporary;
            begin temporary=value+1; f=temporary; end
        endfunction
        always_comb y=f(a);
        always_ff @(posedge clk) q<=f(b);
    endmodule"""
    path = tmp_path / "design.sv"
    path.write_text(source)
    result = parse_source(tmp_path, "M", run=_run, typed=True)
    assert (result is not None) == automatic
    if not automatic:
        assert "multiple-always-assigns" in capfd.readouterr().err
    assert path.read_text() == source


@pytest.mark.parametrize("kind", ["logic", "wire"])
def test_duplicate_continuous_driver_legality_depends_on_net_or_variable(tmp_path, capfd, kind):
    source = f"""module M(input [3:0] a,output [3:0] y);
        {kind} [3:0] x[0:1];
        assign x[0]=a;
        assign x[0]=a;
        assign y=x[0];
    endmodule"""
    path = tmp_path / "design.sv"
    path.write_text(source)
    result = parse_source(tmp_path, "M", run=_run, typed=True)
    assert (result is not None) == (kind == "wire")
    if kind == "logic":
        assert "multiple-cont-assigns" in capfd.readouterr().err
    assert path.read_text() == source


@pytest.mark.eda("yosys")
@pytest.mark.parametrize("prefix", ["", "`timescale 1ns/1ps\n"])
def test_deferred_typed_parse_uses_the_same_compatibility_profile(tmp_path, prefix):
    source = prefix + """module M(input clk,a,output logic q);
        logic memory[0:3];
        initial begin
            for(integer i=0;i<4;i=i+1) memory[i]=0;
        end
        always_ff @(posedge clk) memory[0]<=a;
        assign q=memory[0];
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    (tmp_path / "run.ys").write_text(elaboration_script("M") + "write_rtlil design.rtlil\n")
    commands = []

    def run(where, *argv):
        commands.append(argv)
        return _run(where, *argv)

    assert compile_source(tmp_path, "M", "run.ys", run=run)
    parses = [argv for argv in commands if Path(argv[0]).name == "slang"]
    assert len(parses) == 2
    assert "--parse-only" in parses[0] and "--ast-json" in parses[1]
    assert all(all(flag in argv for flag in SLANG_FLAGS) for argv in parses)
    assert (tmp_path / "design.sv").read_text() == source


@pytest.mark.eda("yosys", "verilator", "make")
def test_initialized_ff_keeps_native_outputs_and_event_hits(tmp_path):
    source = """`timescale 1ns/1ps
    module M(input clk,a,output logic [3:0] q);
        initial q=5;
        always_ff @(posedge clk) if(a) q<=q+1; else q<=q-2;
    endmodule"""
    (tmp_path / "design.sv").write_text(source)
    cst, ast = parse_source(tmp_path, "M", run=_run, typed=True)
    plan = plan_source(source, cst, semantic_document=ast)
    collector = prepare_native_events(
        plan, _compile(tmp_path / "original", source),
        _compile(tmp_path / "annotated", plan.compiler.text), filename="design.sv",
        toolchain_sha256="1" * 64, execution_sha256="2" * 64, callback_limit=100)
    driver = '''
    for(unsigned i=0;i<4;++i) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.clk=i%2; dut.a=i<2; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    }
'''
    plain = _trace(tmp_path / "plain", source, collector, driver)
    native = _trace(tmp_path / "native", plan.native().text, collector, driver, native=True)
    assert [q for q, _, _ in plain] == [5, 6, 6, 4]
    assert [q for q, _, _ in native] == [q for q, _, _ in plain]
    # Typed source locations are observed as well as the branch/control entry.
    # Assert event meanings, not ordinals from the older control-only plan.
    from collections import Counter

    observed = [Counter(plan.events[item.source_event].role
                        for index, item in enumerate(plan.invocations) if bits >> index & 1)
                for _, bits, _ in native]
    assert observed == [Counter(), Counter({"process": 1, "location": 2, "if-true": 1}),
                        Counter(), Counter({"process": 1, "location": 2, "if-false": 1})]
    assert [calls for _, _, calls in native] == [0, 4, 0, 4]
