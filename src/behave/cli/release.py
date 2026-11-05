"""Operator-only release preflight/publication. Never synthesize human approval.

--dry-run audits every explicit package without creating a destination. A commit
includes pending dispositions too; 'committed' does not mean every task passed.
"""

import argparse
from pathlib import Path

from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.cli.diagnostics import report_error
from behave.core.artifacts import BlobRef, DirectoryReader, read_verified
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json
from behave.data.publication import audit_release, publish_release
from behave.data.published import ReleaseManifest
from behave.data.release_records import ReleaseRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True, help="canonical ReleaseRequest JSON")
    parser.add_argument("--objects", type=Path, required=True, help="trusted staging content store")
    parser.add_argument("--destination", type=Path, help="new or matching existing publication journal")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.dry_run and args.destination is None:
        parser.error("--destination is required unless --dry-run is selected")
    try:
        payload = read_regular_file(args.request)
        request = ReleaseRequest.from_bytes(payload)
        reader = DirectoryReader(args.objects)
        if args.dry_run:
            admissions = audit_release(request, reader)
            output = {
                "status": "audited",
                "request": BlobRef.of(payload).as_dict(),
                "dispositions": [parse_json(a.disposition.to_bytes()) for a in admissions],
            }
        else:
            destination = JournalDirectory(args.destination)
            result = publish_release(
                release_id=request.release_id,
                policy=request.policy,
                packages=request.packages,
                approvals=request.approvals,
                reader=reader,
                destination=destination,
            )
            head, _ = destination.seal("publication")
            if head != result.head:
                raise RecordError("publication-head-mismatch", "release changed before export")
            manifest = ReleaseManifest.from_bytes(read_verified(destination.objects, result.release))
            output = {
                "status": "committed",
                "release": result.release.as_dict(),
                "head": result.head.as_dict(),
                "summary": thaw_json(manifest.metadata),
            }
    except (RecordError, AttemptInterrupted, ValueError, TypeError, OSError) as error:
        report_error(error)
        print(
            canonical_json(
                {"status": "unavailable", "code": getattr(error, "code", "release-command-invalid")}
            ).decode()
        )
        return 1
    print(canonical_json(output).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
