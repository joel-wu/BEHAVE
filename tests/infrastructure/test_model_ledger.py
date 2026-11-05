from dataclasses import replace

import pytest
from test_process_model import command, request

from behave.agent.journal import AttemptJournal
from behave.agent.model import Charge, ModelFailure, ModelReply, ModelRequest
from behave.agent.model_ledger import ModelLedger
from behave.agent.trajectory import trajectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json
from behave.core.storage import DirectoryStore


def ledger(tmp_path):
    journal = AttemptJournal(tmp_path / "provider", DirectoryStore(tmp_path / "objects"))
    return ModelLedger(journal, {"policy": "fixture"}), journal


def test_provider_result_commits_before_delivery_and_duplicate_reads_do_not_charge_twice(tmp_path):
    owner, journal = ledger(tmp_path)
    req = request(command("pass"))
    reply = ModelReply("known", raw=b"evidence", charge=Charge("0.01", "USD", "test"))
    assert owner.dispatch(req, lambda _: reply) == reply
    restored, _ = ledger(tmp_path)
    assert restored.dispatch(req, lambda _: pytest.fail("duplicate provider dispatch")) == reply
    assert trajectory(journal).cost_by_currency() == {"USD": "0.01"}
    with pytest.raises(RecordError, match="different request"):
        restored.lookup(replace(req, messages=({"role": "user", "content": "changed"},)))


def test_pending_provider_dispatch_stays_unknown_and_queue_decline_is_not_usage(tmp_path):
    owner, journal = ledger(tmp_path)
    req = request(command("pass"))
    class Crash(BaseException):
        pass
    with pytest.raises(Crash):
        owner.dispatch(req, lambda _: (_ for _ in ()).throw(Crash()))
    restored, _ = ledger(tmp_path)
    got = restored.dispatch(req, lambda _: pytest.fail("unknown is not permission to retry"))
    assert got.kind == "unknown" and got.code == "provider-outcome-unknown"
    assert trajectory(journal).unresolved_dispatches == 1
    declined = replace(req, call_id="a:1:0", turn=1)
    assert restored.decline(declined, "resource-cancelled").kind == "permanent"
    assert restored.dispatch(declined, lambda _: pytest.fail("declined call must not restart")).code == "resource-cancelled"
    view = trajectory(journal)
    assert view.unpriced_dispatches == 0 and view.unresolved_dispatches == 1
    assert not hasattr(view.billing, "turns")


@pytest.mark.parametrize("outcome", ["reply", "failure", "declined", "exception"])
def test_settlement_reuses_the_saved_request_without_reserializing_or_putting_it(tmp_path, monkeypatch, outcome):
    owner, journal = ledger(tmp_path)
    req = request(command("pass"))
    packet = canonical_json(req.as_dict())
    serialized, stored = [], []
    as_dict, put = ModelRequest.as_dict, journal.objects.put

    def encode(actual):
        if actual is req:
            serialized.append(actual.call_id)
        return as_dict(actual)

    def save(data):
        if data == packet:
            stored.append(data)
        return put(data)

    monkeypatch.setattr(ModelRequest, "as_dict", encode)
    monkeypatch.setattr(journal.objects, "put", save)
    expected = ModelReply("done") if outcome == "reply" else ModelFailure("permanent", "fixture-declined")

    def generate(actual):
        assert actual is req and journal.load()[-1].kind == "model_requested"
        if outcome == "exception":
            raise RuntimeError("fixture failure")
        return expected

    if outcome == "declined":
        result = owner.decline(req, expected.code)
        kinds = ["broker_initialized", "model_declined"]
    else:
        result = owner.dispatch(req, generate)
        if outcome == "exception":
            expected = ModelFailure("unknown", "provider-RuntimeError")
        kinds = ["broker_initialized", "model_requested", "model_settled"]
    assert result == expected
    assert serialized == [req.call_id] and stored == [packet]
    events = journal.load()
    assert [event.kind for event in events] == kinds
    assert events[-1].artifacts[0] == BlobRef.of(packet)
    restored, _ = ledger(tmp_path)
    assert restored.lookup(req) == result


def test_settlement_write_failure_propagates_and_recovery_does_not_redispatch(tmp_path, monkeypatch):
    owner, journal = ledger(tmp_path)
    req = request(command("pass"))
    record = owner.tape.record
    calls = []

    def fail_settlement(kind, *args, **kwargs):
        if kind == "model_settled":
            raise OSError("fixture write failure")
        return record(kind, *args, **kwargs)

    def generate(actual):
        calls.append(actual.call_id)
        return ModelReply("done")

    monkeypatch.setattr(owner.tape, "record", fail_settlement)
    with pytest.raises(OSError, match="fixture write failure"):
        owner.dispatch(req, generate)
    assert journal.load()[-1].kind == "model_requested"
    restored, _ = ledger(tmp_path)
    assert restored.dispatch(req, generate).code == "provider-outcome-unknown"
    assert calls == [req.call_id]
    assert trajectory(journal).unresolved_dispatches == 1
