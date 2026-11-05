"""Worker-owned source queries over recorded native boundaries, never a scorer."""

import time

from ...symbolic.validation import EncodingError, _procedure_validation
from ...behavior.goal_projection import literal_false_goals
from ...core.catalogue import CatalogueManifest, catalogue_subset
from ...core.contract import TaskContract
from ...core.record_value import canonical_json
from ...core.refusal import Refusal, Refused
from ...core.stimulus import AuditedTrace, SlotRecord, Stimulus
from ...generation_protocol import ProbeRequest
from ...job_session import serve as serve_session
from ...job_session import stdio
from ...rtl.elaboration import SOURCE_FILENAME
from ...rtl.source_generation import VERSION
from ...rtl.source_goals import is_source_goal, manifest_of
from ...rtl.source_plan import plan_source
from ...rtl.source_relation import SourceIntervalTemplate
from ...rtl.source_scheduler import lower_source_scheduler
from .. import probing
from .generation_job import encode_probe_reply
from .source_boundary import SourceTraceBindings
from .source_query import query_source_interval


def _constant_false_source_goals(machine):
    """Project validated, literal-blocked IR occurrences to source goal IDs."""
    blocked = literal_false_goals(machine.procedure)
    declared = {goal for _, _, goal in machine.occurrences}
    possible = {goal for mark, _, goal in machine.occurrences if mark not in blocked}
    return tuple(sorted(declared - possible))


