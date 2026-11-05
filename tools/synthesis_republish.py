"""Republish completed Syn packages under an explicit runtime admission policy.

Uses the existing publisher; never calls models, regenerates tasks or rescoring.
The source publication stays unchanged. Explicitly deferred task IDs and reasons
are recorded alongside the new request, rather than silently removed.
"""

import argparse
from dataclasses import replace
from pathlib import Path

from behave.agent.run_store import JournalDirectory
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json, parse_json, thaw_json
from behave.core.storage import install_exclusive
from behave.data.publication import publish_release
from behave.data.published import ReleaseManifest
from behave.data.release_records import ReleaseDisposition, ReleasePolicy, ReleaseRequest


def prepare(source, runtime, release_id, deferred):
    previous = JournalDirectory(source)
    events = previous.journal("publication").load(verify_artifacts=False)
    if not events or events[-1].kind != "publication_finished":
        raise ValueError("source publication has not finished")
    old_release = BlobRef.from_dict(thaw_json(events[-1].payload)["release"])
    manifest = ReleaseManifest.from_bytes(previous.objects.read(old_release))
    policy = ReleasePolicy.from_bytes(previous.objects.read(manifest.policy))
    policy = replace(policy, require_license_metadata=runtime["require_license_metadata"])
    rows = [ReleaseDisposition.from_bytes(previous.objects.read(BlobRef.from_dict(ref)))
            for ref in thaw_json(manifest.metadata)["dispositions"]]
    unknown = set(deferred) - {row.task_id for row in rows}
    if unknown:
        raise ValueError(f"deferred tasks are not in the source publication: {sorted(unknown)}")
    request = ReleaseRequest(release_id, BlobRef.of(policy.to_bytes()),
                             tuple(row.package for row in rows if row.task_id not in deferred))
    selection = {"source": str(Path(source).resolve()), "source_release": old_release.as_dict(),
                 "deferred": [{"task_id": row.task_id, "package": row.package.as_dict(),
                               "reason": deferred[row.task_id]} for row in rows if row.task_id in deferred]}
    return request, policy, selection, previous.objects


def republish(source, runtime, release_id, destination, *, deferred=None):
    request, policy, selection, original = prepare(source, runtime, release_id, deferred or {})
    destination = JournalDirectory(destination)
    policy_bytes = policy.to_bytes()
    for name, data in (("request.json", request.to_bytes()), ("policy.json", policy_bytes),
                       ("selection.json", canonical_json(selection))):
        if (not install_exclusive(destination.directory, name, data)
                and (destination.directory / name).read_bytes() != data):
            raise ValueError(f"new selection requires a new destination: {name}")

    class Reader:
        def read(self, ref):
            return policy_bytes if ref == request.policy else original.read(ref)

    def publish_inputs(target, references):
        # The old publication already owns and preserves these immutable files.
        # Only the selected policy is new; do not copy all transcripts again.
        target.put(policy_bytes)
        target.publish_owned(original, tuple(ref for ref in references if ref != request.policy),
                             workers=runtime.get("publication_workers", defaults.PUBLICATION_WORKERS))

    result = publish_release(release_id=request.release_id, policy=request.policy,
                             packages=request.packages, reader=Reader(), destination=destination,
                             publish_inputs=publish_inputs, verify_history=False)
    head, _ = destination.seal("publication")
    manifest = ReleaseManifest.from_bytes(destination.objects.read(result.release))
    return {"status": "committed", "release": result.release.as_dict(), "head": head.as_dict(),
            "summary": thaw_json(manifest.metadata), "deferred": selection["deferred"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="completed publication journal")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--defer-task", nargs=2, action="append", default=[], metavar=("TASK_ID", "REASON"))
    args = parser.parse_args()
    result = republish(args.source, parse_json(args.runtime.read_bytes()), args.release_id,
                       args.destination, deferred=dict(args.defer_task))
    print(canonical_json(result).decode())


if __name__ == "__main__":
    main()
