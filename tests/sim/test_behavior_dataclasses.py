"""Standard Python construction, linked sources, native hits and ownership."""

import ast

import pytest

from behave.sim import api
from behave.sim.behavior import instrumented
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.isolated import Broken, IsolatedModel
from behave.sim.behavior.source_plan import plan_source

from .test_isolated import TIGHT

SOURCE = '''from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class Value:
    value: int
    items: list[int] = field(default_factory=list)
    def __post_init__(self):
        if self.value < 0:
            self.value = -self.value

@dataclass
class M:
    total: int = 0
    def reset(self):
        self.total = 0
    def process(self, a):
        record = Value(a)
        record.items.append(record.value)
        self.total += record.items[0]
        return {'y': self.total}
'''


def prepared(source=SOURCE, packaged=False):
    if not packaged:
        return source, 'entry.py', 'M'
    bundle = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
        {'entry.py': b'from model import M\n', 'model.py': source.encode()},
        'entry.py', 'M'))
    return bundle.source, bundle.file, bundle.model


@pytest.mark.parametrize('packaged', (False, True))
@pytest.mark.parametrize('spelling', ('direct', 'alias', 'qualified'))
def test_dataclass_source_and_native_observation(packaged, spelling):
    text = SOURCE
    if spelling == 'alias':
        text = text.replace('import dataclass, field', 'import dataclass as record, field').replace('@dataclass', '@record')
    if spelling == 'qualified':
        text = text.replace('from dataclasses import dataclass, field', 'import dataclasses as dc').replace('@dataclass', '@dc.dataclass').replace('field(', 'dc.field(')
    text, file, model = prepared(text, packaged)
    shape = source_model(text, file=file, cls=model, keyword_calls=True)
    plan = plan_source(shape, text, file)
    namespace, _, hits = instrumented.load_source(text, file, goal_plan=plan)
    instance = namespace[model]()
    instance.reset()
    assert [instance.process(a)['y'] for a in (3, -4, 2)] == [3, 7, 9]
    # Both outcomes in a post-init source helper belong to the catalogue and
    # are emitted by actual execution, not invented by constructor lowering.
    post_init = next(node for node in ast.walk(ast.parse(text))
                     if isinstance(node, ast.FunctionDef) and node.name == '__post_init__')
    outcomes = {goal.event: identifier for identifier, goal in plan.manifest.items()
                if identifier.startswith('branch:') and dict(goal.provenance)['line'] == post_init.lineno + 1}
    assert set(outcomes) == {'T', 'F'}
    assert set(outcomes.values()) <= set(hits)
    assert hits
    assert set(hits) <= set(plan.manifest)
    with pytest.raises(LoweringError, match='decorat'):
        source_model(text, file=file, cls=model)


@pytest.mark.isolation
@pytest.mark.parametrize('packaged', (False, True))
@pytest.mark.parametrize('qualified', (False, True))
def test_dataclass_candidate_uses_real_generated_constructor(packaged, qualified):
    text = SOURCE
    if qualified:
        text = text.replace('from dataclasses import dataclass, field', 'import dataclasses as dc')
        text = text.replace('@dataclass', '@dc.dataclass').replace('field(', 'dc.field(')
    text, file, model = prepared(text, packaged)
    with IsolatedModel(text, file=file, cls=model, limits=TIGHT, snapshot_policy='on-demand') as instance:
        instance.reset()
        assert instance.process(a=-5) == {'y': 5}
        assert instance.process(a=3) == {'y': 8}


@pytest.mark.parametrize('prefix', (
    'def dataclass(cls): return cls\n',
    'from dataclasses import dataclass\ndataclass = lambda cls: cls\n',
    'from dataclasses import dataclass\nif True:\n def dataclass(cls): return cls\n',
    'from dataclasses import dataclass\nif True:\n from custom import decorate as dataclass\n',
))
def test_same_spelling_does_not_admit_a_custom_decorator(prefix):
    text = prefix + '@dataclass\nclass M:\n def process(self, a): return {"y": a}\n'
    with pytest.raises(LoweringError, match='decorat'):
        source_model(text, cls='M', keyword_calls=True)


@pytest.mark.isolation
def test_generated_method_rebinding_is_not_a_trusted_definition():
    text = SOURCE + '\nM.__eq__ = M.process\n'
    with pytest.raises(Broken):
        IsolatedModel(text, cls='M', limits=TIGHT)


@pytest.mark.isolation
@pytest.mark.parametrize('packaged', (False, True))
@pytest.mark.parametrize('qualified', (False, True))
def test_typing_annotations_do_not_change_dataclass_execution(packaged, qualified):
    text = SOURCE.replace('from dataclasses', 'from typing import Dict, Any, List, Optional\nfrom dataclasses')
    text = text.replace('list[int]', 'List[int]').replace("def process(self, a):", "def process(self, a) -> Dict[str, Any]:")
    if qualified:
        text = text.replace('from typing import Dict, Any, List, Optional', 'import typing as tp')
        text = text.replace('List[int]', 'tp.List[int]').replace('Dict[str, Any]', 'tp.Dict[str, tp.Any]')
    text, file, model = prepared(text, packaged)
    with IsolatedModel(text, file=file, cls=model, limits=TIGHT, snapshot_policy='on-demand') as instance:
        instance.reset()
        assert instance.process(a=-5) == {'y': 5}


