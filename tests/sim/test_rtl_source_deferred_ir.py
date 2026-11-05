"""Source NBA summaries preserve every changed bit, not only the last value."""

from itertools import product

import pytest

from behave.sim.symbolic import encoder, interp, ir, ranges, typecheck as types
from behave.sim.rtl.source_deferred import DeferredWrite
from behave.sim.rtl.source_ir import _sequence

from .test_rtl_source_batch import _encoding, _lower

pytestmark = pytest.mark.solver


def _bits(term, width):
    import z3

    return (z3.Extract(width-1, 0, term) if term.size() >= width
            else z3.ZeroExt(width-term.size(), term))


@pytest.mark.parametrize('width', [1, 4, 32])
@pytest.mark.parametrize('signed', [False, True])
@pytest.mark.parametrize('mask_kind', ['implicit', 'explicit', 'partial', 'symbolic'])
def test_full_overwrite_drops_only_the_old_data_dependency(width, signed, mask_kind):
    write = DeferredWrite('q', 'data', 'valid', 'first', 'varied', 'mask', width, signed)
    mask = {'implicit': None, 'explicit': ir.Lit((1 << width) - 1),
            'partial': ir.Lit(0), 'symbolic': ir.Var('input_mask')}[mask_kind]
    commands = write.enqueue(ir.Var('value'), mask)
    update = next(command.expr for command in commands
                  if isinstance(command, ir.Assign) and command.var == write.data)
    assert (write.data not in ir._reads(update)) == (mask_kind in {'implicit', 'explicit'})
    # Only the payload representation changes; compare signed physical wraps
    # with the former masked expression over all bounded input combinations.
    old_mask = ir.Lit((1 << width) - 1) if mask is None else mask
    old = write.wrap(ir.Op('or', ir.Op('and', ir.Var(write.data), ir.Op('inv', old_mask)),
                           ir.Op('and', ir.Var('value'), old_mask)))
    inputs = ['data', 'value', 'input_mask']
    proc = ir.Procedure([], inputs, [], ['actual', 'expected'],
        _sequence([ir.Assign('actual', update), ir.Assign('expected', old)]),
        types=dict.fromkeys([*inputs, 'actual', 'expected'], types.INTEGER))
    domain = ranges.Range(-(1 << width), (1 << width) - 1)
    enc = encoder.encode_step(proc, env=ranges.fixpoint(proc, dict.fromkeys(inputs, domain)))
    import z3
    solver = z3.Solver()
    solver.add(enc.constraint, enc.post['actual'] != enc.post['expected'])
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("width", [1, 4, 32])
@pytest.mark.parametrize("count", [0, 1, 4])
@pytest.mark.parametrize("signed", [False, True])
@pytest.mark.parametrize("queue", ["generic", "whole", "partial"])
def test_summary_equals_ordered_commits_for_all_values_and_enabled_subsets(width, count, signed, queue):
    import z3

    partial = queue == "partial"
    write = DeferredWrite("q", "last", "valid", "first", "varied", "written", width, signed,
                          whole=queue == "whole")
    values = [f"v{index}" for index in range(count)]
    enables = [f"g{index}" for index in range(count)]
    masks = [f"m{index}" for index in range(count)] if partial else []
    inputs = ["precommit", *values, *enables, *masks]
    types_ = dict.fromkeys(["q", "changed", *write.registers, *inputs], types.INTEGER)
    types_.update(dict.fromkeys([write.valid, *enables], types.BOOLEAN))
    commands = list(write.initialize())
    for index, (value, enable) in enumerate(zip(values, enables, strict=True)):
        mask = ir.Var(masks[index]) if partial else None
        commands.append(ir.If(ir.Var(enable), _sequence(write.enqueue(ir.Var(value), mask)), ir.Skip()))
    # Other active commands may change the target after enqueueing. The queue
    # must compare against this value at commit, not the old pre-enqueue q.
    commands.append(ir.Assign("q", ir.Var("precommit")))
    commands.extend(write.commit("changed"))
    proc = ir.Procedure(["q"], inputs, list(write.registers), ["changed"], _sequence(commands), types=types_)
    domains = {name: ranges.Range(0, 1 if name in enables else (1 << width)-1)
               for name in ["q", *inputs]}
    enc = encoder.encode_step(proc, env=ranges.fixpoint(proc, domains))
    previous = _bits(enc.pre["precommit"], width)
    expected = z3.BitVecVal(0, width)
    for index, (value, enable) in enumerate(zip(values, enables, strict=True)):
        data = _bits(enc.pre[value], width)
        mask = _bits(enc.pre[masks[index]], width) if partial else z3.BitVecVal((1 << width)-1, width)
        enabled = enc.pre[enable] != 0  # Boundary Boolean values are encoded as one bit.
        expected = z3.If(enabled, expected | ((previous ^ data) & mask), expected)
        previous = z3.If(enabled, (previous & ~mask) | (data & mask), previous)
    solver = z3.Solver()
    solver.add(enc.constraint, z3.Or(_bits(enc.post["changed"], width) != expected,
                                     _bits(enc.post["q"], width) != previous))
    assert solver.check() == z3.unsat


