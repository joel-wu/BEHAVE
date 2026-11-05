"""The Behavior frontend has one catalogue from lowering through the report."""

from __future__ import annotations

import ast
import inspect
import json
from collections import Counter
from pathlib import Path

import pytest

import behave.sim.command as command_module
from behave.sim import api
from behave.sim.behavior import fp_lib
from behave.sim.symbolic import ir
from behave.sim.behavior import goals as source_goals
from behave.sim.behavior.catalogue import (
    frontend_catalogue,
    source_frontend_catalogue,
)
from behave.sim.behavior.extract import source_model
from behave.sim.behavior.instance import lower
from behave.sim.behavior.lowering import _Lowerer
from behave.sim.core.catalogue import CatalogueManifest, Goal
from behave.sim.core.driver import run_in_memory
from behave.sim.core.refusal import Refused
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema
from behave.sim.core.stimulus import AuditedTrace, SlotRecord
from behave.sim.engines.behavior.generation import (
    AcceptedTraceOccurrences,
    BehaviorExactProvider,
    BehaviorOccurrence,
)
from behave.sim.generation import ProbeRequest
from behave.sim.generation_cache import GenerationCacheTelemetry

from .test_api_path import Doubling, _ref, contract, realization, schedule
from .test_command_handler import _BaselineOnly

SOURCE = """\
class M:
    def reset(self):
        pass

    def helper(self, in_a):
        if in_a:
            return 8 // in_a
        return 0

    def unused(self, in_a):
        if in_a:
            return 99
        return 98

    def process(self, in_a):
        return {"out_y": self.helper(in_a)}
"""


COMMAND_SOURCE = """\
class M:
    def reset(self):
        pass

    def process(self, in_a):
        if in_a & 1:
            marker = 1
        else:
            marker = 0
        return {"out_y": (in_a * 2 + marker - marker) & 255}
"""


REPEATED_HELPER_SOURCE = """\
class M:
    def reset(self):
        pass

    def helper(self, in_a):
        if in_a:
            return in_a + 1
        return 0

    def process(self, in_a):
        left = self.helper(in_a)
        right = self.helper(in_a)
        return {"out_y": (left + right) & 255}
"""


def _schema():
    return TransactionSchema(
        [Endpoint("in_a", "input", 8), Endpoint("out_y", "output", 8)],
        authority=Authority.TASK, reading=Reading.ELABORATED)


def _replace_manifest(manifest, *, omit=(), rename=None):
    omit = set(omit)
    goals = []
    replacement = {}
    for goal_id, goal in manifest.items():
        if goal_id in omit:
            continue
        new_id = rename[1] if rename is not None and goal_id == rename[0] else goal_id
        replacement[goal_id] = new_id
        goals.append(Goal(
            new_id, goal.cls, family=goal.family, provider=goal.provider,
            event=goal.event, provenance=dict(goal.schema.provenance),
            policy=goal.policy, evidence=goal.evidence))
    occurrences = [
        (key, replacement[goal_id])
        for key, goal_id in manifest.occurrences
        if goal_id in replacement
    ]
    return CatalogueManifest(goals, occurrences=occurrences)


def test_one_source_goal_has_one_range_independent_public_occurrence():
    source = """\
class M:
    def process(self, a):
        if a:
            return {"y": 1}
        return {"y": 0}
"""
    site = next(site for site in source_goals.enumerate_sites(source, "repeat.py")
                if site.family == source_goals.DECISION)
    reached = site.goal(True).id
    unreachable = site.goal(False).id
    proc = ir.Procedure(
        (), ("a",), (), (),
        ir.Seq(ir.Mark(reached), ir.Mark(reached)),
        impossible={unreachable: "the synthetic fixture fixes the other arm"},
    )

    manifest = frontend_catalogue(proc, source, "repeat.py")
    reached_occurrences = tuple(
        key for key, goal in manifest.occurrences if goal == reached)
    unreachable_occurrences = tuple(
        key for key, goal in manifest.occurrences if goal == unreachable)

    assert len(reached_occurrences) == 1
    assert len(unreachable_occurrences) == 1
    assert reached_occurrences[0].path[-1] == "source"
    assert unreachable_occurrences[0].path[-1] == "source"
    assert frontend_catalogue(proc, source, "repeat.py") == manifest

    missing_one_copy = tuple(
        pair for pair in manifest.occurrences
        if pair[0] != reached_occurrences[0])
    with pytest.raises(Refused, match="catalogue-lowered-missing-occurrence"):
        manifest.reconcile_occurrences(
            missing_one_copy, "lowered", exhaustive=True)


