"""Private PID-1 bootstrap; transfer a kernel lifetime handle before exec.

The parent acknowledges the pidfd before any configured service code can run.
Unlike the outer unshare PID, this handle becomes readable only when namespace
init exits. Killing PID 1 also terminates the namespace's remaining processes.
"""

import ctypes
import os
import socket
import sys


def self_pidfd() -> int:
    # The standalone CPython build used by the harness omits os.pidfd_open,
    # even on a kernel/libc which provides it. Use libc's named interface, not
    # architecture-specific syscall numbers or a weaker numeric-PID fallback.
    operation = ctypes.CDLL(None, use_errno=True).pidfd_open
    operation.argtypes = (ctypes.c_int, ctypes.c_uint)
    operation.restype = ctypes.c_int
    descriptor = operation(os.getpid(), 0)
    if descriptor < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return descriptor


def main() -> None:
    if os.getpid() != 1:
        raise RuntimeError("trusted service requires a new PID namespace")
    startup_fd = os.environ.pop("BEHAVE_ISOLATION_STARTUP_FD", None)
    if startup_fd is not None:
        os.close(int(startup_fd))
    with socket.socket(fileno=int(sys.argv[1])) as owner:
        pidfd = self_pidfd()
        try:
            socket.send_fds(owner, [b"init"], [pidfd])
            if owner.recv(1) != b"1":
                raise RuntimeError("service lifetime handoff was not acknowledged")
        finally:
            os.close(pidfd)
    os.execv(sys.argv[2], sys.argv[2:])


if __name__ == "__main__":
    main()
