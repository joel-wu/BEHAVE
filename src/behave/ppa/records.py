"""Chunked PPA documents over the existing verified content store.

Pools and logs are independent immutable documents. Measurement histories hold
their references, never copies of every prior test suite. Chunking handles a
single large replay/evidence document without an aggregate size quota.
All writers return the complete dependency closure for the owning journal.
"""

from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, read_verified
from behave.core.file_input import read_regular_file
from behave.core.records import canonical_json, fields, parse_json, thaw_json, version

DOCUMENT = "behave.ppa-document/1"


def release_reference(path):
    """Accept the shared publisher's release.json or a legacy manifest file."""
    payload = read_regular_file(path)
    value = parse_json(payload)
    return (BlobRef.from_dict(value) if set(value) == {"sha256", "size_bytes"}
            else BlobRef.of(payload))


def finalized_report(directory):
    """Read a verified finished workflow without importing its executor."""
    from behave.agent.run_store import JournalDirectory

    if not (Path(directory) / "workflow").is_dir():
        raise ValueError("PPA search has not durably completed")
    run = JournalDirectory(directory)
    events = run.journal("workflow").load()
    if not events or events[-1].kind != "finished":
        raise ValueError("PPA search has not durably completed")
    ref = BlobRef.from_dict(thaw_json(events[-1].payload["result"]))
    return run, ref, read_document(run.objects, ref)


def search_cost(result):
    """Summarize recorded service calls and billing without settling an account."""
    history = [item for row in result["candidates"] for item in row["history"]]
    return {"reason": result["reason"], "billing": result["billing"], "turns": result["turns"],
            "search_service_calls": result["measurement_requests"],
            "final_service_calls": sum(item["final"] for item in history),
            "search_service_seconds": sum(item["elapsed_seconds"] for item in history if not item["final"]),
            "final_service_seconds": sum(item["elapsed_seconds"] for item in history if item["final"])}


def comparison_cost(objects, result, manifest):
    """Use an already verified final and manifest; verify each referenced search.

    Callers own archive verification. This neither reopens its workflow nor
    calculates a frontier, and keeps every recorded search label and charge.
    """
    if result["schema"] != "behave.ppa-comparison/2":
        raise ValueError("unsupported completed PPA report")
    return {"final_service_calls": result["final_service_calls"],
            "searches": {label: {"result": ref, **search_cost(read_document(
                objects, BlobRef.from_dict(ref)))} for label, ref in manifest["searches"].items()}}


def union_pool(*pools):
    """Merge recorded stimuli by Sim identity, retaining the last original row."""
    from behave.sim import api

    rows = {api.Stimulus.from_dict(row).fingerprint: row for pool in pools for row in pool}
    return tuple(rows[key] for key in sorted(rows))


def write_document(objects, value):
    data = canonical_json({"value": value})
    size = defaults.PPA_DOCUMENT_CHUNK_BYTES
    chunks = tuple(objects.put(data[start:start + size]) for start in range(0, len(data), size))
    root = objects.put(canonical_json({"schema": DOCUMENT, "content": BlobRef.of(data).as_dict(),
                                      "chunks": [ref.as_dict() for ref in chunks]}))
    return root, tuple(dict.fromkeys((root, *chunks)))


def read_document(objects, reference):
    row = fields(parse_json(read_verified(objects, reference)), {"schema", "content", "chunks"})
    version(row["schema"], DOCUMENT)
    expected = BlobRef.from_dict(row["content"])
    chunks = tuple(BlobRef.from_dict(ref) for ref in row["chunks"])
    if sum(ref.size_bytes for ref in chunks) != expected.size_bytes:
        raise ValueError("PPA document size mismatch")
    data = b"".join(read_verified(objects, ref) for ref in chunks)
    if BlobRef.of(data) != expected:
        raise ValueError("PPA document content mismatch")
    return fields(parse_json(data), {"value"})["value"]


def pack_result(objects, result):
    """Detach heavy values; preserve the typed outcome and its evidence."""
    packed = dict(result)
    refs = [BlobRef.from_dict(ref) for ref in result.get("evidence", ())]
    if "pool" in packed:
        pool, closure = write_document(objects, packed.pop("pool"))
        packed["pool_ref"] = pool.as_dict()
        refs.extend(closure)
    if "eda" in packed:
        eda = packed["eda"] = dict(packed["eda"])
        if "log" in eda:
            log = eda.pop("log")
            ref, closure = write_document(objects, log)
            eda.update(log_ref=ref.as_dict(), log_chars=len(log))
            refs.extend(closure)
        if eda.get("evidence"):
            refs.append(BlobRef.from_dict(eda["evidence"]))
        refs.extend(BlobRef.from_dict(ref) for ref in eda.get("closure", ()))
    return packed, tuple(dict.fromkeys(refs))


def unpack_result(objects, packed):
    result = dict(packed)
    if "pool_ref" in result:
        result["pool"] = read_document(objects, BlobRef.from_dict(result.pop("pool_ref")))
    if "eda" in result:
        eda = result["eda"] = dict(result["eda"])
        if "log_ref" in eda:
            eda["log"] = read_document(objects, BlobRef.from_dict(eda.pop("log_ref")))
            eda.pop("log_chars", None)
    return result


def read_log(objects, eda):
    if "log_ref" in eda:
        return read_document(objects, BlobRef.from_dict(eda["log_ref"]))
    return eda.get("log", "")
