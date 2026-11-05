"""The explicit source boundary for fixed-width Behavior primitives."""

import ast
import itertools
from dataclasses import replace

import pytest

from behave.sim.behavior import audit, goals, instrumented
from behave.sim.symbolic import encoder, interp, ir, primitives
from behave.sim.behavior.admission import admitted, check
from behave.sim.behavior.instrumented import _allow_approved_imports
from behave.sim.behavior.lowering import LoweringError, lower_with_state_ranges

FIXED = frozenset({
    "uwrap", "swrap", "usat", "ssat",
    "ushl", "sshl", "ulshr", "sashr",
    "ult", "ule", "ugt", "uge", "slt", "sle", "sgt", "sge",
})


def _model(expression, imports="uwrap"):
    return (f"from behave_intrinsics import {imports}\n"
            "class M:\n"
            "    def reset(self):\n"
            "        pass\n"
            "    def process(self, x, n):\n"
            f"        return {{'y': {expression}}}\n")


def test_the_registry_is_the_authority_for_the_source_intrinsic_surface():
    assert primitives.source_intrinsic_names() == FIXED
    for symbol in FIXED:
        spec = primitives.source_intrinsic(symbol)
        assert spec is primitives.primitive(symbol)
        assert spec.source_symbol == symbol

    _allow_approved_imports()
    import behave_intrinsics

    assert frozenset(behave_intrinsics.__all__) == FIXED
    for width in range(1, 5):
        for value, amount in itertools.product(
                range(-(1 << width), 1 << width), range(width + 2)):
            for symbol in ("uwrap", "swrap", "usat", "ssat"):
                assert getattr(behave_intrinsics, symbol)(value, width) == \
                    primitives.source_intrinsic(symbol).denotation(
                        value, width)
            for symbol in ("ushl", "sshl", "ulshr", "sashr"):
                assert getattr(behave_intrinsics, symbol)(
                    value, amount, width) == \
                    primitives.source_intrinsic(symbol).denotation(
                        value, amount, width)
        for left, right in itertools.product(range(1 << width), repeat=2):
            for symbol in ("ult", "ule", "ugt", "uge",
                           "slt", "sle", "sgt", "sge"):
                assert getattr(behave_intrinsics, symbol)(
                    left, right, width) == \
                    primitives.source_intrinsic(symbol).denotation(
                        left, right, width)

    with pytest.raises(primitives.PrimitiveRegistryError,
                       match="both operator and intrinsic"):
        replace(primitives.primitive("add"), source_symbol="fixed_add")
    with pytest.raises(primitives.PrimitiveRegistryError,
                       match="invalid source symbol"):
        replace(primitives.primitive("uwrap"), source_symbol="not-a-name")


def test_only_named_module_scope_imports_enter_the_source_language():
    assert admitted("from behave_intrinsics import uwrap, slt as less\n")

    bad = {
        "import behave_intrinsics\n": "named from-imports",
        ("import behave_intrinsics as bv\n"
         "def f(x):\n"
         "    return bv.uwrap(x, 4)\n"): "named from-imports",
        "from behave_intrinsics import *\n": "not a registered",
        "from behave_intrinsics import imaginary\n": "not a registered",
        ("from behave_intrinsics import uwrap as _behave_hit\n"):
            "reserved by the Behavior runtime",
        ("def f():\n"
         "    from behave_intrinsics import uwrap\n"): "module scope",
    }
    for source, reason in bad.items():
        refusals = check(source)
        assert refusals and any(reason in refusal.reason
                                for refusal in refusals)


def test_aliases_lower_to_registry_ops_and_keep_ordinary_add_mathematical():
    source = (
        "from behave_intrinsics import uwrap as wrap, slt, sashr\n"
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, x, n):\n"
        "        return {'uw': wrap(x + 1, 4),\n"
        "                'lt': int(slt(x, 0, 4)),\n"
        "                'sh': sashr(x, n, 4),\n"
        "                'math': x + 1}\n")
    proc = lower_with_state_ranges(
        source, input_widths=[4, 3], read_initial={})
    operations = [node.op for node in ir.expression_nodes(proc.body)
                  if isinstance(node, ir.Op)]
    assert {"uwrap", "slt", "sashr"} <= set(operations)
    assert "add" in operations

    namespace, _sites, _hits = instrumented.load_source(source)
    native = namespace["M"]().process(15, 1)
    assert native == {"uw": 0, "lt": 1, "sh": -1, "math": 16}

    _post, output, _hits, error = interp.run_step(
        proc, {"x": 15, "n": 1})
    assert not error
    assert output.transactions == [{
        "uw": 0, "lt": 1, "sh": -1, "math": 16,
    }]
    assert audit.step_audit(
        proc, proc.state_ranges,
        [[{"x": value, "n": amount}]
         for value in range(16) for amount in range(6)],
        workers=1,
    ) == []


def test_a_construction_parameter_is_a_static_intrinsic_width():
    source = (
        "from behave_intrinsics import ssat, swrap, usat, uwrap\n"
        "class M:\n"
        "    def __init__(self, width=4):\n"
        "        self.width = width\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, x):\n"
        "        return {'u': uwrap(x, self.width),\n"
        "                's': swrap(x, self.width),\n"
        "                'us': usat(x, self.width),\n"
        "                'ss': ssat(x, self.width)}\n")
    proc = lower_with_state_ranges(source, input_widths=[5])
    operations = [node for node in ir.expression_nodes(proc.body)
                  if isinstance(node, ir.Op) and node.op in {
                      "uwrap", "swrap", "usat", "ssat"}]
    assert len(operations) == 4
    assert all(isinstance(node.args[1], ir.Lit)
               and node.args[1].value == 4 for node in operations)
    _post, output, _hits, error = interp.run_step(proc, {"x": 31})
    assert not error
    assert output.transactions == [{"u": 15, "s": -1,
                                    "us": 15, "ss": 7}]


