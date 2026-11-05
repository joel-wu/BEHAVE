"""Compare two internal event orders under one explicitly shared environment.

SAT supplies an ordering counterexample, not an RTL score. The domain audit
checks feasibility, completeness, definedness and order independence through
the caller's solver owner. Native correspondence is a separate obligation;
neither constructing nor auditing this product grants an exact capability.
"""

import re
from dataclasses import dataclass, field
from itertools import chain

import z3

from ..symbolic.functional_array import FunctionalArray
from .source_completeness import boundary_expressions as _boundary_terms
from .source_relation import SourceObservation, source_event_images
from .symbolic_state import assertion_disjunction


@dataclass(frozen=True, slots=True)
class SourceOrderComparison:
    left: object
    right: object
    differences: dict
    observation_scope: SourceObservation = SourceObservation.PHASE_EVENTS
    _monitor_source_cuts: dict | None = field(default=None, init=False, repr=False, compare=False)
    _monitor_step_cuts: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _source_prefixes: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _source_event_boundaries: tuple | None = field(default=None, init=False, repr=False, compare=False)
    _source_fact_parts: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _monitor_images: tuple | None = field(default=None, init=False, repr=False, compare=False)
    _observations: tuple = field(default=(), init=False, repr=False, compare=False)
    _shared_coordinates: frozenset | None = field(default=None, init=False, repr=False, compare=False)
    _replicas_checked: bool = field(default=False, init=False, repr=False, compare=False)
    _induction: dict | None = field(default=None, init=False, repr=False, compare=False)

    def inductive(self, *, check):
        """A stronger proof view, never a replacement observation/domain policy.

        Local source cuts forget private state and adapter history. Prove their
        predecessor agreement too, rather than assuming an invariant from a
        reference or a trace. Full success implies every original obligation;
        SAT/UNKNOWN must return to the original view without partial lemmas.
        The view has no native observation certificate of its own.
        """
        if not self._local_source_steps or self._induction is not None:
            return None
        histories = self.left, self.right
        count = len(self.left.source.slots)
        if not count or len(self.right.source.slots) != count:
            return None
        for history in histories:
            check()
            protocol = getattr(history, 'protocol', None)
            roots = getattr(protocol, 'constraints', ())
            owners = getattr(protocol, 'constraint_slots', ())
            if (getattr(history, 'startup', None) is None
                    or len(getattr(history, 'sample_frames', ())) != count
                    or len(roots) != len(owners)
                    or any(type(i) is not int or not -1 <= i < count for i in owners)
                    or any(len(getattr(protocol, name, ())) != count + 1
                           for name in ('adapters', 'pointers', 'released', 'framing'))):
                return None
        extra = {}
        for name, term in self.differences.items():
            check()
            match = re.match(r'protocol\.(accepted|emitted|hits|adapters|pointers|released|framing)\[(\d+)\]', name)
            if match is not None:
                index = int(match[2]) - int(match[1] in {'adapters', 'pointers', 'released', 'framing'})
                label = 'startup' if index < 0 else f'slot[{index}]'
                extra.setdefault(label, []).append(term)

        def equal(a, b):
            check()
            if isinstance(a, dict):
                if a.keys() != b.keys():
                    raise ValueError('inductive source states have different fields')
                values = (equal(a[key], b[key]) for key in a)
            elif isinstance(a, (tuple, list)):
                values = (equal(x, y) for x, y in zip(a, b, strict=True))
            else:
                return a.equal(b, context=self.left.alive.ctx) if isinstance(a, FunctionalArray) else a == b
            return z3.And(z3.BoolVal(True, ctx=self.left.alive.ctx), *values)

        def boundary(a, b, label):
            a = a.phases[-1] if hasattr(a, 'phases') else a
            b = b.phases[-1] if hasattr(b, 'phases') else b
            terms = extra.setdefault(label, [])
            for name, value in a.state.items():
                terms.append(z3.And(a.alive, b.alive, z3.Not(equal(value, b.state[name]))))
            for part in (a, b):
                # These are extra obligations, NEVER domain premises. Startup
                # proves the base case; a later local query may use only its
                # consumed predecessor's entire negated defect, which also
                # proves these scalar ranges. A bad hypothesis invalidates
                # this optional stronger view, not the original domain.
                for name, bounds in (getattr(part, 'state_range_candidates', None) or {}).items():
                    check()
                    value = part.state.get(name)
                    if not z3.is_bv(value) or type(bounds.lo) is not int or type(bounds.hi) is not int:
                        continue
                    low, high = -(1 << (value.size() - 1)), (1 << (value.size() - 1)) - 1
                    if bounds.lo > low:
                        outside = z3.BoolVal(True, ctx=value.ctx) if bounds.lo > high else value < bounds.lo
                        terms.append(z3.And(part.alive, outside))
                    if bounds.hi < high:
                        outside = z3.BoolVal(True, ctx=value.ctx) if bounds.hi < low else value > bounds.hi
                        terms.append(z3.And(part.alive, outside))
            terms.append(z3.And(a.entered, b.entered, a.alive, b.alive,
                                a.quiescent != b.quiescent))

        boundary(self.left.startup, self.right.startup, 'startup')
        for i, (a, b) in enumerate(zip(self.left.source.slots, self.right.source.slots, strict=True)):
            for x, y in zip(a.phases, b.phases, strict=True):
                boundary(x, y, f'slot[{i}]')
        check()
        view = SourceOrderComparison(self.left, self.right, self.differences, self.observation_scope)
        object.__setattr__(view, '_induction', {label: tuple(terms) for label, terms in extra.items()})
        return view

    def native_literals(self, encoding, model, fixed, *, check):
        """Instantiate a FULLY ADMITTED confluence proof at a fixed member.

        The owner may call this only after every domain/order obligation passed.
        First verify that the histories really are independent alpha-renamed
        replicas and that every shared symbol is fixed by the public request.
        A fully validated left member then also supplies a right member. The
        proved image equalities make its readable literals universal on the
        left. Uncovered/guarded/unknown coordinates contribute no fact. These
        are premises for a fresh native comparison, never cached replay data.
        """
        from .symbolic_state import FixedBindings, _literal, _scalar_replacements, assertion_conjunction

        check()
        if encoding is not self.left or model is None or not self._observations:
            return None
        assignments = _scalar_replacements(fixed)
        if not self._replicas_checked:
            if len(self.left.constraints) != len(self.right.constraints):
                return None
            pairs = tuple(zip(self.left.constraints, self.right.constraints, strict=True))
            pairs += tuple((a, b) for a, b, _ in self._observations)
            shared = _replica_coordinates(pairs, check=check)
            check()
            object.__setattr__(self, '_shared_coordinates', shared)
            object.__setattr__(self, '_replicas_checked', True)
        if self._shared_coordinates is None or not self._shared_coordinates.issubset(
                atom.decl().get_id() for atom, _ in assignments):
            return None
        complete = assertion_conjunction((*self.left.constraints, *fixed), check=check)
        if not z3.is_true(model.eval(complete, model_completion=False)):
            return None
        known = dict((atom.get_id(), (atom, value)) for atom, value in assignments)
        for left, _right, guard in self._observations:
            check()
            # Guards are the left validity coordinate whose equality was
            # proved earlier in this image. A value from one model alone is
            # NOT sufficient to enable a guarded equality universally.
            guarded = known.get(guard.get_id())
            if not z3.is_true(guard) and (guarded is None or not z3.is_true(guarded[1])):
                continue
            value = model.eval(left, model_completion=False)
            if _literal(value):
                known[left.get_id()] = left, value
        false = z3.BoolVal(False, ctx=self.left.alive.ctx)
        for term in (self.left.unfinished, *self.left.domain_faults.values(),
                     *self.left.source.memory.conflicts):
            known[term.get_id()] = term, false
        check()
        literals = tuple(known.values())
        return FixedBindings((), literals, literals)

    @property
    def constraints(self):
        return (*self.left.constraints, *self.right.constraints)

    @property
    def unfinished(self):
        """Either event history may be truncated; check before confluence."""
        return z3.Or(self.left.unfinished, self.right.unfinished)

    @property
    def different(self):
        return assertion_disjunction((z3.BoolVal(False, ctx=self.left.alive.ctx),
                                      *self.differences.values()))

    @property
    def _framed_monitors(self):
        return all(getattr(history, 'monitor_frame_sizes', ()) for history in (self.left, self.right))

    @property
    def _local_source_steps(self):
        # External RAM is another history-carrying component. Source-state
        # cuts alone do not summarize its read/write dependencies; keep the
        # established complete prefix there, not an unsupported local proof.
        return self._framed_monitors and not any(
            getattr(getattr(getattr(history, 'source', None), 'memory', None), 'arrays', {})
            for history in (self.left, self.right))

    def _confluence_obligations(self):
        # This is only a partition of the full disagreement disjunction, not
        # a cone or domain restriction. Unrecognized image families stay in
        # product; every term is retained exactly once, including empty images.
        groups = {}
        framed = self._framed_monitors
        for name, term in self.differences.items():
            match = re.match(r'(startup|slot\[\d+\]\.phase\[\d+\])\.', name)
            # All phases of one external slot share a history prefix. Prove
            # their disagreement disjunction together instead of translating
            # and solving that prefix once per phase. Every phase's state and
            # event image remains, not just the settled slot boundary.
            label = match.group(1).split('.phase[')[0] if match else 'product'
            if framed and name.startswith('protocol.'):
                label = 'protocol'
            elif framed and (monitor := re.match(r'monitor\.(states|hits|faults|assumptions)\[(\d+)\]', name)):
                # A step's observations read its predecessor; pair them with
                # that step's successor state. Initial state precedes step 0.
                index = int(monitor[2]) + (monitor[1] != 'states')
                label = f'monitor[{index}]'
            groups.setdefault(label, []).append(term)
        false = z3.BoolVal(False, ctx=self.left.alive.ctx)
        if not groups:
            groups['product'] = ()
        order = [label for label in groups if label != 'product' and not label.startswith('monitor[')]
        order.extend(sorted((label for label in groups if label.startswith('monitor[')),
                            key=lambda label: int(label[8:-1])))
        if 'product' in groups:
            order.append('product')
        for label in order:
            extra = () if self._induction is None else self._induction.get(label, ())
            yield f'confluence:{label}', assertion_disjunction((false, *groups[label], *extra))

    def _prefix_constraints(self, phase, constraints):
        return _ProofPrefixes(self, constraints)(phase)

    def _prefix_premises(self, phase, lemmas, facts):
        if self._local_source_steps and phase == 'confluence:startup':
            return self._local_source_facts(-1, facts)
        source = re.fullmatch(r'confluence:slot\[(\d+)\]', phase) if self._local_source_steps else None
        if source:
            index = int(source[1])
            previous = 'confluence:startup' if index == 0 else f'confluence:slot[{index - 1}]'
            # Only already-consumed agreement is available. Omitting older
            # lemmas weakens this sufficient proof, not the original domain.
            return (*(() if previous not in lemmas else (lemmas[previous],)),
                    *self._local_source_facts(index, facts))
        monitor = re.fullmatch(r'confluence:monitor\[(\d+)\]', phase) if self._framed_monitors else None
        if monitor is None:
            return (*lemmas.values(), *facts)
        previous = int(monitor[1]) - 1
        # Drives read the adapter/queue ENTRY, not only the accepted/emitted
        # flags. Its equality is already established by the protocol group.
        # A proved Not(Or(images)) entails every Not(image). The index stores
        # names, not proofs: recheck the consumed owner and current term on
        # every call, including speculative planning versus ordered auditing.
        premises = tuple(z3.Not(self.differences[name])
                         for owner, name in self._monitor_image_names(previous)
                         if owner in lemmas)
        # A monitor frame reads this slot and its preceding monitor state.
        # The slot images above include entry/survival agreement. Carrying
        # every older entry and the full-history absence disjunction here
        # reintroduced a growing source DAG into each otherwise local proof.
        # Project only consequences of proved facts; an insufficient local
        # proof still takes the unchanged complete-history SAT fallback.
        return (*premises, *self._local_source_facts(previous, facts))

    def _monitor_image_names(self, index):
        """Index image ownership once, preserving the original selection order."""
        from ..analysis_budget import check_analysis

        names = tuple(self.differences)
        if self._monitor_images is None or self._monitor_images[0] != names:
            rows = {}
            for name in names:
                check_analysis()
                match = re.match(r'(slot|monitor\.states|protocol\.(?:accepted|emitted|'
                                 r'adapters|pointers|released|framing))\[(-?\d+)\]', name)
                if match is None or (match[1] == 'slot' and not name[match.end():].startswith('.')):
                    continue
                kind, row = match[1], match[2]
                owner = (f'confluence:slot[{row}]' if kind == 'slot' else
                         f'confluence:monitor[{row}]' if kind == 'monitor.states' else
                         'confluence:protocol')
                rows.setdefault(row, []).append((owner, name))
            # Never publish a partially built index after cancellation. Keep
            # only syntax; changed values and lemma sets are read by the caller.
            object.__setattr__(self, '_monitor_images', (names, rows))
        return self._monitor_images[1].get(str(index), ())

    def _local_source_facts(self, index, facts):
        """Project proved absence facts, never assume a local state invariant.

        Not(Or(...)) entails every negated leaf. Keep the current transition
        and predecessor's leaves only, so a local proof does not pull the
        entire future history back into its formula. Unrecognized facts and
        the complete-history fallback remain unchanged.
        """
        from ..analysis_budget import check_analysis

        key = tuple(term.get_id() for term in facts)
        if key not in self._source_fact_parts:
            leaves, other, seen = set(), [], set()
            for fact in facts:
                if not z3.is_not(fact) or not z3.is_or(fact.arg(0)):
                    other.append(fact)
                    continue
                pending = [fact.arg(0)]
                while pending:
                    check_analysis()
                    term = pending.pop()
                    if term.get_id() in seen:
                        continue
                    seen.add(term.get_id())
                    if z3.is_or(term):
                        pending.extend(term.children())
                    else:
                        leaves.add(term.get_id())
            # Own the roots while caching native AST identities.
            self._source_fact_parts[key] = tuple(facts), leaves, tuple(other)
        _, leaves, other = self._source_fact_parts[key]
        selected = {}
        for history in (self.left, self.right):
            startup = getattr(history, 'startup', None)
            parts = (() if index > 0 or startup is None else getattr(startup, 'phases', (startup,)))
            slots = getattr(getattr(history, 'source', None), 'slots', ())
            parts = chain(parts, (part for slot in slots[max(0, index - 1):index + 1]
                                  for part in slot.phases))
            for part in parts:
                for term in (getattr(part, 'unfinished', None),
                             *getattr(part, 'domain_faults', {}).values()):
                    if term is not None and term.get_id() in leaves:
                        selected.setdefault(term.get_id(), z3.Not(term))
        return (*other, *selected.values())

    def _event_boundaries(self):
        """Index cumulative event coordinates, not their values or reachability.

        Goal-prefix observations carry history just like physical state. A
        local proof must summarize that carry too, or an otherwise local
        transition pulls every earlier source interval back into its formula.
        """
        from ..analysis_budget import check_analysis

        if self.observation_scope is not SourceObservation.GOAL_PREFIX:
            return ()
        if self._source_event_boundaries is None:
            boundaries = [dict() for _ in range(len(self.left.source.slots) + 1)]
            for history in (self.left, self.right):
                groups = [() if history.startup is None else (history.startup,)]
                groups.extend(slot.phases for slot in history.source.slots)
                images = source_event_images(chain.from_iterable(groups), self.observation_scope,
                                             check=check_analysis)
                for terms, group in zip(boundaries, groups, strict=True):
                    for _phase in group:
                        _boundary_terms(next(images), terms)
            object.__setattr__(self, '_source_event_boundaries', tuple(boundaries))
        return self._source_event_boundaries

    def _source_cuts(self):
        """Syntactic boundary names only; no assumed equality or cached proof."""
        if self._monitor_source_cuts is not None:
            return self._monitor_source_cuts
        terms = {}
        for history in (self.left, self.right):
            startup = getattr(history, 'startup', None)
            intervals = [] if startup is None else list(getattr(startup, 'phases', (startup,)))
            intervals.extend(part for slot in history.source.slots for part in slot.phases)
            for interval in intervals:
                for name in ('state', 'alive', 'error', 'quiescent', 'entered'):
                    _boundary_terms(getattr(interval, name, None), terms)
        for boundary in self._event_boundaries():
            terms.update(boundary)
        cuts = {key: (term, z3.FreshConst(term.sort(), prefix='monitor_boundary'))
                for key, term in terms.items()}
        object.__setattr__(self, '_monitor_source_cuts', cuts)
        return cuts

    def _prefix_query(self, phase, prefix, premises, predicate):
        """Cut prior source DAGs from a sufficient transition/product proof.

        Replace the SAME subterm everywhere by one fresh coordinate. Every
        original model extends to this relaxed formula by assigning that
        coordinate its original value. Thus only UNSAT transfers; SAT still
        rechecks the untouched complete history. Preserve all predecessor,
        completeness and liveness premises, including reset phase boundaries.
        """
        from .symbolic_state import FixedBindings, assertion_conjunction

        roots = (*prefix, *premises, predicate)
        source = re.fullmatch(r'confluence:slot\[(\d+)\]', phase) if self._local_source_steps else None
        if source:
            index = int(source[1])
            if index not in self._source_prefixes:
                terms, removed = {}, set()
                for history in (self.left, self.right):
                    startup = getattr(history, 'startup', None)
                    # The first transition has no older history to summarize.
                    # Keep its actual startup definitions rather than inventing
                    # a relaxed initial boundary and forcing a full fallback.
                    previous = (() if startup is None or index == 0
                                else getattr(startup, 'phases', (startup,)))
                    previous = chain(previous, (part for slot in history.source.slots[:index]
                                                for part in slot.phases))
                    for interval in previous:
                        removed.update(term.get_id() for term in interval.constraints)
                        for name in ('state', 'alive', 'error', 'quiescent', 'entered'):
                            _boundary_terms(getattr(interval, name, None), terms)
                if index:
                    for boundary in self._event_boundaries()[:index + 1]:
                        terms.update(boundary)
                # One name per original boundary DAG node, shared across proof
                # scopes. Every query still substitutes exactly its own prefix;
                # unrelated query formulas are never asserted together.
                names = self._source_cuts() if terms else {}
                cuts = tuple(names[key] for key in terms)
                if self._induction is not None:
                    entries = {}
                    for history in (self.left, self.right):
                        for name in ('adapters', 'pointers', 'released', 'framing'):
                            for at in range(max(0, index - 1), index + 1):
                                _boundary_terms(getattr(history.protocol, name)[at], entries)
                        removed.update(term.get_id() for term, owner in zip(
                            history.protocol.constraints, history.protocol.constraint_slots, strict=True)
                            if 0 <= owner < index)
                        removed.update(term.get_id() for frame in history.sample_frames[:index] for term in frame)
                    cuts += tuple((term, z3.FreshConst(term.sort(), prefix='adapter_entry'))
                                  for key, term in entries.items() if key not in terms)
                self._source_prefixes[index] = removed, FixedBindings((), cuts, ())
            removed, bindings = self._source_prefixes[index]
            # Earlier definitions are not needed for a sufficient transition
            # proof. Replace every retained occurrence consistently; a local
            # SAT still requires the untouched full-domain check in audit().
            roots = (*(term for term in prefix if term.get_id() not in removed),
                     *premises, *self._control_definitions(index - 1, index), predicate)
            return (bindings.substitute(assertion_conjunction(roots)),)
        if (self._framed_monitors and phase in {'confluence:protocol', 'confluence:product'}
                and self._source_cuts()):
            # Conditional on the preceding groups: the audit consumes this
            # result only after proving those groups in order.
            # The terminal image may follow from those facts alone. Protocol
            # agreement still needs its prefix definitions: source agreement
            # alone does not fix all acceptance/emission history. Both paths
            # retain the complete untouched history for an abstract SAT.
            if phase not in self._monitor_step_cuts:
                self._monitor_step_cuts[phase] = FixedBindings((), tuple(self._source_cuts().values()), ())
            if phase == 'confluence:product':
                roots = (*premises, predicate)
            roots = (*roots, *self._control_definitions())
            return (self._monitor_step_cuts[phase].substitute(
                assertion_conjunction(roots)),)
        if not self._framed_monitors or not phase.startswith('confluence:monitor['):
            return roots
        index = int(phase[19:-1]) - 1
        if index < 0:
            return roots
        source_cuts = self._source_cuts()
        if index not in self._monitor_step_cuts:
            cuts = dict(source_cuts)
            terms = {}
            for history in (self.left, self.right):
                protocol = getattr(history, 'protocol', None)
                for name in ('accepted', 'emitted', 'adapters', 'pointers', 'released', 'framing'):
                    rows = getattr(protocol, name, ())
                    if index < len(rows):
                        _boundary_terms(rows[index], terms)
            for key, term in terms.items():
                if key not in cuts:
                    cuts[key] = (term, z3.FreshConst(term.sort(), prefix='monitor_transfer'))
            if not cuts:
                return roots
            # The identical cut pairs are used by both the speculative plan
            # and ordered audit. Retain their native argument buffers, not a
            # solver result, instead of coercing the entire history twice.
            self._monitor_step_cuts[index] = FixedBindings((), tuple(cuts.values()), ())

        roots = (*roots, *self._control_definitions(index, index))
        return (self._monitor_step_cuts[index].substitute(assertion_conjunction(roots)),)

    def _control_definitions(self, first=-1, last=None):
        """Keep a normalized guard's identity across a sufficient boundary cut.

        These are tautologies of the typed interval, not successful-execution
        assumptions. After composite terms become fresh coordinates they link
        the continuation guard back to the state-validity guard used by the
        preceding proof. The untouched query does not need these extra roots.
        """
        from .source_relation import SourceIntervalEncoding

        for history in (self.left, self.right):
            startup = getattr(history, 'startup', None)
            parts = (() if first >= 0 or startup is None else getattr(startup, 'phases', (startup,)))
            slots = history.source.slots[max(0, first):None if last is None else last + 1]
            for part in chain(parts, (part for slot in slots for part in slot.phases)):
                if type(part) is SourceIntervalEncoding:
                    yield part.continuable == z3.And(part.entered, part.alive, part.quiescent)

    def _domain_obligations(self):
        from ..core.query import SolverAnswer

        false = z3.BoolVal(False, ctx=self.left.alive.ctx)
        faults = z3.Or(false, *(term for history in (self.left, self.right)
                               for term in history.domain_faults.values()))
        conflicts = z3.Or(false, *(term for history in (self.left, self.right)
                                  for term in history.source.memory.conflicts))
        return (
            ('feasibility', None, SolverAnswer.SAT, 'empty-domain',
             'the declared source-history domain has no member'),
            ('completeness', self.unfinished, SolverAnswer.UNSAT, 'event-bound-incomplete',
             'a declared source history has pending events beyond the event bound'),
            ('definedness', faults, SolverAnswer.UNSAT, 'operation-domain-unencoded',
             'a declared source history reaches an unencoded operation domain'),
            ('memory', conflicts, SolverAnswer.UNSAT, 'memory-conflict',
             'a declared source history reaches conflicting external-memory accesses'),
        )

    def order_queries(self, *, domain=(), prefix_proofs=False):
        """Conditional queries, not proofs: consume only after earlier checks agree.

        Every later group assumes the earlier groups' agreement. Parallel work
        may speculate on those conditions; only audit's ordered consumption can
        establish them. No partial result from an unconsumed group is reusable.
        """
        constraints = (*self.constraints, *domain)
        prefix_for = _ProofPrefixes(self, constraints) if prefix_proofs else None
        facts = tuple(z3.Not(term) for _, term, *_ in self._domain_obligations()
                      if term is not None)
        lemmas = {}
        for phase, predicate in self._confluence_obligations():
            jobs = []
            if prefix_proofs:
                prefix = prefix_for(phase)
                if len(prefix) < len(constraints):
                    premises = self._prefix_premises(phase, lemmas, facts)
                    jobs.append((phase + ':prefix', self._prefix_query(phase, prefix, premises, predicate)))
            jobs.append((phase, (*constraints, *lemmas.values(), *facts, predicate)))
            yield phase, tuple(jobs)
            lemmas[phase] = z3.Not(predicate)

    def audit(self, solve, *, domain=(), prefix_proofs=False, check=None):
        """Check the unfiltered product, without making an RTL correctness claim.

        ``solve(phase, assertions)`` belongs to the enclosing analysis request:
        it owns the shared remaining budget, cancellation and query receipts,
        and returns an ExactCheckResult. No private solver or deadline is made
        here. Exceptions propagate; UNKNOWN never satisfies an obligation.
        Explicit domain constraints restrict the shared initial/environment
        choices, not a baseline's observed states. Confluence is proved in
        history order; only an already proved domain fact or UNSAT disagreement
        may become a lemma for a later group. Thus no unproved equality, successful DUT
        completion, or absence of safety hits is assumed.

        Optional prefix proofs first omit later source-phase constraints. This
        enlarges the domain: only UNSAT can prove full-domain agreement. SAT is
        rechecked with every original constraint; UNKNOWN proves nothing. The
        same solve owner and budget cover both attempts, with no private cap.
        """
        from ..symbolic.exact_solve import ExactCheckResult
        from ..analysis_budget import check_analysis
        from ..core.audit import AuditCheck
        from ..core.query import SolverAnswer
        from .source_completeness import local_completeness

        constraints = (*self.constraints, *domain)
        prefix_for = _ProofPrefixes(self, constraints) if prefix_proofs else None
        obligations = self._domain_obligations()
        confluence = (
            (phase, predicate, SolverAnswer.UNSAT, 'order-sensitive',
             'allowed internal event orders or private initial choices disagree on the full observed history')
            for phase, predicate in self._confluence_obligations())
        lemmas, domain_facts = {}, []
        for phase, predicate, required, code, why in chain(obligations, confluence):
            answer = None
            if prefix_proofs and phase in {'definedness', 'memory'}:
                # A false defect is impossible under ANY history. Do not load
                # the entire product merely to re-prove this syntactic fact.
                # Keep the owner's solver receipt/deadline; nonconstant defects
                # still use the unchanged complete-domain obligation below.
                folded = z3.simplify(predicate)
                (check or check_analysis)()
                if z3.is_false(folded):
                    answer = solve(phase, (folded,))
            if prefix_proofs and phase == 'completeness':
                answer = local_completeness(self, solve, check=check or check_analysis)
            # Earlier full-domain UNSAT checks already establish these facts.
            # Reuse them during ordering proofs, including relaxed prefixes;
            # they cannot exclude a member of the original complete domain.
            premises = (*lemmas.values(), *domain_facts) if phase.startswith('confluence:') else ()
            if answer is None and prefix_proofs and (phase.startswith('confluence:') or phase in {'completeness', 'memory'}):
                prefix = prefix_for(phase)
                if len(prefix) < len(constraints):
                    local = self._prefix_premises(phase, lemmas, domain_facts) if phase.startswith('confluence:') else ()
                    answer = solve(f'{phase}:prefix', self._prefix_query(phase, prefix, local, predicate))
                    if type(answer) is not ExactCheckResult:
                        raise TypeError('source-domain audit requires the exact solver result type')
                    if answer.answer is SolverAnswer.SAT:
                        answer = None  # A relaxed-domain witness is not a counterexample.
            if answer is None:
                answer = solve(phase, constraints if predicate is None else (*constraints, *premises, predicate))
            if type(answer) is not ExactCheckResult:
                raise TypeError('source-domain audit requires the exact solver result type')
            if answer.answer is SolverAnswer.UNKNOWN:
                return AuditCheck.refused('rtl-source-domain-check-unresolved',
                                         f'{phase}: {answer.reason}')
            if answer.answer is not required:
                if phase == 'definedness' and answer.model is not None:
                    reached = sorted({name for history in (self.left, self.right)
                                      for name, term in history.domain_faults.items()
                                      if z3.is_true(answer.model.eval(term, model_completion=True))})
                    if reached:
                        why += '; reached: ' + ', '.join(reached)
                if code == 'order-sensitive':
                    # Only a full-domain SAT model reaches this branch. A
                    # relaxed-prefix SAT is rechecked above before reporting.
                    first = next((name for name, term in self.differences.items()
                                  if z3.is_true(answer.model.eval(term, model_completion=True))), None)
                    if first is not None:
                        why += f'; first differing observation: {first}'
                    why += '; this is an exact-analysis refusal, not an artifact failure'
                return AuditCheck.refused(f'rtl-source-{code}', f'{phase}: {why}')
            if phase.startswith('confluence:'):
                lemmas[phase] = z3.Not(predicate)
            elif predicate is not None:
                domain_facts.append(z3.Not(predicate))
        return AuditCheck.agreed()