def test_a_source_coverage_occurrence_cannot_be_reclassified_as_a_failure():
    source = """\
class M:
    def process(self, a):
        if a:
            return {"y": 1}
        return {"y": 0}
"""
    site = next(site for site in source_goals.enumerate_sites(source, "class.py")
                if site.family == source_goals.DECISION)
    goal = site.goal(True).id
    proc = ir.Procedure(
        (), ("a",), (), (), ir.Fail("Stopped", goal))

    with pytest.raises(
            Refused, match="behavior-catalogue-goal-class-conflict"):
        frontend_catalogue(proc, source, "class.py")


def test_repeated_helper_inlining_retains_source_goal_identity():
    behavior = lower(REPEATED_HELPER_SOURCE, _schema(), bound=1)
    manifest = behavior.frontend_catalogue_manifest()
    source_manifest = source_frontend_catalogue(
        source_model(REPEATED_HELPER_SOURCE), REPEATED_HELPER_SOURCE,
        "behavior.py")
    helper_goals = tuple(
        goal for goal in manifest
        if manifest[goal].where("line") == 6
        and manifest[goal].family == source_goals.DECISION)

    assert len(helper_goals) == 2
    for goal in helper_goals:
        occurrences = tuple(
            key for key, owner in manifest.occurrences if owner == goal)
        assert len(occurrences) == 1
        assert occurrences[0].path[-1] == "source"
    assert manifest == source_manifest

    deleted = tuple(
        pair for pair in manifest.occurrences
        if pair[0] != next(
            key for key, owner in manifest.occurrences
            if owner == helper_goals[0]))
    with pytest.raises(Refused, match="catalogue-lowered-missing-occurrence"):
        manifest.reconcile_occurrences(deleted, "lowered", exhaustive=True)


def test_false_goal_terms_reuse_one_source_suffix_analysis():
    """Inlining one helper repeatedly does not reanalyse its source suffix."""

    source = """\
class M:
    def process(self, a):
        return {"y": 0}
        if a:
            return {"y": 1}
        return {"y": 2}
"""
    tree = ast.parse(source)
    function = tree.body[0].body[0]
    suffix = function.body[1:]
    lowerer = _Lowerer(source_goals.enumerate_sites(source, "behavior.py"))

    first = lowerer._false_goal_terms(suffix)
    second = lowerer._false_goal_terms(tuple(suffix))

    assert second is first
    assert ir.goals_of(first)


def test_repeated_pruned_helper_suffix_has_one_false_term_per_source_goal():
    source = """\
class M:
    def reset(self):
        pass

    def helper(self, value):
        return value
        if value:
            return 1
        return 0

    def process(self, in_a):
        left = self.helper(in_a)
        right = self.helper(in_a)
        return {"out_y": (left + right) & 255}
"""

    behavior = lower(source, _schema(), bound=1)
    manifest = behavior.frontend_catalogue_manifest()
    pruned = {
        goal for goal, declaration in manifest.items()
        if declaration.where("line") in {7, 8, 9}
    }
    counts = Counter(
        command.goal for command in ir.walk(behavior.proc.body)
        if isinstance(command, ir.Mark) and command.goal in pruned
    )

    assert pruned
    assert counts == {goal: 1 for goal in pruned}


def test_a_pruned_call_retains_its_helper_goal_closure_as_false():
    source = """\
class M:
    def reset(self):
        pass

    def helper(self, value):
        if value:
            return 8 // value
        return 0

    def process(self, in_a):
        if in_a < 0:
            result = self.helper(in_a)
        else:
            result = 0
        return {"out_y": result}
"""

    behavior = lower(source, _schema(), bound=1)
    manifest = behavior.frontend_catalogue_manifest()
    frozen = source_frontend_catalogue(
        source_model(source), source, "behavior.py")
    helper_goals = {
        goal for goal, declaration in manifest.items()
        if declaration.where("line") in {6, 7}
    }

    assert manifest == frozen
    assert len(helper_goals) == 5
    safety = {goal for goal in helper_goals if goal.startswith("safety:")}
    structural = helper_goals - safety
    assert safety <= set(behavior.proc.discharged)
    assert structural <= ir.goals_of(behavior.proc.body)


def test_lowering_freezes_selected_reachable_structural_and_safety_occurrences():
    behavior = lower(SOURCE, _schema(), bound=1)
    manifest = behavior.frontend_catalogue_manifest()

    assert type(manifest) is CatalogueManifest
    assert behavior.goals() is manifest
    assert CatalogueManifest.from_dict(manifest.as_dict()) == manifest
    assert len(manifest) == 7
    assert {goal.cls for goal in manifest.values()} == {"coverage", "safety"}
    assert any(goal.startswith("safety:") for goal in manifest)
    assert {goal.where("line") for goal in manifest.values()} == {6, 7, 8, 16}
    assert len(manifest.occurrences) == len(manifest)
    assert not any(goal.where("line") == 10 for goal in manifest.values()), \
        "an uncalled helper entered the selected model's denominator"


