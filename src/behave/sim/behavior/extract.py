"""Facts the source states about a model, read before anything is lowered.

The first stage. What a model says about itself, in the source's own terms and
before any of it becomes a command: which attributes are fixed once, which
sequences it grows and how far, what `reset` leaves behind, how many
transactions a return can produce, and which names a call may resolve to.

Kept apart from the lowering for two reasons. These answers are about the
source and the lowering's are about the representation, and mixing the two is
most of why one class came to be doing ten jobs. And a lowering that can reach
any of twenty-odd module functions at any point cannot be split later: whatever
it reaches for is an edge, and the edges have to be visible before the pieces
can be moved.

Nothing here builds IR, and nothing here decides anything: every function
returns what the source says, or nothing when the source does not say.
"""

from __future__ import annotations

import ast

from . import budgets, source_intrinsics
from .method_binding import PROPERTY_ACCESSORS, method_kind, method_parameters, property_accessors
from .source_calls import sequence_method_target
from .source_rom import DEFAULT_ROM_NODES, FrozenValue, evaluate_frozen, update_frozen_table
from .source_syntax import execution_children, local_annotations
from .source_values import SourceValueBudget


class LoweringError(Exception):
    """A model this does not lower, with the site that caused it.

    Defined here rather than in the lowering because this is the earliest stage
    that can refuse: a shape the source states and this cannot read is a
    refusal before any command exists. The lowering re-exports it, since every
    caller that catches it is catching "not in the subset" rather than "failed
    at a particular stage".

    ``code`` is the **stable identity of the rule that refused**, in kebab-case,
    distinct from ``what``, which is a sentence for a reader and carries the
    model-specific parts. The tracked census (T3-11) groups refusals by this
    field; grouping by normalized message text was how one rule's counts split
    across spellings and two rules' counts merged. Every raise site states one
    -- `tests/sim/test_lowering_census.py` walks the source and refuses a new
    raise without it -- so `code` is never derived from the message.
    """

    def __init__(self, node, what, code="unclassified"):
        super().__init__(f"{what} at line {getattr(node, 'lineno', 0)}")
        self.node = node
        self.what = what
        self.code = str(code)


#: Methods whose identity decides what is being verified rather than how.
_RELEVANT = ("process", "reset", "__init__")


def _assignment_targets(node):
    """Targets whose execution can bind or mutate a source value."""
    if isinstance(node, (ast.Assign, ast.Delete)):
        return node.targets
    if isinstance(node, ast.AnnAssign) and node.value is None:
        return ()
    if isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For)):
        return (node.target,)
    return ()


def _state_targets(target):
    """Instance fields at the roots of one possibly recursive target."""
    if isinstance(target, ast.Starred):
        yield from _state_targets(target.value)
        return
    if isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _state_targets(element)
        return
    current = target
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        if (isinstance(current, ast.Attribute)
                and isinstance(current.value, ast.Name)
                and current.value.id == "self"):
            yield current.attr
            return
        current = current.value


class SourceModel:
    """Canonical source selection of the model class and its methods.

    Lowering, native source audits and engine metadata use this same selection;
    source-aware loaded-module consumers look up its chosen class by name.
    Ambiguous, rebound or unsupported entries produce located refusals rather
    than selecting whichever class or method is encountered first. Selecting an
    entry does not establish that its body is executable, queryable or correct.
    """

    __slots__ = (
        "cls",
        "constructor",
        "file",
        "helpers",
        "reset",
        "steps",
        "tree",
    )

    def __init__(self, tree, file, cls, steps, reset, constructor, helpers):
        self.tree = tree
        self.file = file
        #: The class the harness will construct, by name.
        self.cls = cls
        #: The step methods, by the domain each belongs to. ``None`` is the key for a
        #: model with one step, which is the certified case; a multi-clock model has
        #: one per clock domain, keyed by the suffix it is written with, so `process_w`
        #: and `process_r` give ``{"w": ..., "r": ...}``.
        #:
        #: **Read here, refused elsewhere.** Multi-clock is intended and the theory
        #: already accommodates it: a step is not required to be a cycle, so several
        #: domains need another instantiation in which a step is one global event slot
        #: rather than a new theorem. What is not built yet is that instantiation and
        #: the three conditions it needs, so `certified_subset` below is what refuses
        #: one today. Putting the refusal in the reading instead would mean the shape
        #: has to be recognised twice when the instantiation is written, which is the
        #: pattern this class exists to remove.
        self.steps = dict(steps)
        #: The reset and the constructor, as syntax.
        self.reset = reset
        self.constructor = constructor
        #: Every other function the step may reach, by name.
        self.helpers = dict(helpers)

    @property
    def step(self):
        """The one step, for a caller of the certified single-clock kind.

        Raises where there are several, rather than picking, since which of two clock
        domains a caller meant is not something to guess at.
        """
        if len(self.steps) != 1:
            raise LoweringError(
                self.cls,
                f"{self.cls.name} has {len(self.steps)} step methods "
                f"({', '.join(sorted(str(d) for d in self.steps))}) and this asked "
                f"for one", code="step-method-ambiguous")
        return next(iter(self.steps.values()))

    @property
    def domains(self):
        """The clock domains this model steps in, or ``[None]`` for one."""
        return sorted(self.steps, key=lambda d: (d is not None, d))

    def for_domains(self, domains):
        """Bind named handlers to task clocks; other prefixed methods are helpers."""
        missing = set(domains) - set(self.steps)
        if missing:
            raise LoweringError(
                self.cls, f"missing clock-domain handlers: {sorted(missing)}",
                code="behavior-boundary-clock-domains-mismatch")
        steps = {domain: self.steps[domain] for domain in domains}
        helpers = dict(self.helpers)
        helpers.update((method.name, method) for domain, method in self.steps.items()
                       if domain not in steps)
        return SourceModel(self.tree, self.file, self.cls, steps,
                           self.reset, self.constructor, helpers)

    @property
    def inputs(self):
        """What the step is driven with, in order, read off its signature.

        Instance/class receivers are dropped by position. Static methods
        expose every parameter, including one literally named ``self``."""
        return list(method_parameters(self.step))

    @property
    def optional_inputs(self):
        """Step parameters with Python defaults, excluding the receiver."""
        count = len(self.step.args.defaults)
        return tuple(a.arg for a in self.step.args.args[-count:]
                     if a.arg in self.inputs) if count else ()

    def state_contract(self):
        """Every `self.X` some method of this class writes, from the source alone.

        **The state universe the audits compare over, and it is deliberately not
        `proc.state`.** `proc.state` is what the lowering collected, so an audit
        that walks it lets the audited object declare its own universe: a lowering
        that dropped a state variable also dropped it from the list, the variable
        moved natively, and `model_audit` returned nothing (review 19). This is
        read off the class's AST -- plain, augmented and annotated assignments,
        subscript stores, and tuple targets -- before any lowering exists, frozen
        onto the procedure, and consumed by the lowering (which may not invent
        state outside it) and by the audit (which walks it, not `proc.state`).
        """
        fields = set()

        for node in ast.walk(self.cls):
            for target in _assignment_targets(node):
                fields.update(_state_targets(target))
        # A property assignment calls its setter; the descriptor name is not
        # an instance cell. The accessor bodies above declare the real cells.
        return tuple(sorted(fields - self._property_names()))

    def _property_names(self):
        return {fn.name for fn in self.helpers.values()
                if method_kind(fn) in PROPERTY_ACCESSORS}

    def _state_method_closure(self, roots):
        """Exposed receiver methods and descriptor effects, without merging accessors.

        A method reference can be captured and called later. Its writes must
        not be folded as construction constants merely because the eventual
        call uses a local name. This is a conservative effect closure, not a
        claim that every referenced body executes.
        """
        methods, properties = {}, {}
        for fn in list(self.steps.values()) + list(self.helpers.values()) \
                + [m for m in (self.reset, self.constructor) if m is not None]:
            kind = method_kind(fn)
            if kind in PROPERTY_ACCESSORS:
                properties.setdefault(fn.name, {})[PROPERTY_ACCESSORS[kind]] = fn
            else:
                methods[fn.name] = fn
        queue, seen = list(roots), set()
        while queue:
            method = queue.pop()
            if id(method) in seen:
                continue
            seen.add(id(method))
            yield method
            for node in _walk_function_scope(method):
                if (isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "self" and node.attr in methods):
                    queue.append(methods[node.attr])
                if not properties:
                    continue
                for child in ast.iter_child_nodes(node):
                    if (isinstance(child, ast.Attribute)
                            and isinstance(child.value, ast.Name)
                            and child.value.id == "self"
                            and child.attr in properties):
                        accessors = properties[child.attr]
                        queue.extend(accessors[name] for name in property_accessors(child, node)
                                     if name in accessors)

    def step_state_contract(self):
        """The contract fields the **step can reach and write**, by call graph.

        Not "every method that is not reset" (review 21's counterexample: a
        helper only `reset` calls wrote a field, and the name-based rule called
        it step-written and refused a correct model). Two effect closures exist
        -- writes reachable from the step, writes reachable from reset/__init__
        -- and this is the step's: a breadth-first walk over `self.<m>` references
        starting at the step method(s), collecting each reached method's writes
        from the class's parsed method registry.
        """
        return self.reachable_state_writes(list(self.steps.values()))

    def step_state_reads(self):
        """Contract fields the selected step may read through its method closure.

        A multi-clock handler must carry a field written by another domain when it
        reads that field, even if this handler never writes it.  Conversely, another
        domain's unrelated state is not part of this procedure merely because it is
        part of the product.  This source-level closure makes that distinction before
        lowering can fold a construction value into the selected handler.
        """

        reads = set()
        for method in self._state_method_closure(self.steps.values()):
            for node in _walk_function_scope(method):
                if (isinstance(node, ast.Attribute)
                        and isinstance(node.ctx, ast.Load)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "self"):
                    reads.add(node.attr)
        return tuple(sorted(reads - self._property_names()))

    def reachable_state_writes(self, roots):
        """State fields written by the resolved ``self``-method closure.

        ``roots`` are methods from this model's parsed registry.  Consumers use this
        source authority when a proof depends on indirect helper effects; re-walking a
        call with a second name-resolution rule would make that proof disagree with
        lowering.
        """
        if type(roots) not in (tuple, list) or any(
                not isinstance(root, ast.FunctionDef) for root in roots):
            raise TypeError(
                "reachable state writes need parsed source methods")
        # The registry this class already parsed -- steps, reset, constructor,
        # helpers -- and **not** `ast.walk(self.cls)`: the walk collected nested
        # functions too, so a same-name nested helper shadowed the method
        # `self.helper()` actually calls and emptied the closure (review 22).
        writes = set()
        for method in self._state_method_closure(roots):
            for node in _walk_function_scope(method):
                for target in _assignment_targets(node):
                    writes.update(_state_targets(target))
                for operation in ("append", "extend", "pop", "clear"):
                    sequence = sequence_method_target(node, operation)
                    if sequence is not None and sequence[0] == "state":
                        writes.add(sequence[1])
        return tuple(sorted(writes - self._property_names()))

    def certified_subset(self):
        """Reasons this model is outside what is certified today, or an empty list.

        Separate from whether it could be read. Six models in the population step once
        per clock domain, and the appendix names what a multi-clock certificate would
        need first: that only primary external events drive a slot, with a clock the
        design produces lowered to an enable; that simultaneous events have a semantics
        fixed by the simulator rather than by an incidental scheduling order; and that
        the emitted relation update only the registers the events of that slot
        activate. None of the three is established, so the answer is not yet rather
        than never.
        """
        if len(self.steps) > 1:
            return [(f"{self.cls.name} steps in {len(self.steps)} clock domains "
                     f"({', '.join(sorted(str(d) for d in self.steps))}), and the "
                     "event-slot instantiation multi-clock needs is not built yet")]
        return []

    def __repr__(self):
        return (f"SourceModel({self.cls.name}, "
                f"{len(self.steps)} step(s), {self.file})")


