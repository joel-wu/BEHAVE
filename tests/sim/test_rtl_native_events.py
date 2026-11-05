"""Real compiled collector ABI, independent of either production Driver.

The C++ test library is judge-owned transport code, not candidate HDL. Native
RTL noninterference and full formal-hit comparison live in test_rtl_source_plan.
"""

import ctypes
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest

from behave.sim.core.catalogue import OccurrenceKey
from behave.sim.core.failure import FailureDisposition, FailureOrigin, FailureRaised
from behave.sim.rtl.native_events import MAX_EVENTS, NativeEventCollector, NativeEventPlan, prepare_native_events
from behave.sim.rtl.source_bindings import BoundOccurrence, EventBindings, EventCoordinate
from behave.sim.rtl.source_plan import plan_source

pytestmark = pytest.mark.eda("g++")


def _plan(*, count=130, limit=5000):
    entries = tuple(BoundOccurrence(OccurrenceKey("test", [i], "action"), f"goal:{i}", i, ())
                    for i in range(count))
    return NativeEventPlan(EventBindings(count, entries), "a" * 64, limit)


def _load(tmp_path, plan):
    source = tmp_path / "collector.cpp"
    source.write_text(plan.collector_source())
    target = tmp_path / "collector.so"
    completed = subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
         "-pthread", "-O2", str(source), "-o", str(target)],
        capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    library = ctypes.CDLL(str(target))
    library.__behave_native_event_hit.argtypes = [ctypes.c_uint]
    library.__behave_native_event_hit.restype = None
    library.__behave_native_event_scope_id.argtypes = [ctypes.c_char_p]
    library.__behave_native_event_scope_id.restype = ctypes.c_uint
    library.__behave_native_event_source_hit.argtypes = [ctypes.c_uint, ctypes.c_uint]
    library.__behave_native_event_source_hit.restype = None
    return library


def _scoped_plan():
    coordinates = tuple(EventCoordinate(scope, source) for scope in
                        ("TOP.M.left", "TOP.M.right") for source in (2, 7))
    return replace(_plan(count=4), bindings=replace(_plan(count=4).bindings, coordinates=coordinates))


@pytest.mark.parametrize("root", ["TOP", "", "runner"])
def test_source_scope_tokens_route_without_broadcast_or_renumbering(tmp_path, root):
    plan = replace(_scoped_plan(), scope_root=root)
    prefix = root + "." if root else ""
    library = _load(tmp_path, plan)
    left = library.__behave_native_event_scope_id((prefix + "M.left").encode())
    right = library.__behave_native_event_scope_id((prefix + "M.right").encode())
    assert left != right
    # Routing has no simulator-scope dependency: the standalone collector
    # compiles without DPI headers, symbols, or a synthetic scope shim.
    collector = NativeEventCollector(plan, library=library)
    collector.begin()
    library.__behave_native_event_source_hit(7, left)
    library.__behave_native_event_source_hit(2, right)
    interval = collector.end()
    assert interval.events == {1, 2} and interval.callbacks == 2
    collector.finish()


def test_an_unused_source_scope_does_not_invent_an_event(tmp_path):
    plan = _scoped_plan()
    library = _load(tmp_path, plan)
    unused = library.__behave_native_event_scope_id(b"TOP.M.unused")
    assert unused == 2**32-1
    collector = NativeEventCollector(plan, library=library)
    collector.begin()
    assert collector.end().events == set()
    collector.begin()
    library.__behave_native_event_source_hit(2, unused)
    _failure(collector.end, "unknown-context")


@pytest.mark.parametrize("root", ["", "runner"])
def test_root_is_a_bound_launch_parameter_not_an_optional_callback_prefix(tmp_path, root):
    plan = replace(_scoped_plan(), scope_root=root)
    assert plan.identity != _scoped_plan().identity
    assert plan.bindings == _scoped_plan().bindings
    assert NativeEventPlan.from_dict(plan.as_dict()) == plan
    changed = plan.as_dict()
    changed["scope_root"] = "TOP"
    with pytest.raises(ValueError, match="identity"):
        NativeEventPlan.from_dict(changed)
    library = _load(tmp_path, plan)
    collector = NativeEventCollector(plan, library=library)
    collector.begin()
    scope = library.__behave_native_event_scope_id(b"TOP.M.left")
    library.__behave_native_event_source_hit(2, scope)
    _failure(collector.end, "unknown-context")


