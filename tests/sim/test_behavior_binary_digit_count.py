"""Exact binary digit counts share integer primitives, not a string approximation."""

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.behavior.admission import check
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.goals import goal_set
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges


def _source(expression, *, declarations="", setup="pass", methods=""):
    return f'''{declarations}class M:
    def reset(self):
        {setup}
{methods}
    def process(self, a):
        return {{"y": {expression}}}
'''


def _compare(source, inputs, *, width=8, lowerer=None):
    proc = lower_with_state_ranges(
        source, input_widths=[width], outputs=["y"], lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    for a in inputs:
        model = namespace["M"]()
        model.reset()
        expected = model.process(a)
        _state, output, _hits, error = interp.run_step(proc, {"a": a})
        assert not error, (a, error)
        assert output.transactions == [expected], (a, output, expected)
    produced = {command.goal for command in ir.walk(proc.body)
                if isinstance(command, (ir.Mark, ir.Fail)) and command.goal}
    assert set(goal_set(source)) <= produced | set(proc.discharged)
    return proc


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
@pytest.mark.parametrize("digit", ["0", "1"])
@pytest.mark.parametrize("value", ["a - 128", "a == 7", "-(a << 250)"])
def test_binary_counts_agree_for_signed_zero_boolean_and_wide_values(digit, value, lowerer):
    _compare(_source(f'bin({value}).count("{digit}")'), range(256), lowerer=lowerer)


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
@pytest.mark.parametrize("digit", ["0", "1"])
def test_count_evaluates_stateful_argument_once(digit, lowerer):
    source = _source(
        f'bin(self.tick(a)).count("{digit}") + 100 * self.calls',
        setup="self.calls = 0", methods='''    def tick(self, a):
        self.calls += 1
        return a - 128
''')
    _compare(source, [0, 1, 127, 128, 255], lowerer=lowerer)


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
def test_binary_count_retains_scalar_contract_across_branches(lowerer):
    source = '''class M:
    def reset(self):
        pass
    def process(self, a):
        if a & 1:
            value = bin(a).count("1")
        else:
            value = bin(a).count("0")
        return {"y": value}
'''
    assert not check(source)
    _compare(source, range(256), lowerer=lowerer)


@pytest.mark.parametrize("declarations,methods,expression", [
    ("def bin(a):\n    return [0, 1]\n", "", 'bin(a).count("1")'),
    ("", "", '[bin(a).count("1") for bin in [0]][0]'),
    ("", "    def bin(self, a):\n        return a\n", 'self.bin(a).count("1")'),
])
def test_shadowed_or_nonbuiltin_formatting_is_not_reinterpreted(declarations, methods, expression):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(_source(expression, declarations=declarations, methods=methods),
                                input_widths=[8], outputs=["y"])


@pytest.mark.parametrize("expression", [
    'bin(a).count("1", 2)', 'bin(a).count(sub="1")', 'bin(number=a).count("1")',
    'bin(a).count("10")', 'bin(a).count(1)', 'bin(a).count("1" if a else "0")',
    'bin([a]).count("1")', 'bin(a).count()', 'bin(a)',
])
def test_other_string_operations_and_argument_forms_are_not_silently_rewritten(expression):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(_source(expression), input_widths=[8], outputs=["y"])


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("digit", ["0", "1"])
def test_binary_count_queries_and_source_goal_hits_agree_with_native(digit):
    from behave.sim.behavior.instance import BehaviorInstance

    source = f'''class M:
    def reset(self):
        pass
    def process(self, a):
        if bin(a - 128).count("{digit}") == 3:
            return {{"y": 1}}
        return {{"y": 0}}
'''
    proc = _compare(source, range(256))
    instance = BehaviorInstance(proc, proc.state_ranges, source=source)
    instance.bind(1)
    goals = set(instance.goals())
    verdict = instance.query(goals, 1)
    assert not verdict.undecided
    assert {g for g in goals if g.startswith("branch:")} <= set(verdict.reachable)
    assert instance.source_audit(tuple(verdict.reachable.values())).status.value == "agreed"


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("expression", ['bin(a).count("1")', 'bin(-a).count("1")'])
def test_wide_population_count_symbolic_witness_is_not_truncated(expression):
    from behave.sim.behavior.instance import BehaviorInstance

    source = f'''class M:
    def reset(self):
        pass
    def process(self, a):
        if {expression} == 255:
            return {{"y": 1}}
        return {{"y": 0}}
'''
    proc = _compare(source, [0, 1, 1 << 254, (1 << 255) - 1], width=255)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source)
    instance.bind(1)
    goals = {g for g in instance.goals() if g.startswith("branch:")}
    verdict = instance.query(goals, 1, timeout_ms=20_000)
    assert set(verdict.reachable) == goals
    assert instance.source_audit(tuple(verdict.reachable.values())).status.value == "agreed"


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("wrong", [False, True])
def test_public_search_binary_count_closes_native_hit_and_counterexample_loop(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self):
        pass
    def process(self, a, b):
        if bin(a ^ b).count("1") == 5:
            return {"y": 1}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        left = slot["inputs"]["left"]["a"]
        right = slot["inputs"]["right"]["b"]
        return {"result": self.process(left, right)}
'''
    candidate = source.replace(b'{"y": 1}', b'{"y": 2}') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)
