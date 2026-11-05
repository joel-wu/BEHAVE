"""Reported Behavior frontend limits and exact-analysis resource budgets.

Capability limits bound representation work; representability limits bound range
discovery. Neither permits approximating an admitted execution. Declared budgets
bound the analysis service, not the trace domain: exhaustion leaves an unresolved
query or a refused construction, never a proof of unreachability.

Historical sizing observations are retained in docs/sim_budget_design_history.md.
"""

from __future__ import annotations

from behave.core import runtime_defaults as defaults

__all__ = ["CAPABILITY", "DECLARED", "REPRESENTABILITY", "Spent", "as_dict",
           "available_mb", "helper_activation_exceeds",
           "reservation_for_peak", "scalar_magnitude_bits",
           "solver_memory_mb", "source_audit_limits", "workers_for"]

from ..core.resources import (
    ARTIFACT_MEMORY_MB as ARTIFACT_MEMORY_MB,
    SOLVER_MEMORY_MB as SOLVER_MEMORY_MB,
    available_cpus, worker_limit, solver_memory_mb as solver_memory_mb,
)
from ..symbolic.capabilities import (
    scalar_magnitude_bits as scalar_magnitude_bits,
    STATE_ROUNDS as STATE_ROUNDS, STATE_MAGNITUDE as STATE_MAGNITUDE,
    TERM_BUDGET as TERM_BUDGET, REPRESENTATION_DEPTH as REPRESENTATION_DEPTH,
    SCALAR_BITS as SCALAR_BITS, SHIFT_LIMIT as SHIFT_LIMIT,
)

# ── capability ───────────────────────────────────────────────────────

#: Iterations a data-dependent loop may be unrolled to.
UNROLL_LIMIT = defaults.SIM_BEHAVIOR_UNROLL_LIMIT
#: Simultaneously nested source-helper activations below the public step.
#: The step itself is the boundary operation, not a helper activation.  Source
#: planning, IR inlining and the alias proof all consume this same quantity so
#: one stage cannot accept a call chain another stage interprets differently.
HELPER_ACTIVATION_LIMIT = defaults.SIM_BEHAVIOR_HELPER_ACTIVATIONS
#: Retained semantic-IR construction work.  A source loop remains one ``For``;
#: its task-domain trip count does not multiply this quantity.
SEMANTIC_IR_NODES = defaults.SIM_BEHAVIOR_SEMANTIC_IR_NODES
#: Abstract source-value work allowed while planning one procedure.  This is
#: one aggregate allowance across declaration evaluation, representation
#: contracts and lowering; a fresh allowance per query or module binding does
#: not compose.  It precedes IR construction, so the IR term budget cannot
#: contain it.  In particular, nested finite comprehensions can otherwise
#: construct a Cartesian product of abstract environments before a single term
#: exists.
SOURCE_PLAN_NODES = defaults.SIM_BEHAVIOR_SOURCE_PLAN_NODES
#: Largest source text accepted before parsing, and largest string the
#: declaration evaluator may materialise.  Both are host strings allocated
#: before SolverIR exists; bounding only the generated one leaves the parser as
#: an unreported pre-analysis allocation.
SOURCE_TEXT_CHARS = defaults.SIM_BEHAVIOR_SOURCE_TEXT_CHARS
#: Compatibility spelling for callers predating the shared scalar contract.
#: It is an alias, not a second independently configurable budget, and is
#: consequently not repeated in ``CAPABILITY`` or reports.
SOURCE_INTEGER_BITS = SCALAR_BITS
#: Largest tree obtained by expanding one finite module-value DAG.  Aggregate
#: source analysis is governed by ``SOURCE_PLAN_NODES``; this second dimension
#: is needed because a compact chain such as ``v = (v, v)`` can have a small
#: source graph and an exponentially large value-tree representation.
ROM_EXPANSION_NODES = defaults.SIM_BEHAVIOR_ROM_EXPANSION_NODES
CAPABILITY = ("UNROLL_LIMIT", "HELPER_ACTIVATION_LIMIT",
              "SEMANTIC_IR_NODES", "TERM_BUDGET",
              "REPRESENTATION_DEPTH",
              "SOURCE_PLAN_NODES",
              "SOURCE_TEXT_CHARS", "SCALAR_BITS", "ROM_EXPANSION_NODES")

