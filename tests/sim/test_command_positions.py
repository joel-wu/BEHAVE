"""Canonical source positions do not depend on Seq association or transport."""

import pickle
import random

import pytest

from behave.sim.symbolic import ir
from behave.sim.symbolic.plan import _command_numbers

pytestmark = pytest.mark.solver


def sequence(parts, association):
    if len(parts) == 1:
        return parts[0]
    if association == "left":
        result = parts[0]
        for part in parts[1:]:
            result = ir.Seq(result, part)
        return result
    if association == "right":
        result = parts[-1]
        for part in reversed(parts[:-1]):
            result = ir.Seq(part, result)
        return result
    middle = len(parts) // 2
    return ir.Seq(sequence(parts[:middle], association),
                  sequence(parts[middle:], association))


def canonical_nodes(command):
    """Reference preorder, including each virtual right-associated Seq."""
    if isinstance(command, ir.Seq):
        parts, pending = [], [command]
        while pending:
            part = pending.pop()
            if isinstance(part, ir.Seq):
                pending.extend((part.second, part.first))
            else:
                parts.append(part)
        for index, part in enumerate(parts):
            if index + 1 < len(parts):
                yield None
            yield from canonical_nodes(part)
    else:
        yield command
        if isinstance(command, ir.If):
            yield from canonical_nodes(command.then)
            yield from canonical_nodes(command.orelse)
        elif isinstance(command, ir.For):
            yield from canonical_nodes(command.body)


@pytest.mark.parametrize("seed", range(12))
def test_nested_positions_match_canonical_preorder_and_worker_transport(seed):
    rng = random.Random(seed)
    shared = ir.Skip()

    def tree(depth):
        if not depth:
            return shared if rng.randrange(3) == 0 else ir.Mark(str(rng.getrandbits(64)))
        kind = rng.randrange(3)
        if kind == 0:
            return sequence([tree(depth - 1) for _ in range(rng.randrange(2, 6))],
                            rng.choice(("left", "right", "balanced")))
        if kind == 1:
            return ir.If(ir.Lit(True), tree(depth - 1), tree(depth - 1))
        return ir.For("i", 3, tree(depth - 1))

    parts = [tree(3) for _ in range(5)]
    bodies = [sequence(parts, association)
              for association in ("left", "right", "balanced")]
    expected = {id(command): position
                for position, command in enumerate(canonical_nodes(bodies[0]))
                if command is not None}
    for body in bodies:
        assert _command_numbers(body) == expected
        restored = pickle.loads(pickle.dumps(body))
        restored_expected = {id(command): position
                             for position, command in enumerate(canonical_nodes(restored))
                             if command is not None}
        assert _command_numbers(restored) == restored_expected
        assert sorted(restored_expected.values()) == sorted(expected.values())


@pytest.mark.parametrize("association", ["left", "right", "balanced"])
def test_long_sequences_keep_exact_positions_without_recursive_numbering(association):
    parts = [ir.Mark(f"g{i}") for i in range(20_000)]
    body = sequence(parts, association)
    positions = _command_numbers(body)
    assert positions == {id(part): min(2 * i + 1, 2 * len(parts) - 2)
                         for i, part in enumerate(parts)}
