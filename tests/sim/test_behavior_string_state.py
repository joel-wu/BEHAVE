"""Source-derived finite FSM labels retain Python semantics and native hits."""

import pytest

from behave.sim.behavior.audit import model_audit
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges


SOURCES = [
    '''class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        y = 1 if self.mode == "run" else 0
        self.mode = "run" if a == 7 else "idle"
        return {"y": y}
''',
    '''class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        next_mode = self.mode
        if a == 7:
            next_mode = "run"
        elif a == 3:
            next_mode = "idle"
        y = 1 if next_mode == self.mode else 0
        self.mode = next_mode
        return {"y": y}
''',
    '''class M:
    def reset(self):
        self.mode = "idle"
        self.saved = "run"
    def change(self): self.mode = "run"
    def process(self, a):
        old = self.mode
        if a == 7: self.change()
        if a == 3: self.mode = self.saved
        self.saved = old
        return {"y": 1 if self.mode == self.saved else 0}
''',
    '''class M:
    def reset(self):
        self.IDLE = "idle"
        self.RUN = "run"
        self.mode = self.IDLE
    def process(self, a):
        self.mode = self.RUN if a else self.IDLE
        return {"y": 1 if self.mode == self.RUN else 0}
''',
]

LABEL_OPERATIONS = [
    '''class M:
    IDLE = "idle"
    RUN = "run"
    def reset(self): self.mode = self.IDLE
    def process(self, a):
        self.mode = self.RUN if a else self.IDLE
        return {"y": 1 if self.mode == self.RUN else 0}
''',
    '''class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        y = 1 if self.mode in ("run", "missing", 0, True) else 0
        self.mode = "run" if a == 7 else "idle"
        return {"y": y}
''',
    '''class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        y = 1 if self.mode not in ("run", "missing") else 0
        self.mode = "run" if a == 7 else "idle"
        return {"y": y}
''',
    '''class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        y = 1 if a in ("run", 0, True, 7) else 0
        self.mode = "run" if a == 7 else "idle"
        return {"y": y}
''',
    '''class M:
    def reset(self): self.mode = "idle"
    def change(self, a):
        self.mode = "run" if a == 7 else "idle"
        return a == 7
    def process(self, a):
        y = 1 if self.mode in (("idle",) if self.change(a) else ("run",)) else 0
        return {"y": y}
''',
    '''class M:
    LABEL = "wrong-class-label"
    def reset(self):
        self.LABEL = "idle"
        self.mode = self.LABEL
    def process(self, a):
        self.mode = "run" if a else self.LABEL
        return {"y": 1 if self.mode == self.LABEL else 0}
''',
]

GROUPED_OPERATIONS = [
    '''class M:
    IDLE, RUN = ("idle", "run")
    def reset(self): self.mode = self.IDLE
    def process(self, a):
        self.mode = self.RUN if a else self.IDLE
        return {"y": 1 if self.mode == self.RUN else 0}
''',
    '''class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        self.mode, y = ("run" if a == 7 else "idle", int(self.mode == "run"))
        return {"y": y}
''',
    '''class M:
    def reset(self):
        self.mode = "idle"
        self.other = "run"
    def process(self, a):
        if a: self.mode, self.other = self.other, self.mode
        return {"y": int(self.mode == "run")}
''',
]

MAPPING_OPERATIONS = [f'''TABLE = {{"idle": 3, "run": 5}}
class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        ref = TABLE
        y = {lookup}
        self.mode = "run" if a == 7 else "idle"
        return {{"y": y}}
''' for lookup in ("TABLE[self.mode]", "TABLE.get(self.mode, 0)",
                   "ref[self.mode]", "ref.get(self.mode, 0)")]

MAPPING_OPERATIONS += ['''TABLE = {"idle": 3, "run": 5}
class M:
    def reset(self): self.mode = "idle"
    def change(self, a):
        self.mode = "run" if a == 7 else "idle"
        return a
    def process(self, a):
        return {"y": TABLE.get(self.mode, self.change(a))}
''']

GROUPED_EFFECTS = [
    '''class M:
    IDLE, (RUN, AGAIN) = ("idle", ("run", "idle"))
    IDLE, RUN = RUN, IDLE
    def reset(self): self.mode = self.IDLE
    def process(self, a):
        self.mode = self.RUN if a else self.IDLE
        return {"y": int(self.mode == "run")}
''',
    '''class M:
    LABEL, LABEL = ("wrong", "idle")
    def reset(self): self.mode = self.LABEL
    def process(self, a):
        self.mode = "run" if a else self.LABEL
        return {"y": int(self.mode == "run")}
''',
    '''class M:
    def reset(self): self.mode = "idle"
    def change(self, a):
        self.mode = "run" if a else "idle"
        return a
    def process(self, a):
        self.mode, y = self.mode, self.change(a)
        return {"y": y + int(self.mode == "run")}
''',
    '''class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        self.mode, self.mode = "run", ("idle" if a else "run")
        return {"y": int(self.mode == "run")}
''',
]

MISSING_KEY_OPERATIONS = [f'''TABLE = {{"idle": 3, 0: 9, 1: 11}}
class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        ref = TABLE
        self.mode = "run" if a else "idle"
        return {{"y": {lookup}}}
''' for lookup in ("TABLE[self.mode]", "TABLE.get(self.mode, 7)",
                   "ref[self.mode]", "ref.get(self.mode, 7)")]