def test_concrete_catalogue_uses_the_selected_source_call_closure_without_lowering():
    manifest = source_frontend_catalogue(
        source_model(SOURCE), SOURCE, "behavior.py")
    exact = lower(SOURCE, _schema(), bound=1).frontend_catalogue_manifest()

    assert type(manifest) is CatalogueManifest
    assert len(manifest) == 7
    assert {goal.cls for goal in manifest.values()} == {"coverage", "safety"}
    assert {goal.where("line") for goal in manifest.values()} == {6, 7, 8, 16}
    assert not any(goal.where("line") == 10 for goal in manifest.values())
    assert manifest == exact, {
        goal: manifest[goal].conflict_with(exact[goal])
        for goal in set(manifest) & set(exact)
        if manifest[goal] != exact[goal]
    }


def test_source_catalogue_includes_safety_sites_from_reachable_fp_library_code():
    source = """\
from fp_lib import fp32_add

class M:
    def reset(self):
        pass
    def process(self, a, b):
        return {"y": fp32_add(a, b)}
"""
    schema = TransactionSchema(
        [Endpoint("a", "input", 32), Endpoint("b", "input", 32),
         Endpoint("y", "output", 32)],
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )

    manifest = source_frontend_catalogue(
        source_model(source), source, "behavior.py")
    exact = lower(source, schema, bound=1).frontend_catalogue_manifest()

    assert manifest == exact, {
        goal: manifest[goal].conflict_with(exact[goal])
        for goal in set(manifest) & set(exact)
        if manifest[goal] != exact[goal]
    }
    assert any(
        goal.cls == "safety" and goal.family == "negativeshift"
        for goal in manifest.values())


@pytest.mark.isolation
@pytest.mark.solver
def test_sorted_key_safety_is_declared_lowered_and_attributed_on_replay():
    from behave.sim.behavior.instrumented import attribute_safety_failure
    from behave.sim.symbolic.interp import run_step
    from behave.sim.behavior.source_plan import plan_source

    source = """\
class M:
    def reset(self):
        pass
    def process(self, in_a):
        values = [5, 9]
        indices = [in_a & 3, 0]
        ordered = sorted(indices, key=lambda i: values[i])
        return {'out_y': ordered[0]}
"""
    model = source_model(source)
    manifest = source_frontend_catalogue(model, source)
    site = next(node for node in ast.walk(model.tree)
                if isinstance(node, ast.Lambda)).body
    from behave.sim.behavior.declared import safety_goal_id
    key_goal = safety_goal_id("behavior.py", site, "indexerror")
    assert key_goal in manifest
    exact = lower(source, _schema(), bound=1)
    assert exact.frontend_catalogue_manifest() == manifest

    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102 - fixed oracle fixture
    native = namespace["M"]()
    assert native.process(1) == {"out_y": 0}
    with pytest.raises(IndexError) as caught:
        native.process(2)
    assert attribute_safety_failure(
        caught.value, plan_source(model, source).safety_sites) == key_goal
    _state, _outputs, hits, error = run_step(exact.proc, {"in_a": 2})
    assert error == "IndexError"
    assert key_goal in hits

    from behave.sim.behavior.isolated import isolated_model
    factory = isolated_model(source)
    try:
        model = factory()
        with pytest.raises(IndexError):
            model.process(in_a=2)
        assert key_goal in factory.hits
    finally:
        factory.close()


def test_uncalled_and_shadowed_lambda_bodies_do_not_add_safety_goals():
    source = """\
def sorted(items, key):
    return items
class M:
    def reset(self):
        pass
    def process(self, in_a):
        unused = lambda x: 1 // x
        ignored = sorted([0], key=lambda x: 2 // x)
        return {'out_y': in_a}
"""
    manifest = source_frontend_catalogue(source_model(source), source)
    assert not any(goal.cls == "safety" for goal in manifest.values())


@pytest.mark.solver
@pytest.mark.parametrize("items", ["[in_a, 1]", "[]"])
def test_sorted_key_helper_closure_has_one_source_catalogue(items):
    source = f"""\
def key_of(i):
    return 1 // i
class M:
    def reset(self):
        pass
    def process(self, in_a):
        ordered = sorted({items}, key=lambda i: key_of(i))
        return {{'out_y': in_a}}
"""
    manifest = source_frontend_catalogue(source_model(source), source)
    assert any(goal.family == "divzero" for goal in manifest.values())
    exact = lower(source, _schema(), bound=1)
    assert exact.frontend_catalogue_manifest() == manifest


