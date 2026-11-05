"""Explicit access to the verifier's private in-memory-device test seams.

Production signal-level consumers must enter through
``RTLHandle.command_request()`` and ``api.handle_command()``.  Unit tests still
need a small, deterministic device to exercise the shared driver and evaluator without
an EDA toolchain.  Keeping those aliases here makes that privilege visible and prevents
the convenience path from becoming part of :mod:`behave.sim.api`'s public surface.
"""

from behave.sim.core.admission import AdmittedTask
from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.evaluation import evaluate as evaluate_device
from behave.sim.evaluation import evaluate_generated as evaluate_generated_device
from behave.sim.evaluation import run as run_device

__all__ = [
    "evaluate_device",
    "evaluate_generated_device",
    "prepare_device_command",
    "run_device",
]


def prepare_device_command(
    task,
    artifact,
    *,
    run_directory,
    baseline,
    providers,
    generation_spec,
    probe_budget=None,
    search_policy=None,
    reference=None,
    run_analysis=True,
    capture_analysis=False,
    secondary_pool_budget=0,
    feasibility_timeout_ms=None,
    query_timeout_ms=None,
    batch=None,
    analysis_wall_timeout_seconds=None,
):
    """Build a command fixture without making Python devices a public entry point.

    The production command handler and its validation remain unchanged. Only the
    fixture supplies an empty structural catalogue for an in-memory device.
    """
    from behave.sim.command import _command_request

    if type(task) is not AdmittedTask:
        raise TypeError("preparing a simulator command needs an AdmittedTask")
    if getattr(artifact, "kind", None) != "device":
        raise Refused(Refusal(
            "sim-command-public-artifact-kind-unsupported",
            "this test fixture accepts only an admitted in-memory device; "
            "production artifacts enter through their public handles",
            artifact_kind=str(getattr(artifact, "kind", "") or "") or None,
        ))
    return _command_request(
        run_directory=run_directory,
        task=task,
        artifact=artifact,
        baseline=baseline,
        providers=providers,
        frontend_catalogue=CatalogueManifest(()),
        generation_spec=generation_spec,
        probe_budget=probe_budget,
        search_policy=search_policy,
        run_analysis=run_analysis,
        capture_analysis=capture_analysis,
        secondary_pool_budget=secondary_pool_budget,
        reference=reference,
        feasibility_timeout_ms=feasibility_timeout_ms,
        query_timeout_ms=query_timeout_ms,
        batch=batch,
        # Device fixtures choose a finite phase budget; production callers must
        # supply their own rather than inheriting a hidden Sim default.
        analysis_wall_timeout_seconds=(60 if run_analysis and analysis_wall_timeout_seconds is None
                                       else analysis_wall_timeout_seconds),
    )