def test_a_same_named_helper_is_not_recognised_as_an_intrinsic():
    source = (
        "def uwrap(value, width):\n"
        "    return value + 100\n"
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, x):\n"
        "        return {'y': uwrap(x, 4)}\n")
    proc = lower_with_state_ranges(
        source, input_widths=[4], read_initial={})
    assert not any(isinstance(node, ir.Op) and node.op == "uwrap"
                   for node in ir.expression_nodes(proc.body))
    assert interp.run_step(proc, {"x": 1})[1].transactions == [{"y": 101}]

    same_body = (
        "def my_wrap(value, width):\n"
        "    return value % (1 << width)\n"
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, x):\n"
        "        return {'y': my_wrap(x, 4)}\n")
    proc = lower_with_state_ranges(
        same_body, input_widths=[5], read_initial={})
    assert not any(isinstance(node, ir.Op) and node.op == "uwrap"
                   for node in ir.expression_nodes(proc.body))
    assert interp.run_step(proc, {"x": 17})[1].transactions == [{"y": 1}]


def test_canonical_fixed_width_helpers_have_one_exact_summary():
    source = (
        "def unsigned(value, width):\n"
        "    return value & ((1 << width) - 1)\n"
        "def signed(value, width):\n"
        "    value &= (1 << width) - 1\n"
        "    if value & (1 << (width - 1)):\n"
        "        value -= 1 << width\n"
        "    return value\n"
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, x):\n"
        "        return {'u': unsigned(x, 4), 's': signed(x, 4)}\n"
    )
    proc = lower_with_state_ranges(
        source, input_widths=[5], read_initial={})
    operations = [node.op for node in ir.expression_nodes(proc.body)
                  if isinstance(node, ir.Op)]
    assert operations.count("uwrap") == 1
    assert operations.count("swrap") >= 1

    # Summarising the data path must not summarise away source observations.
    assert set(goals.goal_set(source)) <= ir.goals_of(proc.body)
    namespace, _sites, native_hits = instrumented.load_source(source)
    traces = [[{"x": value}] for value in range(32)]
    assert audit.model_audit(
        proc, namespace["M"], traces, native_hits) == []
    assert audit.step_audit(
        proc, proc.state_ranges, traces, workers=1) == []
    assert encoder.verify_encoding(proc, proc.state_ranges) == []


@pytest.mark.solver
@pytest.mark.parametrize("value", ["x != 0", "True if x else 2"])
def test_fixed_width_helper_summary_preserves_python_numeric_promotion(value):
    source = (
        "def unsigned(value, width):\n"
        "    return value & ((1 << width) - 1)\n"
        "def signed(value, width):\n"
        "    value &= (1 << width) - 1\n"
        "    if value & (1 << (width - 1)):\n"
        "        value -= 1 << width\n"
        "    return value\n"
        "class M:\n"
        "    def reset(self):\n"
        "        pass\n"
        "    def process(self, x):\n"
        f"        return {{'u': unsigned({value}, 1), "
        f"'s': signed({value}, 1)}}\n"
    )
    proc = lower_with_state_ranges(source, input_widths=[1], read_initial={})
    namespace, _sites, hits = instrumented.load_source(source)
    traces = [[{"x": value}] for value in (0, 1)]
    assert audit.model_audit(proc, namespace["M"], traces, hits) == []
    for inputs, in traces:
        native = namespace["M"]().process(**inputs)
        _, result, _, error = interp.run_step(proc, inputs)
        assert error == ""
        assert result.transactions == [native]
        assert all(type(item) is int for item in result.transactions[0].values())
    assert encoder.verify_encoding(proc, proc.state_ranges) == []


@pytest.mark.parametrize("source, code", [
    (_model("uwrap(x, 4)") + "uwrap = 1\n",
     "source-intrinsic-shadowed"),
    (_model("wrap(x, 4)", imports="uwrap as wrap") + "wrap = 1\n",
     "source-intrinsic-shadowed"),
    (_model("uwrap(x, 4)").replace(
        "    def process(self, x, n):\n",
        "    def process(self, x, n):\n        uwrap = x\n"),
     "source-intrinsic-shadowed"),
    (_model("uwrap(x, 4)").replace(
        "    def process(self, x, n):\n",
        "    def process(self, x, n):\n"
        "        def uwrap(value, width):\n"
        "            return value\n"),
     "source-intrinsic-shadowed"),
    (_model("uwrap(x)") , "source-intrinsic-arity"),
    (_model("uwrap(x, n)"), "source-intrinsic-argument-domain"),
    (_model("uwrap(value=x, width=4)"),
     "source-intrinsic-argument-form"),
    (_model("sashr(x, -1, 4)", imports="sashr"),
     "source-intrinsic-argument-domain"),
])
def test_shadowing_and_malformed_intrinsic_calls_are_typed_refusals(
        source, code):
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4, 3], read_initial={})
    assert caught.value.code == code


def test_a_bare_spelling_without_the_import_is_not_an_intrinsic():
    source = ast.unparse(ast.parse(_model("uwrap(x, 4)")).body[1]) + "\n"
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(
            source, input_widths=[4, 3], read_initial={})
    assert caught.value.code == "call-unlowered"
