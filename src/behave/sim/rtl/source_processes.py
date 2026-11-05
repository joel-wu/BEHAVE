"""Retain source initialization ownership in the common compiler output.

Yosys can lower an initial block into both ``sync init`` and ``sync always``
work, or only the latter for storage with no runtime writer. The sync actions
alone therefore do not identify a source activation. Mark initial processes
from exact CST anchors after the normal compiler source-map projection.
"""

from ._rtlil_sites import SOURCE_INITIAL_ATTRIBUTE
from .constant_arrays import concrete_spans
from .source_edits import SourceEdits
from .source_locations import SOURCE_ATTRIBUTE
from .source_syntax import walk

VERSION = "rtl-source-processes/1"


def mark_initial_processes(source, cst, rtlil, *, filename="design.sv"):
    nodes = [node for tree in cst["syntaxTrees"] for node in walk(tree["root"])
             if type(node) is dict and node.get("kind") == "InitialBlock"]
    anchors = set()
    if nodes:
        _, spans = concrete_spans(source, cst)
        positions = SourceEdits(source, ())
        for node in nodes:
            # The locked Yosys grammar anchors initial at the empty production
            # immediately following its keyword, not at the whole body range.
            line, column = positions.original_position(spans[id(node["keyword"])][1])
            anchors.add(f"{filename}:{line}.{column}-{line}.{column}")

    lines, pending_source = [], ""
    marker = "attribute \\" + SOURCE_INITIAL_ATTRIBUTE + " "
    for line in rtlil.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(marker):
            # Only the frontend supplies this provenance; a submitted HDL
            # attribute with the same spelling must not hide runtime actions.
            continue
        if match := SOURCE_ATTRIBUTE.fullmatch(line):
            if match["name"] == "src":
                pending_source = match["locations"]
        if stripped.startswith("process ") and anchors.intersection(pending_source.split("|")):
            indent = line[:len(line) - len(line.lstrip())]
            lines.append(f"{indent}{marker}1\n")
        lines.append(line)
        if not stripped.startswith("attribute "):
            pending_source = ""
    return "".join(lines)
