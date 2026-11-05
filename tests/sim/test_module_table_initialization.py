"""Module initialization preserves Python values, aliases, failures and order."""

import pytest

from behave.sim import api
from tests.infrastructure.test_behavior_source_bundle import linked, native


@pytest.mark.parametrize('initialization', [
    'TABLE = list((3, 5, 7))\nassert len(TABLE) == 3',
    'TABLE = [0] * 3\nfor i in range(3):\n TABLE[i] = adjust(i)\nassert i == 2',
    'TABLE = [adjust(i) for i in range(3)]',
    'TABLE = [adjust(x + y) for (x, y) in ((0, 1), (1, 2), (2, 3))]',
    'TABLE = [0] * 3\nALIAS = TABLE\nfor i in range(3):\n ALIAS[i] = adjust(i)',
])
def test_initializers_match_original_module_execution(tmp_path, monkeypatch, initialization):
    source = api.BehaviorSourceBundle({
        'helper.py': ('def adjust(x): return x * 2 + 1\n' + initialization + '\n').encode(),
        'model.py': b'from helper import TABLE\nclass M:\n def process(self,a): return {"y":TABLE[a]}\n',
    }, 'model.py', 'M')
    before = native(source, tmp_path, monkeypatch)
    after, prepared = linked(source)
    assert [before.process(i) for i in range(3)] == [after.process(i) for i in range(3)]
    assert 'behave.behavior-source-link/4' in prepared.source


def test_assertion_failure_is_retained_not_erased_or_run_by_linker():
    bundle = api.BehaviorSourceBundle({
        'helper.py': b'TABLE = [3, 5]\nassert len(TABLE) == 3, "wrong table"\n',
        'model.py': b'from helper import TABLE\nclass M:\n def process(self,a): return {"y":a}\n',
    }, 'model.py', 'M')
    prepared = api.prepare_behavior_bundle(bundle)
    with pytest.raises(AssertionError, match='wrong table'):
        exec(compile(prepared.source, prepared.file, 'exec'), {})  # noqa: S102 - fixed fixture


@pytest.mark.parametrize('initialization', [
    'assert False\nfrom other import X\n',
    'TABLE = list((1, 2))\nlist = (3, 4)\n',
    'TABLE = [adjust(0) for adjust in (1, 2)]\n',
    'for adjust in range(2):\n pass\n',
    'for i in range(2):\n adjust = i\n',
    'TABLE = [0]\nfor i in range(2):\n TABLE[0] = eval("3")\n',
])
def test_dynamic_namespace_and_moved_import_effects_remain_refused(initialization):
    bundle = api.BehaviorSourceBundle({
        'helper.py': ('def adjust(x): return x\n' + initialization).encode(),
        'other.py': b'X = 1\n',
        'model.py': b'import helper\nclass M:\n def process(self,a): return {"y":a}\n',
    }, 'model.py', 'M')
    with pytest.raises(api.Refused):
        api.prepare_behavior_bundle(bundle)


@pytest.mark.isolation
def test_public_isolated_realization_accepts_retained_initialization():
    from tests.sim.test_behavior_public_exact_construction import _contract

    bundle = api.BehaviorSourceBundle({
        'helper.py': b'def adjust(x): return x + 1\nTABLE = list((0, 0, 0))\n'
                     b'for i in range(3):\n TABLE[i] = adjust(i)\nassert len(TABLE) == 3\n',
        'model.py': b'from helper import TABLE\nclass M:\n def reset(self): pass\n'
                    b' def process(self,a): return {"y":TABLE[a % 3]}\n',
    }, 'model.py', 'M')
    prepared = api.prepare_behavior_bundle(bundle)
    assert api.realization_of_behavior_source(prepared.source, _contract(), model='M', file=prepared.file)


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('initialization', [
    'TABLE = list((1, 3, 5))',
    'TABLE = [0]*3\nfor i in range(3):\n TABLE[i] = adjust(i)',
    'TABLE = [1]*3\nfor i in range(1,3):\n TABLE[i] = TABLE[i-1] + 2',
    'TABLE = [0]*3\nfor i in range(2,-1,-1):\n TABLE[i] = adjust(i)',
    'TABLE = [adjust(i) for i in range(3)]',
    'TABLE = tuple(range(1, 7, 2))',
    'TABLE = [adjust(x + y) for (x, y) in ((0, 0), (0, 1), (1, 1))]',
    'TABLE = [adjust(i) for i in range(5) if i < 3]',
    'def width(x):\n assert x > 0\n return x.bit_length()\nTABLE = [width(1 << i) for i in range(3)]',
])
def test_finite_table_initialization_matches_native_and_exact_execution(initialization):
    from behave.sim.behavior import instance
    from behave.sim.core.audit import AuditCheckStatus
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    bundle = api.BehaviorSourceBundle({
        'helper.py': ('def adjust(x): return x * 2 + 1\n' + initialization + '\n'
                      'assert len(TABLE) == 3\n').encode(),
        'model.py': b'from helper import TABLE\nclass M:\n def reset(self): pass\n'
                    b' def process(self,a): return {"y":TABLE[a % 3]}\n',
    }, 'model.py', 'M')
    prepared = api.prepare_behavior_bundle(bundle)
    schema = TransactionSchema([Endpoint('a', 'input', 8), Endpoint('y', 'output', 8)],
                              source='module table fixture', authority=Authority.TASK, reading=Reading.ELABORATED)
    model = instance.lower(prepared.source, schema, file=prepared.file, bound=3)
    traces = (({'a': 0}, {'a': 1}, {'a': 2}), ({'a': 3}, {'a': 255}))
    assert model.source_audit(traces).status is AuditCheckStatus.AGREED
    assert model.audit(traces).status is AuditCheckStatus.AGREED


