"""Adversarial tests for the state contract at construction entry."""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import multiprocessing
import os
import sys
import threading
import time

import pytest

from behave.sim.behavior import construction as construction_module
from behave.sim.behavior import lower_prepare
from behave.sim.symbolic import interp, typecheck as types
from behave.sim.behavior.construction import (
    CONSTRUCTION_VERSION,
    ConstructionFacts,
    ConstructionUnavailable,
    clear_construction_cache,
    construction_facts,
)
from behave.sim.behavior.extract import initial_by_construction
from behave.sim.behavior.lowering import LoweringError, lower_with_state_ranges
from behave.sim.behavior.native_values import (
    NativeValueError,
    NativeValueLimit,
    ValueWireEncoder,
    WireDecoder,
    WireEncoder,
    freeze,
)


def test_construction_protocol_version_is_pinned():
    """Carrier/share protocol changes invalidate in-process fact caches."""
    assert CONSTRUCTION_VERSION == 6


def test_construction_facts_strict_json_round_trip_preserves_native_types():
    facts = ConstructionFacts(
        (("flag", ("bool", True)), ("value", ("int", 1))),
        (("blob", "a bytes value SolverIR does not carry"),),
        (("flag", "value"),),
        ("@module:rom",),
        (("blob", freeze(b"\x00\xff")),
         ("flag", freeze(True)), ("value", freeze(1))),
    )

    image = facts.as_dict()
    restored = ConstructionFacts.from_dict(json.loads(json.dumps(image)))

    assert restored == facts
    assert restored.exact_values() == {
        "blob": b"\x00\xff", "flag": True, "value": 1}
    assert type(restored.exact_values()["flag"]) is bool
    assert type(restored.exact_values()["value"]) is int


def test_construction_facts_decoder_rejects_noncanonical_or_open_images():
    facts = ConstructionFacts(
        (("value", ("int", 1)),), (), (), (),
        (("value", freeze(1)),))
    image = facts.as_dict()

    with pytest.raises(TypeError, match="complete closed"):
        ConstructionFacts.from_dict({**image, "extra": None})
    with pytest.raises(ValueError, match="sorted and unique"):
        ConstructionFacts.from_dict({
            **image,
            "shared_pairs": [["z", "a"], ["a", "z"]],
        })


def test_boundary_projection_removes_only_replaced_source_state():
    facts = ConstructionFacts(
        (("mem", ("list", (("int", 0), ("int", 1)))),
         ("x", ("int", 3))),
        (), (("mem", "x"),), ("mem",),
        (("mem", freeze([0, 1])), ("x", freeze(3))),
    )

    projected = facts.for_boundary(["mem"])
    restored = ConstructionFacts.from_dict(
        json.loads(json.dumps(projected.as_dict())))

    assert restored == projected
    assert restored.boundary_fields == ("mem",)
    assert restored.values() == {"x": 3}
    assert restored.exact_values() == {"x": 3}
    assert restored.shared_pairs == ()
    assert restored.opaque_fields == ()
    with pytest.raises(ValueError, match="applied once"):
        projected.for_boundary(["x"])


def _native(source, **inputs):
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    model = namespace["M"]()
    model.reset()
    result = model.process(**inputs)
    return result if isinstance(result, list) else [result]


def _entry_values(source):
    values, unsupported = initial_by_construction(source)
    assert unsupported == {}
    return values


def _lowered(source, *, widths=(2,), **inputs):
    proc = lower_with_state_ranges(
        source,
        input_widths=list(widths),
        outputs=["y"],
        read_initial=_entry_values(source),
    )
    _state, output, _hits, error = interp.run_step(proc, inputs)
    assert error == ""
    return proc, [dict(row) for row in output.transactions]


def _assert_agrees(source, cases, *, widths=(2,)):
    for inputs in cases:
        _proc, got = _lowered(source, widths=widths, **inputs)
        assert got == _native(source, **inputs)


def _hold_process_build_slot(namespace, active, maximum, lock, overlap):
    """Picklable worker for the cross-process construction-cap test."""
    slots = construction_module._BuildCapacity(2, namespace=namespace)
    with slots:
        with lock:
            active.value += 1
            maximum.value = max(maximum.value, active.value)
            if active.value == 2:
                overlap.set()
        # Ensure two owners overlap rather than merely happening to be
        # scheduled sequentially.  A broken capacity of one times out here
        # and leaves ``maximum`` at one, so the assertion still fails.
        overlap.wait(3)
        time.sleep(0.03)
        with lock:
            active.value -= 1


def _exit_while_holding_process_build_slot(namespace, acquired):
    slots = construction_module._BuildCapacity(1, namespace=namespace)
    with slots:
        acquired.set()
        os._exit(0)


def _acquire_inherited_build_slots(slots, acquired):
    with slots:
        acquired.set()


def _build_after_fork(source, connection):
    """Exercise the real cache path in a forked child under a hard deadline."""
    facts = ConstructionFacts((('x', ('int', 1)),), (), (), ())
    construction_module._build = lambda *_args: facts
    try:
        got = construction_facts(source, cls_name="M")
        connection.send(("ok", got.values()))
    except BaseException as exc:  # noqa: BLE001 - returned to the test parent
        connection.send(("error", type(exc).__name__, str(exc)))
    finally:
        connection.close()


