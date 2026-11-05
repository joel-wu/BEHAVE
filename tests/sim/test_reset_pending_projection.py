"""Driver pending records follow the same reset policy as protocol obligations."""

import pytest

from behave.sim import api
from behave.sim.core.driver import Driver
from behave.sim.core.monitor import monitor_for
from tests.sim.test_protocol_adapters import _contract


@pytest.mark.parametrize("kind", ["ready-valid", "start-done", "sampled-edge"])
@pytest.mark.parametrize("keeps", [False, True])
def test_pending_projection_retains_and_retires_the_declared_obligation(kind, keeps):
    policy = api.ResetPolicy.KEEPS if keeps else api.ResetPolicy.CLEARS
    contract = _contract("ready-valid" if kind == "sampled-edge" else kind,
                         reset_policy=policy)
    if kind == "sampled-edge":
        image = contract.as_dict()
        for endpoint in image["endpoints"]:
            endpoint["adapter"] = api.sampled_edge(
                endpoint["direction"], valid=endpoint["name"] + "_valid").as_dict()
        contract = api.TaskContract.from_dict(image)
    controls = {"out_ready": 1} if kind == "ready-valid" else {}
    stimulus = api.Stimulus({"in": [(3,), (5,)]}, [
        api.EventSlot(offers=("in",) if index in (0, 3) else (),
                      reset=index == 1, controls={**controls, "rst": int(index == 1)})
        for index in range(5)])
    driver = Driver(contract, stimulus)
    states = []
    for index in range(5):
        reply = index == 4 or (keeps and index == 2)
        signals = {"out_y": 6 if index == 2 else 10}
        if kind == "start-done":
            signals["done"] = int(reply)
        else:
            signals["out_valid"] = int(reply)
            if kind == "ready-valid":
                signals["in_ready"] = 1
        driver.observe(index, driver.presentations(index), signals)
        states.append(driver.last_state)
    expected = [1, int(keeps), 0, 1, 0]
    assert [dict(record.monitor)["outstanding"]
            for record in driver.records] == expected
    assert [state["monitor.outstanding"] for state in states] == expected
    assert [driver._in_flight(driver.records[:end]) for end in range(1, 6)] == expected
    report = monitor_for(contract).check(stimulus, driver.trace())
    assert not report.violations and not report.assumption_violations
