"""Strict named-job boundary for source-Behavior guidance of a target artifact.

The verifier parent carries only authenticated source bytes, an accepted target trace,
and closed request/result records.  Source parsing, lowering, concrete SolverIR replay,
one-step encoding, and every Z3 object live in these jobs.  A satisfiable local query is
only a candidate generator; the common scheduler and later replay remain authoritative.
"""

from __future__ import annotations

import base64
import hashlib
import math
import pickle
import zlib
from functools import partial

from behave.core import runtime_defaults as defaults

from ...core.catalogue import CatalogueManifest, catalogue_subset
from ...core.contract import TaskContract
from ...core.record_value import freeze_record_value
from ...core.reference import ReferenceArtifact
from ...core.refusal import Refusal, Refused
from ...core.stimulus import AuditedTrace, Completion, Stimulus
from ...generation_protocol import (
    ProbeRequest,
    ProbeSelection,
    QueryPlan,
    QueryTemplate,
    ReplayFeedback,
    StimulusField,
)
from ...query_plan import (ending_windows)
from ...generation_trace import queue_release_slots
from ...stimulus_set import GenerationSpec
from .. import probing
from .generation import _field_sets, _fixed_inputs, _ordered_requests, _remaining_goal_groups

__all__ = [
    "BehaviorGenerationLayout",
    "decode_context_reply",
    "decode_layout_reply",
    "decode_probe_reply",
    "decode_selection_reply",
    "layout_job",
    "probe_job",
    "select_job",
]


_LAYOUT_VERSION = "behavior-generation-layout/4"
_SELECTION_VERSION = "behavior-generation-selection/24"
_PROBE_VERSION = "behavior-generation-probe/4"
_NATIVE_PATH = "native-path:"
_PROPOSAL_PATH = "path-proposal:"
_CONTEXT_PATH = "context-path:"
_CONTEXT_REPLY_VERSION = "behavior-generation-context-reply/1"
_CONTEXT_PAYLOAD_VERSION = "behavior-generation-context/26"
_CONTEXT_PAYLOAD_SCHEMA = "behavior-generation-context-zlib-pickle/2"
# Optional context-reuse limits belong to the runtime policy (None by default).
# A context outside that ancillary capability takes the ordinary cold path.
_CONTEXT_WIRE_MAX_BYTES = defaults.SIM_GENERATION_CONTEXT_WIRE_BYTES
_CONTEXT_IMAGE_MAX_BYTES = defaults.SIM_GENERATION_CONTEXT_IMAGE_BYTES
_CONTEXT_MAGIC = b"BEHAVE-GENERATION-CONTEXT-ZLIB-2\0"


def _path_identity(occurrence):
    return occurrence.removeprefix(_NATIVE_PATH).removeprefix(_PROPOSAL_PATH).removeprefix(_CONTEXT_PATH)


class BehaviorGenerationLayout:
    """Solver-free source identity returned by the layout worker."""

    __slots__ = (
        "catalogue", "constant_false_goals", "frontend_catalogue", "input_endpoints",
        "source_identity", "targetable_goals",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("BehaviorGenerationLayout is a closed worker record")

    def __init__(self, source_identity, catalogue, targetable_goals,
                 input_endpoints, *, frontend_catalogue, constant_false_goals=()):
        if type(source_identity) is not str or not source_identity:
            raise TypeError("a Behavior generation layout identifies its source")
        if type(catalogue) is not CatalogueManifest:
            raise TypeError("a Behavior generation layout carries one exact catalogue")
        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError("a Behavior generation layout identifies its frontend-owned catalogue")
        targets = tuple(targetable_goals)
        if targets != tuple(sorted(set(targets))) or any(
                type(item) is not str or not item for item in targets):
            raise ValueError("Behavior generation targets are sorted and distinct")
        false_goals = tuple(constant_false_goals)
        if false_goals != tuple(sorted(set(false_goals))) or not set(false_goals) <= set(targets):
            raise ValueError("constant-false query goals are a sorted subset of the targets")
        endpoints = []
        seen_endpoints = set()
        seen_names = set()
        for row in input_endpoints:
            if type(row) not in (tuple, list) or len(row) != 2:
                raise TypeError(
                    "a Behavior generation endpoint row is endpoint/fields")
            endpoint, fields = row[0], tuple(row[1])
            if type(endpoint) is not str or not endpoint \
                    or endpoint in seen_endpoints:
                raise TypeError(
                    "Behavior generation input endpoints are distinct and nonempty")
            if (any(type(field) not in (tuple, list) or len(field) != 2
                           or type(field[0]) is not str or not field[0]
                           or type(field[1]) is not str or not field[1]
                           for field in fields)
                    or len({field[0] for field in fields}) != len(fields)
                    or len({field[1] for field in fields}) != len(fields)
                    or seen_names & {field[1] for field in fields}):
                raise ValueError(
                    "Behavior generation endpoint fields retain distinct public and "
                    "globally distinct logical names")
            seen_endpoints.add(endpoint)
            seen_names.update(field[1] for field in fields)
            endpoints.append((endpoint, tuple(tuple(field) for field in fields)))
        self.source_identity = source_identity
        self.catalogue = catalogue
        self.frontend_catalogue = frontend_catalogue
        self.targetable_goals = targets
        self.constant_false_goals = false_goals
        self.input_endpoints = tuple(endpoints)

    def as_dict(self):
        return {
            "version": _LAYOUT_VERSION,
            "status": "ok",
            "source_identity": self.source_identity,
            "catalogue": self.catalogue.as_dict(),
            "frontend_goals": list(self.frontend_catalogue),
            "targetable_goals": list(self.targetable_goals),
            "constant_false_goals": list(self.constant_false_goals),
            "input_endpoints": [
                {"endpoint": endpoint, "fields": [
                    {"field": public, "logical": logical}
                    for public, logical in fields
                ]}
                for endpoint, fields in self.input_endpoints
            ],
        }


def decode_context_reply(value):
    """Split one worker answer from its optional detached context image.

    The opaque bytes are never deserialised in the verifier process.  They may only be
    handed to a later invocation of the same named isolated job.
    """

    fields = {
        "version", "answer", "context_base64", "context_reused",
        "context_schema",
    }
    if type(value) is not dict or set(value) != fields \
            or value.get("version") != _CONTEXT_REPLY_VERSION \
            or type(value.get("answer")) is not dict \
            or type(value.get("context_reused")) is not bool \
            or value.get("context_schema") != _CONTEXT_PAYLOAD_SCHEMA:
        raise ValueError(
            "a Behavior generation context reply has its complete versioned shape")
    encoded = value["context_base64"]
    if encoded is None:
        context = None
    else:
        if type(encoded) is not str:
            raise TypeError("a Behavior generation context is base64 text or null")
        try:
            context = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeError, ValueError):
            raise ValueError(
                "a Behavior generation context is canonical base64") from None
        if not context \
                or (_CONTEXT_WIRE_MAX_BYTES is not None
                    and len(context) > _CONTEXT_WIRE_MAX_BYTES) \
                or base64.b64encode(context).decode("ascii") != encoded:
            raise ValueError(
                "a Behavior generation context is nonempty canonical base64 within its configured quota")
    if value["context_reused"] and context is None:
        raise ValueError(
            "a reused Behavior generation context carries its exact opaque bytes")
    return value["answer"], context, value["context_reused"]


def _refusal_reply(version, refusal):
    if type(refusal) is not Refusal:
        raise TypeError("an isolated Behavior generation refusal is exact")
    return {
        "version": version,
        "status": "refused",
        "refusal": refusal.as_dict(),
    }


def _decode_refusal(value):
    if type(value) is not dict or set(value) not in (
            {"kind", "why"}, {"kind", "why", "detail"}):
        raise TypeError("a Behavior generation refusal has exact fields")
    if any(type(value.get(name)) is not str or not value[name]
           for name in ("kind", "why")):
        raise TypeError("a Behavior generation refusal has nonempty text")
    detail = value.get("detail") or {}
    if type(detail) is not dict:
        raise TypeError("a Behavior generation refusal detail is a mapping")
    return Refusal(value["kind"], value["why"], **detail)


