"""Checked finite-run preparation and independent closure proofs.

The solver-free planner proposes carriers. This layer owns encoding those
transitions, discharging obligations, and sealing a plan for reuse by queries.
Syntactic reductions are sufficient proofs only; their failure retains the
original exact obligation. Budget exhaustion never establishes closure.
"""

import math

from behave.core import runtime_defaults as defaults

from . import ir
from .term_bounds import (
    _CONTROL_PROPOSITION_BUDGET as _CONTROL_PROPOSITION_BUDGET,
    _unsigned_hull as _unsigned_hull,
    _unsigned_quotient_hull as _unsigned_quotient_hull,
    _compare_hulls as _compare_hulls,
    _StructuralProofLimit as _StructuralProofLimit,
    _StructuralProofWork as _StructuralProofWork,
    _SyntacticClosureProof as _SyntacticClosureProof,
    _signed_bounds as _signed_bounds,
    _no_wrap_hull as _no_wrap_hull,
    _PropositionalProof as _PropositionalProof,
    _syntactic_signed_hull as _syntactic_signed_hull,
)
from . import ranges
SOLVER_MEMORY_MB = defaults.SIM_BEHAVIOR_SOLVER_MEMORY_MB
from .plan import (
    EncodingPlan,
    PlanMismatch,
    Unresolved,
    _FrozenMap,
    _NO_BOUND,
    _UniqueUnresolved,
    _add_unresolved,
    _carrier_layer_image,
    _checked_bound,
    _checked_unresolved,
    _derive_position_layers,
    _diagnostic_image,
    _digest,
    _environment_fingerprint,
    _environment_image,
    _input_environment,
    _intern_carrier_layers,
    _intern_occurrence_layers,
    _layer_fingerprints,
    _occurrence_layer_image,
    _position_procedure_fingerprint,
)

_POSITION_PLAN_VERSION = "position-encoding-plan/21"
_PREPARED_POSITION_VERSION = "prepared-position-plan/2"
_POSITION_CLOSURE_TIMEOUT_MS = defaults.SIM_BEHAVIOR_POSITION_CLOSURE_MS
_POSITION_PATH_PROOF_LEAVES = 64
# Optional structural proofs share their work budget across all components.
_POSITION_STRUCTURAL_PROOF_WORK = defaults.SIM_BEHAVIOR_POSITION_PROOF_NODES
_PREPARE_TOKEN = object()


