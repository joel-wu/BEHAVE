"""FIFO admission for one coordinator's local resource invocations.

No NFS lock, cross-process semaphore or remote cancellation is implied. Share
one pool per resource in its owning coordinator; process workers cannot copy it.
Acquire before writing a dispatch record. A lost in-flight call still needs
fencing/reconciliation, independently of releasing its local capacity slot.
Queueing has no default private timeout: the caller supplies a deadline and/or
cancellation. An explicit policy wait cap can additionally shorten that wait.
"""

from __future__ import annotations

import math
import os
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Condition

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, text

from .attempt import AttemptInterrupted


@dataclass(frozen=True, slots=True)
class AdmissionPolicy:
    resource: str
    concurrency: int
    wait_seconds: int | None = defaults.ADMISSION_WAIT_SECONDS
    starts_per_window: int | None = None
    window_seconds: int | None = None

    def __post_init__(self) -> None:
        text(self.resource, "resource name")
        if type(self.concurrency) is not int or self.concurrency < 1:
            raise ValueError("concurrency must be positive")
        if self.wait_seconds is not None and (type(self.wait_seconds) is not int or self.wait_seconds < 1):
            raise ValueError("wait_seconds must be positive or None")
        if (self.starts_per_window is None) != (self.window_seconds is None):
            raise ValueError("rate count and window must be supplied together")
        for value in (self.starts_per_window, self.window_seconds):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("rate limits must be positive integers")

    @property
    def identity(self) -> str:
        return BlobRef.of(canonical_json({"schema": "behave.admission/1", **self.as_dict()})).sha256

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def validate_policies(policies: Iterable[AdmissionPolicy | None]) -> None:
    """One coordinator resource has one policy, even when several stages use it."""
    seen = {}
    for policy in policies:
        if policy is None:
            continue
        if type(policy) is not AdmissionPolicy:
            raise TypeError("resource configuration requires AdmissionPolicy")
        if policy.resource in seen and seen[policy.resource] != policy:
            raise ValueError("one resource cannot have conflicting admission policies")
        seen[policy.resource] = policy


def shared_pools(**policies: AdmissionPolicy | None) -> dict[str, ResourcePool | None]:
    """Construct one pool per resource; aliases share capacity and quarantine."""
    validate_policies(policies.values())
    pools = {}
    for policy in policies.values():
        if policy is not None and policy.resource not in pools:
            pools[policy.resource] = ResourcePool(policy)
    return {name: None if policy is None else pools[policy.resource] for name, policy in policies.items()}


class ResourcePool:
    def __init__(self, policy: AdmissionPolicy):
        if type(policy) is not AdmissionPolicy:
            raise TypeError("resource pool requires AdmissionPolicy")
        self.policy = policy
        self._pid = os.getpid()
        self._condition = Condition()
        self._waiting: deque[object] = deque()
        self._starts: deque[float] = deque()
        self._active = 0
        self._cleanup = CleanupState.COMPLETE

    def quarantine(self, cleanup: CleanupState) -> None:
        """Stop new local admissions when an owner's service may still be alive.

        Releasing a semaphore slot is not fencing. This poison is monotone for
        this coordinator; there is deliberately no clear/reset method. Restart
        still requires the durable phase journals and external reconciliation.
        """
        if os.getpid() != self._pid:
            raise RuntimeError("a resource pool cannot be copied into a process worker")
        with self._condition:
            self._cleanup = CleanupState.combine(self._cleanup, cleanup)
            self._condition.notify_all()

    @property
    def counts(self) -> tuple[int, int]:
        with self._condition:
            return self._active, len(self._waiting)

    @contextmanager
    def acquire(self, *, deadline: float | None = None,
                cancelled: Callable[[], bool] = lambda: False) -> Iterator[None]:
        if os.getpid() != self._pid:
            raise RuntimeError("a resource pool cannot be copied into a process worker")
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError("resource deadline must be finite")
        if not callable(cancelled):
            raise TypeError("resource cancellation must be callable")
        stop = deadline
        if self.policy.wait_seconds is not None:
            wait_end = time.monotonic() + self.policy.wait_seconds
            stop = wait_end if stop is None else min(stop, wait_end)
        token = object()
        acquired = False
        try:
            with self._condition:
                self._waiting.append(token)
                while True:
                    if not self._cleanup.quiescent:
                        raise AttemptInterrupted("resource-cleanup-" + self._cleanup.value)
                    if cancelled():
                        raise AttemptInterrupted("resource-cancelled")
                    now = time.monotonic()
                    if stop is not None and now >= stop:
                        raise AttemptInterrupted("resource-wait-expired")
                    if self.policy.window_seconds is not None:
                        while self._starts and self._starts[0] <= now - self.policy.window_seconds:
                            self._starts.popleft()
                    rate_ready = (self.policy.starts_per_window is None
                                  or len(self._starts) < self.policy.starts_per_window)
                    if (self._waiting[0] is token and self._active < self.policy.concurrency and rate_ready):
                        self._waiting.popleft()
                        self._active += 1
                        if self.policy.starts_per_window is not None:
                            self._starts.append(now)
                        acquired = True
                        self._condition.notify_all()
                        break
                    wait = defaults.ADMISSION_POLL_SECONDS
                    if stop is not None:
                        wait = min(wait, stop - now)
                    self._condition.wait(wait)
            yield
        finally:
            with self._condition:
                if acquired:
                    self._active -= 1
                elif token in self._waiting:
                    self._waiting.remove(token)
                self._condition.notify_all()
