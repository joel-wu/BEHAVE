"""Source parsing and syntax-level execution rules shared by frontend consumers."""

import ast
import keyword

from ..core.failure import CandidateFault
from ..core.refusal import Refusal, Refused
from . import budgets

FUTURE_FEATURES = frozenset({"annotations"})


def future_annotations_import(node):
    """The supported, module-local Python annotation compiler directive."""
    return (isinstance(node, ast.ImportFrom) and node.level == 0
            and node.module == "__future__"
            and all(alias.name in FUTURE_FEATURES for alias in node.names))


def require_model_identifier(model, file):
    """Validate an explicit source entry without choosing a different class."""
    if type(model) is not str:
        raise TypeError("a Behavior model selector must be an exact string")
    if not model.isidentifier() or keyword.iskeyword(model):
        raise CandidateFault(Refusal(
            "behavior-entry-invalid", "the selected Behavior entry is not a Python class identifier",
            file=file, model=model))


def annotation_target_expressions(target):
    """Evaluations of ``target: T`` without its final attribute/item operation.

    A bare name only declares a lexical local. A compound target evaluates its
    receiver and indices, but neither loads nor stores the final attribute/item.
    A slice evaluates its bounds without constructing or applying the slice.
    """
    if isinstance(target, ast.Attribute):
        return (target.value,)
    if isinstance(target, ast.Subscript):
        index = target.slice
        indices = (tuple(part for part in (index.lower, index.upper, index.step)
                         if part is not None)
                   if isinstance(index, ast.Slice) else (index,))
        return (target.value, *indices)
    return ()


def execution_children(node):
    """Runtime children of a function-body node; local annotations never run.

    This is not a lexical-binding walk: even an unevaluated annotation can
    declare a local. Scope resolution retains that separate responsibility.
    """
    if isinstance(node, ast.AnnAssign):
        return ((node.value, node.target) if node.value is not None
                else annotation_target_expressions(node.target))
    return tuple(ast.iter_child_nodes(node))


def comprehension_tail(node, generator_index, filter_index=0):
    """Expressions skipped when this generator/filter has no continuation.

    The current iterable was already evaluated. Its remaining filters, later
    generators and final element retain their goals even if none can execute.
    """
    roots = list(node.generators[generator_index].ifs[filter_index:])
    for generator in node.generators[generator_index + 1:]:
        roots.extend((generator.iter, *generator.ifs))
    roots.extend((node.key, node.value) if isinstance(node, ast.DictComp)
                 else (node.elt,))
    return tuple(roots)


def local_annotations(tree):
    """Unevaluated annotation roots, excluding module/class annotations."""
    pending = [(tree, False)]
    while pending:
        node, function_scope = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            pending.extend((statement, True) for statement in node.body)
        elif isinstance(node, ast.ClassDef):
            pending.extend((statement, False) for statement in node.body)
        elif isinstance(node, ast.AnnAssign) and function_scope:
            yield node.annotation
            pending.extend((child, True) for child in execution_children(node))
        else:
            pending.extend((child, function_scope) for child in ast.iter_child_nodes(node))


def unevaluated_annotations(tree):
    """Annotation expressions that Python does not execute in this module."""
    if not isinstance(tree, ast.Module) or not any(
            future_annotations_import(node) for node in tree.body):
        yield from local_annotations(tree)
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.AnnAssign, ast.arg)) and node.annotation is not None:
            yield node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            yield node.returns


def parsed_source(source, file):
    if len(source) > budgets.SOURCE_TEXT_CHARS:
        raise Refused(Refusal("source-text-too-large",
            "the source exceeds the frontend text budget",
            limit=budgets.SOURCE_TEXT_CHARS))
    try:
        tree = ast.parse(source, filename=file)
        # Parsing alone accepts context errors such as a class-body return.
        # Validate the original module before linking/instrumentation, without
        # executing it or inheriting this worker's compiler settings.
        compile(tree, file, "exec", dont_inherit=True, optimize=0)
        return tree
    except SyntaxError as exc:
        raise CandidateFault(Refusal(
            "behavior-source-syntax-error", "Python rejected the submitted source",
            file=file, line=exc.lineno, offset=exc.offset, detail=exc.msg)) from None
