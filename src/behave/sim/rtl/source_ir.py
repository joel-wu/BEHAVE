"""Lower a typed source activation to the shared command/value IR.

This layer is deliberately NOT a whole RTL step: scheduling determines which
process activates and when deferred writes commit. Blocking writes, deferred
write values, source occurrences and helper control are computed together here.
Neither synthesis nor a final wire snapshot supplies the event conditions.
"""

from collections import ChainMap
from dataclasses import dataclass, replace
from functools import cached_property

from ..symbolic import ir, typecheck as types
from ..symbolic import ranges
from ..symbolic.operation_goals import predicate_expression
from ..symbolic.slicing import reads
from ..core.catalogue import OccurrenceKey
from ..core.operation_goal import OperationValue
from ..core.refusal import Refusal, Refused
from .source_arguments import CallPhases, prepare_arguments
from .source_case import (
    constant_labels_exhaustive, constant_pattern, qualified_constant_check, qualified_pattern_check,
)
from .source_constants import coalesce_inactive_occurrences, fold_literal_copies
from .source_declarations import SourceContext, _children, _nodes
from .source_deferred import DeferredWrite
from .source_deferred_array import ArrayDeferredWrite
from .source_defined import DefinedStorage, default_defined
from .source_domains import DomainProofBudget, _with_domain_marks
from .source_effects import accesses, conflicts
from .source_failure_events import argument_order_issue
from .source_goals import goal_of, occurrence_of
from .source_integral import (
    BINARY_PRIMITIVES,
    UNARY_PRIMITIVES,
    IntegralType,
    binary_value,
    four_state_literal,
    integer_literal,
    native_assignment_literal,
    integral_type,
    typed_symbols,
)
from .source_loop_constants import _integral_interval, loop_key
from .source_loops import loop_transfers
from .source_operation_events import OperationCapture
from .source_packed import literal_window, merge_bits, selection_offset
from .source_values import ArrayType, source_type
from .source_value_effects import SourceValueEffects
from .source_while import for_progress, while_progress

VERSION = "rtl-source-activation-ir/86"


def _sequence(commands):
    result = ir.Skip()
    for command in reversed(commands):
        result = command if isinstance(result, ir.Skip) else ir.Seq(command, result)
    return result


def _constant_case_dispatch(arms, fallback):
    """Balanced first-match choice after effect-free constant label tests.

    Duplicate labels retain source priority. Only the selected arm executes;
    helpers and other observable label computations never use this transform.
    """
    def select(rows, known=False):
        if not rows:
            return fallback
        if len(rows) == 1:
            condition, body = rows[0]
            return body if known else ir.If(condition, body, fallback)
        middle = len(rows) // 2
        left, right = rows[:middle], rows[middle:]
        condition = ir._balanced_op("lor", (test for test, _ in left), ir.Lit(False))
        return ir.If(condition, select(left, True), select(right, known))

    return select(arms)


def _literal_case_dispatch(selector, labels, arms, fallback, bounds):
    """Binary lookup for integer labels of one captured, defined selector.

    Reordering pure label tests cannot reorder body effects. First source
    matches still own duplicate values. Source bodies with no selectable
    value remain present under false control for occurrence accounting.
    """
    if any(effects or not isinstance(value, ir.Lit) or type(value.value) is not int
           for row in labels for effects, value in row):
        return None
    choices = {}
    for row, (_, _, body) in zip(labels, arms, strict=True):
        for _, value in row:
            choices.setdefault(value.value, body)
    rows = sorted(choices.items())
    fallback_used = False

    def select(start, stop, low, high):
        nonlocal fallback_used
        if start == stop:
            fallback_used = True
            return fallback
        if stop == start + 1:
            value, body = rows[start]
            if low == high == value:
                return body
            fallback_used = True
            return ir.If(ir.Op('eq', selector, ir.Lit(value)), body, fallback)
        middle = (start + stop) // 2
        pivot = rows[middle][0]
        return ir.If(ir.Op('lt', selector, ir.Lit(pivot)),
            select(start, middle, low, min(high, pivot - 1)),
            select(middle, stop, max(low, pivot), high))

    dispatch = select(0, len(rows), *bounds)
    selected = {id(body) for body in choices.values()}
    unselected = [body for _, _, body in arms if id(body) not in selected]
    if not fallback_used:
        unselected.append(fallback)
    return _sequence([*(ir.If(ir.Lit(False), body, ir.Skip()) for body in unselected), dispatch])


@dataclass(frozen=True, slots=True)
class SourceProcedure:
    # Implicit port transfers and construction have no step-goal occurrence.
    context: SourceContext | None
    procedure: ir.Procedure
    # IR state name -> selected compiler storage symbol (not a hit identity).
    storage: tuple[tuple[str, str], ...]
    # Pending values and exact per-bit change histories, not immediate writes.
    deferred: tuple[DeferredWrite, ...]
    # These are internal occurrence marks, not a new public coverage/safety
    # classification. The original manifest owns the class of each public goal.
    occurrences: tuple[tuple[str, OccurrenceKey, str], ...]
    # Source commands without the per-activation deferred-output initialization.
    # A scheduler initializes pending writes and composes these commands before
    # committing them; it must not compose independent run_step successors.
    activation: object
    # Conservative source write footprints: storage, blocking bits, NBA bits.
    # These describe selected HDL destinations, not the containing IR copy.
    write_masks: tuple[tuple[str, int, int], ...]
    # Private terminal prefixes awaiting proof in an actual local input domain.
    # They are not source goals, native failures or score authority.
    domain_checks: tuple[tuple[str, Refusal], ...] = ()
    # Only implicit transfers use this; source processes retain the compiler's
    # sensitivity ranges. Destination read/modify/write is not a dependency.
    implicit_reads: tuple[str, ...] = ()
    # An elaborated initial block is pending once at startup, never subscribed
    # to later value changes. Its compiler identity also binds initialization.
    initial_process: str | None = None


@dataclass(frozen=True, slots=True)
class BoundSelection:
    index: object
    owner: IntegralType | ArrayType
    selected: IntegralType | ArrayType


@dataclass(frozen=True, slots=True)
class BoundLValue:
    """Storage and evaluated coordinates, shared by a read/modify/write."""

    name: str
    kind: IntegralType | ArrayType
    selections: tuple[BoundSelection, ...]
    source: dict

    @property
    def value_type(self):
        return self.selections[-1].selected if self.selections else self.kind


class SourceCompiler:
    """One typed document and declaration index for all its activations."""

    def __init__(self, source, document, plan, *, filename, native_assignments=False):
        self.native_assignments = native_assignments
        self.document = document
        self.loops = {(loop.start, loop.end): loop for loop in plan.loops}
        if plan.loop_proofs is not None:
            for site in plan.declarations.sites_for("loop-activation"):
                span = site.start, site.end
                syntax, _ = plan.loop_proofs.nodes[span]
                if (syntax.get('repeatOrWhile', {}).get('text') in {'while', 'repeat'}
                        or syntax.get('kind') == 'DoWhileStatement'):
                    continue  # Typed count/ranking proof belongs to lowering.
                if span not in self.loops:
                    try:
                        self.loops[span] = plan.loop_proofs.prove(span)
                    except ValueError:
                        # A dynamic initial value has no constant coordinate
                        # enumeration. Prove its typed ranking at the actual
                        # entry below, after lowering its initialization.
                        pass
        self.source = source.encode()
        self.lines = [0]
        for line in self.source.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.symbols = typed_symbols(document)
        self.net_views = {}
        self.filename = filename
        self.operation_rules = {(site.start, site.end): site.operation_rules
                                for site in plan.declarations.sites_for("operation-event")}
        self.read_rules = {(site.start, site.end): site.read_rules
                           for site in plan.declarations.sites_for("value-observation")}
        self.failures = {(site.start, site.end): site for site in plan.declarations.failures}
        # Compiler-resolved identities include instance/type specialization.
        # Only universal domain answers are shared, never source occurrences,
        # call results, caller-state ranges or cross-invocation evidence.
        self.helper_domains = {}
        self.helper_returns = {}
        self.marks = {context.key: {} for context in plan.declarations.contexts}
        self.occurrences = {context.key: [] for context in plan.declarations.contexts}
        for item in plan.declarations.occurrences:
            site = plan.declarations.sites[item.site]
            for outcome in site.outcomes:
                occurrence = occurrence_of(site, item, outcome, filename=filename)
                mark = occurrence.canonical
                self.marks[item.context][(site.schema, site.start, site.end, item.calls, outcome)] = mark
                self.occurrences[item.context].append((mark, occurrence, goal_of(site, outcome, filename=filename).id))

    def lower(self, context, *, namespace="", storage_name=None, defer_domains=False):
        # Local queries retain helper obligations and discharge them over the
        # recorded domain. An optional all-argument lemma is not a prerequisite.
        return _Lowerer(self, context, namespace=namespace, storage_name=storage_name,
                        modular_domains=not defer_domains).build(
            self.document, defer_domains=defer_domains)

    def lower_transfer(self, assignment, *, namespace, storage_name, defer_domains=False):
        lowerer = _Lowerer(self, None, namespace=namespace, storage_name=storage_name,
                          modular_domains=not defer_domains)
        lowerer.frames.append({})
        commands, value = lowerer.snapshot(*lowerer.expression(assignment["right"]))
        before, bound = lowerer.bind_lvalue(assignment["left"])
        reads = lowerer.effects(commands, value)[0] | lowerer.effects(before, None)[0]
        body = _sequence([*commands, *before, *lowerer.store_lvalue(bound, value, deferred=False)])
        return lowerer.finish(body, defer_domains=defer_domains, implicit_reads=tuple(sorted(reads)))

    def lower_initial(self, node, *, namespace, storage_name, defer_domains=False):
        lowerer = _Lowerer(self, None, namespace=namespace, storage_name=storage_name,
                          modular_domains=not defer_domains)
        lowerer.frames.append({})
        body = lowerer.statement(node['body'])
        return replace(lowerer.finish(body, defer_domains=defer_domains),
                       initial_process=str(node['addr']))

    @cached_property
    def failure_effects(self):
        # Construction has no step-goal declaration, but diagnostic operands
        # still need the same ordering certificate as observed runtime calls.
        return SourceValueEffects(self.symbols,
            {key: value for key, value in self.symbols.items() if value.get('kind') == 'Subroutine'},
            _children, _nodes)

    def constant_initializer(self, node):
        """Prove a closed initializer using the existing typed lowering/ranges.

        Slang does not eagerly fold arbitrary datapath or helper expressions.
        No sampled inputs or default source storage may turn such an expression
        into a constant. Only state-free construction with a singleton proved
        result is admitted here; stateful construction needs its own relation.
        """
        lowerer = _Lowerer(self, None, namespace='initial_constant_', storage_name=None)
        lowerer.frames.append({})
        commands, value = lowerer.expression(node)
        if lowerer.storage or lowerer.deferred:
            return None
        name = lowerer.temp(kind=source_type(node['type'], self.symbols).ir_type)
        procedure = lowerer.finish(_sequence([*commands, ir.Assign(name, value)])).procedure
        after = ranges.procedure_post(procedure, {})
        bound = None if after is None else after.get(name)
        return bound.lo if bound is not None and bound.lo == bound.hi else None