NAMESPACE_OPERATIONS = [
    LABEL_OPERATIONS[0] + "\nAlias = M\nBehavior = Alias\n",
    '''class M:
    IDLE = "idle"
    RUN = "run"
    def reset(self): self.mode = self.IDLE
    @staticmethod
    def enabled(a): return a != 0
    def process(self, a):
        self.mode = self.RUN if self.enabled(a) else self.IDLE
        return {"y": int(self.mode == self.RUN)}
''',
]


def _audit(source, lowerer):
    proc = lower_with_state_ranges(
        source, input_widths=[4], outputs=["y"], lowerer=lowerer)
    scope = {}
    exec(compile(source, "behavior.py", "exec"), scope)  # noqa: S102 -- trusted fixtures
    assert model_audit(
        proc, scope["M"], [[{"a": a} for a in (0, 7, 7, 3, 0, 7)]]) == []
    binding = proc.semantics.value_binding("state", "mode")
    assert set(binding.value_type.members) == {"idle", "run"}
    return proc


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_finite_string_domains_are_derived_from_all_source_writes(source, lowerer):
    _audit(source, lowerer)


@pytest.mark.parametrize("source", LABEL_OPERATIONS)
@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_finite_label_constants_and_membership_preserve_python_semantics(source, lowerer):
    _audit(source, lowerer)


@pytest.mark.parametrize("source", [*GROUPED_OPERATIONS, *MAPPING_OPERATIONS])
@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_finite_label_grouped_writes_and_mapping_selectors(source, lowerer):
    _audit(source, lowerer)


@pytest.mark.parametrize("source", [*GROUPED_EFFECTS, *MISSING_KEY_OPERATIONS])
@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_finite_labels_preserve_binding_effects_and_missing_key_errors(source, lowerer):
    _audit(source, lowerer)


@pytest.mark.parametrize("source", NAMESPACE_OPERATIONS)
@pytest.mark.parametrize("lowerer", [_Lowerer, GraphLowerer])
def test_finite_label_namespace_keeps_pure_aliases_and_standard_descriptors(source, lowerer):
    _audit(source, lowerer)


def test_class_alias_mutation_does_not_certify_old_labels():
    source = LABEL_OPERATIONS[0] + '\nAlias = M\nAlias.RUN = "changed"\n'
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])


def test_shadowed_standard_descriptor_is_not_treated_as_inert():
    source = "staticmethod = lambda fn: fn\n" + NAMESPACE_OPERATIONS[1]
    with pytest.raises(LoweringError, match="decorated"):
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])


@pytest.mark.parametrize("write", [
    "self.mode = a", "self.mode += 'x'", "self.mode = str(a)",
    "self.mode = 'run' if a else None", "del self.mode",
    "self.mode, y = a, 'run'", "self.mode, y = 'run', a; self.mode = y",
])
def test_reset_label_does_not_establish_a_finite_domain_for_unknown_writes(write):
    source = f'''class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        {write}
        return {{"y": a}}
'''
    with pytest.raises(LoweringError):
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])


def test_unknown_local_assignment_poison_reaches_the_state_domain():
    source = '''class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        next_mode = "run"
        if a: next_mode = a
        self.mode = next_mode
        return {"y": a}
'''
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    assert caught.value.code == "state-value-not-ir-carriable"


def test_state_domain_includes_unobserved_writes_without_declaring_them_reachable():
    source = '''class M:
    def reset(self): self.mode = "idle"
    def process(self, a):
        if a == 13: self.mode = "rare"
        if a > 15: self.mode = "outside"
        return {"y": 1 if self.mode == "rare" else 0}
'''
    proc = lower_with_state_ranges(source, input_widths=[4], outputs=["y"])
    members = proc.semantics.value_binding("state", "mode").value_type.members
    assert set(members) == {"idle", "rare", "outside"}


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("source", [SOURCES[1], *LABEL_OPERATIONS,
                                   *GROUPED_OPERATIONS, *MAPPING_OPERATIONS,
                                   *NAMESPACE_OPERATIONS])
def test_automatically_derived_string_state_queries_have_native_witnesses(source):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = _audit(source, GraphLowerer)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source)
    instance.bind(2)
    goals = set(instance.goals())
    result = instance.query(goals, 2)
    assert not result.undecided
    assert {g for g in goals if g.startswith("branch:")} <= set(result.reachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize("source", MISSING_KEY_OPERATIONS)
def test_missing_string_keys_keep_reachable_safety_goals_and_native_failures(source):
    from behave.sim.behavior.instance import BehaviorInstance

    proc = _audit(source, GraphLowerer)
    instance = BehaviorInstance(proc, proc.state_ranges, source=source)
    instance.bind(2)
    goals = set(instance.goals())
    result = instance.query(goals, 2)
    assert not result.undecided
    assert goals == set(result.reachable) | set(result.unreachable)
    if "[self.mode]" in source:
        assert any(goal.startswith("safety:") for goal in result.reachable)
    assert instance.source_audit(tuple(result.reachable.values())).status.value == "agreed"
