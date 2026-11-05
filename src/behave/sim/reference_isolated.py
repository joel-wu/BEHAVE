"""A reference adapter that runs the agent's model in a process of its own.

T3-09 retired the former public ``transaction/1`` adapter because it compiled the
recorded bytes and executed them in the verifier process.  A module imported there was
shared through ``sys.modules`` across runs, module globals and class variables outlived
every instance, and agent code ran on the verifier side of the boundary.

So this adapter builds each model in a child process, from the recorded sources and
nothing else:

* **Every run is a fresh execution environment.** A new child per `new_model()`, so
  module globals, class variables and `sys.modules` cannot carry from run N to run N+1.
  For the accumulating designs in this population that is the difference between two
  independent tests and one test twice.
* **The dependency closure is what the digest is over.** The sources -- `behavior.py` and
  every helper the loader recorded beside it -- are materialised into a directory of this
  bundle's own, and that directory goes on the child's import path *first*. A model
  importing `fp_lib` gets the `fp_lib.py` whose bytes are in the digest, not whichever
  same-named module the parent happens to have importable. In the in-process adapter the
  import simply failed: 43 tasks of this population were refused for
  `authority-no-reference` because their model imports that helper, and its bytes sat in
  the digest with nothing able to import them.

  What is importable is still only what `behavior/admission._APPROVED_MODULES` admits.
  This does not widen that list and should not: which modules a model may depend on is
  an admission question, and a loader that made every recorded name importable would
  answer it by accident. What it fixes is *which copy* of an approved module runs.
* **A model cannot take the verifier with it.** `behavior/isolated` supplies the limits
  and the audit hook, and its module docstring is exact about what they contain and what
  needs a namespace or a container instead. A model that hangs, allocates, writes, forks
  or calls `os._exit` ends its own child.

What this does **not** claim. The child reads files it has the path of. Two runs are
isolated from each other and neither is isolated from the machine. And the crossing is
JSON, so a model whose answer is not JSON-representable is refused by this adapter where
the in-process one would have handed the object straight through -- that is a narrowing,
stated here rather than discovered as a refusal.

The retired and isolated adapters have different names because a reference digest names
its adapter.  Nothing silently upgrades: old ``transaction/1`` records remain
identifiable but the public loader refuses to execute them.
"""

from __future__ import annotations

import atexit
import hashlib
import shutil
import tempfile
from functools import partial
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.sim.core.source_rules import EMPTY_SOURCE_RULES

from ..core.paths import work_root
from .behavior.isolated import Broken, IsolatedModel, Limits, Timeout
from .core.compare import normalise
from .core.failure import CandidateFault, FailureRaised, RunFailure
from .core.reference import (
    ReferenceFactory,
    register_atomic_loader,
    register_loader,
    register_source_observer,
)
from .core.reference_observation import (
    VERSION as SOURCE_OBSERVATION_VERSION,
)
from .core.reference_observation import (
    ReferenceSourceRecorder,
)
from .core.refusal import Refusal, Refused

__all__ = ["ADAPTER", "ATOMIC_ADAPTER_VERSION", "isolated_loader"]

#: The adapter name a reference built this way carries in its digest.
ADAPTER = "transaction/isolated-6"

#: Atomic handler dispatch is an additional execution contract, so its version enters
#: the reference artifact identity separately from the transaction loader's name.
ATOMIC_ADAPTER_VERSION = "transaction-isolated-atomic/6"

#: Standalone wall allowance and per-answer CPU allowance. Scoped source calls
#: and their transport share the enclosing score/search/analysis deadline;
#: host GC or decoding must not turn that shared budget into a reference fault.
#: CPU and memory limits remain enforced independently inside the model process.
LIMITS = Limits(seconds=defaults.SIM_REFERENCE_CPU_SECONDS,
                memory_bytes=defaults.SIM_REFERENCE_RSS_BYTES,
                wall_seconds=defaults.SIM_REFERENCE_WALL_SECONDS,
                processes=defaults.SIM_REFERENCE_PROCESSES)

