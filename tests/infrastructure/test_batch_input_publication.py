"""Grouped candidates publish shared inputs once without changing their records."""

from collections import Counter
from dataclasses import replace

from test_evaluation import Judge, plan_for
from test_task_workflow import setup

from behave.agent.journal import load_chain
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError
from behave.core.storage import DirectoryStore
from behave.eval.runner import run_evaluation
from behave.task_execution import batch as batch_module
from behave.task_execution.batch_plan import BatchPlan
from behave.task_execution.run_store import RunStore


def test_plan_decode_reads_shared_public_files_once(tmp_path, publication):
    job, _ = setup(tmp_path / "task", publication)
    plan = plan_for(*(replace(job, attempt=replace(job.attempt, attempt_id=f"candidate-{i}"))
                      for i in range(8)))
    counts = Counter()

    class Reader:
        def read(self, reference):
            counts[reference] += 1
            return publication.reader.read(reference)

    restored = BatchPlan.from_bytes(plan.to_bytes(), Reader())
    assert restored == plan
    assert all(count == 1 for count in counts.values())
    assert restored.jobs[0].task.files[0].data is restored.jobs[1].task.files[0].data


def test_group_inputs_are_deduplicated_before_storage(tmp_path, publication, monkeypatch):
    job, _ = setup(tmp_path / "task", publication)
    jobs = tuple(replace(job, attempt=replace(job.attempt, attempt_id=f"candidate-{i}")) for i in range(8))
    plan = plan_for(*jobs)
    run = RunStore(tmp_path / "batch")
    expected = tuple(dict.fromkeys(BlobRef.of(data) for item in jobs for data in item.input_blobs()))
    assert tuple(BlobRef.of(data) for data in plan.input_blobs()) == expected
    counts = Counter()
    original = DirectoryStore.put

    def put(store, data):
        if store.directory == run.objects.directory and BlobRef.of(data) in expected:
            counts[BlobRef.of(data)] += 1
        return original(store, data)

    def interrupted(**kwargs):
        # Input-publication test only; no model, score computation or process.
        raise RecordError("fixture-interrupted", "fixture has no candidate execution")

    monkeypatch.setattr(DirectoryStore, "put", put)
    monkeypatch.setattr(batch_module, "execute_task", interrupted)
    result = run_evaluation(plan=plan, reader=publication.reader, directory=run.directory, judge=Judge())
    assert counts == Counter({reference: 1 for reference in expected})
    events = load_chain(run.objects, result.head)
    assert events[0].artifacts == (BlobRef.of(plan.to_bytes()), *expected)
    assert result.summary["requested"] == 8 and result.summary["no_result"] == 8
    assert len([event for event in events if event.kind == "evaluation_case"]) == 8
