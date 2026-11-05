"""Round control with a scripted diagnostic, no model service or optimizer.

The empty-source path uses real feedback/material/admission/paired-round records.
Nonempty construction and native-worker boundaries have their own test lanes.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from test_evaluation import plan_for
from test_improvement_feedback import report_at
from test_improvement_library import prepared as source_library
from test_rejudge import local_rollout  # noqa: F401
from test_slime_training_plan import inputs  # noqa: F401
from test_task_workflow import setup

from behave.agent.attempt import AttemptSpec
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.core.storage import DirectoryStore
from behave.data.published import load_published_task
from behave.data.release_records import ReleasePolicy
from behave.data.role import RoleJob, ScopeInputs
from behave.data.role_policy import ProcessRoles, RolePolicy
from behave.data.scope import ScopeConfiguration, ScopePlan
from behave.data.supervisor import ConstructionConfiguration
from behave.data.synthesis import SynthesisConfiguration
from behave.data.synthesis_records import SourceReviewPlan, SynthesisPlan
from behave.improve import round as flow
from behave.improve.learning_round import LearningParent, LearningRuntime
from behave.improve.library import import_library
from behave.improve.round import RoundRuntime, run_round
from behave.improve.round_evidence import load_round
from behave.train.plan import TrainingManifest
from behave.train.training_data import RawEncoding


def prepared(tmp_path, monkeypatch, publication, inputs):
    job, _ = setup(tmp_path / "diagnosis-config", publication)
    root, objects, catalog, _ = source_library(tmp_path / "library")
    library = import_library(root, catalog, objects)
    for path in publication.root.iterdir():
        objects.put(path.read_bytes())
    # The cumulative learner also consumes the seed. Reuse the training fixture's
    # valid evaluator policy, not FixturePublication's intentionally opaque one.
    configured_release = BlobRef.from_dict(parse_json((inputs[1] / "release.json").read_bytes()))
    job = replace(job, task=load_published_task(objects, configured_release, job.task.task_id).public)
    checkpoint = objects.put(b"initial fixture checkpoint")
    policy = {**dict(job.attempt.model_policy), "checkpoint": checkpoint.as_dict()}
    role = RolePolicy(job.model)
    attempt = lambda name: AttemptSpec(name, name, policy)
    guide = objects.put(b"Preserve source functionality. Fixture review policy.")
    judge = objects.put(b"{}")
    manifest = objects.put(TrainingManifest(job.task.release, ((job.task.task_id, job.task.revision),)).to_bytes())
    scope = ScopePlan("scope", RoleJob(ScopeInputs(BlobRef.of(b"unbound source"), guide),
        attempt("scope_author"), job.model), attempt("scope_reviewer"), role)
    synthesis = SynthesisPlan("task", "task", BlobRef.of(b"unbound scope"), judge,
        attempt("task_author"), attempt("spec_reader"), source_review=SourceReviewPlan(guide, attempt("source_reviewer")),
        processes=ProcessRoles(role, role, role))
    construction = ConstructionConfiguration("build", SynthesisConfiguration(synthesis), ScopeConfiguration(scope))
    release = objects.put(ReleasePolicy("fixture", "acquired-training", guide, judge, policy, False).to_bytes())
    config, _, _, _ = inputs
    tokenizer = objects.put((Path(config["model_path"]) / "tokenizer.json").read_bytes())
    learning = LearningRuntime(config, 4, {"PATH": "/usr/bin:/bin"}, None, 5)
    runtime = RoundRuntime(job.model, policy, plan_for(job), (), construction, 1, release, (), 0, 0,
        RawEncoding(tokenizer, "tokenizers/fixture", 1, 2, 32), learning, learning)
    calls = []
    def diagnose(**kwargs):
        calls.append("diagnosis")
        _, report = report_at(kwargs["directory"], publication, job, ("fault",), plan=kwargs["plan"])
        return report
    monkeypatch.setattr(flow, "run_batch", diagnose)
    def release_model():
        calls.append("release")
        return {"cleanup_complete": True, "owner": "scripted runtime, no model was launched"}
    return {"round_id": "round-1", "base_checkpoint": checkpoint, "library": library,
        "training_manifest": manifest, "parents": {route: LearningParent(None, tmp_path / "initial")
            for route in ("task", "raw")}, "runtime": runtime, "reader": objects,
        "run": JournalDirectory(tmp_path / "round"), "release_model": release_model}, calls


def test_empty_source_round_completes_and_cold_replay_has_no_side_effects(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    result = run_round(**options)
    assert result["status"] == "completed" and calls == ["diagnosis", "release"]
    assert all(parent["checkpoint"] is None for parent in result["parents"].values())
    pool = TrainingManifest.from_bytes(options["run"].objects.read(BlobRef.from_dict(result["training_manifest"])))
    seed = TrainingManifest.from_bytes(options["reader"].read(options["training_manifest"]))
    assert pool.tasks == seed.tasks
    assert run_round(**options) == result and calls == ["diagnosis", "release"]
    head, closure = options["run"].seal("round")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(options["run"].objects, closure)
    assert load_round(exported, head) == result
    with pytest.raises(RecordError, match="replay differs"):
        run_round(**{**options, "runtime": replace(options["runtime"], proposals_per_source=2)})


@pytest.mark.parametrize("interrupted", [False, True])
def test_round_input_transfer_preserves_commit_and_replay(tmp_path, monkeypatch, publication, inputs, interrupted):
    from types import SimpleNamespace

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    source = options["reader"]
    run_round(**options)
    expected = options["run"].journal("round").load()[0]
    transferred = []
    reads = []

    def read(ref):
        reads.append(ref)
        return source.read(ref)

    def transfer(destination, refs):
        before = tuple(reads)
        destination.publish_owned(source, refs)
        assert tuple(reads) == before
        transferred.append(refs)
        if interrupted and len(transferred) == 1:
            raise OSError("interrupted input publication")

    candidate = {**options, "run": JournalDirectory(tmp_path / "owned-round"),
                 "reader": SimpleNamespace(read=read), "publish_feedback": transfer}
    if interrupted:
        with pytest.raises(OSError, match="interrupted input publication"):
            run_round(**candidate)
        assert candidate["run"].journal("round").load() == ()
        assert calls == ["diagnosis", "release"]
    result = run_round(**candidate)
    actual = candidate["run"].journal("round").load()[0]
    assert actual == expected
    assert transferred[0] == expected.artifacts[1:]
    calls_before = tuple(calls)
    transfers_before = tuple(transferred)
    assert run_round(**candidate) == result
    assert tuple(calls) == calls_before and tuple(transferred) == transfers_before
    head, closure = candidate["run"].seal("round")
    exported = DirectoryStore(tmp_path / "owned-export")
    exported.publish(candidate["run"].objects, closure)
    assert load_round(exported, head) == result


def test_round_seed_can_use_the_shared_training_preparation(tmp_path, monkeypatch, publication, inputs):
    from behave.train.slime_data import prepare_task_plan

    options, _ = prepared(tmp_path, monkeypatch, publication, inputs)
    manifest = TrainingManifest.from_bytes(options["reader"].read(options["training_manifest"]))
    binding = prepare_task_plan(tmp_path / "seed-plan", options["runtime"].task.configuration,
                                options["reader"], manifest)
    assert binding["manifest"] == options["training_manifest"].as_dict()


def test_resume_after_material_boundary_does_not_reexport_feedback(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    options["runtime"] = replace(options["runtime"], generation_only=True)
    construct = flow._construct_and_learn

    def interrupt(**kwargs):
        raise InterruptedError("fixture construction handoff")

    monkeypatch.setattr(flow, "_construct_and_learn", interrupt)
    with pytest.raises(InterruptedError, match="construction handoff"):
        run_round(**options)
    prefix = options["run"].journal("round").load(verify_artifacts=False)
    assert prefix[-1].kind == "round_materials" and calls == ["diagnosis"]
    monkeypatch.setattr(flow, "_construct_and_learn", construct)
    monkeypatch.setattr(flow, "_acquire_materials",
                        lambda **kwargs: pytest.fail("resume reexported committed rollout evidence"))
    monkeypatch.setattr("behave.improve.materials.load_materials",
                        lambda *args: pytest.fail("resume repeated acquisition audit"))
    monkeypatch.setattr("behave.improve.materials.load_acquisition_report",
                        lambda *args: pytest.fail("handoff repeated Analyst audit"))
    result = run_round(**options)
    assert result["status"] == "completed" and calls == ["diagnosis", "release"]
    events = options["run"].journal("round").load(verify_artifacts=False)
    assert events[:len(prefix)] == prefix


def test_generation_only_round_cannot_fall_through_to_learning(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    options["runtime"] = replace(options["runtime"], generation_only=True, workers=2,
                                  acquired_updates=3, raw_updates=1)
    monkeypatch.setattr(flow, "learn_round", lambda **kw: pytest.fail("generation-only started learning"))
    result = run_round(**options)
    assert result["status"] == "completed" and result["learning"] is None
    assert result["parents"] == {name: parent.record() for name, parent in options["parents"].items()}
    assert calls == ["diagnosis", "release"]
    assert run_round(**options) == result
    assert load_round(options["run"].objects, options["run"].seal("round")[0]) == result


def test_generation_child_retains_serving_without_claiming_cleanup(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    options["runtime"] = replace(options["runtime"], generation_only=True, keep_serving=True)
    result = run_round(**options)
    assert calls == ["diagnosis"] and result["learning"] is None
    events = options["run"].journal("round").load(verify_artifacts=False)
    assert any(e.kind == "round_model_retained" for e in events)
    assert not any(e.kind == "round_model_released" for e in events)
    assert "training_manifest" not in result
    assert not any(e.kind == "round_training_pool" for e in events)
    assert load_round(options["run"].objects, options["run"].seal("round")[0]) == result
    with pytest.raises(ValueError, match="generation-only"):
        run_round(**{**options, "runtime": replace(options["runtime"], generation_only=False)})


def test_target_generation_borrows_engine_and_returns_pool_without_nested_training(
        tmp_path, monkeypatch, publication, inputs):
    from behave.improve import generation_batch

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    runtime = options["runtime"]
    options["runtime"] = replace(runtime, generation_only=True, keep_serving=True,
        construction=replace(runtime.construction, scope=None),
        generation_target={"admitted_tasks": 20, "proposal_tasks": 30, "max_batches": 2})
    assignments = []
    def empty_assignment(runtime, count, *args):
        assignments.append(count)
        return ()  # No sources in this controller fixture, not a successful model generation.
    monkeypatch.setattr(generation_batch, "assigned_acquisitions", empty_assignment)
    monkeypatch.setattr(flow, "learn_round", lambda **kw: pytest.fail("borrowed engine launched a trainer"))
    options["release_model"] = lambda: pytest.fail("borrower closed the training-owned engine")
    result = run_round(**options)
    assert assignments == [30, 30] and calls == ["diagnosis"]
    assert result["status"] == "completed" and result["learning"] is None
    assert result["generation"]["admitted"] == 0 and not result["generation"]["target_met"]
    pool = TrainingManifest.from_bytes(options["run"].objects.read(BlobRef.from_dict(result["training_manifest"])))
    seed = TrainingManifest.from_bytes(options["reader"].read(options["training_manifest"]))
    assert pool.tasks == seed.tasks
    assert run_round(**options) == result and assignments == [30, 30]
    events = options["run"].journal("round").load(verify_artifacts=False)
    assert not any(e.kind == "round_model_released" for e in events)
    assert load_round(options["run"].objects, options["run"].seal("round")[0]) == result


def test_resume_after_feedback_boundary_does_not_reimport_old_rollouts(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    runner = flow.run_stage_tasks
    def interrupted(*args, **kwargs):
        raise InterruptedError("saved feedback")
    monkeypatch.setattr(flow, "run_stage_tasks", interrupted)
    with pytest.raises(InterruptedError, match="saved feedback"):
        run_round(**options)
    assert options["run"].journal("round").load(verify_artifacts=False)[-1].kind == "round_feedback"
    monkeypatch.setattr(flow, "run_stage_tasks", runner)
    monkeypatch.setattr(flow, "export_feedback", lambda *args, **kwargs: pytest.fail("reimported old rollouts"))
    result = run_round(**options)
    assert calls == ["diagnosis", "release"]
    assert load_round(options["run"].objects, options["run"].seal("round")[0]) == result


def test_model_is_released_before_cpu_only_publication(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    original = flow.prepare_round_data

    def publish(**kwargs):
        assert calls == ["diagnosis", "release"]
        return original(**kwargs)

    monkeypatch.setattr(flow, "prepare_round_data", publish)
    result = run_round(**options)
    assert calls == ["diagnosis", "release"]
    assert load_round(options["run"].objects, options["run"].seal("round")[0]) == result


@pytest.mark.parametrize("target", [{"admitted_tasks": 0, "max_batches": 1},
                                    {"admitted_tasks": 20, "max_batches": None},
                                    {"admitted_tasks": 20, "max_batches": -1}])
def test_target_requires_explicit_positive_runtime_budget(target):
    from behave.improve.generation_batch import target_settings

    with pytest.raises(ValueError, match="runtime integers"):
        target_settings(target)


def test_target_is_divided_across_analysts_and_replenishment_retains_context(
        tmp_path, monkeypatch, publication, inputs):
    from behave.improve.acquisition import AcquisitionConfiguration
    from behave.improve.acquisition_records import AcquisitionInputs
    from behave.improve.generation_batch import assigned_acquisitions

    options, _ = prepared(tmp_path, monkeypatch, publication, inputs)
    objects, runtime = options["reader"], options["runtime"]
    guide = objects.put(b"Keep the approved function.")
    job = RoleJob(AcquisitionInputs(options["library"], guide, guide),
        AttemptSpec("acquire", "source_acquirer", runtime.model_policy), runtime.model)
    runtime = replace(runtime, acquisitions=(AcquisitionConfiguration(job),) * 3)
    assigned = assigned_acquisitions(runtime, 20, 0, None, None, objects, objects)
    texts = [objects.read(a.job.inputs.guidance).decode() for a in assigned]
    assert [f"about {n} useful" in text for n, text in zip((7, 7, 6), texts)] == [True] * 3
    memory, history = objects.put(b"notes"), objects.put(b"history")
    refill = assigned_acquisitions(runtime, 1, 1, memory, history, objects, objects)
    assert len(refill) == 1
    assert refill[0].job.inputs.experience == memory
    assert refill[0].job.inputs.construction_history == history
    assert b"same training feedback" in objects.read(refill[0].job.inputs.guidance)


@pytest.mark.isolation
def test_target_empty_batches_keep_zero_admissions_and_stop_at_runtime_budget(
        tmp_path, monkeypatch, publication, inputs):
    from test_role_process import job_for

    from behave.improve.acquisition import AcquisitionConfiguration
    from behave.improve.acquisition_records import AcquisitionInputs

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    objects, runtime = options["reader"], options["runtime"]
    source = AcquisitionInputs(options["library"], objects.put(b"unbound"), objects.put(b"No useful sources."))
    role = job_for(source, [], guard='''
from behave.agent.model import ToolCall
reply = ModelReply("", (ToolCall("submit", "submit_materials", {
    "materials": [], "experience": {"construction": "", "solver": ""}}),))
sys.stdout.buffer.write(encode_model_outcome(request, reply))
raise SystemExit(0)
''')
    options["runtime"] = replace(runtime, model=role.model, generation_only=True,
        model_policy={**runtime.model_policy, "transport": role.model.identity},
        acquisitions=(AcquisitionConfiguration(role),), construction=replace(runtime.construction, scope=None),
        generation_target={"admitted_tasks": 20, "max_batches": 2})
    result = run_round(**options)
    assert calls == ["diagnosis", "release"]
    assert result["generation"]["admitted"] == result["generation"]["proposed"] == 0
    assert result["generation"]["stop_reason"] == "batch_budget_exhausted"
    assert len(result["generation"]["batches"]) == 2
    assert result["learning"] is None
    assert load_round(options["run"].objects, options["run"].seal("round")[0]) == result


def test_online_data_handoff_reuses_material_boundary_and_retains_cold_audit(
        tmp_path, monkeypatch, publication, inputs):
    from behave.improve.round_data import load_round_data

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    options["runtime"] = replace(options["runtime"], generation_only=True)
    with monkeypatch.context() as live:
        live.setattr("behave.improve.round_data.load_materials",
                     lambda *args: pytest.fail("online publication repeated acquisition audit"))
        result = run_round(**options)
    assert result["status"] == "completed" and calls == ["diagnosis", "release"]
    data = JournalDirectory(options["run"].directory / "data")
    head, closure = data.seal("round_data")
    exported = DirectoryStore(tmp_path / "data-export")
    exported.publish_owned(data.objects, closure)
    assert load_round_data(exported, head).head == head
    assert load_round(options["run"].objects, options["run"].seal("round")[0]) == result


def test_terminal_acquisition_non_submission_does_not_discard_other_results():
    failed = {"name": "acquisition:0", "outcome": {"status": "unfinished", "code": "acquisition-turn_limit"}}
    successful = {"name": "acquisition:1", "outcome": {"status": "completed", "materials": {"materials": []}}}
    rows = (failed, successful)
    completed, interrupted = flow._acquisition_outcomes(rows)
    assert completed == (successful,) and interrupted is None
    assert rows[0] == failed  # Failure remains an explicit stage outcome.
    assert flow._acquisition_outcomes((failed,)) == ((), None)
    stopped = {"name": "acquisition:2", "outcome": {"status": "interrupted"}}
    assert flow._acquisition_outcomes((*rows, stopped)) == ((successful,), stopped)


def test_terminal_construction_non_submission_does_not_block_other_tasks():
    pending, ready = BlobRef.of(b"pending package"), BlobRef.of(b"ready package")
    failed = {"status": "unfinished", "code": "author-turn_limit", "package": pending.as_dict()}
    successful = {"status": "review_ready", "package": ready.as_dict()}
    unresolved = {"status": "unfinished", "code": "builder-objective-unresolved", "package": None}
    packages, interrupted = flow._construction_outcomes((failed, successful, unresolved))
    assert packages == (pending, ready) and interrupted is None
    assert failed["status"] == "unfinished"  # Admission still sees the failure, not a relabelled success.
    stopped = {"status": "unfinished", "stage": {"name": "construction:active"}}
    assert flow._construction_outcomes((successful, stopped)) == ((ready,), stopped)


@pytest.mark.isolation
@pytest.mark.parametrize("mode", ["one_failed", "all_failed", "merge_failed"])
def test_terminal_acquisition_failure_keeps_successes_and_prior_memory(
        tmp_path, monkeypatch, publication, inputs, mode):
    from test_role_process import job_for

    from behave.improve.acquisition import AcquisitionConfiguration
    from behave.improve.acquisition_records import AcquisitionInputs
    from behave.improve.experience import experience_files, experience_snapshot, load_experience
    from behave.improve.materials import MaterialRequest, load_materials
    from behave.improve.round_data import load_round_data

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    objects, runtime = options["reader"], options["runtime"]
    files = experience_files({"construction": "Prior lesson.", "solver": "Prior lesson."})
    for file in files:
        objects.put(file.data)
    prior = objects.put(experience_snapshot(files).to_bytes())
    role_inputs = AcquisitionInputs(options["library"], objects.put(b"unbound feedback"),
                                   objects.put(b"worker-one"), experience=prior)
    role = job_for(role_inputs, [], guard=f'''
from behave.agent.model import ToolCall
text = str(request.messages)
mode = {mode!r}
failed = mode == "all_failed" or (mode == "one_failed" and "worker-one" in text) or (
    mode == "merge_failed" and "consolidates" in text)
note = "First lesson." if "worker-one" in text else "Second lesson."
reply = ModelReply("No selection.") if failed else ModelReply("", (ToolCall("submit", "submit_materials", {{
    "materials": [], "experience": {{"construction": note, "solver": note}}}}),))
sys.stdout.buffer.write(encode_model_outcome(request, reply))
raise SystemExit(0)
''')
    other = replace(role, inputs=replace(role_inputs, guidance=objects.put(b"worker-two")))
    policy = {**runtime.model_policy, "transport": role.model.identity}
    options["runtime"] = replace(runtime, model=role.model, model_policy=policy,
        acquisitions=(AcquisitionConfiguration(role), AcquisitionConfiguration(other)),
        workers=2, generation_only=True)
    monkeypatch.setattr(flow, "learn_round", lambda **kw: pytest.fail("generation started training"))
    with monkeypatch.context() as live:
        live.setattr("behave.improve.materials.load_acquisition_report",
                     lambda *args: pytest.fail("live coordinator repeated acquisition replay"))
        result = run_round(**options)
    assert result["status"] == "completed" and result["learning"] is None
    run = options["run"]
    expected = b"Second lesson." if mode == "one_failed" else b"Prior lesson."
    assert {file.data for file in load_experience(run.objects, BlobRef.from_dict(result["experience"]))} == {expected}
    data = load_round_data(run.objects, BlobRef.from_dict(result["data"]))
    material = load_materials(run.objects, data.materials)
    context = MaterialRequest.from_bytes(run.objects.read(material.request))
    assert len(context.acquisitions) == {"one_failed": 1, "all_failed": 0, "merge_failed": 2}[mode]
    stages = [event.payload for event in run.journal("round").load(verify_artifacts=False)
              if event.kind == "round_stage"]
    assert any(stage["outcome"]["status"] == "unfinished" for stage in stages
               if stage["name"].startswith("acquisition:") or stage["name"] == "experience")
    head, references = run.seal("round")
    exported = DirectoryStore(tmp_path / "failed-selection-export")
    exported.publish(run.objects, references)
    assert load_round(exported, head) == result
    assert run_round(**options) == result and calls == ["diagnosis", "release"]


def test_completed_round_returns_receipt_without_full_reaudit(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    result = run_round(**options)
    assert result["status"] == "completed"
    assert run_round(**options) == result and calls == ["diagnosis", "release"]
    head, _ = options["run"].seal("round")
    # Auditing remains available when explicitly requested.
    assert load_round(options["run"].objects, head) == result


@pytest.mark.parametrize("windowed", [False, True])
def test_round_reuses_training_feedback_without_diagnosis_or_rejudge(tmp_path, monkeypatch, publication, inputs, windowed):
    from behave.improve.feedback import FeedbackRequest

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    producer = options["runtime"].diagnosis
    store, report = report_at(tmp_path / "previous-training", publication,
        producer.jobs[0], ("fault",), plan=producer)
    for path in store.directory.iterdir():
        options["reader"].put(path.read_bytes())
    old_checkpoint = options["reader"].put(b"older rollout-generating checkpoint")
    request = FeedbackRequest("previous-training", old_checkpoint, options["training_manifest"],
        producer.jobs[0].model.identity, (report.head,))
    reference = options["reader"].put(request.to_bytes())
    if windowed:
        from behave.improve.feedback import FeedbackWindow
        request = FeedbackWindow("previous-training-window", request.training_manifest, ((1, reference),))
        reference = options["reader"].put(request.to_bytes())
    monkeypatch.setattr(flow, "run_batch", lambda **kw: pytest.fail("duplicate diagnostic rollout"))
    monkeypatch.setattr(flow, "rejudge_batch", lambda **kw: pytest.fail("duplicate scoring"))
    class InventoryOnlyReader(flow.PublishedReleaseReader):
        def load(self, task_id):
            pytest.fail("eager full training-library load")

    monkeypatch.setattr(flow, "PublishedReleaseReader", InventoryOnlyReader)
    result = run_round(**options, feedback_from=reference)
    assert result["status"] == "completed" and calls == ["release"]
    assert not (options["run"].directory / "diagnosis").exists()
    events = options["run"].journal("round").load(verify_artifacts=False)
    record = next(thaw_json(event.payload) for event in events if event.kind == "round_feedback")
    from behave.improve.feedback import read_feedback_request
    saved = read_feedback_request(options["run"].objects.read(BlobRef.from_dict(record["request"])))
    assert saved == request
    if not windowed:
        assert saved.checkpoint != options["base_checkpoint"]
    assert run_round(**options, feedback_from=reference) == result
    assert calls == ["release"]
    head, closure = options["run"].seal("round")
    exported = DirectoryStore(tmp_path / "imported-export")
    exported.publish(options["run"].objects, closure)
    assert load_round(exported, head) == result


def test_imported_feedback_does_not_bypass_training_membership(tmp_path, monkeypatch, publication, inputs):
    from behave.improve.feedback import FeedbackRequest

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    request = FeedbackRequest("other-training", options["base_checkpoint"],
        BlobRef.of(b"another task pool"), options["runtime"].model.identity, (BlobRef.of(b"unused report"),))
    reference = options["reader"].put(request.to_bytes())
    with pytest.raises(RecordError, match="operator-approved"):
        run_round(**options, feedback_from=reference)
    assert not calls


@pytest.mark.isolation
@pytest.mark.parametrize("memory_first", [False, True])
def test_builder_and_memory_overlap_and_recover_committed_children(
        tmp_path, monkeypatch, publication, inputs, memory_first):
    from threading import Event

    from test_role_process import job_for

    from behave.improve.acquisition import AcquisitionConfiguration
    from behave.improve.acquisition_records import AcquisitionInputs
    from behave.improve.library import LibraryIndex

    options, _ = prepared(tmp_path, monkeypatch, publication, inputs)
    objects, runtime = options["reader"], options["runtime"]
    job = job_for(AcquisitionInputs(options["library"], objects.put(b"unbound"), objects.put(b"worker-one")), [], guard='''
from behave.agent.model import ToolCall
note = "One lesson." if "worker-one" in str(request.messages) else "Another lesson."
reply = ModelReply("", (ToolCall("submit", "submit_materials", {"materials": [],
    "experience": {"construction": note, "solver": note}}),))
sys.stdout.buffer.write(encode_model_outcome(request, reply))
raise SystemExit(0)
''')
    other = replace(job, inputs=replace(job.inputs, guidance=objects.put(b"two")))
    options["runtime"] = replace(runtime, model=job.model,
        model_policy={**runtime.model_policy, "transport": job.model.identity},
        acquisitions=(AcquisitionConfiguration(job), AcquisitionConfiguration(other)),
        construction=replace(runtime.construction, scope=None), workers=2, generation_only=True)
    identifier = next(iter(LibraryIndex(objects, options["library"]).units))
    monkeypatch.setattr("behave.improve.materials.proposal_briefs", lambda *args: {
        "fixture": canonical_json({"source_id": identifier, "objective": "preserve computation"})})
    original = flow.acquire_materials
    builder, merging, merged, memory_committed = Event(), Event(), Event(), Event()
    executed = []

    def acquire(**kwargs):
        if kwargs["configuration"].job.inputs.experience_proposals:
            merging.set()
            assert builder.wait(20), "memory blocked Builder dispatch"
            result = original(**kwargs)
            merged.set()
            return result
        return original(**kwargs)

    def construct(**kwargs):
        assert merging.wait(20), "Builder blocked memory dispatch"
        assert kwargs["plan"].initial.construction_experience is None
        builder.set()
        if memory_first:
            assert memory_committed.wait(20)
        executed.append("build")
        kwargs["run"].journal("construction").append([], "fixture", {})
        return {"status": "pending", "package": None}

    def stop_before_publication(**kwargs):
        assert builder.is_set() and merged.is_set()
        raise InterruptedError("fixture publication boundary")

    monkeypatch.setattr(flow, "acquire_materials", acquire)
    monkeypatch.setattr(flow, "construct", construct)
    monkeypatch.setattr(flow, "prepare_round_data", stop_before_publication)
    runner = flow.run_stage_tasks
    def stages(*args, **kwargs):
        callback = kwargs.get("on_stage", lambda row: None)
        def committed(row):
            callback(row)
            if row["name"] == "experience":
                memory_committed.set()
        return runner(*args, **{**kwargs, "on_stage": committed})
    monkeypatch.setattr(flow, "run_stage_tasks", stages)
    with pytest.raises(InterruptedError, match="publication boundary"):
        run_round(**options)
    events = options["run"].journal("round").load(verify_artifacts=False)
    boundary = next(e for e in events if e.kind == "round_materials")
    assert boundary.payload.get("experience_pending") is not None
    names = [e.payload["name"] for e in events[boundary.sequence + 1:] if e.kind == "round_stage"]
    assert names == (["experience", "construction:round-1-fixture"] if memory_first else
                     ["construction:round-1-fixture", "experience"])
    # A cold coordinator must consume either completion order without calling
    # any model again, even though publication has not completed yet.
    monkeypatch.setattr(flow, "acquire_materials", lambda **kw: pytest.fail("repeated Analyst or memory"))
    monkeypatch.setattr(flow, "construct", lambda **kw: pytest.fail("repeated Builder"))
    monkeypatch.setattr(flow, "export_feedback", lambda *a, **kw: pytest.fail("reexported feedback"))
    with pytest.raises(InterruptedError, match="publication boundary"):
        run_round(**options)
    assert executed == ["build"]


def test_unreleased_model_cannot_overlap_native_learning(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    options["release_model"] = lambda: {"cleanup_complete": False}
    learn = flow.learn_round
    monkeypatch.setattr(flow, "learn_round", lambda **kwargs: pytest.fail("learning before model release"))
    result = run_round(**options)
    assert result["status"] == "unfinished" and result["stage"] == "model-release"
    with pytest.raises(RecordError, match="not complete"):
        load_round(options["run"].objects, options["run"].seal("round")[0])
    options["release_model"] = lambda: {"cleanup_complete": True}
    monkeypatch.setattr(flow, "learn_round", learn)
    assert run_round(**options)["status"] == "completed"
    assert calls == ["diagnosis"]


def test_round_adopts_committed_child_after_parent_disconnection(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    original = flow.prepare_round_data
    def disconnect(**kwargs):
        original(**kwargs)
        raise InterruptedError("fixture parent disconnected after data publication")
    monkeypatch.setattr(flow, "prepare_round_data", disconnect)
    with pytest.raises(InterruptedError):
        run_round(**options)
    data_head, _ = JournalDirectory(options["run"].directory / "data").seal("round_data")
    monkeypatch.setattr(flow, "prepare_round_data", original)
    result = run_round(**options)
    assert result["data"] == data_head.as_dict()
    # The first owner released before CPU publication; the recovery owner must
    # confirm its own services are stopped without rerunning any model work.
    assert calls == ["diagnosis", "release", "release"]


def test_round_reuses_committed_pool_after_interruption(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    learn = flow.learn_round
    monkeypatch.setattr(flow, "learn_round", lambda **kw: (_ for _ in ()).throw(InterruptedError("after pool")))
    with pytest.raises(InterruptedError, match="after pool"):
        run_round(**options)
    events = options["run"].journal("round").load(verify_artifacts=False)
    assert events[-1].kind == "round_training_pool"
    pool_ref = events[-1].payload["manifest"]
    monkeypatch.setattr(flow, "learn_round", learn)
    monkeypatch.setattr(flow, "retain_training_manifest", lambda **kw: pytest.fail("re-imported committed pool"))
    result = run_round(**options)
    assert result["training_manifest"] == pool_ref
    assert calls == ["diagnosis", "release", "release"]
    assert load_round(options["run"].objects, options["run"].seal("round")[0]) == result


def test_old_cleanup_receipt_cannot_authorize_learning_with_live_recovery_services(tmp_path, monkeypatch, publication, inputs):
    options, _ = prepared(tmp_path, monkeypatch, publication, inputs)
    monkeypatch.setattr(flow, "learn_round", lambda **kw: (_ for _ in ()).throw(InterruptedError("after pool")))
    with pytest.raises(InterruptedError, match="after pool"):
        run_round(**options)
    options["release_model"] = lambda: {"cleanup_complete": False, "owner": "new recovery"}
    monkeypatch.setattr(flow, "learn_round", lambda **kw: pytest.fail("learning overlapped a live recovery service"))
    result = run_round(**options)
    assert result["status"] == "unfinished" and result["stage"] == "model-release"
    assert result["release"]["owner"] == "new recovery"
    assert options["run"].journal("round").load(verify_artifacts=False)[-1].kind == "round_training_pool"


def test_cumulative_pool_is_the_shared_training_pass_even_without_new_admissions(
        tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    options["runtime"] = replace(options["runtime"], acquired_updates=3)
    captured = []
    def learning(**kwargs):
        captured.append(kwargs)
        raise InterruptedError("fixture stops before native update")
    monkeypatch.setattr(flow, "learn_round", learning)
    with pytest.raises(InterruptedError, match="fixture stops"):
        run_round(**options)
    assert calls == ["diagnosis", "release"] and len(captured) == 1
    handoff = captured[0]
    pool = TrainingManifest.from_bytes(handoff["reader"].read(handoff["task_passes"][0][0]))
    seed = TrainingManifest.from_bytes(options["reader"].read(options["training_manifest"]))
    assert pool.tasks == seed.tasks and handoff["task_passes"][0][1] == 3
    assert handoff["parents"] == options["parents"]
    assert handoff["task_runtime"] == options["runtime"].task
    assert "solver_experience" not in handoff["raw_runtime"].record()
    assert "solver_experience" not in handoff["task_runtime"].record()


def test_analyst_only_round_removes_legacy_builder_attachment(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    runtime = options["runtime"]
    note = options["reader"].put(b"Private cross-task Analyst memory.")
    options["runtime"] = replace(runtime, construction=replace(runtime.construction,
        synthesis=replace(runtime.construction.synthesis,
            plan=replace(runtime.construction.synthesis.plan, construction_experience=note))))
    original = flow._bind_construction
    observed = []
    def bind(configuration, *args):
        result = original(configuration, *args)
        observed.append(result.synthesis.plan.construction_experience)
        return result
    monkeypatch.setattr(flow, "_bind_construction", bind)
    result = run_round(**options)
    assert result["status"] == "completed" and observed == [None]
    events = options["run"].journal("round").load(verify_artifacts=False)
    request = parse_json(options["run"].objects.read(BlobRef.from_dict(thaw_json(events[0].payload)["request"])))
    assert request["runtime"]["experience_visibility"] == "analyst-only"
    assert "solver_experience" not in request["runtime"]["task"]
    assert load_round(options["run"].objects, options["run"].seal("round")[0]) == result


@pytest.mark.parametrize("change", ["cleanup", "feedback", "runtime", "pool"])
def test_round_export_rejects_changed_stage_evidence(tmp_path, monkeypatch, publication, inputs, change):
    options, _ = prepared(tmp_path, monkeypatch, publication, inputs)
    run_round(**options)
    run = options["run"]
    journal = run.journal("forged")
    rewritten = []
    for event in run.journal("round").load():
        row = thaw_json(event.payload)
        if change == "cleanup" and event.kind == "round_model_released":
            row["cleanup_complete"] = False
        elif change == "feedback" and event.kind == "round_feedback":
            row["feedback"] = options["base_checkpoint"].as_dict()
        elif change == "runtime" and event.kind == "round_requested":
            request = parse_json(run.objects.read(BlobRef.from_dict(row["request"])))
            request["runtime"]["raw_updates"] += 1
            row["request"] = run.objects.put(canonical_json(request)).as_dict()
        elif change == "pool" and event.kind == "round_settled":
            row["training_manifest"] = options["training_manifest"].as_dict()
        rewritten.append(journal.append(rewritten, event.kind, row, event.artifacts))
    with pytest.raises(RecordError):
        load_round(run.objects, journal.seal()[0])


def test_wrong_served_checkpoint_is_rejected_before_dispatch(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    runtime = options["runtime"]
    options["runtime"] = replace(runtime, model_policy={**runtime.model_policy,
        "checkpoint": BlobRef.of(b"stale weights").as_dict()})
    with pytest.raises(ValueError, match="current Task checkpoint"):
        run_round(**options)
    assert not calls


def frozen_acquisition(options):
    run = options["run"]
    events = run.journal("round").load(verify_artifacts=False)
    boundary = next(event for event in events if event.kind == "round_materials")
    for event in events[:boundary.sequence + 1]:
        run.objects.put(event.to_bytes())
    return BlobRef.of(boundary.to_bytes())


def test_construction_retry_reuses_diagnosis_with_original_identity(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    run_round(**options)
    prior = options["run"]
    old_events = prior.journal("round").load(verify_artifacts=False)
    head = frozen_acquisition(options)
    runtime = options["runtime"]
    model = replace(runtime.model, revision="new-construction-snapshot")
    runtime = replace(runtime, model=model, model_policy={**runtime.model_policy, "transport": model.identity})
    monkeypatch.setattr(flow, "run_batch", lambda **kw: pytest.fail("repeated diagnosis"))
    monkeypatch.setattr(flow, "acquire_materials", lambda **kw: pytest.fail("repeated acquisition"))
    retry = {**options, "runtime": runtime, "reader": prior.objects,
             "run": JournalDirectory(tmp_path / "retry"), "acquisition_from": head}
    result = run_round(**retry)
    assert result["status"] == "completed" and calls == ["diagnosis", "release", "release"]
    assert prior.journal("round").load(verify_artifacts=False) == old_events
    assert not (retry["run"].directory / "diagnosis").exists()
    assert run_round(**retry) == result
    exported_head, closure = retry["run"].seal("round")
    exported = DirectoryStore(tmp_path / "retry-export")
    exported.publish(retry["run"].objects, closure)
    assert load_round(exported, exported_head) == result


def test_owned_acquisition_retry_keeps_payloads_without_rereading_them(
        tmp_path, monkeypatch, publication, inputs):
    options, _ = prepared(tmp_path, monkeypatch, publication, inputs)
    run_round(**options)
    prior = options["run"]
    payload = prior.objects.put(b"retained old token payload, not a new role input")
    selected = prior.journal("selected_acquisition")
    events = []
    for event in prior.journal("round").load(verify_artifacts=False):
        artifacts = (*event.artifacts, payload) if event.kind == "round_feedback" else event.artifacts
        events.append(selected.append(events, event.kind, thaw_json(event.payload), artifacts))
        if event.kind == "round_materials":
            break
    head, _ = selected.seal()

    class MetadataReader:
        def read(self, ref):
            assert ref != payload, "old token payload was reread"
            return prior.objects.read(ref)

    transfers = []
    def publish(target, references):
        transfers.extend(references)
        target.publish_owned(prior.objects, references)

    def reached_construction(**kwargs):
        raise InterruptedError("reached construction with retained material")

    monkeypatch.setattr(flow, "_construct_and_learn", reached_construction)
    class InventoryOnlyReader(flow.PublishedReleaseReader):
        def load(self, task_id):
            pytest.fail("new diagnosis inputs")

    monkeypatch.setattr(flow, "PublishedReleaseReader", InventoryOnlyReader)
    monkeypatch.setattr("behave.improve.materials.load_materials",
                        lambda *a, **kw: pytest.fail("repeated Analyst audit"))
    retry = {**options, "reader": MetadataReader(), "run": JournalDirectory(tmp_path / "retry"),
             "acquisition_from": head, "publish_feedback": publish}
    with pytest.raises(InterruptedError, match="reached construction"):
        run_round(**retry)
    assert payload in transfers
    assert retry["run"].objects.read(payload) == prior.objects.read(payload)
    assert prior.journal("selected_acquisition").load(verify_artifacts=False) == tuple(events)
    # Explicit audit still reads the complete payload closure by default.
    with pytest.raises(AssertionError, match="old token payload was reread"):
        flow.acquired_prefix(MetadataReader(), head)


@pytest.mark.parametrize("change", ["checkpoint", "library", "round_id", "training_manifest"])
def test_acquisition_retry_keeps_its_learning_context(tmp_path, monkeypatch, publication, inputs, change):
    options, _ = prepared(tmp_path, monkeypatch, publication, inputs)
    run_round(**options)
    head = frozen_acquisition(options)
    retry = {**options, "reader": options["run"].objects,
             "run": JournalDirectory(tmp_path / "retry"), "acquisition_from": head}
    if change == "checkpoint":
        ref = retry["reader"].put(b"different weights")
        retry["base_checkpoint"] = ref
        retry["runtime"] = replace(options["runtime"], model_policy={
            **options["runtime"].model_policy, "checkpoint": ref.as_dict()})
    else:
        retry[change] = "other round" if change == "round_id" else BlobRef.of(b"other inputs")
    with pytest.raises(RecordError, match="another learning context"):
        run_round(**retry)


def deferred_round(tmp_path, monkeypatch, publication, inputs, score):
    """Shared rollout and rejudge stages; only model replies and verdicts are fixtures."""
    from behave.task_execution.batch import run_batch
    from behave.task_execution.judgment import ComponentJudgment
    from behave.task_execution.rejudge import rejudge_batch

    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    runtime = options["runtime"]
    options["runtime"] = replace(runtime, diagnosis=replace(runtime.diagnosis, score_on_submit=False))
    requests = []

    class Judge:
        identity = runtime.diagnosis.judge_backend

        def evaluate(self, request, objects):
            requests.append(request)
            if request.component == "behavior":
                return ComponentJudgment(1, "fixture-match")
            return (ComponentJudgment(None, "fixture-unavailable", "verifier") if score is None else
                    ComponentJudgment(score, "fixture-match" if score else "fixture-mismatch"))

    def diagnose(**kwargs):
        calls.append("diagnosis")
        return run_batch(**kwargs)

    def scoring(**kwargs):
        calls.append("scoring")
        return rejudge_batch(**kwargs)

    exporter = flow.export_feedback

    def feedback(*args, **kwargs):
        bundle = exporter(*args, **kwargs)
        assert bundle.facts["counts"]["first_attempts"]["pending_judgment"] == 0
        calls.append("feedback")
        return bundle

    monkeypatch.setattr(flow, "run_batch", diagnose)
    monkeypatch.setattr(flow, "rejudge_batch", scoring)
    monkeypatch.setattr(flow, "SimulatorJudge", Judge)
    monkeypatch.setattr(flow, "export_feedback", feedback)
    return options, calls, requests


@pytest.mark.parametrize("score", [1, 0, None])
def test_deferred_diagnosis_scores_saved_submissions_before_feedback(
        tmp_path, monkeypatch, publication, inputs, local_rollout, score):
    from behave.task_execution.batch import load_batch_report
    from behave.task_execution.result import TaskResult

    options, calls, requests = deferred_round(tmp_path, monkeypatch, publication, inputs, score)
    result = run_round(**options)
    assert result["status"] == "completed"
    assert calls == ["diagnosis", "scoring", "feedback", "release"]
    assert len(local_rollout) == 1 and len(requests) == 2
    run = options["run"]
    stages = {event.payload["name"]: thaw_json(event.payload) for event in run.journal("round").load()
              if event.kind == "round_stage"}
    original = load_batch_report(run.objects, BlobRef.from_dict(stages["diagnosis"]["head"]))
    scored = load_batch_report(run.objects, BlobRef.from_dict(stages["diagnosis_scoring"]["head"]))
    assert original.summary["pending_judgment"] == 1
    assert scored.summary.get("pending_judgment", 0) == 0
    assert original.summary["billing"] == scored.summary["billing"]
    before, after = [TaskResult.from_bytes(run.objects.read(BlobRef.from_dict(
        thaw_json(report.summary["candidates"][0]["result"])))) for report in (original, scored)]
    assert (before.submission, before.solver_head) == (after.submission, after.solver_head)
    assert before.score is None and after.score == (None if score is None else (score + 1) / 2)
    assert after.judgment.rtl.score == score
    assert run_round(**options) == result
    assert len(local_rollout) == 1 and len(requests) == 2
    assert calls == ["diagnosis", "scoring", "feedback", "release"]
    head, closure = run.seal("round")
    exported = DirectoryStore(tmp_path / "export")
    exported.publish(run.objects, closure)
    assert load_round(exported, head) == result


def test_deferred_scoring_recovers_committed_report_without_reroll_or_rejudge(
        tmp_path, monkeypatch, publication, inputs, local_rollout):
    options, calls, requests = deferred_round(tmp_path, monkeypatch, publication, inputs, 1)
    scoring = flow.rejudge_batch

    def disconnect(**kwargs):
        scoring(**kwargs)
        raise InterruptedError("fixture disconnect after scoring commit")

    monkeypatch.setattr(flow, "rejudge_batch", disconnect)
    with pytest.raises(InterruptedError, match="after scoring commit"):
        run_round(**options)
    assert calls == ["diagnosis", "scoring"] and len(requests) == 2
    monkeypatch.setattr(flow, "rejudge_batch", scoring)
    assert run_round(**options)["status"] == "completed"
    assert len(local_rollout) == 1 and len(requests) == 2
    assert calls == ["diagnosis", "scoring", "scoring", "feedback", "release"]


def test_historical_feedback_round_remains_readable_and_resumable(tmp_path, monkeypatch, publication, inputs):
    options, calls = prepared(tmp_path, monkeypatch, publication, inputs)
    record, request = flow.Tape.record, flow.FeedbackRequest

    def legacy_record(self, kind, payload, *args, **kwargs):
        if kind == "round_feedback":
            payload = {key: value for key, value in payload.items() if key != "request"}
        return record(self, kind, payload, *args, **kwargs)

    def legacy_request(*args, **kwargs):
        return request(*args, **{**kwargs, "schema": "behave.train-feedback-request/1"})

    with monkeypatch.context() as old:
        old.setattr(flow.Tape, "record", legacy_record)
        old.setattr(flow, "FeedbackRequest", legacy_request)
        options["release_model"] = lambda: {"cleanup_complete": False}
        assert run_round(**options)["status"] == "unfinished"
    options["release_model"] = lambda: {"cleanup_complete": True}
    result = run_round(**options)
    assert result["status"] == "completed" and calls == ["diagnosis"]
    assert run_round(**options) == result and calls == ["diagnosis"]
