"""Reduce elaborated Yosys JSON to clock-owned state fields.

This module is deliberately standard-library only.  It runs beside Yosys inside the
RTL tool judge, so raw compiler JSON never crosses into the verifier.  Ownership comes
from standard sequential-cell connections and RTLIL net identities; source or signal
names are never classified by spelling.  Top-level observability is recorded as a
separate capability and never changes ownership.
"""

from __future__ import annotations

import json
import re

__all__ = ["source_state_smt2", "state_ownership_document"]


def _storage_term(top, references):
    """Pack MSB-first storage slices without expanding contiguous registers."""
    spans = []
    for name, width, offset in references:
        if spans and spans[-1][:2] == (name, width) and spans[-1][3] == offset + 1:
            spans[-1] = (name, width, spans[-1][2], offset)
        else:
            spans.append((name, width, offset, offset))
    parts = []
    for name, width, high, low in spans:
        term = f"(|{top}_n {name}| state)"
        if width == 1:
            term = f"(ite {term} #b1 #b0)"
        elif (high, low) != (width - 1, 0):
            term = f"((_ extract {high} {low}) {term})"
        parts.append(term)
    while len(parts) > 1:
        parts = [f"(concat {parts[i]} {parts[i + 1]})"
                 if i + 1 < len(parts) else parts[i]
                 for i in range(0, len(parts), 2)]
    return parts[0]


def source_state_smt2(before, after, smt2, top):
    """Expose source storage coordinates across implicit-clock lowering.

    ``async2sync`` preserves each FF cell and its Q-bit order but replaces its
    Q net with private storage and an asynchronous reset mux. Source storage
    before the first event and after each clock edge corresponds to that private
    storage, not to the input-dependent mux output. Follow cell/Q identity across
    the two compiler documents; never infer a mapping from generated wire names.
    All state bits must be covered exactly once. Other transformations are refused.
    """
    ownership = state_ownership_document(before, top)
    header = {"version": "rtl-source-state/1", "fields": [], "issues": []}
    definitions = []
    try:
        if ownership["issues"]:
            raise ValueError("the source state partition is incomplete: "
                             + repr(ownership["issues"]))
        clocks = {(field["clock"], field["edge"]) for field in ownership["fields"]}
        if len(clocks) > 1 or any(edge != "rising" for _, edge in clocks):
            raise ValueError("implicit storage requires one rising-edge clock owner")
        old = before["modules"][top]
        new = after["modules"][top]
        bit_map = {}
        for name, cell in old["cells"].items():
            if cell["type"] not in _CLOCKED:
                continue
            if cell["type"] not in {"$dff", "$dffe", "$adff", "$adffe"}:
                raise ValueError(f"unsupported source storage cell {cell['type']}")
            lowered = new["cells"].get(name)
            if lowered is None or lowered["type"] != "$dff":
                raise ValueError(f"storage cell {name!r} lost its implicit-edge identity")
            source = _wire_bits(cell["connections"]["Q"], "source Q")
            target = _wire_bits(lowered["connections"]["Q"], "lowered Q")
            if len(source) != len(target) or set(source) & set(bit_map):
                raise ValueError("storage Q widths or ownership changed")
            bit_map.update(zip(source, target))

        # Only accessors explicitly exported as registers may carry storage.
        registers = re.findall(r"^; yosys-smt2-register (\S+) (\d+)$", smt2, re.MULTILINE)
        access = {}
        for name, width in registers:
            if any(char in name for char in "|\\\n\r"):
                raise ValueError("the lowered register has no supported SMT symbol")
            bits = _wire_bits(new["netnames"][name]["bits"], "register")
            width = int(width)
            if len(bits) != width:
                raise ValueError("the SMT register width differs from the compiler net")
            for offset, bit in enumerate(bits):
                access.setdefault(bit, (name, width, offset))
        if set(bit_map.values()) != set(access):
            raise ValueError("the source partition does not cover the lowered storage")
        covered = set()
        for field in ownership["fields"]:
            name, width = field["name"], field["width"]
            if any(char in name for char in "|\\\n\r"):
                raise ValueError("the source field has no supported SMT symbol")
            bits = _wire_bits(old["netnames"][name]["bits"], "source field")
            if len(bits) != width or covered.intersection(bits):
                raise ValueError("source fields overlap or change width")
            covered.update(bits)
            term = _storage_term(top, (access[bit_map[bit]] for bit in reversed(bits)))
            sort = "Bool" if width == 1 else f"(_ BitVec {width})"
            if width == 1:
                term = f"(= {term} #b1)"
            definitions.append(
                f"(define-fun |{top}_behave_state {name}| ((state |{top}_s|)) "
                f"{sort} {term})")
            header["fields"].append({"name": name, "width": width})
        if covered != set(bit_map) or len(set(bit_map.values())) != len(bit_map):
            raise ValueError("source and lowered storage are not a bit bijection")
    except (KeyError, ValueError, IndexError) as exc:
        definitions = []
        header["fields"] = []
        header["issues"] = [str(exc)]
    return (smt2 + "\n; behave-source-state "
            + json.dumps(header, sort_keys=True, separators=(",", ":")) + "\n"
            + "\n".join(definitions) + "\n")


