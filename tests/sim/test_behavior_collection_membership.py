"""Finite lookup bindings preserve Python membership, reads and source hits."""

import ast

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.behavior.construction import construction_facts
from behave.sim.behavior.extract import LoweringError, ScopeResolver, source_model
from behave.sim.behavior.goals import goal_set
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from tests.sim.test_source_closed_bindings import agrees


def _compare(source, *, width=3, lowerer=None):
    proc = lower_with_state_ranges(
        source, input_widths=[width], outputs=["y"], lowerer=lowerer)
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    for a in range(1 << width):
        model = namespace["M"]()
        model.reset()
        expected = model.process(a)
        _state, output, _hits, error = interp.run_step(proc, {"a": a})
        assert not error, (a, error)
        assert output.transactions == [expected], (a, output, expected)
    produced = {command.goal for command in ir.walk(proc.body)
                if isinstance(command, (ir.Mark, ir.Fail)) and command.goal}
    assert set(goal_set(source)) <= produced | set(proc.discharged)
    return proc


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
@pytest.mark.parametrize("binding,setup,table", [
    ("TABLE = {1: 6, 4: 2}\n", "pass", "TABLE"),
    ("", "self.table = {1: 6, 4: 2}", "self.table"),
    ("", "self.table = {}", "self.table"),
    ("", "self.table = {i: i + 1 for i in range(4)}", "self.table"),
])
def test_mapping_membership_and_lookup_share_one_source_binding(binding, setup, table, lowerer):
    source = f'''{binding}class M:
    def reset(self):
        {setup}
    def process(self, a):
        if a in {table}:
            return {{"y": {table}[a]}}
        return {{"y": 0}}
'''
    proc = _compare(source, lowerer=lowerer)
    if table == "self.table":
        assert proc.contract.how("table")[0] == "constant"


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
def test_class_mapping_with_nested_values_and_local_alias(lowerer):
    source = '''class M:
    TABLE = {1: (3, 5), 4: (6, 7)}
    def reset(self):
        pass
    def process(self, a):
        table = self.TABLE
        if a not in table:
            return {"y": 0}
        return {"y": table[a][a & 1]}
'''
    _compare(source, lowerer=lowerer)


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
@pytest.mark.parametrize("instance", [False, True])
def test_selected_list_payload_reads_do_not_enable_erased_carrier_equality(instance, lowerer):
    table = "{0: [3, 4], 1: [4, 5]}"
    declaration = "" if instance else f"    TABLE = {table}\n"
    reset = f"self.TABLE = {table}" if instance else "pass"
    source = f'''class M:
{declaration}    def reset(self):
        {reset}
    def process(self, a):
        row = self.TABLE[a & 1]
        return {{"y": int(row == [3, 4]) + 2 * int(row == (3, 4))}}
'''
    _compare(source.replace(
        "int(row == [3, 4]) + 2 * int(row == (3, 4))", "row[0] + row[1]"), lowerer=lowerer)
    # Static references intentionally erase list/tuple identity for reads.
    # Do not silently expand that representation to carrier-observing equality.
    with pytest.raises(LoweringError) as refused:
        lower_with_state_ranges(source, input_widths=[3], outputs=["y"], lowerer=lowerer)
    assert refused.value.code == "static-collection-escapes"


def test_selected_static_mapping_keeps_its_actual_keys_not_padding_or_values():
    source = '''LEFT = {1: 0, 3: 7}
RIGHT = {2: 3}
class M:
    def reset(self):
        pass
    def process(self, a):
        table = LEFT if a & 1 else RIGHT
        return {"y": int(a in table) + 2 * int(a not in table)}
'''
    _compare(source)


def test_static_sequence_membership_compares_elements_not_indices():
    source = '''VALUES = (7, 3)
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": int(a in VALUES)}
'''
    _compare(source)


def test_membership_keeps_python_boolean_integer_key_equality():
    source = '''class M:
    TABLE = {False: 2, True: 6, 3: 7}
    def reset(self):
        pass
    def process(self, a):
        return {"y": int(a in self.TABLE) + 2 * int((a == 1) in self.TABLE)}
'''
    _compare(source)


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
def test_dense_membership_preserves_goals_in_a_proved_dead_exception_arm(lowerer):
    source = '''class M:
    TABLE = {0: 4, 1: 6, 2: 3, 3: 1, 4: 7, 5: 5, 6: 2, 7: 0}
    def reset(self):
        pass
    def process(self, a):
        if a not in self.TABLE:
            raise ValueError(f"out of range: {a}")
        return {"y": self.TABLE[a]}
'''
    _compare(source, lowerer=lowerer)
    # The proof depends on the declared input domain. A wider input must not
    # discard this reachable exception and claim the same capability.
    with pytest.raises(LoweringError, match="statement Raise"):
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"], lowerer=lowerer)


