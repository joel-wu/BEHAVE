"""Whether a candidate realizes the task, decided before anything is generated.

Proven physical contract violations raise an attributed candidate failure before
generation. Unsupported adapters and incomplete task/frontend declarations remain
refusals, not candidate zeroes. Neither path fabricates an admitted task or replay.

The direction of the check matters and is easy to get backwards. This does not read the
candidate to find out what the task is; it reads the candidate to find out whether the
candidate does what the task already said. `core/schema.py` puts it as: letting the
artifact under test declare the widths lets it choose its own denominator, and a narrower
port can only enlarge the set of goals reported unreachable.

`Realization` is what a frontend hands in. It is plain data on purpose -- the RTL frontend
builds one from its elaboration, a behavior frontend from its declared signature -- so
that this module never learns which side it is talking to.
"""

from __future__ import annotations

import hashlib

from .contract import SUPPORTED_ADAPTERS, Direction, TaskContract
from .failure import FailureRaised, RunFailure
from .frozen import Frozen, FrozenMap
from .protocol import adapter_program_for
from .record_value import canonical_json
from .refusal import Refusal, Refused

__all__ = ["AdmittedTask", "Port", "Realization", "admit"]


_ADMITTED_TASK_TOKEN = object()


class Port(Frozen):
    """One physical signal a candidate actually has.

    ``driven_by`` is `"environment"` or `"artifact"`, and it is the fact the handshake
    check is about: an artifact whose `in_ready` is an input has an environment driving
    its own back-pressure, which is a channel that cannot stall and therefore a channel
    whose stall behaviour nothing tests.
    """

    __slots__ = ("clock", "driven_by", "name", "signed", "width")

    def __init__(self, name, width, signed=False, driven_by="artifact", clock=None):
        if type(name) is not str or not name:
            raise Refused(Refusal(
                "realization-port-name-invalid",
                "a frontend port needs a nonempty exact string name"))
        if type(width) is not int or width < 1:
            raise Refused(Refusal(
                "realization-port-width-invalid",
                "a frontend port width must be a positive exact integer",
                port=name, width=repr(width)))
        if signed is not None and type(signed) is not bool:
            raise Refused(Refusal(
                "realization-port-signedness-invalid",
                "reported signedness is exactly Boolean or absent; coercing another "
                "value would certify a fact the frontend did not report",
                port=name, signed=repr(signed)))
        if type(driven_by) is not str or driven_by not in {
                "environment", "artifact"}:
            raise Refused(Refusal(
                "realization-port-owner-invalid",
                "a physical port is driven by exactly the environment or artifact",
                port=name, driven_by=repr(driven_by)))
        if clock is not None and (type(clock) is not str or not clock):
            raise Refused(Refusal(
                "realization-port-clock-invalid",
                "a port clock is absent or a nonempty exact domain name",
                port=name, clock=repr(clock)))
        self.name = name
        self.width = width
        #: `None` means the frontend did not report it, which is **not** `False`.
        #:
        #: An SMT2 model carries no signedness at all, so a frontend reading one has
        #: nothing to say; defaulting that to unsigned would silently pass the signedness
        #: check for every design read that way, and a check that always passes is worse
        #: than one that does not run, because it looks like evidence.
        self.signed = signed
        self.driven_by = driven_by
        self.clock = clock
        self._freeze()

    def as_dict(self):
        return {"name": self.name, "width": self.width, "signed": self.signed,
                "driven_by": self.driven_by, "clock": self.clock}

    def __repr__(self):
        return (f"Port({self.name} {'s' if self.signed else 'u'}{self.width} "
                f"by {self.driven_by})")


