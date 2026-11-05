"""Behavior adapter for Appendix A.3's common ``alg:explore`` scheduler.

The adapter translates a complete stimulus plus an *accepted* source trace into local
Behavior states and queue-field coordinates, then delegates the symbolic question to
``local_ir.local_hit``.  It never retains a candidate or credits a hit; those are,
respectively, the common scheduler's and later canonical replay's responsibilities.
"""

from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager

from behave.core import runtime_defaults as defaults

from ...core.admission import AdmittedTask
from ...core.frozen import Frozen, FrozenMap
from ...core.goal_provider import (
    BEHAVIOR_LOCAL_GUIDANCE_POLICY,
    LOCAL_GUIDANCE,
)
from ...core.record_value import (
    canonical_json,
    freeze_record_value,
    thaw_record_value,
)
from ...core.reference import LoadedReference, ReferenceArtifact
from ...core.refusal import Refusal, Refused
from ...core.stimulus import AuditedTrace, Completion, Stimulus
from ...generation_protocol import ProbeRequest, ProbeSelection, StimulusField, stimulus_bytes
from ...generation_cache import (
    GenerationCacheBinding,
    GenerationCacheEvent,
    GenerationCacheKey,
    GenerationCacheKind,
    GenerationCacheTelemetry,
    GenerationContextCache,
    GenerationPayloadKind,
)
from ...generation_feedback import selection_targets
from ...generation_trace import queue_release_slots
from ...goal_policy import evaluation_goal_policy
from .. import probing

__all__ = [
    "AcceptedTraceOccurrences",
    "BehaviorExactProvider",
    "BehaviorIsolatedGenerationProvider",
    "BehaviorOccurrence",
]


_GENERATION_CONTEXT_BACKEND = "behavior-generation-context/85"
_GENERATION_CONTEXT_SCHEMAS = {
    GenerationCacheKind.LAYOUT: "behavior-generation-layout-context/5",
    GenerationCacheKind.SELECT: "behavior-generation-select-context/2",
    GenerationCacheKind.PROBE: "behavior-generation-probe-context/3",
}


def _thaw(value):
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _remaining_goal_groups(manifest, targets, hits=(), ruled_out=(), *, dependencies=None):
    """Coverage then safety goals not already settled by this concrete seed.

    ``targets`` already excludes this direction's previously observed goals when
    called by the scheduler. This filter additionally removes occurrence-local
    hits and existing sound IR discharges. The scheduler rechecks before Probe.
    """

    from ...core.catalogue import COVERAGE, SAFETY

    remaining = set(targets) - set(hits) - set(ruled_out)
    # Ranks belong to the fixed catalogue, not the current unhit subset.
    # Visit smaller state supports before their unions. Source catalogue order
    # breaks ties; hits never renumber a family. This is a work ordering, not a
    # limit on states, goals or available input sequences.
    partitions = tuple(sorted(dict.fromkeys(dependencies.get(goal, 0) for goal in manifest),
                              key=int.bit_count)) if dependencies is not None else (0,)
    groups = []
    for rank, goal_class in enumerate((COVERAGE, SAFETY)):
        selected = {}
        for goal in targets:
            if goal in remaining and manifest[goal].cls == goal_class:
                partition = dependencies.get(goal, 0) if dependencies is not None else 0
                selected.setdefault(partition, []).append(goal)
        for partition_rank, partition in enumerate(partitions):
            members = tuple(selected.get(partition, ()))
            if not members:
                continue
            # Keep the class rank even when this occurrence has no remaining
            # coverage goals.  Compacting away the empty class would otherwise
            # promote its safety request into the coverage rank.
            groups.append((rank * len(partitions) + partition_rank, members))
    return tuple(groups)


def _ordered_requests(requests):
    """Materialize the policy order encoded by each request's rank."""

    return tuple(sorted(
        requests,
        key=lambda request: (
            request.rank, request.occurrence, request.identity)))


def _field_sets(bindings, domains):
    """Widen by declared carrier size, keeping equal-size fields together.

    This is a finite ordering heuristic, not a domain reduction: the final
    question always frees every bound field. No field name, opcode, seed value
    or target identity participates in the policy. Structured fields remain
    whole, and their cost is the sum of their scalar carriers.
    """
    from ...core.value_type import as_value_type

    def bits(domain):
        kind = as_value_type(domain)
        if kind.kind == "boolean":
            return 1
        if kind.kind == "array":
            return kind.cells * bits(kind.element)
        if kind.kind == "record":
            return sum(bits(child) for _name, child in kind.fields)
        return kind.finite_carrier.width

    costs = tuple((field, bits(domains[name])) for name, field in bindings)
    return tuple(tuple(field for field, cost in costs if cost <= width)
                 for width in sorted({cost for _field, cost in costs}))


def _fixed_inputs(bindings, mutable_fields, values):
    mutable = {field.coordinate for field in mutable_fields}
    return {name: values[name] for name, field in bindings
            if field.coordinate not in mutable}


class BehaviorOccurrence(Frozen):
    """One accepted logical source step and how its inputs map back to a seed."""

    __slots__ = ("bindings", "hits", "id", "rank", "state")

    def __init__(self, id, rank, state, bindings, hits=()):
        self.id = str(id)
        self.rank = rank
        self.state = FrozenMap(state)
        self.bindings = tuple(sorted(
            ((str(name), field) for name, field in bindings),
            key=lambda pair: pair[0]))
        self.hits = tuple(sorted({str(goal) for goal in hits}))
        if any(not goal for goal in self.hits):
            raise ValueError("an occurrence has nonempty goal identifiers")
        self._freeze()

    def state_dict(self):
        return {name: _thaw(value) for name, value in self.state.items()}


