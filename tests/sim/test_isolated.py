"""What an agent's model can do to the verifier, and what it cannot any more.

`instrumented.load_source` execs the model with restricted builtins and a guarded
`__import__`. Both are worth keeping and neither is isolation, which is a claim these
tests run rather than repeat. The five cases below are what a model can do without
importing anything, and each was demonstrated against the old arrangement first.

None is a hole in the proof. The certificate says nothing about a model that writes a
file, and a model that hangs produces no verdict rather than a wrong one. They matter
because the artifact is written by an agent and the population is 836 of them: a verifier
that a model under test can hang, exhaust or terminate cannot be run unattended.
"""
import pathlib
import subprocess

import pytest

from behave.sim.behavior.isolated import (
    Broken,
    IsolatedModel,
    Limits,
    _raised,
    isolated_model,
    snapshot_of,
    unsupported_of,
)
from behave.sim.behavior.native_values import DEFAULT_WIRE_NODES, WireEncoder
from behave.sim.core.failure import CandidateFault, FailureRaised

MODEL = """class M:
    def reset(self):
        self.n = 0
    def process(self, a):
{body}
        self.n = self.n + a
        return {{"y": a}}
"""

_VALID_REPLY = (
    '{"ok":true,"state":{},"why":{},"carried":{},'
    '"opaque":{},"memories":{},"hits":[]}\n')

#: Small, since a test that waits for a twenty second budget is a test nobody runs.
TIGHT = Limits(seconds=3, memory_bytes=512 << 20, wall_seconds=8)


def build(body, limits=TIGHT):
    return IsolatedModel(MODEL.format(body=body), limits=limits)


def test_an_ordinary_model_runs_and_answers():
    with build("        pass") as model:
        model.reset()
        assert model.process(a=3) == {"y": 3}
        assert model.n == 3, "the attributes come back with the answer"
        assert model.process(a=4) == {"y": 4}
        assert model.n == 7, "and the state carries between steps"


def test_a_negative_shift_keeps_its_typed_failure_across_isolation():
    from behave.sim.behavior.audit import model_audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = """\
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": 8 >> (a - 1)}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[2], outputs=["y"])
    make = isolated_model(source, limits=TIGHT)
    try:
        assert model_audit(proc, make, [[{"a": 0}]], make.hits) == []
    finally:
        make.close()


def test_dynamic_sequence_state_audit_crosses_isolation_logically():
    """The child carries physical state; the contract removes only padding."""
    from behave.sim.behavior.audit import model_audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = """\
class M:
    def reset(self):
        self.rows = [[], []]
    def process(self, a):
        index = a & 1
        self.rows[index] = self.rows[index] + [a]
        return {"y": len(self.rows[index])}
"""
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], bound=4)
    make = isolated_model(source, limits=TIGHT)
    try:
        trace = [[{"a": value} for value in (0, 1, 2)]]
        assert model_audit(proc, make, trace, make.hits) == []
    finally:
        make.close()


def test_call_results_cross_with_exact_native_types():
    source = r'''class M:
    def reset(self):
        self.n = 0
    def process(self, a):
        return {"y": a}
    def tick(self):
        return (b"\x00", {1: "one"}, bytearray(b"x"))
'''
    with IsolatedModel(source, cls="M", limits=TIGHT) as model:
        assert model.tick() == (b"\x00", {1: "one"}, bytearray(b"x"))


def test_valid_large_carrier_is_not_rejected_by_an_implicit_wire_quota():
    source = """class M:
    def reset(self):
        self.payload = b"x" * (5 << 20)
    def process(self, a):
        return {"y": a}
