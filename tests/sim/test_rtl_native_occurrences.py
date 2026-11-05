"""Native occurrence hits do not require a static loop-iteration certificate."""

import pytest

from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_encoding import SourceEncoding
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_combinational_events import _trace
from .test_rtl_observation_frontend import _installed
from .test_rtl_source_locations import _collector, _occurrence_meanings

pytestmark = pytest.mark.eda("slang", "verilator", "make")


def _plan(path, source):
    path.mkdir()
    (path / "design.sv").write_text(source)
    parsed = parse_observation_source(path, "M", run=_installed)
    assert parsed is not None
    (cst, ast), sensitivity = parsed
    plan = plan_source(source, cst, semantic_document=ast,
                       sensitivity_document=sensitivity, enumerate_iterations=False)
    assert not plan.native_issues
    assert not plan.loops
    assert [(item.source_event, item.iterations) for item in plan.invocations] == [
        (i, ()) for i in range(len(plan.events))]
    return plan


@pytest.mark.parametrize("expression", ["a==b", "(a==b)", "((a==b))"])
def test_net_location_uses_its_declared_owner_not_parenthesis_extent(tmp_path, expression):
    source = f"module M(input logic a,b,output wire q); wire t={expression}; assign q=t; endmodule"
    plan = _plan(tmp_path / "parse", source)
    collector, manifest = _collector(plan)
    assert len([goal for goal in manifest.values() if goal.family == "location"]) == 2
    driver = """
      for(unsigned i=0;i<4;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=i&1; dut.b=(i>>1)&1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    plain = _trace(tmp_path / "plain", source, collector, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert [row[0] for row in native] == [row[0] for row in plain] == [1, 0, 0, 1]
    seen = {i for _, bits, _ in native for i in range(collector.bindings.event_count) if bits >> i & 1}
    assert collector.bindings.decode(seen).goals == set(manifest)


LOOPS = """module M(input logic clk,input logic [3:0] n,input logic [2:0] mode,
                  output logic [31:0] q);
  function automatic [31:0] f(input logic [3:0] count,input logic [2:0] which);
    integer i,j,m; logic [3:0] k;
    begin
      f=0;
      case(which)
        0: begin i=0; while(i<count) begin f=f+i; i=i+1; end end
        1: for(k=1;k<=count;k=k+1) begin
             if(k==3) continue; f=f+k; if(k==6) break;
           end
        2: for(i=0;i<count;i=i+1) for(j=0;j<i;j=j+1) f=f+1;
        3: repeat(count) f=f+1;
        4: begin i=0; do begin f=f+1; i=i+1; end while(i<count); end
        5: for(i=0;i<count;i=i+1) begin if(i==2) return f; f=f+1; end
        6: for(i=0;i<3;i=i+1) begin
             m=count>>i; for(j=0;j<m;j=j+1) f=f+1;
           end
        7: forever begin f=f+1; if(f>=count) break; end
      endcase
    end
  endfunction
  always_ff @(posedge clk) q<=f(n,mode);
