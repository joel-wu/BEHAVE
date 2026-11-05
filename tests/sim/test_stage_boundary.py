"""The first stage does not reach into the second.

`extract` answers what the source says about a model: which attributes are
fixed, what `reset` leaves, how far a sequence grows, which names a call
resolves to. `lowering` answers what the representation is. Both were in one
file, and a lowering that can reach any of twenty-odd module functions at any
point cannot be split further: whatever it reaches for is an edge, and the
edges have to be visible before the pieces can move.

Read off the imports, like the core boundary test, because a boundary that
lives only in where the code happens to sit is one that decays.
"""
import ast
import pathlib

BEHAVIOR = (pathlib.Path(__file__).resolve().parents[2]
            / "src" / "behave" / "sim" / "behavior")


def _imports(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            yield ("." * node.level) + (node.module or "")


def test_extract_does_not_import_the_lowering():
    named = list(_imports(BEHAVIOR / "extract.py"))
    assert not [n for n in named if "lowering" in n], named


def test_extract_builds_no_commands():
    """It reads the source and reports what it says. A function here that
    returned a command would be lowering under another name, and the split
    would buy nothing."""
    text = (BEHAVIOR / "extract.py").read_text()
    tree = ast.parse(text)
    built = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "ir" and node.attr[0].isupper():
            built.add(node.attr)
    # Shapes and lengths are values, not commands. `Lit` is the one shared
    # spelling of a constant, and nothing else from the IR belongs here.
    assert built <= {"Lit"}, sorted(built)


def test_step_alias_authority_does_not_import_lowering_or_ir():
    """The source points-to proof is upstream of representation building."""
    named = list(_imports(BEHAVIOR / "step_alias.py"))
    assert not [n for n in named if n.endswith("lowering")], named
    assert not [n for n in named if n.endswith("ir")], named


def test_lowering_checks_consume_the_source_alias_authority_directly():
    from behave.sim.behavior import lower_checks, lowering, step_alias

    assert lower_checks.step_alias is step_alias
    assert not hasattr(lowering, '_step_alias_hazard')
    assert not hasattr(lowering, '_alias_query_identity')
    assert '.step_alias' not in set(_imports(BEHAVIOR / 'lowering.py'))


def test_return_builders_have_one_owner_independent_of_the_control_lowerers():
    from behave.sim.behavior import lower_graph, lower_returns, lowering

    named = list(_imports(BEHAVIOR / "lower_returns.py"))
    assert ".lowering" not in named and ".lower_graph" not in named
    assert lower_graph._NamedReturn is lowering._NamedReturn is lower_returns._NamedReturn
    for name in ("_ValueReturn", "_BatchBuilder", "_BatchValue", "_OptionBuilder",
                 "_RecordBuilder", "_PositionalBuilder", "_PositionalValue",
                 "_StructuredValue"):
        assert getattr(lowering, name) is getattr(lower_returns, name)
