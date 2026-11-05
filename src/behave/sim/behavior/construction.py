"""Authenticated facts at the Behavior step boundary.

Construction is executable source semantics: the model class is instantiated
and reset in the same isolated environment used by replay.  A source fold may
explain a value, but it does not own the boundary.  This module records one
complete, repeatable image of that boundary and exposes only the finite values
SolverIR can carry.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

from ..symbolic.native_values import (
    DEFAULT_WIRE_NODES,
    NativeValueError,
    WireDecoder,
    WireEncoder,
    freeze,
    thaw,
)

CONSTRUCTION_VERSION = 6
# The carrier itself is bounded in the child, but an entry-count bound can
# still retain many maximum-sized images.  Bound the material actually kept;
# an unusually large valid image remains usable for its caller but is not
# cached.
_CACHE_WEIGHT_LIMIT = defaults.SIM_CONSTRUCTION_CACHE_BYTES
_SHARE_ROOT_LIMIT = defaults.SIM_CONSTRUCTION_SHARE_ROOTS
_SHARE_ID_LIMIT = DEFAULT_WIRE_NODES
_SHARE_PAIR_LIMIT = defaults.SIM_CONSTRUCTION_SHARE_PAIRS
_MAX_CONCURRENT_BUILDS = defaults.SIM_CONSTRUCTION_WORKERS
_CACHE = OrderedDict()
_CACHE_WEIGHT = 0
_CACHE_LOCK = threading.RLock()
_INFLIGHT = {}


def _cache_before_fork():
    """Freeze the cache tables while a process image is copied."""
    _CACHE_LOCK.acquire()


def _cache_after_fork_parent():
    _CACHE_LOCK.release()


def _cache_after_fork_child():
    """Give the child process-local single-flight state.

    Completed facts are immutable and may safely remain in the child's
    copy-on-write cache.  An in-flight Future, however, is owned by a thread
    in the parent and can never complete in the child.  Replacing the lock
    also avoids inheriting the ownership state of a lock held by another
    parent thread.
    """
    global _CACHE_LOCK, _INFLIGHT
    _CACHE_LOCK = threading.RLock()
    _INFLIGHT = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_cache_before_fork,
        after_in_parent=_cache_after_fork_parent,
        after_in_child=_cache_after_fork_child,
    )


class _BuildCapacity:
    """A bounded construction lease shared by threads and worker processes.

    A threading semaphore alone is not an aggregate bound for the lowering
    census: its multiprocessing workers import this module independently.
    Advisory slot locks in the node-local temporary directory survive that
    boundary and are released by the kernel if an owner exits.  The local
    semaphore keeps excess threads from spinning on those locks.
    """

    def __init__(self, capacity, namespace="v1"):
        self.capacity = int(capacity)
        if self.capacity < 1:
            raise ValueError("construction capacity must be positive")
        self.namespace = str(namespace)
        allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        if not self.namespace or any(c not in allowed for c in self.namespace):
            raise ValueError("construction capacity namespace is not plain")
        self._local = threading.BoundedSemaphore(self.capacity)
        self._held = threading.local()
        self._fds = set()
        self._fd_lock = threading.Lock()
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=self._after_fork)

    def _after_fork(self):
        # A fork inherits every descriptor held by every parent thread.  The
        # child must close its duplicate without LOCK_UN (the open-file
        # description, and hence the flock, is shared with the live parent).
        for fd in self._fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = set()
        self._fd_lock = threading.Lock()
        self._local = threading.BoundedSemaphore(self.capacity)
        self._held = threading.local()

    def _directory(self):
        root = scratch_root() / (
            f"behave-construction-{os.getuid()}-{self.namespace}")
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() \
                or stat.S_IMODE(info.st_mode) & 0o077:
            raise ConstructionUnavailable(
                "the construction capacity directory is not private")
        return root

    def __enter__(self):
        self._local.acquire()
        try:
            root = self._directory()
            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            while True:
                for index in range(self.capacity):
                    path = root / f"slot-{index}"
                    fd = os.open(path, flags, 0o600)
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) \
                            or info.st_uid != os.getuid() \
                            or stat.S_IMODE(info.st_mode) & 0o077:
                        os.close(fd)
                        raise ConstructionUnavailable(
                            "a construction capacity slot is not private")
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        os.close(fd)
                        continue
                    try:
                        with self._fd_lock:
                            self._fds.add(fd)
                        held = getattr(self._held, "fds", None)
                        if held is None:
                            held = []
                            self._held.fds = held
                        held.append(fd)
                    except BaseException:
                        with self._fd_lock:
                            self._fds.discard(fd)
                        try:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        finally:
                            os.close(fd)
                        raise
                    return self
                time.sleep(defaults.SIM_CONSTRUCTION_POLL_SECONDS)
        except BaseException:
            self._local.release()
            raise

    def __exit__(self, _kind, _value, _traceback):
        held = self._held.fds
        fd = held.pop()
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            with self._fd_lock:
                self._fds.discard(fd)
            os.close(fd)
            self._local.release()


_BUILD_SLOTS = _BuildCapacity(_MAX_CONCURRENT_BUILDS)


class ConstructionUnavailable(Exception):
    """The selected model could not establish a repeatable step boundary."""


@dataclass(frozen=True)
class ConstructionFacts:
    """Deeply immutable, repeatable construction facts."""

    _value_images: tuple
    _unsupported_items: tuple
    shared_pairs: tuple
    opaque_fields: tuple
    _exact_images: tuple = ()
    boundary_fields: tuple = ()

    def values(self):
        return {name: _thaw_step_image(image)
                for name, image in self._value_images}

    def unsupported(self):
        return dict(self._unsupported_items)

    def exact_values(self):
        """All faithfully transported field values, including constants.

        ``values`` is deliberately narrower: it is the Boolean/integer/tree
        carrier a SolverIR state variable can hold.  Reset-only configuration
        may use strings or finite containers that never become IR state; the
        exact construction record still owns those constants and their types.
        """
        return {name: thaw(image) for name, image in self._exact_images}

    def matches(self, name, value):
        try:
            image = freeze(value)
        except NativeValueError:
            return False
        return dict(self._exact_images).get(name) == image

    def for_boundary(self, fields):
        """Remove state whose entry value is supplied by the closed loop.

        Construction authenticates the source object immediately after reset.  A
        task-owned boundary may then replace a declared field before the first step;
        external memories are the canonical example.  Carrying the discarded source
        value into the exact-analysis worker is both semantically irrelevant and, for
        a deep memory, needlessly linear in its address space.  This projection records
        the replacement domain explicitly and retains every other construction fact.
        """

        names = tuple(sorted(set(fields)))
        if any(type(name) is not str or not name.isidentifier()
               for name in names):
            raise TypeError("construction boundary fields are identifiers")
        if self.boundary_fields:
            raise ValueError("a construction boundary projection is applied once")
        domain = ({name for name, _image in self._exact_images}
                  | {name for name, _why in self._unsupported_items})
        missing = sorted(set(names) - domain)
        if missing:
            raise ValueError(
                "construction boundary fields are absent from the source image: "
                + ", ".join(missing))
        removed = set(names)
        return ConstructionFacts(
            tuple(row for row in self._value_images if row[0] not in removed),
            tuple(row for row in self._unsupported_items if row[0] not in removed),
            tuple(row for row in self.shared_pairs
                  if row[0] not in removed and row[1] not in removed),
            tuple(name for name in self.opaque_fields if name not in removed),
            tuple(row for row in self._exact_images if row[0] not in removed),
            names,
        )

    def as_dict(self):
        """Return the closed JSON carrier used by isolated frontend jobs.

        The private tuples above are Python-native cache images.  Sending them through
        ``json`` directly would erase tuples, bytes and exact scalar types.  The native
        value wire codec is already the authority for that boundary, so the public job
        image carries the exact construction mapping through that codec and records the
        two sharing relations separately.
        """

        encoder = WireEncoder(DEFAULT_WIRE_NODES)
        return {
            "version": CONSTRUCTION_VERSION,
            "values": encoder.encode(self.exact_values()),
            "unsupported": [[name, why]
                            for name, why in self._unsupported_items],
            "shared_pairs": [[left, right]
                             for left, right in self.shared_pairs],
            "opaque_fields": list(self.opaque_fields),
            "boundary_fields": list(self.boundary_fields),
        }

    @classmethod
    def from_dict(cls, value):
        """Decode one canonical, finite construction-facts job reply."""

        fields = {
            "version", "values", "unsupported", "shared_pairs",
            "opaque_fields", "boundary_fields",
        }
        if type(value) is not dict or set(value) != fields:
            raise TypeError(
                "a construction-facts image has its complete closed field set")
        if type(value["version"]) is not int \
                or value["version"] != CONSTRUCTION_VERSION:
            raise ValueError("a construction-facts image has the supported version")
        try:
            exact_values = WireDecoder(DEFAULT_WIRE_NODES).decode(value["values"])
        except NativeValueError as exc:
            raise ValueError(f"invalid construction value carrier: {exc}") from None
        if type(exact_values) is not dict \
                or any(type(name) is not str or not name.isidentifier()
                       for name in exact_values):
            raise TypeError(
                "construction exact values are an identifier-keyed exact mapping")

        def pairs(rows, what):
            if type(rows) is not list:
                raise TypeError(f"construction {what} is a finite exact list")
            out = []
            for row in rows:
                if type(row) is not list or len(row) != 2 \
                        or any(type(item) is not str or not item for item in row):
                    raise TypeError(
                        f"each construction {what} row contains two exact strings")
                out.append(tuple(row))
            if out != sorted(set(out)):
                raise ValueError(
                    f"construction {what} rows are sorted and unique")
            return tuple(out)

        unsupported = pairs(value["unsupported"], "unsupported-value")
        shared = pairs(value["shared_pairs"], "sharing")
        opaque = value["opaque_fields"]
        if type(opaque) is not list or any(
                type(name) is not str or not name for name in opaque):
            raise TypeError(
                "construction opaque fields are a finite exact string list")
        if opaque != sorted(set(opaque)):
            raise ValueError("construction opaque fields are sorted and unique")
        boundary = value["boundary_fields"]
        if type(boundary) is not list or any(
                type(name) is not str or not name.isidentifier()
                for name in boundary):
            raise TypeError(
                "construction boundary fields are a finite identifier list")
        if boundary != sorted(set(boundary)):
            raise ValueError(
                "construction boundary fields are sorted and unique")

        exact_images = tuple(
            sorted((name, freeze(item)) for name, item in exact_values.items()))
        step_images = tuple(
            (name, step)
            for name, image in exact_images
            for step in (_step_image(image),)
            if step is not None)
        unsupported_names = {name for name, _why in unsupported}
        step_names = {name for name, _image in step_images}
        if step_names & unsupported_names:
            raise ValueError(
                "a carried construction value is also marked unsupported")
        non_step = set(exact_values) - step_names
        if not non_step <= unsupported_names:
            raise ValueError(
                "every exact value outside the SolverIR carrier names its refusal")
        carried_names = set(exact_values) | unsupported_names
        if carried_names & set(boundary):
            raise ValueError(
                "a boundary-owned construction field is also carried")
        got = cls(
            step_images, unsupported, shared, tuple(opaque), exact_images,
            tuple(boundary))
        if got.as_dict() != value:
            raise ValueError("a construction-facts image is not canonical")
        return got


def _canonical_carried(encoded, decoder=None):
    """Validate the child's JSON carrier through the native-value authority."""
    try:
        return ((decoder or WireDecoder(DEFAULT_WIRE_NODES)).image(encoded))
    except NativeValueError as exc:
        raise ConstructionUnavailable(str(exc)) from None


