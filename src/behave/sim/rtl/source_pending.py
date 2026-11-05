"""Compiler-bound pending activations, independent of goal outcome predicates.

Read ranges and write occurrences join by compiler symbol identity within one
parse. The compiler also resolves each consumer scope from the original write
site; neither source names nor simulator variable names define that join.
"""

from dataclasses import dataclass, field

from .source_declarations import _children, _nodes
from .source_elaboration import constant_environments, parameter_partition
from .source_index import affine_index
from .source_integral import typed_symbols
from .source_scheduling import blocking_write_parts, blocking_write_watch, conditional_pending_writer
from .source_write_index import SourceWriteIndex

_INCREMENTS = {"Preincrement", "Predecrement", "Postincrement", "Postdecrement"}
_BASE = "__behave_source_write_base"
SourceSpan = tuple[int, int]
HeaderParts = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]


@dataclass(slots=True)
class BlockingNotifications:
    statements: dict[SourceSpan, tuple[str, str]] = field(default_factory=dict)
    headers: dict[SourceSpan, dict[SourceSpan, HeaderParts]] = field(default_factory=dict)
    declarations: dict[SourceSpan, str] = field(default_factory=dict)


def read_subscribers(document, guarded, pending_layout):
    """Index compiler-bound notifications by storage and original write site."""
    result, storage = {}, {}
    for row in document:
        process = row["start"], row["end"]
        if process not in guarded:
            continue
        for read in row["reads"]:
            if "write_references" not in read:
                raise ValueError("pending source activations require RTL frontend revision 6")
            for write in read["write_references"]:
                identity = str(write.get("symbol_id", read["symbol_id"]))
                writes = result.setdefault(identity, {})
                storage.setdefault(identity, set()).add(str(read["symbol_id"]))
                coordinates = write["file"], write["start"], write["end"]
                notifications = writes.setdefault(coordinates, set())
                if write["self_blocking"]:
                    continue
                pending = write["scope_reference"] + pending_layout.reference(process[0])
                reference = write.get("storage_reference", _BASE)
                notifications.update((projection["expression"].replace(_BASE, reference), pending)
                                     for projection in read["projections"])
    return result, storage


def _written_bases(node):
    kind = node.get("kind")
    if kind in {"NamedValue", "HierarchicalValue"}:
        return (node,)
    if kind in {"ElementSelect", "RangeSelect", "MemberAccess"}:
        return _written_bases(node["value"])
    if kind == "Concatenation":
        return tuple(base for operand in node["operands"] for base in _written_bases(operand))
    raise ValueError("pending source write needs a resolved variable lvalue")


def _mutation(node):
    if node.get("kind") == "Assignment" and not node.get("isNonBlocking"):
        return node["left"]
    if node.get("kind") == "UnaryOp" and node.get("op") in _INCREMENTS:
        return node["operand"]
    return None


def _write_locations(node, symbols, stable):
    """Bind array/bit paths without mistaking a shared container for one cell.

    Affine selectors use compiler symbol identities, types and unchanged source
    values, never spelling or guessed loop bounds. Part selects and aggregate
    aliases retain the whole-storage rule until their footprints are bound.
    """
    kind = node.get("kind")
    if kind in {"NamedValue", "HierarchicalValue"}:
        return ((node["symbol"].split(" ", 1)[0], ()),)
    if kind == "Concatenation":
        return tuple(location for operand in node["operands"]
                     for location in _write_locations(operand, symbols, stable))
    if kind == "ElementSelect":
        index = affine_index(node["selector"], symbols, stable=stable)
        return tuple((identity, (*path, index) if path is not None else None)
                     for identity, path in _write_locations(node["value"], symbols, stable))
    return tuple((base["symbol"].split(" ", 1)[0], None) for base in _written_bases(node))


def _separate_locations(left, right):
    # A whole container overlaps all its descendants. Paths first differing
    # at an element select name disjoint physical elements.
    return left is not None and right is not None and any(
        first is not None and second is not None and first.disjoint(second)
        for first, second in zip(left, right))


