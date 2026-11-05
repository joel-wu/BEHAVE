"""Pinned operator input for the detached serving acceptance tool.

This is not another checkpoint format: all history, tensor-file and lineage
verification remains in train.checkpoint_storage. It never discovers 'latest'.
"""

from dataclasses import dataclass
from pathlib import Path

from behave.agent.providers.local_chat import LocalChatEndpoint
from behave.core.artifacts import BlobRef, DirectoryReader
from behave.core.file_input import read_regular_file
from behave.core.records import canonical_json, fields, parse_json, text, version
from behave.serving.spec import ServingSpec
from behave.train.checkpoint_storage import stage_weights
from behave.train.training_data import NativeChatVerifier
from behave.train.update_records import TrainerContract


@dataclass(frozen=True)
class WeightInput:
    objects: str
    publication: BlobRef
    checkpoint: BlobRef
    contract: TrainerContract
    component: str
    endpoints: tuple[LocalChatEndpoint, ...] = ()

    def __post_init__(self):
        if type(self.objects) is not str or not Path(self.objects).is_absolute():
            raise ValueError("weight objects require an explicit absolute private CAS directory")
        if any(type(ref) is not BlobRef for ref in (self.publication, self.checkpoint)):
            raise TypeError("weights require explicit publication and checkpoint references")
        if type(self.contract) is not TrainerContract:
            raise TypeError("weights require the expected trainer contract")
        text(self.component, "inference component")
        if type(self.endpoints) is not tuple:
            raise TypeError("approved native profiles must be a tuple")
        self.verifier()  # Validate the explicitly approved history, if present.

    def verifier(self):
        return NativeChatVerifier(*self.endpoints) if self.endpoints else None

    def to_bytes(self) -> bytes:
        return canonical_json({
            "schema": "behave.weight-smoke-input/1", "objects": self.objects,
            "publication": self.publication.as_dict(), "checkpoint": self.checkpoint.as_dict(),
            "contract": parse_json(self.contract.to_bytes()), "component": self.component,
            "endpoints": [ep.as_dict() for ep in self.endpoints],
        })

    @classmethod
    def from_bytes(cls, data: bytes):
        row = fields(parse_json(data), {"schema", "objects", "publication", "checkpoint", "contract",
                                      "component", "endpoints"})
        version(row["schema"], "behave.weight-smoke-input/1")
        if type(row["endpoints"]) is not list:
            raise ValueError("approved endpoints must be an explicit list")
        return cls(row["objects"], BlobRef.from_dict(row["publication"]), BlobRef.from_dict(row["checkpoint"]),
                   TrainerContract.from_bytes(canonical_json(row["contract"])), row["component"],
                   tuple(LocalChatEndpoint.from_dict(ep) for ep in row["endpoints"]))

    @classmethod
    def read(cls, path: Path, expected: BlobRef | None = None):
        data = read_regular_file(path)
        if expected is not None and BlobRef.of(data) != expected:
            raise ValueError("weight input changed after submission")
        return cls.from_bytes(data)

    def stage(self, spec: ServingSpec) -> ServingSpec:
        return stage_weights(DirectoryReader(self.objects), self.publication,
                             checkpoint=self.checkpoint, contract=self.contract, component=self.component,
                             spec=spec, verifier=self.verifier())
