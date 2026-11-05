"""Compatibility CLI for native runtime preparation/checking from this checkout."""

from pathlib import Path

from behave.train import runtime_setup
from behave.train.runtime_setup import COMPONENTS, METADATA_PROBE, inspect_environment  # noqa: F401 -- legacy exports

ROOT = Path(__file__).resolve().parents[1]


def prepare_sources(directory: Path, *, root: Path = ROOT) -> dict:
    return runtime_setup.prepare_sources(directory, root=root)


def check(configuration: Path, *, machine_profile: Path | None = None, root: Path = ROOT) -> dict:
    return runtime_setup.check(configuration, machine_profile=machine_profile, root=root)


def main():
    from behave.cli.training_runtime import main as runtime_main

    return runtime_main(repository=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
