"""Exact sharing of complete occurrence questions, independent of goal names."""

from .budgets import Spent


class GroupBudget:
    """A construction-through-refinement deadline, sharing the artifact counter."""

    def __init__(self, parent, ms, *, now=None):
        self.parent = parent
        self.clock = Spent(ms=ms, max_queries=None, now=now)

    def exhausted(self):
        return self.parent.exhausted() or (
            f"the shared exact-query clock of {self.clock.ms} ms is spent"
            if self.clock.remaining_ms() <= 0 else None)

    def for_query(self, ms, *, participants=1):
        return min(self.parent.for_query(ms, participants=participants),
                   max(1, self.clock.remaining_ms()))

    def remaining_queries(self):
        return self.parent.remaining_queries()

    def charge(self, count=1):
        self.parent.charge(count)


def complete_groups(work_by_goal):
    """Group only singleton complete covers with equal per-position commands.

    A multi-fragment cover still needs every fragment before UNSAT can settle
    its public goal. Keeping it out lets the existing set-wise coordinator
    consume a shared formula without learning a second proof-accounting scheme.
    The caller supplies all fragments returned by its occurrence partitioner,
    not one arbitrarily selected fragment from each goal.
    """
    by_selection = {}
    for goal, fragments in work_by_goal.items():
        if len(fragments) == 1:
            by_selection.setdefault(fragments[0].keeps, []).append(goal)
    groups = {}
    for members in by_selection.values():
        if len(members) > 1:
            group = tuple(members)
            groups.update((goal, group) for goal in group)
    return groups
