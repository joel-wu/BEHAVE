"""The vertical path, end to end: contract, admission, replay, score, analysis.

TODO3 T3-01's first cut. The point of testing it as a path rather than four units is
that the failure this replaces is not in any one of the four: `cosim.py` derives widths
and signedness from the candidate, checks only for missing names, and reconstructs a
schedule from transaction rows, so the contract a verdict is relative to is assembled
from the thing under test. A facade over that would pass every unit test.

So each test here walks the whole chain and asks what a caller can conclude at the end.
"""
import shutil

import pytest

from behave.sim import api
from behave.sim.core.refusal import Refused
from tests.support.device_boundary import evaluate_device, run_device

U8 = api.FiniteType(8)


def _evaluate(task, *args, **kwargs):
    """Direct-test boundary with an explicit empty frontend declaration."""

    kwargs["catalogue"] = api.evaluation_manifest(
        task, api.CatalogueManifest(()))
    return evaluate_device(task, *args, **kwargs)


def contract(max_outstanding=None, matching=api.Matching.ORDERED, answers=None,
             quiescent=None, deadline=None):
    """One input endpoint and one output, ready/valid on both, one clock."""
    return api.TaskContract(
        endpoints=[
            api.Endpoint("in", api.Direction.IN, [api.Field("a", U8, "in_a")],
                         api.ready_valid("in", "in_valid", "in_ready"), clock="clk",
                         max_outstanding=max_outstanding,
                         response_deadline=deadline,
                         kind=api.Kind.TRANSACTION),
            api.Endpoint("out", api.Direction.OUT, [api.Field("y", U8, "out_y")],
                         api.ready_valid("out", "out_valid", "out_ready"),
                         clock="clk", matching=matching,
                         answers_per_transaction=answers,
                         terminal_quiescence=quiescent,
                         kind=api.Kind.TRANSACTION),
        ],
        clocks=[api.ClockDomain("clk", "clk", "rst")],
        source="the task specification")


def realization(identity="doubling", **overrides):
    ports = {
        "in_a": api.Port("in_a", 8, driven_by="environment", clock="clk"),
        "out_y": api.Port("out_y", 8, driven_by="artifact", clock="clk"),
        "in_valid": api.Port("in_valid", 1, driven_by="environment"),
        "in_ready": api.Port("in_ready", 1, driven_by="artifact"),
        "out_valid": api.Port("out_valid", 1, driven_by="artifact"),
        "out_ready": api.Port("out_ready", 1, driven_by="environment"),
        "clk": api.Port("clk", 1, driven_by="environment"),
        "rst": api.Port("rst", 1, driven_by="environment"),
    }
    ports.update(overrides)
    return api.Realization("device", ports.values(),
                           channel_ports={"in": ["in_a"], "out": ["out_y"]},
                           identity=identity)


def test_realization_cannot_silently_collapse_duplicate_physical_ports():
    with pytest.raises(api.Refused, match="realization-duplicate-port"):
        api.Realization(
            "device",
            [api.Port("data", 8), api.Port("data", 16)],
            identity="ambiguous")


def test_realization_channel_rows_name_ports_in_the_same_record():
    with pytest.raises(api.Refused, match="realization-channel-port-unknown"):
        api.Realization(
            "device", [api.Port("data", 8)],
            channel_ports={"in": ["missing"]}, identity="incoherent")


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"name": 1, "width": 1}, "realization-port-name-invalid"),
        ({"name": "p", "width": True}, "realization-port-width-invalid"),
        ({"name": "p", "width": "1"}, "realization-port-width-invalid"),
        ({"name": "p", "width": 0}, "realization-port-width-invalid"),
        ({"name": "p", "width": 1, "signed": 1},
         "realization-port-signedness-invalid"),
        ({"name": "p", "width": 1, "driven_by": "both"},
         "realization-port-owner-invalid"),
        ({"name": "p", "width": 1, "clock": 0},
         "realization-port-clock-invalid"),
    ],
)
def test_frontend_port_values_are_not_silently_coerced(kwargs, reason):
    with pytest.raises(api.Refused, match=reason):
        api.Port(**kwargs)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"kind": 1}, "realization-kind-invalid"),
        ({"kind": "device", "source": None}, "realization-source-invalid"),
        ({"kind": "device", "identity": 1},
         "realization-identity-invalid"),
        ({"kind": "device", "ports": [object()]},
         "realization-port-record-invalid"),
        ({"kind": "device", "channel_ports": []},
         "realization-channel-map-invalid"),
        ({"kind": "device", "channel_ports": {1: []}},
         "realization-channel-name-invalid"),
        ({"kind": "device", "channel_ports": {"in": [1]}},
         "realization-channel-port-name-invalid"),
    ],
)
def test_frontend_realization_values_are_not_silently_coerced(kwargs, reason):
    with pytest.raises(api.Refused, match=reason):
        api.Realization(**kwargs)


def test_admission_refuses_a_frontend_reported_undeclared_channel():
    got = realization()
    channels = dict(got.channel_ports)
    channels["sideband"] = []
    extra = api.Realization(
        got.kind, got.ports.values(), channel_ports=channels,
        source=got.source, identity=got.identity)

    with pytest.raises(api.FailureRaised, match="admission-extra-channel") as caught:
        api.admit(contract(), extra)
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.determines_score


class Doubling:
    """An in-memory device with a one-deep pipeline, for testing the driver's rule.

    Not a reference. The reference is not run at all any more: it consumes the
    transactions the **artifact** accepted, through `expected_for`, because a reference
    given the stimulus can consume a different set than a design that stalls. This is a
    device -- it answers `ready` and `valid` per cycle, like silicon does.

    `settle` returns what the artifact drives at the read point; `edge` is the clock.
    """

    kind = "device"
    identity = "doubling"

    def __init__(self, gain=2, ports=("in_a", "out_y")):
        self.gain = gain
        self.in_port, self.out_port = ports
        self.reset()

    def reset(self):
        self.held = None
        self.next_held = None

    def settle(self, presented, controls):
        # Ready when nothing is held, or when what is held is being taken this cycle.
        taking_out = self.held is not None and controls.get("out_ready", 0)
        ready = 1 if (self.held is None or taking_out) else 0
        accepting = presented is not None and controls.get("in_valid", 0) and ready
        self.next_held = None if taking_out else self.held
        if accepting:
            self.next_held = ((presented[0] * self.gain) & 0xFF,)
        return {"in_ready": ready,
                "out_valid": 1 if self.held is not None else 0,
                self.out_port: self.held[0] if self.held is not None else 0}

    def edge(self):
        self.held = self.next_held


class FirstOnlyDoubling:
    """A faulty device that permanently stops accepting after one transaction."""

    kind = "device"
    identity = "doubling"

    def __init__(self):
        self.presented = []
        self.reset()

    def reset(self):
        self.accepted_once = False
        self.accepting = False
        self.held = None
        self.next_held = None

    def settle(self, presented, controls):
        self.presented.append(presented)
        taking = self.held is not None and controls.get("out_ready", 0)
        ready = int(not self.accepted_once and (self.held is None or taking))
        self.accepting = bool(
            presented is not None and controls.get("in_valid", 0) and ready)
        self.next_held = None if taking else self.held
        if self.accepting:
            self.next_held = ((presented[0] * 2) & 0xFF,)
        return {
            "in_ready": ready,
            "out_valid": int(self.held is not None),
            "out_y": self.held[0] if self.held is not None else 0,
        }

    def edge(self):
        self.accepted_once = self.accepted_once or self.accepting
        self.held = self.next_held


class _Model:
    """The task's reference: one transaction in, one payload out. No handshake."""

    def __init__(self, gain=2):
        self.gain = gain

    def reset(self):
        pass

    def process(self, in_a):
        return {"out_y": (in_a * self.gain) & 0xFF}


def _ref(cls=None, gain=None, **config):
    """The bundle a loader would return for a test model.

    Through the **production loader**, from the class's own source bytes. The tests used
    to build the two halves separately -- `ReferenceModel(SomeModel(), contract,
    artifact=_artifact(SomeOtherModel))` typechecked -- which is exactly the pairing
    `load_reference` exists to prevent, so a test suite that kept doing it was testing a
    path no caller should have.

    The source is the class and its bases, dedented, since the models here are defined
    inside test functions and several subclass `_Model`.
    """
    import inspect
    import textwrap

    cls = cls or _Model
    if gain is not None:
        config["gain"] = gain
    parts = [textwrap.dedent(inspect.getsource(base))
             for base in reversed(cls.__mro__[:-1])]
    source = "\n\n".join(dict.fromkeys(parts)).encode()
    from tests.support.reference import load

    return load({"behavior.py": source},
                config={"class": cls.__name__, **config})


def _live(c, cls=None, **config):
    """One instantiated reference, for the checks that are about a live model.

    A bundle is instantiated against the contract it is being used with, so a reference
    bound to the *wrong* contract cannot be built from one -- which is the point, and
    which leaves the refusal that catches it needing a live model to test against.
    """
    return _ref(cls, **config).instantiate(c, identity="ref")


def in_memory(task, stimulus, device):
    """One stimulus through the shared driver, against an in-memory device."""
    from behave.sim.core.driver import run_in_memory

    return run_in_memory(task.contract, stimulus, device)


def schedule(rows, ready=1, c=None, drain=2):
    """A stimulus over the contract's controls, reset in front and a drain behind.

    The drain is not decoration. A design that answers the cycle after it accepts has its
    last answer fall off the end of a horizon that stops at the last offer, and the
    comparison then has a truncated output stream -- which it declines to hold to the
    reference's length, so a wrong design looks clean.
    """
    return api.reset_then(c or contract(), [(v,) for v in rows],
                          controls={"out_ready": ready}, drain=drain, source="test")


def test_reset_framing_materializes_a_single_pass_row_source_once():
    rows = ((value,) for value in (3, 5))

    stimulus = api.reset_then(
        contract(), rows, controls={"out_ready": 1}, drain=1)

    assert dict(stimulus.queues)["in"] == ((3,), (5,))
    assert sum("in" in slot.offers for slot in stimulus.slots) == 2


# ── the path ──