def _step_image(encoded):
    """Project one carried value into the exact SolverIR source carrier."""
    kind = encoded[0]
    # Boolean and integer have distinct SolverIR semantic types.  Keeping the
    # child's exact tag here is the earliest common boundary for reset values;
    # erasing it made every later stage see ``True`` as integer ``1``.
    if kind in {"bool", "int"}:
        return encoded
    if kind in {"list", "tuple"}:
        children = tuple(_step_image(child) for child in encoded[1])
        if any(child is None for child in children):
            return None
        return (kind, children)
    return None


def _thaw_step_image(image):
    kind, payload = image
    if kind in {"bool", "int"}:
        return payload
    values = [_thaw_step_image(child) for child in payload]
    return values if kind == "list" else tuple(values)


def _share_signature(report):
    """Alias equivalence and per-field opacity, independent of process ids."""
    if type(report) is not dict:
        raise ConstructionUnavailable("malformed construction sharing image")
    fields = tuple(sorted(report))
    if len(fields) > _SHARE_ROOT_LIMIT:
        raise ConstructionUnavailable(
            "the construction sharing image has too many roots")

    opaque, owners = [], {}
    identity_entries = 0
    for name in fields:
        row = report[name]
        if (type(name) is not str or type(row) is not dict
                or set(row) != {"ids", "opaque"}
                or type(row["ids"]) is not list
                or type(row["opaque"]) is not bool):
            raise ConstructionUnavailable(
                "malformed construction sharing row")
        if any(type(identity) is not int or identity <= 0
               for identity in row["ids"]):
            raise ConstructionUnavailable(
                "malformed construction object identity")
        identity_entries += len(row["ids"])
        if _SHARE_ID_LIMIT is not None and identity_entries > _SHARE_ID_LIMIT:
            raise ConstructionUnavailable(
                "the construction sharing image has too many identities")
        if len(row["ids"]) != len(set(row["ids"])):
            raise ConstructionUnavailable(
                "malformed duplicate construction object identity")
        if row["opaque"]:
            opaque.append(name)
        for identity in row["ids"]:
            owners.setdefault(identity, []).append(name)

    # Invert the relation once.  Comparing every root's id set with every
    # other root was quadratic even when no roots shared anything, and it ran
    # in the verifier after the bounded child had already answered.
    pairs = set()
    for names in owners.values():
        unique = sorted(set(names))
        for index, left in enumerate(unique):
            for right in unique[index + 1:]:
                pairs.add((left, right))
                if len(pairs) > _SHARE_PAIR_LIMIT:
                    raise ConstructionUnavailable(
                        "the construction sharing relation is too large")
    return fields, tuple(sorted(pairs)), tuple(opaque)