class AcceptedTraceOccurrences:
    """Read-only occurrence service over ``(schedule, accepted_trace_A)``.

    ``trace_of`` is injected by the orchestration boundary that can ephemerally execute
    the source target or replay the seed.  Its trace must be of the exact seed.  Offered
    rows are never substituted: only non-bottom accepted payloads advance the logical
    Behavior state. Each mutable field points to the transaction's release
    slot, which can precede its presentation and acceptance after a stall.
    """

    def __init__(self, proc, contract, trace_of, *, replay_artifact=None,
                 replay_domain=None):
        self.proc = proc
        self.contract = contract
        self.trace_of = trace_of
        self.replay_artifact = (None if replay_artifact is None
                                else str(replay_artifact))
        self.replay_domain = (None if replay_domain is None
                              else str(replay_domain))
        self._configuration_refusal = None
        if not self.replay_artifact or not self.replay_domain:
            self._configuration_refusal = Refusal(
                "behavior-generation-replay-identity-unbound",
                "the accepted-trace service must be bound to the replay artifact and "
                "trace-domain identities for T-to-A generation")
        try:
            from ...behavior.task_boundary import task_signature_boundary

            self.boundary = task_signature_boundary(contract, proc.signature)
        except Refused as exc:
            self.boundary = None
            if self._configuration_refusal is None:
                self._configuration_refusal = exc.refusal

    def __call__(self, seed):
        if self._configuration_refusal is not None:
            return self._configuration_refusal
        try:
            trace = self.trace_of(seed)
        except Exception as exc:  # noqa: BLE001 - execution service boundary
            return Refusal(
                "behavior-generation-local-execution-failed",
                "the read-only source execution service could not produce the accepted "
                "trace needed to select local Behavior occurrences",
                seed=seed.fingerprint,
                error=f"{type(exc).__name__}: {exc}")
        if isinstance(trace, Refusal):
            return trace
        if type(trace) is not AuditedTrace:
            return Refusal(
                "behavior-generation-source-trace-not-audited",
                "the occurrence service requires an AuditedTrace, so offered and "
                "accepted transactions cannot be conflated",
                got=type(trace).__name__)
        if trace.stimulus.fingerprint != seed.fingerprint \
                or stimulus_bytes(trace.stimulus) != stimulus_bytes(seed):
            return Refusal(
                "behavior-generation-source-trace-wrong-seed",
                "the accepted trace is of a different complete stimulus than the seed "
                "being selected", seed=seed.fingerprint,
                trace=trace.stimulus.fingerprint)
        if trace.artifact != self.replay_artifact:
            return Refusal(
                "behavior-generation-source-trace-wrong-artifact",
                "the accepted trace was not produced by the replay artifact bound to "
                "this T-to-A provider",
                expected=self.replay_artifact, got=trace.artifact)
        if trace.domain != self.replay_domain:
            return Refusal(
                "behavior-generation-source-trace-wrong-domain",
                "the accepted trace was not produced under the trace domain bound to "
                "this generation run",
                expected=self.replay_domain, got=trace.domain)
        if trace.completion is not Completion.NORMAL:
            return Refusal(
                "behavior-generation-source-trace-incomplete",
                "selection requires a normally completed accepted trace for the full "
                "seed schedule; a failed or partial execution cannot define every "
                "source occurrence",
                completion=trace.completion.value, records=len(trace.records),
                slots=len(seed.slots))
        inputs = {row.endpoint: row.fields for row in self.boundary.inputs}
        first_accepted = next((
            slot for slot, record in enumerate(trace.records)
            if any(dict(record.accepted).get(endpoint) is not None
                   for endpoint in inputs)), None)
        if trace.initial_state and first_accepted is not None and not any(
                slot.reset for slot in seed.slots[:first_accepted + 1]):
            return Refusal(
                "behavior-generation-initial-state-unmapped",
                "the adapter can initialize the Behavior state from its declared reset, "
                "but this accepted trace begins before any reset slot and carries no "
                "audited artifact-to-Behavior state map",
                first_accepted=first_accepted)

        state = {name: _thaw(value) for name, value in self.proc.initial.items()}
        releases = queue_release_slots(
            self.contract, seed, (record.accepted for record in trace.records))
        transaction = {endpoint: 0 for endpoint in inputs}
        occurrences = []
        for slot, record in enumerate(trace.records):
            accepted_by_endpoint = dict(record.accepted)
            active = {
                endpoint: accepted_by_endpoint.get(endpoint)
                for endpoint in inputs
                if accepted_by_endpoint.get(endpoint) is not None
            }
            if seed.slots[slot].reset:
                # Reset has priority over a same-slot payload in the canonical reference
                # semantics.  A physical transfer still consumes the queue head, but it
                # is not a Behavior step and therefore is not a probe occurrence.
                state = {name: _thaw(value)
                         for name, value in self.proc.initial.items()}
                for endpoint, payload in active.items():
                    fields = inputs[endpoint]
                    if (type(payload) not in (tuple, list)
                            or len(payload) != len(fields)
                            or transaction[endpoint] >= seed.depth(endpoint)):
                        return Refusal(
                            "behavior-generation-source-map-outside-seed",
                            "an accepted reset-slot payload cannot be mapped to its "
                            "task endpoint queue",
                            endpoint=endpoint, slot=slot,
                            transaction=transaction[endpoint])
                    transaction[endpoint] += 1
                continue
            if not active and seed.slots[slot].ticks:
                return Refusal(
                    "behavior-generation-idle-tick-capability",
                    "the lowered transaction-step IR does not encode a reference-visible "
                    "tick method, so it cannot reconstruct state across an idle slot "
                    "that names clock ticks",
                    slot=slot, ticks=list(seed.slots[slot].ticks))
            if not active:
                continue
            from ...behavior.task_boundary import (
                TaskBoundaryError,
                accepted_input_valuation,
            )

            try:
                values = accepted_input_valuation(self.boundary, active)
            except TaskBoundaryError as exc:
                return Refusal(exc.code, str(exc), slot=slot, **exc.detail)

            bindings = []
            occurrence_parts = []
            for endpoint in sorted(active):
                payload = active[endpoint]
                projection = inputs[endpoint]
                index = transaction[endpoint]
                if (type(payload) not in (tuple, list)
                        or len(payload) != len(projection)
                        or index >= seed.depth(endpoint)):
                    return Refusal(
                        "behavior-generation-source-map-outside-seed",
                        "the accepted trace cannot be mapped one-to-one to its seed "
                        "endpoint queue",
                        endpoint=endpoint, slot=slot, transaction=index)
                fields = tuple(
                    StimulusField(endpoint, index, field, releases[endpoint, index])
                    for field in range(len(projection)))
                bindings.extend(
                    (logical, field)
                    for (_public, logical), field in zip(projection, fields))
                occurrence_parts.append(f"{endpoint}:{index}")
            before = state
            try:
                from ...symbolic import interp

                state, _outputs, _hits, error = interp.run_step(
                    self.proc, values, state)
            except Exception as exc:  # noqa: BLE001 - local execution diagnostic
                return Refusal(
                    "behavior-generation-local-interpreter-failed",
                    "the lowered Behavior representation could not replay the accepted "
                    "source position used for selection",
                    slot=slot,
                    error=f"{type(exc).__name__}: {exc}")
            occurrences.append(BehaviorOccurrence(
                f"{seed.fingerprint}:{'+'.join(occurrence_parts)}@{slot}",
                slot, before, bindings, _hits))
            for endpoint in active:
                transaction[endpoint] += 1
            if error:
                break
        return tuple(occurrences)


