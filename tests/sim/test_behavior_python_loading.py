"""Ordinary Python class/annotation syntax has one shared execution contract."""

import pytest

from behave.sim import api
from behave.sim.behavior import admission, instrumented
from behave.sim.core.failure import CandidateFault

SOURCE = '''class Model(object):
    def reset(self): pass
    def process(self, a: int) -> dict:
        return {'y': a + 1 if a < 7 else a - 1}
'''


def test_public_environment_describes_supported_python_class_and_future_syntax():
    environment = api.behavior_source_environment()
    assert 'object' in environment['builtins']
    assert environment['future_features'] == ['annotations']


def prepared_source(source, packaged):
    files = ({'entry.py': b'from model import Model\n', 'model.py': source.encode()}
             if packaged else {'entry.py': source.encode()})
    return api.prepare_behavior_bundle(api.BehaviorSourceBundle(files, 'entry.py', 'Model'))


@pytest.mark.parametrize('packaged', [False, True])
@pytest.mark.parametrize('source', [
    SOURCE + '    return None\n',
    SOURCE + 'return None\n',
    SOURCE.replace("return {'y': a + 1 if a < 7 else a - 1}", 'break'),
    SOURCE.replace("return {'y': a + 1 if a < 7 else a - 1}", 'continue'),
    SOURCE.replace("return {'y': a + 1 if a < 7 else a - 1}", 'nonlocal missing'),
    SOURCE + 'from __future__ import annotations\n',
])
def test_context_syntax_errors_belong_to_original_source_for_single_and_bundle_paths(source, packaged):
    with pytest.raises(SyntaxError) as original:
        compile(source, 'original.py', 'exec', dont_inherit=True, optimize=0)
    with pytest.raises(api.FailureRaised) as caught:
        prepared_source(source, packaged)
    failure = caught.value.failure
    assert failure.origin is api.FailureOrigin.CANDIDATE
    assert failure.reason.kind == 'behavior-source-syntax-error'
    assert failure.reason.detail['file'] == ('model.py' if packaged else 'entry.py')
    assert failure.reason.detail['line'] == original.value.lineno
    assert failure.reason.detail['detail'] == original.value.msg


def test_shared_syntax_validation_does_not_execute_source():
    from behave.sim.behavior.source_syntax import parsed_source

    source = SOURCE + '\nraise RuntimeError("must not execute during validation")\n'
    tree = parsed_source(source, 'original.py')
    assert tree.body[-1].exc.func.id == 'RuntimeError'


@pytest.mark.parametrize('packaged', [False, True])
@pytest.mark.parametrize('postponed', [False, True])
def test_class_base_and_annotations_load_with_identical_outputs_and_hits(packaged, postponed):
    source = SOURCE
    if postponed:
        source = 'from __future__ import annotations\n' + source.replace('a: int', 'a: Missing()')
    prepared = prepared_source(source, packaged)
    assert admission.calls_this_build_will_not_answer(prepared.source) == []
    assert admission.check(prepared.source) == []
    namespace, sites, hits = instrumented.load_source(prepared.source, file=prepared.file)
    model = namespace['Model']()
    model.reset()
    assert [model.process(a) for a in (0, 6, 7, 15)] == [
        {'y': 1}, {'y': 7}, {'y': 6}, {'y': 14}]
    assert sites and hits
    annotations = namespace['Model'].process.__annotations__
    assert annotations == ({'a': 'Missing()', 'return': 'dict'} if postponed
                           else {'a': int, 'return': dict})


@pytest.mark.parametrize('source,error,message,line', [
    (SOURCE.replace('object', 'MissingBase'), 'NameError', 'MissingBase', 1),
    ('import os\n' + SOURCE, 'ImportError', 'os', 1),
    (SOURCE.replace('a: int', 'a: MissingType'), 'NameError', 'MissingType', 3),
])
def test_load_fault_keeps_actionable_source_diagnostic(source, error, message, line):
    with pytest.raises(CandidateFault) as caught:
        instrumented.load_source(source, file='submitted.py', _attribute_startup_failure=True)
    detail = caught.value.refusal.detail
    assert caught.value.refusal.kind == 'candidate-load-failed'
    assert detail['error'] == error
    assert message in detail['message']
    assert detail['file'] == 'submitted.py'
    assert detail['line'] == line


def test_linker_does_not_mistake_a_shadowed_object_for_the_builtin():
    source = 'class object: pass\n' + SOURCE
    with pytest.raises(api.Refused, match='class-form'):
        prepared_source(source, True)


def test_future_semantics_do_not_leak_into_other_bundle_modules():
    helper = 'COUNT = []\ndef mark():\n COUNT.append(1)\n return int\ndef value(a: mark()): return a\n'
    source = 'from __future__ import annotations\nfrom helper import value\n' + SOURCE
    # The helper has an eager, effectful annotation. It remains outside the
    # static-link subset; the entry's directive cannot silently suppress it.
    with pytest.raises(api.Refused, match='definition-effects'):
        api.prepare_behavior_bundle(api.BehaviorSourceBundle(
            {'entry.py': source.encode(), 'helper.py': helper.encode()}, 'entry.py', 'Model'))


@pytest.mark.isolation
@pytest.mark.parametrize('packaged', [False, True])
def test_production_isolation_accepts_object_and_postponed_annotations(packaged):
    from behave.sim.behavior.isolated import isolated_model

    source = 'from __future__ import annotations\n' + SOURCE.replace('a: int', 'a: Missing()')
    prepared = prepared_source(source, packaged)
    factory = isolated_model(prepared.source, cls='Model')
    try:
        model = factory()
        model.reset()
        assert model.process(a=3) == {'y': 4}
        assert model.process(a=7) == {'y': 6}
    finally:
        factory.close()


@pytest.mark.isolation
@pytest.mark.parametrize('source,kind,message,line', [
    (SOURCE.replace('a: int', 'a: MissingType'), 'candidate-load-failed', 'MissingType', 3),
    (SOURCE.replace('    def reset', '    def __init__(self): self.value = MissingValue\n    def reset'),
     'candidate-construction-failed', 'MissingValue', 2),
])
def test_load_diagnostic_survives_the_production_worker_boundary(source, kind, message, line):
    from behave.sim.behavior.isolated import IsolatedModel

    with pytest.raises(CandidateFault) as caught, IsolatedModel(source, file='submitted.py', cls='Model'):
        pass
    detail = caught.value.refusal.detail
    assert caught.value.refusal.kind == kind
    assert detail['error'] == 'NameError'
    assert message in detail['message']
    assert detail['file'] == 'submitted.py' and detail['line'] == line


@pytest.mark.isolation
@pytest.mark.solver
@pytest.mark.parametrize('packaged', [False, True])
def test_annotation_and_object_support_preserves_source_ir_audit(packaged):
    from behave.sim.behavior import instance as bi
    from behave.sim.core.audit import AuditCheckStatus
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    source = 'from __future__ import annotations\n' + SOURCE.replace('a: int', 'a: Missing()')
    prepared = prepared_source(source, packaged)
    schema = TransactionSchema([Endpoint('a', 'input', 8), Endpoint('y', 'output', 8)],
        source='class/annotation fixture', authority=Authority.TASK, reading=Reading.ELABORATED)
    model = bi.lower(prepared.source, schema, file=prepared.file, bound=4)
    traces = (({'a': 0}, {'a': 6}, {'a': 7}, {'a': 255}),)
    assert model.source_audit(traces).status is AuditCheckStatus.AGREED
    assert model.audit(traces).status is AuditCheckStatus.AGREED
