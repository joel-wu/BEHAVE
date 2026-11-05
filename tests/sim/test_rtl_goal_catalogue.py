"""The closed finite-switch catalogue used by concrete and exact RTL evidence.

Every elaborated switch arm has the same ``case-arm`` family, independent of arity.  A
switch without a written default receives one synthetic ``no-match`` arm, so the family
is exhaustive.  The provider either freezes all of those occurrences under its explicit
resource policy or refuses the whole frontend catalogue; it never changes the
denominator by declining individual arms.
"""
import pytest

from behave.sim.core.refusal import answered
from behave.sim.rtl.catalogue import (
    CASE_ARM,
    DECISION,
    DEFAULT_STRUCTURAL_POLICY,
    LOCATION,
    POLICY,
    POLICY_VERSION,
    Site,
    StructuralGoalPolicy,
    family_of,
    occurrence_key_of,
    shape_of,
)


def site(outcome, siblings, signal="\\a", activation=()):
    return Site("m", "p", signal, "f.sv:1.1-1.2", outcome, 1 + len(activation),
                activation, siblings)


def location(action="$0\\y 1'1", activation=(), triggers=(("always", ""),)):
    return Site(
        "m", "p", "1'1", "f.sv:2.1-2.8", "entry",
        len(activation), activation, kind=LOCATION, action=action,
        triggers=triggers)


# ── the arity, and what it decides ──

def test_every_finite_arm_has_the_one_case_arm_family():
    for outcome in ("1'1", "default"):
        got = site(outcome, ("1'1", "default"))
        assert shape_of(got) == "binary-with-default"
        assert family_of(got) == CASE_ARM == DECISION


def test_an_elaborated_action_is_one_location_occurrence():
    got = location()

    assert family_of(got) == LOCATION
    assert shape_of(got) == LOCATION
    assert got.outcome == "entry"
    assert got.target.startswith("rtl-location:")
    assert occurrence_key_of(got).role == "entry"


@pytest.mark.solver
def test_unrolled_action_occurrences_share_one_public_goal():
    import z3

    from behave.sim.rtl.catalogue import _goal_catalogue_from_sites
    from behave.sim.rtl.site_target import coverage_of, site_hits

    first = Site(
        "m", "p", "1'1", "f.sv:9.1-9.8", "entry", 1,
        (("\\a", "1'1"),), kind=LOCATION,
        action="$0\\row[0] \\input[0]")
    second = Site(
        "m", "p", "1'1", "f.sv:9.1-9.8", "entry", 1,
        (("\\b", "1'1"),), kind=LOCATION,
        action="$1\\row[1] \\input[1]")

    goals, occurrences = _goal_catalogue_from_sites(
        (first, second))

    assert len(goals) == 1
    assert len(occurrences) == 2
    assert first.goal == second.goal == next(iter(goals))
    assert first.target != second.target
    assert site_hits((first, second), {"a": 0, "b": 1}) == (
        {first.goal}, {})
    assert site_hits((first, second), {"b": 1}) == ({first.goal}, {}), \
        "one proved occurrence is enough even when another is unreadable"
    hits, refused = site_hits((first, second), {"b": 0})
    assert not hits and set(refused) == {first.goal}

    a, b = z3.Bools("unrolled_a unrolled_b")

    class Explorer:
        @staticmethod
        def value_var(name):
            return {"a": (a, 1), "b": (b, 1)}.get(name, (None, None))

    exact, refused = coverage_of(Explorer(), (first, second))
    assert not refused and set(exact) == {first.goal}
    for av, bv in ((False, False), (False, True), (True, False), (True, True)):
        value = z3.simplify(z3.substitute(
            exact[first.goal], (a, z3.BoolVal(av)), (b, z3.BoolVal(bv))))
        assert z3.is_true(value) is (av or bv)