def test_the_whole_path_runs_and_the_score_rests_on_accepted_transfers():
    task = api.admit(contract(), realization(), reference=_ref())
    stim = schedule([3, 5], drain=1)
    got = _evaluate(task, [stim], Doubling(), reference=_ref())

    assert got.accepted == 2, "two input transfers, and reset adds none"
    assert got.complete and got.passed
    assert not got.mismatches and not got.infrastructure
    assert dict(got.records[0].records[-1].accepted)["out"] == (10,), \
        "an output transferred in the final horizon slot is part of the score"
    # One domain identity format for every backend: `trace_domain`, whose recoverable
    # prefix is the interface. A bare contract digest here made the in-memory record's
    # domain a different shape from the RTL record's.
    from behave.sim.core.stimulus import contract_of_domain, trace_domain

    assert contract_of_domain(got.records[0].domain) == task.contract.digest
    assert got.records[0].domain == trace_domain(task.contract.digest,
                                                 horizon=len(stim.slots))

    # And the analysis is a typed refusal rather than an empty pass.
    outcome = api.analyse(got,
        wall_timeout_seconds=60)
    assert outcome.verdict is api.Verdict.REFUSED
    assert not outcome.verdict.per_goal and outcome.per_goal == {}
    assert outcome.concrete == got.fingerprint


def test_an_input_remaining_after_receptive_drain_is_one_safety_failure():
    """R01/R02: accepting a strict queue prefix cannot produce a passing score."""

    from behave.sim.core.monitor import Monitor

    task = api.admit(contract(), realization(), reference=_ref())
    stimulus = schedule([3, 5], drain=3)
    device = FirstOnlyDoubling()
    trace = in_memory(task, stimulus, device)

    assert api.transfers(trace, "in") == [(3,)]
    assert api.transfers(trace, "out") == [(6,)]
    assert sum(value == (5,) for value in device.presented) >= 3, \
        "the released queue head remains presented throughout drain"
    report = Monitor(task.contract).check(stimulus, trace)
    assert [item.goal for item in report.violations] == [
        "monitor.input-completion"
    ]
    assert not report.assumption_violations

    evaluation = _evaluate(
        task, [stimulus], FirstOnlyDoubling(), reference=_ref())
    assert evaluation.accepted == 1
    assert evaluation.mismatch_count == 0
    assert evaluation.safety_hits == ("monitor.input-completion",)
    assert evaluation.passed is False
    assert evaluation.as_dict()["mismatch_count"] == 0
    assert evaluation.as_dict()["safety_hits"] == [
        "monitor.input-completion"
    ]


def test_a_wrong_artifact_is_a_mismatch_and_does_not_stop_the_rest():
    task = api.admit(contract(), realization(), reference=_ref())
    stimuli = [schedule([3]), schedule([5]), schedule([7])]
    got = _evaluate(task, stimuli, Doubling(gain=3), reference=_ref())
    assert len(got.records) == 3, "the first mismatch did not stop the run"
    assert [i for i, _ in got.mismatches] == [0, 1, 2]
    assert got.mismatch_count >= 3, "counted per transaction, not per stimulus"
    assert not got.passed


def test_a_run_that_accepts_nothing_is_not_a_pass():
    """T3-02: a set containing only reset and no-transfer slots has zero accepted input
    and scores zero. Reporting it as a pass is reporting an empty test as a passing one.
    """
    task = api.admit(contract(), realization(), reference=_ref())
    # Every control the contract names, in every slot: the driver refuses a slot that
    # leaves one out, because filling it in would drive a value the stimulus does not
    # record and the fingerprint would be of a different control sequence.
    idle = api.Stimulus(
        queues={"in": []},
        slots=[api.EventSlot(reset=True,
                             controls={"rst": 1, "out_ready": 0}),
               api.EventSlot(controls={"rst": 0, "out_ready": 0})],
        source="idle")
    got = _evaluate(task, [idle], Doubling(), reference=_ref())
    assert got.accepted == 0
    assert got.complete, "the slots all ran"
    assert not got.passed, "and nothing was exercised"


# ── the handshake the contract declares ──

def test_a_stalled_offer_is_held_and_taken_when_the_artifact_can_take_it():
    """`max_outstanding=1`: while a transaction is in flight the endpoint does not accept
    another. The schedule offers in every slot, and the record says which offers closed.

    This is what the queue-per-offer model could not express at all, and it is why the
    pointer advances on transfer.
    """
    task = api.admit(contract(max_outstanding=1), realization(), reference=_ref())
    # Offer twice with the output never taken, so the first transaction stays in flight.
    stim = api.Stimulus(
        queues={"in": [(3,), (5,)]},
        slots=[api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 0})]
        + [api.EventSlot(offers=["in"],
                         controls={"rst": 0, "out_ready": 1})
           for _ in range(4)],
        source="stall")
    got = run_device(task, stim, Doubling())
    transferred = [p for r in got.records for n, p in r.accepted
                   if n == "in" and p is not None]
    assert transferred == [(3,), (5,)], "each offer of a held transaction is not a taker"
    assert got.accepted_count({"in"}) == 2
    assert len(got.records) == len(stim.slots), "every slot has a record"


def test_a_slot_that_offers_nothing_presents_nothing():
    """The environment declining to present is a slot with no `offers`, and that is the
    only way to say it.

    It used to be sayable twice -- `offers=["in"]` with `in_valid = 0` said the same
    thing -- and two authorities over one pin is how a slot came to record a value the
    run did not drive: an idle slot copied `in_valid = 1` from the cycle before it while
    the testbench, seeing nothing presented, drove 0. `in_valid` is not in a slot at all
    now; `core/driver.Driver.controls` derives it from `present`, which is the same call
    the bus is driven from.
    """
    task = api.admit(contract(), realization(), reference=_ref())
    stim = api.Stimulus(
        queues={"in": [(3,)]},
        slots=[api.EventSlot(controls={"rst": 0, "out_ready": 1}),
               api.EventSlot(offers=["in"], controls={"rst": 0, "out_ready": 1})],
        source="declining-then-offering")
    got = run_device(task, stim, Doubling())
    assert "in" not in got.records[0].transfers
    assert "in" in got.records[1].transfers

    # And a slot that names it is refused, by a fault that says which rule it broke.
    two = api.Stimulus(
        queues={"in": [(3,)]},
        slots=[api.EventSlot(offers=["in"],
                             controls={"rst": 0, "in_valid": 1, "out_ready": 1})],
        source="two-authorities")
    faults = two.faults_against(task.contract)
    assert any("second authority over one pin" in f for f in faults), faults
    with pytest.raises(Refused, match="driver-stimulus-not-of-this-contract"):
        run_device(task, two, Doubling())


def test_a_reset_slot_discards_what_was_in_flight():
    c = contract(answers=1)
    task = api.admit(c, realization(), reference=_ref())
    stim = api.Stimulus(
        queues={"in": [(3,)]},
        slots=[api.EventSlot(offers=["in"], controls={"rst": 0, "out_ready": 0}),
               api.EventSlot(reset=True, controls={"rst": 1, "out_ready": 0}),
               api.EventSlot(controls={"rst": 0, "out_ready": 1})],
        source="reset-mid-run")
    got = run_device(task, stim, Doubling())
    assert dict(got.records[1].monitor)["outstanding"] == 0
    assert not any(n == "out" for r in got.records for n, _ in r.accepted), \
        "the answer that was waiting did not survive the reset"


# ── admission refuses before anything is generated ──

@pytest.mark.parametrize("kind,port", [
    ("admission-narrower-field",
     api.Port("in_a", 7, driven_by="environment", clock="clk")),
    ("admission-wider-field",
     api.Port("in_a", 9, driven_by="environment", clock="clk")),
    ("admission-signedness",
     api.Port("in_a", 8, signed=True, driven_by="environment", clock="clk")),
    ("admission-handshake-ownership",
     api.Port("in_ready", 1, driven_by="environment")),
    ("admission-wrong-clock-domain",
     api.Port("in_a", 8, driven_by="environment", clock="clk2")),
])
def test_a_candidate_that_does_not_realize_the_contract_is_refused_by_name(kind, port):
    with pytest.raises(api.FailureRaised, match=kind) as caught:
        api.admit(contract(), realization(**{port.name: port}), reference=_ref())
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.determines_score


def test_a_port_the_contract_does_not_claim_is_an_extra_payload():
    """The design carries data the task never declared, so the transaction carries more
    than the contract says and nothing would compare the rest."""
    ports = dict(realization().ports)
    ports["in_b"] = api.Port("in_b", 8, driven_by="environment", clock="clk")
    r = api.Realization("device", ports.values(),
                        channel_ports={"in": ["in_a", "in_b"], "out": ["out_y"]},
                        identity="doubling")
    with pytest.raises(api.FailureRaised, match="admission-extra-payload") as caught:
        api.admit(contract(), r)
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE
    assert caught.value.failure.determines_score


def test_an_unreported_signedness_is_a_check_that_did_not_run():
    """An SMT2 model carries no signedness, so a frontend reading one has nothing to say.
    Defaulting that to unsigned would pass the check for every design read that way, and
    a check that always passes is worse than one that does not run: it looks like
    evidence."""
    task = api.admit(contract(),
                     realization(in_a=api.Port("in_a", 8, signed=None,
                                               driven_by="environment", clock="clk")))
    assert task.checks["payload_width"] is True
    assert task.checks["payload_signedness"] is None
    assert "payload_signedness" in task.unchecked


def test_a_frontend_that_cannot_report_the_channel_leaves_the_check_unrun():
    """Listed as not run rather than dropped: a reader can tell a passed check from an
    absent one."""
    r = api.Realization("device", realization().ports.values(),
                        identity="doubling")
    task = api.admit(contract(), r)
    assert task.checks["no_extra_payload[in]"] is None
    assert "no_extra_payload[in]" in task.unchecked


def test_an_adapter_this_build_does_not_implement_is_a_capability_refusal():
    """Running an unknown channel under a known adapter would give the recorded
    schedule a meaning the task never declared."""
    unsupported = api.Adapter("credit-based", 1,
                              environment_controls=("credit",),
                              artifact_controls=("send",))
    c = api.TaskContract(
        endpoints=[api.Endpoint("in", api.Direction.IN, [api.Field("a", U8, "in_a")],
                                unsupported, clock="clk")],
        clocks=[api.ClockDomain("clk", "clk", "rst")])
    with pytest.raises(Refused, match="admission-adapter-unsupported"):
        api.admit(c, realization(), reference=_ref())


def test_changing_the_adapter_version_changes_the_contract_identity():
    """The adapter decides what a recorded schedule means, so a schedule replayed under a
    different version is a different run."""
    a = contract()
    b = api.TaskContract(
        endpoints=[api.Endpoint(e.name, e.direction, e.fields,
                                api.ready_valid(e.direction, "in_valid", "in_ready",
                                                version=2)
                                if e.direction is api.Direction.IN
                                else api.ready_valid(e.direction, "out_valid",
                                                     "out_ready", version=2),
                                clock=e.clock)
                   for e in a.endpoints],
        clocks=list(a.clocks))
    assert a.digest != b.digest


