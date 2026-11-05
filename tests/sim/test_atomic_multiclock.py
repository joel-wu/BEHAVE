import json

import pytest

from behave.sim.behavior.isolated import Broken, IsolatedModel
from behave.sim.behavior.multiclock import (
    ATOMIC_GENERATION_POLICY,
    ATOMIC_TRANSITION_VERSION,
    ATOMIC_WITNESS_VERSION,
    AtomicKernel,
    AtomicTransition,
    DomainEffect,
    DomainInvocation,
    DomainOwnership,
    IsolatedAtomicMachine,
    NativeAtomicMachine,
    _isolated_transition_from_record,
    _isolated_transition_record,
    atomic_outputs_from_wire,
    atomic_outputs_to_wire,
    atomic_transition_from_record,
    atomic_transition_record,
    invocations_from_record,
    ownership_from_source,
)
from behave.sim.behavior.native_values import DEFAULT_WIRE_NODES, WireEncoder
from behave.sim.core.clock_schedule import (
    ClockTiming,
    GlobalEventSlot,
    GlobalSchedule,
    validate_external_bindings,
)
from behave.sim.core.refusal import Refused
from behave.sim.core.stimulus import EventSlot


def _as_dict(image):
    return dict(image)


def test_atomic_witness_and_generation_policy_versions_pin_memory_environment():
    assert ATOMIC_WITNESS_VERSION == "atomic-witness/2"
    assert ATOMIC_GENERATION_POLICY == "atomic-generation/2"


def test_a_periodic_schedule_keeps_coincident_edges_in_one_slot():
    schedule = GlobalSchedule.periodic(
        [ClockTiming("a", 2), ClockTiming("b", 3)], 5)

    assert [(slot.time, slot.events) for slot in schedule.slots] == [
        (0, ("a", "b")),
        (2, ("a",)),
        (3, ("b",)),
        (4, ("a",)),
        (6, ("a", "b")),
    ]
    assert len(schedule.digest) == 64
    assert GlobalSchedule.from_dict(schedule.as_dict()).digest == schedule.digest
    assert isinstance(schedule.slots[0].event, EventSlot)
    assert schedule.slots[0].event.ticks == ("a", "b")

    with pytest.raises(Refused, match="schedule-event-set-not-induced"):
        GlobalSchedule(
            [ClockTiming("a", 2), ClockTiming("b", 3)],
            [GlobalEventSlot(0, ("a",)), GlobalEventSlot(2, ("a",))])


def test_per_domain_resets_extend_the_canonical_slot_without_rewriting_old_slots():
    old = EventSlot(offers=("in",), controls={"ready": 1}, ticks=("a",))
    assert old.as_dict() == {
        "offers": ["in"], "controls": [["ready", 1]],
        "reset": False, "ticks": ["a"],
    }
    multi = EventSlot(ticks=("a", "b"), resets=("b",))
    assert multi.as_dict()["resets"] == ["b"]
    assert EventSlot.from_dict(multi.as_dict()) == multi


def test_period_phase_edge_reset_and_event_sets_all_bind_the_schedule_digest():
    base = ClockTiming(
        "a", 4, phase=1, edge="rising", clock="a_clk", reset="a_rstn",
        reset_active_low=True)
    schedule = GlobalSchedule.periodic([
        base, ClockTiming("b", 5, reset="b_rst")], 4,
                                       reset_edges={"a": 2, "b": 1})
    images = [
        GlobalSchedule.periodic([
            ClockTiming("a", 3, phase=1, clock="a_clk", reset="a_rstn",
                        reset_active_low=True),
            ClockTiming("b", 5, reset="b_rst"),
        ], 4, reset_edges={"a": 2, "b": 1}),
        GlobalSchedule.periodic([
            ClockTiming("a", 4, phase=2, clock="a_clk", reset="a_rstn",
                        reset_active_low=True),
            ClockTiming("b", 5, reset="b_rst"),
        ], 4, reset_edges={"a": 2, "b": 1}),
        GlobalSchedule.periodic([
            ClockTiming("a", 4, phase=1, edge="falling", clock="a_clk",
                        reset="a_rstn", reset_active_low=True),
            ClockTiming("b", 5, reset="b_rst"),
        ], 4, reset_edges={"a": 2, "b": 1}),
        GlobalSchedule.periodic([
            ClockTiming("a", 4, phase=1, clock="a_clk", reset="a_rst",
                        reset_active_low=False),
            ClockTiming("b", 5, reset="b_rst"),
        ], 4, reset_edges={"a": 2, "b": 1}),
        GlobalSchedule.periodic([
            base, ClockTiming("b", 5, reset="b_rst")], 4,
                                reset_edges={"a": 1, "b": 1}),
    ]
    assert all(other.digest != schedule.digest for other in images)
    with pytest.raises(Refused, match="schedule-digest-mismatch"):
        schedule.assert_digest("0" * 64, consumer="test")


