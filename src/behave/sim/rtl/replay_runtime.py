"""One RTL replay loop for both physical clock strategies.

Core Driver/AtomicDriver still own protocol transitions. This layer owns pin
drives, observation intervals, state capture and complete-prefix publication.
No expected outputs, solver terms or score policy enter the simulator loop.
"""

import pickle
from functools import partial

from cocotb.triggers import ReadOnly, Timer

from ..core.atomic import AtomicDriver
from ..core.driver import Driver
from ..core.failure import RunFailure
from ..core.hit_vector import HIT_VECTOR_ENCODING, HitVectorCodec
from ..core.memory_runtime import MemoryRuntime
from ..core.memory_trace import memory_updates_record
from ..core.physical_value import pack_value
from ..core.record_value import canonical_json
from ..core.refusal import Refused
from .execution_policy import execution_policy
from .initial_state import RTLInitialState
from .replay_clock import FreeRunningClock, ScheduledClocks, SettledInputs
from .replay_plan import ATOMIC_TRACE_VERSION, ReplayPlan
from .replay_progress import ReplayProgress
from .replay_startup import install_initial_state, install_startup_state, settle_initial_state
from .source_handles import exact_handle, path_handle, read_handle, width_of
from .trace_document import write_trace_document


def _read(handles):
    return {name: read_handle(handle) for name, handle in handles.items()}


class _InputPins:
    """Exclusive writer for environment inputs; clocks belong to the scheduler.

    All writes in a slot precede its first yield. Compose that final drive image
    before touching VPI, then skip unchanged assignments. This does not skip a
    simulation boundary, a DUT read, or a native observer callback.
    """

    def __init__(self, dut):
        self.dut = dut
        self._pins = {}

    def drive(self, signals):
        for name, value in signals.items():
            prior = self._pins.get(name)
            if prior is None:
                handle = self.dut[name]
            elif prior[1] == value:
                continue
            else:
                handle = prior[0]
            handle.value = value
            self._pins[name] = (handle, value)


class _ReplayImages:
    """Reuse only the last wire projection of owned observation values.

    Each cycle is still sampled, committed and appended. In particular, an
    identical write on consecutive cycles remains two writes. Keeping these
    images shared during execution avoids building millions of dictionaries
    that would only be deduplicated when publishing the finished trace.
    """

    def __init__(self):
        self._vector = self._hits = None
        self._updates = self._memory = None

    def hits(self, vector):
        if vector is not self._vector:
            self._hits = vector.as_compact_dict()
            self._vector = vector
        return self._hits

    def memory(self, updates):
        # MemoryRuntime produces immutable tuples of Updates containing exact
        # integer writes, pending requests and responses, never mutable cells.
        if updates != self._updates:
            self._memory = memory_updates_record(updates)
            self._updates = updates
        return self._memory


def _record_images(records, structural_hits, memory_states, successor_states, *, state_fields):
    """Overlay observations without thawing the Driver's owned immutable values.

    The common JSON boundary still validates and detaches the final projection.
    This temporary view does not escape as a caller-mutable public record.
    """
    images = []
    previous_record = None
    for index, (record, hits) in enumerate(zip(records, structural_hits, strict=True)):
        image = record._fields()
        image["hits"] = hits
        if memory_states:
            image["environment"] = [["external-memory", memory_states[index]]]
        if state_fields:
            image["state"] = sorted(successor_states[index].items())
        # Driver sharing alone is insufficient if publication recreates a full
        # dict at every repeated slot. All overlay values here are owned wire
        # bitsets, integral VPI samples or typed memory-update records. Compare
        # them too: the same interface samples can have different native hits.
        if record is previous_record and image == images[-1]:
            image = images[-1]
        images.append(image)
        previous_record = record
    return images


