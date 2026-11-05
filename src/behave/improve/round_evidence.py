"""Read-only verification of a completed round's existing child receipts."""

from behave.agent.journal import load_chain
from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.data.scope import load_scope_preparation
from behave.task_execution.batch import load_batch_report

from .feedback import FeedbackRequest, export_feedback, read_feedback_request
from .learning_round import load_learning_round
from .library import LibraryIndex
from .materials import MaterialRequest, load_materials
from .round_data import admitted_training_manifest, load_round_data
from .training_pool import expected_training_manifest


def _stage_order_matches(actual, diagnostic, acquisitions, memory, construction, tail, *, parallel_memory=False):
    """Independent task chains may commit in either completion order."""
    offset = 0
    middle = ((memory[:-1], False), ([*memory[-1:], *construction], True)) if parallel_memory else (
        (memory, False), (construction, True))
    for group, parallel in ((diagnostic, False), (acquisitions, True), *middle, (tail, False)):
        observed = actual[offset:offset + len(group)]
        if (sorted(observed) != sorted(group) if parallel else observed != list(group)):
            return False
        offset += len(group)
    if offset != len(actual):
        return False
    positions = {name: index for index, name in enumerate(actual)}
    return all(positions[name] < positions.get("construction:" + name.removeprefix("scope:"), len(actual))
               for name in construction if name.startswith("scope:"))


def acquired_prefix(reader, head, *, verify_artifacts=True):
    """Read an explicitly selected acquisition boundary, not a new diagnosis."""
    events = load_chain(reader, head, verify_artifacts=verify_artifacts)
    if not events or events[0].kind != "round_requested" or events[-1].kind != "round_materials":
        raise RecordError("round-acquisition-incomplete", "reuse requires the frozen-material boundary")
    request = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(events[0].payload)["request"])))
    return events, request


