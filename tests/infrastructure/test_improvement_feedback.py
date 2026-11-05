"""Model-free feedback checks over genuine shared journals and report readers.

The in-process scripted solver and component verdicts are fixtures. No model
service, EDA tool, training process or claimed production score is involved.
"""

from dataclasses import replace

import pytest
from test_attempts import Scripted, call
from test_evaluation import plan_for
from test_task_workflow import setup

from behave.agent.model import ModelReply
from behave.agent.run_store import JournalDirectory
from behave.agent.tools import Tool, ToolResult, object_schema
from behave.agent.trajectory import trajectory_of
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, thaw_json
from behave.improve.feedback import FeedbackRequest, export_feedback, observation_id, render_brief
from behave.task_execution.accounting import PHASES, encode_accounting
from behave.task_execution.batch import load_batch_report, summarize
from behave.task_execution.judgment import ComponentJudgment, SubmissionJudgment
from behave.task_execution.result import TaskResult
from behave.task_execution.solver import run_solver
from behave.train.plan import TrainingManifest


def test_trusted_projection_matches_full_audit_without_replaying_accounting(tmp_path, publication, monkeypatch):
    from behave.improve import feedback as feedback_module

    _, store, _, request = inputs(tmp_path, publication, ("pass", "no_submission", "fault"))
    audited = export_feedback(request, store, approved_manifest=request.training_manifest)
    monkeypatch.setattr(feedback_module, "load_batch_report", lambda *a, **kw: pytest.fail("repeated producer audit"))
    recorded = export_feedback(request, store, approved_manifest=request.training_manifest, recorded_reports=True)
    assert recorded == audited
    with pytest.raises(RecordError, match="operator-approved"):
        export_feedback(request, store, approved_manifest=BlobRef.of(b"another manifest"), recorded_reports=True)


def test_recorded_native_recovery_keeps_transports_but_requires_identical_policy(tmp_path, publication):
    from behave.agent.providers.local_chat import LocalChatEndpoint
    from behave.agent.providers.native_tokens import NativeChatTokens
    from behave.improve.feedback import _same_native_policy_after_restart

    checkpoint = BlobRef.of(b'fixture same native weights')
    endpoint = LocalChatEndpoint('http://127.0.0.1:31000/v1/chat/completions', 'fixture-model',
        'fixture-runtime', parameters={'temperature': 1}, tokens=NativeChatTokens('sglang-slime',
            checkpoint, BlobRef.of(b'tokenizer'), BlobRef.of(b'template')))
    moved = replace(endpoint, url='http://127.0.0.1:32000/v1/chat/completions')
    command = endpoint.command(python='/tmp/old/bin/python', package='/tmp/old/package')
    other = moved.command(python='/tmp/new/bin/python', package='/tmp/new/package')
    assert _same_native_policy_after_restart(other, command, checkpoint)
    for bad in (replace(moved, parameters={'temperature': .7}).command(),
                replace(moved, tokens=replace(moved.tokens, checkpoint=BlobRef.of(b'other weights'))).command(),
                replace(moved, runtime_revision='other-runtime').command(),
                replace(other, argv=(other.argv[0], '-c', 'different broker'))):
        assert not _same_native_policy_after_restart(bad, command, checkpoint)
    assert not _same_native_policy_after_restart(other, None, checkpoint)

    job, _ = setup(tmp_path/'config', publication)
    jobs = tuple(replace(job, model=model, attempt=replace(job.attempt, attempt_id=f'mixed-{i}',
        model_policy={**thaw_json(job.attempt.model_policy), **profile.model_policy(command=model)}))
        for i, (profile, model) in enumerate(((endpoint, command), (moved, other))))
    plan = plan_for(*jobs)
    store, report = report_at(tmp_path/'batch', publication, jobs[0], ('pass', 'fault'), plan=plan)
    assert store.put(b'fixture same native weights') == checkpoint
    manifest = store.put(TrainingManifest(job.task.release, ((job.task.task_id, job.task.revision),)).to_bytes())
    request = FeedbackRequest('resumed-native', checkpoint, manifest, command.identity, (report.head,))
    facts = export_feedback(request, store, approved_manifest=manifest, recorded_reports=True).facts
    assert {c['model_transport'] for c in facts['contexts'].values()} == {command.identity, other.identity}
    assert [r['final_score'] for r in facts['observations']] == [1, .5]
    with pytest.raises(RecordError, match='model bindings'):
        export_feedback(request, store, approved_manifest=manifest)