def test_whole_word_queue_rejects_an_uncertified_partial_enqueue():
    write = DeferredWrite('q', 'data', 'valid', 'first', 'varied', 'mask', 4, whole=True)
    for mask in (ir.Lit(3), ir.Var('input_mask')):
        with pytest.raises(ValueError, match='whole-word deferred queue'):
            write.enqueue(ir.Var('value'), mask)


@pytest.mark.eda("slang")
@pytest.mark.parametrize("body,whole", [
    ("q<=a; q<=0;", True),
    ("if(a[0]) q<=a;", True),
    ("q<=a; q=a;", True),
    ("q[0]<=a[0]; q<=a;", False),
    ("q<=a; q[0]<=a[0];", False),
    ("if(a[0]) q<=a; else q[0]<=a[0];", False),
    ("q[a[1:0]]<=a[0];", False),
])
def test_source_queue_certifies_every_write_before_building_commit(tmp_path, body, whole):
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      always @(posedge clk) begin {body} end
    endmodule"""
    batch, _ = _lower(tmp_path, source)
    # The selected source storage still has normal commit/notification timing;
    # only its value expression can drop the obsolete precommit dependency.
    writes = [write for process in batch.processes for write in process.deferred]
    assert len(writes) == 1
    write, = writes
    assert write.whole is whole
    assert (write.target not in ir._reads(write.committed_value(ir.Var(write.target)))) is whole


@pytest.mark.eda("slang")
@pytest.mark.parametrize("body", ["q<=a; q<=0;", "if(a[0]) begin q<=a; q<=0; end", "q<=a; q=a;"])
def test_source_batch_reports_changes_at_commit_not_enqueue(tmp_path, body):
    import z3

    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      always @(posedge clk) begin {body} end
    endmodule"""
    batch, _ = _lower(tmp_path, source)
    enc = _encoding(batch)
    target, changed = batch.commit_changes[0]
    names = {field.name: name for name, field in batch.storage}
    assert target == names["q"]
    for a, old, active in product((0, 1, 6, 15), (0, 3, 12), (False, True)):
        inputs = {names["a"]: a, batch.active[0]: active}
        state = {target: old}
        post, outputs, _, error = interp.run_step(batch.procedure, inputs, state=state)
        assert error == ""
        mixed = body == "q<=a; q=a;"
        enabled = active and (not body.startswith("if") or bool(a & 1))
        previous = a if active and mixed else old
        expected = 0
        for value in ([a] if mixed else [a, 0]) if enabled else []:
            expected |= previous ^ value
            previous = value
        actual, = outputs.buffers[changed]
        assert actual == expected and post[target] == previous
        solver = z3.Solver()
        solver.add(enc.constraint, *(enc.pre[name] == value for name, value in {**state, **inputs}.items()),
                   z3.Or(enc.post[changed][0] != expected, enc.post[target] != previous))
        assert solver.check() == z3.unsat
