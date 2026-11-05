"""Finite iterator products preserve source structure, outputs and goal events."""

import ast
import json

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import encoder, interp, ir, ranges
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.instance import lower
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _IterationPlan, _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from behave.sim.behavior.source_values import (
    SCALAR,
    ValueContract,
    contract_contains,
    contract_of_expression,
    contracts_for_target,
    join_contracts,
)
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
from behave.sim.engines.local_ir import local_hit_any
from behave.sim.engines.probing import Outcome

z3 = pytest.importorskip("z3")
pytestmark = pytest.mark.solver


def test_iteration_plan_keeps_the_complete_named_contract():
    count, value, element, available, binding = (object() for _ in range(5))
    plan = _IterationPlan(count, 4, value, element, available, binding)
    assert plan.count is count
    assert plan.bound == 4
    assert plan.value is value
    assert plan.element is element
    assert plan.available is available
    assert plan.binding is binding
    # A three-field positional view would silently omit the live iterator facts.
    with pytest.raises(TypeError):
        tuple(plan)


@pytest.mark.parametrize("count", [ir.Lit(0), ir.Lit(3), ir.Var("trips")])
def test_iteration_activation_without_an_availability_guard_preserves_count(count):
    plan = _IterationPlan(count, 4, None, SCALAR)
    assert plan.activation(count) is count


@pytest.mark.parametrize("available", [ir.Lit(False), ir.Lit(True), ir.Var("ready")])
def test_iteration_activation_checks_only_the_first_element(available):
    indices = []

    def availability(index):
        indices.append(index)
        return available

    count = ir.Var("trips")
    plan = _IterationPlan(count, 4, None, SCALAR, availability)
    assert ir.expressions_equal(
        plan.activation(count), ir.Op("ite", available, count, ir.Lit(0)))
    assert len(indices) == 1
    assert ir.expressions_equal(indices[0], ir.Lit(0))


def _source(body):
    return ("class M:\n    def reset(self):\n        pass\n"
            "    def process(self, a):\n" + body)


CASES = [
    """        total = 0
        for i, (x, y) in enumerate([(a, 2), (3, a + 1)], a & 1):
            if x > y:
                total += i + x
            else:
                total += y
        return {'y': total}
""",
    """        rows = [(i, i + a) for i in range(a)]
        return [{'y': k + x + y}
                for k, (x, y) in enumerate(rows, 2)]
""",
    """        out = []
        for i, (x, *tail) in enumerate([(a, 2, 3), (4, 5, a)], 2):
            out.append({'y': i + x + tail[0] + tail[1]})
        return out
""",
    """        total = 0
        for item in enumerate([(a, 2), (3, a)], 1):
            total += item[0] + item[1][0] + item[1][1]
        return {'y': total}
""",
    """        return [{'y': item[0] + item[1][0] + item[1][1]}
                for item in enumerate([(a, 2), (3, a)], 1)]
""",
    """        total = 0
        for i, (x, y) in enumerate([(1, 2), (3, 4)], a):
            total += i + x + y
            a = 0
        return {'y': total}
""",
    """        if a & 1:
            for item in enumerate([(1, 2), (3, 4)], 1):
                pass
        else:
            for item in enumerate([(5, 6), (7, 8)], 2):
                pass
        return {'y': item[0] + item[1][0] + item[1][1]}
""",
    """        for item in enumerate([(1, 2), (3, 4)], 1):
            pass
        item = a
        return {'y': item}
""",
    """        if a & 1:
            for i in range(2):
                pass
        else:
            for i in range(3):
                pass
        return {'y': i}
""",
    """        for item in enumerate([(1, 2), (3, 4)], 1):
            if a & 1:
                break
            continue
        return {'y': item[0] + item[1][0] + item[1][1]}
""",
    """        total = 0
        for i, (x, y) in enumerate([(a, 2), (a, 4)]):
            total += x + y
            a = 0
        return {'y': total}
""",
    """        total = 0
        for i in range(a, a + 2):
            total += i
            a = 0
        return {'y': total}
""",
    """        total = 0
        for i in reversed(range(a, a + 2)):
            total += i
            a = 0
        return {'y': total}
""",
    """        total = 0
        step = (a & 1) + 1
        for i in range(0, 4, step):
            total += i
            step = 0
        return {'y': total}
""",
    """        total = 0
        step = -((a & 1) + 1)
        for i in range(4, 0, step):
            total += i
            step = 0
        return {'y': total}
""",
    """        rows = [(1, a), (3, 4)]
        total = 0
        for i, (x, y) in enumerate(rows):
            total += x + y
            rows = [(5, 6), (7, 8)]
        return {'y': total}
""",
    """        rows = [1, 2]
        total = 0
        for x in rows:
            total += x
            if a & 1:
                rows = [5, 6]
        return {'y': total}
""",
    """        rows = [a, 2]
        total = 0
        for x in reversed(rows):
            total += x
            rows = [5, 6]
        return {'y': total}
""",
    """        rows = [1, 2]
        total = 0
        for x in rows:
            for y in rows:
                total += x + y
                rows = [5, 6]
        return {'y': total}
""",
    """        rows = [a, 2]
        total = 0
        for x in rows:
            total += x
            rows[1] = 3
        return {'y': total}
""",
    """        rows = [a, 2]
        total = 0
        for x in rows:
            total += x
            rows = rows
            rows[1] = 3
        return {'y': total}
""",
]