def _image(proxy):
    """One complete child boundary image, copied before the proxy can change."""
    raw_carried = proxy.__dict__.get("_carried")
    raw_opaque = proxy.__dict__.get("_opaque")
    if type(raw_carried) is not dict or type(raw_opaque) is not dict:
        raise ConstructionUnavailable(
            "malformed construction value domains")
    if (any(type(name) is not str for name in raw_carried)
            or any(type(name) is not str or type(kind) is not str
                   for name, kind in raw_opaque.items())):
        raise ConstructionUnavailable(
            "malformed construction field identity")
    overlap = set(raw_carried) & set(raw_opaque)
    if overlap:
        raise ConstructionUnavailable(
            "a construction field is both carried and opaque")
    decoder = WireDecoder(DEFAULT_WIRE_NODES)
    carried = {name: _canonical_carried(value, decoder)
               for name, value in raw_carried.items()}
    opaque = tuple(sorted(raw_opaque.items()))
    sharing = proxy._share_image()
    if type(sharing) is not dict or set(sharing) != {"fields", "modules"} \
            or type(sharing["fields"]) is not dict \
            or type(sharing["modules"]) is not dict:
        raise ConstructionUnavailable(
            "malformed construction sharing domains")
    field_roots = sharing["fields"]
    module_roots = sharing["modules"]
    if any(type(name) is not str or not name.isidentifier()
           for name in field_roots) \
            or any(type(name) is not str or not name.isidentifier()
                   for name in module_roots):
        raise ConstructionUnavailable(
            "malformed construction sharing root")
    value_domain = tuple(sorted(set(carried) | set(raw_opaque)))
    if set(field_roots) != set(value_domain):
        raise ConstructionUnavailable(
            "construction value and sharing field domains disagree")
    facts = proxy.__dict__.get("_facts") or {}
    if type(facts) is not dict:
        raise ConstructionUnavailable("malformed construction source facts")
    expected_modules = facts.get("rom_names", [])
    if (type(expected_modules) is not list
            or any(type(name) is not str or not name.isidentifier()
                   for name in expected_modules)
            or len(expected_modules) != len(set(expected_modules))
            or set(module_roots) != set(expected_modules)):
        raise ConstructionUnavailable(
            "construction module and sharing domains disagree")
    flattened = dict(field_roots)
    flattened.update({f"@module:{name}": row
                      for name, row in module_roots.items()})
    share_domain, shared, graph_opaque = _share_signature(flattened)
    # Module roots participate in the alias graph, but they are not model
    # fields and therefore never belong to the step-value contract returned
    # to the lowerer.  Keep the two domains separate instead of leaking a
    # verifier-internal ``@module:...`` name through the public state API.
    return (tuple(sorted(carried.items())), opaque, value_domain,
            share_domain, shared, graph_opaque)


