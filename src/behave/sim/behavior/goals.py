"""Static goal enumeration and instrumentation for a behavior model.

The verifier classifies goals, not lines: a goal is a named event a run either
reaches or misses, and the question asked of each is whether some stimulus
reaches it. That requires the goal set to exist *before* anything runs, which
is what this module provides. It also requires a run to report goals under the
identifiers the enumeration used, which is why the instrumentation and the
enumeration come out of one traversal here rather than being matched up
afterwards by line number.

A goal carries three things besides its identifier. Its *family* says what kind
of event it is, its *class* says whether reaching it records an exercised case
or a defect, and its *outcome* says which side of a two-sided event it is.
Family and class are separate fields because they are independent: an
arithmetic event and a branch outcome are different families and both are
coverage, while a division by zero is a failure family and safety class.

Control-flow identifiers are

    branch:<file>:<line>:<col>:<ordinal>:<T|F>

Statement-entry identifiers are

    location:<file>:<line>:<col>:<ordinal>:entry

with ``ordinal`` the position of the site in a preorder walk, so two sites on
one source line stay distinct and an edit below a site does not move it.

The sites enumerated here are

  location   entry to each executable statement in an admitted procedure
  decision   the condition of ``if``, ``while``, a conditional expression, or
             a comprehension filter, taken whole
  condition  each operand of a boolean operator, which is a site of its own
             because ``and`` / ``or`` short-circuit: an operand that is never
             evaluated is a case the stimulus never exercised, and folding it
             into the enclosing decision would hide that
  loop       whether a loop ran at least once, whose outcomes are the entered
             and the zero-iteration cases

A decision whose condition is a boolean operator is enumerated *in addition to*
its operands. Covering the operands does not in general cover both outcomes of
the decision they form, so the two are separate goals.
"""

from __future__ import annotations

import ast

NAMESPACE = "branch"
LOCATION_NAMESPACE = "location"
HOOK = "_behave_hit"
LOOP_HOOK = "_behave_loop"

#: Which places a goal distinguishes, which is a policy and not a property of the
#: source. A branch written inside a helper is one goal however many callsites reach
#: it, because a goal is a position in the source and a helper has one position.
#:
#: The alternative is defensible and is not what this does: a goal per callsite would
#: report which of the calls exercised the branch. It was treated here as the correct
#: reading and the shared identifier as a defect, so `shared_goals` withheld a
#: certificate from 267 of 836 models for conforming to the policy the code
#: implements. `blas_dsyr2` inlines a helper 115 times, and under a callsite policy
#: two of its goals become 230, which is a denominator that says more about the
#: inlining than about the design.
#:
#: Written down here because it was previously written in two places, in the
#: identifier's shape and in the appendix, and they disagreed. A run under a different
#: policy is a run about a different goal set, so the version travels with the
#: certificate.
POLICY = "source-site"
POLICY_VERSION = "python-structural/12"

COVERAGE = "coverage"
SAFETY = "safety"

DECISION = "decision"
CONDITION = "condition"
LOOP = "loop"
LOCATION = "location"

__all__ = [
    "CONDITION",
    "COVERAGE",
    "DECISION",
    "HOOK",
    "LOCATION",
    "LOOP",
    "LOOP_HOOK",
    "NAMESPACE",
    "SAFETY",
    "Goal",
    "Site",
    "catalog",
    "enumerate_sites",
    "goal_id",
    "goal_set",
    "instrument_source",
]


class Goal:
    """One outcome of one site, with everything a report needs about it."""

    __slots__ = (
        "cls",
        "col",
        "family",
        "file",
        "id",
        "line",
        "ordinal",
        "outcome",
    )

    def __init__(self, id, family, cls, outcome, line, col, ordinal, file):
        self.id = id
        self.family = family
        self.cls = cls
        self.outcome = outcome
        self.line = line
        self.col = col
        self.ordinal = ordinal
        self.file = file

    def as_dict(self):
        return {"id": self.id, "family": self.family, "class": self.cls,
                "outcome": self.outcome, "line": self.line, "col": self.col,
                "ordinal": self.ordinal, "file": self.file}

    def __repr__(self):
        return f"Goal({self.id}, {self.family})"

    def __eq__(self, other):
        return isinstance(other, Goal) and other.id == self.id

    def __hash__(self):
        return hash(self.id)