# ── what this build does not do, said by name ──

def test_an_artifact_of_an_unknown_kind_does_not_fall_through_to_the_old_chain():
    """Dispatch is on the declared kind, not on which methods the object happens to
    have. A design and a reference both have something callable per transaction and they
    mean different things by it, so sniffing would run one under the other's protocol."""
    class Mystery:
        kind = "gate-level"
        identity = "doubling"

        def reset(self):
            pass

        def step(self, payload):
            return payload

    task = api.admit(contract(), realization(), reference=_ref())
    got = _evaluate(task, [schedule([1])], Mystery())
    assert got.infrastructure == ()
    assert len(got.no_result_failures) == 1
    _, failure = got.no_result_failures[0]
    assert failure.origin is api.FailureOrigin.VERIFIER
    assert failure.kind == "evaluate-artifact-kind-unsupported"
    assert not got.passed


def test_the_declared_matching_policy_is_the_one_applied():
    """The old matcher compared by **content** always, so `[2, 1]` satisfied an
    expectation of `[1, 2]` for every task in the population, declared or not. All three
    policies exist now, and a task that declares nothing gets ordered.

    Content matching carries its assumption out with it: the order carrying nothing is
    what the comparison rests on and not what it established, so a report can list it
    among the assumptions rather than among the findings.
    """
    ordered = api.compare(contract().endpoint("out"), [(1,), (2,)], [(2,), (1,)])
    assert not ordered.ok and "output 0" in ordered.why
    assert ordered.assumptions == (), "ordered assumes nothing"

    loose = api.compare(contract(matching=api.Matching.CONTENT).endpoint("out"),
                        [(1,), (2,)], [(2,), (1,)])
    assert loose.ok and loose.reordered == 2
    assert loose.assumptions and "carries nothing" in loose.assumptions[0]

    # And the run carries the assumption up into its telemetry. The reference is built
    # against **this** contract: one built against another is refused, because agreement
    # with a reference bound to a different contract is not agreement with this task's.
    task = api.admit(contract(matching=api.Matching.CONTENT), realization(), reference=_ref())
    got = _evaluate(task, [schedule([1], c=task.contract)], Doubling(),
                       reference=_ref())
    assert dict(got.telemetry)["assumptions"], got.telemetry


def test_a_value_the_declared_type_cannot_hold_is_refused_not_masked():
    """A reference computes in Python integers, which are unbounded. Masking here would
    make the two sides agree about a number neither of them holds."""
    from behave.sim.core.refusal import Refused as R

    out = contract().endpoint("out")
    assert api.normalise(out, (255,)) == (255,)
    with pytest.raises(R, match="compare-value-outside-type"):
        api.normalise(out, (256,))
    with pytest.raises(R, match="compare-value-not-an-integer"):
        api.normalise(out, ("0x",))

    # A field is `w` bits and its signedness is how they are read, so a model answering
    # with the unsigned pattern and a simulator reporting the signed integer are the
    # same transaction. 41 designs of this population compute with `to_unsigned(x, w)`
    # on a signed port, and holding them to the signed interval refused every one --
    # which is a statement about the convention and not about the design.
    signed = api.Endpoint("out", api.Direction.OUT,
                          [api.Field("y", api.FiniteType(8, signed=True), "out_y")],
                          api.ready_valid("out", "out_valid", "out_ready"), clock="clk")
    assert api.normalise(signed, (200,)) == api.normalise(signed, (-56,))
    with pytest.raises(R, match="compare-value-outside-type"):
        api.normalise(signed, (300,)), "outside the type read either way"


def test_an_identifier_the_reference_repeats_makes_the_correlation_ambiguous():
    """The task declares the identifier unique among outstanding transactions. If it is
    not, the comparison would silently pick one of the two."""
    from behave.sim.core.refusal import Refused as R

    u8 = api.FiniteType(8)
    ep = api.Endpoint("out", api.Direction.OUT,
                      [api.Field("id", u8, "out_id"), api.Field("y", u8, "out_y")],
                      api.ready_valid("out", "out_valid", "out_ready"), clock="clk",
                      matching=api.Matching.IDENTIFIER, identifier="id")
    assert api.compare(ep, [(1, 10), (2, 20)], [(2, 20), (1, 10)]).ok, \
        "reordering is what this policy admits"
    # Serial reuse is legal: the policy says unique among **outstanding**, and a tag
    # answered before it is issued again was never outstanding twice. Refusing it was
    # this build reading "unique" as "unique forever" and rejecting a legal design.
    assert api.compare(ep, [(1, 10), (1, 20)], [(1, 10), (1, 20)]).ok
    del R


def test_a_payload_outside_its_declared_type_is_refused_rather_than_masked():
    task = api.admit(contract(), realization(), reference=_ref())
    stim = api.Stimulus(queues={"in": [(300,)]},
                        slots=[api.EventSlot(offers=["in"],
                                             controls={"rst": 0,
                                                       "out_ready": 1})])
    got = _evaluate(task, [stim], Doubling(), reference=_ref())
    # Caught before the run rather than at the slot that would have driven it, and
    # together with every other fault the stimulus has: fixing them one refusal per run
    # is how a caller comes to believe the last one was the only one.
    assert got.infrastructure == ()
    assert len(got.no_result_failures) == 1
    _, failure = got.no_result_failures[0]
    assert failure.origin is api.FailureOrigin.VERIFIER
    assert failure.kind == "driver-stimulus-not-of-this-contract"
    assert not got.passed
    assert any("outside 0..255" in f
               for f in stim.faults_against(task.contract))


# ── the contract's reset, and a real design through the whole path ──

def test_the_reset_segment_is_as_long_as_the_contract_says():
    """Two sides holding reset for two different numbers of edges start from two
    different states. Here it is a fact the task declares, so every stimulus built for
    that task holds it the same number of cycles."""
    c = api.TaskContract(
        endpoints=list(contract().endpoints),
        clocks=[api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=3)])
    stim = api.reset_then(c, [(1,), (2,)], controls={"out_ready": 1})
    # Three held, two offering, one draining: the drain defaults to the endpoint's
    # outstanding depth, because that is how many answers can still be in flight when
    # the offers stop.
    assert [s.reset for s in stim.slots] == [True, True, True, False, False, False]
    assert [bool(s.offers) for s in stim.slots] == [False] * 3 + [True, True, False]
    assert dict(stim.slots[0].controls)["rst"] == 1
    assert dict(stim.slots[3].controls) == {"out_ready": 1, "rst": 0}, \
        "every control the contract names is filled, and the rest are driven at zero"
    assert not stim.slots[5].offers, "the drain offers nothing"
    assert "in_valid" not in dict(stim.slots[5].controls), \
        "the source's own valid is not in a slot: `offers` decides it"
    assert dict(stim.slots[5].controls)["out_ready"] == 1, \
        "and the environment stays ready, so the design can hand back what it holds"

    one = api.TaskContract(endpoints=list(contract().endpoints),
                           clocks=[api.ClockDomain("clk", "clk", "rst",
                                                   reset_hold_cycles=1)])
    assert len(api.reset_then(one, [(1,)], drain=0).slots) == 2


def _tools():
    from behave.core.eda import tool_env

    env = tool_env()
    return all(shutil.which(t, path=env["PATH"])
               for t in ("verilator", "make", "cocotb-config"))


needs_verilator = pytest.mark.skipif(
    not _tools(), reason="verilator, make or cocotb-config is not reachable")

DESIGN = """module held (
    input  logic clk, input logic rst,
    input  logic in_valid, output logic in_ready, input logic [7:0] in_a,
    output logic out_valid, input logic out_ready, output logic [7:0] out_y
);
    logic busy;
    assign in_ready  = ~busy | (out_valid & out_ready);
    assign out_valid = busy;
    always_ff @(posedge clk) begin
        if (rst) begin busy <= 1'b0; out_y <= 8'd0; end
        else if (in_valid & in_ready) begin busy <= 1'b1; out_y <= in_a + 8'd1; end
        else if (out_valid & out_ready) busy <= 1'b0;
    end
endmodule
"""

SPEC = """# Held

## Interface

### RTL interface

```systemverilog
module held (
    input  logic clk, input logic rst,
    input  logic in_valid, output logic in_ready, input logic [7:0] in_a,
    output logic out_valid, input logic out_ready, output logic [7:0] out_y
);
```
"""


@pytest.mark.cosim
@needs_verilator
def test_a_design_goes_through_the_whole_path_from_the_spec_it_declares(tmp_path):
    """Spec to score, with nothing read off the candidate that the task declares.

    This is the property the path exists for. `cosim.py` takes the widths, the
    signedness and the endpoint set from the candidate RTL, so the contract a verdict is
    relative to is assembled from the thing under test. Here the contract comes from the
    specification, the candidate is checked against it, and a candidate that disagreed
    would be refused before a single stimulus was generated.
    """
    from behave.sim.contract_of import contract_of_spec, realization_of_source
    from behave.sim.rtl.artifact import RTLArtifact

    src = tmp_path / "rtl.sv"
    src.write_text(DESIGN)
    # The kind is assumed here, as the population sweep assumes it: a port list cannot
    # say whether this is a request-response channel, and this build refuses to run one
    # it cannot classify.
    held = contract_of_spec(SPEC, "held", assume_kind=api.Kind.TRANSACTION)
    task = api.admit(held, realization_of_source(DESIGN, "held"),
                     reference=_ref())
    assert not task.unchecked, "every admission check had material to run on"

    stim = api.reset_then(task.contract, [(3,), (5,)], controls={"out_ready": 1})
    art = RTLArtifact(str(src), "held", tmp_path / "sim")
    got = run_device(task, stim, art)

    assert len(got.records) == len(stim.slots)
    assert got.accepted_count({"in"}) == 2, "both transactions were taken"
    out = [p for r in got.records for n, p in r.accepted
           if n == "out" and p is not None]
    assert out == [(4,), (6,)], "and each answer came back as a transaction"