def source_model(source, file="behavior.py", cls=None, step="process",
                 *, source_value_budget=None,
                 keyword_calls=False):
    """The one class and the one method, or a refusal naming what it could not read.

    ``cls`` names the class where a task has more than one candidate, which is where a
    manifest would say. ``step`` names the method, for a caller verifying something
    other than `process`. A method with that exact name is the procedure entry;
    methods sharing its prefix remain helpers. Only a class without that entry
    denotes domain handlers through ``process_<domain>``. Public admission still
    checks those domains against the task, rather than inferring task clocks.

    ``keyword_calls`` admits a bare transaction entry accepting ``**kwargs``:
    concrete callers already pass task fields by name, leaving any ``*args``
    empty. It does not invent a positional field order. Exact lowering keeps
    this disabled until it can represent the task-bound keyword dictionary.
    """
    if isinstance(source, str) and len(source) > budgets.SOURCE_TEXT_CHARS:
        raise LoweringError(
            None,
            f"the source has {len(source)} characters, over the reported "
            f"capability of {budgets.SOURCE_TEXT_CHARS}",
            code="source-text-too-large")
    tree = source if isinstance(source, ast.AST) else ast.parse(source)
    if source_value_budget is not None:
        if not isinstance(source_value_budget, SourceValueBudget):
            raise TypeError("source_value_budget must be a SourceValueBudget")
        source_value_budget.spend(tree, sum(1 for _ in ast.walk(tree)))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]

    def _steps_of(node):
        """The step methods of one class, by domain: ``{None: process}`` or per suffix.

        Both shapes are read. A model that steps once per clock domain is what six of
        the population are, and reading it is not the same as certifying it: whether
        the event-slot instantiation exists is `certified_subset`'s question.
        """
        out = {}
        for member in node.body:
            if not isinstance(member, ast.FunctionDef):
                continue
            if member.name == step:
                out[None] = member
            # ``process_slot`` is the task adapter's explicit simultaneous-endpoint
            # method, not a handler for a clock domain named ``slot``.  A reference
            # may define it beside the ordinary transaction-step ``process``; treating
            # that reserved interface as ``process_<domain>`` makes the exact ordinary
            # method ambiguous precisely for every multiendpoint reference.
            elif (member.name.startswith(f"{step}_")
                  and not (step == "process" and member.name == "process_slot")):
                out[member.name[len(step) + 1:]] = member
        if None in out:
            return {None: out[None]}
        return out

    # A method defined under a condition, before candidates are chosen. It has to be
    # first: such a method is not a direct member of the class body, so it is invisible
    # to the search below and the class simply looks as though it defines no step. The
    # refusal was therefore right in outcome and wrong in its reason, which for a
    # refusal is the whole of its value.
    for node in classes:
        for member in node.body:
            if isinstance(member, (ast.If, ast.For, ast.While, ast.Try)) and any(
                    isinstance(inner, ast.FunctionDef)
                    for inner in ast.walk(member)):
                raise LoweringError(
                    member, f"{node.name} defines a method under a condition, so "
                            f"which one exists depends on something this does not "
                            f"evaluate", code="method-conditional")

    candidates = [(c, _steps_of(c)) for c in classes]
    candidates = [(c, s) for c, s in candidates if s]

    if cls is not None:
        named = [(c, s) for c, s in candidates if c.name == cls]
        if not named:
            if not any(c.name == cls for c in classes):
                raise LoweringError(tree, f"no top-level class named {cls}", code="model-class-not-found")
            if keyword_calls and step == "process":
                from .source_entry import missing_process_is_definite

                if missing_process_is_definite(tree, cls):
                    from ..core.failure import CandidateFault
                    from ..core.refusal import Refusal

                    raise CandidateFault(Refusal(
                        "behavior-entry-method-missing",
                        "the selected Behavior class supplies no process, process_slot or clock-domain entry",
                        file=file, model=cls, required="process"))
            raise LoweringError(tree, f"{cls} defines no {step} method", code="step-method-missing")
        candidates = named
    if not candidates:
        raise LoweringError(tree, f"no top-level class defines {step}", code="model-class-not-found")
    if len(candidates) > 1:
        candidates = [c for c, _s in candidates]
        have = ", ".join(c.name for c in candidates)
        raise LoweringError(
            tree, f"{len(candidates)} classes define {step} ({have}) and the task "
                  f"names none of them", code="model-class-ambiguous")

    model, steps = candidates[0]
    if model.bases and [getattr(b, "id", getattr(b, "attr", "?"))
                        for b in model.bases] != ["object"]:
        raise LoweringError(model, f"{model.name} has a base class, and what it "
                                   f"inherits is not read", code="model-class-inherits")
    if model.decorator_list:
        from .class_binding import imported_class_decorator

        if not keyword_calls or not imported_class_decorator(tree, model):
            raise LoweringError(model, f"{model.name} is decorated, and what the "
                                       f"decorator returns is not read", code="model-class-decorated")

    # The class namespace by Python's own rules: the last binding of a name
    # wins, whatever binds it (review 48: `process = 7` after the def left
    # the instance with no callable process while the def still lowered).
    defined = {}
    properties = {}
    rebound = set()
    prefix = tree.body[:tree.body.index(model)]
    module_bindings = module_bound_names(ast.Module(body=prefix, type_ignores=[]))
    class_bindings = set()
    for member in model.body:
        if isinstance(member, ast.FunctionDef):
            if member.name in defined and member.name in _RELEVANT:
                raise LoweringError(
                    member, f"{model.name}.{member.name} is defined twice, and Python "
                            f"binds the second while this would read the first", code="method-defined-twice")
            binding = method_kind(member, shadowed=module_bindings | class_bindings)
            if binding is None:
                raise LoweringError(member, f"{model.name}.{member.name} is decorated", code="method-decorated")
            if binding.startswith("property"):
                if member in steps.values() or member.name in _RELEVANT:
                    raise LoweringError(member, "entry methods cannot be properties",
                                        code="method-entry-descriptor-unsupported")
                if binding == "property":
                    properties[member.name] = {}
                elif member.name not in properties:
                    raise LoweringError(member, "a property accessor needs its preceding property binding",
                                        code="property-binding-unsupported")
                properties[member.name][PROPERTY_ACCESSORS[binding]] = member
                defined.pop(member.name, None)
                class_bindings.add(member.name)
                rebound.discard(member.name)
                continue
            properties.pop(member.name, None)
            if binding != "instance" and member.name in ("reset", "__init__"):
                raise LoweringError(
                    member, "reset and construction methods require an instance receiver",
                    code="method-entry-descriptor-unsupported")
            defined[member.name] = member
            class_bindings.add(member.name)
            rebound.discard(member.name)
            continue
        if isinstance(member, ast.AnnAssign) and member.value is None:
            continue  # a bare annotation binds nothing
        if isinstance(member, ast.Delete):
            # `del process` in the class body leaves the instance without
            # it (review 49); the step/reset checks below then refuse
            for target in member.targets:
                if isinstance(target, ast.Name):
                    defined.pop(target.id, None)
                    properties.pop(target.id, None)
                    rebound.add(target.id)
                    class_bindings.discard(target.id)
            continue
        taken = _statement_bindings(member)[0]
        class_bindings.update(taken)
        for name in taken:
            defined.pop(name, None)
            properties.pop(name, None)
            rebound.add(name)
    for domain, found in sorted(steps.items(), key=lambda kv: str(kv[0])):
        if defined.get(found.name) is not found:
            raise LoweringError(
                found, f"{model.name}.{found.name} is rebound by a later "
                       f"class statement, so the instance's {found.name} is "
                       f"not this def", code="method-rebound")
    for name in ("reset", "__init__"):
        if name in rebound and name not in defined:
            raise LoweringError(
                model, f"{model.name}.{name} is bound by a class statement "
                       f"that is not a def, and what it binds is not read",
                code="method-rebound")

    # Every step's signature, not only the first, since a multi-clock model has one per
    # domain and each is driven.
    for domain, found in sorted(steps.items(), key=lambda kv: str(kv[0])):
        args = found.args
        named_variadic = keyword_calls and domain is None
        for what, present in (("*args", args.vararg), ("**kwargs", args.kwarg),
                              ("keyword-only parameters", args.kwonlyargs),
                              ("positional-only parameters",
                               getattr(args, "posonlyargs", []))):
            if present and not (named_variadic and what in ("*args", "**kwargs")):
                # Defaults are deliberately absent from this list. They are never read,
                # every input coming from the schema, and one model in the population
                # has them.
                raise LoweringError(
                    found, f"{found.name} takes {what}, which the lowering reads only "
                           f"by ignoring", code="step-signature-unsupported")

        # A concrete named call leaves *args empty, including when there is
        # no **kwargs. The task-bound projection, not syntax alone, decides
        # whether the explicit parameters receive every declared input.

    # Instance methods retain the existing explicit `self` convention. A
    # static helper has no implicit argument; a class helper binds its first
    # argument to the class, regardless of that argument's spelling. Never
    # drop every parameter named `self` (review 50).
    defined.update((f"{name}.{accessor}", function)
                   for name, accessors in properties.items()
                   for accessor, function in accessors.items())
    for name, method in defined.items():
        args = method.args
        if (args.posonlyargs
                and (name in _RELEVANT or method in steps.values())):
            raise LoweringError(
                method, f"{model.name}.{name} takes positional-only "
                        f"parameters, which the inliner cannot bind",
                code="method-signature-unsupported")
        binding = method_kind(method)
        if binding == "staticmethod":
            continue  # no implicit argument, including one literally named self
        positional = (*args.posonlyargs, *args.args)
        if not positional or ((binding == "instance" or binding.startswith("property"))
                              and positional[0].arg != "self"):
            receiver = "self" if binding == "instance" else "a class receiver"
            raise LoweringError(
                method, f"{model.name}.{name} has no {receiver} in "
                        f"first position", code="method-receiver-unsupported")

    helpers = {name: node for name, node in defined.items()
               if name not in _RELEVANT and node not in steps.values()}
    return SourceModel(tree, file, model, steps, defined.get("reset"),
                       defined.get("__init__"), helpers)


def _find_method(tree, name):
    """The last definition of ``name``, which is the one Python binds.

    Kept for callers that want one method out of a tree without asking which model it
    belongs to, and corrected: it returned the **first** match of an `ast.walk`, and a
    class binds the last definition. `source_model` refuses a source with two, so the
    two agree on every source that gets past it; this is what a caller reading a tree
    nobody vetted gets, and reading the same one Python would is the least it should do.
    """
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            found = node
    return found


def _target_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    raise LoweringError(node, f"assignment target {type(node).__name__}", code="assignment-target-unsupported")


def _loop_body_may_exit(node, exit_type):
    """Find exits owned by this loop, including those in nested loop else suites."""
    pending = list(node.body)
    while pending:
        child = pending.pop()
        if isinstance(child, exit_type):
            return True
        if isinstance(child, (ast.For, ast.While)):
            # A nested loop's body owns its exits; its else runs in our scope.
            pending.extend(child.orelse)
        elif not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef, ast.Lambda)):
            pending.extend(ast.iter_child_nodes(child))
    return False


def _may_break(node):
    """Whether executing ``node``'s body could break this loop."""
    return _loop_body_may_exit(node, ast.Break)


def _may_continue(node):
    """Whether executing ``node``'s body could continue this loop."""
    return _loop_body_may_exit(node, ast.Continue)


def _const_int(node, constants, owner=None):
    """The value of ``node`` when it is statically an integer.

    Model parameters are attributes assigned a literal once, so a bound
    written as ``self.WIDTH`` is as static as one written as a number, and
    refusing it would reject a loop whose length is fixed before the run.

    ``owner`` is the owner-aware authority (review 51): given a Name or a
    ``self.X`` node it returns the fixed integer that name has *in the scope
    the node is read in*, so a bare name folds its module's constant and a
    ``self.X`` the chosen class's, never another owner's, and a name a
    substitution or a local shadows folds to nothing. Without it a flat dict
    is used, which cannot tell one owner from another and is kept only for
    callers that have no scope (a literal with no free names)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        # A bool is an int here (a loop bound or array size of True is 1),
        # and the state-constant contract records its exact type elsewhere
        # (review 24), so bool and int agree on both sides -- excluding bool
        # here instead broke that contract (review 52 follow-up).
        return node.value
    if isinstance(node, ast.Attribute):
        if owner is not None:
            value = owner(node)
            return value if type(value) in (int, bool) else None
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            return constants.get(node.attr)
        return None
    if isinstance(node, ast.Name):
        return owner(node) if owner is not None else constants.get(node.id)
    if isinstance(node, ast.UnaryOp):
        # A negative literal is a unary minus over a constant rather than a
        # constant, so a countdown loop looks non-constant without this.
        inner = _const_int(node.operand, constants, owner)
        if inner is None:
            return None
        if isinstance(node.op, ast.USub):
            return -inner
        if isinstance(node.op, ast.UAdd):
            return inner
        return None
    if isinstance(node, ast.BinOp):
        left = _const_int(node.left, constants, owner)
        right = _const_int(node.right, constants, owner)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.LShift):
            return left << right
        if isinstance(node.op, ast.FloorDiv) and right != 0:
            return left // right
    return None


def _literal_range(node, constants=None, owner=None, *, is_builtin=None):
    """Trip count of an iterable the source fixes, or nothing."""
    shape = _shape_of(
        node, constants, owner=owner, is_builtin=is_builtin)
    return shape[0] if shape else None


def _literal_cells(node, constants, owner=None, typed=False, *,
                   is_builtin=None):
    """A recursively literal sequence value, or nothing.

    The default normalizes both source sequence carriers to the IR's list
    carrier.  ``typed`` preserves list/tuple at every level for the native state
    contract, which compares the source object rather than its IR encoding.
    """
    if isinstance(node, (ast.List, ast.Tuple)):
        cells = []
        for element in node.elts:
            value = _const_int(element, constants, owner)
            if value is None:
                value = _literal_cells(
                    element, constants, owner, typed,
                    is_builtin=is_builtin)
            if value is None:
                return None
            cells.append(value)
        return tuple(cells) if typed and isinstance(node, ast.Tuple) else cells
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        for seq, count in ((node.left, node.right), (node.right, node.left)):
            cells = _literal_cells(
                seq, constants, owner, typed, is_builtin=is_builtin)
            times = _const_int(count, constants, owner)
            if cells is not None and times is not None:
                out = list(cells) * times
                return tuple(out) if typed and isinstance(seq, ast.Tuple) else out
    if isinstance(node, ast.ListComp):
        if len(node.generators) != 1 or node.generators[0].ifs:
            return None
        length = _literal_range(
            node.generators[0].iter, constants, owner,
            is_builtin=is_builtin)
        value = _const_int(node.elt, constants, owner)
        if length is not None and value is not None:
            return [value] * length
    return None


def _shape_of(node, constants=None, resolve=None, shadowed=(),
              is_builtin=None, owner=None):
    """Dimensions of the value a source expression builds, or ``()``.

    ``resolve`` says what a name stands for, and without it only what the
    source writes down is measurable. That is enough to measure the arrays a
    model declares, which has to happen before any of it is lowered, and the
    lowering passes a resolver so that the same rules cover a name as well.
    """
    constants = constants or {}
    if is_builtin is None:
        # A spelling is not authority for a builtin.  Production callers pass
        # the lexical resolver; callers without one get no builtin folds.
        is_builtin = (lambda _name: False)
    shape = (lambda n: _shape_of(n, constants, resolve, shadowed,
                                 is_builtin, owner))
    if isinstance(node, (ast.List, ast.Tuple)):
        inner = [shape(e) for e in node.elts]
        same = inner[0] if inner and all(s == inner[0] for s in inner) else ()
        return (len(node.elts),) + same
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        # ``[0] * n`` is a sequence of ``n`` elements, which is how a model
        # says how large a buffer it carries.
        for seq, count in ((node.left, node.right), (node.right, node.left)):
            inner = shape(seq)
            times = _const_int(count, constants, owner)
            if inner and times is not None:
                return (inner[0] * times,) + inner[1:]
        return ()
    if isinstance(node, ast.Call) \
            and getattr(node.func, "id", None) == "range" \
            and is_builtin("range"):
        args = [_const_int(a, constants, owner) for a in node.args]
        if args and not any(a is None for a in args):
            return (len(range(*args)),)
        return ()
    if isinstance(node, ast.Call) \
            and getattr(node.func, "id", None) == "reversed" \
            and is_builtin("reversed") and len(node.args) == 1:
        # Reversing does not change the shape. Admission lists `reversed` among the
        # iterables a loop may take, and nothing here measured it, so a loop over one was
        # refused as "a sequence of unknown length" when the length is exactly its
        # argument's. The message was the wrong one rather than the refusal being wrong,
        # which for a refusal is the whole of its value.
        return _shape_of(node.args[0], constants, resolve, shadowed,
                         is_builtin, owner)
    if isinstance(node, ast.ListComp):
        gen = node.generators[0] if len(node.generators) == 1 else None
        if gen is not None and not gen.ifs:
            outer = shape(gen.iter)
            if outer:
                return (outer[0],) + shape(node.elt)
        return ()
    if isinstance(node, ast.Subscript):
        inner = shape(node.value)
        if not isinstance(node.slice, ast.Slice):
            # Reading an element leaves the shape of what was read.
            return inner[1:] if inner else ()
        sl = node.slice
        lo = 0 if sl.lower is None else _const_int(sl.lower, constants, owner)
        hi = (inner[0] if inner else None) if sl.upper is None \
            else _const_int(sl.upper, constants, owner)
        if lo is not None and hi is not None:
            return (max(0, hi - lo),) + (inner[1:] if inner else ())
        return ()
    if resolve is not None and isinstance(node, (ast.Name, ast.Attribute)):
        return resolve(node)
    return ()


def _module_capture_names(tree):
    """Top-level names bound by a match pattern or `except ... as` capture,
    which are runtime values the frontend cannot fold (review 51). The walk
    does not descend into functions or classes -- those are their own
    scopes."""
    out = set()
    for node in tree.body:
        out.update(_statement_bindings(node)[1])
    return out


def _definition_time_has_call(node):
    """Whether evaluating a module/class statement may invoke Python code.

    Function bodies and lambda bodies execute later.  Their decorators,
    defaults and annotations execute now, as do class bodies.  A call in the
    latter set can rebind a module name through ``global`` or mutate a class
    namespace through an opaque callback, so declaration facts do not survive
    it unless a later plain assignment establishes them again.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        expressions = (list(node.decorator_list) + list(node.args.defaults)
                       + [d for d in node.args.kw_defaults if d is not None]
                       + [a.annotation
                          for a in (list(node.args.posonlyargs)
                                    + list(node.args.args)
                                    + list(node.args.kwonlyargs))
                          if a.annotation is not None]
                       + ([node.args.vararg.annotation]
                          if (node.args.vararg is not None
                              and node.args.vararg.annotation is not None)
                          else [])
                       + ([node.args.kwarg.annotation]
                          if (node.args.kwarg is not None
                              and node.args.kwarg.annotation is not None)
                          else [])
                       + ([node.returns] if node.returns is not None else []))
        return any(_definition_time_has_call(expr) for expr in expressions)
    if isinstance(node, ast.Lambda):
        expressions = (list(node.args.defaults)
                       + [d for d in node.args.kw_defaults if d is not None])
        return any(_definition_time_has_call(expr) for expr in expressions)
    if isinstance(node, (ast.Call, ast.NamedExpr, ast.Global, ast.Nonlocal)):
        return True
    return any(_definition_time_has_call(child)
               for child in ast.iter_child_nodes(node))


