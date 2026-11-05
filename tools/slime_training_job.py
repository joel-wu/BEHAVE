"""Compatibility entry for owned native training jobs.

Implementation lives in behave.train. Source-checkout callers retain the old
commands and signatures; the installed submit command requires --repository.
"""

from pathlib import Path

from behave.train.jobs import (
    _staged,
    backup_task_pool,
    hf_comparison_parent,
    job_configuration,
    native_feedback_owner,
    native_resume_prefix,
    node_completed,
    staged,
    wait_for_completion,
)

__all__ = [
    "_staged", "backup_task_pool", "execute", "hf_comparison_parent", "job_configuration", "main",
    "native_feedback_owner", "native_resume_prefix", "node_completed", "staged", "submit", "wait_for_completion",
]


def submit(output: Path, allocation: int, configuration: Path, release: Path,
           tasks: list[str], split: Path, *, cpus: int, resume_from: Path | None = None,
           updates: int | None = None,
           training_seed: int | None = None, resume_rollouts: bool = False,
           workers_stopped: bool = False, workers: int | None = None,
           rollout_gpus_per_engine: int | None = None,
           rollout_excluded_gpus: tuple[int, ...] | None = None,
           actor_gpus_per_node: int | None = None,
           context_parallel: int | None = None,
           checkpoint_directory: Path | None = None,
           machine_profile: Path | None = None) -> dict:
    from behave.train.jobs import submit as submit_job

    return submit_job(output, allocation, configuration, release, tasks, split,
        repository=Path(__file__).resolve().parents[1],
        cpus=cpus,
        resume_from=resume_from,
        updates=updates,
        training_seed=training_seed,
        resume_rollouts=resume_rollouts,
        workers_stopped=workers_stopped,
        workers=workers,
        rollout_gpus_per_engine=rollout_gpus_per_engine,
        rollout_excluded_gpus=rollout_excluded_gpus,
        actor_gpus_per_node=actor_gpus_per_node,
        context_parallel=context_parallel,
        checkpoint_directory=checkpoint_directory,
        machine_profile=machine_profile,
    )


def execute(output: str, local: str) -> int:
    """Compatibility entry: the installed worker receives its staged checkout."""
    from behave.train.jobs import execute as run

    return run(output, local, repository=Path(__file__).resolve().parents[1])


def main(argv=None):
    from behave.cli.training_job import main as submit_main

    return submit_main(argv, repository=Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
