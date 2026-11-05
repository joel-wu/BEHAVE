"""Derive transaction declarations from the trusted Behavior boundary.

The interface declaration fixes ports, widths, clocks and endpoint grouping.  A
Behavior method fixes which endpoint fields form one logical call and which
ordered transaction streams it returns.  Those two sources determine endpoint
kind and output matching without reading prose or observing a candidate design.

Cardinality is deliberately absent.  ``None``, one mapping and a list of mappings
are respectively zero, one and several output transactions; a fixed
``answers_per_transaction`` value does not follow from that interface.
"""

from __future__ import annotations

import hashlib
import json

from .behavior.extract import LoweringError, source_model
from .behavior.task_boundary import task_method_boundary
from .core.contract import Direction, TaskContract
from .core.refusal import Refused

__all__ = [
    "GENERATOR",
    "behavior_digest",
    "derive",
    "interface_digest",
]


# Version 5 is the retired prose rule in ``spec_rules``.  Version 6 changes the
# authority for semantic fields to the Behavior/task-interface boundary.
GENERATOR = 6


def behavior_digest(source):
    """Complete identity of the Behavior source used by this derivation."""

    if type(source) is not str:
        raise TypeError("Behavior source must be an exact string")
    return hashlib.sha256(source.encode()).hexdigest()


def _interface_image(contract):
    """The task facts consumed by :func:`derive`, excluding semantic overlays."""

    if type(contract) is not TaskContract:
        raise TypeError("interface identity needs one exact TaskContract")
    endpoints = []
    for endpoint in contract.endpoints:
        endpoints.append({
            "name": endpoint.name,
            "direction": endpoint.direction.value,
            "fields": [field.as_dict() for field in endpoint.fields],
            "adapter": endpoint.adapter.as_dict(),
            "clock": endpoint.clock,
        })
    clocks = [{
        "name": clock.name,
        "clock": clock.clock,
        "edge": clock.edge,
        "reset": clock.reset,
        "reset_active_low": clock.reset_active_low,
    } for clock in contract.clocks]
    return {
        "endpoints": endpoints,
        "clocks": clocks,
        "memories": [memory.as_dict() for memory in contract.memories],
        "physical_value_layout": contract.physical_value_layout,
    }


def interface_digest(contract):
    """Identity of the physical task boundary used by the derivation."""

    body = json.dumps(
        _interface_image(contract), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def derive(source, contract, *, file="behavior.py"):
    """Return ``(fields, evidence, missing)`` for one trusted reference.

    ``fields`` uses the flat ``endpoint.<name>.<property>`` spelling consumed by
    generated manifests.  ``evidence`` records the Behavior method establishing
    each endpoint boundary.  A nonempty ``missing`` means no partial declaration
    is returned.
    """

    if type(source) is not str:
        raise TypeError("Behavior source must be an exact string")
    if type(contract) is not TaskContract:
        raise TypeError("reference derivation needs one exact TaskContract")

    try:
        model = source_model(source, file=file)
        if None in model.steps:
            methods = (("process", None),)
        else:
            methods = tuple(
                (f"process_{domain}", domain) for domain in model.domains)

        covered = {}
        for method, domain in methods:
            if domain is not None and contract.clock_domain(domain) is None:
                return {}, {}, [
                    ("behavior-clock-domain-unknown: "
                     f"{method} names {domain!r}, absent from the task interface")
                ]
            boundary = task_method_boundary(
                source, file, contract, method=method, clock=domain,
                cls=model.cls.name)
            for projection in boundary.inputs:
                covered.setdefault(projection.endpoint, []).append(
                    (method, Direction.IN))
            for projection in boundary.outputs:
                covered.setdefault(projection.endpoint, []).append(
                    (method, Direction.OUT))
    except LoweringError as exc:
        return {}, {}, [f"{exc.code}: {exc.what}"]
    except Refused as exc:
        return {}, {}, [f"{exc.refusal.kind}: {exc.refusal.why}"]
    except SyntaxError as exc:
        return {}, {}, [f"behavior-syntax-error: {exc.msg}"]

    missing = sorted(
        endpoint.name for endpoint in contract.endpoints
        if endpoint.name not in covered)
    repeated = sorted(
        name for name, rows in covered.items() if len(rows) != 1)
    if missing or repeated:
        reasons = []
        if missing:
            reasons.append(
                "behavior-endpoint-unbound: " + ", ".join(missing))
        if repeated:
            reasons.append(
                "behavior-endpoint-bound-more-than-once: " + ", ".join(repeated))
        return {}, {}, reasons

    fields, evidence = {}, {}
    for endpoint in contract.endpoints:
        method, direction = covered[endpoint.name][0]
        if direction is not endpoint.direction:
            return {}, {}, [
                ("behavior-endpoint-direction-mismatch: "
                 f"{endpoint.name!r}")
            ]
        prefix = f"endpoint.{endpoint.name}"
        fields[f"{prefix}.kind"] = "transaction"
        if endpoint.direction is Direction.OUT:
            fields[f"{prefix}.matching"] = "ordered"
        evidence[endpoint.name] = method
    return fields, evidence, []
