"""Adversarial checks for the paper-facing bounded-evidence boundary.

The final goal partition is a derived view.  These tests keep the lower-level
evidence honest: every solver call remains visible, and no caller can manufacture a
classified result by writing a plausible-looking dictionary.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim import api
from behave.sim.behavior import residual
from behave.sim.symbolic import ranges
from behave.sim.behavior.lowering import lower_with_state_ranges
from behave.sim.core.audit import AuditCheck, AuditedWitness
from behave.sim.core.audit_plan import AuditKind, AuditPlan
from behave.sim.core.catalogue import (
    COVERAGE,
    CatalogueManifest,
    DomainManifest,
    Goal,
    OccurrenceKey,
)
from behave.sim.core.conditional_evidence import (
    AnalysisBinding,
    AnalysisPolicy,
    AnalysisStopEvidence,
    AnalysisStopKind,
    ClassifiedGoalStatus,
    ConditionalEvidence,
    FeasibilityAnswer,
    FeasibilityEvidence,
    FeasibilitySource,
    GoalEvidenceRecord,
    IssueEvidence,
    PassedCheck,
    QueryEvidence,
    TrustedAssumption,
    WitnessEvidence,
    WitnessOrigin,
)
from behave.sim.core.domain import TraceDomain
from behave.sim.core.instance import BoundedInstance, Feasible, Replay, StepOutput
from behave.sim.core.instance import Verdict as QueryVerdict
from behave.sim.core.outcome import (
    AnalysisOutcome,
    ConcreteEvaluation,
    Verdict,
    catalogue_image,
)
from behave.sim.core.query import QueryCall, SolverAnswer

G = Goal("g", COVERAGE, family="test", provider="test", policy="evidence/1")
H = Goal("h", COVERAGE, family="test", provider="test", policy="evidence/1")


def _domain():
    return TraceDomain(
        bound=1, contract={"x": [0, 1]}, frontend="test/1", policy="test",
        artifact="artifact", task_contract="contract")


def _witness(domain, *, hits=()):
    return AuditedWitness(
        domain=domain.digest(), states=[{"s": 0}, {"s": 0}],
        inputs=[{"x": 0}], outputs=[[]], hit_sets=[hits])


def _binding(domain, catalogue, audit_plan=None):
    audit_plan = AuditPlan() if audit_plan is None else audit_plan
    manifest = (catalogue if type(catalogue) is CatalogueManifest
                else CatalogueManifest.from_goals_image(catalogue))
    return AnalysisBinding(
        task_contract="contract", artifact="artifact", provider="artifact",
        domain=domain.digest(), catalogue=manifest.digest(),
        domain_manifest=DomainManifest(domain, manifest).digest(),
        representation="test-representation/1", encoding="test-encoding/1",
        audit_plan=audit_plan.digest())


def _summary(goals):
    statuses = [e.status for e in goals.values()]
    return {
        "n_goals": len(goals),
        "n_reached": statuses.count(ClassifiedGoalStatus.REACHED),
        "n_unreachable": statuses.count(
            ClassifiedGoalStatus.BOUNDED_UNREACHABLE),
        "n_unresolved": statuses.count(ClassifiedGoalStatus.UNRESOLVED),
        "accounted_for": True,
        "query_transcript_complete": True,
    }


def _conditional(*, declared=(G,), goals=None, queries=(), required=(),
                 witnesses=None, checks=None, trusted=None, binding=None,
                 feasibility=None):
    audit_plan = AuditPlan.from_names(list(required))
    domain = _domain()
    catalogue = (declared if type(declared) is CatalogueManifest
                 else catalogue_image(declared))
    feasibility_witness = _witness(domain)
    if feasibility is None:
        feasibility = FeasibilityEvidence(
            FeasibilitySource.EXACT_QUERY,
            witness=feasibility_witness.as_dict(),
            decoded_request=[{"x": 0}], replayed=True)
    trace_required = tuple(
        name for name in required if name in {"step_audit", "source_audit"})
    if witnesses is None:
        witnesses = [WitnessEvidence(
            WitnessOrigin.FEASIBILITY_QUERY, feasibility_witness.as_dict(),
            passed_audits=trace_required)]
        witnesses.extend(
            WitnessEvidence(
                WitnessOrigin.BOUNDED_QUERY,
                query.witness, query_ref=query.query_id,
                passed_audits=query.passed_audits)
            for query in queries
            if query.answer is SolverAnswer.SAT and query.witness is not None)
    if goals is None:
        query = next(
            (item for item in queries if item.answer is SolverAnswer.UNSAT),
            None,
        )
        support = query.query_id if query is not None else "query:missing"
        goals = {G.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.BOUNDED_UNREACHABLE, support)}
    if binding is None:
        binding = _binding(domain, catalogue, audit_plan)
    if checks is None:
        checks = [
            PassedCheck(
                "domain_manifest_binding",
                ("domain-manifest:" + binding.domain_manifest,
                 "provider:" + binding.provider)),
            PassedCheck("task_binding", (
                "domain:" + binding.domain,
                "task-contract:" + binding.task_contract,
                "artifact:" + binding.artifact,
                "provider:" + binding.provider)),
            PassedCheck("catalogue_binding", (
                "catalogue:" + binding.catalogue,
                "provider:" + binding.provider)),
            PassedCheck(
                "audit_plan_binding",
                ("audit-plan:" + binding.audit_plan,
                 "provider:" + binding.provider)),
            PassedCheck(
                "representation_well_formed",
                ("representation:" + binding.representation,
                 "encoding:" + binding.encoding)),
        ]
        for witness in witnesses:
            for name in required:
                if name in {"step_audit", "source_audit"}:
                    checks.append(PassedCheck(name, (witness.witness_ref,)))
        if "goal_set_audit" in required:
            checks.append(PassedCheck(
                "goal_set_audit", ("catalogue:" + binding.catalogue,)))
    if trusted is None:
        trusted = (TrustedAssumption(
            "test-assumption", "the test provider is faithful", scope="test"),)
    return ConditionalEvidence(
        binding=binding, policy=AnalysisPolicy(),
        record_audits=(), feasibility=feasibility,
        domain_manifest=DomainManifest(
            domain,
            catalogue if type(catalogue) is CatalogueManifest
            else CatalogueManifest.from_goals_image(catalogue)),
        audit_plan=audit_plan,
        checks=checks,
        trusted_assumptions=trusted,
        queries=queries, goals=goals, closure=_summary(goals),
        witnesses=witnesses)


@pytest.mark.parametrize(("field", "value"), [
    ("task_contract", "another-contract"),
    ("artifact", "another-artifact"),
    ("provider", "another-provider"),
])
def test_the_binding_cannot_name_task_or_artifact_identities_outside_the_domain(
        field, value):
    domain = _domain()
    catalogue = catalogue_image((G,))
    original = _binding(domain, catalogue)
    values = original.as_dict()
    values["provider"] = values.pop("provider_identity")
    values[field] = value
    forged = AnalysisBinding(**values)
    query = QueryEvidence(0, (G.id,), SolverAnswer.UNSAT)

    with pytest.raises(ValueError, match="task|artifact|provider"):
        _conditional(
            goals={G.id: GoalEvidenceRecord(
                ClassifiedGoalStatus.BOUNDED_UNREACHABLE, query.query_id)},
            queries=(query,), binding=forged)


def test_mandatory_checks_bind_every_identity_the_check_claims_to_compare():
    domain = _domain()
    catalogue = catalogue_image((G,))
    binding = _binding(domain, catalogue)
    query = QueryEvidence(0, (G.id,), SolverAnswer.UNSAT)
    # These were the historical one-subject shapes.  They name each check but omit
    # the provider, task contract, artifact and encoding it is supposed to compare.
    incomplete = (
        PassedCheck("domain_manifest_binding", (
            "domain-manifest:" + binding.domain_manifest,)),
        PassedCheck("task_binding", ("domain:" + binding.domain,)),
        PassedCheck("catalogue_binding", ("catalogue:" + binding.catalogue,)),
        PassedCheck("audit_plan_binding", (
            "audit-plan:" + binding.audit_plan,)),
        PassedCheck("representation_well_formed", (
            "representation:" + binding.representation,)),
    )

    with pytest.raises(ValueError, match="mandatory passed checks"):
        _conditional(
            goals={G.id: GoalEvidenceRecord(
                ClassifiedGoalStatus.BOUNDED_UNREACHABLE, query.query_id)},
            queries=(query,), checks=incomplete)


def test_a_sat_query_request_must_be_the_request_that_was_replayed():
    domain = _domain()
    witness = _witness(domain, hits=(G.id,))
    with pytest.raises(ValueError, match="decoded request|witness inputs"):
        QueryEvidence(
            0, (G.id,), SolverAnswer.SAT,
            decoded_request=[{"x": 1}],
            audited_witness=witness.as_dict(), replayed_hits=(G.id,))


def test_a_sat_query_hit_summary_must_equal_the_retained_witness_hits():
    domain = _domain()
    witness = _witness(domain, hits=(G.id, H.id))
    with pytest.raises(ValueError, match="replayed hits"):
        QueryEvidence(
            0, (G.id,), SolverAnswer.SAT,
            decoded_request=[{"x": 0}], audited_witness=witness.as_dict(),
            replayed_hits=(G.id,))


def test_startup_only_query_hit_has_complete_conditional_evidence():
    witness = AuditedWitness(**{**_witness(_domain()).as_dict(), 'initial_hits': [G.id]})
    query = QueryEvidence(0, (G.id,), SolverAnswer.SAT,
        decoded_request=[{'x': 0}], audited_witness=witness.as_record(), replayed_hits=(G.id,))
    evidence = _conditional(queries=(query,), goals={G.id: GoalEvidenceRecord(
        ClassifiedGoalStatus.REACHED, query.witness_ref)})
    assert evidence.goals[G.id]['status'] == ClassifiedGoalStatus.REACHED.value
    assert not any(witness.hit_sets)
    with pytest.raises(ValueError, match='reaching an asked goal'):
        QueryEvidence(0, (G.id,), SolverAnswer.SAT, decoded_request=[{'x': 0}],
                      audited_witness=witness.as_record(), replayed_hits=())


def test_startup_only_witness_supports_the_derived_goal_partition():
    from behave.sim.core.analysis import _per_goal
    from behave.sim.core.evidence import Closure, GoalEvidence, GoalStatus, Origin

    witness = AuditedWitness(**{**_witness(_domain()).as_dict(), 'initial_hits': [G.id]})
    retained = WitnessEvidence(WitnessOrigin.FEASIBILITY_QUERY, witness.as_record())
    closure = Closure({G.id: GoalEvidence(GoalStatus.REACHED, Origin.POOL, witness.inputs)},
                      {G.id: G}, 1, domain=witness.domain)
    evidence = _per_goal(closure, (retained,))
    assert evidence[G.id].as_dict() == GoalEvidenceRecord(
        ClassifiedGoalStatus.REACHED, retained.witness_ref).as_dict()


def test_old_compact_witness_reference_is_stable_in_conditional_evidence():
    witness = _witness(_domain(), hits=(G.id,)).as_record()
    witness['inputs'] = list(_witness(_domain(), hits=(G.id,)).inputs)
    witness['version'] = 'audited-witness/2'
    del witness['initial_hits']
    original = AuditedWitness.from_record(witness)
    retained = WitnessEvidence(WitnessOrigin.FEASIBILITY_QUERY, witness)
    query = QueryEvidence(0, (G.id,), SolverAnswer.SAT, decoded_request=[{'x': 0}],
                          audited_witness=witness, replayed_hits=(G.id,))
    assert retained.witness_ref == query.witness_ref == 'witness:' + original.fingerprint
    assert retained.witness == query.witness == witness
    evidence = _conditional(queries=(query,), goals={G.id: GoalEvidenceRecord(
        ClassifiedGoalStatus.REACHED, query.witness_ref)})
    assert evidence.goals[G.id]['status'] == 'reached'


def test_every_retained_witness_hit_must_be_classified_reached():
    domain = _domain()
    witness = _witness(domain, hits=(G.id, H.id))
    query = QueryEvidence(
        0, (G.id,), SolverAnswer.SAT,
        decoded_request=[{"x": 0}], audited_witness=witness.as_dict(),
        replayed_hits=(G.id, H.id))
    unresolved = QueryEvidence(
        1, (H.id,), SolverAnswer.UNKNOWN, reason="the singleton timed out")
    goals = {
        G.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.REACHED, query.witness_ref),
        H.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.UNRESOLVED, unresolved.query_id,
            detail="the singleton timed out"),
    }

    with pytest.raises(ValueError, match="classified reached"):
        _conditional(
            declared=(G, H), goals=goals, queries=(query, unresolved))


def test_a_witness_cannot_report_a_hit_outside_the_bound_catalogue():
    domain = _domain()
    witness = _witness(domain, hits=(G.id, "ghost"))
    query = QueryEvidence(
        0, (G.id,), SolverAnswer.SAT,
        decoded_request=[{"x": 0}], audited_witness=witness.as_dict(),
        replayed_hits=(G.id, "ghost"))

    with pytest.raises(ValueError, match="outside the catalogue"):
        _conditional(
            goals={G.id: GoalEvidenceRecord(
                ClassifiedGoalStatus.REACHED, query.witness_ref)},
            queries=(query,))


def test_a_feasibility_request_must_be_the_request_that_was_replayed():
    domain = _domain()
    witness = _witness(domain)
    with pytest.raises(ValueError, match="feasibility.*request|witness inputs"):
        FeasibilityEvidence(
            FeasibilitySource.EXACT_QUERY,
            witness=witness.as_dict(), decoded_request=[{"x": 1}], replayed=True)


def test_a_witness_from_another_domain_cannot_enter_the_evidence_ledger():
    other = TraceDomain(
        bound=1, contract={"x": [0, 1]}, frontend="test/1", policy="other",
        artifact="artifact", task_contract="contract")
    foreign = WitnessEvidence(
        WitnessOrigin.FEASIBILITY_QUERY, _witness(other).as_dict())

    with pytest.raises(ValueError, match="different trace domain"):
        _conditional(witnesses=(foreign,))


def test_a_sat_query_cannot_carry_an_audited_witness_from_another_domain():
    domain = _domain()
    other = TraceDomain(
        bound=1, contract={"x": [0, 1]}, frontend="test/1", policy="other",
        artifact="artifact", task_contract="contract")
    foreign = _witness(other, hits=(G.id,))
    query = QueryEvidence(
        0, (G.id,), SolverAnswer.SAT, decoded_request=[{"x": 0}],
        audited_witness=foreign.as_dict(), replayed_hits=(G.id,))
    feasibility = WitnessEvidence(
        WitnessOrigin.FEASIBILITY_QUERY, _witness(domain).as_dict())

    with pytest.raises(ValueError, match="query witness.*different trace domain"):
        _conditional(queries=(query,), witnesses=(feasibility,))


def test_concrete_manifest_occurrences_survive_the_evidence_handoff():
    class Contract:
        digest = "contract"

        @staticmethod
        def inputs():
            return ()

    class Realization:
        identity = "artifact"

        @staticmethod
        def as_dict():
            return {"identity": "artifact"}

    class Task:
        contract = Contract()
        realization = Realization()
        reference = "reference"

    occurrence = OccurrenceKey("behavior", ("process", 3, 0), "true")
    manifest = CatalogueManifest((G,), occurrences=((occurrence, G.id),))
    concrete = ConcreteEvaluation(Task(), catalogue=manifest)
    restored = CatalogueManifest.from_dict(
        concrete.as_dict()["catalogue_manifest"])
    query = QueryEvidence(0, (G.id,), SolverAnswer.UNSAT)
    evidence = _conditional(declared=restored, queries=(query,))

    handed_off = CatalogueManifest.from_dict(
        evidence.as_dict()["catalogue_manifest"])
    assert handed_off == manifest
    assert handed_off.occurrences == ((occurrence, G.id),)


def test_a_stopped_analysis_retains_the_same_occurrence_manifest():
    domain = _domain()
    occurrence = OccurrenceKey("behavior", ("process", 3, 0), "true")
    manifest = CatalogueManifest((G,), occurrences=((occurrence, G.id),))
    stopped = AnalysisStopEvidence(
        AnalysisStopKind.REFUSED,
        issue=IssueEvidence("test-stop", "the test stopped"),
        binding=_binding(domain, manifest),
        domain_manifest=DomainManifest(domain, manifest))

    handed_off = CatalogueManifest.from_dict(
        stopped.as_dict()["catalogue_manifest"])
    assert handed_off == manifest
    assert stopped.as_dict()["catalogue_digest"] == manifest.digest()

    other = CatalogueManifest((H,))
    with pytest.raises(ValueError, match="different domain manifest"):
        AnalysisStopEvidence(
            AnalysisStopKind.REFUSED,
            issue=IssueEvidence("test-stop", "the test stopped"),
            binding=_binding(domain, other),
            domain_manifest=DomainManifest(domain, manifest))


def test_analysis_outcomes_round_trip_through_the_strict_worker_image():
    query = QueryEvidence(0, (G.id,), SolverAnswer.UNSAT)
    classified = AnalysisOutcome(
        Verdict.CLASSIFIED,
        evidence=_conditional(queries=(query,)), concrete="a" * 64)
    issue = IssueEvidence("test-stop", "the test stopped")
    stopped = AnalysisOutcome(
        Verdict.REFUSED, why=issue.why,
        evidence=AnalysisStopEvidence(
            AnalysisStopKind.REFUSED, issue=issue),
        concrete="b" * 64)

    for outcome in (classified, stopped):
        image = outcome.as_dict()
        assert AnalysisOutcome.from_dict(image).as_dict() == image


def test_analysis_outcome_decoder_rejects_forged_derived_ids_and_fields():
    query = QueryEvidence(0, (G.id,), SolverAnswer.UNSAT)
    outcome = AnalysisOutcome(
        Verdict.CLASSIFIED,
        evidence=_conditional(queries=(query,)), concrete="a" * 64)

    forged = copy.deepcopy(outcome.as_dict())
    forged["evidence"]["queries"][0]["query_id"] = "query:" + "0" * 64
    with pytest.raises(ValueError, match="canonical|query-evidence"):
        AnalysisOutcome.from_dict(forged)

    extended = copy.deepcopy(outcome.as_dict())
    extended["unexpected"] = True
    with pytest.raises(ValueError, match="unknown or missing fields"):
        AnalysisOutcome.from_dict(extended)


def test_a_stopped_analysis_cannot_misbind_the_task_or_provider_identity():
    domain = _domain()
    manifest = CatalogueManifest((G,))
    original = _binding(domain, manifest)
    values = original.as_dict()
    values["provider"] = values.pop("provider_identity")
    values["provider"] = "another-provider"
    forged = AnalysisBinding(**values)

    with pytest.raises(ValueError, match="provider|artifact"):
        AnalysisStopEvidence(
            AnalysisStopKind.REFUSED,
            issue=IssueEvidence("test-stop", "the test stopped"),
            binding=forged,
            domain_manifest=DomainManifest(domain, manifest))


def test_analysis_stop_kind_is_closed_and_not_a_truth_value():
    with pytest.raises(TypeError, match="exact AnalysisStopKind"):
        AnalysisStopEvidence(
            "refused", issue=IssueEvidence("test-stop", "the test stopped"))
    with pytest.raises(TypeError, match="not a truth value"):
        bool(AnalysisStopKind.REFUSED)


def test_evidence_images_do_not_execute_arbitrary_mapping_implementations():
    class HostileMapping(Mapping):
        def __getitem__(self, _key):
            raise AssertionError("candidate mapping code ran")

        def __iter__(self):
            raise AssertionError("candidate mapping code ran")

        def __len__(self):
            raise AssertionError("candidate mapping code ran")

    with pytest.raises(TypeError, match="unsupported HostileMapping"):
        QueryEvidence(
            0, (G.id,), SolverAnswer.SAT,
            decoded_request=HostileMapping())


def test_a_set_unknown_followed_by_singletons_keeps_every_actual_call(monkeypatch):
    """The partition must not erase the set query that caused singleton retries."""
    source = (
        "class M:\n"
        "    def reset(self):\n"
        "        self.s = 0\n"
        "    def process(self, a):\n"
        "        if a:\n"
        "            self.s = 1\n"
        "        if self.s:\n"
        "            self.s = 0\n"
        "        return {'y': self.s}\n"
    )
    proc = lower_with_state_ranges(source)
    env = ranges.fixpoint(proc, {"a": ranges.Range(0, 1)})
    run = residual.unroll(proc, env, 1)
    goals = sorted(run.goals)[:2]
    assert len(goals) == 2

    # Script the logical occurrence-query boundary.  Its implementation may split one
    # request into a changing number of dependency-cone and validation phases; those
    # physical phases are not extra QueryCalls in the published transcript.
    answers = iter((
        SolverAnswer.UNKNOWN,
        SolverAnswer.SAT,
        SolverAnswer.UNSAT,
    ))

    def scripted_solve(formula, targets, _timeout_ms, **_kwargs):
        from types import SimpleNamespace

        answer = next(answers)
        if answer is SolverAnswer.SAT:
            solver = z3.Solver()
            solver.add(*formula.full_constraints, z3.Or(tuple(targets)))
            assert solver.check() == z3.sat
            return SimpleNamespace(
                answer=answer, model=solver.model(), reason="",
                sat_components=tuple(targets))
        if answer is SolverAnswer.UNKNOWN:
            return SimpleNamespace(
                answer=answer, model=None, reason="set query stalled",
                sat_components=())
        return SimpleNamespace(
            answer=answer, model=None, reason="", sat_components=())

    # This test owns the UNKNOWN-refinement transcript.  Structural partitioning is
    # exercised separately and may legitimately expose singleton cones before a set
    # query exists.
    monkeypatch.setattr(residual, "_dependency_partitions", lambda *_args: None)
    monkeypatch.setattr(residual, "solve_exact_components", scripted_solve)
    reached, unreachable, unresolved, calls = residual.decide_open(
        run, proc, goals, timeout_ms=1_000)

    assert not unresolved
    assert set(reached) == {goals[0]}
    assert unreachable == {goals[1]}
    assert [(call.goals, call.answer.value) for call in calls] == [
        (tuple(goals), "unknown"),
        ((goals[0],), "sat"),
        ((goals[1],), "unsat"),
    ]


def test_a_multi_goal_unknown_does_not_support_per_goal_unresolved():
    unknown = QueryEvidence(
        0, (G.id, H.id), SolverAnswer.UNKNOWN,
        reason="the set query timed out")
    goals = {
        G.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.UNRESOLVED, unknown.query_id, detail="timeout"),
        H.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.UNRESOLVED, unknown.query_id, detail="timeout"),
    }

    with pytest.raises(ValueError, match="singleton"):
        _conditional(declared=(G, H), goals=goals, queries=(unknown,))


def test_the_conditional_record_retains_set_and_retry_calls_in_order():
    domain = _domain()
    witness = _witness(domain, hits=(G.id,))
    calls = (
        QueryEvidence(
            0, (G.id, H.id), SolverAnswer.UNKNOWN,
            reason="the set query timed out"),
        QueryEvidence(
            1, (G.id,), SolverAnswer.SAT,
            decoded_request=[{"x": 0}],
            audited_witness=witness.as_dict(), replayed_hits=(G.id,)),
        QueryEvidence(2, (H.id,), SolverAnswer.UNSAT),
    )
    goals = {
        G.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.REACHED, calls[1].witness_ref),
        H.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.BOUNDED_UNREACHABLE, calls[2].query_id),
    }

    evidence = _conditional(declared=(G, H), goals=goals, queries=calls)

    assert [(row["order"], row["goals"], row["answer"])
            for row in evidence.as_dict()["queries"]] == [
        (0, [G.id, H.id], "unknown"),
        (1, [G.id], "sat"),
        (2, [H.id], "unsat"),
    ]


def test_typed_evidence_cannot_hide_unsat_behind_a_sat_witness():
    domain = _domain()
    witness = _witness(domain, hits=(G.id,))
    unsat = QueryEvidence(0, (G.id,), SolverAnswer.UNSAT)
    sat = QueryEvidence(
        1, (G.id,), SolverAnswer.SAT, decoded_request=[{"x": 0}],
        audited_witness=witness.as_dict(), replayed_hits=(G.id,))
    goals = {G.id: GoalEvidenceRecord(
        ClassifiedGoalStatus.REACHED, sat.witness_ref)}

    with pytest.raises(ValueError, match="actual UNSAT query"):
        _conditional(goals=goals, queries=(unsat, sat))


def test_typed_evidence_cannot_hide_unsat_behind_unknown():
    unsat = QueryEvidence(0, (G.id,), SolverAnswer.UNSAT)
    unknown = QueryEvidence(
        1, (G.id,), SolverAnswer.UNKNOWN,
        reason="a later query timed out")
    goals = {
        G.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.UNRESOLVED,
            unknown.query_id, detail="a later query timed out")}

    with pytest.raises(ValueError, match="actual UNSAT query"):
        _conditional(goals=goals, queries=(unsat, unknown))


def test_a_ghost_goal_cannot_enter_a_classified_partition():
    query = QueryEvidence(0, (G.id,), SolverAnswer.UNSAT)
    goals = {
        G.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.BOUNDED_UNREACHABLE, query.query_id),
        "ghost": GoalEvidenceRecord(
            ClassifiedGoalStatus.BOUNDED_UNREACHABLE, query.query_id),
    }

    with pytest.raises(ValueError, match="exactly the catalogue"):
        _conditional(goals=goals, queries=(query,))


def test_bounded_unreachable_requires_an_actual_unsat_call():
    goals = {
        G.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.BOUNDED_UNREACHABLE, "query:forged")}

    with pytest.raises(ValueError, match="UNSAT"):
        _conditional(goals=goals, queries=())


def test_reached_requires_the_exact_audited_witness_record():
    goals = {G.id: GoalEvidenceRecord(
        ClassifiedGoalStatus.REACHED, "witness:forged")}

    with pytest.raises(ValueError, match="witness ledger"):
        _conditional(goals=goals)


def test_a_sat_query_cannot_claim_a_required_audit_that_did_not_run():
    domain = _domain()
    witness = _witness(domain, hits=(G.id,))
    query = QueryEvidence(
        0, (G.id,), SolverAnswer.SAT, decoded_request=[{"x": 0}],
        audited_witness=witness.as_dict(),
        replayed_hits=(G.id,), passed_audits=())
    goals = {
        G.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.REACHED, query.witness_ref)
    }

    with pytest.raises(ValueError, match="required per-trace audits"):
        _conditional(
            goals=goals, queries=(query,),
            required=("step_audit", "source_audit"))


def test_required_audit_check_must_name_the_witness_it_checked():
    domain = _domain()
    witness = _witness(domain, hits=(G.id,))
    query = QueryEvidence(
        0, (G.id,), SolverAnswer.SAT, decoded_request=[{"x": 0}],
        audited_witness=witness.as_dict(), replayed_hits=(G.id,),
        passed_audits=("step_audit",))
    ledger = [
        WitnessEvidence(
            WitnessOrigin.FEASIBILITY_QUERY, _witness(domain).as_dict(),
            passed_audits=("step_audit",)),
        WitnessEvidence(
            WitnessOrigin.BOUNDED_QUERY,
            witness.as_dict(), query_ref=query.query_id,
            passed_audits=("step_audit",)),
    ]
    binding = _binding(
        domain, catalogue_image((G,)), AuditPlan((AuditKind.STEP,)))
    unrelated = "witness:" + "0" * 64
    checks = [
        PassedCheck(
            "domain_manifest_binding",
            ("domain-manifest:" + binding.domain_manifest,
             "provider:" + binding.provider)),
        PassedCheck("task_binding", (
            "domain:" + binding.domain,
            "task-contract:" + binding.task_contract,
            "artifact:" + binding.artifact,
            "provider:" + binding.provider)),
        PassedCheck("catalogue_binding", (
            "catalogue:" + binding.catalogue,
            "provider:" + binding.provider)),
        PassedCheck(
            "audit_plan_binding", (
                "audit-plan:" + binding.audit_plan,
                "provider:" + binding.provider)),
        PassedCheck(
            "representation_well_formed",
            ("representation:" + binding.representation,
             "encoding:" + binding.encoding)),
        PassedCheck("step_audit", (unrelated,)),
    ]

    with pytest.raises(ValueError, match="no passed|required-audit"):
        _conditional(
            goals={G.id: GoalEvidenceRecord(
                ClassifiedGoalStatus.REACHED, query.witness_ref)},
            queries=(query,), required=("step_audit",), witnesses=ledger,
            checks=checks)


def test_trusted_assumptions_are_closed_records_not_passed_checks():
    query = QueryEvidence(0, (G.id,), SolverAnswer.UNSAT)
    goals = {G.id: GoalEvidenceRecord(
        ClassifiedGoalStatus.BOUNDED_UNREACHABLE, query.query_id)}

    with pytest.raises(TypeError, match="TrustedAssumption"):
        _conditional(
            goals=goals, queries=(query,),
            trusted=({"code": "looks-typed", "statement": "but is not"},))


def test_passed_checks_and_trusted_assumptions_serialize_separately():
    query = QueryEvidence(0, (G.id,), SolverAnswer.UNSAT)
    evidence = _conditional(
        goals={G.id: GoalEvidenceRecord(
            ClassifiedGoalStatus.BOUNDED_UNREACHABLE, query.query_id)},
        queries=(query,)).as_dict()

    assert all(row["result"] == "agreed" for row in evidence["passed_checks"])
    assert evidence["trusted_assumptions"] == [{
        "code": "test-assumption",
        "scope": "test",
        "statement": "the test provider is faithful",
    }]


def test_passed_check_subjects_are_not_string_coerced():
    class LooksLikeAReference:
        def __str__(self):
            return "witness:" + "0" * 64

    with pytest.raises(TypeError, match="exact strings"):
        PassedCheck("step_audit", (LooksLikeAReference(),))


def test_a_stopped_run_keeps_the_sat_request_that_failed_witness_audit():
    raw = QueryEvidence(
        0, (G.id,), SolverAnswer.SAT, decoded_request=[{"x": 1}])
    stopped = AnalysisStopEvidence(
        AnalysisStopKind.REFUSED,
        issue=IssueEvidence(
            "witness-audit-failed", "the replay worker did not complete"),
        queries=(raw,))

    assert stopped.as_dict()["queries"] == [{
        "query_id": raw.query_id,
        "order": 0,
        "goals": [G.id],
        "answer": "sat",
        "solver_calls": 1,
        "passed_audits": [],
        "decoded_run_request": [{"x": 1}],
    }]
    with pytest.raises(ValueError, match="complete audited witness"):
        _conditional(
            goals={G.id: GoalEvidenceRecord(
                ClassifiedGoalStatus.REACHED, "witness:missing")},
            queries=(raw,))


class _PretendsToBeJSON:
    def __repr__(self):
        return "{'x': 0}"


@pytest.mark.parametrize("constructor", [
    lambda value: QueryEvidence(
        0, (G.id,), SolverAnswer.SAT, decoded_request=[{"x": 0}],
        audited_witness=value, replayed_hits=(G.id,)),
    lambda value: FeasibilityEvidence(
        FeasibilitySource.AUDITED_POOL,
        witness=value,
    ),
    lambda value: WitnessEvidence(WitnessOrigin.FEASIBILITY_QUERY, value),
])
def test_repr_is_never_an_evidence_serialization(constructor):
    with pytest.raises(TypeError, match="finite|JSON|AuditedWitness"):
        constructor(_PretendsToBeJSON())


def test_feasibility_evidence_uses_the_closed_result_algebra():
    witness = _witness(_domain()).as_dict()
    with pytest.raises(TypeError, match="FeasibilitySource"):
        FeasibilityEvidence("audited-pool", witness=witness)
    with pytest.raises(TypeError, match="FeasibilityAnswer"):
        FeasibilityEvidence(
            FeasibilitySource.EXACT_QUERY,
            answer="infeasible",
            detail="no trace",
        )
    with pytest.raises(TypeError, match="exact Boolean"):
        FeasibilityEvidence(
            FeasibilitySource.EXACT_QUERY,
            answer=FeasibilityAnswer.FEASIBLE,
            decoded_request=[],
            replayed=0,
        )
    with pytest.raises(TypeError, match="not a truth value"):
        bool(FeasibilityAnswer.FEASIBLE)


def test_witness_origin_is_closed_and_not_truthy():
    with pytest.raises(TypeError, match="WitnessOrigin"):
        WitnessEvidence("feasibility-query", _witness(_domain()).as_dict())
    with pytest.raises(TypeError, match="not a truth value"):
        bool(WitnessOrigin.FEASIBILITY_QUERY)


def test_classified_goal_status_is_closed_and_not_truthy():
    with pytest.raises(TypeError, match="ClassifiedGoalStatus"):
        GoalEvidenceRecord("reached", "witness:forged")
    with pytest.raises(TypeError, match="not a truth value"):
        bool(ClassifiedGoalStatus.REACHED)


class _Task:
    class Contract:
        digest = "contract"

        @staticmethod
        def inputs():
            return ()

    class Realization:
        identity = "artifact"

        @staticmethod
        def as_dict():
            return {"kind": "test", "identity": "artifact", "ports": [],
                    "channel_ports": {}, "source": ""}

    contract = Contract()
    realization = Realization()
    reference = "reference"

    @staticmethod
    def binds(_artifact):
        return True


class _NoSingletonProvider:
    def __init__(self, reason):
        self.reason = reason

    def analysis_identity(self):
        return "artifact"

    def evidence_identity(self, _steps):
        return {"representation": "test-representation/1",
                "encoding": "test-encoding/1"}

    def task_audit(self, _task):
        return AuditCheck.agreed()

    def goals(self):
        return {G.id: G}

    def catalogue_manifest(self):
        return CatalogueManifest((G,))

    def well_formed(self):
        return []

    def audit_plan(self):
        return AuditPlan((AuditKind.GOAL_SET,))

    def unaccounted_goals(self):
        return []

    def feasible(self, _steps, timeout_ms=None):
        return Feasible.YES, ({"x": 0},)

    def replay(self, _trace):
        return Replay((), outputs=[StepOutput()], state={"s": 0}, steps=1)

    def audit_request(self, trace, domain):
        return AuditedWitness(
            domain=domain, states=[{"s": 0}, {"s": 0}], inputs=trace,
            outputs=[[]], hit_sets=[()])

    def query(self, goals, steps, timeout_ms=None):
        assert goals == [G.id] and steps == 1
        return QueryVerdict({}, set(), {G.id: self.reason}, queries=())

    def ruled_out(self):
        return {}

    def trusted_assumptions(self):
        return (TrustedAssumption(
            "test-assumption", "the test provider is faithful", scope="test"),)


@pytest.mark.parametrize("reason", [
    "the query budget is exhausted",
    "the goal has no supported exact hit term",
])
def test_no_singleton_query_is_a_public_refusal_not_unresolved(reason):
    evaluation = ConcreteEvaluation(_Task(), catalogue=[G])
    domain = _domain()
    exact = BoundedInstance(_NoSingletonProvider(reason), domain)

    got = api.analyse(evaluation, exact,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.REFUSED
    assert got.per_goal == {}
    assert got.evidence["refusal"]["kind"] == \
        "closure-query-unresolved-without-singleton"


def test_budget_exhaustion_retains_an_honest_unlaunched_singleton_query():
    class BudgetExhausted(_NoSingletonProvider):
        def query(self, goals, steps, timeout_ms=None):
            assert goals == [G.id] and steps == 1
            call = QueryCall(
                (G.id,), SolverAnswer.UNKNOWN, reason=self.reason,
                solver_calls=0)
            return QueryVerdict(
                {}, set(), {G.id: self.reason}, queries=(call,))

    reason = "the artifact budget was already exhausted"
    evaluation = ConcreteEvaluation(_Task(), catalogue=[G])
    exact = BoundedInstance(BudgetExhausted(reason), _domain())

    got = api.analyse(evaluation, exact,
        wall_timeout_seconds=60)

    assert got.verdict is Verdict.CLASSIFIED
    assert got.per_goal[G.id] == {
        "status": "unresolved",
        "support_ref": got.evidence["queries"][0]["query_id"],
        "detail": reason,
    }
    assert got.evidence["queries"][0]["solver_calls"] == 0