def report_at(root, publication, job, states, *, plan=None):
    run = JournalDirectory(root)
    for path in publication.root.iterdir():
        run.objects.put(path.read_bytes())
    if plan is None:
        jobs = tuple(replace(job, attempt=replace(job.attempt, attempt_id=f"sample-{i}")) for i in range(len(states)))
        plan = plan_for(*jobs)
    jobs = plan.jobs
    journal = run.journal("report")
    plan_ref = run.objects.put(plan.to_bytes())
    inputs = tuple(run.objects.put(data) for job in jobs for data in job.input_blobs())
    events = [journal.append((), "evaluation_started", {"plan": plan_ref.as_dict()}, (plan_ref, *inputs))]
    rows = []
    for index, (job, state) in enumerate(zip(jobs, states, strict=True)):
        result = None
        artifacts = []
        if state not in {"unavailable", "unstarted", "cancelled"}:
            solver = run.journal(f"solver_{chr(97 + index)}")
            def write(workspace, arguments):
                workspace.write("candidate.py", b"CANDIDATE_CODE = 123\n")
                workspace.write("candidate.sv", b"// CANDIDATE_RTL\n")
                workspace.write("tb.py", b"# CANDIDATE_TB\n")
                workspace.write("private_scratch.txt", b"UNSELECTED_PRIVATE_CONTEXT")
                return ToolResult({"written": True})
            selection = lambda path, symbol: {"files": [path], "entry_file": path, "entry_symbol": symbol}
            replies = (ModelReply("stop"),) if state == "no_submission" else (
                call("fixture_write", {}), call("submit", {"behavior": selection("candidate.py", "Behavior"),
                    "rtl": selection("candidate.sv", "top"), "testbench": selection("tb.py", "test")}))
            outcome = run_solver(task=job.task, spec=job.attempt, backend=Scripted(*replies), journal=solver,
                command_tool=Tool("fixture_write", "fixture", object_schema({}), write, "fixture/1"),
                workspace_parent=root.parent)
            solver_head, solver_refs = solver.seal()
            supervision = run.journal(f"supervision_{chr(97 + index)}")
            job_ref = run.objects.put(job.to_bytes())
            supervision.append((), "solver_requested", {"job": job_ref.as_dict(),
                "journal": "fixture", "objects": "fixture", "model_admission": None, "provider": None,
                "development_admission": None, "development": None}, (job_ref,))
            supervision_head, supervision_refs = supervision.seal()
            trajectory = trajectory_of(solver.load(), run.objects)
            heads = {name: None for name in PHASES}
            heads.update(solver=solver_head.as_dict(), supervision=supervision_head.as_dict())
            accounting = run.objects.put(encode_accounting(attempt_id=job.attempt.attempt_id, heads=heads,
                trajectory=trajectory, billing=trajectory.billing, billing_source="solver"))
            judgment = None
            if state not in {"no_submission", "pending"}:
                rtl = ComponentJudgment(None, "fixture-memory", "resource-policy", retryable=True) if state == "partial" else (
                    ComponentJudgment(int(state == "pass"), "fixture-match", detail={"secret": "PRIVATE_REFERENCE_TRACE"}))
                behavior = ComponentJudgment(1, "fixture-match")
                if state == "judge_cancelled_rtl":
                    rtl = ComponentJudgment(None, "judge-worker-cancelled", "infrastructure")
                elif state == "judge_cancelled_behavior":
                    behavior = ComponentJudgment(None, "judge-worker-cancelled", "infrastructure")
                    rtl = ComponentJudgment(1, "fixture-match")
                judgment = SubmissionJudgment(outcome.submission, behavior,
                    rtl, run.objects.put(b"fixture judgment head"))
            result = TaskResult(job.task.task_id, job.task.release, job.task.revision, job.attempt.attempt_id,
                solver_head, outcome.attempt.reason, outcome.submission, judgment, None, None, accounting)
            artifacts.extend((*solver_refs, *supervision_refs, accounting, run.objects.put(result.to_bytes())))
        row = {"task_id": job.task.task_id, "attempt_id": job.attempt.attempt_id,
            "run_key": BlobRef.of(job.to_bytes()).sha256,
            "result": None if result is None else run.objects.put(result.to_bytes()).as_dict(),
            "score": None if result is None else result.score,
            "status": "no_result" if result is None else result.status,
            "error": {"type": "RecordError", "code": {
                "unstarted": "candidate-not-admitted", "cancelled": "solver-worker-cancelled"
            }.get(state, "fixture-unavailable")} if result is None else None,
            "testbench": None, "testbench_error": None}
        rows.append(row)
        events.append(journal.append(events, "evaluation_case", {"index": index, "row": row}, tuple(artifacts)))
    summary = run.objects.put(canonical_json(summarize(plan, rows, run.objects)))
    journal.append(events, "evaluation_finished", {"summary": summary.as_dict()}, (summary,))
    head, _ = journal.seal()
    return run.objects, load_batch_report(run.objects, head)


