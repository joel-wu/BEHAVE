"""Freeze a common final suite across completed searches, then remeasure all RTL.

No provider or agent is instantiated here. A dominated search candidate is still
rechecked; new final evidence can change feasibility and hence the frontier.
"""

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import copy
from dataclasses import replace
from pathlib import Path
from threading import Event

from behave.agent.replay import Tape
from behave.agent.run_store import JournalDirectory
from behave.core.artifacts import BlobRef, SourceArtifact, read_verified
from behave.core.records import canonical_json, parse_json, thaw_json
from behave.task_execution.judge_policy import JudgePolicy

from .records import finalized_report, read_document, write_document
from .records import union_pool as _union
from .workflow import SearchArchive, _seal_journals, candidate_identity, frontier


def final_baseline(task):
    """Build Sim's independent final baseline, even when no agent called Sim."""
    from behave.sim import api

    contract = api.TaskContract.from_dict(parse_json(task.contract.data))
    generation = (None if task.run_policy.strip() == b"null" else
                  api.GenerationSpec.from_dict(parse_json(task.run_policy)))
    policy = JudgePolicy.from_bytes(task.judge_policy)
    return api.build_baseline(contract, generation, policy=api.BaselinePolicy(
        seed=policy.seed, case_limit=policy.baseline_cases, max_queue_depth=policy.sequence_length))


def reuse_settled_final(previous, run, manifest, candidates):
    """Reuse only journal-settled results from exactly the same final manifest.

    The caller must fence the old process first. A dangling requested action is
    never promoted to a result. Failures are reused just like passes.
    """
    old = JournalDirectory(previous)
    events = old.journal("workflow").load()
    if not events:
        return candidates, (), (), 0
    if events[0].kind != "started" or thaw_json(events[0].payload["manifest"]) != manifest.as_dict():
        raise ValueError("final recovery requires identical suite, candidates and evaluator")
    measurements = old.journal("measurements").load()
    settled = [event for event in measurements if event.kind == "settled"]
    if not settled:
        return candidates, (), (), 0
    state = read_document(old.objects, BlobRef.from_dict(thaw_json(settled[-1].payload["record"])))
    rows = state["candidates"]
    if set(rows) != set(candidates):
        raise ValueError("final recovery candidate set changed")
    _, closure = _seal_journals(old, "measurements")
    run.objects.publish(old.objects, closure)
    completed = tuple(key for key, row in rows.items()
                      if any(item["final"] and item["mode"] == "final" for item in row["history"]))
    return rows, completed, closure, sum(len(row["history"]) for row in rows.values())


def measure_candidates(archive, completed, workers, *, phase="final"):
    """Admit candidates through the shared CPU pool; commit on one writer thread.

    Each remote worker owns a distinct scheduler ingress. A failed invocation
    stops new dispatch, cancels queued admissions and settles other returned
    results before propagating the error. Recovery reuses those settled rows.
    """
    if phase not in {"check", "final"}:
        raise ValueError("unknown final comparison phase")
    keys = iter(key for key in sorted(archive.candidates) if key not in completed)
    phase_pool = archive.pool

    def modes_for(key):
        if phase == "check":
            return ("check",)
        return ("final",) if "eda" in archive.candidates[key] else ("eda", "final")

    if len(workers) == 1:
        for key in keys:
            modes = modes_for(key)
            archive.actions(key, modes, final=True)
        return
    stopped = Event()

    def measure(worker, inputs, modes):
        worker = copy(worker)
        cancelled = getattr(worker, "cancelled", lambda: False)
        worker.cancelled = lambda: stopped.is_set() or cancelled()
        if len(modes) > 1:
            return worker.run_many(inputs, archive.experiment, modes, phase_pool, archive.run.objects)
        began = time.monotonic()
        result = worker.run(inputs, archive.experiment, modes[0], phase_pool, archive.run.objects)
        return {modes[0]: (result, time.monotonic() - began)}

    pending, errors = {}, []
    pool_ref, closure = write_document(archive.run.objects, list(archive.pool))
    with ThreadPoolExecutor(max_workers=len(workers)) as executor:
        def submit(worker):
            key = next(keys, None)
            if key is None:
                return
            modes = modes_for(key)
            inputs = archive.inputs_for(key, final=True)
            archive.append("requested", {"candidate": key, "modes": list(modes), "final": True,
                                         "pool": pool_ref.as_dict()}, closure)
            future = executor.submit(measure, worker, inputs, modes)
            pending[future] = (key, modes, worker)

        try:
            for worker in workers:
                submit(worker)
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                idle = []
                for future in done:
                    key, modes, worker = pending.pop(future)
                    try:
                        measurements = future.result()
                    except Exception as error:  # noqa: BLE001 -- settle peer results, then re-raise
                        stopped.set()
                        errors.append(error)
                        continue
                    for mode in modes:
                        result, elapsed = measurements[mode]
                        archive.record(key, mode, result, elapsed, final=True)
                        if mode == "check":
                            archive.pool = _union(archive.pool, result.get("pool", ()))
                    archive.snapshot()
                    idle.append(worker)
                if not errors:
                    for worker in idle:
                        submit(worker)
        finally:
            stopped.set()
    if errors:
        raise errors[0]


