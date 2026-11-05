"""The questions the encoder asks, answered before a term is built.

M3. The encoder discovers, from inside a formula, what type a variable has, how many
cells an array holds and whether a subterm has a range. Each has an answer before
encoding starts, and asking late has one visible consequence: a model whose range
analysis came up short appears as an `EncodingError`, indistinguishable from a model the
encoder has a real gap for.

The direction under test is the one M3's acceptance is about:

    verify(proc, plan) == []   =>   encoding cannot fail for a type, a shape or a range

It holds by an argument and not by a count. Whether a subterm has a range is monotone in
the environment, and the encoder's environment only grows -- nothing removes from
`self.env` -- so a subterm this plan resolves is one the encoder can resolve. These tests
exercise that; they do not establish it, and a population run would not either.

The converse is not claimed and is not tested, because it is false today: the encoder
flows its own environment through `_rebind`, so a plan made from the environment the
step starts in is conservative. `test_a_flagged_model_may_still_encode` pins that as a
known property rather than leaving it to look like a bug.
"""
import pickle

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import instance as bi
from behave.sim.behavior import residual
from behave.sim.symbolic import ir, ranges, typecheck as types
from behave.sim.symbolic.encoder import EncodingError, encode_step
from behave.sim.symbolic.plan import (
    EncodingPlan,
    ExpressionPosition,
    FiniteCarrier,
    PlanMismatch,
    Unresolved,
    plan_encoding,
    verify,
)
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

PLAIN = """class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        self.x = (self.x + a) & 255
        return {"y": self.x}
"""

#: A branch, so the body carries the predicates if-conversion introduces. Those have no
#: range in the starting environment and the encoder has no trouble with them.
BRANCHED = """class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        if a > 3:
            self.x = (self.x + 1) & 255
        else:
            self.x = 0
        return {"y": self.x}
"""


def schema():
    return TransactionSchema([Endpoint("a", "input", 8),
                              Endpoint("y", "output", 16)],
                             source="declared by the test",
                             authority=Authority.TASK,
                             reading=Reading.ELABORATED)


def instance(src, bound=10):
    return bi.lower(src, schema(), bound=bound)


def test_a_plan_answers_every_width_the_step_needs():
    inst = instance(PLAIN)
    plan = plan_encoding(inst.proc, inst.env, bound=10)
    assert isinstance(plan, EncodingPlan)
    assert plan.complete, [u.as_dict() for u in plan.unresolved]
    for name in list(inst.proc.state) + list(inst.proc.inputs):
        assert plan.widths[name] >= 1, name


@pytest.mark.parametrize("depth", [0, 1, 2])
def test_singleton_folding_uses_only_explicit_boundary_restrictions(depth):
    from behave.sim.symbolic import encoder, interp

    shape, initial, read = types.INTEGER, -2, ir.Var("s")
    for _ in range(depth):
        shape, initial = types.Array(2, shape), [initial, initial]
        read = ir.Sel(read, ir.Var("a"))
    proc = ir.Procedure(["s"], ["a"], ["out"], ["y"],
        ir.Assign("out", read), initial={"s": initial},
        output_vars={"y": "out"},
        types={"s": shape, "a": types.INTEGER, "out": types.INTEGER})
    env = {"s": ranges.Range(-2, -2), "a": ranges.Range(0, 1)}
    prepared = plan_encoding(proc, env)
    fresh = encode_step(proc, env=env, plan=prepared)
    # Public symbols and their restrictions survive body specialization.
    boundary = tuple(encoder._flatten_tree(fresh.pre["s"]))
    assert all(not z3.is_bv_value(term) for term in boundary)
    solver = z3.Solver()
    solver.add(fresh.constraint, boundary[0] != -2)
    assert solver.check() == z3.unsat

    def supplied(value):
        if isinstance(value, list):
            return [supplied(item) for item in value]
        return z3.BitVecVal(1, prepared.widths["s"])

    # The same singleton proposal cannot narrow a caller's pre-state, even
    # when a larger value fits the already-declared scalar representation.
    external = encode_step(proc, pre={"s": supplied(initial)}, env=env, plan=prepared)
    for encoded, expected in ((fresh, -2), (external, 1)):
        for value in range(2):
            solver = z3.Solver()
            solver.add(encoded.constraint, encoded.pre["a"] == value, encoded.ok)
            assert solver.check() == z3.sat
            assert solver.model().eval(encoded.post["y"]).as_signed_long() == expected
    for value in range(2):
        _state, output, _hits, error = interp.run_step(proc, {"a": value})
        assert not error and output.buffers["y"] == [-2]


