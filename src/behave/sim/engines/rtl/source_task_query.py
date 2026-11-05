"""Local source-goal queries through changing protocol and memory state."""

import time
from dataclasses import replace

import z3

from ...symbolic.exact_solve import z3_check
from ...symbolic.encoder import encode_concrete_value
from ...symbolic.validation import EncodingError
from ...core.construction_checkpoint import ConstructionCheckpoint
from ...core.contract import Kind
from ...core.physical_value import pack_value, packed_width
from ...core.monitor import INPUT_COMPLETION
from ...core.observation_product import TransactionProduct
from ...core.query import SolverAnswer
from ...core.refusal import Refused
from ...rtl.query_deadline import QueryDeadline, is_solver_cancellation
from ...rtl.source_task import SourceTaskRelation
from .. import probing


class SourceTaskQuery:
    """One bound local question's unfinished product, never a solver answer."""

    def __init__(self, machine, template, binding, request, segment):
        self.arguments = machine, template, binding, request, segment
        self.checkpoint = ConstructionCheckpoint()
        self.restart()

    def matches(self, machine, template, binding, request, segment):
        old_machine, old_template, old_binding, old_request, old_segment = self.arguments
        return (machine is old_machine and template is old_template and binding is old_binding
                and request.identity_bytes == old_request.identity_bytes
                and segment.as_dict() == old_segment.as_dict())

    def restart(self):
        self.product = None
        self.broken = False
        self.yields = 0
        self.queried = False
        self.context = z3.Context()
        self.steps = _construct_source_task(*self.arguments, self.context, self.checkpoint)

    def advance(self, check):
        self.checkpoint.callback = check
        try:
            while self.product is None:
                # A deadline here preserves the complete Python/Z3 construction
                # stack. An exception inside next() instead closes that stack;
                # it must be rebuilt, never mistaken for a completed product.
                self.checkpoint()
                try:
                    next(self.steps)
                    self.yields += 1
                except StopIteration as done:
                    self.product, self.steps = done.value, None
                except BaseException:
                    self.steps, self.broken = None, True
                    raise
            self.checkpoint()
            return self.product
        finally:
            self.checkpoint.callback = None


def query_source_task(machine, template, binding, request, segment, *, timeout_ms,
                      _construction=None):
    started = time.monotonic()

    def result(outcome, **kwargs):
        return probing.ProbeResult(outcome, probing.Scope.RTL_LOCAL_HIT,
            solve_ms=(time.monotonic()-started)*1000, **kwargs)

    try:
        construction = _construction
        if construction is None:
            construction = SourceTaskQuery(machine, template, binding, request, segment)
        elif not construction.matches(machine, template, binding, request, segment):
            raise ValueError('source task construction belongs to a different local question')
        elif construction.broken:
            construction.restart()
        context = construction.context
        with QueryDeadline(context, timeout_ms, started=started) as deadline:
            return _query_source_task(machine, template, binding, request, segment,
                                      context, deadline.remaining_ms, result, construction)
    except TimeoutError as exc:
        if (_construction is not None and not construction.broken
                and construction.yields and not construction.queried):
            return result(probing.Outcome.CONSTRUCTING, detail=str(exc))
        return result(probing.Outcome.UNKNOWN, detail=str(exc))
    except z3.Z3Exception as exc:
        if not is_solver_cancellation(exc):
            raise
        return result(probing.Outcome.UNKNOWN, detail="native solver canceled this local query")
    except (EncodingError, KeyError, TypeError, ValueError, Refused) as exc:
        return result(probing.Outcome.UNSUPPORTED, detail=str(exc))


