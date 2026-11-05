"""Source-checkout compatibility entry for native training plan preparation.

Installed users can use ``behave-train prepare`` with an explicit repository.
Both entries call the same package implementation; neither starts training.
"""

from pathlib import Path

from behave.train.plan import prepare_from_release
from behave.train.slime_launch import native_arguments  # noqa: F401 -- legacy import

ROOT = Path(__file__).resolve().parents[1]


def prepare(output: Path, configuration: dict, release: Path, task_ids: list[str],
            train_split: Path, *, parent_reader=None, parent_checkpoint=None,
            repository: Path = ROOT) -> dict:
    return prepare_from_release(output, configuration, release, task_ids, train_split,
        repository=repository, parent_reader=parent_reader, parent_checkpoint=parent_checkpoint)


def main(argv=None):
    from behave.cli.training_plan import main as prepare_main

    return prepare_main(argv, repository=ROOT, prepare_plan=prepare)


if __name__ == "__main__":
    raise SystemExit(main())
