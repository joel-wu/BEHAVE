"""Finite query-window construction shared by Search providers.

The closed plan records live in generation_protocol. Their historical imports
remain aliases here; window ordering and execution-domain boundaries are unchanged.
"""
from itertools import zip_longest

from .generation_protocol import QueryPlan as QueryPlan, QueryTemplate as QueryTemplate


def widening_windows(items):
    """Disjoint tiles per doubling scale, visiting the entry ladder first.

    Include the whole supplied epoch without enumerating every start/end pair:
    O(n) windows and O(n log n) element references. The caller owns epoch/reset
    boundaries. These are search domains, not claims that a state is reachable.
    """
    items = tuple(items)
    scales, length = [], 2
    while len(items) > 1:
        width = min(length, len(items))
        scales.append(tuple((start, min(start + width, len(items)))
                            for start in range(0, len(items), width)
                            if min(width, len(items) - start) > 1))
        if width == len(items):
            break
        length *= 2
    seen = set()
    for tiles in zip_longest(*scales):
        for window in tiles:
            if window is not None and window not in seen:
                seen.add(window)
                yield items[window[0]:window[1]]


def ending_windows(items):
    """Widen backwards from one trigger, retaining the whole epoch last.

    O(log n) questions and O(n) element references, not all start/end pairs.
    Every window ends at the same trigger; its caller binds the native entry.
    """
    items, width = tuple(items), 2
    while len(items) > 1:
        width = min(width, len(items))
        yield items[-width:]
        if width == len(items):
            break
        width *= 2

