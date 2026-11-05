"""Compare native observations with the two exact scheduling products.

An OR of several occurrences can be determined while its operands are missing
or wrong. Native replay audits each operand under the frozen catalogue, not just
that public OR. Unresolved or incomplete operands cannot become known misses.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import z3

from ..core.refusal import Refusal, Refused
from .source_relation import SourceObservation
from .symbolic_state import assertion_conjunction, assertion_disjunction, physical_bits, scalar_difference


class NativeProofCache:
    """Retain complete successful obligations, never native executions or models.

    Each lookup still receives a newly projected comparison and complete
    immutable witness. Packed ASTs retain every ordered root and defect; names
    and witness bytes are losslessly compressed, not hashed. Two independent
    bounds limit retained evidence bytes and predicate occurrences. Eviction
    only causes the ordinary proof to run again.
    """

    def __init__(self, *, max_bytes=32 << 20, max_terms=4 << 20):
        self.max_bytes, self.max_terms = max_bytes, max_terms
        self.entries = {}
        self.bytes = self.terms = 0

    def clear(self):
        self.entries.clear()
        self.bytes = self.terms = 0

    def identity(self, comparison, witness, *, check):
        from zlib import compress

        from ..core.stimulus import canonical_json

        check()
        names = compress(canonical_json((comparison.observation_scope.value,
            tuple(comparison.differences))).encode('utf-8'), 1)
        image = compress(canonical_json(witness.as_record()).encode('utf-8'), 1)
        roots = assertion_conjunction(comparison.constraints, check=check)
        defects = assertion_conjunction(comparison.differences.values(), check=check)
        count = len(comparison.constraints) + len(comparison.differences)
        check()
        return comparison.encoding, roots, defects, names, image, count

    def contains(self, key, identity, *, check):
        check()
        previous = self.entries.get(key)
        if previous is None:
            return False
        owner, roots, defects, names, image, count = identity
        old_owner, old_roots, old_defects, old_names, old_image, old_count = previous
        same = (owner is old_owner and count == old_count and names == old_names
                and image == old_image and roots.eq(old_roots) and defects.eq(old_defects))
        check()
        return same

    def remember(self, key, identity, *, check):
        # Called only after native correspondence AND environment checks pass.
        check()
        size = len(key) + len(identity[3]) + len(identity[4]) + 256
        count = identity[5]
        if size > self.max_bytes or count > self.max_terms:
            return
        if key in self.entries:
            self._remove(key)
        while self.bytes + size > self.max_bytes or self.terms + count > self.max_terms:
            self._remove(next(iter(self.entries)))
        self.entries[key] = identity
        self.bytes += size
        self.terms += count

    def _remove(self, key):
        value = self.entries.pop(key)
        self.bytes -= len(key) + len(value[3]) + len(value[4]) + 256
        self.terms -= value[5]


class NativeEvidence:
    """Session-local native evidence, separate from goal-query preparation.

    Projection proofs belong to their complete encoding. Witness reuse also
    requires the exact request/domain owners and every byte of the native
    record. Neither sampled hits nor a query's SAT model supplies evidence.
    """

    def __init__(self):
        self.proofs = NativeProofCache()
        self.clear()

    def clear(self):
        self.release_projection()
        self.release_witness()

    def release_projection(self):
        """Release old formula owners before constructing another history."""
        self.encoding = self.relation = self.trace = self.agreement = None
        self.proofs.clear()

    def release_witness(self):
        """End one query's native confirmation without dropping reusable syntax."""
        self.member_proof = self.member_miss = self.witness = None

    def audit_witness(self, request, domain, scope, key, trace, *, build, check):
        """Reuse a completed audit only for an identical complete record."""
        from zlib import compress
        from ..core.stimulus import canonical_json

        check()
        image = compress(canonical_json(trace.as_dict()).encode('utf-8'), 1)
        check()
        retained = self.witness
        if (retained is not None and retained[0] is request
                and retained[1] is domain and retained[2] is scope
                and retained[3] == key and retained[4] == image):
            check()
            return retained[5]
        self.witness = None
        witness = build()
        check()  # Cancellation or failure cannot publish reusable evidence.
        self.witness = request, domain, scope, key, image, witness
        return witness

    def propose_member(self, source, request, domain, scope, rows, trace, *,
                       key, remember, solve, check):
        """Try one legal history before constructing every source ordering.

        This is an existential optimization of native admission only. No
        unsuccessful proposal is an inconsistency or an unreachable goal;
        the ordinary complete-cover path remains the sole negative authority.
        """
        from ..core.audit import AuditedWitness
        from ..core.analysis import audit_environment_assumptions
        from ..core.failure import RunFailure
        from ..core.query import SolverAnswer
        from .source_member import source_member_hint, propose_native_phase_order
        from .source_trace import read_source_history

        compiler = source.template.direct
        if compiler is None or not compiler.covers_native_orders:
            return None
        terminal = type(trace) is RunFailure
        retained, self.member_proof = self.member_proof, None

        def finish(relation, comparison):
            # Terminal histories have no completed trace. Their actual prefix
            # and environment predicates are checked by the comparison below.
            audit = (None if terminal else
                audit_environment_assumptions(source.contract, trace.stimulus, trace))
            if audit is not None:
                raise Refused(Refusal('rtl-source-witness-environment-invalid',
                    'the native witness is outside the declared task environment', audit=audit.as_dict()))
            check()
            witness = AuditedWitness(domain=domain.digest(), inputs=rows, **comparison.native_image)
            check()
            remember(key)
            self.member_proof = compiler, request, domain, scope, key, relation, comparison
            return witness

        if retained is not None and retained[0] is compiler and retained[1] is request \
                and retained[2] is domain and retained[3] is scope and retained[4] == key:
            # The replay is still new. Rebuild its COMPLETE observation
            # obligation and reuse only an exactly equal successful proof.
            # Neither a trace hash, final output nor a saved model suffices.
            relation, previous = retained[5:]
            comparison = compare_native_history(relation, request, previous.encoding, rows, trace,
                source.frontend, observation=source.observation, check=check,
                observation_scope=scope)
            if comparison.same_obligation(previous, check=check):
                return finish(relation, comparison)
        # Release the prior construction before preparing another request or
        # changed observation. Failed/cancelled proposals never enter this slot.
        retained = relation = previous = comparison = None
        roots = frozenset(i for i, active in enumerate(compiler.machine.startup) if active)
        try:
            compiler.plan(roots, all_active=True)
            return None  # The existing certified path is already compact.
        except Refused as exc:
            if exc.refusal.kind not in {
                    'rtl-direct-coactive-dependency', 'rtl-direct-event-plan-unbounded'}:
                return None
        environment = (*request.constraints, *request.fix(rows))
        candidate = solve('native-order-environment', environment)
        if candidate.answer is not SolverAnswer.SAT or candidate.model is None:
            return None
        repaired = {}
        while True:
            check()
            try:
                relation, bound_request, encoded = source.bind_request(domain.bound,
                    native_history=(rows, trace), native_member=True,
                    native_phase_actions=tuple(repaired.items()))
            except Refused:
                return None
            assert bound_request is request
            comparison = compare_native_history(relation, request, encoded, rows, trace,
                source.frontend, observation=source.observation, check=check,
                observation_scope=scope)
            defects = z3.Or(encoded.unfinished, *encoded.domain_faults.values(),
                *encoded.source.memory.conflicts, *encoded.environment_assumptions.values())
            constraints = (*comparison.constraints, z3.Not(defects))
            # Both the default and repaired orders are concrete proposals.
            # Complete their frames and check every original root; do not
            # enumerate a large symbolic action cross-product for admission.
            model = source_member_hint(candidate.model, (encoded,), constraints, check=check)
            if model is None:
                return None
            if z3.is_false(model.eval(comparison.different)):
                break  # Every original native observation still must agree.
            if terminal:
                return None  # Never fabricate missing samples for a phase repair.
            phase = comparison.first_missed_phase(model, check=check)
            if phase is None or phase in repaired:
                return None  # No local explanation; retain complete fallback.
            native = read_source_history(trace.stimulus, trace, request.codec.initial,
                source.frontend, observation=source.observation)
            prefix = scope is SourceObservation.GOAL_PREFIX
            prior_hits = set().union(*(row[1].goals for row in native.phases[:phase])) if prefix else set()
            expected = prior_hits | set(native.phases[phase][1].goals)
            phases = (encoded.startup, *(part for slot in encoded.source.slots for part in slot.phases))
            actions = propose_native_phase_order(compiler.machine, phases, phase, model,
                expected, prior_hits, check=check)
            if actions is None:
                return None
            repaired[phase] = actions
            # Do not retain the previous whole AST while rebuilding a repair.
            relation = encoded = comparison = constraints = model = phases = defects = native = None
        return finish(relation, comparison)