"""
    with IsolatedModel(source, cls="M", limits=TIGHT) as model:
        model.reset()
        assert model.process(a=3) == {"y": 3}
        assert model._carried["payload"] == WireEncoder(None).encode(b"x" * (5 << 20))


def test_repeated_visits_are_compacted_per_call_not_across_steps():
    from behave.sim.behavior import instrumented
    from behave.sim.behavior.extract import source_model
    from behave.sim.behavior.source_plan import plan_source

    source = """class M:
    def reset(self):
        pass
    def process(self, a):
        total = 0
        for i in range(1000):
            if i & 1:
                total += a
        return {"y": total // a}
"""
    namespace, _sites, hits = instrumented.load_source(source, file="hits.py")
    plan = plan_source(source_model(source, file="hits.py"), source, "hits.py")
    native = namespace["M"]()
    with IsolatedModel(source, file="hits.py", cls="M", limits=TIGHT) as model:
        for a in (2, 2, 0):
            hits.clear()
            start = len(model._hits)
            if a:
                assert model.process(a=a) == native.process(a=a)
            else:
                with pytest.raises(ZeroDivisionError) as error:
                    native.process(a=a)
                failure_goal = instrumented.attribute_safety_failure(error.value, plan.safety_sites)
                assert failure_goal is not None
                hits.append(failure_goal)
                with pytest.raises(ZeroDivisionError):
                    model.process(a=a)
            delta = model._hits[start:]
            assert delta and set(delta) == set(hits)
            assert len(delta) == len(set(delta))
            assert len(hits) > 100 * len(delta)


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(b"\xff\n", id="invalid-utf8"),
        pytest.param(_VALID_REPLY.encode("utf-16-be"), id="utf-16-be"),
        pytest.param(_VALID_REPLY.encode("utf-16-le"), id="utf-16-le"),
        pytest.param(_VALID_REPLY.encode("utf-32-be"), id="utf-32-be"),
        pytest.param(_VALID_REPLY.encode("utf-32-le"), id="utf-32-le"),
        pytest.param(b'{"ok":true\n', id="malformed-json"),
        pytest.param(_VALID_REPLY.encode() + b'{}', id="trailing-document"),
        pytest.param(b'\xef\xbb\xbf' + _VALID_REPLY.encode(), id="utf8-bom"),
        pytest.param(b"[]\n", id="wrong-reply-schema"),
        pytest.param(
            b'{"ok":true,"state":{"x":1,"x":2},"why":{},'
            b'"carried":{},"opaque":{},"hits":[]}\n',
            id="duplicate-key"),
        pytest.param(
            b'{"ok":true,"state":{"x":1,"\\u0078":2},"why":{},'
            b'"carried":{},"opaque":{},"hits":[]}\n',
            id="escaped-duplicate-key"),
        pytest.param(
            b'{"ok":true,"state":{},"why":{},"carried":{},'
            b'"opaque":{},"hits":[],"produced":NaN}\n',
            id="nan"),
        pytest.param(
            b'{"ok":true,"state":{},"why":{},"carried":{},'
            b'"opaque":{},"hits":[],"produced":Infinity}\n',
            id="infinity"),
        pytest.param(
            b'{"ok":true,"state":{},"why":{},"carried":{},'
            b'"opaque":{},"hits":[],"produced":-Infinity}\n',
            id="negative-infinity"),
        pytest.param(
            b'{"ok":true,"state":{},"why":{},"carried":{},'
            b'"opaque":{},"hits":[],"produced":1e1000000}\n',
            id="overflowing-float"),
        pytest.param(
            b'{"ok":true,"state":{},"why":{},"carried":{},'
            b'"opaque":{},"hits":[1]}\n',
            id="non-string-hit"),
    ],
)
def test_a_non_strict_child_reply_closes_the_child(reply, monkeypatch):
    model = object.__new__(IsolatedModel)
    model._state = {}
    model._unsupported = {}
    model._carried = {}
    model._opaque = {}
    model._hits = []

    closed = []
    monkeypatch.setattr(model, "_read_with_deadline", lambda: reply)
    monkeypatch.setattr(model, "close", lambda: closed.append(True))

    with pytest.raises(Broken):
        model._hear()
    assert closed, "a protocol-violating child must be closed"


def test_eof_from_a_still_live_child_is_closed(monkeypatch):
    model = object.__new__(IsolatedModel)
    closed = []
    monkeypatch.setattr(model, "_read_with_deadline", lambda: b"")
    monkeypatch.setattr(model, "_why", lambda: "the child stopped answering")
    monkeypatch.setattr(model, "_end_child", lambda: closed.append(True))

    with pytest.raises(Broken, match="stopped answering"):
        model._hear()
    assert closed == [True], "EOF must end the child without waiting for another reply"


def _call_protocol_model(monkeypatch, reply):
    model = object.__new__(IsolatedModel)
    model._offers = ("tick",)
    closed = []
    monkeypatch.setattr(model, "_say", lambda *_args, **_kwargs: None)
    reply = dict(reply)
    if "produced" in reply:
        reply["produced"] = WireEncoder(DEFAULT_WIRE_NODES).encode(
            reply["produced"])
    base = {"ok": True, "state": {}, "why": {}, "carried": {},
            "opaque": {}, "memories": {}, "hits": []}
    monkeypatch.setattr(model, "_hear", lambda: {**base, **reply})
    monkeypatch.setattr(model, "close", lambda: closed.append(True))
    return model, closed


@pytest.mark.parametrize("publish_snapshot", [False, True])
def test_atomic_failure_does_not_publish_a_state_image(monkeypatch, publish_snapshot):
    model, _closed = _call_protocol_model(monkeypatch, {
        "raised": "", "produced": {
            "status": "candidate-fault", "refusal": {
                "kind": "behavior-runtime-invariant",
                "why": "the module bindings changed", "detail": {},
            },
        },
    })
    model._state = {"old": 7}
    model._field_names = frozenset(("old",))
    model._hits = ["old-hit"]
    with pytest.raises(CandidateFault, match="behavior-runtime-invariant"):
        model._atomic_call(
            {"call": "atomic_init"},
            lambda _: pytest.fail("a failed atomic call has no value to decode"),
            publish_snapshot=publish_snapshot)
    assert model._state == {"old": 7}
    assert model._field_names == {"old"}
    assert model._hits == ["old-hit"]


@pytest.mark.parametrize("call", ["reset", "process", "tick", "share"])
@pytest.mark.parametrize(
    "reply",
    [
        pytest.param({"produced": None}, id="missing-raised"),
        pytest.param({"raised": [], "produced": None},
                     id="raised-is-not-a-string"),
        pytest.param({"raised": ""}, id="missing-produced"),
        pytest.param({"raised": "", "produced": None, "extra": 1},
                     id="extra-call-field"),
    ],
)
def test_each_call_rejects_an_ill_typed_call_reply(call, reply, monkeypatch):
    if call == "share" and "produced" in reply:
        reply = dict(reply, produced={"fields": {}, "modules": {}})
    model, closed = _call_protocol_model(monkeypatch, reply)

    with pytest.raises(Broken, match="malformed isolated-model call reply"):
        if call == "reset":
            model.reset()
        elif call == "process":
            model.process(a=1)
        elif call == "tick":
            model.tick(a=1)
        else:
            model._share_image()
    assert closed


@pytest.mark.parametrize(
    "produced",
    [
        pytest.param({}, id="missing-domains"),
        pytest.param({"fields": [], "modules": {}}, id="fields-not-a-map"),
        pytest.param({"fields": {}, "modules": []}, id="modules-not-a-map"),
        pytest.param({"fields": {}, "modules": {}, "extra": {}},
                     id="extra-domain"),
    ],
)
def test_a_malformed_sharing_image_closes_the_child(produced, monkeypatch):
    model, closed = _call_protocol_model(
        monkeypatch, {"raised": "", "produced": produced})
    model._state = {"old": 1}
    model._unsupported = {"old": "why"}
    model._carried = {"old": ["int", 1]}
    model._opaque = {"old": "opaque"}
    model._hits = ["old-hit"]

    with pytest.raises(Broken, match="malformed sharing image"):
        model._share_image()
    assert closed
    assert model._state == {"old": 1}
    assert model._unsupported == {"old": "why"}
    assert model._carried == {"old": ["int", 1]}
    assert model._opaque == {"old": "opaque"}
    assert model._hits == ["old-hit"]


@pytest.mark.parametrize(
    "greeting",
    [
        pytest.param({"offers": ["process", 1], "facts": {}},
                     id="offer-not-a-string"),
        pytest.param({"offers": [], "facts": []}, id="facts-not-a-map"),
        pytest.param({"offers": [], "facts": {}, "raised": ""},
                     id="call-field-raised"),
        pytest.param({"offers": [], "facts": {}, "produced": None},
                     id="call-field-produced"),
        pytest.param({"offers": [], "facts": {}, "extra": 1},
                     id="extra-greeting-field"),
    ],
)
def test_a_malformed_greeting_closes_the_child(greeting, monkeypatch):
    model = object.__new__(IsolatedModel)
    closed = []
    monkeypatch.setattr(model, "close", lambda: closed.append(True))
    base = {"ok": True, "state": {}, "why": {}, "carried": {},
            "opaque": {}, "memories": {}, "hits": []}

    with pytest.raises(Broken, match="malformed isolated-model greeting"):
        model._accept_greeting({**base, **greeting})
    assert closed


def test_a_legal_greeting_needs_no_call_result_fields(monkeypatch):
    model = object.__new__(IsolatedModel)
    monkeypatch.setattr(
        model, "close",
        lambda: pytest.fail("a legal greeting must not close the child"))
    model._accept_greeting({
        "ok": True, "state": {}, "why": {}, "carried": {},
        "opaque": {}, "memories": {}, "hits": [], "offers": ["process"],
        "facts": {"cls": "M"},
    })
    assert model._offers == ("process",)
    assert model._facts == {"cls": "M"}


def test_a_rejected_reply_does_not_publish_its_snapshot(monkeypatch):
    model = object.__new__(IsolatedModel)
    model._state = {"old": 1}
    model._unsupported = {"old": "why"}
    model._carried = {"old": ["int", 1]}
    model._opaque = {"old": "opaque"}
    model._hits = ["old-hit"]
    monkeypatch.setattr(model, "close", lambda: None)

    with pytest.raises(Broken, match="malformed isolated-model call reply"):
        model._accept_call_reply({
            "ok": True,
            "state": {"new": 2},
            "why": {},
            "carried": {},
            "opaque": {},
            "memories": {},
            "hits": ["new-hit"],
            "produced": None,
            "raised": "",
            "extra": True,
        })

    assert model._state == {"old": 1}
    assert model._unsupported == {"old": "why"}
    assert model._carried == {"old": ["int", 1]}
    assert model._opaque == {"old": "opaque"}
    assert model._hits == ["old-hit"]


def test_close_releases_pipes_even_after_the_child_has_exited():
    model = build("        pass")
    child = model._child
    child.kill()
    child.wait(timeout=2)

    model.close()

    assert model._child is None
    assert child.stdin.closed
    assert child.stdout.closed


@pytest.mark.parametrize("during_failure", [False, True])
def test_model_cleanup_retains_owner_and_original_failure(monkeypatch, during_failure):
    import io

    from behave.core import runtime_defaults as defaults
    from behave.sim.behavior.isolated import Timeout

    waits = []

    class StuckProcess:
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def poll(self):
            return None

        def kill(self):
            pass

        def wait(self, timeout):
            waits.append(timeout)
            error = subprocess.TimeoutExpired("stuck-child", timeout)
            error.add_note("namespace owner still holds child 123")
            raise error

    model = object.__new__(IsolatedModel)
    model._child = child = StuckProcess()
    monkeypatch.setattr(defaults, "SERVICE_CLEANUP_SECONDS", 7)
    monkeypatch.setattr("time.monotonic", lambda: 10.0)
    try:
        if during_failure:
            original = Timeout("original execution deadline")
            with pytest.raises(Timeout) as caught:
                model._fail(original)
            assert caught.value is original
            assert "cleanup unconfirmed" in caught.value.__notes__[0]
            assert "namespace owner still holds child 123" in caught.value.__notes__[0]
        else:
            with pytest.raises(FailureRaised) as caught:
                model._end_child()
            assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)
        assert waits == [7]
        assert model._child is child
        assert not child.stdout.closed
    finally:
        # Fake ownership only: don't let __del__ start another fake reap.
        model._child = None


def test_model_close_does_not_wait_before_stopping(monkeypatch):
    import io

    from behave.core import runtime_defaults as defaults

    waits = []
    clock = [10.0]

    class StuckUntilStopped:
        def __init__(self):
            self.stdin, self.stdout, self.stderr = (io.BytesIO() for _ in range(3))
            self.stopped = False

        def poll(self):
            return None

        def kill(self):
            self.stopped = True

        def wait(self, timeout):
            waits.append(timeout)
            if not self.stopped:
                clock[0] += 7.0
                raise subprocess.TimeoutExpired("child", timeout)

    model = object.__new__(IsolatedModel)
    model._child = child = StuckUntilStopped()
    monkeypatch.setattr(defaults, "SERVICE_CLEANUP_SECONDS", 7)
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr(model, "_say", lambda message: None)
    try:
        model.close()
        assert waits == [7.0]
        assert model._child is None
        assert child.stdout.closed
    finally:
        model._child = None


def test_model_close_retry_does_not_grant_another_cleanup_window(monkeypatch):
    """Failure handlers, context exit and __del__ keep one child's deadline."""
    import io

    from behave.core import runtime_defaults as defaults

    clock, waits = [10.0], []

    class DelayedReap:
        def __init__(self):
            self.stdin, self.stdout, self.stderr = (io.BytesIO() for _ in range(3))
            self.reaped = False

        def poll(self):
            return 0 if self.reaped else None

        def kill(self):
            pass

        def wait(self, timeout):
            waits.append(timeout)
            clock[0] += timeout
            raise subprocess.TimeoutExpired("delayed-child", timeout)

    model = object.__new__(IsolatedModel)
    model._child = child = DelayedReap()
    monkeypatch.setattr(defaults, "SERVICE_CLEANUP_SECONDS", 7)
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    try:
        for _ in range(2):
            with pytest.raises(FailureRaised) as caught:
                model._end_child()
            assert isinstance(caught.value.__cause__, subprocess.TimeoutExpired)
        assert waits == [7.0, 0.0]
        assert model._child is child
        child.reaped = True
        model.close()  # An already reaped child can still release its pipes.
        assert model._child is None
        assert child.stdout.closed
    finally:
        model._child = None


def test_cleanup_oserror_cannot_replace_model_execution_failure(monkeypatch):
    from types import SimpleNamespace

    from behave.sim.behavior.isolated import Timeout

    def cannot_stop():
        raise OSError("stop failed")

    model = object.__new__(IsolatedModel)
    model._child = child = SimpleNamespace(poll=lambda: None, kill=cannot_stop)
    original = Timeout("original model error")
    try:
        with pytest.raises(Timeout) as caught:
            model._fail(original)
        assert caught.value is original
        assert "OSError: stop failed" in caught.value.__notes__[0]
        assert model._child is child
    finally:
        model._child = None


def test_constructor_failure_reaps_the_started_child(monkeypatch):
    started = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        started.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    with pytest.raises(TypeError):
        IsolatedModel(MODEL.format(body="        pass"), limits=TIGHT,
                      config={"not_json": object()})

    # An uncached source-facts judge precedes the candidate child.  Both use protocol
    # pipes, so the candidate is the final process started; a warmed judge cache may
    # leave it as the only one.
    assert all(child.poll() is not None for child in started)
    assert started
    child = started[-1]
    assert child.poll() is not None
    assert child.stdin.closed
    assert child.stdout.closed


def test_parent_obtains_source_facts_only_through_the_named_judge(monkeypatch):
    from behave.sim import worker
    from behave.sim.behavior import isolated as isolated_module

    answer = {
        "version": isolated_module._SOURCE_FACTS_VERSION,
        "facts": {
            "cls_name": "M",
            "process_inputs": ["a"],
            "process_kwargs": False,
            "process_optional_inputs": [],
            "domains": {},
            "rom_names": [],
            "unanswerable_calls": [],
            "ownership": {},
        },
        "offers": ["reset", "process"],
        "frontend_catalogue": {
            "goals": [], "occurrences": [],
            "version": "catalogue-manifest/1",
        },
    }
    calls = []

    def isolated_answer(name, payload, **_kwargs):
        calls.append((name, payload))
        return answer

    # Environment text is not evidence that this caller is below the judge.  This
    # exact spelling was once the bypass: setting it made the verifier parse locally.
    monkeypatch.setenv("BEHAVE_NAMED_JOB_ISOLATED", "1")
    isolated_module._isolated_source_protocol_facts.cache_clear()
    monkeypatch.setattr(worker, "run_job_strict", isolated_answer)
    monkeypatch.setattr(
        isolated_module, "_source_protocol_facts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("the verifier parsed submitted Python")))

    facts, offers, frontend = isolated_module._trusted_source_protocol_facts(
        "class M: pass", "behavior.py", "M")

    assert facts == answer["facts"]
    assert offers == ("reset", "process")
    assert tuple(frontend) == ()
    assert calls == [(
        "behavior_source_facts",
        {"source": "class M: pass", "file": "behavior.py", "class": "M",
         "source_rules": {"operations": [], "reads": []}, "clock_domains": None},
    )]
    assert "behavior_source_facts" in worker.JOBS


@pytest.mark.parametrize("name", ["SystemExit", "KeyboardInterrupt"])
def test_a_reported_control_exception_cannot_exit_the_parent(name):
    raised = _raised(name)

    assert type(raised) is RuntimeError
    assert str(raised) == name


def test_a_model_that_ends_the_process_ends_only_its_own():
    """The plainest case and the one that decides whether the rest is worth having: in
    one process a model calling `os._exit` takes the verifier with it, and no report is
    written for the run it was part of.

    The guarded import refuses this one before it happens, which is the admission
    defence doing its job. What the isolation adds is that it would not matter if it
    did not: the exit reaches a child.
    """
    with pytest.raises(ImportError), build('        __import__("os")._exit(7)') as model:
        model.reset()
        model.process(a=1)


def test_a_model_reaching_open_through_the_object_graph_cannot_write():
    """The case that settles the difference between an admission defence and isolation.

    Restricted builtins do not close off the object graph:
    `().__class__.__base__.__subclasses__()` reaches `catch_warnings`, whose
    `_module.__builtins__` is the real builtins, and `open` is in it. No import is
    involved, so guarding `__import__` cannot help, and measured against the old
    arrangement this wrote the file.

    Two lines stop it now, and the second is there because the first was measured and
    the measurement was misread. `RLIMIT_FSIZE` of zero makes the write fail, and the
    file is still created empty, which `exists()` reports as success; so the audit hook
    refuses the open as well, and nothing appears at all.
    """
    marker = pathlib.Path(__file__).with_name("isolation-escape-marker.txt")
    marker.unlink(missing_ok=True)
    body = ('        for c in ().__class__.__base__.__subclasses__():\n'
            '            if c.__name__ == "catch_warnings":\n'
            '                g = c()._module.__builtins__\n'
            f'                f = g["open"](r"{marker}", "w")\n'
            '                f.write("escaped")\n'
            '                f.flush()\n'
            '                break')
    try:
        with pytest.raises(PermissionError), build(body) as model:
            model.reset()
            model.process(a=1)
        assert not marker.exists(), "not even an empty file"
    finally:
        marker.unlink(missing_ok=True)


def test_a_model_that_does_not_stop_is_stopped():
    """No syscall, no import, nothing to refuse: the CPU limit is what ends it, and the
    parent hears that the child was ended by a signal rather than waiting."""
    with (pytest.raises(Broken, match="SIGXCPU|SIGKILL|stopped answering"),
          build("        while True:\n            pass") as model):
        model.reset()
        model.process(a=1)


def test_a_model_that_allocates_without_limit_fails_as_the_model():
    """The address-space limit turns it into the model's `MemoryError` rather than the
    verifier's, which is the difference between one refused artifact and a lost run."""
    with pytest.raises(MemoryError), build("        x = [0] * (1 << 34)") as model:
        model.reset()
        model.process(a=1)


def test_a_model_that_opens_a_socket_is_refused():
    """What a resource limit cannot see. The audit hook fires before the operation and
    cannot be removed once installed."""
    with pytest.raises((ImportError, PermissionError)), build('        __import__("socket").socket()') as model:
        model.reset()
        model.process(a=1)


def test_the_child_says_why_a_value_could_not_be_reported():
    """Both halves cross the channel, because the parent has no other way to tell an
    attribute the model does not have from one it has in a shape a step cannot carry.
    Recursive integer arrays cross the same channel without losing their shape.
    """
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.flat = [1, 2]\n"
              "        self.deep = [[1], [2]]\n"
              "        self.count = 5\n"
              "    def process(self, a):\n"
              "        return {'y': a}\n")
    with IsolatedModel(source, limits=TIGHT) as model:
        model.reset()
        assert snapshot_of(model) == {
            "flat": [1, 2], "deep": [[1], [2]], "count": 5}
        assert unsupported_of(model) == {}


def test_the_factory_gives_a_fresh_process_and_keeps_one_alive():
    """`model_audit` constructs a model per trace, so a failure on one trace must not be
    the next trace's problem. Shaped as a factory for that reason, and so that
    `model_audit` needs no change: eight scripts call it and a signature change would
    break them for a reason that has nothing to do with what they measure.

    **Only the latest is usable**, which is the contract and not an accident. Building
    the next one closes the last, because the population run is 836 models of sixteen
    traces and a factory that keeps every child would leave thousands alive. This test
    asserted the opposite first, and the assertion was the thing that was wrong: nothing
    reuses an earlier model, `model_audit` having finished with one before it asks for
    the next.
    """
    make = isolated_model(MODEL.format(body="        pass"), limits=TIGHT)
    try:
        first = make()
        first.reset()
        first.process(a=5)
        assert first.n == 5

        second = make()
        second.reset()
        assert second.n == 0, "a separate process, so a separate state"
        with pytest.raises(Broken):
            first.process(a=1)
    finally:
        make.close()


def test_the_hits_follow_whichever_model_was_built_last():
    """`model_audit` is handed one hit list for the whole run and constructs a model per
    trace. In one process that is right, the hits coming from the loaded module, which
    outlives the models. Here each model is a separate child with hits of its own, so a
    fixed list would be the first child's and would stay empty while the rest ran, and
    every goal the host reached would be reported as a disagreement.
    """
    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.n = 0\n"
              "    def process(self, a):\n"
              "        if a > 2:\n"
              "            self.n = 1\n"
              "        return {'y': a}\n")
    make = isolated_model(source, limits=TIGHT)
    try:
        hits = make.hits
        first = make()
        first.reset()
        first.process(a=5)
        taken = set(hits)
        assert taken, "the host reported the branch it took"

        second = make()
        second.reset()
        assert set(hits) == set(), "a new child, and the view follows it"
        second.process(a=0)
        assert set(hits) and set(hits) != taken, "the other outcome, this time"
    finally:
        make.close()


