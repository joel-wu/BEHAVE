"""A fixed, host-owned runtime shared by the workers of one batch.

Installations are stable for the batch. Preparation records their content once;
launches do not poll installations for updates. Start a new environment after an
upgrade. The private directory is passed to host workers, never to candidates.
It contains runtime declarations and their immutable tool images, not builds,
stimuli, or model state.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from behave.core.paths import scratch_root

from .preparation_control import acquire_lock, check_preparation

_ACTIVE = ContextVar("behave_runtime_environment", default=None)
_ENVIRON = "BEHAVE_RUNTIME_DIR"
_ENVIRONMENTS = {}
_LOCK = threading.Lock()
# Old declarations could point into a short-lived worker scratch directory.
# Reprepare them once into the batch-owned image directory.
_VERSION = "runtime-environment/2"


class RuntimeEnvironment:
    """Prepared resources, shared in memory and through a node-local directory.

    ``directory`` must be owned by the trusted host coordinator. Its lifetime is
    the batch, not an individual command; it must outlive all attached workers.
    """

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).absolute()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._values = {}
        self._lock = threading.RLock()

    @contextmanager
    def activate(self):
        token = _ACTIVE.set(self)
        try:
            yield self
        finally:
            _ACTIVE.reset(token)

    def prepare(self, key, build, *, encode, decode):
        """Publish once per resource; concurrent workers wait for that publication.

        A failed/cancelled builder publishes nothing. The local manifest is
        trusted coordinator state, not a candidate-supplied cache or certificate.
        """
        name = hashlib.sha256(json.dumps([_VERSION, key], sort_keys=True).encode()).hexdigest()
        with self._lock:
            if name in self._values:
                return self._values[name]
            record = self.directory / (name + ".json")
            with (self.directory / (name + ".lock")).open("a+b") as lock:
                acquire_lock(lock.fileno(), fcntl.LOCK_EX)
                if record.exists():
                    value = decode(json.loads(record.read_text()))
                else:
                    with self.activate():
                        value = build()
                    data = json.dumps(encode(value), sort_keys=True).encode()
                    check_preparation()
                    fd, temporary = tempfile.mkstemp(prefix=".preparing-", dir=self.directory)
                    try:
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(data)
                        os.replace(temporary, record)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                self._values[name] = value
                return value


def active_runtime_environment() -> RuntimeEnvironment | None:
    return _ACTIVE.get()


def runtime_environment(directory: str | Path | None = None) -> RuntimeEnvironment:
    """Get the batch runtime, lazily creating it for standalone callers.

    A coordinator calls this before spawning workers; ``BEHAVE_RUNTIME_DIR`` is
    then inherited by those host processes. Independent launchers can pass the
    same node-local directory explicitly. Use a new directory for a new version.
    """
    active = _ACTIVE.get()
    if directory is None and active is not None:
        return active
    with _LOCK:
        if directory is None:
            directory = os.environ.get(_ENVIRON)
            if not directory:
                directory = tempfile.mkdtemp(prefix="behave-runtime-", dir=scratch_root())
                os.environ[_ENVIRON] = directory
        path = str(Path(directory).absolute())
        if path not in _ENVIRONMENTS:
            _ENVIRONMENTS[path] = RuntimeEnvironment(path)
        return _ENVIRONMENTS[path]
