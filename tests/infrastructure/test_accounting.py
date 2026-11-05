"""Sealed accounting is shared by eval/train, never a second invocation."""

from dataclasses import replace

import pytest
from test_judge import ScriptedJudge
from test_task_workflow import STOP, setup

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.trajectory import trajectory
from behave.core.records import RecordError, canonical_json, parse_json
from behave.task_execution.accounting import encode_accounting, load_accounting
from behave.task_execution.run_store import RunStore
from behave.task_execution.workflow import execute_task
from behave.train.training_data import load_candidate

PAID_STOP = STOP.replace("ModelReply('done')", "ModelReply('done', charge=Charge('0.03','USD','fixture'))")


@pytest.mark.isolation
def test_accounting_validates_source_identity_and_exact_summary_before_handoff(tmp_path, publication, monkeypatch):
    job, run = setup(tmp_path, publication, PAID_STOP)
    pool = ResourcePool(AdmissionPolicy("models", 1))
    report = execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge(), model_pool=pool)
    result = report.result
    accounting = load_accounting(run.objects, result)
    assert accounting.billing_source == "provider"
    assert accounting.billing.cost_by_currency() == {"USD": "0.03"}
    assert accounting.billing == load_candidate(run.objects, report.content).billing

    raw = run.objects.read(result.accounting)
    row = parse_json(raw)
    view = trajectory(run.journal("solver"))
    assert raw == encode_accounting(attempt_id=result.attempt_id, heads=row["heads"], trajectory=view,
                                    billing=accounting.billing, billing_source="provider")

    # Matching solver-received fees are not permission to switch authorities.
    forged = {**row, "billing_source": "solver", "heads": {**row["heads"], "provider": None}}
    for change, code in ((forged, "accounting-source-mismatch"),
                         ({**row, "attempt_id": "foreign"}, "accounting-identity-mismatch"),
                         ({**row, "cost_by_currency": {"USD": "0"}}, "accounting-summary-mismatch"),
                         ({**row, "unpriced_dispatches": False}, "accounting-summary-mismatch")):
        altered = replace(result, accounting=run.objects.put(canonical_json(change)))
        with pytest.raises(RecordError) as caught:
            load_candidate(run.objects, run.objects.put(altered.to_bytes()))
        assert caught.value.code == code

    # A real but foreign provider prefix cannot be spliced in, even with the
    # same command, prices and exact amount. Its attempt identity must agree.
    other_job = replace(job, attempt=replace(job.attempt, attempt_id="foreign"))
    other = RunStore(tmp_path / "other")
    second = execute_task(reader=publication.reader, job=other_job, run=other, judge=ScriptedJudge(), model_pool=pool)
    _, refs = other.seal("provider")
    for ref in refs:
        run.objects.put(other.objects.read(ref))
    foreign = parse_json(other.objects.read(second.result.accounting))["heads"]["provider"]
    altered = replace(result, accounting=run.objects.put(canonical_json({
        **row, "heads": {**row["heads"], "provider": foreign}})))
    with pytest.raises(RecordError) as caught:
        load_accounting(run.objects, altered)
    assert caught.value.code == "accounting-identity-mismatch"

    from behave.isolation import service
    monkeypatch.setattr(service, "run_service", lambda *a, **kw: pytest.fail("accounting must never execute"))
    assert load_accounting(run.objects, result) == accounting


@pytest.mark.isolation
def test_accounting_cancelled_before_dispatch_is_known_empty_not_unpriced(tmp_path, publication):
    job, run = setup(tmp_path, publication, PAID_STOP)
    result = execute_task(reader=publication.reader, job=job, run=run, judge=ScriptedJudge(),
                          model_pool=ResourcePool(AdmissionPolicy("models", 1)), cancelled=lambda: True).result
    accounting = load_accounting(run.objects, result)
    assert result.score is None and accounting.trajectory.turns == ()
    assert accounting.billing_source == "provider"
    assert accounting.billing.charges == ()
    assert accounting.billing.unpriced_dispatches == accounting.billing.unresolved_dispatches == 0
    row = parse_json(run.objects.read(result.accounting))
    altered = replace(result, accounting=run.objects.put(canonical_json({
        **row, "heads": {**row["heads"], "provider": None}})))
    with pytest.raises(RecordError) as caught:
        load_accounting(run.objects, altered)
    assert caught.value.code == "accounting-source-mismatch"
