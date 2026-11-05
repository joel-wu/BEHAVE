"""Closed simulator response envelope shared by both Drivers and replay readers.

Slot/state/hit validation belongs to the bound artifact decoder. This layer only
validates response shape and startup-failure ownership; a readable prefix
cannot conceal extra traces, unknown fields or a simultaneous failure record.
"""

from collections import OrderedDict
from pathlib import Path

from behave.core import runtime_defaults as defaults

from ..core.failure import FailureDisposition, FailureOrigin, RunFailure
from ..core.record_value import write_canonical_json
from ..strict_json import strict_json_loads

_RECORD_PATHS = (
    ("traces", None, "records"),
    ("traces", None, "source_evidence", "intervals"),
    ("traces", None, "resolved_stimulus", "slots"),
)
_RECORD_ENCODING = "rtl-trace-record-references/1"


def _pack_rows(rows):
    """Reference only the exact owned object, never approximate equal samples.

    The source list keeps every object alive. The bounded index therefore needs
    only object ids and positions, and cannot confuse a recycled Python id.
    Eviction affects wire size, not the set/order/content of recorded cycles.
    """
    recent, encoded = OrderedDict(), []
    capacity = defaults.SIM_RTL_TRACE_RECORD_CACHE_ENTRIES
    for index, row in enumerate(rows):
        if type(row) is not dict:
            raise ValueError("a trace record array contains object rows")
        identity = id(row)
        prior = recent.get(identity)
        if prior is not None:
            encoded.append(prior)
            recent.move_to_end(identity)
        else:
            encoded.append(row)
            if capacity:
                recent[identity] = index
                if len(recent) > capacity:
                    recent.popitem(last=False)
    return encoded


def _expand_rows(rows):
    """Restore every position; references are local and strictly backward."""
    for index, row in enumerate(rows):
        if type(row) is int and 0 <= row < index:
            rows[index] = rows[row]
        elif type(row) is not dict:
            raise ValueError("a trace record reference must name an earlier row")
    return rows


def _record_arrays(trace, transform):
    # These are the same schema arrays already used by the streaming writer.
    # Do not recursively interpret integers in payloads, hits or memory data.
    result = dict(trace)
    for path in _RECORD_PATHS:
        current = result
        for name in path[2:-1]:
            if type(current.get(name)) is not dict:
                break
            child = dict(current[name])
            current[name] = child
            current = child
        else:
            name = path[-1]
            if type(current.get(name)) is list:
                current[name] = transform(current[name])
    return result


def _check(traces, startup_failure):
    if type(traces) is not list or any(type(trace) is not dict for trace in traces):
        raise ValueError("a simulator response needs a finite list of trace documents")
    if startup_failure is not None and (
        type(startup_failure) is not RunFailure or traces
        or startup_failure.origin is not FailureOrigin.VERIFIER
        or startup_failure.disposition is not FailureDisposition.NO_RESULT
        or startup_failure.phase != "rtl-analysis-startup"
    ):
        raise ValueError("a startup failure is a verifier no-result before any trace")


def read_trace_document(payload, *, max_traces: int):
    """Read no more traces than the request contained; permit diagnostic prefixes."""
    if type(max_traces) is not int or max_traces < 0:
        raise ValueError("a trace response needs a finite request count")
    document = strict_json_loads(payload, what="the isolated RTL trace response")
    if type(document) is not dict:
        raise ValueError("a simulator response has unknown or missing fields")
    encoded = "record_encoding" in document
    if encoded and document["record_encoding"] != _RECORD_ENCODING:
        raise ValueError("a simulator response has an unknown record encoding")
    fields = set(document) - ({"record_encoding"} if encoded else set())
    if fields not in ({"traces"}, {"traces", "startup_failure"}):
        raise ValueError("a simulator response has unknown or missing fields")
    failure = (RunFailure.from_dict(document["startup_failure"])
               if "startup_failure" in document else None)
    traces = document["traces"]
    _check(traces, failure)
    if len(traces) > max_traces:
        raise ValueError("a simulator response contains more traces than requested")
    if encoded:
        traces = [_record_arrays(trace, _expand_rows) for trace in traces]
    return traces, failure


def write_trace_document(path: Path, traces: list, *, startup_failure=None) -> None:
    """Atomically replace the last complete prefix, never the final file in place."""
    _check(traces, startup_failure)
    document = {"record_encoding": _RECORD_ENCODING,
                "traces": [_record_arrays(trace, _pack_rows) for trace in traces]}
    if startup_failure is not None:
        document["startup_failure"] = startup_failure.as_dict()
    temporary = path.with_suffix(".partial")
    with temporary.open("w", encoding="utf-8") as stream:
        write_canonical_json(document, stream, record_paths=_RECORD_PATHS)
    temporary.replace(path)
