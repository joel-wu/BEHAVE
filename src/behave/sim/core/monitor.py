"""The obligations a run owes the protocol, checked against the ledger of what it did.

TODO3 T3-02. Two rewrites in, and the second one is about where the facts come from. The
first version read signals inside each obligation's own branch, so the five obligations
had five readings of one run and disagreed about basic things -- whether the sink offered
at slot 7 depended on which check was asking. This one judges nothing it did not read
from `core/ledger.py`, which turns the run into per-slot protocol facts once.

What that reorganisation let be fixed, each of which had produced a wrong verdict:

**A deadline is measured to the first offer, not to the transfer.** The design controls
when it presents an answer; the environment controls when it takes one. Counting to the
transfer charges the design for slots in which the testbench held `ready` low, so a
correct design under back-pressure was reported as missing its deadline. Counting only
the slots where the environment happened to be ready -- the previous repair -- is a
different obligation again, and one no task declares.

**A broken deadline does not retire the transaction.** It used to `waiting.pop(0)`, so
the answer that arrived late was matched against the *next* transaction, and the run
finished with a phantom unanswered one at the end. One fault, reported as two, in the
wrong places.

**Only the oldest was ever timed.** With three in flight, the second and third accrued
nothing until the first retired, so a design that answered the first promptly and the
rest never scored one violation.

**The end of a horizon is not evidence of a missing response.** A stimulus stops after a
caller's `drain` count. Concluding from that that the design owes an answer reports the
horizon as a defect in the design. A missing response is only a finding where the design
was **observed idle** with the environment receptive and still owing something.

**One answer per request is a declaration, not a default.** Retiring one pending
transaction per output transfer is a guess about the protocol; this population has 16
designs whose outputs outnumber their inputs. Every obligation that has to know when a
transaction stops being outstanding now requires the task to declare the correlation, and
is otherwise reported unchecked.

**An X is not a value.** Two unreadable payloads compared equal, so a design driving X
under stall passed the stability check on the strength of two absences. Unreadable slots
produce an `Unresolved`, which is neither a pass nor a violation.

**A violation, an unresolved and an unchecked obligation are three different facts.** A
run that broke an obligation, a run that could not be judged on it, and a task that never
declared it all used to arrive as "not in the violation list".
"""

from __future__ import annotations

from collections import Counter
from typing import ClassVar

from .catalogue import (
    SAFETY,
    Goal,
)
from .contract import READY_VALID, SAMPLED_EDGE, SETTLED, Kind, Matching, ResetPolicy
from .frozen import Frozen, FrozenMap
from .input_queue import endpoint_is_active, input_completion_environment
from .ledger import ledger_of
from .ordered_monitor import OrderedConcreteMonitor
from .protocol import APB_ACCESS, ARTIFACT, AXI_LITE_READ, AXI_LITE_WRITE, adapter_program_for
from .ready_valid_product import (
    OrderedReadyValidConfig,
    OrderedReadyValidEvent,
)
from .refusal import Refusal, Refused
from .reset_monitor import ResetRestorationProvider

__all__ = [
    "INPUT_COMPLETION",
    "Monitor",
    "MonitorHit",
    "MonitorProduct",
    "MonitorReport",
    "Unresolved",
    "Violation",
    "environment_assumption_violations",
    "monitor_for",
]


INPUT_COMPLETION = "monitor.input-completion"


def _payload_independent_goals(monitor):
    """Count/timing obligations determined by plain sampling and its schedule.

    This projects only protocol obligations, never reset restoration or source
    state. Both settled and clocked plain sampling have no artifact-controlled
    handshake. Ordered matching depends on transfer counts, not payload values;
    fixed reset levels, active edges and releases retain those counts locally.
    """
    endpoints = monitor.contract.endpoints
    if not endpoints or any(
            endpoint.adapter.name not in (SETTLED, SAMPLED_EDGE)
            or endpoint.adapter.version != 1
            or endpoint.adapter.environment_controls
            or endpoint.adapter.artifact_controls
            or endpoint.kind is not Kind.TRANSACTION
            or endpoint.matching is not Matching.ORDERED
            for endpoint in endpoints):
        return frozenset()
    goals = {name for name, goal in monitor.goals().items() if goal.provider == "monitor"}
    return (frozenset(goals) if goals <= set(Monitor.OBLIGATIONS) - {"monitor.sink-stability"}
            else frozenset())


def monitor_for(contract):
    """The concrete protocol monitor for the contract's clock topology.

    The historical :class:`Monitor` is kept as the exact one-clock component.  A
    several-clock task composes one such component per domain and projects only that
    domain's active edges into it; no inactive edge is mistaken for a transfer and no
    coincident edge is serialized through another domain's state.
    """

    from .contract import TaskContract

    if type(contract) is not TaskContract:
        raise TypeError("a protocol monitor needs the exact TaskContract")
    return Monitor(contract) if len(contract.clocks) <= 1 else MonitorProduct(contract)


def environment_assumption_violations(contract, stimulus, trace):
    """Environment-owned adapter failures that put this trace outside its domain."""

    return monitor_for(contract).check(stimulus, trace).assumption_violations


def _ordered_channel_goals_have_exact_product(monitor):
    """Whether the global-schedule product encodes every goal of ``monitor``.

    A unique input/output pair additionally has exact correlation state.  With several
    independent endpoints no pairing is inferred; the finite product still carries one
    stalled-output state per sink, so endpoint-local sink stability remains exact.
    """

    contract = monitor.contract
    endpoints = contract.endpoints
    if any(endpoint.kind is not Kind.TRANSACTION
           or endpoint.adapter.name not in (READY_VALID, SAMPLED_EDGE, SETTLED, APB_ACCESS, AXI_LITE_READ, AXI_LITE_WRITE)
           or endpoint.adapter.version != 1
           or endpoint.matching not in (Matching.ORDERED, Matching.IDENTIFIER)
           for endpoint in endpoints):
        return False
    inputs, outputs = contract.inputs(), contract.outputs()
    if len(inputs) == 1 and len(outputs) == 1:
        return monitor.finite_channel_config(1) is not None
    return (set(monitor.goals()) - set(monitor.reset_restoration.goals())) <= {
        "monitor.sink-stability",
        INPUT_COMPLETION,
    }


def _protocol_goals_have_exact_product(monitor):
    """Whether one registered finite protocol product covers every non-reset goal."""

    if monitor._interfaces:
        return all(_protocol_goals_have_exact_product(part) for _, part in monitor._interfaces)
    reset_goals = set(monitor.reset_restoration.goals())
    protocol_goals = set(monitor.goals()) - reset_goals
    # Completion is a terminal predicate over the driver's accepted cursor.  The RTL
    # exact wrappers add it at the schedule boundary rather than asking an adapter's
    # per-slot control table to own it.
    table_goals = protocol_goals - {INPUT_COMPLETION}
    program = adapter_program_for(monitor.contract)
    exact_goals = set(program.monitor_goals_owned_by(
        monitor.contract, ARTIFACT))
    table_owned = (
        program.exact_control_product is True
        and table_goals <= exact_goals
    )
    shared_owned = (
        program.exact_transition_policy is not None
        and (table_goals - exact_goals) <= set(monitor.OBLIGATIONS)
        and _ordered_channel_goals_have_exact_product(monitor))
    return table_owned or shared_owned or (
        program.exact_control_product and _ordered_channel_goals_have_exact_product(monitor)
        and table_goals <= exact_goals | set(monitor.OBLIGATIONS))