def _fp_field_shift_line():
    source, start = inspect.getsourcelines(fp_lib._fp32_fields)
    shift = next(node for node in ast.walk(ast.parse("".join(source)))
                 if isinstance(node, ast.BinOp) and isinstance(node.op, ast.RShift))
    return start + shift.lineno - 1


def test_model_and_library_safety_sites_with_one_position_do_not_alias():
    library_line = _fp_field_shift_line()
    lines = [
        "from fp_lib import fp32_is_nan",
        "",
        "class M:",
        "    def reset(self):",
        "        pass",
        "    def process(self, a, b):",
    ]
    lines.extend("" for _ in range(library_line - 1 - len(lines)))
    lines.extend((
        "        y = a >> b",
        "        ignored = fp32_is_nan(a)",
        "        return {'y': y}",
    ))
    source = "\n".join(lines) + "\n"
    schema = TransactionSchema(
        [Endpoint("a", "input", 32), Endpoint("b", "input", 1),
         Endpoint("y", "output", 32)],
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )

    manifest = source_frontend_catalogue(
        source_model(source), source, "behavior.py")
    exact = lower(source, schema, bound=1).frontend_catalogue_manifest()
    coincident = [
        goal for goal in manifest.values()
        if goal.cls == "safety"
        and goal.family == "negativeshift"
        and goal.where("line") == library_line
        and goal.where("col") == 12
    ]

    assert manifest == exact
    assert {goal.where("file") for goal in coincident} \
        == {"behavior.py", "fp_lib.py"}
    assert len({goal.id for goal in coincident}) == 2


def test_inlined_library_positions_do_not_emit_model_coverage_goals():
    """Line/column equality across files is not source-site identity.

    ``fp32_is_nan`` reaches a field assignment while the model helper below is
    skipped. The model deliberately has an assignment at the same line/column;
    an index that omits the file makes the library assignment report that model
    statement as covered even though the source did not execute it.
    """
    library_line = _fp_field_shift_line()
    lines = [
        "from fp_lib import fp32_is_nan",
        "",
        "def model_helper(a):",
        "    if a == 0:",
        "        return 0",
    ]
    lines.extend("" for _ in range(library_line - 1 - len(lines)))
    lines.extend((
        "    value = 1",
        "    return value",
        "",
        "class M:",
        "    def reset(self):",
        "        pass",
        "    def process(self, a):",
        "        lib = fp32_is_nan(a)",
        "        if a == 0:",
        "            return {'y': int(lib)}",
        "        return {'y': model_helper(a)}",
    ))
    source = "\n".join(lines) + "\n"
    schema = TransactionSchema(
        [Endpoint("a", "input", 32), Endpoint("y", "output", 32)],
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )

    from behave.sim.behavior import audit, instrumented

    behavior = lower(source, schema, bound=1)
    namespace, _sites, hits = instrumented.load_source(source)

    assert audit.model_audit(
        behavior.proc, namespace["M"], [[{"a": 0}]], hits) == []


def test_discharged_fp_library_sites_keep_the_source_owned_occurrences():
    source = """\
from fp_lib import fp32_add, fp32_mul

class M:
    def reset(self):
        self.running_sum = 0
        self.active = 0
    def process(self, a, b, last):
        product = fp32_mul(a, b)
        result = product if self.active == 0 else fp32_add(
            self.running_sum, product)
        self.running_sum = result
        self.active = 1
        if last:
            self.running_sum = 0
            self.active = 0
            return {"y": result}
        return None
"""
    schema = TransactionSchema(
        [Endpoint("a", "input", 32), Endpoint("b", "input", 32),
         Endpoint("last", "input", 1), Endpoint("y", "output", 32)],
        authority=Authority.TASK,
        reading=Reading.ELABORATED,
    )

    behavior = lower(source, schema, bound=1)
    manifest = source_frontend_catalogue(
        source_model(source), source, "behavior.py")

    assert behavior.frontend_catalogue_manifest() == manifest
    assert set(behavior.proc.discharged) & set(manifest)
    assert all(manifest[goal].cls == "safety"
               for goal in behavior.proc.discharged)


