"""Control flow as a graph, before it is flattened into conditions.

The step the encoder consumes is a term: a tree of ``Seq``, ``If`` and ``For``
in which leaving early is a flag that later conditions test. That is the output
of if-conversion, and we were writing it directly. It works, and everything
downstream of it is fine, but it throws away the shape it came from, and every
question about that shape then has to be recovered by analysis.

The questions are not exotic. Is this variable assigned before it is read. Does
this loop run. Are these two conditions the same one. In a term they are
inference, and inference over an unrolled step is both expensive and never
quite finished, because each construct loses the information in its own way. In
a graph they are lookups: a join is a join, and a definition either reaches a
use or does not.

So the lowering builds this, and `if_convert` produces the term. What the
encoder and the interpreter see does not change; what changes is that the facts
are read off a structure that has them rather than reconstructed from one that
does not.

The outer graph is acyclic. A semantic loop is one structured instruction with
its own finite body; the query's encoding plan, not this graph, supplies the
domain-relative expansion maximum.
"""

from __future__ import annotations

import hashlib

from . import ir

__all__ = [
    "CFG",
    "Block",
    "Branch",
    "Fail",
    "Goto",
    "Pruned",
    "Return",
    "graph_fingerprint",
    "if_convert",
]


class Goto:
    """Control continues at one place."""

    __slots__ = ("target",)

    def __init__(self, target):
        self.target = target

    def successors(self):
        return (self.target,)

    def __repr__(self):
        return f"goto {self.target}"


class Branch:
    """Control continues at one of two places, by a condition.

    ``atom`` optionally names an immutable source predicate. ``positive``
    states whether taking the true edge establishes that atom. Repeated atoms
    let analyses remove contradictory paths; execution depends only on
    ``guard``.
    """

    __slots__ = ("atom", "guard", "orelse", "positive", "then")

    def __init__(self, guard, then, orelse, *, atom=None, positive=True):
        if atom is not None and type(atom) is not str:
            raise TypeError("a branch atom must be a string")
        if type(positive) is not bool:
            raise TypeError("a branch atom polarity must be Boolean")
        self.guard = guard
        self.then = then
        self.orelse = orelse
        self.atom = atom
        self.positive = positive

    def successors(self):
        return (self.then, self.orelse)

    def __repr__(self):
        return f"branch {self.guard!r} ? {self.then} : {self.orelse}"


class Return:
    """The step ends here, having produced what it produced."""

    __slots__ = ()

    def successors(self):
        return ()

    def __repr__(self):
        return "return"


class Fail:
    """The step ends here with an error code, reaching the goal reporting it.

    A terminator rather than an instruction, which is what it always was: no
    instruction after it runs. As a command in the term it was one more thing
    in a sequence, and that everything after it is dead was a fact about the
    encoding rather than about the program.
    """

    __slots__ = ("code", "goal")

    def __init__(self, code, goal=None):
        self.code = code
        self.goal = goal

    def successors(self):
        return ()

    def __repr__(self):
        return f"fail({self.code}, {self.goal})"


class Pruned:
    """A path proved unreachable in the admitted input and state domain.

    This is neither normal completion nor failure.  Keeping that distinction in
    the graph matters to analyses of its exits: representing a pruned arm by a
    ``Return`` invents a normal source execution, while representing it by a
    ``Fail`` invents an error.  It has no successor because no admitted run can
    enter it.
    """

    __slots__ = ()

    def successors(self):
        return ()

    def __repr__(self):
        return "pruned"


class Block:
    """A label, straight-line instructions, and how control leaves.

    Straight-line instructions plus structured semantic loops. Anything that
    changes the outer graph's control is the terminator; control internal to a
    loop remains in its one retained body.
    """

    __slots__ = ("instrs", "label", "term")

    def __init__(self, label, instrs=(), term=None):
        self.label = label
        self.instrs = list(instrs)
        self.term = term or Return()

    def __repr__(self):
        return (f"{self.label}: {len(self.instrs)} instrs, {self.term!r}")


