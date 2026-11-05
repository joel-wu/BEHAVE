"""One analysis lifetime across frontend work, queue waits and exact queries."""

import pytest

from behave.isolation.preparation_control import check_preparation
from behave.sim import analysis_budget, analysis_service, api
from behave.sim.analysis_budget import (
    AnalysisBudget,
    AnalysisStopped,
    active_analysis,
    analysis_scope,
    remaining_query_ms,
)
from behave.sim.analysis_report import analyse_after_decision
from behave.sim.search_policy import check_search, remaining_timeout

from .test_analysis_report_handoff import _decided
from .test_command_handler import _request


@pytest.mark.parametrize("seconds", [0, -1, True, float("inf"), float("nan"), "900"])
def test_invalid_analysis_wall_is_rejected(seconds):
    with pytest.raises(ValueError):
        AnalysisBudget(seconds)


def test_nested_operations_and_preparation_do_not_renew_the_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])
    with analysis_scope(20) as budget:
        now[0] += 15
        with analysis_scope(900) as nested:
            assert nested is budget
            assert remaining_timeout(1800) == 5
            assert remaining_query_ms(None) == 5000
            assert remaining_query_ms(2000) == 2000
        now[0] += 5
        for check in (check_preparation, check_search):
            with pytest.raises(AnalysisStopped):
                check()
    assert active_analysis() is None


def test_submillisecond_query_limit_never_becomes_solver_unlimited(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])
    with analysis_scope(1):
        now[0] = 0.9999
        assert remaining_query_ms(5000) == 1


@pytest.mark.parametrize('stop', ['normal', 'error', 'budget'])
def test_owner_releases_resources_after_nested_scopes_and_all_exit_paths(monkeypatch, stop):
    now, closed = [0.0], []
    monkeypatch.setattr(analysis_budget.time, 'monotonic', lambda: now[0])
    try:
        with analysis_scope(5) as budget:
            budget.on_close(closed.append, 'outer')
            with analysis_scope() as nested:
                nested.on_close(closed.append, 'inner')
            assert closed == []
            if stop == 'error':
                raise ValueError('construction stopped')
            if stop == 'budget':
                now[0] = 5
                budget.check()
    except (ValueError, AnalysisStopped):
        assert stop != 'normal'
    assert closed == ['inner', 'outer']
    assert active_analysis() is None


def test_cleanup_failure_does_not_skip_other_resources_or_leave_active_owner():
    closed = []
    def fail():
        raise RuntimeError('cleanup unconfirmed')
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'), analysis_scope(5) as budget:
        budget.on_close(closed.append, 'still released')
        budget.on_close(fail)
    assert closed == ['still released']
    assert active_analysis() is None


def test_budget_exhaustion_is_a_separate_report_and_keeps_the_score(tmp_path, monkeypatch):
    store, authority, evaluation = _decided(tmp_path)
    standing = store.standing_authority()
    now = [0.0]
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])

    def expires(*args, **kwargs):
        assert active_analysis().wall_seconds == 7
        now[0] = 7
        try:
            check_preparation()
        except AnalysisStopped as exc:
            exc.add_note("RTL exact Analysis operation: bind")
            raise

    monkeypatch.setattr(analysis_service, "_analyse", expires)
    outcome = analyse_after_decision(store, authority, wall_timeout_seconds=7)
    assert outcome.verdict is api.Verdict.REFUSED
    assert outcome.evidence["refusal"]["kind"] == "analysis-budget-exhausted"
    assert list(outcome.evidence["refusal"]["detail"]["notes"]) == [
        "RTL exact Analysis operation: bind"]
    assert outcome.evidence["analysis_policy"]["artifact_timeout_ms"] == 7000
    assert outcome.concrete == evaluation.fingerprint
    assert store.standing_authority() == standing
    assert authority.passed


def test_public_analysis_requires_caller_budget_not_a_frontend_default(tmp_path, monkeypatch):
    _store, _authority, evaluation = _decided(tmp_path)
    observed = []
    original = analysis_service._analyse

    def observe(*args, **kwargs):
        observed.append(active_analysis().wall_seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis_service, "_analyse", observe)
    with pytest.raises(ValueError, match="explicit finite wall_timeout_seconds"):
        api.analyse(evaluation)
    api.analyse(evaluation, wall_timeout_seconds=11)
    assert observed == [11]
    assert active_analysis() is None


def test_inline_command_freezes_analysis_wall_separately_from_scoring(tmp_path):
    default = _request(tmp_path / "default")
    custom = _request(tmp_path / "custom", analysis_wall_timeout_seconds=23)
    assert default.analysis_policy.artifact_timeout_ms == 60_000  # Explicit test fixture budget.
    assert custom.analysis_policy.artifact_timeout_ms == 23000
    assert custom.scoring_policy == default.scoring_policy
    assert custom.as_dict()["analysis"]["policy"]["artifact_timeout_ms"] == 23000
    with pytest.raises(ValueError, match="score-only"):
        _request(tmp_path / "invalid", run_analysis=False, analysis_wall_timeout_seconds=23)


def test_exact_preparation_uses_the_same_command_analysis_deadline(tmp_path, monkeypatch):
    from behave.sim import command

    result = api.handle_command(_request(tmp_path, run_analysis=False))
    assert result.evaluation.passed
    now = [0.0]
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])

    def binding(*args):
        assert active_analysis().wall_seconds == 3
        now[0] = 3
        check_preparation()

    monkeypatch.setattr(command, "_analysis_inputs", binding)
    outcome = api.analyse_command(result, wall_timeout_seconds=3)
    assert outcome.evidence["refusal"]["kind"] == "analysis-budget-exhausted"
    assert result.evaluation.passed
    assert active_analysis() is None


def test_analysis_options_require_a_wall_budget_and_preserve_optional_query_caps():
    from behave.task_execution.analysis import AnalysisOptions

    with pytest.raises(TypeError, match="wall_timeout_seconds"):
        AnalysisOptions()
    options = AnalysisOptions(wall_timeout_seconds=73)
    assert options.query_timeout_ms is options.feasibility_timeout_ms is None
    assert AnalysisOptions.from_dict(options.as_dict()) == options
    old_policy = {"wall_timeout_seconds": 900, "feasibility_timeout_ms": 30_000,
                  "query_timeout_ms": 30_000, "batch": 32}
    assert AnalysisOptions.from_dict(old_policy).as_dict() == old_policy
    for field in options.as_dict():
        for invalid in (True, 0, -1):
            with pytest.raises(ValueError):
                AnalysisOptions.from_dict({**options.as_dict(), field: invalid})


def test_default_analysis_queries_use_the_remaining_stage_not_thirty_seconds(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(analysis_budget.time, "monotonic", lambda: now[0])
    with analysis_scope(73):
        now[0] = 12
        assert remaining_query_ms(None) == 61_000
        assert remaining_query_ms(2_000) == 2_000
        now[0] = 73
        with pytest.raises(AnalysisStopped):
            remaining_query_ms(None)