def _construct_source_task(machine, template, binding, request, segment, context, checkpoint):
    yield
    source_names = {goal for _, _, goal in machine.occurrences}
    declared = {goal.id for goal in TransactionProduct(binding.contract).goals}
    source_goals = tuple(goal for goal in request.targets if goal in source_names)
    transaction_goals = tuple(goal for goal in request.targets if goal in declared)
    monitored = len(source_goals) + len(transaction_goals) != len(request.targets)
    monitors = binding.protocol_monitors() if monitored else None
    transactions = (TransactionProduct(binding.contract, goals=transaction_goals)
                    if transaction_goals else None)
    relation = SourceTaskRelation(template, machine, binding.contract, source_goals,
                                  context=context, monitors=monitors, transactions=transactions)
    query = binding.at(request.mutable_fields,
                       state_names=relation.source.relation.source.state_names,
                       stop=segment.stop)
    selected_fields = {(*f.coordinate, f.source_position) for f in segment.mutable_fields}
    if (query.slot != segment.start or not segment.start < segment.stop <= len(binding.seed.slots)
            or segment.seed != binding.seed.fingerprint
            or not {(*f.coordinate, f.source_position) for f in request.mutable_fields} <= selected_fields):
        raise ValueError('source task query must retain its selected seed, fields and actual interval')
    if not machine.boundary_is_quiet(query.state):
        raise ValueError('source task query needs a quiescent native prefix')
    yield
    boundary, memory = binding.product_boundary(query.slot, check=checkpoint)
    monitor_state = None
    if monitors is not None:
        from ...rtl.monitor_template import constant_monitor_state

        prototype = monitors.initial(relation.protocol.algebra,
            lambda value, width: z3.BoolVal(bool(value), ctx=context) if width == 1 else
                                z3.BitVecVal(value, width, ctx=context))
        monitor_state = constant_monitor_state(
            binding.monitor_boundary(query.slot, check=checkpoint), prototype)
    transaction_state, times = None, None
    if transactions is not None:
        transaction_state = binding.transaction_boundary(relation, query.slot, check=checkpoint)
        schedule = binding.seed.clock_schedule
        if schedule is not None:
            times = tuple(entry.time for entry in schedule.slots[segment.start:segment.stop])
    env = relation.source.relation.source.environment
    kinds = dict(machine.procedure.semantics.state_types)
    state = {name: encode_concrete_value(value, env[name], name, kind=kinds[name], context=context)
             for name, value in query.state.items()}
    mutable = {(coordinate.endpoint, coordinate.transaction, coordinate.field): name
               for name, (coordinate, _field) in query.fields.items()}
    variables, queues = {}, {}
    seed_queues = dict(binding.seed.queues)
    for endpoint in binding.contract.inputs():
        rows = []
        for index, row in enumerate(seed_queues.get(endpoint.name, ())):
            yield
            values = []
            for column, (value, field) in enumerate(zip(row, endpoint.fields, strict=True)):
                width = packed_width(field.type)
                name = mutable.get((endpoint.name, index, column))
                if name is not None:
                    term = (z3.Bool(f'payload_{name}', ctx=context) if width == 1 else
                            z3.BitVec(f'payload_{name}', width, ctx=context))
                    variables[name] = term
                else:
                    bits = pack_value(field.type, value)
                    term = (z3.BoolVal(bool(bits), ctx=context) if width == 1 else
                            z3.BitVecVal(bits, width, ctx=context))
                values.append(term)
            rows.append(tuple(values))
        queues[endpoint.name] = tuple(rows)
    enc = yield from relation.encode_steps(state, boundary, queues, binding.seed.slots[segment.start:segment.stop],
        binding.seed.memory_environment, memory_state=memory, slot_offset=segment.start,
        atomic=binding.atomic, monitor_state=monitor_state, transaction_state=transaction_state,
        times=times)
    if INPUT_COMPLETION in request.targets:
        from ...core.input_queue import input_completion_environment

        environment = input_completion_environment(binding.seed, binding.contract)
        if environment.faults:
            raise ValueError('input completion needs a fully released queue and receptive drain')
        pending = z3.Or(*(enc.protocol.pointers[-1][endpoint.name] < len(seed_queues.get(endpoint.name, ()))
                         for endpoint in binding.contract.inputs() if endpoint.kind is Kind.TRANSACTION))
        enc = replace(enc, terminal_hits={INPUT_COMPLETION: z3.And(enc.continuable, pending,
            z3.BoolVal(segment.stop == len(binding.seed.slots) and bool(environment.drain_slots), ctx=context))})
    return enc, variables, query


def _query_source_task(machine, template, binding, request, segment, context, remaining, result,
                       construction):
    enc, variables, query = construction.advance(remaining)
    constraints = (*enc.constraints, *enc.source.memory.validity_constraints)
    # The source storage is packed, but the shared protocol product also has
    # integer adapter states and queue cursors. Absence of memory arrays does
    # not establish QF_BV; use the existing mixed-theory solver for this product.
    # Single-interval source queries retain their actual bit-vector strategy.
    domain_faults = {**enc.domain_faults, **enc.environment_assumptions}
    if domain_faults:
        timeout = remaining()
        construction.queried = True
        answer = z3_check((*constraints, z3.Or(*domain_faults.values())), timeout,
                         _owned_context=context)
        if answer.answer is SolverAnswer.UNKNOWN:
            return result(probing.Outcome.UNKNOWN, detail=f'source operation-domain proof: {answer.reason}')
        if answer.answer is SolverAnswer.SAT:
            causes = dict(machine.domain_checks)
            mark = next(key for key, term in domain_faults.items()
                        if z3.is_true(answer.model.eval(term)))
            cause = causes.get(mark)
            detail = (f'{cause.kind}: {cause.why}' if cause is not None else
                      f'{mark}: local changes can violate monitor definedness or environment legality')
            return result(probing.Outcome.UNSUPPORTED, detail=detail)
    timeout = remaining()
    construction.queried = True
    # The aggregate folds every source/protocol frame. Build it once per
    # question, not once per goal in this batch.
    available_hits = enc.hits
    hits = {goal: available_hits[goal] for goal in request.targets}
    answer = z3_check((*constraints, z3.Or(*hits.values())), timeout,
                     _owned_context=context)
    if answer.answer is SolverAnswer.UNKNOWN:
        return result(probing.Outcome.UNKNOWN, detail=answer.reason)
    if answer.answer is SolverAnswer.UNSAT:
        return result(probing.Outcome.LOCAL_UNSAT,
                      detail=f'fixed recorded prefix and schedule interval [{segment.start}, {segment.stop})')
    values = {name: (int(z3.is_true(answer.model.eval(term, model_completion=True))) if z3.is_bool(term) else
                     answer.model.eval(term, model_completion=True).as_long())
              for name, term in variables.items()}
    goal = next(goal for goal, term in hits.items() if z3.is_true(answer.model.eval(term)))
    return result(probing.Outcome.SAT, goal=goal, candidate=query.proposal(values))
