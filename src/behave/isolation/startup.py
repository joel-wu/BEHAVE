"""Node-local startup admission, not a limit on running isolated commands.

The bootstrap inherits the locked descriptor. An unsuccessful launcher only
closes its own copy: an unconfirmed child keeps its slot until it exits. After
the ready handshake, explicitly unlock so the outer unshare supervisor cannot
hold startup admission for the command's whole lifetime. Never unlink live
lock files or share candidate state to amortize startup.
"""

from __future__ import annotations

import fcntl
import os
import stat
import time

from behave.core import runtime_defaults as defaults
from behave.core.paths import isolation_startup_root

from .preparation_control import check_preparation


class StartupCancelled(Exception):
    """The caller cancelled before a namespace was launched."""


def startup_environment(slots=None):
    """Encode an explicit launch policy, never inherit a parent's stale gate.

    Configuration uses null for no additional admission queue, or a positive
    integer for an operator-requested throttle. This changes only admission,
    not the sandbox, CPU allocation, deadlines or cleanup. Coordinators apply
    it before spawning workers; nested services relay the resolved value.
    """
    if slots is not None and (type(slots) is not int or slots < 1):
        raise ValueError("isolation startup slots must be a positive integer or null")
    return {"BEHAVE_ISOLATION_STARTUP_SLOTS": "none" if slots is None else str(slots)}


def startup_slots():
    """Trusted launcher override; candidate environments never receive it."""
    value = os.environ.get("BEHAVE_ISOLATION_STARTUP_SLOTS")
    if value is None:
        slots = defaults.ISOLATION_STARTUP_SLOTS
    elif value == "none":
        slots = None
    elif value.isascii() and value.isdecimal():
        slots = int(value)
    else:
        raise ValueError("isolation startup slots must be positive or none")
    if slots is not None and (type(slots) is not int or slots < 1):
        raise ValueError("isolation startup slots must be positive or None")
    return slots


class StartupPermit:
    def __init__(self, descriptor=None, directory=None):
        self.descriptor = descriptor
        self.directory = directory

    @property
    def pass_fds(self):
        return () if self.descriptor is None else (self.descriptor,)

    def ready(self):
        if self.descriptor is not None:
            fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        self.close()

    def close(self):
        if self.descriptor is not None:
            descriptor, self.descriptor = self.descriptor, None
            os.close(descriptor)


def acquire_startup(*, deadline=None, cancelled=None):
    slots = startup_slots()

    def check():
        check_preparation()
        if cancelled is not None and cancelled():
            raise StartupCancelled()
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("execution deadline expired waiting for namespace startup")

    check()
    if slots is None:
        return StartupPermit()
    # scratch_root is explicitly node-local and ignores ambient TMPDIR. All
    # cooperating batches of this uid using that root share startup admission.
    root = isolation_startup_root()
    root.mkdir(mode=0o700, exist_ok=True)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        metadata = os.fstat(directory)
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError("startup admission requires an owner-private directory")
        while True:
            check()
            for slot in range(slots):
                descriptor = os.open(str(slot), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                                     0o600, dir_fd=directory)
                try:
                    metadata = os.fstat(descriptor)
                    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                            or stat.S_IMODE(metadata.st_mode) & 0o077 or metadata.st_nlink != 1):
                        raise PermissionError("startup admission requires an owner-private regular file")
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    check()
                    permit = StartupPermit(descriptor, root)
                    descriptor = None
                    return permit
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
            time.sleep(defaults.ISOLATION_COMPLETION_POLL_SECONDS)
    finally:
        os.close(directory)
