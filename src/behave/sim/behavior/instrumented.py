"""Running a behavior model under instrumentation.

The goals a run reaches are read off the model itself, by compiling a copy
whose sites call a hook. That makes a hit a statically known goal rather than
something recovered from a line number, and it is what lets a witness be
replayed and its coverage rechecked.

Both frontends observe real execution events and hand their named hits to the
common record. Their source instrumentation and execution timelines differ.
"""

from __future__ import annotations

import ast
import dis

from .goals import HOOK, LOOP_HOOK, goal_id, instrument_source
from .method_binding import descriptor_function
from .source_intrinsics import BUILTIN_NAMES

__all__ = [
    "Classification",
    "attribute_safety_failure",
    "classify_pool",
    "load_instrumented",
    "witness_hits",
]


def _failure_kind(error):
    """The declared kind of one native Python source failure."""

    from .declared import ASSERTIONERROR, ATTRIBUTEERROR, DIVZERO, INDEXERROR, NEGATIVESHIFT, OVERFLOWERROR, VALUEERROR

    kind = type(error)
    if kind is AssertionError:
        return ASSERTIONERROR
    if kind is AttributeError:
        return ATTRIBUTEERROR
    if kind is ZeroDivisionError:
        return DIVZERO
    if kind in (IndexError, KeyError):
        return INDEXERROR
    if kind is ValueError:
        return NEGATIVESHIFT if str(error) == "negative shift count" else VALUEERROR
    if kind is OverflowError:
        return OVERFLOWERROR
    return None


def _failing_instruction(error):
    """Return the instruction at the exception origin, never a caller frame."""

    traceback = error.__traceback__
    if traceback is None:
        return None, None
    while traceback.tb_next is not None:
        traceback = traceback.tb_next
    instructions = tuple(dis.get_instructions(traceback.tb_frame.f_code))
    exact = next(
        (instruction for instruction in instructions
         if instruction.offset == traceback.tb_lasti),
        None,
    )
    if exact is None:
        # Some interpreter versions report an inline-cache offset.  The owning
        # instruction is the last real instruction before that cache entry.
        exact = next(
            (instruction for instruction in reversed(instructions)
             if instruction.offset <= traceback.tb_lasti),
            None,
        )
    return traceback.tb_frame.f_code.co_filename, exact


def _same_source_file(left, right):
    left = str(left).replace("\\", "/")
    right = str(right).replace("\\", "/")
    return left == right or left.rsplit("/", 1)[-1] == right.rsplit("/", 1)[-1]


def attribute_safety_failure(error, sites):
    """Map a native source failure to its declared public goal.

    The traceback's innermost instruction is the operation that actually failed.
    Requiring its source span to equal the designated AST occurrence prevents
    an exception raised while evaluating an operand from being blamed on the enclosing
    division, subscript, or ``pop``.  No match is an ordinary runtime failure, not a
    guessed safety hit. Assertion raises have the test's span in CPython;
    their public goal remains attached to the complete assertion statement.
    """

    from .declared import SafetySite

    kind = _failure_kind(error)
    if kind is None:
        return None
    filename, instruction = _failing_instruction(error)
    if instruction is None or instruction.positions is None:
        return None
    position = instruction.positions
    span = (
        position.lineno,
        position.end_lineno,
        position.col_offset,
        position.end_col_offset,
    )
    candidates = []
    values = sites.values() if type(sites) is dict else tuple(sites)
    for site in values:
        if type(site) is not SafetySite or site.kind != kind \
                or not _same_source_file(filename, site.file):
            continue
        node = site.node
        if node is None:
            continue
        if isinstance(node, ast.Assert):
            # CPython's assert-generated raise is located at its test, not at
            # the whole statement. A failure inside the test/message instead
            # originates at that operation (or in its innermost helper).
            if instruction.opname != "RAISE_VARARGS":
                continue
            node = node.test
        node_span = (
            node.lineno,
            getattr(node, "end_lineno", None),
            node.col_offset,
            getattr(node, "end_col_offset", None),
        )
        if span == node_span:
            candidates.append(site.id)
    return candidates[0] if len(candidates) == 1 else None


