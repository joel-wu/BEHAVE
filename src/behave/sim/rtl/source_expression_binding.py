"""Bind source expression events to unchanged compiler ports and short circuits.

The selector is the value at expression evaluation, including preceding blocking
assignments. It is not the final output or a re-evaluated source expression.
Missing/pruned or multiply instantiated matches remain unencoded; source spans
alone must not choose one of several loop/helper copies.
"""

import re

CellKey = tuple[str, str, str, str]
ExpressionCells = dict[CellKey, dict[str, str] | None]

_SRC = re.compile(r'attribute \\src "([^"\n]+)"')


def expression_cells(rtlil: str, cell_scopes: dict[str, str]) -> ExpressionCells:
    """Index original mux/logical ports once; ambiguous copies stay unencoded."""
    module, source, cell, ports = "", "", None, {}
    result = {}
    for line in rtlil.splitlines():
        text = line.strip()
        if found := _SRC.fullmatch(text):
            source = found[1]
            continue
        if text.startswith("attribute "):
            continue
        if text.startswith("module "):
            module = text.split()[1].lstrip("\\")
        if text.startswith("cell "):
            _, kind, name = text.split()
            cell = ((module, cell_scopes.get(name, ""), kind, set(source.split("|")))
                    if kind in {"$mux", "$logic_and", "$logic_or"} else None)
            ports = {}
        elif cell is not None and text.startswith("connect "):
            _, port, signal = text.split(maxsplit=2)
            ports[port.lstrip("\\")] = signal
        elif text == "end":
            if cell is not None:
                owner, scope, kind, origins = cell
                for origin in origins:
                    key = (owner, scope, kind, origin)
                    result[key] = None if key in result else ports
            cell = None
        source = ""
    return result


def expression_levels(plan, event, process, cells: ExpressionCells, process_scopes, aliases,
                      *, filename) -> tuple[tuple[str, str, tuple[str, ...]], ...] | None:
    # One physical copy only. A repeated source range needs an additional
    # iteration/call-context certificate, not an arbitrary cell-list ordinal.
    if event.contexts or event.calls:
        return None
    scope = process_scopes.get(process.process, "")

    def ports_at(start, end, kind):
        first, last = (plan.compiler.original_position(offset) for offset in (start, end))
        origin = f"{filename}:{first[0]}.{first[1]}-{last[0]}.{last[1]}"
        return cells.get((process.module, scope, kind, origin))

    def truth(signal, value):
        signal = " ".join(aliases.get(token, token) for token in signal.split())
        # The operand may be a vector. Truth is nonzero, not equality to one.
        return signal, "default" if value else "0", ("0", "default")

    levels, ports = [], None
    for operand in event.logical_path:
        ports = ports_at(operand.start, operand.end, operand.cell)
        if ports is None or "A" not in ports or "B" not in ports:
            return None
        if operand.port == "B":
            levels.append(truth(ports["A"], operand.cell == "$logic_and"))
    if event.role.startswith("boolean-"):
        if ports is None:
            return None
        levels.append(truth(ports[event.logical_path[-1].port], event.choice == "true"))
    elif event.role.startswith("expression-"):
        ports = ports_at(event.start, event.end, "$mux")
        if ports is None or "S" not in ports:
            return None
        levels.append(truth(ports["S"], event.choice == "true"))
    return tuple(levels)