def _declaration_bound_names(tree):
    """Names that can shadow builtins while module declarations execute.

    Final runtime bindings alone are insufficient: a name can shadow a
    builtin during initialization and be deleted before the module returns.
    A called helper can also introduce it with an explicit global write.
    """
    return frozenset(name for stmt in tree.body for name in _statement_bindings(stmt)[0]) | {
        name for node in ast.walk(tree) if isinstance(node, ast.Global) for name in node.names}


def _module_constant_contract(tree, imported=None, *, budget=None):
    """Known module integers and literal collections, in execution order.

    A function body reads the module after module initialization, whereas a
    class body and a function default read it when their defining statement
    executes.  Keeping only one final dictionary confuses those two times
    (``N=2; class M: def __init__(..., n=N); N=3``).  This small ordered
    transfer is the authority for both: ``final`` serves runtime function
    bodies and ``before[id(stmt)]`` serves definition-time declarations.

    Only straight-line integer assignments and approved imported constants
    establish facts. Other bindings remove a fact; an executed call removes
    every name the source declares writable through ``global``. Losing a fact
    can only cause a typed refusal; it cannot leave a stale value to fold.
    """
    if tree is None:
        return {}, {}, frozenset()
    budget = budget if budget is not None else SourceValueBudget()
    imported = imported or {}
    # Within the admitted, non-reflective source subset a Python call can
    # rebind a module name only through an explicit ``global`` declaration.
    # Track those names once.  Clearing every fact at every call discarded
    # unrelated immutable constants (for example, a QNaN bit pattern before
    # building an independent LUT) and turned a value we had read exactly
    # into a spurious namespace refusal.
    global_writes = {
        name
        for node in ast.walk(tree) if isinstance(node, ast.Global)
        for name in node.names
    }
    module_bound = _declaration_bound_names(tree)
    current, collections, before = {}, set(), {}
    for stmt in tree.body:
        before[id(stmt)] = dict(current)
        bound = set(_statement_bindings(stmt)[0])
        value_binding = None
        collection_binding = None
        grouped_bindings = ()
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)):
            value_binding = (stmt.targets[0].id,
                             _const_int(stmt.value, current))
            if isinstance(stmt.value, (ast.List, ast.Tuple, ast.Dict)):
                collection_binding = stmt.targets[0].id
        elif isinstance(stmt, ast.Assign):
            grouped_bindings = _closed_assignment_bindings(
                stmt.targets, stmt.value,
                lambda node: current.get(node.id) if isinstance(node, ast.Name) else None,
                builtin_range='range' not in module_bound,
                budget=budget) or ()
        elif (isinstance(stmt, ast.AnnAssign)
              and isinstance(stmt.target, ast.Name)
              and stmt.value is not None):
            value_binding = (stmt.target.id,
                             _const_int(stmt.value, current))
            if isinstance(stmt.value, (ast.List, ast.Tuple, ast.Dict)):
                collection_binding = stmt.target.id

        for name in bound:
            current.pop(name, None)
            collections.discard(name)

        if isinstance(stmt, ast.ImportFrom) and stmt.module == "fp_lib":
            for alias in source_intrinsics.concrete_import_aliases(stmt):
                value = imported.get(alias.name)
                if value is not None:
                    current[alias.asname or alias.name] = value
        elif value_binding is not None and value_binding[1] is not None:
            current[value_binding[0]] = value_binding[1]
        for name, frozen in grouped_bindings:
            current.pop(name, None)
            if frozen.kind in {'int', 'bool'}:
                current[name] = frozen.payload
        if collection_binding is not None:
            collections.add(collection_binding)

        if _definition_time_has_call(stmt):
            for name in global_writes:
                current.pop(name, None)
                collections.discard(name)
    return current, before, frozenset(collections)


def _argument_constant_defaults(fn, constants):
    """Integer defaults of one function in its definition-time namespace."""
    out = {}
    positional = list(fn.args.posonlyargs) + list(fn.args.args)
    offset = len(positional) - len(fn.args.defaults)
    for index, arg in enumerate(positional):
        if index < offset:
            continue
        value = _const_int(fn.args.defaults[index - offset], constants)
        if value is not None:
            out[arg.arg] = value
    for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        if default is None:
            continue
        value = _const_int(default, constants)
        if value is not None:
            out[arg.arg] = value
    return out


def _frozen_target_bindings(target, frozen, *, budget):
    """Bind a closed RHS in Python target order, without re-evaluating it."""
    budget.spend(target)
    if frozen is None:
        return None
    if isinstance(target, ast.Name):
        return [(target.id, frozen)]
    if (isinstance(target, (ast.Tuple, ast.List))
            and frozen.kind in {"tuple", "list"}
            and len(target.elts) == len(frozen.payload)):
        rows = [_frozen_target_bindings(child, value, budget=budget)
                for child, value in zip(target.elts, frozen.payload)]
        if all(row is not None for row in rows):
            return [item for row in rows for item in row]
    return None


def _closed_assignment_bindings(targets, value, value_of, *, builtin_range, budget):
    """One closed RHS, then Python's ordered bindings in any namespace.

    A built-in range is materialized only for fixed unpacking, never treated
    as a tuple-valued binding. Check the target arity before constructing its
    elements; a huge range cannot allocate merely to discover wrong arity.
    """
    if (builtin_range and isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name) and value.func.id == 'range'
            and not value.keywords and 1 <= len(value.args) <= 3
            and all(isinstance(target, (ast.Tuple, ast.List)) for target in targets)):
        arguments = [evaluate_frozen(arg, value_of, budget=budget) for arg in value.args]
        if any(arg is None or arg.kind not in {'int', 'bool'} for arg in arguments):
            return None
        try:
            sequence = range(*(arg.payload for arg in arguments))
            size = len(sequence)
        except (ValueError, OverflowError):
            return None
        if any(len(target.elts) != size for target in targets):
            return None
        budget.spend(value, size)
        frozen = FrozenValue('tuple', tuple(FrozenValue('int', item) for item in sequence))
    else:
        frozen = evaluate_frozen(value, value_of, budget=budget)
    rows = [_frozen_target_bindings(target, frozen, budget=budget) for target in targets]
    return ([item for row in rows for item in row]
            if all(row is not None for row in rows) else None)


def _class_declaration_contract(classnode, module_constants, written=(), *, budget,
                                module_bound=()):
    """Final integer class attributes and method defaults, in body order."""
    module_values = dict(module_constants)
    values, defaults, class_bound = {}, {}, set()
    for stmt in classnode.body:
        namespace = {**module_values, **values}
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults[id(stmt)] = _argument_constant_defaults(stmt, namespace)

        bound = set(_statement_bindings(stmt)[0])
        value_binding = None
        grouped_bindings = ()
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)):
            value_binding = (stmt.targets[0].id,
                             _const_int(stmt.value, namespace))
        elif isinstance(stmt, ast.Assign):
            grouped_bindings = _closed_assignment_bindings(
                stmt.targets, stmt.value, lambda node, namespace=namespace: namespace.get(node.id)
                if isinstance(node, ast.Name) else None,
                builtin_range='range' not in class_bound and 'range' not in module_bound,
                budget=budget) or ()
        elif (isinstance(stmt, ast.AnnAssign)
              and isinstance(stmt.target, ast.Name)
              and stmt.value is not None):
            value_binding = (stmt.target.id,
                             _const_int(stmt.value, namespace))

        for name in bound:
            values.pop(name, None)
        class_bound.update(bound)
        if value_binding is not None and value_binding[1] is not None:
            values[value_binding[0]] = value_binding[1]
        for name, frozen in grouped_bindings:
            values.pop(name, None)
            if frozen.kind in {"int", "bool"}:
                values[name] = frozen.payload
        if _definition_time_has_call(stmt):
            # A call can rebind module globals, but it cannot rebind an
            # immutable integer already stored in the not-yet-created class
            # namespace unless that namespace itself escapes.  The certified
            # subset exposes no such mapping, so retain class facts and drop
            # only the module fallback facts.
            module_values.clear()
    return ({name: value for name, value in values.items()
             if name not in set(written)}, defaults)


def _library_imports(tree):
    """What each name bound by an approved import stands for: bound name to real name.

    `from fp_lib import fp32_add` binds `fp32_add` and `from fp_lib import fp32_add as
    f` binds `f`, and both name the same routine. The two halves used to be read in two
    places, one of which kept only the bound name and the other only the real one, so an
    aliased import bound a name that resolved to nothing. Which is a rule of Python and
    not a fact about this library, so it is answered once, here.
    """
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "fp_lib":
            for alias in source_intrinsics.concrete_import_aliases(node):
                out[alias.asname or alias.name] = alias.name
    return out


def _approved_primitives(tree):
    """Names the model may call because it imported them from the approved library.

    The names as the model binds them, since that is what a call site says. What each
    one stands for is `_library_imports`, and resolving it to source is
    `ScopeResolver`: the library is ordinary source and its routines are inlined,
    so a name here is a name the frontend must be able to find a body for.
    """
    return set(_library_imports(tree))


def _library_tree():
    """The approved library's source, parsed, or nothing if it is not there."""
    from . import LIBRARY_DIR

    try:
        return ast.parse((LIBRARY_DIR / "fp_lib.py").read_text())
    except OSError:
        return None


def _statement_bindings(node):
    """Names one statement binds in its surrounding lexical scope.

    Nested function, class, lambda, and comprehension locals do not leak into
    their containing namespace.  Definition-time expressions and assignment
    expressions in comprehensions do execute in the surrounding scope, so they
    are still visited.  This is the shared binding walker for module and class
    final-binding rules.
    """
    bound, captures = set(), set()

    def visit(current):
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(current.name)
            for expr in (list(current.decorator_list)
                         + list(current.args.defaults)
                         + [d for d in current.args.kw_defaults
                            if d is not None]
                         + ([current.returns]
                            if current.returns is not None else [])):
                visit(expr)
            return
        if isinstance(current, ast.ClassDef):
            bound.add(current.name)
            for expr in (list(current.decorator_list) + list(current.bases)
                         + [kw.value for kw in current.keywords]):
                visit(expr)
            return
        if isinstance(current, ast.Lambda):
            return
        if isinstance(current, (ast.ListComp, ast.SetComp,
                                ast.DictComp, ast.GeneratorExp)):
            # Generator targets are local to the comprehension.  Walrus
            # targets in its expressions bind the surrounding function or
            # module and are found by visiting those expressions.
            for gen in current.generators:
                visit(gen.iter)
                for guard in gen.ifs:
                    visit(guard)
            if isinstance(current, ast.DictComp):
                visit(current.key)
                visit(current.value)
            else:
                visit(current.elt)
            return
        if isinstance(current, (ast.Import, ast.ImportFrom)):
            for alias in current.names:
                bound.add(alias.asname or alias.name.split(".")[0])
            return
        if isinstance(current, ast.MatchAs) and current.name or isinstance(current, ast.MatchStar) and current.name:
            bound.add(current.name)
            captures.add(current.name)
        elif isinstance(current, ast.MatchMapping) and current.rest:
            bound.add(current.rest)
            captures.add(current.rest)
        elif isinstance(current, ast.ExceptHandler) and current.name:
            bound.add(current.name)
            captures.add(current.name)
        elif isinstance(current, ast.Name) and isinstance(
                current.ctx, (ast.Store, ast.Del)):
            bound.add(current.id)
        for child in ast.iter_child_nodes(current):
            visit(child)

    visit(node)
    return frozenset(bound), frozenset(captures)


def class_bound_names(classnode):
    """The class body's final names, excluding nested scopes and deleted names."""
    out = set()
    for statement in classnode.body:
        if isinstance(statement, ast.Delete):
            out.difference_update(target.id for target in statement.targets
                                  if isinstance(target, ast.Name))
            continue
        if isinstance(statement, ast.AnnAssign) and statement.value is None:
            continue
        out.update(_statement_bindings(statement)[0])
    return frozenset(out)


def _module_binding_pass(tree):
    """Top-level bindings by Python's own rules, in one ordered pass.

    Review 46: `ast.walk` mixed module functions, the model's methods, other
    classes' methods and decorated or rebound defs into one namespace, so a
    bare call inlined a method Python would NameError on, a decorated def
    was inlined as its body, and a module def named `reversed` was lowered
    as the builtin. Here a bare name resolves only to its **final**
    top-level binding: an undecorated, unconditional `def` not later
    rebound. Everything else a top-level statement binds -- classes,
    imports, assignments, walruses, decorated or conditional defs -- takes
    the name away from inlining, and the full bound-name set is what the
    builtin spellings must check themselves against.
    """
    inlinable: dict = {}
    imports: dict = {}
    intrinsics: dict = {}
    bound = set()

    def taken(name):
        bound.add(name)
        inlinable.pop(name, None)
        imports.pop(name, None)
        intrinsics.pop(name, None)

    def deleted(name):
        # `del name` at module level unbinds it: it is neither inlinable
        # nor a bound name a later call resolves (review 49)
        bound.discard(name)
        inlinable.pop(name, None)
        imports.pop(name, None)
        intrinsics.pop(name, None)

    for node in tree.body:
        if isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    deleted(target.id)
            continue
        if isinstance(node, ast.FunctionDef):
            bound.add(node.name)
            imports.pop(node.name, None)
            intrinsics.pop(node.name, None)
            if node.decorator_list or node.name in ("process", "reset") \
                    or node.name.startswith("process_"):
                inlinable.pop(node.name, None)
            else:
                inlinable[node.name] = node
            for decorator in node.decorator_list:
                for sub in ast.walk(decorator):
                    if isinstance(sub, ast.Name) and isinstance(
                            sub.ctx, ast.Store):
                        taken(sub.id)
            continue
        if isinstance(node, ast.ClassDef):
            taken(node.name)
            for decorator in list(node.decorator_list) + list(node.bases):
                for sub in ast.walk(decorator):
                    if isinstance(sub, ast.Name) and isinstance(
                            sub.ctx, ast.Store):
                        taken(sub.id)
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                taken(alias.asname or alias.name.split(".")[0])
            continue
        if isinstance(node, ast.ImportFrom):
            for alias in source_intrinsics.concrete_import_aliases(node):
                taken(alias.asname or alias.name)
                if node.module == "fp_lib":
                    # the final binding so far is the approved import; a
                    # later top-level binder takes the name back
                    imports[alias.asname or alias.name] = alias.name
                elif (not node.level
                      and node.module == source_intrinsics.MODULE
                      and alias.name in source_intrinsics.names()):
                    # Unlike fp_lib, this is not source to inline.  The final
                    # binding names one registry primitive directly.
                    intrinsics[alias.asname or alias.name] = alias.name
            continue
        for name in _statement_bindings(node)[0]:
            taken(name)
    return inlinable, bound, imports, intrinsics


