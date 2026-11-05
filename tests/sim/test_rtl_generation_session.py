"""Prepared RTL queries retain formulas, never query state or answers."""

from __future__ import annotations

import io
import json

import pytest

pytest.importorskip("z3")
pytestmark = pytest.mark.solver

from behave.sim import job_session as transport
from behave.sim.core.catalogue import CatalogueManifest


_CONTEXT = {"complete_catalogue": CatalogueManifest(()).as_dict()}
_OPENED = {"catalogue_digest": CatalogueManifest(()).digest()}
_LAYOUT = {"version": "rtl-generation-layout/1", "status": "ok",
           "outputs": [], "state_names": []}


def _question(payload):
    return {key: value for key, value in payload.items() if key not in {"seed", "trace"}}


@pytest.mark.parametrize("limit", [None, 1024])
def test_session_frames_use_only_the_explicit_wire_budget(monkeypatch, limit):
    monkeypatch.setattr(transport, "WIRE_BYTES", limit)
    document = json.dumps({"version": "test", "id": 0, "op": "open", "payload": "x" * 2048})
    reader, writer = io.BytesIO(document.encode() + b"\n"), io.BytesIO()
    calls = []

    def dispatch(op, value):
        calls.append(op)
        return value

    transport.serve(reader, writer, version="test", dispatch=dispatch)
    reply = json.loads(writer.getvalue())
    assert reply["ok"] is (limit is None)
    assert calls == (["open"] if limit is None else [])

