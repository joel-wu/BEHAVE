"""Local queries over completed concrete executions.

Runtime queries freeze one *observed* schedule; fixed-schedule callers retain
their declared finite domain. Answers change queue values only. Runtime inputs
are executed again with their original policy, not the frozen query schedule.
Local UNSAT says nothing about other schedules or the bounded-analysis domain.
The command's execution owner supplies ordinary traces without running them twice.
"""

from contextlib import ExitStack, contextmanager
from concurrent.futures import Future
from dataclasses import dataclass
import json
import math
from threading import Lock

from .core.goal_provider import LOCAL_GUIDANCE, GoalCapabilityUse
from .core.refusal import Refusal, Refused
from .core.stimulus import (
    AuditedTrace,
    Completion,
    Stimulus,
    canonical_json,
    leading_reset_slots,
)
from .generation_protocol import (
    GenerationExtensions, GenerationProvider, Outcome, ProbeResult, ProbeSelection, ReplayFeedback, Scope,
)
from .generation import (UnavailableGenerationProvider, _candidate_refusal)
from .generation_cache import GenerationCacheTelemetry
from .generation_feedback import (selection_targets)
from .stimulus_set import GenerationSpec


class RecordedExecutions:
    """Bind input requests to actual traces, and serve their fixed query views."""

    def __init__(self, replay, *, feedback=None):
        self._replay = replay
        self._feedback = feedback
        self._requests = {}
        self._views = {}

    def prepare(self, seeds):
        missing = {seed.identity_bytes: seed for seed in seeds
                   if seed.identity_bytes not in self._requests}
        if not missing:
            return
        if self._replay is None:
            raise ValueError("search workers consume coordinator-replayed seeds only")
        traces = self._replay(tuple(missing.values()))
        if len(traces) != len(missing):
            raise ValueError("runtime replay must answer each requested seed")
        for seed, trace in zip(missing.values(), traces):
            if isinstance(trace, Refusal):
                got = trace
            elif (type(trace) is not AuditedTrace or
                  (trace.request if seed.replay_policy is not None else trace.stimulus) != seed):
                got = Refusal("generation-runtime-trace-mismatch",
                              "runtime guidance requires the actual trace of this request")
            elif trace.completion is not Completion.NORMAL:
                got = Refusal("generation-runtime-trace-incomplete",
                              "local guidance requires a normally completed runtime seed",
                              completion=trace.completion.value)
            else:
                got = trace
                self._views[trace.stimulus.identity_bytes] = trace
            self._requests[seed.identity_bytes] = got

    def trace(self, seed):
        self.prepare((seed,))
        return self._requests[seed.identity_bytes]

    def reader(self):
        """Share immutable completed rows; only the coordinator may append rows."""
        reader = RecordedExecutions(None, feedback=self._feedback)
        reader._requests, reader._views = self._requests, self._views
        return reader

    def traces(self, fixed_seeds):
        # Deliberately no fixed-schedule execution fallback. These views must
        # already belong to a real concrete execution, even after a Probe.
        return tuple(self._views[seed.identity_bytes] for seed in fixed_seeds)

    def feedback(self, fixed_seeds, catalogue):
        """Read existing assessment hits, rebinding only the recorded schedule ID."""
        if self._feedback is None:
            return None
        traces = self.traces(fixed_seeds)
        originals = tuple(trace.request or trace.stimulus for trace in traces)
        feedback = self._feedback(originals, catalogue)
        return tuple(item if isinstance(item, Refusal) else ReplayFeedback(
            trace.stimulus.fingerprint, item.catalogue_digest, item.record, item.hits)
            for trace, item in zip(traces, feedback, strict=True))


def _query_spec(seed, contract):
    hold = leading_reset_slots(seed.slots) if contract.clocks else 0
    return GenerationSpec(
        len(seed.slots), max((1, *(len(queue) for _, queue in seed.queues))),
        hold, 0, stall_every=0)