class _Lowerer:
    def __init__(self, compiler, context, *, namespace, storage_name,
                 domain_budget=None, modular_domains=True):
        self.compiler = compiler
        self.domain_budget = domain_budget or DomainProofBudget()
        self.modular_domains = modular_domains
        self.loops = compiler.loops
        self.source = compiler.source
        self.lines = compiler.lines
        self.symbols = compiler.symbols
        self.context = context
        self.filename = compiler.filename
        self.operation_rules = compiler.operation_rules if context is not None else {}
        self.read_rules = compiler.read_rules if context is not None else {}
        self.types = {}
        self.ranges = {}
        self.snapshot_ranges = {}
        self.snapshots = {}
        self.loop_ranges = {}
        self.storage = {}
        self.source_values = set()
        self.frames = []
        self.references = {}
        self.calls = ()
        self.active_calls = ()
        self.returned = None
        self.return_value = None
        self.loop_control = None
        self.lvalue_value = None
        self.ranking_capture = None
        self.native_assignment_node = None
        self.deferred = {}
        self.write_masks = {}
        self.access_bounds = {}
        self.divisor_bounds = {}
        self.control_domains = {}
        self.construction_failures = {}
        self.marks = compiler.marks[context.key] if context is not None else {}
        self.occurrences = compiler.occurrences[context.key] if context is not None else ()
        self.next_temp = 0
        self.namespace = namespace
        self.storage_name = storage_name
        self.defined = DefinedStorage(self.temp)

    def span(self, node):
        if node.get("source_file_start") != self.filename or node.get("source_file_end") != self.filename:
            raise Refused(Refusal("rtl-source-ir-coordinate-missing", "a runtime node has no selected source span"))
        return (self.lines[node["source_line_start"] - 1] + node["source_column_start"] - 1,
                self.lines[node["source_line_end"] - 1] + node["source_column_end"] - 1)

    def mark(self, schema, node, outcome):
        if self.context is None:
            return ir.Skip()
        return self.mark_at(schema, self.span(node), outcome)

    def mark_at(self, schema, coordinates, outcome):
        key = (schema, *coordinates, self.calls, outcome)
        mark = self.marks.get(key)
        return ir.Skip() if mark is None else ir.Mark(mark)

    def terminal_failure(self, node, code):
        event = self.mark('artifact-failure', node, 'failure')
        if isinstance(event, ir.Mark):
            return ir.Fail(code, event.goal)
        if self.context is not None:
            return self.unsupported(node, 'terminal failure has no source declaration')
        # Initial construction is outside the public step catalogue. Preserve
        # its actual terminal prefix as a private domain fault, never silently
        # continue or invent a public event that native observation cannot emit.
        mark = f'@source-construction:{self.namespace}{len(self.construction_failures)}'
        self.construction_failures[mark] = Refusal('rtl-source-construction-failure',
            'construction can terminate before the step-goal observation domain',
            node_kind=node.get('kind'), span=self.span(node), failure=code)
        return ir.Fail(code, mark)

    def temp(self, *, boolean=False, kind=None):
        name = f"{self.namespace}t{self.next_temp}"
        self.next_temp += 1
        self.types[name] = kind or (types.BOOLEAN if boolean else types.INTEGER)
        return name

    def bind(self, identity, *, local=False):
        frame = self.frames[-1] if local else self.storage
        if identity not in frame:
            name = (self.temp() if local or self.storage_name is None
                    else self.storage_name(identity))
            kind = source_type(self.symbols[identity]["type"], self.symbols)
            self.types[name] = kind.ir_type
            frame[identity] = name
            self.source_values.add(name)
            self.ranges[name] = kind.domain
        return frame[identity]

    def variable(self, reference):
        identity = reference.split(" ", 1)[0]
        if identity in self.references:
            bound = self.references[identity]
            if bound.selections:
                return self.unsupported(bound.source, 'selected references need a bound lvalue access')
            return bound.name
        if identity in self.compiler.net_views:
            view = self.compiler.net_views[identity]
            kind = source_type(self.symbols[view.owner]["type"], self.symbols)
            if view.offset or view.kind != kind:
                return self.unsupported(self.symbols[identity], 'selected net references need a bound lvalue access')
            return self.bind(view.owner)
        for frame in reversed(self.frames):
            if identity in frame:
                return frame[identity]
        return self.bind(identity)

    def reference_binding(self, node):
        identity = node['symbol'].split(' ', 1)[0]
        if identity in self.references:
            return self.references[identity]
        view = self.compiler.net_views.get(identity)
        if view is None:
            return None
        name = self.bind(view.owner)
        kind = source_type(self.symbols[view.owner]['type'], self.symbols)
        selections = (() if view.offset == 0 and kind == view.kind else
                      (BoundSelection(ir.Lit(view.offset), kind, view.kind),))
        return BoundLValue(name, kind, selections, node)

    def snapshot(self, commands, value):
        """Preserve an evaluated operand before later operands may write storage."""
        errors = []
        kind = types._of_expression(value, self.types, errors, "source operand")
        if errors or kind is None:
            raise ValueError(f"source operand has no consistent IR type: {errors}")
        name = self.temp(kind=kind)
        bound = (None if isinstance(kind, types.Array) else ranges.bounds_of_expression(value,
            ChainMap(self.loop_ranges, self.snapshot_ranges, self.ranges)))
        if bound is not None and bound.lo is not None and bound.hi is not None:
            self.snapshot_ranges[name] = ranges.Range(bound.lo, bound.hi)
        capture = ir.Assign(name, value)
        self.snapshots[name] = capture
        return [*commands, capture], ir.Var(name)

    def array_index(self, node, kind):
        commands, index = self.snapshot(*self.expression(node))
        position = kind.position(index)
        bound = ranges.bounds_of_expression(position,
            ChainMap(self.loop_ranges, self.snapshot_ranges, self.ranges))
        if (bound is None or bound.lo is None or bound.hi is None
                or bound.lo < 0 or bound.hi >= kind.cells):
            # The declared type may be wider than the value assigned before
            # this access. Check the captured index at its actual IR position,
            # sharing the flow proof used for packed updates below.
            # walk_post's witness sees the pre-assignment environment. Prove
            # the captured RHS there, not its as-yet-unassigned temporary.
            captured = commands[-1]
            self.access_bounds[id(captured)] = (kind.position(captured.expr), 1, kind.cells,
                Refusal("rtl-source-ir-array-domain-unproved",
                    "array selection needs a proved in-range index; unknown bits are not zero-filled",
                    node_kind=node.get("kind"), span=self.span(node)))
        return commands, position

    def packed_selection(self, node):
        """Evaluate a typed selector once; reads and writes share coordinates."""
        kind = integral_type(node["value"]["type"], self.symbols)
        if node["kind"] == "ElementSelect":
            commands, index = self.snapshot(*self.expression(node["selector"]))
            return commands, selection_offset(kind, index), kind
        selection = node["selectionKind"]
        right = node["right"].get("constant")
        if right is None:
            return self.unsupported(node, "a packed selection needs an elaborated width or bound")
        right = integer_literal(right)
        if selection in {"IndexedUp", "IndexedDown"}:
            commands, index = self.snapshot(*self.expression(node["left"]))
            return commands, selection_offset(kind, index, right,
                descending_select=selection == "IndexedDown"), kind
        if selection != "Simple" or node["left"].get("constant") is None:
            return self.unsupported(node)
        return [], selection_offset(kind, ir.Lit(right)), kind

    def align_packed_write(self, value, mask, offset, width, destination_width):
        """Shift a proved in-range selection into its containing packed value."""
        commands, offset = self.snapshot([], offset)
        data, selected = self.temp(), self.temp()
        # The ordinary encoder may use joined rather than path-refined ranges.
        # Saturation gives it a total nonnegative shift; build() proves that it
        # is the identity at every reachable occurrence, before admitting IR.
        amount = ir.Op("usat", offset, ir.Lit(max(1, (destination_width - 1).bit_length())))
        commands.extend(ir.Assign(name, ir.Op("ushl", expr, amount, ir.Lit(destination_width)))
                        for name, expr in ((data, value), (selected, mask)))
        self.access_bounds[id(commands[-2])] = (offset, width, destination_width,
            Refusal("rtl-source-ir-packed-write-domain-unproved",
                "packed writes need an in-range native mapping; out-of-range stores differ between simulators",
                selected_width=width, destination_width=destination_width))
        return commands, ir.Var(data), ir.Var(selected)

    def read_packed(self, value, offset, owner, target):
        """Read a captured selection using the same domain proof as stores.

        Declared integer widths alone need not prove a selector assigned or
        guarded earlier in this activation. Register the original offset at
        its actual program point; saturation is permitted only after that
        proof establishes it is the identity at every executed occurrence.
        """
        bound = ranges.bounds_of_expression(offset,
            ChainMap(self.loop_ranges, self.snapshot_ranges, self.ranges))
        if (bound is not None and bound.lo is not None and bound.hi is not None
                and bound.lo >= 0 and bound.hi + target.width <= owner.width):
            window = literal_window(value, offset, owner.width, target.width)
            return [], target.wrap(window if window is not None else ir.Op("shr", value, offset))
        commands, captured_offset = self.snapshot([], offset)
        captured = commands[-1]
        self.access_bounds[id(captured)] = (captured.expr, target.width, owner.width,
            Refusal("rtl-source-ir-packed-read-domain-unproved",
                "packed reads need a proved in-range bound selection",
                selected_width=target.width, source_width=owner.width))
        amount = ir.Op("usat", captured_offset, ir.Lit(max(1, (owner.width - 1).bit_length())))
        window = literal_window(value, amount, owner.width, target.width)
        return commands, target.wrap(window if window is not None else
                                     ir.Op("ulshr", value, amount, ir.Lit(owner.width)))

    def effects(self, commands, value):
        # Pending NBA slots also carry source side effects: two operand calls
        # writing the same pending target cannot silently acquire an order.
        pending = {name for write in self.deferred.values() for name in write.registers}
        return accesses(_sequence(commands), self.source_values | pending, value)

    def loop_bound_ranges(self, commands):
        """Enclosing counter proofs that remain invariant in this inner loop.

        Use resolved storage identities, never names or sampled values. Include
        initializer, condition, body and step effects (including helper writes)
        before borrowing a range. Mutable limits keep their universal type bound.
        """
        writes = self.effects(commands, ir.Lit(0))[1]
        bindings = ChainMap(*reversed(self.frames), self.storage)
        return {identity: self.loop_ranges[name] for identity, name in bindings.items()
                if name in self.loop_ranges and name not in writes}

    def loop_condition(self, node, progress):
        """Retain the already-lowered limit; never evaluate source effects twice."""
        previous = self.ranking_capture
        capture = [None if progress is None else progress.limit, None]
        self.ranking_capture = capture
        try:
            commands, test = self.expression(node)
        finally:
            self.ranking_capture = previous
        return commands, test, capture[1]

    @staticmethod
    def invariant_loop_limit(value, commands, snapshots=None):
        """Expand pure operand captures only when their source reads are invariant.

        The original commands, checks and events still execute at every test.
        Only the ranking expression is expanded, including for a do-loop whose
        first body precedes the condition. A capture outside this loop retains
        its stored value. Arbitrary assignments are never treated as snapshots.
        """
        if value is None:
            return None
        snapshots = snapshots or {}
        writes = {}
        for command in ir.walk(_sequence(commands)):
            written = (command.var if isinstance(command, (ir.Assign, ir.Store))
                       else command.index if isinstance(command, ir.For) else None)
            if written is not None:
                writes[written] = None if written in writes else command
        # snapshot() can only reference earlier values: its definition graph
        # is acyclic. Memoization retains sharing in compound arithmetic bounds.
        pending, expanded = [(value, False)], {}
        while pending:
            expr, ready = pending.pop()
            if id(expr) in expanded:
                continue
            if isinstance(expr, ir.Var):
                if expr.name not in writes:
                    expanded[id(expr)] = expr
                    continue
                capture = snapshots.get(expr.name)
                if capture is None or writes[expr.name] is not capture:
                    return None
                children = (capture.expr,)
            elif isinstance(expr, ir.Op):
                children = expr.args
            elif isinstance(expr, ir.Sel):
                children = (expr.array, expr.index)
            else:
                expanded[id(expr)] = expr
                continue
            if not ready:
                pending.append((expr, True))
                pending.extend((child, False) for child in children)
                continue
            args = tuple(expanded[id(child)] for child in children)
            if isinstance(expr, ir.Var):
                result = args[0]
            elif all(a is b for a, b in zip(args, children)):
                result = expr
            elif isinstance(expr, ir.Op):
                result = ir.Op(expr.op, *args)
            else:
                result = ir.Sel(*args)
            expanded[id(expr)] = result
        return expanded[id(value)]

    def require_independent(self, node, first, second):
        if conflicts(first, second):
            return self.unsupported(node, "operand effects need a qualified evaluation order")

    def inactive_pattern_events(self, node, elements):
        """Keep declarations of setters omitted by the compiler's expansion.

        An overridden default does not execute, including its helper calls.
        Source ranges join the original setters to the compiler's typed copies;
        the current call prefix distinguishes other uses of the same helper.
        """
        if node["kind"] != "StructuredAssignmentPattern":
            return []
        used = {self.span(part) for part in _nodes(elements)
                if part.get("source_file_start") == self.filename
                and part.get("source_file_end") == self.filename}
        setters = [node["defaultSetter"]] if "defaultSetter" in node else []
        setters.extend(row["expr"] for key in ("indexSetters", "typeSetters", "memberSetters")
                       for row in node.get(key, ()))
        marks = set()
        for setter in setters:
            low, high = self.span(setter)
            if (low, high) in used:
                continue
            for (_schema, start, end, calls, _outcome), mark in self.marks.items():
                if calls[:len(self.calls)] != self.calls:
                    continue
                if len(calls) > len(self.calls):
                    start, end = calls[len(self.calls)]
                if low <= start and end <= high:
                    marks.add(mark)
        return ([ir.If(ir.Lit(False), _sequence([ir.Mark(mark) for mark in sorted(marks)]), ir.Skip())]
                if marks else [])

    def concatenate(self, node, operands):
        """Assemble compiler-ordered packed elements without sign leakage."""
        commands, value = [], ir.Lit(0)
        previous = (set(), set())
        for operand in operands:
            before, part = self.snapshot(*self.expression(operand))
            width = (0 if operand["kind"] == "Replication" and self.replication_count(operand) == 0
                     else integral_type(operand["type"], self.symbols).width)
            if width == 0:
                commands.extend(before)
                continue  # No bits or effects; retain the false-guarded marks.
            effects = self.effects(before, part)
            self.require_independent(node, previous, effects)
            previous = previous[0] | effects[0], previous[1] | effects[1]
            commands.extend(before)
            value = ir.Op("or", ir.Op("shl", value, ir.Lit(width)),
                          ir.Op("uwrap", part, ir.Lit(width)))
        return commands, integral_type(node["type"], self.symbols).wrap(value)

    def replication_count(self, node):
        count = node["count"].get("constant")
        count = None if count is None else integer_literal(count)
        if count is None or count < 0:
            return self.unsupported(node, "packed replication needs a nonnegative elaborated count")
        return count

    def expression(self, node, *, mark_term=True):
        commands, value = self._expression(node)
        commands, value = self.observe_read_value(node, commands, value, mark_term=mark_term)
        if self.ranking_capture is not None and node is self.ranking_capture[0]:
            self.ranking_capture[1] = value
        return commands, value

    def assignment_expression(self, node, target):
        """Materialize direct X literals only at variable storage writes.

        Ordinary source queries keep four-state refusal guards. The native
        cover opts into the simulator's physical storage profile; it does not
        reinterpret X-bearing operators or narrow any initial-state domain.
        """
        owner = target
        while owner.get('kind') in {'ElementSelect', 'RangeSelect', 'MemberAccess'}:
            owner = owner['value']
        symbol = (self.symbols.get(owner.get('symbol', '').split(' ', 1)[0], {})
                  if owner.get('kind') in {'NamedValue', 'HierarchicalValue'} else {})
        if not self.compiler.native_assignments or symbol.get('kind') not in {'Variable', 'FormalArgument'}:
            return self.expression(node)
        leaf = node
        while leaf.get('kind') == 'Conversion':
            leaf = leaf['operand']
        # Conversion itself remains compiler-typed and observable. Only the
        # literal leaf is eligible, never another expression evaluated nearby.
        previous = self.native_assignment_node
        self.native_assignment_node = leaf if leaf.get('kind') in {
            'IntegerLiteral', 'UnbasedUnsizedIntegerLiteral', 'NamedValue', 'HierarchicalValue'} else None
        try:
            return self.expression(node)
        finally:
            self.native_assignment_node = previous

    def observe_read_value(self, node, commands, value, *, mark_term=True):
        if self.read_rules and node.get("kind") in {
                "NamedValue", "HierarchicalValue", "ElementSelect", "RangeSelect", "MemberAccess"}:
            rules = self.read_rules.get(self.span(node), ())
            if rules:
                kind = integral_type(node["type"], self.symbols)
                sampled = OperationValue("value", width=kind.width, signed=kind.signed)
                commands, value = self.snapshot(commands, value)
                for rule in rules:
                    condition = predicate_expression(rule.bind(sampled), (value,))
                    commands.append(ir.If(condition, self.mark("value-observation", node, rule.name), ir.Skip()))
        return self.observe_expression(node, commands, value, mark_term=mark_term)

    def observe_expression(self, node, commands, value, *, mark_term=True):
        # A Boolean term is recorded only when that term actually executes.
        false = self.mark("boolean-expression", node, "false")
        true = self.mark("boolean-expression", node, "true")
        if mark_term and (not isinstance(false, ir.Skip) or not isinstance(true, ir.Skip)):
            commands, value = self.snapshot(commands, value)
            commands.append(ir.If(ir.Op("truth", value), true, false))
        return commands, value

    def operation_result(self, node, commands, operands, result):
        rules = self.operation_rules.get(self.span(node), ())
        if not rules:
            return commands, result
        capture = OperationCapture.from_node(node, self.symbols)
        values = capture.goal_values()
        commands, result = self.snapshot(commands, result)
        payload = (*operands, ir.Op("truth", result) if values[-1].kind == "boolean" else result)
        kinds = tuple(value.kind for value in values)
        for rule in rules:
            predicate = (predicate_expression(rule.goal.bind(values), payload)
                         if rule.kinds == kinds else ir.Lit(False))
            commands.append(ir.If(predicate, self.mark("operation-event", node, rule.goal.name), ir.Skip()))
        return commands, result

    def convert(self, node, value):
        """One compiler-owned conversion for expressions and helper copy-back."""
        target = source_type(node['type'], self.symbols)
        source = source_type(node['operand']['type'], self.symbols)
        if isinstance(target, ArrayType):
            if target.signature_type != source.signature_type:
                return self.unsupported(node, 'array element conversions need a typed recursive conversion')
            return target.wrap(value)
        if isinstance(source, ArrayType):
            return self.unsupported(node, 'array-to-scalar conversion needs a typed packing operation')
        conversion = node.get('conversionKind')
        if (conversion not in {None, 'Implicit', 'Explicit', 'Propagated'}
                or (conversion is None and source.signed != target.signed and source.width < target.width)):
            return self.unsupported(node, 'scalar conversion needs its compiler conversion kind')
        return target.convert(value, source, propagated=conversion == 'Propagated')

    def literal(self, node, value, kind=None):
        kind = source_type(node['type'], self.symbols) if kind is None else kind
        if isinstance(kind, ArrayType):
            if not isinstance(value, list) or len(value) != kind.cells:
                raise Refused(Refusal('rtl-source-ir-constant-facts-missing',
                    'an array constant needs every compiler-resolved element', span=self.span(node)))
            commands, elements = [], []
            for item in value:
                before, element = self.literal(node, item, kind.element)
                commands.extend(before)
                elements.append(element)
            return commands, ir.Op('arraylit', *elements)
        if not four_state_literal(value):
            return [], kind.wrap(ir.Lit(integer_literal(value)))
        if node is self.native_assignment_node:
            physical = native_assignment_literal(value)
            if physical is not None:
                return [], kind.wrap(ir.Lit(physical))
        valid = ir.Lit(False)
        check = ir.Assign(self.temp(boolean=True), valid)
        self.control_domains[id(check)] = (valid, Refusal(
            'rtl-source-ir-four-state-domain-unproved',
            'evaluating this X/Z constant requires a four-state source relation',
            value=value, span=self.span(node)))
        # This placeholder is not a denotation of X/Z. Standalone lowering
        # must prove the evaluation unreachable. Deferred queries insert a
        # terminal private fault before it and prove that fault absent across
        # their entire domain, without conjoining the requested public goal.
        return [check], ir.Lit(0)

    def _expression(self, node):
        kind = node.get("kind")
        scalar = lambda: integral_type(node["type"], self.symbols)
        if kind in {"IntegerLiteral", "UnbasedUnsizedIntegerLiteral"}:
            return self.literal(node, node['value'])
        if kind in {"NamedValue", "HierarchicalValue"}:
            symbol = self.symbols[node["symbol"].split(" ", 1)[0]]
            if symbol["kind"] in {"Parameter", "EnumValue", "Genvar"}:
                return self.literal(node, symbol.get('constant_value', symbol['value']))
            reference = self.reference_binding(node)
            if reference is not None:
                return self.read_lvalue(reference)
            name = self.variable(node['symbol'])
            return self.read_lvalue(BoundLValue(name, source_type(node['type'], self.symbols), (), node))
        if kind == "LValueReference":
            if self.lvalue_value is None:
                return self.unsupported(node, "an lvalue reference needs its enclosing bound assignment")
            return [], scalar().wrap(self.lvalue_value)
        if kind == "Conversion":
            commands, value = self.expression(node["operand"],
                mark_term=self.span(node["operand"]) != self.span(node))
            return commands, self.convert(node, value)
        if kind == "UnaryOp":
            op = node["op"]
            if op in {"Preincrement", "Postincrement", "Predecrement", "Postdecrement"}:
                commands, bound = self.bind_lvalue(node["operand"])
                before, value = self.read_lvalue(bound)
                commands, old = self.snapshot(*self.observe_expression(
                    node["operand"], [*commands, *before], value))
                new = scalar().wrap(ir.Op("add", old, ir.Lit(-1 if "decrement" in op else 1)))
                commands.extend(self.store_lvalue(bound, new, deferred=False))
                return commands, old if op.startswith("Post") else new
            commands, value = self.expression(node["operand"])
            if self.span(node) in self.operation_rules:
                commands, value = self.snapshot(commands, value)
            if op == "Plus":
                return self.operation_result(node, commands, (value,), value)
            if op == "LogicalNot":
                return self.operation_result(node, commands, (value,),
                                             ir.Op("toint", ir.Op("not", ir.Op("truth", value))))
            if op in {"BitwiseAnd", "BitwiseNand", "BitwiseOr", "BitwiseNor", "BitwiseXor", "BitwiseXnor"}:
                commands, value = self.snapshot(commands, value)
                width = integral_type(node["operand"]["type"], self.symbols).width
                bits = ir.Op("uwrap", value, ir.Lit(width))
                if op in {"BitwiseAnd", "BitwiseNand"}:
                    result = ir.Op("toint", ir.Op("eq", bits, ir.Lit((1 << width) - 1)))
                elif op in {"BitwiseOr", "BitwiseNor"}:
                    result = ir.Op("toint", ir.Op("truth", bits))
                else:
                    index, parity = self.temp(), self.temp()
                    bit = ir.Op("and", ir.Op("shr", bits, ir.Var(index)), ir.Lit(1))
                    commands.extend([ir.Assign(parity, ir.Lit(0)),
                        ir.For(index, width, ir.Assign(parity, ir.Op("xor", ir.Var(parity), bit)))])
                    result = ir.Var(parity)
                if op in {"BitwiseNand", "BitwiseNor", "BitwiseXnor"}:
                    result = ir.Op("xor", result, ir.Lit(1))
                return commands, result
            if op in UNARY_PRIMITIVES:
                return self.operation_result(node, commands, (value,), scalar().wrap(ir.Op(UNARY_PRIMITIVES[op], value)))
        if kind == "BinaryOp":
            op = node["op"]
            if op in {"CaseEquality", "CaseInequality"}:
                operands = node['left'], node['right']
                unknown_literals = [child.get('kind') in {'IntegerLiteral', 'UnbasedUnsizedIntegerLiteral'}
                                    and four_state_literal(child['value']) for child in operands]
                if any(unknown_literals):
                    if self.span(node) in self.operation_rules:
                        return self.unsupported(node, 'four-state operand capture needs a four-state carrier')
                    if all(unknown_literals):
                        constant = node.get('constant')
                        if constant is None or four_state_literal(constant):
                            return self.unsupported(node, 'literal case equality needs its compiler result')
                        return [], scalar().wrap(ir.Lit(integer_literal(constant)))
                    # Ordinary expression lowering retains all effects and proves
                    # its value defined (including local reads, selects, division
                    # and X-producing branches). Such a value cannot be strictly
                    # case-equal to a literal containing X/Z. Do not substitute a
                    # zero for the unknown literal or assume other operands known.
                    defined = operands[1 if unknown_literals[0] else 0]
                    commands, _ = self.expression(defined)
                    return commands, ir.Lit(int(op == 'CaseInequality'))
            first, left = self.snapshot(*self.expression(node["left"]))
            second, right = self.expression(node["right"])
            if op in {"LogicalAnd", "LogicalOr"}:
                result = self.temp()
                first.append(ir.Assign(result, ir.Op("toint", ir.Op("truth", left))))
                selected = _sequence([*second, ir.Assign(result, ir.Op("toint", ir.Op("truth", right)))])
                guard = ir.Op("truth", left)
                first.append(ir.If(guard, selected, ir.Skip()) if op == "LogicalAnd"
                             else ir.If(guard, ir.Skip(), selected))
                return first, ir.Var(result)
            self.require_independent(node, self.effects(first, left), self.effects(second, right))
            if op in {"CaseEquality", "CaseInequality"}:
                result = ir.Op('eq' if op == 'CaseEquality' else 'ne', left, right)
                return self.operation_result(node, [*first, *second], (left, right), ir.Op('toint', result))
            if self.span(node) in self.operation_rules and op not in {"Divide", "Mod"}:
                second, right = self.snapshot(second, right)
            if op in {"Divide", "Mod"}:
                # Capture both operands once, at their original program points.
                # The compiler has already applied contextual sizing/sign rules.
                second, right = self.snapshot(second, right)
                captured = second[-1]
                self.divisor_bounds[id(captured)] = (captured.expr,
                    Refusal("rtl-source-ir-divisor-domain-unproved",
                        "source division needs a nonzero divisor; unknown results are not zero-filled",
                        node_kind=kind, span=self.span(node)))
                if scalar().signed:
                    # SV truncates toward zero; Python's floor division/modulo
                    # differ for negative operands. Unbounded magnitude math
                    # also preserves min_signed / -1 until the final wrap.
                    left_negative = ir.Op("lt", left, ir.Lit(0))
                    right_negative = ir.Op("lt", right, ir.Lit(0))
                    a = ir.Op("ite", left_negative, ir.Op("neg", left), left)
                    b = ir.Op("ite", right_negative, ir.Op("neg", right), right)
                    magnitude = ir.Op("fdiv" if op == "Divide" else "fmod", a, b)
                    negative = (ir.Op("ne", left_negative, right_negative)
                                if op == "Divide" else left_negative)
                    value = ir.Op("ite", negative, ir.Op("neg", magnitude), magnitude)
                else:
                    value = ir.Op("fdiv" if op == "Divide" else "fmod", left, right)
                return self.operation_result(node, [*first, *second], (left, right), scalar().wrap(value))
            if op in {"LogicalShiftLeft", "ArithmeticShiftLeft", "LogicalShiftRight", "ArithmeticShiftRight"}:
                target = scalar()
                # SV treats the self-determined RHS bits as an unsigned count.
                # Use finite-width shifts: huge counts must not allocate huge
                # host integers, and arithmetic right shift preserves sign.
                count_type = integral_type(node["right"]["type"], self.symbols)
                count = ir.Op("uwrap", right, ir.Lit(count_type.width))
                primitive = (("sshl" if target.signed else "ushl") if op.endswith("Left") else
                             "sashr" if op == "ArithmeticShiftRight" and target.signed else "ulshr")
                return self.operation_result(node, [*first, *second], (left, right),
                                             target.wrap(ir.Op(primitive, left, count, ir.Lit(target.width))))
            if op in BINARY_PRIMITIVES:
                value = binary_value(op, left, right)
                return self.operation_result(node, [*first, *second], (left, right), scalar().wrap(value))
        if kind == "ConditionalOp":
            condition = node["conditions"]
            if len(condition) != 1 or condition[0].get("pattern"):
                return self.unsupported(node)
            commands, value = self.expression(condition[0]["expr"])
            result = self.temp(kind=source_type(node["type"], self.symbols).ir_type)
            yes, a = self.expression(node["left"])
            no, b = self.expression(node["right"])
            commands.append(ir.If(ir.Op("truth", value),
                _sequence([self.mark("control-choice", node, "true"), *yes, ir.Assign(result, a)]),
                _sequence([self.mark("control-choice", node, "false"), *no, ir.Assign(result, b)])))
            return commands, ir.Var(result)
        if kind == "Concatenation":
            return self.concatenate(node, node["operands"])
        if kind == "Replication":
            count = self.replication_count(node)
            commands, part = self.expression(node["concat"])
            if count == 0:
                # The native profile does not evaluate an empty replication.
                # Keep every declared operand occurrence with a false guard,
                # not a fictitious call or a deleted catalogue entry.
                return [ir.If(ir.Lit(False), _sequence(commands), ir.Skip())], ir.Lit(0)
            width = integral_type(node["concat"]["type"], self.symbols).width
            # The operand is evaluated once. Its bit pattern occupies disjoint
            # width-bit lanes; a constant geometric sum duplicates those bits
            # without duplicating the helper calls, marks or IR expression.
            factor = ((1 << (width * count)) - 1) // ((1 << width) - 1)
            return commands, scalar().wrap(ir.Op("mul",
                ir.Op("uwrap", part, ir.Lit(width)), ir.Lit(factor)))
        if kind in {"SimpleAssignmentPattern", "StructuredAssignmentPattern"}:
            target = source_type(node["type"], self.symbols)
            items = node["elements" if kind == "SimpleAssignmentPattern" else "resolvedElements"]
            inactive = self.inactive_pattern_events(node, items)
            if isinstance(target, IntegralType):
                commands, value = self.concatenate(node, items)
                return [*inactive, *commands], value
            if len(items) != target.cells:
                return self.unsupported(node, "this assignment pattern needs its recursive typed elements")
            commands, elements = inactive, []
            previous = (set(), set())
            for element in items:
                before, value = self.snapshot(*self.expression(element))
                effects = self.effects(before, value)
                self.require_independent(node, previous, effects)
                previous = previous[0] | effects[0], previous[1] | effects[1]
                commands.extend(before)
                elements.append(value)
            return commands, ir.Op("arraylit", *elements)
        if kind in {"ElementSelect", "RangeSelect"}:
            if self.storage_selection(node):
                commands, bound = self.bind_lvalue(node, observe_read=True)
                before, value = self.read_lvalue(bound)
                return [*commands, *before], value
            commands, base = self.expression(node["value"])
            # Constants cannot change while the selector is evaluated. Keep
            # their source effects, but do not materialize an entire packed
            # ROM as a scalar temporary before reading a narrow window.
            if not isinstance(base, ir.Lit):
                commands, base = self.snapshot(commands, base)
            value_kind = source_type(node["value"]["type"], self.symbols)
            if isinstance(value_kind, ArrayType):
                if kind != "ElementSelect":
                    return self.unsupported(node, "unpacked slices need their array value mapping")
                index_commands, position = self.array_index(node["selector"], value_kind)
                self.require_independent(node, self.effects(commands, base), self.effects(index_commands, position))
                return [*commands, *index_commands], ir.Sel(base, position)
            before, offset, value_type = self.packed_selection(node)
            self.require_independent(node, self.effects(commands, base), self.effects(before, offset))
            access, value = self.read_packed(base, offset, value_type, scalar())
            return [*commands, *before, *access], value
        if kind == "Assignment":
            if "timingControl" in node:
                return self.unsupported(node, "an intra-assignment event/delay needs a resumable activation")
            if node.get("op"):
                commands, bound = self.bind_lvalue(node["left"])
                before, value = self.read_lvalue(bound)
                commands, old = self.snapshot([*commands, *before], value)
                previous = self.lvalue_value
                self.lvalue_value = old
                try:
                    right, value = self.snapshot(*self.expression(node["right"]))
                finally:
                    self.lvalue_value = previous
                # Slang owns the context-sized operation and conversions. We
                # only bind its LValueReference, once; no source-text rewrite.
                self.require_independent(node, self.effects(commands, old), self.effects(right, value))
                return [*commands, *right, *self.store_lvalue(bound, value, deferred=False)], value
            commands, value = self.assignment_expression(node["right"], node["left"])
            # A literal cannot change while the destination is evaluated.
            # Retain every RHS event/effect, but do not copy its immutable
            # value through an otherwise unused scratch variable.
            if not isinstance(value, ir.Lit):
                commands, value = self.snapshot(commands, value)
            commands.extend(self.store(node["left"], value, deferred=node.get("isNonBlocking", False),
                rhs_effects=self.effects(commands, value)))
            return commands, value
        if kind == "Call":
            return self.call(node)
        return self.unsupported(node)

    def store(self, target, value, *, deferred, rhs_effects=None):
        if target['kind'] == 'Concatenation':
            return self.store_concatenation(target, value, deferred=deferred, rhs_effects=rhs_effects)
        commands, bound = self.bind_lvalue(target)
        if rhs_effects is not None:
            self.require_independent(target, rhs_effects, self.effects(commands, None))
        return [*commands, *self.store_lvalue(bound, value, deferred=deferred)]

    def store_concatenation(self, target, value, *, deferred, rhs_effects):
        """Capture all coordinates before writing disjoint packed destinations."""
        parts, pending = [], list(reversed(target['operands']))
        while pending:
            part = pending.pop()
            if part['kind'] == 'Concatenation':
                pending.extend(reversed(part['operands']))
            else:
                parts.append(part)
        commands, destinations, footprints = [], [], {}
        previous = (set(), set()) if rhs_effects is None else rhs_effects
        for part in parts:
            before, bound = self.bind_lvalue(part)
            effects = self.effects(before, None)
            self.require_independent(target, previous, effects)
            previous = previous[0] | effects[0], previous[1] | effects[1]
            mask = self.lvalue_mask(bound)
            if footprints.get(bound.name, 0) & mask:
                return self.unsupported(target, 'overlapping concatenation destinations need their assignment order')
            footprints[bound.name] = footprints.get(bound.name, 0) | mask
            commands.extend(before)
            destinations.append(bound)
        offset = sum(bound.value_type.width for bound in destinations)
        for bound in destinations:
            offset -= bound.value_type.width
            part = ir.Op('uwrap', ir.Op('shr', value, ir.Lit(offset)), ir.Lit(bound.value_type.width))
            commands.extend(self.store_lvalue(bound, part, deferred=deferred))
        return commands

    def storage_selection(self, node):
        # A named storage path reads only its selected value. Capturing its
        # containing arrays first copies every cell on every loop iteration.
        # bind_lvalue preserves selector effects and source observation points;
        # read_lvalue retains local definedness.
        # Computed values and elaboration constants still use expression().
        while node['kind'] in {'ElementSelect', 'RangeSelect'}:
            node = node['value']
        if node['kind'] not in {'NamedValue', 'HierarchicalValue'}:
            return False
        identity = node['symbol'].split(' ', 1)[0]
        return self.symbols[identity]['kind'] not in {'Parameter', 'EnumValue', 'Genvar'}

    def bind_lvalue(self, target, *, observe_read=False):
        levels, base = [], target
        while base["kind"] in {"ElementSelect", "RangeSelect"}:
            levels.append(base)
            base = base["value"]
        if base["kind"] not in {"NamedValue", "HierarchicalValue"}:
            return self.unsupported(target, "selected writes need an explicit typed storage update")
        alias = self.reference_binding(base)
        name = alias.name if alias is not None else self.variable(base["symbol"])
        commands, selections = [], list(alias.selections) if alias is not None else []
        owner = alias.kind if alias is not None else source_type(base['type'], self.symbols)
        previous = (set(), set())
        for selection in reversed(levels):
            if observe_read:
                part = selection['value']
                # Normally an aggregate prefix is a storage path, not a read
                # of every cell. Explicit intermediate read/Boolean goals do
                # observe that value, so retain their definedness and marks.
                span = self.span(part)
                observed = (span in self.read_rules or any(
                    ('boolean-expression', *span, self.calls, outcome) in self.marks
                    for outcome in ('false', 'true')))
                if observed:
                    before, value = self.read_lvalue(BoundLValue(name, owner, tuple(selections), part))
                    commands.extend(before)
                    commands, value = self.observe_read_value(part, commands, value)
            kind = source_type(selection["value"]["type"], self.symbols)
            if isinstance(kind, ArrayType):
                if selection["kind"] != "ElementSelect":
                    return self.unsupported(selection, "unpacked slices need their array value mapping")
                before, index = self.array_index(selection["selector"], kind)
            else:
                before, index, kind = self.packed_selection(selection)
            effects = self.effects(before, index)
            if observe_read:
                self.require_independent(selection, ({name}, set()), effects)
            self.require_independent(selection, previous, effects)
            previous = previous[0] | effects[0], previous[1] | effects[1]
            commands.extend(before)
            selections.append(BoundSelection(index, kind, source_type(selection["type"], self.symbols)))
        return commands, BoundLValue(name, owner, tuple(selections), target)

    def read_lvalue(self, bound):
        commands, value = [], ir.Var(bound.name)
        for selection in bound.selections:
            index, kind, target = selection.index, selection.owner, selection.selected
            if isinstance(kind, ArrayType):
                value = ir.Sel(value, index)
            else:
                # Use the same flow-sensitive in-range proof as packed stores;
                # a selector side effect must never be evaluated a second time.
                before, value = self.read_packed(value, index, kind, target)
                commands.extend(before)
        commands.extend(self.defined.read(bound))
        return commands, value

    def store_lvalue(self, bound, value, *, deferred):
        self.record_write(bound, deferred=deferred)
        if deferred:
            return self.defer_lvalue(bound, value)
        name, owner_type = bound.name, bound.kind
        commands, parents, packed = [], [], []
        # Reload containing arrays here, after RHS evaluation: independent RHS
        # effects on other cells must survive an ordinary selected assignment.
        for selection in bound.selections:
            index, kind = selection.index, selection.owner
            if isinstance(kind, ArrayType):
                parents.append((name, index))
                child = self.temp(kind=kind.element.ir_type)
                commands.append(ir.Assign(child, ir.Sel(ir.Var(name), index)))
                name, owner_type = child, kind.element
            else:
                packed.append((index, kind.width))
        type_ = bound.value_type
        value = type_.wrap(value)
        mask = None
        if packed:
            width = type_.width
            value, mask = ir.Op("uwrap", value, ir.Lit(width)), ir.Lit((1 << width) - 1)
            for offset, destination_width in reversed(packed):
                before, value, mask = self.align_packed_write(value, mask, offset, width, destination_width)
                commands.extend(before)
                width = destination_width
        if mask is not None:
            value = merge_bits(owner_type, ir.Var(name), value, mask)
        commands.append(ir.Assign(name, value))
        for parent, index in reversed(parents):
            commands.append(ir.Store(parent, index, ir.Var(name)))
            name = parent
        commands.extend(self.defined.write(bound))
        return commands

    def defer_lvalue(self, bound, value):
        """Capture a typed selected value now; update source storage at commit.

        Array coordinates and packed selections share one per-bit queue. Each
        selector is already captured and each containing range remains proved;
        different cells, repeated writes and restored bits retain their history.
        """
        kind, owner = bound.value_type, bound.kind
        if isinstance(owner, ArrayType):
            return self.defer_array_lvalue(bound, value)
        commands, value = [], kind.wrap(value)
        width, mask = kind.width, None
        if bound.selections:
            value = ir.Op("uwrap", value, ir.Lit(width))
            mask = ir.Lit((1 << width) - 1)
            for selection in reversed(bound.selections):
                container = selection.owner
                offset = selection.index
                before, value, mask = self.align_packed_write(value, mask, offset, width, container.width)
                commands.extend(before)
                width = container.width
        if bound.name not in self.deferred:
            self.deferred[bound.name] = DeferredWrite(bound.name, self.temp(), self.temp(boolean=True),
                self.temp(), self.temp(), self.temp(), owner.width,
                owner.signed, whole=not bound.selections)
        elif bound.selections and self.deferred[bound.name].whole:
            # A later partial write invalidates the whole-word certificate for
            # the entire queue, including earlier whole writes. Commit plans
            # are built only after all source assignments have been lowered.
            self.deferred[bound.name] = replace(self.deferred[bound.name], whole=False)
        commands.extend(self.deferred[bound.name].enqueue(value, mask))
        return commands

    def defer_array_lvalue(self, bound, value):
        owner = bound.kind
        if bound.name not in self.deferred:
            array_temp = lambda: self.temp(kind=owner.ir_type)
            self.deferred[bound.name] = ArrayDeferredWrite(
                bound.name, array_temp(), self.temp(boolean=True), array_temp(), array_temp(),
                array_temp(), owner.width, array=owner, rises=array_temp(), falls=array_temp(),
                whole=True)
        path, packed, kind = [], [], owner
        for selection in bound.selections:
            if isinstance(selection.owner, ArrayType):
                path.append(selection.index)
                kind = selection.selected
            else:
                packed.append(selection)
        commands, mask = [], None
        value = bound.value_type.wrap(value)
        if packed:
            # Whole-cell writes preserve a uniform element invariant. A packed
            # slice does not: invalidate the certificate for the entire queue.
            self.deferred[bound.name] = replace(self.deferred[bound.name], whole=False)
            width = bound.value_type.width
            value, mask = ir.Op("uwrap", value, ir.Lit(width)), ir.Lit((1 << width) - 1)
            for selection in reversed(packed):
                before, value, mask = self.align_packed_write(
                    value, mask, selection.index, width, selection.owner.width)
                commands.extend(before)
                width = selection.owner.width
        commands.extend(self.deferred[bound.name].enqueue_selected(value, kind, tuple(path), mask, self.temp))
        return commands

    def lvalue_mask(self, bound):
        """Union the typed write footprint over every possible selector value.

        Build inside out: an unknown outer array row still preserves a fixed
        inner column. Never borrow one replay index. Access-domain admission
        proves the original read/write separately before publication.
        """
        mask = (1 << bound.value_type.width)-1
        domains = ChainMap(self.loop_ranges, self.snapshot_ranges, self.ranges)
        for selection in reversed(bound.selections):
            index = ranges.bounds_of_expression(selection.index, domains)
            stride = selection.owner.element.width if isinstance(selection.owner, ArrayType) else 1
            if index is not None and index.lo is not None and index.lo == index.hi:
                low = index.lo * stride
                if 0 <= low and low + selection.selected.width <= selection.owner.width:
                    mask <<= low
                    continue
            if isinstance(selection.owner, ArrayType):
                # Each element is a disjoint stride-wide block. Multiplication
                # tiles the inner mask without overlapping bits or enumerating
                # the array; all rows are included even when ranges are unknown.
                repeats = ((1 << selection.owner.width)-1)//((1 << stride)-1)
                mask *= repeats
            else:
                mask = (1 << selection.owner.width)-1
        return mask

    def record_write(self, bound, *, deferred):
        mask = self.lvalue_mask(bound)
        previous = self.write_masks.get(bound.name, (0, 0))
        self.write_masks[bound.name] = (previous[0], previous[1] | mask) if deferred else (
            previous[0] | mask, previous[1])

    def call(self, node):
        phases = self.call_phases(node)
        return [*phases.encounter, *phases.execution], phases.value

    def capture_action(self, node):
        """Lower a report action without moving its entry or body to encounter.

        The report scheduler must separately handle maturity, cancellation and
        dispatch. This does not relax the deferred assertion admission gate.
        """
        entry = self.mark('location', node, 'entry')
        if node['kind'] == 'Empty':
            return CallPhases((), (entry,), ir.Lit(0))
        if node['kind'] != 'ExpressionStatement' or node['expr']['kind'] != 'Call':
            return self.unsupported(node, 'a deferred action needs its bound subroutine call')
        phases = self.call_phases(node['expr'], deferred=True)
        return CallPhases(phases.encounter, (entry, *phases.execution),
                          phases.value, phases.captures)

    def call_phases(self, node, *, deferred=False):
        if node["subroutine"] == "$clog2":
            if deferred:
                return self.unsupported(node, 'a logarithm is not a deferred action subroutine')
            argument, = node['arguments']
            commands, value = self.snapshot(*self.expression(argument))
            width = integral_type(argument['type'], self.symbols).width
            # SV uses the operand's unsigned bit pattern; zero is a special
            # case, otherwise ceil(log2(n)) = bit_length(n-1). Evaluate once.
            bits = ir.Op('uwrap', value, ir.Lit(width))
            result = self.temp()
            commands.append(ir.If(ir.Op('eq', bits, ir.Lit(0)),
                ir.Assign(result, ir.Lit(0)),
                ir.Assign(result, ir.Op('bitlen', ir.Op('sub', bits, ir.Lit(1))))))
            return CallPhases(tuple(commands), (), integral_type(node['type'], self.symbols).wrap(ir.Var(result)))
        if node["subroutine"] == "$bits":
            if deferred:
                return self.unsupported(node, 'a type query is not a deferred action subroutine')
            argument, = node["arguments"]
            # The fixed-size type, not the operand value, owns this answer.
            # Match source_declarations: neither operand calls nor reads execute.
            # source_type rejects dynamic/unimplemented layouts explicitly.
            width = source_type(argument["type"], self.symbols).width
            return CallPhases((), (), integral_type(node["type"], self.symbols).wrap(ir.Lit(width)))
        if node["subroutine"] in {"$countones", "$onehot", "$onehot0"}:
            if deferred:
                return self.unsupported(node, 'a bit query is not a deferred action subroutine')
            argument, = node["arguments"]
            commands, value = self.expression(argument)
            width = integral_type(argument["type"], self.symbols).width
            # SV counts the operand's self-determined bit pattern, whereas
            # Python/IR popcount counts an integer's magnitude. Convert signed
            # values to their finite unsigned bits first; retain all operand
            # effects and definedness checks from the ordinary expression path.
            count = ir.Op("popcount", ir.Op("uwrap", value, ir.Lit(width)))
            if node["subroutine"] != "$countones":
                comparison = "eq" if node["subroutine"] == "$onehot" else "le"
                count = ir.Op("toint", ir.Op(comparison, count, ir.Lit(1)))
            result = integral_type(node["type"], self.symbols).wrap(count)
            return CallPhases(tuple(commands), (), result)
        if node["subroutine"] in {"$signed", "$unsigned"}:
            if deferred:
                return self.unsupported(node, 'a conversion is not a deferred action subroutine')
            argument, = node["arguments"]
            commands, value = self.expression(argument)
            # Compiler-resolved cast width/signedness, before any enclosing
            # expression's conversion. The operand and its effects run once.
            return CallPhases(tuple(commands), (), integral_type(node["type"], self.symbols).wrap(value))
        if node["subroutine"] in {"$fatal", "$error"}:
            # Native and symbolic paths consume the same ordering admission.
            # Argument commands (including an inner Fail) execute before the
            # outer failure. Formatting itself has no artifact-state effect.
            failure = self.compiler.failures.get(self.span(node))
            if failure is not None and failure.issue:
                return self.unsupported(node, failure.issue)
            if failure is None and self.context is None:
                issue = argument_order_issue(node.get('arguments', ()), self.compiler.failure_effects)
                if issue:
                    return self.unsupported(node, issue)
            commands = []
            for argument in node.get("arguments", ()):
                if argument['kind'] not in {'StringLiteral', 'RealLiteral'}:
                    evaluated, _ = self.expression(argument)
                    commands.extend(evaluated)
            # The bound native execution profile stops on both severity tasks.
            # This is not the language-wide claim that every $error is fatal.
            code = "RTLFatal" if node["subroutine"] == "$fatal" else "RTLError"
            return CallPhases(tuple(commands), (self.terminal_failure(node, code),), ir.Lit(0))
        identity = node["subroutine"].split(" ", 1)[0]
        callee = self.symbols.get(identity)
        if callee is None or callee.get("kind") != "Subroutine":
            return self.unsupported(node, "system calls need an explicit source semantic primitive")
        if identity in self.active_calls:
            return self.unsupported(node, "recursive source calls need a finite activation relation")
        arguments = prepare_arguments(self, callee, node, deferred=deferred)
        # Argument evaluation belongs to the caller. Its domain obligations
        # cannot be discharged by a proof of the callee body.
        accesses_before, divisors_before = set(self.access_bounds), set(self.divisor_bounds)
        controls_before = set(self.control_domains)
        definitions_before = set(self.defined.obligations)
        # The action may execute after other processes have changed source
        # storage. Only its captured arguments keep encounter-time ranges;
        # caller loop facts are not premises about dispatch-time globals.
        old_loops, old_snapshots = self.loop_ranges, self.snapshot_ranges
        if deferred:
            self.loop_ranges = {}
            self.snapshot_ranges = {name: bound for name, bound in old_snapshots.items()
                                    if name in arguments.captures}
        try:
            commands, value = self.invoke(callee, node, arguments.values, arguments=arguments)
        finally:
            if deferred:
                self.loop_ranges = old_loops
                self.snapshot_ranges = old_snapshots
        accesses = set(self.access_bounds) - accesses_before
        divisors = set(self.divisor_bounds) - divisors_before
        controls = set(self.control_domains) - controls_before
        definitions = set(self.defined.obligations) - definitions_before
        if (self.modular_domains and not arguments.outputs and not arguments.references
                and (accesses or divisors or controls or definitions) and self.helper_defined(callee, node)):
            for key in accesses:
                del self.access_bounds[key]
            for key in divisors:
                del self.divisor_bounds[key]
            for key in controls:
                del self.control_domains[key]
            for key in definitions:
                del self.defined.obligations[key]
        return CallPhases(tuple(arguments.commands), tuple(commands), value, arguments.captures)

    def helper_defined(self, callee, node):
        identity = str(callee["addr"])
        answers = self.compiler.helper_domains
        if identity not in answers:
            helper = _Lowerer(self.compiler, self.context, namespace="", storage_name=None,
                             domain_budget=self.domain_budget, modular_domains=False)
            helper.calls, helper.active_calls = self.calls, self.active_calls
            # Typed formals are fresh boundary values, not the actual arguments
            # or the caller's interval facts. Static/global source storage also
            # receives its entire declared domain, including before later writes.
            values = [ir.Var(helper.bind(str(arg["addr"]))) for arg in callee.get("arguments", ())]
            try:
                commands, _ = helper.invoke(callee, node, values)
                # A dynamic-loop lemma over all formal values can be much
                # larger than the actual call (e.g. f(..., 5) versus every int).
                # Keep those obligations at their caller instead. This omits
                # only an optional reusable lemma, never a required proof.
                if any(isinstance(command, ir.For) and not isinstance(command.trip_count, ir.Lit)
                       for command in ir.walk(_sequence(commands))):
                    answers[identity] = False
                    return False
                state = list(helper.storage.values())
                boundary = set(state)
                locals_ = [name for name in helper.types if name not in boundary]
                procedure = ir.Procedure(state, [], locals_, [], _sequence(commands), types=helper.types)
                helper.domain_budget.prove(procedure, {name: helper.ranges[name] for name in state},
                                           helper.access_bounds, divisors=helper.divisor_bounds,
                                           predicates={**helper.control_domains, **helper.defined.obligations})
            except Refused:
                # A helper can require a caller precondition. SAT/UNKNOWN here
                # is not a design failure or final refusal: retain all original
                # obligations for the ordinary full-activation proof.
                answers[identity] = False
            else:
                answers[identity] = True
        return answers[identity]

    def invoke(self, callee, node, values, *, arguments=None):
        """Lower the same body for actual calls and all-input domain lemmas."""
        identity = str(callee["addr"])
        formals = callee.get("arguments", ())
        before = []
        frame = {}
        self.frames.append(frame)
        old_references = self.references
        if arguments is not None:
            self.references = {**old_references, **arguments.references}
        old_calls, old_active = self.calls, self.active_calls
        old_returned, old_return_value = self.returned, self.return_value
        old_loop_control, self.loop_control = self.loop_control, None
        self.calls = (*self.calls, self.span(node))
        self.active_calls = (*self.active_calls, identity)
        returns = self.compiler.helper_returns
        if identity not in returns:
            # Assignment to the function's result is not an early return.
            # Calls have compiler-bound identities, not nested callee bodies,
            # so a called helper's return cannot escape this activation.
            returns[identity] = any(part.get("kind") == "Return"
                                    for part in _nodes(callee["body"]))
        self.returned = self.temp(boolean=True) if returns[identity] else None
        self.return_value = None
        if self.returned is not None:
            before.append(ir.Assign(self.returned, ir.Lit(False)))
        for member in callee.get("members", ()):
            if member["kind"] in {"Variable", "FormalArgument"} and member.get("lifetime") == "Automatic":
                if str(member['addr']) in self.references:
                    continue
                name = self.bind(str(member["addr"]), local=True)
                if member.get("flags") == "compiler_generated" and member["name"] == callee["name"]:
                    self.return_value = name
        for formal, value in zip(formals, values):
            if formal['direction'] == 'Ref':
                continue
            name = self.bind(str(formal["addr"]), local=True)
            if formal['direction'] == 'Out':
                before.extend(self.defined.declare(name, source_type(formal['type'], self.symbols),
                    role='output', defined=default_defined(formal['type'], self.symbols)))
            else:
                before.append(ir.Assign(name, source_type(formal['type'], self.symbols).wrap(value)))
        return_type = callee['returnType']
        if isinstance(return_type, str):
            return_type = self.symbols[return_type.split(' ', 1)[0]]
        void = return_type['kind'] == 'VoidType'
        if not void and self.return_value is None:
            return self.unsupported(node, "a source function needs its compiler-resolved return storage")
        if not void:
            before.extend(self.defined.declare(self.return_value, source_type(node['type'], self.symbols),
                role='return', defined=default_defined(node['type'], self.symbols)))
        before.append(self.statement(callee["body"]))
        outputs = ([] if arguments is None else
                   [ir.Var(self.variable(str(formal['addr']))) for formal, _ in arguments.outputs])
        result = ir.Lit(0) if void else source_type(node['type'], self.symbols).wrap(ir.Var(self.return_value))
        for formal, _ in (() if arguments is None else arguments.outputs):
            before.extend(self.defined.read(BoundLValue(self.variable(str(formal['addr'])),
                source_type(formal['type'], self.symbols), (), node)))
        if not void:
            before.extend(self.defined.read(BoundLValue(self.return_value,
                source_type(node['type'], self.symbols), (), node)))
        self.calls, self.active_calls = old_calls, old_active
        self.returned, self.return_value = old_returned, old_return_value
        self.loop_control = old_loop_control
        self.references = old_references
        self.frames.pop()
        if arguments is not None:
            # Actual lvalues (and helpers in their selectors) are caller source
            # occurrences, even though copy-out executes after the callee body.
            before.extend(arguments.copy_out(self, outputs))
        return before, result

    def active(self, command, *, control=None, backedge=False):
        flags = [] if self.returned is None else [self.returned]
        control = self.loop_control if control is None else control
        if control is not None:
            flags.extend(flag for flag in (control[:1] if backedge else control) if flag is not None)
        for flag in reversed(flags):
            command = ir.If(ir.Op("not", ir.Var(flag)), command, ir.Skip())
        return command

    def loop_body(self, node):
        # A transfer belongs to the nearest lexical loop in this function.
        # Each iteration clears continue; break persists until this loop exits.
        previous = self.loop_control
        transfers = loop_transfers(node)
        control = tuple(self.temp(boolean=True) if kind in transfers else None
                        for kind in ('Break', 'Continue'))
        self.loop_control = control
        body = self.statement(node)
        self.loop_control = previous
        reset = [] if control[1] is None else [ir.Assign(control[1], ir.Lit(False))]
        return control, _sequence([*reset, body])

    @staticmethod
    def loop_start(control):
        return [] if control[0] is None else [ir.Assign(control[0], ir.Lit(False))]

    def loop_iteration(self, body, control, tail=()):
        # Continue suppresses later body statements, never the step/test.
        # Break and return suppress the tail and all subsequent iterations.
        return self.active(_sequence([body, self.active(_sequence(tail),
            control=control, backedge=True)]), control=control, backedge=True)

    def statement(self, node):
        kind = node.get("kind")
        if kind == "List":
            parts = []
            for item in node["list"]:
                parts.append(self.active(self.statement(item)))
            return _sequence(parts)
        if kind == "Block":
            return self.statement(node["body"])
        entry = self.mark("location", node, "entry")
        if kind == "Empty":
            return entry
        if kind == "ExpressionStatement":
            commands, _ = self.expression(node["expr"])
            return _sequence([entry, *commands])
        if kind == "VariableDeclaration":
            symbol = self.symbols[node["symbol"].split(" ", 1)[0]]
            if symbol.get("lifetime") != "Automatic":
                return ir.Skip()
            name = self.bind(str(symbol["addr"]), local=True)
            target = source_type(symbol['type'], self.symbols)
            initialization = self.defined.declare(name, target,
                defined=default_defined(symbol['type'], self.symbols))
            if "initializer" not in symbol:
                return _sequence(initialization)
            commands, value = self.expression(symbol["initializer"])
            bound = BoundLValue(name, target, (), node)
            return _sequence([*initialization, entry, *commands, ir.Assign(name, target.wrap(value)),
                              *self.defined.write(bound)])
        if kind == "Conditional":
            conditions = node["conditions"]
            if len(conditions) != 1 or conditions[0].get("pattern"):
                return self.unsupported(node)
            commands, condition = self.expression(conditions[0]["expr"])
            yes = _sequence([self.mark("control-choice", node, "true"), self.statement(node["ifTrue"])])
            no = _sequence([self.mark("control-choice", node, "false"),
                            self.statement(node["ifFalse"]) if "ifFalse" in node else ir.Skip()])
            return _sequence([entry, *commands, ir.If(ir.Op("truth", condition), yes, no)])
        if kind == "Case":
            if node["condition"] not in {"Normal", "WildcardJustZ", "WildcardXOrZ"}:
                return self.unsupported(node, "wildcard cases need their declared match semantics")
            before, value = self.expression(node["expr"])
            selector_type = integral_type(node['expr']['type'], self.symbols)
            selector_bounds = selector_type.interval
            try:
                # The comparison may widen a narrow value to int. Preserve
                # its typed expression range, without state invariants or a
                # sample. Effects/definedness still execute in `before`.
                selector_bounds = _integral_interval(node['expr'], self.symbols)
            except Refused:
                # A range optimization cannot reject a deferred operation or
                # invent its value; the ordinary domain guard remains intact.
                pass
            selector_reads, selector_writes = self.effects(before, value)
            commands, selector = self.snapshot(before, value)
            items = tuple(node.get("items", ()))
            wildcard = node['condition'] != 'Normal'
            if wildcard:
                width = integral_type(node['expr']['type'], self.symbols).width
                patterns = tuple(tuple(constant_pattern(self, label, node['condition'], width)
                                       for label in item['expressions']) for item in items)
                label_values = tuple(tuple(([], pattern.predicate(selector)) for pattern in row)
                                     for row in patterns)
                checks = qualified_pattern_check(self, node, selector, patterns)
            else:
                label_values = tuple(tuple(self.expression(label) for label in item['expressions'])
                                     for item in items)
                checks = qualified_constant_check(self, node, selector, label_values)
            # Qualified case lowering evaluates each reached label once, in
            # source priority order. The captured selector precedes all label
            # writes; later labels see earlier writes, not a reread selector.
            # Keep both retained-state changes and native observation effects.
            default = node.get("defaultCase")
            fallback = _sequence([self.mark("control-choice", node, "default" if default else "no-match"),
                                  self.statement(default) if default else ir.Skip()])
            priority = node.get('check') == 'Priority'
            if any(kind == 'no-match' for _, kind in checks):
                # The native no-match callback follows the original case and
                # is not reached after a terminal violation. Keep its declared
                # occurrence, but never credit it on that failed prefix.
                fallback = (_sequence([self.terminal_failure(node, 'RTLCaseNoMatch'), fallback])
                            if priority else ir.If(ir.Lit(False), fallback, ir.Skip()))
            if not wildcard and constant_labels_exhaustive(
                    selector_type, label_values, bounds=selector_bounds):
                # Preserve every declared default/body occurrence beneath its
                # exact false predicate. The compiler-typed, defined selector
                # must match a constant label; no sampled/state-domain premise
                # or qualification about which input search tries is involved.
                fallback = ir.If(ir.Lit(False), fallback, ir.Skip())
            constant_labels = True
            for values in label_values:
                for effects, label in values:
                    dependencies = set()
                    reads(label, dependencies)
                    constant_labels &= wildcard or (not effects and not dependencies)
            arms = []
            for index, item in enumerate(items):
                values = [(effects, value if wildcard else ir.Op('eq', selector, value))
                          for effects, value in label_values[index]]
                # Only effectful multi-label items need short-circuit state.
                # A single label is evaluated once when its item is reached;
                # effect-free comparisons can form one balanced disjunction.
                # Item priority and all source events stay in the ordered scan.
                if not constant_labels:
                    matched = self.temp(boolean=True)
                if constant_labels:
                    # The selector is already captured and labels are pure.
                    # A shared predicate needs no extra assigned Boolean for
                    # every table row; dispatch can read it unchanged.
                    match = ir._balanced_op("lor", (value for _, value in values), ir.Lit(False))
                    labels = []
                elif len(values) == 1:
                    effects, value = values[0]
                    labels = [*effects, ir.Assign(matched, value)]
                elif not any(effects for effects, _ in values):
                    labels = [ir.Assign(matched, ir._balanced_op("lor", (
                        value for _, value in values), ir.Lit(False)))]
                else:
                    labels = [ir.Assign(matched, ir.Lit(False))]
                    for effects, value in values:
                        labels.append(ir.If(ir.Op("not", ir.Var(matched)),
                            _sequence([*effects, ir.Assign(matched, value)]), ir.Skip()))
                if not constant_labels:
                    match = ir.Var(matched)
                selected = _sequence([self.mark("control-choice", node, f"arm_{index}"),
                                       self.statement(item["stmt"])])
                arms.append((labels, match, selected))
            if constant_labels:
                # A constant-label table has no label effects to short-circuit.
                # Keep actual branch structure, so other flow proofs still see
                # that a complete case writes once on each path.
                rest = (None if wildcard else _literal_case_dispatch(selector, label_values,
                    arms, fallback, selector_bounds))
                if rest is None:
                    rest = _sequence([*(part for labels, _, _ in arms for part in labels),
                        _constant_case_dispatch([(match, body) for _, match, body in arms], fallback)])
            else:
                rest = fallback
                for labels, match, selected in reversed(arms):
                    rest = _sequence([*labels, ir.If(match, selected, rest)])
            if checks and not priority and (selector_writes or selector_reads & self.effects([rest], ir.Lit(0))[1]):
                # The native compiler can check uniqueness after the selected
                # body using the live selector. A program-point proof is not
                # enough when the selector can change before that check.
                return self.unsupported(node, "program-point case checks need a stable native selector")
            after = []
            if not priority:
                after = [ir.If(valid, ir.Skip(), self.terminal_failure(node,
                    'RTLCaseNoMatch' if kind == 'no-match' else 'RTLCaseOverlap'))
                    for valid, kind in checks]
            if not checks and node.get('check') in {'Unique', 'Unique0', 'Priority'}:
                # A structurally inert native check still has its frozen
                # safety catalogue entry, with an exact false producer.
                after.append(ir.If(ir.Lit(False),
                    self.mark('artifact-failure', node, 'failure'), ir.Skip()))
            # Unique checks are native post-body actions. Return/break/continue
            # bypass them; a failed body cannot continue into them either.
            return _sequence([entry, *commands, rest, self.active(_sequence(after))])
        if kind == "ForLoop":
            loop = self.loops.get(self.span(node))
            initialization = []
            for initializer in node.get("initializers", ()):
                commands, _ = self.expression(initializer)
                initialization.extend(commands)
            # Event-free tests are already discharged by the constant-header
            # proof. Observable tests retain their source expression effects.
            dynamic = loop is None
            if dynamic:
                try:
                    progress = for_progress(node, self.symbols)
                except ValueError as exc:
                    return self.unsupported(node, str(exc))
                condition, test, limit = self.loop_condition(node["stopExpr"], progress)
                live = self.temp(boolean=True)
                condition = [*condition, ir.Assign(live, ir.Op("truth", test))]
                values = ()
            else:
                condition = self.expression(node["stopExpr"])[0] if loop.condition_events else []
                values = loop.execution_values(loop_key(node))
            counter = progress.counter if dynamic else node["stopExpr"]["left"]
            while counter.get("kind") == "Conversion":
                counter = counter["operand"]
            previous = self.loop_ranges
            name = self.variable(counter["symbol"])
            if values:
                self.loop_ranges = {**previous, name: ranges.Range(min(values), max(values))}
            control, body = self.loop_body(node["body"])
            self.loop_ranges = previous
            counter_changes = name in self.effects([body], ir.Lit(0))[1]
            if name in self.effects(condition, ir.Lit(0))[1] or (counter_changes and not dynamic):
                return self.unsupported(node, 'for condition/body mutates its proved counter')
            steps = []
            for step in node["steps"]:
                commands, _ = self.expression(step)
                steps.extend(commands)
            capture = []
            count = len(values)
            if dynamic:
                if self.loop_ranges:
                    progress = for_progress(node, self.symbols, bound_ranges=self.loop_bound_ranges(
                        [*initialization, *condition, body, *steps]))
                writes = [cmd for cmd in ir.walk(_sequence(steps))
                          if isinstance(cmd, (ir.Assign, ir.Store)) and cmd.var == name]
                if len(writes) != 1:
                    return self.unsupported(node, 'for step changes its counter outside the proved back-edge')
                start = ir.Var(name)
                bound = self.temp()
                limit = self.invariant_loop_limit(limit, [*condition, body, *steps], self.snapshots)
                snapshot = ir.Assign(bound, ir.Op('ite', ir.Var(live),
                    progress.trips(start, limit), ir.Lit(0)))
                valid = ir.Op('lor', ir.Op('not', ir.Var(live)), progress.in_domain(start, snapshot.expr))
                self.control_domains[id(snapshot)] = (valid, Refusal('rtl-source-ir-loop-domain-unproved',
                    'for ranking needs nonwrapping, order-preserving source conversions', span=self.span(node)))
                capture.append(snapshot)
                count = ir.Var(bound)
            entered = ir.Var(live) if dynamic else ir.Lit(bool(count))
            activation = ir.If(entered, self.mark("loop-activation", node, "nonzero"),
                               self.mark("loop-activation", node, "zero"))
            # Evaluate the actual source test once before entry and after each
            # completed body/step, including the final false test. Return skips
            # subsequent steps/tests; a failing test precedes any loop hit.
            iteration = self.loop_iteration(body, control, [*steps, *condition])
            if dynamic:
                iteration = ir.If(ir.Var(live), iteration, ir.Skip())
            completed = []
            if counter_changes:
                # The header supplies a candidate finite bound, not permission
                # to truncate execution. Prove that every actual body/step/test
                # has exited by that bound, including source break and return.
                # Failure stays a private translation obligation and cannot be
                # treated as an assumption on the inputs or a public goal hit.
                exits = [ir.Op('not', ir.Var(live))]
                exits.extend(ir.Var(flag) for flag in (control[0], self.returned) if flag is not None)
                valid = ir._balanced_op('lor', exits, ir.Lit(False))
                certificate = ir.Assign(self.temp(boolean=True), valid)
                self.control_domains[id(certificate)] = (valid, Refusal(
                    'rtl-source-ir-loop-domain-unproved',
                    'body-modified for counter needs proof of exit within its header bound',
                    span=self.span(node)))
                completed.append(certificate)
            index = self.temp()
            if not dynamic and values and not any(control) and self.returned is None:
                # The constant-header proof establishes this equality at every
                # entry (including nonwrapping source increments). Retain the
                # actual body, back-edge writes and observations. This binding
                # writes the value already present, so adds no transition; it
                # exposes the same coordinate to shared value/range summaries.
                coordinate = ir.Op('add', ir.Lit(values.start),
                                   ir.Op('mul', ir.Var(index), ir.Lit(values.step)))
                iteration = _sequence([ir.Assign(name, coordinate), iteration])
            return _sequence([entry, *initialization, *condition, *capture, activation,
                              *self.loop_start(control),
                              ir.For(index, count, iteration), *completed])
        if kind in {'WhileLoop', 'DoWhileLoop'}:
            return self.while_loop(node, entry)
        if kind == 'RepeatLoop':
            commands, value = self.expression(node['count'])
            count = self.temp()
            # SV captures the typed count once, before any body effect. A
            # signed nonpositive count performs no iterations.
            capture = ir.Assign(count, ir.Op('ite', ir.Op('gt', value, ir.Lit(0)), value, ir.Lit(0)))
            activation = ir.If(ir.Op('gt', ir.Var(count), ir.Lit(0)),
                self.mark('loop-activation', node, 'nonzero'), self.mark('loop-activation', node, 'zero'))
            control, body = self.loop_body(node['body'])
            return _sequence([entry, *commands, capture, activation,
                              *self.loop_start(control),
                              ir.For(self.temp(), ir.Var(count), self.loop_iteration(body, control))])
        if kind in {'Break', 'Continue'} and self.loop_control is not None:
            return _sequence([entry, ir.Assign(self.loop_control[kind == 'Continue'], ir.Lit(True))])
        if kind == "ImmediateAssertion":
            if node["assertionKind"] != "Assert" or node.get("isDeferred") or node.get("isFinal"):
                return self.unsupported(node, "deferred assertion actions need their event-region semantics")
            commands, condition = self.expression(node["cond"])
            success = self.statement(node["ifTrue"]) if "ifTrue" in node else ir.Skip()
            event = self.mark("artifact-failure", node, "failure")
            if not isinstance(event, ir.Mark):
                return self.unsupported(node, "assertion failure has no source declaration")
            # An explicit null else recovers; an omitted else runs default
            # $error even if there is a pass action (IEEE 1800-2017 16.3).
            failure = (_sequence([event, self.statement(node["ifFalse"])])
                       if "ifFalse" in node else ir.Fail("RTLAssertion", event.goal))
            return _sequence([entry, *commands, ir.If(ir.Op("truth", condition), success, failure)])
        if kind == "Return" and self.returned is not None:
            commands = []
            if 'expr' in node:
                commands, value = self.expression(node['expr'])
                commands.append(ir.Assign(self.return_value, value))
                commands.extend(self.defined.write(BoundLValue(self.return_value,
                    source_type(node['expr']['type'], self.symbols), (), node)))
            return _sequence([entry, *commands, ir.Assign(self.returned, ir.Lit(True))])
        return self.unsupported(node)

    def unsupported(self, node, message="this typed source operation has no command lowering"):
        raise Refused(Refusal("rtl-source-ir-operation-unsupported", message,
                              node_kind=node.get("kind"), span=self.span(node)))

    def while_loop(self, node, entry):
        """Share progress proofs, not the entry order of pre/post-tested loops."""
        posttest = node['kind'] == 'DoWhileLoop'
        progress = None
        # A universally false typed test proves zero/one bodies without a
        # ranking counter. Its effects still execute at the source test site.
        if _integral_interval(node['cond'], self.symbols) != (0, 0):
            try:
                progress = while_progress(node, self.symbols)
            except ValueError as exc:
                return self.unsupported(node, str(exc))
        condition, test, limit = self.loop_condition(node['cond'], progress)
        live = self.temp(boolean=True)
        condition = [*condition, ir.Assign(live, ir.Op('truth', test))]
        control, body = self.loop_body(node['body'])
        bound = ir.Lit(0)
        if progress is not None:
            if self.loop_ranges:
                progress = while_progress(node, self.symbols,
                    bound_ranges=self.loop_bound_ranges([*condition, body]))
            counter = self.variable(progress.counter['symbol'])
            if counter in self.effects(condition, test)[1]:
                return self.unsupported(node, 'while condition mutates its ranking counter')
            writes = [cmd for cmd in ir.walk(body)
                      if isinstance(cmd, (ir.Assign, ir.Store)) and cmd.var == counter]
            if len(writes) != 1:
                return self.unsupported(node, 'while body changes its counter outside the proved back-edge')
            start = ir.Var(counter)
            limit = self.invariant_loop_limit(limit, [*condition, body], self.snapshots)
            bound = progress.trips(start, limit, minimum=int(posttest))
        count = self.temp()
        if posttest and progress is None:
            # First body precedes any test, even when the entry counter already
            # exceeds the cutoff. The actual post-test controls later bodies.
            bound = ir.Lit(1)
        capture = ir.Assign(count, ir.Op('ite', ir.Var(live), bound, ir.Lit(0)))
        if progress is not None:
            valid = ir.Op('lor', ir.Op('not', ir.Var(live)), progress.in_domain(start, capture.expr))
            self.control_domains[id(capture)] = (valid, Refusal('rtl-source-ir-loop-domain-unproved',
                'while ranking needs nonwrapping, order-preserving source conversions', span=self.span(node)))
        activation = ir.If(ir.Var(live), self.mark('loop-activation', node, 'nonzero'),
                           self.mark('loop-activation', node, 'zero'))
        iteration = ir.If(ir.Var(live), self.loop_iteration(body, control, condition), ir.Skip())
        initial = [ir.Assign(live, ir.Lit(True))] if posttest else condition
        return _sequence([entry, *initial, capture, activation, *self.loop_start(control),
                          ir.For(self.temp(), ir.Var(count), iteration)])

    def build(self, document, *, defer_domains=False):
        node = document["design"]
        for part in self.context.key:
            node = node[part]
            if isinstance(node, str):
                node = self.symbols[node.split(" ", 1)[0]]
        self.frames.append({})
        if node["kind"] == "ContinuousAssign":
            commands, _ = self.expression(node["assignment"])
            body = _sequence([self.mark("location", node["assignment"], "entry"), *commands])
        elif node["kind"] == "Net":
            commands, value = self.expression(node["initializer"])
            target = {**node, "kind": "NamedValue", "symbol": str(node["addr"])}
            commands.extend(self.store(target, value, deferred=False))
            coordinates = (self.context.process_start, self.span(node["initializer"])[1])
            body = _sequence([self.mark_at("location", coordinates, "entry"), *commands])
        else:
            body_node = node["body"]
            if body_node["kind"] == "Timed":
                # Only the process's entry event is consumed by the scheduler.
                # A nested wait/delay suspends execution and is not an entry.
                timing = body_node["timing"]
                if timing["kind"] not in {"SignalEvent", "EventList", "ImplicitEvent"}:
                    return self.unsupported(body_node, "process timing needs a resumable activation")
                body_node = body_node["stmt"]
            body = self.statement(body_node)
        return self.finish(body, defer_domains=defer_domains)

    def finish(self, body, *, defer_domains=False, implicit_reads=()):
        initialization = []
        for write in self.deferred.values():
            initialization.extend(write.initialize(self.temp))
        state = list(dict.fromkeys(self.storage.values()))
        outputs = [name for write in self.deferred.values() for name in write.registers]
        locals_ = [name for name in self.types if name not in {*state, *outputs}]
        procedure = ir.Procedure(state, [], locals_, outputs, _sequence([*initialization, body]), types=self.types)
        unresolved, domain_checks = None, ()
        if self.access_bounds or self.divisor_bounds or self.control_domains or self.defined.obligations:
            unresolved = self.domain_budget.prove(procedure, {name: self.ranges[name] for name in state},
                self.access_bounds, divisors=self.divisor_bounds,
                predicates={**self.control_domains, **self.defined.obligations}, defer=defer_domains)
        if unresolved:
            marks = {identity: (condition, f"@source-domain:{self.namespace}{index}")
                     for index, (identity, (condition, _cause)) in enumerate(unresolved.items())}
            domain_checks = tuple((marks[identity][1], cause)
                                  for identity, (_condition, cause) in unresolved.items())
            # Stop at the first undefined operation. Later totalized values or
            # failure to settle must not hide an invalid native prefix.
            body = _with_domain_marks(body, marks, terminal=True)
            procedure = ir.Procedure(state, [], locals_, outputs, _sequence([*initialization, body]),
                                     types=self.types)
        elif self.defined.private:
            body = self.defined.erase(body)
            self.types = {name: kind for name, kind in self.types.items() if name not in self.defined.private}
            locals_ = [name for name in locals_ if name not in self.defined.private]
            procedure = ir.Procedure(state, [], locals_, outputs, _sequence([*initialization, body]),
                                     types=self.types)
        body = fold_literal_copies(body, locals_)
        body, occurrences = coalesce_inactive_occurrences(body, self.occurrences)
        domain_checks += tuple(self.construction_failures.items())
        procedure = ir.Procedure(state, [], locals_, outputs, _sequence([*initialization, body]),
                                 types=self.types)
        produced = {command.goal for command in ir.walk(procedure.body)
                    if isinstance(command, (ir.Mark, ir.Fail))}
        missing = ({mark for mark, _, _ in occurrences}
                   | {mark for mark, _cause in domain_checks}) - produced
        if missing:
            raise Refused(Refusal("rtl-source-ir-occurrences-missing",
                                  "source declarations have no symbolic event producer",
                                  occurrences=tuple(sorted(missing))))
        procedure.state_ranges = {name: self.ranges[name] for name in state}
        return SourceProcedure(self.context, procedure, tuple((name, identity) for identity, name in self.storage.items()),
            tuple(self.deferred.values()), occurrences, body,
            tuple((name, *self.write_masks[name]) for name in state if name in self.write_masks),
            domain_checks, implicit_reads)


def lower_source_process(source, document, plan, context, *, filename):
    """Consume one declaration context; do not enumerate or prune a new catalogue."""
    return SourceCompiler(source, document, plan, filename=filename).lower(context)
