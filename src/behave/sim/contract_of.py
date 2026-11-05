"""Reading the task's contract from the task, and a candidate's realization from it.

Two functions and one rule: **the first reads the specification and the second reads the
candidate, and neither may stand in for the other.** `core/contract.py` says what a
contract is; this says where each side's facts come from, which is the part that has been
getting them from the wrong place. `cosim.py` derives widths from the candidate RTL,
checks only for missing names, and derives signedness from the candidate too, so the
contract a verdict is relative to is assembled from the thing under test.

This lives in `sim/` rather than `sim/core/`, deliberately. Recognising `in_valid` as a
handshake and `clk` as a clock is a **naming convention**, and a convention is a fact
about how this project writes SystemVerilog, not about what a contract is. The core would
be the wrong owner for it; `rtl_meta` already holds the same rules for the candidate side
and they are shared from there rather than written twice.

Where the convention does not apply, both functions **refuse**. A task whose declaration
has no recognisable handshake has not declared a ready/valid endpoint, and guessing one
would produce a contract that agrees with whatever the candidate does.
"""

from __future__ import annotations

from .core.admission import Port, Realization, _physical_bindings
from .core.contract import ClockDomain, Direction, Endpoint, Field, FiniteType, Kind, TaskContract, ready_valid
from .core.failure import FailureRaised, RunFailure
from .core.memory import MemoryInterface, MemoryPort, MemoryRealization
from .core.refusal import Refusal, Refused
from .rtl_meta import _classify_clock, _classify_reset, _domain_for_signal

__all__ = ["contract_of_schema", "contract_of_spec", "realization_of_source"]

#: How this project spells the two handshakes. Read from the same place `rtl_meta`
#: partitions a design by, so the task side and the candidate side cannot drift.
_HANDSHAKE = {
    "in": ("in_valid", "in_ready"),
    "out": ("out_valid", "out_ready"),
}


def _partition(schema):
    """Split a declared schema into clocks, handshakes, memory ports and data, by name.

    Returns ``(clocks, handshake, data, memory)``. Anything the convention does not
    recognise is data, which is the safe direction -- a data port nobody declared is
    caught by admission as an extra payload, where a handshake signal misread as data
    would silently become a field.

    **Memory ports are separated and not folded into the payload.** A design's
    `mem_req_valid` / `mem_resp_rdata` are a second protocol on a sideband channel, and
    treating them as fields of the input transaction produces a contract that says the
    task's transactions carry a memory address -- which is not a smaller version of the
    truth, it is a different task. `sim/memory.py` already knows the shapes, and it is
    asked here rather than a second detector being written.
    """
    from .memory import detect_memory_interfaces, memory_port_names

    ins = {n: e.width for n, e in schema.endpoints.items()
           if e.direction == "input"}
    outs = {n: e.width for n, e in schema.endpoints.items()
            if e.direction == "output"}
    memory = detect_memory_interfaces(ins, outs, strict=True)
    memory_names = memory_port_names(memory)

    clocks, handshake, data = {}, {}, {"input": [], "output": []}
    for name, endpoint in sorted(schema.endpoints.items()):
        if name in memory_names:
            continue
        if name in ("in_valid", "in_ready", "out_valid", "out_ready"):
            handshake[name] = endpoint
            continue
        # ``in_*`` and ``out_*`` are transaction-channel names in this task
        # convention.  Their suffix can itself describe the payload
        # (``in_reset``) or sampled result (``out_clk``); classifying that word
        # as another physical reset/clock invents an incomplete domain.  Real
        # external clocks and resets are environment inputs outside these two
        # reserved channel namespaces (for example ``clk``, ``rst``,
        # ``write_clk`` and ``write_rst``).
        channel_data = (
            endpoint.direction == "input" and name.startswith("in_")
        ) or (
            endpoint.direction == "output" and name.startswith("out_")
        )
        if not channel_data:
            domain = _classify_clock(name)
            if domain is not None and endpoint.direction == "input":
                clocks.setdefault(domain, {})["clock"] = name
                continue
            reset = _classify_reset(name)
            if reset is not None and endpoint.direction == "input":
                clocks.setdefault(reset[0], {})["reset"] = name
                clocks[reset[0]]["active_low"] = reset[1]
                continue
        data[endpoint.direction].append(endpoint)
    return clocks, handshake, data, tuple(memory)