@pytest.mark.cosim
@needs_verilator
def test_a_stalled_design_holds_its_transaction_and_the_record_says_so(tmp_path):
    """Injection stalls the sink; the receptive drain releases its back-pressure.

    The body offers in every slot with ``out_ready`` low.  Its released queue head stays
    presented while the design is busy.  The drain then becomes receptive, so the first
    response and the held second request transfer together; it does not keep injection's
    back-pressure policy alive past injection.
    """
    from behave.sim.contract_of import contract_of_spec, realization_of_source
    from behave.sim.rtl.artifact import RTLArtifact

    src = tmp_path / "rtl.sv"
    src.write_text(DESIGN)
    # The kind is assumed here, as the population sweep assumes it: a port list cannot
    # say whether this is a request-response channel, and this build refuses to run one
    # it cannot classify.
    held = contract_of_spec(SPEC, "held", assume_kind=api.Kind.TRANSACTION)
    task = api.admit(held, realization_of_source(DESIGN, "held"),
                     reference=_ref())
    stim = api.reset_then(task.contract, [(3,), (5,), (7,)],
                          controls={"out_ready": 0})
    got = run_device(task, stim, RTLArtifact(str(src), "held", tmp_path / "sim"))

    assert got.accepted_count({"in"}) == 2, \
        "the held second request transfers when drain becomes receptive"
    assert [p for r in got.records for n, p in r.accepted
            if n == "out" and p is not None] == [(4,)]
    assert len(got.records) == len(stim.slots), "every slot still has a record"


# ── the monitor, resident over the whole horizon ──

def test_a_transaction_that_is_never_answered_is_a_safety_hit():
    """The replay testbench stops monitoring as soon as the driver is done and the
    expected outputs are empty, so a missing terminal response happens after anybody is
    looking. This observes every slot including the drain.

    **What settles it is the declared deadline.** Over a finite horizon "accepted and
    unanswered at the end" is also what a run that stopped too early looks like, and the
    obligation that used to fire here inferred it from an idle slot instead -- which is
    the converse of what `terminal_quiescence` declares. The task says how long the
    design may take; a design that never answers exceeds it, and that is observable.
    """
    task = api.admit(contract(answers=1, quiescent=True, deadline=2), realization(),
                     reference=_ref())

    class Silent:
        """Takes a transaction and never raises `out_valid`."""

        kind = "device"
        identity = "doubling"

        def reset(self):
            pass

        def settle(self, presented, controls):
            del presented, controls
            return {"in_ready": 1, "out_valid": 0, "out_y": 0}

        def edge(self):
            pass

    stim = api.reset_then(task.contract, [(3,)], controls={"out_ready": 1}, drain=6)
    got = _evaluate(task, [stim], Silent())
    # **The deadline is what settles this, not quiescence.** The obligation that used to
    # fire here read an idle slot as proof that nothing was owed, which is the converse
    # of what `terminal_quiescence` declares -- see `Monitor._terminal`. A design that
    # never answers breaks its declared deadline, and that is the finding.
    assert "monitor.response-deadline" in got.safety_hits, got.telemetry
    # Mismatch evidence and safety evidence remain separate, but both participate in
    # the one success decision.
    assert got.accepted > 0 and not got.mismatches, \
        "not a mismatch: nobody compared, because this run has no reference"
    assert not got.passed, \
        "and a run nobody compared cannot pass: the empty mismatch list means only " \
        "that nobody looked"
    assert got.compared == (), "no record was held against a reference"
    assert got.safety_hits, "the safety classification is where the finding is"


def test_an_output_with_no_transaction_outstanding_is_a_safety_hit():
    """The design produced more than it was given."""
    from behave.sim.core.monitor import Monitor

    c = contract(answers=1)
    stim = api.reset_then(c, [(1,)], controls={"out_ready": 1})
    trace = api.AuditedTrace(
        stim,
        records=[api.SlotRecord() for _ in stim.slots[:-2]]
        + [api.SlotRecord(accepted={"in": (1,), "out": (2,)}),
           api.SlotRecord(accepted={"out": (9,)})])
    bad, chances = Monitor(c).check(stim, trace)
    assert [v.goal for v in bad] == ["monitor.output-without-transaction"]
    # And the obligation had chances to be broken, which is what keeps an empty
    # violation list from reading as a design that met it.
    assert chances["monitor.output-without-transaction"] > 0


def test_a_deadline_the_task_does_not_declare_is_recorded_as_unchecked():
    """"Answered eventually" and "answered within four slots" are different designs, and
    only the task can say which one it wanted. Reporting the check as passed would report
    the absence of a requirement as evidence of meeting one."""
    from behave.sim.core.monitor import Monitor

    assert "monitor.response-deadline" in Monitor(contract()).unchecked()

    prompt = api.TaskContract(
        endpoints=[api.Endpoint(e.name, e.direction, e.fields, e.adapter,
                                clock=e.clock, response_deadline=1,
                                kind=api.Kind.TRANSACTION)
                   if e.direction is api.Direction.IN
                   else api.Endpoint(e.name, e.direction, e.fields, e.adapter,
                                     clock=e.clock, answers_per_transaction=1,
                                     kind=api.Kind.TRANSACTION)
                   for e in contract().endpoints],
        clocks=list(contract().clocks))
    assert "monitor.response-deadline" not in Monitor(prompt).unchecked()

    task = api.admit(prompt, realization(), reference=_ref())
    stim = api.reset_then(prompt, [(3,)], controls={"out_ready": 1}, drain=6)

    class Slow:
        """Accepts, then waits four cycles before raising `out_valid`."""

        kind = "device"
        identity = "doubling"

        def reset(self):
            self.wait = None
            self.next_wait = None

        def settle(self, presented, controls):
            ready = 1 if self.wait is None else 0
            self.next_wait = self.wait
            if presented is not None and controls.get("in_valid", 0) and ready:
                self.next_wait = 4
            elif self.wait is not None:
                self.next_wait = self.wait - 1
                if self.wait == 0 and controls.get("out_ready", 0):
                    self.next_wait = None
            return {"in_ready": ready,
                    "out_valid": 1 if self.wait == 0 else 0, "out_y": 1}

        def edge(self):
            self.wait = self.next_wait

    # A design that answers late is caught by the deadline the task declared; without the
    # declaration the same run is clean, which is the point of counting it unchecked.
    got = _evaluate(task, [stim], Slow())
    assert "monitor.response-deadline" in got.safety_hits, got.telemetry


def test_a_real_behavior_model_can_be_the_reference(tmp_path):
    """The population's models are `reset()` plus `process(**ports)` returning a dict
    keyed by output port. What was missing was the mapping, not the model."""
    model_src = ("class M:\n"
                 "    def reset(self): self.last = 0\n"
                 "    def process(self, in_a):\n"
                 "        self.last = (in_a * 2) & 0xFF\n"
                 "        return {'out_y': self.last}\n")
    # The loader digests the **bytes it read** and builds the model from those same
    # bytes, which is the case introspection cannot serve: `inspect.getsource` on a
    # class from `exec` raises, so the old digest degraded to the class name -- which is
    # what the population sweep was freezing, meaning it froze nothing.
    from behave.sim import reference_isolated

    ref = api.load_reference(
        reference_isolated.ADAPTER, {"behavior.py": model_src.encode()},
        loader="test")
    task = api.admit(contract(), realization(), reference=ref)
    stim = api.reset_then(task.contract, [(3,), (5,)], controls={"out_ready": 1})
    got = _evaluate(task, [stim], Doubling(), reference=ref)
    assert got.passed, (got.mismatches, got.safety_hits)

    wrong = _evaluate(task, [stim], Doubling(gain=3), reference=ref)
    assert wrong.mismatches and not wrong.passed


def test_a_reference_answer_outside_its_declared_type_leaves_no_result():
    """The model has not been shown to disagree with the design; it has produced
    something the contract cannot express, and nobody can compare that."""
    class Overflowing:

        def reset(self):
            pass

        def process(self, in_a):
            return {"out_y": in_a * 1000}

    ref = _ref(Overflowing)
    task = api.admit(contract(), realization(), reference=ref)
    stim = api.reset_then(task.contract, [(3,)], controls={"out_ready": 1})
    got = _evaluate(task, [stim], Doubling(), reference=ref)
    assert got.infrastructure == ()
    assert len(got.no_result_failures) == 1
    _, failure = got.no_result_failures[0]
    assert failure.origin is api.FailureOrigin.REFERENCE
    assert failure.kind == "compare-value-outside-type"
    assert not got.mismatches and not got.passed


# ── the review's reproductions, each pinned ──

def test_a_record_cannot_change_after_its_digest_was_taken():
    """The reproduction that started this: read `contract.digest`, set
    `endpoint.max_outstanding = 9`, read the digest again and it is unchanged while
    `as_dict()` already reports 9. Evidence bound to that digest is evidence about a
    contract nobody can recover.

    `__slots__` does not give this. It fixes which attributes exist, not whether they can
    be written, and the two are unrelated.
    """
    c = contract()
    before = c.digest
    for obj, attr, value in ((c.endpoint("in"), "max_outstanding", 9),
                             (c.endpoint("in").fields[0], "port", "elsewhere"),
                             (c.endpoint("in").fields[0].type, "width", 32),
                             (c.clocks[0], "reset", "nope"),
                             (c, "authority", "candidate")):
        with pytest.raises(AttributeError, match="cannot be reassigned"):
            setattr(obj, attr, value)
    assert c.digest == before

    stim = schedule([1])
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        stim.slots[0].reset = False
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        stim.source = "elsewhere"


def test_an_active_low_reset_is_recorded_the_way_it_is_driven():
    """The stimulus recorded `[1, 1, 1, 0, 0]` while the design was driven
    `[0, 0, 0, 1, 1]`, because the polarity was applied on the way down to cycles. Worse,
    the released level in the recorded slots was 0, which for an active-low design is
    *asserted*, so every non-reset cycle held reset too.

    A fingerprint over a control sequence that is not the one that ran is not a
    fingerprint of the run.
    """
    low = api.TaskContract(endpoints=list(contract().endpoints),
                           clocks=[api.ClockDomain("clk", "clk", "rst",
                                                   reset_active_low=True)])
    stim = api.reset_then(low, [(1,)], controls={"out_ready": 1})
    assert [dict(s.controls)["rst"] for s in stim.slots] == [0, 0, 0, 1, 1]

    high = api.reset_then(contract(), [(1,)], controls={"out_ready": 1})
    assert [dict(s.controls)["rst"] for s in high.slots] == [1, 1, 1, 0, 0]

    # And the driver drives exactly that, rather than applying a polarity of its own.
    from behave.sim.core.driver import Driver

    assert Driver(low, stim).controls(0)["rst"] == 0


def test_a_slot_that_leaves_a_control_unnamed_is_refused_not_filled_in():
    """Filling one in drives a value the stimulus does not record, so the fingerprint is
    of a control sequence that is not the one that ran."""
    from behave.sim.core.driver import Driver
    from behave.sim.core.refusal import Refused as R

    c = contract()
    partial = api.Stimulus(queues={"in": [(1,)]},
                           slots=[api.EventSlot(offers=["in"],
                                                controls={"rst": 0})],
                           source="missing out_ready")
    with pytest.raises(R, match="driver-stimulus-not-of-this-contract"):
        Driver(c, partial)
    assert any("does not name ['out_ready']" in f
               for f in partial.faults_against(c))