def test_atomic_source_catalogue_includes_readiness_call_closure():
    from behave.sim.behavior.declared import ATTRIBUTEERROR, safety_goal_id
    source = """\
class M:
    def ready_helper(self, value):
        if value:
            return True
        return False

    def w_ready(self):
        if self.ready_helper(self.full):
            return False
        return True

    def unused(self):
        if self.full:
            return False
        return True

    def process_w(self, w_in_data):
        if w_in_data:
            self.full = 1

    def process_r(self):
        return {"r_out_data": 0}
"""
    manifest = source_frontend_catalogue(
        source_model(source), source, "behavior.py")

    sites = tuple(source_goals.enumerate_sites(source, "behavior.py"))
    expected = {
        site.goal(outcome).id
        for site in sites
        if site.owner[0] in {"ready_helper", "w_ready", "process_w", "process_r"}
        for outcome in site.outcomes
    }
    unused = {
        site.goal(outcome).id
        for site in sites if site.owner[0] == "unused"
        for outcome in site.outcomes
    }
    # The fixture deliberately has no reset/constructor defining ``full``.
    # Its reachable readiness read owes AttributeError as well as structural
    # events; enumerate_sites alone is not the complete safety catalogue.
    readiness = next(node for node in ast.walk(ast.parse(source))
                     if isinstance(node, ast.FunctionDef) and node.name == "w_ready")
    read = next(node for node in ast.walk(readiness)
                if isinstance(node, ast.Attribute) and node.attr == "full")
    safety = safety_goal_id("behavior.py", read, ATTRIBUTEERROR)
    expected.add(safety)
    assert manifest[safety].cls == "safety"
    assert set(manifest) == expected
    assert set(manifest).isdisjoint(unused)


def test_bind_rejects_omission_rename_duplicate_and_analysis_catalogue_mismatch():
    behavior = lower(SOURCE, _schema(), bound=1)
    manifest = behavior.frontend_catalogue_manifest()
    first = next(iter(manifest))

    omitted = _replace_manifest(manifest, omit=(first,))
    with pytest.raises(Refused, match="catalogue-behavior-frontend-unknown-goal"):
        behavior.bind(1, catalogue_manifest=omitted)

    occurrence_omitted = CatalogueManifest(
        tuple(manifest.values()), occurrences=manifest.occurrences[1:])
    with pytest.raises(
            Refused,
            match="catalogue-behavior-frontend-unknown-occurrence"):
        behavior.bind(1, catalogue_manifest=occurrence_omitted)

    renamed = _replace_manifest(manifest, rename=(first, first + ":renamed"))
    with pytest.raises(Refused, match="catalogue-behavior-frontend-unknown-goal"):
        behavior.bind(1, catalogue_manifest=renamed)

    with pytest.raises(Refused, match="catalogue-duplicate-goal"):
        CatalogueManifest([manifest[first], manifest[first]])

    injected = CatalogueManifest(
        tuple(manifest.values())
        + (Goal("foreign.goal", "coverage", provider="foreign"),),
        occurrences=manifest.occurrences)
    with pytest.raises(Refused, match="behavior-catalogue-external-goal-invalid"):
        behavior.bind(1, catalogue_manifest=injected)

    task = api.admit(
        contract(), realization(identity=behavior.identity), reference=_ref())
    exact = api._bind_behavior_exact(task, behavior, 1)
    evaluation = api.ConcreteEvaluation(task, catalogue=manifest)
    outcome = api.analyse(
        evaluation, exact=exact,
        goal_policy=api.evaluation_policy(
            task, behavior.frontend_catalogue_manifest()),
        wall_timeout_seconds=60)
    assert outcome.verdict is api.Verdict.REFUSED
    assert outcome.evidence["refusal"]["kind"] == \
        "analysis-goal-policy-catalogue-mismatch"


