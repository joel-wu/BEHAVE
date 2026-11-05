"""Point acquisition at a completed training batch or window; never rescore it."""

import argparse
from pathlib import Path

from behave.core.records import canonical_json
from behave.improve.feedback import prepare, prepare_window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--round", type=Path)
    source.add_argument("--window-round", nargs=2, action="append", metavar=("UPDATE", "DIRECTORY"),
                        help="repeat in order for each completed update (human update number)")
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.round is not None:
        result = prepare(args.round.resolve(), args.binding.resolve(), args.output.resolve())
    else:
        try:
            rounds = tuple((int(update), Path(directory).resolve()) for update, directory in args.window_round)
        except ValueError:
            parser.error("window update numbers must be integers")
        result = prepare_window(rounds, args.binding.resolve(), args.output.resolve())
    print(canonical_json(result).decode())


if __name__ == "__main__":
    main()