def blocking_notifications(source, document, declarations, sensitivity, guarded, *,
                           filename, pending_layout, index=None):
    """Bind notifications around blocking statements and for-header expressions.

    Nonblocking enqueue/commit notifications are a separate scheduler operation,
    not a post-statement read of a value which has not yet been assigned. A
    statement may mutate several independent subscribed variables (for example
    a post-incremented index and an array cell). Before/after comparison is exact
    when each is written at most once; repeated writes need primitive scheduling.
    """
    result = BlockingNotifications()
    subscribers, storage = read_subscribers(sensitivity, guarded, pending_layout)
    if not subscribers:
        return result
    index = index or SourceWriteIndex(source, document, declarations, sensitivity, filename=filename)
    original, span, nodes = index.source, index.span, index.nodes
    symbols = typed_symbols(document)
    continuous = {id(node["assignment"]) for node in nodes if node.get("kind") == "ContinuousAssign"}
    def watches_for(lvalue):
        watches = set()
        for base in _written_bases(lvalue):
            identity = base["symbol"].split(" ", 1)[0]
            readers = subscribers.get(identity)
            if readers is None:
                continue
            first, last = span(base)
            notifications = readers.get((filename, first, last))
            if notifications is None:
                raise ValueError("pending source write has no compiler-bound consumer reference")
            reference = original[first:last].decode("utf-8") + " "
            watches.update((template.replace(_BASE, reference), pending)
                           for template, pending in notifications)
        return tuple(sorted(watches))

    handled, groups = set(), {}

    def watched(expression):
        mutations, written, watches = [], {}, set()
        parts = tuple(_nodes(expression))
        targets = [(part, target) for part in parts if (target := _mutation(part)) is not None]
        modified = {base["symbol"].split(" ", 1)[0]
                    for _, target in targets for base in _written_bases(target)}
        local_calls = all(part.get("kind") != "Call" or span(part) in index.frame_local_calls
                          for part in parts)
        def stable(symbol):
            return local_calls and symbol not in modified
        for part, target in targets:
            for identity, path in _write_locations(target, symbols, stable):
                for actual in storage.get(identity, ()):
                    # Distinct ref formals can name the same storage. The exact
                    # read-range watch handles one primitive write; cancelling
                    # writes in a single expression still need primitive order.
                    footprint = path if actual == identity else None
                    previous = written.setdefault(actual, [])
                    if any(not _separate_locations(footprint, other) for other in previous):
                        raise ValueError("repeated source writes in one expression need primitive write scheduling")
                    previous.append(footprint)
            watches.update(watches_for(target))
            mutations.append(span(part))
        return mutations, tuple(sorted(watches))

    for node in nodes:
        if node.get("kind") not in {"ExpressionStatement", "ForLoop"} or not index.selected(node):
            continue
        if node["kind"] == "ForLoop":
            for expression in (*node.get("initializers", ()), *node.get("steps", ())):
                mutations, watches = watched(expression)
                coordinates = span(expression)
                groups.setdefault((span(node), coordinates), []).append((id(expression), watches))
                handled.update(mutations)
            continue
        mutations, watches = watched(node["expr"])
        groups.setdefault((None, span(node)), []).append((id(node), watches))
        handled.update(mutations)

    # Empty outcomes matter: a write watched in one instance must not notify
    # consumers in another instance with no corresponding subscription.
    varied = {identity for members in groups.values() if len({watches for _, watches in members}) > 1
              for identity, _ in members}
    environments = (constant_environments(document, _children, typed_symbols(document), varied)
                    if varied else {})
    for (loop, coordinates), members in groups.items():
        watches = tuple(sorted({watch for _, outcome in members for watch in outcome}))
        if not watches:
            continue
        enabled, writers = {}, {}
        if members[0][0] in varied:
            partition = parameter_partition([dict(environments[identity]) for identity, _ in members],
                                            [outcome for _, outcome in members])
            constants = []
            for ordinal, watch in enumerate(watches):
                selectors = [selector for selector, outcome in partition if watch in outcome]
                if len(selectors) == len(partition):
                    continue
                name = f"__behave_native_event_write_{coordinates[0]}_enabled_{ordinal}"
                constants.append(f"localparam bit {name}=" + " || ".join(f"({s})" for s in selectors) + ";\n")
                enabled[watch] = name
                # Slots share one bank layout. If that bank is bound in
                # every instance, its reference stays at the original write
                # site; only the slot's notification condition varies.
                owner = pending_layout.owner_reference(watch[1])
                if all(any(pending_layout.owner_reference(other[1]) == owner for other in outcome)
                       for _, outcome in partition):
                    continue
                writer = f"__behave_native_event_write_{coordinates[0]}_notify_{ordinal}"
                constants.append(conditional_pending_writer(writer, name, watch[1]))
                writers[watch] = writer + ".latch"
            result.declarations[coordinates] = "".join(constants)
        if loop is None:
            result.statements[coordinates] = blocking_write_watch(
                coordinates[0], watches, enabled=enabled, writers=writers)
        else:
            declarations, before, after = blocking_write_parts(
                coordinates[0], watches, enabled=enabled, writers=writers)
            # For-initializer lists require a simple assignment name, not an
            # indexed pending-bit lvalue. Retain the original ordered expression.
            ignored = f"__behave_native_event_write_{coordinates[0]}_ignored"
            result.headers.setdefault(loop, {})[coordinates] = (
                (*declarations, f"bit {ignored};"), before,
                tuple(f"{ignored}=({assignment})" for assignment in after))
    for node in nodes:
        if id(node) in continuous:
            # A continuous driver is not a procedural blocking write, even
            # though Slang also represents its expression as an Assignment.
            continue
        target = _mutation(node)
        if target is None:
            continue
        coordinates = span(node)
        if index.selected(node) and coordinates not in handled and watches_for(target):
            raise ValueError("pending source write inside an expression needs primitive write scheduling")
    return result
