"""Atomic audits compare latent monitor state, not just current outputs/hits."""


import pytest
import z3

from behave.sim import api
from behave.sim.core.stimulus import AuditedTrace, SlotRecord


@pytest.mark.parametrize('answers,policy,keep', [(None, None, False), (1, None, True),
                                                (2, None, True), (1, 'clears', False)])
def test_query_projection_retains_real_domain_faults_not_an_inert_payload_observer(answers, policy, keep):
    from behave.sim.core.ready_valid_product import OrderedReadyValidConfig
    from behave.sim.rtl.atomic_monitors import AtomicMonitorProduct, ProtocolMonitor
    from tests.sim.test_rtl_source_transactions import _contract

    contract = _contract()
    config = OrderedReadyValidConfig(capacity=2, payload_fields=len(contract.outputs()[0].fields),
                                    answers_per_transaction=answers, reset_policy=policy)
    component = ProtocolMonitor('c', contract.inputs()[0], contract.outputs()[0], config,
                                ('monitor.sink-stability',))
    product = AtomicMonitorProduct((component,), (), {})
    assert bool(product.project(('monitor.input-completion',)).protocol) is keep
    assert product.project(('monitor.sink-stability',)).protocol == (component,)


@pytest.mark.parametrize('answers', [None, 1, 2])
@pytest.mark.parametrize('policy', [None, 'clears', 'keeps'])
def test_control_projection_matches_all_original_goals_and_domain_faults(answers, policy):
    from behave.sim.core.ready_valid_product import OrderedReadyValidConfig
    from behave.sim.rtl.atomic_monitors import AtomicMonitorProduct, ProtocolMonitor
    from behave.sim.rtl.task_symbolic import Z3Algebra
    from tests.sim.test_rtl_source_history import _simple_contract

    contract = _simple_contract()
    source, sink = contract.inputs()[0], contract.outputs()[0]
    goals = ('monitor.output-without-transaction', 'monitor.outstanding-exceeded',
             'monitor.response-deadline')
    config = OrderedReadyValidConfig(capacity=3, payload_fields=len(sink.fields),
        answers_per_transaction=answers, reset_policy=policy, max_outstanding=2,
        response_deadline=None if answers == 1 else 1)
    full = AtomicMonitorProduct((ProtocolMonitor('c', source, sink, config,
                                (*goals, 'monitor.sink-stability')),), (), {})
    projected = full.project(goals)
    assert projected.protocol[0].payload_fields == ()
    assert projected.protocol[0].goals == goals
    algebra = Z3Algebra()
    word = lambda value, width: z3.BitVecVal(value, width) if type(value) is int else value
    left, right = (owner.initial(algebra, word) for owner in (full, projected))
    differences = []
    for i in range(5):
        accepted, valid, ready, reset = z3.Bools(f'accepted{i} valid{i} ready{i} reset{i}')
        raw = {'ov': valid, 'y': z3.BitVec(f'payload{i}', 2)}
        rows = []
        for owner, state in ((full, left), (projected, right)):
            faults, assumptions = {}, {}
            result, hits = owner.step(state, i, api.EventSlot(ticks=('c',)),
                {'in': accepted}, raw, {}, algebra=algebra, boolean=lambda value: value,
                word=word, controls={'ordy': ready}, reset_terms={'c': reset},
                faults=faults, assumptions=assumptions)
            rows.append((result, hits, faults, assumptions))
        left, right = rows[0][0], rows[1][0]
        assert set(rows[1][1]) == set(goals)
        differences.extend(rows[0][1][goal] != rows[1][1][goal] for goal in goals)
        for column in (2, 3):
            assert rows[0][column].keys() == rows[1][column].keys()
            differences.extend(rows[0][column][name] != rows[1][column][name]
                               for name in rows[0][column])
    solver = z3.Solver()
    solver.set(timeout=10_000)
    solver.add(z3.Or(*differences))
    assert solver.check() == z3.unsat


def _source(instance, stimulus, image):
    # Only the original RTL observations cross the replay seam. Monitor state
    # must be reconstructed, never copied from the symbolic product.
    trace = AuditedTrace(
        stimulus, initial_state=image["rtl_states"][0],
        records=tuple(SlotRecord(
            accepted=image["accepted"][i], state=image["rtl_states"][i + 1],
            outputs=image["raw_outputs"][i]) for i in range(len(stimulus.slots))),
        artifact=instance.analysis_identity())
    return instance._source_execution_from_trace(stimulus, trace)


PROTOCOL_FIELDS = (
    "accepted_count", "pending", "owed", "accepted_at", "offered", "reported",
    "stalled", "held",
)
RESET_FIELDS = (
    "reference", "reference_valid", "reference_readable", "seen_non_reset", "reset_streak",
)
STATE_FIELDS = ([(2, "protocol", field) for field in PROTOCOL_FIELDS]
                + [(1, "protocol", field) for field in
                   ("accepted_count", "outstanding", "stalled", "held")]
                + [(1, "reset", field) for field in RESET_FIELDS])


def _changed(value):
    if type(value) is tuple:
        return (_changed(value[0]), *value[1:])
    return z3.Not(value) if z3.is_bool(value) else value + 1


def _leaf(record, family):
    return (record["monitor.ready-valid"]["a"]["a_out"] if family == "protocol" else
            record["monitor.reset-restoration"]["a"])


def test_analysis_worker_rejects_pre_complete_state_evidence():
    from behave.sim.rtl.analysis_job import VERSION, decode_analysis_reply

    reply = {"version": VERSION, "operation": "replay-source", "status": "refused",
             "refusal": {"kind": "test-unavailable", "why": "unavailable"}}
    assert decode_analysis_reply(reply, "replay-source").kind == "test-unavailable"
    with pytest.raises(ValueError, match="expected identity"):
        decode_analysis_reply({**reply, "version": "rtl-exact-analysis/12"}, "replay-source")
