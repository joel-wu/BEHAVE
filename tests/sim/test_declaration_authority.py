"""Adversarial tests for source declarations consumed by the frontend.

These are deliberately separate from the native-oracle corpus.  The subject
here is the boundary *before* lowering: which source binding owns a constant,
array shape, or table.  An admitted case must execute identically; a source the
declaration analysis cannot represent must refuse for the declaration rule,
not merely fail at some later and unrelated gate.
"""

import pytest

from behave.sim.behavior import LIBRARY_DIR, extract
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError
from behave.sim.behavior.isolated import Broken, Limits, facts_of
from behave.sim.behavior.lowering import lower_with_state_ranges

WIDTH = 4
TIGHT = Limits(seconds=5, memory_bytes=1 << 30, wall_seconds=10)


def _native(source, **inputs):
    import sys

    namespace = {}
    sys.path.insert(0, str(LIBRARY_DIR))
    try:
        exec(source, namespace)  # noqa: S102 - the source is the test fixture
    finally:
        sys.path.pop(0)
    model = namespace["M"]()
    model.reset()
    return model.process(**inputs)


def _lowered(source, **inputs):
    proc = lower_with_state_ranges(
        source, input_widths=[WIDTH] * max(1, len(inputs)))
    _state, output, _hits, error = interp.run_step(proc, inputs)
    assert not error
    return output.transactions


def _agrees(source, **inputs):
    native = _native(source, **inputs)
    expected = [] if native is None else ([native] if isinstance(native, dict)
                                           else list(native))
    assert _lowered(source, **inputs) == expected


def _refuses(source, code):
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(source, input_widths=[WIDTH])
    assert caught.value.code == code


def test_augmented_state_read_keeps_the_instance_namespace():
    source = """X = 7
class M:
    def reset(self):
        self.X = 1
    def process(self, a):
        self.X += a
        return {"y": self.X}
"""
    assert _native(source, a=1) == {"y": 2}
    _agrees(source, a=1)


@pytest.mark.parametrize("effect, expected", [
    ("TABLE.update({0: 2})", {"y": 2}),
    ("TABLE |= {0: 2}", {"y": 2}),
    ("del TABLE[0]", KeyError),
    ("ALIAS = TABLE\nALIAS[0] = 2", {"y": 2}),
    ("def mutate(value):\n    value[0] = 2\nmutate(TABLE)", {"y": 2}),
    ("def mutate(value=TABLE):\n    value[0] = 2\nmutate()", {"y": 2}),
    ("if True:\n    TABLE = {0: 2}", {"y": 2}),
])
def test_module_table_requires_a_closed_read_only_binding(effect, expected):
    source = f"""TABLE = {{0: 1}}
{effect}
class M:
    def reset(self):
        pass
    def process(self, a):
        return {{"y": TABLE[0]}}
"""
    if isinstance(expected, type) and issubclass(expected, Exception):
        with pytest.raises(expected):
            _native(source, a=0)
    else:
        assert _native(source, a=0) == expected
    _refuses(source, "module-table-not-static")


def test_module_table_analysis_is_owner_aware_not_spelling_based():
    source = """K = 0
TABLE = {K: 7}
def helper():
    TABLE = {0: 3}
    TABLE[0] = 4
    return TABLE[0]
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": TABLE[a]}
"""
    _agrees(source, a=0)


def test_read_only_module_sequences_are_finite_roms():
    source = """ROWS = ((1, 2), (3, 4))
class M:
    def reset(self):
        pass
    def process(self, a, b):
        return {"y": ROWS[a][b]}
"""
    for a, b in ((0, 0), (0, 1), (1, 0), (1, 1)):
        _agrees(source, a=a, b=b)


@pytest.mark.parametrize("effect, expected", [
    ("TABLE[0] = 9", 9),
    ("TABLE.append(9)", 1),
    ("ALIAS = TABLE\nALIAS[0] = 9", 9),
])
def test_module_sequence_rom_requires_a_closed_read_only_binding(
        effect, expected):
    source = f"""TABLE = [1, 2]
{effect}
class M:
    def reset(self):
        pass
    def process(self, a):
        return {{"y": TABLE[0]}}
"""
    assert _native(source, a=0) == {"y": expected}
    _refuses(source, "module-table-not-static")


def test_constructor_constants_are_resolved_at_their_occurrence():
    rebound = """class M:
    def __init__(self, n=2):
        n = 3
        self.buf = [0] * n
    def reset(self):
        pass
    def process(self, a):
        return {"y": self.buf[a]}
"""
    _agrees(rebound, a=2)

    definition_scope = """N = 3
class M:
    def __init__(self, n=N):
        self.buf = [0] * n
    def reset(self):
        pass
    def process(self, a):
        return {"y": self.buf[a]}
"""
    _agrees(definition_scope, a=2)

    rebound_after_definition = """N = 2
class M:
    def __init__(self, n=N):
        self.buf = [0] * n
    def reset(self):
        pass
    def process(self, a):
        return {"y": self.buf[a]}
N = 3
"""
    model = extract.source_model(rebound_after_definition)
    resolver = extract.ScopeResolver(model)
    assert extract._model_arrays(
        model, resolver.occurrence_owner()) == {"buf": (2,)}
    _agrees(rebound_after_definition, a=1)

    resolved_reset_effect = """class M:
    def __init__(self, n=2):
        self.N = n
        self.reset()
    def reset(self):
        self.buf = [0] * self.N
    def process(self, a):
        return {"y": self.buf[a]}
"""
    model = extract.source_model(resolved_reset_effect)
    resolver = extract.ScopeResolver(model)
    assert resolver.self_consts == {"N": 2}
    assert extract._model_arrays(
        model, resolver.occurrence_owner()) == {"buf": (2,)}
    _agrees(resolved_reset_effect, a=1)


