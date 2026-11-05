"""The concrete-record boundary carries a replay, not an input recipe."""

import hashlib
import json
import pickle

import pytest

from behave.sim import api
from behave.sim.symbolic import ir
from behave.sim.behavior.analysis import audit_record
from behave.sim.behavior.instance import BehaviorInstance
from behave.sim.behavior.instance import lower as lower_behavior
from behave.sim.symbolic.uniform_array import UniformArray
from behave.sim.core import audit as audit_module
from behave.sim.core.analysis import AuditDisposition, RecordAudit
from behave.sim.core.audit import (
    AUDITED_WITNESS_RECORD_VERSION,
    AuditCheck,
    AuditCheckStatus,
    AuditedWitness,
    AuditedWitnessLimitError,
    audit_check,
    witness_replay_check,
)
from behave.sim.core.domain import TraceDomain
from behave.sim.core.instance import (
    BoundedInstance,
    Replay,
    StepOutput,
    endpoint_batch_output,
    endpoint_batch_rows,
)
from behave.sim.core.run_record import RunResult
from behave.sim.core.schema import Authority, Reading, TransactionSchema
from behave.sim.core.schema import Endpoint as SchemaEndpoint
from behave.sim.core.stimulus import (
    AuditedTrace,
    EventSlot,
    SlotRecord,
    Stimulus,
    trace_domain,
)


def _witness():
    return AuditedWitness(
        domain="domain-1",
        states=({"x": 0}, {"x": 1}, {"x": 1}),
        inputs=({"a": 1}, {"a": 0}),
        outputs=(({"y": 1},), ()),
        hit_sets=(("branch:T",), ()),
        terminal_error="")


def test_an_audited_witness_is_complete_frozen_and_recoverable():
    witness = _witness()

    assert witness.initial_state == {"x": 0}
    assert witness.final_state == {"x": 1}
    assert witness.inputs == ({"a": 1}, {"a": 0})
    assert witness.outputs == (({"y": 1},), ())
    assert witness.hit_sets == (frozenset({"branch:T"}), frozenset())
    assert witness.hits == {"branch:T"}
    assert len(witness.fingerprint) == 64
    record = witness.as_record()
    assert record["version"] == AUDITED_WITNESS_RECORD_VERSION
    assert record["state_trace"] == {
        "initial": {"x": 0},
        "deltas": [["r", {"x": 1}], None],
    }
    restored = AuditedWitness.from_record(record)
    assert restored.as_dict() == witness.as_dict()
    assert restored.fingerprint == witness.fingerprint
    assert pickle.loads(pickle.dumps(witness)).as_record() == record

    # Views are detached from the canonical record.
    witness.initial_state["x"] = 99
    witness.inputs[0]["a"] = 99
    assert witness.initial_state == {"x": 0}
    assert witness.inputs[0] == {"a": 1}
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        witness._canonical = "forged"


def test_witness_equality_uses_the_complete_canonical_typed_value(monkeypatch):
    witness = _witness()
    restored = AuditedWitness.from_record(witness.as_record())
    assert witness is not restored
    assert witness == restored
    assert hash(witness) == hash(restored)
    assert {witness, restored} == {witness}

    variants = []
    for component in ("inputs", "outputs", "states"):
        image = witness.as_dict()
        if component == "inputs":
            image[component][0]["a"] = True
        elif component == "outputs":
            image[component][0][0]["y"] = True
        else:
            image[component][1]["x"] = True
        changed = AuditedWitness(**image)
        assert changed.as_dict() == witness.as_dict()
        variants.append(changed)

    def no_expansion(_self):
        pytest.fail("witness equality must not expand the complete state trajectory")

    monkeypatch.setattr(AuditedWitness, "as_dict", no_expansion)
    assert witness == restored
    assert all(witness != changed for changed in variants)
    assert witness != {}


def test_startup_hits_are_retained_without_becoming_first_step_hits():
    witness = AuditedWitness(**{**_witness().as_dict(), 'initial_hits': ['startup']})
    assert witness.initial_hits == {'startup'}
    assert witness.hit_sets == _witness().hit_sets
    assert witness.hits == {'startup', 'branch:T'}
    assert witness.fingerprint != _witness().fingerprint
    assert AuditedWitness.from_record(witness.as_record()) == witness
    assert pickle.loads(pickle.dumps(witness)) == witness
    image = witness.as_record()
    image['initial_hits'].clear()
    assert witness.initial_hits == {'startup'}
    replay = Replay(witness.hits, [StepOutput(1, {'y': [1]}), StepOutput(0, {})],
                    state=witness.final_state, steps=2)
    assert witness_replay_check(witness, replay).status is AuditCheckStatus.AGREED
    missing = Replay({'branch:T'}, replay.outputs, state=replay.state, steps=2)
    assert witness_replay_check(witness, missing).status is AuditCheckStatus.INCONSISTENT


@pytest.mark.parametrize('version', ['audited-witness/2', 'audited-witness/3'])
def test_old_compact_witness_keeps_its_original_bytes_and_fingerprint(version):
    record = _witness().as_record()
    record['version'] = version
    record['inputs'] = list(_witness().inputs)
    if version == 'audited-witness/2':
        del record['initial_hits']
    canonical = json.dumps(record, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False, allow_nan=False)
    witness = AuditedWitness.from_record(record)
    assert witness.as_record() == record
    assert witness.fingerprint == hashlib.sha256(canonical.encode()).hexdigest()
    assert witness.initial_hits == frozenset()
    assert witness.hit_sets == _witness().hit_sets
    assert witness.hits == _witness().hits
    assert pickle.loads(pickle.dumps(witness)).as_record() == record
    assert witness.matches_inputs(_witness().inputs)
    assert not witness.matches_inputs([{"a": True}, {"a": 0}])
    with pytest.raises(TypeError, match="non-string key"):
        witness.matches_inputs([{1: 1}, {"a": 0}])


