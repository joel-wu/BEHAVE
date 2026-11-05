"""Finite RTL case patterns: independent truth tables and the two renderers."""

import pytest

from behave.sim.rtl.case_match import _condition, match_rule, matches

pytestmark = pytest.mark.solver


@pytest.mark.parametrize(("siblings", "winners"), (
    (("3'001 , 3'011", "default"), (1, 0, 1, 0, 1, 1, 1, 1)),
    (("3'1--", "3'-1-", "default"), (2, 2, 1, 1, 0, 0, 0, 0)),
    (("3'1--", "3'001,3'011", "no-match"), (2, 1, 2, 1, 0, 0, 0, 0)),
    (("3'---", "3'001", "default"), (0,) * 8),
    (("3'000", "default", "3'001"), (0, 1, 1, 1, 1, 1, 1, 1)),
))
@pytest.mark.parametrize("sort", ("bv", "int"))
def test_case_priority_matches_an_independent_truth_table(siblings, winners, sort):
    import z3

    var = z3.BitVec("case_input", 3) if sort == "bv" else z3.Int("case_input")
    for value, winner in enumerate(winners):
        observed = []
        for index, arm in enumerate(siblings):
            concrete, why = matches(arm, siblings, value)
            assert concrete is (index == winner), why
            term, why = _condition(var, 3, arm, siblings)
            assert term is not None, why
            numeral = z3.BitVecVal(value, 3) if sort == "bv" else z3.IntVal(value)
            symbolic = z3.simplify(z3.substitute(term, (var, numeral)))
            assert z3.is_true(symbolic) is concrete
            observed.append(concrete)
        assert sum(observed) == 1


def test_masked_rule_does_not_enumerate_the_matching_values():
    import z3

    label = "128'1" + "-" * 127
    siblings = (label, "default")
    kind, payload, why = match_rule(label, siblings)
    assert kind is not None, why
    assert len(repr(payload)) < 1000
    var = z3.BitVec("wide_case", 128)
    term, why = _condition(var, 128, label, siblings)
    assert term is not None, why
    solver = z3.Solver()
    solver.add(term != (z3.Extract(127, 127, var) == 1))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("label", ("3'00x", "3'0z1", "3'0m1", "3'001,", ",3'001"))
def test_unknown_or_malformed_patterns_are_not_dont_cares(label):
    kind, _payload, why = match_rule(label, (label, "default"))
    assert kind is None and why


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_real_rtlil_casez_and_comma_arms_have_a_complete_catalogue(tmp_path):
    from behave.sim.rtl.catalogue import catalogue_manifest_of

    design = tmp_path / "patterns.sv"
    design.write_text("""module patterns(input wire [2:0] in_x, output reg [1:0] out_y);
        always @* casez (in_x)
            3'b1??: out_y = 1;
            3'b001, 3'b011: out_y = 2;
            default: out_y = 0;
        endcase
    endmodule
    """)
    manifest, sites = catalogue_manifest_of(design, "patterns")
    assert len(manifest) > 0
    arms = [site for site in sites.values() if site.signal == r"\in_x"]
    assert any("-" in site.outcome for site in arms)
    assert any("," in site.outcome for site in arms)
    for value, expected_label in enumerate(("default", "comma", "default", "comma",
                                             "mask", "mask", "mask", "mask")):
        taken = [site for site in arms if matches(site.outcome, site.siblings, value)[0]]
        assert len(taken) == 1
        outcome = taken[0].outcome
        found = "mask" if "-" in outcome else "comma" if "," in outcome else "default"
        assert found == expected_label