@pytest.mark.parametrize("kind,over", [
    ("admission-payload-direction",
     {"in_a": api.Port("in_a", 8, driven_by="artifact", clock="clk")}),
    ("admission-clock-ownership",
     {"clk": api.Port("clk", 1, driven_by="artifact")}),
    ("admission-handshake-width",
     {"in_valid": api.Port("in_valid", 2, driven_by="environment")}),
])
def test_admission_closes_the_holes_the_review_walked_through(kind, over):
    """Each of these passed all seven checks before: an input payload port declared as an
    output, a clock the candidate drives, and a two-bit handshake."""
    with pytest.raises(api.FailureRaised, match=kind) as caught:
        api.admit(contract(), realization(**over), reference=_ref())
    assert caught.value.failure.origin is api.FailureOrigin.CANDIDATE


def test_admitting_one_candidate_does_not_license_running_another():
    """Admit A, evaluate B, and every refusal admission made was about a design that is
    not under test."""
    task = api.admit(contract(), realization(identity="A"), reference=_ref())
    other = Doubling()
    other.identity = "B"
    with pytest.raises(Refused, match="run-artifact-not-the-admitted-one"):
        run_device(task, schedule([1]), other)

    # And neither does an artifact that will not say which it is.
    nameless = Doubling()
    nameless.identity = ""
    with pytest.raises(Refused, match="run-artifact-identity-unstated"):
        run_device(task, schedule([1]), nameless)


def test_the_reference_consumes_what_the_artifact_accepted():
    """`o*(xi) = B*(sigma, a_A(xi))`. Replaying both sides against the same stimulus
    independently lets a design that stalls consume a different set of transactions than
    the reference does, and then the two output streams are answers to different
    questions.

    Here the design takes one of three and the reference is stepped once, not three
    times -- which is the whole difference, and it is invisible when nothing stalls.
    """
    task = api.admit(contract(), realization(), reference=_ref())

    class TakesOne:
        kind = "device"
        identity = "doubling"

        def reset(self):
            self.full = False
            self.next_full = False

        def settle(self, presented, controls):
            ready = 0 if self.full else 1
            self.next_full = self.full or (
                presented is not None and controls.get("in_valid", 0) and ready)
            return {"in_ready": ready, "out_valid": 0, "out_y": 0}

        def edge(self):
            self.full = self.next_full

    stim = api.reset_then(task.contract, [(1,), (2,), (3,)],
                          controls={"out_ready": 1}, drain=2)
    trace = run_device(task, stim, TakesOne())
    assert trace.accepted_count({"in"}) == 1

    class Counting(_Model):
        def reset(self):
            self.seen = []

        def process(self, in_a):
            self.seen.append(in_a)
            return super().process(in_a)

    counting = _live(contract(), Counting)
    api.expected_for(api.admit(contract(), realization(), reference=counting),
                     counting, trace)
    assert counting.model.seen == [1], "the reference saw exactly what the artifact took"


# ── the product state, and completeness as a mechanism ──

def test_a_component_that_changes_and_nobody_owns_fails_the_reconciliation():
    """T3-07's acceptance clause, stated as a mechanism rather than as a promise.

    A backend that says "I captured everything" is attesting to its own completeness, so
    a component added later is missed in exactly the way nobody notices: the attestation
    still says yes. Here every component is declared with an owner and a change no owner
    claimed is a refusal.
    """
    from behave.sim.core.product import Component, ProductState, reconcile
    from behave.sim.core.refusal import Refused as R

    manifest = [Component("driver.pointer", "driver"),
                Component("artifact.regs", "artifact", in_novelty=True),
                Component("monitor.outstanding", "monitor")]
    before = ProductState(manifest, {"driver.pointer": 0, "artifact.regs": (0,),
                                     "monitor.outstanding": 0})
    after = ProductState(manifest, {"driver.pointer": 1, "artifact.regs": (1,),
                                    "monitor.outstanding": 1})

    assert reconcile(before, after, {"driver": ["driver.pointer"],
                                     "artifact": ["artifact.regs"],
                                     "monitor": ["monitor.outstanding"]}) == (
        "artifact.regs", "driver.pointer", "monitor.outstanding")

    with pytest.raises(R, match="product-unclaimed-change"):
        reconcile(before, after, {"driver": ["driver.pointer"],
                                  "artifact": ["artifact.regs"]})
    with pytest.raises(R, match="product-wrong-owner"):
        reconcile(before, after, {"monitor": ["driver.pointer"],
                                  "artifact": ["artifact.regs"],
                                  "driver": ["monitor.outstanding"]})
    # A claim that is not true is worse than no claim: a later reader uses these to
    # decide what a query has to pin.
    still = ProductState(manifest, {"driver.pointer": 1, "artifact.regs": (0,),
                                    "monitor.outstanding": 0})
    with pytest.raises(R, match="product-phantom-change"):
        reconcile(before, still, {"driver": ["driver.pointer"],
                                  "artifact": ["artifact.regs"]})

    # And two owners claiming one component: the manifest puts it under one of them, so
    # at most one claim is true, and taking the last written makes the answer depend on
    # dictionary order. Found by this test, which had written exactly that by accident.
    with pytest.raises(R, match="product-two-owners-claim"):
        reconcile(before, after, {"driver": ["driver.pointer", "artifact.regs"],
                                  "artifact": ["artifact.regs"],
                                  "monitor": ["monitor.outstanding"]})


def test_a_state_carrying_something_the_manifest_does_not_declare_is_refused():
    """The direction that catches things. A backend that grows a field and does not
    declare it fails here; one that only checked the other direction would carry the new
    field along and never mention it."""
    from behave.sim.core.product import Component, ProductState
    from behave.sim.core.refusal import Refused as R

    manifest = [Component("driver.pointer", "driver")]
    with pytest.raises(R, match="product-undeclared-component"):
        ProductState(manifest, {"driver.pointer": 0, "grew": 1})
    with pytest.raises(R, match="product-missing-component"):
        ProductState(manifest + [Component("late", "artifact")],
                     {"driver.pointer": 0})


def test_a_novelty_key_cannot_exist_without_the_state_it_projects():
    """It may make a frontier call two states alike and it may never justify a query or
    an evidence claim, so it is derived from a product state and cannot be built from
    anything else. Only the components declared for it are projected: a counter that
    ticks every cycle makes every state novel."""
    from behave.sim.core.product import Component, NoveltyKey, ProductState

    manifest = [Component("driver.slot", "driver"),
                Component("artifact.regs", "artifact", in_novelty=True)]
    a = ProductState(manifest, {"driver.slot": 0, "artifact.regs": (7,)})
    later = ProductState(manifest, {"driver.slot": 99, "artifact.regs": (7,)})
    other = ProductState(manifest, {"driver.slot": 0, "artifact.regs": (8,)})

    assert NoveltyKey(a) == NoveltyKey(later), "the slot counter is not novelty"
    assert NoveltyKey(a) != NoveltyKey(other)
    assert NoveltyKey(a).of == a.digest, "and it says which state it is of"


def test_the_driver_reconciles_every_slot_it_runs():
    """The loop's own components go through the same gate as anyone else's."""
    c = contract(answers=1)
    task = api.admit(c, realization(), reference=_ref())
    stim = api.reset_then(task.contract, [(3,), (5,)], controls={"out_ready": 1})
    trace = run_device(task, stim, Doubling())

    # One product state per slot, each reconciled against the manifest on the way.
    from behave.sim.core.driver import Driver

    driver = Driver(task.contract, stim)
    assert {c.owner for c in driver.manifest} == {"driver", "monitor"}
    assert dict(trace.records[-1].monitor)["outstanding"] == 0
    assert trace.accepted_count({"in"}) == 2


# ── what a comparison establishes, and what the score rests on ──

def test_a_reference_may_owe_several_answers_to_one_transaction():
    """26 designs of this population return a **list** of output dicts: one request
    produces several output transactions. A reference interface taking only a dict called
    every one of them a model that omits a declared field."""
    class Three(_Model):
        def process(self, in_a):
            return [{"out_y": (in_a + k) & 0xFF} for k in range(3)]

    ref = _ref(Three)
    task = api.admit(contract(), realization(), reference=ref)
    stim = api.reset_then(task.contract, [(1,)], controls={"out_ready": 1})
    trace = run_device(task, stim, Doubling())
    assert api.expected_for(task, ref, trace) == [(1,), (2,), (3,)]

    # And `None` is still none, which is not the same as one.
    class Silent(_Model):
        def process(self, in_a):
            del in_a

    quiet = _ref(Silent)
    hushed = api.admit(contract(), realization(), reference=quiet)
    assert api.expected_for(hushed, quiet, trace) == []


def test_a_short_output_stream_is_a_mismatch_and_the_shortage_is_diagnostics():
    """Appendix A.1: **every unpaired expected transaction contributes a mismatch.**

    An earlier version of this returned `ok=True, complete=False` for an observed stream
    that is a strict prefix of the expected one, reasoning that a run cannot tell "the
    design owes more and the horizon ended first" from "the design will never produce
    them". That is true and it is not a licence to invent a third verdict: twenty designs
    of the population were reported as matching on it. The definition stands and the
    ambiguity goes in the diagnostics, where a caller can read it as "try a longer
    horizon".
    """
    class Streaming(_Model):
        def process(self, in_a):
            return [{"out_y": (in_a * 2) & 0xFF} for _ in range(4)]

    ref = _ref(Streaming)
    task = api.admit(contract(), realization(), reference=ref)
    stim = api.reset_then(task.contract, [(3,)], controls={"out_ready": 1})
    trace = run_device(task, stim, Doubling())      # emits one, the reference expects four
    got = api.compare_run(task, ref, trace)
    assert not got.ok, "a short stream is a mismatch"
    assert not got.complete, "and the shortage is why, for a caller reading diagnostics"

    ev = _evaluate(task, [stim], Doubling(), reference=ref)
    assert ev.mismatches and ev.incomplete and not ev.passed