class Site:
    """A source site and the finite events it contributes as goals.

    Decisions and loops retain both outcomes, even when one is statically
    impossible. Reachability results do not change the declared denominator.

    ``callsite`` is the path of inlined calls a goal was reached through, and is
    empty under the ``source-site`` policy: a branch inside a helper is one goal
    however many callsites reach it. Enumeration, instrumentation and lowering
    use the same source identity.
    """

    __slots__ = (
        "callsite",
        "col",
        "family",
        "file",
        "index",
        "kind",
        "line",
        "namespace",
        "ordinal",
        "outcomes",
        "owner",
    )

    def __init__(self, ordinal, line, col, kind, family, file,
                 outcomes=(True, False), callsite=(), owner=None, *, index=None,
                 namespace=NAMESPACE):
        self.ordinal = ordinal
        self.index = ordinal if index is None else index
        self.namespace = namespace
        self.line = line
        self.col = col
        self.kind = kind
        self.family = family
        self.file = file
        self.outcomes = tuple(outcomes)
        self.callsite = tuple(callsite)
        self.owner = owner

    @property
    def base(self):
        # The callsite path contributes nothing while it is empty, so an identifier
        # under this policy reads exactly as it did before and no recorded hit or
        # catalogue entry moves.
        path = "".join(f"@{one}" for one in self.callsite)
        return (f"{self.namespace}:{self.file}:{self.line}:{self.col}:"
                f"{self.ordinal}{path}")

    @property
    def goals(self):
        return tuple(goal_id(self, o) for o in self.outcomes)

    def goal(self, outcome):
        return Goal(goal_id(self, outcome), self.family, COVERAGE,
                    _outcome_name(self, outcome), self.line, self.col,
                    self.ordinal, self.file)

    def __repr__(self):
        return f"Site({self.base}, {self.kind})"


def goal_id(site, outcome):
    """Identifier of one declared outcome of ``site``."""
    return f"{site.base}:{_outcome_name(site, outcome)}"


def _outcome_name(site, outcome):
    if site.family == LOCATION:
        if outcome is not True:
            raise ValueError("a location site has only its entry event")
        return "entry"
    if type(outcome) is not bool:
        raise TypeError("a control-flow site outcome is Boolean")
    return "T" if outcome else "F"


#: What a loop's two goals are, said once, because there are three pairs of events a
#: reader could take them for and they are not the same:
#:
#:   zero-trip against non-zero-trip     which is what these are
#:   normal exhaustion against break     a different family, not declared here
#:   reaching the unroll bound or not    a capability question, not a goal
#:
#: The pair here is the trip count against zero, and both the lowering and the
#: instrumented host report it that way: the mark is guarded by a test of the count
#: emitted before the loop, so a run with a count of one reaches only the non-zero
#: goal even though the unrolled guard is false on the iterations that follow.
LOOP_OUTCOMES = ("non-zero trip count", "zero trip count")