def _proof_shape(root, *, check):
    """Bounded structural sample, NEVER authority to reuse a proof.

    Sampling 64 AST edges bounds cache-lookup overhead, not the proof domain.
    The exact checker still visits ALL premises and the defect before reuse.
    Coordinates are anonymous; visited-node ordinals preserve sampled aliases.
    Collisions (including differences beyond the sample) require that checker.
    """
    from z3 import z3core as native

    ctx = root.ctx_ref()

    def children(term, count):
        # Lazy, including for very wide conjunctions. Visit the defect first,
        # matching the exact checker's last-child-first traversal.
        for index in range(count - 1, -1, -1):
            yield native.Z3_get_app_arg(ctx, term, index)

    pending, sample, seen = [iter((root.as_ast(),))], [], {}
    while pending and len(sample) < 64:
        check()
        term = next(pending[-1], None)
        if term is None:
            pending.pop()
            continue
        key = native.Z3_get_ast_id(ctx, term)
        if key in seen:
            sample.append(('seen', seen[key]))
            continue
        if native.Z3_get_ast_kind(ctx, term) not in (z3.Z3_APP_AST, z3.Z3_NUMERAL_AST):
            return None  # Quantified/non-application queries have no reuse bucket.
        seen[key] = len(seen)
        count = native.Z3_get_app_num_args(ctx, term)
        declaration = native.Z3_get_app_decl(ctx, term)
        free = count == 0 and native.Z3_get_decl_kind(ctx, declaration) == z3.Z3_OP_UNINTERPRETED
        sample.append((native.Z3_get_sort_id(ctx, native.Z3_get_sort(ctx, term)), count,
                       'coordinate' if free else native.Z3_get_func_decl_id(ctx, declaration)))
        if count:
            pending.append(children(term, count))
    return hash(tuple(sample))


