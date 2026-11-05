"""Private fixed entrypoint: decode/monitor only, never caller or model source."""

import socket
import sys

from .trace_postprocessing import _initialize, _worker
from .trace_process import READY
from .trace_transport import TraceChannel


def main():
    with TraceChannel(socket.socket(fileno=int(sys.argv[1]))) as connection:
        _initialize(*connection.recv())
        connection.send(READY)
        while True:
            try:
                request = connection.recv()
            except EOFError:
                return
            try:
                result = _worker(*request)
            except Exception as exc:  # noqa: BLE001 - trusted computation defect
                connection.send((False, type(exc).__name__))
            else:
                connection.send((True, result))
                # The host now owns this checked record. Keeping it in the
                # worker's loop frame doubles the live trace graph during the
                # next decode, especially for successive long checkpoints.
                del result
            finally:
                del request


if __name__ == "__main__":
    main()
