"""From a provider's candidates to the canonical stimulus set a run is over.

**Definition.** One pipeline, in order: decode each witness through the legacy boundary
(`stimuli_from_witnesses`), give each candidate its own declared drain, pad the set to
one schedule horizon `N`, deduplicate **after** the padding, merge provenance per
surviving fingerprint, and hand the caller a set it may cap (`canonical_set`). The
effective reset hold is supplied by the runtime caller, not by a task-local file.
This module also decodes explicit finite schedules; ordinary public baseline
requests use `core.runtime_schedule` and acquire their horizon during execution.

**Constraints.**

* A recorded injection schedule is the provider's environment, taken as recorded;
  `stall_every` constructs injection for candidates that carry no schedule. Drain is
  receptive so environment back-pressure cannot prevent completion.
* Every schedule begins with exactly the run's reset hold, ends with at least the run's
  drain after its last offer, and is `N` slots long.
* A slot never carries a presented control (`TaskContract.scheduled_controls`), and
  every recorded control is one bit.
* Provenance is per surviving stimulus, keyed by the fingerprint of what runs, listing
  every candidate that canonicalised to it.

**Refusals.** `authority-provider-missing-column`, `authority-no-usable-candidate`,
`authority-horizon-shorter-than-a-candidate`; per candidate (dropped and counted, never
the set): `authority-recorded-schedule-empty`, `-incomplete`, `-control-not-a-bit`,
`-reset-not-the-run-s`.

Split out of `authority.py`, which had grown every one of these concerns inline; the
history of each rule is in that file's git log and the review ledger.
"""

from __future__ import annotations

import hashlib
import json

from behave.core.records import fields

from .core.frozen import Frozen
from .core.refusal import Refusal, Refused

__all__ = [
    "GenerationSpec", "canonical_set", "effective_queue_bound",
    "multiclock_stimulus_from_witness", "one_horizon", "provenance_of",
    "schedule_provenance_after_generation", "stimuli_from_witnesses",
    "stimulus_admissible",
]


