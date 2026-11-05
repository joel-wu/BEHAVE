"""Construction outcomes for the next acquisition, not another review pass.

Read small coordinator/review records, never model traces or implementation
blobs. Only acquisition receives this private history; solver notes remain a
separate, explicitly selected experience view.
"""

from dataclasses import replace

from behave.agent.journal import load_chain
from behave.core.artifacts import BlobRef, FileData, FileRef, read_verified
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json, version
from behave.data.supervisor import ConstructionPlan

HISTORY_PATH = "history/construction.json"


def history_file(reader, reference):
    data = read_verified(reader, reference)
    version(parse_json(data)["schema"], "behave.construction-history/1")
    return FileData(FileRef(HISTORY_PATH, reference), data)


def _record(reader, reference):
    return parse_json(read_verified(reader, BlobRef.from_dict(reference)))


def _review(reader, reference):
    row = _record(reader, reference)
    return {key: row[key] for key in ("assessments", "findings", "builder_feedback", "implementer_feedback")
            if key in row}


def _scope(reader, result):
    return {"status": result["status"], "code": result["code"],
        "proposals": [{key: row[key] for key in ("intent", "capabilities", "adaptations")}
                      for ref in result["scopes"] for row in (_record(reader, ref),)],
        "reviews": [_review(reader, ref) for ref in result["reviews"]]}


def _synthesis(reader, head):
    revisions = {}
    # Only the coordinator journal is read. Its artifacts stay in the original
    # store; history does not copy transcripts, B1/B2, or token payloads.
    for event in load_chain(reader, BlobRef.from_dict(head), verify_artifacts=False):
        if event.kind != "phase_settled":
            continue
        payload = thaw_json(event.payload)
        phase, output = payload["phase"], payload["output"]
        row = revisions.setdefault(phase["revision"], {"revision": phase["revision"]})
        if output is None:
            row[phase["role"]] = {"submitted": False}
        elif phase["role"] == "source_review":
            row["review"] = _review(reader, output)
        elif phase["role"] == "reader":
            row["reader_findings"] = _record(reader, output)["findings"]
        elif phase["role"] == "check":
            check = _record(reader, output)["comparison"]["result"]
            row["check"] = {key: check[key] for key in ("score", "code", "origin")}
    return list(revisions.values())


def construction_history(*, reader, round_id, prior, stages, briefs, release):
    """Append this batch's proposals, failures, repairs and admission outcomes."""
    rounds = [] if prior is None else parse_json(history_file(reader, prior).data)["rounds"]
    tasks = {}
    for stage in stages:
        kind, _, task_id = stage["name"].partition(":")
        if kind not in {"scope", "construction"}:
            continue
        task = tasks.setdefault(task_id, {"task_id": task_id})
        result = stage["outcome"]
        if kind == "scope":
            plan = _record(reader, result["plan"])
            task["source_id"] = _record(reader, plan["author"]["inputs"]["source"])["source_id"]
            task["scope"] = _scope(reader, result)
        else:
            plan = _record(reader, result["plan"])
            task["source_id"] = _record(reader, _record(reader, plan["initial"]["scope"])["source"])["source_id"]
            attempts = []
            for child in result["stages"]:
                if child["stage"] == "synthesis":
                    attempts.append({"revisions": _synthesis(reader, child["head"])})
                elif child["stage"] == "scope":
                    events = load_chain(reader, BlobRef.from_dict(child["head"]), verify_artifacts=False)
                    attempts.append({"scope": _scope(reader, _record(reader, events[-1].payload["result"]))})
            task["construction"] = {"status": result["status"], "code": result["code"], "attempts": attempts}
    if release is not None:
        manifest = parse_json(read_verified(reader, release))
        for reference in manifest["metadata"]["dispositions"]:
            decision = _record(reader, reference)
            task = tasks.setdefault(decision["task_id"], {"task_id": decision["task_id"]})
            task["source_id"] = _record(reader, decision["source"])["source_id"]
            task["admission"] = {key: decision[key] for key in ("outcome", "reasons")}
    return canonical_json({"schema": "behave.construction-history/1", "rounds": [*rounds, {
        "round_id": round_id,
        "proposals": [{"proposal_id": key, "source_id": parse_json(brief)["source_id"],
                       "brief": parse_json(brief)} for key, brief in briefs.items()],
        "tasks": list(tasks.values())}]})


def saved_construction(reader, head):
    events = load_chain(reader, head, verify_artifacts=False)
    if not events or events[-1].kind != "construction_finished":
        raise RecordError("construction-not-settled", "resume a completed construction boundary")
    outcome = parse_json(read_verified(reader, BlobRef.from_dict(thaw_json(events[-1].payload)["result"])))
    plan = ConstructionPlan.from_bytes(read_verified(reader, BlobRef.from_dict(outcome["plan"])))
    refs = tuple(dict.fromkeys(ref for event in events for ref in (BlobRef.of(event.to_bytes()), *event.artifacts)))
    return plan, outcome, refs


def construction_handoff(reader, heads, configuration):
    """Retain finished tasks and route only pending tasks to saved-phase repair."""
    retained, repairs = {}, {}
    for task_id, head in heads.items():
        previous, outcome, refs = saved_construction(reader, head)
        if previous.initial.task_id != task_id:
            raise RecordError("construction-task-mismatch", "saved construction belongs to another task")
        if outcome["status"] == "review_ready":
            name = f"construction:{task_id}"
            retained[name] = ({"name": name, "head": head.as_dict(), "outcome": outcome}, refs)
            continue
        synthesis = outcome["stages"][-1]
        if synthesis["stage"] != "synthesis":
            raise RecordError("construction-phase-not-supported", "saved construction has not reached task synthesis")
        from behave.data.synthesis_records import SynthesisPlan, SynthesisResult

        synthesis_head = BlobRef.from_dict(synthesis["head"])
        events = load_chain(reader, synthesis_head, verify_artifacts=False)
        prior_result = SynthesisResult.from_bytes(read_verified(reader,
            BlobRef.from_dict(thaw_json(events[-1].payload)["result"])))
        prior_plan = SynthesisPlan.from_bytes(read_verified(reader, prior_result.plan))
        # Reuse the operator's runtime template, with the same task/source and
        # a fresh call namespace. The existing synthesis continuation checks
        # task, model, guidance and execution-policy compatibility before calls.
        initial = replace(configuration.synthesis.plan, run_id=prior_plan.run_id + "-continued",
                          task_id=task_id, scope=prior_plan.scope, preparation=prior_plan.preparation)
        repairs[task_id] = (replace(configuration.plan, run_id=initial.run_id, initial=initial), synthesis_head)
    return retained, repairs
