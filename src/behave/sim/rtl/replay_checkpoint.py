"""Same-process replay checkpoints, assessed outside the candidate sandbox.

Only one checkpoint is in flight. The simulator pauses at a settled boundary
while its existing CPU lane decodes, monitors and compares the prefix. No
reference implementation or expected values are mounted in the sandbox.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from time import monotonic, sleep

from behave.core import runtime_defaults as defaults

from .trace_document import write_trace_document

MOUNT = "/replay-checkpoints"


class CheckpointClient:
    def __init__(self, completion):
        self.completion = completion
        self.outputs = frozenset(name for name, _ in completion.outputs)
        self.directory = Path(MOUNT)
        self.sequence = 0
        self.next_count = 1
        self.next_poll = 0.0
        self.invalidated = False

    def stopped(self):
        now = monotonic()
        if now < self.next_poll:
            return False
        self.next_poll = now + defaults.ISOLATION_COMPLETION_POLL_SECONDS
        return (self.directory / "stop").exists()

    def due(self, counts, *, memory_quiescent):
        accepted, emitted = counts["accepted"], counts["emitted"]
        return (not self.invalidated and sum(accepted.values()) >= self.next_count
                and self.completion.at_checkpoint_boundary(
                    accepted, emitted, memory_quiescent=memory_quiescent))

    def invalidate(self):
        # Announce a known later violation *before* writing its potentially
        # large evidence. A timeout during publication must not hide that fact.
        # The host no longer acknowledges checkpoints after this, so publish
        # none: the completed record carries the violation.
        self.invalidated = True
        (self.directory / "unsafe").touch()

    def publish(self, trace, accepted):
        self.sequence += 1
        partial = self.directory / "trace.partial"
        write_trace_document(partial, [trace])
        partial.replace(self.directory / "trace.json")
        partial = self.directory / "ready.partial"
        partial.write_text(str(self.sequence), encoding="ascii")
        partial.replace(self.directory / "ready")
        # Deliberately no simulator yield: the physical clock and DUT state do
        # not advance while the host checks the observation.
        while not self.stopped():
            try:
                if (self.directory / "ack").read_text(encoding="ascii") == str(self.sequence):
                    # Geometric publication bounds repeated whole-prefix work;
                    # it is not a limit on transactions, slots or evidence.
                    self.next_count = 2 * sum(accepted.values())
                    return True
            except FileNotFoundError:
                pass
            sleep(defaults.ISOLATION_COMPLETION_POLL_SECONDS)
        return False


class CheckpointService:
    def __init__(self, consume, invalidate):
        self.consume = consume
        self.invalidate = invalidate
        self.failure = None
        self.stop_requested = False

    def run(self, execute, directory):
        """Join normal isolation cleanup before propagating callback cancellation."""
        from ..execution_control import check_execution

        sequence = 0
        invalidated = False
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="replay-supervisor") as pool:
            future = pool.submit(copy_context().run, execute)
            while not future.done():
                if self.failure is None:
                    try:
                        check_execution()
                        if (directory / "unsafe").exists() and not invalidated:
                            # The checked prefix no longer decides. Do not stop
                            # the guest: its completed record is the failure
                            # evidence, and a stop would leave only the
                            # placeholder no-result in its place.
                            self.invalidate()
                            invalidated = True
                        ready = directory / "ready"
                        if ready.exists() and not invalidated:
                            number = int(_read(ready))
                            if number == sequence + 1:
                                if self.consume(_read(directory / "trace.json")) is False:
                                    self.stop_requested = True
                                    _create(directory / "stop", b"")
                                sequence = number
                                partial = directory / f"ack-{sequence}.partial"
                                _create(partial, str(sequence).encode("ascii"))
                                partial.replace(directory / "ack")
                            elif number != sequence:
                                raise ValueError("replay checkpoint sequence is not contiguous")
                    except BaseException as exc:  # noqa: BLE001 - join before propagating cancellation
                        self.failure = exc
                        try:
                            _create(directory / "stop", b"")
                        except FileExistsError:
                            pass  # Already stopped; never follow or modify its target.
                sleep(defaults.ISOLATION_COMPLETION_POLL_SECONDS)
            result = future.result()
        # A violation can arrive concurrently with timeout/cleanup. It must
        # supersede a good checkpoint even when no comparison budget remains.
        if (directory / "unsafe").exists() and not invalidated:
            try:
                self.invalidate()
                self.stop_requested = True
            except BaseException as exc:  # noqa: BLE001 - preserve the earlier cancellation
                if self.failure is None:
                    self.failure = exc
        return result


def _read(path):
    # Candidate-visible files cannot redirect a host read outside this channel.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        import stat

        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("checkpoint evidence must be a regular file")
        return stream.read()


def _create(path, body):
    # Ack/stop paths are candidate-visible too. Never open an existing inode,
    # including a symlink/hardlink planted between readiness and host response.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(body)
