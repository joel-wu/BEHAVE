"""Finite monitor state for a fixed global schedule, independent of a solver.

Compose the existing core transitions; do not introduce another definition of
their goals. Exact construction and source-trace reconstruction supply different
scalar algebras. Public replay remains the independent authority for goal hits.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from types import MappingProxyType

from ..core.adapter_exact import InterfaceExactProduct
from ..core.contract import Endpoint, TaskContract
from ..core.monitor import INPUT_COMPLETION, Monitor
from ..core.physical_value import packed_width
from ..core.protocol import APB_ACCESS
from ..core.ready_valid_product import (
    CountedReadyValidState,
    OrderedReadyValidConfig,
    OrderedReadyValidEvent,
    OrderedReadyValidState,
    ordered_ready_valid_initial,
    ordered_ready_valid_state_image,
    ordered_ready_valid_step,
)
from ..core.refusal import Refusal, Refused
from ..core.reset_monitor import (
    ResetRestorationConfig,
    ResetRestorationEvent,
    ResetRestorationProvider,
    ResetRestorationState,
    reset_restoration_initial,
    reset_restoration_state_image,
    reset_restoration_step,
)


@dataclass(frozen=True)
class ProtocolMonitor:
    """One output's stability state, plus correlation only for a declared pair."""

    domain: str | None
    source: Endpoint | None
    sink: Endpoint
    config: OrderedReadyValidConfig
    goals: tuple[str, ...]

    @property
    def key(self):
        return self.domain, self.sink.name

    @property
    def payload_fields(self):
        # Control/count goals do not inspect output values. Identifier-based
        # correlation and stability retain the original complete payload.
        return self.sink.fields if self.config.payload_fields else ()

    @property
    def identifier_field(self):
        if self.config.identifier_index is None:
            return None
        return self.source.field(self.sink.fields[self.config.identifier_index].name)


def protocol_monitor_components(contract, adapter_components, queue_bound):
    """Bind shared obligations once for circuit and source-history products.

    Adapter tables already own their protocol-specific goals. Completion is a
    whole-input-environment obligation, not an ordered-channel transition.
    Correlation is added only for an explicitly supported input/output pair.
    """
    domain_monitors, components = [], []

    def leaves(domain, view, program, table):
        if isinstance(table, InterfaceExactProduct):
            for _, child_view, child, product, _ in table.parts:
                yield from leaves(domain, child_view, child, product)
        else:
            yield domain, view, program, table

    for domain, view, program, table in (
            part for component in adapter_components for part in leaves(*component)):
        projected = TaskContract(view.endpoints, view.clocks, source=contract.source,
                                 authority=contract.authority, protocol_instances=view.protocol_instances)
        monitor = Monitor(projected)
        domain_monitors.append((domain, monitor, program))
        remaining = set(monitor.goals()) - set(table.goal_names) - {INPUT_COMPLETION}
        if not remaining:
            continue
        inputs, outputs = projected.inputs(), projected.outputs()
        if len(inputs) == 1 and len(outputs) == 1:
            config = monitor.finite_channel_config(queue_bound)
            if config is not None:
                components.append(ProtocolMonitor(domain, inputs[0], outputs[0], config,
                    tuple(sorted(remaining.intersection(monitor.OBLIGATIONS)))))
                continue
        # No unique pair: retain stability without inventing response ownership.
        for sink in outputs:
            components.append(ProtocolMonitor(domain, None, sink,
                OrderedReadyValidConfig(capacity=queue_bound, payload_fields=len(sink.fields),
                    answers_per_transaction=None, max_outstanding=None, response_deadline=None),
                tuple(sorted(remaining.intersection({'monitor.sink-stability'})))))
    return tuple(domain_monitors), tuple(components)


@dataclass(frozen=True)
class AtomicMonitorState:
    protocol: Mapping[tuple[str | None, str], OrderedReadyValidState | CountedReadyValidState]
    reset: Mapping[str, ResetRestorationState]

    def __post_init__(self):
        object.__setattr__(self, "protocol", MappingProxyType(dict(self.protocol)))
        object.__setattr__(self, "reset", MappingProxyType(dict(self.reset)))

    def image(self, read=lambda value: value):
        # Two-level keys preserve domain/endpoint identity without concatenation.
        protocol, settled = {}, {}
        for (domain, endpoint), state in sorted(self.protocol.items(), key=lambda item: (item[0][0] or '', item[0][1])):
            channels = settled if domain is None else protocol.setdefault(domain, {})
            channels[endpoint] = ordered_ready_valid_state_image(state, read)
        image = {
            "monitor.ready-valid": protocol,
            "monitor.reset-restoration": {
                domain: reset_restoration_state_image(state, read)
                for domain, state in sorted(self.reset.items())},
        }
        if settled:
            image['monitor.settled'] = settled
        return image


