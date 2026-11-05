"""A correct done/output transaction is not a correct memory-writing result."""

import json

import pytest

from behave.sim import api
from behave.sim.core.compare import comparison_for
from behave.sim.core.failure import FailureRaised
from behave.sim.core.memory import MemoryEnvironmentState, MemoryState
from behave.sim.core.memory_result import MemoryResult, memory_faults
from tests.support.reference import load

from .test_api_path import contract as stream_contract
from .test_api_path import realization as stream_realization

SOURCE = """\
class M:
    def reset(self):
        self.ram = [0] * 4

    def process(self, in_a):
        self.ram[1] = in_a
        return {"out_y": 1}
"""

# This adapter is test-owned. Production uses the isolated protocol's memory
# channel, covered separately below; candidate-defined getters are not trusted.
TRUSTED_ADAPTER = """\
from behave.sim.behavior.reference_memory import ReferenceMemory
from behave.sim.core.memory_result import MemoryResult

def bind(self, memories, environment, horizon):
    self.binding = memories[0], environment.run('ram')

def reset(self):
    self.ram = ReferenceMemory(*self.binding)

def result(self, interfaces):
    return MemoryResult([('ram', self.ram.items())])

M._bind_external_memory = bind
M._external_memory_result = result
M.reset = reset
"""


def setup(reference=None, *, kind="device", width=2, with_output=True):
    base = stream_contract()
    memory = api.MemoryInterface("ram", width, 8, "clk", [api.MemoryPort("rw")])
    contract = api.TaskContract(
        endpoints=base.endpoints if with_output else base.inputs(),
        clocks=base.clocks, memories=(memory,),
        source="result is both out.y and the complete final ram contents")
    base_realization = stream_realization()
    ports = list(base_realization.ports.values()) + [
        api.Port(name, bits, driven_by=owner, clock="clk")
        for name, bits, owner in (
            ("ram_req_valid", 1, "artifact"), ("ram_req_addr", width, "artifact"),
            ("ram_req_we", 1, "artifact"), ("ram_req_wdata", 8, "artifact"),
            ("ram_req_ready", 1, "environment"), ("ram_resp_valid", 1, "environment"),
            ("ram_resp_rdata", 8, "environment"))]
    channels = dict(base_realization.channel_ports)
    if not with_output:
        ports = [port for port in ports if port.name not in {'out_y', 'out_valid', 'out_ready'}]
        channels.pop('out')
    realization = api.Realization(
        kind, ports, channel_ports=channels, identity="writer",
        memories=[api.MemoryRealization("ram", width, 8, "clk", [api.MemoryPort("rw")])])
    reference = reference or load({"behavior.py": (SOURCE + TRUSTED_ADAPTER).encode()},
                                  config={"class": "M"})
    return api.admit(contract, realization, reference=reference), reference


def trace_for(task, cells, *, behavior=False, missing=False, input_value=5):
    environment = api.MemoryEnvironment([
        api.MemoryRun("ram", initial={1: 7, 2: 11}, ready={None: [1, 1]})])
    stimulus = api.Stimulus(
        queues={"in": [(input_value,)]},
        slots=[api.EventSlot(offers=("in",), controls={"rst": 0, "out_ready": 1}),
               api.EventSlot(controls={"rst": 0, "out_ready": 1})],
        memory_environment=environment)
    initial = MemoryEnvironmentState.initial(task.contract.memories, environment)
    final = MemoryEnvironmentState({"ram": MemoryState(
        cells=cells, pending={None: []}, responses={None: None})})
    result = ({"memory-result": MemoryResult([("ram", cells.items())]).as_dict()}
              if behavior else {"external-memory": final.as_dict()})
    first = api.SlotRecord(accepted={"in": (input_value,)}, environment={"external-memory": initial.as_dict()})
    last = api.SlotRecord(
        accepted={} if behavior else {"out": (1,)},
        outputs={"out": ((1,),)} if behavior else {},
        environment={} if missing else result)
    return api.AuditedTrace(stimulus, records=(first, last),
                            domain=task.contract.digest, artifact=task.realization.identity)