def decode_layout_reply(value):
    if type(value) is not dict or value.get("version") != _LAYOUT_VERSION:
        raise ValueError("a Behavior layout reply has the supported version")
    if value.get("status") == "refused":
        if set(value) != {"version", "status", "refusal"}:
            raise ValueError("a refused Behavior layout reply has no extra fields")
        return _decode_refusal(value["refusal"])
    fields = {
        "version", "status", "source_identity", "catalogue", "frontend_goals",
        "targetable_goals", "input_endpoints", "constant_false_goals",
    }
    if value.get("status") != "ok" or set(value) != fields:
        raise ValueError("a successful Behavior layout reply has all fields")
    if type(value["targetable_goals"]) is not list \
            or type(value["constant_false_goals"]) is not list \
            or type(value["input_endpoints"]) is not list:
        raise TypeError("Behavior layout names are exact lists")
    endpoints = []
    for row in value["input_endpoints"]:
        if type(row) is not dict or set(row) != {"endpoint", "fields"} \
                or type(row["endpoint"]) is not str \
                or type(row["fields"]) is not list:
            raise TypeError("Behavior layout endpoint rows have endpoint/fields")
        fields = []
        for field in row["fields"]:
            if type(field) is not dict or set(field) != {"field", "logical"}:
                raise TypeError(
                    "Behavior layout fields retain public and logical names")
            fields.append((field["field"], field["logical"]))
        endpoints.append((row["endpoint"], fields))
    catalogue = CatalogueManifest.from_dict(value["catalogue"])
    got = BehaviorGenerationLayout(
        value["source_identity"], catalogue, value["targetable_goals"], endpoints,
        frontend_catalogue=catalogue_subset(catalogue, value["frontend_goals"]),
        constant_false_goals=value["constant_false_goals"])
    if got.as_dict() != value:
        raise ValueError("a Behavior generation layout image is not canonical")
    return got


def decode_selection_reply(value):
    if type(value) is not dict or value.get("version") != _SELECTION_VERSION:
        raise ValueError("a Behavior Select reply has the supported version")
    if value.get("status") == "refused":
        if set(value) != {"version", "status", "refusal"}:
            raise ValueError("a refused Behavior Select reply has no extra fields")
        return _decode_refusal(value["refusal"])
    if value.get("status") != "ok" or set(value) != {
            "version", "status", "requests", "catalogue_digest", "replay", "equivalent_requests",
            "constant_false_goals", "query_chains", "preparation_plan"}:
        raise ValueError("a successful Behavior Select reply has all fields")
    if type(value["requests"]) is dict:
        requests = QueryPlan.from_dict(value["requests"])
    elif type(value["requests"]) is list:
        requests = tuple(ProbeRequest.from_dict(row) for row in value["requests"])
    else:
        raise TypeError("Behavior Select requests are a closed plan or an exact list")
    return ProbeSelection(
        requests,
        value["catalogue_digest"], replay=ReplayFeedback.from_dict(value["replay"]),
        equivalent_requests=value["equivalent_requests"],
        constant_false_goals=value["constant_false_goals"], query_chains=value["query_chains"])


def decode_probe_reply(value, *, expected_catalogue_digest):
    fields = {
        "version", "outcome", "scope", "goal", "candidate", "detail",
        "solve_ms", "catalogue_digest",
    }
    if type(value) is not dict or value.get("version") != _PROBE_VERSION \
            or set(value) != fields:
        raise ValueError("a Behavior Probe reply has its complete versioned shape")
    if type(value["outcome"]) is not str or type(value["scope"]) is not str \
            or value["goal"] is not None and type(value["goal"]) is not str \
            or type(value["detail"]) is not str \
            or type(value["solve_ms"]) not in {int, float} \
            or not math.isfinite(value["solve_ms"]) or value["solve_ms"] < 0:
        raise TypeError("a Behavior Probe reply has exact finite field types")
    digest = value["catalogue_digest"]
    if digest != expected_catalogue_digest:
        raise ValueError("the Behavior Probe worker rebuilt another source catalogue")
    outcome = probing.Outcome(value["outcome"])
    scope = probing.Scope(value["scope"])
    if scope not in (probing.Scope.EXACT_LOCAL_HIT, probing.Scope.PATH_PREFIX,
                     probing.Scope.STATE_PREPARATION):
        raise ValueError("a Behavior worker returns a local query or state preparation")
    candidate = (None if value["candidate"] is None
                 else Stimulus.from_dict(value["candidate"]))
    return probing.ProbeResult(
        outcome, scope, goal=value["goal"], candidate=candidate,
        detail=value["detail"], solve_ms=float(value["solve_ms"]))


def _generation_spec(value):
    if type(value) is not dict or set(value) != {
            "horizon", "queue_bound", "reset_hold_cycles", "drain",
            "stall_every", "max_body_steps"}:
        raise TypeError("a Behavior generation job carries one GenerationSpec image")
    got = GenerationSpec(
        value["horizon"], value["queue_bound"],
        value["reset_hold_cycles"], value["drain"], value["stall_every"])
    if got.as_dict() != value:
        raise ValueError("a Behavior generation spec image is not canonical")
    return got


def _source_bundle(value):
    fields = {"artifact", "sources", "config"}
    if type(value) is not dict or set(value) != fields \
            or type(value["artifact"]) is not dict \
            or type(value["sources"]) is not list \
            or type(value["config"]) is not dict:
        raise TypeError("a Behavior generation source is one closed bundle image")
    sources = {}
    for row in value["sources"]:
        if type(row) is not dict or set(row) != {"name", "base64"} \
                or type(row["name"]) is not str or not row["name"] \
                or type(row["base64"]) is not str or row["name"] in sources:
            raise TypeError("each Behavior generation source row has name/base64")
        try:
            sources[row["name"]] = base64.b64decode(
                row["base64"].encode("ascii"), validate=True)
        except (UnicodeError, ValueError):
            raise ValueError("a Behavior source row is not canonical base64") from None
        if base64.b64encode(sources[row["name"]]).decode("ascii") \
                != row["base64"]:
            raise ValueError("a Behavior source row is not canonical base64")
    artifact = value["artifact"]
    required = {
        "digest", "adapter", "adapter_impl", "loader", "sources", "config"}
    if set(artifact) != required:
        raise TypeError("the source bundle carries one ReferenceArtifact image")
    rebuilt = ReferenceArtifact(
        sources, config=value["config"], adapter=artifact["adapter"],
        loader=artifact["loader"], adapter_impl=artifact["adapter_impl"])
    if rebuilt.as_dict() != artifact:
        raise Refused(Refusal(
            "behavior-generation-source-bundle-mismatch",
            "the retained source bytes/config do not reconstruct their authenticated "
            "ReferenceArtifact"))
    if tuple(row["name"] for row in value["sources"]) != tuple(sorted(sources)):
        raise ValueError("Behavior source rows are in canonical name order")
    return sources, dict(value["config"]), rebuilt


def _context(payload, extras=()):
    fields = {
        "source", "contract", "generation_spec", "target_artifact", *extras}
    if type(payload) is not dict:
        raise TypeError("a Behavior generation job has its exact payload fields")
    allowed = fields | ({"cached_context"} if "cached_context" in payload else set())
    if set(payload) != allowed:
        raise TypeError("a Behavior generation job has its exact payload fields")
    contract = TaskContract.from_dict(payload["contract"])
    spec = _generation_spec(payload["generation_spec"])
    artifact = payload["target_artifact"]
    if type(artifact) is not str or not artifact:
        raise TypeError("a Behavior generation job identifies the target artifact")
    sources, config, reference = _source_bundle(payload["source"])
    return contract, spec, artifact, sources, config, reference


def _build(payload, extras=()):
    contract, spec, target, sources, config, reference = _context(payload, extras)
    if set(config) - {"class", "file"}:
        raise Refused(Refusal(
            "behavior-generation-constructor-config-unsupported",
            "isolated guidance admits only source class and entry-file selection; "
            "constructor arguments need a construction contract",
            keys=sorted(set(config) - {"class", "file"})))
    entry = config.get("file", "behavior.py")
    if type(entry) is not str or not entry:
        raise Refused(Refusal(
            "behavior-generation-source-name-invalid",
            "the authenticated guidance entry source has a nonempty string name"))
    body = sources.get(entry)
    if body is None:
        raise Refused(Refusal(
            "behavior-generation-source-missing",
            "the authenticated guidance bundle has no selected entry source",
            file=entry))
    try:
        source = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise Refused(Refusal(
            "behavior-generation-source-not-utf8",
            "Behavior lowering requires behavior.py to be exact UTF-8")) from None

    from ...behavior.construction import _dependency_snapshot

    dependency_identity, dependency = _dependency_snapshot()
    carried_dependency = sources.get("fp_lib.py")
    if carried_dependency is not None and carried_dependency != dependency:
        raise Refused(Refusal(
            "behavior-generation-dependency-mismatch",
            "the authenticated guidance source records fp_lib bytes different from "
            "the approved lowering dependency",
            approved=dependency_identity,
            carried=hashlib.sha256(carried_dependency).hexdigest()))
    unknown_dependencies = sorted(set(sources) - {entry, "fp_lib.py"})
    if unknown_dependencies:
        raise Refused(Refusal(
            "behavior-generation-dependency-capability-unsupported",
            "the Behavior frontend has no lowering contract for an authenticated "
            "source dependency in this bundle",
            sources=unknown_dependencies))

    from ...behavior.extract import source_model

    wanted = config.get("class")
    selected = source_model(source, entry, cls=wanted)

    from ...behavior.task_boundary import lower_task_behavior

    if None not in selected.steps:
        from .atomic_generation import AtomicGuidance

        raw = AtomicGuidance(source, entry, contract, selected.cls.name,
                             reference.digest, spec.max_body_steps)
        boundary = None
    elif contract.memories:
        from .memory_generation import MemoryGuidance

        raw = MemoryGuidance(source, entry, contract, selected,
                             reference.digest, spec.max_body_steps)
        boundary = None  # The selected run supplies the external-memory image.
    else:
        raw, boundary = lower_task_behavior(
            source, entry, contract, spec.max_body_steps, cls=selected.cls.name,
            prove_outputs=False, local_guidance=True)
    native_catalogue = raw.frontend_catalogue_manifest()
    from ...goal_policy import source_guidance_catalogue

    manifest = source_guidance_catalogue(contract, native_catalogue, artifact_kind="behavior")
    # Guidance carries this task-owned manifest separately and queries one
    # source-audited concrete state. Building/discarding a BoundedInstance here
    # made local search depend on whole-run position closure. That proof belongs
    # to optional analysis, not to a pinned one-step query or a concrete audit.
    # The same applies to universal output validity: the complete native seed
    # is checked, and the local query retains exact failure/output semantics.
    # Deferred evidence is not a proved output contract for bounded analysis.
    instance = raw
    goals = tuple(manifest)
    return (contract, spec, target, reference, instance, manifest, goals,
            boundary)


