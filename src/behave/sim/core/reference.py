"""Which `B*` a verdict is relative to, bound to what it actually is.

An earlier attempt digested `inspect.getsource(type(model))`. That binds the class body
and nothing else: two instances of one class with different constructor arguments have
one digest, so freezing `Model(gain=2)` and running `Model(gain=3)` reported agreement.
And a model loaded with `exec` into a bare namespace has no retrievable source at all, so
the digest silently degraded to `sha256("builtins.ClassName")` -- which is what the
population sweep was using, meaning nothing was frozen there at all.

Introspecting harder is the wrong repair. What identifies a reference is what was *fed to
the loader*: the bytes of the model file, the files it depends on, the configuration it
was constructed with, and the version of the adapter that drives it. A loader knows all
four; a digest recovered afterwards from a live object knows none of them reliably.

So `ReferenceArtifact` is built by whoever loads the model, carries those four, and
digests them canonically. Nothing here inspects a live object.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass

from .catalogue import CatalogueManifest
from .frozen import Frozen, freeze
from .refusal import Refusal, Refused
from .stimulus import canonical_json, uses_global_events

__all__ = ["AtomicReferenceModel", "LoadedReference", "ReferenceArtifact",
           "ReferenceModel", "SlotEvent", "expected_by_endpoint_of", "expected_of",
           "expected_run_of", "load_reference", "register_atomic_loader", "register_loader"]


class ReferenceArtifact(Frozen):
    """The identity of one reference, as the thing that loaded it can state.

    ``sources`` maps a name to the **bytes** that were loaded under it -- the model file
    and anything it depends on. Bytes rather than a path: a path is where a file was, and
    a run has to be reproducible from what the file said.

    ``config`` is what the model was constructed with. A constructor argument that
    changes what the model computes is part of which model it is, and leaving it out is
    the hole that let `gain=2` and `gain=3` share an identity.

    ``adapter`` is the version of the thing that drives the model, because the same model
    under a different adapter answers a different question.
    """

    __slots__ = ("_digest", "adapter", "adapter_impl", "config", "loader", "sources")

    def __init__(self, sources, config=None, adapter="", loader="",
                 adapter_impl=""):
        got = {str(k): v for k, v in dict(sources or {}).items()}
        if not got:
            raise Refused(Refusal(
                "reference-artifact-no-source",
                "a reference identity over no sources identifies nothing; the loader "
                "knows what bytes it read and this is where they are recorded",
                config=sorted(dict(config or {}))))
        for name, body in got.items():
            if not isinstance(body, (bytes, bytearray)):
                raise Refused(Refusal(
                    "reference-artifact-source-not-bytes",
                    "a source is the bytes that were loaded, not a path or a summary: a "
                    "path says where a file was and a run has to be reproducible from "
                    "what the file said",
                    source=name, got=type(body).__name__))
        self.sources = tuple(sorted(
            (name, hashlib.sha256(bytes(body)).hexdigest())
            for name, body in got.items()))
        self.config = freeze(dict(config or {}))
        self.adapter = str(adapter)
        #: What loaded it, for a reader. Outside the digest: provenance is not identity.
        self.loader = str(loader)
        #: **The code behind the adapter name**, inside the digest.
        #:
        #: The adapter was a string, so `transaction/1` identified a contract about how a
        #: model is driven and not the implementation of it. Registering a different
        #: builder under the same name left every digest identical while a different
        #: model executed, and nothing downstream could tell.
        self.adapter_impl = str(adapter_impl)
        self._digest = hashlib.sha256(canonical_json({
            "sources": [list(p) for p in self.sources],
            "config": [list(p) for p in self.config],
            "adapter": self.adapter,
            "adapter_impl": self.adapter_impl,
        }).encode()).hexdigest()
        self._freeze()

    @property
    def digest(self):
        return self._digest

    def as_dict(self):
        return {"digest": self.digest, "adapter": self.adapter,
                "adapter_impl": self.adapter_impl,
                "loader": self.loader,
                "sources": [list(p) for p in self.sources],
                "config": [list(p) for p in self.config]}

    def __eq__(self, other):
        return isinstance(other, ReferenceArtifact) and self.digest == other.digest

    def __hash__(self):
        return hash(self.digest)

    def __repr__(self):
        return (f"ReferenceArtifact({len(self.sources)} sources, "
                f"{self.digest[:8]})")


#: `adapter -> factory builder`. A builder takes the **same** source mapping the artifact
#: digested, plus the configuration, and returns a zero-argument callable producing a
#: fresh model. Registered rather than passed at the call site: see `load_reference`.
_LOADERS = {}

#: ``adapter -> (builder, semantic version)`` for the optional atomic-slot
#: capability.  Core owns only this closed dispatch boundary; the implementation lives
#: with the frontend that knows how its source denotes per-domain handlers.  Keeping a
#: frontend import out of this module is not cosmetic: otherwise changing Behavior's
#: isolation package changes a supposedly frontend-neutral reference semantics.
_ATOMIC_LOADERS = {}

# Optional native source observation of an already running reference. Like the
# atomic adapter, this is registered by the frontend, not guessed from model
# attributes or reconstructed by the comparison layer.
_SOURCE_OBSERVERS = {}

#: Adapter spellings which existed in released records but are not safe construction
#: capabilities.  Keeping the tombstone prevents a later caller from reclaiming the
#: familiar name for another in-process loader and making old/new records look alike.
_RETIRED_LOADERS = {
    "transaction/1": (
        "this adapter executed submitted reference source in the verifier process; "
        "use transaction/isolated-6"
    ),
    "transaction/isolated-1": (
        "this adapter did not bind a stimulus's external-memory state into its "
        "Behavior model; use transaction/isolated-6"
    ),
    "transaction/isolated-2": (
        "this adapter treated an artifact's observed internal state as portable "
        "reference state; use transaction/isolated-6"
    ),
}

#: Only `load_reference` may build a bundle. A module-private token rather than a naming
#: convention, because the thing being prevented is exactly a caller pairing an identity
#: with a model of its own choosing, and a leading underscore does not prevent it.
_AUTHORISED = object()


def register_loader(adapter, builder):
    """Declare one trusted implementation adapter.

    This implementation seam is deliberately absent from :mod:`behave.sim.api`:
    submitted callers may select a registered adapter but cannot mint an in-process
    source executor.  Production registers only the isolated adapter; unit tests may
    register explicitly named test adapters through this internal module.

    **A name is claimed once.** This used to assign into the table, so re-registering
    `transaction/1` silently replaced the loader while every artifact's digest stayed the
    same -- identical sources, identical configuration, identical adapter string, a
    different model executed. The digest names the adapter and did not name the code
    behind it, so nothing downstream could see the substitution.

    Two changes. Re-registering a name refuses unless it is the same builder object, and
    `_builder_identity` goes into the artifact's digest, so two builds of one adapter
    that run different code are two references.

    Registration is still not itself an isolation mechanism.  The registered builder
    must provide that boundary; the production builder does, while the tests-only
    in-process adapter says its weaker trust model in its name.
    """
    name = str(adapter)
    if name in _RETIRED_LOADERS:
        raise Refused(Refusal(
            "reference-adapter-retired",
            _RETIRED_LOADERS[name], adapter=name))
    seen = _LOADERS.get(name)
    if seen is not None and seen is not builder:
        raise Refused(Refusal(
            "reference-adapter-already-registered",
            "this adapter name is already claimed by a different loader. Replacing it "
            "would leave every reference digest unchanged while a different model ran "
            "behind them",
            adapter=name, registered=_builder_identity(seen),
            offered=_builder_identity(builder)))
    _LOADERS[name] = builder


def register_atomic_loader(adapter, builder, version):
    """Attach one versioned atomic-slot implementation to a registered adapter.

    The ordinary loader and this capability have separate contracts: the former makes
    a fresh transaction model, while the latter interprets coincident clock events and
    domain-owned handlers.  A name may claim each contract once.  The capability's
    version and implementation identity enter every subsequently loaded reference
    artifact, so adding or changing atomic semantics cannot reuse an older digest.

    This is an internal registration seam, deliberately absent from ``sim.api``.
    Submitted code can select an already registered adapter but cannot register a
    callback in the verifier process.
    """
    name = str(adapter)
    if name in _RETIRED_LOADERS:
        raise Refused(Refusal(
            "reference-adapter-retired",
            _RETIRED_LOADERS[name], adapter=name))
    if _LOADERS.get(name) is None:
        raise Refused(Refusal(
            "reference-atomic-adapter-without-loader",
            "an atomic capability belongs to an already registered reference loader",
            adapter=name))
    if not callable(builder):
        raise TypeError("an atomic reference adapter builder must be callable")
    if type(version) is not str or not version:
        raise TypeError("an atomic reference adapter needs a nonempty exact version")
    seen = _ATOMIC_LOADERS.get(name)
    offered = (builder, version)
    if seen is not None and seen != offered:
        raise Refused(Refusal(
            "reference-atomic-adapter-already-registered",
            "this adapter name already has different atomic-slot semantics",
            adapter=name,
            registered={"builder": _builder_identity(seen[0]),
                        "version": seen[1]},
            offered={"builder": _builder_identity(builder),
                     "version": version}))
    _ATOMIC_LOADERS[name] = offered


def _builder_identity(builder):
    """What code stands behind an adapter name, as a digest over its source.

    **An identity of a partial semantic image, and the limit is explicit.** The digest is
    over this function's own source text and its qualified name. It does **not** cover
    what the builder closes over, the module globals it reads, the helpers it calls, or
    the semantics of the adapter it implements -- so two builders whose bodies are
    identical and whose helpers differ share this. It is the same class of gap as
    digesting a model class body and not its constructor arguments, one level up.

    What it does buy is that the two substitutions that actually happened are visible: a
    different function registered under an existing name, and a body edited in place. A
    complete answer needs the loader's whole import closure, which is `T3-09`'s
    isolation boundary rather than a hash computed here.

    Falls back to the qualified name where the source cannot be read.
    """
    import inspect

    module = getattr(builder, "__module__", "?")
    name = getattr(builder, "__qualname__", repr(builder))
    where = f"{module}.{name}"
    try:
        body = inspect.getsource(builder).encode()
    except (OSError, TypeError):
        return where
    return f"{where}@{hashlib.sha256(body).hexdigest()}"


def register_source_observer(adapter, builder, version):
    """Attach a read-only native hit collector to a registered reference adapter."""
    if adapter not in _LOADERS or not callable(builder) or not version:
        raise ValueError("a source observer belongs to a registered reference adapter")
    previous = _SOURCE_OBSERVERS.get(adapter)
    if previous is not None and previous != (builder, version):
        raise ValueError("a reference source observer is already registered")
    _SOURCE_OBSERVERS[adapter] = builder, version


def _adapter_identity(name, builder):
    """Identity of every execution contract registered under ``name``."""
    parts = [f"transaction={_builder_identity(builder)}"]
    atomic = _ATOMIC_LOADERS.get(name)
    if atomic is not None:
        atomic_builder, version = atomic
        parts.append(
            f"atomic={version}:{_builder_identity(atomic_builder)}")
    observer = _SOURCE_OBSERVERS.get(name)
    if observer is not None:
        builder, version = observer
        parts.append(f"source-observer={version}:{_builder_identity(builder)}")
    return ";".join(parts)


@dataclass(frozen=True, slots=True)
class ReferenceFactory:
    """A trusted loader's constructor and already-judged source declaration.

    Declaration is independent of a run's hit collector. Legacy adapters may
    return a plain constructor, without advertising source-guidance metadata.
    """

    build: Callable[[], object]
    source_catalogue: CatalogueManifest
    bind_contract: Callable[[object], ReferenceFactory] | None = None

    def __call__(self):
        return self.build()

    def for_contract(self, contract):
        """Bind observation policy without changing the recorded model bytes."""
        return self if self.bind_contract is None else self.bind_contract(contract)


def load_reference(adapter, sources, config=None, loader=""):
    """Read a reference and return the **inseparable** bundle of what it is and how to
    build it.

    The caller hands bytes and configuration; it never hands a model or a factory. That
    is the whole point. `ReferenceArtifact` on its own is an identity record, and an
    identity record can be spliced onto any live object: `ReferenceModel(SomeOtherModel(),
    contract, artifact=digest_of_the_real_one)` typechecks, admits, runs, and reports
    agreement with a `B*` that was never executed. Nothing downstream can detect it,
    because by then the two halves look exactly like a matched pair.

    So the digest and the factory are derived here from one input, in one call, and
    leave together. There is no supported way to obtain one without the other.
    """
    got = {str(k): bytes(v) for k, v in dict(sources or {}).items()}
    name = str(adapter)
    if name in _RETIRED_LOADERS:
        raise Refused(Refusal(
            "reference-adapter-retired",
            _RETIRED_LOADERS[name], adapter=name))
    builder = _LOADERS.get(name)
    if builder is None:
        raise Refused(Refusal(
            "reference-adapter-unknown",
            "no loader is registered for this adapter, so there is no way to build a "
            "model from these sources that this build can vouch for",
            adapter=name, known=sorted(_LOADERS)))
    artifact = ReferenceArtifact(got, config=config, adapter=name,
                                 loader=str(loader),
                                 adapter_impl=_adapter_identity(name, builder))
    factory = builder(got, dict(config or {}))
    if not callable(factory):
        raise Refused(Refusal(
            "reference-loader-not-a-factory",
            "a loader returns a callable that produces a fresh model each time it is "
            "called; one that returns a model instead hands every run the same object, "
            "and state left by run N is then part of run N+1's reference",
            adapter=name, got=type(factory).__name__))
    # **Checked, not assumed.** `callable` says it can be called; it does not say each
    # call produces a new model. A factory closing over one instance and returning it --
    # `lambda: model` -- passes every other check here and makes run N+1's expected
    # output a function of run N for any model that accumulates. Two calls at load time
    # cost one extra construction and settle it.
    # These instances only test freshness. An isolated model's transport threads
    # retain it, so dropping Python references does not release the worker.
    with ExitStack() as probes:
        first = factory()
        close = getattr(first, "close", None)
        if callable(close):
            probes.callback(close)
        second = factory()
        if second is not first:
            close = getattr(second, "close", None)
            if callable(close):
                probes.callback(close)
        if first is second:
            raise Refused(Refusal(
                "reference-loader-returns-one-instance",
                "this loader hands back the same model object every time, so every run of "
                "the stimulus set would share whatever state the previous one left in it, "
                "and no per-run record could show it",
                adapter=name, model=type(first).__name__))
    source_catalogue = factory.source_catalogue if type(factory) is ReferenceFactory else None
    return LoadedReference(
        artifact, factory, sources=got, config=dict(config or {}),
        source_catalogue=source_catalogue, _token=_AUTHORISED)


class LoadedReference(Frozen):
    """One reference, as identity and construction that cannot be taken apart.

    ``new_model()`` returns an instance no live run is already using, and callers are
    expected to take one per run.

    Fresh model identity alone does not isolate module globals or imported state.
    Production replay therefore uses the registered isolated adapter: each model
    has its own process and frozen dependency closure. Legacy in-process test
    adapters make no such guarantee and do not qualify for parallel assessment.
    The live-identity guard is atomic, but factory construction remains outside
    its lock so independent isolated children can start concurrently.
    """

    __slots__ = (
        "_factory", "_handed_out", "_model_lock", "_source_config", "_source_observer",
        "_source_payload", "artifact", "source_catalogue",
    )

    def __init__(self, artifact, factory, *, sources=None, config=None,
                 source_catalogue=None, _token=None):
        if _token is not _AUTHORISED:
            raise Refused(Refusal(
                "reference-bundle-not-from-a-loader",
                "a bundle pairs an identity with a way to build the model, and pairing "
                "them at a call site is how a digest of one model comes to vouch for "
                "another. Use `load_reference(adapter, sources, ...)`, which derives "
                "both from the same bytes",
                artifact=getattr(artifact, "digest", "")))
        self.artifact = artifact
        self._factory = factory
        observer = _SOURCE_OBSERVERS.get(artifact.adapter)
        self._source_observer = None if observer is None else observer[0]
        self.source_catalogue = source_catalogue
        # Keep the exact loader inputs inseparable from the digest/factory bundle.  The
        # public image intentionally exposes only their digests; trusted isolated-job
        # construction can obtain a fresh copy through the private method below instead
        # of asking a caller to supply a path or bytes that could name another model.
        payload = tuple(sorted(
            (str(name), bytes(body))
            for name, body in dict(sources or {}).items()))
        if not payload:
            raise Refused(Refusal(
                "reference-bundle-source-payload-missing",
                "an authenticated reference bundle retains the exact source bytes "
                "from which its artifact and factory were derived"))
        source_digests = tuple(
            (name, hashlib.sha256(body).hexdigest()) for name, body in payload)
        if source_digests != artifact.sources:
            raise Refused(Refusal(
                "reference-bundle-source-payload-mismatch",
                "the retained source bytes do not match the ReferenceArtifact they "
                "would be used to reconstruct"))
        frozen_config = freeze(dict(config or {}))
        if frozen_config != artifact.config:
            raise Refused(Refusal(
                "reference-bundle-config-payload-mismatch",
                "the retained construction configuration does not match the "
                "ReferenceArtifact it would be used to reconstruct"))
        self._source_payload = payload
        self._source_config = frozen_config
        #: Weak references to every model handed out and still alive. See `new_model`.
        import weakref

        object.__setattr__(self, "_handed_out", weakref.WeakSet())
        self._model_lock = threading.Lock()
        self._freeze()

    def _generation_source_payload(self):
        """Return copies of the loader inputs for a trusted isolated-job adapter.

        This is deliberately private and absent from :meth:`as_dict`: bytes are an
        execution capability, not public provenance.  The returned containers are new,
        while their immutable contents remain exactly those authenticated by
        :func:`load_reference`.
        """

        return ({name: bytes(body) for name, body in self._source_payload},
                {name: value for name, value in self._source_config})

    @property
    def digest(self):
        return self.artifact.digest

    def source_catalogue_for(self, contract):
        """The declaration used by this task's reference replay and guidance."""
        if type(self._factory) is ReferenceFactory:
            return self._factory.for_contract(contract).source_catalogue
        return self.source_catalogue

    def new_model(self, *, contract=None):
        """A model no live run is already using.

        **Checked on every call, not twice at load time.** `load_reference` calls the
        factory twice and refuses if it gets one object back, which catches
        `lambda: model` and nothing subtler: a factory cycling `a, b, a, b` passes that
        check and then hands run 3 the object run 1 is still holding, and for a model
        that accumulates run 3's expected output is a function of run 1.

        A weak set, so a model the caller has finished with is collected and its
        identity is free again -- what is refused is two live runs sharing one object,
        which is the thing that makes their expected outputs depend on each other.
        """
        factory = self._factory
        if contract is not None and type(factory) is ReferenceFactory:
            factory = factory.for_contract(contract)
        model = factory()
        # Construction may launch independent children concurrently. Claim the live
        # identity atomically so two requests cannot both acquire one shared model.
        with self._model_lock:
            if any(model is seen for seen in self._handed_out):
                raise Refused(Refusal(
                    "reference-loader-reused-a-live-model",
                    "this loader handed back a model another run is still using, so the "
                    "state that run leaves in it is part of this one's reference",
                    adapter=self.artifact.adapter, model=type(model).__name__))
            try:
                self._handed_out.add(model)
            except TypeError:
                # Legacy test adapters may return non-weakrefable objects. They do
                # not qualify for the production parallel execution path.
                pass
        return model

    def instantiate(self, contract, identity="reference"):
        """A fresh `ReferenceModel` over this bundle, for one run."""
        return ReferenceModel(self, contract, identity=identity)

    def _source_recorder(self, model):
        # The observer is frozen with the loader, not rediscovered or rehashed
        # for each trace. Each call only creates a fresh run-local hit ledger.
        return None if self._source_observer is None else self._source_observer(model)

    def as_dict(self):
        return self.artifact.as_dict()

    def __repr__(self):
        return f"LoadedReference({self.digest[:8]})"


