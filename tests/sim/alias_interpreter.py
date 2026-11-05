"""The static construction-alias interpreter, as **test support only**.

Reviews 28-35 grew this abstract interpreter one counterexample at a
time, and review 36 settled that no reading of agent-written Python can
carry alias soundness: authority lives in the runtime witness
(`construction.construction_facts`). The interpreter is kept here for the
oracle tests that pin its behaviour at the hazard level; nothing in
production imports it (review 37: a second analyzer with no consumer
does not get to live in the production tree).
"""

import ast

from behave.sim.behavior.extract import ScopeResolver, _const_int


def constants_in_scope(model):
    """The test interpreter's flat view of the production authority.

    The interpreter itself is deliberately non-authoritative, but its oracle
    cases still need constants.  Derive that compatibility view from the one
    production resolver instead of keeping a second spelling-based constant
    analyzer alive solely for tests.
    """
    resolver = ScopeResolver(model)
    out = dict(resolver.lib_consts)
    out.update(resolver.model_consts)
    out.update(resolver.class_consts)
    out.update(resolver.self_consts)
    return out


def reset_field_aliases(model, constants=None):
    """Field pairs that may share one mutable object when the step starts.

    ``self.x = self.t`` leaves two names on one object, and the IR has no
    aliases: it initializes x and t independently, so a step that writes
    x[0] moves the native t and not the IR's, and an audit that happens not
    to disturb the shared cells passes (review 28: a=1 agreed, a=2 did not).

    Occurrence-based pattern matching cannot say which bindings still hold
    when construction finishes (review 29: a local rebound after the alias
    hid it, a binding under an `if` was invisible, a helper bound fields the
    scan never read, and an alias the reset itself dissolved was refused).
    So this is a small transfer analysis in **real execution order** --
    constructor then reset, helpers entered at their call sites -- over
    points-to sets: each expression denotes a set of abstract objects (empty
    means provably immutable), a plain binding **replaces** the target's set
    (rebinding releases an alias), an in-place store **adds** to it, both
    arms of a branch are analyzed and joined by union, loops run to a small
    fixpoint, and anything outside the analysis -- an unresolved call, bare
    self escaping, a statement kind with no rule -- havocs: every field may
    then alias every other, fail closed. What is returned are the unordered
    field pairs whose sets intersect **at exit**, and the caller filters
    them with what it knows: the lowering refuses a pair only where an end
    of it is array-typed in the IR, since an IR scalar has value semantics
    exactly as Python's immutable numbers do -- which is what keeps the
    population's scalar initializations (`self.shift_q = self.seed`,
    `self.init_remaining = self.init_total`) admitted.
    """
    constants = constants or {}
    registry = {}
    for fn in (list(model.steps.values()) + list(model.helpers.values())
               + [m for m in (model.reset, model.constructor)
                  if m is not None]):
        if not fn.decorator_list:
            registry[fn.name] = fn
    # Module-level functions are source this analysis can read, so a plain
    # call to one is entered like a helper (review 30: `keep(self.t)` stored
    # the alias in a global and `take()` returned it). Anything else that is
    # not a whitelisted pure builtin havocs.
    # A decorated def's runtime callable is not its AST body (review 32:
    # @replace turned passthrough into keep), so it earns no inlining: a
    # call to it havocs like any unreadable callable.
    module_fns = {fn.name: fn for fn in getattr(model.tree, "body", ())
                  if isinstance(fn, ast.FunctionDef)
                  and not fn.decorator_list}
    # Every def shadows a builtin of the same name, decorated or not
    # (review 33: @replace def min impersonated the builtin): "bound in the
    # module" and "safe to inline" are different sets.
    module_def_names = {fn.name for fn in getattr(model.tree, "body", ())
                        if isinstance(fn, (ast.FunctionDef,
                                           ast.AsyncFunctionDef))}
    # Names the module itself binds to something other than those defs:
    # every Store anywhere in a top-level statement (a module walrus binds
    # too, review 32), imports, classes and their decorator expressions. A
    # builtin contract applies only to a name nothing shadows (review 31:
    # `min = keep`), and a def the module also rebinds is not trusted.
    module_assigned = set()
    for node in getattr(model.tree, "body", ()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            if isinstance(node, ast.ClassDef):
                module_assigned.add(node.name)
            scan = list(node.decorator_list)
        else:
            scan = [node]
        for root in scan:
            for sub in ast.walk(root):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx,
                                                            ast.Store):
                    module_assigned.add(sub.id)
                elif isinstance(sub, ast.Import):
                    for alias in sub.names:
                        module_assigned.add(
                            alias.asname or alias.name.split(".")[0])
                elif isinstance(sub, ast.ImportFrom):
                    for alias in sub.names:
                        module_assigned.add(alias.asname or alias.name)
    module_fns = {name: fn for name, fn in module_fns.items()
                  if name not in module_assigned}

    #: Builtins with a known alias contract: scalar-valued, returning one of
    #: their arguments, or building a fresh container over the arguments.
    SCALAR_BUILTINS = frozenset({
        "int", "float", "bool", "str", "len", "abs", "sum", "ord", "chr",
        "round", "pow", "divmod", "hash", "repr", "range", "isinstance",
        "issubclass", "callable", "id", "all", "any", "bin", "hex", "oct",
        "format", "print"})
    PASS_THROUGH_BUILTINS = frozenset({"min", "max"})
    CONTAINER_BUILTINS = frozenset({
        "list", "tuple", "set", "frozenset", "dict", "sorted", "reversed",
        "enumerate", "zip", "map", "filter", "iter", "next", "bytearray",
        "bytes"})

    HAVOC = ("havoc",)
    #: The instance itself, as a value. Bare self is not immutable "nothing"
    #: (review 32: q = self; q.bind() read as a no-op call on an immutable
    #: receiver): it is a first-class token, and every consumption point
    #: that cannot prove it safe -- an attribute on it, a call argument, a
    #: receiver -- havocs.
    SELF = ("self",)
    EMPTY = frozenset()

    def _pure(ids):
        """Whether every object is one this analysis built from literals.

        A ("cont", ...) id is a container the interpreter itself constructed
        out of immutable scalars and other such containers, so the whole
        reachable graph is builtin-typed: no __int__, no __lt__, no
        __format__ of the agent's can run under it. That is the proof a
        builtin contract needs (review 32: min(..., key=...) executed user
        code; int(param) can too, through __int__). Anything else -- a
        parameter, a global, a field initial, the instance -- may dispatch
        user code and gets no contract.
        """
        return all(i[0] == "cont" for i in ids)

    def _touched(ids, st):
        """Havoc when an operation would dispatch on an unproven value.

        Comparison, truthiness, hashing, iteration, formatting, arithmetic,
        attribute and subscript access each call a dunder the value's own
        class chooses (review 33: `evil == 0` ran __eq__, set.add ran
        __hash__, a for ran __iter__, an attribute ran a property). A
        proven value -- an immutable scalar or a container built here from
        literals -- dispatches only into the interpreter's C. Everything
        else may run the agent's code, so the operation havocs.
        """
        if _pure(ids):
            return False
        _havoc(st)
        return True

    def _spread(elts, st, stack):
        """Elements of a literal, in order, with `*x` iterating x.

        Unpacking runs the operand's __iter__ (review 35: `[*evil]` built
        the alias), so a starred element is touched like any iteration.
        """
        inner = EMPTY
        for element in elts:
            if isinstance(element, ast.Starred):
                ids = _eval(element.value, st, stack)
                _touched(ids, st)
                inner = inner | ids
            else:
                inner = inner | _eval(element, st, stack)
        return inner

    def _slice_ids(node, st, stack):
        """Evaluate everything inside a subscript's brackets (review 33: a
        call hid in the index and was never evaluated)."""
        if isinstance(node, ast.Slice):
            return frozenset().union(
                EMPTY, *(_eval(part, st, stack)
                         for part in (node.lower, node.upper, node.step)
                         if part is not None))
        if isinstance(node, ast.Tuple):
            return frozenset().union(
                EMPTY, *(_slice_ids(e, st, stack) for e in node.elts))
        return _eval(node, st, stack)

    class _State:
        __slots__ = ("fields", "global_names", "globals", "havoc", "locals")

        def __init__(self, fields=None, locals_=None, havoc=False,
                     globals_=None, global_names=None):
            self.fields = dict(fields or {})
            self.locals = dict(locals_ or {})
            self.havoc = havoc
            self.globals = dict(globals_ or {})
            self.global_names = set(global_names or ())

        def copy(self):
            return _State(self.fields, self.locals, self.havoc,
                          self.globals, self.global_names)

    def _field(st, name):
        got = st.fields.get(name, frozenset({("init", name)}))
        return got | {HAVOC} if st.havoc else got

    def _havoc(st):
        st.havoc = True
        for name in list(st.fields):
            st.fields[name] = st.fields[name] | {HAVOC}

    def _join(a, b):
        out = _State(havoc=a.havoc or b.havoc,
                     global_names=a.global_names | b.global_names)
        for name in set(a.fields) | set(b.fields):
            out.fields[name] = _field(a, name) | _field(b, name)
        for name in set(a.locals) | set(b.locals):
            out.locals[name] = (a.locals.get(name, EMPTY)
                                | b.locals.get(name, EMPTY))
        for name in set(a.globals) | set(b.globals):
            out.globals[name] = (a.globals.get(name, EMPTY)
                                 | b.globals.get(name, EMPTY))
        return out

    def _assign_state(st, other):
        st.fields, st.locals, st.havoc = \
            other.fields, other.locals, other.havoc
        st.globals, st.global_names = other.globals, other.global_names

    def _absorb(st, fork):
        """Join a conditionally-executed fork back into the main state."""
        _assign_state(st, _join(st, fork))

    def _merge_states(st, states):
        if not states:
            return
        merged = states[0]
        for state in states[1:]:
            merged = _join(merged, state)
        _assign_state(st, merged)

    def _same(a, b):
        return (a.havoc == b.havoc and a.fields == b.fields
                and a.locals == b.locals and a.globals == b.globals
                and a.global_names == b.global_names)

    def _global_decls(fn):
        """The names ``global`` declares anywhere in ``fn``'s own scope.

        Python's `global` is lexical: it covers the whole function whatever
        statement it sits under, so treating it as a runtime statement let a
        declaration inside a loop go unseen until an iteration the fixpoint
        never ran (review 31). Nested functions keep their own scopes.
        """
        names, todo = set(), list(fn.body)
        while todo:
            node = todo.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(node, ast.Global):
                names |= set(node.names)
            todo.extend(ast.iter_child_nodes(node))
        return names

    def _self_attr(node):
        return (node.attr if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self" else None)

    def _eval(node, st, stack):
        """The abstract objects ``node`` may denote; empty means immutable."""
        if node is None:
            return EMPTY
        if _const_int(node, constants) is not None:
            return EMPTY
        if isinstance(node, ast.Constant):
            return EMPTY
        if isinstance(node, ast.Name):
            if node.id in st.locals and node.id not in st.global_names:
                return st.locals[node.id]
            if node.id == "self":
                return frozenset({SELF})
            if node.id in st.globals:
                return st.globals[node.id]
            # A module-level name is one object shared by every read of it.
            return frozenset({("global", node.id)})
        if isinstance(node, ast.Attribute):
            name = _self_attr(node)
            if name == "__dict__":
                _havoc(st)
                return frozenset({HAVOC})
            if name is not None:
                return _field(st, name)
            base = _eval(node.value, st, stack)
            # A descriptor or property runs on attribute access, so only a
            # proven base is read through (SELF included: any field, read or
            # written, through a non-syntactic self).
            if _touched(base, st):
                return frozenset({HAVOC})
            return base
        if isinstance(node, ast.Subscript):
            # An element or a slice shares whatever its container reaches,
            # the index is evaluated (a call hides there), and indexing an
            # unproven container or with an unproven index dispatches
            # __getitem__ / __hash__ / __index__.
            base = _eval(node.value, st, stack)
            index = _slice_ids(node.slice, st, stack)
            if _touched(base | index, st):
                return frozenset({HAVOC})
            return base
        if isinstance(node, ast.Tuple):
            # A tuple is immutable; it carries whatever its elements do.
            return _spread(node.elts, st, stack)
        if isinstance(node, ast.List):
            inner = _spread(node.elts, st, stack)
            return inner | {("cont", "list", id(node))}
        if isinstance(node, ast.Set):
            # Set members are hashed on construction.
            inner = _spread(node.elts, st, stack)
            if _touched(inner, st):
                return frozenset({HAVOC})
            return inner | {("cont", "set", id(node))}
        if isinstance(node, ast.Dict):
            # Python evaluates key, value, key, value -- not all keys first
            # (review 34: the order decided which helper's rebinding won).
            # Keys are hashed on construction; values are only stored; a
            # None key is a **mapping unpacked, which iterates and hashes.
            inner = EMPTY
            for key, value in zip(node.keys, node.values):
                if key is not None:
                    key_ids = _eval(key, st, stack)
                    if _touched(key_ids, st):
                        return frozenset({HAVOC})
                    inner = inner | key_ids
                got = _eval(value, st, stack)
                if key is None and _touched(got, st):
                    return frozenset({HAVOC})
                inner = inner | got
            return inner | {("cont", "dict", id(node))}
        if isinstance(node, ast.BinOp):
            left = _eval(node.left, st, stack)
            right = _eval(node.right, st, stack)
            # Every operator is a dunder of the operand's class.
            if _touched(left | right, st):
                return frozenset({HAVOC})
            if isinstance(node.op, (ast.Add, ast.Mult)) and (left or right):
                # + and * build a fresh container out of proven operands,
                # of the operands' own carrier kind.
                carriers = {i[1] for i in left | right if i[0] == "cont"}
                carrier = carriers.pop() if len(carriers) == 1 else "mixed"
                return frozenset({("cont", carrier, id(node))})
            return EMPTY
        if isinstance(node, ast.BoolOp):
            # `a or b` evaluates b only on some paths (review 34: an eager
            # right side dissolved an alias short-circuit keeps). The first
            # operand runs on the main path; each later one runs on a fork
            # that joins back. Truth testing dispatches __bool__.
            out = _eval(node.values[0], st, stack)
            if _touched(out, st):
                return frozenset({HAVOC})
            for value in node.values[1:]:
                fork = st.copy()
                got = _eval(value, fork, stack)
                _touched(got, fork)
                out = out | got
                _absorb(st, fork)
            return out
        if isinstance(node, ast.IfExp):
            if _touched(_eval(node.test, st, stack), st):
                return frozenset({HAVOC})
            taken, skipped = st.copy(), st.copy()
            out = (_eval(node.body, taken, stack)
                   | _eval(node.orelse, skipped, stack))
            _assign_state(st, _join(taken, skipped))
            return out
        if isinstance(node, ast.Compare):
            # a < b < c evaluates c only when a < b held: the first
            # comparison is on the main path, each further comparator on a
            # fork. `is`/`is not` are identity, dispatch-free; every other
            # comparison runs an operand's own dunder.
            operands = (_eval(node.left, st, stack)
                        | _eval(node.comparators[0], st, stack))
            for comparator in node.comparators[1:]:
                fork = st.copy()
                operands = operands | _eval(comparator, fork, stack)
                _absorb(st, fork)
            if all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
                return EMPTY
            if _touched(operands, st):
                return frozenset({HAVOC})
            return EMPTY
        if isinstance(node, (ast.UnaryOp, ast.JoinedStr, ast.FormattedValue)):
            inner = frozenset().union(
                EMPTY, *(_eval(child, st, stack)
                         for child in ast.iter_child_nodes(node)
                         if isinstance(child, ast.expr)))
            if _touched(inner, st):
                return frozenset({HAVOC})
            return EMPTY
        if isinstance(node, ast.Starred):
            return _eval(node.value, st, stack)
        if isinstance(node, ast.NamedExpr):
            got = _eval(node.value, st, stack)
            if isinstance(node.target, ast.Name):
                st.locals[node.target.id] = got
            return got
        if isinstance(node, ast.Call):
            return _call(node, st, stack)
        if isinstance(node, ast.GeneratorExp):
            # Creating a generator runs nothing but the outermost iterable;
            # the body is a computation that has not happened, so the value
            # is opaque and any later consumption of it havocs.
            first = _eval(node.generators[0].iter, st, stack)
            if _touched(first, st):
                return frozenset({HAVOC})
            return frozenset({("opaque", id(node))})
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp)):
            # Interpreted as the loop it is (review 34): the outermost
            # iterable is evaluated on the main path, the body zero or more
            # times on forks joined back -- an alias the body would dissolve
            # survives the zero-iteration path -- and the targets live in
            # the comprehension's own scope, not the caller's (a walrus
            # inside still leaks, by the letter of the language).
            first = _eval(node.generators[0].iter, st, stack)
            if _touched(first, st):
                return frozenset({HAVOC})
            targets = {sub.id
                       for gen in node.generators
                       for sub in ast.walk(gen.target)
                       if isinstance(sub, ast.Name)}
            entry_locals = dict(st.locals)
            head = st.copy()
            inner = EMPTY
            stable = False
            for _ in range(64):
                work = head.copy()
                # Every prefix of the chain is a path of its own (review
                # 35): a filter that came back false keeps its own effects
                # and skips the rest, and a nested generator may yield zero
                # times after its iterable ran. Each such stop-state joins
                # at the end of the round.
                stops = []
                broken = False
                for position, gen in enumerate(node.generators):
                    if getattr(gen, "is_async", False):
                        _havoc(work)
                        broken = True
                        break
                    iter_ids = first if position == 0 \
                        else _eval(gen.iter, work, stack)
                    if position:
                        if _touched(iter_ids, work):
                            broken = True
                            break
                        stops.append(work.copy())
                    _bind(gen.target, iter_ids, None, work, stack)
                    for cond in gen.ifs:
                        if _touched(_eval(cond, work, stack), work):
                            broken = True
                            break
                        stops.append(work.copy())
                    if broken:
                        break
                if not broken:
                    if isinstance(node, ast.DictComp):
                        keys = _eval(node.key, work, stack)
                        _touched(keys, work)
                        inner = inner | keys | _eval(node.value, work, stack)
                    else:
                        got = _eval(node.elt, work, stack)
                        if isinstance(node, ast.SetComp):
                            _touched(got, work)
                        inner = inner | got
                grown = _join(head, work)
                for stop in stops:
                    grown = _join(grown, stop)
                if _same(grown, head):
                    stable = True
                    break
                head = grown
            if not stable:
                _havoc(head)
            _assign_state(st, head)
            st.locals = {name: ids for name, ids in st.locals.items()
                         if name not in targets}
            for name in targets:
                if name in entry_locals:
                    st.locals[name] = entry_locals[name]
            carrier = {ast.ListComp: "list", ast.SetComp: "set",
                       ast.DictComp: "dict"}[type(node)]
            return inner | {("cont", carrier, id(node))}
        # An expression with no rule here may denote anything.
        _havoc(st)
        return frozenset({HAVOC})

    def _call(node, st, stack):
        plain = list(node.args)
        if (any(isinstance(a, ast.Starred) for a in plain)
                or any(kw.arg is None for kw in node.keywords)):
            # *args / **kwargs at the call site: no rule binds these.
            _havoc(st)
            return frozenset({HAVOC})
        # Python's order (review 35): the callable expression runs first,
        # then positional arguments, then keywords, then the invocation. A
        # name lookup and `self.m` have no effects of their own; a method's
        # receiver and a computed callable do.
        func = node.func
        method = _self_attr(func)
        recv = None
        if method is None and isinstance(func, ast.Attribute):
            recv = _eval(func.value, st, stack)
        elif method is None and not isinstance(func, ast.Name):
            # A computed callable (a call result, a lambda, a subscript):
            # its effects run, and the invocation itself is unmodeled.
            _eval(func, st, stack)
            for arg in plain:
                _eval(arg, st, stack)
            for kw in node.keywords:
                _eval(kw.value, st, stack)
            _havoc(st)
            return frozenset({HAVOC})
        arg_vals = [_eval(arg, st, stack) for arg in plain]
        kw_vals = {kw.arg: _eval(kw.value, st, stack)
                   for kw in node.keywords}
        arg_ids = frozenset().union(EMPTY, *arg_vals, *kw_vals.values())
        if method is not None:
            fn = registry.get(method)
            if fn is None or method in stack or len(stack) > 8:
                _havoc(st)
                return frozenset({HAVOC})
            return _inline(fn, arg_vals, kw_vals, st, stack + [method])
        if SELF in arg_ids:
            # The instance itself handed to code below: semantic, not
            # syntactic, so `q = self; f(q)` is the same escape (review 32).
            _havoc(st)
            return frozenset({HAVOC})
        if isinstance(func, ast.Attribute):
            # A method contract exists only where the receiver's whole
            # object graph is proven: containers this analysis built
            # (`self.k.append(v)` stores v's objects into k), or immutable
            # scalars, whose methods must then see only proven arguments
            # ("{}".format(obj) runs obj's own __format__). Any other
            # receiver -- the instance through a local, a field holding an
            # agent's object, a module object -- is a backchannel (reviews
            # 31-32: vault.keep / vault.take, q = self; q.bind()). The
            # receiver was evaluated before the arguments, above.
            if not recv:
                if _pure(arg_ids):
                    return EMPTY
                _havoc(st)
                return frozenset({HAVOC})
            if SELF in recv or not _pure(recv) or node.keywords:
                # A keyword on a container method is where callbacks live
                # (sort(key=...)), and those run user code.
                _havoc(st)
                return frozenset({HAVOC})
            carriers = {i[1] for i in recv}
            if not _pure(arg_ids) and not (func.attr == "append"
                                           and carriers == {"list"}):
                # One contract per (carrier, method), not per method name
                # (review 34: bytearray.append runs the argument's
                # __index__). Exactly list.append stores without looking at
                # its argument; every other method on every carrier
                # iterates, hashes, compares or converts what it is given.
                _havoc(st)
                return frozenset({HAVOC})
            for name in list(st.fields):
                if st.fields[name] & recv:
                    st.fields[name] = st.fields[name] | arg_ids
            for name in list(st.locals):
                if st.locals[name] & recv:
                    st.locals[name] = st.locals[name] | arg_ids
            return recv | arg_ids
        if isinstance(func, ast.Name):
            # A contract applies only to a name nothing shadows: a local, a
            # tracked global, a lexical global declaration, or a module
            # binding takes the name away from both the source defs and the
            # builtins (review 31).
            shadowed = (func.id in st.locals or func.id in st.globals
                        or func.id in st.global_names
                        or func.id in module_assigned)
            fn = None if shadowed else module_fns.get(func.id)
            if fn is not None:
                if func.id in stack or len(stack) > 8:
                    _havoc(st)
                    return frozenset({HAVOC})
                return _inline(fn, arg_vals, kw_vals, st,
                               stack + [func.id], is_method=False)
            # A builtin contract needs proof that no user code can run under
            # it: no keywords (where callbacks live) and every argument's
            # object graph built here from literals -- int(param) dispatches
            # __int__, sorted(xs) dispatches __lt__ on the elements
            # (review 32).
            if (not shadowed and func.id not in module_def_names
                    and not node.keywords and _pure(arg_ids)):
                if func.id in SCALAR_BUILTINS:
                    return EMPTY
                if func.id in PASS_THROUGH_BUILTINS:
                    return arg_ids
                if func.id in CONTAINER_BUILTINS:
                    carrier = {"list": "list", "sorted": "list",
                               "set": "set", "dict": "dict",
                               "bytearray": "bytearray",
                               "tuple": "tuple", "frozenset": "frozenset",
                               "bytes": "bytes"}.get(func.id, "iter")
                    return arg_ids | frozenset({("cont", carrier,
                                                 id(node))})
        # A call this analysis cannot read may store its arguments anywhere
        # and return anything (review 30: keep(self.t) then take()).
        _havoc(st)
        return frozenset({HAVOC})

    def _inline(fn, arg_vals, kw_vals, st, stack, is_method=True):
        params = [a.arg for a in fn.args.args]
        if is_method and params and params[0] == "self":
            params = params[1:]
        if fn.args.vararg is not None or fn.args.kwarg is not None \
                or fn.args.posonlyargs or fn.args.kwonlyargs:
            # Signatures this binding has no rule for.
            _havoc(st)
            return frozenset({HAVOC})
        if len(arg_vals) > len(params) \
                or any(k not in params for k in kw_vals):
            _havoc(st)
            return frozenset({HAVOC})
        # Python's own call semantics: positionals in order, keywords by
        # name (review 30: keywords were bound by position), defaults for
        # the rest -- a default is evaluated once at definition time, so its
        # allocation site is genuinely shared between calls.
        bound = dict(zip(params, arg_vals))
        for name, value in kw_vals.items():
            if name in bound:
                _havoc(st)
                return frozenset({HAVOC})
            bound[name] = value
        defaults = fn.args.defaults or []
        needed = [(params[len(params) - len(defaults) + i], default)
                  for i, default in enumerate(defaults)
                  if params[len(params) - len(defaults) + i] not in bound]
        if needed:
            # A default is evaluated once, at definition time, in module
            # scope. This evaluation is frozen accordingly: no caller
            # locals, no mutated globals (review 32: it read the current
            # global map), and a default that calls anything is not modeled
            # at all -- havoc. The stable per-node site id preserves the
            # shared-between-calls object.
            if any(isinstance(sub, ast.Call)
                   for _, default in needed for sub in ast.walk(default)):
                _havoc(st)
                return frozenset({HAVOC})
            saved_scope = st.locals, st.global_names, st.globals
            st.locals, st.global_names, st.globals = {}, set(), {}
            try:
                for name, default in needed:
                    bound[name] = _eval(default, st, stack)
            finally:
                st.locals, st.global_names, st.globals = saved_scope
        for name in params:
            if name not in bound:
                bound[name] = frozenset({("param", fn.name, name)})
        saved_locals, saved_names = st.locals, st.global_names
        st.locals = bound
        st.global_names = _global_decls(fn)
        exits = {"return": [], "break": [], "continue": []}
        try:
            live = _run_body(fn.body, st, stack, exits)
            states = ([st.copy()] if live else []) \
                + [state for state, _ in exits["return"]] \
                + exits["break"] + exits["continue"]
            _merge_states(st, states)
            return frozenset().union(
                EMPTY, *(ids for _, ids in exits["return"]))
        finally:
            st.locals, st.global_names = saved_locals, saved_names

    def _store_into(target, value, st, stack):
        """A subscript store: the container now reaches ``value``'s objects.

        The index is evaluated (a call hides there, review 33), storing
        through an unproven container dispatches __setitem__, and a store
        this analysis cannot resolve at all is an escape.
        """
        root, index = target, EMPTY
        while isinstance(root, (ast.Subscript, ast.Attribute)):
            if isinstance(root, ast.Subscript):
                index = index | _slice_ids(root.slice, st, stack)
            name = _self_attr(root) if isinstance(root, ast.Attribute) \
                else None
            if name is not None:
                held = _field(st, name)
                if _touched(index, st) or _touched(held, st):
                    return
                st.fields[name] = held | value
                return
            root = root.value
        if _touched(index, st):
            return
        if (isinstance(root, ast.Name) and root.id in st.locals
                and root.id not in st.global_names):
            recv = st.locals[root.id]
            if _touched(recv, st):
                return
            for name in list(st.fields):
                if st.fields[name] & recv:
                    st.fields[name] = st.fields[name] | value
            st.locals[root.id] = recv | value
            return
        _havoc(st)

    def _bind(target, value, value_node, st, stack):
        name = _self_attr(target) if isinstance(target, ast.Attribute) \
            else None
        if name is not None:
            st.fields[name] = value  # rebinding releases the old alias
            return
        if isinstance(target, ast.Name):
            if target.id in st.global_names:
                st.globals[target.id] = value
            else:
                st.locals[target.id] = value
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            pairwise = (isinstance(value_node, (ast.Tuple, ast.List))
                        and len(value_node.elts) == len(target.elts))
            for i, sub in enumerate(target.elts):
                if pairwise:
                    _bind(sub, _eval(value_node.elts[i], st, stack),
                          value_node.elts[i], st, stack)
                else:
                    _bind(sub, value, None, st, stack)
            return
        if isinstance(target, ast.Subscript):
            _store_into(target, value, st, stack)
            return
        if isinstance(target, ast.Starred):
            _bind(target.value, value, None, st, stack)
            return
        _havoc(st)

    def _run_body(body, st, stack, exits):
        """Run ``body`` on ``st``; False when control does not fall through.

        Review 30: Return ended nothing, so the interpreter executed the
        unreachable code after it, and a rebinding there "released" an alias
        the runtime keeps. Return, Break and Continue each record the state
        at their point into ``exits`` and stop the body; a branch falls
        through only through its live arms.
        """
        for stmt in body:
            if not _stmt(stmt, st, stack, exits):
                return False
        return True

    def _stmt(stmt, st, stack, exits):
        if isinstance(stmt, ast.Assign):
            value = _eval(stmt.value, st, stack)
            for target in stmt.targets:
                _bind(target, value, stmt.value, st, stack)
            return True
        if isinstance(stmt, ast.AnnAssign):
            if stmt.value is not None:
                _bind(stmt.target, _eval(stmt.value, st, stack),
                      stmt.value, st, stack)
            return True
        if isinstance(stmt, ast.AugAssign):
            value = _eval(stmt.value, st, stack)
            name = _self_attr(stmt.target) \
                if isinstance(stmt.target, ast.Attribute) else None
            if name is not None:
                held = _field(st, name)
                # An augmented operator is a dunder of both operands.
                if _touched(held | value, st):
                    return True
                st.fields[name] = held | value
            elif isinstance(stmt.target, ast.Name):
                if stmt.target.id in st.global_names:
                    st.globals[stmt.target.id] = \
                        st.globals.get(stmt.target.id, EMPTY) | value
                else:
                    st.locals[stmt.target.id] = \
                        st.locals.get(stmt.target.id, EMPTY) | value
            else:
                _store_into(stmt.target, value, st, stack)
            return True
        if isinstance(stmt, ast.Expr):
            _eval(stmt.value, st, stack)
            return True
        if isinstance(stmt, ast.If):
            _touched(_eval(stmt.test, st, stack), st)  # truth dispatches
            taken, skipped = st.copy(), st.copy()
            live_taken = _run_body(stmt.body, taken, stack, exits)
            live_skipped = _run_body(stmt.orelse, skipped, stack, exits)
            fall = ([taken] if live_taken else []) \
                + ([skipped] if live_skipped else [])
            _merge_states(st, fall)
            return bool(fall)
        if isinstance(stmt, (ast.For, ast.While)):
            iter_ids = EMPTY
            if isinstance(stmt, ast.For):
                iter_ids = _eval(stmt.iter, st, stack)
                _touched(iter_ids, st)  # iterating dispatches __iter__
                # The target binds only when an iteration happens: binding
                # it here rebound the caller's local on the zero-iteration
                # path too (review 34), so it binds inside the fork below.
            else:
                _touched(_eval(stmt.test, st, stack), st)
            # A true fixpoint over the loop head: the lattice is finite (the
            # ids are allocation sites, parameters, globals, initials and
            # HAVOC) and the transfer is monotone under the join, so the
            # head stabilizes; the bound is an engineering guard, and
            # reaching it without stability havocs rather than pretending
            # the last round was the answer (review 30: four rounds missed a
            # five-stage propagation).
            head = st.copy()
            breaks = []
            stable = False
            for _ in range(64):
                work = head.copy()
                if isinstance(stmt, ast.For):
                    _bind(stmt.target, iter_ids, None, work, stack)
                else:
                    _touched(_eval(stmt.test, work, stack), work)
                inner = {"return": exits["return"],
                         "break": [], "continue": []}
                live = _run_body(stmt.body, work, stack, inner)
                breaks.extend(inner["break"])
                grown = head
                for state in ([work] if live else []) + inner["continue"]:
                    grown = _join(grown, state)
                if _same(grown, head):
                    stable = True
                    break
                head = grown
            if not stable:
                _havoc(head)
            # `else` runs only on normal exhaustion, never after a break
            # (review 31: a rebinding in the else "released" an alias that a
            # broken-out iteration keeps).
            _assign_state(st, head)
            live_else = _run_body(stmt.orelse, st, stack, exits)
            fall = ([st.copy()] if live_else else []) + breaks
            if not fall:
                return False
            _merge_states(st, fall)
            return True
        if isinstance(stmt, ast.Return):
            # The value first: `return keep(self.t)` writes a global, and a
            # state copied before the evaluation lost that write (review 31).
            value = _eval(stmt.value, st, stack)
            exits["return"].append((st.copy(), value))
            return False
        if isinstance(stmt, ast.Break):
            exits["break"].append(st.copy())
            return False
        if isinstance(stmt, ast.Continue):
            exits["continue"].append(st.copy())
            return False
        if isinstance(stmt, ast.Global):
            # Already collected lexically at function entry (_global_decls).
            return True
        if isinstance(stmt, ast.Assert):
            _touched(_eval(stmt.test, st, stack), st)
            return True
        if isinstance(stmt, (ast.Pass, ast.Nonlocal, ast.Import,
                             ast.ImportFrom)):
            return True
        # A statement with no rule here runs code this analysis cannot see.
        _havoc(st)
        return True

    st = _State()
    for method in (model.constructor, model.reset):
        if method is None:
            continue
        # The harness may pass one object to two parameters, so every
        # parameter carries a shared token besides its own: two fields bound
        # from different parameters may still be one object (review 32).
        st.locals = {a.arg: frozenset({("param", method.name, a.arg),
                                       ("param-shared",)})
                     for a in method.args.args if a.arg != "self"}
        st.global_names = _global_decls(method)
        exits = {"return": [], "break": [], "continue": []}
        live = _run_body(method.body, st, [method.name], exits)
        states = ([st.copy()] if live else []) \
            + [state for state, _ in exits["return"]] \
            + exits["break"] + exits["continue"]
        _merge_states(st, states)
    universe = set(st.fields)
    if st.havoc:
        universe |= set(model.state_contract())
    hazards = []
    for a in sorted(universe):
        for b in sorted(universe):
            if b <= a:
                continue
            if _field(st, a) & _field(st, b):
                hazards.append((a, b))
    return hazards
