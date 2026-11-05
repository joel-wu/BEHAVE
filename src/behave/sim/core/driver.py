"""Execute the task's declared protocol, once, for whoever owns the clock.

``Driver`` binds the contract's versioned adapter and asks it what to drive before a
slot settles and what was accepted or emitted afterwards.  The cocotb loop owns clock
edges and an in-memory device owns ordinary calls, but neither reimplements ready/valid,
start/done, or a later protocol.  ``Driver`` itself never awaits or reads a signal.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contract import Kind, Matching
from .input_queue import InputQueueState, PreparedInputs
from .product import Component, ProductLayout, ProductState, reconcile
from .protocol import AdapterDrive, adapter_program_for
from .record_value import same_record_value
from .refusal import Refusal, Refused
from .stimulus import (
    AuditedTrace,
    Completion,
    SlotRecord,
    TraceDomainFields,
    trace_domain,
    uses_global_events,
)

__all__ = ["Driver", "check_channel_shape", "run_in_memory"]


def _same_pairs(pairs, values):
    """Compare frozen fields with the driver's validated string-keyed mapping."""
    return len(pairs) == len(values) and all(
        name in values and same_record_value(value, values[name])
        for name, value in pairs)


@dataclass(slots=True)
class _PreparedSlot:
    """One pre-transition view; never contains samples or an accepted transfer."""

    bindings: tuple[object, ...]
    inputs: PreparedInputs
    drive: AdapterDrive | None = None


