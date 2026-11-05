"""Compiler-bound port nets and transfers for the source event scheduler.

Compatible wire connections share physical storage, not a process that copies
its final value later. Typed contiguous views preserve packed orientation,
subscriptions and intermediate edges. Variable and computed ports instead use
the compiler's typed assignment as an implicit continuous driver.
Resolved/multiple drivers and noncontiguous net partitions need other semantics.
"""

from dataclasses import dataclass

from ..symbolic import ir
from ..symbolic import ranges
from ..core.refusal import Refusal, Refused
from .source_declarations import _nodes
from .source_integral import IntegralType, integral_value_facts, integer_literal
from .source_packed import selection_offset
from .source_values import source_type


@dataclass(frozen=True, slots=True)
class NetView:
    owner: str
    offset: int
    kind: IntegralType


class _NetGroups:
    """Weighted unions of contiguous views; offsets count physical LSB bits."""

    def __init__(self, symbols, bindings):
        self.symbols, self.bindings = symbols, bindings
        self.parents = {}

    def kind(self, identity):
        return source_type(self.symbols[identity]["type"], self.symbols)

    def root(self, identity):
        path, offset = [], 0
        while identity in self.parents:
            parent, delta = self.parents[identity]
            path.append((identity, offset))
            identity, offset = parent, offset + delta
        for child, before in path:
            self.parents[child] = identity, offset - before
        return identity, offset

    def join(self, child, parent, offset):
        inner, inner_offset = self.root(child)
        outer, outer_offset = self.root(parent)
        delta = outer_offset + offset - inner_offset
        if inner == outer:
            if delta:
                raise ValueError("this net connection shorts different bits of one storage")
            return
        inner_width, outer_width = self.kind(inner).width, self.kind(outer).width
        if 0 <= delta and delta + inner_width <= outer_width:
            self.parents[inner] = outer, delta
        elif delta <= 0 and outer_width - delta <= inner_width:
            self.parents[outer] = inner, -delta
        else:
            raise ValueError("overlapping net containers require noncontiguous bit partitions")

    def finish(self):
        groups, bindings, views = {}, dict(self.bindings), {}
        for identity in bindings:
            owner, offset = self.root(identity)
            groups.setdefault(owner, []).append((identity, offset))
        for root, members in groups.items():
            if len(members) == 1:
                continue
            width = self.kind(root).width
            inputs = [identity for identity, _ in members if bindings[identity].input]
            whole = [identity for identity, offset in members
                     if offset == 0 and self.kind(identity).width == width]
            if len(inputs) > 1 or inputs and inputs[0] not in whole:
                raise Refused(Refusal("rtl-source-scheduler-connected-inputs",
                    "the external Driver must own one complete collapsed net, not aliased input fragments"))
            owner = (inputs[0] if inputs else min(whole,
                key=lambda identity: (len(bindings[identity].key), repr(bindings[identity].key))))
            field = bindings[owner]
            for identity, offset in members:
                bindings[identity] = field
                if identity != owner:
                    views[identity] = NetView(owner, offset, self.kind(identity))
        return bindings, views


def _net_selection(node, symbols):
    """Compiler-constant contiguous net lvalue, including packed orientation."""
    kind = source_type(node["type"], symbols)
    if not isinstance(kind, IntegralType):
        raise ValueError("net connection needs an integral packed view")
    if node["kind"] == "Conversion":
        if source_type(node["operand"]["type"], symbols).width != kind.width:
            raise ValueError("resized nets need an explicit extension/truncation relation")
        return _net_selection(node["operand"], symbols)
    if node["kind"] in {"NamedValue", "HierarchicalValue"}:
        return node["symbol"].split(" ", 1)[0], 0, kind.width
    if node["kind"] not in {"ElementSelect", "RangeSelect"}:
        raise ValueError("this net expression needs noncontiguous bit partitions")
    owner, offset, _ = _net_selection(node["value"], symbols)
    parent = source_type(node["value"]["type"], symbols)
    if node["kind"] == "ElementSelect":
        index = node["selector"].get("constant")
        count, descending = 1, False
    else:
        count = node["right"].get("constant")
        if count is None:
            raise ValueError("net slice needs its elaborated width or bound")
        selection = node["selectionKind"]
        if selection == "Simple":
            index, count, descending = count, 1, False
        elif selection in {"IndexedUp", "IndexedDown"}:
            index, count, descending = node["left"].get("constant"), integer_literal(count), selection == "IndexedDown"
        else:
            raise ValueError("net slice needs its compiler selection kind")
    if index is None:
        raise ValueError("dynamic net selection needs its changing driver relation")
    selected = ranges.bounds_of_expression(selection_offset(parent, ir.Lit(integer_literal(index)),
        count, descending_select=descending), {})
    low = selected.lo
    if low < 0 or low + kind.width > parent.width:
        raise ValueError("net slice extends outside its physical storage")
    return owner, offset + low, kind.width