def test_periodic_reset_is_released_at_the_next_global_instant():
    """A faster clock is not held until its next edge while another domain fires."""

    timings = (
        ClockTiming("fast", 2, phase=0, reset="fast_rst"),
        ClockTiming("slow", 3, phase=1, reset="slow_rst"),
    )
    periodic = GlobalSchedule.periodic(
        timings, 3, reset_edges={"fast": 1, "slow": 1})
    prefixed = GlobalSchedule.with_reset_prefix(
        timings, 1, reset_edges={"fast": 1, "slow": 1})

    assert [(slot.time, slot.events, slot.resets)
            for slot in periodic.slots] == [
        (0, ("fast",), ("fast", "slow")),
        (1, ("slow",), ("slow",)),
        (2, ("fast",), ()),
    ]
    assert periodic.as_dict() == prefixed.as_dict()


def test_generated_or_shared_clocks_need_an_explicit_event_policy():
    with pytest.raises(Refused, match="schedule-clock-not-external"):
        validate_external_bindings(
            [ClockTiming("a", 2, clock="generated_clk")], {"clk"})
    with pytest.raises(Refused, match="schedule-clock-binding-shared"):
        validate_external_bindings([
            ClockTiming("a", 2, clock="clk"),
            ClockTiming("b", 3, clock="clk"),
        ], {"clk"})
    with pytest.raises(Refused, match="schedule-reset-not-external"):
        validate_external_bindings(
            [ClockTiming("a", 2, clock="clk", reset="internal_rst")],
            {"clk"})


def test_schedule_and_action_deserialisation_reject_unknown_or_conflicting_forms():
    schedule = GlobalSchedule.periodic([
        ClockTiming("a", 2), ClockTiming("b", 3)], 2)
    image = schedule.as_dict()
    image["unknown"] = True
    with pytest.raises(Refused, match="schedule-record-malformed"):
        GlobalSchedule.from_dict(image)

    slot = GlobalEventSlot(0, ("a", "b"), resets=("a",))
    record = {**slot.as_dict(), "actions": [["tick", "a", []]]}
    got = GlobalEventSlot.from_dict(record, allow_actions=True)
    with pytest.raises(Refused, match="invocation-without-edge"):
        invocations_from_record(got, record)
    record = {**slot.as_dict(), "actions": [
        ["tick", "b", []], ["tick", "b", []],
    ]}
    with pytest.raises(Refused, match="two-invocations"):
        invocations_from_record(
            GlobalEventSlot.from_dict(record, allow_actions=True), record)
    record = {**GlobalEventSlot(0, ("a", "b")).as_dict(), "actions": [
        ["tick", "b", []], ["tick", "a", []],
    ]}
    with pytest.raises(Refused, match="canonical domain order"):
        invocations_from_record(
            GlobalEventSlot.from_dict(record, allow_actions=True), record)