def test_more_outputs_than_the_reference_expects_is_a_mismatch():
    """Truncation cannot explain extra outputs, so the prefix rule does not apply."""
    task = api.admit(contract(), realization(), reference=_ref())

    class One(_Model):
        def process(self, in_a):
            return {"out_y": (in_a * 2) & 0xFF}

    stim = api.reset_then(task.contract, [(3,), (5,)], controls={"out_ready": 1})
    trace = run_device(task, stim, Doubling())
    only_one = _ref(One)
    fewer = api.expected_for(api.admit(contract(), realization(), reference=only_one),
                             only_one, trace)
    got = api.compare(task.contract.endpoint("out"), fewer[:1],
                      api.transfers(trace, "out"))
    assert not got.ok
    # One fault per unpaired transaction, with its kind, rather than one sentence about
    # the lengths: a score over a mismatch count needs the count.
    assert [f[0] for f in got.faults] == ["extra"]
    assert "arrived and the reference issues nothing" in got.why


def test_a_stimulus_set_with_a_repeat_in_it_is_refused_rather_than_quietly_fixed():
    """Two producers reaching one stimulus found one stimulus. Replaying it twice does
    not make it two tests, and counting it twice makes a search that rediscovers its own
    witnesses look thorough.

    This used to deduplicate here, silently, and that hid the caller's own arithmetic
    being wrong: the telemetry said two stimuli while one result came back, the second
    candidate's provenance overwrote the first's, and a cap's denominator was not the set
    that ran. Canonicalising a set is `authority.canonical_set`'s job -- after the padding
    to `N`, because two stimuli that differ only in their tail become one there. This
    checks, and says so.
    """
    task = api.admit(contract(), realization(), reference=_ref())
    one = schedule([3])
    with pytest.raises(Refused, match="evaluate-stimulus-set-is-not-a-set"):
        _evaluate(task, [one, one, one], Doubling(), reference=_ref())

    ev = _evaluate(task, [one], Doubling(), reference=_ref())
    assert len(ev.results) == 1 and len(ev.stimuli) == 1
    assert len(ev.records) == 1, "and it was replayed once"


def test_a_run_with_no_reference_establishes_nothing_and_cannot_pass():
    """`evaluate(..., reference=None)` used to return `passed=True`: the mismatch list
    was empty because nobody had looked, and the score asked only whether it was empty.

    A reference that is missing, one that refuses and one that fails are the same fact --
    output agreement has no basis -- so all three leave the record out of `agreed`.
    """
    task = api.admit(contract(), realization(), reference=_ref())
    stim = schedule([3])

    blind = _evaluate(task, [stim], Doubling())
    assert blind.accepted > 0 and not blind.mismatches
    assert blind.compared == () and not blind.passed

    seen = _evaluate(task, [stim], Doubling(), reference=_ref())
    assert seen.compared == (0,) and seen.passed

    class Exploding(_Model):
        def process(self, in_a):
            raise ZeroDivisionError("the model itself failed")

    boom = _ref(Exploding)
    fated = api.admit(contract(), realization(), reference=boom)
    crashed = _evaluate(fated, [stim], Doubling(), reference=boom)
    assert crashed.infrastructure == ()
    assert len(crashed.no_result_failures) == 1
    _, failure = crashed.no_result_failures[0]
    assert failure.origin is api.FailureOrigin.REFERENCE
    assert failure.kind == "reference-runtime-failed"
    assert crashed.compared == () and not crashed.passed, \
        "a reference that crashes leaves output agreement with no basis, exactly as a " \
        "missing one does"


def test_the_stability_obligation_is_the_artifacts_and_can_fail():
    """It used to be written over the **environment's** payload, which the driver holds
    by construction -- so it could not fail, and it was reported as the one obligation
    this population could check. The obligation that matters is the design's: while
    `out_valid` is high and `out_ready` is low, the receiver is entitled to the same
    payload and a `valid` that does not drop.
    """
    from behave.sim.core.monitor import Monitor

    c = contract()
    queue = ((1,), (2,), (3,))
    stim = api.reset_then(c, queue, controls={"out_ready": 0}, drain=1)

    def trace(pattern):
        outputs = iter(pattern)
        queued = iter(queue)
        records = []
        for slot in stim.slots:
            if slot.reset:
                records.append(api.SlotRecord())
                continue
            valid, y = next(outputs)
            accepted = ({"in": next(queued)} if slot.offers else {})
            records.append(api.SlotRecord(
                accepted=accepted,
                outputs={"out_valid": valid, "out_y": y}))
        return api.AuditedTrace(stim, records=records)

    steady = trace(((1, 7), (1, 7), (1, 7), (1, 7)))
    found, chances = Monitor(c).check(stim, steady)
    assert not found and chances["monitor.sink-stability"] == 3, \
        "three slots in which it could have moved and did not"

    wobbly = trace(((1, 7), (1, 9), (0, 0), (0, 0)))
    found, _ = Monitor(c).check(stim, wobbly)
    assert [v.goal for v in found] == ["monitor.sink-stability"] * 2
    assert "changed from [7] to [9]" in found[0].why
    assert "dropped `valid`" in found[1].why


def test_an_obligation_nothing_exercised_is_not_an_obligation_that_was_met():
    """A run with no stalls has no chance to break the stability obligation, and an
    empty violation list would otherwise read as a design that handles stalls."""
    task = api.admit(contract(), realization(), reference=_ref())
    never_stalls = api.reset_then(task.contract, [(1,)], controls={"out_ready": 1})
    got = _evaluate(task, [never_stalls], Doubling(), reference=_ref())
    assert not got.safety_hits
    assert dict(dict(got.telemetry)["monitor_opportunities"])["monitor.sink-stability"] == 0, \
        "nothing was ever offered and declined, so nothing was watched"


def test_a_declared_artifact_failure_pads_the_horizon_and_does_not_score():
    """T3-02: exactly `N` recorded slots, the safety hit once, padding thereafter, the
    artifact never invoked again, and the completion predicate false.

    Each half matters separately. `N` slots because a shorter record is a record of a
    shorter stimulus, and a later reader comparing two runs would be comparing different
    horizons. And a false completion because analysis padding must never turn a failed
    concrete run into a passing one.
    """
    from behave.sim.core.driver import Driver
    from behave.sim.core.refusal import Refused as R
    from behave.sim.core.stimulus import Completion

    c = contract()
    stim = api.reset_then(c, [(1,), (2,), (3,)], controls={"out_ready": 1})
    driver = Driver(c, stim)
    device = Doubling()
    device.reset()
    for index, slot in enumerate(stim.slots):
        if index == 4:
            driver.fail(index, "the design asserted its own failure")
            break
        controls = driver.controls(index)
        presented = driver.present(index)
        if slot.reset:
            device.reset()
        driver.observe(index, presented, device.settle(presented, controls))
        device.edge()

    trace = driver.trace()
    assert len(trace.records) == len(stim.slots), "exactly N slots"
    assert trace.completion is Completion.PADDED
    assert not trace.completion.scores, "padding never makes a failed run pass"
    assert sum(
        bool(dict(record.hits).get("artifact.terminal-failure"))
        for record in trace.records
    ) == 1, "the terminating safety goal is emitted exactly once"
    assert dict(trace.records[-1].monitor) == {"padding": 1}
    assert not any(dict(r.accepted) for r in trace.records[4:]), \
        "the artifact was not invoked again"

    with pytest.raises(R, match="driver-already-failed"):
        driver.fail(5, "again")


def test_the_reference_must_be_bound_to_this_task_s_contract():
    """Without this the run establishes that the artifact agrees with **some object the
    caller passed in**, which is not the statement the appendix asks for."""
    task = api.admit(contract(), realization(), reference=_ref())
    other = api.TaskContract(endpoints=list(contract().endpoints),
                             clocks=[api.ClockDomain("clk", "clk", "rst",
                                                     reset_hold_cycles=9)])
    assert other.digest != task.contract.digest

    trace = run_device(task, schedule([1]), Doubling())
    with pytest.raises(Refused, match="reference-not-of-this-contract"):
        api.expected_for(task, _live(other), trace)
    api.expected_for(task, _ref(), trace)


def test_a_reference_is_stepped_once_per_slot_through_one_interface():
    """It used to be stepped only on accepted transactions where there was an input
    endpoint, and on every non-reset slot where there was not -- the same rule written
    twice with different answers -- and to call `tick()` on idle slots, falling back to
    `process()` with no arguments where the model had none. For the population's standard
    `process(in_a)` that is a `TypeError` on the first drain slot.

    A transaction-level model has nothing to say about a cycle in which no transaction
    happened. Saying that once, in the adapter, is different from every caller guessing.
    """
    task = api.admit(contract(), realization(), reference=_ref())
    stim = api.reset_then(task.contract, [(3,)], controls={"out_ready": 1}, drain=4)
    trace = run_device(task, stim, Doubling())

    class Recording(_Model):
        def reset(self):
            self.seen = getattr(self, "seen", []) + ["reset"]

        def process(self, in_a):
            self.seen.append(("process", in_a))
            return super().process(in_a)

    rec = _live(contract(), Recording)
    api.expected_for(api.admit(contract(), realization(), reference=rec), rec, trace)
    assert rec.model.seen == ["reset", "reset", "reset", ("process", 3)], \
        "one reset per reset slot, one process per accepted transaction, and nothing " \
        "at all for a drain slot: a transaction-level model has nothing to say about a " \
        "cycle in which no transaction happened"


def test_a_mismatch_is_counted_per_transaction_with_its_kind():
    """The appendix counts them per transaction. Expected `[1, 2]` against observed `[9]`
    is a wrong value **and** an answer that never came, which is two; a boolean plus one
    sentence per stimulus could only ever say one.

    The kind matters as much as the count: `value`, `missing` and `extra` are three
    different things about a design, and a report that has to parse a sentence to tell
    them apart is a report that will stop being able to.
    """
    out = contract().endpoint("out")

    got = api.compare(out, [(1,), (2,)], [(9,)])
    assert [f[0] for f in got.faults] == ["value", "missing"]
    assert got.as_dict()["mismatches"] == 2

    assert api.compare(out, [(1,)], [(1,)]).faults == ()
    assert [f[0] for f in api.compare(out, [(1,)], [(1,), (2,)]).faults] == ["extra"]

    # And the evaluation's count is over transactions, not over stimuli.
    task = api.admit(contract(), realization(), reference=_ref())
    ev = _evaluate(task, [schedule([3])], Doubling(gain=3), reference=_ref())
    assert len(ev.mismatches) == 1, "one stimulus disagreed"
    assert ev.mismatch_count >= 1 and not ev.passed


# ── the four false passes the review constructed ──