def native_protocol_history(contract, stimulus, trace, *, check=None, pending_sample=None):
    """Reconstruct protocol state with the actual public driver, not its record.

    Only the recorded pins are inputs. Queue advancement, framing and adapter
    state are recomputed; recorded transfers are deliberately not trusted here.
    The caller compares them, after validating the trace/request identity.
    No RTL execution, solver, score or source-event authority lives here.
    """
    from ..core.atomic import AtomicDriver
    from ..core.driver import Driver
    from ..core.stimulus import Completion, uses_global_events

    atomic = uses_global_events(contract, stimulus)
    driver = (AtomicDriver if atomic else Driver)(contract, stimulus)

    def image():
        return {
            'adapters': ({name: dict(state) for name, state in driver.states.items()} if atomic else
                         {driver.clock.name if driver.clock is not None else None: dict(driver.adapter_state)}),
            'pointers': dict(driver.pointers), 'released': dict(driver.released),
            'framing': dict(driver._input_domain.states),
        }

    states = [image()]
    if trace.completion is not Completion.INCOMPLETE and len(trace.records) != len(stimulus.slots):
        raise Refused(Refusal('rtl-source-protocol-horizon-mismatch',
            'native protocol reconstruction needs every requested slot'))
    samples = [dict(record.outputs) for record in trace.records]
    if pending_sample is not None:
        samples.append(pending_sample)
    for index, pins in enumerate(samples):
        if check is not None:
            check()
        if any(type(value) is not int or value < 0 for value in pins.values()):
            raise Refused(Refusal('rtl-source-protocol-pin-unreadable',
                'protocol reconstruction requires readable physical pin patterns', slot=index))
        if atomic:
            driver.observe(index, driver.drive(index), pins)
        else:
            driver.controls(index)
            driver.observe(index, driver.presentations(index), pins)
        states.append(image())
    return tuple(states), tuple(driver.records)