def test_schedule_deserialisation_is_typed_and_rejects_noncanonical_values():
    schedule = GlobalSchedule.periodic([
        ClockTiming("a", 2), ClockTiming("b", 3)], 2)

    image = json.loads(json.dumps(schedule.as_dict()))
    image["timings"][0]["reset_active_low"] = "false"
    with pytest.raises(Refused, match="schedule-clock-record-malformed"):
        GlobalSchedule.from_dict(image)

    image = json.loads(json.dumps(schedule.as_dict()))
    image["slots"][0]["ticks"].append(image["slots"][0]["ticks"][0])
    with pytest.raises(Refused, match="schedule-slot-record-malformed"):
        GlobalSchedule.from_dict(image)

    image = json.loads(json.dumps(schedule.as_dict()))
    image["slots"] = None
    with pytest.raises(Refused, match="schedule-record-malformed"):
        GlobalSchedule.from_dict(image)

    image = json.loads(json.dumps(schedule.as_dict()))
    image["slots"][0]["reset"] = True
    with pytest.raises(Refused, match="schedule-reset-domain-ambiguous"):
        GlobalSchedule.from_dict(image)


def test_atomic_records_do_not_coerce_objects_into_evidence():
    class Printable:
        def __str__(self):
            return "looks-like-a-goal"

    class MappingLike:
        def items(self):
            return (("x", ("a",)),)

    with pytest.raises(TypeError, match="hits.*exact goal"):
        DomainEffect("a", hits=(Printable(),))
    with pytest.raises(TypeError, match="outputs must be an exact tuple"):
        DomainEffect("a", outputs=[{"y": 1}])
    with pytest.raises(TypeError, match="domain ownership.*exact mapping"):
        DomainOwnership(MappingLike())
    with pytest.raises(TypeError, match="domains must be exact"):
        NativeAtomicMachine(object, DomainOwnership({}), (1, "a"))
    with pytest.raises(Refused, match="state-key-unsupported"):
        AtomicKernel(
            {"keys": {float("nan")}}, DomainOwnership({"keys": ("a",)}),
            {"a": lambda _state, _args: DomainEffect("a")})


def test_atomic_output_wire_preserves_exact_types_and_rejects_noncanonical_rows():
    transition = AtomicKernel(
        {"x": 0}, DomainOwnership({"x": ("a",)}),
        {"a": lambda _state, _args: DomainEffect(
            "a", outputs=((('flag', True), ('value', 1)),))},
    ).step(GlobalEventSlot(0, ("a",)), (DomainInvocation("a"),))
    encoded = atomic_outputs_to_wire(transition)

    assert atomic_outputs_from_wire(encoded) == (
        ("a", ((('flag', True), ('value', 1)),)),
    )
    malformed = WireEncoder(DEFAULT_WIRE_NODES).encode([
        ["a", [[['value', 1], ['flag', True]]]],
    ])
    with pytest.raises(Refused, match="multiclock-output-record-malformed"):
        atomic_outputs_from_wire(malformed)


def test_coincident_handlers_read_one_pre_state_and_order_cannot_change_result():
    ownership = DomainOwnership({"x": ("a",), "y": ("b",)})

    def a(pre, _args):
        return DomainEffect(
            "a", {("x",): pre["y"] + 1},
            outputs=((('a', pre["y"]),),))

    def b(pre, _args):
        return DomainEffect(
            "b", {("y",): pre["x"] + 1},
            outputs=((('b', pre["x"]),),))

    slot = GlobalEventSlot(0, ("a", "b"))
    first = AtomicKernel({"x": 1, "y": 9}, ownership, {"a": a, "b": b})
    second = AtomicKernel({"x": 1, "y": 9}, ownership, {"b": b, "a": a})
    got1 = first.step(slot, [DomainInvocation("a"), DomainInvocation("b")])
    got2 = second.step(slot, [DomainInvocation("b"), DomainInvocation("a")])

    assert _as_dict(got1.after) == {"x": 10, "y": 2}
    assert got1.after == got2.after
    assert got1.outputs == got2.outputs