CLASS_AND_MODULE = """COUNT = 0

class M:
    COUNT = 0
    SEEN = []

    def __init__(self):
        self.total = 0

    def reset(self):
        global COUNT
        COUNT = 0
        M.COUNT = 0
        M.SEEN.clear()
        self.total = 0

    def process(self, a):
        global COUNT
        COUNT += 1
        M.COUNT += a
        M.SEEN.append(a)
        self.total = M.COUNT + COUNT
        return {"y": M.COUNT}
"""

NEW_GLOBAL = """class M:
    def __init__(self):
        self.total = 0

    def reset(self):
        self.total = 0

    def process(self, a):
        global STASH
        STASH = a
        self.total += a
        return {"y": self.total}
"""

NAN_IMAGE = """X = float("nan")

class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        global X
        X = -X
        return {"y": a}
"""


def test_a_reference_model_may_keep_class_and_module_state():
    """Review 47. The frozen class table and module image are lowering
    semantics; a reference model (strict_module=False) is not lowered and
    may accumulate in class attributes and module globals across greeting,
    reset and every step. Only the proxy-safety checks remain."""
    with IsolatedModel(CLASS_AND_MODULE, cls="M", limits=TIGHT,
                       strict_module=False) as model:
        model.reset()
        assert [model.process(a=k)["y"] for k in (1, 2, 3)] == [1, 3, 6]
        assert snapshot_of(model)["total"] == 9
        model.reset()
        assert model.process(a=5)["y"] == 5, "reset really cleared it"
    with IsolatedModel(NEW_GLOBAL, cls="M", limits=TIGHT,
                       strict_module=False) as model:
        model.reset()
        assert [model.process(a=k)["y"] for k in (1, 2)] == [1, 3]