def _monitor_goal_providers(owner, *, exact_protocol, exact_reset, local_protocol=False):
    """Partition one monitor catalogue by the standard schema it implements.

    Deadline and wait-limit violations are safety obligations, not latency-bin
    coverage. The reset machine contributes a sequence-coverage goal and an
    obligation-violation goal; both retain its state-ownership capability.
    """

    from .catalogue import COVERAGE, CatalogueManifest, catalogue_subset
    from .goal_provider import (
        CONCRETE_HIT,
        DECLARATION,
        EXACT_BOUNDED_HIT,
        LOCAL_GUIDANCE,
        RTL_LOCAL_GUIDANCE_POLICY,
        CapabilityBinding,
        GoalProvider,
        GoalProviderIdentity,
        standard_goal_schemas,
    )

    catalogue = CatalogueManifest(owner.goals())
    if not catalogue:
        return ()
    schemas = {schema.name: schema for schema in standard_goal_schemas()}
    reset_names = {
        name for name, goal in catalogue.items()
        if goal.provider == "reset-restoration"
    }
    reset_scenarios = {name for name in reset_names if catalogue[name].cls == COVERAGE}
    protocol_names = set(catalogue) - reset_names
    # Partition declarations, not execution: one reset state machine still emits
    # both bits. A completed reset-after-work scenario is coverage even when the
    # state was restored correctly; only failed restoration is an obligation hit.
    groups = (
        ("task-monitor", 1, "monitor-obligation", protocol_names, exact_protocol),
        ("reset-scenario", 1, "sequence", reset_scenarios,
         exact_protocol and exact_reset),
        ("reset-restoration", 2, "monitor-obligation", reset_names - reset_scenarios,
         exact_protocol and exact_reset),
    )
    component_monitors = tuple(
        monitor for _domain, _contract, monitor
        in getattr(owner, "_components", ())) or (owner,)
    policies = ",".join(sorted({
        f"{program.name}-v{program.version}-"
        f"{program.exact_control_product_policy or 'algebraic'}"
        for program in (
            adapter_program_for(monitor.contract)
            for monitor in component_monitors)
    }))
    exact_identity = f"finite-task-monitor/5:{policies}"
    local_identity = RTL_LOCAL_GUIDANCE_POLICY
    providers = []
    for identity, version, schema_name, names, exact in groups:
        if not names:
            continue
        component = catalogue_subset(catalogue, names)
        rows = [
            CapabilityBinding(DECLARATION, owner.POLICY, component),
            CapabilityBinding(CONCRETE_HIT, owner.POLICY, component),
        ]
        if exact:
            rows.append(CapabilityBinding(
                EXACT_BOUNDED_HIT, exact_identity, component))
        # Local guidance uses the finite product above, or the fixed-transfer
        # projection for plain sampling endpoints. The latter is not a binding
        # for whole-domain bounded analysis.
        if exact or (local_protocol and names <= protocol_names):
            rows.append(CapabilityBinding(
                LOCAL_GUIDANCE, local_identity,
                component))
        providers.append(GoalProvider.builtin(
            GoalProviderIdentity(identity, version), schemas[schema_name], component,
            tuple(rows)))
    return tuple(providers)


class Violation(Frozen):
    """One obligation, broken, at one slot."""

    __slots__ = ("endpoint", "goal", "slot", "why")

    def __init__(self, goal, slot, endpoint, why):
        #: A stable identifier, so a report can count the same obligation across runs.
        self.goal = str(goal)
        self.slot = slot
        self.endpoint = str(endpoint)
        self.why = str(why)
        self._freeze()

    def as_dict(self):
        return {"goal": self.goal, "slot": self.slot, "endpoint": self.endpoint,
                "why": self.why}

    def __repr__(self):
        return f"Violation({self.goal} at slot {self.slot}: {self.why})"


class MonitorHit(Frozen):
    """One attributed trace-goal hit, including non-violation scenarios."""

    __slots__ = ("endpoint", "goal", "slot")

    def __init__(self, goal, slot, endpoint=""):
        if type(goal) is not str or not goal:
            raise TypeError("a monitor hit needs a nonempty exact goal string")
        if type(slot) is not int or slot < 0:
            raise TypeError("a monitor hit needs a nonnegative exact slot")
        if type(endpoint) is not str:
            raise TypeError("a monitor hit endpoint must be an exact string")
        self.goal = goal
        self.slot = slot
        self.endpoint = endpoint
        self._freeze()

    def as_dict(self):
        return {"goal": self.goal, "slot": self.slot, "endpoint": self.endpoint}


class Unresolved(Frozen):
    """An obligation this run could not be judged on, and why.

    Distinct from `unchecked`, which is the *task* not declaring enough, and from a
    violation, which is the design breaking something. This is the run itself failing to
    carry the evidence: an unreadable signal, a reset under an undeclared policy, a
    horizon that ended before anything could be concluded.
    """

    __slots__ = ("about", "endpoint", "goal", "slot", "why")

    def __init__(self, goal, slot, endpoint, why, about=None):
        self.goal = str(goal)
        self.slot = slot
        self.endpoint = str(endpoint)
        self.why = str(why)
        #: **Which instance of the obligation this is**, as fields rather than as prose.
        #: `_terminal` emits one of these per transaction still in flight, and a caller
        #: that wants to line them up against a comparison's faults had `why` and a
        #: regular expression -- so the evaluation policy did not line them up at all:
        #: it asked whether *any* row anywhere in the evaluation
        #: mentioned "unanswered" and then excused every fault in every run. The fields
        #: are what an alignment can be written over: `accepted_at` and `owed`.
        self.about = FrozenMap(dict(about or {}))
        self._freeze()

    def as_dict(self):
        return {"goal": self.goal, "slot": self.slot, "endpoint": self.endpoint,
                "why": self.why, "about": dict(self.about)}

    def __repr__(self):
        return f"Unresolved({self.goal} at slot {self.slot}: {self.why})"


class MonitorReport(Frozen):
    """What one run established about the protocol obligations.

    Environment assumption failures are separate from goal outcomes: they put the
    trace outside the declared domain and therefore cannot become safety hits.  The
    remaining fields distinguish violations, exercised opportunities, unresolved
    evidence, and obligations the task did not declare enough to check.
    """

    __slots__ = (
        "assumption_violations", "hits", "opportunities", "unchecked",
        "unresolved", "violations",
    )

    def __init__(self, violations=(), opportunities=None, unresolved=(),
                 unchecked=(), hits=(), assumption_violations=()):
        if type(hits) not in (tuple, list) \
                or any(type(item) is not MonitorHit for item in hits):
            raise TypeError("monitor hits must be exact MonitorHit records")
        self.hits = tuple(hits)
        self.violations = tuple(violations)
        self.assumption_violations = tuple(assumption_violations)
        #: `goal -> how many times it could have failed`. An obligation with zero
        #: opportunities and zero violations was not met; it was not tested.
        self.opportunities = dict(opportunities or {})
        self.unresolved = tuple(unresolved)
        self.unchecked = tuple(unchecked)
        self._freeze()

    def __iter__(self):
        """`violations, opportunities`, for the two-value callers that predate this."""
        return iter((self.violations, self.opportunities))