class PositionEncodingPlan:
    """Finite carriers for every boundary of one fixed Behavior run.

    ``state_layers[t]`` is the component carrier for a normal state before
    step ``t`` and ``normal_layers[t]`` says whether the abstract transfer still
    has a normal successor.  It is a may-reachability flag, not an exact
    inhabitation claim.  After every abstract path has failed, the terminal
    error is represented by the absorbed run while the component carrier
    supplies only an irrelevant padding value to the implementation.

    ``input_layers[t]`` and ``output_layers[t]`` are that step's public input
    and output carriers.  ``steps[t]`` is the existing one-step plan derived in
    the corresponding state/input environment, while
    ``occurrence_layers[t]`` records the finite carrier of each expression
    occurrence the symbolic encoder visits there.  Every one of these scalar
    carriers uses ``scalar_width``.  The semantic sets remain position-specific,
    but a value has one encoding everywhere in the procedure.

    Completeness is stronger than a collection of bounded intervals.  The
    builder also asks the exact symbolic step whether any normally completing
    transition can leave the next state or output layer.  SAT, UNKNOWN, an
    absent boundary value is retained as ``unresolved`` and prevents this plan
    from being consumed.
    """

    __slots__ = (
        "bound",
        "fingerprint",
        "input_environment",
        "input_environment_fingerprint",
        "input_layers",
        "normal_layers",
        "occurrence_layers",
        "output_layers",
        "procedure_fingerprint",
        "scalar_width",
        "state_layers",
        "steps",
        "unresolved",
    )

    def __init__(self, state_layers, input_layers, output_layers,
                 occurrence_layers, steps, normal_layers, scalar_width,
                 bound, unresolved, input_environment, procedure_fingerprint):
        bound = _checked_bound(bound)
        if bound is None:
            raise ValueError("a position-indexed plan has an exact finite bound")
        if type(scalar_width) is not int or scalar_width < 1:
            raise ValueError("a position plan has one positive scalar width")
        state_layers = _intern_carrier_layers(state_layers, scalar_width)
        input_layers = _intern_carrier_layers(input_layers, scalar_width)
        output_layers = _intern_carrier_layers(output_layers, scalar_width)
        occurrence_layers = _intern_occurrence_layers(
            occurrence_layers, scalar_width)
        steps = tuple(steps)
        normal_layers = tuple(normal_layers)
        if len(state_layers) != bound + 1:
            raise ValueError("a position plan has N+1 state layers")
        if any(len(parts) != bound for parts in (
                input_layers, output_layers, occurrence_layers, steps)):
            raise ValueError("a position plan has one input/output/step layer per slot")
        if (len(normal_layers) != bound + 1
                or any(type(item) is not bool for item in normal_layers)
                or not normal_layers[0]):
            raise ValueError(
                "a position plan has N+1 Boolean normal-layer flags and starts normal")
        if any(type(step) is not EncodingPlan for step in steps):
            raise TypeError("each position step is an exact EncodingPlan")
        unresolved = tuple(_checked_unresolved(item) for item in unresolved)
        input_environment = _FrozenMap(input_environment)
        input_fingerprint = _environment_fingerprint(input_environment)
        image = (
            _POSITION_PLAN_VERSION,
            str(procedure_fingerprint),
            input_fingerprint,
            bound,
            normal_layers,
            scalar_width,
            tuple(_carrier_layer_image(layer) for layer in state_layers),
            tuple(_carrier_layer_image(layer) for layer in input_layers),
            tuple(_carrier_layer_image(layer) for layer in output_layers),
            _layer_fingerprints(occurrence_layers, _occurrence_layer_image),
            tuple(step.fingerprint for step in steps),
            tuple((item.kind, _diagnostic_image(item.what), item.detail)
                  for item in unresolved),
        )
        for name, value in (
                ("state_layers", state_layers),
                ("input_layers", input_layers),
                ("output_layers", output_layers),
                ("occurrence_layers", occurrence_layers),
                ("steps", steps),
                ("normal_layers", normal_layers),
                ("scalar_width", scalar_width),
                ("bound", bound),
                ("unresolved", unresolved),
                ("procedure_fingerprint", str(procedure_fingerprint)),
                ("input_environment", input_environment),
                ("input_environment_fingerprint", input_fingerprint),
                ("fingerprint", _digest(image))):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError("a PositionEncodingPlan is immutable")

    def __reduce__(self):
        return type(self), (
            self.state_layers,
            self.input_layers,
            self.output_layers,
            self.occurrence_layers,
            self.steps,
            self.normal_layers,
            self.scalar_width,
            self.bound,
            self.unresolved,
            self.input_environment,
            self.procedure_fingerprint,
        )

    @property
    def complete(self):
        return (not self.unresolved
                and all(step.representation_complete for step in self.steps))

    @property
    def identity(self):
        return f"{_POSITION_PLAN_VERSION}:{self.fingerprint}"

    def environment_at(self, position):
        """The exact range environment read by step ``position``."""
        if type(position) is not int or not 0 <= position < self.bound:
            raise IndexError("a step position outside the plan")
        # The public layer carrier is one hull for every leaf of a recursive
        # value.  Planning may retain a tighter private range per array cell;
        # that precision determines expression carriers and must survive the
        # PositionEncodingPlan -> EncodingPlan -> encoder boundary unchanged.
        return dict(self.steps[position].env)

    def validate(self, proc, env, bound=_NO_BOUND):
        """Re-derive all layers and exact closure checks before use."""
        ir.validate_procedure_representation(proc)
        got_proc = _position_procedure_fingerprint(proc)
        if got_proc != self.procedure_fingerprint:
            raise PlanMismatch("the position plan belongs to another procedure")
        wanted = self.bound if bound is _NO_BOUND else _checked_bound(bound)
        if wanted != self.bound:
            raise PlanMismatch(
                f"the position plan covers {self.bound} steps, not {wanted}")
        inputs = _input_environment(proc, env)
        if _environment_fingerprint(inputs) != self.input_environment_fingerprint:
            raise PlanMismatch("the position plan belongs to another input support")
        expected = plan_positions(proc, inputs, self.bound)
        if expected.fingerprint != self.fingerprint:
            raise PlanMismatch(
                "the position plan's layers, closure evidence, or diagnostics "
                "do not match the procedure and input support")
        return self

    def replaced(self, *, state_layers=None, input_layers=None,
                 output_layers=None, occurrence_layers=None, steps=None,
                 normal_layers=None, scalar_width=None, bound=None,
                 unresolved=None):
        """Return a distinct value, principally for adversarial tests."""
        return type(self)(
            self.state_layers if state_layers is None else state_layers,
            self.input_layers if input_layers is None else input_layers,
            self.output_layers if output_layers is None else output_layers,
            self.occurrence_layers if occurrence_layers is None
            else occurrence_layers,
            self.steps if steps is None else steps,
            self.normal_layers if normal_layers is None else normal_layers,
            self.scalar_width if scalar_width is None else scalar_width,
            self.bound if bound is None else bound,
            self.unresolved if unresolved is None else unresolved,
            self.input_environment,
            self.procedure_fingerprint,
        )

    def __eq__(self, other):
        return type(other) is type(self) and other.fingerprint == self.fingerprint

    def __hash__(self):
        return hash(self.fingerprint)

    def __repr__(self):
        return (f"PositionEncodingPlan({self.bound} steps, "
                f"{len(self.unresolved)} unresolved, "
                f"id={self.fingerprint[:12]})")