def _facts_from_image(image):
    (carried_items, opaque_items, value_domain, _share_domain,
     shared, graph_opaque) = image
    carried = dict(carried_items)
    opaque = dict(opaque_items)
    values, unsupported = [], []
    for name in value_domain:
        step = _step_image(carried[name]) if name in carried else None
        if step is not None:
            values.append((name, step))
        elif name in opaque:
            unsupported.append(
                (name, f"an opaque {opaque[name]} construction value"))
        elif name in carried:
            unsupported.append(
                (name, f"a {carried[name][0]} value SolverIR does not carry"))
        else:
            unsupported.append(
                (name, "a field missing from the exact construction image"))
    exact = tuple((name, carried[name]) for name in value_domain
                  if name in carried)
    return ConstructionFacts(
        tuple(values), tuple(unsupported), shared, graph_opaque, exact, ())


def _facts_weight(facts, key):
    """Bytes retained by one cache entry, counted without double charging.

    An object-count proxy does not bound memory: a Python integer may carry
    thousands of digits and one non-ASCII source character may occupy four
    bytes.  The cache retains the complete semantic key--source, file, class,
    protocol version and dependency identity--as well as the immutable fact
    trees, so charge their actual host representations.  Shared tuple/string
    objects are counted once, matching what the cache retains.
    """
    if type(key) is not tuple:
        raise TypeError("a construction cache key must be a tuple")
    work = [key, facts]
    seen = set()
    weight = 0
    while work:
        value = work.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        weight += sys.getsizeof(value)
        if type(value) is tuple:
            work.extend(value)
        elif isinstance(value, ConstructionFacts):
            work.extend((value._value_images, value._unsupported_items,
                         value.shared_pairs, value.opaque_fields,
                         value._exact_images, value.boundary_fields))
    return weight


