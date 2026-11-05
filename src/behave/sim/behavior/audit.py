"""Finite differential audits of Behavior lowering and encoding.

`model_audit` compares source execution with the IR: source-owned state,
ordered output transactions, goal hits and failure codes. `step_audit`
compares IR execution with its symbolic encoding. Missing executions cannot
establish agreement or disagreement. Neither finite audit proves universal
frontend correctness."""

from __future__ import annotations

import z3

from ..core.instance import StepOutput
from ..core.record_value import freeze_record_value, same_record_value
from ..core.refusal import Refusal, Refused
from ..core.resources import available_cpus, worker_limit
from ..symbolic import encoder
from ..symbolic import interp
from ..symbolic import ir, typecheck as types
from ..symbolic.encoder import encode_step
from ..symbolic.functional_array import FunctionalArray
from ..symbolic.values import copy_value, value_equal
from ..symbolic.native_values import NativeValueError, clone, typed_equal
from ..symbolic.value_contract import representation_value
from ..symbolic.state_projection import (
    ABSENT_STATE,
    SourceProductBinding,
    StatePresenceBinding,
    StateProjectionError,
    preserved_on_reset,
)
from ..symbolic.uniform_array import UniformArray

__all__ = ["Disagreement", "model_audit", "step_audit"]


# A folded field needs an independent snapshot; an unsupported carrier is a finding.
_UNSNAPSHOTTABLE = object()


def _snapshot(value, _path=None):
    """A faithful independent copy, or the audit's fail-closed sentinel."""
    if value is _ABSENT:
        return value
    try:
        return clone(value)
    except NativeValueError:
        return _UNSNAPSHOTTABLE


# An absent field is distinct from a field whose value is None.
_ABSENT = ABSENT_STATE


class Disagreement:
    """One place two readings of the same step differ."""

    __slots__ = ("left", "right", "stimulus", "what")

    def __init__(self, stimulus, what, left, right):
        self.stimulus = stimulus
        self.what = what
        self.left = left
        self.right = right

    def __repr__(self):
        return f"{self.what}: {self.left!r} against {self.right!r}"


class _PreparedPositionAudit:
    """The symbolic steps shared by audits of one sealed position plan.

    This caches representation, not an audit answer. Each trace gets fresh
    fixed-input solver queries and interpreter replay. Stateless equal positions
    may share one formula, but never conjoin mutually conflicting inputs.
    """

    __slots__ = ("encodings", "independent", "plan_identity")

    def __init__(self, encodings, plan_identity, *, independent=False):
        encodings = tuple(encodings)
        if any(type(item) is not encoder.StepEncoding for item in encodings):
            raise TypeError(
                "a prepared position audit contains only StepEncoding values")
        if type(plan_identity) is not str or not plan_identity:
            raise ValueError("a prepared position audit needs its plan identity")
        if type(independent) is not bool:
            raise TypeError("independent audit positions require a Boolean flag")
        object.__setattr__(self, "encodings", encodings)
        object.__setattr__(self, "independent", independent)
        object.__setattr__(self, "plan_identity", plan_identity)

    def __setattr__(self, name, value):
        raise AttributeError("a prepared position audit is immutable")

    def checked_for(self, proc, env, position_plan):
        raw = position_plan.checked_for(proc, env, position_plan.bound)
        if position_plan.identity != self.plan_identity:
            raise ValueError("the prepared audit belongs to another position plan")
        if len(self.encodings) > raw.bound:
            raise ValueError("the prepared audit exceeds its position plan")
        if self.independent and not _independent_audit_positions(proc, raw):
            raise ValueError("the shared audit no longer has independent positions")
        return self.encodings


def _independent_audit_positions(proc, plan):
    """Whether positions differ only by symbol names, not by step semantics.

    The planner interns equal steps. Identity of those immutable plans, equal
    environments and equal occurrence carriers prove that the encoder would
    execute the same construction at every position. Empty carried state makes
    their fixed-input queries independent. A failed premise retains chaining.
    """
    if (proc.state or proc.initial or proc.semantics.state_types
            or any(plan.state_layers)):
        return False
    first = plan.steps[0]
    environment = plan.environment_at(0)
    carriers = plan.occurrence_layers[0]
    return all(
        step is first and plan.environment_at(position) == environment
        and plan.occurrence_layers[position] == carriers
        for position, step in enumerate(plan.steps))


