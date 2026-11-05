"""Publish source-write history at NBA commit, not at RHS evaluation.

Each scope/read projection has one shared queue summary and each consumer has
an independent acknowledgement. A committed epoch and change bit
are updated together by an NBA. Consumers acknowledge epochs even when the
queue made no change. Blocking writes can rebase a pending queue before commit.
Source assignments remain the only writers of artifact storage.
Argument-only helpers are zero-time functions. Scope-owned updates stay at
the source call site; publication still schedules an NBA, never an immediate update.
"""

from dataclasses import dataclass, field

from .source_declarations import _nodes
from .source_edits import Insertion, Replacement, SourceEdits
from .source_pending import _BASE, _mutation, _written_bases


@dataclass(frozen=True, slots=True)
class DeferredRead:
    name: str
    width: int
    consumer: int | None = None

    @property
    def seen(self) -> str:
        suffix = "" if self.consumer is None else f"_{self.consumer}"
        return f"{self.name}_seen{suffix}"

    @property
    def pending(self) -> str:
        return f"({self.seen} != {self.name}_commit[1] && {self.name}_commit[0])"

    @property
    def consume(self) -> str:
        return f"{self.seen}={self.name}_commit[1];"

    @property
    def acknowledgement(self) -> str:
        return f"bit {self.seen}=0;\n"

    @property
    def declarations(self) -> str:
        return self.helper_declarations + self.acknowledgement + self.history_declarations

    @property
    def helper_declarations(self) -> str:
        """One argument-only implementation per width, not per read subscriber.

        Keep four-state per-bit comparisons unchanged. These private helpers
        receive distinct observer-owned fields; reference arguments avoid
        copying every wide history vector at every subscriber call. They do
        not suspend or call source code, so no caller can observe intermediate
        updates instead of the former copy-back at return.
        Only these argument-only helpers request retained backend call frames;
        epoch publication stays at the call site, with explicit state arguments.
        """
        w = self.width
        return f"""
function automatic bit __behave_native_history_changed_{w}(
    const ref bit [{w-1}:0] touched,
    const ref logic [{w-1}:0] first, base);
  /* verilator no_inline_task */
  bit changed=0;
  for(int k=0;k<{w};k++)
    if(touched[k] && (first[k] !== base[k])) changed=1;
  return changed;
endfunction
function automatic void __behave_native_history_enqueue_{w}(
    const ref logic [{w-1}:0] mask, value,
    ref bit [{w-1}:0] touched,
    ref logic [{w-1}:0] first,
    ref bit varied);
  /* verilator no_inline_task */
  for(int k=0;k<{w};k++) begin
    if(mask[k]) begin
      if(touched[k]) begin
        if(first[k] !== value[k]) varied=1;
      end else first[k]=value[k];
      touched[k]=1;
    end
  end
endfunction
function automatic bit [1:0] __behave_native_history_update_{w}(
    const ref logic [{w-1}:0] mask, value, before_commit,
    const ref bit [1:0] committed,
    ref bit batch,
    ref bit [{w-1}:0] touched,
    ref logic [{w-1}:0] first, base,
    ref bit varied);
  /* verilator no_inline_task */
  if(batch == committed[1]) begin
    batch=~committed[1]; touched='0; varied=0; base=before_commit;
  end
  __behave_native_history_enqueue_{w}(mask, value, touched, first, varied);
  return {{batch, varied | __behave_native_history_changed_{w}(touched, first, base)}};
endfunction
"""

    @property
    def history_declarations(self) -> str:
        p, w = self.name, self.width
        return f"""
bit [1:0] {p}_commit=0;
bit {p}_batch=0, {p}_varied=0;
bit [{w-1}:0] {p}_touched='0;
logic [{w-1}:0] {p}_first, {p}_base;
"""

    @property
    def publish(self) -> str:
        p, w = self.name, self.width
        return (f"{p}_commit <= {{{p}_batch, {p}_varied | "
                f"__behave_native_history_changed_{w}({p}_touched, {p}_first, {p}_base)}};")

    def enqueue(self, mask: str, value: str, before_commit: str) -> str:
        # Only the NBA remains scope-owned. The immediate history update takes
        # distinct captured/private variables, without wide call-frame copies.
        p, w = self.name, self.width
        return (f"{p}_commit <= __behave_native_history_update_{w}("
                f"{mask}, {value}, {before_commit}, {p}_commit, {p}_batch, "
                f"{p}_touched, {p}_first, {p}_base, {p}_varied);")

    def rebase(self, value: str) -> str:
        p = self.name
        return f"""begin
  if({p}_batch != {p}_commit[1]) begin
    {p}_base={value};
    {self.publish}
  end
end"""