class GenerationSpec(Frozen):
    """What generation is handed: the declared domain fields plus construction policy.

    **Definition.** Frozen at admission, **before any candidate exists**: the schedule
    horizon `N`, the per-endpoint queue bound and the effective reset hold -- the
    trace-domain fields -- beside the drain and the back-pressure period, which are
    stimulus **construction** policy. The providers generate inside it rather than the
    domain being read off whatever they produced.

    **Deliberately not "the trace domain".** The domain identity is
    `core/stimulus.trace_domain`'s, and it excludes drain and stall on purpose --
    construction policy is per-stimulus provenance, not identity. Generation needs
    both halves at once (`max_body_steps` is a relation between them), which is what
    this record is: an explicit runtime generation policy, not a task declaration
    or a second spelling of the domain string.

    Appendix A.3 says generation preserves the horizon and the queue bound; until this
    record existed the engines received only `n_steps` and the declared numbers first
    met a candidate in the post-generation membership drops -- solver budget spent on
    candidates the run could never carry, and one oversized candidate per provider
    turning the verdict `partial`. The membership check stays, as the backstop for a
    construction bug, not as the policy.

    JSON-shaped on purpose (`as_dict`): it crosses the worker boundary to the engines.
    """

    __slots__ = ("drain", "horizon", "queue_bound", "reset_hold_cycles",
                 "stall_every")

    def __init__(self, horizon, queue_bound, reset_hold_cycles, drain,
                 stall_every=None):
        values = {
            "horizon": horizon,
            "queue_bound": queue_bound,
            "reset_hold_cycles": reset_hold_cycles,
            "drain": drain,
        }
        for name, value in values.items():
            if type(value) is not int:
                raise Refused(Refusal(
                    "authority-generation-spec-field-not-an-integer",
                    "a generation-policy quantity must be an exact integer; coercing "
                    "a string, Boolean or fractional value would run a policy other "
                    "than the admitted one",
                    field=name, got=repr(value)))
        if type(stall_every) is not int and stall_every is not None:
            raise Refused(Refusal(
                "authority-generation-spec-field-not-an-integer",
                "a generation-policy quantity must be an exact integer; coercing "
                "a string, Boolean or fractional value would run a policy other "
                "than the admitted one",
                field="stall_every", got=repr(stall_every)))
        if horizon < 1 or queue_bound < 1 \
                or reset_hold_cycles < 0 or drain < 0 \
                or (stall_every is not None and stall_every < 0):
            raise Refused(Refusal(
                "authority-generation-spec-field-out-of-range",
                "the generation policy needs a positive horizon and queue bound, "
                "and nonnegative reset, drain and stall quantities",
                **values, stall_every=stall_every))
        self.horizon = horizon
        self.queue_bound = queue_bound
        self.reset_hold_cycles = reset_hold_cycles
        self.drain = drain
        self.stall_every = stall_every
        if self.max_body_steps < 1:
            # The same relation `declarations_at` refuses at admission
            # (`run-policy-domain-uninhabitable`); re-checked here because a direct
            # caller can construct a spec without a manifest.
            raise Refused(Refusal(
                "authority-domain-uninhabitable",
                "this domain cannot carry a single offer: the horizon does not fit "
                "the reset hold, one body slot and the drain, so every candidate "
                "would be dropped and the run would be over nothing",
                horizon=self.horizon, reset_hold_cycles=self.reset_hold_cycles,
                drain=self.drain))
        self._freeze()

    @property
    def max_body_steps(self):
        """The longest witness body this domain can carry, in offers.

        A constructed schedule is `reset hold + body + drain` slots and its queue
        holds one transaction per body row; a recorded one is its own cycles (which
        begin with the same hold) plus the drain. So one number bounds both:
        `min(queue_bound, horizon - reset_hold_cycles - drain)`.
        """
        return min(self.queue_bound,
                   self.horizon - self.reset_hold_cycles - self.drain)

    def as_dict(self):
        return {"horizon": self.horizon, "queue_bound": self.queue_bound,
                "reset_hold_cycles": self.reset_hold_cycles, "drain": self.drain,
                "stall_every": self.stall_every,
                "max_body_steps": self.max_body_steps}

    @classmethod
    def from_dict(cls, image):
        """Restore an exact public record; derived fields are not overrides."""
        image = fields(image, {*cls.__slots__, "max_body_steps"})
        spec = cls(**{name: image[name] for name in cls.__slots__})
        if type(image["max_body_steps"]) is not int or image["max_body_steps"] != spec.max_body_steps:
            raise Refused(Refusal(
                "authority-generation-spec-derived-field-mismatch",
                "max_body_steps must match the declared generation policy"))
        return spec

    @property
    def identity(self):
        """A versioned identity for the complete frozen generation policy."""

        image = json.dumps(
            self.as_dict(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False)
        return f"generation-spec/1:{hashlib.sha256(image.encode()).hexdigest()}"


def _multiclock_controls(contract, resets, index, stall_every):
    """The complete scheduled environment valuation for one atomic slot.

    Reset levels come from the slot's per-domain reset set.  The only other
    environment action currently constructed by the production scheduler is the
    registered adapters' response-acceptance pins; the declared stall rule is
    applied by global-slot index.  Other adapter controls are not guessed.
    """
    from .core.protocol import receptive_controls

    controls = {}
    reset_set = set(resets)
    for domain in contract.clocks:
        asserted = 0 if domain.reset_active_low else 1
        controls[domain.reset] = asserted if domain.name in reset_set else 1 - asserted
    for name in receptive_controls(contract):
        controls[name] = 0 if stall_every and index % stall_every == 0 else 1
    missing = sorted(set(contract.scheduled_controls()) - set(controls))
    if missing:
        raise Refused(Refusal(
            "atomic-schedule-control-policy-incomplete",
            "the atomic schedule construction has no declared value for an "
            "environment-owned control",
            controls=missing))
    return {name: controls[name] for name in contract.scheduled_controls()}


def multiclock_stimulus_from_witness(contract, witness, *, drain=0,
                                     stall_every=0, source=""):
    """Decode one generated atomic witness into the canonical scoring stimulus.

    The witness's embedded ``expected`` rows are deliberately not read.  They are
    generation diagnostics; the score derives its expected streams by replaying the
    frozen reference over the artifact's accepted input transfers.  What this boundary
    consumes is only the authenticated physical schedule and its input actions.
    """
    from .core.atomic_witness import (
        validate_atomic_witness_identity,
        witness_invocations,
    )
    from .core.clock_schedule import GlobalEventSlot, GlobalSchedule
    from .core.stimulus import EventSlot, Stimulus

    if type(drain) is not int or drain < 0:
        raise Refused(Refusal(
            "atomic-schedule-drain-invalid",
            "an atomic candidate drain is an exact nonnegative slot count",
            got=repr(drain)))
    if stall_every is not None and (
            type(stall_every) is not int or stall_every < 0):
        raise Refused(Refusal(
            "atomic-schedule-stall-invalid",
            "an atomic candidate stall period is absent or an exact nonnegative "
            "slot count",
            got=repr(stall_every)))
    if type(source) is not str:
        raise Refused(Refusal(
            "atomic-schedule-source-invalid",
            "atomic stimulus provenance is an exact string",
            got=repr(source)))

    validate_atomic_witness_identity(witness)
    required = {
        "version", "generation_policy", "schedule", "schedule_digest", "slots",
    }
    if not required <= set(witness):
        raise Refused(Refusal(
            "atomic-witness-record-malformed",
            "an atomic witness carries its schedule, schedule digest and one action "
            "record per slot",
            missing=sorted(required - set(witness))))
    schedule = GlobalSchedule.from_dict(witness["schedule"])
    schedule.assert_digest(witness["schedule_digest"], consumer="authority-generation")
    records = witness["slots"]
    if type(records) is not list or len(records) != len(schedule.slots):
        raise Refused(Refusal(
            "atomic-witness-action-count-mismatch",
            "an atomic witness carries one action record per timestamped slot",
            schedule=len(schedule.slots),
            actions=None if type(records) is not list else len(records)))

    by_domain = {clock.name: [] for clock in contract.clocks}
    for endpoint in contract.endpoints:
        by_domain[endpoint.clock].append(endpoint)
    inputs = {}
    for domain, endpoints in by_domain.items():
        found = tuple(
            endpoint for endpoint in endpoints
            if getattr(endpoint.direction, "value", endpoint.direction) == "in")
        if found:
            inputs[domain] = found

    # Invocation arguments are positional.  A generated witness therefore carries the
    # source signature that fixed their order: the fields of every logical input endpoint
    # in the TaskContract's canonical order.  Legacy one-field witnesses are unambiguous
    # and remain readable; a larger product without this binding is refused rather than
    # guessed or sorted from source text.
    binding = witness.get("domain_inputs")
    expected_binding = {
        domain: [field.port
                 for endpoint in inputs.get(domain, ())
                 for field in endpoint.fields]
        for domain in sorted(by_domain)
    }
    if binding is None:
        if any(len(names) > 1 for names in expected_binding.values()):
            raise Refused(Refusal(
                "atomic-witness-input-order-unstated",
                "a multi-field invocation is positional but the witness does not name "
                "the source signature that ordered it",
                expected=expected_binding))
    elif (type(binding) is not dict
          or any(type(domain) is not str or type(names) is not list
                 or any(type(name) is not str for name in names)
                 for domain, names in binding.items())
          or binding != expected_binding):
        raise Refused(Refusal(
            "atomic-witness-input-binding-mismatch",
            "the witness's positional invocation values name different fields than "
            "the task contract",
            recorded=binding, contract=expected_binding))

    queues = {
        endpoint.name: []
        for endpoints in inputs.values()
        for endpoint in endpoints
    }
    events = []
    for index, (scheduled, record) in enumerate(zip(schedule.slots, records)):
        got = GlobalEventSlot.from_dict(record, allow_actions=True)
        if got.as_dict() != scheduled.as_dict():
            raise Refused(Refusal(
                "atomic-witness-schedule-action-mismatch",
                "an action record changes the timestamp, edge or reset set fixed by "
                "the witness schedule",
                slot=index, expected=scheduled.as_dict(), got=got.as_dict()))
        invocations = witness_invocations(got, record)
        raw_actions = {action[1]: action[0] for action in record["actions"]}
        offers = []
        for invocation in invocations:
            endpoints = inputs.get(invocation.domain, ())
            operation = raw_actions[invocation.domain]
            if not endpoints:
                if operation != "tick":
                    raise Refused(Refusal(
                        "atomic-witness-drive-without-input-endpoint",
                        "a drive action names a domain with no environment-driven "
                        "transaction endpoint",
                        domain=invocation.domain, slot=index))
                continue
            if operation != "drive":
                raise Refused(Refusal(
                    "atomic-witness-tick-on-input-domain",
                    "an input-domain transaction is represented by a drive action, not "
                    "a clock-only tick",
                    domain=invocation.domain,
                    endpoints=[endpoint.name for endpoint in endpoints], slot=index))
            payload = tuple(invocation.args)
            arity = sum(len(endpoint.fields) for endpoint in endpoints)
            if len(payload) != arity:
                raise Refused(Refusal(
                    "atomic-witness-input-arity-mismatch",
                    "an invocation carries a different number of values than the "
                    "ordered logical input product in its clock domain",
                    domain=invocation.domain,
                    endpoints=[endpoint.name for endpoint in endpoints],
                    values=len(payload), fields=arity, slot=index))
            offset = 0
            for endpoint in endpoints:
                width = len(endpoint.fields)
                queues[endpoint.name].append(tuple(
                    _logical_provider_value(field, value)
                    for field, value in zip(
                        endpoint.fields, payload[offset:offset + width])))
                offers.append(endpoint.name)
                offset += width
        event = EventSlot(
            offers=offers,
            controls=_multiclock_controls(
                contract, scheduled.resets, index, stall_every),
            ticks=scheduled.events, resets=scheduled.resets)
        events.append(GlobalEventSlot(scheduled.time, event=event))

    # A drain is part of every candidate before the set is padded to one horizon.
    # Extend the physical schedule itself so no timestamp or coincidence set is
    # invented by an idle-tail helper that only knows logical slots.
    total = len(events) + drain
    if total > len(events):
        induced = GlobalSchedule.periodic(schedule.timings, total)
        prefix = [(slot.time, slot.events) for slot in induced.slots[:len(events)]]
        recorded = [(slot.time, slot.events) for slot in schedule.slots]
        if prefix != recorded:
            raise Refused(Refusal(
                "atomic-witness-schedule-prefix-not-induced",
                "the recorded schedule cannot be extended by its own declared clock "
                "timings",
                recorded=recorded, induced=prefix))
        for index, scheduled in enumerate(induced.slots[len(events):], len(events)):
            event = EventSlot(
                controls=_multiclock_controls(contract, (), index, None),
                ticks=scheduled.events)
            events.append(GlobalEventSlot(scheduled.time, event=event))

    complete_schedule = GlobalSchedule(schedule.timings, events)
    return Stimulus(
        queues=queues, slots=[slot.event for slot in events],
        source=source or f"authority/{witness.get('source', 'provider')}/atomic",
        clock_schedule=complete_schedule)


def _atomic_stimuli_from_witnesses(contract, witnesses, drain, stall_every,
                                   queue_bound, horizon):
    out, dropped, origin = [], [], {}
    for witness in witnesses:
        try:
            stimulus = multiclock_stimulus_from_witness(
                contract, witness, drain=drain, stall_every=stall_every,
                source=f"authority/{witness.get('source', 'provider')}/atomic"
                if type(witness) is dict else "authority/atomic")
            stimulus = _with_memory_environment(
                contract, stimulus, witness,
                len(stimulus.slots) if horizon is None else int(horizon))
        except Refused as exc:
            dropped.append({
                "source": (str(witness.get("source", ""))[:40]
                           if type(witness) is dict else ""),
                "carries": [exc.refusal.kind],
            })
            continue
        if queue_bound is not None:
            over = [(endpoint.name, stimulus.depth(endpoint.name))
                    for endpoint in contract.inputs()
                    if stimulus.depth(endpoint.name) > int(queue_bound)]
            if over:
                dropped.append({
                    "source": str(witness.get("source", ""))[:40],
                    "carries": ["authority-queue-exceeds-the-bound"],
                    "queues": over[:4],
                })
                continue
        if horizon is not None and len(stimulus.slots) > int(horizon):
            dropped.append({
                "source": str(witness.get("source", ""))[:40],
                "carries": ["authority-candidate-exceeds-the-horizon"],
                "slots": len(stimulus.slots), "horizon": int(horizon),
            })
            continue
        origin.setdefault(stimulus.fingerprint, []).append({
            "schedule": "recorded-atomic",
            "of": str(witness.get("source", "provider"))[:40],
            "witness_version": witness["version"],
            "generation_policy": witness["generation_policy"],
            "digest": witness.get("schedule_digest", ""),
            "consumed_digest": stimulus.clock_schedule.digest,
            "slots": len(stimulus.slots),
        })
        out.append(stimulus)
    out.sort(key=lambda stimulus: stimulus.fingerprint)
    if witnesses and not out:
        raise Refused(Refusal(
            "authority-no-usable-candidate",
            "every atomic candidate was outside the declared schedule or endpoint "
            "binding, so there is no nonempty stimulus set to score",
            offered=len(witnesses), dropped=dropped))
    return tuple(out), tuple(dropped), origin


def stimuli_from_witnesses(contract, witnesses, order, drain, stall_every=0,
                           reset_hold_cycles=None, queue_bound=None,
                           horizon=None):
    """One `Stimulus` per usable candidate: `(stimuli, dropped, origin)`.

    **Not** a canonical set. Padding to `N` happens afterwards and can make two of these
    equal, so deduplication is `canonical_set`'s, after it.

    * `stimuli` -- one per candidate, in the providers' order, each carrying its own
      declared drain.
    * `dropped` -- candidates this path does not consume, by name, so a rate has a
      denominator: a witness carrying memory contents, or a recorded schedule this run
      cannot carry. One bad candidate does not refuse the set.
    * `origin` -- provenance rows per pre-canonical fingerprint, merged by
      `canonical_set` into the surviving stimulus's.

    Every candidate goes through `core/stimulus.from_legacy`, which is the only place a
    legacy form is read: a candidate carrying `interleave` or `actions` has ticks and
    resets a row list cannot express, and reading `inputs` alone ran a shorter stimulus
    or none. ``order`` is the provider's column order; reindexing to the contract's
    field order happens before that boundary, since both forms are positional.

    Refusals: `authority-provider-missing-column` where the rows do not carry every
    declared field; `authority-no-usable-candidate` where every candidate was dropped.
    """
    from .core.framing import framed, reset_then
    from .core.stimulus import from_legacy

    inputs = contract.inputs()
    if len(contract.clocks) > 1:
        if not inputs:
            raise Refused(Refusal(
                "unsupported-no-input-endpoint",
                "the score is over accepted input transfers, and this multi-clock "
                "task declares no environment-driven transaction endpoint"))
        return _atomic_stimuli_from_witnesses(
            contract, witnesses, drain, stall_every, queue_bound, horizon)
    if not inputs:
        # A task with nothing to offer is driven by ticks; the provider's rows are not
        # about it, and a stimulus built from them would describe a different run.
        #
        # **Every value, like every other exit.** This returned the stimulus tuple alone
        # after the second return value was added, so an output-only task -- 24 designs
        # of this population -- unpacked a `Stimulus` into `(stimuli, dropped)` and died
        # on the first one that reached here. A third joined them; the same care applies.
        # `core/supported.refusals_for` refuses a contract with no input endpoint before
        # any of this runs -- the score is a rate over accepted input transfers, and a
        # task with no input endpoint has no denominator -- so on the authoritative path
        # this branch is unreachable. It stays for a direct caller, and its provenance is
        # a **list** of rows like every other stimulus's: it was a bare dict, which
        # `canonical_set` iterates as rows, and the first output-only contract through it
        # crashed the whole evaluation into `authority-crashed`.
        only = _with_back_pressure(
            reset_then(contract, [], drain=drain, ticks=max(1, drain),
                       reset_hold_cycles=reset_hold_cycles,
                       source="authority/ticks"),
            contract, stall_every, drain=drain)
        return ((only,), (),
                {only.fingerprint: [{"schedule": "constructed",
                                     "of": "authority/ticks",
                                     "digest": "", "slots": len(only.slots)}]})
    source = inputs[0]
    columns = {str(name): i for i, name in enumerate(order)}
    missing = [f.port for f in source.fields if f.port not in columns]
    if missing:
        raise Refused(Refusal(
            "authority-provider-missing-column",
            "the provider's rows do not carry every field this endpoint declares, so a "
            "stimulus built from them would offer a transaction the task does not "
            "describe",
            endpoint=source.name, missing=missing, provider_columns=list(order)))
    picks = [columns[f.port] for f in source.fields]

    # `origin` is this stimulus's provenance, keyed while it is being built and read
    # again *after* dedup and the cap, which is the order the counting got wrong.
    out, dropped, origin = [], [], {}
    for witness in witnesses:
        unconsumed = _unconsumed(contract, witness)
        if unconsumed:
            # **This candidate, not the run.** Vetoing the whole task because one
            # candidate in 761 carries a recorded schedule throws away 751 that do not,
            # and the seam run found exactly that: three of six declared designs refused
            # outright on ten witnesses out of hundreds. Dropping it silently is the
            # other error -- so it is dropped by name and counted, and the count travels
            # with the result, because the size of `Xi_A` is what every rate is over.
            dropped.append({"source": str(witness.get("source", ""))[:40],
                            "carries": unconsumed})
            continue
        try:
            decoded = _reindexed(witness, picks, fields=source.fields)
        except Refused as exc:
            dropped.append({
                "source": str(witness.get("source", ""))[:40],
                "carries": [exc.refusal.kind],
            })
            continue
        body = from_legacy(decoded, endpoint=source.name)
        if witness.get("schedule"):
            # **The environment the provider actually ran, rather than one invented
            # here.** These were dropped, which made every design whose providers record
            # a schedule `partial` -- 10 of 761 candidates was enough. T3-02's complaint
            # about the old replay is exactly the other error: it *ignored* the recorded
            # schedule and invented its own stalls.
            #
            # The witness carries both forms and they answer different questions: the
            # rows are the transactions, and the schedule is the per-cycle environment
            # around them. So the queue still comes from the rows -- the driver decides
            # presentation from its pointer, which is the one rule -- and the controls
            # come from the schedule.
            try:
                stimulus = _on_recorded_schedule(
                    contract, body, witness["schedule"],
                    source=f"authority/{witness.get('source', 'provider')}/recorded",
                    drain=drain, stall_every=stall_every,
                    reset_hold_cycles=reset_hold_cycles)
            except Refused as exc:
                # **This candidate, not the run** -- the same rule the unconsumed
                # candidates above get, and it was not applied here. A schedule this run
                # cannot carry took the whole evaluation to `refused`, so one candidate
                # in several hundred decided the fate of the rest. It is dropped by name
                # and counted, and the count travels with the result.
                dropped.append({"source": str(witness.get("source", ""))[:40],
                                "carries": [exc.refusal.kind]})
                continue
            where = "recorded"
        else:
            stimulus = _with_back_pressure(
                framed(contract, body.slots, queues=body.queues,
                       endpoint=source.name, drain=drain,
                       reset_hold_cycles=reset_hold_cycles,
                       memory_policy=dict(body.memory_policy),
                       initial_state=dict(body.initial_state),
                       source=f"authority/{witness.get('source', 'provider')}"),
                contract, stall_every, drain=drain)
            where = "constructed"
        try:
            stimulus = _with_memory_environment(
                contract, stimulus, witness,
                len(stimulus.slots) if horizon is None else int(horizon))
        except Refused as exc:
            dropped.append({
                "source": str(witness.get("source", ""))[:40],
                "carries": [exc.refusal.kind],
            })
            continue
        # **The declared domain, per candidate -- fixed before generation, never read
        # off the set.** Where the run states a queue bound, a candidate whose queue
        # holds more transactions is not a member of this run's trace domain; where it
        # states a horizon, a candidate longer than `N` cannot be padded *to* `N`. Both
        # drop by name like every other inadmissible candidate rather than deciding the
        # set's fate -- a candidate problem is never a whole-run refusal. (The earlier
        # comment here reasoned that an unstated bound "derives to the horizon because a
        # queue is spent at one transfer per slot"; that bounds *consumption*, not the
        # initial queue depth a provider hands over, and was simply wrong. An unstated
        # bound constrains nothing -- see `effective_queue_bound`.)
        if queue_bound is not None:
            over = [(e.name, stimulus.depth(e.name)) for e in inputs
                    if stimulus.depth(e.name) > int(queue_bound)]
            if over:
                dropped.append({"source": str(witness.get("source", ""))[:40],
                                "carries": ["authority-queue-exceeds-the-bound"],
                                "queues": over[:4]})
                continue
        if horizon is not None and len(stimulus.slots) > int(horizon):
            dropped.append({"source": str(witness.get("source", ""))[:40],
                            "carries": ["authority-candidate-exceeds-the-horizon"],
                            "slots": len(stimulus.slots), "horizon": int(horizon)})
            continue
        # **No dedup here.** This set is not canonical yet: `one_horizon` pads every
        # member to `N` afterwards, and two stimuli that differ only in their tail become
        # equal once both are padded -- so a dedup at this point leaves duplicates in the
        # final set, and the old raw evaluator removed them again silently. Deduplication
        # is `canonical_set`'s, after the padding.
        #
        # Provenance is collected per candidate and merged there, for the same reason: a
        # count taken before the dedup and before the cap said two where one stimulus
        # ran. Reproduced: two identical recorded witnesses gave `stimuli = 1,
        # recorded_count = 2`.
        origin.setdefault(stimulus.fingerprint, []).append({
            "schedule": where,
            "of": str(witness.get("source", "provider"))[:40],
            "digest": _schedule_digest(witness.get("schedule")),
            "slots": len(stimulus.slots),
        })
        out.append(stimulus)
    # **A determined order, not the one the providers happened to return.**
    #
    # `Xi_A` is a set, and a batched replay drives it as a sequence -- the design sees the
    # stimuli back to back -- so the sequence is part of the experiment. The providers
    # search in parallel and hand their candidates back in whatever order they finish, so
    # two runs over an identical set replay it in different orders unless something fixes
    # one. Sorting by fingerprint is arbitrary in the way any fixed order is, and that is
    # the point: it makes the sequence a function of the set.
    #
    # **This is not offered as the explanation for anything.** It was introduced as the
    # cause of `comparator_3bit`'s mismatch count moving between runs, and that claim did
    # not survive its own test: every one of that design's 761 records is identical under
    # sorted and reversed replay, so its score cannot depend on the order. The cause was
    # elsewhere -- see `rtl/artifact._env_for_this_tree`. A determined order is right
    # regardless; it just did not fix that.
    out.sort(key=lambda s: s.fingerprint)
    if witnesses and not out:
        raise Refused(Refusal(
            "authority-no-usable-candidate",
            "every candidate the providers produced carries something this path does "
            "not consume, so there is nothing left to run and a verdict over the empty "
            "set would say the design was never wrong",
            offered=len(witnesses), dropped=dropped))
    return tuple(out), tuple(dropped), origin


def _schedule_digest(recorded):
    """The full digest of recorded cycles, or ``""`` when none were recorded.

    Provenance a reader can check rather than a word. "This stimulus came from a recorded
    schedule" and "this stimulus came from *that* recorded schedule" are different
    statements, and only the second lets anybody go back to the candidate and compare.
    """
    import hashlib
    if not recorded:
        return ""
    from .core.record_value import canonical_json

    return hashlib.sha256(
        canonical_json(recorded).encode()
    ).hexdigest()


def one_horizon(stimuli, contract, declarations):
    """Every stimulus of the set at one length: `(padded, N)`.

    `drain` is the slots that follow the last offer; `N` is the length of a run. They
    are different quantities and the code called the first one `horizon` while a
    constructed stimulus was `reset + body + drain` and a recorded one was its
    schedule's own length with no drain at all -- so one evaluation ran its members over
    a dozen lengths, which is not one trace domain (Appendix B; `todo3` T3-04).

    Each candidate carries its own declared drain **before** this is called -- `framed`
    appends it for a constructed stimulus and `_on_recorded_schedule` for a recorded one
    -- so the longest member already ends `drain` slots after its last offer. `N` is that
    length, and everything shorter is padded with the shared framing rule. Every stimulus then
    has at least `drain` idle slots after its last offer, and all of them the same `N`.

    Refusal: `authority-horizon-shorter-than-a-candidate`, where a declared `N` meets a
    candidate that does not fit in it. Truncating would drop offers the provider made.
    On the authoritative path this is a backstop, not the policy: `evaluate_admitted`
    hands the declared horizon to `stimuli_from_witnesses`, which drops an over-`N`
    candidate by name before the set is canonicalised -- a candidate problem is that
    candidate's, never the run's.

    `N` enters the trace domain identity (`core/stimulus.trace_domain`), so two runs at
    two horizons are two domains rather than two numbers under one name -- and a score
    under this rule is not comparable to one from before it, which is what
    `RESULT_VERSION` says.
    """
    stimuli = tuple(stimuli)
    if not stimuli:
        return stimuli, 0
    declared = getattr(declarations, "horizon", None)
    longest = max(len(s.slots) for s in stimuli)
    if declared is None:
        horizon = longest
    elif int(declared) < longest:
        # A declared N shorter than a candidate is a candidate this run cannot carry, and
        # truncating it would drop offers the provider made. Named, with the number.
        raise Refused(Refusal(
            "authority-horizon-shorter-than-a-candidate",
            "this run declares a schedule horizon shorter than the longest stimulus its "
            "providers produced, so running under it would cut offers off the end and "
            "score a design on transactions it was never given",
            declared=int(declared), longest=longest))
    else:
        horizon = int(declared)
    stall = getattr(declarations, "stall_every", None)
    return (tuple(_padded_to(contract, s, horizon, stall) for s in stimuli), horizon)


def canonical_set(stimuli, origin, contract, declarations):
    """The set a run is over: padded to one `N`, deduplicated, provenance merged.

    Returns `(stimuli, N, provenance)`. `stimuli` is sorted by fingerprint and every
    member is distinct; `provenance` is keyed by those fingerprints and each entry lists
    every candidate that produced that stimulus.

    **Why the order is padding, then dedup, and not the other way.** Deduplicating first
    is deduplicating a set that is not canonical yet: two stimuli differing only in their
    tail become one once both are padded to `N`. That happened -- `distinct_before=2,
    members_after=2, distinct_after=1` -- and the consequences were spread across the
    record: the telemetry said two stimuli, the evaluation held one result because
    The old raw evaluator removed the duplicate itself and said nothing, the second candidate's
    provenance overwrote the first's, and the cap's denominator was not the set that ran.

    So: pad, then deduplicate, then let the caller cap. Concrete evaluation refuses a set with
    a repeat rather than quietly making one, because a caller handing it duplicates is a
    caller whose count of `Xi_A` is already wrong.
    """
    padded, horizon = one_horizon(stimuli, contract, declarations)
    merged, out = {}, []
    for before, after in zip(stimuli, padded):
        if after.fingerprint not in merged:
            merged[after.fingerprint] = []
            out.append(after)
        for row in origin.get(before.fingerprint, ()):
            row = dict(row)
            row["padded_from"] = len(before.slots)
            row["slots"] = len(after.slots)
            if row not in merged[after.fingerprint]:
                merged[after.fingerprint].append(row)
    out.sort(key=lambda s: s.fingerprint)
    # **Every member passes the domain's own membership test.** The constructions above
    # are supposed to guarantee this; the predicate is what the guarantee means, and a
    # violation here is this module contradicting itself -- refused as such rather than
    # handed to an evaluation as a member of a domain it is not in.
    hold = _hold_of(contract, getattr(declarations, "reset_hold_cycles", None))
    bound = effective_queue_bound(declarations, horizon)
    for stimulus in out:
        faults = stimulus_admissible(stimulus, contract, horizon=horizon,
                                     reset_hold_cycles=hold, queue_bound=bound)
        if faults:
            raise Refused(Refusal(
                "authority-canonical-set-outside-its-domain",
                "a stimulus this module canonicalised fails the domain's own "
                "membership predicate, so the construction and the definition "
                "disagree and neither can be trusted for this run",
                stimulus=stimulus.fingerprint, faults=faults))
    return tuple(out), horizon, merged


def effective_queue_bound(declarations, horizon):
    """The domain's queue bound: the declared one, or `None` where none is declared.

    This used to fall back to the horizon, reasoning that "a queue cannot be spent
    faster than one transfer per slot, so a run of `N` slots bounds every queue at
    `N`". That bounds how much of a queue a run can *consume*; it says nothing about
    how deep a queue a provider may hand over, so a depth-10 queue under `N = 5` was
    called a member of a domain whose identity string claimed `queue_bound = 5` -- and
    tripped `canonical_set`'s own membership check into refusing the whole run. The
    bound is a fact of the declared run policy, fixed before generation: declared, it
    is the number; undeclared, the domain states none and the membership test skips
    it. `horizon` stays in the signature because callers pass
    it positionally; it no longer feeds the answer.
    """
    declared = getattr(declarations, "queue_bound", None)
    return int(declared) if declared is not None else None


def stimulus_admissible(stimulus, contract, *, horizon, reset_hold_cycles,
                        queue_bound):
    """Why this **stimulus** is not well-formed for the run's domain. `[]` means it is.

    **What this is, and is deliberately not.** It is the static membership test over the
    stimulus half of the domain -- the fields `trace_domain` binds into the identity
    string, as one function, run by `canonical_set` over every member of the final set.
    It is **not by itself** the paper's `K_N^env` (appendix, RTL trace domain), which is
    over the complete closed-loop trace.  After execution, the registered adapter
    replays that trace and checks its environment-owned assumptions; a failure makes the
    record ``Outside`` and the same predicate constrains the exact relation.  This
    function owns only the static stimulus half and therefore does not duplicate that
    state-dependent protocol check.

    A well-formed stimulus:

    * is exactly ``horizon`` slots long;
    * begins with exactly ``reset_hold_cycles`` reset slots, and the flag and the pin
      agree everywhere (`Stimulus.faults_against`);
    * offers only what its queues hold, with at most ``queue_bound`` transactions
      queued per input endpoint (skipped where the run declares no bound: an unstated
      bound constrains nothing, rather than being quietly derived from the set);
    * names every scheduled control in every slot, one bit each, and no presented one.
    """
    out = list(stimulus.faults_against(contract))
    if not contract.clocks and reset_hold_cycles:
        out.append("clockless settled sampling requires a zero reset-hold count")
    if len(stimulus.slots) != int(horizon):
        out.append(f"the schedule is {len(stimulus.slots)} slots long and the domain's "
                   f"horizon is {horizon}")
    if len(contract.clocks) == 1:
        from .core.stimulus import leading_reset_slots

        held = leading_reset_slots(stimulus.slots)
        if held != int(reset_hold_cycles):
            out.append(f"the schedule holds reset for {held} slots and the domain "
                       f"holds {reset_hold_cycles}")
    else:
        wanted = int(reset_hold_cycles)
        for domain in contract.clocks:
            levels = [domain.name in slot.resets for slot in stimulus.slots
                      if domain.name in slot.ticks]
            held = 0
            while held < len(levels) and levels[held]:
                held += 1
            if held != wanted or any(levels[held:]):
                out.append(
                    f"clock domain {domain.name!r} has reset on {held} leading "
                    f"active edges (and {'a later reset edge' if any(levels[held:]) else 'none later'}); "
                    f"the run holds exactly {wanted}")
    if queue_bound is not None:
        for endpoint in contract.inputs():
            depth = stimulus.depth(endpoint.name)
            if depth > int(queue_bound):
                out.append(f"queue {endpoint.name!r} holds {depth} transactions and "
                           f"the domain's bound is {queue_bound}")
    from .core.input_queue import input_completion_environment
    terminal_environment = input_completion_environment(stimulus, contract)
    out.extend(fault.why for fault in terminal_environment.faults)
    return out


def _padded_to(contract, stimulus, horizon, stall_every=None):
    """The same stimulus with idle slots appended until it is ``horizon`` long.

    Idle in the sense the drain is idle: nothing offered, no tick, not a reset.  Every
    ready/valid receiver is receptive, so padding cannot prevent already-produced work
    from transferring or keep a full output buffer from blocking pending inputs.

    For a stimulus on a **recorded** environment the provider's own schedule covers only
    what it recorded, and the slots past its end have to come from somewhere. The run's
    drain policy is that somewhere: the body is the provider's and the tail is the
    run's.
    """
    from .core.framing import idle_tail
    from .core.stimulus import EventSlot, Stimulus

    slots = list(stimulus.slots)
    if len(slots) >= horizon:
        return stimulus
    if stimulus.clock_schedule is not None:
        from .core.clock_schedule import GlobalEventSlot, GlobalSchedule

        schedule = stimulus.clock_schedule
        induced = GlobalSchedule.periodic(schedule.timings, int(horizon))
        recorded = [(slot.time, slot.events) for slot in schedule.slots]
        prefix = [(slot.time, slot.events)
                  for slot in induced.slots[:len(schedule.slots)]]
        if prefix != recorded:
            raise Refused(Refusal(
                "atomic-schedule-padding-prefix-not-induced",
                "the recorded global schedule is not a prefix of the periodic "
                "schedule its own clock declarations induce",
                recorded=recorded, induced=prefix))
        events = list(schedule.slots)
        for index, scheduled in enumerate(
                induced.slots[len(events):], len(events)):
            event = EventSlot(
                controls=_multiclock_controls(contract, (), index, None),
                ticks=scheduled.events)
            events.append(GlobalEventSlot(scheduled.time, event=event))
        complete = GlobalSchedule(schedule.timings, events)
        return Stimulus(
            queues=dict(stimulus.queues),
            slots=[slot.event for slot in events],
            memory_policy=dict(stimulus.memory_policy),
            memory_environment=stimulus.memory_environment,
            initial_state=dict(stimulus.initial_state),
            source=f"{stimulus.source}, to a horizon of {horizon}",
            clock_schedule=complete)
    slots += idle_tail(contract, slots, horizon - len(slots), stall_every)
    return Stimulus(queues=dict(stimulus.queues), slots=slots,
                    memory_policy=dict(stimulus.memory_policy),
                    memory_environment=stimulus.memory_environment,
                    initial_state=dict(stimulus.initial_state),
                    source=f"{stimulus.source}, to a horizon of {horizon}")


def _logical_provider_value(field, value):
    """Decode an RTL bit pattern, preserving an already-logical provider value."""

    # A signed provider may spell one wire pattern as its unsigned numeral
    # (for example 15 on a 4-bit signed port).  Preserve an admitted logical
    # value first; otherwise an exact integer is a physical pattern and must be
    # decoded.  Unsigned integers are unchanged because their logical and
    # physical carriers coincide.
    if field.type.admits(value) or type(value) is not int:
        return value
    from .core.physical_value import PhysicalValueError, unpack_value

    try:
        return unpack_value(field.type, value)
    except PhysicalValueError as exc:
        raise Refused(Refusal(
            "authority-provider-packed-value-invalid",
            "an RTL provider produced a physical pattern outside the task field's "
            "recursive declaration",
            field=field.name, reason=exc.code)) from None


def _reindexed(witness, picks, *, fields=None):
    """The same candidate with every driven row in the contract's field order.

    Both legacy forms carry values positionally, so both need it: `inputs` rows, and the
    values of each `drive` action in `interleave` or `actions`. Everything else is passed
    through untouched, so the boundary sees the record the provider wrote.  When task
    fields are supplied, a non-integer recursive field represented by a physical RTL
    bit pattern is decoded to its logical value here.  Logical Behavior-provider values
    already admitted by the declaration pass through unchanged.
    """
    if fields is not None:
        fields = tuple(fields)
        if len(fields) != len(picks):
            raise ValueError("reindexing needs one task field per selected column")

    def selected(row):
        values = [row[index] for index in picks]
        if fields is None:
            return values
        return [_logical_provider_value(field, value)
                for field, value in zip(fields, values)]

    out = dict(witness)
    if witness.get("inputs"):
        out["inputs"] = [selected(row) for row in witness["inputs"]]
    for form in ("interleave", "actions"):
        if not witness.get(form):
            continue
        out[form] = [
            ([action[0], action[1], selected(action[2])] + list(action[3:]))
            if action and action[0] == "drive" else action
            for action in witness[form]]
    return out


def _unconsumed(contract, witness):
    """What this candidate carries that the path does not consume, or an empty list.

    A witness with memory content describes a run in which the design and the reference
    exchange values through state no transaction carries, and a stimulus built from the
    rest of it runs a different experiment -- then compares the two transaction streams
    as though they were the whole of what happened. A recorded `schedule` is the same
    shape of error the old replay made: it invented its own stalls rather than using the
    one the witness carried.

    ``mem_init`` and ``mem_policy`` are consumed only when the task declares the named
    memory.  Otherwise they describe an environment outside the task and the candidate
    is dropped here.  ``initial_state`` remains a separate device-state policy, and
    ``schedule`` is consumed by :func:`_on_recorded_schedule`.
    """
    if contract.memories:
        return []
    return sorted(k for k in ("mem_init", "mem_policy") if witness.get(k))


def _with_memory_environment(contract, stimulus, witness, memory_horizon):
    """Bind a legacy generator policy to the actual finite schedule it produced.

    Randomness is a construction device only.  The returned ``Stimulus`` carries every
    ready bit, so replay and exact analysis consume the same environment without rerunning
    an RNG or interpreting a probability.
    """

    from .core.memory import MemoryEnvironment, MemoryRun
    from .core.stimulus import Stimulus

    exact_image = witness.get("memory_environment")
    if exact_image is not None:
        if witness.get("mem_init") or witness.get("mem_policy"):
            raise Refused(Refusal(
                "memory-witness-two-authorities",
                "an exact memory environment cannot coexist with its retired "
                "generator-policy fields"))
        try:
            environment = MemoryEnvironment.from_dict(exact_image)
        except (TypeError, ValueError, Refused) as exc:
            raise Refused(Refusal(
                "memory-witness-environment-malformed",
                "a witness memory environment is one canonical finite record",
                error=f"{type(exc).__name__}: {exc}")) from None
        faults = environment.faults_against(
            contract.memories, int(memory_horizon))
        if faults:
            raise Refused(Refusal(
                "memory-witness-environment-outside-task",
                "the recorded memory environment is outside the task interface or "
                "witness schedule",
                first=faults[0], count=len(faults)))
        return Stimulus(
            queues=dict(stimulus.queues), slots=stimulus.slots,
            memory_policy={}, initial_state=dict(stimulus.initial_state),
            source=stimulus.source, clock_schedule=stimulus.clock_schedule,
            memory_environment=environment)

    if not contract.memories:
        return stimulus

    initial = witness.get("mem_init") or {}
    policy = witness.get("mem_policy") or {}
    if type(initial) is not dict or type(policy) is not dict:
        raise Refused(Refusal(
            "memory-witness-shape-invalid",
            "memory initialization and policy are exact mappings keyed by declared "
            "memory name"))
    declared = {memory.name: memory for memory in contract.memories}
    unknown = sorted((set(initial) | set(policy)) - set(declared))
    if unknown:
        raise Refused(Refusal(
            "memory-witness-name-unknown",
            "a witness carries memory state or policy for a memory the task does not "
            "declare",
            memories=unknown))
    runs = []
    for memory in contract.memories:
        cells = initial.get(memory.name, {}) or {}
        row = policy.get(memory.name, {}) or {}
        if type(cells) is not dict or type(row) is not dict:
            raise Refused(Refusal(
                "memory-witness-shape-invalid",
                "each declared memory's initialization and policy are exact mappings",
                memory=memory.name))
        allowed = {"read_latency", "stall_rate", "seed"}
        extra = sorted(set(row) - allowed)
        if extra:
            raise Refused(Refusal(
                "memory-policy-field-unknown",
                "a memory generator policy contains fields this version does not "
                "interpret",
                memory=memory.name, fields=extra))
        parsed_cells = []
        for address, value in cells.items():
            if type(address) is int:
                parsed_address = address
            elif type(address) is str and address \
                    and (address == "0" or address[0] != "0") \
                    and address.isdecimal():
                # JSON object keys are strings.  Accept their one canonical decimal
                # spelling; do not silently coerce floats, booleans or ``"01"`` into
                # another address.
                parsed_address = int(address)
            else:
                raise Refused(Refusal(
                    "memory-initial-address-invalid",
                    "a witness memory address is an exact nonnegative integer or its "
                    "canonical decimal JSON spelling",
                    memory=memory.name, got=repr(address)))
            parsed_cells.append((parsed_address, value))
        runs.append(MemoryRun.from_policy(
            memory, memory_horizon, initial=parsed_cells,
            read_latency=row.get("read_latency", 1),
            stall_rate=row.get("stall_rate", 0.0),
            seed=row.get("seed", 0)))
    environment = MemoryEnvironment(runs)
    return Stimulus(
        queues=dict(stimulus.queues), slots=stimulus.slots,
        # The generator policy has been consumed into exact ready bits. Keeping both
        # would be two authorities over the memory environment.
        memory_policy={},
        initial_state=dict(stimulus.initial_state), source=stimulus.source,
        clock_schedule=stimulus.clock_schedule,
        memory_environment=environment)


def _on_recorded_schedule(contract, body, recorded, source="", drain=0,
                          stall_every=None, reset_hold_cycles=None):
    """A stimulus whose slots are the cycles the provider recorded, plus this run's drain.

    One slot per recorded cycle. `reset` is read from the reset signal against the
    polarity the **contract** declares, not from the raw level: an active-low design's
    released reset is a 1, and reading the bit would invert every slot. A slot offers
    where the input's own `valid` was recorded asserted outside reset; what it offers is
    the queue's business, since the driver's pointer advances on transfer and a stalled
    offer is re-presented unchanged.

    **Who owns what.**

    * The **environment** is the provider's. Overwriting it with this run's stall pattern
      is the "replay invents its own stalls" defect T3-02 names, so `stall_every` is the
      rule for candidates that carry no schedule.
    * **Where the run starts** is the run's: every schedule of an evaluation begins with
      the same reset, or the records are not of one trace domain.
    * The **drain** is the run's: the shared framing rule appends `drain` idle slots, so this
      stimulus ends where the policy says a run ends rather than where the provider
      stopped recording.

    Refusals: `authority-recorded-schedule-empty`;
    `authority-recorded-schedule-incomplete`, where a cycle does not name every control
    the environment owns; `authority-recorded-schedule-control-not-a-bit`, where a raw
    recorded control is outside {0, 1}; `authority-recorded-schedule-reset-not-the-run-s`,
    where the leading reset is not **exactly** the run's hold -- shorter starts the design
    from the simulator's power-up state, longer is a different environment under the same
    domain identity. The caller drops that candidate rather than refusing the set.
    """
    from .core.framing import idle_tail
    from .core.stimulus import EventSlot, Stimulus, leading_reset_slots

    inputs = contract.inputs()
    endpoint = inputs[0] if inputs else None
    domain = contract.clock_domain(
        endpoint.clock if endpoint is not None else contract.clocks[0].name)
    asserted = 0 if domain.reset_active_low else 1
    # What a *cycle* of a recorded schedule must name is every control the environment
    # owns -- the provider drove them all. What a *slot* carries is only the scheduled
    # ones: an input's own `valid` is read here to decide `offers` and then dropped,
    # because `core/driver.Driver` derives it from what is presented and two authorities
    # over one pin is how a slot came to record a value the run did not drive.
    every = set(contract.environment_controls())
    names = set(contract.scheduled_controls())
    offered_by = set(contract.presented_controls())
    hold = domain.reset_hold_cycles if reset_hold_cycles is None \
        else int(reset_hold_cycles)

    cycles = recorded.get("cycles") or ()
    if not cycles:
        raise Refused(Refusal(
            "authority-recorded-schedule-empty",
            "this candidate carries a schedule with no cycles in it, so it describes no "
            "run at all and the rows beside it would be driven under an environment "
            "nobody recorded",
            source=str(source)))

    slots = []
    for index, cycle in enumerate(cycles):
        # **Validated before the projection, over the raw record.** The input's own
        # `valid` is read to decide `offers` and then dropped from the slot, so a check
        # on the slot's controls never sees it -- and `int(...)` is a conversion, not a
        # validation. A recorded `in_valid = 2` came through as a schedule with zero
        # offers and no fault: an illegal environment silently rewritten into a
        # different, legal stimulus, whose fingerprint then bound a run nobody recorded.
        control = {}
        for name, value in dict(cycle.get("control") or {}).items():
            if value not in (0, 1, True, False):
                raise Refused(Refusal(
                    "authority-recorded-schedule-control-not-a-bit",
                    "every control the environment owns is one bit, and this cycle "
                    "records a value that cannot be put on the pin; projecting it away "
                    "would replace the environment the provider ran with a different "
                    "one under the same name",
                    cycle=index, control=str(name), got=repr(value),
                    source=str(source)))
            control[str(name)] = int(value)
        missing = sorted(every - set(control))
        if missing:
            raise Refused(Refusal(
                "authority-recorded-schedule-incomplete",
                "this cycle of the recorded schedule does not name every control the "
                "contract says the environment drives, so running it would have this "
                "build choose the rest -- which is the hole the recorded schedule "
                "exists to close",
                missing=missing, source=str(source)))
        in_reset = control.get(domain.reset) == asserted
        offering = (endpoint is not None and not in_reset
                    and all(control.get(c) == 1 for c in offered_by))
        slots.append(EventSlot(
            offers=[endpoint.name] if offering else (),
            reset=in_reset,
            controls={k: v for k, v in control.items() if k in names}))
    held = leading_reset_slots(slots)
    if held != hold:
        # **Exactly, not at least.** `reset_hold_cycles` is the run's policy for how the
        # front of *every* schedule looks, and it is in the trace-domain identity as that
        # exact number -- so a schedule holding five under a policy of three was
        # accepted, and its record then carried a domain naming a reset it did not run.
        # A lower-bound reading would need the actual prefix in the identity instead;
        # the exact reading keeps one number meaning one thing, and the provider
        # generates exactly this many (`reset_then`), so a conforming candidate is not
        # narrowed by it.
        raise Refused(Refusal(
            "authority-recorded-schedule-reset-not-the-run-s",
            "this recorded schedule does not begin with exactly the reset this run "
            "holds at the front of every schedule -- shorter starts the design from the "
            "simulator's power-up state, longer is a different environment under the "
            "same domain identity -- so its record is not in this run's trace domain. A "
            "provider generating for this run resets for the run's own hold",
            source=str(source), reset_slots=held,
            held_by_this_run=hold))
    # This run's drain, before anything computes a horizon over the set.
    slots += idle_tail(contract, slots, drain, stall_every)
    return Stimulus(queues=dict(body.queues), slots=slots,
                    memory_policy=dict(body.memory_policy),
                    memory_environment=body.memory_environment,
                    initial_state=dict(body.initial_state), source=source)


def provenance_of(telemetry):
    """`schedule_provenance` out of an evaluation's telemetry, as dictionaries.

    `core/frozen.freeze` turns a nested mapping into sorted pairs -- right for a digest,
    and not something a reader can subscript. The shape here is known
    (`{fingerprint: [row, ...]}`), so this reads it rather than guessing at a general
    thaw, and a caller that wants the provenance has one place to get it.
    """
    got = dict(telemetry).get("schedule_provenance") or ()
    pairs = got.items() if hasattr(got, "items") else got
    return {str(f): [dict(row) for row in rows] for f, rows in pairs}


def schedule_provenance_after_generation(portfolio, baseline_provenance):
    """Carry each baseline environment through queue-only generation.

    Appendix A.3 permits a local probe to change only selected queue payload fields.
    Its candidate therefore runs under the seed's exact schedule.  Generation records
    that parent relation separately from the older schedule provenance, so this helper
    closes the relation after the independent searches are merged: every member of the
    final ``Xi_A`` receives all baseline schedule rows reachable through its retained
    generation parents.

    A missing root is refused rather than reported as an unclassified environment.
    Cycles cannot be produced by the FIFO scheduler, but the fixed-point construction
    also makes a malformed cyclic record fail closed instead of recursing forever.
    """
    from .generation import GenerationPortfolio

    if type(portfolio) is not GenerationPortfolio:
        raise TypeError("schedule provenance needs an exact GenerationPortfolio")
    baseline_provenance = dict(baseline_provenance or {})
    members = {stimulus.fingerprint: stimulus for stimulus in portfolio.stimuli}
    baseline = {stimulus.fingerprint for stimulus in portfolio.baseline}
    unknown = sorted(set(baseline_provenance) - baseline)
    if unknown:
        raise Refused(Refusal(
            "generation-schedule-provenance-baseline-unbound",
            "schedule provenance names a stimulus outside the common generation "
            "baseline",
            fingerprints=unknown))

    resolved = {}
    for fingerprint in sorted(baseline):
        rows = baseline_provenance.get(fingerprint)
        if type(rows) not in (tuple, list) or not rows:
            raise Refused(Refusal(
                "generation-schedule-provenance-baseline-missing",
                "every common-baseline stimulus needs the environment record under "
                "which it will be replayed",
                fingerprint=fingerprint))
        plain = []
        for row in rows:
            try:
                item = dict(row)
            except (TypeError, ValueError):
                item = None
            if item is None or type(item.get("schedule")) is not str:
                raise Refused(Refusal(
                    "generation-schedule-provenance-row-malformed",
                    "a schedule-provenance row names its schedule kind",
                    fingerprint=fingerprint))
            if item not in plain:
                plain.append(item)
        resolved[fingerprint] = plain

    parents = {}
    for fingerprint, rows in portfolio.provenance.items():
        if fingerprint not in members:
            raise Refused(Refusal(
                "generation-schedule-provenance-member-unbound",
                "generation provenance names no member of the final stimulus set",
                fingerprint=fingerprint))
        target = members[fingerprint]
        for frozen_row in rows:
            row = dict(frozen_row)
            seed_id = row.get("seed")
            seed = members.get(seed_id)
            if seed is None:
                raise Refused(Refusal(
                    "generation-schedule-provenance-seed-unbound",
                    "a generated stimulus names no retained parent environment",
                    fingerprint=fingerprint, seed=seed_id))
            if target.slots != seed.slots \
                    or target.clock_schedule != seed.clock_schedule:
                raise Refused(Refusal(
                    "generation-schedule-changed-under-local-probe",
                    "a local generation query changed the physical schedule inherited "
                    "from its seed",
                    fingerprint=fingerprint, seed=seed_id))
            parents.setdefault(fingerprint, set()).add(seed_id)

    # Propagate roots to a fixed point so all independent derivations are retained.
    changed = True
    while changed:
        changed = False
        for fingerprint in sorted(parents):
            seed_ids = parents[fingerprint]
            inherited = list(resolved.get(fingerprint, ()))
            for seed_id in sorted(seed_ids):
                for row in resolved.get(seed_id, ()):
                    if row not in inherited:
                        inherited.append(dict(row))
            if inherited != resolved.get(fingerprint, []):
                resolved[fingerprint] = inherited
                changed = True

    missing = sorted(set(members) - set(resolved))
    if missing:
        raise Refused(Refusal(
            "generation-schedule-provenance-unrooted",
            "every generated stimulus must inherit a schedule from the common "
            "baseline",
            fingerprints=missing))
    return {stimulus.fingerprint: resolved[stimulus.fingerprint]
            for stimulus in portfolio.stimuli}


def _hold_of(contract, declared):
    """How many slots this run holds reset: the run's number, or the contract's.

    The explicit runtime value takes precedence. Legacy finite-schedule callers
    that omit it retain the contract's declared reset requirement.

    Read here rather than written into the `TaskContract`: injecting the run's number
    into the contract put an experiment's knob inside the task's semantics, and the
    contract digest is what a task's identity is.
    """
    if declared is not None:
        return int(declared)
    return contract.clocks[0].reset_hold_cycles if contract.clocks else None


def _with_back_pressure(stimulus, contract, stall_every, *, drain=0):
    """Apply the declared injection stalls and make the drain receptive.

    Rebuilt rather than driven differently at run time: a stimulus is immutable and its
    fingerprint has to be over what will actually be driven. `reset_then` leaves the
    receiver's `ready` at zero, which is not a neutral default -- it is an environment
    that never takes anything, and under it a correct design transfers nothing. The
    final ``drain`` slots always drive ready: they introduce no work and exist to let
    already-released work complete.
    """
    from .core.protocol import receptive_controls
    from .core.stimulus import EventSlot, Stimulus

    ready = receptive_controls(contract)
    if not ready:
        return stimulus
    drain = int(drain or 0)
    drain_start = max(0, len(stimulus.slots) - drain)
    slots = []
    for index, slot in enumerate(stimulus.slots):
        controls = dict(slot.controls)
        taking = (1 if index >= drain_start else
                  0 if (stall_every and index % stall_every == 0) else 1)
        for signal in ready:
            controls[signal] = 0 if slot.reset else taking
        slots.append(EventSlot(offers=slot.offers, ticks=slot.ticks, reset=slot.reset,
                               controls=controls))
    return Stimulus(queues=dict(stimulus.queues), slots=slots,
                    memory_policy=dict(stimulus.memory_policy),
                    memory_environment=stimulus.memory_environment,
                    initial_state=dict(stimulus.initial_state),
                    source=f"{stimulus.source}, declining every {stall_every}"
                           if stall_every else f"{stimulus.source}, always receptive")