class _ReplayCapture:
    """One owner for already sampled values, including simulator shutdown.

    Publication never reads VPI. The ordinary Driver samples before an edge,
    so it can have one more record than completed source intervals. Only an
    interrupted publication separates that pending sample from completed slots;
    normal publication still requires every array to agree exactly.
    """

    def __init__(self, plan, native):
        self.plan, self.native = plan, native
        self.driver = self.boundaries = self.initial_state = self.pending_sample = None
        self.initialization = None
        self.state_widths = []
        self.hits, self.memory, self.states = [], [], []

    def image(self, *, interrupted=False):
        plan, native = self.plan, self.native
        records = () if self.driver is None else self.driver.records
        if interrupted:
            records = records[:len(self.hits)]
        image = {
            "execution_policy": execution_policy(native_events=native is not None),
            "records": _record_images(records, self.hits, self.memory, self.states,
                                       state_fields=bool(plan.state_fields)),
            "structural_refusals": {}, "hit_encoding": HIT_VECTOR_ENCODING,
            "hit_catalogue": list(native.catalogue if native is not None else ()),
            "state_widths": self.state_widths,
        }
        if plan.records_initial_state and self.initial_state is not None:
            image["initial_state"] = self.initial_state
        if plan.startup_state is not None:
            image["startup_state"] = plan.startup_state
        if self.boundaries is not None:
            image["query_boundaries"] = self.boundaries.as_dict()
        if plan.atomic:
            image.update(version=ATOMIC_TRACE_VERSION, artifact=plan.artifact)
        if interrupted:
            # This separate document is not a completed AuditedTrace. In
            # particular a pre-clock sample is never a successor observation.
            image.update(interrupted=True, stimulus=plan.stimuli[0].fingerprint,
                         pending_sample=self.pending_sample, initialization=self.initialization)
        return image

    def checkpoint(self):
        resolved = self.driver._runtime_schedule.observed_stimulus()
        image = self.image()
        image.update(checkpoint=True, resolved_stimulus=resolved._image(shared_slots=True))
        if self.native is not None:
            image["source_evidence"] = self.native.checkpoint(resolved)
        return image


async def replay(dut, directory, *, period_ns):
    plan = ReplayPlan.from_dict(pickle.loads((directory / "plan.pickle").read_bytes()))
    native = None
    if plan.native_events is not None:
        from .native_events import NativeEventPlan
        from .native_observation import NativeObservation

        # Begin before the first simulator yield, including natural startup.
        native = NativeObservation(NativeEventPlan.from_dict(plan.native_events), plan.stimuli[0])
    capture = _ReplayCapture(plan, native)
    try:
        await _replay(dut, directory, period_ns=period_ns, plan=plan, native=native, capture=capture)
    except BaseException as failure:
        try:
            if plan.checkpoint is not None:
                from .replay_checkpoint import CheckpointClient

                CheckpointClient(plan.checkpoint).invalidate()
        except BaseException as publication:  # noqa: BLE001 - keep original termination and native evidence
            failure.add_note(
                f"Checkpoint invalidation failed: {type(publication).__name__}: {publication}")
        # Cocotb unwinds this coroutine on simulator end-of-simulation, including
        # Verilator's fatal exit callback. No further VPI reads or yields are safe.
        # SIGKILL / a collector fault cannot manufacture a sealed prefix.
        try:
            if isinstance(failure, Refused):
                # A driver can reject an input before committing its sampled
                # record. Preserve the typed cause even if prefix capture is
                # incomplete; this diagnostic grants no execution evidence.
                (directory / 'replay_refusal.json').write_text(
                    canonical_json(failure.refusal.as_dict()), encoding='utf-8')
            if native is not None and native.interruptible:
                body = canonical_json(native.interrupt())
                temporary = directory / "native_prefix.partial"
                temporary.write_text(body, encoding="utf-8")
                temporary.replace(directory / "native_prefix.json")
                write_trace_document(directory / "replay_prefix.json", [capture.image(interrupted=True)])
        except BaseException as publication:  # noqa: BLE001 - preserve execution failure/cancellation
            failure.add_note(
                f"Native prefix publication failed: {type(publication).__name__}: {publication}")
        raise


