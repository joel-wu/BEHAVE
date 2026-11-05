"""Declared input domains, not task-name repairs or candidate safety goals."""

import itertools

import pytest
import z3

from behave.sim import api
from behave.sim.core.input_framing import symbolic_initial, symbolic_step


def nested(rows=3, columns=4):
    return api.InputFraming((api.FrameLevel("sof", "eof", rows),
                             api.FrameLevel("sol", "eol", columns)))


def contract(framing=None):
    return api.TaskContract([
        api.Endpoint("in", "in", [
            api.Field(name, api.FiniteType(8 if name == "pixel" else 1), name)
            for name in ("pixel", "sof", "eof", "sol", "eol")],
            api.ready_valid("in", "valid", "ready"), "clk", framing=framing),
        api.Endpoint("out", "out", [api.Field("pixel", api.FiniteType(8), "out_pixel")],
                     api.ready_valid("out", "out_valid", "out_ready"), "clk"),
    ], [api.ClockDomain("clk", "clk", "rst")])


def test_public_contract_roundtrip_and_marker_types():
    value = contract(nested())
    assert api.TaskContract.from_dict(value.as_dict()).as_dict() == value.as_dict()
    with pytest.raises((TypeError, ValueError)):
        contract(api.InputFraming((api.FrameLevel("pixel", "eof", 3),)))
    with pytest.raises(ValueError):
        api.InputFraming((api.FrameLevel("flag", "flag", 3),))


@pytest.mark.parametrize("limits", [(1,), (3,), (1, 1), (2, 3), (2, 2, 3)])
def test_pattern_projection_keeps_data_and_concatenates_complete_frames(limits):
    frame = api.InputFraming(tuple(api.FrameLevel(f"s{i}", f"e{i}", limit)
                                  for i, limit in enumerate(limits)))
    fields = ("data",) + frame.fields
    for choices in itertools.product((0, 1), repeat=len(frame.fields)):
        original = tuple((value,) + choices for value in range(17))
        shaped = frame.shape(original, fields)
        assert [row[0] for row in shaped] == list(range(17))
        state = frame.initial
        for row in shaped + shaped:
            state, legal = frame.step(state, dict(zip(fields, row)))
            assert legal
        assert state == frame.initial


def test_ragged_rows_and_early_outer_end():
    frame = nested(2, 3)
    rows = [{"sof": 1, "eof": 0, "sol": 1, "eol": 1},  # one pixel row
            {"sof": 0, "eof": 0, "sol": 1, "eol": 0},
            {"sof": 0, "eof": 1, "sol": 0, "eol": 1}]  # two pixel row
    state = frame.initial
    for row in rows:
        state, legal = frame.step(state, row)
        assert legal
    assert state == frame.initial
    assert not frame.step(frame.initial, {"sof": 1, "eof": 1, "sol": 1, "eol": 0})[1]


def test_concrete_domain_stutters_and_reset_cancels_partial_frame():
    from behave.sim.core.input_framing import InputDomainCursor

    cursor = InputDomainCursor(contract(nested()))
    slot = api.EventSlot(offers=("in",))
    cursor.advance(slot, {"in": (5, 1, 0, 1, 0)}, index=0)
    assert cursor.states["in"] == (0, 1)
    cursor.advance(slot, {"in": None}, index=1)
    assert cursor.states["in"] == (0, 1)
    cursor.advance(api.EventSlot(reset=True), {}, index=2)
    assert cursor.states["in"] == (0, 0)
    with pytest.raises(api.Refused, match="input-framing-violated"):
        cursor.advance(slot, {"in": (6, 0, 1, 0, 1)}, index=3)
    assert cursor.states["in"] == (0, 0)
    cursor.advance(slot, {"in": (6, 1, 1, 1, 1)}, index=3)
    assert cursor.states["in"] == (0, 0)


def test_symbolic_transition_is_the_same_finite_domain_including_reset_and_stall():
    frame = nested(2, 3)
    state = symbolic_initial(frame)
    fields = {name: z3.BitVec(name, 1) for name in frame.fields}
    concrete = frame.initial
    for row in [{"sof": 1, "eof": 0, "sol": 1, "eol": 0},
                {"sof": 0, "eof": 0, "sol": 0, "eol": 1},
                {"sof": 0, "eof": 1, "sol": 1, "eol": 1}]:
        for bits in itertools.product((0, 1), repeat=4):
            values = dict(zip(frame.fields, bits))
            next_concrete, legal = frame.step(concrete, values)
            next_symbolic, condition = symbolic_step(frame, state, fields)
            solver = z3.Solver()
            solver.add(*(fields[name] == value for name, value in values.items()))
            solver.add(z3.Or(condition != legal,
                             *(a != b for a, b in zip(next_symbolic, next_concrete))))
            assert solver.check() == z3.unsat
        concrete, _ = frame.step(concrete, row)
        state = tuple(z3.BitVecVal(value, old.size()) for value, old in zip(concrete, state))
    stalled, legal = symbolic_step(frame, state, fields, accepted=False)
    assert z3.is_true(z3.simplify(legal))
    assert all(z3.is_true(z3.simplify(a == b)) for a, b in zip(stalled, state))
    reset, legal = symbolic_step(frame, state, fields, reset=True)
    assert z3.is_true(z3.simplify(legal))
    assert all(z3.simplify(value).as_long() == 0 for value in reset)