def _context_image(built):
    contract, spec, target, reference, _instance, manifest, _goals, _boundary = built
    image = {
        "version": _CONTEXT_PAYLOAD_VERSION,
        "contract": contract.digest,
        "generation_spec": spec.as_dict(),
        "target": target,
        "source": reference.digest,
        "catalogue": manifest.digest(),
        "built": built,
    }
    try:
        payload = pickle.dumps(image, protocol=5)
        if _CONTEXT_IMAGE_MAX_BYTES is not None and len(payload) > _CONTEXT_IMAGE_MAX_BYTES:
            return None
        payload = _CONTEXT_MAGIC + zlib.compress(payload, level=6)
    except Exception:  # noqa: BLE001 - cache carrier is diagnostic only
        return None
    return (None if _CONTEXT_WIRE_MAX_BYTES is not None
            and len(payload) > _CONTEXT_WIRE_MAX_BYTES else payload)


def _decoded_context_image(raw):
    """Expand one worker-local context under its optional operator quota."""

    if type(raw) is not bytes \
            or not raw.startswith(_CONTEXT_MAGIC) \
            or (_CONTEXT_WIRE_MAX_BYTES is not None and len(raw) > _CONTEXT_WIRE_MAX_BYTES):
        return None
    try:
        decoder = zlib.decompressobj()
        payload = decoder.decompress(
            raw[len(_CONTEXT_MAGIC):],
            0 if _CONTEXT_IMAGE_MAX_BYTES is None else _CONTEXT_IMAGE_MAX_BYTES + 1)
    except zlib.error:
        return None
    if (_CONTEXT_IMAGE_MAX_BYTES is not None and len(payload) > _CONTEXT_IMAGE_MAX_BYTES) \
            or not decoder.eof \
            or decoder.unconsumed_tail \
            or decoder.unused_data:
        return None
    return payload


def _validated_context(payload, extras, raw):
    """Restore source preparation only when its construction inputs agree.

    Deserialisation occurs only inside this named isolated job.  The verifier parent
    treats ``raw`` as opaque bytes.  Any malformed, stale, or incomplete image is an
    ordinary miss and the current authenticated source is lowered below.
    """

    raw = _decoded_context_image(raw)
    if raw is None:
        return None
    try:
        value = pickle.loads(raw)
        if type(value) is not dict or set(value) != {
                "version", "contract", "generation_spec", "target", "source",
                "catalogue", "built"} \
                or value["version"] != _CONTEXT_PAYLOAD_VERSION:
            return None
        contract, spec, target, _sources, _config, reference = _context(
            payload, extras)
        built = value["built"]
        if type(built) is not tuple or len(built) != 8:
            return None
        (cached_contract, cached_spec, cached_target, cached_reference, instance,
         manifest, goals, boundary) = built
        from ...behavior.instance import BehaviorInstance
        from ...behavior.task_boundary import TaskProcedureBoundary
        from ...goal_policy import source_guidance_catalogue
        from .atomic_generation import AtomicGuidance
        from .memory_generation import MemoryGuidance

        expected_goals = tuple(manifest)
        if type(cached_contract) is not TaskContract \
                or cached_contract.as_dict() != contract.as_dict() \
                or type(cached_spec) is not GenerationSpec \
                or cached_spec.max_body_steps != spec.max_body_steps \
                or cached_target != target \
                or type(cached_reference) is not ReferenceArtifact \
                or cached_reference.digest != reference.digest \
                or type(instance) not in (BehaviorInstance, AtomicGuidance, MemoryGuidance) \
                or instance.bound != spec.max_body_steps \
                or type(manifest) is not CatalogueManifest \
                or source_guidance_catalogue(contract, instance.frontend_catalogue_manifest(),
                                             artifact_kind="behavior") != manifest \
                or type(goals) is not tuple or goals != expected_goals \
                or (type(instance) is BehaviorInstance and type(boundary) is not TaskProcedureBoundary) \
                or (type(instance) in (AtomicGuidance, MemoryGuidance) and boundary is not None) \
                or value["contract"] != contract.digest \
                or value["generation_spec"] != cached_spec.as_dict() \
                or value["target"] != target \
                or value["source"] != reference.digest \
                or value["catalogue"] != manifest.digest():
            return None
        return built
    except Exception:  # noqa: BLE001 - malformed cache is an ordinary miss
        return None


def _build_with_context(payload, extras=()):
    encoded = payload.get("cached_context")
    raw = None
    if encoded is not None:
        if type(encoded) is str:
            try:
                raw = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (UnicodeError, ValueError):
                raw = None
            if raw is not None \
                    and base64.b64encode(raw).decode("ascii") != encoded:
                raw = None
        built = _validated_context(payload, extras, raw)
        if built is not None:
            # The three source builders consume only max_body_steps. A new
            # recorded schedule may change horizon/reset/drain/stall without
            # changing that source plan. Rebind the complete execution spec;
            # do not carry any selected trace, state or solver answer across.
            spec = _generation_spec(payload["generation_spec"])
            if spec.as_dict() != built[1].as_dict():
                built = (built[0], spec, *built[2:])
                raw = _context_image(built)
            return built, raw, True
    built = _build(payload, extras)
    return built, _context_image(built), False


def _context_reply(answer, context, reused):
    if type(answer) is not dict or type(reused) is not bool:
        raise TypeError("a Behavior generation context reply carries typed values")
    return {
        "version": _CONTEXT_REPLY_VERSION,
        "answer": answer,
        "context_base64": (
            None if context is None else base64.b64encode(context).decode("ascii")),
        "context_reused": reused,
        "context_schema": _CONTEXT_PAYLOAD_SCHEMA,
    }


def _targets(value, available):
    if type(value) is not list or any(type(goal) is not str or not goal
                                      for goal in value):
        raise TypeError("Behavior generation targets are an exact string list")
    if value != sorted(set(value)):
        raise ValueError("Behavior generation targets are sorted and distinct")
    unknown = sorted(set(value) - set(available))
    if unknown:
        raise Refused(Refusal(
            "behavior-generation-target-without-exact-encoding",
            "a requested source goal has no exact local encoding",
            goals=unknown))
    return tuple(value)


