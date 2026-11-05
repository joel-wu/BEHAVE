"""What the task declares, with nothing left for an artifact to decide.

TODO3 T3-01. One immutable, task-owned record naming the logical endpoints, the fields
each transaction carries and their finite types, the channel bindings, clock and reset
ownership, the environment-owned controls, and each endpoint's output matching policy.
Candidate artifacts **realize** this; they do not choose its widths, signedness, endpoint
set, or score denominator.

Handshake direction and clock ownership belong to the task. The candidate's physical
interface is checked against this declaration; it cannot define its own contract.

**The adapter is part of the contract's identity.** An endpoint names a versioned
protocol adapter, and the adapter is what says how scheduled signal events offer, accept
and produce logical transactions, which controls the environment owns, and what finite
monitor state a trace carries. Ready/valid is the standard one. A task naming an adapter
this build does not implement gets a typed capability refusal -- it does not get
ready/valid semantics applied to a start/done channel because that is the code that
exists. Changing the adapter version changes the contract identity, because it changes
what the recorded schedule means.

**Widths are finite types and are declared, not inferred.** A candidate declaring a port
one bit narrower than the task narrows the contract, and narrowing the contract can only
enlarge the set of goals reported unreachable: the artifact under test would be choosing
its own denominator. `core/schema.py` already says this about the port schema; this says
it about the whole task.
"""

from __future__ import annotations

import builtins
import enum
import hashlib

from .frozen import Frozen
from .interface import ProtocolInstance, check_protocol_instances
from .memory import MemoryInterface, MemoryPort, memory_port_signal_set
from .protocol import (
    APB_ACCESS,
    AXI_LITE_READ,
    AXI_LITE_WRITE,
    READY_VALID,
    SAMPLED_EDGE,
    SETTLED,
    START_DONE,
    supported_adapter_identities,
)
from .refusal import Refusal, Refused
from .stimulus import canonical_json

__all__ = [
    "APB_ACCESS",
    "AXI_LITE_READ",
    "AXI_LITE_WRITE",
    "READY_VALID",
    "SAMPLED_EDGE",
    "SETTLED",
    "START_DONE",
    "SUPPORTED_ADAPTERS",
    "TASK_CONTRACT_VERSION",
    "Adapter",
    "ClockDomain",
    "Direction",
    "Endpoint",
    "Field",
    "FiniteType",
    "Kind",
    "Matching",
    "MemoryInterface",
    "MemoryPort",
    "ProtocolInstance",
    "ResetPolicy",
    "TaskContract",
    "ValueType",
    "apb_access",
    "axi_lite_read",
    "axi_lite_write",
    "ready_valid",
    "sampled_edge",
    "settled",
    "start_done",
]

# Re-exported here because this module is the established task-contract import
# surface.  ``ValueType`` itself imports ``FiniteType`` only inside methods, so
# this import is safe after the scalar carrier has been defined below; the
# actual binding is installed at end of module.


#: Version of the strict JSON image accepted by :meth:`TaskContract.from_dict`.
#: This is a representation identity, not part of the task's semantic digest.
_TASK_CONTRACT_REVISION = 17
TASK_CONTRACT_VERSION = f"task-contract/{_TASK_CONTRACT_REVISION}"


class Direction(enum.Enum):
    """Which way a transaction travels, from the environment's point of view."""

    IN = "in"
    OUT = "out"


class Matching(enum.Enum):
    """How an observed output transaction is compared with an expected one.

    T3-02's three, and **ordered is the default**. The current matcher always compares
    content, which passes `[2, 1]` against an expectation of `[1, 2]`; the appendix
    defaults to ordered and permits the other two only when the task declares them, so
    that a run relying on one is a run whose report can say so.
    """

    #: Position by position. The default, and the only one needing no declaration.
    ORDERED = "ordered"
    #: Correlated by an identifier the task declares unique among outstanding
    #: transactions. Reordering is admitted; a duplicate identifier is not.
    IDENTIFIER = "identifier"
    #: As a multiset. Admits any reordering, so it is only sound where the task says the
    #: order carries nothing, and it is recorded as an assumption.
    CONTENT = "content"


class ResetPolicy(enum.Enum):
    """What a reset does to transactions the design has accepted and not answered.

    Undeclared is a third state and the default: with neither policy stated, a run in
    which a reset arrives while something is in flight cannot be judged on any
    obligation that counts outstanding transactions, and the monitor records those as
    unresolved from that slot on rather than picking one of the two.
    """

    #: Reset discards them: nothing is owed afterwards.
    CLEARS = "clears"
    #: Reset preserves them: the design still owes what it took before.
    KEEPS = "keeps"


def _declared_enum(enum_type, value, refusal, what, **detail):
    """Decode one public enum value without host-language coercion errors.

    Constructors accept the enum itself and its documented string image.  Anything
    else is a malformed task declaration, so expose it through the same typed refusal
    boundary as the other contract facts instead of leaking ``Enum``'s ``ValueError``
    or ``TypeError``.
    """

    if builtins.type(value) is enum_type:
        return value
    if builtins.type(value) is str:
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise Refused(Refusal(
        refusal, what, got=repr(value), **detail))


class FiniteType(Frozen):
    """One field's carrier: a width and a signedness, both declared.

    A type and not a width, because "8 bits read as signed" and "8 bits read as
    unsigned" admit different values and a comparison across the two is not a
    comparison. The pair is what every later encoding is injective **into**.
    """

    __slots__ = ("signed", "width")

    def __init__(self, width, signed=False):
        if type(width) is not int:
            raise Refused(Refusal(
                "contract-type-width-not-integer",
                "a finite field width must be an integer rather than a "
                "coerced host value",
                width=repr(width)))
        if width < 1:
            raise Refused(Refusal(
                "contract-empty-type",
                "a field carrying no bits carries no value, and a contract that admits "
                "one has an endpoint nothing can be said about",
                width=width))
        if type(signed) is not bool:
            raise Refused(Refusal(
                "contract-type-signedness-invalid",
                "a finite field's signedness is an exact Boolean task fact",
                signed=repr(signed)))
        self.width = width
        self.signed = signed
        self._freeze()

    @property
    def bounds(self):
        if self.signed:
            return -(1 << (self.width - 1)), (1 << (self.width - 1)) - 1
        return 0, (1 << self.width) - 1

    def admits(self, value):
        low, high = self.bounds
        return type(value) is int and low <= value <= high

    def as_dict(self):
        return {"width": self.width, "signed": self.signed}

    @classmethod
    def from_dict(cls, value):
        """Decode the exact finite JSON image emitted by :meth:`as_dict`."""

        if type(value) is not dict or set(value) != {"width", "signed"}:
            raise TypeError("a finite type image has exactly width and signed")
        if type(value["width"]) is not int or type(value["signed"]) is not bool:
            raise TypeError("finite type width/signed have exact int/bool types")
        return cls(value["width"], value["signed"])

    def __eq__(self, other):
        return (isinstance(other, FiniteType) and self.width == other.width
                and self.signed == other.signed)

    def __hash__(self):
        return hash((self.width, self.signed))

    def __repr__(self):
        return f"{'s' if self.signed else 'u'}{self.width}"


