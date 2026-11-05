"""Iterator-held objects survive rebinding, in both executable lowerings."""

import pytest

from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from .test_iterable_products import (
    _assert_outputs_and_every_goal, _assert_public_goal_queries, _source,
)

pytestmark = pytest.mark.solver


REBIND_BODIES = [
    """        rows = [1, 2]
        total = 0
        for x in rows:
            total += x
            rows[1] = 3
            rows = [5, 6]
        return {'y': total}
""",
    """        rows = [1, 2]
        total = 0
        for x in rows:
            total += x
            rows = [5, 6]
            rows[1] = 7
        return {'y': total}
""",
    """        rows = [1, 2, 3]
        total = 0
        for x in rows:
            total += x
            rows.pop()
            rows = [5, 6, 7]
        return {'y': total}
""",
    """        rows = [1, 2]
        total = 0
        for x in rows:
            total += x
            if a & 1:
                rows[1] = 3
                rows = [5, 6]
            else:
                rows = rows
                rows[1] = 4
        return {'y': total}
""",
    """        rows = [1, 2]
        total = 0
        for x in rows:
            total += x
            rows.append(3)
            rows = [5, 6]
        return {'y': total}
""",
]

SOURCES = [_source(body.replace('for x in rows:', header))
           for body in REBIND_BODIES
           for header in ('for x in rows:', 'for x in reversed(rows):',
                          'for i, x in enumerate(rows, a):')]
SOURCES += [
    """class M:
    def reset(self):
        self.rows = [1, 2]
    def replace(self):
        self.rows[1] = 3
        self.rows = [5, 6]
        return 0
    def process(self, a):
        total = 0
        for x in self.rows:
            total += x
            self.replace()
        return {'y': total}
""",
    """class M:
    def reset(self):
        self.rows = [1, 2]
    def start(self):
        self.rows = [5, 6]
        return 0
    def process(self, a):
        total = 0
        for i, x in enumerate(self.rows, self.start()):
            total += x
        return {'y': total}
""",
    _source("""        rows = [1, 2]
        total = 0
        for x in rows:
            rows[1] = 3
            for y in rows:
                total += x + y
                rows = [5, 6]
                rows[1] = 7
        return {'y': total}
"""),
    _source("""        rows = [1, 2]
        old = rows
        total = 0
        for x in rows:
            total += x
            rows = [5, 6]
        else:
            old[1] = a
        return {'y': total + old[1]}
"""),
    _source("""        rows = [1, 2]
        old = rows
        total = 0
        for x in rows:
            total += x
            rows = [5, 6]
            rows.append(7)
        return {'y': total + old[0]}
"""),
    _source("""        rows = [1, 2]
        total = 0
        for x in rows:
            total += x
            rows, unused = ([5, 6], a)
            rows[1] = 7
        return {'y': total}
"""),
]


@pytest.mark.parametrize('source', SOURCES)
@pytest.mark.parametrize('lowerer', (GraphLowerer, _Lowerer))
def test_held_iterator_outputs_and_hits(source, lowerer):
    _assert_outputs_and_every_goal(source, lowerer)


@pytest.mark.parametrize('source', SOURCES)
def test_held_iterator_public_goal_queries(source):
    _assert_public_goal_queries(source)


@pytest.mark.parametrize('body', [
    """        rows = [1, 2]
        original = rows
        total = 0
        for x in rows:
            total += x
            rows = [5, 6]
            original[1] = 3
        return {'y': total}
""",
    """        rows = [1, 2]
        original = rows
        total = 0
        for x in rows:
            total += x
            rows = [5, 6]
            rows = original
            rows[1] = 3
        return {'y': total}
""",
    """        rows = [1, 2]
        original = rows
        total = 0
        for x in rows:
            total += x
            if a & 1:
                rows = original
            else:
                original = [7, 8]
            rows[1] = 3
        return {'y': total}
""",
])
@pytest.mark.parametrize('lowerer', (GraphLowerer, _Lowerer))
def test_detached_iterator_alias_cannot_silently_enter_queries(body, lowerer):
    with pytest.raises(LoweringError) as failure:
        lower_with_state_ranges(_source(body), input_widths=[2], lowerer=lowerer)
    assert failure.value.code == 'array-assigned-by-reference'


@pytest.mark.parametrize('lowerer', (GraphLowerer, _Lowerer))
def test_start_argument_rebinding_does_not_hide_the_original_alias(lowerer):
    source = """class M:
    def reset(self):
        self.rows = [1, 2]
    def start(self):
        self.rows = [5, 6]
        return 0
    def process(self, a):
        old = self.rows
        total = 0
        for i, x in enumerate(self.rows, self.start()):
            total += x
            old[1] = 3
        return {'y': total}
"""
    with pytest.raises(LoweringError) as failure:
        lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer)
    assert failure.value.code == 'array-assigned-by-reference'
