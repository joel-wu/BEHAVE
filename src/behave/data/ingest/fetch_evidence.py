"""Offline verification of a settled acquisition and its selected-byte closure."""

import math
from dataclasses import dataclass, replace

from behave.agent.journal import load_chain
from behave.core.artifacts import BlobRef, ContentReader, FileRef, SourceArtifact, read_verified
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, fields, parse_json, thaw_json, version

from ..drafts import SourceRecord
from .fetch_http import REDIRECTS, public_address
from .fetch_records import FetchPlan, FetchPolicy, public_url


def require(condition: bool):
    if not condition:
        raise RecordError("fetch-evidence-invalid", "acquisition evidence does not match its fixed plan")


@dataclass(frozen=True, slots=True)
class FetchResult:
    status: str
    code: str
    source: BlobRef | None
    head: BlobRef
    cleanup: CleanupState

    def as_dict(self):
        return {"status": self.status, "code": self.code, "source": None if self.source is None else self.source.as_dict(),
                "head": self.head.as_dict(), "cleanup": self.cleanup.value, "publication_approved": False}


def trace_result(reader: ContentReader, head: BlobRef | None, plan: FetchPlan, policy: FetchPolicy
                 ) -> tuple[str | None, SourceRecord | None]:
    """A well-formed prefix is allowed after termination, but is never a source."""
    events = load_chain(reader, head)
    index, total, pending, terminal = 0, 0, False, None
    files, seen = [], set()
    url, last = plan.files[0].url, None
    for event in events:
        require(terminal is None)
        row = thaw_json(event.payload)
        if event.kind == "hop_requested":
            require(not pending and index < len(plan.files) and not event.artifacts)
            require(last in {None, "redirect", "downloaded"})
            require(row == {"file": index, "url": url})
            public_url(row["url"])
            pending = True
        elif event.kind == "hop_received":
            require(pending)
            fields(row, {"code", "peer", "status", "headers", "location_digest", "redirect", "content"})
            code, peer, status = row["code"], row["peer"], row["status"]
            require(type(code) is str and (code in {"redirect", "downloaded"} or code.startswith("fetch-")))
            require(peer is None or type(peer) is str and public_address(peer))
            require(status is None or type(status) is int and 100 <= status <= 599)
            headers = row["headers"]
            require(type(headers) is dict and set(headers) <= {
                "content-type", "content-length", "content-encoding", "etag", "last-modified"})
            require(all(type(value) is str for value in headers.values()))
            if row["location_digest"] is not None:
                BlobRef.from_dict(row["location_digest"])
            redirect = row["redirect"]
            if redirect is not None:
                public_url(redirect)
            content = None if row["content"] is None else BlobRef.from_dict(row["content"])
            require(event.artifacts == (() if content is None else (content,)))
            require((content is not None) == (code in {"downloaded", "fetch-content-mismatch"}))
            if code == "fetch-host-forbidden":
                require(not policy.permits(url) and peer is None and status is None)
            else:
                require(policy.permits(url))
            if code == "redirect":
                require(peer is not None and status in REDIRECTS and redirect is not None
                        and row["location_digest"] is not None and len(seen) < policy.max_redirects)
                require(redirect not in {*seen, url})
                seen.add(url)
                url = redirect
            if content is not None:
                require(peer is not None and status == 200 and redirect is None)
                require(content.size_bytes <= policy.file_bytes)
                total += content.size_bytes
                require(total <= policy.total_bytes)
                read_verified(reader, content)
                expected = plan.files[index].expected
                require((expected is None or expected == content) == (code == "downloaded"))
                if code == "downloaded":
                    files.append(FileRef(plan.files[index].path, content))
                    index += 1
                    seen = set()
                    if index < len(plan.files):
                        url = plan.files[index].url
            pending, last = False, code
        elif event.kind == "fetch_finished":
            require(not pending and not event.artifacts)
            fields(row, {"code"})
            terminal = "complete" if index == len(plan.files) else last
            require(terminal is not None and terminal not in {"redirect", "downloaded"} and row["code"] == terminal)
        else:
            require(False)
    source = None if terminal != "complete" else SourceRecord(plan.source_id, plan.origin, plan.revision, plan.license,
        SourceArtifact(tuple(files), plan.entry_file, plan.entry_symbol))
    return terminal, source


def _record(reader: ContentReader, ref: BlobRef) -> dict:
    return parse_json(read_verified(reader, ref))


