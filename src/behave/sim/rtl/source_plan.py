"""One immutable source-declaration/origin plan for RTL preparation.

This is a preparation boundary, not a Driver capability. The caller must obtain
slang's concrete syntax tree inside the RTL tool judge. Compiler annotations and
native callbacks consume the same origins; neither changes the goal catalogue.
Typed preparation additionally selects the runtime source closure and preserves
pre-synthesis schema declarations. Elaborated occurrence/context binding and
native transport remain separate gates; new declarations do not enable them.
"""

import hashlib
from dataclasses import dataclass, replace
from itertools import product

from ..core.source_rules import EMPTY_SOURCE_RULES
from .source_callbacks import callback_declaration
from .source_calls import HelperCalls, HelperContexts, HelperInvocation
from .source_continuous import ContinuousDriver, driver_edits
from .source_declarations import SourceDeclarations, resolve_source
from .source_deferred_events import DeferredNotifications, deferred_notifications, lifted_selector_edits
from .source_edits import Insertion, SourceEdits
from .source_expression_events import LOGICAL_CELLS, LogicalOperand, choice_function, choice_wrapper
from .source_failure_events import (
    CASE_FAILURE_FUNCTION,
    NO_EVENT,
    assertion_actions,
    assertion_function,
    assertion_wrapper,
    case_failure_function,
    post_argument_edits,
)
from .source_locations import SOURCE_ATTRIBUTES
from .source_loop_constants import loop_headers
from .source_loop_events import loop_entry_edits
from .source_loops import MAX_EVENTS, LoopProofs, StaticLoop, invocation_index
from .source_operations import operation_functions, operation_tokens
from .source_pending import blocking_notifications
from .source_read_events import read_function, read_wrapper
from .source_read_goals import READ_GOAL_PACKAGE, read_class, read_package
from .source_scheduling import PendingActivations, implicit_process, process_wait_edits, sensitivities_from_document
from .source_scopes import GeneratedScopes, generated_scopes
from .source_statement_events import (
    RESUME_DECLARATION,
    block_entry,
    entry_function,
    for_initializer_entry,
    initializer_entries,
)
from .source_syntax import LOOP_STATEMENTS as _LOOPS
from .source_syntax import children as _children
from .source_syntax import concrete_token
from .source_syntax import walk as _walk
from .source_write_index import SourceWriteIndex

VERSION = "rtl-source-plan/95"
ORIGIN_ATTRIBUTE = "behave_control_origin"
CALLBACK = "__behave_native_event_hit"
_PROCESSES = {"AlwaysBlock", "AlwaysFFBlock", "AlwaysCombBlock", "AlwaysLatchBlock",
              "ImmediateAssertionMember"}
_CONTROLS = _PROCESSES | {"InitialBlock", "ConditionalStatement", "CaseStatement"}


def _token(node):
    trivia, text = concrete_token(node)
    if node.get("kind") == "Identifier":
        name = node["text"].lstrip("\\")
        if name == ORIGIN_ATTRIBUTE or name.startswith("__behave_native_event_"):
            raise ValueError("the source uses the observer's reserved namespace")
    return trivia, text


def _signal_control(node):
    """An explicit signal-event wait, whose original syntax stays intact."""
    if node.get("kind") == "EventControl":
        # The bare @name spelling has its own CST node. Its bound event is the
        # same as @(name); observation still executes only inside the body,
        # and the compiler owns sensitivity and symbolic-scheduler support.
        return True
    if node.get("kind") != "EventControlWithExpression":
        return False
    signals, pending = [], [node]
    while pending:
        item = pending.pop()
        if type(item) is dict and item.get("kind") == "SignalEventExpression":
            signals.append(item)
        else:
            pending.extend(_children(item))
    return bool(signals) and all(signal.get("edge", {}).get("kind") in
                                 {None, "PosEdgeKeyword", "NegEdgeKeyword", "EdgeKeyword"}
                                 for signal in signals)


@dataclass(frozen=True, slots=True)
class SourceChoice:
    """One ordered source arm and its exact compiler provenance anchor.

    Yosys records case items at the preceding syntax boundary (an empty grammar
    production), not at their statement. These anchors come from the CST;
    consumers require exact equality and never search for a nearby source line.
    """
    key: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class ControlOrigin:
    """Source extent plus the locked compiler's exact provenance anchor."""

    key: str
    kind: str
    start: int
    end: int
    choices: tuple[SourceChoice, ...]
    compiler_span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class SourceEvent:
    ordinal: int
    origin: str
    role: str
    start: int
    end: int
    choice: str | None
    ancestors: tuple[int, ...]
    contexts: tuple[tuple[str, int, int], ...]
    calls: tuple[tuple[int, int], ...] = ()
    logical_path: tuple[LogicalOperand, ...] = ()


@dataclass(frozen=True, slots=True)
class EventInvocation:
    source_event: int
    iterations: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ControlVisit:
    origin: str
    process: str
    iterations: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DeclaredEvent:
    """A native producer for one source-declared outcome, before projection."""

    site: int
    outcome: str
    event: int


@dataclass(frozen=True, slots=True)
class SourcePlan:
    source_sha256: str
    controls: tuple[ControlOrigin, ...]
    events: tuple[SourceEvent, ...]
    compiler: SourceEdits
    _native: SourceEdits
    native_issues: tuple[str, ...]
    loops: tuple[StaticLoop, ...]
    invocations: tuple[EventInvocation, ...]
    visits: tuple[ControlVisit, ...]
    calls: tuple[HelperInvocation, ...]
    declarations: SourceDeclarations | None = None
    declared_events: tuple[DeclaredEvent, ...] = ()
    event_waits: bool = False
    binding_issues: tuple[str, ...] = ()
    callback_scopes: tuple[int, ...] = ()
    enumerate_iterations: bool = True
    loop_proofs: LoopProofs | None = None
    unit_callback_scopes: tuple[int, ...] = ()
    read_definitions: tuple[str, ...] = ()

    def require_compiler_binding(self):
        """Native attribution does not prove the identity of RTLIL copies."""
        self.native()
        if not self.enumerate_iterations:
            raise ValueError("source occurrence coordinates are not a finite iteration encoding")
        if any(len({values for _, values in loop.variants}) > 1 for loop in self.loops):
            raise ValueError("RTLIL copy binding needs per-instance loop ranges, not a shared native grid")
        if self.binding_issues:
            raise ValueError(self.binding_issues[0])

    def native(self, *, scoped: bool = False) -> SourceEdits:
        """Render source-bound probes and the selected process scheduling plan.

        Native compilation requires native_events.VERILATOR_FLAGS. Compiler
        event-wait plans additionally require the simulator's timing support;
        legacy plans retain typed read anchors and their existing restrictions.
        Scheduled interval support is a separate admission condition.
        """
        if self.native_issues:
            raise ValueError("native source plan is incomplete: " + self.native_issues[0])
        # Only transport changes here. The event sites and scope arguments are
        # identical; construction binds each original elaboration scope once.
        read_helpers = ((Insertion(0, read_package(self.read_definitions,
            callback_declaration(CALLBACK, scoped=scoped, owns_scope=False))),) if self.read_definitions else ())
        insertions = (*read_helpers, *self._native.insertions, *(Insertion(offset,
            callback_declaration(CALLBACK, scoped=scoped)) for offset in self.callback_scopes),
            *(Insertion(offset, callback_declaration(CALLBACK, scoped=scoped, owns_scope=False))
              for offset in self.unit_callback_scopes))
        return SourceEdits(self._native.source, tuple(sorted(insertions, key=lambda edit: edit.offset)),
                           self._native.replacements)