def test_native_atomic_hit_hook_is_branched_and_committed_once():
    hit_log = []

    class M:
        def reset(self):
            self.a = 0
            self.b = 0

        def process_a(self):
            assert hit_log == []
            hit_log.append("a.hit")
            self.a += 1

        def process_b(self):
            # The first coincident handler's instrumentation is not this
            # handler's pre-state.
            assert hit_log == []
            hit_log.append("b.hit")
            self.b += 1

    machine = NativeAtomicMachine(
        M, DomainOwnership({"a": ("a",), "b": ("b",)}), ("a", "b"),
        hit_log=hit_log)
    transition = machine.step(
        GlobalEventSlot(0, ("a", "b")),
        (DomainInvocation("a"), DomainInvocation("b")))

    assert transition.hits == ("a.hit", "b.hit")
    assert hit_log == ["a.hit", "b.hit"]


def test_untriggered_domain_holds_and_produces_no_hit():
    ownership = DomainOwnership({"x": ("a",), "y": ("b",)})

    def a(pre, _args):
        return DomainEffect(
            "a", {("x",): pre["x"] + 1}, hits=("a.hit",))

    def b(_pre, _args):
        raise AssertionError("an inactive domain must not execute")

    got = AtomicKernel(
        {"x": 1, "y": 7}, ownership, {"a": a, "b": b}).step(
            GlobalEventSlot(2, ("a",)), [DomainInvocation("a")])
    assert _as_dict(got.after) == {"x": 2, "y": 7}
    assert got.hits == ("a.hit",)


def test_disjoint_array_elements_commit_together_but_colliding_writes_refuse():
    ownership = DomainOwnership({"mem": ("a", "b")})

    def a(_pre, _args):
        return DomainEffect("a", {("mem", 0): 3})

    def b(_pre, args):
        return DomainEffect("b", {("mem", args[0]): 8})

    slot = GlobalEventSlot(0, ("a", "b"))
    kernel = AtomicKernel({"mem": [0, 0]}, ownership, {"a": a, "b": b})
    got = kernel.step(slot, [DomainInvocation("a"), DomainInvocation("b", [1])])
    assert _as_dict(got.after) == {"mem": [3, 8]}

    # Reading the frozen transition gives a fresh native value: its exact list
    # type survives, but a consumer cannot mutate the evidence in place.
    image = got.after["mem"]
    image[0] = 99
    assert got.after["mem"] == [3, 8]

    kernel = AtomicKernel({"mem": [0, 0]}, ownership, {"a": a, "b": b})
    with pytest.raises(Refused, match="multiclock-write-collision"):
        kernel.step(slot, [DomainInvocation("a"), DomainInvocation("b", [0])])


def test_identical_collision_requires_an_explicit_total_policy():
    slot = GlobalEventSlot(0, ("a", "b"))

    def write(domain):
        return lambda _pre, _args: DomainEffect(domain, {("flag",): 1})

    handlers = {domain: write(domain) for domain in ("a", "b")}
    with pytest.raises(Refused, match="multiclock-write-collision"):
        AtomicKernel(
            {"flag": 0}, DomainOwnership({"flag": ("a", "b")}),
            handlers).step(slot, [DomainInvocation("a"), DomainInvocation("b")])

    got = AtomicKernel(
        {"flag": 0},
        DomainOwnership({"flag": ("a", "b")}, merge={"flag": "identical"}),
        handlers).step(slot, [DomainInvocation("a"), DomainInvocation("b")])
    assert _as_dict(got.after) == {"flag": 1}


def test_independent_resets_are_domain_transitions_from_the_same_pre_state():
    ownership = DomainOwnership({"a_state": ("a",), "b_state": ("b",)})

    def hold(domain):
        return lambda _pre, _args: DomainEffect(domain)

    def reset_a(_pre, _args):
        return DomainEffect("a", {("a_state",): 0})

    kernel = AtomicKernel(
        {"a_state": 7, "b_state": 9}, ownership,
        {"a": hold("a"), "b": hold("b")}, reset_handlers={"a": reset_a})
    got = kernel.step(GlobalEventSlot(3, events=("a", "b"), resets=("a",)),
                      [DomainInvocation("b")])
    assert _as_dict(got.after) == {"a_state": 0, "b_state": 9}

    with pytest.raises(Refused, match="independent-reset-unavailable"):
        kernel.step(GlobalEventSlot(4, events=("b",), resets=("b",)))