def _replica_coordinates(pairs, *, check, shared_functions=False, independent=True):
    """Check alpha-isomorphism, including independence, without a new solver.

    A permutation or accidental alias of shared symbols is not an independent
    replica. Native witness reuse rejects uninterpreted functions; a local
    universal proof may explicitly permit the SAME function declaration on
    both sides and overlapping bijective coordinate renamings. Such a theorem
    transfer is not evidence of independent execution replicas. Quantifiers
    always disable reuse.
    The traversal visits each paired DAG node once and publishes no partial plan.
    """
    from z3 import z3core as native

    # Keep the root ExprRefs alive while traversing their borrowed native ASTs.
    # Constructing Python ExprRef/sort/declaration wrappers for every shared
    # DAG node dominates this purely structural check on long histories.
    pairs = tuple(pairs)
    if not pairs:
        return frozenset()
    context = pairs[0][0].ctx
    if any(a.ctx != context or b.ctx != context for a, b in pairs):
        return None
    ctx = context.ref()
    pending = [(a.as_ast(), b.as_ast()) for a, b in pairs]
    seen, forward, reverse = set(), {}, {}
    while pending:
        check()
        left, right = pending.pop()
        key = native.Z3_get_ast_id(ctx, left), native.Z3_get_ast_id(ctx, right)
        if key in seen:
            continue
        seen.add(key)
        if (native.Z3_get_ast_kind(ctx, left) not in (z3.Z3_APP_AST, z3.Z3_NUMERAL_AST)
                or native.Z3_get_ast_kind(ctx, right) not in (z3.Z3_APP_AST, z3.Z3_NUMERAL_AST)
                or not native.Z3_is_eq_sort(ctx, native.Z3_get_sort(ctx, left), native.Z3_get_sort(ctx, right))):
            return None
        a, b = native.Z3_get_app_decl(ctx, left), native.Z3_get_app_decl(ctx, right)
        kind, count = native.Z3_get_decl_kind(ctx, a), native.Z3_get_app_num_args(ctx, left)
        if kind != native.Z3_get_decl_kind(ctx, b) or count != native.Z3_get_app_num_args(ctx, right):
            return None
        if kind == z3.Z3_OP_UNINTERPRETED:
            if count:
                if not shared_functions or not native.Z3_is_eq_func_decl(ctx, a, b):
                    return None
            else:
                x, y = native.Z3_get_func_decl_id(ctx, a), native.Z3_get_func_decl_id(ctx, b)
                if forward.get(x, y) != y or reverse.get(y, x) != x:
                    return None
                forward[x], reverse[y] = y, x
        elif not native.Z3_is_eq_func_decl(ctx, a, b):
            return None  # Includes different literal values and extract ranges.
        pending.extend((native.Z3_get_app_arg(ctx, left, i), native.Z3_get_app_arg(ctx, right, i))
                       for i in range(count))
    shared = forward.keys() & reverse.keys()
    if independent and any(forward[key] != key or reverse[key] != key for key in shared):
        return None
    return frozenset(shared)


