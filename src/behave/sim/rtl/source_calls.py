"""Source helper invocations, without guessing compiler inline order.

Typed calls bind to their original compilation-unit/module/package definitions. CST-only
diagnostics require unambiguous lexical calls. Neither uses generated wire names.
Helpers must have direct, non-suspending source calls.
Functions and tasks retain their original frame and module writes. The
expansion owner proves call/control contexts; recursive cycles need automatic frames.
The index is temporary CST state; SourcePlan retains only immutable source spans.
"""

import re
from collections import defaultdict, deque
from dataclasses import dataclass

from .source_callbacks import SCOPE_TOKEN
from .source_dispatch import ContextDispatch
from .source_edits import Insertion
from .source_syntax import children, walk

# The current RTLIL binder proves copies by statement-control ancestry. These
# wrappers introduce no additional activation condition; native calls do not
# depend on this compiler-specific list.
_COMPILER_EAGER_CONTEXTS = {
    "InvocationExpression", "ArgumentList", "OrderedArgument", "SimplePropertyExpr",
    "SimpleSequenceExpr", "ParenthesizedExpression", "ConcatenationExpression",
    "AddExpression", "SubtractExpression", "MultiplyExpression", "DivideExpression",
    "ModExpression", "BinaryAndExpression", "BinaryOrExpression", "BinaryXorExpression",
    "BinaryXnorExpression", "LogicalShiftLeftExpression", "LogicalShiftRightExpression",
    "ArithmeticShiftLeftExpression", "ArithmeticShiftRightExpression", "UnaryPlusExpression",
    "UnaryMinusExpression", "UnaryNotExpression", "UnaryBitwiseNotExpression",
    "EqualityExpression", "InequalityExpression", "LessThanExpression", "LessThanEqualExpression",
    "GreaterThanExpression", "GreaterThanEqualExpression", "CaseEqualityExpression",
    "CaseInequalityExpression", "AssignmentExpression", "NonblockingAssignmentExpression",
}

_SUSPENDING_STATEMENTS = {
    "TimingControlStatement", "DisableStatement", "ParallelBlockStatement",
    "WaitStatement", "WaitForkStatement", "DisableForkStatement", "EventTriggerStatement",
}

_LEXICAL_SCOPES = {
    "CompilationUnit", "ModuleDeclaration", "PackageDeclaration", "FunctionDeclaration", "TaskDeclaration",
    "SequentialBlockStatement", "ParallelBlockStatement", "GenerateBlock",
    "ForLoopStatement", "ForeachLoopStatement",
}


def _name(node):
    if type(node) is dict and node.get("kind") == "IdentifierName":
        token = node.get("identifier", {})
        if token.get("kind") == "Identifier" and not token.get("text", "").startswith("\\"):
            return token.get("text")
    return None


@dataclass(frozen=True, slots=True)
class HelperInvocation:
    name: str
    start: int
    end: int
    definition_start: int
    definition_end: int
    context: int


