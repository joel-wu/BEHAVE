"""A bounded RTL request includes the chosen initial state, not just its stimulus."""

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = [pytest.mark.solver, pytest.mark.cosim]

from behave.sim import api
from behave.sim.rtl.artifact import RTLArtifact
from behave.sim.stimulus_set import GenerationSpec



def test_state_ownership_preserves_a_bit_constant_only_after_an_edge(tmp_path):
    from .test_rtl_public_analysis_path import START_DONE_RTL

    path = tmp_path / "top.sv"
    path.write_text(START_DONE_RTL)
    artifact = RTLArtifact(path, "public_start_done", tmp_path / "source")
    declared = api.TaskContract(endpoints=(), clocks=(api.ClockDomain(
        "clk", "clk", "rst", reset_restores=True),))
    ownership = artifact.state_ownership(declared, expected_identity=artifact.identity)
    # out_y[0] becomes zero on an update, but its pre-reset value is arbitrary.
    # State/clock discovery must not optimize that initial choice away.
    assert dict(ownership.widths) == {"busy": 1, "done": 1, "out_y": 8, "saved": 8}
    assert all(field.reset == "rst" for field in ownership.fields)


def test_atomic_worker_and_source_replay_share_the_selected_state(tmp_path):
    from pathlib import Path

    from behave.sim.core.goal_provider import EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY
    from behave.sim.rtl.exact_job_context import RTLExactJobContext

    from .test_rtl_initial_state import initial_fixture

    artifact, declared, stimulus, _ = initial_fixture(
        tmp_path, True, False, False, "rising", declared_initial=False,
        primary_domain="clk")
    source = Path(artifact.rtl_path).read_text()
    # This admission test exposes only task-declared ports. Retain B's internal
    # counter by using it in ready, rather than adding an unclaimed output port.
    source = source.replace(
        ", output logic [2:0] b_value", "").replace(
        "assign in_ready = 1'b1;", "logic [2:0] b_value;\n    assign in_ready = b_value[0];")
    Path(artifact.rtl_path).write_text(source)
    task = api.admit(declared, api.realization_of_rtl_source(
        source, top="top", contract=declared))
    frontend = artifact.frontend_catalogue(expected_identity=artifact.identity)
    policy = api.evaluation_policy(task, frontend)
    capability = policy.bind_complete_implementation(
        EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY, policy.manifest)
    spec = GenerationSpec(horizon=2, queue_bound=1, reset_hold_cycles=1, drain=0)
    context = RTLExactJobContext(
        artifact, task.contract, spec, frontend, policy.manifest,
        workdir=tmp_path / "context", clock_schedule=stimulus.clock_schedule)
    context.bind_goal_policy(policy, capability)
    from behave.sim.rtl.analysis_binding import bind_analysis

    bound = bind_analysis(
        task, artifact, spec, frontend, exact_context=context,
        goal_policy=policy, goal_capability=capability)
    selected = {"b_value": 2, "value": 6}
    rows = []
    for slot in stimulus.slots:
        row = {name: low for name, (low, _high) in bound.domain.contract.items()}
        row.update(dict(slot.controls))
        row.update({f"initial.rtl.{name}": value for name, value in selected.items()})
        rows.append(row)
    assert bound.validate(rows) == []
    witness = bound.instance.audit_request(rows, bound.domain.digest())
    assert hasattr(witness, "states"), witness.as_dict()
    assert dict(witness.states[0]["artifact.state"]) == selected
    assert dict(witness.states[1]["artifact.state"]) == dict.fromkeys(selected, 0)
    assert dict(witness.states[2]["artifact.state"]) == dict.fromkeys(selected, 1)
