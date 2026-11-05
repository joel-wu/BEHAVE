"""Final assembly cannot retry away an earlier comparison-service failure."""

import pytest

from behave.sim import api, evaluation

from .test_command_handler import _request


@pytest.mark.parametrize("origin", ("reference", "verifier"))
def test_command_retains_the_first_comparison_failure(tmp_path, monkeypatch, origin):
    compare = evaluation.compare_run
    calls = []
    failure = getattr(api.RunFailure, origin)(
        "comparison", api.Refusal("comparison-unavailable", "first attempt failed"))

    def intermittent(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise api.FailureRaised(failure)
        return compare(*args, **kwargs)

    monkeypatch.setattr(evaluation, "compare_run", intermittent)
    result = api.handle_command(_request(tmp_path, run_analysis=False))

    assert len(calls) == 1, "final assembly must reuse the failure, not retry comparison"
    assert not result.decision.authoritative
    assert result.evaluation.no_result_failures == ((0, failure),)
    assert not result.evaluation.passed