class ReferenceModel:
    """A task's behavior model behind the contract, as this build's reference adapter.

    Built **from a bundle**, never from a live object plus an identity. The old signature
    took `(model, contract, artifact=...)` and every caller was trusted to pass a matching
    pair; this takes the bundle and asks it for the model, so the digest a verdict is
    relative to is a digest of the code that produced that verdict.

    The population's models are plain Python: `reset()`, and `process(**fields)` returning
    a dict keyed by output **port** name, or a list of such dicts where one request owes
    several answers. That is a transaction-level reference, and this is the **adapter**
    that decides which slots of a run mean something to it.

    Values remain logical here.  The matcher converts expected and observed
    transactions to canonical physical patterns at the single comparison boundary, so
    recursive values are encoded exactly once.
    """

    kind = "reference"

    __slots__ = (
        "_sink", "_sinks", "_source", "_source_recorder", "_sources",
        "bundle", "contract", "identity", "model",
    )

    def __init__(self, bundle, contract, identity="reference"):
        if not isinstance(bundle, LoadedReference):
            raise Refused(Refusal(
                "reference-model-not-from-a-bundle",
                "a reference is instantiated from the bundle its loader returned. "
                "Passing a live model and an identity separately is what let a digest "
                "of one model vouch for the behaviour of another",
                got=type(bundle).__name__))
        self.bundle = bundle
        #: A **fresh** model, so two runs over one bundle share no state.
        self.model = bundle.new_model(contract=contract)
        self._source_recorder = None
        self.contract = contract
        self.identity = str(identity)
        inputs, outputs = contract.inputs(), contract.outputs()
        self._sources = tuple(inputs)
        self._sinks = tuple(outputs)
        self._source = inputs[0] if len(inputs) == 1 else None
        self._sink = outputs[0] if len(outputs) == 1 else None
        if (len(inputs) > 1 or len(outputs) > 1) \
                and not hasattr(self.model, "process_slot"):
            close = getattr(self.model, "close", None)
            if callable(close):
                close()
            raise Refused(Refusal(
                "reference-slot-interface-missing",
                "a task with several transaction endpoints needs a reference that "
                "implements process_slot(slot); repeatedly calling a one-transaction "
                "process method would invent an order among simultaneous transfers",
                inputs=[endpoint.name for endpoint in inputs],
                outputs=[endpoint.name for endpoint in outputs]))
        # ── an output-only task needs a declared way to be advanced ──
        #
        # With no input endpoint every non-reset slot is a tick, and a model with no
        # `tick` is then advanced by nothing at all: `expected_for` returns an empty
        # expected stream, the design's real outputs are all `extra`, and where the
        # design also produced nothing the two empty streams **agree**. That is a pass
        # for a reference that was never run. It has to refuse.
        if not self._sources and not hasattr(self.model, "tick"):
            raise Refused(Refusal(
                "reference-output-only-without-tick",
                "this task has no input endpoint, so the only thing that can advance "
                "the reference is a tick, and this model defines none. Comparing a "
                "design against a reference nothing advances compares it against an "
                "empty stream, which an idle design matches",
                model=type(self.model).__name__))

    @property
    def artifact(self):
        """The identity, read off the bundle rather than stored beside it."""
        return self.bundle.artifact

    def reset(self):
        self.model.reset()

    def start(self, state):
        self.model.start(state)

    def close(self):
        """Release an owned isolated model after this run."""

        close = getattr(self.model, "close", None)
        if callable(close):
            close()

    def _answers_for(self, sink, produced):
        """The model's answer as a **list** of output transactions.

        One dict is one transaction; a list of dicts is several; `None` is none. The
        third and second are not the same and neither is a degenerate case -- 26 designs
        of this population return a list, because one request produces several output
        transactions, and a reference interface that took only a dict called every one of
        them a model that omits a declared field.
        """
        if sink is None or produced is None:
            return []
        answers = produced if isinstance(produced, (list, tuple)) else [produced]
        out = []
        for answer in answers:
            try:
                values = tuple(answer[f.port] for f in sink.fields)
            except (KeyError, TypeError) as exc:
                raise Refused(Refusal(
                    "reference-output-missing-field",
                    "the model's answer does not carry every field the contract "
                    "declares for this endpoint, so the transaction it describes is not "
                    "one the task says the endpoint carries",
                    endpoint=sink.name, missing=str(exc),
                    declared=[f.port for f in sink.fields])) from None
            out.append(values)
        return out

    def _answer(self, produced):
        return self._answers_for(self._sink, produced)

    def advance_by_endpoint(self, event):
        """Advance one slot and return expected rows by logical output endpoint.

        ``process_slot`` is the explicit concrete reference boundary for a slot that
        may accept several input transactions or emit on several outputs.  It receives
        one JSON-shaped record, so the isolation wire preserves the simultaneity rather
        than serialising endpoint calls in an incidental order.  A returned mapping is
        keyed by logical output endpoint; each value has the same ``dict | list[dict] |
        None`` transaction shape as the ordinary single-output adapter.
        """

        if event.reset:
            self.model.reset()
            return {}
        inputs = {}
        for endpoint in self._sources:
            payload = event.payload(endpoint.name)
            if payload is None:
                continue
            inputs[endpoint.name] = {
                field.port: value for field, value in zip(endpoint.fields, payload)
            }
        slot = {
            "index": event.index,
            "ticks": list(event.ticks),
            "controls": dict(event.controls),
            "inputs": inputs,
        }
        produced = self._invoke_step(self.model.process_slot, slot)
        if produced is None:
            return {}
        if type(produced) is not dict \
                or any(type(name) is not str for name in produced):
            raise Refused(Refusal(
                "reference-slot-output-shape",
                "process_slot returns a mapping from logical output endpoint names "
                "to transaction rows, or None",
                got=type(produced).__name__))
        sinks = {endpoint.name: endpoint for endpoint in self._sinks}
        unknown = sorted(set(produced) - set(sinks))
        if unknown:
            raise Refused(Refusal(
                "reference-slot-output-endpoint-unknown",
                "process_slot emitted rows for an endpoint the task does not declare",
                endpoints=unknown, declared=sorted(sinks)))
        return {
            name: self._answers_for(sinks[name], rows)
            for name, rows in produced.items()
        }

    def advance(self, event):
        """One slot, as this adapter reads it. Returns the outputs it owes.

        Three cases and no fourth. A reset resets the model. A slot in which a
        transaction was accepted calls `process` with that transaction's fields. **Any
        other slot advances the model only if the model says how** -- a `tick` it
        defines -- and otherwise leaves it alone, because a transaction-level model has
        nothing to say about a cycle in which no transaction happened, and inventing a
        call for it is how a `TypeError` on a drain slot became possible.
        """
        if event.reset:
            self.model.reset()
            return []
        payload = event.payload(self._source.name) if self._source else None
        if payload is not None:
            fields = {f.port: v for f, v in zip(self._source.fields, payload)}
            return self._answer(self._invoke_step(self.model.process, **fields))
        tick = getattr(self.model, "tick", None)
        if tick is None:
            return []
        # **A tick the slot names, not every idle slot.** T3-01 asks that a
        # reference-visible inputless tick affect `B*`; a model advanced on every slot in
        # which nothing was accepted is advanced by the *absence* of a transaction, which
        # is a different thing and is true of every drain slot. A slot that names no
        # clock domain names no tick, and this leaves the model alone.
        if not event.ticks:
            return []
        return self._answer(tick())

    def _invoke_step(self, operation, *args, **kwargs):
        # A selected helper can also execute during reset or an idle tick.
        # Only this process/process_slot invocation contributes step goals.
        recorder = self._source_recorder
        if recorder is not None:
            recorder.discard_pending()
        produced = operation(*args, **kwargs)
        if recorder is not None:
            recorder.observe()
        return produced

    def __repr__(self):
        return f"ReferenceModel({type(self.model).__name__}, {self.bundle!r})"


