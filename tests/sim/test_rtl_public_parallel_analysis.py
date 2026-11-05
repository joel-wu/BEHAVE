"""Parallel admission through baseline, classification and independent witnesses."""

import pytest

from behave.sim import api, job_session

from .test_rtl_public_protocol_analysis import _check_protocol

pytestmark = [pytest.mark.cosim, pytest.mark.solver]


@pytest.mark.parametrize('faulty', [False, True])
def test_public_parallel_analysis_keeps_native_evidence_and_score(tmp_path, monkeypatch, faulty):
    monkeypatch.setenv('BEHAVE_JOB_CPU_LIMIT', '2')
    original, limits = job_session._job_spec, []

    def job_spec(name, *args, **kwargs):
        spec = original(name, *args, **kwargs)
        if name == 'rtl_analysis_session':
            limits.append(dict(spec.environment)['BEHAVE_JOB_CPU_LIMIT'])
        return spec

    monkeypatch.setattr(job_session, '_job_spec', job_spec)
    with api.CpuPool(2) as pool, api.cpu_scope(pool.endpoint) as usage:
        _check_protocol(tmp_path, 'tagged', faulty, fixed_schedule=False)
        assert limits == ['2']
        assert pool.snapshot()['reserved'] == 0 and not pool.snapshot()['quarantined']
    assert all(row['released'] for row in usage.as_dict()['reservations'])