@pytest.mark.parametrize("root", [None, True, 1, "TOP.M", "a\nb", 'a"b'])
def test_scope_root_is_one_closed_identifier(root):
    with pytest.raises(ValueError, match="scope root"):
        replace(_scoped_plan(), scope_root=root)


@pytest.mark.parametrize("scope,event", [
    (None, 2), (b"M.left", 2), (b"TOP.Other.left", 2), (b"TOP.M.left.extra", 2),
    (b"TOP.M.left", 0), (b"TOP.M.left", 2**32 - 1),
])
def test_unknown_context_or_source_event_poison_the_scoped_collector(tmp_path, scope, event):
    library = _load(tmp_path, _scoped_plan())
    collector = NativeEventCollector(_scoped_plan(), library=library)
    collector.begin()
    token = library.__behave_native_event_scope_id(scope)
    library.__behave_native_event_source_hit(event, token)
    _failure(collector.end, "unknown-context")
    token = library.__behave_native_event_scope_id(b"TOP.M.left")
    library.__behave_native_event_source_hit(2, token)
    assert library.behave_native_events_begin_v1(0) == 5


@pytest.mark.parametrize("coordinates", [
    (EventCoordinate("TOP.M.a", True),),
    (EventCoordinate("TOP.M.a", -1),),
    (EventCoordinate("TOP.M.a", MAX_EVENTS),),
    (EventCoordinate("other.M.a", 0),),
    (EventCoordinate('TOP.M.a"injected', 0),),
    (EventCoordinate("TOP.M.a", 0),) * 2,
    (EventCoordinate("TOP.M.z", 0), EventCoordinate("TOP.M.a", 0)),
])
def test_context_coordinates_are_closed_unique_and_ordered(coordinates):
    with pytest.raises(ValueError, match="coordinates"):
        NativeEventPlan(EventBindings(len(coordinates), (), coordinates), "a" * 64, 1)


def _failure(operation, reason):
    with pytest.raises(FailureRaised, match=reason) as caught:
        operation()
    assert caught.value.failure.origin is FailureOrigin.VERIFIER
    assert caught.value.failure.disposition is FailureDisposition.NO_RESULT


def test_compiled_intervals_retain_all_bits_and_seal(tmp_path):
    plan = _plan()
    library = _load(tmp_path, plan)
    collector = NativeEventCollector(plan, library=library)
    observed = {0, 63, 64, 65, 127, 128, 129}
    for sequence, ids in enumerate((observed, set(), {4})):
        collector.begin()
        for event in sorted(ids) * 3:
            library.__behave_native_event_hit(event)
        interval = collector.end()
        assert interval.sequence == sequence
        assert interval.callbacks == 3 * len(ids)
        assert interval.events == ids
        assert interval.hits.goals == {f"goal:{i}" for i in ids}
        assert interval.hits.occurrences == {plan.bindings.occurrences[i].occurrence for i in ids}
    collector.finish()
    _failure(collector.begin, "interval-protocol")


@pytest.mark.parametrize("when", ["before", "between", "after-seal"])
def test_unframed_callbacks_cannot_be_cleared_by_begin_or_retry(tmp_path, when):
    plan = _plan(count=1)
    library = _load(tmp_path, plan)
    collector = NativeEventCollector(plan, library=library)
    if when != "before":
        collector.begin()
        collector.end()
    if when == "after-seal":
        collector.finish()
    library.__behave_native_event_hit(0)
    # Check the compiled side too: creating a second Python decoder cannot
    # reset the actual collector's sticky fault or its interval counter.
    assert library.behave_native_events_begin_v1(0) == 1
    assert library.behave_native_events_finish_v1() == 1
    if when != "after-seal":
        _failure(collector.begin, "event-outside-interval")


