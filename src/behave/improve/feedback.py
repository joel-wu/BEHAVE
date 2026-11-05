"""Deterministic train-side facts, not another scorer or failure classifier.

The input reports stay in the trusted store. Only the returned file set belongs
in an acquisition workspace: no reference implementation, full solver context,
private task manifest or unrestricted content-store reader is exported.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from behave.core.artifacts import BlobRef, ContentReader, FileData, FileRef, read_verified
from behave.core.records import RecordError, canonical_json, fields, parse_json, text, thaw_json, version
from behave.core.storage import DirectoryStore, install_exclusive
from behave.data.published import PublicTaskInputs, PublishedReleaseReader
from behave.task_execution.batch import load_batch_report, read_batch_report
from behave.task_execution.batch_plan import BatchPlan
from behave.task_execution.capture import load_submission
from behave.task_execution.judgment import ComponentJudgment
from behave.task_execution.result import TaskResult
from behave.task_execution.solver_settings import submission_components
from behave.train.plan import TrainingManifest

from .diagnostics import execution_feedback, scoring_diagnostics
from .task_feedback import task_groups


@dataclass(frozen=True, slots=True)
class FeedbackRequest:
    round_id: str
    checkpoint: BlobRef
    training_manifest: BlobRef
    model_transport: str
    reports: tuple[BlobRef, ...]
    # (later observation ID, earlier observation ID). A rerun is not a new
    # independent sample and never replaces its predecessor with a best score.
    retries: tuple[tuple[str, str], ...] = ()
    schema: str = "behave.train-feedback-request/2"

    def __post_init__(self) -> None:
        if self.schema != "behave.train-feedback-request/1":
            version(self.schema, "behave.train-feedback-request/2")
        text(self.round_id, "round id")
        text(self.model_transport, "generating model transport")
        if any(type(ref) is not BlobRef for ref in (self.checkpoint, self.training_manifest)):
            raise TypeError("feedback requires checkpoint and train membership identities")
        if not self.reports or any(type(ref) is not BlobRef for ref in self.reports):
            raise ValueError("feedback requires sealed reports")
        if len(set(self.reports)) != len(self.reports):
            raise ValueError("a report cannot be counted twice")
        if len({later for later, _ in self.retries}) != len(self.retries):
            raise ValueError("a retry has exactly one predecessor")
        for later, earlier in self.retries:
            text(later, "retry observation")
            text(earlier, "prior observation")

    def to_bytes(self) -> bytes:
        return canonical_json({"schema": self.schema, "round_id": self.round_id,
            "checkpoint": self.checkpoint.as_dict(), "training_manifest": self.training_manifest.as_dict(),
            "model_transport": self.model_transport, "reports": [ref.as_dict() for ref in self.reports],
            "retries": [list(pair) for pair in self.retries]})

    @classmethod
    def from_bytes(cls, payload: bytes) -> FeedbackRequest:
        row = fields(parse_json(payload), {"schema", *cls.__dataclass_fields__})
        result = cls(row["round_id"], BlobRef.from_dict(row["checkpoint"]),
            BlobRef.from_dict(row["training_manifest"]), row["model_transport"],
            tuple(BlobRef.from_dict(ref) for ref in row["reports"]),
            tuple(tuple(pair) for pair in row["retries"]), row["schema"])
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "feedback request must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class FeedbackBundle:
    """Explicit safe files. Identity references in the JSON grant no read access."""

    files: tuple[FileData, ...]

    @property
    def facts(self) -> dict:
        return parse_json(next(file.data for file in self.files if file.reference.path == "feedback.json"))


@dataclass(frozen=True, slots=True)
class _TaskContext:
    """Keep comparison metadata after verifying and releasing private payloads."""

    public: PublicTaskInputs
    run_policy: BlobRef
    judge_policy: BlobRef


def read_feedback_request(payload):
    """Read old single-update records or an explicit multi-update index."""
    from .feedback import FeedbackWindow

    if parse_json(payload)["schema"] == "behave.train-feedback-window/1":
        return FeedbackWindow.from_bytes(payload)
    return FeedbackRequest.from_bytes(payload)


def feedback_reports(request, reader):
    if isinstance(request, FeedbackRequest):
        return request.reports
    from .feedback import window_requests

    return tuple(head for _, _, member in window_requests(request, reader) for head in member.reports)


def select_feedback(facts, task_ids):
    """Project complete task groups without changing observation identities."""
    selected = set(task_ids)
    observations = [row for row in facts["observations"] if row["task_id"] in selected]
    contexts = {row["context"] for row in observations}
    if "window" in facts:
        contexts.update(row["context"] for row in facts["excluded_attempts"] if row["task_id"] in selected)
    return {**facts, "observations": observations,
        "contexts": {key: value for key, value in facts["contexts"].items() if key in contexts},
        "tasks": [group for group in facts["tasks"] if group["task_id"] in selected],
        "excluded_attempts": [row for row in facts.get("excluded_attempts", []) if row["task_id"] in selected],
        "counts": {"first_attempts": _counts([row for row in observations if row["retry_of"] is None]),
                   "all_observations": _counts(observations),
                   "retries": sum(row["retry_of"] is not None for row in observations),
                   "by_context": {key: _counts([row for row in observations if row["context"] == key
                                                 and row["retry_of"] is None]) for key in contexts}}}


def observation_id(report: BlobRef, index: int) -> str:
    return BlobRef.of(canonical_json({"report": report.as_dict(), "index": index})).sha256


def _component(part: ComponentJudgment | None) -> dict:
    if part is None:
        return {"status": "not_observed", "score": None}
    if part.code == "submission-component-missing":
        status = "not_submitted"
    elif part.score is not None:
        status = "passed" if part.score else "candidate_failed"
    elif part.origin in {"reference", "verifier"}:
        status = "sim_no_conclusion"
    else:
        status = "execution_no_conclusion"
    # Detail/evidence can contain private reference traces or arbitrary submitted
    # text. Do not recursively export it. Diagnostic projections are separate
    # named artifacts, not permission to traverse a judgment's whole closure.
    return {"status": status, "score": part.score, "code": part.code,
            "origin": part.origin, "retryable": part.retryable, "cleanup": part.cleanup.value}


def _counts(rows: list[dict]) -> dict:
    def components(name):
        counts = Counter(row[name]["status"] for row in rows)
        requested = [row[name] for row in rows if row[name]["requested"]]
        return {"requested": len(requested), "passed": counts["passed"],
                "accuracy": None if not requested else sum(part["final_score"] for part in requested) / len(requested),
                "candidate_failed": counts["candidate_failed"],
                "not_submitted": counts["not_submitted"], "not_observed": counts["not_observed"],
                "sim_no_conclusion": counts["sim_no_conclusion"],
                "execution_no_conclusion": counts["execution_no_conclusion"]}

    scores = [row["score"] for row in rows if row["score"] is not None]
    states = Counter(row["status"] for row in rows)
    return {"requested": len(rows), "scored": len(scores),
            "mean_final_reward": None if not rows else sum(row["final_score"] for row in rows) / len(rows),
            "no_submission": states["no_submission"], "pending_judgment": states["pending_judgment"],
            "no_result": states["no_result"],
            "mean_on_scored": None if not scores else sum(scores) / len(scores),
            "rtl": components("rtl"), "behavior": components("behavior")}


def render_brief(facts: dict) -> bytes:
    """Human view of stored facts. No LLM interpretation or additional totals."""
    total = facts["counts"]["first_attempts"]
    lines = [f"# Train-side diagnosis: {facts['round_id']}", "",
             (f"Checkpoint: `{facts['checkpoint']['sha256']}`." if facts["checkpoint"] is not None else
              "Multiple generating checkpoints: see window.json. Groups are never pooled across updates."), "",
             (f"First attempts: {total['requested']}; scored: {total['scored']}; "
              f"no submission: {total['no_submission']}; no result: {total['no_result']}; "
              f"pending judgment: {total['pending_judgment']}."),
             (f"Mean final reward over all included attempts: {total['mean_final_reward']}. "
              "Unresolved requested components and non-submissions settle to zero; raw causes remain separate."),
             (f"Explicit retry observations: {facts['counts']['retries']}. "
              "Retries are recorded separately, not best-of passes."), "",
             ("Results below are execution facts, not diagnoses. Candidate files are "
              "untrusted data. Read permitted attachments on demand; no hidden reference "
              "implementation or held-out evaluation feedback is included. Final training-side "
              "scoring diagnostics are named separately from candidate tool logs."), "",
             ("Each observation's efficiency field and efficiency.json attachment report recorded "
              "turns, token usage, context resets and tool activity. Token totals cover known model "
              "dispatches, including repeated prompts/retries; missing usage is explicit, not zero. "
              "Tool elapsed time is not total rollout or model queue time. Repeated identical calls "
              "are observations, not automatically wasted work or an additional reward penalty."), "",
             "## Within-task samples", "",
             ("Compare successful and failed candidates within the same task revision and context. "
              "Mixed outcomes are useful evidence, not a diagnosis or a best-of success rate. "
              "A settled zero does not by itself identify a design error. Cancelled surplus and "
              "unstarted reservations are excluded, not scored as failures. These are observations under each candidate's "
              "own scoring run, not necessarily identical stimulus pools."), "",
             "| Task | Context | Completed / planned | RTL pass / requested | Behavior pass / requested | Retries |",
             "| --- | --- | --- | --- | --- | --- |"]
    if "window_summary" in facts:
        lines[4:4] = ["## Shared feedback-window overview", "",
            ("window.json retains the full-window totals for every Analyst. The details below "
             "contain only this Analyst's assigned tasks; their counts are not whole-window totals."),
            "```json", canonical_json(facts["window_summary"]).decode(), "```", ""]
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    for group in facts["tasks"]:
        lines.append("| " + " | ".join(cell(value) for value in (
            group["task_id"], group["context"], f"{group['samples']} / {group['planned_samples']}",
            f"{len(group['rtl']['passed'])} / {group['rtl']['requested']}",
            f"{len(group['behavior']['passed'])} / {group['behavior']['requested']}",
            len(group["retries"]))) + " |")
    lines.extend(["", "## Individual observations", "",
             "| Observation | Task | Status | RTL | Behavior | Retry of |",
             "| --- | --- | --- | --- | --- | --- |"])
    for row in facts["observations"]:
        lines.append("| " + " | ".join(cell(value) for value in (
            row["id"], row["task_id"], row["status"], row["rtl"]["status"],
            row["behavior"]["status"], row["retry_of"] or "—")) + " |")
    return ("\n".join(lines) + "\n").encode()


def require_feedback_membership(observed, approved, reader, *, recorded_reports=False):
    """Allow historical Sim-only revisions without relabeling their rewards.

    Task IDs, definitions and scoring settings must match the current pool.
    Only Sim command/authority versions may differ, as in native pool resume.
    External/unrecorded evidence still requires the exact approved manifest.
    """
    if observed == approved:
        return
    message = "feedback requires operator-approved train membership"
    if not recorded_reports:
        raise RecordError("feedback-membership-mismatch", message)
    from behave.train.pool import resume_pool

    try:
        old = TrainingManifest.from_bytes(read_verified(reader, observed))
        new = TrainingManifest.from_bytes(read_verified(reader, approved))
        if {key for key, _ in old.tasks} != {key for key, _ in new.tasks} or old.corpus != new.corpus:
            raise ValueError("training selection changed")
        state = {"tasks": {key: {"revision": ref.as_dict()} for key, ref in old.tasks}}
        _, changes = resume_pool(state, new.tasks, reader, reader)
        if changes["changed_tasks"]:
            raise ValueError("task definitions or scoring settings changed")
    except (OSError, ValueError, KeyError) as error:
        raise RecordError("feedback-membership-mismatch", message + ": " + str(error)) from error


def _same_native_policy_after_restart(command, anchor, checkpoint):
    """Recognize a recorded native broker's relocated launcher/loopback port.

    This is not model-name matching: the entire native token/weight profile,
    sampling settings and worker pool must agree. Arbitrary brokers are never
    normalized, and callers still retain each original transport in contexts.
    """
    from dataclasses import replace

    from behave.agent.providers.local_chat import LocalChatEndpoint

    def endpoint(model):
        if model is None or len(model.argv) != 7:
            raise ValueError("not a native local chat command")
        result = LocalChatEndpoint.from_dict(parse_json(model.argv[-1].encode()))
        if (result.tokens is None or result.tokens.checkpoint != checkpoint
                or result.command(python=model.argv[0], package=model.argv[5]) != model):
            raise ValueError("unbound native broker or checkpoint")
        return result

    try:
        observed, approved = endpoint(command), endpoint(anchor)
        return replace(observed, url=approved.url) == approved
    except (ValueError, TypeError, KeyError):
        return False


def export_feedback(request: FeedbackRequest, reader: ContentReader, *,
                    approved_manifest: BlobRef, recorded_reports: bool = False) -> FeedbackBundle:
    """Verify sealed batches and export only the approved train-side projection.

    ``approved_manifest`` comes from the controller/operator, not the acquisition
    agent. The checkpoint-to-transport binding likewise belongs to the controller;
    a model's claimed name is not a checkpoint attestation.
    ``recorded_reports`` consumes the trusted producer's committed summary. It
    keeps membership, task/context and submission checks, but does not replay
    provider accounting or reread every token blob. The default remains the
    full offline audit for external evidence.
    """
    from .feedback import FeedbackWindow, export_window

    if isinstance(request, FeedbackWindow):
        return export_window(request, reader, approved_manifest=approved_manifest, recorded_reports=recorded_reports)
    require_feedback_membership(request.training_manifest, approved_manifest, reader,
                                recorded_reports=recorded_reports)
    manifest = TrainingManifest.from_bytes(read_verified(reader, request.training_manifest))
    read_verified(reader, request.checkpoint)
    allowed = dict(manifest.tasks)
    observations, contexts, attached = [], {}, {}
    previous, retry_map, seen_attempts = {}, dict(request.retries), {}
    published, excluded, planned = {}, [], Counter()
    release = None

    def attach(prefix, files):
        paths = []
        for file in files:
            name = prefix + "/" + file.reference.path
            attached[name] = FileData(FileRef(name, file.reference.content), file.data)
            paths.append(name)
        return paths

    for head in request.reports:
        report = (read_batch_report if recorded_reports else load_batch_report)(reader, head)
        summary = thaw_json(report.summary)
        plan_ref = BlobRef.from_dict(summary["plan"])
        plan = BatchPlan.from_bytes(read_verified(reader, plan_ref), reader)
        anchor = next((job.model for job in plan.jobs
                       if job.model.identity == request.model_transport), None)
        if request.schema == "behave.train-feedback-request/2":
            batch = parse_json(plan.to_bytes())
            batch.pop("run_id")
            batch.pop("jobs")
            batch_policy = BlobRef.of(canonical_json(batch)).as_dict()
        for index, (job, row) in enumerate(zip(plan.jobs, summary["candidates"], strict=True)):
            task = job.task
            if task.release != manifest.release or allowed.get(task.task_id) != task.revision:
                raise RecordError("feedback-task-forbidden", "diagnosis contains a task outside the training selection")
            if (job.model.identity != request.model_transport
                    and not (recorded_reports and _same_native_policy_after_restart(
                        job.model, anchor, request.checkpoint))):
                raise RecordError("feedback-model-mismatch", "diagnosis mixes generating model bindings")
            if task.task_id not in published:
                if release is None:
                    release = PublishedReleaseReader(reader, manifest.release)
                source = release.load(task.task_id)
                published[task.task_id] = _TaskContext(
                    source.public, source.manifest.run_policy, source.manifest.judge_policy)
            source = published[task.task_id]
            if source.public != task:
                raise RecordError("feedback-public-view-mismatch", "diagnosis public files differ from the pinned task")
            # Preserve contexts instead of silently pooling different runtime,
            # scoring or prompt settings into one estimated success rate.
            runtime = job.attempt.as_dict()
            runtime.pop("attempt_id")
            context = {"model_transport": job.model.identity, "setting": job.setting,
                       "judge_backend": plan.judge_backend,
                       "judge_policy": source.judge_policy.as_dict(),
                       "run_policy": source.run_policy.as_dict(), "attempt_policy": runtime}
            if request.schema == "behave.train-feedback-request/2":
                # Reuse the authoritative serializations. Only sample identities
                # are removed; shell limits and development Sim bindings remain.
                # Export digests, not private command/reference configurations.
                solver = parse_json(job.to_bytes())
                solver.pop("task")
                solver["attempt"].pop("attempt_id")
                context.update(solver_policy=BlobRef.of(canonical_json(solver)).as_dict(),
                               batch_policy=batch_policy)
            context_id = BlobRef.of(canonical_json(context)).sha256
            contexts[context_id] = context
            identifier = observation_id(head, index)
            retry = retry_map.get(identifier)
            if retry is not None:
                before = previous.get(retry)
                if before is None or (before["task_id"], before["revision"], before["context"]) != (
                        task.task_id, task.revision.as_dict(), context_id):
                    raise RecordError("feedback-retry-invalid", "retry must follow the same task and execution context")
            attempt_key = (task.task_id, job.attempt.attempt_id)
            if attempt_key in seen_attempts and retry != seen_attempts[attempt_key]:
                raise RecordError("feedback-retry-unlinked", "repeated candidate identity requires an explicit retry link")
            seen_attempts[attempt_key] = identifier
            result = None if row["result"] is None else TaskResult.from_bytes(
                read_verified(reader, BlobRef.from_dict(row["result"])))
            if retry is None:
                planned[(task.task_id, task.revision.sha256, context_id)] += 1
            code = (row["error"] or {}).get("code") if result is None else result.failure_code
            judgment = None if result is None else result.judgment
            # Surplus work can be cancelled after submission, with one scored
            # component retained. Its settled score is not a completed observation.
            if judgment is not None and any(component is not None and component.code == "judge-worker-cancelled"
                    for component in (judgment.behavior, judgment.rtl)):
                code = "judge-worker-cancelled"
            if code in {"candidate-not-admitted", "solver-worker-cancelled", "judge-worker-cancelled"}:
                excluded.append({"id": identifier, "task_id": task.task_id,
                    "attempt_id": job.attempt.attempt_id, "context": context_id, "reason": code})
                continue
            paths = attach(f"observations/{identifier}/public", task.files)
            efficiency = None
            if result is not None:
                efficiency, diagnostics = execution_feedback(reader, result.solver_head,
                                                             verify_artifacts=not recorded_reports)
                paths.extend(attach(f"observations/{identifier}/diagnostics",
                                    diagnostics))
            if result is not None and result.submission is not None:
                submission = load_submission(reader, result.submission)
                if (submission.task_id, submission.release, submission.revision, submission.setting) != (
                        task.task_id, task.release, task.revision, job.setting):
                    raise RecordError("feedback-submission-mismatch", "candidate belongs to another task or setting")
                for name in ("behavior", "rtl", "testbench"):
                    files = tuple(FileData(file, read_verified(reader, file.content))
                                  for file in getattr(submission, name).files)
                    paths.extend(attach(f"observations/{identifier}/candidate/{name}", files))
            if judgment is not None:
                paths.extend(attach(f"observations/{identifier}/scoring", scoring_diagnostics(reader, judgment)))
            entry = {"id": identifier, "task_id": task.task_id, "revision": task.revision.as_dict(),
                     "attempt_id": job.attempt.attempt_id, "context": context_id, "report": head.as_dict(),
                     "result": row["result"], "retry_of": retry, "status": row["status"], "score": row["score"],
                     "termination": None if result is None else result.termination,
                     "failure_stage": "execution" if result is None else result.failure_stage,
                     "failure_code": row["error"]["code"] if result is None else result.failure_code,
                     "rtl": _component(None if judgment is None else judgment.rtl),
                     "behavior": _component(None if judgment is None else judgment.behavior),
                     "attachments": paths, "billing": row["billing"], "efficiency": efficiency}
            entry["final_score"] = 0.0 if result is None else result.final_score
            for name in ("rtl", "behavior"):
                requested = name in submission_components(job.setting)
                entry[name].update(requested=requested,
                    final_score=(entry[name]["score"] or 0) if requested else None)
            observations.append(entry)
            previous[identifier] = entry
    if set(retry_map) - previous.keys():
        raise RecordError("feedback-retry-invalid", "retry link names an absent observation")
    groups = task_groups(observations)
    for group in groups:
        group["planned_samples"] = planned[(group["task_id"], group["revision"]["sha256"], group["context"])]
        group["complete"] = group["samples"] == group["planned_samples"]
    facts = {"schema": "behave.train-feedback/1", "round_id": request.round_id,
             "checkpoint": request.checkpoint.as_dict(), "training_manifest": request.training_manifest.as_dict(),
             "request": BlobRef.of(request.to_bytes()).as_dict(),
             "contexts": contexts, "observations": observations,
             "tasks": groups, "excluded_attempts": excluded,
             "counts": {"first_attempts": _counts([row for row in observations if row["retry_of"] is None]),
                        "all_observations": _counts(observations), "retries": len(retry_map),
                        "by_context": {key: _counts([row for row in observations if row["context"] == key
                                                     and row["retry_of"] is None]) for key in contexts}}}
    files = tuple(FileData(FileRef(name, BlobRef.of(payload)), payload) for name, payload in (
        ("feedback.json", canonical_json(facts)), ("brief.md", render_brief(facts))))
    return FeedbackBundle(files + tuple(attached[name] for name in sorted(attached)))


def prepare(round_directory, binding_path, output):
    from behave.core.runtime_locations import runtime_path
    round_directory, binding_path = runtime_path(round_directory), runtime_path(binding_path)
    # Read committed small receipts and the plan, not token blobs or optimizer state.
    events = [parse_json(path.read_bytes()) for path in sorted((round_directory / "training").glob("*.json"))]
    if not events or events[-1]["kind"] != "training_finished":
        raise ValueError("feedback requires a completed training batch")
    store = DirectoryStore(round_directory / "objects")
    report = parse_json(store.read(BlobRef.from_dict(events[-1]["payload"]["result"])))
    plan = parse_json(store.read(BlobRef.from_dict(report["plan"])))
    binding = parse_json(binding_path.read_bytes())
    checkpoint = BlobRef.from_dict(plan["requirements"]["checkpoint"])
    model = plan["execution"]["jobs"][0]["model"]
    from behave.agent.process_model import ModelCommand

    request = FeedbackRequest(round_directory.name, checkpoint, BlobRef.from_dict(plan["manifest"]),
        ModelCommand.from_dict(model).identity, (BlobRef.from_dict(report["execution_head"]),))
    output.mkdir(parents=True, exist_ok=True)
    objects = DirectoryStore(output / "objects")
    reference = objects.put(request.to_bytes())
    result = {"request": reference.as_dict(), "objects": str(objects.directory),
        "source_objects": [str(store.directory), str((round_directory / "execution/objects").resolve()), binding["objects"]],
        "training_objects": binding["objects"], "training_manifest": plan["manifest"],
        "producer_plan": report["plan"], "producer_checkpoint": checkpoint.as_dict(),
        "source_round": str(round_directory.resolve()), "new_rollouts": 0, "new_scores": 0}
    payload = canonical_json(result)
    if not install_exclusive(output, "input.json", payload) and (output / "input.json").read_bytes() != payload:
        raise ValueError("feedback output belongs to another recorded batch")
    return result


def prepare_window(rounds, binding_path, output):
    """Index existing single-update exports, with no new model or scoring work."""
    from behave.core.runtime_locations import runtime_path

    from .feedback import FeedbackWindow

    binding_path = runtime_path(binding_path)

    rounds = tuple(rounds)
    updates = [update for update, _ in rounds]
    if (not updates or any(type(update) is not int or update < 1 for update in updates)
            or updates != list(range(updates[0], updates[0] + len(updates)))):
        raise ValueError("feedback window requires consecutive completed update numbers")
    binding = parse_json(binding_path.read_bytes())
    objects = DirectoryStore(output / "objects")
    members, stores = [], []
    # Rollout stores hold sampled tasks only; an approved Sim refresh also
    # needs its complete historical task definitions for membership checking.
    for old_path, new_path in binding.get('self_improvement', {}).get('binding_overrides', {}).items():
        refreshed = parse_json(runtime_path(new_path).read_bytes())
        if refreshed['manifest'] == binding['manifest']:
            historical = parse_json(runtime_path(old_path).read_bytes())
            stores.append(historical['objects'])
    historical_manifest = None
    for update, directory in rounds:
        member = prepare(directory, binding_path, output / "updates" / f"{update:08d}")
        observed = BlobRef.from_dict(member["training_manifest"])
        first_member = historical_manifest is None
        if first_member:
            historical_manifest = observed
        elif observed != historical_manifest:
            raise ValueError("feedback window spans different training memberships")
        # Every later member must pin the same historical manifest. Validate
        # that immutable membership once, not once per completed update.
        if first_member and member["training_manifest"] != binding["manifest"]:
            from .feedback import require_feedback_membership
            from .input_stores import Inputs
            reader = Inputs(*(DirectoryStore(path) for path in
                dict.fromkeys((*member["source_objects"], *stores, binding["objects"]))))
            require_feedback_membership(observed, BlobRef.from_dict(binding["manifest"]), reader,
                                        recorded_reports=True)
        reference = BlobRef.from_dict(member["request"])
        objects.put(DirectoryStore(member["objects"]).read(reference))
        members.append((update, reference))
        stores.extend(member["source_objects"])
    window = FeedbackWindow(f"updates_{members[0][0]:08d}_{members[-1][0]:08d}",
                            historical_manifest, tuple(members))
    result = {"request": objects.put(window.to_bytes()).as_dict(), "objects": str(objects.directory),
        "source_objects": list(dict.fromkeys(stores)), "new_rollouts": 0, "new_scores": 0,
        "updates": [update for update, _ in members]}
    payload = canonical_json(result)
    if not install_exclusive(output, "input.json", payload) and (output / "input.json").read_bytes() != payload:
        raise ValueError("feedback output belongs to another recorded window")
    return result


@dataclass(frozen=True, slots=True)
class FeedbackWindow:
    round_id: str
    training_manifest: BlobRef
    # Human update numbers and existing single-update request references.
    members: tuple[tuple[int, BlobRef], ...]
    schema: str = "behave.train-feedback-window/1"

    def __post_init__(self):
        version(self.schema, "behave.train-feedback-window/1")
        text(self.round_id, "feedback window id")
        if type(self.training_manifest) is not BlobRef or not self.members:
            raise ValueError("feedback window requires train membership and updates")
        updates = [step for step, _ in self.members]
        if (any(type(step) is not int or step < 1 for step in updates)
                or updates != list(range(updates[0], updates[0] + len(updates)))
                or any(type(ref) is not BlobRef for _, ref in self.members)
                or len({ref for _, ref in self.members}) != len(self.members)):
            raise ValueError("feedback window requires consecutive distinct updates and requests")

    def to_bytes(self):
        return canonical_json({"schema": self.schema, "round_id": self.round_id,
            "training_manifest": self.training_manifest.as_dict(),
            "members": [{"update": step, "request": ref.as_dict()} for step, ref in self.members]})

    @classmethod
    def from_bytes(cls, payload):
        row = fields(parse_json(payload), {"schema", "round_id", "training_manifest", "members"})
        members = [fields(item, {"update", "request"}) for item in row["members"]]
        result = cls(row["round_id"], BlobRef.from_dict(row["training_manifest"]),
            tuple((item["update"], BlobRef.from_dict(item["request"])) for item in members), row["schema"])
        if result.to_bytes() != payload:
            raise RecordError("record-not-canonical", "feedback window must be canonical")
        return result


def window_requests(window, reader):
    from .feedback import FeedbackRequest

    seen = set()
    for update, reference in window.members:
        request = FeedbackRequest.from_bytes(read_verified(reader, reference))
        if request.training_manifest != window.training_manifest:
            raise RecordError("feedback-membership-mismatch", "window mixes training memberships")
        if seen.intersection(request.reports):
            raise RecordError("feedback-report-repeated", "an update report cannot appear twice in a window")
        seen.update(request.reports)
        yield update, reference, request


def _summary(facts):
    """Full window counts stay visible even when candidate evidence is sharded."""
    observed = {row["id"]: row for row in facts["observations"]}
    rewards = Counter({name: 0 for name in ("different_rewards", "all_1", "other_same_reward")})
    unresolved = 0
    incomplete = sum(not group["complete"] for group in facts["tasks"])
    known = {(group["task_id"], group["context"]) for group in facts["tasks"]}
    excluded_only = {(row["task_id"], row["context"]) for row in facts["excluded_attempts"]} - known
    for group in facts["tasks"]:
        rows = [observed[key] for key in group["observations"]]
        if any(row["status"] in {"no_result", "pending_judgment"} or any(
                row[name]["status"] in {"sim_no_conclusion", "execution_no_conclusion"}
                for name in ("rtl", "behavior")) for row in rows):
            unresolved += 1
        if group["complete"]:
            values = {row["final_score"] for row in rows}
            rewards["different_rewards" if len(values) > 1 else
                    "all_1" if values == {1} else "other_same_reward"] += 1
    return {"complete_groups": sum(group["complete"] for group in facts["tasks"]),
        "incomplete_groups": incomplete + len(excluded_only),
        "groups_with_unresolved_execution": unresolved,
        "settled_reward_groups": dict(rewards),
        "excluded_attempts": len(facts["excluded_attempts"]),
        "attempts": facts["counts"]["first_attempts"],
        "note": "Counts describe this recorded window, not the whole task pool. Reward categories use "
                "settled training rewards; unresolved execution is an overlapping flag, not a capability diagnosis. "
                "Cancelled and unstarted attempts are excluded, never scored as failures."}


def export_window(window, reader, *, approved_manifest, recorded_reports=False):
    from .feedback import FeedbackBundle, export_feedback, render_brief, require_feedback_membership, select_feedback

    require_feedback_membership(window.training_manifest, approved_manifest, reader,
                                recorded_reports=recorded_reports)
    sources, observations, groups, excluded, contexts, attached = [], [], [], [], {}, {}
    for update, reference, request in window_requests(window, reader):
        bundle = export_feedback(request, reader, approved_manifest=window.training_manifest,
                                 recorded_reports=recorded_reports)
        facts = bundle.facts
        provenance = {"update": update, "source_round": request.round_id,
                      "checkpoint": request.checkpoint.as_dict(), "source_request": reference.as_dict()}
        context_map = {}
        for key, value in facts["contexts"].items():
            value = {**value, **provenance}
            identity = BlobRef.of(canonical_json(value)).sha256
            context_map[key] = identity
            contexts[identity] = value
        def scoped(row):
            return {**row, **provenance, "context": context_map[row["context"]]}
        observations.extend(scoped(row) for row in facts["observations"])
        groups.extend(scoped(row) for row in facts["tasks"])
        excluded.extend(scoped(row) for row in facts["excluded_attempts"])
        sources.append({**provenance, "reports": [ref.as_dict() for ref in request.reports],
                        "summary": _summary(facts)})
        for file in bundle.files:
            if file.reference.path in {"brief.md", "feedback.json"}:
                continue
            prior = attached.setdefault(file.reference.path, file)
            if prior != file:
                raise RecordError("feedback-attachment-conflict", "window evidence paths collide")
    facts = {"schema": "behave.train-feedback/1", "round_id": window.round_id,
        "checkpoint": None, "training_manifest": window.training_manifest.as_dict(),
        "request": BlobRef.of(window.to_bytes()).as_dict(), "window": sources,
        "observations": observations, "tasks": groups, "excluded_attempts": excluded, "contexts": contexts}
    # Reuse the same observation denominators as the existing single-update exporter.
    facts = select_feedback(facts, {row["task_id"] for row in (*observations, *excluded)})
    facts["window_summary"] = _summary(facts)
    payloads = (("feedback.json", canonical_json(facts)), ("brief.md", render_brief(facts)),
        ("window.json", canonical_json({"updates": sources, "summary": facts["window_summary"]})))
    files = tuple(FileData(FileRef(path, BlobRef.of(data)), data) for path, data in payloads)
    return FeedbackBundle(files + tuple(attached[path] for path in sorted(attached)))
