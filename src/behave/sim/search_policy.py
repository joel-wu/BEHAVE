"""Public search policy and invocation-owned monotonic deadlines.

Search cancellation is control flow, not a candidate, solver or verifier fault.
The context is inherited by bounded replay threads, but ends before scoring.
"""

from __future__ import annotations

import math
import time
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from threading import Event

from behave.core import runtime_defaults as defaults
from behave.isolation.preparation_control import PreparationCancelled, preparation_scope

from .core.frozen import Frozen
from .execution_control import (
    ExecutionPhase, active_budget, budget_scope, check_execution, remaining_timeout,
)

DEFAULT_SEARCH_SECONDS = defaults.SIM_SEARCH_SECONDS
WORKLIST_POLICY = "context-frontier+goal-family+native-progress+preparation-handoff+query-chain/17"
_QUERY_SHARE = ContextVar("behave_search_query_share", default=(1, 0.0))

# Historical entry name; the implementation and active storage are shared.
check_search = check_execution


class SearchPolicy(Frozen):
    """One total wall budget; optional total Probe cap for controlled experiments.

    Directions run in declared order. Each receives remaining usable time
    divided by remaining directions, so early completion transfers unused time
    forward. An enclosing scoring deadline can shorten that shared lifetime.
    ``time_budget_s=None`` requires an explicit finite ``max_probes``.
    ``debug`` stops at a search failure instead of returning the retained pool.
    Budget exhaustion and ordinary solver UNKNOWN/UNSAT remain normal stops.
    Completed query chains yield to other seeds instead of draining one seed's
    full request list. Related questions retain construction on their worker;
    independent questions are singleton chains. Suspended seeds resume there.
    Natively confirmed progress starts a successor after its completed SAT,
    including inside a chain. Its parent's pending questions remain available.
    A completed preparation without new coverage rotates its child and parent
    behind older continuations, without waiting for the full query chain.
    Construction yields resume on that lane under the same shared deadline;
    solver UNKNOWN instead advances to the next local question.
    Parallel lanes first divide unclaimed targets; deferred targets remain
    pending, and a claim never substitutes for native execution evidence.
    Newly selected seeds prefer target families with fewer previous lane
    starts. Ties keep provider order; each admitted chain retains its order
    and construction. These shared scheduling hints never remove a question.
    A native-confirmed successor first continues its producing family's
    remaining targets; global visit balancing orders the other families.
    A resumed construction receives its equal share plus the time already
    invested in that question, capped by the original remaining stage budget.
    This gives expensive prepared queries a comparable chance to solve without
    adding a fixed per-query time limit or renewing any deadline.
    """

    __slots__ = ("debug", "max_probes", "time_budget_s")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("SearchPolicy is a closed public record")

    def __init__(self, *, time_budget_s=DEFAULT_SEARCH_SECONDS, max_probes=None,
                 debug=defaults.SIM_SEARCH_DEBUG):
        if time_budget_s is not None and (
                type(time_budget_s) not in (int, float)
                or not math.isfinite(time_budget_s) or time_budget_s < 0):
            raise ValueError("search time must be finite and nonnegative, or None")
        if max_probes is not None and (type(max_probes) is not int or max_probes < 0):
            raise ValueError("max_probes must be a nonnegative exact integer, or None")
        if time_budget_s is None and max_probes is None:
            raise ValueError("a search needs a finite time or Probe budget")
        if type(debug) is not bool:
            raise ValueError("search debug must be an exact Boolean")
        self.time_budget_s = None if time_budget_s is None else float(time_budget_s)
        self.max_probes = max_probes
        self.debug = debug
        self._freeze()

    def as_dict(self):
        return {"version": "search-policy/10", "time_budget_s": self.time_budget_s,
                "max_probes": self.max_probes, "debug": self.debug,
                "allocation": "remaining-share+construction-cost/2",
                "worklist": WORKLIST_POLICY}

    @classmethod
    def from_dict(cls, image):
        if type(image) is not dict or set(image) != {
                "version", "time_budget_s", "max_probes", "debug", "allocation", "worklist"} \
                or image["version"] != "search-policy/10" \
                or image["allocation"] != "remaining-share+construction-cost/2" \
                or image["worklist"] != WORKLIST_POLICY:
            raise ValueError("unsupported search policy record")
        return cls(time_budget_s=image["time_budget_s"], max_probes=image["max_probes"],
                   debug=image["debug"])


