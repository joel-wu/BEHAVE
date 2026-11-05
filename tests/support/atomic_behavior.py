"""Solver-independent atomic Behavior fixtures shared by replay and exact tests."""

from behave.sim import api
from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule

U2 = api.FiniteType(2)


U4 = api.FiniteType(4)


def _contract():
    endpoints = (
        api.Endpoint(
            "a_in", api.Direction.IN, [api.Field("a", U2, "a")],
            api.ready_valid(api.Direction.IN, "a_valid", "a_ready"),
            clock="a", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "a_out", api.Direction.OUT, [api.Field("oa", U4, "oa")],
            api.ready_valid(api.Direction.OUT, "oa_valid", "oa_ready"),
            clock="a", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "b_in", api.Direction.IN, [api.Field("b", U2, "b")],
            api.ready_valid(api.Direction.IN, "b_valid", "b_ready"),
            clock="b", kind=api.Kind.TRANSACTION),
        api.Endpoint(
            "b_out", api.Direction.OUT, [api.Field("ob", U4, "ob")],
            api.ready_valid(api.Direction.OUT, "ob_valid", "ob_ready"),
            clock="b", kind=api.Kind.TRANSACTION),
    )
    return api.TaskContract(
        endpoints,
        [api.ClockDomain("a", "a_clk", "a_rst", reset_hold_cycles=0),
         api.ClockDomain("b", "b_clk", "b_rst", reset_hold_cycles=0)],
        source="atomic exact unit task")


SOURCE = """
class M:
    def __init__(self):
        self.reset()

    def reset(self):
        self.x = 0
        self.y = 8

    def process_a(self, a):
        self.x = self.y + a
        if a == 1:
            self.x = self.x + 1
        return {"oa": self.x}

    def process_b(self, b):
        self.y = self.x + b
        return {"ob": self.y}
"""


ATOMIC_PARTIAL_FAILURE_SOURCE = """
class M:
    def reset(self):
        pass

    def process_a(self, a):
        return {"oa": 1 // a}

    def process_b(self, b):
        return {"ob": b}
"""


def _schedule(periods=(2, 3), *, offers_by_domain=None, n_slots=3):
    base = GlobalSchedule.periodic(
        [ClockTiming("a", periods[0], clock="a_clk", reset="a_rst"),
         ClockTiming("b", periods[1], clock="b_clk", reset="b_rst")],
        n_slots)
    controls = {
        "a_rst": 0, "b_rst": 0, "oa_ready": 1, "ob_ready": 1,
    }
    if offers_by_domain is None:
        offers_by_domain = {"a": ("a_in",), "b": ("b_in",)}
    return GlobalSchedule(base.timings, [
        GlobalEventSlot(
            slot.time,
            event=api.EventSlot(
                offers=tuple(
                    endpoint
                    for domain in slot.events
                    if domain not in slot.resets
                    for endpoint in offers_by_domain.get(domain, ())),
                controls=controls, ticks=slot.events, resets=slot.resets))
        for slot in base.slots
    ])


def _transaction_stimulus():
    base = _schedule()
    offers = (("a_in", "b_in"), ("a_in",), ("b_in",))
    schedule = GlobalSchedule(
        base.timings,
        [GlobalEventSlot(
            slot.time,
            event=api.EventSlot(
                offers=offered, controls=dict(slot.event.controls),
                ticks=slot.events, resets=slot.resets))
         for slot, offered in zip(base.slots, offers)])
    return api.Stimulus(
        queues={"a_in": ((1,), (0,)), "b_in": ((2,), (3,))},
        slots=[slot.event for slot in schedule.slots],
        source="atomic-public-record", clock_schedule=schedule)
