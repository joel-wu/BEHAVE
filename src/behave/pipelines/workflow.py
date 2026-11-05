"""External-method rollout, frozen RTL submission and independent judgment.

Orchestration is upstream-owned. Model transport, journals, sandbox execution,
artifact capture and the final judge are shared with ordinary Eval.
"""

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import traceback

from behave.agent.attempt import AttemptInterrupted
from behave.agent.journal import AttemptJournal
from behave.agent.workspace import Workspace
from behave.core.artifacts import BlobRef, read_verified
from behave.core.paths import scratch_root
from behave.core.records import canonical_json, thaw_json
from behave.task_execution.capture import load_submission, submission_tool
from behave.task_execution.judge import judge_submission

from .generation import RoleCalls
from .public_inputs import chipmate_interface, question, rtl_interface
from .sandbox import PipelineSandbox
from .upstream import module, source_root


def run(*, method, settings, task, spec, backend_factory, directory, objects,
        model_pool=None, control=None, cpu_pool=None, cpu_resources=None):
    """Caller supplies a local directory; no scoring or provider choice is implicit."""
    if method not in ("chipmate", "mage"):
        raise ValueError("external method must be chipmate or mage")
    directory = Path(directory)
    prompt = question(task)
    top, _ = rtl_interface(prompt)
    input_ref = objects.put(canonical_json({
        "method": method, "settings": asdict(settings), "attempt": spec.as_dict(),
        "task": task.task_id, "release": task.release.as_dict(), "revision": task.revision.as_dict(),
        "question": prompt,
    }))
    journal = AttemptJournal(directory / "workflow", objects)
    events = journal.load()
    if events:
        if events[0].payload["inputs"] != input_ref.as_dict():
            raise ValueError("external workflow inputs changed in an existing run directory")
        if events[-1].kind == "external_finished":
            return thaw_json(events[-1].payload)
        # A lost upstream Python object is not a resumable method checkpoint.
        # Preserve partial evidence and let the operator request a new attempt.
        raise AttemptInterrupted("external-workflow-interrupted-use-new-attempt")

    sources = []
    upstream = source_root(method)
    package = upstream / ("src/mage" if method == "mage" else "chipmate")
    for path in sorted(package.rglob("*.py")):
        sources.append({"path": str(path.relative_to(upstream)), "content": objects.put(path.read_bytes()).as_dict()})
    source_ref = objects.put(canonical_json(sources))
    start = journal.append((), "external_started", {"inputs": input_ref.as_dict(), "source": source_ref.as_dict()},
                           (input_ref, source_ref, *(BlobRef.from_dict(row["content"]) for row in sources)))
    calls = RoleCalls(directory / "roles", objects, spec, backend_factory, model_pool=model_pool, control=control)
    sandbox = PipelineSandbox(directory / "commands", objects, cpu_pool=cpu_pool, cpu_resources=cpu_resources,
                              cancelled=(lambda: False) if control is None else control.cancelled)
    selected = None
    upstream_result = None
    status, error = "completed", None
    submission_ref = None
    submission_evidence = ()
    try:
        if method == "chipmate":
            from .chipmate import run as chipmate_run

            _, interface = chipmate_interface(prompt)
            upstream_result = asdict(chipmate_run(task_id=task.task_id, question=prompt, interface=interface,
                                                 calls=calls, sandbox=sandbox, settings=settings))
            # Upstream evaluates the first module under a fixed name. Restore
            # only that name for submission to the explicitly declared task top.
            selected = module("chipmate", "cross_verify").normalize_module_name(upstream_result["verilog"], top)
        else:
            from .mage import run as mage_run

            with TemporaryDirectory(prefix="behave-mage-", dir=scratch_root()) as temporary:
                upstream_result = mage_run(question=prompt, workspace=Path(temporary), calls=calls,
                                           sandbox=sandbox, settings=settings)
            selected = upstream_result["verilog"]
            if upstream_result["upstream_error"] is not None:
                raise RuntimeError(upstream_result["upstream_error"])
        if selected:
            workspace = Workspace(objects)
            workspace.write("rtl.sv", selected.encode("utf-8"))
            capture = submission_tool(task, setting="rtl_only").handler(workspace, {"rtl": {
                "files": ["rtl.sv"], "entry_file": "rtl.sv", "entry_symbol": top}})
            submission_ref = BlobRef.from_dict(thaw_json(capture.content["submission"]))
            submission_evidence = capture.artifacts
        else:
            status = "no_submission"
    except AttemptInterrupted as exc:
        status, error = "interrupted", str(exc)
    except Exception:
        # A failed external workflow still owns the calls already made. Return
        # their evidence/accounting to the batch report, without assigning zero
        # correctness or silently resampling the method.
        status, error = "execution_error", traceback.format_exc()
    finally:
        # Even an adapter bug must leave the already-paid role and tool evidence.
        heads = calls.evidence()
        receipts = tuple(item for item in sandbox.executions if item is not None)
        cost = calls.accounting()
        evidence = tuple(ref for _, closure in heads for ref in closure) + tuple(ref for group in receipts for ref in group)
        snapshot = {
            "roles": [{"path": str(child.directory.relative_to(directory)), "head": head.as_dict() if head else None}
                      for child, (head, _) in zip(calls.journals, heads)],
            "commands": [[ref.as_dict() for ref in group] for group in receipts],
            "cost": dict(cost.cost_by_currency()), "unpriced_dispatches": cost.unpriced_dispatches,
            "unresolved_dispatches": cost.unresolved_dispatches,
        }
        checkpoint = journal.append_after(start, "external_evidence", snapshot, evidence)
    result_ref = objects.put(canonical_json(upstream_result))
    result = {"method": method, "task": task.task_id, "status": status, "error": error,
              "submission": None if submission_ref is None else submission_ref.as_dict(),
              "upstream_result": result_ref.as_dict(), **snapshot}
    journal.append_after(checkpoint, "external_finished", result, (result_ref, *submission_evidence))
    return result