LIVE_LIST_CASES = [
    """        rows = [a, 2, 3]
        for x in rows:
            rows.pop()
        return {'y': x}
""",
    """        rows = [a, 2, 3, 4]
        for x in reversed(rows):
            rows.pop()
            rows.pop()
        return {'y': x}
""",
    """        rows = [a, 1, 2, 3]
        total = 0
        for x in rows:
            if x == 2:
                total += 10
            rows.pop(0)
        return {'y': total}
""",
    """        rows = [a, 2, 3]
        total = 0
        for x in rows:
            total += x
            rows.pop()
        return {'y': total + len(rows) * 10}
""",
    """        rows = [a, 2, 3]
        total = 0
        for x in rows:
            total += x
            rows.pop(0)
        return {'y': total + len(rows) * 10}
""",
    """        rows = [a, 2, 3, 4]
        total = 0
        for i, x in enumerate(rows, 2):
            total += i + x
            rows.pop()
        else:
            total += 16
        return {'y': total}
""",
    """        rows = [a, 2, 3, 4]
        total = 0
        for x in reversed(rows):
            total += x
            rows.pop()
        return {'y': total}
""",
    """        rows = [a, 2, 3, 4]
        total = 0
        for x in reversed(rows):
            total += x
            rows.pop()
            rows.pop()
        else:
            total += 16
        return {'y': total + len(rows)}
""",
    """        rows = [a, 2, 3, 4]
        total = 0
        for x in rows:
            rows.pop()
            if x == a:
                continue
            total += x
        else:
            total += 16
        return {'y': total}
""",
    """        rows = [a, 2, 3, 4]
        total = 0
        for x in rows:
            rows.pop()
            if a & 1:
                break
            total += x
        else:
            total += 16
        return {'y': total + len(rows)}
""",
    """        rows = [a, 2, 3, 4]
        total = 0
        for x in rows:
            for y in rows:
                total += x + y
                rows.pop()
        return {'y': total + len(rows)}
""",
]
GROWING_LIST_CASES = [
    """        rows = [1, 2]
        out = []
        for x in rows:
            for j in range(x):
                out.append(j)
        return {'y': sum(out)}
""",
    """        rows = [1, 1]
        total = 0
        for x in rows:
            total += x
            if x == 2:
                break
            rows[1] = 2
        return {'y': total}
""",
    """        rows = [1]
        total = 0
        for x in rows:
            total += x
            if x == 2:
                break
            rows.append(2)
        return {'y': total}
""",
    """        rows = [a]
        total = 0
        for i, x in enumerate(rows):
            total += x
            if i == 0:
                rows.append(3)
            else:
                break
        return {'y': total}
""",
    """        rows = [0]
        total = 0
        for x in rows:
            total += x
            if len(rows) == 1:
                rows.append(a)
        else:
            total += 10
        return {'y': total}
""",
    """        rows = [1, 2]
        total = 0
        for x in reversed(rows):
            total += x
            rows.append(3)
        return {'y': total}
""",
]

