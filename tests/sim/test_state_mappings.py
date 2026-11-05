"""Integer-key state maps keep source values, errors and event identities."""

import ast
import itertools

import pytest

from behave.sim.behavior import audit, instrumented, lower_mappings
from behave.sim.symbolic import interp, ir
from behave.sim.behavior.extract import LoweringError, ScopeResolver, source_model
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from behave.sim.behavior.source_sequences import plan_sequences
from behave.sim.behavior.source_values import (
    SCALAR,
    ValueContract,
    contract_contains,
    contract_of_value,
    contracts_may_overlap,
    join_contracts,
)
from behave.sim.symbolic.state_projection import SourceProductBinding, StateProjectionError

SOURCE = '''class M:
    def reset(self): self.data = {}
    def process(self, a):
        op = a & 3
        key = a >> 2
        value = self.data.get(key, 9)
        if op == 0: self.data[key] = key + 1
        elif op == 1:
            if key in self.data: value = self.data.pop(key)
        elif op == 2:
            if key in self.data:
                value = self.data[key]
                del self.data[key]
        else: self.data.clear()
        return {"y": value + 16 * len(self.data)}
'''

CASES = [
    ("operations", SOURCE),
    ("replace", SOURCE.replace("self.data.clear()", "self.data = {}")),
    ("initial", SOURCE.replace("self.data = {}", "self.data = {2: 4}")),
    ("pop-default", SOURCE.replace("if key in self.data: value = self.data.pop(key)",
                                    "value = self.data.pop(key, 7)")),
    ("helper", SOURCE.replace("    def process(self, a):", """    def put(self, k, v): self.data[k] = v
    def process(self, a):""").replace("self.data[key] = key + 1", "self.put(key, key + 1)")),
    ("overwrite-order", '''class M:
    def reset(self): self.data = {2: 5, 0: 8}
    def process(self, a):
        key = a >> 2
        if a & 1: self.data.pop(key, 0)
        self.data[key] = a
        return {"y": self.data[key]}
'''),
    ("optional-get", '''class M:
    def reset(self): self.data = {}
    def process(self, a):
        key = a >> 2
        value = self.data.get(key)
        self.data[key] = a
        if value is None: return {"y": 9}
        return {"y": value}
'''),
    ("augmented-update", '''class M:
    def reset(self): self.data = {0: 1, 1: 2}
    def process(self, a):
        self.data[a >> 2] += a
        return {"y": self.data[a >> 2]}
'''),
    ("clear-only", '''class M:
    def reset(self): self.data = {0: 7}
    def process(self, a):
        if a & 1: self.data.clear()
        return {"y": self.data.get(a >> 1, 3)}
'''),
    ("remove-reinsert", '''class M:
    def reset(self): self.data = {0: 7, 1: 9}
    def process(self, a):
        key = a >> 2
        self.data[key] = self.data.pop(key, 0) + a
        return {"y": self.data[key] + 16 * len(self.data)}
'''),
    ("multiple-delete", '''class M:
    def reset(self): self.data = {}
    def process(self, a):
        self.data[a] = a
        self.data[a + 1] = 0
        del self.data[a], self.data[a + 1]
        return {"y": len(self.data)}
'''),
]


def histories():
    return ([{"a": a} for a in values]
            for values in itertools.product(range(8), repeat=3))


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
@pytest.mark.parametrize("name,source", CASES, ids=[name for name, _ in CASES])
def test_mapping_values_and_state(name, source, lowerer):
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=["y"], bound=3,
                                   lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102 -- fixed fixture
    assert audit.model_audit(proc, namespace["M"], histories()) == [], name


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("name,source", CASES, ids=[name for name, _ in CASES])
def test_mapping_native_events_and_queries(name, source):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(source, input_widths=[3], outputs=["y"], bound=3)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=3)
    plan = plan_source(source_model(source), source)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    expected = set()
    for history in histories():
        native = namespace["M"]()
        native.reset()
        state = dict(proc.initial)
        for inputs in history:
            hits.clear()
            native.process(**inputs)
            expected.update(hits)
            state, _, ir_hits, error = interp.run_step(proc, inputs, state)
            assert not error
            assert set(ir_hits) == set(hits), (name, history)
    result = instance.query(instance.goals(), 3)
    assert not result.undecided
    assert set(result.reachable) == expected
    assert set(instance.goals()) == set(result.reachable) | set(result.unreachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("operation", ["return {'y': self.data[a]}",
                                        "return {'y': self.data.pop(a)}",
                                        "del self.data[a]\n        return {'y': 0}",
                                        "self.data[a] += 1\n        return {'y': 0}"])
def test_missing_mapping_key_is_a_native_queryable_failure(operation):
    from behave.sim.behavior.instance import BehaviorInstance

    source = f'''class M:
    def reset(self): self.data = {{0: 7}}
    def process(self, a):
        {operation}
'''
    proc = lower_with_state_ranges(source, input_widths=[1], outputs=["y"], bound=1)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=1)
    result = instance.query(instance.goals(), 1)
    assert not result.undecided
    failures = {g for g in result.reachable if g.endswith(":indexerror")}
    assert failures
    _, _, hits, error = interp.run_step(proc, {"a": 1})
    assert error == "KeyError" and failures <= set(hits)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"


