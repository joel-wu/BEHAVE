"""Training rollouts, release-to-plan preparation, and native job submission.

The default rollout SDK does not load a checkpoint or optimizer. --native-endpoint
selects original token-enabled profiles (repeat for history). The explicit
``prepare`` subcommand builds a plan; ``submit`` launches the native training job
on an existing allocation. ``prepare-study`` freezes self-improvement stages
without launching training. ``runtime`` prepares pinned sources or inspects an
explicit native environment. ``report`` exports recorded learning metrics without
loading a model or dispatching work. Use a subcommand with --help for its arguments.
"""

import argparse
import sys
from pathlib import Path

from behave.agent.attempt import AttemptInterrupted
from behave.cli.diagnostics import report_error
from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json

from .plan import TrainingManifest, TrainingPlan


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["report"]:
        from .learning_report import main as report_main

        return report_main(argv[1:])
    if argv[:1] == ["prepare"]:
        from behave.cli.training_plan import main as prepare_main

        return prepare_main(argv[1:])
    if argv[:1] == ["submit"]:
        from behave.cli.training_job import main as submit_main

        return submit_main(argv[1:])
    if argv[:1] == ["prepare-study"]:
        from behave.cli.training_study import main as study_main

        return study_main(argv[1:])
    if argv[:1] == ["runtime"]:
        from behave.cli.training_runtime import main as runtime_main

        return runtime_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--approved-manifest", type=Path, required=True)
    parser.add_argument("--task-objects", type=Path, required=True)
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--native-endpoint", type=Path, action="append",
                        help="approved token-enabled LocalChatEndpoint JSON; repeat for history")
    args = parser.parse_args(argv)
    if not args.dry_run and args.run_directory is None:
        parser.error("--run-directory is required unless --dry-run is selected")
    try:
        packet = read_regular_file(args.plan)
        approved = read_regular_file(args.approved_manifest)
        TrainingManifest.from_bytes(approved)
        reader = DirectoryReader(args.task_objects)
        # A resumed plan resolves its public files from the previous local closure.
        inputs = reader
        if args.run_directory is not None and (args.run_directory / "training").is_dir():
            inputs = DirectoryReader(args.run_directory / "objects")
        plan = TrainingPlan.from_bytes(packet, inputs)
        plan.preflight(inputs, approved_manifest=BlobRef.of(approved))
        verifier = None
        if args.native_endpoint is not None:
            from .training_data import NativeChatVerifier

            verifier = NativeChatVerifier.from_files(args.native_endpoint)
            verifier.require(plan.requirements)
        if args.dry_run:
            output = {
                "status": "validated",
                "plan": BlobRef.of(packet).as_dict(),
                "candidates": len(plan.execution.jobs),
                "optimizer": False,
            }
        else:
            from behave.agent.run_store import JournalDirectory
            from behave.runtime.records import command_scope
            from behave.task_execution.cocotb_worker import CocotbBackend
            from behave.task_execution.simulator_judge import SimulatorJudge

            from .rollouts import run_rollouts

            with command_scope() as cancelled:
                from behave.core.process_limits import configure_worker_process_limit

                configure_worker_process_limit()
                output = run_rollouts(
                    plan=plan,
                    approved_manifest=BlobRef.of(approved),
                    reader=reader,
                    run=JournalDirectory(args.run_directory),
                    judge=SimulatorJudge(),
                    testbench=None if plan.execution.testbench is None else CocotbBackend(),
                    cancelled=cancelled,
                    verifier=verifier,
                )
    except (RecordError, AttemptInterrupted, ValueError, TypeError, OSError) as error:
        report_error(error)
        print(
            canonical_json(
                {"status": "unavailable", "code": getattr(error, "code", "training-command-invalid")}
            ).decode()
        )
        return 1
    print(canonical_json(output).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
