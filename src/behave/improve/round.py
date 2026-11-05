"""One self-improvement round using the existing execution stages.

Model serving and budgets are runtime inputs. This controller does not allocate
GPUs, choose a model, resample failed candidates or decide optimizer settings.
Each child has one stable directory; a parent interruption reuses its receipt.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import Event

from behave.agent.admission import ResourcePool
from behave.agent.lifetime import AttemptControl
from behave.agent.process_model import ModelCommand
from behave.agent.prompting import render
from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.agent.workspace import WorkspaceSnapshot
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.data.drafts import ScopeProposal, scope_closure
from behave.data.published import PublishedReleaseReader, ReleaseManifest
from behave.data.release_records import ReleasePolicy, ReleaseRequest, ScopeReviewPolicy
from behave.data.scope import load_scope_preparation, prepare_scope
from behave.data.supervisor import ConstructionConfiguration, construct
from behave.task_execution.batch import run_batch
from behave.task_execution.batch_plan import BatchPlan
from behave.task_execution.cocotb_worker import CocotbBackend
from behave.task_execution.rejudge import judgment_run, rejudge_batch
from behave.task_execution.run_store import RunStore
from behave.task_execution.simulator_judge import SimulatorJudge
from behave.train.plan import TrainingManifest, validate_membership
from behave.train.trainer import NativeAnchor
from behave.train.training_data import RawEncoding

from .acquisition import AcquisitionConfiguration, acquire_materials
from .concurrent import run_stage_tasks
from .feedback import FeedbackRequest, export_feedback, feedback_reports, read_feedback_request
from .input_stores import ReadSet
from .learning_round import LearningParent, LearningRuntime, learn_round
from .library import LibraryIndex
from .materials import FrozenMaterials, MaterialRequest, freeze_materials
from .round_data import admitted_training_manifest, committed_round_data, prepare_round_data
from .round_evidence import acquired_prefix
from .training_pool import cumulative_training_manifest, retain_training_manifest


@dataclass(frozen=True, slots=True)
class RoundRuntime:
    model: ModelCommand
    model_policy: dict
    diagnosis: BatchPlan
    acquisitions: tuple[AcquisitionConfiguration, ...]
    construction: ConstructionConfiguration
    proposals_per_source: int | None
    release_policy: BlobRef
    replay_passes: tuple[tuple[BlobRef, int], ...]
    acquired_updates: int
    raw_updates: int
    encoding: RawEncoding
    task: LearningRuntime
    raw: LearningRuntime
    workers: int = defaults.IMPROVEMENT_WORKERS
    generation_only: bool = False
    generation_target: dict | None = None
    keep_serving: bool = False
    construction_target: int | None = None
    acquisition_limits: dict | None = None

    def __post_init__(self):
        if self.acquisitions:
            inputs = [config.job.inputs for config in self.acquisitions]
            if any(value.experience_mode not in {None, "delta-v1"} for value in inputs):
                raise ValueError("round acquisition templates must be Analysts, not memory merge jobs")
            if any(value.experience_mode is not None for value in inputs):
                first = inputs[0]
                if any((value.experience_mode, value.experience_policy, value.experience, value.pending_experience)
                       != (first.experience_mode, first.experience_policy, first.experience, first.pending_experience)
                       for value in inputs):
                    raise ValueError("parallel Analysts must share one memory version, backlog and policy")
        if self.acquisition_limits is not None:
            from .acquisition import workload_limits

            workload_limits(self.acquisition_limits)

    def record(self):
        return {"model": self.model.as_dict(), "model_policy": self.model_policy,
            "diagnosis": parse_json(self.diagnosis.to_bytes()),
            "acquisitions": [parse_json(config.to_bytes()) for config in self.acquisitions],
            "construction": parse_json(self.construction.to_bytes()),
            "proposals_per_source": self.proposals_per_source, "release_policy": self.release_policy.as_dict(),
            "replay_passes": [[ref.as_dict(), count] for ref, count in self.replay_passes],
            "acquired_updates": self.acquired_updates, "raw_updates": self.raw_updates,
            "encoding": self.encoding.as_dict(), "task": self.task.record(), "raw": self.raw.record(),
            "experience_visibility": "analyst-only",
            **({"workers": self.workers} if self.workers != defaults.IMPROVEMENT_WORKERS else {}),
            **({"generation_only": True} if self.generation_only else {}),
            **({"generation_target": self.generation_target} if self.generation_target is not None else {}),
            **({"construction_target": self.construction_target} if self.construction_target is not None else {}),
            **({"acquisition_limits": self.acquisition_limits} if self.acquisition_limits is not None else {}),
            **({"keep_serving": True} if self.keep_serving else {})}


def _bind_construction(configuration, model, policy):
    """Bind every role to the runtime's current Task model, keeping role budgets."""
    synthesis = configuration.synthesis.plan
    scope = None if configuration.scope is None else configuration.scope.plan
    if synthesis.source_review is None:
        raise ValueError("self-improvement construction requires its independent source review")
    attempt = lambda value: replace(value, model_policy=policy)
    role = lambda value: replace(value, model=model)
    processes = replace(synthesis.processes, author=role(synthesis.processes.author),
        reader=role(synthesis.processes.reader), source_review=role(synthesis.processes.source_review))
    synthesis = replace(synthesis, author=attempt(synthesis.author), reader=attempt(synthesis.reader),
        source_review=replace(synthesis.source_review, reviewer=attempt(synthesis.source_review.reviewer)),
        processes=processes, rtl=None)
    if scope is not None:
        scope = replace(scope, author=replace(scope.author, attempt=attempt(scope.author.attempt), model=model),
                        reviewer=attempt(scope.reviewer), reviewer_policy=role(scope.reviewer_policy))
    return replace(configuration, synthesis=replace(configuration.synthesis, plan=synthesis),
                   scope=None if scope is None else replace(configuration.scope, plan=scope))