#: Human-readable quantities for capability records whose names predate their
#: complete enforcement.  ``TERM_BUDGET`` began as a command-node ceiling; it
#: now governs the aggregate representation work described above, and the run
#: record must say that rather than preserve the narrower historical reading.
MEASURE = {
    "HELPER_ACTIVATION_LIMIT": (
        "simultaneously nested helper activations below the public step"),
    "SEMANTIC_IR_NODES": "retained semantic IR construction nodes",
    "TERM_BUDGET": (
        "finite exact-encoding and recursive representation work"),
    "REPRESENTATION_DEPTH": (
        "nesting of an IR command, expression, type, or initial value"),
    "SOURCE_PLAN_NODES": "aggregate source-value work per lowering pass",
    "SOURCE_TEXT_CHARS": "source or generated declaration text characters",
    "SCALAR_BITS": "magnitude bits in one retained scalar value or port",
    "ROM_EXPANSION_NODES": "nodes in one expanded finite module-value DAG",
}


def helper_activation_exceeds(candidate):
    """Whether a candidate helper activation lies outside the capability."""
    if type(candidate) is not int:
        raise TypeError("a helper activation count must be an exact integer")
    if candidate < 0:
        raise ValueError("a helper activation count must be nonnegative")
    return candidate > HELPER_ACTIVATION_LIMIT

# ── representability ─────────────────────────────────────────────────


REPRESENTABILITY = ("STATE_ROUNDS", "STATE_MAGNITUDE")

# ── declared ─────────────────────────────────────────────────────────

#: Milliseconds per solver query, further limited by the remaining artifact budget.
QUERY_MS = defaults.SIM_BEHAVIOR_ANALYSIS_QUERY_MS

# ── declared, per artifact ───────────────────────────────────────────

#: Milliseconds the whole closure of one artifact may take.
ARTIFACT_MS = defaults.SIM_BEHAVIOR_ANALYSIS_MS
#: Solver invocations one artifact's closure may make. A cap on questions asked,
#: which bounds the work even where each question is quick.
ARTIFACT_QUERIES = defaults.SIM_BEHAVIOR_ANALYSIS_QUERIES
# ARTIFACT_MEMORY_MB, SOLVER_MEMORY_MB and their scaling policy are imported
# from core.resources and retained in this frontend's declared budget report.
#: Minimum memory reserved when scheduling one concurrent formula rebuild.  This is
#: not a second hard limit.  The prefix query measures the actual formula worker and
#: raises this reservation when needed; 1 GiB is the floor for an unmeasured worker.
PARALLEL_WORKER_RESERVATION_MB = defaults.SIM_ANALYSIS_WORKER_RESERVATION_MB
#: Optional JSON request/response quota in MiB; None leaves evidence size uncapped.
#: Process memory and time budgets still apply.
ARTIFACT_WIRE_MB = defaults.SIM_BEHAVIOR_ANALYSIS_WIRE_MB

DECLARED = ("QUERY_MS", "ARTIFACT_MS", "ARTIFACT_QUERIES",
            "ARTIFACT_MEMORY_MB", "SOLVER_MEMORY_MB",
            "PARALLEL_WORKER_RESERVATION_MB",
            "ARTIFACT_WIRE_MB")