class Realization(Frozen):
    """What one candidate offers, as the frontend that read it reports.

    ``identity`` is **which** candidate. Without it an `AdmittedTask` says that *some*
    artifact realizes the contract, and nothing stops the run from being of another one:
    admit A, evaluate B, and every refusal admission made was about a design that was not
    under test. A frontend that cannot identify its artifact leaves it empty and the bind
    check is recorded as not run, which is the same rule as everywhere else here.

    ``channel_ports`` maps an endpoint name to the data ports the frontend found on that
    channel. It is what makes the *extra payload* check possible: a port sitting on the
    channel that no field claims is a value the design reads or drives and the task never
    declared, and a binding that quietly ignores it says the transaction carries less
    than it does. A frontend that cannot say leaves the endpoint out, and the check is
    then recorded as not run rather than passed.
    """

    __slots__ = (
        "channel_ports", "identity", "kind", "memories", "ports", "source",
    )

    def __init__(self, kind, ports=(), channel_ports=None, source="", identity="",
                 memories=()):
        if type(kind) is not str or not kind:
            raise Refused(Refusal(
                "realization-kind-invalid",
                "a frontend realization needs a nonempty exact kind"))
        for name, value in (("source", source), ("identity", identity)):
            if type(value) is not str:
                raise Refused(Refusal(
                    f"realization-{name}-invalid",
                    f"a realization's {name} is an exact string",
                    got=repr(value)))
        self.kind = kind
        #: **Frozen all the way down.** These were plain dicts on a record admission's
        #: refusals are about: `realization.ports["in_valid"].driven_by = "environment"`
        #: after `admit` returned turned a refused candidate into an admitted one with
        #: no trace of the change, and `AdmittedTask` is exactly the record that is
        #: supposed to say what was checked.
        try:
            ports = tuple(ports)
        except TypeError:
            raise Refused(Refusal(
                "realization-ports-invalid",
                "a frontend realization carries a finite iterable of Port records")) \
                from None
        if any(type(port) is not Port for port in ports):
            raise Refused(Refusal(
                "realization-port-record-invalid",
                "every frontend port must be an exact Port record"))
        names = [port.name for port in ports]
        if len(set(names)) != len(names):
            raise Refused(Refusal(
                "realization-duplicate-port",
                "a frontend reported two physical ports with one name, so its "
                "realization has no single width, owner or clock for that signal",
                ports=sorted(name for name in set(names)
                             if names.count(name) > 1)))
        self.ports = FrozenMap({port.name: port for port in ports})

        if channel_ports is None:
            channel_ports = {}
        if type(channel_ports) not in {dict, FrozenMap}:
            raise Refused(Refusal(
                "realization-channel-map-invalid",
                "channel_ports must be an exact mapping from endpoint names to "
                "finite port-name collections"))
        channels = {}
        for endpoint, reported in channel_ports.items():
            if type(endpoint) is not str or not endpoint:
                raise Refused(Refusal(
                    "realization-channel-name-invalid",
                    "a realization channel needs a nonempty exact endpoint name"))
            try:
                reported = tuple(reported)
            except TypeError:
                raise Refused(Refusal(
                    "realization-channel-ports-invalid",
                    "a realization channel carries a finite collection of port "
                    "names",
                    endpoint=endpoint)) from None
            if any(type(port) is not str or not port for port in reported):
                raise Refused(Refusal(
                    "realization-channel-port-name-invalid",
                    "every realization channel member is a nonempty exact port name",
                    endpoint=endpoint))
            if len(set(reported)) != len(reported):
                raise Refused(Refusal(
                    "realization-duplicate-channel-port",
                    "a frontend listed one physical port more than once in a "
                    "transaction channel",
                    endpoint=endpoint, ports=sorted(reported)))
            unknown = sorted(set(reported) - set(self.ports))
            if unknown:
                raise Refused(Refusal(
                    "realization-channel-port-unknown",
                    "a frontend assigned a transaction field to a physical port "
                    "that is absent from the same realization",
                    endpoint=endpoint, ports=unknown))
            channels[endpoint] = tuple(sorted(reported))
        self.channel_ports = FrozenMap(channels)
        from .memory import MemoryRealization

        if type(memories) not in (tuple, list) or any(
                type(memory) is not MemoryRealization for memory in memories):
            raise Refused(Refusal(
                "realization-memories-invalid",
                "a frontend realization carries a finite list of exact "
                "MemoryRealization records"))
        memory_names = [memory.name for memory in memories]
        if len(set(memory_names)) != len(memory_names):
            raise Refused(Refusal(
                "realization-memory-duplicate",
                "a frontend reported two external memories with one name",
                memories=memory_names))
        self.memories = tuple(sorted(memories, key=lambda memory: memory.name))
        self.source = source
        self.identity = identity
        self._freeze()

    def as_dict(self):
        return {"kind": self.kind, "identity": self.identity,
                "ports": [self.ports[n].as_dict() for n in sorted(self.ports)],
                "channel_ports": {k: list(v)
                                  for k, v in sorted(self.channel_ports.items())},
                "memories": [memory.as_dict() for memory in self.memories],
                "source": self.source}

    def __repr__(self):
        return f"Realization({self.kind}, {len(self.ports)} ports)"


