"""Recovery reads identity evidence; only explicit audit reconstructs payloads."""

from dataclasses import replace

import pytest
from test_rejudge import local_rollout  # noqa: F401
from test_task_workflow import setup

from behave.agent.admission import AdmissionPolicy
from behave.agent.journal import Event, load_chain
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.task_execution.accounting import accounting_artifacts, load_accounting, load_accounting_identity
from behave.task_execution.workflow import execute_task


@pytest.fixture
def recorded(tmp_path, publication, request):
    request.getfixturevalue("local_rollout")
    job, run = setup(tmp_path, publication)
    result = execute_task(reader=publication.reader, job=job, run=run).result
    return job, run.objects, result


def test_identity_does_not_read_turn_payloads(recorded):
    job, objects, result = recorded
    audit = load_accounting(objects, result)
    payloads = {ref for turn in audit.trajectory.turns
                for ref in (turn.request, turn.response, turn.raw_response)}
    assert payloads

    class MetadataOnly:
        def read(self, ref):
            assert ref not in payloads, "conversation payload reread"
            return objects.read(ref)

    identity = load_accounting_identity(MetadataOnly(), result)
    assert identity.job == audit.job == BlobRef.of(job.to_bytes())
    assert identity.model_admission == audit.model_admission is None
    assert payloads.issubset(accounting_artifacts(MetadataOnly(), result))
    # The full audit still traverses payloads; this is not a weaker default.
    with pytest.raises(AssertionError, match="payload reread"):
        load_accounting(MetadataOnly(), result)


@pytest.mark.parametrize("change, code", [
    ({"attempt_id": "foreign"}, "accounting-identity-mismatch"),
    ({"billing_source": "provider"}, "accounting-source-mismatch"),
    ({"schema": "behave.attempt-accounting/999"}, "record-version-unsupported"),
])
def test_identity_rejects_wrong_record_binding(recorded, change, code):
    _, objects, result = recorded
    row = parse_json(objects.read(result.accounting))
    altered = replace(result, accounting=objects.put(canonical_json({**row, **change})))
    for read in (load_accounting_identity, accounting_artifacts, load_accounting):
        with pytest.raises(RecordError) as error:
            read(objects, altered)
        assert error.value.code == code


def test_identity_is_not_a_billing_audit(recorded):
    _, objects, result = recorded
    row = parse_json(objects.read(result.accounting))
    altered = replace(result, accounting=objects.put(canonical_json({**row, "cost_by_currency": {"USD": "999"}})))
    assert load_accounting_identity(objects, altered) == load_accounting_identity(objects, result)
    with pytest.raises(RecordError, match="summary differs"):
        load_accounting(objects, altered)


@pytest.mark.parametrize("field", ["task_id", "attempt_id", "release", "revision", "solver_head"])
def test_identity_rejects_a_foreign_result(recorded, field):
    _, objects, result = recorded
    foreign = "other" if field.endswith("_id") else BlobRef.of(b"other")
    altered = replace(result, **{field: foreign})
    for read in (load_accounting_identity, accounting_artifacts, load_accounting):
        with pytest.raises(RecordError) as error:
            read(objects, altered)
        assert error.value.code == "accounting-identity-mismatch"


@pytest.mark.parametrize("phase", ["supervision", "solver"])
def test_identity_authenticates_journal_bytes(recorded, phase):
    _, objects, result = recorded
    head = BlobRef.from_dict(parse_json(objects.read(result.accounting))["heads"][phase])

    class Corrupted:
        def read(self, ref):
            return b"corrupted" if ref == head else objects.read(ref)

    for read in (load_accounting_identity, accounting_artifacts):
        with pytest.raises(RecordError) as error:
            read(Corrupted(), result)
        assert error.value.code == "content-identity-mismatch"


@pytest.mark.parametrize("change", [None, "missing", "spec", "command", "admission"])
def test_identity_checks_provider_configuration_without_replaying_it(recorded, change):
    job, objects, result = recorded
    row = parse_json(objects.read(result.accounting))
    admission = AdmissionPolicy("fixture", 3)
    config = {"schema": "behave.model-relay/1", "spec": job.attempt.as_dict(),
              "command": job.model.as_dict(), "admission": admission.as_dict()}
    if change == "spec":
        config["spec"]["attempt_id"] = "foreign"
    elif change == "command":
        config["command"]["revision"] = "foreign"
    elif change == "admission":
        config["admission"]["concurrency"] = 4
    ref = objects.put(canonical_json(config))
    provider = objects.put(Event(0, None, "broker_initialized", {"configuration": ref.as_dict()}, (ref,)).to_bytes())
    supervision = load_chain(objects, BlobRef.from_dict(row["heads"]["supervision"]))
    head = None
    for event in supervision:
        payload = thaw_json(event.payload)
        if event.sequence == 0:
            payload["model_admission"] = admission.identity
        head = objects.put(replace(event, parent=head, payload=payload).to_bytes())
    row["heads"].update(supervision=head.as_dict(), provider=None if change == "missing" else provider.as_dict())
    row["billing_source"] = "provider"
    altered = replace(result, accounting=objects.put(canonical_json(row)))
    if change is None:
        identity = load_accounting_identity(objects, altered)
        assert identity.job == BlobRef.of(job.to_bytes())
        assert identity.model_admission == admission.identity
        assert provider in accounting_artifacts(objects, altered)
    else:
        for read in (load_accounting_identity, accounting_artifacts):
            with pytest.raises(RecordError) as error:
                read(objects, altered)
            assert error.value.code == ("accounting-source-mismatch" if change == "missing"
                                        else "accounting-identity-mismatch")
