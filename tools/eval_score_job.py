"""Named, frozen scoring passes over saved Eval runs, with no model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from behave.eval import scoring as _job
from behave.eval.scoring import CandidateQueue, score_shard, staged

# Historical imports and CLI calls retain the installed implementation.
run = score_shard
__all__ = ["CandidateQueue", "launch", "main", "prepare", "run", "staged"]


def prepare(output, sources=(), *, policy=None, workers, previous=None, all_submitted=False,
            retry_codes=None, selection=None, shards=1, retry_testbench=True, search_seconds=0,
            isolation_startup_slots=None):
    return _job.prepare(output, sources, repository=Path(__file__).resolve().parents[1],
        policy=policy, workers=workers, previous=previous, all_submitted=all_submitted,
        retry_codes=retry_codes, selection=selection, shards=shards,
        retry_testbench=retry_testbench, search_seconds=search_seconds,
        isolation_startup_slots=isolation_startup_slots)


def launch(output, allocation, cpus, *, shard=0, process_limit=None, cpu_ids=None,
           allocated_cpus=None, allocated_cpu_ids=None):
    return _job.launch(output, allocation, cpus, shard=shard, process_limit=process_limit,
        cpu_ids=cpu_ids, allocated_cpus=allocated_cpus, allocated_cpu_ids=allocated_cpu_ids,
        cwd=Path(__file__).resolve().parents[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--source", type=Path, action="append")
    selection.add_argument("--index", type=Path, action="append", help="exact archived result index (repeatable)")
    parser.add_argument("--dataset", action="append", default=[], help="select these index datasets only")
    parser.add_argument("--policy", type=Path, help="defaults to current public baseline-only policy")
    parser.add_argument("--search-seconds", type=float, default=0,
                        help="explicit Sim search budget; default 0 preserves baseline-only rejudging")
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--isolation-startup-slots", type=int,
                        help="optional extra sandbox-startup throttle; omitted means no extra queue")
    parser.add_argument("--previous")
    parser.add_argument("--all-submitted", action="store_true")
    parser.add_argument("--retry-code", action="append", dest="retry_codes")
    parser.add_argument("--skip-testbench", action="store_true", help="retain historical TB reports without retry")
    parser.add_argument("--shards", type=int, default=1)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--allocation", required=True)
    parser.add_argument("--cpus", type=int, required=True)
    parser.add_argument("--process-limit", type=int,
                        help="host process/thread soft limit; omitted or -1 uses the inherited hard limit")
    args = parser.parse_args()
    from behave.eval.scoring import select_indices

    selected = None if args.index is None else select_indices(args.index, datasets=args.dataset)
    prepare(args.output, args.source or (), policy=args.policy, workers=args.workers,
            previous=args.previous, all_submitted=args.all_submitted, retry_codes=args.retry_codes,
            selection=selected, shards=args.shards, retry_testbench=not args.skip_testbench,
            search_seconds=args.search_seconds, isolation_startup_slots=args.isolation_startup_slots)
    print(json.dumps(launch(args.output, args.allocation, args.cpus, shard=args.shard,
                            process_limit=args.process_limit), indent=2))


if __name__ == "__main__":
    main()
