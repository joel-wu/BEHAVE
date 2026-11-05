"""Acquire selected public HTTPS files; no source code is executed or approved."""

import argparse
from pathlib import Path

from behave.agent.attempt import AttemptInterrupted
from behave.agent.run_store import JournalDirectory
from behave.cli.diagnostics import report_error
from behave.core.artifacts import BlobRef
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json, parse_json
from behave.runtime.records import command_scope

from .fetch_records import FetchPlan, FetchPolicy
from .remote import acquire_source


def _read(path: Path) -> bytes:
    raw = read_regular_file(path)
    # Operator JSON may be indented. Persisted identities remain canonical and
    # duplicate keys/non-finite numbers still fail at the shared JSON boundary.
    return canonical_json(parse_json(raw))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--run-directory", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="validate selection and host grants without DNS/network")
    args = parser.parse_args(argv)
    if not args.dry_run and args.run_directory is None:
        parser.error("--run-directory is required unless --dry-run is selected")
    try:
        plan = FetchPlan.from_bytes(_read(args.plan))
        policy = FetchPolicy.from_bytes(_read(args.policy))
        policy.check(plan)
        if args.dry_run:
            if any(not policy.permits(f.url) for f in plan.files):
                raise RecordError("fetch-host-forbidden", "selection exceeds the configured host grants")
            output = {"status": "validated", "plan": BlobRef.of(plan.to_bytes()).as_dict(),
                      "policy": BlobRef.of(policy.to_bytes()).as_dict(), "publication_approved": False}
        else:
            with command_scope() as cancelled:
                output = acquire_source(plan, policy, run=JournalDirectory(args.run_directory),
                                        cancelled=cancelled).as_dict()
    except (RecordError, AttemptInterrupted, TypeError, ValueError, OSError) as error:
        report_error(error)
        output = {"status": "unavailable", "code": getattr(error, "code", "fetch-command-invalid")}
    print(canonical_json(output).decode())
    return 0 if output["status"] in {"validated", "acquired"} else 1
