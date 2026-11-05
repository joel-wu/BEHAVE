"""Small, strict JSON primitives for persisted application records.

This module has no application policy or optional runtime dependencies.
"""

from __future__ import annotations

import json
import math
from types import MappingProxyType


class RecordError(ValueError):
    """A malformed record, with a stable machine-readable reason."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    def __reduce__(self):
        # Trusted native workers (e.g. Ray) must retain the typed reason when
        # transporting an exception. Application records still use strict JSON.
        return type(self), (self.code, str(self))


def text(value: object, name: str) -> str:
    if type(value) is not str or not value or any(ord(c) < 32 for c in value):
        raise RecordError("record-text-invalid", f"{name} must be nonempty text without control characters")
    return value


def fields(value: object, expected: set[str]) -> dict:
    if type(value) is not dict:
        raise RecordError("record-fields-invalid", f"expected exactly the fields {sorted(expected)}")
    if set(value) != expected:
        raise RecordError("record-fields-invalid",
                          f"expected exactly the fields {sorted(expected)}. "
                          f"Missing: {sorted(expected - value.keys())}. "
                          f"Unexpected: {sorted(value.keys() - expected)}.")
    return value


def array(value: object) -> list:
    if type(value) is not list:
        raise RecordError("record-array-invalid", "expected a JSON array")
    return value


def version(value: object, expected: str) -> None:
    if value != expected:
        raise RecordError("record-version-unsupported", f"expected schema {expected!r}")


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecordError("record-key-duplicate", f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise RecordError("record-number-invalid", f"non-finite JSON number {value}")


def parse_json(payload: bytes) -> dict:
    if type(payload) is not bytes:
        raise RecordError("record-bytes-required", "a persisted record must be bytes")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        if type(value) is not dict:
            raise RecordError("record-object-required", "expected a JSON object")
        # Validate overflowing exponents and lone surrogates without re-encoding.
        _freeze_json(value, set(), copy=False)
        return value
    except RecordError:
        raise
    except (ValueError, RecursionError) as error:
        raise RecordError("record-json-invalid", "invalid UTF-8 JSON record") from error


def freeze_json(value: object) -> object:
    """Copy a closed JSON value, retaining number types and making it immutable.

    No application nesting limit is imposed; Python's recursion limit still
    applies. Cycles are invalid JSON, but shared acyclic values are allowed.
    """
    try:
        return _freeze_json(value, set())
    except RecursionError as error:
        raise RecordError("record-json-invalid", "JSON exceeds Python's recursion capacity") from error


def _freeze_json(value: object, ancestors: set[int], *, copy: bool = True) -> object:
    kind = type(value)
    if value is None or kind in (bool, int):
        return value
    if kind is str:
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise RecordError("record-text-invalid", "text must be valid UTF-8") from error
        return value
    if kind is float and math.isfinite(value):
        return value
    if kind in (list, tuple, dict, MappingProxyType):
        identity = id(value)
        if identity in ancestors:
            raise RecordError("record-value-invalid", "cyclic containers are not JSON values")
        ancestors.add(identity)
        try:
            if not copy:
                if kind in (list, tuple):
                    for item in value:
                        _freeze_json(item, ancestors, copy=False)
                else:
                    for key, item in value.items():
                        if type(key) is not str:
                            raise RecordError("record-key-invalid", "JSON keys must be strings")
                        _freeze_json(key, ancestors, copy=False)
                        _freeze_json(item, ancestors, copy=False)
                return value
            if kind in (list, tuple):
                return tuple(_freeze_json(item, ancestors) for item in value)
            if any(type(key) is not str for key in value):
                raise RecordError("record-key-invalid", "JSON keys must be strings")
            return MappingProxyType({
                _freeze_json(key, ancestors): _freeze_json(item, ancestors)
                for key, item in value.items()
            })
        finally:
            ancestors.remove(identity)
    raise RecordError("record-value-invalid", f"not a finite JSON value: {kind.__name__}")


def thaw_json(value: object) -> object:
    if type(value) is MappingProxyType:
        return {key: thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [thaw_json(item) for item in value]
    return value


def canonical_json(value: object) -> bytes:
    try:
        # Encoding needs validation, not a recursively frozen copy followed by
        # another mutable copy. Large replay evidence must settle in the same
        # deadline without tripling its object graph. freeze_json still owns
        # detachment when a caller actually needs an immutable record.
        _freeze_json(value, set(), copy=False)
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False, default=_json_proxy,
        ).encode("utf-8")
    except RecordError:
        raise
    except (ValueError, RecursionError) as error:
        raise RecordError("record-json-invalid", "cannot encode JSON record") from error


def _json_proxy(value):
    if type(value) is MappingProxyType:
        return dict(value)
    raise RecordError("record-value-invalid", f"not a finite JSON value: {type(value).__name__}")


def metadata(value: object) -> MappingProxyType:
    frozen = freeze_json(value)
    if type(frozen) is not MappingProxyType:
        raise RecordError("record-object-required", "metadata must be a JSON object")
    return frozen