@dataclass(slots=True)
class DeferredNotifications:
    statements: dict[tuple[int, int], tuple[str, str]] = field(default_factory=dict)
    captures: dict[tuple[int, int], str] = field(default_factory=dict)
    consumers: dict[int, list[DeferredRead]] = field(default_factory=dict)
    lifted: dict[tuple[int, int], tuple[str, int]] = field(default_factory=dict)

    @property
    def helper_declarations(self) -> str:
        widths = {queue.width for reads in self.consumers.values() for queue in reads}
        return "".join(DeferredRead("", width).helper_declarations for width in sorted(widths))

    @property
    def declarations(self) -> str:
        """The typed queue API is identical in every source observer scope.

        A queue keeps its original width and update rules. Only its declaration
        owner changes: generate alternatives expose the same private interface,
        with independent state in each actual scope. Unused queues have no
        source effects; their preparation/storage cost still needs qualification.
        """
        reads = tuple(queue for start in sorted(self.consumers) for queue in self.consumers[start])
        histories = {queue.name: queue.history_declarations for queue in reads}
        return ("".join(histories[name] for name in sorted(histories))
                + "".join(queue.acknowledgement for queue in reads))


def _relative_symbol(symbol, scope):
    """Normalize compiler-owned instance coordinates, never resolve source names."""
    target, owner = symbol.split("."), scope.split(".")
    common = 0
    while common < min(len(target), len(owner)) and target[common] == owner[common]:
        common += 1
    return (len(owner)-common, tuple(target[common:]))


def _unchanged_selector(index, selector, expression):
    """A literal or unmodified variable can be read after this same statement.

    Keep these selectors in their original form. Besides avoiding a redundant
    temporary, this preserves compiler loop-index recognition. Calls need the
    existing typed frame-local effect certificate; their arguments are still
    checked for mutations in the caller.
    """
    if selector.get("kind") in {"IntegerLiteral", "UnbasedUnsizedIntegerLiteral"}:
        return True
    if selector.get("kind") in {"UnaryOp", "Conversion"} and _mutation(selector) is None:
        # E.g. -1 is a unary expression, not an IntegerLiteral. Pure unary
        # operations/casts over the same unchanged value need no index capture.
        return _unchanged_selector(index, selector["operand"], expression)
    if selector.get("kind") == "BinaryOp":
        # Keep constant/generate arithmetic in its original elaboration form.
        # Stable source variables remain stable until the NBA commits as well.
        return all(_unchanged_selector(index, selector[side], expression)
                   for side in ("left", "right"))
    if selector.get("kind") not in {"NamedValue", "HierarchicalValue"}:
        return False
    identity = selector["symbol"].split(" ", 1)[0]
    for node in _nodes(expression):
        if node.get("kind") == "Call" and index.span(node) not in index.frame_local_calls:
            return False
        target = _mutation(node)
        if target is not None and any(base["symbol"].split(" ", 1)[0] == identity
                                      for base in _written_bases(target)):
            return False
    return True


def _selectors(node):
    """Selectors of the assigned storage, not nested reads inside an index."""
    kind = node.get("kind")
    if kind == "ElementSelect":
        yield node["selector"]
    elif kind == "RangeSelect" and node["selectionKind"] != "Simple":
        yield node["left"]
    if kind in {"ElementSelect", "RangeSelect", "MemberAccess"}:
        yield from _selectors(node["value"])
    elif kind == "Concatenation":
        for operand in node["operands"]:
            yield from _selectors(operand)


