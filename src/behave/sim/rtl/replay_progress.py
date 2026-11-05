"""Small live diagnostics independent of complete replay evidence and scoring."""

import json
from time import monotonic

from behave.core import runtime_defaults as defaults


class ReplayProgress:
    def __init__(self, directory, stimulus, expected_outputs):
        self.path = directory / "replay_progress.json"
        self.stimulus = stimulus.fingerprint
        self.queued = {name: len(rows) for name, rows in stimulus.queues}
        self.expected = None if expected_outputs is None else dict(expected_outputs)
        self.started = monotonic()
        self.next_at = self.started

    def publish(self, driver, *, stage="executing", force=False):
        now = monotonic()
        if not force and now < self.next_at:
            return
        counts = driver.progress_counts()
        image = {"version": "rtl-replay-progress/1", "stimulus": self.stimulus,
                 "stage": stage, "elapsed_seconds": now - self.started,
                 **counts, "queued": self.queued, "expected_outputs": self.expected}
        # This receipt does NOT attest comparison, protocol/memory audit, or a
        # fully checked input. Publication remains useful before a long trace
        # can be sealed, including a subsequent timeout or process kill.
        body = json.dumps(image, separators=(",", ":"), sort_keys=True)
        temporary = self.path.with_suffix(".partial")
        temporary.write_text(body, encoding="utf-8")
        temporary.replace(self.path)
        print("BEHAVE_REPLAY_PROGRESS " + body, flush=True)
        self.next_at = now + defaults.SIM_RTL_REPLAY_PROGRESS_SECONDS


def progress_detail(execution):
    """Diagnostic text only; never promote worker progress to checked evidence."""
    image = execution.outputs.get("replay_progress.json")
    return {} if image is None else {"replay_progress": image.decode("utf-8", errors="replace")}