def test_reset_overrides_the_constructor_at_construction_entry():
    source = """\
class M:
    def reset(self):
        self.rows = [[1, 2], [3, 4]]
    def __init__(self):
        self.rows = [[9]]
    def process(self, a):
        return {"y": self.rows[a & 1][1]}
"""

    assert _entry_values(source)["rows"] == [[1, 2], [3, 4]]
    _assert_agrees(source, [{"a": 0}, {"a": 1}])


def test_unconditional_nested_assignment_defines_the_entry_contract():
    source = """\
class M:
    def reset(self):
        self.rows = [[1, 2], [3, 4], [5, 6]]
    def process(self, a):
        return {"y": self.rows[a & 3][0]}
"""

    _assert_agrees(source, [{"a": 0}, {"a": 1}, {"a": 2}])


def test_an_unexecuted_conditional_assignment_does_not_define_state():
    source = """\
class M:
    def __init__(self):
        self.enabled = 0
    def reset(self):
        if self.enabled:
            self.buf = [7, 9]
    def process(self, a):
        return {"y": self.buf[a & 1]}
"""

    assert "buf" not in _entry_values(source)
    with pytest.raises(AttributeError):
        _native(source, a=0)
    with pytest.raises(LoweringError):
        lower_with_state_ranges(
            source,
            input_widths=[1],
            outputs=["y"],
            read_initial=_entry_values(source),
        )


def test_equal_branch_contracts_join_at_construction_entry():
    source = """\
class M:
    def __init__(self, mode=0):
        self.mode = mode
    def reset(self):
        if self.mode:
            self.buf = [1, 2]
        else:
            self.buf = [3, 4]
    def process(self, a):
        return {"y": self.buf[a & 1]}
"""

    _assert_agrees(source, [{"a": 0}, {"a": 1}], widths=(1,))


def test_disagreeing_branches_use_the_authenticated_entry_value():
    source = """\
class M:
    def __init__(self, mode=0):
        self.mode = mode
    def reset(self):
        if self.mode:
            self.buf = [1, 2]
        else:
            self.buf = [[3, 4], [5, 6]]
    def process(self, a):
        return {"y": self.buf[a & 1][0]}
"""

    assert _entry_values(source)["buf"] == [[3, 4], [5, 6]]
    proc = lower_with_state_ranges(
        source, input_widths=[1], outputs=["y"])
    _state, output, _hits, error = interp.run_step(proc, {"a": 0})
    assert error == ""
    assert [dict(row) for row in output.transactions] == [{"y": 3}]
    _assert_agrees(source, [{"a": 0}, {"a": 1}], widths=(1,))


def test_comprehension_and_range_preserve_recursive_entry_carriers():
    source = """\
class M:
    def __init__(self, rows=3):
        self.rows = rows
    def reset(self):
        self.table = [(i, i + 1) for i in range(self.rows)]
    def process(self, a):
        row = self.table[a & 3]
        return {"y": int(row == (a, a + 1))}
"""

    assert _entry_values(source)["table"] == [(0, 1), (1, 2), (2, 3)]
    _assert_agrees(source, [{"a": 0}, {"a": 1}, {"a": 2}])


def test_constructor_default_owns_the_recursive_entry_shape():
    source = """\
N = 4
class M:
    def __init__(self, n=2):
        self.n = n
    def reset(self):
        self.table = [[i] for i in range(self.n)]
    def process(self, a):
        return {"y": self.table[a & 1][0]}
"""

    assert _entry_values(source)["table"] == [[0], [1]]
    _assert_agrees(source, [{"a": 0}, {"a": 1}], widths=(1,))


def test_a_helper_value_is_observed_at_the_construction_boundary():
    source = """\
def build():
    return [[1, 2], [3, 4]]

class M:
    def reset(self):
        self.rows = build()
    def process(self, a):
        return {"y": self.rows[a & 1][0]}
"""

    proc = lower_with_state_ranges(
        source, input_widths=[1], outputs=["y"])
    _state, output, _hits, error = interp.run_step(proc, {"a": 1})
    assert error == ""
    assert [dict(row) for row in output.transactions] == [{"y": 3}]
    _assert_agrees(source, [{"a": 0}, {"a": 1}], widths=(1,))


def test_a_resolved_construction_helper_obeys_constructor_then_reset_order():
    source = """\
class M:
    def __init__(self):
        self.rows = [[9]]
    def install(self):
        self.rows = [[1, 2], [3, 4]]
    def reset(self):
        self.install()
    def process(self, a):
        return {"y": self.rows[a & 1][1]}
"""

    assert _entry_values(source)["rows"] == [[1, 2], [3, 4]]
    _assert_agrees(source, [{"a": 0}, {"a": 1}], widths=(1,))


