"""Suspend exact construction, not semantic execution or a solver verdict."""

import pytest
import z3

from behave.sim.symbolic.exact_formula import _FormulaConstruction
from behave.sim.symbolic import encoder, interp, plan
from behave.sim.engines import local_ir as exact
from behave.sim.engines import probing

from .test_local_query_sharing import problem
from .test_loop_index_specialization import command, encode, leaves

pytestmark = pytest.mark.solver


@pytest.mark.parametrize("state", [0, 4])
def test_every_encoder_checkpoint_can_pause_without_changing_the_step(state):
    proc, env = problem(trips=2)
    pre = exact.pin_state(proc, env, {"s": state})
    checkpoints = []
    whole = encoder._encode_step(proc, pre=pre, env=env, tag="whole",
                                 _checkpoint=lambda: checkpoints.append(1))
    for pause_at in range(1, len(checkpoints) + 1):
        construction, calls, pauses = encoder._StepConstruction(), [0], []

        def checkpoint(calls=calls, pauses=pauses, pause_at=pause_at):
            calls[0] += 1
            if calls[0] == pause_at:
                pauses.append(calls[0])
                raise encoder._EncodingPaused

        try:
            result = encoder._encode_step(
                proc, pre=pre, env=env, tag=f"paused{pause_at}",
                _checkpoint=checkpoint, _construction=construction)
        except encoder._EncodingPaused:
            pytest.fail("an owned setup checkpoint must retain its continuation")
        assert pauses and result is None
        result = encoder._encode_step(
            proc, pre=pre, env=env, tag=f"paused{pause_at}",
            _checkpoint=checkpoint, _construction=construction)
        assert result is not None
        assert {g: len(v) for g, v in result.hit_occurrences.items()} == {
            g: len(v) for g, v in whole.hit_occurrences.items()}
        solver = z3.Solver()
        solver.add(result.constraint, whole.constraint, result.pre["a"] == whole.pre["a"])
        differences = [result.ok != whole.ok, result.error != whole.error]
        differences.extend(result.final[n] != value for n, value in whole.final.items())
        differences.extend(result.hits[g] != value for g, value in whole.hits.items())
        solver.add(z3.Or(differences))
        assert solver.check() == z3.unsat, pause_at


@pytest.mark.parametrize("phase", ["plan", "numbering", "boundary"])
def test_setup_progress_is_retained_instead_of_repeated_after_each_deadline(monkeypatch, phase):
    proc, env = problem(trips=2)
    pre = exact.pin_state(proc, env, {"s": 0})
    validation = encoder._procedure_validation(proc)
    prepared = None if phase == "plan" else plan.plan_encoding(
        proc, env, _procedure_validation=validation)
    whole = encoder._encode_step(proc, pre=pre, env=env, tag="whole", plan=prepared,
                                 _procedure_validation=validation, _plan_prevalidated=True)
    # The reference formula must not prewarm the setup stage whose pause is
    # exercised below. A new invocation owns its own implicit-plan cache.
    validation = encoder._procedure_validation(proc)
    module, name = {
        "plan": (plan, "plan_encoding"),
        "numbering": (plan, "_command_numbers"),
        "boundary": (encoder, "_semantic_type_constraints"),
    }[phase]
    original, calls, expired = getattr(module, name), [], [False]

    def slow_once(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:
            expired[0] = True
        return result

    def checkpoint():
        if expired[0]:
            raise encoder._EncodingPaused

    monkeypatch.setattr(module, name, slow_once)
    construction = encoder._StepConstruction()
    options = {"pre": pre, "env": env, "tag": "resumed", "plan": prepared,
               "_procedure_validation": validation, "_plan_prevalidated": True,
               "_construction": construction, "_checkpoint": checkpoint}
    assert encoder._encode_step(proc, **options) is None
    assert calls == [1] and construction.iterator is not None
    assert construction.result is None  # A partial boundary is not a formula.
    expired[0] = False
    result = encoder._encode_step(proc, **options)
    assert result is not None
    assert len(calls) == (len(proc.state + proc.inputs) if phase == "boundary" else 1)
    solver = z3.Solver()
    solver.add(whole.constraint, result.constraint, whole.pre["a"] == result.pre["a"])
    solver.add(z3.Or(whole.ok != result.ok, whole.error != result.error,
                     *(whole.final[n] != term for n, term in result.final.items()),
                     *(whole.hits[g] != term for g, term in result.hits.items())))
    assert solver.check() == z3.unsat


def test_external_cancellation_after_setup_pause_discards_the_continuation():
    proc, env = problem()
    construction = encoder._StepConstruction()

    def paused():
        raise encoder._EncodingPaused

    assert encoder._encode_step(proc, env=env, _construction=construction,
                                _checkpoint=paused) is None
    assert construction.iterator is not None

    def cancelled():
        raise RuntimeError("outer search cancelled")

    with pytest.raises(RuntimeError, match="outer search cancelled"):
        encoder._encode_step(proc, env=env, _construction=construction, _checkpoint=cancelled)
    assert construction.iterator is None and construction.result is None


@pytest.mark.parametrize('changing_tag', [False, True])
def test_local_query_keeps_completed_commands_and_replays_the_witness(monkeypatch, changing_tag):
    proc, env = problem(trips=8)
    prepared = exact._LocalQueryPreparation(proc, env)
    constructions, proved, clock, writes = {}, set(), [0.0], []
    assign = encoder._Commands._assign

    def timed_assignment(self, command, state, reached, **kwargs):
        result = assign(self, command, state, reached, **kwargs)
        writes.append((command.var, repr(command.expr)))
        clock[0] += .025
        return result

    monkeypatch.setattr(encoder._Commands, "_assign", timed_assignment)
    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    answers = []
    for attempt in range(40):
        answer = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=50,
                                tag=f'query_{attempt}' if changing_tag else 'query',
                                _proved_unsat=proved, _constructions=constructions)
        answers.append(answer)
        if answer.outcome is not probing.Outcome.CONSTRUCTING:
            break
    assert len(answers) > 1
    assert answers[-1].outcome is probing.Outcome.SAT, answers[-1].detail
    assert all(a.candidate is None for a in answers[:-1])
    assert answers[-1].goal in interp.run_step(proc, answers[-1].candidate, {"s": 0})[2]
    # The first retained assignment is not replayed at the start of each slice.
    resumed_writes = tuple(writes)
    writes.clear()
    fresh = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=20_000,
                            _constructions={})
    assert fresh.outcome is probing.Outcome.SAT
    assert resumed_writes == tuple(writes)
    assert not constructions  # A SAT answer/candidate is not cached.


