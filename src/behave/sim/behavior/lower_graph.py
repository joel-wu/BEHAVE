"""Lowering that emits a control-flow graph.

The turn. `lowering` produces a predicated term and `cfg.of_command` reads a
graph back out of it, which is backwards: the lowering knows the control flow
and flattens it, and then something has to recover what was flattened. Here the
graph is what is produced, and `cfg.if_convert` is the only place predication is
written.

Done as a subclass rather than a rewrite, and the reason is worth stating. Most
of the expression rules do not care: they hoist what a value needs and return the
value, and "what must run before this value" is exactly "what is already in the
block being filled". So `before` emits into the block and those rules are
untouched.

What does change is what happens to a command once it is complete. A term
appends it to a sequence; here it is emitted, and emitting splits control flow
into blocks: an `If` ends the block with a branch and opens a join, a `Fail`
ends the block with a terminator. So the rules that build conditionals keep
building them, and the shape they describe becomes edges at the one place that
knows about blocks.

Where the line falls between the two lowerings is the whole design, and the
first version put it in the wrong place. It inherited the compound statements
as well, and those lower a nested body into a command that is emitted
afterwards, while the expression rules inside that body queue their effects
*now*, into whatever block is open. Those are the same block only for a
top-level straight-line statement. For a loop they are not: the guard for
``a // (i + 1)`` landed in the block before the loop and read the index before
any iteration assigned it, and only ``z := ...`` travelled inside the body
command. One symptom of one boundary in the wrong place.

So the split is by kind, not by convenience. Expressions are inherited, because
"what must run before this value" is "what the open block already holds" and
that is true wherever the expression is. Control flow is written here, because
only this side knows that a body belongs in a block of its own. A loop body is
lowered into one local acyclic region, if-converted once, and retained by the IR
as a structured loop.  Its finite expansion is an encoding-plan decision, not
part of the source-to-IR translation.
"""

from __future__ import annotations

import ast

from ..symbolic import cfg, ir, ssa
from . import flow_facts
from ..symbolic import ranges
from .lower_returns import _NamedReturn, _PositionalBuilder
from .lowering import (
    CONTINUED,
    LEFT,
    NORMAL,
    RETURNED,
    LoweringError,
    _loop_else_ways,
    _loop_ways,
    _Lowerer,
    _may_break,
)
from .source_syntax import comprehension_tail

__all__ = ["GraphLowerer"]


class _Leave:
    """Control leaves the step here.

    A command only so that it can travel inside one: the rules that build arms
    hand back a command and something has to be in it saying the arm ends. It
    never reaches the encoder, since `emit` turns it into a block's terminator.

    Without it a return inside a conditional was two instructions, storing the
    transactions and setting a flag, and the arm's block fell through to the
    join like any other. Both returns of `if a: return 1` then `return 2` ran and
    the second overwrote the first.
    """

    __slots__ = ()

    def __repr__(self):
        return "leave"


class _Region:
    """Blocks lowered aside, to be wired in where their caller puts them.

    What `_scoped` produces. Its caller is about to put the subexpression's
    effects under a condition, so they must not be in the block that is open:
    that block runs either way. A list would do for instructions, and the first
    version used one, but a subexpression is not only instructions. An inlined
    call inside a conditional-expression arm builds blocks of its own, and those
    blocks go wherever the open block is, which a list cannot redirect. So the
    scope is a place in the graph rather than a container, and then there is one
    piece of state saying where things go instead of two.
    """

    __slots__ = ("exit", "head")

    def __init__(self, head, exit_):
        self.head = head
        self.exit = exit_

    def __repr__(self):
        return f"region {self.head}..{self.exit}"


class _Jump:
    """Control leaves for a named block.

    What a ``break`` is. In the term it was a flag cleared here and tested by
    every statement that could follow, in two places that had to agree about
    which flags count; here it is an edge, and the statements after it are
    simply in a block this one does not reach.
    """

    __slots__ = ("target",)

    def __init__(self, target):
        self.target = target

    def __repr__(self):
        return f"jump {self.target}"


