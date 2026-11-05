"""Offline verification of reports and score prefixes; no execution dispatch.

This is a trusted operator command, not an agent-visible tool. The record file
contains a JSON BlobRef, not a live journal path. Raw transcripts are not printed.
"""

import argparse
import sys
from pathlib import Path

from behave.core.artifacts import BlobRef, DirectoryReader, read_verified
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json, parse_json, thaw_json


def inspect_record(kind, reader, reference, *, approved_manifest=None, verifier=None):
    extra = {}
    if kind == "eval":
        from behave.task_execution.batch import load_batch_report

        return load_batch_report(reader, reference).as_dict()
    if kind == "train":
        from behave.train.learning_report import load_training_report

        if approved_manifest is None:
            raise RecordError("training-manifest-unapproved", "training inspection requires the approved manifest")
        return load_training_report(reader, reference, approved_manifest=approved_manifest, verifier=verifier)
    if kind == "release":
        from behave.data.publication import load_publication
        from behave.data.published import ReleaseManifest

        publication = load_publication(reader, reference)
        return {"release": publication.release.as_dict(), "head": publication.head.as_dict(),
                "manifest": parse_json(ReleaseManifest.from_bytes(read_verified(reader, publication.release)).to_bytes())}
    if kind == "score":
        from behave.task_execution.score_progress import load_score_progress

        return load_score_progress(reader, reference)
    if kind == "acquisition":
        from behave.data.ingest import verify_acquisition

        return verify_acquisition(reader, reference).as_dict()
    if kind == "selection":
        from behave.data.selection import selection_summary

        return selection_summary(reader, reference)
    if kind == "discovery":
        from behave.data.discovery import load_discovery

        return load_discovery(reader, reference)
    if kind == "corpus":
        from behave.data.corpus import summary

        return summary(reader, reference)
    if kind == "scope":
        from behave.data.scope import load_scope_preparation

        result, billing = load_scope_preparation(reader, reference)
        result = parse_json(result.to_bytes())
    elif kind == "synthesis":
        from behave.data.process_evidence import load_synthesis

        result, billing = load_synthesis(reader, reference)
        result = parse_json(result.to_bytes())
        if result.get("rtl_head") is not None:
            from behave.data.rtl import rtl_summary

            extra["auxiliary_rtl"] = rtl_summary(reader, BlobRef.from_dict(result["rtl_head"]))
    else:
        raise ValueError("unsupported inspection kind")
    return {"result": result, **extra, "billing": {"known_estimates_by_currency": thaw_json(billing.cost_by_currency()),
        "unpriced_dispatches": billing.unpriced_dispatches, "unresolved_dispatches": billing.unresolved_dispatches}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=("eval", "train", "scope", "synthesis", "release", "score",
                                                        "acquisition", "discovery", "selection", "corpus"))
    parser.add_argument("--objects", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path,
                        help="JSON file containing the report or score-prefix BlobRef")
    parser.add_argument("--approved-manifest", type=Path, help="required train-only manifest for training reports")
    parser.add_argument("--native-endpoint", type=Path, action="append",
                        help="approved native-token endpoint for training inspection; repeat for history")
    args = parser.parse_args(argv)
    if args.kind == "train" and args.approved_manifest is None:
        parser.error("--approved-manifest is required for training inspection")
    if args.kind != "train" and (args.approved_manifest is not None or args.native_endpoint is not None):
        parser.error("--approved-manifest and --native-endpoint only apply to training inspection")
    try:
        packet = read_regular_file(args.record)
        reference = BlobRef.from_dict(parse_json(packet))
        approved = None
        if args.approved_manifest is not None:
            from behave.train.plan import TrainingManifest

            content = read_regular_file(args.approved_manifest)
            TrainingManifest.from_bytes(content)
            approved = BlobRef.of(content)
        verifier = None
        if args.native_endpoint is not None:
            from behave.train.training_data import NativeChatVerifier

            verifier = NativeChatVerifier.from_files(args.native_endpoint)
        result = inspect_record(args.kind, DirectoryReader(args.objects), reference, approved_manifest=approved,
                                verifier=verifier)
    except (RecordError, ValueError, TypeError, OSError) as error:
        # Operator diagnostics go to stderr; stdout remains the existing JSON
        # interface. Do not dump the input record or a conversation transcript.
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        print(canonical_json({"status": "unavailable", "code": getattr(error, "code", "inspection-invalid")}).decode())
        return 1
    print(canonical_json(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
