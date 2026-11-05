"""Regular operator-file reads, independent of record schemas and execution."""

import os
import stat
from pathlib import Path


def read_regular_file(path: str | Path) -> bytes:
    """Read the whole file, rejecting special files without waiting on a FIFO.

    Operator-selected symlinks to regular files are allowed; this is not the
    stricter, digest-authenticated content-store reader.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("operator input must be a regular file")
        return stream.read()