class BehaviorExactProvider:
    """Existing exact-local solver behind the shared Select/Probe protocol."""

    policy = BEHAVIOR_LOCAL_GUIDANCE_POLICY

    def __init__(self, instance, occurrences, targetable_goals=None,
                 timeout_ms=defaults.SIM_SEARCH_QUERY_MS, *, provider_id=None):
        from ...core.catalogue import CatalogueManifest

        self.instance = instance
        self.proc = instance.proc
        self.env = instance.env
        self.occurrences = occurrences
        self.name = "" if provider_id is None else str(provider_id)
        manifest_of = getattr(instance, "catalogue_manifest", None)
        if not callable(manifest_of):
            raise TypeError(
                "a Behavior generation provider needs the instance's frozen complete "
                "CatalogueManifest")
        manifest = manifest_of()
        if type(manifest) is not CatalogueManifest:
            raise TypeError(
                "the Behavior frontend returned a non-CatalogueManifest")
        declared_goals = set(manifest)
        self._catalogue_manifest = manifest
        # The manifest is the declaration authority.  Do not walk the IR here to
        # rediscover its goals: that would let generation silently use a denominator
        # different from concrete replay and bounded analysis.  The lowering's frozen
        # ``impossible`` and ``discharged`` maps are not other declarations.  They are
        # exact false implementations of source-declared goals, retained in Delta but
        # omitted from seed-local generation questions.
        encoded_goals = {
            goal_id for goal_id, goal in manifest.items()
            if goal.provider == "python-structural"
        }
        from ...behavior.goal_projection import literal_false_goals

        self._ruled_out = literal_false_goals(self.proc)
        goals = encoded_goals if targetable_goals is None else targetable_goals
        selected = tuple(
            str(goal) for goal in (goals.keys() if hasattr(goals, "keys") else goals))
        duplicate_targets = sorted(
            goal for goal in set(selected) if selected.count(goal) > 1)
        self.targetable_goals = tuple(sorted(selected))
        unknown = sorted(set(self.targetable_goals) - declared_goals)
        unencoded = sorted(set(self.targetable_goals) - encoded_goals)
        self._configuration_refusal = None
        if duplicate_targets:
            self._configuration_refusal = Refusal(
                "behavior-generation-target-duplicate",
                "one Behavior generation provider names the same public goal more "
                "than once",
                goals=duplicate_targets)
        elif not self.name.strip():
            self._configuration_refusal = Refusal(
                "behavior-generation-provider-identity-unbound",
                "a Behavior provider must name its source-target to replay-artifact "
                "direction so two independent A.3 searches remain distinguishable")
        elif unknown:
            self._configuration_refusal = Refusal(
                "behavior-generation-target-outside-catalogue",
                "an exact generation target must belong to this Behavior instance's "
                "declared goal catalogue",
                unknown=unknown)
        elif unencoded:
            self._configuration_refusal = Refusal(
                "behavior-generation-target-without-exact-encoding",
                "a selected goal is not owned by the Behavior local-guidance adapter",
                unsupported=unencoded)
        if timeout_ms is not None and (type(timeout_ms) is not int or timeout_ms < 1):
            raise ValueError("Behavior query timeout must be a positive integer or None")
        self.timeout_ms = timeout_ms
        self._selected = {}

    def catalogue_manifest(self):
        """The frontend declaration this provider selected targets from."""

        return self._catalogue_manifest

    def cache_telemetry(self):
        return GenerationCacheTelemetry()

    def select(self, seed, *, targets=None):
        if self._configuration_refusal is not None:
            return self._configuration_refusal
        if not self.targetable_goals:
            return Refusal(
                "behavior-generation-no-targetable-goals",
                "this Behavior instance declares no enabled local guidance adapter")
        targets = selection_targets(self.targetable_goals, targets)
        got = self.occurrences(seed)
        if isinstance(got, Refusal):
            return got
        if type(got) not in (tuple, list):
            return Refusal(
                "behavior-generation-occurrences-not-finite",
                "the occurrence service must return a materialized finite sequence",
                got=type(got).__name__)
        by_id = {}
        for occurrence in got:
            if type(occurrence) is not BehaviorOccurrence:
                return Refusal(
                    "behavior-generation-occurrence-not-typed",
                    "the source service returned something other than a typed "
                    "BehaviorOccurrence", got=type(occurrence).__name__)
            if occurrence.id in by_id:
                return Refusal(
                    "behavior-generation-occurrence-duplicate",
                    "two source positions have the same occurrence identity",
                    occurrence=occurrence.id)
            by_id[occurrence.id] = occurrence
        rank_span = 1 + max((occurrence.rank for occurrence in by_id.values()),
                            default=0)
        requests = []
        selections = {}
        domains = dict(self.proc.semantics.input_domains)
        field_sets = {occurrence.id: _field_sets(occurrence.bindings, domains)
                      for occurrence in by_id.values()}
        tier_span = max((len(sets) for sets in field_sets.values()), default=1)
        for occurrence in by_id.values():
            local = []
            for group, selected in _remaining_goal_groups(
                    self._catalogue_manifest, targets,
                    occurrence.hits, self._ruled_out):
                for tier, fields in enumerate(field_sets[occurrence.id]):
                    local.append(ProbeRequest(
                        selected, occurrence=occurrence.id, mutable_fields=fields,
                        rank=(group * rank_span + occurrence.rank) * tier_span + tier,
                        catalogue_digest=self._catalogue_manifest.digest()))
            requests.extend(local)
            selections[occurrence.id] = (occurrence, tuple(local))
        self._selected[stimulus_bytes(seed)] = selections
        return ProbeSelection(
            _ordered_requests(requests), self._catalogue_manifest.digest())

    def _candidate(self, seed, occurrence, values):
        bindings = dict(occurrence.bindings)
        if type(values) is not dict or set(values) != set(bindings):
            return None, Refusal(
                "behavior-generation-decode-shape",
                "the exact provider decoded neither exactly one value per Behavior "
                "input nor the names this occurrence binds",
                expected=sorted(bindings),
                got=sorted(values) if type(values) is dict else type(values).__name__)
        queues = {name: [list(transaction) for transaction in txns]
                  for name, txns in seed.queues}
        try:
            for name, value in values.items():
                field = bindings[name]
                queues[field.endpoint][field.transaction][field.field] = value
            candidate = Stimulus(
                queues={name: [tuple(transaction) for transaction in txns]
                        for name, txns in queues.items()},
                slots=seed.slots,
                memory_policy=dict(seed.memory_policy),
                memory_environment=seed.memory_environment,
                initial_state=dict(seed.initial_state),
                source=seed.source,
                clock_schedule=seed.clock_schedule)
        except Exception as exc:  # noqa: BLE001 - decode is a provider outcome
            return None, Refusal(
                "behavior-generation-decode-failed",
                "the exact model could not be spliced into the request's finite "
                "stimulus fields",
                error=f"{type(exc).__name__}: {exc}")
        return candidate, None

    def probe(self, seed, request):
        if request.catalogue_digest != self._catalogue_manifest.digest():
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED,
                probing.Scope.EXACT_LOCAL_HIT,
                detail="the request names a different catalogue manifest from the "
                       "one this Behavior provider selected against")
        occurrences = self._selected.get(stimulus_bytes(seed), {})
        selected = occurrences.get(request.occurrence)
        if selected is None:
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED,
                probing.Scope.EXACT_LOCAL_HIT,
                detail="this request does not name an occurrence selected for the seed")
        occurrence, requests = selected
        if not any(request.is_restriction_of(item) for item in requests):
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED,
                probing.Scope.EXACT_LOCAL_HIT,
                detail="the request differs from the selected Behavior occurrence, "
                       "targets, rank or mutable fields")

        from .. import local_ir

        queues = dict(seed.queues)
        got = local_ir.local_hit_any(
            self.proc, self.env, occurrence.state_dict(), request.targets,
            timeout_ms=self.timeout_ms,
            fixed_inputs=_fixed_inputs(occurrence.bindings, request.mutable_fields,
                {name: queues[field.endpoint][field.transaction][field.field]
                 for name, field in occurrence.bindings}),
            tag=f"_gen_{request.identity[:12]}")
        if got.outcome is not probing.Outcome.SAT:
            return got
        candidate, refusal = self._candidate(seed, occurrence, got.candidate)
        if refusal is not None:
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED,
                probing.Scope.EXACT_LOCAL_HIT,
                goal=got.goal, detail=f"{refusal.kind}: {refusal.why}",
                solve_ms=got.solve_ms)
        return probing.ProbeResult(
            probing.Outcome.SAT,
            probing.Scope.EXACT_LOCAL_HIT,
            goal=got.goal, candidate=candidate, solve_ms=got.solve_ms)