class SearchStopped(PreparationCancelled):
    """Only the scheduler catches this; ordinary fault handlers must not swallow it."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class SearchFailed(SearchStopped):
    """Unwind provider exception handlers without losing the original refusal."""

    def __init__(self, refusal):
        self.refusal = refusal
        super().__init__("search-failed")


class SearchBudget:
    def __init__(self, policy):
        if type(policy) is not SearchPolicy:
            raise TypeError("search requires the exact SearchPolicy")
        self.policy = policy
        self.started = time.monotonic()
        self.deadline = (None if policy.time_budget_s is None
                         else self.started + policy.time_budget_s)
        self.probes = 0

    def direction(self, remaining_directions):
        from .scoring_policy import active_scoring

        now = time.monotonic()
        deadline = self.deadline
        if deadline is not None:
            scoring = active_scoring()
            if scoring is not None and not scoring.finished:
                deadline = min(deadline, scoring.deadline)
            deadline = now + max(0, deadline - now) / remaining_directions
        return SearchLease(self, deadline)


class SearchLease:
    def __init__(self, owner, deadline):
        self.owner, self.deadline = owner, deadline
        self.started = time.monotonic()
        self.phases = {}
        self.probes_started = self.probes_completed = 0
        self.resources = ExitStack()
        self._cancelled = Event()
        self._cancel_reason = None

    def cancel(self, reason):
        self._cancel_reason = reason
        self._cancelled.set()

    def check(self):
        from .scoring_policy import check_scoring

        check_scoring()
        if self._cancelled.is_set():
            raise SearchStopped(self._cancel_reason)
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise SearchStopped("time-budget-exhausted")

    def remaining(self, seconds):
        # A count-only lease has no wall clamp. Explicit cancellation remains
        # checked at work boundaries, exactly as for the former shared helper.
        if self.deadline is None:
            return seconds
        self.check()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise SearchStopped("time-budget-exhausted")
        return min(seconds, remaining)

    def start_probe(self):
        self.check_work()
        self.owner.probes += 1
        self.probes_started += 1

    def check_work(self):
        self.check()
        limit = self.owner.policy.max_probes
        if limit is not None and self.owner.probes >= limit:
            raise SearchStopped("probe-budget-exhausted")

    def report(self, reason):
        return {"policy": self.owner.policy.as_dict(), "stop_reason": reason,
                "elapsed_s": time.monotonic() - self.started,
                "total_elapsed_s": time.monotonic() - self.owner.started,
                "allocated_s": (None if self.deadline is None else
                                max(0, self.deadline - self.started)),
                "probes_started": self.probes_started,
                "probes_completed": self.probes_completed,
                "probes_cancelled": self.probes_started - self.probes_completed,
                "phases_s": dict(self.phases)}


def active_search():
    return active_budget(ExecutionPhase.SEARCH)


@contextmanager
def search_scope(lease):
    # Legacy per-direction probe counts have no time lease. Preserve any
    # caller-owned preparation cancellation instead of installing a new scope.
    with budget_scope(ExecutionPhase.SEARCH, lease):
        with (nullcontext() if lease is None else preparation_scope(lease.check)):
            yield


def check_search_work():
    lease = active_search()
    if lease is not None:
        lease.check_work()


def search_cleanup(function, *args, **kwargs):
    """Retain owned temporary resources until every search worker has joined."""
    lease = active_search()
    if lease is not None:
        lease.resources.callback(function, *args, **kwargs)


def remaining_solver_ms(milliseconds):
    if milliseconds is not None and (type(milliseconds) is not int or milliseconds < 1):
        raise ValueError("a query limit must be a positive integer or None")
    remaining = remaining_timeout(float("inf"))
    pending, construction_seconds = _QUERY_SHARE.get()
    available = min(remaining, remaining / pending + construction_seconds)
    if milliseconds is not None:
        available = min(available, milliseconds / 1000)
    if not math.isfinite(available):
        raise ValueError("a query needs a finite owning stage or an explicit timeout_ms")
    # Zero means unlimited to several solver APIs. Never round down to zero.
    return max(1, int(available * 1000))


@contextmanager
def search_query_scope(pending_queries, *, construction_seconds=0.0):
    """Share this lane's remaining time among independent query work items.

    Related prefixes count as one construction chain, not a collection of
    hypothetical independent solves competing against their own continuation.

    Enter in the executing worker, not while enqueueing it. Waiting time and
    earlier preparation therefore cannot renew the stage deadline.
    Retained construction is measured by the scheduler, not a provider's
    claimed solve time. It is local to this exact question and resets on a
    terminal answer or a changed question. Unknown solver answers are not
    retried. All backend subqueries still share the assigned slice.
    """
    if type(pending_queries) is not int or pending_queries < 1:
        raise ValueError("pending query count must be a positive integer")
    if type(construction_seconds) not in (int, float) \
            or not math.isfinite(construction_seconds) or construction_seconds < 0:
        raise ValueError("retained construction time must be finite and nonnegative")
    token = _QUERY_SHARE.set((pending_queries, construction_seconds))
    try:
        yield
    finally:
        _QUERY_SHARE.reset(token)


@contextmanager
def search_phase(name, *, cleanup=False):
    lease = active_search()
    if not cleanup:
        check_execution()
    started = time.monotonic()
    try:
        yield
    finally:
        if lease is not None:
            lease.phases[name] = lease.phases.get(name, 0) + time.monotonic() - started
    if not cleanup:
        check_execution()
