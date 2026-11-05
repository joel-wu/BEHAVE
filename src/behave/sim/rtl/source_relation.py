"""Symbolic source-event intervals shared by search and bounded construction.

The caller owns the initial-state, pin, protocol and schedule domain. This
relation does not obtain a boundary from a baseline, select a native process
order, solve a query or grant an analysis capability. It retains all enabled
scheduler choices within the supplied event bound. A whole-domain consumer
must establish that bound's completeness and its native-order correspondence.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from ..core.construction_checkpoint import finish_construction

if TYPE_CHECKING:
    from ..symbolic.step_template import StepTemplate


class SourceObservation(Enum):
    """Closed observation policies; neither one changes the execution domain."""

    PHASE_EVENTS = 'phase-events/1'
    GOAL_PREFIX = 'goal-prefix/1'


class _HistoryControlFacts:
    """Scalar definitions valid on one consecutive, active source prefix.

    Transition constraints are guarded, so their definitions are NOT global
    equations. A later phase may use them only when its entry continues the
    preceding live/settled phase (or the same enabled, required-quiet chain).
    Changed boundaries, native replacement, restart and unrelated callers
    retire the proof. No fact is an extra asserted constraint or a verdict.
    """

    def __init__(self):
        self.previous = None
        self.enabled = None
        self.proof = None

    def begin(self, state, alive, enabled):
        import z3
        from behave.isolation.preparation_control import check_preparation
        from ..symbolic.term_bounds import _SyntacticClosureProof, _StructuralProofWork

        def same(left, right):
            return left is right or (z3.is_expr(left) and z3.is_expr(right) and z3.eq(left, right))

        previous = self.previous
        if (previous is None or state is not previous.state
                or not same(alive, previous.alive) or not same(enabled, self.enabled)):
            self.proof = _SyntacticClosureProof({}, work=_StructuralProofWork(
                None, float('inf'), checkpoint=check_preparation))
        return self.proof

    def finish(self, phase, enabled, require_quiescence):
        self.previous = phase
        self.enabled = enabled if require_quiescence else phase.continuable


def _accumulate_guarded_hits(target, alive, hits):
    """Keep every event key, without building guarded literal-false events."""
    import z3
    from z3 import z3core as native
    from .symbolic_state import assertion_conjunction

    for name, hit in hits.items():
        terms = target.setdefault(name, [])
        literal = native.Z3_get_bool_value(hit.ctx_ref(), hit.as_ast())
        if literal != z3.Z3_L_FALSE:
            terms.append(alive if literal == z3.Z3_L_TRUE
                         else assertion_conjunction((alive, hit)))


def source_event_images(phases, scope, *, check=None):
    """Retain every goal AND occurrence, optionally as monotone prefix bits.

    Prefix observations distinguish the first hit at every physical boundary,
    but not subsequent redundant activations. No recorded hit is a premise.
    Keeping individual occurrences prevents a goal OR from hiding an operand.
    """
    from .symbolic_state import assertion_disjunction

    if type(scope) is not SourceObservation:
        raise TypeError('a source observation scope must be a SourceObservation')
    previous = None
    for phase in phases:
        if check is not None:
            check()
        images = (phase.hits, phase.occurrences)
        if scope is SourceObservation.GOAL_PREFIX and previous is not None:
            if any(a.keys() != b.keys() for a, b in zip(previous, images, strict=True)):
                raise ValueError('source phases must retain the same goal and occurrence catalogue')
            images = tuple({key: assertion_disjunction((old[key], value)) for key, value in new.items()}
                           for old, new in zip(previous, images, strict=True))
        previous = images
        yield images


@dataclass(frozen=True, slots=True)
class SourceIntervalEncoding:
    constraints: tuple
    hits: dict
    occurrences: dict
    state: dict
    inputs: dict
    actions: tuple
    alive: object
    error: object
    domain_faults: dict
    quiescent: object
    entered: object
    step_definitions: tuple = ()
    entry_terms: tuple | None = None
    # Optional proof hypotheses, not constraints on this encoded interval.
    # A stronger order proof must establish initialization and preservation
    # before using these ranges at a cut boundary.
    state_range_candidates: dict | None = None
    # Constructor-owned completion order, not a proof or query restriction.
    # Native member proposals still validate every original assertion.
    member_layers: tuple = field(default=(), repr=False, compare=False)
    _continuable: object = field(default=None, init=False, repr=False, compare=False)
    _member_frames_cache: object = field(default=None, init=False, repr=False, compare=False)

    def _member_frames(self, *, check, dependencies=None):
        """Reuse immutable equation layout, never a candidate model or verdict."""
        from .monitor_template import _MonitorFrames

        check()
        if self._member_frames_cache is None:
            definitions = self.step_definitions or ((),) * len(self.actions)
            plan = _MonitorFrames(tuple(term for row in definitions for term in row),
                                  tuple(map(len, definitions)), check=check, dependencies=dependencies,
                                  proposal_layers=self.member_layers)
            check()  # A cancelled construction must not publish a cache entry.
            object.__setattr__(self, '_member_frames_cache', plan)
        return self._member_frames_cache

    @property
    def unfinished(self):
        """A live prefix still has scheduler work; failure is terminal instead."""
        import z3

        return z3.And(self.entered, self.alive, z3.Not(self.quiescent))

    @property
    def continuable(self):
        """Only a reached, live, settled interval permits the next drive."""
        import z3

        # These three ASTs are immutable. Source, protocol and monitor owners
        # ask for the same boundary repeatedly; simplifying its growing prefix
        # again at each read needlessly traverses the whole history. A replaced
        # interval starts with an empty cache, so a native cut cannot reuse the
        # unrestricted interval's condition (or vice versa).
        if self._continuable is None:
            value = _fold_control(z3.And(self.entered, self.alive, self.quiescent))
            object.__setattr__(self, '_continuable', value)
        return self._continuable


@dataclass(frozen=True, slots=True)
class SourcePhaseEncoding:
    """Separate physical intervals, retaining their samples and failure prefix."""

    phases: tuple[SourceIntervalEncoding, ...]
    constraints: tuple
    hits: dict
    occurrences: dict

    @property
    def entered(self):
        return self.phases[0].entered

    @property
    def state(self):
        return self.phases[-1].state

    @property
    def alive(self):
        return self.phases[-1].alive

    @property
    def error(self):
        return self.phases[-1].error

    @property
    def domain_faults(self):
        return _union_images(phase.domain_faults for phase in self.phases)

    @property
    def unfinished(self):
        import z3

        return z3.Or(*(phase.unfinished for phase in self.phases))

    @property
    def continuable(self):
        return self.phases[-1].continuable


def _hold_value(enabled, new, old):
    """Keep the physical/private boundary when no next interval was entered."""
    import z3

    from ..symbolic.bitvectors import widen
    from ..symbolic.functional_array import FunctionalArray
    from ..symbolic.step_template import _tree

    if z3.is_true(enabled):
        return new
    if z3.is_false(enabled):
        return old
    if isinstance(new, FunctionalArray) and isinstance(old, FunctionalArray):
        return FunctionalArray.choose(enabled, new, old)
    if isinstance(new, (list, FunctionalArray)):
        return [_hold_value(enabled, left, right)
                for left, right in zip(_tree(new), _tree(old), strict=True)]
    new, old = widen(new, old)
    # Unchanged storage needs no new hold mux. This is structural identity,
    # independent of whether the interval is reachable or its enable is true.
    return new if z3.eq(new, old) else z3.If(enabled, new, old)


def _union_images(images):
    from .symbolic_state import assertion_disjunction

    terms = {}
    for image in images:
        for name, term in image.items():
            terms.setdefault(name, []).append(term)
    return {name: assertion_disjunction(values) for name, values in terms.items()}


def _fold_control(term):
    return _fold_controls((term,))[0]


def _fold_controls(terms):
    """Fold control/conditional structure without rewriting arithmetic atoms.

    Proxies are local rewrite variables, never solver variables or premises.
    Restore every original atom after simplification. Cost follows Boolean
    structure, not the growing arithmetic histories beneath comparisons.
    """
    import z3

    gates = {z3.Z3_OP_AND, z3.Z3_OP_OR, z3.Z3_OP_NOT, z3.Z3_OP_IMPLIES,
             z3.Z3_OP_XOR, z3.Z3_OP_IFF, z3.Z3_OP_ITE, z3.Z3_OP_EQ,
             z3.Z3_OP_DISTINCT}
    terms = tuple(terms)
    known, decoded, bindings = {}, {}, []
    # Error and liveness share guard DAGs within one source event. Traverse
    # them together; no state, symbolic value or cache survives this call.
    pending = [(term, False) for term in terms]
    while pending:
        value, ready = pending.pop()
        key = value.get_id()
        if key in known:
            continue
        # A conditional returns here after each demanded arm and then its
        # guard. Decode its immutable structure once, not at every return.
        # This cache is local to the existing rewrite, not a history/model.
        if key not in decoded:
            if z3.is_const(value):
                known[key] = value
                continue
            declaration, children = value.decl(), tuple(value.children())
            kind = declaration.kind()
            logical = kind in gates and all(z3.is_bool(child) for child in children)
            decoded[key] = declaration, kind, children, logical
        declaration, kind, children, logical = decoded[key]
        if kind == z3.Z3_OP_ITE:
            guard, yes, no = children
            chosen = (yes if z3.is_true(guard) or yes.eq(no) else
                      no if z3.is_false(guard) else None)
            if chosen is not None:
                if chosen.get_id() not in known:
                    pending.extend(((value, True), (chosen, False)))
                else:
                    known[key] = known[chosen.get_id()]
                continue
            # Fold the two values before opening their condition. Equal
            # successors need no condition at all, including when dead failure
            # branches obscure that equality until these local folds finish.
            # This avoids traversing an unrelated arithmetic/control cone.
            missing = [child for child in (yes, no) if child.get_id() not in known]
            if missing:
                pending.append((value, True))
                pending.extend((child, False) for child in missing)
                continue
            yes, no = known[yes.get_id()], known[no.get_id()]
            if yes.eq(no):
                known[key] = yes
                continue
            if guard.get_id() not in known:
                pending.extend(((value, True), (guard, False)))
                continue
            guard = known[guard.get_id()]
            known[key] = yes if z3.is_true(guard) else no if z3.is_false(guard) else z3.If(guard, yes, no)
            continue
        if logical:
            if not ready:
                pending.append((value, True))
                pending.extend((child, False) for child in children)
                continue
            folded = tuple(known[child.get_id()] for child in children)
            if kind in (z3.Z3_OP_AND, z3.Z3_OP_OR):
                conjunction = kind == z3.Z3_OP_AND
                absorbing, neutral = ((z3.is_false, z3.is_true) if conjunction else
                                      (z3.is_true, z3.is_false))
                if any(absorbing(child) for child in folded):
                    known[key] = z3.BoolVal(not conjunction, ctx=value.ctx)
                    continue
                folded = tuple(child for child in folded if not neutral(child))
                if not folded or len(folded) == 1:
                    known[key] = folded[0] if folded else z3.BoolVal(conjunction, ctx=value.ctx)
                    continue
            elif kind == z3.Z3_OP_NOT and (z3.is_true(folded[0]) or z3.is_false(folded[0])):
                known[key] = z3.BoolVal(z3.is_false(folded[0]), ctx=value.ctx)
                continue
            known[key] = declaration(*folded)
        elif kind in (z3.Z3_OP_EQ, z3.Z3_OP_DISTINCT) and len(children) == 2 and z3.eq(*children):
            known[key] = z3.BoolVal(kind == z3.Z3_OP_EQ, ctx=value.ctx)
        elif all(z3.is_bv_value(child) or z3.is_int_value(child) for child in children):
            known[key] = z3.simplify(value)
        else:
            proxy = z3.FreshConst(value.sort(), prefix='control_atom')
            known[key] = proxy
            bindings.append((proxy, value))
    folded = tuple(z3.simplify(known[term.get_id()]) for term in terms)
    return tuple(z3.substitute(term, *bindings) for term in folded) if bindings else folded


def _define_control(term, definitions, tag):
    """Name an exact total control successor, without growing prefix DAGs."""
    return _define_controls((term,), definitions, (tag,))[0]


def _define_controls(terms, definitions, tags):
    """Name simultaneous control successors from their original boundary."""
    import z3

    # Fold before naming: And(True, True) is not a Z3 constant until rewritten.
    # A fresh atom for it needlessly keeps every later state behind a liveness
    # mux. This uses no premises; real failure/enable conditions remain symbolic.
    result = []
    for term, tag in zip(_fold_controls(terms), tags, strict=True):
        if z3.is_const(term):
            result.append(term)
        else:
            named = z3.FreshConst(term.sort(), prefix=tag)
            definitions.append(named == term)
            result.append(named)
    return tuple(result)


class SourceIntervalTemplate:
    """Two exact views of one transition, with no seed or solver answers."""

    def __init__(self, procedure, env, *, direct=None, _validation=None):
        from ..symbolic.step_template import _template_validation

        # Admission/type/error-alphabet ownership is eager; formula allocation
        # is not. Callers may inspect the validated semantics before selecting.
        self._validation = _template_validation(procedure, _validation)
        self.procedure, self.environment = procedure, dict(env)
        self._source = self._drive = None
        self._normalized = False
        self._selection = None
        self._live_relations = WeakValueDictionary()
        self._marks = None
        self._specialization = None
        self.direct = direct

    @property
    def context(self):
        import z3

        return z3.main_ctx() if self._source is None else self._source.context

    def initial_state(self, machine, values, **options):
        """Physical/declaration support needs types, not transition formulas."""
        from .source_initial import bind_initial_state

        return bind_initial_state(machine, self, values, **options)

    def observations(self, marks, *, outputs=(), state_reads=()):
        """Share a closed control cone across groups, not just identical lists.

        Retain its already-controlled observations, without pulling in another
        branch's computations. Only one prepared image is retained; a request
        outside it replaces the image after successful construction. A smaller
        output/state boundary reuses the image; relation() projects its exact
        requested roots rather than rebuilding the same transition.
        """
        from ..symbolic.step_template import StepTemplate

        if self._source is not None:
            return self._source
        marks = frozenset(marks)
        boundary = tuple(outputs), frozenset(state_reads)
        if (self._marks is None
                or not set(boundary[0]) <= set(self._marks[0][0])
                or not boundary[1] <= set(self._marks[1].state_names)
                or not marks <= set(self._marks[1].hit_names)):
            if self._specialization is None:
                value = StepTemplate.selected(self.procedure, self.environment, marks,
                    outputs=boundary[0], state_reads=boundary[1], include_controlled=True,
                    _validation=self._validation)
            else:
                owner, values = self._specialization
                value = owner.observations(marks, outputs=boundary[0],
                    state_reads=boundary[1]).specialize_inputs(values)
            if self._normalized:
                value = value.normalized()
            self._marks = boundary, value
        return self._marks[1]

    def local_observations(self, marks):
        """Observe one activation without first constructing a history image.

        A live history image can supply these roots, never the reverse. Cold
        local observations prune before encoding and do not replace the owner's
        history cache. State, failures and input domains remain unrestricted.
        """
        from ..symbolic.step_template import StepTemplate

        marks = frozenset(marks)
        available = self._source if self._source is not None else (
            None if self._marks is None else self._marks[1])
        if available is not None and marks <= set(available.hit_names):
            return available.project_local({mark: (mark,) for mark in sorted(marks)})
        if self._specialization is None:
            value = StepTemplate.selected(self.procedure, self.environment, marks,
                successors=False, _validation=self._validation)
        else:
            owner, values = self._specialization
            value = owner.local_observations(marks).specialize_inputs(values)
        return value.normalized() if self._normalized else value

    @property
    def source(self):
        from ..symbolic.step_template import StepTemplate
        from ..symbolic.diagnostics import diagnostic_stage

        if self._source is None:
            with diagnostic_stage('source.template'):
                if self._specialization is None:
                    value = StepTemplate(self.procedure, self.environment, _validation=self._validation)
                else:
                    owner, values = self._specialization
                    value = owner.source.specialize_inputs(values)
                self._source = value.normalized() if self._normalized else value
        return self._source

    @property
    def drive(self):
        if self._drive is None:
            # Retain the input equality, not just its folded consequences.
            value = self.source.specialize_inputs({'action': 0})
            self._drive = value.normalized() if self._normalized else value
        return self._drive

    def normalized(self):
        """Normalize the selected cone, without eagerly encoding unused code."""
        from copy import copy

        view = copy(self)
        view._normalized = True
        view._source = None if self._source is None else self._source.normalized()
        view._drive = None if self._drive is None else self._drive.normalized()
        view._selection = None
        view._live_relations = WeakValueDictionary()
        view._marks = None
        return view

    def specialize_inputs(self, values):
        """Bind inputs without forcing the unselected transition to be encoded.

        Sibling phases share their owner's demanded observation image. A later
        complete-state consumer may still request the full image. The existing
        step specialization retains every input equality, including conflicting
        nested bindings; no scheduler choice is left to the solver.
        """
        from copy import copy

        if not set(values) <= set(self.procedure.inputs):
            raise ValueError('transition specialization binds inputs, not state or unknown names')
        view = copy(self)
        view._specialization = self, dict(values)
        view._source = view._drive = None
        view._selection = None
        view._live_relations = WeakValueDictionary()
        view._marks = None
        return view

    def relation(self, machine, goals, *, context=None, complete_state=False, occurrences=False,
                 state_reads=()):
        if self.procedure is not machine.procedure:
            raise ValueError('a source relation must bind its own scheduler')
        requested, state_reads = frozenset(goals), frozenset(state_reads)
        selection = requested, complete_state, occurrences, state_reads
        # Native observation and projected queries alternate while both still
        # own their immutable formulas. Reuse those exact live selections, not
        # only the most recent one. The weak index does not extend their lives;
        # only the last view stays warm without another owner. No seed, answer
        # or private-context translation is retained here.
        if self._selection is not None:
            owner, previous, relation = self._selection
            if owner is machine:
                relation = relation if previous == selection else self._live_relations.get(selection)
                if relation is not None:
                    self._selection = machine, selection, relation
                    return relation if context is None else relation.translate(context)
        groups = {}
        for mark, _, goal in machine.occurrences:
            if goal in requested:
                groups.setdefault(goal, []).append(mark)
        if requested != groups.keys():
            raise ValueError('a requested source goal has no encoded occurrence')
        goal_names = tuple(groups)
        domain_names = tuple(mark for mark, _cause in machine.domain_checks)
        occurrence_names = {}
        if occurrences:
            for mark, key, goal in machine.occurrences:
                if goal in requested:
                    occurrence_names.setdefault(key, []).append(mark)
        outputs = None if complete_state else ('accepted', 'quiescent')
        reads = set(dict(machine.drive_inputs)) | set(state_reads)
        if not complete_state and self._source is None:
            selected = set(domain_names)
            selected.update(mark for members in groups.values() for mark in members)
            source = self.observations(selected, outputs=outputs, state_reads=reads)
            drive = source.specialize_inputs({'action': 0})
            if self._normalized:
                drive = drive.normalized()
        else:
            source, drive = self.source, self.drive
        # A false event in the unrestricted prepared transition also remains
        # false in its drive-input specialization. Do not carry these empty
        # private images through every physical phase or their conservative
        # dependency masks into the observation cone. The original source IR,
        # failure semantics and all public goal declarations stay intact.
        public_marks = (() if complete_state else
                        tuple(mark for members in groups.values() for mark in members))
        blocked = source.literal_false_hits((*domain_names, *public_marks))
        domain_names = tuple(name for name in domain_names if name not in blocked)
        groups.update((mark, (mark,)) for mark in domain_names)
        if occurrences:
            groups.update(occurrence_names)
        # Keep every public goal/occurrence key, including empty groups. A
        # query's constant-false event must not demand source-order observation
        # through every slot. Full native-state/observation views stay intact.
        groups = {name: tuple(mark for mark in members if mark not in blocked)
                  for name, members in groups.items()}
        # Owners composing another transition must retain its physical sample
        # roots too; a goal-only cone need not contain memory request pins.
        # Direct query batches compose drive/event views in one history. Closing each view
        # separately is insufficient: state produced only for the next view
        # must survive THIS view too. Close their union before any unrolling.
        if not complete_state and self.direct is not None:
            from ..symbolic.step_template import project_templates

            views = project_templates((source, drive), groups,
                                      outputs=outputs, state_reads=reads)
        else:
            views = tuple(view.project(groups, outputs=outputs, state_reads=reads)
                          for view in (source, drive))
        direct = self.direct
        if direct is not None:
            direct = direct.observing(mark for members in groups.values() for mark in members)
        relation = SourceIntervalRelation(*views, machine.drive_inputs, goal_names,
                                          tuple(occurrence_names), domain_names, direct)
        # Publish only after both views complete. A cancelled construction
        # leaves the last complete selection intact, never a partial formula.
        if self._selection is not None and self._selection[0] is not machine:
            self._live_relations.clear()
        self._live_relations[selection] = relation
        self._selection = machine, selection, relation
        return relation if context is None else relation.translate(context)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SourceIntervalRelation:
    source: StepTemplate
    drive: StepTemplate
    drive_inputs: tuple
    goal_names: tuple
    occurrence_names: tuple
    domain_names: tuple
    direct: object = None

    def initial_state(self, machine, values, *, tag='source_initial', check=None,
                      declarations=False):
        """Bind explicit full physical support, not a seed's observed state."""
        from .source_initial import bind_initial_state

        return bind_initial_state(machine, self.source, values, tag=tag, check=check,
                                  declarations=declarations)

    def translate(self, context):
        """Move this selected cone once into its query-owned context."""
        return replace(self, source=self.source.translate(context), drive=self.drive.translate(context),
                       direct=None if self.direct is None else self.direct.for_request())

    def encode_phases(self, state, phases, *, event_bounds, held_inputs=(),
                      alive=None, error=None, enabled=None, require_quiescence=True,
                      check=None, tag='source_phase'):
        return finish_construction(self.encode_phases_steps(state, phases, event_bounds=event_bounds,
            held_inputs=held_inputs, alive=alive, error=error, enabled=enabled,
            require_quiescence=require_quiescence, tag=tag), check)

    def encode_phases_steps(self, state, phases, *, event_bounds, held_inputs=(),
                            alive=None, error=None, enabled=None, require_quiescence=True,
                            tag='source_phase', previous_pins=None, bind_phase=None, phase_relations=None,
                            _facts=None):
        """Compose actual physical drives, not independently reset micro-runs.

        Each image supplies all pins. Only explicitly held inputs reuse the
        first phase's terms; other None values are independently symbolic.
        Per-phase successors/hits remain available at the Driver's sample,
        clock and clock-return boundaries. No protocol inputs or schedule are
        invented here, and no startup hits are included implicitly.
        An unfiltered history stops at its first unfinished interval. It does
        not drive the next clock edge, turn a cutoff into an error, or require
        later phases to settle in order to retain that prefix.
        """
        phases, bounds, held = tuple(phases), tuple(event_bounds), frozenset(held_inputs)
        physical = {name for name, _driven in self.drive_inputs}
        if not phases or len(phases) != len(bounds):
            raise ValueError('source phases need one explicit event bound per nonempty image')
        if not held <= physical:
            raise ValueError('only physical source inputs can be held across phases')
        relations = (self,) * len(phases) if phase_relations is None else tuple(phase_relations)
        if len(relations) != len(phases) or any(
                relation.drive_inputs != self.drive_inputs for relation in relations):
            raise ValueError('each physical phase needs a relation over the same source inputs')
        encoded = []
        for index, (pins, bound) in enumerate(zip(phases, bounds, strict=True)):
            if set(pins) != physical:
                raise ValueError('a source interval must bind every physical input')
            if encoded:
                pins = {**pins, **{name: encoded[0].inputs[name] for name in held}}
            facts = None if _facts is None else _facts.begin(state, alive, enabled)
            phase = yield from relations[index].encode_steps(state, pins, event_bound=bound, alive=alive, error=error,
                                enabled=enabled, require_quiescence=require_quiescence,
                                tag=f'{tag}{index}', previous_pins=previous_pins, _facts=facts)
            if _facts is not None:
                _facts.finish(phase, enabled, require_quiescence)
            if bind_phase is not None:
                phase = bind_phase(phase, index)
            encoded.append(phase)
            state, alive, error = phase.state, phase.alive, phase.error
            previous_pins = phase.inputs
            if not require_quiescence:
                enabled = phase.continuable
        return SourcePhaseEncoding(tuple(encoded),
            tuple(term for phase in encoded for term in phase.constraints),
            _union_images(phase.hits for phase in encoded),
            _union_images(phase.occurrences for phase in encoded))

    def settle(self, state, *, event_bound, alive=None, error=None, check=None, tag='source_settle',
               require_quiescence=True):
        """Advance existing startup/event work without first inventing a drive.

        The owner supplies the typed state, pending initial activations and
        legal initial-state predicate. This operation neither chooses register
        values nor includes these startup events in a later slot's hit image.
        """
        return self.encode(state, None, event_bound=event_bound, alive=alive, error=error,
                           check=check, tag=tag, require_quiescence=require_quiescence)

    def encode(self, state, pins, *, event_bound, alive=None, error=None, check=None, tag='source_event',
               require_quiescence=True, enabled=None):
        return finish_construction(self.encode_steps(state, pins, event_bound=event_bound,
            alive=alive, error=error, tag=tag, require_quiescence=require_quiescence,
            enabled=enabled), check)

    def encode_steps(self, state, pins, *, event_bound, alive=None, error=None, tag='source_event',
                     require_quiescence=True, enabled=None, previous_pins=None, _facts=None):
        """One external drive followed by a finite scheduler prefix.

        State and non-None pins are typed symbolic values, not sampled Python
        values. An explicit None pin is fresh; the returned input image lets a
        caller hold it across further phases. Omitted pins are not free inputs.
        A None pin image is the drive-free settle operation, not fresh pins.
        Quiet padding holds pins unchanged. On failure, retain the event prefix
        and first error, but require no quiet successor. Pass ``alive`` and
        ``error`` when composing successive intervals. ``state`` is meaningful
        only while ``alive``; a failed trace never resumes on a later drive.
        ``check`` is the owning operation's cancellation/deadline checkpoint.

        Analysis can set ``require_quiescence=False`` to retain *all* legal
        prefixes at this bound, including unfinished ones. It must establish
        feasibility and then query ``unfinished`` before claiming completeness.
        SAT disproves this bound; UNSAT is conditional on the supplied initial
        state/input domain and this scheduler's semantics, not native fidelity.
        Local search retains its existing completed-interval constraint.
        ``enabled`` gates entry into this interval independently of failure.
        A disabled interval preserves state/status and produces no events.
        """
        if self.direct is not None:
            return (yield from self.direct.encode_steps(self, state, pins, event_bound=event_bound,
                alive=alive, error=error, tag=tag, require_quiescence=require_quiescence,
                enabled=enabled, previous_pins=previous_pins, _facts=_facts))
        return (yield from self._encode_schedule_steps(state, pins, event_bound=event_bound,
            alive=alive, error=error, tag=tag, require_quiescence=require_quiescence,
            enabled=enabled, _facts=_facts))

    def _encode_schedule_steps(self, state, pins, *, event_bound, alive=None, error=None,
                               tag='source_event', require_quiescence=True, enabled=None,
                               proposed_actions=None, _facts=None):
        """Shared boundary/observation construction for scheduler instances.

        Only the private native-member compiler supplies ``proposed_actions``.
        Its inactive internal actions execute the kernel's identity branch;
        they do not assert an activation occurred. Ordinary symbolic scheduling
        and all external drives still require the original admission predicate.
        No proposal grants a complete cover of other legal scheduler orders.
        """
        import z3

        from ..symbolic.encoder import encode_concrete_value
        from ..symbolic.step_template import _bind_successors
        from .symbolic_state import assertion_conjunction, assertion_disjunction

        if proposed_actions is not None and len(proposed_actions) != event_bound:
            raise ValueError('each proposed event needs one bound action view')

        drive = dict(self.drive_inputs)
        if not set(self.source.state_names) <= set(state) <= set(self.source.procedure.state):
            raise ValueError('a source interval must bind every retained state variable')
        if pins is not None and set(pins) != set(drive):
            raise ValueError('a source interval must bind every physical input')
        if type(event_bound) is not int or event_bound < 1:
            raise ValueError('a source interval needs a positive event bound')
        if alive is None:
            alive = z3.BoolVal(True, ctx=self.source.context)
        if enabled is not None:
            # One shared control rewrite, not one whole-state rewrite per field.
            enabled = z3.simplify(enabled)
        entry_terms = None
        if not require_quiescence:
            from .source_completeness import boundary_expressions

            terms = {}
            boundary_expressions(state, terms)
            for term in (alive, error, enabled):
                boundary_expressions(term, terms)
            entry_terms = tuple(terms.values())
        prior_alive = alive
        entry_definitions = []
        if enabled is not None:
            # Name the total entry condition just like a data successor.
            # Otherwise continuable carries every earlier enable forward and
            # each later phase traverses that growing Boolean prefix again.
            alive = _define_control(assertion_conjunction((alive, enabled)),
                                    entry_definitions, tag + '_entered')
        entered = alive
        env = self.source.environment
        action_type = dict(self.source.procedure.semantics.input_types)['action']
        drive_action = encode_concrete_value(0, env['action'], 'action', kind=action_type)
        constraints, hit_terms, actions, definitions, member_layers = [], {}, [], [], []
        pre, inputs, initial_boundary = state, None, None
        for step in range(event_bound):
            yield
            boundary = dict(pre)
            external_drive = step == 0 and pins is not None
            if external_drive:
                boundary['action'] = drive_action
                boundary.update((driven, pins[name]) for name, driven in drive.items()
                                if pins[name] is not None)
            else:
                boundary.update((driven, pre[name]) for name, driven in drive.items())
            template = self.drive if external_drive else self.source
            if proposed_actions is not None:
                action, template = proposed_actions[step]
                boundary['action'] = encode_concrete_value(action, env['action'], 'action', kind=action_type)
            enc = yield from template.encode_steps(boundary, tag=f'{tag}:{step}', _facts=_facts)
            admission = (assertion_conjunction((enc.constraint,
                assertion_disjunction((z3.Not(enc.ok), enc.post['accepted'][0] == 1))))
                if proposed_actions is None or external_drive else enc.constraint)
            admitted = z3.Implies(alive, admission)
            if inputs is None:
                inputs = {name: enc.pre[driven] for name, driven in drive.items()}
                initial_boundary = enc.pre
            if error is None:
                error = z3.BitVecVal(0, enc.error.size(), ctx=self.source.context)
            actions.append(enc.pre['action'])
            _accumulate_guarded_hits(hit_terms, alive, enc.hits)
            control_definitions = []
            error, alive = _define_controls((z3.If(alive, enc.error, error),
                assertion_conjunction((alive, enc.ok))), control_definitions,
                (tag + '_error', tag + '_alive'))
            # These are total definitions even for a disabled/dead event. They
            # neither require execution nor assume success; the original event
            # constraint remains guarded by its ORIGINAL incoming liveness.
            entry = entry_definitions if step == 0 else ()
            constraints.append(assertion_conjunction((*entry, admitted, *control_definitions)))
            definitions.append((*entry, *enc.definitions, *control_definitions))
            # Successors are all defined from the preceding boundary. Control
            # coordinates follow them; preserve this construction grouping
            # instead of rediscovering it through the instantiated history DAG.
            member_layers.append(tuple(tuple(group) for group in
                                       (entry, enc.definitions, control_definitions) if group))
            # Proposed action views may omit exact identity successors. All
            # other roots, including source restrictions and events, remain.
            pre = {**pre, **enc.state_post} if proposed_actions is not None else enc.state_post
        quiescent = enc.post['quiescent'][0] == 1
        if require_quiescence:
            constraints.append(z3.Implies(alive, quiescent))
        hits = {name: assertion_disjunction(terms) if terms else z3.BoolVal(False, ctx=self.source.context)
                for name, terms in hit_terms.items()}
        if enabled is not None:
            pre = {name: _hold_value(enabled, value, initial_boundary[name]) for name, value in pre.items()}
            alive = z3.If(enabled, alive, prior_alive)
            # The transition template names its computed successors BEFORE
            # this hold mux. Leaving the mux inline grows the preceding
            # boundary into every later disabled phase. Name this exact total
            # boundary too; no state or entry condition becomes a premise.
            # Keep the equations in the existing final action frame, rather
            # than inventing another action that native membership must take.
            if not z3.is_true(enabled) and not z3.is_false(enabled):
                boundary_definitions = []
                pre = yield from _bind_successors(pre, boundary_definitions, {},
                    tag + '_boundary', wires=True, _facts=_facts)
                alive = _define_control(alive, boundary_definitions, tag + '_boundary_alive')
                constraints[len(actions) - 1] = assertion_conjunction((
                    constraints[len(actions) - 1], *boundary_definitions))
                definitions[-1] = (*definitions[-1], *boundary_definitions)
                if boundary_definitions:
                    member_layers[-1] = (*member_layers[-1], tuple(boundary_definitions))
        return SourceIntervalEncoding(tuple(constraints),
            {goal: hits[goal] for goal in self.goal_names},
            {key: hits[key] for key in self.occurrence_names}, pre, inputs,
            tuple(actions), alive, error, {name: hits[name] for name in self.domain_names},
            quiescent, entered, tuple(definitions), entry_terms,
            dict(getattr(self.source.procedure, 'state_ranges', {})) if not require_quiescence else None,
            member_layers=tuple(member_layers))
