"""Exact-local RTL guidance behind Appendix A.3's common scheduler.

Selection consumes the execution owner's completed native seed. For every
queue transaction that the recorded schedule actually presents, it freezes the first
presentation slot and the finite segment ending at the next first presentation.  Probe
then reconstructs the source or task-product state at that boundary. Single-input
questions are interleaved with widening windows of consecutive presentations within
one reset epoch. The entry state, schedule, queue length and fields outside the
window stay fixed; the task product recomputes all later transfers and states.
Concrete selection does not first construct the SMT relation it is meant to guide.

This module has no replay, hit-ledger, novelty or retention authority.  A satisfiable
model is merely a candidate returned to :mod:`behave.sim.generation`; the common
execution owner replays new candidates and retains the already-completed baseline.
"""

from __future__ import annotations

from bisect import bisect_right
from contextlib import contextmanager
from itertools import zip_longest

from behave.core import runtime_defaults
from behave.core import runtime_defaults as defaults

from ...core.catalogue import CatalogueManifest
from ...core.frozen import Frozen
from ...core.goal_provider import (
    LOCAL_GUIDANCE,
    RTL_LOCAL_GUIDANCE_POLICY,
    GoalCapabilityUse,
)
from ...core.monitor import INPUT_COMPLETION
from ...core.observation_product import TransactionProduct
from ...core.refusal import Refusal, Refused
from ...core.stimulus import Stimulus
from ...generation_protocol import (
    ProbeRequest,
    ProbeSelection,
    StimulusField,
    stimulus_bytes,
)
from ...generation_cache import GenerationCacheTelemetry
from ...generation_feedback import selection_targets
from ...generation_trace import queue_origins
from ...goal_policy import evaluation_goal_policy
from ...rtl.source_goals import is_source_goal
from .. import probing

__all__ = [
    "RTLGenerationProvider",
    "RTLScheduleSegment",
]


class RTLScheduleSegment(Frozen):
    """A seed-local interval selected from one independently replayed transaction."""

    __slots__ = ("id", "mutable_fields", "rank", "seed", "start", "stop")

    def __init__(self, id, seed, start, stop, rank, mutable_fields):
        if type(id) is not str or not id:
            raise ValueError("an RTL schedule segment needs a nonempty identity")
        if type(seed) is not str or not seed:
            raise ValueError("an RTL schedule segment needs its exact seed identity")
        if any(type(value) is not int or isinstance(value, bool)
               for value in (start, stop, rank)):
            raise TypeError("segment bounds and rank must be exact integers")
        if start < 0 or stop <= start or rank < 0:
            raise ValueError("an RTL schedule segment must be nonempty and ordered")
        fields = tuple(mutable_fields)
        if not fields or any(type(field) is not StimulusField for field in fields):
            raise ValueError(
                "an RTL schedule segment needs nonempty typed mutable fields")
        self.id = id
        self.seed = seed
        self.start = start
        self.stop = stop
        self.rank = rank
        self.mutable_fields = fields
        self._freeze()

    def as_dict(self):
        return {
            "id": self.id,
            "seed": self.seed,
            "start": self.start,
            "stop": self.stop,
            "rank": self.rank,
            "mutable_fields": [field.as_dict() for field in self.mutable_fields],
        }

    def request(self, targets, catalogue_digest, *, rank_span, leading=False):
        """Query priority is not part of the observed physical interval."""
        return ProbeRequest(targets, occurrence=self.id,
            mutable_fields=self.mutable_fields,
            rank=self.rank + (0 if leading else rank_span),
            catalogue_digest=catalogue_digest)

    @classmethod
    def from_dict(cls, value):
        fields = {
            "id", "seed", "start", "stop", "rank", "mutable_fields",
        }
        if type(value) is not dict or set(value) != fields:
            raise TypeError("an RTL schedule-segment image has exactly six fields")
        if type(value["mutable_fields"]) is not list:
            raise TypeError("segment mutable_fields is an exact finite list")
        got = cls(
            value["id"], value["seed"], value["start"], value["stop"],
            value["rank"], tuple(StimulusField.from_dict(field)
                  for field in value["mutable_fields"]),
        )
        if got.as_dict() != value:
            raise ValueError("an RTL schedule-segment image is not canonical")
        return got