def test_module_initializer_extension_does_not_relax_class_definition_rules():
    bundle = api.BehaviorSourceBundle({
        'model.py': b'import helper\nclass M:\n TABLE = [x for x in (1,2)]\n'
                    b' def process(self,a): return {"y":a}\n',
        'helper.py': b'VALUE = 1\n',
    }, 'model.py', 'M')
    with pytest.raises(api.Refused, match='definition-effects'):
        api.prepare_behavior_bundle(bundle)


@pytest.mark.isolation
@pytest.mark.parametrize('effect,reset', [
    ('ALIAS = TABLE\nALIAS[0] = 7', 'pass'),
    ('TABLE.append(7)', 'pass'),
    ('', 'TABLE[0] = 7'),
    ('for j in range(2):\n TABLE[j] += 1', 'pass'),
    ('for j in range(2):\n TABLE[j] = adjust(j)\ndef adjust(x): return j', 'pass'),
])
def test_initialized_table_keeps_closed_read_only_obligation(effect, reset):
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = ('TABLE = list((0,0))\nfor i in range(2):\n TABLE[i] = i\n' + effect + '\n'
              'class M:\n def reset(self):\n  ' + reset + '\n'
              ' def process(self,a): return {"y":TABLE[a]}\n')
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[1])
    assert caught.value.code == 'module-table-not-static'


@pytest.mark.isolation
@pytest.mark.parametrize('alias', ['TABLE', '[TABLE]'])
def test_alias_of_initialized_table_cannot_keep_a_stale_image(alias):
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    read = 'ALIAS[a]' if alias == 'TABLE' else 'ALIAS[0][a]'
    source = ('TABLE = [0]*2\nALIAS = ' + alias + '\n'
              'for i in range(2):\n TABLE[i] = i + 1\n'
              'class M:\n def reset(self): pass\n'
              ' def process(self,a): return {"y":' + read + '}\n')
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[1])
    assert caught.value.code == 'module-table-not-static'


def test_initializer_helper_unbound_local_is_not_a_module_constant():
    from behave.sim.behavior.extract import ScopeResolver, source_model

    source = '''X = 9
def value(flag):
 if flag: X = 3
 return X
TABLE = [value(False)]
class M:
 def reset(self): pass
 def process(self,a): return {"y":TABLE[a]}
'''
    resolver = ScopeResolver(source_model(source))
    assert 'TABLE' not in resolver.tables


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('initialization', [
    'TABLE = list((1,0))',
    'TABLE = [1-i for i in range(2)]',
    'TABLE = [0]*2\nfor i in range(2):\n TABLE[i] = 1-i',
])
def test_public_analysis_classifies_initialized_table_goals(tmp_path, initialization):
    from tests.sim.test_behavior_public_exact_construction import _run_public_behavior_command

    source = initialization + '''
assert len(TABLE) == 2
class M:
 def reset(self): pass
 def process(self,a):
  if TABLE[a] == 1: return {"y":1}
  if TABLE[a] == 2: return {"y":1}
  return {"y":0}
'''
    result, _ = _run_public_behavior_command(tmp_path, source)
    assert result.evaluation.passed, result.as_dict()
    assert result.analysis.verdict is api.Verdict.CLASSIFIED, result.analysis.as_dict()
    assert result.analysis.concrete == result.evaluation.fingerprint
    assert {entry['status'] for entry in result.analysis.per_goal.values()} == {
        'reached', 'bounded-unreachable'}