def test_the_reference_cannot_see_what_the_artifact_produced():
    """`SlotEvent` carried the whole accepted batch, and the driver fills that with the
    **output** transfers too. So a reference could read what the artifact produced and
    hand it straight back as the expected output: a wrong design then scored
    `passed=True` with `mismatch_count=0`, because both sides of the comparison came
    from the same place.

    What the artifact produced flows one way, into the matcher, and never into `B*`.
    """
    seen = []

    class Echo(_Model):
        def process(self, in_a):
            del in_a
            return {"out_y": 0}

        def advance_saw(self, event):
            seen.append(tuple(n for n, _ in event.accepted_inputs))

    ref = _ref(Echo)
    task = api.admit(contract(), realization(), reference=ref)
    stim = api.reset_then(task.contract, [(3,)], controls={"out_ready": 1}, drain=3)
    trace = run_device(task, stim, Doubling())

    # The output endpoint transferred, and no event carries it.
    assert api.transfers(trace, "out"), "the design did produce something"
    for index, (slot, record) in enumerate(zip(stim.slots, trace.records)):
        event = api.SlotEvent(index, reset=slot.reset, ticks=slot.ticks,
                              controls=slot.controls,
                              accepted_inputs=[(n, p) for n, p in record.accepted
                                               if n == "in"])
        assert all(name == "in" for name, _ in event.accepted_inputs)
    assert not [n for r in trace.records for n, _ in r.accepted
                if n == "out" and any(n2 == "out" for n2, _ in
                                      api.SlotEvent(0).accepted_inputs)]


def test_a_reference_behind_the_same_interface_is_not_the_frozen_one():
    """The contract check establishes **shape**. Swap a different behavior model in
    behind that shape and the run reported agreement, which is agreement with some
    object rather than with the `B*` the task declares."""
    right = _ref()
    task = api.admit(contract(), realization(), reference=right)

    class Wrong(_Model):
        def process(self, in_a):
            return {"out_y": (in_a * 7) & 0xFF}

    impostor = _live(contract(), Wrong)
    assert impostor.contract.digest == task.contract.digest, "same interface"
    trace = run_device(task, schedule([3]), Doubling())
    with pytest.raises(Refused, match="reference-not-the-frozen-one"):
        api.expected_for(task, impostor, trace)

    # And a task that froze nothing cannot be scored at all: the verdict would be
    # relative to whatever the caller happened to hand in.
    unfrozen = api.admit(contract(), realization())
    with pytest.raises(Refused, match="reference-not-frozen"):
        api.expected_for(unfrozen, right, trace)


def test_a_comparison_cannot_contradict_itself():
    """`Comparison(True, faults=(...))` claimed agreement while carrying the faults that
    deny it, and `Comparison(False, faults=())` claimed a failure with nothing behind it.
    A score reads one field and a report reads the other, so a record holding both
    answers gives each reader the one it wants."""
    from behave.sim.core.compare import Comparison

    assert Comparison().ok
    assert not Comparison("bad", faults=[("value", 0, [1], [2])]).ok
    with pytest.raises(TypeError):
        Comparison(ok=True)                     # there is no such field to set
    with pytest.raises(AttributeError, match="cannot be reassigned"):
        Comparison().why = "rewritten"


def test_a_comparison_against_another_reference_is_refused():
    """`_check_bindings` called `entry.check(stimulus, trace, entry.reference)` -- the
    entry's reference against its own, which passes for every possible value. The task's
    frozen digest is what a comparison has to have been made against."""
    from behave.sim.core.compare import Comparison
    from behave.sim.core.outcome import ConcreteEvaluation
    from behave.sim.core.run_record import RunResult

    ref = _ref()
    task = api.admit(contract(), realization(), reference=ref)
    stim = schedule([3])
    trace = run_device(task, stim, Doubling())

    mine = api.compare_run(task, ref, trace)
    ConcreteEvaluation(task, results=[RunResult.compared(stim, trace, mine)])

    # Against a reference the task did not freeze. The matcher takes the digest off the
    # **task**, so this is arranged by admitting a task that froze a different one --
    # there is no argument through which a caller can name the reference either.
    other = api.admit(contract(), realization(), reference=_ref(gain=3))
    with pytest.raises(Refused, match="run-result-wrong-reference"):
        ConcreteEvaluation(task, results=[RunResult.compared(
            stim, trace, api.compare_run(other, _ref(gain=3), trace))])

    # A run with no trace needs typed attribution: a candidate fault and a simulator
    # outage have different score dispositions even if their prose happened to match.
    with pytest.raises(TypeError, match="exact RunFailure"):
        RunResult.failed_to_run(stim, "")
    with pytest.raises(Refused, match="run-result-comparison-of-another-run"):
        RunResult.compared(stim, trace, Comparison())
    # And a caller cannot state the binding itself, which is how a clean comparison came
    # to be rebound onto a wrong design's trace.
    with pytest.raises(Refused, match="comparison-bound-outside-the-matcher"):
        Comparison(of_stimulus=stim.fingerprint, of_trace=trace.fingerprint,
                   of_reference=task.reference)

    # And the fields cannot be assembled directly, which is what let a trace of one run
    # sit beside a comparison of another.
    with pytest.raises(Refused, match="run-result-not-from-a-constructor"):
        RunResult(stim, trace, mine, task.reference, None, None, True)


def test_a_comparison_of_another_run_cannot_be_filed_under_this_one():
    """The record checked its own shape and never what the comparison was **of**, so a
    clean verdict could be built out of one run's stimulus and another run's trace."""
    from behave.sim.core.compare import Comparison
    from behave.sim.core.run_record import RunResult

    ref = _ref()
    task = api.admit(contract(), realization(), reference=ref)
    one, two = schedule([3]), schedule([5])
    here, there = run_device(task, one, Doubling()), run_device(task, two, Doubling())

    RunResult.compared(one, here, api.compare_run(task, ref, here))
    with pytest.raises(Refused, match="run-result-comparison-of-another-run"):
        RunResult.compared(one, here, api.compare_run(task, ref, there))

    # **The rebinding this replaces.** `Comparison.about(stimulus, trace, reference)`
    # let a caller name the three digests, so the clean comparison of a correct design
    # could be attached to a wrong design's trace and pass -- reproduced on the previous
    # commit as `passed=True, mismatch_count=0` over outputs nothing read. A comparison
    # is now bound only by the matcher, from the trace it consumed.
    assert not hasattr(Comparison(), "about")
    with pytest.raises(Refused, match="comparison-bound-outside-the-matcher"):
        Comparison(of_stimulus=one.fingerprint, of_trace=there.fingerprint,
                   of_reference=task.reference)

    # And the matcher will not take a live model, let alone an expected stream: it
    # builds the reference from the bundle the task froze and runs it itself.
    from behave.sim.core.compare import comparison_for

    with pytest.raises(Refused, match="comparison-reference-not-a-bundle"):
        comparison_for(task, _live(task.contract), here)


def test_one_failed_stimulus_does_not_shift_every_later_result():
    """`evaluate` kept every stimulus and appended only successful traces, so an
    infrastructure failure at index 0 made index 1's comparison point at index 0's trace
    -- which refused, and took the whole evaluation with it."""
    task = api.admit(contract(), realization(), reference=_ref())

    class Fussy(Doubling):
        def settle(self, presented, controls):
            if presented == (99,):
                raise ZeroDivisionError("this one blows up")
            return super().settle(presented, controls)

    got = _evaluate(task, [schedule([99]), schedule([3])], Fussy(),
                       reference=_ref())
    assert len(got.results) == 2
    assert not got.results[0].ran and got.results[0].refused
    assert got.results[1].ran and got.results[1].ok
    assert got.compared == (1,)


def test_two_runs_with_different_faults_do_not_serialise_the_same():
    """The record carried the compared indices and a total, so two evaluations whose
    per-transaction faults were entirely different had the same fingerprint."""
    ref = _ref()
    task = api.admit(contract(), realization(), reference=ref)
    a = _evaluate(task, [schedule([3])], Doubling(gain=3), reference=ref)
    b = _evaluate(task, [schedule([5])], Doubling(gain=3), reference=ref)
    assert a.mismatch_count and b.mismatch_count
    assert a.as_dict()["results"] != b.as_dict()["results"]
    assert a.fingerprint != b.fingerprint


def test_a_serialized_comparison_says_how_many_faults_it_elided():
    """It kept the first twenty silently, so two runs with different hundred-fault sets
    serialised identically."""
    from behave.sim.core.compare import FAULTS_SHOWN, Comparison

    many = Comparison("lots", faults=[("value", i, [i], [0])
                                      for i in range(FAULTS_SHOWN + 7)])
    got = many.as_dict()
    assert got["mismatches"] == FAULTS_SHOWN + 7
    assert len(got["faults"]) == FAULTS_SHOWN
    assert got["faults_elided"] == 7


def test_an_identifier_is_unique_among_the_outstanding_not_over_all_time():
    """The policy says unique among **outstanding** transactions. This built the whole
    expected history into one map and refused a repeated identifier anywhere in it, so
    serial reuse of a small tag space -- the ordinary way these channels work -- was
    called ambiguous and a legal design was refused.
    """
    u8 = api.FiniteType(8)
    ep = api.Endpoint("out", api.Direction.OUT,
                      [api.Field("id", u8, "out_id"), api.Field("y", u8, "out_y")],
                      api.ready_valid("out", "out_valid", "out_ready"), clock="clk",
                      matching=api.Matching.IDENTIFIER, identifier="id")

    assert api.compare(ep, [(1, 10), (1, 20)], [(1, 10), (1, 20)]).ok, \
        "tag 1 answered, then reused: never two outstanding at once"
    assert api.compare(ep, [(1, 10), (2, 20)], [(2, 20), (1, 10)]).ok, \
        "and reordering is what this policy admits"

    wrong = api.compare(ep, [(1, 10), (2, 20)], [(2, 99), (1, 10)])
    assert [f[0] for f in wrong.faults] == ["value"]


def test_a_reference_ticks_on_a_slot_that_names_a_clock_not_on_every_idle_one():
    """T3-01 asks that a reference-visible inputless tick affect `B*`. A model advanced
    on every slot in which nothing was accepted is advanced by the *absence* of a
    transaction -- true of every drain slot -- which is a different thing."""
    class Ticking(_Model):
        def reset(self):
            self.ticks = []

        def tick(self):
            self.ticks.append(1)

    ref = _live(contract(), Ticking)
    task = api.admit(contract(), realization(), reference=ref)
    stim = api.reset_then(task.contract, [(3,)], controls={"out_ready": 1}, drain=4)
    trace = run_device(task, stim, Doubling())

    api.expected_for(task, ref, trace)
    assert ref.model.ticks == [], "no slot of this stimulus names a clock domain"

    ticked = api.reset_then(task.contract, [(3,)], controls={"out_ready": 1},
                            drain=0, ticks=3)
    api.expected_for(task, ref, run_device(task, ticked, Doubling()))
    assert len(ref.model.ticks) == 3, "exactly the slots that name one"


