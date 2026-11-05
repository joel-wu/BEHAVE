"""A public resource scope reuses builds, never simulation or score results."""

import pytest

from behave.sim import api
from behave.sim.rtl import replay_build
from tests.sim.test_rtl_replay_build import _compiler


@pytest.mark.parametrize('kind', (api.RTLHandle, api.BehaviorHandle))
def test_public_session_owns_only_builds_and_unwinds_exceptions(monkeypatch, kind):
    # The resource method only needs artifact ownership, not an executable task.
    handle = object.__new__(kind)
    artifact = object()
    object.__setattr__(handle, '_artifact', artifact)
    calls = _compiler(monkeypatch)
    inputs = {'rtl.sv': b'source', 'run0/Makefile': b'compile'}
    with pytest.raises(LookupError, match='caller failure'):
        with handle.replay_session() as active:
            assert active is handle
            outer = replay_build.current_replay_build(artifact)
            first, reused = outer.compile(inputs, timeout_s=1)
            assert not reused
            with handle.replay_session():
                assert replay_build.current_replay_build(artifact) is outer
                assert outer.compile(inputs, timeout_s=1) == (first, True)
            raise LookupError('caller failure')
    with pytest.raises(RuntimeError, match='no owning'):
        replay_build.current_replay_build(artifact)
    with handle.replay_session():
        later = replay_build.current_replay_build(artifact)
        assert later is not outer
        assert not later.compile(inputs, timeout_s=1)[1]
    assert len(calls) == 2


@pytest.mark.isolation
@pytest.mark.parametrize('kind', ('behavior', pytest.param('rtl', marks=pytest.mark.eda)))
@pytest.mark.parametrize('mutant', (False, True))
def test_public_session_reexecutes_full_pools_and_keeps_the_same_verdict(
        tmp_path, kind, mutant):
    from tests.sim.test_baseline_public_sequence import _contract
    from tests.sim.test_parallel_public_replay import BEHAVIOR, REFERENCE, RTL, _semantic

    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': REFERENCE.encode()}, config={'class': 'M'})
    spec = api.GenerationSpec(16, 8, 2, 2)
    if kind == 'behavior':
        source = BEHAVIOR.replace('ERROR', '1' if mutant else '0')
        path = tmp_path / 'candidate.py'
        path.write_text(source)
        realization = api.realization_of_behavior_source(source, contract, file='candidate.py')
        task = api.admit(contract, realization, reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file='candidate.py'), spec)
    else:
        source = RTL.replace('ERROR', "8'd1" if mutant else "8'd0")
        path = tmp_path / 'candidate.sv'
        path.write_text(source)
        realization = api.realization_of_rtl_source(source, top='M', contract=contract)
        task = api.admit(contract, realization, reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, 'M', tmp_path / 'rtl'), spec)
    stimuli = api.build_baseline(contract, spec, policy=api.BaselinePolicy(
        case_limit=2, max_queue_depth=3), realization=realization).stimuli

    def replay(*, capture=False):
        result = handle.replay(stimuli, reference=reference, capture_analysis=capture)
        assert result.authoritative and result.passed is not mutant
        assert result.evaluation.complete
        return result.evaluation

    first = replay()
    with handle.replay_session():
        second, third = replay(), replay()
    fourth = replay()
    assert _semantic(first) == _semantic(second) == _semantic(third) == _semantic(fourth)
    assert second is not third  # Neither score nor trace evaluation was memoized.
    if kind == 'rtl':
        roles = [entry['role'] for entry in handle._artifact.tool_lineage()]
        assert roles.count('verilator-build') == 3
        assert sum(role.startswith('verilator-simulate:') for role in roles) >= 4

    # Passive evidence has its own trace identity, but no execution or scoring
    # difference. Both a correct and a faulty candidate keep the same result.
    captured = replay(capture=True)
    assert captured.hits == first.hits
    for plain, observed in zip(first.results, captured.results, strict=True):
        before, after = plain.trace.as_dict(), observed.trace.as_dict()
        if kind == 'rtl':
            from behave.sim.rtl.source_boundary_capture import HISTORY_VERSION

            history = after['query_boundaries'].pop('history')
            assert history['version'] == HISTORY_VERSION
            assert history['startup'] is not None and history['refusal'] is None
        assert before == after
        before, after = plain.comparison.as_dict(), observed.comparison.as_dict()
        assert before['of'].pop('trace') == plain.trace.fingerprint
        assert after['of'].pop('trace') == observed.trace.fingerprint
        if before['reference_source'] is not None:
            assert before['reference_source'].pop('trace') == plain.trace.fingerprint
            assert after['reference_source'].pop('trace') == observed.trace.fingerprint
        assert before == after
