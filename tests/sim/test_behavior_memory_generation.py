"""Ordinary source search uses the real run-owned memory, including resets."""

import pytest

from behave.sim import api
from behave.sim.core.memory import MemoryEnvironment, MemoryInterface, MemoryPort, MemoryRun
from behave.sim.engines.behavior import generation_job as jobs
from behave.sim.engines.behavior.generation_session import PreparedGeneration
from tests.sim.test_behavior_atomic_generation import _payload
from tests.support.reference import load

pytestmark = [pytest.mark.solver, pytest.mark.isolation]

SOURCE = b'''class M:
    def reset(self):
        self.ram = [0] * 4
    def process(self, addr, write, value):
        if write:
            self.ram[addr] = value
        if self.ram[0] == 7 and addr == 2:
            return {"y": 9}
        return {"y": self.ram[addr]}
'''


def contract():
    fields = [api.Field(name, api.FiniteType(width), name)
              for name, width in (("addr", 2), ("write", 1), ("value", 4))]
    return api.TaskContract([
        api.Endpoint("in", "in", fields, api.ready_valid("in", "iv", "ir"), clock="clk"),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(4), "y")],
                     api.ready_valid("out", "ov", "or"), clock="clk")],
        [api.ClockDomain("clk", "clk", "rst")],
        memories=[MemoryInterface("ram", 2, 4, "clk", [MemoryPort("rw")])])


def seed(*, reset=True, last=0):
    return api.Stimulus(queues={"in": ((0, 1, 7), (1, 0, 0))},
        slots=[api.EventSlot(offers=("in",)), api.EventSlot(reset=reset),
               api.EventSlot(offers=("in",))],
        memory_environment=MemoryEnvironment([MemoryRun(
            "ram", initial={0: 0, 1: 0, 2: 0, 3: last}, ready={None: [1] * 3})]))


def query(stimulus):
    queue = iter(dict(stimulus.queues)["in"])
    return {"seed": stimulus.as_dict(), "trace": api.AuditedTrace(stimulus, records=[
        api.SlotRecord(accepted={"in": next(queue)} if slot.offers else {})
        for slot in stimulus.slots], artifact="target-a", domain="memory-test").as_dict()}


@pytest.mark.parametrize("reset", (False, True))
def test_memory_native_prefix_query_and_confirmation(reset, monkeypatch):
    from behave.sim.behavior import lowering
    from behave.sim.symbolic import ranges

    def no_global_output_proof(*_args, **_kwargs):
        pytest.fail("a memory-local query must not prove all bounded outputs")

    monkeypatch.setattr(lowering, "output_contract_evidence", no_global_output_proof)
    monkeypatch.setattr(ranges, "bounded", no_global_output_proof)
    payload = _payload(SOURCE, contract())
    built = jobs._build(payload)
    targets = [goal for goal in built[6] if built[5][goal].family == "decision"]
    session = PreparedGeneration({**payload, "targets": targets})
    original = seed(reset=reset)
    selected = session.select({**query(original), "targets": targets})
    assert list(session.selected.occurrences.values())[1]["state"]["ram"][0] == 7
    remaining = sorted(set(targets) - set(selected["replay"]["hits"]))
    assert remaining
    selected = session.select({**query(original), "targets": remaining})
    requests = [request.as_dict() for request in jobs.decode_selection_reply(selected).requests]
    requests = [request for request in requests
                if request["occurrence"] in session.selected.occurrences
                and session.selected.occurrences[request["occurrence"]]["rank"] == 2]
    # The ranked narrow query changes write only, so addr remains 1 and the
    # target is genuinely unreachable in that local domain. Do not treat the
    # first ranked request as a promise that every field has been released.
    narrow = next(request for request in requests
                  if {field["field"] for field in request["mutable_fields"]} == {1})
    blocked = session.probe({**query(original), "targets": remaining,
                             "request": narrow, "timeout_ms": 20_000})
    assert blocked["outcome"] == "local-unsat", blocked
    request = next(request for request in requests
                   if any(field["field"] == 0 for field in request["mutable_fields"]))
    answer = session.probe({**query(original), "targets": remaining,
                            "request": request, "timeout_ms": 20_000})
    assert answer["outcome"] == "sat", answer
    candidate = api.Stimulus.from_dict(answer["candidate"])
    assert candidate.memory_environment.as_dict() == original.memory_environment.as_dict()
    assert dict(candidate.queues)["in"][0] == (0, 1, 7)
    assert dict(candidate.queues)["in"][1][0] == 2
    confirmed = session.select({**query(candidate), "targets": remaining})
    assert answer["goal"] in confirmed["replay"]["hits"]
    assert answer["goal"] not in selected["replay"]["hits"]
    assert session.selected.manifest == built[5]


def test_memory_context_reuses_only_the_same_image_and_retains_prior_selection():
    built = jobs._build(_payload(SOURCE, contract()))
    guidance = built[4]
    first, _ = guidance.prepare(seed(last=3))
    assert guidance.prepare(seed(last=3))[0] is first
    second, _ = guidance.prepare(seed(last=4))
    assert second is not first
    assert first.proc.initial["ram"][3] == 3
    assert second.proc.initial["ram"][3] == 4
    assert first.frontend_catalogue_manifest() == second.frontend_catalogue_manifest()


def test_memory_layout_is_image_independent_but_selection_requires_the_image():
    payload = _payload(SOURCE, contract())
    built = jobs._build(payload)
    assert jobs._layout(built)["catalogue"]
    with pytest.raises(api.Refused, match="memory-environment-missing"):
        built[4].prepare(api.Stimulus(queues={"in": ((0, 0, 0),)},
                                    slots=[api.EventSlot(offers=("in",))]))
    cached = jobs._context_image(built)
    assert cached is not None
    assert jobs._validated_context(payload, (), cached) is not None


def test_public_isolated_provider_solves_and_confirms_memory_dependent_goal():
    task_contract = contract()
    realization = api.realization_of_behavior_source(
        SOURCE.decode(), task_contract, file="behavior.py", model="M")
    reference = load({"behavior.py": SOURCE}, config={"class": "M"})
    task = api.admit(task_contract, realization, reference=reference)

    def trace(stimulus):
        image = query(stimulus)["trace"]
        image["artifact"] = task.realization.identity
        return api.AuditedTrace.from_dict(image)

    provider = api.prepare_behavior_generation(task, api.GenerationSpec(3, 2, 0, 0, 0),
                                               reference, trace, timeout_s=60, timeout_ms=20_000)
    original = seed()
    targets = [name for name, goal in provider.catalogue_manifest().items()
               if goal.family == "decision"]
    baseline = provider.select(original, targets=targets)
    remaining = sorted(set(targets) - set(baseline.replay.hits))
    selection = provider.select(original, targets=remaining)
    assert selection.requests
    requests = [row for row in selection.requests
                if all(field.transaction == 1 for field in row.mutable_fields)
                and any(field.field == 0 for field in row.mutable_fields)]
    assert requests
    # A relaxed state-preparation proposal may precede the exact goal query
    # for the same occurrence; the exact query is the one that names the goal.
    answers = []
    for request in requests:
        result = provider.probe(original, request)
        answers.append(result.as_dict())
        if result.outcome.value == "sat":
            break
    else:
        raise AssertionError(f"no exact query answered sat: {answers}")
    assert result.candidate.memory_environment.as_dict() == original.memory_environment.as_dict()
    confirmed = provider.select(result.candidate, targets=remaining)
    assert result.goal in confirmed.replay.hits
    assert result.goal not in baseline.replay.hits