class AtomicReferenceModel:
    """A bundle's registered atomic-slot adapter, behind a core-owned boundary.

    Core does not know how a Behavior source names domain handlers, nor should it import
    that frontend to learn.  The loader that already owns source construction registers
    the corresponding atomic capability.  This wrapper checks that the loaded artifact
    actually bound that exact capability and exposes only ``expected`` and ``close``.
    """

    __slots__ = ("_delegate", "artifact", "bundle", "contract")

    def __init__(self, bundle, contract):
        if type(bundle) is not LoadedReference:
            raise Refused(Refusal(
                "atomic-reference-not-from-a-bundle",
                "atomic expected outputs must be derived from the exact frozen "
                "reference bundle"))
        registered = _ATOMIC_LOADERS.get(bundle.artifact.adapter)
        if registered is None:
            raise Refused(Refusal(
                "atomic-reference-adapter-unsupported",
                "this reference loader declares no atomic-slot interpretation",
                adapter=bundle.artifact.adapter,
                supported=sorted(_ATOMIC_LOADERS)))
        builder, _version = registered
        ordinary = _LOADERS.get(bundle.artifact.adapter)
        if ordinary is None or bundle.artifact.adapter_impl != \
                _adapter_identity(bundle.artifact.adapter, ordinary):
            raise Refused(Refusal(
                "atomic-reference-capability-not-in-artifact",
                "the loaded reference identity does not bind the atomic adapter now "
                "registered under its name",
                adapter=bundle.artifact.adapter,
                recorded=bundle.artifact.adapter_impl,
                current=_adapter_identity(bundle.artifact.adapter, ordinary)))
        try:
            delegate = builder(bundle, contract)
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - registered adapter boundary
            raise Refused(Refusal(
                "atomic-reference-adapter-failed",
                "the registered atomic reference adapter could not be constructed",
                adapter=bundle.artifact.adapter,
                error=f"{type(exc).__name__}: {exc}")) from None
        if not callable(getattr(delegate, "expected", None)) \
                or not callable(getattr(delegate, "close", None)):
            raise Refused(Refusal(
                "atomic-reference-adapter-result-invalid",
                "an atomic adapter exposes expected(trace) and close()",
                adapter=bundle.artifact.adapter,
                got=type(delegate).__name__))
        self.bundle = bundle
        self.artifact = bundle.artifact
        self.contract = contract
        self._delegate = delegate

    def close(self):
        self._delegate.close()

    def expected(self, trace, *, progress):
        """Expected transaction streams, keyed by logical output endpoint."""
        return self._delegate.expected(trace, progress=progress)