def _occurrences(instance, boundary, seed, trace, contract):
    """Map accepted transactions to source calls without executing the IR."""
    if trace.stimulus != seed or trace.stimulus.fingerprint != seed.fingerprint:
        raise Refused(Refusal(
            "behavior-generation-source-trace-wrong-seed",
            "the accepted target trace names another complete stimulus"))
    if trace.completion is not Completion.NORMAL or len(trace.records) != len(seed.slots):
        raise Refused(Refusal(
            "behavior-generation-source-trace-incomplete",
            "selection needs one normally completed target record per schedule slot",
            completion=trace.completion.value,
            records=len(trace.records), slots=len(seed.slots)))
    if trace.artifact == "":
        raise Refused(Refusal(
            "behavior-generation-source-trace-artifact-unbound",
            "the accepted target trace does not identify the artifact that ran"))
    releases = queue_release_slots(
        contract, seed, (record.accepted for record in trace.records))
    transaction = {row.endpoint: 0 for row in boundary.inputs}
    fields_by_endpoint = {row.endpoint: row.fields for row in boundary.inputs}
    step_position = 0
    out = []
    # Mapping this one trace neither executes nor changes the procedure. Bind
    # its structural identity once, not once per accepted transaction.
    occurrence_prefix = f"{instance.identity}:{seed.fingerprint}:"
    for slot, record in enumerate(trace.records):
        accepted_by_endpoint = dict(record.accepted)
        active = {
            endpoint: accepted_by_endpoint.get(endpoint)
            for endpoint in fields_by_endpoint
            if accepted_by_endpoint.get(endpoint) is not None
        }
        if seed.slots[slot].reset:
            step_position = 0
            for endpoint, payload in active.items():
                fields = fields_by_endpoint[endpoint]
                if len(payload) != len(fields) \
                        or transaction[endpoint] >= seed.depth(endpoint):
                    raise Refused(Refusal(
                        "behavior-generation-source-map-outside-seed",
                        "an accepted reset-slot payload cannot be mapped to its "
                        "task endpoint queue",
                        endpoint=endpoint, slot=slot,
                        transaction=transaction[endpoint]))
                transaction[endpoint] += 1
            continue
        if not active:
            continue
        from ...behavior.task_boundary import (
            TaskBoundaryError,
            accepted_input_valuation,
        )

        try:
            logical = accepted_input_valuation(boundary, active)
        except TaskBoundaryError as exc:
            raise Refused(Refusal(
                exc.code, str(exc), slot=slot, **exc.detail)) from None
        bindings = []
        occurrence_parts = []
        for endpoint in sorted(active):
            payload = active[endpoint]
            fields_by_name = fields_by_endpoint[endpoint]
            index = transaction[endpoint]
            if index >= seed.depth(endpoint) \
                    or len(payload) != len(fields_by_name):
                raise Refused(Refusal(
                    "behavior-generation-source-map-outside-seed",
                    "the accepted target trace cannot be mapped one-to-one to its "
                    "seed endpoint queue",
                    endpoint=endpoint, slot=slot, transaction=index))
            fields = tuple(
                StimulusField(endpoint, index, field, releases[endpoint, index])
                for field in range(len(fields_by_name)))
            bindings.extend(
                (logical, field)
                for (_public, logical), field in zip(fields_by_name, fields))
            occurrence_parts.append(f"{endpoint}:{index}")
        occurrence_id = (
            occurrence_prefix +
            f"{'+'.join(occurrence_parts)}@{slot}")
        out.append({
            "id": occurrence_id,
            "rank": slot,
            "position": step_position,
            "bindings": tuple(bindings),
            "input": logical,
        })
        for endpoint in active:
            transaction[endpoint] += 1
        step_position += 1
    return tuple(out)


def _interpret_occurrences(instance, occurrences, contract, observations):
    """Reconstruct a one-shot Probe's states when it does not request feedback."""
    from ...symbolic import interp
    from ...behavior.reference_memory import reset_state
    from ...symbolic.state_projection import preserved_on_reset

    proc, state, observed = instance.proc, instance.proc.initial, []
    previous = {}
    external = tuple(memory.name for memory in contract.memories)
    preserved = preserved_on_reset(proc)
    for item in occurrences:
        if item["position"] == 0:
            state = reset_state(proc.initial, state, external, preserved_fields=preserved)
        before = state
        try:
            state, outputs, hits, error = interp.run_step(proc, item["input"], state)
        except Exception as exc:  # noqa: BLE001 - worker-side source reconstruction
            raise Refused(Refusal(
                "behavior-generation-local-interpreter-failed",
                "the isolated lowered source could not replay an accepted target position",
                slot=item["rank"], error=f"{type(exc).__name__}: {exc}")) from None
        prior = dict(previous)
        hits |= observations.observe(previous, item["input"], outputs.transactions)
        observed.append({**item, "state": before, "previous": prior, "hits": tuple(sorted(hits))})
        if error:
            break
    return tuple(observed)


def _requests(occurrences, targets, manifest, ruled_out, domains, *, dependencies=None, controls=(),
              factory=ProbeRequest):
    occurrences = tuple(occurrences)
    rank_span = 1 + max((item["rank"] for item in occurrences), default=0)
    field_sets = {item["id"]: _field_sets(item["bindings"], domains) for item in occurrences}
    if controls:
        # Query the existing command's payload first. The original widening
        # ladder, including all fields, remains available as a fallback.
        for item in occurrences:
            payload = tuple(field for name, field in item['bindings'] if name not in controls)
            scopes = field_sets[item['id']]
            if payload and payload != scopes[-1]:
                field_sets[item['id']] = (payload, *(s for s in scopes if s != payload))
    tier_span = max((len(sets) for sets in field_sets.values()), default=1)
    out = []
    for item in occurrences:
        # A zero-payload event still invokes the model and records its hits.
        # This local payload search has no free input at that occurrence;
        # do not send a fabricated mutation request to the common scheduler.
        if not item["bindings"]:
            continue
        for group, selected in _remaining_goal_groups(
                manifest, targets, item["hits"], ruled_out, dependencies=dependencies):
            for tier, fields in enumerate(field_sets[item["id"]]):
                out.append(factory(
                    selected, occurrence=item["id"], mutable_fields=fields,
                    rank=(group * rank_span + item["rank"]) * tier_span + tier,
                    catalogue_digest=manifest.digest()))
    return (tuple(sorted(out, key=lambda row: row.rank))
            if all(type(row) is QueryTemplate for row in out)
            else _ordered_requests(out))


def _local_hit(instance, occurrence, goals, timeout_ms, tag, input_domain=None, observations=None,
               *, preparation=None, proved_unsat=None, constructions=None, fixed_inputs=None):
    from ..local_ir import local_hit_any

    options = {
        "timeout_ms": timeout_ms, "tag": tag,
        "fixed_inputs": fixed_inputs,
        "observation_query": (None if observations is None else
                              observations.at(instance.proc, occurrence["previous"])),
        "input_constraints": (None if input_domain is None else
                              lambda inputs: input_domain.constraints(occurrence["rank"], inputs)),
    }
    if preparation is not None:
        return preparation.probe(occurrence["state"], goals, _proved_unsat=proved_unsat,
                                 _constructions=constructions, **options)
    return local_hit_any(instance.proc, instance.env, occurrence["state"], goals, **options)


def _candidate(seed, occurrence, values):
    return _candidate_many(seed, ((occurrence, values),))


def _candidate_many(seed, updates):
    # Copy the queues once, not once per jointly changed input.
    queues = {name: [list(transaction) for transaction in txns]
              for name, txns in seed.queues}
    for occurrence, values in updates:
        bindings = dict(occurrence["bindings"])
        if type(values) is not dict or set(values) != set(bindings):
            raise Refused(Refusal(
                "behavior-generation-decode-shape",
                "the exact candidate does not cover the selected logical inputs"))
        for name, value in values.items():
            field = bindings[name]
            queues[field.endpoint][field.transaction][field.field] = value
    return Stimulus(
        queues={name: [tuple(transaction) for transaction in txns]
                for name, txns in queues.items()},
        slots=seed.slots, memory_policy=dict(seed.memory_policy),
        memory_environment=seed.memory_environment,
        initial_state=dict(seed.initial_state), source=seed.source,
        clock_schedule=seed.clock_schedule)


def _layout(built, *, ruled_out=None):
    _contract, _spec, _target, _reference, instance, manifest, goals, boundary = built
    if ruled_out is None:
        from ...behavior.goal_projection import literal_false_goals

        ruled_out = literal_false_goals(instance.proc) if boundary is not None else ()
    return BehaviorGenerationLayout(
        instance.identity, manifest, goals,
        (instance.input_endpoints if boundary is None else
         tuple((row.endpoint, row.fields) for row in boundary.inputs)),
        frontend_catalogue=instance.frontend_catalogue_manifest(),
        constant_false_goals=tuple(sorted(set(goals) & set(ruled_out)))).as_dict()


def _replay_feedback(instance, manifest, seed, occurrences, observations):
    """Collect states and credit hits from the one native source/IR audit.

    A source run keeps external memory across computation reset. Reset positions
    must therefore remain in the audit, not become independently initialized runs.
    The existing source audit checks state, outputs, hits and partial failures;
    neither a symbolic model nor the replay target's catalogue supplies feedback.
    """
    from ...core.audit import AuditCheckStatus
    from ...core.record_value import canonical_json

    inputs = tuple(item["input"] for item in occurrences)
    resets = tuple(index for index, item in enumerate(occurrences)
                   if index and item["position"] == 0)
    observed = []
    previous = {}

    def collect(_trace_index, step, state, hits, outputs):
        item = occurrences[step]
        prior = dict(previous)
        hits = hits | observations.observe(previous, item["input"], outputs)
        observed.append({**item, "state": state, "previous": prior, "hits": tuple(sorted(hits))})

    if inputs:
        checked = instance.source_audit(
            (inputs,), reset_indices=(resets,), observe_step=collect)
        if checked.status is not AuditCheckStatus.AGREED:
            raise Refused(Refusal(
                "behavior-generation-feedback-unverified",
                "source/IR replay did not establish the selected seed's goal hits",
                audit=checked.as_dict()))
    # A matched source failure ends this source's execution even when the target
    # accepted more inputs. Do not create local queries after that terminal step.
    occurrences = tuple(observed)
    inputs = inputs[:len(occurrences)]
    resets = tuple(index for index in resets if index < len(occurrences))
    hits = {goal for item in occurrences for goal in item["hits"]}
    image = {"seed": seed.fingerprint, "catalogue": manifest.digest(),
             "inputs": inputs, "reset_indices": resets, "hits": sorted(hits)}
    return (ReplayFeedback(seed.fingerprint, manifest.digest(),
                           hashlib.sha256(canonical_json(image).encode()).hexdigest(), hits),
            occurrences)


