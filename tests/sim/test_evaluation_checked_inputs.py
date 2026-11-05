"""Accepted traffic is not necessarily completely checked reference traffic."""

import pytest

from behave.sim import api
from tests.sim.test_api_path import Doubling, _evaluate, _ref, contract, realization, schedule
from tests.sim.test_transaction_completion import observed_prefix


@pytest.mark.parametrize('gain', [2, 3])
def test_complete_comparisons_count_work_even_when_the_values_mismatch(gain):
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    result = _evaluate(task, (schedule([3]), schedule([4, 5])), Doubling(gain), reference)
    before = result.as_evidence_dict()
    assert result.accepted == result.checked_inputs == 3
    assert result.passed is (gain == 2)
    assert result.as_evidence_dict() == before  # A derived count changes no evidence.

    missing = api.RunFailure.infrastructure('reference-cleanup', api.Refusal('cleanup-lost', 'test'))
    first, second = result.results
    unchecked = api.RunResult.not_compared(second.stimulus, second.trace, missing)
    incomplete = api.ConcreteEvaluation(task, (first, unchecked), catalogue=result.catalogue)
    assert incomplete.accepted == 3 and incomplete.checked_inputs == 1
    assert incomplete.no_result_failures and not incomplete.passed


@pytest.mark.parametrize('completion', [api.Completion.INCOMPLETE, api.Completion.CHECKPOINT])
def test_only_explicit_retained_boundary_counts_as_checked_work(completion):
    from behave.sim.core.monitor import Monitor
    from behave.sim.run_assessment import assess_run

    task, reference, request, _, _, raw = observed_prefix()
    trace = api.AuditedTrace(raw.stimulus, records=raw.records, completion=completion,
                            request=request, artifact=raw.artifact, domain=raw.domain)
    assessment = assess_run(task, request, trace, reference, monitor=Monitor(task.contract))
    assert assessment.result.was_compared and assessment.result.comparison.complete
    result = api.ConcreteEvaluation(task, (assessment.result,),
        catalogue=api.evaluation_manifest(task, api.CatalogueManifest(())),
        requested_stimuli=(request,), budget_exhausted=True)
    assert result.accepted == 1
    assert result.checked_inputs == int(completion is api.Completion.CHECKPOINT)


@pytest.mark.parametrize('padded', [False, True])
def test_missing_response_or_analysis_padding_never_counts_as_checked_work(padded):
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    full = _evaluate(task, (schedule([3]),), Doubling(), reference)
    original = full.results[0].trace
    records = original.records if padded else tuple(api.SlotRecord(
        accepted={name: value for name, value in row.accepted if name != 'out'},
        state=row.state, outputs=row.outputs, environment=row.environment,
        monitor=row.monitor, hits=row.hits) for row in original.records)
    trace = api.AuditedTrace(original.stimulus, records=records,
        completion=api.Completion.PADDED if padded else api.Completion.NORMAL,
        initial_state=original.initial_state, artifact=original.artifact, domain=original.domain)
    comparison = api.compare_run(task, reference, trace)
    assert comparison.complete is padded
    result = api.ConcreteEvaluation(task,
        (api.RunResult.compared(trace.stimulus, trace, comparison),), catalogue=full.catalogue)
    assert result.accepted == 1 and result.checked_inputs == 0