class SlotEvent:
    """What happened at one slot, as the reference is told about it.

    The reference needs more than "a transaction was accepted": whether the slot was a
    reset, which clock domains ticked, and what the environment drove are all things a
    model may be entitled to see. Passing them as a record rather than as a bare payload
    is what lets a later reference kind take them without changing every caller -- and it
    is what makes "the reference saw the run" checkable rather than assumed.
    """

    __slots__ = ("accepted_inputs", "controls", "index", "reset", "ticks")

    def __init__(self, index, reset=False, ticks=(), controls=(),
                 accepted_inputs=()):
        self.index = int(index)
        self.reset = bool(reset)
        self.ticks = tuple(ticks)
        self.controls = tuple(controls)
        #: `(endpoint, payload)` pairs for the **input** transfers at this slot, payload
        #: `None` where an offer was not taken.
        #:
        #: **Inputs only, and the name says so.** This carried the whole accepted batch,
        #: which the driver fills with the output transfers too -- so a reference could
        #: read what the artifact produced and hand it straight back as the expected
        #: output. A wrong design then scored `passed=True` with `mismatch_count=0`,
        #: because both sides of the comparison came from the same place. What the
        #: artifact produced flows one way, into the matcher, and never into `B*`.
        self.accepted_inputs = tuple(accepted_inputs)

    def payload(self, endpoint):
        for name, value in self.accepted_inputs:
            if name == endpoint:
                return value
        return None

    def __repr__(self):
        return (f"SlotEvent({self.index}"
                f"{', reset' if self.reset else ''}, "
                f"{len(self.accepted_inputs)} accepted)")