def inputs(tmp_path, publication, states):
    job, _ = setup(tmp_path / "config", publication)
    store, report = report_at(tmp_path / "batch", publication, job, states)
    membership = store.put(TrainingManifest(job.task.release, ((job.task.task_id, job.task.revision),)).to_bytes())
    checkpoint = store.put(b"fixture generating checkpoint")
    request = FeedbackRequest("round-0", checkpoint, membership, job.model.identity, (report.head,))
    return job, store, report, request


def test_feedback_keeps_partial_scores_and_all_denominators(tmp_path, publication):
    _, store, _, request = inputs(tmp_path, publication, ("pass", "fault", "partial", "no_submission", "pending", "unavailable"))
    assert FeedbackRequest.from_bytes(request.to_bytes()) == request
    bundle = export_feedback(request, store, approved_manifest=request.training_manifest)
    facts = bundle.facts
    rows = facts["observations"]
    counts = facts["counts"]["first_attempts"]
    assert [row["score"] for row in rows] == [1, .5, None, 0, None, None]
    assert [row["final_score"] for row in rows] == [1, .5, .5, 0, 0, 0]
    assert counts["mean_final_reward"] == 2 / 6
    assert counts["rtl"]["accuracy"] == 1 / 6
    assert counts["behavior"]["accuracy"] == 3 / 6
    assert [row["final_score"] for row in rows] == [1, .5, .5, 0, 0, 0]
    assert counts["mean_final_reward"] == 2 / 6
    assert counts["rtl"]["accuracy"] == 1 / 6
    assert counts["behavior"]["accuracy"] == 3 / 6
    assert facts["tasks"][0]["complete"]
    assert (counts["requested"], counts["scored"], counts["no_result"], counts["no_submission"],
            counts["pending_judgment"]) == (6, 3, 2, 1, 1)
    assert counts["rtl"]["passed"] == 1 and counts["behavior"]["passed"] == 3
    assert rows[2]["rtl"]["retryable"] and rows[2]["behavior"]["score"] == 1
    assert rows[-1]["failure_code"] == "fixture-unavailable"
    assert render_brief(facts) == next(file.data for file in bundle.files if file.reference.path == "brief.md")
    assert export_feedback(request, store, approved_manifest=request.training_manifest) == bundle


def test_feedback_export_has_candidate_files_but_no_reference_or_private_context(tmp_path, publication):
    _, store, _, request = inputs(tmp_path, publication, ("pass",))
    bundle = export_feedback(request, store, approved_manifest=request.training_manifest)
    content = b"\n".join(file.data for file in bundle.files)
    assert b"CANDIDATE_CODE" in content and b"CANDIDATE_RTL" in content and b"CANDIDATE_TB" in content
    for forbidden in (b"PRIVATE_BEHAVIOR", b"PRIVATE_HELPER", b"PRIVATE_PROVENANCE", b"PRIVATE_REFERENCE_TRACE",
                      b"UNSELECTED_PRIVATE_CONTEXT", b"PRIVATE_REFERENCE_RTL"):
        assert forbidden not in content
    allowed = {file.reference.path for file in bundle.files}
    assert set(bundle.facts["observations"][0]["attachments"]) <= allowed


def test_feedback_rejects_eval_membership_and_wrong_model(tmp_path, publication):
    job, store, _, request = inputs(tmp_path, publication, ("pass",))
    with pytest.raises(RecordError, match="operator-approved"):
        export_feedback(request, store, approved_manifest=BlobRef.of(b"other manifest"))
    forbidden = store.put(TrainingManifest(job.task.release, (("other-task", job.task.revision),)).to_bytes())
    with pytest.raises(RecordError, match="outside the training"):
        export_feedback(replace(request, training_manifest=forbidden), store, approved_manifest=forbidden)
    with pytest.raises(RecordError, match="model bindings"):
        export_feedback(replace(request, model_transport="wrong-checkpoint-transport"), store,
                        approved_manifest=request.training_manifest)