class CFG:
    """The blocks of one step, and where it starts."""

    __slots__ = ("_order", "blocks", "entry")

    def __init__(self, blocks, entry):
        self.blocks = dict(blocks)
        self.entry = entry
        self._order = self._ordered_labels()

    def __getitem__(self, label):
        return self.blocks[label]

    def order(self):
        """The blocks in an order where a block comes after its predecessors.

        The graph is acyclic, so one exists. Everything that walks the graph
        wants this, and computing it once is what keeps those walks linear.
        """
        return self._order

    def _ordered_labels(self):
        """Compute the immutable topological order once at construction."""

        # Iterative because these graphs may contain thousands of blocks.
        seen, out = set(), []
        stack = [(self.entry, False)]
        while stack:
            label, done = stack.pop()
            if done:
                out.append(label)
                continue
            if label in seen:
                continue
            seen.add(label)
            stack.append((label, True))
            for succ in self.blocks[label].term.successors():
                if succ not in seen:
                    stack.append((succ, False))
        return tuple(reversed(out))

    def predecessors(self):
        """Which blocks can reach each block, as a mapping."""
        out = {label: [] for label in self.blocks}
        for label, block in self.blocks.items():
            for succ in block.term.successors():
                out[succ].append(label)
        return out

    def dominators(self):
        """Each reached block's immediate dominator, or ``None`` for the entry.

        The block every path from the entry passes through last before reaching
        this one. Pointers rather than sets: these graphs hold tens of thousands
        of blocks and a set per block would be quadratic, while the pointers are
        one per block and the questions are answered by walking them.
        """
        order = self.order()
        rank = {label: i for i, label in enumerate(order)}
        preds = self.predecessors()
        idom = {self.entry: self.entry}
        # One pass suffices because the graph is acyclic and the order is
        # topological: every predecessor of a block already has its answer.
        for label in order:
            if label == self.entry:
                continue
            known = [p for p in preds[label] if p in idom]
            if not known:
                continue                      # not reached, so not dominated
            candidate = known[0]
            for other in known[1:]:
                candidate = _meet(candidate, other, idom, rank)
            idom[label] = candidate
        idom[self.entry] = None
        return idom


    def post_dominators(self):
        """Each reached block's immediate post-dominator, or ``None`` for an end.

        The block every path from this one passes through first afterwards. The
        same computation read backwards, which on an acyclic graph is the same
        single pass over the reverse of the order.

        A block that ends the step has no post-dominator, and two blocks that end
        it have none in common, which is why this is a mapping and not a tree: a
        step with a failure and a return has two ways out.
        """
        order = self.order()
        back = list(reversed(order))
        rank = {label: i for i, label in enumerate(back)}
        ipdom = {}
        for label in back:
            succs = [s for s in self.blocks[label].term.successors()
                     if s in rank]
            if not succs:
                ipdom[label] = None           # the step ends here
                continue
            candidate = succs[0]
            for other in succs[1:]:
                candidate = _meet(candidate, other, ipdom, rank)
                if candidate is None:
                    break                     # the two ways out never rejoin
            ipdom[label] = candidate
        return ipdom

    def __repr__(self):
        return f"CFG({len(self.blocks)} blocks from {self.entry})"


def graph_fingerprint(graph):
    """Stable structural identity of one control-flow graph.

    Every block is included, not only the currently reachable order, so adding
    a dormant alternate graph cannot leave a previously sealed authority
    unchanged.  Commands use their executable image; loop proof metadata is
    bound separately by the procedure identity.
    """
    if not isinstance(graph, CFG):
        raise TypeError("a control-flow authority is a CFG")
    rows = []
    for label in sorted(graph.blocks):
        block = graph.blocks[label]
        if block.label != label:
            raise ValueError(
                f"control-flow block {label!r} carries label {block.label!r}")
        term = block.term
        if isinstance(term, Goto):
            term_image = ("goto", term.target)
        elif isinstance(term, Branch):
            term_image = (
                "branch",
                ir.expression_fingerprint(term.guard),
                term.then,
                term.orelse,
                term.atom,
                term.positive,
            )
        elif isinstance(term, Return):
            term_image = ("return",)
        elif isinstance(term, Fail):
            term_image = ("fail", term.code, term.goal)
        elif isinstance(term, Pruned):
            term_image = ("pruned",)
        else:
            raise TypeError(
                f"unknown CFG terminator {type(term).__name__}")
        rows.append((
            label,
            tuple(ir.command_fingerprint(instr) for instr in block.instrs),
            term_image,
        ))
    image = ("behavior-cfg/2", graph.entry, tuple(rows))
    return hashlib.sha256(repr(image).encode("utf-8")).hexdigest()