def _prepare_position_audit(
        proc, env, position_plan, required=None, prepared=None):
    """Encode one chained symbolic prefix, extending a prior prefix if present.

    The first state is the exact concrete construction state and every later state
    is the preceding symbolic successor.  This is both the trace semantics and the
    representation needed by large memories: making an unrelated arbitrary state
    at every position would turn a sparse indexed base into one packed atom whose
    concrete audit had to enumerate the full memory capacity.

    With no carried state and identical position plans, one formula suffices;
    its positions are checked in separate fixed-input solvers instead of chained.
    """

    from ..symbolic.encoder import _encode_derived_step
    from ..symbolic.closure_proof import _PreparedPositionPlan

    if type(position_plan) is not _PreparedPositionPlan:
        raise TypeError(
            "a position-aware step audit needs the internally prepared plan")
    raw = position_plan.checked_for(proc, env, position_plan.bound)
    if required is None:
        required = raw.bound
    if type(required) is not int or isinstance(required, bool) \
            or not 0 <= required <= raw.bound:
        raise ValueError("a prepared audit prefix lies within its position plan")
    if prepared is None:
        encodings = []
        initial_env = raw.environment_at(0)
        state_types = dict(proc.semantics.state_types)
        pre = {
            name: encoder.encode_concrete_value(
                value, initial_env.get(name), name,
                kind=state_types.get(name), width=raw.scalar_width)
            for name, value in proc.initial.items()
            if name in initial_env
        }
    else:
        if type(prepared) is not _PreparedPositionAudit:
            raise TypeError("a prior prepared audit has its closed internal type")
        encodings = list(prepared.checked_for(proc, env, position_plan))
        pre = (dict(encodings[-1].state_post) if encodings else {})
    if len(encodings) >= required:
        return prepared if prepared is not None else _PreparedPositionAudit(
            encodings, position_plan.identity)
    independent = _independent_audit_positions(proc, raw)
    if independent and encodings:
        return _PreparedPositionAudit(
            (encodings[0],) * required, position_plan.identity, independent=True)
    for position in range(len(encodings), required):
        encoded = _encode_derived_step(
            proc,
            pre=pre,
            env=raw.environment_at(position),
            tag=f"@step-audit-{position}",
            plan=raw.steps[position],
            occurrence_carriers=raw.occurrence_layers[position],
            scalar_width=raw.scalar_width,
        )
        encodings.append(encoded)
        if independent:
            # Reuse the formula only across separate solvers. Conjoining these
            # copies would incorrectly equate different positions' inputs.
            return _PreparedPositionAudit(
                (encoded,) * required, position_plan.identity, independent=True)
        pre = dict(encoded.state_post)
    return _PreparedPositionAudit(encodings, position_plan.identity)


def _audit_chunk(work):
    """Audit a share of the traces, in a process of its own."""
    proc, env, traces, state, position_plan = work
    return step_audit(
        proc, env, traces, state=state, workers=1,
        position_plan=position_plan)


def _parallel_step_audit(
        proc, env, traces, state, workers, position_plan=None):
    from concurrent.futures import ProcessPoolExecutor

    requested = (min(len(traces), max(1, available_cpus() - 2))
                 if workers is None else workers)
    workers = worker_limit("BEHAVE_ANALYSIS_WORKERS", requested)
    if workers == 1:
        # A named job may already own the sole available CPU. Keep the exact
        # audit in that process instead of opening a redundant one-worker pool.
        return step_audit(proc, env, traces, state=state, workers=1,
                          position_plan=position_plan)
    shares = [traces[i::workers] for i in range(workers)]
    out = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for got in pool.map(_audit_chunk,
                            [(proc, env, share, state, position_plan)
                             for share in shares if share]):
            out.extend(got)
    return out


