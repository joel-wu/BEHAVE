"""Bit-exact FP32 replay, independent of a search engine's wall-clock progress."""

import json
import random
from pathlib import Path

import pytest

from behave.core.paths import problem_set
from behave.sim import api
from behave.sim.behavior import LIBRARY_DIR

# Designs whose behavior models are dominated by FP32 arithmetic, where a single
# rounding or special-value discrepancy between the model and the RTL is enough
# to fail.
FP32_NUMERIC_EXAMPLES = (
    {
        "name": "numeric_pool_distance_cityblock_distance",
        "model": "CityblockDistanceF32x4",
        "top": "CityblockDistanceF32x4",
        "path": problem_set("numeric") / "numeric_pool_distance_cityblock_distance",
    },
    {
        "name": "numeric_pool_interpolation_arm_linear_interp_f32",
        "model": "LinearInterpF32",
        "top": "linear_interp_f32",
        "path": problem_set("numeric") / "numeric_pool_interpolation_arm_linear_interp_f32",
    },
)


def _fp32_rows(width):
    # Fixed binary32 patterns exercise cancellation, signs, rounding boundaries,
    # subnormals, infinities and NaNs; seeded raw patterns extend the same corpus.
    values = (0, 0x80000000, 0x3F800000, 0xBF800000, 0x3F800001,
              0x3B800000, 0x00000001, 0x007FFFFF, 0x00800000,
              0x7F7FFFFF, 0x7F800000, 0xFF800000, 0x7FC00000)
    rows = [[values[(index + 3 * column) % len(values)]
             for column in range(width)] for index in range(len(values))]
    rng = random.Random(20260907)
    rows.extend([[rng.getrandbits(32) for _ in range(width)] for _ in range(12)])
    return rows


@pytest.mark.cosim
@pytest.mark.eda
@pytest.mark.isolation
@pytest.mark.parametrize("example", FP32_NUMERIC_EXAMPLES, ids=lambda x: x["name"])
def test_fp32_numeric_example_cosim_passes(tmp_path: Path, example: dict):
    problem = example["path"]
    rtl = problem / "rtl.sv"
    contract = api.TaskContract.from_dict(json.loads((problem / "interface.json").read_text()))
    endpoint, = contract.inputs()
    assert all(field.type.width == 32 for field in endpoint.fields)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER, {
        "behavior.py": (problem / "behavior.py").read_bytes(),
        "fp_lib.py": (LIBRARY_DIR / "fp_lib.py").read_bytes(),
    }, config={"class": example["model"]})
    task = api.admit(contract, api.realization_of_rtl_source(
        rtl.read_text(), top=example["top"], contract=contract), reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(rtl, example["top"], tmp_path / "rtl"))
    rows = _fp32_rows(len(endpoint.fields))
    stimulus = api.Stimulus(
        queues={endpoint.name: rows}, replay_policy=api.ReplayPolicy(),
        memory_environment=api.MemoryEnvironment([
            api.MemoryRun.from_policy(memory, None) for memory in contract.memories]),
        source="fixed-fp32-regression")
    # Keep the exact corpus in both fresh-reset and continuous replay. This uses
    # public explicit-pool scoring, not a fabricated baseline or a search budget.
    suite = api.build_replay_suite(contract, (stimulus,))
    result = handle.replay(suite, reference=reference)
    assert result.authoritative and result.passed, result.as_dict()
    evaluation = result.evaluation
    assert evaluation.complete and evaluation.required_complete
    assert evaluation.mismatch_count == 0
    assert len(evaluation.results) == 2
    for run, requested in zip(evaluation.results, suite.stimuli, strict=True):
        assert run.stimulus == requested
        assert run.trace.completion is api.Completion.NORMAL
        assert run.trace.accepted_count([endpoint.name]) == len(rows)
        assert run.trace.accepted_count(["out"]) == len(rows)
