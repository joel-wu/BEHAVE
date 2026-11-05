"""Resolve source process edges to the Driver's top-level signal names.

This is direct-edge encoding metadata, not a native callback admission test
or a second goal inventory. Bindings follow the
selected typed instance tree: each specialization gets its own port environment.
Only direct continuous aliases preserve a signal's edge identity. Computed or
stateful clocks remain unresolved instead of borrowing a similarly named pin.
"""


def _identity(reference):
    return reference.split(" ", 1)[0]


def signal_of(expression, bindings, active=frozenset()):
    """A top input name, or None when the expression is not a proven alias."""
    if not isinstance(expression, dict) or expression.get("kind") != "NamedValue":
        return None
    identity = _identity(expression["symbol"])
    if identity in active:
        return None
    bound = bindings.get(identity)
    if isinstance(bound, str) or bound is None:
        return bound
    return signal_of(bound, bindings, active | {identity})


def scope_bindings(node, inherited):
    """Add direct continuous drivers in this scope; never follow procedures."""
    bindings = dict(inherited)
    drivers = {}
    for member in node.get("members", ()):
        expression = target = None
        if member.get("kind") == "Net" and "initializer" in member:
            target, expression = str(member["addr"]), member["initializer"]
        elif member.get("kind") == "ContinuousAssign":
            assignment = member["assignment"]
            left = assignment.get("left", {})
            if left.get("kind") == "NamedValue":
                target = _identity(left["symbol"])
                expression = assignment["right"]
        if target is not None:
            drivers.setdefault(target, []).append(expression)
    for identity, values in drivers.items():
        bindings[identity] = values[0] if len(values) == 1 else None
    return bindings


def instance_bindings(body, connections, inherited, *, top):
    bindings = dict(inherited)
    ports = {member["name"]: member for member in body.get("members", ())
             if member.get("kind") == "Port" and member.get("direction") == "In"}
    if top:
        for port in ports.values():
            bindings[_identity(port["internalSymbol"])] = port["name"]
    else:
        # The serializer can share one InstanceBody while giving each instance's
        # connection its own Port address. Match the compiler-resolved formal to
        # that body's declaration, not to the first instance's actual argument.
        for connection in connections:
            port = ports.get(connection["port"]["name"])
            if port is not None:
                bindings[_identity(port["internalSymbol"])] = signal_of(
                    connection.get("expr"), inherited)
    return scope_bindings(body, bindings)


def process_triggers(process, bindings):
    """Preserve every sensitivity alternative; unknown is not 'never active'."""
    if process["procedureKind"] in {"AlwaysComb", "AlwaysLatch"}:
        return (("always", ""),)
    body = process["body"]
    timing = body.get("timing", {}) if body.get("kind") == "Timed" else {}

    def events(node):
        kind = node.get("kind")
        if kind == "ImplicitEvent":
            return (("always", ""),)
        if kind == "EventList":
            groups = [events(event) for event in node["events"]]
            return None if any(group is None for group in groups) else tuple(
                event for group in groups for event in group)
        if kind != "SignalEvent" or node.get("edge") not in {"PosEdge", "NegEdge"}:
            return None
        signal = signal_of(node["expr"], bindings)
        if signal is None:
            return None
        return (("posedge" if node["edge"] == "PosEdge" else "negedge", signal),)

    return events(timing)


def check_source_triggers(declarations, allowed):
    """Require top-level edge ownership for the direct-edge action binding."""
    for context in declarations.contexts:
        if context.triggers is None or set(context.triggers) - allowed:
            raise ValueError(
                f"source process at {context.scope}:{context.process_start} has "
                f"non-Driver-owned edge triggers: {context.triggers}")
