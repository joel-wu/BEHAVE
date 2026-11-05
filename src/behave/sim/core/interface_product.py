"""Same-edge product of independently declared protocol instances.

Each component reads the same pre-edge device sample and its own pre-state.
No component executes an RTL edge or a reference call. The existing Driver
commits the merged result once; shared behavior effects use process_slot.
"""

from __future__ import annotations

from .frozen import FrozenMap
from .product import Component
from .protocol import AdapterDrive, AdapterProgram, AdapterTransition, adapter_program_for
from .refusal import Refusal, Refused


def instance_contracts(contract):
    """Project declarations, preserving membership and physical configuration."""
    from .contract import TaskContract

    for instance in contract.protocol_instances:
        endpoints = tuple(endpoint for endpoint in contract.endpoints
                          if endpoint.name in instance.endpoints)
        clocks = tuple(clock for clock in contract.clocks if clock.name == endpoints[0].clock)
        yield instance, TaskContract(
            endpoints, clocks, source=getattr(contract, "source", ""),
            authority=getattr(contract, "authority", "task"),
            protocol_instances=(instance,))


def project_run(contract, stimulus, trace):
    """Keep all slots but only this instance's queues, controls and transfers."""
    from .stimulus import AuditedTrace, EventSlot, SlotRecord, Stimulus

    names = {endpoint.name for endpoint in contract.endpoints}
    controls = set(contract.scheduled_controls())
    projected = Stimulus(
        queues={name: rows for name, rows in stimulus.queues if name in names},
        slots=[EventSlot(
            offers=tuple(name for name in slot.offers if name in names),
            controls={name: value for name, value in slot.controls if name in controls},
            reset=slot.reset, ticks=slot.ticks) for slot in stimulus.slots],
        source=stimulus.source)
    records = [SlotRecord(
        accepted={name: payload for name, payload in record.accepted if name in names},
        state=dict(record.state), outputs=dict(record.outputs), hits=dict(record.hits),
        monitor=dict(record.monitor), environment=dict(record.environment))
        for record in trace.records]
    return projected, AuditedTrace(
        projected, initial_state=dict(trace.initial_state), records=records,
        completion=trace.completion, domain="", artifact=trace.artifact)


class InterfaceProduct(AdapterProgram):
    name = "interface-product"
    version = 1
    __slots__ = ("owners", "parts")

    def __init__(self, contract):
        self.parts = tuple((instance.name, view, adapter_program_for(view))
                           for instance, view in instance_contracts(contract))
        pins = {}
        owners = {}
        for name, view, program in self.parts:
            for endpoint in view.endpoints:
                owners[endpoint.name] = (name, view, program)
                for pin in (tuple(field.port for field in endpoint.fields)
                            + endpoint.adapter.environment_controls
                            + endpoint.adapter.artifact_controls):
                    if pin in pins and pins[pin] != name:
                        raise Refused(Refusal(
                            "admission-interface-shared-pin",
                            "independent instances cannot share payload or control pins",
                            pin=pin, interfaces=sorted((name, pins[pin]))))
                    pins[pin] = name
        self.owners = FrozenMap(owners)
        self._freeze()

    @staticmethod
    def _key(name, key):
        return f"interface:{len(name)}:{name}:{key}"

    def _state(self, name, view, program, state):
        return FrozenMap({key: state[self._key(name, key)]
                          for key in program.initial_state(view)})

    def initial_state(self, contract):
        return FrozenMap({self._key(name, key): value
                          for name, view, program in self.parts
                          for key, value in program.initial_state(view).items()})

    def components(self, contract):
        return tuple(Component(self._key(name, component.name), "adapter",
                               in_novelty=component.in_novelty, why=component.why)
                     for name, view, program in self.parts
                     for component in program.components(view))

    def validate(self, contract):
        for _, view, program in self.parts:
            program.validate(view)

    def state_space(self, contract):
        raise Refused(Refusal(
            "adapter-state-enumeration-unsupported",
            "heterogeneous interface composition currently supports concrete replay"))

    def monitor_goal_declarations_for(self, contract):
        declarations = {}
        for _, view, program in self.parts:
            for goal, owner, role in program.monitor_goal_declarations_for(view):
                if goal in declarations and declarations[goal] != (owner, role):
                    raise Refused(Refusal(
                        "admission-interface-goal-conflict",
                        "composed protocol goals must retain one ownership", goal=goal))
                declarations[goal] = (owner, role)
        return tuple((goal, *value) for goal, value in sorted(declarations.items()))

    def monitor_goals_for(self, contract):
        return tuple(row[0] for row in self.monitor_goal_declarations_for(contract))

    def baseline_queue_range(self, endpoint, *, offers, samples):
        _, _, program = self.owners[endpoint.name]
        return program.baseline_queue_range(endpoint, offers=offers, samples=samples)

    def presentation_allowed(self, contract, state, endpoint):
        name, view, program = self.owners[endpoint.name]
        return program.presentation_allowed(
            view, self._state(name, view, program, state), endpoint)

    def _drive_part(self, name, view, program, state, slot, presentations, controls):
        return program.drive(
            view, self._state(name, view, program, state), slot,
            {endpoint.name: presentations.get(endpoint.name) for endpoint in view.inputs()},
            {key: controls[key] for key in view.scheduled_controls()})

    def drive(self, contract, state, slot, presented, scheduled_controls):
        controls, presentations = {}, {}
        for name, view, program in self.parts:
            drive = self._drive_part(
                name, view, program, state, slot, presented, scheduled_controls)
            controls.update(drive.controls)
            presentations.update(drive.presentations)
        return AdapterDrive(controls, presentations)

    def legal_environment_action(self, contract, state, slot, drive):
        events = []
        for name, view, program in self.parts:
            part = AdapterDrive(
                {key: drive.controls[key] for key in view.environment_controls()},
                {endpoint.name: drive.presentation(endpoint.name) for endpoint in view.inputs()})
            events.extend(program.environment_violations(
                view, self._state(name, view, program, state), slot, part))
        return tuple(events)

    def step(self, contract, state, slot, drive, signals, *, strict=True):
        successor, accepted, emitted, channels, monitor = {}, {}, {}, {}, {}
        checks, violations = set(), []
        for name, view, program in self.parts:
            part = AdapterDrive(
                {key: drive.controls[key] for key in view.environment_controls()},
                {endpoint.name: drive.presentation(endpoint.name) for endpoint in view.inputs()})
            transition = program.step(
                view, self._state(name, view, program, state), slot, part, signals, strict=strict)
            part_checks, part_violations = program.artifact_events(view, transition)
            successor.update({self._key(name, key): value for key, value in transition.state.items()})
            accepted.update(transition.accepted_inputs)
            emitted.update(transition.emitted_outputs)
            channels.update(transition.channels)
            monitor.update({self._key(name, key): value for key, value in transition.monitor.items()})
            checks.update(part_checks)
            violations.extend(part_violations)
        return AdapterTransition(
            state=successor, accepted_inputs=accepted, emitted_outputs=emitted,
            channels=channels, monitor=monitor,
            checks=tuple(sorted(checks)), violations=tuple(violations))

    def _elapse(self, contract, state, slots):
        return FrozenMap({self._key(name, key): value
                          for name, view, program in self.parts
                          for key, value in program.elapse(
                              view, self._state(name, view, program, state), slots).items()})
