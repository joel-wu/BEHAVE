"""Read-only campaign aggregation and optional figures over retained results.

No model, simulator, job launcher or spending settlement runs here. Numerical
admission reuses the production frontier predicate; plotting imports Matplotlib
only when requested and preserves the recorded experiment and measurements.
"""

import json
import socket
import time
from pathlib import Path

from .planning import campaign_slots as slots


def collect_final(plan, root, outcomes, pool):
    """Verify the common-suite binding before admitting any point to the plot."""
    from behave.core.artifacts import BlobRef
    from behave.core.records import canonical_json
    from behave.ppa.records import comparison_cost, finalized_report, read_document, union_pool
    from behave.ppa.report import frontier

    points, rows_by_method, costs = [], {m["name"]: {} for m in plan["methods"]}, {}
    experiment = json.loads(Path(plan.get("final_experiment") or plan["experiment"]).read_text())
    native = None
    for outcome in outcomes:
        if not outcome["ok"]:
            continue
        name, replica = outcome["label"].split("/")
        directory = root / "runs" / name / replica / "final"
        run, _, result = finalized_report(directory)
        manifest = read_document(run.objects, BlobRef.from_dict(result["manifest"]))
        if union_pool(read_document(run.objects, BlobRef.from_dict(manifest["pool_ref"]))) != union_pool(pool):
            raise ValueError("final shards do not share one correctness suite")
        if canonical_json(manifest["experiment"]) != canonical_json(experiment):
            raise ValueError("final shards use a different measurement experiment")
        if native is not None and manifest["native"] != native:
            raise ValueError("final shards use different EDA/evaluator builds")
        native = manifest["native"]
        costs[outcome["label"]] = comparison_cost(run.objects, result, manifest)
        for key, row in result["candidates"].items():
            # Reuse the production admission predicate, not a plot-only approximation.
            admitted = bool(frontier((row,)))
            points.append({"method": name, "replica": replica, "candidate": key,
                "admitted": admitted, "source_score": row.get("score"),
                **({"mapped_score": row.get("mapped_score")}
                   if row.get("performance", {}).get("source") != "rtl" else {}),
                "area_um2": row.get("eda", {}).get("area_um2"),
                "timing": row.get("eda", {}).get("timing"),
                "power": row.get("eda", {}).get("power"), "performance": row.get("performance")})
            # Keep repeat measurements distinct. No favorable overwrite of identical RTL.
            identity = f"{replica}:{key}"
            rows_by_method[name][identity] = {**row, "candidate": identity}
    return {"schema": "behave.ppa-campaign-report/1", "plan": plan, "experiment": experiment, "outcomes": outcomes,
            "common_pool": BlobRef.of(canonical_json(list(pool))).as_dict(),
            "points": points, "costs": costs,
            "frontiers": {name: frontier(rows.values()) for name, rows in rows_by_method.items()}}


