"""Explicit fixed-world placement in existing Slurm allocations.

Placement is operator configuration, not an elastic scheduler. All paths must
be available at the same spelling on every node; scratch I/O stays node-local.
"""

import re
from dataclasses import dataclass, replace
from pathlib import Path

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, array, canonical_json, fields, thaw_json

from .checkpoints import TrainingState


@dataclass(frozen=True, slots=True)
class TrainingNode:
    allocation: int
    host: str
    cpus: int = 1
    gpus: int = 0

    def __post_init__(self):
        if type(self.allocation) is not int or self.allocation < 1:
            raise ValueError("training node requires an existing allocation")
        if type(self.host) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", self.host) is None:
            raise ValueError("training node requires one explicit host, not a hostlist")
        if type(self.cpus) is not int or self.cpus < 1:
            raise ValueError("invalid training node CPU count")
        if type(self.gpus) is not int or self.gpus < 0:
            raise ValueError("invalid training node GPU count")

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value):
        return cls(**fields(value, set(cls.__dataclass_fields__)))


@dataclass(frozen=True, slots=True)
class SlurmTraining:
    nodes: tuple[TrainingNode, ...]
    shared_directory: str
    master_port: int
    srun: str = "/usr/bin/srun"
    overlap: bool = False

    def __post_init__(self):
        if (type(self.nodes) is not tuple or len(self.nodes) < 2
                or any(type(node) is not TrainingNode for node in self.nodes)):
            raise ValueError("cross-node training requires at least two explicit nodes")
        if len({node.host for node in self.nodes}) != len(self.nodes):
            raise ValueError("cross-node placement must name distinct hosts")
        for path in (self.shared_directory, self.srun):
            if (type(path) is not str or "\0" in path or not Path(path).is_absolute()
                    or ".." in Path(path).parts or Path(path) == Path("/")):
                raise ValueError("cross-node paths must be explicit absolute paths")
        if type(self.master_port) is not int or not 1 <= self.master_port <= 65535:
            raise ValueError("cross-node rendezvous requires an explicit valid port")
        if type(self.overlap) is not bool:
            raise TypeError("Slurm overlap must be an explicit boolean")

    def as_dict(self):
        return {**{name: getattr(self, name) for name in self.__dataclass_fields__},
                "nodes": [node.as_dict() for node in self.nodes]}

    @classmethod
    def from_dict(cls, value):
        row = fields(value, set(cls.__dataclass_fields__))
        return cls(**{**row, "nodes": tuple(TrainingNode.from_dict(node) for node in array(row["nodes"]))})


def pack_rank_states(states: tuple[TrainingState, ...]) -> TrainingState:
    if type(states) is not tuple or len(states) < 2:
        raise ValueError("distributed checkpoint needs at least two ordered rank states")
    if any(type(state) is not TrainingState for state in states):
        raise TypeError("rank states must be TrainingState records")
    components = {file.component for file in states[0].files}
    if any({file.component for file in state.files} != components for state in states):
        raise RecordError("checkpoint-rank-components", "every rank must supply the same declared component set")
    ranks = [{"policy": state.policy.as_dict(), "cursor": thaw_json(state.cursor)} for state in states]
    policy = BlobRef.of(canonical_json({
        "schema": "behave.distributed-policy/1", "ranks": [row["policy"] for row in ranks],
    }))
    return TrainingState(policy, tuple(
        replace(file, path=f"ranks/{rank:04d}/{file.path}")
        for rank, state in enumerate(states) for file in state.files
    ), {"schema": "behave.distributed-cursor/1", "world_size": len(states), "ranks": ranks})


def unpack_rank_states(state: TrainingState, world_size: int) -> tuple[TrainingState, ...]:
    cursor = thaw_json(state.cursor)
    if (type(world_size) is not int or world_size < 2
            or set(cursor) != {"schema", "world_size", "ranks"}
            or cursor["schema"] != "behave.distributed-cursor/1"
            or type(cursor["world_size"]) is not int or cursor["world_size"] != world_size
            or type(cursor["ranks"]) is not list or len(cursor["ranks"]) != world_size):
        raise RecordError("checkpoint-world-mismatch", "resume requires the original complete rank topology")
    states = []
    for rank, row in enumerate(cursor["ranks"]):
        if type(row) is not dict or set(row) != {"policy", "cursor"}:
            raise RecordError("checkpoint-rank-invalid", "invalid rank checkpoint metadata")
        prefix = f"ranks/{rank:04d}/"
        states.append(TrainingState(BlobRef.from_dict(row["policy"]), tuple(
            replace(file, path=file.path[len(prefix):]) for file in state.files if file.path.startswith(prefix)
        ), row["cursor"]))
    result = tuple(states)
    if pack_rank_states(result) != state:
        raise RecordError("checkpoint-ranks-mismatch", "checkpoint contains unclaimed files or an invalid policy")
    return result