class _PreparedPositionPlan:
    """One complete position plan sealed at the verifier-owned bind boundary.

    Public raw plans remain adversarial inputs and are fully re-derived before
    use.  This private wrapper records that :func:`prepare_positions` just did
    that work, so query workers need only check the immutable plan is still
    attached to the same procedure, input support, and horizon.  In particular,
    they do not repeat every closure SMT query after each pickle boundary.
    """

    __slots__ = ("_seal", "plan")

    def __init__(self, plan, token):
        if token is not _PREPARE_TOKEN:
            raise TypeError(
                "a prepared position plan is created only by prepare_positions")
        if type(plan) is not PositionEncodingPlan:
            raise TypeError("a prepared position plan wraps one exact raw plan")
        if not plan.complete:
            raise ValueError("an incomplete position plan cannot be prepared")
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "_seal", _prepared_seal(plan))

    def __setattr__(self, name, value):
        raise AttributeError("a prepared position plan is immutable")

    def __reduce__(self):
        return _restore_prepared_positions, (self.plan, self._seal)

    @property
    def identity(self):
        return f"{_PREPARED_POSITION_VERSION}:{self._seal}"

    @property
    def fingerprint(self):
        return self.plan.fingerprint

    @property
    def bound(self):
        return self.plan.bound

    def checked_for(self, proc, env, bound, *, _procedure_validation=None):
        """Return the raw plan after solver-free boundary checks."""
        if _procedure_validation is None:
            ir.validate_procedure_representation(proc)
        else:
            from .validation import _checked_procedure_validation

            _checked_procedure_validation(_procedure_validation, proc)
        if _prepared_seal(self.plan) != self._seal:
            raise PlanMismatch("the prepared position-plan seal is invalid")
        if not self.plan.complete:
            raise PlanMismatch("the prepared position plan is no longer complete")
        if (_position_procedure_fingerprint(proc)
                != self.plan.procedure_fingerprint):
            raise PlanMismatch(
                "the prepared position plan belongs to another procedure")
        wanted = _checked_bound(bound)
        if wanted != self.plan.bound:
            raise PlanMismatch(
                f"the prepared position plan covers {self.plan.bound} steps, "
                f"not {wanted}")
        inputs = _input_environment(proc, env)
        if (_environment_fingerprint(inputs)
                != self.plan.input_environment_fingerprint):
            raise PlanMismatch(
                "the prepared position plan belongs to another input support")
        return self.plan

    def first_step(self, proc, env, bound):
        """Derive a checked prefix, not a proof about the remaining steps.

        The first transition's carriers and closure evidence are unchanged.
        Keep even its original scalar width: recomputing a narrower layout is
        not part of this structural prefix operation. Goal-query callers need
        a separate temporal equivalence before using this prefix for N steps.
        """
        original = self.checked_for(proc, env, bound)
        prefix = PositionEncodingPlan(
            original.state_layers[:2], original.input_layers[:1],
            original.output_layers[:1], original.occurrence_layers[:1],
            (original.steps[0].replaced(bound=1),),
            original.normal_layers[:2], original.scalar_width, 1, (),
            original.input_environment, original.procedure_fingerprint)
        return _PreparedPositionPlan(prefix, _PREPARE_TOKEN)


def _prepared_seal(plan):
    return _digest((_PREPARED_POSITION_VERSION, plan.identity))


