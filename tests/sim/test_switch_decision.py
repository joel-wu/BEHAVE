"""Compiled switch lookups equal the independent ordered per-arm semantics."""

import pickle
from itertools import product

import pytest

from behave.sim.rtl.case_match import SwitchMatcher, matches


def test_all_small_switches_match_including_priority_and_unknown_diagnostics():
    labels = ("2'00", "2'0-", "2'-1", 'default', 'no-match', "2'xx",
              " 2'01 ", "2'01,2'10", '1', '')
    values = (0, 1, 2, 3, 4, -1, 2**128, True, False, None, 'x', 'z', 0.0)
    for siblings in product(labels, repeat=3):
        matcher = SwitchMatcher(siblings)
        for value in values:
            decision = matcher.decision(value)
            assert {label: decision[label] for label in siblings} == {
                label: matches(label, siblings, value) for label in siblings}
            assert 'not-an-arm' not in decision
            with pytest.raises(KeyError):
                decision['not-an-arm']


def test_literal_dispatch_does_not_walk_patterns_or_build_a_whole_outcome_table(monkeypatch):
    import behave.sim.rtl.case_match as module

    siblings = tuple(str(i) for i in range(4096)) + ('default',)
    matcher = SwitchMatcher(siblings)

    def forbidden(*_args, **_kwargs):
        pytest.fail('a compiled literal switch rebuilt or traversed its arm outcomes')

    monkeypatch.setattr(module, '_matches', forbidden)
    monkeypatch.setattr(SwitchMatcher, 'sample', forbidden)
    for value in (0, 4095, 4096, -1, 0, None, True):
        table = matcher.decision(value)
        if value is None:
            assert table['0'] == (None, 'the sampled value None is not a number')
        else:
            assert table['0'] == (value == 0, None)
            assert table['default'] == (not 0 <= value < 4096, None)
    # A lookup shares a constant rule table; it is not an N-by-N truth table.
    assert len({id(selection._positions) for selection in matcher._selections}) == 1
    assert len(matcher._selections) == len(siblings) + 1


def test_no_mask_expansion_and_unknowns_do_not_become_false():
    siblings = ("128'1" + '-' * 127, 'default', "1'x")
    matcher = SwitchMatcher(siblings)
    assert matcher._literal_index is None
    assert matcher._selections == ()
    for value in (2**127, 0, None, 'x'):
        table = matcher.decision(value)
        assert {label: table[label] for label in siblings} == matcher.sample(value)


def test_int_subclasses_keep_ordered_comparison_not_dictionary_dispatch():
    class UnhashableInt(int):
        __hash__ = None

    matcher = SwitchMatcher(('1', 'default'))
    assert matcher.decision(UnhashableInt(1)) == matcher.sample(UnhashableInt(1))


def test_legacy_label_carriers_do_not_acquire_new_constructor_restrictions():
    matcher = SwitchMatcher((1, 'default'))
    for value in (None, 1, 2):
        assert matcher.decision(value) == matcher.sample(value)


def test_decisions_are_read_only_detached_and_pickle_with_the_plan():
    labels = ['1', 'default']
    matcher = SwitchMatcher(labels)
    labels.reverse()
    for copy in (matcher, pickle.loads(pickle.dumps(matcher))):
        for value in (None, 0, 1, True, None, 1):
            table = copy.decision(value)
            assert {label: table[label] for label in ('1', 'default')} == matcher.sample(value)
            with pytest.raises((AttributeError, TypeError)):
                table['1'] = (False, None)
        # The old dictionary-returning interface still gives the caller a copy.
        writable = copy.sample(None)
        writable['1'] = (False, None)
        assert copy.decision(None)['1'] == (None, 'the sampled value None is not a number')
