"""Submit a native training job on an explicitly selected Slurm allocation.

This command uses the same submission implementation as the source-checkout
launcher. It does not request an allocation or implicitly update a saved runtime.
"""

import argparse
import json
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.train.jobs import submit, wait_for_completion


def main(argv=None, *, repository: Path | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repository", type=Path, required=repository is None, default=repository,
                        help="source checkout to freeze and use for split membership and maintenance inputs")
    parser.add_argument("--allocation", type=int, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--machine-profile", type=Path,
                        help="explicit deployment settings for a split experiment; omit for saved flat configs")
    parser.add_argument("--release", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--task", action="append")
    selection.add_argument("--tasks-file", type=Path,
                           help="explicit task IDs, still checked against --train-split and current eval sets")
    parser.add_argument("--train-split", type=Path, required=True)
    parser.add_argument("--cpus", type=int, required=True)
    parser.add_argument("--checkpoint-directory", type=Path,
                        help="external native save directory; otherwise use the workspace storage profile")
    parser.add_argument("--updates", type=int,
                        help="absolute total updates (40 -> 50: pass 50), without a cold-resume probe phase")
    parser.add_argument("--training-seed", type=int,
                        help="one seed for pool shuffle and native trainer/rollout engine initialization")
    parser.add_argument("--workers", type=int, help="total concurrent candidates, not per engine")
    parser.add_argument("--rollout-gpus-per-engine", type=int, help="serving TP only; actor layout is unchanged")
    parser.add_argument("--exclude-rollout-gpu", type=int, action="append", default=None,
                        help="full-node GPU slot to skip during TP1 rollout; does not select actor ranks")
    parser.add_argument("--actor-gpus-per-node", type=int,
                        help="training ranks on the first visible GPU slots; independent of rollout engines")
    parser.add_argument("--context-parallel", type=int,
                        help="actor context parallelism; resume permits CP resharding with unchanged TP/PP/DP")
    parser.add_argument("--resume-from", type=Path, help="previous job's latest completed checkpoint, keeping its frozen code")
    parser.add_argument("--resume-rollouts", action="store_true",
                        help="also carry completed candidates from the first uncommitted round")
    parser.add_argument("--workers-stopped", action="store_true",
                        help="confirm source trainer, engines and candidate workers were stopped/fenced")
    parser.add_argument("--after-completion", action="store_true",
                        help="wait for --resume-from to finish and release resources before submitting")
    parser.add_argument("--poll-seconds", type=float, default=defaults.JOB_HEARTBEAT_SECONDS)
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    if args.after_completion:
        if args.resume_from is None or args.poll_seconds <= 0:
            parser.error("--after-completion requires --resume-from and a positive --poll-seconds")
        wait_for_completion(args.resume_from, poll_seconds=args.poll_seconds)
    tasks = args.task if args.tasks_file is None else args.tasks_file.read_text().split()
    continuation = {} if args.resume_from is None else {"resume_from": args.resume_from}
    if args.updates is not None:
        continuation["updates"] = args.updates
    if args.training_seed is not None:
        continuation["training_seed"] = args.training_seed
    if args.workers is not None:
        continuation["workers"] = args.workers
    if args.rollout_gpus_per_engine is not None:
        continuation["rollout_gpus_per_engine"] = args.rollout_gpus_per_engine
    if args.exclude_rollout_gpu is not None:
        continuation["rollout_excluded_gpus"] = tuple(args.exclude_rollout_gpu)
    for name in ("actor_gpus_per_node", "context_parallel"):
        if getattr(args, name) is not None:
            continuation[name] = getattr(args, name)
    if args.resume_rollouts or args.workers_stopped:
        continuation.update(resume_rollouts=args.resume_rollouts, workers_stopped=args.workers_stopped)
    if args.checkpoint_directory is not None:
        continuation["checkpoint_directory"] = args.checkpoint_directory
    if args.machine_profile is not None:
        continuation["machine_profile"] = args.machine_profile
    print(json.dumps(submit(args.output, args.allocation, args.configuration, args.release,
                           tasks, args.train_split, repository=repository, cpus=args.cpus, **continuation), indent=2))
    return 0