@pytest.mark.solver
def test_location_entry_uses_the_same_concrete_and_exact_activation():
    import z3

    from behave.sim.rtl.site_target import predicate_for, site_hits

    got = location(activation=(("\\a", "1'1"),))

    class Explorer:
        a = z3.Bool("a")

        @classmethod
        def value_var(cls, name):
            return (cls.a, 1) if name == "a" else (None, None)

    predicate = predicate_for(Explorer(), got)
    assert z3.is_true(z3.simplify(z3.substitute(predicate, (Explorer.a, z3.BoolVal(True)))))
    assert z3.is_false(z3.simplify(z3.substitute(predicate, (Explorer.a, z3.BoolVal(False)))))
    assert site_hits((got,), {"a": 1}) == ({got.goal}, {})
    assert site_hits((got,), {"a": 0}) == (set(), {})


def test_a_clocked_location_is_hit_only_in_its_process_event_slot():
    from behave.sim.rtl.site_target import site_hits

    got = location(triggers=(("posedge", "\\a_clk"),))

    assert site_hits(
        (got,), {}, active_events={("rising", "a_clk")}) == ({got.goal}, {})
    assert site_hits(
        (got,), {}, active_events={("rising", "b_clk")}) == (set(), {})


def test_rtlil_reader_emits_case_rule_assignments_as_locations():
    from behave.sim.rtl.catalogue import sites_of

    sites = sites_of(r'''module \top
  process $proc
    assign $0\y \y
    switch \a
      case 1'1
        assign $0\y 1'1
    end
    sync posedge \clk
      update \y $0\y
  end
end
''')
    locations = [item for item in sites if item.kind == LOCATION]

    assert [item.action for item in locations] == ["$0\\y 1'1"]
    assert locations[0].activation == (("\\a", "1'1"),)
    assert {item.triggers for item in sites} == {(('posedge', '\\clk'),)}


def test_rtlil_locations_exclude_holds_and_compiler_temporaries():
    from behave.sim.rtl.catalogue import sites_of

    sites = sites_of(r'''module \top
  process $proc
    assign $0\y \y
    assign $4\helper$func$f.sv:3$1.tmp 1'0
    assign $logic$f.sv:4$2_Y 1'0
    assign $0\y 1'1
    sync posedge \clk
      update \y $0\y
  end
end
''')

    assert [site.action for site in sites if site.kind == LOCATION] == [
        "$0\\y 1'1"]


def test_a_structural_process_without_a_runtime_trigger_is_refused():
    from behave.sim.rtl.catalogue import sites_of

    with pytest.raises(ValueError, match="no closed sync trigger"):
        sites_of(r'''module \top
  process $proc
    switch \a
      case 1'1
    end
  end
end
''')


@pytest.mark.parametrize("scaffold", ["", "    sync always\n"])
def test_initialization_actions_are_not_event_slot_locations(scaffold):
    from behave.sim.rtl.catalogue import sites_of

    rtlil = r'''module \top
  process $proc
    assign $0\y 1'0
    sync init
      update \y $0\y
  end
end
'''
    assert sites_of(rtlil.replace("    sync init", scaffold + "    sync init")) == []


def test_initial_scaffold_rule_preserves_real_combinational_and_clock_events():
    from behave.sim.rtl._rtlil_sites import process_syncs, runtime_syncs

    rtlil = r'''module \top
  process $init
    assign $0\x 1'0
    sync always
    sync init
      update \x $0\x
  end
  process $comb
    assign $0\y \a
    sync always
      update \y $0\y
    sync init
      update \y 1'0
  end
  process $clock
    sync posedge \clk
    sync init
      update \q 1'0
  end
  process $empty_comb
    sync always
  end
end
'''
    phases = process_syncs(rtlil)
    assert runtime_syncs(phases[("top", "$init")]) == ()
    assert runtime_syncs(phases[("top", "$comb")]) == (("always", ""),)
    assert runtime_syncs(phases[("top", "$clock")]) == (("posedge", "\\clk"),)
    assert runtime_syncs(phases[("top", "$empty_comb")]) == (("always", ""),)


def test_multiway_single_and_no_default_arms_share_that_family():
    got = site("8'00000011", tuple(f"8'{i:08b}" for i in range(255)) +
               ("default",))
    assert shape_of(got) == "multiway"
    assert family_of(got) == CASE_ARM
    got = site("no-match", ("1'1", "no-match"))
    assert shape_of(got) == "binary-no-default"
    assert family_of(got) == CASE_ARM


