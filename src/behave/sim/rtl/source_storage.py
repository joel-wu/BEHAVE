"""One physical source-storage index, separate from source goal occurrences.

The observation compiler elaborates every actual instance. Its symbol identities
join reads and writes within that compilation; stable storage keys instead use
the declaration's path through the design. Processes do not allocate their own
copy of a referenced child, sibling, parent or package variable.
"""

from dataclasses import dataclass

from ..core.refusal import Refusal, Refused
from .source_declarations import _children
from .source_elaboration import generate_iterations
from .source_integral import typed_symbols


@dataclass(frozen=True, slots=True)
class SourceStorage:
    key: tuple[str | int, ...]
    name: str
    input: bool
    port: str | None = None
    observation: str | tuple[str | int, ...] | None = None


def anonymous_scope_name(node):
    """A logical source key, never a guessed simulator-generated block name."""
    line, column = node.get("source_line"), node.get("source_column")
    if type(line) is int and type(column) is int and line > 0 and column > 0:
        return f"__behave_native_event_block_{line}_{column}"
    return None


def storage_bindings(document, *, symbols=None):
    """Index actual storage once, including instances with no selected process.

    Ports remain distinct storage at this layer: connecting an output variable
    to its parent is a scheduled propagation, not permission to alias two source
    variables. Port propagation belongs to the event scheduler.
    """
    if symbols is None:
        symbols = typed_symbols(document)
    cells = _scope_storage(document["design"], (), top=False)

    def visit(node, path, depth, scope):
        if isinstance(node, dict):
            if node.get("isUninstantiated"):
                return
            kind = node.get("kind")
            if kind == "Instance":
                body = node["body"]
                if isinstance(body, str):
                    body = symbols[body.split(" ", 1)[0]]
                # The simulator handle is already rooted at the top instance.
                # Child identifiers remain individual path segments, including
                # escaped names containing dots or brackets.
                name = node.get("name")
                nested = (() if depth == 0 else
                          (*scope, name) if scope is not None and name else None)
                visit(body, (*path, "body"), depth, nested)
                return
            if kind == "GenerateBlockArray":
                name = node.get("name")
                for index, member, value in generate_iterations(node):
                    nested = ((*scope, name, value)
                              if scope is not None and name and value is not None else None)
                    for key, child in _children(member):
                        visit(child, (*path, "members", index, key), depth, nested)
                return
            if kind == "GenerateBlock":
                name = node.get("name")
                scope = (*scope, name) if scope is not None and name else None
            elif kind == "InstanceArray":
                scope = None  # Requires its own compiler-owned index mapping.
            if kind == "InstanceBody":
                depth += 1
                for identity, cell in _scope_storage(node, path, top=depth == 1, scope=scope).items():
                    previous = cells.get(identity)
                    if previous is not None and previous.key != cell.key:
                        raise Refused(Refusal("rtl-source-storage-instance-unresolved",
                            "source storage requires individually elaborated physical instances",
                            symbol=cell.name))
                    cells[identity] = cell
        for key, child in _children(node):
            visit(child, (*path, key), depth, scope)

    visit(document["design"], (), 0, None)
    return cells


def _scope_storage(body, owner, *, top, scope=None):
    ports = {port["internalSymbol"].split(" ", 1)[0]: port["name"]
             for port in body.get("members", ()) if top and port.get("kind") == "Port"}
    inputs = {port["internalSymbol"].split(" ", 1)[0]
              for port in body.get("members", ())
              if top and port.get("kind") == "Port" and port.get("direction") == "In"}
    cells = {}

    def visit(node, path, scope):
        if isinstance(node, dict):
            kind = node.get("kind")
            if node.get("isUninstantiated") or kind in {"Instance", "InstanceArray", "InstanceBody"}:
                return
            if kind == "GenerateBlockArray":
                name = node.get("name")
                for index, member, value in generate_iterations(node):
                    nested = ((*scope, name, value)
                              if scope is not None and name and value is not None else None)
                    for key, child in _children(member):
                        visit(child, (*path, "members", index, key), nested)
                return
            if kind == "GenerateBlock":
                name = node.get("name")
                scope = (*scope, name) if scope is not None and name else None
            elif kind == "StatementBlock":
                # Anonymous scopes need a separate native-compiler witness.
                # Keep their source identity here; do not rename the HDL block
                # (which would change %m) or invent Verilator's numbering.
                name = node.get("name") or anonymous_scope_name(node)
                scope = (*scope, name) if scope is not None and name else None
            elif kind in {"ProceduralBlock", "Subroutine", "Package", "ClassType"}:
                # These owners need a separate physical scope binding;
                # never borrow an identically named module net.
                scope = None
            if kind in {"Net", "Variable", "FormalArgument"}:
                identity = str(node["addr"])
                if node.get("lifetime") != "Automatic":
                    # Keep identifier and index segments distinct. A literal
                    # escaped identifier containing dots/brackets is not a path.
                    observed = None
                    if scope is not None:
                        observed = (*scope, node["name"]) if scope else node["name"]
                    cells[identity] = SourceStorage((*owner, *path), node["name"],
                        identity in inputs, ports.get(identity), observed)
                return
        for key, child in _children(node):
            visit(child, (*path, key), scope)

    for index, member in enumerate(body.get("members", ())):
        visit(member, ("members", index), scope)
    return cells