#: Directories this process materialised, removed when it exits. Keyed by the digest of
#: the sources, so two bundles over identical bytes share one and a bundle that is loaded
#: twice does not write twice.
_MATERIALISED: dict = {}


def _still_the_recorded_bytes(where, sources):
    """Which materialised files no longer hold what was recorded. `[]` where all do.

    The directory is written once and read by every child that builds a model from this
    bundle. Nothing in the child can create a file -- `RLIMIT_FSIZE` is zero and the
    audit hook refuses opening for writing -- but the hook does not see `unlink`,
    `rename` or `symlink`, and a container is what would. So the bytes are checked
    against the digest again before each child starts: cheap, these are small files, and
    it turns "a later child could replace what an earlier one imported" from an argument
    into a check.
    """
    out = []
    for name, body in sources.items():
        target = Path(where) / name
        try:
            if target.read_bytes() != bytes(body):
                out.append(f"{name}: the file is not the bytes that were recorded")
        except OSError as exc:
            out.append(f"{name}: {type(exc).__name__}")
    return out


def _materialise(sources):
    """The recorded sources as a directory, and the directory's path.

    Written by the **parent**. The child runs with `RLIMIT_FSIZE` at zero and an audit
    hook that refuses opening anything for writing, so it could not do this itself -- and
    that is the right way round: the files it imports are ones this side wrote from bytes
    the digest covers.
    """
    # Keyed by the bytes, through a digest. Keying it by `(name, length)` -- which this
    # did for about an hour -- makes two different files of one length share a directory,
    # so the second bundle would import the first one's code while carrying its own
    # digest. That is the substitution this whole module is about, reintroduced in the
    # cache.
    digest = hashlib.sha256()
    for name, body in sorted(sources.items()):
        digest.update(name.encode())
        digest.update(hashlib.sha256(bytes(body)).digest())
    key = digest.hexdigest()
    got = _MATERIALISED.get(key)
    if got is not None:
        # **The cache is checked before it is used.** Two bundles over identical bytes
        # share a directory, so one that is emptied or edited -- by a sweep of the work
        # root, by a test that mutates it, by anything the audit hook does not see -- is
        # a directory the next *load* would hand out as though it still held what it
        # recorded. Load-time repairs from the bytes, which are the truth here; run-time
        # refuses, in `build`, because by then a model has already been built from what
        # was there and a silent repair would hide the substitution.
        if not _still_the_recorded_bytes(got, sources):
            return got
        _MATERIALISED.pop(key, None)
        shutil.rmtree(got, ignore_errors=True)
    # Under the work root, not the system temporary directory: everything this build
    # writes lives where the rest of its working files do, and a run on a machine whose
    # `/tmp` is small or swept mid-run is one whose reference disappears under it.
    root = work_root() / "references"
    root.mkdir(parents=True, exist_ok=True)
    where = Path(tempfile.mkdtemp(prefix="ref-", dir=str(root)))
    for name, body in sources.items():
        target = where / name
        # A source name is a file beside the model, not a path into the filesystem. The
        # names come from a loader's own dictionary today; refusing the general shape
        # here is cheaper than finding out when one does not.
        if target.resolve().parent != where.resolve():
            shutil.rmtree(where, ignore_errors=True)
            raise Refused(Refusal(
                "reference-source-name-is-a-path",
                "a recorded source is a file beside the model; a name that resolves "
                "outside the directory this materialises would put a file where the "
                "digest does not say it is",
                source=name))
        target.write_bytes(bytes(body))
    _MATERIALISED[key] = str(where)
    return str(where)


@atexit.register
def _clean():
    for where in _MATERIALISED.values():
        shutil.rmtree(where, ignore_errors=True)
    _MATERIALISED.clear()


