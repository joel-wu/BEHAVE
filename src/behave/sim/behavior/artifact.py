"""Concrete replay of an admitted Behavior source.

The simulator command owns generation, comparison, persistence, and bounded analysis.
This module supplies only the backend-specific operation it needs: execute one frozen
Behavior source on one canonical stimulus and return the common :class:`AuditedTrace`.

``SlotRecord.accepted`` remains the input-acceptance ledger used by the score
denominator and by reference replay.  A Behavior step may emit zero, one, or several
transactions, so its logical output batches live in ``SlotRecord.outputs`` as
``endpoint -> tuple[payload, ...]``.  The task-aware matcher is the sole reader of that
projection; no ready/valid timing is invented for a transaction-level procedure.
"""

from __future__ import annotations

from ..core.admission import AdmittedTask
from ..core.catalogue import SAFETY, CatalogueManifest
from ..core.failure import (
    CandidateFault,
    FailureDisposition,
    FailureRaised,
    RunFailure,
)
from ..core.frozen import Frozen
from ..core.reference_memory import bind_external_memory
from ..core.refusal import Refusal, Refused
from ..core.stimulus import (
    AuditedTrace,
    Completion,
    SlotRecord,
    Stimulus,
    TraceDomainFields,
    trace_domain,
    uses_global_events,
)
from ..execution_control import check_execution
from ..stimulus_set import GenerationSpec
from .exact_service import BehaviorExactJobContext
from .isolated import Broken, IsolatedModel, Limits, Timeout, hits_of, is_model_exception
from .multiclock import DomainInvocation
from .task_boundary import _field_binding_name

__all__ = ["BehaviorArtifact"]


BEHAVIOR_TRANSACTION_ADAPTER = "behavior-transaction/11"
BEHAVIOR_ATOMIC_ADAPTER = "behavior-atomic/9"
BEHAVIOR_REPLAY_BUDGET_POLICY = "behavior-replay-budget/1"


def _input_projection(contract, process_inputs):
    """Bind task input fields to the already-judged ``process`` signature."""

    process_inputs = tuple(process_inputs)
    used = set()
    projections = []
    for endpoint in contract.inputs():
        fields = []
        for field in endpoint.fields:
            selected, candidates = _field_binding_name(field, process_inputs)
            if selected is None or selected in used:
                raise Refused(Refusal(
                    "behavior-concrete-input-binding-mismatch",
                    "the admitted task fields do not map uniquely to the frozen "
                    "Behavior process signature",
                    endpoint=endpoint.name, field=field.name,
                    candidates=list(candidates), process_inputs=list(process_inputs)))
            used.add(selected)
            fields.append(selected)
        projections.append((endpoint, tuple(fields)))
    if used != set(process_inputs):
        raise Refused(Refusal(
            "behavior-concrete-input-binding-mismatch",
            "the admitted input endpoints and frozen Behavior process signature "
            "name different values",
            missing=sorted(set(process_inputs) - used),
            extra=sorted(used - set(process_inputs))))
    return tuple(projections)


def _transactions(produced):
    if produced is None:
        return ()
    rows = produced if type(produced) in (tuple, list) else (produced,)
    if any(type(row) is not dict for row in rows):
        raise Refused(Refusal(
            "behavior-concrete-output-shape",
            "a Behavior step emits a mapping, a finite sequence of mappings, or "
            "no transaction",
            got=type(produced).__name__))
    return tuple(rows)


def _project_outputs(endpoints, produced):
    """Project complete logical result rows into endpoint-identified batches."""

    endpoints = tuple(endpoints)
    batches = {endpoint.name: [] for endpoint in endpoints}
    for index, row in enumerate(_transactions(produced)):
        if any(type(name) is not str or not name for name in row):
            raise Refused(Refusal(
                "behavior-concrete-output-shape",
                "a Behavior output row is a string-keyed logical valuation",
                transaction=index))
        used = set()
        projected = {}
        for endpoint in endpoints:
            payload = []
            for field in endpoint.fields:
                selected, candidates = _field_binding_name(field, row)
                if selected is None or selected in used:
                    raise Refused(Refusal(
                        "behavior-concrete-output-binding-mismatch",
                        "a Behavior result does not map uniquely to every declared "
                        "output field",
                        transaction=index, endpoint=endpoint.name,
                        field=field.name, candidates=list(candidates)))
                used.add(selected)
                payload.append(row[selected])
            projected[endpoint.name] = tuple(payload)
        if used != set(row):
            raise Refused(Refusal(
                "behavior-concrete-output-binding-mismatch",
                "a Behavior result contains values outside the admitted output "
                "endpoint product",
                transaction=index, extra=sorted(set(row) - used),
                missing=sorted(used - set(row))))
        for endpoint, payload in projected.items():
            batches[endpoint].append(payload)
    return {endpoint: tuple(rows) for endpoint, rows in batches.items() if rows}