def _meet(a, b, up, rank):
    """The nearest block both ``a`` and ``b`` are below, walking the pointers.

    The standard step of computing dominators by iteration: walk whichever of
    the two is further from the root until they meet. ``rank`` orders the blocks
    so that "further" is a comparison rather than a search, and ``up`` is the
    pointer map being built, which is complete for everything ranked before the
    block being answered.

    ``None`` where they never meet, which for post-dominance is a real answer:
    a step that can fail and can return has two ends and no block after both.
    """
    while a is not b:
        if a is None or b is None:
            return None
        while a is not None and rank.get(a, -1) > rank.get(b, -1):
            a = up.get(a)
        while b is not None and rank.get(b, -1) > rank.get(a, -1):
            b = up.get(b)
        if a is None or b is None:
            return None
        if rank.get(a, -1) == rank.get(b, -1) and a is not b:
            return None                       # two roots, so nothing in common
    return a


def dominates(label, other, idom):
    """Whether ``label`` is on every path from the entry to ``other``."""
    at = other
    while at is not None:
        if at == label:
            return True
        at = idom.get(at)
    return False


def of_command(cmd, entry="entry", *, origins=None):
    """The graph a structured command describes.

    Built from the term rather than from the source, for now. That is not where
    it belongs: the lowering should produce this and the term should be what
    `if_convert` makes of it, and building it here means the graph is a reading
    of an already-flattened program rather than the thing that was flattened.

    It is worth having anyway, and worth having first. The questions that were
    being answered by analysis over the term are answered exactly here, so the
    machinery can be built and checked against the whole population before the
    lowering is turned around to produce it. A rewrite whose replacement has
    already been validated on every model is a different proposition from one
    that has not.

    A loop remains one structured instruction, so the outer graph remains
    acyclic without copying its body.

    Optional ``origins`` receives each block's original command. This lets
    range observers retain branch/failure identity after graph conversion.
    """
    blocks = {}
    counter = [0]

    def fresh(kind):
        counter[0] += 1
        return f"{kind}{counter[0]}"

    # An explicit worklist rather than recursion. The step this reads has its
    # loops already unrolled and its calls already inlined, so a nesting of a
    # few thousand is ordinary and the interpreter's stack is not: on the
    # population, seventy-four models were deep enough to exhaust it, which is
    # a fact about the recursion and not about them.
    work = [(cmd, entry, "exit")]
    while work:
        command, current, after = work.pop()
        if isinstance(command, ir.Seq):
            middle = fresh("b")
            work.append((command.second, middle, after))
            work.append((command.first, current, middle))
            continue
        if origins is not None:
            origins[current] = command
        if isinstance(command, ir.If):
            then_label, else_label = fresh("t"), fresh("e")
            blocks[current] = Block(current, [],
                                    Branch(command.guard, then_label,
                                           else_label))
            work.append((command.orelse, else_label, after))
            work.append((command.then, then_label, after))
            continue
        if isinstance(command, ir.For):
            blocks[current] = Block(current, [command], Goto(after))
            continue
        if isinstance(command, ir.Fail):
            blocks[current] = Block(current, [],
                                    Fail(command.code, command.goal))
            continue
        if isinstance(command, (ir.Assign, ir.Store, ir.Mark)):
            blocks[current] = Block(current, [command], Goto(after))
            continue
        blocks[current] = Block(current, [], Goto(after))     # Skip

    exit_label = "exit"
    blocks.setdefault(exit_label, Block(exit_label, [], Return()))
    return CFG(blocks, entry)


