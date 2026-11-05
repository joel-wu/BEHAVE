"""Real dataset Eval review using the shared runner and lossless journal export.

This is an experiment launcher, not another solver or judge. Settings are
explicit in the run directory. Failed candidates remain in the report.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from behave.eval import generation
from behave.eval.followup import prepare_review as prepare
from behave.eval.followup import run_review as run
from behave.eval.report import component_progress, export_review  # noqa: F401 -- legacy exports
from behave.eval.scoring import (
    _stage_score_objects as _stage_score_objects,  # noqa: PLC0414 -- retained operator export
)
from behave.eval.scoring import (
    score_review as score,
)
from behave.eval.scoring import (
    score_saved as _score_local,  # noqa: F401 -- legacy export
)
from behave.eval.scoring import (
    scoring_environment,  # noqa: F401 -- legacy export
)
from behave.runtime.records import archive_run as archive_run  # noqa: PLC0414 -- retained operator export
from behave.runtime.records import write_json

# Keep historical Python entry names without another implementation.
attempt_name = generation.attempt_name
endpoint_for = generation.endpoint_for
export_candidate = generation.export_candidate
load_tasks = generation.load_tasks
reasoning_blocks = generation.reasoning_blocks
solver_cases = generation.solver_cases
trace_name = generation.trace_name
_run_local = generation.run
_run_local_with_scratch = generation._run_with_scratch
_check_development_shell = generation.check_development_shell


def submit(output, allocation, *, snapshot=None):
    from behave.runtime.jobs import submit as detached
    from behave.runtime.snapshots import freeze, staged_command, verify

    repository = Path(__file__).resolve().parents[1]
    settings = json.loads((output / "settings.json").read_text())
    if (output / "control/launch.json").exists():
        from behave.runtime.jobs import status_of

        return status_of(output / "control")
    if snapshot is None:
        snapshot = output / "snapshot"
        freeze(repository, snapshot, extra_files=("tools/eval_review_trial.py", "tools/eval_review_retry.py"))
    else:
        verify(Path(snapshot))
    snapshot = Path(snapshot).resolve()
    worker = "from tools.eval_review_trial import run; raise SystemExit(run(Path(sys.argv[1])))"
    job_seconds = settings.get("job_seconds")
    argv = ["srun", f"--jobid={allocation}", "--overlap", "--exact", "--nodes=1", "--ntasks=1",
            f"--cpus-per-task={settings['cpus']}", "--gres=none", "--kill-on-bad-exit=1",
            *([] if job_seconds is None else [f"--time={job_seconds // 60}"]),
            *staged_command(snapshot, worker, output, prefix="behave-eval-code-", temporary=True)]
    return detached(output / "control", argv, cwd=Path(__file__).resolve().parents[1], wall_seconds=None if job_seconds is None else job_seconds + 120,
                    metadata={"purpose": "real dataset multi-turn Eval review", "allocation": allocation,
                              "tasks": len(settings["tasks"]), "model": settings["model"]})


def audit_billing(output, prices):
    """Reprice exported provider usage without changing any original record."""
    from dataclasses import replace
    from decimal import Decimal

    from behave.agent.billing import sum_amounts
    from behave.agent.providers.hosted_usage import usage_of

    settings = json.loads((output / "settings.json").read_text())
    endpoint = replace(endpoint_for(output, settings), prices=prices)
    rows = []
    for path in sorted((output / "traces").glob("*/*_provider_raw.json")):
        raw = json.loads(path.read_bytes())
        usage, charge = usage_of(endpoint, raw)
        if not endpoint.accepts_model(raw.get("model")):
            charge = None
        reply = json.loads(path.with_name(path.name.replace("_provider_raw", "_reply")).read_bytes())
        rows.append({"task": path.parent.name, "raw": str(path.relative_to(output)),
                     "response_id": raw.get("id"), "usage": usage,
                     "recorded_charge": reply.get("charge"),
                     "audited_charge": None if charge is None else charge.as_dict()})
    audited = [row["audited_charge"] for row in rows if row["audited_charge"] is not None]
    recorded = [row["recorded_charge"] for row in rows if row["recorded_charge"] is not None]
    result = {"prices": prices.as_dict(), "calls": len(rows), "unpriced_calls": len(rows) - len(audited),
              "audited_usd": str(sum_amounts(Decimal(c["amount"]) for c in audited)),
              "recorded_usd": str(sum_amounts(Decimal(c["amount"]) for c in recorded)),
              "source": "exported API usage, not a provider invoice", "rows": rows}
    write_json(output / "billing_audit.json", result)
    return result


def run_staged(snapshot, output):
    """Compatibility entry; the worker never inherits this process's imports."""
    from behave.runtime.snapshots import source_command, stage

    with TemporaryDirectory(prefix="behave-eval-code-", dir="/tmp") as scratch:
        local = Path(scratch) / "checkout"
        stage(Path(snapshot), local)
        command = source_command(local,
            "from tools.eval_review_trial import run; raise SystemExit(run(Path(sys.argv[1])))", output)
        return subprocess.run(command, check=False).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "submit", "run", "score", "export"))
    parser.add_argument("--resume", action="store_true", help="continue the same retained review on its owning node")
    parser.add_argument("output", type=Path)
    parser.add_argument("--allocation", type=int)
    parser.add_argument("--snapshot", type=Path, help="reuse a prepared common source snapshot")
    parser.add_argument("--template", type=Path, help="existing settings.json to reuse in a new run")
    parser.add_argument("--setting", choices=("joint", "rtl_only", "joint_sim", "rtl_sim"))
    parser.add_argument("--show-behavior", action="store_true",
                        help="mount the supplied reference read-only for rtl_sim")
    parser.add_argument("--tasks", type=Path, help="task IDs, one per line, from the template's published release")
    parser.add_argument("--workers", type=int, help="model/rollout concurrency for prepare")
    parser.add_argument("--rollout-only", action="store_true", help="prepare a run without final scoring")
    parser.add_argument("--judgment", help="scoring attempt name for score/export")
    parser.add_argument("--judge-workers", type=int, help="scoring concurrency")
    parser.add_argument("--from-judgment", help="resume unscored submissions from an earlier scoring attempt")
    parser.add_argument("--judge-policy", type=Path, help="explicit JudgePolicy override for scoring")
    parser.add_argument("--all-submitted", action="store_true",
                        help="fresh judge pass, including already scored submissions; no model calls")
    args = parser.parse_args()
    if args.resume and args.action != "run":
        parser.error("--resume applies only to run")
    output = args.output.resolve()
    if args.action == "prepare":
        if args.template is None:
            parser.error("prepare requires --template")
        print(json.dumps(prepare(output, args.template, setting=args.setting, tasks=args.tasks,
                                 workers=args.workers, rollout_only=args.rollout_only,
                                 show_behavior=args.show_behavior), indent=2))
        return 0
    if args.action == "submit":
        if args.allocation is None:
            parser.error("submit requires --allocation")
        print(json.dumps(submit(output, args.allocation, snapshot=args.snapshot), indent=2))
        return 0
    if args.action == "export":
        export_review(output, judgment=args.judgment)
        return 0
    if args.action == "score":
        if args.judgment is None:
            parser.error("score requires --judgment NAME")
        report = score(output, args.judgment, workers=args.judge_workers,
                       previous=args.from_judgment, judge_policy=args.judge_policy,
                       all_submitted=args.all_submitted)
        print(json.dumps({"summary": report.content.as_dict(), "head": report.head.as_dict()}, indent=2))
        return 0
    return run(output, resume=args.resume)


if __name__ == "__main__":
    raise SystemExit(main())