class AdmittedTask(Frozen):
    """A contract and a candidate that was checked against it, together.

    The pair travels as one record so that nothing downstream can pick up the contract
    and a *different* artifact, and so that "this was admitted" is a thing a report can
    point at rather than a step someone remembers taking.
    """

    __slots__ = ("checks", "contract", "realization", "reference")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("AdmittedTask is a closed admission record")

    def __init__(self, contract, realization, checks=(), reference="", *,
                 _token=None):
        if _token is not _ADMITTED_TASK_TOKEN:
            raise TypeError("use admit() to construct an AdmittedTask")
        if type(contract) is not TaskContract:
            raise TypeError("an admitted task needs the exact TaskContract")
        if type(realization) is not Realization:
            raise TypeError("an admitted task needs the exact Realization")
        if type(checks) not in {dict, FrozenMap} \
                or any(type(name) is not str or not name
                       or value is not None and type(value) is not bool
                       for name, value in checks.items()):
            raise TypeError(
                "admission checks are nonempty string names mapped to bool or None")
        if type(reference) is not str:
            raise TypeError("an admitted task reference identity is an exact string")
        self.contract = contract
        self.realization = realization
        #: The digest of the reference `B*` this task freezes, or `""`.
        #:
        #: **Frozen by the task, not offered by the caller.** Checking that the reference
        #: carries the same interface contract only establishes that it has the same
        #: shape: swap in a different behavior model behind that interface and the run
        #: still reports agreement. What the appendix asks is agreement with *the* `B*`
        #: the task declares, so the task has to say which one, here, before anything
        #: runs -- and an identity string the caller passes in beside the model is not
        #: that, because the caller is who would get it wrong.
        self.reference = str(reference)
        #: Which admission checks ran, by name. A check that could not run -- because the
        #: frontend did not report what it needed -- is listed as `None` rather than
        #: dropped, so a reader can tell a passed check from an absent one.
        self.checks = FrozenMap(checks)
        self._freeze()

    @property
    def unchecked(self):
        return tuple(sorted(k for k, v in self.checks.items() if v is None))

    def binds(self, artifact):
        """Whether ``artifact`` is the one this admission was about.

        Compares the artifact's declared identity against the realization's. An artifact
        that declares none, or a realization that carries none, cannot be checked and
        this says so rather than allowing it: `None` here means the caller has to decide,
        and `evaluate` treats that as a refusal, because "some artifact realizes this
        contract" is not a statement about the one being run.
        """
        mine = self.realization.identity
        theirs = str(getattr(artifact, "identity", "") or "")
        if not mine or not theirs:
            return None
        return mine == theirs

    def as_dict(self):
        return {"contract": self.contract.digest,
                # The frozen `B*`. It was absent here, so a serialized admission did not
                # record which reference its verdicts would be relative to.
                "reference": self.reference,
                "realization": self.realization.as_dict(),
                "checks": dict(self.checks)}

    @property
    def handoff_identity(self):
        """Identity of the complete immutable admission record.

        The task contract digest alone does not bind the realization, checks or frozen
        reference. Exact-worker handoffs carry this identity beside the canonical image
        so changing any of those fields is detected before the worker uses it.
        """

        return _admitted_task_identity(self.as_dict())

    def __repr__(self):
        return (f"AdmittedTask({self.contract!r}, {self.realization!r}, "
                f"{len(self.unchecked)} checks not run)")