def _accesses(index, expression):
    reads, writes = set(), set()
    for node in _nodes(expression):
        if node.get("kind") in {"NamedValue", "HierarchicalValue"}:
            reads.add(node["symbol"].split(" ", 1)[0])
        if node.get("kind") == "Call" and index.span(node) not in index.closed_value_calls:
            if index.span(node) not in index.frame_local_calls:
                raise ValueError("NBA selector capture needs resolved independent expression effects")
            reads.update(index.call_effects.nonlocal_reads(node.get("subroutine", "")))
        target = _mutation(node)
        if target is not None:
            writes.update(base["symbol"].split(" ", 1)[0] for base in _written_bases(target))
    return reads, writes


def _check_lifting(index, selector, others):
    reads, writes = _accesses(index, selector)
    for other in others:
        other_reads, other_writes = _accesses(index, other)
        if writes & other_reads or other_writes & reads:
            raise ValueError("interdependent NBA index/RHS effects need ordered primitive capture")


def lifted_selector_edits(source, edits, lifted, *, replacements=()):
    """Move a certified independent index once, retaining its original probes.

    Assignment expressions in NBA lvalue indices crash the pinned compiler.
    Separate statements are equivalent only after the effect-commutation check
    above. Their captured values replace the original indices, not the storage
    assignment. This is the same edit relocation used for continuous RHSs.
    """
    from .source_edits import relocate_expression

    original = source.encode("utf-8")
    replacements = list(replacements)
    for (first, last), (name, statement_start) in sorted(lifted.items()):
        value, retained, replacements = relocate_expression(
            original, (first, last), edits, replacements)
        retained.append(Insertion(statement_start, f" {name}=({value}); "))
        replacements.append(Replacement(first, last, name))
        edits = retained
    return edits, replacements


def _pack_projection(expressions, dimensions, prefix, *, width=None):
    """Pack a compiler-selected array in declared left-to-right order.

    The pinned native backend cannot stream nested unpacked arrays directly.
    Loop over their typed dimensions instead, copying packed leaves without
    changing four-state values. Bounds come from the actual physical instance;
    generated source grows with rank, not with the number of memory cells.
    All operands use one traversal, including masked writes and precommit data.
    """
    witness = expressions[0]
    packed = tuple(f"{prefix}_{i}" for i in range(len(expressions)))
    cursor = prefix + "_cursor"
    carrier = f"$bits({witness})" if width is None else str(width)
    declarations = [f"logic [{carrier}-1:0] {name};" for name in packed]
    if not dimensions:
        # A packed integral value already has the desired bit order. A normal
        # concatenation is unsigned and zero-extends into the history carrier.
        # A streaming assignment instead left-aligns a narrower stream; the
        # native backend also emits an invalid wide-shift helper for that case.
        return tuple(declarations), tuple(f"{name}={{{term}}};"
            for name, term in zip(packed, expressions, strict=True)), packed
    declarations.append(f"longint unsigned {cursor};")
    # Retain the old unsigned input conversion when a narrow projection feeds
    # a wider history carrier; reference arguments must match its exact type.
    body = [f"{name}='0;" for name in packed] if width is not None else []
    body.append(f"{cursor}=$bits({witness});")
    suffix = ""
    for dimension in range(1, dimensions + 1):
        ordinal, index = f"{prefix}_ordinal_{dimension}", f"{prefix}_index_{dimension}"
        left, right = f"$left({witness},{dimension})", f"$right({witness},{dimension})"
        body.append(f"for(longint unsigned {ordinal}=0;"
                    f"{ordinal}<$size({witness},{dimension});{ordinal}++) begin "
                    f"longint {index}; {index}={left} + "
                    f"(({left}>{right}) ? -longint'({ordinal}) : longint'({ordinal}));")
        suffix += f"[{index}]"
    width = f"$bits({witness}{suffix})"
    body.append(f"{cursor}-={width};")
    body.extend(f"{name}[{cursor} +: {width}]={{>>{{{term}{suffix}}}}};"
                for name, term in zip(packed, expressions, strict=True))
    body.extend("end" for _ in range(dimensions))
    return tuple(declarations), (" ".join(body),), packed


