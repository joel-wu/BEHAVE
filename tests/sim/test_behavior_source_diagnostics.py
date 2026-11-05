"""Capability facts stay canonical when several calls share a physical line."""
from behave.sim.behavior.admission import calls_this_build_will_not_answer
from behave.sim.behavior.isolated import _decode_source_protocol_image, _source_protocol_image
from behave.sim.behavior.instrumented import load_source

import pytest


def test_repeated_unprovided_calls_do_not_break_source_facts():
    source = """class M:
    def reset(self):
        self.x = 0
    def process(self, a):
        return {'q': missing(a) + missing(a), 'r': missing(a)}
"""
    facts = calls_this_build_will_not_answer(source)
    assert len(facts) == 1
    assert 'missing()' in facts[0]
    image = _source_protocol_image(source, cls='M')
    assert image['facts']['unanswerable_calls'] == facts
    _decode_source_protocol_image(image)


def test_distinct_source_lines_keep_their_own_diagnostics():
    facts = calls_this_build_will_not_answer('missing(1)\nmissing(2)\n')
    assert len(facts) == 2 and facts == sorted(facts)


def test_getattr_keeps_python_default_evaluation_and_persistent_state():
    source = """class M:
    def reset(self):
        self.count = 0
    def fallback(self):
        self.count += 1
        return 9
    def process(self, a):
        before = getattr(self, 'value', self.fallback())
        self.value = a
        return {'q': before, 'count': self.count}
"""
    assert calls_this_build_will_not_answer(source) == []
    plain = {}
    exec(source, plain)
    native, sites, hits = load_source(source)
    models = [namespace['M']() for namespace in (plain, native)]
    for model in models:
        model.reset()
    for value, expected in ((5, {'q': 9, 'count': 1}), (7, {'q': 5, 'count': 2})):
        assert [model.process(value) for model in models] == [expected, expected]
    assert sites and hits
    with pytest.raises(AttributeError):
        getattr(models[1], 'absent')


def test_source_can_shadow_getattr_without_changing_binding():
    source = """def getattr(obj, name, default):
    return 11
class M:
    def reset(self):
        pass
    def process(self, a):
        return {'q': getattr(self, 'value', a)}
"""
    namespace, _, _ = load_source(source)
    assert namespace['M']().process(5) == {'q': 11}
