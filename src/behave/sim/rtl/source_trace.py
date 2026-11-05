"""Read complete source evidence under one frontend-owned RTL state/catalogue binding.

This is a record check, not an execution oracle. Protocol reconstruction and comparison
against the symbolic relation remain the exact adapter's separate responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.catalogue import CatalogueManifest, OccurrenceKey
from ..core.hit_vector import HitVector
from ..core.record_value import thaw_record_value
from ..core.refusal import Refusal, Refused
from ..core.stimulus import AuditedTrace, Completion, Stimulus
from .initial_request import InitialStateCoordinates


@dataclass(frozen=True)
class SourceTrace:
    """Validated state and hit evidence; None means no occurrence observation."""

    states: tuple[dict[str, int], ...]
    hits: tuple[frozenset[str], ...]
    occurrences: tuple[frozenset[OccurrenceKey], ...] | None
    phases: tuple = ()
    terminal_error: str = ""
    pending_sample: dict | None = None


def sampled_state_image(observations, widths, *, prefix):
    """Read registers after control settling, before the scheduled clock batch.

    These are source observations, not a second copy of the preceding boundary
    state: an asynchronous control event may already have changed a register.
    """

    rows = []
    for slot, observation in enumerate(observations):
        values = dict(observation)
        unreadable = [name for name, width in widths.items()
                      if type(values.get(name)) is not int
                      or not 0 <= values[name] < (1 << width)]
        if unreadable:
            raise Refused(Refusal(
                f"{prefix}-source-sampled-state-unreadable",
                "source replay must observe every in-width register at the "
                "control-settled, pre-clock boundary",
                slot=slot, first=unreadable[0], count=len(unreadable)))
        rows.append({name: values[name] for name in widths})
    return tuple(rows)


def read_source_trace(
        stimulus: Stimulus, trace: AuditedTrace, initial: InitialStateCoordinates,
        catalogue: CatalogueManifest, *, prefix: str, observation=None,
) -> SourceTrace:
    """Retain all state, public-hit and native-occurrence evidence in a bound trace."""

    return _read_source_trace(stimulus, trace, initial, catalogue,
                              prefix=prefix, observation=observation, whole_history=False)


def read_source_history(stimulus, trace, initial, catalogue, *, observation=None):
    """Read startup and every control/clock/return interval, without merging them.

    Unlike the legacy clocked-circuit reader, the source-history consumer must
    encode and audit return-edge activity. This reader grants no such capability;
    it only preserves the evidence needed to check that obligation.
    """
    return _read_source_trace(stimulus, trace, initial, catalogue,
        prefix="rtl-source-history", observation=observation, whole_history=True)


def read_failed_source_history(failure, stimulus, initial, catalogue, *, observation):
    """Read a source-attributed stop, never promote an arbitrary partial trace.

    The collector authenticates the actual phase/occurrence prefix. Successful
    initialization and sampled pins are separate receipts. No post-failure state
    is invented here; the exact product must still prove terminal membership.
    """
    from ..core.catalogue import SAFETY
    from ..core.failure import FailureOrigin, RunFailure
    from ..core.hit_vector import HIT_VECTOR_ENCODING, HitVectorCodec
    from ..core.stimulus import SlotRecord, trace_domain
    from .native_observation import PREFIX_VERSION
    from .observation_binding import ObservationBinding
    from .source_goals import is_source_goal

    if (type(failure) is not RunFailure or failure.origin is not FailureOrigin.CANDIDATE
            or failure.phase != 'rtl-source-execution' or failure.kind != 'rtl-source-failure'):
        raise Refused(Refusal('rtl-source-terminal-evidence-required',
            'only a source-attributed native failure can supply a terminal witness'))
    if type(observation) is not ObservationBinding or not observation.native:
        raise Refused(Refusal('rtl-source-terminal-binding-required',
            'terminal evidence requires its admitted native observation binding'))
    detail = thaw_record_value(failure.reason.detail)
    try:
        image, replay = detail['source_evidence'], detail['replay_prefix']
        completed = image['completed_steps']
        if (image['version'] != PREFIX_VERSION or replay['interrupted'] is not True
                or replay['stimulus'] != stimulus.fingerprint
                or type(completed) is not int or completed < 0
                or len(replay['records']) != completed
                or replay['hit_encoding'] != HIT_VECTOR_ENCODING
                or replay['hit_catalogue'] != sorted(catalogue)
                or sorted(tuple(row) for row in replay['state_widths']) != sorted(initial.widths.items())
                or replay['execution_policy'] != image['execution']
                or replay.get('artifact', initial.artifact) != initial.artifact):
            raise ValueError('sampled and native prefixes disagree')
        trace = AuditedTrace(stimulus, initial_state=replay.get('initial_state'),
            records=[SlotRecord.from_dict(row, hit_catalogue=HitVectorCodec(sorted(catalogue)))
                     for row in replay['records']], completion=Completion.INCOMPLETE,
            domain=trace_domain(observation.contract), artifact=initial.artifact,
            source_evidence=image, initialization=replay['initialization'])
        native = _read_source_trace(stimulus, trace, initial, catalogue,
            prefix='rtl-source-history', observation=observation, whole_history=True, terminal=True)
        count = len(native.phases)
        # A stop between completed intervals supplies no modeled failure point.
        if count != 1 and not 1 <= count - (1 + 3 * completed) <= 3:
            raise ValueError('terminal evidence must end inside an actual source phase')
        failures = sorted(goal for goal in native.phases[-1][1].goals
            if is_source_goal(catalogue[goal]) and catalogue[goal].family == 'artifact-failure'
            and catalogue[goal].cls == SAFETY)
        if not failures:
            raise ValueError('the interrupted phase has no native failure occurrence')
        pending = replay['pending_sample']
        sampled = count > 1 and native.phases[-1][0] in {'clock', 'advance'}
        if sampled != (pending is not None) or (sampled and type(pending) is not dict):
            raise ValueError('sample receipt disagrees with the interrupted phase')
        if sampled:
            sampled_state_image((pending.items(),), initial.widths, prefix='rtl-source-history')
    except (KeyError, TypeError, ValueError) as exc:
        raise Refused(Refusal('rtl-source-terminal-evidence-invalid', str(exc))) from exc
    return trace, SourceTrace(native.states, native.hits, native.occurrences, native.phases,
                              'rtl-source-failure:' + ','.join(failures), pending)


def _read_source_trace(stimulus, trace, initial, catalogue, *, prefix, observation, whole_history,
                       terminal=False):

    if type(stimulus) is not Stimulus or type(trace) is not AuditedTrace:
        raise TypeError("source execution needs an exact Stimulus/AuditedTrace")
    if type(initial) is not InitialStateCoordinates \
            or type(catalogue) is not CatalogueManifest:
        raise TypeError("source evidence binds exact state coordinates and catalogue")

    def refused(kind, why, **detail):
        return Refused(Refusal(f"{prefix}-source-{kind}", why, **detail))

    if trace.stimulus != stimulus:
        raise refused("stimulus-mismatch", "the source trace records another stimulus")
    if trace.artifact != initial.artifact:
        raise refused("artifact-mismatch", "the source trace names another RTL artifact")
    if observation is not None:
        from .observation_binding import ObservationBinding

        if type(observation) is not ObservationBinding or (
                observation.artifact != initial.artifact or observation.catalogue != catalogue.digest()
                or trace.domain.split(":", 1)[0] != observation.contract):
            raise refused("observation-mismatch", "source audit names another observer binding")
        if observation.native != (trace.source_evidence is not None):
            raise refused("observation-missing", "source replay did not use the admitted observer")
    if catalogue and trace.source_evidence is None:
        raise refused("native-evidence-required",
                      "structural goal bits without native occurrences are not source replay evidence")
    if trace.completion is not (Completion.INCOMPLETE if terminal else Completion.NORMAL):
        raise refused(
            "run-incomplete", "exact RTL replay requires a normally completed trace",
            completion=trace.completion.value)
    if not terminal and len(trace.records) != len(stimulus.slots):
        raise refused(
            "horizon-mismatch", "the source trace must record every scheduled slot",
            expected=len(stimulus.slots), got=len(trace.records))

    widths = initial.widths
    names = set(widths)

    def state_of(image, where):
        values = dict(image)
        unreadable = sorted(
            name for name, value in values.items()
            if type(value) is not int
            or name in widths and not 0 <= value < (1 << widths[name]))
        if set(values) != names or unreadable:
            raise refused(
                "state-unreadable", "source state must contain every in-width register",
                where=where, missing=sorted(names - set(values))[:8],
                extra=sorted(set(values) - names)[:8], unreadable=unreadable[:8])
        return values

    startup_failed = terminal and len(trace.source_evidence['intervals']) == 1
    states = [] if startup_failed else [state_of(trace.initial_state, "initial")]
    goals = frozenset(catalogue)
    ordered_goals = tuple(sorted(goals))
    hits = []
    for slot, record in enumerate(trace.records):
        states.append(state_of(record.state, f"slot {slot}"))
        vector = record.hits
        # A compact vector's false positions still belong to its catalogue.
        # Legacy sparse pairs instead use the supplied catalogue as their universe.
        if type(vector) is HitVector and vector.catalogue != ordered_goals:
            raise refused(
                "goal-shape", "the source hit vector names another structural catalogue",
                slot=slot)
        values = dict(vector)
        extra = sorted(set(values) - goals)
        if extra:
            raise refused(
                "goal-shape", "the source hit vector names undeclared structural goals",
                slot=slot, extra=extra[:8])
        unknown = sorted(goal for goal, reached in values.items()
                         if type(reached) is not bool)
        if unknown:
            raise refused(
                "goal-unreadable", "unknown is not a missed structural occurrence",
                slot=slot, first=unknown[0], count=len(unknown))
        hits.append(frozenset(goal for goal, reached in values.items() if reached))
    sampled_state_image(
        (record.outputs for record in trace.records), widths, prefix=prefix)
    occurrences, phases = None, ()
    if trace.source_evidence is not None:
        from .native_observation import _read_intervals, _step_hits

        try:
            image = thaw_record_value(trace.source_evidence)
            plan = None
            if observation is not None:
                from .native_events import NativeEventPlan

                plan = (NativeEventPlan.from_dict(image["plan"])
                        if image["version"] == "rtl-native-observation/1" else
                        NativeEventPlan.from_evidence(image["plan"]))
                if plan.identity != observation.native_plan:
                    raise ValueError("source replay used another admitted native collector")
            intervals = _read_intervals(image, stimulus, catalogue,
                                       expected_plan=plan, prefix=terminal)
            if whole_history:
                phases = tuple(intervals)
                intervals = phases
            native = _step_hits(intervals)
            if terminal:
                native = native[:len(trace.records)]
        except (TypeError, ValueError, KeyError, Refused) as exc:
            raise refused("native-evidence-invalid",
                          "the native occurrence record is incomplete or unbound",
                          detail=str(exc)) from exc
        if tuple(entry.goals for entry in native) != tuple(hits):
            raise refused("native-hit-mismatch",
                          "public source hits disagree with their native occurrences")
        if not whole_history and any(row["phase"] == "advance" and row["callbacks"]
                                     for row in image["intervals"]):
            raise refused("clock-return-relation-unsupported",
                          "the exact source relation does not yet encode clock-return activity; "
                          "its concrete occurrences remain valid baseline evidence")
        occurrences = tuple(entry.occurrences for entry in native)
    if whole_history and not phases:
        raise refused("native-evidence-required", "whole-history audit requires native phase evidence")
    return SourceTrace(tuple(states), tuple(hits), occurrences, phases)
