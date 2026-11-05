"""Shared worker-side construction of one frozen RTL source exact instance.

Local generation and post-score analysis are separate protocols, but both consume the
same immutable source image and contract.  This module owns that common decode/build
step so neither protocol imports the other.
"""

from __future__ import annotations

from functools import cached_property

from ..core.catalogue import CatalogueManifest
from ..core.contract import TaskContract
from ..core.refusal import Refusal, Refused
from .source_relation import SourceObservation

__all__ = ["PreparedSourceExact"]


def _decode_context(payload, extras):
    fields = {
        "relation_path", "artifact", "contract", "frontend_catalogue",
        "complete_catalogue", "queue_bound", "reset_hold_cycles",
        "clock_schedule", "memory_environment", "state_ownership", "observation", *extras,
    }
    if type(payload) is not dict or set(payload) != fields:
        raise TypeError("an isolated RTL exact job has its exact payload fields")
    if type(payload["relation_path"]) is not str or not payload["relation_path"] \
            or type(payload["artifact"]) is not str or not payload["artifact"]:
        raise TypeError("relation path and artifact identity are nonempty strings")
    if type(payload["queue_bound"]) is not int or payload["queue_bound"] < 1 \
            or type(payload["reset_hold_cycles"]) is not int \
            or payload["reset_hold_cycles"] < 0:
        raise TypeError("queue bound/reset hold are exact in-range integers")
    contract = TaskContract.from_dict(payload["contract"])
    frontend = CatalogueManifest.from_dict(payload["frontend_catalogue"])
    from .observation_binding import ObservationBinding

    if payload["observation"] is None:
        raise Refused(Refusal("rtl-exact-observation-unbound",
            "public RTL queries require the admitted source observation binding"))
    observation = ObservationBinding.from_dict(payload["observation"])
    observation.check(payload["artifact"], contract, frontend)
    complete = CatalogueManifest.from_dict(payload["complete_catalogue"])
    schedule = payload["clock_schedule"]
    if schedule is not None:
        from ..core.clock_schedule import GlobalSchedule

        schedule = GlobalSchedule.from_dict(schedule)
    memory_environment = payload["memory_environment"]
    if memory_environment is not None:
        from ..core.memory import MemoryEnvironment

        memory_environment = MemoryEnvironment.from_dict(memory_environment)
        faults = memory_environment.faults_against(
            contract.memories, len(schedule.slots) if schedule is not None else 0)
        # A non-atomic exact product obtains its horizon from the bind operation, so
        # shape is checked there.  Names and port schedules are still closed now.
        if schedule is not None and faults:
            raise Refused(Refusal(
                "rtl-exact-memory-environment-outside-contract",
                "the exact memory environment disagrees with its task or schedule",
                first=faults[0], count=len(faults)))
    ownership = payload["state_ownership"]
    if ownership is not None:
        from .state_ownership import RTLStateOwnership

        ownership = RTLStateOwnership.from_dict(ownership).validate_contract(contract)
    return (contract, frontend, complete, schedule, memory_environment,
            ownership, observation)


