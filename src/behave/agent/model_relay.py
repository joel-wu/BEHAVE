"""Provider-owned invocation and billing behind the shared local RPC transport."""

from __future__ import annotations

import time
from contextlib import ExitStack

from behave.core.lifecycle import CleanupState
from behave.core.records import parse_json

from .admission import ResourcePool
from .attempt import AttemptInterrupted, AttemptSpec
from .journal import AttemptJournal
from .lifetime import AttemptControl, ExecutionWindow, execution_window
from .local_rpc import LocalRPCServer
from .model import ModelFailure, ModelRequest
from .model_ledger import ModelLedger
from .process_model import ModelCommand, ProcessModelBackend, encode_model_outcome


class ModelRelay(LocalRPCServer):
    def __init__(self, *, spec: AttemptSpec, command: ModelCommand, journal: AttemptJournal,
                 pool: ResourcePool, deadline: float | None, cancelled=lambda: False):
        if type(spec) is not AttemptSpec or type(command) is not ModelCommand or type(pool) is not ResourcePool:
            raise TypeError("model relay requires frozen attempt, command and coordinator pool")
        if spec.model_policy.get("transport") != command.identity:
            raise ValueError("relay command differs from attempt transport")
        super().__init__(name="model-relay", deadline=deadline, cancelled=cancelled)
        self.spec, self.command, self.pool = spec, command, pool
        self._routing = ExitStack()
        self._worker_url = None
        self._local_endpoint = None
        if command.revision == "behave.local-chat/8":
            from .providers.local_chat import LocalChatEndpoint

            self._local_endpoint = LocalChatEndpoint.from_dict(parse_json(command.argv[-1].encode()))
        self.ledger = ModelLedger(journal, {"schema": "behave.model-relay/1", "spec": spec.as_dict(),
                                          "command": command.as_dict(), "admission": pool.policy.as_dict()})

    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            # Release after the attempt worker and its in-flight broker settle,
            # not after each HTTP call or only after final Sim scoring.
            self._routing.close()

    def _backend(self, request):
        if self._local_endpoint is not None and self._worker_url is None:
            from .providers.engine_assignments import engine_assignments

            endpoint = self._local_endpoint
            key = endpoint.headers(request)["X-SMG-Routing-Key"]
            self._worker_url = self._routing.enter_context(
                engine_assignments(self.pool, endpoint.worker_urls).acquire(key))
        return ProcessModelBackend(self.command, local_worker_url=self._worker_url)

    def _valid(self, request):
        # The shared loop owns the network retry budget. A dispatch may instead
        # be a shortened-context retry, so it is not bounded by max_retries.
        return (request.call_id == f"{self.spec.attempt_id}:{request.turn}:{request.dispatch}"
                and request.turn < self.spec.max_turns
                and request.model_policy == self.spec.model_policy
                and request.timeout_seconds == self.spec.request_timeout_seconds)

    def respond(self, data: bytes, *, cancelled) -> bytes:
        self.child_cleanup.require_quiescent(self.name)
        request = ModelRequest.from_dict(parse_json(data))
        if not self._valid(request):
            return encode_model_outcome(request, ModelFailure("permanent", "model-relay-request-mismatch"))
        result = self.ledger.lookup(request)
        if result is None:
            # Admission is not model inference. The backend starts an optional
            # request timeout only after a worker slot has been acquired.
            deadline = self.deadline
            expires = None if deadline is None else time.time() + max(0, deadline - time.monotonic())
            window = ExecutionWindow(AttemptControl(cancelled=cancelled), expires, deadline)
            try:
                with self.pool.acquire(deadline=deadline, cancelled=cancelled), execution_window(window):
                    if cancelled() or window.remaining() <= 0:
                        raise AttemptInterrupted("model-admission-cancelled")
                    backend = self._backend(request)
                    try:
                        result = self.ledger.dispatch(request, backend.generate)
                    finally:
                        self.child_cleanup = CleanupState.combine(self.child_cleanup, backend.cleanup_state)
                        self.pool.quarantine(self.child_cleanup)
            except AttemptInterrupted as error:
                result = self.ledger.decline(request, error.code)
        return encode_model_outcome(request, result)