def test_external_error_is_not_a_resumable_pause():
    proc, env = problem()
    construction = encoder._StepConstruction()

    def cancelled():
        raise RuntimeError("outer search cancelled")

    with pytest.raises(RuntimeError, match="outer search cancelled"):
        encoder._encode_step(proc, env=env, _construction=construction, _checkpoint=cancelled)
    assert construction.iterator is None and construction.result is None


@pytest.mark.parametrize('changing_tag', [False, True])
@pytest.mark.parametrize('state', [0, 4])
def test_completed_formula_survives_a_later_solver_unknown(monkeypatch, changing_tag, state):
    proc, env = problem()
    prepared, constructions, built = exact._LocalQueryPreparation(proc, env), {}, []
    make = encoder._encode_step_iter
    check = type(z3.Solver()).check

    def count(*args, **kwargs):
        built.append(1)
        # This regression is specifically about a completed full relation.
        # Prefix UNKNOWN/continuation is covered by test_local_goal_prefix.
        kwargs["_prefix_goals"] = False
        return (yield from make(*args, **kwargs))

    interrupted = []

    def unknown(self):
        interrupted.append(self)
        return z3.unknown

    monkeypatch.setattr(encoder, "_encode_step_iter", count)
    monkeypatch.setattr(type(z3.Solver()), "check", unknown)
    answer = prepared.probe({"s": state}, ("hit", "bad"), tag='first', _constructions=constructions,
        timeout_ms=60_000)
    assert answer.outcome is probing.Outcome.UNKNOWN and len(built) == 1
    retained = next(iter(constructions.values()))
    assert retained.formula is not None and retained.selected is not None
    assert retained.solver is None and len(interrupted) == 1
    queried = []

    def resumed_check(self):
        assert self not in interrupted, "a cancelled solver cannot supply a later model"
        queried.append(self)
        return check(self)

    monkeypatch.setattr(type(z3.Solver()), "check", resumed_check)
    answer = prepared.probe({"s": state}, ("hit", "bad"),
                            tag='next' if changing_tag else 'first', _constructions=constructions,
        timeout_ms=60_000)
    expected = probing.Outcome.SAT if state == 0 else probing.Outcome.LOCAL_UNSAT
    assert answer.outcome is expected and len(built) == 1
    assert len(queried) == 1 and queried == [retained.solver] and not constructions
    assert tuple(queried[0].assertions()) == tuple(interrupted[0].assertions())
    if state == 0:
        assert answer.goal in interp.run_step(proc, answer.candidate, {"s": state})[2]
    else:
        assert not set().union(*(interp.run_step(proc, {"a": a}, {"s": state})[2]
                                 for a in range(4))) & {"hit", "bad"}


@pytest.mark.parametrize('changing_tag', [False, True])
def test_registration_can_finish_at_deadline_without_restarting_or_using_a_stale_clock(monkeypatch, changing_tag):
    proc, env = problem()
    prepared, constructions, clock, registrations = exact._LocalQueryPreparation(proc, env), {}, [0.0], []
    register = _FormulaConstruction.advance

    def slow_registration(self, checkpoint):
        result = register(self, checkpoint)
        registrations.append(1)
        clock[0] += .101
        return result

    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(_FormulaConstruction, "advance", slow_registration)
    first = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=100, tag='first',
                           _constructions=constructions)
    assert first.outcome is probing.Outcome.CONSTRUCTING
    pending = next(iter(constructions.values()))
    assert pending.formula is not None and pending.selected is None and pending.solver is None
    # Do not reset the fake clock: only the new admitted Probe owns a new deadline.
    second = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=100,
                            tag='next' if changing_tag else 'first',
                            _constructions=constructions)
    assert second.outcome is probing.Outcome.SAT and registrations == [1]
    assert second.goal in interp.run_step(proc, second.candidate, {"s": 0})[2]