def _require_task_reference(task, reference):
    """Establish that ``reference`` is exactly the bundle admitted for ``task``."""
    # A ``LoadedReference`` is deliberately not pre-bound to an interface: its loader
    # freezes source/configuration and each run constructs the appropriate adapter from
    # the already-admitted task contract.  A live ``ReferenceModel`` *is* bound, so its
    # contract must match here.  Treating the bundle as though it were a live adapter
    # made every multi-clock comparison fail before ``AtomicReferenceModel`` could bind
    # it to the task.
    theirs = getattr(reference, "contract", None)
    if type(reference) is not LoadedReference \
            and (theirs is None or theirs.digest != task.contract.digest):
        raise Refused(Refusal(
            "reference-not-of-this-contract",
            "this reference is bound to a different contract than the task, or to none, "
            "so agreement with it is not agreement with the reference the task declares",
            task=task.contract.digest,
            reference=getattr(theirs, "digest", None)))
    if not task.reference:
        raise Refused(Refusal(
            "reference-not-frozen",
            "this task did not freeze a reference at admission, so there is nothing to "
            "check the running one against and the verdict would be relative to "
            "whatever the caller happened to pass",
            hint="admit(contract, realization, reference=...)"))
    got = getattr(getattr(reference, "artifact", None), "digest", "")
    if got != task.reference:
        raise Refused(Refusal(
            "reference-not-the-frozen-one",
            "this is not the reference the task froze at admission, so agreement "
            "with it says nothing about the model the task declares",
            frozen=task.reference, running=got))


