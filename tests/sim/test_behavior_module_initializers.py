"""Closed initializer calls keep module ownership and source execution order."""

import pytest

from behave.sim import api

HELPER = '''def signed(x, width):
    x &= (1 << width) - 1
    return x - (1 << width) if x & (1 << (width - 1)) else x
'''


@pytest.mark.parametrize('form', ['local', 'imported', 'qualified'])
def test_module_numeric_helper_initializer_is_not_guessed_or_precomputed(form):
    if form == 'local':
        files, prefix, call = {}, HELPER, 'signed'
    elif form == 'imported':
        files, prefix, call = {'helper.py': HELPER.encode()}, 'from helper import signed as sx\n', 'sx'
    else:
        files, prefix, call = {'helper.py': HELPER.encode()}, 'import helper as h\n', 'h.signed'
    source = prefix + f'''VALUES = [{call}(15, 4), {call}(x=7, width=4)]
EXTRA: int = {call}(14, 4)
class Chosen:
    def reset(self): pass
    def process(self, a): return {{'y': a + VALUES[0] + VALUES[1] + EXTRA}}
'''
    files['model.py'] = source.encode()
    files['entry.py'] = b'from model import Chosen\n'
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, 'entry.py', 'Chosen'))
    namespace = {'__name__': 'behavior'}
    exec(compile(prepared.source, prepared.file, 'exec'), namespace)  # noqa: S102 - fixed fixture
    assert namespace['Chosen']().process(8) == {'y': 12}
    # Linking retains the helper invocation; it does not execute submitted
    # functions in the parent process or substitute hand-computed constants.
    assert '(15, 4)' in prepared.source


def test_initializer_calls_execute_once_in_original_order_and_preserve_aliases():
    source = '''LOG = []
def record(x):
    LOG.append(x)
    return len(LOG)
FIRST = record(7)
SECOND = record(9)
class Chosen:
    def reset(self): pass
    def process(self, a): return {'y': FIRST * 100 + SECOND * 10 + LOG[0] + a}
'''
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
        {'entry.py': b'from model import Chosen\n', 'model.py': source.encode()}, 'entry.py', 'Chosen'))
    namespace = {'__name__': 'behavior'}
    exec(compile(prepared.source, prepared.file, 'exec'), namespace)  # noqa: S102 - fixed fixture
    assert namespace['Chosen']().process(3) == {'y': 130}


@pytest.mark.parametrize('form', ['imported', 'qualified', 'library'])
def test_imports_after_declarations_preserve_native_results(tmp_path, monkeypatch, form):
    from tests.infrastructure.test_behavior_source_bundle import linked, native

    files = {'helper.py': b'def adjust(x): return x * 2\n'}
    declaration, call = ('from helper import adjust', 'adjust(x)')
    if form == 'qualified':
        declaration, call = 'import helper as h', 'h.adjust(x)'
    elif form == 'library':
        declaration, call = 'from math import isqrt', 'isqrt(x)'
    files['model.py'] = f'''OFFSET = 3
def step(x): return {call} + OFFSET
class M:
    def process(self, a): return {{'y': step(a)}}
{declaration}
'''.encode()
    bundle = api.BehaviorSourceBundle(files, 'model.py', 'M')
    before = native(bundle, tmp_path, monkeypatch)
    after, _ = linked(bundle)
    assert [after.process(a) for a in range(32)] == [before.process(a) for a in range(32)]


@pytest.mark.parametrize('prefix', [
    'VALUE = adjust\n',
    'def before(x=adjust): return x\n',
    'class Before:\n    value = adjust\n',
    'def before(): return 1\nVALUE = before()\n',
])
def test_late_imports_do_not_move_a_binding_across_eager_reads_or_calls(prefix):
    source = prefix + '''from helper import adjust
class Chosen:
    def process(self, a): return {'y': adjust(a)}
'''
    with pytest.raises(api.Refused, match='late-import-effects'):
        api.prepare_behavior_bundle(api.BehaviorSourceBundle(
            {'model.py': source.encode(), 'helper.py': b'def adjust(x): return x\n'}, 'model.py', 'Chosen'))


def test_late_dependency_initialization_cannot_move_before_an_earlier_value_copy():
    files = {
        'state.py': b'values = []\n',
        'mutator.py': b'from state import values\ndef add():\n values.append(7)\n return 1\nDONE=add()\n',
        'model.py': b'from state import values\nCOPY=values+[1]\nfrom mutator import DONE\n'
                    b'class Chosen:\n def process(self,a): return {"y":COPY[0]}\n',
    }
    with pytest.raises(api.Refused, match='late-import-effects'):
        api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, 'model.py', 'Chosen'))


@pytest.mark.parametrize('prefix', [
    "VALUE = helper(1)\ndef helper(x): return x\n",
    "VALUE = eval('3')\n",
    "VALUE = (lambda x: x)(1)\n",
    "def helper(x): return x\ndef f(x=helper(1)): return x\n",
    "def helper(x): return x\nclass Other:\n VALUE = helper(1)\n",
])
def test_dynamic_or_definition_contexts_are_not_silently_admitted(prefix):
    source = prefix + "class Chosen:\n def process(self, a): return {'y': a}\n"
    with pytest.raises(api.Refused):
        api.prepare_behavior_bundle(api.BehaviorSourceBundle(
            {'entry.py': b'from model import Chosen\n', 'model.py': source.encode()}, 'entry.py', 'Chosen'))


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('packaged', [False, True])
def test_initializer_table_values_and_step_goal_hits_match_native_and_ir(packaged):
    from behave.sim.behavior import instance as bi
    from behave.sim.core.audit import AuditCheckStatus
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    source = b'''def clip(x):
    if x < 0: return 0
    if x > 15: return 15
    return x
TABLE = [clip(-2), clip(3), clip(20)]
class Chosen:
    def reset(self): pass
    def process(self, a):
        if a == 7: return {'y': clip(a) + TABLE[1]}
        return {'y': TABLE[a % 3]}
'''
    files = ({'entry.py': b'from model import Chosen\n', 'model.py': source}
             if packaged else {'entry.py': source})
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, 'entry.py', 'Chosen'))
    schema = TransactionSchema([Endpoint('a', 'input', 8), Endpoint('y', 'output', 8)],
        source='module initializer fixture', authority=Authority.TASK, reading=Reading.ELABORATED)
    model = bi.lower(prepared.source, schema, file=prepared.file, bound=3)
    traces = (({'a': 0}, {'a': 7}, {'a': 255}), ({'a': 2}, {'a': 3}))
    assert model.source_audit(traces).status is AuditCheckStatus.AGREED
    assert model.audit(traces).status is AuditCheckStatus.AGREED