def run_round(*, round_id: str, base_checkpoint: BlobRef, library: BlobRef, training_manifest: BlobRef,
              parents: dict[str, LearningParent], runtime: RoundRuntime, reader,
              run: JournalDirectory, release_model: Callable[[], dict], cancelled=lambda: False,
              acquisition_from: BlobRef | None = None, scope_parents: dict[str, BlobRef] | None = None,
              feedback_from: BlobRef | None = None, publish_feedback=None,
              construction_from: dict[str, BlobRef] | None = None):
    """Diagnosis -> acquisition -> construction -> common data -> paired learning.

    ``runtime.model`` is the caller-owned serving binding for the current Task
    checkpoint. It must remain on those weights until acquisition/construction
    finish. ``release_model`` is the runtime owner's idempotent close operation
    (for example its ServingSession close receipt). Learning cannot start before
    that operation confirms cleanup. No final-test data or scores enter this request.
    An explicit ``acquisition_from`` reuses a prior frozen-material boundary in a
    new run directory. It does not repeat diagnosis/acquisition or relabel their
    model identity. Construction and learning still use this runtime.
    Optional scope parents reuse proposal content, not approval: each goes
    through the ordinary scope author/reviewer again under this runtime.
    ``construction_from`` reuses explicitly named completed construction heads:
    accepted children remain unchanged, pending children resume at their saved
    repair/review boundary under the configured task revision budget.
    ``feedback_from`` names an existing single-update request or feedback window over completed training
    reports. Its generating checkpoint is preserved independently of this round's
    analysis/construction model. This path never reruns diagnosis or scoring.
    ``training_manifest`` is the current approved pool. The result returns its
    union with admitted tasks, ready for the existing training stage and the next
    round. Task revisions and checkpoint-owned cooling state are not reset.
    A target round with ``generation_only=True, keep_serving=True`` borrows the
    caller's current engine and returns that pool without closing the engine or
    invoking a nested trainer. The training owner controls weight refresh and
    its next update. Replenishment children leave pool composition to the parent.
    """
    # Persistent experience belongs to Analyst. Downstream roles receive the
    # task proposal/specification, not Analyst's cross-task notes. Clear legacy
    # template attachments as well as avoiding new automatic attachments.
    runtime = replace(runtime,
        construction=replace(runtime.construction, synthesis=replace(runtime.construction.synthesis,
            plan=replace(runtime.construction.synthesis.plan, construction_experience=None))))
    checkpoint = parents["task"].checkpoint or base_checkpoint
    if runtime.model_policy.get("transport") != runtime.model.identity:
        raise ValueError("runtime model policy must bind the supplied model command")
    if runtime.model_policy.get("checkpoint") != checkpoint.as_dict():
        raise ValueError("runtime serving binding must identify the current Task checkpoint")
    if runtime.proposals_per_source is not None and runtime.proposals_per_source < 0:
        raise ValueError("proposal count cannot be negative")
    if runtime.keep_serving and not runtime.generation_only:
        raise ValueError("only a generation-only child may retain its caller's serving engines")
    if runtime.generation_target is not None:
        from .generation_batch import run_target_round

        if construction_from:
            raise ValueError("saved constructions resume their selected single batch, not a new acquisition target")
        return run_target_round(round_id=round_id, base_checkpoint=base_checkpoint, library=library,
            training_manifest=training_manifest, parents=parents, runtime=runtime, reader=reader,
            run=run, release_model=release_model, cancelled=cancelled, feedback_from=feedback_from,
            acquisition_from=acquisition_from, scope_parents=scope_parents, publish_feedback=publish_feedback)
    request = {"round_id": round_id, "base_checkpoint": base_checkpoint.as_dict(),
        "library": library.as_dict(), "training_manifest": training_manifest.as_dict(),
        "parents": {role: parents[role].record() for role in ("task", "raw")}, "runtime": runtime.record()}
    if acquisition_from is not None:
        request["acquisition_from"] = acquisition_from.as_dict()
    if feedback_from is not None:
        if acquisition_from is not None:
            raise ValueError("select existing feedback or frozen acquisition, not both")
        request["feedback_from"] = feedback_from.as_dict()
    scope_parents = {} if scope_parents is None else scope_parents
    if scope_parents:
        request["scope_parents"] = {name: ref.as_dict() for name, ref in scope_parents.items()}
    construction_from = {} if construction_from is None else construction_from
    if construction_from:
        request["construction_from"] = {name: ref.as_dict() for name, ref in construction_from.items()}
    with run.owner():
        objects = run.objects
        tape = Tape(run.journal("round"))
        ref = objects.put(canonical_json(request))
        inputs = ()
        if not tape.events:
            tracked = ReadSet(reader)
            inherited = ()
            if acquisition_from is not None:
                prefix, prior = acquired_prefix(tracked, acquisition_from, verify_artifacts=False)
                prior_checkpoint = prior["parents"]["task"]["checkpoint"] or prior["base_checkpoint"]
                if (prior_checkpoint != checkpoint.as_dict() or any(prior[key] != request[key]
                        for key in ("round_id", "library", "training_manifest"))):
                    raise RecordError("round-acquisition-context", "reused acquisition belongs to another learning context")
                inherited = tuple(dict.fromkeys(ref for event in prefix for ref in event.artifacts))
            LibraryIndex(tracked, library)
            for item in (base_checkpoint, checkpoint, training_manifest, runtime.release_policy,
                         runtime.encoding.tokenizer,
                         runtime.construction.synthesis.plan.judge_policy,
                         runtime.construction.synthesis.plan.source_review.guidance):
                read_verified(tracked, item)
            if runtime.construction.scope is not None:
                read_verified(tracked, runtime.construction.scope.plan.author.inputs.guidance)
            for config in runtime.acquisitions:
                read_verified(tracked, config.job.inputs.guidance)
                if config.job.inputs.experience is not None:
                    from .experience import load_experience

                    load_experience(tracked, config.job.inputs.experience)
                if config.job.inputs.construction_history is not None:
                    from .construction import history_file

                    history_file(tracked, config.job.inputs.construction_history)
                if config.job.inputs.pending_experience:
                    from .experience import ExperiencePolicy, load_delta

                    policy = ExperiencePolicy.from_dict(config.job.inputs.experience_policy)
                    for ref in config.job.inputs.pending_experience:
                        load_delta(tracked, ref, policy.delta_bytes)
            for parent in scope_parents.values():
                scope_closure(tracked, parent)
            if construction_from:
                from .construction import saved_construction

                saved_refs = tuple(ref for head in construction_from.values()
                                   for ref in saved_construction(tracked, head)[2])
                inherited = tuple(dict.fromkeys((*inherited, *saved_refs)))
            for manifest_ref in dict.fromkeys((training_manifest, *(ref for ref, _ in runtime.replay_passes))):
                manifest = TrainingManifest.from_bytes(read_verified(tracked, manifest_ref))
                published = PublishedReleaseReader(tracked, manifest.release)
                if feedback_from is None and acquisition_from is None:
                    # A new diagnostic batch needs executable task inputs.
                    # Imported reports load only the task views they actually use.
                    for task, _ in manifest.tasks:
                        published.load(task)
                if manifest.corpus is not None or "corpus" in published.manifest.metadata:
                    validate_membership(tracked, manifest, ())
            for parent in parents.values():
                if parent.checkpoint is not None:
                    NativeAnchor.load(tracked, parent.checkpoint)
            tracked.publish(objects, transfer=publish_feedback)
            if inherited:
                # The selected boundary already owns these immutable payloads.
                # Reuse the same owner-to-owner transfer as imported feedback.
                if publish_feedback is None:
                    objects.publish(reader, inherited)
                else:
                    publish_feedback(objects, inherited)
            inputs = tuple(dict.fromkeys((*tracked.references, *inherited)))
        tape.record("round_requested", {"request": ref.as_dict()}, (ref, *inputs))
        if tape.events[-1].kind == "round_settled":
            return thaw_json(tape.events[-1].payload)

        def stage(name, operation, child, journal, *, extra_journals=()):
            if tape.cursor < len(tape.events):
                row = thaw_json(tape.take("round_stage").payload)
                if row["name"] != name:
                    raise RecordError("round-stage-order", "round stage order changed")
                return row
            outcome = operation()
            head, closure = child.seal(journal)
            for extra in extra_journals:
                extra_head, refs = child.seal(extra)
                closure = (*closure, *refs)
                outcome = {**outcome, extra + "_head": extra_head.as_dict()}
            objects.publish_owned(child.objects, closure)
            row = {"name": name, "head": head.as_dict(), "outcome": outcome}
            # Native interruption is resumable in its own child journal. Do not
            # freeze an observer's unfinished result as a completed parent stage.
            if name != "learning" or outcome["status"] == "completed":
                tape.record("round_stage", row, closure)
            return row

        material_boundary = next((event for event in tape.events[tape.cursor:]
                                  if event.kind == "round_materials"), None)
        if material_boundary is not None:
            # The completed acquisition already owns its feedback and material
            # closure. Resume from that boundary, without republishing old
            # rollout evidence to NFS before every construction retry.
            while tape.cursor <= material_boundary.sequence:
                tape.take(tape.events[tape.cursor].kind)
            material_ref = BlobRef.from_dict(thaw_json(material_boundary.payload)["materials"])
            material = FrozenMaterials.from_bytes(read_verified(objects, material_ref))
        elif acquisition_from is not None:
            prefix, _ = acquired_prefix(objects, acquisition_from, verify_artifacts=False)
            for event in prefix[1:]:
                tape.record(event.kind, thaw_json(event.payload), event.artifacts)
            material_ref = BlobRef.from_dict(thaw_json(prefix[-1].payload)["materials"])
            material = FrozenMaterials.from_bytes(read_verified(objects, material_ref))
        else:
            material_ref, material = _acquire_materials(round_id=round_id, checkpoint=checkpoint,
                library=library, training_manifest=training_manifest, runtime=runtime,
                objects=objects, run=run, tape=tape, stage=stage, cancelled=cancelled,
                feedback_from=feedback_from, feedback_reader=reader, publish_feedback=publish_feedback)
            if material_ref is None:
                return material

        control = AttemptControl(cancelled=cancelled)
        return _construct_and_learn(round_id=round_id, base_checkpoint=base_checkpoint, library=library,
            parents=parents, runtime=runtime, objects=objects, run=run, tape=tape, stage=stage,
            control=control, material=material, material_ref=material_ref,
            release_model=release_model, cancelled=cancelled, scope_parents=scope_parents,
            training_manifest=training_manifest, input_reader=reader, publish_inputs=publish_feedback,
            construction_from=construction_from)


