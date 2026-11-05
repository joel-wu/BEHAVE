"""APB setup/access execution with one logical request at completed access.

Physical wait state is separate from accepted logical obligations. The input
queue retains the same request through setup/wait/reset; completion consumes it
and emits its response together. Response status is ordinary typed payload.
"""

from __future__ import annotations

from .failure import CandidateFault
from .bus_control import apb_control
from .ready_valid_product import ConcreteAlgebra
from .frozen import FrozenMap
from .product import Component
from .protocol import (
    APB_ACCESS,
    ARTIFACT,
    ENVIRONMENT,
    AdapterChannel,
    AdapterDrive,
    AdapterProgram,
    AdapterTransition,
    AdapterViolation,
    _directions,
    _payload,
    _presentation_mapping,
)
from .refusal import Refusal, Refused


class APBAccessV1(AdapterProgram):
    name = APB_ACCESS
    version = 1
    transaction_mapping = "completed-access/1"
    physical_configuration = True
    exact_transition_policy = "apb-completed-access/1"
    declared_monitor_state = ("request", "wait_cycles", "waiting")
    request_response_correlation = True
    generic_correlation_monitor = True
    SEQUENCE = "monitor.apb.request-sequence"
    WAIT = "monitor.apb.wait-limit"
    monitor_goals = (SEQUENCE,)
    monitor_goal_declarations = ((SEQUENCE, ENVIRONMENT, "in"),)

    @staticmethod
    def _instance(contract):
        if len(contract.protocol_instances) != 1:
            raise Refused(Refusal(
                "adapter-apb-instance-required",
                "APB requires one instance with explicit select/enable/ready roles"))
        return contract.protocol_instances[0]

    def validate(self, contract):
        inputs, outputs = _directions(contract)
        if len(inputs) != 1 or len(outputs) != 1:
            raise Refused(Refusal(
                "adapter-apb-endpoints", "APB maps one request to one response"))
        source, sink = inputs[0], outputs[0]
        instance = self._instance(contract)
        pins = instance.bindings
        if (set(pins) != {"select", "enable", "ready"}
                or set(source.adapter.environment_controls) != {pins["select"], pins["enable"]}
                or source.adapter.artifact_controls or sink.adapter.environment_controls
                or sink.adapter.artifact_controls != (pins["ready"],)):
            raise Refused(Refusal(
                "adapter-apb-bindings",
                "APB binds environment select/enable and artifact ready explicitly"))
        if (getattr(source.kind, "value", source.kind) != "transaction"
                or getattr(sink.kind, "value", sink.kind) != "transaction"
                or sink.answers_per_transaction != 1
                or getattr(sink.matching, "value", sink.matching) != "ordered"):
            raise Refused(Refusal(
                "adapter-apb-logical-mapping",
                "completed APB accesses have one ordered transaction response"))
        if source.response_deadline is not None:
            raise Refused(Refusal(
                "adapter-apb-deadline-domain",
                "use max_wait_cycles for pre-completion access waits, not an "
                "accepted-request response deadline"))
        if source.reset_policy is None or source.reset_policy is not sink.reset_policy:
            raise Refused(Refusal(
                "adapter-apb-reset-policy",
                "APB endpoints must declare the same reset policy"))
        # Control state snapshots carry the scalar bus request, not host objects.
        if any(getattr(field.type, "kind", None) in ("array", "record") for endpoint in (source, sink)
               for field in endpoint.fields):
            raise Refused(Refusal(
                "adapter-apb-payload-type", "APB bus fields are finite scalar values"))

    def monitor_goals_for(self, contract):
        return tuple(row[0] for row in self.monitor_goal_declarations_for(contract))

    def monitor_goal_declarations_for(self, contract):
        return self.monitor_goal_declarations + (
            ((self.WAIT, ARTIFACT, "out"),)
            if self._instance(contract).max_wait_cycles is not None else ())

    def initial_state(self, contract):
        return FrozenMap({"adapter.waiting": False, "adapter.wait_cycles": 0,
                          "adapter.request": None})

    def state_space(self, contract):
        raise Refused(Refusal(
            "adapter-state-enumeration-unsupported",
            "APB uses the shared arithmetic product, not exhaustive state enumeration"))

    def components(self, contract):
        return tuple(Component("adapter." + name, "adapter", why=why) for name, why in (
            ("waiting", "a setup has occurred and access has not completed"),
            ("wait_cycles", "saturated access wait count under the task bound"),
            ("request", "the payload held from setup through completion")))

    def baseline_queue_range(self, endpoint, *, offers, samples):
        # Even a zero-wait access needs setup and completion on distinct edges.
        return 1, min(offers, samples // 2)

    def drive(self, contract, state, slot, presented, scheduled_controls):
        source = contract.inputs()[0]
        presentations = dict(_presentation_mapping(contract, presented))
        if slot.reset:
            presentations[source.name] = None
        pins = self._instance(contract).bindings
        selected = presentations[source.name] is not None
        controls = dict(scheduled_controls)
        controls[pins["select"]] = int(selected)
        controls[pins["enable"]] = int(selected and state["adapter.waiting"])
        return AdapterDrive(controls, presentations)

    def legal_environment_action(self, contract, state, slot, drive):
        if slot.reset:
            return ()
        source = contract.inputs()[0]
        pins = self._instance(contract).bindings
        payload = drive.presentation(source.name)
        select, enable = (drive.controls.get(pins[role]) for role in ("select", "enable"))
        expected = (int(payload is not None), int(payload is not None and state["adapter.waiting"]))
        stable = not state["adapter.waiting"] or payload == state["adapter.request"]
        if (select, enable) != expected or not stable:
            return (AdapterViolation(
                self.SEQUENCE, source.name,
                "setup/access controls and request must persist until completion or reset"),)
        return ()

    def step(self, contract, state, slot, drive, signals, *, strict=True):
        source, sink = contract.inputs()[0], contract.outputs()[0]
        instance = self._instance(contract)
        selected = drive.controls[instance.bindings["select"]] == 1 and not slot.reset
        access = selected and state["adapter.waiting"]
        sample = signals.get(instance.bindings["ready"])
        readable = type(sample) is int and sample in (0, 1)
        if strict and access and not readable:
            raise CandidateFault(Refusal(
                "driver-handshake-unreadable", "PREADY is unreadable during APB access",
                endpoint=sink.name))
        control = apb_control(
            state["adapter.waiting"], state["adapter.wait_cycles"], selected,
            readable and sample == 1, instance.max_wait_cycles, ConcreteAlgebra, readable=readable)
        complete = control.complete
        payload, data_readable, why = _payload(sink, signals) if complete else (None, True, "")
        if complete and not data_readable:
            raise CandidateFault(Refusal(
                "driver-output-unreadable", "completed APB response has unreadable fields",
                endpoint=sink.name, detail=why))
        checks, violations = [], []
        limit = instance.max_wait_cycles
        if access and limit is not None:
            checks.append(self.WAIT)
            if readable and sample == 0:
                if control.wait_hit:
                    violations.append(AdapterViolation(
                        self.WAIT, sink.name, f"APB access exceeded {limit} wait cycles"))
        waiting = control.waiting
        next_state = {"adapter.waiting": waiting,
                      "adapter.wait_cycles": control.waits,
                      "adapter.request": drive.presentation(source.name) if waiting else None}
        monitor = dict(next_state)
        monitor.update({f"adapter.checked:{goal}": 1 for goal in checks})
        monitor.update({f"adapter.violation:{item.goal}": item.why for item in violations})
        return AdapterTransition(
            state=next_state,
            accepted_inputs={source.name: drive.presentation(source.name)} if complete else {},
            emitted_outputs={sink.name: payload} if complete else {},
            channels={
                source.name: AdapterChannel(
                    offered=selected, ready=bool(complete), transferred=bool(complete),
                    payload=drive.presentation(source.name), protocol_readable=not access or readable),
                sink.name: AdapterChannel(
                    offered=bool(complete), ready=None, transferred=bool(complete),
                    payload=payload, protocol_readable=not access or readable,
                    payload_readable=data_readable, why_unreadable=why)},
            checks=tuple(checks), violations=tuple(violations), monitor=monitor)
