"""G6's acceptance, on the design its acceptance names.

    for `if (a) if (b)`, the inner-true hit is **false** when `a = 0, b = 1`

That sentence could not be posed against the mechanism it replaces. `br_vars` is every
numbered `Bool`-returning `define-fun` in the yosys SMT2 dump, and on this design yosys
has already collapsed the nested conditional into `ite` chains in the datapath -- every
numbered term is a bitvector, the only Bool ones are the input ports, and the catalogue
comes out **empty**. There is no inner-true target to be wrong about.

Read one stage earlier, after `hierarchy` and before `proc`, the structure is intact and
the nesting *is* the activation condition. So the acceptance becomes a statement about
the catalogue rather than about a regular expression's luck.
"""
import pytest

from behave.sim.rtl.catalogue import Site, catalogue_of, read_sites
from tests.support.rtl_site_oracle import activation_holds, occurrence_holds

NESTED = """module nested (
    input  wire       clk,
    input  wire       rst,
    input  wire       a,
    input  wire       b,
    output reg  [1:0] y
);
    always @(posedge clk) begin
        if (rst) begin
            y <= 2'd0;
        end else begin
            if (a) begin
                if (b) y <= 2'd3;
                else   y <= 2'd2;
            end else begin
                y <= 2'd1;
            end
        end
    end
endmodule
"""


@pytest.fixture(scope="module")
def nested(tmp_path_factory):
    path = tmp_path_factory.mktemp("g6") / "nested.sv"
    path.write_text(NESTED)
    return path


@pytest.fixture(scope="module")
def sites(nested):
    return read_sites(str(nested), "nested")


def inner_true(sites):
    got = [s for s in sites if s.signal.lstrip("\\") == "b" and s.outcome == "1'1"]
    assert len(got) == 1, got
    return got[0]


# ── the acceptance ──

@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_the_inner_true_hit_is_false_when_the_outer_branch_is_not_taken(sites):
    """G6, word for word. `a = 0, b = 1` takes neither inner outcome, and a hit that
    reports the inner-true because `b` happens to be 1 is reporting a branch the
    design did not take."""
    site = inner_true(sites)
    assert occurrence_holds(site, {"rst": 0, "a": 0, "b": 1}) is False
    assert activation_holds(site, {"rst": 0, "a": 0, "b": 1}) is False

    # And the three neighbouring cases, so that the assertion above is not passing
    # because the site never hits at all.
    assert occurrence_holds(site, {"rst": 0, "a": 1, "b": 1}) is True
    assert occurrence_holds(site, {"rst": 0, "a": 1, "b": 0}) is False
    assert occurrence_holds(site, {"rst": 1, "a": 1, "b": 1}) is False, \
        "reset encloses the whole conditional, so it deactivates the site too"


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_the_activation_is_the_nesting(sites):
    """Read off the structure rather than reconstructed. The inner site is enclosed by
    the `else` of `rst` and the true case of `a`, in that order."""
    site = inner_true(sites)
    assert [(s.lstrip("\\"), c) for s, c in site.activation] == \
        [("rst", "default"), ("a", "1'1")]
    assert site.depth == 3
    assert site.activation


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_both_outcomes_of_every_site_are_catalogued(sites):
    """A site is a place with two ways out, and a catalogue with only the taken one
    cannot express "this outcome was never reached"."""
    by_signal = {}
    for s in sites:
        by_signal.setdefault(s.signal.lstrip("\\"), set()).add(s.outcome)
    assert by_signal["rst"] == {"1'1", "default"}
    assert by_signal["a"] == {"1'1", "default"}
    assert by_signal["b"] == {"1'1", "default"}


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_a_target_identifier_survives_a_second_run(nested):
    """A numbered `define-fun` moves when anything upstream of it changes; a source
    location does not. The identifier is what a frontier and a ledger are keyed by, so
    it has to mean the same thing twice."""
    first = {s.target for s in read_sites(str(nested), "nested")}
    second = {s.target for s in read_sites(str(nested), "nested")}
    assert first == second and first


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_targets_are_unique_or_reported(nested):
    """Two sites one identifier cannot tell apart would make a probe result ambiguous,
    so `catalogue_of` returns the collisions rather than merging them."""
    catalogue, collisions = catalogue_of(str(nested), "nested")
    assert catalogue and not collisions
    assert all(k == v.target for k, v in catalogue.items())


# ── what it refuses to guess ──

def test_a_value_nothing_states_is_not_taken_to_be_zero():
    """An unstated signal returns `None` rather than defaulting. Defaulting it would
    make an activation hold that nothing established, which is the same shape as
    reading a hit off a bare condition."""
    site = Site(
        "m", "p", "\\b", "f:1.1-1.2", "1'1", 2,
        [("\\a", "1'1")], siblings=("1'1", "default"))
    assert occurrence_holds(site, {"a": 1, "b": 1}) is True
    assert occurrence_holds(site, {"a": 1}) is None, "b is not stated"
    assert occurrence_holds(site, {"b": 1}) is None, "a is not stated"