class Field(Frozen):
    """One field of a transaction, and the physical signal that carries it.

    ``port`` is declared rather than derived. A field named `sel` on a port named
    `in_sel` is a binding someone stated, and a rule that strips a prefix silently binds
    the wrong port the first time a design does not follow it.
    """

    __slots__ = ("name", "port", "type")

    def __init__(self, name, type, port=None):
        from .value_type import ValueType, as_value_type

        # ``type`` is the established public parameter name.  Use the builtin
        # explicitly so exact host-type checks do not accidentally call the
        # declared value type as a function.
        if builtins.type(name) is not str or not name:
            raise Refused(Refusal(
                "contract-field-name-invalid",
                "a transaction field needs a nonempty exact string name",
                got=repr(name)))
        if port is not None and (builtins.type(port) is not str or not port):
            raise Refused(Refusal(
                "contract-field-port-invalid",
                "a physical field binding is null or a nonempty exact string",
                field=name, got=repr(port)))
        self.name = name
        # FiniteType remains the exact flat Integer shorthand.  Normalize it
        # immediately so task fields, Behavior signatures and source bindings
        # all carry the same recursive ValueType authority.
        if (builtins.type(type) is not FiniteType
                and builtins.type(type) is not ValueType):
            raise Refused(Refusal(
                "contract-field-type-invalid",
                "a transaction field carries an exact finite or recursive task type",
                field=name, got=builtins.type(type).__name__))
        self.type = as_value_type(type)
        #: The physical signal, or `None` for a field the task declares logically and
        #: leaves an artifact to bind. `None` is not "the same name": it is "unstated",
        #: and admission refuses to guess.
        self.port = port
        self._freeze()

    def as_dict(self):
        # Preserve the task-contract/2 scalar image.  Structured values carry
        # their own versioned image and cannot be mistaken for a width record.
        image = (self.type.carrier.as_dict()
                 if self.type.kind == "integer" else self.type.as_dict())
        return {"name": self.name, "type": image, "port": self.port}

    @classmethod
    def from_dict(cls, value):
        """Decode the exact finite JSON image emitted by :meth:`as_dict`."""

        if type(value) is not dict or set(value) != {"name", "type", "port"}:
            raise TypeError("a field image has exactly name, type and port")
        if type(value["name"]) is not str \
                or value["port"] is not None and type(value["port"]) is not str:
            raise TypeError("field name/port have exact string types")
        from .value_type import VALUE_TYPE_VERSION, ValueType

        image = value["type"]
        declared = (ValueType.from_dict(image)
                    if type(image) is dict
                    and image.get("version") == VALUE_TYPE_VERSION
                    else FiniteType.from_dict(image))
        return cls(value["name"], declared, value["port"])

    def __repr__(self):
        return f"Field({self.name}:{self.type} on {self.port})"


class Adapter(Frozen):
    """A protocol, by name and version, and what it makes the environment own.

    Versioned because it decides what a recorded schedule *means*. Ready/valid v1 says a
    slot's `in_valid` is the environment's and `in_ready` is the artifact's, and that a
    transfer is the cycle both hold; a later version that changed, say, whether the
    payload must be stable under stall would make every recorded schedule mean something
    else. So the version is in the contract's identity rather than in a comment.
    """

    __slots__ = (
        "artifact_controls", "environment_controls", "monitor_state", "name",
        "version",
    )

    def __init__(self, name, version, environment_controls=(), artifact_controls=(),
                 monitor_state=()):
        if type(name) is not str or not name:
            raise Refused(Refusal(
                "contract-adapter-name-invalid",
                "a protocol adapter needs a nonempty exact string name",
                got=repr(name)))
        if type(version) is not int or version < 1:
            raise Refused(Refusal(
                "contract-adapter-version-invalid",
                "a protocol adapter version is a positive exact integer",
                adapter=name, got=repr(version)))
        self.name = name
        self.version = version

        def names(values, label):
            if type(values) not in (tuple, list) or any(
                    type(value) is not str or not value for value in values):
                raise Refused(Refusal(
                    "contract-adapter-signal-invalid",
                    "adapter controls and monitor fields are finite lists of "
                    "nonempty exact strings",
                    adapter=name, field=label))
            if len(values) != len(set(values)):
                raise Refused(Refusal(
                    "contract-adapter-signal-duplicate",
                    "one adapter role lists the same signal or monitor field twice",
                    adapter=name, field=label, values=list(values)))
            return tuple(sorted(values))

        #: Signals the environment drives. A schedule must name every one of them in
        #: every slot: anything it leaves out is chosen by whatever runs the schedule,
        #: which is the defect `docs/single_clock_semantics.md` item 3 is about.
        self.environment_controls = names(
            environment_controls, "environment_controls")
        #: Signals the artifact drives. A contract that let the environment drive one of
        #: these would be a contract in which the handshake cannot fail.
        self.artifact_controls = names(artifact_controls, "artifact_controls")
        #: The finite monitor state this protocol contributes to a trace, by name.
        self.monitor_state = names(monitor_state, "monitor_state")
        overlap = sorted(set(self.environment_controls)
                         & set(self.artifact_controls))
        if overlap:
            raise Refused(Refusal(
                "contract-adapter-control-owner-conflict",
                "one protocol pin cannot be driven by both the environment and the "
                "artifact",
                adapter=name, signals=overlap))
        self._freeze()

    def as_dict(self):
        return {"name": self.name, "version": self.version,
                "environment_controls": list(self.environment_controls),
                "artifact_controls": list(self.artifact_controls),
                "monitor_state": list(self.monitor_state)}

    @classmethod
    def from_dict(cls, value):
        """Decode the exact finite JSON image emitted by :meth:`as_dict`."""

        keys = {"name", "version", "environment_controls",
                "artifact_controls", "monitor_state"}
        if type(value) is not dict or set(value) != keys:
            raise TypeError("an adapter image has the complete adapter field set")
        if type(value["name"]) is not str or type(value["version"]) is not int \
                or isinstance(value["version"], bool):
            raise TypeError("adapter name/version have exact string/integer types")
        controls = (value["environment_controls"], value["artifact_controls"],
                    value["monitor_state"])
        if any(type(items) is not list
               or any(type(item) is not str for item in items)
               for items in controls):
            raise TypeError("adapter control/state images are lists of strings")
        return cls(value["name"], value["version"], *controls)

    def __eq__(self, other):
        return isinstance(other, Adapter) and self.as_dict() == other.as_dict()

    def __repr__(self):
        return f"Adapter({self.name}/v{self.version})"


#: The standard adapter. `valid` is offered by whoever sends and `ready` by whoever
#: receives, so which of the two the environment owns depends on the endpoint's
#: direction; `for_direction` resolves that rather than leaving each caller to.
#: Adapters this build implements. A task naming anything else is refused by name at
#: admission, rather than being run under whichever adapter the code happens to have.
SUPPORTED_ADAPTERS = supported_adapter_identities()