def _merge_reports(reports):
    """Combine independent checks without losing attribution or opportunities."""
    hits, violations, unresolved, assumptions = [], [], [], []
    opportunities, unchecked = {}, set()
    for report in reports:
        hits.extend(report.hits)
        violations.extend(report.violations)
        unresolved.extend(report.unresolved)
        assumptions.extend(report.assumption_violations)
        unchecked.update(report.unchecked)
        for goal, count in report.opportunities.items():
            opportunities[goal] = opportunities.get(goal, 0) + count
    return MonitorReport(
        hits=hits, violations=violations, unresolved=unresolved,
        assumption_violations=assumptions, opportunities=opportunities,
        unchecked=tuple(sorted(unchecked)))


def _input_completion(contract, stimulus, trace):
    """Judge terminal progress against the original, unprojected run schedule.

    Interface and clock projections own protocol events, not the injection/drain
    boundary. Another interface may still inject while this one legally stalls.
    Transfer records are the same authority used by the protocol ledger; padding
    contributes neither transfers nor evidence of terminal completion.
    """
    from .stimulus import Completion, uses_global_events

    inputs = {endpoint.name: endpoint for endpoint in contract.inputs()
              if endpoint.kind is Kind.TRANSACTION}
    depths = {name: stimulus.depth(name) for name in inputs if stimulus.depth(name) > 0}
    chances = {INPUT_COMPLETION: len(depths)}
    if not depths:
        return MonitorReport(opportunities=chances)
    observed = trace.observed_count
    # Retain the projections' active-edge domain when counting the original
    # records. An off-domain record cannot discharge a real input obligation.
    atomic = uses_global_events(contract, stimulus)
    accepted = Counter(
        name for slot, record in zip(stimulus.slots[:observed], trace.records[:observed])
        for name, payload in record.accepted if name in depths and payload is not None
        and endpoint_is_active(inputs[name], slot, atomic))
    remaining = {name: depth - accepted[name] for name, depth in depths.items()
                 if accepted[name] < depth}
    slot = max(0, observed - 1)
    if trace.request is not None and trace.completion in (Completion.INCOMPLETE, Completion.CHECKPOINT):
        # A runtime counterexample prefix has not reached injection/drain end.
        # Requiring future offers or a receptive suffix here would replace the
        # observed candidate violation with an invented environment fault. The
        # causal schedule and per-slot environment assumptions are checked apart.
        return MonitorReport(opportunities=chances, unresolved=tuple(
            Unresolved(INPUT_COMPLETION, slot, endpoint,
                       f"runtime replay stopped with {count} input transaction(s) remaining",
                       about={"remaining": count, "truncated": True})
            for endpoint, count in sorted(remaining.items())))
    environment = input_completion_environment(stimulus, contract)
    outside = [Violation(fault.kind, fault.slot, fault.endpoint, fault.why)
               for fault in environment.faults]
    if remaining and not environment.drain_slots:
        outside.append(Violation(
            "environment.drain-missing", slot, "",
            "the run ended with pending input but no active drain slot"))
    if outside:
        return MonitorReport(opportunities=chances, assumption_violations=outside)
    if not remaining:
        return MonitorReport(opportunities=chances)
    if (trace.completion in (Completion.INCOMPLETE, Completion.PADDED)
            or observed < len(trace.records)):
        return MonitorReport(opportunities=chances, unresolved=tuple(
            Unresolved(
                INPUT_COMPLETION, slot, endpoint,
                f"the run ended before it could establish whether {count} "
                "remaining input transaction(s) would be accepted",
                about={"remaining": count, "truncated": True})
            for endpoint, count in sorted(remaining.items())))
    violations = tuple(Violation(
        INPUT_COMPLETION, slot, endpoint,
        f"{count} queued input transaction(s) remained unaccepted at the end of drain")
        for endpoint, count in sorted(remaining.items()))
    return MonitorReport(
        hits=tuple(MonitorHit(item.goal, item.slot, item.endpoint) for item in violations),
        violations=violations, opportunities=chances)


def _reset_report(provider, stimulus, trace, *, state_fields=None, clock_domain=None):
    """Observe reset levels on every step and retain the original slot indexes."""
    evidence = provider.check(
        stimulus, trace, state_fields=state_fields, clock_domain=clock_domain)
    endpoint = provider.domains[0].name if len(provider.domains) == 1 else ""
    opportunities = {}
    for goal in evidence.opportunities:
        opportunities[goal] = opportunities.get(goal, 0) + 1

    return MonitorReport(
        hits=[MonitorHit(goal, slot, endpoint)
              for goal, slot in evidence.hits],
        violations=[Violation(goal, slot, endpoint, why)
                    for goal, slot, why in evidence.violations],
        unresolved=[Unresolved(goal, slot, endpoint, why)
                    for goal, slot, why in evidence.unresolved],
        opportunities=opportunities)