@pytest.mark.parametrize("write", ["exact", "conditional", "dynamic", "other"])
def test_array_shape_preserves_exact_writes_without_narrowing_entry(write):
    from behave.sim.symbolic import plan as planning

    update = ir.Store("buf", ir.Lit(0), ir.Lit(1))
    if write == "conditional":
        update = ir.If(ir.Op("ne", ir.Var("a"), ir.Lit(0)), update, ir.Skip())
    elif write == "dynamic":
        update = ir.Store("buf", ir.Var("a"), ir.Lit(1))
    elif write == "other":
        update = ir.Store("buf", ir.Lit(1), ir.Lit(1))
    loop = ir.For("i", ir.Sel(ir.Var("buf"), ir.Lit(0)),
                  ir.Assign("total", ir.Op("add", ir.Var("total"), ir.Lit(1))))
    proc = ir.Procedure(
        ["buf"], ["a"], ["i", "total"], ["y"],
        ir._sequence([update, ir.Assign("total", ir.Lit(0)), loop]),
        initial={"buf": [0, 0]}, output_vars={"y": "total"},
        types={"buf": types.Array(2, types.INTEGER), "a": types.INTEGER,
               "i": types.INTEGER, "total": types.INTEGER})
    env = {"buf": ranges.Range(0, 7), "a": ranges.Range(0, 1)}
    plan = plan_encoding(proc, env, bound=2)
    assert plan.complete, plan.unresolved
    number = planning._command_numbers(proc.body)[id(loop)]
    assert plan.loop_bounds[str(ExpressionPosition(number, "trip_count"))] == (
        1 if write == "exact" else 7)
    assert type(env["buf"]) is ranges.Range
    assert (plan.env["buf"].lo, plan.env["buf"].hi) == (0, 7)
    # Frozen plan ranges must behave identically when verification re-derives
    # the plan. No stronger product premise is added to the public boundary.
    assert plan_encoding(proc, plan.env, bound=2).fingerprint == plan.fingerprint
    encoded = encode_step(proc, env=env, plan=plan)
    for first in range(8):
        for a in range(2):
            expected = 1 if (write == "exact" or write == "conditional" and a
                             or write == "dynamic" and a == 0) else first
            solver = z3.Solver()
            solver.add(encoded.constraint, encoded.pre["a"] == a,
                       encoded.pre["buf"][0] == first,
                       encoded.pre["buf"][1] == 7)
            assert solver.check() == z3.sat
            assert solver.model().eval(encoded.post["y"]).as_long() == expected


def test_an_empty_verify_means_the_step_encodes():
    """The direction M3's acceptance is about."""
    inst = instance(PLAIN)
    plan = plan_encoding(inst.proc, inst.env, bound=10)
    assert verify(inst.proc, plan) == []
    encode_step(inst.proc, env=inst.env)        # must not raise


def test_noop_padding_does_not_allocate_semantic_command_copies(monkeypatch):
    from behave.sim.symbolic import encoder, interp
    from behave.sim.symbolic import plan as planning

    body = ir.Assign("y", ir.Lit(1))
    padded = body
    for _ in range(30):
        padded = ir.Seq(ir.Skip(), ir.Seq(padded, ir.Skip()))
    env = {"y": ranges.Range(0, 1)}
    monkeypatch.setattr(planning, "TERM_BUDGET", 3)
    monkeypatch.setattr(encoder, "TERM_BUDGET", 3)
    prime = encoder._Encoder._prime_command

    def checked_prime(self, command):
        assert not isinstance(command, ir.Skip)
        return prime(self, command)

    monkeypatch.setattr(encoder._Encoder, "_prime_command", checked_prime)
    for loop_body in (body, padded):
        proc = ir.Procedure([], [], ["i", "y"], ["y"],
                            ir.For("i", ir.Lit(2), loop_body))
        planned = plan_encoding(proc, env)
        assert planned.complete, planned.unresolved
        assert planned.expansion_cost == 3  # one loop and two actual writes
        planned.validate(proc, env)
        encoded = encode_step(proc, env=env, plan=planned)
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.post["y"] != 1)
        assert solver.check() == z3.unsat
        _, output, _, error = interp.run_step(proc, {})
        assert not error
        assert output.transactions == [{"y": 1}]


