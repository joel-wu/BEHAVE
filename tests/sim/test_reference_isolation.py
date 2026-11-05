"""Submitted code stays behind its boundary, and a reference is what its digest says.

Split out of `test_wave1_closure.py` by concern: the frozen bundle, the isolated
adapter's fresh process per model, the materialised sources, the spawn+JSON worker, and
the paths that must never execute a candidate in the deciding process. Shared fixtures
are imported from where they are defined.
"""
import pathlib
import shutil

import pytest

from behave.sim import api
from behave.sim.core.refusal import Refusal, Refused
from tests.support.device_boundary import run_device
from tests.support.scoring_task import stage_handshake_register

from .test_api_path import Deaf, Doubling, _evaluate, _live, _ref, contract, realization, schedule  # noqa: F401
from .test_wave1_closure import HOLD, _concrete, _top_of  # noqa: F401


@pytest.fixture
def model_problem():
    """Isolation regressions use a fixed test model, not the evolving benchmark."""
    problem = stage_handshake_register()
    try:
        yield problem
    finally:
        shutil.rmtree(problem)


def _accumulating():
    """A model that keeps its state where no per-instance freshness can reach it."""
    return b'''
SEEN = []

class Accumulating:
    def reset(self):
        pass

    def process(self, a):
        SEEN.append(a)
        return {"y": len(SEEN)}
'''


@pytest.mark.parametrize("phase", ["source-facts", "task-binding", "worker-start"])
@pytest.mark.parametrize("candidate_fault", [False, True])
def test_reference_preparation_keeps_transport_and_source_faults_distinct(
    tmp_path, monkeypatch, phase, candidate_fault,
):
    from types import SimpleNamespace

    from behave.sim import reference_isolated
    from behave.sim.behavior import isolated
    from behave.sim.core.failure import (
        Broken,
        CandidateFault,
        FailureOrigin,
        FailureRaised,
    )

    body = b"class Model:\n    pass\n"
    (tmp_path / "behavior.py").write_bytes(body)
    monkeypatch.setattr(reference_isolated, "_materialise", lambda sources: str(tmp_path))
    reason = Refusal("reference-source-invalid", "fixture source fault")

    def fail(*_args, **_kwargs):
        if candidate_fault:
            raise CandidateFault(reason)
        raise Broken("fixture startup failure", code="isolation-launch-failed",
                     detail="namespace helper did not become ready")

    def facts(*_args, **_kwargs):
        return {"unanswerable_calls": []}, (), None

    monkeypatch.setattr(isolated, "_trusted_source_protocol_facts",
                        fail if phase == "source-facts" else facts)
    if phase == "task-binding":
        # The factory closes over the imported function; fail only its second,
        # task-bound request, after ordinary preparation succeeds.
        def binding_facts(*args):
            return fail() if len(args) == 5 else facts()
        monkeypatch.setattr(isolated, "_trusted_source_protocol_facts", binding_facts)
    monkeypatch.setattr(reference_isolated, "IsolatedModel", fail)

    def prepare():
        factory = reference_isolated.isolated_loader({"behavior.py": body}, {})
        if phase == "task-binding":
            factory.for_contract(SimpleNamespace(source_rules=("fixture",), clocks=()))
        elif phase == "worker-start":
            factory()

    if not candidate_fault or phase == "source-facts":
        with pytest.raises(FailureRaised) as caught:
            prepare()
        failure = caught.value.failure
        assert failure.origin is (FailureOrigin.REFERENCE if candidate_fault
                                  else FailureOrigin.INFRASTRUCTURE)
        assert not failure.determines_score
        assert failure.kind == (reason.kind if candidate_fault else "isolation-launch-failed")
        if not candidate_fault:
            assert failure.reason.detail["worker_detail"] == "namespace helper did not become ready"
    else:
        # A proved source fault is not relabelled as infrastructure.
        with pytest.raises(Refused) as caught:
            prepare()
        assert caught.value.refusal.kind == (reason.kind if phase == "task-binding"
                                             else "reference-model-would-not-start")


def test_a_digest_of_one_model_cannot_vouch_for_another():
    """The pairing `ReferenceModel(other_model, contract, artifact=real_digest)` admitted,
    ran, and reported agreement with a `B*` that never executed. There is now no way to
    obtain the two halves separately."""
    from behave.sim.core.reference import LoadedReference, ReferenceModel

    bundle = _ref()
    with pytest.raises(Refused, match="reference-bundle-not-from-a-loader"):
        LoadedReference(bundle.artifact, lambda: object())
    with pytest.raises(Refused, match="reference-model-not-from-a-bundle"):
        ReferenceModel(object(), contract())


