"""Circuit-product record checks and their boundary with native source evidence.

The synthetic atomic fixture exercises the old circuit adapter only. Native
whole-history admission is tested in test_rtl_source_analysis_worker; a circuit
catalogue must never be substituted for that observer's source-event catalogue.
"""

from types import SimpleNamespace

import pytest

from behave.sim.rtl import analysis_job


pytestmark = pytest.mark.solver


@pytest.mark.parametrize("override", (None, 2))
def test_parent_dispatch_uses_only_its_bound_domain(monkeypatch, override):
    from behave.sim.core.domain import TraceDomain
    from behave.sim.rtl.analysis import RTLIsolatedAnalysisInstance

    proxy = object.__new__(RTLIsolatedAnalysisInstance)
    proxy._session_active = False
    proxy._session_owner = None
    proxy._session = None
    proxy._context = SimpleNamespace(
        generation_spec=SimpleNamespace(horizon=99),
        artifact=SimpleNamespace(timeout_s=10), analysis_payload=dict)
    proxy._domain = TraceDomain(4)
    calls = []

    def worker(_job, payload, **_kwargs):
        calls.append(payload)
        return {"version": analysis_job.VERSION, "status": "ok",
                "operation": payload["operation"], "value": {}}

    monkeypatch.setattr("behave.sim.worker.run_job_strict", worker)
    if override is None:
        assert proxy._raw_call("decode", rows=[{}] * 4) == {}
        assert calls[0]["horizon"] == 4
    else:
        with pytest.raises(ValueError, match="bound horizon"):
            proxy._raw_call("decode", rows=[{}] * 4, horizon=override)
        assert not calls