class GraphLowerer(_Lowerer):
    """A lowerer whose output is blocks.

    "What has to run before this value" is the block being filled, which is what the
    base class's `before` means here. It is not a queue: the block already holds it,
    in order, and there is nothing to prepend it to.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.blocks = {}
        self._instrs = []
        self._labels = 0
        self.label = None
        # Through `open`, not by setting the label: `open` is what registers a
        # block, and an entry that was never registered is an entry the graph
        # does not have. Everything the step does was landing in it.
        self.open("entry")
        # Where an inlined helper's `return` goes. A helper's return leaves the
        # helper and not the step, so it is an edge like a break rather than the
        # step's terminator.
        self._returns = []
        # The shared loop frame carries the local graph's exit and owning
        # return destination. Helper continuations remain graph-local above.
        # Goal sites in source paths proved unreachable still need explicit
        # false formulas in the encoded catalogue.  They cannot live in an
        # unreachable CFG block: ``if_convert`` quite correctly omits blocks
        # with no path from entry.  Keep this representation-only accounting
        # beside the graph and prepend it once at the boundary.
        self._pruned_goal_terms = ir.Skip()
        self._pruned_goal_ids = set()

    def _account_pruned_goals(self, roots):
        terms = self._false_goal_terms(roots)
        goals = ir.goals_of(terms)
        # Goal identity is the source occurrence, not the number of call sites
        # that inline it.  One literal-false term preserves that occurrence in
        # the formula; repeating the same term at every call adds no behavior
        # and makes the retained IR grow with call multiplicity.
        if not goals or goals <= self._pruned_goal_ids:
            return
        self._pruned_goal_ids.update(goals)
        self._pruned_goal_terms = ir.Seq(self._pruned_goal_terms, terms)

    # ── the block being filled ──

    def before(self, cmd):
        """Put what a value needs first into the block being filled.

        The expression rules hoist two different kinds of thing under one word. A
        capture or an append is an instruction and belongs in the block; a guard, a
        conditional expression and a short circuit are control flow and belong as
        edges. `emit` is the one place that knows the difference, so routing every
        hoist through it makes the distinction without any rule having to know there
        are blocks at all.
        """
        self.emit(cmd)

    def mark(self):
        """Where in the graph "here" is: which block, and how full it was.

        The block matters as much as the count. An inlined call or a scoped arm can
        close the block and open another, so a later count is not comparable with an
        earlier one unless they are counts of the same block, and a value lowered
        across a block boundary needed something hoisted by definition.
        """
        return (self.label, len(self._instrs))

    def hoisted_since(self, mark):
        label, filled = mark
        return self.label != label or len(self._instrs) != filled

    def _scoped(self, lower):
        """Lower a subexpression into blocks of its own.

        The expression-level half of the same boundary the compound statements
        are on the other side of. An arm of a conditional expression and the
        operands after the first of a short circuit are evaluated only where the
        branch reaches them, so what they need to run first must not be in the
        block that is open, which runs either way: `1 if (a and (7 // a)) else 0`
        divided by zero on `a == 0`, an operand the source never evaluates.

        Blocks rather than a list, and the difference is not presentational. A
        list holds the instructions a subexpression queues, and a subexpression
        is not only instructions: an inlined call inside an arm builds blocks,
        and those go to wherever the open block is. Collecting the instructions
        while the call's blocks escaped put the arm's definitions on a path that
        did not reach the read, which the graph reported itself, exactly, as a
        use with no definition reaching it.

        The caller puts the region inside an `ir.If`, and `emit` turns that into
        a branch whose arms are blocks, so the region is entered under the
        condition that reaches it.
        """
        saved_label, saved_instrs = self.label, self._instrs
        head = self.fresh_label("s")
        exit_ = self.fresh_label("z")
        for label in (head, exit_):
            self.blocks.setdefault(label, cfg.Block(label, [], cfg.Return()))
        # Not `open`, which would discard the instructions of the block this is
        # lowered aside from.
        self.label, self._instrs = head, []
        try:
            value = lower()
            if self.label is not None:
                self.close(cfg.Goto(exit_))
        finally:
            self.label, self._instrs = saved_label, saved_instrs
        return _Region(head, exit_), value

    def fresh_label(self, kind="b"):
        self._labels += 1
        return f"{kind}{self._labels}"

    def close(self, term):
        """Finish the current block with ``term`` and return its label."""
        label = self.label
        self.blocks[label] = cfg.Block(label, self._instrs, term)
        self._instrs = []
        self.label = None
        return label

    def open(self, label):
        """Start filling ``label``.

        A label is promised as soon as it is named, since a branch names its
        join before the join exists. So it is recorded here as an empty block
        that returns, and closing it replaces that: a join nothing writes into
        and nothing follows is a real block that ends the step, and leaving it
        out left edges pointing at labels the graph did not have.
        """
        self.label = label
        self._instrs = []
        self.blocks.setdefault(label, cfg.Block(label, [], cfg.Return()))

    # ── emitting a command, which is where control flow becomes edges ──

    def emit(self, cmd):
        """Add a command to the graph, splitting its control flow into blocks.

        The one place that knows a block from a term. Every rule that used to
        append a conditional to the pending list still appends a conditional;
        it arrives here and becomes a branch and a join.  ``Seq`` is flattened
        with an explicit stack: its nesting reflects construction history, not
        source control depth, and large straight-line programs must not consume
        the Python call stack.
        """
        pending = [cmd]
        while pending:
            part = pending.pop()
            if part is None or isinstance(part, ir.Skip):
                continue
            if isinstance(part, ir.Seq):
                pending.append(part.second)
                pending.append(part.first)
                continue
            if isinstance(part, ir.If):
                then_label = self.fresh_label("t")
                else_label = self.fresh_label("e")
                join = self.fresh_label("j")
                self.blocks.setdefault(join, cfg.Block(join, [], cfg.Return()))
                self.close(cfg.Branch(part.guard, then_label, else_label))
                for label, arm in ((then_label, part.then),
                                   (else_label, part.orelse)):
                    self.open(label)
                    self.emit(arm)
                    # An arm that ended its own block, by failing, has nothing
                    # to join with. One that did not falls through to the join.
                    if self.label is not None:
                        self.close(cfg.Goto(join))
                self.open(join)
                continue
            if isinstance(part, ir.For):
                # A semantic loop is a structured instruction of the outer
                # DAG. Its body was itself lowered through a local CFG, so
                # retaining it here does not bypass graph lowering or replay
                # source lowering.
                self._spend()
                self._instrs.append(part)
                continue
            if isinstance(part, _Region):
                # Blocks that were lowered aside, entered here. Where the
                # caller put the region decides the condition they run under.
                self.close(cfg.Goto(part.head))
                self.open(part.exit)
                continue
            if isinstance(part, _Jump):
                self.close(cfg.Goto(part.target))
                self.open(self.fresh_label("dead"))
                continue
            if isinstance(part, _Leave):
                self.close(cfg.Return())
                self.open(self.fresh_label("dead"))
                continue
            if isinstance(part, ir.Fail):
                self.close(cfg.Fail(part.code, part.goal))
                # Nothing follows a failure, so what comes next is unreachable
                # and gets a block of its own that no edge enters.
                self.open(self.fresh_label("dead"))
                continue
            # One semantic command built, charged against the frontend
            # structure budget. A retained loop is charged once here; its later
            # guarded expansion is a separate EncodingPlan capability.
            self._spend()
            self._instrs.append(part)

    def _insert_straight_line(self, mark, cmd):
        """Insert a proved straight-line command at an earlier graph point."""
        instructions = []
        pending = [cmd]
        while pending:
            part = pending.pop()
            if isinstance(part, ir.Skip):
                continue
            if isinstance(part, ir.Seq):
                pending.append(part.second)
                pending.append(part.first)
                continue
            if isinstance(part, (ir.If, ir.For, ir.Fail, _Region,
                                 _Jump, _Leave)):
                raise TypeError(
                    "a helper initialization must be straight-line")
            self._spend()
            instructions.append(part)
        label, offset = mark
        if label == self.label:
            self._instrs[offset:offset] = instructions
            return
        self.blocks[label].instrs[offset:offset] = instructions

    # ── what the base class does with completed commands ──

    def _flush(self, cmd):
        """Hand the command back, since the queue is already where it belongs.

        The base class prepends its queue to the command and returns the pair as
        one term. Here the queued instructions are in the block being filled, so
        there is nothing to prepend and the command is returned unchanged. That
        holds inside `_scoped` too, since what `_scoped` changes is which block
        is being filled and not whether there is one.

        It must not emit. `_if` and the loop rules build their arms by lowering
        statements and putting the result inside a conditional, and every one of
        those statements passes through here; emitting would land the arm's
        instructions in the block that is open now, which is the one that
        branches. Both arms of a conditional ended up there, holding nothing but
        their marks, and the branch chose between two empty blocks.
        """
        return cmd

    def _return(self, node):
        """A return ends its block: the step's, or the helper's it is in.

        Both are edges now. The step's return has nowhere to go, so it is the
        `Return` terminator; a helper's goes to the block after the inlined
        body, which is what the flag was standing in for. With the flag, a
        helper written as `if c: return 1` then `return 2` ran both returns and
        the second overwrote the first, because the arm's block fell through to
        the join like any other.
        """
        if self.activation.destination is None:
            raise LoweringError(node, "return outside a procedure", code="return-outside-procedure")
        assign = self.activation.destination.assign(self, node)
        if not isinstance(self.activation.destination, _NamedReturn):
            self.activation.return_facts.append(self._flow_facts())
        if (self.loop is not None
                and self.loop.return_destination is self.activation.destination):
            # A retained loop body is converted as a closed local graph.  A
            # return therefore leaves that local graph through its loop exit;
            # the done bit carries the return across the structured-loop
            # boundary.  This applies equally to a procedure return and to a
            # return from an inlined helper: jumping directly to the helper's
            # continuation would put an edge to an outer block in the local
            # graph.
            return ir.Seq(
                assign,
                ir.Seq(ir.Assign(self.activation.destination.done, ir.Lit(1)),
                       _Jump(self.loop.exit_label)))
        if isinstance(self.activation.destination, _NamedReturn):
            return ir.Seq(assign, _Leave())
        return ir.Seq(assign, _Jump(self._returns[-1]))

    def _inlined(self, node, fn, bind):
        """Emit the helper's body into blocks, and give its returns somewhere.

        The base class hands the caller a command to run before the read. Here
        the body is emitted, which is what makes an early return inside it an
        edge, and `None` says the placing is done.
        """
        self.emit(bind)
        entry = self.mark()
        # Return arms establish the representation before the shared, typed
        # initialization is inserted at this entry.  The graph and retained
        # structured-loop body must agree on the same result carrier.
        after = self.fresh_label("h")
        self.blocks.setdefault(after, cfg.Block(after, [], cfg.Return()))
        self._returns.append(after)
        returned_facts = self.activation.return_facts
        try:
            ways = self.body(fn.body)[1]
        finally:
            self._returns.pop()
        if NORMAL in ways and self.activation.destination.required \
                and not self.activation.destination.planned.is_option:
            raise LoweringError(
                node, f"{fn.name} can finish without returning, and a "
                      f"helper that falls off its end returns None", code="helper-may-not-return")
        self._insert_straight_line(
            entry, self.activation.destination.initialize(self, node))
        reaching = list(returned_facts)
        if NORMAL in ways:
            reaching.append(self._flow_facts())
        if reaching:
            self._set_joined_flow_facts(reaching)
        if self.label is not None:
            self.close(cfg.Goto(after))
        self.open(after)

    def body(self, nodes):
        """Lower a block of statements into the block being filled.

        Emitting, everywhere, which the first version could not do: the compound
        statements were inherited, and those call this for their arms while the
        block that is open is the one *before* the branch, so an arm's
        instructions landed there. That is why the compound statements are
        written on this side now. Once they open the right block first, emitting
        is right in every position, and the two versions of this method that had
        to be told apart collapse into one.

        Nothing is returned but the completion set. A statement that leaves does
        not need what follows it guarded, because what follows it is in a block
        this one has no edge to, so `_live` has nothing to do here.
        """
        ways = {NORMAL}
        for index, node in enumerate(nodes):
            self.emit(self._location_mark(node))
            cmd, node_ways = self.stmt(node)
            self.emit(cmd)
            ways = (ways - {NORMAL}) | node_ways
            if NORMAL not in node_ways:
                # Control left at this statement, and the statement itself said
                # so in the graph: a return closed its block with `Return`, a
                # break with a `Goto` to the loop's exit. So what is written
                # after it is unreachable by construction rather than by a flag
                # every later statement tests.  Preserve only explicit false
                # terms for goal sites in that unreachable suffix.
                self._account_pruned_goals(nodes[index + 1:])
                break
        return ir.Skip(), ways

    # ── control flow, which is what this side owns ──

    def _if(self, node):
        """A conditional is a branch, two blocks and a join."""
        before, predicate = self._atom_of(node.test)
        path_atom = self._path_guard_atom(node.test)
        before, guard = self._stable_guard(before, predicate)
        self.emit(before)
        then_label = self.fresh_label("t")
        else_label = self.fresh_label("e")
        join = self.fresh_label("j")
        self.blocks.setdefault(join, cfg.Block(join, [], cfg.Return()))
        atom, positive = path_atom if path_atom is not None else (None, True)
        self.close(cfg.Branch(
            guard, then_label, else_label, atom=atom, positive=positive))
        ways = set()
        entering = self._flow_facts()
        live_facts = []
        arms = ((then_label, self._site_goal(node.test, True), node.body,
                 guard, True),
                (else_label, self._site_goal(node.test, False), node.orelse,
                 ir.Op("not", guard), False))
        for label, mark, nodes, condition, truth in arms:
            assumed = self._assumed_source_flow_facts(
                entering, node.test, predicate, truth)
            self.open(label)
            if mark:
                self._instrs.append(ir.Mark(mark))
            if assumed is None:
                self._account_pruned_goals(nodes)
                arm_ways = set()
                # This block is unreachable in the represented source domain.
                # It is not a normal return: entry-state and other exit analyses
                # must not invent a completing source path for it.
                self.close(cfg.Pruned())
            elif nodes:
                self._set_flow_facts(assumed)
                arm_ways = self.body(nodes)[1]
            else:
                # An absent else falls through, which is a way the conditional
                # as a whole can finish normally however its other arm ends.
                self._set_flow_facts(assumed)
                arm_ways = {NORMAL}
            ways |= arm_ways
            if assumed is not None and NORMAL in arm_ways:
                live_facts.append((condition, self._flow_facts()))
            if self.label is not None:
                self.close(cfg.Goto(join))
        self._set_flow_facts(
            self._joined_branch_flow_facts(guard, live_facts))
        if NORMAL in ways:
            self.open(join)
        return ir.Skip(), ways

    def _loop_region(
            self, node, body_entry, body_indices, running, *, lower_body=None):
        """Lower one loop body through a local acyclic graph.

        The resulting term is retained once inside ``ir.For``.  Local jumps
        end this body application: continue leaves ``running`` set, while
        break clears it before taking the same exit.  A step return records the
        procedure return flag and takes that exit too.  No edge from the outer
        CFG enters these temporary blocks; after if-conversion they are
        discarded.
        """
        saved_label, saved_instrs = self.label, self._instrs
        saved_blocks = set(self.blocks)
        head = self.fresh_label("l")
        exit_label = self.fresh_label("q")
        self.blocks[head] = cfg.Block(head, [], cfg.Return())
        self.blocks[exit_label] = cfg.Block(exit_label, [], cfg.Return())
        self.label, self._instrs = head, []
        body_indices = (() if body_indices is None else
                        (body_indices,) if isinstance(body_indices, str) else
                        tuple(body_indices))
        try:
            with self._loop_scope(running, indices=body_indices, exit_label=exit_label) as loop:
                breaks, continues = loop.breaks, loop.continues
                self._set_flow_facts(body_entry)
                if lower_body is None:
                    _unused, ways = self.body(node.body)
                else:
                    ways = lower_body()
                body_facts = self._flow_facts()
                if self.label is not None:
                    self.close(cfg.Goto(exit_label))
                local = {
                    label: self.blocks[label]
                    for label in set(self.blocks) - saved_blocks}
                local_graph = cfg.CFG(local, head)
                entry_defined = set(body_indices)
                flow_analysis = ssa.analyze(local_graph, entry_defined)
                required = flow_analysis.undefined_names
                defined = (
                    flow_analysis.out_of(exit_label) - entry_defined
                    if exit_label in flow_analysis.into_masks else set()
                )
                term = cfg.if_convert(local_graph)
        finally:
            self.label, self._instrs = saved_label, saved_instrs
            for label in set(self.blocks) - saved_blocks:
                del self.blocks[label]
        return (term, ways, body_facts, breaks, continues, required, defined,
                local_graph)

    def _comprehension_command(self, comp, emit, *, active=None):
        """Retain finite comprehension loops instead of copying their bodies.

        Each generator is one proved structured loop.  Its filters are CFG
        branches inside that body and a nested generator is another retained
        loop reached only through those branches.  Computed mappings remain
        expanded because their keys select physical output names at lowering
        time; ordinary list values and transaction batches have run-time
        indexed storage and need no such expansion.
        """
        if isinstance(comp, ast.DictComp):
            return super()._comprehension_command(comp, emit, active=active)

        def filters(conditions, index, continuation, pruned):
            if index == len(conditions):
                continuation()
                return
            condition = conditions[index]
            # The reducer's internal active guard precedes source filters.
            # It controls execution but does not invent a source goal/site.
            source_condition = isinstance(condition, ast.AST)
            before, predicate = (self._atom_of(condition) if source_condition
                                 else (ir.Skip(), condition))
            before, guard = self._stable_guard(before, predicate)
            self.emit(before)
            entering = self._flow_facts()
            then_label = self.fresh_label("t")
            else_label = self.fresh_label("e")
            join = self.fresh_label("j")
            self.blocks.setdefault(join, cfg.Block(join, [], cfg.Return()))
            self.close(cfg.Branch(guard, then_label, else_label))
            live = []
            for label, truth in ((then_label, True), (else_label, False)):
                self.open(label)
                mark = self._site_goal(condition, truth) if source_condition else None
                if mark:
                    self._instrs.append(ir.Mark(mark))
                assumed = (self._assumed_source_flow_facts(
                    entering, condition, predicate, truth)
                    if source_condition else entering)
                if assumed is None:
                    if truth:
                        self._account_pruned_goals((*conditions[index + 1:], *pruned))
                    self.close(cfg.Pruned())
                    continue
                self._set_flow_facts(assumed)
                if truth:
                    filters(
                        conditions, index + 1, continuation, pruned)
                if self.label is not None:
                    live.append((
                        guard if truth else ir.Op("not", guard),
                        self._flow_facts(),
                    ))
                    self.close(cfg.Goto(join))
            self._set_flow_facts(
                self._joined_branch_flow_facts(guard, live))
            if live:
                self.open(join)

        def generators(index):
            if index == len(comp.generators):
                return emit()

            gen = comp.generators[index]
            iteration = self._dynamic_bound(gen, comp)
            count, bound, value = iteration.count, iteration.bound, iteration.value
            if isinstance(count, ir.Lit):
                trip_expr = ir.Lit(max(count.value, 0))
                prefix = ir.Skip()
            else:
                trips = self.storage.fresh()
                clamped = ir.Op(
                    "ite", ir.Op("lt", count, ir.Lit(0)), ir.Lit(0), count)
                self._record_range(trips, clamped)
                trip_expr = ir.Var(trips)
                prefix = ir.Assign(trips, clamped)
            goal = self._loop_goals(gen.iter, trip_expr)
            if goal is not None:
                prefix = ir.Seq(prefix, goal)
            if bound <= 0:
                self._account_pruned_goals(comprehension_tail(comp, index))
                return prefix

            iterable_contract = self._contract_ast(gen.iter)
            element_contract = (
                iterable_contract.element
                if iterable_contract.is_sequence else None)
            if element_contract is None:
                raise LoweringError(
                    gen.iter,
                    "a comprehension iterable has no element contract",
                    code="comprehension-target-shape-unsupported",
                )

            sample = ir.Lit(0) if value is None else value(ir.Lit(0))
            missing, saved, physical = self._prepare_comprehension_target(
                gen.target, sample, element_contract)
            counter = self.storage.fresh()
            item = (
                ir.Var(counter) if value is None else value(ir.Var(counter)))
            values = self._comprehension_target_values(
                gen.target, item, element_contract)
            for name, holder in physical.items():
                builder = self.storage.carrier(_PositionalBuilder, holder)
                self.activation.substitutions[name] = ir.Var(holder) if builder is None else builder.value
            counter_bounds = ranges.Bounds(0, bound - 1)
            self.flow.bounds[counter] = counter_bounds
            finite_counter = counter_bounds.finite()
            if finite_counter is not None:
                self.flow.ranges[counter] = finite_counter
            for name, (expression, _contract) in values.items():
                for holder, field in self._binding_fields(physical[name], expression):
                    self._record_range(holder, field)
            body_entry = self._flow_facts()

            def lower_body():
                for name, (expression, _contract) in values.items():
                    for holder, field in self._binding_fields(physical[name], expression):
                        self.emit(ir.Assign(holder, field))

                def continuation():
                    self.emit(generators(index + 1))

                filters(
                    gen.ifs if active is None else [active, *gen.ifs],
                    0,
                    continuation,
                    comprehension_tail(comp, index, len(gen.ifs)),
                )
                return {NORMAL}

            try:
                (body_term, ways, body_facts, breaks, continues, required,
                 defined, local_graph) = self._loop_region(
                    gen,
                    body_entry,
                    counter,
                    None,
                    lower_body=lower_body,
                )
                minimum, maximum = self.source_loop_spans[id(gen)]
                self._reject_loop_carried_static_references(
                    gen, body_entry, [body_facts], bound)
                flow = ir._make_loop_flow(
                    minimum,
                    maximum,
                    required,
                    defined,
                    (counter,),
                    local_graph,
                    cfg.graph_fingerprint(local_graph),
                    body_term,
                    body_term,
                    trip_expr,
                )
                loop = ir.For(counter, trip_expr, body_term, flow=flow)
                exits = self._loop_exit_facts(
                    body_entry,
                    body_facts,
                    ways,
                    breaks,
                    continues,
                    may_be_empty=minimum == 0,
                )
                self._set_joined_flow_facts(
                    exits, ranges.post(loop, body_entry.ranges))
            finally:
                self._restore_comprehension_target(
                    missing, saved, physical)
                self.flow.ranges.pop(counter, None)
                self.flow.bounds.pop(counter, None)
                self.flow.unknown_ranges.discard(counter)

            return ir.Seq(prefix, loop)

        return generators(0)

    def _continue_after_returning_loop(self, returned):
        """Keep only the no-return edge after a structured loop."""
        if not returned:
            return
        live = self.fresh_label("r")
        left = self.fresh_label("u")
        self.blocks.setdefault(live, cfg.Block(live, [], cfg.Return()))
        self.blocks.setdefault(left, cfg.Block(left, [], cfg.Return()))
        self.close(cfg.Branch(
            ir.Op("eq", ir.Var(self.activation.destination.done), ir.Lit(0)), live, left))
        self.open(left)
        # A return from an inlined helper leaves that helper before it leaves any
        # source loop surrounding the call site.  The retained inner loop has
        # already been exited at this point; jump to the helper's own continuation
        # so its caller receives the value. Looking only at the active loop
        # mistakes an outer caller loop for a loop owned by the helper, while a
        # bare ``Return`` terminates the complete procedure when the helper itself
        # has a continuation block.
        if self._returns:
            self.close(cfg.Goto(self._returns[-1]))
        elif self.loop is not None:
            self.close(cfg.Goto(self.loop.exit_label))
        else:
            self.close(cfg.Return())
        self.open(live)

    def _finish_loop_else(self, node, ways, breaks, *, running, exhausts):
        """Emit the exhaustion-only arm and join it with source break exits.

        The caller installs its loop-specific exhaustion facts first. This
        owner handles only control flow: break bypasses ``else``, return leaves
        its enclosing frame, and only normal else exits join the continuation.
        """
        else_ways = set()
        final_facts = list(breaks)
        if exhausts:
            if running is None:
                else_ways = self.body(node.orelse)[1]
                if NORMAL in else_ways:
                    final_facts.append(self._flow_facts())
            else:
                else_label = self.fresh_label("e")
                after_label = self.fresh_label("x")
                self.blocks.setdefault(
                    else_label, cfg.Block(else_label, [], cfg.Return()))
                self.blocks.setdefault(
                    after_label, cfg.Block(after_label, [], cfg.Return()))
                self.close(cfg.Branch(
                    ir.Op("ne", ir.Var(running), ir.Lit(0)),
                    else_label, after_label))
                self.open(else_label)
                else_ways = self.body(node.orelse)[1]
                if self.label is not None:
                    if NORMAL in else_ways:
                        final_facts.append(self._flow_facts())
                    self.close(cfg.Goto(after_label))
                self.open(after_label)
        else:
            self._account_pruned_goals(node.orelse)

        self._set_joined_flow_facts(final_facts)
        return ir.Skip(), _loop_else_ways(
            ways, else_ways, breaks=breaks, exhausts=exhausts)

    def _for(self, node):
        """Retain one source loop body and one captured trip count.

        Source lowering proves the count has a finite range but does not copy
        the body to that maximum.  A local CFG represents the body's ordinary
        control flow once; the EncodingPlan later owns the domain-relative
        guarded expansion.
        """
        iteration_plan = self._dynamic_bound(node)
        count, bound = iteration_plan.count, iteration_plan.bound
        if isinstance(count, ir.Lit):
            trip_expr = ir.Lit(max(count.value, 0))
        else:
            trips = self.storage.fresh()
            clamped = ir.Op(
                "ite", ir.Op("lt", count, ir.Lit(0)), ir.Lit(0), count)
            self.emit(ir.Assign(trips, clamped))
            self._record_range(trips, clamped)
            trip_expr = ir.Var(trips)
        self.emit(self._loop_goals(node.iter, iteration_plan.activation(trip_expr)))
        # Evaluating the source iterable may itself execute a helper.  Those
        # effects happen before loop entry and must be in the loop transfer's
        # input facts; the captured count is then stable across all trips.
        loop_input = self._flow_facts()

        if bound == 0:
            self._account_pruned_goals(node.body)
            if node.orelse:
                return self.body(node.orelse)
            return ir.Skip(), {NORMAL}

        running = self.storage.fresh() if _may_break(node) else None
        body_indices, bind_target = self._prepare_loop_target(
            node.target, iteration_plan, node.iter)
        body_entry = self._loop_source_body_entry(
            node, self._flow_facts())
        (body_term, ways, body_facts, breaks, continues, required, defined,
         local_graph) = \
            self._with_iterator_binding(iteration_plan.binding, lambda: self._loop_region(
                node, body_entry, body_indices, running))

        counter = self.storage.fresh()
        iteration = ir.Seq(bind_target(ir.Var(counter)), body_term)
        iterator_entry, iteration = self._live_iterator_body(
            iteration_plan, ir.Var(counter), iteration)
        self.emit(iterator_entry)
        returned = RETURNED in ways
        alive = []
        if returned:
            alive.append(ir.Op("eq", ir.Var(self.activation.destination.done), ir.Lit(0)))
            self.emit(ir.Assign(self.activation.destination.done, ir.Lit(0)))
        if running is not None:
            alive.append(ir.Op("ne", ir.Var(running), ir.Lit(0)))
            self.emit(ir.Assign(running, ir.Lit(1)))
        if alive:
            guard = alive[0]
            for part in alive[1:]:
                guard = ir.Op("land", guard, part)
            iteration = ir.If(guard, iteration, ir.Skip())
        minimum, maximum = self.source_loop_spans[id(node)]
        static_exits = [body_facts, *breaks, *continues]
        self._reject_loop_carried_static_references(
            node, body_entry, static_exits, bound)
        flow = ir._make_loop_flow(
            minimum,
            maximum,
            required,
            defined,
            body_indices,
            local_graph,
            cfg.graph_fingerprint(local_graph),
            body_term,
            iteration,
            trip_expr,
        )
        loop = ir.For(counter, trip_expr, iteration, flow=flow)
        self.emit(loop)

        normal_exits = self._loop_exit_facts(
            body_entry, body_facts, ways, (), continues,
            may_be_empty=False)
        if minimum <= 0:
            # next() never bound a target on this edge. Preserve its previous
            # source identity (or its absence), not the body-entry binding.
            normal_exits.append(loop_input)
        self._set_joined_flow_facts(
            [*breaks, *normal_exits],
            ranges.post(loop, loop_input.ranges))
        # The source body is lowered once, whereas the retained loop transfer
        # above applies it for every possible trip.  Keep that semantic post
        # state for the command following a loop.  Rejoining ``body_facts``
        # below would silently replace, for example, three increments by one.
        loop_facts = self._flow_facts()
        self._continue_after_returning_loop(returned)

        if not node.orelse:
            return ir.Skip(), _loop_ways(ways)
        if normal_exits:
            # With no break, the loop postcondition is precisely the state in
            # which ``else`` starts.  A breaking loop needs the source-path
            # split below so the else arm is not applied to a break exit.
            if running is None:
                self._set_flow_facts(loop_facts)
            else:
                self._set_joined_flow_facts(normal_exits)
        return self._finish_loop_else(
            node, ways, breaks, running=running, exhausts=bool(normal_exits))

    def _exact_while(self, node):
        """Retain a proved monotone while as one semantic ``ir.For``.

        The source progress proof produces the exact natural trip expression.
        Break and return controls guard later copies, so this remains exact for
        early exits without a synthetic unwinding failure.
        """
        trip_expr, maximum, progress, body_guard = self._while_trip_count(node)
        trip_expr = self._capture(trip_expr)
        if maximum == 0:
            self.emit(self._empty_while_marks(node))
            self._account_pruned_goals(node.body)
            return self.body(node.orelse)
        loop_input = self._flow_facts()

        running = self.storage.fresh() if _may_break(node) else None
        body_entry = self._while_body_entry(node, progress, maximum)
        test_entry = body_entry
        narrowed = flow_facts.assume(body_entry, body_guard, True)
        if narrowed is not None:
            body_entry = narrowed
        counter = self.storage.fresh()
        gt = self._site_goal(node.test, True)

        def lower_body():
            # Entry ran before the captured count; retain each subsequent
            # source test inside the body graph, including its goal producers.
            self._set_flow_facts(test_entry)
            condition_pre, _ = self._scoped(lambda: self._truth_expr(node.test))
            self.emit(ir.If(ir.Op("gt", ir.Var(counter), ir.Lit(0)),
                            condition_pre, ir.Skip()))
            # The exact natural count admits this iteration only while the
            # ranking predicate holds.  Retain that fact in the local CFG:
            # otherwise an abstract entry containing zero-trip states can
            # evaluate, for example, (hole - 1) // 2 at hole == 0.  The false
            # edge is impossible by the same progress proof that owns the
            # count, not an extra source failure or a normal body exit.
            entered = self.fresh_label("w")
            exhausted = self.fresh_label("u")
            self.close(cfg.Branch(body_guard, entered, exhausted))
            self.open(exhausted)
            self.close(cfg.Pruned())
            self.open(entered)
            if gt:
                self.emit(ir.Mark(gt))
            self._set_flow_facts(body_entry)
            return self.body(node.body)[1]

        (body_term, ways, body_facts, breaks, continues, required, defined,
         local_graph) = self._loop_region(
            node, body_entry, counter, running, lower_body=lower_body)

        returned = RETURNED in ways
        active = []
        if returned:
            active.append(ir.Op("eq", ir.Var(self.activation.destination.done), ir.Lit(0)))
            self.emit(ir.Assign(self.activation.destination.done, ir.Lit(0)))
        if running is not None:
            active.append(ir.Op("ne", ir.Var(running), ir.Lit(0)))
            self.emit(ir.Assign(running, ir.Lit(1)))

        iteration = body_term
        guard = None
        if active:
            guard = active[0]
            for part in active[1:]:
                guard = ir.Op("land", guard, part)
            iteration = ir.If(guard, iteration, ir.Skip())

        minimum, planned_maximum = self.source_loop_spans[id(node)]
        static_exits = [body_facts, *breaks, *continues]
        self._reject_loop_carried_static_references(
            node, body_entry, static_exits, maximum)
        flow = ir._make_loop_flow(
            minimum,
            planned_maximum,
            required,
            defined,
            (counter,),
            local_graph,
            cfg.graph_fingerprint(local_graph),
            body_term,
            iteration,
            trip_expr,
        )
        loop = ir.For(counter, trip_expr, iteration, flow=flow)
        self.emit(self._loop_goals(node, trip_expr))
        self.emit(loop)

        gf = self._site_goal(node.test, False)
        self._set_flow_facts(test_entry)
        condition_pre, _ = self._scoped(lambda: self._truth_expr(node.test))
        final = ir.Seq(ir.If(ir.Op("gt", trip_expr, ir.Lit(0)),
                            condition_pre, ir.Skip()),
                       ir.Mark(gf) if gf else ir.Skip())
        if guard is not None:
            final = ir.If(guard, final, ir.Skip())
        self.emit(final)

        normal_exits = self._loop_exit_facts(
            body_entry, body_facts, ways, (), continues,
            may_be_empty=False)
        if minimum == 0:
            normal_exits.append(loop_input)
        self._set_joined_flow_facts(
            [*breaks, *normal_exits],
            ranges.post(loop, loop_input.ranges),
        )
        self._continue_after_returning_loop(returned)

        if not node.orelse:
            self._set_joined_flow_facts([*breaks, *normal_exits])
            return ir.Skip(), _loop_ways(ways)

        if normal_exits:
            self._set_joined_flow_facts(normal_exits)
        return self._finish_loop_else(
            node, ways, breaks, running=running, exhausts=bool(normal_exits))

    def _bounded_while(self, node):
        """Retain a data-dependent while under a finite sum ranking."""
        maximum, body_entry, test_entry = self._bounded_while_domain(node)
        if maximum == 0:
            before, _ = self._atom_of(node.test)
            self.emit(before)
            self.emit(self._empty_while_marks(node))
            self._account_pruned_goals(node.body)
            return self.body(node.orelse)
        activation, first_body, zero_body = self._while_activation(node)
        loop_input = self._flow_facts()
        active = self.storage.fresh()
        false_exits = []

        def lower_iteration():
            before, predicate = self._atom_of(node.test)
            before, guard = self._stable_guard(before, predicate)
            self.emit(before)
            tested = self._flow_facts()
            then_label = self.fresh_label("t")
            else_label = self.fresh_label("e")
            join = self.fresh_label("j")
            self.blocks.setdefault(join, cfg.Block(join, [], cfg.Return()))
            self.close(cfg.Branch(guard, then_label, else_label))

            ways = {NORMAL}
            live = []
            self.open(then_label)
            truth = self._assumed_source_flow_facts(
                tested, node.test, predicate, True)
            self._set_flow_facts(body_entry if truth is None else truth)
            goal = self._site_goal(node.test, True)
            if goal:
                self._instrs.append(ir.Mark(goal))
            self.emit(first_body)
            body_ways = self.body(node.body)[1]
            ways |= body_ways
            if self.label is not None and NORMAL in body_ways:
                live.append((guard, self._flow_facts()))
                self.close(cfg.Goto(join))

            self.open(else_label)
            false = self._assumed_source_flow_facts(
                tested, node.test, predicate, False)
            false = test_entry if false is None else false
            self._set_flow_facts(false)
            goal = self._site_goal(node.test, False)
            if goal:
                self._instrs.append(ir.Mark(goal))
            self.emit(zero_body)
            self._instrs.append(ir.Assign(active, ir.Lit(0)))
            false_exits.append(false)
            live.append((ir.Op("not", guard), false))
            self.close(cfg.Goto(join))

            self._set_flow_facts(
                self._joined_branch_flow_facts(guard, live))
            self.open(join)
            return ways

        running = self.storage.fresh() if _may_break(node) else None
        (body_term, ways, body_facts, breaks, continues, required, defined,
         local_graph) = self._loop_region(
            node, test_entry, None, running,
            lower_body=lower_iteration)

        returned = RETURNED in ways
        guards = [ir.Op("ne", ir.Var(active), ir.Lit(0))]
        if returned:
            guards.append(ir.Op("eq", ir.Var(self.activation.destination.done), ir.Lit(0)))
            self.emit(ir.Assign(self.activation.destination.done, ir.Lit(0)))
        if running is not None:
            guards.append(ir.Op("ne", ir.Var(running), ir.Lit(0)))
            self.emit(ir.Assign(running, ir.Lit(1)))
        self.emit(ir.Assign(active, ir.Lit(1)))
        self.emit(activation)
        guard = guards[0]
        for part in guards[1:]:
            guard = ir.Op("land", guard, part)
        iteration = ir.If(guard, body_term, ir.Skip())
        counter = self.storage.fresh()
        checks = maximum + 1
        trip_expr = ir.Lit(checks)
        self._reject_loop_carried_static_references(
            node, body_entry, [body_facts, *breaks, *continues], maximum)
        flow = ir._make_loop_flow(
            checks,
            checks,
            required,
            defined,
            (counter,),
            local_graph,
            cfg.graph_fingerprint(local_graph),
            body_term,
            iteration,
            trip_expr,
        )
        loop = ir.For(counter, trip_expr, iteration, flow=flow)
        self.emit(loop)

        false_facts = (flow_facts.join(false_exits)
                       if false_exits else body_entry)
        self._set_joined_flow_facts(
            [*breaks, false_facts],
            ranges.post(loop, loop_input.ranges),
        )
        self._continue_after_returning_loop(returned)

        if not node.orelse:
            self._set_joined_flow_facts([*breaks, false_facts])
            return ir.Skip(), _loop_ways(ways)

        self._set_flow_facts(false_facts)
        return self._finish_loop_else(
            node, ways, breaks, running=running, exhausts=True)

    def _exit_bounded_while(self, node):
        """Retain a constant-true loop under a cutoff-ranking proof."""

        visits, ranking, entry_range = self._while_exit_bound(node)
        body_entry = self._exit_ranking_body_entry(
            node, ranking, entry_range)
        running = self.storage.fresh()
        (body_term, ways, body_facts, breaks, continues, required, defined,
         local_graph) = self._loop_region(
            node, body_entry, None, running)

        returned = RETURNED in ways
        guards = [ir.Op("ne", ir.Var(running), ir.Lit(0))]
        if returned:
            guards.append(ir.Op("eq", ir.Var(self.activation.destination.done), ir.Lit(0)))
            self.emit(ir.Assign(self.activation.destination.done, ir.Lit(0)))
        self.emit(ir.Assign(running, ir.Lit(1)))
        guard = guards[0]
        for part in guards[1:]:
            guard = ir.Op("land", guard, part)
        iteration = body_term
        true_goal = self._site_goal(node.test, True)
        if true_goal:
            iteration = ir.Seq(ir.Mark(true_goal), iteration)
        iteration = ir.If(guard, iteration, ir.Skip())
        counter = self.storage.fresh()
        trip_expr = ir.Lit(visits)
        self._reject_loop_carried_static_references(
            node, body_entry, [body_facts, *breaks, *continues], visits)
        flow = ir._make_loop_flow(
            visits,
            visits,
            required,
            defined,
            (counter,),
            local_graph,
            cfg.graph_fingerprint(local_graph),
            body_term,
            iteration,
            trip_expr,
        )
        self.emit(self._loop_goals(node, trip_expr))
        self.emit(ir.For(counter, trip_expr, iteration, flow=flow))
        false_goal = self._site_goal(node.test, False)
        if false_goal:
            # No reachable CFG edge represents the false outcome of the
            # literal-true source condition.  Keep its exact false hit term at
            # the representation boundary, where graph pruning cannot erase
            # it from the public catalogue.
            self._pruned_goal_terms = ir.Seq(
                self._pruned_goal_terms,
                ir.If(ir.Lit(False), ir.Mark(false_goal), ir.Skip()),
            )
        self._set_joined_flow_facts(breaks)
        self._continue_after_returning_loop(returned)
        if node.orelse:
            self._account_pruned_goals(node.orelse)
        result_ways = ({NORMAL} if breaks else set())
        if returned:
            result_ways.add(RETURNED)
        return ir.Skip(), result_ways

    def stmt(self, node):
        """One statement. Loop jumps are the only ones intercepted here.

        The rest reach the inherited rules, and the compound ones among those
        are overridden above, so what arrives here as a command is always
        straight-line or a conditional an expression built.
        """
        if isinstance(node, ast.Break):
            if self.loop is None or self.loop.exit_label is None:
                raise LoweringError(node, "break outside a loop", code="break-outside-loop")
            self.loop.breaks.append(self._flow_facts())
            jump = _Jump(self.loop.exit_label)
            if self.loop.running is not None:
                jump = ir.Seq(ir.Assign(self.loop.running, ir.Lit(0)), jump)
            return jump, {LEFT}
        if isinstance(node, ast.Continue):
            if self.loop is None or self.loop.exit_label is None:
                raise LoweringError(
                    node, "continue outside a loop", code="continue-outside-loop")
            self.loop.continues.append(self._flow_facts())
            return _Jump(self.loop.exit_label), {CONTINUED}
        return _Lowerer.stmt(self, node)

    def finish(self):
        """The graph, with the exit block closed."""
        if self.label is not None:
            self.close(cfg.Return())
        return cfg.CFG(self.blocks, "entry")

    def as_command(self):
        """The graph flattened, which is what everything downstream reads.

        This is the point of having measured `if_convert` first. The lowering
        produces a graph; what the encoder, the interpreter and every check
        consume is the term the graph flattens to, and that flattening is known
        to compute the same step on every model in the population. So the turn
        does not need the rest of the package to change, and the graph is what
        the questions about control flow are asked of.
        """
        graph = self.finish()
        self.graph = graph
        term = ir.Seq(self._pruned_goal_terms, cfg.if_convert(graph))
        self.control_flow = ir._make_control_flow_authority(graph, term)
        # The flags if-conversion introduces are variables of the step like any
        # other, so the caller has to be able to find them.
        self.storage.temporaries.extend(sorted(
            {c.var for c in ir.walk(term)
             if isinstance(c, ir.Assign) and c.var.startswith("#")}))
        return term
