"""Operator stderr diagnostics; machine-readable stdout stays with each command."""

import errno
import os
import sys

from behave.agent.attempt import AttemptInterrupted
from behave.core.records import RecordError


def report_error(error: Exception) -> None:
    # Record boundaries provide deliberate public reasons. Arbitrary exception
    # text/filenames/causes can instead contain private trainer commands or data.
    if isinstance(error, (RecordError, AttemptInterrupted)):
        message = str(error)
    elif isinstance(error, OSError):
        number = error.errno
        message = os.strerror(number) if type(number) is int and number in errno.errorcode else "I/O operation failed"
    else:
        message = "invalid command input"
    print(f"{type(error).__name__}: {message}", file=sys.stderr)
