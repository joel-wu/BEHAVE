"""Search-owned isolated source session.

No baseline execution lives here. A session receives only the already-recorded
state/port/acceptance and memory deltas needed by its boundary adapter, once per seed.
The native hit evidence stays with the common execution owner.
"""

from ..core.record_value import thaw_record_value
from ..job_session import JobSession
from ..execution_control import remaining_timeout

VERSION = "rtl-source-generation-session/41"


class SourceGenerationSession(JobSession):
    """One worker owns lowering, range analysis and all source-local queries."""

    def __init__(self, context, *, prepare_timeout_s, query_timeout_s, probe_budget, timings):
        self._prepare_timeout = prepare_timeout_s
        self._prepared = False
        self._query_timeout = query_timeout_s
        self._binding = None
        super().__init__("rtl_source_generation_session", context,
            module="behave.sim.engines.rtl.source_session", version=VERSION,
            timeout_s=prepare_timeout_s,
            wall_s=(remaining_timeout(float("inf")) if probe_budget is None else
                    prepare_timeout_s * (probe_budget + 2) + query_timeout_s * probe_budget),
            exchanges=None if probe_budget is None else 2 * probe_budget + 2,
            timings=timings)

    def prepare(self):
        """Build once only when a nonliteral query needs the transition.

        This exchange retains preparation's resource owner and the original
        session wall deadline. It neither restarts the shared search budget
        nor consumes the solver-only deadline of the first real Probe.
        """
        if not self._prepared:
            self._exchange("prepare", {}, self._prepare_timeout)
            self._prepared = True

    def bind(self, seed, trace):
        """Prepare one native prefix under the existing preparation allowance.

        Like the RTL product session, binding is not a solver query. The shared
        session/search deadline still caps the exchange; no budget is restarted.
        """
        if self._binding is not None and self._binding[0] is seed and self._binding[1] is trace:
            return
        self._binding = None
        # Do not serialize source-evidence ledgers/hit vectors a second time.
        # Query construction uses observations; it cannot issue replay evidence.
        self._exchange("bind", {"seed": seed.as_dict(), "initial_state": trace.initial_state,
            "query_boundaries": thaw_record_value(trace.query_boundaries),
            "records": [{"outputs": row.outputs, "state": row.state, "accepted": row.accepted,
                         "environment": thaw_record_value(row.environment)}
                        for row in trace.records]}, self._prepare_timeout)
        self._binding = seed, trace

    def probe(self, request, timeout_ms, *, segment=None):
        return self._exchange("probe", {"request": request.as_dict(), "timeout_ms": timeout_ms,
                                       "segment": None if segment is None else segment.as_dict()},
                              self._query_timeout)