def _build(source, file, cls_name, dependency_payload=None):
    from .isolated import isolated_model

    # The digest in the cache key and the bytes imported by both children
    # must name one artifact.  Reading the central path once for the key and
    # again in each child leaves a TOCTOU window (and an ABA can evade a
    # post-build recheck).  Materialise the one captured payload and use the
    # isolated protocol's recorded-dependency path for both executions.
    with tempfile.TemporaryDirectory(prefix="behave-construction-", dir=scratch_root()) as tmp:
        recorded = {}
        if dependency_payload is not None:
            dependency = Path(tmp) / "fp_lib.py"
            dependency.write_bytes(dependency_payload)
            recorded["fp_lib"] = dependency
        factory = isolated_model(
            source, file=file, cls=cls_name, recorded=recorded)
        try:
            images = []
            for _attempt in range(2):
                proxy = factory()
                proxy.reset()
                images.append(_image(proxy))
            if images[0] != images[1]:
                raise ConstructionUnavailable(
                    "construction values or object sharing differ between runs")
            return _facts_from_image(images[0])
        finally:
            factory.close()


def _dependency_snapshot():
    """Identity and exact bytes of the approved construction dependency."""
    try:
        from . import LIBRARY_DIR

        payload = (LIBRARY_DIR / "fp_lib.py").read_bytes()
    except OSError as exc:
        raise ConstructionUnavailable(
            f"the approved fp_lib dependency cannot be snapshotted: {exc}") \
            from None
    digest = hashlib.sha256(payload).hexdigest()
    return digest, payload


def _dependency_identity():
    """Compatibility view of the dependency snapshot's identity."""
    return _dependency_snapshot()[0]


def clear_construction_cache():
    """Testing seam; production facts are otherwise immutable once cached."""
    global _CACHE_WEIGHT
    with _CACHE_LOCK:
        _CACHE.clear()
        _CACHE_WEIGHT = 0