def _restore_prepared_positions(plan, seal):
    if type(plan) is not PositionEncodingPlan or type(seal) is not str:
        raise TypeError("an invalid prepared position-plan image")
    if _prepared_seal(plan) != seal:
        raise PlanMismatch("the pickled prepared position-plan seal is invalid")
    return _PreparedPositionPlan(plan, _PREPARE_TOKEN)


def prepare_positions(proc, env, bound, *, _closure_deadline=None,
                      _closure_timeout_ms=None):
    """Build and seal the one complete position plan used by production."""
    plan = plan_positions(
        proc, env, bound, _closure_deadline=_closure_deadline,
        _closure_timeout_ms=_closure_timeout_ms,
        _fail_on_incomplete=True)
    _require_complete_positions(plan)
    return _PreparedPositionPlan(plan, _PREPARE_TOKEN)


def _require_complete_positions(plan):
    if plan.complete:
        return
    from .validation import EncodingError

    first = plan.unresolved[0] if plan.unresolved else None
    raise EncodingError(
        "the position-indexed representation is incomplete"
        + (f" at {first.what}: {first.detail}" if first else ""),
        kind=first.kind if first else "capability")


def _checked_position_plan(plan, proc, env, bound):
    """Resolve a raw or prepared position plan for one direct unrolling."""
    if type(plan) is _PreparedPositionPlan:
        return plan.checked_for(proc, env, bound)
    if type(plan) is PositionEncodingPlan:
        plan.validate(proc, env, bound=bound)
        _require_complete_positions(plan)
        return plan
    raise TypeError("plan is not a PositionEncodingPlan or prepared position plan")


def _prepare_supplied_positions(plan, proc, env, bound):
    """Validate one public raw plan once before distributing it to workers."""
    if type(plan) is _PreparedPositionPlan:
        plan.checked_for(proc, env, bound)
        return plan
    checked = _checked_position_plan(plan, proc, env, bound)
    # This constructor call is deliberately colocated with the full raw-plan
    # validation above.  There is no helper that can bless an arbitrary raw
    # value without first re-deriving it.
    return _PreparedPositionPlan(checked, _PREPARE_TOKEN)


def _flatten_symbolic(value, path=()):
    from .functional_array import FunctionalArray

    if isinstance(value, FunctionalArray):
        # A compact scalar array has no component list to walk.  Its guarded
        # value sources are checked directly by ``_carrier_leaf_memberships``.
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flatten_symbolic(item, path + (index,))
    else:
        yield path, value


