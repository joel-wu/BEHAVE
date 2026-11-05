"""Read-only experiment summaries; never invoke a model, simulator or EDA tool."""

from behave.core.artifacts import BlobRef, read_verified
from behave.core.records import parse_json

from .records import comparison_cost, finalized_report, read_document
from .records import search_cost as _search_cost


def frontier(candidates):
    # Old archives measured the mapped image and keep that admission rule.
    # New archives explicitly identify their source-RTL workload measurement.
    feasible = [row for row in candidates if row.get("score") == 1
                and (row.get("performance", {}).get("source") == "rtl" or row.get("mapped_score") == 1)
                and row.get("performance", {}).get("score") == 1
                and row.get("performance", {}).get("time_ns") is not None
                and row.get("eda", {}).get("status") == "measured"
                and (row["eda"].get("timing") or {}).get("status") == "met"]
    def point(row):
        return row["eda"]["area_um2"], row["performance"]["time_ns"]

    return [row["candidate"] for row in feasible if not any(
        all(x <= y for x, y in zip(point(other), point(row))) and point(other) != point(row)
        for other in feasible)]


def target_results(candidates, targets):
    rows = tuple(candidates)
    admitted = set(frontier(rows))
    feasible = [row for row in rows if row["candidate"] in admitted]
    result = []
    for bound in targets:
        eligible = [row for row in feasible if row["performance"]["time_ns"] <= bound]
        best = min(eligible, key=lambda row: (row["eda"]["area_um2"], row["performance"]["time_ns"],
                                            row["candidate"]), default=None)
        result.append({"bound_ns": bound, "candidate": None if best is None else best["candidate"],
                       "area_um2": None if best is None else best["eda"]["area_um2"],
                       "time_ns": None if best is None else best["performance"]["time_ns"],
                       "power": None if best is None else best["eda"].get("power")})
    return result


def summary(directory):
    run, identity, result = finalized_report(directory)
    if result["schema"] == "behave.ppa-search/2":
        config = parse_json(read_verified(run.objects, BlobRef.from_dict(result["configuration"])))
        experiment = config["experiment"]
        candidates = {row["candidate"]: row for row in result["candidates"]}
        members = {"search": list(candidates)}
        cost = _search_cost(result)
        awaiting = result.get("evaluation_status") == "awaiting_final"
    elif result["schema"] == "behave.ppa-comparison/2":
        manifest = read_document(run.objects, BlobRef.from_dict(result["manifest"]))
        experiment = manifest["experiment"]
        candidates, members = result["candidates"], result["members"]
        cost = comparison_cost(run.objects, result, manifest)
        awaiting = False
    else:
        raise ValueError("unsupported completed PPA report")
    return {"schema": "behave.ppa-report/2", "result": identity.as_dict(),
            "evaluation_status": "awaiting_final" if awaiting else "completed",
            "stage": experiment["stage"], "cost": cost,
            "methods": {label: {"candidates": len(keys),
                        "frontier": [] if awaiting else frontier(candidates[k] for k in keys),
                        "targets": target_results(() if awaiting else (candidates[k] for k in keys), experiment["targets_ns"])}
                        for label, keys in members.items()},
            "points": [{"candidate": key, "source_score": row.get("score"),
                        **({"mapped_score": row.get("mapped_score")}
                           if row.get("performance", {}).get("source") != "rtl" else {}),
                        "area_um2": row.get("eda", {}).get("area_um2"),
                        "timing": row.get("eda", {}).get("timing"), "performance": row.get("performance"),
                        "power": row.get("eda", {}).get("power")}
                       for key, row in sorted(candidates.items())]}