@pytest.mark.parametrize("cells, count", [
    ({1: 5, 2: 11}, 0),             # correct result
    ({1: 7, 2: 11}, 1),             # done but no write
    ({1: 6, 2: 11}, 1),             # wrong value
    ({1: 7, 2: 5}, 2),              # wrong destination
    ({1: 5, 2: 12}, 1),             # collateral damage outside the intended write
])
@pytest.mark.parametrize("behavior", [False, True])
def test_output_match_does_not_hide_final_memory_mismatch(cells, count, behavior):
    task, reference = setup(kind="behavior" if behavior else "device")
    trace = trace_for(task, cells, behavior=behavior)
    comparison = comparison_for(task, reference, trace)
    assert comparison.faults == ()
    assert len(comparison.memory_faults) == count
    assert comparison.ok == (count == 0)
    evaluation = api.ConcreteEvaluation(
        task, [api.RunResult.compared(trace.stimulus, trace, comparison)],
        catalogue=api.CatalogueManifest(()))
    assert evaluation.complete
    assert evaluation.mismatch_count == 0
    assert evaluation.memory_mismatch_count == count
    assert evaluation.passed == (count == 0)
    assert evaluation.as_dict()["memory_mismatch_count"] == count
    assert not evaluation.safety_hits


def test_only_last_record_is_a_final_memory_observation():
    task, reference = setup()
    trace = trace_for(task, {1: 5, 2: 11}, missing=True)
    with pytest.raises(FailureRaised) as error:
        comparison_for(task, reference, trace)
    assert not error.value.failure.determines_score
    assert error.value.failure.reason.kind == "memory-result-unavailable"


def test_sparse_full_value_equality_does_not_scan_depth_or_compare_write_history():
    memory = api.MemoryInterface("ram", 40, 8, "clk", [api.MemoryPort("rw")])
    from behave.sim.core.memory import memory_junk_value

    default = memory_junk_value("ram", 42, 8)
    left = MemoryResult([("ram", [(1, 5)])])
    right = MemoryResult([("ram", [(42, default), (1, 5)])])
    assert memory_faults([memory], left, right) == ()
    restored = MemoryResult.from_dict(json.loads(json.dumps(left.as_dict())))
    assert restored.memories == left.memories
    with pytest.raises(AttributeError):
        left.memories = ()


def test_all_memory_faults_participate_in_the_evidence_digest():
    from behave.sim.core.compare import Comparison

    prefix = [("ram", i, 0, 1) for i in range(20)]
    left = Comparison(memory_faults=prefix + [("ram", 20, 0, 1)])
    right = Comparison(memory_faults=prefix + [("ram", 20, 0, 2)])
    a, b = left.as_dict(), right.as_dict()
    assert a["memory_faults"] == b["memory_faults"]
    assert a["memory_faults_elided"] == 1
    assert a["memory_faults_digest"] != b["memory_faults_digest"]


@pytest.mark.parametrize("memories", [(), (("ram", ()),),
    (("字存储", ((3, 1 << 96), (0, 0))), ("ram", ((1, 7),)))])
def test_memory_result_digest_preserves_public_image_bytes(memories):
    import hashlib

    from behave.sim.core.record_value import canonical_json

    image = MemoryResult(memories)
    expected = hashlib.sha256(canonical_json(image.as_dict()).encode()).hexdigest()
    assert image.digest == expected
    assert MemoryResult.from_dict(image.as_dict()).digest == expected


def test_comparison_reuses_only_its_immutable_memory_result_digests(monkeypatch):
    from behave.sim.core import memory_result
    from behave.sim.core.compare import Comparison

    cells = [[3, 7]]
    left = MemoryResult((("ram", cells),))
    right = MemoryResult((("ram", ((3, 8),)),))
    expected = [left.digest, right.digest]
    assert expected[0] != expected[1]
    cells[0][1] = 99
    exported = left.as_dict()
    exported["memories"][0][1][0][1] = 98
    comparison = Comparison(memory_images=(left, right))
    monkeypatch.setattr(MemoryResult, "as_dict", lambda _: pytest.fail(
        "summary must not rebuild immutable RAM cells"))
    monkeypatch.setattr(memory_result, "canonical_json", lambda _: pytest.fail(
        "memory identity must not be recomputed by a later phase"))
    for _ in range(3):
        assert comparison.as_dict()["memory_images"] == expected


def test_active_reset_keeps_the_final_memory_comparison_available():
    from behave.sim.core.memory_result import require_memory_result_path

    task, _ = setup()
    trace = trace_for(task, {1: 5, 2: 11})
    stimulus = api.Stimulus(
        trace.stimulus.queues,
        [trace.stimulus.slots[0], api.EventSlot(reset=True, controls={"rst": 1})],
        memory_environment=trace.stimulus.memory_environment)
    require_memory_result_path(task.contract, stimulus)