def test_a_site_with_no_siblings_recorded_declares_nothing():
    """The pre-`siblings` shape. It must not read as a decision by default, or every
    site built the old way would be declared under a policy it was never checked
    against."""
    got = site("1'1", ())
    assert shape_of(got) == "no-labels"
    assert not answered(family_of(got))


def test_an_unreadable_arm_is_a_typed_refusal_with_its_rule_reason():
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl.catalogue import _validate_structural_sites

    siblings = ("not-a-constant", "default")
    sites = (site("not-a-constant", siblings), site("default", siblings))

    with pytest.raises(Refused) as caught:
        _validate_structural_sites(sites, DEFAULT_STRUCTURAL_POLICY)

    assert caught.value.refusal.kind == "rtl-structural-arm-unreadable"
    assert "plain constant" in caught.value.refusal.detail["rule_reason"]


def test_repeated_elaborated_instances_have_distinct_stable_occurrences():
    first = site("1'1", ("1'1", "default"), signal="\\u0.a")
    second = site("1'1", ("1'1", "default"), signal="\\u1.a")

    assert first.target != second.target
    assert occurrence_key_of(first) != occurrence_key_of(second)
    assert occurrence_key_of(first) == occurrence_key_of(
        site("1'1", ("1'1", "default"), signal="\\u0.a"))


# ── siblings come off the RTLIL, not off a guess ──

@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_siblings_are_read_from_the_switch(tmp_path):
    from behave.sim.rtl.catalogue import read_sites

    design = tmp_path / "shapes.sv"
    design.write_text("""module shapes (
    input wire clk, input wire a, input wire [1:0] sel, output reg [3:0] y
);
    always @(posedge clk) begin
        if (a) y <= 4'd1;
        else   y <= 4'd2;
        case (sel)
            2'b00: y <= 4'd3;
            2'b01: y <= 4'd4;
            2'b10: y <= 4'd5;
            default: y <= 4'd6;
        endcase
    end
endmodule
""")
    sites = read_sites(str(design), "shapes")
    assert sites, "the reader found no control sites at all"

    by_shape = {}
    for s in sites:
        by_shape.setdefault(shape_of(s), []).append(s)

    assert "binary-with-default" in by_shape, \
        f"the if/else was not read as two-way: {sorted(by_shape)}"
    assert "multiway" in by_shape, \
        f"the four-way case was not read as multiway: {sorted(by_shape)}"
    # And every site of one switch agrees about the switch it belongs to.
    for s in by_shape["multiway"]:
        assert len(s.siblings) >= 3
        assert s.outcome in s.siblings, "a site missing from its own switch's labels"


def test_sequential_identical_switches_keep_distinct_stable_occurrences():
    from behave.sim.rtl.catalogue import (
        _validate_structural_sites,
        sites_of,
    )

    one = r'''module \top
  process $proc
    attribute \src "design.sv:7.1-7.9"
    switch \index
      case 2'00
    end
    attribute \src "design.sv:7.1-7.9"
    switch \index
      case 2'00
    end
    sync always
      update \index \index
  end
end
'''
    prefixed = one.replace(
        "    attribute \\src \"design.sv:7.1-7.9\"\n",
        "    attribute \\src \"design.sv:6.1-6.9\"\n"
        "    switch \\other\n"
        "      case 1'1\n"
        "    end\n"
        "    attribute \\src \"design.sv:7.1-7.9\"\n",
        1,
    )

    sites = sites_of(one)
    repeated = sites_of(one)
    after_unrelated = [site for site in sites_of(prefixed)
                       if site.signal == "\\index"]
    switch_ids = tuple(dict.fromkeys(site.switch_id for site in sites))

    assert len(switch_ids) == 2
    assert switch_ids == tuple(dict.fromkeys(
        site.switch_id for site in repeated))
    assert switch_ids == tuple(dict.fromkeys(
        site.switch_id for site in after_unrelated))
    assert len({site.target for site in sites}) == len(sites)
    assert _validate_structural_sites(
        sites, DEFAULT_STRUCTURAL_POLICY) == tuple(sites)