class _ProofPrefixes:
    """Index one immutable proof plan, not a solver answer or a new domain.

    Source proofs omit future slots and monitor definitions. A monitor proof
    keeps only its own frame plus external constraints. Indexing those roots
    once avoids rescanning every historical queue cell for every transition.
    Root order, duplicates, and the untouched full-query fallback are retained.
    """

    def __init__(self, comparison, constraints):
        self.constraints, self.framed = constraints, comparison._framed_monitors
        histories = (comparison.left, comparison.right)
        monitor_ids = {term.get_id() for history in histories
                       for term in getattr(history, 'monitor_constraints', ())}
        rows = tuple((term.get_id(), term) for term in constraints)
        self.source = tuple((key, term) for key, term in rows if key not in monitor_ids)
        self.last_slot = {}
        interval_ids = set()
        for history in histories:
            startup = getattr(history, 'startup', None)
            if startup is not None:
                interval_ids.update(term.get_id() for term in startup.constraints)
            for index, slot in enumerate(history.source.slots):
                for interval in slot.phases:
                    for term in interval.constraints:
                        key = term.get_id()
                        interval_ids.add(key)
                        self.last_slot[key] = max(index, self.last_slot.get(key, -1))
        # Protocol follows already-proved source images. Retain its defining
        # equations, physical sample links and all external constraints, but
        # do not re-prove every source transition for that sufficient check.
        # SAT still takes the untouched full-history path in audit().
        self.protocol = tuple(term for key, term in self.source if key not in interval_ids)
        self.protocol_slots = {}
        if comparison._local_source_steps:
            # Sample equations and adapter legality also belong to a slot.
            # Previously their future history survived the source-only cut.
            # Missing/invalid metadata keeps the established complete prefix.
            for history in histories:
                protocol = getattr(history, 'protocol', None)
                roots = getattr(protocol, 'constraints', ())
                slots = getattr(protocol, 'constraint_slots', ())
                if len(slots) == len(roots) and all(type(i) is int and
                        -1 <= i < len(history.source.slots) for i in slots):
                    for term, index in zip(roots, slots):
                        key = term.get_id()
                        self.protocol_slots[key] = min(index, self.protocol_slots.get(key, index))
                frames = getattr(history, 'sample_frames', ())
                if len(frames) == len(history.source.slots):
                    for index, frame in enumerate(frames):
                        for term in frame:
                            key = term.get_id()
                            self.protocol_slots[key] = min(index, self.protocol_slots.get(key, index))
        self.frames, self.extra = None, ()
        if self.framed:
            history_ids = {term.get_id() for history in histories for term in history.constraints}
            self.extra = tuple((i, term) for i, (key, term) in enumerate(rows)
                               if key not in history_ids and key not in monitor_ids)
            locations = {}
            for i, (key, term) in enumerate(rows):
                locations.setdefault(key, []).append((i, term))
            sizes = [history.monitor_frame_sizes for history in histories]
            if all(all(type(size) is int and size >= 0 for size in row)
                   and sum(row) == len(history.monitor_constraints)
                   for row, history in zip(sizes, histories)):
                self.frames = [set() for _ in range(min(map(len, sizes)))]
                for history in histories:
                    offset = 0
                    for frame, size in zip(self.frames, history.monitor_frame_sizes):
                        frame.update(term.get_id() for term in history.monitor_constraints[offset:offset + size])
                        offset += size
                self.frames = tuple(tuple(sorted(
                    (*self.extra, *(entry for key in frame for entry in locations.get(key, ()))),
                    key=lambda entry: entry[0])) for frame in self.frames)

    def __call__(self, phase):
        if self.framed and phase == 'confluence:protocol':
            return self.protocol
        monitor = re.fullmatch(r'confluence:monitor\[(\d+)\]', phase) if self.framed else None
        if monitor:
            index = int(monitor[1])
            if index == 0:
                selected = self.extra
            elif self.frames is None or index > len(self.frames):
                return self.constraints
            else:
                selected = self.frames[index - 1]
            return tuple(term for _, term in selected)
        if phase in {'completeness', 'memory'} or (self.framed and phase in {
                'confluence:product'}):
            return tuple(term for _, term in self.source)
        if phase == 'confluence:startup':
            boundary = -1
        elif match := re.fullmatch(r'confluence:slot\[(\d+)\]', phase):
            boundary = int(match[1])
        else:
            return self.constraints
        return tuple(term for key, term in self.source if self.last_slot.get(key, -1) <= boundary
                     and self.protocol_slots.get(key, -1) <= boundary)


