"""Integer context plans preserve every token in the native zigzag layout."""

from itertools import pairwise
from types import SimpleNamespace

import pytest

from behave.train.slime_launch import context_slices


class Boundaries:
    def __init__(self, values):
        self.values = values
        self.transfers = 0

    def tolist(self):
        self.transfers += 1
        return self.values


@pytest.mark.parametrize("cp", [2, 4, 8])
@pytest.mark.parametrize("chunks", [(2,), (2, 6, 4), (1, 17, 2, 3)])
def test_gather_scatter_are_exact_and_host_copy_is_shared(cp, chunks):
    boundaries = [0]
    for chunk in chunks:
        boundaries.append(boundaries[-1] + 2 * cp * chunk)
    packed = SimpleNamespace(cu_seqlens_q=Boundaries(boundaries))
    plan = context_slices(packed, cp)
    full = list(range(boundaries[-1]))
    native_parts = []
    for rank in range(cp):
        pieces = []
        for begin, end in pairwise(boundaries):
            chunk = (end - begin) // (2 * cp)
            offsets = (rank, 2 * cp - rank - 1)
            for offset in offsets:
                pieces.extend(full[begin + offset * chunk:begin + (offset + 1) * chunk])
        native_parts.append(pieces)
        assert [token for part in plan.scatter[rank] for token in full[part]] == pieces
    assert [token for rank, part in plan.gather for token in native_parts[rank][part]] == full
    for _ in range(96):  # 48 layers plus their recomputation reuse the same plan.
        assert context_slices(packed, cp) is plan
    assert packed.cu_seqlens_q.transfers == 1


def test_plans_do_not_escape_their_microbatch():
    first = SimpleNamespace(cu_seqlens_q=Boundaries([0, 8, 32]))
    second = SimpleNamespace(cu_seqlens_q=Boundaries([0, 16, 32]))
    left, right = context_slices(first, 2), context_slices(second, 2)
    assert left != right
    assert first.cu_seqlens_q.transfers == second.cu_seqlens_q.transfers == 1
