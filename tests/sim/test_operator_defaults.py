"""Operator tools do not silently select historical site configurations."""

import pytest

from tools import population


def test_population_has_no_implicit_historical_baseline(monkeypatch):
    measured = {'sample': 'current'}
    monkeypatch.setattr(population, 'measure', lambda *_args: measured)

    def report(got, expected):
        assert got is measured
        assert expected is None
        return 0

    monkeypatch.setattr(population, 'report', report)
    assert population.main([]) == 0


@pytest.mark.parametrize('write', [True, False])
def test_population_checks_baseline_argument_before_measurement(monkeypatch, tmp_path, write):
    def unexpected(*_args):
        pytest.fail('invalid baseline must not start a census')

    monkeypatch.setattr(population, 'measure', unexpected)
    args = ['--write-expected'] if write else ['--expected', str(tmp_path / 'missing.json')]
    with pytest.raises(SystemExit) as caught:
        population.main(args)
    assert caught.value.code == 2