def receipt_result(reader: ContentReader, reference: BlobRef
                   ) -> tuple[FetchResult, SourceRecord | None, tuple[BlobRef, ...]]:
    """An acyclic provenance capsule: no reference back to the produced source."""
    from behave.isolation.service import service_execution_status

    row = fields(_record(reader, reference), {"schema", "plan", "policy", "trace", "service"})
    version(row["schema"], "behave.source-fetch-receipt/1")
    plan_ref, policy_ref = BlobRef.from_dict(row["plan"]), BlobRef.from_dict(row["policy"])
    plan = FetchPlan.from_bytes(read_verified(reader, plan_ref))
    policy = FetchPolicy.from_bytes(read_verified(reader, policy_ref))
    policy.check(plan)
    trace = None if row["trace"] is None else BlobRef.from_dict(row["trace"])
    service_ref = BlobRef.from_dict(row["service"])
    raw = _record(reader, service_ref)
    legacy = raw.get("schema") == "behave.source-fetch-service/1"
    service = fields(raw, {
        "schema", "plan", "policy", "status", "returncode", "cleanup", "started_at", "stdout", "stderr"}
        | (set() if legacy else {"execution_status"}))
    version(service["schema"], f"behave.source-fetch-service/{1 if legacy else 2}")
    if not legacy:
        service_execution_status(service["status"], service["execution_status"])
    require(service["plan"] == plan_ref.as_dict() and service["policy"] == policy_ref.as_dict())
    require(type(service["started_at"]) in (float, int) and math.isfinite(service["started_at"]))
    require(service["status"] in {"exited", "timeout", "cancelled", "output-limit", "launch-failed",
                                   "cleanup-incomplete", "supervision-failed"})
    require(service["returncode"] is None or type(service["returncode"]) is int)
    refs = [reference, plan_ref, policy_ref, service_ref]
    for name in ("stdout", "stderr"):
        ref = BlobRef.from_dict(service[name])
        read_verified(reader, ref)
        refs.append(ref)
    cleanup = CleanupState(service["cleanup"])
    terminal, source = trace_result(reader, trace, plan, policy)
    if not cleanup.quiescent:
        status, code = "unavailable", "fetch-cleanup-unconfirmed"
    elif service["status"] != "exited" or service["returncode"] != 0:
        status, code = "unavailable", "fetch-worker-" + ("failed" if service["status"] == "exited" else service["status"])
    elif terminal is None:
        status, code = "unavailable", "fetch-worker-incomplete"
    elif terminal in {"fetch-transport-failed", "fetch-http-unavailable", "fetch-body-incomplete"}:
        status, code = "unavailable", terminal
    else:
        status, code = ("acquired", "complete") if source is not None else ("rejected", terminal)
    source = replace(source, acquisition=reference) if status == "acquired" else None
    source_ref = BlobRef.of(source.to_bytes()) if status == "acquired" else None
    for event in load_chain(reader, trace):
        refs.extend((BlobRef.of(event.to_bytes()), *event.artifacts))
    return FetchResult(status, code, source_ref, reference, cleanup), source, tuple(dict.fromkeys(refs))


def source_acquisition_closure(reader: ContentReader, source: SourceRecord) -> tuple[BlobRef, ...]:
    if source.acquisition is None:
        return ()
    result, observed, closure = receipt_result(reader, source.acquisition)
    require(result.status == "acquired" and observed == source)
    return closure


def verify_acquisition(reader: ContentReader, head: BlobRef) -> FetchResult:
    events = load_chain(reader, head)
    require(len(events) == 2 and [e.kind for e in events] == ["fetch_started", "fetch_settled"])
    start, settled = (thaw_json(e.payload) for e in events)
    fields(start, {"plan", "policy"})
    fields(settled, {"receipt", "source"})
    reference = BlobRef.from_dict(settled["receipt"])
    result, source, closure = receipt_result(reader, reference)
    receipt = _record(reader, reference)
    require(start == {name: receipt[name] for name in ("plan", "policy")})
    require(events[0].artifacts == tuple(BlobRef.from_dict(start[name]) for name in ("plan", "policy")))
    require(set(closure) <= set(events[1].artifacts))
    source_ref = result.source
    require(settled["source"] == (None if source_ref is None else source_ref.as_dict()))
    if source_ref is not None:
        require(source_ref in events[1].artifacts)
        require(read_verified(reader, source_ref) == source.to_bytes())
    return replace(result, head=head)