class HelperCalls:
    def __init__(self, root, *, event_nodes, runtime_calls=None,
                 retained_frame_calls=(), spans=None, resolved_calls=()):
        self._runtime_calls = None if runtime_calls is None else frozenset(runtime_calls)
        self._spans = spans
        self.retained_frame_calls = frozenset(retained_frame_calls)
        self.modules, self.parents = {}, {}
        self.functions, self.calls = defaultdict(list), defaultdict(list)
        self.shadows, self.owners, self.scopes = defaultdict(set), {}, {}
        controlled = set()
        suspending = set()
        self.qualified = set()
        self.resolved_calls = {}
        for start, end, definition in resolved_calls:
            key = start, end
            if key in self.resolved_calls and self.resolved_calls[key] != definition:
                raise ValueError("instance-dependent helper call needs source specialization")
            self.resolved_calls[key] = definition
        definitions_by_name = {}
        call_nodes = []
        self.call_keys = {}

        def collect(node, module=None, parent=None, owner=None, scopes=()):
            if type(node) is dict:
                if node.get("kind") in {"CompilationUnit", "ModuleDeclaration", "PackageDeclaration"}:
                    module = node
                    owner = None
                    scopes = ()
                    for item in node.get("members", []):
                        if item.get("kind") in {"FunctionDeclaration", "TaskDeclaration"}:
                            name = _name(item["prototype"]["name"])
                            if name:
                                self.functions[(id(module), name)].append(item)
                if node.get("kind") in {"FunctionDeclaration", "TaskDeclaration"}:
                    owner = (id(module), _name(node["prototype"]["name"]))
                    if spans is not None:
                        definitions_by_name[spans[id(node["prototype"]["name"])][0]] = owner
                if node.get("kind") in _LEXICAL_SCOPES:
                    scopes = (*scopes, id(node))
                if node.get("kind") == "Declarator":
                    self.shadows[scopes[-1]].add(node.get("name", {}).get("text"))
                if id(node) in event_nodes and owner:
                    controlled.add(owner)
                if node.get("kind") in _SUSPENDING_STATEMENTS and owner:
                    suspending.add(owner)
                self.modules[id(node)], self.parents[id(node)] = module, parent
                self.owners[id(node)] = owner
                self.scopes[id(node)] = scopes
                if node.get("kind") == "InvocationExpression" or (
                        node.get("kind") in {"IdentifierName", "ScopedName"}
                        and spans is not None and spans[id(node)] in self.resolved_calls):
                    call_nodes.append(node)
                parent = node
            for child in children(node):
                collect(child, module, parent, owner, scopes)

        collect(root, scopes=(id(root),))
        for call in call_nodes:
            coordinates = None if spans is None else spans[id(call)]
            if coordinates in self.resolved_calls:
                key = definitions_by_name.get(self.resolved_calls[coordinates])
                if key is None:
                    raise ValueError("resolved helper has no selected source definition")
            else:
                name = _name(call.get("left"))
                key = (id(self.modules[id(call)]), name) if name else None
                if key is not None and key not in self.functions and (id(root), name) in self.functions:
                    key = (id(root), name)
                if key is None:
                    self.qualified.update(part["text"] for part in walk(call.get("left"))
                                          if type(part) is dict and part.get("kind") == "Identifier")
            self.call_keys[id(call)] = key
            if key is not None:
                self.calls[key].append(call)
        # The source plan, not a second syntax whitelist, owns which nodes emit.
        # Helpers without those events require no callback, just as before.
        # Close this set over calls so an arithmetic wrapper cannot hide a
        # controlled callee. No runtime source object escapes this index.
        callers, callees = defaultdict(set), defaultdict(set)
        for callee, calls in self.calls.items():
            for call in calls:
                if owner := self.owners[id(call)]:
                    callers[callee].add(owner)
                    callees[owner].add(callee)
        # A timing statement can live in a control-free callee which has no
        # native events of its own. Close over all lexical calls, not only the
        # observed helper graph, before accepting a static context parameter.
        pending = deque(suspending)
        while pending:
            for caller in callers[pending.popleft()] - suspending:
                suspending.add(caller)
                pending.append(caller)
        self.suspending = frozenset(suspending)
        self.observed = set(controlled)
        pending = deque(controlled)
        while pending:
            for caller in callers[pending.popleft()] - self.observed:
                self.observed.add(caller)
                pending.append(caller)
        # A retained runtime frame is not a legal constant-function boundary
        # for the backend. Keep construction-call chains inline, transitively:
        # a runtime-only spelling inside f can still execute while folding f().
        construction = {callee for callee, calls in self.calls.items()
                        if any(not self.requires_context(call) for call in calls)}
        pending = deque(construction)
        while pending:
            for callee in callees[pending.popleft()] - construction:
                construction.add(callee)
                pending.append(callee)
        self.runtime_only = self.observed - construction

    def resolve(self, call):
        """Return the unique proved definition, None for non-helper calls."""
        key = self.call_keys[id(call)]
        definitions = self.functions.get(key, ())
        if not definitions or key not in self.observed:
            return None
        name = key[1]
        resolved = self._spans is not None and self._spans[id(call)] in self.resolved_calls

        def reject():
            raise ValueError("native helper occurrence contexts need an unshadowed non-suspending call")

        if len(definitions) != 1 or (not resolved and name in self.qualified) or key in self.suspending:
            reject()
        definition, = definitions
        prototype = definition["prototype"]
        # The original body and lifetime stay intact. Static locals (including
        # declaration initializers and the return variable) are not copied or
        # reinitialized. Context inputs are consumed before this call returns:
        # recursive cycles have automatic frames and this gate excludes suspension,
        # so another invocation cannot overwrite a live observation context.
        ports = prototype.get("portList", {}).get("ports", ())
        if "arguments" not in call and not resolved:
            reject()
        if ports is None or any(p.get("kind") not in {"Comma", "FunctionPort"} or
                               p.get("kind") == "FunctionPort" and
                               "initializer" in p.get("declarator", {}) for p in ports):
            reject()
        # Original arguments stay in their original call and direction. Appending
        # observer inputs does not copy or reinterpret output/inout/ref actuals;
        # the HDL runtime retains their binding and copy-back semantics.
        # Only declarations visible from this call can shadow its helper.
        # A local in another function / sibling block is not module state.
        if not resolved and any(name in self.shadows[scope] for scope in self.scopes[id(call)]):
            reject()
        parameters = call.get("arguments", {}).get("parameters", ())
        if any(p.get("kind") not in {"Comma", "OrderedArgument", "NamedArgument"} for p in parameters):
            reject()
        # Returns, casts and module writes stay in the original body. No
        # mutable context stack needs cleanup on return. Loop proofs and symbolic
        # expression ordering keep their own effect checks, not a native-call gate.
        return definition

    def is_call(self, node):
        return id(node) in self.call_keys

    def requires_context(self, call):
        # Only the selected typed call graph can exclude construction, unused
        # helpers and type-only expressions. CST-only diagnostics stay strict.
        return self._runtime_calls is None or self._spans[id(call)] in self._runtime_calls

    def compiler_context_issue(self, call):
        """Can the current compiler binder use control ancestry alone?

        Native callbacks stay inside the original invocation, including a lazy
        operand. A compiler may hoist the helper's pure computation, so mapping
        those copies needs more than the enclosing statement's control path.
        This restriction belongs to RTLIL binding, not native instrumentation.
        """
        issue = "compiler helper copy mapping requires eager call contexts"
        parent = self.parents[id(call)]
        while parent is not None and parent.get("kind") not in {
                "ExpressionStatement", "ConditionalStatement", "CaseStatement"}:
            kind = parent.get("kind")
            if kind == "ConditionalPredicate":
                supported = len(parent.get("conditions", ())) == 1
            elif kind == "ConditionalPattern":
                supported = set(parent) == {"kind", "expr"}
            else:
                supported = kind in _COMPILER_EAGER_CONTEXTS
            if not supported:
                return issue
            parent = self.parents[id(parent)]
        return issue if parent is None else None


