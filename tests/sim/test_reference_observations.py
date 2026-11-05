"""Reference transport may omit redundant state, never observable results."""

import pytest

from behave.sim.behavior.isolated import IsolatedModel, Limits, hits_of, snapshot_of
from behave.sim.core.memory import MemoryEnvironment, MemoryInterface, MemoryPort, MemoryRun

SOURCE = '''class M:
    def __init__(self):
        self.reset()
    def reset(self):
        self.ram = [0] * 8192
        self.total = 0
    def process(self, address, value):
        self.ram[address] = value
        self.total += value
        assert value != 255
        return {"y": self.ram[address]}
    def tick(self):
        self.total += 1
        return {"y": self.total}
'''


def model(policy, *, strict_module=False):
    return IsolatedModel(
        SOURCE, cls="M", strict_module=strict_module, snapshot_policy=policy,
        limits=Limits(seconds=30, memory_bytes=512 << 20, wall_seconds=15))


def _count_replies(instance):
    count = [0]
    read = instance._read_with_deadline

    def counted_read():
        line = read()
        count[0] += len(line)
        return line

    instance._read_with_deadline = counted_read
    return count


@pytest.mark.parametrize("strict_module", [False, True])
def test_observation_only_calls_preserve_outputs_hits_failures_and_fresh_state(strict_module):
    results = []
    traffic = []
    for policy in ("eager", "on-demand"):
        with model(policy, strict_module=strict_module) as instance:
            count = _count_replies(instance)
            outputs = [instance.process(address=i, value=i) for i in range(64)]
            outputs.append(instance.tick())
            with pytest.raises(AssertionError):
                instance.process(address=8191, value=255)
            events = list(hits_of(instance))
            # A failed call has already written RAM. Lazy observation must not
            # expose the greeting's state or lose the failure's structural hits.
            state, opaque = instance._audit_view()
            assert not opaque
            assert state["ram"][8191] == 255
            assert state["total"] == sum(range(64)) + 1 + 255
            assert instance.total == state["total"]
            assert list(hits_of(instance)) == events  # observing does not rerun
            results.append((outputs, events, state))
            traffic.append(count[0])
            instance.reset()
            assert snapshot_of(instance)["total"] == 0
            assert not hits_of(instance)
    assert results[0] == results[1]
    assert traffic[1] * 10 < traffic[0]


@pytest.mark.parametrize("policy", ["eager", "on-demand"])
@pytest.mark.parametrize("strict_module", [False, True])
def test_final_external_memory_is_current_even_without_a_state_read(policy, strict_module):
    memory = MemoryInterface("ram", 13, 32, "clk", [MemoryPort("rw")])
    environment = MemoryEnvironment([MemoryRun(
        "ram", initial={9: 33}, ready={None: [1] * 8})])
    with model(policy, strict_module=strict_module) as instance:
        instance._bind_external_memory((memory,), environment, 8)
        instance.process(address=9, value=5)
        instance.process(address=8191, value=7)
        result = instance._external_memory_result((memory,))
        assert result.memories == (("ram", ((9, 5), (8191, 7))),)
        instance.reset()
        assert instance._external_memory_result((memory,)).memories == (
            ("ram", ((9, 5), (8191, 7))),)


