"""Bounded state closure retains a flag's relation to an optional payload."""

from itertools import product

import pytest

from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges


PACKER = '''class M:
    def reset(self):
        self.pending = False
        self.byte = None
    def process(self, a):
        if a == 7:
            return {"y": 0}
        if not self.pending:
            self.byte = a
            self.pending = True
            return {"y": 0}
        word = (self.byte << 3) | a
        self.pending = False
        self.byte = None
        return {"y": word}
'''

SOURCES = [
    PACKER,
    PACKER.replace('False', '0').replace('True', '1'),
    PACKER.replace('self.byte = a', 'self.byte = [a, a + 1]')
          .replace('(self.byte << 3)', '(self.byte[0] << 3)'),
    PACKER.replace('self.byte = a', 'self.byte = {"v": a}')
          .replace('(self.byte << 3)', '(self.byte["v"] << 3)'),
    PACKER.replace('False', '2').replace('True', '5')
          .replace('if not self.pending:', 'if self.pending == 2:'),
    PACKER.replace('self.byte', 'self.a'),
    PACKER.replace('        self.pending = False\n        self.byte = None\n        return',
                   '        self.clear()\n        return')
          + '    def clear(self):\n        self.pending = False\n        self.byte = None\n',
]

HELPER_STATUS = '''class M:
    def reset(self):
        self.left = None
        self.right = None
    def status(self):
        return {"y": (1 if self.left is None else 0) + 2 * (1 if self.right is None else 0)}
    def process(self, a):
        out = self.status()
        if a & 1:
            self.left = a
        else:
            self.left = None
        if a & 2:
            self.right = a
        else:
            self.right = None
        return out
'''

# One call occurrence is reached from four source-state alternatives. None
# may overwrite the helper context for the others.
SOURCES.append(HELPER_STATUS)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('source', SOURCES)
def test_bounded_state_presence_matches_native_history(lowerer, source):
    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                   bound=4, lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)
    traces = [[{'a': a} for a in values] for values in product((0, 1, 2, 7), repeat=4)]
    findings = model_audit(proc, namespace['M'], traces)
    assert not findings, findings[:5]


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('source', [
    PACKER.replace('self.pending = False\n        self.byte = None',
                   'self.pending = True\n        self.byte = None', 1),
    PACKER.replace('        word =', '        self.byte = None\n        word ='),
    PACKER.replace('            self.byte = a', '            self.byte = None'),
    PACKER.replace('        self.pending = False\n        self.byte = None\n        return',
                   '        self.byte = None\n        return'),
    PACKER.replace('        word =', '        self.clear()\n        word =')
          + '    def clear(self):\n        self.byte = None\n',
])
def test_absent_payload_is_not_assumed_present(lowerer, source):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[3], outputs=['y'],
                                bound=4, lowerer=lowerer)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('source', SOURCES)
def test_all_goals_query_and_replay_across_calls(source):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = lower_with_state_ranges(source, input_widths=[3], outputs=['y'], bound=4)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=4)
    instance.bind(4)
    verdict = instance.query(instance.goals(), 4)
    assert verdict.reachable and not verdict.undecided
    audit = instance.source_audit(tuple(verdict.reachable.values()))
    assert audit.status.value == 'agreed', audit.as_dict()


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
def test_other_history_can_break_presence_after_initial_success(lowerer):
    # The first four calls are safe. On call five an unrelated state transition
    # can create pending=True with no byte; a bounded proof must not reuse the
    # earlier prefix as an invariant for that longer query.
    source = PACKER.replace('        self.pending = False\n        self.byte = None',
                            '        self.pending = False\n        self.byte = None\n'
                            '        self.calls = 0', 1)
    source = source.replace('    def process(self, a):',
                            '    def process(self, a):\n        self.calls += 1\n'
                            '        if self.calls == 5:\n'
                            '            self.pending = True\n            self.byte = None')
    lower_with_state_ranges(source, input_widths=[3], outputs=['y'], bound=4, lowerer=lowerer)
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[3], outputs=['y'], bound=6, lowerer=lowerer)