class _Walker(ast.NodeTransformer):
    """Assign stable ordinals and optionally instrument the same sites.

    Enumeration and rewriting share this traversal so that a site's ordinal is
    the same number in both, which is what makes a reported hit and a
    statically enumerated goal the same object.
    """

    def __init__(self, file, rewrite, names=(), declared_sites=None):
        self.file = file
        self.rewrite = rewrite
        self._declared_sites = declared_sites
        self.sites = []
        self._owners = []
        self._ordinals = {NAMESPACE: 0, LOCATION_NAMESPACE: 0}
        self._names = set(names)

    def _temporary(self, index):
        name = f"_behave_while_entered_{index}"
        while name in self._names:
            name += "_"
        self._names.add(name)
        return name

    def _site(self, node, kind, family, outcomes=(True, False)):
        namespace = LOCATION_NAMESPACE if family == LOCATION else NAMESPACE
        ordinal = self._ordinals[namespace]
        self._ordinals[namespace] += 1
        owner = self._owners[-1] if self._owners else None
        index = len(self.sites)
        if self._declared_sites is None:
            s = Site(ordinal, node.lineno, node.col_offset, kind, family,
                     self.file, outcomes, owner=owner, index=index, namespace=namespace)
        else:
            if index >= len(self._declared_sites):
                raise ValueError("instrumentation produced an undeclared source site")
            s = self._declared_sites[index]
            if (s.ordinal, s.line, s.col, s.kind, s.family, s.file, s.outcomes,
                    s.owner, s.index, s.namespace, s.callsite) != (
                    ordinal, node.lineno, node.col_offset, kind, family, self.file,
                    tuple(outcomes), owner, index, namespace, ()):
                raise ValueError("instrumentation differs from the declared source site")
        self.sites.append(s)
        return s

    def _call(self, hook, ordinal, arg, node):
        call = ast.Call(func=ast.Name(id=hook, ctx=ast.Load()),
                        args=[ast.Constant(value=ordinal), arg], keywords=[])
        return ast.copy_location(call, node)

    def _wrap(self, node, kind, family):
        site = self._site(node, kind, family)
        inner = self.visit(node)
        if not self.rewrite:
            return inner
        return self._call(HOOK, site.index, inner, node)

    def _decision(self, node, kind):
        """A decision is a site; when it is a boolean operator its operands are
        sites too, and both are enumerated."""
        site = self._site(node, kind, DECISION)
        inner = self._atoms(node) if isinstance(node, ast.BoolOp) else self.visit(node)
        if not self.rewrite:
            return inner
        return self._call(HOOK, site.index, inner, node)

    def _atoms(self, node):
        """Operands of a boolean operator, each a condition site."""
        node.values = [
            self._atoms(v) if isinstance(v, ast.BoolOp)
            else self._wrap(v, "atom", CONDITION)
            for v in node.values
        ]
        return node

    # ── statements and expressions carrying a decision ──

    #: Methods that are read rather than lowered. `reset` and `__init__` say what
    #: the state holds before the first step, and `_initial_state` reads the
    #: assignments they make; nothing lowers them, so a decision written in one is
    #: not a decision the step takes.
    NOT_LOWERED = ("reset", "__init__")

    @staticmethod
    def _is_location(node):
        # A docstring is metadata, not an executed command in the admitted
        # Behavior procedure.  Prefixing it would also change ``__doc__``.
        return not (isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str))

    def visit(self, node):
        """Declare statement entry before visiting the statement's contents."""

        location = None
        if isinstance(node, ast.stmt) and self._owners and self._is_location(node):
            location = self._site(
                node, type(node).__name__.lower(), LOCATION, outcomes=(True,))
        transformed = super().visit(node)
        if location is None or not self.rewrite:
            return transformed
        hit = ast.Expr(value=self._call(
            HOOK, location.index, ast.Constant(value=True), node))
        ast.copy_location(hit, node)
        return [hit, *transformed] if isinstance(transformed, list) else [hit, transformed]

    def _statements(self, nodes):
        out = []
        for node in nodes:
            transformed = self.visit(node)
            if isinstance(transformed, list):
                out.extend(transformed)
            elif transformed is not None:
                out.append(transformed)
        return out

    def visit_FunctionDef(self, node):
        """Keep declaration and instrumentation ordinals identical.

        This step profile excludes construction/reset bodies. The catalogue
        subsequently selects helper owners using the resolved call closure.
        """
        if node.name in self.NOT_LOWERED:
            return node
        owner = (node.name, node.lineno, node.col_offset)
        self._owners.append(owner)
        try:
            # Signature annotations/defaults belong to definition time, not
            # to execution of this function's body.
            node.body = self._statements(node.body)
            return node
        finally:
            self._owners.pop()

    def visit_AnnAssign(self, node):
        # Keep the original statement/location and annotation spelling. Only
        # its RHS and target evaluations can produce runtime events.
        if node.value is not None:
            node.value = self.visit(node.value)
            node.target = self.visit(node.target)
        elif isinstance(node.target, (ast.Attribute, ast.Subscript)):
            node.target.value = self.visit(node.target.value)
            if isinstance(node.target, ast.Subscript):
                node.target.slice = self.visit(node.target.slice)
        return node

    def visit_If(self, node):
        node.test = self._decision(node.test, "if")
        node.body = self._statements(node.body)
        node.orelse = self._statements(node.orelse)
        return node

    def visit_While(self, node):
        site = self._site(node, "while-loop", LOOP)
        node.test = self._decision(node.test, "while")
        node.body = self._statements(node.body)
        node.orelse = self._statements(node.orelse)
        if not self.rewrite:
            return node

        # This flag belongs to one dynamic loop entry, not to its final false
        # condition. Record entry before a body can break, return, or fail.
        name = self._temporary(site.index)
        initialize = ast.Assign(
            targets=[ast.Name(id=name, ctx=ast.Store())], value=ast.Constant(False))
        entered = ast.Assign(
            targets=[ast.Name(id=name, ctx=ast.Store())], value=ast.Constant(True))

        def first(outcome, following=()):
            hit = ast.Expr(self._call(HOOK, site.index, ast.Constant(outcome), node))
            return ast.copy_location(ast.If(
                test=ast.UnaryOp(ast.Not(), ast.Name(id=name, ctx=ast.Load())),
                body=[hit, *following], orelse=[]), node)

        node.body.insert(0, first(True, (entered,)))
        node.orelse.insert(0, first(False))
        return [ast.copy_location(initialize, node), node]

    def visit_IfExp(self, node):
        node.test = self._decision(node.test, "ifexp")
        node.body = self.visit(node.body)
        node.orelse = self.visit(node.orelse)
        return node

    def visit_BoolOp(self, node):
        # A boolean operator outside a decision, as in ``flag = a and b``,
        # still short-circuits, so its operands are sites.
        return self._atoms(node)

    # ── loops ──

    def visit_For(self, node):
        node.iter = self._loop(node.iter, "for")
        node.target = self.visit(node.target)
        node.body = self._statements(node.body)
        node.orelse = self._statements(node.orelse)
        return node

    def _loop(self, node, kind):
        """Declare both zero/nonzero activation, including for constant iterables."""
        site = self._site(node, kind, LOOP)
        inner = self.visit(node)
        if not self.rewrite:
            return inner
        return self._call(LOOP_HOOK, site.index, inner, node)

    def visit_comprehension(self, node):
        node.iter = self._loop(node.iter, "comp")
        node.ifs = [self._decision(f, "comp-if") for f in node.ifs]
        return node


