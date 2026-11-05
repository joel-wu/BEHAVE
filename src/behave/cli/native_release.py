"""Freeze explicitly selected native experiment inputs; not benchmark approval.

Current native files use the public Sim contract API. --from-release preserves
saved task bytes and only rebinds the simulator versions in a new release.
Neither mode generates solutions or reinterprets saved scores.
"""

import argparse
import json
from pathlib import Path

from behave.core.records import RecordError, canonical_json


def main(argv=None, *, repository: Path | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new destination; existing inputs are never overwritten")
    parser.add_argument("--repository", type=Path, default=repository,
                        help="explicit checkout containing data/problems; required unless --from-release is used")
    parser.add_argument("--tasks", type=Path, action="append", required=True,
                        help="task-ID list; repeat for disjoint, distinctly named lists")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--from-release", type=Path,
                        help="retain saved inputs, updating only their pinned Sim versions")
    args = parser.parse_args(argv)
    if args.repository is None and args.from_release is None:
        parser.error("--repository is required when freezing current native tasks")
    if args.workers < 1:
        parser.error("--workers must be positive")
    from behave.data import native_release

    try:
        result = (native_release.prepare(args.repository.resolve(), args.output.resolve(),
                                        args.tasks, workers=args.workers)
                  if args.from_release is None else
                  native_release.refresh_simulator(args.from_release.resolve(), args.output.resolve(),
                                                   args.tasks, workers=args.workers))
    except (RecordError, ValueError, TypeError, OSError, KeyError) as error:
        print(canonical_json({"status": "unavailable", "code": getattr(error, "code", "native-inputs-invalid"),
                              "message": str(error)}).decode())
        return 1
    print(json.dumps(result, indent=2))
    return 0