def _decoded_value(term, model, kind=None):
    """Read one model value in the recursive SolverIR semantic type."""

    if isinstance(term, FunctionalArray):
        element_kind = kind.elem if isinstance(kind, types.Array) else None
        return term.to_uniform(
            model,
            lambda current_model, leaf: _decoded_value(
                leaf, current_model, element_kind),
        )
    if isinstance(term, list):
        element_kind = kind.elem if isinstance(kind, types.Array) else None
        return [_decoded_value(item, model, element_kind) for item in term]
    value = model.eval(term, model_completion=True)
    if kind == types.BOOLEAN:
        if z3.is_bool(value):
            return z3.is_true(value)
        integer = value.as_long()
        if integer not in (0, 1):
            raise ValueError("an encoded Boolean has a value outside {0, 1}")
        return bool(integer)
    return value.as_signed_long()


def _concrete_tree(value):
    """Expose the interpreter's compact array as its SolverIR value."""
    if type(value) is UniformArray:
        return value
    if type(value) is list:
        return [_concrete_tree(item) for item in value]
    return value


def _output_value_equal(left, right):
    """Typed equality after the source/IR output projection."""
    return same_record_value(
        freeze_record_value(left), freeze_record_value(right))


def _assert_value(solver, term, value):
    """Fix a scalar or recursive array term to one concrete IR value."""
    value = _concrete_tree(value)
    if isinstance(term, FunctionalArray):
        if type(value) is UniformArray:
            solver.add(term.equal(FunctionalArray.from_uniform(
                value, term.element_width)))
            return
        if not isinstance(value, list) or len(value) != term.length:
            solver.add(z3.BoolVal(False))
            return
        width = max(1, term.length.bit_length() + 1)
        for index, concrete in enumerate(value):
            _assert_value(
                solver,
                term.select(z3.BitVecVal(index, width)),
                concrete,
            )
        return
    if isinstance(term, list) and type(value) is UniformArray:
        value = value.materialize()
    if isinstance(term, list) != isinstance(value, list):
        solver.add(z3.BoolVal(False))
        return
    if isinstance(term, list):
        if len(term) != len(value):
            solver.add(z3.BoolVal(False))
            return
        for child, concrete in zip(term, value):
            _assert_value(solver, child, concrete)
        return
    solver.add(term == value)


def step_audit(proc, env, traces, encoding=None, state=None, workers=None,
               position_plan=None, _prepared_position=None):
    """Compare chained IR executions with their encodings, variable by variable.

    Each step starts from its predecessor's state. Independent traces may run in
    separate workers, which construct their own solver terms. A failed trace must
    retain its requested horizon through an absorbing suffix.
    """
    if encoding is not None and position_plan is not None:
        raise TypeError(
            "a step audit accepts either one test encoding or the prepared "
            "position plan, not both")
    if _prepared_position is not None and position_plan is None:
        raise TypeError("a prepared position audit needs its position plan")
    if _prepared_position is not None \
            and type(_prepared_position) is not _PreparedPositionAudit:
        raise TypeError("a prepared position audit has its closed internal type")
    if workers != 1 and encoding is None and len(traces) > 1 \
            and _prepared_position is None:
        return _parallel_step_audit(
            proc, env, traces, state, workers, position_plan)
    positioned = position_plan is not None
    if positioned:
        from ..symbolic.encoder import _encode_derived_step
        from ..symbolic.closure_proof import _PreparedPositionPlan

        if type(position_plan) is not _PreparedPositionPlan:
            raise TypeError(
                "a position-aware step audit needs the internally prepared plan")
        raw = position_plan.checked_for(proc, env, position_plan.bound)
        length = max((len(trace) for trace in traces), default=0)
        if length > raw.bound:
            raise ValueError(
                f"a step-audit trace has {length} steps but the prepared plan "
                f"covers {raw.bound}")
        if _prepared_position is None:
            enc = tuple(
                _encode_derived_step(
                    proc,
                    env=raw.environment_at(position),
                    tag=f"@step-audit-{position}",
                    plan=raw.steps[position],
                    occurrence_carriers=raw.occurrence_layers[position],
                    scalar_width=raw.scalar_width,
                )
                for position in range(length)
            )
        else:
            enc = _prepared_position.checked_for(proc, env, position_plan)
            if len(enc) < length:
                raise ValueError(
                    "the prepared position audit does not cover the trace prefix")
            enc = enc[:length]
    else:
        enc = encoding or encode_step(proc, env=env)
    out = []
    for trace in traces:
        out.extend(_audit_trace(
            proc, enc, trace, state, positioned=positioned,
            independent=(_prepared_position is not None
                         and _prepared_position.independent)))
        # Step evaluation stops at failure; the trace must still absorb its suffix.
        out.extend(absorbed_suffix(proc, trace, state))
    return out


