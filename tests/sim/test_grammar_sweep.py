"""Every form the grammar admits, put through the frontend that has to handle it.

Admission is a whitelist of AST node types, and a node type is not a form: `Call` covers
a call to a helper and a call to `sorted`, `Assign` covers one target and three, `For`
covers a name and a pair. So the gap between what is admitted and what has a rule cannot
be found by comparing two lists, and hand-listing forms found six of them and stopped.
Walking the grammar instead -- one minimal model per admitted node type, in a position
the grammar allows, through the production lowering -- found nine.

Two properties, and the second is the one with teeth:

* every entry of `ADMITTED` has a case here or is named as having no template, so a node
  type added to the grammar without a case is visible rather than absent;

* an admitted form never makes the frontend fall over. It compiles, or it is refused by
  admission, or the lowering refuses it naming the construct. What it may not do is raise
  an `AttributeError` from somewhere inside, which is what "admitted but no rule" looked
  like every one of the nine times.

The second is deliberately weaker than "admitted implies encodable", which is false and
should be: a loop whose bound has no proved range is admitted, has a rule, and is refused
later for a reason that is about this model rather than about the subset.
"""
import ast

import pytest

from behave.sim.behavior.admission import ADMITTED, check
from behave.sim.behavior.lowering import LoweringError, lower_with_state_ranges

#: One minimal model body per admitted node type, in a position the grammar allows.
#: Several types are covered by one body and are listed together in the key.
BODIES = {
    "Assign": "z = a",
    "AnnAssign": "z: int = a",
    "AugAssign": "self.n += a",
    "If": "if a > 1:\n    z = 1\nelse:\n    z = 2",
    "For": "z = 0\nfor i in range(3):\n    z = z + i",
    "While": "n = 0\nwhile n < 3:\n    n = n + 1\nz = n",
    "Break": "z = 0\nfor i in range(3):\n    if i == 1:\n        break\n    z = i",
    "Continue": "z = 0\nfor i in range(3):\n    if i == a:\n        continue\n    z += i",
    "Return": "return {'y': a}",
    "Pass": "pass\nz = a",
    "Expr": "self.helper(a)\nz = a",
    "Assert": "assert a != 3\nz = a",
    "Name": "z = a",
    "Constant": "z = 7",
    "Attribute": "z = self.n",
    "BinOp": "z = a + 1",
    "UnaryOp": "z = -a",
    "BoolOp": "z = 1 if (a and a > 1) else 0",
    "Compare": "z = 1 if a > 1 else 0",
    "Subscript": "t = [1, 2, 3]\nz = t[1]",
    "IfExp": "z = 1 if a else 2",
    "Call": "z = self.helper(a)",
    # Admitted only as a fixed helper argument or an assignment target.
    "Starred": "z = self.helper(*(a,))",
    "List": "t = [1, 2]\nz = t[0]",
    "Tuple": "p, q = 1, 2\nz = p + q",
    "Dict": "d = {1: 2}\nz = 0",
    "ListComp": "t = [i for i in range(3)]\nz = t[0]",
    "DictComp": 'return {f"out_{i}": a + i for i in range(2)}',
    "FormattedValue": 'return {f"out_{i}": a + i for i in range(2)}',
    "GeneratorExp": "z = sum(i for i in range(3))",
    "JoinedStr": 'return {f"out_{i}": a + i for i in range(2)}',
    "Lambda": "z = sorted([a, 1], key=lambda x: -x)[0]",
    "Slice": "t = [1, 2, 3][0:2]\nz = t[0]",
    "comprehension": "t = [i for i in range(3)]\nz = t[0]",
    "keyword": "z = sorted([a, 1], reverse=True)[0]",
    # Operators, each in the position it appears.
    "Add": "z = a + 1", "Sub": "z = a - 1", "Mult": "z = a * 2",
    "FloorDiv": "z = a // 2", "Mod": "z = a % 2",
    "LShift": "z = a << 1", "RShift": "z = a >> 1",
    "BitAnd": "z = a & 1", "BitOr": "z = a | 1", "BitXor": "z = a ^ 1",
    "USub": "z = -a", "Invert": "z = ~a", "Not": "z = 1 if not a else 0",
    "And": "z = 1 if (a and a > 1) else 0", "Or": "z = 1 if (a or a > 1) else 0",
    "Eq": "z = 1 if a == 1 else 0", "NotEq": "z = 1 if a != 1 else 0",
    "Lt": "z = 1 if a < 1 else 0", "LtE": "z = 1 if a <= 1 else 0",
    "Gt": "z = 1 if a > 1 else 0", "GtE": "z = 1 if a >= 1 else 0",
    "In": "z = 1 if a in [1, 2] else 0",
    "NotIn": "z = 1 if a not in [1, 2] else 0",
    "Load": "z = a", "Store": "z = a",
    # Shapes a node type does not pin down, which is where the gap lived. Each of these
    # was admitted with no rule when this was written.
    "Assign, several targets": "x = y = a\nz = x",
    "Assign, tuple target": "p, q = a, 1\nz = p",
    "For over enumerate": "z = 0\nfor i, x in enumerate([1, 2, 3]):\n    z = z + x",
    "For over zip": "z = 0\nfor p, q in zip([1, 2], [3, 4]):\n    z = z + p",
    "For over reversed": "z = 0\nfor x in reversed([1, 2, 3]):\n    z = z + x",
    "For over a name": "z = 0\nfor x in self.buf:\n    z = z + x",
    "Call to a builtin": "z = len([1, 2])",
    "Call to an unknown name": "z = sorted([2, 1])[0]",
    "Subscript of a dict": "d = {1: 2}\nz = d[1]",
    "Dict get": "d = {1: 2}\nz = d.get(1, 0)",
    "Call to append": "t = []\nt.append(a)\nz = t[0]",
}

