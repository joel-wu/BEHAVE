"""One sampled switch is evaluated once, without weakening per-site evidence."""

from itertools import product

import pytest

from behave.sim.rtl import case_match
from behave.sim.rtl.catalogue import sites_of
from behave.sim.rtl.site_target import site_hits
from tests.support.rtl_site_oracle import occurrence_holds


def _sites(arms):
    rows = ["module \\top", "  process $p", "    switch \\a"]
    for value in range(arms):
        rows.extend((f"      case 16'{value:016b}", "        assign \\y 1'1"))
    return sites_of("\n".join((*rows, "    end", "    sync always",
                               "      update \\y \\y", "  end", "end")))


@pytest.mark.parametrize('value', [63, None, 'x'])
def test_one_sample_parses_each_switch_label_once(monkeypatch, value):
    sites = _sites(64)
    original = case_match._patterns
    parsed = []

    def counted(label):
        parsed.append(label)
        return original(label)

    monkeypatch.setattr(case_match, "_patterns", counted)
    got, refused = site_hits(sites, {"a": value})
    if value == 63:
        assert not refused and len(got) == 2  # selected arm and its assignment
    else:
        assert not got and refused
    assert len(parsed) <= 64, len(parsed)


def test_signal_reading_does_not_copy_the_complete_width_table():
    from collections.abc import Mapping

    from behave.sim.rtl.sigspec import signal_value

    class Widths(Mapping):
        walks = 0

        def __iter__(self):
            self.walks += 1
            return iter(f'a{i}' for i in range(1024))

        def __len__(self):
            return 1024

        def __getitem__(self, key):
            return 8

    widths = Widths()
    for i in range(64):
        assert signal_value(f'\\a{i} [3:0]', {f'a{i}': 31}, widths) == (15, None)
    assert widths.walks == 0


def test_replay_sampler_prepares_identities_and_patterns_only_once(monkeypatch):
    from behave.sim.rtl import catalogue
    from behave.sim.rtl.site_sampling import SiteSampler

    sites = _sites(64)
    sampler = SiteSampler(sites)
    expected = {value: site_hits(sites, {'a': value}) for value in (0, 63, 65, None)}

    def repeated(*args, **kwargs):
        pytest.fail('a replay slot recomputed fixed catalogue metadata')

    monkeypatch.setattr(catalogue, 'goal_id_of', repeated)
    monkeypatch.setattr(case_match, '_patterns', repeated)
    for value in (0, 63, 65, None, 63, 0):
        assert sampler.sample({'a': value}) == expected[value]


def test_prepared_sampler_owns_its_binding_and_activation_snapshot():
    from behave.sim.rtl.catalogue import Site
    from behave.sim.rtl.site_sampling import SiteSampler

    frame = {'labels': ["1'1", 'default']}
    site = Site('top', '$p', '\\a', 'f:1', "1'1", 1,
                (('\\outer', 'default'),), siblings=("1'1", 'default'),
                activation_frames=(frame,), triggers=(('posedge', '\\clk'),))
    sampler = SiteSampler([site])
    expected = site_hits([site], {'a': 1, 'outer': 0}, active_events={('rising', 'clk')})
    assert expected[0] == set(sampler.goals)
    frame['labels'].reverse()
    with pytest.raises(AttributeError):
        site.outcome = 'default'
    with pytest.raises(AttributeError):
        site.triggers = (('negedge', '\\clk'),)
    assert sampler.sample({'a': 1, 'outer': 0}, active_events={('rising', 'clk')}) == expected
    assert sampler.sample({'a': 1, 'outer': 0}, active_events={('falling', 'clk')}) == (set(), {})
    with pytest.raises(AttributeError):
        sampler.goals = ()


def test_prepared_occurrences_keep_clock_gating_and_hit_dominance():
    from behave.sim.rtl.catalogue import Site
    from behave.sim.rtl.site_sampling import SiteSampler

    sites = [Site('top', '$p', signal, 'f:1', "1'1", 1, (),
                  siblings=("1'1", 'default'), switch_id=signal,
                  triggers=((edge, '\\clk'),))
             for signal, edge in (('\\a', 'posedge'), ('\\b', 'negedge'))]
    bindings = {site.target: 'one-public-goal' for site in sites}
    sampler = SiteSampler(sites, occurrence_goals=bindings)
    readings = {'a': 1, 'b': None}
    assert sampler.sample(readings) == ({'one-public-goal'}, {})
    assert sampler.sample(readings, active_events=set()) == (set(), {})
    assert sampler.sample(readings, active_events={('rising', 'clk')}) == (
        {'one-public-goal'}, {})
    assert sampler.sample(readings, active_events={('falling', 'clk')}) == (
        set(), {'one-public-goal': 'the sampled value None is not a number'})
    readings['a'] = 0
    assert sampler.sample(readings) == (
        set(), {'one-public-goal': 'the sampled value None is not a number'})


def test_switch_samples_are_not_reused_across_slots():
    sites = _sites(4)
    for value in (0, 3, 5, 1, 5, 0):
        got, refused = site_hits(sites, {"a": value})
        assert not refused
        expected = {site.goal for site in sites if occurrence_holds(site, {"a": value})}
        assert got == expected


def test_whole_switch_matches_the_per_arm_rule_including_refusals():
    from behave.sim.rtl.case_match import matches, switch_matches

    labels = ("2'00", "2'0-", "2'-1", "default", "no-match", "2'xx", " 2'01 ")
    for siblings in product(labels, repeat=3):
        for value in (0, 1, 2, 3, 4, -1, None, "x"):
            expected = {label: matches(label, siblings, value) for label in siblings}
            assert switch_matches(siblings, value) == expected, (siblings, value)


def test_matching_tables_do_not_confuse_two_signals_with_the_same_labels():
    from behave.sim.rtl.catalogue import Site

    labels = ("1'1", "default")
    sites = [Site("top", "$p", signal, "f:1", label, 1, (), siblings=labels,
                  switch_id=signal)
             for signal in ("\\a", "\\b") for label in labels]
    got, refused = site_hits(sites, {"a": 0, "b": 1})
    assert not refused
    assert got == {sites[1].goal, sites[2].goal}
