"""One decoder for JSON documents produced beyond the verifier boundary.

Python's default decoder accepts duplicate object members by keeping the last one and
accepts NaN/Infinity as extensions.  Neither is a value in BEHAVE's wire formats: a
record must have one value for each field, and every numeric value must be finite.
Boundary readers use this decoder before applying their schema-specific checks; it is
not itself a schema or an attestation of the producer.
"""

from __future__ import annotations

import json
import math

__all__ = ["strict_json_loads"]


def strict_json_loads(payload: bytes | str, *, what: str = "JSON document"):
    """Decode one finite JSON value with no duplicate object members."""

    if type(payload) is bytes:
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError(f"{what} is not UTF-8") from error
    elif type(payload) is str:
        text = payload
    else:
        raise TypeError(f"{what} must be bytes or text")

    def object_from_pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"{what} contains duplicate member {key!r}")
            out[key] = value
        return out

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"{what} contains a non-finite number")
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=object_from_pairs,
            parse_float=finite_float,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"{what} contains non-standard constant {value}")),
        )
    except (OverflowError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError(f"{what} is not one finite JSON value") from error