_CLOCKED = {
    "$dff", "$dffe", "$adff", "$adffe", "$sdff", "$sdffe", "$sdffce",
    "$aldff", "$aldffe", "$dffsr", "$dffsre",
}
_RESET = {
    "$adff": ("ARST", "ARST_POLARITY"),
    "$adffe": ("ARST", "ARST_POLARITY"),
    "$sdff": ("SRST", "SRST_POLARITY"),
    "$sdffe": ("SRST", "SRST_POLARITY"),
}
_UNSUPPORTED_STATE = {
    "$ff", "$dlatch", "$adlatch", "$dlatchsr", "$sr", "$mem", "$mem_v2",
}
_UNSUPPORTED_STATE_PREFIXES = (
    "$_DFF", "$_SDFF", "$_ALDFF", "$_DLATCH", "$_SR", "$mem",
)


def storage_kind(cell):
    """Shared state-cell boundary for ownership and data-dependency reducers."""
    kind = cell["type"]
    if type(kind) is not str:
        raise ValueError("a state-cell type must be a string")
    if kind in {"$mem", "$mem_v2"}:
        parameters = cell.get("parameters", {})
        if all(type(parameters.get(key)) is str and parameters[key]
               and set(parameters[key]) == {"0"}
               for key in ("WR_PORTS", "RD_CLK_ENABLE")):
            return "combinational"
    if kind in _UNSUPPORTED_STATE or kind.startswith(_UNSUPPORTED_STATE_PREFIXES):
        return "unsupported"
    return "clocked" if kind in _CLOCKED else "combinational"


def _bits(value, what):
    if type(value) is not list:
        raise ValueError(f"{what} is not a bit list")
    out = []
    for bit in value:
        if ((type(bit) is int and bit > 0)
                or (type(bit) is str and bit in {"0", "1", "x", "z"})):
            out.append(bit)
        else:
            raise ValueError(f"{what} contains an invalid wire-or-constant bit")
    return tuple(out)


def _wire_bits(value, what):
    bits = _bits(value, what)
    if any(type(bit) is not int for bit in bits):
        raise ValueError(f"{what} contains a constant instead of a state wire")
    return bits


def _polarity(value, what):
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if type(value) is str and value and set(value) <= {"0", "1"}:
        parsed = int(value, 2)
        if parsed in (0, 1):
            return bool(parsed)
    raise ValueError(f"{what} is not a one-bit polarity")


def _control_bit(value, what):
    if type(value) is not list or len(value) != 1:
        raise ValueError(f"{what} is not a one-bit connection")
    bit = value[0]
    if type(bit) is int and bit > 0:
        return bit
    if type(bit) is str and bit in {"0", "1", "x", "z"}:
        return bit
    raise ValueError(f"{what} is not a wire or constant bit")


def _control_vector(value, what, width):
    bits = _bits(value, what)
    if len(bits) != width:
        raise ValueError(f"{what} does not match the state width")
    return bits


