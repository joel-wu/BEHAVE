"""Public cocotb external-memory driver. No Sim/evaluator imports."""

__all__ = ["MemoryDriver"]


def __getattr__(name):
    if name == "MemoryDriver":
        from ._cocotb import MemoryDriver

        return MemoryDriver
    raise AttributeError(name)