def _audit_model(solver):
    """Return a model, or None only for a proved empty encoding."""
    from ..analysis_budget import check_analysis, remaining_query_ms

    remaining = remaining_query_ms(None)
    if remaining is not None:
        solver.set(timeout=remaining)
    outcome = solver.check()
    check_analysis()
    if outcome == z3.unknown:
        raise Refused(Refusal(
            "behavior-step-audit-unresolved",
            "the fixed Behavior encoding query is unresolved: "
            + solver.reason_unknown()))
    return solver.model() if outcome == z3.sat else None


def _fixed_input_model(encoding, fixed):
    # Assertions, not assumptions: preprocessing sees the concrete values.
    solver = z3.Solver()
    solver.add(encoding.constraint)
    for name, value in fixed.items():
        term = encoding.pre.get(name)
        if term is not None:
            _assert_value(solver, term, value)
    return _audit_model(solver)


def _audit_input_image(fixed):
    """Copy IR values without expanding compact arrays or changing their types."""
    try:
        return {name: copy_value(value) for name, value in fixed.items()}
    except (TypeError, RecursionError):
        return None


def _same_audit_inputs(left, right):
    return (left is not None and right is not None and left.keys() == right.keys()
            and all(value_equal(value, right[name]) for name, value in left.items()))


def _audit_trace(proc, enc, trace, state, *, positioned=False, independent=False):
    if positioned:
        if state is not None:
            raise ValueError(
                "a position-aware audit starts from its sealed construction state")
        if not independent:
            return _audit_positioned_trace(proc, enc, trace)

    out = []
    at = dict(proc.initial if state is None else state)
    last_encoding, last_fixed, last_model = None, None, None
    for position, inputs in enumerate(trace):
        step_encoding = enc[position] if positioned else enc
        fixed = dict(inputs)
        for name, value in at.items():
            if name in step_encoding.pre:
                fixed[name] = value
        image = _audit_input_image(fixed) if independent else None
        if step_encoding is last_encoding and _same_audit_inputs(image, last_fixed):
            # Identical stateless fixed-input equations have the same reading.
            # Reuse at most the preceding model, within this trace only. Every
            # concrete step is still executed and compared below.
            model = last_model
        else:
            model = _fixed_input_model(step_encoding, fixed)
        if model is None:
            out.append(Disagreement(inputs, "the formula admits no run",
                                    None, None))
            return out
        last_encoding, last_fixed, last_model = step_encoding, image, model
        observed = _observe_step(proc, inputs, at)
        out.extend(_compare_step(proc, step_encoding, inputs, observed, model))
        post, _produced, _hits, error, _left = observed
        if error:
            return out                  # the run stops, so the trace does
        at = post
    return out


def _observe_step(proc, inputs, state):
    """One concrete IR step plus every variable the command leaves."""

    post, produced, hits, error = interp.run_step(proc, inputs, state)
    _, _, _, _, watch = interp.trace_step(proc, inputs, state)
    left = {}
    for _cmd, name, value in watch:
        left[name] = value
    # A state variable the command does not write is still an observable part of
    # the successor state.  Omitting it would let the symbolic step change it
    # without the audit noticing.
    for name in proc.state:
        if name not in left and name in post:
            left[name] = post[name]
    return post, produced, hits, error, left


