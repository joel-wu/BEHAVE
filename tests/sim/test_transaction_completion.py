"""A response boundary is necessary for a checkpoint, never sufficient to pass."""

import pytest

from behave.sim import api
from behave.sim.core.transaction_completion import TransactionCompletion
from behave.sim.runtime_execution import completion_plan, completion_targets
from tests.support.reference import load

from .test_api_path import contract, realization


def planned(source, values):
    reference = load({"behavior.py": source.encode()}, config={"class": "M"})
    task = api.admit(contract(), realization(), reference=reference)
    request = api.Stimulus(queues={"in": [(value,) for value in values]},
                           replay_policy=api.ReplayPolicy(2, 4, 3))
    return completion_plan(task, request, reference), task, request, reference


def test_one_output_is_not_one_complete_transaction():
    plan = TransactionCompletion("request", (("in", 2),), (("out", 2048),),
                                 ((0,), (1024,), (2048,)))
    assert not plan.at_response_boundary({"in": 0}, {"out": 0}, memory_quiescent=True)
    assert not plan.at_response_boundary({"in": 1}, {"out": 1}, memory_quiescent=True)
    assert plan.at_response_boundary({"in": 1}, {"out": 1024}, memory_quiescent=True)
    assert not plan.at_response_boundary({"in": 2}, {"out": 1024}, memory_quiescent=True)
    assert not plan.at_response_boundary({"in": 1}, {"out": 1025}, memory_quiescent=True)
    assert not plan.at_response_boundary({"in": 1}, {"out": 1024}, memory_quiescent=False)


def test_variable_counts_are_cumulative_and_stateful_not_a_fixed_ratio():
    source = """class M:
    def reset(self): self.n = 0
    def process(self, in_a):
        self.n += 1
        return [{'out_y': in_a} for _ in range(self.n * in_a)]
"""
    plan, task, request, reference = planned(source, [3, 0, 2])
    assert plan.request == request.fingerprint
    assert plan.prefixes == ((0,), (3,), (3,), (9,))
    assert plan.targets_after({"in": 2}) == {"out": 3}
    assert plan.totals == completion_targets(task, request, reference) == {"out": 9}


def test_reference_count_pass_is_not_repeated(monkeypatch):
    from behave.sim.core.reference import LoadedReference

    instantiate = LoadedReference.instantiate
    calls = []
    def counted(self, contract):
        calls.append(contract)
        return instantiate(self, contract)
    monkeypatch.setattr(LoadedReference, "instantiate", counted)
    source = """class M:
    def reset(self): pass
    def process(self, in_a): return {'out_y': in_a}
"""
    plan, *_ = planned(source, [2, 5, 7])
    assert len(calls) == 1
    assert plan.prefixes == ((0,), (1,), (2,), (3,))
    assert plan.targets_after({"in": 1}) == {"out": 1}
    assert plan.targets_after({"in": 2}) == {"out": 2}
    assert len(calls) == 1


def test_zero_response_transaction_is_not_confused_with_empty_execution():
    source = """class M:
    def reset(self): pass
    def process(self, in_a): return []
"""
    plan, *_ = planned(source, [2, 5])
    assert not plan.at_response_boundary({"in": 0}, {"out": 0}, memory_quiescent=True)
    assert plan.at_response_boundary({"in": 1}, {"out": 0}, memory_quiescent=True)
    # This is only a possible boundary. State/memory effects must still be compared.


def test_unknown_product_boundary_does_not_invent_an_input_pairing():
    plan = TransactionCompletion("product", (("a", 2), ("b", 3)), (("x", 5), ("y", 7)))
    assert plan.targets_after({"a": 1, "b": 1}) is None
    assert not plan.at_response_boundary({"a": 1, "b": 1}, {"x": 1, "y": 1},
                                         memory_quiescent=True)
    assert plan.targets_after({"a": 2, "b": 3}) == {"x": 5, "y": 7}
    assert not plan.at_response_boundary({"a": 2, "b": 3}, {"x": 5, "y": 6},
                                         memory_quiescent=True)


@pytest.mark.parametrize("accepted", [{"other": 1}, {"in": True}, {"in": -1}, {"in": 3}])
def test_progress_is_checked_against_the_request(accepted):
    plan = TransactionCompletion("request", (("in", 2),), (("out", 2),), ((0,), (1,), (2,)))
    with pytest.raises(ValueError):
        plan.targets_after(accepted)


def test_zero_output_tail_does_not_finish_because_an_earlier_input_responded():
    plan = TransactionCompletion("request", (("in", 3),), (("out", 2),),
                                 ((0,), (1,), (1,), (2,)))
    assert plan.at_checkpoint_boundary({"in": 1}, {"out": 1}, memory_quiescent=True)
    assert plan.at_response_boundary({"in": 2}, {"out": 1}, memory_quiescent=True)
    assert not plan.at_checkpoint_boundary({"in": 2}, {"out": 1}, memory_quiescent=True)
    assert plan.at_checkpoint_boundary({"in": 3}, {"out": 2}, memory_quiescent=True)


