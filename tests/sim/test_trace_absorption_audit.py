"""Trace composition retains every step and absorbs failures without re-execution."""

import pytest

pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import audit
from behave.sim.symbolic import interp
from behave.sim.behavior.lowering import lower_with_state_ranges
from behave.sim.symbolic.uniform_array import UniformArray
from behave.sim.core.audit import AuditCheckStatus

from .test_encoding_plan import instance

SOURCE = """class M:
    def reset(self):
        self.n = 0
        self.buf = [[0, 0], [0, 0]]
    def process(self, a):
        self.n = self.n + 1
        self.buf[0][0] = a
        return {"y": a // (a & 1)}
"""


@pytest.fixture
def proc():
    return lower_with_state_ranges(SOURCE, input_widths=[4])


@pytest.mark.parametrize("mutation", [
    "repeated-suffix-hit", "transient-suffix-state", "missing-suffix-state",
    "normal-output", "early-hit-lost",
])
def test_absorption_audit_checks_step_evidence_not_only_the_final_union(
        proc, monkeypatch, mutation):
    trace = [{"a": 3}, {"a": 2}, {"a": 1}, {"a": 3}]
    original = interp.run_trace_detailed
    assert audit.absorbed_suffix(proc, trace) == []

    def changed(*args, **kwargs):
        states, outputs, hits, error = original(*args, **kwargs)
        if len(outputs) == len(trace):
            if mutation == "repeated-suffix-hit":
                hits[2] = set(hits[1])  # Same final union, wrong step.
            elif mutation == "transient-suffix-state":
                states[3] = dict(states[3], n=99)  # Final state remains honest.
            elif mutation == "missing-suffix-state":
                states[3] = {key: value for key, value in states[3].items()
                             if key != "n"}
            elif mutation == "normal-output":
                outputs[0] = interp.StepOutput(1, {"y": [99]})
            else:
                hits[0] = set()
        return states, outputs, hits, error

    monkeypatch.setattr(interp, "run_trace_detailed", changed)
    assert audit.absorbed_suffix(proc, trace), mutation


@pytest.mark.parametrize("mutation", [
    "missing-state", "extra-state", "wrong-error", "lost-hits",
    "suffix-output", "truncated-outputs",
])
def test_absorption_audit_checks_the_public_aggregate(proc, monkeypatch, mutation):
    trace = [{"a": 3}, {"a": 2}, {"a": 1}]
    original = interp.run_trace

    def changed(*args, **kwargs):
        state, outputs, hits, error = original(*args, **kwargs)
        if mutation == "missing-state":
            state.pop("n")
        elif mutation == "extra-state":
            state["smuggled"] = 1
        elif mutation == "wrong-error":
            error = "IndexError"
        elif mutation == "lost-hits":
            hits = set()
        elif mutation == "suffix-output":
            outputs[-1] = interp.StepOutput(1, {"y": [99]})
        else:
            outputs.pop()
        return state, outputs, hits, error

    monkeypatch.setattr(interp, "run_trace", changed)
    assert audit.absorbed_suffix(proc, trace), mutation


@pytest.mark.parametrize("entrypoint", ["run_trace", "run_trace_detailed"])
def test_trace_composition_preserves_output_value_types(proc, monkeypatch, entrypoint):
    original = getattr(interp, entrypoint)
    trace = [{"a": 1}]
    assert audit.absorbed_suffix(proc, trace) == []

    def changed(*args, **kwargs):
        state, outputs, hits, error = original(*args, **kwargs)
        assert outputs[0].transactions == [{"y": 1}]
        outputs[0] = interp.StepOutput(1, {"y": [True]})
        return state, outputs, hits, error

    monkeypatch.setattr(interp, entrypoint, changed)
    findings = audit.absorbed_suffix(proc, trace)
    assert any("transactions" in finding.what for finding in findings), findings


def test_trace_composition_ignores_unused_output_capacity(proc, monkeypatch):
    original = interp.run_trace

    def padded(*args, **kwargs):
        state, outputs, hits, error = original(*args, **kwargs)
        outputs[0] = interp.StepOutput(1, {"y": [1, 99]})
        return state, outputs, hits, error

    monkeypatch.setattr(interp, "run_trace", padded)
    assert audit.absorbed_suffix(proc, [{"a": 1}]) == []


@pytest.mark.parametrize("values", [[], [2], [2, 3, 3], [3, 3, 2], [3, 1, 3]])
def test_absorption_accepts_honest_traces_from_the_supplied_state(proc, values):
    initial = {"n": 7, "buf": [[1, 2], [3, 4]]}
    assert audit.absorbed_suffix(proc, [{"a": a} for a in values], initial) == []
    assert initial == {"n": 7, "buf": [[1, 2], [3, 4]]}


def test_absorption_compares_compact_and_dense_state_extensionally(proc, monkeypatch):
    original = interp.run_trace

    def compact(*args, **kwargs):
        state, outputs, hits, error = original(*args, **kwargs)
        state["buf"] = UniformArray.uniform(2, UniformArray.uniform(2, 0))
        return state, outputs, hits, error

    monkeypatch.setattr(interp, "run_trace", compact)
    assert audit.absorbed_suffix(proc, [{"a": 0}, {"a": 3}]) == []


def test_late_failure_audit_uses_linear_step_work(proc, monkeypatch):
    original = interp.run_step
    calls = []

    def counted(*args, **kwargs):
        calls.append(None)
        return original(*args, **kwargs)

    monkeypatch.setattr(interp, "run_step", counted)
    for length in (16, 32, 64):
        calls.clear()
        trace = [{"a": 3}] * (length - 1) + [{"a": 2}]
        assert audit.absorbed_suffix(proc, trace) == []
        assert len(calls) <= 3 * length, (length, len(calls))


def test_positioned_public_audit_rejects_post_failure_hits(monkeypatch):
    model = instance(SOURCE, bound=3)
    traces = [[{"a": 3}, {"a": 2}, {"a": 1}]]
    assert model.audit(traces).status is AuditCheckStatus.AGREED
    original = interp.run_trace_detailed

    def repeated(*args, **kwargs):
        states, outputs, hits, error = original(*args, **kwargs)
        if error and len(hits) == 3:
            hits[-1] = set(hits[-2])
        return states, outputs, hits, error

    monkeypatch.setattr(interp, "run_trace_detailed", repeated)
    got = model.audit(traces)
    assert got.status is AuditCheckStatus.INCONSISTENT, got.as_dict()
    assert any("hits" in finding for finding in got.findings)
