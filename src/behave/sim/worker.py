"""Named JSON jobs in isolated Python processes.

The common namespace supervisor owns each process tree and its resource limits.
Production has no direct-process fallback; that launcher is a scoped test seam.
A job's returned observation is untrusted: generation may propose stimuli, but
only canonical replay can turn them into score evidence."""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager

__all__ = ["JOBS", "WorkerFailed", "run_job"]


_TEST_JOB_RUNNER = None


@contextmanager
def _testing_job_runner(runner):
    """Install the direct named-job launcher for the surrounding test scope.

    Pytest may execute a yielding fixture and its test under distinct context-variable
    contexts and threads.  This private process-local seam is therefore scoped by the
    context manager itself.  Production never enters it, and child workers start a fresh
    interpreter whose value is ``None``.
    """
    global _TEST_JOB_RUNNER
    previous = _TEST_JOB_RUNNER
    _TEST_JOB_RUNNER = runner
    try:
        yield
    finally:
        _TEST_JOB_RUNNER = previous


class WorkerFailed(Exception):
    """The job did not produce an answer. Carries which way it failed."""

    def __init__(self, how, detail="", *, code=""):
        #: `"timeout"`, `"crashed"`, `"no-answer"` or `"refused"`.
        self.how = how
        self.detail = str(detail)
        #: Stable refusal kind supplied by the isolation boundary, when one exists.
        self.code = str(code)
        super().__init__(f"{how}: {self.detail}" if detail else how)


# ── the parent's side ────────────────────────────────────────────────


def run_job(name, payload, timeout_s, label=None, verbose=False, default=None):
    """Run job ``name`` with ``payload`` in a fresh interpreter. Its answer, or ``default``.

    ``default`` is returned for every way of not getting an answer -- a timeout, a child
    that died, a job that refused -- because every caller of this treats "no answer" the
    same way and the alternative is five copies of the same `except`. A caller that needs
    to know *which* way calls `run_job_strict`.
    """
    try:
        return run_job_strict(name, payload, timeout_s, label=label, verbose=verbose)
    except WorkerFailed as exc:
        if verbose:
            print(f"[worker] {label or name} {exc.how}; continuing without its result",
                  flush=True)
        return default


def run_job_strict(name, payload, timeout_s, label=None, verbose=False):
    """As `run_job`, raising `WorkerFailed` instead of returning a default."""
    from .execution_control import check_execution, remaining_timeout

    timeout_s = remaining_timeout(timeout_s)
    if name not in JOBS:
        raise WorkerFailed("refused", f"no job named {name!r}")
    if type(payload) is not dict:
        raise WorkerFailed("refused", "a job payload must be a plain dictionary")
    try:
        # Validate before either launcher.  ``allow_nan=False`` keeps the wire language
        # finite JSON rather than Python's non-standard NaN/Infinity extension.
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise WorkerFailed(
            "refused", f"this job's payload is not JSON: {exc}") from None

    direct = _TEST_JOB_RUNNER
    if direct is not None:
        try:
            return direct(name, payload, timeout_s, label=label, verbose=verbose)
        finally:
            check_execution()

    from .job_isolation import IsolatedJobFailure, run_isolated_job

    try:
        return run_isolated_job(name, payload, timeout_s=timeout_s)
    except IsolatedJobFailure as exc:
        check_execution()
        failure = WorkerFailed(exc.how, exc.detail, code=exc.code)
        for note in getattr(exc, "__notes__", ()):
            failure.add_note(note)
        raise failure from None
    finally:
        check_execution()


# ── the jobs, and the child's side ───────────────────────────────────
#
# Each takes one JSON dictionary and returns one JSON value. Nothing here closes over
# anything: what a job needs is in its payload, which is what makes it startable from a
# command line and readable in a log.


def _job_rtl_generation_layout(payload):
    from .rtl.exact_job_layout import layout_job

    return layout_job(payload)


def _job_behavior_generation_layout(payload):
    from .engines.behavior.generation_job import layout_job

    return layout_job(payload)


def _job_behavior_generation_select(payload):
    from .engines.behavior.generation_job import select_job

    return select_job(payload)


def _job_behavior_generation_probe(payload):
    from .engines.behavior.generation_job import probe_job

    return probe_job(payload)


def _job_rtl_exact_analysis(payload):
    from .rtl.analysis_job import run_analysis_job

    return run_analysis_job(payload)


def _job_behavior_exact_analysis(payload):
    from .behavior.analysis_job import run_analysis_job

    return run_analysis_job(payload)


