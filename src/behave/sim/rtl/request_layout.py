"""Disjoint, immutable coordinates for finite RTL analysis requests."""

from types import MappingProxyType

from ..core.refusal import Refusal, Refused


def coordinate_bounds(entries):
    """Freeze intervals only after checking every declaration, before key merging."""
    bounds = {}
    for name, interval in entries:
        if type(name) is not str or not name:
            raise TypeError("a request coordinate has a nonempty exact name")
        if name in bounds:
            raise Refused(Refusal(
                "rtl-request-coordinate-collision",
                "distinct request roles must not share a coordinate",
                coordinate=name))
        low, high = interval
        if type(low) is not int or type(high) is not int or low > high:
            raise ValueError("a request coordinate has an exact finite integer interval")
        bounds[name] = (low, high)
    return MappingProxyType(bounds)
