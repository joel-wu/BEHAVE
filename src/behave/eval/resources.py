"""Deployment resource validation shared by Eval generation and scoring."""

import re


def node_capacity(resources):
    """Read deployment capacity; omitted fields retain historical node requests.

    Capacity is not CPU affinity or the number of GPUs used by one model.
    In particular a fleet worker still requests the whole declared allocation,
    preserving device numbering and the existing overlapping-step ownership.
    """
    cpus = resources.get("allocated_cpus", 188)
    gpus = resources.get("allocated_gpus", 8)
    if any(type(value) is not int or value < 1 for value in (cpus, gpus)):
        raise ValueError("allocated_cpus and allocated_gpus must be positive integers")
    return cpus, gpus


def execution_cpu_ids(resources, cpus):
    """Validate affinity without confusing a physical CPU ID with a count."""
    capacity, _ = node_capacity(resources)
    if (not isinstance(cpus, list) or not cpus
            or any(type(cpu) is not int or cpu < 0 for cpu in cpus)
            or len(set(cpus)) != len(cpus)):
        raise ValueError("execution CPU IDs must be a nonempty list of distinct nonnegative integers")
    if len(cpus) > capacity:
        raise ValueError("execution CPU affinity exceeds allocated_cpus")
    allocated = resources.get("allocated_cpu_ids")
    if allocated is not None:
        if (not isinstance(allocated, list) or len(allocated) != capacity
                or any(type(cpu) is not int or cpu < 0 for cpu in allocated)
                or len(set(allocated)) != len(allocated)):
            raise ValueError("allocated_cpu_ids must enumerate allocated_cpus distinct CPU IDs")
        if not set(cpus).issubset(allocated):
            raise ValueError("execution CPU affinity is outside allocated_cpu_ids")
    return list(cpus)


def validate_generation_resources(resources):
    """Check the saved serving topology against deployment capacity, not defaults."""
    _, capacity = node_capacity(resources)
    execution_cpu_ids(resources, resources["generation_cpu_ids"])
    groups = resources["device_groups"]
    tp, engines = resources["tensor_parallel_per_engine"], resources["engines"]
    if (type(tp) is not int or tp < 1 or type(engines) is not int or engines < 1
            or not isinstance(groups, list) or len(groups) != engines):
        raise ValueError("device_groups must match the declared serving topology")
    devices = []
    for group in groups:
        if not isinstance(group, str) or not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", group):
            raise ValueError("device_groups must contain comma-separated GPU IDs")
        members = [int(value) for value in group.split(",")]
        if len(members) != tp:
            raise ValueError("each device group must match tensor_parallel_per_engine")
        devices.extend(members)
    if len(set(devices)) != len(devices) or any(device >= capacity for device in devices):
        raise ValueError("device_groups overlap or exceed allocated_gpus")


def scoring_cpu_plan(cpus, *, cpu_ids=None, allocated_cpus=None, allocated_cpu_ids=None):
    """Return (Slurm step size, worker affinity); never infer size from CPU IDs.

    Unbound scoring requests its worker budget. Explicit affinity requests the
    declared allocation, then binds to the smaller budget. A supplied physical
    CPU inventory rejects gaps before submission. Existing saved launches are
    observed by their caller without reinterpretation.
    """
    if type(cpus) is not int or cpus < 1:
        raise ValueError("scoring CPUs must be a positive integer")
    if allocated_cpus is None:
        if cpu_ids is not None or allocated_cpu_ids is not None:
            raise ValueError("explicit scoring CPU IDs require allocated_cpus")
        return cpus, None
    resources = {"allocated_cpus": allocated_cpus}
    if allocated_cpu_ids is not None:
        resources["allocated_cpu_ids"] = allocated_cpu_ids
    capacity, _ = node_capacity(resources)
    if cpus > capacity:
        raise ValueError("scoring CPUs exceed allocated_cpus")
    if cpu_ids is None:
        if allocated_cpu_ids is not None:
            execution_cpu_ids(resources, allocated_cpu_ids)
        return cpus, None
    try:
        affinity = list(cpu_ids)
    except TypeError as error:
        raise ValueError("scoring CPU IDs must be an iterable matching cpus") from error
    if len(affinity) != cpus:
        raise ValueError("scoring CPU IDs must match cpus")
    return capacity, execution_cpu_ids(resources, affinity)
