"""The existing logical-transaction observers attached to atomic invocations.

One global slot may accept several domain inputs. A failed slot emits none of
its proposed outputs, including those of otherwise successful coincident calls.
Monitor ordering is task-declared, not the order in which handlers were encoded.
"""

from types import SimpleNamespace

import z3

from ..core.catalogue import CatalogueManifest
from ..core.finite_goal import _value_key
from ..core.observation_monitor import TransactionHistory, execution_monitor_for
from ..core.observation_product import TransactionProduct
from ..core.observation_symbolic import condition_terms, predicate_term
from ..core.refusal import Refusal, Refused
from ..core.stimulus import SlotRecord
from .transaction_goals import symbolic_output_samples, symbolic_payload, transaction_algebra


class AtomicTransactionObservations:
    def __init__(self, contract, components, *, steps):
        self.product = TransactionProduct(contract, artifact_kind="behavior")
        self.catalogue = CatalogueManifest(self.product.goals)
        self.native = execution_monitor_for(contract, "behavior")
        self.history = TransactionHistory(contract)
        self.endpoints = {endpoint.name: endpoint for endpoint in self.product.groups}
        self.components = components
        self.algebra = transaction_algebra(self.product, steps * max(
            1, *(component.proc.output_capacity or 0 for component in components.values())))
        self.inputs = {domain: tuple((self.endpoints[name], fields)
                                     for name, fields in component.input_endpoints if name in self.endpoints)
                       for domain, component in components.items()}
        self.outputs = {domain: tuple((self.endpoints[name], fields)
                                      for name, fields in component.output_endpoints if name in self.endpoints)
                        for domain, component in components.items()}

    def initial(self):
        return self.product.initial(self.algebra, zero=lambda _kind: z3.BitVecVal(0, 1))

    def prefix(self, previous):
        """Lift actual native history; no symbolic prefix or solver execution."""
        state = self.initial()
        for endpoint, fields in self.product.fields.items():
            payload = previous.get(endpoint)
            if payload is None:
                continue
            values = []
            for index, field in fields:
                value = payload[index]
                if type(value) is not (bool if field.type.kind == "boolean" else int):
                    raise Refused(Refusal(
                        "behavior-atomic-history-unreadable",
                        "a local query needs readable native transaction history",
                        endpoint=endpoint.name, field=field.name))
                scalar = int(value)
                values.append(z3.BitVecVal(scalar, scalar.bit_length() + 1))
            state["observations"][endpoint.name] = {
                "seen": z3.BoolVal(True), "payload": tuple(values)}
        for spec in self.product.monitors.specs:
            known, before = previous.get(("finite-monitor", spec.name), (True, spec.monitor.initial))
            if not known:
                raise Refused(Refusal(
                    "behavior-atomic-history-unreadable",
                    "a local query needs a readable native finite-monitor state",
                    monitor=spec.name))
            state["automata"][spec.name] = tuple(
                z3.BoolVal(_value_key(value) == _value_key(before)) for value in spec.monitor.states)
        for rule in self.product.latency.rules:
            try:
                state["latency"][rule.name] = self.product.latency.prefix(
                    rule, previous.get(("latency", rule.name)), self.algebra)
            except ValueError as exc:
                raise Refused(Refusal("behavior-atomic-history-unreadable", str(exc))) from exc
        return state

    def symbolic_step(self, state, invocations, outputs, halted):
        samples = {}
        for domain, active, encoded in invocations:
            component = self.components[domain]
            for endpoint, fields in self.inputs[domain]:
                samples[endpoint.name] = ((z3.And(z3.Not(halted), active), symbolic_payload(
                    component.proc, endpoint, fields, encoded.pre, "input")),)
        for domain, active, encoded in outputs:
            component = self.components[domain]
            for endpoint, fields in self.outputs[domain]:
                samples[endpoint.name] = tuple((z3.And(z3.Not(halted), active, emitted), payload)
                    for emitted, payload in symbolic_output_samples(component.proc, endpoint, fields, encoded))
        return self.product.advance(state, samples, self.algebra,
                                    predicate=predicate_term, condition=condition_terms)

    def record(self, inputs, outputs):
        """Project the actual logical inputs and visible endpoint batches once."""
        accepted, emitted = {}, {}
        for domain, values in inputs.items():
            for endpoint, fields in self.inputs[domain]:
                projection = dict(fields)
                accepted[endpoint.name] = tuple(values[projection[field.name]] for field in endpoint.fields)
        for batch in outputs:
            name, row = next(iter(batch.items()))
            if name in self.endpoints:
                endpoint = self.endpoints[name]
                emitted.setdefault(name, []).append(tuple(row[field.name] for field in endpoint.fields))
        return SlotRecord(accepted=accepted, outputs=emitted)

    def observe(self, records, hit_sets):
        """Read native predicates/table on these executions, never rerun them."""
        report = self.native.check(None, SimpleNamespace(records=records))
        if report.unresolved:
            raise Refused(Refusal("behavior-atomic-transaction-observation-unreadable",
                                  "the native atomic transaction monitor has unreadable observations"))
        hits = [set(row) for row in hit_sets]
        for observation in (*report.hits, *report.violations):
            hits[observation.slot].add(observation.goal)
        return [tuple(sorted(row)) for row in hits]