def test_a_state_variable_with_no_range_is_reported_rather_than_discovered():
    """The fault the plan exists to move earlier. Without a range there is no width,
    and the encoder finds that out while building a formula."""
    inst = instance(PLAIN)
    env = {k: v for k, v in inst.env.items() if k != "x"}
    plan = plan_encoding(inst.proc, env, bound=10)
    faults = verify(inst.proc, plan)
    assert any(item.kind == "range" and item.what == "x"
               for item in plan.unresolved), plan.unresolved
    assert any(f.kind == "range" and "unresolved at x:" in f.message for f in faults), \
        [f.as_dict() for f in faults]
    with pytest.raises(EncodingError):
        encode_step(inst.proc, env=env)


def test_a_model_that_encodes_is_not_flagged():
    """Exact in both directions now, and this is the direction that used to fail.

    Two predictors were written before this. The first asked in the environment the
    step starts in and flagged **365** models that encode without trouble, because the
    encoder writes each assignment's range back into its own environment as the step is
    built -- `eq(#taken:entry, 0)` has a range by the time it is reached although the
    predicate has none at the start. The second flowed the environment and was wrong by
    three in the other direction, which is the one that costs soundness.

    Neither is here. `verify` asks the encoder in the mode where it records instead of
    stopping, so the thing that answers is the thing that would fail.
    """
    inst = instance(BRANCHED)
    encode_step(inst.proc, env=inst.env)
    assert verify(inst.proc, plan_encoding(inst.proc, inst.env, bound=10)) == []


def test_occurrence_carriers_use_the_source_join_before_an_inlined_helper():
    src = """def mask(value, width):
    return value & ((1 << width) - 1)


class M:
    def reset(self):
        pass

    def process(self, a):
        if a < 2:
            selected = 1
        elif a < 5:
            selected = 2
        else:
            selected = 3
        return {"y": mask(selected, 2)}
"""
    inst = instance(src)
    plan = plan_encoding(inst.proc, inst.env, bound=10)

    assert plan.complete, [item.as_dict() for item in plan.unresolved]
    encode_step(inst.proc, env=inst.env, plan=plan)


def test_an_inlined_helper_join_supplies_its_next_branch_decision():
    src = """def choose(a):
    if a < 4:
        selected = 1
    else:
        selected = 2
    if selected:
        return selected
    return 0


class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": choose(a)}
"""
    inst = instance(src)
    plan = plan_encoding(inst.proc, inst.env, bound=10)

    assert plan.complete, [item.as_dict() for item in plan.unresolved]
    encode_step(inst.proc, env=inst.env, plan=plan)


def test_occurrence_carriers_use_source_joins_inside_retained_loops():
    src = """class M:
    def reset(self):
        pass

    def process(self, a):
        y = 0
        for row in range(2):
            for col in range(2):
                if row >= 0 and row < 2 and col >= 0 and col < 2 and a < 8:
                    y += 1
        return {"y": y}
"""
    inst = instance(src)
    plan = plan_encoding(inst.proc, inst.env, bound=10)

    assert plan.complete, [item.as_dict() for item in plan.unresolved]
    encode_step(inst.proc, env=inst.env, plan=plan)


def test_branch_refinement_bounds_a_partial_shift_before_encoding():
    src = """def shifted_mask(a, b):
    amount = a - b
    if amount >= 28:
        return 0
    if amount == 0:
        return 0
    return (1 << amount) - 1


class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": shifted_mask(a, 1) & 255}
"""
    inst = instance(src)
    plan = plan_encoding(inst.proc, inst.env, bound=10)

    assert plan.complete, [item.as_dict() for item in plan.unresolved]
    encode_step(inst.proc, env=inst.env, plan=plan)


def test_every_fault_is_reported_and_not_only_the_first():
    """A step with four gaps was reported as having one, because the encoder stopped
    at the first. Recording instead of stopping is what makes the funnel's reasons
    a list rather than whichever gap the walk reached soonest."""
    inst = instance(PLAIN)
    env = {k: v for k, v in inst.env.items() if k != "x"}
    faults = verify(inst.proc, env=env)
    assert len(faults) > 1, [f.as_dict() for f in faults]
    assert {f.kind for f in faults} <= {"range", "shape", "type"}


