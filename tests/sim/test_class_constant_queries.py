"""Class-qualified constants use the same lexical declaration as self reads."""

import pytest

from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges


SOURCE = '''class M:
    IDLE, RUN = 0, 3
    WIDTH = 2 + 1
    YES = True
    def reset(self): self.state = M.IDLE
    def process(self, a):
        old = self.state
        self.state = M.RUN if a == 5 else M.IDLE
        return {"y": old + int(M.YES)}
'''

SOURCES = [
    SOURCE,
    SOURCE.replace('M.', 'self.'),
    SOURCE.replace('IDLE, RUN = 0, 3', 'IDLE, (RUN, UNUSED) = 0, (3, 7)').replace('M.', 'self.'),
    SOURCE.replace('IDLE, RUN = 0, 3', 'IDLE, RUN = FIRST, LAST = 0, 3').replace('M.', 'self.'),
    SOURCE.replace('IDLE, RUN = 0, 3', 'IDLE, RUN = 3, 0\n    IDLE, RUN = RUN, IDLE').replace('M.', 'self.'),
    SOURCE.replace('        old = self.state',
                   '        old = self.offset() + self.state')
    .replace('    def reset(self)', '    def offset(self, n=RUN): return n\n    def reset(self)'),
    SOURCE.replace('M.RUN if', 'Alias.RUN if') + '\nAlias = M\n',
    SOURCE.replace('M.RUN if', 'Saved.RUN if')
    + '\nAlias = M\nSaved = Alias\nAlias = 9\n',
    SOURCE.replace('        old = self.state',
                   '        self.RUN = a\n        old = self.state'),
    SOURCE.replace('        old = self.state',
                   '        old = sum([i for i in range(M.WIDTH)]) + self.state'),
    SOURCE.replace('        old = self.state',
                   '        def helper(): return M.RUN\n        old = helper() + self.state'),
]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('source', SOURCES)
def test_qualified_constants_preserve_native_state_and_outputs(lowerer, source):
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    traces = [[{'a': a} for a in values]
              for values in ((0, 5, 7, 5), (5, 5, 5, 0), (1, 2, 3, 4))]
    assert model_audit(proc, namespace['M'], traces) == []


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('source', [
    SOURCE + '\nM.RUN = 9\n',
    SOURCE + '\nAlias = M\nAlias.RUN = 9\n',
    SOURCE.replace('M.RUN if', 'Alias.RUN if') + '\nAlias = M\nAlias = 9\n',
    SOURCE.replace('        old = self.state', '        M = a\n        old = self.state'),
    SOURCE.replace('        old = self.state', '        M.RUN = a\n        old = self.state'),
    SOURCE.replace('        old = self.state', '        M.RUN += a\n        old = self.state'),
    SOURCE.replace('        old = self.state',
                   '        M.RUN = other = a\n        old = self.state'),
    SOURCE.replace('        old = self.state',
                   '        def helper(): M.RUN = a\n        helper()\n        old = self.state'),
    SOURCE.replace('def reset(self): self.state = M.IDLE',
                   'def reset(self): M.RUN = 6; self.state = M.IDLE'),
    SOURCE.replace('        old = self.state',
                   '        Alias.RUN = a\n        old = self.state') + '\nAlias = M\n',
    SOURCE.replace('        old = self.state',
                   '        old = sum([M.RUN for M in [a]]) + self.state'),
    SOURCE.replace('        old = self.state',
                   '        def helper(M): return M.RUN\n        old = helper(a)'),
])
def test_rebound_or_mutated_class_is_not_the_closed_namespace(lowerer, source):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                bound=4, lowerer=lowerer)


@pytest.mark.isolation
@pytest.mark.solver
def test_class_constants_all_goal_queries_agree_with_native():
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(SOURCE, input_widths=[3], outputs=['y'], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=SOURCE, bound=4)
    instance.bind(4)
    result = instance.query(instance.goals(), 4)
    assert result.reachable and not result.undecided
    assert instance.source_audit(tuple(result.reachable.values())).status.value == 'agreed'


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_class_constant_public_search_loop(tmp_path, wrong):
    from tests.sim.test_behavior_collection_membership import check_public_search_loop

    source = b'''class M:
    MAGIC = 37
    def reset(self): pass
    def process(self, a, b):
        if a == M.MAGIC:
            return {"y": 7}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        return {"result": self.process(slot["inputs"]["left"]["a"],
                                       slot["inputs"]["right"]["b"])}
'''
    candidate = source.replace(b'"y": 7', b'"y": 6') if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)