def score(*, task, result, directory, objects, backend, admission=None, cancelled=lambda: False):
    """No model invocation. Same submission/judge path, including cold replay.

    Use a new judgment directory when intentionally changing Sim/policy. An
    existing identical judgment is read by the shared journal replay machinery.
    """
    if result["submission"] is None:
        return None
    ref = BlobRef.from_dict(result["submission"])
    load_submission(objects, ref)
    return judge_submission(task=task, submission=ref,
                            journal=AttemptJournal(Path(directory), objects), backend=backend,
                            admission=admission, cancelled=cancelled)


def export_trace(result, directory, objects):
    """Lossless per-call responses and readable role/tool order for joint review."""
    from behave.agent.trajectory import trajectory

    lines = [f"# {result['method']} — {result['task']}", "", f"Status: {result['status']}", ""]
    if result.get("error"):
        lines.extend(["## Execution error", "", "````text", result["error"], "````", ""])
    for role in result["roles"]:
        child = AttemptJournal(Path(directory) / role["path"], objects)
        lines.extend([f"## {role['path']}", ""])
        for turn in trajectory(child).turns:
            for label, ref in (("Request", turn.request), ("Response", turn.response)):
                data = read_verified(objects, ref).decode("utf-8")
                lines.extend([f"### {label}", "", "````json", data, "````", ""])
            lines.extend([f"Raw provider response object: `{turn.raw_response.sha256}`", ""])
        # Failed model exchanges are not successful trajectory turns, but are
        # still part of the interactive trace and must explain interruptions.
        events = child.load()
        for index, event in enumerate(events):
            if event.kind != "model_settled" or event.payload["outcome"] != "failure":
                continue
            requested = next(item for item in reversed(events[:index]) if item.kind == "model_requested")
            for label, ref in (("Failed request", requested.payload["request"]),
                               ("Model failure", event.payload["record"]),
                               ("Raw failure response", event.payload["raw"])):
                data = read_verified(objects, BlobRef.from_dict(thaw_json(ref))).decode(errors="replace")
                lines.extend([f"### {label}", "", "````json", data, "````", ""])
    for index, refs in enumerate(result["commands"]):
        lines.extend([f"## Command {index}", "", "````json",
                      read_verified(objects, BlobRef.from_dict(refs[0])).decode(), "````", ""])
        for label, ref in zip(("stdout", "stderr"), refs[1:3]):
            lines.extend([f"### {label}", "", "````text",
                          read_verified(objects, BlobRef.from_dict(ref)).decode(errors="replace"), "````", ""])
    return "\n".join(lines)
