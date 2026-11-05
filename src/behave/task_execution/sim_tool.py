"""Development comparisons shared by solver tools and PPA.

The caller binds the task and the reference source. Sim owns stimulus generation,
execution and comparison. An agent-authored reference is not a golden oracle.
"""

from dataclasses import dataclass, replace

from behave.agent.prompting import render
from behave.agent.tools import Tool, ToolResult, ToolUnavailable, object_schema
from behave.core.artifacts import (
    ArtifactSelection,
    BlobRef,
    FileData,
    FileRef,
    LoadedSource,
    SourceArtifact,
    read_verified,
)
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json, fields
from behave.core.storage import DirectoryStore

from .comparison import ComparisonInputs, compare_sources
from .feedback import comparison_feedback
from .judge_policy import JudgePolicy


def source_selection_schema():
    return object_schema({"files": {"type": "array", "items": {"type": "string"}},
                          "entry_file": {"type": "string"}, "entry_symbol": {"type": "string"}})


def workspace_source(workspace, selection) -> LoadedSource:
    """Read the selected live files, not an earlier archived workspace snapshot."""
    chosen = ArtifactSelection.from_dict(selection)
    files = []
    for path in chosen.files:
        data = workspace.read(path)
        files.append(FileData(FileRef(path, BlobRef.of(data)), data))
    return LoadedSource(SourceArtifact(tuple(file.reference for file in files),
                                      chosen.entry_file, chosen.entry_symbol), tuple(files))


@dataclass(frozen=True)
class ComparisonToolInputs:
    """Trusted bindings. None selects an agent-authored Behavior on each call.

    Supplied source visibility controls public files/diagnostics, never which
    bytes the comparison executes. A copied/edited public file cannot replace
    the supplied reference.
    """

    contract: FileData
    run_policy: bytes
    judge_policy: bytes
    reference_behavior: LoadedSource | None = None
    reference_visible: bool = False

    def __post_init__(self):
        if self.reference_visible and self.reference_behavior is None:
            raise ValueError("only a supplied reference needs a visibility setting")
        if JudgePolicy.from_bytes(self.judge_policy).analysis is not None:
            raise ValueError("development comparison does not run ancillary analysis")

    @property
    def source(self):
        return "agent" if self.reference_behavior is None else "provided"

    @property
    def diagnostics_visible(self):
        return self.reference_behavior is None or self.reference_visible

    def as_dict(self):
        return {"contract": self.contract.reference.as_dict(),
                "run_policy": BlobRef.of(self.run_policy).as_dict(),
                "judge_policy": BlobRef.of(self.judge_policy).as_dict(),
                "reference_behavior": (None if self.reference_behavior is None
                                       else self.reference_behavior.artifact.as_dict()),
                "reference_visible": self.reference_visible}

    @classmethod
    def from_dict(cls, value, reader):
        row = fields(value, {"contract", "run_policy", "judge_policy", "reference_behavior", "reference_visible"})
        contract = FileRef.from_dict(row["contract"])
        artifact = (None if row["reference_behavior"] is None
                    else SourceArtifact.from_dict(row["reference_behavior"]))
        reference = None if artifact is None else LoadedSource(artifact, tuple(
            FileData(file, read_verified(reader, file.content)) for file in artifact.files))
        return cls(FileData(contract, read_verified(reader, contract.content)),
                   read_verified(reader, BlobRef.from_dict(row["run_policy"])),
                   read_verified(reader, BlobRef.from_dict(row["judge_policy"])),
                   reference, row["reference_visible"])

    @classmethod
    def baseline(cls, task, *, source="agent", reference_visible=False):
        """Bind a published task's development comparison, independently of final scoring."""
        from behave.sim import api

        if source not in {"agent", "provided"}:
            raise ValueError("Behavior source must be agent or provided")
        policy = replace(JudgePolicy.from_bytes(task.judge_policy), command_version=api.COMMAND_VERSION,
                         authority_version=api.RESULT_VERSION, search_seconds=0, analysis=None)
        return cls(task.contract, task.run_policy, policy.to_bytes(),
                   task.reference_behavior if source == "provided" else None, reference_visible)

    def input_blobs(self):
        reference = () if self.reference_behavior is None else tuple(file.data for file in self.reference_behavior.files)
        return (self.contract.data, self.run_policy, self.judge_policy, *reference)

    def public_files(self):
        if not self.reference_visible:
            return ()
        return tuple(FileData(FileRef("reference/" + file.reference.path, file.reference.content), file.data)
                     for file in self.reference_behavior.files)

    def description(self):
        if self.reference_behavior is None:
            return render("shared/tools/sim/comparison_agent_reference")
        if self.reference_visible:
            artifact = self.reference_behavior.artifact
            return render("shared/tools/sim/comparison_visible_reference", entry_file="reference/" + artifact.entry_file,
                          entry_symbol=artifact.entry_symbol)
        return render("shared/tools/sim/comparison_hidden_reference")

    def bind(self, source, reference_behavior=None):
        reference = self.reference_behavior if self.reference_behavior is not None else reference_behavior
        if reference is None:
            raise ValueError("agent-reference comparison requires an explicit Behavior bundle")
        if self.reference_behavior is not None and reference_behavior is not None:
            raise ValueError("a supplied reference cannot be replaced by a tool argument")
        return ComparisonInputs("rtl", source, reference, self.contract, self.run_policy, self.judge_policy,
                                reference_visible=self.diagnostics_visible)

    def capture(self, workspace, arguments):
        names = {"rtl", "behavior"} if self.reference_behavior is None else {"rtl"}
        row = fields(arguments, names)
        return self.bind(workspace_source(workspace, row["rtl"]),
                         None if "behavior" not in row else workspace_source(workspace, row["behavior"]))