def test_an_unknown_construction_effect_is_checked_against_the_witness():
    source = """\
def replace(model):
    model.buf = [7, 9]

class M:
    def reset(self):
        self.buf = [1, 2, 3]
        replace(self)
    def process(self, a):
        return {"y": self.buf[a]}
"""

    values = _entry_values(source)
    assert values["buf"] == [7, 9]
    _assert_agrees(source, [{"a": 0}, {"a": 1}], widths=(2,))


def test_mutable_aliases_are_not_erased_by_equal_entry_values():
    source = """\
class M:
    def reset(self):
        row = [1, 2]
        self.left = row
        self.right = row
    def process(self, a):
        self.left[0] = a
        return {"y": self.right[0]}
"""

    values = _entry_values(source)
    assert values["left"] == values["right"] == [1, 2]
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source,
            input_widths=[2],
            outputs=["y"],
            read_initial=values,
        )
    assert caught.value.code == "state-aliased-in-construction"


def test_module_rom_and_state_may_not_share_a_mutable_root():
    source = """\
TABLE = [1, 2]

class M:
    def reset(self):
        self.buf = TABLE
    def process(self, a):
        self.buf[0] = a
        return {"y": TABLE[0]}
"""

    assert _native(source, a=3) == [{"y": 3}]
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=["y"])
    assert caught.value.code == "state-aliased-in-construction"


def test_copying_a_module_rom_does_not_invent_sharing():
    source = """\
TABLE = [1, 2]

class M:
    def reset(self):
        self.buf = list(TABLE)
    def process(self, a):
        self.buf[0] = a
        return {"y": TABLE[0]}
"""

    _assert_agrees(source, [{"a": 0}, {"a": 3}])


def test_the_construction_domain_separates_ir_values_from_native_only_values():
    source = """\
class M:
    def reset(self):
        self.flag = True
        self.meta = {"mode": 3}
        self.x = 7
    def process(self, a):
        return {"y": self.x + a}
"""

    facts = construction_facts(source, cls_name="M")
    # Boolean and integer construction values retain distinct semantic
    # carriers.  The mapping is faithfully witnessed but has no SolverIR value
    # representation.
    assert facts.values() == {"flag": True, "x": 7}
    assert facts.exact_values() == {
        "flag": True, "meta": {"mode": 3}, "x": 7}
    assert set(facts.unsupported()) == {"meta"}
    assert facts.opaque_fields == ()


@pytest.mark.parametrize("value", [
    None,
    True,
    7,
    1.5,
    "mode",
    b"bytes",
    bytearray(b"bytes"),
    [1, (2, 3)],
    {1, 2},
    frozenset({1, 2}),
    {"rows": [1, 2]},
    [float("nan")],
    {1: float("nan")},
])
def test_construction_and_native_values_share_one_exact_carrier(value):
    encoded = WireEncoder(200_000).encode(value)
    image = construction_module._canonical_carried(encoded)
    assert image == freeze(value)
    facts = ConstructionFacts((), (), (), (), (("value", image),))
    assert facts.matches("value", value)
    assert freeze(facts.exact_values()["value"]) == image


@pytest.mark.parametrize("value", [
    {float("nan")},
    {float("nan"): 1},
])
def test_identity_sensitive_native_values_are_outside_every_carrier(value):
    with pytest.raises(NativeValueError):
        freeze(value)
    with pytest.raises(NativeValueError):
        WireEncoder(200_000).encode(value)


@pytest.mark.parametrize("mutable", [
    [0],
    {0},
    {"x": 0},
    bytearray(b"x"),
])
def test_a_tree_carrier_rejects_repeated_mutable_identity(mutable):
    shared = [mutable, mutable]
    distinct = [mutable, type(mutable)(mutable)]
    with pytest.raises(NativeValueError, match="repeated mutable"):
        freeze(shared)
    with pytest.raises(NativeValueError, match="repeated mutable"):
        WireEncoder(200_000).encode(shared)
    # Equal contents without shared identity remain ordinary finite trees.
    freeze(distinct)


def test_a_task_value_copies_repeated_references_but_still_rejects_cycles():
    row = {"y": [1, 2]}
    encoded = ValueWireEncoder(200_000).encode([row, row])
    decoded = WireDecoder(200_000).decode(encoded)

    assert decoded == [{"y": [1, 2]}, {"y": [1, 2]}]
    assert decoded[0] is not decoded[1]
    assert decoded[0]["y"] is not decoded[1]["y"]

    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(NativeValueError, match="cyclic"):
        ValueWireEncoder(200_000).encode(cyclic)


def test_cross_field_aliasing_remains_owned_by_the_sharing_relation():
    value = [0]
    encoder = WireEncoder(200_000)
    assert encoder.encode(value) == encoder.encode(value)


