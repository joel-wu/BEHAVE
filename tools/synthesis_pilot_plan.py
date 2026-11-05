"""Compatibility entry for shared Syn study preparation; never launches a job."""

from pathlib import Path

from behave.cli.training_study import main as _main
from behave.improve.study_plan import load_settings, runtime_for, study_files
from behave.improve.study_plan import prepare as _prepare

__all__ = ["load_settings", "main", "prepare", "runtime_for", "study_files"]


def prepare(settings_path, output, *, repository=None, machine_profile=None):
    return _prepare(settings_path, output,
                    repository=Path(__file__).resolve().parents[1] if repository is None else repository,
                    machine_profile=machine_profile)


def main(argv=None):
    return _main(argv, repository=Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    raise SystemExit(main())
