"""Resolve function-only package imports for the shared Yosys input.

Slang, not spelling or nearest scope, identifies each called subroutine.
Native simulation still consumes the original source. Unsupported package
members keep their imports and are left to the compiler, never guessed.
"""

from .constant_arrays import concrete_spans
from .source_edits import SourceEdits
from .source_replacements import Replacement, SourceReplacements
from .source_syntax import walk

VERSION = "rtl-package-imports/1"


def imports_in(cst):
    return tuple(node for tree in cst["syntaxTrees"] for node in walk(tree["root"])
                 if isinstance(node, dict) and node.get("kind") == "PackageImportDeclaration")


def _nodes(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from _nodes(value)


def normalize_package_imports(source, cst, typed):
    imports = imports_in(cst)
    if not imports:
        return SourceReplacements(source, ())
    root, spans = concrete_spans(source, cst)
    packages, callees = {}, {}
    for node in _nodes(typed):
        if node.get("kind") != "Package":
            continue
        members = node.get("members", ())
        if not all(member.get("kind") == "Subroutine"
                   and member.get("subroutineKind") == "Function" for member in members):
            continue
        packages[node["name"]] = {member["name"] for member in members}
        for member in members:
            callees[str(member["addr"])] = node["name"], member["name"]
    accepted = []
    for declaration in imports:
        items = [item for item in declaration["items"]
                 if item.get("kind") == "PackageImportItem"]
        if items and all(item["package"]["text"] in packages
                         and (item["item"]["kind"] == "Star"
                              or item["item"]["text"] in packages[item["package"]["text"]])
                         for item in items):
            accepted.append(declaration)
    if not accepted:
        return SourceReplacements(source, ())
    positions = SourceEdits(source, ())
    tokens = {spans[id(node)][0]: node for node in walk(root)
              if isinstance(node, dict) and node.get("kind") == "Identifier"}
    edits, resolved_calls = {}, set()
    for node in _nodes(typed):
        if node.get("kind") != "Call" or node.get("source_file_start") != "design.sv":
            continue
        line, column = node["source_line_start"], node["source_column_start"]
        offset = positions._source_lines[line - 1] + column - 1
        resolved_calls.add(offset)
        target = callees.get(node.get("subroutine", "").split(" ", 1)[0])
        if target is None:
            continue
        package, name = target
        token = tokens.get(offset)
        if token is None or token["text"] != name:
            continue  # Already qualified, or outside concrete supported syntax.
        start, end = spans[id(token)]
        # Yosys stores package functions under this qualified identifier.
        # Its scope-token re-lexing double-counts columns for P::f inside
        # procedural calls. The equivalent escaped Yosys identifier avoids
        # that re-lexing; its mandatory terminating space is source-mapped.
        replacement = Replacement(start, end, f"\\{package}::{name} ")
        if start in edits and edits[start] != replacement:
            raise ValueError("package call has inconsistent compiler bindings across instances")
        edits[start] = replacement
    imported_names = set().union(*(packages[item["package"]["text"]]
        for declaration in accepted for item in declaration["items"]
        if item.get("kind") == "PackageImportItem"))
    for node in walk(root):
        if not isinstance(node, dict) or node.get("kind") != "InvocationExpression":
            continue
        left = node["left"]
        if left.get("kind") == "IdentifierName":
            token = left["identifier"]
            if token["text"] in imported_names and spans[id(token)][0] not in resolved_calls:
                # For example, an uninstantiated module or an erased constant
                # call. Removing its import could rebind a global name. Leave
                # the compiler to refuse it; never infer the missing binding.
                return SourceReplacements(source, ())
    for declaration in accepted:
        for token in walk(declaration):
            if isinstance(token, dict) and "text" in token:
                start, end = spans[id(token)]
                edits[start] = Replacement(start, end, " " * (end - start))
    return SourceReplacements(source, tuple(sorted(edits.values(), key=lambda edit: edit.start)))