CASES += LIVE_LIST_CASES + GROWING_LIST_CASES
CASES += [GROWING_LIST_CASES[0].replace("for x in rows:", header)
          for header in ("for x in reversed(rows):",
                         "for i, x in enumerate(rows):")]


# Source slices copy the selected values; omitted reverse endpoints are not
# explicit -1, and a variable logical length is not the backing capacity.
CASES += [
    f"""        rows = {rows}
        selected = rows[{selection}]
        return [{{'y': x}} for x in selected]
"""
    for rows, selection in (
        ("[1, 2, 3, 4]", "::2"),
        ("[1, 2, 3, 4]", "::-1"),
        ("[1, 2, 3, 4]", ":-1:-1"),
        ("[1, 2, 3, 4]", "-1::-2"),
        ("[1, 2, 3, 4]", "-20:20:3"),
        ("[1, 2, 3, 4]", "20:-20:-3"),
        ("[1, 2, 3, 4]", "a::2"),
        ("[1, 2, 3, 4]", "a-5:a+5:2"),
        ("[1, 2, 3, 4]", "a+5:a-5:-2"),
        ("[i for i in range(a)]", "::2"),
        ("[i for i in range(a)]", "::-1"),
        ("[i for i in range(a)]", ":-1:-1"),
        ("[i for i in range(a)]", "a-5:a+5:2"),
        ("[i for i in range(a)]", "a+5:a-5:-2"),
    )
]
CASES += [
    """        rows = [(a, 2), (3, 4), (1, a)]
        selected = rows[::-2]
        return [{'y': x + y} for x, y in selected]
""",
    """        rows = [a, 2, 3, 4]
        selected = rows[::-1]
        rows[0] = 7
        return [{'y': x} for x in selected]
""",
    """        rows = [a, 2, 3, 4]
        total = 0
        for offset in range(2):
            selected = rows[offset::2]
            for x in selected:
                total += x
        return {'y': total}
""",
    """        total = 0
        for x in range(3):
            x += a
            total += x
        return {'y': total + x}
""",
    """        total = 0
        for x, y in [(a, 2), (3, a + 1)]:
            x = (x << 2) >> 1
            y -= a
            if x > 4:
                total += x + y
        return {'y': total}
""",
    """        total = 0
        for x in range(3):
            for x in range(a):
                x += 4
            total += x
        return {'y': total}
""",
    """        total = 0
        for x in range(3):
            x, other = (a + x, 2)
            if x > 2:
                break
            total += other
        else:
            total += 8
        return {'y': total + x}
""",
    """        total = 0
        for x in range(3):
            x += a
            if x & 1:
                continue
            total += x
        return {'y': total + x}
""",
    """        rows = [a, 2, 3]
        total = 0
        for x in rows:
            x = 9
            total += x
        return {'y': total + rows[0]}
""",
    """        total = 0
        for x in range(2):
            if x == 0:
                x = 7
            if x == 7:
                total += 10
        return {'y': total + x}
""",
    """        row = []
        for i in range(a):
            row.append(i)
        row = []
        for i in range(2):
            row.append(i + a)
        rows = []
        rows.append(row)
        return {'y': rows[0][0] + rows[0][1]}
""",
    """        row = []
        for i in range(4):
            row.append(i)
        row = []
        for i in range(a):
            row.append(i + 1)
        rows = []
        rows.append(row)
        return {'y': len(rows[0]) * 10 + sum(rows[0])}
""",
    """        row = []
        for i in range(a):
            row.append(i)
        row = []
        rows = []
        rows.append(row)
        return {'y': len(rows[0])}
""",
    """        row = []
        for i in range(a):
            row.append((i, i + a))
        row = []
        for i in range(2):
            row.append((i, i + a))
        rows = []
        rows.append(row)
        return {'y': rows[0][0][1] + rows[0][1][1]}
""",
    """        row = []
        for i in range(a):
            row.append((i, i + a))
        row = []
        rows = []
        rows.append(row)
        return {'y': len(rows[0])}
""",
]


