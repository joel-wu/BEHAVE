"""Validate explicit PPA continuations and select retained evidence.

This module neither launches jobs nor takes over running attempts. Callers own
process fencing and dispatch. Records, retry eligibility and attempt selection
are shared with the historical campaign controller, not reimplemented per site.
"""

import json
import time
from pathlib import Path

from .campaign_inputs import publish
from .planning import validate_campaign


def recovery_binding(plan, previous, *, _seen=()):
    """An explicit new attempt may select old methods, never alter their experiment.

    Public input copies may have different paths. Input bytes, model parameters,
    accounts, prompts and per-tool limits remain identical. CPU scheduling,
    cumulative supervisor deadlines and provider request-rate policy may change.
    """
    from behave.core.artifacts import BlobRef

    previous = Path(previous).resolve()
    if previous in _seen:
        raise ValueError("cyclic campaign recovery")
    old = validate_campaign(json.loads((previous / "plan.json").read_text()))
    ancestry = {}
    ancestor = previous / "recovery.json"
    if ancestor.exists():
        prior = json.loads(ancestor.read_text())
        if not isinstance(prior, dict) or not prior.get("directory"):
            raise ValueError("invalid campaign recovery ancestor")
        expected = recovery_binding(old, Path(prior["directory"]), _seen=(*_seen, previous))
        if prior != expected:
            raise ValueError("campaign recovery ancestor changed")
        ancestry = {"ancestor": BlobRef.of(ancestor.read_bytes()).as_dict()}
    copied = {"objects", "release", "experiment", "retained_pool", "methods", "judge_policy",
              "final_judge_policy", "final_experiment", "parallelism", "cpus", "cpu_workers",
              "replicate_seconds", "final_seconds"}
    if {k: v for k, v in plan.items() if k not in copied} != {k: v for k, v in old.items() if k not in copied}:
        raise ValueError("recovery must preserve the original campaign settings")
    pairs = [(plan[k], old[k]) for k in ("release", "experiment", "retained_pool")]
    for key in ("judge_policy", "final_judge_policy", "final_experiment"):
        if bool(plan.get(key)) != bool(old.get(key)):
            raise ValueError("recovery must preserve the judge policy")
        if plan.get(key):
            pairs.append((plan[key], old[key]))
    selected = {m["name"] for m in plan["methods"]}
    original_methods = [m for m in old["methods"] if m["name"] in selected]
    if [m["name"] for m in plan["methods"]] != [m["name"] for m in original_methods]:
        raise ValueError("recovery must preserve selected method labels and ordering")
    old = {**old, "methods": original_methods}
    if [m["behavior_source"] for m in plan["methods"]] != [m["behavior_source"] for m in old["methods"]]:
        raise ValueError("recovery must preserve Behavior sources")
    if [m.get("sim_feedback", True) for m in plan["methods"]] != [m.get("sim_feedback", True) for m in old["methods"]]:
        raise ValueError("recovery must preserve Sim feedback settings")
    pairs += [(new["prompt"], prior["prompt"]) for new, prior in zip(plan["methods"], old["methods"])]
    for new, prior in zip(plan["methods"], old["methods"]):
        a, b = Path(new["provider"]).read_bytes(), Path(prior["provider"]).read_bytes()
        if a != b:
            from behave.agent.providers.hosted import HostedEndpoint

            a = HostedEndpoint.from_dict(json.loads(a)).as_dict()
            b = HostedEndpoint.from_dict(json.loads(b)).as_dict()
            a.pop("rate_limit", None)
            b.pop("rate_limit", None)
            if a != b:
                raise ValueError("recovery must preserve provider, model parameters and shared spending accounts")
    if any(Path(a).read_bytes() != Path(b).read_bytes() for a, b in pairs):
        raise ValueError("recovery must preserve publication, prompts and shared spending accounts")
    # Authenticate both original input copies and the selected publication later
    # through the same production loader used for every search/final comparison.
    for path, digest in json.loads((previous / "inputs.json").read_text()).items():
        if BlobRef.of(Path(path).read_bytes()).sha256 != digest:
            raise ValueError(f"original campaign input changed: {path}")
    stopped = previous / "interruption.json"
    interruption = {}
    if stopped.exists():
        receipt = json.loads(stopped.read_text())
        if (receipt.get("schema") != "behave.ppa-campaign-stop/1"
                or receipt.get("cleanup") != "complete" or not receipt.get("step")):
            raise ValueError("campaign interruption requires confirmed step cleanup")
        interruption = {"interruption": BlobRef.of(stopped.read_bytes()).as_dict()}
    return {"schema": "behave.ppa-campaign-recovery/1", "directory": str(previous),
            "plan": BlobRef.of((previous / "plan.json").read_bytes()).as_dict(),
            "wait_seconds": (old["replicate_seconds"] or 0) + 300, **interruption, **ancestry}


def recovery_ancestor(binding):
    """Follow only the immutable recovery link, never choose among attempts."""
    from behave.core.artifacts import BlobRef

    if "ancestor" not in binding:
        return None
    source = Path(binding["directory"]) / "recovery.json"
    data = source.read_bytes()
    if BlobRef.of(data).as_dict() != binding["ancestor"]:
        raise ValueError("campaign recovery ancestor changed")
    return json.loads(data)