def test_memory_can_be_the_only_result_without_an_output_transaction():
    reference = load({"behavior.py": (SOURCE.replace('return {"out_y": 1}', 'return None')
                                     + TRUSTED_ADAPTER).encode()}, config={"class": "M"})
    task, reference = setup(reference, with_output=False)
    trace = trace_for(task, {1: 7, 2: 11})
    trace = api.AuditedTrace(trace.stimulus,
        records=[trace.records[0], api.SlotRecord(environment=trace.records[-1].environment)],
        domain=trace.domain, artifact=trace.artifact)
    compared = comparison_for(task, reference, trace)
    assert not compared.ok and not compared.faults
    assert compared.memory_faults == (("ram", 1, 5, 7),)


def test_reference_memory_is_read_before_close_and_without_reexecuting():
    guarded = TRUSTED_ADAPTER + """\
old_process = M.process
def once(self, in_a):
    if getattr(self, 'called', False):
        raise RuntimeError('reexecuted')
    self.called = True
    return old_process(self, in_a)
M.process = once
"""
    task, reference = setup(load({"behavior.py": (SOURCE + guarded).encode()},
                                 config={"class": "M"}))
    assert comparison_for(task, reference, trace_for(task, {1: 5, 2: 11})).ok


def test_secondary_pool_retains_the_unique_memory_counterexample():
    task, reference = setup()
    traces = [trace_for(task, {1: 6, 2: 11}, input_value=6),
              trace_for(task, {1: 7, 2: 11})]
    results = [api.RunResult.compared(trace.stimulus, trace,
               comparison_for(task, reference, trace)) for trace in traces]
    evaluation = api.ConcreteEvaluation(task, results, catalogue=api.CatalogueManifest(()))
    before = evaluation.fingerprint
    pool = api.minimize_witnesses(evaluation)
    assert results[1] in pool.results
    assert any(feature.startswith('memory-mismatch:')
               for feature in pool.contributions[traces[1].stimulus.fingerprint])
    assert evaluation.fingerprint == before and not evaluation.passed


def test_missing_output_does_not_explain_away_a_memory_difference():
    from behave.sim.evaluation_policy import _only_unanswered_at_the_horizon

    task, reference = setup()
    trace = trace_for(task, {1: 7, 2: 11})
    trace = api.AuditedTrace(trace.stimulus,
        records=[trace.records[0], api.SlotRecord(environment=trace.records[-1].environment)],
        domain=trace.domain, artifact=trace.artifact)
    comparison = comparison_for(task, reference, trace)
    assert len(comparison.faults) == len(comparison.memory_faults) == 1
    evaluation = api.ConcreteEvaluation(task,
        [api.RunResult.compared(trace.stimulus, trace, comparison)],
        catalogue=api.CatalogueManifest(()), telemetry={"monitor_unresolved": [(0, {
            "goal": "monitor.unanswered-at-horizon", "endpoint": "out", "slot": 1,
            "about": {"owed": 1}})]})
    assert _only_unanswered_at_the_horizon(evaluation) == 0


@pytest.mark.isolation
def test_isolated_reference_publishes_final_memory_from_the_same_execution():
    from behave.sim.reference_isolated import ADAPTER

    reference = api.load_reference(ADAPTER, {"behavior.py": SOURCE.encode()}, config={"class": "M"})
    task, _ = setup(reference)
    trace = trace_for(task, {1: 7, 2: 11})
    comparison = comparison_for(task, reference, trace)
    assert comparison.faults == ()
    assert comparison.memory_faults == (("ram", 1, 5, 7),)


