"""Wave 1's acceptance pass: one test per way a verdict could be built out of nothing.

These are not unit tests of the modules they touch. Each one reproduces a *shape* --
a record spliced together from two runs, an obligation reported met because nothing
exercised it, a score carried by a result that has none -- and asserts that the shape is
now refused rather than scored. Several of them passed before the wave and produced the
wrong number; the docstrings say which.

Population qualification uses the public command through
`tools/qualify_dataset_baseline.py`; these regressions are not a population run.
"""

import pathlib

import pytest

from behave.sim import api
from tests.support.device_boundary import run_device

#: The reset hold the population's manifests were generated with, and therefore the one
#: their `policy_digest` covers. A test that rewrites a run policy has to recompute the
#: digest with it, or the file it writes is one the runtime refuses as edited -- which is
#: the check working, and not what those tests are about.
HOLD = 3
from behave.sim.core.refusal import Refused

from .test_api_path import (
    Doubling,
    _evaluate,
    _ref,
    contract,
    realization,
    schedule,
)

# ── 1. the reference: identity, mutation, instance ──







def test_an_admitted_task_cannot_be_edited_after_admission():
    """`realization.ports["in_valid"].driven_by = "environment"` after `admit` returned
    turned a refused candidate into an admitted one, on the record whose whole job is to
    say what was checked."""
    task = api.admit(contract(), realization(), reference=_ref())
    with pytest.raises(AttributeError):
        task.reference = "something else"
    with pytest.raises(AttributeError):
        task.realization.ports["in_valid"].driven_by = "artifact"
    with pytest.raises(TypeError):
        task.realization.ports["in_b"] = api.Port("in_b", 8)
    with pytest.raises(TypeError):
        task.checks["adapter_supported"] = False


def test_an_output_only_task_needs_a_declared_tick():
    """With no input endpoint nothing advances the reference, `expected_for` returns an
    empty stream, and an idle design matches it: a pass for a reference never run."""
    counter = api.TaskContract(
        endpoints=[api.Endpoint(
            "out", api.Direction.OUT, [api.Field("y", api.FiniteType(8), "out_y")],
            api.ready_valid("out", "out_valid", "out_ready"), clock="clk",
            kind=api.Kind.TRANSACTION)],
        clocks=[api.ClockDomain("clk", "clk", "rst")])

    class NoTick:
        def reset(self):
            pass

        def process(self, in_a):
            del in_a

    with pytest.raises(Refused, match="reference-output-only-without-tick"):
        _ref(NoTick).instantiate(counter)


# ── 2. the record: what a comparison is of ──

def test_a_result_cannot_be_assembled_out_of_two_runs():
    """`RunResult(stimulus_A, trace=trace_of_B, comparison=Comparison())` was a clean
    pass built from another run's trace and a comparison of nothing."""
    from behave.sim.core.compare import Comparison
    from behave.sim.core.run_record import RunResult

    ref = _ref()
    task = api.admit(contract(), realization(), reference=ref)
    one, two = schedule([3]), schedule([5])
    here, there = run_device(task, one, Doubling()), run_device(task, two, Doubling())

    # A trace of another stimulus is refused before any comparison is looked at: the
    # pairing is wrong whatever the verdict attached to it says about itself.
    with pytest.raises(Refused, match="run-result-trace-of-another-stimulus"):
        RunResult.compared(one, there, api.compare_run(task, ref, here))
    with pytest.raises(Refused, match="run-result-comparison-of-another-run"):
        RunResult.compared(one, here, api.compare_run(task, ref, there))
    # An unbound comparison establishes nothing about any run.
    with pytest.raises(Refused, match="run-result-comparison-of-another-run"):
        RunResult.compared(one, here, Comparison())
    with pytest.raises(Refused, match="run-result-not-from-a-constructor"):
        RunResult(one, here, Comparison(), "", None, None, True)

    # And the three legitimate states each say what they are.
    assert RunResult.failed_to_run(
        one,
        api.RunFailure.infrastructure(
            "test-replay", api.Refusal("no-simulator", "no simulator"))).refused
    assert not RunResult.not_compared(
        one, here,
        api.RunFailure.reference(
            "test-reference", api.Refusal("no-reference", "no reference"))
    ).was_compared
    assert RunResult.compared(one, here, api.compare_run(task, ref, here)).ok


