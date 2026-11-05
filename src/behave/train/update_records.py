"""Algorithm-neutral update identities. Reuse is explicit, not implicit replay."""

from dataclasses import dataclass

from behave.core.artifacts import BlobRef
from behave.core.records import RecordError, array, canonical_json, fields, parse_json, text, version


@dataclass(frozen=True, slots=True)
class TrainerContract:
    adapter: str
    configuration: BlobRef
    manifest: BlobRef
    components: tuple[str, ...] = ("model", "optimizer", "rng", "sampler")

    def __post_init__(self):
        text(self.adapter, "trainer adapter/version")
        if type(self.configuration) is not BlobRef or type(self.manifest) is not BlobRef:
            raise TypeError("trainer contract requires configuration and approved training manifest")
        if type(self.components) is not tuple or not self.components:
            raise ValueError("declare nonempty trainer state components")
        for name in self.components:
            text(name, "trainer state component")
        if len(set(self.components)) != len(self.components) or not {"model", "rng", "sampler"} <= set(self.components):
            raise ValueError("unique components must include model, rng and sampler")

    def to_bytes(self):
        data = canonical_json(
            {
                "schema": "behave.trainer-contract/1",
                "adapter": self.adapter,
                "configuration": self.configuration.as_dict(),
                "manifest": self.manifest.as_dict(),
                "components": list(self.components),
            }
        )
        return data

    @classmethod
    def from_bytes(cls, data):
        row = fields(parse_json(data), {"schema", "adapter", "configuration", "manifest", "components"})
        version(row["schema"], "behave.trainer-contract/1")
        result = cls(
            row["adapter"],
            BlobRef.from_dict(row["configuration"]),
            BlobRef.from_dict(row["manifest"]),
            tuple(array(row["components"])),
        )
        if result.to_bytes() != data:
            raise RecordError("record-not-canonical", "trainer contract must be canonical")
        return result


@dataclass(frozen=True, slots=True)
class ExampleUse:
    report: BlobRef
    candidate: int
    turn: int
    exposure: str

    def __post_init__(self):
        if type(self.report) is not BlobRef:
            raise TypeError("example use requires a sealed training report")
        if any(type(value) is not int or value < 0 for value in (self.candidate, self.turn)):
            raise ValueError("candidate and turn must be nonnegative integers")
        text(self.exposure, "algorithm-assigned exposure identity")

    def as_dict(self):
        return {
            "report": self.report.as_dict(),
            "candidate": self.candidate,
            "turn": self.turn,
            "exposure": self.exposure,
        }

    @classmethod
    def from_dict(cls, value):
        row = fields(value, {"report", "candidate", "turn", "exposure"})
        return cls(BlobRef.from_dict(row["report"]), row["candidate"], row["turn"], row["exposure"])


@dataclass(frozen=True, slots=True)
class UpdateRequest:
    key: str
    parent: BlobRef
    uses: tuple[ExampleUse, ...]
    decision: BlobRef

    def __post_init__(self):
        text(self.key, "update identity")
        if type(self.parent) is not BlobRef or type(self.decision) is not BlobRef:
            raise TypeError("update requires a committed checkpoint and an algorithm decision record")
        if type(self.uses) is not tuple or not self.uses:
            raise ValueError("an update requires nonempty example uses")
        if any(type(use) is not ExampleUse for use in self.uses) or len(set(self.uses)) != len(self.uses):
            raise ValueError("example uses must be distinct ExampleUse records")

    def to_bytes(self):
        data = canonical_json(
            {
                "schema": "behave.training-update/1",
                "key": self.key,
                "parent": self.parent.as_dict(),
                "uses": [use.as_dict() for use in self.uses],
                "decision": self.decision.as_dict(),
            }
        )
        return data

    @classmethod
    def from_bytes(cls, data):
        row = fields(parse_json(data), {"schema", "key", "parent", "uses", "decision"})
        version(row["schema"], "behave.training-update/1")
        result = cls(
            row["key"],
            BlobRef.from_dict(row["parent"]),
            tuple(ExampleUse.from_dict(use) for use in array(row["uses"])),
            BlobRef.from_dict(row["decision"]),
        )
        if result.to_bytes() != data:
            raise RecordError("record-not-canonical", "training update must be canonical")
        return result
