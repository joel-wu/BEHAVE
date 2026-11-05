"""Prepare, run or score an external-method workflow.

Preparation is offline. Rollout may dispatch the explicitly configured model.
"""

import argparse
import json
from pathlib import Path

from .configuration import prepare
from .provider import SAMPLING_POLICIES


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "rollout", "score"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--method", choices=("chipmate", "mage"))
    parser.add_argument("--tasks", type=Path, help="whitespace-separated task IDs")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--options", type=Path, help="upstream method settings JSON")
    parser.add_argument("--provider-sampling", choices=SAMPLING_POLICIES, default="upstream",
                        help="explicitly omit sampling overrides when the selected provider does not accept them")
    parser.add_argument("--judgment", default="baseline")
    parser.add_argument("--judge-policy", type=Path)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        if args.template is None or args.method is None:
            parser.error("prepare requires --template and --method")
        print(prepare(args.output, args.template, args.method, tasks=args.tasks, workers=args.workers,
                      options=None if args.options is None else json.loads(args.options.read_text()),
                      provider_sampling=args.provider_sampling))
    elif args.action == "rollout":
        from .execution import rollout

        rollout(args.output)
    else:
        from .execution import score

        score(args.output, name=args.judgment, workers=args.workers, policy_file=args.judge_policy)


if __name__ == "__main__":
    main()