#: Spellings the pipeline may treat as builtins anywhere, once resolution
#: proves nothing shadows them.
KNOWN_BUILTIN_SPELLINGS = source_intrinsics.BUILTIN_NAMES


def _module_source_callables(tree):
    """Possible source bodies behind module names, independent of inlining.

    A conditional rebind does not prove the previous function cannot run.
    Referencing a source class can also invoke methods implicitly (comparison,
    iteration, construction), so its method bodies belong to this declaration
    closure. This does not certify dynamic dispatch for symbolic lowering.
    Function/class-local bindings never enter the module namespace.
    """
    candidates = {}
    aliases = []
    pending = list(reversed(tree.body))
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef):
            candidates.setdefault(node.name, []).append(node)
        elif isinstance(node, ast.ClassDef):
            methods = [child for child in node.body if isinstance(child, ast.FunctionDef)]
            candidates.setdefault(node.name, []).extend(methods)
        elif not isinstance(node, (ast.AsyncFunctionDef, ast.Lambda)):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Name):
                targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                aliases.extend((target.id, node.value.id) for target in targets
                               if isinstance(target, ast.Name))
            pending.extend(reversed(tuple(ast.iter_child_nodes(node))))
    # Alias edges only expose existing source bodies; no binding is certified
    # for inlining. The finite union also handles alias cycles without recursion.
    changed = True
    while changed:
        changed = False
        for name, origin in aliases:
            bodies = candidates.setdefault(name, [])
            for body in candidates.get(origin, ()):
                if body not in bodies:
                    bodies.append(body)
                    changed = True
    return {name: tuple(bodies) for name, bodies in candidates.items()}