def isolated_loader(sources, config):
    """`sources` and `config` to a factory that yields one child process per call.

    Registered under `ADAPTER`. The signature is the loader contract from
    `core/reference.register_loader`: bytes in, a callable out, and the callable must
    produce a **fresh** model each time -- which here means a fresh process.
    """
    body = sources.get("behavior.py")
    if body is None:
        raise Refused(Refusal(
            "reference-loader-no-model-source",
            "this adapter builds a model from `behavior.py`, and the sources do not "
            "include one",
            sources=sorted(sources)))
    # **What the model calls, before it is asked to run.** A model runs in the child
    # with the admitted builtins and nothing else, so a call to anything outside them
    # raises where it is reached -- and the caller saw `reference-failed: NameError` on
    # every stimulus rather than one statement about the model. One design in this
    # population calls `locals()`. The source says so before any of that.
    from .behavior.isolated import _trusted_source_protocol_facts
    from .behavior.source_syntax import require_model_identifier

    try:
        selected = (config or {}).get("class")
        if selected is not None:
            require_model_identifier(selected, "behavior.py")
        facts, _offers, frontend = _trusted_source_protocol_facts(
            body.decode("utf-8", "replace"), "behavior.py",
            selected)
    except CandidateFault as exc:
        raise FailureRaised(RunFailure.reference("reference-preparation", exc.refusal)) from None
    except Broken as exc:
        # Source facts are a judge-owned prerequisite, not an exception the public
        # adapter may leak.  Preserve the isolation layer's stable refusal kind when it
        # supplied one; the prose remains diagnostic only.
        raise FailureRaised(RunFailure.infrastructure("reference-preparation", Refusal(
            exc.code or "reference-source-facts-unavailable",
            "the isolated Behavior source judge could not inspect this reference",
            adapter=ADAPTER, worker_detail=exc.detail or str(exc)))) from None
    unanswerable = facts["unanswerable_calls"]
    if unanswerable:
        raise Refused(Refusal(
            "reference-model-calls-what-this-build-does-not-provide",
            "this model calls names that will not exist when it runs: it would raise on "
            "whichever slot reached them, once per stimulus, and none of those runs "
            "would establish anything",
            adapter=ADAPTER, calls=unanswerable, total=len(unanswerable)))

    where = _materialise(dict(sources))
    kwargs = {k: v for k, v in dict(config or {}).items() if k != "class"}
    text = body.decode("utf-8", "replace")
    cls = (config or {}).get("class")

    # **Not `behavior.isolated.isolated_model`.** That factory closes the model it made
    # last, which is right for `model_audit` -- one trace at a time, and a dead child
    # costs nothing -- and wrong here: `new_model()` promises an instance no live run is
    # already using, and closing the previous one makes the *previous* run's reference
    # stop answering. Each call builds a child and hands it over; the child ends when the
    # model it belongs to is dropped.
    # Every recorded source except the model itself, as a module name and the file this
    # materialised it into. The child executes these before the model, so what an
    # `import fp_lib` resolves to is the bytes in this bundle's digest -- see the child
    # in `behavior/isolated`, and the measurement that made it necessary.
    recorded = {name[:-3]: str(Path(where) / name)
                for name in sources
                if name.endswith(".py") and name != "behavior.py"}

    every = dict(sources)

    def build(source_rules=EMPTY_SOURCE_RULES, clock_domains=None):
        changed = _still_the_recorded_bytes(where, every)
        if changed:
            raise Refused(Refusal(
                "reference-sources-changed-under-us",
                "the files this bundle materialised are no longer the bytes its digest "
                "is over, so a model built from them now is not the model this "
                "reference identifies",
                adapter=ADAPTER, files=changed))
        try:
            return IsolatedModel(text, file="behavior.py", cls=cls, limits=LIMITS,
                                 strict_module=False, snapshot_policy="on-demand",
                                 path=(where,), config=kwargs, recorded=recorded,
                                 source_rules=source_rules, clock_domains=clock_domains)
        except CandidateFault as exc:
            raise Refused(Refusal(
                "reference-model-would-not-start",
                "the reference source failed before comparison could start",
                adapter=ADAPTER, cause=exc.refusal.as_dict())) from None
        except Broken as exc:
            raise FailureRaised(RunFailure.infrastructure("reference-preparation", Refusal(
                exc.code or "reference-worker-unavailable",
                "the isolated reference worker stopped answering during preparation",
                adapter=ADAPTER, worker_detail=exc.detail or str(exc)))) from None
        except Timeout as exc:
            raise Refused(Refusal(
                "reference-model-would-not-start",
                "the reference model did not finish preparing within its execution allowance",
                adapter=ADAPTER, detail=f"{type(exc).__name__}: {exc}")) from None

    def bind_contract(contract):
        rules = contract.source_rules
        domains = (tuple(clock.name for clock in contract.clocks)
                   if len(contract.clocks) > 1 else None)
        if not rules and domains is None:
            return factory
        try:
            _, _, catalogue = _trusted_source_protocol_facts(
                text, "behavior.py", cls, rules, domains)
        except Broken as exc:
            raise FailureRaised(RunFailure.infrastructure("reference-preparation", Refusal(
                exc.code or "reference-source-facts-unavailable",
                "the reference source judge could not bind task operation rules",
                adapter=ADAPTER, worker_detail=exc.detail or str(exc)))) from None
        return ReferenceFactory(partial(build, rules, domains), catalogue)

    factory = ReferenceFactory(build, frontend, bind_contract)
    return factory


