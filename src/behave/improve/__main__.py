"""Export training-side diagnostic facts as a standalone, model-free stage."""

import argparse
from pathlib import Path

from behave.agent.workspace import WorkspaceSnapshot
from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json
from behave.core.storage import DirectoryStore, install_exclusive
from behave.train.plan import TrainingManifest

from .feedback import FeedbackRequest, export_feedback


def _install(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not install_exclusive(path.parent, path.name, data) and read_regular_file(path) != data:
        raise RecordError("feedback-output-mismatch", "use a new output directory for changed feedback")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("feedback",))
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--approved-manifest", required=True, type=Path)
    parser.add_argument("--source-objects", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.dry_run and args.output is None:
        parser.error("--output is required unless --dry-run is selected")
    try:
        request = FeedbackRequest.from_bytes(read_regular_file(args.request))
        approved = read_regular_file(args.approved_manifest)
        TrainingManifest.from_bytes(approved)
        bundle = export_feedback(request, DirectoryReader(args.source_objects), approved_manifest=BlobRef.of(approved))
        snapshot = WorkspaceSnapshot(tuple(file.reference for file in bundle.files))
        identity = {"request": BlobRef.of(request.to_bytes()).as_dict(),
                    "workspace": BlobRef.of(snapshot.to_bytes()).as_dict()}
        if not args.dry_run:
            _install(args.output / "export.json", canonical_json(identity))
            objects = DirectoryStore(args.output / "objects")
            for file in bundle.files:
                objects.put(file.data)
                _install(args.output / "view" / file.reference.path, file.data)
            objects.put(snapshot.to_bytes())
            _install(args.output / "workspace.json", snapshot.to_bytes())
        print(canonical_json({"status": "validated" if args.dry_run else "exported", **identity}).decode())
        return 0
    except (RecordError, ValueError, TypeError, OSError) as error:
        print(canonical_json({"status": "error", "code": getattr(error, "code", "feedback-export-error"),
                              "detail": str(error)}).decode())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
