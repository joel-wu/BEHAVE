"""Changing the observer cannot silently change the published budget policy."""

from dataclasses import replace

import pytest

from behave.sim import api
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.rtl import tool_isolation
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.observation_binding import ObservationBinding, PreparedObservation
from behave.sim.rtl.replay_failure import ReplayPurpose, budget_failure, replay_seconds

from .test_rtl_execution_policy import _contract
from .test_rtl_native_preparation import _catalogue, _plan
from .test_rtl_observation_binding import _prepared
from .test_rtl_tool_isolation import _command_row, _completed


@pytest.mark.parametrize("native", [False, True], ids=["interface-only", "native"])
@pytest.mark.parametrize("purpose", list(ReplayPurpose), ids=lambda value: value.value)
@pytest.mark.parametrize("fault", ["build-timeout", "run-timeout", "malformed-record", "build-failed"])
def test_production_failure_ownership_keeps_purpose_separate_from_observer(
        tmp_path, monkeypatch, native, purpose, fault):
    source = tmp_path / "M.sv"
    source.write_text("module M; endmodule\n")
    artifact = RTLArtifact(source, "M", tmp_path / "run")
    contract = _contract()
    catalogue = _catalogue(_plan()) if native else CatalogueManifest(())
    prepared = _prepared() if native else None
    binding = ObservationBinding(artifact.identity, contract.digest, catalogue.digest())
    if native:
        binding = replace(binding, native_plan=prepared.plan.identity,
                          preparation=prepared.request_sha256)
    artifact._observation = PreparedObservation(binding, prepared)
    monkeypatch.setattr(artifact, "frontend_catalogue", lambda **_kwargs: catalogue)
    calls = []

    def tool(workspace, _groups, *, role, **_kwargs):
        if role == "rtl-source-check":
            calls.append("source-check")
            return _completed(commands=(_command_row("verilator-source-check"),),
                              outputs={"build/Vtop.mk": b"original source builds"})
        build = role == "verilator-build"
        if not build:
            assert _groups[0].commands[0].timeout_s == artifact.timeout_s
        calls.append("build" if build else "run")
        row = _command_row("verilator-build" if build else "verilator-simulate")
        if fault == ("build-timeout" if build else "run-timeout"):
            row.update(returncode=-9, timed_out=True)
        elif build and fault == "build-failed":
            row["returncode"] = 1
        if build:
            if not row["returncode"]:
                (workspace / "build").mkdir()
                (workspace / "build/Vtop").write_bytes(b"test executable")
            return _completed(commands=(row,), outputs={"build/Vtop": b"test executable"})
        return _completed(commands=(row,), outputs={"driven.json": b"not a record"})

    monkeypatch.setattr(tool_isolation, "run_workspace", tool)
    stimulus = api.Stimulus({}, [api.EventSlot(controls={"rst": 0, "out_ready": 1})])
    result, = artifact.traces_per_process(contract, (stimulus,), workers=1, _purpose=purpose)
    assert type(result) is api.RunFailure
    expected_calls = ["build"] if fault.startswith("build") else ["build", "run"]
    if fault == "build-failed" and native and purpose is ReplayPurpose.CONCRETE:
        expected_calls.append("source-check")
    assert calls == expected_calls
    if fault.endswith("timeout"):
        concrete = purpose is ReplayPurpose.CONCRETE
        assert result.origin is api.FailureOrigin.RESOURCE_POLICY
        assert result.policy == "rtl-replay-budget/2"
        assert result.disposition is (
            api.FailureDisposition.SCORE_ZERO if concrete else api.FailureDisposition.NO_RESULT)
        assert result.phase == (
            "candidate-" if concrete else "rtl-analysis-") + fault.split("-")[0]
    elif fault == "build-failed" and not native and purpose is ReplayPurpose.CONCRETE:
        assert result.origin is api.FailureOrigin.CANDIDATE
        assert result.disposition is api.FailureDisposition.SCORE_ZERO
    else:
        # Invalid collector/driver records and instrumented build defects cannot
        # be reclassified as bad candidates merely because this is a scored run.
        assert result.origin is api.FailureOrigin.VERIFIER
        assert result.disposition is api.FailureDisposition.NO_RESULT


@pytest.mark.parametrize("purpose", [None, True, "concrete"])
def test_budget_purpose_is_not_inferred_from_an_untyped_flag(purpose):
    with pytest.raises(TypeError, match="explicit purpose"):
        budget_failure(purpose, "run", api.Refusal("timed-out", "the limit expired"))


def test_window_replay_has_one_deadline_not_a_shorter_resource_zero(monkeypatch):
    from behave.sim import scoring_policy

    now = [100.0]
    monkeypatch.setattr(scoring_policy.time, "monotonic", lambda: now[0])
    assert replay_seconds(ReplayPurpose.CONCRETE, 17) == 17
    with api.scoring_scope(api.ScoringPolicy(time_budget_s=1200), deadline=1280):
        assert replay_seconds(ReplayPurpose.CONCRETE, 17) == 1180
        assert replay_seconds(ReplayPurpose.ANALYSIS, 17) == 17
        now[0] += 400
        assert replay_seconds(ReplayPurpose.CONCRETE, 17) == 780
        now[0] = 1280
        with pytest.raises(api.ScoringStopped):
            replay_seconds(ReplayPurpose.CONCRETE, 17)


def test_public_config_retires_replay_specific_allowances():
    from behave.core import runtime_defaults as defaults

    config = api.RTLConfig('model.sv', 'M', 'work', timeout_s=37)
    assert config.timeout_s == 37
    assert 'per_stimulus_s' not in config.as_dict()
    assert not hasattr(defaults, 'SIM_RTL_STIMULUS_SECONDS')
    assert not hasattr(defaults, 'SIM_RTL_SIMULATOR_STARTUP_SECONDS')
    with pytest.raises(TypeError, match='per_stimulus_s'):
        api.RTLConfig('model.sv', 'M', 'work', per_stimulus_s=5)