@pytest.mark.isolation
@pytest.mark.parametrize("outcome", ["normal", "assertion", "indexerror"])
def test_candidate_replay_matches_eager_outputs_hits_and_reset(tmp_path, monkeypatch, outcome):
    from behave.sim import api
    from behave.sim.behavior import artifact as artifact_module
    from behave.sim.core.stimulus import Completion

    from .test_baseline_public_sequence import _contract

    source = '''class M:
    def reset(self):
        self.ram = [0] * 8192
        self.total = 0
    def process(self, a):
        self.ram[a] = a
        self.total += a
        if a == 255:
            self.ram[8192] = 1
        assert a != 254
        return {"y": self.ram[a]}
'''
    path = tmp_path / "behavior.py"
    path.write_text(source)
    contract = _contract()
    task = api.admit(contract, api.realization_of_behavior_source(
        source, contract, file=path.name, model="M"))
    handle = api.prepare_behavior(task, api.BehaviorConfig(
        path, file=path.name, model="M"), bounded_analysis=False)
    values = [*range(64), {"normal": 64, "assertion": 254, "indexerror": 255}[outcome], 1]
    stimulus = api.reset_then(contract, [(v,) for v in values],
                              controls={"out_ready": 1}, drain=2)
    traces, traffic = [], []
    for eager in (False, True):
        def instrument_transport(*args, _eager=eager, **kwargs):
            assert kwargs["snapshot_policy"] == "on-demand"
            if _eager:
                kwargs["snapshot_policy"] = "eager"
            instance = IsolatedModel(*args, **kwargs)
            traffic.append(_count_replies(instance))
            return instance

        monkeypatch.setattr(artifact_module, "IsolatedModel", instrument_transport)
        trace = handle._artifact.trace(contract, stimulus)
        assert trace.completion is (
            Completion.NORMAL if outcome == "normal" else Completion.PADDED)
        if outcome != "normal":
            # Both failures now have native safety producers. A padded trace
            # must retain the exact failure hit, never masquerade as normal.
            family = "assertionerror" if outcome == "assertion" else "indexerror"
            goals = handle._artifact.frontend_catalogue
            failures = {goal for goal in goals if goals[goal].family == family}
            assert failures
            assert any(hit for record in trace.records
                       for goal, hit in record.hits if goal in failures)
        traces.append(trace.as_dict())
    assert traces[0] == traces[1]
    assert traffic[0][0] * 10 < traffic[1][0]


@pytest.mark.parametrize("policy", ["eager", "on-demand"])
def test_omitting_state_does_not_skip_source_binding_guards(policy):
    from behave.sim.behavior.isolated import Broken

    source = '''class M:
    def reset(self):
        pass
    def process(self, a):
        self.process = a
        return {"y": a}
'''
    with (
        IsolatedModel(source, cls="M", strict_module=True,
                      snapshot_policy=policy) as instance,
        pytest.raises(Broken),
    ):
        instance.process(a=1)


def test_reference_adapter_selects_observation_transport():
    from behave.sim import api

    bundle = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": SOURCE.encode()},
        config={"class": "M"})
    with bundle.new_model() as instance:
        assert instance._snapshot_policy == "on-demand"
        assert instance.process(address=1, value=7) == {"y": 7}
        assert not instance._snapshot_valid
        assert snapshot_of(instance)["ram"][1] == 7


def test_absent_attribute_lookup_does_not_observe_the_complete_state(monkeypatch):
    instance = object.__new__(IsolatedModel)
    instance._offers = ("process",)
    instance._snapshot_valid = False
    instance._field_names = frozenset(("ram", "total"))
    instance._state = {"ram": [0], "total": 0}
    monkeypatch.setattr(
        instance, "_refresh_snapshot",
        lambda: pytest.fail("a missing attribute must not read the model's RAM"))
    assert getattr(instance, "tick", None) is None
    assert not hasattr(instance, "process_slot")
    assert callable(instance.process)


@pytest.mark.parametrize("fields", [None, ["ram", 1], ["ram", "ram"]])
def test_invalid_field_presence_does_not_replace_the_previous_reply(monkeypatch, fields):
    from behave.sim.behavior.isolated import Broken
    from behave.sim.behavior.native_values import DEFAULT_WIRE_NODES, WireEncoder

    instance = object.__new__(IsolatedModel)
    instance._snapshot_policy = "on-demand"
    instance._snapshot_valid = True
    instance._field_names = frozenset(("old",))
    instance._state = {"old": 7}
    instance._hits = ["old-hit"]
    monkeypatch.setattr(instance, "close", lambda: None)
    with pytest.raises(Broken, match="field names"):
        instance._accept_model_reply({
            "ok": True, "state": {}, "why": {}, "carried": {},
            "opaque": {}, "memories": {}, "hits": ["new-hit"],
            "produced": WireEncoder(DEFAULT_WIRE_NODES).encode(None),
            "raised": "", "fields": fields,
        })
    assert instance._snapshot_valid
    assert instance._field_names == {"old"}
    assert instance._state == {"old": 7}
    assert instance._hits == ["old-hit"]


