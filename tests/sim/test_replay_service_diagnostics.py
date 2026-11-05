"""An unexpected service failure remains no-result and keeps its actual cause."""

from behave.sim.core.failure import FailureDisposition, FailureOrigin
from behave.sim.run_assessment import replay_one


def test_unattributed_replay_error_retains_diagnostic_without_scoring_candidate():
    def unavailable():
        raise RuntimeError("cannot start the replay reader")

    result = replay_one(None, unavailable)
    assert result.origin is FailureOrigin.VERIFIER
    assert result.disposition is FailureDisposition.NO_RESULT
    assert result.reason.kind == "verifier-replay-failed"
    assert result.reason.as_dict()["detail"] == {
        "error": "RuntimeError", "detail": "cannot start the replay reader"}