@pytest.mark.parametrize("body", CASES)
@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_iterator_products_match_native_outputs_and_every_goal(body, lowerer):
    _assert_outputs_and_every_goal(_source(body), lowerer)


def _assert_outputs_and_every_goal(source, lowerer):
    proc = lower_with_state_ranges(source, input_widths=[2], lowerer=lowerer)
    env = {**proc.state_ranges, "a": ranges.Range(0, 3)}
    planned = plan_source(source_model(source), source)
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=planned)
    model = namespace["M"]()
    pre = {name: encoder.encode_concrete_value(
        proc.initial[name], env[name], name, kind=proc.types[name])
        for name in proc.state}
    enc = encoder.encode_step(proc, pre=pre, env=env)
    solver = z3.Solver()
    solver.add(enc.constraint)
    for a in range(4):
        model.reset()
        native_hits.clear()
        output = model.process(a)
        expected = output if isinstance(output, list) else [output]
        _, got, hits, error = interp.run_step(proc, {"a": a})
        assert not error
        assert got.transactions == expected
        assert hits == set(native_hits)
        solver.push()
        solver.add(enc.pre["a"] == a)
        assert solver.check() == z3.sat
        differences = [z3.Not(enc.ok), enc.post["#count"] != len(expected)]
        differences.extend(z3.BV2Int(enc.post["y"][i], is_signed=True) != row["y"]
                           for i, row in enumerate(expected))
        differences.extend(term != (goal in hits) for goal, term in enc.hits.items())
        solver.add(z3.Or(*differences))
        assert solver.check() == z3.unsat
        solver.pop()


@pytest.mark.parametrize("body", CASES)
def test_public_iterator_goals_have_exact_queries_and_native_witnesses(body):
    _assert_public_goal_queries(_source(body))


def test_enumerate_leaf_hull_includes_the_start_index():
    source = _source("""        rows = [1, 2]
        out = []
        for i, x in enumerate(rows, a + 5):
            if i >= 5:
                for j in range(x):
                    out.append(i + j)
        return {'y': sum(out)}
""")
    for lowerer in (GraphLowerer, _Lowerer):
        _assert_outputs_and_every_goal(source, lowerer)
    _assert_public_goal_queries(source)