def test_authenticated_construction_shape_uses_runtime_name_resolution():
    source = """def range(n):
    return [0, 1, 2]
class M:
    def reset(self):
        self.buf = [7 for _ in range(1)]
    def process(self, a):
        y = 0
        for value in self.buf:
            y += value
        return {"y": y + (a & 0)}
"""
    assert _native(source, a=0) == {"y": 21}
    _agrees(source, a=0)


def test_class_body_constants_use_the_module_value_at_class_definition():
    source = """N = 2
class M:
    K = N
    def reset(self):
        pass
    def process(self, a):
        return {"y": self.K + a}
N = 3
"""
    assert facts_of(source, cls="M", limits=TIGHT)["cls_name"] == "M"
    _agrees(source, a=0)


@pytest.mark.parametrize("setup", [
    ("def __init__(self):\n        self.K = 2\n"
     "    def reset(self):\n        pass"),
    "def reset(self):\n        self.K = 2",
])
def test_an_instance_field_shadows_a_same_named_class_constant(setup):
    source = f"""class M:
    K = 1
    {setup}
    def process(self, a):
        return {{"y": self.K + a}}
"""
    assert _native(source, a=3) == {"y": 5}
    _agrees(source, a=3)


def test_function_body_globals_use_the_final_module_value():
    source = """N = 2
def value():
    return N
N = 3
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": value() + a}
"""
    _agrees(source, a=0)


def test_an_unrelated_module_call_does_not_erase_integer_facts():
    source = """K = 7
def build():
    return [1, 2]
LUT = build()
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": K + a}
"""
    _agrees(source, a=1)


def test_a_called_global_write_invalidates_the_old_integer_fact():
    source = """K = 7
def mutate():
    global K
    K = 9
mutate()
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": K + a}
"""
    assert _native(source, a=1) == {"y": 10}
    _refuses(source, "name-not-a-value")


def test_a_class_body_global_write_invalidates_the_module_fact():
    source = """N = 2
class Other:
    global N
    N = 3
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": N + a}
"""
    assert _native(source, a=0) == {"y": 3}
    _refuses(source, "name-not-a-value")


def test_declarations_are_confined_to_the_selected_model():
    source = """class M:
    def reset(self):
        self.buf = [0, 0]
    def process(self, a):
        return {"y": self.buf[a]}

class Other:
    def reset(self):
        self.buf = [0, 0, 0, 0, 0]
"""
    _agrees(source, a=1)


def test_an_unrelated_class_table_does_not_invalidate_the_module_table():
    source = """TABLE = {0: 7}
class Other:
    TABLE = {0: 3}
    first = TABLE[0]
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": TABLE[a]}
"""
    _agrees(source, a=0)


def test_comprehension_targets_do_not_rebind_enclosing_names():
    module_scope = """def helper():
    return 4
xs = [helper for helper in range(1)]
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": helper()}
"""
    _agrees(module_scope, a=0)

    class_scope = """class M:
    K = 3
    def reset(self):
        pass
    def process(self, a):
        return {"y": self.K + a}
    xs = [process for process in range(1)]
    ys = [K for K in range(1)]
"""
    _agrees(class_scope, a=2)


def test_isolation_uses_the_frontend_class_constant_authority():
    honest_integer = """class M:
    K = 1
    def reset(self):
        pass
    def process(self, a):
        return {"y": self.K + a}
"""
    # This assertion is about class-binding authority.  A Boolean used as an
    # Integer operand is outside SolverIR's typed primitive contract and is not
    # an honest witness for that separate property.
    assert facts_of(honest_integer, cls="M", limits=TIGHT)["cls_name"] == "M"
    _agrees(honest_integer, a=2)

    mutated_import = """from fp_lib import FP32_MANT_W as W
class M:
    K = W
    def reset(self):
        pass
    def process(self, a):
        return {"y": self.K}
M.K = 99
"""
    assert _native(mutated_import, a=0) == {"y": 99}
    with pytest.raises((Broken, ValueError)):
        facts_of(mutated_import, cls="M", limits=TIGHT)


def test_a_rebound_import_uses_its_final_integer_value():
    source = """from fp_lib import FP32_MANT_W as W
W = 99
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": W}
"""
    assert _native(source, a=0) == {"y": 99}
    _agrees(source, a=0)


def test_a_module_sequence_uses_the_static_collection_rule():
    source = """VALUES = [3, 5]
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": VALUES[a]}
"""
    _agrees(source, a=1)


def test_a_flat_record_payload_uses_the_nested_collection_rule():
    source = """VALUES = {0: (3, 5)}
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": VALUES[a][0]}
"""
    _agrees(source, a=0)


@pytest.mark.parametrize("declaration, lookup", [
    ("VALUES = [7]", "VALUES[0]"),
    ("VALUES = {0: 7}", "VALUES[0]"),
])
def test_module_collections_and_same_named_state_keep_distinct_namespaces(
        declaration, lookup):
    source = f"""{declaration}
class M:
    def reset(self):
        self.VALUES = [1]
    def process(self, a):
        return {{"y": {lookup} + self.VALUES[0] + (a & 0)}}
"""
    _agrees(source, a=0)


def test_read_only_table_membership_uses_the_comparison_rule():
    source = """VALUES = {0: 3, 1: 5}
class M:
    def reset(self):
        pass
    def process(self, a):
        return {"y": 1 if a in VALUES else 0}
"""
    _agrees(source, a=1)
