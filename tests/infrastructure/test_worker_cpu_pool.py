"""Shared public CPU admission reaches the actual isolated Eval workers."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from test_cocotb_backend import POLICY, submitted
from test_data_checks import comparison

from behave.sim import api
from behave.task_execution.cocotb_worker import CocotbBackend
from behave.task_execution.comparison import compare_sources
from behave.task_execution.testbench import observe_testbench


@pytest.mark.isolation
@pytest.mark.eda("verilator", "yosys")
def test_shared_pool_crosses_judge_worker_transport(tmp_path):
    def invoke(component, endpoint):
        path = tmp_path / component
        inputs, journal = comparison(path, real=True, component=component)
        policy = replace(inputs.policy, baseline_cases=2, sequence_length=4, search_seconds=0, analysis=None)
        inputs = replace(inputs, run_policy=b"null", judge_policy=policy.to_bytes())
        work = path / "execution"
        work.mkdir()
        return compare_sources(inputs, objects=journal.objects, workdir=work, cpu_pool=endpoint)

    with api.CpuPool(2) as cpu, ThreadPoolExecutor(max_workers=2) as callers:
        futures = [callers.submit(invoke, component, cpu.endpoint.as_dict()) for component in ("rtl", "behavior")]
        results = [future.result() for future in futures]
        for result in results:
            assert result.score == 1, result.as_dict()
            assert result.cleanup.quiescent
        snapshot = cpu.snapshot()
        assert 0 < snapshot["peak_reserved"] <= 2
        assert snapshot["reserved"] == snapshot["queued"] == 0
        assert not snapshot["quarantined"]


@pytest.mark.isolation
@pytest.mark.eda("verilator")
def test_shared_pool_crosses_final_testbench_transport(tmp_path):
    _, public, submission, _, journal = submitted(tmp_path)
    with api.CpuPool(2) as cpu:
        result = observe_testbench(task=public, submission=submission, policy=POLICY, journal=journal,
                                   backend=CocotbBackend(cpu_pool=cpu.endpoint.as_dict()))
        assert result.observation.comparison == "passed", result.as_dict()
        assert result.observation.coverage is not None
        snapshot = cpu.snapshot()
        assert 0 < snapshot["peak_reserved"] <= 2
        assert snapshot["reserved"] == snapshot["queued"] == 0
        assert not snapshot["quarantined"]
