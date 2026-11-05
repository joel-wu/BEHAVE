"""Unit tests for admission of a behavior model.

The property under test is that nothing is skipped quietly: a construct the
goal enumeration has no rule for must produce a rejection naming it.
"""
import pytest

from behave.sim.behavior.admission import admitted, check
from behave.sim.behavior.lowering import LoweringError, lower_procedure


def test_a_plain_model_is_admitted():
    src = ("class M:\n"
           "    def reset(self):\n"
           "        self.n = 0\n"
           "    def process(self, a):\n"
           "        if a and a > 3:\n"
           "            for i in range(4):\n"
           "                self.n += i\n"
           "        return {'y': self.n}\n")
    assert admitted(src)


@pytest.mark.parametrize("call, accepted", [
    ("values.pop()", True),
    ("values.pop(0)", True),
    ("values.pop(0, 1)", False),
    ("values.pop(index=0)", False),
    ("values.pop(*[0])", False),
    ("pop(values)", False),
])
def test_pop_admission_uses_the_shared_receiver_and_argument_shape(call, accepted):
    source = ("class M:\n    def reset(self):\n        pass\n"
              "    def process(self, a):\n        values = [1, 2]\n"
              f"        return {{'y': {call}}}\n")
    assert admitted(source) is accepted


@pytest.mark.parametrize("call, accepted", [
    ("out.update(y=a)", True),
    ("out.update()", True),
    ("out.update({'y': a})", False),
    ("out.update(**{'y': a})", False),
    ("update(out, y=a)", False),
])
def test_record_update_admission_matches_the_shared_operation_shape(call, accepted):
    source = ("class M:\n    def reset(self): pass\n    def process(self, a):\n"
              f"        out = {{'y': 0}}\n        {call}\n        return out\n")
    assert admitted(source) is accepted


def test_try_is_rejected():
    r = check("try:\n    x = 1\nexcept Exception:\n    x = 2\n")
    assert [x.construct for x in r] == ["Try"]


def test_assert_is_admitted_but_arbitrary_raise_is_not():
    # A subset and not the whole set: `ValueError()` is also a call to a name this
    # model neither defines nor imports, which is its own refusal, and a form may
    # fail the grammar for more than one reason.
    kinds = {x.construct for x in check("assert x\nraise ValueError()\n")}
    assert "Raise" in kinds
    assert "Assert" not in kinds


def test_generator_is_rejected():
    r = check("def g():\n    yield 1\n")
    assert [x.construct for x in r] == ["Yield"]


def test_for_and_while_else_are_admitted():
    assert admitted("for i in range(3):\n    pass\nelse:\n    x = 1\n")
    assert admitted("x = 3\nwhile x:\n    x -= 1\nelse:\n    y = 1\n")


def test_iterating_an_arbitrary_call_is_rejected():
    # Three refusals now, in source order: the pair target, the call to `items`, and
    # the iterable itself. The one this test is about is the last, so it is looked up
    # by name rather than by position.
    r = [x for x in check("for k, v in raw.items():\n    pass\n")
         if x.construct == "iterable-call"]
    assert r and "items" in r[0].reason


def test_iterating_range_and_enumerate_is_admitted():
    assert admitted("for i in range(4):\n    pass\n")
    # Both the iterable and its fixed pair binding have lowering rules.
    assert admitted("for i in enumerate(buf):\n    pass\n")
    assert admitted("for i, x in enumerate(buf):\n    pass\n")


def test_iterating_a_name_is_admitted():
    # A fixed-size value array is the common case and its length is static.
    assert admitted("for x in self.buf:\n    pass\n")


def test_break_and_continue_are_admitted_loop_jumps():
    assert admitted("for i in range(4):\n"
                    "    if i == 3:\n"
                    "        break\n")
    assert admitted("for i in range(4):\n"
                    "    if i == 2:\n"
                    "        continue\n")


def test_chained_comparison_and_scalar_chained_assignment_are_admitted():
    assert admitted("x = y = a\n"
                    "if x < y < 3:\n"
                    "    pass\n")


def test_compound_target_in_chained_assignment_is_still_rejected():
    got = check("x[0] = y = a\n")
    assert [r.construct for r in got] == ["Assign"]


def test_rejections_are_reported_in_source_order():
    r = check("import os\ntry:\n    pass\nexcept Exception:\n    pass\n")
    assert [x.line for x in r] == sorted(x.line for x in r)


def test_the_approved_primitive_library_may_be_imported():
    assert admitted("from fp_lib import fp32_add, fp32_mul\n")
    assert not admitted("from .fp_lib import fp32_add\n")