def _diagnostic_feedback(*, round_id, checkpoint, training_manifest, runtime, objects,
                         run, tape, stage, cancelled):
    jobs = tuple(replace(job, attempt=replace(job.attempt,
        attempt_id=f"{round_id}:diagnosis:{index}", model_policy=runtime.model_policy), model=runtime.model)
        for index, job in enumerate(runtime.diagnosis.jobs))
    diagnosis = replace(runtime.diagnosis, run_id=f"{round_id}:diagnosis", jobs=jobs)
    child = RunStore(run.directory / "diagnosis")
    diagnosed = stage("diagnosis", lambda: run_batch(plan=diagnosis, reader=objects, directory=child.directory,
        judge=SimulatorJudge(), testbench=CocotbBackend() if diagnosis.testbench is not None else None,
        cancelled=cancelled).as_dict(), child, "report")
    recorded_feedback = next((thaw_json(event.payload) for event in tape.events
                              if event.kind == "round_feedback"), None)
    legacy_feedback = recorded_feedback is not None and "request" not in recorded_feedback
    if not diagnosis.score_on_submit and not legacy_feedback:
        scoring = judgment_run(child, "scoring")
        diagnosed = stage("diagnosis_scoring", lambda: rejudge_batch(
            directory=child.directory, name="scoring", reader=objects, judge=SimulatorJudge(),
            testbench=CocotbBackend() if diagnosis.testbench is not None else None,
            cancelled=cancelled).as_dict(), scoring, "report", extra_journals=("task",))
    feedback_request = FeedbackRequest(round_id, checkpoint, training_manifest, runtime.model.identity,
        (BlobRef.from_dict(diagnosed["head"]),),
        schema="behave.train-feedback-request/1" if legacy_feedback else "behave.train-feedback-request/2")
    return feedback_request, legacy_feedback