def _enabled_state(before, after, enabled, algebra):
    """Retain every latent field when a source phase was not completed."""
    if enabled is None:
        return after

    def choose(old, new):
        if isinstance(old, tuple):
            return tuple(choose(a, b) for a, b in zip(old, new, strict=True))
        return algebra.ite(enabled, new, old)

    return type(before)(**{field.name: choose(getattr(before, field.name), getattr(after, field.name))
                           for field in fields(before)})


@dataclass(frozen=True)
class AtomicMonitorProduct:
    protocol: tuple[ProtocolMonitor, ...]
    resets: tuple[tuple[str, ResetRestorationConfig], ...]
    state_widths: Mapping[str, int]
    reset_observations: frozenset[str] | None = None

    def __post_init__(self):
        object.__setattr__(self, "state_widths", MappingProxyType(dict(self.state_widths)))
        if self.reset_observations is not None:
            object.__setattr__(self, "reset_observations", frozenset(self.reset_observations))

    def project(self, goals):
        """Keep observation owners and every domain/fault owner, before unrolling.

        Reset-restoration only observes state. Protocol monitors may ALSO own
        identifier legality or an unresolved reset-policy fault; those cannot
        be dropped merely because their safety goals are not being queried.
        """
        goals = frozenset(goals)
        protocol = []
        for component in self.protocol:
            selected = tuple(goal for goal in component.goals if goal in goals)
            config = component.config
            if not (selected or config.identifier_index is not None
                    or config.reset_policy is None and config.answers_per_transaction is not None):
                continue
            if 'monitor.sink-stability' not in selected and config.identifier_index is None:
                # The shared transition's held payload affects stability only.
                # Its counts, reset-definedness and acceptance semantics are
                # unchanged. Do not pull a datapath into a control-only query.
                config = replace(config, payload_fields=0)
            protocol.append(replace(component, goals=selected, config=config))
        # With no declared response count, the shared transition has no
        # pending-request cells at all. Its reset-policy fault is identically
        # false; keeping the unqueried stability observer would pull the whole
        # output payload into otherwise control-only queries.
        resets, observations = [], set()
        for domain, config in self.resets:
            scenario = ResetRestorationProvider.scenario_goal(domain)
            violation = ResetRestorationProvider.violation_goal(domain)
            selected = goals.intersection((scenario, violation))
            if self.reset_observations is not None and not selected <= self.reset_observations:
                raise ValueError('a projected reset observer cannot restore discarded observations')
            if not selected:
                continue
            # The scenario depends on the reset/edge history, not register
            # values. Keep the identical automaton but omit its unobserved
            # equality carrier. Never export the now-absent violation bit.
            if violation not in selected:
                config = replace(config, state_fields=())
            resets.append((domain, config))
            observations.update(selected)
        fields = {name for _, config in resets for name in config.state_fields}
        return AtomicMonitorProduct(tuple(protocol), tuple(resets),
            {name: self.state_widths[name] for name in fields}, frozenset(observations))

    def request_keys(self, payloads, pack):
        """Project keys from actual driver payloads; idle payloads cannot be accepted."""
        result = {}
        for component in self.protocol:
            field = component.identifier_field
            if field is None:
                continue
            payload = payloads.get(component.source.name)
            index = next(i for i, item in enumerate(component.source.fields) if item.name == field.name)
            result[component.source.name] = None if payload is None else pack(field.type, payload[index])
        return result

    def initial(self, algebra, word):
        """``word(value, width)`` supplies the packed scalar carrier."""
        return AtomicMonitorState({
            component.key: ordered_ready_valid_initial(
                component.config, algebra,
                tuple(word(0, packed_width(field.type)) for field in component.payload_fields))
            for component in self.protocol
        }, {
            domain: reset_restoration_initial(
                config, tuple(word(0, self.state_widths[name])
                              for name in config.state_fields), algebra)
            for domain, config in self.resets
        })

    def step(self, state, slot, event, input_fired, raw, successor, *,
             algebra, boolean, word, controls=None, sampled=None, completed=None,
             reset_terms=None, faults=None, input_keys=None, assumptions=None):
        """Advance from sampled pins/transfers and the actual RTL successor.

        Protocol automata run on their domain's edges. Reset restoration also
        observes inactive reset-level changes, without inventing clock edges.
        ``boolean`` and ``word`` only translate scalar carriers, not semantics.
        Optional ``controls`` are already in the scalar algebra, so a free
        ready signal is not coerced to a Python truth value. ``sampled`` guards
        pre-clock protocol observations; ``completed`` guards the post-step
        reset image. An unfinished interval retains the preceding monitor
        state, not a reset/empty state. Omitted guards preserve complete replay.
        Optional ``reset_terms`` supplies already-typed assertions by domain;
        the fixed event still owns clock edges, independently of reset levels.
        Exact consumers collect ``faults`` for whole-domain definedness checks;
        they must not constrain those faults away as environment assumptions.
        ``input_keys`` carries the actual driver's packed request identifiers.
        Duplicate in-flight IDs go to ``assumptions``, a separate environment
        legality image audited before it may restrict a target query.
        """
        protocol, resets, hits = dict(state.protocol), {}, {}

        def reset(domain):
            if domain is None:
                return algebra.bool(False)
            return algebra.bool(domain in event.resets) if reset_terms is None else reset_terms[domain]

        if controls is None:
            controls = {name: algebra.bool(value) for name, value in event.controls}
        for component in self.protocol:
            for goal in component.goals:
                hits.setdefault(goal, algebra.bool(False))
            if component.domain is not None and component.domain not in event.ticks:
                continue
            sink = component.sink
            accepted = (algebra.bool(False) if component.source is None else
                        input_fired.get(component.source.name, algebra.bool(False)))
            key = ()
            if component.config.identifier_index is not None:
                if input_keys is None or assumptions is None:
                    raise ValueError("identified monitors require keys and an environment legality owner")
                value = input_keys[component.source.name]
                if value is None:
                    value = algebra.zero_like(state.protocol[component.key].held[component.config.identifier_index])
                key = (value,)
            stepped = ordered_ready_valid_step(
                component.config, state.protocol[component.key],
                OrderedReadyValidEvent(
                    reset=reset(component.domain), slot=slot,
                    input_accepted=accepted,
                    output_valid=(input_fired.get(sink.name, algebra.bool(False))
                        if sink.adapter.name == APB_ACCESS else algebra.land(*(
                            boolean(raw[name]) for name in sink.adapter.artifact_controls))),
                    output_ready=algebra.land(*(
                        controls[name] for name in sink.adapter.environment_controls)),
                    output_payload=tuple(word(raw[field.port], packed_width(field.type))
                                         for field in component.payload_fields), input_key=key), algebra)
            protocol[component.key] = _enabled_state(
                state.protocol[component.key], stepped.state, sampled, algebra)
            if faults is not None and component.config.reset_policy is None:
                value = stepped.details["reset_policy_unresolved"]
                if sampled is not None:
                    value = algebra.land(sampled, value)
                faults[f"monitor.reset-policy-unresolved[{component.domain}][{sink.name}]"] = value
            if key:
                value = stepped.details["identifier_reused"]
                if sampled is not None:
                    value = algebra.land(sampled, value)
                assumptions[f"environment.identifier-reused-while-outstanding[{component.domain}][{sink.name}]"] = value
            missing = set(component.goals) - set(stepped.hits)
            if missing:
                raise Refused(Refusal(
                    "rtl-atomic-monitor-goal-unencoded",
                    "an active monitor omitted a declared goal predicate",
                    domain=component.domain, goals=sorted(missing)))
            for goal in component.goals:
                value = stepped.hits[goal]
                if sampled is not None:
                    value = algebra.land(sampled, value)
                hits[goal] = algebra.lor(hits[goal], value)
        for domain, config in self.resets:
            if completed is False:
                # A concrete source halt has no readable post-step state.
                # Preserve the same disabled transition as the symbolic guard,
                # without asking a caller to fabricate successor registers.
                resets[domain] = state.reset[domain]
                for goal in (ResetRestorationProvider.scenario_goal(domain),
                             ResetRestorationProvider.violation_goal(domain)):
                    if self.reset_observations is None or goal in self.reset_observations:
                        hits.setdefault(goal, algebra.bool(False))
                continue
            stepped = reset_restoration_step(
                config, state.reset[domain],
                ResetRestorationEvent(
                    reset=reset(domain),
                    active=algebra.bool(domain in event.ticks),
                    state_after=tuple(word(successor[name], self.state_widths[name])
                                      for name in config.state_fields),
                    state_readable=algebra.bool(True)), algebra)
            resets[domain] = _enabled_state(state.reset[domain], stepped.state, completed, algebra)
            for goal, value in (
                    (ResetRestorationProvider.scenario_goal(domain), stepped.scenario),
                    (ResetRestorationProvider.violation_goal(domain), stepped.violation)):
                if self.reset_observations is not None and goal not in self.reset_observations:
                    continue
                if completed is not None:
                    value = algebra.land(completed, value)
                hits[goal] = algebra.lor(hits.get(goal, algebra.bool(False)), value)
        return AtomicMonitorState(protocol, resets), hits