def _segments_from_trace(contract, seed, trace):
    """Select first adapter presentations from existing concrete observations."""

    origins = queue_origins(contract, seed, trace)
    sources = {endpoint.name: endpoint for endpoint in contract.inputs()}
    ordered = sorted(
        (origin.presented, name, transaction)
        for (name, transaction), origin in origins.items())
    segments = []
    for index, (start, name, transaction) in enumerate(ordered):
        stop = (ordered[index + 1][0]
                if index + 1 < len(ordered) else len(seed.slots))
        # Coincident first presentations are distinct local questions over the same
        # slot.  Their intervals must remain nonempty.
        stop = max(start + 1, stop)
        source = sources[name]
        fields = tuple(
            StimulusField(name, transaction, field, origins[name, transaction].released)
            for field in range(len(source.fields)))
        # A payload-free event still executes and bounds its neighbors' source
        # intervals, but this fixed-schedule payload search cannot mutate it.
        # Keep its native hits/catalogue; offer no fabricated empty request.
        if not fields:
            continue
        identity = f"{seed.fingerprint}:{name}:{transaction}@{start}:{stop}"
        segments.append(RTLScheduleSegment(
            identity, seed.fingerprint, start, stop, start, fields))
    return tuple(segments)


def _joint_segments(contract, seed, segments):
    """Finite multi-input domains; no reset, framing or schedule synthesis."""
    from ...query_plan import widening_windows

    resets = [i for i, slot in enumerate(seed.slots) if slot.reset or slot.resets]
    epochs = {}
    for segment in segments:
        epoch = bisect_right(resets, segment.start)
        epochs.setdefault(epoch, {}).setdefault(segment.start, []).append(segment)
    markers = {(endpoint.name, i) for endpoint in contract.inputs()
               if endpoint.framing is not None for i, field in enumerate(endpoint.fields)
               if field.name in endpoint.framing.fields}
    for epoch, presentations in epochs.items():
        stop = resets[epoch] if epoch < len(resets) else len(seed.slots)
        for window in widening_windows(tuple(presentations.values())):
            mutable = tuple((segment.start, field) for group in window for segment in group
                            for field in segment.mutable_fields
                            if (field.endpoint, field.field) not in markers)
            fields = tuple(field for _start, field in mutable)
            if len({(f.endpoint, f.transaction) for f in fields}) < 2:
                continue
            start = min(start for start, _field in mutable)
            end = min(stop, max(s.stop for s in window[-1]))
            yield RTLScheduleSegment(f'{seed.fingerprint}:window@{start}:{end}',
                seed.fingerprint, start, end, start, fields)


def _product_rank_span(seed):
    # Source windows add at most one domain per dyadic tree node. Leave their
    # ordering before protocol and schedule queries; reconstruction must depend
    # only on the seed, never on the parent's selected target groups or intervals.
    return len(seed.slots) + 2 * sum(len(rows) for _name, rows in seed.queues)