def test_unknown_intermediate_product_boundary_is_not_a_checkpoint():
    plan = TransactionCompletion("request", (("a", 1), ("b", 1)), (("out", 2),))
    assert plan.at_response_boundary({"a": 1, "b": 1}, {"out": 2}, memory_quiescent=True)
    assert not plan.at_checkpoint_boundary({"a": 1, "b": 1}, {"out": 2}, memory_quiescent=True)


@pytest.mark.parametrize("prefixes", [((0,), (1,)), ((1,), (1,), (2,)),
                                     ((0,), (3,), (2,)), ((0,), (True,), (2,))])
def test_prefixes_must_describe_the_complete_count_pass(prefixes):
    with pytest.raises(ValueError):
        TransactionCompletion("request", (("in", 2),), (("out", 2),), prefixes)


def observed_prefix(*, wrong_value=False):
    from behave.sim.core.driver import Driver
    from behave.sim.core.stimulus import trace_domain

    from .test_api_path import FirstOnlyDoubling, _ref

    reference = _ref()
    task = api.admit(contract(answers=1), realization(), reference=reference)
    request = api.Stimulus(queues={"in": [(3,), (5,), (7,)]},
                           replay_policy=api.ReplayPolicy(2, 4, 0))
    plan = completion_plan(task, request, reference)
    driver = Driver(task.contract, request, artifact_identity=task.realization.identity,
                    expected_outputs=plan.totals)
    device = FirstOnlyDoubling()
    for index, _slot in driver.iter_slots():
        presentations = driver.presentations(index)
        outputs = device.settle(presentations["in"], driver.controls(index))
        if wrong_value and outputs["out_valid"]:
            outputs["out_y"] += 1
        driver.observe(index, presentations, outputs)
        device.edge()
        counts = driver.progress_counts()
        if plan.at_response_boundary(counts["accepted"], counts["emitted"],
                                     memory_quiescent=True):
            break
    resolved = driver._runtime_schedule.observed_stimulus()
    trace = api.AuditedTrace(resolved, records=driver.records,
        completion=api.Completion.INCOMPLETE, request=request,
        artifact=task.realization.identity,
        domain=trace_domain(task.contract.digest, horizon=len(resolved.slots)))
    return task, reference, request, plan, driver, trace


def test_observed_schedule_keeps_pending_inputs_and_does_not_finish_the_driver():
    from behave.sim.core.runtime_schedule import validate_resolution

    task, _, request, plan, driver, trace = observed_prefix()
    assert trace.stimulus.queues == request.queues
    assert trace.accepted_count(["in"]) == trace.accepted_count(["out"]) == 1
    assert not driver._runtime_schedule.complete
    with pytest.raises(api.Refused, match="driver-incomplete"):
        driver.completed_stimulus()
    with pytest.raises(api.Refused, match="runtime-schedule-incomplete"):
        validate_resolution(task.contract, request, trace.stimulus, trace.records, plan.totals)
    ledgers = validate_resolution(task.contract, request, trace.stimulus, trace.records,
                                  plan.totals, observed_prefix=True, collect_protocol=True)
    assert ledgers and all(ledger.truncated for ledger in ledgers.values())
    assert api.AuditedTrace.from_dict(trace.as_dict()).fingerprint == trace.fingerprint


@pytest.mark.parametrize("wrong_value", (False, True))
def test_prefix_uses_the_ordinary_reference_comparison_not_response_counts(wrong_value):
    from behave.sim.core.monitor import Monitor
    from behave.sim.run_assessment import assess_run

    task, reference, request, _, _, trace = observed_prefix(wrong_value=wrong_value)
    assessment = assess_run(task, request, trace, reference, monitor=Monitor(task.contract))
    assert not assessment.report.assumption_violations
    assert not assessment.report.violations
    assert assessment.result.was_compared
    assert assessment.result.comparison.complete
    assert assessment.result.comparison.ok is not wrong_value
    # A checked boundary is not yet a public budget-stop receipt. Arbitrary
    # incomplete traces must not gain pass authority via this foundation.
    result = api.ConcreteEvaluation(task, [assessment.result],
        catalogue=api.evaluation_manifest(task, api.CatalogueManifest(())),
        requested_stimuli=(request,), budget_exhausted=True)
    assert not result.passed and not result.complete and not result.required_complete


def test_prefix_validation_does_not_allow_changing_the_original_queue():
    from behave.sim.core.runtime_schedule import validate_resolution

    task, _, request, plan, _, trace = observed_prefix()
    shortened = api.Stimulus(queues={"in": [(3,)]}, slots=trace.stimulus.slots)
    with pytest.raises((ValueError, api.Refused)):
        validate_resolution(task.contract, request, shortened, trace.records, plan.totals,
                             observed_prefix=True)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("wrong_value", [False, True])