def load_instrumented(behavior_path):
    """Compile an instrumented copy of a behavior model at a path.

    Returns ``(namespace, sites, hits)``: the module namespace the model class
    is taken from, the enumerated branch sites, and the list the hook appends
    goal identifiers to as the model runs.
    """
    path = str(behavior_path)
    return load_source(open(path).read(), _short(path), path=path)


def source_failure_detail(error, file, path=None):
    """Actionable source-only location and text for an isolated startup fault."""
    source_frame = error.__traceback__
    line = None
    while source_frame is not None:
        if source_frame.tb_frame.f_code.co_filename == (path or file):
            line = source_frame.tb_lineno
        source_frame = source_frame.tb_next
    return dict(error=type(error).__name__, message=str(error), file=file, line=line)


def load_source(source, file="behavior.py", path=None, *,
                _attribute_startup_failure=False, goal_plan=None,
                operation_observer=None):
    """The same, from the source itself.

    A caller that already has the source should not have to write it to a file
    to run it. The instance does: it holds the source it lowered, and running
    that same text is what makes the comparison a comparison rather than a
    check that two files happen to agree.

    The isolated replay child opts into typed source-load faults. Native oracle
    callers keep the original exception class; instrumentation and compilation
    errors remain outside the source-execution attribution boundary.

    ``operation_observer`` is an internal prepared-plan consumer, not a new
    public goal policy or an arbitrary candidate callback. It sees completed
    operations' actual values, does not replace their results, and must not
    mutate artifact state. Bound operation predicates in ``goal_plan`` emit
    ordinary named hits without requiring this diagnostic observer.
    """
    path = path or file
    tree, sites = instrument_source(source, file=file, plan=goal_plan)
    read_hook = None
    bound_reads = {}
    for binding in (() if goal_plan is None else goal_plan.read_goals):
        bound_reads.setdefault(binding.site, []).append(binding)
    if bound_reads:
        from .source_reads import instrument_reads

        read_sites = tuple(sorted(bound_reads))
        tree, read_hook = instrument_reads(tree, read_sites)
    operation_hook = None
    bound_operations = {}
    for binding in (() if goal_plan is None else goal_plan.operation_goals):
        bound_operations.setdefault(binding.site, []).append(binding)
    if operation_observer is not None or bound_operations:
        from .source_operations import instrument_operations

        if goal_plan is None:
            raise ValueError("operation observation requires the prepared source plan")
        operation_sites = (goal_plan.operations if operation_observer is not None else
                           tuple(sorted(bound_operations)))
        tree, operation_hook = instrument_operations(tree, operation_sites)
    hits: list = []
    # Immutable identifiers belong to the source plan, not to each dynamic
    # visit. Keep the ordered visit log and evaluate truth at the original hook.
    identifiers = tuple({outcome: goal_id(site, outcome) for outcome in site.outcomes}
                        for site in sites)

    def hook(ordinal, value):
        # The outcome is the truth of the value; the value is what the source
        # computes with. Returning the truth instead makes ``5 or 7`` evaluate
        # to True under instrumentation, so a run of the model measured here
        # is a run of a different model.
        hits.append(identifiers[ordinal][bool(value)])
        return value

    def loop_hook(ordinal, iterable):
        """Report whether the loop ran at all.

        The entered outcome is reported on the first item, so a loop left by
        ``break`` still reports it, and the empty outcome is reported when the
        iterable ends without having produced one.
        """
        outcomes = identifiers[ordinal]
        n = 0
        for item in iterable:
            if n == 0:
                hits.append(outcomes[True])
            n += 1
            yield item
        if n == 0:
            hits.append(outcomes[False])

    # A temporary rebinding would be captured by functions defined during it.
    # Reject source bindings before executing the module.
    from .class_binding import DATACLASS_HOOK, observe_dataclasses

    for _node in ast.walk(tree):
        if isinstance(_node, ast.Name) and _node.id == DATACLASS_HOOK:
            raise ValueError("the source uses a reserved class-observer binding")
        if isinstance(_node, ast.Name) and isinstance(_node.ctx, ast.Store) \
                and _node.id == "__builtins__":
            raise ValueError("the model binds __builtins__, which it may not")
        if isinstance(_node, (ast.Global, ast.Nonlocal)) \
                and "__builtins__" in _node.names:
            raise ValueError("the model declares __builtins__ global")

    baseline = _safe_builtins()
    import sys
    from types import ModuleType

    module = ModuleType("behavior")
    ns = module.__dict__
    ns.update({HOOK: hook, LOOP_HOOK: loop_hook,
               "__file__": path, "__builtins__": baseline})
    ns[DATACLASS_HOOK], dataclass_tables = observe_dataclasses(tree)
    # A receipt of definition-time library output, not a candidate-supplied
    # __dataclass_fields__ flag. The isolated owner validates these bindings.
    ns[DATACLASS_HOOK].tables = dataclass_tables
    if read_hook is not None:
        def observe_read(index, value):
            for binding in bound_reads[read_sites[index]]:
                if binding.accepts(value) and binding.predicate.observe((value,)):
                    hits.append(binding.id)
            return value

        ns[read_hook] = observe_read
    if operation_hook is not None:
        def observe_operation(index, operands, result):
            site = operation_sites[index]
            payload = (*operands, result)
            for binding in bound_operations.get(site, ()):
                if not binding.accepts(payload):
                    continue
                observed = binding.predicate.observe(payload)
                if observed is None:
                    from ..core.refusal import Refusal, Refused

                    raise Refused(Refusal("behavior-operation-goal-unreadable",
                                          "a bound operation producer did not supply a readable scalar",
                                          goal=binding.id))
                if observed:
                    hits.append(binding.id)
            if operation_observer is not None:
                operation_observer(site, operands, result)
            return result

        ns[operation_hook] = observe_operation
    _allow_approved_imports()
    # Only the submitted source may opt into future semantics. Inheriting this
    # module's annotations flag silently suppresses eager annotation evaluation.
    # Assertions are artifact semantics, not the launcher's optimization flag.
    code = compile(tree, path, "exec", dont_inherit=True, optimize=0)
    previous_module = sys.modules.get(module.__name__)
    sys.modules[module.__name__] = module
    try:
        exec(code, ns)  # noqa: S102 - executes isolated model source
    except BaseException as error:  # only the source execution boundary
        if not _attribute_startup_failure:
            raise
        from ..core.failure import CandidateFault
        from ..core.refusal import Refusal

        raise CandidateFault(Refusal(
            "candidate-load-failed", "the candidate module raised during loading",
            **source_failure_detail(error, file, path))) from None
    finally:
        if previous_module is None:
            sys.modules.pop(module.__name__, None)
        else:
            sys.modules[module.__name__] = previous_module
    # Module and function bindings must still refer to the read-only baseline
    # installed before source execution, not a table supplied by the source.
    if ns.get("__builtins__") is not baseline:
        raise ValueError(
            "the model rebound the builtins table at load, so the "
            "environment the certificate is about is not the one it ran in")
    import types as _types

    def _model_fn(value):
        # A function the model itself defined resolves globals in ns; the
        # injected hooks are defined in this module and are not checked.
        return isinstance(value, _types.FunctionType) \
            and getattr(value, "__globals__", None) is ns

    for _value in list(ns.values()):
        if _model_fn(_value) and _value.__builtins__ is not baseline:
            raise ValueError(
                "a model function carries a builtins table other than the "
                "one it ran under")
        if isinstance(_value, type):
            for _member in vars(_value).values():
                _member = descriptor_function(_member)
                if _model_fn(_member) \
                        and _member.__builtins__ is not baseline:
                    raise ValueError(
                        "a model method carries a builtins table other than "
                        "the one it ran under")
    return ns, sites, hits


