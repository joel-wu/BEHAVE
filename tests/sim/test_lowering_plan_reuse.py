"""Reuse one source representation only for an identical lowering request."""

import ast
from itertools import product

import pytest

from behave.sim.behavior import extract, lower_prepare, lowering
from behave.sim.symbolic import interp, ranges
from behave.sim.behavior.construction import CONSTRUCTION_VERSION, ConstructionFacts
from behave.sim.behavior.extract import ScopeResolver
from behave.sim.behavior.native_values import DEFAULT_WIRE_NODES, WireEncoder
from behave.sim.behavior.source_values import SCALAR, SourceValueBudget, ValueContract


def test_exhausted_source_plan_is_not_retried_with_a_fresh_quota(monkeypatch):
    from behave.sim.behavior.extract import LoweringError

    calls = []
    failure = LoweringError(ast.parse('pass'), 'planning exhausted',
                            code='source-plan-budget-exceeded')

    def exhausted(*args, **kwargs):
        calls.append((args, kwargs))
        raise failure

    monkeypatch.setattr(lowering, 'lower_procedure', exhausted)
    with pytest.raises(LoweringError) as result:
        lowering.lower_source('class M:\n    def process(self, a):\n'
                              '        return {"y": a}\n', input_widths=[2], bound=2)
    assert result.value is failure
    assert len(calls) == 1


def _facts(values):
    return ConstructionFacts.from_dict({
        "version": CONSTRUCTION_VERSION,
        "values": WireEncoder(DEFAULT_WIRE_NODES).encode(values),
        "unsupported": [], "shared_pairs": [], "opaque_fields": [], "boundary_fields": []})


SOURCES = (
    ('''class M:
    def reset(self):
        pass
    def helper(self, a):
        rows = []
        for i in range(a):
            rows.append({"y": i + 1})
        return rows
    def process(self, a):
        return self.helper(a)
''', {}),
    ('''class M:
    def reset(self):
        self.count = 0
    def process(self, a):
        self.count = (self.count + a) % 4
        if self.count == 3:
            return [{"y": self.count}, {"y": a}]
        return []
''', {"count": 0}),
    ('''class M:
    def reset(self):
        self.table = [2, 4, 6, 8]
    def process(self, a):
        old = self.table[a]
        self.table[a] = a
        return {"y": old}
''', {"table": [2, 4, 6, 8]}),
)


@pytest.mark.parametrize("source,initial", SOURCES, ids=("helper-batch", "stateful", "array"))
def test_two_value_lowerings_share_one_plan_and_match_independent_source(source, initial, monkeypatch):
    planned, model_trees, lowered = [], [], []
    sequence = lower_prepare.plan_sequences
    lower = lowering.lower_procedure

    def record_plan(model, *args, **kwargs):
        planned.append(kwargs["input_ranges"])
        model_trees.append(model.tree)
        return sequence(model, *args, **kwargs)

    def record_lower(*args, **kwargs):
        result = lower(*args, **kwargs)
        lowered.append(result)
        return result

    monkeypatch.setattr(lower_prepare, "plan_sequences", record_plan)
    monkeypatch.setattr(lowering, "lower_procedure", record_lower)
    result = lowering.lower_source(source, input_bounds={"a": (0, 3)}, bound=3,
                                    construction=_facts(initial))
    assert len(lowered) == 2  # The informed IR pass was not skipped.
    assert len(planned) == 1
    original_tree = ast.dump(model_trees[0], include_attributes=True)
    namespace = {}
    exec(compile(source, "reuse_oracle.py", "exec"), namespace)
    for inputs in product(range(4), repeat=3):
        native = namespace["M"]()
        native.reset()
        state = None
        for value in inputs:
            expected = native.process(value)
            state, outputs, _hits, error = interp.run_step(result.proc, {"a": value}, state)
            assert not error
            assert outputs.transactions == (expected if isinstance(expected, list) else [expected])
            for name in initial:
                assert state[name] == getattr(native, name)
    assert ast.dump(model_trees[0], include_attributes=True) == original_tree