@pytest.mark.parametrize('hits', [None, 'startup', [True], [''], ['b', 'a'], ['a', 'a']])
def test_startup_wire_hits_require_canonical_exact_identifiers(hits):
    record = _witness().as_record()
    record['initial_hits'] = hits
    with pytest.raises((TypeError, ValueError)):
        AuditedWitness.from_record(record)


def test_startup_evidence_cannot_be_silently_removed_or_downgraded():
    record = _witness().as_record()
    del record['initial_hits']
    with pytest.raises(TypeError):
        AuditedWitness.from_record(record)
    record.update(version='audited-witness/2', initial_hits=['startup'])
    with pytest.raises(TypeError):
        AuditedWitness.from_record(record)


def test_audited_witness_rejects_a_boolean_state_delta_index():
    before = [0] * 50
    after = list(before)
    after[1] = 7
    witness = AuditedWitness(
        domain="domain-1",
        states=({"memory": before}, {"memory": after}),
        inputs=({"a": 0},), outputs=((),), hit_sets=((),))
    record = witness.as_record()
    delta = record["state_trace"]["deltas"][0]
    assert delta == ["d", {"memory": ["l", [[1, ["r", 7]]]]}]
    delta[1]["memory"][1][0][0] = True

    with pytest.raises(ValueError, match="not in canonical form"):
        AuditedWitness.from_record(record)


def test_an_audited_witness_fails_closed_before_an_unbounded_evidence_tree(
        monkeypatch):
    monkeypatch.setattr(audit_module, "AUDITED_WITNESS_MAX_NODES", 12)

    with pytest.raises(AuditedWitnessLimitError, match="12-node evidence limit"):
        AuditedWitness(
            domain="domain-1",
            states=({"memory": [0, 0, 0]}, {"memory": [0, 0, 0]}),
            inputs=({"a": 1},),
            outputs=((),),
            hit_sets=((),),
        )


def test_audited_witness_wire_record_does_not_repeat_unchanged_large_state(
        monkeypatch):
    monkeypatch.setattr(audit_module, "AUDITED_WITNESS_MAX_NODES", 180)
    first = [0] * 100
    second = list(first)
    second[17] = 1
    third = list(second)
    third[81] = 2

    witness = AuditedWitness(
        domain="domain-1",
        states=({"memory": first}, {"memory": second}, {"memory": third}),
        inputs=({"a": 1}, {"a": 2}),
        outputs=((), ()),
        hit_sets=((), ()),
    )

    assert witness.final_state["memory"][17] == 1
    assert witness.final_state["memory"][81] == 2
    assert len(witness.as_record()["state_trace"]["deltas"]) == 2
    assert AuditedWitness.from_record(witness.as_record()).as_dict() == \
        witness.as_dict()


def test_witness_compresses_repeated_queues_without_losing_inputs(monkeypatch):
    monkeypatch.setattr(audit_module, "AUDITED_WITNESS_MAX_NODES", 600)
    queue = list(range(100))
    rows = [{"queue": queue, "release": i % 2, "ready": bool(i % 3)} for i in range(20)]
    witness = AuditedWitness(domain="queue-domain", states=[{}] * 21,
        inputs=rows, outputs=[()] * 20, hit_sets=[()] * 20)
    packed = witness.as_record()
    assert audit_module._json_nodes(packed) < 600
    assert audit_module._json_nodes(witness.as_dict()) > 2000
    assert witness.inputs == tuple(rows)
    assert AuditedWitness.from_record(packed).as_dict() == witness.as_dict()
    assert pickle.loads(pickle.dumps(witness)) == witness
    assert witness.matches_inputs(rows)
    queue[0] = -1
    assert not witness.matches_inputs(rows)
    detached = witness.inputs
    detached[0]["queue"][0] = -2
    assert detached[1]["queue"][0] == witness.inputs[0]["queue"][0] == 0


def test_input_deltas_preserve_types_and_signed_zero():
    rows = [{"x": x, "queue": [0] * 50} for x in (0, False, 0.0, -0.0, 1)]
    witness = AuditedWitness(domain="typed-inputs", states=[{}] * 6,
        inputs=rows, outputs=[()] * 5, hit_sets=[()] * 5)
    restored = AuditedWitness.from_record(witness.as_record())
    assert [type(row["x"]) for row in restored.inputs] == [int, bool, float, float, int]
    assert restored.inputs[2]["x"].hex() != restored.inputs[3]["x"].hex()
    assert restored == witness
    assert restored.matches_inputs(rows)
    rows[0]["x"] = False
    assert not restored.matches_inputs(rows)