def ready_valid(direction, valid, ready, version=1):
    """The standard adapter for one endpoint, with the ownership the direction implies.

    An input endpoint: the environment sends, so it drives `valid` and the artifact
    answers `ready`. An output endpoint: the artifact sends, so it drives `valid` and the
    environment answers `ready`. Getting this backwards is one of the admission refusals,
    and it is worth a function rather than a convention because the two are mirror images
    and a reader checking a literal cannot see which one is wrong.
    """
    direction = _declared_enum(
        Direction, direction, "contract-direction-invalid",
        "an endpoint direction is 'in', 'out', or the corresponding Direction",
        endpoint="ready-valid adapter")
    if direction is Direction.IN:
        env, art = (valid,), (ready,)
    else:
        env, art = (ready,), (valid,)
    return Adapter(READY_VALID, version, environment_controls=env,
                   artifact_controls=art,
                   monitor_state=("outstanding", "stall_cycles"))


def sampled_edge(direction, *, valid=None, version=1):
    """Sample at each non-reset active clock boundary, optionally gated by valid.

    ``valid=None`` declares plain pins; every boundary is a logical transaction.
    Otherwise the sender owns one valid pin. Neither form has ready/backpressure.
    RTL samples after control settling and before committing that clock edge.
    Clockless or post-edge sampling requires a different, explicit configuration.
    """
    direction = _declared_enum(
        Direction, direction, "contract-direction-invalid",
        "an endpoint direction is 'in', 'out', or the corresponding Direction",
        endpoint="sampled-edge adapter")
    pins = () if valid is None else (valid,)
    return Adapter(
        SAMPLED_EDGE, version,
        environment_controls=pins if direction is Direction.IN else (),
        artifact_controls=pins if direction is Direction.OUT else ())


def settled(direction, *, version=1):
    """Plain clockless pins, sampled once after each driven input settles."""
    _declared_enum(Direction, direction, "contract-direction-invalid",
                   "an endpoint direction is 'in' or 'out'", endpoint="settled adapter")
    return Adapter(SETTLED, version)


def start_done(direction, start, done, version=1):
    """The start/done v1 binding for one endpoint.

    The input endpoint carries the environment-owned one-cycle ``start`` pulse.  The
    output endpoint carries the artifact-owned ``done`` pulse.  There is deliberately
    no ready signal: the finite adapter state decides whether a start is accepted and a
    start while busy is a protocol violation, not hidden back-pressure.
    """
    direction = _declared_enum(
        Direction, direction, "contract-direction-invalid",
        "an endpoint direction is 'in', 'out', or the corresponding Direction",
        endpoint="start-done adapter")
    if direction is Direction.IN:
        env, art = (start,), ()
    else:
        env, art = (), (done,)
    return Adapter(START_DONE, version, environment_controls=env,
                   artifact_controls=art, monitor_state=("age", "busy"))


def apb_access(direction, *, select, enable, ready, version=1):
    """APB control ownership for the completed-access logical mapping.

    The instance binds the select/enable/ready roles explicitly. Request fields
    carry address, direction and write data as declared; response fields carry
    read data/status. In particular PSLVERR is payload, not a protocol failure.
    """
    direction = _declared_enum(
        Direction, direction, "contract-direction-invalid",
        "an APB endpoint direction is in or out", endpoint="apb-access adapter")
    return Adapter(
        APB_ACCESS, version,
        environment_controls=(select, enable) if direction is Direction.IN else (),
        artifact_controls=(ready,) if direction is Direction.OUT else (),
        monitor_state=("request", "wait_cycles", "waiting"))


def _axi_lite_adapter(direction, name, request, response, version):
    direction = _declared_enum(
        Direction, direction, "contract-direction-invalid",
        "an AXI4-Lite endpoint direction is in or out", endpoint=name)
    env, art = (tuple(pair[0] for pair in request), tuple(pair[1] for pair in request)) \
        if direction is Direction.IN else ((response[1],), (response[0],))
    return Adapter(name, version, environment_controls=env, artifact_controls=art,
                   monitor_state=("accepted_channels", "pending", "request", "wait_cycles"))


def axi_lite_write(direction, *, awvalid, awready, wvalid, wready, bvalid, bready, version=1):
    """Independent AW/W controls; the instance binds fields and one outstanding write."""
    return _axi_lite_adapter(direction, AXI_LITE_WRITE,
                             ((awvalid, awready), (wvalid, wready)), (bvalid, bready), version)


def axi_lite_read(direction, *, arvalid, arready, rvalid, rready, version=1):
    """AR/R controls for one outstanding read, composable with the write instance."""
    return _axi_lite_adapter(direction, AXI_LITE_READ,
                             ((arvalid, arready),), (rvalid, rready), version)


class Kind(enum.Enum):
    """The logical channel interpretation, declared rather than inferred from pins.

    A transaction stream may come from handshakes or an explicitly selected sampling
    adapter. Back-to-back transfers need no intervening valid-low cycle. Continuously
    meaningful signals without such a mapping remain SAMPLED and cannot be scored as
    an inferred transaction stream.
    """

    #: A logical transaction sequence under the endpoint's declared adapter.
    TRANSACTION = "transaction"
    #: Continuously meaningful signals, sampled. Not a transaction channel, and this
    #: build's adapter does not describe one.
    SAMPLED = "sampled"
    #: Nobody said. Not a default and not a guess -- a run of a channel whose kind is
    #: unknown establishes nothing about it either way.
    UNDECLARED = "undeclared"


