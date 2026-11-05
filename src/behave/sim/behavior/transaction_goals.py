"""Task transaction predicates in the existing Behavior step relation.

Inputs are accepted logical values; outputs are the normal emitted prefix, not
padding cells. Local queries fix history from an audited prefix. Whole-history
queries carry that same history across the complete, failure-absorbing run.
"""

import operator

import z3

from ..core.catalogue import CatalogueManifest
from ..core.contract import Direction
from ..core.finite_goal import _value_key
from ..core.observation_monitor import TransactionHistory, execution_monitor_for
from ..core.observation_product import TransactionProduct
from ..core.observation_symbolic import _compare, condition_terms, predicate_term
from ..symbolic.encoder import COUNT
from ..symbolic.bitvectors import widen
from ..symbolic.validation import EncodingError


class _HistoryAlgebra:
    bool = staticmethod(z3.BoolVal)
    integer = staticmethod(z3.IntVal)
    land = staticmethod(z3.And)
    lor = staticmethod(z3.Or)
    lnot = staticmethod(z3.Not)
    eq = staticmethod(operator.eq)
    add = staticmethod(operator.add)
    sub = staticmethod(operator.sub)
    gt = staticmethod(operator.gt)

    @staticmethod
    def count(values):
        terms = tuple(z3.If(value, 1, 0) for value in values)
        return z3.Sum(*terms) if terms else z3.IntVal(0)

    @staticmethod
    def zero_like(value):
        return z3.BoolVal(False) if z3.is_bool(value) else z3.IntVal(0)

    @staticmethod
    def ite(condition, when_true, when_false):
        # Position plans share a scalar width. Unplanned diagnostic encodings
        # may use different widths; widening must not truncate logical values.
        return (z3.If(condition, when_true, when_false) if not z3.is_bv(when_true) else
                z3.If(condition, *widen(when_true, when_false)))


class _LatencyAlgebra(_HistoryAlgebra):
    """Exact QF_BV counters sized from this query's finite transaction bound.

    A sign bit above the maximum count/bin literal prevents wraparound. Key
    values keep their own lossless widths; the counter width never narrows them.
    """

    def __init__(self, rules, count_bound):
        self.width = max(count_bound, *(bound for rule in rules for row in rule.bins
                                       for bound in (row.lower, row.upper or 0))).bit_length() + 1

    def integer(self, value):
        return z3.BitVecVal(value, self.width)

    @staticmethod
    def literal(value):
        # SolverIR payload scalars, including Boolean keys, are signed BVs.
        # Monitor flags use bool(); a native Boolean payload must not become a
        # Bool key compared with a current 0/1 BV payload.
        scalar = int(value) if type(value) is bool else value
        return z3.BitVecVal(scalar, scalar.bit_length() + 1)

    @staticmethod
    def eq(a, b):
        return _compare("eq", a, b)

    @staticmethod
    def zero_like(value):
        return z3.BoolVal(False) if z3.is_bool(value) else z3.BitVecVal(0, value.size())

    def count(self, values):
        return sum((z3.If(value, self.integer(1), self.integer(0)) for value in values), self.integer(0))


def transaction_algebra(product, count_bound):
    return _LatencyAlgebra(product.latency.rules, count_bound) if product.latency.rules else _HistoryAlgebra


def symbolic_payload(proc, endpoint, fields, values, role, index=None):
    """Project one logical endpoint through the procedure's typed bindings."""
    fields = dict(fields)
    payload = []
    for field in endpoint.fields:
        if field.type.kind not in ("integer", "boolean"):
            payload.append(None)  # An unreferenced structured field.
            continue
        logical = fields[field.name]
        binding = proc.semantics.value_binding(role, logical, required=False)
        if binding is None:
            value = values[logical]
            payload.append(value if index is None else value[index])
            continue
        leaves = binding.leaf_bindings
        if len(leaves) != 1 or leaves[0][0] != ():
            raise EncodingError("a scalar transaction goal needs its scalar logical binding", kind="shape")
        leaf = leaves[0][1]
        value = values[leaf.variable]
        if index is not None:
            value = value[index]
        for child in leaf.indices:
            value = value[child]
        payload.append(value)
    return tuple(payload)


