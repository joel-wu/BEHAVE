"""Shared payload policy, not name inference or an empirical coverage guarantee."""

import itertools
import pickle
from collections import Counter
from dataclasses import replace

import pytest

from behave.sim import api
from behave.sim.baseline import BASELINE_VERSION
from behave.sim.stimulus_patterns import PATTERN_CASES, PATTERN_FAMILIES, PatternPlan, _pair_at, pattern_case
from behave.task_execution.judge_policy import JudgePolicy


def _rows(widths, n_steps, cases, seed):
    """Unsigned payload rows of the first ``cases`` pattern cases."""
    plan = PatternPlan(api.ValueType.integer(api.FiniteType(width)) for width in widths)
    return [[list(row) for row in plan.rows(case, n_steps, seed=str(seed))] for case in range(cases)]


def _contract(types=None, names=None):
    types = (api.FiniteType(8),) if types is None else types
    names = names or tuple(f"data{index}" for index in range(len(types)))
    return api.TaskContract([
        api.Endpoint("in", "in", [api.Field(name, kind, name)
                                  for name, kind in zip(names, types)],
                     api.ready_valid("in", "valid", "ready"), clock="clk")],
        [api.ClockDomain("clk", "clk", "rst")])


def _by_case(baseline):
    return {case: dict(stimulus.queues)["in"]
            for stimulus in baseline.stimuli
            for row in baseline.provenance_dict()[stimulus.fingerprint]
            for case in row["cases"]}


def test_public_defaults_share_one_authority_and_explicit_overrides_survive():
    policy = api.BaselinePolicy()
    judge = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, search_seconds=0)
    assert policy.max_queue_depth == judge.sequence_length == 100
    assert policy.case_limit == judge.baseline_cases == 50
    assert len(PATTERN_CASES) == 37  # Primary recipes are not the sample budget.
    assert JudgePolicy.from_bytes(judge.to_bytes()) == judge
    short = replace(judge, sequence_length=4, baseline_cases=8)
    assert JudgePolicy.from_bytes(short.to_bytes()) == short
    prior = replace(judge, sequence_length=60)
    assert JudgePolicy.from_bytes(prior.to_bytes()) == prior
    assert api.BaselinePolicy(max_queue_depth=4).identity != policy.identity
    assert BASELINE_VERSION == "generation-baseline/13"
    assert policy.as_dict()["patterns"] == "typed-patterns/4"


def test_every_case_has_the_effective_length_and_matches_its_pattern_plan():
    widths = (8, 3)
    policy = api.BaselinePolicy(seed="comparison")
    c = _contract(tuple(api.FiniteType(width) for width in widths))
    b = api.build_baseline(c, api.GenerationSpec(120, 128, 2, 4), policy=policy)
    by_case = _by_case(b)
    expected = _rows(widths, 100, policy.case_limit, policy.seed)
    assert set(by_case) == set(range(policy.case_limit))
    for case, rows in enumerate(expected):
        assert len(by_case[case]) == 100
        assert by_case[case] == tuple(map(tuple, rows))
    for rows in b.provenance_dict().values():
        for row in rows:
            assert row["queue_depths"] == {"in": 100}
            assert row["patterns"] == [
                {"case": case, **pattern_case(case).as_dict()}
                for case in row["cases"]]
    # Unsigned low/zero merge, but neither provenance case disappears.
    assert by_case[0] == by_case[1]
    assert len(b.stimuli) < policy.case_limit


def test_default_length_does_not_rewrite_a_frozen_queue_bound_or_horizon():
    c = _contract()
    spec = api.GenerationSpec(8192, 64, 3, 256)
    baseline = api.build_baseline(c, spec)
    assert baseline.policy.max_queue_depth == 100
    assert all(len(rows) == 64 for rows in _by_case(baseline).values())
    assert all(len(stimulus.slots) == 8192 for stimulus in baseline.stimuli)
    assert spec.queue_bound == 64
    assert all(row["queue_depths"] == {"in": 64}
               for rows in baseline.provenance_dict().values() for row in rows)

    explicit = api.build_baseline(c, api.GenerationSpec(120, 128, 2, 4),
                                 policy=api.BaselinePolicy(max_queue_depth=60))
    assert all(len(rows) == 60 for rows in _by_case(explicit).values())


def test_small_budget_is_a_stable_prefix_not_a_different_last_long_case():
    c, spec = _contract(), api.GenerationSpec(20, 10, 2, 2)
    large = _by_case(api.build_baseline(c, spec))
    for count in (1, 3, 8, 37, 49):
        small = _by_case(api.build_baseline(c, spec, policy=api.BaselinePolicy(case_limit=count)))
        assert small == {case: large[case] for case in range(count)}
        assert all(len(rows) == 10 for rows in small.values())