def test_membership_interval_coalescing_preserves_gaps_negatives_and_duplicates():
    source = '''class M:
    VALUES = (-3, -2, False, True, 1, 4, 4)
    def reset(self):
        pass
    def process(self, a):
        return {"y": int((a - 3) in self.VALUES)}
'''
    _compare(source)


def test_dense_membership_formula_size_is_independent_of_interval_length():
    from behave.sim.behavior.lower_static_refs import integer_membership

    expected = ir.Op(
        "land", ir.Op("le", ir.Lit(0), ir.Op("toint", ir.Var("a"))),
        ir.Op("le", ir.Op("toint", ir.Var("a")), ir.Lit(255)))
    assert ir.expressions_equal(
        integer_membership(ir.Var("a"), range(256)), expected)


def test_membership_chain_does_not_execute_the_rhs_after_an_earlier_false_pair():
    source = '''TABLE = {1: 0, 3: 0}
class M:
    def reset(self):
        self.count = 0
    def key(self, a):
        self.count += 1
        return a
    def process(self, a):
        hit = 0 < self.key(a) in TABLE
        return {"y": int(hit) + self.count * 2}
'''
    _compare(source)


@pytest.mark.parametrize("change", [
    "M.TABLE[1] = 7",
    "Alias = M\nAlias.TABLE = {0: 6}",
    "def alter():\n    M.TABLE[1] = 7\nalter()",
])
def test_definition_time_class_mutations_do_not_create_a_stale_rom(change):
    source = '''class M:
    TABLE = {1: 2}
    def reset(self):
        pass
    def process(self, a):
        return {"y": int(a in self.TABLE)}
''' + change + "\n"
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[3], outputs=["y"])


@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer], ids=["term", "graph"])
@pytest.mark.parametrize("mutation", ["self.table[a] = 7", "self.table.clear()"])
def test_mutable_mapping_is_not_frozen_to_its_construction_value(mutation, lowerer):
    source = f'''class M:
    def reset(self):
        self.table = {{1: 2}}
    def process(self, a):
        {mutation}
        return {{"y": int(a in self.table)}}
'''
    agrees(source, lowerer)


def test_mapping_alias_mutation_cannot_be_frozen_to_its_construction_value():
    source = '''class M:
    def reset(self): self.table = {1: 2}
    def process(self, a):
        alias = self.table
        alias[a] = 7
        return {"y": int(a in self.table)}
'''
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[3], outputs=["y"])


def test_class_mapping_is_not_used_when_an_instance_field_shadows_it():
    source = '''class M:
    table = {1: 5}
    def reset(self):
        self.table = {2: 7}
    def process(self, a):
        return {"y": self.table[a] if a in self.table else 0}
'''
    _compare(source)


@pytest.mark.parametrize("declaration", [
    "annotation: Hook[TABLE]",
    "annotation: Hook[TABLE] = 0",
    "def helper(self, a: Hook[TABLE]):\n        return a",
    "def helper(self, a=Hook[TABLE]):\n        return a",
    "ignored = Hook[TABLE]",
])
def test_implicit_definition_calls_cannot_leave_class_collection_facts(declaration):
    source = f'''class Hook:
    @classmethod
    def __class_getitem__(cls, table):
        table[0] = 7
        return int
class M:
    TABLE = {{1: 2}}
    {declaration}
    def reset(self):
        pass
    def process(self, a):
        return {{"y": int(a in self.TABLE)}}
'''
    resolver = ScopeResolver(source_model(source, cls="M"))
    assert "TABLE" not in resolver.class_tables


@pytest.mark.parametrize("tail", ["ignored = hook[0]", "ignored = hook + 1"])
def test_opaque_module_tail_cannot_leave_class_collection_facts(tail):
    source = '''class M:
    TABLE = {1: 2}
    def reset(self):
        pass
    def process(self, a):
        return {"y": int(a in self.TABLE)}
''' + tail + "\n"
    resolver = ScopeResolver(source_model(source, cls="M"))
    assert "TABLE" not in resolver.class_tables


def test_postponed_annotations_do_not_invoke_their_subscripts():
    source = '''from __future__ import annotations
class M:
    TABLE = {1: 2}
    annotation: Unknown[TABLE]
    def reset(self):
        pass
    def process(self, a: Unknown[TABLE]) -> dict[str, int]:
        return {"y": int(a in self.TABLE)}
'''
    resolver = ScopeResolver(source_model(source, cls="M"))
    assert resolver.class_tables["TABLE"] == {1: 2}


