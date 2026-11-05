"""Python modular power agrees in the source catalogue, both IRs and queries."""

import pytest

from behave.sim.behavior import audit, instrumented
from behave.sim.symbolic import encoder, interp, ranges
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.instance import lower
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
from behave.sim.engines.local_ir import local_hit_any
from behave.sim.engines.probing import Outcome


def _source(expression, helpers=""):
    return ("class M:\n    def reset(self): self.n = 0\n" + helpers
            + "    def process(self, a):\n"
            + f"        result = {expression}\n"
            + "        return {'y': (result + self.n) & 255}\n")


PARAMETERS = [(-3, 9), (-1, -13), (-1, -8), (-1, -1), (-1, 0),
              (-1, 1), (-1, 2), (-1, 4), (-1, 7), (-1, 10),
              (-2, 16), (0, 0), (0, -7), (0, 1), (1, -13), (5, 9),
              (17, -8), (True, True), (False, False)]


def _native(model, visits, plan, value):
    model.reset()
    visits.clear()
    try:
        result = model.process(value)
    except (ValueError, ZeroDivisionError) as error:
        goal = instrumented.attribute_safety_failure(error, plan.safety_sites)
        assert goal is not None, (error, plan.safety_sites)
        return None, set(visits) | {goal}
    return result, set(visits)


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
@pytest.mark.parametrize("exponent,modulus", PARAMETERS)
def test_signed_modular_powers_native_outputs_errors_hits(lowerer, exponent, modulus):
    source = _source(f"pow(a - 16, {exponent!r}, {modulus!r})")
    proc = lower_with_state_ranges(source, input_widths=[5], lowerer=lowerer)
    plan = plan_source(source_model(source), source)
    namespace, _, visits = instrumented.load_source(source, goal_plan=plan)
    model = namespace['M']()
    for value in range(32):
        output, expected = _native(model, visits, plan, value)
        _, actual, hits, error = interp.run_step(proc, {'a': value})
        assert bool(error) == (output is None)
        assert hits == expected
        if output is not None:
            assert actual.transactions == [output]
        else:
            assert error == 'ValueError'
    assert audit.model_audit(proc, namespace['M'], [[{'a': a}] for a in range(32)]) == []


@pytest.mark.solver
@pytest.mark.parametrize("exponent,modulus", [(-1, 8), (-2, 9), (3, -7), (-1, 0)])
def test_all_goals_have_queries_and_sat_witnesses_are_native(exponent, modulus):
    source = _source(f"pow(a - 4, {exponent}, {modulus})")
    schema = TransactionSchema(
        [Endpoint('a', 'input', 3), Endpoint('y', 'output', 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    exact = lower(source, schema, bound=1)
    plan = plan_source(source_model(source), source)
    assert exact.frontend_catalogue_manifest() == plan.manifest
    namespace, _, visits = instrumented.load_source(source, goal_plan=plan)
    model = namespace['M']()
    possible = set()
    for value in range(8):
        possible.update(_native(model, visits, plan, value)[1])
    for goal in plan.manifest:
        answer = local_hit_any(exact.proc, exact.env, exact.proc.initial, (goal,),
            timeout_ms=60_000)
        assert answer.outcome is (Outcome.SAT if goal in possible else Outcome.LOCAL_UNSAT)
        if answer.outcome is Outcome.SAT:
            assert goal in _native(model, visits, plan, answer.candidate['a'])[1]


@pytest.mark.solver
@pytest.mark.parametrize("exponent,modulus", [(-1, 8), (-1, 9), (5, -7)])
def test_encoded_output_and_error_match_every_small_input(exponent, modulus):
    z3 = pytest.importorskip('z3')
    source = _source(f"pow(a - 4, {exponent}, {modulus})")
    proc = lower_with_state_ranges(source, input_widths=[3])
    env = {**proc.state_ranges, 'a': ranges.Range(0, 7)}
    pre = {name: encoder.encode_concrete_value(
        proc.initial[name], env[name], name, kind=proc.types[name]) for name in proc.state}
    encoded = encoder.encode_step(proc, pre=pre, env=env)
    for value in range(8):
        _, output, hits, error = interp.run_step(proc, {'a': value})
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre['a'] == value)
        assert solver.check() == z3.sat
        differences = [encoded.ok != (not error)]
        differences.extend(term != (goal in hits) for goal, term in encoded.hits.items())
        if not error:
            differences.append(encoded.post['y'][0] != output.transactions[0]['y'])
        solver.add(z3.Or(*differences))
        assert solver.check() == z3.unsat


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('expression', [
    'pow(a > 2, -1, 7)', 'pow(self.bump(a), -1, 8)',
    'pow(1 // (a - 2), -1, 8)', 'pow(1 << (a - 2), -1, 8)',
])
def test_operand_evaluation_and_failure_order(lowerer, expression):
    source = _source(expression, "    def bump(self, a):\n"
                     "        self.n += 1\n        return a\n")
    proc = lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer)
    namespace = {}
    exec(compile(source, 'behavior.py', 'exec'), namespace)  # noqa: S102
    assert audit.model_audit(proc, namespace['M'], [[{'a': a}] for a in range(4)]) == []


def test_shadowed_pow_is_a_helper_not_a_modular_builtin():
    source = "def pow(a, b, c): return a + b + c\n" + _source('pow(a, -1, 0)')
    proc = lower_with_state_ranges(source, input_widths=[2])
    plan = plan_source(source_model(source), source)
    assert not [site for site in plan.safety_sites if site.kind == 'valueerror']
    assert interp.run_step(proc, {'a': 3})[1].transactions == [{'y': 2}]


@pytest.mark.parametrize('expression', ['pow(a, -1)', 'pow(a, a, 8)', 'pow(a, 2, a)',
                                       'pow([a], -1, 8)', 'pow(a, exp=-1, mod=8)'])
def test_unrepresented_forms_remain_explicit_refusals(expression):
    with pytest.raises(LoweringError):
        lower_with_state_ranges(_source(expression), input_widths=[2])


def test_missing_partiality_producer_is_detected(monkeypatch):
    original = _Lowerer._guard

    def missing(self, condition, goal, code, node=None):
        if code == 'ValueError':
            return
        return original(self, condition, goal, code, node)

    monkeypatch.setattr(_Lowerer, '_guard', missing)
    with pytest.raises(LoweringError, match='neither produced nor discharged'):
        lower_with_state_ranges(_source('pow(a, -1, 8)'), input_widths=[2])


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('wrong', [False, True])
def test_public_search_confirms_a_modular_condition_missed_by_baseline(tmp_path, wrong, record_property):
    import json

    from behave.sim import api

    from .test_baseline_public_sequence import _contract

    reference_source = "class M:\n def reset(self): pass\n def process(self,a): return {'y': a}\n"
    source = ("class M:\n def reset(self): pass\n def process(self,a):\n"
              "  value = pow((a << 1) | 1, -1, 256)\n"
              "  if value == 57:\n"
              f"   return {{'y': a ^ {int(wrong)}}}\n"
              "  return {'y': a}\n")
    contract = _contract()
    path = tmp_path / 'candidate.py'
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {'behavior.py': reference_source.encode()}, config={'class': 'M'})
    task = api.admit(contract, api.realization_of_behavior_source(source, contract, file=path.name),
                     reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, file=path.name), bounded_analysis=False)
    providers = tuple(p for p in handle.generation_providers(reference, workdir=tmp_path / 'queries')
                      if 'candidate:' in p.name)
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / 'command', reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=1), providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=60, debug=True), run_analysis=False))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and result.decision.passed is not wrong
    search, = result.generated.searches
    assert search.generated
    assert search.telemetry['baseline_coverage']['unhit']
    values = [row[0] for stimulus in search.generated for row in dict(stimulus.queues)['in']]
    assert any(pow((value << 1) | 1, -1, 256) == 57 for value in values)
    record_property('modular_search', json.dumps({
        'wrong': wrong, 'passed': result.decision.passed,
        'generated': len(search.generated), 'generated_inputs': values,
        'baseline_unhit': len(search.telemetry['baseline_coverage']['unhit']),
    }))


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('expression', ['pow(a, -1, 8)', 'pow(a, -1, 9)',
                                       'pow(1 << (a - 1), -1, 8)'])