class BehaviorArtifact(Frozen):
    """The private concrete runner retained by :class:`BehaviorHandle`."""

    __slots__ = (
        "_context", "_domain_fields", "_frontend_catalogue",
        "_generation_spec", "identity", "kind",
    )

    def __init__(self, task, context, generation_spec, frontend_catalogue):
        if type(task) is not AdmittedTask or task.realization.kind != "behavior":
            raise TypeError("a Behavior artifact needs an admitted Behavior task")
        if type(context) is not BehaviorExactJobContext or context.task is not task:
            raise TypeError("a Behavior artifact needs its frozen exact-job context")
        if generation_spec is not None and type(generation_spec) is not GenerationSpec:
            raise TypeError("generation_spec must be a GenerationSpec or absent")
        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError("a Behavior artifact needs its frozen frontend catalogue")
        self.kind = "behavior"
        self.identity = task.realization.identity
        self._context = context
        self._generation_spec = generation_spec
        self._frontend_catalogue = frontend_catalogue
        self._domain_fields = None if generation_spec is None else TraceDomainFields(
            generation_spec.horizon,
            generation_spec.reset_hold_cycles,
            generation_spec.queue_bound,
        )
        self._freeze()

    @property
    def frontend_catalogue(self):
        return self._frontend_catalogue

    def owns_atomic_request(self, request):
        """Whether ``request`` was frozen from this concrete runner's context."""

        from .atomic_analysis import BehaviorAtomicAnalysisRequest

        return (
            type(request) is BehaviorAtomicAnalysisRequest
            and request.context is self._context
            and request.generation_spec is self._generation_spec
            and request.frontend_catalogue is self._frontend_catalogue
        )

    def owns_analysis_request(self, request):
        """Whether ``request`` was frozen from this transaction-step runner."""

        from .transaction_analysis import BehaviorAnalysisRequest

        return (
            type(request) is BehaviorAnalysisRequest
            and request.context is self._context
            and request.generation_spec is self._generation_spec
            and request.frontend_catalogue is self._frontend_catalogue
        )

    def _model(self):
        timeout = self._context.timeout_s
        return IsolatedModel(
            self._context.source,
            file=self._context.file,
            cls=self._context.class_name,
            limits=Limits(seconds=timeout, wall_seconds=timeout),
            strict_module=True,
            source_rules=self._context.task.contract.source_rules,
            clock_domains=self._context.domains or None,
            # Replay consumes outputs and source hits, not a full state image
            # after every call. Explicit state/audit and final-memory reads
            # refresh through the same transport used by the reference.
            snapshot_policy="on-demand",
        )

    def _check(self, contract, stimulus):
        if contract.digest != self._context.task.contract.digest:
            raise Refused(Refusal(
                "behavior-concrete-contract-mismatch",
                "the Behavior runner and requested trace name different tasks"))
        if type(stimulus) is not Stimulus:
            raise TypeError("a Behavior concrete run needs an exact Stimulus")
        if (self._generation_spec is not None and stimulus.replay_policy is None
                and len(stimulus.slots) != self._generation_spec.horizon):
            raise Refused(Refusal(
                "behavior-concrete-horizon-mismatch",
                "the concrete stimulus has another horizon than the frozen run",
                stimulus=len(stimulus.slots),
                horizon=self._generation_spec.horizon))
        faults = stimulus.faults_against(contract)
        if faults:
            raise Refused(Refusal(
                "behavior-concrete-stimulus-outside-contract",
                "the Behavior runner cannot interpret this stimulus as a member of "
                "the admitted task",
                first=faults[0], count=len(faults)))

    def _hit_bits(self, reached):
        reached = set(reached)
        declared = set(self._frontend_catalogue)
        unknown = sorted(reached - declared)
        if unknown:
            raise Refused(Refusal(
                "behavior-concrete-hit-outside-catalogue",
                "the instrumented Behavior emitted a goal absent from its frozen "
                "frontend catalogue",
                goals=unknown))
        return {goal: goal in reached for goal in sorted(declared)}

    @staticmethod
    def _new_hits(model, before):
        current = hits_of(model)
        count = len(current)
        # The proxy appends validated replies; earlier events were checked when
        # consumed.  Copy and validate only this step, not the whole run again.
        delta = tuple(current[before:])
        if count < before or any(type(hit) is not str or not hit for hit in delta):
            raise Refused(Refusal(
                "behavior-concrete-hit-record-malformed",
                "the isolated Behavior returned no append-only string goal log"))
        return delta, count

    @staticmethod
    def _reset_hit_epoch(model):
        """Reset starts a new proxy log; reset-body hits are not step goals."""
        model.reset()
        _ignored, count = BehaviorArtifact._new_hits(model, 0)
        return count

    def _failure_goal(self, reached):
        """Return the single declared safety occurrence that ended this step."""

        # Validate the whole delta first.  A candidate-process reply cannot turn an
        # undeclared string into a semantic failure merely by placing it beside one
        # declared goal.
        self._hit_bits(reached)
        safety = tuple(
            goal for goal in reached
            if self._frontend_catalogue[goal].cls == SAFETY)
        return safety[0] if len(safety) == 1 else None

    def _padded_failure(self, records, stimulus, accepted, reached):
        """Record the failing slot, then terminal-stutter to the fixed horizon."""

        records.append(SlotRecord(
            accepted=accepted,
            hits=self._hit_bits(reached),
            monitor={"terminal_failure": 1},
        ))
        while len(records) < len(stimulus.slots):
            records.append(SlotRecord(
                hits=self._hit_bits(()), monitor={"padding": 1}))
        return records, Completion.PADDED

    def _atomic_failure_hits(self, error):
        from .atomic_failure import source_failure_evidence

        evidence = source_failure_evidence(error, self._frontend_catalogue)
        return None if evidence is None else evidence.hits

    def _ordinary_trace(self, contract, stimulus, model):
        from ..core.input_framing import InputDomainCursor

        input_domain = InputDomainCursor(contract)
        bind_external_memory(model, contract, stimulus)
        projections = _input_projection(contract, self._context.process_inputs)
        pointers = {endpoint.name: 0 for endpoint, _fields in projections}
        records = []
        hit_count = len(hits_of(model))
        global_events = uses_global_events(contract, stimulus)
        starts_reset = bool(stimulus.slots) and (
            bool(stimulus.slots[0].resets) if global_events else stimulus.slots[0].reset)
        if not starts_reset:
            hit_count = self._reset_hit_epoch(model)

        for position, slot in enumerate(stimulus.slots):
            check_execution()
            accepted = {}
            produced = None
            reset = bool(slot.resets) if global_events else slot.reset
            if reset:
                input_domain.advance(slot, {}, index=position)
                hit_count = self._reset_hit_epoch(model)
            elif global_events and not slot.ticks:
                pass  # A control-only event cannot invoke the untimed procedure.
            elif projections:
                present = []
                for endpoint, _fields in projections:
                    head = (stimulus.head(endpoint.name, pointers[endpoint.name])
                            if endpoint.name in slot.offers else None)
                    present.append((endpoint, head))
                active = [(endpoint, payload) for endpoint, payload in present
                          if payload is not None]
                if active and len(active) != len(projections):
                    raise Refused(Refusal(
                        "behavior-concrete-partial-input-batch",
                        "one Behavior process call denotes the complete same-slot "
                        "input endpoint product",
                        slot=position,
                        accepted=[endpoint.name for endpoint, _ in active],
                        missing=[endpoint.name for endpoint, payload in present
                                 if payload is None]))
                if active:
                    logical = {}
                    for (endpoint, names), (_same, payload) in zip(
                            projections, present):
                        if len(payload) != len(names):
                            raise Refused(Refusal(
                                "behavior-concrete-input-payload-arity",
                                "an input transaction has another field arity",
                                endpoint=endpoint.name, expected=len(names),
                                got=len(payload)))
                        accepted[endpoint.name] = payload
                        pointers[endpoint.name] += 1
                        logical.update(zip(names, payload))
                    input_domain.advance(slot, accepted, index=position)
                    try:
                        produced = model.process(**logical)
                    except Exception as exc:
                        if not is_model_exception(exc):
                            raise
                        reached, hit_count = self._new_hits(model, hit_count)
                        if self._failure_goal(reached) is None:
                            raise
                        return self._padded_failure(
                            records, stimulus, accepted, reached)
            else:
                try:
                    produced = model.process()
                except Exception as exc:
                    if not is_model_exception(exc):
                        raise
                    reached, hit_count = self._new_hits(model, hit_count)
                    if self._failure_goal(reached) is None:
                        raise
                    return self._padded_failure(
                        records, stimulus, accepted, reached)

            reached = ()
            if not reset:
                reached, hit_count = self._new_hits(model, hit_count)
            try:
                outputs = _project_outputs(contract.outputs(), produced)
            except Refused as exc:
                raise FailureRaised(RunFailure.candidate(
                    "candidate-output", exc.refusal)) from None
            records.append(SlotRecord(
                accepted=accepted,
                outputs=outputs,
                hits=self._hit_bits(reached),
            ))
        return records, Completion.NORMAL

    def _atomic_trace(self, contract, stimulus, model, runtime=None):
        from ..core.input_framing import InputDomainCursor

        input_domain = InputDomainCursor(contract)
        schedule = stimulus.clock_schedule
        if schedule is None and runtime is None:
            raise Refused(Refusal(
                "behavior-concrete-atomic-schedule-missing",
                "a multi-clock Behavior stimulus carries one global schedule"))
        domains = tuple(sorted(self._context.domains))
        model.atomic_init_from_source(domains)
        inputs = {
            domain: tuple(endpoint for endpoint in contract.inputs()
                          if endpoint.clock == domain)
            for domain in domains
        }
        outputs = {
            domain: tuple(endpoint for endpoint in contract.outputs()
                          if endpoint.clock == domain)
            for domain in domains
        }
        pointers = {endpoint.name: 0 for endpoint in contract.inputs()}
        emitted_counts = dict.fromkeys((endpoint.name for endpoint in contract.outputs()), 0)
        records = []

        def slots():
            if runtime is None:
                yield from zip(stimulus.slots, schedule.slots)
                return
            while True:
                pending = any(emitted_counts[name] < count
                              for name, count in runtime.expected_outputs.items())
                slot = runtime.slot(pointers, pointers, responses_pending=pending)
                if slot is None:
                    return
                yield slot, runtime.scheduled[-1]

        for position, (slot, event) in enumerate(slots()):
            check_execution()
            accepted = {}
            invocations = []
            ready_hits = []
            for domain in event.events:
                if domain in event.resets:
                    continue
                endpoints = inputs.get(domain, ())
                try:
                    ready, reached = model.atomic_ready_observation(domain)
                except CandidateFault as error:
                    reached = self._atomic_failure_hits(error)
                    if reached is None:
                        raise FailureRaised(RunFailure.candidate(
                            "candidate-run", error.refusal)) from None
                    return self._padded_failure(
                        records, stimulus, accepted,
                        tuple(ready_hits) + reached)
                ready_hits.extend(reached)
                if not endpoints:
                    if ready:
                        invocations.append(DomainInvocation(domain, ()))
                    continue
                payloads = tuple(
                    stimulus.head(endpoint.name, pointers[endpoint.name])
                    if endpoint.name in slot.offers else None
                    for endpoint in endpoints)
                present = tuple(payload is not None for payload in payloads)
                if any(present) and not all(present):
                    raise Refused(Refusal(
                        "behavior-concrete-partial-input-batch",
                        "one atomic domain handler denotes its complete same-slot "
                        "input endpoint product",
                        slot=position, domain=domain,
                        accepted=[endpoint.name for endpoint, yes
                                  in zip(endpoints, present) if yes],
                        missing=[endpoint.name for endpoint, yes
                                 in zip(endpoints, present) if not yes]))
                if ready and all(present):
                    values = []
                    for endpoint, payload in zip(endpoints, payloads):
                        accepted[endpoint.name] = payload
                        pointers[endpoint.name] += 1
                        values.extend(payload)
                    invocations.append(DomainInvocation(domain, values))
            input_domain.advance(slot, accepted, index=position)
            try:
                transition = model.atomic_step(event, tuple(invocations))
            except CandidateFault as error:
                reached = self._atomic_failure_hits(error)
                if reached is None:
                    raise FailureRaised(RunFailure.candidate(
                        "candidate-run", error.refusal)) from None
                return self._padded_failure(
                    records, stimulus, accepted,
                    tuple(ready_hits) + reached)
            emitted = {}
            for domain, rows in transition.outputs:
                try:
                    projected = _project_outputs(
                        outputs.get(domain, ()), tuple(dict(row) for row in rows))
                except Refused as exc:
                    raise FailureRaised(RunFailure.candidate(
                        "candidate-output", exc.refusal)) from None
                for endpoint, batch in projected.items():
                    if endpoint in emitted:
                        raise FailureRaised(RunFailure.candidate(
                            "candidate-output",
                            Refusal(
                                "behavior-concrete-output-endpoint-duplicate",
                                "one atomic slot emitted two batches for one endpoint",
                                slot=position, endpoint=endpoint)))
                    emitted[endpoint] = batch
                    emitted_counts[endpoint] += len(batch)
            records.append(SlotRecord(
                accepted=accepted,
                outputs=emitted,
                hits=self._hit_bits(tuple(ready_hits) + transition.hits),
            ))
        return records, Completion.NORMAL

    def trace(self, contract, stimulus):
        self._check(contract, stimulus)
        request = None
        runtime = None
        if stimulus.replay_policy is not None:
            from ..core.runtime_schedule import TransactionSchedule
            from ..runtime_execution import behavior_schedule, declared_completion_targets

            request = stimulus
            if len(contract.clocks) > 1:
                runtime = TransactionSchedule(
                    contract, request, request.replay_policy,
                    expected_outputs=declared_completion_targets(contract, request))
            else:
                stimulus = behavior_schedule(contract, request)
        try:
            model = self._model()
        except CandidateFault as exc:
            raise FailureRaised(RunFailure.candidate(
                "candidate-start", exc.refusal)) from None
        except Timeout as exc:
            raise FailureRaised(RunFailure.resource(
                "candidate-start",
                Refusal(
                    "behavior-artifact-timed-out",
                    "the Behavior candidate did not start inside its replay budget",
                    seconds=self._context.timeout_s,
                    error=type(exc).__name__),
                policy=BEHAVIOR_REPLAY_BUDGET_POLICY,
                disposition=FailureDisposition.SCORE_ZERO)) from None
        except Broken as exc:
            raise FailureRaised(RunFailure.infrastructure(
                "behavior-replay-service",
                Refusal(
                    "behavior-isolated-replay-failed",
                    "the isolated Behavior service did not start a valid replay",
                    code=exc.code or None,
                    message=str(exc),
                    error=type(exc).__name__))) from None
        try:
            try:
                records, completion = (
                    self._atomic_trace(contract, stimulus, model, runtime)
                    if len(self._context.domains) >= 2
                    else self._ordinary_trace(contract, stimulus, model))
                if runtime is not None:
                    if completion is Completion.PADDED:
                        # Failure is absorbing; do not invent a future schedule.
                        runtime.complete = True
                    stimulus = runtime.stimulus()
                if contract.memories:
                    from ..core.memory_result import (
                        model_memory_result,
                        require_memory_result_path,
                    )

                    require_memory_result_path(contract, stimulus)
                    final_memory = model_memory_result(model, contract)
                    if records:
                        last = records[-1]
                        records[-1] = SlotRecord(
                            accepted=last.accepted, state=last.state,
                            outputs=last.outputs, hits=last.hits, monitor=last.monitor,
                            environment={**dict(last.environment),
                                         "memory-result": final_memory.as_dict()})
            except Timeout:
                raise FailureRaised(RunFailure.resource(
                    "candidate-run",
                    Refusal(
                        "behavior-artifact-timed-out",
                        "the Behavior candidate did not finish inside its replay "
                        "budget",
                        seconds=self._context.timeout_s),
                    policy=BEHAVIOR_REPLAY_BUDGET_POLICY,
                    disposition=FailureDisposition.SCORE_ZERO)) from None
            except Broken as exc:
                raise FailureRaised(RunFailure.infrastructure(
                    "behavior-replay-service",
                    Refusal(
                        "behavior-isolated-replay-failed",
                        "the isolated Behavior service stopped before returning a "
                        "valid replay",
                        code=exc.code or None,
                        message=str(exc),
                        error=type(exc).__name__))) from None
            except CandidateFault as exc:
                raise FailureRaised(RunFailure.candidate(
                    "candidate-run", exc.refusal)) from None
            except Exception as exc:
                if not is_model_exception(exc):
                    raise
                raise FailureRaised(RunFailure.candidate(
                    "candidate-run", Refusal(
                        "candidate-runtime-failed",
                        "the isolated Behavior model raised during replay",
                        error=type(exc).__name__))) from None
        finally:
            model.close()
        adapter = (BEHAVIOR_ATOMIC_ADAPTER
                   if len(self._context.domains) >= 2
                   else BEHAVIOR_TRANSACTION_ADAPTER)
        fields = self._domain_fields
        if request is not None:
            fields = TraceDomainFields(
                len(stimulus.slots), request.replay_policy.reset_hold_cycles,
                max((len(rows) for _, rows in request.queues), default=1))
        domain = (fields.identity(contract.digest, adapter_version=adapter)
                  if fields is not None else trace_domain(
                      contract.digest, horizon=len(stimulus.slots), adapter_version=adapter))
        return AuditedTrace(
            stimulus,
            records=records,
            completion=completion,
            domain=domain,
            artifact=self.identity,
            request=request,
        )
