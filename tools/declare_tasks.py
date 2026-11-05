#!/usr/bin/env python3
"""Derive ``task.toml`` from Behavior and optionally check the reference RTL.

The explicit interface block supplies physical endpoint bindings.  ``behavior.py``
supplies transaction boundaries and ordered output streams.  ``rtl.sv`` may be checked
as one realization of the resulting contract, but its result never creates, changes, or
removes the contract.  Fixed request/output ratios are not inferred: Behavior may return
no transaction, one mapping, or a list of mappings on each call.

Execution policy belongs to Sim runtime, not generated task files.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from behave.core.paths import problems_root
from behave.sim.contract_of import contract_of_spec
from behave.sim.declarations import Declarations, declared_contract
from behave.sim.reference_rules import (
    GENERATOR,
    behavior_digest,
    derive,
    interface_digest,
)

#: A diagnostic threshold, not a per-item deadline.
PER_ITEM_S = 60.0

#: The header the first generator wrote, which had no `[generated]` block. Recognised so
#: its files can be told from hand-written ones and revoked.
_V1_HEADER = "# Written by tools/declare_tasks.py"


def _toml_key(value):
    """A table-key spelling valid for arbitrary endpoint names."""

    bare = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    return value if value and all(character in bare for character in value) \
        else json.dumps(value)


def _endpoint_declarations(fields):
    out = {}
    for flat, value in fields.items():
        if not flat.startswith("endpoint.") or "." not in flat[9:]:
            raise ValueError(f"unknown derived field {flat}")
        endpoint, name = flat[9:].rsplit(".", 1)
        out.setdefault(endpoint, {})[name] = value
    return out


def declaration_for(problem):
    """Return `(task_toml, [])`, or `(None, [why...])`.

    The semantic derivation is static and independent of every candidate realization.
    """
    spec = (problem / "spec.md").read_text()
    behavior = (problem / "behavior.py").read_text()
    try:
        contract = contract_of_spec(spec, problem.name)
    except Exception as exc:                                   # noqa: BLE001
        return None, [f"contract-unreadable: {type(exc).__name__}"]

    fields, evidence, missing = derive(
        behavior, contract, file=str(problem / "behavior.py"))
    if missing:
        return None, missing

    endpoints = _endpoint_declarations(fields)
    try:
        declared_contract(contract, Declarations(endpoints=endpoints))
    except Exception as exc:                                   # noqa: BLE001
        kind = getattr(getattr(exc, "refusal", None), "kind", type(exc).__name__)
        return None, [f"derived-contract-invalid: {kind}"]

    lines = [
        "# Derived from behavior.py against the task's explicit interface.",
        "# The checked reference RTL is one realization, not a declaration source.",
        "",
        "[generated]",
        f"generator = {GENERATOR}",
        f'behavior_digest = "{behavior_digest(behavior)}"',
        f'interface_digest = "{interface_digest(contract)}"',
        "",
    ]
    for endpoint, block in sorted(endpoints.items()):
        lines.extend([
            f"[endpoint.{_toml_key(endpoint)}]",
            f"# Bound by behavior.py::{evidence[endpoint]}.",
        ])
        for name, value in sorted(block.items()):
            lines.append(f"{name} = {json.dumps(value)}")
        lines.append("")
    task = "\n".join(lines)

    return task, []


def validate_reference(problem, task):
    """Validate with the same public runtime baseline as corpus qualification."""
    from tools.qualify_dataset_baseline import worker

    with tempfile.TemporaryDirectory(prefix="behave-declare-") as root:
        checkout = Path(root)
        staged = checkout / problem.name
        shutil.copytree(problem, staged, ignore=shutil.ignore_patterns(
            "obj*", "runs", "run.toml", "authority.json", "cosim_report.json", "latest.json"))
        (staged / "task.toml").write_text(task)
        result = worker(checkout, checkout / "validation",
                        {"task": problem.name, "missing": []})
        return result["status"] == "scored-pass", result["status"]


def _derive_one(job):
    """Derive and optionally validate one task in a worker process."""

    problem_text, options = job
    problem = Path(problem_text)
    started = time.monotonic()
    validation = None
    try:
        task, missing = declaration_for(problem)
        if task is not None and options["validate_reference"]:
            _aligned, why = validate_reference(problem, task)
            validation = why
    except Exception as exc:                                   # noqa: BLE001
        task, missing = None, [f"crashed: {type(exc).__name__}: {exc}"]
    return (problem_text, task, missing, time.monotonic() - started,
            validation)


def _generated_block(text):
    """The `[generated]` fields of an existing file, or `{}`."""
    import tomllib

    try:
        return dict(tomllib.loads(text).get("generated") or {})
    except Exception:                                          # noqa: BLE001
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--match", action="append", default=[],
                        help="only problems whose path contains this. Repeatable.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--write", action="store_true",
                        help="write derived task files. Reference validation is "
                             "an optional, independent realization check.")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace an existing generated file. A file this generator "
                             "did not write is never replaced.")
    parser.add_argument("--revoke", action="store_true",
                        help="delete a generated file this rule would no longer write. "
                             "For a rule that got stricter: the files it produced under "
                             "the old one are still on disk and still being read.")
    parser.add_argument("--check", action="store_true",
                        help="verify each generated file against the specification "
                             "beside it and the rule that produced it, and exit non-zero "
                             "where any disagree. Writes nothing.")
    parser.add_argument("--validate-reference", action="store_true",
                        help="also run Behavior against the trusted reference RTL; a "
                             "failed check is reported but cannot suppress the task")
    parser.add_argument("--workers", type=int, default=1,
                        help="tasks derived concurrently. Keep this small with "
                             "--validate-reference because each task starts EDA tools")
    args = parser.parse_args()

    if args.write and args.check:
        parser.error("--write and --check are mutually exclusive")
    if (args.overwrite or args.revoke) and not args.write:
        parser.error("--overwrite and --revoke require --write")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.workers < 1:
        parser.error("--workers must be positive")

    problems = sorted(p.parent for p in problems_root().rglob("*/behavior.py")
                      if (p.parent / "rtl.sv").is_file()
                      and (p.parent / "spec.md").is_file())
    if args.match:
        problems = [p for p in problems if any(m in str(p) for m in args.match)]
    if args.limit:
        problems = problems[:args.limit]

    mode = ("check" if args.check else "write" if args.write else "report")
    print(f"[declare] {len(problems)} problems; generator={GENERATOR} "
          f"mode={mode}", flush=True)

    options = {
        "validate_reference": args.validate_reference,
    }
    jobs = [(str(problem), options) for problem in problems]
    if args.workers == 1:
        results = map(_derive_one, jobs)
    else:
        pool = concurrent.futures.ProcessPoolExecutor(max_workers=args.workers)
        results = pool.map(_derive_one, jobs, chunksize=1)

    written, skipped, revoked, refused, stale = [], [], [], {}, []
    validations = {}
    slow = []
    for index, result in enumerate(results, 1):
        problem_text, task, missing, elapsed, validation = result
        problem = Path(problem_text)
        if validation is not None:
            validations[validation] = validations.get(validation, 0) + 1
        if elapsed > PER_ITEM_S:
            slow.append((problem.name, round(elapsed, 1)))

        where = problem / "task.toml"
        existing = where.read_text() if where.exists() else ""
        was = _generated_block(existing)
        generated_by_us = bool(was) or existing.startswith(_V1_HEADER)

        if args.check:
            if not existing:
                status = "no file" if task is None else "MISSING: this rule would write one"
                if task is not None:
                    stale.append((problem.name, "missing"))
            elif not generated_by_us:
                status = "hand-written, left alone"
            elif task is None:
                status = f"STALE: this rule would not write it ({missing[0]})"
                stale.append((problem.name, f"would-not-write: {missing[0]}"))
            elif existing != task:
                new = _generated_block(task)
                why = ("the Behavior or task interface changed"
                       if (was.get("behavior_digest"), was.get("interface_digest"))
                       != (new.get("behavior_digest"), new.get("interface_digest"))
                       else "the rule changed")
                status = f"STALE: {why}"
                stale.append((problem.name, why))
            else:
                status = "matches"
            if validation is not None:
                status += f" ({validation})"
            print(f"[declare] {index}/{len(problems)} {problem.name}: {status}",
                  flush=True)
            continue

        if task is None:
            refused[problem.name] = missing
            status = "REFUSED " + ", ".join(missing[:2])
            if existing and generated_by_us and args.revoke:
                if args.write:
                    where.unlink()
                revoked.append(problem.name)
                status += " (revoked the file the old rule wrote)"
        elif existing and not generated_by_us:
            skipped.append(problem.name)
            status = "hand-written, left alone"
        elif existing and not args.overwrite:
            skipped.append(problem.name)
            status = ("unchanged" if existing == task
                      else "generated files differ; use --overwrite to replace them")
        else:
            written.append(problem.name)
            status = "written" if args.write else "would write"
            if args.write:
                where.write_text(task)
        if validation is not None:
            status += f" ({validation})"
        print(f"[declare] {index}/{len(problems)} {problem.name}: {status}", flush=True)

    if args.workers != 1:
        pool.shutdown()

    if args.check:
        print(f"\n[declare] {len(stale)} of {len(problems)} disagree with the "
              f"reference derivation", flush=True)
        for row in stale[:20]:
            print(f"[declare]   {row[0]}: {row[1]}", flush=True)
        return 1 if stale else 0

    print(f"\n[declare] {'wrote' if args.write else 'would write'} {len(written)}, "
          f"kept {len(skipped)}, revoked {len(revoked)}, refused {len(refused)} "
          f"of {len(problems)}", flush=True)
    why = {}
    for names in refused.values():
        for name in names:
            why[name.split(":")[0]] = why.get(name.split(":")[0], 0) + 1
    for name, count in sorted(why.items(), key=lambda kv: -kv[1]):
        print(f"[declare]   {count:>4} missing {name}", flush=True)
    for name, count in sorted(validations.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"[declare]   {count:>4} validation {name}", flush=True)
    if slow:
        print(f"[declare] slower than {PER_ITEM_S:.0f}s: {slow[:5]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
