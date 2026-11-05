"""The tracked lowering census: one typed row per task, grouped by refusal code.

**Definition (todo3 T3-11).** For every `behavior.py` of the population, run the
lowering-core entry `lower_with_state_ranges` (no task schema -- the raw frontend
question "is this source in the subset") and emit one row:

    {"task", "outcome", "code", "what", "site": {"file", "line", "col"},
     "stage", "ir_commands", "representation_cost", "elapsed_s"}

`outcome` is one of `lowered`, `refused`, `analysis-timeout`, `crashed`. `code` is the
**stable rule identity** every `LoweringError` raise site states (kebab-case,
enforced by `tests/sim/test_lowering_census.py`); grouping happens on it, never on
normalized message text -- the scratch census this replaces regexed the sentences,
which split one rule across spellings and merged two rules that shared one. `stage`
is the module that refused (`extract`, `lowering`, `lower_graph`, ...), read off the
deepest frame of the raise inside `behave/sim/behavior/`. An analysis limit is a
**typed refusal** (`analysis-timeout`), not a crash and not a silent omission.

The result is one JSON document under `tools/census/`, named by date and commit and
meant to be committed: a dated result under revision `A` cannot certify an
improvement claimed at revision `B`, which is exactly how the scratch log was being
read. A record is written only from a clean detached worktree. Its header carries
the commit, a digest over every `behavior.py` in the dataset, the entry point, and
the per-task limit -- the facts a reader needs to know what the numbers describe.

Run on the compute allocation, never the login node:

    srun --jobid $(cat ~/.dev_jobid) --overlap --ntasks=1 \\
        --cpus-per-task=4 --chdir=$REPO \\
        .venv/bin/python tools/lowering_census.py --workers 4 --write

Four workers match the frontend's four cross-process construction slots. A larger
pool is useful only as an explicitly labelled contention stress run: the per-task
deadline includes time spent waiting to enter construction, so oversubscription can
otherwise turn scheduler delay into a false ``analysis-timeout`` row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
# Direct execution puts only ``tools/`` on sys.path.  The documented command
# imports both the installed-style ``behave`` package and the tracked
# measurement helpers, so make those two checkout roots explicit.
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

from tools.measurement_records import RECORD_SCHEMA, framed_sha256, write_json_exclusive
from behave.core import runtime_defaults as defaults

#: Where a run's rows land. Tracked in git, one file per (date, commit).
CENSUS_DIR = Path(__file__).resolve().parent / "census"

#: Runtime-owned diagnostic deadline, not a language-capability limit: the
#: per-artifact budget of Behavior exact analysis, which begins with lowering.
DEFAULT_TIMEOUT_S = defaults.SIM_BEHAVIOR_ANALYSIS_MS // 1000


class _Alarm(BaseException):
    """The per-task deadline. **BaseException on purpose**: the lowering has
    broad `except Exception` regions (reading a model's types, for one), and an
    alarm that inherits `Exception` is swallowed there -- reproduced: a 5s stall
    inside one under `timeout_s=1` came back `outcome="lowered"` after 6 wall
    seconds. Application code has no business catching a measurement deadline;
    `measure_authority.Deadline` draws the same line."""


def _ring(signum, frame):
    raise _Alarm()


def _stage_of(exc):
    """The `behave/sim/behavior/` module that raised, off the deepest such frame."""
    stage = ""
    for frame in traceback.extract_tb(exc.__traceback__):
        name = Path(frame.filename)
        if name.parent.name == "behavior" and name.parent.parent.name == "sim":
            stage = name.stem
    return stage


def census_row(behavior_path, timeout_s=DEFAULT_TIMEOUT_S, source=None):
    """The one typed row for one task. Never raises; every outcome is a row.

    ``source`` is the model's text as **frozen at the start of the run**: the
    parent reads every file once, digests the snapshot, and the workers lower
    exactly those bytes -- so a dataset edited mid-run cannot produce rows from a
    mixture of versions under a header that binds only the final state. `None`
    reads the file, for a single-row caller.
    """
    from behave.sim.symbolic import ir
    from behave.sim.behavior.lowering import LoweringError, lower_with_state_ranges

    task = Path(behavior_path).parent.name
    # And the suite-relative path beside the short name: `parent.name` is unique
    # across today's 836 and nothing guarantees it stays so across suites
    # (`rtllm_v2/RAM`, `verilogeval_v2/2014_q3bfsm` are already bare words).
    try:
        from behave.core.paths import problems_root
        rel = str(Path(behavior_path).resolve().parent
                  .relative_to(problems_root()))
    except Exception:                                                # noqa: BLE001
        rel = str(Path(behavior_path).parent)
    if source is None:
        source = Path(behavior_path).read_text()
    # alarm(0) disables the deadline and alarm(-1) wraps to ~2**32-1 seconds, so
    # a non-positive limit must fail here rather than silently unmeasure itself.
    if type(timeout_s) is not int or timeout_s < 1:
        raise ValueError(f"timeout_s must be at least 1 second, got {timeout_s}")
    began = time.monotonic()
    old = signal.signal(signal.SIGALRM, _ring)
    signal.alarm(int(timeout_s))
    try:
        proc = lower_with_state_ranges(source, file="behavior.py")
        return {"task": task, "path": rel, "outcome": "lowered",
                "code": None, "what": None,
                "site": None, "stage": None,
                "ir_commands": sum(1 for _ in ir.walk(proc.body)),
                "representation_cost":
                    ir.procedure_representation_cost(proc),
                "elapsed_s": round(time.monotonic() - began, 3)}
    except LoweringError as exc:
        node = exc.node
        return {"task": task, "path": rel, "outcome": "refused",
                "code": exc.code,
                "what": str(exc.what)[:160],
                "site": {"file": "behavior.py",
                         "line": int(getattr(node, "lineno", 0) or 0),
                         "col": int(getattr(node, "col_offset", 0) or 0)},
                "stage": _stage_of(exc), "ir_commands": None,
                "representation_cost": None,
                "elapsed_s": round(time.monotonic() - began, 3)}
    except _Alarm:
        return {"task": task, "path": rel, "outcome": "analysis-timeout",
                "code": "analysis-timeout",
                "what": f"lowering exceeded {timeout_s}s; an analysis limit is a "
                        f"typed refusal, not a claim about the subset",
                "site": None, "stage": None, "ir_commands": None,
                "representation_cost": None,
                "elapsed_s": round(time.monotonic() - began, 3)}
    except Exception as exc:                                         # noqa: BLE001
        # A crash is the frontend's own bug, not a rule: it gets its own outcome so
        # a broken build cannot masquerade as two hundred principled refusals.
        return {"task": task, "path": rel, "outcome": "crashed",
                "code": f"crash-{type(exc).__name__}",
                "what": str(exc)[:160], "site": None, "stage": _stage_of(exc),
                "ir_commands": None,
                "representation_cost": None,
                "elapsed_s": round(time.monotonic() - began, 3)}
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _pair_row(pair, timeout_s):
    path, source = pair
    return census_row(path, timeout_s=timeout_s, source=source)


def dataset_digest(paths, contents=None):
    """One digest over every model in the dataset, order-independent.

    Relative paths and raw file bytes are length-framed before hashing, then sorted by
    path -- so the header says *which* files the numbers are about without relying on
    delimiters or ambiguous concatenation.  A census over an edited or renamed dataset
    cannot present itself as a re-measurement of the old one.
    """
    from behave.core.paths import problems_root

    root = problems_root().resolve()
    members = []
    for p in sorted(Path(p).resolve() for p in paths):
        name = (p.relative_to(root).as_posix()
                if p.is_relative_to(root) else p.as_posix())
        body = (contents[str(p)].encode("utf-8") if contents is not None
                else p.read_bytes())
        members.append((name, body))
    return framed_sha256(members)


_ROW_FIELDS = {
    "task", "path", "outcome", "code", "what", "site", "stage",
    "ir_commands", "representation_cost", "elapsed_s",
}
_OUTCOMES = {"lowered", "refused", "analysis-timeout", "crashed"}


def validate_rows(rows, expected_paths):
    """Fail closed before a partial or malformed population becomes a record."""
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        raise SystemExit("[census] worker results are not a materialized row list")
    from behave.core.paths import problems_root

    root = problems_root().resolve()
    expected = {
        str(Path(path).resolve().parent.relative_to(root))
        for path in expected_paths
    }
    got = [row.get("path") for row in rows]
    if len(got) != len(set(got)):
        raise SystemExit("[census] two worker rows name the same task path")
    if set(got) != expected:
        raise SystemExit(
            "[census] worker rows do not cover exactly the frozen task set: "
            f"missing={sorted(expected - set(got))[:4]}, "
            f"extra={sorted(set(got) - expected)[:4]}")
    for row in rows:
        if set(row) != _ROW_FIELDS or row.get("outcome") not in _OUTCOMES:
            raise SystemExit(
                "[census] a worker returned a malformed typed row for "
                f"{row.get('path')!r}")
        if type(row.get("task")) is not str or not row["task"] \
                or type(row.get("path")) is not str or not row["path"]:
            raise SystemExit("[census] a worker row has no stable task identity")
        if type(row.get("elapsed_s")) not in (int, float) \
                or not math.isfinite(row["elapsed_s"]) or row["elapsed_s"] < 0:
            raise SystemExit(
                f"[census] a worker returned invalid elapsed telemetry for "
                f"{row.get('path')!r}")
        outcome = row["outcome"]
        if outcome == "lowered":
            valid = (
                row["code"] is None and row["what"] is None
                and row["site"] is None and row["stage"] is None
                and type(row["ir_commands"]) is int and row["ir_commands"] > 0
                and type(row["representation_cost"]) is int
                and row["representation_cost"] >= row["ir_commands"])
        elif outcome == "refused":
            site = row["site"]
            valid = (
                type(row["code"]) is str and bool(row["code"])
                and type(row["what"]) is str
                and type(site) is dict
                and set(site) == {"file", "line", "col"}
                and type(site["file"]) is str
                and type(site["line"]) is int and site["line"] >= 0
                and type(site["col"]) is int and site["col"] >= 0
                and type(row["stage"]) is str and bool(row["stage"])
                and row["ir_commands"] is None
                and row["representation_cost"] is None)
        elif outcome == "analysis-timeout":
            valid = (
                row["code"] == "analysis-timeout"
                and type(row["what"]) is str
                and row["site"] is None and row["stage"] is None
                and row["ir_commands"] is None
                and row["representation_cost"] is None)
        else:
            valid = (
                type(row["code"]) is str
                and row["code"].startswith("crash-")
                and type(row["what"]) is str and row["site"] is None
                and (row["stage"] is None or type(row["stage"]) is str))
            valid = (valid and row["ir_commands"] is None
                     and row["representation_cost"] is None)
        if not valid:
            raise SystemExit(
                "[census] a worker row contradicts its declared outcome for "
                f"{row['path']!r}")


def record_configuration(dataset, timeout_s, match, workers):
    """Complete identity of the knobs that may change census rows."""
    payload = json.dumps(
        {"dataset": dataset, "timeout_s": int(timeout_s),
         "match": sorted(match or []), "workers": int(workers)},
        sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def record_path(commit, stamp, dataset, timeout_s, match, workers):
    """Where one run's record lands: named by everything that decides what the
    rows are, plus the invocation's own timestamp. Date + commit alone let a
    `--match`/`--timeout` run on the same day silently overwrite the canonical
    836-row record (and `tools/census` is outside the dirty check by design, so
    nothing would even flag it); `workers` is in the identity because contention
    moves the timeout outcomes; and the timestamp is in the name because two runs
    of one configuration are two measurements -- `elapsed_s` alone guarantees
    their bytes differ, so pretending the second is an idempotent rerun of the
    first was never true (review 14)."""
    identity = record_configuration(
        dataset, timeout_s, match, workers)[:12]
    return CENSUS_DIR / f"lowering_{stamp}_{commit}_{identity}.json"


def write_record(path, document):
    """Atomically, and never over a record that says something else.

    `write_text` interrupted mid-run leaves truncated JSON under the canonical
    name; a rerun with different rows silently replaces history. So: temp file,
    then `os.link` into the final name -- an atomic **exclusive** create, never a
    replace -- and an existing file with different bytes is a refusal:
    re-measuring identically is idempotent, revising history is not a thing this
    tool does.
    """
    try:
        return write_json_exclusive(path, document)
    except RuntimeError as exc:
        raise SystemExit(f"[census] {exc}") from None


def _commit():
    def run(*argv):
        # **Fail closed.** The return code was ignored, so a git failure (exit
        # 128: not a repository, a lock, a corrupt index) read as "unknown
        # commit, clean tree" and --write happily bound the rows to nothing
        # (review 16). A census whose provenance command failed has no
        # provenance, and no provenance is a refusal, not a default.
        got = subprocess.run(argv, capture_output=True, text=True, check=False,
                             cwd=Path(__file__).resolve().parents[1])
        if got.returncode != 0:
            raise SystemExit(
                f"[census] {' '.join(argv)} failed "
                f"(exit {got.returncode}): {got.stderr.strip()[:200]} -- a "
                f"record cannot bind rows to a commit git cannot name")
        return got.stdout.strip()

    commit = run("git", "rev-parse", "--short", "HEAD")
    commit_full = run("git", "rev-parse", "HEAD")
    # The record is installed only after the final clean-tree check, so it does not
    # need to be hidden from that check. Untracked files **count** (review 15):
    # `--untracked-files=no` made "clean" mean only "tracked files unmodified",
    # so a brand-new task directory or module was invisible to the flag while
    # fully visible to the measurement. (Ignored paths -- scratch/ -- still do
    # not count; a formal census is best taken in a detached clean worktree.)
    dirty = bool(run("git", "status", "--porcelain", "--untracked-files=normal"))
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
    return commit, commit_full, dirty, branch == "HEAD"


def summarize(rows):
    """The grouped view, by stable code -- with `call-unlowered` sub-grouped by
    callee, since one rule covers every unregistered builtin and the work list
    wants the names."""
    from collections import Counter

    outcomes = Counter(r["outcome"] for r in rows)
    codes = Counter(r["code"] for r in rows if r["code"])
    examples = {}
    calls = Counter()
    for r in rows:
        if r["code"]:
            examples.setdefault(r["code"], r["task"])
        if r["code"] == "call-unlowered":
            calls[(r["what"] or "").split(" with ")[0]] += 1
    return outcomes, codes, examples, calls


def nonlowered_diagnostics(rows):
    """Stable JSON lines for the rows that need language or task work.

    The formal census document remains the only persistent measurement.  This view is
    intentionally derived from its already validated rows: it exposes every affected
    task during a diagnostic run without creating a second row schema or another file
    that could be mistaken for release evidence.
    """
    return tuple(
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for row in rows if row["outcome"] != "lowered"
    )


def percentile_summary(values):
    """Nearest-rank percentiles for a finite collection of integer costs."""
    ordered = sorted(int(value) for value in values)
    if not ordered:
        return None

    def at(percent):
        # Nearest rank: ceil(percent * n / 100), converted to a zero-based
        # index.  Integer arithmetic keeps the recorded summary deterministic.
        index = max(0, (percent * len(ordered) + 99) // 100 - 1)
        return ordered[index]

    return {"count": len(ordered), "p50": at(50), "p90": at(90),
            "p95": at(95), "p99": at(99), "max": ordered[-1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workers", type=int, default=4,
        help="parallel tasks (default: 4, matching frontend construction "
             "capacity; larger values also measure contention)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S,
                        help="per-task limit in seconds; exceeding it is the typed "
                             "refusal analysis-timeout")
    parser.add_argument("--match", action="append", default=[],
                        help="only tasks whose path contains this. Repeatable.")
    parser.add_argument("--write", action="store_true",
                        help="write the dated record under tools/census/. "
                             "Without it this measures and prints only.")
    parser.add_argument(
        "--list-nonlowered", action="store_true",
        help="print one deterministic JSON line for every refusal, timeout or crash")
    args = parser.parse_args()
    if args.write and args.match:
        parser.error("--write is the canonical population record and --match is "
                     "a subset; a targeted run prints, it does not write history")
    # `signal.alarm(-1)` is ~2**32-1 seconds and `alarm(0)` disables the deadline
    # entirely, so a nonsense number would not fail loudly -- it would quietly
    # unmeasure the limit (review 15).
    if args.timeout < 1:
        parser.error(f"--timeout must be at least 1 second, got {args.timeout}")
    if args.workers < 1:
        parser.error(f"--workers must be at least 1, got {args.workers}")

    from behave.core.paths import problems_root

    paths = sorted(str(p) for p in problems_root().rglob("*/behavior.py"))
    if args.match:
        paths = [p for p in paths if any(m in p for m in args.match)]
    commit, commit_full, dirty, detached = _commit()
    if args.write and (dirty or not detached):
        raise SystemExit(
            "[census] refusing --write outside a clean detached worktree: the "
            "record binds one commit, so neither uncommitted edits nor a moving "
            "branch may define the measured code")

    # **The snapshot the rows are about, frozen before anything runs.** The digest
    # used to be computed after the last lowering finished, so a dataset edited
    # during the run produced rows over a mixture of versions under a header that
    # bound only the final bytes. The parent reads every model once, digests that,
    # the workers lower exactly these strings, and the tree and dataset are
    # checked again before a record is written.
    frozen = {str(Path(p).resolve()): Path(p).read_text() for p in paths}
    dataset = dataset_digest(paths, contents=frozen)
    print(f"[census] {len(paths)} models at {commit}{' (dirty)' if dirty else ''}, "
          f"dataset {dataset}, {args.workers} workers, {args.timeout}s per task",
          flush=True)

    import functools
    import multiprocessing as mp

    rows, done, began = [], 0, time.monotonic()
    pairs = [(p, frozen[str(Path(p).resolve())]) for p in paths]
    with mp.Pool(args.workers) as pool:
        for row in pool.imap_unordered(
                functools.partial(_pair_row, timeout_s=args.timeout), pairs):
            rows.append(row)
            done += 1
            if done % 50 == 0 or done == len(paths):
                print(f"[census] {done}/{len(paths)} "
                      f"({time.monotonic() - began:.0f}s)", flush=True)
    validate_rows(rows, paths)
    # A short task name is not globally unique across suites. Completion order from
    # ``imap_unordered`` must not decide the bytes of a census with two such names.
    rows.sort(key=lambda row: (row["path"], row["task"]))

    outcomes, codes, examples, calls = summarize(rows)
    sizes = sorted(r["ir_commands"] for r in rows if r["ir_commands"] is not None)
    representation = percentile_summary(
        r["representation_cost"] for r in rows
        if r["representation_cost"] is not None)
    print(f"[census] outcomes: {dict(sorted(outcomes.items()))}", flush=True)
    if sizes:
        print(f"[census] IR commands: median {sizes[len(sizes) // 2]}, "
              f"max {sizes[-1]}", flush=True)
    if representation is not None:
        print("[census] representation cost: "
              + ", ".join(f"{key} {value}"
                           for key, value in representation.items()), flush=True)
    for code, n in codes.most_common():
        print(f"{n:5d}  {code}   e.g. {examples[code]}", flush=True)
    # A crash is the census tool/front-end failing, not a supported refusal.
    # Always name every affected task: one example per grouped code is useful for
    # language work, but is not enough to repair an untyped implementation defect.
    for row in rows:
        if row["outcome"] == "crashed":
            print(
                f"[census] crash {row['path']}: {row['code']}: "
                f"{row['what']} (stage={row['stage'] or 'unknown'})",
                flush=True,
            )
    if calls:
        print("[census] call-unlowered by callee: "
              + ", ".join(f"{what.removeprefix('call to ')} {n}"
                          for what, n in calls.most_common()), flush=True)
    if args.list_nonlowered:
        for line in nonlowered_diagnostics(rows):
            print(f"[census-row] {line}", flush=True)

    if args.write:
        # The tree and the dataset must still be what the run started from: rows
        # over bytes the header does not bind are not a record of anything.
        commit_after, full_after, dirty_after, detached_after = _commit()
        if (commit_after, full_after, dirty_after, detached_after) != \
                (commit, commit_full, False, True):
            raise SystemExit(
                f"[census] refusing to write: the tree changed during the run "
                f"({commit_full}/clean -> {full_after}/"
                f"{'dirty' if dirty_after else 'clean'}/"
                f"{'detached' if detached_after else 'attached'})")
        from behave.core.paths import problems_root as _root

        paths_after = sorted(str(p) for p in _root().rglob("*/behavior.py"))
        if paths_after != paths:
            gained = sorted(set(paths_after) - set(paths))[:4]
            lost = sorted(set(paths) - set(paths_after))[:4]
            raise SystemExit(
                f"[census] refusing to write: the task set changed during the "
                f"run (+{len(set(paths_after) - set(paths))} "
                f"-{len(set(paths) - set(paths_after))}; e.g. {gained + lost}) "
                f"-- the rows are not a census of the population that now "
                f"exists")
        if dataset_digest(paths) != dataset:
            raise SystemExit(
                "[census] refusing to write: the dataset changed during the "
                "run, so the rows describe bytes the header would not bind")
        import uuid as _uuid

        # The invocation id: second-resolution timestamps collide (two runs of
        # one configuration finishing in the same second got one path), so the
        # name carries entropy no two invocations share -- and the **full** id
        # is in the document, because a truncation in a filename is a
        # convenience, not an identity (review 16).
        invocation = _uuid.uuid4().hex
        stamp = f"{time.strftime('%Y%m%dT%H%M%S')}-{invocation[:6]}"
        out = record_path(commit, stamp, dataset, args.timeout, args.match,
                          args.workers)
        document = {
            "record_schema": RECORD_SCHEMA,
            "kind": "lowering-census",
            "commit": commit, "commit_full": commit_full, "dirty": dirty,
            "detached": detached,
            "invocation": invocation,
            "date": time.strftime("%Y-%m-%d"),
            "entry": "lower_with_state_ranges", "schema": None,
            "manifest_scope": (
                "not applicable: this census asks only whether raw behavior.py "
                "lowers and does not admit a task or run policy"),
            # The lowering is deterministic over the source bytes: no RNG is
            # seeded or consumed on this path, so there is no seed to record --
            # stated rather than omitted, so a reader can tell "unseeded by
            # construction" from "seed lost".
            "seed": None,
            "python": sys.version.split()[0],
            # What this census deliberately does not cover: nothing -- a formal
            # record is the whole population or it is not written (--match
            # refuses --write). Present so an exclusion could never be silent.
            "exclusions": [],
            "timeout_s": args.timeout,
            "workers": args.workers,
            "dataset_digest": dataset,
            "configuration_digest": record_configuration(
                dataset, args.timeout, args.match, args.workers),
            "total": len(rows),
            "outcomes": dict(sorted(outcomes.items())),
            "representation_cost_summary": representation,
            "rows": rows,
        }
        write_record(out, document)
        print(f"[census] wrote {out}", flush=True)


if __name__ == "__main__":
    main()
