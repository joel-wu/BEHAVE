"""Query-boundary worklists; isolated Select/Probe calls run concurrently.

Generators execute on the coordinator, so coverage, proof reuse and candidate
acceptance have one writer. Each lane owns a provider and a deadline-bound
session; threads wait for independent isolated processes, never share a solver.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections import Counter, deque
from contextlib import ExitStack
from contextvars import copy_context
from dataclasses import dataclass, field
from itertools import zip_longest

from behave.core import runtime_defaults as defaults
from .core.resources import available_cpus
from .cpu_admission import cpu_work_width
from .generation_protocol import GenerationExtensions, QueryPlan
from .execution_control import check_execution
from .search_policy import WORKLIST_POLICY, SearchLease, active_search, search_phase, search_scope


def search_workers(provider, *, legacy=False, _extensions=None):
    if legacy:
        return 1
    extensions = _extensions or GenerationExtensions.bind(provider)
    if not extensions.parallel_search or extensions.fork_search_worker is None:
        return 1
    requested = defaults.SIM_SEARCH_WORKERS
    if requested is None:
        requested = available_cpus()
    if type(requested) is not int or requested < 1:
        raise ValueError("SIM_SEARCH_WORKERS must be a positive integer or None")
    return cpu_work_width(requested)


def partition_targets(request, in_flight, workers):
    """Share unclaimed goals among available lanes, retaining every remainder.

    Claims are only scheduling hints, never coverage or proof. If every target
    is already being tried, another seed may still try the same targets rather
    than idling. A single lane keeps the original disjunctive query unchanged.
    """
    if workers == 1:
        return request, None
    claimed = {goal for targets in in_flight.values() for goal in targets}
    available = tuple(goal for goal in request.targets if goal not in claimed)
    if not available:
        return request, None
    lanes = max(1, workers - len(in_flight))
    width = (len(available) + lanes - 1) // lanes
    chosen = available[:width]
    chosen_set = set(chosen)
    remaining = tuple(goal for goal in request.targets if goal not in chosen_set)
    return (request.with_targets(chosen),
            request.with_targets(remaining) if remaining else None)


class QueryWork:
    """One selection's pending questions, grouped by construction affinity.

    The source supplies chain identities, never proofs. Unchained requests stay
    independent. Keep original request indices for accounting and native target
    narrowing; requeues and target splits cannot consume or duplicate a chain.
    """

    def __init__(self, requests, chains=(), *, priority=None, continuation_targets=(), context_visits=None):
        self.plan = requests if type(requests) is QueryPlan else None
        if self.plan is not None:
            by_identity = {item.rank: index for index, item in enumerate(requests.templates)}
            chains = requests.chains
        else:
            by_identity = {request.identity: index for index, request in enumerate(requests)}
        self.family = {index: index for index in range(len(requests))}
        members = {}
        for chain in chains:
            indices = tuple(by_identity[key] for key in chain if key in by_identity)
            if indices:
                owner = indices[0]
                for index in indices:
                    self.family[index] = owner
                members[owner] = indices
        owners, visited = [], set()
        for index in range(len(requests)):
            owner = self.family[index]
            if owner not in visited:
                visited.add(owner)
                owners.append(owner)
        if priority is not None:
            # Rank independent families once when the seed enters this lane.
            # Identical target sets share the score; do not expand compact
            # requests or rescan all windows after every construction slice.
            rows = self.plan.templates if self.plan is not None else requests
            scores = {}
            for owner in owners:
                targets = rows[owner].targets
                if targets not in scores:
                    scores[targets] = priority(targets)
            owners.sort(key=lambda owner: scores[rows[owner].targets])
        if self.plan is not None and self.plan.explorations:
            exploratory = set(self.plan.explorations)
            indices = [member for owner in owners for member in members.get(owner, (owner,))]
            # Static-goal visit counts must not reorder dynamic-context
            # priorities supplied by the native source selection.
            contexts = sorted(index for index in indices if self.plan.templates[index].rank in exploratory)
            regular = [index for index in indices if self.plan.templates[index].rank not in exploratory]
            continuing = frozenset(continuation_targets)
            related = {index for index in contexts
                       if continuing.intersection(self.plan.templates[index].targets)}
            if related:
                # A replayed child should use the state just prepared for its
                # producing family before generating another setup. Baseline
                # seeds still start with ordinary work. Keep native priority
                # within each group and retain every deferred question. Setup
                # recipes themselves are exploratory too; they must not be
                # mistaken for queries that use the state just prepared.
                preparations = set(self.plan.preparations)
                contexts.sort(key=lambda index: (index not in related,
                    self.plan.templates[index].rank in preparations))
            if context_visits is not None:
                # Fresh native entry contexts precede already queried ones.
                # A child must not restart every earlier state just because its
                # stimulus digest changed. All old questions remain pending.
                contexts.sort(key=lambda index: context_visits.get(
                    self.plan.contexts.get(self.plan.templates[index].rank), 0))
            # Alternate completed questions, not entire widening chains: an
            # ordinary chain may contain every input window of a long seed.
            # Its relative order remains intact. Requeued unfinished queries
            # stay at the front and are never interrupted by this ordering.
            queues = (contexts, regular) if related else (regular, contexts)
            indices = [index for pair in zip_longest(*queues)
                       for index in pair if index is not None]
        else:
            indices = [member for owner in owners for member in members.get(owner, (owner,))]
        ordered = [(index, None if self.plan is not None else requests[index]) for index in indices]
        self.pending = deque(ordered)
        self.counts = Counter(self.family[index] for index, _request in ordered)

    def __len__(self):
        return len(self.pending)

    def popleft(self):
        item = self.pending.popleft()
        owner = self.family[item[0]]
        self.counts[owner] -= 1
        if not self.counts[owner]:
            del self.counts[owner]
        return (item[0], self.plan[item[0]]) if item[1] is None else item

    def appendleft(self, item):
        self.pending.appendleft(item)
        self.counts[self.family[item[0]]] += 1

    def continues(self, index):
        return bool(self.pending and self.family[index] == self.family[self.pending[0][0]])

    def shares(self, index):
        return len(self.counts) + int(self.family[index] not in self.counts)


@dataclass
class _Lane:
    index: int
    provider: object
    lease: object
    task: object = None
    suspended: deque = field(default_factory=deque)
    prefer_new: bool = True


@dataclass(frozen=True)
class ExploreSuccessor:
    """Explore a replayed successor at its completed producing query."""

    seed: object
    progress: bool = True


@dataclass(frozen=True)
class QueryComplete:
    """Yield a seed after its current question or construction chain finishes.

    CONSTRUCTING is not this boundary: the worker must retain and resume that
    question. Pending questions/proofs stay in the coordinator generator;
    there is no suspended solver to move or reconstruct at this boundary.
    """


def _call(operation):
    phase, function = operation
    with search_phase(phase):
        return function()


def run_seed_worklist(queue, provider, explore, can_continue, *, workers, telemetry,
                      _extensions=None):
    """Share completed-query boundaries without dropping pending seed work.

    New seeds and retained continuations alternate at completed-chain boundaries.
    A supply of new inputs therefore cannot indefinitely displace retained work.
    Preparation without new coverage rotates its parent and child fairly at
    this completed query, rather than waiting for the whole construction chain
    or recursively preempting every parent. Older continuations stay ahead of
    the new child. A preparation is not a coverage or state-novelty claim.
    Construction yields do not rotate: they own the provider's unfinished
    formula. A completed SAT with native progress may start its successor
    inside a chain. Suspended generators keep request position and local proofs,
    not extra workers or solver contexts. Restoration uses the provider's
    existing authenticated Select/Probe path.
    """
    worklist = telemetry["worklist"] = {
        "policy": WORKLIST_POLICY, "successors_selected": 0,
        "seeds_resumed": 0, "query_handoffs": 0, "peak_suspended_per_lane": 0,
        "preparations_deferred": 0,
    }

    def suspend(lane, task, *, started=True):
        lane.suspended.append((task, started))
        worklist["peak_suspended_per_lane"] = max(
            worklist["peak_suspended_per_lane"], len(lane.suspended))

    def resume(lane):
        if not lane.suspended:
            return None
        task, started = lane.suspended.popleft()
        worklist["seeds_resumed"] += int(started)
        return task

    def choose_next(lane):
        if queue and (lane.prefer_new or not lane.suspended):
            lane.prefer_new = False
            return explore(queue.popleft(), lane.provider)
        lane.prefer_new = True
        return resume(lane)

    def successor(seed, lane):
        worklist["successors_selected"] += 1
        yield from explore(seed, lane.provider)

    def next_operation(lane, answer=None, error=None):
        while lane.task is not None:
            try:
                operation = lane.task.throw(error) if error is not None else lane.task.send(answer)
            except StopIteration:
                lane.task = choose_next(lane) if can_continue() else None
                answer, error = None, None
                continue
            if isinstance(operation, QueryComplete):
                if not can_continue():
                    return None
                if queue or lane.suspended:
                    suspend(lane, lane.task)
                    lane.task = choose_next(lane)
                    worklist["query_handoffs"] += 1
                answer, error = None, None
                continue
            if not isinstance(operation, ExploreSuccessor):
                return operation
            # Admission and this checkpoint both run on the coordinator. No
            # other lane can take the just-enqueued seed between them.
            queue.remove(operation.seed)
            child = successor(operation.seed, lane)
            if operation.progress:
                suspend(lane, lane.task)
                lane.task = child
            else:
                # The producing query has completed, so its prepared input can
                # compete now rather than wait behind every larger question in
                # the chain. FIFO continuation order prevents recursively made
                # preparations from starving their parents. Unfinished formulae
                # never reach this boundary and stay on their original lane.
                suspend(lane, child, started=False)
                suspend(lane, lane.task)
                lane.task = choose_next(lane)
                worklist["preparations_deferred"] += 1
                worklist["query_handoffs"] += 1
            answer, error = None, None
        return None

    def close(lane):
        if lane.task is not None:
            lane.task.close()
        for task, _started in lane.suspended:
            task.close()

    if workers == 1:
        lane = _Lane(0, provider, active_search())
        try:
            while queue and can_continue():
                lane.task = explore(queue.popleft(), provider)
                operation = next_operation(lane)
                while operation is not None:
                    try:
                        answer = _call(operation)
                    except BaseException as exc:
                        operation = next_operation(lane, error=exc)
                    else:
                        # Consume a completed answer before testing a cap for
                        # new work, including the very last allowed Probe.
                        operation = next_operation(lane, answer)
        finally:
            worklist["suspended_at_stop"] = len(lane.suspended)
            close(lane)
        return
    extensions = _extensions or GenerationExtensions.bind(provider)
    owner = active_search()
    lanes, pending = [], {}
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="behave-search")
    scopes = ExitStack()
    telemetry["parallel"] = {
        "workers": workers, "peak_in_flight": 0,
        "phase_accounting": "summed-worker-seconds",
    }

    def submit(lane, operation):
        context = copy_context()
        def execute():
            with search_scope(lane.lease):
                return _call(operation)
        future = pool.submit(context.run, execute)
        pending[future] = lane
        telemetry["parallel"]["peak_in_flight"] = max(
            telemetry["parallel"]["peak_in_flight"], len(pending))

    def advance(lane, answer=None, error=None):
        operation = next_operation(lane, answer, error)
        if operation is not None:
            submit(lane, operation)

    try:
        for index in range(workers):
            check_execution()
            local = extensions.fork_search_worker(index)
            lease = SearchLease(owner.owner, owner.deadline)
            scopes.enter_context(local.search_scope(probe_budget=None))
            lanes.append(_Lane(index, local, lease))
        while can_continue():
            check_execution()
            for lane in lanes:
                if lane.task is None and queue:
                    lane.task = explore(queue.popleft(), lane.provider)
                    advance(lane)
            if not pending:
                if not queue:
                    break
                continue
            ready, _ = wait(pending, timeout=defaults.ISOLATION_COMPLETION_POLL_SECONDS,
                            return_when=FIRST_COMPLETED)
            for future in sorted(ready, key=lambda item: pending[item].index):
                lane = pending.pop(future)
                try:
                    answer = future.result()
                except BaseException as error:
                    advance(lane, error=error)
                else:
                    advance(lane, answer)
    finally:
        # Cancel siblings before joining: one failed/finished seed must not wait
        # for another session's full preparation timeout. Permits are released
        # by each exchange only after its own process cleanup.
        for lane in lanes:
            lane.lease.cancel("search-work-cancelled")
        for future in pending:
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
        try:
            worklist["suspended_at_stop"] = sum(len(lane.suspended) for lane in lanes)
            for lane in lanes:
                close(lane)
            scopes.close()
        finally:
            for lane in lanes:
                lane.lease.resources.close()
                for phase, elapsed in lane.lease.phases.items():
                    owner.phases[phase] = owner.phases.get(phase, 0) + elapsed