def symbolic_output_samples(proc, endpoint, fields, encoded):
    """Only the normal emitted prefix is sampled, not buffer padding."""
    from ..core.observation_symbolic import _compare

    capacity = proc.output_capacity
    if capacity is None:
        raise EncodingError("transaction queries require a declared output capacity", kind="shape")
    count = 1 if proc.count is None else encoded.post[COUNT]
    return tuple((z3.And(encoded.ok, _compare("le", count, capacity), _compare("gt", count, index)),
                  symbolic_payload(proc, endpoint, fields, encoded.post, "output", index))
                 for index in range(capacity))


class TransactionQueries:
    """One typed endpoint binding, reused for source feedback and local queries."""

    def __init__(self, contract, boundary):
        self.history = TransactionHistory(contract)
        self.native_monitor = execution_monitor_for(contract, "behavior")
        self.product = TransactionProduct(contract, artifact_kind="behavior")
        self.catalogue = CatalogueManifest(self.product.goals)
        self.rules = {rule.declaration().id: rule for rule in contract.observation_goals}
        self.endpoints = {endpoint.name: endpoint for endpoint in contract.endpoints}
        observed = {endpoint.name for endpoint in self.product.groups}
        self.inputs = {row.endpoint: row.fields for row in boundary.inputs if row.endpoint in observed}
        self.outputs = {row.endpoint: row.fields for row in boundary.outputs if row.endpoint in observed}

    def observe(self, previous, inputs, outputs):
        samples = {}
        for projections, transactions in ((self.inputs, (inputs,)), (self.outputs, outputs)):
            for name, fields in projections.items():
                endpoint = self.endpoints[name]
                samples[name] = []
                for row in transactions:
                    payload = tuple(row[logical] for _public, logical in fields)
                    samples[name].append(payload)
        return self.history.observe(previous, samples)

    def at(self, proc, previous):
        return TransactionQuery(self, proc, previous)

    def encode_history(self, proc, run):
        """One predicate per step, gated by actual invocation/emission events.

        History is analysis state, not model state. In particular, a stateless
        model cannot collapse a consecutive-observation query to one invocation.
        An invocation which fails still accepts its input, but emits no output;
        the absorbed suffix accepts neither inputs nor outputs.
        """
        algebra = transaction_algebra(self.product, run.steps * max(1, proc.output_capacity or 0))
        state = self.product.initial(algebra,
                                     zero=lambda _kind: z3.BitVecVal(0, 1))
        components = {goal: [] for goal in self.catalogue}
        states = [state]
        query = self.at(proc, {})
        for index, encoded in enumerate(run.encodings):
            active = z3.Not(run.halted[index])
            samples = {}
            for endpoint in self.product.groups:
                if endpoint.direction is Direction.IN:
                    samples[endpoint.name] = ((active, query._payload(
                        endpoint, "input", encoded)),)
                else:
                    samples[endpoint.name] = tuple(
                        (z3.And(active, emitted), payload)
                        for emitted, payload in query.output_samples(endpoint, encoded))
            state, hits = self.product.advance(
                state, samples, algebra, predicate=predicate_term, condition=condition_terms)
            states.append(state)
            for goal, hit in hits.items():
                components[goal].append(hit)
        return tuple(states), {goal: tuple(terms) for goal, terms in components.items()}

    def audit_history(self, instance, traces):
        """Compare every encoded hit with the independent native predicate.

        The ordinary step audit checks state and emitted values; source audit
        separately checks those values against Python execution. This check adds
        the finite history and predicate interpretation, including the halt gate.
        It reuses one unrolling per bound, but never reuses an audit result.
        """
        from . import audit, residual

        run = instance._prepared_transaction_audit
        steps = instance.bound or max(map(len, traces))
        if run is None or run.steps != steps:
            run = residual.unroll(instance.proc, instance.env, steps,
                                  plan=instance._position_plan, transactions=self)
            instance._prepared_transaction_audit = run
        findings = []
        for trace in traces:
            solver = z3.Solver()
            solver.add(run.constraint)
            for encoded, inputs in zip(run.encodings, trace, strict=False):
                for name in instance.proc.inputs:
                    audit._assert_value(solver, encoded.pre[name], inputs[name])
            model = audit._audit_model(solver)
            if model is None:
                findings.append("transaction-history audit inputs have no encoded execution")
                continue
            _states, _outputs, hit_sets, _error = instance._run_trace(trace)
            for goal, terms in run.transaction_components.items():
                for index, expected in enumerate(hit_sets):
                    actual = z3.is_true(model.eval(terms[index], model_completion=True))
                    if actual != (goal in expected):
                        findings.append(f"transaction-history hit differs at step {index}: {goal}")
        return findings