class PreparedSourceExact:
    """Worker-owned source construction; not yet whole-domain admission.

    This retains the complete compiler state and HDL startup, not a native seed
    or a local search cone. Query, monitor and certificate binding are separate
    obligations and cannot be inferred from a successful layout preparation.
    """

    def __init__(self, payload, extras=(), *, direct=False,
                 observation_scope=SourceObservation.PHASE_EVENTS):
        import hashlib
        import json
        from pathlib import Path

        from behave.isolation.preparation_control import check_preparation
        from ..core.catalogue import catalogue_subset
        from .elaboration import SOURCE_FILENAME, SOURCE_IMAGE_VERSION, elaboration_script
        from .source_goals import is_source_goal, manifest_of
        from .source_plan import plan_source
        from .source_relation import SourceIntervalTemplate
        from .source_scheduler import lower_source_scheduler

        if type(observation_scope) is not SourceObservation:
            raise TypeError('source construction needs a typed observation scope')
        self.observation_scope = observation_scope

        (self.contract, self.frontend, self.complete, self.schedule,
         self.memory_environment, ownership, self.observation) = _decode_context(
            payload, ("source_layout", "source_image_digest", *extras))
        if payload["source_layout"] is not True or ownership is not None:
            raise ValueError("source construction owns its layout, not a circuit ownership hint")
        body = Path(payload["relation_path"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != payload["source_image_digest"]:
            raise Refused(Refusal("rtl-exact-source-image-changed",
                "the source documents differ from their frozen compiler image"))
        image = json.loads(body)
        if type(image) is not dict or set(image) != {
                "version", "top", "source", "cst", "ast", "sensitivity", "state_ownership"} \
                or image["version"] != SOURCE_IMAGE_VERSION \
                or type(image["source"]) is not str:
            raise ValueError("source construction has a closed versioned compiler image")
        elaboration_script(image["top"])
        from .state_ownership import RTLStateOwnership

        self.ownership = (None if image['state_ownership'] is None else
            RTLStateOwnership.from_dict(image['state_ownership']).validate_contract(self.contract))
        if self.ownership is not None and self.ownership.top != image['top']:
            raise Refused(Refusal('rtl-exact-source-ownership-top-mismatch',
                'native reset ownership and source construction must name the same top'))
        if hashlib.sha256((image["top"] + "\0" + image["source"]).encode()).hexdigest() != payload["artifact"]:
            raise Refused(Refusal("rtl-exact-source-image-artifact-mismatch",
                "source construction needs the admitted artifact bytes and top"))
        check_preparation()
        plan = plan_source(image["source"], image["cst"], semantic_document=image["ast"],
            sensitivity_document=image["sensitivity"], filename=SOURCE_FILENAME,
            enumerate_iterations=False, source_rules=self.contract.source_rules)
        declared = catalogue_subset(self.frontend,
            {name for name, goal in self.frontend.items() if is_source_goal(goal)})
        if manifest_of(plan.declarations, filename=SOURCE_FILENAME) != declared \
                or not set(self.frontend) <= set(self.complete) \
                or catalogue_subset(self.complete, set(self.frontend)) != self.frontend:
            raise Refused(Refusal("rtl-exact-source-catalogue-mismatch",
                "source construction and native observation need the same frozen declarations"))
        self.machine = lower_source_scheduler(image["source"], image["ast"], plan,
            image["sensitivity"], filename=SOURCE_FILENAME, defer_domains=True, initialization=True,
            native_assignments=direct)
        if self.machine.event_bound is None:
            raise Refused(Refusal("rtl-exact-source-cyclic-domain-unbound",
                "source construction needs a proved finite event domain"))
        check_preparation()
        from .source_direct import NativeCoverSourceCompiler

        compiler = NativeCoverSourceCompiler(self.machine) if direct else None
        self.template = SourceIntervalTemplate(self.machine.procedure,
            {**self.machine.procedure.state_ranges, **self.machine.input_ranges}, direct=compiler)
        if compiler is not None:
            self.template = self.template.normalized()
        self.goal_names = tuple(declared)
        self.initial = self.template.initial_state(self.machine,
            dict.fromkeys(self.machine.program.fields), declarations=True, check=check_preparation)
        self.initial_layout, self.initial_binding = self.initial.request_layout(
            self.machine.program, payload["artifact"], check=check_preparation)
        # The native-cover constructor retains every private derived initial
        # bit and legal event order. Their independence is not a global
        # prerequisite: positive proposals must be confirmed by native replay.
        self.queue_bound = payload['queue_bound']
        self.reset_hold = payload['reset_hold_cycles']
        self.artifact = payload['artifact']
        self.image_digest = payload['source_image_digest']
        self._requests = {}
        self._query_requests = {}
        self._coordinates = {}
        self._native_views = None

    def _domain_query_view(self, proved, *, check):
        """Query-local equivalent syntax, after fresh proofs on this instance.

        Called only by Analysis immediately after its arbitrary-entry or
        complete reset-request domain proof. Only the same request's membership
        may reuse request-scoped equivalent syntax;
        native observation/audit keeps the original owner. No retained log,
        serialized certificate or sampled execution supplies exclusions.
        """
        from copy import copy
        from .source_direct import NativeCoverSourceCompiler
        from .source_domains import _elide_transition_domains
        from .source_relation import SourceIntervalTemplate

        if not proved:
            return self
        machine = _elide_transition_domains(self.machine, proved, check=check)
        view = copy(self)
        view.machine = machine
        view.template = SourceIntervalTemplate(machine.procedure,
            {**machine.procedure.state_ranges, **machine.input_ranges},
            direct=NativeCoverSourceCompiler(machine)).normalized()
        view._requests, view._query_requests, view._native_views = {}, {}, None
        view.__dict__.pop('relation', None)
        # Physical coordinates, initial support and the public request domain
        # are identical; query formulas/caches are exclusively this view's.
        return view

    @cached_property
    def relation(self):
        """Full observation is demand-driven, not a prerequisite to metadata."""
        return self.template.relation(self.machine, self.goal_names,
                                      complete_state=True, occurrences=True)

    def _native_interval(self, interval, *, member):
        """Reuse one observation owner's syntax, never its recorded history.

        The cover and chosen-order proposal share normalized transition
        formulas only. Changed observation/cone/context identity replaces the
        cache; concrete bindings, constraints and verdicts are not retained.
        """
        from dataclasses import replace

        from behave.isolation.preparation_control import check_preparation
        from .source_direct import NativeMemberSourceCompiler

        check_preparation()
        cached = self._native_views
        if cached is None or cached[0] is not interval:
            cover = replace(interval, source=interval.source.normalized(),
                            drive=interval.drive.normalized())
            proposal = None
        else:
            _, cover, proposal = cached
        if member and proposal is None:
            proposal = replace(cover, direct=NativeMemberSourceCompiler(self.machine))
        check_preparation()  # A cancelled view never replaces a complete one.
        self._native_views = interval, cover, proposal
        return proposal if member else cover

    def request_coordinates(self, horizon):
        """Freeze input/schedule coordinates without executing a symbolic history.

        Domain identity and input validation do not require an RTL unroll. The
        same coordinate owner is later consumed by every selected goal image
        and by the full native-observation image.
        """
        from behave.isolation.preparation_control import check_preparation
        from ..core.catalogue import catalogue_of, merge_catalogues
        from ..core.monitor import monitor_for
        from ..core.observation_product import TransactionProduct
        from .atomic_request import AtomicRequestCodec
        from .exact_product import needs_global_schedule_product
        from .protocol_relation import SymbolicRequest
        from .task_request import ReadyValidRequestCodec
        from .settled_request import SettledRequestCodec

        if type(horizon) is not int or horizon < 1:
            raise ValueError('a complete source request has a positive exact horizon')
        check_preparation()
        if horizon in self._coordinates:
            return self._coordinates[horizon]
        catalogue = merge_catalogues(self.frontend,
            catalogue_of(monitor_for(self.contract).goals(), allow_legacy=False),
            catalogue_of(TransactionProduct(self.contract).goals, allow_legacy=False))
        if catalogue != self.complete:
            raise Refused(Refusal('rtl-exact-source-task-catalogue-mismatch',
                'the source task product must consume the complete frozen catalogue'))
        atomic = needs_global_schedule_product(self.contract, self.schedule)
        if not self.contract.clocks:
            if self.schedule is not None or self.memory_environment is not None:
                raise Refused(Refusal('rtl-exact-settled-schedule-invalid',
                    'clockless source requests have no physical clock or memory schedule'))
            codec = SettledRequestCodec(self.contract, self.queue_bound, initial=self.initial_layout)
        elif atomic and self.schedule is None:
            raise Refused(Refusal('rtl-exact-source-schedule-unbound',
                'this endpoint product requires its declared global schedule'))
        else:
            codec = (AtomicRequestCodec(self.contract, self.queue_bound, self.schedule,
                    memory_environment=self.memory_environment, initial=self.initial_layout) if atomic else
                 ReadyValidRequestCodec(self.contract, self.queue_bound, self.reset_hold,
                    memory_environment=self.memory_environment, initial=self.initial_layout))
        request = SymbolicRequest(codec, horizon, check=check_preparation)
        check_preparation()
        self._coordinates[horizon] = request
        return request

    def monitor_layout(self, request):
        """Bind the same complete observation carrier without unrolling time."""
        from behave.isolation.preparation_control import check_preparation
        from ..core.reset_monitor import ResetRestorationConfig, leading_reset_edges, reset_state_domains
        from .atomic_monitors import AtomicMonitorProduct, protocol_monitor_components
        from .protocol_relation import ProtocolIntervalRelation
        from .source_handles import physical_name, state_paths
        from .source_initial import source_storage_leaves
        parts = ProtocolIntervalRelation(self.contract).parts
        _, protocol = protocol_monitor_components(self.contract,
            tuple((domain, *part) for domain, part in sorted(parts.items())), self.queue_bound)
        resets, widths, fields = [], {}, {}
        claimed = tuple(clock for clock in self.contract.clocks if clock.reset_restores is True)
        active_resets = reset_state_domains(self.contract,
            request.slots if request.atomic else None, global_events=request.atomic)
        if claimed:
            if active_resets and self.ownership is None:
                raise Refused(Refusal('rtl-exact-source-reset-state-unavailable',
                    'reset goals need the complete native state partition'))
            program = self.machine.program
            leaves = {native: (path, width) for native, path, width in
                      source_storage_leaves(program, check=check_preparation)}
            # The ownership frontend's `observable` flag describes top ports,
            # not the source observer's hierarchical storage capability. Reuse
            # native replay's compiler-declared array mapping, then require an
            # actual equal-width writable leaf; no spelling/width heuristic.
            native_paths = state_paths(() if self.ownership is None else self.ownership.names,
                {field.observation: program.kinds[name].observation
                 for name, field in program.fields.items()
                 if not field.input and field.observation is not None})
            owned_widths = {} if self.ownership is None else self.ownership.widths
            by_domain = {} if self.ownership is None else self.ownership.by_domain()
            selected = set()
            for clock in claimed:
                check_preparation()
                hold = (leading_reset_edges(request.slots, clock_domain=clock.name)
                        if request.atomic else self.reset_hold)
                # Keep the same automaton and both goal bits. Only its unused
                # comparison carrier disappears in a proved-inactive domain.
                names = by_domain.get(clock.name, ()) if clock.name in active_resets else ()
                if hold < 1:
                    raise Refused(Refusal('rtl-exact-source-reset-domain-unbound',
                        'reset goals need a leading reset and every owned state field', domain=clock.name))
                for name in names:
                    check_preparation()
                    width = owned_widths[name]
                    path = native_paths[name]
                    native = None if path is None else physical_name(path)
                    if (native not in leaves or leaves[native][1] != width
                            or self.initial_layout.widths.get(native) != width or native in selected):
                        raise Refused(Refusal('rtl-exact-source-reset-layout-mismatch',
                            'native reset state must map uniquely to equal-width source storage', field=name))
                    selected.add(native)
                    fields[name], widths[name] = leaves[native][0], width
                resets.append((clock.name, ResetRestorationConfig(hold, names)))
        return AtomicMonitorProduct(protocol, tuple(resets), widths), fields

    def release_query(self, horizon, encoding):
        """Retire only this disposable query image, never native evidence."""
        cached = self._query_requests.get(horizon)
        if cached is not None and cached[1][2] is encoding:
            del self._query_requests[horizon]

    def bind_request(self, horizon, *, goals=None, native_history=None, native_member=False,
                     native_cover_phases=(), native_phase_actions=(), fixed_rows=None):
        from behave.isolation.preparation_control import check_preparation
        from ..core.construction_checkpoint import finish_construction

        steps = self.bind_request_steps(horizon, goals=goals, native_history=native_history,
            native_member=native_member, native_cover_phases=native_cover_phases,
            native_phase_actions=native_phase_actions, fixed_rows=fixed_rows)
        try:
            return finish_construction(steps, check_preparation)
        finally:
            steps.close()

    def bind_request_steps(self, horizon, *, goals=None, native_history=None, native_member=False,
                           native_cover_phases=(), native_phase_actions=(), fixed_rows=None):
        """Compose a goal batch over the complete public request domain.

        Omitted ``goals`` retains the complete native observation image. A
        direct query may select a batch BEFORE history construction. Source
        dependencies are transitively closed; domain/fault owners and public
        coordinates remain unchanged. Unselected goals are absent, not false.
        Retain only the latest observation batch per horizon. A smaller batch
        is projected afresh so it does not inherit the old batch's datapath.
        Repeated queries within the same batch still share one encoding.
        """
        from behave.isolation.preparation_control import check_preparation

        from ..core.failure import RunFailure
        from ..core.observation_product import TransactionProduct
        from .source_task import SourceTaskRelation

        request = self.request_coordinates(horizon)
        if fixed_rows is not None and (native_history is not None or native_member
                                      or native_cover_phases or native_phase_actions):
            raise ValueError('fixed request construction is separate from native observation')
        fixed = None if fixed_rows is None else request.fixed(fixed_rows)
        if native_member and native_history is None:
            raise ValueError('a member-order proposal requires a complete native history')
        if (native_cover_phases or native_phase_actions) and not native_member:
            raise ValueError('local order repairs belong only to an existential native proposal')
        if native_history is not None and (goals is not None or self.template.direct is None):
            raise ValueError('native specialization belongs only to the complete cover membership check')
        if goals is not None:
            goals = frozenset(goals)
            if self.template.direct is None or not goals <= set(self.complete):
                raise ValueError('a projected query needs direct construction and declared goals')
        check_preparation()
        if fixed is None and native_history is None and goals is None and horizon in self._requests:
            return self._requests[horizon]
        if fixed is None and goals is not None and horizon in self._query_requests:
            selected, bound = self._query_requests[horizon]
            if goals == selected:
                return bound
            # Construction can be the peak-memory phase. The superseded
            # query is reproducible syntax, not evidence; drop the cache AND
            # this local reference before allocating its replacement.
            del self._query_requests[horizon]
            del bound
        transactions = TransactionProduct(self.contract)
        monitors, fields = self.monitor_layout(request)
        source_goals = self.goal_names
        if goals is not None:
            monitors = monitors.project(goals)
            fields = {name: fields[name] for name in monitors.state_widths}
            transactions = TransactionProduct(self.contract, goals=goals)
            source_goals = tuple(goal for goal in source_goals if goal in goals)
        relation = SourceTaskRelation(self.template, self.machine, self.contract,
            source_goals, complete_state=True, occurrences=goals is None,
            monitors=monitors, transactions=transactions, monitor_fields=fields,
            query_projection=goals is not None, observation_scope=self.observation_scope)
        if native_history is None:
            encoding = yield from relation.encode_request_steps(request if fixed is None else fixed,
                self.initial, self.initial_binding, check=check_preparation)
        elif type(native_history[1]) is RunFailure:
            # The same fixed-request view specializes positive and terminal
            # membership. No incomplete observation becomes a normal history:
            # the witness owner still validates the failure and actual prefix.
            if native_cover_phases or native_phase_actions:
                raise ValueError('native phase repairs require a complete observed history')
            if native_member:
                relation.source.relation = self._native_interval(relation.source.relation, member=True)
            encoding = yield from relation.encode_request_steps(request.fixed(native_history[0]),
                self.initial, self.initial_binding, check=check_preparation)
        else:
            from .observation_audit import NativeHistoryBindings

            rows, trace = native_history
            interval = relation.source.relation
            cover = self._native_interval(interval, member=False) if native_cover_phases else None
            phase_relations = {}
            if native_phase_actions:
                from dataclasses import replace
                from .source_direct import NativeMemberSourceCompiler
                for phase, actions in native_phase_actions:
                    if type(phase) is not int or phase <= 0 or phase in phase_relations:
                        raise ValueError('native action proposals name distinct non-startup cuts')
                    phase_relations[phase] = replace(self._native_interval(interval, member=True),
                        direct=NativeMemberSourceCompiler(self.machine, actions=actions))
            bindings = NativeHistoryBindings(self.machine, request, rows, trace, self.frontend,
                observation=self.observation, check=check_preparation,
                cover_relation=cover, cover_phases=native_cover_phases, phase_relations=phase_relations)
            relation.source.relation = self._native_interval(interval, member=native_member)
            encoding = yield from relation.encode_request_steps(request.fixed(rows), self.initial, self.initial_binding,
                                               check=check_preparation, native=bindings)
        if fixed is not None:
            from dataclasses import replace

            # This is an existential member of the ORIGINAL request, not a
            # shorter domain or a universal query restricted to sampled data.
            encoding = replace(encoding, constraints=(
                *request.constraints, *request.fix(fixed_rows), *encoding.constraints))
        expected = set(self.complete) if goals is None else goals
        if not expected <= set(encoding.hits) or not set(encoding.hits) <= set(self.complete):
            raise Refused(Refusal('rtl-exact-source-task-goal-unencoded',
                'every frozen goal needs its actual source or monitor predicate',
                missing=sorted(expected-set(encoding.hits)),
                extra=sorted(set(encoding.hits)-set(self.complete))))
        bound = relation, request, encoding
        if fixed is not None or native_history is not None:
            return bound
        if goals is None:
            self._requests[horizon] = bound
        else:
            self._query_requests[horizon] = goals, bound
        return bound

    def compare_request(self, request, *, check, observation_scope=SourceObservation.PHASE_EVENTS):
        """Pair an owned complete history with one fresh independent history.

        ``bind_request`` already built the unrestricted HDL initial support
        and whole protocol product. Keep that exact encoding, not an arbitrary
        caller-supplied formula or a native-state restriction. The right side
        still gets fresh private storage and scheduler choices, sharing only
        the public request coordinates. No admission result is reused here.
        """
        from .source_confluence import compare_histories
        check()
        for relation, owned, encoding in self._requests.values():
            if request is owned:
                relation.require_observation_scope(observation_scope)
                right = relation.bind_native_request(request, check=check,
                                                     tag='source_request_orders_right')
                check()
                return compare_histories(encoding, right,
                    tuple(self.machine.program.fields), check=check, observation_scope=observation_scope)
        raise ValueError('source comparison requires this instance\'s bound request')

    def trace_domain(self, horizon, *, observation_scope=SourceObservation.PHASE_EVENTS):
        """Describe the full constructor/request domain, never a baseline cone."""
        from ..core.domain import TraceDomain
        from .source_task import TERMINAL_ENVIRONMENT_FAULT
        if type(observation_scope) is not SourceObservation:
            raise TypeError('an exact source domain needs a named observation scope')
        if (self.observation_scope is SourceObservation.GOAL_PREFIX
                and observation_scope is not SourceObservation.GOAL_PREFIX):
            raise ValueError('a goal-prefix constructor cannot declare a phase-event domain')

        request = self.request_coordinates(horizon)
        return TraceDomain(horizon, contract=dict(request.codec.bounds),
            initial={'kind': 'pre-hdl-construction',
                     'registers': dict(self.initial_layout.widths),
                     'pins': dict(self.initial_layout.startup_pins),
                     'private_storage': 'hdl-support-independent-of-request',
                     'reset_hold_cycles': None if request.atomic or request.settled else self.reset_hold,
                     'reset_prefix_inside_horizon': not request.settled},
            schema={'kind': 'source-task-history', 'queue_bound': self.queue_bound,
                    'task_contract': self.contract.as_dict(),
                    'clock_schedule': None if self.schedule is None else self.schedule.as_dict(),
                    'memory_environment': (None if self.memory_environment is None
                                           else self.memory_environment.as_dict()),
                    'source_image': self.image_digest,
                    'observation': self.observation.identity,
                    'catalogue': self.complete.digest(),
                    'events': 'startup-control-clock-return',
                    'source_query_semantics': ('native-cover/1' if self.template.direct is not None
                                               else 'order-equivalent/1'),
                    'source_value_semantics': ('native-variable-literals/1' if self.template.direct is not None
                                               else 'defined-source/1'),
                    'source_observation_scope': observation_scope.value,
                    'witness_initial_state': 'after-hdl-startup-with-separate-initial-hits',
                    'outputs': 'endpoint-tagged-transactions',
                    'excluded_environment_faults': [TERMINAL_ENVIRONMENT_FAULT],
                    'terminal': 'input-completion-with-explicit-environment-and-unresolved-images'},
            frontend=('rtl-source-task-native-cover/2' if self.template.direct is not None
                      else 'rtl-source-task-exact/2'), policy='whole-source-request/1',
            artifact=self.artifact, task_contract=self.contract.digest)
