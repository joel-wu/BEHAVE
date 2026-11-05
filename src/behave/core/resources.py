"""Resolve the CPUs assigned to this worker, not the machine's total size."""

import os


def allocated_cpus(cpus: int | None = None) -> tuple[int, ...]:
    available = tuple(sorted(os.sched_getaffinity(0)))
    allocated = min(len(available), int(os.environ.get("SLURM_CPUS_PER_TASK", len(available))))
    if allocated < 1:
        raise ValueError("worker has no allocated CPUs")
    if cpus is not None and (type(cpus) is not int or not 1 <= cpus <= allocated):
        raise ValueError("requested CPUs exceed the worker allocation")
    return available[:allocated if cpus is None else cpus]