def _observe_trace(built, seed, trace, *, verify_feedback=True):
    """Read one authenticated execution without constructing goal queries."""
    contract, _spec, target, _reference, instance, manifest, _available, boundary = built
    from ...behavior.transaction_goals import TransactionQueries
    from ...core.input_framing import LocalInputDomain

    observations = instance.transaction_goals
    if observations is None:
        observations = TransactionQueries(contract, boundary)
    seed, trace = Stimulus.from_dict(seed), AuditedTrace.from_dict(trace)
    if trace.artifact != target:
        raise Refused(Refusal(
            "behavior-generation-source-trace-wrong-artifact",
            "the accepted trace was not produced by the bound target artifact",
            expected=target, got=trace.artifact))
    occurrences = _occurrences(instance, boundary, seed, trace, contract)
    domain = LocalInputDomain(contract, seed, trace.records,
        {projection.endpoint: projection.fields for projection in boundary.inputs})
    if verify_feedback:
        replay, occurrences = _replay_feedback(
            instance, manifest, seed, occurrences, observations)
    else:
        replay = None
        occurrences = _interpret_occurrences(instance, occurrences, contract, observations)
    if instance.proc.input_framing:
        frame_entries = domain.entry_states()
        occurrences = tuple({**item, "framing": frame_entries[item["rank"]]}
                            for item in occurrences)
    return seed, observations, domain, replay, occurrences


