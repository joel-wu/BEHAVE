"""Framework-free configuration checks; native tensor checks have their own lane."""

import pytest

from behave.train.trainer import TorchRNG, TorchState


class State:
    def state_dict(self):
        return {}

    def load_state_dict(self, state):
        pass


def test_native_bridge_retains_explicit_order_and_loads_rng_last():
    components = {name: State() for name in ("rng", "model", "scheduler", "optimizer", "sampler")}
    bridge = TorchState(components)
    components.clear()
    assert bridge.components == ("rng", "model", "scheduler", "optimizer", "sampler")
    assert bridge._order == ("model", "scheduler", "optimizer", "sampler", "rng")
    # No optimizer requirement: algorithms can have other stateful components.
    assert TorchState({name: State() for name in ("model", "rng", "sampler")}).components


@pytest.mark.parametrize(
    "names", [("model", "rng"), ("rng", "sampler"), ("model", "sampler"), tuple(map(str, range(65)))]
)
def test_native_bridge_rejects_incomplete_component_declarations(names):
    with pytest.raises(ValueError):
        TorchState({name: State() for name in names})


def test_native_components_need_both_state_hooks():
    with pytest.raises(TypeError):
        TorchState({"model": State(), "rng": TorchRNG(), "sampler": object()})


@pytest.mark.parametrize(
    "kwargs",
    [{"numpy": 1}, {"cuda_devices": [0]}, {"cuda_devices": (True,)}, {"cuda_devices": (-1,)}, {"cuda_devices": (0, 0)}],
)
def test_rng_domains_are_explicit(kwargs):
    with pytest.raises((TypeError, ValueError)):
        TorchRNG(**kwargs)
