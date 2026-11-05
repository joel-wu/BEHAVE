"""Prepare Raw source learning for the same owned native Slime driver.

Run preparation in the native tokenizer environment. It validates the frozen
external corpus and writes a binding; it does not launch training. Generated
task answers never become Raw targets. Empty material rounds need no update.
"""

import argparse
import json
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.storage import DirectoryStore
from behave.train.slime_data import prepare_raw_plan as prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--objects", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True, help="JSON BlobRef for the frozen token corpus")
    parser.add_argument("--parent-objects", type=Path)
    parser.add_argument("--parent-checkpoint", type=Path, help="JSON BlobRef for a sealed native checkpoint receipt")
    args = parser.parse_args()
    if (args.parent_objects is None) != (args.parent_checkpoint is None):
        parser.error("parent objects and checkpoint must be provided together")
    prepare(args.output, json.loads(args.configuration.read_text()), args.objects,
        BlobRef.from_dict(json.loads(args.corpus.read_text())),
        parent_reader=None if args.parent_objects is None else DirectoryStore(args.parent_objects),
        parent_checkpoint=None if args.parent_checkpoint is None else BlobRef.from_dict(json.loads(args.parent_checkpoint.read_text())))
    print(json.dumps({"binding": str(args.output.resolve() / "binding.json"), "native_training_started": False}))


if __name__ == "__main__":
    main()