@pytest.mark.parametrize('edit', ['append', 'prepend', 'insert', 'delete', 'shrink', 'replace'])
def test_variable_length_list_edits_are_lossless_and_canonical(edit):
    before = [{'index': i, 'value': [i, -0.0]} for i in range(40)]
    after = list(before)
    if edit == 'append':
        after.append({'index': 40, 'value': [False, 0.0]})
    elif edit == 'prepend':
        after.insert(0, {'index': -1, 'value': [True, 1]})
    elif edit == 'insert':
        after[17:17] = [{'index': 50, 'value': [0.0]}, {'index': 51, 'value': [None]}]
    elif edit == 'delete':
        del after[17:19]
    elif edit == 'shrink':
        after = []
    else:
        after = ['different']
    rows = [{'queue': before}, {'queue': after}]
    witness = AuditedWitness(domain='list-edits', states=[rows[0], *rows], inputs=rows,
                             outputs=[(), ()], hit_sets=[(), ()])
    restored = AuditedWitness.from_record(witness.as_record())
    assert restored == witness
    assert restored.matches_inputs(rows)
    assert audit_module._same_json_value(restored.final_state, rows[-1])
    assert audit_module._same_json_value(list(restored.inputs), rows)
    assert pickle.loads(pickle.dumps(restored)) == witness
    detached = restored.final_state
    detached['queue'].append('changed')
    assert restored.matches_inputs(rows)


def test_cumulative_lists_fit_the_original_envelope_without_omitting_states(monkeypatch):
    monkeypatch.setattr(audit_module, 'AUDITED_WITNESS_MAX_NODES', 10000)
    states = [{'log': [{'id': str(j), 'at': j} for j in range(i)]} for i in range(201)]
    witness = AuditedWitness(domain='cumulative', states=states, inputs=[{}] * 200,
                             outputs=[()] * 200, hit_sets=[()] * 200)
    wire = witness.as_record()
    legacy = audit_module.encode_valuation_trace(states, 'states')
    assert audit_module._json_nodes(legacy) > 10000
    assert audit_module._json_nodes(wire) < 10000
    assert witness.states == tuple(states)
    assert AuditedWitness.from_record(wire).states == tuple(states)


