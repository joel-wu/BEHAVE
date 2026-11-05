"""A control site as a formula, and what it costs to make one expressible.

P1.7. The explorer targets `br_vars` -- numbered `Bool` `define-fun`s found by a regular
expression -- so an **inactive** branch can be counted as a hit. `catalogue.py` builds the
real control structure from RTLIL before `proc`, where the nesting says what activates
what; this is the join between the two, and the join is not a lookup.

A `Site` has no `define-fun` to be matched to, because the catalogue is from before `proc`
and the SMT2 is from after. What it has is a definition -- `signal == outcome` and every
`(asig == alabel)` in its activation -- which is a formula over wires **if those wires
exist in the model**. Measured first, because they mostly do not: only 7.4% of sites over
the population have their switched signal in the SMT2, since `opt` deletes a wire nothing
reads. Keeping the ones the catalogue names takes that to 64.6%.

So there are two things to pin: that the formula is right where it can be built, and that
where it cannot the answer is a refusal naming the signal rather than a fallback to the
`br_var` that looks closest -- which would put the unguarded target back under a guarded
name, and is the defect this item exists to remove.
"""
import pytest

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver

# Nested control, so a site has a real activation condition: the inner assignment happens
# only when the outer one did. Named wires, so the sites are resolvable without keeping
# anything -- the keep flow is tested separately, on the signals synthesis removes.
NESTED = """module nested (
    input wire clk, input wire rst, input wire in_valid, input wire out_ready,
    input wire [7:0] a,
    output wire in_ready, output wire out_valid, output reg [7:0] y
);
    wire take  = in_valid & in_ready;
    wire big   = a[7];
    assign in_ready  = 1'b1;
    assign out_valid = 1'b1;
    always @(posedge clk) begin
        if (rst) y <= 8'd0;
        else if (take) begin
            if (big) y <= y + 8'd2;
            else     y <= y + 8'd1;
        end
    end
endmodule
"""


def _catalogue(tmp_path, source, top):
    from behave.sim.rtl.catalogue import catalogue_of

    src = tmp_path / f"{top}.sv"
    src.write_text(source)
    return src, catalogue_of(str(src), top)[0]


@pytest.fixture
def bench(tmp_path):
    src, sites = _catalogue(tmp_path, NESTED, "nested")
    if not sites:
        pytest.skip("no control sites were read from this design")
    return src, sites, tmp_path


# ── the formula ──

def test_a_bit_selected_switch_uses_its_base_wire_and_selected_value():
    """``\\in_a [0]`` is an RTLIL SigSpec, not a wire name containing a space."""

    from behave.sim.rtl.catalogue import Site
    from behave.sim.rtl.site_target import predicate_for, site_hits

    site = Site(
        "m", "$proc$1", r"\in_a [0]", "m.sv:1.1-1.2", "1'1", 1, (),
        siblings=("1'1", "default"),
    )
    sites = {site.target: site}
    value = z3.BitVec("in_a", 8)

    class Explorer:
        @staticmethod
        def value_var(name):
            return (value, 8) if name == "in_a" else (None, None)

    predicate = predicate_for(Explorer(), site)
    assert z3.is_bool(predicate)
    solver = z3.Solver()
    solver.add(predicate, value == 3)
    assert solver.check() == z3.sat
    solver = z3.Solver()
    solver.add(predicate, value == 2)
    assert solver.check() == z3.unsat
    assert site_hits(sites, {"in_a": 3}) == ({site.goal}, {})
    assert site_hits(sites, {"in_a": 2}) == (set(), {})


def test_brackets_inside_an_automatic_wire_name_are_not_a_selection():
    """Yosys function temporaries include source ranges inside one `$` name."""

    from behave.sim.rtl.sigspec import signal_value, wire_names

    name = r"$8\fp32_add$func$design.sv:203$3.do_pack[0:0]$641"

    assert wire_names(name) == (name,)
    assert signal_value(name, {name: 1}, {name: 1}) == (1, None)
    # A separating space still denotes an actual RTLIL selection.
    assert wire_names(name + " [0]") == (name,)
    assert signal_value(name + " [0]", {name: 1}, {name: 1}) == (1, None)


def test_a_concatenated_switch_has_one_shared_symbolic_and_concrete_value():
    """RTLIL prints a packed case key as a SigSpec concatenation, not a new wire."""

    from behave.sim.rtl.catalogue import Site
    from behave.sim.rtl.site_target import predicate_for, site_hits

    site = Site(
        "m", "$proc$1", r"{ \hi \lo [2:1] 2'01 }", "m.sv:2.1-2.2",
        "5'10101", 1, (), siblings=("5'10101", "default"),
    )
    hi = z3.BitVec("hi", 1)
    lo = z3.BitVec("lo", 4)

    class Explorer:
        @staticmethod
        def value_var(name):
            return {"hi": (hi, 1), "lo": (lo, 4)}.get(name, (None, None))

    predicate = predicate_for(Explorer(), site)
    assert z3.is_bool(predicate)
    solver = z3.Solver()
    solver.add(predicate, hi == 1, lo == 2)
    assert solver.check() == z3.sat
    solver = z3.Solver()
    solver.add(predicate, hi == 0, lo == 2)
    assert solver.check() == z3.unsat
    assert site_hits(
        (site,), {"hi": 1, "lo": 2}, {"hi": 1, "lo": 4},
    ) == ({site.goal}, {})