@pytest.mark.isolation
@pytest.mark.solver
def test_mapping_default_is_evaluated_even_for_a_present_key():
    from behave.sim.behavior.instance import BehaviorInstance

    source = '''class M:
    def reset(self): self.data = {}
    def process(self, a):
        self.data[0] = 7
        return {"y": self.data.get(0, 1 // a)}
'''
    proc = lower_with_state_ranges(source, input_widths=[1], outputs=["y"], bound=1)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=1)
    result = instance.query(instance.goals(), 1)
    assert any(g.endswith(":divzero") for g in result.reachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"


@pytest.mark.parametrize("body", [
    "alias = self.data\n        alias[a] = 2",
    "self.other = self.data",
    "self.data[self.mutate()] = 1",
    "result = self.data.get(a, self.mutate())",
    "result = self.data == self.other",
    "result = self.data + self.other",
])
def test_unrepresented_mapping_identity_is_not_copied(body):
    source = f'''class M:
    def reset(self): self.data = {{}}; self.other = {{}}
    def mutate(self): self.data = {{}}; return 0
    def process(self, a):
        {body}
        return {{"y": 0}}
'''
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[1], outputs=["y"], bound=2)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("wrong", [False, True])
def test_public_residual_search_map_witness(tmp_path, wrong):
    from .test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    def reset(self): self.data = {}
    def process(self, a, b):
        self.data[a] = b
        if 3 in self.data: return {"y": self.data[3] + 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}: return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'+ 7', b'+ 8') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


def test_mapping_projection_keeps_order_and_ignores_dead_padding():
    contract = ValueContract.mapping(0, 3, SCALAR)
    binding = SourceProductBinding("data", contract, (
        (("length",), "n"), (("keys",), "keys"), (("values",), "values")))
    source = {-(1 << 80): 6, 5: 19}
    state = binding.solver_values(source)
    state["keys"][-1] = 5
    state["values"][-1] = 999
    assert list(binding.reconstruct(state).items()) == list(source.items())
    assert binding.equivalent(binding.reconstruct(state), source)
    assert binding.reconstruct({**state, "n": 0}) == {}
    assert not binding.equivalent(source, dict(reversed(tuple(source.items()))))
    with pytest.raises(StateProjectionError, match="duplicate"):
        binding.reconstruct({**state, "n": 3})
    with pytest.raises(StateProjectionError, match="outside"):
        binding.reconstruct({**state, "n": 4})


def test_mapping_contract_lattice_and_numeric_key_domain_are_separate():
    empty = contract_of_value({})
    one = contract_of_value({1 << 80: 4})
    joined = join_contracts(empty, one)
    assert joined == ValueContract.mapping(0, 1, SCALAR)
    assert contract_contains(joined, empty) and contract_contains(joined, one)
    assert not contract_contains(empty, one)
    assert contracts_may_overlap(joined, one)
    assert not contracts_may_overlap(empty, one)
    assert joined.physical_shape is None  # a product, not an address-space array


@pytest.mark.parametrize("condition", ["len(self.data) < 2", "count < 2"])
def test_source_cardinality_guard_limits_storage_not_numeric_key_space(condition):
    source = f'''class M:
    def reset(self): self.data = {{}}
    def process(self, a):
        count = len(self.data)
        if {condition}: self.data[a] = a + 1
        return {{"y": self.data.get(a, 0)}}
'''
    proc = lower_with_state_ranges(source, input_widths=[80], outputs=["y"], bound=12)
    assert proc.contract.projection("data").contract.maximum == 2
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102 -- fixed fixture
    assert audit.model_audit(proc, namespace["M"], [
        [{"a": a} for a in (2**70, 3, 9, 2**70, 3, 9)],
    ]) == []


