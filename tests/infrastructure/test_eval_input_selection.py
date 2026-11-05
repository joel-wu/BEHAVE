"""Every Eval entry must preserve the requested candidate set before dispatch."""

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from behave.core.records import RecordError
from behave.data.published import PublishedReleaseReader, TaskRevision, load_published_task
from behave.eval.generation import build_plan, endpoint_for, load_tasks, run
from behave.task_execution.judge_policy import JudgePolicy


@pytest.fixture
def settings(publication):
    example = Path(__file__).resolve().parents[2] / "configs/eval/local.example.json"
    return {**json.loads(example.read_text()), "task_objects": str(publication.root),
            "release": publication.ref.as_dict(), "tasks": [publication.task.task_id]}


@pytest.mark.parametrize("explicit_cases", [False, True])
def test_shared_builder_refuses_partial_preparation(tmp_path, publication, settings, explicit_cases):
    settings["tasks"].append("missing-task")
    if explicit_cases:
        settings["cases"] = [{"task": task, "setting": "joint"} for task in settings["tasks"]]
    output = tmp_path / "eval"
    objects, tasks = load_tasks(output, settings)
    assert len(tasks) == 1
    with pytest.raises(RecordError, match="missing-task") as error:
        build_plan(output, settings, objects, tasks, endpoint_for(output, settings))
    assert error.value.code == "evaluation-inputs-incomplete"
    rows = json.loads((output / "preparation.json").read_text())
    assert [row["status"] for row in rows] == ["prepared", "preparation_failed"]
    assert all("score" not in row for row in rows)
    assert not (output / "plan.json").exists() and not (output / "run").exists()


def test_missing_explicit_case_cannot_disappear_from_a_plan(tmp_path, settings):
    objects, tasks = load_tasks(tmp_path, settings)
    settings["cases"] = [{"task": settings["tasks"][0], "setting": "joint"},
                         {"task": "missing-case", "setting": "rtl_only"}]
    with pytest.raises(RecordError, match="missing-case") as error:
        build_plan(tmp_path, settings, objects, tasks, endpoint_for(tmp_path, settings))
    assert error.value.code == "evaluation-inputs-incomplete"


def test_conflicting_prepared_tasks_are_not_overwritten(tmp_path, settings):
    objects, tasks = load_tasks(tmp_path, settings)
    with pytest.raises(RecordError, match="duplicate") as error:
        build_plan(tmp_path, settings, objects, tasks * 2, endpoint_for(tmp_path, settings))
    assert error.value.code == "evaluation-tasks-invalid"


def test_paired_selection_keeps_case_order_and_complete_inputs(tmp_path, settings):
    objects, tasks = load_tasks(tmp_path, settings)
    settings["cases"] = [{"task": settings["tasks"][0], "setting": setting}
                         for setting in ("rtl_only", "joint")]
    plan, recovery = build_plan(tmp_path, settings, objects, tasks, endpoint_for(tmp_path, settings))
    assert [(job.task.task_id, job.setting) for job in plan.jobs] == [
        (case["task"], case["setting"]) for case in settings["cases"]]
    assert recovery is None


@pytest.fixture
def comparison_inputs(publication, settings):
    policy = JudgePolicy("fixture-command", "fixture-result", seed="17", baseline_cases=1,
                         sequence_length=1, search_seconds=0)
    tasks = tuple(replace(publication.task, task_id=f"comparison-{i}",
                          judge_policy=publication.put(policy.to_bytes())) for i in range(3))
    revisions = tuple(TaskRevision(task.task_id, publication.put(task.to_bytes())) for task in tasks)
    _, reference = publication.publish(tasks=revisions)
    public = tuple(load_published_task(publication.reader, reference, task.task_id).public for task in tasks)
    settings.update(release=reference.as_dict(), tasks=[task.task_id for task in tasks])
    return public


@pytest.mark.parametrize("visible", [False, True])
def test_comparison_plan_reuses_inventory_but_preserves_each_binding(
        tmp_path, publication, settings, comparison_inputs, monkeypatch, visible):
    from behave.data import published

    public = comparison_inputs
    settings.update(reference_visible=visible, cases=[
        {"task": public[2].task_id, "setting": "rtl_sim"},
        {"task": public[0].task_id, "setting": "joint_sim"},
        {"task": public[2].task_id, "setting": "joint_sim"},
        {"task": public[1].task_id, "setting": "rtl_only"},
    ])
    # Agent-authored comparisons cannot expose a supplied reference. Exercise
    # visibility on the provided-reference jobs without changing that rule.
    if visible:
        settings["cases"] = [{**case, "setting": "rtl_sim"} for case in settings["cases"][:2]]
    reads = Counter()

    class CountingReader:
        def read(self, ref):
            reads[ref] += 1
            return publication.reader.read(ref)

    endpoint = endpoint_for(tmp_path, settings)
    plan, recovery = build_plan(tmp_path, settings, CountingReader(), public, endpoint)
    assert recovery is None
    assert reads[public[0].release] == 1
    assert reads[public[1].revision] == 0  # Public-only or unselected task.
    assert reads[public[2].revision] == (1 if visible else 2)
    assert [(job.task.task_id, job.setting) for job in plan.jobs] == [
        (case["task"], case["setting"]) for case in settings["cases"]]
    assert all(b"PRIVATE" not in file.data for job in plan.jobs for file in job.task.files)
    assert plan.jobs[0].comparison.reference_visible is visible

    class SingleTaskReader:
        def __init__(self, objects, reference):
            self.objects, self.reference = objects, reference

        def load(self, task_id):
            return load_published_task(self.objects, self.reference, task_id)

    # Compare the complete plan and retained inputs to the pre-batch read path.
    monkeypatch.setattr(published, "PublishedReleaseReader", SingleTaskReader)
    previous, _ = build_plan(tmp_path, settings, publication.reader, public, endpoint)
    assert plan.to_bytes() == previous.to_bytes()
    assert plan.input_blobs() == previous.input_blobs()