def test_a_certified_model_may_not_touch_class_or_module_state():
    """The same shapes under the certified default: the frozen invariants
    fail closed, a new module name included (review 47 closed the key-set
    gap: a binding absent at freeze time was previously unchecked)."""
    with (IsolatedModel(CLASS_AND_MODULE, cls="M", limits=TIGHT) as model,
          pytest.raises(CandidateFault, match="behavior-runtime-invariant")):
        # reset rebinds only interned equal values, so the first identity
        # change may land on the first step rather than on reset itself
        model.reset()
        model.process(a=1)
    with IsolatedModel(NEW_GLOBAL, cls="M", limits=TIGHT) as model:
        model.reset()
        with pytest.raises(CandidateFault, match="behavior-runtime-invariant"):
            model.process(a=1)


def test_the_module_image_compares_float_bits_not_repr():
    """Two NaNs have the same repr even when their binary64 images differ.

    A certified module cannot change a folded value through that hole; reference
    mode remains free to keep module state because it makes no lowering claim.
    """
    with IsolatedModel(NAN_IMAGE, cls="M", limits=TIGHT) as model:
        model.reset()
        with pytest.raises(CandidateFault, match="behavior-runtime-invariant"):
            model.process(a=1)
    with IsolatedModel(NAN_IMAGE, cls="M", limits=TIGHT,
                       strict_module=False) as model:
        model.reset()
        assert model.process(a=1) == {"y": 1}