def test_reuse_and_fresh_planning_preserve_goals_hits_failures_and_state(monkeypatch):
    source = '''class M:
    def reset(self):
        self.x = 0
    def process(self, a):
        self.x = self.x + 1
        return {"y": 10 // a}
'''
    kwargs = {"input_bounds": {"a": (0, 3)}, "bound": 3, "construction": _facts({"x": 0})}
    shared = lowering.lower_source(source, **kwargs)
    cache = lower_prepare.PreparationCache

    class FreshPlanning(cache):
        def source_model(self, source, file, method, cls, budget):
            return lower_prepare.source_model(source, file, step=method, cls=cls,
                                          source_value_budget=budget)

        def plan_sequences(self, model, resolver, outputs, *, dynamic_state, budget, **facts):
            return lower_prepare.plan_sequences(model, resolver, outputs, budget=budget, **facts)

    monkeypatch.setattr(lower_prepare, "PreparationCache", FreshPlanning)
    fresh = lowering.lower_source(source, **kwargs)
    assert shared.catalogue == fresh.catalogue
    for values in product(range(4), repeat=3):
        left = right = None
        for value in values:
            left, output_l, hits_l, error_l = interp.run_step(shared.proc, {"a": value}, left)
            right, output_r, hits_r, error_r = interp.run_step(fresh.proc, {"a": value}, right)
            assert (left, output_l.transactions, hits_l, error_l) == (
                right, output_r.transactions, hits_r, error_r)
            if error_l:
                assert error_l == "DivZero"
                break


def test_changed_external_state_range_replans_and_does_not_fold_to_reset(monkeypatch):
    source = '''class M:
    def reset(self):
        self.limit = 0
    def process(self):
        count = 0
        for i in range(self.limit + 1):
            count = count + 1
        return {"y": count}
'''
    cache = lower_prepare.PreparationCache()
    sequence, calls = lower_prepare.plan_sequences, []

    def measured(*args, **kwargs):
        calls.append(dict(kwargs["input_ranges"]))
        return sequence(*args, **kwargs)

    monkeypatch.setattr(lower_prepare, "plan_sequences", measured)
    first = lowering.lower_procedure(source, outputs=("y",), bound=1,
        state_ranges={"limit": ranges.Range(0, 1)}, dynamic_state=("limit",),
        construction=_facts({"limit": 0}), _analysis_cache=cache)
    second = lowering.lower_procedure(source, outputs=("y",), bound=1,
        state_ranges={"limit": ranges.Range(0, 3)}, dynamic_state=("limit",),
        construction=_facts({"limit": 0}), _analysis_cache=cache)
    assert calls == [{"self.limit": ranges.Range(0, 1)},
                     {"self.limit": ranges.Range(0, 3)}]
    assert interp.run_step(first, {}, {"limit": 1})[1].transactions == [{"y": 2}]
    assert interp.run_step(second, {}, {"limit": 3})[1].transactions == [{"y": 4}]


def test_dependency_helpers_keep_the_same_source_occurrences_across_passes(monkeypatch):
    source = '''from fp_lib import fp32_is_nan
class M:
    def reset(self):
        pass
    def process(self, a):
        if fp32_is_nan(a):
            return {"y": 1}
        return {"y": 0}
'''
    library, sequence = extract._library_tree, lower_prepare.plan_sequences
    libraries, plans = [], []

    def parsed():
        result = library()
        libraries.append(result)
        return result

    def planned(*args, **kwargs):
        result = sequence(*args, **kwargs)
        plans.append(result)
        return result

    monkeypatch.setattr(extract, "_library_tree", parsed)
    monkeypatch.setattr(lower_prepare, "plan_sequences", planned)
    result = lowering.lower_source(source, input_bounds={"a": (0, 2**32 - 1)}, bound=2,
                                    construction=_facts({}))
    assert len(libraries) == len(plans) == 1
    for value in (0, 0x80000000, 1, 0x3F800000, 0x7F800000, 0xFF800000,
                  0x7FC00000, 0x7F800001, 0xFFFFFFFF):
        expected = int((value & 0x7F800000) == 0x7F800000 and (value & 0x7FFFFF) != 0)
        _state, outputs, _hits, error = interp.run_step(result.proc, {"a": value})
        assert not error and outputs.transactions == [{"y": expected}]


