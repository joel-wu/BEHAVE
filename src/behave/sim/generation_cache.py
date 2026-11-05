"""Durable semantic context cache for local stimulus generation.

This module owns storage, not generation policy.  It accepts only complete semantic
bindings, exact seed/request bytes, and detached bytes or finite JSON values.  It does
not import a provider, solver, replay service, evidence type, or execution telemetry.

The cache is deliberately a *context* cache.  A caller may reuse an immutable lowering,
layout, relation, or formula image, but a hit is not a Probe answer and carries no
candidate or evidence.  Later integration must still charge the logical request and run
the ordinary solve, model decode, replay, and audit path.
"""

from __future__ import annotations

import base64
import enum
import fcntl
import hashlib
import json
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from behave.core import runtime_defaults as defaults

from .core.frozen import Frozen
from .core.record_value import canonical_json

__all__ = [
    "GenerationCacheBinding",
    "GenerationCacheEvent",
    "GenerationCacheKey",
    "GenerationCacheKind",
    "GenerationCacheLookup",
    "GenerationCacheTelemetry",
    "GenerationContextCache",
    "GenerationPayloadKind",
]


BINDING_VERSION = "generation-cache-binding/1"
KEY_VERSION = "generation-cache-key/1"
ENTRY_VERSION = "generation-cache-entry/1"
STORE_VERSION = "generation-context-store/1"
_LOCK_STRIPES = 4096


def _identity(prefix, value):
    image = canonical_json(value).encode("utf-8")
    return prefix + hashlib.sha256(image).hexdigest()


def _nonempty_strings(values, what):
    if any(type(value) is not str or not value for value in values.values()):
        raise ValueError(f"{what} needs every nonempty exact string component")


