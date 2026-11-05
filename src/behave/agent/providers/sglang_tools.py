"""Opt-in fix for the pinned Qwen XML parser's compound JSON-schema types.

The upstream parser treats an anyOf object/null parameter as text, even when
the generated XML contains a valid JSON object. Keep its XML framing and
ordinary scalar handling; decode compound-type values without changing the
advertised schema or repairing invalid JSON. Tool validation remains final.
No SGLang import or registration occurs unless the native launcher opts in.
"""

import os
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.records import fields, parse_json

PLUGIN = "behave_qwen_tools"
PARSER = "behave-qwen3-coder"
UPSTREAM_SHA256 = "642294f642f563fadd52bff2ee3a204e30fe26f33c7e5306d47e4609ef15f129"
_KINDS = frozenset(("object", "array", "string", "integer", "number", "boolean", "null"))
_detector = None


def _kinds(schema):
    pending, result = [schema], set()
    while pending:
        branch = pending.pop()
        if type(branch) is not dict:
            return None
        kind = branch.get("type")
        if type(kind) is str:
            if kind not in _KINDS:
                return None
            result.add(kind)
        elif type(kind) is list:
            if not kind or any(type(k) is not str or k not in _KINDS for k in kind):
                return None
            result.update(kind)
        else:
            alternatives = branch.get("anyOf", branch.get("oneOf"))
            if type(alternatives) is not list or not alternatives:
                return None
            pending.extend(alternatives)
    return result


def compound_value(raw: str, schema) -> tuple[bool, object]:
    """Return whether compound-type handling applies, and the unrepaired value.

    XML string values are unquoted text. If a union admits both text and a
    non-null JSON value, preserve text instead of guessing which was intended.
    Object constraints, required fields and union exclusivity belong to the
    existing tool validator, not this transport decoder.
    """
    if type(schema) is not dict or type(schema.get("type")) is str:
        return False, raw
    kinds = _kinds(schema)
    if kinds is None:
        return False, raw
    if raw == "null" and "null" in kinds:
        return True, None
    if "string" in kinds:
        return True, raw
    try:
        # Reuse strict duplicate-key, finite-number and depth checks for any
        # JSON value, while rejecting attempts to escape the wrapper object.
        value = fields(parse_json(b'{"value":' + raw.encode("utf-8") + b'}'), {"value"})["value"]
    except ValueError:
        return True, raw
    kind = {dict: "object", list: "array", bool: "boolean", int: "integer",
            float: "number", str: "string", type(None): "null"}[type(value)]
    valid = kind in kinds or (kind == "integer" and "number" in kinds)
    return True, value if valid else raw


def register_qwen_tools():
    """Register a new parser name only in an explicitly selected native process."""
    if PLUGIN not in os.environ.get("SGLANG_PLUGINS", "").split(","):
        return
    from sglang.srt.function_call import qwen3_coder_detector as upstream
    from sglang.srt.function_call.function_call_parser import FunctionCallParser

    if BlobRef.of(Path(upstream.__file__).read_bytes()).sha256 != UPSTREAM_SHA256:
        raise ValueError("Qwen tool parser source differs from the reviewed native revision")
    global _detector
    registry = FunctionCallParser.ToolCallParserEnum
    if PARSER in registry:
        if registry[PARSER] is not _detector:
            raise ValueError("BEHAVE Qwen tool parser name is already registered")
        return

    class CompoundQwenDetector(upstream.Qwen3CoderDetector):
        def _convert_param_value(self, param_value, param_name, param_config, func_name):
            handled, value = compound_value(param_value, param_config.get(param_name))
            return value if handled else super()._convert_param_value(
                param_value, param_name, param_config, func_name)

    _detector = CompoundQwenDetector
    registry[PARSER] = _detector