class Endpoint(Frozen):
    """One logical endpoint: what it carries, how it handshakes, how it is compared."""

    __slots__ = (
        "adapter", "answers_per_transaction", "clock", "direction", "fields", "framing",
        "identifier", "kind", "matching", "max_outstanding", "name",
        "reset_policy", "response_deadline", "terminal_quiescence",
    )

    def __init__(self, name, direction, fields, adapter, clock,
                 matching=Matching.ORDERED, identifier=None, max_outstanding=None,
                 response_deadline=None, answers_per_transaction=None,
                 terminal_quiescence=None, kind=Kind.UNDECLARED,
                 reset_policy=None, framing=None):
        if type(name) is not str or not name:
            raise Refused(Refusal(
                "contract-endpoint-name-invalid",
                "a logical endpoint needs a nonempty exact string name",
                got=repr(name)))
        if clock is not None and (type(clock) is not str or not clock):
            raise Refused(Refusal(
                "contract-endpoint-clock-invalid",
                "an endpoint clock binding is a domain name, or null for settled sampling",
                endpoint=name, got=repr(clock)))
        if type(fields) not in (tuple, list) or any(
                type(field) is not Field for field in fields):
            raise Refused(Refusal(
                "contract-endpoint-fields-invalid",
                "an endpoint carries a finite list of exact Field records",
                endpoint=name))
        if type(adapter) is not Adapter:
            raise Refused(Refusal(
                "contract-endpoint-adapter-invalid",
                "an endpoint protocol is an exact Adapter record",
                endpoint=name, got=type(adapter).__name__))
        self.name = name
        self.direction = _declared_enum(
            Direction, direction, "contract-direction-invalid",
            "an endpoint direction is 'in', 'out', or the corresponding Direction",
            endpoint=name)
        self.fields = tuple(fields)
        from .input_framing import InputFraming

        if framing is not None:
            if type(framing) is not InputFraming or self.direction is not Direction.IN:
                raise TypeError("framing is an InputFraming declaration on an input endpoint")
            field_types = {field.name: field.type for field in fields}
            for marker in framing.fields:
                marker_type = field_types.get(marker)
                if (marker_type is None or marker_type.kind != "integer"
                        or marker_type.carrier.width != 1 or marker_type.carrier.signed):
                    raise ValueError(f"frame marker {marker!r} must name an unsigned one-bit input field")
        self.framing = framing
        self.adapter = adapter
        #: Which clock domain this endpoint's events happen in. Named per endpoint, since
        #: a multi-clock task has endpoints in different ones and a single-clock task
        #: names the same one twice rather than leaving it implicit.
        self.clock = clock
        self.matching = _declared_enum(
            Matching, matching, "contract-matching-invalid",
            "an endpoint matching rule is one documented Matching value",
            endpoint=name)
        #: The field correlating an output with its request, for `Matching.IDENTIFIER`.
        if identifier is not None and (
                type(identifier) is not str or not identifier):
            raise Refused(Refusal(
                "contract-identifier-invalid",
                "an identifier field is null or a nonempty exact string",
                endpoint=name, got=repr(identifier)))
        self.identifier = identifier
        #: How many transactions may be in flight at once, or `None` where the task
        #: does not say.
        #:
        #: **The default is `None` and it used to be one.** The argument for one was that
        #: something has to tell a transaction-level reference when to stop accepting --
        #: but the reference does not decide acceptance at all any more: the artifact
        #: does, and the reference consumes what it took. So the only thing this can be
        #: is an obligation the task declares and the monitor checks, and defaulting it
        #: would be this build inventing an obligation and then reporting designs for
        #: breaking it. It never even bound while it was the default: the replay drained
        #: its in-flight list inside the same slot, so the limit was never reached.
        if max_outstanding is not None and type(max_outstanding) is not int:
            raise Refused(Refusal(
                "contract-count-not-integer",
                "endpoint counts are exact integers or undeclared",
                endpoint=name, field="max_outstanding",
                got=repr(max_outstanding)))
        self.max_outstanding = max_outstanding
        if self.max_outstanding is not None and self.max_outstanding < 1:
            raise Refused(Refusal(
                "contract-no-outstanding",
                "an endpoint that may hold no transaction in flight can never accept "
                "one, so nothing can ever be offered to it",
                endpoint=self.name, max_outstanding=max_outstanding))
        #: Scheduled macrosteps from acceptance to the first response offer, or None.
        #: Other domains' edges and control-only steps count; internal simulator
        #: phases do not. The obligation is checked at this endpoint's active edges.
        #:
        #: None leaves the obligation undeclared: report it as not checked,
        #: rather than inventing a bound or claiming that the deadline was met.
        if response_deadline is not None and (
                type(response_deadline) is not int or response_deadline < 0):
            raise Refused(Refusal(
                "contract-response-deadline-invalid",
                "a response deadline is an exact nonnegative slot count or "
                "undeclared",
                endpoint=name, got=repr(response_deadline)))
        self.response_deadline = response_deadline
        #: How many outputs one accepted transaction produces, or `None`.
        #:
        #: **`None` is undeclared, and one is not the default.** A monitor that assumes
        #: one answer per request calls every extra output "produced with nothing
        #: outstanding" -- and the population's own sweep has 16 designs whose outputs
        #: outnumber their inputs, so the assumption is wrong on the data before it is
        #: wrong in principle. Undeclared means the obligations that rest on the ratio
        #: are reported as not checked, which is the honest reading: nobody said what the
        #: design owes, so nobody can say it failed to owe it.
        if answers_per_transaction is not None and (
                type(answers_per_transaction) is not int
                or answers_per_transaction < 1):
            raise Refused(Refusal(
                "contract-answer-ratio-invalid",
                "answers per transaction is a positive exact integer or undeclared",
                endpoint=name, got=repr(answers_per_transaction)))
        self.answers_per_transaction = answers_per_transaction
        #: Whether the task declares that the design goes quiet once it owes nothing.
        #:
        #: `missing-response` rests on this and not on the answer ratio. Over a **finite**
        #: horizon, "accepted and not answered by the end" is what a run that stopped too
        #: early looks like, and calling it a safety violation reports the horizon as a
        #: defect in the design. A task that declares terminal quiescence has said the
        #: design owes an answer before it goes idle, and then the finding is real.
        if terminal_quiescence is not None and type(terminal_quiescence) is not bool:
            raise Refused(Refusal(
                "contract-terminal-quiescence-invalid",
                "terminal quiescence is true, false, or undeclared",
                endpoint=name, got=repr(terminal_quiescence)))
        self.terminal_quiescence = terminal_quiescence
        #: What this channel is. See `Kind`: it cannot be read off a run, so it is
        #: declared or it is `UNDECLARED`.
        self.kind = _declared_enum(
            Kind, kind, "contract-kind-invalid",
            "an endpoint kind is one documented Kind value", endpoint=name)
        #: What a reset does to transactions already in flight. See `ResetPolicy`.
        #:
        #: This was an *assumption* the monitor printed beside its results -- "a reset
        #: slot clears what is in flight" -- and then acted on. Two designs differing in
        #: exactly this respect were judged by one rule, and for the one that keeps its
        #: obligations across reset the monitor forgot the transactions it still owed
        #: and reported a clean run. An assumption that changes the verdict is not an
        #: assumption; it is an undeclared part of the contract.
        self.reset_policy = (
            None if reset_policy is None else _declared_enum(
                ResetPolicy, reset_policy, "contract-reset-policy-invalid",
                "an endpoint reset policy is null or one documented ResetPolicy value",
                endpoint=name))
        for label, value in (("max_outstanding", self.max_outstanding),
                             ("answers_per_transaction",
                              self.answers_per_transaction)):
            if value is not None and value < 1:
                raise Refused(Refusal(
                    "contract-non-positive-count",
                    "an endpoint that may hold no transaction in flight can never "
                    "accept one, and one that produces no answer per transaction is not "
                    "a ratio; undeclared is `None`, which is a different statement",
                    endpoint=self.name, field=label, value=value))
        self._check()
        self._freeze()

    def _check(self):
        names = [f.name for f in self.fields]
        if len(set(names)) != len(names):
            raise Refused(Refusal(
                "contract-duplicate-field",
                "two fields of one endpoint share a name, so a payload cannot say "
                "which of them it is carrying",
                endpoint=self.name, fields=sorted(names)))
        ports = [f.port for f in self.fields if f.port is not None]
        if len(set(ports)) != len(ports):
            raise Refused(Refusal(
                "contract-duplicate-binding",
                "two fields of one endpoint bind the same port, so one of them is not "
                "carried and nothing would say which",
                endpoint=self.name, ports=sorted(ports)))
        if self.matching is Matching.IDENTIFIER and self.identifier not in names:
            raise Refused(Refusal(
                "contract-identifier-not-a-field",
                "this endpoint is compared by identifier and names one that is not one "
                "of its fields, so there is nothing to correlate on",
                endpoint=self.name, identifier=self.identifier, fields=sorted(names)))

    def field(self, name):
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def admits(self, payload):
        """Which fields of ``payload`` fall outside their declared type.

        An endpoint with no fields admits the empty payload and nothing else. That is a
        real endpoint: a request channel whose handshake is the whole event carries no
        data, and the transaction is that it happened.
        """
        if len(payload) != len(self.fields):
            return [f"{len(payload)} values for {len(self.fields)} fields"]
        return [fault
                for field, value in zip(self.fields, payload)
                for fault in field.type.faults(value, path=field.name)]

    def as_dict(self):
        return {"name": self.name, "direction": self.direction.value,
                "fields": [f.as_dict() for f in self.fields],
                "adapter": self.adapter.as_dict(), "clock": self.clock,
                "matching": self.matching.value, "identifier": self.identifier,
                "max_outstanding": self.max_outstanding,
                "response_deadline": self.response_deadline,
                "answers_per_transaction": self.answers_per_transaction,
                "terminal_quiescence": self.terminal_quiescence,
                "reset_policy": (None if self.reset_policy is None
                                 else self.reset_policy.value),
                "kind": self.kind.value,
                **({"framing": self.framing.as_dict()} if self.framing is not None else {})}

    @classmethod
    def from_dict(cls, value):
        """Decode the exact finite JSON image emitted by :meth:`as_dict`."""

        keys = {
            "name", "direction", "fields", "adapter", "clock", "matching",
            "identifier", "max_outstanding", "response_deadline",
            "answers_per_transaction", "terminal_quiescence", "reset_policy",
            "kind",
        }
        if type(value) is not dict or set(value) not in (keys, keys | {"framing"}):
            raise TypeError("an endpoint image has the complete endpoint field set")
        for name in ("name", "direction", "matching", "kind"):
            if type(value[name]) is not str:
                raise TypeError(f"endpoint {name} is an exact string")
        if value["clock"] is not None and type(value["clock"]) is not str:
            raise TypeError("endpoint clock is null or an exact string")
        if value["identifier"] is not None \
                and type(value["identifier"]) is not str:
            raise TypeError("endpoint identifier is null or an exact string")
        for name in ("max_outstanding", "response_deadline",
                     "answers_per_transaction"):
            child = value[name]
            if child is not None and (type(child) is not int
                                      or isinstance(child, bool)):
                raise TypeError(f"endpoint {name} is null or an exact integer")
        terminal = value["terminal_quiescence"]
        if terminal is not None and type(terminal) is not bool:
            raise TypeError("endpoint terminal_quiescence is null or Boolean")
        reset = value["reset_policy"]
        if reset is not None and type(reset) is not str:
            raise TypeError("endpoint reset_policy is null or an exact string")
        if type(value["fields"]) is not list:
            raise TypeError("endpoint fields are one finite list")
        from .input_framing import InputFraming

        return cls(
            value["name"], value["direction"],
            [Field.from_dict(field) for field in value["fields"]],
            Adapter.from_dict(value["adapter"]), value["clock"],
            matching=value["matching"], identifier=value["identifier"],
            max_outstanding=value["max_outstanding"],
            response_deadline=value["response_deadline"],
            answers_per_transaction=value["answers_per_transaction"],
            terminal_quiescence=terminal, kind=value["kind"],
            reset_policy=reset,
            framing=InputFraming.from_dict(value["framing"]) if "framing" in value else None,
        )

    def __repr__(self):
        return (f"Endpoint({self.name} {self.direction.value} "
                f"{len(self.fields)} fields, {self.adapter!r})")


