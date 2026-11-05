"""Selected source mutations and compiler-resolved storage coordinates."""

from bisect import bisect_right

from .source_call_effects import FrameLocalCalls
from .source_declarations import _nodes
from .source_edits import SourceEdits
from .source_read_events import type_witness


class SourceWriteIndex:
    def __init__(self, source, document, declarations, sensitivity, *, filename):
        self.source = source.encode("utf-8")
        self.filename = filename
        self.frame_local_calls = frozenset(declarations.frame_local_calls)
        self.closed_value_calls = frozenset(declarations.closed_value_calls)
        self.positions = SourceEdits(source, ())
        self.nodes = tuple(_nodes(document))
        symbols = {str(node["addr"]): node for node in self.nodes if "addr" in node}
        self.call_effects = FrameLocalCalls(
            {identity: node for identity, node in symbols.items() if node.get("kind") == "Subroutine"},
            _nodes)
        self.types = {identity: node["target"] if node.get("kind") == "TypeAlias" else node
                      for identity, node in symbols.items()}
        runtime = {(row["start"], row["end"]) for row in sensitivity
                   if row["start"] in declarations.process_starts}
        runtime.update((site.start, site.end) for site in declarations.sites)
        calls = set(declarations.call_spans)
        for node in self.nodes:
            if node.get("kind") == "Call" and self.span(node) in calls:
                callee = symbols.get(node.get("subroutine", "").split(" ", 1)[0])
                if callee is not None:
                    body = [self.span(part) for part in _nodes(callee["body"])
                            if part.get("source_file_start") == filename]
                    if body:
                        runtime.add((min(first for first, _ in body), max(last for _, last in body)))
        self.starts, self.ends = [], []
        for first, last in sorted(runtime):
            self.starts.append(first)
            self.ends.append(max(last, self.ends[-1] if self.ends else 0))

    def span(self, node):
        if node.get("source_file_start") != self.filename or node.get("source_file_end") != self.filename:
            raise ValueError("source write notification has no selected source coordinate")
        return tuple(self.positions._source_lines[node[f"source_line_{side}"]-1]
                     + node[f"source_column_{side}"]-1 for side in ("start", "end"))

    def text(self, node):
        first, last = self.span(node)
        return self.source[first:last].decode("utf-8")

    def resolved_type(self, type_):
        while isinstance(type_, str):
            type_ = self.types[type_.split(" ", 1)[0]]
        return type_

    def type_expression(self, node):
        return type_witness(node, self.source, self.span, self.resolved_type)

    def fill(self, node, bit):
        """A typed all-zero/all-one value without flattening unpacked shape."""
        def value(type_, witness):
            type_ = self.resolved_type(type_)
            if type_.get("kind") == "FixedSizeUnpackedArrayType":
                element = f"{witness}[$left({witness})]"
                return "'{default:" + value(type_["elementType"], element) + "}"
            # Enum and packed aggregate masks are observer bit patterns, not
            # implicit source assignments to an enumerator. Preserve the exact
            # carrier with an explicit cast; the DUT lvalue remains unchanged.
            return f"type({witness} )'('{bit})"
        return value(node["type"], self.type_expression(node))

    def selected(self, node):
        first, last = self.span(node)
        index = bisect_right(self.starts, first)-1
        return index >= 0 and last <= self.ends[index]