class Driver:
    """One stimulus, one contract, stepped a slot at a time."""

    def __init__(self, contract, stimulus, artifact_identity="", *,
                 trace_domain_fields=None, execution_policy=None, expected_outputs=None):
        if stimulus.replay_policy is not None:
            if execution_policy is not None and execution_policy != stimulus.replay_policy:
                raise ValueError("the request and driver name different replay policies")
            execution_policy = stimulus.replay_policy
        #: Which artifact this run is of, written into the trace it produces. Empty
        #: where the runner cannot say, which the checks downstream treat as
        #: unverifiable rather than as agreement.
        self.artifact_identity = str(artifact_identity)
        if trace_domain_fields is not None \
                and type(trace_domain_fields) is not TraceDomainFields:
            raise TypeError(
                "a driver trace domain must be exact TraceDomainFields or absent")
        if trace_domain_fields is not None \
                and trace_domain_fields.horizon != len(stimulus.slots):
            raise Refused(Refusal(
                "driver-trace-domain-horizon-mismatch",
                "the frozen trace-domain horizon and this stimulus's slot count "
                "differ, so the replay would record a member of another domain",
                horizon=trace_domain_fields.horizon,
                slots=len(stimulus.slots)))
        self.trace_domain_fields = trace_domain_fields
        if execution_policy is not None and trace_domain_fields is not None:
            raise ValueError("transaction execution cannot also fix an analysis domain")
        inputs, outputs = contract.inputs(), contract.outputs()
        if uses_global_events(contract, stimulus):
            raise Refused(Refusal(
                "driver-needs-one-clock-domain",
                "the implicit driver advances one clock per slot; explicitly timed "
                "events use the atomic global-event driver",
                clocks=[clock.name for clock in contract.clocks]))
        faults = stimulus.faults_against(contract)
        if faults:
            raise Refused(Refusal(
                "driver-stimulus-not-of-this-contract",
                "this stimulus does not describe a run of this contract, so driving it "
                "would produce a record of something the task did not ask for",
                faults=faults, total=len(faults)))
        self.contract = contract
        self.stimulus = stimulus
        self._runtime_schedule = None
        if execution_policy is not None:
            from .runtime_schedule import TransactionSchedule

            self._runtime_schedule = TransactionSchedule(
                contract, stimulus, execution_policy, expected_outputs=expected_outputs)
        self.inputs = tuple(inputs)
        self.outputs = tuple(outputs)
        #: Historical singular endpoint views.  They deliberately become ``None``
        #: when several endpoints exist rather than silently selecting the first.
        self.source = inputs[0] if len(inputs) == 1 else None
        self.sink = outputs[0] if len(outputs) == 1 else None
        self.clock = contract.clocks[0] if contract.clocks else None
        self.protocol = adapter_program_for(contract)
        self._protocol_facts = None
        self._runtime_monitor = None
        if self._runtime_schedule is not None:
            from .monitor import RuntimeProtocolMonitor

            self._runtime_monitor = RuntimeProtocolMonitor(contract, self.protocol)
            self._protocol_facts = self._runtime_monitor.facts
        from .input_framing import InputDomainCursor

        self._input_domain = InputDomainCursor(contract)
        self.adapter_state = self.protocol.initial_state(contract)
        #: The controls a **slot** carries. An input's presentation pulse is not among
        #: them: the adapter derives it from `present`, so one pin has one authority.
        #: See `TaskContract.scheduled_controls`.
        self.names = tuple(contract.scheduled_controls())
        self._queue_state = InputQueueState.initial(inputs)
        self._prepared_slot = None
        self._pointer_component = (
            "driver.pointer" if len(inputs) == 1 else "driver.pointers")
        self._released_component = "driver.released"
        #: The historical scalar projection is valid only for an explicitly declared
        #: one-answer correlation.  Endpoint counts alone do not say that an output
        #: answers an input, and ratios greater than one need the full monitor state.
        scalar_correlation = False
        if len(inputs) == 1 and len(outputs) == 1 \
                and self.protocol.request_response_correlation \
                and outputs[0].answers_per_transaction == 1:
            sink = outputs[0]
            scalar_correlation = sink.matching is Matching.ORDERED or (
                sink.matching is Matching.IDENTIFIER
                and sink.identifier is not None
                and inputs[0].field(sink.identifier) is not None)
        self._has_scalar_outstanding = scalar_correlation
        self._response_ratio = (self._runtime_schedule.response_ratio
                                if self._runtime_schedule is not None else None)
        self._track_response_counts = (scalar_correlation
                                       or self._response_ratio is not None)
        # Accepted/emitted totals since the last clearing reset.  Production stepping
        # advances this pair once per slot; `_in_flight(records)` remains the slow,
        # history-derived oracle used by differential tests.
        self._outstanding_counts = (0, 0)
        self._emitted_counts = dict.fromkeys((endpoint.name for endpoint in outputs), 0)
        self.records = []
        #: `(slot, why, goal)` where the artifact failed terminally, or `None`.
        self.failed = None
        #: What this loop declares it has, by owner. The artifact contributes its own
        #: components through `device_manifest`, so a device that grows a field and does
        #: not declare it makes the reconciliation fail rather than carrying it along.
        self.manifest = [
            Component(
                self._pointer_component, "driver",
                why=("how far into the input queue the transfers have got"
                     if len(inputs) == 1 else
                     "how far each input endpoint has advanced independently")),
            Component(
                self._released_component, "driver",
                why="how much of each input queue the schedule has released"),
            Component("driver.slot", "driver", why="which slot the loop is at"),
        ]
        if self._has_scalar_outstanding:
            self.manifest.append(Component(
                "monitor.outstanding", "monitor",
                why="accepted and not yet answered"))
        self._component_binding = (self.protocol, contract)
        self._protocol_components = tuple(self.protocol.components(contract))
        self.manifest.extend(self._protocol_components)
        self._product_layout = None
        # Reconcile every transition, but retain only the current product state.
        # The trace owns execution history; exact analyses build their own state
        # sequences. Keeping every transient product here duplicates that history
        # even when the concrete caller never reads it.
        self.last_state: ProductState | None = None

    def iter_slots(self):
        """Execute a fixed schedule or generate slots from actual queue progress.

        Both modes use the same protocol and queue transition. Runtime mode
        records a finite schedule at completion; it never pads successful runs.
        """
        if self._runtime_schedule is None:
            yield from enumerate(self.stimulus.slots)
            return
        while True:
            index = len(self._runtime_schedule.slots)
            if index != len(self.records):
                raise ValueError("observe the current slot before requesting the next")
            taken, given = self._outstanding_counts
            responses_pending = (self._response_ratio is not None
                                 and given < taken * self._response_ratio)
            if self._runtime_schedule.expected_outputs is not None:
                responses_pending = any(self._emitted_counts[name] < count
                    for name, count in self._runtime_schedule.expected_outputs.items())
            slot = self._runtime_schedule.slot(
                self.pointers, self.released, responses_pending=responses_pending)
            if slot is None:
                return
            yield index, slot

    def _slot(self, index):
        slots = (self.stimulus.slots if self._runtime_schedule is None
                 else self._runtime_schedule.slots)
        return slots[index]

    @property
    def protocol_stop(self):
        return None if self._runtime_monitor is None else self._runtime_monitor.stop

    @property
    def pointers(self):
        """Accepted-transfer offsets, independently named by input endpoint."""
        return self._queue_state.accepted

    def progress_counts(self):
        """Observed transfers, not successfully checked transactions."""
        return {"slots": len(self.records), "accepted": dict(self.pointers),
                "emitted": dict(self._emitted_counts)}

    @property
    def released(self):
        """Released queue-prefix lengths, independently named by input endpoint."""
        return self._queue_state.released

    @property
    def taken(self):
        """The historical scalar pointer, available for a unique input only."""
        if len(self.inputs) != 1:
            raise Refused(Refusal(
                "driver-pointer-ambiguous",
                "a scalar input pointer requires exactly one input endpoint",
                inputs=[endpoint.name for endpoint in self.inputs]))
        return self._queue_state.accepted[self.inputs[0].name]

    def declare(self, components):
        """Add an artifact's or environment's components to the manifest.

        Before the first slot. A component declared later would be one the earlier
        transitions were reconciled without, and those reconciliations would have passed
        for the wrong reason.
        """
        if self.records:
            raise Refused(Refusal(
                "driver-manifest-late",
                "components have to be declared before the run starts; one added later "
                "was absent from the transitions already reconciled, and those passed "
                "because nobody was looking for it",
                adding=[c.name for c in components], slots=len(self.records)))
        self.manifest.extend(components)
        self._product_layout = None

    def state(self, extra=None, *, records=None, queue_state=None,
              adapter_state=None, slot_count=None, outstanding=None):
        """The committed or proposed product state.

        ``observe`` reconciles a proposed transition before publishing its immutable
        :class:`SlotRecord`. The pointer, adapter, slot and outstanding overrides let it
        see the complete proposed successor without partially committing the driver.

        Passing ``records`` explicitly selects the history-derived debug oracle. The
        production path never does so and therefore does not rescan or copy its trace.
        """
        if records is not None:
            if slot_count is not None or outstanding is not None:
                raise TypeError(
                    "record-derived driver state cannot override its slot or count")
            slot_count = len(records)
            if self._has_scalar_outstanding:
                outstanding = self._in_flight(records)
        else:
            slot_count = len(self.records) if slot_count is None else slot_count
            if self._has_scalar_outstanding and outstanding is None:
                accepted, emitted = self._outstanding_counts
                outstanding = max(0, accepted - emitted)
        queue_state = self._queue_state if queue_state is None else queue_state
        if type(queue_state) is not InputQueueState:
            raise TypeError("driver state needs exact InputQueueState")
        adapter_state = (self.adapter_state if adapter_state is None
                         else adapter_state)
        pointer_value = (queue_state.accepted[self.inputs[0].name]
                         if len(self.inputs) == 1 else dict(queue_state.accepted))
        released_value = (queue_state.released[self.inputs[0].name]
                          if len(self.inputs) == 1 else dict(queue_state.released))
        values = {self._pointer_component: pointer_value,
                  self._released_component: released_value,
                  "driver.slot": slot_count}
        if self._has_scalar_outstanding:
            values["monitor.outstanding"] = outstanding
        values.update(self.protocol.state_values(adapter_state))
        values.update(dict(extra or {}))
        declarations = tuple(self.manifest)
        layout = self._product_layout
        if (layout is None or not layout.immutable
                or layout.declarations != declarations):
            layout = self._product_layout = ProductLayout(declarations)
        return ProductState(layout, values)

    def _adapter_components(self):
        """Declarations belong to the bound program/contract, not to a slot."""
        program, contract = self._component_binding
        if (type(self) is not Driver or self.protocol is not program
                or self.contract is not contract):
            self._component_binding = (self.protocol, self.contract)
            self._protocol_components = tuple(self.protocol.components(self.contract))
        return self._protocol_components

    def _in_flight(self, records=None):
        """Derive the scalar count from a complete prefix; the differential oracle."""
        if not self._has_scalar_outstanding:
            raise Refused(Refusal(
                "driver-outstanding-relation-undeclared",
                "several independent endpoints do not declare which output retires "
                "which input, so there is no scalar outstanding count",
                inputs=[endpoint.name for endpoint in self.inputs],
                outputs=[endpoint.name for endpoint in self.outputs]))
        records = self.records if records is None else records
        source, sink = self.source, self.sink
        taken = given = 0
        for i, record in enumerate(records):
            if self._slot(i).reset:
                taken, given = self.protocol.outstanding_after_reset(
                    self.contract, taken, given)
                continue
            for name, payload in record.accepted:
                if payload is None:
                    continue
                if source is not None and name == source.name:
                    taken += 1
                if sink is not None and name == sink.name:
                    given += 1
        return max(0, taken - given)

    def _outstanding_after(self, slot, accepted):
        """Increment the scalar count authority by one proposed transition."""

        taken, given = self._outstanding_counts
        if slot.reset:
            return self.protocol.outstanding_after_reset(
                self.contract, taken, given)
        for name, payload in accepted.items():
            if payload is None:
                continue
            if self.source is not None and name == self.source.name:
                taken += 1
            if self.sink is not None and name == self.sink.name:
                given += 1
        return taken, given

    # ── before the cycle settles ──

    def _prepared_inputs(self, index):
        slot = self._slot(index)
        if slot.reset:
            releasable = presentable = ()
        else:
            releasable = {endpoint.name for endpoint in self.inputs}
            presentable = {
                endpoint.name for endpoint in self.inputs
                if self.protocol.presentation_allowed(
                    self.contract, self.adapter_state, endpoint)
            }
        return self._queue_state.prepare(
            self.stimulus, slot, releasable=releasable,
            presentable=presentable)

    def _prepare_slot(self, index):
        # The contract, stimulus and program are immutable bindings; queue and
        # adapter states are immutable pre-transition images. Keep their strong
        # references, not id() values that could be recycled. An unchanged slot,
        # queue and adapter state have exactly the same drive even on a later
        # cycle. Keep only that one view; observations and monitors still advance
        # every cycle. Subclasses keep fresh evaluation of their mutable views.
        bindings = (self.contract, self.stimulus, self.protocol, self.inputs,
                    self.names, self.clock, self._queue_state, self.adapter_state,
                    self._slot(index))
        prepared = self._prepared_slot
        if (type(self) is Driver and prepared is not None
                and all(old is new for old, new in zip(prepared.bindings, bindings))):
            return prepared
        inputs = self._prepared_inputs(index)
        for endpoint in self.inputs:
            payload = inputs.presentations[endpoint.name]
            bad = endpoint.admits(payload) if payload is not None else ()
            if bad:
                raise Refused(Refusal(
                    "driver-payload-outside-contract",
                    "the stimulus presents a value this endpoint's declared finite type "
                    "does not admit, and driving it would put a value on the bus the "
                    "contract cannot express",
                    slot=index, endpoint=endpoint.name, faults=bad))
        prepared = _PreparedSlot(bindings, inputs)
        self._prepared_slot = prepared
        return prepared

    def controls(self, index):
        """Every environment-owned signal for this slot, and where each one comes from.

        Two sources, one per pin:

        * **scheduled** controls, such as receiver ``ready`` and reset, come from the
          slot;
        * an input presentation control, such as ``valid`` or ``start``, is derived by
          the selected adapter from :meth:`present` and is not a second slot authority.

        The second used to be in the slot as well, and the two could disagree: a slot
        copied from the cycle before it recorded `in_valid = 1` on an idle bus, the
        testbench drove 0 because nothing was presented, and the fingerprint bound a
        control sequence that never ran. One authority per pin, and the record is what
        ran because it is computed from the same call the bus is driven from.
        """
        drive = self._drive(index, self._prepare_slot(index))
        # Public callers own the returned dict, not the retained control image.
        return dict(drive.controls)

    def _drive(self, index, prepared):
        """The immutable drive shared by pin driving and observation of a slot."""
        if prepared.drive is None:
            slot = self._slot(index)
            declared = dict(slot.controls)
            scheduled = {n: int(declared[n]) for n in self.names}
            drive = self.protocol.drive(
                self.contract, self.adapter_state, slot,
                (prepared.inputs.presentations if type(self) is Driver
                 else self.presentations(index)), scheduled)
            got = drive.controls
            if slot.reset and got[self.clock.reset] == (
                    1 if self.clock.reset_active_low else 0):
                raise Refused(Refusal(
                    "driver-reset-slot-not-asserted",
                    "this slot is marked as a reset and drives the reset signal at its "
                    "released level for the polarity the contract declares",
                    slot=index, signal=self.clock.reset, drives=got[self.clock.reset],
                    active_low=self.clock.reset_active_low))
            prepared.drive = drive
        return prepared.drive

    def presentations(self, index):
        """Every input endpoint's payload for this slot, including idle ``None``.

        Each endpoint holds its own queue head until that endpoint transfers.  All
        entries are computed from the same pre-slot pointer image, so simultaneous
        handshakes are one product transition rather than an endpoint iteration order.
        """
        return self._prepare_slot(index).inputs.presentations

    def present(self, index):
        """The historical scalar presentation, for a unique input endpoint.

        ``None`` remains the output-only/idle value.  With several inputs, choosing one
        would erase a same-slot presentation and is therefore a typed ambiguity.
        """
        if not self.inputs:
            return None
        if len(self.inputs) != 1:
            raise Refused(Refusal(
                "driver-presentation-ambiguous",
                "a scalar presentation cannot name several input endpoints",
                slot=index, inputs=[endpoint.name for endpoint in self.inputs]))
        return self.presentations(index)[self.inputs[0].name]

    # ── after it settles ──

    def observe(self, index, presented, signals, device_state=None,
                device_after=None, device_changed=None):
        """What happened this slot, from the artifact-driven signals at the read point.

        ``signals`` maps a signal name to what the artifact drove.  The selected adapter
        decides the protocol event from those pins and its finite state; an offer alone
        is never treated as an accepted transaction.
        """
        from .failure import CandidateFault

        if type(signals) is not dict or any(
                type(name) is not str or not name for name in signals):
            raise CandidateFault(Refusal(
                "driver-artifact-observation-malformed",
                "a device observation maps nonempty signal names to pin samples"))
        slot = self._slot(index)
        # Subclasses may supply fresh controls/presentations. The concrete Driver
        # already owns both immutable views; observing them needs no detached
        # controls dict and no second preparation of that same slot.
        if type(self) is not Driver:
            controls = self.controls(index)
        prepared_slot = self._prepare_slot(index)
        if type(self) is Driver:
            drive = self._drive(index, prepared_slot)
            if presented is not prepared_slot.inputs.presentations:
                drive = self.protocol.drive(
                    self.contract, self.adapter_state, slot, presented,
                    dict(drive.controls))
        else:
            # Explicit alternate presentations and subclass overrides still mean
            # what the caller supplied, not what the Driver would have presented.
            drive = self.protocol.drive(
                self.contract, self.adapter_state, slot, presented, controls)
        before = self.state(device_state)
        transition = self.protocol.step(
            self.contract, self.adapter_state, slot, drive, signals)
        accepted = dict(transition.accepted_inputs)
        emitted = dict(transition.emitted_outputs)
        overlap = sorted(set(accepted) & set(emitted))
        if overlap:
            raise Refused(Refusal(
                "driver-record-endpoint-collision",
                "one logical endpoint was reported as both an accepted input and an "
                "emitted output in the same slot",
                slot=index, endpoints=overlap))
        accepted.update(emitted)
        prepared = (prepared_slot.inputs if type(self) is Driver
                    else self._prepare_slot(index).inputs)
        next_queue_state = self._queue_state.commit(prepared, accepted)
        next_outstanding_counts = (
            self._outstanding_after(slot, accepted)
            if self._track_response_counts else None)
        next_outstanding = (
            max(0, next_outstanding_counts[0] - next_outstanding_counts[1])
            if next_outstanding_counts is not None else None)
        after = self.state(
            device_after if device_after is not None else device_state,
            queue_state=next_queue_state, adapter_state=transition.state,
            slot_count=len(self.records) + 1, outstanding=next_outstanding)
        # Every transition is reconciled against the owner list. A device that mutated
        # something it did not declare, or declared something it did not mutate, fails
        # here -- which is what "completeness follows from the type and the
        # reconciliation" means, as against a backend attesting to its own.
        moved = reconcile(before, after, {
            "driver": [n for n in (
                self._pointer_component, self._released_component, "driver.slot")
                       if before[n] != after[n]],
            "monitor": (["monitor.outstanding"]
                        if self._has_scalar_outstanding
                        and before["monitor.outstanding"]
                        != after["monitor.outstanding"] else []),
            "adapter": [c.name for c in self._adapter_components()
                        if before[c.name] != after[c.name]],
            **{owner: names for owner, names in dict(device_changed or {}).items()},
        })
        # The monitor field is written from the reconciled state, not from a count taken
        # beside it: two places counting what is in flight is two answers.  Build the
        # final immutable record once; changing a frozen record with
        # ``object.__setattr__`` made its constructor checks and any identity already
        # taken describe a different value from the one later published.
        recorded_monitor = dict(transition.monitor)
        if self._has_scalar_outstanding:
            recorded_monitor["outstanding"] = after["monitor.outstanding"]
        # A long-latency device can spend many slots with the same observations.
        # Compare before rebuilding the immutable record. All transitions and
        # checks still execute; sharing never removes a slot or an observation.
        outputs = {n: v for n, v in signals.items() if isinstance(v, int)}
        previous = self.records[-1] if self.records else None
        if (type(previous) is SlotRecord
                and not (previous.state or previous.environment or previous.hits)
                and _same_pairs(previous.accepted, accepted)
                and _same_pairs(previous.outputs, outputs)
                and _same_pairs(previous.monitor, recorded_monitor)):
            record = previous
        else:
            record = SlotRecord(
                accepted=accepted, outputs=outputs, monitor=recorded_monitor)
        # One commit point after the complete product transition reconciles.  A refused
        # slot leaves the pointers, adapter state, committed state and trace unchanged.
        self._input_domain.advance(slot, accepted, index=index)
        if self._protocol_facts is not None:
            self._protocol_facts.record(index, slot, self.adapter_state, drive, transition,
                                        record, self._queue_state.accepted)
        self._queue_state = next_queue_state
        self.adapter_state = transition.state
        for name, payload in emitted.items():
            if payload is not None:
                self._emitted_counts[name] += 1
        if next_outstanding_counts is not None:
            self._outstanding_counts = next_outstanding_counts
        self.last_state = after
        self.records.append(record)
        if self.protocol_stop is not None:
            self._runtime_schedule.complete = True
        del moved
        return record

    def fail(self, index, why, goal="artifact.terminal-failure"):
        """The artifact failed at ``index``: pad the rest and stop invoking it.

        T3-02: a supported artifact failure produces exactly `N` recorded slots, emits
        its safety hit **once**, uses only padding outputs thereafter, and never invokes
        the artifact again in the padded suffix -- and its concrete completion predicate
        stays false.

        Each half of that matters separately. `N` slots because a shorter record is a
        record of a different, shorter stimulus, and a later reader comparing two runs
        would be comparing different horizons. Terminal stuttering rather than nothing so
        the padded slots say what they are. Not invoking the artifact because it has
        already failed and whatever it returns next is not evidence. And a false
        completion because **analysis padding must never turn a failed concrete run into
        a passing one** -- which is the whole reason the record carries `PADDED` rather
        than being marked normal and noted somewhere.
        """
        if self.failed is not None:
            raise Refused(Refusal(
                "driver-already-failed",
                "this run has already recorded a terminal failure; a second would emit "
                "the safety hit twice and the first is the one that ended the run",
                first=self.failed[0], again=index))
        slots = (self.stimulus.slots if self._runtime_schedule is None
                 else self._runtime_schedule.slots)
        if type(index) is not int or index != len(self.records) \
                or index >= len(slots):
            raise Refused(Refusal(
                "driver-failure-slot-mismatch",
                "a terminal failure belongs to the next unrecorded slot of this "
                "fixed-horizon run",
                slot=index, next_slot=len(self.records),
                horizon=len(slots)))
        self.failed = (index, str(why), str(goal))
        self.records.append(SlotRecord(
            hits={str(goal): True}, monitor={"terminal_failure": 1}))
        if self._runtime_schedule is not None:
            self._runtime_schedule.complete = True
        while len(self.records) < len(slots):
            self.records.append(SlotRecord(monitor={"padding": 1}))
        return self.failed

    def completed_stimulus(self):
        """The actual schedule, after completion and channel-shape checks."""
        stimulus = (self.stimulus if self._runtime_schedule is None
                    else self._runtime_schedule.stimulus())
        if len(self.records) != len(stimulus.slots):
            raise Refused(Refusal(
                "driver-incomplete",
                "the driver has not observed every slot of its stimulus, so the record "
                "would be of a shorter run",
                slots=len(stimulus.slots), observed=len(self.records)))
        if self.failed is None:
            # A padded run's suffix is stuttering, not signals, so the channel shape
            # cannot be read off it -- and a design that failed has not told us what
            # shape its channel is.
            check_channel_shape(self.contract, stimulus, self.records)
        return stimulus

    def trace(self):
        """The finished record. Refuses before the last slot has been observed."""
        stimulus = self.completed_stimulus()
        # The same domain identity the RTL path writes -- `trace_domain`, with the
        # interface as its recoverable prefix -- rather than the bare contract digest:
        # two readers of one record model must not need to know which backend produced
        # it to compare domains. The horizon is the stimulus's own; the run-policy
        # fields this loop was not given are absent rather than invented.
        domain = (self.trace_domain_fields.identity(self.contract.digest)
                  if self.trace_domain_fields is not None
                  else trace_domain(
                      self.contract.digest, horizon=len(stimulus.slots)))
        return AuditedTrace(
            stimulus, records=self.records,
            completion=(Completion.PADDED if self.failed else Completion.INCOMPLETE
                        if self.protocol_stop is not None else Completion.NORMAL),
            domain=domain,
            artifact=self.artifact_identity,
            request=self.stimulus if self.stimulus.replay_policy is not None else None)