class ScopeResolver:
    """Lexical name resolution, computed once and consumed everywhere.

    Review 47: the call lowering, the loop and shape analyses and the alias
    authority each kept a flat table, so a local shadowing a module helper,
    a parameter named range, a nested def leaking across functions, an
    instance attribute shadowing a method and a module rebinding of len all
    resolved to something Python would not run. This resolver answers by
    Python's rules: a name bound anywhere in a function is local throughout
    that function; a local resolves to a nested def only when that def is
    the name's unique, unconditional, undecorated, direct-child binding;
    a fixed local helper capture resolves through that same authority only
    when its binding dominates all uses and the reference cannot escape;
    a free name resolves to the final module binding, inlinable only when
    that is a unique undecorated unconditional top-level def; a builtin
    spelling means the builtin only when neither scope binds it; and
    `self.m` resolves to the chosen class's method only when no instance
    field of that name can shadow it. Anything else is opaque, which every
    consumer treats as a typed refusal or a conservative pass-through.
    """

    def __init__(self, model, *, source_value_budget=None, _library_trees=None):
        self.model = model
        self.source_value_budget = (source_value_budget if
                                    source_value_budget is not None else
                                    SourceValueBudget())
        if not isinstance(self.source_value_budget, SourceValueBudget):
            raise TypeError("source_value_budget must be a SourceValueBudget")
        inlinable, bound, imports, intrinsic_imports = _module_binding_pass(
            model.tree)
        self.module_functions = dict(inlinable)
        self._module_source_callables = _module_source_callables(model.tree)
        from .class_binding import dataclass_factories

        self._constructor_factories = {
            node.name: dataclass_factories(model.tree, node)
            for node in model.tree.body if isinstance(node, ast.ClassDef) and node.decorator_list}
        self.module_bound = frozenset(bound)
        self._declaration_bound = _declaration_bound_names(model.tree)
        self.intrinsic_imports = dict(intrinsic_imports)
        self.declared_intrinsics = source_intrinsics.declared_bindings(
            model.tree)
        # Module names whose binding is a runtime capture -- a match pattern
        # or an `except ... as` -- rather than a value the frontend can read
        # (review 51): reading one as a scalar has no fixed value, so it is
        # refused, while an ordinary module value (a lookup table) is not.
        self.module_capture_bound = _module_capture_names(model.tree)
        # The approved library is ordinary source and its routines inline
        # like any helper -- but lexically: a model name bound by a
        # top-level `from fp_lib import ...` and never rebound resolves to
        # the library def of the imported name, and a library routine's
        # own free names resolve in the *library's* module scope, not the
        # model's (the old flat table merged both and could not tell).
        self._lib_functions: dict = {}
        self._lib_bound: frozenset = frozenset()
        self._lib_fn_ids: frozenset = frozenset()
        library = None
        if imports:
            if _library_trees is None:
                library = _library_tree()
            else:
                # A lowering session may share read-only source occurrences,
                # including dependency bodies, across its two value passes.
                # Resolver state below remains local to this pass.
                if "fp_lib" not in _library_trees:
                    _library_trees["fp_lib"] = _library_tree()
                library = _library_trees["fp_lib"]
        # Fixed integers, kept by owner (review 51): the model module, the
        # library module and the chosen class each have their own, so a name
        # defined in more than one folds to the right value in each body and
        # a name bound to something that is not a supported integer is a
        # typed refusal rather than a free variable. `module_bound` is every
        # name the module binds at all, so a module-bound non-constant is
        # told apart from a genuinely free name.
        self.lib_consts: dict = {}
        self._library_before: dict = {}
        # A constant a top-level `from fp_lib import W` brings into the model
        # module is a module-scope name of the model, so it folds in model
        # code and in a class body's right-hand side (review 52: `class M:
        # K = W` was refused).
        self.imported_consts: dict = {}
        if library is not None:
            lib_inlinable, lib_bound, _, _ = _module_binding_pass(library)
            self._lib_functions = dict(lib_inlinable)
            self._lib_bound = frozenset(lib_bound)
            self._lib_fn_ids = frozenset(
                id(node) for node in ast.walk(library)
                if isinstance(node, ast.FunctionDef))
            self.lib_consts, self._library_before, _ = _module_constant_contract(
                library, budget=self.source_value_budget)
            for bound_name, real in imports.items():
                node = lib_inlinable.get(real)
                if node is not None:
                    self.module_functions.setdefault(bound_name, node)
        # Module runtime values and definition-time values are distinct
        # authorities.  A function body sees the final module namespace; the
        # chosen class body and its method defaults see the snapshot at the
        # class statement.  Imported fp_lib integers participate in the same
        # ordered transfer rather than being merged into a flat dictionary.
        (self.model_consts, self._module_before,
         self.module_collections) = _module_constant_contract(
             model.tree, self.lib_consts, budget=self.source_value_budget)
        self.imported_consts = {
            bound_name: self.model_consts[bound_name]
            for bound_name, real in imports.items()
            if real in self.lib_consts and bound_name in self.model_consts}
        class_module = self._module_before.get(id(model.cls), {})
        self.class_consts, self._method_defaults = (
            _class_declaration_contract(
                model.cls, class_module, model.state_contract(),
                budget=self.source_value_budget, module_bound=self._declaration_bound))
        # Every AST node's innermost enclosing function, so a name folds in
        # the scope it actually occurs in -- a constructor parameter is that
        # constructor's local, not the module's constant (review 52: one
        # `_decl_scope` resolved __init__ and reset alike).
        self._enclosing: dict = {}
        self._parents: dict = {}
        self._index_enclosing(model.tree, None)
        if library is not None:
            self._index_enclosing(library, None)
        self.instance_attrs = frozenset(model.state_contract())
        self.methods = {}
        self.properties = {}
        receiver_methods = []
        for method in (list(model.helpers.values())
                       + list(model.steps.values())
                       + [m for m in (model.reset, model.constructor)
                          if m is not None]):
            kind = method_kind(method)
            if kind in PROPERTY_ACCESSORS:
                self.properties.setdefault(method.name, {})[PROPERTY_ACCESSORS[kind]] = method
            else:
                self.methods[method.name] = method
            if kind == "instance" or kind in PROPERTY_ACCESSORS:
                receiver_methods.append(method)
        # `self` is the receiver only in a method of the model class; in a
        # nested or module function it is an ordinary parameter (review 49:
        # `def f(self): return 7` dropped the parameter and inlined f()).
        self._method_ids = frozenset(id(m) for m in receiver_methods)
        self._class_method_ids = frozenset(id(m) for m in self.methods.values()
                                           if method_kind(m) == "classmethod")
        self._locals: dict = {}
        self._nested: dict = {}
        self._nested_sources: dict = {}
        self._call_aliases: dict = {}
        self._resolved_call_aliases: dict = {}
        self._rebinds_self: dict = {}
        self._rebound_parameters: dict = {}
        self._written_parameters: dict = {}
        self._pure_functions: dict = {}
        # Python resolves a nested function's free names through the
        # enclosing *functions* (class scopes are skipped): a sibling
        # nested def is a local of the enclosing scope, captured by
        # closure. Indexed here so resolve_call can walk the chain.
        self._parent: dict = {}
        self._index_parents(model.tree)
        if library is not None:
            self._index_parents(library)
        # Declaration-time constants are interpreted in execution order for
        # the selected constructor and reset.  This is deliberately not a
        # whole-module spelling scan: a constructor local may be rebound, and
        # another class's constructor is not part of this model at all.
        self._point_values: dict = {}
        self.self_consts = self._declaration_constants()
        # Fields written by another component of a closed transition are not
        # constants of this procedure, even when this procedure only reads
        # them.  ``lower_procedure`` installs that boundary after it has used
        # the construction values to establish representation shapes.
        self._externally_dynamic_state = frozenset()
        # Constant tables are admitted only under a closed, read-only use
        # contract.  Keep both halves: the tables the lowerer may encode and
        # literal candidates whose use violated the contract, so a read can
        # refuse for this rule rather than falling through to an unrelated
        # unknown-length error.
        self.class_values, self.class_names = _class_namespace_contract(self)
        (self.tables, self.table_source_values,
         self.invalid_tables) = _module_table_contract(
             self, budget=self.source_value_budget)
        self.class_tables = {
            name: value for name, value in self.class_values.items()
            if type(value) in (dict, list, tuple, str)
            and name not in model.state_contract()}
        self.static_field_values = dict(self.class_tables)

    def _index_parents(self, tree):
        def visit(node, enclosing):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef,
                                      ast.AsyncFunctionDef)):
                    self._parent[id(child)] = enclosing
                    visit(child, child)
                elif isinstance(child, ast.Lambda):
                    continue
                else:
                    # class bodies do not take part in the lexical chain
                    visit(child, enclosing)
        visit(tree, None)

    def _walk_same_scope(self, root):
        """Yield nodes in ``root`` without entering a nested lexical scope."""
        stack = [root]
        first = True
        while stack:
            node = stack.pop()
            if not first and isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.Lambda, ast.ClassDef)):
                continue
            first = False
            yield node
            children = (node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        else execution_children(node))
            stack.extend(reversed(children))

    def _definition_defaults(self, fn):
        """Constant defaults frozen when this class method was defined."""
        return dict(self._method_defaults.get(id(fn), {}))

    def helper_default(self, fn, name, expression):
        """A frozen scalar default, never evaluated in the caller namespace.

        Module and class integers use the existing ordered declaration facts.
        Closed scalar literals also work in nested defs. A captured dynamic or
        shared mutable default needs a represented definition event; declining
        it is essential rather than re-evaluating it at each call.
        """
        if isinstance(expression, ast.Constant) and type(expression.value) in (
                int, bool, str, type(None)):
            return expression
        if id(fn) in self._method_defaults:
            value = self._method_defaults[id(fn)].get(name)
        else:
            before = (self._library_before if id(fn) in self._lib_fn_ids
                      else self._module_before)
            value = _const_int(expression, before.get(id(fn), {}))
        if value is None:
            return None
        return ast.copy_location(ast.Constant(value=value), expression)

    def _declaration_constants(self):
        """Fixed integer fields, with constructor/reset program points.

        This is a small abstract execution, not a second Python interpreter.
        It handles the straight-line declarations the frontend is willing to
        use as proof facts.  A complex statement invalidates every local or
        field it may write; a call invalidates the carried field facts.  Thus
        unsupported control can reduce precision but cannot leave a stale
        value available for folding.
        """
        fields = dict(self.class_consts)

        effect_cache = {}

        def target_fields(target):
            if isinstance(target, (ast.Tuple, ast.List)):
                out = set()
                for element in target.elts:
                    out.update(target_fields(element))
                return out
            if isinstance(target, ast.Starred):
                return target_fields(target.value)
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                return {target.attr}
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and isinstance(target.value.value, ast.Name)
                    and target.value.value.id == "self"):
                return {target.value.attr}
            return set()

        def method_effect(fn, active=frozenset()):
            """Fields a resolved self-call may write, or None if opaque."""
            key = id(fn)
            if key in effect_cache:
                return effect_cache[key]
            if key in active:
                return None
            writes = set()
            for node in self._walk_same_scope(fn):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign,
                                       ast.NamedExpr, ast.For,
                                       ast.AsyncFor)):
                    targets = [node.target]
                elif isinstance(node, ast.Delete):
                    targets = node.targets
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    targets = [item.optional_vars for item in node.items
                               if item.optional_vars is not None]
                for target in targets:
                    writes.update(target_fields(target))
                if not isinstance(node, ast.Call):
                    continue
                if (isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "self"
                        and node.func.attr in self.methods):
                    nested = method_effect(
                        self.methods[node.func.attr], active | {key})
                    if nested is None:
                        return None
                    writes.update(nested)
                else:
                    # The declaration analysis does not assign effects to an
                    # opaque Python call.  Dropping all carried field facts is
                    # the fail-closed answer.
                    return None
            result = frozenset(writes)
            effect_cache[key] = result
            return result

        def invalidate(call):
            effect = None
            if (isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "self"
                    and call.func.attr in self.methods):
                effect = method_effect(self.methods[call.func.attr])
            if effect is None:
                fields.clear()
            else:
                for name in effect:
                    fields.pop(name, None)

        def record(node, locals_):
            snapshot = (dict(locals_), dict(fields))
            for sub in self._walk_same_scope(node):
                self._point_values[id(sub)] = snapshot

        def value_of(expr, locals_):
            def owner(node):
                if isinstance(node, ast.Name):
                    if node.id in locals_:
                        return locals_[node.id]
                    kind, value = self.resolve_const(None, node.id)
                    return value if kind == "const" else None
                if (isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "self"):
                    return fields.get(node.attr)
                return None
            return _const_int(expr, {}, owner)

        for fn in (self.model.constructor, self.model.reset):
            if fn is None:
                continue
            locals_ = self._definition_defaults(fn)
            for stmt in fn.body:
                record(stmt, locals_)
                if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                    targets = (stmt.targets if isinstance(stmt, ast.Assign)
                               else [stmt.target])
                    rhs = getattr(stmt, "value", None)
                    if rhs is not None:
                        for sub in self._walk_same_scope(rhs):
                            if isinstance(sub, ast.Call):
                                # Calls are evaluated before the assignment.
                                # A resolved self-call invalidates exactly its
                                # reachable writes; an opaque call invalidates
                                # every carried field fact.
                                invalidate(sub)
                    value = value_of(rhs, locals_) if rhs is not None else None
                    simple = len(targets) == 1
                    target = targets[0] if simple else None
                    if isinstance(target, ast.Name):
                        if value is None:
                            locals_.pop(target.id, None)
                        else:
                            locals_[target.id] = value
                        continue
                    if (isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"):
                        if value is None:
                            fields.pop(target.attr, None)
                        else:
                            fields[target.attr] = value
                        continue

                # Anything not handled above is conservative.  Remove every
                # name/field it can bind in this function's scope.  A call may
                # reach arbitrary selected helpers, so no carried field fact
                # survives it.
                for sub in self._walk_same_scope(stmt):
                    if isinstance(sub, ast.Name) and isinstance(
                            sub.ctx, (ast.Store, ast.Del)):
                        locals_.pop(sub.id, None)
                    if (isinstance(sub, ast.Attribute)
                            and isinstance(sub.value, ast.Name)
                            and sub.value.id == "self"
                            and isinstance(sub.ctx, (ast.Store, ast.Del))):
                        fields.pop(sub.attr, None)
                    if isinstance(sub, ast.Call):
                        invalidate(sub)

        # A field the step can write is state, not a declaration constant.
        step_writes = set(self.model.step_state_contract())
        return {name: value for name, value in fields.items()
                if name not in step_writes}

    def _scope(self, fn):
        key = id(fn)
        if key in self._locals:
            return
        names = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args)}
        if id(fn) in self._method_ids:
            # the receiver, not a local; a nested/module function keeps a
            # parameter that happens to be spelled `self`
            names.discard("self")
        for extra in (fn.args.vararg, fn.args.kwarg):
            if extra is not None:
                names.add(extra.arg)
        names |= {a.arg for a in fn.args.kwonlyargs}
        # A comprehension's targets live in the comprehension's own scope
        # (review 48: a target named like a module helper made the helper
        # unresolvable); a walrus anywhere still binds the function.
        comp_targets = set()
        unevaluated = {id(node) for annotation in local_annotations(fn)
                       for node in ast.walk(annotation)}
        for node in ast.walk(fn):
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                                 ast.GeneratorExp)):
                for gen in node.generators:
                    for sub in ast.walk(gen.target):
                        comp_targets.add(id(sub))
        bind_counts: dict = {}
        mutated_roots: set = set()
        nested: dict = {}
        nested_sources: dict = {}
        deleted: set = set()
        hoisted_globals = set()
        rebinds_self = False
        todo = list(fn.body)
        direct = set(map(id, fn.body))

        def bind(name):
            nonlocal rebinds_self
            if name == "self":
                # `self` bound by anything is an ordinary local; every rule
                # reading `self.x` as the model is then wrong (review 48)
                rebinds_self = True
            names.add(name)
            bind_counts[name] = bind_counts.get(name, 0) + 1

        while todo:
            node = todo.pop()
            if isinstance(node, ast.AnnAssign) and node.value is None:
                # Declarations affect lexical lookup, but do not rebind an
                # existing local/parameter/def or write through a target.
                if isinstance(node.target, ast.Name):
                    if node.simple and (node.target.id != "self" or id(fn) not in self._method_ids):
                        names.add(node.target.id)
                else:
                    todo.extend(execution_children(node))
                # Walrus bindings in an unevaluated annotation still belong
                # to this lexical scope (CPython's symbol-table rule).
                todo.append(node.annotation)
                continue
            if isinstance(node, (ast.Attribute, ast.Subscript)) \
                    and isinstance(node.ctx, (ast.Store, ast.Del)):
                root = node
                while isinstance(root, (ast.Attribute, ast.Subscript)):
                    root = root.value
                if isinstance(root, ast.Name):
                    mutated_roots.add(root.id)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bind(node.name)
                if isinstance(node, ast.FunctionDef):
                    nested_sources.setdefault(node.name, []).append(node)
                if id(node) in direct and not node.decorator_list \
                        and isinstance(node, ast.FunctionDef):
                    nested[node.name] = node
                # its body is its own scope
                todo.extend(node.decorator_list)
                continue
            if isinstance(node, ast.ClassDef):
                # a class statement binds its name locally (review 49: a
                # forward local class made a same-named call resolve to a
                # module helper); its body is its own scope
                bind(node.name)
                todo.extend(node.decorator_list)
                todo.extend(node.bases)
                todo.extend(kw.value for kw in node.keywords)
                continue
            if isinstance(node, ast.Lambda):
                continue
            if isinstance(node, ast.Global):
                hoisted_globals |= set(node.names)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # an import binds its aliases wherever the statement sits,
                # reachable or not (review 48)
                for alias in node.names:
                    bind(alias.asname or alias.name.split(".")[0])
                continue
            if isinstance(node, ast.Delete):
                # `del name` makes the name local and, after it, unbound
                # (review 49): it is a binding site for scope, and it is not
                # an inlinable def
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bind(target.id)
                        deleted.add(target.id)
                    else:
                        todo.append(target)
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) \
                    and id(node) not in comp_targets:
                if id(node) not in unevaluated:
                    bind(node.id)
                elif node.id != "self" or id(fn) not in self._method_ids:
                    names.add(node.id)
            todo.extend(ast.iter_child_nodes(node))
        names -= hoisted_globals
        self._locals[key] = frozenset(names)
        self._nested_sources[key] = {
            name: tuple(sorted(nodes, key=lambda node: (node.lineno, node.col_offset)))
            for name, nodes in nested_sources.items() if name in names}
        self._rebinds_self[key] = rebinds_self
        parameters = {
            argument.arg for argument in (
                list(fn.args.posonlyargs) + list(fn.args.args)
                + list(fn.args.kwonlyargs)
                + [arg for arg in (fn.args.vararg, fn.args.kwarg)
                   if arg is not None])
        }
        self._rebound_parameters[key] = frozenset(
            name for name in parameters if bind_counts.get(name, 0))
        self._written_parameters[key] = frozenset(
            name for name in parameters
            if bind_counts.get(name, 0) or name in mutated_roots)
        # A call may inline a nested def only when the binding is unique and
        # Python would already have executed it: the def is a statement, so
        # a name read lexically before it is not this def's (review 48's
        # `y = f()` above `def f`), and a def whose defaults or annotations
        # compute anything has effects the lowering has nowhere to put.
        # Only reads in fn's OWN lexical scope decide use-before-def; a
        # read inside a nested body is a closure capture, evaluated when
        # that inner function runs, not at this def's line (review 49: a
        # legal forward sibling closure was wrongly refused).
        first_read: dict = {}
        own = list(fn.body)
        while own:
            node = own.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.Lambda, ast.ClassDef,
                                 ast.ListComp, ast.SetComp, ast.DictComp,
                                 ast.GeneratorExp)):
                continue  # a scope of its own
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                line = getattr(node, "lineno", None)
                if line is not None:
                    first_read[node.id] = min(
                        first_read.get(node.id, line), line)
            own.extend(execution_children(node))
        self._nested[key] = {
            name: node for name, node in nested.items()
            if bind_counts.get(name) == 1
            and name not in deleted
            and _def_time_pure(node)
            and first_read.get(name, node.lineno) >= node.lineno}
        # A fixed local function reference needs no runtime value carrier.
        # Prove its binding dominates all uses, and that it is only called or
        # copied to another such binding. Closures/nonlocal writes require a
        # runtime callable environment and must not inherit this local proof.
        aliases = {}
        for stmt in fn.body:
            if isinstance(stmt, ast.Assign):
                targets = stmt.targets
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets = (stmt.target,)
            else:
                continue
            if not isinstance(stmt.value, (ast.Name, ast.Attribute)):
                continue
            if not all(isinstance(target, ast.Name) for target in targets):
                continue
            for target in targets:
                if (bind_counts.get(target.id) == 1 and target.id not in parameters
                        and target.id not in deleted and target.id not in hoisted_globals):
                    aliases[target.id] = stmt
        if aliases:
            valid = set(aliases)
            for node in ast.walk(fn):
                if id(node) in unevaluated:
                    continue
                if isinstance(node, ast.Nonlocal):
                    valid.difference_update(node.names)
                if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                        and node.id in aliases):
                    continue
                owner = self.scope_of(node)
                while owner is not None and owner is not fn:
                    self._scope(owner)
                    if node.id in self._locals[id(owner)]:
                        break  # a descendant's distinct local, not a capture
                    owner = self._parent.get(id(owner))
                if owner is not None and owner is not fn:
                    continue
                stmt = aliases[node.id]
                parent = self._parents.get(id(node))
                called = isinstance(parent, ast.Call) and parent.func is node
                copied = (isinstance(parent, (ast.Assign, ast.AnnAssign))
                          and parent.value is node and parent in aliases.values())
                if (self.scope_of(node) is not fn
                        or (node.lineno, node.col_offset) < (stmt.end_lineno, stmt.end_col_offset)
                        or not (called or copied)):
                    valid.discard(node.id)
            aliases = {name: stmt for name, stmt in aliases.items() if name in valid}
        self._call_aliases[key] = aliases

    def _resolve_call_alias(self, fn, name):
        key = (id(fn), name)
        if key not in self._resolved_call_aliases:
            self._resolved_call_aliases[key] = None
            stmt = self._call_aliases[id(fn)].get(name)
            if stmt is not None:
                value = stmt.value
                if isinstance(value, ast.Name):
                    kind, target = self.resolve_call(fn, value.id)
                    if kind in {"nested", "module", "alias"}:
                        self._resolved_call_aliases[key] = target
                elif (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                      and value.value.id == "self" and not self.is_local(fn, "self")):
                    kind, target = self.resolve_method(fn, value.attr)
                    if kind == "method":
                        self._resolved_call_aliases[key] = target
        return self._resolved_call_aliases[key]

    def call_alias_assignment(self, fn, node):
        """Target of a proved local helper capture, without rewriting source."""
        self._scope(fn)
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        target_function = None
        for target in targets:
            if not isinstance(target, ast.Name):
                return None
            stmt = self._call_aliases[id(fn)].get(target.id)
            if stmt is None or stmt.value is not node.value:
                return None
            target_function = self._resolve_call_alias(fn, target.id)
            if target_function is None:
                return None
        return target_function

    def resolve_call(self, fn, name):
        """Resolve one call through its lexical and module bindings.

        Intrinsics return ``('intrinsic', primitive_name)``.  A source name
        imported as an intrinsic but shadowed at this call site returns
        ``('intrinsic-shadowed', None)`` so lowering can name that contract
        violation rather than silently treating the spelling as a primitive.
        """
        scope = fn
        while scope is not None:
            self._scope(scope)
            if name in self._locals[id(scope)]:
                if name in self.declared_intrinsics:
                    return ("intrinsic-shadowed", None)
                node = self._nested[id(scope)].get(name)
                if node is not None:
                    return ("nested", node)
                if scope is fn:
                    node = self._resolve_call_alias(scope, name)
                    if node is not None:
                        return ("alias", node)
                return ("opaque", None)
            scope = self._parent.get(id(scope))
        if id(fn) in self._lib_fn_ids:
            # a library routine's free names live in the library's module
            # scope
            if name in self._lib_bound:
                node = self._lib_functions.get(name)
                return (("module", node) if node is not None
                        else ("opaque", None))
            if name in KNOWN_BUILTIN_SPELLINGS:
                return ("builtin", None)
            return ("unbound", None)
        if name in self.module_bound:
            intrinsic = self.intrinsic_imports.get(name)
            if intrinsic is not None:
                return ("intrinsic", intrinsic)
            if name in self.declared_intrinsics:
                return ("intrinsic-shadowed", None)
            node = self.module_functions.get(name)
            return ("module", node) if node is not None else ("opaque", None)
        if name in KNOWN_BUILTIN_SPELLINGS:
            return ("builtin", None)
        return ("unbound", None)

    def resolve_method(self, fn, name):
        """('method', node) | ('opaque', None) for a self.<name>(...) call."""
        if fn is not None and self.scope_rebinds_self(fn):
            return ("opaque", None)
        if name in self.instance_attrs:
            # an instance field of this name may shadow the method
            return ("opaque", None)
        node = self.methods.get(name)
        return ("method", node) if node is not None else ("opaque", None)

    def reachable_functions(self, roots):
        """Source functions exposed by the selected executable roots.

        Follow resolved callable references as well as direct calls: Python may
        invoke a function through an alias or pass it to another function.  This
        is a conservative declaration boundary, not a claim that every exposed
        function executes. Unreferenced helpers and nested bodies stay outside.
        """

        roots = tuple(roots)
        if any(not isinstance(root, ast.FunctionDef) for root in roots):
            raise TypeError("a source call closure starts at FunctionDef roots")
        todo = list(reversed(roots))
        seen = set()
        out = []
        while todo:
            fn = todo.pop()
            if id(fn) in seen:
                continue
            seen.add(id(fn))
            out.append(fn)
            callees = self._referenced_functions((fn,))
            todo.extend(reversed(callees))
        return tuple(out)

    def reachable_functions_from_occurrences(self, roots):
        """Resolved call closure entered through source occurrences in ``roots``.

        A frontend may prove a source subtree unreachable before visiting the
        calls it contains.  The public catalogue nevertheless already contains
        the callees selected by those occurrences, so the lowering needs the
        same lexical answer in order to give their goals explicit false
        conditions.  This is the occurrence-level counterpart of
        :meth:`reachable_functions`; both use this resolver's single binding
        authority.
        """

        roots = tuple(roots)
        if any(not isinstance(root, ast.AST) for root in roots):
            raise TypeError("a source call closure starts at AST occurrences")
        return self.reachable_functions(self._referenced_functions(roots))

    def _referenced_functions(self, roots):
        """Resolve function-valued loads through the same lexical authority."""
        callees = []
        seen = set()
        for root in roots:
            for node in self.execution_nodes(root):
                scope = self.scope_of(node)
                targets = ()
                if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
                        and scope is not None):
                    parent = self._parents.get(id(node))
                    # M.helper / cls.helper is already resolved by the
                    # attribute occurrence below. It does not expose every
                    # method of M (notably reset) as an executable root.
                    bound_attribute = (isinstance(parent, ast.Attribute)
                                       and parent.value is node
                                       and self.class_receiver(scope, node.id))
                    if not bound_attribute:
                        targets = self._source_callable_candidates(scope, node.id)
                elif (isinstance(node, ast.Attribute)
                      and isinstance(node.value, ast.Name)
                      and scope is not None):
                    if (node.value.id == "self" and node.attr in self.properties
                            and not self.scope_rebinds_self(scope)):
                        accessors = self.properties[node.attr]
                        parent = self._parents.get(id(node))
                        targets = tuple(accessors.get(name)
                                        for name in property_accessors(node, parent))
                    elif not isinstance(node.ctx, ast.Load):
                        continue
                    elif self.class_receiver(scope, node.value.id):
                        targets = (self.methods.get(node.attr),)
                    elif node.value.id == "self":
                        kind, target = self.resolve_method(scope, node.attr)
                        if kind == "method":
                            targets = (target,)
                for target in targets:
                    if isinstance(target, ast.FunctionDef) and id(target) not in seen:
                        seen.add(id(target))
                        callees.append(target)
        return callees

    def _source_callable_candidates(self, fn, name):
        """Source bodies a lexical reference may expose, not inline eligibility.

        A conditional or multiply-defined local function can execute natively
        even when lowering cannot prove its binding at a particular call. Keep
        those possible bodies in the catalogue; do not relax ``resolve_call``'s
        separate use-before-definition and effect checks.
        """
        scope = fn
        while scope is not None:
            self._scope(scope)
            if name in self._locals[id(scope)]:
                if scope is fn:
                    target = self._resolve_call_alias(scope, name)
                    if target is not None:
                        return (target,)
                return self._nested_sources[id(scope)].get(name, ())
            scope = self._parent.get(id(scope))
        kind, target = self.resolve_call(fn, name)
        if kind == "module":
            return (target,)
        if id(fn) not in self._lib_fn_ids:
            # A constructor can call source factories without a lexical call
            # in one of its methods. Follow those declared callable references
            # through this same closure, including constructors used as factories.
            pending, seen, bodies = [name], set(), []
            while pending:
                symbol = pending.pop()
                if symbol in seen:
                    continue
                seen.add(symbol)
                bodies.extend(self._module_source_callables.get(symbol, ()))
                for expression in self._constructor_factories.get(symbol, ()):
                    pending.extend(part.id for part in ast.walk(expression)
                                   if isinstance(part, ast.Name) and isinstance(part.ctx, ast.Load))
            return tuple(bodies)
        return ()

    def class_receiver(self, fn, name):
        """Whether a name is the unchanged implicit class argument here.

        Class lookup is distinct from instance lookup: an instance field cannot
        shadow ``cls.helper``. Nested functions inherit the receiver only when
        no intervening local binding shadows it.
        """
        while fn is not None:
            self._scope(fn)
            if id(fn) in self._class_method_ids and fn.args.args[0].arg == name:
                return name not in self._rebound_parameters[id(fn)]
            if name in self._locals[id(fn)]:
                return False
            fn = self._parent.get(id(fn))
        # The isolated loader independently checks that this module binding is
        # still the selected class. An explicit Class.helper reference must
        # expose the same source body to the concrete observer as cls.helper.
        return name == self.model.cls.name

    def scope_rebinds_self(self, fn):
        """Whether ``self`` is an ordinary local anywhere on ``fn``'s
        lexical chain, in which case no rule may read it as the model."""
        while fn is not None:
            self._scope(fn)
            if self._rebinds_self[id(fn)]:
                return True
            fn = self._parent.get(id(fn))
        return False

    def rebound_parameters(self, fn):
        """Parameters assigned by this function's own lexical body.

        Parameters are local bindings in Python, but public SolverIR inputs
        are read-only.  The frontend uses this occurrence-independent lexical
        fact to allocate private copies only for parameters the source can
        actually rebind.  Comprehension targets are excluded by ``_scope``;
        walrus targets, loop targets, imports and destructuring assignments
        are included because they really bind the surrounding function.
        """
        self._scope(fn)
        return self._rebound_parameters[id(fn)]

    def written_parameters(self, fn):
        """Parameters rebound or mutated by this function's lexical body.

        A compound target does not rebind its root name in Python, but it
        still cannot write through SolverIR's public read-only input context.
        The frontend uses this set to allocate one private by-value view for
        either kind of write.
        """
        self._scope(fn)
        return self._written_parameters[id(fn)]

    def is_read_only_parameter(self, fn, name):
        """Whether ``name`` is an unwritten parameter of ``fn``.

        Public Behavior inputs are read-only, but a Python parameter may be
        rebound through the frontend's private IR copy.  Source planning and
        CFG analysis may correlate repeated tests only in the former case, so
        the lexical resolver owns that distinction.
        """
        parameters = {
            argument.arg
            for argument in (*getattr(fn.args, "posonlyargs", ()),
                             *fn.args.args, *fn.args.kwonlyargs)
        }
        if id(fn) in self._method_ids:
            parameters.discard("self")
        return name in parameters and name not in self.written_parameters(fn)

    def is_builtin(self, fn, name):
        return self.resolve_call(fn, name)[0] == "builtin"

    def callback_bodies(self, call):
        """Inline callback bodies evaluated by a resolved source operation.

        Creating a lambda does not execute its body. The admitted ``sorted``
        key callback does execute once per item, including its partial
        operations; a shadowing source function has no such builtin contract.
        """
        if (not isinstance(call, ast.Call)
                or not isinstance(call.func, ast.Name)
                or call.func.id != "sorted"
                or not self.is_builtin(self.scope_of(call), call.func.id)):
            return ()
        return tuple(keyword.value.body for keyword in call.keywords
                     if keyword.arg == "key" and isinstance(keyword.value, ast.Lambda))

    def execution_nodes(self, root):
        """Occurrences in one scope plus callbacks that its calls may execute."""
        pending = [root]
        while pending:
            for node in self._walk_same_scope(pending.pop()):
                yield node
                if isinstance(node, ast.Call):
                    pending.extend(reversed(self.callback_bodies(node)))

    def call_has_no_external_effect(self, owner, call):
        """Whether one resolved call can change caller-visible source state.

        This is a deliberately strict effect summary, not a spelling
        whitelist.  Builtins and declared intrinsics are pure by contract.
        A source helper is pure only when every call in its lexical body is
        recursively pure and every assignment remains a plain local binding;
        state, collection, global and opaque writes therefore fail closed.
        """
        if not isinstance(call, ast.Call):
            raise TypeError("a call effect query requires ast.Call")
        target = None
        if isinstance(call.func, ast.Name):
            kind, target = self.resolve_call(owner, call.func.id)
            if kind in {"builtin", "intrinsic"}:
                return True
            if kind not in {"nested", "module", "alias"}:
                return False
        elif (isinstance(call.func, ast.Attribute)
              and isinstance(call.func.value, ast.Name)
              and call.func.value.id == "self"):
            kind, target = self.resolve_method(owner, call.func.attr)
            if kind != "method":
                return False
        else:
            return False
        return self._function_has_no_external_effect(target)

    def _function_has_no_external_effect(self, fn, active=frozenset()):
        cached = self._pure_functions.get(id(fn))
        if cached is not None:
            return cached
        if id(fn) in active:
            return False
        active = active | {id(fn)}

        def plain_target(target):
            if isinstance(target, ast.Name):
                return True
            if isinstance(target, ast.Starred):
                return plain_target(target.value)
            if isinstance(target, (ast.Tuple, ast.List)):
                return all(plain_target(item) for item in target.elts)
            return False

        for node in self._walk_same_scope(fn):
            if node is fn:
                continue
            if isinstance(node, (ast.Global, ast.Nonlocal, ast.Delete,
                                 ast.Import, ast.ImportFrom, ast.With,
                                 ast.AsyncWith, ast.Raise, ast.Yield,
                                 ast.YieldFrom, ast.Await, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
                self._pure_functions[id(fn)] = False
                return False
            targets = ()
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign,
                                   ast.NamedExpr, ast.For, ast.AsyncFor)):
                targets = (node.target,)
            if targets and not all(plain_target(target)
                                   for target in targets):
                self._pure_functions[id(fn)] = False
                return False
            if not isinstance(node, ast.Call):
                continue
            nested = None
            if isinstance(node.func, ast.Name):
                kind, nested = self.resolve_call(fn, node.func.id)
                if kind in {"builtin", "intrinsic"}:
                    continue
                if kind not in {"nested", "module", "alias"}:
                    self._pure_functions[id(fn)] = False
                    return False
            elif (isinstance(node.func, ast.Attribute)
                  and isinstance(node.func.value, ast.Name)
                  and node.func.value.id == "self"):
                kind, nested = self.resolve_method(fn, node.func.attr)
                if kind != "method":
                    self._pure_functions[id(fn)] = False
                    return False
            else:
                self._pure_functions[id(fn)] = False
                return False
            if not self._function_has_no_external_effect(nested, active):
                self._pure_functions[id(fn)] = False
                return False
        self._pure_functions[id(fn)] = True
        return True

    def is_local(self, fn, name):
        """Whether ``name`` is bound as a local anywhere on ``fn``'s lexical
        chain, in which case it is not a module-level constant to fold
        (review 49: a parameter named like a module constant K)."""
        return self.local_owner(fn, name) is not None

    def local_owner(self, fn, name):
        """Nearest lexical function whose local binding owns ``name``.

        A closure reads that exact activation, not whichever dynamic caller
        happens to use the same spelling.  Class scopes are absent from
        ``_parent`` by construction, matching Python's lookup rules.
        """
        while fn is not None:
            self._scope(fn)
            if name in self._locals[id(fn)]:
                return fn
            fn = self._parent.get(id(fn))
        return None

    def lexical_parent(self, fn):
        """The enclosing function captured by ``fn``, if it has one."""
        return self._parent.get(id(fn))

    def resolve_const(self, fn, name):
        """The one authority for what a plain name folds to, by lexical owner
        (review 51). ``fn`` is the scope the name is read in (a FunctionDef,
        or None at module level).

        Returns one of:
          ("local", None)   -- a local of fn's chain; not a constant.
          ("const", int)    -- a fixed integer of the owning module.
          ("bound", None)   -- bound in the owning module but not a supported
                               integer constant (a def, a class, a non-int);
                               the caller must refuse rather than free-var it.
          ("builtin", None) -- an unshadowed builtin spelling.
          ("free", None)    -- bound nowhere this can see.
        """
        if fn is not None and self.is_local(fn, name):
            return ("local", None)
        in_lib = fn is not None and id(fn) in self._lib_fn_ids
        consts = self.lib_consts if in_lib else self.model_consts
        bound = self._lib_bound if in_lib else self.module_bound
        if name in consts:
            return ("const", consts[name])
        if name in bound:
            return ("bound", None)
        if name in KNOWN_BUILTIN_SPELLINGS:
            return ("builtin", None)
        return ("free", None)

    def resolves_model_module(self, fn, name):
        """Whether this occurrence denotes a model-module binding.

        Collection lowering needs the same lexical answer as scalar
        lowering.  Looking only in the model's table dictionary is wrong for
        a local that shadows a table and for an inlined library function,
        whose free names belong to the library module.  Keep that ownership
        decision here, beside the ordinary name resolver.
        """
        if fn is not None and self.is_local(fn, name):
            return False
        if fn is not None and id(fn) in self._lib_fn_ids:
            return False
        return name in self.module_bound

    def class_attribute(self, fn, node, *, shadowed=()):
        """A field of the closed selected-class namespace, not a local lookalike."""
        if (not isinstance(node, ast.Attribute)
                or not isinstance(node.value, ast.Name)
                or node.value.id in shadowed
                or node.value.id not in self.class_names
                or not self.resolves_model_module(fn, node.value.id)):
            return None
        return self.class_values.get(node.attr)

    def is_collection_base(self, node):
        """Whether ``node`` is consumed as the base of an indexed read.

        A module value that is not an admitted scalar or table is not a free
        IR variable.  An indexed read has its own, more precise declaration
        question, however: whether that value has a static collection shape.
        Let that rule issue the refusal instead of hiding the unsupported
        collection behind the generic module-value rule.
        """
        parent = self._parents.get(id(node))
        return isinstance(parent, ast.Subscript) and parent.value is node

    def _index_enclosing(self, node, fn):
        # Handle the current node, including a nested def that is itself a
        # function-body statement. Handling only child FunctionDefs assigned
        # those nested bodies to the enclosing function instead.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = (*node.args.posonlyargs, *node.args.args,
                         *node.args.kwonlyargs, node.args.vararg, node.args.kwarg)
            definition_time = [
                *node.decorator_list, *node.args.defaults,
                *(value for value in node.args.kw_defaults if value is not None),
                *(arg.annotation for arg in arguments
                  if arg is not None and arg.annotation is not None),
                *([node.returns] if node.returns is not None else []),
            ]
            children = [(child, fn) for child in definition_time]
            children.extend((child, node) for child in node.body)
        else:
            children = ((child, fn) for child in ast.iter_child_nodes(node))
        for child, owner in children:
            self._parents[id(child)] = node
            self._enclosing[id(child)] = owner
            self._index_enclosing(child, owner)

    def scope_of(self, node):
        """The function whose lexical scope contains this occurrence."""
        return self._enclosing.get(id(node))

    def occurrence_is_local(self, node):
        """Whether a Name occurrence resolves to a function-local binding."""
        return (isinstance(node, ast.Name)
                and self.scope_of(node) is not None
                and self.is_local(self.scope_of(node), node.id))

    def occurrence_is_class_local(self, node):
        """Whether this occurrence reads/writes a class-body binding.

        Methods do not close over their class namespace, but the class body
        itself (including a method's decorators and defaults) executes in
        that namespace.  This distinction matters to the module-table
        contract: an unrelated ``Other.TABLE`` spelling must not be treated
        as an alias of the module's ``TABLE``, while ``TABLE[0] = ...`` in a
        class with no prior class binding still mutates the module object.
        """
        if not isinstance(node, ast.Name) or self.scope_of(node) is not None:
            return False
        current = node
        classnode = direct = None
        while id(current) in self._parents:
            parent = self._parents[id(current)]
            if isinstance(parent, (ast.ListComp, ast.SetComp, ast.DictComp,
                                   ast.GeneratorExp, ast.Lambda)):
                # A comprehension/lambda body has its own lookup rules and
                # does not read the surrounding class namespace.
                return False
            if isinstance(parent, ast.ClassDef):
                classnode, direct = parent, current
                break
            current = parent
        if classnode is None:
            return False
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            return True

        known = set()
        for stmt in classnode.body:
            if stmt is direct:
                return node.id in known
            if isinstance(stmt, ast.Delete):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        known.discard(target.id)
                continue
            # Only statements that certainly execute and bind at this level
            # establish a class-local name for a later LOAD_NAME.  Conditional
            # binders remain ambiguous and therefore conservatively keep the
            # module reference visible to the table audit.
            if isinstance(stmt, (ast.Assign, ast.AugAssign, ast.Import,
                                 ast.ImportFrom, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef, ast.Expr)) or isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                known.update(_statement_bindings(stmt)[0])
        return False

    def owner_of(self, scope, subst=None):
        """The owner-aware `_const_int` authority for one scope (review 51):
        a callable resolving a bare name to its owning module's constant and
        a `self.X` to the chosen class's, honouring locals and a current
        substitution, so every structural consumer -- array shape, initial
        state, loop and slice bounds -- folds exactly what the value fold
        does, never another owner's constant."""
        subst = subst or {}

        def owner(node):
            value = self.class_attribute(scope, node, shadowed=subst)
            if type(value) in (int, bool):
                return value
            if isinstance(node, ast.Attribute) \
                    and isinstance(node.value, ast.Name) \
                    and node.value.id == "self":
                if node.attr in self._externally_dynamic_state:
                    return None
                if node.attr in self.class_consts:
                    return self.class_consts[node.attr]
                return self.self_consts.get(node.attr)
            if isinstance(node, ast.Name):
                if node.id in subst:
                    return None
                kind, value = self.resolve_const(scope, node.id)
                return value if kind == "const" else None
            return None

        return owner

    def mark_externally_dynamic_state(self, names):
        """Exclude closed-loop state from procedure-local constant folding.

        A selected multi-clock handler or readiness predicate may not write a
        field itself, while another handler changes it between activations.
        Such a field retains its construction value for representation
        planning but not as a value fact of an arbitrary procedure step.
        """
        names = frozenset(names)
        unknown = names - self.instance_attrs
        if unknown:
            raise ValueError(
                f"dynamic fields are absent from the source model: "
                f"{sorted(unknown)!r}")
        self._externally_dynamic_state = names

    def occurrence_owner(self):
        """The owner-aware authority for declaration-time analysis (array
        shape, initial state, append/output capacity), resolving each name
        in the function it actually occurs in (review 52): a `self.X` to the
        chosen class's or a constructor default, a constructor parameter to
        its default, any other bare name to the module constant of its
        enclosing scope."""

        def owner(node):
            fn = self._enclosing.get(id(node))
            value = self.class_attribute(fn, node)
            if type(value) in (int, bool):
                return value
            at_point = self._point_values.get(id(node))
            if at_point is not None:
                locals_, fields = at_point
                if isinstance(node, ast.Name) and node.id in locals_:
                    return locals_[node.id]
                if (isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "self"
                        and node.attr in fields):
                    return fields[node.attr]
            if isinstance(node, ast.Attribute) \
                    and isinstance(node.value, ast.Name) \
                    and node.value.id == "self":
                if node.attr in self.class_consts:
                    return self.class_consts[node.attr]
                return self.self_consts.get(node.attr)
            if isinstance(node, ast.Name):
                kind, value = self.resolve_const(fn, node.id)
                return value if kind == "const" else None
            return None

        return owner

    def occurrence_file(self, node):
        """Source file owning one resolved AST occurrence.

        Approved library routines are parsed into the same resolver graph as
        model routines, so line and column alone are not an occurrence identity.
        Synthetic nodes created while lowering may be represented by their
        current function scope; both forms receive the same answer here.
        """
        fn = (node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
              else self._enclosing.get(id(node)))
        return "fp_lib.py" if fn is not None and id(fn) in self._lib_fn_ids \
            else self.model.file