def _refuse(kind, why, **detail):
    raise Refused(Refusal(kind, why, **detail))


def _candidate_fault(kind, why, **detail):
    raise FailureRaised(RunFailure.candidate(
        "candidate-admission", Refusal(kind, why, **detail)))


def _physical_bindings(contract):
    """Validate task-owned bindings before inspecting any candidate port."""
    from .physical_value import packed_width

    bound = {}
    for endpoint in contract.endpoints:
        for field in endpoint.fields:
            if field.port is None:
                _refuse("admission-field-unbound",
                        "the task does not bind this field to a physical signal",
                        endpoint=endpoint.name, field=field.name)
            if packed_width(field.type) < 1:
                _refuse("admission-empty-physical-value",
                        "the task field has no physical bit to bind",
                        endpoint=endpoint.name, field=field.name, port=field.port)
            if field.port in bound:
                _refuse("admission-duplicate-binding",
                        "two task fields bind one physical signal",
                        port=field.port, fields=sorted([bound[field.port], field.name]))
            bound[field.port] = field.name


def _admitted_task_identity(value):
    """Hash one canonical admission image for a trusted process handoff."""

    if type(value) is AdmittedTask:
        value = value.as_dict()
    if type(value) is not dict:
        raise TypeError("an admission handoff identity needs an exact mapping")
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _realization_from_record(value):
    """Decode the complete canonical realization nested in an admission image."""

    fields = {"kind", "identity", "ports", "channel_ports", "memories", "source"}
    if type(value) is not dict or set(value) != fields:
        raise TypeError("an admission realization image has its complete field set")
    if any(type(value[name]) is not str
           for name in ("kind", "identity", "source")):
        raise TypeError("admission realization identities are exact strings")
    ports_image = value["ports"]
    if type(ports_image) is not list:
        raise TypeError("an admission realization carries an exact port list")
    ports = []
    port_fields = {"name", "width", "signed", "driven_by", "clock"}
    for index, image in enumerate(ports_image):
        if type(image) is not dict or set(image) != port_fields:
            raise TypeError(
                f"admission realization port {index} has its complete field set")
        if type(image["name"]) is not str \
                or type(image["width"]) is not int \
                or image["signed"] is not None \
                   and type(image["signed"]) is not bool \
                or type(image["driven_by"]) is not str \
                or image["clock"] is not None \
                   and type(image["clock"]) is not str:
            raise TypeError(
                f"admission realization port {index} has exact field types")
        ports.append(Port(
            image["name"], image["width"], signed=image["signed"],
            driven_by=image["driven_by"], clock=image["clock"]))
    channels = value["channel_ports"]
    if type(channels) is not dict or any(
            type(name) is not str or type(names) is not list
            or any(type(port) is not str for port in names)
            for name, names in channels.items()):
        raise TypeError(
            "admission realization channel ports are exact string lists")
    memories = value["memories"]
    if type(memories) is not list:
        raise TypeError("an admission realization carries an exact memory list")
    from .memory import MemoryRealization

    got = Realization(
        value["kind"], ports, channel_ports=channels,
        source=value["source"], identity=value["identity"],
        memories=[MemoryRealization.from_dict(memory) for memory in memories])
    if got.as_dict() != value:
        raise ValueError("an admission realization image is not canonical")
    return got