# ── the goals themselves ──

@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_a_declared_goal_carries_its_policy_and_its_provenance(tmp_path):
    from behave.sim.core.catalogue import COVERAGE
    from behave.sim.rtl.catalogue import catalogue_manifest_of

    design = tmp_path / "nested.sv"
    design.write_text("""module nested (
    input wire clk, input wire rst, input wire a, input wire b, output reg y
);
    always @(posedge clk) begin
        if (rst) y <= 1'b0;
        else if (a) begin
            if (b) y <= 1'b1;
        end
    end
endmodule
""")
    goals, _sites = catalogue_manifest_of(str(design), "nested")
    assert goals

    one = next(goal for goal in goals.values() if goal.family == CASE_ARM)
    assert one.cls == COVERAGE, "no safety family here: todo3 has no RTL error provider"
    assert one.family == CASE_ARM
    assert one.provider == "rtl-structural"
    assert one.policy == DEFAULT_STRUCTURAL_POLICY.identity
    # `provenance` is sorted pairs rather than a mapping: the goal is frozen because its
    # identity is a digest over it, and a frozen record holding a dict is a record whose
    # contents are still anyone's to change. `where` is the read side of that.
    carried = dict(one.provenance)
    for key in (
            "module", "signal", "src", "outcome", "triggers", "occurrences",
            "match_rule"):
        assert key in carried, f"{key} missing from provenance"
    assert "depth" not in carried and "activation" not in carried, \
        "a grouped public goal must not inherit one representative occurrence's path"


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_manifest_binds_each_declared_goal_to_its_elaborated_occurrence(tmp_path):
    from behave.sim.core.catalogue import OccurrenceKey
    from behave.sim.rtl.catalogue import catalogue_manifest_of

    design = tmp_path / "occurrences.sv"
    design.write_text("""module occurrences (
    input wire clk, input wire a, output reg y
);
    always @(posedge clk) begin
        if (a) y <= 1'b1;
        else y <= 1'b0;
    end
endmodule
""")
    manifest, sites = catalogue_manifest_of(
        str(design), "occurrences")

    assert manifest
    assert {site.goal for site in sites.values()} == set(manifest)
    assert len(manifest.occurrences) == len(sites)
    by_occurrence = {occurrence_key_of(site): site for site in sites.values()}
    for occurrence, goal in manifest.occurrences:
        assert type(occurrence) is OccurrenceKey
        assert occurrence.provider == "rtl-structural"
        assert goal in manifest
        assert by_occurrence[occurrence].outcome == occurrence.role


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_artifact_freezes_the_manifest_against_its_admitted_source(tmp_path):
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl.artifact import RTLArtifact

    design = tmp_path / "frozen.sv"
    design.write_text("""module frozen (
    input wire clk, input wire a, output reg y
);
    always @(posedge clk) begin
        if (a) y <= 1'b1;
        else y <= 1'b0;
    end
endmodule
""")
    artifact = RTLArtifact(design, "frozen", tmp_path / "work")
    admitted = artifact.identity
    manifest = artifact.frontend_catalogue(expected_identity=admitted)
    assert manifest is artifact.frontend_catalogue(expected_identity=admitted)

    design.write_text(design.read_text() + "\n// changed after admission\n")
    with pytest.raises(Refused, match="rtl-catalogue-artifact-mismatch"):
        artifact.frontend_catalogue(expected_identity=admitted)


def test_artifact_uses_its_elaboration_budget_for_the_catalogue(
        tmp_path, monkeypatch):
    from behave.sim.core.catalogue import CatalogueManifest
    from behave.sim.rtl.artifact import RTLArtifact

    design = tmp_path / "budgeted.sv"
    design.write_text("module budgeted; endmodule\n")
    seen = []

    class Execution:
        @staticmethod
        def as_dict():
            return {"status": "completed"}

    def catalogue(_path, _top, *, timeout, source_rules):
        assert not source_rules
        seen.append(timeout)
        return CatalogueManifest(()), Execution()

    monkeypatch.setattr(
        "behave.sim.rtl.catalogue.source_catalogue_with_execution_of",
        catalogue)
    artifact = RTLArtifact(
        design, "budgeted", tmp_path / "work", timeout_s=347)

    assert artifact.frontend_catalogue(
        expected_identity=artifact.identity) == CatalogueManifest(())
    assert seen == [347]


