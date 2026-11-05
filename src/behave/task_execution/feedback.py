"""Model-facing comparison feedback from named public Sim diagnostics.

No execution, verdict reconstruction or traversal of private evidence happens
here. Callers retain responsibility for deciding which role may see this view.
"""

from behave.core.artifacts import BlobRef, ContentReader, FileData, FileRef, read_verified
from behave.core.records import parse_json

from .judgment import ComponentJudgment


def _failure_details(image):
    """Actionable source diagnostics, not stdout/stderr or private evidence."""
    detail = (image.get("reason") or {}).get("detail") or {}
    messages = [detail[key] for key in ("worker_detail", "detail")
                if isinstance(detail.get(key), str) and detail[key]]
    return messages + list(detail.get("calls", []))


def _failure_text(image):
    """First public source error. Complete diagnostics and logs stay in files."""
    context = "/".join(str(image[key]) for key in ("origin", "phase") if image.get(key))
    reason = image.get("reason") or {}
    code = image.get("code", reason.get("kind", "execution failure"))
    message = f"{code} ({context})" if context else str(code)
    message += f": {reason['why']}" if reason.get("why") else ""
    detail = reason.get("detail") or {}
    location = str(detail.get("file", ""))
    if detail.get("line"):
        location += f":{detail['line']}" if location else f"line {detail['line']}"
    if location:
        message += f" [{location}]"
    errors = _failure_details(image)
    if detail.get("error"):
        message += f". {detail['error']}"
    if errors:
        message += f". {errors[0]}"
    if reason.get("kind") in {"multiclock-state-aliased", "multiclock-state-cyclic"} \
            and detail.get("path") and detail.get("first_path"):
        message += f". Shared state paths: {detail['first_path']} and {detail['path']}"
    return message


def _case_preview(observation):
    """One example per failure kind; complete differences stay in observations."""
    comparison = observation.get("comparison") or {}
    lines = []
    faults = comparison.get("faults", [])
    count = comparison.get("fault_count", len(faults))
    if count:
        line = f"Output mismatches: {count}."
        if faults:
            fault = faults[0]
            line += (f" First ({fault['kind']}), output {fault['position']}:"
                     f" expected {fault['expected']}, observed {fault['observed']}.")
        lines.append(line)
    faults = comparison.get("memory_faults", [])
    count = comparison.get("memory_fault_count", len(faults))
    if count:
        line = f"Final-memory mismatches: {count}."
        if faults:
            fault = faults[0]
            line += (f" First, {fault['memory']}[{fault['address']}]:"
                     f" expected {fault['expected']}, observed {fault['observed']}.")
        lines.append(line)
    if observation.get("safety_hits"):
        hit = observation["safety_hits"][0]
        lines.append(f"Safety failure at slot {hit['slot']}: {', '.join(hit['goals'])}.")
    if observation.get("failure") is not None:
        lines.append(_failure_text(observation["failure"]))
    return lines


