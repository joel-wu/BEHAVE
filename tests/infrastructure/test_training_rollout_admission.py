"""Replace empty transport slots without selecting candidates by reward."""

import json
import threading
from collections import Counter
from dataclasses import replace

import pytest
from test_evaluation import plan_for
from test_task_workflow import setup

from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json
from behave.task_execution import batch as batch_module
from behave.task_execution.batch import read_batch_report, summarize_recorded
from behave.task_execution.result import TaskReport, TaskResult
from behave.train.pool import CompletedGroups, has_rollout, retry_empty_rollout


def test_empty_transport_retries_only_its_slot_and_replays_without_dispatch(tmp_path, publication, monkeypatch):
    first, _ = setup(tmp_path, publication)
    second = replace(first, attempt=replace(first.attempt, attempt_id="second"))
    plan = replace(plan_for(first, second), score_on_submit=False)
    counts, fenced = Counter(), []

    def execute(**kwargs):
        job, run = kwargs["job"], kwargs["run"]
        key = job.attempt.attempt_id
        counts[key] += 1
        empty = job == first and counts[key] == 1
        account = run.objects.put(canonical_json({"turns": [] if empty else [{"exclusion": None}],
            "charges": [], "unpriced_dispatches": 0, "unresolved_dispatches": 0,
            "billing_source": "fixture"}))
        head = run.objects.put(b"fixture solver head")
        result = TaskResult(job.task.task_id, job.task.release, job.task.revision, key,
            None if empty else head, None if empty else "stopped", None, None,
            "solver" if empty else None,
            "model-unknown:model-relay-ConnectionResetError" if empty else None, account)
        ref = run.objects.put(result.to_bytes())
        run.journal("task").append((), "finished", {"result": ref.as_dict()}, (account, head, ref))
        return TaskReport(result, ref, head)

    monkeypatch.setattr(batch_module, "execute_task", execute)
    monkeypatch.setattr(batch_module, "require_task_quiescent", lambda run, **_: fenced.append(run.directory))
    race = CompletedGroups(("task", "task"), 1)
    args = dict(plan=plan, reader=publication.reader, directory=tmp_path / "batch",
        case_retries=2, retry_case=retry_empty_rollout, accept_case=has_rollout,
        report_summary=summarize_recorded, report_reader=read_batch_report,
        on_case=race.complete)
    report = batch_module.run_batch(**args)
    assert counts == {first.attempt.attempt_id: 2, second.attempt.attempt_id: 1}
    assert len(fenced) == 1
    assert race.summary()["selected_indices"] == [0, 1]
    assert [row["final_score"] for row in report.summary["candidates"]] == [0., 0.]
    key = BlobRef.of(first.to_bytes()).sha256
    attempt_files = sorted((tmp_path / "batch/candidates" / key).glob("attempt_*.json"))
    assert len(attempt_files) == 2
    assert json.loads(attempt_files[0].read_text())["failure_code"].endswith("ConnectionResetError")
    before = counts.copy()
    assert batch_module.run_batch(**args) == report
    assert counts == before


def test_partial_generated_failure_is_not_retried(tmp_path):
    from types import SimpleNamespace

    from behave.core.storage import DirectoryStore

    store = DirectoryStore(tmp_path / "objects")
    ref = store.put(canonical_json({"turns": [{"exclusion": None}]}))
    result = SimpleNamespace(failure_stage="solver",
        failure_code="model-unknown:model-relay-ConnectionResetError", accounting=ref)
    assert not retry_empty_rollout(result, store)
    result.accounting = store.put(canonical_json({"turns": []}))
    assert retry_empty_rollout(result, store)
    result.failure_stage = "judge"
    assert not retry_empty_rollout(result, store)


@pytest.mark.parametrize("statuses,expected", [
    ({}, False),
    ({0: ("unavailable", "token-data-missing")}, False),
    ({0: ("excluded", "original-exclusion")}, False),
    ({0: ("unavailable", "token-data-missing"), 1: ("eligible", None)}, True),
])
def test_group_admission_uses_cached_token_availability_not_reward(statuses, expected):
    ref = BlobRef.of(b"result")
    class NoReads:
        def read(self, reference):
            pytest.fail("live admission reread producer tokens")
    for reward in (0., 1., None):
        assert has_rollout({"result": ref.as_dict(), "score": reward}, NoReads(),
                           token_statuses={ref.sha256: statuses}) is expected


def test_cold_admission_reuses_eligibility_for_export(tmp_path):
    from test_slime_records import data
    store, _, _, candidates = data(tmp_path)
    candidate = candidates[0]
    first = store.put(canonical_json({"token_data": {
        "schema": "behave.native-token-unavailable/1", "reason": "missing-native-logprobs"}}))
    second = store.put(canonical_json({"token_data": candidate.turns[0].tokens.as_dict()}))
    account = store.put(canonical_json({"turns": [
        {"turn": 0, "exclusion": None, "response": first.as_dict()},
        {"turn": 1, "exclusion": None, "response": second.as_dict()}]}))
    result = store.put(canonical_json({"accounting": account.as_dict()}))
    cache = {}
    args = dict(token_statuses=cache, requirements=candidate.requirements.as_dict(), verifier=object())
    assert has_rollout({"result": result.as_dict()}, store, **args)
    assert cache[result.sha256] == {
        0: ("unavailable", "missing-native-logprobs"), 1: ("eligible", None)}
    class NoReads:
        def read(self, reference):
            pytest.fail("repeated admission reread token payloads")
    assert has_rollout({"result": result.as_dict()}, NoReads(), **args)


@pytest.mark.isolation
def test_real_relay_reset_replaces_empty_attempt_and_preserves_zero_scores(tmp_path, publication, monkeypatch):
    from test_evaluation import Judge

    from behave.agent import local_rpc
    from behave.agent.admission import AdmissionPolicy

    job, _ = setup(tmp_path, publication)
    receive = local_rpc.receive_frame
    injected = threading.Event()

    def reset_first_model_ingress(*args, **kwargs):
        if threading.current_thread().name == "behave-model-relay" and not injected.is_set():
            injected.set()
            raise ConnectionResetError("fixture initial relay reset")
        return receive(*args, **kwargs)

    monkeypatch.setattr(local_rpc, "receive_frame", reset_first_model_ingress)
    seen = []
    report = batch_module.run_batch(plan=plan_for(job, model_admission=AdmissionPolicy("model", 1)), reader=publication.reader,
        directory=tmp_path / "actual", judge=Judge(no_result=True), case_retries=2,
        retry_case=retry_empty_rollout, accept_case=has_rollout, on_case=seen.append)
    assert injected.is_set() and seen == [0]
    assert report.summary["candidates"][0]["final_score"] == .5
    attempts = list((tmp_path / "actual/candidates").glob("*/attempt_*.json"))
    assert len(attempts) == 2
    outcomes = sorted((json.loads(path.read_text()) for path in attempts), key=lambda row: row["attempt"])
    assert outcomes[0]["failure_code"].startswith("model-unknown:model-relay-")
    assert outcomes[1]["failure_code"] is None