def test_static_mapping_hits_both_branch_outcomes_and_can_bind_a_query():
    from behave.sim.behavior.instance import BehaviorInstance

    source = '''class M:
    TABLE = {3: 7}
    def reset(self):
        pass
    def process(self, a):
        if a in self.TABLE:
            return {"y": 7}
        return {"y": 0}
'''
    proc = _compare(source)
    branch = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.If))
    targets = {g for g in goal_set(source)
               if g.startswith("branch:") and f":{branch.lineno}:" in g}
    assert len(targets) == 2
    for a, suffix in [(0, ":F"), (3, ":T")]:
        hits = interp.run_step(proc, {"a": a})[2]
        assert hits & targets == {g for g in targets if g.endswith(suffix)}
    instance = BehaviorInstance(proc, proc.state_ranges, source=source)
    instance.bind(1)
    verdict = instance.query(targets, 1)
    assert set(verdict.reachable) == targets
    witnesses = tuple(verdict.reachable.values())
    assert instance.source_audit(witnesses).status.value == "agreed"
    assert construction_facts(source).exact_values() == {}


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("wrong", [False, True])
def test_public_search_finds_missing_lookup_branch_and_native_replay_confirms_it(tmp_path, wrong):
    source = b'''class M:
    TABLE = {37: 5, 193: 7}
    def reset(self):
        pass
    def process(self, a, b):
        if a in self.TABLE:
            return {"y": self.TABLE[a] + (b & 1)}
        return {"y": 0}
    def process_slot(self, slot):
        if set(slot["inputs"]) != {"left", "right"}:
            return {}
        left = slot["inputs"]["left"]["a"]
        right = slot["inputs"]["right"]["b"]
        return {"result": self.process(left, right)}
'''
    candidate = source.replace(b"{37: 5, 193: 7}", b"{37: 4, 193: 6}") if wrong else source
    check_public_search_loop(tmp_path, source, candidate, wrong=wrong)


def check_public_search_loop(tmp_path, source, candidate, *, wrong):
    """One baseline -> residual query -> native hit/score regression path."""
    from behave.sim import api
    from tests.sim.test_behavior_generation_provider import _contract, _stimulus

    path = tmp_path / "behavior.py"
    path.write_bytes(candidate)
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
                                   {"behavior.py": source}, config={"class": "M"})
    contract = _contract()
    realization = api.realization_of_behavior_source(candidate.decode(), contract, file="behavior.py")
    task = api.admit(contract, realization, reference=reference)
    spec = api.GenerationSpec(3, 1, 1, 1, 0)
    handle = api.prepare_behavior(task, api.BehaviorConfig(path, model="M"), spec)
    evaluations = []

    def trace_of(seed):
        for evaluation in evaluations:
            if evaluation.stimuli == (seed,):
                return evaluation.results[0].trace
        authority = handle.replay((seed,), reference=reference)
        assert authority.authoritative, str([
            run.refused.as_dict() if run.refused else None
            for run in authority.evaluation.results])
        evaluations.append(authority.evaluation)
        return authority.evaluation.results[0].trace

    offered = _stimulus()
    ready = dict.fromkeys(api.receptive_controls(contract), 1)
    seed = api.Stimulus(queues=dict(offered.queues), slots=[
        api.EventSlot(offers=slot.offers, reset=slot.reset,
                      controls={**ready, "rst": int(slot.reset)})
        for slot in offered.slots])
    trace_of(seed)
    assert evaluations[0].passed
    provider = api.prepare_behavior_generation(task, spec, reference, trace_of,
                                               timeout_s=60, timeout_ms=20_000)
    targets = {goal for goal, declaration in provider.catalogue_manifest().items()
               if declaration.family == "decision"} - set(evaluations[0].hits)
    assert targets
    # Proposals are requested before exact goal queries, so one counted Probe
    # no longer suffices: the loop below probes the selection in order, as the
    # production search does, until a native replay grants a target hit. The
    # counted budget bounds the session; a scope without one has no deadline.
    with provider.search_scope(probe_budget=8):
        selected = provider.select(seed, targets=targets)
        assert isinstance(selected, api.ProbeSelection), selected.as_dict()
        assert targets.isdisjoint(selected.replay.hits)
        assert selected.requests
        answers, reached = [], set()
        for request in selected.requests:
            answer = provider.probe(seed, request)
            answers.append(answer.as_dict())
            # A relaxed proposal along the observed path only prepares state and
            # may name no candidate; the native replay is what grants a hit.
            if answer.outcome.value not in ("sat", "proposed") or answer.candidate is None:
                continue
            confirmed = provider.select(answer.candidate, targets=targets)
            assert isinstance(confirmed, api.ProbeSelection), confirmed.as_dict()
            reached = targets & set(confirmed.replay.hits)
            if reached:
                break
        assert reached, answers
        assert answer.goal is None or answer.goal in reached
    assert len(evaluations) >= 2
    assert reached <= set(evaluations[-1].hits)
    assert evaluations[-1].passed is not wrong
    if wrong:
        assert evaluations[-1].failure_witness is not None