def _report(result, documents, *, diagnostics_visible):
    label = "UNAVAILABLE" if result.score is None else "PASS" if result.score == 1 else "FAIL"
    overview = [label + "."]
    if not diagnostics_visible:
        return overview[0], "# Comparison report\n\n" + overview[0] + "\n"
    baseline = result.detail.get("baseline")
    if baseline is not None:
        progress = (f"{baseline['completed']}/{baseline['generated']} completed"
                    if baseline.get("generated") is not None else baseline["status"].replace("_", " "))
        if baseline.get("generated") is not None and baseline["status"] != "completed":
            progress += f" ({baseline['status'].replace('_', ' ')})"
        overview.append(f"Baseline: {progress}.")
        if baseline.get("search_generated"):
            overview.append(f"Search-generated tests: {baseline['search_generated']}.")
    for phase in result.detail.get("replay_phases", ()):
        overview.append(f"{phase['phase'].capitalize()} replay: "
                        f"{phase['completed_runs']}/{phase['requested_runs']} completed, "
                        f"{phase['input_transactions']} planned input transactions.")
    if result.elapsed_seconds is not None:
        overview.append(f"Elapsed: {result.elapsed_seconds:.2f} s.")
    image = parse_json(documents["summary.json"]) if "summary.json" in documents else {}
    if "run_count" in image:
        completion = (" (complete)" if image["complete"] else " (incomplete)") if "complete" in image else ""
        overview.append(f"Tests: {image['run_count']}{completion}.")
    if image.get("affected_runs"):
        overview.append(f"Affected tests: {image['affected_runs']}.")
    if image.get("interrupted_stage"):
        overview.append(f"Interrupted stage: {image['interrupted_stage']}.")
    tool = image.get("interrupted_tool") or {}
    if tool.get("role"):
        status = f" ({tool['status']})" if tool.get("status") else ""
        elapsed = (f" Tool-stage wall time: {tool['wall_seconds']:.2f} s (including admission/setup)."
                   if tool.get("wall_seconds") is not None else "")
        overview.append(f"Interrupted tool: {tool['role']}{status}.{elapsed}")
    if result.origin is not None or result.score is None:
        origin = f" ({result.origin})" if result.origin is not None else ""
        overview.append(f"Reason: {result.code}{origin}.")

    sections, first = [], None
    if "search.json" in documents:
        search = parse_json(documents["search.json"])
        overview.append(f"Search: {search['status'].replace('_', ' ')}.")
        for direction in search["directions"]:
            name = f"{direction['provider'].capitalize()} search ({direction['direction']})"
            status = direction["status"].replace("_", " ")
            probes = direction["probes_started"]
            count = "probe count unavailable" if probes is None else f"{probes} probes started"
            line = (f"{name}: {status}; {count}; "
                    f"{direction['generated_stimuli']} new stimuli.")
            reasons = [*direction["refusals"]]
            if direction.get("failure"):
                reasons.append(direction["failure"])
            if reasons:
                line += " Reasons: " + ", ".join(dict.fromkeys(row["code"] for row in reasons)) + "."
            overview.append(line)
            sections.extend([f"## {name}", f"Stop reason: {direction['termination']}."])
            for reason in reasons:
                text = f"{reason['code']} ({reason['count']}): {reason.get('why', '')}".rstrip(": ")
                detail = reason.get("detail", {})
                if detail:
                    text += " " + "; ".join(f"{key}: {value}" for key, value in detail.items())
                sections.append(text)
        if search["status"] in ("not_run", "limited"):
            overview.append("Search was not fully available; the verdict covers only the tests executed.")
        sections.append("Search status is separate from functional correctness. "
                        "No new stimulus does not prove equivalence. [Search diagnostics](search.json)")
    if "failure.json" in documents:
        failure = parse_json(documents["failure.json"])
        first = _failure_text(failure)
        sections.extend(["## Execution failure", first, *_failure_details(failure)[1:],
                         "[Diagnostic](failure.json)"])
    for run in image.get("runs", []):
        name = run["observations"]
        preview = _case_preview(parse_json(documents[name])) if name in documents else []
        if first is None and preview:
            first = f"Test {run['index']}: {preview[0]}"
        sections.extend([f"## Test {run['index']}", *preview])
        # A link in summary.json does not authorize reads from private evidence.
        links = [f"[{label}]({run[key]})" for key, label in (
            ("stimulus", "Inputs and schedule"), ("observations", "Observations and all differences"))
            if run[key] in documents]
        sections.append(" · ".join(links) if links else "Details were not exported.")
    if image.get("runs_omitted"):
        sections.append(f"Affected tests omitted by the recorded export: {image['runs_omitted']}.")
    if "failure.json" in documents or image.get("runs"):
        sections.append("Previews show the first recorded failure of each kind. "
                        "Linked files retain all exported inputs and observations.")
    if result.score is None:
        sections.append(f"Retryable: {'yes' if result.retryable else 'no'}.")
    diagnostic_status = result.detail.get("diagnostics_status")
    if diagnostic_status and diagnostic_status != "recorded":
        overview.append(f"Diagnostic export: {diagnostic_status}. Detailed feedback may be unavailable.")
    elif not documents:
        sections.append("No detailed diagnostics were recorded.")

    summary = " ".join((*overview, *((f"First failure: {first}",) if first else ())))
    report = "\n\n".join(("# Comparison report", "\n".join(overview), *sections)) + "\n"
    return summary, report


def comparison_feedback(result: ComponentJudgment, reader: ContentReader, *,
                        prefix: str = "", diagnostics_visible: bool = True) -> tuple[dict, tuple[FileData, ...]]:
    """Return a short reply and named files with report.md as the reading entry.

    ``prefix`` is the caller's workspace-relative mount directory. The report
    path in the reply includes it; native diagnostic bytes and their relative
    links remain unchanged. Only ``result.diagnostics`` are read, never evidence,
    arbitrary detail fields, source files or internal reference state.

    The first failure is a preview of the first exported affected run, not a
    newly minimized counterexample. All exported cases and observations remain
    available. Missing historical timings are omitted and missing diagnostics
    are reported, not reconstructed from private evidence.

    Hidden-reference tools set ``diagnostics_visible=False``: the same report
    entry contains only the verdict, without diagnostic reads or new disclosures.
    """
    root = prefix.rstrip("/")

    def path(name):
        return f"{root}/{name}" if root else name

    diagnostics = result.diagnostics if diagnostics_visible else ()
    files = tuple(FileData(FileRef(path(file.path), file.content), read_verified(reader, file.content))
                  for file in diagnostics)
    documents = {original.path: copied.data for original, copied in zip(diagnostics, files)}
    summary, report = _report(result, documents, diagnostics_visible=diagnostics_visible)
    content = {
        "status": "no_result" if result.score is None else "compared",
        "matched": None if result.score is None else result.score == 1,
        "summary": summary,
        "report": path("report.md"),
    }
    if "search.json" in documents:
        content["search"] = parse_json(documents["search.json"])
    payload = report.encode("utf-8")
    rendered = FileData(FileRef(content["report"], BlobRef.of(payload)), payload)
    return content, (*files, rendered)
