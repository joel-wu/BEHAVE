"""A reply allowance must not become the lifetime cap of a long reference run."""

import ast
import math
from types import SimpleNamespace

import pytest

from behave.sim.behavior import isolated
from behave.sim.scoring_policy import ScoringPolicy, scoring_scope


def test_public_adapter_matches_registered_frontend():
    from behave.sim import api, reference_isolated

    assert api.ISOLATED_REFERENCE_ADAPTER == reference_isolated.ADAPTER


def test_standalone_cpu_retains_its_total_cap():
    assert isolated._session_cpu_seconds(3) == 3


def test_scoped_cpu_uses_remaining_owner_not_reply_allowance():
    with scoring_scope(ScoringPolicy(time_budget_s=60)) as budget:
        allowance = isolated._session_cpu_seconds(2)
        assert 2 < allowance <= 60
        budget.deadline -= 40
        assert 0 < isolated._session_cpu_seconds(2) <= 20


def test_cpu_refresh_keeps_hard_owner_cap():
    class Resource:
        RLIMIT_CPU = 0
        RUSAGE_SELF = 0
        RLIM_INFINITY = -1

        def __init__(self):
            self.limit = (2, 10)
            self.used = 5.2

        def getrlimit(self, _which):
            return self.limit

        def setrlimit(self, _which, limits):
            self.limit = limits

        def getrusage(self, _which):
            return SimpleNamespace(ru_utime=self.used, ru_stime=0.3)

    node = next(n for n in ast.parse(isolated._CHILD).body
                if isinstance(n, ast.FunctionDef) and n.name == '_answer_cpu_budget')
    resource = Resource()
    namespace = {'resource': resource, 'math': math}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<cpu-limit-helper>', 'exec'), namespace)
    namespace['_answer_cpu_budget'](2)
    assert resource.limit == (8, 10)
    resource.used = 9
    namespace['_answer_cpu_budget'](2)
    assert resource.limit == (10, 10)


@pytest.mark.parametrize('nested', [False, True])
def test_many_finite_calls_keep_state_past_one_cpu_allowance(nested):
    from contextlib import nullcontext
    from behave.sim.job_context import _named_job_scope

    source = '''class M:
    def __init__(self): self.calls = 0
    def reset(self): self.calls = 0
    def process(self, value):
        result = 0
        for i in range(20000): result = (result + value + i) & 65535
        self.calls += 1
        return {"result": result, "calls": self.calls}
'''
    with scoring_scope(ScoringPolicy(time_budget_s=60)):
        with _named_job_scope() if nested else nullcontext():
            with isolated.IsolatedModel(source, cls='M', strict_module=False,
                    snapshot_policy='on-demand',
                    limits=isolated.Limits(seconds=2, wall_seconds=10, memory_bytes=512 << 20)) as model:
                for i in range(2000):
                    assert model.process(value=i) == {
                        'result': (20000 * i + 19999 * 20000 // 2) & 65535,
                        'calls': i + 1}
                model.reset()
                assert model.process(value=0)['calls'] == 1


@pytest.mark.parametrize('nested', [False, True])
def test_single_infinite_call_is_still_terminated(nested):
    from contextlib import nullcontext
    from behave.sim.job_context import _named_job_scope

    source = '''class M:
    def reset(self): pass
    def process(self, value):
        while True: value = (value + 1) & 65535
'''
    with scoring_scope(ScoringPolicy(time_budget_s=60)):
        with _named_job_scope() if nested else nullcontext():
            with isolated.IsolatedModel(source, cls='M', strict_module=False,
                    limits=isolated.Limits(seconds=2, wall_seconds=10, memory_bytes=512 << 20)) as model:
                with pytest.raises(isolated.Broken):
                    model.process(value=0)
                assert model._child is None