class HelperContexts:
    """Observer-only call arguments and per-invocation callback dispatch.

    Helpers retain their original lifetime, arguments and directions, return type,
    and body. Observation inputs neither repeat DUT expressions nor add DUT state.
    A nested call derives its ID from its caller's input;
    this avoids a shared mutable context register or a guessed execution order.
    Construction and unselected callers disable callbacks, not original actions;
    their calls share the same body and any static locals with runtime calls.
    The source/control binder separately proves which compiler copy owns it.
    """

    _INPUT = "__behave_native_event_call_context"
    _ENABLED = "__behave_native_event_call_observed"
    _LOOPS = "__behave_native_event_loop_context"
    _SCOPE = "__behave_native_event_call_scope"

    def __init__(self, helpers, spans, callback):
        self.helpers, self.spans, self.callback = helpers, spans, callback
        self.calls, self._stack = [], []
        self._calls, self._definitions, self._sites = {}, {}, {}
        self._loop_contexts = {}
        self._loop_width = 0

    def enter(self, node, definition, loops=()):
        context = len(self.calls)
        self.calls.append(HelperInvocation(_name(definition["prototype"]["name"]),
            *self.spans[id(node)], *self.spans[id(definition)], context))
        self._calls.setdefault(id(node), (node, []))[1].append(
            (self._stack[-1] if self._stack else None, context,
             tuple(self.counter(loop) for loop in loops)))
        self._loop_contexts[context] = tuple(loop.context for loop in loops)
        self._loop_width = max(self._loop_width, len(loops))
        self._definitions[id(definition)] = definition
        self._stack.append(context)

    def leave(self):
        self._stack.pop()

    def reenter(self, node, index, *, typed=False):
        """Route a recursive backedge to the existing finite source context.

        The original call still pushes its automatic frame. Only observer IDs
        repeat, like loop callbacks; caller inputs remain intact on return.
        Outer loop coordinates belong to that entry context, not to the new
        recursive call's local loops.
        """
        if not typed:
            definitions = {self.spans[id(item)][0]: item for item in self._definitions.values()}
            for context in self._stack[index:]:
                definition = definitions[self.calls[context].definition_start]
                if definition['prototype'].get('lifetime', {}).get('text') != 'automatic':
                    raise ValueError("recursive observation requires automatic helper frames")
        target, caller = self._stack[index], self._stack[-1]
        values = tuple(f"{self._LOOPS}[{32 * self._loop_contexts[caller].index(loop)} +: 32]"
                       for loop in self._loop_contexts[target])
        self._calls.setdefault(id(node), (node, []))[1].append((caller, target, values))

    def inherited(self, context):
        return bool(self._stack) and context in self._loop_contexts[self._stack[-1]]

    def counter(self, loop):
        """Outer coordinates are copied values, never callee lexical names."""
        if self.inherited(loop.context):
            index = self._loop_contexts[self._stack[-1]].index(loop.context)
            return f"{self._LOOPS}[{32 * index} +: 32]"
        return loop.counter

    @property
    def path(self):
        return tuple((self.calls[index].start, self.calls[index].end) for index in self._stack)

    @property
    def enabled(self):
        return self._ENABLED if self._stack else "1'b1"

    @property
    def scope(self):
        return self._SCOPE if self._stack else SCOPE_TOKEN

    def event(self, site, index, *, expression=False):
        """Share a lexical callback statement, never its invocation's hit bit."""
        marker, alternatives, _ = self._sites.setdefault(
            site, (f"__behave_native_event_site_{len(self._sites)}__", [], expression))
        alternatives.append((self._stack[-1] if self._stack else None, index))
        return marker

    def apply(self, edits, issues):
        edits = list(edits)
        declarations = []
        runtime_definitions = set()
        for definition in self._definitions.values():
            # Append, never prepend: an untyped first formal must not inherit
            # the observer parameter's integer type or input direction.
            # Supply context explicitly at every runtime call. A non-ANSI port's
            # initializer is not an ANSI default argument; it can overwrite
            # the actual input on entry and must not be added.
            parameters = [(self._INPUT, "int unsigned", "32'hffffffff"),
                          (self._ENABLED, "bit", "1'b0"),
                          (self._SCOPE, "int unsigned", "32'hffffffff")]
            if self._loop_width:
                parameters.append((self._LOOPS, f"logic [{32 * self._loop_width - 1}:0]", "'0"))
            inputs = [f"input {type_} {name}" for name, type_, _ in parameters]
            ports = definition["prototype"].get("portList")
            if ports is not None:
                # Type witnesses may still refer to the original signature.
                # These ANSI defaults disable observation for type-only calls;
                # runtime and construction calls are explicitly bound below.
                # Non-ANSI initializers execute, so that form must keep its
                # mandatory context ports rather than acquiring defaults.
                inputs = [f"input {type_} {name}={value}" for name, type_, value in parameters]
                declarations.append(Insertion(self.spans[id(ports["closeParen"])][0],
                    (", " if ports.get("ports") else "") + ", ".join(inputs)))
            else:
                # Non-ANSI formal order is declaration order. Append after the
                # original port declarations, never ahead of them or after code.
                port_nodes = [item for item in definition.get("items", ())
                              if item["kind"] == "PortDeclaration"]
                boundary = port_nodes[-1]["semi"] if port_nodes else definition["semi"]
                declarations.append(Insertion(self.spans[id(boundary)][1],
                                              " " + "; ".join(inputs) + "; "))
            name = _name(definition["prototype"]["name"])
            key = (id(self.helpers.modules[id(definition)]), name)
            if key in self.helpers.runtime_only:
                runtime_definitions.add(self.spans[id(definition)][0])
            runtime_calls = [call for call in self.helpers.calls[key]
                             if self.helpers.requires_context(call)]
            for call in self.helpers.calls[key]:
                if self.helpers.requires_context(call):
                    continue
                arguments = call.get("arguments")
                if arguments is None:
                    declarations.append(Insertion(self.spans[id(call)][1],
                        "(" + ", ".join(value for _, _, value in parameters) + ")"))
                    continue
                actuals = arguments.get("parameters", ())
                named = any(actual["kind"] == "NamedArgument" for actual in actuals)
                values = [f".{name}({value})" if named else value for name, _, value in parameters]
                declarations.append(Insertion(self.spans[id(arguments["closeParen"])][0],
                    (", " if actuals else "") + ", ".join(values)))
            if key in self.helpers.runtime_only and runtime_calls and all(
                    self.spans[id(call)] in self.helpers.retained_frame_calls for call in runtime_calls):
                # Retain the original automatic frame instead of replicating
                # every callback/context dispatch at every invocation. The typed
                # proof excludes nonlocal storage and reference arguments;
                # output/inout copy-back stays at the original call boundary.
                # Other helpers stay inline; this is not a new refusal rule.
                declarations.append(Insertion(self.spans[id(definition["semi"])][1],
                                       " /* verilator no_inline_task */ "))
            if any(self.helpers.requires_context(call) and id(call) not in self._calls
                   for call in self.helpers.calls[key]):
                issues.append("helper call outside an observed event body: " + name)
        for node, contexts in self._calls.values():
            def coordinates(values):
                lanes = (*values, *("32'b0" for _ in range(self._loop_width - len(values))))
                return "{" + ",".join(reversed(lanes)) + "}"

            if contexts[0][0] is None:
                if len(contexts) != 1:
                    raise ValueError("duplicate root helper call context")
                selector = str(contexts[0][1])
                enabled = "1'b1"
                scope = SCOPE_TOKEN
                loop_selector = coordinates(contexts[0][2])
            else:
                selector = "32'hffffffff"
                enabled = self._ENABLED
                scope = self._SCOPE
                loop_selector = "'0"
                for parent, context, values in reversed(contexts):
                    selector = f"({self._INPUT} == {parent} ? {context} : {selector})"
                    loop_selector = f"({self._INPUT} == {parent} ? {coordinates(values)} : {loop_selector})"
            values = [(self._INPUT, selector), (self._ENABLED, enabled), (self._SCOPE, scope)]
            if self._loop_width:
                values.append((self._LOOPS, loop_selector))
            arguments = node.get("arguments")
            # Once a call uses named arguments, appended observer inputs must
            # also be named. Leave original argument binding and effects intact.
            named = arguments is not None and any(
                actual["kind"] == "NamedArgument" for actual in arguments.get("parameters", ()))
            actuals = ", ".join(f".{name}({value})" if named else value for name, value in values)
            if arguments is None:
                edits.append(Insertion(self.spans[id(node)][1], "(" + actuals + ")"))
            else:
                edits.append(Insertion(self.spans[id(arguments["closeParen"])][0],
                    (", " if arguments.get("parameters") else "") + actuals))
        callbacks = {}
        dispatch = ContextDispatch()
        for marker, alternatives, expression in self._sites.values():
            if alternatives[0][0] is None:
                index = alternatives[0][1]
                text = index if expression else f" {self.callback}({index}, {SCOPE_TOKEN}); "
            else:
                definition_start = self.calls[alternatives[0][0]].definition_start
                index = dispatch.select(alternatives, definition_start=definition_start,
                    context=self._INPUT, runtime_only=definition_start in runtime_definitions)
                if index is not None:
                    text = (index if expression else
                            f" if ({self._ENABLED}) {self.callback}({index}, {self._SCOPE}); ")
                elif expression:
                    text = "32'hffffffff"
                    for context, value in reversed(alternatives):
                        text = f"({self._INPUT} == {context} ? {value} : {text})"
                else:
                    text = " if (" + self._ENABLED + ") case (" + self._INPUT + ") " + " ".join(
                        f"{context}: begin {self.callback}({index}, {self._SCOPE}); end"
                        for context, index in alternatives)
                    text += f" default: {self.callback}(32'hffffffff, {self._SCOPE}); endcase "
            callbacks[marker] = text
        # At a shared source boundary, declaration metadata must precede a
        # callback that wraps the first statement (including one-line helpers).
        return [Insertion(edit.offset, re.sub(r"__behave_native_event_site_\d+__",
                    lambda match: callbacks[match[0]], edit.text))
                for edit in (*dispatch.declarations, *declarations, *edits)]
