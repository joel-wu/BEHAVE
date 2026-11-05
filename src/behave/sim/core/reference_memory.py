"""Bind a concrete model to the external-memory environment of one stimulus."""

from __future__ import annotations

from .memory import MemoryEnvironment
from .refusal import Refusal, Refused
from .stimulus import Stimulus

__all__ = ["bind_external_memory"]


def bind_external_memory(model, contract, stimulus):
    """Install ``stimulus``'s memory image in a trusted concrete model adapter.

    The memory ready schedule drives the RTL product.  A transaction-level Behavior
    model does not observe that timing, but its memory fields must start from and mutate
    the same cells.  The trusted model adapter owns the representation of those fields.
    """

    if type(stimulus) is not Stimulus:
        raise TypeError("external-memory binding needs one exact Stimulus")
    memories = tuple(contract.memories)
    if not memories:
        return
    environment = stimulus.memory_environment
    if type(environment) is not MemoryEnvironment:
        raise Refused(Refusal(
            "reference-memory-environment-missing",
            "the task declares external memory but this stimulus carries no exact "
            "memory environment"))
    faults = environment.faults_against(memories, len(stimulus.slots))
    if faults:
        raise Refused(Refusal(
            "reference-memory-environment-invalid",
            "the reference and RTL cannot share an invalid memory environment",
            first=faults[0], count=len(faults)))
    binder = getattr(model, "_bind_external_memory", None)
    if not callable(binder):
        raise Refused(Refusal(
            "reference-memory-adapter-unavailable",
            "this trusted reference adapter cannot bind the task's external-memory "
            "state to the stimulus"))
    try:
        binder(memories, environment, len(stimulus.slots))
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - trusted adapter boundary
        raise Refused(Refusal(
            "reference-memory-binding-failed",
            "the trusted reference adapter could not install the stimulus's external "
            "memory image",
            error=f"{type(exc).__name__}: {exc}")) from None