def _domain_channels(schema, clocks, memory):
    """Port groups for the naming-convention channel in each clock domain."""
    names = sorted(clocks)
    from .memory import memory_port_names

    memory = memory_port_names(list(memory))
    out = {}
    for domain in names:
        prefix = f"{domain}_" if domain else ""
        controls = {
            "in_valid": prefix + "in_valid",
            "in_ready": prefix + "in_ready",
            "out_valid": prefix + "out_valid",
            "out_ready": prefix + "out_ready",
        }
        excluded = {facts.get("clock") for facts in clocks.values()}
        excluded.update(facts.get("reset") for facts in clocks.values())
        excluded.update(
            f"{other + '_' if other else ''}{stem}"
            for other in names
            for stem in ("in_valid", "in_ready", "out_valid", "out_ready"))
        local = [endpoint for name, endpoint in sorted(schema.endpoints.items())
                 if _domain_for_signal(name, names) == domain
                 and name not in excluded and name not in memory]
        out[domain] = {
            "controls": controls,
            "inputs": [endpoint for endpoint in local
                       if endpoint.direction == "input"],
            "outputs": [endpoint for endpoint in local
                        if endpoint.direction == "output"],
        }
    return out


def _memory_records(memory, clocks, *, realization=False, domain_labels=None):
    """Bind detected physical memories to the task's declared clock domains."""

    domains = sorted(clocks)
    records = []
    record_type = MemoryRealization if realization else MemoryInterface
    for descriptor in memory:
        if len(domains) == 1:
            owner = domains[0] or "clk"
        else:
            owners = set()
            for port in descriptor["ports"]:
                prefix = (descriptor["name"] if port["id"] is None else
                          f"{descriptor['name']}__{port['id']}")
                found = _domain_for_signal(prefix + "_req_valid", domains)
                if found is not None:
                    owners.add(found)
            if len(owners) != 1:
                raise Refused(Refusal(
                    "contract-memory-clock-ambiguous" if not realization else
                    "realization-memory-clock-ambiguous",
                    "every external memory belongs to exactly one declared clock "
                    "domain; its request ports do not identify one",
                    memory=descriptor["name"], domains=sorted(owners)))
            owner = next(iter(owners))
        if domain_labels is not None:
            owner = domain_labels.get(owner, owner)
        records.append(record_type(
            descriptor["name"], descriptor["addr_width"],
            descriptor["data_width"], owner,
            [MemoryPort(port["type"], port["id"])
             for port in descriptor["ports"]]))
    return records