def test_two_configurations_of_one_class_are_two_references():
    """`inspect.getsource(type(model))` binds the class body and not the constructor
    arguments, so freezing `gain=2` and running `gain=3` reported agreement."""
    two, three = _ref(gain=2), _ref(gain=3)
    assert two.digest != three.digest
    task = api.admit(contract(), realization(), reference=two)
    with pytest.raises(Refused, match="reference-not-the-frozen-one"):
        api.expected_for(task, _live(task.contract, gain=3),
                         run_device(task, schedule([3]), Doubling()))


def test_each_run_gets_its_own_model():
    """A model shared across a stimulus set carries what the previous run left in it.
    For an accumulating model that makes run N+1's expected output a function of run N,
    and no single run's record can show it."""
    class Accumulating:
        def __init__(self):
            self.total = 0

        def reset(self):
            self.total = 0

        def process(self, in_a):
            self.total = (self.total + in_a) & 0xFF
            return {"out_y": self.total}

    bundle = _ref(Accumulating)
    first, second = bundle.instantiate(contract()), bundle.instantiate(contract())
    assert first.model is not second.model
    first.model.process(in_a=9)
    assert second.model.total == 0, "the second run starts where the first one did"

    # And `evaluate` refuses a live model outright, so the sharing cannot be reintroduced
    # by a caller passing one.
    task = api.admit(contract(), realization(), reference=bundle)
    with pytest.raises(Refused, match="evaluate-reference-not-a-bundle"):
        _evaluate(task, [schedule([1])], Doubling(),
                     reference=bundle.instantiate(task.contract))


def test_an_artifact_state_observation_is_not_injected_into_the_reference():
    from behave.sim.core.reference import _begin_reference_run

    reference = _ref().instantiate(contract())
    stimulus = schedule([1])
    trace = api.AuditedTrace(
        stimulus, initial_state={"state": 7},
        records=[api.SlotRecord() for _slot in stimulus.slots])
    try:
        _begin_reference_run(reference, trace)
    finally:
        reference.close()


def test_an_unmapped_initial_state_policy_is_refused():
    from behave.sim.core.reference import _begin_reference_run

    reference = _ref().instantiate(contract())
    ordinary = schedule([1])
    stimulus = api.Stimulus(
        dict(ordinary.queues), ordinary.slots,
        initial_state={"policy": "recorded"})
    trace = api.AuditedTrace(
        stimulus, initial_state={"state": 7},
        records=[api.SlotRecord() for _slot in stimulus.slots])
    try:
        with pytest.raises(
                Refused, match="reference-initial-state-policy-unmapped"):
            _begin_reference_run(reference, trace)
    finally:
        reference.close()


def test_a_loader_that_hands_back_one_instance_is_refused():
    """`callable` says the factory can be called; it does not say each call produces a
    new model. `lambda: model` passes every other check and makes run N+1's expected
    output a function of run N for any model that accumulates."""
    from behave.sim.core import reference as ref_mod

    shared = type("Shared", (), {"reset": lambda self: None,
                                 "process": lambda self, in_a: None})()

    def one_instance(sources, config):
        del sources, config
        return lambda: shared

    ref_mod.register_loader("one-instance/1", one_instance)
    try:
        with pytest.raises(Refused, match="reference-loader-returns-one-instance"):
            api.load_reference("one-instance/1", {"behavior.py": b"x"})
    finally:
        ref_mod._LOADERS.pop("one-instance/1", None)


def test_a_factory_that_cycles_between_two_instances_is_caught():
    """`load_reference` calls the factory twice and refuses if it gets one object back,
    which catches `lambda: model` and nothing subtler: a factory cycling `a, b, a, b`
    passes that check and then hands run 3 the object run 1 is still holding."""
    from behave.sim.core import reference as ref_mod

    class Model:
        def reset(self):
            pass

        def process(self, in_a):
            del in_a

    pair = [Model(), Model()]

    def cycling(sources, config):
        del sources, config
        turn = iter(pair * 50)
        return lambda: next(turn)

    ref_mod.register_loader("cycling/1", cycling)
    try:
        bundle = api.load_reference("cycling/1", {"behavior.py": b"x"})
        first, second = bundle.new_model(), bundle.new_model()
        assert first is not second, "the load-time check let these two through"
        with pytest.raises(Refused, match="reference-loader-reused-a-live-model"):
            bundle.new_model()
    finally:
        ref_mod._LOADERS.pop("cycling/1", None)

    # And a factory that really does build one each time is never refused, however many
    # runs ask for one.
    honest = _ref()
    seen = [honest.new_model() for _ in range(8)]
    assert len({id(m) for m in seen}) == 8


