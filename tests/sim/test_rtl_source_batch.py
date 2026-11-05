"""Source active/NBA batches: shared storage, deferred commit and real hits."""

from dataclasses import replace
from itertools import product

import pytest

from behave.sim.symbolic import encoder, interp, ranges
from behave.sim.core.refusal import Refused
from behave.sim.rtl.source_batch import lower_source_batch
from behave.sim.rtl.source_plan import plan_source
from behave.sim.rtl.source_storage import storage_bindings

from .test_rtl_combinational_events import _trace
from .test_rtl_source_locations import _collector, _observation_documents

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


def _lower(tmp_path, source):
    cst, ast = _observation_documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    batch = lower_source_batch(source, ast, plan, filename="design.sv")
    return batch, plan


def _encoding(batch):
    env = ranges.fixpoint(batch.procedure, {**batch.procedure.state_ranges, **batch.input_ranges})
    enc = encoder.encode_step(batch.procedure, env=env)
    assert {mark for mark, _, _ in batch.occurrences} <= enc.hits.keys()
    return enc


def _run(batch, values, active, enc):
    import z3

    proc = batch.procedure
    cells = {name: values[field.name] for name, field in batch.storage}
    inputs = {name: cells[name] for name in proc.inputs if name not in batch.active}
    inputs.update(zip(batch.active, active, strict=True))
    state = {name: cells[name] for name in proc.state}
    post, _, hits, error = interp.run_step(proc, inputs, state=state)
    assert error == ""
    solver = z3.Solver()
    solver.add(enc.constraint, *(enc.pre[name] == value for name, value in {**state, **inputs}.items()))
    assert solver.check() == z3.sat
    model = solver.model()
    assert hits == {mark for mark, term in enc.hits.items() if z3.is_true(model.eval(term))}
    for name, value in post.items():
        actual = model.eval(enc.post[name], model_completion=True)
        assert actual.as_long() == value % (1 << actual.size())
    return ({field.name: post[name] for name, field in batch.storage if name in post},
            {occurrence for mark, occurrence, _ in batch.occurrences if mark in hits})


def test_all_clocked_processes_read_before_any_deferred_commit(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q,r);
      always @(posedge clk) q<=a;
      always @(posedge clk) if(q[0]) r<=q; else r<=0;
    endmodule"""
    batch, _ = _lower(tmp_path, source)
    enc = _encoding(batch)
    for a, q, active in product((0, 5, 14), (0, 3, 8), product((False, True), repeat=2)):
        post, hits = _run(batch, {"a": a, "q": q, "r": 7}, active, enc)
        assert post == {"q": a if active[0] else q,
                        "r": (q if q & 1 else 0) if active[1] else 7}
        if not any(active):
            assert not hits
    assert {field.name for name, field in batch.storage if name in batch.procedure.inputs} == {"a"}
    assert {field.name for name, field in batch.storage if name in batch.procedure.state} == {"q", "r"}


def test_deferred_swap_and_multiple_writes_keep_old_source_reads(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q,r);
      always @(posedge clk) begin q<=r; if(a[0]) q<=a; end
      always @(posedge clk) r<=q;
    endmodule"""
    batch, _ = _lower(tmp_path, source)
    enc = _encoding(batch)
    for a in range(16):
        post, _ = _run(batch, {"a": a, "q": 3, "r": 12}, (True, True), enc)
        assert post == {"q": a if a & 1 else 12, "r": 3}


def test_batch_state_chaining_does_not_carry_inputs_into_the_next_event(tmp_path):
    import z3

    source = """module M(input clk,input [3:0] a,output reg [3:0] q,r);
      always @(posedge clk) q<=a;
      always @(posedge clk) r<=q;
    endmodule"""
    batch, _ = _lower(tmp_path, source)
    proc = batch.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **batch.input_ranges})
    first = encoder.encode_step(proc, env=env, tag="first")
    second = encoder.encode_step(proc, pre=first.state_post, env=env, tag="second")
    names = {field.name: name for name, field in batch.storage}
    assert set(first.state_post) == set(proc.state)
    assert set(first.state_post).isdisjoint(proc.inputs)
    solver = z3.Solver()
    solver.add(first.constraint, second.constraint,
               first.pre[names["q"]] == 0, first.pre[names["r"]] == 0,
               first.pre[names["a"]] == 5, second.pre[names["a"]] == 9,
               *(enc.pre[flag] == 1 for enc in (first, second) for flag in batch.active))
    assert solver.check() == z3.sat
    model = solver.model()
    assert model.eval(first.state_post[names["q"]]).as_long() == 5
    assert model.eval(first.state_post[names["r"]]).as_long() == 0
    assert model.eval(second.state_post[names["q"]]).as_long() == 9
    assert model.eval(second.state_post[names["r"]]).as_long() == 5


