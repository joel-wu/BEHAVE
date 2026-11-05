"""Unit checks of compiler conversion metadata, not a frontend qualification."""

from types import SimpleNamespace

import pytest

from behave.sim.symbolic.interp import _Interp
from behave.sim.core.refusal import Refused
from behave.sim.rtl.source_declarations import SourceContext, SourceDeclarations
from behave.sim.rtl.source_ir import SourceCompiler, _Lowerer


def _conversion(bits, source_signed, target_signed, target_width, kind):
    context = SourceContext((), ("M",), 0)
    declarations = SourceDeclarations((), (), (), (), contexts=(context,))
    plan = SimpleNamespace(loops=(), loop_proofs=None, declarations=declarations)
    compiler = SourceCompiler("a", {}, plan, filename="design.sv")
    lowerer = _Lowerer(compiler, context, namespace="", storage_name=None)
    span = dict(source_file_start="design.sv", source_file_end="design.sv",
                source_line_start=1, source_line_end=1,
                source_column_start=1, source_column_end=2)
    source_type = dict(kind="PredefinedIntegerType", bitWidth=4, isSigned=source_signed)
    target_type = dict(kind="PredefinedIntegerType", bitWidth=target_width, isSigned=target_signed)
    node = dict(kind="Conversion", type=target_type, **span,
                operand=dict(kind="IntegerLiteral", type=source_type, value=str(bits), **span))
    if kind is not None:
        node["conversionKind"] = kind
    commands, expression = lowerer.expression(node)
    assert not commands
    return _Interp({}).expr(expression)


@pytest.mark.parametrize("kind", ("Propagated", "Explicit", "Implicit"))
@pytest.mark.parametrize("source_signed", (False, True))
@pytest.mark.parametrize("target_signed", (False, True))
@pytest.mark.parametrize("target_width", (2, 4, 8))
def test_contextual_conversion_sets_sign_before_resizing(
        kind, source_signed, target_signed, target_width):
    for bits in range(16):
        extend_signed = target_signed if kind == "Propagated" else source_signed
        value = bits - 16 if extend_signed and bits >= 8 else bits
        expected = value % (1 << target_width)
        if target_signed and expected >= 1 << (target_width - 1):
            expected -= 1 << target_width
        assert _conversion(bits, source_signed, target_signed, target_width, kind) == expected


@pytest.mark.parametrize("source_signed", (False, True))
@pytest.mark.parametrize("target_signed", (False, True))
@pytest.mark.parametrize("target_width", (2, 4, 8))
def test_missing_kind_refuses_only_when_extension_semantics_are_ambiguous(
        source_signed, target_signed, target_width):
    if source_signed != target_signed and target_width > 4:
        with pytest.raises(Refused, match="scalar conversion needs its compiler conversion kind"):
            _conversion(15, source_signed, target_signed, target_width, None)
        return
    for bits in range(16):
        expected = _conversion(bits, source_signed, target_signed, target_width, "Implicit")
        assert _conversion(bits, source_signed, target_signed, target_width, None) == expected


def test_unknown_conversion_semantics_are_not_an_implicit_cast():
    with pytest.raises(Refused, match="scalar conversion needs its compiler conversion kind"):
        _conversion(15, True, True, 4, "Bitstream")
