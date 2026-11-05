"""Compatibility command for pinned external sources; package owns verification."""

import argparse
from pathlib import Path

from behave.runtime import snapshots
from behave.runtime.snapshots import _expected_index, _git, _matches, _source  # noqa: F401 -- legacy imports


def install(name, *, root=None, checkout=None, exact=False, verify_only=False):
    return snapshots.install(name, root=Path(__file__).resolve().parents[1] if root is None else root,
                               checkout=checkout, exact=exact, verify_only=verify_only)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("methods", nargs="*", metavar="{chipmate,mage}")
    args = parser.parse_args()
    if set(args.methods) - {"chipmate", "mage"}:
        parser.error("methods must be chipmate or mage")
    for name in args.methods or ("chipmate", "mage"):
        print(install(name))


if __name__ == "__main__":
    main()
