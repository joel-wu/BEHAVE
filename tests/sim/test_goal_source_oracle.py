"""Expected source events are handwritten, independently of the site extractor."""

import pytest

from behave.sim.behavior import instrumented
from behave.sim.behavior.catalogue import source_frontend_catalogue
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.catalogue import COVERAGE, SAFETY

SOURCE = """\
class M:
    def helper(self, a):
        return 9 if a else 8
    def unused(self, a):
        if a:
            return 42
        return 0
    def process(self, a, b):
        x = self.helper(a)
        y = self.helper(b)
        if a and b:
            y = y + 1
        for j in range(a):
            if j:
                break
            x = x + 1
        return {'y': x + y}
    def reset(self):
        pass
"""


def _event(goal):
    origin = goal.schema.provenance
    return (goal.family, origin["line"], origin["kind"], goal.event,
            origin["col"] if goal.family == "condition" else None)


def _entry(line, kind):
    return ("location", line, kind, "entry", None)


def _outcome(line, kind, value, family="decision", col=None):
    return (family, line, kind, "T" if value else "F", col)


LOCATIONS = {
    _entry(3, "return"), _entry(9, "assign"), _entry(10, "assign"),
    _entry(11, "if"), _entry(12, "assign"), _entry(13, "for"),
    _entry(14, "if"), _entry(15, "break"), _entry(16, "assign"),
    _entry(17, "return"),
}
DECLARED = LOCATIONS | {
    _outcome(line, kind, value, family, col)
    for line, kind, family, col in (
        (3, "ifexp", "decision", None), (11, "if", "decision", None),
        (11, "atom", "condition", 11), (11, "atom", "condition", 17),
        (13, "for", "loop", None), (14, "if", "decision", None))
    for value in (False, True)
}


def _expected_hits(a, b):
    # Two calls share a source goal but may take different outcomes. A skipped
    # Boolean operand supplies no value, and loop exhaustion is not zero entry.
    hit = LOCATIONS - {
        _entry(12, "assign"), _entry(14, "if"), _entry(15, "break"), _entry(16, "assign")}
    hit |= {_outcome(3, "ifexp", bool(a)), _outcome(3, "ifexp", bool(b)),
            _outcome(11, "if", bool(a and b)),
            _outcome(11, "atom", bool(a), "condition", 11),
            _outcome(13, "for", bool(a), "loop")}
    if a:
        hit |= {_outcome(11, "atom", bool(b), "condition", 17),
                _entry(14, "if"), _outcome(14, "if", False), _entry(16, "assign")}
    if a and b:
        hit.add(_entry(12, "assign"))
    if a >= 2:
        hit |= {_outcome(14, "if", True), _entry(15, "break")}
    return hit


def test_source_instances_and_native_events_match_independent_expectations():
    manifest = source_frontend_catalogue(source_model(SOURCE), SOURCE)
    assert {_event(goal) for goal in manifest.values()} == DECLARED
    assert len(manifest) == len(DECLARED) == 22
    assert all(goal.cls == COVERAGE for goal in manifest.values())
    assert len(manifest.occurrences) == len(manifest)
    native, _sites, hits = instrumented.load_source(SOURCE)
    for a in range(4):
        for b in range(4):
            hits.clear()
            result = native["M"]().process(a, b)
            assert result == {"y": 16 + bool(a) * 2 + bool(b) + bool(a and b)}
            assert {_event(manifest[goal]) for goal in hits} == _expected_hits(a, b)


@pytest.mark.solver
def test_same_source_oracle_checks_ir_and_symbolic_events():
    z3 = pytest.importorskip("z3")
    from behave.sim.symbolic import encoder, interp, plan, ranges
    from behave.sim.behavior.lowering import lower_with_state_ranges

    manifest = source_frontend_catalogue(source_model(SOURCE), SOURCE)
    proc = lower_with_state_ranges(SOURCE, input_widths=[2, 2])
    env = {**proc.state_ranges, "a": ranges.Range(0, 3), "b": ranges.Range(0, 3)}
    prepared = plan.plan_encoding(proc, env)
    assert prepared.complete, prepared.unresolved
    encoded = encoder.encode_step(proc, env=env, plan=prepared)
    for a in range(4):
        for b in range(4):
            _state, output, hits, error = interp.run_step(proc, {"a": a, "b": b})
            assert not error
            assert output.transactions == [{"y": 16 + bool(a) * 2 + bool(b) + bool(a and b)}]
            assert {_event(manifest[goal]) for goal in hits} == _expected_hits(a, b)
            solver = z3.Solver()
            solver.add(encoded.constraint, encoded.pre["a"] == a, encoded.pre["b"] == b)
            assert solver.check() == z3.sat
            model = solver.model()
            symbolic = {goal for goal, term in encoded.hits.items()
                        if z3.is_true(model.eval(term, model_completion=True))}
            assert {_event(manifest[goal]) for goal in symbolic} == _expected_hits(a, b)


def test_no_decision_is_not_an_unimplemented_decision():
    source = "class M:\n    def process(self, a):\n        return {'y': a}\n"
    manifest = source_frontend_catalogue(source_model(source), source)
    assert {_event(goal) for goal in manifest.values()} == {_entry(3, "return")}


@pytest.mark.parametrize("denominator,argument,raises", (("2", 0, False), ("a", 0, True), ("a", 2, False)))
def test_failure_sites_exist_before_reachability_and_emit_only_on_failure(denominator, argument, raises):
    source = f"class M:\n    def process(self, a):\n        return {{'y': 8 // {denominator}}}\n"
    selected = source_model(source)
    plan = plan_source(selected, source)
    manifest = plan.manifest
    failures = [goal for goal in manifest.values() if goal.cls == SAFETY]
    assert len(failures) == 1
    goal = failures[0]
    assert (goal.family, goal.schema.provenance["line"], goal.event) == ("divzero", 3, "failure")
    native, _sites, hits = instrumented.load_source(source)
    emitted = None
    try:
        result = native["M"]().process(argument)
        assert result == {"y": 4}
    except ZeroDivisionError as error:
        emitted = instrumented.attribute_safety_failure(error, plan.safety_sites)
    assert (emitted == goal.id) is raises
    assert {_event(manifest[hit]) for hit in hits} == {_entry(3, "return")}