def _acquisition_outcomes(rows):
    """Keep terminal non-submissions visible without blocking other selections.

    Acquisition's ``unfinished`` status is a sealed role ending without submit
    (for example, turn_limit), not an active worker to wait on. An interrupted
    process remains a separate recovery boundary.
    """
    interrupted = next((row for row in rows if row["outcome"]["status"] == "interrupted"), None)
    completed = tuple(row for row in rows if row["outcome"]["status"] == "completed")
    return completed, interrupted


def _new_acquisition_feedback(*, round_id, checkpoint, library, training_manifest, runtime,
                      objects, run, tape, stage, cancelled, feedback_from=None, feedback_reader=None,
                      publish_feedback=None):
    if feedback_from is None:
        feedback_request, legacy_feedback = _diagnostic_feedback(round_id=round_id,
            checkpoint=checkpoint, training_manifest=training_manifest, runtime=runtime,
            objects=objects, run=run, tape=tape, stage=stage, cancelled=cancelled)
        source = objects
    else:
        source = ReadSet(feedback_reader)
        feedback_request = read_feedback_request(read_verified(source, feedback_from))
        legacy_feedback = False
    # The shared exporter validates membership and never supplies B1/RTL goldens.
    bundle = export_feedback(feedback_request, source, approved_manifest=training_manifest,
                             recorded_reports=feedback_from is not None)
    if feedback_from is not None:
        from behave.agent.journal import load_chain

        # Preserve the producer's complete evidence by reference for later audit;
        # analysis does not need to reread its model/token payloads now.
        closure = tuple(dict.fromkeys(ref for head in feedback_reports(feedback_request, source)
            for event in load_chain(source, head, verify_artifacts=False)
            for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))
        references = tuple(dict.fromkeys((*source.references, *closure)))
        if publish_feedback is None:
            objects.publish(feedback_reader, references)
        else:
            publish_feedback(objects, references)
        source.references.update(dict.fromkeys(references))
    for file in bundle.files:
        objects.put(file.data)
    feedback = objects.put(WorkspaceSnapshot(tuple(file.reference for file in bundle.files)).to_bytes())
    feedback_record, feedback_refs = {"feedback": feedback.as_dict()}, ()
    if not legacy_feedback:
        request_ref = objects.put(feedback_request.to_bytes())
        feedback_record["request"] = request_ref.as_dict()
        feedback_refs = (request_ref, *source.references) if feedback_from is not None else (request_ref,)
    return feedback, bundle.facts, feedback_record, (
        feedback, *feedback_refs, *(file.reference.content for file in bundle.files))