def _carrier_leaf_memberships(
        value, carrier, definitions=None, *, assumption=None, proof=None,
        _coarse_only=False):
    """Return one exact membership predicate per scalar representation leaf."""

    import z3

    definitions = {} if definitions is None else definitions
    if proof is None:
        proof = _SyntacticClosureProof(definitions, assumption=assumption)
    elif type(proof) is not _SyntacticClosureProof:
        raise TypeError("a carrier proof is a _SyntacticClosureProof")

    def scalar_membership(term):
        context = term.ctx if z3.is_expr(term) else None
        if not z3.is_bv(term):
            return z3.BoolVal(False, ctx=context)
        # Most boundary obligations need no path reasoning: joining both arms
        # of every conditional already stays inside the declared carrier.  Try
        # that linear, branch-insensitive proof first.  Only a genuinely narrow
        # carrier asks the precise proof to traverse Boolean control.
        try:
            hull = proof.coarse_signed_hull(term)
            if hull is not None and carrier.lo <= hull.lo and hull.hi <= carrier.hi:
                return z3.BoolVal(True, ctx=context)
            if not _coarse_only:
                hull = proof.signed_hull(term)
                if (hull is not None
                        and carrier.lo <= hull.lo and hull.hi <= carrier.hi):
                    return z3.BoolVal(True, ctx=context)
                if proof.proves_carrier(
                        term, carrier, max_leaves=_POSITION_PATH_PROOF_LEAVES):
                    return z3.BoolVal(True, ctx=context)
        except _StructuralProofLimit:
            # Exhaustion means no structural proof, not membership. Emit the
            # original predicate below for the independent exact checker.
            pass
        # A bit-vector already ranges over its complete signed carrier.  Avoid
        # asking the solver to rediscover this tautology through a potentially huge
        # definitional circuit: no transition equation can produce a value outside
        # the type's representable interval.
        signed_lo = -(1 << (term.size() - 1))
        signed_hi = (1 << (term.size() - 1)) - 1
        if carrier.lo <= signed_lo and signed_hi <= carrier.hi:
            return z3.BoolVal(True, ctx=context)
        # Compare in a width that carries both the symbolic value and both
        # finite bounds.  Constructing a bound at ``term.size()`` would
        # silently truncate it when an adversarial or future encoder returns a
        # narrower term; that would turn the closure query into a check of the
        # truncated interval rather than the declared carrier.
        width = max(term.size(), carrier.width)
        widened = (z3.SignExt(width - term.size(), term)
                   if term.size() < width else term)
        return z3.And(
            widened >= z3.BitVecVal(carrier.lo, width, ctx=context),
            widened <= z3.BitVecVal(carrier.hi, width, ctx=context),
        )

    from .functional_array import FunctionalArray

    checks = []
    if isinstance(value, FunctionalArray):
        for index, (guard, term, intrinsic) in enumerate(value.range_cases()):
            if intrinsic is not None:
                low, high = intrinsic
                member = z3.BoolVal(
                    carrier.lo <= low and high <= carrier.hi, ctx=guard.ctx)
            else:
                member = scalar_membership(term)
            if not z3.is_true(guard) and not z3.is_true(member):
                member = z3.Or(z3.Not(guard), member)
            checks.append((("source", index), member))
        if _coarse_only or all(z3.is_true(member) for _path, member in checks):
            return tuple(checks)
        # Source ranges are a sufficient proof, not an exact counterexample:
        # an old value may have been overwritten, or its store index may miss
        # the array. The authoritative fallback checks the actual final cells.
        width = max(1, value.length.bit_length() + 1)
        context = next(iter(value.formula_terms), z3.BoolVal(True)).ctx
        return tuple(
            ((index,), scalar_membership(value.select(
                z3.BitVecVal(index, width, ctx=context))))
            for index in range(value.length)
        )

    for path, term in _flatten_symbolic(value):
        checks.append((path, scalar_membership(term)))
    # Shape validation precedes encoding.  An empty symbolic aggregate is
    # therefore the exact encoding of a declared zero-cell product, whose
    # componentwise membership condition is vacuously true.
    return tuple(checks)


def _carrier_membership(value, carrier, definitions=None):
    import z3

    checks = [check for _path, check in
              _carrier_leaf_memberships(value, carrier, definitions)]
    # Preserve syntactic truth for a complete bit-vector carrier.  Closure
    # planning uses this to avoid constructing a solver query whose target is
    # already false; ``And(True)`` is logically equal but does not satisfy
    # ``z3.is_true`` until another simplification pass.
    return z3.simplify(z3.And(checks)) if checks else z3.BoolVal(True)