OPAQUE_MODULE = """class Blob:
    def __init__(self):
        self.k = 1

BLOB = Blob()

class M:
    def __init__(self):
        self.total = 0

    def reset(self):
        self.total = 0

    def process(self, a):
        self.total += a + BLOB.k
        return {"y": self.total}
"""

DIR_FORGED = """class M:
    def __dir__(self):
        return []

    def reset(self):
        self.s = 0

    def process_w(self, w):
        return {"y": w}

    def process_r(self, r):
        return {"y": r}
"""


def test_reference_mode_never_builds_the_module_image():
    """Review 48. The module image belongs to certified mode; a reference
    model whose module holds an object the imager refuses must still
    start, step, and share."""
    with IsolatedModel(OPAQUE_MODULE, cls="M", limits=TIGHT,
                       strict_module=False) as model:
        model.reset()
        assert model.process(a=2)["y"] == 3
    with IsolatedModel(CLASS_AND_MODULE, cls="M", limits=TIGHT,
                       strict_module=False) as model:
        model.reset()
        model.process(a=1)
        report = model._share_map()
        assert "total" in report, "share honors the mode too"


def test_the_offered_domains_come_from_the_source_not_dir():
    """A candidate's __dir__ cannot hide (or invent) a process_* handler:
    the domains are read off the source model source_model chose, not off
    the runtime object's __dir__ (reviews 49-50)."""
    from behave.sim.behavior.isolated import facts_of

    facts = facts_of(DIR_FORGED, limits=TIGHT)
    domains = facts.get("domains") or {}
    assert set(domains) == {"w", "r"}, facts
    assert domains["w"]["inputs"] == ["w"]
    assert facts["ownership"] == {"s": []}, \
        "the named source judge also freezes multi-clock state ownership"