class Monitor:
    """The contract's obligations, over one stimulus and one record of running it."""

    #: The version of this obligation set. Two runs under different versions counted
    #: different things, so it goes on every goal a monitor declares.
    #:
    #: Bumped from `monitor/2`: the deadline is measured to a different event, the
    #: retirement rule changed, and four obligations that used to run on an assumed 1:1
    #: correlation now require it declared. Results under the two are not comparable and
    #: the identifier has to say so.
    # ``/5`` adds registered-adapter protocol events and the start/done deadline
    # transition.  Keeping the old identity would make pre-adapter and adapter-backed
    # monitor records appear comparable even though their catalogue and hit relation
    # differ.
    # ``/6`` removes environment-owned adapter assumptions from the goal catalogue
    # and reports their failures as trace-domain exclusions instead. ``/7`` adds the
    # terminal input-completion obligation and makes receptive drain part of its trace
    # premise. ``/8`` makes an accepted, repeated in-flight identifier an explicit
    # environment-domain violation instead of unresolved artifact evidence.
    # ``/9`` keeps global attribution on partial domain projections; terminal
    # padding is outside every protocol and reset-restoration transition.
    # ``/10`` carries elapsed global slots into registered-adapter replay, including
    # retained requests across reset. Projection no longer changes deadline units.
    # ``/11`` observes terminal input completion once on the unprojected schedule.
    # ``/13`` preserves a runtime counterexample's unfinished input/drain premise.
    POLICY = "monitor/14"

    #: The shared precondition of the four obligations that count outstanding
    #: transactions, as a name an unresolved can be filed under.
    #:
    #: Not one of the four. A run whose identifier could not be read, or whose reset
    #: arrived under an undeclared policy, has failed to establish the *correlation* --
    #: and filing that under `monitor.response-deadline` reported a doubt about an
    #: obligation the task may never have declared, which is a third kind of thing again.
    CORRELATION = "monitor.correlation"

    #: `goal -> (role, preconditions, owed_by)`.
    #:
    #: ``role`` is which endpoint carries the declaration and the signals: `in` for the
    #: environment's channel, `out` for the artifact's. ``preconditions`` is what the
    #: task must declare for the obligation to mean anything -- field names on the
    #: endpoint, plus the pseudo-precondition `correlation`, which means the task has
    #: said how an output is matched to the request it answers. ``owed_by`` is who
    #: breaks it.
    OBLIGATIONS: ClassVar = {
        INPUT_COMPLETION: ("in", (), "artifact"),
        "monitor.sink-stability": ("out", (), "artifact"),
        "monitor.outstanding-exceeded":
            ("in", ("max_outstanding", "correlation"), "artifact"),
        "monitor.output-without-transaction": ("out", ("correlation",), "artifact"),
        "monitor.response-deadline":
            ("in", ("response_deadline", "correlation"), "artifact"),
    }

    #: What the run could not settle about transactions still outstanding at the end.
    #: An `Unresolved` name, never a violation. See `_terminal`.
    UNANSWERED = "monitor.unanswered-at-horizon"

    def __init__(self, contract):
        self.contract = contract
        self.reset_restoration = ResetRestorationProvider(contract)
        self._interfaces = ()
        if len(contract.protocol_instances) > 1:
            from .interface_product import instance_contracts

            # Correlation belongs to the instance, not all endpoints on its clock.
            self._interfaces = tuple(
                (view, Monitor(view)) for _, view in instance_contracts(contract))

    # ── what the task declared ──

    def _endpoint(self, role):
        got = (self.contract.inputs() if role == "in" else self.contract.outputs())
        # Correlation is a relation between a declared request stream and a declared
        # response stream.  With several endpoints, selecting the first pair would add
        # a relation the task never declared.  Endpoint-local obligations (notably
        # stalled-offer stability) iterate all endpoints in ``_general_check`` below;
        # the singular accessor is deliberately available only for a unique pair.
        return got[0] if len(got) == 1 else None

    def correlation(self):
        """How an output is matched to the request it answers, or `None`.

        `("ordered", ratio)` or `("identifier", ratio, field)`. Both require the task to
        declare how many answers a transaction owes, because that is what says when it
        stops being outstanding, and every obligation below that counts in-flight
        transactions rests on it.

        **`None` is the common case and that is the point.** Assuming one answer per
        request is a claim about the protocol that the task did not make.
        """
        source, sink = self._endpoint("in"), self._endpoint("out")
        if source is None or sink is None:
            return None
        # A channel whose kind is not declared as carrying transactions is not one whose
        # transfers can be counted as transactions. `Kind` exists precisely because this
        # cannot be recovered from a run.
        if source.kind is not Kind.TRANSACTION or sink.kind is not Kind.TRANSACTION:
            return None
        try:
            program = adapter_program_for(self.contract)
        except Refused:
            return None
        if not program.request_response_correlation:
            return None
        ratio = sink.answers_per_transaction
        if ratio is None:
            return None
        if sink.matching is Matching.IDENTIFIER:
            field = sink.identifier
            if field is None or source.field(field) is None:
                # An identifier the request does not also carry correlates nothing: the
                # answer names a transaction that cannot be found among those in flight.
                return None
            return ("identifier", ratio, field)
        if sink.matching is Matching.ORDERED:
            return ("ordered", ratio)
        # CONTENT admits any reordering, so an answer cannot be attributed to a request
        # at all and nothing that counts outstanding transactions can be checked.
        return None

    def _declared(self, goal):
        """Whether the task declares enough for ``goal`` to be checked."""
        if not self._applicable(goal):
            return False
        if goal == INPUT_COMPLETION:
            return any(endpoint.kind is Kind.TRANSACTION
                       for endpoint in self.contract.inputs())
        if goal == "monitor.sink-stability":
            return any(endpoint.kind is Kind.TRANSACTION
                       for endpoint in self.contract.outputs())
        role, needs, _owed = self.OBLIGATIONS[goal]
        endpoint = self._endpoint(role)
        for need in needs:
            if need == "correlation":
                if self.correlation() is None:
                    return False
                continue
            if endpoint is None or getattr(endpoint, need, None) is None:
                return False
        return endpoint is not None

    def _applicable(self, goal):
        """Whether this adapter gives the obligation a protocol event to observe."""
        if goal == INPUT_COMPLETION:
            return bool(self.contract.inputs())
        program = adapter_program_for(self.contract)
        if goal == "monitor.sink-stability":
            return program.stalled_offer_stability
        return program.generic_correlation_monitor

    def unchecked(self):
        """Obligations the contract does not declare enough for this build to check."""
        if self._interfaces:
            return tuple(sorted({goal for _, monitor in self._interfaces
                                 for goal in monitor.unchecked()}))
        return tuple(sorted(g for g in self.OBLIGATIONS
                            if self._applicable(g) and not self._declared(g)))

    def goals(self):
        """The obligations this task **declares**, as catalogue `Goal` records."""
        if self._interfaces:
            goals = {}
            for _, monitor in self._interfaces:
                MonitorProduct._merge_goals(goals, monitor.goals())
            return goals
        goals = {
            name: Goal(name, SAFETY,
                       family=("timing" if name == "monitor.response-deadline"
                               else "protocol"),
                       provider="monitor",
                       event=name.split(".", 1)[1], policy=self.POLICY,
                       evidence="trace",
                       provenance={"owed_by": owed, "role": role})
            for name, (role, _needs, owed) in self.OBLIGATIONS.items()
            if self._declared(name)
        }
        for name, owed, role in adapter_program_for(
                self.contract).monitor_goal_declarations_for(self.contract):
            if owed != ARTIFACT:
                continue
            goals[name] = Goal(
                name, SAFETY,
                family=("timing" if name in ("monitor.response-deadline", "monitor.apb.wait-limit",
                                              "monitor.axil.request-wait-limit")
                        else "protocol"),
                provider="monitor",
                event=name.split(".", 1)[1], policy=self.POLICY,
                evidence="trace",
                provenance={"owed_by": owed, "role": role})
        goals.update(self.reset_restoration.goals())
        return goals

    def goal_providers(self):
        """Standard-schema providers implemented by this concrete monitor."""

        exact_protocol = _protocol_goals_have_exact_product(self)
        exact_reset = not self.reset_restoration.domains or (
            len(self.contract.clocks) == 1
            and len(self.reset_restoration.domains) == 1)
        return _monitor_goal_providers(
            self, exact_protocol=exact_protocol, exact_reset=exact_reset,
            local_protocol=bool(self.payload_independent_goals()))

    def payload_independent_goals(self):
        return _payload_independent_goals(self)

    def goal_provider_capabilities(self):
        """Describe the monitor's implemented T3-10 capabilities without guessing.

        Declaration and concrete trace judgement are always present.  Exact bounded
        hits are present when the registered protocol exposes the finite control table
        used by RTL analysis, or for the shared ordered-channel product. Reset
        queries additionally require the exact frontend's owned-state carrier. Goals
        additionally name the exact-local RTL provider that freezes the complete
        product boundary and mutates only selected queue cells; this is a separate
        capability from exact reachability.

        Adapter-owned start/done goals enter through :meth:`goals`, so protocol
        extensions use the same capability record rather than a side catalogue.
        """

        providers = self.goal_providers()
        protocol = next((provider for provider in providers
                         if provider.schema.name == "monitor-obligation"), None)
        if protocol is None:
            raise Refused(Refusal(
                "task-monitor-provider-inapplicable",
                "this task declares no protocol-monitor goal instances"))
        return protocol

    def assumptions(self):
        """What the checks take for granted and do not establish.

        Empty, and deliberately. The one standing entry was "a reset slot clears what is
        in flight", which the monitor then acted on -- and an assumption that decides a
        verdict is an undeclared part of the contract, not a caveat. It is now
        `Endpoint.reset_policy`: declared, or the obligations that depend on it are
        unresolved from the reset onward.
        """
        return ()

    def ordered_channel_config(self, capacity):
        config = self.finite_channel_config(capacity)
        return config if config is not None and config.identifier_index is None else None

    def finite_channel_config(self, capacity):
        """Shared ordered monitor over handshake or sampled transfer facts.

        Missing sender/receiver controls mean true, not invented physical pins.
        Adapter sampling still owns when an event is active.
        """
        source, sink = self._endpoint("in"), self._endpoint("out")
        if source is None or sink is None:
            return None
        if source.kind is not Kind.TRANSACTION or sink.kind is not Kind.TRANSACTION:
            return None
        if sink.matching not in (Matching.ORDERED, Matching.IDENTIFIER):
            return None
        if any(endpoint.adapter.name not in (READY_VALID, SAMPLED_EDGE, SETTLED, APB_ACCESS, AXI_LITE_READ, AXI_LITE_WRITE)
               or endpoint.adapter.version != 1
               for endpoint in (source, sink)):
            return None
        correlation = self.correlation()
        ratio = None if correlation is None else correlation[1]
        identifier_index = None
        if correlation is not None and correlation[0] == "identifier":
            key = sink.field(correlation[2])
            if source.field(key.name).type != key.type:
                return None  # Packed equality requires equal logical key types.
            identifier_index = next(i for i, field in enumerate(sink.fields) if field.name == key.name)
        return OrderedReadyValidConfig(
            capacity=capacity,
            payload_fields=len(sink.fields),
            answers_per_transaction=ratio,
            max_outstanding=(source.max_outstanding if ratio is not None else None),
            response_deadline=(source.response_deadline if ratio is not None else None),
            reset_policy=None if source.reset_policy is None else source.reset_policy.value,
            identifier_index=identifier_index,
        )

    # ── the check ──

    def check(self, stimulus, trace, *, reset_state_fields=None, _protocol_ledgers=None):
        """A `MonitorReport` over one run.

        ``reset_state_fields`` is supplied by the RTL frontend when it owns a complete
        finite state carrier.  Omitting it cannot manufacture a clean restoration
        result: the scenario remains observable, while the safety goal is unresolved.
        """
        from .stimulus import uses_global_events

        domain = None
        local_stimulus, local_trace, indexes = stimulus, trace, None
        if uses_global_events(self.contract, stimulus):
            from .atomic import project_domain_trace

            domain = self.contract.clocks[0].name
            local_stimulus, local_trace, indexes = project_domain_trace(
                domain, self.contract, stimulus, trace)
        return _merge_reports((
            self._check_protocol(local_stimulus, local_trace, _slot_indexes=indexes,
                                 _protocol_ledgers=_protocol_ledgers),
            _input_completion(self.contract, stimulus, trace),
            _reset_report(self.reset_restoration, stimulus, trace,
                          state_fields=reset_state_fields, clock_domain=domain)))

    def _check_protocol(self, stimulus, trace, *, _slot_indexes=None, _protocol_ledgers=None):
        """Protocol-local checks; global completion and reset are checked separately."""
        if self._interfaces:
            from .interface_product import project_run

            return _merge_reports(tuple(
                monitor._check_protocol(*project_run(view, stimulus, trace),
                                        _slot_indexes=_slot_indexes,
                                        _protocol_ledgers=_protocol_ledgers)
                for view, monitor in self._interfaces))
        ledger = (_protocol_ledgers or {}).get(
            tuple(endpoint.name for endpoint in self.contract.endpoints))
        if ledger is None:
            ledger = ledger_of(self.contract, stimulus, trace, slot_indexes=_slot_indexes)
        finite = self._finite_ordered_check(ledger)
        protocol = finite if finite is not None else self._general_check(ledger)
        # Protocol goals are safety goals, so each detailed violation is also its
        # attributed hit.  Reset scenarios are checked separately.
        protocol_violations = protocol.violations
        protocol_hits = tuple(
            MonitorHit(item.goal, item.slot, item.endpoint)
            for item in protocol_violations)
        return MonitorReport(
            hits=protocol_hits,
            violations=protocol_violations,
            opportunities=protocol.opportunities,
            unresolved=protocol.unresolved,
            unchecked=protocol.unchecked,
            assumption_violations=protocol.assumption_violations,
        )

    def _general_check(self, ledger):
        """The general ledger monitor outside the finite ordered product subset."""
        cursor = ProtocolMonitorCursor(self)
        for fact in ledger:
            cursor.advance(fact)
        if cursor.counting and not cursor.correlation_lost:
            self._terminal(ledger, cursor.sink, cursor.pending, cursor.unresolved)
        return cursor.report()

    def _finite_ordered_check(self, ledger):
        """Use the shared finite transition rule where its premises are visible.

        Returning ``None`` selects the general monitor.  In particular, an unreadable
        trace, identifier matching, a truncated run or a reset after traffic retains the
        richer unresolved semantics above rather than being forced into Boolean terms.
        """
        source, sink = self._endpoint("in"), self._endpoint("out")
        if source is None or sink is None:
            return None
        config = self.ordered_channel_config(max(1, len(ledger)))
        if config is None or ledger.truncated:
            return None
        # This fast path emits only the generic obligations. Bus adapters also
        # own per-slot predicates (e.g. EXOKAY and response-before-request);
        # the general cursor combines those with the same ordered semantics.
        if adapter_program_for(self.contract).monitor_goals_owned_by(self.contract, ARTIFACT):
            return None
        seen_body = False
        accepted = 0
        for fact in ledger:
            if fact.reset:
                if seen_body:
                    return None
                continue
            seen_body = True
            inp, out = fact.at(source.name), fact.at(sink.name)
            if inp is None or out is None:
                return None
            # Input acceptance is the driver's recorded authority.  Its derived valid
            # is intentionally not duplicated in ``slot.controls``, so the ledger may
            # leave ``inp.offered`` unstated even though ``inp.transferred`` is exact.
            values = (inp.transferred, out.offered, out.ready, out.transferred)
            if any(type(value) is not bool for value in values):
                return None
            if not out.payload_readable or out.payload is None:
                return None
            accepted += int(inp.transferred)

        # Each accepted request selects its monotonically numbered queue slot.
        # No slot beyond this complete ledger's acceptance count can be written;
        # all omitted components stay at reset, including on violating traces.
        # Do not cap by max_outstanding: exceeding it must remain observable.
        # Symbolic products still use their declared domain's queue bound.
        config = self.ordered_channel_config(max(1, accepted))

        machine = OrderedConcreteMonitor(config)
        found, unresolved = [], []
        chances = dict.fromkeys(self.OBLIGATIONS, 0)
        # This finite product implements exactly ``OBLIGATIONS``.  The complete
        # catalogue may also contain adapter-owned goals and independent providers
        # such as reset restoration; those are merged by ``check`` after this protocol
        # transition has run and must not be indexed through this product's hit map.
        live = (set(self.OBLIGATIONS) - {INPUT_COMPLETION}).intersection(
            self.goals())
        for fact in ledger:
            inp, out = fact.at(source.name), fact.at(sink.name)
            event = OrderedReadyValidEvent(
                reset=fact.reset,
                slot=fact.index,
                input_accepted=False if inp is None else inp.transferred,
                output_valid=False if out is None else out.offered,
                output_ready=False if out is None else out.ready,
                output_payload=((0,) * len(sink.fields)
                                if out is None or out.payload is None
                                else out.payload),
            )
            stepped = machine.step(event)
            for goal in live:
                chances[goal] += int(stepped.opportunities[goal])
                if not stepped.hits[goal]:
                    continue
                if goal == "monitor.response-deadline":
                    for _index, accepted_at in stepped.deadline_hits:
                        why = (f"the transaction accepted at slot {accepted_at} has "
                               f"gone {fact.index - accepted_at} slots without the "
                               "design offering an answer, where the task declares "
                               f"{source.response_deadline}")
                        found.append(Violation(goal, fact.index, sink.name, why))
                    continue
                if goal == "monitor.sink-stability":
                    if stepped.stability_dropped:
                        why = (f"the design dropped `valid` at slot {fact.index} while "
                               "the environment had not taken its preceding offer")
                    else:
                        why = (f"the payload changed from {list(stepped.held_before)} to "
                               f"{list(event.output_payload)} while the environment "
                               "had not taken it")
                elif goal == "monitor.output-without-transaction":
                    why = ("an output transferred with no transaction outstanding, "
                           "and the task declares how many answers each one owes")
                else:
                    why = (f"{stepped.outstanding} "
                           "transactions are in flight at the end of this slot where "
                           f"the task declares at most {source.max_outstanding}")
                endpoint = (source.name if goal == "monitor.outstanding-exceeded"
                            else sink.name)
                found.append(Violation(goal, fact.index, endpoint, why))

        pending = machine.pending_requests()
        self._terminal(ledger, sink, pending, unresolved)
        return MonitorReport(
            violations=found,
            opportunities=chances,
            unresolved=unresolved,
            unchecked=self.unchecked(),
        )

    # ── one obligation each ──

    def _stability(self, out, sink, index, offering_since, held, found, unresolved,
                   chances):
        """`valid` and the payload must not move while the receiver has not taken them.

        **Two facts, tracked apart.** Whether an offer is in progress, and what payload
        it carried. They were one variable -- the held payload, `None` for both "no offer"
        and "an offer whose data lines were unreadable" -- and the second reading swallowed
        a violation the run had fully determined: a design that stalls with X on the data
        bus and then withdraws `valid` on the next cycle has broken the obligation, and
        `valid` is readable on both slots, so nothing about it is in doubt. It came back
        as an unresolved payload and no violation at all.

        So the payload being unknown makes the *payload* comparison unresolved and leaves
        the offer standing. Only the handshake being unreadable puts the offer itself in
        doubt, and then there is nothing to hold.

        Returns `(offering_since, held)` for the next slot.
        """
        goal = "monitor.sink-stability"
        if out.offered is None:
            # The handshake itself did not come back. Whether an offer is even in
            # progress is unknown, so the state is dropped rather than carried across a
            # gap: comparing the next readable slot against one from before it would
            # report a change the run cannot attribute anywhere.
            if offering_since is not None:
                unresolved.append(Unresolved(
                    goal, index, sink.name,
                    f"the offer begun at slot {offering_since} could not be followed: "
                    f"{out.why_unreadable} did not come back as a value, so whether "
                    f"`valid` is still asserted is unknown"))
            return None, None

        if offering_since is not None:
            chances[goal] += 1
            if not out.offered:
                # **Determined, whatever the payload was.** `valid` is readable here and
                # was readable when the offer began; the design withdrew an offer nobody
                # had taken.
                found.append(Violation(
                    goal, index, sink.name,
                    f"the design dropped `valid` at slot {index} while the environment "
                    f"had not taken the offer it began at slot {offering_since}"))
                return None, None
            if not out.payload_readable or held is None:
                unresolved.append(Unresolved(
                    goal, index, sink.name,
                    f"the offer begun at slot {offering_since} is still asserted and "
                    f"{out.why_unreadable or 'its payload was not recorded'}, so "
                    f"whether the design held what it was offering cannot be told -- "
                    f"two unknowns compare equal"))
            elif out.payload != held:
                found.append(Violation(
                    goal, index, sink.name,
                    f"the payload changed from {list(held)} to {list(out.payload)} "
                    f"while the environment had not taken it"))

        if out.offered and not out.transferred:
            return (offering_since if offering_since is not None else index,
                    out.payload if out.payload_readable else None)
        return None, None

    def _correlate(self, fact, index, source, sink, inp, out, correlation, pending,
                   live, found, outside, unresolved, chances):
        """Accept, offer, transfer and retire, in the order they happen within a slot.

        The order is the whole content of two of these checks. An accept and a
        retirement in one slot is a design holding one transaction, not two: counting
        the limit between them charged a correct design with an overflow it never had.
        """
        kind, ratio = correlation[0], correlation[1]
        idfield = correlation[2] if kind == "identifier" else None

        # 1. what the design took here
        if inp is not None and inp.transferred:
            key = None
            if idfield is not None:
                key = self._field_of(source, idfield, inp.payload)
                if key is None:
                    unresolved.append(Unresolved(
                        self.CORRELATION, index, source.name,
                        "this transaction's identifier could not be read, so no answer "
                        "can be correlated with it"))
                elif any(p["key"] == key for p in pending):
                    # This is the environment-side premise of identifier matching, not
                    # uncertainty in the artifact's result.  The accepted schedule lies
                    # outside the declared trace domain and therefore receives no score.
                    outside.append(Violation(
                        "environment.identifier-reused-while-outstanding",
                        index, source.name,
                        f"identifier {key} is already in flight, and the matching "
                        "policy requires it to be unique among outstanding "
                        "transactions"))
                    return True
            pending.append({"at": index, "owed": ratio, "key": key,
                            "offered_at": None, "reported": False})

        # 2. the deadline, per transaction, measured to the **first offer**
        #
        # Before the offer below is credited, so that an offer arriving one slot past the
        # deadline is the violation it is. Crediting first and checking after made every
        # deadline unbreakable as long as an answer eventually came: the transaction was
        # marked offered and then skipped.
        if "monitor.response-deadline" in live:
            deadline = source.response_deadline
            for item in pending:
                if item["offered_at"] is not None or item["reported"]:
                    continue
                chances["monitor.response-deadline"] += 1
                if index - item["at"] > deadline:
                    found.append(Violation(
                        "monitor.response-deadline", index,
                        sink.name if sink else source.name,
                        f"the transaction accepted at slot {item['at']} has gone "
                        f"{index - item['at']} slots without the design offering an "
                        f"answer, where the task declares {deadline}"))
                    # **Kept.** Retiring it here matched its eventual answer to the
                    # next transaction and manufactured a second, phantom fault.
                    item["reported"] = True

        # 3. what the design offered here, and to which request
        #
        # **An offer whose payload is unreadable is still an offer.** `valid` is what
        # says one was made, and the deadline is measured to it; what is on the data
        # lines decides whether the answer can be *compared*, not whether it was
        # presented. Requiring the payload here made an X-driven offer invisible, so
        # the deadline went on firing against a transaction the design had answered --
        # and the run reported an unresolved and a late violation for one event.
        if out is not None and out.started and out.offered:
            if idfield is not None and not out.payload_readable:
                unresolved.append(Unresolved(
                    self.CORRELATION, index, sink.name,
                    "this offer's identifier could not be read, so which outstanding "
                    "transaction it answers is unknown"))
            else:
                target = self._target(pending, out, sink, idfield, offered=True)
                if target is not None and target["offered_at"] is None:
                    target["offered_at"] = index

        # 4. what crossed here
        if out is not None and out.transferred:
            target = self._target(pending, out, sink, idfield, offered=False)
            if "monitor.output-without-transaction" in live:
                chances["monitor.output-without-transaction"] += 1
                # **Also where nothing it could answer is outstanding.** Under an
                # identifier policy an answer tagged 9 while only tag 7 is in flight is
                # exactly this obligation's case, and the check only looked at whether
                # the pending list was empty -- so the answer to a request that was
                # never made went unreported whenever any other request happened to be
                # open.
                if not pending:
                    found.append(Violation(
                        "monitor.output-without-transaction", index, sink.name,
                        "an output transferred with no transaction outstanding, and "
                        "the task declares how many answers each one owes"))
                elif target is None and idfield is not None and out.payload_readable:
                    found.append(Violation(
                        "monitor.output-without-transaction", index, sink.name,
                        f"this output is tagged "
                        f"{self._field_of(sink, idfield, out.payload)} and no "
                        f"outstanding transaction carries that identifier, so it "
                        f"answers a request that is not in flight"))
            if target is not None:
                if target["offered_at"] is None:
                    target["offered_at"] = index
                target["owed"] -= 1
                if target["owed"] <= 0:
                    pending.remove(target)

        # 5. the outstanding limit, at the **end** of the slot
        if "monitor.outstanding-exceeded" in live:
            chances["monitor.outstanding-exceeded"] += 1
            if len(pending) > source.max_outstanding:
                found.append(Violation(
                    "monitor.outstanding-exceeded", index, source.name,
                    f"{len(pending)} transactions in flight at the end of this slot "
                    f"where the task declares at most {source.max_outstanding}"))
        return False

    def _terminal(self, ledger, sink, pending, unresolved):
        """What still being owed at the end of the horizon does **not** establish.

        It establishes nothing, and the previous version thought it established a lot.
        It looked back through the run for a slot on which the design offered nothing
        while the environment was ready, and read that as terminal quiescence: the task
        declares the design quiet when it owes nothing, the design was quiet here, so it
        owed nothing here, so what is outstanding now was never answered.

        The middle step is the converse of the declaration. `terminal_quiescence` says
        *owes nothing implies quiet*. It does not say *quiet implies owes nothing*, and
        the difference is every design that takes a cycle to compute: a request accepted
        at slot 3 and answered at slot 5 is silent at slot 4, and slot 4 is then cited as
        proof that a later request was never answered. Reset slots and the padding after
        a failure were read the same way.

        Over a finite horizon, with no observable done condition in this adapter, an
        outstanding transaction is settled by exactly one thing: a declared response
        deadline, which `_correlate` already checks each slot and reports where it
        elapses. Anything else is unresolved, and says which of the reasons it is.
        """
        if not pending:
            return
        where = sink.name if sink is not None else ""
        last = ledger.slots[-1].index if ledger.slots else 0
        if ledger.truncated:
            unresolved.append(Unresolved(
                self.UNANSWERED, last, where,
                f"the run stopped before its horizon -- cut short, or padded after the "
                f"artifact failed -- with {len(pending)} transaction(s) in flight, so "
                f"nothing about a terminal state was observed",
                about={"truncated": True, "in_flight": len(pending),
                       # Deliberately no `owed`: a truncated run did not reach its
                       # horizon, so what it owes at the end is not a number this
                       # observed. A caller aligning faults against obligations gets
                       # nothing to align, which is the right answer here.
                       "owed": None}))
            return
        deadline = self._endpoint("in")
        declared = getattr(deadline, "response_deadline", None)
        for item in pending:
            if item["reported"]:
                # Its deadline elapsed and `monitor.response-deadline` said so. Saying
                # it again here would count one fault twice, in two obligations.
                continue
            unresolved.append(Unresolved(
                self.UNANSWERED, last, where,
                about={"accepted_at": item["at"], "owed": int(item["owed"]),
                       "truncated": False},
                why=f"the transaction accepted at slot {item['at']} still owes "
                f"{item['owed']} answer(s) at the end of the horizon"
                + (f", and its declared deadline of {declared} has not elapsed"
                   if declared is not None else
                   ", and the task declares no response deadline, so a finite run "
                   "cannot tell a design that will never answer from one that had not "
                   "finished")))

    # ── reading the ledger ──

    @staticmethod
    def _field_of(endpoint, name, payload):
        if payload is None:
            return None
        for position, field in enumerate(endpoint.fields):
            if field.name == name and position < len(payload):
                return payload[position]
        return None

    def _target(self, pending, out, sink, idfield, offered):
        """Which outstanding transaction this output belongs to.

        Under `ORDERED`, the oldest that has not had this event yet. Under `IDENTIFIER`,
        the one whose declared field matches -- and none, where it matches nothing,
        which is an answer to a request that is not outstanding rather than an answer to
        the oldest one.
        """
        if not pending:
            return None
        if idfield is not None:
            key = self._field_of(sink, idfield, out.payload)
            for item in pending:
                if item["key"] == key:
                    return item
            return None
        # **The head, for both events.** Under ordered matching a transaction's answers
        # all arrive before the next transaction's, so the one being answered is always
        # the oldest outstanding. Searching for the first transaction with no offer yet
        # was the same thing whenever a request owes exactly one answer, and wrong the
        # moment it owes two: the second answer to transaction A was credited to
        # transaction B, whose deadline then never fired however long it was starved.
        return pending[0]


