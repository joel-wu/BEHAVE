"""Atomic source guidance through the common Behavior Select/Probe protocol.

Layout is schedule-independent. Selection binds the real replay schedule and
memory image; Probe uses one audited global boundary, not a whole-run proof.
"""

from ...behavior.atomic_exact import _atomic_signatures, _row_name, prepare_atomic_context
from ...behavior.atomic_record import atomic_rows_from_records
from ...behavior.atomic_selection import AtomicSelectedTrace
from ...behavior.multiclock import ownership_from_source
from ...behavior.source_plan import plan_source
from ...core.record_value import canonical_json
from ...core.refusal import Refusal, Refused
from ...core.stimulus import AuditedTrace, Completion, Stimulus
from ...generation_protocol import QueryTemplate, ReplayFeedback, StimulusField
from ...generation_trace import queue_release_slots
from .generation import _fixed_inputs
from .generation_job import SelectedTrace


class AtomicGuidance:
    """Worker-owned source plan and at most one schedule-bound preparation."""

    def __init__(self, source, file, contract, cls, identity, bound):
        model, _ownership = ownership_from_source(source, file=file, cls=cls, contract=contract)
        self.source, self.file, self.contract, self.cls = source, file, contract, model.cls.name
        self.identity, self.bound = identity, bound
        self.manifest = plan_source(model, source, file,
                                    source_rules=contract.source_rules).manifest
        boundaries = _atomic_signatures(source, file, contract, model)
        self.input_endpoints = tuple(
            (item.endpoint, tuple((public, _row_name(domain, logical))
                                  for public, logical in item.fields))
            for domain, boundary in sorted(boundaries.items()) for item in boundary.inputs)
        self._key = self._prepared = None

    def frontend_catalogue_manifest(self):
        return self.manifest

    def prepare(self, seed):
        schedule = seed.clock_schedule
        if schedule is None:
            raise Refused(Refusal("behavior-generation-atomic-schedule-missing",
                                 "atomic guidance needs the replay's global clock schedule"))
        # These public records intentionally do not use structural __eq__.
        # Compare their declared contents once, not Python object identities
        # reconstructed by each session request.
        key = (schedule.as_dict(), None if seed.memory_environment is None else
               seed.memory_environment.as_dict())
        if self._key != key:
            self._prepared = None
            self._key = None
            prepared = prepare_atomic_context(
                self.source, self.file, self.contract, schedule, cls=self.cls,
                memory_environment=seed.memory_environment,
                memory_horizon=(len(schedule.slots) if seed.memory_environment is not None else None))
            self.manifest.reconcile(prepared.frontend_catalogue_manifest(),
                                    "behavior-atomic-generation")
            self._prepared, self._key = prepared, key
        return self._prepared


class AtomicGenerationSelection(SelectedTrace):
    """Only the boundary/encoding differs; selection and wire answers are shared."""

    def __init__(self, built, seed, trace, targets, *, verify_feedback=True, ruled_out=None):
        import hashlib

        contract, _spec, target, _reference, guidance, manifest, available, _boundary = built
        self.seed = Stimulus.from_dict(seed)
        trace = AuditedTrace.from_dict(trace)
        if trace.artifact != target:
            raise Refused(Refusal("behavior-generation-source-trace-wrong-artifact",
                                 "the accepted trace belongs to another target artifact"))
        if trace.stimulus != self.seed:
            raise Refused(Refusal("behavior-generation-source-trace-wrong-seed",
                                 "the accepted trace belongs to another seed"))
        if trace.completion is not Completion.NORMAL or len(trace.records) != len(self.seed.slots):
            raise Refused(Refusal("behavior-generation-source-trace-incomplete",
                                 "selection needs a complete target trace"))
        context = guidance.prepare(self.seed)
        self.atomic = AtomicSelectedTrace(context, atomic_rows_from_records(context, trace.records),
                                          verify_feedback=verify_feedback)
        self.instance, self.manifest, self.available = context, manifest, available
        self.input_domains = {
            _row_name(domain, name): kind
            for domain, component in context.components.items()
            for name, kind in component.proc.semantics.input_domains}
        self.ruled_out = ()
        releases = queue_release_slots(contract, self.seed, (record.accepted for record in trace.records))
        fields = dict(guidance.input_endpoints)
        counters = dict.fromkeys(fields, 0)
        self.occurrences = {}
        self.prefixes = {}
        # State-preparation recipes, prefix/path queries and observed write
        # locations belong to one procedure's transaction windows; an atomic
        # product has none of them.
        self._setup_proposals, self._write_locations = {}, {}
        self.preparation_recipes = self._preparation_binding = None
        self._prefix_query = self._path_query = None
        for position, prefix in enumerate(self.atomic.prefixes):
            bindings, inputs = [], {}
            for endpoint, payload in trace.records[position].accepted:
                if endpoint not in fields:
                    continue
                index = counters[endpoint]
                if index >= self.seed.depth(endpoint) or len(payload) != len(fields[endpoint]):
                    raise Refused(Refusal("behavior-generation-source-map-outside-seed",
                                         "the accepted input is outside its endpoint queue"))
                bindings.extend((logical, StimulusField(endpoint, index, field, releases[endpoint, index]))
                                for field, (_public, logical) in enumerate(fields[endpoint]))
                inputs.update((logical, value) for (_public, logical), value
                              in zip(fields[endpoint], payload, strict=True))
                counters[endpoint] += 1
            if not bindings:
                continue
            key = f"{guidance.identity}:{self.seed.fingerprint}@{position}"
            self.occurrences[key] = {"id": key, "rank": position, "position": position,
                                     "bindings": tuple(bindings), "hits": tuple(sorted(prefix.hits)),
                                     "input": inputs}
        # Feedback includes silent/reset/clock-only slots too, even though these
        # cannot propose a mutation of an accepted input in this local scope.
        self.replay = None
        if verify_feedback:
            image = {"seed": self.seed.fingerprint, "catalogue": manifest.digest(),
                     "rows": self.atomic.rows, "hits": sorted(self.atomic.hits)}
            self.replay = ReplayFeedback(self.seed.fingerprint, manifest.digest(),
                hashlib.sha256(canonical_json(image).encode()).hexdigest(), self.atomic.hits)
        self.targets = None
        self.select(targets)

    def _equivalent_requests(self):
        # Atomic queries include a physical prefix/schedule. Ordinary Behavior's
        # one-call state/history equality does not establish their equivalence.
        return ()

    def _context_keys(self, requests):
        # Entry-context hints summarize one procedure's native states and control
        # inputs. An atomic product has no single procedure, and its physical
        # prefix is part of every query, so no cross-stimulus hint applies.
        return {}

    def _build_requests(self):
        # Physical multi-clock prefixes use AtomicSelectedTrace, not the
        # ordinary Behavior transaction-window construction owner.
        from .generation_job import _requests

        return _requests(self.occurrences.values(), self.available, self.manifest,
                         self.ruled_out, self.input_domains, factory=QueryTemplate)

    def local_hit(self, occurrence, request, timeout_ms):
        fixed = _fixed_inputs(occurrence["bindings"], request.mutable_fields, occurrence["input"])
        return self.atomic.local_hit(occurrence["position"], request.targets,
                                     timeout_ms=timeout_ms, fixed_inputs=fixed)