class SelectedTrace:
    """Worker-owned source observation and its current local request selection.

    The source context is fixed by the worker, the trace is bound to its target,
    and every Probe must be one of this exact selection's requests. Changing
    targets reuses the same checked
    source observation; it does not replay the seed or reconstruct its states.
    The latest complete exact-local UNSAT question per target group can be shared
    by selections in the same prepared source/contract session, after each seed's audit.
    Each field scope retains only its latest exact question's unfinished
    disjunction and completed local UNSAT groups. Widening another scope does
    not discard that work, but a different state/domain/target/kept value does.
    Paused work is not a verdict; SAT candidates are never retained.
    """

    def __init__(self, built, seed, trace, targets, *, verify_feedback=True,
                 ruled_out=None, preparation=None, proved_questions=None, preparation_recipes=None):
        _contract, _spec, target, _reference, instance, manifest, available, boundary = built
        self.instance, self.manifest = instance, manifest
        self.input_domains = dict(instance.proc.semantics.input_domains)
        from ..local_ir import _LocalQueryPreparation

        if preparation is not None and (preparation._proc is not instance.proc
                                        or preparation._env != instance.env):
            raise ValueError("local query preparation belongs to another procedure or carrier")
        self._query_preparation = (preparation if preparation is not None else
                                   _LocalQueryPreparation(instance.proc, instance.env))
        self._partial_queries = {}
        if ruled_out is None:
            from ...behavior.goal_projection import literal_false_goals

            ruled_out = literal_false_goals(instance.proc)
        self.ruled_out = ruled_out
        self.available = available
        (self.seed, self.observations, self.input_domain,
         self.replay, occurrences) = _observe_trace(built, seed, trace,
                                                  verify_feedback=verify_feedback)
        self.occurrences = {item["id"]: item for item in occurrences}
        from .preparation_frontier import write_locations

        self._write_locations = write_locations(occurrences)
        from .prefix_generation import prefixes

        self.prefixes = prefixes(occurrences) if instance.proc.state else {}
        self._setup_proposals = {}
        from ...core.record_value import canonical_json
        from .preparation_frontier import restore_recipes

        self._preparation_binding = hashlib.sha256(canonical_json((
            _contract.digest, _spec.as_dict(), target, _reference.digest,
            manifest.digest(), seed, trace)).encode()).hexdigest()
        if type(preparation_recipes) is dict:
            preparation_recipes = restore_recipes(preparation_recipes, self._preparation_binding,
                self.prefixes, instance.proc, manifest, available)
        self.preparation_recipes = preparation_recipes
        if verify_feedback and self.prefixes:
            from .preparation_frontier import PreparationFrontier

            owner = self._query_preparation
            if owner._preparation_frontier is None:
                owner._preparation_frontier = PreparationFrontier(owner.command_preparations())
            owner._preparation_frontier.observe(occurrences)
        self._prefix_query = None
        self._path_query = None
        self.targets = None
        self._unsat_questions = {} if proved_questions is None else proved_questions
        self.select(targets)

    def select(self, targets):
        targets = _targets(targets, self.available)
        if targets != self.targets:
            if self.targets is None:
                # Selection templates belong to the accepted trace and complete
                # source catalogue. Native hits only restrict their targets;
                # they must not renumber or reconstruct unrelated questions.
                self._request_templates = self._build_requests()
                self._context_identities = self._context_keys(self._request_templates)
            wanted = set(targets)
            requests = []
            for item in self._request_templates:
                kept = tuple(goal for goal in item.targets if goal in wanted)
                if kept:
                    requests.append(item if kept == item.targets else item.with_targets(kept))
            self.requests = QueryPlan(requests, self.manifest.digest())
            self.requests = QueryPlan(requests, self.manifest.digest(),
                equivalents=self._equivalent_requests(), chains=self._query_chains(),
                explorations=tuple(item.rank for item in requests
                                   if item.occurrence.startswith(_CONTEXT_PATH)
                                   or item.occurrence in self._setup_proposals),
                contexts=tuple((item.rank, self._context_identities[item.rank]) for item in requests
                               if item.rank in self._context_identities),
                preparations=tuple(item.rank for item in requests
                                   if item.occurrence in self._setup_proposals))
            self.targets = targets

    @property
    def equivalent_requests(self):
        return self.requests.request_groups(self.requests.equivalents)

    @property
    def query_chains(self):
        return self.requests.request_groups(self.requests.chains)

    def _build_requests(self):
        from .prefix_generation import (
            _control_inputs,
            preparation_requests,
            requests as prefix_requests,
        )
        from .preparation_frontier import entry_facts
        from .generation import _remaining_goal_groups
        from ...core.catalogue import SAFETY

        dependencies = self._query_preparation.state_dependencies()
        make_template = QueryTemplate.factory()
        local = _requests(self.occurrences.values(), self.available, self.manifest,
                          self.ruled_out, self.input_domains, dependencies=dependencies,
                          controls=_control_inputs(self.instance.proc),
                          factory=make_template)
        if not self.prefixes:
            return self._with_path_requests(local)
        # Reuse already reached states before rebuilding their setup from reset.
        # This changes work order only: no local/joint question is discarded.
        facts = entry_facts(self.occurrences.values(), self.instance.proc,
                            self._query_preparation.command_preparations())
        native_hits = {goal for item in self.occurrences.values() for goal in item['hits']}
        priorities, first_local = {}, {}
        for item in local:
            first = item.targets[0]
            group = self.manifest[first].cls, dependencies.get(first, 0)
            if group not in priorities:
                priorities[group] = frozenset(item.targets) - native_hits
            active, reachable = facts[item.occurrence]
            compatible = reachable is None or bool(priorities[group] & reachable)
            activity = (active & group[1]).bit_count() if priorities[group] else 0
            priority = (not compatible, -activity, item.rank)
            if group not in first_local or priority < first_local[group][0]:
                first_local[group] = priority, item.key, compatible and activity > 0
        prepared_local = {key for _priority, key, ready in first_local.values() if ready}
        first_local = {key for _priority, key, _ready in first_local.values()}
        ordered = [item.with_rank(2 * index)
                   for index, item in enumerate(local)]
        ordered.extend(prefix_requests(self.prefixes, self.available, self.manifest,
                       self.ruled_out, self.observations.catalogue, self.instance.proc,
                       dependencies=dependencies, factory=make_template))
        groups = _remaining_goal_groups(self.manifest,
            tuple(g for g in self.available if g not in self.observations.catalogue),
            (), self.ruled_out, dependencies=dependencies)
        frontier = self._query_preparation._preparation_frontier
        if self.preparation_recipes is not None:
            for template, recipe in self.preparation_recipes:
                self._setup_proposals[template.occurrence] = tuple(
                    (self.occurrences[identity], values) for identity, values in recipe)
                ordered.append(template)
        elif frontier is not None and self.replay is not None:
            for request, updates in frontier.proposals(self.prefixes, groups, self.instance.proc,
                    self.manifest, dependencies, factory=make_template):
                self._setup_proposals[request.occurrence] = updates
                ordered.append(request)
        self.preparation_recipes = tuple((request, tuple(
            (item['id'], values) for item, values in self._setup_proposals[request.occurrence]))
            for request in ordered if request.occurrence in self._setup_proposals)
        prepared = tuple(preparation_requests(self.prefixes, groups, self.instance.proc,
            self.manifest, dependencies, self._query_preparation.command_preparations(),
            factory=make_template))
        ordered.extend(prepared)
        ordered.sort(key=lambda item: (self.manifest[item.targets[0]].cls == SAFETY,
                     item.key not in prepared_local,
                     not item.occurrence.startswith("state-setup:"),
                     item.key not in first_local,
                     not item.occurrence.startswith("prepare:"),
                     item.rank, item.occurrence, item.key))
        return self._with_path_requests(ordered)

    def _with_path_requests(self, requests):
        """Propose, then query the native path and full source independently."""
        ordered = []
        for item in requests:
            if (item.occurrence in self.occurrences or item.occurrence in self.prefixes) and any(
                    goal not in self.observations.catalogue for goal in item.targets):
                if item.occurrence in self.occurrences:
                    ordered.append(item.at(_PROPOSAL_PATH + item.occurrence, len(ordered)))
                ordered.append(item.at(_NATIVE_PATH + item.occurrence, len(ordered)))
            ordered.append(item.with_rank(len(ordered)))
        # A static branch hit says nothing about its later state contexts.
        # Try the existing command's payload before widening its control fields;
        # no public goals are invented and native replay remains mandatory.
        from .generation import _remaining_goal_groups
        from .prefix_generation import _control_inputs
        from .preparation_frontier import entry_facts
        from ...core.catalogue import COVERAGE
        from ...symbolic import ir

        source_goals = ir.goals_of(self.instance.proc.body)
        coverage = tuple(goal for goal in self.available
                         if self.manifest[goal].cls == COVERAGE and goal in source_goals)
        dependencies = self._query_preparation.state_dependencies()
        groups = _remaining_goal_groups(self.manifest, coverage, (), self.ruled_out,
                                       dependencies=dependencies)
        controls = _control_inputs(self.instance.proc)
        facts = entry_facts(self.occurrences.values(), self.instance.proc,
                            self._query_preparation.command_preparations())
        make_template = QueryTemplate.factory()
        contexts = []
        for occurrence in self.occurrences.values():
            fields = tuple(field for _, field in occurrence['bindings'])
            if not fields:
                continue
            payload = tuple(field for name, field in occurrence['bindings'] if name not in controls)
            scopes = (payload, fields) if payload and payload != fields else (fields,)
            active, reachable = facts[occurrence['id']]
            hits = frozenset(occurrence['hits'])
            for _group, targets in groups:
                # Extend a command that already exercised this family before
                # changing an unrelated setup command into a shallow trigger.
                priority = (not bool(hits.intersection(targets)),
                            reachable is not None and not bool(set(targets) & reachable),
                            -(active & dependencies.get(targets[0], 0)).bit_count(),
                            -len(hits), occurrence['rank'])
                for tier, scope in enumerate(scopes):
                    contexts.append(((*priority, tier), make_template(
                        targets, _CONTEXT_PATH + occurrence['id'], scope, 0,
                        catalogue_digest=self.manifest.digest())))
        # For each source-state family, query the real setup ending AT its
        # observed trigger before pinning that state in a local query. A local
        # miss cannot stand in for changing the inputs that built the state.
        # Fixed-size tiles need not end at the trigger.
        # One trigger per family. Widen its setup backwards from the native
        # entry rather than making every question rebuild the entire history.
        from .prefix_generation import (array_setup_writers, continuation_requests, first_consumer_windows,
                                        requests as prefix_requests, setup_window)

        effects = self._query_preparation.command_preparations()
        epochs, epoch = {}, []
        for occurrence in self.occurrences.values():
            if occurrence['position'] == 0:
                epoch = []
            epoch.append(occurrence)
            epochs[occurrence['id']] = epoch
        anchored, context_keys, writer_scopes = set(), set(), {}

        def append_context(item):
            if item.occurrence.startswith(_CONTEXT_PATH + 'prefix:'):
                identity = item.occurrence.removeprefix(_CONTEXT_PATH)
                mask = dependencies.get(item.targets[0], 0)
                key = identity, mask
                if key not in writer_scopes:
                    window = self.prefixes[identity]
                    writers = array_setup_writers(window, self.instance.proc, effects,
                                                   mask, self._write_locations.keys())
                    writer_scopes[key] = tuple(field for writer in writers
                        for name, field in writer['bindings'] if name not in controls)
                fields = writer_scopes[key]
                if fields:
                    # Apply to fallback histories too, not only the first
                    # static-goal anchor. Keep every consumer invocation fixed
                    # while asking how its source-identified setup can change.
                    narrow = make_template(item.targets, item.occurrence, fields, 0,
                                           catalogue_digest=self.manifest.digest())
                    if narrow.key not in context_keys:
                        ordered.append(narrow.with_rank(len(ordered)))
                        context_keys.add(narrow.key)
            if item.key not in context_keys:
                ordered.append(item.with_rank(len(ordered)))
                context_keys.add(item.key)

        continuations = {}
        for item in continuation_requests(self.occurrences.values(), self.prefixes, groups,
                self.instance.proc, self.manifest, dependencies, effects, factory=make_template):
            consumer = self.prefixes[item.occurrence][-1]
            key = consumer['id'], dependencies.get(item.targets[0], 0)
            continuations.setdefault(key, []).append(item)
        for _priority, item in sorted(contexts, key=lambda row: row[0]):
            occurrence = self.occurrences[_path_identity(item.occurrence)]
            if (item.targets not in anchored and dependencies.get(item.targets[0], 0)
                    and occurrence['position'] > 0
                    and set(item.targets).intersection(occurrence['hits'])):
                # A trigger at reset has no setup to vary. It must not consume
                # the family's anchor and hide every later stateful trigger.
                anchored.add(item.targets)
                epoch = epochs[occurrence['id']][:occurrence['position'] + 1]
                # A nearby query need not write the state that the trigger
                # reads. Try a source-ranked setup writer first, then retain the
                # complete original window ladder. Effects only rank domains;
                # they never prune source transitions or establish a verdict.
                setup = setup_window(epoch, self.instance.proc, effects, dependencies[item.targets[0]])
                writers = tuple(array_setup_writers(epoch, self.instance.proc, effects,
                        dependencies[item.targets[0]], self._write_locations.keys()))
                for writer in writers:
                    fields = tuple(field for name, field in writer['bindings'] if name not in controls)
                    if fields:
                        append_context(make_template(item.targets,
                            _CONTEXT_PATH + writer['id'], fields, 0,
                            catalogue_digest=self.manifest.digest()))
                preferred = (setup,) if setup else ()
                consumer_windows = tuple(first_consumer_windows(epoch, writers, effects))
                seen = set()
                for window in (*consumer_windows, *preferred, *ending_windows(epoch)):
                    key = window[0]['id'], len(window)
                    if key in seen:
                        continue
                    seen.add(key)
                    identity = f"prefix:{window[0]['id']}:{len(window)}"
                    self.prefixes.setdefault(identity, window)
                    for joint in prefix_requests({identity: window}, item.targets, self.manifest,
                            self.ruled_out, (), self.instance.proc, dependencies=dependencies,
                            factory=make_template):
                        append_context(joint.at(_CONTEXT_PATH + identity, len(ordered)))
            # Retain the source's existing setup priority, then extend this
            # same consumer rather than front-loading unrelated preparations.
            key = occurrence['id'], dependencies.get(item.targets[0], 0)
            for prepared in continuations.pop(key, ()):
                if any(goal not in self.observations.catalogue for goal in prepared.targets):
                    ordered.append(prepared.at(_NATIVE_PATH + prepared.occurrence, len(ordered)))
                append_context(prepared.at(_CONTEXT_PATH + prepared.occurrence, len(ordered)))
            append_context(item)
        # The earlier inputs that establish this state may need to change too.
        # Reuse the same reset-safe windows and fixed-framing field scopes.
        # Continuations already have their exact writer-only scope above;
        # their prepared consumer remains fixed, so broader field declarations
        # would merely schedule the same constraints under different keys.
        fallback = {key: window for key, window in self.prefixes.items()
                    if not key.startswith('prepare:continuation:')}
        for item in prefix_requests(fallback, coverage, self.manifest,
                self.ruled_out, (), self.instance.proc,
                dependencies=self._query_preparation.state_dependencies(), factory=make_template):
            append_context(item.at(_CONTEXT_PATH + item.occurrence, len(ordered)))
        return tuple(ordered)

    def _context_keys(self, requests):
        """Source-local scheduling hints, never exact question/proof identities.

        Remember attempted entry contexts across different stimulus digests.
        Hash each native state once; retain digests, not large state histories.
        Payload changes alone do not make an old solver entry unexplored again.
        Replay-only setup recipes instead retain their concrete input choices:
        they prepare new states even when their static target is already hit.
        """
        from .prefix_generation import _control_inputs

        from ...core.record_value import canonical_json

        controls = _control_inputs(self.instance.proc)
        states, keys = {}, {}

        def state_key(item):
            identity = item['id']
            if identity not in states:
                states[identity] = hashlib.sha256(canonical_json(
                    self._state_key(item, None)).encode()).hexdigest()
            return states[identity]

        for request in requests:
            recipe = self._setup_proposals.get(request.occurrence)
            if recipe is not None:
                keys[request.rank] = hashlib.sha256(canonical_json((
                    'state-setup', request.targets, state_key(recipe[0][0]),
                    tuple((item['position'], values) for item, values in recipe)
                )).encode()).hexdigest()
                continue
            if not request.occurrence.startswith(_CONTEXT_PATH):
                continue
            identity = _path_identity(request.occurrence)
            window = self.prefixes.get(identity)
            window = (self.occurrences[identity],) if window is None else window
            mutable = {field.coordinate for field in request.mutable_fields}
            scopes = tuple(tuple(name for name, field in item['bindings']
                                 if field.coordinate in mutable) for item in window)
            commands = tuple(tuple((name, item['input'][name]) for name in sorted(controls)
                                   if name in item['input']) for item in window)
            keys[request.rank] = hashlib.sha256(canonical_json((request.targets,
                state_key(window[0]), state_key(window[-1]), scopes, commands,
                tuple(item.get('prepared_inputs', {}) for item in window),
                self._write_locations)).encode()).hexdigest()
        return keys

    def _query_chains(self):
        groups = {}
        anchors = {}
        for request in self.requests.templates:
            if request.occurrence.startswith(_CONTEXT_PATH):
                continue
            window = self.prefixes.get(_path_identity(request.occurrence))
            if window is None:
                continue
            mutable = {field.coordinate for field in request.mutable_fields}
            scope = tuple(name for name, field in window[0]["bindings"]
                          if field.coordinate in mutable)
            # Same concrete entry, target cone and field scope. Prefix binding
            # still validates every old fixed input before sharing construction.
            key = window[0]["id"], request.targets, scope, window[0].get("preparation")
            groups.setdefault(key, []).append(request)
            anchors.setdefault(key[:2], key)
        # Exhaust the selected native entry's field scopes before enlarging
        # its window. Keep their exact answers separate; chain membership is
        # construction/scheduling affinity, not shared satisfiability.
        local = {}
        for request in self.requests.templates:
            if request.occurrence.startswith(_CONTEXT_PATH):
                continue
            occurrence = _path_identity(request.occurrence)
            if occurrence in self.occurrences:
                local.setdefault((occurrence, request.targets), []).append(request)
        for identity, requests in local.items():
            key = anchors.get(identity)
            if key is None:
                groups[identity] = requests
            else:
                groups[key][0:0] = requests
        return tuple(tuple(item.rank for item in sorted(group,
                     key=lambda item: len(self.prefixes.get(
                         _path_identity(item.occurrence), (None,)))))
                     for group in groups.values() if len(group) > 1)

    def _equivalent_requests(self):
        groups = {}
        states, windows, native_inputs = {}, {}, {}
        for request in self.requests.templates:
            if (request.occurrence in self._setup_proposals
                    or request.occurrence.startswith((_PROPOSAL_PATH, _CONTEXT_PATH))):
                continue  # A proposal has no reusable UNSAT answer.
            if request.occurrence in self.prefixes:
                question = self._window_question(request, states=states, windows=windows)
            elif request.occurrence.startswith(_NATIVE_PATH):
                identity = request.occurrence.removeprefix(_NATIVE_PATH)
                if identity in self.prefixes:
                    if identity not in native_inputs:
                        native_inputs[identity] = freeze_record_value(tuple(
                            item['input'] for item in self.prefixes[identity]))
                    question = ('native-window', self._window_question(
                        request, states=states, windows=windows), native_inputs[identity])
                    groups.setdefault(question, []).append(request.rank)
                    continue
                item = self.occurrences[identity]
                # Mutable input values do not bind the complete local question,
                # but they select this concrete path. Never merge those scopes.
                question = ('native-path', self._question(item, request, states=states),
                            freeze_record_value(item['input']))
            else:
                question = self._question(self.occurrences[request.occurrence], request, states=states)
            groups.setdefault(question, []).append(request.rank)
        return tuple(tuple(group) for group in groups.values() if len(group) > 1)

    @staticmethod
    def _state_key(occurrence, states):
        from ...behavior.value_records import domain_tree

        if states is None:
            return freeze_record_value(domain_tree(occurrence['state']))
        identity = occurrence['id']
        if identity not in states:
            states[identity] = freeze_record_value(domain_tree(occurrence['state']))
        return states[identity]

    def _window_question(self, request, *, states=None, windows=None):
        """Exact local identity, not equality of coverage or similarity of seeds.

        Windows share an UNSAT answer only with the same observed entry state,
        logical mutable fields, fixed inputs, framing context, and target set.
        Position-specific candidate decoding is deliberately not reused.
        """
        identity = request.occurrence.removeprefix(_NATIVE_PATH)
        key = identity, request.key[2] if type(request) is QueryTemplate else tuple(
            (f.endpoint, f.transaction, f.field, f.source_position) for f in request.mutable_fields)
        if windows is not None and key in windows:
            return ('window', request.targets, windows[key])
        window = self.prefixes[identity]
        mutable = {field.coordinate for field in request.mutable_fields}
        # Goal groups share an entry and input domain, not a solver verdict.
        # Freeze the potentially large state once per occurrence in this Select;
        # putting it inside the per-goal record would traverse it again anyway.
        domain = (self._state_key(window[0], states), freeze_record_value({
            "framing": window[0].get("framing"),
            "steps": tuple({
                "mutable": tuple(name for name, field in item["bindings"]
                                 if field.coordinate in mutable),
                "fixed": {name: item["input"][name] for name, field in item["bindings"]
                          if field.coordinate not in mutable},
                "prepared_inputs": item.get("prepared_inputs", {}),
            } for item in window),
        }))
        if windows is not None:
            windows[key] = domain
        return ('window', request.targets, domain)

    def as_dict(self):
        from .preparation_frontier import recipe_image

        return {
            "version": _SELECTION_VERSION,
            "status": "ok",
            "requests": self.requests.as_dict(),
            "equivalent_requests": [],
            "query_chains": [],
            "constant_false_goals": sorted(set(self.ruled_out) & set(self.targets)),
            "catalogue_digest": self.manifest.digest(),
            "replay": self.replay.as_dict(),
            "preparation_plan": recipe_image(self._preparation_binding, self.preparation_recipes),
        }

    def probe(self, request, timeout_ms):
        if type(timeout_ms) is not int or timeout_ms < 1:
            raise TypeError("a Behavior local probe timeout is a positive integer")
        request = ProbeRequest.from_dict(request)
        expected = self.requests.selected(request)
        if expected is None:
            raise Refused(Refusal(
                "behavior-generation-request-not-selected",
                "the Probe request is not one deterministically selected for this "
                "source/target trace"))
        if request.occurrence in self._setup_proposals:
            candidate = _candidate_many(self.seed, self._setup_proposals[request.occurrence]).as_dict()
            result = probing.ProbeResult(probing.Outcome.PROPOSED,
                probing.Scope.STATE_PREPARATION, candidate=candidate,
                detail="recombined observed state writers; native replay required; no solver verdict")
        elif request.occurrence.startswith((_NATIVE_PATH, _PROPOSAL_PATH, _CONTEXT_PATH)):
            from .path_generation import PathQuery, WindowPathQuery

            identity = _path_identity(request.occurrence)
            window = self.prefixes.get(identity)
            retained = self._path_query
            if retained is None or not request.is_restriction_of(retained[0]):
                if window is not None:
                    query = WindowPathQuery(self.instance, window, request.targets,
                        validation=self._query_preparation.validation(),
                        context=request.occurrence.startswith(_CONTEXT_PATH),
                        mutable_fields=request.mutable_fields, input_domain=self.input_domain,
                        write_locations=self._write_locations)
                else:
                    occurrence = self.occurrences[identity]
                    query = PathQuery(self.instance, occurrence, request.targets,
                        validation=self._query_preparation.validation(),
                        proposal=request.occurrence.startswith(_PROPOSAL_PATH),
                        context=request.occurrence.startswith(_CONTEXT_PATH),
                        write_locations=self._write_locations,
                        fixed_inputs=_fixed_inputs(occurrence['bindings'], request.mutable_fields,
                                                   occurrence['input']),
                        input_constraints=partial(self.input_domain.constraints, occurrence['rank']))
            else:
                query = retained[1]
            self._path_query = request, query
            result = query.probe(request.targets, timeout_ms)
            candidate = None
            if result.outcome in (probing.Outcome.SAT, probing.Outcome.PROPOSED):
                candidate = (_candidate(self.seed, self.occurrences[identity], result.candidate)
                    if window is None else _candidate_many(self.seed,
                        zip(window, result.candidate, strict=True))).as_dict()
        elif request.occurrence in self.prefixes:
            from .prefix_generation import PrefixPreparation, PrefixQuery

            retained = self._prefix_query
            if retained is None or not request.is_restriction_of(retained[0]):
                owner = self._query_preparation
                if owner._prefix_preparation is None:
                    owner._prefix_preparation = PrefixPreparation(self.instance.proc)
                # The coordinator may have split this family's targets among
                # lanes before the first Probe. Build that lane's actual cone,
                # not the original wider selection. Later narrowing can still
                # resume this same construction; a different subset gets its
                # own checked projection below.
                query = PrefixQuery(
                    self.instance, self.prefixes[request.occurrence], request.targets,
                    preparation=owner._prefix_preparation)
            else:
                # Native hits from another lane can remove targets while this
                # lane is constructing. Its broader transition relation still
                # answers the subset; do not discard the encoded prefix.
                query = retained[1]
            self._prefix_query = request, query
            result = query.probe(request, timeout_ms)
            candidate = None
            if result.outcome is probing.Outcome.SAT:
                candidate = _candidate_many(self.seed, zip(
                    self.prefixes[request.occurrence], result.candidate, strict=True)).as_dict()
        else:
            occurrence = self.occurrences[request.occurrence]
            result = self.local_hit(occurrence, request, timeout_ms)
            candidate = (None if result.outcome is not probing.Outcome.SAT else
                         _candidate(self.seed, occurrence, result.candidate).as_dict())
        return {
            "version": _PROBE_VERSION,
            "outcome": result.outcome.value,
            "scope": result.scope.value,
            "goal": result.goal,
            "candidate": candidate,
            "detail": result.detail,
            "solve_ms": result.solve_ms,
            "catalogue_digest": self.manifest.digest(),
        }

    def _question(self, occurrence, request, *, states=None):
        targets = request.targets

        # Procedure, input types/ranges, source and contract belong to this
        # prepared session. Only these contexts and the target set change the one-step
        # question; a physical slot number is not part of its formula. Framing
        # depends on the fixed suffix, and observed-history goals on the prefix.
        # History keys include immutable contract endpoints as well as monitor
        # identifiers. Keep those owner-bound keys, freezing only their values;
        # this private question is not a serialized execution record.
        history = (occurrence["previous"]
                   if any(goal in self.observations.catalogue for goal in targets) else {})
        return (tuple(history), targets, self._state_key(occurrence, states), freeze_record_value({
            "history": tuple(history.values()),
            "input_domain": self.input_domain.context_key(occurrence["rank"]),
            "fixed_inputs": _fixed_inputs(occurrence["bindings"], request.mutable_fields,
                                           occurrence["input"]),
        }))

    def local_hit(self, occurrence, request, timeout_ms):
        question = self._question(occurrence, request)
        if self._unsat_questions.get(request.targets) == question:
            return probing.ProbeResult(probing.Outcome.LOCAL_UNSAT, probing.Scope.EXACT_LOCAL_HIT,
                detail="identical audited local question already UNSAT in this source/contract context; no solver query")
        fixed = _fixed_inputs(occurrence["bindings"], request.mutable_fields,
                              occurrence["input"])
        # Names, not physical transaction coordinates: finite field scopes
        # alternate along a seed. Keep one latest question per scope, never a
        # growing history of states, occurrences or solver answers.
        scope = tuple(sorted(fixed))
        partial = self._partial_queries.get(scope)
        if partial is None or partial[0] != question:
            partial = self._partial_queries[scope] = (question, set(), {})
        _question, proved, constructions = partial
        answer = _local_hit(self.instance, occurrence, request.targets, timeout_ms,
                            f"_bgen_{request.identity[:12]}", self.input_domain, self.observations,
                            preparation=self._query_preparation, proved_unsat=proved,
                            constructions=constructions, fixed_inputs=fixed)
        if answer.outcome in (probing.Outcome.SAT, probing.Outcome.LOCAL_UNSAT):
            self._partial_queries.pop(scope)
        if answer.outcome is probing.Outcome.LOCAL_UNSAT:
            # Replace the preceding state/history for this target group. A
            # search must not retain every large state it has ever proved.
            self._unsat_questions[request.targets] = question
        return answer


