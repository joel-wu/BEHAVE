"""Feed committed generation cases to the existing saved-submission scorer.

Only immutable evaluation_case records are read from a live generation. Each
bounded subset becomes a separate, sealed scoring input; the generator's owner,
workspace and journals are never taken over. Scoring remains rejudge_batch.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from behave.core.records import RecordError
from behave.core.storage import install_exclusive
from behave.runtime.records import write_json


def committed_cases(source):
    from behave.agent.journal import Event

    directory = Path(source) / "run/report"
    cases = []
    for path in sorted(directory.glob("*.json")):
        event = Event.from_bytes(path.read_bytes())
        if event.kind == "evaluation_case":
            cases.append(event)
    return cases


def publish_subset(source, output, events):
    """Preserve original jobs/results, but seal only these already-ended cases."""
    from behave.agent.journal import Event
    from behave.agent.replay import Tape
    from behave.core.artifacts import BlobRef, read_verified
    from behave.core.records import canonical_json, thaw_json
    from behave.task_execution.batch import case_of, summarize_recorded
    from behave.task_execution.batch_plan import BatchPlan
    from behave.task_execution.recovery import require_task_quiescent
    from behave.task_execution.run_store import RunStore

    source, output = Path(source), Path(output)
    if not events:
        raise ValueError("a scoring subset requires committed cases")
    origin = RunStore(source / "run")
    initial = Event.from_bytes((origin.directory / "report/000000.json").read_bytes())
    plan = BatchPlan.from_bytes(read_verified(origin.objects,
        BlobRef.from_dict(thaw_json(initial.payload)["plan"])), origin.objects)
    selected = [case_of(plan, thaw_json(event.payload)) for event in events]
    if len({index for index, _ in selected}) != len(selected):
        raise ValueError("duplicate committed case")
    output.mkdir(parents=True, exist_ok=False)
    (output / "inputs").symlink_to((source / "inputs").resolve(), target_is_directory=True)
    target = RunStore(output / "run")
    with target.owner():
        # The public task release stays in inputs. Result closures are already
        # committed/fsynced by their producer, so hardlink publication is safe.
        refs = tuple(dict.fromkeys(ref for event in (initial, *events) for ref in event.artifacts))
        target.objects.publish_owned(origin.objects, refs)
        for _, row in selected:
            candidate = origin.directory / "candidates" / row["run_key"]
            if candidate.exists():
                require_task_quiescent(RunStore(candidate), run_key=row["run_key"])
                phases = ("supervision", "judge", "testbench")
                if not (candidate / "supervision").exists():
                    phases += ("solver", "provider", "development")
                for phase in phases:
                    if (candidate / phase).exists():
                        shutil.copytree(candidate / phase,
                            target.directory / "candidates" / row["run_key"] / phase)
        jobs = tuple(plan.jobs[index] for index, _ in selected)
        keys = {BlobRef.of(job.to_bytes()) for job in jobs}
        subset = replace(plan, jobs=jobs,
                         recorded_admissions=tuple(item for item in plan.recorded_admissions if item[0] in keys))
        plan_ref = target.objects.put(subset.to_bytes())
        tape = Tape(target.journal("report"))
        tape.record("evaluation_started", {"plan": plan_ref.as_dict()}, (plan_ref, *refs))
        for index, (event, (_, row)) in enumerate(zip(events, selected, strict=True)):
            tape.record("evaluation_case", {"index": index, "row": row}, event.artifacts)
        summary = summarize_recorded(subset, [row for _, row in selected], target.objects)
        ref = target.objects.put(canonical_json(summary))
        tape.record("evaluation_finished", {"summary": ref.as_dict()}, (ref,))
        tape.finish()
        target.seal("report")
    write_json(output / "source.json", {"source": str(source.resolve()),
        "keys": [row["run_key"] for _, row in selected]})
    return output


def consolidate(source, chunks, output, *, published=None):
    """One canonical report per measurement, with original run keys and CAS.

    ``published`` is an optional, measurement-local cache of references already
    durably installed by this process. It is only an I/O optimization: restart
    starts with an empty cache and the same exclusive publication checks.
    """
    from behave.agent.journal import Event
    from behave.agent.replay import Tape
    from behave.core.artifacts import BlobRef, read_verified
    from behave.core.records import canonical_json, thaw_json
    from behave.task_execution.batch import case_of, summarize_recorded
    from behave.task_execution.batch_plan import BatchPlan
    from behave.task_execution.run_store import RunStore

    source, output = Path(source), Path(output)
    origin = RunStore(source / "run")
    initial_path = origin.directory / "report/000000.json"
    if not initial_path.exists():
        return
    initial = Event.from_bytes(initial_path.read_bytes())
    plan_ref = BlobRef.from_dict(thaw_json(initial.payload)["plan"])
    plan = BatchPlan.from_bytes(read_verified(origin.objects, plan_ref), origin.objects)
    indexes = {BlobRef.of(job.to_bytes()).sha256: index for index, job in enumerate(plan.jobs)}
    cases, publications = {}, []
    installed = set() if published is None else published
    for chunk in chunks:
        saved = RunStore(Path(chunk) / "run")
        directory = saved.directory / "judgments" / "baseline"
        references = {}
        for path in sorted((directory / "report").glob("*.json")):
            event = Event.from_bytes(path.read_bytes())
            if event.kind != "evaluation_case":
                continue
            row = thaw_json(event.payload)["row"]
            if row["run_key"] not in indexes:
                raise ValueError("scoring chunk contains an unrequested attempt")
            case_of(plan, {"index": indexes[row["run_key"]], "row": row})
            if row["run_key"] in cases:
                raise ValueError("one eval attempt was scored by multiple stream chunks")
            references.update(dict.fromkeys(event.artifacts))
            cases[row["run_key"]] = (row, event.artifacts)
        publications.append((saved.objects, references))
    # Validate all cases first, then deduplicate overlapping closures per
    # source store. Do not issue one NFS link/fsync batch for every case, or
    # republish the complete history whenever one new judgment arrives.
    for objects, references in publications:
        missing = tuple(ref for ref in references if ref not in installed)
        if missing:
            origin.objects.publish_owned(objects, missing)
            installed.update(missing)  # Only after publication and fsync succeed.
    ordered = [(BlobRef.of(job.to_bytes()).sha256, index) for index, job in enumerate(plan.jobs)]
    rows = [cases[key][0] for key, _ in ordered if key in cases]
    summary = summarize_recorded(plan, rows, origin.objects)
    expected = len(plan.jobs)
    settled = (len(rows) == expected and all(row["status"] in {"scored", "no_submission"} for row in rows))
    state = "completed" if settled else ("needs_recovery" if len(rows) == expected else "scoring")
    if len(rows) == expected:
        target = RunStore(origin.directory / "judgments" / output.name, objects=origin.objects)
        with target.owner():
            tape = Tape(target.journal("report"))
            tape.record("evaluation_started", {"plan": plan_ref.as_dict()}, (plan_ref,))
            for key, index in ordered:
                row, refs = cases[key]
                tape.record("evaluation_case", {"index": index, "row": row}, refs)
            ref = origin.objects.put(canonical_json(summary))
            tape.record("evaluation_finished", {"summary": ref.as_dict()}, (ref,))
            tape.finish()
            target.seal("report")
    write_json(output / "progress-0.json", [{"source": str(source), "state": state,
        "report": {"summary": summary}, "expected_tasks": expected}])
    marker = source.parent / "scoring.json"
    if marker.exists() and not (output / "prior_scoring.json").exists():
        write_json(output / "prior_scoring.json", json.loads(marker.read_text()))
    write_json(marker, {"state": state, "output": str(output),
        "quality": {"complete": settled, "expected_tasks": expected,
                    "requested": len(rows), "no_result": summary["no_result"]}})


def score_signature(chunks):
    """Immutable case filenames change only when the scorer commits new work."""
    return tuple(str(path) for chunk in chunks
                 for path in sorted((Path(chunk) / "run/judgments/baseline/report").glob("*.json")))


def scoring_sources(root, config, allocation):
    """Add restored scoring-only work without adding any GPU measurement.

    A separately published readiness receipt prevents observing a half-restored
    archive. The backlog is reread by the existing CPU owner, not another
    scheduler or an additional pool of scoring workers.
    """
    sources = [Path(item["directory"]) / "rollout" for item in config["measurements"]
               if item["allocation"] == allocation]
    queue = Path(root) / "score_backlog.json"
    if not queue.is_file():
        return list(dict.fromkeys(sources))
    backlog = json.loads(queue.read_text())
    if backlog.get("schema") != "behave.eval-score-backlog/1":
        raise ValueError("unknown scoring backlog schema")
    archive = Path(config["archive_root"]).resolve()
    seen = set()
    for item in backlog["measurements"]:
        if item["allocation"] != allocation:
            continue
        source = Path(item["directory"]) / "rollout"
        if source in seen:
            raise ValueError("duplicate scoring backlog source")
        seen.add(source)
        if not source.resolve().is_relative_to(archive):
            raise ValueError("scoring backlog source outside archive")
        ready = Path(item["ready"])
        if not ready.is_file():
            continue
        receipt = json.loads(ready.read_text())
        if receipt.get("key") != item["key"] or Path(receipt.get("source", "")) != source:
            raise ValueError("scoring backlog readiness identity differs")
        generation = json.loads((source.parent / "generation.json").read_text())
        if generation.get("state") != "completed":
            raise ValueError("scoring-only backlog generation is not completed")
        sources.append(source)
    return list(dict.fromkeys(sources))


def run(root, allocation):
    """CPU-only consumer; no model calls or GPU allocations."""
    from behave.agent.cpu_resources import CpuResources
    from behave.core.process_limits import configure_worker_process_limit
    from behave.eval.scoring import CandidateQueue, score_saved, scoring_environment
    from behave.isolation.startup import startup_environment
    from behave.task_execution.run_store import RunStore

    root = Path(root)
    limits = configure_worker_process_limit()
    config = json.loads((root / "fleet.json").read_text())
    node = config["nodes"][str(allocation)]
    os.sched_setaffinity(0, node["scoring_cpu_ids"])
    slots = config.get("isolation_startup_slots")
    startup = startup_environment(slots)
    resources = CpuResources(config["scoring_workers"])
    status = root / "scoring" / str(allocation) / "progress.json"
    policy = root / "judge_policy.json"
    with RunStore(root / "scoring" / str(allocation)).owner(), \
            scoring_environment(environment=startup) as cancelled, \
            CandidateQueue(config["scoring_workers"]) as candidates, ThreadPoolExecutor(max_workers=4) as archives:
        inflight, done, blocked, signatures, published = {}, set(), {}, {}, {}

        def enqueue(chunk):
            future = archives.submit(score_saved, chunk, "baseline",
                workers=config["scoring_workers"], previous=None, judge_policy=policy,
                cpu_pool=None, stage_from=None, all_submitted=True,
                retry_testbench=True, executor=candidates, cancelled=cancelled, cpu_resources=resources)
            inflight[future] = chunk

        while not cancelled():
            for future in list(inflight):
                chunk = inflight[future]
                if not future.done():
                    continue
                try:
                    result = future.result()
                    write_json(chunk / "scored.json", result.as_dict())
                except Exception as error:
                    logging.getLogger(__name__).exception("Committed-submission score chunk failed")
                    blocked[str(chunk)] = f"{type(error).__name__}: {error}"
                    write_json(chunk / "failure.json", {"error": blocked[str(chunk)]})
                done.add(str(chunk))
                del inflight[future]
            for source in scoring_sources(root, config, allocation):
                directory = source.parent / "scoring" / config["score_name"]
                chunks = sorted((directory / "chunks").glob("*/source.json"))
                used = {key for path in chunks for key in json.loads(path.read_text())["keys"]}
                for path in chunks:
                    chunk = path.parent
                    if (chunk / "scored.json").exists() or (chunk / "failure.json").exists():
                        done.add(str(chunk))
                    if str(chunk) not in done and chunk not in inflight.values() and len(inflight) < 4:
                        enqueue(chunk)
                if len(inflight) < 4:
                    available = [event for event in committed_cases(source)
                                 if event.payload["row"]["run_key"] not in used]
                    if available:
                        chunk = directory / "chunks" / f"{len(chunks):05d}"
                        if chunk.exists():
                            blocked[str(chunk)] = "incomplete subset publication requires reconciliation"
                            continue
                        try:
                            publish_subset(source, chunk, available[:config["score_chunk_size"]])
                        except Exception as error:
                            logging.getLogger(__name__).exception("Committed-submission publication failed")
                            blocked[str(chunk)] = f"{type(error).__name__}: {error}"
                            write_json(chunk / "failure.json", {"error": blocked[str(chunk)]})
                        else:
                            # The subset is sealed. Start scoring now so input
                            # preparation overlaps scoring and shares its cap,
                            # rather than preparing every source before any
                            # scoring is admitted on the next loop.
                            enqueue(chunk)
                if chunks:
                    signature = score_signature(path.parent for path in chunks)
                    if signature != signatures.get(str(source)):
                        consolidate(source, [path.parent for path in chunks], directory,
                                    published=published.setdefault(str(source), set()))
                        signatures[str(source)] = signature
            write_json(status, {"state": "running", "cpu": resources.snapshot(),
                "process_limits": limits,
                "isolation_startup_slots": slots,
                "candidates": candidates.activity(), "active_chunks": [str(p) for p in inflight.values()],
                "blocked": blocked, "updated_at": time.time()})
            time.sleep(10)


def import_scores(source, previous, output, *, previous_objects=None):
    """Seal one already-scored subset before a restored source becomes ready.

    ``previous`` is a list of restored score-chunk directories. Only committed
    evaluation_case events count, including explicit no-result outcomes. A
    partially evaluated chunk therefore leaves its unscored cases available to
    the normal scorer. Duplicate/foreign attempts are rejected, never merged.
    """
    from behave.agent.journal import Event
    from behave.agent.replay import Tape
    from behave.core.artifacts import BlobRef, read_verified
    from behave.core.records import canonical_json, thaw_json
    from behave.eval.score_stream import committed_cases, publish_subset
    from behave.runtime.records import write_json
    from behave.task_execution.batch import case_of, summarize_recorded
    from behave.task_execution.batch_plan import BatchPlan
    from behave.task_execution.run_store import RunStore

    source, output = Path(source), Path(output)
    events = committed_cases(source)
    generated = {event.payload['row']['run_key']: event for event in events}
    if len(generated) != len(events):
        raise ValueError('duplicate generation case')
    origin = RunStore(source / 'run')
    initial = Event.from_bytes((origin.directory / 'report/000000.json').read_bytes())
    full = BatchPlan.from_bytes(read_verified(origin.objects,
        BlobRef.from_dict(thaw_json(initial.payload)['plan'])), origin.objects)
    indexes = {BlobRef.of(job.to_bytes()).sha256: i for i, job in enumerate(full.jobs)}
    scored = {}
    for directory in previous:
        run = RunStore(Path(directory) / 'run', objects=previous_objects)
        for path in sorted((run.directory / 'judgments/baseline/report').glob('*.json')):
            event = Event.from_bytes(path.read_bytes())
            if event.kind != 'evaluation_case':
                continue
            row = thaw_json(event.payload)['row']
            key = row['run_key']
            if key not in generated or key not in indexes:
                raise ValueError('score contains foreign or uncommitted generation attempt')
            if key in scored:
                raise ValueError('duplicate prior score')
            case_of(full, {'index': indexes[key], 'row': row})
            scored[key] = (row, event.artifacts, run.objects)
    if output.exists():
        raise ValueError('score import destination already exists')
    if not scored:
        return 0
    selected = [event for event in events if event.payload['row']['run_key'] in scored]
    publish_subset(source, output, selected)
    target = RunStore(output / 'run')
    initial = Event.from_bytes((target.directory / 'report/000000.json').read_bytes())
    plan_ref = BlobRef.from_dict(thaw_json(initial.payload)['plan'])
    subset = BatchPlan.from_bytes(read_verified(target.objects, plan_ref), target.objects)
    judgment = RunStore(target.directory / 'judgments/baseline', objects=target.objects)
    rows = []
    with judgment.owner():
        tape = Tape(judgment.journal('report'))
        tape.record('evaluation_started', {'plan': plan_ref.as_dict()}, (plan_ref,))
        for index, job in enumerate(subset.jobs):
            row, refs, reader = scored[BlobRef.of(job.to_bytes()).sha256]
            target.objects.publish_owned(reader, refs)
            tape.record('evaluation_case', {'index': index, 'row': row}, refs)
            rows.append(row)
        report = summarize_recorded(subset, rows, target.objects)
        ref = target.objects.put(canonical_json(report))
        tape.record('evaluation_finished', {'summary': ref.as_dict()}, (ref,))
        tape.finish()
        judgment.seal('report')
    # The existing stream checks this marker before ever scheduling a chunk.
    write_json(output / 'scored.json', {'reused_committed_scores': len(rows),
        'previous': [str(Path(p)) for p in previous], 'summary': report})
    return len(rows)


class ScoreCheckpoint:
    """Copy new immutable journal entries only after their evidence is durable.

    Report case entries already contain the task/testbench evidence closure.
    Unfinished candidates, caches and simulators are deliberately not copied.
    The caller owns the destination; the source may append an immutable tail.
    """

    def __init__(self, source, destination, *, existing=()):
        self.source, self.destination = source, destination
        self.existing = existing
        self.published = set()
        self.heads = {}
        for phase in ("task", "report"):
            events = destination.journal(phase).load(verify_artifacts=False)
            self.heads[phase] = events[-1] if events else None

    def publish(self):
        cases = 0
        for phase, head in tuple(self.heads.items()):
            for event in self.source.journal(phase).load_after(head, verify_artifacts=False):
                refs = tuple(ref for ref in event.artifacts
                             if ref.sha256 not in self.existing and ref not in self.published)
                self.destination.objects.publish(self.source.objects, refs)
                self.published.update(refs)
                data = event.to_bytes()
                self.destination.objects.put(data)
                directory = self.destination.directory / phase
                name = f"{event.sequence:06d}.json"
                if not install_exclusive(directory, name, data) and (directory / name).read_bytes() != data:
                    raise RecordError("journal-conflict", "checkpoint destination has a different event")
                self.heads[phase] = event
                cases += event.kind == "evaluation_case"
        return cases
