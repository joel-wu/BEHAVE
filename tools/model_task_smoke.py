"""Synthetic live-model rollout acceptance, not a dataset or an optimizer run.

The existing solver prompt, process broker, clean judge and training exporter
are exercised unchanged. The private fixture oracle is never a solver input.
"""

from pathlib import Path

SMOKE_CONFIG = "infra/model_runtimes/serving-smoke.json"


def smoke_config(path: Path | None = None) -> dict:
    """Test-only settings; never imported by production serving/eval/train."""
    from behave.core.file_input import read_regular_file
    from behave.core.records import parse_json

    config = parse_json(read_regular_file(
        path if path is not None else Path(__file__).resolve().parents[1] / SMOKE_CONFIG))
    for name in ("cpus", "schedule_wait_seconds", "preparation_seconds", "archive_seconds"):
        value = config["job"][name]
        if type(value) is not int or value <= 0:
            raise ValueError(f"smoke job {name} must be a positive integer")
    return config


def fixture(objects, *, mask: int, task_id: str = "native-smoke-xor"):
    from behave.core.artifacts import FileRef, SourceArtifact
    from behave.core.records import canonical_json
    from behave.data.published import ReleaseManifest, TaskManifest, TaskRevision, load_published_task
    from behave.sim import api
    from behave.task_execution.judge_policy import JudgePolicy

    if type(mask) is not int or not 1 <= mask <= 255:
        raise ValueError("the smoke fixture requires a nonzero eight-bit XOR mask")
    byte = api.FiniteType(8)
    contract = api.TaskContract([
        api.Endpoint("in", api.Direction.IN, [api.Field("a", byte, "in_a")],
                     api.ready_valid("in", "in_valid", "in_ready"), "clk", kind=api.Kind.TRANSACTION),
        api.Endpoint("out", api.Direction.OUT, [api.Field("y", byte, "out_y")],
                     api.ready_valid("out", "out_valid", "out_ready"), "clk", kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1),
    ], [api.ClockDomain("clk", "clk", "rst", reset_hold_cycles=3)])
    policy = JudgePolicy(api.COMMAND_VERSION, api.RESULT_VERSION, max_retries=0,
        baseline_cases=4, sequence_length=1, search_seconds=0,
        # Admission/elaboration, compilation and replay have separate tool
        # deadlines. The smoke's outer budget must cover their complete chain,
        # including isolated runtime preparation, not just one tool invocation.
        score_timeout_seconds=240)

    def file(path, data):
        return FileRef(path, objects.put(data))

    spec = (
        "# Synthetic infrastructure check: eight-bit XOR\n"
        f"For every accepted input a, emit one y = a XOR {mask}, both unsigned eight-bit values. "
        "There is no persistent arithmetic state.\n"
        "Submit a single Python file defining class M, reset(self), and process(self, a), "
        "returning a dictionary with key y. Submit a single SystemVerilog file defining module M "
        "with input clk, rst, in_valid, in_a[7:0], out_ready and output in_ready, out_valid, out_y[7:0]. "
        "Active-high rst suppresses transfers. Input and output use ready/valid; hold a pending output "
        "stable while stalled. A combinational implementation is permitted. See contract.json.\n"
        "The two entry symbols must both be M. No testbench is required. Finish with the submit tool; "
        "do not merely describe your solution. This is a smoke fixture, not a benchmark task.\n"
    ).encode()
    oracle = ("class Oracle:\n    def reset(self): pass\n"
              f"    def process(self, in_a): return {{'out_y': in_a ^ {mask}}}\n").encode()
    task = TaskManifest(task_id, file("spec.md", spec),
        file("contract.json", canonical_json(contract.as_dict())),
        # The current isolated-reference API explicitly requires this entry path.
        SourceArtifact((file("behavior.py", oracle),), "behavior.py", "Oracle"),
        objects.put(canonical_json(api.GenerationSpec(5, 1, 3, 1).as_dict())), objects.put(policy.to_bytes()),
        metadata={"fixture_only": True, "not_dataset_approval": True})
    revision = TaskRevision(task.task_id, objects.put(task.to_bytes()))
    release = ReleaseManifest("native-smoke-fixture", (revision,),
        objects.put(canonical_json({"fixture_only": True})), {"fixture_only": True})
    return load_published_task(objects, objects.put(release.to_bytes()), task.task_id).public