def comparison_tool(inputs: ComparisonToolInputs, *, executor=None, cpu_pool=None, cpu_resources=None) -> Tool:
    """One normal agent Tool. The trusted caller owns its reference bindings.

    Scratch is node-local. Retain the selected comparison inputs and returned
    evidence, not complete transient workspace trees. The existing agent
    journal handles settled tool replay; this function does not retry a score.
    """

    def execute(workspace, arguments):
        comparison = inputs.capture(workspace, arguments)
        source_evidence = comparison.stage_contents(workspace.store)
        cleanup = CleanupState.COMPLETE
        from contextlib import ExitStack

        from behave.agent.cpu_resources import cpu_job
        from behave.isolation.scratch import ScratchDirectory
        from behave.sim import api

        with cpu_job(cpu_resources) as lease, ExitStack() as resources, \
                ScratchDirectory(prefix="behave-compare-", cleanup=lambda: cleanup) as scratch:
            pool = cpu_pool
            if lease is not None:
                pool = resources.enter_context(api.CpuPool(capacity=len(lease.cpus))).endpoint.as_dict()
            objects = DirectoryStore(scratch / "objects")
            workdir = scratch / "execution"
            workdir.mkdir()
            cleanup = CleanupState.UNKNOWN
            result = (compare_sources if executor is None else executor)(
                comparison, objects=objects, workdir=workdir,
                **({"cpu_pool": pool} if pool is not None else {}))
            cleanup = result.cleanup
            if lease is not None:
                lease.cleanup = cleanup
            workspace.store.publish(objects, result.closure)
            record = workspace.store.put(canonical_json({"inputs": comparison.as_dict(),
                                                         "result": result.as_dict()}))
            evidence = tuple(dict.fromkeys((record, *source_evidence, *result.closure)))
            if not cleanup.quiescent:
                raise ToolUnavailable("comparison-cleanup-unconfirmed", artifacts=evidence)
        content, files = comparison_feedback(result, workspace.store, prefix=f"comparison/{record.sha256}",
                                             diagnostics_visible=inputs.diagnostics_visible)
        content["reference_source"] = inputs.source
        for file in files:
            workspace.write(file.reference.path, file.data)
        return ToolResult(content, evidence)

    properties = {"rtl": source_selection_schema()}
    if inputs.reference_behavior is None:
        properties["behavior"] = source_selection_schema()
    return Tool("compare_rtl", render("shared/tools/sim/compare_rtl", reference=inputs.description(),
                timeout_seconds=str(JudgePolicy.from_bytes(inputs.judge_policy).score_timeout_seconds)),
                object_schema(properties), execute,
                "development-comparison/4:" + BlobRef.of(canonical_json(inputs.as_dict())).sha256)