async def _replay(dut, directory, *, period_ns, plan, native, capture):
    contract = plan.contract
    inputs = contract.inputs()
    input_pins = _InputPins(dut)
    idle_payload = {field.port: 0 for endpoint in inputs for field in endpoint.fields}
    flush = partial(write_trace_document, directory / "driven.json")
    empty_hits = HitVectorCodec(()).vector(())
    # Resolve VPI handles once: repeated lookup grows cocotb's retained handle graph.
    state_paths = dict(plan.state_paths)
    state_handles = {name: path_handle(dut, path) for name, path in state_paths.items()}
    # A flattened memory word has the same physical handle at both boundaries.
    # Reuse its compiler-declared array coordinates, but read it separately at
    # control settling and after the committed edge (never reuse its value).
    handles = {name: state_handles[name] if name in state_handles else exact_handle(dut, name)
               for name in plan.watch}
    state_widths = [[name, width_of(handle)] for name, handle in state_handles.items()]
    capture.state_widths = state_widths
    scheduler = (SettledInputs() if not contract.clocks else
                 ScheduledClocks(dut) if plan.atomic else
                 FreeRunningClock(dut, contract.clocks[0], period_ns))
    done = []
    if plan.atomic:
        flush(done)
    from .source_boundary_capture import BoundaryCapture

    history = None
    if plan.source_history is not None:
        history = BoundaryCapture(dut, plan.source_boundary_fields, paths=plan.source_boundary_paths)
        capture.boundaries = history
        history.start_history(plan.source_history, contract, input_aliases=plan.startup_aliases)
    if plan.startup_state is not None:
        from .startup_state import RTLStartupState

        try:
            install_startup_state(RTLStartupState.from_dict(plan.startup_state), dut,
                                  state_handles, paths=state_paths, input_aliases=plan.startup_aliases)
            capture.initialization = plan.startup_state
        except Refused as exc:
            flush([], startup_failure=RunFailure.verifier("rtl-analysis-startup", exc.refusal))
            return
    # A construction choice replaces only pre-HDL bits. The environment must
    # still release reset and park each clock exactly as in ordinary replay.
    await settle_initial_state(dut, contract.clocks)
    if plan.initial_state is not None:
        try:
            await install_initial_state(
                RTLInitialState.from_dict(plan.initial_state), state_handles, top=dut._name,
                paths=state_paths)
            capture.initialization = plan.initial_state
        except Refused as exc:
            flush([], startup_failure=RunFailure.verifier("rtl-analysis-startup", exc.refusal))
            return
    scheduler.start()
    if not plan.atomic and plan.checkpoint is None:
        flush(done)

    for stimulus in plan.stimuli:
        # Protocol state is per stimulus, even in explicitly shared-process diagnostics.
        driver = (AtomicDriver(contract, stimulus, artifact_identity=plan.artifact,
                               expected_outputs=plan.completion_targets)
                  if plan.atomic else Driver(contract, stimulus,
                                              expected_outputs=plan.completion_targets))
        initial_state = _read(state_handles) if plan.records_initial_state else None
        boundaries = history or (BoundaryCapture(dut, plan.source_boundary_fields,
                                      paths=plan.source_boundary_paths)
                      if plan.source_boundary_fields else None)
        capture.driver, capture.initial_state, capture.boundaries = driver, initial_state, boundaries
        if history is not None:
            history.snapshot('initial')
        if plan.atomic:
            scheduler.begin(stimulus, timings=driver.timings, slots=driver.scheduled)
        else:
            scheduler.begin(stimulus)
        if plan.atomic:
            input_pins.drive(idle_payload | {
                name: 0 for endpoint in inputs for name in endpoint.adapter.environment_controls})
        structural_hits, successor_states = [], []
        images = _ReplayImages()
        # AtomicDriver already owns its memory product. The one-domain Driver's
        # memory transition remains at the same pre-clock sampling boundary.
        memory_runtime = None
        memory_states = []
        capture.hits, capture.states, capture.memory = structural_hits, successor_states, memory_states
        if contract.memories and not plan.atomic:
            memory_runtime = MemoryRuntime(contract.memories, stimulus.memory_environment)
        progress = ReplayProgress(directory, stimulus, plan.completion_targets)
        checkpoint = None
        if plan.checkpoint is not None:
            from .replay_checkpoint import CheckpointClient

            checkpoint = CheckpointClient(plan.checkpoint)
        safety = set(plan.safety_goals)
        safety_bits = sum(1 << i for i, goal in enumerate(native.catalogue if native else ())
                          if goal in safety)

        progress.publish(driver, force=True)
        for index, _slot in driver.iter_slots():
            capture.pending_sample = None
            if checkpoint is not None and checkpoint.stopped():
                return  # Only earlier host-assessed evidence may survive this stop.
            if native is not None:
                if index:
                    native.next_slot()
                else:
                    native.start_slots()
            if plan.atomic:
                drive_image = driver.drive(index)
                signals = dict(idle_payload)
                signals.update(drive_image.signals)
            else:
                signals = dict(driver.controls(index))
                presentations = driver.presentations(index)
                if memory_runtime is not None:
                    signals.update(memory_runtime.drive(
                        index, reset_domains=(driver.clock.name,) if _slot.reset else ()))
                for endpoint in driver.inputs:
                    presented = presentations[endpoint.name]
                    if presented is None:
                        signals.update((field.port, 0) for field in endpoint.fields)
                    else:
                        signals.update((field.port, pack_value(field.type, value))
                                       for field, value in zip(endpoint.fields, presented))
            if boundaries is not None:
                if plan.atomic:
                    presentations = {name: payload for drive in drive_image.domains.values()
                                     for name, payload in drive.presentations.items()}
                boundaries.observe(index, presentations, driver.pointers)
            input_pins.drive(signals)

            await scheduler.settle_controls(index)
            if history is not None:
                history.snapshot('sampled')
            sampled = _read(handles)
            recorded = {name: sampled[name] for name in plan.record_names}
            capture.pending_sample = recorded
            memory_updates = None
            if not plan.atomic:
                driver.observe(index, presentations, recorded)
                if memory_runtime is not None:
                    memory_updates = memory_runtime.prepare(
                        index, sampled, active_domains={driver.clock.name},
                        reset_domains=(driver.clock.name,) if _slot.reset else ())
            if native is not None:
                native.sampled()

            await scheduler.commit(index)
            if native is not None:
                native.clocked()
            await scheduler.advance(index)
            vector = native.successor() if native is not None else empty_hits
            structural_hits.append(images.hits(vector))
            if memory_updates is not None:
                memory_runtime.commit(memory_updates)
                memory_states.append(images.memory(memory_updates))
            successor = _read(state_handles) if plan.state_fields else None
            if history is not None:
                history.snapshot('states')
            if successor is not None:
                successor_states.append(successor)
            if plan.atomic:
                driver.observe(index, drive_image, recorded,
                               hits=tuple(goal for goal, reached in vector if reached), state=successor)
            capture.pending_sample = None
            progress.publish(driver)
            if checkpoint is not None:
                if driver.protocol_stop is not None or int.from_bytes(vector._true, "little") & safety_bits:
                    checkpoint.invalidate()
                elif (any(name in checkpoint.outputs and value is not None
                          for name, value in driver.records[-1].accepted)
                      and checkpoint.due(driver.progress_counts(),
                            memory_quiescent=memory_runtime is None or memory_runtime.quiescent)):
                    # The published wire projection is temporary. Do not keep
                    # its whole-prefix dictionary graph alive while the next
                    # transaction runs (the owned observation history remains).
                    resumed = checkpoint.publish(capture.checkpoint(), driver.pointers)
                    if not resumed:
                        return

        progress.publish(driver, stage="publishing", force=True)
        completed = capture.image()
        resolved = stimulus
        if stimulus.replay_policy is not None:
            resolved = driver.completed_stimulus()
            completed["resolved_stimulus"] = resolved._image(shared_slots=True)
            if driver.protocol_stop is not None:
                completed["protocol_stop"] = driver.protocol_stop.as_dict()
        if native is not None:
            scheduler.stop()
            # The final clock return is already settled and recorded. Finish
            # checks the collector fence before publishing the completed trace.
            await Timer(1, unit="ps")
            await ReadOnly()
            completed["source_evidence"] = native.finish(resolved=resolved)
        done.append(completed)
        flush(done)
        progress.publish(driver, stage="published", force=True)
        if native is None:
            await scheduler.between_traces()