def if_convert(graph):
    """The term this graph flattens to, which is what the encoder consumes.

    Every block runs, and what it does is conditioned on control having reached
    it. That is exactly the predication the lowering used to write by hand, and
    writing it here instead means the conditions come from the graph's own
    edges rather than from flags a caller remembered to test.

    ``reached`` is the variable carrying whether a block was reached. One per
    block that needs one, assigned from its predecessors' conditions, so a join
    is a disjunction and nothing has to reason about which guards are related.

    Which blocks need one is a question about dominance, and asking it is the
    whole reason for having a graph. Two cases need no flag:

    A block reached whenever its immediate dominator was reached has that
    block's condition and not one of its own, which is the case exactly when it
    post-dominates its dominator: every path through the dominator continues to
    here. The join of a conditional is that case, and so is every block in a
    straight-line chain.

    Everything else carries a variable, and a variable is all it ever carries: a
    condition here is the truth, or a name, and never an expression built out of
    other conditions. That is what keeps it bounded. The first version inlined
    the predecessor's condition into the successor's, which is cheaper by a
    variable and grows along a chain of blocks that each have one way in, and one
    model built a conjunction deep enough to exhaust the interpreter's stack.
    Capping the depth would have been a number chosen to fit that model; keeping
    conditions to names has nothing to choose.

    How the variable is written depends on how many ways in there are, and only
    that. One way in is one assignment, since one edge decides it and no other
    can. A real join is cleared first and disjoined at each edge, which is what
    a join means and the only place the disjunction is needed.

    Not an optimisation. A flag the analyses cannot see through costs precision:
    the instructions of a block sit under `If(#reached, ...)`, the range analysis
    joins the arms of a conditional, and a variable first assigned inside one
    has no range afterwards. Thirty-four models refused to encode for want of a
    width that the term lowering had, on a `#reached` that was provably true.
    """
    order = graph.order()
    preds = graph.predecessors()
    idom = graph.dominators()
    ipdom = graph.post_dominators()
    postdom_intervals = _forest_intervals(ipdom)
    reached = set(order)
    # Predecessors that can actually bring control here. A block left behind a
    # failure has no path to it, so counting it would make a join out of
    # something with one way in.
    ways = {label: [p for p in preds[label] if p in reached] for label in order}
    # Which blocks carry a variable of their own: everything not reached whenever
    # its dominator was. The rest share that block's condition.
    carries = {label for label in order
               if label != graph.entry
               and not _follows(
                   label, idom.get(label), ipdom, postdom_intervals)}
    # And of those, the ones a single edge decides. They need no clearing: the
    # one assignment is the whole story, and it comes before the block in
    # topological order because its predecessor does.
    once = {label for label in carries if len(ways[label]) == 1}
    conditions, shared = {}, set()
    for label in order:
        # A nonconstant decision is false when its block does not run, and
        # otherwise holds the branch's truth value. It already is the true
        # edge's reaching condition. A sole successor edge may share that
        # name instead of copying it into another flag. Real joins still
        # accumulate every predecessor; no guard expression is inlined.
        parent = ways[label][0] if label in once else None
        term = graph.blocks[parent].term if parent is not None else None
        if isinstance(term, Branch) and _constant_branch(term) is None \
                and term.then == label and term.orelse != label:
            conditions[label] = ir.Var(_taken_name(parent))
            shared.add(label)
        else:
            conditions[label] = _condition(
                graph, label, carries, idom, ipdom, conditions)
    written = carries - shared
    # A flag with several ways in starts clear, and the entry's condition is the
    # truth. Without the clear the flags are read before they are written, which
    # the interpreter refuses and is right to: a name with no definition is
    # exactly what this whole layer exists to make impossible.
    out = None
    for label in order:
        if label in carries and label not in once:
            clear = ir.Assign(_reached_name(label), ir.Lit(False))
            out = clear if out is None else ir.Seq(out, clear)
        # Conditional decisions need a clear: a block that never runs never
        # makes one, and its successors read it anyway. An unconditional
        # decision is always written before its successors, so has no clear.
        if conditions[label] is not None \
                and isinstance(graph.blocks[label].term, Branch) \
                and _constant_branch(graph.blocks[label].term) is None:
            clear = ir.Assign(_taken_name(label), ir.Lit(False))
            out = clear if out is None else ir.Seq(out, clear)
    # Blocks that share a condition and follow one another go under one
    # conditional. The converted term is a flat sequence of guarded bodies, so
    # putting two adjacent ones with the same guard together is a local rewrite
    # and changes nothing about what runs; what it changes is that the encoder
    # forks once instead of twice, and that the definitions land in one branch
    # where an analysis can see them dominate the uses after them. A chain of
    # blocks sharing the entry's condition becomes straight-line code.
    held_cond, held_body = None, None

    def flush(out):
        if held_body is None:
            return out
        piece = (held_body if held_cond is None
                 else ir.If(held_cond, held_body, ir.Skip()))
        return piece if out is None else ir.Seq(out, piece)

    for label in order:
        block = graph.blocks[label]
        cond = conditions[label]
        body = None
        for instr in block.instrs:
            body = instr if body is None else ir.Seq(body, instr)
        # A branch's decision is made inside the block, not beside it. Writing
        # the guard into the flag arithmetic evaluates it whether or not the
        # block ran, and a guard usually reads a temporary that the block's own
        # instructions define, so a block that did not run left the guard
        # reading something undefined. In the term the branch is simply not
        # evaluated when its arm is not taken, and the conversion has to say
        # the same thing.
        if isinstance(block.term, Branch) and _constant_branch(block.term) is None:
            decide = ir.Assign(
                _taken_name(label), ir.Op("truth", block.term.guard))
            body = decide if body is None else ir.Seq(body, decide)
        if body is not None:
            if held_body is not None and _same(held_cond, cond):
                held_body = ir.Seq(held_body, body)
            else:
                out = flush(out)
                held_cond, held_body = cond, body
        setter = _reached_setter(graph, label, block, written, once, cond)
        if setter is not None:
            # The setter reads the decision this block just made, so the body has
            # to be written before it. That is the only thing stopping a merge.
            out = flush(out)
            held_cond, held_body = None, None
            out = setter if out is None else ir.Seq(out, setter)
    out = flush(out)
    return out if out is not None else ir.Skip()