def compare_searches(*, searches: dict[str, Path], task, experiment, native, directory,
                     retained_pool: tuple = (), reuse_final: Path | None = None, workers=None):
    """Each label identifies one method/replicate; all share this frozen suite.

    Search budgets remain in their original reports. Final validation costs are
    recorded separately and cannot be used to keep searching. Duplicate source
    bytes may share a measurement, but membership of every search is preserved.
    The selected measurement/evaluator configuration may differ from search.
    Task semantics stay fixed; new results never overwrite search evidence.
    """
    if not searches or any(type(label) is not str or not label for label in searches):
        raise ValueError("comparison requires named completed searches")
    if workers is None:
        resources = getattr(native, "cpu_resources", None)
        count = resources.snapshot()["slots"] if hasattr(resources, "snapshot") else 1
        workers = (native,) * count
    workers = tuple(workers)
    if not workers or any(worker.identity != native.identity for worker in workers):
        raise ValueError("final workers must use the same evaluator")
    endpoints = [worker.cpu_resources for worker in workers if isinstance(getattr(worker, "cpu_resources", None), str)]
    if len(endpoints) != len(set(endpoints)):
        raise ValueError("concurrent final workers require distinct CPU scheduler ingresses")
    task = replace(task, judge_policy=replace(JudgePolicy.from_bytes(task.judge_policy),
                    score_timeout_seconds=experiment.service_seconds).to_bytes())
    if Path(directory).resolve() in {Path(path).resolve() for path in searches.values()}:
        raise ValueError("final comparison needs a separate output directory")
    run = JournalDirectory(directory)
    with run.owner():
        members, candidates, reports = {}, {}, {}
        search_journals, source_configurations, dependencies = {}, {}, []
        judge_ref = run.objects.put(task.judge_policy)
        dependencies.append(judge_ref)
        task_binding = {"task": task.revision.as_dict(), "release": task.release.as_dict(),
                        "contract": task.contract.reference.content.as_dict(),
                        "reference_behavior": task.reference_behavior.artifact.as_dict(),
                        "run_policy": BlobRef.of(task.run_policy).as_dict()}
        eda_inputs = {key: value for key, value in experiment.as_dict().items()
                      if key not in {"workloads", "targets_ns", "service_seconds"}}
        baseline = final_baseline(task)
        baseline_ref, baseline_closure = write_document(run.objects, baseline.as_dict())
        dependencies.extend(baseline_closure)
        pool = _union(retained_pool, (stimulus.as_dict() for stimulus in baseline.stimuli))
        for label, path in sorted(searches.items()):
            old, ref, report = finalized_report(path)
            cfg = parse_json(read_verified(old.objects, BlobRef.from_dict(report["configuration"])))
            if any(canonical_json(cfg.get(key)) != canonical_json(value) for key, value in task_binding.items()):
                raise ValueError("comparison mixes task, contract or reference revisions")
            prior_eda_inputs = {key: value for key, value in cfg["experiment"].items()
                                if key not in {"workloads", "targets_ns", "service_seconds"}}
            reuse_eda = (cfg["native"] == native.identity
                         and canonical_json(cfg["technology"]) == canonical_json(native.technology)
                         and canonical_json(prior_eda_inputs) == canonical_json(eda_inputs))
            source_configurations[label] = report["configuration"]
            heads, closure = _seal_journals(old, "workflow", "agent", "measurements")
            run.objects.publish(old.objects, closure)
            search_journals[label] = heads
            dependencies.extend(closure)
            reports[label] = ref.as_dict()
            pool = _union(pool, read_document(old.objects, BlobRef.from_dict(report["pool_ref"])))
            members[label] = [row["candidate"] for row in report["candidates"]]
            for row in report["candidates"]:
                artifact = SourceArtifact.from_dict(row["source"])
                reference = row.get("source_reference")
                if candidate_identity(artifact.as_dict(), reference) != row["candidate"]:
                    raise ValueError("candidate source identity mismatch")
                reference_files = () if reference is None else SourceArtifact.from_dict(reference).files
                for file in (*artifact.files, *reference_files):
                    run.objects.put(read_verified(old.objects, file.content))
                candidates[row["candidate"]] = {"candidate": row["candidate"], "source": artifact.as_dict(),
                                                "history": [], **({"eda": row["eda"]}
                                                    if reuse_eda and row.get("eda", {}).get("status") == "measured" else {}),
                                                **({"source_reference": reference}
                                                                    if reference is not None else {})}
        pool_ref, pool_closure = write_document(run.objects, list(pool))
        manifest, manifest_closure = write_document(run.objects, {"schema": "behave.ppa-final-suite/5", "searches": reports,
            "baseline": baseline_ref.as_dict(),
            "search_journals": search_journals,
            "members": members, "pool_ref": pool_ref.as_dict(), "experiment": experiment.as_dict(), "native": native.identity,
            "source_configurations": source_configurations,
            "evaluation": {**task_binding, "judge_policy": judge_ref.as_dict(), "technology": native.technology}})
        tape = Tape(run.journal("workflow"))
        tape.record("started", {"manifest": manifest.as_dict()},
                    tuple(dict.fromkeys((*manifest_closure, *pool_closure, *dependencies))))
        archive = SearchArchive(run, task, experiment, native)
        if tape.begin("final_requested", {"manifest": manifest.as_dict()}):
            completed, reused_calls = (), 0
            if reuse_final is not None:
                candidates, completed, closure, reused_calls = reuse_settled_final(
                    reuse_final, run, manifest, candidates)
                tape.record("measurements_reused", {"directory": str(reuse_final),
                    "completed": list(completed), "service_calls": reused_calls}, closure)
            archive.candidates, archive.pool = candidates, pool
            if reused_calls:
                archive.pending_refs.extend(closure)
                archive.snapshot()
            if JudgePolicy.from_bytes(task.judge_policy).search_seconds > 0:
                checked = []
                for key, row in archive.candidates.items():
                    for item in row["history"]:
                        if not item["final"] or item["mode"] != "check":
                            continue
                        checked.append(key)
                        record = read_document(run.objects, BlobRef.from_dict(item["record"]))
                        archive.pool = _union(archive.pool, read_document(
                            run.objects, BlobRef.from_dict(record["pool_ref"])))
                # Reuse the public comparison path with no explicit replay:
                # baseline + fresh search against golden, never agent-visible.
                # Only after every check settles, replay their union for all.
                measure_candidates(archive, checked, workers, phase="check")
            measure_candidates(archive, completed, workers)
            final_pool, final_pool_closure = write_document(run.objects, list(archive.pool))
            result = {"schema": "behave.ppa-comparison/2", "manifest": manifest.as_dict(),
                "pool_ref": final_pool.as_dict(),
                "candidates": archive.candidates, "members": members,
                "frontiers": {label: frontier(archive.candidates[key] for key in keys)
                              for label, keys in members.items()},
                "final_service_calls": sum(len(row["history"]) for row in archive.candidates.values()),
                **({"reused_service_calls": reused_calls} if reuse_final is not None else {})}
            ref, report_closure = write_document(run.objects, result)
            heads, closure = _seal_journals(run, "measurements")
            tape.record("finished", {"result": ref.as_dict(), "journals": heads},
                        (*report_closure, *final_pool_closure, *closure))
        else:
            if reuse_final is not None:
                tape.take("measurements_reused")
            ref = BlobRef.from_dict(thaw_json(tape.take("finished").payload["result"]))
            result = read_document(run.objects, ref)
        tape.finish()
        return result