def test_atomic_adapter_semantics_are_part_of_the_reference_identity():
    """Adding an atomic interpretation cannot leave an older bundle's digest valid."""
    from behave.sim.core import reference as ref_mod

    class Model:
        def reset(self):
            pass

        def process(self, in_a):
            return {"out_y": in_a}

    def loader(sources, config):
        del sources, config
        return Model

    def atomic(bundle, task_contract):
        del bundle, task_contract
        raise AssertionError("identity construction does not execute the adapter")

    name = "atomic-identity-test/1"
    ref_mod.register_loader(name, loader)
    try:
        before = api.load_reference(name, {"behavior.py": b"model"})
        ref_mod.register_atomic_loader(name, atomic, "atomic-test/1")
        after = api.load_reference(name, {"behavior.py": b"model"})
        assert before.digest != after.digest
        assert "atomic-test/1" in after.artifact.adapter_impl
    finally:
        ref_mod._ATOMIC_LOADERS.pop(name, None)
        ref_mod._LOADERS.pop(name, None)


def test_a_reference_subclass_cannot_substitute_the_model_that_runs():
    """`isinstance` admitted a subclass, and a subclass overriding `instantiate`
    returned a model of its own choosing while carrying the frozen artifact -- so
    swapping in a reference that agrees with the design under test made a wrong design
    compare clean.

    Two halves are needed and both are here: the exact type, so `new_model` is the
    loader's, and the construction inside the matcher, so no method the caller can
    replace stands between the bundle and the model that runs.
    """
    ref = _ref()
    task = api.admit(contract(), realization(), reference=ref)
    wrong = run_device(task, schedule([3]), Doubling(gain=3))

    class Impostor(type(ref)):
        def __init__(self, real):
            object.__setattr__(self, "_real", real)

        @property
        def artifact(self):
            return self._real.artifact

        def instantiate(self, contract, identity="reference"):
            raise AssertionError("the matcher must not call this")

        def new_model(self):
            raise AssertionError("nor this")

    with pytest.raises(Refused, match="comparison-reference-not-a-bundle"):
        api.compare_run(task, Impostor(ref), wrong)


def test_a_fresh_model_from_the_in_process_adapter_is_not_a_fresh_environment():
    """Reproduced, not feared. `LoadedReference.new_model()` promises an instance no run
    is already using, and the in-process adapter execs the source once per bundle: a
    module global outlives every instance, so run N+1's expected output is a function of
    run N. Three fresh models over an accumulating model answer 1, 2, 3.

    For the accumulating designs in this population that is the difference between two
    independent tests and one test counted twice.
    """
    from tests.support.reference import load

    bundle = load({"behavior.py": _accumulating()})
    answers = []
    for _ in range(3):
        model = bundle.new_model()
        model.reset()
        answers.append(model.process(a=1)["y"])
    assert answers == [1, 2, 3], "this is the defect, and it is still here"


def test_the_isolated_adapter_gives_each_run_its_own_process():
    """The same three runs through the adapter T3-09 asks for: 1, 1, 1. Each model is a
    child of its own, so module globals, class variables and `sys.modules` cannot carry
    from one run to the next."""
    from behave.sim import reference_isolated
    from behave.sim.core.reference import load_reference

    bundle = load_reference(reference_isolated.ADAPTER,
                            {"behavior.py": _accumulating()}, loader="test")
    answers = []
    for _ in range(3):
        model = bundle.new_model()
        model.reset()
        answers.append(model.process(a=1)["y"])
    assert answers == [1, 1, 1], answers

    # And the two are different references, because a digest names its adapter: the same
    # model driven two ways is not one reference with two implementations.
    from tests.support.reference import load

    other = load({"behavior.py": _accumulating()})
    assert bundle.digest != other.digest


