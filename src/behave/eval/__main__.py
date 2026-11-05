"""Run/score a saved evaluation plan, or use `prepare`, `export` and their `--help`.

Preparation is offline. Running a plan may dispatch its configured model.
"""

import argparse
import sys
from dataclasses import replace
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["prepare"]:
        from behave.cli.evaluation_plan import main as prepare_main

        return prepare_main(argv[1:])
    if argv[:1] == ["controller"]:
        from behave.eval.controller import main as controller_main

        return controller_main(argv[1:])
    if argv[:1] == ["followup"]:
        from behave.eval.followup import main as followup_main

        return followup_main(argv[1:])
    if argv[:1] == ["export"]:
        from behave.eval.report import main as export_main

        return export_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--task-objects", required=True, type=Path)
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="validate the plan without starting a worker")
    parser.add_argument("--rollout-only", action="store_true",
                        help="save model rollouts and submissions without final Sim or TB scoring")
    parser.add_argument("--score", "--retry-judgment", dest="retry_judgment", metavar="NAME",
                        help="score saved unscored submissions into judgments/NAME, without a model call")
    parser.add_argument("--judge-workers", type=int, help="concurrent scoring workers, independent of rollout workers")
    parser.add_argument("--isolation-startup-slots", type=int,
                        help="optional extra sandbox-startup throttle; omitted means no extra queue")
    parser.add_argument("--from-judgment", metavar="NAME", help="use an earlier judgment report instead of the original")
    parser.add_argument("--judge-policy", type=Path, help="explicit JudgePolicy JSON override for replacement scoring")
    args = parser.parse_args(argv)
    if args.retry_judgment:
        if args.plan is not None or args.dry_run or args.rollout_only:
            parser.error("--score reads the saved report, not --plan, --dry-run or --rollout-only")
    elif args.plan is None or args.from_judgment is not None or args.judge_policy is not None or args.judge_workers is not None:
        parser.error("generation requires --plan; judgment overrides require --score")
    if not args.dry_run and args.run_directory is None:
        parser.error("--run-directory is required unless --dry-run is selected")

    from behave.core.artifacts import DirectoryReader
    from behave.core.file_input import read_regular_file
    from behave.core.records import RecordError, canonical_json

    try:
        from behave.isolation.startup import startup_environment

        startup = startup_environment(args.isolation_startup_slots)
        reader = DirectoryReader(args.task_objects)
        if not args.retry_judgment:
            from .plan import EvaluationPlan

            data = read_regular_file(args.plan)
            inputs = reader
            if args.run_directory is not None and (args.run_directory / "report").is_dir():
                inputs = DirectoryReader(args.run_directory / "objects")
            plan = EvaluationPlan.from_bytes(data, inputs)
            if args.rollout_only:
                plan = replace(plan, score_on_submit=False)
            if plan.score_on_submit:
                from behave.task_execution.cocotb_worker import BACKEND
                from behave.task_execution.comparison import COMPARISON_BACKEND

                if (plan.judge_backend != COMPARISON_BACKEND or (plan.testbench is not None
                        and plan.testbench.backend != BACKEND)):
                    raise ValueError("unsupported evaluation backend")
            if args.dry_run:
                print(canonical_json({"status": "validated", "plan": plan.identity.as_dict(),
                                      "candidates": len(plan.jobs), "score_on_submit": plan.score_on_submit}).decode())
                return 0
    except (RecordError, ValueError, TypeError, OSError) as error:
        print(canonical_json({"status": "unavailable", "code": getattr(error, "code", "evaluation-command-invalid"),
                              "message": str(error)}).decode())
        return 1

    from behave.agent.attempt import AttemptInterrupted
    from behave.runtime.records import command_scope
    from behave.task_execution.batch import execution_status
    from behave.task_execution.run_store import RunStore

    try:
        from behave.core.process_limits import configure_worker_process_limit

        # Only the trusted coordinator owns signals and the startup policy.
        with command_scope(environment=startup) as cancelled:
            configure_worker_process_limit()
            if args.retry_judgment:
                from behave.task_execution.cocotb_worker import CocotbBackend
                from behave.task_execution.rejudge import rejudge_batch
                from behave.task_execution.simulator_judge import SimulatorJudge

                report = rejudge_batch(directory=args.run_directory, name=args.retry_judgment, reader=reader,
                                      judge=SimulatorJudge(), previous=args.from_judgment,
                                      testbench=CocotbBackend(), workers=args.judge_workers,
                                      judge_policy=None if args.judge_policy is None else read_regular_file(args.judge_policy),
                                      cancelled=cancelled)
            else:
                from .runner import run_evaluation

                judge, testbench = None, None
                if plan.score_on_submit:
                    from behave.task_execution.simulator_judge import SimulatorJudge

                    judge = SimulatorJudge()
                    if plan.testbench is not None:
                        from behave.task_execution.cocotb_worker import CocotbBackend

                        testbench = CocotbBackend()
                report = run_evaluation(plan=plan, reader=reader, directory=args.run_directory,
                                        judge=judge, testbench=testbench,
                                        cancelled=cancelled)
            print(canonical_json({"summary": report.content.as_dict(), "head": report.head.as_dict(),
                                  **execution_status(report, RunStore(args.run_directory).objects)}).decode())
        return 0
    except (RecordError, AttemptInterrupted, ValueError, TypeError, OSError) as error:
        print(canonical_json({"status": "unavailable", "code": getattr(error, "code", "evaluation-command-invalid"),
                              "message": str(error)}).decode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