@pytest.mark.isolation
@pytest.mark.parametrize('bad_constructor', (False, True))
def test_public_dataclass_baseline_returns_a_real_decision(tmp_path, bad_constructor):
    from .test_api_path import _ref, contract

    text = SOURCE.replace('self.total += record.items[0]', 'self.total = (2 * record.items[0]) & 255')
    if bad_constructor:
        text = text.replace('total: int = 0', 'total: int')
    candidate = tmp_path / 'candidate.py'
    candidate.write_text(text)
    task_contract, reference = contract(), _ref()
    task = api.admit(task_contract, api.realization_of_behavior_source(
        text, task_contract, model='M'), reference=reference)
    handle = api.prepare_behavior(task, api.BehaviorConfig(candidate, model='M'))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / 'run', reference=reference,
        baseline=api.BaselinePolicy(case_limit=3, max_queue_depth=4),
        providers=(), search_policy=api.SearchPolicy(time_budget_s=0),
        run_analysis=False))
    assert result.decision.authoritative, result.as_dict()
    assert result.decision.passed is not bad_constructor, result.as_dict()


@pytest.mark.isolation
@pytest.mark.parametrize('mutation', (
    'M.__dataclass_fields__["total"].default = 8',
    'M.__dataclass_params__.eq = False',
))
def test_dataclass_metadata_mutation_is_still_checked(mutation):
    from behave.sim.core.failure import CandidateFault

    text = SOURCE.replace('self.total += record.items[0]', mutation)
    with IsolatedModel(text, cls='M', limits=TIGHT, snapshot_policy='on-demand') as instance:
        instance.reset()
        with pytest.raises(CandidateFault) as caught:
            instance.process(a=3)
        assert caught.value.refusal.kind == 'behavior-runtime-invariant'


@pytest.mark.parametrize('packaged', (False, True))
@pytest.mark.parametrize('alias', (False, True))
def test_source_default_factory_hits_belong_to_the_same_catalogue(packaged, alias):
    text = SOURCE.replace('@dataclass\nclass Value:', '''def factory():
    seed = 1
    if seed:
        return []
    return [9]

@dataclass
class Value:''').replace('default_factory=list', 'default_factory=factory')
    if alias:
        text = text.replace('@dataclass\nclass Value:', 'make = factory\n\n@dataclass\nclass Value:')
        text = text.replace('default_factory=factory', 'default_factory=make')
    text, file, model = prepared(text, packaged)
    plan = plan_source(source_model(text, file=file, cls=model, keyword_calls=True), text, file)
    namespace, _, hits = instrumented.load_source(text, file, goal_plan=plan)
    assert namespace[model]().process(-3) == {'y': 3}
    assert hits
    assert set(hits) <= set(plan.manifest)
    factory = next(node for node in ast.walk(ast.parse(text))
                   if isinstance(node, ast.FunctionDef) and node.name.endswith('factory'))
    outcomes = {goal.event: identifier for identifier, goal in plan.manifest.items()
                if identifier.startswith('branch:') and dict(goal.provenance)['line'] == factory.lineno + 2}
    assert set(outcomes) == {'T', 'F'}
    assert outcomes['T'] in hits and outcomes['F'] not in hits


def test_annotation_abc_image_tracks_registration_not_derived_caches():
    from abc import ABC

    from behave.sim.behavior.namespace_metadata import abc_registry

    class Interface(ABC):
        pass

    class Implementation:
        pass

    before = abc_registry(Interface, Interface._abc_impl)
    assert not isinstance(Implementation(), Interface)
    assert abc_registry(Interface, Interface._abc_impl) == before
    Interface.register(Implementation)
    assert abc_registry(Interface, Interface._abc_impl) != before


@pytest.mark.isolation
@pytest.mark.parametrize('mutation', (False, True))
def test_qualified_annotation_cache_and_public_binding_changes(mutation):
    from behave.sim.core.failure import CandidateFault

    text = '''import typing as tp
class M:
    def process(self, a):
        annotation = tp.Literal[a]
        MUTATION
        return {'y': a}
'''.replace('MUTATION', 'tp.List = tp.Dict' if mutation else 'pass')
    with IsolatedModel(text, cls='M', limits=TIGHT, snapshot_policy='on-demand') as instance:
        if mutation:
            with pytest.raises(CandidateFault) as caught:
                instance.process(a=3)
            assert caught.value.refusal.kind == 'behavior-runtime-invariant'
        else:
            assert instance.process(a=3) == {'y': 3}
            assert instance.process(a=5) == {'y': 5}