def test_the_isolated_adapter_makes_the_recorded_helper_importable():
    """43 tasks were refused `authority-no-reference` because their model imports the
    `fp_lib` helper: the bytes were recorded in the digest and nothing made them
    importable, so the `exec` raised `ModuleNotFoundError` and the task was dropped.

    The helper is the one the digest is over, not whichever copy the parent has: the
    recorded sources are materialised and that directory goes on the child's path first.
    Which modules a model may import at all is still admission's question -- this widens
    nothing.
    """
    from behave.sim import reference_isolated
    from behave.sim.behavior import LIBRARY_DIR
    from behave.sim.core.reference import load_reference

    sources = {
        "behavior.py": b"from fp_lib import fp32_abs\n\n"
                       b"class M:\n"
                       b"    def reset(self):\n        pass\n\n"
                       b"    def process(self, a):\n"
                       b"        return {'y': fp32_abs(a)}\n",
        "fp_lib.py": (pathlib.Path(LIBRARY_DIR) / "fp_lib.py").read_bytes(),
    }
    # **The in-process adapter is not asserted about here, and that is the finding.**
    # It was: `load_reference("transaction/1", ...)` raising `ModuleNotFoundError` was
    # the "before" half of this test. It stopped raising under `-n 100`, because
    # `instrumented._allow_approved_imports` puts the verifier's library on *this*
    # process's `sys.path` the first time anything loads a model in it -- so whether the
    # import resolves depends on what ran earlier in the same worker. A test that asserts
    # either answer is asserting the order of the suite.
    bundle = load_reference(reference_isolated.ADAPTER, sources, loader="test")
    model = bundle.new_model()
    model.reset()
    assert model.process(a=0xBF800000) == {"y": 0x3F800000}   # -1.0 -> 1.0


def test_the_isolated_proxy_reports_the_calls_the_model_defines():
    """The proxy serves the model's attributes, and a method is an attribute. Without the
    child saying which calls exist, `getattr(model, "tick", None)` came back `None` for a
    model that defines `tick` -- and the reference adapter advances an inputless slot
    only through a tick, so a tick-driven reference would have been advanced by nothing
    at all while every check downstream looked consistent."""
    from behave.sim import reference_isolated
    from behave.sim.core.reference import load_reference

    with_tick = b'''
class M:
    def __init__(self):
        self.n = 0

    def reset(self):
        self.n = 0

    def process(self, a):
        return {"y": a}

    def tick(self):
        self.n += 1
        return {"y": self.n}
'''
    bundle = load_reference(reference_isolated.ADAPTER,
                            {"behavior.py": with_tick}, loader="test")
    model = bundle.new_model()
    model.reset()
    assert callable(getattr(model, "tick", None)), "a tick the model defines"
    assert model.tick() == {"y": 1}
    assert model.tick() == {"y": 2}
    # And one it does not define is absent, rather than a call that raises in the child.
    assert getattr(model, "start", None) is None

    without = load_reference(reference_isolated.ADAPTER,
                             {"behavior.py": _accumulating()}, loader="test")
    assert getattr(without.new_model(), "tick", None) is None


def test_the_isolated_proxy_carries_one_atomic_multi_endpoint_slot_call():
    """The production reference boundary preserves a slot as one JSON call.

    Multi-endpoint comparison must not implement simultaneity by calling a legacy
    one-transaction ``process`` method once per endpoint.  This exercises the real
    isolated loader and child protocol, rather than the in-process tests-only loader:
    the complete accepted-input map crosses once and the output remains keyed by its
    logical endpoint.
    """
    from behave.sim import reference_isolated
    from behave.sim.core.reference import load_reference

    source = b'''
class M:
    def reset(self):
        pass

    def process_slot(self, slot):
        left = slot["inputs"].get("left")
        right = slot["inputs"].get("right")
        if left is None or right is None:
            return None
        return {
            "sum": {"sum_data": left["left_data"] + right["right_data"]},
            "parts": [
                {"part_data": left["left_data"]},
                {"part_data": right["right_data"]},
            ],
        }
'''
    bundle = load_reference(
        reference_isolated.ADAPTER, {"behavior.py": source}, loader="test")
    model = bundle.new_model()
    try:
        model.reset()
        got = model.process_slot({
            "index": 4,
            "ticks": ["clk"],
            "controls": {"sum_ready": 1, "parts_ready": 1},
            "inputs": {
                "left": {"left_data": 3},
                "right": {"right_data": 5},
            },
        })
        assert got == {
            "sum": {"sum_data": 8},
            "parts": [{"part_data": 3}, {"part_data": 5}],
        }
    finally:
        model.close()