# ── 3. the protocol obligations ──



































# ── 4. the supported range and the production path ──

def test_the_supported_range_refuses_by_name_and_lists_every_reason():
    """An operator fixing one shape at a time and re-running learns the next only after
    another sweep, so the check reports all of them."""
    from behave.sim.core.supported import refusals_for

    undeclared = api.TaskContract(
        endpoints=[api.Endpoint("in", api.Direction.IN,
                                contract().endpoint("in").fields,
                                contract().endpoint("in").adapter, clock="clk"),
                   api.Endpoint("out", api.Direction.OUT,
                                contract().endpoint("out").fields,
                                contract().endpoint("out").adapter, clock="clk",
                                matching=api.Matching.CONTENT)],
        clocks=list(contract().clocks))
    kinds = {r.kind for r in refusals_for(undeclared)}
    assert kinds == {"unsupported-kind-not-transaction",
                     "unsupported-no-reference", "unsupported-implicit-horizon",
                     "unsupported-implicit-back-pressure"}
    assert not refusals_for(contract(), _ref(), (), horizon=8, stall_every=0)




# ── 5. identifier correlation: reorder, duplicate, reuse ──

U8 = api.FiniteType(8)


















# ── 6. the second review's five, each reproduced before it was closed ──

def test_a_clean_comparison_cannot_be_rebound_onto_a_wrong_design():
    """The construction the review reported, verbatim, on the previous commit:

        clean = compare_run(task, ref, right_trace)      # ok
        RunResult.compared(stim, wrong_trace, clean.about(stim, wrong_trace, ref))
        ConcreteEvaluation(...).passed  ->  True

    A comparison stated its three digests and `RunResult` checked the statement against
    itself, so a verdict could name a trace whose outputs nothing had read. There is now
    no way to state them: `comparison_for` reads the observed stream out of the trace it
    binds to, and the constructor refuses a binding from anywhere else.
    """
    from behave.sim.core.compare import Comparison, comparison_for
    from behave.sim.core.outcome import ConcreteEvaluation
    from behave.sim.core.run_record import RunResult

    ref = _ref()
    task = api.admit(contract(), realization(), reference=ref)
    stim = schedule([3])
    right = run_device(task, stim, Doubling())
    wrong = run_device(task, stim, Doubling(gain=3))

    clean = api.compare_run(task, ref, right)
    assert clean.ok
    assert not hasattr(clean, "about"), "the rebinding entry point is gone"
    with pytest.raises(Refused, match="comparison-bound-outside-the-matcher"):
        Comparison(clean.why, clean.policy, clean.assumptions, clean.reordered,
                   complete=clean.complete, faults=clean.faults,
                   of_stimulus=stim.fingerprint, of_trace=wrong.fingerprint,
                   of_reference=task.reference)

    # Through the matcher, against the wrong design, the answer is the wrong design's.
    honest = comparison_for(task, ref, wrong)
    assert not honest.ok
    evaluation = ConcreteEvaluation(
        task, results=[RunResult.compared(stim, wrong, honest)])
    assert not evaluation.passed and evaluation.mismatch_count