def _check_position_closure(proc, position, env, step, occurrence_carriers,
                            scalar_width, next_state, outputs, unresolved, *,
                            certified_outputs=None, deadline=None,
                            _procedure_validation=None):
    import time

    import z3

    if deadline is None:
        deadline = time.monotonic() + _POSITION_CLOSURE_TIMEOUT_MS / 1000.0
    work = _StructuralProofWork(_POSITION_STRUCTURAL_PROOF_WORK, deadline)

    def checkpoint():
        if time.monotonic() >= deadline:
            raise TimeoutError("the shared exact layer-closure deadline expired during construction")

    from ..symbolic.exact_solve import (
        ExactSliceValidationDisagreement,
        solve_exact,
        z3_check_qfbv,
    )
    from ..core.query import SolverAnswer
    from .encoder import COUNT, _encode_boundary_step, _encode_derived_step
    from .validation import EncodingError

    def certified(carrier, hull):
        return (hull is not None
                and carrier.lo <= hull.lo and hull.hi <= carrier.hi)

    # A sealed frontend output proof is an independent closure proof for that
    # component.  Do not rebuild the complete symbolic datapath merely to
    # rediscover it.  State and any genuinely narrower output remain exact
    # symbolic obligations below.
    closure_outputs = {
        name: carrier for name, carrier in outputs.items()
        if not (name == COUNT and proc.count is None)
        and not certified(carrier, (certified_outputs or {}).get(name))
    }

    # The sealed source CFG can independently prove a small but common exact
    # state relation: every normal exit carries the same entry scalar or the
    # same literal.  This is not an abstract range claim.  It is an exact
    # path-wise certificate, so a containing next-state carrier needs no SMT
    # reconstruction of unrelated combinational work.
    from .state_boundary import normal_state_sources

    state_sources = normal_state_sources(proc)

    def source_is_contained(name):
        carrier = next_state.get(name)
        source = state_sources.get(name)
        if carrier is None or source is None:
            return False
        if source.kind == "literal":
            value = int(source.value)
            return carrier.lo <= value <= carrier.hi
        value = env.get(source.value)
        return (isinstance(value, ranges.Range)
                and carrier.lo <= value.lo and value.hi <= carrier.hi)

    closure_state = tuple(
        name for name in proc.state if not source_is_contained(name))
    if not closure_state and not closure_outputs:
        return

    def value_for_output(encoding, name, boundary_cone):
        if not boundary_cone:
            # The exact fallback needs actual boundary cells, not the broader
            # source-value union of a compact array's update history.
            return encoding.post.get(name)
        variable = proc.count if name == COUNT else proc.output_vars.get(name)
        if variable is not None:
            # Boundary cones omit public outputs from ``post``, which may
            # still hold a same-named state field. Read the output's declared
            # physical binding, never that unrelated state value.
            return encoding.final.get(variable)
        return encoding.post.get(name)

    def membership_checks(
            encoding, wanted_state, wanted_outputs, *, boundary_cone=False,
            coarse_only=False):
        definitions = {
            item.lhs.get_id(): item.rhs for item in encoding.definitions
        }
        proof = _SyntacticClosureProof(
            definitions,
            assumption=None if boundary_cone else encoding.ok,
            work=work, array_selections=encoding.array_selections)
        for name, value in encoding.pre.items():
            hull = env.get(name)
            if not isinstance(hull, ranges.Range):
                continue
            for _path, term in _flatten_symbolic(value):
                proof.assume_boundary_hull(term, hull)
        for name, hull in (certified_outputs or {}).items():
            value = value_for_output(encoding, name, boundary_cone)
            if value is None:
                continue
            for _path, term in _flatten_symbolic(value):
                proof.use_certified_hull(term, hull)
        checks = []
        for name in wanted_state:
            carrier = next_state.get(name)
            value = encoding.state_post.get(name)
            if carrier is None or value is None:
                return None
            members = [
                check for _path, check in _carrier_leaf_memberships(
                    value, carrier, definitions, assumption=encoding.ok,
                    proof=proof, _coarse_only=coarse_only)
            ]
            checks.append((
                ("state", name),
                z3.simplify(z3.And(members))
                if members else z3.BoolVal(True),
            ))
        for name, carrier in wanted_outputs.items():
            value = value_for_output(encoding, name, boundary_cone)
            if value is None:
                return None
            members = [
                check for _path, check in _carrier_leaf_memberships(
                    value, carrier, definitions, assumption=encoding.ok,
                    proof=proof, _coarse_only=coarse_only)
            ]
            checks.append((
                ("output", name),
                z3.simplify(z3.And(members))
                if members else z3.BoolVal(True),
            ))
        return checks

    # First try a failure-erased data cone.  It contains every computation
    # feeding the selected boundary values but admits paths on which the source
    # would already have halted.  Closure of this broader relation is sufficient
    # for the source relation.  Failure to prove it says nothing about the
    # source, so the complete encoding below remains the authoritative fallback.
    from . import slicing

    selected = set(closure_state)
    for name in closure_outputs:
        variable = proc.count if name == COUNT else proc.output_vars.get(name)
        if variable is not None:
            selected.add(variable)
    keep = slicing.for_boundary(proc, selected)
    if keep is not None:
        try:
            broad = _encode_boundary_step(
                proc, env=env, tag=f"@position-broad-{position}", plan=step,
                occurrence_carriers=occurrence_carriers,
                scalar_width=scalar_width, keep=keep,
                _validation=_procedure_validation, _checkpoint=checkpoint)
            # This cone deliberately denotes a broader transition relation.
            # Prove its boundary without reopening the complete source
            # normal-completion guard: doing so would pull every erased
            # failure path back into the supposedly small precheck.  A proof
            # with no completion premise is stronger; failure still falls
            # through to the exact source transition below.
            broad_checks = membership_checks(
                broad, closure_state, closure_outputs, boundary_cone=True,
                coarse_only=True)
            if broad_checks is not None:
                # Each proved conjunct is independently sufficient, even when
                # another component exhausts the shared structural budget.
                # Do not discard these proofs and solve their cones again.
                proved = {key for key, check in broad_checks if z3.is_true(check)}
                closure_state = tuple(
                    name for name in closure_state if ("state", name) not in proved)
                closure_outputs = {
                    name: carrier for name, carrier in closure_outputs.items()
                    if ("output", name) not in proved}
                if not closure_state and not closure_outputs:
                    return
        except TimeoutError as exc:
            _add_unresolved(unresolved, Unresolved("resource", f"closure[{position}]", str(exc)))
            return
        except (EncodingError, PlanMismatch, TypeError, ValueError):
            pass
        finally:
            # Only proved component names survive the optional precheck.
            # Keeping its SSA graph and unresolved membership terms alive
            # while building the exact graph needlessly retains two models.
            broad = broad_checks = None

    try:
        encoding = _encode_derived_step(
            proc, env=env, tag=f"@position-check-{position}", plan=step,
            occurrence_carriers=occurrence_carriers,
            scalar_width=scalar_width,
            _validation=_procedure_validation, observations=False, _checkpoint=checkpoint)
    except TimeoutError as exc:
        _add_unresolved(unresolved, Unresolved("resource", f"closure[{position}]", str(exc)))
        return
    except (EncodingError, PlanMismatch, TypeError, ValueError) as exc:
        _add_unresolved(unresolved, Unresolved(
            "capability", f"closure[{position}]",
            f"the symbolic closure check could not be built: "
            f"{type(exc).__name__}: {exc}"))
        return

    checks = membership_checks(encoding, closure_state, closure_outputs)
    if checks is None:
        _add_unresolved(unresolved, Unresolved(
            "range", f"closure[{position}]",
            "the symbolic result has no matching state or output carrier"))
        return

    if not checks:
        return
    # A conjunction is invariant exactly when every conjunct is invariant:
    #
    #   ok => (c1 and ... and cn)  iff  forall i. not (ok and not ci).
    #
    # Check one declared state/output product at a time.  Its scalar leaves
    # share the same array update cone, so one disjunctive counterexample query
    # lets the solver reuse that cone instead of rebuilding it for every cell.
    # Distinct variables remain separate to keep unrelated cones out of each
    # slice.  All products share one deadline, and SAT remains validated against
    # the complete formula by ``solve_exact``.
    formula = None
    for (_role, label), check in checks:
        target = z3.simplify(z3.And(encoding.ok, z3.Not(check)))
        if z3.is_false(target):
            continue
        if time.monotonic() >= deadline:
            _add_unresolved(unresolved, Unresolved(
                "resource", f"closure[{position}]",
                "the shared exact layer-closure deadline expired before "
                f"checking {label}"))
            return
        if formula is None:
            # Most finite boundaries are discharged by the structural hull
            # proof above.  Materializing every SSA definition into one exact
            # formula before discovering that there is no solver query is both
            # semantically redundant and dominant on large straight-line IRs.
            # Keep the formula lazy; the first genuine obligation still gets
            # the identical complete formula and validation path.
            formula = encoding.exact_formula()
        remaining_ms = max(
            0, math.ceil((deadline - time.monotonic()) * 1000))
        if remaining_ms == 0:
            _add_unresolved(unresolved, Unresolved(
                "resource", f"closure[{position}]",
                "the shared exact layer-closure deadline expired before "
                f"checking {label}"))
            return
        try:
            result = solve_exact(
                formula,
                target,
                pin_atoms=formula.boundary_atoms,
                timeout_ms=remaining_ms,
                memory_mb=SOLVER_MEMORY_MB,
                check=z3_check_qfbv,
            )
        except ExactSliceValidationDisagreement as exc:
            _add_unresolved(unresolved, Unresolved(
                "capability", f"closure[{position}]",
                "the audited layer-closure slice for "
                f"{label} disagreed with its complete formula: {exc}"))
            return
        if result.answer is SolverAnswer.SAT:
            _add_unresolved(unresolved, Unresolved(
                "range", f"closure[{position}]",
                f"a normally completing symbolic step can leave {label} "
                "outside its declared finite layer"))
            return
        if result.answer is SolverAnswer.UNKNOWN:
            _add_unresolved(unresolved, Unresolved(
                "resource", f"closure[{position}]",
                "the exact layer-closure query for "
                f"{label} was unresolved: {result.reason or 'unknown'}"))
            return