def test_checked_transaction_window_is_explicit_and_retains_mismatches(enabled, wrong_value):
    from behave.sim.core.monitor import Monitor
    from behave.sim.run_assessment import assess_run

    task, reference, request, _, _, prior = observed_prefix(wrong_value=wrong_value)
    trace = api.AuditedTrace(prior.stimulus, records=prior.records,
        completion=api.Completion.CHECKPOINT, request=request,
        artifact=prior.artifact, domain=prior.domain)
    assessed = assess_run(task, request, trace, reference, monitor=Monitor(task.contract))
    assert assessed.result.was_compared
    result = api.ConcreteEvaluation(task, [assessed.result],
        catalogue=api.evaluation_manifest(task, api.CatalogueManifest(())),
        requested_stimuli=(request,), budget_exhausted=True,
        telemetry={"scoring_budget": {"policy": api.ScoringPolicy(
            transaction_checkpoints=enabled).as_dict()}})
    assert not result.complete
    from behave.sim.evaluation_policy import verdict

    assert result.score_scope == "incomplete-baseline"
    assert verdict(result).as_summary_dict()["score_scope"] == result.score_scope
    assert not result.passed
    assert verdict(result).authoritative is wrong_value
    assert (result.failure_witness is not None) is wrong_value
    assert not trace.completion.scores  # Never eligible as a full analysis trace.


def test_decoder_requires_bound_completion_and_rejects_forged_boundary():
    from dataclasses import replace

    from .test_trace_postprocessing import packet

    task, _, request, completion, _, trace = observed_prefix()
    plan, _, document = packet(task.contract, trace.stimulus, trace)
    wire = document["traces"][0]
    wire.update(checkpoint=True, resolved_stimulus=trace.stimulus.as_dict())
    # Use the regular closed wire encoder, not a special permissive decoder.
    from behave.sim.core.record_value import canonical_json

    payload = canonical_json(document).encode()
    assert plan.decode(request, payload, {}).kind == "rtl-checkpoint-unrequested"
    plan = replace(plan, completion_targets={request.fingerprint: completion.totals},
                   checkpoint_plans={request.fingerprint: completion})
    got = plan.decode(request, payload, {})
    assert type(got) is api.AuditedTrace
    assert got.completion is api.Completion.CHECKPOINT
    wrong = replace(completion, outputs=(("out", 6),), prefixes=((0,), (2,), (4,), (6,)))
    plan = replace(plan, checkpoint_plans={request.fingerprint: wrong})
    assert plan.decode(request, payload, {}).kind == "rtl-checkpoint-in-flight"


def test_checkpoint_is_immutable_but_later_protocol_failure_remains_a_failure():
    from behave.sim.core.monitor import Monitor
    from behave.sim.run_assessment import assess_run

    task, reference, request, _, driver, checkpoint = observed_prefix()
    identity = checkpoint.fingerprint
    iterator = driver.iter_slots()
    index, _slot = next(iterator)
    # An unsolicited second response after the good first transaction. A saved
    # checkpoint must neither change nor replace this later negative evidence.
    driver.observe(index, driver.presentations(index),
                   {"in_ready": 0, "out_valid": 1, "out_y": 6})
    assert driver.protocol_stop is not None
    assert checkpoint.fingerprint == identity
    assert checkpoint.accepted_count(["out"]) == 1
    failed = assess_run(task, request, driver.trace(), reference, monitor=Monitor(task.contract))
    assert failed.report.violations
    assert failed.result.determines_score
    assert not failed.result.ok


def test_reused_decode_worker_gets_each_trusted_completion_binding():
    from dataclasses import replace

    from behave.sim.core.record_value import canonical_json
    from behave.sim.rtl.trace_postprocessing import MonitorPlan, TracePostprocessor
    from behave.sim.run_assessment import CheckedRun, RunAssessment

    from .test_trace_postprocessing import packet

    task, _, request, completion, _, trace = observed_prefix()
    plan, _, document = packet(task.contract, trace.stimulus, trace)
    document['traces'][0].update(checkpoint=True, resolved_stimulus=trace.stimulus.as_dict())
    payload = canonical_json(document).encode()
    plan = replace(plan, completion_targets={}, checkpoint_plans={})
    with TracePostprocessor(plan, MonitorPlan(), 2) as checker:
        refused = checker.decode(request, payload, {}, 0)
        assert type(refused) is RunAssessment
        assert refused.result.run_failure.kind == 'rtl-checkpoint-unrequested'
        plan.completion_targets[request.fingerprint] = completion.totals
        plan.checkpoint_plans[request.fingerprint] = completion
        checked = checker.decode(request, payload, {}, 0)
        assert type(checked) is CheckedRun
        assert checked.trace.completion is api.Completion.CHECKPOINT
        plan.checkpoint_plans[request.fingerprint] = replace(completion,
            outputs=(("out", 6),), prefixes=((0,), (2,), (4,), (6,)))
        refused = checker.decode(request, payload, {}, 0)
        assert type(refused) is RunAssessment
        assert refused.result.run_failure.kind == 'rtl-checkpoint-in-flight'