@pytest.mark.parametrize('limit', [1, 3])
@pytest.mark.parametrize('helper', [False, True])
@pytest.mark.parametrize('drain', ['clear', 'replace', 'delete'])
def test_mapping_peak_storage_does_not_widen_boundary_state(limit, helper, drain):
    fill = 'self.fill(a)' if helper else f'for i in range({limit}): self.data[a + i] = i'
    empty = {'clear': 'self.data.clear()', 'replace': 'self.data = {}',
             'delete': f'for i in range({limit}): del self.data[a + i]'}[drain]
    source = f'''class M:
    def reset(self): self.data = {{}}
    def fill(self, a):
        for i in range({limit}): self.data[a + i] = i
    def process(self, a):
        {fill}
        {empty}
        return {{"y": len(self.data)}}
'''
    model = source_model(source)
    plan = plan_sequences(model, ScopeResolver(model), ('y',),
                          declaration_contracts={'data': contract_of_value({})},
                          input_contracts={'a': SCALAR}, state_bound=3)
    assert plan.state_contracts['data'].maximum == 0
    assert plan.state_storage_contracts['data'].maximum == limit
    namespace = {}
    exec(source, namespace)
    traces = [[{'a': a} for a in values] for values in ((0, 1, 0), (7, 7, 7))]
    for lowerer in (_Lowerer, GraphLowerer):
        proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                      bound=3, lowerer=lowerer)
        assert audit.model_audit(proc, namespace['M'], traces) == []


@pytest.fixture(params=[_Lowerer, GraphLowerer], ids=['term', 'graph'])
def map_storage(request, monkeypatch):
    low = request.param([])
    node = ast.parse('self.data', mode='eval').body
    builder = lower_mappings.MappingBuilder.allocate(
        low, ValueContract.mapping(0, 3, SCALAR), node)
    commands = []
    monkeypatch.setattr(low, 'before', commands.append)

    def evaluate(source):
        if isinstance(source, ast.Attribute) and ast.dump(source) == ast.dump(node):
            return builder
        assert isinstance(source, ast.Constant), ast.dump(source)
        return ir.Lit(source.value)

    monkeypatch.setattr(low, 'expr', evaluate)
    return low, builder, commands


@pytest.mark.parametrize('key', [0, 1, 2])
@pytest.mark.parametrize('method', ['get', 'pop'])
def test_map_storage_preserves_zero_snapshot_and_dead_padding(map_storage, key, method):
    low, builder, commands = map_storage
    node = ast.parse(f'self.data.{method}({key}' + (', 17)' if method == 'pop' else ')'),
                     mode='eval').body
    result = builder.call(low, node)
    machine = interp._Interp({builder.count: 2, builder.keys: [0, 1, 2],
                              builder.values: [0, 8, 900]})
    hits = set()
    for command in commands:
        machine.run(command, hits)
    assert not hits
    if method == 'get':
        assert machine.value(result.present) is (key != 2)
        if key != 2:
            assert machine.value(result.payload) == (0 if key == 0 else 8)
        assert machine.rho[builder.count] == 2
    else:
        assert machine.value(result) == (0 if key == 0 else 8 if key == 1 else 17)
        count = machine.rho[builder.count]
        assert count == (2 if key == 2 else 1)
        expected = [(k, v) for k, v in [(0, 0), (1, 8)] if k != key]
        assert list(zip(machine.rho[builder.keys][:count],
                        machine.rho[builder.values][:count])) == expected


@pytest.mark.parametrize('key', [0, 3])
def test_map_store_snapshots_rhs_before_receiver_and_enforces_capacity(map_storage, monkeypatch, key):
    low, builder, commands = map_storage
    target = ast.parse(f'self.data[{key}]', mode='eval').body
    source = ast.parse('rhs', mode='eval').body
    evaluate = low.expr

    def receiver_effect(node):
        if node is target.value:
            low.before(ir.Assign('rhs', ir.Lit(99)))
        return evaluate(node)

    monkeypatch.setattr(low, 'expr', receiver_effect)
    command = builder.store(low, target, ir.Var('rhs'), source)
    machine = interp._Interp({builder.count: 3, builder.keys: [0, 1, 2],
                              builder.values: [0, 8, 7], 'rhs': 5})
    hits = set()

    def run():
        for entry in (*commands, command):
            machine.run(entry, hits)

    if key == 3:
        with pytest.raises(interp.Halt, match='CapacityExceeded'):
            run()
        assert hits == {low._safety_goal(target, 'capacity')}
        assert machine.rho[builder.values] == [0, 8, 7]
    else:
        run()
        assert not hits
        assert machine.rho[builder.values] == [5, 8, 7]
        assert machine.rho[builder.keys] == [0, 1, 2]
    assert machine.rho[builder.count] == 3