def _acquire_materials(*, round_id, checkpoint, library, training_manifest, runtime,
                      objects, run, tape, stage, cancelled, feedback_from=None, feedback_reader=None,
                      publish_feedback=None):
    saved = next((event for event in tape.events if event.kind == "round_feedback"), None)
    if saved is None:
        feedback, facts, feedback_record, feedback_refs = _new_acquisition_feedback(
            round_id=round_id, checkpoint=checkpoint, library=library, training_manifest=training_manifest,
            runtime=runtime, objects=objects, run=run, tape=tape, stage=stage, cancelled=cancelled,
            feedback_from=feedback_from, feedback_reader=feedback_reader, publish_feedback=publish_feedback)
    else:
        # The round request already matched. Its pinned feedback and completed
        # diagnosis are authoritative; do not reimport every old rollout trace
        # from NFS merely to resume a later acquisition stage.
        while tape.cursor < saved.sequence:
            event = tape.take("round_stage")
            if event.payload["name"] not in {"diagnosis", "diagnosis_scoring"}:
                raise RecordError("round-stage-order", "unexpected stage before frozen feedback")
        feedback_record = thaw_json(saved.payload)
        feedback = BlobRef.from_dict(feedback_record["feedback"])
        snapshot = WorkspaceSnapshot.from_bytes(read_verified(objects, feedback))
        facts = parse_json(read_verified(objects, snapshot.file("feedback.json").content))
        feedback_refs = saved.artifacts
    control = AttemptControl(cancelled=cancelled)
    from .acquisition import acquisition_assignments, feedback_task_ids

    task_ids = feedback_task_ids(facts, include_incomplete=runtime.acquisition_limits is not None)
    assignments = acquisition_assignments(runtime.acquisitions, facts, task_ids,
        library=library, feedback=feedback, round_id=round_id, policy=runtime.model_policy,
        model=runtime.model, limits=runtime.acquisition_limits)
    if runtime.acquisition_limits is not None:
        feedback_record["assignments"] = [parse_json(config.to_bytes()) for config in assignments]
    tape.record("round_feedback", feedback_record, feedback_refs)
    model_pools = {config.model_admission: None if config.model_admission is None else ResourcePool(config.model_admission)
                   for config in runtime.acquisitions}
    def acquire(item, child_stage):
        index, configuration = item
        child = JournalDirectory(run.directory / "acquisition" / f"pass_{index}")
        return child_stage(f"acquisition:{index}", lambda: acquire_materials(
            configuration=configuration, reader=objects, run=child, control=control,
            model_pool=model_pools[configuration.model_admission]), child, "acquisition")
    rows = run_stage_tasks(enumerate(assignments), acquire,
                           tape=tape, objects=objects, workers=runtime.workers)
    completed, interrupted = _acquisition_outcomes(rows)
    if interrupted is not None:
        return None, {"status": "unfinished", "stage": interrupted}
    acquired = tuple(BlobRef.from_dict(row["head"]) for row in completed)
    incremental = bool(runtime.acquisitions and runtime.acquisitions[0].job.inputs.experience_mode == "delta-v1")
    memory_key = "experience_delta" if incremental else "experience"
    backlog = runtime.acquisitions[0].job.inputs.pending_experience if incremental else ()
    proposals = tuple(dict.fromkeys((*backlog, *(BlobRef.from_dict(row["outcome"]["materials"][memory_key])
        for row in completed if memory_key in row["outcome"]["materials"]))))
    prior = runtime.acquisitions[0].job.inputs.experience if runtime.acquisitions else None
    experience = proposals[0] if proposals and not incremental else prior
    pending_experience = None
    memory_stage = "experience"
    if incremental and proposals:
        template = runtime.acquisitions[0]
        job = replace(template.job, inputs=replace(template.job.inputs, library=library, feedback=feedback,
            task_ids=(), experience_proposals=proposals, pending_experience=(),
            proposal_target=None, construction_history=None, experience_mode="merge-v1",
            guidance=objects.put(render("synthesis/source_acquirer/consolidate").encode())),
            attempt=replace(template.job.attempt, attempt_id=f"{round_id}:experience",
                            model_policy=runtime.model_policy), model=runtime.model)
        configuration = replace(template, job=job)
        pending_experience = objects.put(configuration.to_bytes())
    elif len(proposals) > 1:
        template = runtime.acquisitions[0]
        job = replace(template.job, inputs=replace(template.job.inputs, library=library, feedback=feedback,
            task_ids=task_ids, experience_proposals=proposals,
            proposal_target=None,
            feedback_view="compact-v1" if runtime.acquisition_limits is not None else template.job.inputs.feedback_view,
            guidance=objects.put(render("synthesis/source_acquirer/legacy_consolidate").encode())),
            attempt=replace(template.job.attempt, attempt_id=f"{round_id}:experience",
                            model_policy=runtime.model_policy), model=runtime.model)
        configuration = replace(template, job=job)
        if tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "round_stage":
            # Older rounds committed memory before their material boundary.
            # Replay those receipts unchanged; only unscheduled work moves.
            child = JournalDirectory(run.directory / "experience")
            row = stage("experience", lambda: acquire_materials(configuration=configuration, reader=objects,
                run=child, control=control, model_pool=model_pools[configuration.model_admission]), child, "acquisition")
            if row["outcome"]["status"] == "interrupted":
                if cancelled():
                    return None, {"status": "unfinished", "stage": row}
                # Preserve the old failed receipt, but do not replay it forever.
                # Only the merge is retried; completed Analysts remain pinned.
                pending_experience = objects.put(configuration.to_bytes())
                memory_stage = "experience:retry"
            experience = (BlobRef.from_dict(row["outcome"]["materials"]["experience"])
                          if row["outcome"]["status"] == "completed" else prior)
        else:
            # Builder consumes proposals, not private Analyst memory. Freeze
            # both the proposal handoff and the independent merge job now.
            pending_experience = objects.put(configuration.to_bytes())
            experience = prior
    material_request = MaterialRequest(round_id, checkpoint, library, feedback, runtime.model.identity, tuple(acquired),
        feedback_request=feedback_from)
    objects.put(material_request.to_bytes())
    material = freeze_materials(material_request, objects,
        acquisition_reports=tuple(row["outcome"] for row in completed))
    material_ref = objects.put(material.to_bytes())
    pending_closure = ()
    if pending_experience is not None:
        from behave.data.role import input_closure

        pending_closure = (pending_experience, *input_closure(configuration.job, objects))
    history = runtime.acquisitions[0].job.inputs.construction_history if runtime.acquisitions else None
    tape.record("round_materials", {"materials": material_ref.as_dict(),
        **({"experience_pending": pending_experience.as_dict()} if pending_experience is not None else {}),
        **({"experience_stage": memory_stage} if memory_stage != "experience" else {}),
        **({"construction_history": history.as_dict()} if history is not None else {}),
        **({"experience": experience.as_dict()} if experience is not None else {})},
        (material.request, material_ref, *pending_closure,
         *((experience,) if experience is not None else ()),
         *((history,) if history is not None else ())))
    return material_ref, material


