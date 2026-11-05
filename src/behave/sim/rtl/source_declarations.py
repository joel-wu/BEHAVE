"""Source goal declarations from the selected, typed design, before synthesis.

These rows belong to SourcePlan, not to a second public catalogue. They retain
events that RTLIL can erase. A source site is declared once; instance/process/call
contexts are separate occurrences. Declaration alone enables no observer or
symbolic encoder. Public execution and symbolic consumers must implement these
same rows; compiler action guards are not substitutes for source events.
"""

from dataclasses import dataclass

from ..core.operation_goal import OperationRule, OperationValue
from ..core.source_rules import EMPTY_SOURCE_RULES, SourceReadRule
from .source_call_effects import FrameLocalCalls
from .source_edits import SourceEdits
from .source_elaboration import generate_iterations
from .source_failure_events import SourceFailure, argument_order_issue, effect_free_argument
from .source_integral import integral_value_facts, typed_symbols
from .source_loop_constants import loop_key
from .source_operation_types import CaptureTypes
from .source_operations import OperationCollector
from .source_read_events import DependencyRead, type_witness
from .source_triggers import instance_bindings, process_triggers, scope_bindings
from .source_value_effects import SourceValueEffects

VERSION = "rtl-source-declarations/16"
_LOGICAL = {"LogicalAnd", "LogicalOr"}
_LOOPS = {"ForLoop", "WhileLoop", "DoWhileLoop", "RepeatLoop", "ForeverLoop", "ForeachLoop"}
_PROCESSES = {"Always", "AlwaysComb", "AlwaysFF", "AlwaysLatch"}
_SCOPES = {"Root", "CompilationUnit", "Instance", "InstanceBody", "GenerateBlock",
           "GenerateBlockArray", "InstanceArray"}
_TYPE_QUERIES = {"$typename", "$dimensions", "$unpacked_dimensions", "$isunbounded"}
_ARRAY_QUERIES = {"$left", "$right", "$low", "$high", "$increment", "$size"}
_READS = {"NamedValue", "HierarchicalValue", "ElementSelect", "RangeSelect", "MemberAccess"}
_INCREMENTS = {"Preincrement", "Predecrement", "Postincrement", "Postdecrement"}
# Executable statement entries, not AST containers or scheduling annotations.
# Declaration initializers and continuous drivers have their own entry rules
# below. Keeping unsupported statement forms here declares the obligation; it
# does not claim that a native producer or an encoder implements it.
_LOCATION_STATEMENTS = {
    "Empty", "ExpressionStatement", "Return", "Continue", "Break", "Disable",
    "Conditional", "Case", "PatternCase", *_LOOPS, "ImmediateAssertion",
    "ConcurrentAssertion", "DisableFork", "Wait", "WaitFork", "WaitOrder",
    "EventTrigger", "ProceduralAssign", "ProceduralDeassign", "RandCase",
    "RandSequence", "ProceduralChecker",
}


def _children(node):
    # Compiler-resolved pattern elements repeat source setters and can add
    # context conversions. They drive evaluation, not new source declarations.
    if isinstance(node, list):
        return tuple(enumerate(node))
    if isinstance(node, dict):
        return tuple((key, value) for key, value in node.items()
                     if key not in {"type", "elementType", "netType", "resolvedElements"}
                     and isinstance(value, (dict, list)))
    return ()


def _nodes(node):
    if isinstance(node, dict):
        yield node
    for _, child in _children(node):
        yield from _nodes(child)


