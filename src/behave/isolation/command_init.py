"""Reap orphan descendants of a supervised trusted command.

The service supervisor owns this PID 1 through its pidfd. An application (for
example pytest or an eval coordinator) must not itself become the namespace
reaper: it does not know about orphaned grandchildren from nested services.
"""

import os
import sys


def main() -> int:
    if os.getpid() != 1 or len(sys.argv) < 2:
        raise RuntimeError("command init requires a supervised PID namespace and command")
    leader = os.fork()
    if leader == 0:
        try:
            os.execv(sys.argv[1], sys.argv[1:])
        except OSError:
            os._exit(127)
    while True:
        # We own the leader and only reap otherwise-orphaned descendants;
        # application processes keep ownership of their own children's waits.
        child, status = os.waitpid(-1, 0)
        if child == leader:
            code = os.waitstatus_to_exitcode(status)
            # Leaving PID 1 terminates remaining live descendants. The parent
            # supervisor separately checks that the namespace has exited.
            return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(main())
