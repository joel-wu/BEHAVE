"""Prepare pinned native sources or inspect an explicitly selected runtime.

This does not install Python packages, import CUDA or dispatch training. The
repository supplies the reviewed native observation and external source pins.
"""

import argparse
import json
from pathlib import Path

from behave.train import runtime_setup


def main(argv=None, *, repository: Path | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sources = commands.add_parser("sources", help="prepare pinned sources; does not install Python packages")
    sources.add_argument("--directory", type=Path, required=True)
    inspect = commands.add_parser("check", help="read-only metadata and editable-source check")
    inspect.add_argument("--configuration", type=Path, required=True)
    inspect.add_argument("--machine-profile", type=Path)
    for command in (sources, inspect):
        command.add_argument("--repository", type=Path, required=repository is None, default=repository,
                             help="explicit root containing external/manifest.toml and native runtime pins")
        command.add_argument("--output", type=Path, help="new receipt file; existing records are never overwritten")
    args = parser.parse_args(argv)
    if args.output and (args.output.exists() or args.output.is_symlink()):
        parser.error("output already exists; choose a new receipt path")
    if args.command == "sources":
        result = runtime_setup.prepare_sources(args.directory, root=args.repository.resolve())
    else:
        result = runtime_setup.check(args.configuration, machine_profile=args.machine_profile, root=args.repository.resolve())
    content = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as handle:
            handle.write(content)
    print(content, end="")
    return int(result["status"] == "mismatch")