# Concrete replay provides the resolved source vocabulary plus class/literal
# support. Host I/O, evaluation and imports are not inherited from Python.
_ALLOWED_BUILTINS = BUILTIN_NAMES | {
    "True", "False", "None", "NotImplemented", "__build_class__", "__name__",
}


def _safe_builtins():
    """The builtins an admitted model may see, and a guarded import.

    The import has to work, since a model may import the approved library, and
    `__import__` is the plainest way out of any of this. So it is wrapped
    rather than removed: the same approved list admission checks the import
    statement against is checked again here, because the two are different
    stages and only one of them runs the code.
    """
    import builtins

    from .source_intrinsics import CONCRETE_MODULES
    from .source_syntax import FUTURE_FEATURES

    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        annotation_directive = (name == "__future__" and level == 0 and fromlist
                                and all(item in FUTURE_FEATURES for item in fromlist))
        if root not in CONCRETE_MODULES and not annotation_directive:
            raise ImportError(f"{name} is not an approved library")
        return real_import(name, globals, locals, fromlist, level)

    import types as _types

    out = {name: getattr(builtins, name) for name in _ALLOWED_BUILTINS}
    out["__import__"] = guarded
    # A proxy exposes no mutator, including inherited dict operations.
    return _types.MappingProxyType(out)


