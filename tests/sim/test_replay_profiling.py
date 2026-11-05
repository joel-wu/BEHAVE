"""Performance diagnostics do not select a different replay or scoring path."""

import cProfile
import json
import pstats
import time
from pathlib import Path

import pytest

from behave.sim import api
from behave.sim.rtl.artifact import RTLArtifact
from tests.sim.test_rtl_tool_isolation import _completed
from tools import profile_dataset_command, qualify_dataset_baseline
from tools.dataset_runtime_snapshot import freeze


def test_replay_profile_configuration_is_explicit_and_roundtrips(tmp_path):
    config = api.RTLConfig(tmp_path / 'rtl.sv', 'M', tmp_path / 'artifact')
    assert config.profile_replay is False
    enabled = api.RTLConfig(**{**config.as_dict(), 'profile_replay': True})
    assert enabled.as_dict()['profile_replay'] is True
    assert enabled.timeout_s == config.timeout_s
    assert enabled.timeout_s == config.timeout_s
    with pytest.raises(TypeError, match='profile_replay'):
        api.RTLConfig(**{**config.as_dict(), 'profile_replay': 'true'})


def test_repeated_profiles_are_retained_without_overwriting(tmp_path):
    stimulus = api.Stimulus({}, [])
    execution = _completed(outputs={'cocotb.pstat': b'complete profile'})
    artifact = RTLArtifact('unused.sv', 'M', tmp_path, profile_replay=True)
    artifact._retain_replay_profile(execution, stimulus)
    artifact._retain_replay_profile(execution, stimulus)
    profiles = tuple((tmp_path / 'profiles').glob('*.pstat'))
    assert len(profiles) == 2
    assert all(path.name.startswith(stimulus.fingerprint) for path in profiles)
    assert all(path.read_bytes() == b'complete profile' for path in profiles)
    plain = RTLArtifact('unused.sv', 'M', tmp_path / 'plain')
    plain._retain_replay_profile(execution, stimulus)
    assert not (tmp_path / 'plain').exists()


@pytest.fixture
def profile_snapshot(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'witness').write_text('frozen input')
    root = tmp_path / 'snapshot'
    freeze(source, root, ('witness',))
    row = {'task': 'data/problems/domain/example', 'rtl_top': 'ExplicitTop'}
    (root / 'plan.json').write_text(json.dumps({'tasks': [row]}))
    return root, row


@pytest.mark.parametrize('artifact', ['rtl', 'behavior'])
@pytest.mark.parametrize('timeout', [False, True])
def test_profile_tool_uses_the_population_worker_and_preserves_diagnostic_stop(
        tmp_path, monkeypatch, profile_snapshot, artifact, timeout):
    root, row = profile_snapshot
    seen = []

    def worker(checkout, work, task_row, kind, *, profile_replay):
        seen.append((task_row, kind, profile_replay))
        if timeout:
            time.sleep(10)
        if profile_replay:
            profiles = work / 'artifact/profiles'
            profiles.mkdir(parents=True)
            profiler = cProfile.Profile()
            profiler.runcall(sum, range(100))
            profiler.dump_stats(str(profiles / 'sample.pstat'))
        return {'status': 'no-result', 'reason': 'test capability not available'}

    monkeypatch.setattr(qualify_dataset_baseline, 'worker', worker)
    output = tmp_path / 'profile'
    assert profile_dataset_command.main(output, root, 'example', 0.05 if timeout else 5,
                                         artifact_kind=artifact) == int(timeout)
    result = json.loads((output / 'profile-result.json').read_text())
    assert seen == [(row, artifact, artifact == 'rtl')]
    assert result['reused_score'] is False
    assert 'decision' not in result
    if timeout:
        assert result['state'] == 'diagnostic-timeout'
        assert 'qualification' not in result
    else:
        assert result['qualification']['status'] == 'no-result'
        assert bool(result['simulator']) == (artifact == 'rtl')


@pytest.mark.parametrize('seconds', [0, -1, float('inf'), float('nan')])
def test_profile_budget_is_finite_and_positive_before_starting(tmp_path, seconds):
    with pytest.raises(ValueError, match='finite positive'):
        profile_dataset_command.main(tmp_path / 'out', tmp_path / 'absent', 'task', seconds)
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('worker_failed', [False, True])
def test_profile_summary_failure_preserves_the_workload_outcome(
        tmp_path, monkeypatch, profile_snapshot, worker_failed):
    root, _ = profile_snapshot

    def worker(*args, **kwargs):
        if worker_failed:
            raise RuntimeError('original workload failure')
        return {'status': 'no-result', 'reason': 'original typed refusal'}

    def failed_summary(*args):
        raise OSError('profile summary could not be saved')

    monkeypatch.setattr(qualify_dataset_baseline, 'worker', worker)
    monkeypatch.setattr(profile_dataset_command, '_profile_summary', failed_summary)
    output = tmp_path / 'out'
    assert profile_dataset_command.main(output, root, 'example', 5) == 1
    report = json.loads((output / 'profile-result.json').read_text())
    assert 'profile summary could not be saved' in report['profile_error']
    if worker_failed:
        assert report['state'] == 'exception'
        assert 'original workload failure' in report['error']
    else:
        assert report['state'] == 'profile-error'
        assert report['qualification']['reason'] == 'original typed refusal'


@pytest.mark.cosim
@pytest.mark.isolation
def test_public_profiled_replay_matches_unprofiled_outputs_and_goal_hits(tmp_path):
    from tests.sim.test_runtime_public_handles import SOURCE
    from tests.sim.test_settled_protocol import contract

    task_contract = contract()
    source = ('module M(input wire [7:0] in_a, output wire [7:0] out_y); '
              'assign out_y = in_a << 1; endmodule')
    rtl = tmp_path / 'rtl.sv'
    rtl.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                   {'behavior.py': SOURCE.encode()}, config={'class': 'M'})
    realization = api.realization_of_rtl_source(source, top='M', contract=task_contract)
    task = api.admit(task_contract, realization, reference=reference)
    records = []
    for enabled in (False, True):
        work = tmp_path / str(enabled)
        work.mkdir()
        handle = api.prepare_rtl(task, api.RTLConfig(rtl, 'M', work / 'artifact', profile_replay=enabled))
        command = handle.command_request(
            run_directory=work / 'command', baseline=api.BaselinePolicy(case_limit=2),
            reference=reference, search_policy=api.SearchPolicy(time_budget_s=0), run_analysis=False)
        result = api.handle_command(command)
        assert result.decision is not None and result.decision.authoritative, result.as_dict()['failure']
        assert result.decision.passed and result.evaluation.complete, result.as_dict()
        records.append([(run.stimulus.fingerprint, run.trace.as_dict()) for run in result.evaluation.results])
        profiles = tuple((work / 'artifact/profiles').glob('*.pstat'))
        assert bool(profiles) is enabled
        for path in profiles:
            stats = pstats.Stats(str(path))
            assert stats.total_calls > 0
            assert any(Path(key[0]).name == 'replay_runtime.py' for key in stats.stats)
    assert records[0] == records[1]
