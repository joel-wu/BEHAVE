"""Construction phase receipts are evidence, not permission to launch a worker."""


import pytest

from behave.agent.attempt import AttemptInterrupted
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.records import RecordError
from behave.data.synthesis_records import PhaseReceipt, ProcessReceipt


def phase_body(run, role, *, cleanup="complete", pending=False):
    ref = run.objects.put(b"phase result")
    Tape(run.journal("revisions", "r0", role)).record("fixture_result", {}, (ref,))
    supervision = Tape(run.journal("revisions", "r0", role + "_supervision"))
    supervision.record("construction_requested", {"fixture": True})
    if not pending:
        supervision.record("construction_settled", {"schema": "behave.construction-settlement/1",
            "cleanup": cleanup, "status": "exited", "returncode": 0,
            "elapsed_seconds": 1., "interruption": None, "supervision_errors": []})
    for channel in ("provider", "development"):
        Tape(run.journal("revisions", "r0", role + "_" + channel)).record("fixture_ledger", {}, (ref,))
    return ref


@pytest.mark.parametrize("role,process_backed", [
    ("author", False), ("author", True), ("reader", True), ("source_review", True),
    ("scope_author", True), ("scope_reviewer", True), ("source_acquirer", True),
    ("rtl_author", True), ("check", False), ("rtl_check", False),
])
def test_shared_phase_seal_preserves_receipt_and_closure(tmp_path, role, process_backed):
    from behave.data.phase_journal import seal_phase, verify_process_seal

    run = JournalDirectory(tmp_path / "run")
    phase_body(run, role)
    # Independent old recipe: auxiliary channels first, role closure first in
    # the parent's event. Keep reference ordering as well as the record bytes.
    heads, auxiliary = {}, []
    if process_backed:
        for channel in ("supervision", "provider", "development"):
            head, refs = run.seal("revisions", "r0", role + "_" + channel)
            heads[channel] = head
            auxiliary.extend(refs)
    head, closure = run.seal("revisions", "r0", role)
    expected = PhaseReceipt(0, role, head, ProcessReceipt(**heads) if process_backed else None)
    phase, refs = seal_phase(run, 0, role, process_backed=process_backed)
    assert phase.as_dict() == expected.as_dict()
    assert refs == (*closure, *dict.fromkeys(auxiliary))
    if process_backed:
        verify_process_seal(run, phase)
    assert seal_phase(run, 0, role, process_backed=process_backed) == (phase, refs)


@pytest.mark.parametrize("state", ["missing", "pending", "unknown", "incomplete"])
def test_phase_seal_never_touches_journals_until_process_is_quiescent(tmp_path, monkeypatch, state):
    from behave.data.phase_journal import seal_phase

    run = JournalDirectory(tmp_path / "run")
    if state != "missing":
        phase_body(run, "source_acquirer", pending=state == "pending",
                   cleanup=state if state in {"unknown", "incomplete"} else "complete")
    monkeypatch.setattr(run, "seal", lambda *args: pytest.fail("sealed an unsettled phase"))
    with pytest.raises((AttemptInterrupted, RecordError)) as raised:
        seal_phase(run, 0, "source_acquirer", process_backed=True)
    assert raised.value.code == ("construction-outcome-unknown" if state in {"missing", "pending"}
                                 else "construction-cleanup-" + state)
    if state == "missing":
        # Journal.load creates its empty directory; it must not commit events,
        # artifacts or a receipt when the lifetime owner is absent.
        assert not any(path.is_file() for path in run.directory.rglob("*"))


@pytest.mark.parametrize("channel", ["supervision", "provider", "development"])
def test_replay_rejects_changed_auxiliary_head(tmp_path, channel):
    from behave.data.phase_journal import auxiliary_path, seal_phase, verify_process_seal

    run = JournalDirectory(tmp_path / "run")
    phase_body(run, "source_acquirer")
    phase, _ = seal_phase(run, 0, "source_acquirer", process_backed=True)
    journal = run.journal(*auxiliary_path(0, "source_acquirer", channel))
    journal.append(journal.load(), "changed", {})
    with pytest.raises(RecordError, match="auxiliary journal changed") as raised:
        verify_process_seal(run, phase)
    assert raised.value.code == "synthesis-phase-mismatch"


@pytest.mark.parametrize("cleanup", ["unknown", "incomplete"])
def test_matching_heads_do_not_replace_cleanup_evidence(tmp_path, cleanup):
    from behave.data.phase_journal import verify_process_seal

    run = JournalDirectory(tmp_path / "run")
    phase_body(run, "author", cleanup=cleanup)
    heads = {channel: run.seal("revisions", "r0", "author_" + channel)[0]
             for channel in ("supervision", "provider", "development")}
    phase = PhaseReceipt(0, "author", run.seal("revisions", "r0", "author")[0], ProcessReceipt(**heads))
    with pytest.raises(RecordError) as raised:
        verify_process_seal(run, phase)
    assert raised.value.code == "construction-cleanup-" + cleanup


@pytest.mark.parametrize("lane", ["scope", "acquisition"])
@pytest.mark.parametrize("state", ["clean_failure", "unknown", "pending"])
def test_coordinator_settlement_and_cold_replay(tmp_path, monkeypatch, publication, lane, state):
    from test_role_process import Unavailable

    from behave.data import role as role_module

    if lane == "scope":
        from test_scope_preparation import prepared

        from behave.data.scope import load_scope_preparation, prepare_scope

        _, _, _, options = prepared(tmp_path)
        invoke = prepare_scope
        load = lambda reader, head: load_scope_preparation(reader, head)[0]
        requested = "scope_phase_requested"
    else:
        from test_improvement_acquisition import configuration

        from behave.improve.acquisition import (
            AcquisitionConfiguration,
            acquire_materials,
            load_acquisition_report,
        )

        store, job, _, _ = configuration(tmp_path, publication)
        options = {"configuration": AcquisitionConfiguration(job), "reader": store,
                   "run": JournalDirectory(tmp_path / "acquire")}
        invoke, load, requested = acquire_materials, load_acquisition_report, "acquisition_requested"
    calls = []

    def launch(**kwargs):
        calls.append(kwargs["module"])
        if state == "pending":
            raise AttemptInterrupted("fixture-lost-owner")
        return {"schema": "behave.construction-settlement/2",
                "cleanup": "complete" if state == "clean_failure" else "unknown",
                "status": "supervisor-failed", "execution_status": None, "returncode": None,
                "elapsed_seconds": 1., "interruption": None, "supervision_errors": ["fixture-startup"]}, ()

    monkeypatch.setattr(role_module, "supervise_worker", launch)
    run = options["run"]
    if state == "clean_failure":
        result = invoke(**options)
        assert (result["status"] if lane == "acquisition" else result.status) == "interrupted"
        assert (result["code"] if lane == "acquisition" else result.code) == "fixture-startup"
        head, _ = run.seal(lane)
        assert load(run.objects, head) == result
    else:
        with pytest.raises((AttemptInterrupted, RecordError)):
            invoke(**options)
        assert run.journal(lane).load()[-1].kind == requested
    events = [event.to_bytes() for event in run.journal(lane).load()]
    monkeypatch.setattr(role_module, "supervise_worker", lambda **kw: pytest.fail("cold replay relaunched worker"))
    cold = {**options, "reader": Unavailable()}
    if state == "clean_failure":
        assert invoke(**cold) == result
    else:
        with pytest.raises((AttemptInterrupted, RecordError)):
            invoke(**cold)
    assert calls == ["behave.data.role"]
    assert [event.to_bytes() for event in run.journal(lane).load()] == events