def recover_search(binding, label, destination):
    """Reuse success; restart fenced infrastructure failures, not candidate errors.

    This is not an API retry or attempt takeover. An uncertain paid request keeps
    its old reservation. A replacement search gets a new attempt/call identity,
    and shares the original account/ceiling. Successful/running searches are not
    discarded. Candidate errors are not retried. A known HTTP 429 rejection may
    replace an interrupted rollout; its earlier candidates and charges stay in
    the original archive, without selecting the best of the two attempts.
    """
    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef, read_verified
    from behave.core.records import parse_json

    previous = Path(binding["directory"])
    if BlobRef.of((previous / "plan.json").read_bytes()).as_dict() != binding["plan"]:
        raise ValueError("original campaign plan changed")
    directory = previous / "runs" / label
    receipt = directory / "search-service/service.json"
    stopped = binding.get("interruption")
    if stopped is not None and BlobRef.of((previous / "interruption.json").read_bytes()).as_dict() != stopped:
        raise ValueError("campaign interruption evidence changed")
    # A reused search has no local service. A newer dispatched attempt takes
    # precedence, including failures; recovery never selects an older success.
    if not (directory / "search-service/requested.json").exists():
        ancestor = recovery_ancestor(binding)
        if ancestor is not None:
            return recover_search(ancestor, label, destination)
    if stopped is not None and not receipt.exists():
        publish(destination / "recovery.json", {"previous": str(directory),
            "interruption": stopped, "action": "new-attempt", "failure": "campaign-stopped"})
        return None
    if not (directory / "search-service/requested.json").exists():
        raise ValueError(f"original search was not dispatched: {label}")
    stop = time.monotonic() + binding["wait_seconds"]
    while not receipt.exists():
        if time.monotonic() >= stop:
            raise RuntimeError(f"original search still unsettled; no redispatch: {label}")
        time.sleep(5)
    outcome = json.loads(receipt.read_text())
    if outcome.get("cleanup") != "complete":
        raise RuntimeError(f"original process cleanup unconfirmed: {label}")
    evidence = {"previous": str(directory), "receipt": BlobRef.of(receipt.read_bytes()).as_dict(),
                "action": "reuse" if outcome["ok"] else "keep-failure"}
    if stopped is not None and not outcome["ok"] and outcome.get("status") == "timeout":
        evidence.update(action="new-attempt", failure="campaign-service-timeout", interruption=stopped)
    if not outcome["ok"] and outcome.get("status") == "exited" and outcome.get("returncode") == 1:
        run = JournalDirectory(directory / "search")
        # Authenticate the final provider event, not only the traceback text.
        measurements = run.journal("measurements").load()
        events = run.journal("agent").load()
        if events and events[-1].kind == "model_settled":
            event = events[-1]
            if event.payload.get("outcome") == "failure":
                failure = parse_json(read_verified(run.objects,
                    BlobRef.from_dict(dict(event.payload["record"]))))
                before_measurement = (not measurements and failure.get("kind") == "unknown"
                    and failure.get("code") in {"model-worker-failed", "budget-account-busy"})
                rate_rejection = (failure.get("kind") == "retryable"
                                  and failure.get("code") == "openrouter-http-429")
                transport_interrupted = (failure.get("kind") == "unknown"
                    and failure.get("code") == "deepseek-exchange-interrupted")
                if before_measurement or rate_rejection or transport_interrupted:
                    evidence.update(action="new-attempt", failed_call=event.payload["call_id"],
                        agent_head=BlobRef.of(event.to_bytes()).as_dict(), failure=failure["code"])
    publish(destination / "recovery.json", evidence)
    if evidence["action"] == "new-attempt":
        return None
    return {"label": label, "phase": "search", **outcome,
            "search_directory": str(directory / "search"), "recovery": evidence["action"]}


def recover_final(binding, label, pool, *, native_identity=None):
    """Find the latest settled final archive with the same frozen input suite.

    The caller authenticates the binding with ``recovery_binding`` before using
    it. Native-refresh mismatch stops lookup rather than reviving older results.
    """
    from behave.agent.run_store import JournalDirectory

    while binding is not None:
        previous = Path(binding["directory"])
        prior = previous / "runs" / label / "final"
        old_pool = previous / "common-pool.json"
        if (binding.get("interruption") is not None and (prior / "workflow").is_dir()
                and old_pool.exists() and json.loads(old_pool.read_text()) == {"stimuli": list(pool)}):
            events = JournalDirectory(prior).journal("measurements").load(verify_artifacts=False)
            if any(event.kind == "settled" for event in events):
                if native_identity is not None:
                    from behave.core.artifacts import BlobRef
                    from behave.core.records import thaw_json

                    from .records import read_document

                    run = JournalDirectory(prior)
                    started = run.journal("workflow").load(verify_artifacts=False)[0]
                    manifest = read_document(run.objects,
                        BlobRef.from_dict(thaw_json(started.payload["manifest"])))
                    if manifest["native"] != native_identity:
                        # Explicit native refresh starts a fresh final pass; do
                        # not resurrect an older archive behind this selection.
                        return None
                return prior
        binding = recovery_ancestor(binding)
    return None


def selected_search(binding, label, directory, *, final_only=False):
    prior = None if binding is None else recover_search(binding, label, directory)
    if prior is None and final_only:
        raise RuntimeError(f"final-only campaign has no completed selected search: {label}; no model dispatched")
    return prior
