"""Shared control prefixes preserve every occurrence's three-valued evidence."""

import itertools

import pytest

from behave.sim.rtl.case_match import matches
from behave.sim.rtl.catalogue import Site
from behave.sim.rtl.sigspec import signal_value
from behave.sim.rtl.site_sampling import SiteSampler


def _flat_oracle(sites, values, widths, *, events=None, combinational=True, bindings=None):
    """Independent per-occurrence conjunction using scalar match semantics."""
    hits, pending = set(), {}
    for site in sites:
        if not site.active_for(events, include_combinational=combinational):
            continue
        goal = site.goal if bindings is None else bindings[site.target]
        levels = [(*level, siblings) for level, siblings in
                  zip(site.activation, site.activation_siblings)]
        if site.kind != "location":
            levels.append((site.signal, site.outcome, site.siblings))
        verdict, why = True, None
        for signal, label, siblings in levels:
            try:
                value, missing = signal_value(site.read_signal(signal), values, widths)
                if missing is not None:
                    why = why or f"this reading does not carry {missing!r}"
                    continue
            except (TypeError, ValueError) as error:
                why = why or str(error)
                continue
            got, bad = matches(label, siblings, value)
            if got is None:
                why = why or bad
            elif not got:
                verdict, why = False, None
                break
        if why is not None:
            pending.setdefault(goal, why)
        elif verdict:
            hits.add(goal)
    return hits, {goal: why for goal, why in pending.items() if goal not in hits}


def _sites(depth=3, count=12):
    labels = ("2'0-", "2'-1", "default")
    activation = tuple((f"\\outer{i}", labels[i % 3]) for i in range(depth))
    frames = ({"labels": labels},) * depth
    return [Site("M", "p", "\\condition", f"prefix.sv:{i + 1}", labels[i % 3],
                 depth + 1, activation, labels, activation_frames=frames,
                 triggers=(("always", "") if i % 3 == 0 else
                           ("posedge" if i % 3 == 1 else "negedge", "\\clk"),))
            for i in range(count)]


@pytest.mark.parametrize("events", (None, (), (("rising", "clk"),), (("falling", "clk"),)))
@pytest.mark.parametrize("combinational", (False, True))
def test_shared_prefixes_equal_independent_occurrences(events, combinational):
    sites = _sites()
    # Elaborated occurrences can share a public goal without sharing a trigger.
    bindings = {site.target: f"goal-{i // 4}" for i, site in enumerate(sites)}
    sampler = SiteSampler(sites, occurrence_goals=bindings)
    names = ("outer0", "outer1", "outer2", "condition")
    widths = dict.fromkeys(names, 2)
    for row in itertools.product((0, 1, 2, 3, None), repeat=len(names)):
        values = {name: value for name, value in zip(names, row) if value is not None}
        assert sampler.sample(values, widths, active_events=events,
                              include_combinational=combinational) == _flat_oracle(
            sites, values, widths, events=events, combinational=combinational,
            bindings=bindings)


def test_ordered_unknown_reasons_and_false_descendants():
    sites = _sites(depth=2)
    sampler = SiteSampler(sites)
    for values in ({}, {"outer0": None}, {"outer0": "x", "outer1": 2},
                   {"outer0": 2, "outer1": None}, {"condition": 3}):
        assert sampler.sample(values) == _flat_oracle(sites, values, None)


def test_one_prefix_extension_per_sample_not_per_occurrence():
    sites = _sites(depth=20, count=300)
    sampler = SiteSampler(sites)

    class CountReads(tuple):
        def __getitem__(self, index):
            reads.append(index)
            return super().__getitem__(index)

    reads = []
    prefixes = sampler._prefixes
    object.__setattr__(sampler, "_prefixes", CountReads(prefixes))
    values = {f"outer{i}": (0, 3, 2)[i % 3] for i in range(20)}
    for condition in (0, 3, None, 2, 0):
        reads.clear()
        values["condition"] = condition
        assert sampler.sample(values) == _flat_oracle(sites, values, None)
        assert len(reads) == len(prefixes) - 1
    assert len(prefixes) == 1 + 20 + 3


def test_deep_prefixes_do_not_consume_python_recursion_depth():
    # Explicit occurrence bindings isolate sampling from catalogue-id formatting.
    sites = _sites(depth=1500, count=2)
    bindings = {site.target: str(i) for i, site in enumerate(sites)}
    sampler = SiteSampler(sites, occurrence_goals=bindings)
    values = {f"outer{i}": (0, 3, 2)[i % 3] for i in range(1500)}
    values["condition"] = 0
    assert sampler.sample(values) == _flat_oracle(sites, values, None, bindings=bindings)


def test_inactive_descendant_does_not_read_its_private_condition(monkeypatch):
    from behave.sim.rtl import site_sampling

    labels = ("1'1", "default")
    sites = [Site("M", "p", signal, f"clock.sv:{i}", "1'1", 2,
                  (("\\outer", "1'1"),), labels,
                  activation_frames=({"labels": labels},), triggers=((edge, "\\clk"),))
             for i, (signal, edge) in enumerate(
                 (("\\unavailable", "negedge"), ("\\live", "posedge")))]
    original = site_sampling.signal_value
    reads = []

    def observed(signal, *args):
        assert signal != "\\unavailable"
        reads.append(signal)
        return original(signal, *args)

    monkeypatch.setattr(site_sampling, "signal_value", observed)
    assert SiteSampler(sites).sample({"outer": 1, "live": 1},
                                      active_events=(("rising", "clk"),)) == ({sites[1].goal}, {})
    assert reads == ["\\outer", "\\live"]


def test_trigger_groups_are_not_limited_to_a_machine_word():
    labels = ("1'1", "default")
    sites = [Site("M", "p", "\\condition", f"clocks.sv:{i}", "1'1", 1,
                  (), labels, triggers=(("posedge", f"\\clock{i}"),)) for i in range(97)]
    sampler = SiteSampler(sites)
    for index in (0, 31, 63, 64, 96):
        assert sampler.sample({"condition": 1},
                              active_events=(("rising", f"clock{index}"),)) == ({sites[index].goal}, {})
    assert sampler.sample({"condition": 1}) == (set(sampler.goals), {})
