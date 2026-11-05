"""An exception's owner comes from the execution boundary, not its Python type."""

import json
from pathlib import Path

import pytest

from behave.sim import api, evaluation
from behave.sim.core.atomic import AtomicDriver
from behave.sim.core.driver import Driver
from behave.sim.core.failure import CandidateFault, FailureRaised

from .test_api_path import Doubling, _evaluate, _ref, contract, realization, schedule
from .test_command_handler import _request
from .test_multiclock_authority import _AtomicDevice
from .test_multiclock_authority import _evaluate as evaluate_atomic


def _broken(*_args, **_kwargs):
    raise RuntimeError("injected replay defect")


def test_source_worker_launch_refusal_is_infrastructure():
    from behave.isolation import IsolationRefusal, IsolationUnavailable
    from behave.sim.behavior import isolated

    from .test_behavior_public_exact_construction import STRAIGHT_LINE_SOURCE

    reason = IsolationRefusal("isolation-launch-failed", "namespace setup failed",
                              {"stderr": "unshare: resource temporarily unavailable"})

    def unavailable(_program):
        raise IsolationUnavailable(reason)

    with isolated._testing_worker_launcher(unavailable), pytest.raises(FailureRaised) as caught:
        isolated.IsolatedModel(STRAIGHT_LINE_SOURCE, "candidate.py")
    failure = caught.value.failure
    assert failure.origin is api.FailureOrigin.INFRASTRUCTURE
    assert failure.reason.kind == reason.code
    assert dict(failure.reason.detail) == dict(reason.detail)
    assert not failure.determines_score


@pytest.mark.isolation
def test_missing_reset_is_a_source_exception_not_a_broken_service():
    from behave.sim.behavior import isolated

    source = 'class M:\n    def process(self, a): return {"y": a}\n'
    with isolated.IsolatedModel(source) as model, pytest.raises(AttributeError) as caught:
        model.reset()
    assert isolated.is_model_exception(caught.value)


def test_native_atomic_failure_attribution_has_a_new_adapter_identity():
    from behave.sim.behavior.artifact import BEHAVIOR_ATOMIC_ADAPTER

    assert BEHAVIOR_ATOMIC_ADAPTER == "behavior-atomic/9"


def _no_result(result, origin):
    assert not result.complete
    assert not result.passed
    assert not result.candidate_failures
    assert result.no_result_failures
    assert all(failure.origin is origin for _, failure in result.no_result_failures)


@pytest.mark.parametrize("target", ["service", "controls", "observe"])
def test_trusted_replay_defects_do_not_score_the_candidate(monkeypatch, target):
    if target == "service":
        monkeypatch.setattr(evaluation, "run", _broken)
    else:
        monkeypatch.setattr(Driver, target, _broken)
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)

    result = _evaluate(task, [schedule([3])], Doubling(), reference=reference)

    _no_result(result, api.FailureOrigin.VERIFIER)


def test_driver_invariant_refusal_is_not_an_artifact_fault(monkeypatch):
    def refused(*_args):
        raise api.Refused(api.Refusal("driver-invariant", "injected driver invariant"))

    monkeypatch.setattr(Driver, "controls", refused)
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    _no_result(
        _evaluate(task, [schedule([3])], Doubling(), reference=reference),
        api.FailureOrigin.VERIFIER,
    )


@pytest.mark.parametrize("method", ["drive", "observe"])
def test_atomic_driver_defects_are_no_result(monkeypatch, method):
    monkeypatch.setattr(AtomicDriver, method, _broken)
    result = evaluate_atomic(_AtomicDevice())
    _no_result(result, api.FailureOrigin.VERIFIER)


def test_atomic_candidate_exception_still_scores_zero():
    class Crashing(_AtomicDevice):
        atomic_step = staticmethod(_broken)

    result = evaluate_atomic(Crashing())
    assert result.complete and not result.passed
    assert result.candidate_failures
    assert not result.no_result_failures


@pytest.mark.parametrize("batch_method", ["traces_per_process", "traces"])
@pytest.mark.parametrize("typed", [False, True])
def test_batch_failures_cannot_be_erased_by_a_single_run_retry(batch_method, typed):
    class Batched(Doubling):
        def settle(self, *_args):
            pytest.fail("a failed replay service must not silently retry")

    failure = api.RunFailure.infrastructure(
        "replay-service", api.Refusal("service-down", "injected service failure"))

    def failed_batch(*_args):
        if typed:
            raise FailureRaised(failure)
        raise RuntimeError("injected batch coordination defect")

    setattr(Batched, batch_method, staticmethod(failed_batch))
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    result = _evaluate(
        task, [schedule([3]), schedule([4])], Batched(), reference=reference)

    _no_result(result, api.FailureOrigin.INFRASTRUCTURE if typed
               else api.FailureOrigin.VERIFIER)
    assert len(result.no_result_failures) == 2
    if typed:
        assert all(got is failure for _, got in result.no_result_failures)