def test_a_concatenation_without_a_sampled_wire_width_is_undecidable():
    from behave.sim.rtl.catalogue import Site
    from behave.sim.rtl.site_target import site_hits

    site = Site(
        "m", "$proc$1", r"{ \hi \lo }", "m.sv:3.1-3.2", "2'10", 1,
        (), siblings=("2'10", "default"),
    )
    hits, refused = site_hits((site,), {"hi": 1, "lo": 0})
    assert not hits
    assert "does not carry the width" in refused[site.goal]


def test_an_unsized_integer_switch_is_a_constant_not_a_wire():
    """Pre-proc RTLIL emits ``switch 0`` for an elaborated integer argument."""

    from behave.sim.rtl.catalogue import Site
    from behave.sim.rtl.site_target import predicate_for, site_hits

    zero = Site(
        "m", "$proc$1", "0", "m.sv:4.1-4.2", "0", 1, (),
        siblings=("0", "1", "default"),
    )
    one = Site(
        "m", "$proc$1", "0", "m.sv:4.3-4.4", "1", 1, (),
        siblings=("0", "1", "default"),
    )

    class Explorer:
        @staticmethod
        def value_var(_name):
            raise AssertionError("an integer constant must not request a wire")

    assert z3.is_true(z3.simplify(predicate_for(Explorer(), zero)))
    assert z3.is_false(z3.simplify(predicate_for(Explorer(), one)))
    assert site_hits((zero, one), {}) == ({zero.goal}, {})


# ── and what it refuses ──

# `flip_branch` is now a wrapper over `reach`, and its goal string and result have to be
# unchanged because G1's baseline is counted from them. There is no test for that here on
# purpose: this design has no `br_vars`, so a test written against it would skip every
# run and read as coverage it is not providing. The rest of the suite drives `flip_branch`
# through `explore` on designs that do have branches.


# ── one rule, two renderings ──

def test_the_symbolic_and_the_concrete_reading_of_a_label_are_one_rule():
    """`match_rule` is the record; `_condition` and `matches` are its renderings.

    The point of factoring it is not tidiness. The hit component of the relation-versus-
    simulator audit compares "the relation says this site ran" against "the simulator
    says this site ran", and if the two sides each carried their own transcription of
    what a case label matches, the comparison would be between the transcriptions. Here
    the two are asked the same questions and required to agree.
    """
    import z3

    from behave.sim.rtl.case_match import _condition, matches

    var = z3.BitVec("s", 4)
    cases = [("2'10", ("2'10", "2'01"), [0, 1, 2, 3]),
             ("default", ("2'10", "2'01"), [0, 1, 2, 3]),
             ("1'1", ("1'1",), [0, 1])]
    for label, siblings, values in cases:
        term, why = _condition(var, 4, label, siblings)
        assert term is not None, why
        for value in values:
            concrete, bad = matches(label, siblings, value)
            assert concrete is not None, bad
            symbolic = z3.simplify(z3.substitute(
                term, (var, z3.BitVecVal(value, 4))))
            assert z3.is_true(symbolic) is concrete, (label, siblings, value)


@pytest.mark.parametrize("explicit", [1, 2, 4])
@pytest.mark.parametrize("has_default", [False, True])
def test_small_finite_switches_partition_values_in_concrete_and_exact_readings(
        explicit, has_default):
    """Every written arm plus default/no-match is one exhaustive, disjoint family."""
    from behave.sim.rtl.case_match import _condition, matches

    labels = tuple(f"3'{value:03b}" for value in range(explicit))
    tail = "default" if has_default else "no-match"
    siblings = labels + (tail,)
    var = z3.BitVec("finite_switch", 3)
    for value in range(8):
        concrete_hits = []
        exact_hits = []
        for arm in siblings:
            concrete, why = matches(arm, siblings, value)
            assert concrete is not None, why
            concrete_hits.append(concrete)
            term, why = _condition(var, 3, arm, siblings)
            assert term is not None, why
            exact_hits.append(z3.is_true(z3.simplify(z3.substitute(
                term, (var, z3.BitVecVal(value, 3))))))
        assert concrete_hits == exact_hits
        assert sum(concrete_hits) == 1