def test_rerun_is_explicit_not_best_of_or_an_independent_sample(tmp_path, publication):
    job, store, first, request = inputs(tmp_path, publication, ("fault",))
    second_store, second = report_at(tmp_path / "retry", publication, job, ("pass",))
    for path in second_store.directory.iterdir():
        store.put(path.read_bytes())
    request = replace(request, reports=(first.head, second.head))
    with pytest.raises(RecordError, match="explicit retry"):
        export_feedback(request, store, approved_manifest=request.training_manifest)
    request = replace(request, retries=((observation_id(second.head, 0), observation_id(first.head, 0)),))
    facts = export_feedback(request, store, approved_manifest=request.training_manifest).facts
    assert [row["score"] for row in facts["observations"]] == [.5, 1]
    assert facts["counts"]["first_attempts"]["rtl"]["passed"] == 0
    assert facts["counts"]["all_observations"]["rtl"]["passed"] == 1
    assert facts["counts"]["retries"] == 1
    group, = facts["tasks"]
    assert group["samples"] == 1 and len(group["retries"]) == 1
    assert group["rtl"]["passed"] == [] and not group["rtl"]["mixed_outcomes"]
    with pytest.raises(RecordError, match="same task and execution context"):
        export_feedback(replace(request, reports=tuple(reversed(request.reports))), store,
                        approved_manifest=request.training_manifest)


def test_task_sampling_groups_retain_success_failure_and_unresolved(tmp_path, publication):
    _, store, _, request = inputs(tmp_path, publication, ("pass", "fault", "partial", "no_submission"))
    facts = export_feedback(request, store, approved_manifest=request.training_manifest).facts
    group, = facts["tasks"]
    assert group["samples"] == 4 and not group["retries"]
    assert group["planned_samples"] == 4 and group["complete"]
    assert group["rtl"]["accuracy"] == 1 / 4
    assert group["behavior"]["accuracy"] == 3 / 4
    rtl = group["rtl"]
    assert rtl["mixed_outcomes"] and len(rtl["passed"]) == len(rtl["failed"]) == 1
    assert rtl["decided"] == 2 and rtl["pass_fraction_on_decided"] == 1 / 2
    assert len(rtl["unresolved"]) == 1
    assert group["status_counts"]["no_submission"] == 1
    assert not group["behavior"]["mixed_outcomes"]
    assert group["rtl"]["accuracy"] == 1 / 4
    assert group["behavior"]["accuracy"] == 3 / 4


def test_feedback_excludes_unstarted_and_cancelled_reservations(tmp_path, publication):
    _, store, _, request = inputs(tmp_path, publication, ("pass", "no_submission", "unstarted", "cancelled"))
    facts = export_feedback(request, store, approved_manifest=request.training_manifest).facts
    assert facts["counts"]["first_attempts"]["requested"] == 2
    assert facts["counts"]["first_attempts"]["mean_final_reward"] == .5
    assert len(facts["excluded_attempts"]) == 2
    group, = facts["tasks"]
    assert group["samples"] == 2 and group["planned_samples"] == 4 and not group["complete"]


@pytest.mark.parametrize("cancelled", ("judge_cancelled_rtl", "judge_cancelled_behavior"))
@pytest.mark.parametrize("recorded", (False, True))
def test_feedback_excludes_cancelled_scoring_but_preserves_settled_scores(tmp_path, publication, cancelled, recorded):
    _, store, report, request = inputs(tmp_path, publication, ("pass", "no_submission", "partial", cancelled))
    last = thaw_json(report.summary)["candidates"][-1]
    result = TaskResult.from_bytes(store.read(BlobRef.from_dict(last["result"])))
    assert result.failure_code is None and result.final_score == .5
    facts = export_feedback(request, store, approved_manifest=request.training_manifest,
                            recorded_reports=recorded).facts
    assert [row["final_score"] for row in facts["observations"]] == [1, 0, .5]
    assert facts["counts"]["first_attempts"]["requested"] == 3
    assert facts["counts"]["first_attempts"]["mean_final_reward"] == .5
    assert [row["reason"] for row in facts["excluded_attempts"]] == ["judge-worker-cancelled"]
    group, = facts["tasks"]
    assert group["samples"] == 3 and group["planned_samples"] == 4 and not group["complete"]
    assert store.read(BlobRef.from_dict(last["result"])) == result.to_bytes()