def _plain_config(value):
    if type(value) is tuple:
        return [_plain_config(item) for item in value]
    return value


def _source_image(artifact, sources, config):
    """Freeze source bytes and construction data beside their exact identity."""

    if type(artifact) is not ReferenceArtifact:
        raise TypeError("Behavior guidance needs one exact source artifact")
    rows = [{
        "name": name,
        "base64": base64.b64encode(body).decode("ascii"),
    } for name, body in sorted(sources.items())]
    return freeze_record_value({
        "artifact": artifact.as_dict(),
        "sources": rows,
        "config": {name: _plain_config(value)
                   for name, value in sorted(config.items())},
    }, "behavior-generation-source")


def _authenticated_source_image(reference):
    """A recursively immutable image of authenticated reference-loader inputs."""

    if type(reference) is not LoadedReference:
        raise TypeError("Behavior guidance needs one exact LoadedReference")
    sources, config = reference._generation_source_payload()
    return _source_image(reference.artifact, sources, config)


def _context_source_image(context):
    """Bind candidate guidance to the source snapshot retained by its exact handle."""

    from ...behavior.construction import _dependency_snapshot
    from ...behavior.exact_service import BehaviorExactJobContext
    from ...behavior.instance import FRONTEND

    if type(context) is not BehaviorExactJobContext:
        raise TypeError("candidate Behavior guidance needs its exact job context")
    if context.refusal is not None:
        raise Refused(context.refusal)
    dependency_identity, dependency = _dependency_snapshot()
    if dependency_identity != context.construction_dependency:
        raise Refused(Refusal(
            "behavior-generation-construction-dependency-changed",
            "candidate guidance and exact analysis name different approved "
            "construction dependencies",
            exact=context.construction_dependency,
            generation=dependency_identity))
    if context.file == "fp_lib.py":
        raise Refused(Refusal(
            "behavior-generation-source-name-reserved",
            "the candidate Behavior entry file cannot use the approved dependency's "
            "reserved source name",
            file=context.file))
    sources = {
        context.file: context.source.encode("utf-8"),
        "fp_lib.py": dependency,
    }
    config = {"class": context.class_name, "file": context.file}
    artifact = ReferenceArtifact(
        sources, config=config, adapter="behavior/exact-context-1",
        loader="candidate exact-generation source",
        adapter_impl=(f"behavior-frontend={FRONTEND};"
                      f"construction={dependency_identity}"))
    return _source_image(artifact, sources, config), artifact.digest