def test_one_fifty_case_pool_contains_directed_pages_and_independent_random_samples():
    policy = api.BaselinePolicy(seed="mixed-pool")
    recipes = [pattern_case(index) for index in range(policy.case_limit)]
    assert recipes[:37] == list(PATTERN_CASES)
    assert [(case.family, case.page) for case in recipes[37:]] == [
        ("random", 1), ("counter", 1),
        ("random", 2), ("walking-one", 1),
        ("random", 3), ("walking-zero", 1),
        ("random", 4), ("boundaries", 1),
        ("random", 5), ("field-boundaries", 1),
        ("random", 6), ("pairwise-boundaries", 1),
        ("random", 7),
    ]
    assert Counter(case.family for case in recipes)["random"] == 8
    plan = PatternPlan((api.ValueType.integer(api.FiniteType(64)),))
    samples = [tuple(plan.rows(case, 100, seed=policy.seed))
               for case, recipe in enumerate(recipes) if recipe.family == "random"]
    assert len(set(samples)) == 8
    assert all(len(sample) == 100 for sample in samples)
    for case, recipe in enumerate(recipes):
        rows = tuple(plan.rows(case, 100, seed=policy.seed))
        expected = tuple(plan.row(recipe.family, recipe.page * 100 + index,
                                 sample=f"case:{case}", seed=policy.seed,
                                 variant=recipe.variant)
                         for index in range(100))
        if recipe.family != "binary-windows":
            assert rows == expected


@pytest.mark.parametrize("runtime", (False, True))
def test_fifty_case_dedup_retains_all_provenance_without_refilling(runtime):
    c = _contract((api.ValueType.enumeration(("only",)),))
    spec = None if runtime else api.GenerationSpec(110, 100, 2, 4)
    baseline = api.build_baseline(c, spec)
    assert baseline.policy.case_limit == 50
    assert len(baseline.stimuli) == 1  # The whole carrier has only one value.
    stimulus = baseline.stimuli[0]
    assert stimulus.depth("in") == 100  # Do not remove repeated input rows.
    provenance, = baseline.provenance_dict()[stimulus.fingerprint]
    assert provenance["cases"] == list(range(50))
    assert len(provenance["patterns"]) == 50


def test_seed_changes_only_random_payloads():
    widths = (1, 8, 8, 32)
    left = _rows(widths, 12, 50, "a")
    right = _rows(widths, 12, 50, "b")
    for case in range(len(left)):
        assert (left[case] == right[case]) is (pattern_case(case)[0] != "random")


def test_temporal_variants_have_independent_prefix_sum_oracles():
    deltas = {
        "up": [1], "down": [-1], "wobble": [1, -1], "pulse": [1, 1, -1, -1],
        "geometric": [1, 2, 4, 8, -1, -2, -4, -8],
        "pauses": [1, 0, 1, 0, -1, 0, -1, 0], "asymmetric": [3, -1],
    }
    plan = PatternPlan((api.ValueType.integer(api.FiniteType(8, signed=True)),))
    variants = [case for case in PATTERN_CASES if case.family == "numeric-walk"]
    assert len(variants) == 21
    for recipe in variants:
        start, walk = recipe.variant.split("/")
        value = {"low": -128, "zero": 0, "high": 127}[start]
        for index in range(100):
            value = (value + deltas[walk][index % len(deltas[walk])] + 128) % 256 - 128
            assert plan.row("numeric-walk", index, variant=recipe.variant) == (value,)


def test_binary_pages_cover_windows_without_joining_independent_traces():
    plan = PatternPlan((api.ValueType.boolean(),))
    observed = set()
    for case, recipe in enumerate(PATTERN_CASES):
        if recipe.family == "binary-windows":
            rows = [row[0] for row in plan.rows(case, 100)]
            observed.update(tuple(rows[index:index + 8]) for index in range(93))
    assert observed == set(itertools.product((False, True), repeat=8))


def test_high_background_sweep_and_later_pages_are_not_discarded():
    plan = PatternPlan((api.ValueType.integer(api.FiniteType(8)),) * 32)
    rows = [plan.row("field-boundaries-high", index) for index in range(100)]
    assert all(sum(value != 255 for value in row) <= 1 for row in rows)
    assert any(0 in row for row in rows)
    later = next(index for index in range(37, 100)
                 if pattern_case(index).family == "walking-one")
    assert next(plan.rows(later, 100)) == plan.row("walking-one", 100)


