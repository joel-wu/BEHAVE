"""Unit tests for encoding a step as a formula.

The obligation under test is agreement: on a fixed stimulus the formula must
say what running the representation says, value for value and goal for goal.
Each test below fixes one way the two came apart, and every one of them was a
width or a meaning decided in the encoder rather than read off the
representation.
"""
import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.symbolic import encoder, interp, ranges
from behave.sim.symbolic.encoder import EncodingError, encode_step
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges


def build(body, inputs=("a",), state_ranges=None, widths=None, lowerer=None):
    """Lower and encode a one-method model, with ranges for its inputs."""
    src = ("class M:\n"
           "    def reset(self): pass\n"
           f"    def process(self, {', '.join(inputs)}):\n"
           + "".join(f"        {line}\n" for line in body))
    proc = lower_with_state_ranges(src, lowerer=lowerer)
    env = {n: ranges.Range(0, (1 << (widths or {}).get(n, 16)) - 1)
           for n in proc.inputs}
    env.update(state_ranges or {})
    inferred = ranges.fixpoint(proc, env)
    # No normally completing path (e.g. unconditional division by zero) leaves
    # no post-state facts. It does not erase the declared input domain.
    inferred.update({name: env[name] for name in proc.inputs})
    return proc, encode_step(proc, env=inferred)


def solve(enc, values):
    """What the formula leaves, on inputs fixed to ``values``."""
    s = z3.Solver()
    s.add(enc.constraint)
    for name, value in values.items():
        s.add(enc.pre[name] == value)
    assert s.check() == z3.sat
    m = s.model()

    def read(term):
        if isinstance(term, list):
            return [read(t) for t in term]
        return m.eval(term, model_completion=True).as_signed_long()

    return ({name: read(term) for name, term in enc.post.items()},
            {g for g, c in enc.hits.items()
             if z3.is_true(m.eval(c, model_completion=True))})


def agrees(proc, enc, values):
    """The formula and the representation, on the same stimulus.

    The transaction count as well as the values. Comparing only the buffers let
    the two readings agree about what a step put on its endpoints while
    disagreeing about whether it put anything there.
    """
    _, out, hits, _error = interp.run_step(proc, values)
    said, said_hits = solve(enc, values)
    if encoder.COUNT in said:
        assert said[encoder.COUNT] == out.count, "the transaction count"
    for name, value in out.buffers.items():
        got = said[name]
        if isinstance(got, list):
            active = value if isinstance(value, list) else [value]
            assert got[:out.count] == active, name
        else:
            assert got == value, name
    assert said_hits == hits
    return out


def test_a_shift_keeps_the_bits_it_moves():
    """A sixteen bit value shifted left by sixteen needs thirty-three bits.
    Taking the width from the operand drops exactly what the shift moved."""
    proc, enc = build(['return {"y": (a & 0xFFFF) << 16}'])
    agrees(proc, enc, {"a": 50494})
    assert solve(enc, {"a": 50494})[0]["y"][0] == 50494 << 16


def test_a_literal_index_names_the_cell_it_says():
    """An index carries only the width its own value needs, so cell numbers
    have to be compared at a width that tells them apart."""
    _proc, enc = build(['t = [a, a + 1, a + 2, a + 3]',
                       'return {"y": t[0]}'])
    assert solve(enc, {"a": 7})[0]["y"][0] == 7


@pytest.mark.parametrize("nested", [False, True])
def test_known_array_index_does_not_build_a_test_for_every_cell(monkeypatch, nested):
    from behave.sim.symbolic import ir

    engine = encoder._Encoder(None, {})
    values = [z3.BitVec(f"cell_{i}", 10 + i) for i in range(8)]
    cells = [[value, value + 1] for value in values] if nested else values

    def unnecessary(*_args):
        pytest.fail("a known in-range index traversed the array")

    monkeypatch.setattr(engine, "choose_value", unnecessary)
    for index in range(len(cells)):
        expr = ir.Op("sub", ir.Lit(index + 1), ir.Lit(1))
        got = engine.value(ir.Sel(ir.Var("a"), expr), {"a": cells})
        expected = cells[index]
        if nested:
            assert got is not expected  # Recursive values remain independent.
            assert all(z3.eq(a, b) for a, b in zip(got, expected))
        else:
            assert z3.eq(got, expected)