@pytest.mark.parametrize('version', ['audited-witness/2', 'audited-witness/3', 'audited-witness/4', 'audited-witness/5'])
def test_old_growing_lists_keep_original_canonical_bytes(version):
    rows = [{'queue': list(range(i))} for i in (30, 31)]
    states = [rows[0], *rows]
    wire = AuditedWitness(domain='old-lists', states=states, inputs=rows,
                          outputs=[(), ()], hit_sets=[(), ()]).as_record()
    wire['version'] = version
    wire['state_trace'] = audit_module.encode_valuation_trace(states, 'states', splices=version == 'audited-witness/5')
    wire['inputs'] = (audit_module.encode_valuation_trace(rows, 'inputs', splices=version == 'audited-witness/5')
                      if version in ('audited-witness/4', 'audited-witness/5') else rows)
    if version == 'audited-witness/2':
        del wire['initial_hits']
    canonical = json.dumps(wire, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    restored = AuditedWitness.from_record(wire)
    assert restored.fingerprint == hashlib.sha256(canonical.encode()).hexdigest()
    assert restored.as_record() == wire
    assert restored.states == tuple(states)
    assert restored.inputs == tuple(rows)
    assert restored.matches_inputs(rows)
    assert pickle.loads(pickle.dumps(restored)).as_record() == wire


@pytest.mark.parametrize('value', [0, False, 0.0, -0.0, True, 1])
def test_splices_preserve_typed_middle_edits(value):
    before = [0] * 30
    after = [*before[:15], value, *before[15:]]
    trace = audit_module.encode_valuation_trace([{'q': before}, {'q': after}], 'trace', splices=True)
    audit_module._validate_trace(trace, 'trace', splices=True)
    result = tuple(audit_module._expand_trace(trace))[-1]['q']
    assert audit_module._same_json_value(result, after)


@pytest.mark.parametrize('payload', [[True, 0, []], [0, False, []], [-1, 0, []],
                                    [51, 0, []], [0, 51, []], [0, 0, {}]])
def test_splices_reject_invalid_indices_and_payloads(payload):
    with pytest.raises(ValueError, match='splice'):
        audit_module._apply_delta([0] * 50, ['s', payload])


def test_splices_cannot_be_relabelled_as_old_wire_or_use_noncanonical_edits():
    states = [{'queue': list(range(i))} for i in (30, 31)]
    wire = AuditedWitness(domain='splice-wire', states=states, inputs=[{}],
                          outputs=[()], hit_sets=[()]).as_record()
    assert wire['state_trace']['deltas'][0][1]['queue'][0] == 's'
    wire['version'] = 'audited-witness/4'
    with pytest.raises(ValueError, match='canonical'):
        AuditedWitness.from_record(wire)
    wire['version'] = AUDITED_WITNESS_RECORD_VERSION
    wire['state_trace']['deltas'][0][1]['queue'] = ['s', [29, 1, [29, 30]]]
    with pytest.raises(ValueError, match='canonical'):
        AuditedWitness.from_record(wire)


def test_validation_reuses_unchanged_state_but_still_requires_canonical_changes():
    # Validation shares the unchanged subtrees of successive states instead of
    # copying and walking all of them at every step. A change inside such a
    # subtree must still have its canonical spelling, and a growing log must
    # still decode to every state.
    states = []
    for step in range(12):
        nested = {'x': [0] * 32, 'y': 'same'}
        if step >= 6:
            nested['x'][5] = 1
        states.append({'big': {'cells': list(range(64)), 'nested': nested},
                       'count': step, 'log': [[i, -0.0] for i in range(step)]})
    wire = AuditedWitness(domain='shared-validation', states=states, inputs=[{}] * 11,
                          outputs=[()] * 11, hit_sets=[()] * 11).as_record()
    restored = AuditedWitness.from_record(wire)
    assert restored == AuditedWitness.from_record(wire)
    assert restored.final_state == states[-1]
    big = wire['state_trace']['deltas'][5][1]['big']
    assert big[0] == 'd'
    wire['state_trace']['deltas'][5][1]['big'] = ['r', states[6]['big']]
    with pytest.raises(ValueError, match='canonical'):
        AuditedWitness.from_record(wire)


@pytest.mark.parametrize('deleting', (False, True))
@pytest.mark.parametrize('value', [False, 0, 0.0, -0.0, None, {'nested': [False, -0.0]}])
def test_scattered_sequence_edits_preserve_all_typed_values(deleting, value):
    before = [{'index': i, 'value': [i, -0.0]} for i in range(60)]
    after = [*before[:4], value, *before[4:43], value, *before[43:]]
    if deleting:
        before, after = after, before
    rows = [{'q': before}, {'q': after}]
    witness = AuditedWitness(domain='scattered', states=[rows[0], *rows],
                             inputs=rows, outputs=[(), ()], hit_sets=[(), ()])
    wire = witness.as_record()
    assert wire['inputs']['deltas'][0][1]['q'][0] == 'e'
    restored = AuditedWitness.from_record(wire)
    assert audit_module._same_json_value(restored.final_state, rows[-1])
    assert restored.matches_inputs(rows)
    assert restored.as_record() == wire
    assert pickle.loads(pickle.dumps(restored)) == witness
    # The prior codec remains independently readable with its original bytes.
    wire['version'] = 'audited-witness/5'
    with pytest.raises(ValueError, match='canonical'):
        AuditedWitness.from_record(wire)
    wire['state_trace'] = audit_module.encode_valuation_trace([rows[0], *rows], 'states', splices=True)
    wire['inputs'] = audit_module.encode_valuation_trace(rows, 'inputs', splices=True)
    old = AuditedWitness.from_record(wire)
    assert old.as_record() == wire and old.matches_inputs(rows)
    assert old.fingerprint == hashlib.sha256(json.dumps(
        wire, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def test_scattered_cumulative_lists_fit_without_omitting_events(monkeypatch):
    values = set(range(0, 1600, 4))
    states = [{'events': sorted(values)}]
    for index in range(100):
        values.update((4 * index + 1, 4 * index + 801))
        states.append({'events': sorted(values)})
    old = audit_module.encode_valuation_trace(states, 'states', splices=True)
    assert audit_module._json_nodes(old) > 10000
    monkeypatch.setattr(audit_module, 'AUDITED_WITNESS_MAX_NODES', 10000)
    witness = AuditedWitness(domain='scattered-growth', states=states, inputs=[{}] * 100,
                             outputs=[()] * 100, hit_sets=[()] * 100)
    assert audit_module._json_nodes(witness.as_record()) < 10000
    assert witness.states == tuple(states)
    assert AuditedWitness.from_record(witness.as_record()).states == tuple(states)


@pytest.mark.parametrize('before,after', [
    ([0, False, 0.0, -0.0], [0, False, False, 0.0, -0.0]),
    ([0, 0, 0, 1], [0, 0, 1]),
    ([1, 2, 3], [3, 2, 1, 4]),
    ([1, 2, 3, 4], [1, 9, 4]),
    ([], [None, False]), ([1, 1], []),
])
def test_subsequence_edits_never_confuse_repeats_reordering_or_replacements(before, after):
    edit = audit_module._subsequence_edits(before, after)
    if edit is not None:
        assert audit_module._same_json_value(audit_module._apply_delta(before, edit), after)
    rows = [{'q': before}, {'q': after}]
    trace = audit_module.encode_valuation_trace(rows, 'trace', splices=True, sequence_edits=True)
    audit_module._validate_trace(trace, 'trace', splices=True, sequence_edits=True)
    assert audit_module._same_json_value(list(audit_module._expand_trace(trace)), rows)


@pytest.mark.parametrize('edits', [[], {}, [[True, 0, []]], [[0, 1, []], [0, 0, [2]]],
                                 [[0, 3, []], [2, 1, []]], [[2, 1, []], [1, 0, []]],
                                 [[1, 0, {}]], [[0, 9, []]]])
def test_scattered_edits_reject_invalid_or_overlapping_coordinates(edits):
    with pytest.raises(ValueError, match='splice'):
        audit_module._apply_delta([0, 1, 2, 3], ['e', edits])


def test_compact_inputs_require_complete_canonical_deltas():
    before = {"queue": [0] * 50}
    after = {"queue": [0, 7] + [0] * 48}
    witness = AuditedWitness(domain="queue-domain", states=[{}] * 3,
        inputs=[before, after], outputs=[()] * 2, hit_sets=[()] * 2)
    record = witness.as_record()
    record["inputs"]["deltas"][0][1]["queue"][1][0][0] = True
    with pytest.raises(ValueError, match="not in canonical form"):
        AuditedWitness.from_record(record)
    record = witness.as_record()
    record["inputs"]["deltas"].clear()
    with pytest.raises(ValueError, match="one state successor"):
        AuditedWitness.from_record(record)


@pytest.mark.parametrize("version", ["audited-witness/2", "audited-witness/3"])
def test_old_witness_versions_do_not_accept_new_compact_inputs(version):
    record = _witness().as_record()
    record["version"] = version
    if version == "audited-witness/2":
        del record["initial_hits"]
    with pytest.raises(TypeError, match="input trace"):
        AuditedWitness.from_record(record)


def test_audited_witness_state_deltas_preserve_signed_float_zero():
    witness = AuditedWitness(
        domain="domain-1",
        states=({"x": -0.0}, {"x": 0.0}),
        inputs=({"a": 0},), outputs=((),), hit_sets=((),))

    assert witness.states[0]["x"].hex() == (-0.0).hex()
    assert witness.states[1]["x"].hex() == (0.0).hex()
    assert witness.as_record()["state_trace"]["deltas"] != [None]


def test_replay_and_step_output_are_closed_deeply_frozen_records():
    buffers = {"y": [[1, 2]]}
    state = {"memory": [3, 4]}
    output = StepOutput(1, buffers)
    replay = Replay({"g"}, [output], state=state, steps=1)

    buffers["y"][0][0] = 99
    state["memory"][0] = 99
    output.buffers["y"][0][0] = 88
    replay.outputs.clear()
    replay.hits.clear()
    replay.state["memory"][0] = 88

    assert output.transactions == [{"y": [1, 2]}]
    assert replay.outputs == [output]
    assert replay.hits == {"g"}
    assert replay.state == {"memory": [3, 4]}
    assert pickle.loads(pickle.dumps(replay)).as_dict() == replay.as_dict()
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        replay.error = "forged"
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        output.count = 0


def test_endpoint_batch_envelope_is_an_injective_sparse_round_trip():
    batch = (
        {"left": {"data": 1}},
        {"right": {"data": 2}},
        {"left": {"data": 3}},
    )

    output = endpoint_batch_output(batch)

    assert output.count == 3
    assert output.transactions == [
        {"@endpoint": "left", "@payload": {"data": 1}},
        {"@endpoint": "right", "@payload": {"data": 2}},
        {"@endpoint": "left", "@payload": {"data": 3}},
    ]
    assert endpoint_batch_rows(output) == batch
    assert endpoint_batch_rows(endpoint_batch_output(())) == ()

    # Record key iteration is not semantic.  The recursive frozen carrier gives the
    # same envelope to the same endpoint payload whichever insertion order built it.
    assert endpoint_batch_output(({"left": {"b": 2, "a": 1}},)) == \
        endpoint_batch_output(({"left": {"a": 1, "b": 2}},))


def test_witness_replay_compares_the_semantic_endpoint_batch():
    batch = ({"left": {"data": 1}}, {"right": {"data": 2}})
    witness = AuditedWitness(
        domain="domain-1",
        states=({"x": 0}, {"x": 1}),
        inputs=({"a": 1},),
        outputs=(batch,),
        hit_sets=((),),
    )
    replay = Replay(
        (), [endpoint_batch_output(batch)], state={"x": 1}, steps=1)

    assert witness_replay_check(
        witness, replay).status is AuditCheckStatus.AGREED


@pytest.mark.parametrize(
    "recorded,replayed",
    [
        (True, 1),
        (-0.0, 0.0),
    ],
)
def test_witness_replay_does_not_coerce_typed_numeric_values(
        recorded, replayed):
    witness = AuditedWitness(
        domain="domain-1",
        states=({"x": recorded}, {"x": recorded}),
        inputs=({"a": 0},), outputs=((),), hit_sets=((),))
    replay = Replay((), [StepOutput(0)], state={"x": replayed}, steps=1)

    got = witness_replay_check(witness, replay)

    assert got.status is AuditCheckStatus.INCONSISTENT
    assert got.findings[0].startswith("final_state:")


def test_endpoint_batch_decoder_never_accepts_dense_none_padding():
    dense = StepOutput(2, {
        "left": ({"data": 1}, None),
        "right": (None, {"data": 2}),
    })
    with pytest.raises(ValueError, match="only its endpoint and payload"):
        endpoint_batch_rows(dense)


@pytest.mark.parametrize(
    "batch,match",
    [
        (({"left": {}, "right": {}},), "one exact endpoint"),
        (({"": {}},), "nonempty endpoint"),
        (({"left": None},), "exact record"),
    ],
)
def test_endpoint_batch_envelope_refuses_ambiguous_rows(batch, match):
    with pytest.raises(TypeError, match=match):
        endpoint_batch_output(batch)


@pytest.mark.parametrize("count", [True, -1, 1.0])
def test_step_output_never_coerces_a_count(count):
    with pytest.raises((TypeError, ValueError), match="count"):
        StepOutput(count)


def test_replay_rejects_open_or_untyped_fields_at_construction():
    with pytest.raises(TypeError, match="finite built-in collection"):
        Replay(iter(("g",)))
    with pytest.raises(TypeError, match="StepOutput"):
        Replay((), outputs=[object()])
    with pytest.raises(TypeError, match="modeled replay error"):
        Replay((), error=1)
    with pytest.raises(TypeError, match="finite record carrier"):
        Replay((), state=object())


@pytest.mark.parametrize(
    "change,match",
    [
        ({"states": ({"x": 0},)}, "needs 3 states"),
        ({"outputs": (({"y": 1},),)}, "one output batch"),
        ({"hit_sets": (("branch:T",),)}, "one output batch"),
    ],
)
def test_a_witness_cannot_omit_a_step_component(change, match):
    fields = {
        "domain": "domain-1",
        "states": ({"x": 0}, {"x": 1}, {"x": 1}),
        "inputs": ({"a": 1}, {"a": 0}),
        "outputs": (({"y": 1},), ()),
        "hit_sets": (("branch:T",), ()),
    }
    fields.update(change)
    with pytest.raises(ValueError, match=match):
        AuditedWitness(**fields)


def test_record_audit_admits_only_the_typed_complete_witness():
    with pytest.raises(TypeError, match="AuditedWitness"):
        RecordAudit.admitted(({"a": 1},))

    witness = _witness()
    record = RecordAudit.admitted(witness)

    assert record.disposition is AuditDisposition.ADMITTED
    assert record.witness is witness
    assert record.trace == witness.inputs
    assert record.as_dict() == {
        "disposition": "admitted",
        "witness": witness.as_record(),
        "witness_ref": "witness:" + witness.fingerprint,
    }


def test_behavior_replay_keeps_per_step_states_hits_outputs_and_halt():
    body = ir.If(
        ir.Op("truth", ir.Var("a")),
        ir.Seq(ir.Assign("x", ir.Lit(1)), ir.Fail("Stopped", "safety:stop")),
        ir.Assign("x", ir.Lit(2)),
    )
    proc = ir.Procedure(
        state=("x",), inputs=("a",), locals=(), outputs=(), body=body,
        initial={"x": 0}, output_capacity=0)
    instance = BehaviorInstance(proc, {})

    replayed, witness = instance.replay_witness(
        ({"a": 1}, {"a": 0}), "domain-1")

    assert replayed.broken is None and replayed.error == "Stopped"
    assert replayed.hits == {"safety:stop"}
    assert witness.states == ({"x": 0}, {"x": 1}, {"x": 1})
    assert witness.outputs == ((), ())
    assert witness.hit_sets == (frozenset({"safety:stop"}), frozenset())
    assert witness.terminal_error == "Stopped"


def test_behavior_replay_exposes_a_compact_array_as_componentwise_state():
    from behave.sim.symbolic import typecheck as value_types

    proc = ir.Procedure(
        state=("memory",), inputs=("a",), locals=(), outputs=(),
        body=ir.Store("memory", ir.Lit(1), ir.Var("a")),
        types={"memory": value_types.Array(3)},
        initial={"memory": UniformArray.uniform(3, 0)},
        output_capacity=0)
    instance = BehaviorInstance(proc, {})

    replayed, witness = instance.replay_witness(
        ({"a": 7},), "domain-uniform-array")

    assert replayed.broken is None
    assert replayed.state == {"memory": [0, 7, 0]}
    assert witness.states == (
        {"memory": [0, 0, 0]},
        {"memory": [0, 7, 0]},
    )


def test_behavior_witness_retains_a_large_external_memory_sparsely():
    from behave.sim.symbolic import typecheck as value_types

    cells = 1 << 16
    proc = ir.Procedure(
        state=("memory",), inputs=("a",), locals=(), outputs=(),
        body=ir.Store("memory", ir.Lit(1), ir.Var("a")),
        types={"memory": value_types.Array(cells)},
        initial={"memory": UniformArray.memory(cells, "memory", 32)},
        output_capacity=0)
    instance = BehaviorInstance(proc, {})

    replayed, witness = instance.replay_witness(
        ({"a": 7},), "domain-external-memory")

    expected_initial = {
        "representation": "compact-array/2",
        "length": cells,
        "base": ["memory-junk/index-mix/2", "memory", 32],
        "writes": [],
    }
    expected_final = {
        **expected_initial,
        "writes": [[1, 7]],
    }
    assert replayed.broken is None
    assert replayed.state == {"memory": expected_final}
    assert witness is not None
    assert witness.states == (
        {"memory": expected_initial},
        {"memory": expected_final},
    )
    assert witness_replay_check(
        witness, instance.replay(({"a": 7},))
    ).status is AuditCheckStatus.AGREED


def test_bound_provider_audits_a_sat_recipe_before_naming_it_a_witness():
    class AgreeingBehavior(BehaviorInstance):
        def audit(self, _traces):
            return AuditCheck.agreed()

        def source_audit(self, _traces):
            return AuditCheck.agreed()

    proc = ir.Procedure(
        state=("x",), inputs=("a",), locals=(), outputs=(),
        body=ir.Assign("x", ir.Var("a")), initial={"x": 0},
        output_capacity=0)
    bound = BoundedInstance(
        AgreeingBehavior(proc, {}),
        TraceDomain(bound=2, contract={"a": [0, 1]},
                    frontend="test/1", policy="test", artifact="artifact"))

    got = bound.audit_request(({"a": 1}, {"a": 0}))

    assert type(got) is AuditedWitness
    assert got.domain == bound.domain.digest()
    assert got.states == ({"x": 0}, {"x": 1}, {"x": 0})


@pytest.mark.parametrize("replacement", [True, 1.0])
def test_bound_gate_preserves_request_value_types(replacement):
    changed = []

    class ChangedRequest(BehaviorInstance):
        def audit(self, _traces):
            return AuditCheck.agreed()

        def source_audit(self, _traces):
            return AuditCheck.agreed()

        def audit_requests(self, traces, domain):
            results = []
            for witness in super().audit_requests(traces, domain):
                assert type(witness) is AuditedWitness
                image = witness.as_dict()
                assert type(image["inputs"][0]["a"]) is int
                image["inputs"][0]["a"] = replacement
                result = AuditedWitness(**image)
                changed.append(result)
                results.append(result)
            return tuple(results)

    proc = ir.Procedure(
        state=("x",), inputs=("a",), locals=(), outputs=(),
        body=ir.Assign("x", ir.Var("a")), initial={"x": 0},
        output_capacity=0)
    bound = BoundedInstance(
        ChangedRequest(proc, {}),
        TraceDomain(bound=2, contract={"a": [0, 1]},
                    frontend="test/1", policy="test", artifact="artifact"))
    rows = ({"a": 1}, {"a": 0})
    assert bound.validate(rows) == []

    got = bound.audit_request(rows)

    assert len(changed) == 1
    assert type(changed[0].inputs[0]["a"]) is type(replacement)
    assert changed[0].inputs == rows  # The old untyped comparison misses this.
    assert type(got) is AuditCheck
    assert got.status is AuditCheckStatus.INCONSISTENT
    assert "another input trace" in got.findings[0]


def test_audit_records_are_closed_and_legacy_shapes_fail_closed():
    with pytest.raises(TypeError, match="cannot be subclassed"):
        class ForgedAudit(AuditCheck):
            pass

    with pytest.raises(TypeError, match="cannot be subclassed"):
        class ForgedWitness(AuditedWitness):
            pass

    assert audit_check([]).status is AuditCheckStatus.REFUSED
    assert audit_check([]).kind == "audit-result-invalid"
    assert audit_check(None).status is AuditCheckStatus.REFUSED

    with pytest.raises(TypeError, match="exact AuditCheckStatus"):
        AuditCheck("agreed")
    with pytest.raises(TypeError, match="no truth value"):
        bool(AuditCheckStatus.AGREED)
    restored = pickle.loads(pickle.dumps(AuditCheck.refused("x", "why")))
    assert restored.as_dict() == {
        "status": "refused", "refusal": {"kind": "x", "why": "why"}}


def test_bound_provider_rejects_an_out_of_domain_recipe_before_replay():
    proc = ir.Procedure(
        state=("x",), inputs=("a",), locals=(), outputs=(),
        body=ir.Assign("x", ir.Var("a")), initial={"x": 0},
        output_capacity=0)
    bound = BoundedInstance(
        BehaviorInstance(proc, {}),
        TraceDomain(bound=2, contract={"a": [0, 1]},
                    frontend="test/1", policy="test", artifact="artifact"))

    got = bound.audit_request(({"a": 1},))

    assert type(got) is AuditCheck
    assert got.status is AuditCheckStatus.INCONSISTENT
    assert "outside its bound domain" in got.findings[0]


def test_audit_refusal_owns_details_and_preserves_them_across_pickle():
    detail = {"failure": {"origin": "candidate", "hits": ["bad-event"]}}
    check = AuditCheck.refused("native-failed", "source stopped", **detail)
    expected = check.as_dict()
    detail["failure"]["hits"].clear()
    check.as_dict()["refusal"]["detail"]["failure"]["hits"].clear()
    assert check.as_dict() == expected
    assert pickle.loads(pickle.dumps(check)).as_dict() == expected
    for status, findings in ((AuditCheckStatus.AGREED, ()),
                             (AuditCheckStatus.INCONSISTENT, ("disagrees",))):
        with pytest.raises(ValueError):
            AuditCheck(status, findings=findings, detail={"failure": "not agreement"})


def test_bound_audit_keeps_provider_refusal_details():
    from behave.sim.core.refusal import Refusal, Refused

    reason = Refusal("native-failed", "source stopped", failure={"origin": "candidate"})

    class FailingAudit(BehaviorInstance):
        def audit_requests(self, traces, domain):
            raise Refused(reason)

    proc = ir.Procedure(state=("x",), inputs=("a",), locals=(), outputs=(),
        body=ir.Assign("x", ir.Var("a")), initial={"x": 0}, output_capacity=0)
    bound = BoundedInstance(FailingAudit(proc, {}), TraceDomain(
        bound=1, contract={"a": [0, 1]}, frontend="test/1", policy="test", artifact="artifact"))
    check = bound.audit_request(({"a": 1},))
    assert check.status is AuditCheckStatus.REFUSED
    assert check.refusal == reason


def test_a_replayed_recipe_is_not_a_witness_when_source_audit_cannot_run():
    class NoSourceBehavior(BehaviorInstance):
        def audit(self, _traces):
            return AuditCheck.agreed()

    proc = ir.Procedure(
        state=("x",), inputs=("a",), locals=(), outputs=(),
        body=ir.Assign("x", ir.Var("a")), initial={"x": 0},
        output_capacity=0)
    # No source is attached, so the IR replay exists but the source conjunct does not.
    instance = NoSourceBehavior(proc, {})

    got = instance.audit_request(({"a": 1},), "domain-1")

    assert type(got) is AuditCheck
    assert got.status is AuditCheckStatus.REFUSED
    assert got.kind == "behavior-source-audit-unavailable"


def test_behavior_record_audit_returns_the_complete_replay_not_just_inputs():
    source = """
class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        self.x = self.x + a
        return {"y": self.x}
"""
    schema = TransactionSchema(
        [SchemaEndpoint("a", "input", 1), SchemaEndpoint("y", "output", 2)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    behavior = lower_behavior(source, schema, bound=1)
    contract = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("a", api.FiniteType(1), "a")],
                api.ready_valid("in", "in_valid", "in_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("y", api.FiniteType(2), "y")],
                api.ready_valid("out", "out_valid", "out_ready"),
                clock="clk", kind=api.Kind.TRANSACTION),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")])
    task = api.admit(
        contract,
        api.Realization(
            "behavior",
            (
                api.Port("a", 1, driven_by="environment", clock="clk"),
                api.Port("y", 2, driven_by="artifact", clock="clk"),
                api.Port("in_valid", 1, driven_by="environment"),
                api.Port("in_ready", 1, driven_by="artifact"),
                api.Port("out_valid", 1, driven_by="artifact"),
                api.Port("out_ready", 1, driven_by="environment"),
                api.Port("clk", 1, driven_by="environment"),
                api.Port("rst", 1, driven_by="environment"),
            ),
                channel_ports={"in": ("a",), "out": ("y",)},
                identity=behavior.identity))
    exact = behavior.bind(1, task_contract=contract.digest)
    replay_hits = exact.instance.replay(({"a": 1},)).hits
    hit_values = [
        (goal, goal in replay_hits)
        for goal in exact.instance.catalogue_manifest()
    ]
    stimulus = Stimulus(
        queues={"in": [(1,)]}, slots=[EventSlot(offers=["in"])], source="test")
    trace = AuditedTrace(
        stimulus,
        records=[SlotRecord(
            accepted={"in": (1,)},
            outputs={"out": ((1,),)},
            hits=hit_values,
        )],
        domain=trace_domain(contract.digest, horizon=1),
        artifact=behavior.identity)
    result = RunResult.not_compared(
        stimulus, trace,
        api.RunFailure.verifier(
            "test-audit-view",
            api.Refusal("test-uncompared", "not needed for this audit")))

    got = audit_record(result, exact, task=task)

    assert got.disposition is AuditDisposition.ADMITTED, got.as_dict()
    assert type(got.witness) is AuditedWitness
    assert got.witness.inputs == ({"a": 1},)
    assert got.witness.states == ({"x": 0}, {"x": 1})
    assert got.witness.outputs == (({"y": 1},),)
    assert got.witness.hit_sets == (frozenset(replay_hits),)

    class OtherTask:
        class contract:
            digest = "another-task-contract"

    wrong = audit_record(result, exact, task=OtherTask())
    assert wrong.disposition is AuditDisposition.REFUSED
    assert wrong.kind == "behavior-record-task-unbound"


def test_behavior_record_audit_does_not_apply_rtl_pin_protocols():
    from behave.sim.core.analysis import audit_environment_assumptions

    source = """\
class M:
    def reset(self):
        self.x = 0

    def process(self, a):
        return {"y": a}
"""
    schema = TransactionSchema(
        [SchemaEndpoint("a", "input", 1), SchemaEndpoint("y", "output", 1)],
        authority=Authority.TASK, reading=Reading.ELABORATED)
    behavior = lower_behavior(source, schema, bound=1)
    contract = api.TaskContract(
        endpoints=[
            api.Endpoint(
                "in", api.Direction.IN,
                [api.Field("a", api.FiniteType(1), "a")],
                api.start_done("in", "start", "done"),
                clock="clk", kind=api.Kind.TRANSACTION,
                reset_policy=api.ResetPolicy.CLEARS),
            api.Endpoint(
                "out", api.Direction.OUT,
                [api.Field("y", api.FiniteType(1), "y")],
                api.start_done("out", "start", "done"),
                clock="clk", kind=api.Kind.TRANSACTION,
                answers_per_transaction=1,
                reset_policy=api.ResetPolicy.CLEARS),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")])
    task = api.admit(
        contract,
        api.Realization(
            "behavior",
            (
                api.Port("a", 1, driven_by="environment", clock="clk"),
                api.Port("y", 1, driven_by="artifact", clock="clk"),
                api.Port("start", 1, driven_by="environment"),
                api.Port("done", 1, driven_by="artifact"),
                api.Port("clk", 1, driven_by="environment"),
                api.Port("rst", 1, driven_by="environment"),
            ),
            channel_ports={"in": ("a",), "out": ("y",)},
            identity=behavior.identity))
    stimulus = Stimulus(
        queues={"in": [(1,), (0,)]},
        slots=[EventSlot(offers=["in"]), EventSlot(offers=["in"])],
        source="start asserted while busy")
    trace = AuditedTrace(
        stimulus,
        records=[
            SlotRecord(accepted={"in": (1,)}, outputs={"done": 0, "y": 0}),
            SlotRecord(outputs={"done": 0, "y": 0}),
        ],
        domain=trace_domain(contract.digest, horizon=2),
        artifact=behavior.identity)
    result = RunResult.not_compared(
        stimulus, trace,
        api.RunFailure.verifier(
            "test-audit-view",
            api.Refusal("test-uncompared", "audit fixture")))
    exact = behavior.bind(1, task_contract=contract.digest)

    got = audit_record(result, exact, task=task)

    # The two readings have different observation boundaries. RTL's pending
    # pin handshake is outside its environment; Behavior has logical calls.
    rtl = audit_environment_assumptions(contract, stimulus, trace, artifact_kind="rtl")
    assert rtl.disposition is AuditDisposition.OUTSIDE
    assert "environment.drain-missing" in rtl.why
    assert audit_environment_assumptions(
        contract, stimulus, trace, artifact_kind="behavior") is None
    # This hand-written record is still not admitted: it has no source goal
    # observations. Ignoring RTL-only pins cannot fabricate Behavior evidence.
    assert got.disposition is AuditDisposition.REFUSED
    assert got.kind == "behavior-record-hit-observation-unavailable"