def test_a_refusal_says_which_column_it_belongs_in():
    """The attribution is free and was being thrown away.

    Which column a refusal belongs in -- the frontend left something unanswered, or the
    subset has no operation for it, or nothing can build that width -- decides whose
    work item it is, and it is known at the moment of the refusal. It used to be
    recovered by encoding a second time in collecting mode, and a second encoding is not
    free on a path that scores candidates.
    """
    inst = instance(PLAIN)
    env = {k: v for k, v in inst.env.items() if k != "x"}
    with pytest.raises(EncodingError) as caught:
        encode_step(inst.proc, env=env)
    assert caught.value.kind == "range"


def test_a_refusal_outside_the_frontend_classes_says_so_by_saying_nothing():
    """`kind` is `None` for an operation with no primitive or a width nothing can
    build, so a reader is not told the frontend is at fault when it is not."""
    assert EncodingError("something else").kind is None


def test_a_plan_says_which_bound_it_is_about():
    """A width follows from a range and a range is a property of a bounded instance,
    so the same program at two bounds has two plans."""
    inst = instance(PLAIN)
    assert plan_encoding(inst.proc, inst.env, bound=10).bound == 10
    assert plan_encoding(inst.proc, inst.env).bound is None


def test_arrays_are_counted_in_cells_and_not_given_a_width_of_their_own():
    src = """class M:
    def reset(self):
        self.t = [0, 0, 0, 0]

    def process(self, a):
        self.t[a & 3] = a
        return {"y": self.t[0]}
"""
    inst = instance(src)
    plan = plan_encoding(inst.proc, inst.env, bound=10)
    assert plan.cells.get("t") == 4
    assert plan.widths.get("t", 1) >= 1


def test_a_plan_carries_the_complete_recursive_shape():
    src = """class M:
    def reset(self):
        self.t = [[1, 2], [3, 4]]

    def process(self, a):
        self.t[a & 1][0] = a
        return {"y": self.t[0][0]}
"""
    inst = instance(src)
    plan = plan_encoding(inst.proc, inst.env, bound=10)
    assert plan.cells["t"] == 2
    assert plan.shapes["t"] == (2, 2)

    shapes = dict(plan.shapes)
    shapes["t"] = (2, 3)
    plan = plan.replaced(shapes=shapes)
    with pytest.raises(PlanMismatch, match="finite-carrier layout"):
        encode_step(inst.proc, env=inst.env, plan=plan)


def test_a_supplied_pre_state_must_match_recursive_shape_and_leaf_width():
    src = """class M:
    def reset(self):
        self.t = [[1, 2], [3, 4]]

    def process(self, a):
        self.t[a & 1][0] = a
        return {"y": self.t[0][0]}
"""
    inst = instance(src)
    plan = plan_encoding(inst.proc, inst.env, bound=10)
    width = plan.widths["t"]
    leaf = lambda name, bits=width: z3.BitVec(name, bits)

    with pytest.raises(EncodingError) as caught:
        encode_step(inst.proc, env=inst.env, plan=plan,
                    pre={"t": [[leaf("a"), leaf("b")]]})
    assert caught.value.kind == "shape"

    wrong_width = max(1, width - 1) if width != 1 else width + 1
    with pytest.raises(EncodingError) as caught:
        encode_step(inst.proc, env=inst.env, plan=plan, pre={
            "t": [[leaf("c", wrong_width), leaf("d", wrong_width)],
                  [leaf("e", wrong_width), leaf("f", wrong_width)]]})
    assert caught.value.kind == "range"


# ── M3's remainder: the encoder reads the plan ──

def test_the_plan_and_the_encoder_agree_on_every_width():
    """The property that has to hold before the encoder can read the plan at all,
    and it did **not** hold.

    Both sides computed "the width the encoder carries this range at" as their own
    expression, and `plan_encoding` omitted `signed=True`, so it inferred
    signedness from `lo < 0` and every non-negative range came out **one bit
    narrower**. Measured over four real models before the fix: 20 of 20 widths
    disagreed, each by exactly one. Nothing had broken because nothing read
    `plan.widths` yet, and M3's remaining task is to read them -- so the bug's
    whole lifetime was the window in which it was invisible.

    Both now call `ranges.encoding_width`. This is the gate that keeps them there.
    """
    for src in (PLAIN, BRANCHED):
        inst = instance(src)
        plan = plan_encoding(inst.proc, inst.env, bound=10)
        enc = encode_step(inst.proc, env=inst.env)
        assert plan.widths, "a plan with no widths tests nothing"
        for name, width in plan.widths.items():
            if name in enc.widths:
                assert width == enc.widths[name], (name, width, enc.widths[name])