def test_runtime_baseline_and_local_query_keep_the_same_legal_domain():
    from behave.sim.baseline import BaselinePolicy, build_baseline
    from behave.sim.core.input_framing import LocalInputDomain

    task = contract(nested())
    baseline = build_baseline(task, policy=BaselinePolicy(case_limit=50, max_queue_depth=100))
    source = task.inputs()[0]
    for stimulus in baseline.stimuli:
        state = source.framing.initial
        for row in dict(stimulus.queues)["in"]:
            state, legal = source.framing.step(state, dict(zip(
                (field.name for field in source.fields), row)))
            assert legal
        assert state == source.framing.initial
    # Two one-pixel rows. Closing the frame on the first row would make the
    # second row's fixed sof=0 invalid, even though the first row alone is legal.
    rows = [(5, 1, 0, 1, 1), (6, 0, 1, 1, 1)]
    stimulus = api.Stimulus(queues={"in": rows}, slots=[api.EventSlot(offers=("in",))] * 2)
    records = [api.SlotRecord(accepted={"in": row}) for row in rows]
    projection = {"in": tuple((field.name, field.name) for field in source.fields)}
    domain = LocalInputDomain(task, stimulus, records, projection)
    values = {field.name: z3.BitVec(field.name, field.type.carrier.width) for field in source.fields}
    solver = z3.Solver()
    solver.add(*domain.constraints(0, values), values["eof"] == 1)
    assert solver.check() == z3.unsat
    solver = z3.Solver()
    solver.add(*domain.constraints(0, values), values["pixel"] == 77)
    assert solver.check() == z3.sat


def test_local_context_key_preserves_prefix_suffix_and_reset_constraints():
    from behave.sim.core.input_framing import LocalInputDomain

    task = contract(nested(2, 3))
    endpoint = task.inputs()[0]
    projection = {"in": tuple((field.name, field.name) for field in endpoint.fields)}
    first, last = (5, 1, 0, 1, 1), (6, 0, 1, 1, 1)
    # Same prefix and fixed suffix, despite idle slots and a reset separating
    # repetitions. The final isolated record has a genuinely different domain.
    timeline = ["reset", first, None, last, "reset", first, last,
                "reset", first, "reset"]
    rows = [row for row in timeline if type(row) is tuple]
    stimulus = api.Stimulus({"in": rows}, [
        api.EventSlot(reset=True) if row == "reset" else
        api.EventSlot(offers=("in",)) if type(row) is tuple else api.EventSlot()
        for row in timeline])
    records = [api.SlotRecord(accepted={"in": row}) if type(row) is tuple
               else api.SlotRecord() for row in timeline]
    domain = LocalInputDomain(task, stimulus, records, projection)
    assert domain.context_key(1) == domain.context_key(5)
    assert domain.context_key(3) == domain.context_key(6)
    assert domain.context_key(1) != domain.context_key(3)  # Prefix differs.
    assert domain.context_key(1) != domain.context_key(8)  # Fixed suffix differs.
    assert domain.context_key(0) == domain.context_key(2) == ()
    values = {field.name: z3.BitVec(field.name, field.type.carrier.width)
              for field in endpoint.fields}
    for position in (1, 3, 5, 6, 8):
        for markers in itertools.product((0, 1), repeat=4):
            replacement = dict(zip(("pixel", "sof", "eof", "sol", "eol"), (77, *markers)))
            state, expected = endpoint.framing.initial, True
            for index, row in enumerate(timeline):
                if row == "reset":
                    state = endpoint.framing.initial
                elif type(row) is tuple:
                    payload = replacement if index == position else dict(zip(values, row))
                    state, legal = endpoint.framing.step(state, payload)
                    expected &= legal
            solver = z3.Solver()
            solver.add(*domain.constraints(position, values))
            solver.add(*(values[name] == value for name, value in replacement.items()))
            assert (solver.check() == z3.sat) == expected


def test_behavior_ir_and_exact_unroll_refuse_the_same_illegal_input():
    from behave.sim.behavior import residual
    from behave.sim.symbolic import interp
    from behave.sim.behavior.task_boundary import lower_task_behavior

    source = '''class M:
    def reset(self):
        pass
    def process(self, pixel, sof, eof, sol, eol):
        if sof:
            return {"out_pixel": pixel}
        return {"out_pixel": 0}
'''
    instance, _ = lower_task_behavior(source, "framed.py", contract(nested()), bound=2)
    valid = {"pixel": 4, "sof": 1, "eof": 1, "sol": 1, "eol": 1}
    invalid = dict(valid, eol=0)
    interp.run_trace_detailed(instance.proc, [valid])
    with pytest.raises(ValueError, match="input-framing-violated"):
        interp.run_trace_detailed(instance.proc, [invalid])
    relation = residual.unroll(instance.proc, instance.env, 1)
    solver = z3.Solver()
    solver.add(relation.constraint)
    # Exact counter-domain constraints must be present before any goal query.
    input_terms = relation.encodings[0].pre
    solver.add(*(input_terms[name] == value for name, value in invalid.items()))
    assert solver.check() == z3.unsat


