"""Select follow-up review work, reusing Eval's rollout and saved-submission judge.

By default only submitted, unscored RTL is rejudged. --reroll additionally
requests a new attempt for failed RTL and missing submissions. Passing RTL is
always retained. --failure-code instead selects only specified infrastructure
failures without submissions. Sources are explicit, oldest first, never a
best-of selection.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import mkdtemp
from threading import Event

from behave.agent.cpu_resources import CpuResources, cpu_job
from behave.agent.run_store import JournalDirectory
from behave.agent.spending import SpendAccount, SpendPolicy
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json
from behave.eval import generation
from behave.isolation.scratch import ScratchDirectory
from behave.runtime.jobs import run_child, wait_control
from behave.runtime.records import archive_run, archive_spending, command_scope, publish_json, write_json
from behave.runtime.snapshots import source_command

def component_progress(part):
    # Keep selection/preparation importable without loading scoring backends.
    from behave.eval.report import component_progress as present

    return present(part)


def load_outcomes(sources):
    from behave.core.artifacts import BlobRef
    from behave.task_execution.run_store import RunStore

    outcomes = {}
    for source in sources:
        source = Path(source).resolve()
        selection = source / "selection.json"
        if selection.exists():
            outcomes.update(json.loads(selection.read_text())["original_outcomes"])
        for path in sorted((source / "run/report").glob("*.json")):
            event = json.loads(path.read_text())
            if event["kind"] != "evaluation_case":
                continue
            row = event["payload"]["row"]
            result = None
            if row["result"] is not None:
                store = RunStore(source / "run/candidates" / row["run_key"])
                result = json.loads(store.objects.read(BlobRef.from_dict(row["result"])))
            outcomes[row["task_id"]] = {"source": str(source), "row": row, "result": result}
        path = source / "rejudged.json"
        if path.exists():
            for task, item in json.loads(path.read_text()).items():
                outcomes[task] = item
    return outcomes


def select_retries(outcomes, *, reroll=False, failure_codes=()):
    selection = {"keep": [], "rollout": [], "rejudge": []}
    for task, item in outcomes.items():
        result = item["result"]
        judgment = None if result is None else result["judgment"]
        rtl = None if judgment is None else judgment["rtl"]["score"]
        if failure_codes:
            action = ("rollout" if result is not None and result["submission"] is None
                      and result.get("failure_code") in failure_codes else "keep")
        elif rtl == 1:
            action = "keep"
        elif result is not None and result["submission"] is not None and rtl is None:
            action = "rejudge"
        else:
            action = "rollout" if reroll else "keep"
        selection[action].append(task)
    return selection


def _publish_task(objects, source, task):
    """Copy the declared task closure, not other revisions in a shared CAS."""
    from behave.data.published import task_files

    manifest = task.manifest
    objects.publish_owned(source, (task.public.release, task.public.revision, task.release.policy,
        manifest.run_policy, manifest.judge_policy, *(file.content for file in task_files(manifest))))


def _preparation_plan(output, sources, *, reroll, failure_codes, spending):
    """Freeze choices and configuration before any input transfer can fail."""
    from behave.core.artifacts import BlobRef, DirectoryReader
    from behave.data.published import ReleaseManifest, TaskManifest, TaskRevision
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy

    settings = [json.loads((p / "settings.json").read_text()) for p in sources]
    base = settings[-1]
    if any(s["setting"] != base["setting"] for s in settings):
        raise ValueError("follow-up sources must use one solver setting")
    if any(s["release"] != base["release"] for s in settings):
        raise ValueError("follow-up sources must use the same task release")
    outcomes = load_outcomes(sources)
    expected = {task for s in settings for task in s["tasks"]}
    for source in sources:
        selection_path = source / "selection.json"
        if selection_path.exists():
            expected.update(json.loads(selection_path.read_text())["original_outcomes"])
    if set(outcomes) != expected:
        raise ValueError("follow-up requires a recorded outcome for every source task")
    selection = select_retries(outcomes, reroll=reroll, failure_codes=failure_codes)
    reader = DirectoryReader(base["task_objects"])
    release_ref = BlobRef.from_dict(base["release"])
    release = ReleaseManifest.from_bytes(reader.read(release_ref))
    revisions, policies, manifests = {revision.task_id: revision for revision in release.tasks}, {}, []
    selected = set(selection["rollout"]) | set(selection["rejudge"])
    if selected - set(revisions):
        raise ValueError("selected tasks are absent from the source release")
    # Only metadata is needed to pin replacement policies and release hashes.
    # Actual task bodies are verified and transferred after the plan commits.
    for revision in release.tasks:
        task_id = revision.task_id
        if failure_codes or task_id not in selected:
            continue
        manifest = TaskManifest.from_bytes(reader.read(revision.manifest))
        if manifest.task_id != task_id:
            raise ValueError("task manifest differs from its release entry")
        policy = replace(JudgePolicy.from_bytes(reader.read(manifest.judge_policy)),
                         command_version=api.COMMAND_VERSION, authority_version=api.RESULT_VERSION,
                         search_seconds=0, analysis=None)
        policies[task_id] = json.loads(policy.to_bytes())
        manifest = replace(manifest, judge_policy=BlobRef.of(policy.to_bytes()))
        manifests.append(manifest.as_dict())
        revisions[task_id] = TaskRevision(task_id, BlobRef.of(manifest.to_bytes()))
    replacement = release if failure_codes else replace(release, release_id=output.name, tasks=tuple(revisions.values()))
    retry = {"sources": [str(p) for p in sources], "reroll": reroll,
             "failure_codes": list(failure_codes), **selection,
             "original_release": base["release"], "original_outcomes": outcomes, "policies": policies}
    if spending is not None:
        retry["spending"] = spending
    settings = {**base, **(spending or {}),
        "tasks": selection["rollout"], "release": BlobRef.of(replacement.to_bytes()).as_dict(),
        "task_objects": str(output / "task_objects"),
        "retry": {"selection": str(output / "selection.json")},
        "notes": {"judge": ("original policy retained" if failure_codes else
                            "baseline-only: search_seconds=0, analysis=None"),
                  "comparison": "selected follow-up, not a new independent full-dataset accuracy estimate",
                  "storage": "node-local mutable state, one final archive",
                  "sources": [str(p) for p in sources]}}
    if spending is not None and "endpoint" in base and base.get("provider") != "sglang":
        from behave.eval.generation import endpoint_for

        endpoint = endpoint_for(output, base)
        if endpoint.spending is None:
            raise ValueError("supplement requires an existing hosted budget policy")
        binding = replace(endpoint.spending, limit=spending["budget_usd"],
                          directory=spending["spending_directory"], account=spending["spending_account"])
        settings["endpoint"] = replace(endpoint, spending=binding).as_dict()
    if "attempt_namespace" in base:
        # A new explicitly requested wave is not the original paid attempt.
        # Its preparation receipt then pins this namespace across all resumes.
        settings["attempt_namespace"] = str(output)
    return {"schema": "behave.eval-followup-preparation/1", "selection": retry, "settings": settings,
            "input_objects": base["task_objects"], "release": replacement.as_dict(), "manifests": manifests}


def _publish_preparation(output, plan):
    """Import pinned inputs, then commit selection and runnable settings last."""
    from behave.core.artifacts import BlobRef
    from behave.core.records import canonical_json
    from behave.core.storage import DirectoryStore
    from behave.data.published import PublishedReleaseReader, ReleaseManifest, TaskManifest

    selection = plan["selection"]
    reader = DirectoryStore(plan["input_objects"])
    release_ref = BlobRef.from_dict(selection["original_release"])
    published = PublishedReleaseReader(reader, release_ref)
    objects = DirectoryStore(output / "task_objects")
    objects.publish_owned(reader, (release_ref, published.manifest.policy))
    # Retain the declared release for later follow-ups without copying unrelated
    # history. Loaded closures are verified before transferring owned objects.
    for revision in published.manifest.tasks:
        _publish_task(objects, reader, published.load(revision.task_id))
    # A follow-up result still identifies its original solver release, even
    # when the current preparation pins a replacement scoring policy.
    historical = {}
    for task_id in selection["rejudge"]:
        result = selection["original_outcomes"][task_id]["result"]
        ref = BlobRef.from_dict(result["release"])
        if ref == release_ref:
            continue
        if ref not in historical:
            historical[ref] = PublishedReleaseReader(reader, ref)
        task = historical[ref].load(task_id)
        if task.public.revision.as_dict() != result["revision"]:
            raise ValueError("saved result differs from its original task revision")
        _publish_task(objects, reader, task)
    for value in plan["manifests"]:
        manifest = TaskManifest.from_bytes(canonical_json(value))
        policy = objects.put(canonical_json(selection["policies"][manifest.task_id]))
        if policy != manifest.judge_policy:
            raise ValueError("prepared task differs from its pinned scoring policy")
        objects.put(manifest.to_bytes())
    release = ReleaseManifest.from_bytes(canonical_json(plan["release"]))
    if objects.put(release.to_bytes()).as_dict() != plan["settings"]["release"]:
        raise ValueError("prepared release differs from its pinned settings")
    publish_json(output / "selection.json", selection)
    publish_json(output / "settings.json", plan["settings"])


def prepare(output, sources, *, reroll=False, failure_codes=(), spending=None):
    """Pin one follow-up; recover preparation without reselecting tasks.

    An optional SpendPolicy binds an already authorized supplemental account.
    Only its USD budget and account binding replace inherited settings; solver
    settings and reservation bounds remain unchanged. This does not fund it.
    """
    from behave.agent.run_store import JournalDirectory
    from behave.core.records import fields, version

    output, sources = Path(output).resolve(), [Path(p).resolve() for p in sources]
    if reroll and failure_codes:
        raise ValueError("choose general reroll or specific infrastructure failure codes")
    if spending is not None:
        from behave.agent.spending import SpendPolicy

        if type(spending) is not SpendPolicy or spending.currency != "USD":
            raise ValueError("follow-up spending requires an explicit USD SpendPolicy")
        spending = {"budget_usd": spending.limit, "spending_directory": spending.directory,
                    "spending_account": spending.account}

    def check_request(selection, legacy_settings=None):
        if (selection["sources"] != [str(p) for p in sources] or selection["reroll"] != reroll
                or selection.get("failure_codes", []) != list(failure_codes)):
            raise ValueError("this output already belongs to a different follow-up")
        bound = selection.get("spending")
        if bound is None and spending is not None and legacy_settings is not None:
            bound = {key: legacy_settings.get(key) for key in spending}
        if bound != spending:
            raise ValueError("this output already belongs to a different spending request")

    with JournalDirectory(output).owner():
        receipt = output / "followup_preparation.json"
        if receipt.exists():
            plan = fields(json.loads(receipt.read_text()),
                          {"schema", "selection", "settings", "input_objects", "release", "manifests"})
            version(plan["schema"], "behave.eval-followup-preparation/1")
            check_request(plan["selection"])
            # Existing public records must agree before doing any more IO.
            for name in ("selection", "settings"):
                path = output / (name + ".json")
                if path.exists():
                    publish_json(path, plan[name])
            if (output / "selection.json").exists() and (output / "settings.json").exists():
                return plan["selection"]
        elif (output / "selection.json").exists():
            # Completed legacy preparations remain readable without a new plan
            # or source access. An incomplete legacy pair has no frozen settings.
            saved = json.loads((output / "selection.json").read_text())
            if not (output / "settings.json").is_file():
                raise ValueError("incomplete legacy follow-up has no pinned preparation; use a new output")
            check_request(saved, json.loads((output / "settings.json").read_text()))
            return saved
        else:
            if any(path.name != "owner.lock" for path in output.iterdir()):
                raise ValueError("unowned follow-up files require an explicit new output")
            plan = _preparation_plan(output, sources, reroll=reroll, failure_codes=failure_codes,
                                     spending=spending)
            publish_json(receipt, plan)
        _publish_preparation(output, plan)
        return plan["selection"]


@contextmanager
def retry_wave(directory, *, failure_codes, spending=None):
    """Own one arm's next explicit retry, reusing its unfinished preparation.

    The caller holds this context through worker execution. This lock prevents
    cooperating dispatchers from minting competing waves; run() still checks
    node ownership, retained settings and old worker cleanup separately.
    """
    from behave.agent.run_store import JournalDirectory
    from behave.eval.report import rollout_sources

    if not failure_codes:
        raise ValueError("retry wave requires explicit failure codes")
    directory = Path(directory).resolve()
    with JournalDirectory(directory / "retry_control").owner():
        chain = rollout_sources(directory)
        sources = []
        target = None
        for index, path in enumerate(chain):
            runtime = path / "runtime.json"
            state = json.loads(runtime.read_text()) if runtime.exists() else {}
            if state.get("state") == "archived":
                sources.append(path)
            elif index and index == len(chain) - 1:
                target = path
            else:
                raise ValueError(f"source rollout still unfinished: {path.name}")
        if target is None:
            target = sources[-1] / (sources[-1].name + "_infra_retry")
            if not select_retries(load_outcomes(sources), failure_codes=failure_codes)["rollout"]:
                yield None
                return
        selection = prepare(target, sources, failure_codes=failure_codes, spending=spending)
        yield target, selection


def _candidate_artifacts(objects, previous, row):
    """Keep original billing/submission and optional TB evidence, not the CAS."""
    from behave.agent.journal import load_chain
    from behave.core.artifacts import BlobRef
    from behave.task_execution.accounting import accounting_artifacts
    from behave.task_execution.result import TaskResult

    result = TaskResult.from_bytes(objects.read(previous))
    if result.task_id != row["task_id"]:
        raise ValueError("saved result belongs to another task")
    refs = [previous, *accounting_artifacts(objects, result)]
    if row.get("testbench") is not None:
        reference = BlobRef.from_dict(row["testbench"])
        report = json.loads(objects.read(reference))
        refs.append(reference)
        for event in load_chain(objects, BlobRef.from_dict(report["head"]), verify_artifacts=False):
            refs.extend((BlobRef.of(event.to_bytes()), *event.artifacts))
    return tuple(dict.fromkeys(refs))


def run_rejudgments(output, *, status_output, cancelled):
    """Use original submissions and accounting. This path has no model backend."""
    from behave.agent.admission import AdmissionPolicy, ResourcePool
    from behave.core.artifacts import BlobRef, DirectoryReader
    from behave.core.records import canonical_json
    from behave.sim import api
    from behave.task_execution.recovery import require_task_quiescent
    from behave.task_execution.rejudge import rejudge_task
    from behave.task_execution.result import TaskResult
    from behave.task_execution.run_store import RunStore
    from behave.task_execution.simulator_judge import SimulatorJudge

    settings = json.loads((output / "settings.json").read_text())
    selection = json.loads(Path(settings["retry"]["selection"]).read_text())
    if not selection["rejudge"]:
        return {}
    api.prepare_runtime(profiles=("simulation",), directory=output.parent / "runtime")
    reader = DirectoryReader(settings["task_objects"])
    pool = ResourcePool(AdmissionPolicy("eval-review-rejudging", settings["judge_workers"]))

    def one(task):
        prior = selection["original_outcomes"][task]
        old = Path(prior.get("candidate_directory") or
                   Path(prior["source"]) / "run/candidates" / prior["row"]["run_key"])
        original = RunStore(old)
        run = RunStore(output / "rejudgments" / task)
        previous = BlobRef.from_dict(prior["row"]["result"])
        with original.owner():
            require_task_quiescent(original, run_key=prior["row"]["run_key"])
            run.objects.publish_owned(original.objects, _candidate_artifacts(original.objects, previous, prior["row"]))
        with run.owner():
            require_task_quiescent(run, run_key=prior["row"]["run_key"])
        ref = rejudge_task(previous=previous, reader=reader, run=run, judge=SimulatorJudge(),
            judge_policy=canonical_json(selection["policies"][task]), admission=pool, cancelled=cancelled)
        result = TaskResult.from_bytes(run.objects.read(ref))
        old_result = TaskResult.from_bytes(run.objects.read(previous))
        # The scorer can change, but not the saved submission or model history.
        assert result.submission == old_result.submission and result.solver_head == old_result.solver_head
        row = {**prior["row"], "result": ref.as_dict(), "status": result.status, "score": result.score}
        return task, {"source": str(status_output), "row": row, "result": result.as_dict(),
                      "previous_result": previous.as_dict(),
                      "candidate_directory": str(status_output / "rejudgments" / task),
                      "new_model_calls": 0}

    with RunStore(output / "rejudgments").owner(), ThreadPoolExecutor(max_workers=settings["judge_workers"]) as workers:
        saved = output / "rejudged.json"
        results = json.loads(saved.read_text()) if saved.exists() else {}
        if set(results) - set(selection["rejudge"]):
            raise ValueError("saved rejudgments belong to a different selection")
        first_error = None
        futures = [workers.submit(one, task) for task in selection["rejudge"]]
        for future in as_completed(futures):
            try:
                task, item = future.result()
            except Exception as error:  # noqa: BLE001 -- drain siblings, then re-raise the first failure
                # Commit other completed tasks before propagating the failure.
                # On restart, rejudge_task replays its own committed journal.
                if first_error is None:
                    first_error = error
                continue
            results[task] = item
            write_json(output / "rejudged.json", results)
            progress = {name: {component: component_progress(part)
                         for component, part in item["result"]["judgment"].items()
                         if component in {"rtl", "behavior"}}
                        if item["result"]["judgment"] is not None else {"status": item["result"]["status"]}
                        for name, item in results.items()}
            write_json(status_output / "rejudge_progress.json", progress)
            print(json.dumps({"rejudged": task, "score": item["row"]["score"]}), flush=True)
        if first_error is not None:
            raise first_error
    return results


def summarize(output):
    """Read the follow-up and retained history; never dispatch or pick best-of."""
    from collections import Counter

    output = Path(output).resolve()
    selection = json.loads((output / "selection.json").read_text())
    current = load_outcomes([output])

    def rtl(item):
        result = item["result"]
        if result is None or result.get("failure_stage") == "solver":
            return {"status": "rollout_unavailable", "score": None}
        if result["submission"] is None:
            return {"status": "no_submission", "score": None}
        if result["judgment"] is None:
            return {"status": "execution_no_conclusion", "score": None, "code": result["failure_code"]}
        return component_progress(result["judgment"]["rtl"])

    rows = []
    for action in ("rollout", "rejudge"):
        for task in selection[action]:
            item = current[task]
            finished = item["source"] == str(output)
            rows.append({"task": task, "action": action,
                "before": rtl(selection["original_outcomes"][task]),
                "after": rtl(item) if finished else {"status": "pending", "score": None}})
    summary = {"note": "Selected retries plus retained successes, not an independent full-dataset accuracy run.",
        "selected": {key: len(selection[key]) for key in ("keep", "rollout", "rejudge")},
        "before_counts": dict(Counter(rtl(item)["status"] for item in selection["original_outcomes"].values())),
        "latest_known_counts": dict(Counter(rtl(item)["status"] for item in current.values())),
        "pending_retries": sum(row["after"]["status"] == "pending" for row in rows),
        "rows": rows}
    write_json(output / "followup_summary.json", summary)
    lines = ["# Selected eval follow-up\n", summary["note"] + "\n",
             "Baseline-only for new scoring. Retained historical scores are unchanged.\n",
             "| Task | Action | Previous RTL | New RTL | Baseline |",
             "| --- | --- | --- | --- | --- |"]
    for row in rows:
        after = row["after"]
        progress = after.get("baseline", {"status": "not_recorded"})
        detail = progress["status"]
        if progress.get("generated") is not None:
            detail += f" ({progress['completed']}/{progress['generated']})"
        status = after["status"] + (f" ({after['code']})" if after.get("code") else "")
        lines.append(f"| {row['task']} | {row['action']} | {row['before']['status']} | {status} | {detail} |")
    (output / "FOLLOWUP.md").write_text("\n".join(lines) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", type=Path, action="append",
                        help="completed review directories, oldest first")
    parser.add_argument("--report", action="store_true", help="read existing follow-up results without dispatch")
    parser.add_argument("--reroll", action="store_true",
                        help="explicitly spend model calls on failed/unsubmitted tasks as new attempts")
    parser.add_argument("--failure-code", action="append", default=[],
                        help="retry only unsubmitted attempts with this failure code; may be repeated")
    args = parser.parse_args(argv)
    if args.report:
        print(json.dumps(summarize(args.output), indent=2))
        return
    if not args.source:
        parser.error("preparing a follow-up requires --source")
    selection = prepare(args.output, args.source, reroll=args.reroll, failure_codes=args.failure_code)
    print(json.dumps({key: len(selection[key]) for key in ("keep", "rollout", "rejudge")}, indent=2))


OWNER = "behave.eval-review-owner/1"


def _quiescent(local):
    from behave.task_execution.recovery import require_task_quiescent
    from behave.task_execution.run_store import RunStore

    # Only this retained node-local run is visited, never a source/cold CAS.
    for root, candidates in ((local / "run", local / "run/candidates"),
                             (local / "rejudgments", local / "rejudgments")):
        if not root.exists():
            continue
        with RunStore(root).owner():
            for path in sorted(candidates.iterdir()) if candidates.exists() else ():
                if path.is_dir() and path.name != "objects":
                    with RunStore(path).owner():
                        require_task_quiescent(RunStore(path), run_key=path.name)


def _continue(output, runtime, settings_bytes):
    if runtime.get("owner_protocol") != OWNER or runtime.get("host") != socket.gethostname():
        raise RuntimeError("review recovery requires its owning node and a recorded owner; legacy runs need reconciliation")
    local = Path(runtime["local_directory"])
    if (not local.is_absolute() or local.is_symlink() or local.parent.is_symlink()
            or local.name != output.name or not local.parent.name.startswith("behave-eval-review-")):
        raise ValueError("invalid retained review scratch")
    owner = {"schema": OWNER, "output": str(output), "local_directory": str(local),
             "host": socket.gethostname(), "settings": BlobRef.of(settings_bytes).as_dict()}
    if (json.loads((local / "review_owner.json").read_text()) != owner
            or (local / "settings.json").read_bytes() != settings_bytes):
        raise ValueError("retained review belongs to different settings or output")
    _quiescent(local)
    return local


def _execute(local, output, settings):
    if "retry" in settings:
        from behave.eval.followup import run_rejudgments

        with command_scope() as cancelled:
            run_rejudgments(local, status_output=output, cancelled=cancelled)
            # A scorer may finish its last result just as cancellation arrives.
            # Preserve it, but do not dispatch the next phase or mark completion.
            if cancelled():
                return 1
    return generation.run(local, status_output=output) if settings.get("tasks") != [] else 0


def run_review(output, *, resume=False):
    """Stage once; explicitly resume pinned journals or only finish their archive."""
    from behave.core.process_limits import configure_worker_process_limit

    output = Path(output).resolve()
    with JournalDirectory(output).owner():
        runtime_path = output / "runtime.json"
        previous = json.loads(runtime_path.read_text()) if runtime_path.exists() else None
        if previous is not None:
            if previous["state"] == "archived":
                # A crash after committing archive status but before cleanup
                # may leave this owner's scratch. Do not touch legacy/remote
                # locations or recreate a missing retained directory.
                if (resume and previous.get("owner_protocol") == OWNER
                        and previous.get("host") == socket.gethostname()
                        and Path(previous["local_directory"]).exists()):
                    local = _continue(output, previous, (output / "settings.json").read_bytes())
                    if json.loads((local / "review_finished.json").read_text()) != {"exit_code": 0}:
                        raise ValueError("invalid review completion receipt")
                    with ScratchDirectory.retained(local.parent, cleanup=lambda: CleanupState.COMPLETE):
                        pass
                return 0
            if not resume:
                raise RuntimeError("review has an existing node-local run; resume it before dispatching again")
            if previous["state"] not in {"running", "archiving", "failed-retained"}:
                raise RuntimeError("review state requires explicit reconciliation")
        settings_bytes = (output / "settings.json").read_bytes()
        settings = json.loads(settings_bytes)
        limits = configure_worker_process_limit(settings.get("process_limit"))
        archived = False
        cleanup = lambda: CleanupState.COMPLETE if archived else CleanupState.UNKNOWN
        if previous is None:
            scratch = ScratchDirectory(prefix="behave-eval-review-", cleanup=cleanup)
            local = scratch.path / output.name
            local.mkdir()
            (local / "settings.json").write_bytes(settings_bytes)
            publish_json(local / "review_owner.json", {
                "schema": OWNER, "output": str(output), "local_directory": str(local),
                "host": socket.gethostname(), "settings": BlobRef.of(settings_bytes).as_dict()})
        else:
            local = _continue(output, previous, settings_bytes)
            scratch = ScratchDirectory.retained(local.parent, cleanup=cleanup)
        runtime = {"host": socket.gethostname(), "local_directory": str(local),
                   "state": "running", "process_limit": limits, "owner_protocol": OWNER}
        prior_scratch = os.environ.get("BEHAVE_SCRATCH_DIR")
        with scratch:
            # Publish the recovery pointer before starting any model/tool work.
            write_json(runtime_path, runtime, durable=True)
            os.environ["BEHAVE_SCRATCH_DIR"] = str(local.parent)
            finished = local / "review_finished.json"
            code = None
            completed = False
            try:
                if finished.exists():
                    if json.loads(finished.read_text()) != {"exit_code": 0}:
                        raise ValueError("invalid review completion receipt")
                    code = 0
                    completed = True
                else:
                    code = _execute(local, output, settings)
                    if code == 0:
                        _quiescent(local)
                        publish_json(finished, {"exit_code": 0})
                        completed = True
                return code
            finally:
                try:
                    write_json(runtime_path, {**runtime, "state": "archiving", "exit_code": code}, durable=True)
                    archive_run(local, output)
                    state = "archived" if completed else "failed-retained"
                    write_json(runtime_path, {**runtime, "state": state, "exit_code": code}, durable=True)
                    archived = completed
                finally:
                    if prior_scratch is None:
                        os.environ.pop("BEHAVE_SCRATCH_DIR", None)
                    else:
                        os.environ["BEHAVE_SCRATCH_DIR"] = prior_scratch


def prepare_review(output, template, *, setting=None, tasks=None, workers=None, rollout_only=False,
            show_behavior=False):
    """Reuse explicit model/data settings, without publishing data or calling a model."""
    settings = json.loads(Path(template).read_text())
    settings.pop("retry", None)
    # Capture the durable run identity before staging into a node-local path.
    # A copied template must never retain another attempt's paid-call IDs.
    settings["attempt_namespace"] = str(Path(output).resolve())
    if setting is not None:
        settings["setting"] = setting
        settings.pop("solver_settings", None)
        settings.pop("cases", None)
    if show_behavior:
        if settings.get("setting", "joint") != "rtl_sim":
            raise ValueError("--show-behavior applies to the supplied-reference rtl_sim setting")
        settings["reference_visible"] = True
    elif settings.get("setting", "joint") != "rtl_sim":
        settings.pop("reference_visible", None)
    if tasks is not None:
        settings["tasks"] = Path(tasks).read_text().split()
    if workers is not None:
        settings["workers"] = settings["model_workers"] = workers
    if rollout_only:
        settings["score_on_submit"] = False
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "settings.json", settings)
    return {"output": str(output), "model": settings["model"], "setting": settings.get("setting", "joint"),
            "tasks": len(settings["tasks"]), "workers": settings["workers"],
            "score_on_submit": settings.get("score_on_submit", True)}


SCHEMA = "behave.eval-hosted-supplement/1"
FAILURES = ("model-permanent:model-budget-exhausted",)


def _read(path):
    return json.loads(path.read_text())


def _plan(root, authorization, manifest, source):
    names = authorization["runs"]
    available = [row["name"] for row in manifest["runs"]]
    if (not isinstance(names, list) or not names or len(set(names)) != len(names)
            or len(set(available)) != len(available) or set(names) - set(available)
            or any(not isinstance(name, str) or Path(name).name != name or name in {".", ".."} for name in names)
            or set(authorization.get("after_infra_repair", ())) - set(names)):
        raise ValueError("supplement requires distinct authorized campaign arms")
    request = {"authorization": authorization, "campaign": BlobRef.of(canonical_json(manifest)).as_dict(),
               "source": None if source is None else str(Path(source).resolve())}
    path = root / "supplement_plan.json"
    if path.exists():
        plan = _read(path)
        if plan["schema"] != SCHEMA or plan["request"] != request:
            raise ValueError("supplement authorization or campaign changed; retain the original plan")
        if plan["host"] != socket.gethostname():
            raise ValueError("resume the supplemental account on its owning node")
        local = Path(plan["local_directory"])
        if local.is_symlink() or not local.is_dir():
            raise ValueError("retained supplemental account is missing; refusing to recreate it")
        if _read(local / "supplement_owner.json") != {"root": str(root), "plan": BlobRef.of(canonical_json(plan)).as_dict()}:
            raise ValueError("retained supplemental directory belongs to another plan")
        return plan
    if (root / "supplement_runtime.json").exists():
        raise ValueError("legacy supplemental controller requires explicit reconciliation")
    # Validate money/configuration before allocating a local directory.
    policy = SpendPolicy(str(root / "unallocated"), root.name + ":supplement", "USD",
                         authorization["additional_budget_usd"], manifest["configuration"]["attempt"]["context_tokens"])
    local = Path(mkdtemp(prefix="behave-hosted-supplement-", dir="/tmp"))
    local.chmod(0o700)
    plan = {"schema": SCHEMA, "request": request, "host": socket.gethostname(), "local_directory": str(local),
            "account": policy.account, "budget_usd": policy.limit, "maximum_input_tokens": policy.maximum_input_tokens,
            "runs": [name for name in available if name in names]}
    publish_json(local / "supplement_owner.json", {"root": str(root), "plan": BlobRef.of(canonical_json(plan)).as_dict()})
    publish_json(path, plan)
    return plan


def _binding(plan):
    return SpendPolicy(str(Path(plan["local_directory"]) / "spending"), plan["account"], "USD",
                       plan["budget_usd"], plan["maximum_input_tokens"])


def _target(root, name, intent):
    if intent["target"] is None:
        if intent["tasks"]:
            raise ValueError("empty supplemental intent has tasks")
        return None
    target = Path(intent["target"]).resolve()
    if str(target) != intent["target"]:
        raise ValueError("supplemental target must be a canonical owned path")
    if not target.is_relative_to(root / "runs" / name) or target == root / "runs" / name:
        raise ValueError("supplemental target belongs to another arm")
    for key in ("settings", "selection"):
        if BlobRef.of((target / (key + ".json")).read_bytes()).as_dict() != intent[key]:
            raise ValueError("supplemental target differs from its pinned intent")
    return target


def _archived(target):
    if _read(target / "runtime.json")["state"] != "archived":
        raise RuntimeError("supplemental worker has not committed its archive")


def _dispatch(root, plan, name, control, intent, stopped):
    target = _target(root, name, intent)
    result_path = control / "result.json"
    expected = {"run": name, "tasks": intent["tasks"], "exit_code": 0}
    if result_path.exists():
        if _read(result_path) != expected:
            raise ValueError("supplemental result differs from its pinned intent")
        return expected
    if target is not None:
        if stopped.is_set():
            return {**expected, "exit_code": 1}
        program = "from behave.eval.followup import run_review; raise SystemExit(run_review(Path(sys.argv[1]), resume=True))"
        source = plan["request"]["source"]
        command = (source_command(Path(source), program, target) if source is not None else
                   [sys.executable, "-I", "-B", "-u", "-c", "import sys; from pathlib import Path; " + program, str(target)])
        code = run_child(command, Path(plan["local_directory"]) / (name + ".log"), stopped)
        if code != 0:
            return {**expected, "exit_code": code}
        with JournalDirectory(target).owner():
            _archived(target)
    publish_json(result_path, expected)
    return expected


def _arm(root, plan, name, stopped):
    directory = root / "runs" / name
    control = root / "supplement_controller/arms" / name
    with JournalDirectory(control).owner():
        intent_path = control / "intent.json"
        if intent_path.exists():
            with JournalDirectory(directory / "retry_control").owner():
                return _dispatch(root, plan, name, control, _read(intent_path), stopped)
        if name in plan["request"]["authorization"].get("after_infra_repair", []):
            wait_control(root / "repair-hosted_control")
        with retry_wave(directory, failure_codes=FAILURES, spending=_binding(plan)) as pending:
            if pending is None:
                intent = {"target": None, "tasks": []}
            else:
                target, selection = pending
                intent = {"target": str(target), "tasks": selection["rollout"],
                          **{key: BlobRef.of((target / (key + ".json")).read_bytes()).as_dict()
                             for key in ("settings", "selection")}}
            publish_json(intent_path, intent)
            return _dispatch(root, plan, name, control, intent, stopped)


def _finalize(root, plan, results):
    with ExitStack() as ownership:
        # Targeted arms and other retry callers must not race account closure.
        for name in sorted(plan["runs"]):
            control = root / "supplement_controller/arms" / name
            ownership.enter_context(JournalDirectory(control).owner())
            ownership.enter_context(JournalDirectory(root / "runs" / name / "retry_control").owner())
            intent = _read(control / "intent.json")
            result = _read(control / "result.json")
            if result != {"run": name, "tasks": intent["tasks"], "exit_code": 0} or result not in results:
                raise ValueError("incomplete supplemental arm cannot close its account")
            target = _target(root, name, intent)
            if target is not None:
                ownership.enter_context(JournalDirectory(target).owner())
                _archived(target)
        publish_json(root / "supplement_controller/workers_finished.json", results)
        billing = root / "supplement_billing.json"
        if not billing.exists():
            publish_json(billing, SpendAccount(_binding(plan)).summary())
        archive_spending(Path(plan["local_directory"]) / "spending", root / "supplement_spending")


def run_supplement(root, *, run_name=None, source=None):
    """Resume one frozen supplement; a selected arm never settles shared money."""
    root = Path(root).resolve()
    owner = JournalDirectory(root / "supplement_controller")
    with owner.owner() if run_name is None else nullcontext():
        authorization, manifest = (_read(root / name) for name in ("supplement.json", "campaign.json"))
        state_path = root / "supplement_runtime.json"
        previous = _read(state_path) if state_path.exists() else None
        # Complete old records remain readable, but an unowned live legacy
        # controller is not adopted based on a status label alone.
        if previous is not None and previous["state"] == "archived" and run_name is None:
            return previous
        if run_name is not None:
            if run_name not in authorization["runs"]:
                raise ValueError("selected arm is not authorized for this supplement")
            if not (root / "supplement_plan.json").exists():
                raise ValueError("start the authorized shared supplement before selecting an arm")
        plan = _plan(root, authorization, manifest, source)
        state = {key: plan[key] for key in ("host", "local_directory", "budget_usd", "account")}
        state.update(state="running", owner_protocol=SCHEMA)
        stopped = Event()
        handlers = {sig: signal.signal(sig, lambda *_: stopped.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            if run_name is not None:
                return _arm(root, plan, run_name, stopped)
            write_json(state_path, state, durable=True)
            try:
                finished = root / "supplement_controller/workers_finished.json"
                if finished.exists():
                    results = _read(finished)
                else:
                    lanes = CpuResources(len(plan["runs"]))

                    def arm(name):
                        with cpu_job(lanes, cancelled=stopped.is_set) as lease:
                            result = _arm(root, plan, name, stopped)
                            if result["exit_code"] == 0 and not stopped.is_set():
                                lease.cleanup = CleanupState.COMPLETE
                            return result

                    with ThreadPoolExecutor(max_workers=len(plan["runs"])) as pool:
                        results = list(pool.map(arm, plan["runs"]))
                if any(row["exit_code"] != 0 for row in results) or stopped.is_set():
                    state.update(state="failed", runs=results)
                else:
                    _finalize(root, plan, results)
                    state.update(state="archived", runs=results)
                write_json(state_path, state, durable=True)
                return state
            except Exception as error:
                try:
                    write_json(state_path, {**state, "state": "failed", "error": str(error)}, durable=True)
                except OSError as report_error:
                    error.add_note(f"could not update controller status: {report_error}")
                raise
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
