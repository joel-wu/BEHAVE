"""Test-only executor of the unchanged dense ordered-monitor definition."""

from dataclasses import asdict

from behave.sim.core.ordered_monitor import OrderedObservation
from behave.sim.core.ready_valid_product import (
    ConcreteAlgebra,
    CountedReadyValidState,
    OrderedReadyValidConfig,
    ordered_ready_valid_initial,
    ordered_ready_valid_state_image,
    ordered_ready_valid_step,
)


class DenseConfig(OrderedReadyValidConfig):
    @property
    def counted(self):
        return False


class DenseOrderedOracle:
    def __init__(self, config):
        self.counted = config.counted
        self.config = DenseConfig(**asdict(config))
        self.state = ordered_ready_valid_initial(self.config, ConcreteAlgebra())

    def step(self, event):
        before = self.state
        got = ordered_ready_valid_step(self.config, before, event, ConcreteAlgebra())
        self.state = got.state
        return OrderedObservation(
            got.hits, got.opportunities,
            tuple((i, before.accepted_at[i])
                  for i, hit in enumerate(got.details["deadline_hits"]) if hit),
            before.held, got.details["stability_dropped"],
            got.details["stability_changed"], sum(self.state.pending),
            got.details["reset_policy_unresolved"])

    def pending_requests(self):
        return [{"at": self.state.accepted_at[i], "owed": self.state.owed[i],
                 "reported": self.state.reported[i]}
                for i, pending in enumerate(self.state.pending) if pending]

    def state_image(self):
        if self.counted:
            return ordered_ready_valid_state_image(CountedReadyValidState(
                self.state.accepted_count, sum(self.state.pending),
                self.state.stalled, self.state.held))
        return ordered_ready_valid_state_image(self.state)