class PreparedSourceGeneration:
    def __init__(self, context):
        self.contract = TaskContract.from_dict(context["contract"])
        self.catalogue = CatalogueManifest.from_dict(context["catalogue"])
        source, ast, sensitivity = (context[name] for name in ("source", "ast", "sensitivity"))
        plan = plan_source(source, context["cst"], semantic_document=ast,
            sensitivity_document=sensitivity, filename=SOURCE_FILENAME, enumerate_iterations=False,
            source_rules=self.contract.source_rules)
        declared = catalogue_subset(self.catalogue, {
            name for name, goal in self.catalogue.items() if is_source_goal(goal)})
        if manifest_of(plan.declarations, filename=SOURCE_FILENAME) != declared:
            raise Refused(Refusal("rtl-source-query-catalogue-mismatch",
                "source queries must use the same declared occurrences as native observation"))
        self.machine = lower_source_scheduler(source, ast, plan, sensitivity,
            filename=SOURCE_FILENAME, defer_domains=True)
        if self.machine.event_bound is None:
            raise Refused(Refusal("rtl-source-query-cyclic-domain-unbound",
                "cyclic event dependencies need an explicit finite local query domain"))
        # RTL storage already has compiler-declared finite carriers. A local
        # query binds its recorded state, not an inferred reachable-state hull.
        # StepTemplate plans unrestricted carrier values and widens successor
        # carriers to closure; it never assumes these intervals as invariants.
        self.env = {**self.machine.procedure.state_ranges, **self.machine.input_ranges}
        # Literal-control facts need validated IR, not a complete transition
        # formula. Publish them before optional, expensive query construction.
        self._validation = _procedure_validation(self.machine.procedure, compact_arrays=True)
        self.template = None
        self._protocol_template = None
        # One public source site can have several lowered occurrences (for
        # example, an inlined helper). Omit a local question only when every
        # occurrence is blocked by literal IR control, not after local UNSAT.
        self.constant_false_goals = _constant_false_source_goals(self.machine)
        self.binding = None
        self._task_progress = {}
        self._interval_progress = None

    def prepare(self):
        from ...rtl.source_direct import NativeCoverSourceCompiler

        self._interval_progress = None
        self._protocol_template = None
        try:
            template = SourceIntervalTemplate(self.machine.procedure, self.env,
                                              _validation=self._validation)
            try:
                template.direct = NativeCoverSourceCompiler(self.machine)
            except Refused as exc:
                if exc.refusal.kind != 'rtl-direct-event-regions-unsupported':
                    raise
            # Native-cover Analysis and Search share source compilation. Its
            # uncertified event orders retain the existing scheduler fallback.
            # A local SAT remains a proposal until ordinary native replay.
            self.template = template.normalized()
        except EncodingError as exc:
            raise Refused(Refusal("rtl-source-query-encoding-unsupported",
                "the source transition cannot be represented under the declared encoding limits",
                encoding_kind=exc.kind, detail=str(exc))) from exc

    def bind(self, payload):
        self.binding = None
        self._task_progress = {}
        self._interval_progress = None
        seed = Stimulus.from_dict(payload["seed"])
        # These are query inputs supplied by the parent's execution owner, not
        # authenticated native evidence and not usable to award a score or hit.
        trace = AuditedTrace(seed, initial_state=payload["initial_state"],
            query_boundaries=payload.get("query_boundaries"),
            records=tuple(SlotRecord.from_dict(row) for row in payload["records"]))
        self.binding = SourceTraceBindings(self.machine, self.contract, seed, trace)
        return {"seed": seed.fingerprint}

    def probe(self, payload):
        started = time.perf_counter()
        request = ProbeRequest.from_dict(payload["request"])
        if request.catalogue_digest != self.catalogue.digest():
            raise ValueError("source query requested a different catalogue")
        previous, self._interval_progress = self._interval_progress, None
        if payload.get('segment') is not None:
            from .generation import RTLScheduleSegment
            from .source_task_query import SourceTaskQuery, query_source_task

            segment = RTLScheduleSegment.from_dict(payload['segment'])
            protocol = any(not is_source_goal(self.catalogue[goal]) for goal in request.targets)
            joint = len({(f.endpoint, f.transaction) for f in request.mutable_fields}) > 1
            if (segment.stop > segment.start+1 and (self.contract.clocks or joint) or protocol):
                # Protocol samples and source events have distinct observation
                # roots. Their alternating queries must not evict one another's
                # compiled cone. Both owners share the same validated source IR;
                # neither stores a replay, solver answer or seed-specific value.
                template = self.template
                if protocol:
                    if self._protocol_template is None:
                        self._protocol_template = self.template.normalized()
                    template = self._protocol_template
                # Keep each widening scope's latest question, not a live arena
                # per seed/goal/request. Rebinding any seed drops every owner.
                scope = protocol, tuple((field.endpoint, field.field) for field in request.mutable_fields)
                owner = self._task_progress.get(scope)
                if owner is None or not owner.matches(self.machine, template, self.binding, request, segment):
                    owner = SourceTaskQuery(self.machine, template, self.binding, request, segment)
                    self._task_progress[scope] = owner
                answer = query_source_task(self.machine, template, self.binding, request, segment,
                                           timeout_ms=payload['timeout_ms'], _construction=owner)
                if answer.outcome not in (probing.Outcome.CONSTRUCTING, probing.Outcome.UNKNOWN):
                    self._task_progress.pop(scope, None)
                return encode_probe_reply(answer, self.catalogue.digest())
        try:
            selection = request.identity_bytes, canonical_json(payload.get('segment'))
            if previous is not None and previous[0] == selection:
                _, relation, query = previous
            else:
                relation = self.template.relation(self.machine, request.targets)
                query = self.binding.at(request.mutable_fields, state_names=relation.source.state_names)
        except Refused as exc:
            answer = probing.ProbeResult(probing.Outcome.UNSUPPORTED, probing.Scope.RTL_LOCAL_HIT,
                detail=f"{exc.refusal.kind}: {exc.refusal.why}")
        except (EncodingError, KeyError, TypeError, ValueError) as exc:
            answer = probing.ProbeResult(probing.Outcome.UNSUPPORTED, probing.Scope.RTL_LOCAL_HIT,
                detail=str(exc))
        else:
            remaining = int(payload["timeout_ms"] - (time.perf_counter() - started) * 1000)
            if remaining <= 0:
                # The relation and native boundary are complete, but no solver
                # has run. Resume this exact question under the scheduler's
                # existing shared budget instead of discarding its construction.
                # Rebinding/prepare clears it; every solver answer is terminal.
                self._interval_progress = selection, relation, query
                answer = probing.ProbeResult(probing.Outcome.CONSTRUCTING, probing.Scope.RTL_LOCAL_HIT,
                    detail="source relation and boundary prepared; query awaits its next budget slice")
            else:
                answer = query_source_interval(self.machine, self.env, query.state, query.phases[0],
                    request.targets, mutable_inputs=query.mutable_inputs,
                    following_inputs=query.phases[1:], event_bound=self.machine.event_bound,
                    timeout_ms=remaining, _relation=relation)
            if answer.outcome is probing.Outcome.SAT:
                answer = probing.ProbeResult(answer.outcome, answer.scope, goal=answer.goal,
                    candidate=query.proposal(answer.candidate), detail=answer.detail,
                    solve_ms=answer.solve_ms)
        answer.solve_ms = (time.perf_counter() - started) * 1000
        return encode_probe_reply(answer, self.catalogue.digest())


def serve(reader, writer):
    prepared = None

    def dispatch(op, payload):
        nonlocal prepared
        if op == "open":
            prepared = PreparedSourceGeneration(payload)
            return {"catalogue_digest": prepared.catalogue.digest(),
                    "event_bound": prepared.machine.event_bound,
                    "constant_false_goals": list(prepared.constant_false_goals)}
        if op == "bind":
            return prepared.bind(payload)
        if op == "prepare":
            prepared.prepare()
            return {}
        if op == "probe":
            return prepared.probe(payload)
        raise ValueError("invalid source query operation")

    serve_session(reader, writer, version=VERSION, dispatch=dispatch)


if __name__ == "__main__":
    stdio(serve)