def selected_trace(built, *args, preparation=None, proved_questions=None,
                   preparation_recipes=None, **kwargs):
    from .atomic_generation import AtomicGenerationSelection, AtomicGuidance
    from .memory_generation import MemoryGuidance

    if type(built[4]) is AtomicGuidance:
        return AtomicGenerationSelection(built, *args, **kwargs)
    if type(built[4]) is MemoryGuidance:
        return built[4].select(built, *args, **kwargs)
    return SelectedTrace(built, *args, preparation=preparation,
                         proved_questions=proved_questions,
                         preparation_recipes=preparation_recipes, **kwargs)


def layout_job(payload):
    context = None
    reused = False
    try:
        (built, context, reused) = _build_with_context(payload)
        answer = _layout(built)
    except Refused as exc:
        answer = _refusal_reply(_LAYOUT_VERSION, exc.refusal)
    except Exception as exc:  # noqa: BLE001 - closed worker refusal boundary
        answer = _refusal_reply(_LAYOUT_VERSION, Refusal(
            str(getattr(exc, "code", "") or "behavior-generation-layout-failed"),
            "the isolated Behavior source could not construct its guidance layout",
            error=f"{type(exc).__name__}: {exc}"))
    return _context_reply(answer, context, reused)


def select_job(payload):
    context = None
    reused = False
    try:
        (built, context, reused) = _build_with_context(
            payload, extras={"seed", "trace", "targets"})
        answer = selected_trace(built, payload["seed"], payload["trace"],
                               payload["targets"]).as_dict()
    except Refused as exc:
        answer = _refusal_reply(_SELECTION_VERSION, exc.refusal)
    except Exception as exc:  # noqa: BLE001 - closed worker refusal boundary
        answer = _refusal_reply(_SELECTION_VERSION, Refusal(
            str(getattr(exc, "code", "") or "behavior-generation-select-failed"),
            "the isolated Behavior source could not select local occurrences",
            error=f"{type(exc).__name__}: {exc}"))
    return _context_reply(answer, context, reused)