def contract_of_schema(schema, source="", max_outstanding=None, assume_kind=None):
    """Build endpoint, clock and memory records from a declared port schema.

    Port shape does not establish transaction semantics or an outstanding-request
    limit: both remain undeclared unless explicitly supplied. ``assume_kind`` is
    a caller-provided semantic assumption, never an inferred default.

    Multiple clock domains retain their separate endpoint and memory ownership.
    Incomplete or ambiguous bindings are refused instead of merging domains.
    """
    clocks, handshake, data, memory = _partition(schema)
    if not clocks:
        raise Refused(Refusal(
            "contract-of-spec-clock-count",
            "this declaration names no clock domain, so its events have no declared "
            "moments",
            domains=[]))
    if len(clocks) > 1:
        incomplete = {name: sorted({"clock", "reset"} - set(facts))
                      for name, facts in clocks.items()
                      if not {"clock", "reset"} <= set(facts)}
        if incomplete:
            raise Refused(Refusal(
                "contract-of-spec-clock-incomplete",
                "every clock domain needs both an external clock and reset binding",
                domains=incomplete))
        channels = _domain_channels(schema, clocks, memory)
        endpoints = []
        for domain in sorted(clocks):
            group = channels[domain]
            controls = group["controls"]
            for direction, data_key, valid_key, ready_key in (
                    (Direction.IN, "inputs", "in_valid", "in_ready"),
                    (Direction.OUT, "outputs", "out_valid", "out_ready")):
                data = group[data_key]
                valid = schema.endpoints.get(controls[valid_key])
                ready = schema.endpoints.get(controls[ready_key])
                present = valid is not None or ready is not None
                if not data and not present:
                    continue
                want_valid = "input" if direction is Direction.IN else "output"
                want_ready = "output" if direction is Direction.IN else "input"
                if (valid is None or ready is None
                        or valid.direction != want_valid
                        or ready.direction != want_ready):
                    raise Refused(Refusal(
                        "contract-of-spec-no-handshake",
                        "this domain declares a channel without both correctly owned "
                        "ready/valid controls",
                        domain=domain, direction=direction.value,
                        valid=controls[valid_key], ready=controls[ready_key]))
                endpoint_name = f"{domain}_{direction.value}"
                endpoints.append(Endpoint(
                    kind=(assume_kind if assume_kind is not None
                          else Kind.UNDECLARED),
                    name=endpoint_name,
                    direction=direction,
                    fields=[Field(port.name, FiniteType(port.width, port.signed),
                                  port.name) for port in data],
                    adapter=ready_valid(
                        direction, controls[valid_key], controls[ready_key]),
                    clock=domain,
                    max_outstanding=max_outstanding))
        if not endpoints:
            raise Refused(Refusal(
                "contract-of-spec-no-channel",
                "the declared clock domains contain no complete transaction channel",
                domains=sorted(clocks)))
        return TaskContract(
            endpoints=endpoints,
            clocks=[ClockDomain(
                domain, facts["clock"], facts["reset"],
                reset_active_low=facts.get("active_low", False))
                for domain, facts in sorted(clocks.items())],
            source=source or (schema.source or ""),
            authority=getattr(schema.authority, "value", str(schema.authority)),
            memories=_memory_records(memory, clocks))
    domain_name, facts = next(iter(clocks.items()))
    if "clock" not in facts or "reset" not in facts:
        raise Refused(Refusal(
            "contract-of-spec-clock-incomplete",
            "a clock domain needs both a clock and a reset: the reset is the task's, "
            "and without one nothing says where a run starts",
            domain=domain_name, found=sorted(facts)))

    endpoints = []
    for direction, key in ((Direction.IN, "input"), (Direction.OUT, "output")):
        ports = data[key]
        valid, ready = _HANDSHAKE["in" if direction is Direction.IN else "out"]
        present = [s for s in (valid, ready) if s in handshake]
        # **The handshake makes the endpoint, not the data.** `bcd_counter` declares
        # `in_valid`/`in_ready` and no data input at all: its input transaction is a
        # payload-free request, and 24 designs of this population are that shape.
        # Keying on the data dropped their input endpoint entirely, so a task with a
        # request channel looked like a task with none.
        if not ports and not present:
            continue
        if len(present) != 2:
            raise Refused(Refusal(
                "contract-of-spec-no-handshake",
                "this declaration has a channel in this direction and does not declare "
                "both sides of its handshake, so nothing says when a transaction is "
                "offered or taken; guessing one would give the recorded schedule a "
                "meaning the task never stated",
                direction=direction.value,
                missing=[s for s in (valid, ready) if s not in handshake],
                found=sorted(handshake)))
        endpoints.append(Endpoint(
            kind=assume_kind if assume_kind is not None else Kind.UNDECLARED,
            name=direction.value,
            direction=direction,
            fields=[Field(p.name, FiniteType(p.width, p.signed), p.name)
                    for p in ports],
            adapter=ready_valid(direction, valid, ready),
            clock=domain_name or "clk",
            max_outstanding=max_outstanding))
    if not endpoints:
        raise Refused(Refusal(
            "contract-of-spec-no-channel",
            "this declaration has no channel in either direction -- no data and no "
            "handshake -- so there is no transaction for a contract to be about",
            ports=sorted(schema.endpoints)))

    return TaskContract(
        endpoints=endpoints,
        clocks=[ClockDomain(domain_name or "clk", facts["clock"], facts["reset"],
                            reset_active_low=facts.get("active_low", False))],
        source=source or (schema.source or ""),
        authority=getattr(schema.authority, "value", str(schema.authority)),
        memories=_memory_records(memory, clocks))


def contract_of_spec(spec, name="the task", max_outstanding=None, assume_kind=None):
    """The `TaskContract` a specification declares.

    Goes through `task.schema_of_spec`, so the interface is **elaborated**: a width
    written over the declaration's parameters is a number here rather than a refusal, and
    91 designs in the population declare a port whose width is not a literal. Reading the
    task with a regular expression and the candidate with an elaborator would have
    replaced one unread contract with another.
    """
    from .task import schema_of_spec

    return contract_of_schema(schema_of_spec(spec, name),
                              source=f"declared by {name}",
                              max_outstanding=max_outstanding,
                              assume_kind=assume_kind)


