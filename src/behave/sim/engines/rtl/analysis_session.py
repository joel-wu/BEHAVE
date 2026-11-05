"""A fixed RTL bounded-analysis context behind the shared isolated transport."""

from __future__ import annotations

import math
import json
import sys
import time
from contextlib import ExitStack

from ...analysis_budget import AnalysisStopped, analysis_scope, check_analysis
from ...symbolic.diagnostics import diagnostic_scope, diagnostic_stage
from ...core.refusal import Refusal, Refused
from ...job_session import serve as serve_session
from ...job_session import stdio
from ...rtl.analysis_job import PreparedAnalysis

VERSION = "rtl-analysis-session/2"


def serve(reader, writer):
    prepared = None

    with ExitStack() as lifetime:
        def dispatch(operation, payload):
            nonlocal prepared
            try:
                if operation == "open":
                    if (type(payload) is not dict or set(payload) != {"context", "deadline"}
                            or type(payload["deadline"]) not in (int, float)
                            or not math.isfinite(payload["deadline"])):
                        raise ValueError("Analysis opens with its context and a finite parent deadline")
                    remaining = payload["deadline"] - time.monotonic()
                    if remaining <= 0:
                        raise Refused(Refusal("analysis-budget-exhausted",
                                              "the parent Analysis deadline expired before construction"))
                    budget = lifetime.enter_context(analysis_scope(remaining))
                    # Do not grant time for transport, preparation, or a later
                    # exchange. The parent's deadline remains the hard bound.
                    budget.deadline = min(budget.deadline, payload["deadline"])
                    prepared = PreparedAnalysis(payload["context"])
                    check_analysis()
                    answer = prepared.binding()
                else:
                    check_analysis()
                    answer = prepared.call(operation, payload)
                check_analysis()
                return answer
            except AnalysisStopped as exc:
                raise Refused(Refusal("analysis-budget-exhausted",
                                      "the parent Analysis deadline expired in the worker",
                                      operation=operation,
                                      notes=list(getattr(exc, "__notes__", ())))) from None

        def emit(row):
            print('ANALYSIS_STAGE ' + json.dumps(row, separators=(',', ':')),
                  file=sys.stderr, flush=True)

        def measured_dispatch(operation, payload):
            with diagnostic_stage(operation):
                return dispatch(operation, payload)

        with diagnostic_scope(emit):
            serve_session(reader, writer, version=VERSION, dispatch=measured_dispatch)


if __name__ == "__main__":
    stdio(serve)