def _def_time_pure(fn):
    """Whether defining ``fn`` computes nothing: no decorators, and every
    default and annotation a plain constant. Python evaluates all three at
    the def statement (review 48: a walrus default bound the enclosing
    local and the lowering skipped it)."""
    if fn.decorator_list:
        return False
    defaults = list(fn.args.defaults) + [d for d in fn.args.kw_defaults
                                         if d is not None]
    if any(not isinstance(d, ast.Constant) for d in defaults):
        return False
    every = (list(fn.args.args) + list(fn.args.kwonlyargs)
             + list(fn.args.posonlyargs)
             + [a for a in (fn.args.vararg, fn.args.kwarg) if a is not None])
    annotations = [a.annotation for a in every if a.annotation is not None]
    if fn.returns is not None:
        annotations.append(fn.returns)
    return all(isinstance(a, ast.Constant) for a in annotations)


def module_bound_names(tree):
    """Every name a top-level statement binds, whatever binds it."""
    return _module_binding_pass(tree)[1]


def _walk_function_scope(fn):
    """Walk one function body without entering a nested function or class."""
    stack = list(reversed(fn.body))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue
        yield node
        stack.extend(reversed(tuple(execution_children(node))))


def _model_arrays(model, owner=None, *, resolver=None):
    """Lengths of the arrays the model assigns a literal sequence.

    A loop over ``self.buf`` has as many iterations as ``buf`` has elements, so
    the shape is as much a parameter of the model as a width is. A buffer
    sized by a parameter is as fixed as one sized by a number, since nothing
    in a step changes a parameter.

    A model may write the array more than once, and what has to hold is that
    the assignments agree on the shape, not that there is one of them: a
    buffer cleared in two places is one buffer. An assignment whose shape is
    not measurable here is left to the lowering, which sees it once the value
    it assigns has been measured.
    """
    if not isinstance(model, SourceModel):
        model = source_model(model)
    resolver = resolver or ScopeResolver(model)
    shapes = {}
    functions = (list(model.steps.values()) + list(model.helpers.values())
                 + [fn for fn in (model.constructor, model.reset)
                    if fn is not None])
    for fn in functions:
        for node in _walk_function_scope(fn):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if not (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                continue
            shape = _shape_of(
                node.value, {}, owner=owner,
                is_builtin=lambda name, fn=fn: resolver.is_builtin(fn, name))
            if not shape:
                continue
            seen = shapes.setdefault(target.attr, shape)
            if seen != shape:
                shapes[target.attr] = ()
    return {k: v for k, v in shapes.items() if v}


def _class_namespace_contract(resolver):
    """Closed class values and the module names still denoting that class.

    Method execution is checked by lowering and native namespace ownership.
    Definition-time effects instead invalidate these facts: the final module
    must not call code or expose the class after its declaration, and a class
    body operation outside closed bindings clears its accumulated collections.
    Receiver reads separately exclude instance fields. A class-qualified read
    remains distinct when an instance shadows the same member.
    """
    model = resolver.model
    if model.cls.decorator_list:
        return {}, frozenset()
    postponed = any(isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__"
                    and any(name.name == "annotations" for name in stmt.names)
                    for stmt in model.tree.body)
    known = dict(resolver._module_before.get(id(model.cls), {}))

    def frozen_value(node):
        return evaluate_frozen(
            node, lambda value: known.get(value.id) if isinstance(value, ast.Name) else None,
            budget=resolver.source_value_budget)

    def passive_annotation(node):
        # Even a call-free C[T] can execute __class_getitem__. Bare name
        # lookup and literals cannot invoke candidate code in this namespace.
        return node is None or postponed or isinstance(node, (ast.Name, ast.Constant))

    def passive_function(stmt, *, member=False):
        # source_model already authenticates unshadowed built-in descriptors
        # for class members. Creating those descriptors invokes no user body.
        if stmt.decorator_list and (not member or method_kind(stmt) not in {
                "staticmethod", "classmethod"}):
            return False
        args = stmt.args
        parameters = [*args.posonlyargs, *args.args, *args.kwonlyargs,
                      *([args.vararg] if args.vararg else []),
                      *([args.kwarg] if args.kwarg else [])]
        return (passive_annotation(stmt.returns)
                and all(passive_annotation(arg.annotation) for arg in parameters)
                and all(frozen_value(value) is not None
                        for value in (*args.defaults, *args.kw_defaults) if value is not None))

    def inert(stmt):
        return isinstance(stmt, ast.Pass) or (
            isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))

    collections, class_bound = {}, set()
    for stmt in model.cls.body:
        prior_bound = class_bound.copy()
        class_bound.update(_statement_bindings(stmt)[0])
        targets = value = None
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            targets, value = [stmt.target], stmt.value
            if not passive_annotation(stmt.annotation):
                known.clear()
                collections.clear()
                continue
            if value is None:
                continue
        if targets is not None:
            rows = _closed_assignment_bindings(
                targets, value,
                lambda node: known.get(node.id) if isinstance(node, ast.Name) else None,
                builtin_range='range' not in prior_bound and 'range' not in resolver._declaration_bound,
                budget=resolver.source_value_budget)
            if rows is None:
                known.clear()
                collections.clear()
                continue
            # Freeze the complete RHS before any target binds. Repeated names
            # then follow Python's left-to-right assignment order.
            for target, frozen in rows:
                known[target] = frozen
                collections.pop(target, None)
                if frozen.fits_expansion(
                        DEFAULT_ROM_NODES, budget=resolver.source_value_budget, node=stmt):
                    collections[target] = frozen.to_source_value(
                        budget=resolver.source_value_budget, node=stmt)
        elif isinstance(stmt, ast.FunctionDef) and passive_function(stmt, member=True):
            known.pop(stmt.name, None)
            collections.pop(stmt.name, None)
        elif inert(stmt):
            continue
        else:
            known.clear()
            collections.clear()
    # After the class is published, admit only declarations whose evaluation
    # is closed. Unknown subscriptions/operators may call overloaded Python
    # methods, even when the AST contains no explicit Call or class reference.
    known = {}
    class_aliases = {model.cls.name}
    for stmt in model.tree.body[model.tree.body.index(model.cls) + 1:]:
        if inert(stmt):
            continue
        if isinstance(stmt, ast.FunctionDef) and passive_function(stmt):
            known.pop(stmt.name, None)
            class_aliases.discard(stmt.name)
            continue
        if isinstance(stmt, ast.Assign) and all(isinstance(n, ast.Name) for n in stmt.targets):
            names = {target.id for target in stmt.targets}
            if isinstance(stmt.value, ast.Name) and stmt.value.id in class_aliases:
                # An identity alias exposes no operation on the class. Any
                # later mutation/call still fails the closed suffix check.
                class_aliases.update(names)
                for name in names:
                    known.pop(name, None)
                continue
            frozen = frozen_value(stmt.value)
            if frozen is not None:
                known.update({n.id: frozen for n in stmt.targets})
                class_aliases.difference_update(names)
                continue
        return {}, frozenset()
    return collections, frozenset(class_aliases)


