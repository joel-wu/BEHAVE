"""Explicit rollback after stopped native workers; no deletion or model launch.

Restart with a new learning-stage directory, the same prepared plan, and the
returned load checkpoint. An unfinished stage's own log is never rewritten.
"""

import argparse
import json
from pathlib import Path

from .slime_stage import _native


def abandon_round(plan: Path, *, load: Path, updates: int, reason: str, workers_stopped: bool):
    binding = json.loads((plan / "binding.json").read_text())
    config = json.loads((plan / "configuration.json").read_text())
    native = _native(binding, config, load=load, save=load, updates=updates)
    return native.abandon(reason=reason, workers_stopped=workers_stopped)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--load", type=Path, required=True)
    parser.add_argument("--updates", type=int, required=True, help="absolute stopping update index plus one")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--workers-stopped", action="store_true",
                        help="operator confirms all old training/rollout workers have exited")
    args = parser.parse_args(argv)
    result = abandon_round(args.plan, load=args.load, updates=args.updates,
                           reason=args.reason, workers_stopped=args.workers_stopped)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