def _identity(prefix, value):
    return prefix + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _generation_cache_binding(task, target, generation_spec, source_binding, *,
                              catalogue=None, targets=None,
                              source_identity=None, policy_digest=None):
    """Bind source construction separately from a selected execution domain."""

    import z3

    from ...behavior.instance import FRONTEND

    if catalogue is None:
        catalogue_identity = "behavior-catalogue/1:derived-from-source"
    else:
        catalogue_identity = _identity(
            "behavior-catalogue-and-targets/1:", {
                "catalogue": catalogue.digest(),
                "targets": list(targets or ()),
            })
    return GenerationCacheBinding(
        source=source_binding,
        target=f"realization/1:{target}",
        task_contract=f"task-contract/1:{task.contract.digest}",
        # Lowering depends on the source/contract and transaction bound, not
        # the physical slots used by an already recorded execution. Select
        # and Probe still bind the complete spec and exact trace below.
        generation_spec=(_identity("behavior-source-bound/1:", {
            "max_body_steps": generation_spec.max_body_steps}) if catalogue is None
            else _identity("generation-spec/1:", generation_spec.as_dict())),
        catalogue=catalogue_identity,
        goal_policy=(
            "goal-policy/1:derived-from-task-and-source"
            if policy_digest is None else f"goal-policy/1:{policy_digest}"),
        representation=(
            "behavior-representation/1:derived-from-source"
            if source_identity is None
            else f"behavior-representation/1:{source_identity}"),
        encoding="behavior-generation-encoding/3:shared-occurrence-local-hit",
        environment=(
            "accepted-target-trace/1:none-at-layout"
            if catalogue is None else "accepted-target-trace/1:key-bound"),
        provider="behavior-isolated-generation/1",
        provider_policy=BEHAVIOR_LOCAL_GUIDANCE_POLICY,
        frontend="behavior", frontend_version=FRONTEND,
        backend="behavior-generation-worker",
        backend_version=_GENERATION_CONTEXT_BACKEND,
        tool="z3", tool_version=z3.get_version_string())


class _ContextNotCacheable(Exception):
    __slots__ = ("answer",)

    def __init__(self, answer):
        self.answer = answer


def _context_reuse_detail(context, reused):
    if context is None:
        return ""
    return "provider-context-reused" if reused else "provider-context-rejected"