@pytest.mark.parametrize("bad_id", [1, 2**32 - 1])
def test_unknown_event_is_not_an_empty_vector(tmp_path, bad_id):
    plan = _plan(count=1)
    library = _load(tmp_path, plan)
    collector = NativeEventCollector(plan, library=library)
    collector.begin()
    library.__behave_native_event_hit(bad_id)
    _failure(collector.end, "unknown-event")
    assert library.behave_native_events_begin_v1(0) == 2


def test_repeated_hits_count_against_callback_budget(tmp_path):
    plan = _plan(count=1, limit=3)
    library = _load(tmp_path, plan)
    collector = NativeEventCollector(plan, library=library)
    collector.begin()
    for _ in range(3):
        library.__behave_native_event_hit(0)
    interval = collector.end()
    assert interval.callbacks == 3 and interval.events == {0}
    collector.begin()
    for _ in range(4):
        library.__behave_native_event_hit(0)
    _failure(collector.end, "callback-budget-exceeded")
    assert library.behave_native_events_finish_v1() == 3


@pytest.mark.parametrize("scoped", [False, True])
def test_disabled_callback_quota_keeps_counting_and_all_hits(tmp_path, scoped):
    plan = replace(_scoped_plan() if scoped else _plan(count=2), callback_limit=None)
    assert NativeEventPlan.from_dict(plan.as_dict()) == plan
    library = _load(tmp_path, plan)
    collector = NativeEventCollector(plan, library=library)
    collector.begin()
    callbacks = 1_000_001  # Exceed the retired default, not a new production limit.
    if scoped:
        context = library.__behave_native_event_scope_id(b"TOP.M.left")
        for _ in range(callbacks):
            library.__behave_native_event_source_hit(2, context)
        library.__behave_native_event_source_hit(7, context)
    else:
        for _ in range(callbacks):
            library.__behave_native_event_hit(0)
        library.__behave_native_event_hit(1)
    interval = collector.end()
    assert interval.callbacks == callbacks + 1
    assert interval.events == {0, 1}
    assert interval.hits.goals == {"goal:0", "goal:1"}
    collector.finish()


@pytest.mark.parametrize("operation", ["skipped-sequence", "duplicate-begin", "missing-begin",
                                       "wrong-end", "wrong-buffer", "null-buffer",
                                       "null-count", "open-finish", "empty-finish"])
def test_compiled_protocol_faults_are_sticky(tmp_path, operation):
    plan = _plan(count=1)
    library = _load(tmp_path, plan)
    NativeEventCollector(plan, library=library)  # Declare the real ABI signatures.
    words, callbacks = (ctypes.c_uint64 * 1)(), ctypes.c_uint64()
    if operation not in {"missing-begin", "empty-finish", "skipped-sequence"}:
        assert library.behave_native_events_begin_v1(0) == 0
    if operation == "skipped-sequence":
        status = library.behave_native_events_begin_v1(1)
    elif operation == "duplicate-begin":
        status = library.behave_native_events_begin_v1(0)
    elif operation in {"open-finish", "empty-finish"}:
        status = library.behave_native_events_finish_v1()
    else:
        status = library.behave_native_events_end_v1(
            int(operation == "wrong-end"), None if operation == "null-buffer" else words,
            2 if operation == "wrong-buffer" else 1,
            None if operation == "null-count" else ctypes.byref(callbacks))
    assert status == 4
    assert library.behave_native_events_begin_v1(0) == 4
    assert library.behave_native_events_finish_v1() == 4


def test_concurrent_callbacks_do_not_lose_bits_or_counts(tmp_path):
    plan = _plan(count=130)
    library = _load(tmp_path, plan)
    collector = NativeEventCollector(plan, library=library)
    collector.begin()

    def emit(offset):
        for index in range(1000):
            library.__behave_native_event_hit((index + offset) % 130)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(emit, range(4)))
    interval = collector.end()
    assert interval.callbacks == 4000
    assert interval.events == set(range(130))
    collector.finish()