@pytest.mark.parametrize("change", (
    "input_ranges", "state_bound", "declarations", "declaration_contracts",
    "input_contracts", "declared_output_contracts", "dynamic_state", "outputs", "source"))
def test_plan_reuse_compares_all_request_facts_without_a_persistent_cache(monkeypatch, change):
    cache = lower_prepare.PreparationCache()
    model = cache.source_model(SOURCES[0][0], "model.py", "process", None, SourceValueBudget())
    resolver = ScopeResolver(model)
    plans = []

    def planned(*args, **kwargs):
        plans.append(object())
        return plans[-1]

    monkeypatch.setattr(lower_prepare, "plan_sequences", planned)
    facts = dict(input_ranges={"a": ranges.Range(0, 3)}, state_bound=3,
        declarations={"size": 3}, declaration_contracts={"size": SCALAR},
        input_contracts={"a": SCALAR}, declared_output_contracts={"y": SCALAR})
    first = cache.plan_sequences(model, resolver, ("y",), dynamic_state=(),
                                  budget=SourceValueBudget(), **facts)
    again = cache.plan_sequences(model, ScopeResolver(model), ("y",), dynamic_state=(),
                                  budget=SourceValueBudget(), **facts)
    assert again is first and len(plans) == 1
    dynamic, outputs = (), ("y",)
    if change == "dynamic_state":
        dynamic = ("size",)
    elif change == "outputs":
        outputs = ("z",)
    elif change == "source":
        model = cache.source_model(SOURCES[1][0], "model.py", "process", None, SourceValueBudget())
    else:
        alternatives = dict(input_ranges={"a": ranges.Range(0, 4)}, state_bound=4,
            declarations={"size": 4}, declaration_contracts={"size": ValueContract.fixed((2,))},
            input_contracts={"a": ValueContract.fixed((2,))},
            declared_output_contracts={"y": ValueContract.fixed((2,))})
        facts = {**facts, change: alternatives[change]}
    changed = cache.plan_sequences(model, ScopeResolver(model), outputs, dynamic_state=dynamic,
                                    budget=SourceValueBudget(), **facts)
    assert changed is not first and len(plans) == 2


def test_separate_lowering_calls_do_not_reuse_a_previous_plan(monkeypatch):
    sequence, calls = lower_prepare.plan_sequences, []

    def measured(model, *args, **kwargs):
        calls.append(model)
        return sequence(model, *args, **kwargs)

    monkeypatch.setattr(lower_prepare, "plan_sequences", measured)
    for _ in range(2):
        lowering.lower_source(SOURCES[0][0], input_bounds={"a": (0, 3)}, bound=3,
                               construction=_facts({}))
    assert len(calls) == 2 and calls[0] is not calls[1]


def test_failed_plan_is_not_retained_as_a_result(monkeypatch):
    cache = lower_prepare.PreparationCache()
    model = cache.source_model(SOURCES[0][0], "model.py", "process", None, SourceValueBudget())
    attempts = []

    def fails_then_succeeds(*args, **kwargs):
        attempts.append(None)
        if len(attempts) == 1:
            raise lower_prepare.SequencePlanError(model.step, "diagnostic failure", "diagnostic")
        return object()

    monkeypatch.setattr(lower_prepare, "plan_sequences", fails_then_succeeds)
    with pytest.raises(lower_prepare.SequencePlanError):
        cache.plan_sequences(model, ScopeResolver(model), (), dynamic_state=(), budget=SourceValueBudget())
    assert cache.plan_sequences(model, ScopeResolver(model), (), dynamic_state=(), budget=SourceValueBudget())
    assert len(attempts) == 2