def _begin_reference_run(reference, trace, *, global_events=False):
    """Put one fresh reference at the stimulus's initial boundary.

    ``trace.initial_state`` is an observation of the artifact that produced the
    trace.  It is audit evidence, not a portable reference state: an RTL register
    image and a Behavior model need not even have the same names or representation.
    Only the stimulus may request a nonstandard initial-state policy, and this
    adapter currently has no task-declared map with which to realize one.
    """

    if trace.stimulus.initial_state:
        raise Refused(Refusal(
            "reference-initial-state-policy-unmapped",
            "the stimulus requests an initial-state policy, but this reference "
            "adapter has no task-declared map from that policy to reference state",
            policy=[key for key, _value in trace.stimulus.initial_state]))

    if type(reference) is ReferenceModel:
        from .reference_memory import bind_external_memory

        # Install a fresh run's stimulus image once. Later computation resets
        # preserve this external store independently of model control state.
        bind_external_memory(
            reference.model, reference.contract, trace.stimulus)

    started = bool(trace.stimulus.slots) and (
        bool(trace.stimulus.slots[0].resets) if global_events
        else trace.stimulus.slots[0].reset)
    if not started:
        reference.reset()


def _slot_event(index, slot, record, inputs, *, global_events=False):
    # A timestamped control-only event is not a Behavior tick. Reset still
    # takes effect without an active edge, including an asynchronous pulse.
    if global_events and not slot.ticks and not slot.resets:
        return None
    driven = {endpoint.name for endpoint in inputs}
    return SlotEvent(
        index, reset=bool(slot.resets) if global_events else slot.reset,
        ticks=slot.ticks, controls=slot.controls,
        accepted_inputs=[
            (name, payload) for name, payload in record.accepted if name in driven
        ],
    )


