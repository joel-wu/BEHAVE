"""Frozen experiment inputs. No candidate-selected constraints or golden paths."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef
from behave.core.records import canonical_json


def identifier(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
        raise ValueError("PPA pilot requires an ordinary HDL identifier")
    return value


@dataclass(frozen=True)
class TimingPolicy:
    clock: str
    period_ns: float
    uncertainty_ns: float
    input_delay_ns: float
    output_delay_ns: float
    input_transition_ns: float
    output_load_pf: float

    def __post_init__(self):
        identifier(self.clock)
        for key, value in asdict(self).items():
            if key == "clock":
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid timing constraint {key}")
        if self.period_ns <= 0 or self.uncertainty_ns >= self.period_ns:
            raise ValueError("clock period must exceed zero and uncertainty")


@dataclass(frozen=True)
class MappingPolicy:
    """ABC boundary driver and optional shared post-map repair profile."""

    driving_cell: str = "sky130_fd_sc_hd__buf_2"
    repair_design: bool = False
    dont_use_cells: tuple[str, ...] = ()
    placement_repair: bool = False
    technology: str = 'sky130hd'

    def __post_init__(self):
        from .technology import technology
        technology(self.technology)
        identifier(self.driving_cell)
        if type(self.repair_design) is not bool:
            raise TypeError("mapping repair must be an explicit boolean")
        if type(self.placement_repair) is not bool:
            raise TypeError("placement repair must be an explicit boolean")
        if self.placement_repair and not self.repair_design:
            raise ValueError("placement repair requires mapping repair")
        if type(self.dont_use_cells) is not tuple:
            raise TypeError("mapping exclusions must be a tuple")
        for cell in self.dont_use_cells:
            identifier(cell)
        if len(set(self.dont_use_cells)) != len(self.dont_use_cells):
            raise ValueError("duplicate mapping exclusion")
        if self.driving_cell in self.dont_use_cells:
            raise ValueError("mapping driver cannot be excluded")

    @property
    def stage(self):
        return ('mapped-synthesis-placement-estimated-interconnect-sta' if self.placement_repair
                else 'mapped-synthesis-ideal-interconnect-sta')

    @classmethod
    def from_dict(cls, row):
        from behave.core.records import fields
        row = fields(row, {'driving_cell'} | (set(row) & {
            'repair_design', 'dont_use_cells', 'placement_repair', 'technology'}))
        cells = row.get('dont_use_cells', ())
        if type(cells) not in (list, tuple):
            raise TypeError('mapping exclusions must be a sequence of cell names')
        return cls(**{**row, 'dont_use_cells': tuple(cells)})


@dataclass(frozen=True)
class PowerPolicy:
    """Diagnostic vectorless estimate, not workload activity or an objective."""

    input_activity: float = 0.1
    input_duty: float = 0.5

    def __post_init__(self):
        for name, upper in (("input_activity", 2), ("input_duty", 1)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= upper:
                raise ValueError(f"invalid power assumption {name}")


@dataclass(frozen=True)
class Workload:
    """One input stream, ordered responses, fresh reset; Sim owns completion."""

    name: str
    inputs: tuple[tuple[int, ...], ...]
    reset_cycles: int = defaults.SIM_RESET_HOLD_CYCLES

    def __post_init__(self):
        identifier(self.name)
        if type(self.inputs) is not tuple or not self.inputs or any(
            type(row) is not tuple or not row or any(type(v) is not int for v in row) for row in self.inputs
        ):
            raise ValueError("workload needs a nonempty tuple of integer input records")
        if type(self.reset_cycles) is not int or self.reset_cycles < 1:
            raise ValueError("reset_cycles must be positive")

    @property
    def identity(self):
        return BlobRef.of(canonical_json(asdict(self))).sha256


@dataclass(frozen=True)
class EDABudget:
    """Operator-owned execution deadlines and per-stage process-tree memory."""

    synthesis_seconds: int = defaults.PPA_SYNTHESIS_SECONDS
    timing_seconds: int = defaults.PPA_TIMING_SECONDS
    power_seconds: int = defaults.PPA_POWER_SECONDS
    overhead_seconds: int = defaults.PPA_OVERHEAD_SECONDS
    rss_bytes: int = defaults.SIM_EDA_RSS_BYTES

    def __post_init__(self):
        for name, value in asdict(self).items():
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    @property
    def service_seconds(self):
        return self.synthesis_seconds + self.timing_seconds + self.power_seconds + self.overhead_seconds


@dataclass(frozen=True)
class Experiment:
    timing: TimingPolicy
    workloads: tuple[Workload, ...]
    targets_ns: tuple[float, ...]
    service_seconds: int = defaults.PPA_SERVICE_SECONDS  # Non-EDA worker lifetime.
    power: PowerPolicy = PowerPolicy()
    eda_budget: EDABudget = EDABudget()
    sat_resource_sharing: bool = True
    mapping: MappingPolicy = MappingPolicy()
    parallelism_lanes: tuple[int, ...] = ()

    def __post_init__(self):
        if type(self.timing) is not TimingPolicy:
            raise TypeError("experiment requires a timing policy")
        if type(self.power) is not PowerPolicy:
            raise TypeError("experiment requires an explicit power policy")
        if type(self.eda_budget) is not EDABudget:
            raise TypeError("experiment requires an EDA budget")
        if type(self.mapping) is not MappingPolicy:
            raise TypeError("experiment requires a mapping policy")
        if type(self.sat_resource_sharing) is not bool:
            raise TypeError("SAT resource sharing must be an explicit boolean")
        if type(self.workloads) is not tuple or not self.workloads or any(type(w) is not Workload for w in self.workloads):
            raise ValueError("experiment requires explicit workloads")
        if len({w.name for w in self.workloads}) != len(self.workloads):
            raise ValueError("workload names must be unique")
        if type(self.targets_ns) is not tuple or any(
            type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in self.targets_ns
        ):
            raise ValueError("targets must be finite positive times")
        if type(self.parallelism_lanes) is not tuple or any(
            type(v) is not int or v < 1 for v in self.parallelism_lanes
        ) or len(set(self.parallelism_lanes)) != len(self.parallelism_lanes):
            raise ValueError("parallelism lanes must be distinct positive integers")
        if bool(self.targets_ns) == bool(self.parallelism_lanes):
            raise ValueError("choose time bounds or parallelism guidance, not both")
        for key in ("service_seconds",):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError(f"{key} must be positive")

    def as_dict(self):
        row = {"schema": "behave.ppa-experiment/10" if self.parallelism_lanes else "behave.ppa-experiment/9", **asdict(self),
               "aggregation": "maximum", "stage": self.mapping.stage,
               "initial_rtl": None}
        if not self.parallelism_lanes:
            row.pop("parallelism_lanes")
        if not self.mapping.repair_design:
            row["mapping"].pop("repair_design")
        if not self.mapping.dont_use_cells:
            row["mapping"].pop("dont_use_cells")
        if not self.mapping.placement_repair:
            row["mapping"].pop("placement_repair")
        if self.mapping.technology == 'sky130hd':
            row['mapping'].pop('technology')
        # Keep the identity of existing default-memory experiments unchanged.
        if self.eda_budget.rss_bytes == defaults.SIM_EDA_RSS_BYTES:
            row["eda_budget"].pop("rss_bytes")
        return row

    @property
    def identity(self):
        return BlobRef.of(canonical_json(self.as_dict())).sha256

    @classmethod
    def from_dict(cls, row):
        from behave.core.records import fields, version

        architecture = row.get("schema") == "behave.ppa-experiment/10"
        row = fields(row, {"schema", "timing", "workloads", "targets_ns",
                           "service_seconds", "aggregation", "stage", "initial_rtl", "power",
                           "eda_budget", "sat_resource_sharing", "mapping"}
                     | ({"parallelism_lanes"} if architecture else set()))
        version(row["schema"], "behave.ppa-experiment/10" if architecture else "behave.ppa-experiment/9")
        if architecture and (row["targets_ns"] or not row["parallelism_lanes"]):
            raise ValueError("parallelism experiments require lanes and no time bounds")
        mapping = MappingPolicy.from_dict(row['mapping'])
        if (row["aggregation"], row["stage"], row["initial_rtl"]) != (
            "maximum", mapping.stage, None
        ):
            raise ValueError("unsupported PPA experiment profile")
        return cls(TimingPolicy(**row["timing"]), tuple(Workload(**{
            **w, "inputs": tuple(tuple(v) for v in w["inputs"])
        }) for w in row["workloads"]), tuple(row["targets_ns"]),
            row["service_seconds"], PowerPolicy(**row["power"]),
            EDABudget(**fields(row["eda_budget"], {"synthesis_seconds", "timing_seconds", "power_seconds",
                                                 "overhead_seconds"} | (
                {"rss_bytes"} if "rss_bytes" in row["eda_budget"] else set()))), row["sat_resource_sharing"],
            mapping,
            tuple(row.get("parallelism_lanes", ())))


def replica_objective(experiment, replica):
    """One shared assignment for prompt binding and result reporting."""
    lanes, targets = experiment.get("parallelism_lanes", ()), experiment["targets_ns"]
    if bool(lanes) == bool(targets):
        raise ValueError("choose time bounds or parallelism guidance, not both")
    index = replica % len(lanes or targets)
    return {"objective_mode": "parallelism" if lanes else "time_bound", "objective_index": index,
            "target_ns": None if lanes else targets[index],
            "parallelism_lanes": lanes[index] if lanes else None}
