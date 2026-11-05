"""Preserve native shutdown observations and source-attributed failure."""

from ..core.catalogue import SAFETY, catalogue_subset
from ..core.failure import ObservedGoals, RunFailure
from ..core.refusal import Refusal, Refused
from ..strict_json import strict_json_loads
from .native_observation import read_native_prefix
from .source_goals import is_source_goal


def interrupted_replay(payload, stimulus, catalogue, *, expected_plan, execution,
                       replay_payload=None, refusal_payload=None):
    """The execution service supplies bytes; the prepared observer owns identity.

    A prefix proves which callbacks ran, not why the simulator stopped. A bound
    source failure proves a candidate violation independently of the exit cause;
    an exit status or log message alone does not. Neither produces an IR successor.
    """
    try:
        image = strict_json_loads(payload, what="the interrupted native observation")
        prefix = read_native_prefix(image, stimulus, catalogue, expected_plan=expected_plan)
    except (TypeError, ValueError, Refused) as exc:
        return RunFailure.verifier("rtl-trace-collection", Refusal(
            "rtl-native-prefix-invalid", "the interrupted observer supplied invalid evidence",
            detail=str(exc), tool_execution=execution))
    observed = frozenset().union(prefix.startup.goals, prefix.pending.goals,
                                 *(interval.goals for interval in prefix.completed))
    failures = sorted(goal for goal in observed if is_source_goal(catalogue[goal])
                      and catalogue[goal].family == "artifact-failure" and catalogue[goal].cls == SAFETY)
    # Startup activity is not step coverage. A source failure is
    # still a violation wherever execution encounters it. Keep the unfinished
    # step's positive hits without inventing its successor.
    step_hits = frozenset().union(*(interval.goals for interval in prefix.completed))
    if prefix.phase in {"control", "clock", "advance"}:
        step_hits |= prefix.pending.goals
    reached = ObservedGoals(
        stimulus.fingerprint, catalogue_subset(catalogue, step_hits | set(failures)))
    detail = {}
    if refusal_payload is not None:
        try:
            cause = strict_json_loads(refusal_payload, what='the interrupted driver refusal')
            if (type(cause) is not dict or set(cause) not in
                    ({'kind', 'why'}, {'kind', 'why', 'detail'})
                    or any(type(cause[name]) is not str or not cause[name] for name in ('kind', 'why'))
                    or type(cause.get('detail', {})) is not dict):
                raise ValueError('driver refusal has no canonical shape')
            detail['driver_refusal'] = Refusal(cause['kind'], cause['why'],
                                               **cause.get('detail', {})).as_dict()
        except (TypeError, ValueError, Refused) as exc:
            detail['driver_refusal_invalid'] = str(exc)
    if replay_payload is not None:
        from .trace_document import read_trace_document

        try:
            traces, startup_failure = read_trace_document(replay_payload, max_traces=1)
            if startup_failure is not None or len(traces) != 1:
                raise ValueError('interrupted replay needs one sampled prefix')
            replay = traces[0]
            if (replay.get('interrupted') is not True
                    or replay.get('stimulus') != stimulus.fingerprint
                    or type(replay.get('records')) is not list
                    or len(replay['records']) != len(prefix.completed)):
                raise ValueError('sampled prefix disagrees with the native completion boundary')
            detail['replay_prefix'] = replay
        except (TypeError, ValueError, Refused) as exc:
            # Missing/malformed optional state evidence cannot erase a source
            # failure actually observed by the prepared collector. Nor can it
            # be used as a complete Analysis witness.
            detail['replay_prefix_refusal'] = Refusal('rtl-replay-prefix-invalid', str(exc)).as_dict()
    if failures:
        return RunFailure.candidate("rtl-source-execution", Refusal(
            "rtl-source-failure", "native execution reached a designated source failure",
            goals=failures, completed_steps=len(prefix.completed), phase=prefix.phase,
            source_evidence=image, tool_execution=execution, **detail), observed=reached)
    return RunFailure.verifier("rtl-trace-collection", Refusal(
        "rtl-native-run-interrupted",
        "native observation stopped before a complete trace; its actual event prefix is retained",
        completed_steps=len(prefix.completed), phase=prefix.phase,
        source_evidence=image, tool_execution=execution, **detail), observed=reached)