def test_an_asserted_reset_without_that_domains_edge_is_only_a_level():
    ownership = DomainOwnership({"a_state": ("a",), "b_state": ("b",)})

    def reset_b(_pre, _args):
        return DomainEffect("b", {("b_state",): 0})

    kernel = AtomicKernel(
        {"a_state": 7, "b_state": 9}, ownership,
        {"a": lambda _pre, _args: DomainEffect("a")},
        reset_handlers={"b": reset_b})
    got = kernel.step(GlobalEventSlot(3, events=("a",), resets=("b",)))

    assert _as_dict(got.after) == {"a_state": 7, "b_state": 9}
    assert got.effects == ()


def test_common_reset_state_holds_until_each_domain_is_released():
    class M:
        def reset(self):
            self.a = 0
            self.b = 0

        def process_a(self):
            self.a += 1

        def process_b(self):
            self.b += 1

    machine = NativeAtomicMachine(
        M, DomainOwnership({"a": ("a",), "b": ("b",)}), ("a", "b"))

    # a remains in the state established by common reset while b is released.
    got = machine.step(
        GlobalEventSlot(0, ("a", "b"), resets=("a",)),
        (DomainInvocation("b"),))
    assert _as_dict(got.after) == {"a": 0, "b": 1}

    machine.step(GlobalEventSlot(1, ("a",)), (DomainInvocation("a"),))
    with pytest.raises(Refused, match="independent-reset-unavailable"):
        machine.step(GlobalEventSlot(2, ("a",), resets=("a",)))


def test_native_adapter_refuses_a_real_concurrent_scalar_collision():
    source = """
class M:
    def reset(self):
        self.count = 1
    def process_a(self):
        self.count += 1
    def process_b(self):
        self.count -= 1
""".lstrip()
    namespace = {}
    exec(source, namespace)  # noqa: S102 - the test constructs its local fixture
    _model, ownership = ownership_from_source(source)
    machine = NativeAtomicMachine(namespace["M"], ownership, ("a", "b"))
    with pytest.raises(Refused, match="multiclock-write-collision"):
        machine.step(GlobalEventSlot(0, ("a", "b")),
                     [DomainInvocation("a"), DomainInvocation("b")])


@pytest.mark.parametrize("domains", [("a", "b"), ("write", "read"),
                                     ("ingress", "compute", "egress")])
def test_declared_multiclock_behavior_has_executable_domain_ownership(domains):
    # Task revisions may legitimately switch between native-clock and sampled-
    # level models. Test the ownership contract without pinning mutable tasks to
    # an obsolete frontend shape; the population qualification checks real tasks.
    source = "class M:\n    def reset(self):\n" + "".join(
        f"        self.{domain}_count = 0\n" for domain in domains)
    source += "".join(
        f"    def process_{domain}(self, amount):\n"
        f"        self.{domain}_count += amount\n" for domain in domains)
    model, ownership = ownership_from_source(source)
    assert len(model.domains) >= 2
    assert {name for name, _domains in ownership.owners} == set(model.state_contract())
    assert all(set(domains) <= set(model.domains)
               for _name, domains in ownership.owners)

    # The ownership is executable, not only an AST label.  Use the same isolated
    # atomic worker as production generation: each declared handler takes one event
    # while every untriggered domain holds, and no candidate module is imported into
    # the verifier process.
    with IsolatedAtomicMachine(
            source, ownership, model.domains, cls=model.cls.name) as machine:
        for index, domain in enumerate(model.domains):
            transition = machine.step(
                GlobalEventSlot(index, (domain,)),
                [DomainInvocation(domain, [1])])
            assert transition.slot.events == (domain,)
            before, after = dict(transition.before), dict(transition.after)
            assert after == {name: value + int(name == f"{domain}_count")
                             for name, value in before.items()}