def test_the_policy_is_not_the_behavior_sides(tmp_path):
    """Each frontend records its own source projection in every goal."""
    from behave.sim.behavior import goals as behavior_goals

    assert POLICY != behavior_goals.POLICY
    assert POLICY_VERSION != behavior_goals.POLICY_VERSION


def test_multiway_structural_semantics_have_new_pinned_identities():
    """Multiway arms and synthetic no-match changed declaration and hit semantics.

    Reusing the binary-only identities would make old policy/capability records look
    comparable to the exhaustive finite-switch implementation.  The next semantic
    change must move these pins again. Version 9 selects concrete instances
    before collecting occurrences, rather than one row per module definition.
    """
    from behave.sim.core.goal_provider import (
        RTL_EXACT_BOUNDED_POLICY,
        RTL_LOCAL_GUIDANCE_POLICY,
    )
    from behave.sim.goal_policy import EVALUATION_GOAL_POLICY_VERSION
    from behave.sim.rtl._rtlil_sites import RTLIL_SITES_VERSION

    assert RTLIL_SITES_VERSION == "rtlil-sites/9"
    assert POLICY_VERSION == "rtl-structural/10"
    assert EVALUATION_GOAL_POLICY_VERSION == "evaluation-goals/27"
    assert RTL_EXACT_BOUNDED_POLICY == "rtl-task-bounded-hit/3"
    assert RTL_LOCAL_GUIDANCE_POLICY.startswith("rtl-exact-local/30:")
    assert '+scope-owned-construction+' in RTL_LOCAL_GUIDANCE_POLICY
    assert RTL_LOCAL_GUIDANCE_POLICY.endswith(
        "access-domain-proof+operation-predicates+audited-cone-carriers"
        "+representation-completion")


@pytest.mark.eda("yosys")
@pytest.mark.isolation
def test_a_multiway_design_declares_every_arm(tmp_path):
    from behave.sim.rtl.catalogue import catalogue_manifest_of

    design = tmp_path / "lut.sv"
    design.write_text("""module lut (
    input wire clk, input wire [2:0] sel, output reg [7:0] y
);
    always @(posedge clk) begin
        case (sel)
            3'd0: y <= 8'd10;
            3'd1: y <= 8'd11;
            3'd2: y <= 8'd12;
            3'd3: y <= 8'd13;
            3'd4: y <= 8'd14;
            default: y <= 8'd0;
        endcase
    end
endmodule
""")
    goals, _sites = catalogue_manifest_of(str(design), "lut")
    case_goals = [goal for goal in goals.values() if goal.family == CASE_ARM]
    location_goals = [goal for goal in goals.values() if goal.family == LOCATION]
    assert len(case_goals) == 6
    assert location_goals
    assert {goal.family for goal in goals.values()} == {CASE_ARM, LOCATION}


def test_policy_bounds_refuse_the_whole_catalogue_before_freeze():
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl.catalogue import _goal_catalogue_from_sites

    with pytest.raises(Refused) as stopped:
        _goal_catalogue_from_sites(
            tuple(site(outcome, ("2'00", "2'01", "default"))
                  for outcome in ("2'00", "2'01", "default")),
            StructuralGoalPolicy(max_arms_per_switch=2))

    assert stopped.value.refusal.kind == "rtl-switch-arm-policy-bound"

    with pytest.raises(Refused) as stopped:
        _goal_catalogue_from_sites(
            tuple(site(outcome, ("2'00", "2'01", "default"))
                  for outcome in ("2'00", "2'01", "default")),
            StructuralGoalPolicy(max_structural_goals=2))
    assert stopped.value.refusal.kind == "rtl-structural-goal-policy-bound"


