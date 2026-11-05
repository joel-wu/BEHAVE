"""CPU jobs share FIFO admission; model waiting holds no CPU reservation.

Use the existing admission and local RPC transports. Each trusted solver has
its own ingress, so its supervisor can release a lost reply's reservation only
after confirming that solver and its descendants have exited. Sim pools remain
private to individual invocations.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, parse_json
from behave.core.resources import allocated_cpus

from .admission import AdmissionPolicy, ResourcePool
from .attempt import AttemptInterrupted
from .local_rpc import LocalRPCServer, exchange

_CURRENT = ContextVar("behave_cpu_job", default=None)


@dataclass
class CpuLease:
    cpus: tuple[int, ...]
    cleanup: CleanupState = CleanupState.UNKNOWN


class CpuResources:
    def __init__(self, workers: int, *, cpus: int | None = None):
        if type(workers) is not int or workers < 1:
            raise ValueError("CPU job concurrency must be positive")
        self.cpus = allocated_cpus(cpus)
        slots = min(workers, len(self.cpus))
        # Disjoint sets, including the remainder. One invocation may use its
        # whole set; simultaneous invocations never claim the same CPUs.
        self._available = [self.cpus[i::slots] for i in range(slots)]
        self._pool = ResourcePool(AdmissionPolicy("cpu-jobs", slots))
        self._lock = Lock()
        self._held = {}
        self._retired = set()

    def acquire(self, cancelled):
        permit = self._pool.acquire(cancelled=cancelled)
        permit.__enter__()
        with self._lock:
            lease = CpuLease(self._available.pop(0))
            self._held[id(lease)] = (lease, permit)
        return lease

    def release(self, lease, cleanup):
        with self._lock:
            if not cleanup.quiescent:
                # Do not reuse CPUs whose previous descendants may still live.
                self._retired.add(id(lease))
                exhausted = len(self._retired) == self._pool.policy.concurrency
                permit = None
            else:
                _, permit = self._held.pop(id(lease))
                self._available.append(lease.cpus)
                exhausted = False
        if exhausted:
            self._pool.quarantine(cleanup)
        if permit is not None:
            permit.__exit__(None, None, None)

    def snapshot(self):
        active, queued = self._pool.counts
        with self._lock:
            return {"cpus": list(self.cpus), "capacity": len(self.cpus),
                    "slots": self._pool.policy.concurrency, "active": active - len(self._retired),
                    "queued": queued, "retired": len(self._retired),
                    "available_cpus": sum(map(len, self._available))}

    def write_snapshot(self, path):
        """Replace mutable diagnostics, even after a read-only archive restore.

        Never chmod or truncate the old inode: it may still be hardlinked into
        an immutable archive. The caller owns the run's coordination lock.
        """
        path = Path(path)
        temporary = None
        try:
            with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                    suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(canonical_json(self.snapshot()))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def relay(self, *, cancelled=lambda: False):
        return CpuRelay(self, cancelled=cancelled)


class CpuRelay(LocalRPCServer):
    def __init__(self, resources, *, cancelled):
        super().__init__(name="cpu-relay", deadline=None, cancelled=cancelled)
        self.resources = resources
        self.lease = None
        self.worker_cleanup = CleanupState.UNKNOWN

    def respond(self, data, *, cancelled):
        command = parse_json(data)
        if command == {"op": "acquire"}:
            if self.lease is not None:
                return canonical_json({"error": "previous-cpu-job-not-cleaned"})
            try:
                self.lease = self.resources.acquire(cancelled)
            except AttemptInterrupted as error:
                return canonical_json({"error": error.code})
            return canonical_json({"cpus": list(self.lease.cpus)})
        if command == {"op": "release"} and self.lease is not None:
            self.resources.release(self.lease, CleanupState.COMPLETE)
            self.lease = None
            return canonical_json({"released": True})
        raise ValueError("invalid trusted CPU request")

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            # Stop/join ingress before accessing its held reservation. If its
            # thread could not stop, ownership remains uncertain.
            if self._thread is None or not self._thread.is_alive():
                if self.lease is not None:
                    self.resources.release(self.lease, self.worker_cleanup)
                    self.lease = None


def current_cpu_lease():
    return _CURRENT.get()


@contextmanager
def cpu_job(resources=None, *, cancelled=lambda: False):
    """Admit one real CPU operation, locally or from a supervised solver.

    The caller sets lease.cleanup from its executor's receipt. Affinity is
    thread-local on Linux and inherited by child processes, so model threads
    and other jobs retain their own affinity. It is scheduling, not a security
    boundary against a program that deliberately changes its affinity.
    """
    inherited = current_cpu_lease()
    if inherited is not None or resources is None:
        yield inherited
        return
    remote = isinstance(resources, str)
    if remote:
        reply = parse_json(exchange(resources, canonical_json({"op": "acquire"}),
                                    deadline=None, cancelled=cancelled))
        if "error" in reply:
            raise AttemptInterrupted(reply["error"])
        lease = CpuLease(tuple(reply["cpus"]))
    else:
        lease = resources.acquire(cancelled)
    before = os.sched_getaffinity(0)
    token = _CURRENT.set(lease)
    try:
        try:
            os.sched_setaffinity(0, lease.cpus)
        except OSError:
            # No executor has started; a failed binding has nothing to reap.
            lease.cleanup = CleanupState.COMPLETE
            raise
        yield lease
    finally:
        try:
            os.sched_setaffinity(0, before)
        finally:
            _CURRENT.reset(token)
            if remote:
                if lease.cleanup.quiescent:
                    exchange(resources, canonical_json({"op": "release"}), deadline=None, cancelled=cancelled)
                # Otherwise the supervisor keeps this reservation until the
                # whole worker is fenced; it is not handed to the next command.
            else:
                resources.release(lease, lease.cleanup)