CLS_NAME_INJECT_MODULE = """class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        return {"y": a}


M.__name__ = "M\\nPWNED = 1 #"
"""

SIGNATURE_FORGE = """class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        return {"y": a}


M.process.__signature__ = 7
"""

PRELOAD_BUILTIN = """def evil(n):
    return [0, 1]


__builtins__["range"] = evil


class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        y = 0
        for i in range(1):
            y = y + 1
        return {"y": y}
"""

CLASSBODY_SWAP = """def evil(self, a):
    return a + 1


class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        return {"y": a}


M.process = evil
"""


def test_facts_come_from_the_verified_source_not_runtime():
    """Review 49. The class name is the source binding, not the mutable
    runtime __name__, and the step's parameters are read off the source
    AST, not a forgeable inspect.signature."""
    from behave.sim.behavior.isolated import facts_of

    facts = facts_of(CLS_NAME_INJECT_MODULE, cls="M", limits=TIGHT)
    assert facts["cls_name"] == "M", "a mutated __name__ cannot reach the TB"
    assert facts["process_inputs"] == ["a"]

    forged = facts_of(SIGNATURE_FORGE, cls="M", limits=TIGHT)
    assert forged["process_inputs"] == ["a"], \
        "a forged __signature__ does not become zero inputs"


def test_candidate_stdout_cannot_choose_parent_source_metadata():
    """A schema-valid line is still untrusted when candidate code wrote it."""
    from behave.sim.behavior.isolated import facts_of

    source = r'''
for candidate in ().__class__.__base__.__subclasses__():
    if candidate.__name__ == "catch_warnings":
        builtins = candidate()._module.__builtins__
        system = builtins["__import__"]("sys")
        system.__stdout__.write(
            '{"ok":true,"state":{},"why":{},"carried":{},'
            '"opaque":{},"memories":{},"hits":[],"offers":[],'
            '"facts":{"cls_name":"FORGED","process_inputs":["evil"],'
            '"domains":{},"rom_names":[]}}\n')
        system.__stdout__.flush()
        break

class M:
    def reset(self):
        self.s = 0
    def process(self, a):
        return {"y": a}
'''

    with pytest.raises(Broken, match="trusted source metadata"):
        facts_of(source, cls="M", limits=TIGHT)


def test_a_load_that_mutates_the_environment_is_refused():
    """The builtins a model swaps at load, or a method rebound after its
    class body, would make the mutation the certified baseline; the
    certification path refuses both rather than freezing after the fact."""
    from behave.sim.behavior.isolated import Broken, facts_of

    with pytest.raises(CandidateFault) as caught:
        facts_of(PRELOAD_BUILTIN, cls="M", limits=TIGHT)
    assert caught.value.refusal.kind == "candidate-load-failed"
    assert caught.value.refusal.detail["error"] == "TypeError"
    with pytest.raises(Broken):
        facts_of(CLASSBODY_SWAP, cls="M", limits=TIGHT)


@pytest.mark.parametrize("replacement", [
    '__repr__ = lambda self: "replacement"',
    'del __repr__',
    'unused: int',
])
def test_method_audit_checks_final_class_bindings(replacement):
    source = '''class M:
    def __repr__(self):
        return "dead definition"
    REPLACEMENT
    def reset(self):
        self.n = 0
    def process(self, a):
        self.n += a
        return {"y": self.n}
'''.replace("REPLACEMENT", replacement)
    with IsolatedModel(source, cls="M", limits=TIGHT) as model:
        model.reset()
        assert model.process(a=2) == {"y": 2}
        assert model.process(a=3) == {"y": 5}


def test_final_helper_binding_still_rejects_module_level_replacement():
    source = '''class M:
    def reset(self):
        pass
    def helper(self, a):
        return a
    def process(self, a):
        return {"y": self.helper(a)}
def replacement(self, a):
    return a + 1
M.helper = replacement
'''
    with pytest.raises(Broken, match="class method helper"):
        IsolatedModel(source, cls="M", limits=TIGHT)


def test_the_change_check_does_not_keep_pinning_references():
    """The per-reply module comparison must not grow the kept-reference
    list on every step (review 48's leak, now a permanent guard): a
    function imaged for change detection with keep=False adds nothing."""
    import re

    from behave.sim.behavior import isolated

    child = isolated._CHILD
    start = child.index("    _kept_refs = []")
    end = child.index("    # The module names the lowering may fold")
    block = "import types as _pytypes\nimport functools as _functools\n" + re.sub(
        r"^    ", "", child[start:end], flags=re.MULTILINE)
    from behave.core import runtime_defaults

    ns = {"_image_nodes": runtime_defaults.SIM_SOURCE_NAMESPACE_NODES}
    exec(block, ns)                                             # noqa: S102
    image = ns["_data_image"]
    probe = (lambda v: v)
    image(probe)
    before = len(ns["_kept_refs"])
    image(probe, keep=False)
    assert len(ns["_kept_refs"]) == before, "keep=False must not pin"


DECOY_ALIAS = """class Decoy:
    def reset(self):
        self.s = 0

    def process(self):
        return {"y": 0}


class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        return {"y": a + 1}


Decoy = M
"""

RENAMED_RECEIVER = """class M:
    def reset(self):
        self.s = 0

    def process(model, a):
        return {"y": a + 1}
"""

TEMP_BUILTIN_SWAP = """_real = range


def evil(n):
    return [0, 1]


__builtins__["range"] = evil
__builtins__["range"] = _real


class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        y = 0
        for i in range(1):
            y = y + 1
        return {"y": y}
"""