def test_isolated_source_audit_distinguishes_pow_and_shift_errors(expression):
    from behave.sim.behavior.instance import BehaviorInstance

    source = _source(expression)
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=1)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=1)
    result = instance.query(instance.goals(), 1)
    assert not result.undecided
    checked = instance.source_audit(tuple(result.reachable.values()))
    assert checked.status.value == 'agreed', checked.as_dict()


@pytest.mark.parametrize('bits', [1, 2, 7, 32, 64, 127])
def test_inverse_lifting_has_no_machine_word_assumption(bits):
    source = _source(f'pow(a, -1, 1 << {bits})')
    proc = lower_with_state_ranges(source, input_widths=[bits])
    values = [0, 1, (1 << bits) - 1, 1 << (bits - 1), (1 << bits) // 3]
    for value in values:
        _, output, _, error = interp.run_step(proc, {'a': value})
        if value % 2 == 0:
            assert error == 'ValueError'
        else:
            assert not error
            assert output.transactions == [{'y': pow(value, -1, 1 << bits) & 255}]


@pytest.mark.parametrize('modulus', [3, 6, 15, 31, 63, 97, 144])
def test_euclidean_inverse_composites_and_noninvertible_inputs(modulus):
    from behave.sim.behavior import modular_power
    from behave.sim.symbolic import ir

    evaluator = interp._Interp({})

    def capture(expression):
        return ir.Lit(evaluator.value(expression))

    for base in range(-40, 41):
        value, invalid = modular_power.inverse(ir.Lit(base), modulus, capture)
        try:
            expected = pow(base, -1, modulus)
        except ValueError:
            assert evaluator.value(invalid), (base, modulus)
        else:
            assert not evaluator.value(invalid), (base, modulus)
            assert evaluator.value(value) == expected


@pytest.mark.isolation
@pytest.mark.solver
def test_wrong_ir_error_code_is_not_its_own_native_oracle(monkeypatch):
    from behave.sim.behavior.instance import BehaviorInstance

    original = _Lowerer._guard

    def wrong_code(self, condition, goal, code, node=None):
        return original(self, condition, goal, 'NegativeShift' if code == 'ValueError' else code, node)

    monkeypatch.setattr(_Lowerer, '_guard', wrong_code)
    source = _source('pow(a, -1, 8)')
    proc = lower_with_state_ranges(source, input_widths=[2], outputs=['y'], bound=1)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source, bound=1)
    result = instance.query(instance.goals(), 1)
    checked = instance.source_audit(tuple(result.reachable.values()))
    assert checked.status.value == 'inconsistent', checked.as_dict()