def enumerate_sites(source, file="behavior.py"):
    """Sites of ``source``, in preorder."""
    walker = _Walker(file, rewrite=False)
    walker.visit(ast.parse(source))
    return walker.sites


def goal_set(source, file="behavior.py"):
    """Every goal identifier declared by ``source``."""
    return [g for s in enumerate_sites(source, file) for g in s.goals]


def catalog(source, file="behavior.py"):
    """Every goal of ``source`` as a record, in site order."""
    return [s.goal(outcome)
            for s in enumerate_sites(source, file)
            for outcome in s.outcomes]


def instrument_source(source, file="behavior.py", *, plan=None):
    """Return ``(tree, sites)`` with every site wrapped in a hook.

    The caller binds ``HOOK`` to ``(index, value) -> value`` and ``LOOP_HOOK``
    to ``(index, iterable) -> iterable``.  Statement-entry calls use the same
    value-preserving hook with ``True``.  Each index names the corresponding
    item in the returned site sequence, so replay needs no frame or line-number
    recovery.

    A prepared plan supplies those exact Site objects. The traversal checks its
    producer-to-declaration correspondence once before source execution.
    """
    if plan is not None:
        plan.check_source(source, file)
    tree = ast.parse(source)
    # Include argument, import, global/nonlocal and definition bindings as well
    # as Name nodes. Private instrumentation locals must not shadow source names.
    names = {
        item for node in ast.walk(tree) for _field, value in ast.iter_fields(node)
        for item in (value if isinstance(value, list) else (value,))
        if isinstance(item, str)
    }
    walker = _Walker(file, rewrite=True, names=names,
                     declared_sites=None if plan is None else plan.sites)
    tree = walker.visit(tree)
    if plan is not None and len(walker.sites) != len(plan.sites):
        raise ValueError("instrumentation omitted a declared source site")
    ast.fix_missing_locations(tree)
    return tree, walker.sites if plan is None else plan.sites