def _job_behavior_source_facts(payload):
    if type(payload) is not dict or set(payload) != {
            "source", "file", "class", "source_rules", "clock_domains"}:
        raise ValueError(
            "behavior_source_facts needs source, file, class, source_rules and clock_domains")
    from .behavior.isolated import _source_protocol_image
    from .core.source_rules import SourceGoalRules

    return _source_protocol_image(
        payload["source"], payload["file"], payload["class"],
        SourceGoalRules.from_dict(payload["source_rules"]), payload["clock_domains"])


def _job_behavior_realization(payload):
    from .behavior.realization import realization_job

    return realization_job(payload)


def _job_classify_seeds(payload):
    from .behavior.instrumented import classify_pool

    return sorted(classify_pool(payload["behavior_path"], payload["seeds"]).covered)


def _job_echo(payload):
    """The channel's own self-test: hand back `answer`, after `sleep_s`, or raise.

    A job registry with no job that can be started cheaply is a channel nothing can test
    without a solver and a design, and the three properties worth testing here are the
    channel's: an answer comes back, an overrun is killed inside its budget, and a child
    that raises is reported rather than propagated. It does nothing a caller would want
    in a run, which is why it is safe to leave registered.
    """
    if payload.get("raise"):
        raise RuntimeError(str(payload["raise"]))
    if payload.get("sleep_s"):
        time.sleep(float(payload["sleep_s"]))
    return payload.get("answer")


#: Every job this build will run in a worker, by name. A caller names one of these; there
#: is no way to send a callable, which is the point.
JOBS = {
    "echo": _job_echo,
    "rtl_generation_layout": _job_rtl_generation_layout,
    "behavior_generation_layout": _job_behavior_generation_layout,
    "behavior_generation_select": _job_behavior_generation_select,
    "behavior_generation_probe": _job_behavior_generation_probe,
    "rtl_exact_analysis": _job_rtl_exact_analysis,
    "behavior_exact_analysis": _job_behavior_exact_analysis,
    "behavior_source_facts": _job_behavior_source_facts,
    "behavior_realization": _job_behavior_realization,
    "classify_seeds": _job_classify_seeds,
}


def _answer_for(job):
    if type(job) is not dict or set(job) - {"job", "payload"}:
        raise ValueError("a job document has unknown or non-object fields")
    name = job["job"]
    payload = job.get("payload") or {}
    if type(name) is not str or name not in JOBS:
        raise KeyError(f"no job named {name!r}")
    if type(payload) is not dict:
        raise TypeError("a job payload must be a plain object")
    # Only this dispatcher may assert that a call is already below the named judge
    # boundary.  Source-frontends use the scope to avoid recursively creating another
    # namespace; an environment variable cannot safely carry that authority because a
    # caller can set it in the verifier before the run starts.
    from .job_context import _named_job_scope

    with _named_job_scope():
        result = JOBS[name](payload)
    return {"ok": True, "result": result}


def _execute_document(job):
    try:
        answer = _answer_for(job)
    except BaseException as exc:                                     # noqa: BLE001
        # Even SystemExit becomes a failure response, not an unexplained EOF.
        # The traceback goes to stderr, which the parent quotes on a crash.
        import traceback

        traceback.print_exc()
        answer = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        text = json.dumps(answer, allow_nan=False)
    except (TypeError, ValueError) as exc:
        answer = {"ok": False,
                  "error": f"this job's answer is not JSON-representable: {exc}"}
        text = json.dumps(answer, allow_nan=False)
    return answer, text


def _stdio_main():
    """One stdin document to one stdout document; the launcher owns quotas."""
    payload = sys.stdin.buffer.read()
    try:
        job = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError) as exc:
        answer = {"ok": False,
                  "error": f"the job document is not JSON: {exc}"}
        text = json.dumps(answer)
    else:
        # The decoded job owns every value needed by the handler. Large
        # replay documents must not retain their wire copy while building
        # the model as well.
        del payload
        # Candidate prints are not protocol data. Buffered write sends the whole
        # response, including responses larger than a pipe's write capacity.
        with os.fdopen(os.dup(1), "wb") as protocol:
            with open(os.devnull, "wb") as sink:
                os.dup2(sink.fileno(), 1)
            answer, text = _execute_document(job)
            protocol.write(text.encode("utf-8"))
        # A valid ok:false response is a job failure, not a broken transport.
        return 0
    sys.stdout.write(text)
    sys.stdout.flush()
    return 0


def main(argv):
    if argv != ["--stdio"]:
        raise SystemExit("usage: python -m behave.sim.worker --stdio")
    return _stdio_main()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
