"""Sharing a stateless formula never identifies two positions' inputs."""

from types import SimpleNamespace

import pytest

z3 = pytest.importorskip("z3", reason="solver lane requires z3-solver")
pytestmark = pytest.mark.solver

from behave.sim.behavior import audit
from behave.sim.symbolic import encoder
from behave.sim.behavior.instance import lower
from behave.sim.core.audit import AuditCheckStatus
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

SOURCE = '''
class M:
    def reset(self):
        pass
    def process(self, a):
        y = a + 1
        if a == 2:
            y = a + 3
        return {"y": y}
'''


def _instance(source=SOURCE):
    schema = TransactionSchema(
        [Endpoint("a", "input", 3), Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    return lower(source, schema, bound=3).bind(3).instance


def _trace(*values):
    return [{"a": value} for value in values]


def _watch(monkeypatch):
    built, checked = [], []
    encode = encoder._encode_derived_step
    check = z3.Solver.check

    def build(*args, **kwargs):
        built.append(kwargs["tag"])
        return encode(*args, **kwargs)

    def solve(solver, *args, **kwargs):
        checked.append(solver)
        return check(solver, *args, **kwargs)

    monkeypatch.setattr(encoder, "_encode_derived_step", build)
    monkeypatch.setattr(z3.Solver, "check", solve)
    return built, checked


def test_distinct_inputs_use_one_formula_and_separate_solvers(monkeypatch):
    instance = _instance()
    built, checked = _watch(monkeypatch)
    assert instance.audit((_trace(1),)).status is AuditCheckStatus.AGREED
    assert instance.audit((_trace(1, 2, 3),)).status is AuditCheckStatus.AGREED
    prepared = instance._prepared_step_audit
    assert prepared.independent
    assert len(prepared.encodings) == 3
    assert all(enc is prepared.encodings[0] for enc in prepared.encodings)
    assert built == ["@step-audit-0"]
    assert len(checked) == 4
    assert len({id(solver) for solver in checked}) == 4


def test_identical_adjacent_inputs_share_only_the_fixed_equations(monkeypatch):
    instance = _instance()
    built, checked = _watch(monkeypatch)
    observed = []
    observe = audit._observe_step

    def record(proc, inputs, state):
        observed.append(dict(inputs))
        return observe(proc, inputs, state)

    monkeypatch.setattr(audit, "_observe_step", record)
    repeated = _trace(1, 1, 1)
    assert instance.audit((repeated, repeated)).status is AuditCheckStatus.AGREED
    assert len(built) == 1
    assert len(checked) == 2  # Fresh query for each trace, not cached audit answers.
    assert len(observed) == 6
    assert instance.audit((_trace(1, 2, 1),)).status is AuditCheckStatus.AGREED
    assert len(checked) == 5  # Retain only the preceding model, not a growing cache.
    assert len(observed) == 9


def test_fixed_input_identity_uses_ir_values_not_record_normalization():
    from behave.sim.symbolic.uniform_array import UniformArray

    dense = {"a": [[1, 2], [1, 2]]}
    compact = {"a": UniformArray.uniform(2, [1, 2])}
    image = audit._audit_input_image(dense)
    sparse_image = audit._audit_input_image(compact)
    assert sparse_image["a"] is compact["a"]
    assert audit._same_audit_inputs(image, sparse_image)
    dense["a"][0][0] = 9
    assert not audit._same_audit_inputs(audit._audit_input_image(dense), image)
    assert not audit._same_audit_inputs({"a": True}, {"a": 1})
    # Core records normalize tuple/list alike; the IR input checker does not.
    assert audit._audit_input_image({"a": (1, 2)}) is None
    assert audit._audit_input_image({"a": object()}) is None
    assert not audit._same_audit_inputs(image, None)


def test_reused_model_does_not_skip_later_concrete_comparisons(monkeypatch):
    instance = _instance()
    _built, checked = _watch(monkeypatch)
    observe = audit._observe_step
    calls = 0
    name = instance.proc.output_vars["y"]

    def corrupt_second(proc, inputs, state):
        nonlocal calls
        calls += 1
        post, produced, hits, error, left = observe(proc, inputs, state)
        if calls == 2:
            left[name] = [99]
        return post, produced, hits, error, left

    monkeypatch.setattr(audit, "_observe_step", corrupt_second)
    result = instance.audit((_trace(1, 1, 1),))
    assert len(checked) == 1
    assert calls == 3
    assert result.status is AuditCheckStatus.INCONSISTENT
    assert any(name in finding for finding in result.findings)


@pytest.mark.parametrize("failure", [False, True])
def test_shared_and_separately_encoded_steps_agree(monkeypatch, failure):
    source = SOURCE if not failure else SOURCE.replace(
        'return {"y": y}', 'return {"y": 8 // a}')
    shared = _instance(source)
    traces = (_trace(1, 2, 3), _trace(2, 0, 3), _trace(0, 2, 1))
    current = shared.audit(traces)
    assert current.status is AuditCheckStatus.AGREED
    assert shared._prepared_step_audit.independent
    # Same admitted program/domain, but construct every position as before.
    monkeypatch.setattr(audit, "_independent_audit_positions", lambda *_: False)
    separate = _instance(source)
    before = separate.audit(traces)
    assert before.as_dict() == current.as_dict()
    assert not separate._prepared_step_audit.independent
    assert len({id(enc) for enc in separate._prepared_step_audit.encodings}) == 3


def test_stateful_audit_still_checks_one_chained_trace(monkeypatch):
    source = SOURCE.replace("pass", "self.n = 0").replace(
        "y = a + 1", "self.n += a\n        y = self.n")
    instance = _instance(source)
    built, checked = _watch(monkeypatch)
    assert instance.audit((_trace(1, 3, 1),)).status is AuditCheckStatus.AGREED
    assert not instance._prepared_step_audit.independent
    assert len(built) == 3
    assert len(checked) == 1


def test_partial_failure_checks_the_failing_step_and_absorbs_the_suffix(monkeypatch):
    instance = _instance(SOURCE.replace('return {"y": y}', 'return {"y": 8 // a}'))
    built, checked = _watch(monkeypatch)
    trace = _trace(1, 0, 3)
    assert instance.audit((trace,)).status is AuditCheckStatus.AGREED
    assert len(built) == 1
    assert len(checked) == 2
    replay = instance.replay(trace)
    assert replay.error == "DivZero"
    assert replay.outputs[-1].count == 0
    assert any(goal.startswith("safety:") for goal in replay.hits)


def test_later_input_dependent_encoding_error_is_not_hidden_by_sharing():
    instance = _instance()
    assert instance.audit((_trace(1, 2, 3),)).status is AuditCheckStatus.AGREED
    encoded = instance._prepared_step_audit.encodings[0]
    name = instance.proc.output_vars["y"]
    output = encoded.final[name]
    index = z3.BitVecVal(0, 2)
    encoded.final[name] = output.store(
        index, output.select(index) + 1, encoded.pre["a"] == 2)
    # A correct first input cannot certify later uses of the shared formula.
    assert instance.audit((_trace(1),)).status is AuditCheckStatus.AGREED
    wrong = instance.audit((_trace(1, 2, 3),))
    assert wrong.status is AuditCheckStatus.INCONSISTENT
    assert any(name in finding for finding in wrong.findings)


def test_different_position_plans_do_not_share():
    instance = _instance()
    raw = instance._position_plan.checked_for(instance.proc, instance.env, 3)
    assert audit._independent_audit_positions(instance.proc, raw)
    changed = SimpleNamespace(
        state_layers=raw.state_layers, steps=(raw.steps[0], object(), raw.steps[2]),
        occurrence_layers=raw.occurrence_layers, environment_at=raw.environment_at)
    assert not audit._independent_audit_positions(instance.proc, changed)


def test_nonuniform_occurrence_carriers_do_not_share():
    from behave.sim.symbolic.plan import OccurrenceCarriers

    instance = _instance()
    raw = instance._position_plan.checked_for(instance.proc, instance.env, 3)
    changed = SimpleNamespace(
        state_layers=raw.state_layers, steps=raw.steps,
        occurrence_layers=(raw.occurrence_layers[0], OccurrenceCarriers(),
                           raw.occurrence_layers[2]),
        environment_at=raw.environment_at)
    assert not audit._independent_audit_positions(instance.proc, changed)


def test_nonuniform_input_support_does_not_share():
    from behave.sim.symbolic.ranges import Range

    instance = _instance()
    raw = instance._position_plan.checked_for(instance.proc, instance.env, 3)

    def environment_at(position):
        environment = dict(raw.environment_at(position))
        if position == 1:
            environment["a"] = Range(0, 1)
        return environment

    changed = SimpleNamespace(
        state_layers=raw.state_layers, steps=raw.steps,
        occurrence_layers=raw.occurrence_layers, environment_at=environment_at)
    assert not audit._independent_audit_positions(instance.proc, changed)
