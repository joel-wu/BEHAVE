"""Decode simulator JSON against the frozen replay plan.

The same decoder serves serial replay and trusted postprocessing workers.
Simulator bytes never supply the catalogue, state layout or observer identity.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from ..core.catalogue import CatalogueManifest
from ..core.hit_vector import HIT_VECTOR_ENCODING, HitVectorCodec, _unreadable_union
from ..core.record_value import RecordRowDecoder
from ..core.refusal import Refusal, Refused
from ..core.stimulus import AuditedTrace, Completion, SlotRecord, Stimulus, trace_domain, uses_global_events
from .execution_policy import execution_adapter_version, execution_policy
from .replay_plan import ATOMIC_TRACE_VERSION as ATOMIC_DRIVER_TRACE_VERSION


@dataclass(frozen=True)
class TraceDecoder:
    _frontend_catalogue: CatalogueManifest | None
    run_policy: Mapping[str, object]

    def decode(self, contract, stimulus, records, check_channel_shape, identity,
                  *, expected_state_widths=None, expected_native=None, observation=None,
                  expected_outputs=None, _protocol_ledgers=None, checkpoint_plan=None,
                  initialization=None):
        """One stimulus's records as an `AuditedTrace`, or the `Refusal` that stops it."""
        initial_state = None
        structural_refusals = {}
        source_evidence = None
        query_boundaries = None
        native_hits = None
        protocol_stop = None
        checkpoint = type(records) is dict and "checkpoint" in records
        if checkpoint and (records["checkpoint"] is not True or checkpoint_plan is None
                           or checkpoint_plan.request != stimulus.fingerprint
                           or stimulus.replay_policy is None):
            return Refusal("rtl-checkpoint-unrequested", "this replay has no checkpoint capability")
        atomic = uses_global_events(contract, stimulus)
        if expected_native is None and self._frontend_catalogue:
            return Refusal("rtl-native-evidence-required",
                           "structural goal hits require prepared native occurrence evidence")
        request = stimulus if stimulus.replay_policy is not None else None
        if request is not None:
            try:
                stimulus = Stimulus.from_dict(records["resolved_stimulus"])
            except (KeyError, TypeError, ValueError) as exc:
                return Refusal("rtl-runtime-schedule-missing",
                               "runtime replay did not record its actual schedule", detail=str(exc))
        if type(records) is not dict:
            if atomic:
                return Refusal(
                    "rtl-atomic-artifact-binding-mismatch",
                    "the isolated atomic trace is missing its versioned document and "
                    "frozen artifact identity",
                    expected_version=ATOMIC_DRIVER_TRACE_VERSION,
                    got_version=None,
                    expected_artifact=identity,
                    got_artifact=None)
            return Refusal(
                "rtl-artifact-structural-record-malformed",
                "the isolated trace is missing its versioned structural-hit bit-vector "
                "document")
        if type(records) is dict:
            if atomic and (
                    set(records).isdisjoint({"version", "artifact"})
                    or "version" not in records or "artifact" not in records):
                return Refusal(
                    "rtl-atomic-artifact-binding-mismatch",
                    "the isolated atomic trace is missing the versioned artifact "
                    "identity supplied in its frozen driver plan",
                    expected_version=ATOMIC_DRIVER_TRACE_VERSION,
                    got_version=records.get("version"),
                    expected_artifact=identity,
                    got_artifact=records.get("artifact"))
            allowed = {
                "records", "structural_refusals", "hit_encoding",
                "hit_catalogue", "execution_policy",
            }
            if checkpoint:
                allowed.add("checkpoint")
            if "query_boundaries" in records:
                allowed.add("query_boundaries")
                query_boundaries = records["query_boundaries"]
            if request is not None:
                allowed.add("resolved_stimulus")
                if "protocol_stop" in records:
                    allowed.add("protocol_stop")
                    protocol_stop = records["protocol_stop"]
                    if type(protocol_stop) is not dict:
                        return Refusal("rtl-protocol-stop-malformed",
                                       "an early protocol stop needs its determined violation")
            if expected_native is not None:
                from .native_observation import read_native_goal_bits

                allowed.add("source_evidence")
                source_evidence = records.get("source_evidence")
                try:
                    native_hits = read_native_goal_bits(
                        source_evidence, stimulus, self._frontend_catalogue,
                        expected_plan=expected_native, checkpoint=checkpoint)
                except (TypeError, ValueError, Refused) as exc:
                    return Refusal("rtl-native-evidence-invalid",
                                   "native replay has no complete bound occurrence evidence",
                                   detail=str(exc))
            if records.get("execution_policy") != execution_policy(
                    native_events=expected_native is not None):
                return Refusal(
                    "rtl-artifact-execution-policy-mismatch",
                    "the source trace does not name the scheduled execution "
                    "policy supplied in its driver plan")
            if atomic:
                allowed.update({"version", "artifact"})
            if "state_widths" in records:
                allowed.add("state_widths")
            if "initial_state" in records:
                allowed.add("initial_state")
                initial_state = records["initial_state"]
            if set(records) != allowed \
                    or type(records["records"]) is not list \
                    or type(records["structural_refusals"]) is not dict \
                    or records["hit_encoding"] != HIT_VECTOR_ENCODING \
                    or (initial_state is not None
                        and type(initial_state) is not dict):
                return Refusal(
                    "rtl-artifact-structural-record-malformed",
                    "the simulator's structural-hit channel did not return its "
                    "closed bit-vector records/refusals/initial-state shape")
            if atomic and (
                    records["version"] != ATOMIC_DRIVER_TRACE_VERSION
                    or records["artifact"] != identity):
                return Refusal(
                    "rtl-atomic-artifact-binding-mismatch",
                    "the isolated atomic trace does not carry the exact versioned "
                    "artifact identity supplied in its frozen driver plan",
                    expected_version=ATOMIC_DRIVER_TRACE_VERSION,
                    got_version=records["version"],
                    expected_artifact=identity,
                    got_artifact=records["artifact"])
            if expected_state_widths is not None:
                widths = records.get("state_widths")
                malformed = type(widths) is not list or any(
                    type(row) is not list or len(row) != 2
                    or type(row[0]) is not str or not row[0]
                    or (row[1] is not None and (
                        type(row[1]) is not int or isinstance(row[1], bool)
                        or row[1] < 1))
                    for row in widths or ())
                if malformed:
                    return Refusal(
                        "rtl-artifact-state-capability-malformed",
                        "the simulator did not return a closed state-width capability")
                names = [row[0] for row in widths]
                if len(names) != len(set(names)):
                    return Refusal(
                        "rtl-artifact-state-capability-malformed",
                        "the simulator returned one state-width capability twice")
                got_widths = dict(widths)
                if got_widths != expected_state_widths:
                    return Refusal(
                        "rtl-artifact-state-capability-mismatch",
                        "the simulator cannot expose exactly the frontend-owned state",
                        missing=sorted(set(expected_state_widths) - set(got_widths)),
                        extra=sorted(set(got_widths) - set(expected_state_widths)),
                        unavailable=sorted(
                            name for name, width in got_widths.items()
                            if width is None),
                        wrong_width=sorted(
                            name for name in set(got_widths) &
                            set(expected_state_widths)
                            if got_widths[name] != expected_state_widths[name]))
            structural_refusals = records["structural_refusals"]
            if any(type(goal) is not str or type(detail) is not dict
                   for goal, detail in structural_refusals.items()):
                return Refusal(
                    "rtl-artifact-structural-record-malformed",
                    "the simulator's structural refusal ledger contains an invalid "
                    "goal or detail row")
            raw_catalogue = records["hit_catalogue"]
            if type(raw_catalogue) is not list or any(
                    type(goal) is not str or not goal for goal in raw_catalogue) \
                    or raw_catalogue != sorted(set(raw_catalogue)):
                return Refusal(
                    "rtl-artifact-structural-record-malformed",
                    "the simulator's hit-vector catalogue is not a canonical list "
                    "of unique goal identifiers")
            hit_catalogue = tuple(raw_catalogue)
            expected_catalogue = tuple(sorted(self._frontend_catalogue or ()))
            if hit_catalogue != expected_catalogue:
                return Refusal(
                    "rtl-artifact-structural-catalogue-mismatch",
                    "the simulator's hit-vector positions do not name exactly the "
                    "frozen frontend manifest",
                    missing=sorted(set(expected_catalogue) - set(hit_catalogue)),
                    extra=sorted(set(hit_catalogue) - set(expected_catalogue)))
            hit_codec = HitVectorCodec(hit_catalogue)
            records = records["records"]
        if len(records) != len(stimulus.slots):
            return Refusal(
                "rtl-artifact-short-trace",
                "the driver recorded fewer slots than the stimulus names, so the record "
                "is of a different run",
                slots=len(stimulus.slots), recorded=len(records))
        built = []
        decode_row = RecordRowDecoder(lambda row: SlotRecord.from_dict(row, hit_catalogue=hit_codec))
        for slot, row in enumerate(records):
            if type(row) is not dict or type(row.get("hits")) is not dict:
                return Refusal(
                    "rtl-artifact-structural-record-malformed",
                    "every simulator slot must carry the structural-hit vector",
                    slot=slot)
            try:
                # The closed trace reader restores references to the same owned
                # JSON row. Decode that row once into an immutable record, but
                # retain every position. Native-hit agreement, protocol replay
                # and memory checking below still visit every cycle.
                built.append(decode_row(row))
            except (TypeError, ValueError) as exc:
                return Refusal(
                    "rtl-artifact-structural-record-malformed",
                    "a simulator structural-hit bit vector is malformed",
                    slot=slot, error=f"{type(exc).__name__}: {exc}")
        unresolved = _unreadable_union(record.hits for record in built)
        if native_hits is not None and any(
            record.hits != hit_codec.from_bits(observed)
            for record, observed in zip(built, native_hits, strict=True)
        ):
            return Refusal("rtl-native-hit-mismatch",
                           "public hits differ from the complete native occurrence evidence")
        if unresolved != set(structural_refusals):
            return Refusal(
                "rtl-artifact-structural-record-malformed",
                "the simulator's unknown hit values and structural refusal ledger "
                "name different goals",
                unknown=sorted(unresolved),
                refused=sorted(structural_refusals))
        # The same shape check the in-memory path gets. It lived on the driver, which
        # this path does not step -- it rebuilds records from the simulator's JSON -- so
        # the check ran on the path that did not need it.
        try:
            check_channel_shape(contract, stimulus, built)
            if request is not None:
                from ..core.runtime_schedule import validate_resolution

                recovered = validate_resolution(
                    contract, request, stimulus, built, expected_outputs,
                    collect_protocol=_protocol_ledgers is not None, protocol_stop=protocol_stop,
                    observed_prefix=checkpoint)
                if _protocol_ledgers is not None:
                    _protocol_ledgers.update(recovered)
            else:
                from ..core.input_framing import check_input_trace

                check_input_trace(contract, stimulus, built)
            if checkpoint:
                from collections import Counter

                from ..core.memory_trace import memory_successor_quiescent

                counts = Counter(name for row in built for name, value in row.accepted
                                 if value is not None)
                quiescent = not contract.memories or (bool(built) and memory_successor_quiescent(
                    dict(built[-1].environment)["external-memory"],
                    sorted(memory.name for memory in contract.memories)))
                if not checkpoint_plan.at_checkpoint_boundary(
                        {name: counts[name] for name, _ in checkpoint_plan.inputs},
                        {name: counts[name] for name, _ in checkpoint_plan.outputs},
                        memory_quiescent=quiescent):
                    return Refusal("rtl-checkpoint-in-flight", "the prefix has unfinished transactions")
        except Refused as exc:
            return exc.refusal
        # The domain is what the admissibility predicate is over: the interface, the
        # horizon, the reset policy and the adapter version. `contract.digest` alone
        # made a run of 40 slots and one of 200 members of one domain. The horizon comes
        # off the stimulus, since every stimulus of a run has the same one and a record's
        # own length is the fact rather than a number passed beside it. See
        # `core/stimulus.trace_domain` for what is deliberately not in it.
        adapter_version = self.run_policy.get("adapter_version", "")
        if atomic:
            from ..core.atomic import ATOMIC_ADAPTER_VERSION

            adapter_version = ATOMIC_ADAPTER_VERSION
        return AuditedTrace(stimulus, initial_state=initial_state,
                            initialization=initialization,
                            records=built, artifact=identity,
                            source_evidence=source_evidence,
                            query_boundaries=query_boundaries,
                            request=request,
                            completion=(Completion.CHECKPOINT if checkpoint else
                                        Completion.INCOMPLETE if protocol_stop is not None
                                        else Completion.NORMAL),
                            domain=trace_domain(
                                contract.digest, horizon=len(stimulus.slots),
                                reset_hold_cycles=self.run_policy.get(
                                    "reset_hold_cycles") if request is None else
                                    request.replay_policy.reset_hold_cycles,
                                adapter_version=execution_adapter_version(
                                    adapter_version, native_events=expected_native is not None,
                                    observation=observation),
                                queue_bound=self.run_policy.get("queue_bound") if request is None
                                else max((len(rows) for _, rows in request.queues), default=1)))