def compare_histories(left, right, fields, *, check=None,
                      observation_scope=SourceObservation.PHASE_EVENTS):
    """Compare complete state/product and the explicitly scoped event images.

    The caller shares public initial coordinates, queue cells, environment
    actions and memory policy, with distinct internal names. Private initial
    fields may be independent when proving their observational irrelevance;
    an order-only audit instead shares the entire initial state.
    Dead source successors and absent memory-response payloads have no meaning;
    compare their validity/error but not unconstrained payload bits.
    """
    if type(observation_scope) is not SourceObservation:
        raise TypeError('a source comparison needs a named observation scope')
    context, differences, observations = left.alive.ctx, {}, []
    always = z3.BoolVal(True, ctx=context)

    def images(history):
        phases = (() if history.startup is None else (history.startup,))
        phases = chain(phases, (phase for slot in history.source.slots for phase in slot.phases))
        return source_event_images(phases, observation_scope, check=check)

    left_images, right_images = images(left), images(right)

    def compare(a, b, name, guard=None):
        if check is not None:
            check()
        if isinstance(a, dict):
            if a.keys() != b.keys():
                raise ValueError(f'{name}: histories have different image fields')
            for key in a:
                compare(a[key], b[key], f'{name}.{key}', guard)
        elif isinstance(a, (tuple, list)):
            for i, (x, y) in enumerate(zip(a, b, strict=True)):
                compare(x, y, f'{name}[{i}]', guard)
        else:
            equal = a.equal(b, context=context) if isinstance(a, FunctionalArray) else a == b
            differences[name] = z3.And(always if guard is None else z3.And(*guard), z3.Not(equal))
            if isinstance(a, z3.ExprRef) and isinstance(b, z3.ExprRef):
                observations.append((a, b, always if guard is None else guard[0]))

    def phase(a, b, name):
        # Entry and survival differ for a disabled interval. Retain both
        # proved boundaries: a named control successor must not force later
        # monitors to reconstruct its entire source history to recover entry.
        compare(a.entered, b.entered, f'{name}.entered')
        compare(a.alive, b.alive, f'{name}.alive')
        # Consumers use this normalized guard, not the unnormalized conjunction
        # of entered/alive/quiescent. A local cut must carry its proved agreement
        # as well: simplification can otherwise hide its syntactic dependence.
        compare(a.continuable, b.continuable, f'{name}.continuable')
        compare(a.error, b.error, f'{name}.error')
        compare({key:a.state[key] for key in fields}, {key:b.state[key] for key in fields},
                f'{name}.state', (a.alive, b.alive))
        for image, x, y in zip(('hits', 'occurrences'), next(left_images), next(right_images), strict=True):
            compare(x, y, f'{name}.{image}')
        compare(a.domain_faults, b.domain_faults, f'{name}.domain_faults')

    if (left.startup is None) != (right.startup is None):
        raise ValueError('source histories must have the same startup boundary')
    if left.startup is not None:
        phase(left.startup, right.startup, 'startup')
    for slot, (a, b) in enumerate(zip(left.source.slots, right.source.slots, strict=True)):
        for index, (x, y) in enumerate(zip(a.phases, b.phases, strict=True)):
            phase(x, y, f'slot[{slot}].phase[{index}]')
    for image in ('accepted', 'emitted', 'hits', 'adapters', 'pointers', 'released', 'framing'):
        compare(getattr(left.protocol, image), getattr(right.protocol, image), f'protocol.{image}')
    compare(left.monitor_hits, right.monitor_hits, 'monitor.hits')
    compare(left.monitor_faults, right.monitor_faults, 'monitor.faults')
    compare(left.monitor_assumptions, right.monitor_assumptions, 'monitor.assumptions')
    compare(tuple(state.image() for state in left.monitor_states),
            tuple(state.image() for state in right.monitor_states), 'monitor.states')
    compare(left.transaction_hits, right.transaction_hits, 'transactions.hits')
    compare(left.transaction_states, right.transaction_states, 'transactions.states')
    for name in ('terminal_hits', 'terminal_assumptions', 'terminal_unresolved'):
        a, b = getattr(left, name), getattr(right, name)
        if a is None and b is None:
            continue  # An interval/continuation does not claim terminal evidence.
        if a is None or b is None:
            raise ValueError('source histories must have the same terminal boundary')
        compare(a, b, name)
    a, b = left.source.memory, right.source.memory
    for image in ('arrays', 'write_flags', 'write_values'):
        compare({key:tuple(rows) for key, rows in getattr(a, image).items()},
                {key:tuple(rows) for key, rows in getattr(b, image).items()}, f'memory.{image}')
    compare(tuple(a.conflicts), tuple(b.conflicts), 'memory.conflicts')
    if not (a.responses.keys() == b.responses.keys() == a.delays.keys() == b.delays.keys()):
        raise ValueError('source histories must have the same memory response channels')
    for key in a.responses:
        for slot, (x, y) in enumerate(zip(a.responses[key], b.responses[key], strict=True)):
            name = f'memory.responses.{key}[{slot}]'
            compare(x[0], y[0], f'{name}.valid')
            compare(tuple(x[1:]), tuple(y[1:]), f'{name}.payload', (x[0], y[0]))
        for slot, (x, y) in enumerate(zip(a.delays[key], b.delays[key], strict=True)):
            for stage, (p, q) in enumerate(zip(x, y, strict=True)):
                name = f'memory.pending.{key}[{slot}][{stage}]'
                compare(p[0], q[0], f'{name}.valid')
                compare(tuple(p[1:]), tuple(q[1:]), f'{name}.payload', (p[0], q[0]))
    result = SourceOrderComparison(left, right, differences, observation_scope)
    object.__setattr__(result, '_observations', tuple(observations))
    return result
