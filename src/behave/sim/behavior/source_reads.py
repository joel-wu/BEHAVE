"""Scalar source-read samples, at the original evaluation point.

An identity hook receives the value already read by Python. It does not fetch an
attribute again, move the read to statement entry, or replace a source value.
Selectors are explicit source spellings, not inferred signal-name conventions.
"""

import ast
from dataclasses import dataclass

from ..core.catalogue import COVERAGE, Goal, OccurrenceKey
from ..core.source_rules import SOURCE_READ_POLICY, BoundReadPredicate


@dataclass(frozen=True, order=True, slots=True)
class ReadSite:
    file: str
    line: int
    col: int
    end_line: int
    end_col: int
    symbol: str

    @classmethod
    def of(cls, node, file):
        return cls(file, node.lineno, node.col_offset, node.end_lineno,
                   node.end_col_offset, ast.unparse(node))


@dataclass(frozen=True, slots=True)
class SourceReadGoal:
    site: ReadSite
    predicate: BoundReadPredicate

    @property
    def id(self):
        site = self.site
        return (f"read:{site.file}:{site.line}:{site.col}:{site.end_line}:"
                f"{site.end_col}:{site.symbol}:{self.predicate.rule.name}")

    def declaration(self):
        site = self.site
        return Goal(self.id, COVERAGE, provider="python-structural",
                    family="value-observation", event=self.predicate.rule.name,
                    policy=SOURCE_READ_POLICY, provenance={
                        "file": site.file, "line": site.line, "col": site.col,
                        "end_line": site.end_line, "end_col": site.end_col,
                        "read": self.predicate.rule.as_dict()})

    def occurrence(self):
        site = self.site
        return OccurrenceKey("python-structural",
                             (site.file, site.line, site.col, site.end_line,
                              site.end_col, site.symbol, "source"),
                             f"read:{self.predicate.rule.name}")

    def accepts(self, value):
        return type(value) is (bool if self.predicate.value.kind == "boolean" else int)


def declare_reads(functions, nodes_of, file, rules):
    by_symbol = {}
    for rule in rules:
        by_symbol.setdefault(rule.symbol, []).append(rule.bind())
    sites = set()
    for function in functions:
        nodes = tuple(nodes_of(function))
        callees = {id(node.func) for node in nodes if isinstance(node, ast.Call)}
        sites.update(ReadSite.of(node, file) for node in nodes
                     if isinstance(node, (ast.Name, ast.Attribute))
                     and isinstance(node.ctx, ast.Load) and id(node) not in callees)
    return tuple(SourceReadGoal(site, predicate) for site in sorted(sites)
                 for predicate in by_symbol.get(site.symbol, ()))


def instrument_reads(tree, sites):
    names = {item for node in ast.walk(tree) for _field, value in ast.iter_fields(node)
             for item in (value if isinstance(value, list) else (value,)) if isinstance(item, str)}
    hook = "_behave_read"
    while hook in names:
        hook += "_"
    by_site = {site: index for index, site in enumerate(sites)}
    file = sites[0].file
    emitted = set()

    class Capture(ast.NodeTransformer):
        def read(self, node):
            index = by_site.get(ReadSite.of(node, file)) if isinstance(node.ctx, ast.Load) else None
            node = self.generic_visit(node)
            if index is None:
                return node
            emitted.add(index)
            return ast.copy_location(ast.Call(func=ast.Name(id=hook, ctx=ast.Load()),
                                             args=[ast.Constant(index), node], keywords=[]), node)

        visit_Name = read
        visit_Attribute = read

    tree = Capture().visit(tree)
    if emitted != set(range(len(sites))):
        raise ValueError("read instrumentation omitted a declared source occurrence")
    ast.fix_missing_locations(tree)
    return tree, hook
