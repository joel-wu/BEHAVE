"""One immutable source comparison, shared by evaluation and task construction.

This operation needs no published release, solver rollout, submission or second
candidate. The caller owns orchestration/retries. The existing trusted worker
owns lifetime; ``behave.sim.api`` owns comparison semantics and evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from behave.core.artifacts import BlobRef, ContentReader, FileData, FileRef, LoadedSource, SourceArtifact, read_verified
from behave.core.records import canonical_json, fields, version
from behave.core.storage import DirectoryStore

from .judge_policy import JudgePolicy
from .judgment import ComponentJudgment

COMPARISON_BACKEND = "behave.public-simulator-judge/20"


@dataclass(frozen=True, slots=True)
class ComparisonInputs:
    component: str
    source: LoadedSource
    reference_behavior: LoadedSource
    contract: FileData
    run_policy: bytes
    judge_policy: bytes
    # Trusted disclosure choice, not a change to the comparator or its score.
    reference_visible: bool = False
    # Optional explicit replay inputs. Sim constructs the complete replay suite.
    replay_stimuli: bytes | None = None
    # Construction may also bind the public RTL declaration to this check.
    spec: FileData | None = None
    policy: JudgePolicy = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.component not in ("behavior", "rtl"):
            raise ValueError("comparison component must be behavior or rtl")
        if any(type(source) is not LoadedSource for source in (self.source, self.reference_behavior)):
            raise TypeError("comparison requires immutable source bundles")
        if type(self.contract) is not FileData:
            raise TypeError("comparison requires immutable contract bytes")
        if type(self.run_policy) is not bytes or type(self.judge_policy) is not bytes:
            raise TypeError("comparison policies must be immutable bytes")
        if type(self.reference_visible) is not bool:
            raise TypeError("reference visibility must be explicit")
        object.__setattr__(self, "policy", JudgePolicy.from_bytes(self.judge_policy))
        if self.replay_stimuli is not None:
            if type(self.replay_stimuli) is not bytes:
                raise TypeError("replay stimuli must be immutable JSON bytes")
            if self.policy.analysis is not None:
                raise ValueError("explicit replay does not run generation analysis")
        if self.spec is not None and type(self.spec) is not FileData:
            raise TypeError("comparison spec must be immutable file data")

    def as_dict(self) -> dict:
        row = {"schema": "behave.source-comparison/1", "component": self.component,
                "source": self.source.artifact.as_dict(),
                "reference_behavior": self.reference_behavior.artifact.as_dict(),
                "contract": self.contract.reference.as_dict(),
                "run_policy": BlobRef.of(self.run_policy).as_dict(),
                "judge_policy": BlobRef.of(self.judge_policy).as_dict()}
        if self.reference_visible:
            row["reference_visible"] = True
        if self.replay_stimuli is not None:
            row["replay_stimuli"] = BlobRef.of(self.replay_stimuli).as_dict()
        if self.spec is not None:
            row["spec"] = self.spec.reference.as_dict()
        return row

    def stage_contents(self, objects: DirectoryStore) -> tuple[BlobRef, ...]:
        """Copy input bytes once per distinct content, without saving a manifest."""
        contents = (self.contract.data, *(file.data for file in (*self.source.files, *self.reference_behavior.files)),
                    self.run_policy, self.judge_policy)
        if self.replay_stimuli is not None:
            contents += (self.replay_stimuli,)
        if self.spec is not None:
            contents += (self.spec.data,)
        return tuple(objects.put(data) for data in dict.fromkeys(contents))

    def stage(self, objects: DirectoryStore) -> tuple[BlobRef, tuple[BlobRef, ...]]:
        refs = self.stage_contents(objects)
        identity = objects.put(canonical_json(self.as_dict()))
        return identity, tuple(dict.fromkeys((identity, *refs)))

    @classmethod
    def from_dict(cls, value: object, reader: ContentReader) -> ComparisonInputs:
        row = dict(value)
        reference_visible = row.pop("reference_visible", False)
        replay_stimuli = row.pop("replay_stimuli", None)
        spec = row.pop("spec", None)
        row = fields(row, {"schema", "component", "source", "reference_behavior", "contract",
                             "run_policy", "judge_policy"})
        version(row["schema"], "behave.source-comparison/1")

        def source(value):
            artifact = SourceArtifact.from_dict(value)
            return LoadedSource(artifact, tuple(FileData(ref, read_verified(reader, ref.content))
                                                 for ref in artifact.files))

        contract = FileRef.from_dict(row["contract"])
        return cls(row["component"], source(row["source"]), source(row["reference_behavior"]),
                   FileData(contract, read_verified(reader, contract.content)),
                   read_verified(reader, BlobRef.from_dict(row["run_policy"])),
                   read_verified(reader, BlobRef.from_dict(row["judge_policy"])), reference_visible,
                   None if replay_stimuli is None else read_verified(reader, BlobRef.from_dict(replay_stimuli)),
                   None if spec is None else FileData(FileRef.from_dict(spec),
                       read_verified(reader, FileRef.from_dict(spec).content)))


@dataclass(frozen=True, slots=True)
class ComparisonRequest:
    """Trusted invocation context; host paths never enter ComparisonInputs."""

    inputs: ComparisonInputs
    workdir: Path
    dispatch: int = 0
    cancelled: Callable[[], bool] = lambda: False
    on_decided: Callable[[ComponentJudgment], None] | None = None
    cpu_pool: dict | None = None
    score_deadline: float | None = None

    def __post_init__(self) -> None:
        if type(self.inputs) is not ComparisonInputs or not isinstance(self.workdir, Path):
            raise TypeError("comparison requires fixed inputs and an explicit work directory")
        if type(self.dispatch) is not int or not 0 <= self.dispatch <= self.policy.max_retries:
            raise ValueError("invalid comparison dispatch")
        if not callable(self.cancelled):
            raise TypeError("comparison cancellation must be callable")
        if self.on_decided is not None and not callable(self.on_decided):
            raise TypeError("score publication must be callable")

    @property
    def policy(self) -> JudgePolicy:
        return self.inputs.policy


def compare_sources(inputs: ComparisonInputs, *, objects: DirectoryStore, workdir: Path,
                    dispatch: int = 0, cancelled: Callable[[], bool] = lambda: False,
                    on_decided: Callable[[ComponentJudgment], None] | None = None,
                    cpu_pool: dict | None = None) -> ComponentJudgment:
    """Run one bounded comparison through the existing supervised service.

    ``workdir`` is a fresh private node-local directory owned by the trusted
    caller. No candidate source is imported here. Analysis runs only when the
    supplied policy explicitly requests it; synthesis checks require None.
    """
    from .judge import run_worker

    return run_worker(ComparisonRequest(inputs, workdir, dispatch, cancelled, on_decided, cpu_pool), objects)
