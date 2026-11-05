"""Compound XML values must not become double-encoded tool arguments."""

import pytest

from behave.agent.providers.sglang_tools import compound_value, register_qwen_tools


@pytest.mark.parametrize("kind,raw,expected", [
    ("object", '{"files":["behavior.py"],"flag":true,"optional":null}',
     {"files": ["behavior.py"], "flag": True, "optional": None}),
    ("array", '[false,{"x":2}]', [False, {"x": 2}]),
    ("boolean", "false", False),
    ("integer", "42", 42),
    ("number", "4.2", 4.2),
    ("number", "42", 42),
    ("string", '{"still":"text"}', '{"still":"text"}'),
])
@pytest.mark.parametrize("form", ("anyOf", "oneOf", "type"))
def test_nullable_types_keep_values_and_null(kind, raw, expected, form):
    schema = ({"type": [kind, "null"]} if form == "type" else
              {form: [{"type": kind}, {"type": "null"}]})
    handled, actual = compound_value(raw, schema)
    assert handled and type(actual) is type(expected) and actual == expected
    assert compound_value("null", schema) == (True, None)


@pytest.mark.parametrize("raw", [
    "{'x': 1}", '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}',
    'null', 'true', '[]', '"{\\"x\\":1}"', '{} , "extra":1',
])
def test_invalid_or_wrong_type_json_is_not_repaired(raw):
    assert compound_value(raw, {"anyOf": [{"type": "object"}]}) == (True, raw)


def test_unknown_and_ordinary_schemas_stay_with_native_parser():
    for schema in (None, {}, {"type": "object"}, {"anyOf": [{"$ref": "#/definitions/X"}]},
                   {"type": ["object", {}]}, {"anyOf": []}):
        assert compound_value("{}", schema) == (False, "{}")
    assert compound_value("42", {"anyOf": [{"type": "string"}, {"type": "integer"}]}) == (True, "42")


def test_plugin_is_inert_without_explicit_native_opt_in(monkeypatch):
    monkeypatch.delenv("SGLANG_PLUGINS", raising=False)
    register_qwen_tools()  # The ordinary control ABI does not install SGLang.