def test_an_adapter_name_cannot_be_reclaimed_by_different_code():
    """The retired in-process adapter name cannot be reclaimed by new code."""
    from behave.sim.core import reference as ref_mod

    def impostor(sources, config):
        del sources, config
        return lambda: None

    with pytest.raises(Refused, match="reference-adapter-retired"):
        ref_mod.register_loader("transaction/1", impostor)

    # And the code behind the name is inside the digest, so two adapters that run
    # different loaders are two references even with identical sources.
    ref_mod.register_loader("test-adapter/1", impostor)
    try:
        one = api.ReferenceArtifact({"behavior.py": b"x"}, adapter="a",
                                    adapter_impl="loader-a")
        two = api.ReferenceArtifact({"behavior.py": b"x"}, adapter="a",
                                    adapter_impl="loader-b")
        assert one.digest != two.digest
    finally:
        ref_mod._LOADERS.pop("test-adapter/1", None)


def test_the_fingerprint_covers_what_each_goal_is_not_only_its_name():
    """It serialised `sorted(catalogue)`, so a catalogue whose goal changed class from
    coverage to safety, or was produced under a different policy, had one fingerprint --
    and the policy is what a goal count is relative to."""
    from behave.sim.core.catalogue import COVERAGE, SAFETY, Goal
    from behave.sim.core.outcome import ConcreteEvaluation

    task = api.admit(contract(), realization(), reference=_ref())
    def evaluation(cls, policy):
        return ConcreteEvaluation(
            task, catalogue={"g": Goal("g", cls, family="protocol",
                                       provider="monitor", policy=policy)})

    base = evaluation(SAFETY, "monitor/4")
    assert base.fingerprint != evaluation(COVERAGE, "monitor/4").fingerprint
    assert base.fingerprint != evaluation(SAFETY, "monitor/3").fingerprint








# ── 7. the report and its consumers ──

def _concrete(why="compared", evaluation=None):
    """A real concrete result: an in-memory evaluation over a real run, never a stub.

    `Authoritative` is a tagged union now -- CONCRETE admits only the exact
    `ConcreteEvaluation` type, non-empty and whole -- so the `_Compared` stubs the store
    tests used to carry were testing a state production must not allow. This is the
    cheapest real one: `Doubling` driven in memory over one stimulus.
    """
    from behave.sim.authority import CONCRETE, Authoritative

    if evaluation is None:
        task = api.admit(contract(), realization(), reference=_ref())
        evaluation = _evaluate(task, [schedule([3])], Doubling(),
                                  reference=_ref())
    return Authoritative(CONCRETE, why=why, evaluation=evaluation)


def _repo():
    return pathlib.Path(__file__).resolve().parents[2]