def realization_of_source(source, name="the candidate", top=None, *, contract=None):
    """What a candidate design offers, as a `Realization` for `admit`.

    Elaborated for the same reason the task is, and it is the elaboration that makes the
    signedness check runnable: an SMT2 model carries no signedness at all, so a
    realization built from one has to report `None` and the check does not run. Here it
    is read from the source, so it does.

    ``driven_by`` follows the port direction, which is what makes the handshake-ownership
    check mean something: an input the candidate declares is a signal the environment
    drives, so a candidate whose `in_ready` is an input is one whose back-pressure the
    environment supplies, and its stall behaviour is untested by construction.
    """
    from .rtl.elaborate import schema_of_module

    if contract is not None and type(contract) is not TaskContract:
        raise TypeError("an RTL realization contract is a TaskContract or absent")
    if contract is not None:
        # Missing task bindings cannot prove that a candidate port is extra.
        _physical_bindings(contract)
    schema = schema_of_module(source, name, top=top)
    clocks, _handshake, _data, memory = _partition(schema)
    domain_names = sorted(clocks)
    domain_labels = {domain or "clk": domain or "clk" for domain in clocks}
    if contract is not None:
        # Clock pin spelling and task domain identity are different namespaces.
        # Translate the existing physical grouping through the declared pin;
        # do not copy an endpoint's desired owner or infer a new clock relation.
        declared_clocks = {clock.clock: clock.name for clock in contract.clocks}
        domain_labels.update({domain or "clk": declared_clocks[facts["clock"]]
                              for domain, facts in clocks.items()
                              if facts.get("clock") in declared_clocks})
    # Memory sideband remains present in the physical port set and is additionally
    # grouped into an independent candidate realization.  The task declaration is read
    # from the specification by the same detector, but admission compares the two rather
    # than allowing this reading to define the task.
    ports = []
    for endpoint in schema.endpoints.values():
        owner = (_domain_for_signal(endpoint.name, domain_names)
                 if domain_names else None)
        ports.append(Port(
            endpoint.name, endpoint.width, signed=endpoint.signed,
            driven_by=("environment" if endpoint.direction == "input"
                       else "artifact"),
            clock=domain_labels[owner or "clk"] if owner is not None else None))
    channel = {}
    if contract is not None:
        # The task owns endpoint and protocol names; the candidate owns the physical
        # ports.  Use the former only to group the latter, then reject every external
        # port the task does not name.  This handles arbitrary registered adapters and
        # endpoint names without guessing either from RTL spelling.
        claimed = {
            field.port
            for endpoint in contract.endpoints
            for field in endpoint.fields
            if field.port is not None
        }
        claimed.update(
            signal
            for endpoint in contract.endpoints
            for signal in (
                endpoint.adapter.environment_controls
                + endpoint.adapter.artifact_controls)
        )
        claimed.update(
            signal
            for clock in contract.clocks
            for signal in (clock.clock, clock.reset)
        )
        from .memory import memory_port_names

        claimed.update(memory_port_names(memory))
        unclaimed = sorted(set(schema.endpoints) - claimed)
        if unclaimed:
            raise FailureRaised(RunFailure.candidate("candidate-admission", Refusal(
                "realization-port-outside-contract",
                "the candidate exposes physical ports absent from the task's fields, "
                "protocol controls, clocks, resets, and memory interfaces",
                ports=unclaimed)))
        channel = {
            endpoint.name: tuple(
                field.port for field in endpoint.fields
                if field.port in schema.endpoints)
            for endpoint in contract.endpoints
        }
    elif len(clocks) > 1:
        for owner, group in _domain_channels(schema, clocks, memory).items():
            for direction, key in (("in", "inputs"), ("out", "outputs")):
                found = [endpoint.name for endpoint in group[key]]
                controls = group["controls"]
                if (schema.endpoints.get(controls[f"{direction}_valid"]) is not None
                        or schema.endpoints.get(
                            controls[f"{direction}_ready"]) is not None
                        or found):
                    channel[f"{owner}_{direction}"] = found
    else:
        for direction, key in (("in", "input"), ("out", "output")):
            found = [e.name for e in _data[key]]
            if found:
                channel[direction] = found
    # Which candidate this is, as a digest of what was read. Not the path: two designs
    # at one path over a session are two candidates, and an admission that cannot tell
    # them apart licenses running the second under the first's checks.
    #
    # The top is **the elaborator's answer** rather than the caller's spelling or the
    # first module in the source.  The normalized elaboration boundary refuses an
    # ambiguous root, so identity cannot silently bind admission to a helper module.
    import hashlib

    resolved = schema.top
    identity = hashlib.sha256(
        (str(resolved) + "\0" + source).encode()).hexdigest()
    return Realization(
        "rtl", ports, channel_ports=channel,
        source=f"elaborated from {name}", identity=identity,
        memories=_memory_records(memory, clocks, realization=True, domain_labels=domain_labels))
