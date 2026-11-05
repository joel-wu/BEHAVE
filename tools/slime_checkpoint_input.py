"""Legacy checkpoint handoff command; implementation lives in behave.train."""

import argparse
from pathlib import Path

from behave.core.file_input import read_regular_file
from behave.core.records import RecordError, canonical_json
from behave.core.storage import install_exclusive
from behave.train import checkpoint_input as _implementation

model_handoff = _implementation.model_handoff
periodic_handoff = _implementation.periodic_handoff
prepare = _implementation.prepare
prepare_round = _implementation.prepare_round


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, help="legacy option; metadata handoff does not read weight bytes")
    args = parser.parse_args()
    result = prepare(args.job, workers=args.workers)
    data = canonical_json(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not install_exclusive(args.output.parent, args.output.name, data) and read_regular_file(args.output) != data:
        raise RecordError("checkpoint-handoff-changed", "use a new handoff for changed training artifacts")
    print(
        canonical_json(
            {
                "checkpoint": result["checkpoint"],
                "step": result["step"],
                "model_revision": result["model_revision"],
                "output": str(args.output),
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
