"""Model client of the bounded local RPC transport; not an agent tool."""

from __future__ import annotations

from behave.core.records import canonical_json

from .lifetime import current_window
from .local_rpc import exchange
from .model import ModelFailure, ModelRequest
from .process_model import decode_model_outcome


class RelayModelBackend:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def generate(self, request: ModelRequest):
        packet = canonical_json(request.as_dict())
        window = current_window()
        cancel = (lambda: False) if window is None else window.control.cancelled
        try:
            # The broker owns any explicit request timeout. Local delivery must
            # not race that timeout or charge admission waiting against it.
            response = exchange(self.endpoint, packet,
                                deadline=None if window is None else window.monotonic_deadline,
                                cancelled=cancel)
            return decode_model_outcome(request, response)
        except (OSError, ValueError) as error:
            return ModelFailure("unknown", f"model-relay-{type(error).__name__}")