def _compare_step(proc, step_encoding, inputs, observed, model):
    """Compare one encoded successor with one already-run concrete step."""

    _post, produced, hits, error, left = observed
    out = []
    for name, value in left.items():
        term = step_encoding.final.get(name)
        if term is None:
            out.append(Disagreement(
                inputs, f"{name} is not in the formula", None, value))
            continue
        said = _decoded_value(term, model, proc.types.get(name))
        value = _concrete_tree(value)
        if not value_equal(said, value):
            out.append(Disagreement(inputs, name, said, value))

    if proc.count is not None and proc.count in step_encoding.final:
        said_count = _decoded_value(
            step_encoding.final[proc.count], model, types.INTEGER)
        if said_count != produced.count:
            out.append(Disagreement(
                inputs, "how many transactions", said_count, produced.count))

    said_hits = {
        goal for goal, condition in step_encoding.hits.items()
        if z3.is_true(model.eval(condition, model_completion=True))
    }
    if said_hits != hits:
        out.append(Disagreement(
            inputs, "the goals reached",
            sorted(said_hits - hits), sorted(hits - said_hits)))
    said_ok = z3.is_true(model.eval(
        step_encoding.ok, model_completion=True))
    if said_ok == bool(error):
        out.append(Disagreement(
            inputs, "whether the step completed", said_ok, not error))
    elif error:
        said_code = encoder.error_name(
            model.eval(step_encoding.error, model_completion=True),
            step_encoding.error_codes)
        if said_code != error:
            out.append(Disagreement(
                inputs, "the error code", said_code, error))
    return out


def _audit_positioned_trace(proc, encodings, trace):
    """Audit one chained concrete trace with one solver call.

    Every encoded position starts from the preceding encoded successor.  Fixing
    the complete input trace therefore fixes all normal states; solving each
    growing prefix again would ask the same question N times and retain N solver
    states.  One model suffices to compare every recorded successor and hit set.
    """

    solver = z3.Solver()
    at = dict(proc.initial)
    rows = []
    for position, inputs in enumerate(trace):
        step_encoding = encodings[position]
        solver.add(step_encoding.constraint)
        for name, value in inputs.items():
            term = step_encoding.pre.get(name)
            if term is not None:
                _assert_value(solver, term, value)
        observed = _observe_step(proc, inputs, at)
        rows.append((step_encoding, inputs, observed))
        post, _produced, _hits, error, _left = observed
        if error:
            break
        at = post

    model = _audit_model(solver)
    if model is None:
        inputs = rows[-1][1] if rows else trace
        return [Disagreement(inputs, "the formula admits no run", None, None)]
    out = []
    for step_encoding, inputs, observed in rows:
        out.extend(_compare_step(proc, step_encoding, inputs, observed, model))
    return out


def absorbed_suffix(proc, trace, state=None):
    """Check finite trace composition against chained steps in linear step work.

    After the first failure the expected state is fixed, with empty outputs and
    no hits. Detailed step records and the public aggregate must both agree;
    neither is used to construct the expected trace.
    """
    out = []
    states, outputs, hit_sets, error = interp.run_trace_detailed(proc, trace, state)
    at = {name: copy_value(value) for name, value in
          (proc.initial if state is None else state).items()}

    def length(what, values, expected):
        if len(values) != expected:
            out.append(Disagreement(
                trace, f"the trace {what} has the wrong length",
                len(values), expected))

    def state_matches(what, observed, expected):
        for name in sorted(set(observed) | set(expected)):
            left, right = observed.get(name, _ABSENT), expected.get(name, _ABSENT)
            if left is _ABSENT or right is _ABSENT or not value_equal(left, right):
                out.append(Disagreement(
                    trace, f"{what}: state {name}",
                    "absent" if left is _ABSENT else left,
                    "absent" if right is _ABSENT else right))

    def output_matches(what, observed, expected):
        if observed.count != expected.count or not _output_value_equal(
                observed.transactions, expected.transactions):
            out.append(Disagreement(
                trace, f"{what} produced transactions",
                observed.transactions, expected.transactions))

    length("states", states, len(trace) + 1)
    length("outputs", outputs, len(trace))
    length("hit sets", hit_sets, len(trace))
    if states:
        state_matches("initial", states[0], at)
    halted = ""
    reached = set()
    expected_outputs = []
    ended = None
    for index, inputs in enumerate(trace):
        if halted:
            produced, hits = StepOutput(), set()
        else:
            at, produced, hits, halted = interp.run_step(proc, inputs, at)
            if halted:
                ended = index
        expected_outputs.append(produced)
        reached.update(hits)
        where = f"step {index}"
        if ended is not None and index > ended:
            where += " after the failure"
        if index + 1 < len(states):
            state_matches(where, states[index + 1], at)
        if index < len(outputs):
            output_matches(where, outputs[index], produced)
        if index < len(hit_sets) and hit_sets[index] != hits:
            out.append(Disagreement(
                trace, f"{where}: hits", sorted(hit_sets[index]), sorted(hits)))
    if error != halted:
        out.append(Disagreement(trace, "the trace error code", error, halted))

    state_after, aggregate_outputs, aggregate_hits, aggregate_error = (
        interp.run_trace(proc, trace, state))
    state_matches("aggregate", state_after, at)
    length("aggregate outputs", aggregate_outputs, len(trace))
    for index, (observed, expected) in enumerate(zip(
            aggregate_outputs, expected_outputs)):
        where = f"step {index}"
        if ended is not None and index > ended:
            where += " after the failure"
        output_matches(where, observed, expected)
    if aggregate_hits != reached:
        out.append(Disagreement(
            trace, "the aggregate hits", sorted(aggregate_hits), sorted(reached)))
    if aggregate_error != halted:
        out.append(Disagreement(
            trace, "the aggregate error code", aggregate_error, halted))
    return out