def state_ownership_document(document, top):
    """Return the closed state/clock record for one flattened top module."""

    if type(top) is not str or re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_$]*", top) is None:
        raise ValueError("the ownership top must be an identifier")
    if type(document) is not dict or set(document) - {"creator", "modules"}:
        raise ValueError("Yosys JSON has an unknown top-level field")
    modules = document.get("modules")
    if type(modules) is not dict or set(modules) != {top}:
        raise ValueError("Yosys JSON does not contain exactly the flattened top")
    module = modules[top]
    if type(module) is not dict:
        raise ValueError("the flattened top is not a module record")
    ports = module.get("ports")
    cells = module.get("cells")
    if type(ports) is not dict or type(cells) is not dict:
        raise ValueError("the flattened top lacks ports or cells")

    input_bits = {}
    top_ports = {}
    for name, row in ports.items():
        if type(name) is not str or not name or type(row) is not dict \
                or set(row) - {"direction", "bits", "offset", "upto", "signed"}:
            raise ValueError("a Yosys port has an invalid shape")
        direction = row.get("direction")
        bits = _bits(row.get("bits"), f"port {name!r}")
        if direction == "input":
            for bit in bits:
                if type(bit) is int:
                    input_bits.setdefault(bit, []).append(name)
        elif direction in {"output", "inout"}:
            top_ports[name] = bits
        else:
            raise ValueError(f"port {name!r} has an unknown direction")

    owners = {}
    storage_groups = set()
    issues = []
    for cell_name, cell in cells.items():
        if type(cell_name) is not str or type(cell) is not dict:
            raise ValueError("a Yosys cell has an invalid shape")
        cell_type = cell.get("type")
        if type(cell_type) is not str:
            raise ValueError("a Yosys cell has no exact type")
        classification = storage_kind(cell)
        if classification == "unsupported":
            issues.append({"kind": "unsupported-state-cell", "cell": cell_name})
            continue
        if classification != "clocked":
            continue
        connections = cell.get("connections")
        parameters = cell.get("parameters", {})
        if type(connections) is not dict or type(parameters) is not dict:
            raise ValueError("a clocked Yosys cell lacks connections or parameters")
        try:
            clock = _control_bit(
                connections["CLK"], f"cell {cell_name!r} CLK")
            q_bits = _wire_bits(connections["Q"], f"cell {cell_name!r} Q")
            rising = _polarity(
                parameters.get("CLK_POLARITY", "1"),
                f"cell {cell_name!r} CLK_POLARITY")
        except (KeyError, ValueError):
            issues.append({"kind": "clocked-cell-shape", "cell": cell_name})
            continue
        if not q_bits:
            issues.append({"kind": "clocked-cell-shape", "cell": cell_name})
            continue
        reset = None
        reset_active_low = None
        if cell_type in _RESET:
            reset_port, polarity_name = _RESET[cell_type]
            try:
                reset_bit = _control_bit(
                    connections[reset_port], f"cell {cell_name!r} {reset_port}")
                reset_polarity = _polarity(
                    parameters[polarity_name],
                    f"cell {cell_name!r} {polarity_name}")
            except (KeyError, ValueError):
                issues.append({"kind": "clocked-cell-shape", "cell": cell_name})
                continue
            reset = reset_bit
            reset_active_low = not reset_polarity
            if reset_port == "SRST" and reset_bit not in input_bits:
                # A synthesized synchronous clear is ordinary D-path logic on
                # the same clock (e.g. rst || !enable), not an extra event domain.
                # Retain clock ownership without claiming that a task reset port
                # alone restores it. The reset monitor checks the complete owned
                # state, including this field; async generated resets still refuse.
                reset = None
                reset_active_low = None
        elif cell_type == "$sdffce":
            # Unlike $sdffe, $sdffce gives enable priority over synchronous reset.
            # Merely holding the reset port therefore does not define a restoration
            # transition for this state unless another environment constraint also
            # establishes enable.  The task's reset claim has no such hidden premise.
            try:
                _control_bit(
                    connections["SRST"], f"cell {cell_name!r} SRST")
                _polarity(parameters["SRST_POLARITY"],
                          f"cell {cell_name!r} SRST_POLARITY")
                _control_bit(connections["EN"], f"cell {cell_name!r} EN")
                _polarity(parameters["EN_POLARITY"],
                          f"cell {cell_name!r} EN_POLARITY")
            except (KeyError, ValueError):
                issues.append({"kind": "clocked-cell-shape", "cell": cell_name})
                continue
            issues.append({"kind": "reset-semantics-unsupported", "cell": cell_name})
        elif cell_type in {"$aldff", "$aldffe"}:
            # An asynchronous load is not a reset: its loaded value is another input.
            # Parse the control/polarity before refusing so malformed cells cannot hide
            # behind the unsupported-semantics answer.
            try:
                _control_bit(
                    connections["ALOAD"], f"cell {cell_name!r} ALOAD")
                _polarity(parameters["ALOAD_POLARITY"],
                          f"cell {cell_name!r} ALOAD_POLARITY")
                _control_vector(
                    connections["AD"], f"cell {cell_name!r} AD", len(q_bits))
            except (KeyError, ValueError):
                issues.append({"kind": "clocked-cell-shape", "cell": cell_name})
                continue
            issues.append({"kind": "reset-semantics-unsupported", "cell": cell_name})
        elif cell_type in {"$dffsr", "$dffsre"}:
            try:
                _control_vector(
                    connections["SET"], f"cell {cell_name!r} SET", len(q_bits))
                _control_vector(
                    connections["CLR"], f"cell {cell_name!r} CLR", len(q_bits))
                _polarity(parameters["SET_POLARITY"],
                          f"cell {cell_name!r} SET_POLARITY")
                _polarity(parameters["CLR_POLARITY"],
                          f"cell {cell_name!r} CLR_POLARITY")
                if cell_type == "$dffsre":
                    _control_bit(connections["EN"], f"cell {cell_name!r} EN")
                    _polarity(parameters["EN_POLARITY"],
                              f"cell {cell_name!r} EN_POLARITY")
            except (KeyError, ValueError):
                issues.append({"kind": "clocked-cell-shape", "cell": cell_name})
                continue
            issues.append({"kind": "reset-semantics-unsupported", "cell": cell_name})
        owner = (
            clock, "rising" if rising else "falling",
            reset, reset_active_low)
        storage_groups.add(frozenset(q_bits))
        for bit in q_bits:
            previous = owners.get(bit)
            if previous is not None:
                issues.append({
                    "kind": ("duplicate-state-bit" if previous == owner
                             else "mixed-state-bit"),
                    "bit": bit,
                })
            owners[bit] = owner

    netnames = module.get("netnames")
    if type(netnames) is not dict:
        raise ValueError("the flattened top lacks net identities")
    candidates = []
    for name, row in netnames.items():
        if type(name) is not str or not name or type(row) is not dict \
                or set(row) - {"hide_name", "bits", "attributes", "offset", "upto",
                               "signed"}:
            raise ValueError("a Yosys net identity has an invalid shape")
        bits = _bits(row.get("bits"), f"net {name!r}")
        if not bits or any(type(bit) is not int for bit in bits) \
                or len(set(bits)) != len(bits) \
                or not all(bit in owners for bit in bits):
            continue
        field_owners = {owners[bit] for bit in bits}
        if len(field_owners) != 1:
            # A cross-domain aggregate is not a state field.  Its component nets remain
            # candidates, so mixed ownership is neither guessed nor globally refused.
            continue
        hidden = row.get("hide_name")
        if type(hidden) not in (bool, int) or hidden not in (0, 1, False, True):
            raise ValueError(f"net {name!r} has an invalid hide_name flag")
        # Prefer a complete FF Q carrier over slices/concatenations of other
        # registers. A wide combinational alias can otherwise consume parts of
        # several carriers and make a complete disjoint partition appear absent.
        # Bit order remains the net's order; only membership selects a carrier.
        candidates.append((frozenset(bits) not in storage_groups,
                           bool(hidden), -len(bits), name, bits,
                           next(iter(field_owners))))

    fields = []
    covered = set()
    overlapping = False
    for _alias, _hidden, _negative_width, name, bits, owner in sorted(candidates):
        bit_set = set(bits)
        if bit_set & covered:
            overlapping = overlapping or bool(bit_set - covered)
            continue
        clock_bit, edge, reset_bit, reset_active_low = owner
        clocks = sorted(input_bits.get(clock_bit, ()))
        if not clocks:
            issues.append({"kind": "generated-clock", "field": name})
            continue
        if len(clocks) != 1:
            issues.append({"kind": "ambiguous-clock", "field": name})
            continue
        reset_name = None
        if reset_bit is not None:
            resets = sorted(input_bits.get(reset_bit, ()))
            if not resets:
                issues.append({"kind": "generated-reset", "field": name})
                continue
            if len(resets) != 1:
                issues.append({"kind": "ambiguous-reset", "field": name})
                continue
            reset_name = resets[0]
        fields.append({
            "name": name,
            "width": len(bits),
            "clock": clocks[0],
            "edge": edge,
            "reset": reset_name,
            "reset_active_low": reset_active_low,
            "observable": name in top_ports and top_ports[name] == bits,
        })
        covered.update(bits)

    if set(owners) - covered:
        issues.append({
            "kind": ("overlapping-state-nets" if overlapping
                     else "unnamed-state"),
            "count": len(set(owners) - covered),
        })
    issues.sort(key=lambda row: tuple((key, repr(row[key]))
                                      for key in sorted(row)))
    return {
        "version": "rtl-state-clock-ownership/1",
        "top": top,
        "fields": fields,
        "issues": issues,
    }