#: Node types with no body here. Listed rather than skipped, so a type in the grammar
#: with no case is visible instead of absent.
NO_TEMPLATE = {"Module", "ClassDef", "FunctionDef", "ImportFrom", "Import",
               "arguments", "arg", "alias", "UAdd"}

MODEL = """class M:
    def reset(self):
        self.n = 0
        self.buf = [1, 2, 3]

    def helper(self, v):
        return v + 1

    def process(self, a):
        z = 0
{body}
        return {{"y": z}}
"""


def source_for(body):
    return MODEL.format(
        body="\n".join(f"        {line}" for line in body.splitlines()))


def test_every_admitted_node_type_has_a_case():
    covered = {name.split(",")[0].split(" ")[0] for name in BODIES}
    missing = sorted(t for t in ADMITTED
                     if t not in covered and t not in NO_TEMPLATE)
    assert not missing, (f"admitted with no case in this sweep: {missing}. Add a "
                         f"minimal body, or name it in NO_TEMPLATE and say why.")


def test_a_case_here_is_for_a_type_the_grammar_admits():
    """The other direction: a body for a type the grammar refuses outright would go on
    passing forever by being refused, and says nothing about any rule."""
    named = {name.split(",")[0].split(" ")[0] for name in BODIES}
    stale = sorted(t for t in named if t not in ADMITTED)
    assert not stale, (f"a case here for a type the grammar does not admit: {stale}. "
                       f"Drop the body, or move the type back into ADMITTED.")


@pytest.mark.parametrize("name", sorted(BODIES))
def test_an_admitted_form_never_falls_over(name):
    """Compiled, or refused by a stage that names the construct. Not an AttributeError."""
    source = source_for(BODIES[name])
    if check(source, file="behavior.py"):
        return                        # refused by admission, which is an answer
    try:
        lower_with_state_ranges(source, file="behavior.py", input_widths=[8])
    except LoweringError as e:
        # A refusal that names something. `expression Dict` and `loop whose bound has
        # no known range` both qualify; the point is that it is a sentence and not a
        # traceback from inside a visitor.
        assert str(e.what).strip(), f"{name}: an empty refusal is not an answer"
    except Exception as e:
        raise AssertionError(
            f"{name}: admitted, and the frontend raised "
            f"{type(e).__name__}: {e}") from e


def test_the_sweep_bodies_are_all_syntactically_what_they_claim():
    """A body with a typo would pass the sweep by being refused for the typo."""
    for body in BODIES.values():
        ast.parse(source_for(body))