def _same(one, other):
    """Whether two conditions are the same condition.

    Names and the truth, which is all a condition ever is here, so this is an
    identity test on what they denote rather than a comparison of expressions.
    """
    if one is None or other is None:
        return one is None and other is None
    return (isinstance(one, ir.Var) and isinstance(other, ir.Var)
            and one.name == other.name)


def _forest_intervals(parent):
    """Number a parent-pointer forest for constant-time ancestry queries.

    Immediate post-dominators form a forest: multiple terminal outcomes may
    give it multiple roots.  Walking a parent chain for every block makes a
    long sequence of joins quadratic, although the relation itself is fixed.
    Entry/exit intervals compute the same relation once in linear space and
    time.  ``None`` is returned only for a malformed cyclic parent relation;
    callers can then retain the defensive pointer walk.
    """
    nodes = set(parent)
    children = {node: [] for node in nodes}
    roots = []
    for node, above in parent.items():
        if above is None or above not in nodes:
            roots.append(node)
        elif above == node:
            return None
        else:
            children[above].append(node)

    intervals = {}
    entered = set()
    clock = 0
    for root in roots:
        stack = [(root, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                start, _ = intervals[node]
                intervals[node] = (start, clock)
                clock += 1
                continue
            if node in entered:
                return None
            entered.add(node)
            intervals[node] = (clock, None)
            clock += 1
            stack.append((node, True))
            for child in reversed(children[node]):
                stack.append((child, False))
    return intervals if entered == nodes else None


def _follows(label, dominator, ipdom, intervals=None):
    """Whether reaching ``dominator`` means reaching ``label``.

    True when ``label`` post-dominates it: every path out of the dominator
    arrives here.  The interval is the indexed form of that parent-pointer
    query.  The walk remains as a defensive fallback for a malformed relation.
    """
    if dominator is None:
        return False
    if intervals is not None:
        label_interval = intervals.get(label)
        dominator_interval = intervals.get(dominator)
        if label_interval is None or dominator_interval is None:
            return False
        return (label != dominator
                and label_interval[0] <= dominator_interval[0]
                and dominator_interval[1] <= label_interval[1])
    at = ipdom.get(dominator)
    while at is not None:
        if at == label:
            return True
        if at == dominator:
            return False                    # walked into a cycle of pointers
        at = ipdom.get(at)
    return False


def _condition(graph, label, carries, idom, ipdom, conditions):
    """What has to hold for this block to run: the truth, or a name.

    Never an expression over other conditions, which is what keeps it from
    growing along a chain. Read in topological order, so the dominator this may
    defer to has already been answered.
    """
    if label == graph.entry:
        return None
    if label in carries:
        return ir.Var(_reached_name(label))
    # It runs whenever its dominator did, so it is that condition and not a
    # copy of it: sharing the name is what makes the chain flat.
    return conditions.get(idom.get(label))


def _reached_name(label):
    return f"#reached:{label}"


def _taken_name(label):
    """Where a branch's decision is kept, made inside the block that makes it."""
    return f"#taken:{label}"


def _constant_branch(term):
    """Literal Boolean decisions need no conditionally assigned temporary.

    The reaching condition still guards both outgoing edges. Keeping the
    decision literal preserves a false edge's predicate even when this block
    is itself conditional; neither its instructions nor goal sites are removed.
    Nonliteral guards still run inside the block, after their defining writes.
    """
    if isinstance(term, Branch) and isinstance(term.guard, ir.Lit) \
            and type(term.guard.value) is bool:
        return term.guard.value
    return None


def _reached_setter(graph, label, block, carries, once, cond):
    """Tell each successor that carries a name whether control came here.

    A successor that does not carry one shares this block's condition, so there
    is nothing to write for it. That is where most of the bookkeeping went: a
    chain of blocks each told the next it was reached, and each of those was a
    clear, a disjunction and a conditional saying what the edge already said.
    """
    term = block.term
    here = ir.Lit(True) if cond is None else cond
    if isinstance(term, Goto):
        return _accumulate(term.target, here, carries, once)
    if isinstance(term, Branch):
        constant = _constant_branch(term)
        decided = (ir.Var(_taken_name(label)) if constant is None
                   else ir.Lit(constant))
        taken = ir.Op("land", here, decided) if cond is not None else decided
        missed = ir.Op("not", decided)
        if cond is not None:
            missed = ir.Op("land", here, missed)
        first = _accumulate(term.then, taken, carries, once)
        second = _accumulate(term.orelse, missed, carries, once)
        if first is None:
            return second
        return first if second is None else ir.Seq(first, second)
    if isinstance(term, Fail):
        # A failure has no successor, so nothing accumulates; what it does is
        # end the step, and that has to be written or the converted term simply
        # never fails. It was not written, and the round trip caught it: the
        # term halted on a division by zero and the graph's did not.
        fail = ir.Fail(term.code, term.goal)
        return fail if cond is None else ir.If(cond, fail, ir.Skip())
    return None


def _accumulate(label, cond, carries, once):
    """A successor is reached if any predecessor sent control to it.

    One way in is one assignment; several are disjoined onto the cleared name,
    which is what a join is and the only place the disjunction is needed.
    """
    if label not in carries:
        return None                 # it shares its dominator's condition instead
    name = _reached_name(label)
    if label in once:
        return ir.Assign(name, cond)
    return ir.Assign(name, ir.Op("lor", ir.Var(name), cond))