@pytest.mark.parametrize("index", [-4, -1, 0, 1, 7, 8, 16])
@pytest.mark.parametrize("closed_expression", [False, True])
def test_literal_and_closed_ir_index_follow_the_same_total_selection(index, closed_expression):
    from behave.sim.symbolic import ir
    from behave.sim.symbolic import typecheck as value_types

    cells = ir.Op("arraylit", *(ir.Lit(i + 20) for i in range(8)))
    at = ir.Op("sub", ir.Lit(index + 1), ir.Lit(1)) if closed_expression else ir.Lit(index)
    proc = ir.Procedure([], [], [], ["y"], ir.Assign("y", ir.Sel(cells, at)))
    proc.types = value_types.of_procedure(proc)[0]
    enc = encode_step(proc, env={"y": ranges.Range(0, 27)})
    actual = interp.run_step(proc, {})[1].transactions[0]["y"]
    assert solve(enc, {})[0]["y"] == actual == (index + 20 if 0 <= index < 8 else 0)


@pytest.mark.parametrize("index", [-4, -1, 0, 1, 7, 8, 16])
def test_literal_store_does_not_alias_a_negative_index_to_a_positive_cell(index):
    from behave.sim.symbolic import ir
    from behave.sim.symbolic import typecheck as value_types

    proc = ir.Procedure(["t"], [], [], [], ir.Store("t", ir.Lit(index), ir.Lit(99)),
                        types={"t": value_types.Array(8)}, initial={"t": list(range(8))})
    env = {"t": ranges.Range(0, 99)}
    pre = {"t": [z3.BitVecVal(value, ranges.encoding_width(env["t"]))
                 for value in proc.initial["t"]]}
    enc = encode_step(proc, pre=pre, env=env)
    actual = interp.run_step(proc, {})[0]["t"]
    assert solve(enc, {})[0]["t"] == actual


def test_a_negative_index_reads_from_the_end():
    _proc, enc = build(['t = [a, a + 1, a + 2, a + 3]',
                       'return {"y": t[-1]}'])
    assert solve(enc, {"a": 7})[0]["y"][0] == 10


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("closed_expression", [False, True])
def test_closed_store_visits_only_the_selected_cell(monkeypatch, nested, closed_expression):
    from behave.sim.symbolic import ir

    engine = encoder._Commands(None, {"t": ranges.Range(-128, 127), "v": ranges.Range(-8, 7)})
    guard = z3.Bool("closed_store_guard")
    cells = [z3.BitVec(f"closed_store_cell_{i}", 8) for i in range(128)]
    value = z3.BitVec("closed_store_value", 4)
    if nested:
        cells = [[cell, cell + 1] for cell in cells]
        value = [value, value + 1]
    before = list(cells)
    state = encoder._State({"t": cells, "v": value}, {}, z3.BoolVal(True),
                           encoder._code("", engine.error_codes))
    index = ir.Op("sub", ir.Lit(65), ir.Lit(1)) if closed_expression else ir.Lit(64)
    visited = []
    original_choose = engine.choose_value

    def choose(condition, new, old, what):
        visited.append(old)
        return original_choose(condition, new, old, what)

    monkeypatch.setattr(engine, "choose_value", choose)
    engine._store(ir.Store("t", index, ir.Var("v")), state, guard)
    assert len(visited) == (3 if nested else 1)
    assert state.rho["t"] is not cells
    for i in range(128):
        assert cells[i] is before[i]
        if i != 64:
            assert state.rho["t"][i] is cells[i]
    actual = state.rho["t"][64] if nested else [state.rho["t"][64]]
    old = cells[64] if nested else [cells[64]]
    new = value if nested else [value]
    different = [got != z3.If(guard, *encoder.widen(update, prior))
                 for got, update, prior in zip(actual, new, old, strict=True)]
    solver = z3.Solver()
    solver.add(*engine.defs, z3.Or(*different))
    assert solver.check() == z3.unsat


def test_identical_symbolic_scalar_reads_construct_one_selector(monkeypatch):
    from behave.sim.symbolic import ir

    engine = encoder._Encoder(None, {})
    cells = [z3.BitVec(f"shared_cell_{i}", 10 + i) for i in range(8)]
    index = z3.BitVec("shared_index", 9)
    read = ir.Sel(ir.Var("table"), ir.Var("i"))
    first = engine.value(read, {"table": cells, "i": index})

    def duplicate(*_args):
        pytest.fail("identical scalar cells/index rebuilt a selector")

    monkeypatch.setattr(engine, "choose_value", duplicate)
    # Reuse depends on the complete value, not list identity or source spelling.
    again = engine.value(read, {"table": list(cells), "i": index})
    alias = engine.value(ir.Sel(ir.Var("alias"), ir.Var("j")),
                         {"alias": list(cells), "j": index})
    assert z3.eq(first, again) and z3.eq(first, alias)