def test_the_wire_budget_counts_scalar_bytes_in_both_directions():
    """One scalar is not one bounded protocol value.

    A byte string expands to two hexadecimal characters per byte, and a large
    integer must be converted to decimal by JSON.  Both used to pass a
    one-node budget and fail only after the isolated child tried to answer.
    """
    with pytest.raises(NativeValueLimit, match="byte budget"):
        WireEncoder(10, max_bytes=96).encode(b"x" * 40)
    with pytest.raises(NativeValueLimit, match="byte budget"):
        WireDecoder(10, max_bytes=96).decode(
            {"t": "bytes", "v": (b"x" * 40).hex()})

    huge = 1 << 20_000
    with pytest.raises(NativeValueLimit, match="byte budget"):
        WireEncoder(1, max_bytes=96).encode(huge)
    with pytest.raises(NativeValueLimit, match="byte budget"):
        WireDecoder(1, max_bytes=96).decode({"t": "int", "v": huge})


def test_native_values_have_no_default_aggregate_byte_or_node_quota():
    value = [b"x" * (5 << 20), list(range(200_001))]
    wire = WireEncoder(None).encode(value)
    assert WireDecoder(None).decode(wire) == value


def test_the_wire_byte_budget_is_aggregate_across_values():
    encoder = WireEncoder(10, max_bytes=120)
    encoder.encode(b"x" * 20)
    with pytest.raises(NativeValueLimit, match="byte budget"):
        encoder.encode(b"y" * 20)

    decoder = WireDecoder(10, max_bytes=120)
    decoder.decode({"t": "bytes", "v": (b"x" * 20).hex()})
    with pytest.raises(NativeValueLimit, match="byte budget"):
        decoder.decode({"t": "bytes", "v": (b"y" * 20).hex()})


def test_valid_wire_values_consume_the_same_budget_in_both_directions():
    values = [
        None,
        True,
        -(1 << 200),
        "quoted \" text \N{SNOWMAN}",
        b"bytes",
        bytearray(b"mutable bytes"),
        -0.0,
        [1, (2, 3), {"payload": b"x"}],
        {1, 2},
        frozenset({3, 4}),
    ]
    encoder = WireEncoder(1000, max_bytes=100_000)
    wires = [encoder.encode(value) for value in values]
    decoder = WireDecoder(1000, max_bytes=100_000)
    decoded = [decoder.decode(wire) for wire in wires]

    assert [freeze(value) for value in decoded] == [
        freeze(value) for value in values]
    assert encoder._remaining_nodes == decoder._remaining_nodes
    assert encoder._remaining_bytes == decoder._remaining_bytes
    assert sum(len(json.dumps(wire).encode("utf-8")) for wire in wires) \
        <= 100_000 - encoder._remaining_bytes


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, "8"])
def test_wire_byte_budgets_are_exact_positive_integers(budget):
    for codec in (WireEncoder, WireDecoder):
        with pytest.raises(ValueError, match="byte budget must be positive"):
            codec(10, max_bytes=budget)


@pytest.mark.parametrize("kind", ["set", "frozenset", "dict"])
def test_the_wire_decoder_cannot_reintroduce_an_identity_sensitive_key(kind):
    import struct

    nan = {"t": "float", "v": struct.pack("<d", float("nan")).hex()}
    encoded = ({"t": kind, "v": [nan]} if kind != "dict" else
               {"t": "dict", "v": [[nan, {"t": "int", "v": 1}]]})
    with pytest.raises(ConstructionUnavailable):
        construction_module._canonical_carried(encoded)


@pytest.mark.parametrize("initial", [
    "1.5",
    "'mode'",
    "{3}",
    "[[1.5]]",
])
def test_exact_native_values_do_not_become_solver_ir_state(initial):
    """Faithful construction transport is not an IR type conversion.

    Exact transport alone does not establish a supported state carrier.
    Other exact Python values may be reset-only constants, but a step-written
    field carrying one at entry cannot be silently reinterpreted as an integer
    state variable.
    """
    source = f"""\
class M:
    def reset(self):
        self.x = {initial}
    def process(self, a):
        self.x = a
        return {{"y": a}}
"""

    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[2], outputs=["y"])
    assert caught.value.code == "state-value-not-ir-carriable"


def test_optional_entry_state_keeps_presence_instead_of_becoming_integer_zero():
    source = '''class M:
    def reset(self): self.x = None
    def process(self, a):
        old = self.x
        self.x = a
        return {"y": 7 if old is None else old}
'''
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=3)
    state = None
    for value, expected in ((0, 7), (2, 0), (1, 2)):
        state, output, _, error = interp.run_step(proc, {'a': value}, state)
        assert not error
        assert output.transactions == [{'y': expected}]