def test_the_width_the_encoder_carries_a_range_at_is_one_function():
    """Signed always, and at least one bit. `width_for`'s own default is neither,
    because it answers a question about the range rather than about the encoder."""
    from behave.sim.symbolic import ranges

    for rng, expected in ((ranges.Range(0, 1), 2),
                          (ranges.Range(0, 3), 3),
                          (ranges.Range(0, 255), 9),
                          (ranges.Range(-128, 127), 8),
                          (ranges.Range(0, 0), 1)):
        assert ranges.encoding_width(rng) == expected, rng
        # And it is strictly the signed width, never the range-inferred one, which
        # is what differed.
        assert ranges.encoding_width(rng) == \
            max(ranges.width_for(rng, signed=True), 1)


def test_a_plan_for_another_bound_is_refused_rather_than_used():
    """The encoder reads the plan and does not trust it.

    A plan whose width for a name is not the width this step carries was built
    against a different environment or a different bound. Using it would narrow a
    term silently; preferring the encoder's would leave a reader holding a plan
    that does not describe the formula. So it is a refusal, in the same column as
    anything else the frontend had not worked out.
    """
    inst = instance(PLAIN)
    plan = plan_encoding(inst.proc, inst.env, bound=10)
    good = encode_step(inst.proc, env=inst.env, plan=plan)
    assert good.widths

    name = next(iter(plan.widths))
    widths = dict(plan.widths)
    widths[name] = widths[name] - 1
    plan = plan.replaced(widths=widths)
    with pytest.raises(PlanMismatch, match="finite-carrier layout"):
        encode_step(inst.proc, env=inst.env, plan=plan)


def test_reading_the_plan_gives_the_same_formula_as_not_reading_it():
    """Since the plan agrees with the encoder, passing it changes nothing about
    the formula. That is the point: the encoder stops *discovering* the widths, and
    what it builds is unchanged."""
    for src in (PLAIN, BRANCHED):
        inst = instance(src)
        plan = plan_encoding(inst.proc, inst.env, bound=10)
        without = encode_step(inst.proc, env=inst.env)
        with_plan = encode_step(inst.proc, env=inst.env, plan=plan)
        assert without.widths == with_plan.widths


def test_a_plan_is_an_immutable_pickle_stable_value():
    inst = instance(PLAIN)
    plan = plan_encoding(inst.proc, inst.env, bound=10)

    with pytest.raises(AttributeError):
        plan.bound = 11
    with pytest.raises(TypeError):
        plan.widths["x"] = 1
    with pytest.raises(AttributeError):
        plan.env["x"].lo = -1

    restored = pickle.loads(pickle.dumps(plan))
    assert restored == plan
    assert restored.fingerprint == plan.fingerprint
    assert dict(restored.env) == dict(plan.env)


def test_plan_identity_binds_procedure_environment_and_exact_finite_bound():
    inst = instance(PLAIN)
    plan = plan_encoding(inst.proc, inst.env, bound=10)
    assert plan.validate(inst.proc, inst.env, bound=10) is plan

    another = instance(BRANCHED)
    with pytest.raises(PlanMismatch, match="another procedure"):
        plan.validate(another.proc, inst.env, bound=10)

    changed_env = dict(inst.env)
    changed_env["a"] = ranges.Range(-1, changed_env["a"].hi)
    with pytest.raises(PlanMismatch, match="another range environment"):
        plan.validate(inst.proc, changed_env, bound=10)
    with pytest.raises(PlanMismatch, match="10 steps, not 9"):
        plan.validate(inst.proc, inst.env, bound=9)

    assert plan.replaced(bound=11).fingerprint != plan.fingerprint
    assert plan.replaced(env=changed_env).fingerprint != plan.fingerprint
    assert plan_encoding(another.proc, inst.env, bound=10).fingerprint != \
        plan.fingerprint