@pytest.mark.parametrize("mutation", [
    "global TOKEN; TOKEN = 1",
    "M.TOKEN = 1",
])
@pytest.mark.parametrize("fails", [False, True])
def test_coincident_handlers_cannot_communicate_through_module_or_class_state(
        mutation, fails):
    source = f"""
TOKEN = 0
class M:
    TOKEN = 0
    def reset(self):
        self.a = 0
        self.b = 0
    def process_a(self):
        {mutation}
        self.a = 1
        {"1 // 0" if fails else "pass"}
    def process_b(self):
        self.b = TOKEN + M.TOKEN
""".lstrip()
    model, ownership = ownership_from_source(source)
    # Both handlers fire in one global slot of the production isolated machine.
    with IsolatedAtomicMachine(source, ownership, model.domains, cls=model.cls.name) as machine:
        with pytest.raises(Refused, match="behavior-runtime-invariant"):
            machine.step(GlobalEventSlot(0, ("a", "b")),
                         [DomainInvocation("a"), DomainInvocation("b")])


def test_an_atomic_handler_cannot_change_its_private_instance_class():
    source = """
class N:
    pass
class M:
    def reset(self):
        self.a = 0
        self.b = 0
    def process_a(self):
        self.__class__ = N
    def process_b(self):
        self.b += 1
""".lstrip()
    namespace = {}
    exec(source, namespace)  # noqa: S102 - explicit local atomic-unit seam
    machine = NativeAtomicMachine(
        namespace["M"], DomainOwnership({"a": ("a",), "b": ("b",)}),
        ("a", "b"))
    with pytest.raises(Refused, match="multiclock-instance-class-changed"):
        machine.step(
            GlobalEventSlot(0, ("a", "b")),
            (DomainInvocation("a"), DomainInvocation("b")))


def test_atomic_transition_wire_round_trip_and_old_version_refusal():
    transition = AtomicTransition(
        GlobalEventSlot(0, ("a",)), {"x": 0}, {"x": 0},
        (DomainEffect("a"),))
    record = atomic_transition_record(transition)

    assert record["version"] == ATOMIC_TRANSITION_VERSION
    assert atomic_transition_record(atomic_transition_from_record(record)) == record

    for version in (None, "atomic-transition/0"):
        old = dict(record)
        if version is None:
            old.pop("version")
        else:
            old["version"] = version
        with pytest.raises(
                Refused, match="multiclock-transition-version-unsupported"):
            atomic_transition_from_record(old)


def test_isolated_transition_wire_carries_only_the_exact_patch():
    before = {"mem": [0] * 1024, "pointer": 0}
    effect = DomainEffect(
        "a", {("mem", 17): 9, ("pointer",): 18}, hits=("write",))
    transition = AtomicTransition(
        GlobalEventSlot(0, ("a",)), before,
        {"mem": [0] * 17 + [9] + [0] * (1024 - 18), "pointer": 18},
        (effect,))

    record = _isolated_transition_record(transition)
    rebuilt = _isolated_transition_from_record(
        record, before, DomainOwnership({"mem": ("a",), "pointer": ("a",)}))

    assert set(record) == {"version", "slot", "effects"}
    assert rebuilt.before == transition.before
    assert rebuilt.after == transition.after
    assert [effect.as_record() for effect in rebuilt.effects] == [
        effect.as_record() for effect in transition.effects]


def test_idle_atomic_slot_holds_the_persistent_state_without_copying_it():
    ownership = DomainOwnership({"mem": ("a",), "pointer": ("a",)})
    kernel = AtomicKernel(
        {"mem": [0] * 1024, "pointer": 0}, ownership, {})

    transition = kernel.step(GlobalEventSlot(0, ("a",)))

    assert not transition.effects
    assert transition.before is transition.after
    assert transition.after is kernel._state_image
    assert transition.after["mem"] == [0] * 1024