def _transactions_of(produced):
    """Normalize a native return: None, one mapping, or an ordered list of mappings."""
    if produced is None:
        return []
    if isinstance(produced, list):
        return [dict(one) for one in produced]
    return [dict(produced)]


def model_audit(proc, model_cls, traces, hits_of=None, *, reset_indices=None,
                external_fields=(), observe_step=None):
    """Compare native and IR executions from reset, step by step.

    The sealed source contract fixes the state comparison universe. Each step
    compares ordered transactions, goal hits and exact failure codes; source
    execution remains independent of the lowerer and symbolic encoder.

    Internal consumers may collect the interpreter's pre-state and hits through
    ``observe_step(trace_index, step_index, state, hits, transactions)``. These are observations,
    not audit evidence: the consumer must discard them if this audit disagrees or
    cannot finish.  Collecting them avoids executing the same IR a second time.
    """
    # Type/CFG preparation is a property of this fixed program, not of each
    # input. Keep an owned execution image for the audit; do not repeatedly
    # rebuild its semantic contract or trust a mutable caller-owned procedure.
    proc = interp._prepare_procedure(proc)
    out = []
    # Which goals are the safety class, from the commands that produce them
    # rather than from the shape of their names.
    safety = {c.goal for c in ir.walk(proc.body)
              if isinstance(c, ir.Fail) and c.goal}
    contract = getattr(proc, "contract", None)
    reset_preserved = preserved_on_reset(proc)
    traces = tuple(traces)
    if reset_indices is None:
        reset_indices = ((),) * len(traces)
    for trace_index, (trace, resets) in enumerate(zip(traces, reset_indices, strict=True)):
        resets = frozenset(resets)
        if any(type(index) is not int or not 0 <= index < len(trace) for index in resets):
            raise ValueError("source-audit reset positions must index their input trace")
        for framing, projection in proc.input_framing:
            framing_state = framing.initial
            for position, inputs in enumerate(trace):
                if position in resets:
                    framing_state = framing.initial
                markers = {public: inputs[logical] for public, logical in projection
                           if public in framing.fields}
                framing_state, legal = framing.step(framing_state, markers)
                if not legal:
                    raise ValueError("source-audit input violates its declared framing")
        instance = model_cls()
        instance.reset()
        if hits_of is not None:
            hits_of.clear()
        # Only the exact isolation proxy owns the typed audit view. Candidate methods
        # cannot substitute a forged view, and opaque fields must remain findings.
        from .isolated import IsolatedModel as _Proxy
        from .isolated import is_model_exception

        trusted = type(instance) is _Proxy

        def _looked(instance=instance, trusted=trusted):
            return instance._audit_view() if trusted else (None, {})

        def _read(values, name, instance=instance):
            if values is not None:
                return values.get(name, _ABSENT)
            return getattr(instance, name, _ABSENT)

        values, opaque = _looked()
        unreadable = set()

        def _refuse_opaque(
                where, name, *, unreadable=unreadable, opaque=opaque, out=out):
            unreadable.add(name)
            out.append(Disagreement(
                where, f"the state {name}",
                f"a {opaque[name]} the isolation cannot carry to the audit",
                "a value the audit can read"))

        for name in (contract.fields if contract is not None else ()):
            if name in opaque:
                _refuse_opaque("after reset", name)
        # What the folded fields held when the lowering froze them in: a field
        # represented as ("folded",) has no runtime carrier, so the audit's
        # obligation is that the native value never moves after reset.
        folded_baseline = {}
        for name, how in (contract.items() if contract is not None else ()):
            if how[0] != "folded" or name in unreadable:
                continue
            taken = _snapshot(_read(values, name))
            folded_baseline[name] = taken
            if taken is _UNSNAPSHOTTABLE:
                # Reported, not trusted: without an independent copy, "never
                # moves after reset" would be compared against the live object
                # itself and hold vacuously.
                out.append(Disagreement(
                    "after reset", f"the folded state {name}",
                    f"a {type(_read(values, name)).__name__} the "
                    f"audit cannot snapshot",
                    "a carrier from the closed snapshot set"))
        at = None
        for step, inputs in enumerate(trace):
            if step in resets:
                from .reference_memory import reset_state

                instance.reset()
                at = reset_state(proc.initial, proc.initial if at is None else at,
                                 external_fields, preserved_fields=reset_preserved)
            if hits_of is not None:
                hits_of.clear()
            try:
                produced = instance.process(**inputs)
            except Exception as e:
                if trusted and not is_model_exception(e):
                    raise
                produced, raised = None, f"{type(e).__name__}"
                native_code = encoder.EXCEPTION_CODES.get(raised)
                if type(e) is ValueError:
                    if trusted and hits_of is not None:
                        # Isolated exceptions do not retain Python messages.
                        # Their independently observed source occurrence does
                        # distinguish a shift failure from a modular pow error.
                        catalogue = instance._source_catalogue
                        kinds = {catalogue[goal].family for goal in hits_of
                                 if goal in catalogue and catalogue[goal].family
                                 in {"negativeshift", "valueerror"}}
                        if kinds == {"negativeshift"}:
                            native_code = "NegativeShift"
                    elif str(e) == "negative shift count":
                        native_code = "NegativeShift"
            else:
                raised = ""
            # The step changed the model's state, so the faithful view is
            # taken again: a field can become uncarriable mid-trace.
            values, opaque = _looked()
            before = proc.initial if at is None else at
            at, left, got, error = proc.run_step(inputs, at)
            if observe_step is not None:
                observe_step(trace_index, step, before, frozenset(got), left.transactions)
            if hits_of is not None:
                ran, expected = set(hits_of), set(got)
                if not trusted:
                    # Plain instrumentation has no native-exception attribution;
                    # its failures are checked by error code below. The isolated
                    # production proxy also supplies exact safety occurrences.
                    ran -= safety
                    expected -= safety
                if ran != expected:
                    out.append(Disagreement(
                        inputs, f"the goals reached at step {step}",
                        sorted(expected - ran), sorted(ran - expected)))
            if bool(error) != bool(raised):
                out.append(Disagreement(inputs, "whether the step failed",
                                        error or "completed",
                                        raised or "completed"))
                break
            if error:
                # Source failures have exact mapped codes. Analysis-limit codes are
                # not native exceptions and cannot establish source correspondence.
                if error not in proc.semantics.analysis_errors:
                    wanted = native_code
                    if wanted is None:
                        out.append(Disagreement(
                            inputs, "the model's failure has no code",
                            error, raised))
                    elif wanted != error:
                        out.append(Disagreement(inputs, "which failure",
                                                error, f"{raised} ({wanted})"))
                break
            # The source contract owns the fields and their representation. A missing
            # state field is a finding, never an implicit initial-state fallback.
            for name in (contract.fields if contract is not None
                         else proc.state):
                if name in unreadable:
                    # Already refused once, fail closed, per trace.
                    continue
                if name in opaque:
                    _refuse_opaque(inputs, name)
                    continue
                mine = _read(values, name)
                how = contract.how(name) if contract is not None else ("state",)
                if how[0] == "folded":
                    theirs = folded_baseline.get(name, _ABSENT)
                    if theirs is _UNSNAPSHOTTABLE:
                        # Already reported once, at reset, per trace.
                        continue
                elif how[0] == "constant":
                    # The contract reconstructs the constant's original native type.
                    theirs = how[1]
                else:
                    binding = proc.semantics.value_binding(
                        "state", name, required=False)
                    projection = (contract.projection(name)
                                  if contract is not None else None)
                    if binding is not None:
                        try:
                            theirs = binding.reconstruct(at)
                        except ValueError:
                            theirs = _ABSENT
                    elif projection is not None:
                        try:
                            theirs = projection.reconstruct(at)
                        except StateProjectionError as exc:
                            out.append(Disagreement(
                                inputs, f"the state {name} projection",
                                f"invalid SolverIR state: {exc}",
                                "the declared logical state image"))
                            continue
                    else:
                        theirs = at.get(name, _ABSENT)
                if mine is _ABSENT and theirs is _ABSENT:
                    # Neither side ever held it on this trace: a field written
                    # only on paths these inputs did not take.
                    continue
                if (mine is _ABSENT) != (theirs is _ABSENT):
                    out.append(Disagreement(
                        inputs, f"the state {name}",
                        "absent" if theirs is _ABSENT else theirs,
                        "absent" if mine is _ABSENT else mine))
                    continue
                # Folded/constant fields compare native types exactly. Carried state
                # instead compares through its declared source-to-IR value map.
                if how[0] in ("constant", "folded"):
                    if not typed_equal(mine, theirs):
                        out.append(Disagreement(inputs, f"the state {name}",
                                                theirs, mine))
                    continue
                binding = proc.semantics.value_binding(
                    "state", name, required=False)
                if binding is not None:
                    # Mapping insertion order is not part of a declared record
                    # value.  Compare through the canonical logical layout so
                    # exact Boolean/Integer/enum leaf carriers are retained
                    # without treating two field orders as different records.
                    try:
                        agrees = (binding.layout.flatten(mine)
                                  == binding.layout.flatten(theirs))
                    except ValueError:
                        agrees = False
                elif isinstance(projection, SourceProductBinding) or (
                        isinstance(projection, StatePresenceBinding)
                        and isinstance(projection.value, SourceProductBinding)):
                    product = (projection.value if isinstance(projection, StatePresenceBinding)
                               else projection)
                    agrees = product.equivalent(mine, theirs)
                else:
                    mine = representation_value(mine)
                    theirs = representation_value(theirs)
                    agrees = value_equal(mine, theirs)
                if not agrees:
                    out.append(Disagreement(inputs, f"the state {name}",
                                            theirs, mine))
            # Compare every transaction, preserving sequence length and order.
            want = _transactions_of(produced)
            if proc.signature is not None:
                try:
                    want = [
                        proc.signature.project_source_transaction(transaction)
                        for transaction in want
                    ]
                except ValueError as exc:
                    out.append(Disagreement(
                        inputs, "the source output domain",
                        "a value in the declared source-to-IR output map",
                        str(exc)))
                    break
            got_txns = left.transactions
            if len(got_txns) != len(want):
                out.append(Disagreement(inputs, "how many transactions",
                                        len(got_txns), len(want)))
                break
            for index, (said, value) in enumerate(zip(got_txns, want)):
                # The union detects both missing and extra fields.
                for name in sorted(set(said) | set(value)):
                    mine_f = value.get(name, _ABSENT)
                    said_f = said.get(name, _ABSENT)
                    if mine_f is _ABSENT or said_f is _ABSENT \
                            or not _output_value_equal(mine_f, said_f):
                        out.append(Disagreement(
                            inputs, f"{name} of transaction {index}",
                            "absent" if said_f is _ABSENT else said_f,
                            "absent" if mine_f is _ABSENT else mine_f))
    return out
