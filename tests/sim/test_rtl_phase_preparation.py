"""Public search/analysis share construction, not phase authority or answers."""


import pytest

from behave.sim import api
from behave.sim.engines.rtl.generation import RTLGenerationProvider



def test_rtl_handoff_does_not_inspect_other_frontend_requests():
    from behave.sim.production_generation import reuse_rtl_request

    class OtherRequest:
        @property
        def goal_capability(self):
            raise AssertionError('composition inspected an unrelated request')

    request = OtherRequest()
    assert reuse_rtl_request(request, ()) is request


@pytest.mark.cosim
def test_public_search_and_completed_analysis_share_one_real_relation(tmp_path):
    import json

    from tests.support.reference import load as load_test_reference

    from .test_rtl_public_analysis_path import REFERENCE, RTL, _contract

    source = tmp_path / "public_exact_wire.sv"
    source.write_text(RTL)
    task_contract = _contract()
    reference = load_test_reference(
        {"behavior.py": REFERENCE.encode()}, config={"class": "Identity"},
        loader="shared public RTL preparation fixture")
    task = api.admit(task_contract, api.realization_of_rtl_source(
        RTL, "shared public RTL preparation fixture", top="public_exact_wire",
        contract=task_contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(
        source, "public_exact_wire", tmp_path / "rtl", timeout_s=120),
        api.GenerationSpec(horizon=5, queue_bound=1, reset_hold_cycles=3, drain=1))
    recipes = handle.generation_providers(reference, workdir=tmp_path / "generation")
    provider = RTLGenerationProvider.from_artifact(
        handle._artifact, task_contract, handle._generation_spec, handle.frontend_catalogue,
        workdir=tmp_path / "fixed-query", goal_policy=handle._goal_policy)
    context = provider.exact_job_context()
    providers = (recipes[0], provider)
    # Sharing the exact context requires the same finite domain. A bare policy
    # now requests runtime scheduling, whose recorded local domains are distinct
    # from this explicitly bounded analysis domain.
    baseline = api.build_baseline(
        task_contract, handle._generation_spec,
        policy=api.BaselinePolicy(case_limit=1, max_queue_depth=1))
    command = handle.command_request(
        run_directory=tmp_path / "command",
        baseline=baseline,
        providers=providers, probe_budget=2, run_analysis=True, reference=reference,
        analysis_wall_timeout_seconds=180)
    assert command.analysis_policy.feasibility_timeout_ms is None
    assert command.analysis_policy.query_timeout_ms is None
    assert command._analysis_request.exact_context is context
    result = api.handle_command(command)
    (tmp_path / "public-analysis.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert context.goal_policy is command.goal_policy
    assert context.goal_capability_use is command._analysis_request.goal_capability
    assert result.evaluation.complete and result.evaluation.passed
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    assert result.analysis.concrete == result.evaluation.fingerprint
    assert command.exact_capability_unchanged()
    # Both phases share the compiler-owned source relation.
    assert context._source_layout is not None
    assert any(row["op"] == "probe" for row in provider.execution_cost()["operations"])
    assert sum(row["role"] == "rtl-source-query"
               for row in handle._artifact.tool_lineage()) == 1