@pytest.mark.isolation
@pytest.mark.parametrize("missing_write", [False, True])
@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("active_reset", [False, True])
def test_public_behavior_replay_retains_memory_for_the_shared_score(
        tmp_path, missing_write, reuse, active_reset):
    from behave.sim.reference_isolated import ADAPTER

    reference = api.load_reference(ADAPTER, {"behavior.py": SOURCE.encode()}, config={"class": "M"})
    template, _ = setup(reference)
    source = SOURCE.replace("self.ram[1] = in_a", "pass") if missing_write else SOURCE
    path = tmp_path / "candidate.py"
    path.write_text(source)
    realization = api.realization_of_behavior_source(source, template.contract, file=str(path))
    task = api.admit(template.contract, realization, reference=reference)
    stimulus = trace_for(template, {1: 5, 2: 11}).stimulus
    if active_reset:
        stimulus = after_write_reset(stimulus)
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file=str(path), timeout_s=60),
        api.GenerationSpec(horizon=len(stimulus.slots), queue_bound=1,
                           reset_hold_cycles=0, drain=1),
        bounded_analysis=False)
    from behave.sim.evaluation import ReplayExecution, evaluate

    catalogue = api.evaluation_manifest(task, handle.frontend_catalogue)
    if reuse:
        execution = ReplayExecution(task, handle._artifact, reference, catalogue)
        observed, = execution.traces((stimulus,))
        evaluation = execution.evaluate((stimulus,))
        assert evaluation.results[0].trace is observed
        assert evaluation.execution_cost["reuse"]["completed_runs"] == 1
    else:
        evaluation = evaluate(task, [stimulus], handle._artifact,
                              reference=reference, catalogue=catalogue)
    assert evaluation.complete, evaluation.as_dict()
    assert evaluation.mismatch_count == 0
    assert evaluation.memory_mismatch_count == int(missing_write)
    assert evaluation.passed is not missing_write
    assert "memory-result" in dict(evaluation.results[0].trace.records[-1].environment)


def after_write_reset(stimulus):
    slots = (*stimulus.slots,
             api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 1}),
             api.EventSlot(controls={"rst": 0, "out_ready": 1}))
    return api.Stimulus(queues=stimulus.queues, slots=slots,
        memory_environment=api.MemoryEnvironment([api.MemoryRun(
            "ram", initial={1: 7, 2: 11}, ready={None: [1] * len(slots)})]))


@pytest.mark.cosim
@pytest.mark.parametrize("missing_write", [False, True])
@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("active_reset", [False, True])
def test_public_rtl_replay_scores_memory_even_when_done_matches(
        tmp_path, missing_write, reuse, active_reset):
    from behave.sim.evaluation import ReplayExecution, evaluate
    from behave.sim.reference_isolated import ADAPTER
    from behave.sim.rtl.artifact import RTLArtifact

    reference = api.load_reference(ADAPTER, {"behavior.py": SOURCE.encode()}, config={"class": "M"})
    template, _ = setup(reference)
    rtl = tmp_path / "writer.sv"
    rtl.write_text("""
module writer(input clk, input rst,
 input [7:0] in_a, input in_valid, output in_ready,
 output [7:0] out_y, output out_valid, input out_ready,
 output ram_req_valid, output [1:0] ram_req_addr, output ram_req_we,
 output [7:0] ram_req_wdata, input ram_req_ready,
 input ram_resp_valid, input [7:0] ram_resp_rdata);
 assign in_ready = ram_req_ready;
 assign out_valid = in_valid;
 assign out_y = 8'd1;
 assign ram_req_valid = WRITE_ENABLE && in_valid && !rst;
 assign ram_req_addr = 2'd1;
 assign ram_req_we = 1'b1;
 assign ram_req_wdata = in_a;
endmodule
""".replace("WRITE_ENABLE", "1'b0" if missing_write else "1'b1"))
    artifact = RTLArtifact(str(rtl), "writer", tmp_path / "rtl-work")
    from behave.sim.contract_of import realization_of_source

    realization = realization_of_source(rtl.read_text(), "writer", top="writer",
                                        contract=template.contract)
    task = api.admit(template.contract, realization, reference=reference)
    manifest = artifact.frontend_catalogue(expected_identity=task.realization.identity)
    stimulus = trace_for(template, {1: 5, 2: 11}).stimulus
    if active_reset:
        stimulus = after_write_reset(stimulus)
    catalogue = api.evaluation_manifest(task, manifest)
    if reuse:
        execution = ReplayExecution(task, artifact, reference, catalogue)
        observed, = execution.traces((stimulus,))
        evaluation = execution.evaluate((stimulus,))
        assert evaluation.results[0].trace is observed
        assert evaluation.execution_cost["reuse"]["completed_runs"] == 1
    else:
        evaluation = evaluate(task, [stimulus], artifact,
                              reference=reference, catalogue=catalogue)
    assert evaluation.complete, evaluation.as_dict()
    assert evaluation.mismatch_count == 0
    assert evaluation.memory_mismatch_count == int(missing_write)
    assert evaluation.passed is not missing_write
