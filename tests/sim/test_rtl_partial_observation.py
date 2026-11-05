"""A known-false activation decides a miss even with unavailable inner readings."""

import itertools

import pytest

from behave.sim.rtl.catalogue import Site
from behave.sim.rtl.site_sampling import SiteSampler


@pytest.mark.parametrize("values", tuple(itertools.product((0, 1, None), repeat=3)))
def test_partial_observations_agree_with_all_boolean_completions(values):
    names = ("outer", "inner", "condition")
    levels = tuple(("\\" + name, "1'1") for name in names[:2])
    labels = ("1'1", "1'0")
    site = Site("M", "p", "\\condition", "test.sv:1.1-1.2", "1'1", 3,
                levels, labels, activation_frames=({"labels": labels},) * 2)
    sampler = SiteSampler((site,))
    observation = {name: value for name, value in zip(names, values) if value is not None}
    hits, unresolved = sampler.sample(observation)

    # Independent oracle: substitute every possible missing reading, then evaluate
    # the complete conjunction. Mixed answers are the only undecidable case.
    completions = {all(row) for row in itertools.product(*[
        (0, 1) if value is None else (value,) for value in values])}
    assert hits == (set(sampler.goals) if completions == {True} else set())
    assert set(unresolved) == (set(sampler.goals) if len(completions) > 1 else set())


def test_inactive_process_does_not_need_its_unavailable_readings():
    site = Site("M", "p", "\\missing", "test.sv:2.1-2.2", "1'1", 1,
                (), ("1'1", "1'0"), triggers=(("posedge", "\\clk"),))
    assert SiteSampler((site,)).sample({}, active_events=()) == (set(), {})