def _query_domain(view, contract, fixed_spec, schedule):
    spec = fixed_spec if fixed_spec is not None else _query_spec(view, contract)
    key = canonical_json({
        "spec": spec.as_dict(),
        "clock_schedule": None if view.clock_schedule is None else view.clock_schedule.as_dict(),
        "query_schedule": None if schedule is None else schedule.as_dict(),
        "memory_environment": (None if view.memory_environment is None
                               else view.memory_environment.as_dict())})
    return key, spec, schedule


@dataclass(frozen=True, slots=True)
class _PreparedGuidance:
    """A recorded domain's provider and ports, not its live session or answers."""

    spec: GenerationSpec
    provider: GenerationProvider
    extensions: GenerationExtensions

    @classmethod
    def bind(cls, spec, provider):
        return cls(spec, provider, GenerationExtensions.bind(provider))


class RuntimeGuidance:
    """One source catalogue, with local contexts indexed by recorded domains.

    Declaration and baseline observations do not construct a query context.
    Select prepares one only for a still-needed recorded domain, and reconciles
    its complete source/target identity against the declared capability.
    Prepared contexts can be reused; only one worker scope is live at a time.
    Switching scopes invalidates Select state and reselects before the next Probe.
    No runtime policy is replaced by a clock cutoff, including for new candidates
    whose latency differs from their parent's. Conversely, a supplied fixed_spec
    is never narrowed to the queue length or reset/drain observed in one seed.
    """

    runtime_guidance = True
    replay_guided = True

    def __init__(self, factory, executions, baseline, contract, *, name,
                 capability, observe=None, fixed_spec=None, clock_schedule=None,
                 recorded_schedule=None, worker_factory=None):
        if type(capability) is not GoalCapabilityUse or capability.capability != LOCAL_GUIDANCE:
            raise TypeError("runtime guidance needs its declared local-guidance capability")
        self._factory, self._executions, self._contract = factory, executions, contract
        self._baseline = tuple(baseline)
        self._worker_factory = worker_factory
        self.parallel_search = worker_factory is not None
        self._workers = []
        self._fixed_spec, self._clock_schedule = fixed_spec, clock_schedule
        self._recorded_schedule = recorded_schedule
        self._observe = observe
        self.name, self.policy = name, capability.implementation
        self._capability = capability
        self._manifest = capability.source_catalogue
        self.targetable_goals = tuple(capability.targets)
        self._contexts = {}
        self._query_view = None
        self._query_binding = None
        self._active = None
        self._scope = ExitStack()
        self._probe_budget = None
        self._selected = {}
        self._bindings = {}
        self._baseline_views = {}
        # Evaluation-owned proposal material, not query states or answers.
        # Different recorded schedules may reuse source input writers; the
        # exact query contexts above remain separate.
        self._preparation = ({}, Lock(), {}, {})
        self._material_cursor = 0
        self.prepare_seeds(baseline)
        if not self._baseline_views:
            raise Refused(Refusal("generation-runtime-no-complete-seed",
                                 "no baseline execution supplied a complete local query schedule"))

    def catalogue_manifest(self):
        return self._manifest

    def observe_many(self, seeds):
        """Read native feedback even when this run cannot seed another query.

        The execution owner runs new inputs once and retains failures as well
        as normal traces. Baseline observations reuse those existing records.
        """
        if self._observe is None:
            return None
        self._executions.prepare(seeds)
        return self._observe(seeds, self._manifest)

    def goal_capability_use(self):
        return self._capability

    def cache_telemetry(self):
        return GenerationCacheTelemetry(
            event for owner in (self, *self._workers)
            for context in owner._contexts.values()
            for event in context.provider.cache_telemetry().events)

    def execution_cost(self):
        return {"version": "runtime-guidance/2",
                "bindings": {key: value for owner in (self, *self._workers)
                             for key, value in owner._bindings.items()},
                "domains": [{"identity": key, "generation_spec": context.spec.as_dict(),
                             "worker": index,
                             "execution": (context.extensions.execution_cost()
                                           if context.extensions.execution_cost is not None else None)}
                            for index, owner in enumerate((self, *self._workers))
                            for key, context in owner._contexts.items()]}

    def fork_search_worker(self, index):
        factory = (self._factory if self._worker_factory is None
                   else self._worker_factory(index))
        worker = RuntimeGuidance(
            factory, self._executions.reader(), self._baseline, self._contract,
            name=self.name, capability=self._capability, observe=self._observe,
            fixed_spec=self._fixed_spec, clock_schedule=self._clock_schedule,
            recorded_schedule=self._recorded_schedule)
        worker._preparation = self._preparation
        self._workers.append(worker)
        return worker

    def _prepare_material(self, context):
        collect = context.extensions.collect_preparation
        use = context.extensions.use_preparation
        if collect is None or use is None:
            return
        futures, lock, _updates, _seen = self._preparation
        bound = context.spec.max_body_steps
        with lock:
            pending = futures.get(bound)
            owns_build = pending is None
            if owns_build:
                pending = futures[bound] = Future()
        if owns_build:
            try:
                seeds = tuple(view for views in self._baseline_views.values() for view in views
                              if (self._fixed_spec or _query_spec(view, self._contract)).max_body_steps == bound)
                # JSON detaches mutable dictionaries between worker handles.
                # No state, schedule or goal hit is imported with these inputs.
                pending.set_result(canonical_json(collect(seeds)))
            except BaseException as error:
                pending.set_exception(error)
                raise
        from .execution_control import check_execution, remaining_timeout

        remaining = remaining_timeout(float('inf'))
        try:
            material = pending.result(timeout=remaining if math.isfinite(remaining) else None)
        except TimeoutError:
            # Waiting for shared preparation consumes the same stage budget,
            # not an independent worker-failure or UNSAT outcome.
            check_execution()
            raise
        use(json.loads(material))

    def _exchange_preparation(self, context, *, publish=False):
        """Share new input donors across lanes and restarted source sessions.

        Each live session consumes each delta once. A restarted session imports
        the retained input pool again, but never another session's states,
        query plans or answers. Issued plans remain pinned by their provider.
        """
        export = context.extensions.preparation_updates
        use = context.extensions.use_preparation
        if export is None or use is None:
            return
        _futures, lock, updates, seen = self._preparation
        bound = context.spec.max_body_steps
        if publish:
            material = export()
            if material is None:
                return
            with lock:
                known = seen.setdefault(bound, set())
                delta = {key: material[key] for key in ('version', 'binding')}
                for kind in ('writers', 'commands'):
                    delta[kind] = []
                    for row in material[kind]:
                        identity = kind, canonical_json(row)
                        if identity not in known:
                            known.add(identity)
                            delta[kind].append(row)
                if delta['writers'] or delta['commands']:
                    updates.setdefault(bound, []).append(canonical_json(delta))
            return
        with lock:
            pending = tuple(updates.get(bound, ())[self._material_cursor:])
        if not pending:
            return
        material = json.loads(pending[0])
        for encoded in pending[1:]:
            delta = json.loads(encoded)
            if any(delta[key] != material[key] for key in ('version', 'binding')):
                raise ValueError('preparation updates disagree on their source binding')
            for kind in ('writers', 'commands'):
                material[kind].extend(delta[kind])
        use(material)
        self._material_cursor += len(pending)

    @contextmanager
    def search_scope(self, *, probe_budget):
        self._probe_budget = probe_budget
        self._workers.clear()
        try:
            yield
        finally:
            self._close_scope()

    def _close_scope(self):
        self._scope.close()
        self._active = None
        self._selected.clear()
        self._material_cursor = 0

    def _domain(self, view):
        # Select, capability checks and Probe share the same immutable recorded
        # view. Serialize its clock/memory domain once while it remains current,
        # not on every question. Keep one binding, not a copy per baseline seed.
        if view is not self._query_view:
            if self._fixed_spec is not None:
                schedule = self._clock_schedule
            elif self._recorded_schedule is not None:
                schedule = self._recorded_schedule(view)
            else:
                schedule = view.clock_schedule
            self._query_binding = _query_domain(view, self._contract, self._fixed_spec, schedule)
            self._query_view = view
        return self._query_binding

    def _context(self, view):
        key, spec, schedule = self._domain(view)
        if key not in self._contexts:
            # Preparation may need CPU workers too. Release the previous
            # session before building, not only before entering the next one.
            self._close_scope()
            provider = self._factory(spec, self._executions, schedule)
            if type(provider) is not UnavailableGenerationProvider:
                from .command import _provider_image

                if _provider_image(provider) != _provider_image(self):
                    raise Refused(Refusal("generation-runtime-catalogue-changed",
                                         "a local query context differs from its declared source capability"))
            self._contexts[key] = _PreparedGuidance.bind(spec, provider)
        return key, self._contexts[key]

    def _activate(self, view):
        key, context = self._context(view)
        if key != self._active:
            self._close_scope()
            scope = context.extensions.search_scope
            if scope is not None:
                self._scope.enter_context(scope(probe_budget=self._probe_budget))
            prepare = context.extensions.prepare_seeds
            if prepare is not None:
                # The worker's finite exchange accounting must see the entire
                # domain's baseline, not assume a single seed. Traces are reused.
                prepare(self._baseline_views.get(key, (view,)))
            self._prepare_material(context)
            self._active = key
        return context

    def prepare_seeds(self, seeds):
        self._executions.prepare(seeds)
        self._baseline_views.clear()
        for seed in seeds:
            trace = self._executions.trace(seed)
            if type(trace) is AuditedTrace:
                key, _spec, _schedule = self._domain(trace.stimulus)
                self._baseline_views.setdefault(key, []).append(trace.stimulus)

    def seed_refusal(self, seed):
        """A failed local context disables its seeds, not other recorded domains."""
        trace = self._executions.trace(seed)
        if isinstance(trace, Refusal):
            return trace
        key, _spec, _schedule = self._domain(trace.stimulus)
        context = self._contexts.get(key)
        return None if context is None else context.extensions.direction_refusal()

    def query_targets(self, targets, *, seed):
        """Read facts only from this seed's existing recorded query domain."""
        selected = selection_targets(self.targetable_goals, targets)
        trace = self._executions.trace(seed)
        if isinstance(trace, Refusal):
            return selected
        key, _spec, _schedule = self._domain(trace.stimulus)
        context = self._contexts.get(key)
        query = None if context is None else context.extensions.query_targets
        return (selected if query is None else
                query(selected, seed=trace.stimulus))

    def request_refusal(self, seed, request):
        """Report only the selected recorded domain; do not activate a worker."""
        trace = self._executions.trace(seed)
        if isinstance(trace, Refusal):
            return None  # The seed-level report owns missing execution evidence.
        key, _spec, _schedule = self._domain(trace.stimulus)
        context = self._contexts.get(key)
        if context is None:
            return None
        refusal = context.extensions.direction_refusal()
        if refusal is not None:
            return refusal
        return context.extensions.request_failure(trace.stimulus, request)

    def select(self, seed, *, targets=None):
        trace = self._executions.trace(seed)
        if isinstance(trace, Refusal):
            return trace
        view = trace.stimulus
        targets = selection_targets(self.targetable_goals, targets)
        key, context = self._context(view)
        unavailable = self.seed_refusal(seed)
        if unavailable is not None:
            return unavailable
        query_targets = context.extensions.query_targets
        if self._observe is not None and query_targets is not None and not query_targets(targets):
            # Native observation is the authority for coverage, including safety.
            # If Select could produce no query, it need not also reconstruct IR
            # states that nobody will consume. Missing native evidence still
            # refuses; constant-false targets remain declared and unhit.
            feedback, = self.observe_many((seed,))
            if isinstance(feedback, Refusal):
                return feedback
            if (type(feedback) is not ReplayFeedback or feedback.stimulus != seed.fingerprint
                    or feedback.catalogue_digest != self._manifest.digest()):
                raise Refused(Refusal("generation-runtime-feedback-mismatch",
                                     "query elision requires this seed's complete native feedback"))
            self._bindings[seed.fingerprint] = {
                "record": trace.fingerprint, "schedule": view.fingerprint,
                "query_domain": key, "selection": "constant-false-targets",
                "targets": list(targets)}
            return ProbeSelection((), self._manifest.digest(), replay=feedback,
                                  recorded_seed=view if seed.replay_policy is not None else None)
        context = self._activate(view)
        self._exchange_preparation(context)
        selected = context.provider.select(view, targets=targets)
        if isinstance(selected, Refusal):
            return selected
        feedback = selected.replay
        if feedback is None or feedback.stimulus != view.fingerprint:
            raise Refused(Refusal("generation-runtime-feedback-mismatch",
                                     "source feedback must refer to the recorded query seed"))
        self._exchange_preparation(context, publish=True)
        self._selected[view.identity_bytes] = frozenset(targets)
        self._bindings[seed.fingerprint] = {
            "record": trace.fingerprint, "schedule": view.fingerprint,
            "query_domain": self._active}
        from .generation_protocol import (QueryPlan)

        planned = type(selected.requests) is QueryPlan
        return ProbeSelection(
            selected.requests, selected.catalogue_digest,
            equivalent_requests=() if planned else selected.equivalent_requests,
            query_chains=() if planned else selected.query_chains,
            constant_false_goals=selected.constant_false_goals,
            replay=ReplayFeedback(seed.fingerprint, feedback.catalogue_digest,
                                  feedback.record, feedback.hits),
            recorded_seed=view if seed.replay_policy is not None else None)

    def probe(self, seed, request):
        trace = self._executions.trace(seed)
        if isinstance(trace, Refusal):
            return ProbeResult(Outcome.UNSUPPORTED, Scope.EXACT_LOCAL_HIT, detail=trace.why)
        view = trace.stimulus
        context = self._activate(view)
        selected_targets = self._selected.get(view.identity_bytes, frozenset())
        if not set(request.targets) <= selected_targets:
            # A new candidate can switch the active domain between two old
            # requests. Restoring only the first request's target group does
            # not restore the other source/protocol groups selected earlier.
            selected = self.select(seed, targets=tuple(sorted(
                selected_targets | set(request.targets))))
            if isinstance(selected, Refusal):
                return ProbeResult(Outcome.UNSUPPORTED, Scope.EXACT_LOCAL_HIT, detail=selected.why)
        answer = context.provider.probe(view, request)
        if answer.outcome not in (Outcome.SAT, Outcome.PROPOSED) or seed.replay_policy is None:
            return answer
        # A runtime query fixes the actual recorded schedule, including each
        # domain's reset edges. The shape-only query spec cannot express that
        # schedule's reset policy. Validate its typed fields and exact equality
        # to the seed, not an invented common reset-prefix count. Explicit
        # fixed-domain callers still retain their original membership checks.
        refusal = _candidate_refusal(view, answer.candidate, request, self._contract,
                                     self._fixed_spec)
        if refusal is not None:
            return ProbeResult(Outcome.UNSUPPORTED, answer.scope, detail=refusal.why,
                               solve_ms=answer.solve_ms)
        candidate = Stimulus(
            queues=answer.candidate.queues, memory_policy=seed.memory_policy,
            initial_state=seed.initial_state, source=seed.source,
            memory_environment=seed.memory_environment, replay_policy=seed.replay_policy)
        return ProbeResult(answer.outcome, answer.scope, answer.goal, candidate,
                           answer.detail, answer.solve_ms)
