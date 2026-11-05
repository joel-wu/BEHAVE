"""Incremental concrete storage using the same transition as exact memory states."""

from ..memory_driver import _protocol
from .memory import MemoryEnvironmentState, MemoryState
from .refusal import Refusal, Refused


class MemoryRuntime:
    """One execution's owned memories, with prepare/commit at the sampling edge."""

    def __init__(self, interfaces, environment):
        interfaces = tuple(interfaces)
        initial = MemoryEnvironmentState.initial(interfaces, environment)
        self._memories = []
        self._ready_slot = None
        self._ready = {}
        for interface in interfaces:
            state, run = initial.state(interface.name), environment.run(interface.name)
            faults = state.faults_against(interface, run)
            if faults:
                raise Refused(Refusal(
                    "memory-state-outside-interface",
                    "an external-memory state is outside its task/run declaration",
                    memory=interface.name, first=faults[0], count=len(faults)))
            self._memories.append((interface, run, _protocol.RuntimeState(state, interface)))

    def _drive(self, interface, run, state, slot, *, reset=False):
        # Only readiness is a pure function of (run, slot). Responses still
        # come from the current committed state, including same-slot resets.
        if type(slot) is not int or slot != self._ready_slot:
            self._ready_slot, self._ready = slot, {}
        ready = self._ready.get(interface.name)
        if ready is None:
            ready = {port.id: run.ready_at(port.id, slot) for port in interface.ports}
            self._ready[interface.name] = ready
        return _protocol.drive(interface, ready, state, reset=reset)

    def drive(self, slot, *, reset_domains=()):
        driven = {}
        for interface, run, state in self._memories:
            driven.update(self._drive(interface, run, state, slot,
                                      reset=interface.clock in reset_domains))
        return driven

    def prepare(self, slot, artifact_outputs, *, active_domains=None, reset_domains=()):
        """Validate every active memory without committing any partial update."""
        active = None if active_domains is None else set(active_domains)
        updates = []
        for interface, run, state in self._memories:
            changed = state.unchanged()
            reset = interface.clock in reset_domains
            if reset or active is None or interface.clock in active:
                driven = self._drive(interface, run, state, slot, reset=reset)
                try:
                    changed = state.prepare(interface, run.read_latency, driven,
                                            artifact_outputs, slot=slot, reset=reset)
                except _protocol.MemoryProtocolError as exc:
                    raise Refused(Refusal(exc.kind, exc.why, **exc.facts)) from exc
            updates.append((interface.name, changed))
        return tuple(updates)

    def commit(self, updates):
        by_name = dict(updates)
        for interface, _run, state in self._memories:
            state.commit(by_name[interface.name])

    def snapshot(self):
        """Export a detached immutable state for inspection, not the hot loop."""
        return MemoryEnvironmentState((interface.name, MemoryState(**state.snapshot()._asdict()))
                                      for interface, _run, state in self._memories)

    @property
    def quiescent(self):
        """No delayed read or response remains at this committed boundary.

        This reads only the small protocol queues, not a copy of the memory.
        It does not assert equality of memory cells or make a scoring decision.
        """
        return all(not queue for _, _, state in self._memories for _, queue in state.pending) \
            and all(value is None for _, _, state in self._memories for _, value in state.responses)