def expected_of(task, reference, trace, *, progress=None):
    """``B*(sigma, a_A(xi))``: the reference over the artifact's own accepted trace.

    The appendix defines the expected outputs as the reference applied to the artifact's
    *accepted transaction trace*, not to the stimulus. The difference is invisible only
    when nothing ever stalls: replaying both sides against the same stimulus lets a
    design that stalls consume a different set of transactions than the reference does,
    and then the two output streams answer different questions.

    **One step per slot, and every slot.** The reference is advanced with what happened
    at that slot -- the accepted batch, whether reset was asserted, and the environment's
    controls -- so a slot in which nothing was accepted is still a step. An earlier
    version stepped only on accepted transactions when there was an input endpoint, so a
    reset-free idle slot, a tick and a control change were all invisible to a reference
    that is entitled to see them; and where there was no input endpoint it ticked every
    non-reset slot, which is the same rule written twice with different answers.

    A reference that wants less can take less: `step` is called when a transaction was
    accepted and `tick` otherwise, and a model with no `tick` simply is not advanced by
    idle slots -- which is the common case and is now a property of the model rather than
    of this loop.

    Shapes this cannot express **refuse** rather than taking the first endpoint. Reading
    `inputs()[0]` on a two-endpoint contract silently describes a different task.

    A bundle may be passed instead of a live model, and one run's instance is built here.
    """
    # **The reference is the one this task froze.** Checked here rather than assumed:
    # without it the run establishes that the artifact agrees with some object the
    # caller passed in, which is not the same statement as agreeing with `B*`.
    _require_task_reference(task, reference)
    if not hasattr(reference, "advance"):
        raise Refused(Refusal(
            "reference-has-no-advance",
            "a reference is stepped once per slot through `advance(event)`; one without "
            "it would have to be driven by this loop guessing which slots mean "
            "something to it",
            reference=type(reference).__name__))

    inputs, outputs = task.contract.inputs(), task.contract.outputs()
    if len(inputs) > 1 or len(outputs) > 1:
        raise Refused(Refusal(
            "reference-multi-endpoint-unsupported",
            "this reference interface advances one input endpoint and reads one output "
            "endpoint. Taking the first of several would describe a different task, and "
            "which transactions a multi-endpoint reference owes per slot is T3-13's "
            "semantics",
            inputs=[e.name for e in inputs], outputs=[e.name for e in outputs]))
    source = inputs[0] if inputs else None
    sink = outputs[0] if outputs else None

    # The initial state is the run's, and the reference starts from the same place. A
    # `reset()` here **and** on the first reset slot was two resets, which for a model
    # counting them is two different runs.
    global_events = uses_global_events(task.contract, trace.stimulus)
    _begin_reference_run(reference, trace, global_events=global_events)

    # The endpoints the **environment** drives. Everything else in a record's accepted
    # batch is what the artifact produced, and that must not reach the reference.
    from .reference_progress import ReferenceProgress

    if progress is None:
        progress = ReferenceProgress(task.contract, behavior=task.realization.kind == "behavior")
    expected = []
    for index, (slot, record) in enumerate(zip(trace.stimulus.slots, trace.records)):
        event = _slot_event(index, slot, record, inputs, global_events=global_events)
        if event is not None and event.reset:
            progress.before_reset(
                (clock.name for clock in task.contract.clocks),
                {} if sink is None else {sink.name: expected}, index)
        produced = (() if event is None else reference.advance(event)) or ()
        expected.extend(produced)
        if progress.has_transaction_monitors:
            progress.observe_reference_transactions(record, index, {} if sink is None else {sink.name: produced})
        progress.observe(record)
    del source, sink
    return expected