class ClockDomain(Frozen):
    """One clock, its reset, and who owns them.

    Both are the **task's**. A candidate that names its reset active-low where the task
    says active-high does not change what reset means; it fails to realize the contract,
    and admission says so rather than the harness quietly following the candidate.
    """

    __slots__ = (
        "clock", "edge", "name", "reset", "reset_active_low",
        "reset_hold_cycles", "reset_restores",
    )

    def __init__(self, name, clock, reset, reset_active_low=False,
                 reset_hold_cycles=3, reset_restores=None, edge="rising"):
        for field, value in (("name", name), ("clock", clock),
                             ("reset", reset), ("edge", edge)):
            if type(value) is not str or not value:
                raise Refused(Refusal(
                    "contract-clock-field-invalid",
                    "a clock domain's names and active edge are nonempty exact "
                    "strings; coercing another value would put a different pin or "
                    "policy in the contract digest",
                    field=field, got=repr(value)))
        if edge not in ("rising", "falling"):
            raise Refused(Refusal(
                "contract-clock-edge-invalid",
                "a clock domain's active edge is either rising or falling",
                domain=name, edge=edge))
        if type(reset_active_low) is not bool:
            raise Refused(Refusal(
                "contract-reset-polarity-invalid",
                "reset polarity is an exact Boolean task fact",
                domain=name, got=repr(reset_active_low)))
        if type(reset_hold_cycles) is not int or reset_hold_cycles < 0:
            raise Refused(Refusal(
                "contract-reset-hold-invalid",
                "a task's reset hold is an exact nonnegative number of active edges",
                domain=name, got=repr(reset_hold_cycles)))
        if reset_restores is not None and type(reset_restores) is not bool:
            raise Refused(Refusal(
                "contract-reset-restoration-invalid",
                "reset restoration is true, false, or undeclared",
                domain=name, got=repr(reset_restores)))
        self.name = name
        self.clock = clock
        # Task-owned. ``rising`` is the compatibility value for old declarations;
        # a stimulus may not replace it with another edge.
        self.edge = edge
        self.reset = reset
        self.reset_active_low = reset_active_low
        #: How many edges reset is held for. In the contract because two sides holding it
        #: for two different numbers of cycles start from two different states, which is
        #: `docs/single_clock_semantics.md` item 2 -- there it is one constant in one
        #: module, and here it is a fact the task declares.
        self.reset_hold_cycles = reset_hold_cycles
        #: Whether holding this reset for `reset_hold_cycles` puts the design back where
        #: it started, whatever it was doing before. `None` where the task does not say.
        #:
        #: **A run's starting state is what its verdict is relative to.** Two designs of
        #: the six declared here -- `cvdp_copilot_gcd_0001` and `history_shift` -- do not
        #: come back: replay a stimulus, then another, then the first again, and the
        #: third record differs from the first. Each isolated run is still reproducible,
        #: because each starts from the simulator's power-up state as well as from the
        #: reset; what it is *not* is a statement about a reset in a design that has been
        #: running, which is what a reader takes it for.
        #:
        #: So the task declares it and the run checks it. Declared `True` and observed
        #: otherwise is a design that does not meet its own reset semantics. Undeclared
        #: is recorded as what the score rests on, rather than assumed either way.
        self.reset_restores = reset_restores
        self._freeze()

    def as_dict(self):
        return {"name": self.name, "clock": self.clock, "edge": self.edge,
                "reset": self.reset,
                "reset_active_low": self.reset_active_low,
                "reset_hold_cycles": self.reset_hold_cycles,
                "reset_restores": self.reset_restores}

    @classmethod
    def from_dict(cls, value):
        """Decode the exact finite JSON image emitted by :meth:`as_dict`."""

        keys = {"name", "clock", "edge", "reset", "reset_active_low",
                "reset_hold_cycles", "reset_restores"}
        if type(value) is not dict or set(value) != keys:
            raise TypeError("a clock-domain image has the complete clock field set")
        if any(type(value[name]) is not str
               for name in ("name", "clock", "edge", "reset")):
            raise TypeError("clock-domain names are exact strings")
        if type(value["reset_active_low"]) is not bool \
                or type(value["reset_hold_cycles"]) is not int \
                or isinstance(value["reset_hold_cycles"], bool):
            raise TypeError("clock reset polarity/hold have exact bool/int types")
        restores = value["reset_restores"]
        if restores is not None and type(restores) is not bool:
            raise TypeError("clock reset_restores is null or Boolean")
        return cls(
            value["name"], value["clock"], value["reset"],
            value["reset_active_low"], value["reset_hold_cycles"], restores,
            value["edge"])

    def __repr__(self):
        return f"ClockDomain({self.name}: {self.clock}/{self.reset})"