def startup_input_aliases(document):
    """Native input mirrors of compiler-collapsed top-input nets, including slices.

    These are not arbitrary child inputs or continuous assignments. They share
    storage in the source relation and must see the same bits before HDL initial
    blocks run, even when the simulator retains separate public mirrors.
    """
    from .source_integral import typed_symbols
    from .source_storage import storage_bindings

    symbols = typed_symbols(document)
    original = storage_bindings(document, symbols=symbols)
    bindings, views, _ = bind_port_connections(symbols, original)
    aliases = []
    for identity, view in views.items():
        owner, field = bindings[view.owner], original[identity]
        if not owner.input:
            continue
        path = field.observation
        if path is None:
            raise Refused(Refusal('rtl-startup-input-alias-unmapped',
                'a collapsed input net requires a compiler-owned native path', field=field.name))
        aliases.append(((path,) if isinstance(path, str) else path,
                        view.kind.width, owner.port, view.offset))
    return tuple(sorted(set(aliases), key=repr))


def bind_port_connections(symbols, bindings):
    """Return physical net groups and implicit, goal-free typed assignments."""
    nets = _NetGroups(symbols, bindings)
    transfers = []

    def refuse(instance, port, why):
        raise Refused(Refusal("rtl-source-scheduler-port-propagation-unbound", why,
            instance=instance.get("name"), port=port.get("name")))

    def wire(node):
        net = node.get("netType")
        if isinstance(net, str):
            net = symbols[net.split(" ", 1)[0]]
        return node.get("kind") == "Net" and net is not None and net.get("name") == "wire"

    def net_lvalue(node):
        # Context conversions do not turn a net into an independent process.
        if node.get("kind") == "Conversion" and node.get("conversionKind") != "Explicit":
            return net_lvalue(node["operand"])
        if node.get("kind") in {"ElementSelect", "RangeSelect"}:
            return net_lvalue(node["value"])
        if node.get("kind") == "Concatenation":
            return any(net_lvalue(part) for part in node["operands"])
        return (node.get("kind") in {"NamedValue", "HierarchicalValue"}
                and symbols[node["symbol"].split(" ", 1)[0]].get("kind") == "Net")

    def replace_empty(node, reference):
        if isinstance(node, dict):
            if node.get("kind") == "EmptyArgument":
                return {**node, **reference}
            return {key: replace_empty(value, reference) for key, value in node.items()}
        if isinstance(node, list):
            return [replace_empty(value, reference) for value in node]
        return node

    for instance in symbols.values():
        if instance.get("kind") != "Instance" or instance.get("isUninstantiated"):
            continue
        for connection in instance.get("connections", ()):
            port, actual = connection["port"], connection.get("expr") or {}
            if port.get("direction") == "Out" and not actual:
                continue  # An unconnected output has no implicit external driver.
            original = actual
            if port.get("direction") == "Out" and actual.get("kind") == "Assignment":
                actual = actual["left"]
            elif port.get("direction") != "In":
                refuse(instance, port, "this port direction needs an explicit connection relation")
            internal = port.get("internalSymbol", "").split(" ", 1)[0]
            if internal not in bindings:
                refuse(instance, port, "the connection lacks individually bound physical storage")
            child = symbols[internal]
            if wire(child) and net_lvalue(actual):
                try:
                    external, offset, width = _net_selection(actual, symbols)
                except ValueError as exc:
                    refuse(instance, port, str(exc))
                if external not in bindings:
                    refuse(instance, port, "the connection lacks individually bound physical storage")
                parent = symbols[external]
                child_type = source_type(child["type"], symbols)
                if (not wire(parent) or not isinstance(child_type, IntegralType) or child_type.width != width
                        or integral_value_facts(child["type"], symbols)[2]
                        != integral_value_facts(parent["type"], symbols)[2]):
                    refuse(instance, port, "a resized or resolved net connection needs its bit-level relation")
                try:
                    nets.join(internal, external, offset)
                except ValueError as exc:
                    refuse(instance, port, str(exc))
                continue

            for node in (child, *_nodes(original)):
                referenced = (symbols[node["symbol"].split(" ", 1)[0]]
                              if node.get("kind") in {"NamedValue", "HierarchicalValue"} else node)
                if referenced.get("kind") == "Net" and not wire(referenced):
                    refuse(instance, port, "a resolved net needs its driver-resolution relation")
                if ((node.get("kind") == "Assignment" and (node is not original or port["direction"] != "Out"))
                        or node.get("kind") == "UnaryOp" and node.get("op") in {
                            "Preincrement", "Postincrement", "Predecrement", "Postdecrement"}
                        or node.get("kind") == "Call" and node.get("subroutine") not in {"$signed", "$unsigned"}):
                    refuse(instance, port, "effectful/helper port expressions need source occurrence binding")
            if not actual:
                refuse(instance, port, "an undriven input needs its four-state source value")
            reference = {"kind": "NamedValue", "type": child["type"],
                         "symbol": port["internalSymbol"]}
            if port["direction"] == "Out":
                transfers.append(replace_empty(original, reference))
            else:
                coordinates = {key: value for key, value in actual.items() if key.startswith("source_")}
                transfers.append({**coordinates, "kind": "Assignment", "type": child["type"],
                    "left": {**coordinates, **reference}, "right": actual, "isNonBlocking": False})

    bindings, views = nets.finish()
    return bindings, views, tuple(transfers)
