"""Local finite lookup tables retain the same contracts as module tables."""

import pytest

from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, LoweringError, lower_with_state_ranges


TABLES = [
    ('{0: [0, 1], 1: [2, 1], 2: [0, 3], 3: [2, 1]}',
     'table[self.state][a & 1]'),
    ('{0: {0: 0, 1: 1}, 1: {0: 2, 1: 1}, 2: {0: 0, 1: 3}, 3: {0: 2, 1: 1}}',
     'table[self.state].get(a & 1, 0)'),
    ('{0: 2, 1: 3, 0: 1}', 'table.get(a, 0)'),
    ('{0: [0, 1], 1: [2, 3]} if a & 1 else {0: [3, 2], 1: [1, 0]}',
     'table[(a >> 1) & 1][a & 1]'),
]


def model(table, read, mutation=''):
    return f'''class M:
    def reset(self): self.state = 0
    def process(self, a):
        table = {table}
{mutation}        self.state = {read}
        return {{"y": self.state}}
'''


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('table,read', TABLES)
def test_local_table_state_contract_matches_native(lowerer, table, read):
    source = model(table, read)
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    traces = [[{'a': a} for a in values]
              for values in ((0, 1, 1, 0), (3, 2, 7, 1), (1, 0, 1, 1))]
    assert model_audit(proc, namespace['M'], traces) == []


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_table_mutation_cannot_reuse_literal_value(lowerer):
    source = model('{0: 1, 1: 2}', 'table[0]', '        table[0] = a\n')
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                bound=4, lowerer=lowerer)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('selected', [
    '{0: (1, 2), 1: (3, 4)}[a & 1]',
    '{0: (1, 2)}.get(a & 1, (3, 4))',
])
def test_lookup_tuple_and_computed_tuple_share_one_state_layout(lowerer, selected):
    source = f'''class M:
    def reset(self):
        self.phase = 0
        self.data = (0, 0)
    def process(self, a):
        before = self.data[0] + self.data[1]
        if self.phase == 0:
            result = (a, 0)
        else:
            row = {selected}
            result = row
        self.phase = 1 - self.phase
        self.data = result
        return {{"y": before}}
'''
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    traces = [[{'a': a} for a in values]
              for values in ((0, 1, 2, 3), (7, 6, 1, 0), (3, 3, 3, 3))]
    assert model_audit(proc, namespace['M'], traces) == []


@pytest.mark.isolation
@pytest.mark.solver
def test_local_table_goals_query_and_replay():
    from behave.sim.behavior.instance import BehaviorInstance

    source = model(*TABLES[0])
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=4)
    instance.bind(4)
    result = instance.query(instance.goals(), 4)
    assert result.reachable and not result.undecided
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'