def _allow_approved_imports():
    """Put the approved library where a model's import of it will find it.

    A model may import the helpers distributed with the verifier, so loading
    one has to make them importable. Leaving that to each caller means a model
    that lowers and a model that runs are different populations.
    """
    import sys

    from . import LIBRARY_DIR

    directory = str(LIBRARY_DIR)
    if directory not in sys.path:
        sys.path.insert(0, directory)


def _short(path):
    """File component of a goal identifier: the name alone, so that the same
    model classifies identically wherever the problem directory is copied."""
    return path.rsplit("/", 1)[-1]


def _model_class(ns, name=None):
    """The model class of an instrumented namespace.

    ``name`` is what the caller already decided, and a caller on the certification path
    has decided: `extract.source_model` reads which class the lowering is about, and
    this is the class the audit runs. Passing the name is what keeps those the same
    object. Without it the two decide separately, by different rules -- the lowering
    takes the class that defines the step and refuses a second candidate, while this
    takes the first class in the namespace that has `reset` and something beginning with
    `process` -- and a disagreement would have the audit comparing the lowered program
    against a different class, which reads as a fidelity failure rather than as the
    mix-up it is.

    Without a name it guesses as before, for the callers that have nothing to pass.
    """
    if name is not None:
        found = ns.get(name)
        if not isinstance(found, type):
            raise ValueError(f"{name} is not a class in the model's namespace")
        return found
    for value in ns.values():
        if not isinstance(value, type):
            continue
        names = dir(value)
        if "reset" in names and any(
                n == "process" or n.startswith("process_") for n in names):
            return value
    raise ValueError("no model class with reset() and process() found")


def witness_hits(behavior_path, rows, ns=None, sites=None, hits=None):
    """Goals hit by driving one witness through the instrumented model.

    ``rows`` is the witness's transaction list in ``process()`` argument order.
    The model is reset first, so the hits are those of a run from the initial
    state, which is the run the certificate's witness names.
    """
    if ns is None:
        ns, _sites, hits = load_instrumented(behavior_path)
    model_cls = _model_class(ns)
    model = model_cls()
    model.reset()
    start = len(hits)
    for row in rows:
        model.process(*row)
    return set(hits[start:])


class Classification:
    """Goals split into covered, unreachable and undecided.

    ``unreachable`` is populated only by a query that ranges over every path,
    so a pool alone leaves it empty and everything not covered is undecided.
    """

    __slots__ = ("covered", "goals", "unreachable", "witness_of")

    def __init__(self, goals, covered=None, unreachable=None, witness_of=None):
        self.goals = list(goals)
        self.covered = set(covered or ())
        self.unreachable = set(unreachable or ())
        self.witness_of = dict(witness_of or {})

    @property
    def undecided(self):
        return [g for g in self.goals
                if g not in self.covered and g not in self.unreachable]

    @property
    def residual(self):
        """The goals the per-goal queries still have to decide."""
        return self.undecided

    def summary(self):
        return {
            "n_goals": len(self.goals),
            "n_covered": len(self.covered),
            "n_unreachable": len(self.unreachable),
            "n_undecided": len(self.undecided),
            "pct_covered": (round(100.0 * len(self.covered) / len(self.goals), 2)
                            if self.goals else 0.0),
        }


def classify_pool(behavior_path, witnesses, goals=None):
    """Classify ``goals`` against a witness pool.

    Each witness is replayed on the instrumented model, so a covered goal
    carries the witness that reached it, which is what makes the count
    recheckable rather than asserted.
    """
    ns, sites, hits = load_instrumented(behavior_path)
    if goals is None:
        goals = [g for s in sites for g in s.goals]
    covered, witness_of = set(), {}
    for idx, rows in enumerate(witnesses):
        got = witness_hits(behavior_path, rows, ns, sites, hits)
        for g in got - covered:
            witness_of[g] = idx
        covered |= got
    return Classification(goals, covered=covered & set(goals),
                          witness_of=witness_of)