def test_behavior_provider_requests_are_bound_to_the_complete_task_manifest(
        monkeypatch):
    behavior = lower(COMMAND_SOURCE, _schema(), bound=1)
    task = api.admit(
        contract(), realization(identity=behavior.identity), reference=_ref())
    exact = api._bind_behavior_exact(task, behavior, 1)
    seed = schedule([2], drain=2)
    raw = run_in_memory(task.contract, seed, Doubling())
    # The accepted-position service consumes the admitted artifact/domain identity,
    # independently of whether this small test device shares the identity.
    trace = AuditedTrace(
        seed, records=raw.records,
        domain="generation-domain", artifact=behavior.identity)
    occurrences = AcceptedTraceOccurrences(
        exact.instance.proc, task.contract, lambda _seed: trace,
        replay_artifact=behavior.identity, replay_domain="generation-domain")
    goal = next(goal for goal in exact.instance.frontend_catalogue_manifest()
                if goal.endswith(":T"))

    def rediscovered(*_args, **_kwargs):
        raise AssertionError("generation rediscovered the goal catalogue from the IR")

    from behave.sim.symbolic import ir

    with monkeypatch.context() as guarded:
        guarded.setattr(ir, "goals_of", rediscovered)
        provider = BehaviorExactProvider(
            exact.instance, occurrences, targetable_goals=(goal,),
            provider_id="behavior->device", timeout_ms=20_000)

    request = provider.select(seed).requests[0]
    assert provider.catalogue_manifest() is exact.instance.catalogue_manifest()
    assert request.catalogue_digest == exact.instance.catalogue_manifest().digest()

    # Observation-only selection cannot leave an earlier request authorized.
    assert provider.select(seed, targets=()).requests == ()
    assert provider.probe(seed, request).outcome.value == "unsupported"
    assert provider.select(seed).requests[0].as_dict() == request.as_dict()

    bad = ProbeRequest(
        request.targets, occurrence=request.occurrence,
        mutable_fields=request.mutable_fields, rank=request.rank,
        catalogue_digest="0" * 64)
    assert provider.probe(seed, bad).outcome.value == "unsupported"
    class WrongDigest:
        name = "behavior->device/wrong-digest"
        policy = provider.policy
        targetable_goals = provider.targetable_goals
        @staticmethod
        def catalogue_manifest():
            return provider.catalogue_manifest()

        @staticmethod
        def cache_telemetry():
            return GenerationCacheTelemetry()

        @staticmethod
        def select(_seed):
            return api.ProbeSelection(
                (bad,), provider.catalogue_manifest().digest())

        @staticmethod
        def probe(_seed, _request):
            raise AssertionError("the scheduler must reject the request before Probe")

    with pytest.raises(Refused, match="generation-request-catalogue-mismatch"):
        api.generate_stimuli(
            task, (seed,), (WrongDigest(),),
            catalogue=exact.instance.catalogue_manifest(),
            generation_spec=api.GenerationSpec(
                len(seed.slots), 1,
                task.contract.clocks[0].reset_hold_cycles, 2),
            probe_budget=1)

    generated = api.generate_stimuli(
        task, (seed,), (provider,),
        catalogue=exact.instance.catalogue_manifest(),
        generation_spec=api.GenerationSpec(
            len(seed.slots), 1,
            task.contract.clocks[0].reset_hold_cycles, 2),
        probe_budget=1)
    assert generated.catalogue == exact.instance.catalogue_manifest()
    assert generated.catalogue.canonical == \
        exact.instance.catalogue_manifest().canonical
    assert generated.as_dict()["catalogue_digest"] == request.catalogue_digest
    rows = [row for entries in generated.provenance.values() for row in entries]
    assert rows and all(dict(row)["catalogue_digest"] == request.catalogue_digest
                        for row in rows)


def test_occurrence_search_returns_a_candidate_that_replay_hits(monkeypatch):
    behavior = lower(COMMAND_SOURCE, _schema(), bound=1)
    task = api.admit(
        contract(), realization(identity=behavior.identity), reference=_ref())
    exact = api._bind_behavior_exact(task, behavior, 1)
    manifest = exact.instance.frontend_catalogue_manifest()
    goals = tuple(
        goal for goal, declaration in manifest.items()
        if declaration.cls == "coverage"
        and goal not in exact.instance.proc.impossible)
    assert len(goals) > 1

    from behave.sim.symbolic import interp
    from behave.sim.engines import local_ir as local_exact

    calls = []
    cone = local_exact._LocalQueryPreparation.cone

    # Cones may now come from one shared reverse walk instead of a per-occurrence
    # _goal_occurrence_keep call; this accessor still serves each occurrence.
    def counted(self, goal, site):
        calls.append((goal, site))
        return cone(self, goal, site)

    monkeypatch.setattr(local_exact._LocalQueryPreparation, "cone", counted)
    initial = dict(exact.instance.proc.initial)
    answer = local_exact.local_hit_any(
        exact.instance.proc, exact.instance.env, initial, goals,
        timeout_ms=20_000)

    assert calls
    assert len(calls) == len(set(calls))
    assert all(goal in goals and type(site) is int for goal, site in calls)
    assert answer.outcome.value == "sat", answer.as_dict()
    _state, _outputs, hits, error = interp.run_step(
        exact.instance.proc, answer.candidate, initial)
    assert not error
    assert answer.goal in hits


def test_seed_selection_keeps_but_does_not_probe_a_discharged_goal():
    source = """\
class M:
    def reset(self):
        pass

    def process(self, in_a):
        values = [4, 5]
        return {"out_y": values[0]}
"""
    behavior = lower(source, _schema(), bound=1)
    safety = tuple(
        goal for goal, declaration in behavior.frontend_catalogue_manifest().items()
        if declaration.cls == "safety")
    assert safety == tuple(behavior.proc.discharged)
    occurrence = BehaviorOccurrence(
        "seed:0", 0, dict(behavior.proc.initial), ())
    provider = BehaviorExactProvider(
        behavior, lambda _seed: (occurrence,), targetable_goals=safety,
        provider_id="behavior->device", timeout_ms=20_000)

    selected = provider.select(schedule([2], drain=2))

    assert provider.targetable_goals == safety
    assert selected.requests == ()