def test_task_sampling_never_pools_different_contexts_or_revisions():
    from behave.improve.task_feedback import task_groups

    row = {"id": "a", "task_id": "t", "revision": BlobRef.of(b"v1").as_dict(),
           "context": "runtime-1", "retry_of": None, "status": "scored",
           "rtl": {"status": "passed", "score": 1},
           "behavior": {"status": "not_observed", "score": None}}
    groups = task_groups([row, {**row, "id": "b", "context": "runtime-2"},
                          {**row, "id": "c", "revision": BlobRef.of(b"v2").as_dict()}])
    assert len(groups) == 3 and all(group["samples"] == 1 for group in groups)
    assert all(group["behavior"]["decided"] == 0 for group in groups)


@pytest.mark.parametrize("change", ["shell_access", "shell_budget", "logs", "comparison_budget", "reference_visibility"])
def test_feedback_separates_development_configurations(tmp_path, publication, change):
    from behave.data.published import load_published_task
    from behave.task_execution.judge_policy import JudgePolicy
    from behave.task_execution.sim_tool import ComparisonToolInputs

    job, store, _, request = inputs(tmp_path, publication, ("unavailable",))
    other = replace(job, attempt=replace(job.attempt, attempt_id="other"))
    if change == "shell_access":
        other = replace(other, command_limits=None)
    elif change == "shell_budget":
        other = replace(other, command_limits=replace(job.command_limits, cpu_seconds=job.command_limits.cpu_seconds + 1))
    elif change == "logs":
        other = replace(other, log_bytes=job.log_bytes + 1)
    else:
        task = load_published_task(store, job.task.release, job.task.task_id).judge
        comparison = ComparisonToolInputs(task.contract, task.run_policy, task.judge_policy,
            reference_behavior=task.reference_behavior if change == "reference_visibility" else None)
        setting = "rtl_sim" if change == "reference_visibility" else "joint_sim"
        job = replace(job, setting=setting, comparison=comparison)
        different = (replace(comparison, reference_visible=True) if change == "reference_visibility" else
            replace(comparison, judge_policy=replace(JudgePolicy.from_bytes(task.judge_policy), search_seconds=1).to_bytes()))
        other = replace(other, setting=setting, comparison=different)
    combined, report = report_at(tmp_path / "combined", publication, job, ("unavailable", "unavailable"),
                                plan=plan_for(job, other))
    for path in combined.directory.iterdir():
        store.put(path.read_bytes())
    request = replace(request, reports=(report.head,))
    bundle = export_feedback(request, store, approved_manifest=request.training_manifest)
    assert len(bundle.facts["contexts"]) == len(bundle.facts["tasks"]) == 2
    assert all(group["samples"] == 1 for group in bundle.facts["tasks"])
    assert b"PRIVATE_REFERENCE_RTL" not in b"\n".join(file.data for file in bundle.files)
    assert b"PRIVATE_BEHAVIOR_MUST_NOT_EXECUTE" not in b"\n".join(file.data for file in bundle.files)

    # Archived v1 requests retain their exact original grouping on replay.
    legacy = replace(request, schema="behave.train-feedback-request/1")
    assert FeedbackRequest.from_bytes(legacy.to_bytes()) == legacy
    assert FeedbackRequest.from_bytes(request.to_bytes()) == request
    old = export_feedback(legacy, store, approved_manifest=request.training_manifest).facts
    assert len(old["contexts"]) == 1
    assert "solver_policy" not in next(iter(old["contexts"].values()))


@pytest.mark.parametrize("change", ["workers", "verification"])
def test_feedback_separates_batch_resource_settings(tmp_path, publication, change):
    job, store, first, request = inputs(tmp_path, publication, ("unavailable",))
    other = replace(job, attempt=replace(job.attempt, attempt_id="other"))
    plan = plan_for(other)
    plan = (replace(plan, workers=plan.workers + 1) if change == "workers" else
            replace(plan, verification=replace(plan.verification, concurrency=plan.verification.concurrency + 1)))
    combined, report = report_at(tmp_path / "other-batch", publication, other, ("unavailable",), plan=plan)
    for path in combined.directory.iterdir():
        store.put(path.read_bytes())
    request = replace(request, reports=(first.head, report.head))
    facts = export_feedback(request, store, approved_manifest=request.training_manifest).facts
    assert len(facts["contexts"]) == len(facts["tasks"]) == 2