def _assert_public_goal_queries(source):
    schema = TransactionSchema(
        [Endpoint("a", "input", 2), Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    exact = lower(source, schema, bound=1)
    planned = plan_source(source_model(source), source)
    assert exact.frontend_catalogue_manifest() == planned.manifest
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=planned)
    model = namespace["M"]()
    possible = set()
    for value in range(4):
        model.reset()
        native_hits.clear()
        model.process(value)
        possible.update(native_hits)
    for goal in planned.manifest:
        answer = local_hit_any(exact.proc, exact.env, exact.proc.initial, (goal,),
            timeout_ms=60_000)
        expected = Outcome.SAT if goal in possible else Outcome.LOCAL_UNSAT
        assert answer.outcome is expected, (goal, answer.detail)
        if answer.outcome is Outcome.SAT:
            model.reset()
            native_hits.clear()
            model.process(**answer.candidate)
            assert goal in native_hits


SLICE_REBIND = """class M:
    def reset(self):
        self.rows = [1, 2, 3, 4]
    def replace(self):
        self.rows = [5, 6, 7, 8]
        return 1
    def process(self, a):
        selected = self.rows[self.replace()::2]
        return [{'y': x} for x in selected]
"""


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_slice_primary_is_evaluated_before_effectful_bounds(lowerer):
    _assert_outputs_and_every_goal(SLICE_REBIND, lowerer)


def test_slice_bound_effects_have_native_confirmed_local_queries():
    _assert_public_goal_queries(SLICE_REBIND)


ITERATOR_ARGUMENT_EFFECT = """class M:
    def reset(self):
        self.rows = [7]
    def start(self):
        self.rows.pop()
        return 0
    def process(self, a):
        total = 0
        for i, x in enumerate(self.rows, self.start()):
            total += x
        else:
            total += 2
        return {'y': total}
"""


@pytest.mark.parametrize("lowerer", (GraphLowerer, _Lowerer))
def test_iterator_argument_alias_remains_an_explicit_representation_boundary(lowerer):
    # This needs a held reference across argument evaluation, beyond the
    # live-length loop rule. The existing value-array alias check must not
    # silently hand an inexact procedure to the query engine.
    with pytest.raises(LoweringError) as failure:
        lower_with_state_ranges(
            ITERATOR_ARGUMENT_EFFECT, input_widths=[2], lowerer=lowerer)
    assert failure.value.code == "array-assigned-by-reference"


def test_public_iterator_argument_alias_is_not_reported_as_local_unsat():
    plan = plan_source(source_model(ITERATOR_ARGUMENT_EFFECT), ITERATOR_ARGUMENT_EFFECT)
    namespace, _, visits = instrumented.load_source(
        ITERATOR_ARGUMENT_EFFECT, goal_plan=plan)
    model = namespace["M"]()
    model.reset()
    assert model.process(0) == {'y': 2}
    assert visits
    schema = TransactionSchema(
        [Endpoint("a", "input", 2), Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    with pytest.raises(LoweringError) as failure:
        lower(ITERATOR_ARGUMENT_EFFECT, schema, bound=1)
    assert failure.value.code == "array-assigned-by-reference"


def test_public_pop_safety_has_native_failure_witnesses():
    source = _source("""        values = [7]
        return {'y': values.pop(a)}
""")
    schema = TransactionSchema(
        [Endpoint("a", "input", 2), Endpoint("y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    exact = lower(source, schema, bound=1)
    plan = plan_source(source_model(source), source)
    assert exact.frontend_catalogue_manifest() == plan.manifest
    namespace, _, visits = instrumented.load_source(source, goal_plan=plan)
    model = namespace["M"]()

    def native(inputs):
        model.reset()
        visits.clear()
        try:
            model.process(**inputs)
        except IndexError as error:
            failure = instrumented.attribute_safety_failure(error, plan.safety_sites)
            assert failure is not None
            visits.append(failure)
        return set(visits)

    possible = set().union(*(native({'a': value}) for value in range(4)))
    assert any(goal.startswith('safety:') for goal in possible)
    for goal in plan.manifest:
        answer = local_hit_any(exact.proc, exact.env, exact.proc.initial, (goal,),
            timeout_ms=60_000)
        expected = Outcome.SAT if goal in possible else Outcome.LOCAL_UNSAT
        assert answer.outcome is expected, (goal, answer.detail)
        if answer.outcome is Outcome.SAT:
            assert goal in native(answer.candidate)


@pytest.mark.isolation
@pytest.mark.parametrize('body, kind', [
    (('        values = [a]\n'
      '        return {"y": values.pop(1 if a == 7 else 0)}'), 'indexerror'),
    (('        rows = [0, a]\n'
      '        for i, x in enumerate(rows):\n'
      '            rows = [0, 0]\n'
      '            rows[0] = a\n'
      '            if i == 1:\n'
      '                assert x != 7\n'
      '        return {"y": a}'), 'assertionerror'),
], ids=['pop-indexerror', 'held-object-assertion'])
def test_public_search_replays_a_baseline_missed_failure(tmp_path, body, kind):
    from behave.sim import api

    from .test_baseline_public_sequence import REFERENCE, _contract

    source = REFERENCE.replace('        return {"y": a}', body)
    contract = _contract()
    path = tmp_path / "candidate.py"
    path.write_text(source)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": REFERENCE.encode()}, config={"class": "M"})
    task = api.admit(contract, api.realization_of_behavior_source(
        source, contract, file="candidate.py"), reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(
        path, file="candidate.py", timeout_s=90), bounded_analysis=False)
    providers = tuple(provider for provider in handle.generation_providers(
        reference, workdir=tmp_path / "guidance") if "candidate:" in provider.name)
    assert len(providers) == 1
    request = handle.command_request(
        run_directory=tmp_path / "command", reference=reference,
        baseline=api.BaselinePolicy(case_limit=1, max_queue_depth=100),
        providers=providers,
        search_policy=api.SearchPolicy(time_budget_s=120, max_probes=4, debug=True),
        run_analysis=False)
    result = api.handle_command(request)
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED
    assert result.decision.authoritative and not result.decision.passed
    assert not result.evaluation.no_result_failures
    failures = {goal for goal in result.evaluation.catalogue
                if goal.endswith(':' + kind)}
    assert len(failures) == 1
    assert failures <= set(result.evaluation.hits)
    baseline_ids = {stimulus.identity_bytes for stimulus in request.baseline.stimuli}
    for run in result.evaluation.results:
        if run.stimulus.identity_bytes in baseline_ids:
            assert not (failures & {goal for record in run.trace.records
                                   for goal, hit in record.hits if hit})
    assert any(run.stimulus.identity_bytes not in baseline_ids
               for run in result.evaluation.results)
    assert 0 < sum(search.telemetry["probes"]
                   for search in result.generated.searches) <= 4


@pytest.mark.parametrize("name", ("pop", "append"))
def test_model_helper_named_like_a_list_method_is_not_a_list_operation(name):
    source = """class M:
    def reset(self):
        pass
    def pop(self, x):
        return x + 1
    def process(self, a):
        return {'y': self.pop(a)}
"""
    source = source.replace("pop", name)
    plan = plan_source(source_model(source), source)
    assert not plan.safety_sites
    _assert_public_goal_queries(source)


def test_positional_contract_join_and_unpack_are_componentwise():
    row = ValueContract.sequence("tuple", 2, 2, SCALAR)
    small = ValueContract.positional("tuple", (SCALAR, row))
    large = ValueContract.positional("tuple", (
        SCALAR, ValueContract.sequence("tuple", 2, 3, SCALAR)))
    joined = join_contracts(small, large)
    assert contract_contains(joined, small)
    assert contract_contains(joined, large)
    assert not contract_contains(small, large)
    target = ast.parse("i, (x, y) = value").body[0].targets[0]
    assert contracts_for_target(target, small) == {"i": SCALAR, "x": SCALAR, "y": SCALAR}
    assert contracts_for_target(target, large) is None
    for expression, expected in (("item[0]", SCALAR), ("item[1]", row),
                                 ("item[1][0]", SCALAR)):
        assert contract_of_expression(
            ast.parse(expression, mode="eval").body,
            resolve=lambda node, _env: small if node.id == "item" else SCALAR) == expected


def test_empty_sequence_contracts_do_not_observe_unused_backing_elements():
    empty = ValueContract.sequence("list", 0, 0)
    typed = ValueContract.sequence("list", 0, 0, SCALAR)
    nested = ValueContract.sequence(
        "list", 0, 0, ValueContract.sequence("tuple", 2, 2, SCALAR))
    for value in (empty, typed, nested):
        assert contract_contains(empty, value)
        assert contract_contains(value, empty)
    assert not contract_contains(empty, ValueContract.sequence("list", 1, 1, SCALAR))
    assert not contract_contains(empty, ValueContract.sequence("tuple", 0, 0))
    assert not contract_contains(ValueContract.sequence("list", 1, 1, SCALAR), typed)


@pytest.mark.parametrize("body, code", [
    ("""        rows = [i for i in range(a)]
        for x in rows:
            rows.pop()
        return {'y': x}
""", "variable-may-be-unassigned"),
    ("""        for i in range(a):
            pass
        return {'y': i}
""", "variable-may-be-unassigned"),
    ("""        if a & 1:
            for item in enumerate([(1, 2)], 1):
                pass
        return {'y': item[0] + item[1][0]}
""", "subscript-length-not-static"),
])
def test_loop_bindings_cannot_invent_a_definition_on_an_empty_path(body, code):
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(_source(body), input_widths=[2])
    assert caught.value.code == code


@pytest.mark.parametrize("body, code", [
    ("""        rows = [[1, 2], [3, 4]]
        for i, row in enumerate(rows):
            row[0] = a
        return {'y': rows[0][0]}
""", "loop-index-assignment"),
    ("""        rows = [[1, 2], [3, 4]]
        for item in enumerate(rows):
            item[1][0] = a
        return {'y': rows[0][0]}
""", "subscript-length-not-static"),
])
def test_iterator_products_do_not_silently_copy_a_mutated_alias(body, code):
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(_source(body), input_widths=[2])
    assert caught.value.code == code