class _RecordedBehaviorDevice(Doubling):
    """The ordinary signal-level device plus the Behavior goal-bit channel."""

    def __init__(self, behavior):
        super().__init__()
        self.behavior = behavior
        self.identity = behavior.identity

    def traces_per_process(self, task_contract, stimuli):
        out = []
        frontend = self.behavior.frontend_catalogue_manifest()
        for stimulus in stimuli:
            trace = run_in_memory(task_contract, stimulus, self)
            rows, slots = [], []
            for index, record in enumerate(trace.records):
                accepted = dict(record.accepted).get("in")
                if accepted is not None:
                    rows.append({"in_a": accepted[0]})
                    slots.append(index)
            replayed, witness = self.behavior.replay_witness(rows, "raw-goal-bits")
            assert not replayed.broken and witness is not None
            by_slot = dict(zip(slots, witness.hit_sets))
            records = []
            for index, record in enumerate(trace.records):
                reached = set(by_slot.get(index, ()))
                records.append(SlotRecord(
                    accepted=dict(record.accepted), state=dict(record.state),
                    outputs=dict(record.outputs), monitor=dict(record.monitor),
                    hits={goal: goal in reached for goal in frontend}))
            out.append(AuditedTrace(
                trace.stimulus, request=trace.request,
                initial_state=dict(trace.initial_state), records=records,
                completion=trace.completion, domain=trace.domain,
                artifact=self.identity))
        return out


def test_public_command_preserves_one_manifest_through_score_analysis_and_report(
        tmp_path):
    exemplar = schedule([2], drain=2)
    behavior = lower(
        COMMAND_SOURCE, _schema(), bound=len(exemplar.slots))
    # Doubling emits exactly one response. The batched test device has no
    # reference-driven completion channel, so declare that fixture contract.
    task_contract = contract(answers=1)
    task_realization = realization(identity=behavior.identity)
    reference = _ref()
    admitted = api.admit(
        task_contract, task_realization, reference=reference)
    exact = api._bind_behavior_exact(
        admitted, behavior, len(exemplar.slots))
    generation_spec = api.GenerationSpec(
        len(exemplar.slots), 1,
        task_contract.clocks[0].reset_hold_cycles, 2)
    request = command_module._command_request(
        run_directory=tmp_path / "behavior-command",
        task=admitted,
        artifact=_RecordedBehaviorDevice(behavior),
        baseline=api.BaselinePolicy(case_limit=4, max_queue_depth=1),
        providers=(_BaselineOnly(),),
        frontend_catalogue=behavior.frontend_catalogue_manifest(),
        generation_spec=generation_spec,
        probe_budget=0, reference=reference, exact=exact,
        analysis_wall_timeout_seconds=60,
        feasibility_timeout_ms=20_000, query_timeout_ms=20_000)
    assert request.catalogue is exact.instance.catalogue_manifest()

    got = api.handle_command(request)

    assert got.status is api.CommandStatus.EVALUATED
    assert got.evaluation.results
    assert all(run.ran for run in got.evaluation.results)
    assert got.generated.catalogue is request.catalogue
    assert got.generated.catalogue is got.evaluation.catalogue
    assert got.generated.catalogue == request.catalogue
    assert got.analysis is not None
    assert got.analysis.verdict is api.Verdict.REFUSED
    assert got.analysis.per_goal == {}
    assert got.analysis.evidence["refusal"]["kind"] == \
        "behavior-analysis-monitor-state-unavailable"
    assert got.analysis.evidence["catalogue_manifest"] == request.catalogue.as_dict()
    assert got.analysis.evidence["catalogue_digest"] == request.catalogue.digest()
    public = got.as_dict()
    assert public["catalogue_manifest"] == request.catalogue.as_dict()
    assert public["catalogue_digest"] == request.catalogue.digest()
    assert "catalogue_manifest" not in public["generation"]
    assert "catalogue_manifest" not in public["concrete"]
    assert "catalogue" not in public["concrete"]

    record = json.loads(Path(got.record_path).read_text())
    command_image = record["progress"]["command"]
    assert command_image["catalogue"]["digest"] == request.catalogue.digest()
    assert set(command_image["catalogue"]) == {"digest"}
    assert command_image["frontend_catalogue"]["digest"] == \
        request.frontend_catalogue.digest()
    assert set(command_image["frontend_catalogue"]) == {"digest"}
    assert record["authority"]["evaluation"]["catalogue_manifest"] == \
        request.catalogue.as_dict()
    assert record["report"]["analysis"]["evidence"]["catalogue_manifest"] == \
        request.catalogue.as_dict()

    refused = api.handle_command(request)
    assert refused.status is api.CommandStatus.REFUSED
    assert refused.as_dict()["catalogue_manifest"] == request.catalogue.as_dict()
    assert refused.as_dict()["catalogue_digest"] == request.catalogue.digest()


