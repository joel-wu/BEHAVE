"""The public preparation command feeds the existing Eval runner and journals."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_rejudge import local_rollout  # noqa: F401 -- existing trusted solver fixture

from behave.cli.evaluation_plan import prepare
from behave.core.artifacts import DirectoryReader
from behave.core.records import RecordError
from behave.data.published import load_published_task
from behave.eval.__main__ import main
from behave.eval.plan import EvaluationPlan


@pytest.fixture
def configuration():
    path = Path(__file__).resolve().parents[2] / "configs/eval/local.example.json"
    return json.loads(path.read_text())


def arguments(tmp_path, publication, configuration):
    config = tmp_path / "configuration.json"
    config.write_text(json.dumps(configuration))
    release = tmp_path / "release-ref.json"
    release.write_text(json.dumps(publication.ref.as_dict()))
    return ["prepare", "--configuration", str(config), "--task-objects", str(publication.root),
            "--release", str(release), "--task", publication.task.task_id, "--output", str(tmp_path / "prepared")]


def test_public_prepare_is_offline_and_keeps_complete_private_input_closure(
        tmp_path, publication, configuration, monkeypatch, capsys):
    monkeypatch.setattr("behave.sim.api.prepare_runtime", lambda **_: pytest.fail("preparation cannot start runtime"))
    monkeypatch.setattr("behave.eval.runner.run_evaluation", lambda **_: pytest.fail("preparation cannot dispatch"))
    assert main(arguments(tmp_path, publication, configuration)) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["status"] == "prepared" and result["candidates"] == 1
    root = tmp_path / "prepared"
    reader = DirectoryReader(root / "inputs")
    plan = EvaluationPlan.from_bytes((root / "plan.json").read_bytes(), reader)
    assert result["plan"] == plan.identity.as_dict()
    task = load_published_task(reader, publication.ref, publication.task.task_id)
    assert task == load_published_task(publication.reader, publication.ref, publication.task.task_id)
    assert all(b"PRIVATE" not in file.data for file in plan.jobs[0].task.files)
    assert plan.jobs[0].attempt.context_reset_message is None
    assert plan.jobs[0].attempt.context_tokens == 131072
    endpoint = json.loads(plan.jobs[0].model.argv[-1])
    assert endpoint == configuration["endpoint"]
    assert endpoint["parameters"]["seed"] == 17
    assert not plan.score_on_submit and plan.testbench is not None
    assert not (root / "run").exists()
    # The original source store is no longer needed after preparation.
    publication.root.rename(tmp_path / "original-inputs-offline")
    assert main(["--plan", str(root / "plan.json"), "--task-objects", str(root / "inputs"), "--dry-run"]) == 0


@pytest.mark.parametrize("setting", ["joint", "rtl_only", "joint_sim", "rtl_sim"])
@pytest.mark.parametrize("score", [True, False])
@pytest.mark.parametrize("preview", [None, 32768])
def test_prepared_plan_is_identical_to_existing_campaign_builder(
        tmp_path, publication, configuration, setting, score, preview):
    from behave.agent.admission import AdmissionPolicy
    from behave.agent.attempt import AttemptSpec
    from behave.eval.generation import endpoint_for
    from behave.task_execution.cocotb_worker import CocotbBackend
    from behave.task_execution.judge_policy import JudgePolicy
    from behave.task_execution.sim_tool import ComparisonToolInputs
    from behave.task_execution.simulator_judge import SimulatorJudge
    from behave.task_execution.solver import SolverJob
    from behave.task_execution.solver_settings import comparison_source
    from behave.task_execution.testbench import TBPolicy

    policy = JudgePolicy("fixture-command", "fixture-authority", search_seconds=0, max_retries=0)
    publication.task = replace(publication.task, judge_policy=publication.put(policy.to_bytes()))
    publication.release, publication.ref = publication.publish()
    configuration.update(setting=setting, score_on_submit=score, output_preview_bytes=preview)
    root = tmp_path / "prepared"
    prepare(configuration=configuration, task_objects=publication.root, release=publication.ref,
            tasks=[publication.task.task_id], output=root)
    settings = json.loads((root / "settings.json").read_text())
    reader = DirectoryReader(root / "inputs")
    task = load_published_task(reader, publication.ref, publication.task.task_id)
    endpoint = endpoint_for(root, settings)
    source = comparison_source(setting)
    # Assemble the pre-existing wire contract independently of build_plan and
    # its new preflight, so an implicit settings/identity change is observable.
    job = SolverJob(task.public, AttemptSpec(f"prepared-{task.public.task_id}", "solver",
                    endpoint.model_policy(), **configuration["attempt"]), endpoint.command(), setting=setting,
                    comparison=None if source is None else ComparisonToolInputs.baseline(task.judge, source=source),
                    output_preview_bytes=preview)
    expected = EvaluationPlan("prepared", (job,), SimulatorJudge.identity, configuration["workers"],
        AdmissionPolicy("eval-review-judging", configuration["judge_workers"]),
        TBPolicy(CocotbBackend.identity, CocotbBackend.collector, CocotbBackend.metric) if setting == "joint" else None,
        AdmissionPolicy("eval-review-model", configuration["model_workers"]), score_on_submit=score)
    publication.root.rename(tmp_path / "original-inputs-offline")
    restored = EvaluationPlan.from_bytes((root / "plan.json").read_bytes(), reader)
    assert restored.to_bytes() == expected.to_bytes()
    assert restored.score_on_submit is score


@pytest.mark.parametrize("missing", ["task", "reference"])
def test_missing_inputs_never_publish_a_smaller_plan(tmp_path, publication, configuration, missing, capsys):
    args = arguments(tmp_path, publication, configuration)
    if missing == "task":
        args += ["--task", "not-a-published-task"]
    else:
        dependency = publication.task.reference_behavior.files[1].content
        (publication.root / dependency.sha256).rename(publication.root / "unavailable-reference")
    assert main(args) == 1
    error = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert error["code"] == "evaluation-inputs-incomplete"
    assert "preparation.json" in error["message"]
    root = tmp_path / "prepared"
    assert not (root / "plan.json").exists()
    failures = [row for row in json.loads((root / "preparation.json").read_text())
                if row["status"] == "preparation_failed"]
    assert len(failures) == 1 and "score" not in failures[0]


def test_derived_input_publication_failure_cannot_commit_plan(tmp_path, publication, configuration, monkeypatch):
    from behave.core.storage import DirectoryStore
    from behave.task_execution.judge_policy import JudgePolicy
    from behave.task_execution.sim_tool import ComparisonToolInputs

    policy = JudgePolicy("fixture-command", "fixture-authority", search_seconds=0, max_retries=0)
    publication.task = replace(publication.task, judge_policy=publication.put(policy.to_bytes()))
    publication.release, publication.ref = publication.publish()
    task = load_published_task(publication.reader, publication.ref, publication.task.task_id)
    derived = ComparisonToolInputs.baseline(task.judge).judge_policy
    configuration["setting"] = "joint_sim"
    original = DirectoryStore.put

    def interrupted(store, data):
        if data == derived:
            raise OSError("fixture derived input publication failed")
        return original(store, data)

    monkeypatch.setattr(DirectoryStore, "put", interrupted)
    root = tmp_path / "prepared"
    with pytest.raises(OSError, match="derived input publication"):
        prepare(configuration=configuration, task_objects=publication.root, release=publication.ref,
                tasks=[publication.task.task_id], output=root)
    assert (root / "settings.json").exists()
    assert not (root / "plan.json").exists() and not (root / "run").exists()


def test_existing_output_and_duplicate_tasks_are_not_silently_replaced(tmp_path, publication, configuration):
    inputs = {"configuration": configuration, "task_objects": publication.root, "release": publication.ref,
              "tasks": [publication.task.task_id], "output": tmp_path / "prepared"}
    with pytest.raises(RecordError, match="duplicate"):
        prepare(**{**inputs, "tasks": inputs["tasks"] * 2})
    assert not inputs["output"].exists()
    prepare(**inputs)
    before = (inputs["output"] / "plan.json").read_bytes()
    with pytest.raises(FileExistsError):
        prepare(**inputs)
    assert (inputs["output"] / "plan.json").read_bytes() == before


def test_unknown_settings_cannot_be_ignored(tmp_path, publication, configuration):
    configuration["model_wokers"] = 80
    with pytest.raises(RecordError, match="model_wokers"):
        prepare(configuration=configuration, task_objects=publication.root, release=publication.ref,
                tasks=[publication.task.task_id], output=tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("entry", ["prepare", "campaign"])
@pytest.mark.parametrize("field,value,message", [
    ("workers", 0, "workers"),
    ("workers", True, "workers"),
    ("model_workers", 0, "concurrency"),
    ("judge_workers", False, "concurrency"),
    ("score_on_submit", "false", "score_on_submit"),
    ("output_preview_bytes", 0, "output_preview_bytes"),
    ("output_preview_bytes", True, "output_preview_bytes"),
    ("attempt.max_turns", 0, "max_turns"),
    ("attempt.max_retries", -1, "max_retries"),
    ("attempt.context_tokens", False, "context_tokens"),
    ("attempt.request_timeout_seconds", 0, "request_timeout_seconds"),
    ("attempt.typo", 1, "typo"),
    ("setting", "unknown", "solver setting"),
    ("endpoint.parameters.temperature", -1, "temperature"),
    ("endpoint.parameters.min_p", [], "min_p"),
    ("endpoint.parameters.repetition_penalty", "1.0", "repetition_penalty"),
    ("endpoint.parameters.presence_penalty", False, "presence_penalty"),
    ("endpoint.parameters.frequency_penalty", {}, "frequency_penalty"),
])
def test_invalid_settings_fail_before_preparing_inputs(
        tmp_path, publication, configuration, monkeypatch, entry, field, value, message):
    from behave.eval import generation

    row = configuration
    names = field.split(".")
    for name in names[:-1]:
        row = row[name]
    row[names[-1]] = value
    output = tmp_path / "prepared"
    before = json.loads(json.dumps(configuration))
    for target in ("behave.eval.generation.load_tasks",
                   "behave.sim.api.prepare_runtime", "behave.core.process_limits.configure_worker_process_limit"):
        monkeypatch.setattr(target, lambda *a, **kw: pytest.fail("invalid settings must not prepare inputs or workers"))
    if entry == "campaign":
        output.mkdir()
        settings = {**configuration, "task_objects": str(publication.root), "release": publication.ref.as_dict(),
                    "tasks": [publication.task.task_id]}
        (output / "settings.json").write_text(json.dumps(settings))
        before_bytes = (output / "settings.json").read_bytes()
        with pytest.raises((ValueError, TypeError), match=message):
            generation.run(output, status_output=output)
        assert list(output.iterdir()) == [output / "settings.json"]
        assert (output / "settings.json").read_bytes() == before_bytes
    else:
        with pytest.raises((ValueError, TypeError), match=message):
            prepare(configuration=configuration, task_objects=publication.root, release=publication.ref,
                    tasks=[publication.task.task_id], output=output)
        assert not output.exists()
    assert configuration == before


@pytest.mark.parametrize("phase", ["preflight", "generation", "scoring"])
def test_eval_cli_preserves_actionable_error_message(
        tmp_path, publication, configuration, monkeypatch, capsys, phase):
    def fail(*a, **kw):
        raise RecordError("fixture-failure", "specific failure that the operator can act on")

    assert main(arguments(tmp_path, publication, configuration)) == 0
    capsys.readouterr()
    root = tmp_path / "prepared"
    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", lambda: None)
    if phase == "preflight":
        monkeypatch.setattr(EvaluationPlan, "from_bytes", fail)
    else:
        owner = "behave.task_execution.rejudge.rejudge_batch" if phase == "scoring" else "behave.eval.runner.run_evaluation"
        monkeypatch.setattr(owner, fail)
    args = ["--task-objects", str(root / "inputs"), "--run-directory", str(root / "run")]
    args += ["--score", "fixture"] if phase == "scoring" else ["--plan", str(root / "plan.json")]
    assert main(args) == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "unavailable", "code": "fixture-failure", "message": "specific failure that the operator can act on"}


@pytest.mark.parametrize("startup_args,expected", [([], None), (["--isolation-startup-slots", "4"], 4)])
def test_prepared_plan_uses_existing_rollout_score_and_resume(
        tmp_path, publication, configuration, local_rollout, monkeypatch, capsys, startup_args, expected):  # noqa: F811
    from behave.eval.runner import run_evaluation
    from behave.isolation.startup import startup_slots
    from behave.task_execution.comparison import COMPARISON_BACKEND
    from behave.task_execution.judge_policy import JudgePolicy
    from behave.task_execution.judgment import ComponentJudgment

    policy = JudgePolicy("fixture-command", "fixture-authority", search_seconds=0, max_retries=0)
    publication.task = replace(publication.task, judge_policy=publication.put(policy.to_bytes()))
    publication.release, publication.ref = publication.publish()
    # This trusted fixture executes the solver in-process, without a provider
    # relay ledger. Match its execution mode, not the production admission plan.
    # The unmodified production plan and its identity are checked above.
    configuration["setting"] = "rtl_only"
    with monkeypatch.context() as preparation:
        preparation.setattr("behave.eval.plan.EvaluationPlan",
                            lambda *a, **kw: replace(EvaluationPlan(*a, **kw), model_admission=None))
        assert main(arguments(tmp_path, publication, configuration)) == 0
    root = tmp_path / "prepared"
    monkeypatch.setenv("BEHAVE_ISOLATION_STARTUP_SLOTS", "1")
    generate = [*startup_args, "--plan", str(root / "plan.json"), "--task-objects", str(root / "inputs"),
                "--run-directory", str(root / "run")]
    monkeypatch.setattr("behave.core.process_limits.configure_worker_process_limit", lambda: None)
    def generate_with_policy(**kwargs):
        assert startup_slots() == expected
        return run_evaluation(**kwargs)

    monkeypatch.setattr("behave.eval.runner.run_evaluation", generate_with_policy)
    assert main(generate) == 0
    assert startup_slots() == 1  # Restore the caller's policy, not the command's override.
    assert len(local_rollout) == 1
    assert main(generate) == 0 and len(local_rollout) == 1  # No repeated model call.
    assert startup_slots() == 1
    judgments = []

    class Judge:
        identity = COMPARISON_BACKEND

        def evaluate(self, request, objects):
            assert startup_slots() == expected
            judgments.append(request)
            return ComponentJudgment(1, "fixture-matched")

    monkeypatch.setattr("behave.task_execution.simulator_judge.SimulatorJudge", Judge)
    score = [*startup_args, "--score", "fixture", "--task-objects", str(root / "inputs"),
             "--run-directory", str(root / "run"), "--judge-workers", "1"]
    assert main(score) == 0
    assert startup_slots() == 1
    assert len(judgments) == 1 and len(local_rollout) == 1
    assert main(score) == 0
    assert startup_slots() == 1
    assert len(judgments) == 1 and len(local_rollout) == 1
    assert '"status":"unavailable"' not in capsys.readouterr().out