class _IsolatedRTLGeneration:
    """Production exact-local capability with no solver object in the parent.

    Yosys and the independent Verilator replay already have their own OS-isolated
    boundaries.  This object batches concrete Select replay, then sends the frozen
    relation to an isolated, search-scoped Probe session. It retains only JSON
    values and the process handle; every Z3 parse and formula stays in the worker.
    """

    __slots__ = (
        "_context",
        "_declared_targets",
        "_execution",
        "_schedule_queries",
        "_schedule_targets",
        "_search_active",
        "_session_budget",
        "_session_timings",
        "_source_constant_false",
        "_source_payload",
        "_source_refusal",
        "_source_session",
        "_source_targets",
        "_source_traces",
        "_timeout_ms",
    )

    def __init__(self, artifact, contract, generation_spec, frontend_catalogue,
                 complete_catalogue, *, workdir, timeout_ms,
                 clock_schedule=None, goal_policy=None, goal_capability_use=None,
                 generation_targets=(),
                 generation_policy=RTL_LOCAL_GUIDANCE_POLICY, execution=None):
        from ...rtl.exact_job_context import RTLExactJobContext
        from .schedule_monitor_query import ScheduleMonitorQuery

        # Source, protocol and declared transaction goals share the local task
        # relation; schedule projections have their own query; Analysis owns the rest.
        declared = {goal.id for goal in TransactionProduct(contract).goals}
        self._declared_targets = frozenset(goal for goal in generation_targets if goal in declared)
        self._source_targets = tuple(goal for goal in generation_targets
            if is_source_goal(complete_catalogue[goal])
            or complete_catalogue[goal].provider == "monitor" or goal in declared)
        schedule_goals = ScheduleMonitorQuery.candidate_goals(contract)
        self._schedule_targets = tuple(goal for goal in generation_targets if goal in schedule_goals)
        self._context = RTLExactJobContext(
            artifact, contract, generation_spec, frontend_catalogue,
            complete_catalogue, workdir=workdir,
            clock_schedule=clock_schedule,
            generation_policy=generation_policy)
        self._execution = execution
        if goal_policy is not None:
            self._context.bind_goal_policy(goal_policy, guidance=goal_capability_use)
        self._timeout_ms = timeout_ms
        self._session_budget = None
        self._search_active = False
        self._session_timings = []
        self._source_payload = None
        self._source_session = None
        self._source_refusal = None
        self._source_traces = {}
        self._source_constant_false = frozenset()
        self._schedule_queries = {}

    def begin_search(self, probe_budget):
        if self._search_active:
            raise RuntimeError("an RTL generation search is already active")
        self._session_budget = probe_budget
        self._search_active = True
        self._source_refusal = None
        self._session_timings = []
        self._source_constant_false = frozenset()

    def end_search(self):
        try:
            if self._source_session is not None:
                self._source_session.close()
        finally:
            self._source_session = None
            self._session_budget = None
            self._search_active = False
            self._source_payload = None
            self._source_traces.clear()
            self._schedule_queries.clear()
            self._context.replay.clear()

    def execution_cost(self):
        # The scheduler collects this receipt after closing search_scope. Keep
        # completed facts until the next search, not the worker. Local UNSAT
        # answers never enter this static exclusion set.
        return {"version": "rtl-generation-cost/4", "operations": self._session_timings,
                "constant_false_goals": sorted(self._source_constant_false)}

    @staticmethod
    def _unavailable(stage, error):
        from ...rtl.exact_job_context import RTLExactJobContext

        got = RTLExactJobContext.unavailable(stage, error)
        return Refusal(
            got.kind.replace("rtl-exact-", "rtl-generation-", 1),
            got.why.replace("RTL exact", "RTL generation", 1), **got.detail)

    def cache_telemetry(self):
        # RTL Search keeps no cross-invocation context cache.
        return GenerationCacheTelemetry()

    def search_refusal(self):
        # These failures belong to the shared relation/session, not a local goal.
        # Reading them must not eagerly build a relation just to select seed hits.
        failures = []
        if self._source_targets:
            failures.append(self._source_refusal)
        if self._schedule_targets:
            failures.append(None)
        return failures[0] if failures and all(item is not None for item in failures) else None

    def request_refusal(self, seed, request):
        projected = self._schedule_queries.get(stimulus_bytes(seed))
        if projected is not None and set(request.targets) <= projected.goals:
            return None
        if request.targets and all(goal in self._source_targets for goal in request.targets):
            return self._source_refusal
        return None

    def _selection_from_trace(self, seed, trace, targets, feedback=None):
        from ...core.stimulus import AuditedTrace, Completion
        from .schedule_monitor_query import ScheduleMonitorQuery

        if isinstance(trace, Refusal):
            return trace
        if type(trace) is not AuditedTrace or trace.stimulus != seed:
            return Refusal(
                "rtl-generation-source-trace-mismatch",
                "concrete selection returned no complete trace of this seed")
        if trace.completion is not Completion.NORMAL:
            return Refusal(
                "rtl-generation-source-trace-incomplete",
                "concrete selection requires a normally completed source trace",
                completion=trace.completion.value)
        try:
            segments = _segments_from_trace(self._context.contract, seed, trace)
            digest = self._context.complete_catalogue.digest()
            seed_key = stimulus_bytes(seed)
            self._source_traces[seed_key] = trace
            projected = frozenset()
            if set(targets) & set(self._schedule_targets):
                query = ScheduleMonitorQuery(self._context.contract, seed, trace)
                self._schedule_queries[seed_key] = query
                projected = query.goals
            requests, selected = [], []
            catalogue = self._context.complete_catalogue
            # Source events and declared goals lead (enumeration rank, widening
            # windows); protocol and schedule goals trail with one input each.
            groups = (("/source", True, True, tuple(goal for goal in targets
                            if is_source_goal(catalogue[goal])
                            and goal not in self._source_constant_false and goal not in projected)),
                      ("/declared", False, True, tuple(goal for goal in targets
                            if goal in self._declared_targets and goal not in projected)),
                      ("", False, False, tuple(goal for goal in targets
                            if goal in self._source_targets and not is_source_goal(catalogue[goal])
                            and goal not in self._declared_targets and goal not in projected)),
                      ("/schedule", False, False, tuple(goal for goal in targets if goal in projected)))
            windows = tuple(_joint_segments(self._context.contract, seed, segments))
            domains = tuple((segment, joint) for pair in zip_longest(segments, windows)
                            for joint, segment in enumerate(pair) if segment is not None)
            for rank, (segment, joint) in enumerate(domains):
                for suffix, source_events, leading, goals in groups:
                    if not goals or (joint and not leading):
                        continue
                    interval = RTLScheduleSegment(
                        segment.id + suffix,
                        segment.seed, segment.start,
                        segment.start + 1 if source_events and not joint and not self._context.contract.clocks
                        else segment.stop, rank if leading else segment.rank,
                        segment.mutable_fields)
                    selected.append(interval)
                    requests.append(interval.request(goals, digest,
                        rank_span=_product_rank_span(seed), leading=leading))
            # A complete concrete selection owns the run environment. Callers
            # must not initialize this through a private context side channel.
            self._context.bind_memory_environment(seed.memory_environment)
            if feedback is None:
                feedback = self._context.selection_feedback(seed, trace)
            if isinstance(feedback, Refusal):
                return feedback
            if feedback.stimulus != seed.fingerprint or feedback.catalogue_digest != digest:
                raise ValueError("selection feedback belongs to a different seed or catalogue")
            return ProbeSelection(tuple(requests), digest, replay=feedback), tuple(selected)
        except Refused as exc:
            return exc.refusal
        except Exception as exc:  # noqa: BLE001 - ancillary selection boundary
            return self._unavailable("select", exc)

    def select_many(self, seeds, targets):
        """Select a finite seed batch after one shared concrete RTL build."""

        seeds = tuple(seeds)
        try:
            traces = (self._context.selection_traces(seeds) if self._execution is None
                      else self._execution.traces(seeds))
            feedback = (None if self._execution is None else
                        self._execution.feedback(seeds, self._context.complete_catalogue))
        except Refused as exc:
            return tuple(exc.refusal for _seed in seeds)
        except Exception as exc:  # noqa: BLE001 - ancillary replay boundary
            refusal = self._unavailable("select", exc)
            return tuple(refusal for _seed in seeds)
        if feedback is None:
            feedback = (None,) * len(seeds)
        return tuple(self._selection_from_trace(seed, trace, targets, observed)
                     for seed, trace, observed in zip(seeds, traces, feedback, strict=True))

    def select(self, seed, targets):
        return self.select_many((seed,), targets)[0]

    def probe(self, seed, request, segment, targets):
        # Direct API callers also get deterministic cleanup. The common scheduler
        # keeps this scope around its entire ordered search instead of each call.
        standalone = not self._search_active
        if standalone:
            self.begin_search(1)
        try:
            return self._probe(seed, request, segment, targets)
        finally:
            if standalone:
                self.end_search()

    def _probe(self, seed, request, segment, targets):
        from ...search_policy import remaining_solver_ms
        query = self._schedule_queries.get(stimulus_bytes(seed))
        if query is not None and set(request.targets) <= query.goals:
            remaining_solver_ms(self._timeout_ms)
            return query.probe(request, segment)
        if request.targets and all(goal in self._source_targets for goal in request.targets):
            return self._probe_source(seed, request, segment)
        return probing.ProbeResult(
            probing.Outcome.UNSUPPORTED, probing.Scope.RTL_LOCAL_HIT,
            detail="local RTL Search exhibits source events, protocol and declared transaction "
                   "goals, and schedule projections; bounded Analysis classifies the rest")

    def _probe_source(self, seed, request, segment):
        from ...rtl.source_generation import SourceGenerationSession
        from ...execution_control import remaining_timeout
        from ...search_policy import remaining_solver_ms
        from .generation_job import decode_probe_reply

        if self._source_refusal is not None:
            raise Refused(self._source_refusal)
        try:
            if self._source_payload is None:
                self._source_payload = {**self._context.source_documents(
                    timeout_s=remaining_timeout(self._context.artifact.timeout_s)),
                    "contract": self._context.contract.as_dict(),
                    "catalogue": self._context.complete_catalogue.as_dict()}
            if self._source_session is None:
                self._source_session = SourceGenerationSession(self._source_payload,
                    prepare_timeout_s=self._context.artifact.timeout_s,
                    query_timeout_s=(defaults.SIM_GENERATION_QUERY_OVERHEAD_SECONDS
                                     + (remaining_timeout(float("inf")) if self._timeout_ms is None
                                        else self._timeout_ms / 1000)),
                    probe_budget=self._session_budget, timings=self._session_timings)
                self._source_constant_false = frozenset(
                    self._source_session.opened["constant_false_goals"])
            if request.targets and set(request.targets) <= self._source_constant_false:
                return probing.ProbeResult(probing.Outcome.LOCAL_UNSAT,
                    probing.Scope.RTL_LOCAL_HIT,
                    detail="every requested source occurrence has literal-false IR control; "
                           "no solver query, catalogue unchanged")
            self._source_session.prepare()
            self._source_session.bind(seed, self._source_traces[stimulus_bytes(seed)])
            return decode_probe_reply(self._source_session.probe(request, remaining_solver_ms(self._timeout_ms),
                                                                 segment=segment),
                expected_catalogue_digest=self._context.complete_catalogue.digest())
        except Exception as exc:  # noqa: BLE001 - optional search transport, never a score
            self._source_refusal = exc.refusal if isinstance(exc, Refused) else self._unavailable("source", exc)
            if self._source_session is not None:
                self._source_session.close()
            raise Refused(self._source_refusal) from None