def probe_job(payload):
    digest = ""
    context = None
    reused = False
    try:
        (built, context, reused) = _build_with_context(
            payload, extras={
                "seed", "trace", "targets", "request", "timeout_ms"})
        digest = built[5].digest()
        selected = selected_trace(built, payload["seed"], payload["trace"], payload["targets"],
                                 verify_feedback=payload['request']['occurrence'].startswith('state-setup:'))
        answer = selected.probe(payload["request"], payload["timeout_ms"])
        # This job ends the selection's lifetime. The returned source context
        # contains no live query continuation, unlike a persistent search session.
        if answer["outcome"] == probing.Outcome.CONSTRUCTING.value:
            answer["outcome"] = probing.Outcome.UNKNOWN.value
    except Refused as exc:
        result = probing.ProbeResult(
            probing.Outcome.UNSUPPORTED, probing.Scope.EXACT_LOCAL_HIT,
            detail=f"{exc.refusal.kind}: {exc.refusal.why}")
        answer = {
            "version": _PROBE_VERSION,
            "outcome": result.outcome.value,
            "scope": result.scope.value,
            "goal": None,
            "candidate": None,
            "detail": result.detail,
            "solve_ms": 0.0,
            "catalogue_digest": digest,
        }
    except Exception as exc:  # noqa: BLE001 - closed worker refusal boundary
        result = probing.ProbeResult(
            probing.Outcome.UNSUPPORTED, probing.Scope.EXACT_LOCAL_HIT,
            detail=(str(getattr(exc, "code", "")
                        or "behavior-generation-probe-failed")
                    + ": " + f"{type(exc).__name__}: {exc}"[:220]))
        answer = {
            "version": _PROBE_VERSION,
            "outcome": result.outcome.value,
            "scope": result.scope.value,
            "goal": None,
            "candidate": None,
            "detail": result.detail,
            "solve_ms": 0.0,
            "catalogue_digest": digest,
        }
    return _context_reply(answer, context, reused)