def _tool_module(name):
    """One of `tools/` as a module. They are scripts, not a package.

    Registered in `sys.modules` before it is executed, because a module that defines a
    dataclass looks itself up there while the class body runs.
    """
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(name, root / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_population_identity_is_complete_and_a_dataset_change_fails_comparison(
        tmp_path, capsys):
    """A population baseline is relative to exact dataset bytes.

    A truncated digest is not the dataset identity, and equal aggregate counts over a
    different dataset are not a successful reproduction of the baseline.
    """
    tool = _tool_module("population")
    task = tmp_path / "task"
    task.mkdir()
    (task / "behavior.py").write_text("class M: pass\n")
    (task / "spec.md").write_text("one\n")
    digest = tool.dataset_digest([task / "behavior.py"])
    assert len(digest) == 64
    same = tmp_path / "other" / "task"
    same.mkdir(parents=True)
    (same / "behavior.py").write_bytes((task / "behavior.py").read_bytes())
    (same / "spec.md").write_bytes((task / "spec.md").read_bytes())
    assert tool.dataset_digest([same / "behavior.py"]) != digest

    aggregates = {name: 0 for name in tool.OUTCOMES}
    got = {
        "provenance": {"dataset_digest": digest, "commit": "new"},
        "aggregates": aggregates,
        "gates": {},
        "models": [],
    }
    expected = {
        "provenance": {"dataset_digest": "0" * 64, "commit": "old"},
        "aggregates": dict(aggregates),
    }
    assert tool.report(got, expected) == 1
    assert "dataset digest differs" in capsys.readouterr().out




    # That the crash is recorded rather than fatal, and that the verdict is written
    # first, are runs: see `test_the_authoritative_result_is_on_disk_before_the_smoke_
    # test_runs`, which makes the chain raise and reads the record off the disk. This
    # test used to assert the order of two lines in the source, which kept passing while
    # what those lines did changed.






def test_the_catalogue_encoding_keeps_types_and_keys():
    """`str()` over every part of the identity collapsed `1` and `"1"` onto one encoding
    and threw away which provenance key each value was under."""
    from behave.sim.core.catalogue import SAFETY, Goal
    from behave.sim.core.outcome import ConcreteEvaluation

    task = api.admit(contract(), realization(), reference=_ref())

    def fingerprint(**provenance):
        return ConcreteEvaluation(
            task, catalogue={"g": Goal("g", SAFETY, provenance=provenance)}).fingerprint

    assert fingerprint(depth=1) != fingerprint(depth="1")
    assert fingerprint(a=1, b=2) != fingerprint(a=2, b=1), "the keys are kept"


def test_a_goal_s_provenance_cannot_be_edited_after_the_fingerprint():
    """It was frozen one level: the keys were stringified and the values were not
    touched, so a provenance carrying a list was editable inside the identity."""
    from behave.sim.core.catalogue import SAFETY, Goal

    goal = Goal("g", SAFETY, provenance={"owners": ["a", "b"]})
    owners = dict(goal.provenance)["owners"]
    assert isinstance(owners, tuple), owners
    with pytest.raises(AttributeError):
        owners.append("c")


def test_a_hit_outside_the_catalogue_is_refused():
    """`safety_hits` filters by the catalogue's class, so a hit naming a goal nobody
    enumerated was dropped from the classification while still counting as a hit."""
    from behave.sim.core.outcome import ConcreteEvaluation

    task = api.admit(contract(), realization(), reference=_ref())
    with pytest.raises(Refused, match="evaluation-hit-outside-the-catalogue"):
        ConcreteEvaluation(task, hits=["monitor.sink-stability"], catalogue={})


def test_the_matcher_will_not_take_an_expected_stream_from_its_caller():
    """The construction the second review reported, verbatim:

        comparison_for(endpoint, transfers_of(wrong_trace, "out"), stim, wrong, ref)
        -> passed=True, mismatch_count=0

    Real expected `[(6,)]`, observed `[(9,)]`, and the wrong design's own outputs handed
    in as the expectation. Binding the *result* to a trace does nothing when the thing
    being compared never came from it. The matcher now runs the frozen bundle itself and
    there is no argument through which an answer can be supplied.
    """
    import inspect

    from behave.sim.core.compare import comparison_for, transfers_of
    from behave.sim.core.outcome import ConcreteEvaluation
    from behave.sim.core.run_record import RunResult

    assert set(inspect.signature(comparison_for).parameters) == {
        "task", "reference", "trace"}, "no `expected`, no reference digest"

    ref = _ref()
    task = api.admit(contract(), realization(), reference=ref)
    stim = schedule([3])
    wrong = run_device(task, stim, Doubling(gain=3))
    assert api.expected_for(task, ref, wrong) == [(6,)]
    assert transfers_of(wrong, "out") == [(9,)]

    got = comparison_for(task, ref, wrong)
    evaluation = ConcreteEvaluation(
        task, results=[RunResult.compared(stim, wrong, got)])
    assert not evaluation.passed and evaluation.mismatch_count == 1
    assert got.faults == (("value", 0, (6,), (9,)),)


    # What a bounded run then reports is a run, not a shape: the next test caps a real
    # candidate set with `max_candidates=1` and reads the outcome off the result -- it
    # comes back `partial`, saying it was cut by a caller's bound.


# ── 8. the third review: a subset of Xi_A is not a score ──

def test_a_trace_of_another_contract_or_another_artifact_is_refused():
    """Reported and reproduced: a trace built with `domain="another-domain"` filed under
    this task passed, and so did admitting artifact A, running artifact B and filing B's
    trace under A. A trace said what stimulus it was of and nothing about what ran it."""
    from behave.sim.core.outcome import ConcreteEvaluation
    from behave.sim.core.run_record import RunResult
    from behave.sim.core.stimulus import AuditedTrace

    ref = _ref()
    task = api.admit(contract(), realization(), reference=ref)
    stim = schedule([3])
    honest = run_device(task, stim, Doubling())
    from behave.sim.core.stimulus import contract_of_domain

    assert contract_of_domain(honest.domain) == task.contract.digest
    assert honest.artifact == "doubling"

    def filed(**overrides):
        fields = {"records": list(honest.records), "domain": honest.domain,
                  "artifact": honest.artifact, "completion": honest.completion}
        fields.update(overrides)
        trace = AuditedTrace(stim, **fields)
        return ConcreteEvaluation(
            task, results=[RunResult.compared(
                stim, trace, api.compare_run(task, ref, trace))])

    with pytest.raises(Refused, match="run-result-trace-of-another-run"):
        filed(domain="another-domain")
    with pytest.raises(Refused, match="run-result-trace-of-another-run"):
        filed(artifact="some-other-design")

    # A runner that cannot say is recorded as unverifiable, not waved through.
    assert filed(artifact="").unbound == ("artifact",)


def test_two_artifacts_under_one_contract_are_two_evaluations():
    """The persisted result named the contract and the reference and not the design, so
    two different candidates produced the same fingerprint -- and a record that cannot
    tell them apart is evidence about neither."""
    from behave.sim.core.outcome import ConcreteEvaluation

    ref = _ref()
    def evaluation(identity):
        task = api.admit(contract(), realization(identity=identity), reference=ref)
        return ConcreteEvaluation(task)

    assert evaluation("A").fingerprint != evaluation("B").fingerprint


def test_a_goal_is_filed_under_its_own_name():
    """Nothing required the mapping key to be the goal's `id`, so two records with one
    identifier could sit under two keys and both survive the conflict check, which
    compares per key."""
    from behave.sim.core.catalogue import SAFETY, Goal, catalogue_of

    with pytest.raises(Refused, match="catalogue-key-is-not-the-goal-id"):
        catalogue_of({"b": Goal("a", SAFETY)})


def test_a_hit_no_run_recorded_is_refused_unless_its_provider_judges_it():
    """Hits were an aggregate the caller passed in beside the results, so the two could
    disagree about what a run reached and nothing compared them.

    Which hits a trace should carry is the catalogue's own answer: a goal whose
    declared **evidence location** is the slot record must appear in one. The monitor's
    goals are exempt because they say `evidence="trace"` -- judged over the trace
    rather than written into it. The exemption used to be spelled
    `provider == "monitor"`, which conflated who provides a goal with where its
    evidence lives. (A third location, `"set"`, existed for one release and review 10
    removed it: evidence with no step `t` on any admitted trace belongs in telemetry,
    not the catalogue.)
    """
    from behave.sim.core.catalogue import SAFETY, Goal
    from behave.sim.core.outcome import ConcreteEvaluation

    task = api.admit(contract(), realization(), reference=_ref())
    written = {"behaviour.branch.7": Goal("behaviour.branch.7", SAFETY,
                                          provider="behavior")}
    with pytest.raises(Refused, match="evaluation-hit-no-run-recorded"):
        ConcreteEvaluation(task, hits=["behaviour.branch.7"], catalogue=written)

    judged = {"monitor.sink-stability": Goal("monitor.sink-stability", SAFETY,
                                             provider="monitor",
                                             evidence="trace")}
    ConcreteEvaluation(task, hits=["monitor.sink-stability"], catalogue=judged)

    with pytest.raises(Refused, match="goal-evidence-unknown"):
        Goal("task.reset-restores", SAFETY, provider="reset_probe",
             evidence="set")


def _staged(name):
    """One problem plus its declaration overlay, in a scratch directory of its own.

    **Unique per call.** A fixed `scratch/wave3_{name}` was fine one test at a time and
    a collision under `-n 40`: two tests stage `dualedge`, each `rmtree`s and re-copies
    the path the other is running a simulator in. It passed alone and failed in the
    suite, which is the tell.
    """
    import os
    import shutil
    import uuid

    from behave.core.paths import problems_root

    source = next(p.parent for p in problems_root().rglob("*/behavior.py")
                  if p.parent.name == name)
    where = _repo() / "scratch" / f"wave3_{name}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    shutil.rmtree(where, ignore_errors=True)
    shutil.copytree(source, where)
    # Stage task semantics only. Each caller chooses its runtime policy explicitly.
    overlays = _repo() / "tools" / "declarations"
    shutil.copy2(overlays / f"{name}.toml", where / "task.toml")
    return where


def _top_of(problem):
    import re

    return re.findall(r"^\s*module\s+(\w+)",
                      re.sub(r"//[^\n]*", "", (problem / "rtl.sv").read_text()),
                      re.MULTILINE)[0]






# ── 9. the batch simulator ──



    # That the guard *fires* is `test_the_guard_catches_a_design_its_own_reset_does_not_
    # restore`, on `cvdp_copilot_gcd_0001` against a real toolchain; that a withheld
    # batch sends the evaluation back to one-at-a-time is
    # `test_a_withheld_batch_sends_evaluate_back_to_one_at_a_time`, driven with an
    # artifact that returns the refusal. Neither reads the source any more.


    # And what the artifact does with a run that stopped short is
    # `test_a_simulator_that_leaves_no_record_refuses_each_stimulus_by_name`, which
    # drives it rather than reading it.



    # The partition itself is driven in
    # `test_one_unrunnable_stimulus_keeps_its_own_refusal_and_the_others_run`: the
    # foreign stimulus keeps the driver's refusal, the rest are attempted, and the
    # simulator is invoked once for them.



    # And a batch that does run out of time says so by name, rather than raising into
    # the fallback that would take longer than the batch it replaced.
    # That it comes back as a refusal rather than an exception is
    # `test_a_batch_that_runs_out_of_time_says_so_rather_than_raising`, which makes
    # `make` time out and reads the refusals off the return value.




# ── 10. where a run starts is what its verdict is relative to ──





class _FakeComparison:
    """Everything `RunResult` read off a comparison, and nothing that makes one."""

    ok = complete = bound = True
    why, policy, assumptions, reordered, faults = "", None, (), 0, ()

    def __init__(self, stimulus, trace, reference):
        self.of_stimulus, self.of_trace, self.of_reference = stimulus, trace, reference

    def as_dict(self):
        return {"ok": True, "faults": []}


class _FakeRun:
    """Everything `ConcreteEvaluation` read off a result."""

    ran = was_compared = ok = True
    refused = run_failure = comparison_failure = ""
    trace = comparison = None
    faults = ()

    def __init__(self, stimulus):
        self.stimulus = stimulus

    def check(self, reference):
        return self

    def check_task(self, task):
        return ()

    def as_dict(self):
        return {}


def test_a_duck_typed_comparison_is_not_a_verdict():
    """`RunResult.compared` read `.of_stimulus`, `.of_trace`, `.bound` and `.ok` off
    whatever it was handed, so a dozen lines of duck type gave `passed=True,
    mismatch_count=0` over a design whose outputs disagreed. Every guarantee rests on the
    matcher having produced the object -- the token, the derivation of both streams, the
    freezing -- and an interface check tests for none of it."""
    from behave.sim.core.run_record import RunResult

    ref = _ref()
    task = api.admit(contract(), realization(), reference=ref)
    stim = schedule([3])
    wrong = run_device(task, stim, Doubling(gain=3))
    assert not api.compare_run(task, ref, wrong).ok, "the design really does disagree"

    with pytest.raises(Refused, match="run-result-comparison-not-a-comparison"):
        RunResult.compared(stim, wrong, _FakeComparison(
            stim.fingerprint, wrong.fingerprint, task.reference))


def test_a_duck_typed_result_is_not_a_run():
    """Same shape one level up, and it has to be checked before anything reads them:
    the hits aggregation reaches into `result.trace.records`, so a malformed impostor
    crashed there with an `AttributeError` instead of being told what was wrong."""
    from behave.sim.core.outcome import ConcreteEvaluation

    task = api.admit(contract(), realization(), reference=_ref())
    with pytest.raises(Refused, match="evaluation-result-not-a-run-record"):
        ConcreteEvaluation(task, results=[_FakeRun(schedule([3]))])


def test_a_trace_that_names_neither_contract_nor_artifact_cannot_pass():
    """The binding check recorded `unbound` and the score ignored it, so a
    correct-looking run of an unknown design under an unknown contract passed."""
    from behave.sim.core.outcome import ConcreteEvaluation
    from behave.sim.core.run_record import RunResult
    from behave.sim.core.stimulus import AuditedTrace

    ref = _ref()
    task = api.admit(contract(), realization(), reference=ref)
    stim = schedule([3])
    right = run_device(task, stim, Doubling())

    blind = AuditedTrace(stim, records=list(right.records),
                         completion=right.completion)
    got = ConcreteEvaluation(task, results=[RunResult.compared(
        stim, blind, api.compare_run(task, ref, blind))])
    assert got.unbound == ("artifact", "domain")
    assert not got.passed, "an unverifiable binding is not a passing one"

    # The same run, bound, does pass -- so it is the binding that is being refused and
    # not the run.
    assert ConcreteEvaluation(task, results=[RunResult.compared(
        stim, right, api.compare_run(task, ref, right))]).passed





    # And both withholding kinds send the evaluation back to one-at-a-time, which
    # `test_a_withheld_batch_sends_evaluate_back_to_one_at_a_time` drives for each of
    # them rather than reading the two names out of `_batched`.




# ── 6. the order of production, and what survives a kill ──

def test_a_decided_score_is_not_reported_as_partial_because_telemetry_was_killed():
    """The watchdog set `partial: True` unconditionally. Since the authoritative result is
    computed and written before the smoke test, a kill during the smoke test downgraded a
    verdict that was already complete -- for every task whose telemetry ran long."""
    from behave.sim.result_store import partial_after_timeout

    assert partial_after_timeout({"phase_reached": "replay"}) is True
    assert partial_after_timeout(
        {"phase_reached": "replay", "score_decided_before_any_timeout": True}) is False


def test_the_atomic_write_leaves_no_moment_with_no_file():
    """It unlinked the previous file and then wrote through a rename -- so between the two
    calls there was no file at all, which is the failure the rename exists to prevent. A
    reader landing in that window saw a run that had written nothing."""
    import json
    import os

    from behave.sim.result_store import write_json_atomically

    where = _repo() / "scratch" / f"atomic_{os.getpid()}.json"
    where.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomically(where, {"kind": "first"})

    # A write that dies before the rename: the previous record is still whole.
    real = os.replace
    try:
        os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("killed"))
        with pytest.raises(OSError):
            write_json_atomically(where, {"kind": "second"})
    finally:
        os.replace = real
    assert json.loads(where.read_text()) == {"kind": "first"}

    write_json_atomically(where, {"kind": "second"})
    assert json.loads(where.read_text()) == {"kind": "second"}
    where.unlink()
    for leftover in where.parent.glob(f".{where.name}.*.partial"):
        leftover.unlink()