def _module_table_contract(resolver, *, budget=None):
    """Closed, read-only finite module collections, and invalid ones.

    The proof rule is intentionally closed: after a finite binding and any
    certified initialization loops, every reference to that object must be one of the
    reads the lowerer implements (``T[k]`` for a mapping or sequence, and
    ``T.get(k, default)`` for a mapping).
    Passing it, aliasing it, iterating it, invoking another method, or writing
    through it makes the object mutable from the frontend's point of view and
    removes it from the contract.  Lexically local names with the same spelling
    are unrelated and ignored.

    This is stronger and simpler than recognizing a growing list of mutator
    spellings: a future Python operation is rejected unless its read semantics
    are explicitly represented.  The lowering separately checks whether the
    payload has a SolverIR representation.
    """
    tree = resolver.model.tree
    direct = {}
    known = {}
    initializers = {}
    invalidated = set()
    top_index = {}
    name_occurrences = {}
    for index, stmt in enumerate(tree.body):
        for sub in ast.walk(stmt):
            top_index[id(sub)] = index
            if isinstance(sub, ast.Name):
                name_occurrences.setdefault(sub.id, []).append(sub)

    occurrence_owner = resolver.occurrence_owner()
    for index, stmt in enumerate(tree.body):
        target = value = None
        if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)):
            target, value = stmt.targets[0], stmt.value
        elif (isinstance(stmt, ast.AnnAssign)
              and isinstance(stmt.target, ast.Name)
              and stmt.value is not None):
            target, value = stmt.target, stmt.value
        complete_name = None if target is None else target.id
        bound = set(_statement_bindings(stmt)[0])
        for name in bound:
            # Function bodies observe the final module binding.  A complete
            # rebinding disconnects the old object, so it also clears any
            # table status that old object had.  Mutations of the final
            # collection are detected by the closed-use walk below.  A
            # conditional or read-modify-write binding does not establish a
            # replacement on every execution, and therefore invalidates a
            # collection that may still be the live object.
            if name == complete_name:
                invalidated.discard(name)
            elif name in direct:
                invalidated.add(name)
            known.pop(name, None)
            direct.pop(name, None)

        def value_of(node):
            if (isinstance(node, ast.Name) and node.id in known
                    and not resolver.occurrence_is_local(node)):
                return known[node.id]
            return occurrence_owner(node)

        def function_of(call):
            if not isinstance(call.func, ast.Name):
                return None
            scope = resolver.scope_of(call)
            kind, target = resolver.resolve_call(scope, call.func.id)
            if kind != "module":
                return None
            # Frozen evaluation interprets only the concrete local-only path.
            # It refuses external writes/opaque calls itself, including methods
            # without a proved primitive receiver; no candidate code runs here.
            return target

        def builtin_of(call):
            if (isinstance(call.func, ast.Name)
                    and call.func.id not in resolver._declaration_bound
                    and resolver.is_builtin(resolver.scope_of(call), call.func.id)):
                return call.func.id
            return None

        if isinstance(stmt, ast.For):
            written = {part.value.id for part in ast.walk(stmt)
                       if isinstance(part, ast.Subscript) and isinstance(part.ctx, ast.Store)
                       and isinstance(part.value, ast.Name)}
            if len(written) == 1:
                name, = written
                if name in direct:
                    binding_index, binding, previous = direct[name]
                    # Updating one source object also changes its aliases.
                    # Those aliases cannot keep the pre-initialization image;
                    # this rule certifies only an unshared initializer target.
                    shared = previous.mutable_identities(budget=budget, node=stmt)
                    for other, (_, _, image) in direct.items():
                        if other != name and shared.intersection(
                                image.mutable_identities(budget=budget, node=stmt)):
                            invalidated.add(other)
                    updated = update_frozen_table(
                        stmt, name, previous, value_of, function_of=function_of,
                        builtin_of=builtin_of, budget=budget)
                    if updated is not None:
                        known[name] = updated
                        direct[name] = (binding_index, binding, updated)
                        initializers.setdefault(name, set()).update(id(part) for part in ast.walk(stmt))
            continue
        if target is None:
            continue

        frozen = evaluate_frozen(
            value, value_of, function_of=function_of,
            builtin_of=builtin_of, budget=budget)
        if frozen is None:
            continue
        known[target.id] = frozen
        if frozen.is_collection and frozen.payload:
            direct[target.id] = (index, stmt, frozen)
            # A later complete finite binding disconnects an older object.
            # Only the final binding matters to function bodies.
            invalidated.discard(target.id)

    def in_subtree(root, wanted):
        return any(id(node) == wanted for node in ast.walk(root))

    def comp_local(node):
        """Whether a comprehension has bound this spelling at this point."""
        wanted, name = id(node), node.id
        parent = resolver._parents.get(id(node))
        while parent is not None:
            if isinstance(parent, (ast.ListComp, ast.SetComp,
                                   ast.DictComp, ast.GeneratorExp)):
                bound = set()
                for gen in parent.generators:
                    if in_subtree(gen.iter, wanted):
                        return name in bound
                    bound |= {n.id for n in ast.walk(gen.target)
                              if isinstance(n, ast.Name)}
                    if any(in_subtree(guard, wanted) for guard in gen.ifs):
                        return name in bound
                bodies = ([parent.key, parent.value]
                          if isinstance(parent, ast.DictComp)
                          else [parent.elt])
                if any(in_subtree(body, wanted) for body in bodies):
                    return name in bound
            parent = resolver._parents.get(id(parent))
        return False

    def local(node):
        return (resolver.occurrence_is_local(node)
                or resolver.occurrence_is_class_local(node)
                or comp_local(node))

    def allowed_read(node):
        # A module collection may be tracked symbolically inside a lowered
        # function, but writing through the root is never a read.  In
        # particular reset executes before step lowering and therefore cannot
        # delegate ``TABLE[i] = ...`` to StaticRef's escape checks.
        current = node
        parent = resolver._parents.get(id(current))
        while (isinstance(parent, (ast.Subscript, ast.Attribute))
               and parent.value is current):
            current = parent
            parent = resolver._parents.get(id(current))
        if isinstance(getattr(current, "ctx", None), (ast.Store, ast.Del)):
            return False
        if (isinstance(current, ast.Attribute)
                and isinstance(parent, ast.Call)
                and parent.func is current
                and current.attr != "get"):
            return False
        # A function body executes after the module has reached its final
        # namespace.  The lowering tracks finite references there and refuses
        # every mutation, identity observation, or escape it cannot preserve.
        # Keeping that obligation in the lowering lets ``ref = ROM`` and
        # conditionally selected ROMs remain first-class compile-time facts
        # without pretending the IR contains Python objects.
        if resolver.scope_of(node) is not None:
            return True
        parent = resolver._parents.get(id(node))
        if (isinstance(parent, ast.Call) and parent.args == [node]
                and not parent.keywords and isinstance(parent.func, ast.Name)
                and parent.func.id == 'len'
                and 'len' not in resolver._declaration_bound):
            # A module assertion may inspect the final table's size. The
            # isolated construction still executes (and must satisfy) it.
            return True
        if (isinstance(parent, ast.Subscript) and parent.value is node
                and isinstance(parent.ctx, ast.Load)):
            return True
        if (isinstance(parent, ast.Attribute) and parent.value is node
                and parent.attr == "get"):
            call = resolver._parents.get(id(parent))
            # Signature validation belongs to call lowering.  Even a malformed
            # ``TABLE.get(...)`` is only a lookup attempt: it neither exposes
            # nor mutates the literal table, and Python evaluates its
            # arguments before raising.  Marking the table non-static here
            # hides that argument/call error behind an unrelated declaration
            # refusal.
            return isinstance(call, ast.Call) and call.func is parent
        return False

    valid, source_values, invalid = {}, {}, set(invalidated)
    for name, (binding_index, stmt, frozen) in direct.items():
        # The frozen declaration graph preserves sharing, while SolverIR ROMs
        # are value trees.  Refuse an oversized expansion before constructing
        # either host projection; otherwise a linear source DAG can consume
        # exponential verifier memory merely while we test its read contract.
        if not frozen.fits_expansion(
                DEFAULT_ROM_NODES, budget=budget, node=stmt):
            continue
        entries = frozen.to_plain(budget=budget, node=stmt)
        source_entries = frozen.to_source_value(budget=budget, node=stmt)
        # A literal whose payload is outside the finite-table representation
        # is a static-collection lowering gap, not evidence that its binding
        # can mutate.  Leave it to the collection rule instead of
        # mislabelling it as a failed read-only contract.
        encoded = entries
        if not isinstance(encoded, (dict, tuple)) or not encoded:
            continue

        closed = True
        candidate_target = (stmt.targets[0]
                            if isinstance(stmt, ast.Assign) else stmt.target)
        for node in name_occurrences.get(name, ()):
            if local(node):
                continue
            if node is candidate_target:
                continue
            if id(node) in initializers.get(name, ()):
                continue
            fn = resolver.scope_of(node)
            index = top_index.get(id(node), len(tree.body))
            # Module code before the final literal refers to an older object;
            # rebinding to the literal disconnects it.  Function bodies run
            # later and therefore always see the final module binding.
            if fn is None and index < binding_index:
                continue
            if isinstance(node.ctx, ast.Load):
                if allowed_read(node):
                    continue
                parent = resolver._parents.get(id(node))
                # Membership reads a literal dict without exposing or
                # mutating it.  The IR does not yet encode membership, but
                # that is an operation-level refusal, not a declaration
                # failure, so let the comparison rule report it.
                if isinstance(parent, ast.Compare):
                    continue
            closed = False
            break
        if closed and name not in invalid:
            valid[name] = encoded
            source_values[name] = source_entries
        else:
            invalid.add(name)
    return valid, source_values, frozenset(invalid)


