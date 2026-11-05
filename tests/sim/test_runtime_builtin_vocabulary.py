"""Resolved source builtins must remain available in concrete execution."""

import builtins

import pytest

from behave.sim import api
from behave.sim.behavior.extract import KNOWN_BUILTIN_SPELLINGS
from behave.sim.behavior.instrumented import _safe_builtins
from behave.sim.behavior.isolated import isolated_model


def test_resolved_builtin_vocabulary_is_present_in_the_runtime():
    runtime = _safe_builtins()
    assert KNOWN_BUILTIN_SPELLINGS <= runtime.keys()
    for name in KNOWN_BUILTIN_SPELLINGS:
        assert runtime[name] is getattr(builtins, name)


def test_builtin_vocabulary_does_not_open_host_capabilities():
    runtime = _safe_builtins()
    assert not {"open", "eval", "exec", "compile", "globals", "locals"} & runtime.keys()
    with pytest.raises(TypeError):
        runtime["str"] = int
    with pytest.raises(ImportError, match="not an approved library"):
        runtime["__import__"]("os")


@pytest.mark.isolation
@pytest.mark.parametrize("expression, expected", [
    ("len(str(in_a))", 1),
    ("tuple([in_a])[0]", 7),
    ("dict([('value', in_a)])['value']", 7),
    ("int(all([True, in_a > 0]))", 1),
    ("int(any([False, in_a > 0]))", 1),
    ("next(iter([in_a]))", 7),
    ("len(hex(in_a))", 3),
])
def test_registered_builtins_execute_in_the_production_child(expression, expected):
    source = (
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, in_a):\n"
        f"        return {{'out_y': {expression}}}\n"
    )
    factory = isolated_model(source)
    try:
        assert factory().process(in_a=7) == {"out_y": expected}
    finally:
        factory.close()


@pytest.mark.isolation
@pytest.mark.parametrize('syntax', ['ordinary', 'object', 'annotations'])
def test_public_score_does_not_turn_a_registered_builtin_into_a_candidate_fault(tmp_path, syntax):
    byte = api.FiniteType(8)
    contract = api.TaskContract([
        api.Endpoint("in", api.Direction.IN, [api.Field("a", byte, "in_a")],
                     api.ready_valid("in", "in_valid", "in_ready"),
                     clock="clk", kind=api.Kind.TRANSACTION),
        api.Endpoint("out", api.Direction.OUT, [api.Field("y", byte, "out_y")],
                     api.ready_valid("out", "out_valid", "out_ready"),
                     clock="clk", kind=api.Kind.TRANSACTION, answers_per_transaction=1),
    ], [api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=3)])
    reference_source = (
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, in_a):\n"
        "        return {'out_y': in_a}\n"
    )
    if syntax == 'object':
        reference_source = reference_source.replace('class M:', 'class M(object):')
    elif syntax == 'annotations':
        reference_source = ('from __future__ import annotations\n' + reference_source.replace(
            'self, in_a)', 'self, in_a: Missing())'))
    source = reference_source.replace("'out_y': in_a", "'out_y': int(str(in_a))")
    candidate = tmp_path / "candidate.py"
    candidate.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                   {"behavior.py": reference_source.encode()})
    realization = api.realization_of_behavior_source(source, contract)
    task = api.admit(contract, realization, reference=reference)
    generation = api.GenerationSpec(horizon=5, queue_bound=1,
                                    reset_hold_cycles=3, drain=1)
    handle = api.prepare_behavior(task, api.BehaviorConfig(candidate), generation)
    providers = handle.generation_providers(
        reference, workdir=tmp_path / "generation", enable_local_guidance=False)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "run", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1),
        providers=providers, probe_budget=0, run_analysis=False))
    assert result.evaluation is not None, result.as_dict()
    assert result.evaluation.complete and result.evaluation.passed
    assert result.evaluation.candidate_failures == ()
    assert result.record_path