@pytest.mark.parametrize('initial', ['{"mode": 3}', 'bytearray([3])'])
def test_supported_product_state_cannot_silently_change_into_a_scalar(initial):
    source = f'''class M:
    def reset(self): self.x = {initial}
    def process(self, a):
        self.x = a
        return {{"y": a}}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[2], outputs=['y'])
    assert caught.value.code == 'state-value-contract-disagrees'


@pytest.mark.parametrize("initial, expected, observed", [
    ("True", True, "int(old) + (a & 0)"),
    ("False", False, "int(old) + (a & 0)"),
    ("[True, False]", [True, False],
     "int(old[0]) + int(old[1]) + (a & 0)"),
])
def test_boolean_state_stays_boolean_until_an_explicit_integer_conversion(
        initial, expected, observed):
    source = f"""\
class M:
    def reset(self):
        self.x = {initial}
    def process(self, a):
        old = self.x
        self.x = {initial}
        return {{"y": {observed}}}
"""

    proc = lower_with_state_ranges(
        source, input_widths=[2], outputs=["y"])
    assert proc.initial["x"] == expected
    expected_type = (types.Array(2, types.BOOLEAN)
                     if isinstance(expected, list) else types.BOOLEAN)
    assert proc.types["x"] == expected_type


def test_non_ir_construction_values_are_valid_reset_only_constants():
    source = """\
class M:
    def reset(self):
        self.flag = True
        self.rate = 1.5
        self.name = "mode"
        self.meta = {"mode": 3}
        self.tags = {3}
        self.raw = bytearray([3])
    def process(self, a):
        return {"y": a}
"""

    proc = lower_with_state_ranges(
        source, input_widths=[2], outputs=["y"])
    assert proc.contract.how("flag") == ("constant", True)
    assert proc.contract.how("rate") == ("constant", 1.5)
    assert proc.contract.how("name") == ("constant", "mode")
    assert proc.contract.how("meta") == ("constant", {"mode": 3})
    assert proc.contract.how("tags") == ("constant", {3})
    assert proc.contract.how("raw") == ("constant", bytearray([3]))


def test_cached_construction_facts_are_deeply_immutable():
    source = """\
class M:
    def reset(self):
        self.buf = [[1, 2], [3, 4]]
    def process(self, a):
        return {"y": self.buf[a & 1][0]}
"""

    clear_construction_cache()
    facts = construction_facts(source, cls_name="M")
    first = facts.values()
    first["buf"][0][0] = 99
    assert facts.values()["buf"] == [[1, 2], [3, 4]]
    assert construction_facts(source, cls_name="M").values()["buf"] \
        == [[1, 2], [3, 4]]


def test_two_runs_compare_the_complete_sharing_image(monkeypatch):
    encoded = {"t": "list", "v": [
        {"t": "int", "v": 1}, {"t": "int", "v": 2}]}

    class Proxy:
        def __init__(self, report):
            self._carried = {"left": encoded, "right": encoded}
            self._opaque = {}
            self._report = report

        def reset(self):
            return None

        def _share_image(self):
            return {"fields": self._report, "modules": {}}

    reports = iter((
        {"left": {"ids": [1], "opaque": False},
         "right": {"ids": [2], "opaque": False}},
        {"left": {"ids": [3], "opaque": False},
         "right": {"ids": [3], "opaque": False}},
    ))

    class Factory:
        def __call__(self):
            return Proxy(next(reports))

        def close(self):
            return None

    from behave.sim.behavior import isolated

    monkeypatch.setattr(isolated, "isolated_model",
                        lambda *_args, **_kwargs: Factory())
    with pytest.raises(ConstructionUnavailable, match="sharing differ"):
        construction_module._build("source", "behavior.py", "M")


@pytest.mark.parametrize("carried, opaque, fields, modules, facts, message", [
    (
        {"left": {"t": "int", "v": 1},
         "right": {"t": "int", "v": 2}},
        {},
        {"left": {"ids": [], "opaque": False}},
        {}, {}, "value and sharing field domains disagree",
    ),
    (
        {"left": {"t": "int", "v": 1}},
        {"left": "opaque"},
        {"left": {"ids": [], "opaque": False}},
        {}, {}, "both carried and opaque",
    ),
    (
        {"left": {"t": "int", "v": 1}},
        {},
        {"left": {"ids": [], "opaque": False},
         "extra": {"ids": [], "opaque": False}},
        {}, {}, "value and sharing field domains disagree",
    ),
    (
        {"left": {"t": "int", "v": 1}},
        {},
        {"left": {"ids": [], "opaque": False}},
        {"EXTRA": {"ids": [], "opaque": False}},
        {}, "module and sharing domains disagree",
    ),
])
def test_construction_protocol_domains_must_correspond_exactly(
        carried, opaque, fields, modules, facts, message):
    class Proxy:
        def __init__(self):
            self._carried = carried
            self._opaque = opaque
            self._facts = facts

        def _share_image(self):
            return {"fields": fields, "modules": modules}

    with pytest.raises(ConstructionUnavailable, match=message):
        construction_module._image(Proxy())


@pytest.mark.parametrize("encoded", [
    {"t": "none", "v": 0},
    {"t": "bool", "v": 1},
    {"t": "int", "v": True},
    {"t": "float", "v": "00"},
    {"t": "bytes", "v": "not-hex"},
    {"t": "dict", "v": [[{"t": "int", "v": 1}]]},
])
def test_the_construction_protocol_validates_scalar_payloads(encoded):
    with pytest.raises(ConstructionUnavailable, match="malformed"):
        construction_module._canonical_carried(encoded)


def test_failed_construction_is_retried_but_success_is_single_flight(
        monkeypatch):
    # This test owns the in-process single-flight table.  The production-wide
    # construction capacity is exercised separately; sharing its four node-local
    # slots with other xdist workers would make this test measure suite load instead.
    monkeypatch.setattr(
        construction_module, "_BUILD_SLOTS", contextlib.nullcontext())
    facts = ConstructionFacts((("x", ("int", 1)),), (), (), ())
    calls = []

    def flaky(*_args):
        calls.append("flaky")
        if len(calls) == 1:
            raise ConstructionUnavailable("transient")
        return facts

    clear_construction_cache()
    monkeypatch.setattr(construction_module, "_build", flaky)
    with pytest.raises(ConstructionUnavailable, match="transient"):
        construction_facts("source-a", cls_name="M")
    assert construction_facts("source-a", cls_name="M") is facts
    assert len(calls) == 2

    calls.clear()
    clear_construction_cache()

    def slow(*_args):
        calls.append("slow")
        time.sleep(0.05)
        return facts

    monkeypatch.setattr(construction_module, "_build", slow)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(
            lambda _index: construction_facts("source-b", cls_name="M"),
            range(8)))
    assert all(item is facts for item in got)
    assert calls == ["slow"]

    # Single-flight is per semantic key. Independent tasks must not queue
    # behind one slow construction, which would serialize a population run.
    clear_construction_cache()
    both_started = threading.Event()
    count_lock = threading.Lock()
    active = []

    def parallel(source, *_args):
        with count_lock:
            active.append(source)
            if len(active) == 2:
                both_started.set()
        assert both_started.wait(5)
        return facts

    monkeypatch.setattr(construction_module, "_build", parallel)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        got = list(pool.map(
            lambda source: construction_facts(source, cls_name="M"),
            ("source-d", "source-e")))
    assert got == [facts, facts]

    # Cancellation is outside ``Exception``. It must still resolve/remove the
    # in-flight slot so this key can be tried again instead of blocking forever.
    clear_construction_cache()
    attempts = []

    def cancelled(*_args):
        attempts.append("cancelled")
        if len(attempts) == 1:
            raise KeyboardInterrupt
        return facts

    monkeypatch.setattr(construction_module, "_build", cancelled)
    with pytest.raises(KeyboardInterrupt):
        construction_facts("source-f", cls_name="M")
    assert construction_facts("source-f", cls_name="M") is facts
    assert attempts == ["cancelled", "cancelled"]


def test_two_pass_lowering_acquires_one_construction_authority(monkeypatch):
    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": a}
"""
    facts = ConstructionFacts((), (), (), ())
    calls = []

    def acquire(*_args, **_kwargs):
        calls.append("construction")
        return facts

    monkeypatch.setattr(lower_prepare, "construction_facts", acquire)
    proc = lower_with_state_ranges(
        source, input_widths=[2], outputs=["y"], bound=1)

    assert proc.outputs == ["y"]
    assert calls == ["construction"]


def test_an_oversized_fact_is_used_but_not_retained(monkeypatch):
    facts = ConstructionFacts((("x", ("int", 1)),), (), (), ())
    calls = []

    def build(*_args):
        calls.append("build")
        return facts

    clear_construction_cache()
    monkeypatch.setattr(construction_module, "_build", build)
    monkeypatch.setattr(
        construction_module, "_facts_weight",
        lambda *_args: construction_module._CACHE_WEIGHT_LIMIT + 1)
    assert construction_facts("source-c", cls_name="M") is facts
    assert construction_facts("source-c", cls_name="M") is facts
    assert calls == ["build", "build"]


def test_a_cache_weight_failure_does_not_poison_single_flight(monkeypatch):
    facts = ConstructionFacts((("x", ("int", 1)),), (), (), ())
    builds = []
    weights = []
    real_weight = construction_module._facts_weight

    def build(*_args):
        builds.append("build")
        return facts

    def weight(*args):
        weights.append("weight")
        if len(weights) == 1:
            raise RuntimeError("weight failed")
        return real_weight(*args)

    clear_construction_cache()
    monkeypatch.setattr(construction_module, "_build", build)
    monkeypatch.setattr(construction_module, "_facts_weight", weight)
    with pytest.raises(ConstructionUnavailable, match="weight failed"):
        construction_facts("source-weight-failure", cls_name="M")
    assert construction_facts("source-weight-failure", cls_name="M") \
        is facts
    assert builds == ["build", "build"]


def test_distinct_constructions_share_one_global_capacity_bound(monkeypatch):
    """Per-key single-flight is not an aggregate resource bound.

    Every distinct key owns a child pair.  Limit those owners globally while
    leaving the existing same-key Future outside the semaphore.
    """
    facts = ConstructionFacts((("x", ("int", 1)),), (), (), ())
    entered_two = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def build(*_args):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                entered_two.set()
        assert entered_two.wait(5)
        time.sleep(0.03)
        with lock:
            active -= 1
        return facts

    clear_construction_cache()
    monkeypatch.setattr(
        construction_module, "_BUILD_SLOTS", threading.BoundedSemaphore(2))
    monkeypatch.setattr(construction_module, "_build", build)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(
            lambda index: construction_facts(
                f"distinct-source-{index}", cls_name="M"),
            range(8)))
    assert got == [facts] * 8
    assert maximum == 2


def test_construction_capacity_is_shared_across_worker_processes():
    context = multiprocessing.get_context("fork")
    namespace = f"test-{time.time_ns()}"
    active = context.Value("i", 0)
    maximum = context.Value("i", 0)
    lock = context.Lock()
    overlap = context.Event()
    workers = [context.Process(
        target=_hold_process_build_slot,
        args=(namespace, active, maximum, lock, overlap))
        for _index in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert [worker.exitcode for worker in workers] == [0] * len(workers)
    assert maximum.value == 2
    assert active.value == 0


def test_a_process_exit_releases_its_construction_slot():
    context = multiprocessing.get_context("fork")
    namespace = f"exit-test-{time.time_ns()}"
    acquired = context.Event()
    worker = context.Process(
        target=_exit_while_holding_process_build_slot,
        args=(namespace, acquired))
    worker.start()
    assert acquired.wait(5)
    worker.join(5)
    assert worker.exitcode == 0

    # The lock file persists by design, but carries no stale state: the
    # kernel released its flock when the holder exited.
    slots = construction_module._BuildCapacity(1, namespace=namespace)
    with slots:
        fd = slots._held.fds[-1]
        assert not os.get_inheritable(fd), "slot locks must be CLOEXEC"


def test_a_forked_worker_does_not_retain_the_parents_slot_descriptor():
    context = multiprocessing.get_context("fork")
    namespace = f"fork-test-{time.time_ns()}"
    slots = construction_module._BuildCapacity(1, namespace=namespace)
    acquired = context.Event()

    with slots:
        worker = context.Process(
            target=_acquire_inherited_build_slots,
            args=(slots, acquired))
        worker.start()
        assert not acquired.wait(0.2)
    assert acquired.wait(5)
    worker.join(5)
    assert worker.exitcode == 0


def test_same_key_waiters_do_not_consume_build_slots(monkeypatch):
    facts = ConstructionFacts((("x", ("int", 1)),), (), (), ())

    class CountingSlots:
        def __init__(self):
            self.entries = 0
            self.lock = threading.Lock()

        def __enter__(self):
            with self.lock:
                self.entries += 1
            return self

        def __exit__(self, *_exc):
            return None

    slots = CountingSlots()

    def slow(*_args):
        time.sleep(0.05)
        return facts

    clear_construction_cache()
    monkeypatch.setattr(construction_module, "_BUILD_SLOTS", slots)
    monkeypatch.setattr(construction_module, "_build", slow)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(
            lambda _index: construction_facts(
                "one-semantic-key", cls_name="M"),
            range(8)))
    assert got == [facts] * 8
    assert slots.entries == 1


def test_a_forked_child_does_not_wait_on_the_parents_pending_future(monkeypatch):
    # Isolate the fork-state invariant from the independent aggregate build limit.
    monkeypatch.setattr(
        construction_module, "_BUILD_SLOTS", contextlib.nullcontext())
    context = multiprocessing.get_context("fork")
    source = f"fork-pending-{time.time_ns()}"
    dependency_identity = construction_module._dependency_identity()
    key = (source, "behavior.py", "M", CONSTRUCTION_VERSION,
           dependency_identity)
    pending = concurrent.futures.Future()
    with construction_module._CACHE_LOCK:
        construction_module._INFLIGHT[key] = pending

    parent, child = context.Pipe(duplex=False)
    worker = context.Process(target=_build_after_fork, args=(source, child))
    try:
        worker.start()
        child.close()
        worker.join(5)
        if worker.is_alive():
            worker.kill()
            worker.join(5)
            pytest.fail("the child waited on a Future owned by its parent")
        assert worker.exitcode == 0
        assert parent.poll(1)
        assert parent.recv() == ("ok", {"x": 1})
        assert not pending.done()
    finally:
        parent.close()
        child.close()
        if worker.is_alive():
            worker.kill()
            worker.join(5)
        with construction_module._CACHE_LOCK:
            construction_module._INFLIGHT.pop(key, None)


def test_a_forked_child_does_not_inherit_another_threads_cache_lock(monkeypatch):
    # This test owns the cache-lock invariant, not the independent aggregate
    # construction-capacity policy.  Under xdist another process may hold every
    # cross-process build slot for longer than this test's child deadline, which
    # used to turn capacity contention into a false cache-lock failure.
    monkeypatch.setattr(
        construction_module, "_BUILD_SLOTS", contextlib.nullcontext())
    context = multiprocessing.get_context("fork")
    source = f"fork-lock-{time.time_ns()}"
    acquired = threading.Event()

    def hold_cache_lock():
        with construction_module._CACHE_LOCK:
            acquired.set()
            time.sleep(0.2)

    holder = threading.Thread(target=hold_cache_lock)
    holder.start()
    assert acquired.wait(1)

    parent, child = context.Pipe(duplex=False)
    worker = context.Process(target=_build_after_fork, args=(source, child))
    try:
        # The before-fork hook waits for the owner and copies a coherent
        # table.  The child then gets a fresh process-local lock.
        worker.start()
        child.close()
        worker.join(5)
        if worker.is_alive():
            worker.kill()
            worker.join(5)
            pytest.fail("the child inherited a lock held by a vanished thread")
        assert worker.exitcode == 0
        assert parent.poll(1)
        assert parent.recv() == ("ok", {"x": 1})
    finally:
        parent.close()
        child.close()
        if worker.is_alive():
            worker.kill()
            worker.join(5)
        holder.join(2)
        assert not holder.is_alive()


def test_cache_weight_counts_large_scalar_and_text_payloads():
    small = ConstructionFacts(
        (("x", ("int", 1)),), (), (), (),
        (("raw", ("bytes", "")),))
    huge_integer = 1 << 200_000
    large = ConstructionFacts(
        (("x", ("int", huge_integer)),), (), (), (),
        (("raw", ("bytes", "ab" * 20_000)),))

    small_key = ("a" * 2000, "f.py", "M", CONSTRUCTION_VERSION, "d" * 64)
    large_key = ("\U0001f642" * 2000, "f.py", "M", CONSTRUCTION_VERSION,
                 "d" * 64)
    small_weight = construction_module._facts_weight(small, small_key)
    large_weight = construction_module._facts_weight(
        large, large_key)
    # The retained representation contains the integer limbs, the hex text
    # used for bytes, and Python's wider Unicode source storage.  Charging a
    # fixed amount per node would make all three differences disappear.
    assert large_weight - small_weight >= sys.getsizeof(huge_integer)
    assert large_weight - small_weight >= 40_000


def test_cache_weight_charges_every_retained_key_field():
    facts = ConstructionFacts((('x', ('int', 1)),), (), (), ())
    small = ("source", "f", "M", CONSTRUCTION_VERSION, "d")
    large_parts = tuple(character * 20_000
                        for character in ("s", "f", "M", "d"))
    large = (large_parts[0], large_parts[1], large_parts[2],
             CONSTRUCTION_VERSION, large_parts[3])

    small_weight = construction_module._facts_weight(facts, small)
    large_weight = construction_module._facts_weight(facts, large)
    retained_text_growth = sum(sys.getsizeof(part) for part in large_parts) \
        - sum(sys.getsizeof(part) for part in ("source", "f", "M", "d"))
    assert large_weight - small_weight >= retained_text_growth


def test_the_dependency_key_and_both_children_use_one_byte_snapshot():
    payload = b"def special():\n    return [[7, 8], [9, 10]]\n"
    source = """\
from fp_lib import special

class M:
    def reset(self):
        self.rows = special()
    def process(self, a):
        return {"y": self.rows[a & 1][0]}
"""

    # The installed fp_lib has no ``special``.  Success therefore proves
    # both construction runs imported the one materialised payload rather
    # than reopening whichever dependency happened to occupy the live path.
    facts = construction_module._build(
        source, "behavior.py", "M", dependency_payload=payload)
    assert facts.values()["rows"] == [[7, 8], [9, 10]]


def test_construction_passes_the_bytes_that_formed_its_dependency_key(
        monkeypatch):
    facts = ConstructionFacts((("x", ("int", 1)),), (), (), ())
    payload = b"the exact dependency bytes"
    observed = []

    monkeypatch.setattr(
        construction_module, "_dependency_snapshot",
        lambda: ("fixed-digest", payload))

    def build(_source, _file, _cls_name, dependency_payload):
        observed.append(dependency_payload)
        return facts

    clear_construction_cache()
    monkeypatch.setattr(construction_module, "_build", build)
    assert construction_facts("source-toctou", cls_name="M") is facts
    assert observed == [payload]


def test_sharing_normalization_is_linear_without_aliases_and_bounded_with_them():
    disjoint = {
        f"f{index}": {"ids": [index + 1], "opaque": False}
        for index in range(4000)}
    fields, pairs, opaque = construction_module._share_signature(disjoint)
    assert len(fields) == 4000
    assert pairs == ()
    assert opaque == ()

    too_many = {
        f"f{index}": {"ids": [1], "opaque": False}
        for index in range(500)}
    with pytest.raises(ConstructionUnavailable, match="relation is too large"):
        construction_module._share_signature(too_many)


def test_sharing_normalization_bounds_all_identity_entries(monkeypatch):
    monkeypatch.setattr(construction_module, "_SHARE_ID_LIMIT", 3)
    report = {
        "left": {"ids": [1, 2], "opaque": False},
        "right": {"ids": [3, 4], "opaque": False},
    }
    with pytest.raises(ConstructionUnavailable, match="too many identities"):
        construction_module._share_signature(report)


@pytest.mark.parametrize("identity", [0, -1, True, 1.5, "1"])
def test_sharing_object_identities_are_exact_positive_integers(identity):
    report = {"field": {"ids": [identity], "opaque": False}}
    with pytest.raises(ConstructionUnavailable,
                       match="malformed construction object identity"):
        construction_module._share_signature(report)