@dataclass(frozen=True, slots=True)
class SourceNativeComparison:
    """Fixed-request native correspondence, not whole-domain admission.

    The enclosing request owns solving, deadlines and receipts. Both modes
    check complete native histories, not just matching final outputs. The
    exact-order mode additionally excludes disagreeing source histories;
    native-cover mode allows them but confirms every positive native claim.
    """

    encoding: object
    constraints: tuple
    differences: dict
    native_image: dict
    observation_scope: SourceObservation = SourceObservation.PHASE_EVENTS
    member_observations: tuple = ()

    @property
    def different(self):
        return assertion_disjunction((z3.BoolVal(False, ctx=self.encoding.alive.ctx),
                                      *self.differences.values()))

    def first_missed_phase(self, model, *, check):
        """Locate a proposal repair, never infer a fact about another history.

        In prefix mode one missed event persists in every later image. Repair
        its first differing cut, not all those cumulative copies. Unknown
        evaluations cannot identify a cut and retain the caller's fallback.
        """
        for index, observations in enumerate(self.member_observations):
            for term in observations:
                check()
                value = model.eval(term)
                if z3.is_false(value):
                    return index
                if not z3.is_true(value):
                    return None
        return None

    def member_prefixes(self, environment, *, check):
        """Propose native-compatible orders using already constructed frames.

        A prefix can seed a complete member, never certify one or establish
        UNSAT. The caller still validates every original native obligation.
        """
        from .source_member import source_query_prefixes

        phases = (self.encoding.startup,
            *(phase for slot in self.encoding.source.slots for phase in slot.phases))
        if len(phases) != len(self.member_observations):
            return
        cuts = {}
        for phase, observations in zip(phases, self.member_observations, strict=True):
            check()
            # Startup can contain multiple event regions. Its observation
            # belongs after the last, not after a partial startup region.
            cut = getattr(phase, 'phases', (phase,))[-1]
            cuts[id(cut)] = observations
        for roots, _hits, _faults in source_query_prefixes(self.encoding, environment,
                check=check, cut_constraints=cuts):
            yield roots

    def same_obligation(self, other, *, check):
        """Exact proof reuse after a fresh projection, never output-only reuse.

        The complete encoding, fixed-request roots, named observation defects
        and witness image must all match. No hash, partial hit set, final state,
        or score can stand in for this comparison. The owner may retain only a
        successfully proved obligation within its immutable bound session.
        """
        check()
        if (type(other) is not SourceNativeComparison or self.encoding is not other.encoding
                or self.observation_scope is not other.observation_scope
                or self.differences.keys() != other.differences.keys()
                or len(self.constraints) != len(other.constraints)
                or self.native_image != other.native_image):
            return False
        for left, right in zip(self.constraints, other.constraints, strict=True):
            check()
            if not left.eq(right):
                return False
        for name, left in self.differences.items():
            check()
            if not left.eq(other.differences[name]):
                return False
        check()
        return True

    def _certificate(self, solve, defects, check, facts=None):
        """Prove the source image before reducing its deterministic monitors.

        Omitting monitor equations only relaxes the first proof. Only UNSAT
        lets its observed equalities become premises of the second proof.
        They reduce the original defect predicate, never the source domain;
        unsuccessful reductions retain the untouched full-query fallback.
        No native value is a premise until its correspondence is proved.
        """
        from ..symbolic.exact_solve import ExactCheckResult
        from ..core.query import SolverAnswer
        from .monitor_template import monitor_frames, reduce_monitor_defect
        from .source_member import reduce_fixed_source_defect
        from .symbolic_state import FixedBindings, _literal, _scalar_replacements

        original = (*self.constraints, defects)
        if facts is not None:
            check()
            reduced = z3.simplify(facts.substitute(defects))
            check()
            if z3.is_false(reduced):
                answer = solve('native-source-certificate', (reduced,))
                if type(answer) is not ExactCheckResult:
                    raise TypeError('native source audit requires the exact solver result type')
                check()
                if answer.answer is SolverAnswer.UNSAT:
                    return solve('native-certificate', (reduced,))
        reduced = reduce_fixed_source_defect(self.constraints, self.encoding, defects, check=check)
        if z3.is_false(reduced):
            return solve('native-certificate', (reduced,))
        definitions = getattr(self.encoding, 'monitor_constraints', ())
        if not definitions:
            return solve('native-certificate', original)
        check()
        frames = monitor_frames(self.encoding, check=check)
        omitted = frames.omitted
        roots = tuple(term for term in self.constraints if term.get_id() not in omitted)
        observed = tuple(term for name, term in self.differences.items()
                         if not name.startswith('monitor[') and '.monitor.' not in name)
        startup = getattr(self.encoding, 'startup', None)
        intervals = ([] if startup is None else list(getattr(startup, 'phases', (startup,))))
        intervals.extend(phase for slot in getattr(self.encoding.source, 'slots', ())
                         for phase in slot.phases)
        certified = (self.encoding.unfinished,
            *self.encoding.source.domain_faults.values(),
            *getattr(startup, 'domain_faults', {}).values(),
            *self.encoding.source.memory.conflicts, *observed,
            # Monitors distinguish a completed sample from a completed slot.
            # Prove both guards explicitly; never infer them from a trace's
            # presence or assume them merely to shrink the native query.
            *(z3.Not(phase.continuable) for phase in intervals))
        answer = solve('native-source-certificate', (*roots, z3.Or(*certified)))
        if type(answer) is not ExactCheckResult:
            raise TypeError('native source audit requires the exact solver result type')
        check()
        if answer.answer is SolverAnswer.UNSAT:
            # Only unconditional scalar assignments are needed from the request.
            # Normalizing compound source equalities one by one would revisit
            # the complete symbolic prefix for every recorded field.
            assignments = _scalar_replacements(roots)
            fixed = FixedBindings(roots, assignments, assignments)
            known = list(fixed.literal_bindings)
            false = z3.BoolVal(False, ctx=defects.ctx)
            for term in certified:
                check()
                known.append((term, false))
                if z3.is_not(term):
                    known.append((term.arg(0), z3.BoolVal(True, ctx=term.ctx)))
                # Native scalar comparisons are term != literal. Keep the
                # original DAG key; simplifying each history-valued term here
                # repeats the entire prefix once per observed field.
                if z3.is_distinct(term) and term.num_args() == 2:
                    left, right = term.children()
                    if _literal(left):
                        left, right = right, left
                    if _literal(right):
                        known.append((left, right))
            bindings = FixedBindings(fixed.equations, fixed.replacements, tuple(known))
            reduced = reduce_monitor_defect(defects, definitions, bindings,
                check=check, _frames=frames)
            check()
            if z3.is_false(reduced):
                return solve('native-certificate', (reduced,))
            # Unknown coordinates remain symbolic. Keep every original
            # equation; only the target uses already-proved substitutions.
            return solve('native-certificate', (*self.constraints, reduced))
        return solve('native-certificate', original)

    def admit_member(self, solve, *, check=lambda: None):
        """Admit an actual native execution into a conservative source cover.

        Unlike order equivalence, this asks for one complete, defined source
        history matching EVERY recorded observation. It never establishes a
        goal hit without that native record and cannot turn source SAT into
        native reachability. Other source orders may legitimately disagree.
        """
        from ..symbolic.exact_solve import ExactCheckResult
        from ..core.audit import AuditCheck
        from ..core.query import SolverAnswer

        check()
        defects = z3.Or(self.encoding.unfinished, *self.encoding.domain_faults.values(),
                        *self.encoding.source.memory.conflicts, self.different)
        answer = solve('native-cover-membership', (*self.constraints, z3.Not(defects)))
        if type(answer) is not ExactCheckResult:
            raise TypeError('native membership requires the exact solver result type')
        check()
        if answer.answer is SolverAnswer.UNKNOWN:
            return AuditCheck.refused('rtl-source-native-membership-unresolved', answer.reason)
        if answer.answer is SolverAnswer.UNSAT:
            return AuditCheck.inconsistent('the native history is outside the source cover')
        return AuditCheck.agreed()

    def audit(self, solve, *, joint=True, check=lambda: None, proved_literals=None):
        """Prove all obligations jointly, retaining individual fault diagnosis.

        A SAT member of C and UNSAT of C & (unfinished | undefined | conflict
        | different) prove every universal obligation. Nonemptiness then also
        proves native membership C & !different. This is the same full domain,
        not an assumption that the observed run is a member. An inconclusive
        joint check falls back under the caller's unchanged remaining budget.
        """
        from ..symbolic.exact_solve import ExactCheckResult
        from ..core.audit import AuditCheck
        from ..core.query import SolverAnswer

        check()
        false = z3.BoolVal(False, ctx=self.encoding.alive.ctx)
        different = self.different
        obligations = (
            ('feasibility', None, SolverAnswer.SAT),
            ('completeness', self.encoding.unfinished, SolverAnswer.UNSAT),
            ('definedness', z3.Or(false, *self.encoding.domain_faults.values()), SolverAnswer.UNSAT),
            ('memory', z3.Or(false, *self.encoding.source.memory.conflicts), SolverAnswer.UNSAT),
            ('membership', z3.Not(different), SolverAnswer.SAT),
            ('agreement', different, SolverAnswer.UNSAT),
        )
        model = None
        for phase, predicate, required in obligations:
            check()
            assertions = self.constraints if predicate is None else (*self.constraints, predicate)
            answer = solve(phase, assertions)
            if type(answer) is not ExactCheckResult:
                raise TypeError('native source audit requires the exact solver result type')
            if answer.answer is SolverAnswer.UNKNOWN:
                return AuditCheck.refused('rtl-source-native-check-unresolved', f'{phase}: {answer.reason}')
            if phase == 'feasibility':
                model = answer.model
            if answer.answer is not required:
                if phase == 'membership':
                    # Reuse the existing feasible model for diagnostics only;
                    # membership UNSAT, not this list, establishes inconsistency.
                    fields = tuple(name for name, term in self.differences.items()
                                   if model is not None and z3.is_true(model.eval(term)))
                    return AuditCheck.inconsistent((
                        'the native history is outside the source relation', *fields))
                return AuditCheck.refused(f'rtl-source-native-{phase}',
                    f'the fixed native request did not satisfy the {phase} obligation')
            if phase == 'feasibility' and joint:
                defects = z3.Or(*(predicate for _, predicate, required in obligations
                                  if required is SolverAnswer.UNSAT))
                facts = None if proved_literals is None else proved_literals(model)
                combined = self._certificate(solve, defects, check, facts)
                if type(combined) is not ExactCheckResult:
                    raise TypeError('native source audit requires the exact solver result type')
                if combined.answer is SolverAnswer.UNSAT:
                    return AuditCheck.agreed()
        return AuditCheck.agreed()