class ProtocolMonitorCursor:
    """The general protocol rules, advanced once per observed fact.

    Runtime replay can stop on a determined violation without waiting for its
    missing output. Offline checking uses the same transition. No terminal
    completion/deadline is invented from elapsed wall time or an unfinished run.
    """

    def __init__(self, monitor):
        self.monitor = monitor
        self.source, self.sink = monitor._endpoint("in"), monitor._endpoint("out")
        self.sinks = tuple(endpoint for endpoint in monitor.contract.outputs()
                           if endpoint.kind is Kind.TRANSACTION)
        self.live = {goal for goal in monitor.OBLIGATIONS if monitor._declared(goal)}
        self.correlation = monitor.correlation()
        self.counting = bool(self.live & {"monitor.outstanding-exceeded",
                                         "monitor.response-deadline",
                                         "monitor.output-without-transaction"})
        self.pending = []
        self.correlation_lost = False
        self.held = {endpoint.name: (None, None) for endpoint in self.sinks}
        self.found, self.outside, self.unresolved = [], [], []
        self.chances = dict.fromkeys(tuple(monitor.OBLIGATIONS) + tuple(
            adapter_program_for(monitor.contract).monitor_goals_owned_by(
                monitor.contract, ARTIFACT)), 0)

    def advance(self, fact):
        monitor, source, sink = self.monitor, self.source, self.sink
        index = fact.index
        out = fact.at(sink.name) if sink is not None else None
        inp = fact.at(source.name) if source is not None else None
        for goal in fact.adapter_checks:
            self.chances[goal] = self.chances.get(goal, 0) + 1
        for violation in fact.adapter_violations:
            self.found.append(Violation(violation.goal, index, violation.endpoint, violation.why))
        for violation in fact.adapter_environment_violations:
            self.outside.append(Violation(violation.goal, index, violation.endpoint, violation.why))
        if fact.reset:
            self.held = {endpoint.name: (None, None) for endpoint in self.sinks}
            if self.pending and self.counting and not self.correlation_lost:
                policy = source.reset_policy if source is not None else None
                if policy is None:
                    self.correlation_lost = True
                    self.unresolved.append(Unresolved(
                        monitor.CORRELATION, index, source.name if source else "",
                        f"reset arrived with {len(self.pending)} transaction(s) in "
                        f"flight and the task does not declare whether a reset "
                        f"clears them, so nothing that counts outstanding "
                        f"transactions can be judged from here on"))
                elif policy is ResetPolicy.CLEARS:
                    self.pending = []
            return
        if "monitor.sink-stability" in self.live:
            for endpoint in self.sinks:
                channel = fact.at(endpoint.name)
                if channel is not None:
                    self.held[endpoint.name] = monitor._stability(
                        channel, endpoint, index, *self.held[endpoint.name],
                        self.found, self.unresolved, self.chances)
        if self.counting and not self.correlation_lost:
            self.correlation_lost = monitor._correlate(
                fact, index, source, sink, inp, out, self.correlation,
                self.pending, self.live, self.found, self.outside, self.unresolved, self.chances)

    def report(self):
        return MonitorReport(violations=self.found, opportunities=self.chances,
                             unresolved=self.unresolved, unchecked=self.monitor.unchecked(),
                             assumption_violations=self.outside)