def test_command_persists_a_verifier_failure_without_a_score(tmp_path, monkeypatch):
    request = _request(tmp_path, run_analysis=False)
    monkeypatch.setattr(evaluation, "run", _broken)
    result = api.handle_command(request)

    _no_result(result.evaluation, api.FailureOrigin.VERIFIER)
    assert result.authority_kind == "partial"
    record = json.loads(Path(result.record_path).read_text())
    assert record["authority"]["kind"] == "partial"
    assert "passed" not in record["authority"]
    assert "passed" not in record["report"]


@pytest.mark.parametrize("origin", ["reference", "verifier", "infrastructure"])
@pytest.mark.parametrize("stop", [False, True])
def test_no_result_stops_new_replay_without_hiding_its_cause(monkeypatch, origin, stop):
    reference = _ref()
    task = api.admit(contract(), realization(), reference=reference)
    catalogue = api.evaluation_manifest(task, api.CatalogueManifest(()))
    execution = evaluation.ReplayExecution(task, Doubling(), reference, catalogue)
    seeds = tuple(schedule([value]) for value in (3, 4, 5))
    failure = getattr(api.RunFailure, origin)(
        "replay-service", api.Refusal("original-service-failure", "the first failure"))
    calls = []

    def unavailable(_task, seed, _artifact, **_kwargs):
        calls.append(seed)
        return failure

    monkeypatch.setattr(evaluation, "run", unavailable)
    first = execution.evaluate(seeds[:2], stop_on_failure=stop)
    final = execution.evaluate(seeds, stop_on_failure=stop)
    expected = seeds[:1] if stop else seeds
    assert tuple(calls) == expected
    assert tuple(row.stimulus for row in final.results) == expected
    assert final.requested_stimuli == seeds
    assert final.unexecuted == (seeds[1:] if stop else ())
    assert not first.passed and not final.passed
    assert all(cause is failure for _, cause in final.no_result_failures)


@pytest.mark.isolation
@pytest.mark.parametrize("candidate_fault", [False, True])
def test_real_behavior_boundary_distinguishes_source_and_projector_errors(
        tmp_path, monkeypatch, candidate_fault):
    from behave.sim.behavior import artifact as behavior_artifact

    from .test_behavior_public_exact_construction import (
        STRAIGHT_LINE_SOURCE,
        _contract,
        _generation,
        _reference,
    )
    from .test_command_handler import _BaselineOnly

    source = STRAIGHT_LINE_SOURCE
    if candidate_fault:
        source = source.replace('return {"y": a}', 'raise RuntimeError("source fault")')
    path = tmp_path / "candidate.py"
    path.write_text(source)
    task_contract = _contract()
    reference = _reference(STRAIGHT_LINE_SOURCE)
    task = api.admit(
        task_contract,
        api.realization_of_behavior_source(source, task_contract, file="candidate.py"),
        reference=reference,
    )
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file="candidate.py", timeout_s=90),
        _generation(task_contract), bounded_analysis=False)
    request = handle.command_request(
        run_directory=tmp_path / "command",
        baseline=api.BaselinePolicy(case_limit=2, max_queue_depth=1),
        providers=(_BaselineOnly(),), probe_budget=0,
        run_analysis=False, reference=reference,
    )
    if not candidate_fault:
        monkeypatch.setattr(behavior_artifact, "_project_outputs", _broken)
    result = api.handle_command(request)

    if candidate_fault:
        # A retained candidate failure settles zero without running the remaining
        # baseline/workload; complete-pool replay is only required for a pass.
        assert not result.evaluation.passed
        assert result.evaluation.failure_witness is not None
        assert result.evaluation.candidate_failures
        assert not result.evaluation.no_result_failures
    else:
        _no_result(result.evaluation, api.FailureOrigin.VERIFIER)


def test_worker_exception_origin_keeps_the_exact_native_exception_class():
    from behave.sim.behavior.isolated import _raised, is_model_exception

    reported = _raised("ZeroDivisionError")
    local = ZeroDivisionError("verifier defect")
    local._isolated_model_origin = True
    assert type(reported) is ZeroDivisionError
    assert is_model_exception(reported)
    assert not is_model_exception(local)


@pytest.mark.isolation
def test_worker_reset_preparation_defect_is_not_a_native_exception(monkeypatch):
    from behave.sim.behavior import isolated

    from .test_behavior_public_exact_construction import STRAIGHT_LINE_SOURCE

    # Reset now preserves the bound memory instead of rebinding its initial image.
    original = "from behave.sim.behavior.reference_memory import preserve_external_memory"
    assert isolated._CHILD.count(original) == 1
    monkeypatch.setattr(isolated, "_CHILD", isolated._CHILD.replace(
        original, original + '\n                raise RuntimeError("injected environment preparation defect")'))
    with isolated.IsolatedModel(STRAIGHT_LINE_SOURCE, "candidate.py") as model, \
            pytest.raises(isolated.Broken):
        model.reset()