def _seed_construction_facts(source, file, cls_name, facts,
                             dependency_identity):
    """Seed one fresh frontend job with facts obtained by the parent service.

    This is deliberately private.  The public construction service still obtains the
    record by executing the exact frozen source through :func:`construction_facts`.
    A subsequent isolated lowering job receives that record over strict JSON and seeds
    only its process-local cache, after checking the trusted construction dependency.
    Consequently ``lower_procedure`` consumes the frozen observation without executing
    the submitted source in the lowering/solver process or silently taking a second
    construction image.
    """

    if type(source) is not str or type(file) is not str or not file \
            or type(cls_name) is not str or not cls_name.isidentifier():
        raise TypeError(
            "seeding construction needs source text, a file and one class name")
    if type(facts) is not ConstructionFacts:
        raise TypeError("only exact ConstructionFacts may seed construction")
    if type(dependency_identity) is not str or not dependency_identity:
        raise TypeError("a construction seed needs its dependency identity")
    current = _dependency_identity()
    if dependency_identity != current:
        raise ConstructionUnavailable(
            "the construction facts and lowering job use different trusted "
            "dependency bytes")
    key = (source, file, cls_name, CONSTRUCTION_VERSION, current)
    weight = _facts_weight(facts, key)
    if weight > _CACHE_WEIGHT_LIMIT:
        raise ConstructionUnavailable(
            "the construction record is too large to freeze for isolated lowering")
    global _CACHE_WEIGHT
    with _CACHE_LOCK:
        present = _CACHE.get(key)
        if present is not None and present[0] != facts:
            raise ConstructionUnavailable(
                "two construction records name the same frozen semantic key")
        if present is None and weight <= _CACHE_WEIGHT_LIMIT:
            _CACHE[key] = (facts, weight)
            _CACHE_WEIGHT += weight
        if key in _CACHE:
            _CACHE.move_to_end(key)
        while _CACHE_WEIGHT > _CACHE_WEIGHT_LIMIT:
            old_key, (_old_facts, old_weight) = _CACHE.popitem(last=False)
            _CACHE_WEIGHT -= old_weight
            if old_key == key:
                raise ConstructionUnavailable(
                    "the construction record exceeds the frontend cache boundary")


def construction_facts(source, file="behavior.py", cls_name=None):
    """Return the authenticated construction record for this source.

    Only successful records are cached.  A timeout or broken child is an
    invocation event, not a permanent semantic fact about the source.
    """
    if cls_name is None:
        from .extract import source_model

        cls_name = source_model(source, file).cls.name
    dependency_identity, dependency_payload = _dependency_snapshot()
    key = (source, file, cls_name, CONSTRUCTION_VERSION,
           dependency_identity)
    global _CACHE_WEIGHT
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached[0]
        pending = _INFLIGHT.get(key)
        if pending is None:
            pending = Future()
            _INFLIGHT[key] = pending
            owns_build = True
        else:
            owns_build = False

    # Serialize duplicate requests, not unrelated models. Holding the cache
    # lock while a child constructs made a parallel population run entirely
    # sequential even though its keys were all different.
    if not owns_build:
        return pending.result()

    try:
        # Distinct semantic keys may build concurrently, but not without an
        # aggregate bound: each isolated child has a per-process memory cap,
        # and an unbounded number of individually compliant children can
        # still exhaust the verifier's cgroup.  Same-key waiters remain on
        # the Future above and consume no slot.
        with _BUILD_SLOTS:
            got = _build(source, file, cls_name, dependency_payload)
        # Weighting is part of producing a cacheable result, not an operation
        # after single-flight has finished.  If it fails, resolve and remove
        # the Future by the same failure path as construction itself.
        weight = _facts_weight(got, key)
    except ConstructionUnavailable as exc:
        with _CACHE_LOCK:
            _INFLIGHT.pop(key, None)
            pending.set_exception(exc)
        raise
    except Exception as exc:                                  # noqa: BLE001
        unavailable = ConstructionUnavailable(
            f"{type(exc).__name__}: {exc}")
        with _CACHE_LOCK:
            _INFLIGHT.pop(key, None)
            pending.set_exception(unavailable)
        raise unavailable from None
    except BaseException as exc:
        # A cancellation must not leave an unresolved Future poisoning this
        # semantic key forever.  Waiters observe the same cancellation; a
        # later invocation may retry because failures are not cached.
        with _CACHE_LOCK:
            _INFLIGHT.pop(key, None)
            pending.set_exception(exc)
        raise

    with _CACHE_LOCK:
        if weight <= _CACHE_WEIGHT_LIMIT:
            _CACHE[key] = (got, weight)
            _CACHE_WEIGHT += weight
            _CACHE.move_to_end(key)
        while _CACHE_WEIGHT > _CACHE_WEIGHT_LIMIT:
            _old_key, (_old_facts, old_weight) = _CACHE.popitem(last=False)
            _CACHE_WEIGHT -= old_weight
        _INFLIGHT.pop(key, None)
        pending.set_result(got)
    return got


__all__ = [
    "CONSTRUCTION_VERSION",
    "ConstructionFacts",
    "ConstructionUnavailable",
    "clear_construction_cache",
    "construction_facts",
]