def test_a_model_that_tries_to_end_the_verifier_ends_its_own_process():
    """The reference is written by an agent, and the private token that stops a caller
    pairing a digest with a model of its own choosing says nothing about what the model
    does when it runs. In process, `os._exit` takes the verifier with it -- which is why
    this test can only be written against the isolated adapter."""
    from behave.sim import reference_isolated
    from behave.sim.behavior.isolated import Broken, Timeout
    from behave.sim.core.reference import load_reference

    hostile = b'''
import os

class M:
    def reset(self):
        pass

    def process(self, a):
        os._exit(7)
'''
    # It does not get as far as `process`, and not as far as a bundle: the loader builds
    # two models to check they are distinct, the import is refused where the model loads,
    # and the child dies there. What reaches this process is a **named refusal** rather
    # than the transport's exception -- `Broken: the child exited with 1` describes the
    # channel, not the reference.
    with pytest.raises(Refused, match="reference-model-would-not-start"):
        load_reference(reference_isolated.ADAPTER, {"behavior.py": hostile},
                       loader="test")

    # An allocation no rlimit lets through, from a model that imports nothing: the model
    # fails, and the failure is the model's. In this process it would be the verifier's.
    greedy = b'''
class M:
    def reset(self):
        pass

    def process(self, a):
        return {"y": len([0] * (1 << 40))}
'''
    model = load_reference(reference_isolated.ADAPTER, {"behavior.py": greedy},
                           loader="test").new_model()
    model.reset()
    with pytest.raises((MemoryError, OverflowError, Broken, Timeout)):
        model.process(a=1)


def test_the_recorded_helper_is_the_one_that_runs():
    """The bundle's digest is over the bytes of `fp_lib.py` it was handed, and the
    central library ran instead: `instrumented._allow_approved_imports` inserts the
    verifier's own library at `sys.path[0]`, ahead of the directory this materialised.
    So a reference could be identified by one helper and computed by another.

    **Two different helpers, not two copies of one.** The test this replaces used a copy
    of the real `fp_lib.py`, so both answers agreed and it could not see the substitution.
    Here the recorded helper answers 123 and the central one does not.
    """
    from behave.sim import reference_isolated
    from behave.sim.core.reference import load_reference

    recorded = b"def fp32_abs(x):\n    return 123\n"
    model = (b"from fp_lib import fp32_abs\n\n"
             b"class M:\n"
             b"    def reset(self):\n        pass\n\n"
             b"    def process(self, a):\n"
             b"        return {'y': fp32_abs(a)}\n")

    bundle = load_reference(reference_isolated.ADAPTER,
                            {"behavior.py": model, "fp_lib.py": recorded},
                            loader="test")
    got = bundle.new_model()
    got.reset()
    assert got.process(a=0xBF800000) == {"y": 123}, \
        "the central library ran, and the digest is over bytes that did not execute"

    # And the digest distinguishes the two bundles, so the record says which ran.
    other = load_reference(reference_isolated.ADAPTER,
                           {"behavior.py": model,
                            "fp_lib.py": b"def fp32_abs(x):\n    return 7\n"},
                           loader="test")
    assert other.digest != bundle.digest
    got = other.new_model()
    got.reset()
    assert got.process(a=1) == {"y": 7}


def test_what_the_model_is_can_be_read_without_importing_it_here(model_problem):
    """`cosim` imported the agent's module into the verifier's process to learn three
    things: the class's name, the parameters of `process`, and the per-domain handlers of
    a multi-clock model. The import executes whatever sits at the module's top level, and
    it happened before admission had decided the task could be verified at all."""
    from behave.sim.behavior.isolated import facts_of

    got = facts_of((model_problem / "behavior.py").read_text())
    assert got["cls_name"] == "BitRegister"
    assert got["process_inputs"] == ["in_d"]
    assert got["domains"] == {}

    # A bare process owns entry selection; process_* names remain helpers.
    multi = b'''
class TwoDomains:
    def reset(self):
        pass

    def process(self, a):
        return {"y": a}

    def process_fast(self, x, y):
        return {"y": x + y}
'''
    got = facts_of(multi.decode())
    assert got["domains"] == {}
    assert got["process_inputs"] == ["a"]

    # Without the bare entry, domain handlers provide the multi-clock metadata.
    domains = b'''
class TwoDomains:
    def reset(self):
        pass

    def process_slow(self, a):
        return {"y": a}

    def process_fast(self, x, y):
        return {"y": x + y}
'''
    got = facts_of(domains.decode())
    assert got["process_inputs"] == []
    assert got["domains"] == {
        "fast": {"method": "process_fast", "inputs": ["x", "y"]},
        "slow": {"method": "process_slow", "inputs": ["a"]},
    }