class NativeHistoryBindings:
    """Factor ONE existential native-membership obligation at recorded cuts.

    Recorded bits replace their terms only alongside the original equalities.
    Private storage, unobserved carrier bits, events and faults stay symbolic.
    This object is never attached to a universal query or shared template.
    """

    def __init__(self, machine, request, rows, trace, catalogue, *, observation=None, check=None,
                 cover_relation=None, cover_phases=(), phase_relations=None):
        from .source_initial import source_storage_leaves
        from .source_trace import read_source_history

        self.check = check or (lambda: None)
        self.check()
        request.fix(rows)
        request.codec.initial.decode(rows).check_trace(trace)
        self.native = read_source_history(trace.stimulus, trace, request.codec.initial,
                                         catalogue, observation=observation)
        self.outputs = tuple(dict(record.outputs) for record in trace.records)
        self.leaves = tuple(source_storage_leaves(machine.program, check=self.check))
        self.ports = tuple((field.port, (name,), machine.program.kinds[name].width)
                          for name, field in machine.program.fields.items() if field.port is not None)
        self.cover_relation, self.cover_phases = cover_relation, frozenset(cover_phases)
        self.phase_relations = dict(phase_relations or {})
        if (self.cover_phases and cover_relation is None) or any(
                type(index) is not int or not 0 <= index < len(self.native.phases)
                for index in (*self.cover_phases, *self.phase_relations)) \
                or self.cover_phases.intersection(self.phase_relations):
            raise ValueError('native cover repairs must name observed physical phases')

    def phase_relation(self, relation, slot=None, position=None):
        # Native source history has startup, then control/clock/return cuts.
        index = 0 if slot is None else 1 + 3 * slot + position
        if index in getattr(self, 'phase_relations', {}):
            return self.phase_relations[index]
        return self.cover_relation if index in self.cover_phases else relation

    def phase_event_bound(self, bound, slot=None, position=None):
        """A short existential order proposal, never a completeness bound.

        Try one action-table-sized prefix at a repaired cut. Every selected
        interval must still settle and match its complete native observation;
        an insufficient prefix retains the caller's full-cover fallback.
        This changes neither the number of request inputs nor universal queries.
        """
        index = 0 if slot is None else 1 + 3 * slot + position
        if index in self.cover_phases:
            return min(bound, len(self.cover_relation.direct.machine.actions))
        return bound

    def samples(self, widths, context):
        def term(row, pin, width):
            value = row.get(pin)
            if type(value) is not int or not 0 <= value < 1 << width:
                raise Refused(Refusal('rtl-source-native-pin-unreadable',
                    'native membership needs every in-width protocol sample', field=pin))
            return (z3.BoolVal(bool(value), ctx=context) if width == 1
                    else z3.BitVecVal(value, width, ctx=context))
        return tuple({pin: term(row, pin, width) for pin, width in widths.items()}
                     for row in self.outputs)

    def bind_phase(self, phase, slot=None, position=None):
        from dataclasses import replace

        self.check()
        observed = (self.native.states[0] if slot is None else self.outputs[slot] if position == 0
                    else self.native.states[slot + 1] if position == 2 else {})
        values = {path: (width, observed[name]) for name, path, width in (*self.leaves, *self.ports)
                  if name in observed}
        constraints = [phase.entered, phase.alive, phase.error == 0, phase.quiescent]

        # Do not materialize unobserved private arrays.
        fields = {path[0] for path in values}
        state = {name: self._bind_value(value, (name,), values, constraints, phase.alive.ctx)
                 if name in fields else value
                 for name, value in phase.state.items()}
        # Preserve the existing action/frame grouping so forward construction
        # can try a member. EVERY observation equality remains a required root.
        roots = (*phase.constraints[:-1], z3.And(*phase.constraints[-1:], *constraints))
        return replace(phase, state=state, constraints=roots,
            entered=z3.BoolVal(True, ctx=phase.alive.ctx), alive=z3.BoolVal(True, ctx=phase.alive.ctx),
            quiescent=z3.BoolVal(True, ctx=phase.alive.ctx),
            error=z3.BitVecVal(0, phase.error.size(), ctx=phase.error.ctx))

    def _bind_value(self, value, path, values, constraints, context):
        # A recursive local closure would keep this phase's entire native AST
        # arena alive until cyclic GC. Method recursion releases it promptly.
        from ..symbolic.functional_array import FunctionalArray

        self.check()
        if isinstance(value, FunctionalArray):
            bound = self._bind_array(value, path, values, constraints, context)
            if bound is not None:
                return bound
            return FunctionalArray.from_cells([
                self._bind_value(value.select(z3.BitVecVal(i, 32, ctx=context)),
                                 (*path, i), values, constraints, context)
                for i in range(value.length)])
        if isinstance(value, (tuple, list)):
            return type(value)(self._bind_value(item, (*path, i), values, constraints, context)
                               for i, item in enumerate(value))
        if path not in values:
            return value
        width, bits = values[path]
        if type(bits) is not int or not 0 <= bits < 1 << width:
            raise Refused(Refusal('rtl-source-native-value-unreadable',
                'a native boundary must retain its physical width', field=list(path),
                physical_width=width, encoded_width=value.size(),
                observed_type=type(bits).__name__,
                observed_bits=bits.bit_length() if type(bits) is int else None,
                observed_negative=bits < 0 if type(bits) is int else None))
        literal = z3.BitVecVal(bits, width, ctx=value.ctx)
        constraints.append(physical_bits(value, width) == literal)
        if value.size() < width:
            # Normalization may narrow a signed mathematical IR carrier (a
            # 32-bit counter holding literal zero can have a one-bit carrier).
            # Compare its sign extension to ALL observed physical bits first.
            # A nonrepresentable observation makes this obligation UNSAT; low
            # bits alone must never admit it. Keep the original carrier sort.
            return z3.BitVecVal(bits, value.size(), ctx=value.ctx)
        # The observer does not own extra mathematical carrier bits.
        return (literal if value.size() == width else
                z3.simplify(z3.Concat(z3.Extract(value.size() - 1, width, value), literal)))

    def _bind_array(self, value, path, values, constraints, context):
        """Bind a complete physical vector without re-reading history per cell.

        The packed equality is exactly the conjunction of scalar equalities.
        A wider carrier retains every unobserved bit; a narrower carrier must
        still match each sign-extended physical word, not just its low bits.
        Partial or heterogeneous observations use the scalar path unchanged.
        """
        from ..symbolic.functional_array import FunctionalArray, _packed_literal, _repeat_block

        first = values.get((*path, 0))
        if first is None:
            return None
        width, _ = first
        carrier = value.element_width
        words = []
        low_mask = (1 << min(width, carrier)) - 1
        for i in range(value.length):
            self.check()
            observed = values.get((*path, i))
            if observed is None or observed[0] != width:
                return None
            bits = observed[1]
            if type(bits) is not int or not 0 <= bits < 1 << width:
                raise Refused(Refusal('rtl-source-native-value-unreadable',
                    'a native boundary must retain its physical width', field=[*path, i],
                    physical_width=width, encoded_width=carrier,
                    observed_type=type(bits).__name__,
                    observed_bits=bits.bit_length() if type(bits) is int else None,
                    observed_negative=bits < 0 if type(bits) is int else None))
            words.append(bits)
        literal = z3.simplify(_packed_literal(
            (bits & low_mask for bits in words), carrier, context, check=self.check))
        if carrier > width:
            image = value._packed_value(carrier, context)
            observed_mask = _repeat_block(z3.BitVecVal(low_mask, carrier, ctx=context), value.length)
            constraints.append(image & observed_mask == literal)
            successor = (image & ~observed_mask) | literal
        else:
            image = value._packed_value(width, context)
            observed = (literal if carrier == width else
                        _packed_literal(words, width, context, check=self.check))
            constraints.append(image == observed)
            successor = literal
        return FunctionalArray.from_packed(successor, value.length, carrier)

    def bind_startup(self, encoded):
        from .source_relation import SourcePhaseEncoding, _union_images

        if type(encoded) is not SourcePhaseEncoding:
            return self.bind_phase(encoded)
        phases = (*encoded.phases[:-1], self.bind_phase(encoded.phases[-1]))
        return SourcePhaseEncoding(phases, tuple(term for phase in phases for term in phase.constraints),
            _union_images(phase.hits for phase in phases),
            _union_images(phase.occurrences for phase in phases))