def test_host_arithmetic_libraries_are_rejected():
    kinds = {(x.construct, x.reason) for x in check("import math\nimport struct\n")}
    assert {k for k, _ in kinds} == {"Import"}
    assert any("math" in reason for _, reason in kinds)


def test_direct_lowering_refuses_an_unapproved_import_before_construction(
        monkeypatch):
    source = ("import math\n"
              "class M:\n"
              "    def reset(self):\n"
              "        pass\n"
              "    def process(self, a):\n"
              "        return {'y': a}\n")

    def construction_must_not_run(*_args, **_kwargs):
        raise AssertionError("an unapproved source reached construction")

    monkeypatch.setattr(
        "behave.sim.behavior.lower_prepare.construction_facts",
        construction_must_not_run)
    with pytest.raises(LoweringError) as caught:
        lower_procedure(source, input_widths=[1], outputs=["y"])
    assert caught.value.code == "source-import-unapproved"


# ── the grammar is a whitelist, and it agrees with the lowering ──

def _model(body):
    indented = "\n".join("        " + line for line in body)
    return ("class M:\n"
            "    def reset(self):\n"
            "        self.z = 0\n"
            "    def process(self, a):\n"
            f"{indented}\n"
            '        return {"y": a}\n')


#: Forms with no lowering rule. Ten of these were admitted while admission was
#: a list of refusals, and every one of them was refused a stage later.
UNADMITTED = {
    "walrus": ["if (n := a) > 0:", "    z = n"],
    "starred": ["t = [1, 2]", "z = max(*t)"],
    "keyword argument": ["z = max(a, key=None)"],
    "augmented subscript": ["t = [1, 2]", "t[0] += 1"],
    "f-string": ["z = len(f'{a}')"],
    "del": ["z = a", "del z"],
    "nested class": ["class Inner:", "    pass"],
    "set": ["z = len({1, 2})"],
    "true division": ["z = a / 2"],
    "power": ["z = a ** 2"],
}

#: The subset. If one of these stops being admitted, the grammar has locked out
#: something the lowering handles, which is the failure mode a whitelist has
#: and a reject-list does not.
ADMITTED_FORMS = {
    "if": ["if a > 1:", "    z = 1", "else:", "    z = 2"],
    "loop": ["z = 0", "for i in range(3):", "    z = z + i"],
    "while": ["n = 0", "while n < 3:", "    n = n + 1"],
    "comprehension": ["t = [i for i in range(3)]", "z = t[0]"],
    "slice": ["t = [1, 2, 3][0:2]", "z = t[0]"],
    "boolean operator": ["z = 1 if (a and a > 1) else 0"],
    "augmented name": ["self.z += a"],
    "continue": ["z = 0", "for i in range(3):", "    if i == a:",
                 "        continue", "    z += i"],
    "chained comparison": ["z = 1 if 0 < a < 3 else 0"],
    "chained assignment": ["x = z = a"],
    "dict comprehension with fixed output schema": [
        'return {f"out_{i}": a + i for i in range(2)}'],
    "bounded generator consumed by sum": [
        "z = sum(t for t in [1, 2, 3])"],
    "finite membership": ["z = 1 if a in [1, 2] else 0"],
    "finite stable sort": [
        "z = sorted([a, 1], key=lambda x: -x)[0]"],
}


def test_a_form_with_no_lowering_rule_is_not_admitted():
    still_admitted = [name for name, body in UNADMITTED.items()
                      if admitted(_model(body))]
    assert not still_admitted


def test_the_subset_stays_admitted():
    refused = {name: check(_model(body))[0]
               for name, body in ADMITTED_FORMS.items()
               if not admitted(_model(body))}
    assert not refused


def test_a_form_nobody_anticipated_is_rejected_by_default():
    """The property a reject-list cannot have. `Match` is not named anywhere in
    the grammar and is rejected for that reason alone, so a construct added to
    the language does not arrive admitted."""
    from behave.sim.behavior import admission

    assert "Match" not in admission.ADMITTED
    got = check("class M:\n"
                "    def process(self, a):\n"
                "        match a:\n"
                "            case 1:\n"
                "                pass\n")
    assert any(r.construct == "Match" for r in got)


def test_the_hook_names_are_reserved():
    """A model that binds the instrumentation's hook decides what its own
    coverage says, so the source is refused rather than the collision being
    resolved: which of the two won would be a property of binding order."""
    got = check("class M:\n"
                "    def process(self, a):\n"
                "        _behave_hit = 1\n"
                "        return {'y': a}\n")
    assert any("_behave_hit" in r.reason for r in got)