def test_a_reference_that_calls_what_this_build_lacks_is_refused_by_name():
    """A model runs in the child with the admitted builtins and nothing else, so a call
    to anything outside them raises on whichever slot reaches it. That came back as
    `reference-failed: NameError` once per stimulus and a `partial` over four runs -- a
    task refused four times for a reason a reader cannot act on, when the source says it
    before anything runs.

    A model calling `locals` is in that position. Under the in-process adapter it
    would run with the **real** builtins and be scored, which is the thing this
    replaces rather than a regression from it.
    """
    from behave.sim import reference_isolated
    from behave.sim.behavior.admission import calls_this_build_will_not_answer
    from behave.sim.core.reference import load_reference

    model = (b"class M:\n"
             b"    def reset(self):\n        pass\n\n"
             b"    def process(self, a):\n"
             b"        return {'y': len(locals())}\n")
    said = calls_this_build_will_not_answer(model.decode())
    assert said and "locals()" in said[0]

    with pytest.raises(Refused,
                       match="reference-model-calls-what-this-build-does-not-provide"):
        load_reference(reference_isolated.ADAPTER, {"behavior.py": model},
                       loader="test")

    # A model that calls only what it defines and what the build provides is untouched:
    # this check is about names that will not exist, not about a subset of the language.
    fine = (b"def twice(x):\n    return 2 * x\n\n"
            b"class M:\n"
            b"    def reset(self):\n        pass\n\n"
            b"    def process(self, a):\n"
            b"        return {'y': twice(len(range(a)))}\n")
    assert calls_this_build_will_not_answer(fine.decode()) == []
    got = load_reference(reference_isolated.ADAPTER, {"behavior.py": fine},
                         loader="test").new_model()
    got.reset()
    assert got.process(a=3) == {"y": 6}


