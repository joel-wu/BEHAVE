"""Native continuous drivers with compiler-owned RHS activation.

The original continuous assignment still owns its net, strength and delay. Its
RHS is evaluated once into a value of the original assignment's destination
type, on startup and then on the compiler's dependency events. This prevents
VPI's extra dataflow evaluations from executing source callbacks. It does not
filter callbacks or infer hits from the driven value.
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from .source_edits import Insertion, Replacement, relocate_expression
from .source_scheduling import InstanceSensitivity, activation_guard


@dataclass(frozen=True, slots=True)
class ContinuousDriver:
    start: int
    right: tuple[int, int]
    after: int
    target: str
    triggers: tuple[str, ...]
    callback: str
    instances: tuple[InstanceSensitivity, ...] = ()


def driver_edits(source, edits, drivers, *, pending_layout, deferred=None, replacements=()):
    """Move each observed RHS, including its probes, into its event process.

    Helpers remain in place. The moved native body is generated text, not a new
    compiler provenance anchor; the compiler stream still uses the original
    expression and every callback retains its original SourceEvent coordinate.
    """
    original = source.encode("utf-8")
    # RHS ranges are disjoint compiler-owned expressions. Index their existing
    # edits once; moving one driver must not scan every other driver's probes.
    edits = list(edits)
    replacements = list(replacements)
    insertion_order = sorted(range(len(edits)), key=lambda i: edits[i].offset)
    insertion_offsets = [edits[i].offset for i in insertion_order]
    replacement_order = sorted(range(len(replacements)), key=lambda i: replacements[i].start)
    replacement_starts = [replacements[i].start for i in replacement_order]
    # A prefix maximum also finds crossing replacements without assuming their
    # input order or dropping a malformed overlap before the usual check.
    replacement_ends = []
    for i in replacement_order:
        replacement_ends.append(max(replacements[i].end,
                                    replacement_ends[-1] if replacement_ends else -1))
    moved_insertions, moved_replacements = set(), set()
    added_insertions, added_replacements = [], []
    for driver in drivers:
        first, last = driver.right
        selected_insertions = [i for i in insertion_order[
            bisect_left(insertion_offsets, first):bisect_right(insertion_offsets, last)]
            if i not in moved_insertions]
        selected_replacements = [i for i in replacement_order[
            bisect_right(replacement_ends, first):bisect_left(replacement_starts, last)]
            if i not in moved_replacements and first < replacements[i].end]
        expression, _, _ = relocate_expression(original, driver.right,
            [edits[i] for i in selected_insertions],
            [replacements[i] for i in selected_replacements])
        moved_insertions.update(selected_insertions)
        moved_replacements.update(selected_replacements)
        name = f"__behave_native_event_driver_{driver.start}"
        # Copied source may end in an escaped identifier. Whitespace must
        # separate it from generated punctuation in both value and type uses.
        body = driver.callback + f" {name} = {expression} ; "
        if driver.triggers:
            guard = activation_guard(driver.start, driver.triggers, pending=pending_layout.reference(driver.start),
                                     deferred=() if deferred is None else deferred.get(driver.start, ()),
                                     instances=driver.instances)
            process = guard.declarations + " always_comb " + guard.before + body + guard.after
        else:
            process = "initial begin " + body + "end "
        added_insertions.append(Insertion(driver.after, f" var type({driver.target} ) {name}; " + process))
        added_replacements.append(Replacement(first, last, name))
    return ([edit for i, edit in enumerate(edits) if i not in moved_insertions] + added_insertions,
            [edit for i, edit in enumerate(replacements) if i not in moved_replacements] + added_replacements)
