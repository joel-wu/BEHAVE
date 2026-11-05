from types import SimpleNamespace

import pytest

from behave.train.slime_hooks import configure_startup as configure
from behave.train.slime_hooks import wait_ready


def actor(rank):
    return SimpleNamespace(get_master_addr_and_port=SimpleNamespace(remote=lambda: rank))


def test_every_constructor_is_ready_before_native_init_and_configuration_is_idempotent():
    calls = []

    class Group:
        args = SimpleNamespace()

        def _allocate_gpus_for_actor(self):
            calls.append("allocate")
            self._actor_handlers = [actor(i) for i in range(8)]

        def create(self):
            self._allocate_gpus_for_actor()
            calls.append("native-init")

    ray = SimpleNamespace(wait=lambda refs, **kw: ([refs[-1]], refs[:-1]),
                          get=lambda ref: calls.append(ref))
    configure(group_class=Group, ray_module=ray)
    configure(group_class=Group, ray_module=ray)
    Group().create()
    assert calls == ["allocate", *range(7, -1, -1), "native-init"]


def test_constructor_failure_propagates_without_initializing_other_ranks():
    def failed(ref):
        raise RuntimeError("import failed")

    ray = SimpleNamespace(wait=lambda refs, **kw: ([refs[0]], refs[1:]), get=failed)
    with pytest.raises(RuntimeError, match="import failed"):
        wait_ready([actor(0), actor(1)], ray=ray, seconds=100)


def test_startup_deadline_reports_pending_ranks_separately():
    times = iter([0, 0, 31, 31])
    ray = SimpleNamespace(wait=lambda refs, **kw: ([], refs), get=lambda ref: None)
    with pytest.raises(TimeoutError, match=r"pending ranks=\[0, 1\].*has not started"):
        wait_ready([actor(0), actor(1)], ray=ray, seconds=30, clock=lambda: next(times))


@pytest.mark.parametrize("seconds", [0, -1, True, float("inf"), float("nan")])
def test_invalid_startup_deadline_is_rejected(seconds):
    with pytest.raises(ValueError, match="finite and positive"):
        wait_ready([], ray=None, seconds=seconds)
