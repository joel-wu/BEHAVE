"""Measure Behavior frontend coverage and record reproducible per-task results.

    python tools/population.py --bound 4 --jobs 8

The report includes the dataset digest, tool versions, revision and timeouts.
Use --expected to compare with a baseline for the same input digest.
Run on a compute allocation; use --out to retain a new measurement record.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from behave.core import runtime_defaults as defaults
from behave.sim.core.resources import available_cpus
from tools.measurement_records import RECORD_SCHEMA, framed_sha256, write_json_exclusive

#: Every tier `tiers.TIERS` names, plus what this harness itself can fail with. Listed so
#: a model always lands in exactly one column and the columns add up to the dataset.
OUTCOMES = ("bounded", "instrumented", "concrete", "none",
            "no-contract", "timeout", "harness-error")


class TooSlow(BaseException):
    """Raised by the per-model alarm. Not an ``Exception``, so a model's own
    ``except Exception`` cannot swallow the timeout and run on."""


def _one(args):
    frozen, bound, limit = args
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TooSlow()))
    signal.alarm(limit)
    name, key = frozen["model"], frozen["path"]
    try:
        sys.setrecursionlimit(40000)
        from behave.sim import task
        from behave.sim.behavior.tiers import tier_of

        try:
            if frozen["spec"] is None:
                raise ValueError("spec.md is absent")
            schema = task.schema_of_spec(frozen["spec"].decode("utf-8"), name)
        except Exception as e:                                  # noqa: BLE001
            return (key, name, "no-contract", "spec",
                    f"{type(e).__name__}: {e}"[:160])
        got = tier_of(frozen["behavior"].decode("utf-8"), schema,
                      file=f"{key}/behavior.py", bound=bound)
        return (key, name, got.name, got.stopped_by or "", got.detail[:160])
    except TooSlow:
        return (key, name, "timeout", "alarm", f"over {limit}s")
    except Exception as e:                                      # noqa: BLE001
        return (key, name, "harness-error", type(e).__name__, str(e)[:160])
    finally:
        signal.alarm(0)


def _freeze(paths):
    from behave.core.paths import problems_root

    root = problems_root().resolve()
    frozen = []
    for path in paths:
        directory = Path(path).resolve().parent
        try:
            key = directory.relative_to(root).as_posix()
        except ValueError:
            key = directory.as_posix()
        behavior = (directory / "behavior.py").read_bytes()
        spec = ((directory / "spec.md").read_bytes()
                if (directory / "spec.md").is_file() else None)
        frozen.append({"model": directory.name, "path": key,
                       "behavior": behavior, "spec": spec})
    return sorted(frozen, key=lambda row: row["path"])


def dataset_digest(paths=None, contents=None):
    """One value naming the dataset, so two runs can be told to be about the same one.

    Over the problem-relative path and the content of both artifacts a measurement
    reads. Sorted, so it does not depend on directory order.
    """
    frozen = contents if contents is not None else _freeze(paths)
    return framed_sha256(
        (f"{row['path']}/{name}", row[name.removesuffix(".py").removesuffix(".md")])
        for row in frozen for name in ("behavior.py", "spec.md"))


def _git_state():
    def run(*args):
        got = subprocess.run(["git", "-C", str(REPO), *args],
                             capture_output=True, text=True, timeout=defaults.TOOL_INSPECTION_SECONDS,
                             check=False)
        if got.returncode != 0:
            raise SystemExit(
                f"[population] git {' '.join(args)} failed: "
                f"{got.stderr.strip()}")
        return got.stdout.strip()

    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    return {"commit": run("rev-parse", "HEAD"),
            "branch": None if branch == "HEAD" else branch,
            "detached": branch == "HEAD",
            "dirty_files": run("status", "--porcelain",
                               "--untracked-files=normal").splitlines()}


def provenance(frozen, bound, limit, jobs, git_state=None):
    """What the numbers are about. Two runs whose provenance differs are two runs."""
    try:
        import z3
        z3_version = z3.get_version_string()
    except Exception:                                           # noqa: BLE001
        z3_version = "unavailable"
    state = git_state or _git_state()
    return {
        "record_schema": RECORD_SCHEMA,
        "problems": len(frozen),
        "dataset_digest": dataset_digest(contents=frozen),
        "commit": state["commit"],
        "branch": state.get("branch"),
        "detached": bool(state.get("detached", False)),
        # Said out loud, because a measurement taken with uncommitted changes to `src`
        # is not a measurement of `commit`.
        "dirty_files": list(state["dirty_files"]),
        "python": sys.version.split()[0],
        "z3": z3_version,
        "bound": bound,
        "per_model_timeout_s": limit,
        "jobs": jobs,
        # There is no seed: every gate here is deterministic. Recorded as `None` rather
        # than left out, so that the day something in the chain becomes random the field
        # is already the place it goes.
        "seed": None,
        "manifest_scope": (
            "not applicable: this is the raw Behavior frontend-tier census and "
            "does not admit or execute task/run manifests"),
    }


def measure(bound, limit, jobs):
    from behave.core.paths import problems_root

    if type(bound) is not int or bound < 1:
        raise ValueError("bound must be a positive integer")
    if type(limit) is not int or limit < 1:
        raise ValueError("per-model timeout must be a positive integer")
    if type(jobs) is not int or jobs < 1:
        raise ValueError("jobs must be a positive integer")

    paths = sorted(problems_root().rglob("*/behavior.py"))
    if not paths:
        raise SystemExit(
            f"no problems under {problems_root()}: this tool needs the dataset, which "
            f"a checkout of the code alone does not carry")

    frozen = _freeze(paths)
    state = _git_state()
    where = provenance(frozen, bound, limit, jobs, state)
    print(f"  {where['problems']} problems, dataset {where['dataset_digest']}, "
          f"commit {where['commit']}{' +dirty' if where['dirty_files'] else ''}, "
          f"bound {bound}, timeout {limit}s, jobs {jobs}", flush=True)

    tiers, gates, rows = Counter(), Counter(), []
    started, done = time.time(), 0
    with ProcessPoolExecutor(jobs) as pool:
        futures = [pool.submit(_one, (row, bound, limit)) for row in frozen]
        for future in as_completed(futures):
            key, name, tier, gate, detail = future.result()
            done += 1
            tiers[tier] += 1
            if gate:
                gates[(tier, gate)] += 1
            rows.append({"model": name, "path": key, "tier": tier, "gate": gate,
                         "detail": detail})
            if done % 100 == 0:
                print(f"  {done}/{len(paths)} in {time.time() - started:.0f}s",
                      flush=True)

    unknown = sorted(set(tiers) - set(OUTCOMES))
    if unknown:
        raise SystemExit(f"outcomes this tool does not know: {unknown}")
    aggregates = {name: tiers.get(name, 0) for name in OUTCOMES}
    total = sum(aggregates.values())
    if total != len(paths):
        raise SystemExit(f"{total} outcomes for {len(paths)} problems: a model landed "
                         f"in no column or in two")
    return {
        "provenance": where,
        "aggregates": aggregates,
        "gates": {f"{tier}/{gate}": n for (tier, gate), n in sorted(gates.items())},
        "elapsed_s": round(time.time() - started, 1),
        "models": sorted(rows, key=lambda r: r["path"]),
    }


def report(got, expected):
    print()
    print("  tier                 count")
    for name in OUTCOMES:
        n = got["aggregates"][name]
        if n or name in ("bounded", "instrumented", "none"):
            print(f"  {name:20} {n:5d}")
    print(f"  {'total':20} {sum(got['aggregates'].values()):5d}")

    dropped = got["aggregates"]["timeout"] + got["aggregates"]["harness-error"]
    print()
    print(f"  dropped by this tool rather than by the frontend: {dropped}")
    for row in got["models"]:
        if row["tier"] in ("timeout", "harness-error"):
            print(f"    {row['model'][:52]:52} {row['tier']}: {row['detail'][:60]}")

    print()
    print("  the gates that stopped models, most first")
    for gate, n in sorted(got["gates"].items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {n:5d}  {gate}")

    if expected is None:
        print()
        print("  no baseline to compare against; --write-expected records one")
        return 0

    old, new = expected["aggregates"], got["aggregates"]
    moved = {k: (old.get(k, 0), new[k]) for k in OUTCOMES if old.get(k, 0) != new[k]}
    print()
    same_dataset = (
        expected["provenance"]["dataset_digest"]
        == got["provenance"]["dataset_digest"]
    )
    if not same_dataset:
        print("  the dataset digest differs from the baseline's, so the two totals are "
              "about two datasets and a difference between them means nothing on its "
              "own:")
        print(f"    baseline {expected['provenance']['dataset_digest']}  "
              f"now {got['provenance']['dataset_digest']}")
    if not moved and same_dataset:
        print(f"  matches the baseline recorded at commit "
              f"{expected['provenance']['commit']}")
        return 0
    print(f"  differs from the baseline recorded at commit "
          f"{expected['provenance']['commit']}:")
    for name, (was, now) in moved.items():
        print(f"    {name:20} {was:5d} -> {now:5d}   ({now - was:+d})")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bound", type=int, default=10,
                        help="finite steps used by bounded encoding")
    parser.add_argument("--timeout", type=int, default=90,
                        help="seconds per model before this tool gives up on it")
    parser.add_argument("--jobs", type=int,
                        default=max(1, min(16, available_cpus() - 2)))
    parser.add_argument("--out", type=Path, default=None,
                        help="exclusively install the full per-model record here")
    parser.add_argument("--expected", type=Path, help="baseline record to compare or create")
    parser.add_argument("--write-expected", action="store_true",
                        help="create --expected only when absent")
    args = parser.parse_args(argv)
    if args.write_expected and args.expected is None:
        parser.error("--write-expected requires --expected PATH")
    if args.expected is not None and not args.write_expected and not args.expected.is_file():
        parser.error(f"baseline does not exist: {args.expected}")

    got = measure(args.bound, args.timeout, args.jobs)
    expected = (json.loads(args.expected.read_text())
                if args.expected is not None and args.expected.is_file() else None)
    code = report(got, expected)

    if args.out or args.write_expected:
        state = _git_state()
        if state["dirty_files"] or not state["detached"]:
            raise SystemExit(
                "a population record requires a clean detached worktree")
        # The rows were computed from a frozen snapshot.  Refuse persistence if the
        # live task set or bytes no longer match the identity in the document.
        from behave.core.paths import problems_root
        paths = sorted(problems_root().rglob("*/behavior.py"))
        if dataset_digest(paths) != got["provenance"]["dataset_digest"]:
            raise SystemExit(
                "the population changed during measurement; refusing to write")
        if state["commit"] != got["provenance"]["commit"]:
            raise SystemExit(
                "the revision changed during measurement; refusing to write")
    if args.out:
        try:
            write_json_exclusive(args.out, {
                "record_schema": RECORD_SCHEMA,
                "kind": "behavior-frontend-population",
                **got,
            })
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from None
        print(f"\n  per-model report written to {args.out}")
    if args.write_expected:
        # The baseline carries the provenance too, so a later run can say whether a
        # difference is about the code or about the dataset.
        if args.expected.exists():
            raise SystemExit(
                f"refusing to overwrite historical baseline {args.expected}; write a "
                "new invocation record with --out and update the baseline only in "
                "a separately reviewed history change")
        write_json_exclusive(args.expected, {
            "record_schema": RECORD_SCHEMA,
            "kind": "behavior-frontend-population-baseline",
            "provenance": got["provenance"],
            "aggregates": got["aggregates"], "gates": got["gates"],
        })
        print(f"  baseline written to {args.expected}")
        return 0
    return code


if __name__ == "__main__":
    raise SystemExit(main())
