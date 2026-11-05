"""A local source context is a capability slice, never the score denominator."""

import json

import pytest

from behave.sim import api
from behave.sim.core.catalogue import COVERAGE, CatalogueManifest, Goal, OccurrenceKey, catalogue_subset
from behave.sim.core.goal_provider import (
    BEHAVIOR_LOCAL_GUIDANCE_POLICY,
    EXACT_BOUNDED_HIT,
    LOCAL_GUIDANCE,
)
from behave.sim.goal_policy import evaluation_goal_policy

from .test_baseline_public_sequence import REFERENCE, _contract


def _contract_with_observations():
    base = _contract()
    monitor = api.TransactionMonitorSpec("ordered-output", api.FiniteMonitor(
        (0, 1), ("zero", "other"), 0, ("zero-then-other",), (
            api.FiniteMonitorTransition(0, "zero", 1, ()),
            api.FiniteMonitorTransition(0, "other", 0, ()),
            api.FiniteMonitorTransition(1, "zero", 1, ()),
            api.FiniteMonitorTransition(1, "other", 0, ("zero-then-other",)))), (
        api.TransactionEvent("zero", "out", api.GoalCondition("eq", api.GoalField("y"), 0)),
        api.TransactionEvent("other", "out")), order=("out",))
    return api.TaskContract(base.endpoints, base.clocks, observation_goals=[
        api.TransactionGoal("input-seven", "in", api.GoalCondition("eq", api.GoalField("a"), 7)),
        api.TransactionGoal("output-seven", "out", api.GoalCondition("eq", api.GoalField("y"), 7)),
        api.TransactionGoal("after-seven", "out", api.GoalCondition("and",
            api.GoalCondition("eq", api.GoalField("y", previous=True), 7),
            api.GoalCondition("ne", api.GoalField("y"), 7))),
        api.TransactionGoal("outside-carrier", "out", api.GoalCondition("eq", api.GoalField("y"), 256)),
    ], transaction_monitors=(monitor,))


def _frontend():
    return CatalogueManifest((Goal("behavior.branch.true", COVERAGE, family="decision",
        provider="python-structural", event="T", policy="source-site/1"),))


def test_local_context_does_not_narrow_the_frozen_policy():
    frontend = _frontend()
    policy = evaluation_goal_policy(_contract_with_observations(), frontend, artifact_kind="behavior")
    original = policy.as_dict()
    capability = policy.bind_implementation(
        LOCAL_GUIDANCE, BEHAVIOR_LOCAL_GUIDANCE_POLICY, policy.manifest,
        tuple(frontend), exhaustive=False, context_catalogue=frontend)
    assert capability.source_catalogue is frontend
    assert capability.targets == frontend
    assert capability.policy_digest == policy.digest()
    assert policy.as_dict() == original
    assert set(frontend) < set(policy.manifest)
    with pytest.raises(ValueError, match="only valid for local"):
        policy.bind_implementation(EXACT_BOUNDED_HIT, BEHAVIOR_LOCAL_GUIDANCE_POLICY,
                                   policy.manifest, context_catalogue=frontend)
    # Finite task monitors now have an encoder. A *partial* local context must
    # still not masquerade as the complete exact catalogue.
    with pytest.raises(api.Refused):
        policy.bind_complete_implementation(EXACT_BOUNDED_HIT, "test/1", frontend)


def test_local_context_cannot_rewrite_a_goal_or_delete_its_occurrences():
    from .test_goal_provider_capabilities import _declarative_provider, _enabled_policy

    _schema, provider = _declarative_provider()
    policy = _enabled_policy(provider)
    implementation = provider.binding(LOCAL_GUIDANCE).implementation
    partial = catalogue_subset(policy.manifest, ("extension.bin.low",))
    # The context may not hide a target that this implementation registers.
    with pytest.raises((ValueError, api.Refused)):
        policy.bind_implementation(LOCAL_GUIDANCE, implementation, policy.manifest,
                                   context_catalogue=partial)
    native = _frontend()
    frontend = CatalogueManifest(tuple(native.values()), occurrences=((
        OccurrenceKey("python-structural", ("branch", 0), "T"), "behavior.branch.true"),))
    policy = evaluation_goal_policy(_contract_with_observations(), frontend, artifact_kind="behavior")
    missing_occurrences = CatalogueManifest(tuple(frontend.values()))
    with pytest.raises(api.Refused) as removed:
        policy.bind_implementation(LOCAL_GUIDANCE, BEHAVIOR_LOCAL_GUIDANCE_POLICY, policy.manifest,
                                   context_catalogue=missing_occurrences)
    assert removed.value.refusal.kind == "catalogue-local-context-missing-occurrence"

    frontend = _frontend()
    policy = evaluation_goal_policy(_contract_with_observations(), frontend, artifact_kind="behavior")
    rewritten = CatalogueManifest((Goal("behavior.branch.true", COVERAGE, family="decision",
        provider="python-structural", event="F", policy="source-site/1"),))
    with pytest.raises(api.Refused):
        policy.bind_implementation(LOCAL_GUIDANCE, BEHAVIOR_LOCAL_GUIDANCE_POLICY, policy.manifest,
                                   context_catalogue=rewritten)