@pytest.mark.parametrize("source_fault", [False, True])
def test_native_atomic_branch_distinguishes_callback_and_bookkeeping(
        monkeypatch, source_fault):
    from behave.sim.behavior.multiclock import (
        DomainInvocation,
        DomainOwnership,
        NativeAtomicMachine,
    )
    from behave.sim.core.clock_schedule import GlobalEventSlot

    class M:
        def reset(self):
            self.a = self.b = 0

        def process_a(self):
            if source_fault:
                _broken()
            self.a += 1

        def process_b(self):
            self.b += 1

    machine = NativeAtomicMachine(
        M, DomainOwnership({"a": ("a",), "b": ("b",)}), ("a", "b"))
    if not source_fault:
        monkeypatch.setattr(machine, "_branch", _broken)
    expected = CandidateFault if source_fault else RuntimeError
    with pytest.raises(expected) as caught:
        machine.step(GlobalEventSlot(0, ("a",)), (DomainInvocation("a"),))
    assert type(caught.value) is expected


@pytest.mark.isolation
@pytest.mark.parametrize("service_operation", ["initialisation", "readiness", "transition"])
def test_atomic_worker_service_defects_never_score_zero(
        tmp_path, monkeypatch, service_operation):
    from behave.sim.behavior import isolated
    from tests.support.atomic_behavior import SOURCE, _contract, _transaction_stimulus

    path = tmp_path / "candidate.py"
    path.write_text(SOURCE)
    contract = _contract()
    task = api.admit(contract, api.realization_of_behavior_source(
        SOURCE, contract, file="candidate.py"))
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file="candidate.py", timeout_s=90),
        api.GenerationSpec(horizon=3, queue_bound=2, reset_hold_cycles=0, drain=0),
        bounded_analysis=False)
    # Inject inside the real worker's service operation, outside source callbacks.
    statement = {
        "initialisation": '                    owners = {field: set() for field in selected_model.state_contract()}',
        "readiness": 'ready, ready_hits = _atomic.ready_observation(call["domain"])',
        "transition": 'return _transition_record(\n                        _atomic.step(slot, invocations))',
    }[service_operation]
    assert isolated._CHILD.count(statement) == 1
    indent = " " * 20 if service_operation == "initialisation" else ""
    monkeypatch.setattr(isolated, "_CHILD", isolated._CHILD.replace(
        statement, indent + 'raise RuntimeError("injected atomic service defect")'))

    with pytest.raises(FailureRaised) as caught:
        handle._artifact.trace(task.contract, _transaction_stimulus())
    assert caught.value.failure.origin is api.FailureOrigin.INFRASTRUCTURE
    assert not caught.value.failure.determines_score


@pytest.mark.isolation
@pytest.mark.parametrize("operation", ["handler", "ready", "reset"])
def test_atomic_worker_preserves_real_callback_faults(tmp_path, operation):
    from tests.support.atomic_behavior import (
        ATOMIC_PARTIAL_FAILURE_SOURCE,
        _contract,
        _transaction_stimulus,
    )

    source = ATOMIC_PARTIAL_FAILURE_SOURCE.replace("1 // a", "a")
    if operation == "handler":
        source = source.replace('return {"oa": a}', 'raise RuntimeError("source fault")')
    elif operation == "ready":
        source += '\n    def a_ready(self):\n        raise RuntimeError("source fault")\n'
    else:
        # Initial construction succeeds; this reset callback runs after traffic.
        source += '\n    def reset_b(self):\n        raise RuntimeError("source fault")\n'
    path = tmp_path / "candidate.py"
    path.write_text(source)
    contract = _contract()
    task = api.admit(contract, api.realization_of_behavior_source(
        source, contract, file="candidate.py"))
    handle = api.prepare_behavior(
        task, api.BehaviorConfig(path, file="candidate.py", timeout_s=90),
        api.GenerationSpec(horizon=3, queue_bound=2, reset_hold_cycles=0, drain=0),
        bounded_analysis=False)

    stimulus = _transaction_stimulus()
    if operation == "reset":
        from behave.sim.core.clock_schedule import GlobalEventSlot, GlobalSchedule

        schedule = stimulus.clock_schedule
        last = schedule.slots[-1]
        reset = GlobalEventSlot(last.time, event=api.EventSlot(
            controls={**dict(last.event.controls), "b_rst": 1},
            ticks=last.events, resets=("b",)))
        schedule = GlobalSchedule(schedule.timings, (*schedule.slots[:-1], reset))
        stimulus = api.Stimulus(
            queues=dict(stimulus.queues), slots=[slot.event for slot in schedule.slots],
            clock_schedule=schedule)

    with pytest.raises(FailureRaised) as caught:
        handle._artifact.trace(task.contract, stimulus)
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.determines_score
    assert caught.value.failure.reason.kind == f"multiclock-{operation}-failed"
