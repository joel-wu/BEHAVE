"""Prepare a self-improvement study from explicit inputs; do not start training."""

import argparse
from pathlib import Path

from behave.core.records import canonical_json


def main(argv=None, *, repository: Path | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=repository, required=repository is None,
                        help="root for the settings' configuration, guides, sources and task paths")
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--machine-profile", type=Path,
                        help="deployment settings when training_configuration is a split experiment")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from behave.improve.study_plan import prepare

    print(canonical_json(prepare(args.settings, args.output, repository=args.repository,
                                machine_profile=args.machine_profile)).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
