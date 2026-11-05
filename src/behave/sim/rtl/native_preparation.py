"""Judge-owned source observation preparation for both RTL Drivers.

This boundary returns immutable build inputs or a typed no-result. Candidate CST
and RTLIL are consumed only inside the tool judge; the host decodes a closed plan.
Preparing a build does not enable a Driver or remove an exact-analysis guard.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root
from behave.sim.core.source_rules import EMPTY_SOURCE_RULES, SourceGoalRules

from ..core.catalogue import CatalogueManifest
from ..core.failure import RunFailure
from ..core.record_value import canonical_json
from ..core.refusal import Refusal, Refused
from ..strict_json import strict_json_loads
from .elaboration import SOURCE_FILENAME
from .native_events import NativeEventPlan, check_callback_limit, check_scope_root
from .source_boundary_capture import ObservationField, decode_fields
from .source_goals import SCHEMAS, is_source_goal
from .tool_protocol import ToolExecution

VERSION = "rtl-native-preparation/47"
_OUTPUTS = ("prepared.json", "native.sv", "collector.cpp")
_REFUSALS = {"source-parse-failed", "original-compile-failed",
             "annotated-compile-failed", "source-mapping-unavailable", "unscheduled-trigger"}


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _request(value: object) -> dict:
    """One closed request language on both sides of the tool boundary."""
    if type(value) is not dict or set(value) != {
        "version", "source", "top", "toolchain", "execution", "callback_limit", "catalogue", "scope_root",
        "catalogue_kind", "source_schemas", "allowed_triggers", "source_rules",
    } or value["version"] != VERSION:
        raise ValueError("invalid native preparation request")
    if type(value["top"]) is not str or re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_$]*", value["top"]
    ) is None:
        raise ValueError("native preparation needs a simple top identifier")
    for key in ("source", "toolchain", "execution"):
        if type(value[key]) is not str or re.fullmatch(r"[0-9a-f]{64}", value[key]) is None:
            raise ValueError("native preparation identities must be SHA256 digests")
    if value["catalogue"] is not None and (
        type(value["catalogue"]) is not str
        or re.fullmatch(r"[0-9a-f]{64}", value["catalogue"]) is None
    ):
        raise ValueError("native preparation catalogue must be a SHA256 digest")
    # Keep storage/callback limits owned by the actual collector constructor.
    check_callback_limit(value["callback_limit"])
    check_scope_root(value["scope_root"])
    if value["catalogue_kind"] not in ("actions", "source", "mixed"):
        raise ValueError("unknown native catalogue kind")
    SourceGoalRules.from_dict(value["source_rules"])
    schemas = value["source_schemas"]
    if (type(schemas) is not list or any(type(schema) is not str for schema in schemas)
            or schemas != sorted(set(schemas)) or not set(schemas) <= SCHEMAS
            or (value["catalogue_kind"] == "actions" and schemas)
            or (value["catalogue_kind"] == "mixed" and not schemas)):
        raise ValueError("native schemas must match the frozen catalogue kind")
    triggers = value["allowed_triggers"]
    if triggers is not None and (
            type(triggers) is not list or any(
                type(pair) is not list or len(pair) != 2
                or pair[0] not in {"always", "posedge", "negedge"}
                or type(pair[1]) is not str
                or (pair[0] == "always") != (pair[1] == "") for pair in triggers)):
        raise ValueError("native schedule must declare explicit Driver trigger pairs")
    return value


@dataclass(frozen=True, slots=True)
class PreparedNativeEvents:
    source: str
    collector: str
    plan: NativeEventPlan
    request_sha256: str
    execution: ToolExecution
    boundary_fields: tuple[ObservationField, ...] = ()
    boundary_paths: tuple = ()
    startup_aliases: tuple | None = None


def prepare_scheduled_observer(source, *, top, contract, catalogue, timeout_s):
    """The replay admission boundary: one schedule, catalogue and finite budget."""
    from .execution_policy import execution_policy
    from .native_observation import VERSION as OBSERVATION_VERSION
    from .trigger_support import driver_triggers

    return prepare_source_observer(
        source, top=top, timeout_s=timeout_s, catalogue=catalogue,
        scope_root="",  # The locked cocotb Verilator launcher constructs Vtop("").
        allowed_triggers=driver_triggers(contract),
        source_rules=contract.source_rules,
        callback_limit=defaults.SIM_RTL_NATIVE_CALLBACKS,
        execution_sha256=_sha(canonical_json({
            "execution": execution_policy(native_events=True),
            "observer": OBSERVATION_VERSION, "contract": contract.digest,
        }).encode()))


def _decode(execution: ToolExecution, request_sha256: str, callback_limit: int | None,
            catalogue: CatalogueManifest | None = None,
            *, scope_root: str = "TOP",
            ) -> PreparedNativeEvents | RunFailure:
    """Check bounded judge outputs before any generated text becomes a build input."""
    outputs = execution.outputs
    image = strict_json_loads(outputs["prepared.json"], what="native preparation response")
    common = {"version", "request", "status"}
    if type(image) is not dict or image.get("version") != VERSION \
            or image.get("request") != request_sha256:
        raise ValueError("native preparation response is not bound to this request")
    if image.get("status") == "refused":
        if set(image) != common | {"code", "detail"} or set(outputs) != {"prepared.json"} \
                or image["code"] not in _REFUSALS or type(image["detail"]) is not str:
            raise ValueError("invalid native preparation refusal")
        return RunFailure.verifier("rtl-native-preparation", Refusal(
            "native-events-" + image["code"], "native source observation is unavailable",
            detail=image["detail"], tool_execution=execution.as_dict()))
    if image.get("status") != "prepared" or set(image) != common | {
        "plan", "native_sha256", "collector_sha256", "boundary_fields", "boundary_paths", "startup_aliases",
    } or set(outputs) != set(_OUTPUTS):
        raise ValueError("incomplete native preparation outputs")
    for key, filename in (("native_sha256", "native.sv"), ("collector_sha256", "collector.cpp")):
        if image[key] != _sha(outputs[filename]):
            raise ValueError("native preparation output digest mismatch")
    plan = NativeEventPlan.from_dict(image["plan"])
    if plan.callback_limit != callback_limit:
        raise ValueError("native collector changed the requested callback budget")
    if plan.scope_root != scope_root:
        raise ValueError("native collector changed the requested scope root")
    if catalogue is not None:
        plan.check_catalogue(catalogue)
    collector = outputs["collector.cpp"].decode("utf-8")
    if collector != plan.collector_source():
        raise ValueError("native collector differs from its judge-owned template")
    from .replay_startup import decode_input_aliases

    return PreparedNativeEvents(outputs["native.sv"].decode("utf-8"), collector,
                                plan, request_sha256, execution,
                                decode_fields(image["boundary_fields"]),
                                tuple(image["boundary_paths"]), decode_input_aliases(image['startup_aliases']))


def prepare_source_observer(source: str, *, top: str, execution_sha256: str,
                            callback_limit: int | None,
                            timeout_s: int = defaults.SIM_RTL_NATIVE_PREPARATION_SECONDS,
                            catalogue: CatalogueManifest | None = None,
                            scope_root: str = "TOP",
                            allowed_triggers: frozenset[tuple[str, str]] | None = None,
                            source_rules=EMPTY_SOURCE_RULES,
                            ) -> PreparedNativeEvents | RunFailure:
    """Parse and bind inside one supervised preparation job.

    Source outcomes bind to typed instance contexts directly. Synthesized
    action catalogues additionally need the compiler-origin witness.

    Original artifact identity remains the caller's source/top identity. The
    returned tool execution additionally authenticates the observer build inputs.
    No source/model/task is rewritten in place, and no host EDA fallback exists.
    """
    from .tool_isolation import default_limits, run_workspace
    from .tool_protocol import ToolCommand, ToolGroup, ToolIsolationError, command_result, require_completed
    from .tool_runtime import freeze_runtime

    if type(source) is not str or type(timeout_s) is not int or timeout_s < 1:
        raise ValueError("native preparation needs source text and a positive timeout")
    if catalogue is not None and type(catalogue) is not CatalogueManifest:
        raise TypeError("native preparation needs an exact frontend catalogue")
    source_goals = (() if catalogue is None else
                    tuple(is_source_goal(goal) for goal in catalogue.values()))
    catalogue_kind = ("source" if catalogue is not None and all(source_goals) else
                      "mixed" if any(source_goals) else "actions")
    profile = "source-observation" if catalogue_kind == "source" else "source-events"
    source_schemas = ([] if catalogue is None else sorted({
        goal.family for goal in catalogue.values() if is_source_goal(goal)}))
    request = _request({"version": VERSION, "source": _sha(source.encode("utf-8")),
                        "top": top, "execution": execution_sha256,
                        "catalogue": None if catalogue is None else catalogue.digest(),
                        "toolchain": "0" * 64, "callback_limit": callback_limit,
                        "scope_root": scope_root, "catalogue_kind": catalogue_kind,
                        "source_schemas": source_schemas,
                        "source_rules": source_rules.as_dict(),
                        "allowed_triggers": (None if allowed_triggers is None else
                                             [list(pair) for pair in sorted(allowed_triggers)])})
    try:
        runtime = freeze_runtime(profile)
        request["toolchain"] = runtime.identity
    except (OSError, RuntimeError, ValueError) as exc:
        return RunFailure.infrastructure("rtl-native-preparation", Refusal(
            "native-events-runtime-unavailable", "the source observer runtime cannot be frozen",
            detail=str(exc)))
    payload = canonical_json(request).encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="behave-native-prepare-", dir=scratch_root()) as temporary:
        root = Path(temporary)
        (root / SOURCE_FILENAME).write_text(source, encoding="utf-8")
        (root / "request.json").write_bytes(payload)
        try:
            execution = require_completed(run_workspace(
                root, (ToolGroup((ToolCommand(
                    "prepare-native-events", ("/runtime/python/bin/python", "-S", "-m",
                                              "behave.sim.rtl._prepare_native_events"),
                    timeout_s=timeout_s),)),),
                outputs=_OUTPUTS, role="rtl-native-preparation", runtime_profile=profile,
                frozen_runtime=runtime,
                limits=default_limits(wall_seconds=timeout_s + defaults.SIM_EDA_ENVELOPE_OVERHEAD_SECONDS)))
        except ToolIsolationError as exc:
            return RunFailure.infrastructure("rtl-native-preparation", Refusal(
                exc.code, exc.why, tool_execution=exc.execution.as_dict()))
    row = command_result(execution, "prepare-native-events")
    if row["timed_out"] or row["returncode"] != 0:
        return RunFailure.verifier("rtl-native-preparation", Refusal(
            "native-events-preparation-incomplete", "source observation preparation did not complete",
            tool_execution=execution.as_dict()))
    try:
        return _decode(execution, _sha(payload), callback_limit, catalogue, scope_root=scope_root)
    except Refused as exc:
        return RunFailure.verifier("rtl-native-preparation", exc.refusal)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        return RunFailure.verifier("rtl-native-preparation", Refusal(
            "native-events-preparation-invalid", "source observation has no authenticated build inputs",
            detail=str(exc), tool_execution=execution.as_dict()))
