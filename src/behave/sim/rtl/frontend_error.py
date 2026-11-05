"""Source rejection is distinct from failure of a trusted frontend.

The reader may elaborate task or candidate source. It records the rejection;
the public candidate boundary assigns its scoring consequence.
"""

from ..core.failure import FailureDisposition, RunFailure
from ..core.refusal import Refusal
from ..core.schema import InterfaceError


class SourceRejected(InterfaceError):
    """A source-bound check rejected the selected entry or its language input."""

    def __init__(self, reason):
        if type(reason) is not Refusal:
            raise TypeError("a source rejection needs a typed reason")
        self.reason = reason
        super().__init__(str(reason))


def tool_failure(error):
    """Frontend budgets withhold a result; they do not penalize the candidate."""
    from behave.isolation import IsolationStatus

    from .tool_protocol import ToolIsolationError

    if type(error) is not ToolIsolationError:
        raise TypeError("a frontend tool failure needs its exact execution evidence")
    reason = Refusal(error.code, error.why,
                     tool_executions=[item.as_dict() for item in error.executions])
    if error.code in {"rtl-frontend-resource-limit", "rtl-catalogue-resource-limit",
                      "rtl-source-bundle-resource-limit"} \
            or error.execution.status in {
                IsolationStatus.TIMED_OUT, IsolationStatus.CPU_LIMIT,
                IsolationStatus.MEMORY_LIMIT, IsolationStatus.PROCESS_LIMIT,
                IsolationStatus.OUTPUT_LIMIT, IsolationStatus.PROTOCOL_LIMIT,
                IsolationStatus.FILE_LIMIT}:
        return RunFailure.resource("rtl-frontend", reason,
            policy="frontend-preparation-budget/1",
            disposition=FailureDisposition.NO_RESULT)
    return RunFailure.infrastructure("rtl-frontend", reason)