@pytest.mark.parametrize("family", PATTERN_FAMILIES)
def test_all_families_preserve_recursive_types_and_declared_signed_ranges(family):
    record = api.ValueType.record({
        "bits": api.ValueType.array(3, api.FiniteType(5, signed=True)),
        "bool": api.ValueType.boolean(),
        "mode": api.ValueType.enumeration(("c", "a", "b")),
        "empty": api.ValueType.array(0, api.FiniteType(8)),
    })
    plan = PatternPlan((record,))
    for index in range(140):
        row = plan.row(family, index)
        assert record.admits(row[0])
        assert type(row[0]["bool"]) is bool
        assert type(row[0]["bits"]) is tuple
        assert row[0]["empty"] == ()
    restored = pickle.loads(pickle.dumps(plan))
    assert restored.row(family, 31) == plan.row(family, 31)


def test_wide_random_and_walking_bits_are_not_truncated_to_256_or_64_bits():
    plan = PatternPlan((api.ValueType.integer(api.FiniteType(1024)),))
    values = [plan.row("random", index)[0] for index in range(8)]
    assert len({value >> 256 for value in values}) > 1
    assert plan.row("walking-one", 1023) == (1 << 1023,)
    assert plan.row("walking-zero", 1023) == ((1 << 1023) - 1,)
    signed = PatternPlan((api.ValueType.integer(api.FiniteType(8, signed=True)),))
    assert signed.row("walking-one", 7) == (-128,)


def test_field_sweep_pairwise_and_exhaustive_prefix_have_independent_oracles():
    types = (api.ValueType.integer(api.FiniteType(2)),) * 3
    plan = PatternPlan(types)
    for step in range(12):
        row = plan.row("field-boundaries", step)
        assert sum(value != 0 for value in row) <= 1
    for count in range(2, 16):
        pairs = list(itertools.combinations(range(count), 2))
        assert [_pair_at(count, i) for i in range(len(pairs))] == pairs
    pairs = {plan.row("pairwise-boundaries", i)[:2] for i in range(300)
             if plan.row("pairwise-boundaries", i)[2] == 0}
    assert pairs == set(itertools.product(range(4), repeat=2))
    assert {plan.row("exhaustive-prefix", i) for i in range(64)} == set(
        itertools.product(range(4), repeat=3))


def test_pattern_budget_does_not_build_a_cartesian_product_or_later_cases(monkeypatch):
    calls = []
    original = PatternPlan.row

    def row(self, *args, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PatternPlan, "row", row)
    plan = PatternPlan((api.ValueType.integer(api.FiniteType(1024)),) * 100)
    assert len(list(plan.rows(0, 2, seed="budget"))) == 2
    assert len(calls) == 2


def test_pairwise_short_prefix_varies_values_even_with_many_field_pairs():
    plan = PatternPlan((api.ValueType.integer(api.FiniteType(16)),) * 32)
    rows = [plan.row("pairwise-boundaries", step) for step in range(100)]
    assert len(set(rows)) > 1
    assert any(a and b for row in rows
               for a, b in itertools.combinations(row, 2))
    assert all(sum(value != 0 for value in row) <= 2 for row in rows)


def test_pairwise_staggering_preserves_each_heterogeneous_cartesian_product():
    types = (api.ValueType.boolean(), api.ValueType.integer(api.FiniteType(3)),
             api.ValueType.integer(api.FiniteType(4, signed=True)),
             api.ValueType.enumeration(("a", "b", "c")))
    plan = PatternPlan(types)
    pairs = list(itertools.combinations(range(len(types)), 2))
    for ordinal, (left, right) in enumerate(pairs):
        a, b = plan.leaves[left], plan.leaves[right]
        count = len(a.boundaries) * len(b.boundaries)
        rows = [plan.row("pairwise-boundaries", ordinal + page * len(pairs))
                for page in range(count)]
        assert {(row[left], row[right]) for row in rows} == set(itertools.product(
            map(a.decode, a.boundaries), map(b.decode, b.boundaries)))


def test_heterogeneous_endpoints_keep_their_own_queue_limits():
    c = api.TaskContract([
        api.Endpoint("sample", "in", [api.Field("a", api.FiniteType(8), "a")],
                     api.sampled_edge("in"), clock="clk", kind=api.Kind.TRANSACTION),
        api.Endpoint("request", "in", [api.Field("b", api.FiniteType(8), "b")],
                     api.start_done("in", "start", "done"), clock="clk")],
        [api.ClockDomain("clk", "clk", "rst")])
    b = api.build_baseline(c, api.GenerationSpec(10, 8, 2, 0))
    assert all(s.depth("sample") == 8 and s.depth("request") == 1 for s in b.stimuli)


def test_enum_singleton_and_empty_array_are_total_for_every_pattern():
    kinds = (api.ValueType.enumeration(("only",)),
             api.ValueType.array(0, api.FiniteType(8)))
    for family in PATTERN_FAMILIES:
        assert PatternPlan(kinds).row(family, 5000) == ("only", ())