def expected_by_endpoint_of(task, reference, trace, *, progress=None):
    """Expected streams by endpoint for the single- or multi-clock reference.

    Keeping this derivation inside the reference boundary is the multi-endpoint version
    of :func:`expected_of`: a caller cannot splice the candidate's own output back in as
    the expected stream.  The historical single-output result remains unchanged.
    """
    return _expected_run_of(task, reference, trace, progress=progress,
                            include_memory=False)[0]


def expected_run_of(task, reference, trace, *, progress=None):
    """Output streams and final memory from the same, single reference execution."""
    return _expected_run_of(task, reference, trace, progress=progress,
                            include_memory=True)


def _expected_run_of(task, reference, trace, *, progress, include_memory):
    from .memory_result import model_memory_result, require_memory_result_path
    from .reference_progress import ReferenceProgress

    _require_task_reference(task, reference)
    if include_memory:
        require_memory_result_path(task.contract, trace.stimulus)
    if progress is None:
        progress = ReferenceProgress(task.contract, behavior=task.realization.kind == "behavior")
    if len(task.contract.clocks) > 1:
        atomic = AtomicReferenceModel(reference, task.contract)
        try:
            return atomic.expected(trace, progress=progress), None
        finally:
            atomic.close()
    owned = type(reference) is LoadedReference
    model = (reference.instantiate(task.contract) if owned else reference)
    try:
        if type(model) is ReferenceModel:
            model._source_recorder = progress.begin_source(model.bundle, model.model, trace)
        def finish(outputs):
            memory = (model_memory_result(model.model, task.contract)
                      if include_memory else None)
            return outputs, memory

        inputs = task.contract.inputs()
        outputs = task.contract.outputs()
        if len(inputs) > 1 or len(outputs) > 1:
            if type(model) is not ReferenceModel:
                raise Refused(Refusal(
                    "reference-slot-interface-unavailable",
                    "multi-endpoint comparison needs the loader-bound slot reference "
                    "adapter rather than a live model supplied beside an identity",
                    got=type(model).__name__))
            global_events = uses_global_events(task.contract, trace.stimulus)
            _begin_reference_run(model, trace, global_events=global_events)
            expected = {endpoint.name: [] for endpoint in outputs}
            for index, (slot, record) in enumerate(
                    zip(trace.stimulus.slots, trace.records)):
                event = _slot_event(index, slot, record, inputs, global_events=global_events)
                if event is not None and event.reset:
                    progress.before_reset(
                        (clock.name for clock in task.contract.clocks), expected, index)
                rows = {} if event is None else model.advance_by_endpoint(event)
                for endpoint, values in rows.items():
                    expected[endpoint].extend(values)
                if progress.has_transaction_monitors:
                    progress.observe_reference_transactions(record, index, rows)
                progress.observe(record)
            return finish(expected)
        values = expected_of(task, model, trace, progress=progress)
        return finish({} if not outputs else {outputs[0].name: values})
    finally:
        if owned:
            model.close()