class GenerationCacheBinding(Frozen):
    """Complete semantic and implementation identity for generation contexts.

    Source and target are separate because Behavior guidance and the evaluated artifact
    need not be the same program.  ``environment`` binds task-owned schedule and memory
    semantics that shape a relation.  Drain and stall policy are already part of the
    supplied ``generation_spec`` identity.

    Timeout, worker count, probe budget, stage timings, and resource measurements are
    intentionally not constructor fields.  They may change execution cost, not the
    context named here.
    """

    __slots__ = (
        "backend", "backend_version", "catalogue", "encoding", "environment",
        "frontend", "frontend_version", "generation_spec", "goal_policy",
        "identity", "provider", "provider_policy", "representation",
        "semantic_instance", "source", "target", "task_contract", "tool",
        "tool_version",
    )

    _COMPONENTS = (
        "source", "target", "task_contract", "generation_spec", "catalogue",
        "goal_policy", "representation", "encoding", "environment", "provider",
        "provider_policy", "frontend", "frontend_version", "backend",
        "backend_version", "tool", "tool_version",
    )
    _SEMANTIC_COMPONENTS = (
        "source", "target", "task_contract", "generation_spec", "catalogue",
        "goal_policy", "representation", "encoding", "environment", "provider",
        "provider_policy",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("GenerationCacheBinding is a closed semantic identity")

    def __init__(self, *, source, target, task_contract, generation_spec, catalogue,
                 goal_policy, representation, encoding, environment, provider,
                 provider_policy, frontend, frontend_version, backend,
                 backend_version, tool, tool_version):
        supplied = locals()
        values = {name: supplied[name] for name in self._COMPONENTS}
        _nonempty_strings(values, "a generation cache binding")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        semantic = {name: values[name] for name in self._SEMANTIC_COMPONENTS}
        self.semantic_instance = _identity("generation-semantic-instance/1:", semantic)
        self.identity = _identity(
            "generation-context-binding/1:",
            {"semantic_instance": self.semantic_instance,
             **{name: values[name] for name in self._COMPONENTS
                if name not in self._SEMANTIC_COMPONENTS}})
        self._freeze()

    def as_dict(self):
        return {
            "version": BINDING_VERSION,
            **{name: getattr(self, name) for name in self._COMPONENTS},
            "semantic_instance": self.semantic_instance,
            "identity": self.identity,
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"version", "semantic_instance", "identity", *cls._COMPONENTS}
        if type(value) is not dict or set(value) != fields \
                or value.get("version") != BINDING_VERSION:
            raise TypeError(
                "a generation cache binding has its complete versioned image")
        got = cls(**{name: value[name] for name in cls._COMPONENTS})
        if got.as_dict() != value:
            raise ValueError("a generation cache binding has inconsistent identities")
        return got


class GenerationCacheKind(enum.Enum):
    """The three independently reusable generation context boundaries."""

    LAYOUT = "layout"
    SELECT = "select"
    PROBE = "probe"


class GenerationPayloadKind(enum.Enum):
    """Closed detached carriers accepted by the durable store."""

    BYTES = "bytes"
    JSON = "json"


class GenerationCacheEvent(Frozen):
    """One backend-neutral context-cache disposition, never a generation answer."""

    __slots__ = (
        "detail", "disposition", "key_identity", "kind", "record_identity",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("GenerationCacheEvent is a closed telemetry record")

    def __init__(self, kind, disposition, key_identity, record_identity=None,
                 detail=""):
        if type(kind) is not GenerationCacheKind:
            raise TypeError("generation cache telemetry needs an exact cache kind")
        if type(disposition) is not str \
                or disposition not in {"hit", "miss", "bypass", "error"}:
            raise ValueError("generation cache telemetry has a known disposition")
        if type(key_identity) is not str or not key_identity:
            raise ValueError("generation cache telemetry names its exact key")
        if record_identity is not None \
                and (type(record_identity) is not str or not record_identity):
            raise TypeError("a cache record identity is nonempty text or absent")
        if type(detail) is not str:
            raise TypeError("generation cache telemetry detail is exact text")
        self.kind = kind
        self.disposition = disposition
        self.key_identity = key_identity
        self.record_identity = record_identity
        self.detail = detail
        self._freeze()

    def as_dict(self):
        return {
            "kind": self.kind.value,
            "disposition": self.disposition,
            "key_identity": self.key_identity,
            "record_identity": self.record_identity,
            "detail": self.detail,
        }


class GenerationCacheTelemetry(Frozen):
    """Immutable backend-neutral snapshot of generation context-cache events."""

    __slots__ = ("events",)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("GenerationCacheTelemetry is a closed telemetry record")

    def __init__(self, events=()):
        events = tuple(events)
        if any(type(event) is not GenerationCacheEvent for event in events):
            raise TypeError("generation cache telemetry contains exact event records")
        self.events = events
        self._freeze()

    def as_dict(self):
        counts = {name: 0 for name in ("hit", "miss", "bypass", "error")}
        for event in self.events:
            counts[event.disposition] += 1
        return {
            "version": "generation-cache-telemetry/1",
            "counts": counts,
            "events": [event.as_dict() for event in self.events],
        }


class GenerationCacheKey(Frozen):
    """One exact semantic generation-context request.

    Layout is seed-independent.  Select binds the complete seed and the independently
    replayed trace.  Probe adds the exact request bytes; these bytes include the target,
    occurrence, catalogue and mutable-field set and are retained in the key image rather
    than represented by a collision-assumed digest alone.
    """

    __slots__ = (
        "binding", "identity", "kind", "request_bytes", "seed_bytes",
        "trace_identity",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("GenerationCacheKey is a closed semantic identity")

    def __init__(self, binding, kind, *, seed_bytes=None, trace_identity=None,
                 request_bytes=None):
        if type(binding) is not GenerationCacheBinding:
            raise TypeError("a generation cache key needs the exact binding type")
        if type(kind) is not GenerationCacheKind:
            raise TypeError("a generation cache key needs the exact key kind")
        if kind is GenerationCacheKind.LAYOUT:
            if any(value is not None
                   for value in (seed_bytes, trace_identity, request_bytes)):
                raise ValueError("a layout cache key carries no seed-local fields")
        else:
            if type(seed_bytes) is not bytes or not seed_bytes:
                raise TypeError("a seed-local cache key needs nonempty exact seed bytes")
            if type(trace_identity) is not str or not trace_identity:
                raise TypeError(
                    "a seed-local cache key needs its fresh audited-trace identity")
            if kind is GenerationCacheKind.SELECT and request_bytes is not None:
                raise ValueError("a Select cache key carries no Probe request")
            if kind is GenerationCacheKind.PROBE \
                    and (type(request_bytes) is not bytes or not request_bytes):
                raise TypeError(
                    "a Probe cache key needs nonempty exact request bytes")
        self.binding = binding
        self.kind = kind
        self.seed_bytes = seed_bytes
        self.trace_identity = trace_identity
        self.request_bytes = request_bytes
        self.identity = _identity(
            "generation-context-key/1:", self._identity_image())
        self._freeze()

    @classmethod
    def layout(cls, binding):
        return cls(binding, GenerationCacheKind.LAYOUT)

    @classmethod
    def select(cls, binding, *, seed_bytes, trace_identity):
        return cls(
            binding, GenerationCacheKind.SELECT, seed_bytes=seed_bytes,
            trace_identity=trace_identity)

    @classmethod
    def probe(cls, binding, *, seed_bytes, trace_identity, request_bytes):
        return cls(
            binding, GenerationCacheKind.PROBE, seed_bytes=seed_bytes,
            trace_identity=trace_identity, request_bytes=request_bytes)

    @staticmethod
    def _encoded(value):
        return None if value is None else base64.b64encode(value).decode("ascii")

    def _identity_image(self):
        return {
            "binding": self.binding.identity,
            "kind": self.kind.value,
            "seed_base64": self._encoded(self.seed_bytes),
            "trace_identity": self.trace_identity,
            "request_base64": self._encoded(self.request_bytes),
        }

    def as_dict(self):
        return {
            "version": KEY_VERSION,
            "binding": self.binding.as_dict(),
            "kind": self.kind.value,
            "seed_base64": self._encoded(self.seed_bytes),
            "trace_identity": self.trace_identity,
            "request_base64": self._encoded(self.request_bytes),
            "identity": self.identity,
        }

    @staticmethod
    def _decoded(value, name):
        if value is None:
            return None
        if type(value) is not str:
            raise TypeError(f"a generation cache key's {name} is base64 text or null")
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            raise ValueError(
                f"a generation cache key's {name} is not canonical base64") from None
        if base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError(
                f"a generation cache key's {name} is not canonical base64")
        return decoded

    @classmethod
    def from_dict(cls, value):
        fields = {
            "version", "binding", "kind", "seed_base64", "trace_identity",
            "request_base64", "identity",
        }
        if type(value) is not dict or set(value) != fields \
                or value.get("version") != KEY_VERSION:
            raise TypeError("a generation cache key has its complete versioned image")
        try:
            kind = GenerationCacheKind(value["kind"])
        except (TypeError, ValueError):
            raise TypeError("a generation cache key has a known exact kind") from None
        got = cls(
            GenerationCacheBinding.from_dict(value["binding"]), kind,
            seed_bytes=cls._decoded(value["seed_base64"], "seed"),
            trace_identity=value["trace_identity"],
            request_bytes=cls._decoded(value["request_base64"], "request"))
        if got.as_dict() != value:
            raise ValueError("a generation cache key has inconsistent identities")
        return got


class GenerationCacheLookup(Frozen):
    """One detached cache lookup result, including hit/miss for later telemetry."""

    __slots__ = (
        "_payload", "cache_hit", "key_identity", "payload_kind", "payload_schema",
        "record_identity",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("GenerationCacheLookup is a closed cache-boundary record")

    def __init__(self, key_identity, record_identity, payload_schema, payload_kind,
                 payload, *, cache_hit):
        if type(key_identity) is not str or not key_identity \
                or type(record_identity) is not str or not record_identity \
                or type(payload_schema) is not str or not payload_schema:
            raise ValueError("a cache lookup needs complete nonempty identities")
        if type(payload_kind) is not GenerationPayloadKind:
            raise TypeError("a cache lookup needs an exact payload kind")
        if type(payload) is not bytes:
            raise TypeError("a cache lookup retains only detached immutable bytes")
        if type(cache_hit) is not bool:
            raise TypeError("a cache lookup's hit flag is an exact Boolean")
        self.key_identity = key_identity
        self.record_identity = record_identity
        self.payload_schema = payload_schema
        self.payload_kind = payload_kind
        self._payload = payload
        self.cache_hit = cache_hit
        self._freeze()

    @property
    def payload_bytes(self):
        if self.payload_kind is not GenerationPayloadKind.BYTES:
            raise TypeError("a JSON context is decoded through payload_json")
        return self._payload

    @property
    def payload_json(self):
        if self.payload_kind is not GenerationPayloadKind.JSON:
            raise TypeError("a byte context is read through payload_bytes")
        # Decode afresh on every access.  A caller can mutate this view without
        # changing the lookup record or a subsequent consumer's view.
        return json.loads(self._payload)


class GenerationContextCache(Frozen):
    """A bounded durable content-addressed store rooted at an explicit path.

    Same-key builders are serialized by a process-shared ``flock`` and recheck the
    entry after acquiring it.  Lock files use a fixed stripe set, so arbitrarily many
    misses cannot create an unbounded second store.  A rare stripe collision may delay
    an unrelated builder but cannot change its key or result.
    """

    __slots__ = ("_entries", "_locks", "max_bytes", "max_entries", "root")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("GenerationContextCache is a closed durable store")

    def __init__(self, root, *, max_entries=defaults.SIM_GENERATION_CACHE_ENTRIES,
                 max_bytes=defaults.SIM_GENERATION_CACHE_BYTES):
        if root is None:
            raise TypeError("a generation context cache needs an explicit root path")
        if type(max_entries) is not int or isinstance(max_entries, bool) \
                or max_entries < 1:
            raise ValueError("a generation cache entry bound is positive")
        if type(max_bytes) is not int or isinstance(max_bytes, bool) or max_bytes < 1:
            raise ValueError("a generation cache byte bound is positive")
        self.root = Path(root)
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._entries = self.root / "entries"
        self._locks = self.root / "locks"
        self._mkdir(self.root)
        self._mkdir(self._entries)
        self._mkdir(self._locks)
        self._freeze()
        self._evict(None)

    @staticmethod
    def _mkdir(path):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise OSError(f"generation cache path is not a directory: {path}")

    @staticmethod
    def _validate_request(key, payload_schema, payload_kind):
        if type(key) is not GenerationCacheKey:
            raise TypeError("a generation context lookup needs an exact cache key")
        if type(payload_schema) is not str or not payload_schema:
            raise TypeError("a generation context payload needs a nonempty schema")
        if type(payload_kind) is not GenerationPayloadKind:
            raise TypeError("a generation context payload needs an exact carrier kind")

    @staticmethod
    def _record_identity(key, payload_schema, payload_kind):
        return _identity(
            "generation-context-record/1:", {
                "key": key.identity,
                "payload_schema": payload_schema,
                "payload_kind": payload_kind.value,
            })

    def _record_path(self, record_identity):
        digest = record_identity.rsplit(":", 1)[-1]
        parent = self._entries / digest[:2]
        self._mkdir(parent)
        return parent / f"{digest}.json"

    def _lock_path(self, key):
        digest = key.identity.rsplit(":", 1)[-1]
        stripe = int(digest[:8], 16) % _LOCK_STRIPES
        return self._locks / f"{stripe:03x}.lock"

    @contextmanager
    def _locked(self, path):
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise OSError(f"generation cache lock is not a regular file: {path}")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @staticmethod
    def _payload_bytes(value, kind):
        if kind is GenerationPayloadKind.BYTES:
            if type(value) is not bytes:
                raise TypeError("a byte generation context is exact immutable bytes")
            return value
        return canonical_json(value).encode("utf-8")

    @staticmethod
    def _entry_bytes(key, record_identity, payload_schema, payload_kind, payload):
        return canonical_json({
            "version": ENTRY_VERSION,
            "store_version": STORE_VERSION,
            "record_identity": record_identity,
            "key": key.as_dict(),
            "payload_schema": payload_schema,
            "payload_kind": payload_kind.value,
            "payload_size": len(payload),
            "payload_digest": hashlib.sha256(payload).hexdigest(),
            "payload_base64": base64.b64encode(payload).decode("ascii"),
        }).encode("utf-8")

    @staticmethod
    def _decode_entry(raw, key, record_identity, payload_schema, payload_kind):
        try:
            value = json.loads(raw)
            if type(value) is not dict or set(value) != {
                    "version", "store_version", "record_identity", "key",
                    "payload_schema", "payload_kind", "payload_size",
                    "payload_digest", "payload_base64"}:
                return None
            if canonical_json(value).encode("utf-8") != raw \
                    or value["version"] != ENTRY_VERSION \
                    or value["store_version"] != STORE_VERSION \
                    or value["record_identity"] != record_identity \
                    or value["payload_schema"] != payload_schema \
                    or value["payload_kind"] != payload_kind.value:
                return None
            decoded_key = GenerationCacheKey.from_dict(value["key"])
            if decoded_key.as_dict() != key.as_dict():
                return None
            payload_text = value["payload_base64"]
            if type(payload_text) is not str:
                return None
            payload = base64.b64decode(payload_text, validate=True)
            if base64.b64encode(payload).decode("ascii") != payload_text:
                return None
            if type(value["payload_size"]) is not int \
                    or value["payload_size"] != len(payload) \
                    or type(value["payload_digest"]) is not str \
                    or value["payload_digest"] != hashlib.sha256(payload).hexdigest():
                return None
            if payload_kind is GenerationPayloadKind.JSON:
                decoded = json.loads(payload)
                if canonical_json(decoded).encode("utf-8") != payload:
                    return None
            return payload
        except (OSError, TypeError, ValueError, UnicodeError):
            return None

    def _read(self, path):
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            return None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > self.max_bytes:
                return None
            with os.fdopen(fd, "rb", closefd=True) as stream:
                fd = -1
                raw = stream.read(self.max_bytes + 1)
                return raw if len(raw) <= self.max_bytes else None
        except OSError:
            return None
        finally:
            if fd >= 0:
                os.close(fd)

    def _load(self, path, key, record_identity, payload_schema, payload_kind):
        raw = self._read(path)
        if raw is None:
            return None
        return self._decode_entry(
            raw, key, record_identity, payload_schema, payload_kind)

    @staticmethod
    def _fsync_directory(path):
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _publish(self, path, raw):
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.partial")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        fd = os.open(temporary, flags, 0o600)
        try:
            stream = os.fdopen(fd, "wb", closefd=True)
            fd = -1
            with stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            self._fsync_directory(path.parent)
        except BaseException:
            if fd >= 0:
                os.close(fd)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _touch(self, path):
        try:
            os.utime(path, None, follow_symlinks=False)
        except OSError:
            pass

    def _evict(self, protected):
        lock = self._locks / "eviction.lock"
        with self._locked(lock):
            rows = []
            directories = {}
            try:
                for parent in self._entries.iterdir():
                    try:
                        # Prefixes are a closed lowercase-hex namespace.  Check the
                        # directory object itself, then hold an O_NOFOLLOW descriptor
                        # for both enumeration and deletion so a replacement symlink
                        # cannot redirect eviction outside this store.
                        if len(parent.name) != 2 \
                                or any(char not in "0123456789abcdef"
                                       for char in parent.name):
                            continue
                        info = parent.lstat()
                        if not stat.S_ISDIR(info.st_mode):
                            continue
                        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                        flags |= getattr(os, "O_CLOEXEC", 0)
                        flags |= getattr(os, "O_NOFOLLOW", 0)
                        directory = os.open(parent, flags)
                        directories[parent.name] = directory
                        with os.scandir(directory) as entries:
                            for entry in entries:
                                child = entry.stat(follow_symlinks=False)
                                if stat.S_ISREG(child.st_mode) \
                                        and len(entry.name) == 69 \
                                        and entry.name.endswith(".json") \
                                        and all(char in "0123456789abcdef"
                                                for char in entry.name[:-5]):
                                    rows.append((
                                        child.st_mtime_ns, entry.name,
                                        parent.name, child.st_size))
                    except OSError:
                        continue
                rows.sort()
                count = len(rows)
                weight = sum(row[3] for row in rows)
                changed = set()
                protected_row = (None if protected is None else
                                 (protected.parent.name, protected.name))
                for _mtime, name, prefix, size in rows:
                    if count <= self.max_entries and weight <= self.max_bytes:
                        break
                    if (prefix, name) == protected_row:
                        continue
                    try:
                        os.unlink(name, dir_fd=directories[prefix])
                    except OSError:
                        continue
                    count -= 1
                    weight -= size
                    changed.add(prefix)
                for prefix in changed:
                    try:
                        os.fsync(directories[prefix])
                    except OSError:
                        pass
            finally:
                for directory in directories.values():
                    os.close(directory)

    def load(self, key, *, payload_schema, payload_kind):
        """Load one valid detached context, or return ``None`` on absence/corruption."""

        self._validate_request(key, payload_schema, payload_kind)
        record_identity = self._record_identity(key, payload_schema, payload_kind)
        path = self._record_path(record_identity)
        with self._locked(self._lock_path(key)):
            payload = self._load(
                path, key, record_identity, payload_schema, payload_kind)
            if payload is None:
                return None
            self._touch(path)
        return GenerationCacheLookup(
            key.identity, record_identity, payload_schema, payload_kind, payload,
            cache_hit=True)

    def get_or_build(self, key, *, payload_schema, payload_kind, builder):
        """Return a valid context, single-flighting one successful cold builder.

        The builder runs while the semantic key's interprocess lock is held.  Its
        exception or invalid result propagates and no entry is published.  A valid but
        oversized result is returned to this caller and deliberately not retained.
        """

        self._validate_request(key, payload_schema, payload_kind)
        if not callable(builder):
            raise TypeError("a generation context builder is callable")
        record_identity = self._record_identity(key, payload_schema, payload_kind)
        path = self._record_path(record_identity)
        with self._locked(self._lock_path(key)):
            payload = self._load(
                path, key, record_identity, payload_schema, payload_kind)
            if payload is not None:
                self._touch(path)
                return GenerationCacheLookup(
                    key.identity, record_identity, payload_schema, payload_kind,
                    payload, cache_hit=True)

            payload = self._payload_bytes(builder(), payload_kind)
            raw = self._entry_bytes(
                key, record_identity, payload_schema, payload_kind, payload)
            if len(raw) <= self.max_bytes:
                # Recheck immediately before replacement.  Cooperative writers cannot
                # race this lock; the check also prevents an external valid publisher
                # from being silently clobbered.
                present = self._load(
                    path, key, record_identity, payload_schema, payload_kind)
                if present is not None and present != payload:
                    raise RuntimeError(
                        "two generation contexts name the same semantic cache key")
                if present is None:
                    self._publish(path, raw)
                else:
                    payload = present
                    self._touch(path)
        if len(raw) <= self.max_bytes:
            self._evict(path)
        return GenerationCacheLookup(
            key.identity, record_identity, payload_schema, payload_kind, payload,
            cache_hit=False)