def check_channel_shape(contract, stimulus, records):
    """Refuse a run whose output channel this build's adapter does not describe.

    **From the declaration, not from the run.** An earlier version of this read the kind
    off the trace: an endpoint whose `valid` never fell outside reset was called a
    sampled output. That is not sound. A ready/valid producer with a full backlog, or one
    emitting back-to-back, holds `valid` high legitimately, and a finite trace in which
    it never fell is no evidence that it cannot -- so the check refused legal designs and
    called the refusal a finding about the channel.

    The kind is a fact about the interface and belongs in the task's declaration, where
    `Kind` now is. This asks what was declared. `UNDECLARED` refuses too: a run of a
    channel nobody has classified establishes nothing about it either way, and that is
    the state the naming importer leaves every endpoint in until a manifest says
    otherwise.
    """
    del stimulus, records
    for endpoint in contract.endpoints:
        if endpoint.kind is Kind.TRANSACTION:
            continue
        raise Refused(Refusal(
            "adapter-channel-kind" if endpoint.kind is Kind.SAMPLED
            else "adapter-channel-kind-undeclared",
            "this build's adapter describes a request-response channel, and this "
            "endpoint is declared as something else or as nothing at all. A run under "
            "an adapter that does not describe the channel establishes nothing about "
            "the design either way",
            endpoint=endpoint.name, channel_kind=endpoint.kind.value))


def run_in_memory(contract, stimulus, device, *, trace_domain_fields=None,
                  execution_policy=None, expected_outputs=None):
    """Step a `Driver` against a device that answers in Python rather than in silicon.

    ``device`` implements ``reset()``, ``settle(presented, controls) -> signals`` and
    ``edge()``.  ``presented`` remains the historical tuple-or-``None`` for a unique
    input and is an endpoint mapping for a multi-input product.  It is the same rule the
    generated testbench steps, so a protocol test here is a test of the generated RTL
    path as well.
    """
    from .failure import call_candidate

    driver = Driver(contract, stimulus,
                    artifact_identity=str(getattr(device, "identity", "") or ""),
                    trace_domain_fields=trace_domain_fields,
                    execution_policy=execution_policy, expected_outputs=expected_outputs)
    call_candidate(device.reset)
    for index, slot in driver.iter_slots():
        controls = driver.controls(index)
        presentations = driver.presentations(index)
        presented = (driver.present(index) if len(driver.inputs) <= 1
                     else dict(presentations))
        if slot.reset:
            call_candidate(device.reset)
        signals = call_candidate(device.settle, presented, controls)
        driver.observe(index, presentations, signals)
        call_candidate(device.edge)
    return driver.trace()
