"""Keep generated observer references in the original compiler's scopes.

Anonymous generate names are assigned by the HDL compiler. Adding declarations
can change another compiler's automatic numbering, so the native copy makes the
selected names explicit. This edits only labels, not the generate conditions,
instance selection, original source, or goal occurrence keys.
"""

from dataclasses import dataclass

from .source_declarations import _children
from .source_edits import Insertion, SourceEdits
from .source_syntax import walk


@dataclass(frozen=True)
class GeneratedScopes:
    implicit: frozenset[int] = frozenset()
    opening: tuple[Insertion, ...] = ()
    closing: tuple[Insertion, ...] = ()


def generated_scopes(source, root, spans, document, *, filename):
    """Retain compiler-owned blocks, including single-member generate bodies.

    A generate body has a scope even without written begin/end. Wrapping that
    same body makes room for observer declarations without moving them outside
    its iteration or branch. Source coordinates still refer to the input text.
    """
    names = {}

    def collect(node):
        if isinstance(node, dict):
            if node.get("isUninstantiated"):
                return
            if (node.get("kind") in {"GenerateBlock", "GenerateBlockArray"}
                    and node.get("name") and node.get("source_file") == filename):
                position = node["source_line"], node["source_column"]
                name = node["name"]
                if position in names and names[position] != name:
                    raise ValueError("one generate declaration has different bound scope names")
                names[position] = name
        for _, child in _children(node):
            collect(child)

    collect(document["design"])
    positions = SourceEdits(source, ())
    labels, closing, implicit = [], [], set()
    bodies = {}
    for node in walk(root):
        if not isinstance(node, dict) or "text" in node or "kind" not in node:
            continue
        start, end = spans[id(node)]
        position = positions.original_position(start)
        if position in names:
            previous = bodies.get(position)
            if previous is None or end > spans[id(previous)][1]:
                bodies[position] = node
    for position, node in bodies.items():
        if node.get("kind") == "GenerateBlock":
            continue
        start, end = spans[id(node)]
        implicit.add(id(node))
        labels.append(Insertion(start, f"begin: \\{names[position]} "))
        closing.append(Insertion(end, " end "))
    # The compiler omits alternatives that have no elaborated instances. They
    # still participate in a backend's pre-elaboration scope lookup. Give each
    # single-member alternative the same private declaration space as a written
    # begin/end body; otherwise a sibling's implicit name can resolve to a body
    # with no observer state. Do not invent names or wrap directly nested
    # generate conditionals: those have special no-extra-scope semantics.
    for node in walk(root):
        if not isinstance(node, dict) or node.get("kind") != "IfGenerate":
            continue
        alternatives = (node["block"], node.get("elseClause", {}).get("clause"))
        for body in alternatives:
            if (body is None or id(body) in implicit
                    or body.get("kind") in {"GenerateBlock", "IfGenerate", "CaseGenerate"}):
                continue
            start, end = spans[id(body)]
            implicit.add(id(body))
            labels.append(Insertion(start, "begin "))
            closing.append(Insertion(end, " end "))
    for node in walk(root):
        if (not isinstance(node, dict) or node.get("kind") != "GenerateBlock"
                or "beginName" in node):
            continue
        start, end = spans[id(node["begin"])]
        name = names.get(positions.original_position(start))
        if name is not None:
            labels.append(Insertion(end, f": \\{name} "))
    return GeneratedScopes(frozenset(implicit), tuple(labels), tuple(closing))