def test_a_hit_a_run_recorded_and_nobody_reported_is_refused_too():
    """The check ran in one direction only. A hit with no record was refused; a record
    with no hit passed -- and that is the dangerous way round. Where the hit is a
    violation, the trace observed a fault, the evaluation's `hits` does not carry it, and
    `passed` is computed from what the evaluation carries: a clean score over a record of
    the violation.

    Recorded against a real trace, with the hit written into a slot the way a provider
    would write it, rather than by asserting on the shape of the code.
    """
    from behave.sim.core.catalogue import SAFETY, Goal
    from behave.sim.core.outcome import ConcreteEvaluation
    from behave.sim.core.run_record import RunResult
    from behave.sim.core.stimulus import AuditedTrace, SlotRecord

    from .test_api_path import in_memory

    task = api.admit(contract(), realization(), reference=_ref())
    stimulus = schedule([1, 2])
    trace = in_memory(task, stimulus, Doubling())

    # A provider writing its hit into the record, which is what `SlotRecord.hits` is for.
    marked = list(trace.records)
    first = marked[0]
    marked[0] = SlotRecord(
        accepted={name: txn for name, txn in first.accepted},
        state={k: v for k, v in first.state},
        outputs={k: v for k, v in first.outputs},
        hits={"behaviour.branch.7": True},
        monitor={k: v for k, v in first.monitor})
    recorded = AuditedTrace(stimulus, initial_state=dict(trace.initial_state),
                            records=marked, completion=trace.completion,
                            domain=trace.domain, artifact=trace.artifact)
    result = RunResult.not_compared(
        stimulus, recorded,
        api.RunFailure.verifier(
            "test-audit-view",
            api.Refusal("test-uncompared", "not the point of this test")))
    written = {"behaviour.branch.7": Goal("behaviour.branch.7", SAFETY,
                                          provider="behavior")}

    # Reported: fine, the record carries it.
    ConcreteEvaluation(task, results=[result], hits=["behaviour.branch.7"],
                       catalogue=written)
    # Not reported: the two disagree, and the evaluation says so instead of scoring.
    with pytest.raises(Refused, match="evaluation-hit-recorded-but-not-reported"):
        ConcreteEvaluation(task, results=[result], hits=(), catalogue=written)
    # And a recorded goal nobody enumerated is named for what it is.
    with pytest.raises(Refused, match="evaluation-recorded-hit-outside-the-catalogue"):
        ConcreteEvaluation(task, results=[result], hits=(), catalogue={})


