"""A failed query backend must not disable another backend or invent goal hits."""

from contextlib import contextmanager

import pytest

from behave.sim import api
from behave.sim.generation import generate_independently
from behave.sim.generation_protocol import Outcome, ProbeResult, Scope

from .test_api_path import contract
from .test_generation_scheduler import _Provider, _replace, _request, _spec, _stimulus
from .test_runtime_guidance import runtime, setup


@pytest.mark.parametrize('debug', [False, True])
@pytest.mark.parametrize('scoped', ['none', 'matching', 'different'])
def test_timed_search_continues_only_with_explicit_request_scope(debug, scoped):
    failure = api.Refusal('backend-unavailable', 'this query backend could not prepare')
    other_failure = api.Refusal('backend-unavailable', 'a different failure with the same code')
    seed = _stimulus([0])

    class Provider(_Provider):
        def __init__(self):
            super().__init__({((0,),): [
                _request('source-before'),
                _request('product-fails', targets=('g2',), rank=1),
                _request('source-after', rank=2),
                _request('product-skip', targets=('g2',), rank=3)]}, {})
            self.failure = None
            self.closed = False

        @contextmanager
        def search_scope(self, *, probe_budget):
            try:
                yield
            finally:
                self.closed = True

        def request_refusal(self, seed, request):
            if scoped == 'none' or self.failure is None or request.targets != ('g2',):
                return None
            return self.failure if scoped == 'matching' else other_failure

        def probe(self, seed, request):
            self.probes.append(request.occurrence)
            if request.targets == ('g2',):
                self.failure = failure
                raise api.Refused(failure)
            value = 1 if request.occurrence == 'source-before' else 2
            return ProbeResult(Outcome.SAT, Scope.EXACT_LOCAL_HIT, 'g1', _replace(seed, 0, value))

    provider = Provider()
    def run():
        return generate_independently((seed,), (provider,), contract=contract(),
            generation_spec=_spec(seed), search_policy=api.SearchPolicy(debug=debug, max_probes=8))

    if debug:
        with pytest.raises(api.Refused, match='generation-debug-stop') as caught:
            run()
        assert 'this query backend could not prepare' in str(caught.value.refusal.as_dict())
        assert provider.probes == ['source-before', 'product-fails']
    else:
        result, = run().searches
        assert result.baseline == (seed,)
        row = result.as_dict()['telemetry']
        assert row['refusals'] == {'backend-unavailable': 1}
        assert row['probe_refusals'] == 1
        assert row['outcomes']['local-unsat'] == row['outcomes']['unsupported'] == 0
        if scoped == 'matching':
            assert provider.probes == ['source-before', 'product-fails', 'source-after']
            assert len(result.generated) == 2
            assert row['requests_unavailable'] == 1
            assert row['probes'] == 3 and row['outcomes']['sat'] == 2
            assert row['termination'] == 'queue-empty'
        else:
            assert provider.probes == ['source-before', 'product-fails']
            assert len(result.generated) == 1
            assert row['termination'] == 'search-failed'
    assert provider.targetable_goals == ('g1', 'g2')
    assert provider.closed


def test_runtime_reports_the_recorded_domain_without_switching_workers(monkeypatch):
    from .test_generation_feedback import ObservedProvider

    failure = api.Refusal('one-domain-unavailable', 'only the first recorded query domain')
    # A context's ports are bound when it is created; the bound reader stays live.
    monkeypatch.setattr(ObservedProvider, 'request_refusal', lambda self, view, request: (
        failure if getattr(self, 'refuses', False) and dict(view.queues)['in'][0][0] == 0
        else None), raising=False)
    provider, seeds, _replays, contexts, scopes = setup()
    other = runtime(1)  # Its recorded latency gives a different query context.
    provider.prepare_seeds((*seeds, other))
    with provider.search_scope(probe_budget=8):
        first = provider.select(seeds[0]).requests[0]
        contexts[0].refuses = True
        assert provider.request_refusal(seeds[0], first) is failure
        assert provider.request_refusal(other, first) is None
        assert len(contexts) == 1
        second = provider.select(other).requests[0]
        active_scopes = tuple(scopes)
        assert provider.request_refusal(seeds[0], first) is failure
        assert provider.request_refusal(other, second) is None
        assert len(contexts) == 2 and tuple(scopes) == active_scopes