def test_reference_idle_slots_do_not_read_ram(monkeypatch):
    from behave.sim import api
    from behave.sim.core.compare import comparison_for
    from behave.sim.core.failure import FailureRaised
    from tests.support.device_boundary import run_device

    from .test_api_path import Doubling, contract, realization, schedule

    source = '''class M:
    def reset(self):
        self.ram = [0] * 8192
    def process(self, in_a):
        self.ram[in_a] = 2 * in_a
        return {"out_y": self.ram[in_a]}
'''
    bundle = api.load_reference(
        api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": source.encode()},
        config={"class": "M"})
    task = api.admit(contract(), realization(), reference=bundle)
    trace = run_device(task, schedule([1, 3, 7], drain=20), Doubling())
    calls = []
    say = IsolatedModel._say

    def record(self, message):
        if "call" in message:
            calls.append(message["call"])
        say(self, message)

    monkeypatch.setattr(IsolatedModel, "_say", record)
    try:
        compared = comparison_for(task, bundle, trace)
    except FailureRaised as error:
        pytest.fail(str(error.failure.as_dict()))
    assert compared.ok
    assert calls.count("process") == 3
    assert "_observe_state" not in calls


@pytest.mark.parametrize("strict_module", [False, True])
def test_lazy_attribute_presence_tracks_creation_and_deletion(strict_module):
    source = '''class M:
    def __init__(self):
        self.reset()
    def reset(self):
        self.ram = [0] * 8192
    def process(self, value):
        self.ram[0] = value
        if value:
            self.tick = value
        else:
            del self.tick
        return {"y": value}
'''
    observations = []
    for policy in ("eager", "on-demand"):
        with IsolatedModel(source, cls="M", strict_module=strict_module,
                           snapshot_policy=policy) as instance:
            calls = []
            say = instance._say

            def record(message, calls=calls, say=say):
                calls.append(message["call"])
                say(message)

            instance._say = record
            instance.process(value=7)
            # A field called tick is not an offered method. It still exists,
            # and its current value must not be mistaken for a missing tick.
            observations.append(instance.tick)
            assert calls == (["process", "_observe_state"]
                             if policy == "on-demand" else ["process"])
            calls.clear()
            instance.process(value=0)
            assert getattr(instance, "tick", None) is None
            assert calls == ["process"]
            assert instance.ram[0] == 0
            assert calls == (["process", "_observe_state"]
                             if policy == "on-demand" else ["process"])
    assert observations == [7, 7]


def test_supervisor_limit_survives_the_reference_transport_boundary():
    from behave.isolation import SandboxProcess

    child = object.__new__(SandboxProcess)
    child._limit_status = "output-limit"
    child._status_refusal = None
    instance = object.__new__(IsolatedModel)
    instance._child = child
    instance._why = lambda: "the child was ended by SIGKILL"
    failure = instance._broken_transport()
    assert failure.code == "isolation-output-limit"
    assert failure.detail == "output-limit"

    # Popen-shaped doubles or source text cannot attest an isolation cause.
    instance._child = type("Untrusted", (), {"final_status": "output-limit"})()
    assert instance._broken_transport().code == ""