def test_an_incomplete_plan_can_identify_an_integer_wider_than_decimal_policy():
    """A width refusal must not crash while fingerprinting its diagnostic plan."""

    endpoint = 1 << 20_000
    plan = EncodingPlan(
        {}, {}, {}, {}, {},
        {ExpressionPosition(0, "rhs"): FiniteCarrier(
            -endpoint, endpoint, 20_002)},
        1, 1,
        (Unresolved(
            "width", ExpressionPosition(0, "rhs"),
            "the finite carrier exceeds the supported scalar width"),),
        {"x": ranges.Range(0, 1)},
        "procedure",
    )

    assert not plan.complete
    assert len(plan.fingerprint) == 64
    assert pickle.loads(pickle.dumps(plan)).fingerprint == plan.fingerprint


def test_a_fixpoint_plan_is_not_tied_to_one_finite_horizon():
    inst = instance(PLAIN)
    plan = plan_encoding(inst.proc, inst.env)
    assert plan.validate(inst.proc, inst.env, bound=1) is plan
    assert plan.validate(inst.proc, inst.env, bound=100) is plan


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_a_finite_plan_bound_is_a_positive_integer(bad):
    inst = instance(PLAIN)
    with pytest.raises(ValueError, match="positive integer"):
        plan_encoding(inst.proc, inst.env, bound=bad)


def test_unresolved_expressions_use_stable_occurrence_keys_not_object_ids():
    expr = ir.Op("add", ir.Var("x"), ir.Var("missing"))
    proc = ir.Procedure(
        ["x"], ["a"], [], ["y"], ir.Assign("x", expr),
        types={"x": types.VALUE, "a": types.VALUE},
        initial={"x": 0}, output_vars={"y": "x"})
    env = {"x": ranges.Range(0, 3), "a": ranges.Range(0, 1)}

    first = plan_encoding(proc, env, bound=2)
    rebuilt = pickle.loads(pickle.dumps(proc))
    second = plan_encoding(rebuilt, env, bound=2)
    first_sites = [u.what for u in first.unresolved
                   if isinstance(u.what, ExpressionPosition)]
    second_sites = [u.what for u in second.unresolved
                    if isinstance(u.what, ExpressionPosition)]
    assert first_sites and first_sites == second_sites
    assert first.procedure_fingerprint == second.procedure_fingerprint
    assert not hasattr(first, "subterms")


def test_expression_positions_have_one_lossless_core_occurrence_adapter():
    from behave.sim.core.catalogue import OccurrenceKey

    position = ExpressionPosition(7, "rhs", (2, 0))
    public = position.catalogue_key()

    assert type(public) is OccurrenceKey
    assert public.as_dict() == {
        "provider": "behavior",
        "path": ["expression", 7, 2, 0],
        "role": "rhs",
    }
    assert ExpressionPosition.from_catalogue_key(public) == position
    with pytest.raises(ValueError, match="not a Behavior expression"):
        ExpressionPosition.from_catalogue_key(
            OccurrenceKey("behavior", ("source", 7), "rhs"))


def test_a_wrong_horizon_is_rejected_before_any_z3_value_is_allocated(
        monkeypatch):
    inst = instance(PLAIN)
    plan = plan_encoding(inst.proc, inst.env, bound=10)

    def allocated(*args, **kwargs):
        raise AssertionError("a Z3 value was allocated before plan validation")

    monkeypatch.setattr(residual.z3, "BitVecVal", allocated)
    monkeypatch.setattr(residual.z3, "BitVec", allocated)
    with pytest.raises(PlanMismatch, match="10 steps, not 9"):
        residual.unroll(inst.proc, inst.env, 9, plan=plan)


def test_a_joined_bounded_range_is_not_mistaken_for_a_multistep_plan():
    src = """class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        self.x = self.x + 1
        return {"y": 1 if self.x == 2 else 0}
"""
    inst = instance(src, bound=3)
    plan = plan_encoding(inst.proc, inst.env, bound=3)

    # The ordinary unrolling chooses each step's carrier from what that step
    # actually receives.  The current plan instead fixes the carrier chosen
    # from one joined environment; identity makes it the right record, but does
    # not prove that carrier inductive across steps.
    residual.unroll(inst.proc, inst.env, 3)
    with pytest.raises(EncodingError, match="supplied value has another leaf width"):
        residual.unroll(inst.proc, inst.env, 3, plan=plan)