def source_audit_limits():
    """Give required source replay the artifact's declared resource envelope.

    Public Analysis uses its remaining named lifetime, not the standalone
    artifact default. Full-trace audits do not renew that deadline or inherit
    short generation-probe limits.
    """
    from ..analysis_budget import remaining_query_ms
    from .isolated import Limits

    remaining_ms = remaining_query_ms(None)
    wall_ms = ARTIFACT_MS if remaining_ms is None else remaining_ms
    seconds = max(1, (wall_ms + 999) // 1000)
    return Limits(
        seconds=seconds,
        wall_seconds=seconds,
        memory_bytes=ARTIFACT_MEMORY_MB << 20,
        processes=1,
    )


class Spent:
    """What one artifact's closure has used, and what it has left.

    Queries share the artifact's remaining time and invocation count. An explicit
    None disables the corresponding limit. Solver execution separately enforces
    the returned allowance with a native timeout and a private-context watchdog.
    """

    __slots__ = ("_now", "max_queries", "ms", "queries", "started")

    def __init__(self, ms=ARTIFACT_MS, max_queries=ARTIFACT_QUERIES, now=None,
                 started=None):
        import time

        self._now = now or time.monotonic
        self.started = self._now() if started is None else started
        if type(self.started) not in (int, float):
            raise TypeError("an artifact clock starts at a monotonic number")
        self.ms = ms
        self.max_queries = max_queries
        self.queries = 0

    def remaining_ms(self):
        if self.ms is None:
            return None
        used = (self._now() - self.started) * 1000.0
        return max(0, int(self.ms - used))

    def exhausted(self):
        """The exhausted budget's diagnostic, or None."""
        if self.max_queries is not None and self.queries >= self.max_queries:
            return (f"the artifact's query budget of {self.max_queries} solver "
                    f"invocations is spent")
        if self.ms is not None and self.remaining_ms() <= 0:
            return (f"the artifact's wall clock of {self.ms} ms is spent after "
                    f"{self.queries} queries")
        return None

    def remaining_queries(self):
        """Physical solver invocations still admitted by this artifact."""

        if self.max_queries is None:
            return None
        return max(0, self.max_queries - self.queries)

    def for_query(self, per_query_ms, *, participants=1):
        """Return this call's fair share of the remaining artifact time.

        ``participants`` counts the calls that still need an opportunity,
        including this one.  A large per-query ceiling must not let an early
        query consume the entire artifact and prevent later singleton queries
        from being posed at all.  Calls that finish early leave their unused
        share in the artifact, so the remaining calls divide it again.
        """
        if type(per_query_ms) is not int or per_query_ms < 1:
            raise ValueError("a per-query ceiling is a positive integer")
        if type(participants) is not int or participants < 1:
            raise ValueError("query participants is a positive integer")
        left = self.remaining_ms()
        if left is None:
            return per_query_ms
        fair = max(1, left // participants)
        return min(per_query_ms, fair)

    def charge(self, count=1):
        if type(count) is not int or count < 1:
            raise ValueError("a query charge is a positive integer")
        self.queries += count


def available_mb():
    """Memory estimate from Slurm, then cgroup v2, then host pages, or None."""
    import os

    for var in ("SLURM_MEM_PER_NODE", "SLURM_MEM_PER_CPU"):
        value = os.environ.get(var)
        if value and value.isdigit():
            mb = int(value)
            if var == "SLURM_MEM_PER_CPU":
                mb *= int(os.environ.get("SLURM_CPUS_ON_NODE") or 1)
            return mb
    try:
        with open("/sys/fs/cgroup/memory.max") as stream:
            text = stream.read().strip()
        if text.isdigit():
            return int(text) // (1024 * 1024)
    except OSError:
        pass
    try:
        return (os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
                // (1024 * 1024))
    except (ValueError, OSError):
        return None


def workers_for(tasks, reservation_mb=PARALLEL_WORKER_RESERVATION_MB,
                total_mb=None):
    """Worker count bounded by tasks, CPUs and reserved formula memory.

    The caller can raise the reservation from its prefix worker's measured peak.
    One third of the memory estimate is reserved for the parent and results;
    the enclosing artifact ceiling remains the hard resource boundary.
    """
    if not tasks:
        return 0
    by_cores = min(len(tasks), max(1, available_cpus() - 2))
    by_cores = worker_limit("BEHAVE_ANALYSIS_WORKERS", by_cores)
    have = total_mb if total_mb is not None else available_mb()
    if reservation_mb is None or have is None:
        return by_cores
    if type(reservation_mb) is not int or reservation_mb < 1:
        raise ValueError("a worker memory reservation is a positive integer")
    if type(have) is not int or have < 1:
        raise ValueError("available worker memory is a positive integer")
    # Leave one third for the parent and retained results.
    return max(1, min(by_cores, int(have * 2 // 3 // reservation_mb)))


def reservation_for_peak(peak_bytes,
                         floor_mb=PARALLEL_WORKER_RESERVATION_MB):
    """Scheduling reservation derived from one measured formula worker.

    The prefix worker builds the same unrolling later singleton workers rebuild.
    Its high-water RSS therefore gives the scheduler a per-artifact fact instead of
    asking a population average to predict every model.  Keep 25% above that observed
    peak for solver-path variation; the aggregate isolation ceiling remains the hard
    limit if a later call grows beyond the measurement.
    """
    if type(floor_mb) is not int or floor_mb < 1:
        raise ValueError("a worker memory reservation floor is a positive integer")
    if peak_bytes is None:
        return floor_mb
    if type(peak_bytes) is not int or peak_bytes < 0:
        raise ValueError("a measured worker peak is a nonnegative byte count")
    mib = 1 << 20
    measured_mb = (peak_bytes + mib - 1) // mib
    measured_with_margin = (measured_mb * 5 + 3) // 4
    return max(floor_mb, measured_with_margin)


def as_dict():
    """Report every limit with its kind and, where specified, measured quantity."""
    kinds = {name: "capability" for name in CAPABILITY}
    kinds.update({name: "representability" for name in REPRESENTABILITY})
    kinds.update({name: "declared" for name in DECLARED})
    out = {name: {"value": globals()[name], "kind": kind}
           for name, kind in kinds.items()}
    for name, measure in MEASURE.items():
        out[name]["measure"] = measure
    return out