def test_symbolic_read_reuse_does_not_retain_mutated_cells_or_indices():
    from behave.sim.symbolic import ir

    engine = encoder._Encoder(None, {})
    cells = [z3.BitVecVal(10, 8), z3.BitVecVal(20, 8)]
    index, other = z3.BitVecs("mutation_i mutation_j", 8)
    rho = {"table": cells, "i": index}
    read = ir.Sel(ir.Var("table"), ir.Var("i"))
    first = engine.value(read, rho)
    cells[0] = z3.BitVecVal(99, 9)
    written = engine.value(read, rho)
    rho["i"] = other
    changed_index = engine.value(read, rho)
    # Includes negative and oversized indices: no cached in-range assumption.
    for at in (-128, -1, 0, 1, 2, 127):
        solved = z3.Solver()
        solved.add(index == at, other == 1)
        assert solved.check() == z3.sat
        model = solved.model()
        assert model.eval(first).as_signed_long() == ({0: 10, 1: 20}.get(at, 0))
        assert model.eval(written).as_signed_long() == ({0: 99, 1: 20}.get(at, 0))
        assert model.eval(changed_index).as_signed_long() == 20


def test_shared_selectors_preserve_guarded_writes_failures_and_goal_occurrences(monkeypatch):
    proc, _ = build([
        'table = [a, a + 1, a + 2, a + 3]',
        'x = table[i]',
        'again = table[i]',
        'if a == 2:',
        '    x = 1 // (a - 2)',
        'if a == 1:',
        '    table[i] = a + 7',
        'if x == table[i]:',
        '    again += 1',
        'return {"y": x + again + table[i]}',
    ], inputs=("a", "i"), widths={"a": 2, "i": 3})
    env = {"a": ranges.Range(0, 3), "i": ranges.Range(0, 7)}
    env = ranges.fixpoint(proc, env) | env
    pre = {name: z3.BitVec(f"select_equivalence_{name}", ranges.encoding_width(env[name]))
           for name in proc.inputs}
    shared = encode_step(proc, pre=pre, env=env, tag="shared-read")

    class Unshared(dict):
        def get(self, key, default=None):
            return default

    initialize = encoder._Encoder.__init__

    def unshared(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        self._selected_terms = Unshared()

    with monkeypatch.context() as plain:
        plain.setattr(encoder._Encoder, "__init__", unshared)
        original = encode_step(proc, pre=pre, env=env, tag="unshared-read")

    differences = [shared.ok != original.ok, shared.error != original.error]

    def compare(left, right):
        left, right = (encoder._materialize_functional(value) for value in (left, right))
        if isinstance(left, list):
            for a, b in zip(left, right, strict=True):
                compare(a, b)
        else:
            a, b = encoder.widen(left, right)
            differences.append(a != b)

    for name in shared.final:
        compare(shared.final[name], original.final[name])
    for goal in shared.hits:
        differences.append(shared.hits[goal] != original.hits[goal])
        differences.extend(a != b for a, b in zip(
            shared.hit_occurrences[goal], original.hit_occurrences[goal], strict=True))
    solver = z3.Solver()
    solver.add(shared.constraint, original.constraint, z3.Or(differences))
    assert solver.check() == z3.unsat
    for a in range(4):
        for i in range(8):
            agrees(proc, shared, {"a": a, "i": i})


def test_recursive_array_selection_and_store_encode_componentwise():
    proc, enc = build([
        'rows = [[a, a + 1], [a + 2, a + 3]]',
        'rows[1][0] = 9',
        'return {"y": rows[0][1] * 10 + rows[1][0]}',
    ])
    for value in (0, 1, 7):
        agrees(proc, enc, {"a": value})
        assert solve(enc, {"a": value})[0]["y"][0] == (value + 1) * 10 + 9


def test_nested_dictcomp_record_encodes_last_write_componentwise():
    proc, enc = build([
        'raw = {"x": {"value": a + i} for i in range(3)}',
        'return {"y": raw["x"]["value"]}',
    ])
    for value in (0, 1, 7):
        agrees(proc, enc, {"a": value})
        assert solve(enc, {"a": value})[0]["y"][0] == value + 2


def test_array_store_keeps_the_range_of_untouched_cells():
    """A command Store is read-modify-write even when it is unconditional."""
    from behave.sim.symbolic import ir, ranges
    from behave.sim.symbolic import typecheck as value_types

    body = ir.Seq(
        ir.Store("t", ir.Lit(0), ir.Lit(1)),
        ir.Assign("y", ir.Op(
            "add", ir.Sel(ir.Var("t"), ir.Lit(1)),
            ir.Sel(ir.Var("t"), ir.Lit(1)))))
    proc = ir.Procedure(
        ["t"], [], [], ["y"], body,
        types={"t": value_types.Array(2), "y": value_types.VALUE},
        initial={"t": [100, 200]})
    env = {"t": ranges.Range(0, 200)}
    width = ranges.encoding_width(env["t"])
    pre = {"t": [z3.BitVecVal(100, width), z3.BitVecVal(200, width)]}
    enc = encode_step(proc, pre=pre, env=env)
    said, _hits = solve(enc, {})
    assert said["t"] == [1, 200]
    assert said["y"] == 400


def test_recursive_out_of_range_selection_has_a_shape_correct_zero():
    from behave.sim.symbolic import ir, ranges
    from behave.sim.symbolic import typecheck as value_types

    rows = ir.Op(
        "arraylit",
        ir.Op("arraylit", ir.Lit(3), ir.Lit(4)),
        ir.Op("arraylit", ir.Lit(5), ir.Lit(6)))
    read = ir.Sel(ir.Sel(rows, ir.Lit(9)), ir.Lit(1))
    proc = ir.Procedure([], [], [], ["y"], ir.Assign("y", read))
    proc.types = value_types.of_procedure(proc)[0]
    env = {"y": ranges.Range(0, 6)}
    enc = encode_step(proc, env=env)
    assert solve(enc, {})[0]["y"] == 0
    assert interp.run_step(proc, {})[1].transactions == [{"y": 0}]


def test_a_truth_value_reads_as_one():
    """Every term is read as signed, so a truth value one bit wide would be
    minus one."""
    _proc, enc = build(['return {"y": 1 if a != 0 else 0}'])
    assert solve(enc, {"a": 3})[0]["y"][0] == 1


def test_an_optional_record_local_encodes_presence_and_payload_together():
    proc, enc = build([
        'row = {"y": a + 1} if a else None',
        'return row',
    ])

    absent = agrees(proc, enc, {"a": 0})
    present = agrees(proc, enc, {"a": 3})
    assert absent.transactions == []
    assert present.transactions == [{"y": 4}]
    assert solve(enc, {"a": 0})[0][encoder.COUNT] == 0
    said = solve(enc, {"a": 3})[0]
    assert said[encoder.COUNT] == 1
    assert said["y"][0] == 4


@pytest.mark.parametrize("lowerer", (_Lowerer, GraphLowerer),
                         ids=("term", "graph"))
def test_recursive_record_and_fixed_sequence_reads_encode_exactly(lowerer):
    proc, enc = build([
        'row = {"cells": [a, a + 1]}',
        'return {"y": row["cells"][0] * 10 + row["cells"][1]}',
    ], lowerer=lowerer)

    for value in (0, 4, 9):
        output = agrees(proc, enc, {"a": value})
        assert output.transactions == [{"y": value * 11 + 1}]


def test_the_goals_a_run_reaches_are_the_goals_the_formula_says():
    proc, enc = build(['if a > 10:',
                       '    y = 1',
                       'else:',
                       '    y = 2',
                       'return {"y": y}'])
    for value in (0, 11):
        agrees(proc, enc, {"a": value})


def test_division_rounds_the_way_the_source_does():
    """Exhaustive over a small domain, against Python rather than against the
    other reading: the two written here truncated towards zero, agreed with
    each other, and the step audit passed on them for a day."""
    src = ('class M:\n'
           '    def reset(self): pass\n'
           '    def process(self, a, b):\n'
           '        return {"q": (a - 8) // (b - 8), "r": (a - 8) % (b - 8)}\n')
    proc = lower_with_state_ranges(src)
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 15),
                                 "b": ranges.Range(0, 15)})
    enc = encode_step(proc, env=env)
    for a in range(16):
        for b in range(16):
            if b == 8:
                continue                      # the divisor is guarded, not this
            want = ((a - 8) // (b - 8), (a - 8) % (b - 8))
            _, out, _, error = interp.run_step(proc, {"a": a, "b": b})
            assert not error
            assert out.transactions == [{"q": want[0], "r": want[1]}], (a, b)
            said, _ = solve(enc, {"a": a, "b": b})
            assert (said["q"][0], said["r"][0]) == want, (a, b)


def test_floor_division_uses_one_unsigned_quotient_without_remainder():
    a, b = z3.BitVecs("floor_div_a floor_div_b", 5)
    zero = z3.BitVecVal(0, 5)
    old = z3.If(b == zero, zero, z3.If(
        z3.Or(z3.SRem(a, b) == zero, (a < zero) == (b < zero)),
        a / b, a / b - 1))
    got = encoder._Encoder.floor_div(a, b)
    solver = z3.Solver()
    solver.add(old != got)
    assert solver.check() == z3.unsat
    seen, pending, divisions = set(), [got], []
    while pending:
        term = pending.pop()
        if term.get_id() in seen:
            continue
        seen.add(term.get_id())
        assert term.decl().kind() not in (z3.Z3_OP_BSDIV, z3.Z3_OP_BSREM, z3.Z3_OP_BSMOD)
        if term.decl().kind() == z3.Z3_OP_BUDIV:
            divisions.append(term)
        pending.extend(term.children())
    assert len(divisions) == 1
    for width in (1, 64, 128):
        limit = 1 << (width - 1)
        for left in {-limit, -limit + 1, -1, 0, limit - 1}:
            for right in {-limit, -1, 0, 1, limit - 1}:
                av, bv = z3.BitVecVal(left, width), z3.BitVecVal(right, width)
                x, y = av.as_signed_long(), bv.as_signed_long()
                want = z3.BitVecVal(x // y if y else 0, width)
                assert z3.eq(z3.simplify(encoder._Encoder.floor_div(av, bv)), want)


def test_positive_power_of_two_division_is_shift_and_mask():
    for width in (2, 5, 64, 128):
        value = z3.BitVec(f"power_dividend_{width}", width)
        for shift in {0, min(3, width - 2), width - 2}:
            divisor = z3.BitVecVal(1 << shift, width)
            assert z3.eq(encoder._floor_div(value, divisor), value >> shift)
            assert z3.eq(encoder._floor_mod(value, divisor), value & ((1 << shift) - 1))
            for integer in {-(1 << (width - 1)), -9, -1, 0, 1, 9, (1 << (width - 1)) - 1}:
                literal = z3.BitVecVal(integer, width)
                number = literal.as_signed_long()
                assert z3.simplify(encoder._floor_div(literal, divisor)).as_signed_long() == number // (1 << shift)
                assert z3.simplify(encoder._floor_mod(literal, divisor)).as_signed_long() == number % (1 << shift)
        # Zero and a set sign bit are not positive powers of two in this carrier.
        for divisor in (0, 1 << (width - 1)):
            assert encoder._positive_power_of_two(z3.BitVecVal(divisor, width)) is None


def test_the_contract_holds_the_inputs_to_what_the_interface_admits():
    """A width holds a range but does not say it: a sixteen bit port is
    carried in seventeen signed bits, and a solver free to choose among them
    answers with stimuli nothing can drive."""
    _proc, enc = build(['return {"y": a}'])
    s = z3.Solver()
    s.add(enc.constraint)
    s.add(z3.Or(enc.pre["a"] < 0, enc.pre["a"] > 0xFFFF))
    assert s.check() == z3.unsat


def test_an_input_without_a_range_is_refused():
    """A width guessed for an input is a formula that wraps where the model
    does not, so the step is refused instead."""
    src = ("class M:\n"
           "    def reset(self): pass\n"
           "    def process(self, a):\n"
           "        return {'y': a}\n")
    proc = lower_with_state_ranges(src)
    with pytest.raises(EncodingError):
        encode_step(proc, env={})


def test_a_failed_step_reaches_nothing_after_the_failure():
    _proc, enc = build(['y = a // 0',
                       'return {"y": y}'])
    _said, _ = solve(enc, {"a": 3})
    s = z3.Solver()
    s.add(enc.constraint, enc.pre["a"] == 3)
    assert s.check() == z3.sat
    assert z3.is_false(s.model().eval(enc.ok, model_completion=True))


def test_a_long_step_encodes_without_running_out_of_stack():
    """A sequence is nested one node per command, so a walk that recurses over
    it has a depth that follows the length of the unrolled step rather than the
    nesting of the source. Two models of the population exceeded the
    interpreter's stack in the encoder at around a thousand commands, and this
    is the fifth traversal in this package to have needed a loop for it.

    The bound is deliberately past the default recursion limit and the limit is
    not raised, since raising it is what would hide the next one.
    """
    body = ['z = 0']
    body += [f'z = z + {n}' for n in range(1200)]
    body += ['return {"y": z}']
    _proc, enc = build(body)
    said, _ = solve(enc, {"a": 1})
    assert said["y"] == [sum(range(1200))]
