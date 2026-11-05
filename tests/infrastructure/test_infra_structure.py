"""Shared boundaries keep the existing wire format, ownership and replay rules."""

import itertools
import sys
from dataclasses import replace

import pytest

from behave.agent.admission import AdmissionPolicy, ResourcePool
from behave.agent.attempt import AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.model_ledger import validate_provider_configuration
from behave.agent.process_model import ModelCommand
from behave.agent.process_worker import WORKER_LAUNCH_FIELDS, WorkerChannels
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.records import RecordError, canonical_json, parse_json
from behave.core.storage import DirectoryStore
from behave.data.phase_journal import RevisionPhases, seal_process
from behave.data.synthesis_records import PhaseReceipt
from behave.task_execution.development import development_ingress
from behave.task_execution.testbench import TBPolicy


def channels_at(path):
    run = JournalDirectory(path)
    return WorkerChannels(run.journal("role"), run.journal("supervision"),
        ResourcePool(AdmissionPolicy("model", 2)), run.journal("provider"),
        ResourcePool(AdmissionPolicy("eda", 1)), run.journal("development"))


@pytest.mark.parametrize("omitted", ["model_pool", "provider", "development_pool", "development"])
def test_channels_require_both_sides_before_any_write(tmp_path, omitted):
    channels = channels_at(tmp_path / "run")
    with pytest.raises(ValueError, match="separate execution ledger"):
        replace(channels, **{omitted: None})
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("left,right", list(itertools.combinations(
    ("journal", "supervision", "provider", "development"), 2)))
def test_channels_reject_every_journal_alias(tmp_path, left, right):
    channels = channels_at(tmp_path / "run")
    with pytest.raises(ValueError, match="distinct"):
        replace(channels, **{right: getattr(channels, left)})


@pytest.mark.parametrize("name", ["supervision", "provider", "development"])
def test_channels_reject_foreign_object_stores(tmp_path, name):
    channels = channels_at(tmp_path / "run")
    foreign = AttemptJournal(getattr(channels, name).directory, DirectoryStore(tmp_path / "foreign"))
    with pytest.raises(ValueError, match="one object store"):
        replace(channels, **{name: foreign})


@pytest.mark.parametrize("relayed", [False, True])
def test_shared_launch_payload_preserves_worker_abi(tmp_path, relayed):
    channels = channels_at(tmp_path / "run")
    if not relayed:
        channels = replace(channels, model_pool=None, provider=None, development_pool=None, development=None)
    ref = channels.journal.objects.put(b"fixed job")
    expected = {"job": ref.as_dict(), "journal": str(channels.journal.directory),
        "objects": str(channels.journal.objects.directory),
        "model_admission": channels.model_pool.policy.identity if relayed else None,
        "provider": str(channels.provider.directory) if relayed else None,
        "development_admission": channels.development_pool.policy.identity if relayed else None,
        "development": str(channels.development.directory) if relayed else None}
    assert set(expected) == WORKER_LAUNCH_FIELDS
    assert canonical_json(channels.payload(ref)) == canonical_json(expected)


def test_ingress_factories_defer_owners_and_keep_separate_ledgers(tmp_path, monkeypatch):
    from behave.agent import model_relay
    from behave.task_execution import development as development_module

    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return object()
    monkeypatch.setattr(model_relay, "ModelRelay", create)
    monkeypatch.setattr(development_module, "DevelopmentRelay", create)
    channels = channels_at(tmp_path / "run")
    command = ModelCommand((sys.executable,), "fixture/1")
    spec = AttemptSpec("attempt", "solver", {"transport": command.identity})
    policy = TBPolicy("fixture", "fixture", "fixture", max_retries=0)
    model = channels.model_ingress(spec, command)
    development = development_ingress(spec, policy, channels)
    assert calls == [] and not (tmp_path / "run").exists()
    def cancelled():
        return False
    model(99., cancelled)
    development(99., cancelled)
    assert calls == [
        {"spec": spec, "command": command, "journal": channels.provider,
         "pool": channels.model_pool, "deadline": 99., "cancelled": cancelled},
        {"spec": spec, "policy": policy, "solver": channels.journal, "journal": channels.development,
         "pool": channels.development_pool, "deadline": 99., "cancelled": cancelled},
    ]
    with pytest.raises(ValueError, match="admitted cocotb tool"):
        development_ingress(spec, None, channels)
    local = replace(channels, model_pool=None, provider=None, development_pool=None, development=None)
    assert local.model_ingress(spec, command) is None
    assert development_ingress(spec, None, local) is None


