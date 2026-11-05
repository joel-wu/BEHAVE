"""Completion planning for untimed transaction requests, not score authority.

The reference supplies only output counts to the RTL scheduler. Comparison still
derives all expected values independently from the artifact's accepted trace.
No RTL implementation or task name selects a completion rule here.
"""

from .core.contract import Kind
from .core.failure import FailureRaised, RunFailure
from .core.reference import SlotEvent, _require_task_reference
from .core.refusal import Refusal, Refused
from .core.runtime_schedule import environment_controls
from .core.stimulus import EventSlot, Stimulus
from .core.transaction_completion import TransactionCompletion


def declared_completion_targets(contract, request):
    """Output counts only when the task explicitly relates them to one input.

    Domain placement does not change this logical cardinality. No pairing of
    multiple input streams or order-independent Behavior execution is inferred.
    """
    if not contract.outputs():
        return {}
    from .core.sampled_runtime import SampledClockWindow

    if request.replay_policy is not None:
        window = SampledClockWindow.for_contract(contract, request.replay_policy)
        if window is not None:
            return window.outputs_for(request)
    if len(contract.protocol_instances) > 1:
        from .core.interface_product import instance_contracts

        # Membership, not clock placement or handler order, defines which
        # request stream owes these responses. Each declared instance must
        # independently supply its cardinality; never sum unrelated queues.
        return {name: count for _, view in instance_contracts(contract)
                for name, count in declared_completion_targets(view, request).items()}
    if len(contract.inputs()) != 1 or any(endpoint.kind is not Kind.TRANSACTION
                                         for endpoint in contract.endpoints) or any(
            sink.answers_per_transaction is None for sink in contract.outputs()):
        raise Refused(Refusal(
            "runtime-completion-product-undeclared",
            "product completion needs declared interface membership and response cardinalities "
            "or an explicit finite runtime schedule; stream pairing cannot be inferred"))
    depth = request.depth(contract.inputs()[0].name)
    return {sink.name: depth * sink.answers_per_transaction for sink in contract.outputs()}


def completion_targets(task, request, reference):
    """Existing whole-request scheduler interface, from the shared count plan."""
    plan = completion_plan(task, request, reference)
    return None if plan is None else plan.totals


def completion_plan(task, request, reference):
    """Keep transaction boundaries while doing the existing reference count pass.

    No additional reference execution, candidate-driven count or output-value
    cache is introduced. Comparison still executes its independent reference.
    """
    if request.replay_policy is None:
        return None
    _require_task_reference(task, reference)
    contract = task.contract
    depths = tuple(sorted((endpoint.name, request.depth(endpoint.name))
                          for endpoint in contract.inputs()))
    if len(contract.clocks) > 1 or len(contract.inputs()) != 1 or len(contract.outputs()) > 1:
        return TransactionCompletion(request.fingerprint, depths,
            tuple(sorted(declared_completion_targets(contract, request).items())))
    source = contract.inputs()[0]
    model = None
    try:
        model = reference.instantiate(contract)
        if contract.memories:
            from .core.reference_memory import bind_external_memory

            # Completion uses untimed model transactions. Only the initial cells
            # are bound here; the DUT still receives its actual causal ready bits.
            initial = Stimulus(memory_environment=request.memory_environment.materialize(0))
            bind_external_memory(model.model, contract, initial)
        model.reset()
        count = 0
        prefixes = [tuple(0 for _ in contract.outputs())]
        for index, payload in enumerate(dict(request.queues)[source.name]):
            # No tick or invented idle input: this profile's reference advances
            # only on the input queue, whose order cannot depend on RTL stalls.
            count += len(model.advance(SlotEvent(
                index, accepted_inputs=[(source.name, payload)])))
            prefixes.append(tuple(count for _ in contract.outputs()))
        return TransactionCompletion(request.fingerprint, depths,
            tuple(sorted((sink.name, count) for sink in contract.outputs())), tuple(prefixes))
    except FailureRaised:
        raise
    except Exception as exc:  # noqa: BLE001 - reference execution boundary
        refusal = (exc.refusal if isinstance(exc, Refused) else Refusal(
            "reference-completion-failed",
            "the reference could not determine the request's output count",
            error=type(exc).__name__,
            cause=getattr(exc, "code", ""),
            detail=getattr(exc, "detail", "") or str(exc)))
        raise FailureRaised(RunFailure.reference("reference-completion", refusal)) from None
    finally:
        if model is not None:
            model.close()


def behavior_schedule(contract, request):
    """Untimed Behavior consumes one whole input record per invocation."""
    if len(contract.clocks) > 1:
        raise Refused(Refusal(
            "runtime-behavior-product-unsupported",
            "multi-clock runtime requests need the corresponding completion product"))
    depths = {len(rows) for _, rows in request.queues}
    if len(depths) != 1:
        raise Refused(Refusal(
            "runtime-behavior-partial-input-product",
            "one process invocation consumes the complete input record"))
    reset = request.replay_policy.reset_hold_cycles
    names = tuple(endpoint.name for endpoint in contract.inputs())
    slots = [EventSlot(
        reset=index < reset, offers=() if index < reset else names,
        controls=environment_controls(contract, index,
            tuple(clock.name for clock in contract.clocks) if index < reset else (), 0))
        for index in range(reset + next(iter(depths)))]
    return Stimulus(queues=request.queues, slots=slots,
                    initial_state=request.initial_state, memory_policy=request.memory_policy,
                    memory_environment=(None if request.memory_environment is None else
                                        request.memory_environment.materialize(len(slots))),
                    source=request.source)