def initial_by_construction(source, file="behavior.py", names=None):
    """What the model holds after it is constructed and reset, read by doing it.

    The definition rather than an approximation of it. A run starts where the harness
    starts it, and the harness constructs the model and calls ``reset``: that sentence
    is already in `interp.run_step`, and `_initial_state` below approximates it by
    folding the source. Folding needs a rule per expression and the population keeps
    finding ones it has not got. Eight models' starting values are parameters computed
    as ``in_data_width + (in_data_ns - 1).bit_length()`` or
    ``to_unsigned(p_unlock_code_0, p_data_width)``, which the folder cannot read, so
    each of those names was taken for a state variable whose starting value nothing
    could say, and the certificate was withheld for it.

    Running covers every expression at once and cannot drift from the harness, because
    it is the same construction.

    Two things it owes in return. It executes the artifact, so it belongs where the
    artifact is already executed, which is the instance and its source audit rather
    than the lowering; `lower_procedure` keeps the folding and takes this as an
    override. And it has to be the same answer twice, since a starting value that
    depends on the order two runs happen in is not a starting value: ``twice`` below
    constructs and resets a second time and refuses a disagreement.

    Returns a mapping from attribute name to an integer or a recursively nested
    rectangular list of integers, holding only what a step can carry.
    """
    from .construction import construction_facts

    facts = construction_facts(
        source, file, cls_name=source_model(source, file).cls.name)
    values, unsupported = facts.values(), facts.unsupported()
    if names is None:
        return values, unsupported
    wanted = set(names)
    return ({name: value for name, value in values.items() if name in wanted},
            {name: why for name, why in unsupported.items() if name in wanted})


def _initial_state(model, constants=None, typed=False, owner=None, *,
                   resolver=None):
    """What the state holds before the first step, read off the chosen model.

    A run starts where the harness starts it, and the harness builds the model
    and calls ``reset``. Leaving the initial state to be whatever a solver
    picks makes a query ask a different question: it asks whether some state
    reaches the goal, and answers with a stimulus that may need a state no run
    can be in. Unreachability survives that, since more states were searched
    than exist, but reachability does not.

    Only what ``reset`` fixes outright is read here. A field it computes is
    left out, and a caller that needs it will say so rather than assume zero.

    Outright means unconditionally, and it did not. The statements were walked
    with `ast.walk`, which does not care what they are inside, so a field two
    branches of an `if` set differently was read as whichever assignment the walk
    reached last and a field set only inside a branch was read as though it always
    were. What is wanted is the assignments the body performs however it runs,
    which is the ones at its top level: an assignment under a condition is not a
    starting value the harness fixes, it is one that depends on something, and
    leaving it out is what `guessed_initial` is for.

    Whose methods, and in what order, is fixed by the runtime and not by the
    page (review 24). The harness always runs ``M.__init__`` then ``M.reset``,
    however the two are arranged in the file, and it runs no other class's
    methods at all -- while `ast.walk(tree)` read every ``__init__``/``reset``
    in the module in source order, so a ``reset`` written above ``__init__``
    lost to it and a helper class at the bottom of the file overwrote the
    model's own values. The fold therefore takes the parsed `SourceModel` and
    reads exactly its constructor then its reset. And within that order, a
    later top-level write the fold cannot read **erases** the entry rather
    than being skipped: after ``self.k = 5`` then ``self.k = v``, the field is
    not fixed outright at 5, it is not fixed outright at all.
    """
    constants = constants or {}
    resolver = resolver or ScopeResolver(model)
    out = {}
    registry = {}
    for fn in (list(model.steps.values()) + list(model.helpers.values())
               + [m for m in (model.reset, model.constructor)
                  if m is not None]):
        if not fn.decorator_list:
            registry[fn.name] = fn
    # Whether `self` has left the fold's sight whole -- as a call argument,
    # an alias, a container element (review 26: `set_x(self)` wrote a field
    # through a plain function the fold never reads). From that point, code
    # the fold does not read can write any field at any time, so everything
    # held clears, and only literal self-assignments fold thereafter: every
    # other statement clears again.
    escaped = False

    def immutable(value):
        """Whether handing ``value`` to code cannot mutate the field's value."""
        return (type(value) in (bool, int, float, str, bytes, type(None))
                or (type(value) is tuple
                    and all(immutable(item) for item in value)))

    for node in (model.constructor, model.reset):
        if node is None:
            continue
        is_builtin = lambda name, fn=node: resolver.is_builtin(fn, name)
        for stmt in node.body:
            if _bare_self(stmt):
                escaped = True
            # A call runs code this fold does not read, at this position
            # (review 25). `self.m()` erases m's reachable writes -- and
            # everything held so far when the registry cannot resolve m,
            # since then nothing bounds what it writes. A method called on a
            # field (`self.k.append(v)`) may write that field in place, so
            # the field erases.
            for called in _self_calls(stmt):
                if model.reset is not None and called == model.reset.name:
                    # A constructor's own `self.reset()` is subsumed: the
                    # harness calls reset last and this fold replays reset
                    # last, so whatever that call does, the final pass does
                    # over it. Erasing here broke the population's memory
                    # model, whose __init__ builds the buffer and calls
                    # reset() to fill it.
                    continue
                fn = registry.get(called)
                if fn is None:
                    out.clear()
                    break
                for name in model.reachable_state_writes([fn]):
                    out.pop(name, None)
            for name in _mutated_fields(stmt):
                out.pop(name, None)
            for name in _escaping_fields(stmt):
                # Passing a known immutable value cannot mutate the binding.
                # Mutable values still erase: code outside this fold may keep
                # and change the object through the escaped reference.
                if not immutable(out.get(name)):
                    out.pop(name, None)
            plain = (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                     and isinstance(stmt.targets[0], ast.Attribute)
                     and isinstance(stmt.targets[0].value, ast.Name)
                     and stmt.targets[0].value.id == "self")
            if not plain:
                if escaped:
                    out.clear()
                    continue
                # `self.x[:] = <literal>` replaces every element outright, and
                # the population's memory models reset a constructor-built
                # buffer exactly that way. The container's type is the prior
                # value's own -- a slice assignment never changes it -- so it
                # folds only over an entry already known to be a list.
                name = _whole_slice_write(stmt)
                if name is not None:
                    filled = _literal_cells(
                        stmt.value, constants, owner,
                        is_builtin=is_builtin)
                    if filled is not None and isinstance(out.get(name), list):
                        out[name] = list(filled)
                        continue
                # A field written some other way at this position -- augmented,
                # multi-target, subscripted, or under a condition or loop
                # inside this statement -- is not fixed outright by what came
                # before it, so the write erases rather than being invisible.
                for name in _self_writes(stmt):
                    out.pop(name, None)
                continue
            target = stmt.targets[0]
            value = _const_int(stmt.value, constants, owner)
            if value is not None:
                out[target.attr] = value
                continue
            cells = _literal_cells(
                stmt.value, constants, owner, typed,
                is_builtin=is_builtin)
            if cells is not None:
                # ``typed`` preserves the literal's own sequence type -- the
                # representation contract compares constants against what the
                # native model holds, and a tuple recorded as a list fails an
                # honest run. The default stays type-erased because
                # `proc.initial` is the IR's one sequence kind and its readers
                # copy with `list(...)`. Same walk, same order (the **last**
                # top-level assignment wins, as in Python), one flag on the
                # shape of the answer -- so value and type cannot come from two
                # different rules (review 23: a separate first-match AST reader
                # recorded the first assignment's type while this fold kept the
                # last one's value).
                out[target.attr] = cells
                continue
            if escaped:
                # An unreadable right-hand side may run code holding the
                # escaped alias, which writes more than this one target.
                out.clear()
            else:
                out.pop(target.attr, None)
    return out


def _whole_slice_write(stmt):
    """The field name when ``stmt`` is ``self.<name>[:] = ...``, else None."""
    if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1):
        return None
    target = stmt.targets[0]
    if not (isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Attribute)
            and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "self"
            and isinstance(target.slice, ast.Slice)
            and target.slice.lower is None and target.slice.upper is None
            and target.slice.step is None):
        return None
    return target.value.attr


def _bare_self(stmt):
    """Whether ``self`` escapes as an object rather than an attribute base."""
    shielded = set()
    for node in ast.walk(stmt):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            if node.attr == "__dict__":
                return True
            shielded.add(id(node.value))
    return any(isinstance(node, ast.Name) and node.id == "self"
               and id(node) not in shielded for node in ast.walk(stmt))


def _self_calls(stmt):
    """Every ``self.<name>(...)`` a statement calls, however deeply."""
    return {node.func.attr for node in ast.walk(stmt)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"}


def _escaping_fields(stmt):
    """Fields handed whole to code or aliases the fold does not track."""

    def reads(node):
        return {n.attr for n in ast.walk(node)
                if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id == "self"
                and isinstance(n.ctx, ast.Load)}

    fields = set()
    for node in ast.walk(stmt):
        if isinstance(node, ast.Call):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                fields.update(reads(arg))
        elif isinstance(node, ast.Assign):
            self_slots = all(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self" for target in node.targets)
            if not self_slots:
                fields.update(reads(node.value))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            target_is_slot = (isinstance(node.target, ast.Attribute)
                              and isinstance(node.target.value, ast.Name)
                              and node.target.value.id == "self")
            if node.value is not None and not target_is_slot:
                fields.update(reads(node.value))
        elif isinstance(node, ast.NamedExpr):
            fields.update(reads(node.value))
        elif isinstance(node, (ast.For, ast.comprehension)):
            fields.update(reads(node.iter))
    return fields


def _mutated_fields(stmt):
    """Fields on which a statement invokes a potentially mutating method."""
    fields = set()
    for node in ast.walk(stmt):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)):
            continue
        base = node.func.value
        while isinstance(base, (ast.Subscript, ast.Attribute)):
            if (isinstance(base, ast.Attribute)
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "self"):
                fields.add(base.attr)
                break
            base = base.value
    return fields


def _self_writes(stmt):
    """Every ``self.<name>`` a statement assigns, however deeply."""
    names = set()
    for node in ast.walk(stmt):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For)):
            targets = [node.target]
        else:
            continue
        for target in targets:
            for element in ast.walk(target):
                if (isinstance(element, ast.Attribute)
                        and isinstance(element.value, ast.Name)
                        and element.value.id == "self"):
                    names.add(element.attr)
    return names


def _is_tuple_literal(node):
    """Whether a foldable sequence literal denotes a tuple."""
    if isinstance(node, ast.Tuple):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return (_is_tuple_literal(node.left)
                or _is_tuple_literal(node.right))
    return False


def _output_names(fn):
    """The output variables a procedure names itself.

    Taken from every return rather than from one, so that a procedure whose
    returns disagree is rejected instead of leaving an output unassigned on
    some path. A procedure that returns values without naming them yields no
    names here and the caller supplies them: the design's output ports are
    what the two sides are compared on, so they are the names to use.
    """
    names = None
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return):
            continue
        value = node.value
        if value is None or (isinstance(value, ast.Constant)
                             and value.value is None):
            continue                      # a step that produces no output
        # A step may return several transactions on one beat, and they name the
        # same outputs, so the names are read from any of them.
        mappings = ([e for e in value.elts if isinstance(e, ast.Dict)]
                    if isinstance(value, ast.List) else
                    [value] if isinstance(value, ast.Dict) else [])
        for mapping in mappings:
            keys = []
            for key in mapping.keys:
                if not isinstance(key, ast.Constant) \
                        or not isinstance(key.value, str):
                    raise LoweringError(
                        node, "output name that is not a literal string", code="output-name-not-literal")
                keys.append(key.value)
            if names is None:
                names = keys
            elif set(names) != set(keys):
                raise LoweringError(node, "returns naming different outputs", code="outputs-disagree-across-returns")
    return names or []
