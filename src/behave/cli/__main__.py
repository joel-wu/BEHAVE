"""Thin command router. Each application keeps its own validated configuration."""

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    commands = ("eval", "train", "trainer", "data", "inspect", "release", "credentials")
    parser = argparse.ArgumentParser(description=__doc__, usage="%(prog)s {" + ",".join(commands) + "} [options]")
    parser.add_argument("command", choices=commands)
    arguments = sys.argv[1:] if argv is None else argv
    selected = parser.parse_args(arguments[:1])
    # Closed, lazy routing: --help neither imports a simulator nor selects plugins.
    if selected.command == "eval":
        from behave.eval.__main__ import main as application
    elif selected.command == "train":
        from behave.train.__main__ import main as application
    elif selected.command == "trainer":
        from .trainer import main as application
    elif selected.command == "data":
        from behave.data.__main__ import main as application
    elif selected.command == "release":
        from .release import main as application
    elif selected.command == "credentials":
        from .credentials import main as application
    else:
        from .inspect import main as application
    return application(arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
