"""Exact local projection of monitor facts fixed by the selected schedule.

Every normal active sampling step transfers one value per plain endpoint. For
ordered matching, payload mutation cannot change those transfer counts, offer
times or completion under a fixed schedule/reset prefix. Reuse the native trace's
shared monitor evaluation; the datapath has no influence on these predicates.

This establishes only a fixed-schedule local answer. It neither certifies that
all RTL computations terminate nor exports a whole-domain analysis proof. Source
goals and payload-dependent monitors keep their own symbolic query paths.
Reset scenarios use the same monitor transition: activation is schedule-only,
and a violation is false when no restoration scenario occurs in the seed.
An activated restoration obligation still needs its full state query.
"""

from ...core.monitor import Monitor, monitor_for
from ...core.refusal import Refusal, Refused
from ...core.reset_monitor import ResetRestorationProvider
from ...core.stimulus import AuditedTrace, Completion
from .. import probing


class ScheduleMonitorQuery:
    @staticmethod
    def candidate_goals(contract):
        """Possible projections; reset violations depend on the actual seed."""
        return (monitor_for(contract).payload_independent_goals()
                | set(ResetRestorationProvider(contract).goals()))

    def __init__(self, contract, seed, trace):
        monitor = monitor_for(contract)
        candidates = self.candidate_goals(contract)
        if not candidates:
            raise Refused(Refusal("rtl-schedule-monitor-payload-dependent",
                "this protocol has no schedule-only monitor projection"))
        if (type(trace) is not AuditedTrace or trace.stimulus != seed
                or trace.completion is not Completion.NORMAL
                or trace.observed_count != len(seed.slots)):
            raise Refused(Refusal("rtl-schedule-monitor-incomplete-boundary",
                "a local schedule projection needs the completed native seed"))
        report = monitor.check(seed, trace)
        goals = set(monitor.payload_independent_goals())
        hit_names = {hit.goal for hit in report.hits}
        resets = ResetRestorationProvider(contract)
        for domain in resets.domains:
            scenario = resets.scenario_goal(domain.name)
            goals.add(scenario)
            if scenario not in hit_names:
                # violation = scenario & comparable & !equal in the shared
                # reset transition. No data/state equality is inferred here.
                goals.add(resets.violation_goal(domain.name))
        self.goals = frozenset(goals)
        # An unanswered transaction beyond the selected finite schedule is not
        # a query about one of these local monitor predicates. It remains in the
        # concrete report, but cannot turn a known local count into UNKNOWN.
        uncertain = [item for item in report.unresolved
                     if item.goal in self.goals and item.goal != Monitor.UNANSWERED]
        if report.assumption_violations or uncertain:
            raise Refused(Refusal("rtl-schedule-monitor-unresolved",
                "the native seed did not establish the projected schedule facts"))
        self.seed = seed
        self.hits = tuple((hit.goal, hit.slot) for hit in report.hits if hit.goal in self.goals)

    def probe(self, request, segment):
        if not set(request.targets) <= self.goals:
            raise Refused(Refusal("rtl-schedule-monitor-query-owner",
                "the schedule projection cannot answer a source or active state/value goal"))
        reached = next((goal for goal, slot in self.hits
                        if goal in request.targets and segment.start <= slot < segment.stop), None)
        detail = ("exact schedule-only monitor projection; fixed queue lengths, "
                  "sample/reset schedule and reset activation; not a global unreachability result")
        if reached is None:
            return probing.ProbeResult(probing.Outcome.LOCAL_UNSAT,
                probing.Scope.RTL_LOCAL_HIT, detail=detail)
        # An already-reached target has the actual native seed as its witness.
        # The scheduler normally removes these targets before issuing probes.
        return probing.ProbeResult(probing.Outcome.SAT, probing.Scope.RTL_LOCAL_HIT,
            goal=reached, candidate=self.seed, detail=detail)
