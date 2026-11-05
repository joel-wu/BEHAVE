"""Task-bound public realization of one transaction-level Behavior source.

A Behavior has logical procedure inputs and outputs rather than RTL pins.  The task's
protocol adapter supplies the pin-level wrapper.  An isolated source-boundary service
selects the model, checks its call interface, and produces the stable source identity
used by admission, concrete execution, and optional exact analysis. Concrete replay
validates actual returned rows; admission does not infer their Python construction.
"""

from __future__ import annotations

import hashlib
import json

from behave.core import runtime_defaults as defaults

from ..core.admission import Port, Realization
from ..core.contract import Direction, TaskContract
from ..core.failure import CandidateFault, FailureRaised, RunFailure
from ..core.physical_value import packed_signed, packed_width
from ..core.refusal import Refusal, Refused

BEHAVIOR_REALIZATION_VERSION = "behavior-realization/14"
SOURCE_REALIZATION_VERSION = "behavior-source/2"

__all__ = ["BEHAVIOR_REALIZATION_VERSION", "realization_of_source"]


def _source_realization_identity(source, file, class_name, domains):
    """Identify the selected source artifact independently of an analysis bound."""

    if type(source) is not str or type(file) is not str or not file \
            or "\x00" in file \
            or type(class_name) is not str \
            or not class_name.isidentifier() \
            or type(domains) is not tuple \
            or any(type(domain) is not str or not domain for domain in domains):
        raise TypeError("a Behavior source identity needs its selected source boundary")
    image = {
        "version": SOURCE_REALIZATION_VERSION,
        "source": source,
        "file": file,
        "class": class_name,
        "domains": list(domains),
    }
    digest = hashlib.sha256(json.dumps(
        image, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    return f"{SOURCE_REALIZATION_VERSION}:{digest}"


def realization_job(payload):
    """Named-worker implementation; return only a closed JSON value."""

    fields = {"version", "source", "file", "contract", "model"}
    if type(payload) is not dict or set(payload) != fields:
        raise TypeError("a Behavior realization job has its complete field set")
    if payload["version"] != BEHAVIOR_REALIZATION_VERSION:
        raise ValueError("the Behavior realization job version is unsupported")
    source, file = payload["source"], payload["file"]
    selected = payload["model"]
    if selected is not None and (type(selected) is not str or not selected.isidentifier()):
        raise TypeError("a Behavior model selector is absent or a Python identifier")
    if type(source) is not str or not source \
            or type(file) is not str or not file or "\x00" in file:
        raise TypeError("a Behavior realization job needs exact source and file text")
    contract = TaskContract.from_dict(payload["contract"])
    # Only parsing the submitted text attributes SyntaxError to that source.
    # Errors later in trusted resolution or catalogue construction are worker faults.
    from .extract import LoweringError, source_model
    from .source_syntax import parsed_source
    from .task_boundary import task_call_inputs, task_clock_domains, task_input_projections

    try:
        tree = parsed_source(source, file)
    except CandidateFault as exc:
        return {
            "version": BEHAVIOR_REALIZATION_VERSION,
            "status": "source-failed",
            "contract": contract.digest,
            "failure": RunFailure.candidate("candidate-frontend", exc.refusal).as_dict(),
        }
    except Refused as exc:
        return {"version": BEHAVIOR_REALIZATION_VERSION,
                "status": "refused", "contract": contract.digest,
                "refusal": exc.refusal.as_dict()}
    try:
        model = source_model(tree, file, cls=selected, keyword_calls=True)
        domains = task_clock_domains(
            contract, (domain for domain in model.domains if domain is not None))
        if domains:
            for domain in domains:
                selected = source_model(
                    tree, file, step=f"process_{domain}", cls=model.cls.name)
                task_input_projections(
                    contract, tuple(selected.inputs), clock=domain,
                    optional_inputs=selected.optional_inputs)
        else:
            call_inputs = task_call_inputs(
                contract, model.inputs, extra_keywords=model.step.args.kwarg is not None)
            task_input_projections(contract, call_inputs,
                                   optional_inputs=model.optional_inputs)
    except CandidateFault as exc:
        return {
            "version": BEHAVIOR_REALIZATION_VERSION,
            "status": "source-failed",
            "contract": contract.digest,
            "failure": RunFailure.candidate("candidate-frontend", exc.refusal).as_dict(),
        }
    except Refused as exc:
        refusal = exc.refusal
    except LoweringError as exc:
        refusal = Refusal(
            exc.code,
            "the Behavior source boundary is outside the admitted source subset",
            file=file, line=getattr(exc.node, "lineno", 0), detail=exc.what)
    else:
        return {
            "version": BEHAVIOR_REALIZATION_VERSION,
            "status": "realized",
            "contract": contract.digest,
            "identity": _source_realization_identity(
                source, file, model.cls.name, tuple(sorted(domains))),
        }
    return {
        "version": BEHAVIOR_REALIZATION_VERSION,
        "status": "refused",
        "contract": contract.digest,
        "refusal": refusal.as_dict(),
    }


def _port_rows(contract):
    """Build the task-owned protocol wrapper around a logical Behavior boundary."""

    ports = {}

    def add(name, width, signed, driven_by, clock):
        port = Port(
            name, width, signed=signed, driven_by=driven_by, clock=clock)
        previous = ports.get(name)
        if previous is not None and previous.as_dict() != port.as_dict():
            raise Refused(Refusal(
                "behavior-realization-wrapper-port-conflict",
                "one task signal has incompatible roles in the Behavior protocol "
                "wrapper",
                signal=name, first=previous.as_dict(), second=port.as_dict()))
        ports[name] = port

    channels = {}
    for endpoint in contract.endpoints:
        owner = ("environment" if endpoint.direction is Direction.IN else "artifact")
        unbound = tuple(field.name for field in endpoint.fields
                        if field.port is None)
        if unbound:
            raise Refused(Refusal(
                "admission-field-unbound",
                "a Behavior protocol wrapper needs every task field bound to a "
                "physical payload port",
                endpoint=endpoint.name, fields=list(unbound)))
        channels[endpoint.name] = tuple(field.port for field in endpoint.fields)
        for field in endpoint.fields:
            width = packed_width(field.type)
            if width < 1:
                raise Refused(Refusal(
                    "admission-empty-physical-value",
                    "a Behavior protocol wrapper cannot carry an empty physical "
                    "payload value",
                    endpoint=endpoint.name, field=field.name))
            add(
                field.port, width, packed_signed(field.type),
                owner, endpoint.clock)
        for signal in endpoint.adapter.environment_controls:
            add(signal, 1, False, "environment", endpoint.clock)
        for signal in endpoint.adapter.artifact_controls:
            add(signal, 1, False, "artifact", endpoint.clock)
    for domain in contract.clocks:
        add(domain.clock, 1, False, "environment", domain.name)
        add(domain.reset, 1, False, "environment", domain.name)
    # A transaction-level Behavior has no physical memory pins of its own.  The
    # task-owned wrapper realizes the same declared request/response protocol used by
    # the RTL frontend and binds its finite run into the model's named memory fields.
    # Reporting those pins here is essential: admission independently compares a
    # candidate realization with the task rather than trusting the wrapper by kind.
    from ..core.memory import MemoryRealization, memory_port_prefix

    memories = []
    for memory in contract.memories:
        memories.append(MemoryRealization(
            memory.name, memory.addr_width, memory.data_width,
            memory.clock, memory.ports))
        for memory_port in memory.ports:
            prefix = memory_port_prefix(memory.name, memory_port.id)
            add(prefix + "_req_valid", 1, False, "artifact", memory.clock)
            add(prefix + "_req_addr", memory.addr_width, False,
                "artifact", memory.clock)
            add(prefix + "_req_ready", 1, False, "environment", memory.clock)
            if memory_port.reads:
                add(prefix + "_resp_valid", 1, False,
                    "environment", memory.clock)
                add(prefix + "_resp_rdata", memory.data_width, False,
                    "environment", memory.clock)
            if memory_port.writes:
                add(prefix + "_req_wdata", memory.data_width, False,
                    "artifact", memory.clock)
            if memory_port.kind == "rw":
                add(prefix + "_req_we", 1, False, "artifact", memory.clock)
    return (tuple(ports[name] for name in sorted(ports)), channels,
            tuple(memories))


def _refusal_from_image(value):
    if type(value) is not dict or set(value) not in (
            {"kind", "why"}, {"kind", "why", "detail"}):
        raise ValueError("a Behavior realization refusal is not canonical")
    if type(value["kind"]) is not str or type(value["why"]) is not str:
        raise TypeError("a Behavior realization refusal has exact string fields")
    detail = value.get("detail", {})
    if type(detail) is not dict:
        raise TypeError("a Behavior realization refusal detail is a mapping")
    return Refusal(value["kind"], value["why"], **detail)


def _realization_identity(answer, contract):
    """Decode one closed source-judge answer before constructing a realization."""
    common = {"version", "status", "contract"}
    if type(answer) is not dict or not common <= set(answer):
        raise ValueError("the source judge returned no closed realization record")
    if answer["version"] != BEHAVIOR_REALIZATION_VERSION \
            or answer["contract"] != contract.digest:
        raise ValueError("the source judge answered for different task inputs")
    if answer["status"] == "refused" and set(answer) == common | {"refusal"}:
        raise Refused(_refusal_from_image(answer["refusal"]))
    if answer["status"] == "source-failed" and set(answer) == common | {"failure"}:
        failure = RunFailure.from_dict(answer["failure"])
        if failure.origin.value != "candidate" or failure.phase != "candidate-frontend":
            raise ValueError("the source judge returned an unknown source-failure record")
        raise FailureRaised(failure)
    if answer["status"] != "realized" \
            or set(answer) != common | {"identity"} \
            or type(answer["identity"]) is not str \
            or not answer["identity"].startswith(SOURCE_REALIZATION_VERSION + ":"):
        raise ValueError("the source judge returned a malformed realization record")
    return answer["identity"]


def realization_of_source(source, contract, *, name="the Behavior candidate",
                          file="behavior.py",
                          timeout_s=defaults.SIM_BEHAVIOR_WORKER_SECONDS, model=None):
    """Validate and identify a Behavior source for the common simulator command.

    The isolated service checks the selected source boundary but does not construct a
    bounded encoding.  On success this returns the public :class:`Realization`; from
    admission onward Behavior/Behavior and Behavior/RTL use the same stimulus, score,
    persistence, and optional bounded-analysis path.
    """

    if type(source) is not str or not source:
        raise TypeError("Behavior realization source must be a nonempty exact string")
    if type(contract) is not TaskContract:
        raise TypeError("Behavior realization needs one exact TaskContract")
    if type(name) is not str or not name:
        raise TypeError("Behavior realization name must be a nonempty exact string")
    if type(file) is not str or not file or "\x00" in file:
        raise TypeError(
            "Behavior realization file must be a nonempty exact source label")
    if type(timeout_s) is not int or type(timeout_s) is bool or timeout_s < 1:
        raise TypeError("Behavior realization timeout must be a positive integer")
    if model is not None:
        from .source_syntax import require_model_identifier

        try:
            require_model_identifier(model, file)
        except CandidateFault as exc:
            raise FailureRaised(RunFailure.candidate("candidate-frontend", exc.refusal)) from None

    from ..worker import WorkerFailed, run_job_strict

    try:
        answer = run_job_strict(
            "behavior_realization",
            {
                "version": BEHAVIOR_REALIZATION_VERSION,
                "source": source,
                "file": file,
                "model": model,
                "contract": contract.as_dict(),
            },
            timeout_s,
            label="Behavior task realization",
        )
    except WorkerFailed as exc:
        raise FailureRaised(RunFailure.infrastructure("behavior-frontend", Refusal(
            exc.code or "behavior-realization-unavailable",
            "the isolated Behavior frontend did not produce a realization",
            outcome=exc.how, detail=exc.detail))) from None
    except (Refused, FailureRaised):
        raise
    except Exception as exc:  # noqa: BLE001 - trusted service failure, not source evidence
        raise FailureRaised(RunFailure.verifier("behavior-frontend", Refusal(
            "behavior-realization-service-failed", "the Behavior frontend service failed",
            detail=f"{type(exc).__name__}: {exc}"))) from None
    try:
        identity = _realization_identity(answer, contract)
        ports, channels, memories = _port_rows(contract)
        return Realization(
            "behavior", ports, channel_ports=channels,
            source=name, identity=identity, memories=memories)
    except (Refused, FailureRaised):
        raise
    except Exception as exc:  # noqa: BLE001 - trusted decoder failure, not source evidence
        raise FailureRaised(RunFailure.verifier("behavior-frontend", Refusal(
            "behavior-realization-answer-invalid", "the source judge returned an invalid answer",
            detail=f"{type(exc).__name__}: {exc}"))) from None