class RuntimeProtocolMonitor:
    """One clock's live protocol product, including heterogeneous interfaces.

    Only determined violations stop execution. Unresolved samples, an owed
    response without a declared deadline, and uncovered goals do not.
    """

    def __init__(self, contract, program):
        from .protocol import ProtocolFacts

        monitor = Monitor(contract)
        self.cursors = tuple(ProtocolMonitorCursor(part)
            for part in (tuple(item for _, item in monitor._interfaces) or (monitor,)))
        self.stop = None
        self.facts = ProtocolFacts(contract, program, retain=False, observer=self.advance)

    def advance(self, fact):
        from .ledger import SlotFact

        for cursor in self.cursors:
            local = fact
            if len(self.cursors) > 1:
                names = {endpoint.name for endpoint in cursor.monitor.contract.endpoints}
                local = SlotFact(fact.index, fact.reset, fact.channels, fact.adapter_checks,
                    tuple(item for item in fact.adapter_violations if item.endpoint in names),
                    tuple(item for item in fact.adapter_environment_violations if item.endpoint in names))
            before = len(cursor.found)
            cursor.advance(local)
            if self.stop is None and len(cursor.found) > before:
                self.stop = cursor.found[before]


class MonitorProduct:
    """The product of the existing one-domain monitors over global event slots.

    Each component observes only its own active edges, retaining the global indexes.
    Timers advance across the intervening slots without inventing protocol events.
    Coincident domains are therefore checked from one common trace without
    serialising their transitions.  Generic obligation identifiers remain one catalogue
    goal; the endpoint carried by each finding identifies the concrete component.
    """

    POLICY = "atomic-monitor-product/7"

    def __init__(self, contract):
        from .contract import TaskContract

        if type(contract) is not TaskContract:
            raise TypeError("a monitor product needs the exact TaskContract")
        if len(contract.clocks) < 2:
            raise ValueError("a monitor product needs several clock domains")
        self.contract = contract
        by_domain = {clock.name: [] for clock in contract.clocks}
        for endpoint in contract.endpoints:
            by_domain[endpoint.clock].append(endpoint)
        self._components = []
        self._reset_only = []
        for clock in contract.clocks:
            from .contract import TaskContract

            projected = TaskContract(
                tuple(by_domain[clock.name]), (clock,),
                source=contract.source, authority=contract.authority,
                protocol_instances=contract.protocol_instances_for(clock.name))
            if by_domain[clock.name]:
                self._components.append(
                    (clock.name, projected, Monitor(projected)))
            elif clock.reset_restores is True:
                self._reset_only.append(
                    (clock.name, ResetRestorationProvider(projected)))

    @staticmethod
    def _merge_goals(target, incoming):
        for name, goal in incoming.items():
            previous = target.get(name)
            if previous is not None and previous != goal:
                raise Refused(Refusal(
                    "monitor-product-goal-collision",
                    "two clock-domain monitor components assign different meanings "
                    "to one public goal identifier",
                    goal=name, conflict=previous.conflict_with(goal)))
            target[name] = goal

    def goals(self):
        goals = {}
        for _domain, _contract, monitor in self._components:
            self._merge_goals(goals, monitor.goals())
        for _domain, provider in self._reset_only:
            self._merge_goals(goals, provider.goals())
        return goals

    def assumptions(self):
        return ()

    def payload_independent_goals(self):
        return _payload_independent_goals(self)

    def unchecked(self):
        return tuple(sorted({goal for _domain, _contract, monitor
                             in self._components
                             for goal in monitor.unchecked()}))

    def goal_provider_capabilities(self):
        """Capabilities of the active-edge atomic monitor product.

        Exact bounded hits are available only when every domain component exposes the
        finite protocol transition used by the atomic RTL product (including the
        shared ordered-channel component). Reset restoration uses the same atomic
        product; binding an actual query still requires the RTL frontend's complete
        per-domain state-ownership map and the schedule's leading reset reference.
        """

        providers = self.goal_providers()
        protocol = next((provider for provider in providers
                         if provider.schema.name == "monitor-obligation"), None)
        if protocol is None:
            raise Refused(Refusal(
                "task-monitor-provider-inapplicable",
                "this task declares no protocol-monitor goal instances"))
        return protocol

    def goal_providers(self):
        exact_protocol = bool(self._components)
        for _domain, _contract, monitor in self._components:
            exact_protocol = (
                exact_protocol and _protocol_goals_have_exact_product(monitor))
        return _monitor_goal_providers(
            self, exact_protocol=exact_protocol, exact_reset=True)

    @staticmethod
    def _project(domain, contract, stimulus, trace):
        from .atomic import project_domain_trace

        return project_domain_trace(domain, contract, stimulus, trace)

    def check(self, stimulus, trace, *, reset_state_fields=None, _protocol_ledgers=None):
        reports = [_input_completion(self.contract, stimulus, trace)]
        for domain, contract, monitor in self._components:
            local_stimulus, local_trace, indexes = self._project(
                domain, contract, stimulus, trace)
            # A complete state carrier needs domain ownership before it can prove an
            # independent reset.  Until such ownership is supplied, retaining the
            # scenario and reporting the safety result unresolved is the sound answer.
            local_fields = None
            if type(reset_state_fields) is dict:
                local_fields = reset_state_fields.get(domain)
            reports.append(monitor._check_protocol(
                local_stimulus, local_trace, _slot_indexes=indexes,
                _protocol_ledgers=_protocol_ledgers))
            reports.append(_reset_report(
                monitor.reset_restoration, stimulus, trace,
                state_fields=local_fields, clock_domain=domain))

        for domain, provider in self._reset_only:
            local_fields = (reset_state_fields.get(domain)
                            if type(reset_state_fields) is dict else None)
            reports.append(_reset_report(
                provider, stimulus, trace,
                state_fields=local_fields, clock_domain=domain))

        return _merge_reports(reports)
