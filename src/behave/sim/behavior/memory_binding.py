"""Freeze the one external-memory environment of an exact Behavior relation."""

from __future__ import annotations

from ..core.contract import TaskContract
from ..core.memory import MemoryEnvironment
from ..core.refusal import Refusal, Refused
from ..core.stimulus import Stimulus

__all__ = ["memory_environment_for_stimuli"]


def memory_environment_for_stimuli(contract, stimuli, horizon):
    """Return the common run-owned memory environment, or ``None``.

    One exact relation has one initial state.  A pool whose stimuli carry different
    memory images or ready schedules therefore denotes several relations and must be
    partitioned by its caller rather than silently merged here.
    """

    if type(contract) is not TaskContract:
        raise TypeError("exact memory binding needs one TaskContract")
    stimuli = tuple(stimuli)
    if any(type(stimulus) is not Stimulus for stimulus in stimuli):
        raise TypeError("exact memory binding consumes only Stimulus records")
    if type(horizon) is not int or isinstance(horizon, bool) or horizon < 1:
        raise TypeError("exact memory binding needs a positive schedule horizon")
    if not contract.memories:
        return None
    if not stimuli or any(
            type(stimulus.memory_environment) is not MemoryEnvironment
            for stimulus in stimuli):
        raise Refused(Refusal(
            "behavior-exact-memory-environment-unbound",
            "every external-memory stimulus must carry its exact initialization "
            "and ready schedule"))
    environment = stimuli[0].memory_environment
    if any(
            stimulus.memory_environment.digest != environment.digest
            or stimulus.memory_environment.as_dict() != environment.as_dict()
            for stimulus in stimuli[1:]):
        raise Refused(Refusal(
            "behavior-exact-memory-environment-not-unique",
            "one exact relation cannot combine stimuli run under different memory "
            "initializations or ready schedules"))
    # Concrete runs carry the ready schedule as a policy. The analysis domain
    # is that same run expanded to the finite horizon, not a second choice.
    environment = environment.materialize(horizon)
    faults = environment.faults_against(contract.memories, horizon)
    if faults:
        raise Refused(Refusal(
            "behavior-exact-memory-environment-outside-contract",
            "the concrete memory environment is outside the frozen task and "
            "horizon",
            first=faults[0], count=len(faults)))
    return environment