def test_behavior_record_without_goal_bits_is_refused_not_counted_as_a_miss():
    behavior = lower(COMMAND_SOURCE, _schema(), bound=1)
    task = api.admit(
        contract(), realization(identity=behavior.identity), reference=_ref())
    exact = api._bind_behavior_exact(task, behavior, 1)
    stimulus = schedule([2], drain=2)
    trace = run_in_memory(task.contract, stimulus, Doubling())
    trace = AuditedTrace(
        stimulus, records=trace.records, completion=trace.completion,
        domain=trace.domain, artifact=behavior.identity)
    result = api.RunResult.not_compared(
        stimulus, trace,
        api.RunFailure.verifier(
            "test-audit-view", api.Refusal("audit-only", "audit-only fixture")))
    evaluation = api.ConcreteEvaluation(
        task, results=(result,), catalogue=exact.instance.catalogue_manifest())

    outcome = api.analyse(
        evaluation, exact=exact,
        goal_policy=api.evaluation_policy(
            task, behavior.frontend_catalogue_manifest()),
        wall_timeout_seconds=60)

    assert outcome.verdict is api.Verdict.REFUSED
    assert outcome.evidence["refusal"]["kind"] == \
        "behavior-record-hit-observation-unavailable"


def test_behavior_record_with_non_boolean_goal_bit_is_refused_not_a_hit_or_miss():
    behavior = lower(COMMAND_SOURCE, _schema(), bound=1)
    task = api.admit(
        contract(), realization(identity=behavior.identity), reference=_ref())
    exact = api._bind_behavior_exact(task, behavior, 1)
    stimulus = schedule([2], drain=2)
    trace = run_in_memory(task.contract, stimulus, Doubling())
    goal = next(iter(behavior.frontend_catalogue_manifest()))
    records = list(trace.records)
    first = records[0]
    records[0] = SlotRecord(
        accepted=dict(first.accepted), state=dict(first.state),
        outputs=dict(first.outputs), monitor=dict(first.monitor),
        hits={declared: (1 if declared == goal else False)
              for declared in behavior.frontend_catalogue_manifest()})
    recorded = AuditedTrace(
        stimulus, initial_state=dict(trace.initial_state), records=records,
        completion=trace.completion, domain=trace.domain,
        artifact=behavior.identity)
    result = api.RunResult.not_compared(
        stimulus, recorded,
        api.RunFailure.verifier(
            "test-audit-view", api.Refusal("audit-only", "audit-only fixture")))
    evaluation = api.ConcreteEvaluation(
        task, results=(result,), hits=(goal,),
        catalogue=exact.instance.catalogue_manifest())

    outcome = api.analyse(
        evaluation, exact=exact,
        goal_policy=api.evaluation_policy(
            task, behavior.frontend_catalogue_manifest()),
        wall_timeout_seconds=60)

    assert outcome.verdict is api.Verdict.REFUSED
    assert outcome.evidence["refusal"]["kind"] == \
        "behavior-record-hit-observation-unreadable"


def test_even_a_false_undeclared_goal_bit_stops_concrete_evaluation():
    behavior = lower(COMMAND_SOURCE, _schema(), bound=1)
    task = api.admit(
        contract(), realization(identity=behavior.identity), reference=_ref())
    stimulus = schedule([2], drain=2)
    trace = run_in_memory(task.contract, stimulus, Doubling())
    records = list(trace.records)
    first = records[0]
    records[0] = SlotRecord(
        accepted=dict(first.accepted), state=dict(first.state),
        outputs=dict(first.outputs), monitor=dict(first.monitor),
        hits={"undeclared.behavior.goal": False})
    recorded = AuditedTrace(
        stimulus, initial_state=dict(trace.initial_state), records=records,
        completion=trace.completion, domain=trace.domain,
        artifact=behavior.identity)
    result = api.RunResult.not_compared(
        stimulus, recorded,
        api.RunFailure.verifier(
            "test-audit-view",
            api.Refusal("catalogue-audit", "catalogue audit fixture")))

    with pytest.raises(Refused) as refused:
        api.ConcreteEvaluation(
            task, results=(result,),
            catalogue=behavior.frontend_catalogue_manifest())

    assert refused.value.refusal.kind == \
        "evaluation-recorded-hit-outside-the-catalogue"