def test_broken_control_ancestry_is_a_verifier_failure(tmp_path):
    plan = _plan(count=2)
    parent, child = plan.bindings.occurrences
    plan = replace(plan, bindings=EventBindings(2, (parent, replace(child, ancestors=(0,)))))
    library = _load(tmp_path, plan)
    collector = NativeEventCollector(plan, library=library)
    collector.begin()
    library.__behave_native_event_hit(1)
    _failure(collector.end, "invalid-event-vector")
    _failure(collector.begin, "interval-protocol")


def test_identity_covers_mapping_budget_and_build(tmp_path):
    plan = _plan(count=1)
    item, = plan.bindings.occurrences
    variants = [
        replace(plan, build_sha256="b" * 64),
        replace(plan, callback_limit=plan.callback_limit + 1),
        replace(plan, bindings=EventBindings(1, (replace(item, goal="other"),))),
        replace(plan, bindings=EventBindings(2, (item,))),
    ]
    assert len({p.identity for p in [plan, *variants]}) == 5
    library = _load(tmp_path, plan)
    for wrong in variants:
        _failure(lambda wrong=wrong: NativeEventCollector(wrong, library=library), "identity-mismatch")


@pytest.mark.parametrize("count,limit", [(True, 1), (-1, 1), (MAX_EVENTS + 1, 1),
                                        (0, 0), (0, True), (0, 2**63)])
def test_invalid_compile_bounds_are_rejected(count, limit):
    with pytest.raises(ValueError):
        NativeEventPlan(EventBindings(count, ()), "a" * 64, limit)


def test_empty_catalogue_still_requires_a_complete_interval(tmp_path):
    plan = _plan(count=0)
    collector = NativeEventCollector(plan, library=_load(tmp_path, plan))
    collector.begin()
    interval = collector.end()
    assert not interval.events and not interval.hits.goals and interval.callbacks == 0
    collector.finish()


def _fake_word_library(plan, word, count):
    def end(sequence, words, size, callbacks):
        words[0] = word
        callbacks._obj.value = count
        return 0

    return SimpleNamespace(
        behave_native_events_identity_v1=lambda: plan.identity.encode("ascii"),
        behave_native_events_begin_v1=lambda sequence: 0,
        behave_native_events_end_v1=end,
        behave_native_events_finish_v1=lambda: 0,
        behave_native_events_checkpoint_v1=lambda sequence: 0,
    )


def test_live_collector_keeps_named_views_lazy_without_losing_events(monkeypatch):
    from behave.sim.core.hit_vector import HitVectorCodec

    plan = _plan(count=1)
    collector = NativeEventCollector(plan, library=_fake_word_library(plan, 1, 3))
    collector.begin()

    def forbidden(*args):
        raise AssertionError("collector expanded named sets on its live path")

    with monkeypatch.context() as patch:
        patch.setattr(EventBindings, "decode", forbidden)
        interval = collector.end()
        assert interval.callbacks == 3
        assert HitVectorCodec(plan.bindings.goal_catalogue).from_bits(interval.goal_bits) == (
            HitVectorCodec(plan.bindings.goal_catalogue).vector(["goal:0"]))
        assert "events" not in interval.__dict__ and "hits" not in interval.__dict__
        collector.finish()
    assert interval.events == {0}
    assert interval.hits.goals == {"goal:0"}
    assert interval.hits.occurrences == {plan.bindings.occurrences[0].occurrence}


@pytest.mark.parametrize("word,count,code", [
    (1 << 63, 1, "invalid-event-vector"),
    (1 << 8, 1, "invalid-event-vector"),
    (1, 0, "invalid-callback-count"),
    (0, 1, "invalid-callback-count"),
    (1, 6, "invalid-callback-count"),
])
def test_bitmap_projection_checks_entire_abi_word_and_callback_count(word, count, code):
    plan = _plan(count=1, limit=5)
    # A prior valid projection cannot license a malformed later collection.
    plan.bindings.decode_bitmap(b"\x01")
    collector = NativeEventCollector(plan, library=_fake_word_library(plan, word, count))
    collector.begin()
    _failure(collector.end, code)
    _failure(collector.begin, "interval-protocol")