def test_facts_use_the_class_source_model_chose_not_a_namespace_scan():
    """Review 50. An aliased empty `Decoy = M` must not be picked for the
    facts: the authority is the class source_model chose, and its inputs
    are the receiver-dropped source parameters, so a renamed receiver is
    refused rather than reported as an extra input."""
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.isolated import Broken, facts_of

    facts = facts_of(DECOY_ALIAS, cls="M", limits=TIGHT)
    assert facts["cls_name"] == "M"
    assert facts["process_inputs"] == ["a"]

    with pytest.raises((Broken, LoweringError, ValueError)):
        facts_of(RENAMED_RECEIVER, cls="M", limits=TIGHT)


def test_a_temporary_builtins_swap_is_refused_even_when_restored():
    """The builtins table is read-only during load, so a model that swaps a
    builtin and restores it before the module finishes fails at the write
    rather than passing an after-the-fact identity check (review 50)."""
    from behave.sim.behavior.isolated import facts_of

    with pytest.raises(CandidateFault) as caught:
        facts_of(TEMP_BUILTIN_SWAP, cls="M", limits=TIGHT)
    assert caught.value.refusal.kind == "candidate-load-failed"
    assert caught.value.refusal.detail["error"] == "TypeError"


INJECTED_INIT = """class Evil:
    def __init__(self):
        self.helper = 5


class M:
    def helper(self, v):
        return v + 1

    def reset(self):
        self.s = 0

    def process(self, a):
        return {"y": self.helper(a)}


M.__init__ = Evil.__init__
"""

IOR_BUILTIN_SWAP = """def swap(table, value):
    table |= {"range": value}


def evil(n):
    return [0, 1]


swap(__builtins__, evil)
swap(__builtins__, range)


class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        y = 0
        for i in range(1):
            y = y + 1
        return {"y": y}
"""


def test_a_runtime_member_the_source_does_not_define_is_refused():
    """Review 51. A module-level `M.__init__ = Evil.__init__` adds a
    constructor the source class has none of, whose instance field would
    shadow a method the IR still inlines; the runtime class must define only
    what its source body binds."""
    from behave.sim.behavior.isolated import Broken, facts_of

    with pytest.raises((Broken, ValueError)):
        facts_of(INJECTED_INIT, cls="M", limits=TIGHT)


def test_the_builtins_table_cannot_be_written_through_dict_operators():
    """The builtins table is a read-only mapping proxy, so `table |= {...}`
    (dict.__ior__) and every other inherited mutator are gone, not just the
    ones a subclass blacklist covered (review 51)."""
    from behave.sim.behavior.isolated import facts_of

    with pytest.raises(CandidateFault) as caught:
        facts_of(IOR_BUILTIN_SWAP, cls="M", limits=TIGHT)
    assert caught.value.refusal.kind == "candidate-load-failed"
    assert caught.value.refusal.detail["error"] == "TypeError"


MUTATED_CLASS_CONST = """class M:
    K = 1

    def reset(self):
        self.s = 0

    def process(self, a):
        return {"y": self.K}


M.K = 2
"""

BIND_BUILTINS = """__builtins__ = dict(__builtins__)


class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        return {"y": a}
"""


def test_a_class_constant_changed_after_the_body_is_refused():
    """Review 51 (final). A module-level `M.K = 2` over a body `K = 1`
    leaves the audit reading 2 while the lowering folds 1; the runtime
    class constant must still hold its source value."""
    from behave.sim.behavior.isolated import Broken, facts_of

    with pytest.raises((Broken, ValueError)):
        facts_of(MUTATED_CLASS_CONST, cls="M", limits=TIGHT)


def test_binding_the_builtins_name_is_refused_statically():
    """Any source that binds `__builtins__` is refused before it runs, so a
    swap-and-restore that would leave functions holding the swapped table
    never happens (review 51 final)."""
    from behave.sim.behavior.isolated import Broken, facts_of

    with pytest.raises((Broken, ValueError)):
        facts_of(BIND_BUILTINS, cls="M", limits=TIGHT)


CLASS_CONST_MUTATED = """N = 1


class M:
    K = N

    def reset(self):
        self.s = 0

    def process(self, a):
        return {"y": self.K}


M.K = 2
"""


def test_a_mutated_class_constant_is_caught_against_the_source_value():
    """Review 51. A class constant defined from a module constant (`K = N`)
    is checked at runtime against the value the lowering folded, computed
    over the same module environment, so `M.K = 2` over a body value of 1 is
    refused rather than lowered and only caught later by a finite audit."""
    from behave.sim.behavior.isolated import Broken, facts_of

    with pytest.raises((Broken, ValueError)):
        facts_of(CLASS_CONST_MUTATED, cls="M", limits=TIGHT)


MODULE_TABLE_MUTATED = """TABLE = {0: 1}
TABLE[0] = 2


class M:
    def reset(self):
        self.s = 0

    def process(self, a):
        return {"y": TABLE[a]}
"""


def test_a_module_table_mutated_after_its_literal_is_not_a_constant():
    """Review 52. A module table mutated after its literal (`TABLE[0] = 2`)
    is not the literal the static fold reads; it is disqualified so the read
    is refused rather than lowered to the stale value 1 while the runtime
    freezes 2."""
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    with pytest.raises(LoweringError):
        lower_with_state_ranges(MODULE_TABLE_MUTATED, input_widths=[8])


CONSTRUCTOR_STEP_SHADOW = """def evil(a):
    return {"y": 99}


class M:
    def __init__(self):
        self.process = evil
    def reset(self):
        self.s = 0
    def process(self, a):
        return {"y": a}
"""

RESET_STEP_SHADOW = """def evil(a):
    return {"y": 99}


class M:
    def reset(self):
        self.s = 0
        self.process = evil
    def process(self, a):
        return {"y": a}
"""

RESET_DOMAIN_STEP_SHADOW = """def evil(a):
    return {"y": 99}


class M:
    def reset(self):
        self.process_fast = evil
    def process_fast(self, a):
        return {"y": a}
"""


def test_an_instance_cannot_shadow_the_selected_step_during_construction():
    """Native dispatch must remain the source method lowering selected."""
    with pytest.raises(Broken):
        IsolatedModel(CONSTRUCTOR_STEP_SHADOW, cls="M", limits=TIGHT)


def test_an_instance_cannot_shadow_the_selected_step_during_reset():
    with IsolatedModel(RESET_STEP_SHADOW, cls="M", limits=TIGHT) as model, pytest.raises(Broken):
        model.reset()