def plan_source(source: str, document: dict, *, semantic_document: dict | None = None,
                sensitivity_document: list[dict] | None = None,
                filename: str = "design.sv", enumerate_iterations: bool = True,
                source_rules=EMPTY_SOURCE_RULES) -> SourcePlan:
    """Bind both insertion streams to one exact, single-source concrete tree.

    Offsets are UTF-8 bytes. The resulting plan retains no mutable CST objects.
    Macro expansions and unknown preprocessor provenance cannot round-trip and
    are refused. Helper invocations carry an observer-only input identifying
    their source call and enclosing loop contexts. Native recursive backedges
    revisit automatic-frame contexts; compiler-copy/encoding support is separate.
    Production supplies the selected typed document; CST-only callers are
    parser/occurrence diagnostics, not a complete source declaration path.
    Native source observation sets enumerate_iterations=False: one callback ID
    per source/call occurrence, reused on every execution. Static iteration
    coordinates are an explicit encoding requirement, not an observation gate.
    """
    if type(source) is not str or type(document) is not dict:
        raise TypeError("a source plan needs text and a concrete-syntax document")
    source_bytes = source.encode("utf-8")
    trees = document.get("syntaxTrees")
    if type(trees) is not list or len(trees) != 1 or type(trees[0]) is not dict:
        raise ValueError("source observation needs exactly one concrete source tree")
    root = trees[0].get("root")
    if type(root) is not dict:
        raise ValueError("missing concrete source root")
    spans, tokens, innermost, position = {}, [], {}, 0
    escaped_ends = set()

    def locate(node):
        nonlocal position
        start = position
        if type(node) is dict and node.get("kind") == "AttributeSpec" \
                and node.get("name", {}).get("text", "").lstrip("\\") in SOURCE_ATTRIBUTES | {"scopename"}:
            # These attributes are emitted by the compiler and used to bind
            # source events. A user attribute must not impersonate that origin.
            raise ValueError("source attributes cannot override compiler provenance")
        if type(node) is dict and "text" in node:
            trivia, text = _token(node)
            tokens.append(trivia + text)
            start += len(trivia.encode("utf-8"))
            position += len((trivia + text).encode("utf-8"))
            if text.startswith("\\"):
                escaped_ends.add(position)
        else:
            children = _children(node)
            for child in children:
                locate(child)
            if children:
                start = spans[id(children[0])][0]
        spans[id(node)] = (start, position)
        if type(node) is dict and "kind" in node and "text" not in node:
            # Grammar wrappers can have the same span as their expression.
            # The innermost non-token node denotes the exact typed source term.
            innermost.setdefault((start, position), id(node))

    locate(root)
    if "".join(tokens) != source:
        raise ValueError("CST tokens and trivia do not reproduce the exact source")
    resolution = (None if semantic_document is None else
                  resolve_source(source, semantic_document, filename=filename, source_rules=source_rules))
    if source_rules and resolution is None:
        raise ValueError("source rules require the selected typed source document")
    declarations = None if resolution is None else resolution.declarations
    # Root execution locations are owned by their exact compiler process/net
    # origin. A typed expression can omit parentheses retained by its CST;
    # joining independently computed end offsets would lose a valid producer.
    root_locations = {}
    if declarations is not None:
        contexts = {context.key: context for context in declarations.contexts}
        for occurrence in declarations.occurrences:
            site = declarations.sites[occurrence.site]
            owner = contexts[occurrence.context]
            if site.schema == "location" and not occurrence.calls and site.start == owner.process_start:
                root_locations[owner.process_start] = site
    # Condition events need coordinates for the final failed test as well as
    # body iterations. A called helper's events belong to its call-site span.
    event_spans = (() if declarations is None else tuple(sorted(
                   {(site.start, site.end) for site in declarations.sites} |
                   {call for occurrence in declarations.occurrences for call in occurrence.calls})))
    def selected_loop_headers():
        if semantic_document is None or "loop_headers" not in semantic_document:
            return None
        headers = loop_headers(source, semantic_document, filename=filename)
        selected = {}
        for start, end, key in resolution.loop_keys:
            selected.setdefault((start, end), []).append(headers.get(key))
        return selected
    scheduling = (None if sensitivity_document is None else
                  sensitivities_from_document(sensitivity_document, filename=filename))
    pending_layout = PendingActivations.for_processes(() if scheduling is None else
                                                      (row.start for row in scheduling.values()))
    declared_controls = (None if declarations is None else
                         {(site.start, site.end) for site in declarations.sites
                          if site.schema == "control-choice"})
    declared_processes = None if declarations is None else set(declarations.process_starts)
    declared_calls = None if declarations is None else set(declarations.call_spans)
    frame_local_calls = frozenset() if declarations is None else frozenset(declarations.frame_local_calls)
    argument_bound_calls = frozenset() if declarations is None else frozenset(declarations.argument_bound_calls)
    loop_proofs = LoopProofs(spans, selected_loop_headers, frame_local_calls, event_spans)
    active_contexts = set() if declarations is None else {item.context for item in declarations.occurrences}
    declared_contexts = (set() if declarations is None else
                         {context.process_start for context in declarations.contexts
                          if context.key in active_contexts})
    boolean_nodes = {} if declarations is None else {
        innermost[(site.start, site.end)]: site for site in declarations.sites
        if site.schema == "boolean-expression"}
    operation_sites = {} if declarations is None else {
        (site.start, site.end): site for site in declarations.sites if site.schema == "operation-event"}
    operation_nodes = {} if resolution is None else {
        innermost[(first, last)]: captures for first, last, captures in resolution.operation_captures}
    operand_wrappers, operation_replacements, operation_definitions = {}, [], []
    sampled_sites = {} if declarations is None else {
        (site.start, site.end): site for site in declarations.sites_for("value-observation")}
    sampled_nodes = {} if resolution is None else {
        innermost[(read.start, read.end)]: read for read in resolution.sampled_reads}
    read_definitions = []
    failure_sites = {} if declarations is None else {
        (site.start, site.end): site for site in declarations.failures}
    location_sites = {} if declarations is None else {
        (site.start, site.end): site for site in declarations.sites if site.schema == "location"}
    location_nodes = {}
    read_nodes = {} if declarations is None or scheduling is not None else {
        innermost[(read.start, read.end)]: read for read in declarations.dependency_reads}
    generated = (GeneratedScopes() if semantic_document is None else generated_scopes(
        source, root, spans, semantic_document, filename=filename))
    controls, by_node, event_scopes, event_nodes = [], {}, {}, set()
    loop_scopes = {}

    def choices_of(node):
        kind = node["kind"]
        if kind == "ConditionalStatement":
            clause = node.get("elseClause")
            false = clause["elseKeyword"] if clause else node
            return (SourceChoice("true", *spans[id(node["predicate"])]),
                    SourceChoice("false", *spans[id(false)]))
        if kind == "CaseStatement":
            choices = []
            boundary = spans[id(node["closeParen"])][1]
            for index, item in enumerate(node["items"]):
                if item["kind"] not in {"StandardCaseItem", "DefaultCaseItem"}:
                    raise ValueError("unsupported case item syntax")
                key = "default" if item["kind"] == "DefaultCaseItem" else f"arm_{index}"
                choices.append(SourceChoice(key, boundary, boundary))
                boundary = spans[id(item)][1]
            if not any(choice.key == "default" for choice in choices):
                choices.append(SourceChoice("no-match", *spans[id(node)]))
            return tuple(choices)
        return ()

    def catalogue(node, event_scope=None, scopes=()):
        if id(node) in generated.implicit or (
                type(node) is dict and node.get("kind") in {"ModuleDeclaration", "PackageDeclaration", "GenerateBlock"}):
            event_scope = node
        if type(node) is dict and node.get("kind") in {
                "ModuleDeclaration", "FunctionDeclaration", "TaskDeclaration",
                "SequentialBlockStatement"}:
            scopes = (*scopes, node)
        if type(node) is dict and node.get("kind") in _LOOPS:
            loop_scopes[id(node)] = scopes
        event_scopes[id(node)] = event_scope
        if type(node) is dict and node.get("kind", "").endswith("Statement") \
                and spans[id(node)] in location_sites:
            location_nodes[id(node)] = location_sites[spans[id(node)]]
            event_nodes.add(id(node))
        if type(node) is dict and node.get("kind") == "Declarator" \
                and spans[id(node["name"])] in location_sites:
            event_nodes.add(id(node))
        if type(node) is dict and node.get("kind") in _LOOPS | {
                "ConditionalStatement", "CaseStatement", "ConditionalExpression"}:
            event_nodes.add(id(node))
        if id(node) in boolean_nodes:
            event_nodes.add(id(node))
        if spans[id(node)] in operation_sites:
            event_nodes.add(id(node))
        if spans[id(node)] in failure_sites:
            event_nodes.add(id(node))
        if type(node) is dict and node.get("kind") in _CONTROLS:
            compiler_span = spans[id(node)]
            if node["kind"] == "AlwaysCombBlock":
                # Yosys anchors an implicit combinational process at the empty
                # event-control production immediately after its keyword.
                boundary = spans[id(node["keyword"])][1]
                compiler_span = (boundary, boundary)
            elif node["kind"] == "CaseStatement":
                # Yosys's case range begins at case/casez/casex, excluding the
                # optional unique/unique0/priority qualifier.
                compiler_span = (spans[id(node["caseKeyword"])][0], spans[id(node)][1])
            origin = ControlOrigin(f"control_{len(controls)}", node["kind"],
                                   *spans[id(node)], choices_of(node), compiler_span)
            controls.append(origin)
            by_node[id(node)] = origin
        for child in _children(node):
            catalogue(child, event_scope, scopes)

    catalogue(root, root if root.get("kind") == "CompilationUnit" else None)
    # Only a dependency export that actually includes task bodies can qualify
    # their hidden reads/writes. CST-only plans and always @* keep their narrower
    # argument-bound rule; declarations themselves do not change with support.
    implicit_origins, task_schedule_origins, task_frame_inputs = set(), set(), {}
    for node in _walk(root):
        if type(node) is not dict or node.get("kind") not in _PROCESSES:
            continue
        origin = by_node[id(node)].key
        if implicit_process(node):
            implicit_origins.add(origin)
        process = None if scheduling is None else scheduling.get(spans[id(node)])
        if process is not None:
            if process.inlined_task_dependencies:
                task_schedule_origins.add(origin)
            task_frame_inputs[origin] = process.task_frame_inputs
    helpers = HelperCalls(root, event_nodes=event_nodes, spans=spans, runtime_calls=declared_calls,
                          resolved_calls=() if resolution is None else resolution.helper_bindings,
                          retained_frame_calls=() if declarations is None else declarations.retained_frame_calls)
    helper_contexts = HelperContexts(helpers, spans, CALLBACK)
    expanded_calls, helper_stack = {}, []
    annotations = tuple(Insertion(origin.start, f'(* {ORIGIN_ATTRIBUTE} = "{origin.key}" *) ')
                        for origin in controls)
    compiler = SourceEdits(source, tuple(sorted(annotations, key=lambda edit: edit.offset)))
    edits, events, issues, invocations = [], [], list(
        declarations.read_issues if declarations and scheduling is None else ()), []
    if declarations is not None:
        # Typed declarations also include implicit processes (for example a
        # module-level deferred assertion) without a procedural CST wrapper.
        # Admission belongs to that selected closure, not visitor reachability.
        issues.extend(dict.fromkeys(failure.issue for failure in declarations.failures
                                    if failure.issue))
    if resolution is not None:
        issues.extend(resolution.operation_issues)
    binding_issues = []
    scheduling_openings, scheduling_insertions, scheduling_replacements = [], [], []
    continuous_drivers = []
    scheduled_processes = set()
    loops = {}
    event_stack = []
    context_stack = []
    condition_contexts, condition_loops = set(), {}
    logical_stack = []
    observed = set()
    wrapped = set()
    expression_scopes = {}
    assertion_scopes = {}
    failure_definitions, failure_replacements = [], []
    initializer_scopes = {}
    callback_scopes = set()
    unit_callback_scopes = set()
    initializer_replacements = []
    anchored_reads = set()
    write_notifications = {}
    write_headers = {}
    write_declarations = {}
    deferred = DeferredNotifications()
    if scheduling is not None and declarations is not None:
        guarded = {(row.start, row.end) for row in scheduling.values()
                   if row.triggers and row.kind in {"ContinuousAssign", "NetInitializer"}}
        guarded.update(spans[id(node)] for node in _walk(root)
                       if type(node) is dict and node.get("kind") in _PROCESSES
                       and spans[id(node)] in scheduling
                       and (implicit_process(node) or scheduling[spans[id(node)]].stationary_value_events)
                       and scheduling[spans[id(node)]].triggers)
        try:
            write_index = SourceWriteIndex(source, semantic_document, declarations,
                                          sensitivity_document, filename=filename)
            notifications = blocking_notifications(source, semantic_document, declarations,
                sensitivity_document, guarded, filename=filename, pending_layout=pending_layout, index=write_index)
            write_notifications, write_headers, write_declarations = (
                notifications.statements, notifications.headers, notifications.declarations)
            deferred = deferred_notifications(write_index, sensitivity_document, guarded)
            for span, (before, after) in deferred.statements.items():
                outer_before, outer_after = write_notifications.get(span, ("", ""))
                write_notifications[span] = outer_before + before, after + outer_after
        except ValueError as error:
            issues.append(str(error))
    captured_nodes = {innermost[span]: name for span, name in deferred.captures.items()}

    def insert(offset, text):
        # An escaped HDL identifier ends at whitespace, not punctuation. An
        # observer inserted before its original trailing trivia must retain
        # that token boundary (e.g. an NBA capture around \\cell.D ).
        if offset in escaped_ends:
            text = " " + text
        edits.append(Insertion(offset, text))

    def scope_head(node):
        """Observer declarations live in their original elaboration scope."""
        if node.get("kind") == "CompilationUnit":
            return spans[id(node)][0]
        if id(node) in generated.implicit:
            return spans[id(node)][0]
        header = node["header"] if node["kind"] in {"ModuleDeclaration", "PackageDeclaration"} else node.get("beginName", node["begin"])
        return spans[id(header)][1]

    def event(node, origin, role, choice=None, *, expression=False):
        """Share the lexical callback, never the invocation's hit bit."""
        site = (id(node), origin.key, role, choice)
        index = event_invocation(node, origin, role, choice, expression=True)
        return helper_contexts.event(site, index, expression=expression)

    def event_invocation(node, origin, role, choice=None, *, expression=False, source_span=None):
        entry = SourceEvent(len(events), origin.key, role, *(source_span or spans[id(node)]), choice,
                            tuple(event_stack), tuple(context_stack), helper_contexts.path,
                            tuple(logical_stack))
        events.append(entry)
        if not enumerate_iterations:
            first = len(invocations)
            if first >= MAX_EVENTS:
                raise ValueError("native source occurrences exceed the collector storage bound")
            invocations.append(EventInvocation(entry.ordinal, ()))
            return str(first) if expression else f" {CALLBACK}({first}, {helper_contexts.scope}); "
        contexts = [(condition_loops if context in condition_contexts else loops).get(context)
                    for context in context_stack]
        if any(loop is None for loop in contexts):
            return "32'hffffffff" if expression else f" {CALLBACK}(32'hffffffff, {helper_contexts.scope}); "
        count = 1
        for loop in contexts:
            count *= len(loop.values)
        if len(invocations) + count > MAX_EVENTS:
            raise ValueError("native loop expansion exceeds the collector storage bound")
        first = len(invocations)
        invocations.extend(EventInvocation(entry.ordinal, tuple(values))
                           for values in product(*(loop.values for loop in contexts)))
        if not contexts:
            return str(first) if expression else f" {CALLBACK}({first}, {helper_contexts.scope}); "
        # Reading counters cannot repeat a source condition or alter its data.
        # Keep the same invocation IDs without a product-sized generated case.
        index = invocation_index(first, contexts, tuple(helper_contexts.counter(loop) for loop in contexts))
        return index if expression else f" {CALLBACK}({index}, {helper_contexts.scope}); "

    def wrap(node, origin, role, choice=None):
        start, end = spans[id(node)]
        block = node.get("kind") == "SequentialBlockStatement"
        callback = event(node, origin, role, choice, expression=block)
        key = (id(node), origin.key, role, choice)
        first = key not in wrapped
        wrapped.add(key)
        if first:
            if block:
                edits.append(block_entry(node, spans, callback, ordinal=events[-1].ordinal,
                    observed=helper_contexts.enabled, scope=helper_contexts.scope, hit=CALLBACK))
                scope = event_scopes[id(node)]
                initializer_scopes[id(scope)] = scope
            else:
                insert(start, " begin " + callback)
        event_stack.append(events[-1].ordinal)
        try:
            visit(node, origin)
        finally:
            event_stack.pop()
        if first and not block:
            insert(end, " end ")

    def visit(node, active=None, construction=False, *, reads_only=False):
        argument = operand_wrappers.get(id(node))
        if argument is not None:
            insert(spans[id(node)][0], argument[0])
        capture = captured_nodes.get(id(node)) if active is not None and not construction else None
        capture_key = (id(node), "deferred-value")
        capture_first = capture is not None and capture_key not in wrapped
        if capture_first:
            wrapped.add(capture_key)
            insert(spans[id(node)][0], f"({capture}=(")
        read = read_nodes.get(id(node)) if active is not None and not construction else None
        key = (id(node), "dependency-read")
        first = read is not None and key not in wrapped
        if first:
            wrapped.add(key)
            start, end = read.start, read.end
            before, after = read_wrapper(read.type_expression)
            insert(start, before)
            anchored_reads.add(read)
        sampled = (sampled_nodes.get(id(node)) if active is not None
                   and not construction and not reads_only else None)
        sampled_first = False
        if sampled is not None:
            site = sampled_sites[(sampled.start, sampled.end)]
            origin = ControlOrigin(f"read_{site.start}", "Read", site.start, site.end,
                                   (), (site.start, site.end))
            indices = [event(node, origin, "read", rule.name, expression=True) for rule in site.read_rules]
            sample_key = (id(node), "sampled-read")
            sampled_first = sample_key not in wrapped
            if sampled_first:
                wrapped.add(sample_key)
                name = f"read_{site.start}_{site.end}"
                read_definitions.append(read_class(name, site.read_rules, CALLBACK))
                insert(site.start, f"{READ_GOAL_PACKAGE}::{name}#(type({sampled.type_expression}))::value(")
        if reads_only:
            for child in _children(node):
                visit(child, active, reads_only=True)
        else:
            visit_observed(node, active, construction)
        if sampled_first:
            insert(sampled.end, ", " + ", ".join((helper_contexts.scope, helper_contexts.enabled, *indices)) + ")")
        if first:
            insert(end, after)
        if capture_first:
            insert(spans[id(node)][1], "))")
        if argument is not None:
            insert(spans[id(node)][1], argument[1])

    def location_event(node, location, *, expression=False):
        origin = ControlOrigin(f"location_{location.start}", "Location",
                               location.start, location.end, (), (location.start, location.end))
        return event(node, origin, "location", "entry", expression=expression)

    def visit_observed(node, active=None, construction=False):
        location = location_nodes.get(id(node)) if active is not None and not construction else None
        notification = (None if location is None else
                        write_notifications.get((location.start, location.end)))
        notify_key = (id(node), "pending-write")
        notify = notification is not None and notify_key not in wrapped
        if notify:
            wrapped.add(notify_key)
            insert(location.start, notification[0])
        key = (id(node), "location")
        first = location is not None and key not in wrapped
        if location is not None:
            callback = location_event(node, location)
            if first:
                wrapped.add(key)
                insert(location.start, " begin " + callback)
        visit_expression(node, active, construction)
        if first:
            insert(location.end, " end ")
        if notify:
            insert(location.end, notification[1])

    def visit_expression(node, active=None, construction=False):
        site = boolean_nodes.get(id(node)) if active is not None and not construction else None
        if site is None:
            visit_operation(node, active, construction)
            return
        origin = ControlOrigin(f"boolean_{site.start}", "BooleanTerm", site.start, site.end,
                               (), (site.start, site.end))
        when_true = event(node, origin, "boolean-true", "true", expression=True)
        when_false = event(node, origin, "boolean-false", "false", expression=True)
        key = (id(node), origin.key, "boolean-term")
        first = key not in wrapped
        wrapped.add(key)
        if first:
            before, after = choice_wrapper(when_true, when_false, observed=helper_contexts.enabled,
                                           scope=helper_contexts.scope)
            insert(site.start, before)
            scope = event_scopes[id(node)]
            expression_scopes[id(scope)] = scope
        visit_operation(node, active, construction)
        if first:
            insert(site.end, after)

    def visit_operation(node, active=None, construction=False):
        captures = operation_nodes.get(id(node)) if active is not None and not construction else None
        if not captures:
            visit_body(node, active, construction)
            return
        try:
            capture, widths, variants, type_witnesses = captures.bind()
        except ValueError as error:
            issues.append(str(error))
            visit_body(node, active, construction)
            return
        start, end = spans[id(node)]
        site = operation_sites.get((start, end))
        rules = () if site is None else site.operation_rules
        indices = []
        if site is not None:
            origin = ControlOrigin(f"operation_{start}", "Operation", start, end, (), (start, end))
            indices = [event(node, origin, "operation", rule.goal.name, expression=True) for rule in rules]
        key = (id(node), "operation")
        first = key not in wrapped
        if first:
            wrapped.add(key)
            name = f"__behave_native_event_operation_{start}"
            # Resolve parameter expressions in the declaration's scope once.
            # A helper/procedure-local variable can shadow a module parameter;
            # operand casts refer only to hygienic constant names, not that
            # original spelling at the runtime callsite.
            width_names = tuple(f"{name}_width_{i}" for i in range(len(widths)))
            constants = "".join(f"localparam {key} = {value};\n"
                                for key, value in zip(width_names, widths))
            type_names = tuple(f"{name}_type_{i}" if witness else ""
                               for i, witness in enumerate(type_witnesses))
            constants += "".join(f"typedef type({width}'({witness})) {type_name};\n"
                                 for width, witness, type_name in zip(width_names, type_witnesses, type_names)
                                 if witness)
            opening, token, arguments = operation_tokens(node, spans, capture, name, widths=width_names,
                value_types=type_names, operand_witnesses=captures.operand_witnesses)
            operation_replacements.append(token)
            insert(start, opening)
            for operand, before, after in arguments:
                operand_wrappers[id(operand)] = before, after
            operation_definitions.append((event_scopes[id(node)], constants + operation_functions(
                capture, name, rules, CALLBACK, widths=width_names, variants=variants, value_types=type_names)))
        visit_body(node, active, construction)
        if first:
            for operand, _, _ in arguments:
                del operand_wrappers[id(operand)]
            extra = "" if not rules else ", " + ", ".join((helper_contexts.enabled, *indices))
            insert(end, f", {helper_contexts.scope}{extra})")

    def continuous_driver(node, right, target, after, kind):
        first, last = spans[id(node)]
        process = None if scheduling is None else scheduling.get((first, last))
        if process is not None:
            scheduled_processes.add((first, last))
        if first not in declared_contexts:
            return
        if process is None or process.kind != kind:
            issues.append("continuous assignment needs compiler sensitivity")
            return
        origin = ControlOrigin(f"continuous_{first}", kind, first, last, (), (first, last))
        controls.append(origin)
        observed.add(origin.key)
        # Both explicit assignments and net declaration assignments retain
        # their original driver. Only RHS evaluation moves into the same
        # compiler-scheduled event process, with original source coordinates.
        callback = event_invocation(node, origin, "continuous")
        event_stack.append(events[-1].ordinal)
        try:
            if location := root_locations.get(first):
                callback += event_invocation(node, origin, "location", "entry",
                                             source_span=(location.start, location.end))
            visit(right, origin)
        finally:
            event_stack.pop()
        left, end = spans[id(target)]
        continuous_drivers.append(ContinuousDriver(
            first, spans[id(right)], after, source_bytes[left:end].decode("utf-8"),
            process.triggers, callback, process.instances))

    def visit_body(node, active=None, construction=False):
        if type(node) is not dict or "text" in node:
            for child in _children(node):
                visit(child, active, construction)
            return
        kind = node.get("kind")
        if kind in {"FunctionDeclaration", "TaskDeclaration"}:
            return  # Instrument only through its proved runtime call below.
        if kind == "InitialBlock":
            construction = True
        if construction:
            if id(node) in by_node:
                observed.add(by_node[id(node)].key)
            for child in _children(node):
                visit(child, construction=True)
            return
        if kind in {"ModuleDeclaration", "PackageDeclaration", "GenerateBlock"} or id(node) in generated.implicit:
            callback_scopes.add(scope_head(node))
        if kind == "DataDeclaration" and active is not None:
            callbacks = {}
            for item in node["declarators"]:
                if item.get("kind") == "Comma":
                    continue
                location = location_sites.get(spans[id(item["name"])])
                if location is not None:
                    origin = ControlOrigin(f"location_{location.start}", "Initializer",
                        location.start, location.end, (), (location.start, location.end))
                    callbacks[(location.start, location.end)] = event(
                        item["name"], origin, "location", "entry", expression=True)
            key = (id(node), "initializer-entries")
            if callbacks and key not in wrapped:
                wrapped.add(key)
                added, replaced = initializer_entries(source, node, spans, callbacks,
                                                       observed=helper_contexts.enabled, scope=helper_contexts.scope)
                edits.extend(added)
                initializer_replacements.extend(replaced)
                scope = event_scopes[id(node)]
                initializer_scopes[id(scope)] = scope
            for child in _children(node):
                visit(child, active)
            return
        if kind == "ForVariableDeclaration" and active is not None:
            declarator = node["declarator"]
            location = location_sites.get(spans[id(declarator["name"])])
            if location is not None:
                origin = ControlOrigin(f"location_{location.start}", "Initializer",
                    location.start, location.end, (), (location.start, location.end))
                callback = event(declarator["name"], origin, "location", "entry", expression=True)
                key = (id(node), "initializer-entry")
                if key not in wrapped:
                    wrapped.add(key)
                    edits.append(for_initializer_entry(declarator, spans, callback,
                                                       observed=helper_contexts.enabled, scope=helper_contexts.scope))
                    scope = event_scopes[id(node)]
                    initializer_scopes[id(scope)] = scope
            for child in _children(node):
                visit(child, active)
            return
        if kind == "ContinuousAssign":
            for assignment in node["assignments"]:
                if assignment.get("kind") == "Comma":
                    continue
                continuous_driver(assignment, assignment["right"], assignment["left"],
                                  spans[id(node)][1], "ContinuousAssign")
            return
        if kind == "NetDeclaration":
            for declarator in node["declarators"]:
                if declarator.get("initializer"):
                    continuous_driver(declarator, declarator["initializer"]["expr"], declarator["name"],
                                      spans[id(node)][1], "NetInitializer")
            return
        failure_node = (node.get("expr") if kind == "ExpressionStatement" else
                        node if kind == "ImmediateAssertStatement" else None)
        failure = failure_sites.get(spans[id(failure_node)]) if failure_node is not None else None
        if failure is not None and active is not None:
            if failure.issue:
                return
            origin = ControlOrigin(f"failure_{failure.start}", kind, failure.start, failure.end,
                                   (), (failure.start, failure.end))
            callback = event(failure_node, origin, "failure", "failure", expression=failure.kind == "assert")
            key = (id(node), "failure")
            closing = None
            first = key not in wrapped
            if first:
                wrapped.add(key)
                if failure.kind == "assert":
                    before, _ = assertion_wrapper(callback, deferred=failure.deferred)
                    start, end = spans[id(node["expr"]["expression"])]
                    insert(start, before)
                    scope = event_scopes[id(node)]
                    assertion_scopes[id(scope), failure.deferred] = scope
                else:
                    if failure.post_arguments:
                        added, replaced, definition = post_argument_edits(
                            source, node, spans, failure.kind, callback)
                        # Argument probes are inserted before the closing
                        # callback, including nested helper context arguments.
                        edits.extend(added[:-1])
                        closing = (added[-1].offset, added[-1].text)
                        failure_replacements.extend(replaced)
                        failure_definitions.append((event_scopes[id(node)], definition))
                    else:
                        start, end = spans[id(node)]
                        insert(start, " begin " + callback)
                        closing = (end, " end ")
            if failure.kind == "assert":
                visit(node["expr"], active)
                entries = {}
                action_failures = {}
                for outcome, action in assertion_actions(node):
                    location = location_nodes.get(id(action))
                    if (failure.deferred or action["kind"] == "EmptyStatement") and location is not None:
                        # Preserve the source action, including its null form.
                        # Observe entry after the once-evaluated condition;
                        # never introduce a new block around an empty action.
                        entries[outcome] = location_event(action, location, expression=True)
                        if failure.deferred:
                            # The compiler captures arguments at encounter and
                            # executes the action later. Do not turn its single
                            # call into a block with an early location callback.
                            action_failure = failure_sites.get(spans.get(id(action.get("expr"))))
                            if action_failure is not None:
                                # Preserve the original single diagnostic call.
                                # Its arguments (and their source probes) run at
                                # encounter; the call's failure event belongs to
                                # dispatch, after those arguments completed.
                                if action_failure.issue:
                                    issues.append(action_failure.issue)
                                action_origin = ControlOrigin(
                                    f"failure_{action_failure.start}", action["kind"],
                                    action_failure.start, action_failure.end, (),
                                    (action_failure.start, action_failure.end))
                                action_failures[outcome] = event(action["expr"], action_origin,
                                    "failure", "failure", expression=True)
                                for child in _children(action["expr"]):
                                    visit(child, active)
                            else:
                                visit_expression(action, active)
                    else:
                        visit(action, active)
                if first:
                    _, after = assertion_wrapper(callback, observed=helper_contexts.enabled,
                        scope=helper_contexts.scope, pass_entry=entries.get(True, NO_EVENT),
                        fail_entry=entries.get(False, NO_EVENT), deferred=failure.deferred,
                        pass_failure=action_failures.get(True, NO_EVENT),
                        fail_failure=action_failures.get(False, NO_EVENT))
                    closing = (end, after)
            else:
                # Keep the original argument list and all nested probes.
                for child in _children(node):
                    visit(child, active)
            if closing is not None:
                insert(*closing)
            return
        if kind in LOGICAL_CELLS and active is not None:
            for child, port in (("left", "A"), ("right", "B")):
                logical_stack.append(LogicalOperand(*spans[id(node)], LOGICAL_CELLS[kind], port))
                try:
                    visit(node[child], active)
                finally:
                    logical_stack.pop()
            return
        if helpers.is_call(node) and active is not None:
            if declared_calls is not None and spans[id(node)] not in declared_calls:
                return  # For example, a helper named only in a type-query operand.
            # Arguments execute in the caller's control context, not in a
            # conditional arm inside the callee.
            visit(node.get("arguments"), active)
            try:
                definition = helpers.resolve(node)
                if definition is not None:
                    scope = event_scopes[id(definition)]
                    if scope is not None and scope.get("kind") == "CompilationUnit":
                        unit_callback_scopes.add(scope_head(scope))
                    if definition["kind"] == "TaskDeclaration" and spans[id(node)] not in argument_bound_calls:
                        unqualified = {events[index].origin for index in event_stack
                            if events[index].role == "process" and events[index].origin in implicit_origins
                            and events[index].origin not in task_schedule_origins}
                        if unqualified:
                            retained = sorted({item for origin in unqualified
                                               for item in task_frame_inputs.get(origin, ())})
                            if retained:
                                raise ValueError("implicit task-call depends on retained helper state: "
                                                 + ", ".join(retained))
                            raise ValueError("implicit task-call sensitivity needs a qualified native schedule")
                    if id(definition) in helper_stack:
                        if enumerate_iterations:
                            raise ValueError("native helper occurrence contexts cannot be recursive in static iteration expansion")
                        index = helper_stack.index(id(definition))
                        # Typed declaration checks automatic lifetime across
                        # the cycle, including inherited module lifetime.
                        helper_contexts.reenter(node, index, typed=declarations is not None)
                        binding_issues.append("recursive source calls need a finite activation relation")
                        return
                    if enumerate_iterations and any(context not in loops for context in context_stack):
                        return  # Its loop already recorded a missing certificate.
                    if issue := helpers.compiler_context_issue(node):
                        binding_issues.append(issue)
                    expanded_calls[id(node)] = definition
                    helper_contexts.enter(node, definition,
                        tuple(loops[c] for c in context_stack) if enumerate_iterations else ())
                    helper_stack.append(id(definition))
                    try:
                        visit(definition.get("items", []), active)
                    finally:
                        helper_contexts.leave()
                        helper_stack.pop()
            except ValueError as error:
                issues.append(str(error))
            return
        if kind == "ConditionalExpression" and active is not None:
            if declared_controls is not None and spans[id(node)] not in declared_controls:
                return  # For example, a conditional in a type-only query.
            predicate = node["predicate"]
            parts = predicate.get("conditions", ())
            if len(parts) != 1 or set(parts[0]) != {"kind", "expr"}:
                issues.append("native expression choices require a single scalar predicate")
                return
            predicate_edits = len(edits)
            visit(predicate, active)
            origin = ControlOrigin(f"expression_{spans[id(node)][0]}", kind,
                                   *spans[id(node)], (), spans[id(node)])
            when_true = event(node, origin, "expression-true", "true", expression=True)
            true_event = events[-1].ordinal
            when_false = event(node, origin, "expression-false", "false", expression=True)
            false_event = events[-1].ordinal
            key = (id(node), origin.key, "expression-choice")
            if key not in wrapped:
                wrapped.add(key)
                before, after = choice_wrapper(when_true, when_false, observed=helper_contexts.enabled,
                                               scope=helper_contexts.scope)
                start, end = spans[id(predicate)]
                # Enclose already-planned predicate reads/Boolean events. At
                # an equal source boundary, appending both delimiters would
                # cross those wrappers instead of nesting them.
                edits.insert(predicate_edits, Insertion(start, before))
                insert(end, after)
                scope = event_scopes[id(node)]
                expression_scopes[id(scope)] = scope
            for branch, entry in (("left", true_event), ("right", false_event)):
                event_stack.append(entry)
                try:
                    visit(node[branch], active)
                finally:
                    event_stack.pop()
            return
        if kind in _LOOPS:
            context = (kind, *spans[id(node)])
            loop_proofs.add(node, loop_scopes[id(node)])
            entry_edits = ()
            if enumerate_iterations:
                try:
                    loop = loop_proofs.prove(spans[id(node)])
                    if any(loops[c].counter == loop.counter for c in context_stack
                           if c in loops and not helper_contexts.inherited(c)):
                        raise ValueError("native nested loop counters need distinct accessible names")
                    loops[context] = loop
                    if loop.condition_events:
                        condition_loops[context] = replace(loop, values=loop.condition_values)
                        binding_issues.append("compiler loop-condition events need terminating-test occurrence binding")
                except ValueError as error:
                    issues.append(str(error))
            if active is not None:
                # Entry belongs to the surrounding context, not one occurrence
                # per iteration of this loop. Preserve the header's evaluation.
                origin = ControlOrigin(f"loop_{context[1]}", kind, *spans[id(node)],
                                       (), spans[id(node)])
                zero = event(node, origin, "loop-zero", "zero")
                nonzero = event(node, origin, "loop-nonzero", "nonzero")
                key = (id(node), origin.key, "loop-entry")
                if key not in wrapped:
                    wrapped.add(key)
                    headers = write_headers.get(spans[id(node)], {})
                    for (start, end), (_, before, after) in headers.items():
                        insert(start, ", ".join(before) + ", ")
                        insert(end, ", " + ", ".join(after))
                    entry_edits = loop_entry_edits(
                        spans[id(node)], spans[id(node["statement"])],
                        zero=zero, nonzero=nonzero,
                        declarations=tuple(declaration for parts in headers.values()
                                           for declaration in parts[0]))
                    edits.extend(entry_edits[:2])
            # A for initializer executes before this loop has an iteration
            # coordinate. Only the body/header rechecks use that coordinate.
            initializers = node.get("initializers", ()) if kind == "ForLoopStatement" else ()
            for initializer in initializers:
                visit(initializer, active)
            context_stack.append(context)
            try:
                for child in _children(node):
                    if child is initializers:
                        continue
                    condition = child is node.get("stopExpr") and context in condition_loops
                    if condition:
                        condition_contexts.add(context)
                    try:
                        visit(child, active)
                    finally:
                        if condition:
                            condition_contexts.remove(context)
            finally:
                context_stack.pop()
            edits.extend(entry_edits[2:])
            return
        if kind in _PROCESSES:
            origin = by_node[id(node)]
            if declared_processes is not None and origin.start not in declared_processes:
                return
            if scheduling is not None:
                key = origin.start, origin.end
                process = scheduling.get(key)
                if process is None:
                    raise ValueError("selected process has no compiler sensitivity")
                added, replaced = process_wait_edits(node, spans, process,
                    pending=pending_layout.reference(process.start),
                    deferred=deferred.consumers.get(process.start, ()))
                # An implicit source process has no keyword to replace. Its
                # generated opening must precede body-entry insertions at the
                # same original byte; closing controls follow those insertions.
                scheduling_openings.extend(edit for edit in added if edit.offset == origin.start)
                scheduling_insertions.extend(edit for edit in added if edit.offset != origin.start)
                scheduling_replacements.extend(replaced)
                scheduled_processes.add(key)
            statement = node["statement"]
            body = None
            if kind in {"AlwaysCombBlock", "ImmediateAssertionMember"} or (
                    kind == "AlwaysLatchBlock" and scheduling is not None):
                body = statement
            elif kind in {"AlwaysBlock", "AlwaysFFBlock"} \
                    and statement.get("kind") == "TimingControlStatement":
                timing = statement["timingControl"]
                if _signal_control(timing) or (kind == "AlwaysBlock"
                                            and timing.get("kind") == "ImplicitEventControl"):
                    body = statement["statement"]
            if body is None:
                issues.append("process without a supported event control: " + origin.key)
                return
            observed.add(origin.key)
            # Observe actual activations, never synthesize a call per test slot.
            # Scheduled/native-symbolic interval support is a separate gate.
            wrap(body, origin, "process")
            return
        if active is not None and kind in {"ConditionalStatement", "CaseStatement"}:
            origin = by_node[id(node)]
            observed.add(origin.key)
            if kind == "ConditionalStatement":
                if set(node) - {"kind", "attributes", "ifKeyword", "openParen", "predicate", "closeParen",
                                "statement", "elseClause"}:
                    raise ValueError("unsupported conditional syntax")
                visit(node["predicate"], active)
                wrap(node["statement"], origin, "if-true", "true")
                clause = node.get("elseClause")
                if clause:
                    wrap(clause["clause"], origin, "if-false", "false")
                else:
                    callback = event(node, origin, "if-false", "false")
                    key = (id(node), "implicit-else")
                    if key not in wrapped:
                        insert(spans[id(node)][1], " else begin " + callback + " end ")
                        wrapped.add(key)
            else:
                if set(node) - {"kind", "attributes", "uniqueOrPriority", "caseKeyword", "openParen", "expr", "closeParen",
                                "items", "endcase"}:
                    raise ValueError("unsupported case syntax")
                visit(node["expr"], active)
                default = any(item["kind"] == "DefaultCaseItem" for item in node["items"])
                # Adding a default suppresses unique/priority's original no-match
                # check. Track the chosen arm instead, without reevaluating expr.
                matched = (f"__behave_native_event_case_{origin.start}"
                           if not default and "uniqueOrPriority" in node else None)
                failure = failure_sites.get(spans[id(node)])
                failure_callback = None
                if failure is not None:
                    failure_origin = ControlOrigin(f'failure_{failure.start}', kind,
                        failure.start, failure.end, (), (failure.start, failure.end))
                    failure_callback = event(node, failure_origin, 'failure', 'failure', expression=True)
                case_key = (id(node), 'case-observation')
                first_case = (matched is not None or failure is not None) and case_key not in wrapped
                first_match = first_case and matched is not None
                if first_case:
                    wrapped.add(case_key)
                    declaration = f'bit {matched}; {matched}=0; ' if matched else ''
                    insert(spans[id(node)][0], 'begin ' + declaration)
                for index, item in enumerate(node["items"]):
                    if item["kind"] not in {"StandardCaseItem", "DefaultCaseItem"}:
                        raise ValueError("unsupported case item syntax")
                    # Label callbacks belong inside the original expressions/
                    # helpers, not in the arm body. Do not infer their execution
                    # from which arm the backend eventually selects.
                    for child in _children(item):
                        if child is not item["clause"]:
                            visit(child, active)
                    key = "default" if item["kind"] == "DefaultCaseItem" else f"arm_{index}"
                    if first_match:
                        insert(spans[id(item["clause"])][0], f"begin {matched}=1; ")
                    wrap(item["clause"], origin, "case-arm", key)
                    if first_match:
                        insert(spans[id(item["clause"])][1], " end ")
                if first_case and failure_callback is not None:
                    # The managed compiler moves this adjacent marker into
                    # the original failure action, inside assertOn's gate.
                    insert(spans[id(node)][1], f' {CASE_FAILURE_FUNCTION}({failure_callback}, '
                           f'{helper_contexts.enabled}, {helper_contexts.scope}); ')
                    scope = event_scopes[id(node)]
                    definition_key = (id(scope), 'case-failure-function')
                    if definition_key not in wrapped:
                        wrapped.add(definition_key)
                        failure_definitions.append((scope, case_failure_function(CALLBACK)))
                if not default:
                    callback = event(node, origin, "case-no-match", "no-match")
                    key = (id(node), "implicit-default")
                    if key not in wrapped:
                        if matched is None:
                            insert(spans[id(node["endcase"])][0], " default: begin " + callback + " end ")
                        else:
                            insert(spans[id(node)][1], f" if(!{matched}) begin {callback} end ")
                        wrapped.add(key)
                if first_case:
                    insert(spans[id(node)][1], ' end ')
            return
        for child in _children(node):
            visit(child, active)

    visit(root)
    # A helper with no goal events needs no call-context parameter or event
    # expansion. Its typed, selected reads still need preservation. Do not
    # make read anchors depend on the narrower event-helper inlining profile.
    visit(root, active=True, reads_only=True)
    if anchored_reads:
        # One observer-owned generic definition, outside the DUT's parameterized
        # module scopes. Each use specializes on its original expression type.
        insert(0, read_function())
    for scope in expression_scopes.values():
        insert(scope_head(scope), choice_function(CALLBACK))
    for (_, late), scope in assertion_scopes.items():
        insert(scope_head(scope), assertion_function(CALLBACK, deferred=late))
    for scope in initializer_scopes.values():
        insert(scope_head(scope), entry_function(CALLBACK))
    for scope, definition in (*operation_definitions, *failure_definitions):
        insert(scope_head(scope), definition)
    for span, definition in write_declarations.items():
        insert(scope_head(event_scopes[innermost[span]]), definition)
    def selected(origin):
        if declarations is None:
            return True
        return (origin.start in declared_processes if origin.kind in _PROCESSES else
                (origin.start, origin.end) in declared_controls)

    issues.extend("control outside an observed event body: " + origin.key
                  for origin in controls if selected(origin) and origin.key not in observed)
    # Native context inputs distinguish repeated calls. The current compiler
    # binder uses ancestry only and cannot yet prove those copies' identities.
    ancestry_keys = set()
    for entry in events:
        key = (entry.origin, entry.choice, entry.contexts,
               tuple((events[p].origin, events[p].choice) for p in entry.ancestors))
        if key in ancestry_keys:
            binding_issues.append("compiler helper occurrence contexts have ambiguous control ancestry")
        ancestry_keys.add(key)
    edits = helper_contexts.apply(edits, issues)
    edits, expression_replacements = lifted_selector_edits(
        source, edits, deferred.lifted, replacements=(*operation_replacements, *failure_replacements))
    edits, continuous_replacements = driver_edits(source, edits, continuous_drivers,
                                                  pending_layout=pending_layout,
                                                  deferred=deferred.consumers,
                                                  replacements=expression_replacements)
    if scheduling is not None and scheduled_processes != set(scheduling):
        raise ValueError("compiler sensitivity includes an unselected process")
    # Close scheduling wrappers after all original body/probe insertions at the
    # same boundary. The compiler annotation stream remains unchanged.
    # Scope labels precede declarations inserted at the same begin boundary.
    # Anonymous names come from the original elaboration, not backend numbering.
    # Subscribers access one scope-owned scheduling API. Declarations cannot
    # depend on which alternative of a generated scope a backend visits first.
    scope_state = pending_layout.declaration + deferred.declarations
    if scheduling is not None:
        scope_state += RESUME_DECLARATION
    state_declarations = (tuple(Insertion(offset, scope_state) for offset in callback_scopes)
                          if scope_state else ())
    shared_helpers = ((Insertion(0, deferred.helper_declarations),) if deferred.consumers else ())
    native = SourceEdits(source, tuple(sorted((*shared_helpers, *generated.opening, *state_declarations,
                                             *scheduling_openings, *edits,
                                             *scheduling_insertions, *generated.closing),
                                             key=lambda edit: edit.offset)),
                         tuple(sorted((*scheduling_replacements, *continuous_replacements,
                                       *initializer_replacements),
                                      key=lambda edit: edit.start)))
    visits = []

    def expand(node, process=None, iterations=()):
        if type(node) is dict:
            kind = node.get("kind")
            if kind in {"InitialBlock", "FunctionDeclaration", "TaskDeclaration"}:
                return
            if kind == "InvocationExpression" and id(node) in expanded_calls:
                expand(node.get("arguments"), process, iterations)
                expand(expanded_calls[id(node)].get("items", []), process, iterations)
                return
            if kind in _PROCESSES:
                if not selected(by_node[id(node)]):
                    return
                process = by_node[id(node)].key
            if kind in _LOOPS and process is not None:
                loop = loops.get((kind, *spans[id(node)]))
                if loop is not None:
                    for value in loop.values:
                        expand(node["statement"], process, (*iterations, value))
                return
            if kind == "ConditionalStatement":
                expand(node["predicate"], process, iterations)
            elif kind == "CaseStatement":
                expand(node["expr"], process, iterations)
            if id(node) in by_node and process is not None:
                visits.append(ControlVisit(by_node[id(node)].key, process, iterations))
                if len(visits) > MAX_EVENTS:
                    raise ValueError("native control expansion exceeds the collector storage bound")
            if kind == "ConditionalStatement":
                expand(node["statement"], process, iterations)
                if "elseClause" in node:
                    expand(node["elseClause"]["clause"], process, iterations)
                return
            if kind == "CaseStatement":
                # The compiler emits default last, independently of its source position.
                for item in sorted(node["items"], key=lambda item: item["kind"] == "DefaultCaseItem"):
                    for child in _children(item):
                        if child is not item["clause"]:
                            expand(child, process, iterations)
                    expand(item["clause"], process, iterations)
                return
        for child in _children(node):
            expand(child, process, iterations)

    if enumerate_iterations:
        expand(root)
    declared_events = []
    if declarations is not None:
        sites = {(site.schema, site.start, site.end): index
                 for index, site in enumerate(declarations.sites)}
        origins = {origin.key: origin for origin in controls}
        # The CST counts default at its source position; the typed declaration
        # keeps it separately. Resolve that ordering once per source decision.
        case_outcomes = {}
        for origin in controls:
            if origin.kind == "CaseStatement":
                arms = [choice.key for choice in origin.choices
                        if choice.key not in {"default", "no-match"}]
                case_outcomes[origin.key] = {key: f"arm_{index}" for index, key in enumerate(arms)}
        for entry in events:
            if entry.choice is None:
                continue
            if entry.role in {"failure", "location", "operation", "read"} or entry.role.startswith(("loop-", "expression-", "boolean-")):
                schema = ("location" if entry.role == "location" else
                          "value-observation" if entry.role == "read" else
                          "operation-event" if entry.role == "operation" else
                          "artifact-failure" if entry.role == "failure" else
                          "loop-activation" if entry.role.startswith("loop-") else
                          "boolean-expression" if entry.role.startswith("boolean-") else "control-choice")
                site = sites.get((schema, entry.start, entry.end))
                outcome = entry.choice
            else:
                origin = origins[entry.origin]
                site = sites.get(("control-choice", origin.start, origin.end))
                outcome = case_outcomes.get(entry.origin, {}).get(entry.choice, entry.choice)
            if site is None or outcome not in declarations.sites[site].outcomes:
                raise ValueError("a native event has no selected source declaration")
            declared_events.append(DeclaredEvent(site, outcome, entry.ordinal))
    return SourcePlan(hashlib.sha256(source_bytes).hexdigest(), tuple(controls),
                      tuple(events), compiler, native, tuple(issues), tuple(loops.values()),
                      tuple(invocations), tuple(visits), tuple(helper_contexts.calls), declarations,
                      tuple(declared_events), scheduling is not None,
                      tuple(dict.fromkeys(binding_issues)), tuple(sorted(callback_scopes)), enumerate_iterations,
                      loop_proofs, tuple(sorted(unit_callback_scopes)), tuple(read_definitions))