def test_history_dependent_safety_uses_the_same_batch_relation(tmp_path):
    import z3

    source = """module M(input clk,output reg [1:0] q,output reg r);
      always @(posedge clk) q<=q+2'd1;
      always @(posedge clk) assert(q<2'd3) r<=0; else r<=1;
    endmodule"""
    batch, _ = _lower(tmp_path, source)
    proc = batch.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **batch.input_ranges})
    safety, = (mark for mark, occurrence, _ in batch.occurrences
               if occurrence.path[1] == "artifact-failure")
    names = {field.name: name for name, field in batch.storage}
    solver, prefix, pre = z3.Solver(), [], None
    for step in range(4):
        enc = encoder.encode_step(proc, pre=pre, env=env, tag=f"step{step}")
        solver.add(enc.constraint, *(enc.pre[flag] == 1 for flag in batch.active))
        if step == 0:
            solver.add(enc.pre[names["q"]] == 0, enc.pre[names["r"]] == 0)
        prefix.append(enc.hits[safety])
        solver.push()
        solver.add(z3.Or(prefix))
        assert solver.check() == (z3.unsat if step < 3 else z3.sat)
        solver.pop()
        pre = enc.state_post
    state = {"q": 0, "r": 0}
    concrete_encoding = _encoding(batch)
    for step in range(4):
        state, hits = _run(batch, state, (True, True), concrete_encoding)
        assert any(hit.path[1] == "artifact-failure" for hit in hits) == (step == 3)


@pytest.mark.parametrize("body", [
    "always @(posedge clk) q=a; always @(posedge clk) r=q;",
    "always @(posedge clk) q<=a; always @(posedge clk) q<=r;",
    ("function automatic [3:0] f(input [3:0] v); q=v; return v; endfunction "
     "always @(posedge clk) r=f(a); always @(posedge clk) if(q[0]) r<=0;"),
])
def test_order_dependent_batches_do_not_invent_a_process_order(tmp_path, body):
    source = f"module M(input clk,input [3:0] a,output reg [3:0] q,r); {body} endmodule"
    with pytest.raises(Refused, match="rtl-source-batch-order-unproved"):
        _lower(tmp_path, source)


def test_shared_module_specializations_have_distinct_storage(tmp_path):
    source = """module Leaf(input clk,input [3:0] a,output reg [3:0] q,r);
      always @(posedge clk) q<=a;
      always @(posedge clk) r<=q;
    endmodule
    module M(input clk,input [3:0] a,output wire [3:0] q0,r0,q1,r1);
      Leaf one(clk,a,q0,r0), two(clk,a,q1,r1);
    endmodule"""
    cst, ast = _observation_documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    bindings = storage_bindings(ast)
    batch = lower_source_batch(source, ast, plan, filename="design.sv")
    first, second, third, fourth = batch.processes
    assert {name for name, _ in first.storage} & {name for name, _ in second.storage}
    assert {name for name, _ in third.storage} & {name for name, _ in fourth.storage}
    assert not ({name for process in (first, second) for name, _ in process.storage} &
                {name for process in (third, fourth) for name, _ in process.storage})
    one = {symbol: bindings[symbol].key for process in (first, second) for _, symbol in process.storage}
    two = {symbol: bindings[symbol].key for process in (third, fourth) for _, symbol in process.storage}
    # The source compiler now elaborates physical instances, not just the first
    # specialization. Both invocation-local symbols and stable owner paths must
    # be distinct, while both processes in each instance still share q above.
    assert not one.keys() & two.keys()
    assert not set(one.values()) & set(two.values())
    again = tmp_path / "again"
    again.mkdir()
    _, ast2 = _observation_documents(again, source)
    plan2 = plan_source(source, cst, semantic_document=ast2)
    batch2 = lower_source_batch(source, ast2, plan2, filename="design.sv")
    assert batch.storage == batch2.storage
    assert batch.occurrences == batch2.occurrences