@dataclass(frozen=True, order=True, slots=True)
class SourceGoalSite:
    schema: str
    start: int
    end: int
    outcomes: tuple[str, ...]
    # Terms of one Boolean evaluation share its source span. Separate
    # evaluations may not collectively satisfy a multi-term pattern.
    evaluation: tuple[int, int] | None = None
    # Predicate identity belongs to the declaration, not a mutable observer
    # side table. Source order/outcomes already uniquely order these sites.
    operation_rules: tuple[OperationRule, ...] = ()
    read_rules: tuple[SourceReadRule, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceGoalOccurrence:
    site: int
    # A path through the elaborated design's ordered member lists, not a Slang
    # pointer or a synthesized wire name. Calls are original source spans.
    # A recursive backedge revisits its active callee context (loop-erased
    # call path). Goals describe source events, not an unbounded call depth.
    context: tuple[str | int, ...]
    calls: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class SourceContext:
    key: tuple[str | int, ...]
    # Exact selected HDL scope. None retains a declaration whose native scope
    # mapping is not implemented; it must never be confused with the top scope.
    scope: tuple[str, ...] | None
    process_start: int
    # None means unresolved sensitivity, not an inactive or implicit process.
    triggers: tuple[tuple[str, str], ...] | None = None


@dataclass(frozen=True, slots=True)
class SourceDeclarations:
    sites: tuple[SourceGoalSite, ...]
    occurrences: tuple[SourceGoalOccurrence, ...]
    process_starts: tuple[int, ...]
    call_spans: tuple[tuple[int, int], ...]
    contexts: tuple[SourceContext, ...] = ()
    failures: tuple[SourceFailure, ...] = ()
    dependency_reads: tuple[DependencyRead, ...] = ()
    read_issues: tuple[str, ...] = ()
    # The callee writes only its own frame. Actual argument expressions still
    # belong to the caller and need the loop's independent counter-write check.
    frame_local_calls: tuple[tuple[int, int], ...] = ()
    # Stronger than frame-local writes: all transitive reads also resolve to
    # automatic locals/formals or elaboration constants, with no copy-out effect.
    # Deferred-write analysis consumes this stronger value-call certificate.
    closed_value_calls: tuple[tuple[int, int], ...] = ()
    # A closed native frame may additionally copy output/inout formals back to
    # its caller. This is a compilation boundary, never a purity certificate.
    retained_frame_calls: tuple[tuple[int, int], ...] = ()
    # No hidden state dependencies: automatic frames read only arguments and
    # constants, and may write back output/inout arguments. This is separate
    # from backend retained-frame eligibility and from pure value calls.
    argument_bound_calls: tuple[tuple[int, int], ...] = ()
    def sites_for(self, schema):
        return tuple(site for site in self.sites if site.schema == schema)


@dataclass(frozen=True, slots=True)
class SourceResolution:
    """Stable declarations and private joins valid only in this compilation."""

    declarations: SourceDeclarations
    loop_keys: tuple[tuple[int, int, str], ...]
    # Original call span -> resolved definition's name offset. Includes
    # construction / unselected calls, whose observer inputs must be disabled.
    # This private compiler join is not an additional goal catalogue.
    helper_bindings: tuple[tuple[int, int, int], ...]
    operation_captures: tuple[tuple[int, int, CaptureTypes], ...] = ()
    operation_issues: tuple[str, ...] = ()
    sampled_reads: tuple[DependencyRead, ...] = ()


def declare_source(source: str, document: dict, *, filename: str, source_rules=EMPTY_SOURCE_RULES) -> SourceDeclarations:
    return resolve_source(source, document, filename=filename, source_rules=source_rules,
                          bind_producers=False).declarations


def resolve_source(source: str, document: dict, *, filename: str, source_rules=EMPTY_SOURCE_RULES,
                   bind_producers=True) -> SourceResolution:
    """Enumerate selected run-time code without a reachability/constant filter.

    Slang has already resolved modules, generate scopes and subroutine symbols.
    Its AST retains constant-dead run-time arms and loop bodies. Follow calls
    by symbol identity, never by their spelling; uncalled helpers and unselected
    generate branches contribute nothing. Initial/final construction is outside
    this step-goal profile. Locations name executable statement entries, automatic
    initializer entries (at their declared names), and continuous-driver entries.
    Blocks/lists and the process's timing control only organize these entries.
    """
    positions = SourceEdits(source, ())
    source_bytes = source.encode("utf-8")
    lines = positions._source_lines

    def offset(line, column):
        if not 1 <= line <= len(lines) or column < 1:
            raise ValueError("source declaration has no original source coordinate")
        result = lines[line - 1] + column - 1
        if positions.original_position(result) != (line, column):
            raise ValueError("source declaration is outside its original source line")
        return result

    def span(node):
        if node.get("source_file_start") != filename or node.get("source_file_end") != filename:
            raise ValueError("source declaration is outside the selected source")
        return (offset(node["source_line_start"], node["source_column_start"]),
                offset(node["source_line_end"], node["source_column_end"]))

    symbols = tuple(_nodes(document))
    symbol_table = {str(node["addr"]): node for node in symbols if "addr" in node}
    subroutines = {str(node["addr"]): node for node in symbols if node.get("kind") == "Subroutine"}
    bodies = {str(node["addr"]): node for node in symbols if node.get("kind") == "InstanceBody"}
    aliases = {str(node["addr"]): node["target"] for node in symbols if node.get("kind") == "TypeAlias"}
    sites, occurrences, process_starts, call_spans = {}, [], set(), set()
    frame_local_calls = {}
    frame_local = FrameLocalCalls(subroutines, _nodes)
    closed_value_calls = {}
    retained_frame_calls = {}
    argument_bound_calls = {}
    constants = tuple(identity for identity, symbol in symbol_table.items()
                      if symbol.get("kind") in {"Parameter", "EnumValue", "Genvar"})
    closed_values = FrameLocalCalls(subroutines, _nodes, local_reads=True,
                                   constants=constants)
    retained_frames = FrameLocalCalls(subroutines, _nodes, local_reads=True,
                                     constants=constants, copy_outputs=True, retained_frame=True)
    argument_bound = FrameLocalCalls(subroutines, _nodes, local_reads=True,
                                    constants=constants, copy_outputs=True)
    contexts = []
    failures = {}
    implicit_contexts, dependency_reads, read_issues = set(), set(), set()
    selected_loops = set()
    sampled_reads = set()
    rules_by_read = {}
    for rule in source_rules.reads:
        if rule.kind == "integer":  # HDL integral values are bit vectors, not Python bools.
            rules_by_read.setdefault(rule.symbol, []).append(rule)
    type_symbols = typed_symbols(document) if source_rules and bind_producers else {}

    def resolved_type(type_):
        while isinstance(type_, str):
            identity = type_.split(" ", 1)[0]
            type_ = aliases.get(identity, symbol_table.get(identity))
            if type_ is None:
                raise ValueError("a type query has no resolved type declaration")
        return type_

    def integral(type_):
        type_ = resolved_type(type_)
        return type_.get("kind") in {
            "ScalarType", "PredefinedIntegerType", "PackedArrayType", "EnumType",
            "PackedStructType", "PackedUnionType"}

    def read_type(node):
        if node.get("kind") not in _READS:
            raise ValueError("native dependency type needs a named value or selection")
        return type_witness(node, source_bytes, span, resolved_type)

    def fixed_size(type_):
        type_ = resolved_type(type_)
        kind = type_.get("kind")
        if kind in {"ScalarType", "PredefinedIntegerType", "FloatingType", "PackedArrayType",
                    "PackedStructType", "PackedUnionType", "EnumType"}:
            return True
        if kind == "FixedSizeUnpackedArrayType":
            return fixed_size(type_["elementType"])
        return False

    def evaluated_arguments(target, arguments):
        # QueryFuncs.cpp in the locked Slang frontend: type-only queries do
        # not execute their operands. Fixed-size $bits / array queries use the
        # type as well; an array query's dimension argument still executes.
        # Dynamic-size values remain evaluated, not mistaken for static types.
        if target in _TYPE_QUERIES:
            return ()
        if target in {"$bits"} | _ARRAY_QUERIES and arguments and fixed_size(arguments[0].get("type", {})):
            if target == "$bits":
                return ()
            if target in _ARRAY_QUERIES:
                return arguments[1:]
        return arguments

    def operation_children(node):
        if isinstance(node, dict) and node.get("kind") == "Call":
            return tuple(enumerate(evaluated_arguments(node.get("subroutine", ""), node.get("arguments", ()))))
        return tuple((key, child) for key, child in _children(node) if not (
            isinstance(node, dict) and node.get("kind") == "RangeSelect" and integral(node["value"]["type"])
            and (key == "right" or key == "left" and node["selectionKind"] == "Simple")))

    value_effects = SourceValueEffects(symbol_table, subroutines, operation_children, _nodes)
    operations = OperationCollector(source_rules.operations,
                                    type_symbols,
                                    span, operation_children, capture=bind_producers,
                                    type_witness=lambda node: type_witness(node, source_bytes, span, resolved_type),
                                    effects=value_effects)

    def emit(schema, node, outcomes, context, calls, evaluation=None):
        emit_at(schema, span(node), outcomes, context, calls, evaluation)

    def emit_at(schema, coordinates, outcomes, context, calls, evaluation=None):
        site = SourceGoalSite(schema, *coordinates, outcomes, evaluation)
        ordinal = sites.setdefault(site, len(sites))
        occurrences.append(SourceGoalOccurrence(ordinal, context, calls))

    def logical_terms(node):
        if node.get("kind") == "BinaryOp" and node.get("op") in _LOGICAL:
            yield from logical_terms(node["left"])
            yield from logical_terms(node["right"])
        else:
            yield node

    def runtime(node, context, calls=(), active=(), logical_parent=False, lvalue=False):
        if not isinstance(node, dict):
            for _, child in _children(node):
                runtime(child, context, calls, active, lvalue=lvalue)
            return
        kind = node.get("kind")
        if kind in {"Subroutine", "ProceduralBlock", "ContinuousAssign"} | _SCOPES:
            return  # Definitions/scopes are visited by their execution owner.
        if kind == "VariableDeclaration":
            # The statement references a symbol; its initializer is not a
            # statement child in Slang's AST. Automatic initialization runs at
            # this point on every activation. Static initialization does not.
            variable = symbol_table[node["symbol"].split(" ", 1)[0]]
            if variable.get("lifetime") == "Automatic" and "initializer" in variable:
                emit("location", node, ("entry",), context, calls)
                runtime(variable["initializer"], context, calls, active)
            return
        if kind in _LOCATION_STATEMENTS:
            emit("location", node, ("entry",), context, calls)
        if rules := operations.matching(node):
            site = SourceGoalSite("operation-event", *span(node),
                                  tuple(rule.goal.name for rule in rules), operation_rules=rules)
            ordinal = sites.setdefault(site, len(sites))
            occurrences.append(SourceGoalOccurrence(ordinal, context, calls))
        if rules_by_read and kind in _READS and not lvalue and integral(node.get("type", {})):
            first, last = span(node)
            rules = tuple(rules_by_read.get(source_bytes[first:last].decode(), ()))
            symbol = symbol_table.get(node.get("symbol", "").split(" ", 1)[0], {})
            if rules and symbol.get("kind") not in {"Parameter", "EnumValue", "Genvar"}:
                site = SourceGoalSite("value-observation", first, last,
                                      tuple(rule.name for rule in rules), read_rules=rules)
                ordinal = sites.setdefault(site, len(sites))
                occurrences.append(SourceGoalOccurrence(ordinal, context, calls))
                # Catalogue construction needs the selected scalar occurrence,
                # not native capture metadata. Bind the producer's exact type
                # in the same preparation phase as operation captures.
                if bind_producers:
                    width, signed, _four_state = integral_value_facts(node["type"], type_symbols)
                    for rule in rules:
                        rule.bind(OperationValue("value", width=width, signed=signed))
                    sampled_reads.add(DependencyRead(first, last, read_type(node)))
        if kind in _READS and context in implicit_contexts and not lvalue:
            symbol = symbol_table.get(node.get("symbol", "").split(" ", 1)[0], {})
            if symbol.get("kind") not in {"Parameter", "EnumValue", "Genvar"}:
                if integral(node.get("type", {})):
                    try:
                        dependency_reads.add(DependencyRead(*span(node), read_type(node)))
                    except ValueError as error:
                        read_issues.add(str(error))
                else:
                    read_issues.add("native dependency observation requires integral rvalues")
        logical = kind == "BinaryOp" and node.get("op") in _LOGICAL
        if kind in {"Conditional", "ConditionalOp"}:
            emit("control-choice", node, ("true", "false"), context, calls)
        elif kind == "Case":
            outcomes = tuple(f"arm_{index}" for index, _ in enumerate(node.get("items", ())))
            outcomes += ("default" if node.get("defaultCase") else "no-match",)
            emit("control-choice", node, outcomes, context, calls)
            if node.get('check') in {'Unique', 'Unique0', 'Priority'}:
                emit('artifact-failure', node, ('failure',), context, calls)
                location = span(node)
                failures[location] = SourceFailure(*location, 'case')
        elif kind in _LOOPS:
            emit("loop-activation", node, ("zero", "nonzero"), context, calls)
            if kind == "ForLoop":
                selected_loops.add((*span(node), loop_key(node)))
        elif kind == "ImmediateAssertion" and node.get("assertionKind") == "Assert":
            emit("artifact-failure", node, ("failure",), context, calls)
            location = span(node)
            issue = ("deferred assertions need their own sampling-region semantics"
                     if node.get("isDeferred") or node.get("isFinal") else None)
            failures[location] = SourceFailure(*location, "assert", issue,
                                              deferred=bool(node.get("isDeferred") or node.get("isFinal")))
        if logical and not logical_parent:
            for term in logical_terms(node):
                emit("boolean-expression", term, ("false", "true"), context, calls, span(node))
        if kind == "Call":
            call_spans.add(span(node))
            target = node.get("subroutine", "")
            location = span(node)
            # All selected elaborations of a lexical call must satisfy the
            # effect proof, not just the first instance that visits its source.
            frame_local_calls[location] = frame_local_calls.get(location, True) and frame_local(target)
            closed_value_calls[location] = closed_value_calls.get(location, True) and closed_values(target)
            retained_frame_calls[location] = (retained_frame_calls.get(location, True)
                                               and retained_frames(target))
            argument_bound_calls[location] = (argument_bound_calls.get(location, True)
                                               and argument_bound(target))
            callee = subroutines.get(target.split(" ", 1)[0])
            formals = () if callee is None else callee.get("arguments", ())
            for index, argument in enumerate(evaluated_arguments(target, node.get("arguments", ()))):
                writable = index < len(formals) and formals[index].get("direction") not in {None, "In"}
                runtime(argument, context, calls, active, lvalue=writable)
            if target in {"$error", "$fatal"}:
                emit("artifact-failure", node, ("failure",), context, calls)
                location = span(node)
                arguments = node.get("arguments", ())
                previous = failures.get(location)
                issue = (previous.issue if previous is not None and previous.issue else
                         argument_order_issue(arguments, value_effects))
                post_arguments = not all(effect_free_argument(arg) for arg in arguments)
                failures[location] = SourceFailure(*location, target, issue,
                    post_arguments or previous is not None and previous.post_arguments)
            if callee is not None:
                identity = str(callee["addr"])
                if identity in active:
                    cycle = active[active.index(identity):]
                    if any(subroutines[item].get("defaultLifetime") != "Automatic" for item in cycle):
                        raise ValueError("recursive observation requires automatic helper frames")
                    # All source sites in this frame are already visited by
                    # its active owner. Actual arguments above still execute;
                    # no depth bound truncates the original recursive call.
                    return
                if "body" not in callee:
                    raise ValueError("a selected helper has no source body")
                runtime(callee["body"], context, (*calls, span(node)), (*active, identity))
            elif target and not target.startswith("$"):
                raise ValueError("a run-time helper has no resolved source definition")
            return
        for key, child in _children(node):
            # A simple part-select's two bounds and an indexed part-select's
            # width are elaboration expressions, not per-activation events.
            # A constant-valued *runtime* expression still has goals: exclusion
            # follows the operand's language role, never constant folding.
            if kind == "RangeSelect" and integral(node["value"]["type"]) and (
                    key == "right" or key == "left" and node["selectionKind"] == "Simple"):
                continue
            # Keep assignment targets assignable. A selected value is wrapped
            # as a whole; only its index/range expressions are separate reads.
            # Those indices still execute when the selected value is an lvalue.
            writable = (lvalue if kind not in _READS else key == "value")
            writable |= kind == "Assignment" and key == "left"
            writable |= kind == "UnaryOp" and node.get("op") in _INCREMENTS
            runtime(child, context, calls, active, logical and key in {"left", "right"}, writable)

    def design(node, path=(), scope=(), signals=None):
        signals = {} if signals is None else signals
        if isinstance(node, dict):
            kind = node.get("kind")
            if node.get("isUninstantiated"):
                return
            if kind == "Instance":
                name = node.get("name")
                top = scope == ()
                scope = (*scope, name) if scope is not None and name else None
                body = node.get("body")
                if isinstance(body, str):
                    body = bodies.get(body.split(" ", 1)[0])
                if not isinstance(body, dict) or body.get("kind") != "InstanceBody":
                    raise ValueError("a selected instance has no resolved module body")
                # Slang serializes a shared specialization once. Follow its
                # definition separately at EACH instance path, not just the
                # first dictionary occurrence in the JSON tree.
                signals = instance_bindings(body, node.get("connections", ()), signals, top=top)
                design(body, (*path, "body"), scope, signals)
                return
            elif kind == "GenerateBlockArray":
                # Slang supplies the elaborated genvar value in each iteration;
                # its list position is not that value (descending/strided loops).
                name = node.get("name")
                for index, member, value in generate_iterations(node):
                    nested = ((*scope, f"{name}[{value}]")
                              if scope is not None and name and value is not None else None)
                    # The unnamed LoopIteration block is already represented by
                    # the indexed parent; ordinary generate blocks add a scope.
                    nested_signals = scope_bindings(member, signals)
                    for key, child in _children(member):
                        design(child, (*path, "members", index, key), nested, nested_signals)
                return
            elif kind == "GenerateBlock":
                name = node.get("name")
                scope = (*scope, name) if scope is not None and name else None
                signals = scope_bindings(node, signals)
            elif kind == "InstanceArray":
                scope = None  # Declare faithfully; native array mapping is separate.
            if kind == "ProceduralBlock":
                if node.get("procedureKind") in _PROCESSES:
                    if node.get("source_file") != filename:
                        raise ValueError("a run-time process is outside the selected source")
                    start = offset(node["source_line"], node["source_column"])
                    process_starts.add(start)
                    contexts.append(SourceContext(path, scope, start, process_triggers(node, signals)))
                    if node["procedureKind"] in {"AlwaysComb", "AlwaysLatch"} or any(
                            term.get("kind") == "ImplicitEvent" for term in _nodes(node["body"])):
                        implicit_contexts.add(path)
                    runtime(node["body"], path)
                return
            if kind == "ContinuousAssign":
                if node.get("source_file") != filename:
                    raise ValueError("a continuous assignment is outside the selected source")
                contexts.append(SourceContext(path, scope,
                    offset(node["source_line"], node["source_column"]), (("always", ""),)))
                emit("location", node["assignment"], ("entry",), path, ())
                for _, child in _children(node):
                    runtime(child, path)
                return
            if kind == "Net" and "initializer" in node:
                if node.get("source_file") != filename:
                    raise ValueError("a net driver is outside the selected source")
                first = offset(node["source_line"], node["source_column"])
                last = span(node["initializer"])[1]
                contexts.append(SourceContext(path, scope, first, (("always", ""),)))
                emit_at("location", (first, last), ("entry",), path, ())
                runtime(node["initializer"], path)
                return
            if kind == "Subroutine":
                return
        for key, child in _children(node):
            design(child, (*path, key), scope, signals)

    if document.get("design", {}).get("kind") != "Root":
        raise ValueError("source declarations require the selected elaborated design")
    design(document["design"])
    # Freeze in source order, independent of helper visitation or AST addresses.
    ordered = tuple(sorted(sites))
    remap = {sites[site]: index for index, site in enumerate(ordered)}
    unique = dict.fromkeys(SourceGoalOccurrence(remap[item.site], item.context, item.calls)
                           for item in occurrences)
    declarations = SourceDeclarations(
        sites=ordered, occurrences=tuple(unique), process_starts=tuple(sorted(process_starts)),
        call_spans=tuple(sorted(call_spans)), contexts=tuple(contexts),
        failures=tuple(failures[key] for key in sorted(failures)),
        dependency_reads=tuple(sorted(dependency_reads)), read_issues=tuple(sorted(read_issues)),
        frame_local_calls=tuple(sorted(site for site, proved in frame_local_calls.items() if proved)),
        closed_value_calls=tuple(sorted(site for site, proved in closed_value_calls.items() if proved)),
        retained_frame_calls=tuple(sorted(site for site, proved in retained_frame_calls.items() if proved)),
        argument_bound_calls=tuple(sorted(site for site, proved in argument_bound_calls.items() if proved)))
    helper_bindings = set()
    for node in symbols:
        if node.get("kind") != "Call":
            continue
        callee = subroutines.get(node.get("subroutine", "").split(" ", 1)[0])
        if callee is not None and callee.get("source_file") == filename \
                and node.get("source_file_start") == filename \
                and node.get("source_file_end") == filename:
            helper_bindings.add((*span(node), offset(callee["source_line"], callee["source_column"])))
    return SourceResolution(declarations, tuple(sorted(selected_loops)),
                            tuple(sorted(helper_bindings)),
                            operations.bindings(document, _children),
                            tuple(f"{message} at {first}:{last}" for first, last, message in sorted(operations.issues)),
                            tuple(sorted(sampled_reads)))
