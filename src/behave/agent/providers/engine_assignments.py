"""Attempt-lifetime balancing for the coordinator's owned local engines.

Eval and training already share a model admission pool and ModelRelay. Keep the
assignment there, across individual broker processes and intervening tool calls.
No proxy, polling service or second generation scheduler is needed.
"""

from contextlib import contextmanager
from threading import Lock
from weakref import WeakKeyDictionary


class EngineAssignments:
    """Assign new sessions to the engine with the fewest unfinished sessions."""

    def __init__(self, urls):
        self.active = dict.fromkeys(urls, 0)
        self.assigned = dict.fromkeys(urls, 0)
        self.sessions = {}
        self._lock = Lock()

    @contextmanager
    def acquire(self, key):
        with self._lock:
            if key in self.sessions:
                url, holders = self.sessions[key]
            else:
                url = min(self.active, key=lambda item: (self.active[item], self.assigned[item]))
                self.active[url] += 1
                self.assigned[url] += 1
                holders = 0
            self.sessions[key] = (url, holders + 1)
        try:
            yield url
        finally:
            with self._lock:
                _, holders = self.sessions[key]
                if holders == 1:
                    del self.sessions[key]
                    self.active[url] -= 1
                else:
                    self.sessions[key] = (url, holders - 1)


_owners = WeakKeyDictionary()
_owners_lock = Lock()


def engine_assignments(owner, urls):
    """One allocator per existing admission owner and explicit engine set.

    The owner is process-local, just like ResourcePool. It must be shared by all
    rollouts using these engines. Different training rounds/engine owners do not
    inherit stale assignments. The registry does not keep an owner alive.
    """
    key = tuple(sorted(urls))
    with _owners_lock:
        pools = _owners.setdefault(owner, {})
        if key not in pools:
            pools[key] = EngineAssignments(key)
        return pools[key]