def test_canonical_only_document_cannot_guess_hierarchical_storage(tmp_path):
    from behave.sim.rtl.source_declarations import _nodes

    source = """module Leaf(input clk,output reg q);
      always @(posedge clk) q<=!q;
    endmodule
    module M(input clk,output wire q0,q1);
      Leaf one(clk,q0), two(clk,q1);
    endmodule"""
    cst, ast = _observation_documents(tmp_path, source)
    instances = {node["name"]: node for node in _nodes(ast["design"])
                 if node.get("kind") == "Instance"}
    # Model the old serializer's canonical-body link. Without actual resolved
    # instances a symbol address cannot identify a physical cross-scope cell.
    body = instances["one"]["body"]
    instances["two"]["body"] = f'{body["addr"]} {body["name"]}'
    plan = plan_source(source, cst, semantic_document=ast)
    with pytest.raises(Refused, match="rtl-source-storage-instance-unresolved"):
        lower_source_batch(source, ast, plan, filename="design.sv")


def test_static_function_storage_is_shared_between_calling_processes(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q,r);
      function automatic [3:0] f(input [3:0] v);
        static int count=0;
        count=count+1; return count+v;
      endfunction
      always @(posedge clk) q<=f(a);
      always @(posedge clk) r<=f(a);
    endmodule"""
    # Two calls write the same static helper cell; frame-local names must not
    # turn them into independent per-process counters and hide the race.
    with pytest.raises(Refused, match="rtl-source-batch-order-unproved"):
        _lower(tmp_path, source)


def test_package_storage_is_shared_not_rebound_to_each_process(tmp_path):
    source = """package P; logic [3:0] stored; endpackage
    module M(input clk,input [3:0] a,output reg [3:0] r);
      always @(posedge clk) P::stored<=a;
      always @(posedge clk) r<=P::stored;
    endmodule"""
    batch, _ = _lower(tmp_path, source)
    enc = _encoding(batch)
    post, _ = _run(batch, {"a": 9, "stored": 3, "r": 0}, (True, True), enc)
    assert post == {"stored": 9, "r": 3}
    assert len([field for _, field in batch.storage if field.name == "stored"]) == 1


def test_batch_builds_one_shared_symbol_and_declaration_index(tmp_path, monkeypatch):
    from behave.sim.rtl import source_ir, source_storage

    source = """module M(input clk,input [3:0] a,output reg [3:0] q,r);
      always @(posedge clk) q<=a;
      always @(posedge clk) r<=q;
    endmodule"""
    cst, ast = _observation_documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    calls = []
    index = source_ir.typed_symbols

    def counted(document):
        calls.append(document)
        return index(document)

    monkeypatch.setattr(source_ir, "typed_symbols", counted)
    monkeypatch.setattr(source_storage, "typed_symbols", counted)
    batch = lower_source_batch(source, ast, plan, filename="design.sv")
    assert len(batch.processes) == 2 and len(calls) == 1


@pytest.mark.eda("verilator", "make")
def test_batch_outputs_and_every_occurrence_match_native_multi_clock_history(tmp_path):
    source = """module M(input c0,c1,input [3:0] a,output reg [7:0] q,r);
      initial begin q=0; r=0; end
      function automatic [7:0] f(input [3:0] v);
        if(v[0]) return v+1;
        return v+2;
      endfunction
      always @(posedge c0) begin q<=f(a); if(a==3) q<=9; end
      always @(posedge c1) if(q[0]) r<=q; else r<=1;
    endmodule"""
    batch, plan = _lower(tmp_path, source)
    enc = _encoding(batch)
    collector, _ = _collector(plan)
    words = (collector.bindings.event_count + 63) // 64
    driver = f"""
      dut.c0=0;dut.c1=0;dut.eval();
      for(unsigned i=0;i<32;i++) {{
        if(behave_native_events_begin_v1(i)) return 1;
        dut.c0=0;dut.c1=0;dut.a=i%16;dut.eval();
        dut.c0=i&1;dut.c1=(i>>1)&1;dut.eval();
        uint64_t bits[{words}]={{}},calls=0;
        if(behave_native_events_end_v1(i,bits,{words},&calls)) return 2;
        std::cout<<(unsigned(dut.q)|(unsigned(dut.r)<<8));
        for(auto word:bits) std::cout<<" "<<word;
        std::cout<<" "<<calls<<"\\n";
      }}
    """
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver, event_words=words)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector,
                    driver, native=True, event_words=words)
    assert len(plain) == len(native) == 32
    state = {"q": 0, "r": 0}
    for i, (original, observed) in enumerate(zip(plain, native, strict=True)):
        state, hits = _run(batch, {"a": i % 16, **state}, (bool(i & 1), bool(i & 2)), enc)
        assert original[0] == observed[0] == state["q"] | (state["r"] << 8)
        seen = {event for event in range(collector.bindings.event_count) if observed[1] >> event & 1}
        assert collector.bindings.decode(seen).occurrences == hits
        assert original[1:] == (0, 0)