def plot(report, output, *, labels=None, title=None):
    """One measured area/time figure. Failed and missing runs remain in its counts."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import LogLocator, ScalarFormatter

    plan = report["plan"]
    experiment = report.get("experiment")
    if experiment is None:
        experiment = json.loads(Path(plan["experiment"]).read_text())
    figure, axes = plt.subplots(figsize=(7.2, 4.8), layout="constrained")
    try:
        colors = ("#639DEA", "#BAADE8", "#95D8C6", "#EEAD79")
        markers = ("o", "s", "^", "D")
        labels = labels or {}
        for index, method in enumerate(plan["methods"]):
            color = colors[index % len(colors)]
            marker = markers[index % len(markers)]
            name = method["name"]
            admitted = [p for p in report["points"] if p["method"] == name and p["admitted"]]
            complete = sum(o["ok"] and o["label"].startswith(name + "/") for o in report["outcomes"])
            label = f"{labels.get(name, name)} ({complete}/{plan['replicates']} final runs)"
            axes.scatter([p["area_um2"] / 1000 for p in admitted],
                         [p["performance"]["time_ns"] / 1000 for p in admitted],
                         color=color, marker=marker, alpha=.65, s=38,
                         edgecolor="white", linewidth=.6, zorder=3)
            front = sorted((p for p in admitted
                            if f"{p['replica']}:{p['candidate']}" in report["frontiers"][name]),
                           key=lambda p: (p["area_um2"], p["performance"]["time_ns"]))
            axes.plot([p["area_um2"] / 1000 for p in front], [p["performance"]["time_ns"] / 1000 for p in front],
                      color=color, marker=marker, markersize=7, markeredgecolor="black",
                      markeredgewidth=.6, linewidth=1.8, linestyle="--", label=label, zorder=4)
        period = experiment["timing"]["period_ns"]
        technology = experiment.get("mapping", {}).get("technology", "sky130hd")
        technology_label = {"sky130hd": "SKY130 HD", "nangate45": "Nangate45"}.get(technology, technology)
        times = [p["performance"]["time_ns"] / 1000 for p in report["points"] if p["admitted"]]
        if times:
            axes.set_yscale("log")
            axes.yaxis.set_major_locator(LogLocator(subs=(1, 2, 5)))
            axes.yaxis.set_major_formatter(ScalarFormatter())
            axes.set_ylim(min(times) / 1.3, max(times) * 1.3)
        axes.set(xlabel="Mapped cell area (10³ µm²) ↓",
                 ylabel="Workload completion time (µs, log scale) ↓",
                 title=f"{title or plan['task']}\n{technology_label} · {1000 / period:g} MHz · "
                      + ('placement-RC STA' if experiment.get('mapping', {}).get('placement_repair') else 'ideal-interconnect STA'))
        axes.grid(which="major", color="#DEDEDE", linewidth=.65)
        axes.set_axisbelow(True)
        for spine in axes.spines.values():
            spine.set_color("black")
            spine.set_linewidth(.8)
        axes.tick_params(which="both", direction="out")
        axes.legend(frameon=False, fontsize=9)
        figure.supxlabel("Validated measured designs; dashed lines link each model's nondominated points.",
                         fontsize=8)
        output.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("png", "pdf"):
            figure.savefig(output.with_suffix("." + suffix), dpi=200)
    finally:
        plt.close(figure)


def status(root):
    """Read local/archived evidence without dispatching, settling spend or judging.

    An unsettled event is not proof its process is alive. Runtime host placement
    determines whether a node-local path can be read; another host's /tmp is not
    a fallback. Service receipts, when present, retain exit/cleanup attribution.
    """
    from collections import Counter

    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef
    from behave.core.records import thaw_json
    from behave.ppa.records import read_document

    def read(path):
        return json.loads(path.read_text()) if path.is_file() else None

    root = Path(root).resolve()
    if not root.is_dir():
        return {"directory": str(root), "state": "not-created", "execution_visible": False}
    runtime = read(root / 'runtime.json') or {}
    state = runtime.get('state')
    job_receipt = None
    launch = read(root / 'supervision/launch.json') or {}
    job_key = launch.get('metadata', {}).get('job_key')
    if job_key and (root / 'job/job').is_dir():
        from behave.agent.jobs import JobControl

        job_receipt = JobControl(root / 'job').inspect(job_key).as_dict()
        result = job_receipt['result']
        if result is not None:
            # A killed worker cannot update runtime.json in its finally block.
            # Retain that historical placement record; use the supervisor receipt.
            if result['status'] != 'exited':
                state = result['status']
            elif result['returncode'] != 0:
                state = 'failed'
    source, view = root, 'direct'
    if runtime.get('storage') == 'shared' and (root / 'execution').is_dir():
        source, view = root / 'execution', 'shared'
    elif (runtime.get('host') == socket.gethostname() and runtime.get('local_directory')
            and Path(runtime['local_directory']).is_dir()):
        source, view = Path(runtime['local_directory']), 'node-local'
    elif (root / 'execution').is_dir():
        source, view = root / 'execution', 'archived-copy'
    elif runtime.get('local_directory'):
        return {"directory": str(root), "state": state, "runtime": runtime, "job": job_receipt,
                "execution_visible": False, "query_on_host": runtime.get('host')}

    def events(directory, phase):
        if not (directory / phase).is_dir():
            return ()
        return JournalDirectory(directory).journal(phase).load(verify_artifacts=False)

    def measurements(directory):
        rows = events(directory, 'measurements')
        settled = next((e for e in reversed(rows) if e.kind == 'settled'), None)
        candidates = {}
        if settled is not None:
            record = read_document(JournalDirectory(directory).objects,
                                   BlobRef.from_dict(thaw_json(settled.payload['record'])))
            candidates = record['candidates']
        final = []
        for candidate in candidates.values():
            result = next((h for h in reversed(candidate.get('history', []))
                           if h['mode'] == 'final' and h['final']), None)
            if result is not None:
                final.append(result['result'].get('score'))
        return {'candidates_known': len(candidates),
                'requested': sum(e.kind == 'requested' for e in rows),
                'final_attempts_completed': len(final),
                'final_pass': sum(v == 1 for v in final),
                'final_fail': sum(v is not None and v != 1 for v in final),
                'final_no_conclusion': sum(v is None for v in final),
                'unsettled_measurement': bool(rows and rows[-1].kind == 'requested')}

    def attempt(directory):
        search = directory / 'search'
        recovery = read(directory / 'recovery.json')
        if recovery and recovery.get('action') == 'reuse':
            search = Path(recovery['previous']) / 'search'
        rows = events(search, 'agent')
        counts = Counter(e.kind for e in rows)
        terminal = next((e for e in reversed(rows) if e.kind == 'terminal'), None)
        last = rows[-1] if rows else None
        waiting = ('model' if last and last.kind == 'model_requested' else
                   'tool' if last and last.kind == 'tool_requested' else None)
        workflow = events(search, 'workflow')
        final = events(directory / 'final', 'workflow')
        return {'label': directory.parent.name + '/' + directory.name,
                'model_requests': counts['model_requested'],
                'model_replies': sum(e.kind == 'model_settled' and e.payload.get('outcome') == 'reply' for e in rows),
                'tool_calls': counts['tool_requested'], 'awaiting': waiting,
                'tool': last.payload['call']['name'] if waiting == 'tool' else None,
                'terminal_reason': terminal.payload.get('reason') if terminal else None,
                'search_complete': bool(workflow and workflow[-1].kind == 'finished'),
                'final_complete': bool(final and final[-1].kind == 'finished'),
                'search_measurements': measurements(search),
                'final_measurements': measurements(directory / 'final'),
                'search_directory': str(search), 'recovery': recovery,
                'search_service': read(search.parent / 'search-service/service.json'),
                'final_service': read(directory / 'final-service/service.json')}

    roots = sorted((source / 'campaigns').iterdir()) if (source / 'campaigns').is_dir() else [source]
    tasks = {}
    for task_root in roots:
        if not task_root.is_dir():
            continue
        plan = read(task_root / 'plan.json')
        directories = ([item[2] for item in slots(plan, task_root)] if plan else
                       sorted(task_root.glob('runs/*/r*')))
        attempts = []
        for directory in directories:
            try:
                attempts.append(attempt(directory))
            except (OSError, ValueError, KeyError) as error:
                attempts.append({'label': directory.parent.name + '/' + directory.name,
                                 'read_error': f'{type(error).__name__}: {error}'})
        tasks[task_root.name] = {'task': None if plan is None else plan['task'],
            'planned': None if plan is None else len(directories),
            'search_complete': sum(row.get('search_complete', False) for row in attempts),
            'final_complete': sum(row.get('final_complete', False) for row in attempts),
            'read_errors': sum('read_error' in row for row in attempts),
            'final_candidates': {key: sum(row.get('final_measurements', {}).get(key, 0) for row in attempts)
                                 for key in ('candidates_known', 'final_attempts_completed',
                                             'final_pass', 'final_fail', 'final_no_conclusion')},
            'attempts': attempts}
    return {'directory': str(root), 'source': str(source), 'view': view,
            'state': state, 'runtime': runtime or None, 'job': job_receipt,
            'execution_visible': True, 'sampled_at': time.time(), 'tasks': tasks}
