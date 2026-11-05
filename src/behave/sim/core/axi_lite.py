"""Single-outstanding AXI4-Lite read/write machines, composed on one edge.

A write is one logical input only after both independent AW and W handshakes.
A read is accepted at AR. B/R are separate, held responses. The Driver owns
logical queues; these machines own only physical assembly and response state.
"""

from __future__ import annotations

from .failure import CandidateFault
from .bus_control import axi_lite_control
from .ready_valid_product import ConcreteAlgebra
from .frozen import FrozenMap
from .product import Component
from .protocol import (
    ARTIFACT,
    AXI_LITE_READ,
    AXI_LITE_WRITE,
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


class _AXILiteV1(AdapterProgram):
    version = 1
    transaction_mapping = "single-outstanding/1"
    physical_configuration = True
    exact_transition_policy = "axi-lite-single-outstanding/1"
    request_response_correlation = True
    generic_correlation_monitor = True
    stalled_offer_stability = True
    declared_monitor_state = ("accepted_channels", "pending", "request", "wait_cycles")
    request_channels = ()
    response_channel = ""
    request_fields = ()
    response_fields = ()
    SEQUENCE = "monitor.axil.request-sequence"
    EARLY = "monitor.axil.response-before-request"
    CODE = "monitor.axil.response-code"
    WAIT = "monitor.axil.request-wait-limit"
    monitor_goals = (SEQUENCE, EARLY, CODE)
    monitor_goal_declarations = (
        (SEQUENCE, ENVIRONMENT, "in"),
        (EARLY, ARTIFACT, "out"),
        (CODE, ARTIFACT, "out"),
    )

    @staticmethod
    def _instance(contract):
        if len(contract.protocol_instances) != 1:
            raise Refused(Refusal(
                "adapter-axil-instance-required", "AXI4-Lite needs explicit role bindings"))
        return contract.protocol_instances[0]

    def validate(self, contract):
        from .contract import Kind, Matching, ResetPolicy

        inputs, outputs = _directions(contract)
        if len(inputs) != 1 or len(outputs) != 1:
            raise Refused(Refusal(
                "adapter-axil-endpoints", "each AXI4-Lite path has one request/response pair"))
        source, sink = inputs[0], outputs[0]
        if (source.kind is not Kind.TRANSACTION or sink.kind is not Kind.TRANSACTION
                or sink.matching is not Matching.ORDERED or sink.answers_per_transaction != 1
                or source.max_outstanding != 1):
            raise Refused(Refusal(
                "adapter-axil-logical-mapping",
                "single-outstanding/1 requires one ordered response and max_outstanding=1"))
        if source.reset_policy is not ResetPolicy.CLEARS or sink.reset_policy is not ResetPolicy.CLEARS:
            raise Refused(Refusal(
                "adapter-axil-reset-policy", "this AXI4-Lite mapping clears accepted work at reset"))
        pins = self._instance(contract).bindings
        controls = {channel + suffix for channel in (*self.request_channels, self.response_channel)
                    for suffix in ("valid", "ready")}
        required = controls | set(self.request_fields) | set(self.response_fields)
        if set(pins) not in (required, required | {"protection"}):
            raise Refused(Refusal(
                "adapter-axil-bindings", "every control and payload role needs an explicit pin"))
        request_roles = self.request_fields + (("protection",) if "protection" in pins else ())
        if (set(source.adapter.environment_controls) != {pins[c + "valid"] for c in self.request_channels}
                or set(source.adapter.artifact_controls) != {pins[c + "ready"] for c in self.request_channels}
                or sink.adapter.environment_controls != (pins[self.response_channel + "ready"],)
                or sink.adapter.artifact_controls != (pins[self.response_channel + "valid"],)
                or {field.port for field in source.fields} != {pins[role] for role in request_roles}
                or {field.port for field in sink.fields} != {pins[role] for role in self.response_fields}):
            raise Refused(Refusal(
                "adapter-axil-role-ownership", "AXI4-Lite roles must match endpoint pins and owners"))
        fields = {field.port: field.type for endpoint in (source, sink) for field in endpoint.fields}
        if any(value.kind != "integer" or value.signed for value in fields.values()):
            raise Refused(Refusal(
                "adapter-axil-field-type", "AXI4-Lite bus fields are unsigned finite bit patterns"))
        width = fields[pins["data"]].width
        if (width not in (32, 64) or fields[pins["response"]].width != 2
                or ("strobe" in pins and fields[pins["strobe"]].width != width // 8)
                or ("protection" in pins and fields[pins["protection"]].width != 3)):
            raise Refused(Refusal(
                "adapter-axil-field-width", "AXI4-Lite uses 32/64-bit data, byte strobes and 2-bit status"))

    def initial_state(self, contract):
        return FrozenMap({"adapter.accepted_channels": (), "adapter.pending": False,
                          "adapter.request": None, "adapter.wait_cycles": 0})

    def components(self, contract):
        return tuple(Component("adapter." + name, "adapter", why=why) for name, why in (
            ("accepted_channels", "independent request channels already handshaken"),
            ("pending", "one assembled request awaiting response transfer"),
            ("request", "held request during physical channel assembly"),
            ("wait_cycles", "saturated task-bounded request assembly wait")))

    def state_space(self, contract):
        raise Refused(Refusal(
            "adapter-state-enumeration-unsupported",
            "AXI4-Lite uses the shared arithmetic product, not exhaustive state enumeration"))

    def monitor_goals_for(self, contract):
        return tuple(row[0] for row in self.monitor_goal_declarations_for(contract))

    def monitor_goal_declarations_for(self, contract):
        return self.monitor_goal_declarations + (
            ((self.WAIT, ARTIFACT, "in"),)
            if self._instance(contract).max_wait_cycles is not None else ())

    def baseline_queue_range(self, endpoint, *, offers, samples):
        return 1, min(offers, (samples + 1) // 2)

    def receptive_controls(self, endpoint):
        return endpoint.adapter.environment_controls

    def presentation_allowed(self, contract, state, endpoint):
        return not state["adapter.pending"]

    def drive(self, contract, state, slot, presented, scheduled_controls):
        source = contract.inputs()[0]
        presentations = dict(_presentation_mapping(contract, presented))
        if slot.reset or state["adapter.pending"]:
            presentations[source.name] = None
        pins = self._instance(contract).bindings
        controls = dict(scheduled_controls)
        offered = presentations[source.name] is not None
        for channel in self.request_channels:
            controls[pins[channel + "valid"]] = int(
                offered and channel not in state["adapter.accepted_channels"])
        return AdapterDrive(controls, presentations)

    def legal_environment_action(self, contract, state, slot, drive):
        if slot.reset:
            return ()
        pins, source = self._instance(contract).bindings, contract.inputs()[0]
        expected = self.drive(contract, state, slot, dict(drive.presentations), dict(drive.controls))
        held = state["adapter.request"]
        if (any(drive.controls.get(pins[c + "valid"]) != expected.controls[pins[c + "valid"]]
                for c in self.request_channels)
                or (held is not None and held != drive.presentation(source.name))):
            return (AdapterViolation(
                self.SEQUENCE, source.name, "request fields and unaccepted channel valids must persist"),)
        return ()

    def step(self, contract, state, slot, drive, signals, *, strict=True):
        source, sink = contract.inputs()[0], contract.outputs()[0]
        instance = self._instance(contract)
        pins = instance.bindings
        if slot.reset:
            return AdapterTransition(state=self.initial_state(contract), channels={
                source.name: AdapterChannel(offered=False, ready=False, transferred=False),
                sink.name: AdapterChannel(offered=False, ready=False, transferred=False)})

        request = drive.presentation(source.name)
        request_ready = []
        request_readable = True
        for channel in self.request_channels:
            accepted = False
            if drive.controls[pins[channel + "valid"]]:
                value = signals.get(pins[channel + "ready"])
                readable = type(value) is int and value in (0, 1)
                request_readable &= readable
                if strict and not readable:
                    raise CandidateFault(Refusal(
                        "driver-handshake-unreadable", "AXI4-Lite request READY is unreadable",
                        endpoint=source.name, signal=pins[channel + "ready"]))
                if readable and value:
                    accepted = True
            request_ready.append(accepted)
        valid = signals.get(pins[self.response_channel + "valid"])
        response_readable = type(valid) is int and valid in (0, 1)
        if strict and not response_readable:
            raise CandidateFault(Refusal(
                "driver-handshake-unreadable", "AXI4-Lite response VALID is unreadable", endpoint=sink.name))
        offered = response_readable and valid == 1
        ready = drive.controls[pins[self.response_channel + "ready"]] == 1
        payload, data_readable, why = _payload(sink, signals) if offered else (None, True, "")
        if strict and offered and not data_readable:
            raise CandidateFault(Refusal(
                "driver-output-unreadable", "AXI4-Lite response payload is unreadable", endpoint=sink.name))
        control = axi_lite_control(
            tuple(c in state["adapter.accepted_channels"] for c in self.request_channels),
            state["adapter.pending"], state["adapter.wait_cycles"], request is not None,
            request_ready, offered, ready and data_readable,
            signals.get(pins["response"]), instance.max_wait_cycles, ConcreteAlgebra)
        assembled, emitted = control.assembled, control.emitted
        checks, violations = [self.EARLY], []
        if offered:
            # VALID is a pre-edge fact: a request assembled on THIS edge cannot
            # justify a response that was already asserted before that edge.
            if control.early_hit:
                violations.append(AdapterViolation(
                    self.EARLY, sink.name, "response offered without a previously assembled request"))
            if data_readable:
                checks.append(self.CODE)
                if control.code_hit:
                    violations.append(AdapterViolation(
                        self.CODE, sink.name, "EXOKAY is not an AXI4-Lite response"))

        if request is not None and instance.max_wait_cycles is not None:
            checks.append(self.WAIT)
            if not assembled:
                if control.wait_hit:
                    violations.append(AdapterViolation(
                        self.WAIT, source.name, "request channel assembly exceeded its task wait bound"))
        successor = {
            "adapter.accepted_channels": tuple(sorted(c for c, seen in
                zip(self.request_channels, control.seen) if seen)),
            "adapter.pending": control.pending,
            "adapter.request": request if control.held else None,
            "adapter.wait_cycles": control.waits,
        }
        return AdapterTransition(
            state=successor,
            accepted_inputs={source.name: request} if assembled else {},
            emitted_outputs={sink.name: payload} if emitted else {},
            channels={
                source.name: AdapterChannel(
                    offered=request is not None, ready=bool(assembled), transferred=bool(assembled),
                    payload=request, protocol_readable=request_readable),
                sink.name: AdapterChannel(
                    offered=offered, ready=ready, transferred=emitted, payload=payload,
                    protocol_readable=response_readable, payload_readable=data_readable,
                    why_unreadable=why)},
            checks=tuple(checks), violations=tuple(violations), monitor=successor)


class AXILiteWriteV1(_AXILiteV1):
    name = AXI_LITE_WRITE
    request_channels = ("aw", "w")
    response_channel = "b"
    request_fields = ("address", "data", "strobe")
    response_fields = ("response",)


class AXILiteReadV1(_AXILiteV1):
    name = AXI_LITE_READ
    request_channels = ("ar",)
    response_channel = "r"
    request_fields = ("address",)
    response_fields = ("data", "response")