def test_repeated_bitmap_keeps_distinct_interval_counts_and_sequence():
    plan = _plan(count=1, limit=5)
    library = _fake_word_library(plan, 1, 2)
    collector = NativeEventCollector(plan, library=library)
    collector.begin()
    first = collector.end()
    library.behave_native_events_end_v1 = _fake_word_library(plan, 1, 4).behave_native_events_end_v1
    # The collector binds the ABI callable at initialization. Use a second
    # collector sharing only the immutable projection plan, not interval state.
    other = NativeEventCollector(plan, library=library)
    other.begin()
    second = other.end()
    collector.begin()
    third = collector.end()
    assert first.goal_bits is second.goal_bits is third.goal_bits
    assert (first.callbacks, second.callbacks, third.callbacks) == (2, 4, 2)
    assert (first.sequence, second.sequence, third.sequence) == (0, 0, 1)
    collector.finish()
    other.finish()


@pytest.mark.eda("slang", "yosys")
def test_preparation_binds_source_toolchain_schedule_and_template(tmp_path, monkeypatch):
    from behave.sim.rtl import native_events

    from .test_rtl_source_plan import FIXTURES, _compile, _parse

    source = FIXTURES["inline"]
    plan = plan_source(source, _parse(tmp_path, source))
    original = _compile(tmp_path / "original", source)
    annotated = _compile(tmp_path / "annotated", plan.compiler.text)
    args = {"filename": "design.sv", "toolchain_sha256": "1" * 64,
            "execution_sha256": "2" * 64, "callback_limit": 1000}

    def prepare(**changes):
        return prepare_native_events(plan, original, annotated, **(args | changes))

    compiled = prepare()
    assert compiled == prepare()
    assert compiled.identity != prepare(toolchain_sha256="3" * 64).identity
    assert compiled.identity != prepare(execution_sha256="3" * 64).identity
    bound = prepare(catalogue_sha256="4" * 64)
    assert compiled.identity != bound.identity
    assert bound.identity == prepare(catalogue_sha256="4" * 64).identity
    assert bound.identity != prepare(catalogue_sha256="5" * 64).identity
    with monkeypatch.context() as context:
        context.setattr(native_events, "VERILATOR_FLAGS", ())
        assert compiled.identity != prepare().identity
    template = tmp_path / "changed.cpp.tmpl"
    template.write_text(native_events._TEMPLATE.read_text() + "\n// changed transport\n")
    monkeypatch.setattr(native_events, "_TEMPLATE", template)
    assert compiled.identity != prepare().identity
    # The existing plan retains the same source bytes it authenticated.
    assert "changed transport" not in compiled.collector_source()
    with pytest.raises(ValueError):
        prepare(toolchain_sha256="injected\nsource")
    with pytest.raises(ValueError):
        prepare(catalogue_sha256="not-a-catalogue-digest")
    with pytest.raises(ValueError, match="compiler/source|RTLIL"):
        prepare_native_events(plan, original, annotated.replace("sync posedge", "sync negedge"), **args)


def test_native_makefile_keeps_callbacks_with_their_process(tmp_path):
    from behave.sim.harness import generate_makefile
    from behave.sim.rtl.native_events import VERILATOR_FLAGS

    generate_makefile(tmp_path, "M", coverage=False, native_events=True)
    makefile = (tmp_path / "Makefile").read_text()
    assert VERILATOR_FLAGS == ("-fno-split", "--timing", "-fno-inline-funcs-eager",
                              "-fno-func-opt-split-cat", "-fno-dedup", "-fno-reorder")
    assert "EXTRA_ARGS += " + " ".join(VERILATOR_FLAGS) + " /work/collector.cpp" in makefile
    assert "--coverage" not in makefile
    generate_makefile(tmp_path, "M", coverage=False)
    plain = (tmp_path / "Makefile").read_text()
    assert "-fno-split" not in plain and "collector.cpp" not in plain
    assert "-fno-dedup" not in plain and "-fno-reorder" not in plain
