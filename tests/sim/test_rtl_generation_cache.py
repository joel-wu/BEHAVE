"""Native RTL replay reuses completed traces only for the same initialization choice."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.goal_provider import (
    EXACT_BOUNDED_HIT,
    RTL_EXACT_BOUNDED_POLICY,
)
from behave.sim.goal_policy import evaluation_goal_policy
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.rtl.exact_job_context import RTLExactJobContext
from behave.sim.rtl.exact_job_layout import RTLGenerationLayout
from behave.sim.stimulus_set import GenerationSpec

from .test_api_path import contract
from .test_rtl_generation_provider import _seed


@pytest.fixture(name="context_runtime")
def _context_runtime(tmp_path, monkeypatch):
    """One empty design whose source layout worker returns a fixed finite layout."""
    rtl = tmp_path / "cached.sv"
    rtl.write_text("module cached; endmodule\n")
    layouts = []
    monkeypatch.setattr(
        RTLArtifact, "frontend_catalogue",
        lambda _self, *, expected_identity: CatalogueManifest(()))

    def worker(job, _payload, **_kwargs):
        assert job == "rtl_generation_layout"
        layouts.append(job)
        return RTLGenerationLayout(("out_valid",), ("q",)).as_dict()

    monkeypatch.setattr("behave.sim.worker.run_job_strict", worker)
    return SimpleNamespace(rtl=rtl, layouts=layouts)


def _context(rtl, workdir):
    task_contract = contract(answers=1)
    frontend = CatalogueManifest(())
    policy = evaluation_goal_policy(task_contract, frontend, artifact_kind="rtl")
    capability = policy.bind_complete_implementation(
        EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY, policy.manifest)
    artifact = RTLArtifact(rtl, "cached", workdir / "sim", timeout_s=900)
    context = RTLExactJobContext(
        artifact, task_contract,
        GenerationSpec(horizon=7, queue_bound=2, reset_hold_cycles=3, drain=0),
        frontend, policy.manifest, workdir=workdir)
    context.bind_goal_policy(policy, capability)
    return context


def test_native_witness_cache_distinguishes_startup_pins_and_settled_deposit(
        tmp_path, context_runtime, monkeypatch):
    from behave.sim.core.physical_value import packed_width
    from behave.sim.core.refusal import Refused
    from behave.sim.rtl.initial_state import RTLInitialState
    from behave.sim.rtl.startup_state import RTLStartupState

    context = _context(context_runtime.rtl, tmp_path / 'witnesses')
    layout = RTLGenerationLayout(("out_valid",), ("q",))
    monkeypatch.setattr(RTLExactJobContext, 'prepare_source', lambda self: layout)
    calls = []

    def replay(_self, _contract, _stimulus, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(artifact=context.artifact_identity)

    monkeypatch.setattr(RTLArtifact, 'exact_trace', replay)
    stimulus = _seed()
    registers = RTLInitialState.from_values(context.artifact_identity, {'q': 2}, {'q': 1})
    pins = {field.port: packed_width(field.type)
            for endpoint in context.contract.inputs() for field in endpoint.fields}
    pins.update((pin, 1) for pin in context.contract.environment_controls())
    pins.update((pin, 1) for clock in context.contract.clocks for pin in (clock.clock, clock.reset))
    startup = RTLStartupState(registers, sorted((name, width, 0) for name, width in pins.items()))
    raw = context.source_trace(stimulus)
    settled = context.source_trace(stimulus, initial_state=registers)
    construction = context.source_trace(stimulus, startup_state=startup)
    changed = RTLStartupState(registers, sorted(
        (name, width, int(name == context.contract.clocks[0].reset)) for name, width in pins.items()))
    other_pins = context.source_trace(stimulus, startup_state=changed)
    assert len(calls) == 4 and calls[2]['startup_state'] is startup
    assert calls[2]['initial_state'] is None and calls[1]['initial_state'] is registers
    assert context.source_trace(stimulus) is raw
    assert context.source_trace(stimulus, initial_state=registers) is settled
    assert context.source_trace(stimulus, startup_state=startup) is construction
    assert context.source_trace(stimulus, startup_state=changed) is other_pins
    assert len(calls) == 4
    with pytest.raises(ValueError, match='one initialization phase'):
        context.source_trace(stimulus, initial_state=registers, startup_state=startup)
    with pytest.raises(TypeError, match='exact startup choice'):
        context.source_trace(stimulus, startup_state=registers)
    incomplete = RTLStartupState(registers, ())
    with pytest.raises(Refused, match='layout-mismatch'):
        context.source_trace(stimulus, startup_state=incomplete)
    foreign = RTLStartupState(RTLInitialState.from_values('f'*64, {'q': 2}, {'q': 1}), startup.pins.fields)
    with pytest.raises(Refused, match='artifact-mismatch'):
        context.source_trace(stimulus, startup_state=foreign)
    assert len(calls) == 4  # Invalid choices cannot reach the native runner/cache.
