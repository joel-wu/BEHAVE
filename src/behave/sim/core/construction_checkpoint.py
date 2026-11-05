"""A resumable operation's callback, without retaining its owning operation."""


def finish_construction(steps, check=None):
    """Check between steps: cancellation must not close the suspended iterator."""
    while True:
        if check is not None:
            check()
        try:
            next(steps)
        except StopIteration as done:
            return done.value


class ConstructionCheckpoint:
    """Replace the current quantum's callback without closing over its owner.

    A suspended generator or completed formula can keep this small cell. Using
    the construction owner's bound method instead creates a reference cycle
    that retains the native formula until Python's cyclic collector runs.
    """

    __slots__ = ("callback",)

    def __init__(self):
        self.callback = None

    def __call__(self):
        if self.callback is not None:
            self.callback()
