"""Native byte packing is concrete execution, never an implicit exact primitive."""

import struct

import pytest

from behave.sim import api
from behave.sim.behavior.isolated import IsolatedModel, _source_protocol_image, hits_of

SOURCE = """import struct
class M:
    def reset(self): pass
    def process(self, in_a):
        if in_a > 3:
            return {'out_y': int.from_bytes(struct.pack('>f', float(in_a)), 'big')}
        return {'out_y': struct.unpack('>I', struct.pack('>f', float(in_a)))[0]}
"""


def test_public_module_profile_distinguishes_concrete_and_exact():
    profile = api.behavior_source_environment()
    assert "struct" in profile["approved_modules"]
    assert "struct" not in profile["exact_modules"]


@pytest.mark.isolation
@pytest.mark.parametrize("packaged", (False, True))
def test_native_module_and_named_import_share_real_execution_and_source_hits(packaged):
    text, file, cls = SOURCE, "behavior.py", "M"
    if packaged:
        bundle = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
            {"behavior.py": SOURCE.encode(), "unused.py": b"K = 0\n"},
            entry="behavior.py", model="M"))
        text, file, cls = bundle.source, bundle.file, bundle.model
    catalogue = api.CatalogueManifest.from_dict(
        _source_protocol_image(text, file, cls)["frontend_catalogue"])
    with IsolatedModel(text, file, cls=cls) as model:
        model.reset()
        for value in (0, 1, 4, 255):
            assert model.process(in_a=value) == {
                "out_y": int.from_bytes(struct.pack('>f', float(value)), 'big')}
        assert hits_of(model)
        assert set(hits_of(model)) <= set(catalogue)


@pytest.mark.parametrize("packaged", (False, True))
@pytest.mark.isolation
def test_numeric_native_builtins_and_generator_have_same_concrete_results(packaged):
    source = """from math import isqrt
class M:
    def reset(self): pass
    def process(self, in_a):
        values = list(map(int, (in_a, 2, 3)))
        packed = bytes(values)
        result = sum(x + isqrt(in_a) for x in packed if x > 1)
        return {'out_y': result}
"""
    bundle = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
        {"behavior.py": source.encode(), **({"unused.py": b"K = 0\n"} if packaged else {})},
        entry="behavior.py", model="M"))
    catalogue = api.CatalogueManifest.from_dict(
        _source_protocol_image(bundle.source, bundle.file, bundle.model)["frontend_catalogue"])
    with IsolatedModel(bundle.source, bundle.file, cls=bundle.model) as model:
        model.reset()
        for value in (0, 1, 4, 255):
            import math
            expected = sum(x + math.isqrt(value) for x in bytes((value, 2, 3)) if x > 1)
            assert model.process(in_a=value) == {"out_y": expected}
        assert hits_of(model) and set(hits_of(model)) <= set(catalogue)


@pytest.mark.parametrize("packaged", (False, True))
@pytest.mark.isolation
def test_wildcard_fp_library_native_execution_and_goal_catalogue(packaged):
    source = """from fp_lib import *
import math
class M:
    def reset(self): pass
    def process(self, in_a):
        if math.isfinite(1.0):
            return {'out_y': fp32_add(in_a, FP32_INF_POS)}
        return {'out_y': 0}
"""
    bundle = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
        {"behavior.py": source.encode(), **({"unused.py": b"K = 0\n"} if packaged else {})},
        entry="behavior.py", model="M"))
    catalogue = api.CatalogueManifest.from_dict(
        _source_protocol_image(bundle.source, bundle.file, bundle.model)["frontend_catalogue"])
    from behave.sim.behavior.fp_lib import fp32_add
    with IsolatedModel(bundle.source, bundle.file, cls=bundle.model) as model:
        model.reset()
        for value in (0, 0x3F800000, 0xFF800000):
            assert model.process(in_a=value) == {'out_y': fp32_add(value, 0x7F800000)}
        assert hits_of(model) and set(hits_of(model)) <= set(catalogue)


def test_concrete_numeric_vocabulary_does_not_claim_exact_module_support():
    from behave.sim.behavior.admission import calls_this_build_will_not_answer
    profile = api.behavior_source_environment()
    assert {'map', 'bytes'} <= set(profile['builtins'])
    assert {'struct', 'math'} <= set(profile['approved_modules'])
    assert not {'struct', 'math'} & set(profile['exact_modules'])
    assert calls_this_build_will_not_answer('from fp_lib import *\nfp32_add(0, 0)') == []
    assert calls_this_build_will_not_answer('from math import isqrt as root\nroot(4)') == []
    assert calls_this_build_will_not_answer('from fp_lib import *\nnot_a_real_function()')


def test_bare_variadic_entry_reports_its_actual_keyword_receivers():
    # Only task admission knows which named fields the entry fails to receive.
    facts = _source_protocol_image(
        "class M:\n def process(self, *args): return {'out_y': 0}\n", cls="M")["facts"]
    assert not facts["process_inputs"] and not facts["process_kwargs"]


@pytest.mark.parametrize("packaged", (False, True))
@pytest.mark.isolation
def test_wildcard_intrinsic_keeps_concrete_source_goal_binding(packaged):
    source = """from behave_intrinsics import *
class M:
    def reset(self): pass
    def process(self, in_a):
        return {'out_y': uwrap(in_a + 1, 4)}
"""
    prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
        {"behavior.py": source.encode(), **({"unused.py": b"K = 0\n"} if packaged else {})},
        entry="behavior.py", model="M"))
    catalogue = api.CatalogueManifest.from_dict(
        _source_protocol_image(prepared.source, prepared.file, prepared.model)["frontend_catalogue"])
    with IsolatedModel(prepared.source, prepared.file, cls=prepared.model) as model:
        model.reset()
        for value in (0, 1, 15, 255):
            assert model.process(in_a=value) == {'out_y': (value + 1) & 15}
        assert hits_of(model) and set(hits_of(model)) <= set(catalogue)