def verify(proc, plan=None, env=None):
    """Every type, shape, and range fault from the real encoder walk."""
    from .encoder import verify_encoding

    if env is None:
        env = plan.env if plan is not None else {}
    if plan is not None:
        if type(plan) is not EncodingPlan:
            raise TypeError("plan is not an EncodingPlan")
        plan.validate(proc, env)
    return verify_encoding(proc, env)


def position_analysis_identities(prepared, *, frontend, policy):
    """Names for the exact position plan and transition used by production.

    The function accepts the prepared value itself, rather than a procedure and
    parameters from which it might accidentally build a second plan.  Thus the
    identity recorded beside a query is necessarily the identity of the object
    that query consumed.
    """
    if type(prepared) is not _PreparedPositionPlan:
        raise TypeError(
            "position evidence identity requires an exact prepared position plan")
    representation = _digest((
        "behavior-position-representation/1",
        prepared.identity,
        prepared.plan.identity,
    ))
    encoding = _digest((
        "behavior-position-symbolic-transition/1",
        representation,
        str(frontend),
        str(policy),
    ))
    return {
        "representation": (
            f"behavior-position-representation/1:{representation}"),
        "encoding": (
            f"behavior-position-symbolic-transition/1:{encoding}"),
    }


def plan_positions(proc, env, bound, *, _closure_deadline=None,
                   _closure_timeout_ms=None, _fail_on_incomplete=False):
    """Derive and check the appendix-style finite layer representation.

    ``env`` may be the joined environment stored on a ``BehaviorInstance``;
    only the task inputs are authority at this boundary.  State layers start
    from the procedure's exact initial valuation and are advanced by the same
    range transformer that plans the encoder.  A symbolic closure query then
    checks each abstract transition rather than treating the transformer as a
    proof of its own result.  The private deadline/timeout pair lets the complete
    analysis job place that query inside its recorded resource envelope; neither is
    part of the resulting representation identity.
    """
    bound = _checked_bound(bound)
    if bound is None:
        raise ValueError("a position-indexed plan needs a positive finite bound")
    if _closure_deadline is not None \
            and (type(_closure_deadline) not in {int, float}
                 or not math.isfinite(_closure_deadline)):
        raise TypeError("a position-closure deadline is a finite monotonic time")
    if _closure_timeout_ms is not None \
            and (type(_closure_timeout_ms) is not int
                 or _closure_timeout_ms < 1):
        raise TypeError("a position-closure timeout is a positive integer")
    if type(_fail_on_incomplete) is not bool:
        raise TypeError("the internal incomplete-plan policy is Boolean")
    from .validation import _procedure_validation

    procedure_validation = _procedure_validation(proc, compact_arrays=True)
    layers = _derive_position_layers(
        proc, env, bound, _fail_on_incomplete=_fail_on_incomplete,
        _validation=procedure_validation)
    unresolved = _UniqueUnresolved()
    for item in layers.unresolved:
        unresolved.append(item)

    # Exact closure is meaningful only after every term needed by the symbolic
    # step has a finite carrier.  It is deliberately a second reading: the
    # abstract transformer proposes a layer; the formula independently tries
    # to leave it.
    closure_deadline = _closure_deadline
    if _closure_timeout_ms is not None:
        import time

        query_deadline = time.monotonic() + _closure_timeout_ms / 1000.0
        closure_deadline = (query_deadline if closure_deadline is None
                            else min(closure_deadline, query_deadline))
    checked_requests = set()
    for position in layers.proof_positions:
        step = layers.steps[position]
        request = (
            step.fingerprint,
            _environment_image(layers.environments[position]),
            layers.scalar_width,
            _carrier_layer_image(layers.state_layers[position + 1]),
            _carrier_layer_image(layers.output_layers[position]),
        )
        if request in checked_requests:
            continue
        _check_position_closure(
            proc,
            position,
            layers.environments[position],
            step,
            layers.occurrence_layers[position],
            layers.scalar_width,
            layers.state_layers[position + 1],
            layers.output_layers[position],
            unresolved,
            certified_outputs=layers.certified_outputs,
            deadline=closure_deadline,
            _procedure_validation=procedure_validation,
        )
        # Equal requests denote the same symbolic implication.  Repeating an
        # UNKNOWN or construction failure at every identical horizon position
        # cannot add evidence; it only multiplies the same diagnostic and cost.
        checked_requests.add(request)

    return PositionEncodingPlan(
        layers.state_layers, layers.input_layers, layers.output_layers,
        layers.occurrence_layers, layers.steps, layers.normal_layers,
        layers.scalar_width, layers.bound, unresolved, layers.inputs,
        layers.procedure_fingerprint)
