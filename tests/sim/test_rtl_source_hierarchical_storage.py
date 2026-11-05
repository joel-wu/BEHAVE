"""Cross-scope source references retain one physical storage owner in IR."""

from dataclasses import replace
from itertools import product

import pytest

from behave.sim.core.refusal import Refused

from .test_rtl_combinational_events import _trace
from .test_rtl_source_batch import _encoding, _lower, _run
from .test_rtl_source_locations import _collector

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


@pytest.mark.parametrize("declaration,write,read", [
    ("if(1) begin: bank logic [3:0] stored; end", "bank.stored", "bank.stored"),
    ("Bank bank();", "bank.stored", "bank.stored"),
])
def test_parent_reads_and_writes_the_child_cell(tmp_path, declaration, write, read):
    source = f"""module Bank; logic [3:0] stored; endmodule
    module M(input clk,input [3:0] a,output logic [3:0] q);
      {declaration}
      always @(posedge clk) {write}<=a;
      always @(posedge clk) q<={read};
    endmodule"""
    batch, _ = _lower(tmp_path, source)
    enc = _encoding(batch)
    for a, old, active in product((0, 7, 15), (0, 3, 12), product((False, True), repeat=2)):
        post, _ = _run(batch, {"a": a, "stored": old, "q": 9}, active, enc)
        assert post == {"stored": a if active[0] else old, "q": old if active[1] else 9}
    assert len([field for _, field in batch.storage if field.name == "stored"]) == 1


def test_child_read_of_top_input_is_not_an_independent_state_cell(tmp_path):
    source = """module Child(input clk,output logic [3:0] q);
      always @(posedge clk) q<=M.a;
    endmodule
    module M(input clk,input [3:0] a,output wire [3:0] q);
      Child child(clk,q);
    endmodule"""
    batch, _ = _lower(tmp_path, source)
    enc = _encoding(batch)
    for a in range(16):
        post, _ = _run(batch, {"a": a, "q": 9}, (True,), enc)
        assert post == {"q": a}
    assert {field.name for name, field in batch.storage if name in batch.procedure.inputs} == {"a"}


def test_hierarchical_storage_does_not_alias_a_same_named_local(tmp_path):
    source = """module Bank; logic [3:0] stored; endmodule
    module M(input clk,input [3:0] a,output logic [3:0] q);
      Bank bank();
      function automatic [3:0] read_bank(input [3:0] stored);
        return bank.stored+stored;
      endfunction
      always @(posedge clk) begin bank.stored<=a; q<=read_bank(a); end
    endmodule"""
    batch, _ = _lower(tmp_path, source)
    enc = _encoding(batch)
    for a, old in product(range(16), (0, 3, 12)):
        post, _ = _run(batch, {"a": a, "stored": old, "q": 9}, (True,), enc)
        assert post == {"stored": a, "q": (old+a) % 16}


def test_parent_and_child_writes_do_not_hide_a_shared_cell_race(tmp_path):
    source = """module Bank(input clk); logic [3:0] stored;
      always @(posedge clk) stored<=stored+1;
    endmodule
    module M(input clk,input [3:0] a);
      Bank bank(clk);
      always @(posedge clk) bank.stored<=a;
    endmodule"""
    with pytest.raises(Refused, match="rtl-source-batch-order-unproved"):
        _lower(tmp_path, source)


@pytest.mark.eda("verilator", "make")
def test_hierarchical_history_matches_native_values_and_each_occurrence(tmp_path):
    source = """module Bank; logic [3:0] stored=0; endmodule
    module M(input clk,input [3:0] a,output logic [3:0] q);
      Bank bank();
      initial q=0;
      always @(posedge clk) bank.stored<=a;
      always @(posedge clk) q<=bank.stored;
    endmodule"""
    batch, plan = _lower(tmp_path, source)
    enc = _encoding(batch)
    collector, _ = _collector(plan)
    assert collector.word_count == 1
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    driver = """
      const unsigned values[]={0,5,9,3,0};
      for(unsigned i=0;i<10;i++) {
        if(behave_native_events_begin_v1(i)) return 1;
        dut.a=values[i/2]; dut.clk=i%2; dut.eval();
        uint64_t bits=0,calls=0;
        if(behave_native_events_end_v1(i,&bits,1,&calls)) return 2;
        std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
      }
    """
    original = _trace(tmp_path / "original", source, flat, driver)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert [row[0] for row in native] == [row[0] for row in original]
    state = {"stored": 0, "q": 0}
    for step, (value, bits, _) in enumerate(native):
        state, hits = _run(batch, {**state, "a": (0, 5, 9, 3, 0)[step//2]},
                           (bool(step % 2),)*2, enc)
        seen = {index for index in range(collector.bindings.event_count) if bits >> index & 1}
        assert collector.bindings.decode(seen).occurrences == hits
        assert value == state["q"]