class TransactionQuery:
    def __init__(self, binding, proc, previous):
        self.binding, self.proc, self.previous = binding, proc, previous
        self.goals = binding.catalogue

    def input_only(self, goal):
        if goal in self.binding.product.latency.owners:
            rule = self.binding.product.latency.owners[goal]
            return all(self.binding.endpoints[name].direction is Direction.IN
                       for name in (rule.start.endpoint, rule.end.endpoint, rule.timebase.source))
        if goal in self.binding.product.monitors.owners:
            return all(self.binding.endpoints[name].direction is Direction.IN
                       for name in self.binding.product.monitors.owners[goal].order)
        return self.binding.endpoints[self.binding.rules[goal].endpoint].direction is Direction.IN

    def _payload(self, endpoint, role, encoded, index=None):
        projections = self.binding.inputs if role == "input" else self.binding.outputs
        values = encoded.pre if role == "input" else encoded.post
        return symbolic_payload(self.proc, endpoint, projections[endpoint.name], values, role, index)

    def conditions(self, encoded, goals):
        terms = {}
        for goal in goals:
            if goal not in self.binding.rules:
                continue
            rule = self.binding.rules[goal]
            endpoint = self.binding.endpoints[rule.endpoint]
            previous = self.previous.get(endpoint)
            if endpoint.direction is Direction.IN:
                terms[goal] = predicate_term(rule, endpoint,
                    self._payload(endpoint, "input", encoded), previous)
                continue
            samples = []
            for emitted, current in self.output_samples(endpoint, encoded):
                samples.append(z3.And(emitted, predicate_term(rule, endpoint, current, previous)))
                previous = current
            terms[goal] = z3.Or(samples)
        product = self.binding.product.monitors
        monitors = {product.owners[goal].name for goal in goals if goal in product.owners}
        if monitors:
            state, samples = product.initial(_HistoryAlgebra), {}
            for spec in product.specs:
                if spec.name not in monitors:
                    continue
                known, before = self.previous.get(("finite-monitor", spec.name), (True, spec.monitor.initial))
                if not known:
                    raise EncodingError("a local monitor query needs a readable native prefix", kind="shape")
                state[spec.name] = tuple(z3.BoolVal(_value_key(value) == _value_key(before))
                                        for value in spec.monitor.states)
                for name in spec.order:
                    endpoint = self.binding.endpoints[name]
                    samples[name] = (((z3.BoolVal(True), self._payload(endpoint, "input", encoded)),)
                                     if endpoint.direction is Direction.IN else
                                     self.output_samples(endpoint, encoded))
            _after, hits = product.advance(state, samples, _HistoryAlgebra,
                                          condition=condition_terms, monitor_names=monitors)
            terms.update((goal, hits[goal]) for goal in goals if goal in product.owners)
        product = self.binding.product.latency
        names = {product.owners[goal].name for goal in goals if goal in product.owners}
        if names:
            prefix_count = max((self.previous[("latency", name)][1]
                                for name in names if ("latency", name) in self.previous), default=0)
            algebra = _LatencyAlgebra(product.rules, prefix_count + max(1, self.proc.output_capacity or 0))
            state, samples = product.initial(algebra), {}
            for rule in product.rules:
                if rule.name not in names:
                    continue
                try:
                    state[rule.name] = product.prefix(rule, self.previous.get(("latency", rule.name)), algebra)
                except ValueError as exc:
                    raise EncodingError(str(exc), kind="shape") from exc
            for name, endpoint in product.endpoints.items():
                samples[name] = (((z3.BoolVal(True), self._payload(endpoint, "input", encoded)),)
                                 if endpoint.direction is Direction.IN else self.output_samples(endpoint, encoded))
            _after, hits = product.advance(state, samples, algebra, condition=condition_terms, names=names)
            terms.update((goal, hits[goal]) for goal in goals if goal in product.owners)
        return terms

    def output_samples(self, endpoint, encoded):
        return symbolic_output_samples(self.proc, endpoint, self.binding.outputs[endpoint.name], encoded)
