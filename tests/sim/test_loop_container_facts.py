"""A container changed inside a loop keeps no pre-loop element facts in the body.

An element store keeps a list's binding and length, so it is neither a scalar
write nor a length mutation. The loop body must still forget what the
pre-loop elements were: otherwise a short-circuit operand that reads them is
"proved" constant from the initial value and its test disappears from the IR,
which then disagrees with Python on every later visit (nms4_u8's second model,
2026-10-18). Refusing to lower is acceptable; disagreeing silently is not.
"""

import textwrap

import pytest

from behave.sim.symbolic import interp
from behave.sim.behavior.lowering import LoweringError, lower_with_state_ranges

HEADER = """
class M:
    def reset(self):
        self.seen = [False, False, False, False]

    def mark(self, cells, index):
        cells[index] = True

    def process(self, a):
"""

SELECTION = """
visited = [False, False, False, False]
out = 0
for r in range(4):
    best = 0
    found = False
    for i in range(4):
        if {condition}:
            best = i
            found = True
    {store}
    out |= best << (2 * r)
return {{"y": out}}
"""


def _agrees(body, inputs=(0, 1, 77, 255)):
    text = HEADER + textwrap.indent(textwrap.dedent(body).strip(), "        ") + "\n"
    namespace = {}
    exec(compile(text, "behavior.py", "exec", dont_inherit=True), namespace)  # noqa: S102
    model = namespace["M"]()
    model.reset()
    try:
        proc = lower_with_state_ranges(text, input_widths=[8], cls="M")
    except LoweringError:
        return  # outside the lowered subset is a refusal, not a disagreement
    state = None
    for value in inputs:
        expected = model.process(value)
        state, output, _hits, error = interp.run_step(proc, {"a": value}, state)
        assert not error, (value, error)
        assert output.transactions == [expected], value


@pytest.mark.parametrize("condition", [
    "not visited[i] and True",
    "not visited[i] and (not found or i > best)",
    "not visited[i] and not found",
    "(not found or i > best) and not visited[i]",
])
@pytest.mark.parametrize("store", [
    "visited[best] = True",
    "visited[3 - r] = True",
    "visited[best] |= True",
    "self.mark(visited, best)",
])
def test_short_circuit_reads_see_element_stores_of_earlier_visits(condition, store):
    _agrees(SELECTION.format(condition=condition, store=store))


def test_first_winner_selection_keeps_its_unclaimed_test():
    # The crossbar arbiter's second model has this shape: once a port has a
    # winner, later offers must not replace it.
    _agrees("""
        winner = [-1, -1]
        out = 0
        for port in range(2):
            for offset in range(3):
                candidate = (port + offset) % 3
                if winner[port] == -1 and (a >> candidate) & 1:
                    winner[port] = candidate
            out = out * 4 + winner[port] + 1
        return {"y": out}
    """, inputs=(0, 3, 5, 6, 7))


def test_nested_list_element_stores_reach_later_visits():
    _agrees("""
        rows = [[0, 0], [0, 0]]
        total = 0
        for r in range(4):
            if not rows[r % 2][0] and a > r:
                total += 1
            rows[r % 2][0] = 1
        return {"y": total}
    """)


def test_in_place_reordering_reaches_later_visits():
    _agrees("""
        values = [3, 1, 2]
        out = 0
        for r in range(2):
            if values[0] == 3 and a >= 0:
                out += 1
            values.sort()
        return {"y": out}
    """)


def test_state_list_element_stores_reach_later_visits():
    _agrees("""
        out = 0
        for r in range(4):
            if not self.seen[r % 2] and a >= 0:
                out += 1
            self.seen[r % 2] = True
        return {"y": out}
    """, inputs=(0,))


# After the loop: a long loop is planned from one abstract body visit, which
# computes stored values from loop-entry facts. Storing an accumulator there
# left the element envelope of its initial value ([0] * n), so a helper that
# branches on the element lost its other return (ai_ml_tx_pos_other_alibi_bias,
# 2026-10-18).
def _module_agrees(text, inputs, width):
    namespace = {}
    exec(compile(text, "behavior.py", "exec", dont_inherit=True), namespace)  # noqa: S102
    model = namespace["M"]()
    model.reset()
    try:
        proc = lower_with_state_ranges(text, input_widths=[width], cls="M")
    except LoweringError:
        return
    state = None
    for value in inputs:
        expected = model.process(value)
        state, output, _hits, error = interp.run_step(proc, {"a": value}, state)
        assert not error, (value, error)
        assert output.transactions == [expected], value


@pytest.mark.parametrize("trips", [5, 8, 16])
@pytest.mark.parametrize("read", ["one", "loop"])
def test_an_accumulator_stored_by_a_long_loop_reaches_a_helper_branch(trips, read):
    use = (f"        return {{'y': _bias(position[{trips - 1}], 1)}}\n" if read == "one" else
           f"        packed = 0\n"
           f"        for i in range({trips}):\n"
           f"            packed |= _bias(position[i], 1) << (8 * i)\n"
           f"        return {{'y': packed}}\n")
    text = (
        "def _bias(p, h):\n"
        "    if p == 0:\n"
        "        return 0\n"
        "    return p + h\n"
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, a):\n"
        f"        position = [0] * {trips}\n"
        "        count = 0\n"
        f"        for i in range({trips}):\n"
        "            if (a >> i) & 1:\n"
        "                position[i] = count\n"
        "                count += 1\n" + use)
    _module_agrees(text, (0, 0xffff, 0x8001, 0x00ff), 16)


def test_a_histogram_counted_by_a_long_loop_reaches_a_later_branch():
    text = (
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, a):\n"
        "        hist = [0] * 4\n"
        "        for i in range(8):\n"
        "            hist[(a >> (2 * i)) & 3] += 1\n"
        "        if hist[0] == 0:\n"
        "            return {'y': 99}\n"
        "        return {'y': hist[0]}\n")
    _module_agrees(text, (0, 0xffff, 0x5555, 0x00ff), 16)