def test_worker_layout_explicitly_preserves_frontend_ownership():
    from behave.sim.engines.behavior.generation_job import BehaviorGenerationLayout, decode_layout_reply

    frontend = _frontend()
    policy = evaluation_goal_policy(_contract_with_observations(), frontend, artifact_kind="behavior")
    layout = BehaviorGenerationLayout("source", policy.manifest, tuple(policy.manifest),
        (("in", (("a", "a"),)),), frontend_catalogue=frontend)
    image = layout.as_dict()
    assert image["frontend_goals"] == list(frontend)
    restored = decode_layout_reply(image)
    assert restored.frontend_catalogue == frontend
    assert restored.catalogue == policy.manifest
    assert restored.as_dict() == image
    assert set(restored.frontend_catalogue) < set(restored.catalogue)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("kind", ("behavior", pytest.param("rtl", marks=pytest.mark.eda)))
@pytest.mark.parametrize("with_branch", (False, True))
def test_source_search_and_task_observations_coexist_in_public_scoring(tmp_path, kind, with_branch):
    contract = _contract_with_observations()
    source = REFERENCE.replace('        return {"y": a}',
        '        if a == 7:\n            return {"y": a}\n        return {"y": a}') if with_branch else \
        REFERENCE.replace('return {"y": a}', 'return {"y": a ^ 85}')
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode()}, config={"class": "M"})
    if kind == "behavior":
        path = tmp_path / "candidate.py"
        path.write_text(source)
        task = api.admit(contract, api.realization_of_behavior_source(source, contract, file="candidate.py"),
                         reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(path, file="candidate.py", timeout_s=90),
                                      bounded_analysis=False)
    else:
        rtl = '''module M(input wire clk,rst,in_valid,out_ready,input wire [7:0] a,
                         output wire in_ready,out_valid,output reg [7:0] y);
          assign in_ready=out_ready;
          assign out_valid=in_valid;
          always_comb begin
            if(a==8'd7) y=a;
            else y=a;
          end
        endmodule'''
        if not with_branch:
            rtl = rtl.replace("if(a==8'd7) y=a;\n            else y=a;", "y=a ^ 8'd85;")
        path = tmp_path / "candidate.sv"
        path.write_text(rtl)
        task = api.admit(contract, api.realization_of_rtl_source(rtl, top="M", contract=contract), reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl", timeout_s=120))
    providers = handle.generation_providers(reference, workdir=tmp_path / "guidance")
    if kind == "behavior" and not with_branch:
        # Independently exercise the candidate encoder; other controls include
        # the reference direction. Both must not receive a fresh shared budget.
        providers = tuple(provider for provider in providers if "candidate:" in provider.name)
        assert len(providers) == 1
    request = handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100),
        providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True),
        run_analysis=False)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.status
    assert result.evaluation.complete and result.decision.authoritative and result.decision.passed
    catalogue = result.evaluation.catalogue
    declared = {"observation:" + rule.name for rule in contract.observation_goals}
    assert declared <= set(catalogue)
    sequence_goals = {spec.goal_id(goal) for spec in contract.transaction_monitors for goal in spec.monitor.goals}
    assert sequence_goals <= set(catalogue)
    assert {"observation:input-seven", "observation:output-seven"} <= set(result.evaluation.hits)
    assert "observation:outside-carrier" not in result.evaluation.hits
    baseline_ids = {seed.identity_bytes for seed in request.baseline.stimuli}
    assert all(seed.depth("in") == 100 for seed in request.baseline.stimuli)
    assert any(run.stimulus.identity_bytes not in baseline_ids for run in result.evaluation.results)
    for run in result.evaluation.results:
        if run.stimulus.identity_bytes in baseline_ids:
            assert all(payload == (0,) for record in run.trace.records
                       for name, payload in record.accepted if name == "in" and payload is not None)
    assert len(result.generated.searches) == len(providers)
    assert 0 < sum(search.telemetry["probes"] for search in result.generated.searches) <= 4
    for search in result.generated.searches:
        if search.telemetry["probes"] == 0:
            assert search.telemetry["termination"] == "probe-budget-exhausted"
            assert search.telemetry["probe_budget"] == 0
            continue
        feedback = search.as_dict()["telemetry"]["baseline_coverage"]
        # Both native query providers retain the task predicates; RTL binds
        # their history in its protocol product, not the source-event interval.
        assert declared <= set(feedback["targetable"])
        # This context now carries the finite sequence state as well.
        assert sequence_goals <= set(feedback["targetable"])
        if search.telemetry["provider_policy"] == BEHAVIOR_LOCAL_GUIDANCE_POLICY and not with_branch:
            assert any(set(dict(row)["targets"]) <= declared | sequence_goals for rows in search.provenance.values()
                       for row in rows), "a new input must come from a transaction-predicate query"
        assert feedback["unhit"]
        assert not feedback["remaining"] or search.telemetry["probes"] > 0