def _construction_outcomes(outcomes):
    """Keep terminal pending packages; only an interrupted owner pauses the round."""
    packages = []
    for outcome in outcomes:
        if outcome["status"] == "unfinished" and "stage" in outcome:
            return tuple(packages), outcome
        if outcome.get("package") is not None:
            packages.append(BlobRef.from_dict(outcome["package"]))
    return tuple(packages), None


def _construct_and_learn(*, round_id, base_checkpoint, library, parents, runtime, objects, run,
                         tape, stage, control, material, material_ref, release_model, cancelled, scope_parents,
                         training_manifest, input_reader, publish_inputs, construction_from):
    index = LibraryIndex(objects, library)
    from .materials import proposal_briefs, source_briefs

    direct = runtime.construction.scope is None
    acquired = (thaw_json(event.payload)["outcome"] for event in tape.events
                if event.kind == "round_stage" and event.payload["name"].startswith("acquisition:")
                and event.payload["outcome"]["status"] == "completed")
    briefs = (proposal_briefs if direct else source_briefs)(library, acquired)
    selected = [identifier for identifier, unit in index.units.items() if unit.content in material.documents]
    if selected:
        index.inventory(tuple(selected), objects)
    configuration = _bind_construction(runtime.construction, runtime.model, runtime.model_policy)
    material_record = next(thaw_json(event.payload) for event in tape.events if event.kind == "round_materials")
    memory = material_record.get("experience")
    pending_memory = material_record.get("experience_pending")
    memory_stage = material_record.get("experience_stage", "experience")
    task_runtime = runtime.task
    pools = configuration.pools()
    from .construction import construction_handoff

    retained, repairs = construction_handoff(objects, construction_from, configuration)
    rules = ReleasePolicy.from_bytes(read_verified(objects, runtime.release_policy))
    rules = replace(rules, review_model_policy=runtime.model_policy,
        scope_review=None if rules.scope_review is None else
        ScopeReviewPolicy(rules.scope_review.guidance, runtime.model_policy))
    policy_ref = objects.put(rules.to_bytes())
    reached = Event()
    construction_control = replace(control, cancelled=lambda: cancelled() or reached.is_set())
    selected_packages = {}
    admission_decisions = {}

    def select(row):
        if runtime.construction_target is None or reached.is_set() or not row["name"].startswith("construction:"):
            return
        package = row["outcome"].get("package")
        if package is None:
            return
        from behave.data.publication import audit_release

        # Use the same admission policy as publication, including its corpus
        # rules. A role's review_ready label alone does not count toward the goal.
        candidate = BlobRef.from_dict(package)
        request = ReleaseRequest(round_id, policy_ref, (candidate,))
        disposition = audit_release(request, objects, verify_history=False)[0].disposition
        admission_decisions[row["name"]] = {"outcome": disposition.outcome, "reasons": list(disposition.reasons)}
        if disposition.outcome == "include":
            selected_packages[row["name"]] = candidate
        if len(selected_packages) >= runtime.construction_target:
            reached.set()

    def construct_task(item, stage):
        key, identifier, brief = item
        proposal = parse_json(brief)
        source = objects.put(index.source_record(identifier, proposal.get("supporting_source_ids", ())).to_bytes())
        task_id = f"{round_id}-{key}"
        if direct:
            objective = proposal["objective"]
            intent = objects.put(ScopeProposal(source, objective, (objective,), (),
                                               brief=objects.put(brief)).to_bytes())
            configured = replace(configuration, run_id=task_id,
                synthesis=replace(configuration.synthesis, plan=replace(configuration.synthesis.plan,
                    run_id=task_id, task_id=task_id, scope=intent, preparation=None)))
            return build(configured, task_id, stage)
        scope = replace(configuration.scope.plan, run_id=task_id,
            author=replace(configuration.scope.plan.author,
                inputs=replace(configuration.scope.plan.author.inputs, source=source,
                               parent=scope_parents.get(task_id), feedback="", selection=None,
                               brief=objects.put(brief))))
        child = JournalDirectory(run.directory / "scope" / task_id)
        row = stage(f"scope:{task_id}", lambda: parse_json(prepare_scope(
            plan=scope, reader=objects, run=child, control=construction_control,
            model_pool=pools["model_pool"]).to_bytes()), child, "scope")
        scope_result, _ = load_scope_preparation(objects, BlobRef.from_dict(row["head"]))
        if scope_result.status == "interrupted":
            return {"status": "unfinished", "stage": row}
        if scope_result.status != "review_ready":
            return {"status": "pending", "package": None}  # Keep its material in C_r.
        configured = replace(configuration, run_id=task_id, scope=replace(configuration.scope, plan=scope),
            synthesis=replace(configuration.synthesis, plan=replace(configuration.synthesis.plan,
                run_id=task_id, task_id=task_id, scope=scope_result.scopes[-1], preparation=BlobRef.from_dict(row["head"]))))
        return build(configured, task_id, stage)

    def build(configured, task_id, stage):
        child = JournalDirectory(run.directory / "construction" / task_id)
        plan, continuation = repairs.get(task_id, (configured.plan, None))
        row = stage(f"construction:{task_id}", lambda: construct(plan=plan,
            reader=objects, run=child, control=construction_control, **pools,
            **({"continuation_from": continuation} if continuation is not None else {})), child, "construction")
        if cancelled() or row["outcome"]["status"] in {"interrupted", "cleanup_unconfirmed", "scope_interrupted",
                                                       "rtl_interrupted", "rtl_cleanup_unconfirmed"}:
            return {"status": "unfinished", "stage": row}
        return row["outcome"]

    work = ([(key, parse_json(brief)["source_id"], brief) for key, brief in briefs.items()] if direct else
            [(f"{identifier}-{proposal}", identifier, briefs[identifier]) for identifier in selected if identifier in briefs
             for proposal in range(runtime.proposals_per_source)])
    if set(construction_from) - {f"{round_id}-{key}" for key, _, _ in work}:
        raise ValueError("saved construction IDs do not belong to this proposal batch")
    recorded_names = {event.payload["name"] for event in tape.events if event.kind == "round_stage"} | retained.keys()
    # Recover the admission count before dispatching anything. Otherwise a
    # completed target can start surplus work while its receipts are replayed.
    # Reading these rows does not advance the tape; normal replay below still
    # preserves commit order and checks any recorded cleanup failure.
    saved_stages = {event.payload["name"]: thaw_json(event.payload)
                    for event in tape.events[tape.cursor:] if event.kind == "round_stage"}
    for row in saved_stages.values():
        select(row)
    for name, (row, _) in retained.items():
        if name not in saved_stages:
            select(row)
    def dispatch(item, child_stage):
        if item is not None:
            return construct_task(item, child_stage)
        config = AcquisitionConfiguration.from_bytes(read_verified(objects, BlobRef.from_dict(pending_memory)))
        from .memory_recovery import memory_child

        child = (JournalDirectory(run.directory / "experience")
                 if config.job.inputs.experience_mode == "merge-v1" else
                 memory_child(run.directory / "experience", config))
        shared_pool = pools["model_pool"]
        if shared_pool is not None and shared_pool.policy != config.model_admission:
            shared_pool = None
        row = child_stage(memory_stage, lambda: acquire_materials(configuration=config, reader=objects,
            run=child, control=control, model_pool=shared_pool), child, "acquisition")
        return {"memory_stage": row}

    # One parent writer commits both chains in completion order. The memory
    # worker ignores the construction quota, but honors operator cancellation.
    outcomes = run_stage_tasks(([None] if pending_memory is not None else []) + work, dispatch,
                              tape=tape, objects=objects, workers=runtime.workers,
                              retained_stages=retained, on_stage=select,
                              commit_stage=lambda row: row["name"] != memory_stage
                              or row["outcome"]["status"] != "interrupted",
                              should_start=lambda item: item is None or not reached.is_set()
                              or f"construction:{round_id}-{item[0]}" in recorded_names)
    memory_rows = [outcome["memory_stage"] for outcome in outcomes if "memory_stage" in outcome]
    outcomes = tuple(outcome for outcome in outcomes if "memory_stage" not in outcome)
    memory_fields = {}
    if memory_rows:
        row = memory_rows[0]
        if row["outcome"]["status"] == "interrupted":
            return {"status": "unfinished", "stage": row}
        merged = row["outcome"]
        if merged.get("schema") == "behave.experience-merge/1":
            memory = None if merged["materials"] is None else merged["materials"]["experience"]
            memory_fields["experience_pending_changes"] = merged["pending_experience"]
        elif merged["status"] == "completed":
            memory = merged["materials"]["experience"]
    packages, interrupted = _construction_outcomes(outcomes)
    if interrupted is not None and not reached.is_set():
        return interrupted
    for outcome in outcomes:
        if (outcome.get("status") == "unfinished" and "stage" in outcome
                and "cleanup_unconfirmed" in outcome["stage"]["outcome"]["status"]):
            # Reaching the data target does not release ownership of an
            # unsettled worker. Keep its receipt and finish cleanup first.
            return outcome
    selection = None
    if runtime.construction_target is not None:
        completed = {event.payload["name"]: thaw_json(event.payload) for event in tape.events
                     if event.kind == "round_stage" and event.payload["name"].startswith("construction:")}
        selection = {"target": runtime.construction_target,
            "selected": [{"name": name, "package": package.as_dict()} for name, package in selected_packages.items()],
            "admissions": admission_decisions,
            "deferred": [row for name, row in completed.items() if name not in selected_packages],
            "unstarted": [f"construction:{round_id}-{key}" for key, _, _ in work
                          if f"construction:{round_id}-{key}" not in completed],
            "target_met": reached.is_set()}
        tape.record("round_construction_selection", selection)
        packages = tuple(selected_packages.values())
    publication = ReleaseRequest(round_id, policy_ref, tuple(packages)) if packages else None
    boundary = next(event for event in tape.events if event.kind == "round_materials")
    for event in tape.events[:boundary.sequence + 1]:
        objects.put(event.to_bytes())
    material_boundary = BlobRef.of(boundary.to_bytes())
    # All role workers have settled. Publication/history are CPU-only; do not
    # keep the model resident while copying their evidence over shared storage.
    # A replenishment child retains serving for its parent/next batch.
    released = None if runtime.keep_serving else release_model()
    child = JournalDirectory(run.directory / "data", objects=objects)
    row = stage("data", lambda: prepare_round_data(materials=material_ref, publication=publication,
        reader=objects, run=child, material_boundary=material_boundary).as_dict(), child, "round_data")
    data, _ = committed_round_data(objects, BlobRef.from_dict(row["head"]))
    if tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "round_construction_history":
        history = BlobRef.from_dict(thaw_json(tape.take("round_construction_history").payload)["history"])
    else:
        from .construction import construction_history

        prior = next(thaw_json(event.payload).get("construction_history") for event in tape.events
                     if event.kind == "round_materials")
        prior = None if prior is None else BlobRef.from_dict(prior)
        history = objects.put(construction_history(reader=objects, round_id=round_id, prior=prior,
            stages=[thaw_json(event.payload) for event in tape.events if event.kind == "round_stage"],
            briefs=briefs, release=data.release))
        tape.record("round_construction_history", {"history": history.as_dict()}, (history,))
    return finish_round(round_id=round_id, base_checkpoint=base_checkpoint, parents=parents,
        runtime=runtime, task_runtime=task_runtime, objects=objects, run=run, tape=tape, stage=stage,
        data=data, history=history, memory=memory, training_manifest=training_manifest,
        input_reader=input_reader, publish_inputs=publish_inputs,
        release_model=release_model if runtime.keep_serving else lambda: released,
        cancelled=cancelled,
        result_fields={**memory_fields, **({} if selection is None else {"construction_selection": selection})})