class TaskContract(Frozen):
    """The whole declaration, immutable, with an identity.

    Immutable in the sense that matters: nothing here exposes a setter, every collection
    is a tuple, and the digest is over the canonical encoding, so two records built in
    different orders are one contract and a record that changed would be a different one.
    """

    __slots__ = (
        "_digest",
        "_inputs",
        "_outputs",
        "authority",
        "clocks",
        "endpoints",
        "latency_goals",
        "memories",
        "observation_goals",
        "source_rules",
        "physical_value_layout",
        "protocol_instances",
        "source",
        "transaction_monitors",
    )

    def __init__(self, endpoints, clocks, source="", authority="task", memories=(),
                 *, protocol_instances=(), observation_goals=(), transaction_monitors=(), latency_goals=(),
                 operation_rules=(), source_read_rules=()):
        from .latency_goal import LatencyGoal
        from .observation_goal import TransactionGoal
        from .physical_value import PHYSICAL_VALUE_LAYOUT_VERSION
        from .source_rules import SourceGoalRules
        from .transaction_monitor import TransactionMonitorSpec

        if type(endpoints) not in (tuple, list) or any(
                type(endpoint) is not Endpoint for endpoint in endpoints):
            raise Refused(Refusal(
                "contract-endpoints-invalid",
                "a task contract carries a finite list of exact Endpoint records"))
        if type(clocks) not in (tuple, list) or any(
                type(clock) is not ClockDomain for clock in clocks):
            raise Refused(Refusal(
                "contract-clocks-invalid",
                "a task contract carries a finite list of exact ClockDomain records"))
        if type(memories) not in (tuple, list) or any(
                type(memory) is not MemoryInterface for memory in memories):
            raise Refused(Refusal(
                "contract-memories-invalid",
                "a task contract carries a finite list of exact MemoryInterface "
                "records"))
        if type(protocol_instances) not in (tuple, list) or any(
                type(instance) is not ProtocolInstance for instance in protocol_instances):
            raise Refused(Refusal(
                "contract-interfaces-invalid",
                "protocol instances are a finite list of exact ProtocolInstance records"))
        if type(source) is not str:
            raise Refused(Refusal(
                "contract-source-invalid",
                "task-contract provenance is an exact string rather than a coerced "
                "host value",
                got=repr(source)))
        if type(authority) is not str or not authority:
            raise Refused(Refusal(
                "contract-authority-invalid",
                "task-contract authority is a nonempty exact string rather than a "
                "coerced host value",
                got=repr(authority)))
        self.endpoints = tuple(sorted(endpoints, key=lambda e: e.name))
        self._inputs = tuple(e for e in self.endpoints if e.direction is Direction.IN)
        self._outputs = tuple(e for e in self.endpoints if e.direction is Direction.OUT)
        self.clocks = tuple(sorted(clocks, key=lambda c: c.name))
        self.memories = tuple(sorted(memories, key=lambda memory: memory.name))
        self.protocol_instances = tuple(sorted(
            protocol_instances, key=lambda instance: instance.name))
        if type(observation_goals) not in (tuple, list) or any(
                type(goal) is not TransactionGoal for goal in observation_goals):
            raise TypeError("observation_goals is a finite list of TransactionGoal records")
        self.observation_goals = tuple(sorted(observation_goals, key=lambda goal: goal.name))
        if len({goal.name for goal in self.observation_goals}) != len(self.observation_goals):
            raise ValueError("task observation goal names must be distinct")
        if type(transaction_monitors) not in (tuple, list) or any(
                type(spec) is not TransactionMonitorSpec for spec in transaction_monitors):
            raise TypeError("transaction_monitors is a finite list of TransactionMonitorSpec records")
        self.transaction_monitors = tuple(sorted(transaction_monitors, key=lambda spec: spec.name))
        if len({spec.name for spec in self.transaction_monitors}) != len(self.transaction_monitors):
            raise ValueError("task transaction monitor names must be distinct")
        monitor_ids = [spec.goal_id(goal) for spec in self.transaction_monitors for goal in spec.monitor.goals]
        if len(set(monitor_ids)) != len(monitor_ids):
            raise ValueError("qualified transaction monitor goal identifiers must be distinct")
        if type(latency_goals) not in (tuple, list) or any(type(rule) is not LatencyGoal for rule in latency_goals):
            raise TypeError("latency_goals is a finite list of LatencyGoal records")
        self.latency_goals = tuple(sorted(latency_goals, key=lambda rule: rule.name))
        if len({rule.name for rule in self.latency_goals}) != len(self.latency_goals):
            raise ValueError("task latency goal names must be distinct")
        self.source_rules = SourceGoalRules(operation_rules, source_read_rules)
        #: Where the declaration was read from. Provenance, and outside the digest.
        self.source = source
        #: Whose declaration this is. `core/schema.Authority` makes the same distinction
        #: and for the same reason: a contract recovered from the artifact under test is
        #: a reading of the thing being judged.
        self.authority = authority
        # One task field is carried by one physical port using this closed codec.
        # Packing is semantic interface identity, not an artifact preference.
        self.physical_value_layout = PHYSICAL_VALUE_LAYOUT_VERSION
        self._check()
        for goal in self.observation_goals:
            goal.bind(self.endpoints)
        for spec in self.transaction_monitors:
            spec.bind(self.endpoints)
            spec.bind_schedule(self.clocks)
        for rule in self.latency_goals:
            rule.bind(self)
        # Computed here rather than lazily. A digest filled in on first read is a field
        # written after construction, which is the thing this record is frozen against:
        # the reviewer's reproduction read the digest, changed an endpoint, and read the
        # same digest back. Eager also means the identity exists from birth, so there is
        # no window in which a record has none.
        body = dict(self.as_dict())
        body.pop("source")
        body.pop("version")
        # An absent grouping preserves the v5 semantic identity. Explicit instance
        # membership/mapping is new task information and always participates in it.
        if not self.protocol_instances:
            body.pop("protocol_instances")
        if not self.observation_goals:
            body.pop("observation_goals")
        if not self.transaction_monitors:
            body.pop("transaction_monitors")
        if not self.latency_goals:
            body.pop("latency_goals")
        if not self.operation_rules:
            body.pop("operation_rules")
        if not self.source_read_rules:
            body.pop("source_read_rules")
        self._digest = hashlib.sha256(canonical_json(body).encode()).hexdigest()
        self._freeze()

    @property
    def operation_rules(self):
        return self.source_rules.operations

    @property
    def source_read_rules(self):
        return self.source_rules.reads

    def _check(self):
        names = [e.name for e in self.endpoints]
        if len(set(names)) != len(names):
            raise Refused(Refusal(
                "contract-duplicate-endpoint",
                "two endpoints share a name, so a stimulus naming it does not say which",
                endpoints=sorted(names)))
        clock_names = [clock.name for clock in self.clocks]
        clocks = set(clock_names)
        settled_sampling = bool(self.endpoints) and all(
            e.clock is None and e.adapter.name == SETTLED for e in self.endpoints)
        if not clocks and not settled_sampling:
            raise Refused(Refusal(
                "contract-no-clock",
                "a clockless contract must explicitly use combinational-settled endpoints",
                endpoints=sorted(names)))
        if len(clocks) != len(clock_names):
            raise Refused(Refusal(
                "contract-duplicate-clock-domain",
                "two clock domains share a name, so an endpoint naming that domain "
                "does not identify one clock/reset policy",
                domains=sorted(name for name in clocks
                               if clock_names.count(name) > 1)))
        for e in self.endpoints:
            if e.clock not in clocks and not (not clocks and settled_sampling):
                raise Refused(Refusal(
                    "contract-endpoint-clock-unknown",
                    "this endpoint's events happen in a clock domain the contract does "
                    "not declare",
                    endpoint=e.name, clock=e.clock, clocks=sorted(clocks)))
        check_protocol_instances(self.protocol_instances, self.endpoints)
        memory_names = [memory.name for memory in self.memories]
        if len(set(memory_names)) != len(memory_names):
            raise Refused(Refusal(
                "contract-duplicate-memory",
                "two external-memory declarations share a name",
                memories=memory_names))
        for memory in self.memories:
            if memory.clock not in clocks:
                raise Refused(Refusal(
                    "contract-memory-clock-unknown",
                    "this external memory advances in a clock domain the contract "
                    "does not declare",
                    memory=memory.name, clock=memory.clock,
                    clocks=sorted(clocks)))
        # One port carried by two endpoints is the same fault as one carried by two
        # fields, one level up, and it is invisible from inside either endpoint.
        seen = {}
        for e in self.endpoints:
            for f in e.fields:
                if f.port is None:
                    continue
                if f.port in seen:
                    raise Refused(Refusal(
                        "contract-duplicate-binding",
                        "two endpoints bind the same port, so a value driven on it "
                        "belongs to both transactions and to neither",
                        port=f.port, endpoints=sorted([seen[f.port], e.name])))
                seen[f.port] = e.name

        # A physical pin has one *kind* of task meaning.  Endpoint controls may be
        # deliberately broadcast (for example two output endpoints sharing ``ready``),
        # but a payload bit cannot simultaneously be a handshake bit, clock or reset,
        # and an adapter control cannot double as a clock/reset.  Those collisions do
        # not describe coupling; they give one observed bit two incompatible decoders.
        controls = {
            signal
            for endpoint in self.endpoints
            for signal in (
                endpoint.adapter.environment_controls
                + endpoint.adapter.artifact_controls)
        }
        clock_ports = {clock.clock for clock in self.clocks}
        reset_ports = {clock.reset for clock in self.clocks}
        timing = clock_ports | reset_ports
        payload = set(seen)
        collisions = {
            "payload/control": sorted(payload & controls),
            "payload/clock-reset": sorted(payload & timing),
            "control/clock-reset": sorted(controls & timing),
            "clock/reset": sorted(clock_ports & reset_ports),
        }
        collisions = {kind: names for kind, names in collisions.items() if names}
        if collisions:
            raise Refused(Refusal(
                "contract-port-role-collision",
                "one physical pin has incompatible payload, protocol-control or "
                "clock/reset roles in the task contract",
                collisions=collisions))

        # Memory sideband pins are a separate protocol, but remain physical pins: none
        # may simultaneously carry a transaction, handshake, clock, reset, or a second
        # memory port.  The declaration owns this partition before either artifact is
        # examined.
        memory_pins = {}
        for memory in self.memories:
            for port in memory.ports:
                for pin in memory_port_signal_set(
                        memory.name, port.id, port.kind):
                    if pin in memory_pins:
                        raise Refused(Refusal(
                            "contract-memory-port-collision",
                            "one physical pin belongs to two external-memory ports",
                            pin=pin, memories=sorted(
                                [memory_pins[pin], memory.name])))
                    memory_pins[pin] = memory.name
        ordinary_pins = payload | controls | timing
        overlap = sorted(set(memory_pins) & ordinary_pins)
        if overlap:
            raise Refused(Refusal(
                "contract-memory-role-collision",
                "an external-memory sideband pin also has a transaction, protocol, "
                "clock, or reset role",
                pins=overlap))

    def endpoint(self, name):
        for e in self.endpoints:
            if e.name == name:
                return e
        return None

    def clock_domain(self, name):
        for c in self.clocks:
            if c.name == name:
                return c
        return None

    def protocol_instances_for(self, clock):
        """Keep explicit instance membership when projecting a clock domain."""
        return tuple(instance for instance in self.protocol_instances
                     if self.endpoint(instance.endpoints[0]).clock == clock)

    def inputs(self):
        return self._inputs

    def outputs(self):
        return self._outputs

    def environment_controls(self):
        """Every signal the environment owns: the adapters' plus each domain's reset."""
        out = {c.reset for c in self.clocks}
        for e in self.endpoints:
            out.update(e.adapter.environment_controls)
        return tuple(sorted(out))

    def presented_controls(self):
        """The environment controls **an input endpoint's presentation decides**.

        `in_valid` and anything like it. Not scheduled and not recorded in a slot: whether
        it is asserted follows from the slot's `offers` and from whether the queue still
        has a transaction to present, and `core/driver.Driver` drives it from those.
        """
        out = set()
        for e in self.inputs():
            out.update(e.adapter.environment_controls)
        return tuple(sorted(out))

    def scheduled_controls(self):
        """Every signal a **schedule** must name in every slot.

        `environment_controls` minus `presented_controls`: one authority per pin.

        The two were one set, so a slot carried `in_valid` beside the `offers` that
        decide it -- and the two could disagree. An idle slot copied from the cycle
        before it recorded `in_valid = 1` while the testbench, seeing nothing presented,
        drove 0. The stimulus fingerprint then bound a control sequence that did not run,
        which is the binding between stimulus, trace and domain that everything here
        rests on.

        Refusal: a slot naming one of these, or missing one of the scheduled ones, is a
        fault from `Stimulus.faults_against`.
        """
        presented = set(self.presented_controls())
        return tuple(n for n in self.environment_controls() if n not in presented)

    def as_dict(self):
        return {"version": TASK_CONTRACT_VERSION,
                "endpoints": [e.as_dict() for e in self.endpoints],
                "clocks": [c.as_dict() for c in self.clocks],
                "memories": [memory.as_dict() for memory in self.memories],
                "protocol_instances": [instance.as_dict()
                                       for instance in self.protocol_instances],
                "observation_goals": [goal.as_dict() for goal in self.observation_goals],
                "transaction_monitors": [spec.as_dict() for spec in self.transaction_monitors],
                "latency_goals": [rule.as_dict() for rule in self.latency_goals],
                "operation_rules": [rule.as_dict() for rule in self.operation_rules],
                "source_read_rules": [rule.as_dict() for rule in self.source_read_rules],
                "authority": self.authority, "source": self.source,
                "physical_value_layout": self.physical_value_layout}

    @classmethod
    def from_dict(cls, value):
        """Decode one strict, complete :meth:`as_dict` image.

        This is the sole JSON boundary for contracts sent to isolated generation and
        analysis workers.  Public constructors enforce the same task-fact types; the
        wire boundary additionally rejects omitted defaults and unknown fields.
        """

        if type(value) is not dict:
            raise TypeError("a task-contract image is an exact mapping")
        version = value.get("version")
        versions = {f"task-contract/{revision}": revision
                    for revision in range(5, _TASK_CONTRACT_REVISION + 1)}
        if type(version) is not str or version not in versions:
            raise Refused(Refusal(
                "contract-version-unsupported",
                "a task-contract image must name the wire version this build "
                "decodes",
                got=value.get("version"), supported=TASK_CONTRACT_VERSION))
        revision = versions[version]
        required = {
            "version", "endpoints", "clocks", "memories", "authority", "source",
            "physical_value_layout",
        }
        list_fields = tuple(name for introduced, name in (
            (6, "protocol_instances"), (10, "observation_goals"),
            (11, "transaction_monitors"), (13, "latency_goals"),
            (14, "operation_rules"), (16, "source_read_rules")) if revision >= introduced)
        required.update(list_fields)
        if set(value) != required:
            raise TypeError(
                "a task-contract image needs the exact fields of its wire version")
        for name in list_fields:
            if type(value[name]) is not list:
                raise TypeError(f"contract {name} is a finite list")
        if revision < 12 and any(
                "sampling" in spec for spec in value.get("transaction_monitors", ())):
            raise TypeError("entry-set monitor sampling requires task-contract/12")
        if revision < 15 and any(
                spec.get("sampling") == "schedule-entry" or "schedule_events" in spec
                for spec in value.get("transaction_monitors", ())):
            raise TypeError("physical schedule-entry sampling requires task-contract/15")
        if version == "task-contract/6" and any(
                type(instance) is dict and "physical" in instance
                for instance in value["protocol_instances"]):
            raise TypeError("physical interface configuration needs task-contract/7")
        from .physical_value import PHYSICAL_VALUE_LAYOUT_VERSION

        if value["physical_value_layout"] != PHYSICAL_VALUE_LAYOUT_VERSION:
            raise Refused(Refusal(
                "contract-physical-value-layout-unsupported",
                "the task contract names a physical value layout this build does "
                "not decode",
                got=value["physical_value_layout"],
                supported=PHYSICAL_VALUE_LAYOUT_VERSION))
        if type(value["endpoints"]) is not list \
                or type(value["clocks"]) is not list \
                or type(value["memories"]) is not list:
            raise TypeError("contract endpoints, clocks and memories are finite lists")
        if revision < 8 and not value["clocks"]:
            raise TypeError("clockless contracts require task-contract/8")
        if revision < 9 and any("framing" in e for e in value["endpoints"]):
            raise TypeError("input framing requires task-contract/9")
        if revision < 17 and any("sampling" in m for m in value["memories"]):
            raise TypeError("memory operand sampling requires task-contract/17")
        if type(value["authority"]) is not str or type(value["source"]) is not str:
            raise TypeError("contract authority and source are exact strings")
        from .latency_goal import LatencyGoal
        from .observation_goal import TransactionGoal
        from .operation_goal import OperationRule
        from .source_rules import SourceReadRule
        from .transaction_monitor import TransactionMonitorSpec

        return cls(
            [Endpoint.from_dict(endpoint) for endpoint in value["endpoints"]],
            [ClockDomain.from_dict(clock) for clock in value["clocks"]],
            source=value["source"], authority=value["authority"],
            memories=[MemoryInterface.from_dict(memory)
                      for memory in value["memories"]],
            protocol_instances=[ProtocolInstance.from_dict(instance)
                                for instance in value.get("protocol_instances", ())],
            observation_goals=[TransactionGoal.from_dict(goal)
                               for goal in value.get("observation_goals", ())],
            transaction_monitors=[TransactionMonitorSpec.from_dict(spec)
                                  for spec in value.get("transaction_monitors", ())],
            latency_goals=[LatencyGoal.from_dict(rule) for rule in value.get("latency_goals", ())],
            operation_rules=[OperationRule.from_dict(rule) for rule in value.get("operation_rules", ())],
            source_read_rules=[SourceReadRule.from_dict(rule) for rule in value.get("source_read_rules", ())],
        )

    @property
    def digest(self):
        """The contract's identity, over everything but where it was read from.

        Includes each adapter's name and version, so changing the adapter changes the
        identity -- which is the point: the same schedule under a different adapter is a
        different run, and evidence bound to one contract must not read as evidence about
        the other.
        """
        return self._digest

    def __eq__(self, other):
        return isinstance(other, TaskContract) and self.digest == other.digest

    def __hash__(self):
        return hash(self.digest)

    def __repr__(self):
        return (f"TaskContract({len(self.endpoints)} endpoints, "
                f"{len(self.clocks)} clocks, {self.digest[:8]})")


# Keep the historical ``core.contract`` import surface while the recursive
# type implementation remains in its dependency-neutral module.
from .value_type import ValueType