@pytest.mark.parametrize("code", ["accounting-identity-mismatch", "synthesis-process-mismatch"])
@pytest.mark.parametrize("change", [None, "spec", "command", "admission", "extra_spec", "float_turns"])
def test_provider_auth_binds_exact_configuration_and_preserves_domain_code(code, change):
    command = ModelCommand((sys.executable,), "fixture/1")
    spec = AttemptSpec("attempt", "solver", {"transport": command.identity})
    policy = AdmissionPolicy("model", 2)
    row = parse_json(canonical_json({"schema": "behave.model-relay/1", "spec": spec.as_dict(),
        "command": command.as_dict(), "admission": policy.as_dict()}))
    if change == "spec":
        row["spec"]["attempt_id"] = "foreign"
    elif change == "command":
        row["command"]["revision"] = "foreign/1"
    elif change == "admission":
        row["admission"]["concurrency"] += 1
    elif change == "extra_spec":
        row["spec"]["extra"] = True
    elif change == "float_turns":
        row["spec"]["max_turns"] = float(row["spec"]["max_turns"])
    def validate():
        validate_provider_configuration(row, spec=spec, command=command, admission=policy.identity,
                                        mismatch_code=code)
    if change is None:
        validate()
    else:
        with pytest.raises(RecordError) as raised:
            validate()
        assert raised.value.code == code


def phase_body(run, role, process_backed, *, cleanup="complete"):
    """Path-free role events allow exact comparison across two separate runs."""
    body = run.journal("revisions", "r0", role)
    ref = run.objects.put(b"phase evidence")
    Tape(body).record("fixture_result", {"result": ref.as_dict()}, (ref,))
    if process_backed:
        tape = Tape(run.journal("revisions", "r0", role + "_supervision"))
        tape.record("construction_requested", {"fixture": True})
        tape.record("construction_settled", {"schema": "behave.construction-settlement/1",
            "cleanup": cleanup, "status": "exited", "returncode": 0,
            "elapsed_seconds": 1., "interruption": None, "supervision_errors": []})
    return ref


@pytest.mark.parametrize("role,process_backed", [
    ("author", False), ("author", True), ("reader", True), ("source_review", True),
    ("check", False), ("rtl_author", True), ("rtl_check", False),
])
def test_phase_owner_matches_prior_event_recipe_and_replays_cold(tmp_path, role, process_backed):
    run = JournalDirectory(tmp_path / "new")
    old = JournalDirectory(tmp_path / "prior")
    ref = phase_body(run, role, process_backed)
    assert phase_body(old, role, process_backed) == ref
    inputs, output = {"draft": None}, {"evidence": ref.as_dict()}
    process_roles = frozenset({role}) if process_backed else frozenset()
    phases = RevisionPhases(run, Tape(run.journal("synthesis")), process_roles=process_roles)
    phases.begin(0, role, inputs)
    phases.settle(output, (ref,))

    # Frozen pre-extraction recipe: explicit request, process-before-role seal,
    # unchanged receipt fields and artifact ordering. No new lifecycle format.
    prior = Tape(old.journal("synthesis"))
    prior.record("phase_requested", {"revision": 0, "role": role, "inputs": inputs})
    process, auxiliary = seal_process(old, 0, role) if process_backed else (None, ())
    head, closure = old.seal("revisions", "r0", role)
    receipt = PhaseReceipt(0, role, head, process)
    prior.record("phase_settled", {"phase": receipt.as_dict(), "output": output}, (*closure, *auxiliary, ref))
    expected = [e.to_bytes() for e in prior.events]
    assert [e.to_bytes() for e in phases.tape.events] == expected
    assert phases.active is None and phases.completed == [receipt]

    cold = RevisionPhases(run, Tape(run.journal("synthesis")), process_roles=process_roles)
    cold.begin(0, role, inputs)
    cold.settle(output, (ref,))
    cold.tape.finish()
    assert cold.completed == phases.completed
    assert [e.to_bytes() for e in run.journal("synthesis").load()] == expected


@pytest.mark.parametrize("cleanup", ["unknown", "incomplete"])
def test_unsettled_process_cannot_seal_role_or_advance_phase(tmp_path, monkeypatch, cleanup):
    run = JournalDirectory(tmp_path / "run")
    phase_body(run, "author", True, cleanup=cleanup)
    phases = RevisionPhases(run, Tape(run.journal("synthesis")), process_roles=frozenset({"author"}))
    with pytest.raises(RuntimeError, match="no synthesis phase"):
        phases.settle()
    phases.begin(0, "author", {})
    with pytest.raises(RuntimeError, match="unsettled"):
        phases.begin(1, "reader", {})
    def forbidden_seal(*args):
        pytest.fail("must not seal any journal before confirming process cleanup")
    monkeypatch.setattr(run, "seal", forbidden_seal)
    with pytest.raises(RecordError) as raised:
        phases.settle()
    assert raised.value.code == "construction-cleanup-" + cleanup
    assert phases.active == (0, "author") and phases.completed == []
    assert [event.kind for event in run.journal("synthesis").load()] == ["phase_requested"]
