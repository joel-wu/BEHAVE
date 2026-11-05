"""Process/thread limits for dedicated, trusted node workers."""

import resource


def process_limit(limit: int | None) -> int:
    """Resolve an unspecified soft limit to the existing OS hard limit."""
    if limit is None:
        return resource.getrlimit(resource.RLIMIT_NPROC)[1]
    if type(limit) is not int or limit < 1:
        raise ValueError("process_limit must be positive or null")
    return limit


def configure_worker_process_limit(limit: int | None = None) -> dict[str, int]:
    """Configure the node worker before starting threads or user namespaces.

    This changes the calling worker, not its submitting parent or the system
    hard limit. Candidate sandbox resource policies remain separate. Raising
    the limit inside an already-created user namespace would be too late.
    """
    previous, hard = resource.getrlimit(resource.RLIMIT_NPROC)
    selected = process_limit(limit)
    if selected != previous:
        resource.setrlimit(resource.RLIMIT_NPROC, (selected, hard))
    return {"previous_soft": previous, "soft": selected, "hard": hard}