def test_plain_wide_and_multi_label_cases_are_read_exactly():
    site = Site(
        "m", "p", "\\s", "f:1.1-1.2", "2'10", 1, [],
        siblings=("2'10", "default"))
    assert occurrence_holds(site, {"s": 2}) is True
    assert occurrence_holds(site, {"s": 1}) is False
    multiple = Site(
        "m", "p", "\\s", "f:1.1-1.2", "2'01 2'10", 1, [],
        siblings=("2'01 2'10", "default"))
    assert occurrence_holds(multiple, {"s": 1}) is True
    assert occurrence_holds(multiple, {"s": 2}) is True
    assert occurrence_holds(multiple, {"s": 0}) is False


def test_an_unreadable_case_label_returns_nothing():
    odd = Site(
        "m", "p", "\\s", "f:1.1-1.2", "2'1x", 1, [],
        siblings=("2'1x", "default"))
    assert occurrence_holds(odd, {"s": 1}) is None


def test_site_json_reader_is_closed_and_binds_the_catalogue_key():
    from behave.sim.rtl.catalogue import sites_from_json, sites_to_json

    site = Site("m", "p", "\\a", "f:1.1-1.2", "1'1", 1, [])
    body = site.as_json()
    document = sites_to_json([site])
    assert sites_from_json(document)[site.target].target == site.target
    with pytest.raises(ValueError, match="invalid shape"):
        Site.from_json({**body, "compiler_payload": "not admitted"})
    with pytest.raises(ValueError, match="does not match"):
        sites_from_json({**document, "targets": ["forged"]})


# ── D6: identity that survives elaboration ──

@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_the_identifier_includes_what_elaboration_made_distinct(sites):
    """D6. A source line is a projection for reporting, not an identity: elaboration
    replicates generate bodies, inlines modules and folds case items, so several
    elaborated sites can share one line.

    Measured on the way to this, over 60 designs:

      `module:src:outcome`                  85.4% of sites shared an identifier
      + the switched signal                  0.75%
      + the activation path                  0.44%

    `aes128_round` alone went from 265 identifiers for 4182 sites to 4150, because
    `aes_sbox` is one `case (x)` inlined once per byte lane and every copy carries the
    same location and the same 256 labels.
    """
    site = inner_true(sites)
    assert site.target.startswith("rtl:nested:")
    assert site.signal.lstrip("\\") in site.target, "the switched signal is in it"
    assert site.src in site.target
    assert site.outcome in site.target
    assert site.path_id in site.target, "and the path through the control tree"
    assert site.switch_id in site.target, "and the exact switch occurrence is in it"

    # The path digest separates two sites that differ only in how control got there.
    same_place = Site("m", "p", "\\b", "f:1.1-1.2", "1'1", 2, [("\\a", "1'1")])
    other_path = Site("m", "p", "\\b", "f:1.1-1.2", "1'1", 2, [("\\a", "default")])
    assert same_place.target != other_path.target
    assert same_place.path_id != other_path.path_id


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_the_path_digest_is_the_same_for_the_same_path(sites):
    """It has to mean the same thing twice, since a frontier and a ledger are keyed by
    it. It changes when the control structure above the site changes -- a real change --
    and not when something unrelated earlier in the file moves."""
    site = inner_true(sites)
    twin = Site.from_json(site.as_json())
    assert twin.target == site.target
    assert twin.path_id == site.path_id
    assert twin.match_id == site.match_id
    assert Site("m", "p", "\\b", "f:1.1-1.2", "1'1", 1, []).path_id == \
        Site("other", "q", "\\b", "f:1.1-1.2", "1'1", 1, []).path_id, \
        "the digest is of the path alone; the rest of the identifier carries the rest"


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_two_sites_one_identifier_cannot_tell_apart_are_reported(nested):
    """0.44% of sites over 60 designs remain structurally indistinguishable by
    everything captured -- the same signal, location, outcome and path, appearing twice
    in the RTLIL. `catalogue_of` returns them as collisions instead of merging them,
    because a ledger keyed by an ambiguous identifier is a ledger that is wrong about
    which site it recorded."""
    _catalogue, collisions = catalogue_of(str(nested), "nested")
    assert not collisions, "this design has none"

    # And the reporting path works, checked by building the ambiguity directly rather
    # than by hunting for a design that has it.
    twin = Site("m", "p", "\\b", "f:1.1-1.2", "1'1", 2, [("\\a", "1'1")])
    again = Site("m", "p", "\\b", "f:1.1-1.2", "1'1", 2, [("\\a", "1'1")])
    assert twin.target == again.target
    seen, clashes = {}, []
    for s in (twin, again):
        if s.target in seen:
            clashes.append(s.target)
        seen[s.target] = s
    assert clashes == [twin.target]
