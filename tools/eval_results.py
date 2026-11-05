"""One catalog and trace entry point for formal runs, retries and judgments."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from behave.eval.generation import solver_cases, trace_name
from behave.eval.report import current_records, issue_records, read, source_records
from behave.runtime.records import write_json


def register(root, experiment, cohort, *, dataset=None):
    """Register explicit provenance, never scan work/ for convenient results."""
    root, experiment = Path(root).resolve(), Path(experiment).resolve()
    path = root / "catalog.json"
    catalog = read(path) if path.exists() else {"sources": []}
    known = {source["id"]: source for source in catalog["sources"]}

    def add(directory, model, label, previous=None, datasets=None, dataset=None, objects=None):
        directory, objects = Path(directory).resolve(), Path(objects).resolve()
        identifier = f"{cohort}/{model}/{label}"
        settings = read(directory / "settings.json")
        cases = solver_cases(settings)
        mixed = len({setting for _, setting in cases}) > 1
        source = {"id": identifier, "path": str(directory), "cohort": cohort, "model": model,
                  "retry_of": previous, "datasets": datasets or {}, "dataset": dataset,
                  "objects": str(objects), "judgments": ["baseline"],
                  "planned_cases": [{"task": task, "setting": setting, "run_key": None,
                                     "trace": trace_name(task, setting, mixed=mixed)} for task, setting in cases]}
        if identifier in known:
            old = known[identifier]
            if old["path"] != source["path"]:
                raise ValueError("catalog source name already identifies another run")
            source["judgments"] = old["judgments"]
            old.update(source)
        else:
            catalog["sources"].append(source)
            known[identifier] = source
        return identifier

    if (experiment / "campaign.json").exists():
        from behave.eval.report import rollout_sources

        manifest = read(experiment / "campaign.json")
        for run in manifest["runs"]:
            previous = None
            for number, directory in enumerate(rollout_sources(experiment / "runs" / run["name"])):
                objects = directory / "inputs" if (directory / "inputs").exists() else experiment / "release/objects"
                label = run.get("setting", "mixed")
                previous = add(directory, run["model"], f"{label}_attempt{number:02}", previous,
                               datasets=manifest.get("datasets"), dataset=dataset, objects=objects)
    else:
        previous_by_model, number = {}, 0
        current = experiment
        while True:
            manifest = read(current / "experiment.json")
            for model in (model for node in manifest["nodes"].values() for model in node["models"]):
                names = [model]
                replacement = manifest.get("replacement_runs", {}).get(model)
                if replacement:
                    names.append(replacement)
                for i, name in enumerate(names):
                    directory = current / "runs" / name
                    label = f"attempt{number:02}" + ("_replacement" if i else "")
                    previous_by_model[model] = add(directory, model, label, previous_by_model.get(model),
                        dataset=manifest.get("dataset", "authored_candidates50"), objects=current / "release/objects")
            following = current / "infrastructure_recovery.json"
            if not following.exists():
                break
            current = Path(read(following)["directory"])
            number += 1
    write_json(path, catalog)
    return catalog


def add_judgment(root, source, name):
    path = Path(root) / "catalog.json"
    catalog = read(path)
    selected = next(row for row in catalog["sources"] if row["id"] == source)
    if name not in selected["judgments"]:
        selected["judgments"].append(name)
    write_json(path, catalog)


def relocate(root, experiment, destination):
    """Move a closed experiment, retaining its old name for immutable records.

    The result store becomes the physical owner. Historical manifests and trace
    text keep their original bytes; their absolute paths follow a reverse link.
    This is a same-filesystem rename, not a copy of every trace/CAS object.
    """
    root = Path(root).resolve()
    experiment, destination = Path(experiment).absolute(), Path(destination).absolute()
    catalog = read(root / "catalog.json")
    if experiment.is_symlink() and experiment.resolve() == destination.resolve():
        return {"from": str(experiment), "to": str(destination), "state": "relocated"}
    affected = [s for s in catalog["sources"] if Path(s["path"]).is_relative_to(experiment)]
    if not affected:
        raise ValueError("experiment has no registered sources")
    for source in affected:
        if read(Path(source["path"]) / "runtime.json")["state"] != "archived":
            raise ValueError(f"source is not archived: {source['id']}")
    if destination.exists() or destination.is_symlink():
        raise ValueError("relocation destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if experiment.stat().st_dev != destination.parent.stat().st_dev:
        raise ValueError("relocation requires a same-filesystem rename")
    experiment.rename(destination)
    experiment.symlink_to(destination, target_is_directory=True)
    for source in catalog["sources"]:
        for key in ("path", "objects"):
            path = Path(source[key])
            if path.is_relative_to(experiment):
                source[key] = str(destination / path.relative_to(experiment))
    write_json(root / "catalog.json", catalog)
    entry = {"from": str(experiment), "to": str(destination), "state": "relocated"}
    path = root / "relocations.json"
    history = read(path) if path.exists() else []
    write_json(path, [*history, entry])
    return entry


def link(path, target):
    target = Path(target).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        if path.readlink() != target:
            raise ValueError(f"existing catalog link names another archive: {path}")
    elif path.exists():
        raise ValueError(f"catalog does not replace existing files: {path}")
    else:
        path.symlink_to(target, target_is_directory=True)


def write_csv(path, rows, fields):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), (dict, list))
                             else row.get(key) for key in fields})


def refresh(root):
    root = Path(root).resolve()
    catalog = read(root / "catalog.json")
    records = []
    for source in catalog["sources"]:
        print(f"Indexing {source['id']}", flush=True)
        records.extend(source_records(source))
        link(root / "archives" / source["id"], source["path"])
    current = current_records(records, catalog["sources"])
    for row in records:
        original = Path(row["trace"]).parent
        if original.exists():
            label = row["source_id"].split("/")[-1]
            target = root / "traces" / row["cohort"] / row["model"] / row["setting"] / row["task"] / label
            link(target, original)
            row["catalog_trace"] = str(target / Path(row["trace"]).name)
    issues = issue_records(current)
    write_json(root / "attempts.json", records)
    write_json(root / "cases.json", current)
    write_json(root / "sim_issues.json", issues)
    fields = ("cohort", "dataset", "model", "model_id", "setting", "task", "sample", "source_id", "revision",
              "rollout_status", "turns", "rtl_status", "rtl_score", "behavior_status", "behavior_score",
              "judgment", "failure_code", "trace", "catalog_trace", "source_run", "run_key", "billing")
    flat = [{**row, **{f"{artifact}_{key}": row[artifact][key] for artifact in ("rtl", "behavior")
                      for key in ("status", "score")}} for row in current]
    write_csv(root / "cases.csv", flat, fields)
    rollout_issues = [row for row in flat if row["rollout_status"] in {"interrupted", "rollout_unavailable"}]
    write_csv(root / "rollout_issues.csv", rollout_issues, ("cohort", "dataset", "model", "setting", "task",
              "rollout_status", "failure_code", "trace", "source_run", "run_key"))
    write_csv(root / "sim_issues.csv", issues, ("cohort", "dataset", "model", "setting", "task", "artifact",
              "status", "origin", "code", "reason", "judgment", "trace", "source_run", "run_key", "result"))
    groups = {}
    for row in issues:
        groups.setdefault(row.get("code", "unknown"), []).append(row)
    lines = ["# Unresolved evaluator outcomes", "", f"Updated {datetime.now(UTC).isoformat()}", "",
             "Only explicitly registered, currently selected attempts are included. Candidate failures and",
             "missing submissions are not Sim bugs. Live archives are marked pending in cases.csv.", "",
             "| Code | Artifact records | Distinct tasks |", "| --- | ---: | ---: |"]
    for code, group in sorted(groups.items(), key=lambda pair: -len(pair[1])):
        lines.append(f"| `{code}` | {len(group)} | {len({r['task'] for r in group})} |")
    lines += ["", "See sim_issues.csv for every task, artifact, original trace, result reference and reason.",
              "Named rejudgments are retained in attempts.json; repaired scores do not rewrite old records.", ""]
    (root / "SIM_ISSUES.md").write_text("\n".join(lines))
    summary = {"updated_at": datetime.now(UTC).isoformat(), "sources": len(catalog["sources"]),
               "attempts": len(records), "cases": len(current), "unresolved_artifact_records": len(issues),
               "unresolved_distinct_tasks": len({r["task"] for r in issues}),
               "rollout_unavailable": len(rollout_issues),
               "issue_codes": dict(Counter(r.get("code", "unknown") for r in issues))}
    write_json(root / "summary.json", summary)
    return summary


def finalize(root, controls, ledger, allocation):
    """Wait for explicit jobs, archive their shared ledger once, then refresh."""
    from behave.runtime.jobs import wait_control

    root = Path(root).resolve()
    for control in controls:
        wait_control(Path(control), allow_failure=True)
    destination = root / "billing" / Path(ledger).parent.name
    code = ("import sys;from pathlib import Path;from behave.runtime.records import archive_spending;"
            "from behave.runtime.records import write_json;"
            "from behave.agent.spending import spending_snapshot;"
            "source,target=map(Path,sys.argv[1:]);archive_spending(source,target);"
            "write_json(target/'summary.json',spending_snapshot(source))")
    subprocess.run(["srun", f"--jobid={allocation}", "--overlap", "--exact", "--nodes=1", "--ntasks=1",
                    "--cpus-per-task=1", "--gres=none", sys.executable, "-c", code,
                    str(ledger), str(destination)], check=True)
    return refresh(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "refresh", "judgment", "finalize", "relocate"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--experiment", type=Path)
    parser.add_argument("--cohort")
    parser.add_argument("--dataset", help="explicit dataset label for a campaign without per-task dataset metadata")
    parser.add_argument("--source")
    parser.add_argument("--name")
    parser.add_argument("--control", type=Path, action="append")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--allocation")
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if args.action == "register":
        if args.experiment is None or args.cohort is None:
            parser.error("register needs --experiment and --cohort")
        register(args.root, args.experiment, args.cohort, dataset=args.dataset)
    elif args.action == "relocate":
        if args.experiment is None or args.destination is None:
            parser.error("relocate needs --experiment and --destination")
        print(json.dumps(relocate(args.root, args.experiment, args.destination), indent=2))
    elif args.action == "judgment":
        if args.source is None or args.name is None:
            parser.error("judgment needs --source and --name")
        add_judgment(args.root, args.source, args.name)
    elif args.action == "finalize":
        if not args.control or args.ledger is None or args.allocation is None:
            parser.error("finalize needs --control, --ledger and --allocation")
        print(json.dumps(finalize(args.root, args.control, args.ledger, args.allocation), indent=2))
    else:
        print(json.dumps(refresh(args.root), indent=2))


if __name__ == "__main__":
    main()