register_loader(ADAPTER, isolated_loader)


def _source_recorder(model):
    from .behavior.isolated import hits_of

    if type(model) is not IsolatedModel:
        raise TypeError("isolated reference observation requires its actual model proxy")

    def take_hits():
        log = hits_of(model)
        reached = tuple(log)
        log.clear()
        return reached

    return ReferenceSourceRecorder(model._source_catalogue, take_hits)


register_source_observer(ADAPTER, _source_recorder, SOURCE_OBSERVATION_VERSION)


class _IsolatedAtomicReference:
    """The isolated bundle under the global atomic-slot Behavior semantics.

    Within one domain, the handler signature and return row are the flattened products
    of that domain's logical input and output endpoints in task-owned order.  A partial
    input product is refused: invoking a handler with guessed values for the endpoints
    the artifact did not accept would compare two different runs.
    """

    __slots__ = ("_inputs", "_outputs", "bundle", "contract", "model")

    def __init__(self, bundle, contract):
        model = bundle.new_model(contract=contract)
        if type(model) is not IsolatedModel:
            raise Refused(Refusal(
                "atomic-reference-model-not-isolated",
                "the isolated reference adapter returned a different model type",
                got=type(model).__name__))
        domains = tuple(sorted(clock.name for clock in contract.clocks))
        if len(domains) < 2:
            model.close()
            raise Refused(Refusal(
                "atomic-reference-needs-several-clocks",
                "a one-clock task uses the transaction reference adapter"))
        try:
            model.atomic_init_from_source(domains)
            inputs = self._by_domain(contract.inputs(), "input")
            outputs = self._by_domain(contract.outputs(), "output")
        except BaseException:
            model.close()
            raise
        self.bundle = bundle
        self.contract = contract
        self.model = model
        self._inputs = inputs
        self._outputs = outputs

    @staticmethod
    def _by_domain(endpoints, direction):
        out = {}
        for endpoint in endpoints:
            out.setdefault(endpoint.clock, []).append(endpoint)
        # TaskContract owns the canonical endpoint order.  A process_<domain>
        # signature is the flattened field product in that order; no source or
        # artifact fact is allowed to reorder it.
        return {domain: tuple(rows) for domain, rows in out.items()}

    def close(self):
        self.model.close()

    def expected(self, trace, *, progress):
        """Expected transaction streams, keyed by logical output endpoint."""
        from .behavior.multiclock import DomainInvocation

        if trace.stimulus.clock_schedule is None:
            raise Refused(Refusal(
                "atomic-reference-schedule-missing",
                "a multi-clock trace carries no timestamped GlobalSchedule"))
        if trace.stimulus.initial_state:
            raise Refused(Refusal(
                "reference-initial-state-policy-unmapped",
                "the stimulus requests an initial-state policy, but the atomic "
                "reference adapter has no task-declared map from that policy to "
                "reference state"))
        progress.begin_source(self.bundle, self.model, trace)
        expected = {
            endpoint.name: []
            for endpoints in self._outputs.values()
            for endpoint in endpoints
        }
        driven = {
            endpoint.name
            for endpoints in self._inputs.values()
            for endpoint in endpoints
        }
        for index, (scheduled, record) in enumerate(zip(
                trace.stimulus.clock_schedule.slots, trace.records)):
            progress.before_reset(scheduled.resets, expected, index)
            accepted = {name: payload for name, payload in record.accepted
                        if name in driven and payload is not None}
            invocations = []
            for domain in scheduled.events:
                if domain in scheduled.resets:
                    continue
                endpoints = self._inputs.get(domain, ())
                if not endpoints:
                    # An inputless domain handler is a reference-visible tick.
                    invocations.append(DomainInvocation(domain, ()))
                    continue
                payloads = tuple(accepted.get(endpoint.name)
                                 for endpoint in endpoints)
                present = tuple(payload is not None for payload in payloads)
                if any(present) and not all(present):
                    raise Refused(Refusal(
                        "atomic-reference-input-product-partial",
                        "one process_<domain> invocation denotes the declared input "
                        "endpoint product, but this artifact accepted only part of "
                        "that product in the slot",
                        domain=domain,
                        accepted=[endpoint.name for endpoint, yes
                                  in zip(endpoints, present) if yes],
                        missing=[endpoint.name for endpoint, yes
                                 in zip(endpoints, present) if not yes]))
                if all(present):
                    invocations.append(DomainInvocation(
                        domain,
                        tuple(value for payload in payloads for value in payload)))
            transition = self.model.atomic_step(
                scheduled, tuple(invocations))
            progress.observe_source(transition.hits)
            produced = {} if progress.has_transaction_monitors else None
            for domain, rows in transition.outputs:
                endpoints = self._outputs.get(domain, ())
                if not endpoints:
                    raise Refused(Refusal(
                        "atomic-reference-output-endpoint-missing",
                        "a domain handler emitted transactions but the task declares "
                        "no output endpoint in that domain",
                        domain=domain, transactions=len(rows)))
                ports = tuple(field.port for endpoint in endpoints
                              for field in endpoint.fields)
                for row in rows:
                    values = dict(row)
                    if set(values) != set(ports):
                        raise Refused(Refusal(
                            "atomic-reference-output-shape",
                            "a domain handler's output does not carry exactly the "
                            "fields of its declared logical output product",
                            domain=domain,
                            endpoints=[endpoint.name for endpoint in endpoints],
                            expected=list(ports), got=sorted(values)))
                    for endpoint in endpoints:
                        payload = normalise(
                            endpoint,
                            tuple(values[field.port]
                                  for field in endpoint.fields))
                        expected[endpoint.name].append(payload)
                        if produced is not None:
                            produced.setdefault(endpoint.name, []).append(payload)
            if produced is not None:
                progress.observe_reference_transactions(record, index, produced)
            progress.observe(record)
        return expected


def _isolated_atomic_loader(bundle, contract):
    return _IsolatedAtomicReference(bundle, contract)


register_atomic_loader(ADAPTER, _isolated_atomic_loader, ATOMIC_ADAPTER_VERSION)
