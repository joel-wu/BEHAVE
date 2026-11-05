"""Complete B/B and B/R callers share admission, not just synthetic CPU jobs."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from behave.sim import api
from behave.sim.cpu_admission import reserve_cpus
from tests.sim.test_baseline_public_sequence import _contract
from tests.sim.test_cpu_admission import eventually
from tests.sim.test_parallel_public_replay import BEHAVIOR, REFERENCE, RTL, _semantic


def _score(directory, kind, mutant):
    directory.mkdir()
    contract = _contract()
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    generation = api.GenerationSpec(256, 100, 32, 2)
    if kind == "rtl":
        source = RTL.replace("ERROR", "8'd1" if mutant else "8'd0")
        path = directory / "candidate.sv"
        path.write_text(source)
        realization = api.realization_of_rtl_source(source, "contention", top="M", contract=contract)
        task = api.admit(contract, realization, reference=reference)
        handle = api.prepare_rtl(task, api.RTLConfig(
            path, "M", directory / "rtl", timeout_s=120), generation)
    else:
        source = BEHAVIOR.replace("ERROR", "1" if mutant else "0")
        path = directory / "candidate.py"
        path.write_text(source)
        realization = api.realization_of_behavior_source(source, contract, file="candidate.py")
        task = api.admit(contract, realization, reference=reference)
        handle = api.prepare_behavior(task, api.BehaviorConfig(
            path, file="candidate.py", timeout_s=60), generation, bounded_analysis=False)
    stimuli = api.build_baseline(contract, generation, realization=realization,
        policy=api.BaselinePolicy(case_limit=4, max_queue_depth=100)).stimuli
    assert all(len(queue) == 100 for stimulus in stimuli for _, queue in stimulus.queues)
    authority = handle.replay(stimuli, reference=reference)
    assert authority.authoritative and authority.evaluation.complete, authority.as_summary_dict()
    assert authority.passed is not mutant
    assert all(result.trace.accepted_count({name}) == 100
               for result in authority.evaluation.results for name, _ in result.stimulus.queues)
    return authority.evaluation


@pytest.mark.isolation
@pytest.mark.eda
def test_contended_public_scores_finish_and_cancelled_waiter_does_not_poison_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("BEHAVE_REPLAY_WORKERS", "2")
    cancelled = Event()
    # Two copies of each case: both stateful isolated references and negative
    # score decisions must be independent of competing runs and completion order.
    cases = [(kind, mutant) for kind in ("behavior", "rtl") for mutant in (False, True)] * 2
    with api.CpuPool(4) as cpu, ThreadPoolExecutor(max_workers=9) as callers:
        def rollout(index, kind, mutant, stop=None):
            with api.cpu_scope(cpu.endpoint, cancelled=stop) as usage:
                result = _score(tmp_path / str(index), kind, mutant)
            receipts = usage.as_dict()["reservations"]
            assert receipts and all(row["released"] for row in receipts)
            assert all(row["workers"] <= 4 for row in receipts)
            return result

        with api.cpu_scope(cpu.endpoint), reserve_cpus(4):
            futures = [callers.submit(rollout, index, *case) for index, case in enumerate(cases)]
            stopped = callers.submit(rollout, 8, "behavior", False, cancelled.is_set)
            eventually(lambda: cpu.snapshot()["queued"] == 9)
            cancelled.set()
            with pytest.raises(api.FailureRaised) as exc:
                stopped.result(timeout=30)
            assert exc.value.failure.kind == "cpu-wait-cancelled"
            assert not exc.value.failure.determines_score
            eventually(lambda: cpu.snapshot()["queued"] == 8)
        results = [future.result(timeout=240) for future in futures]
        for first, repeated in zip(results[:4], results[4:]):
            assert _semantic(first) == _semantic(repeated)
        assert cpu.snapshot()["reserved"] == 0
        assert cpu.snapshot()["peak_reserved"] == 4
        assert not cpu.snapshot()["quarantined"]