def test_an_instance_cannot_shadow_a_selected_clock_domain_step():
    with IsolatedModel(
            RESET_DOMAIN_STEP_SHADOW, cls="M", limits=TIGHT) as model, pytest.raises(Broken):
        model.reset()


def test_a_large_shared_dag_has_one_bounded_namespace_image():
    declarations = ["v0 = [0]"]
    for depth in range(1, 21):
        declarations.append(f"v{depth} = [v{depth - 1}, v{depth - 1}]")
    source = "\n".join(declarations) + """

class M:
    def reset(self):
        self.s = 0
    def process(self, a):
        return {"y": a}
"""

    # The reachable graph has 21 list nodes but over 2**20 paths.  Imaging
    # paths recursively is exponential; a pass-wide identity memo makes its
    # cost proportional to the actual graph.
    with IsolatedModel(source, cls="M", limits=TIGHT) as model:
        model.reset()
        assert model.process(a=3) == {"y": 3}


def test_an_exported_plain_model_instance_has_a_complete_namespace_image():
    source = """class M:
    def __init__(self):
        self.s = 7
    def reset(self):
        self.s = 0
    def process(self, a):
        self.s += a
        return {"y": self.s}

exported = M()
"""
    with IsolatedModel(source, cls="M", limits=TIGHT) as model:
        model.reset()
        assert model.process(a=2) == {"y": 2}
        assert model.process(a=3) == {"y": 5}


@pytest.mark.parametrize("imports", ["from fp_lib import annotations", "import fp_lib"])
def test_plain_library_feature_objects_do_not_break_namespace_preparation(imports):
    source = imports + """
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": a}
"""
    with IsolatedModel(source, cls="M", limits=TIGHT) as model:
        model.reset()
        assert model.process(a=3) == {"y": 3}


@pytest.mark.parametrize("mutation", [
    "DATA.value = a",
    "DATA.items.append(a)",
    "DATA = Box()",
    "Box.read = Box.other",
])
def test_frozen_plain_object_fields_identity_and_methods_cannot_change(mutation):
    source = """class Box:
    def __init__(self):
        self.value = 7
        self.items = []
    def read(self):
        return self.value
    def other(self):
        return 0

DATA = Box()

class M:
    def reset(self):
        pass
    def process(self, a):
        global DATA
        MUTATION
        return {"y": a}
""".replace("MUTATION", mutation)
    with IsolatedModel(source, cls="M", limits=TIGHT) as model:
        model.reset()
        # A typed guard rejection crosses the protocol without publishing state.
        with pytest.raises(CandidateFault, match="behavior-runtime-invariant"):
            model.process(a=3)


@pytest.mark.parametrize("declaration", [
    "class Box:\n    @property\n    def __dict__(self): return {}",
    "class Box(int):\n    pass",
])
def test_namespace_objects_with_unrepresented_storage_are_not_shallow_frozen(declaration):
    source = declaration + """

DATA = Box()
class M:
    def process(self, a):
        return {"y": a}
"""
    with pytest.raises(Broken):
        IsolatedModel(source, cls="M", limits=TIGHT)


def test_a_namespace_cycle_has_a_finite_image_and_still_detects_mutation():
    source = """CYCLE = []
CYCLE.append(CYCLE)

class M:
    def reset(self):
        self.s = 0
    def process(self, a):
        if a:
            CYCLE.append(a)
        return {"y": a}
"""

    with IsolatedModel(source, cls="M", limits=TIGHT) as model:
        model.reset()
        assert model.process(a=0) == {"y": 0}
        with pytest.raises(CandidateFault, match="behavior-runtime-invariant"):
            model.process(a=1)


def test_the_namespace_image_has_one_aggregate_node_budget():
    source = """WIDE = [0] * 200001

class M:
    def reset(self):
        self.s = 0
    def process(self, a):
        return {"y": a}
"""

    # A per-root or per-recursion budget can be reset indefinitely.  The
    # entire namespace image consumes one aggregate allowance.
    with pytest.raises(Broken):
        IsolatedModel(source, cls="M", limits=TIGHT)


def test_external_memory_binding_survives_reference_resets():
    from behave.sim.core.memory import (
        MemoryEnvironment,
        MemoryInterface,
        MemoryPort,
        MemoryRun,
    )

    source = """class M:
    def __init__(self):
        self.reset()
    def reset(self):
        self.ram = [0] * 8
    def process(self, address, write, value):
        if write:
            self.ram[address] = value
        return {"y": self.ram[address]}
"""
    memory = MemoryInterface("ram", 3, 4, "clk", [MemoryPort("rw")])
    environment = MemoryEnvironment([MemoryRun(
        "ram", initial={2: 5}, ready={None: [1, 1, 1]})])

    with IsolatedModel(source, cls="M", limits=TIGHT,
                       strict_module=False) as model:
        model._bind_external_memory((memory,), environment, 3)
        assert model.process(address=2, write=0, value=0) == {"y": 5}
        assert model.process(address=2, write=1, value=14) == {"y": 14}
        model.reset()
        assert model.process(address=2, write=0, value=0) == {"y": 14}


def test_large_external_memory_replay_uses_one_sparse_exact_channel():
    from behave.sim.symbolic.uniform_array import UniformArray
    from behave.sim.core.memory import (
        MemoryEnvironment,
        MemoryInterface,
        MemoryPort,
        MemoryRun,
    )

    source = """class M:
    def __init__(self):
        self.reset()
    def reset(self):
        self.ram = [0] * 65536
    def process(self, address, value):
        self.ram[address] = value
        return {"y": self.ram[address]}
"""
    memory = MemoryInterface("ram", 16, 32, "clk", [MemoryPort("rw")])
    environment = MemoryEnvironment([MemoryRun(
        "ram", ready={None: [1] * 64})])
    limits = Limits(
        seconds=30, memory_bytes=512 << 20, wall_seconds=8)

    with IsolatedModel(source, cls="M", limits=limits,
                       strict_module=False) as model:
        model._bind_external_memory((memory,), environment, 64)
        for address in range(64):
            assert model.process(address=address, value=address + 1) == {
                "y": address + 1}
        image = snapshot_of(model)["ram"]
        assert type(image) is UniformArray
        assert image.length == 65536
        assert image.sparse_items() == tuple(
            (address, address + 1) for address in range(64))