def test_atomic_steady_state_reply_does_not_publish_a_generic_snapshot(
        monkeypatch):
    model = object.__new__(IsolatedModel)
    model._state = {"old": 1}
    model._unsupported = {"old": "why"}
    model._carried = {"old": ["int", 1]}
    model._opaque = {"old": "opaque"}
    model._hits = ["old-hit"]
    monkeypatch.setattr(model, "_say", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(model, "_hear", lambda: {
        "ok": True, "state": {}, "why": {}, "carried": {}, "opaque": {},
        "memories": {}, "hits": [], "raised": "",
        "produced": WireEncoder(DEFAULT_WIRE_NODES).encode({
            "status": "ok", "value": True,
        }),
    })

    assert model._atomic_call({"call": "atomic_ready"}, bool) is True
    assert model._state == {"old": 1}
    assert model._unsupported == {"old": "why"}
    assert model._carried == {"old": ["int", 1]}
    assert model._opaque == {"old": "opaque"}
    assert model._hits == ["old-hit"]


def test_atomic_transition_record_rejects_noncanonical_output_fields():
    record = {
        "version": ATOMIC_TRANSITION_VERSION,
        "slot": GlobalEventSlot(0, ("a",)).as_dict(),
        "before": {"x": 0},
        "after": {"x": 0},
        "effects": [{
            "domain": "a", "writes": [], "hits": [],
            "outputs": [[["z", 1], ["z", 2]]],
        }],
    }
    with pytest.raises(Refused, match="multiclock-effect-record-malformed"):
        atomic_transition_from_record(record)


def test_atomic_transition_record_rejects_unhashable_paths_and_bad_owners():
    record = {
        "version": ATOMIC_TRANSITION_VERSION,
        "slot": GlobalEventSlot(0, ("a",)).as_dict(),
        "before": {"x": [0]},
        "after": {"x": [0]},
        "effects": [{
            "domain": "a", "writes": [[["x", [0]], 1]],
            "hits": [], "outputs": [],
        }],
    }
    with pytest.raises(Refused, match="multiclock-effect-record-malformed"):
        atomic_transition_from_record(record)

    with pytest.raises(Refused, match="multiclock-ownership-record-malformed"):
        DomainOwnership.from_record({
            "owners": [["x", [["not", "a", "domain"]]]], "merge": [],
        })


@pytest.mark.parametrize("answer", [
    {
        "status": "ok",
        "value": {
            "version": ATOMIC_TRANSITION_VERSION,
            "slot": GlobalEventSlot(0, ("a",)).as_dict(),
            "before": {"x": 0}, "after": {"x": 0},
            "effects": [{
                "domain": "a", "writes": [], "hits": [],
                "outputs": [[['z', 1], ['z', 2]]],
            }],
        },
    },
    {
        "status": "refused",
        "refusal": {"kind": "bad", "why": "bad reply", "detail": {1: 2}},
    },
])
def test_malformed_atomic_worker_reply_closes_without_publishing(
        monkeypatch, answer):
    model = object.__new__(IsolatedModel)
    model._state = {"old": 1}
    model._unsupported = {"old": "why"}
    model._carried = {"old": ["int", 1]}
    model._opaque = {"old": "opaque"}
    model._hits = ["old-hit"]
    closed = []
    monkeypatch.setattr(model, "_say", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(model, "close", lambda: closed.append(True))
    base = {
        "ok": True, "state": {"new": 2}, "why": {}, "carried": {},
        "opaque": {}, "memories": {}, "hits": ["new-hit"], "raised": "",
        "produced": WireEncoder(DEFAULT_WIRE_NODES).encode(answer),
    }
    monkeypatch.setattr(model, "_hear", lambda: base)

    with pytest.raises(Broken, match="malformed isolated atomic-model reply"):
        model._atomic_call(
            {"call": "atomic_step"}, atomic_transition_from_record)
    assert closed
    assert model._state == {"old": 1}
    assert model._unsupported == {"old": "why"}
    assert model._carried == {"old": ["int", 1]}
    assert model._opaque == {"old": "opaque"}
    assert model._hits == ["old-hit"]


def test_atomic_step_rejects_a_reply_missing_the_invoked_domain_effect(monkeypatch):
    model = object.__new__(IsolatedModel)
    model._state = {"old": 1}
    model._unsupported = {}
    model._carried = {}
    model._opaque = {}
    model._hits = []
    model._atomic_domains = ("a", "b")
    model._atomic_state = {"x": 0}
    model._atomic_ownership = DomainOwnership({"x": ("a",)})
    closed = []
    monkeypatch.setattr(model, "_say", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(model, "close", lambda: closed.append(True))
    transition = _isolated_transition_record(AtomicTransition(
        GlobalEventSlot(0, ("a",)), {"x": 0}, {"x": 0}, ()))
    monkeypatch.setattr(model, "_hear", lambda: {
        "ok": True, "state": {}, "why": {}, "carried": {},
        "opaque": {}, "memories": {}, "hits": [], "raised": "",
        "produced": WireEncoder(DEFAULT_WIRE_NODES).encode({
            "status": "ok", "value": transition,
        }),
    })

    with pytest.raises(Broken, match="malformed isolated atomic-model reply"):
        model.atomic_step(
            GlobalEventSlot(0, ("a",)), (DomainInvocation("a"),))
    assert closed
    assert model._state == {"old": 1}


def test_atomic_step_rejects_a_successor_not_derived_from_its_writes(monkeypatch):
    model = object.__new__(IsolatedModel)
    model._state = {"old": 1}
    model._unsupported = {}
    model._carried = {}
    model._opaque = {}
    model._hits = []
    model._atomic_domains = ("a", "b")
    model._atomic_ownership = DomainOwnership({"x": ("a",), "y": ("b",)})
    model._atomic_state = AtomicKernel(
        {"x": 0, "y": 0}, model._atomic_ownership,
        {"a": lambda _pre, _args: DomainEffect("a")}).state
    # The production field is a type-preserving immutable state view.
    from behave.sim.behavior.multiclock import _NativeState
    model._atomic_state = _NativeState(model._atomic_state)
    closed = []
    monkeypatch.setattr(model, "_say", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(model, "close", lambda: closed.append(True))
    transition = {
        "version": ATOMIC_TRANSITION_VERSION,
        "slot": GlobalEventSlot(0, ("a",)).as_dict(),
        "before": {"x": 0, "y": 0},
        # The only effect writes x=1, so x=9 is not this transition's successor.
        "after": {"x": 9, "y": 0},
        "effects": [{
            "domain": "a", "writes": [[['x'], 1]],
            "hits": [], "outputs": [],
        }],
    }
    monkeypatch.setattr(model, "_hear", lambda: {
        "ok": True, "state": {"new": 2}, "why": {}, "carried": {},
        "opaque": {}, "memories": {}, "hits": [], "raised": "",
        "produced": WireEncoder(DEFAULT_WIRE_NODES).encode({
            "status": "ok", "value": transition,
        }),
    })

    with pytest.raises(Broken, match="malformed isolated atomic-model reply"):
        model.atomic_step(
            GlobalEventSlot(0, ("a",)), (DomainInvocation("a"),))
    assert closed
    assert model._state == {"old": 1}
    assert _as_dict(model._atomic_state) == {"x": 0, "y": 0}


def test_generated_driver_delegates_to_the_shared_native_replay():
    from behave.sim.harness import render_template

    rendered = render_template("driver_tb.py.tmpl", period_ns=10)
    compile(rendered, "<atomic-multiclock-tb>", "exec")
    assert "w[\"interleave\"]" not in rendered
    assert "Clock(" not in rendered
    assert "from behave.sim.rtl.replay_runtime import replay" in rendered
    assert "await replay(dut, Path(__file__).parent, period_ns=10)" in rendered
    assert "import behavior" not in rendered
    assert '"behavior.py"' not in rendered
    assert "NativeAtomicMachine" not in rendered
    assert "IsolatedAtomicMachine" not in rendered
