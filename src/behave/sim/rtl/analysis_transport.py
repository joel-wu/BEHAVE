"""Parent-side lifetime and identity check for one isolated analysis session."""

from __future__ import annotations

from ..analysis_budget import active_analysis
from ..core.stimulus import canonical_json
from ..core.resources import available_cpus
from ..job_isolation import IsolatedJobFailure
from ..job_session import JobSession
from .analysis_job import decode_analysis_reply


class RTLAnalysisSession(JobSession):
    def __init__(self, context, binding=None, *, timeout_s):
        budget = active_analysis()
        if budget is None:
            raise ValueError("an RTL analysis session requires its owning analysis budget")
        self._budget = budget
        wall_s = budget.remaining()
        super().__init__(
            "rtl_analysis_session", context,
            module="behave.sim.engines.rtl.analysis_session",
            version="rtl-analysis-session/2", timeout_s=timeout_s,
            wall_s=wall_s, exchanges=None, cpus=available_cpus())
        try:
            value = decode_analysis_reply(self.opened, "bind")
            if binding is not None and canonical_json(value) != binding:
                raise IsolatedJobFailure("no-answer", "analysis session rebuilt a different binding")
        except BaseException:
            self.close()
            raise

    def _exchange(self, operation, payload, timeout_s):
        if operation == "open":
            # Add control metadata after the ordinary source-path freezing.
            # Parent and isolated child share this host's monotonic clock.
            payload = {"context": payload, "deadline": self._budget.deadline}
        return super()._exchange(operation, payload, timeout_s)

    def call(self, operation, payload):
        # Preparation's envelope is not a cutoff for later audits or decoding.
        # Retain the original owner; another call cannot restart its lifetime.
        return self._exchange(operation, payload, self._budget.remaining())