def finish_round(*, round_id, base_checkpoint, parents, runtime, task_runtime, objects, run, tape,
                 stage, data, history, memory, training_manifest, input_reader, publish_inputs,
                 release_model, cancelled, result_fields=None, combine_training_pool=False):
    """One shared publication-to-pool/learning handoff, including target batches."""
    released = None
    while tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "round_model_released":
        released = thaw_json(tape.take("round_model_released").payload)
    if runtime.keep_serving:
        tape.record("round_model_retained", {
            "owner": "caller" if combine_training_pool else "parent-generation-batch"})
    if runtime.keep_serving and not combine_training_pool:
        # The parent combines admitted tasks once. A replenishment child does
        # not need to republish the whole seed/training pool after each batch.
        result = {"status": "completed", "round_id": round_id, "data": data.head.as_dict(),
                  "learning": None, "parents": {name: parent.record() for name, parent in parents.items()},
                  "construction_history": history.as_dict(), **(result_fields or {})}
        if memory is not None:
            result["experience"] = memory
        tape.record("round_settled", result)
        tape.finish()
        run.seal("round")
        return result
    elif not runtime.keep_serving:
        # A historical receipt certifies its old service owner, not services a
        # recovering operator may have started. The current owner's idempotent
        # close must also confirm quiescence before any native learning.
        current_release = release_model()
        if released is None or released.get("cleanup_complete") is not True:
            tape.record("round_model_released", current_release)
        released = current_release
    if not runtime.keep_serving and released.get("cleanup_complete") is not True:
        return {"status": "unfinished", "stage": "model-release", "release": released}
    if tape.cursor < len(tape.events) and tape.events[tape.cursor].kind == "round_training_pool":
        pool_ref = BlobRef.from_dict(thaw_json(tape.take("round_training_pool").payload)["manifest"])
    else:
        pool_inputs = tuple(dict.fromkeys((training_manifest, *(ref for ref, _ in runtime.replay_passes))))
        retained = tuple(dict.fromkeys(ref for manifest_ref in pool_inputs for ref in retain_training_manifest(
            manifest=manifest_ref, reader=input_reader, objects=objects, publish=publish_inputs)))
        admitted = admitted_training_manifest(objects, data.release)
        if admitted is not None:
            admitted_ref = objects.put(admitted.to_bytes())
            pool_inputs = (*pool_inputs, admitted_ref)
        pool = cumulative_training_manifest(manifests=pool_inputs, release_id=f"{round_id}:training-pool",
                                            reader=objects, objects=objects)
        pool_ref = BlobRef.of(pool.to_bytes())
        pool_release = ReleaseManifest.from_bytes(read_verified(objects, pool.release))
        tape.record("round_training_pool", {"manifest": pool_ref.as_dict()},
                    tuple(dict.fromkeys((pool_ref, pool.release, pool_release.policy, *pool_inputs, *retained))))
    if runtime.generation_only:
        result = {"status": "completed", "round_id": round_id, "data": data.head.as_dict(),
                  "learning": None, "parents": {name: parent.record() for name, parent in parents.items()},
                  "training_manifest": pool_ref.as_dict(), "construction_history": history.as_dict(),
                  **(result_fields or {})}
        if memory is not None:
            result["experience"] = memory
        tape.record("round_settled", result)
        tape.finish()
        run.seal("round")
        return result
    task_passes = list(runtime.replay_passes)
    if runtime.acquired_updates:
        task_passes.append((pool_ref, runtime.acquired_updates))
    child = JournalDirectory(run.directory / "learning")
    learning = stage("learning", lambda: learn_round(data=data.head, base_checkpoint=base_checkpoint,
        task_passes=tuple(task_passes), raw_updates=runtime.raw_updates, encoding=runtime.encoding,
        parents=parents, task_runtime=task_runtime, raw_runtime=runtime.raw,
        reader=objects, run=child, cancelled=cancelled), child, "paired_learning")
    if learning["outcome"]["status"] != "completed":
        return {"status": "unfinished", "stage": learning}
    result = {"status": "completed", "round_id": round_id, "data": data.head.as_dict(),
              "learning": learning["head"], "parents": learning["outcome"]["parents"],
              "training_manifest": pool_ref.as_dict(), "construction_history": history.as_dict(),
              **(result_fields or {})}
    if memory is not None:
        result["experience"] = memory
    tape.record("round_settled", result)
    tape.finish()
    run.seal("round")
    # The live coordinator already owns these committed child outcomes. Keep
    # load_round as an explicit offline audit, not another full NFS read pass
    # after successful learning, checkpointing and cleanup.
    return result