def _pack_once(expressions, dimensions, prefix, *, width, cache, declarations, statements):
    # Notification helpers mutate only private histories, not these source
    # storage/mask/value operands. Reuse within one source statement only,
    # after its original effects; keep every notification/acknowledgement.
    key = expressions, dimensions, width
    if key not in cache:
        local, packing, operands = _pack_projection(expressions, dimensions, prefix, width=width)
        declarations.extend(local)
        statements.extend(packing)
        cache[key] = operands
    return cache[key]


def deferred_notifications(index, sensitivity, guarded):
    result = DeferredNotifications()
    nba_symbols = {base["symbol"].split(" ", 1)[0]
                   for node in index.nodes if node.get("kind") == "Assignment"
                   and node.get("isNonBlocking") and index.selected(node)
                   for base in _written_bases(node["left"])}
    # Identical compiler projections in one lexical observer scope share their
    # write history. Physical instances still have separate storage. Consumers
    # acknowledge independently: one process must never consume another's event.
    rows, widths = [], {}
    for row in sensitivity:
        process = row["start"], row["end"]
        if process not in guarded:
            continue
        for read in row["reads"]:
            if any(str(write.get("symbol_id", read["symbol_id"])) in nba_symbols
                   and str(write.get("symbol_id", read["symbol_id"])) != str(read["symbol_id"])
                   for write in read["write_references"]):
                raise ValueError("NBA through a reference alias needs a bound commit footprint")
            if str(read["symbol_id"]) not in nba_symbols:
                continue
            for projection in read["projections"]:
                template, dimensions = projection["expression"], projection["unpacked_dimensions"]
                key = (row["lexical_scope"], _relative_symbol(read["symbol"], row["scope"]),
                       read["lexical_symbol"], template, dimensions)
                widths[key] = max(widths.get(key, 0), read["width"])
                rows.append((process, read, key, template, dimensions))
    queues = {}
    for ordinal, key in enumerate(sorted(widths)):
        queue = DeferredRead(f"__behave_native_nba_{ordinal}", widths[key])
        queues[key] = queue
    routes = {}
    consumers = set()
    for process, read, key, template, dimensions in rows:
        queue = queues[key]
        if (process, key) not in consumers:
            consumers.add((process, key))
            result.consumers.setdefault(process[0], []).append(
                DeferredRead(queue.name, queue.width, process[0]))
        for write in read["write_references"]:
            identity = str(write.get("symbol_id", read["symbol_id"]))
            source = (identity, write["file"], write["start"], write["end"])
            bound = template.replace(_BASE, write.get("storage_reference", _BASE))
            routes.setdefault(source, set()).add((write["scope_reference"] + queue.name,
                                                  queue.width, bound, dimensions))
    # Dependency enumeration is not HDL execution order. These private
    # subscribers have distinct acknowledgements; keep their emitted order
    # stable across compiler instances so identical observers retain identity.
    for reads in result.consumers.values():
        reads.sort(key=lambda queue: queue.name)

    def targets(base):
        first, last = index.span(base)
        return routes.get((base["symbol"].split(" ", 1)[0], index.filename, first, last), ())

    def retain(mapping, key, value):
        if key in mapping and mapping[key] != value:
            raise ValueError("instance-dependent deferred notification needs specialization")
        mapping[key] = value

    for statement in index.nodes:
        if statement.get("kind") != "ExpressionStatement" or not index.selected(statement):
            continue
        coordinate = index.span(statement)
        expression = statement["expr"]
        declarations, actions = [], []
        packed_projections = {}

        if expression.get("kind") == "Assignment" and expression.get("isNonBlocking"):
            left, right = expression["left"], expression["right"]
            bases = _written_bases(left)
            if not any(targets(base) for base in bases):
                continue
            if expression.get("timingControl"):
                raise ValueError("delayed NBA notifications need a timed commit profile")
            identities = [base["symbol"].split(" ", 1)[0] for base in bases]
            if len(set(identities)) != len(identities):
                raise ValueError("repeated NBA target in one lvalue needs primitive write scheduling")
            prefix = f"__behave_native_nba_write_{coordinate[0]}"
            captured = prefix + "_value"
            declarations = [f"var type({index.type_expression(left)} ) {captured};"]
            retain(result.captures, index.span(right), captured)
            # Lift only selectors whose effects commute with the RHS and other
            # selectors. Cloned mask/value lvalues reuse the captured values.
            selectors = {}
            selection_terms = tuple(_selectors(left))
            for selector in selection_terms:
                if _unchanged_selector(index, selector, expression):
                    continue
                _check_lifting(index, selector, (right, *(term for term in selection_terms
                                                          if term is not selector)))
                span = index.span(selector)
                if span not in selectors:
                    name = f"{prefix}_index_{len(selectors)}"
                    selectors[span] = name
                    witness = index.type_expression(selector)
                    # A size cast to the expression's own width retains its
                    # signedness. It also gives unsized arithmetic a sized
                    # carrier: the native compiler rejects type(1 + i) as a
                    # variable declaration. Only the index value is captured;
                    # no source storage or enum declaration is rewritten.
                    declarations.append(f"var type($bits({witness})'({witness})) {name};")
                    retain(result.lifted, span, (name, coordinate[0]))
            first, last = index.span(left)
            replacements = [Replacement(a-first, b-first, name)
                            for (a, b), name in selectors.items()]
            masks, values, calls = [], [], []
            mask_replacements, value_replacements = list(replacements), list(replacements)
            for number, base in enumerate(bases):
                mask, value = f"{prefix}_mask_{number}", f"{prefix}_bits_{number}"
                text = index.text(base)
                declarations.extend((f"var type({text} ) {mask};", f"var type({text} ) {value};"))
                masks.append(f"{mask}={index.fill(base, '0')};")
                values.append(f"{value}={index.fill(base, '0')};")
                a, b = index.span(base)
                mask_replacements.append(Replacement(a-first, b-first, mask))
                value_replacements.append(Replacement(a-first, b-first, value))
                for projection_number, (queue, width, template, dimensions) in enumerate(sorted(targets(base))):
                    projected = tuple(template.replace(_BASE, term + " ")
                                      for term in (mask, value, text))
                    operands = _pack_once(projected, dimensions,
                        f"{prefix}_pack_{number}_{projection_number}", width=width, cache=packed_projections,
                        declarations=declarations, statements=calls)
                    calls.append(DeferredRead(queue, width).enqueue(*operands))
            lvalue = index.source[first:last].decode("utf-8")
            mask_lhs = SourceEdits(lvalue, (), tuple(sorted(mask_replacements, key=lambda r: r.start))).text
            value_lhs = SourceEdits(lvalue, (), tuple(sorted(value_replacements, key=lambda r: r.start))).text
            actions.extend((*masks, *values, f"{mask_lhs}={index.fill(left, '1')};",
                            f"{value_lhs}={captured};", *calls))
        # The precommit value is the end of the active region, not the value at
        # the first enqueue. Existing blocking-write checks certify these reads.
        rebases = set()
        for part in _nodes(expression):
            target = _mutation(part)
            if target is not None:
                for base in _written_bases(target):
                    for queue, width, template, dimensions in targets(base):
                        rebases.add((queue, width, template.replace(_BASE, index.text(base) + " "), dimensions))
        for number, (queue, width, term, dimensions) in enumerate(sorted(rebases)):
            operands = _pack_once((term,), dimensions,
                f"__behave_native_nba_rebase_{coordinate[0]}_{number}", width=width, cache=packed_projections,
                declarations=declarations, statements=actions)
            actions.append(DeferredRead(queue, width).rebase(operands[0]))
        if actions:
            retain(result.statements, coordinate,
                   ("begin\n" + "\n".join(declarations) + "\n",
                    "\n" + "\n".join(actions) + "\nend "))
    return result