class _PhysicalStateProjection:
    """Compiler-owned leaf paths, with exact packed checks for complete arrays.

    A vector equality is the conjunction of its physical cell equalities.
    Partial observations still use individual leaves; no observation is added
    or omitted. Only immutable path/type metadata is shared across boundaries.
    """

    def __init__(self, program, *, check):
        from .source_handles import physical_name
        from .source_values import ArrayType

        self.leaves, self.arrays, self.check = {}, [], check

        def visit(native, path, kind):
            check()
            if isinstance(kind, ArrayType):
                direction = 1 if kind.left <= kind.right else -1
                names = []
                for index in range(kind.cells):
                    child = (*native, kind.left + direction * index)
                    visit(child, (*path, index), kind.element)
                    names.append(physical_name(child))
                if not isinstance(kind.element, ArrayType):
                    self.arrays.append((path, kind.element.width, tuple(names), physical_name(native)))
            else:
                name = physical_name(native)
                if name in self.leaves:
                    raise Refused(Refusal('rtl-source-native-storage-ambiguous',
                        'distinct source fields share one observation path', field=name))
                self.leaves[name] = path, kind.width

        for name, field in program.fields.items():
            if field.observation is not None:
                native = field.observation
                visit((native,) if isinstance(native, str) else native, (name,), program.kinds[name])
            if field.port is not None:
                self.leaves[field.port] = (name,), program.kinds[name].width

    @staticmethod
    def _value(state, path):
        from ..symbolic.functional_array import FunctionalArray, _term_context

        value = state[path[0]]
        for index in path[1:]:
            value = (value.select(z3.BitVecVal(index, 32, ctx=_term_context(value)))
                     if isinstance(value, FunctionalArray) else value[index])
        return value

    def term(self, state, name):
        self.check()
        if name not in self.leaves:
            raise Refused(Refusal('rtl-source-native-storage-unbound',
                'a native register has no compiler-owned source field', field=name))
        path, width = self.leaves[name]
        return physical_bits(self._value(state, path), width)

    def compare(self, state, recorded, label, compare, differences, context):
        from ..symbolic.functional_array import FunctionalArray, _packed_literal
        from ..core.construction_checkpoint import finish_construction

        remaining = set(recorded)
        for path, width, names, native in self.arrays:
            self.check()
            if not all(name in remaining and self.leaves[name] == ((*path, index), width)
                       for index, name in enumerate(names)):
                continue
            value = self._value(state, path)
            if not isinstance(value, FunctionalArray) or value.length != len(names):
                continue
            words = []
            for name in names:
                self.check()
                bits = recorded[name]
                if type(bits) is not int or not 0 <= bits < 1 << width:
                    raise Refused(Refusal('rtl-source-native-value-unreadable',
                        'a native boundary must retain its physical width', field=name))
                words.append(bits)
            image = finish_construction(value._packed_value_steps(width, context), self.check)
            literal = _packed_literal(words, width, context, check=self.check)
            self.check()
            key = f'{label}.array:{native}'
            while key in differences or key[len(label) + 1:] in recorded:
                key += '#'
            # Keep the shared array circuit intact. Eager simplification of
            # this wide equality can expand each observed cell's update DAG;
            # the enclosing original obligation already evaluates/solves it.
            differences[key] = image != literal
            self.check()
            remaining.difference_update(names)
        for name, value in recorded.items():
            if name in remaining:
                compare(self.term(state, name), value, f'{label}.{name}')


