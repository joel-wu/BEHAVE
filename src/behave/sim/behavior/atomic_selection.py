"""An audited atomic seed supplies local state/history, never a bounded proof.

Selection reads existing native audit feedback once. A request lifts only the
chosen boundary and encodes one real slot; it does not encode the prefix. The
accepted-invocation pattern stays fixed. New input values must also leave the
unchanged accepted-input suffix legal. Native replay still decides actual hits.
"""

from dataclasses import dataclass
from types import SimpleNamespace

import z3

from ..core.audit import AuditCheckStatus
from ..core.input_framing import InputDomainCursor, LocalInputDomain, symbolic_initial
from ..core.refusal import Refusal, Refused
from ..core.stimulus import SlotRecord
from .atomic_exact import (
    _AtomicBoundary, _constant, _encode_atomic_slot, _input_cell_name, _invoke_name, _row_name,
)


@dataclass(frozen=True)
class _Prefix:
    state: object
    released: frozenset
    frames: dict
    transactions: dict
    hits: frozenset


class AtomicSelectedTrace:
    """Worker-local selection, with no solver result or exact-analysis authority."""

    def __init__(self, context, rows, *, verify_feedback=True):
        self.context = context
        self.rows = tuple(dict(row) for row in rows)
        contract = context.observation_contract
        endpoints = {endpoint.name: endpoint for endpoint in contract.endpoints}
        self.framed_endpoints = {
            domain: tuple(endpoint for endpoint in contract.inputs()
                          if endpoint.framing is not None
                          and endpoint.name in dict(component.input_endpoints))
            for domain, component in context.components.items()}
        projections = {}
        for domain, component in context.components.items():
            for name, fields in component.input_endpoints:
                # bind_input_framing already requires scalar IR input bindings.
                projections[name] = tuple((public, _row_name(domain, logical))
                    for public, logical in fields if endpoints[name].framing is not None
                    and public in endpoints[name].framing.fields)
        frames = InputDomainCursor(contract)
        previous, released, prefixes, records = {}, set(), [], []

        def record_for(row, outputs):
            accepted, emitted = {}, {}
            for domain, component in context.components.items():
                if not row[_invoke_name(domain)]:
                    continue
                values = component.decode_inputs(component.physical_inputs(row))
                for name, fields in component.input_endpoints:
                    mapping = dict(fields)
                    accepted[name] = tuple(values[mapping[field.name]] for field in endpoints[name].fields)
            for batch in outputs:
                name, values = next(iter(batch.items()))
                emitted.setdefault(name, []).append(tuple(values[field.name] for field in endpoints[name].fields))
            return SlotRecord(accepted=accepted, outputs=emitted)

        def collect(_trace, position, state, hits, outputs):
            event = context.schedule.slots[position]
            record = record_for(self.rows[position], outputs)
            prefixes.append(_Prefix(state, frozenset(released), dict(frames.states),
                                    dict(previous), hits))
            records.append(record)
            frames.advance(event.event, dict(record.accepted), index=position)
            if set(event.events) == set(context.domains) == set(event.resets):
                released.clear()
            else:
                released.update(domain for domain in event.events if domain not in event.resets)
            if context.observations is not None:
                samples = {name: [payload] for name, payload in record.accepted}
                samples.update((name, list(payloads)) for name, payloads in record.outputs)
                native_hits = context.observations.history.observe(previous, samples)
                if native_hits != set(hits) & set(context.observations.catalogue):
                    raise Refused(Refusal(
                        "behavior-atomic-history-mismatch",
                        "native selected history disagrees with audited transaction hits",
                        slot=position))

        if verify_feedback:
            checked = context.source_audit((self.rows,), observe_step=collect)
            if checked.status is not AuditCheckStatus.AGREED:
                raise Refused(Refusal(
                    "behavior-generation-feedback-unverified",
                    "source/IR replay did not establish the selected atomic seed",
                    audit=checked.as_dict()))
        else:
            # A session may reconstruct a previously confirmed seed for Probe.
            # This produces local state, never new native feedback or hit credit.
            context._semantic_witness(self.rows, context._domain.digest(), observe_step=collect)
        self.prefixes = tuple(prefixes)
        self.hits = frozenset(goal for prefix in prefixes for goal in prefix.hits)
        # Keep the submitted suffix even after a terminal source failure. If a
        # proposed input repairs that failure, the same suffix must be legal.
        records.extend(record_for(row, ()) for row in self.rows[len(records):])
        self.input_domain = LocalInputDomain(
            contract, SimpleNamespace(slots=tuple(slot.event for slot in context.schedule.slots)),
            records, projections)

    def boundary(self, position):
        context, prefix = self.context, self.prefixes[position]
        frames = {}
        for domain, framed in self.framed_endpoints.items():
            frames[domain] = tuple(
                tuple(z3.BitVecVal(value, term.size()) for value, term in zip(
                    prefix.frames[endpoint.name], symbolic_initial(endpoint.framing), strict=True))
                for endpoint in framed)
        return _AtomicBoundary(
            state={name: _constant(value, context.state_env[name], context.state_types[name],
                                  name, width=context.scalar_width) for name, value in prefix.state.items()},
            halted=z3.BoolVal(False),
            error=z3.BitVecVal(0, max(1, (len(context.error_codes) - 1).bit_length())),
            released=prefix.released, framing=frames,
            transactions=(context.observations.prefix(prefix.transactions)
                          if context.observations is not None else None))

    def encode(self, position):
        """One slot plus the fixed-invocation and legal-suffix input domain.

        ``invalid`` remains a separate obligation in the returned step. It is
        not negated and added as an assumption to discard problematic inputs.
        """
        from dataclasses import replace

        context = self.context
        step = _encode_atomic_slot(context, self.boundary(position),
                                   context.schedule.slots[position], position=position)
        fixed = tuple(step.slot.inputs[_invoke_name(domain)] == self.rows[position][_invoke_name(domain)]
                      for domain in context.domains)
        frames = {endpoint.name: state for domain, endpoints in self.framed_endpoints.items()
                  for endpoint, state in zip(endpoints, step.after.framing[domain], strict=True)}
        return replace(step, restrictions=step.restrictions + fixed + tuple(
            z3.Implies(z3.Not(step.after.halted), legal)
            for legal in self.input_domain.suffix_constraints(position, frames)))

    def local_hit(self, position, goals, *, timeout_ms, fixed_inputs=None):
        """Solve one actual slot, keeping invalid transitions out of assumptions."""
        from time import monotonic
        from ..generation_protocol import Outcome, ProbeResult, Scope
        from .atomic_exact import _signed

        started = monotonic()

        def result(outcome, **kwargs):
            return ProbeResult(outcome, Scope.EXACT_LOCAL_HIT,
                               solve_ms=(monotonic() - started) * 1000, **kwargs)

        step = self.encode(position)
        supported = set(self.context.frontend_catalogue_manifest())
        if self.context.observations is not None:
            supported.update(self.context.observations.catalogue)
        missing = set(goals) - supported
        if missing:
            return result(Outcome.UNSUPPORTED, detail=f"no atomic hit term: {sorted(missing)}")
        solver = z3.Solver()
        solver.add(*step.formula.full_constraints)
        # Field scopes are logical values. Bind every fixed leaf through the
        # handler's declared layout, including arrays/records, never guessed
        # variable names. The full-field tier supplies an empty mapping.
        bindings = {_row_name(domain, binding.name): (domain, binding)
                    for domain, component in self.context.components.items()
                    for binding in component.proc.signature.input_bindings}
        for name, value in (fixed_inputs or {}).items():
            domain, binding = bindings[name]
            for path, scalar in binding.flatten(value):
                term = step.slot.inputs[_input_cell_name(domain, path.variable, path.indices)]
                solver.add(term == int(scalar))

        def check(term):
            remaining = timeout_ms - (monotonic() - started) * 1000
            if remaining <= 0:
                return z3.unknown, "atomic probe budget exhausted"
            solver.set(timeout=max(1, int(remaining)))
            solver.push()
            solver.add(term)
            answer = solver.check()
            detail = solver.reason_unknown() if answer == z3.unknown else ""
            return answer, detail

        answer, detail = check(step.invalid)
        if answer == z3.sat:
            return result(Outcome.UNSUPPORTED, detail="selected boundary admits an undefined atomic transition")
        if answer == z3.unknown:
            return result(Outcome.UNKNOWN, detail=detail)
        solver.pop()
        # As in the full product, a declared event with no active producer in
        # this slot is False. This says nothing about another slot or prefix.
        terms = {goal: step.slot.hits.get(goal, z3.BoolVal(False)) for goal in goals}
        answer, detail = check(z3.Or(list(terms.values())))
        if answer == z3.unsat:
            return result(Outcome.LOCAL_UNSAT)
        if answer == z3.unknown:
            return result(Outcome.UNKNOWN, detail=detail)
        model = solver.model()
        row = {name: (model.eval(term, model_completion=True).as_long()
                      if name in step.slot.unsigned_inputs else _signed(model, term))
               for name, term in step.slot.inputs.items()}
        values = {}
        for domain, component in self.context.components.items():
            if row[_invoke_name(domain)]:
                values.update((_row_name(domain, name), value) for name, value in
                              component.decode_inputs(component.physical_inputs(row)).items())
        goal = next(goal for goal in goals if z3.is_true(model.eval(terms[goal])))
        return result(Outcome.SAT, goal=goal, candidate=values)