endmodule"""


def _expected(mode, count):
    return (sum(range(count)), sum(i for i in range(1, min(count, 6) + 1) if i != 3),
            count * (count - 1) // 2, count, max(count, 1), min(count, 2),
            sum(count >> i for i in range(3)), max(count, 1))[mode]


def test_dynamic_loop_hits_outputs_and_early_exits(tmp_path, monkeypatch):
    # Observation must not run a static proof and then swallow its refusal.
    def unexpected(*args, **kwargs):
        pytest.fail("native observation requested a static iteration proof")

    monkeypatch.setattr("behave.sim.rtl.source_loops.static_loop", unexpected)
    plan = _plan(tmp_path / "parse", LOOPS)
    collector, _ = _collector(plan)
    words = collector.word_count
    cases = [(mode, count) for mode in range(8) for count in (0, 1, 4, 7)]
    driver = f"""
      dut.clk=0; dut.n=0; dut.mode=0; dut.eval();
      const unsigned counts[]={{0,1,4,7}};
      unsigned sequence=0;
      for(unsigned mode=0;mode<8;mode++) for(unsigned count:counts) {{
        if(behave_native_events_begin_v1(sequence++)) return 1;
        dut.clk=0; dut.n=count; dut.mode=mode; dut.eval();
        dut.clk=1; dut.eval();
        uint64_t bits[{words}]={{}},calls=0;
        if(behave_native_events_end_v1(sequence-1,bits,{words},&calls)) return 2;
        std::cout<<unsigned(dut.q);
        for(unsigned w=0;w<{words};w++) std::cout<<" "<<bits[w];
        std::cout<<" "<<calls<<"\\n";
      }}
    """
    plain = _trace(tmp_path / "plain", LOOPS, collector, driver, event_words=words)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver,
                    native=True, event_words=words)
    assert [row[0] for row in native] == [row[0] for row in plain] == [
        _expected(*case) for case in cases]
    meanings = _occurrence_meanings(LOOPS, plan)
    loop_texts = {value[2] for value in meanings.values() if value[1] == "loop-activation"}
    assert len(loop_texts) == 10  # two nested pairs, plus six single loops
    for (mode, count), (_, bits, _) in zip(cases, native, strict=True):
        seen = {i for i in range(collector.bindings.event_count) if bits >> i & 1}
        hits = collector.bindings.decode(seen).occurrences
        loop_hits = {(meanings[key][2], meanings[key][3]) for key in hits
                     if meanings[key][1] == "loop-activation"}
        expected = set()
        for text in loop_texts:
            if text.startswith("while(") and mode == 0 or text.startswith("for(k=") and mode == 1:
                expected.add((text, "nonzero" if count else "zero"))
            elif text.startswith("for(i=0;i<count"):
                selected = 5 if "return f" in text else 2
                if mode == selected:
                    expected.add((text, "nonzero" if count else "zero"))
            elif text.startswith("for(j=0;j<i;") and mode == 2 and count:
                expected.add((text, "zero"))
                if count > 1:
                    expected.add((text, "nonzero"))
            elif text.startswith("repeat(") and mode == 3:
                expected.add((text, "nonzero" if count else "zero"))
            elif text.startswith("do ") and mode == 4 or text.startswith("forever ") and mode == 7:
                expected.add((text, "nonzero"))
            elif text.startswith("for(i=0;i<3;") and mode == 6:
                expected.add((text, "nonzero"))
            elif text.startswith("for(j=0;j<m;") and mode == 6:
                expected.update((text, "nonzero" if count >> i else "zero") for i in range(3))
        assert loop_hits == expected, (mode, count, loop_hits, expected)
    with pytest.raises(ValueError, match="not a finite iteration encoding"):
        plan.require_compiler_binding()
    encoded = SourceEncoding(plan, (None,), (0,))
    assert encoded.predicate_for(0, None).kind == "rtl-source-iterations-unencoded"


def test_source_callback_count_does_not_scale_with_literal_trip_count(tmp_path):
    def source(bound):
        return ("module M(input logic clk,a,output logic q); integer i; "
                f"always_ff @(posedge clk) for(i=0;i<{bound};i=i+1) q<=a; endmodule")

    small = _plan(tmp_path / "small", source(3))
    large = _plan(tmp_path / "large", source(30000))
    assert len(small.invocations) == len(large.invocations)
    assert len(small.native().text) + 4 == len(large.native().text)


def test_forward_variable_reference_is_one_shared_language_profile(tmp_path):
    source = """module M(input logic clk,a,output wire q);
      assign q=r; logic r; always_ff @(posedge clk) r<=a; endmodule"""
    _collector(_plan(tmp_path / "parse", source))


@pytest.mark.parametrize("assignment", ["=", "<="])
def test_looped_packed_writes_preserve_every_lane(tmp_path, assignment):
    source = f"""module M(input logic clk, input logic [31:0] a,
                         output logic [31:0] q);
      integer k;
      always @(posedge clk)
        for(k=0;k<4;k=k+1) q[k*8 +: 8] {assignment} a[k*8 +: 8];
    endmodule"""
    plan = _plan(tmp_path / "parse", source)
    collector, _ = _collector(plan)
    driver = """
      dut.clk=0; dut.a=0; dut.eval();
      for(unsigned i=0;i<5;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.clk=0; dut.a=0x12345678U+i; dut.eval();
        dut.clk=1; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<uint32_t(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    plain = _trace(tmp_path / "plain", source, collector, driver)
    flags_only = _trace(tmp_path / "flags", source, collector, driver, native=True)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver,
                    native=True)
    expected = [0x12345678+i for i in range(5)]
    assert [row[0] for row in plain] == [row[0] for row in flags_only] == expected
    assert [row[0] for row in native] == expected