def test_unfinished_registration_never_publishes_a_formula(monkeypatch):
    proc, env = problem()
    prepared, constructions, clock = exact._LocalQueryPreparation(proc, env), {}, [0.0]
    register = _FormulaConstruction.advance

    def expired_registration(self, checkpoint):
        clock[0] += .101
        return register(self, checkpoint)

    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(_FormulaConstruction, "advance", expired_registration)
    first = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=100,
                           _constructions=constructions)
    assert first.outcome is probing.Outcome.CONSTRUCTING
    pending = next(iter(constructions.values()))
    assert pending.formula is None and pending.selected is None and pending.solver is None
    assert pending.encoding.result is not None or pending.encoding.prefix is not None
    monkeypatch.setattr(_FormulaConstruction, "advance", register)
    second = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=100,
                            _constructions=constructions)
    assert second.outcome is probing.Outcome.SAT


def test_public_one_shot_query_cannot_import_an_unchecked_continuation():
    proc, env = problem()
    with pytest.raises(TypeError, match="_constructions"):
        exact.local_hit_any(proc, env, {"s": 0}, ("hit",), _constructions={},
            timeout_ms=60_000)


@pytest.mark.parametrize('changing_tag', [False, True])
def test_local_registration_progress_survives_several_probe_deadlines(monkeypatch, changing_tag):
    proc, env = problem(trips=2)
    prepared, pending = exact._LocalQueryPreparation(proc, env), {}
    clock, starts, finished = [0.0], [], []
    register = _FormulaConstruction.advance
    start = encoder.StepEncoding._start_query_formula

    def started(self, **options):
        result = start(self, **options)
        starts.append(result)
        return result

    def metered(self, checkpoint):
        registering = [True]
        def tick():
            if registering[0]:
                clock[0] += .005
            checkpoint()
        try:
            result = register(self, tick)
        finally:
            registering[0] = False
        finished.append(result)
        return result

    monkeypatch.setattr(exact.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(encoder.StepEncoding, "_start_query_formula", started)
    monkeypatch.setattr(_FormulaConstruction, "advance", metered)
    answers = []
    for attempt in range(300):
        answer = prepared.probe({"s": 0}, ("hit", "bad"), timeout_ms=100,
                                tag=f'query_{attempt}' if changing_tag else 'query',
                                _constructions=pending)
        answers.append(answer)
        if answer.outcome is not probing.Outcome.CONSTRUCTING:
            break
        assert answer.candidate is None
    assert len(answers) > 2 and len(starts) == len(finished) == 1
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.goal in interp.run_step(proc, answer.candidate, {"s": 0})[2]
    assert not pending


@pytest.mark.parametrize("kind", ["basic", "count-write", "nested-distinct",
                                  "nested-same", "index-write"])
def test_nested_loop_array_and_failure_continuations_match_uninterrupted_encoding(monkeypatch, kind):
    body = command(kind)
    pre = {n: z3.BitVec(f"resume_pre_{n}", 16) for n in ("enabled", "n", "stop")}
    pre.update({n: z3.BitVecVal(v, 16) for n, v in {"i": 6, "j": 5, "x": 1, "y": 7}.items()})
    pre["buf"] = [z3.BitVecVal(0, 16) for _ in range(3)]
    whole, before = encode(body, pre, "whole")
    pauses = []

    def paused_cmd(self, node, state, reached):
        calls = [0]

        def checkpoint():
            calls[0] += 1
            if calls[0] % 17 == 0:
                pauses.append(1)
                raise encoder._EncodingPaused

        self._checkpoint = checkpoint
        iterator = self._cmd(node, state, reached)
        for _ in range(1000):
            try:
                next(iterator)
            except StopIteration as done:
                return done.value
        pytest.fail("command construction made no progress")

    monkeypatch.setattr(encoder._Commands, "cmd", paused_cmd)
    resumed, after = encode(body, pre, "resumed")
    assert pauses and not resumed.loop_context
    assert {g: len(v) for g, v in after.hit_occurrences.items()} == {
        g: len(v) for g, v in before.hit_occurrences.items()}
    solver = z3.Solver()
    solver.add(*whole.defs, *resumed.defs,
               pre["enabled"] >= 0, pre["enabled"] <= 1,
               pre["n"] >= -1, pre["n"] <= 3,
               pre["stop"] >= -1, pre["stop"] <= 2)
    differences = [before.ok != after.ok, before.error != after.error]
    differences.extend(before.hits[g] != after.hits[g] for g in before.hits)
    for name in before.rho:
        differences.extend(a != b for a, b in zip(leaves(before.rho[name]),
                                                  leaves(after.rho[name]), strict=True))
    solver.add(z3.Or(differences))
    assert solver.check() == z3.unsat
