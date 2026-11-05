"""What this build's authoritative path accepts, in one place.

Wave 1 item 4. The first version of a sound path is narrow, and the value of saying so
here is that the alternative is not a wider path -- it is an implicit one. Every shape in
this list was previously handled by *something*: a channel whose kind nobody declared was
run as if it carried transactions, a contract with two clocks fell through to the
single-clock driver, a reference nothing could freeze was compared against anyway. None
of those produced an error; they produced a number.

So the range is enumerated, each entry says what goes wrong outside it, and anything
outside is a **typed refusal**. In particular it does not fall back to the older score:
a refusal that quietly becomes the previous answer is the previous answer.

Everything here is a property of the *task* and its reference, checkable before anything
runs. What a run then does with the design is `admission`'s question and the monitor's.
"""

from __future__ import annotations

from .contract import SUPPORTED_ADAPTERS, Kind
from .protocol import adapter_program_for, receptive_controls
from .refusal import Refusal, Refused

__all__ = ["SUPPORTED", "refusals_for", "supported"]

#: The range, as prose, for a report to print beside a refusal.
SUPPORTED = (
    "declared clock/reset domains; at least one input and one output transaction "
    "endpoint -- the score is a rate over accepted input transfers, so a task with no "
    "input has no denominator -- with each endpoint-bearing domain accepted by one "
    "registered executable adapter product; a declared output-matching policy; "
    "external memory only through the declared finite read-first adapter and an exact "
    "per-stimulus initialization/ready schedule; "
    "a reference "
    "the loader froze; and either an explicit finite replay schedule or a supported "
    "transaction-driven ReplayPolicy recording the schedule that actually ran. "
    "Runtime completion profiles report unsupported compositions explicitly; "
    "a bounded-analysis horizon is not a concrete-execution cutoff")


def refusals_for(contract, reference=None, stimuli=(), horizon=None,
                 stall_every=None):
    """Every way this task falls outside the supported range. Empty means inside.

    All of them, not the first: an operator fixing one shape at a time and re-running
    learns the next one only after another full sweep, and the list is cheap.
    """
    out = []
    out += _clocks(contract)
    out += _endpoints(contract)
    out += _reference(reference)
    out += _memory(stimuli)
    out += _horizon(horizon)
    out += _back_pressure(contract, stall_every)
    return tuple(out)


def supported(contract, reference=None, stimuli=(), horizon=None, stall_every=None):
    return not refusals_for(contract, reference, stimuli, horizon, stall_every)


def _clocks(contract):
    return [Refusal(
        "unsupported-no-declared-reset",
        "the clock domain names no reset, so where a run starts is whatever state "
        "the simulator happened to leave the design in, and two runs of one stimulus "
        "are not comparable",
        clock=domain.name)
        for domain in contract.clocks if not domain.reset]


def _endpoints(contract):
    out = []
    inputs, outputs = contract.inputs(), contract.outputs()
    if not outputs:
        out.append(Refusal(
            "unsupported-no-output-endpoint",
            "a task with nothing to observe cannot be compared against a reference, so "
            "a run of it establishes nothing about agreement"))
    if not inputs:
        # Appendix A.1's score is a rate over **accepted input transfers**, so a task
        # with no input endpoint has no denominator: every run of it scores 0 whatever
        # the design does, which is not a measurement of anything. Refused by name here
        # rather than crashing in canonicalisation -- which is where the first
        # output-only contract actually died -- or scoring a constant.
        out.append(Refusal(
            "unsupported-no-input-endpoint",
            "the score is over accepted input transfers, and this task's environment "
            "offers nothing: there is no denominator, and a run of it would score 0 "
            "whatever the design does"))
    for endpoint in contract.endpoints:
        if (endpoint.adapter.name, endpoint.adapter.version) not in SUPPORTED_ADAPTERS:
            out.append(Refusal(
                "unsupported-adapter",
                "this endpoint names a protocol this build does not implement, and "
                "applying the one it has would give the recorded schedule a meaning the "
                "task never declared",
                endpoint=endpoint.name, adapter=endpoint.adapter.name,
                version=endpoint.adapter.version))
        if endpoint.kind is not Kind.TRANSACTION:
            out.append(Refusal(
                "unsupported-kind-not-transaction",
                "this build compares transaction streams, and this channel is not "
                "declared to carry transactions. **Undeclared is not transactional**: "
                "a continuously-meaningful sampled output has a value at every cycle "
                "and comparing its transfers as a stream answers a different question. "
                "It cannot be read off a run, so the task declares it or this refuses",
                endpoint=endpoint.name, declared_kind=endpoint.kind.value))
    try:
        if len(contract.clocks) > 1:
            from .atomic import atomic_adapter_programs

            atomic_adapter_programs(contract)
        else:
            adapter_program_for(contract)
    except Refused as exc:
        out.append(exc.refusal)
    return out


def _reference(reference):
    # Imported here: `core.reference` imports `core.compare`, and this module is
    # imported by the contract-level code that compare does not need.
    from .reference import LoadedReference

    if reference is None:
        return [Refusal(
            "unsupported-no-reference",
            "without a reference there is nothing to be right or wrong against, and a "
            "run with an empty mismatch list because nobody looked is not a pass")]
    if not isinstance(reference, LoadedReference):
        return [Refusal(
            "unsupported-reference-not-frozen",
            "the reference has to be one a loader froze: identity and construction from "
            "one set of bytes. A live model handed in beside a digest is a pairing "
            "nothing downstream can check",
            got=type(reference).__name__)]
    return []


def _memory(stimuli):
    for stimulus in stimuli or ():
        if getattr(stimulus, "memory_policy", ()):  # sorted pairs when non-empty
            return [Refusal(
                "unsupported-memory-sideband",
                "this stimulus carries the retired loose memory-policy mapping rather "
                "than the typed finite MemoryEnvironment. Random policy is permitted "
                "only while constructing exact ready bits; replay cannot reinterpret it",
                stimulus=stimulus.fingerprint)]
    return []


def _horizon(horizon):
    if horizon is None:
        return [Refusal(
            "unsupported-implicit-horizon",
            "how many slots follow the last offer decides what counts as a missing "
            "answer, and a default chosen here would put the caller's number into the "
            "verdict without the report saying so. State it",
            hint="pass the drain the task's latency justifies")]
    return []


def _back_pressure(contract, stall_every):
    # Ask the same response-action authority as baseline, framing and drain.
    # A product's generic monitor flags do not summarize its constituent interfaces.
    try:
        has_stalls = bool(receptive_controls(contract))
    except Refused:
        # `_endpoints` already names the unsupported/mixed adapter.  Do not replace that
        # capability refusal with a run-policy complaint about a protocol we cannot read.
        return []
    if not has_stalls:
        return []
    if stall_every is None:
        return [Refusal(
            "unsupported-implicit-back-pressure",
            "whether the environment ever declines an output decides what the run "
            "tests: always ready and the design is never asked to hold anything, so "
            "the one obligation checkable without a declaration is never exercised and "
            "an empty violation list reads as a design that handles stalls. Never "
            "ready and nothing transfers at all",
            hint="0 to never decline, k to decline every k-th slot")]
    return []
