"""Named checkpoint/seed scheduling over the existing Eval and scoring entries.

One node step stages a checkpoint once and runs its inference seeds in order.
Solver, serving, judgments and task archives remain the shared implementations.
An interrupted owner is never silently replaced or its candidates resampled.
Use run-stages for multiple prepared roots on one allocation: their CPU scoring
shares one budget and never blocks the next root's GPU generation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from behave.eval import checkpoint as _study
from behave.eval.checkpoint import (  # noqa: F401 -- retained operator exports
    checkpoint_directory,
    control_directory,
    generation_directory,
    measurement_directory,
    node,
    prepare_dispatch,
    prepare_measurement,
    read,
    requested_seeds,
    resolve_checkpoint,
    scoring_quality,  # noqa: F401 -- retained operator export
    serving_port_lease,
    snapshot_directory,
    stage_weights,
)
from behave.eval.checkpoint import (
    serving_spec as serving_spec,  # noqa: PLC0414 -- historical operator export
)


def submit_checkpoint(root, index):
    """Compatibility entry; the package owns submission and its resource recipe."""
    from behave.eval.checkpoint import submit_checkpoint as submit

    return submit(root, index, cwd=Path(__file__).resolve().parents[1])


def score(root, entry, seed):
    return _study.score(root, entry, seed, repository=Path(__file__).resolve().parents[1],
                        cwd=Path(__file__).resolve().parents[1])


def run(root):
    return _study.run(root, repository=Path(__file__).resolve().parents[1],
                      cwd=Path(__file__).resolve().parents[1])


def run_stages(roots, *, progress_directory=None):
    return _study.run_stages(roots, repository=Path(__file__).resolve().parents[1],
                            cwd=Path(__file__).resolve().parents[1], progress_directory=progress_directory)


def launch(root):
    return _study.launch(root, cwd=Path(__file__).resolve().parents[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("launch", "run", "run-stages", "node"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--index", type=int)
    parser.add_argument("--stage", type=Path, action="append", default=[],
                        help="Additional prepared stage roots; run-stages overlaps their CPU scoring with generation")
    parser.add_argument("--progress-directory", type=Path)
    args = parser.parse_args()
    if (args.stage or args.progress_directory) and args.action != "run-stages":
        parser.error("--stage and --progress-directory require run-stages")
    if args.action == "launch":
        print(json.dumps(launch(args.root), indent=2))
    elif args.action == "run-stages":
        raise SystemExit(run_stages([args.root, *args.stage], progress_directory=args.progress_directory))
    else:
        raise SystemExit(run(args.root) if args.action == "run" else node(args.root, args.index))


if __name__ == "__main__":
    main()
