"""Reference evidence retained while matching one actual execution.

Reference values remain independent of candidate outputs. This ledger records
only where each stream crosses a reset; the output matcher owns pairing and
cancellation. Storage is linear in endpoints times reset events, not run length.
The optional source recorder retains step hits from that same reference run.
"""

from __future__ import annotations

from collections import Counter

from .contract import ResetPolicy
from .failure import FailureRaised, RunFailure
from .refusal import Refusal, Refused


class ReferenceProgress:
    """Stream boundaries shared by single-clock and atomic reference replay."""

    def __init__(self, contract, *, behavior=False):
        self.behavior = behavior
        self._source = None
        self._source_binding = None
        self._transaction_monitor = None
        self._transaction_hits = set()
        self._declared_monitor = None
        if contract.transaction_monitors:
            from .transaction_monitor import DeclaredTransactionMonitor

            self._declared_monitor = DeclaredTransactionMonitor(contract, behavior=True).new_run()
        if contract.observation_goals:
            from .observation_monitor import TransactionMonitor

            self._transaction_monitor = TransactionMonitor(contract, behavior=True)
        self.outputs = {}
        for endpoint in contract.outputs():
            group = next((set(instance.endpoints) for instance in contract.protocol_instances
                          if endpoint.name in instance.endpoints), None)
            sources = [source for source in contract.inputs()
                       if (source.name in group if group is not None
                           else source.clock == endpoint.clock)]
            policies = {source.reset_policy for source in sources}
            # No pairing is inferred for a mixed-policy group. A pending response
            # there requires a more specific task mapping, not an arbitrary source.
            policy = next(iter(policies)) if len(policies) == 1 else None
            if policy is not ResetPolicy.KEEPS:
                self.outputs[endpoint.name] = (endpoint.clock, policy)
        self.observed = Counter()
        self.cuts = {name: [] for name in self.outputs}

    def begin_source(self, bundle, model, trace):
        self._source = bundle._source_recorder(model)
        # The trace is immutable. Defer its potentially large serialization until
        # comparison assembles the observation, after the reference child closes;
        # provenance bookkeeping must not consume that child's execution lifetime.
        self._source_binding = bundle.digest, trace
        return self._source

    def observe_source(self, hits=None):
        if self._source is not None:
            self._source.observe(hits)

    @property
    def source_observation(self):
        from .catalogue import CatalogueManifest, merge_catalogues
        from .reference_observation import ReferenceSourceObservation

        observed = None
        if self._source is not None:
            reference, trace = self._source_binding
            observed = self._source.finish(reference, trace.fingerprint)
        if type(observed) is not ReferenceSourceObservation or (
                self._transaction_monitor is None and self._declared_monitor is None):
            return observed
        catalogues, hits = [observed.catalogue], set(observed.hits)
        if self._transaction_monitor is not None:
            catalogues.append(CatalogueManifest(self._transaction_monitor.goals()))
            hits.update(self._transaction_hits)
        if self._declared_monitor is not None:
            catalogues.append(self._declared_monitor.binding.catalogue)
            hits.update(hit.goal for hit in self._declared_monitor.hits)
        return ReferenceSourceObservation(observed.reference, observed.trace,
            merge_catalogues(*catalogues), frozenset(hits))

    def observe_transactions(self, trace, expected):
        """Retain task-goal hits from inputs and the reference outputs just computed."""
        from .contract import Direction

        monitor = self._transaction_monitor
        if monitor is None:
            return
        streams = dict(expected)

        def accepted(endpoint_name):
            return (payload for record in trace.records for name, payload in record.accepted
                    if name == endpoint_name and payload is not None)

        for endpoint in monitor._groups:
            if endpoint.direction is Direction.IN:
                streams[endpoint.name] = accepted(endpoint.name)
        self._transaction_hits = monitor.hits_for_streams(streams)

    def observe_reference_transactions(self, record, index, outputs):
        """Check task obligations on outputs from this same reference invocation.

        Aggregated endpoint streams lose interleaving. Feed the ordered record now,
        replacing only outputs with the values independently produced by B*.
        A broken reference obligation is not evidence against the candidate.
        """
        monitor = self._declared_monitor
        if monitor is None:
            return
        monitor.observe(record, index, outputs=outputs)
        if monitor.violations or monitor.unresolved:
            raise FailureRaised(RunFailure.reference("reference-monitor", Refusal(
                "reference-transaction-monitor",
                "the frozen reference violates or cannot establish a declared transaction obligation",
                slot=index, violations=[item.as_dict() for item in monitor.violations],
                unresolved=[item.as_dict() for item in monitor.unresolved])))

    @property
    def has_transaction_monitors(self):
        return self._declared_monitor is not None

    def before_reset(self, resets, expected, index):
        """Record a cut before reference reset and before this slot's outputs."""
        reset_domains = set(resets)
        for name, (domain, policy) in self.outputs.items():
            if domain not in reset_domains:
                continue
            offsets = len(expected.get(name, ())), self.observed[name]
            previous = self.cuts[name][-1][1:3] if self.cuts[name] else (0, 0)
            if offsets != previous:
                self.cuts[name].append((index, *offsets, policy))

    def observe(self, record):
        from .compare import observed_in_record

        try:
            for name in self.outputs:
                self.observed[name] += len(observed_in_record(
                    record, name, behavior=self.behavior))
        except Refused as exc:
            raise FailureRaised(RunFailure.candidate(
                "candidate-output", exc.refusal)) from None
