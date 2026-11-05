"""Fixed trusted fetch service. It only downloads selected bytes; never executes them."""

import sys
import time
from dataclasses import replace
from urllib.parse import urljoin

from behave.agent.journal import AttemptJournal
from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import RecordError, canonical_json
from behave.core.storage import DirectoryStore

from . import fetch_http
from .fetch_records import FetchPlan, FetchPolicy, public_url


def execute(plan: FetchPlan, policy: FetchPolicy, journal: AttemptJournal) -> None:
    policy.check(plan)
    events = journal.load()
    if events:
        raise RecordError("fetch-worker-not-fresh", "a fetch service cannot resume an old dispatch")
    deadline = time.monotonic() + policy.timeout_seconds
    total = 0

    def append(kind, payload, artifacts=()):
        nonlocal events
        events = (*events, journal.append(events, kind, payload, artifacts))

    for index, selected in enumerate(plan.files):
        url, seen = selected.url, set()
        while True:
            append("hop_requested", {"file": index, "url": url})
            if not policy.permits(url):
                hop = fetch_http.Hop("fetch-host-forbidden")
            else:
                hop = fetch_http.get(url, maximum=min(policy.file_bytes, policy.total_bytes - total), deadline=deadline)
            redirect = content = None
            if hop.code == "redirect":
                try:
                    if hop.location is None:
                        raise ValueError("missing Location")
                    redirect = public_url(urljoin(url, hop.location))
                except (TypeError, ValueError):
                    hop = replace(hop, code="fetch-redirect-invalid")
                else:
                    if redirect in {*seen, url}:
                        hop = replace(hop, code="fetch-redirect-loop")
                    elif len(seen) >= policy.max_redirects:
                        hop = replace(hop, code="fetch-redirect-limit")
            if hop.code == "downloaded":
                content = journal.objects.put(hop.body)
                total += content.size_bytes
                if selected.expected is not None and selected.expected != content:
                    hop = replace(hop, code="fetch-content-mismatch")
            append("hop_received", fetch_http.observation(hop, redirect, content),
                   () if content is None else (content,))
            if hop.code == "redirect":
                seen.add(url)
                url = redirect
                continue
            if hop.code != "downloaded":
                append("fetch_finished", {"code": hop.code})
                return
            break
    append("fetch_finished", {"code": "complete"})


def main():
    root, plan_digest, plan_size, policy_digest, policy_size = sys.argv[1:]
    store = DirectoryStore(root + "/objects")
    try:
        plan = FetchPlan.from_bytes(read_verified(store, BlobRef(plan_digest, int(plan_size))))
        policy = FetchPolicy.from_bytes(read_verified(store, BlobRef(policy_digest, int(policy_size))))
        execute(plan, policy, AttemptJournal(root + "/trace", store))
    except Exception as error:  # noqa: BLE001 -- never expose a network exception/URL/credential in logs
        sys.stdout.buffer.write(canonical_json({"error_type": type(error).__name__}))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