def fixture_group(objects, *, masks: tuple[int, ...]):
    """Publish distinct XOR tasks under one fixture-only training release."""
    from behave.core.records import canonical_json
    from behave.data.published import ReleaseManifest, TaskRevision, load_published_task

    if not masks or len(set(masks)) != len(masks):
        raise ValueError("fixture group needs distinct masks")
    tasks = tuple(fixture(objects, mask=mask, task_id=f"native-smoke-xor-{mask}") for mask in masks)
    release = ReleaseManifest("native-smoke-group-fixture", tuple(TaskRevision(t.task_id, t.revision) for t in tasks),
        objects.put(canonical_json({"fixture_only": True})), {"fixture_only": True})
    reference = objects.put(release.to_bytes())
    return tuple(load_published_task(objects, reference, t.task_id).public for t in tasks)


def scored_rollout(endpoint, records: Path, *, mask: int, settings: dict | None = None):
    from behave.agent.admission import AdmissionPolicy
    from behave.agent.attempt import AttemptSpec
    from behave.agent.run_store import JournalDirectory
    from behave.core.artifacts import BlobRef
    from behave.core.lifecycle import CleanupState
    from behave.core.records import canonical_json
    from behave.core.storage import DirectoryStore
    from behave.serving.lifecycle import ServingFailure
    from behave.task_execution.batch_plan import BatchPlan
    from behave.task_execution.recovery import task_cleanup
    from behave.task_execution.run_store import RunStore
    from behave.task_execution.simulator_judge import SimulatorJudge
    from behave.task_execution.solver import SolverJob
    from behave.train.learning_report import load_training_report
    from behave.train.plan import TrainingManifest, TrainingPlan
    from behave.train.rollouts import run_rollouts
    from behave.train.slime_rollout import sample_payloads
    from behave.train.training_data import NativeChatVerifier, TrainingRequirements, load_candidate, prepare_examples

    if endpoint.tokens is None:
        raise ValueError("scored acceptance requires an explicitly token-enabled endpoint")
    settings = smoke_config() if settings is None else settings
    objects = DirectoryStore(records / "inputs")
    task = fixture(objects, mask=mask)
    attempt = AttemptSpec("native-scored-rollout", "solver", endpoint.model_policy(), **settings["scored_attempt"])
    job = SolverJob(task, attempt, endpoint.command())
    execution = BatchPlan("native-smoke-rollouts", (job,), SimulatorJudge.identity, 1,
        AdmissionPolicy("smoke-verification", 1), model_admission=AdmissionPolicy("smoke-generation", 1))
    manifest = objects.put(TrainingManifest(task.release, ((task.task_id, task.revision),)).to_bytes())
    config = endpoint.tokens
    requirements = TrainingRequirements(config.adapter, config.checkpoint, config.tokenizer, config.template,
                                        required_signals=("old_log_probs",))
    plan = TrainingPlan(execution, manifest, requirements, (None,))
    verifier = NativeChatVerifier(endpoint)
    run = JournalDirectory(records / "run")
    candidate_run = RunStore(run.directory / "execution" / "candidates" / BlobRef.of(job.to_bytes()).sha256)
    try:
        result = run_rollouts(plan=plan, approved_manifest=manifest, reader=objects, run=run,
                              judge=SimulatorJudge(), verifier=verifier)
    finally:
        # A completed report or a stopped model server is not a worker cleanup
        # witness. Read the existing lifetime-owner journals, including failures.
        if candidate_run.directory.exists():
            try:
                with candidate_run.owner():
                    cleanup = CleanupState.combine(*(phase.state for phase in task_cleanup(candidate_run)))
            except Exception as error:  # Unreadable ownership cannot release scratch.
                raise ServingFailure("scored-rollout-cleanup-unknown", cleanup_state=CleanupState.UNKNOWN) from error
            if not cleanup.quiescent:
                raise ServingFailure("scored-rollout-cleanup-" + cleanup.value, cleanup_state=cleanup)
    head, closure = run.seal("training")
    archive = DirectoryStore(records / "exported")
    archive.publish(run.objects, closure)
    verified = load_training_report(archive, head, approved_manifest=manifest, verifier=verifier)

    class NoJudge:
        identity = SimulatorJudge.identity

        def evaluate(self, *args, **kwargs):
            raise AssertionError("cold training replay dispatched the judge")

    before = run.journal("training").load()
    cold = run_rollouts(plan=plan, approved_manifest=manifest, reader=archive, run=run,
                        judge=NoJudge(), verifier=verifier)
    replayed = cold == verified == result and before == run.journal("training").load()
    candidate, = result["candidates"]
    row = candidate["examples"]
    completed = None if row is None else load_candidate(archive, BlobRef.from_dict(row["result"])).result
    samples = ()
    if row is not None and row["reward"] is not None:
        examples = prepare_examples(archive, BlobRef.from_dict(row["result"]), requirements, verifier)
        samples = sample_payloads(examples, rollout_id=0, first_index=0)
    for name, value in (("training-head.json", head.as_dict()), ("endpoint.json", endpoint.as_dict()),
                        ("samples.json", {"schema": "behave.slime-smoke-payloads/1", "samples": list(samples)}),
                        ("report.json", result)):
        (records / name).write_bytes(canonical_json(value))
    (records / "approved-manifest.json").write_bytes(objects.read(manifest))
    (records / "plan.json").write_bytes(plan.to_bytes())
    # A bad generated solution remains a failed smoke with its real score. It
    # is not retried, repaired by the harness, or silently dropped from the run.
    return {"schema": "behave.scored-native-smoke/1", "training_head": head.as_dict(),
        "mask": mask, "reward": None if row is None else row["reward"],
        "error": candidate["error"],
        "judgment": None if completed is None or completed.judgment is None else completed.judgment.as_dict(),
        "turns": 0 if row is None else len(row["turns"]), "eligible_turns": result["eligible_turns"],
        "sample_payloads": len(samples), "cold_replay": replayed, "billing": result["billing"],
        "passed": replayed and row is not None and row["reward"] == 1
                  and len(samples) == len(row["turns"]) >= 2}


