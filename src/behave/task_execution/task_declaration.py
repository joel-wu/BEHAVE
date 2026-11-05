"""Check a task's declared RTL ports, without implementing or scoring RTL."""

from contextlib import ExitStack
from dataclasses import replace
from time import monotonic

from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, parse_json
from behave.core.resources import allocated_cpus

from .judgment import ComponentJudgment
from .simulator_judge import _failure


def check_task_declaration(spec, contract, *, objects, cpu_pool=None,
                           deadline=None, cancelled=lambda: False):
    """Reuse Sim admission on the public declaration; never replay an empty body.

    A mismatch is evidence about the task inputs, not a candidate's score.
    The caller retains the ordinary runtime, CPU lease and role visibility.
    """
    from behave.sim import api

    started = monotonic()
    inputs = (objects.put(spec), objects.put(contract))
    with ExitStack() as stack:
        if api.cpu_capacity() is None:
            endpoint = (stack.enter_context(api.CpuPool(capacity=len(allocated_cpus()))).endpoint
                        if cpu_pool is None else api.CpuPoolEndpoint.from_dict(cpu_pool))
            stack.enter_context(api.cpu_scope(endpoint, deadline=deadline, cancelled=cancelled))
        # A supervised comparison worker already owns the shared CPU scope.
        # Reuse it; never create a second pool or nest another scope.
        try:
            task = api.TaskContract.from_dict(parse_json(contract))
            realization = api.realization_of_task_interface(spec.decode("utf-8"), contract=task)
            api.admit(task, realization)
            result = ComponentJudgment(1, "task-interface-consistent")
        except api.FailureRaised as error:
            failure = error.failure
            if failure.origin is api.FailureOrigin.CANDIDATE:
                failure = api.RunFailure.reference("task-interface", failure.reason)
            result = _failure(api, failure, objects=objects, reason_visible=True)
        except api.Refused as error:
            result = _failure(api, api.RunFailure.verifier("task-interface", error.refusal),
                              objects=objects, reason_visible=True)
        except (ValueError, TypeError) as error:
            result = _failure(api, api.RunFailure.reference("task-interface", api.Refusal(
                "task-interface-invalid", "the supplied task declaration could not be read",
                error=str(error))), objects=objects, reason_visible=True)
        resources = api.cpu_usage()
    # A cancelled queue entry never acquired CPUs and has no release field.
    # Once offered, the shared pool records an explicit release outcome.
    cleanup = (CleanupState.UNKNOWN if any(row.get("released") is False
                                          for row in resources["reservations"])
               else CleanupState.COMPLETE)
    evidence = objects.put(canonical_json({
        "spec": inputs[0].as_dict(), "contract": inputs[1].as_dict(),
        "result": result.as_dict(), "resources": resources,
        "functional_correctness_checked": False,
    }))
    return replace(result, cleanup=cleanup, elapsed_seconds=monotonic() - started,
                   evidence=(*inputs, *result.evidence, evidence))
