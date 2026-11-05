"""Compatibility entry for the shared Eval fleet controller.

Keep the historical checkout working directory and frozen worker bootstraps.
Scheduling, preparation, explicit recovery and CPU scoring live in the package.
The exact historical decoder diagnostic remains an operator-selected check;
importing this file does not start or replace any worker.
"""

from pathlib import Path

from behave.eval import checkpoint
from behave.eval import fleet as _controller
from behave.eval.fleet import (  # noqa: F401 -- historical operator exports
    ACTIVE,
    TERMINAL,
    assignment_directory,
    begin_preparation,
    continue_preparation,
    generation_blocker,
    generation_slot,
    measurement_config,
    observe_control,
    publish_score_readiness,
    reconcile_assignments,
    record_assignment,
    reload_fleet,
    resumable_preparation,
    terminal,
    verify_port_preflight,
    verify_unstarted,
)


def start_measurement(root, item, slot, handoff):
    return _controller.start_measurement(root, item, slot, handoff, cwd=Path(__file__).resolve().parents[1])


def start_scores(root, allocation):
    return _controller.start_scores(root, allocation, cwd=Path(__file__).resolve().parents[1])


def verify_recovery_preflight(directory, control):
    """An exact decoder failure before any new batch was created, not a reroll."""

    directory, control = Path(directory), Path(control)
    if observe_control(control).get("state") != "failed":
        raise ValueError("recovery-preflight owner must have exited with failure")
    generation = checkpoint.read(directory / "generation.json")
    cleanup = checkpoint.read(directory / "rollout/cleanup.json")
    if (generation.get("state") != "interrupted"
            or generation.get("cleanup_complete") is not True
            or cleanup.get("cleanup_complete") is not True
            or not cleanup.get("services")
            or any(s.get("cleanup_complete") is not True for s in cleanup["services"])):
        raise ValueError("recovery-preflight cleanup is not confirmed")
    log = (directory / "generation.log").read_text()
    missing_argument = all(s in log for s in ("in recover_jobs", "recorded_admissions",
        "TypeError: BatchPlan.__init__() got an unexpected keyword argument"))
    missing_field = all(s in log for s in ("in recover_jobs", "BatchRecovery.from_events",
        "batch_plan.py", "Missing: []. Unexpected: ['recorded_admissions']."))
    if not (missing_argument or missing_field):
        raise ValueError("not the explicit frozen recovery dependency failure")
    allowed = {"control", "generation.json", "generation.log", "manifest.json",
               "node_status.json", "rollout", "serving.json", "startup_failures"}
    prepared = {"cleanup.json", "runtime.json", "runtime_settings.json", "sampling.json",
                "serving", "serving_preflight", "settings.json", "trained", "endpoint.json",
                "inputs", "preparation.json", "process_limit.json", "release.json", "tool_preflight.json"}
    if ({p.name for p in directory.iterdir()} - allowed
            or {p.name for p in (directory / "rollout").iterdir()} - prepared):
        raise ValueError("possible candidate or scoring artifacts; refusing recovery-preflight retry")
    if not checkpoint.read(directory / "rollout/settings.json").get("generation_recovery"):
        raise ValueError("recovery-preflight requires an unchanged source handoff")


def retry_startup(root, key, retry_id, *, port_preflight=False, recovery_preflight=False):
    return _controller.retry_startup(root, key, retry_id, port_preflight=port_preflight,
        preflight_check=verify_recovery_preflight if recovery_preflight else None,
        cwd=Path(__file__).resolve().parents[1])


def run(root):
    return _controller.run(root, cwd=Path(__file__).resolve().parents[1])


def launch(root):
    return _controller.launch(root, cwd=Path(__file__).resolve().parents[1])