def _admitted_task_from_record(contract, value, *, expected_identity):
    """Strictly reconstruct one task admitted by another trusted process.

    This is the only construction path besides :func:`admit`. It does not perform a
    second admission: it requires the complete immutable image and the identity frozen
    independently by the sending context, reconstructs every nested value, and accepts
    only an exact canonical round trip.
    """

    if type(contract) is not TaskContract:
        raise TypeError("an admission handoff needs the exact TaskContract")
    if type(expected_identity) is not str or len(expected_identity) != 64 \
            or any(char not in "0123456789abcdef" for char in expected_identity):
        raise TypeError("an admission handoff identity is a lowercase SHA-256 digest")
    fields = {"contract", "reference", "realization", "checks"}
    if type(value) is not dict or set(value) != fields:
        raise TypeError("an admission handoff image has its complete field set")
    if type(value["contract"]) is not str \
            or value["contract"] != contract.digest:
        raise Refused(Refusal(
            "admission-handoff-contract-mismatch",
            "the admitted-task handoff and decoded TaskContract have different "
            "identities"))
    if type(value["reference"]) is not str:
        raise TypeError("an admission handoff reference is an exact string")
    checks = value["checks"]
    if type(checks) is not dict or any(
            type(name) is not str or not name
            or status is not None and type(status) is not bool
            for name, status in checks.items()):
        raise TypeError(
            "an admission handoff has string checks mapped to bool or None")
    realization = _realization_from_record(value["realization"])
    got = AdmittedTask(
        contract, realization, checks, reference=value["reference"],
        _token=_ADMITTED_TASK_TOKEN)
    if got.as_dict() != value:
        raise ValueError("an admitted-task handoff image is not canonical")
    if got.handoff_identity != expected_identity:
        raise Refused(Refusal(
            "admission-handoff-identity-mismatch",
            "the canonical admitted-task image changed after its sending context "
            "froze the handoff identity"))
    return got