def test_one_arm_may_name_several_finite_patterns():
    from behave.sim.rtl.case_match import _condition, matches

    siblings = ("3'001 3'011", "default")
    var = z3.BitVec("multi_pattern", 3)
    for value in range(8):
        concrete, why = matches(siblings[0], siblings, value)
        assert concrete is not None, why
        symbolic, why = _condition(var, 3, siblings[0], siblings)
        assert symbolic is not None, why
        assert concrete is z3.is_true(z3.simplify(z3.substitute(
            symbolic, (var, z3.BitVecVal(value, 3)))))


def test_overlapping_multi_pattern_arms_follow_first_match_priority():
    from behave.sim.rtl.site_target import matches

    siblings = ("3'001 3'011", "3'011 3'100", "default")
    assert matches(siblings[0], siblings, 3)[0] is True
    assert matches(siblings[1], siblings, 3)[0] is False
    assert matches(siblings[1], siblings, 4)[0] is True
    assert matches("default", siblings, 3)[0] is False
    assert matches("default", siblings, 7)[0] is True


def test_concrete_exact_parity_check_detects_a_concrete_renderer_mutation(
        monkeypatch):
    """The parity assertion is sensitive to either rendering, not just shared data."""
    from behave.sim.rtl import case_match

    original = case_match.matches

    def inverted(label, siblings, value):
        got, why = original(label, siblings, value)
        return (None if got is None else not got), why

    monkeypatch.setattr(case_match, "matches", inverted)
    siblings = ("2'00", "2'01", "default")
    var = z3.BitVec("mutated_switch", 2)
    mismatches = 0
    for value in range(4):
        concrete, _why = case_match.matches("2'01", siblings, value)
        symbolic, _why = case_match._condition(var, 2, "2'01", siblings)
        exact = z3.is_true(z3.simplify(z3.substitute(
            symbolic, (var, z3.BitVecVal(value, 2)))))
        mismatches += concrete != exact
    assert mismatches == 4


def test_concrete_exact_parity_check_detects_an_exact_renderer_mutation(
        monkeypatch):
    """Changing only the symbolic rendering breaks the same parity property."""
    from behave.sim.rtl import case_match

    original = case_match._condition

    def inverted(var, width, label, siblings):
        term, why = original(var, width, label, siblings)
        return (None if term is None else z3.Not(term)), why

    monkeypatch.setattr(case_match, "_condition", inverted)
    siblings = ("2'00", "2'01", "default")
    var = z3.BitVec("mutated_exact_switch", 2)
    mismatches = 0
    for value in range(4):
        concrete, _why = case_match.matches("2'01", siblings, value)
        symbolic, _why = case_match._condition(var, 2, "2'01", siblings)
        exact = z3.is_true(z3.simplify(z3.substitute(
            symbolic, (var, z3.BitVecVal(value, 2)))))
        mismatches += concrete != exact
    assert mismatches == 4


def test_multiway_concrete_and_exact_paths_attribute_the_same_occurrence():
    from behave.sim.rtl.catalogue import Site
    from behave.sim.rtl.site_target import predicate_for, site_hits

    siblings = ("2'00", "2'01", "default")
    sites = tuple(Site(
        "m", "p", "\\sel", f"f.sv:{line}.1-{line}.2", outcome, 1, (),
        siblings) for line, outcome in enumerate(siblings, 1))
    var = z3.BitVec("sel_occurrence", 2)

    class Explorer:
        @staticmethod
        def value_var(name):
            assert name == "sel"
            return var, 2

    for value in range(4):
        concrete, refused = site_hits(sites, {"sel": value})
        assert not refused and len(concrete) == 1
        exact = {
            site.goal for site in sites
            if z3.is_true(z3.simplify(z3.substitute(
                predicate_for(Explorer(), site),
                (var, z3.BitVecVal(value, 2)))))
        }
        assert exact == concrete


def test_a_value_that_is_not_a_number_does_not_decide_a_case():
    """An X, or a handle that is not a scalar. Resolving either to 0 would invent the
    answer, and answering `False` would report the site as missed -- the false negative
    the activation work exists to stop."""
    from behave.sim.rtl.site_target import matches

    got, why = matches("1'1", ("1'1",), "0x")
    assert got is None and "not a number" in why


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_a_site_the_reading_cannot_decide_is_refused_and_not_reported_missed(bench):
    """`site_hits` over a reading that is missing the switched signal."""
    from behave.sim.rtl.site_target import site_hits

    _src, sites, _tmp = bench
    hits, refused = site_hits(sites, {})
    unconditional = {
        site.goal for site in sites.values()
        if site.kind == "location" and not site.activation
    }
    undecidable = {site.goal for site in sites.values()} - unconditional
    assert hits == unconditional
    assert set(refused) == undecidable, \
        "an undecidable guarded site must not read as a site that did not run"
    assert all("does not carry" in why for why in refused.values())
