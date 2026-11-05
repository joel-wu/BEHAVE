"""Public operand sampling hints, never restrictions on legal memory values.

Regions and packed lanes describe operands without executing task code. The
baseline also includes zero, declared-edge and uninitialized/raw-bit images.
Sampled values become ordinary MemoryRun.initial cells, so all backends and
replay use the same immutable image. Nothing here consults either candidate.
"""
from __future__ import annotations

import hashlib

from .frozen import Frozen

MEMORY_SAMPLING_VERSION = "memory-sampling/1"
MEMORY_IMAGE_MODES = ("ordinary", "ordinary", "ordinary", "ordinary",
                      "ordinary", "zero", "edge", "raw")


def _uint(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} is an exact integer >= {minimum}")
    return value


def _words(values, width, name):
    if type(values) not in (tuple, list):
        raise TypeError(f"{name} is a finite list of encoded words")
    values = tuple(values)
    if any(type(v) is not int or not 0 <= v < 1 << width for v in values):
        raise ValueError(f"{name} contains a value outside its lane")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} contains duplicate words")
    return values


class MemorySampleLane(Frozen):
    """A packed bit lane sampled from explicit words or an unsigned range.

    `counter` chooses a seeded rotation of the range/list, then advances by
    address. It can express distinct coordinate/index operands without deriving
    an algorithm's neighbors, routing decisions or outputs.
    """
    __slots__ = ("offset", "width", "values", "lower", "upper", "sequence", "edges")

    def __init__(self, offset, width, *, values=(), lower=0, upper=None,
                 sequence="random", edges=()):
        self.offset = _uint(offset, "lane offset")
        self.width = _uint(width, "lane width", 1)
        self.values = _words(values, width, "lane values")
        self.edges = _words(edges, width, "lane edges")
        self.lower = _uint(lower, "lane lower bound")
        self.upper = (1 << width) - 1 if upper is None else _uint(upper, "lane upper bound")
        if not self.lower <= self.upper < 1 << width:
            raise ValueError("a lane range fits its unsigned width")
        if self.values and (lower != 0 or upper is not None):
            raise ValueError("choose lane values or a range, not both")
        if sequence not in ("random", "counter") or type(sequence) is not str:
            raise ValueError("lane sequence is random or counter")
        self.sequence = sequence
        self._freeze()

    def as_dict(self):
        return {"offset": self.offset, "width": self.width,
                "values": list(self.values), "lower": self.lower, "upper": self.upper,
                "sequence": self.sequence, "edges": list(self.edges)}

    @classmethod
    def from_dict(cls, value):
        fields = {"offset", "width", "values", "lower", "upper", "sequence", "edges"}
        if type(value) is not dict or set(value) != fields:
            raise TypeError("a memory sample lane has its complete field set")
        if type(value["values"]) is not list or type(value["edges"]) is not list:
            raise TypeError("lane words are JSON lists")
        if value["values"]:
            if type(value["lower"]) is not int or value["lower"] != 0 or \
                    type(value["width"]) is not int or value["width"] < 1 or \
                    type(value["upper"]) is not int or value["upper"] != (1 << value["width"]) - 1:
                raise ValueError("explicit words carry the canonical full-width range")
            bounds = {}
        else:
            bounds = {"lower": value["lower"], "upper": value["upper"]}
        return cls(value["offset"], value["width"], values=value["values"],
                   sequence=value["sequence"], edges=value["edges"], **bounds)


class MemorySampleRegion(Frozen):
    """Half-open word-address interval, with an exact packed-lane layout."""
    __slots__ = ("start", "stop", "lanes")

    def __init__(self, start, stop, lanes):
        self.start = _uint(start, "region start")
        self.stop = _uint(stop, "region stop", self.start + 1)
        if type(lanes) not in (tuple, list) or not lanes or \
                any(type(lane) is not MemorySampleLane for lane in lanes):
            raise TypeError("a region needs exact MemorySampleLane records")
        self.lanes = tuple(sorted(lanes, key=lambda lane: lane.offset))
        end = 0
        for lane in self.lanes:
            if lane.offset != end:
                raise ValueError("region lanes cover one word without gaps or overlaps")
            end += lane.width
        self._freeze()

    @property
    def width(self):
        return sum(lane.width for lane in self.lanes)

    def as_dict(self):
        return {"start": self.start, "stop": self.stop,
                "lanes": [lane.as_dict() for lane in self.lanes]}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"start", "stop", "lanes"} or \
                type(value["lanes"]) is not list:
            raise TypeError("a memory sample region has its complete field set")
        return cls(value["start"], value["stop"],
                   [MemorySampleLane.from_dict(lane) for lane in value["lanes"]])


class MemorySampling(Frozen):
    """Task-owned sampling description; omitted addresses retain raw defaults."""
    __slots__ = ("regions",)

    def __init__(self, regions):
        if type(regions) not in (tuple, list) or not regions or \
                any(type(r) is not MemorySampleRegion for r in regions):
            raise TypeError("memory sampling needs exact MemorySampleRegion records")
        self.regions = tuple(sorted(regions, key=lambda r: r.start))
        if any(a.stop > b.start for a, b in zip(self.regions, self.regions[1:])):
            raise ValueError("memory sample regions do not overlap")
        self._freeze()

    def validate_shape(self, depth, width):
        if any(r.stop > depth or r.width != width for r in self.regions):
            raise ValueError("memory sampling must fit the declared depth and word width")

    def as_dict(self):
        return {"version": MEMORY_SAMPLING_VERSION,
                "regions": [region.as_dict() for region in self.regions]}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {"version", "regions"} or \
                value["version"] != MEMORY_SAMPLING_VERSION or type(value["regions"]) is not list:
            raise TypeError("a memory sampling image has its supported version and exact fields")
        return cls([MemorySampleRegion.from_dict(r) for r in value["regions"]])


def memory_image_mode(case):
    _uint(case, "baseline case")
    return MEMORY_IMAGE_MODES[case % len(MEMORY_IMAGE_MODES)]


def sampled_memory_initial(memory, *, seed, case):
    """Return a deterministic finite operand image for the common baseline."""
    profile = memory.sampling
    mode = memory_image_mode(case)
    if profile is None or mode == "raw":
        return ()
    if type(seed) is not str or not seed:
        raise ValueError("memory sampling seed is a nonempty string")
    cells = []
    for region in profile.regions:
        prepared = []
        for lane in region.lanes:
            salt = f"{MEMORY_SAMPLING_VERSION}\0{seed}\0{memory.name}\0{case}\0{region.start}\0{lane.offset}"
            key = hashlib.sha256(salt.encode()).digest()
            prepared.append((lane, key))
        for address in range(region.start, region.stop):
            word = 0
            for lane, key in prepared:
                index = address - region.start
                if mode == "zero":
                    value = 0
                else:
                    if lane.sequence == "counter":
                        draw = int.from_bytes(key, "big") + index
                    else:
                        draw = int.from_bytes(hashlib.sha256(key + str(index).encode()).digest(), "big")
                    choices = lane.edges if mode == "edge" and lane.edges else lane.values
                    value = (choices[draw % len(choices)] if choices else
                             lane.lower + draw % (lane.upper - lane.lower + 1))
                word |= value << lane.offset
            cells.append((address, word))
    return tuple(cells)