def test_an_unreadable_handshake_refuses_rather_than_reading_as_not_ready():
    """An X or a missing handle both compare false against 1, so an unreadable
    `in_ready` read as "not ready" and an unreadable `out_valid` as "not offering". The
    run then produced a perfectly ordinary trace of a handshake nobody could see, and the
    channel-shape check returns early on a missing sample so it did not catch it either.

    Whether a transfer happened is the one fact the whole record is built on.
    """
    from behave.sim.core.driver import Driver
    from behave.sim.core.refusal import Refused as R

    c = contract()
    stim = api.reset_then(c, [(1,)], controls={"out_ready": 1})
    driver = Driver(c, stim)
    index = 3                                    # the first offering slot
    for bad in ("xx", None):
        with pytest.raises(R, match="driver-handshake-unreadable"):
            driver.observe(index, (1,), {"in_ready": bad, "out_valid": 0, "out_y": 0})


@pytest.mark.cosim
def test_a_channel_whose_kind_nobody_declared_refuses_rather_than_being_guessed():
    """An earlier build read the kind off a run: an endpoint whose `valid` never fell
    outside reset was called a sampled output. A ready/valid producer with a full backlog
    holds `valid` high legitimately, and a finite trace in which it never fell is no
    evidence that it cannot -- so the check refused legal designs and called that a
    finding about the channel.

    A port list cannot distinguish the two, which is exactly why the task has to say.
    """
    from behave.sim.contract_of import contract_of_spec

    plain = contract_of_spec(SPEC, "held")
    assert all(e.kind is api.Kind.UNDECLARED for e in plain.endpoints), \
        "the importer classifies nothing, because it cannot"

    from behave.sim.core.driver import check_channel_shape

    with pytest.raises(Refused, match="adapter-channel-kind-undeclared"):
        check_channel_shape(plain, None, None)

    # And declaring it sampled refuses too, by its own name: this build's adapter
    # describes a request-response channel and says so rather than running one anyway.
    sampled = api.TaskContract(
        endpoints=[api.Endpoint(e.name, e.direction, e.fields, e.adapter,
                                clock=e.clock, kind=api.Kind.SAMPLED)
                   for e in plain.endpoints],
        clocks=list(plain.clocks))
    with pytest.raises(Refused, match="adapter-channel-kind"):
        check_channel_shape(sampled, None, None)



# ── the monitor's accounting, per the review's six ──

def _watched(**kw):
    """A contract declaring everything the monitor needs, per endpoint role."""
    base = contract()
    return api.TaskContract(
        endpoints=[
            api.Endpoint("in", api.Direction.IN, base.endpoint("in").fields,
                         base.endpoint("in").adapter, clock="clk",
                         kind=api.Kind.TRANSACTION,
                         max_outstanding=kw.get("max_outstanding"),
                         reset_policy=kw.get("reset_policy"),
                         response_deadline=kw.get("response_deadline")),
            api.Endpoint("out", api.Direction.OUT, base.endpoint("out").fields,
                         base.endpoint("out").adapter, clock="clk",
                         kind=api.Kind.TRANSACTION,
                         answers_per_transaction=kw.get("answers"),
                         terminal_quiescence=kw.get("quiescent")),
        ],
        clocks=list(base.clocks))


def test_what_is_still_owed_at_the_horizon_is_unresolved_not_a_violation():
    """`terminal_quiescence` says *owes nothing implies quiet*. The check read it as
    *quiet implies owes nothing*, and the difference is every design that takes a cycle
    to compute: a request accepted at slot 3 and answered at slot 5 is silent at slot 4,
    and slot 4 was then cited as proof that a later request went unanswered."""
    from behave.sim.core.monitor import Monitor

    c = _watched(quiescent=True, answers=1)
    stim = api.reset_then(c, [(1,)], controls={"out_ready": 1}, drain=6)
    task = api.admit(c, realization(), reference=_ref())
    report = Monitor(c).check(stim, run_device(task, stim, Deaf()))
    assert not report.violations, report.violations
    assert [u for u in report.unresolved if u.goal == Monitor.UNANSWERED], \
        report.unresolved


def test_the_outstanding_set_retires_even_where_no_ratio_is_declared():
    """It dequeued only when a ratio was declared, so an undeclared task accumulated
    every transaction it ever accepted and reported them all missing at the end."""
    from behave.sim.core.monitor import Monitor

    c = _watched(quiescent=True, answers=1)
    stim = api.reset_then(c, [(1,), (2,)], controls={"out_ready": 1}, drain=2)
    task = api.admit(c, realization(), reference=_ref())
    trace = run_device(task, stim, Doubling())
    report = Monitor(c).check(stim, trace)
    assert not [u for u in report.unresolved if u.goal == Monitor.UNANSWERED], \
        "both were answered, so nothing is owed at the horizon"


class Deaf:
    """A device that accepts and never offers an answer. Several checks need one."""

    kind = "device"
    identity = "doubling"

    def reset(self):
        pass

    def settle(self, presented, controls):
        del presented, controls
        return {"in_ready": 1, "out_valid": 0, "out_y": 0}

    def edge(self):
        pass


def test_a_design_that_never_answers_breaks_the_deadline():
    """It was checked only when an answer finally arrived, so a design that never
    answered scored zero violations **and** zero opportunities -- the quietest possible
    way to pass."""
    from behave.sim.core.monitor import Monitor

    c = _watched(response_deadline=2, answers=1, quiescent=True)
    task = api.admit(c, realization(), reference=_ref())
    stim = api.reset_then(c, [(1,)], controls={"out_ready": 1}, drain=6)
    trace = run_device(task, stim, Deaf())
    found, chances = Monitor(c).check(stim, trace)
    assert any(v.goal == "monitor.response-deadline" for v in found)
    assert chances["monitor.response-deadline"] > 0


def test_the_deadline_is_measured_to_the_offer_the_design_controls():
    """Which event the deadline is measured to decides whose behaviour it is about.

    The design controls when it **offers** an answer; the environment controls when it
    **takes** one. Measuring to the transfer charges the design for the testbench's
    back-pressure, and the previous repair -- counting only the slots in which the
    environment was ready -- swaps one wrong obligation for another that no task
    declares, and lets a design that never offers at all escape while `out_ready` is low.
    """
    from behave.sim.core.monitor import Monitor

    c = _watched(response_deadline=1, answers=1, quiescent=True)
    task = api.admit(c, realization(), reference=_ref())
    stalled = api.reset_then(c, [(1,)], controls={"out_ready": 0}, drain=8)

    # A design that offers on time, held by the environment: not its deadline.
    polite = Monitor(c).check(stalled, run_device(task, stalled, Doubling()))
    assert not [v for v in polite.violations
                if v.goal == "monitor.response-deadline"], \
        "the design offered; nobody took it"

    # A design that never offers, under the same back-pressure: still its deadline.
    deaf = Monitor(c).check(stalled, run_device(task, stalled, Deaf()))
    assert [v for v in deaf.violations if v.goal == "monitor.response-deadline"], \
        "back-pressure is not a licence to never offer an answer"
    assert deaf.opportunities["monitor.response-deadline"] > 0


def test_only_the_obligations_the_task_declares_become_goals():
    """All five used to be instantiated whatever the task said, so a catalogue listed
    four goals nothing could reach and a coverage denominator counted them."""
    from behave.sim.core.monitor import Monitor

    bare = Monitor(contract()).goals()
    assert set(bare) == {
        "monitor.input-completion",
        "monitor.sink-stability",
    }
    full = Monitor(_watched(max_outstanding=1, response_deadline=2, answers=1,
                            quiescent=True)).goals()
    assert len(full) == 5
    assert full["monitor.response-deadline"].where("owed_by") == "artifact"


def test_what_a_reset_does_to_transactions_in_flight_is_declared_not_assumed():
    """It used to be an assumption the monitor printed and then acted on: "a reset slot
    clears what is in flight". An assumption that decides a verdict is an undeclared part
    of the contract -- for a design that keeps its obligations across reset, the monitor
    forgot what was owed and reported a clean run.
    """
    from behave.sim.core.contract import ResetPolicy
    from behave.sim.core.monitor import Monitor

    assert Monitor(contract()).assumptions() == ()

    c = _watched(response_deadline=1, answers=1, quiescent=True)
    task = api.admit(c, realization(), reference=_ref())
    # Accept, then reset while the answer is still owed, then run on.
    slots = list(api.reset_then(c, [(1,)], controls={"out_ready": 1}, drain=6).slots)
    mid = api.Stimulus(queues={"in": [(1,)]},
                       slots=slots[:len(slots) - 4]
                       + [api.EventSlot(reset=True, controls=slots[0].controls)]
                       + slots[len(slots) - 3:])
    undeclared = Monitor(c).check(mid, run_device(task, mid, Deaf()))
    assert [u for u in undeclared.unresolved
            if u.goal == Monitor.CORRELATION
            and "does not declare whether a reset clears" in u.why], \
        "with no policy declared, nothing that counts outstanding transactions can be " \
        "judged past the reset"
    # The deadline it broke *before* the reset stands: that is a fact the run observed.
    # What stops is everything that would have to count what is outstanding afterwards.
    reset_at = len(slots) - 4
    assert all(v.slot < reset_at for v in undeclared.violations)
    assert not [u for u in undeclared.unresolved if u.goal == Monitor.UNANSWERED]

    # **One run, two policies, two different states at the horizon.** No deadline
    # declared here, deliberately: with one, the transaction's deadline elapses before
    # the reset and `monitor.response-deadline` has already reported it, so the terminal
    # state says nothing new either way and the policies would look alike.
    trace = run_device(task, mid, Deaf())
    cleared = Monitor(_watched(answers=1, quiescent=True,
                               reset_policy=ResetPolicy.CLEARS)).check(mid, trace)
    assert not [u for u in cleared.unresolved if "reset" in u.why]
    assert not [u for u in cleared.unresolved if u.goal == Monitor.UNANSWERED], \
        "the policy says the reset discarded it, so nothing is owed at the end"

    kept = Monitor(_watched(answers=1, quiescent=True,
                            reset_policy=ResetPolicy.KEEPS)).check(mid, trace)
    assert [u for u in kept.unresolved if u.goal == Monitor.UNANSWERED], \
        "and the other policy says it survived, which is a different verdict on one run"
