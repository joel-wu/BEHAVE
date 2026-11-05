"""An event-only interface has native goals but no local payload to mutate."""

import pytest

from behave.sim import api
from behave.sim.engines.behavior import generation_job as jobs
from tests.sim.test_behavior_atomic_generation import _payload
from tests.support.reference import load

pytestmark = [pytest.mark.solver, pytest.mark.isolation]

SOURCE = b'''class M:
    def reset(self):
        self.q = 0
    def process(self):
        self.q = (self.q + 1) & 3
        if self.q == 3:
            return {"y": 3}
        return {"y": self.q}
'''


def test_event_only_source_keeps_catalogue_and_native_hits_without_fake_queries():
    contract = api.TaskContract([
        api.Endpoint("in", "in", [], api.sampled_edge("in"), clock="clk"),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(2), "y")],
                     api.sampled_edge("out"), clock="clk")],
        [api.ClockDomain("clk", "clk", "rst")])
    seed = api.Stimulus(queues={"in": ((),)}, slots=[api.EventSlot(offers=("in",))])
    realization = api.realization_of_behavior_source(SOURCE.decode(), contract, file="behavior.py", model="M")
    reference = load({"behavior.py": SOURCE}, config={"class": "M"})
    task = api.admit(contract, realization, reference=reference)

    def trace(stimulus):
        return api.AuditedTrace(stimulus, records=[api.SlotRecord(accepted={"in": ()})],
                                artifact=task.realization.identity, domain="event-only")

    payload = {**_payload(SOURCE, contract), "target_artifact": task.realization.identity}
    built = jobs._build(payload)
    assert jobs._layout(built)["input_endpoints"] == [{"endpoint": "in", "fields": []}]
    targets = [goal for goal in built[6] if built[5][goal].family == "decision"]
    selected = jobs.selected_trace(built, seed.as_dict(), trace(seed).as_dict(), targets)
    assert len(selected.occurrences) == 1
    assert set(targets) & set(selected.replay.hits)
    assert set(targets) - set(selected.replay.hits)  # Unhit is not deleted or declared unreachable.
    provider = api.prepare_behavior_generation(task, api.GenerationSpec(1, 1, 0, 0, 0),
                                               reference, trace, timeout_s=60)
    assert provider.select(seed).requests.templates == ()
    assert set(targets) <= set(provider.catalogue_manifest())