@pytest.mark.parametrize("status", ["timed-out", "cpu-limit", "exited", None])
def test_eof_waits_for_terminal_receipt_with_one_cleanup_deadline(monkeypatch, status):
    import subprocess

    from behave.isolation import SandboxProcess
    from behave.sim.behavior import isolated

    now = [10.0]
    monkeypatch.setattr(isolated.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(isolated.defaults, "SERVICE_CLEANUP_SECONDS", 5)
    child = object.__new__(SandboxProcess)
    child._limit_status = child._status_refusal = child._final = None
    child._finish_status = lambda timeout: child._final is not None
    child.poll = lambda: 0  # Launcher exit is NOT the model's terminal receipt.
    waits, reaps = [], []

    def wait(*, timeout):
        waits.append(timeout)
        if status is None:
            now[0] = 15.0
            raise subprocess.TimeoutExpired("model", timeout)
        now[0] = 11.0
        child._final = {"status": status, "exit_code": 0}
        return 0 if status == "exited" else -9

    child.wait = wait
    child.close = lambda *, deadline: reaps.append(deadline)
    instance = object.__new__(IsolatedModel)
    instance._child = child
    failure = instance._broken_transport()
    instance.close()
    assert waits == [5.0]
    assert reaps == [15.0]  # Receipt waiting did not restart the cleanup clock.
    assert failure.code == ("isolation-" + status
                            if status in {"timed-out", "cpu-limit"} else "")
    if status is None:
        assert str(failure) == "the child stopped answering"
    assert instance._child is None


@pytest.mark.isolation
def test_actual_eof_with_delayed_supervisor_receipt_keeps_timeout(monkeypatch):
    import time

    from behave.isolation import linux
    from behave.sim.behavior.isolated import Broken

    parse = linux._terminal_status
    receipts = []

    def delayed(payload):
        result = parse(payload)
        if result.get("status") == "timed-out":
            time.sleep(0.2)
            receipts.append(result["status"])
        return result

    monkeypatch.setattr(linux, "_terminal_status", delayed)
    source = '''class M:
    def reset(self): pass
    def process(self, x):
        while True: x += 1
'''
    instance = IsolatedModel(source, cls="M",
        limits=Limits(seconds=10, wall_seconds=2, memory_bytes=512 << 20))
    # Keep the response-reader allowance above the launched sandbox's 2s
    # lifetime: this regression exercises EOF, not the independent read timer.
    instance._limits.wall_seconds = 5
    try:
        with pytest.raises(Broken) as caught:
            instance.process(x=1)
        # Both a ready status and one read after EOF must retain the original
        # timeout. A property read may now receive it directly on this caller.
        assert receipts == ["timed-out"]
        assert caught.value.code == "isolation-timed-out"
        assert instance._child is None
    finally:
        instance.close()


@pytest.mark.parametrize("code", ["", "isolation-output-limit"])
def test_reference_transport_limit_cannot_become_a_candidate_zero(monkeypatch, code):
    from behave.sim import api
    from behave.sim.behavior.isolated import Broken
    from behave.sim.core import reference as reference_module
    from behave.sim.core.compare import comparison_for
    from behave.sim.core.failure import FailureRaised
    from tests.support.device_boundary import run_device

    from .test_api_path import Doubling, _ref, contract, realization, schedule

    bundle = _ref()
    task = api.admit(contract(), realization(), reference=bundle)
    trace = run_device(task, schedule([3]), Doubling())
    message = "original reference diagnostic " * 100

    def limited(*args, **kwargs):
        failure = Broken(message, code=code, detail="original boundary detail")
        failure.add_note("cleanup also failed")
        raise failure

    monkeypatch.setattr(reference_module, "expected_run_of", limited)
    with pytest.raises(FailureRaised) as caught:
        comparison_for(task, bundle, trace)
    failure = caught.value.failure.as_dict()
    assert failure["origin"] == "reference"
    assert failure["disposition"] == "no-result"
    assert failure["reason"]["kind"] == (code or "reference-runtime-failed")
    assert failure["reason"]["detail"]["message"] == message
    assert failure["reason"]["detail"]["detail"] == "original boundary detail"
    assert failure["reason"]["detail"]["notes"] == ["cleanup also failed"]