def admit(contract, realization, reference=None):
    """Check ``realization`` against ``contract``, or refuse by name.

    ``reference`` is the task's own `B*`, whose digest is frozen into the result. Given
    as the thing itself rather than as a digest so that the digest is computed here, by
    the code that will later check it, rather than being asserted by whoever calls.

    Returns an `AdmittedTask`. A proven physical mismatch raises `FailureRaised`
    with candidate attribution; task/capability refusals stay `Refused`.
    """
    checks = {}

    # An adapter this build does not implement is a capability refusal and not a fallback
    # to whichever protocol the code happens to have. Running a start/done channel under
    # ready/valid semantics would produce a schedule whose slots mean something the task
    # never declared.
    for e in contract.endpoints:
        if (e.adapter.name, e.adapter.version) not in SUPPORTED_ADAPTERS:
            _refuse("admission-adapter-unsupported",
                    "this endpoint names a protocol adapter this build does not "
                    "implement, and applying the one it does have would give the "
                    "recorded schedule a meaning the task did not declare",
                    endpoint=e.name, adapter=e.adapter.name,
                    version=e.adapter.version,
                    supported=sorted(f"{n}/v{v}" for n, v in SUPPORTED_ADAPTERS))
    # A supported endpoint name is not yet a supported pair.  The executable registry
    # validates the whole channel so a mixed pair never falls through to either
    # implementation with half of its transition relation missing.
    if len(contract.clocks) > 1:
        from .atomic import atomic_adapter_programs

        atomic_adapter_programs(contract)
    else:
        adapter_program_for(contract)
    checks["adapter_supported"] = True
    _physical_bindings(contract)

    ports = realization.ports

    # ── the payload ──
    # One declared field maps to one packed port.  The shared codec is task-owned and
    # versioned in TaskContract, so admission, concrete replay and symbolic RTL cannot
    # independently choose a shape.
    from .physical_value import packed_signed, packed_width

    checks["physical_value_layout"] = True

    signedness_checked = True
    for e in contract.endpoints:
        for f in e.fields:
            port = ports.get(f.port)
            if port is None:
                _candidate_fault("admission-missing-payload",
                        "the contract declares this field and the candidate has no "
                        "such signal, so there is nothing to carry it",
                        endpoint=e.name, field=f.name, port=f.port,
                        has=sorted(ports)[:12])
            width = packed_width(f.type)
            signed = packed_signed(f.type)
            if port.width < width:
                _candidate_fault("admission-narrower-field",
                        "the candidate's signal is narrower than the field the task "
                        "declares. A narrower contract admits fewer inputs and can only "
                        "enlarge the set of goals reported unreachable, so the artifact "
                        "under test would be choosing its own denominator",
                        endpoint=e.name, field=f.name, port=f.port,
                        declared=width, found=port.width)
            if port.width > width:
                _candidate_fault("admission-wider-field",
                        "the candidate's signal is wider than the field the task "
                        "declares, so the design reads bits the task says nothing "
                        "about and two runs can differ by a value the contract cannot "
                        "express",
                        endpoint=e.name, field=f.name, port=f.port,
                        declared=width, found=port.width)
            # **The direction of the payload port**, which nothing checked. A candidate
            # declaring an input endpoint's data port as an output realizes a different
            # interface entirely -- the environment could not drive it -- and it passed
            # admission because only the handshake signals had their ownership checked.
            want = ("environment" if e.direction is Direction.IN else "artifact")
            if port.driven_by != want:
                _candidate_fault("admission-payload-direction",
                        "this field's signal is driven by the wrong side. The contract "
                        "puts the field on an endpoint whose payload the other side "
                        "drives, so the candidate realizes a different interface: on an "
                        "input endpoint the environment presents the data, and on an "
                        "output endpoint the artifact does",
                        endpoint=e.name, field=f.name, port=f.port,
                        declared=want, found=port.driven_by)
            if port.signed is None:
                signedness_checked = False
            elif port.signed != signed:
                _candidate_fault("admission-signedness",
                        "the candidate reads this signal with a different signedness "
                        "than the task declares, so the two admit different values and "
                        "a comparison across them is not a comparison",
                        endpoint=e.name, field=f.name, port=f.port,
                        declared="signed" if signed else "unsigned",
                        found="signed" if port.signed else "unsigned")
    checks["payload_width"] = True
    checks["payload_signedness"] = True if signedness_checked else None

    # An extra data port on a channel is a value the design carries and the task never
    # declared. Only checkable where the frontend reported the channel's ports -- and
    # **per endpoint**, not once for the whole realization. It used to be a single
    # boolean, so a frontend that reported the input channel and not the output one had
    # the check recorded as run: one endpoint's material covered for another's absence,
    # which is the shape of every "the check passed" that never looked.
    for e in contract.endpoints:
        name = f"no_extra_payload[{e.name}]"
        found = realization.channel_ports.get(e.name)
        if found is None:
            checks[name] = None
            continue
        claimed = {f.port for f in e.fields}
        unclaimed = sorted(set(found) - claimed)
        if unclaimed:
            _candidate_fault("admission-extra-payload",
                    "the candidate carries data on this channel that no field of the "
                    "contract claims, so the transaction carries more than the task "
                    "says it does and nothing would compare the rest",
                    endpoint=e.name, unclaimed=unclaimed,
                    declared=sorted(claimed))
        checks[name] = True

    extra_channels = sorted(set(realization.channel_ports) - {
        endpoint.name for endpoint in contract.endpoints
    })
    if extra_channels:
        _candidate_fault("admission-extra-channel",
                "the frontend reports transaction channels the task contract does "
                "not declare, so their transfers would be omitted from the score",
                channels=extra_channels)
    checks["no_extra_channels"] = True

    # ── the handshake ──
    for e in contract.endpoints:
        for signal in (e.adapter.environment_controls
                       + e.adapter.artifact_controls):
            port = ports.get(signal)
            # Every registered control signal is one bit. A wider signal has values the
            # adapter's transition relation does not interpret.
            if port is not None and port.width != 1:
                _candidate_fault("admission-handshake-width",
                        "a protocol control signal carries one bit, and this one "
                        "is wider; what its other bits mean is undeclared, and the "
                        "adapter's semantics are written over a signal that is either "
                        "asserted or not",
                        endpoint=e.name, signal=signal, width=port.width)
        for signal in e.adapter.environment_controls:
            port = ports.get(signal)
            if port is None:
                _candidate_fault("admission-handshake-missing",
                        "the adapter says the environment drives this signal and the "
                        "candidate does not have it, so the handshake the contract "
                        "declares cannot be carried out",
                        endpoint=e.name, signal=signal)
            if port.driven_by != "environment":
                _candidate_fault("admission-handshake-ownership",
                        "the adapter says the environment drives this signal and the "
                        "candidate drives it. A channel whose back-pressure the "
                        "environment cannot withhold is a channel whose stall behaviour "
                        "nothing tests",
                        endpoint=e.name, signal=signal,
                        declared="environment", found=port.driven_by)
        for signal in e.adapter.artifact_controls:
            port = ports.get(signal)
            if port is None:
                _candidate_fault("admission-handshake-missing",
                        "the adapter says the artifact drives this signal and the "
                        "candidate does not have it",
                        endpoint=e.name, signal=signal)
            if port.driven_by != "artifact":
                _candidate_fault("admission-handshake-ownership",
                        "the adapter says the artifact drives this signal and the "
                        "environment drives it here, so the environment would be "
                        "answering its own handshake",
                        endpoint=e.name, signal=signal,
                        declared="artifact", found=port.driven_by)
    checks["handshake_ownership"] = True

    # ── the clock ──
    clock_checked = False
    for e in contract.endpoints:
        for f in e.fields:
            port = ports[f.port]
            if port.clock is None:
                continue
            clock_checked = True
            if port.clock != e.clock:
                _candidate_fault("admission-wrong-clock-domain",
                        "this field's signal is in a different clock domain than the "
                        "endpoint the task puts it on, so its events happen at moments "
                        "the endpoint's schedule does not name",
                        endpoint=e.name, field=f.name, port=f.port,
                        declared=e.clock, found=port.clock)
    checks["clock_domain"] = True if clock_checked else None

    for domain in contract.clocks:
        for signal in (domain.clock, domain.reset):
            if signal not in ports:
                _candidate_fault("admission-clock-missing",
                        "the contract declares this clock domain and the candidate has "
                        "no such signal, so nothing says when a slot happens",
                        domain=domain.name, signal=signal)
            # **The clock too**, which nothing checked: only the reset's ownership was.
            # A candidate that drives its own clock decides when a slot happens, and a
            # schedule of slots then describes moments the environment does not control.
            if ports[signal].driven_by != "environment":
                _candidate_fault("admission-clock-ownership",
                        "the clock and the reset are the task's and the environment "
                        "drives them; a candidate that drives its own decides when a "
                        "slot happens or where a run starts",
                        domain=domain.name, signal=signal,
                        found=ports[signal].driven_by)
            if ports[signal].width != 1:
                _candidate_fault("admission-clock-width",
                        "a clock or a reset carries one bit",
                        domain=domain.name, signal=signal,
                        width=ports[signal].width)
    checks["clock_and_reset"] = True

    # ── external memory ──
    # The task owns protocol semantics; the frontend independently reports only the
    # candidate's physical realization.  Compare both the logical grouping and every
    # underlying pin so a detector cannot hide a malformed port by simply omitting the
    # group it failed to recognise.
    from .memory import memory_port_prefix

    declared_memories = {memory.name: memory for memory in contract.memories}
    realized_memories = {memory.name: memory for memory in realization.memories}
    missing = sorted(set(declared_memories) - set(realized_memories))
    extra = sorted(set(realized_memories) - set(declared_memories))
    if missing:
        _refuse(
            "admission-memory-missing",
            "the task declares an external memory the candidate does not realize",
            memories=missing)
    if extra:
        _refuse(
            "admission-memory-extra",
            "the candidate exposes an external-memory protocol the task does not "
            "declare, so its effects would be absent from the trace",
            memories=extra)
    for name, declared in declared_memories.items():
        found = realized_memories[name]
        if (found.addr_width != declared.addr_width
                or found.data_width != declared.data_width
                or found.clock != declared.clock
                or tuple((port.id, port.kind) for port in found.ports)
                   != tuple((port.id, port.kind) for port in declared.ports)):
            _refuse(
                "admission-memory-shape",
                "the candidate realizes a different external-memory width, port kind, "
                "port set, or clock domain than the task declares",
                memory=name, declared=declared.as_dict(), found=found.as_dict())
        for memory_port in declared.ports:
            prefix = memory_port_prefix(name, memory_port.id)
            expected = {
                prefix + "_req_valid": (1, "artifact"),
                prefix + "_req_addr": (declared.addr_width, "artifact"),
                prefix + "_req_ready": (1, "environment"),
            }
            if memory_port.reads:
                expected.update({
                    prefix + "_resp_valid": (1, "environment"),
                    prefix + "_resp_rdata": (
                        declared.data_width, "environment"),
                })
            if memory_port.writes:
                expected[prefix + "_req_wdata"] = (
                    declared.data_width, "artifact")
            if memory_port.kind == "rw":
                expected[prefix + "_req_we"] = (1, "artifact")
            for signal, (width, owner) in expected.items():
                physical = ports.get(signal)
                if physical is None:
                    _candidate_fault(
                        "admission-memory-signal-missing",
                        "a declared external-memory port signal is absent from the "
                        "candidate realization",
                        memory=name, port=memory_port.id, signal=signal)
                if physical.width != width or physical.driven_by != owner:
                    _candidate_fault(
                        "admission-memory-signal-shape",
                        "an external-memory signal has the wrong width or owner",
                        memory=name, port=memory_port.id, signal=signal,
                        declared_width=width, found_width=physical.width,
                        declared_owner=owner, found_owner=physical.driven_by)
    checks["external_memory_realization"] = True

    frozen = ""
    if reference is not None:
        # A bundle, or the identity a bundle carries. **Not a live model**: a model
        # beside an identity is a pair a caller assembled, and the pair is what
        # `load_reference` exists to make unassemblable.
        artifact = getattr(reference, "artifact", None)
        if artifact is None and hasattr(reference, "digest"):
            artifact = reference
        if artifact is None or not getattr(artifact, "digest", ""):
            _refuse("admission-reference-unidentifiable",
                    "this reference carries no `ReferenceArtifact`, so the task cannot "
                    "freeze which `B*` its verdicts are relative to. Digesting a live "
                    "object was tried and does not work: `inspect.getsource(type(m))` "
                    "binds the class body and not the constructor arguments, so two "
                    "models differing only in configuration shared an identity, and a "
                    "model loaded with `exec` has no retrievable source at all and the "
                    "digest degraded to its class name. What identifies a reference is "
                    "what the loader fed it",
                    reference=type(reference).__name__)
        frozen = artifact.digest
        from .reference import AtomicReferenceModel, LoadedReference

        several_endpoints = (
            len(contract.inputs()) > 1 or len(contract.outputs()) > 1)
        if type(reference) is LoadedReference and len(contract.clocks) > 1:
            # Every multi-clock trace is interpreted by the separately versioned atomic
            # reference capability, even where each domain has only one endpoint.
            # Checking for process_slot on the ordinary transaction model would both
            # miss a loader with no atomic semantics and reject the process_<domain>
            # models the registered adapter exists to compose.
            atomic = AtomicReferenceModel(reference, contract)
            atomic.close()
            checks["reference_atomic_interface"] = True
        elif several_endpoints and type(reference) is LoadedReference:
            model = reference.new_model()
            try:
                if not hasattr(model, "process_slot"):
                    _refuse(
                        "admission-reference-slot-interface-missing",
                        "a one-clock task with several transaction endpoints needs an "
                        "explicit process_slot reference; serial process calls would "
                        "invent an order among simultaneous transfers",
                        inputs=[endpoint.name for endpoint in contract.inputs()],
                        outputs=[endpoint.name for endpoint in contract.outputs()])
            finally:
                close = getattr(model, "close", None)
                if callable(close):
                    close()
            checks["reference_slot_interface"] = True
        elif several_endpoints:
            checks["reference_slot_interface"] = None
    checks["reference_frozen"] = True if reference is not None else None
    return AdmittedTask(
        contract, realization, checks, reference=frozen,
        _token=_ADMITTED_TASK_TOKEN)
