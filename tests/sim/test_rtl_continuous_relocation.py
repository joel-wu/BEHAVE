"""Indexed RHS relocation preserves the previous observer byte-for-byte."""

import random
from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.sim.rtl import source_continuous as continuous
from behave.sim.rtl.source_edits import Insertion, Replacement, SourceEdits, relocate_expression
from behave.sim.rtl.source_scheduling import activation_guard


def _previous(source, edits, drivers, *, pending_layout, replacements=()):
    original = source.encode("utf-8")
    replacements = list(replacements)
    for driver in drivers:
        first, last = driver.right
        expression, retained, replacements = relocate_expression(
            original, driver.right, edits, replacements)
        name = f"__behave_native_event_driver_{driver.start}"
        body = driver.callback + f" {name} = {expression} ; "
        if driver.triggers:
            guard = activation_guard(driver.start, driver.triggers,
                                     pending=pending_layout.reference(driver.start),
                                     deferred=(), instances=driver.instances)
            process = guard.declarations + " always_comb " + guard.before + body + guard.after
        else:
            process = "initial begin " + body + "end "
        retained.append(Insertion(driver.after, f" var type({driver.target} ) {name}; " + process))
        replacements.append(Replacement(first, last, name))
        edits = retained
    return edits, replacements


def _fixture(count):
    source = "// π: UTF-8 source coordinates\n" + "\n".join(
        f"assign w{i} = (a ^ 1'b0);" for i in range(count))
    raw = source.encode()
    drivers, edits, replacements = [], [Insertion(0, "// outside\n")], []
    cursor = 0
    for i in range(count):
        start = raw.index(b"assign", cursor)
        first = raw.index(b"(a", start)
        last = raw.index(b";", first)
        cursor = last + 1
        drivers.append(continuous.ContinuousDriver(start, (first, last), cursor,
            f"w{i}", ("a",) if i % 2 else (), f"callback_{i}();"))
        # Equal-offset insertions must keep their original order.
        edits.extend((Insertion(first, "("), Insertion(first, "("),
                      Insertion(last, "))"), Insertion(cursor, " // retained")))
        position = raw.index(b"1'b0", first)
        replacements.append(Replacement(position, position + 4, "1'b1"))
    return source, drivers, edits, replacements


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("common_after", [False, True])
def test_relocation_matches_previous_bytes_and_source_mapping(seed, common_after):
    source, drivers, edits, replacements = _fixture(32)
    if common_after:
        drivers = [replace(driver, after=len(source.encode())) for driver in drivers]
    rng = random.Random(seed)
    rng.shuffle(drivers)
    rng.shuffle(edits)
    rng.shuffle(replacements)
    layout = SimpleNamespace(reference=lambda start: f"pending_{start}")
    args = dict(pending_layout=layout, replacements=replacements)
    old = _previous(source, edits, drivers, **args)
    new = continuous.driver_edits(source, edits, drivers, **args)
    assert new == old
    plans = [SourceEdits(source, tuple(sorted(rows[0], key=lambda e: e.offset)),
                         tuple(sorted(rows[1], key=lambda e: e.start))) for rows in (old, new)]
    assert plans[0] == plans[1]


@pytest.mark.parametrize("side", ["left", "right"])
def test_crossing_replacement_is_still_rejected(side):
    source, drivers, edits, _ = _fixture(2)
    first, last = drivers[0].right
    replacement = Replacement(first - 1, first + 1, "x") if side == "left" else Replacement(last - 1, last + 1, "x")
    layout = SimpleNamespace(reference=lambda start: "pending")
    for execute in (_previous, continuous.driver_edits):
        with pytest.raises(ValueError, match="relocation crosses"):
            execute(source, edits, drivers, pending_layout=layout, replacements=[replacement])


def test_each_existing_edit_is_relocated_once_not_scanned_per_driver(monkeypatch):
    source, drivers, edits, replacements = _fixture(256)
    seen = []
    original = continuous.relocate_expression

    def inspect(source, span, insertions, replacements):
        seen.append(len(insertions) + len(replacements))
        return original(source, span, insertions, replacements)

    monkeypatch.setattr(continuous, "relocate_expression", inspect)
    continuous.driver_edits(source, edits, drivers,
        pending_layout=SimpleNamespace(reference=lambda start: "pending"), replacements=replacements)
    assert len(seen) == len(drivers)
    assert sum(seen) <= len(edits) + len(replacements)