def test_collision_classification_does_not_depend_on_switch_arity():
    """A collision is ambiguous evidence for binary and multiway arms alike."""
    import collections

    from behave.sim.rtl import catalogue as cat

    twin = ("1'1", "default")
    wide = tuple(f"3'{i:03b}" for i in range(6))
    made = [site("1'1", twin), site("1'1", twin),
            site("3'000", wide), site("3'000", wide),
            site("1'1", twin, signal="\\solo")]

    by_target = collections.defaultdict(list)
    for s in made:
        by_target[s.target].append(s)
    groups = {t: g for t, g in by_target.items() if len(g) > 1}
    assert len(groups) == 2, "the fixture did not produce the two collision kinds"

    assert all(answered(cat.family_of(s)) for g in groups.values() for s in g)


def test_a_public_goal_digest_collision_is_refused(monkeypatch):
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl import catalogue as cat

    rows = (
        site("1'1", ("1'1", "default"), signal="\\a"),
        site("default", ("1'1", "default"), signal="\\a"),
        site("1'1", ("1'1", "default"), signal="\\b"),
        site("default", ("1'1", "default"), signal="\\b"),
    )
    monkeypatch.setattr(cat, "goal_id_of", lambda _site: "forced-collision")
    with pytest.raises(Refused) as stopped:
        cat._goal_catalogue_from_sites(rows)
    assert stopped.value.refusal.kind == "rtl-structural-identifier-collision"


def test_manifest_construction_cannot_publish_a_colliding_subset(monkeypatch):
    from behave.sim.core import catalogue as records
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl import catalogue as cat

    rows = tuple(site(outcome, ("1'1", "default"))
                 for outcome in ("1'1", "default"))
    source = records.CatalogueManifest(())
    monkeypatch.setattr(cat, "read_frontend_with_execution", lambda *a, **kw: (rows, source, None))
    monkeypatch.setattr(cat, "goal_id_of", lambda _site: "forced-collision")

    def premature_manifest(*_args, **_kwargs):
        raise AssertionError("a manifest was constructed before rejecting its collision")

    monkeypatch.setattr(records, "CatalogueManifest", premature_manifest)
    with pytest.raises(Refused, match="rtl-structural-identifier-collision"):
        cat.catalogue_manifest_of("fixture.sv", "m")


def test_arm_order_changes_public_and_occurrence_identity():
    from behave.sim.core.catalogue import CatalogueManifest
    from behave.sim.rtl.catalogue import _goal_catalogue_from_sites

    first = site("2'01", ("2'00", "2'01", "default"))
    reordered = site("2'01", ("default", "2'01", "2'00"))
    assert first.target != reordered.target
    assert occurrence_key_of(first) != occurrence_key_of(reordered)

    def manifest(labels):
        goals, _declared = _goal_catalogue_from_sites(
            tuple(site(outcome, labels) for outcome in labels))
        return CatalogueManifest(tuple(goals.values()))

    assert manifest(("2'00", "2'01", "default")).digest() != \
        manifest(("default", "2'01", "2'00")).digest()


def test_policy_limits_and_no_match_rule_are_in_catalogue_identity():
    from behave.sim.core.catalogue import CatalogueManifest
    from behave.sim.goal_policy import builtin_frontend_goal_providers
    from behave.sim.rtl.catalogue import _goal_catalogue_from_sites

    default = DEFAULT_STRUCTURAL_POLICY
    changed_limit = StructuralGoalPolicy(
        max_arms_per_switch=default.max_arms_per_switch - 1)
    changed_rule = StructuralGoalPolicy(no_match_policy="synthetic-no-match/2")
    assert len({default.identity, changed_limit.identity, changed_rule.identity}) == 3

    sites = tuple(site(outcome, ("1'1", "default"))
                  for outcome in ("1'1", "default"))
    manifests = []
    provider_images = []
    for policy in (default, changed_limit, changed_rule):
        goals, _declared = _goal_catalogue_from_sites(
            sites, policy)
        manifest = CatalogueManifest(tuple(goals.values()))
        manifests.append(manifest.digest())
        provider_images.append(builtin_frontend_goal_providers(manifest)[0].as_dict())
    assert len(set(manifests)) == 3
    assert len({row["catalogue_digest"] for row in provider_images}) == 3
    assert {row["identity"]["version"] for row in provider_images} == {3}