def test_public_only_plan_never_opens_private_inventory(tmp_path, settings, comparison_inputs):
    class NoPrivateReads:
        def read(self, ref):
            pytest.fail("public-only plans must not reopen private task inputs")

    settings["solver_settings"] = ["joint", "rtl_only"]
    plan, _ = build_plan(tmp_path, settings, NoPrivateReads(), comparison_inputs, endpoint_for(tmp_path, settings))
    assert len(plan.jobs) == 6
    assert all(job.comparison is None for job in plan.jobs)


@pytest.mark.parametrize("part", ["inventory", "reference", "unselected-manifest"])
def test_comparison_plan_verifies_selected_closures_only(
        tmp_path, publication, settings, comparison_inputs, part):
    public = comparison_inputs
    settings["cases"] = [{"task": public[0].task_id, "setting": "joint_sim"}]
    reference = {"inventory": public[0].release,
                 "reference": publication.task.reference_behavior.files[0].content,
                 "unselected-manifest": public[1].revision}[part]
    (publication.root / reference.sha256).write_bytes(b"!" * reference.size_bytes)
    if part == "unselected-manifest":
        plan, _ = build_plan(tmp_path, settings, publication.reader, public, endpoint_for(tmp_path, settings))
        assert len(plan.jobs) == 1
    else:
        with pytest.raises(RecordError) as error:
            build_plan(tmp_path, settings, publication.reader, public, endpoint_for(tmp_path, settings))
        assert error.value.code == "content-identity-mismatch"


def test_comparison_plan_revalidates_payload_between_cases(
        tmp_path, publication, settings, comparison_inputs, monkeypatch):
    from behave.data import published

    task = comparison_inputs[0]
    settings["cases"] = [{"task": task.task_id, "setting": setting} for setting in ("joint_sim", "rtl_sim")]
    reference = publication.task.reference_behavior.files[0].content

    class ChangingReader(PublishedReleaseReader):
        def load(self, task_id):
            result = super().load(task_id)
            (publication.root / reference.sha256).write_bytes(b"!" * reference.size_bytes)
            return result

    monkeypatch.setattr(published, "PublishedReleaseReader", ChangingReader)
    with pytest.raises(RecordError) as error:
        build_plan(tmp_path, settings, publication.reader, (task,), endpoint_for(tmp_path, settings))
    assert error.value.code == "content-identity-mismatch"


def test_comparison_plan_still_rejects_mixed_release_inputs(
        tmp_path, publication, settings, comparison_inputs):
    first = comparison_inputs[0]
    other = replace(publication.task, task_id="another-release",
                    judge_policy=publication.put(JudgePolicy(
                        "fixture-command", "fixture-result", seed="17", baseline_cases=1,
                        sequence_length=1, search_seconds=0).to_bytes()))
    _, reference = publication.publish(other)
    second = load_published_task(publication.reader, reference, other.task_id).public
    settings["cases"] = [{"task": task.task_id, "setting": "joint_sim"} for task in (first, second)]
    with pytest.raises(ValueError, match="one pinned task release"):
        build_plan(tmp_path, settings, publication.reader, (first, second), endpoint_for(tmp_path, settings))


def test_campaign_stops_before_dispatch_when_preparation_is_partial(tmp_path, settings, monkeypatch):
    settings["tasks"].append("missing-task")
    (tmp_path / "settings.json").write_text(json.dumps(settings))
    monkeypatch.setenv("BEHAVE_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", lambda *_: {})
    monkeypatch.setattr("behave.runtime.records.signal.signal", lambda *_: None)
    monkeypatch.setattr("behave.sim.api.prepare_runtime", lambda **_: None)
    monkeypatch.setattr("behave.eval.generation.check_development_shell", lambda *_: None)
    monkeypatch.setattr("behave.eval.runner.run_evaluation",
                        lambda **_: pytest.fail("incomplete inputs must not dispatch model or judge work"))
    with pytest.raises(RecordError, match="missing-task") as error:
        run(tmp_path, status_output=tmp_path)
    assert error.value.code == "evaluation-inputs-incomplete"
    assert (tmp_path / "preparation.json").exists()
    assert not (tmp_path / "plan.json").exists()
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "run").exists()