def verify_native_samples(records: Path) -> dict:
    """Read a sealed export in the selected Slime environment; never optimize."""
    from hashlib import sha256
    from inspect import getfile

    import torch
    from slime.utils.types import Sample

    from behave.core.artifacts import BlobRef
    from behave.core.records import parse_json
    from behave.core.storage import DirectoryStore
    from behave.train.learning_report import load_training_report
    from behave.train.plan import TrainingPlan
    from behave.train.slime_rollout import make_samples, sample_payloads
    from behave.train.training_data import NativeChatVerifier, prepare_examples

    if torch.cuda.is_initialized():
        raise RuntimeError("native sample verification must not initialize CUDA")
    archive = DirectoryStore(records / "exported")
    head = BlobRef.from_dict(parse_json((records / "training-head.json").read_bytes()))
    manifest = BlobRef.of((records / "approved-manifest.json").read_bytes())
    verifier = NativeChatVerifier.from_file(records / "endpoint.json")
    report = load_training_report(archive, head, approved_manifest=manifest, verifier=verifier)
    plan = TrainingPlan.from_bytes(archive.read(BlobRef.from_dict(report["plan"])), archive)
    row, = report["candidates"]
    if row["examples"] is None:
        raise ValueError("native sample smoke requires a completed scored candidate")
    examples = prepare_examples(archive, BlobRef.from_dict(row["examples"]["result"]), plan.requirements, verifier)
    payloads = sample_payloads(examples, rollout_id=0, first_index=0)
    samples = make_samples(examples, rollout_id=0, first_index=0)
    if len(samples) < 2 or len(samples) != len(payloads):
        raise ValueError("native sample smoke requires multiple eligible turns")
    for sample, payload in zip(samples, payloads, strict=True):
        if (type(sample) is not Sample or sample.status != Sample.Status.COMPLETED
                or any(getattr(sample, key) != value for key, value in payload.items())
                or Sample.from_dict(sample.to_dict()).to_dict() != sample.to_dict()):
            raise ValueError("native Sample materialization/serialization changed training fields")
    if torch.cuda.is_initialized():
        raise RuntimeError("native sample construction unexpectedly initialized CUDA")
    source = Path(getfile(Sample))
    return {"schema": "behave.native-slime-samples/1", "passed": True, "samples": len(samples),
            "torch": torch.__version__, "sample_source": str(source),
            "sample_source_sha256": sha256(source.read_bytes()).hexdigest(),
            "cuda_initialized": False, "optimizer_executed": False}