class BehaviorIsolatedGenerationProvider(Frozen):
    """Sealed Behavior-source -> target provider backed by named worker jobs.

    The source is either the admitted golden reference or the candidate snapshot
    retained by its exact handle.  The parent runs the target artifact solely to obtain
    its accepted trace.  It never parses or executes the guidance source and never
    imports its lowering, interpreter, encoder, or solver. A search validates the
    fixed source context once inside one worker and binds every Select/Probe to its
    exact accepted trace. Standalone calls retain the independent named-job path.
    """

    policy = BEHAVIOR_LOCAL_GUIDANCE_POLICY
    replay_guided = True
    explores_contexts = True

    def __init_subclass__(cls, **kwargs):
        raise TypeError(
            "BehaviorIsolatedGenerationProvider is a closed public capability")

    def __init__(self, task, target, generation_spec, reference, trace_of, *,
                 traces_of=None,
                 targets=None, timeout_s=defaults.SIM_BEHAVIOR_WORKER_SECONDS,
                 timeout_ms=defaults.SIM_SEARCH_QUERY_MS,
                 cache_root=None):
        if type(reference) is not LoadedReference:
            raise TypeError("Behavior guidance needs an authenticated LoadedReference")
        if type(task) is not AdmittedTask:
            raise TypeError("Behavior guidance needs the exact AdmittedTask")
        if reference.digest != task.reference:
            raise Refused(Refusal(
                "behavior-generation-reference-binding-mismatch",
                "the guidance source must be the exact reference frozen by admission",
                admitted=task.reference or None, reference=reference.digest))
        self._initialize(
            task, target, generation_spec,
            _authenticated_source_image(reference),
            f"reference-artifact/1:{reference.digest}", reference.digest,
            trace_of, traces_of=traces_of, targets=targets, timeout_s=timeout_s,
            timeout_ms=timeout_ms, cache_root=cache_root)

    @classmethod
    def from_behavior_artifact(
            cls, artifact, task, generation_spec, frontend_catalogue, *,
            targets=None, timeout_s=defaults.SIM_BEHAVIOR_WORKER_SECONDS,
            timeout_ms=defaults.SIM_SEARCH_QUERY_MS, cache_root=None, _execution=None,
            goal_policy=None):
        """Construct the ``B_hat -> B_hat`` direction from one closed handle.

        The source bytes and the replay operation come from the same
        :class:`BehaviorArtifact`; callers cannot pair candidate guidance with a
        different executable artifact.
        """

        from ...behavior.artifact import BehaviorArtifact
        from ...core.catalogue import CatalogueManifest

        if type(artifact) is not BehaviorArtifact:
            raise TypeError("candidate Behavior guidance needs a BehaviorArtifact")
        if type(task) is not AdmittedTask or artifact._context.task is not task:
            raise Refused(Refusal(
                "behavior-generation-candidate-task-mismatch",
                "candidate guidance must use the exact admitted task retained by its "
                "concrete Behavior artifact"))
        if type(frontend_catalogue) is not CatalogueManifest \
                or artifact.frontend_catalogue is not frontend_catalogue:
            raise Refused(Refusal(
                "behavior-generation-candidate-catalogue-mismatch",
                "candidate guidance must use the frontend catalogue retained by its "
                "concrete Behavior artifact"))
        source, digest = _context_source_image(artifact._context)
        got = object.__new__(cls)
        got._initialize(
            task, task.realization.identity, generation_spec, source,
            (f"behavior-realization/1:{task.realization.identity};"
             f"source-bundle/1:{digest}"),
            f"candidate:{task.realization.identity}",
            lambda seed: (artifact.trace(task.contract, seed) if _execution is None
                          else _execution.traces((seed,))[0]),
            traces_of=lambda seeds: (tuple(artifact.trace(task.contract, seed) for seed in seeds)
                                     if _execution is None else _execution.traces(seeds)),
            targets=targets, timeout_s=timeout_s, timeout_ms=timeout_ms,
            cache_root=cache_root, expected_catalogue=frontend_catalogue,
            goal_policy=goal_policy)
        return got

    def _initialize(
            self, task, target, generation_spec, source, source_binding,
            source_name, trace_of, *, traces_of=None, targets, timeout_s, timeout_ms,
            cache_root,
            expected_catalogue=None, goal_policy=None):
        from ...stimulus_set import GenerationSpec
        from ...worker import WorkerFailed
        from .generation_job import decode_layout_reply

        if type(task) is not AdmittedTask:
            raise TypeError("Behavior guidance needs the exact AdmittedTask")
        if type(generation_spec) is not GenerationSpec:
            raise TypeError("Behavior guidance needs the frozen GenerationSpec")
        if type(target) is not str or not target \
                or target != task.realization.identity:
            from ...core.refusal import Refused

            raise Refused(Refusal(
                "behavior-generation-target-binding-mismatch",
                "Behavior guidance must target the artifact named by admission",
                admitted=task.realization.identity, target=str(target or "")))
        if not callable(trace_of):
            raise TypeError("Behavior guidance needs a read-only accepted-trace service")
        if traces_of is not None and not callable(traces_of):
            raise TypeError("Behavior guidance batch replay must be callable or absent")
        if type(timeout_s) is not int or timeout_s < 1 \
                or (timeout_ms is not None and (type(timeout_ms) is not int or timeout_ms < 1)):
            raise TypeError("Behavior preparation needs positive seconds; query cap may be None")
        self._task = task
        self._target = target
        self._generation_spec = generation_spec
        self._source = source
        self._trace_of = trace_of
        self._traces_of = traces_of
        self._timeout_s = timeout_s
        self._timeout_ms = timeout_ms
        self._selected = {}
        self._prepared_traces = {}
        self._preparation_plans = {}
        self._searches = []
        self._session_timings = []
        self._session_refusals = []
        self._cache_events = []
        self._cache_error = ""
        self._cache = None
        if cache_root is not None:
            try:
                self._cache = GenerationContextCache(cache_root)
            except Exception as exc:  # noqa: BLE001 - cache is diagnostic only
                self._cache_error = f"{type(exc).__name__}: {exc}"
        layout_binding = _generation_cache_binding(
            task, target, generation_spec, source_binding)
        common = self._common_payload()
        try:
            reply, context_image = self._run_context_job(
                "behavior_generation_layout", common,
                GenerationCacheKey.layout(layout_binding), timeout_s,
                label="Behavior guidance layout")
            layout = decode_layout_reply(reply)
        except WorkerFailed as exc:
            from ...core.refusal import Refused

            raise Refused(Refusal(
                exc.code or "behavior-generation-layout-unavailable",
                "the isolated Behavior guidance layout did not complete",
                failure=exc.how, detail=exc.detail)) from None
        if isinstance(layout, Refusal):
            from ...core.refusal import Refused

            raise Refused(layout)
        from ...goal_policy import source_guidance_catalogue

        native_catalogue = (expected_catalogue if expected_catalogue is not None else
                            layout.frontend_catalogue)
        expected_context = source_guidance_catalogue(task.contract, native_catalogue, artifact_kind="behavior")
        if layout.catalogue != expected_context:
            raise Refused(Refusal(
                "behavior-generation-candidate-catalogue-mismatch",
                "candidate guidance rebuilt a frontend catalogue different from the "
                "one frozen by its exact handle",
                expected=expected_context.digest(),
                got=layout.catalogue.digest()))
        offered = layout.targetable_goals if targets is None else tuple(targets)
        selected = tuple(sorted(str(goal) for goal in offered))
        if len(selected) != len(set(selected)):
            raise ValueError("Behavior guidance targets are distinct")
        unknown = sorted(set(selected) - set(layout.targetable_goals))
        if unknown:
            from ...core.refusal import Refused

            raise Refused(Refusal(
                "behavior-generation-target-without-exact-encoding",
                "a selected reference-source goal has no exact local encoding",
                goals=unknown))
        self._layout = layout
        # This is an opaque worker image, not an answer authority.  Passing it to later
        # one-shot Select and Probe jobs amortizes the source lowering and finite-plan
        # construction; every receiving worker independently validates all semantic
        # bindings before using it.
        self._context_image = context_image
        self.targetable_goals = selected
        source_policy = goal_policy
        if source_policy is None:
            source_policy = evaluation_goal_policy(
                task.contract, native_catalogue, artifact_kind="behavior")
        self._goal_capability = source_policy.bind_implementation(
            LOCAL_GUIDANCE, self.policy, source_policy.manifest, selected,
            exhaustive=targets is None, context_catalogue=layout.catalogue)
        self._cache_binding = _generation_cache_binding(
            task, target, generation_spec, source_binding,
            catalogue=layout.catalogue, targets=selected,
            source_identity=layout.source_identity,
            policy_digest=self._goal_capability.policy_digest)
        # Provider names are copied into every retained provenance row. They bind the
        # source and target and therefore retain both complete identities.
        self.name = f"behavior/{source_name}->{target}"
        self._freeze()

    def _cache_event(self, kind, disposition, key, *, record=None, detail=""):
        self._cache_events.append(GenerationCacheEvent(
            kind, disposition, key.identity, record, detail))

    def _run_context_job(self, job, payload, key, timeout_s, *, label,
                         context=None):
        """Run one job with a validated provider/durable opaque build context."""

        from ...worker import run_job_strict
        from .generation_job import decode_context_reply

        kind = key.kind
        schema = _GENERATION_CONTEXT_SCHEMAS[kind]

        def invoke(context):
            offered = {
                **payload,
                "cached_context": (
                    None if context is None
                    else base64.b64encode(context).decode("ascii")),
            }
            return decode_context_reply(run_job_strict(
                job, offered, timeout_s, label=label))

        if self._cache is None:
            answer, returned_context, reused = invoke(context)
            self._cache_event(
                kind, "error" if self._cache_error else "bypass", key,
                detail=self._cache_error or _context_reuse_detail(context, reused))
            return answer, returned_context

        cold = {}

        def build():
            answer, returned_context, reused = invoke(context)
            cold["answer"] = answer
            cold["context"] = returned_context
            cold["reused"] = reused
            if returned_context is None:
                raise _ContextNotCacheable(answer)
            return returned_context

        try:
            lookup = self._cache.get_or_build(
                key, payload_schema=schema,
                payload_kind=GenerationPayloadKind.BYTES, builder=build)
        except _ContextNotCacheable as exc:
            self._cache_event(kind, "miss", key, detail="context-not-cacheable")
            return exc.answer, None
        except Exception as exc:  # noqa: BLE001 - store is diagnostic only
            answer = cold.get("answer")
            returned_context = cold.get("context")
            if answer is None:
                answer, returned_context, _reused = invoke(context)
            self._cache_event(
                kind, "error", key,
                detail=f"{type(exc).__name__}: {exc}")
            return answer, returned_context

        if not lookup.cache_hit:
            self._cache_event(
                kind, "miss", key, record=lookup.record_identity,
                detail=("provider-context-reused"
                        if context is not None and cold.get("reused") else ""))
            return cold["answer"], cold["context"]

        try:
            answer, returned_context, reused = invoke(lookup.payload_bytes)
        except Exception as exc:  # noqa: BLE001 - warm cache is untrusted
            # A stale/hostile opaque image can at worst make this warm invocation fail;
            # retry the ordinary cold job before exposing an answer.
            answer, returned_context, _reused = invoke(context)
            self._cache_event(
                kind, "error", key, record=lookup.record_identity,
                detail=f"warm-context: {type(exc).__name__}: {exc}")
            return answer, returned_context
        self._cache_event(
            kind, "hit" if reused else "miss", key,
            record=lookup.record_identity,
            detail="" if reused else "cached-context-rejected")
        return answer, returned_context

    def _common_payload(self):
        return {
            # Every job gets a detached wire image.  The handle itself retains only the
            # recursively immutable record derived from LoadedReference, so a caller
            # cannot splice different bytes between Layout, Select and Probe.
            "source": thaw_record_value(self._source),
            "contract": self._task.contract.as_dict(),
            "generation_spec": self._generation_spec.as_dict(),
            "target_artifact": self._target,
        }

    def catalogue_manifest(self):
        return self._layout.catalogue

    def goal_capability_use(self):
        """The source policy row that authorizes this exact provider and target set."""

        return self._goal_capability

    def cache_telemetry(self):
        """Detached hit/miss diagnostics, never consulted by generation policy."""

        return GenerationCacheTelemetry(tuple(self._cache_events))

    def search_refusal(self):
        """A closed worker cannot answer more goals in this query context.

        Ordinary UNKNOWN/UNSUPPORTED replies leave the worker alive. Only a
        fatal transport failure disables the context; RuntimeGuidance scopes
        this report to its recorded domain, not to other domains or artifacts.
        """
        if self._session_refusals:
            return self._session_refusals[0]
        if not self._searches:
            return None
        failure = self._searches[-1].failure
        if failure is None:
            return None
        if isinstance(failure, Refused):
            refusal = failure.refusal
        else:
            refusal = Refusal(
                failure.code or "behavior-generation-session-unavailable",
                "the isolated Behavior guidance session ended without an answer",
                failure=failure.how, detail=failure.detail,
                cleanup_notes=list(getattr(failure, "__notes__", ())))
        self._session_refusals.append(refusal)
        return refusal

    @contextmanager
    def search_scope(self, *, probe_budget):
        from .generation_transport import BehaviorSearch

        if probe_budget is not None and (type(probe_budget) is not int or probe_budget < 0):
            raise ValueError("a search Probe budget is a nonnegative integer")
        if self._searches:
            raise ValueError("a Behavior provider cannot run overlapping searches")
        search = BehaviorSearch(
            {**self._common_payload(), "targets": list(self.targetable_goals),
             "cached_context": (None if self._context_image is None else
                                base64.b64encode(self._context_image).decode("ascii"))},
            self._layout, timeout_s=self._timeout_s, probe_budget=probe_budget,
            preparation_plans=self._preparation_plans)
        self._session_timings.clear()
        self._session_refusals.clear()
        self._selected.clear()
        self._searches.append(search)
        try:
            yield
        finally:
            self.search_refusal()
            search.close()
            self._session_timings.extend(search.timings)
            self._searches.pop()
            self._selected.clear()
            self._prepared_traces.clear()

    def execution_cost(self):
        timings = self._searches[-1].timings if self._searches else self._session_timings
        return {"version": "behavior-generation-session-cost/1",
                "events": [dict(row) for row in timings],
                "constant_false_goals": list(self._layout.constant_false_goals)}

    def query_targets(self, targets, *, seed=None):
        """Remaining query work, not a smaller catalogue or observed hit set.

        These are the same literal-false predicates already omitted by Select.
        A caller with complete native feedback can omit the unused source/IR
        replay when no request could be built. Ordinary standalone Select still
        supplies its own audited observation. Atomic goals are not elided here.
        """
        selected = selection_targets(self.targetable_goals, targets)
        excluded = set(self._layout.constant_false_goals)
        return tuple(goal for goal in selected if goal not in excluded)

    def _checked_trace(self, seed, trace):
        if isinstance(trace, Refusal):
            return trace
        if type(trace) is not AuditedTrace:
            return Refusal(
                "behavior-generation-target-trace-not-audited",
                "the guidance provider requires one exact AuditedTrace")
        if trace.stimulus != seed or trace.artifact != self._target:
            return Refusal(
                "behavior-generation-target-trace-binding-mismatch",
                "the accepted trace names another stimulus or target artifact")
        return trace

    def _trace(self, seed):
        key = stimulus_bytes(seed)
        if key in self._prepared_traces:
            return self._prepared_traces.pop(key)
        try:
            trace = self._trace_of(seed)
        except Exception as exc:  # noqa: BLE001 - target execution capability
            return Refusal(
                "behavior-generation-target-trace-unavailable",
                "the read-only target replay service could not produce a trace",
                error=f"{type(exc).__name__}: {exc}")
        return self._checked_trace(seed, trace)

    def _trace_many(self, seeds):
        seeds = tuple(seeds)
        if self._traces_of is None:
            return tuple(self._trace(seed) for seed in seeds)
        try:
            traces = self._traces_of(seeds)
        except Exception as exc:  # noqa: BLE001 - target execution capability
            refusal = Refusal(
                "behavior-generation-target-trace-unavailable",
                "the read-only target replay service could not produce the trace batch",
                error=f"{type(exc).__name__}: {exc}")
            return tuple(refusal for _seed in seeds)
        if type(traces) not in (tuple, list) or len(traces) != len(seeds):
            refusal = Refusal(
                "behavior-generation-target-trace-batch-incomplete",
                "the read-only target replay service must answer every seed once")
            return tuple(refusal for _seed in seeds)
        return tuple(self._checked_trace(seed, trace)
                     for seed, trace in zip(seeds, traces))

    def _select_from_trace(self, seed, trace, targets):
        from ...worker import WorkerFailed
        from .generation_job import decode_selection_reply

        if isinstance(trace, Refusal):
            return trace
        try:
            if self._searches:
                got = self._searches[-1].select(seed, trace, targets)
            else:
                raw, _context = self._run_context_job(
                    "behavior_generation_select", {
                        **self._common_payload(), "seed": seed.as_dict(),
                        "trace": trace.as_dict(), "targets": list(targets),
                    },
                    GenerationCacheKey.select(
                        self._cache_binding, seed_bytes=stimulus_bytes(seed),
                        trace_identity=trace.fingerprint),
                    self._timeout_s,
                    label=f"Behavior guidance Select {seed.fingerprint[:12]}",
                    context=self._context_image)
                got = decode_selection_reply(raw)
        except Refused as exc:
            return exc.refusal
        except (WorkerFailed, TypeError, ValueError) as exc:
            return Refusal(
                str(getattr(exc, "code", "")
                    or "behavior-generation-select-unavailable"),
                "the isolated Behavior guidance Select did not return a valid answer",
                failure=str(getattr(exc, "how", type(exc).__name__)),
                detail=str(getattr(exc, "detail", exc)))
        if isinstance(got, Refusal):
            return got
        if got.catalogue_digest != self._layout.catalogue.digest():
            return Refusal(
                "behavior-generation-selection-catalogue-mismatch",
                "the Select worker rebuilt another source catalogue")
        self._selected[stimulus_bytes(seed)] = (got.requests, trace, targets)
        return got

    def prepare_seeds(self, seeds):
        """Share target replay without executing unused source Select jobs.

        The scheduler still selects one seed at a time, until its Probe budget is
        spent. Traces are consumed once; neither selections nor solver answers are
        inferred from this preparation.
        """
        seeds = tuple(seeds)
        if self._searches:
            self._searches[-1].seed_count = len(seeds)
        self._prepared_traces.clear()
        self._prepared_traces.update(
            (stimulus_bytes(seed), trace)
            for seed, trace in zip(seeds, self._trace_many(seeds)))

    def collect_preparation(self, seeds):
        """Observe all baseline writers once, without building their queries."""
        search = self._searches[-1]
        seeds = tuple(seeds)
        search.preparation_count = len(seeds) + 3  # Inspect, observe, export, then import.
        if search.prepare({'material': None}) is None:
            return None
        for seed, trace in zip(seeds, self._trace_many(seeds), strict=True):
            if isinstance(trace, Refusal):
                raise Refused(trace)
            search.prepare({'seed': seed.as_dict(), 'trace': trace.as_dict()})
        return search.prepare({'material': None})

    def use_preparation(self, material):
        if material is not None:
            search = self._searches[-1]
            if search.session is None:
                search.preparation_count += 1
            search.prepare({'material': material})

    def preparation_updates(self):
        """Export only new source-audited input donors, never states or hits."""
        return self._searches[-1].prepare({'updates': True})

    def select_many(self, seeds, *, targets=None):
        """Explicit bulk API; the budgeted scheduler uses lazy Select instead."""
        seeds = tuple(seeds)
        self.prepare_seeds(seeds)
        return tuple(self.select(seed, targets=targets) for seed in seeds)

    def select(self, seed, *, targets=None):
        return self._select_from_trace(
            seed, self._trace(seed), selection_targets(self.targetable_goals, targets))

    def probe(self, seed, request):
        from ...search_policy import remaining_solver_ms
        from ...generation_protocol import (QueryPlan)
        from ...worker import WorkerFailed
        from .generation_job import decode_probe_reply

        selected = self._selected.get(stimulus_bytes(seed))
        matches = (False if selected is None else
                   selected[0].selected(request) is not None if type(selected[0]) is QueryPlan else
                   any(request.is_restriction_of(item) for item in selected[0]))
        if not matches:
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED, probing.Scope.EXACT_LOCAL_HIT,
                detail="the request was not selected for this exact seed")
        _requests, trace, targets = selected
        try:
            if self._searches:
                return self._searches[-1].probe(
                    seed, trace, targets, request, remaining_solver_ms(self._timeout_ms))
            raw, _context = self._run_context_job(
                "behavior_generation_probe", {
                    **self._common_payload(), "seed": seed.as_dict(),
                    "trace": trace.as_dict(), "targets": list(targets),
                    "request": request.as_dict(),
                    "timeout_ms": remaining_solver_ms(self._timeout_ms),
                },
                GenerationCacheKey.probe(
                    self._cache_binding, seed_bytes=stimulus_bytes(seed),
                    trace_identity=trace.fingerprint,
                    request_bytes=request.identity_bytes),
                self._timeout_s,
                label=f"Behavior guidance Probe {request.identity[:12]}",
                context=self._context_image)
            return decode_probe_reply(
                raw,
                expected_catalogue_digest=self._layout.catalogue.digest())
        except Refused as exc:
            if self._searches and self.search_refusal() is not None:
                raise
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED, probing.Scope.EXACT_LOCAL_HIT,
                detail=f"{exc.refusal.kind}: {exc.refusal.why}")
        except (WorkerFailed, TypeError, ValueError) as exc:
            if self._searches:
                refusal = self.search_refusal()
                if refusal is not None:
                    raise Refused(refusal) from None
            return probing.ProbeResult(
                probing.Outcome.UNSUPPORTED, probing.Scope.EXACT_LOCAL_HIT,
                detail=(str(getattr(exc, "code", "")
                            or "behavior-generation-probe-unavailable")
                        + ": " + str(getattr(exc, "detail", exc))[:220]))