@pytest.mark.parametrize("change_entry", [False, True])
@pytest.mark.parametrize("stateful", [False, True])
def test_incremental_unroll_preserves_framing_counters(change_entry, stateful):
    from behave.sim.behavior import residual
    from behave.sim.behavior.task_boundary import lower_task_behavior

    source = '''class M:
    def reset(self): pass
    def process(self, pixel, sof, eof, sol, eol):
        if sof:
            return {"out_pixel": pixel}
        return {"out_pixel": 0}
'''
    if stateful:
        source = '''class M:
    def reset(self): self.old = 0
    def process(self, pixel, sof, eof, sol, eol):
        y = 0
        if self.old == 37 and pixel == 91:
            y = 1
        self.old = pixel
        return {"out_pixel": y}
'''
    instance, _ = lower_task_behavior(source, "framed.py", contract(nested()), bound=4)
    goals = tuple(g for g in instance.goals() if g.startswith("branch:"))
    assert goals
    markers = ((1, 0, 1, 0), (0, 0, 0, 0), (0, 0, 0, 0), (0, 1, 0, 1))
    fixed = tuple(dict(zip(("sof", "eof", "sol", "eol"), values)) for values in markers)
    previous = residual._RunConstruction()
    residual.unroll(instance.proc, instance.env, 2, goals=goals,
                    _input_values=fixed[:2], _frame_initial=((0, 0),), _construction=previous)
    entry = ((1, 1),) if change_entry else ((0, 0),)
    options = dict(goals=goals, _input_values=fixed, _frame_initial=entry)
    cold = residual.unroll(instance.proc, instance.env, 4, **options)
    owner = residual._RunConstruction(previous)
    actual = residual.unroll(instance.proc, instance.env, 4, _construction=owner, **options)
    assert owner.reused_steps == (0 if change_entry else 1 if stateful else 2)
    assert z3.eq(actual.constraint, cold.constraint)
    assert all(z3.eq(actual.goal_condition(g), cold.goal_condition(g)) for g in goals)
    solver = z3.Solver()
    solver.add(actual.constraint)
    assert solver.check() == (z3.unsat if change_entry else z3.sat)


def test_atomic_behavior_replay_and_formula_share_domain_counters():
    from behave.sim.behavior.atomic_exact import build_atomic_behavior
    from behave.sim.behavior.construction import CONSTRUCTION_VERSION, ConstructionFacts
    from behave.sim.behavior.native_values import DEFAULT_WIRE_NODES, WireEncoder
    from tests.support.atomic_behavior import _contract, _schedule

    image = _contract().as_dict()
    for endpoint in image["endpoints"]:
        if endpoint["direction"] == "in":
            domain = endpoint["clock"]
            endpoint["fields"].extend([
                api.Field(f"{domain}_{flag}", api.FiniteType(1), f"{domain}_{flag}").as_dict()
                for flag in ("start", "end")])
            endpoint["framing"] = api.InputFraming((
                api.FrameLevel(f"{domain}_start", f"{domain}_end", 1),)).as_dict()
    task = api.TaskContract.from_dict(image)
    source = '''class M:
    def __init__(self):
        self.reset()
    def reset(self):
        self.x = 0
        self.y = 0
    def process_a(self, a, a_start, a_end):
        self.x = a
        return {"oa": a}
    def process_b(self, b, b_start, b_end):
        self.y = b
        return {"ob": b}
'''
    construction = ConstructionFacts.from_dict({
        "version": CONSTRUCTION_VERSION,
        "values": WireEncoder(DEFAULT_WIRE_NODES).encode({"x": 0, "y": 0}),
        "unsupported": [], "shared_pairs": [], "opaque_fields": [], "boundary_fields": []})
    instance = build_atomic_behavior(source, "framed_atomic.py", task, _schedule(),
                                      construction=construction)
    rows = []
    for slot in instance.schedule.slots:
        row = {}
        for domain in ("a", "b"):
            row.update({f"{domain}.__invoke__": int(domain in slot.events),
                        f"{domain}.{domain}": 1,
                        f"{domain}.{domain}_start": 1, f"{domain}.{domain}_end": 1})
        rows.append(row)
    assert not instance.replay(rows).broken
    rows[0]["a.a_end"] = 0
    assert instance.replay(rows).broken
    solver = z3.Solver()
    solver.add(instance._formula.constraint, instance._formula.constrain_rows(rows))
    assert solver.check() == z3.unsat
