#!/runtime/python/bin/python
"""Log ABC before Yosys flushes its deferred output.

The existing EDA sandbox owns deadlines, resource limits and process cleanup.
"""

import subprocess
import sys
import time
from pathlib import Path


def run(argv, *, executable="/tools/abc", log=Path("abc.log"), output=None):
    output = sys.stdout.buffer if output is None else output
    started = time.monotonic()
    with log.open("ab", buffering=0) as trace:
        trace.write(b"=== ABC invocation ===\n")
        with subprocess.Popen([executable, *argv], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT) as process:
            for line in process.stdout:
                trace.write(f"[{time.monotonic() - started:.3f}s] ".encode() + line)
                output.write(line)
                output.flush()
            code = process.wait()
        trace.write(f"[{time.monotonic() - started:.3f}s] exit={code}\n".encode())
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