def test_a_reference_that_will_not_load_says_which_thing_was_wrong():
    body = (b"class M:\n    def reset(self): pass\n"
            b"    def process(self, a): return {'y': len(locals())}\n")
    with pytest.raises(Refused) as caught:
        api.load_reference(api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": body})
    assert caught.value.refusal.kind == "reference-model-calls-what-this-build-does-not-provide"
    assert dict(caught.value.refusal.detail)["calls"]


@pytest.mark.parametrize("origin", [api.FailureOrigin.REFERENCE, api.FailureOrigin.INFRASTRUCTURE])
def test_public_reference_entry_retains_typed_preparation_failure(monkeypatch, origin):
    failure = api.RunFailure(origin, "reference-preparation",
                             Refusal("source-judge-unavailable", "fixture failure"))

    def fail(*args, **kwargs):
        raise api.FailureRaised(failure)

    monkeypatch.setattr(api, "_load_reference", fail)
    with pytest.raises(api.FailureRaised) as caught:
        api.load_reference(api.ISOLATED_REFERENCE_ADAPTER, {"behavior.py": b"class M: pass\n"})
    assert caught.value.failure is failure


def test_a_materialised_source_that_changed_is_not_the_reference_any_more():
    """The bundle writes the recorded bytes into a directory once, and every child that
    builds a model imports them from there. Nothing in a child can create a file, but the
    audit hook does not see `unlink`, `rename` or `symlink` -- so "a later child replaces
    what an earlier one imported" is a real shape, and a container is what would close it
    rather than an argument. The bytes are checked against the digest before each child,
    which turns the argument into a check."""
    from pathlib import Path

    from behave.sim import reference_isolated
    from behave.sim.core.reference import load_reference

    # Content of this test's own, so the content-addressed cache gives it a directory
    # nothing else shares: two tests over identical bytes get one directory, and this one
    # mutates what it is handed.
    recorded = b"def fp32_abs(x):\n    return 4242\n"
    model = (b"from fp_lib import fp32_abs\n\nclass M:\n"
             b"    def reset(self):\n        pass\n\n"
             b"    def process(self, a):\n        return {'y': fp32_abs(a)}\n")

    bundle = load_reference(reference_isolated.ADAPTER,
                            {"behavior.py": model, "fp_lib.py": recorded},
                            loader="test")
    first = bundle.new_model()
    first.reset()
    assert first.process(a=1) == {"y": 4242}

    where = None
    for candidate in reference_isolated._MATERIALISED.values():
        # Other tests in this worker have their own entries here, most without an
        # `fp_lib.py` at all and some already swept; scanning them raised on whichever
        # this worker happened to have run first, which made this test fail by test
        # order rather than by anything it checks.
        try:
            if (Path(candidate) / "fp_lib.py").read_bytes() == recorded:
                where = Path(candidate)
        except OSError:
            continue
    assert where is not None
    (where / "fp_lib.py").write_text("def fp32_abs(x):\n    return 999\n")

    with pytest.raises(Refused, match="reference-sources-changed-under-us"):
        bundle.new_model()


def test_named_job_packages_include_their_own_native_dependencies(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from behave.sim import job_isolation

    package = tmp_path / "package"
    package.mkdir()
    library = package / "helper.so"
    extension = tmp_path / "extension.cpython-311.so"
    for path in (library, extension):
        path.write_bytes(b"trusted fixture, not executed")
    (package / "module.py").write_text("# pure Python does not need ldd\n")
    package_mounts = {
        "package": SimpleNamespace(source=str(package), target="/runtime/packages/package"),
        "extension": SimpleNamespace(source=str(extension), target="/runtime/packages/extension.so"),
    }
    monkeypatch.setitem(job_isolation._JOB_POLICIES, "fixture_job",
                        job_isolation._JobPolicy(packages=tuple(package_mounts)))
    monkeypatch.setattr(job_isolation, "_package_mount", package_mounts.__getitem__)
    seen = []
    dependency = SimpleNamespace(source="/lib/dependency.so", target="/lib/dependency.so")

    def native(paths):
        seen.extend(paths)
        return (dependency, dependency)

    monkeypatch.setattr(job_isolation, "native_runtime", native)
    mounts = job_isolation._dependency_mounts("fixture_job", {})
    assert set(seen) == {str(library), str(extension)}
    assert len(mounts) == 3
    assert mounts[-1] is dependency


def test_a_worker_does_not_inherit_what_this_process_already_ran():
    """The boundary was a fork, which is a copy of this process and not a fresh one.

    So every module `run_cosim` had imported was in the child -- including a candidate's
    own, wherever something had loaded it earlier in the run -- and a "child" running the
    model was running it in a namespace that model may already have written to. The
    channel back was a `multiprocessing.Queue`, which unpickles the child's object graph
    here: a value produced by submitted code, constructed in this process before any of
    this build's checks see it.

    Both halves, at the seam. This process plants a module and a module global; the
    worker is asked what it can see.
    """
    import sys
    import types

    from behave.sim.worker import JOBS, WorkerFailed, run_job, run_job_strict

    planted = types.ModuleType("behave_worker_probe_module")
    planted.value = "planted in the parent"
    sys.modules["behave_worker_probe_module"] = planted
    try:
        # ── the fork half: a worker is a fresh interpreter, so it has neither ──
        seen = run_job("echo", {"answer": "hello"}, timeout_s=120)
        assert seen == "hello", "the channel did not carry an answer at all"

        # `echo` cannot report on the child's `sys.modules`, so the check is on the
        # production mechanism instead: the named job goes through the common OS
        # boundary and that boundary starts the fresh interpreter.
        import inspect

        from behave.sim import worker as worker_module

        source = inspect.getsource(worker_module.run_job_strict)
        assert "run_isolated_job" in source
        assert "Popen" not in source and "subprocess" not in source

        # ── the pickle half: what crosses is JSON, in both directions ──
        with pytest.raises(WorkerFailed) as exc:
            run_job_strict("echo", {"answer": {1, 2, 3}}, timeout_s=60)
        assert exc.value.how == "refused" and "not JSON" in exc.value.detail
        # A child exception crosses as a failed JSON job, not a pickled exception.
        assert run_job("echo", {"raise": "fixture worker failure"}, timeout_s=60) is None

        # ── and every job is a name in the registry: there is no callable to send ──
        assert all(callable(fn) for fn in JOBS.values())
        assert run_job("unregistered-test-job", {}, timeout_s=30) is None
    finally:
        sys.modules.pop("behave_worker_probe_module", None)