def load_round(reader, head):
    events = load_chain(reader, head)
    if not events or events[0].kind != "round_requested" or events[-1].kind != "round_settled":
        raise RecordError("round-incomplete", "the two-route round is not complete")
    request = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(events[0].payload)["request"])))
    if request["runtime"].get("generation_target") is not None:
        from .generation_batch import load_target_round

        return load_target_round(reader, events, request)
    acquisition_request = request
    if request.get("acquisition_from") is not None:
        prior, acquisition_request = acquired_prefix(reader, BlobRef.from_dict(request["acquisition_from"]))
        if [(e.kind, e.payload) for e in events[1:len(prior)]] != [(e.kind, e.payload) for e in prior[1:]]:
            raise RecordError("round-evidence-mismatch", "reused acquisition records changed")
        while acquisition_request.get("acquisition_from") is not None:
            _, acquisition_request = acquired_prefix(reader, BlobRef.from_dict(acquisition_request["acquisition_from"]))
        current_checkpoint = request["parents"]["task"]["checkpoint"] or request["base_checkpoint"]
        prior_checkpoint = acquisition_request["parents"]["task"]["checkpoint"] or acquisition_request["base_checkpoint"]
        if (current_checkpoint != prior_checkpoint or any(request[key] != acquisition_request[key]
                for key in ("round_id", "library", "training_manifest"))):
            raise RecordError("round-acquisition-context", "reused acquisition belongs to another learning context")
    result = thaw_json(events[-1].payload)
    stages = [thaw_json(event.payload) for event in events if event.kind == "round_stage"]
    by_name = {stage["name"]: stage for stage in stages}
    material_record = next(thaw_json(event.payload) for event in events if event.kind == "round_materials")
    memory = material_record.get("experience")
    pending_memory = material_record.get("experience_pending")
    memory_name = material_record.get("experience_stage", "experience")
    if memory_name not in {"experience", "experience:retry"}:
        raise RecordError("round-stage-order", "unknown memory recovery stage")
    if pending_memory is not None:
        merge = by_name.get(memory_name)
        if merge is None:
            raise RecordError("round-stage-order", "missing scheduled memory consolidation")
        merged = merge["outcome"]
        if merged.get("schema") == "behave.experience-merge/1":
            memory = None if merged["materials"] is None else merged["materials"]["experience"]
            if result.get("experience_pending_changes") != merged["pending_experience"]:
                raise RecordError("round-evidence-mismatch", "completion changed its pending experience")
        elif merged["status"] == "completed":
            memory = merged["materials"]["experience"]
    learning_runtime = {route: dict(request["runtime"][route]) for route in ("task", "raw")}
    # Interpret old receipts as recorded; new rounds keep both views private to
    # Analyst. This reader never changes or launches a historical learning pass.
    if memory is not None and request["runtime"].get("experience_visibility") != "analyst-only":
        from .experience import load_experience
        views = {file.reference.path: file.reference.content
                 for file in load_experience(reader, BlobRef.from_dict(memory))}
        learning_runtime["task"]["solver_experience"] = views["experience/solver.md"].as_dict()
    if result.get("experience") != memory:
        raise RecordError("round-evidence-mismatch", "completion changed its persistent experience")
    if result.get("construction_history") is not None:
        from .construction import history_file

        histories = [thaw_json(event.payload)["history"] for event in events
                     if event.kind == "round_construction_history"]
        if histories != [result["construction_history"]]:
            raise RecordError("round-evidence-mismatch", "completion changed its construction history")
        history_file(reader, BlobRef.from_dict(result["construction_history"]))
    imported_feedback = acquisition_request.get("feedback_from")
    generation_only = request["runtime"].get("generation_only", False)
    required = {"data"} | (set() if generation_only else {"learning"}) | ({"diagnosis"} if imported_feedback is None else set())
    if len(by_name) != len(stages) or not required <= by_name.keys():
        raise RecordError("round-stage-order", "missing or duplicated round stages")
    if (result["round_id"] != request["round_id"] or result["status"] != "completed"
            or result["data"] != by_name["data"]["head"]
            or result["learning"] != (None if generation_only else by_name["learning"]["head"])):
        raise RecordError("round-evidence-mismatch", "completion differs from its recorded stages")
    data = load_round_data(reader, BlobRef.from_dict(result["data"]))
    pool_ref = result.get("training_manifest")
    if pool_ref is not None:
        pool_inputs = tuple(dict.fromkeys((BlobRef.from_dict(request["training_manifest"]),
            *(BlobRef.from_dict(ref) for ref, _ in request["runtime"]["replay_passes"]))))
        admitted = admitted_training_manifest(reader, data.release)
        if admitted is not None:
            pool_inputs = (*pool_inputs, BlobRef.of(admitted.to_bytes()))
        pool = expected_training_manifest(manifests=pool_inputs, release_id=f"{request['round_id']}:training-pool",
                                          reader=reader)
        pool_events = [thaw_json(event.payload) for event in events if event.kind == "round_training_pool"]
        if (pool_ref != BlobRef.of(pool.to_bytes()).as_dict()
                or pool_events != [{"manifest": pool_ref}]):
            raise RecordError("round-evidence-mismatch", "completion changed the cumulative training pool")
    if generation_only:
        if result["parents"] != request["parents"] or "learning" in by_name:
            raise RecordError("round-evidence-mismatch", "generation-only round changed learning state")
    else:
        learning = load_learning_round(reader, BlobRef.from_dict(result["learning"]))
        child_events = load_chain(reader, BlobRef.from_dict(result["learning"]))
        child_request = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(child_events[0].payload)["request"])))
        if (learning != by_name["learning"]["outcome"] or learning["data"] != data.head.as_dict()
                or result["parents"] != learning["parents"] or child_request["parents"] != request["parents"]
                or child_request["base_checkpoint"] != request["base_checkpoint"]
                or child_request["runtime"] != learning_runtime
                or child_request["encoding"] != request["runtime"]["encoding"]
                or child_request["raw_updates"] != request["runtime"]["raw_updates"]):
            raise RecordError("round-evidence-mismatch", "learning differs from this round's runtime or parents")
        passes = [{"manifest": ref, "updates": count} for ref, count in request["runtime"]["replay_passes"]]
        if pool_ref is not None:
            if request["runtime"]["acquired_updates"]:
                passes.append({"manifest": pool_ref, "updates": request["runtime"]["acquired_updates"]})
        else:
            # Read historical new-task-only receipts without changing their meaning.
            manifest = admitted_training_manifest(reader, data.release)
            if manifest is not None:
                passes.append({"manifest": BlobRef.of(manifest.to_bytes()).as_dict(),
                               "updates": request["runtime"]["acquired_updates"]})
        if child_request["task_passes"] != passes:
            raise RecordError("round-evidence-mismatch", "learning changed the admitted/replay task selection")

    material = load_materials(reader, data.materials)
    context = MaterialRequest.from_bytes(read_verified(reader, material.request))
    checkpoint = BlobRef.from_dict(request["parents"]["task"]["checkpoint"] or request["base_checkpoint"])
    runtime = request["runtime"]
    acquisition_runtime = acquisition_request["runtime"]
    feedback_record = next(thaw_json(event.payload) for event in events if event.kind == "round_feedback")
    assignment_records = acquisition_runtime["acquisitions"]
    if acquisition_runtime.get("acquisition_limits") is not None:
        from behave.agent.process_model import ModelCommand

        from .acquisition import AcquisitionConfiguration, acquisition_assignments, feedback_task_ids

        snapshot = WorkspaceSnapshot.from_bytes(read_verified(reader, BlobRef.from_dict(feedback_record["feedback"])))
        facts = parse_json(read_verified(reader, snapshot.file("feedback.json").content))
        templates = tuple(AcquisitionConfiguration.from_bytes(canonical_json(row)) for row in assignment_records)
        expected = acquisition_assignments(templates, facts, feedback_task_ids(facts, include_incomplete=True),
            library=BlobRef.from_dict(acquisition_request["library"]),
            feedback=BlobRef.from_dict(feedback_record["feedback"]), round_id=acquisition_request["round_id"],
            policy=acquisition_runtime["model_policy"], model=ModelCommand.from_dict(acquisition_runtime["model"]),
            limits=acquisition_runtime["acquisition_limits"])
        assignment_records = [parse_json(config.to_bytes()) for config in expected]
        if feedback_record.get("assignments") != assignment_records:
            raise RecordError("round-evidence-mismatch", "Analyst assignments differ from recorded workload policy")
    acquisitions = [f"acquisition:{index}" for index in range(len(assignment_records))]
    if any(name not in by_name for name in acquisitions):
        raise RecordError("round-stage-order", "missing assigned Analyst stage")
    if acquisition_runtime.get("acquisition_limits") is not None:
        for name, assigned in zip(acquisitions, assignment_records):
            child = load_chain(reader, BlobRef.from_dict(by_name[name]["head"]))
            config = read_verified(reader, BlobRef.from_dict(thaw_json(child[0].payload)["configuration"]))
            if config != canonical_json(assigned):
                raise RecordError("round-evidence-mismatch", "Analyst changed its assigned input or proposal target")
    completed_acquisitions = [name for name in acquisitions
                              if by_name[name]["outcome"]["status"] == "completed"]
    prior_memory = (acquisition_runtime["acquisitions"][0]["job"]["inputs"].get("experience")
                    if acquisitions else None)
    memory_stages = [name for name in ("experience", "experience:retry") if name in by_name]
    template = acquisition_runtime["acquisitions"][0]["job"]["inputs"] if acquisitions else {}
    incremental = template.get("experience_mode") == "delta-v1"
    memory_key = "experience_delta" if incremental else "experience"
    backlog = tuple(BlobRef.from_dict(ref) for ref in template.get("pending_experience", ()))
    proposals = tuple(dict.fromkeys((*backlog, *(BlobRef.from_dict(by_name[name]["outcome"]["materials"][memory_key])
        for name in completed_acquisitions if memory_key in by_name[name]["outcome"]["materials"]))))
    for name in acquisitions:
        if name not in completed_acquisitions:
            from .acquisition import load_acquisition_report
            report = load_acquisition_report(reader, BlobRef.from_dict(by_name[name]["head"]))
            if report != by_name[name]["outcome"] or report["status"] != "unfinished":
                raise RecordError("round-evidence-mismatch", "acquisition failure is not a terminal role outcome")
    if memory_stages:
        from .acquisition import AcquisitionConfiguration, load_acquisition_report
        memory_head = BlobRef.from_dict(by_name[memory_name]["head"])
        memory_report = load_acquisition_report(reader, memory_head)
        memory_events = load_chain(reader, memory_head)
        memory_config = AcquisitionConfiguration.from_bytes(read_verified(reader,
            BlobRef.from_dict(thaw_json(memory_events[0].payload)["configuration"])))
        if pending_memory is not None and (BlobRef.of(memory_config.to_bytes()).as_dict() != pending_memory
                or material_record.get("experience") != prior_memory):
            raise RecordError("round-evidence-mismatch", "parallel memory changed its frozen job or prior notes")
        if incremental:
            selected_memory = (None if memory_report["materials"] is None else memory_report["materials"]["experience"])
            if (memory_config.job.inputs.experience_mode != "merge-v1"
                    or memory_config.job.inputs.experience_policy != template.get("experience_policy")):
                raise RecordError("round-evidence-mismatch", "memory consolidation changed its policy")
        else:
            selected_memory = (memory_report["materials"]["experience"]
                               if memory_report["status"] == "completed" else prior_memory)
        if (memory_config.job.inputs.experience_proposals != proposals
                or memory_report != by_name[memory_name]["outcome"]
                or memory_report["status"] not in {"completed", "unfinished"}
                or selected_memory != memory):
            raise RecordError("round-evidence-mismatch", "memory consolidation changed its inputs or result")
        if memory_name == "experience:retry":
            failed = by_name.get("experience")
            if failed is None or failed["outcome"]["status"] != "interrupted":
                raise RecordError("round-evidence-mismatch", "memory retry has no interrupted predecessor")
            old_head = BlobRef.from_dict(failed["head"])
            old_events = load_chain(reader, old_head)
            if (load_acquisition_report(reader, old_head) != failed["outcome"] or
                    old_events[0].payload["configuration"] != BlobRef.of(memory_config.to_bytes()).as_dict()):
                raise RecordError("round-evidence-mismatch", "memory retry changed its saved inputs")
    elif (incremental and proposals) or memory != (proposals[0].as_dict() if proposals and not incremental else prior_memory):
        raise RecordError("round-evidence-mismatch", "round memory differs from acquisition")
    if (context.round_id != request["round_id"] or context.checkpoint != checkpoint
            or context.library.as_dict() != request["library"]
            or context.model_transport != acquisition_runtime["model_policy"]["transport"]
            or any(name not in by_name for name in acquisitions)
            or context.acquisitions != tuple(BlobRef.from_dict(by_name[name]["head"])
                                             for name in completed_acquisitions)):
        raise RecordError("round-evidence-mismatch", "common material differs from the round's acquisitions")
    feedback_record = next(thaw_json(event.payload) for event in events if event.kind == "round_feedback")
    diagnostic_names = ["diagnosis"] if imported_feedback is None else []
    if imported_feedback is None and "request" in feedback_record and not acquisition_runtime["diagnosis"].get("score_on_submit", True):
        diagnostic_names.append("diagnosis_scoring")
        if "diagnosis_scoring" not in by_name:
            raise RecordError("round-stage-order", "deferred diagnosis requires its scoring stage")
        scoring = by_name["diagnosis_scoring"]
        report = load_batch_report(reader, BlobRef.from_dict(scoring["head"]))
        prior = load_batch_report(reader, BlobRef.from_dict(by_name["diagnosis"]["head"]))
        scoring_events = load_chain(reader, BlobRef.from_dict(scoring["outcome"]["task_head"]))
        scoring_plan = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(report.summary)["plan"])))
        prior_plan = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(prior.summary)["plan"])))
        expected_request = {"source": prior.head.as_dict(), "backend": scoring_plan["judge_backend"], "policy": None}
        if (tuple(event.kind for event in scoring_events) != ("rejudgment_started",)
                or thaw_json(scoring_events[0].payload) != expected_request
                or scoring_plan["jobs"] != prior_plan["jobs"]
                or not scoring_plan.get("score_on_submit", True)
                or scoring["outcome"] != {**report.as_dict(), "task_head": scoring["outcome"]["task_head"]}):
            raise RecordError("round-evidence-mismatch", "diagnostic scoring changed its saved batch or policy")
    if imported_feedback is not None:
        if context.feedback_request != BlobRef.from_dict(imported_feedback):
            raise RecordError("round-evidence-mismatch", "imported feedback producer changed")
        feedback_request = read_feedback_request(read_verified(reader, BlobRef.from_dict(imported_feedback)))
        if feedback_request.training_manifest.as_dict() != request["training_manifest"]:
            raise RecordError("round-evidence-mismatch", "imported feedback changed training membership")
    else:
        feedback_request = FeedbackRequest(context.round_id, checkpoint, BlobRef.from_dict(request["training_manifest"]),
            context.model_transport, (BlobRef.from_dict(by_name[diagnostic_names[-1]]["head"]),),
            schema="behave.train-feedback-request/2" if "request" in feedback_record else "behave.train-feedback-request/1")
    if "request" in feedback_record and read_feedback_request(
            read_verified(reader, BlobRef.from_dict(feedback_record["request"]))) != feedback_request:
        raise RecordError("round-evidence-mismatch", "feedback request differs from the diagnostic stage")
    feedback = export_feedback(feedback_request, reader, approved_manifest=feedback_request.training_manifest)
    if BlobRef.of(WorkspaceSnapshot(tuple(file.reference for file in feedback.files)).to_bytes()) != context.feedback:
        raise RecordError("round-evidence-mismatch", "acquisition feedback differs from training-side diagnosis")

    from behave.data.scope import ScopePlan

    from .materials import construction_briefs, construction_proposals

    direct = runtime["construction"]["scope"] is None
    briefs = (construction_proposals if direct else construction_briefs)(reader, data.materials)
    expected_names = [*diagnostic_names, *acquisitions, *memory_stages]
    packages = []
    selection = result.get("construction_selection")
    if runtime.get("construction_target") is not None and selection is None:
        raise RecordError("round-evidence-mismatch", "targeted construction has no frozen selection")
    selected_names = None if selection is None else {row["name"] for row in selection["selected"]}
    unstarted = set() if selection is None else set(selection["unstarted"])
    if selection is not None:
        selections = [thaw_json(event.payload) for event in events if event.kind == "round_construction_selection"]
        construction_names = {s["name"] for s in stages if s["name"].startswith("construction:")}
        if (selections != [selection] or selection["target"] != runtime.get("construction_target")
                or len(selected_names) != len(selection["selected"])
                or not selected_names <= construction_names or unstarted & construction_names
                or selection["target_met"] != (len(selected_names) >= selection["target"])
                or selection["deferred"] != [s for s in stages
                    if s["name"] in construction_names - selected_names]):
            raise RecordError("round-evidence-mismatch", "construction selection changed its completed/pending work")
        for row in selection["selected"]:
            if by_name[row["name"]]["outcome"].get("package") != row["package"]:
                raise RecordError("round-evidence-mismatch", "selected package differs from its construction")
    index = LibraryIndex(reader, context.library)
    if direct:
        from behave.data.drafts import load_scope
        from behave.data.supervisor import ConstructionPlan

        requested_names = {f"construction:{context.round_id}-{key}" for key in briefs}
        if not unstarted <= requested_names:
            raise RecordError("round-evidence-mismatch", "deferred proposal is outside this acquisition")

        for key, brief in briefs.items():
            name = f"construction:{context.round_id}-{key}"
            if name in unstarted:
                continue
            expected_names.append(name)
            construction = load_chain(reader, BlobRef.from_dict(by_name[name]["head"]))
            outcome = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(construction[-1].payload)["result"])))
            plan = ConstructionPlan.from_bytes(read_verified(reader, BlobRef.from_dict(outcome["plan"])))
            objective, _, _ = load_scope(reader, plan.initial.scope)
            proposal = parse_json(brief)
            identifier = proposal["source_id"]
            if (plan.scope_review is not None or plan.initial.preparation is not None
                    or objective.source != BlobRef.of(index.source_record(
                        identifier, proposal.get("supporting_source_ids", ())).to_bytes())
                    or objective.brief != BlobRef.of(brief) or outcome != by_name[name]["outcome"]):
                raise RecordError("round-evidence-mismatch", "Builder handoff differs from Analyst proposal")
            prior = request.get("construction_from", {}).get(plan.initial.task_id)
            if prior is not None:
                from .construction import saved_construction

                _, previous, _ = saved_construction(reader, BlobRef.from_dict(prior))
                if previous["status"] == "review_ready":
                    if by_name[name]["head"] != prior or outcome != previous:
                        raise RecordError("round-evidence-mismatch", "retained construction changed its completed result")
                elif construction[0].payload.get("continuation_from") != previous["stages"][-1]["head"]:
                    raise RecordError("round-evidence-mismatch", "repair did not resume its selected construction")
            if outcome["package"] is not None and (selected_names is None or name in selected_names):
                packages.append(outcome["package"])
    for identifier, unit in index.units.items():
        if direct or unit.content not in material.documents or identifier not in briefs:
            continue
        for proposal in range(runtime["proposals_per_source"]):
            task = f"{context.round_id}-{identifier}-{proposal}"
            name = f"scope:{task}"
            expected_names.append(name)
            if name not in by_name:
                raise RecordError("round-stage-order", "missing requested scope proposal")
            scoped, _ = load_scope_preparation(reader, BlobRef.from_dict(by_name[name]["head"]))
            scope_plan = ScopePlan.from_bytes(read_verified(reader, scoped.plan))
            supporting = parse_json(briefs[identifier]).get("supporting_source_ids", ())
            if (scope_plan.author.inputs.source != BlobRef.of(index.source_record(identifier, supporting).to_bytes())
                    or scope_plan.author.inputs.brief != BlobRef.of(briefs[identifier])
                    or (None if scope_plan.author.inputs.parent is None else scope_plan.author.inputs.parent.as_dict())
                        != request.get("scope_parents", {}).get(task)):
                raise RecordError("round-evidence-mismatch", "construction lost the acquired source or objective")
            if scoped.status == "review_ready":
                name = f"construction:{task}"
                expected_names.append(name)
                if name not in by_name:
                    raise RecordError("round-stage-order", "reviewed scope has no construction disposition")
                construction = load_chain(reader, BlobRef.from_dict(by_name[name]["head"]))
                terminal = thaw_json(construction[-1].payload)
                outcome = parse_json(read_verified(reader, BlobRef.from_dict(terminal["result"])))
                if outcome != by_name[name]["outcome"]:
                    raise RecordError("round-evidence-mismatch", "construction disposition changed")
                if outcome["package"] is not None:
                    packages.append(outcome["package"])
    leading = len(diagnostic_names) + len(acquisitions) + len(memory_stages)
    if not _stage_order_matches([stage["name"] for stage in stages], diagnostic_names,
            acquisitions, memory_stages, expected_names[leading:],
            ["data", *([] if generation_only else ["learning"])], parallel_memory=pending_memory is not None):
        raise RecordError("round-stage-order", "round omitted or reordered a requested stage")
    data_events = load_chain(reader, data.head)
    data_request = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(data_events[0].payload)["request"])))
    publication = data_request["publication"]
    # ReleaseRequest canonicalizes package order independently of task dispatch.
    expected_packages = sorted(packages, key=lambda ref: ref["sha256"]) or None
    if expected_packages != (None if publication is None else publication["packages"]):
        raise RecordError("round-evidence-mismatch", "publication omitted or added a constructed package")

    releases = [thaw_json(event.payload) for event in events if event.kind == "round_model_released"]
    kinds = [event.kind for event in events]
    before_material = len(memory_stages) if pending_memory is None else len(memory_stages) - 1
    expected_kinds = (["round_requested"] + ["round_stage"] * len(diagnostic_names) + ["round_feedback"]
        + ["round_stage"] * (len(acquisitions) + before_material) + ["round_materials"]
        + ["round_stage"] * (len(expected_names) - len(acquisitions) - before_material - len(diagnostic_names) + 1)
        + (["round_construction_history"] if result.get("construction_history") is not None else [])
        + (["round_model_retained"] if runtime.get("keep_serving") else ["round_model_released"] * len(releases))
        + (["round_training_pool"] if pool_ref is not None else [])
        + ([] if generation_only else ["round_stage"]) + ["round_settled"])
    if selection is not None:
        # Insert the frozen selection after construction, before publication.
        expected_kinds.insert(3 + len(expected_names), "round_construction_selection")
    retained = runtime.get("keep_serving", False)
    valid_release = (generation_only and not releases if retained else
                     bool(releases) and releases[-1].get("cleanup_complete") is True
                     and not any(release.get("cleanup_complete") is True for release in releases[:-1]))
    if kinds != expected_kinds or not valid_release:
        raise RecordError("round-stage-order", "native learning requires a settled model-release receipt")
    if (feedback_record["feedback"] != context.feedback.as_dict()
            or material_record["materials"] != data.materials.as_dict()):
        raise RecordError("round-evidence-mismatch", "shared material/feedback records changed")
    return result