class RTLGenerationProvider:
    """Exact finite RTL ``Select``/``Probe`` provider.

    Construct with a ready instance, or use :meth:`from_artifact` to defer the costly
    exact frontend until ``Probe``.  A deferred capability failure becomes a typed
    selection refusal and therefore cannot replace or weaken the concrete score.
    """

    policy = RTL_LOCAL_GUIDANCE_POLICY
    replay_guided = True

    @contextmanager
    def search_scope(self, *, probe_budget):
        """Keep solver preparation inside one bounded scheduler-owned lifetime."""
        if self._isolated is not None:
            self._isolated.begin_search(probe_budget)
        try:
            yield
        finally:
            if self._isolated is not None:
                self._isolated.end_search()

    def execution_cost(self):
        return None if self._isolated is None else self._isolated.execution_cost()

    def __init__(self, targetable_goals=None,
                 timeout_ms=runtime_defaults.SIM_SEARCH_QUERY_MS, *,
                 provider_id="rtl/exact-local", frontend_catalogue=None,
                 isolated=None, goal_capability_use=None):
        if type(isolated) is not _IsolatedRTLGeneration:
            raise TypeError("the isolated RTL generation service has an exact type")
        if goal_capability_use is not None \
                and type(goal_capability_use) is not GoalCapabilityUse:
            raise TypeError(
                "RTL generation capability must be a GoalPolicy handoff")
        self._isolated = isolated
        self._goal_capability = goal_capability_use
        self.name = str(provider_id)
        if timeout_ms is not None and (type(timeout_ms) is not int or timeout_ms < 1):
            raise ValueError("RTL local probe timeout must be a positive integer or None")
        self.timeout_ms = timeout_ms

        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError(
                "RTL exact generation needs a frozen CatalogueManifest")
        self._catalogue_manifest = frontend_catalogue
        defaults = (tuple(frontend_catalogue) if targetable_goals is None
                    else tuple(targetable_goals))
        self.targetable_goals = tuple(sorted(defaults))
        self._selected = {}
        self._configuration_refusal = self._configuration_problem()

    @classmethod
    def from_artifact(cls, artifact, contract, generation_spec,
                      frontend_catalogue, *, workdir=None,
                      targetable_goals=None, timeout_ms=runtime_defaults.SIM_SEARCH_QUERY_MS,
                      provider_id="rtl/exact-local", clock_schedule=None,
                      goal_policy=None, _execution=None):
        """Create a lazy production provider without making guidance a score gate."""

        from ...stimulus_set import GenerationSpec

        if type(generation_spec) is not GenerationSpec:
            raise TypeError("RTL generation needs the frozen GenerationSpec")
        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError("RTL generation needs the frozen frontend catalogue")

        from ...goal_policy import source_guidance_catalogue

        complete_catalogue = source_guidance_catalogue(
            contract, frontend_catalogue, artifact_kind="rtl")
        from ...core.goal_provider import GoalPolicy

        if goal_policy is not None and type(goal_policy) is not GoalPolicy:
            raise TypeError("RTL guidance needs the exact frozen GoalPolicy")
        source_policy = (goal_policy if goal_policy is not None else
                         evaluation_goal_policy(contract, frontend_catalogue, artifact_kind="rtl"))
        capability = source_policy.bind_implementation(
            LOCAL_GUIDANCE, cls.policy, source_policy.manifest,
            None if targetable_goals is None else tuple(targetable_goals),
            exhaustive=targetable_goals is None, context_catalogue=complete_catalogue)
        targets = tuple(capability.targets)
        # Standalone callers need the same ordering as public preparation:
        # bind the native observer before freezing a context that refers to it.
        artifact.bind_observation(contract)
        isolated = _IsolatedRTLGeneration(
            artifact, contract, generation_spec, frontend_catalogue,
            complete_catalogue, workdir=workdir, timeout_ms=timeout_ms,
            clock_schedule=clock_schedule, goal_policy=source_policy,
            goal_capability_use=capability,
            generation_targets=targets,
            generation_policy=cls.policy, execution=_execution)
        return cls(
            targets, timeout_ms, provider_id=provider_id,
            isolated=isolated, frontend_catalogue=complete_catalogue,
            goal_capability_use=capability)

    def catalogue_manifest(self):
        return self._catalogue_manifest

    def cache_telemetry(self):
        return self._isolated.cache_telemetry()

    def search_refusal(self):
        if self._configuration_refusal is not None:
            return self._configuration_refusal
        return self._isolated.search_refusal()

    def request_refusal(self, seed, request):
        return self._isolated.request_refusal(seed, request)

    def query_targets(self, targets, *, seed=None):
        """Read already prepared source facts; never construct a query here.

        Source preparation may happen after batched Select. Its literal-false
        predicates apply to every occurrence in this prepared source, whereas
        ordinary local UNSAT answers do not. Other goals are left untouched.
        """
        selected = selection_targets(self.targetable_goals, targets)
        excluded = self._isolated._source_constant_false
        return tuple(goal for goal in selected if goal not in excluded)

    def goal_capability_use(self):
        """The source policy row authorizing this provider's selected targets."""

        return self._goal_capability

    def exact_job_context(self):
        """The solver-free relation context shared with post-score analysis."""

        return self._isolated._context

    def _configuration_problem(self):
        if not self.name.strip():
            return Refusal(
                "rtl-generation-provider-identity-unbound",
                "an RTL generation provider needs a stable nonempty direction name")
        if not self.targetable_goals:
            return Refusal(
                "rtl-generation-no-targetable-goals",
                "this RTL frontend declares no enabled exact-local target")
        if len(set(self.targetable_goals)) != len(self.targetable_goals) \
                or any(not goal for goal in self.targetable_goals):
            return Refusal(
                "rtl-generation-targets-invalid",
                "exact RTL generation targets are distinct nonempty goal IDs")
        unknown = sorted(
            set(self.targetable_goals) - set(self._catalogue_manifest))
        if unknown:
            return Refusal(
                "rtl-generation-target-outside-catalogue",
                "an RTL generation target is outside the frozen catalogue",
                unknown=unknown)
        return None

    def _accept_isolated_selection(self, seed, got, targets):
        if isinstance(got, Refusal):
            return got
        selection, segments = got
        # Select above is the same trusted parent object, not an external wire
        # boundary. Retain its requests once instead of reconstructing them.
        self._selected[stimulus_bytes(seed)] = {
            request.occurrence: (segment, request)
            for segment, request in zip(segments, selection.requests, strict=True)}
        if selection.catalogue_digest != self._catalogue_manifest.digest():
            return Refusal(
                "rtl-generation-isolated-selection-catalogue-mismatch",
                "isolated Select used a different frozen source catalogue")
        return selection

    def select_many(self, seeds, *, targets=None):
        """Select baseline seeds together when the production RTL service can batch."""

        seeds = tuple(seeds)
        if self._configuration_refusal is not None:
            return tuple(self._configuration_refusal for _seed in seeds)
        bad = next((seed for seed in seeds if type(seed) is not Stimulus), None)
        if bad is not None:
            refusal = Refusal(
                "rtl-generation-seed-not-stimulus",
                "RTL Select needs complete typed Stimulus seeds")
            return tuple(refusal for _seed in seeds)
        targets = selection_targets(self.targetable_goals, targets)
        got = self._isolated.select_many(seeds, targets)
        if type(got) not in (tuple, list) or len(got) != len(seeds):
            refusal = Refusal(
                "rtl-generation-isolated-selection-batch-incomplete",
                "isolated Select did not answer every seed in its finite batch")
            return tuple(refusal for _seed in seeds)
        return tuple(self._accept_isolated_selection(seed, selected, targets)
                     for seed, selected in zip(seeds, got))

    def select(self, seed, *, targets=None):
        if self._configuration_refusal is not None:
            return self._configuration_refusal
        if type(seed) is not Stimulus:
            return Refusal(
                "rtl-generation-seed-not-stimulus",
                "RTL Select needs one complete typed Stimulus seed")
        targets = selection_targets(self.targetable_goals, targets)
        return self._accept_isolated_selection(
            seed, self._isolated.select(seed, targets), targets)

    def probe(self, seed, request):
        scope = probing.Scope.RTL_LOCAL_HIT
        if type(seed) is not Stimulus or type(request) is not ProbeRequest:
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED, scope,
                detail="RTL Probe needs a typed seed and ProbeRequest")
        selected = self._selected.get(stimulus_bytes(seed), {})
        found = selected.get(request.occurrence)
        if found is None:
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED, scope,
                detail="the request does not name a segment selected for this seed")
        segment, selected_request = found
        if not request.is_restriction_of(selected_request):
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED, scope,
                detail="the request differs from the frozen selected RTL segment")
        if request.catalogue_digest != self._catalogue_manifest.digest():
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED, scope,
                detail="the request names a different frozen catalogue")
        if not request.targets or not request.mutable_fields:
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED, scope,
                detail="an RTL local query needs targets and mutable queue fields")
        # Input completion is evaluated only at the recorded horizon's final
        # slot. Earlier local segments contain no such event, regardless of
        # payload or DUT state. Fold that false disjunct before constructing
        # the relation; the terminal segment keeps its real query.
        if segment.stop < len(seed.slots) and INPUT_COMPLETION in request.targets:
            targets = tuple(goal for goal in request.targets if goal != INPUT_COMPLETION)
            if not targets:
                return probing.ProbeResult(probing.Outcome.LOCAL_UNSAT, scope,
                    detail="input-completion is terminal; this local interval excludes the final slot")
            request = request.with_targets(targets)
        return self._isolated.probe(seed, request, segment, self.targetable_goals)