# ── 12. T3-09: a fresh model is a fresh execution environment ──













# ── 13. the fourth review: a sample is not a verification ──



@pytest.mark.cosim
def test_the_writer_survives_two_writers_in_one_process():
    """The temporary file was named after the PID, and the two writers this build has --
    the main thread and the watchdog -- are one process. Reproduced at the reviewer's
    suggestion: eight writers, one finished, seven raised `FileNotFoundError` because
    another had already renamed the file out from under it. Their records were lost."""
    import json
    import os
    import threading

    from behave.sim.result_store import write_json_atomically

    where = _repo() / "scratch" / f"writers_{os.getpid()}"
    shutil = __import__("shutil")
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir(parents=True)
    target = where / "authority.json"

    errors, done = [], []

    def write(n):
        try:
            write_json_atomically(target, {"writer": n, "payload": "x" * 20000})
            done.append(n)
        except Exception as exc:                               # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors[:2]
    assert len(done) == 8
    # One file, complete, and nothing half-written left beside it.
    assert sorted(p.name for p in where.iterdir()) == ["authority.json"]
    assert json.loads(target.read_text())["payload"].startswith("x")
    shutil.rmtree(where, ignore_errors=True)


# ── 14. the fifth review: what the digest covers must be what executes ──











# ── 15. the candidate's code, and whose process it runs in ──





























# ── 16. the shape assertions that could become runs ──













# ── 17. the sixth review: one run, one policy ──














#: And the forms that *are* 1:1, which the rule has to keep reading -- taken from the
#: population rather than invented, with their counts among the 343 manifests the loose
#: rule produced.
