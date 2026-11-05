"""Prepare a native training plan from an explicit release; do not start training."""

import argparse
import json
from pathlib import Path

from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json, parse_json
from behave.train.plan import load_configuration, prepare_from_release


def main(argv=None, *, repository: Path | None = None, prepare_plan=None):
    """The source-checkout compatibility entry may supply its known repository."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repository", type=Path, default=repository, required=repository is None,
                        help="checkout containing data/splits/current.json and evaluation membership")
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--machine-profile", type=Path,
                        help="explicit deployment settings for a split experiment; omit for saved flat configs")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--task", action="append", required=True)
    parser.add_argument("--train-split", type=Path, required=True)
    parser.add_argument("--parent-objects", type=Path)
    parser.add_argument("--parent-checkpoint", type=Path, help="JSON BlobRef for a sealed native checkpoint receipt")
    args = parser.parse_args(argv)
    if (args.parent_objects is None) != (args.parent_checkpoint is None):
        parser.error("parent objects and checkpoint must be provided together")
    prepare = prepare_from_release if prepare_plan is None else prepare_plan
    try:
        configuration = load_configuration(args.configuration, machine_profile=args.machine_profile)
        parent = (None if args.parent_checkpoint is None else
                  BlobRef.from_dict(parse_json(read_regular_file(args.parent_checkpoint))))
        prepare(args.output, configuration, args.release, args.task, args.train_split, repository=args.repository,
            parent_reader=None if args.parent_objects is None else DirectoryReader(args.parent_objects),
            parent_checkpoint=parent)
    except (RecordError, ValueError, TypeError, OSError) as error:
        print(canonical_json({"status": "unavailable", "code": getattr(error, "code", "training-prepare-invalid"),
                              "message": str(error)}).decode())
        return 1
    print(json.dumps({"binding": str(args.output.resolve() / "binding.json"), "native_training_started": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