def _event_requirements(terms, observed, seen=None):
    """Exact prefix agreement needs only the first-hit boundary of each bit.

    Before that boundary every phase bit is false; at it the phase bit is
    true. Later bits cannot change the cumulative OR. This is an equivalent
    conjunction, NOT an assumption from native execution. Never-hit bits
    still require every phase. ``None`` retains ordinary per-phase agreement.
    """
    for name, term in terms.items():
        if seen is None or name not in seen:
            yield name, term, name in observed
    if seen is not None:
        seen.update(observed)


def compare_native_history(relation, request, encoded, rows, trace, catalogue, *,
                           observation=None, check=None,
                           observation_scope=SourceObservation.PHASE_EVENTS):
    """Project actual startup/phase/register/pin/transfer/memory evidence.

    This is a source/native witness obligation. Whole-domain independence,
    catalogue admission and raw-record admission remain separate obligations;
    success here alone must never grant an exact-analysis capability.
    """
    from ..core.failure import RunFailure
    from ..core.memory import MemoryEnvironmentState
    from ..core.memory_trace import read_memory_successor
    from ..core.monitor import INPUT_COMPLETION, _input_completion
    from ..core.physical_value import pack_value
    from ..core.ready_valid_product import ConcreteAlgebra
    from ..core.record_value import same_record_value
    from ..core.stimulus import EventSlot, contract_of_domain
    from .analysis_query import source_domain_failure
    from .source_trace import read_failed_source_history, read_source_history

    relation.require_observation_scope(observation_scope)
    if not relation.complete_state or encoded.startup is None:
        raise ValueError('native whole-history audit requires complete state and startup')
    stimulus = request.codec.decode(rows)
    terminal = type(trace) is RunFailure
    if terminal:
        trace, native = read_failed_source_history(trace, stimulus, request.codec.initial,
                                                   catalogue, observation=observation)
    else:
        native = read_source_history(trace.stimulus, trace, request.codec.initial, catalogue,
                                     observation=observation)
    if contract_of_domain(trace.domain) != relation.contract.digest:
        raise Refused(Refusal('rtl-source-native-contract-mismatch',
            'the native history and source relation must name the same task contract'))
    fixed = request.fix(rows)
    choice = request.codec.initial.decode(rows)
    choice.check_trace(trace)
    # The atomic codec names a GlobalSchedule; an ordinary single-clock record
    # can carry the same actions as implicit slots. Compare complete requests,
    # not those two representations. Encoding still validates queues, timing,
    # controls, memory and startup against the frozen domain.
    if stimulus != trace.stimulus:
        actual = trace.stimulus
        equivalent = (request.atomic and actual.clock_schedule is None
            and all(getattr(actual, name) == getattr(stimulus, name)
                    for name in ('initial_state', 'memory_policy', 'replay_policy'))
            and request.codec.encode(actual, initial_state=choice) == rows)
        if not equivalent:
            raise Refused(Refusal('rtl-source-native-request-mismatch',
                'the native trace and symbolic request encode different stimuli'))
    program, differences, literals = relation.machine.program, {}, {}
    comparisons = {}

    def compare(term, value, name, *, agreement=False):
        if check is not None:
            check()
        if not isinstance(term, z3.ExprRef):
            raise TypeError('native audit predicates must be symbolic terms')
        if z3.is_bool(term):
            if type(value) not in (bool, int) or value not in (0, 1):
                raise Refused(Refusal('rtl-source-native-value-unreadable',
                    'a native Boolean observation must be a readable bit', field=name))
            value = bool(value)
        key = ((term.ctx, term.get_id(), type(value), value)
               if type(value) in (bool, int) else None)
        previous = comparisons.get(key) if key is not None else None
        if previous is None:
            difference = scalar_difference(term, value, literals)
            # Retain the term as well as its image: native IDs must not be
            # recycled within this projection. Identical observations share
            # construction only; every original field/phase label survives.
            previous = (term, difference, None)
            if key is not None:
                comparisons[key] = previous
        else:
            difference = previous[1]
        differences[name] = difference
        if agreement:
            equal = previous[2]
            if equal is None:
                equal = z3.Not(difference)
                if key is not None:
                    comparisons[key] = (term, difference, equal)
            return equal
        return difference

    def tree(terms, values, name):
        if isinstance(terms, Mapping):
            if not isinstance(values, Mapping) or terms.keys() != values.keys():
                raise Refused(Refusal('rtl-source-native-product-shape',
                    'symbolic and native product states have different fields', field=name))
            for key, term in terms.items():
                tree(term, values[key], f'{name}.{key}')
        elif isinstance(terms, (tuple, list)):
            if not isinstance(values, (tuple, list)) or len(terms) != len(values):
                raise Refused(Refusal('rtl-source-native-product-shape',
                    'symbolic and native product states have different lengths', field=name))
            for i, (term, value) in enumerate(zip(terms, values, strict=True)):
                tree(term, value, f'{name}[{i}]')
        elif isinstance(terms, z3.ExprRef):
            compare(terms, values, name)
        else:
            differences[name] = z3.BoolVal(terms != values, ctx=encoded.alive.ctx)

    projection = _PhysicalStateProjection(program, check=check or (lambda: None))

    def registers(state, recorded, label):
        projection.compare(state, recorded, label, compare, differences, encoded.alive.ctx)

    phases = (encoded.startup, *(phase for slot in encoded.source.slots for phase in slot.phases))
    native_images, seen_goals, seen_occurrences = [], set(), set()
    member_observations = []
    expected = set(catalogue)
    occurrences = {key for key, _ in catalogue.occurrences}
    for i, phase in enumerate(phases):
        recorded = i < len(native.phases)
        kind, observed = (native.phases[i] if recorded else
                          (('control', 'clock', 'advance')[(i-1) % 3], None))
        label = f'phase[{i}].{kind}'
        if set(phase.hits) != expected or set(phase.occurrences) != occurrences:
            raise Refused(Refusal('rtl-source-native-catalogue-incomplete',
                'each source phase must encode every frozen goal and occurrence', phase=i))
        # Collect only this phase's predicates as they are constructed. Slicing
        # a copy of all prior differences here costs O(phases**2 * events).
        # The complete named differences remain the authoritative audit image.
        stopped = terminal and i >= len(native.phases)-1
        observations = [compare(phase.alive, not stopped, f'{label}.alive', agreement=True),
                        compare(phase.error == 0, not stopped, f'{label}.error', agreement=True)]
        prefix = observation_scope is SourceObservation.GOAL_PREFIX
        for goal, term, hit in _event_requirements(
                phase.hits, observed.goals if recorded else (), seen_goals if prefix else None):
            observations.append(compare(term, hit, f'{label}.hit:{goal}', agreement=True))
        for key, term, hit in _event_requirements(
                phase.occurrences, observed.occurrences if recorded else (), seen_occurrences if prefix else None):
            observations.append(compare(term, hit, f'{label}.occurrence:{key}', agreement=True))
        native_images.append((kind,
            frozenset(seen_goals) if prefix else observed.goals if recorded else frozenset(),
            frozenset(seen_occurrences) if prefix else observed.occurrences if recorded else frozenset()))
        member_observations.append(tuple(observations))
    # Environment faults are not artifact failures. Keep them in the same full
    # membership obligation, including the final sampled-but-uncompleted slot.
    tree(encoded.environment_assumptions,
         dict.fromkeys(encoded.environment_assumptions, False), 'environment')
    terminal_state = {'terminal.error': native.terminal_error,
                      'terminal.phase': len(native.phases)-1}
    if terminal and len(native.phases) == 1:
        return SourceNativeComparison(encoded, (*encoded.constraints, *fixed), differences,
            {'states': [terminal_state] * (len(rows)+1), 'outputs': [()] * len(rows),
             'hit_sets': [set()] * len(rows), 'initial_hits': native.phases[0][1].goals,
             'terminal_error': native.terminal_error}, observation_scope, tuple(member_observations))
    registers(encoded.startup.state, native.states[0], 'startup')
    protocol_states, protocol_records = native_protocol_history(
        relation.contract, trace.stimulus, trace, check=check, pending_sample=native.pending_sample)
    # Reuse the public terminal monitor, independently of symbolic hit terms.
    # The transfer-by-transfer comparisons below also reject a tampered ledger.
    completion = _input_completion(relation.contract, stimulus, trace)
    for name, values in (
            ('terminal_hits', {INPUT_COMPLETION: any(hit.goal == INPUT_COMPLETION
                                                   for hit in completion.hits)}),
            ('terminal_assumptions', {'environment.drain-missing': any(
                item.goal == 'environment.drain-missing' for item in completion.assumption_violations)}),
            ('terminal_unresolved', {INPUT_COMPLETION: any(item.goal == INPUT_COMPLETION
                                                         for item in completion.unresolved)})):
        if terminal:
            # The product does not run completion checks after a source halt.
            # A pending queue remains unresolved, not a missing-response hit.
            if name == 'terminal_unresolved':
                continue
            values = dict.fromkeys(values, False)
        terms = getattr(encoded, name)
        if not isinstance(terms, dict) or terms.keys() != values.keys():
            raise Refused(Refusal('rtl-source-native-product-shape',
                'a complete native request requires every terminal predicate', field=name))
        tree(terms, values, name)
    for i, state in enumerate(protocol_states):
        adapters = {domain: table.state_index(state['adapters'][domain])
                    for domain, (_, _, table) in relation.protocol.parts.items()}
        tree(encoded.protocol.adapters[i], adapters, f'protocol[{i}].adapters')
        for key in ('pointers', 'released', 'framing'):
            tree(getattr(encoded.protocol, key)[i], state[key], f'protocol[{i}].{key}')
    monitor = None if relation.monitors is None else relation.monitors.initial(
        ConcreteAlgebra, lambda value, width: bool(value) if width == 1 else value)
    transactions = None if relation.transactions is None else relation.transactions.initial(ConcreteAlgebra)
    for name, product in (('monitor', monitor), ('transaction', transactions)):
        if (len(getattr(encoded, name+'_states')) != (len(rows)+1 if product is not None else 0)
                or len(getattr(encoded, name+'_hits')) != (len(rows) if product is not None else 0)):
            raise Refused(Refusal('rtl-source-native-product-shape',
                'a monitored native history needs every boundary and hit predicate', field=name))
    if monitor is not None:
        tree(encoded.monitor_states[0].image(), monitor.image(), 'monitor[0]')
    for name in ('monitor_faults', 'monitor_assumptions'):
        if len(getattr(encoded, name)) != (len(rows) if monitor is not None else 0):
            raise Refused(Refusal('rtl-source-native-product-shape',
                'a monitored native history needs every definedness predicate and environment condition', field=name))
    if transactions is not None:
        tree(encoded.transaction_states[0], transactions, 'transactions[0]')
    memory = MemoryEnvironmentState.initial(relation.contract.memories, stimulus.memory_environment)
    states, outputs, hit_sets = [], [], []
    ordered_occurrences = tuple(key for key, _ in catalogue.occurrences)
    image_key = ('source.goal_prefix' if observation_scope is SourceObservation.GOAL_PREFIX
                 else 'source.phases')

    def state_image(index):
        phases = native_images[:1] if index == 0 else native_images[1+3*(index-1):1+3*index]
        protocol = protocol_states[index]
        # Domain names are values, not JSON object keys: clock=None denotes
        # a settled sample and must not become an invented named clock.
        protocol = {**protocol, 'adapters': [
            {'clock': domain, 'state': state}
            for domain, state in sorted(protocol['adapters'].items(), key=lambda item: repr(item[0]))]}
        return {'artifact.state': native.states[index], 'driver.slot': index,
            'driver.protocol': protocol,
            'monitor.protocol': None if monitor is None else monitor.image(),
            'monitor.transactions': (None if transactions is None else
                                     relation.transactions.image(transactions)),
            'memory.environment': memory.as_dict(),
            'source.observation_scope': observation_scope.value,
            image_key: [
                {'phase': phase, 'goals': sorted(goals),
                 'occurrences': [key.as_dict() for key in ordered_occurrences if key in occurred]}
                for phase, goals, occurred in phases]}

    states.append(state_image(0))
    for i, derived in enumerate(protocol_records):
        slot = encoded.source.slots[i]
        completed = i < len(trace.records)
        record = trace.records[i] if completed else derived
        if check is not None:
            check()
        if completed:
            registers(slot.state, native.states[i+1], f'slot[{i}].successor')
        pins = dict(record.outputs)
        sampled_state = slot.phases[0].state
        required = set(request.codec.initial.widths) | {
            field.port for field in program.fields.values() if field.port is not None and not field.input}
        for name in required:
            value = pins.get(name)
            width = projection.leaves[name][1]
            if type(value) is not int or not 0 <= value < 1 << width:
                raise Refused(Refusal('rtl-source-native-pin-unreadable',
                    'the native sample must contain every physical output and register', slot=i, field=name))
        registers(sampled_state, {name: pins[name] for name in required}, f'slot[{i}].sample')
        accepted = dict(record.accepted)
        differences[f'slot[{i}].protocol.transfers'] = z3.BoolVal(
            not same_record_value(record.accepted, derived.accepted), ctx=encoded.alive.ctx)
        differences[f'slot[{i}].protocol.monitor'] = z3.BoolVal(
            not same_record_value(record.monitor, derived.monitor), ctx=encoded.alive.ctx)
        violations = {name.removeprefix('adapter.violation:') for name, _ in derived.monitor
                      if name.startswith('adapter.violation:')}
        active = stimulus.slots[i].ticks if request.atomic else relation.protocol.parts
        protocol_goals = {goal for domain, (_, _, table) in relation.protocol.parts.items()
                          if domain in active for goal in table.goal_names}
        tree(encoded.protocol.hits[i], {goal: goal in violations for goal in protocol_goals},
             f'slot[{i}].protocol.hits')
        source_hits = (native_images[3*(i+1)][1] - native_images[3*i][1]
                       if observation_scope is SourceObservation.GOAL_PREFIX else
                       frozenset().union(*(entry[1].goals for entry in native.phases[1+3*i:4+3*i])))
        hit_set = set(source_hits) | (protocol_goals & violations)
        outputs.append(tuple({endpoint.name: {field.name: value for field, value in
            zip(endpoint.fields, accepted[endpoint.name], strict=True)}}
            for endpoint in relation.contract.outputs() if accepted.get(endpoint.name) is not None))
        if set(accepted) - {endpoint.name for endpoint in relation.contract.endpoints}:
            raise Refused(Refusal('rtl-source-native-endpoint-unbound',
                'the native record names an undeclared endpoint', slot=i))
        for endpoint in relation.contract.endpoints:
            image = encoded.protocol.accepted if endpoint in relation.contract.inputs() else encoded.protocol.emitted
            payload = accepted.get(endpoint.name)
            compare(image[i][endpoint.name], payload is not None, f'slot[{i}].transfer.{endpoint.name}')
            if payload is not None:
                for field, value in zip(endpoint.fields, payload, strict=True):
                    compare(projection.term(sampled_state, field.port), pack_value(field.type, value),
                            f'slot[{i}].payload.{endpoint.name}.{field.name}')
        if completed and relation.contract.memories:
            image = dict(record.environment).get('external-memory')
            if image is None:
                raise Refused(Refusal('rtl-source-native-memory-missing',
                    'a native witness requires every external-memory successor', slot=i))
            memory = read_memory_successor(memory, image)
        memory_terms = encoded.source.memory.state_constraints(i+1, memory.as_dict()) if completed else ()
        for j, term in enumerate(memory_terms):
            differences[f'slot[{i}].memory[{j}]'] = z3.Not(term)
        event = stimulus.slots[i]
        if not request.atomic:
            domains = tuple(clock.name for clock in relation.contract.clocks)
            event = EventSlot(ticks=domains, resets=domains if event.reset else (), controls=event.controls)
        if monitor is not None:
            successor = ({name: native.states[i+1][path]
                          for name, path in relation.monitor_native_names.items()} if completed else {})
            faults = {}
            assumptions = {}
            keys = relation.monitors.request_keys(dict(derived.accepted), pack_value)
            monitor, hits = relation.monitors.step(monitor, i, event,
                {name: payload is not None for name, payload in derived.accepted}, pins, successor,
                algebra=ConcreteAlgebra, boolean=bool, word=lambda value, _: value, faults=faults,
                input_keys=keys, assumptions=assumptions, completed=completed)
            # Native proposals need not have passed the full symbolic query.
            # Undefined monitor semantics are a domain refusal, not evidence
            # that the native execution contradicts the source model.
            refusal = source_domain_failure(name for name, active in faults.items() if active)
            if refusal is not None:
                raise Refused(refusal)
            tree(encoded.monitor_states[i+1].image(), monitor.image(), f'monitor[{i+1}]')
            tree(encoded.monitor_hits[i], hits, f'slot[{i}].monitor.hits')
            tree(encoded.monitor_faults[i], faults, f'slot[{i}].monitor.faults')
            tree(encoded.monitor_assumptions[i], assumptions, f'slot[{i}].monitor.assumptions')
            hit_set.update(name for name, value in hits.items() if value)
        if transactions is not None:
            transactions, hits = relation.transactions.advance(transactions,
                relation.transactions.record_samples(derived), ConcreteAlgebra, ticks=event.ticks,
                resets={name: True for name in event.resets},
                time=stimulus.clock_schedule.slots[i].time if request.atomic else None)
            tree(encoded.transaction_states[i+1], transactions, f'transactions[{i+1}]')
            tree(encoded.transaction_hits[i], hits, f'slot[{i}].transactions.hits')
            hit_set.update(name for name, value in hits.items() if value)
        hit_sets.append(hit_set)
        states.append(state_image(i+1) if completed else terminal_state)
    if terminal:
        if native.pending_sample is None:
            # A control-phase halt precedes the sample; only its actual source
            # events belong to that step. There is no transfer or successor.
            hit_sets.append(set().union(*(entry[1].goals for entry in native.phases[1+3*len(trace.records):])))
            outputs.append(())
            states.append(terminal_state)
        missing = len(rows) - len(outputs)
        states.extend([terminal_state] * missing)
        outputs.extend([()] * missing)
        hit_sets.extend([set()] * missing)
    else:
        hit_sets[-1].update(hit.goal for hit in completion.hits)
    return SourceNativeComparison(encoded, (*encoded.constraints, *fixed), differences,
        {'states': states, 'outputs': outputs, 'hit_sets': hit_sets,
         'initial_hits': native.phases[0][1].goals, 'terminal_error': native.terminal_error},
         observation_scope, tuple(member_observations))
