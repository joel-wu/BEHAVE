"""Shared inputs and helpers for the test suite.

Problems referenced by name are taken from ``data/problems``, which is tracked
with the repository. Only designs that exercise the verifier itself and have no
counterpart in the data set live under ``tests/assets``; nothing in the test tree
duplicates a problem.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from behave.core.paths import problem_set, repo_root

REPO_ROOT = repo_root()
ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets"


def assert_coverage_telemetry(block):
    """Check the coverage record's schema without imposing a quality floor."""
    assert type(block) is dict
    assert {"covered", "total", "pct"} <= set(block)
    covered, total, pct = block["covered"], block["total"], block["pct"]
    assert type(covered) is int and type(total) is int
    assert 0 <= covered <= total
    assert type(pct) in (int, float) and 0.0 <= float(pct) <= 100.0
    expected = round(100 * covered / total, 2) if total else 100.0
    assert float(pct) == expected
    if "available" in block:
        assert type(block["available"]) is bool
        if not block["available"]:
            assert total == covered == 0
            assert type(block.get("why_unavailable")) is str
            assert block["why_unavailable"]

# One problem per design family. The families are chosen so that the engine
# matrix covers combinational, streaming, stateful and memory-backed designs
# rather than several instances of the same shape.
EXPECTED_DESIGN_FAMILIES = {
    "combinational arithmetic",
    "combinational branching",
    "N-to-1 streaming",
    "sequential stack",
    "external memory handshake",
}

DESIGN_FAMILY_EXAMPLES = (
    {
        "name": "adder_8bit",
        "kind": "combinational arithmetic",
        "path": problem_set("rtllm_v2") / "adder_8bit",
    },
    {
        "name": "comparator_3bit",
        "kind": "combinational branching",
        "path": problem_set("rtllm_v2") / "comparator_3bit",
    },
    {
        "name": "serial2parallel",
        "kind": "N-to-1 streaming",
        "path": problem_set("rtllm_v2") / "serial2parallel",
    },
    {
        "name": "LIFObuffer",
        "kind": "sequential stack",
        "path": problem_set("rtllm_v2") / "LIFObuffer",
    },
    {
        "name": "numeric_pool_dense_linalg_blas_daxpy",
        "kind": "external memory handshake",
        "path": problem_set("numeric") / "numeric_pool_dense_linalg_blas_daxpy",
    },
)
# Each representative was selected by measurement: all three behavior engines
# reach full line coverage of its model within the budget used by the matrix
# below, so a shortfall indicates an engine regression rather than a problem that
# happens to be hard to cover.

# Whole problem sets, sampled rather than enumerated.
PROBLEM_SUITES = (
    {"name": "rtllm", "path": problem_set("rtllm_v2"), "min_examples": 40},
    {"name": "cvdp", "path": problem_set("cvdp"), "min_examples": 100},
    {"name": "verilog-eval", "path": problem_set("verilogeval_v2"), "min_examples": 100},
)

# Designs with more than one clock domain.
MULTICLOCK_EXAMPLES = (
    {
        "name": "cvdp_agentic_async_fifo_compute_ram_application_0001",
        "path": problem_set("cvdp") / "cvdp_agentic_async_fifo_compute_ram_application_0001",
    },
    {
        "name": "cvdp_agentic_async_filo_0001",
        "path": problem_set("cvdp") / "cvdp_agentic_async_filo_0001",
    },
    {
        "name": "cvdp_copilot_fifo_async_0001",
        "path": problem_set("cvdp") / "cvdp_copilot_fifo_async_0001",
    },
    {
        "name": "rtllm_asyn_fifo",
        "path": problem_set("rtllm_v2") / "asyn_fifo",
    },
    {
        "name": "rtllm_synchronizer",
        "path": problem_set("rtllm_v2") / "synchronizer",
    },
)

# Designs whose RTL withholds in_ready for several cycles per accepted input,
# while the behavior model returns one output per call.
MULTICYCLE_STALL_EXAMPLES = (
    {
        "name": "multicycle_mul4",
        "kind": "multi-cycle stalling",
        "path": ASSET_ROOT / "multicycle_mul4",
    },
    {
        "name": "rtllm_multi_16bit",
        "path": problem_set("rtllm_v2") / "multi_16bit",
    },
    {
        "name": "rtllm_radix2_div",
        "path": problem_set("rtllm_v2") / "radix2_div",
    },
)

BEHAVIOR_ENGINES = ("random", "dual", "hypothesis")
COSIM_ENGINE_PAIRS = ("random", "dual", "hypothesis")


def behavior_engine_spec(
    name: str,
    *,
    n_steps: int = 10,
    max_witnesses: int = 80,
    time_budget_s: float = 8.0,
) -> dict:
    spec = {
        "name": name,
        "n_steps": n_steps,
        "max_witnesses": max_witnesses,
        "time_budget_s": time_budget_s,
    }
    if name == "dual":
        spec["engine_kwargs"] = {
            "query_timeout_ms": 1000,
            "stall_limit": 20,
        }
    if name == "hypothesis":
        # T14. A suite that fails once in three tells you nothing on the run where it
        # fails. Here and not in the engine's default, because a fuzzer that always
        # tries the same inputs is a worse fuzzer: two runs finding different things is
        # the point of it in use, and the opposite is the point of it under test.
        spec["engine_kwargs"] = {"derandomize": True}
    return spec


def cosim_behavior_engine_spec(name: str) -> dict:
    return behavior_engine_spec(
        name,
        n_steps=10,
        max_witnesses=30,
        time_budget_s=15.0,
    )


def rtl_engine_spec(name: str) -> dict:
    return {
        "name": name,
        "n_steps": 10,
        "max_witnesses": 30,
        "time_budget_s": 15.0,
    }


def one_second_behavior_specs() -> list[dict]:
    return [
        behavior_engine_spec(name, n_steps=1, max_witnesses=1, time_budget_s=1.0)
        for name in BEHAVIOR_ENGINES
    ]


def one_second_rtl_specs() -> list[dict]:
    return [
        {"name": name, "n_steps": 1, "max_witnesses": 1, "time_budget_s": 1.0}
        for name in COSIM_ENGINE_PAIRS
    ]


def copy_problem(tmp_path: Path, example: dict) -> Path:
    return copy_problem_files(tmp_path, example["path"], example["name"])


def copy_problem_files(tmp_path: Path, source: Path, name: str | None = None) -> Path:
    """Copy the two artifacts a run needs into a scratch directory.

    A run writes generated files next to its sources, so a problem is never
    exercised in place.
    """
    problem = tmp_path / (name or source.name)
    problem.mkdir()
    shutil.copy(source / "behavior.py", problem / "behavior.py")
    shutil.copy(source / "rtl.sv", problem / "rtl.sv")
    return problem


def top_module(rtl_path: Path) -> str:
    text = rtl_path.read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", "", text)
    match = re.search(r"^\s*module\s+(\w+)", text, re.MULTILINE)
    assert match is not None, f"could not parse top module from {rtl_path}"
    return match.group(1)
