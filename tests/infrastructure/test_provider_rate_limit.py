"""No provider traffic: shared transport admission and count/generation routing."""

from dataclasses import replace
import json

import pytest

from behave.agent.providers.hosted import HostedEndpoint
from behave.agent.providers.rate_limit import RequestRate, wait_for_request
from test_hosted_providers import endpoint


def test_unconfigured_endpoint_keeps_identity_and_does_not_wait(monkeypatch):
    ep = endpoint()
    assert 'rate_limit' not in ep.as_dict()
    assert HostedEndpoint.from_dict(ep.as_dict()) == ep
    monkeypatch.setattr('behave.agent.providers.rate_limit.time.sleep',
                        lambda seconds: pytest.fail('unconfigured admission must not wait'))
    wait_for_request(ep)


def test_shared_gate_does_not_throttle_a_different_model(tmp_path, monkeypatch):
    import behave.agent.providers.rate_limit as gate
    ep = endpoint('openrouter', rate_limit=RequestRate(str(tmp_path), 20))
    clone = HostedEndpoint.from_dict(ep.as_dict())
    now = [1000.]
    waits = []
    monkeypatch.setattr(gate.time, 'time', lambda: now[0])
    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(gate.time, 'sleep', sleep)
    wait_for_request(ep)
    wait_for_request(clone)
    wait_for_request(ep)
    assert waits == [3., 3.]
    other = replace(ep, model='different', prices=None)
    wait_for_request(other)
    assert waits == [3., 3.]
    assert len(list(tmp_path.glob('*.json'))) == 2


@pytest.mark.parametrize('rpm', [0, -1, 2.5, True, None])
def test_invalid_rate_rejected(tmp_path, rpm):
    with pytest.raises(ValueError, match='requests_per_minute'):
        RequestRate(str(tmp_path), rpm)


def test_corrupt_gate_does_not_silently_dispatch(tmp_path):
    ep = endpoint('openrouter', rate_limit=RequestRate(str(tmp_path), 20))
    wait_for_request(ep)
    next(tmp_path.glob('*.json')).write_text(json.dumps({'next_at': 'invalid'}))
    with pytest.raises(ValueError, match='invalid request-rate state'):
        wait_for_request(ep)
