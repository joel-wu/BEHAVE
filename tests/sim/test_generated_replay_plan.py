"""An invocation reuses its execution plan, never an earlier score or trace."""

import pytest

from behave.sim import api, replay_suite
from tests.sim.test_api_path import Doubling, _ref, contract, realization, schedule
from tests.sim.test_command_handler import _BaselineOnly
from tests.sim.test_replay_suite import _requests
from tests.support.device_boundary import prepare_device_command


def _generated(seeds):
    task = api.admit(contract(), realization(), reference=_ref())
    return api.generate_stimuli(
        task, tuple(sorted(seeds, key=lambda s: s.identity_bytes)), (_BaselineOnly(),),
        frontend_catalogue=api.CatalogueManifest(()),
        generation_spec=None, probe_budget=0)


def test_generated_plan_is_lazy_and_reused_by_identity(monkeypatch):
    build = replay_suite.build_replay_suite
    calls = []

    def counted(*args, **kwargs):
        if 'baseline' in kwargs:  # Final generated-pool plan, not its required subplan.
            calls.append(args)
        return build(*args, **kwargs)

    monkeypatch.setattr(replay_suite, 'build_replay_suite', counted)
    generated = _generated(_requests())
    image = generated.as_dict()
    assert not calls
    plan = generated.replay_suite
    assert plan == build(generated.task.contract, generated.stimuli, baseline=generated.baseline)
    assert generated.replay_suite is plan
    assert len(calls) == 1
    assert generated.as_dict() == image  # No evidence/wire schema change.
    detached = plan.as_dict()
    detached['continuous'].clear()
    assert generated.replay_suite.continuous
    with pytest.raises(AttributeError):
        generated._replay_suite = None


def test_distinct_generated_invocations_do_not_share_plan_cache():
    first, second = (_generated(_requests()) for _ in range(2))
    assert first.replay_suite == second.replay_suite
    assert first.replay_suite is not second.replay_suite


@pytest.mark.parametrize('runtime_member', [False, True])
def test_fixed_or_mixed_pool_never_acquires_a_continuous_plan(monkeypatch, runtime_member):
    def unexpected(*args):
        raise AssertionError('fixed schedules must not be converted to runtime requests')

    monkeypatch.setattr(replay_suite, 'build_replay_suite', unexpected)
    seeds = (schedule([3], drain=2),)
    if runtime_member:
        seeds += _requests()[:1]
    generated = _generated(seeds)
    assert generated.replay_suite is None
    assert generated.replay_suite is None


def test_failed_plan_construction_does_not_cache_success(monkeypatch):
    generated = _generated(_requests())
    build = replay_suite.build_replay_suite

    def interrupted(*args, **kwargs):
        raise RuntimeError('construction interrupted')

    monkeypatch.setattr(replay_suite, 'build_replay_suite', interrupted)
    with pytest.raises(RuntimeError, match='construction interrupted'):
        _ = generated.replay_suite
    monkeypatch.setattr(replay_suite, 'build_replay_suite', build)
    assert generated.replay_suite == build(generated.task.contract, generated.stimuli,
                                           baseline=generated.baseline)


def test_scoring_and_both_command_handoffs_reuse_generated_plan(tmp_path, monkeypatch):
    build = replay_suite.build_replay_suite
    calls = []

    def counted(*args, **kwargs):
        if 'baseline' in kwargs:  # The preceding baseline phase has its own plan.
            calls.append(args)
        return build(*args, **kwargs)

    monkeypatch.setattr(replay_suite, 'build_replay_suite', counted)
    c, reference = contract(), _ref()
    task = api.admit(c, realization(), reference=reference)
    request = prepare_device_command(
        task, Doubling(), run_directory=tmp_path / 'run',
        baseline=api.build_baseline(c, policy=api.BaselinePolicy(
            case_limit=3, max_queue_depth=2)),
        providers=(_BaselineOnly(),), generation_spec=None,
        probe_budget=0, run_analysis=False, reference=reference)
    result = api.handle_command(request)
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.evaluation.complete and result.evaluation.passed
    assert result.evaluation.stimuli == result.generated.replay_suite.stimuli
    assert result.generated.replay_suite.continuous
    assert len(calls) == 1
