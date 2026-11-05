"""Detached local-model tool smoke. No training, dataset evaluation or paid API.

Both server and client run inside one explicitly allocated Slurm step. Source
is frozen before launch and staged to node-local scratch; receipts are copied back afterward.
This CLI owns one session with explicit runtime budgets, not a public daemon.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from tempfile import mkdtemp

REPO = Path(__file__).resolve().parents[1]
BINDING = "infra/model_runtimes/local-serving.json"


def read_binding(path: Path, runtime: str, expected=None):
    """One operator binding for reservation and worker provisioning.

    The ServingSpec remains the launch-policy authority. A supplied reference
    authenticates submitted bytes before normalization or any native inspection.
    """
    from behave.core.artifacts import BlobRef
    from behave.core.file_input import read_regular_file
    from behave.core.records import canonical_json, parse_json
    from behave.serving.spec import bound_spec

    data = read_regular_file(path)
    if expected is not None and BlobRef.of(data) != expected:
        raise ValueError("serving binding changed after submission")
    binding = parse_json(data)
    spec = bound_spec(binding, runtime)
    return binding, spec, canonical_json(binding)


def progress(phase: str, **details) -> None:
    """Small coordinator events, not server output or a successful result."""
    print(json.dumps({"schema": "behave.model-service-progress/1", "phase": phase, **details}), flush=True)


def submit(job_id: int, runtime: str, *, native_tokens: bool = False, task_rollout: bool = False,
           weights: Path | None = None, binding: Path | None = None,
           smoke_settings: Path | None = None) -> dict:
    from behave.runtime.jobs import submit as detached_submit
    from behave.runtime.snapshots import freeze

    if runtime not in {"sglang", "vllm"} or type(job_id) is not int or job_id < 1:
        raise ValueError("positive allocation and explicit standalone runtime required")
    from behave.core.paths import work_root

    output = work_root() / ("model_service_" + uuid.uuid4().hex[:12])
    snapshot = output / "snapshot"
    native_tokens = native_tokens or task_rollout
    record = freeze(REPO, snapshot, extra_files=("tools/model_service_job.py", "tools/model_task_smoke.py", BINDING))
    # Freeze the selected operator declaration as well as the code. The default
    # is the snapshotted repository binding, never a later edit to the checkout.
    from behave.core.artifacts import BlobRef
    from tools.model_task_smoke import SMOKE_CONFIG, smoke_config

    settings = smoke_config(smoke_settings if smoke_settings is not None else snapshot / SMOKE_CONFIG)
    job = settings["job"]
    _, spec, binding_data = read_binding(binding if binding is not None else snapshot / BINDING, runtime)
    binding_ref = BlobRef.of(binding_data)
    with (output / "binding.json").open("xb") as stream:
        stream.write(binding_data)
    (output / "binding.json").chmod(0o444)

    weight_ref = None
    if weights is not None:
        from tools.model_weight_input import WeightInput

        data = WeightInput.read(weights).to_bytes()
        weight_ref = BlobRef.of(data)
        with (output / "weights.json").open("xb") as stream:
            stream.write(data)
        (output / "weights.json").chmod(0o444)
    budgets = (job["schedule_wait_seconds"], job["preparation_seconds"], spec.lifetime_seconds,
               spec.cleanup_seconds, job["archive_seconds"])
    wall = None if None in budgets else sum(budgets)
    run = output / "run"
    argv = ["srun", f"--jobid={job_id}", "--exact", f"--immediate={job['schedule_wait_seconds']}",
            "--nodes=1", "--ntasks=1", f"--cpus-per-task={job['cpus']}",
            f"--gpus-per-task={spec.tensor_parallel}", "--kill-on-bad-exit=1",
            *([] if wall is None else [f"--time={(wall + 59) // 60}"]), sys.executable, "-I", "-B",
            str(snapshot / "tools/model_service_job.py"), "_run", str(snapshot), str(run / "evidence"), runtime,
            "--binding-input", str(output / "binding.json"), "--binding-sha256", binding_ref.sha256,
            "--binding-size", str(binding_ref.size_bytes),
            "--smoke-settings", json.dumps(settings),
            *(["--native-tokens"] if native_tokens else []), *(["--task-rollout"] if task_rollout else []),
            *(["--weight-input", str(output / "weights.json"), "--weight-sha256", weight_ref.sha256,
               "--weight-size", str(weight_ref.size_bytes)] if weight_ref is not None else [])]
    return detached_submit(run, argv, cwd=REPO, wall_seconds=wall, metadata={
        "purpose": "local model tool smoke", "allocation": job_id, "runtime": runtime,
        "snapshot": record["sha256"], "serving": spec.identity, "gpus": spec.tensor_parallel,
        "binding": binding_ref.as_dict(),
        "smoke_settings": settings,
        "native_tokens": native_tokens, "task_rollout": task_rollout,
        "weight_input": None if weight_ref is None else weight_ref.as_dict()})


def native_endpoint(endpoint, spec, objects, *, checkpoint=None):
    """Pin tokenizer/template bytes and a verified publication or cached revision.

    The launch spec is an operator checkpoint declaration, not a digest of all
    weight shards. No tokenizer execution or post-hoc token reconstruction occurs.
    """
    from behave.agent.providers.native_tokens import NativeChatTokens
    from behave.core.file_input import read_regular_file
    from behave.core.records import canonical_json, parse_json, thaw_json

    files = {}
    names = ["tokenizer.json", "tokenizer_config.json"]
    if os.path.lexists(Path(spec.model_path) / "chat_template.jinja"):
        names.append("chat_template.jinja")
    for name in names:
        data = read_regular_file(Path(spec.model_path) / name)
        files[name] = objects.put(data)
    config = parse_json(objects.read(files["tokenizer_config.json"]))
    if not config.get("chat_template") and "chat_template.jinja" not in files:
        raise ValueError("native smoke requires the configured model chat template")
    if checkpoint is None:
        checkpoint = objects.put(canonical_json(spec.as_dict()))
    tokenizer = objects.put(canonical_json({"schema": "behave.smoke-tokenizer/1",
        "files": {name: ref.as_dict() for name, ref in files.items()}}))
    template = objects.put(canonical_json({"schema": "behave.smoke-chat-template/1",
        "chat_template": config.get("chat_template"),
        "template_file": None if "chat_template.jinja" not in files else files["chat_template.jinja"].as_dict(),
        "kwargs": thaw_json(endpoint.parameters).get("chat_template_kwargs", {}),
        "runtime": spec.runtime, "package_version": spec.package_version,
        "tool_parser": spec.tool_parser, "reasoning_parser": spec.reasoning_parser}))
    return replace(endpoint, tokens=NativeChatTokens(spec.runtime, checkpoint, tokenizer, template))


def native_evidence(endpoint, journal, view) -> dict:
    from behave.agent.model import ModelRequest
    from behave.core.records import parse_json, thaw_json
    from behave.train.training_data import NativeChatVerifier, TokenTrace

    verifier = NativeChatVerifier(endpoint)
    turns = []
    for turn in view.turns:
        if turn.exclusion is not None or turn.token_data is None:
            raise ValueError("native smoke requires complete token-bearing turns")
        trace = TokenTrace.from_dict(thaw_json(turn.token_data))
        request = ModelRequest.from_dict(parse_json(journal.objects.read(turn.request)))
        verifier.verify(trace, request, journal.objects.read(turn.raw_response))
        if "old_log_probs" not in trace.signals or not any(trace.response_mask):
            raise ValueError("native smoke lacks exact trainable response probabilities")
        turns.append({"turn": turn.turn, "request": turn.request.as_dict(),
                      "input_tokens": len(trace.input_ids), "response_tokens": len(trace.response_ids),
                      "old_log_probs": len(trace.signals["old_log_probs"]), "verified": True})
    if len(turns) < 2:
        raise ValueError("native smoke did not exercise multi-turn generation")
    return {"schema": "behave.native-token-smoke/1", "adapter": verifier.identity, "turns": turns}


def tool_smoke(endpoint, records: Path, *, native_spec=None, checkpoint=None,
               settings: dict | None = None) -> dict:
    from model_task_smoke import smoke_config

    from behave.agent import AttemptJournal, AttemptSpec, run_attempt
    from behave.agent.process_model import ProcessModelBackend
    from behave.agent.prompting import render
    from behave.agent.tools import file_tools
    from behave.agent.trajectory import trajectory
    from behave.agent.workspace import Workspace
    from behave.core.artifacts import BlobRef, FileData, FileRef
    from behave.core.storage import DirectoryStore
    from behave.serving.lifecycle import ServingFailure

    settings = smoke_config() if settings is None else settings
    journal = AttemptJournal(records / "events", DirectoryStore(records / "objects"))
    if native_spec is not None:
        endpoint = native_endpoint(endpoint, native_spec, journal.objects, checkpoint=checkpoint)
    nonce = uuid.uuid4().hex[:12]
    backend = ProcessModelBackend(endpoint.command())
    kwargs = {"journal": journal, "backend": backend,
              "spec": AttemptSpec("serving-smoke", "tool-smoke", endpoint.model_policy(),
                                   **settings["tool_attempt"], read_only_paths=("input.txt",)),
              "initial_files": (FileData(FileRef("input.txt", BlobRef.of(nonce.encode())), nonce.encode()),),
              "tools": file_tools(),
              "initial_messages": ({"role": "user", "content":
                      render("fixtures/serving_smoke")},)}
    try:
        result = run_attempt(**kwargs)
    finally:
        if not backend.cleanup_state.quiescent:
            raise ServingFailure("tool-smoke-cleanup-" + backend.cleanup_state.value,
                                 cleanup_state=backend.cleanup_state)
    workspace = Workspace(journal.objects)
    workspace.restore(result.workspace)
    correct = any(f.path == "output.txt" for f in workspace.snapshot().files)
    correct = correct and workspace.read("output.txt") == (nonce + ":checked").encode()
    view = trajectory(journal)
    events_before = journal.load()
    # Cold replay must consume the closed journal, not dispatch again.
    class NoDispatch:
        def generate(self, request):
            raise AssertionError("closed attempt dispatched during replay")
    replayed = run_attempt(**{**kwargs, "backend": NoDispatch()})
    replay_ok = result == replayed and journal.load() == events_before
    calls = [event.payload["call"]["name"] for event in events_before if event.kind == "tool_requested"]
    token_report = None if endpoint.tokens is None else native_evidence(endpoint, journal, view)
    return {"schema": "behave.model-tool-smoke/1", "endpoint": endpoint.as_dict(), "reason": result.reason,
            "turns": result.turns, "tool_calls": calls, "output_matches": correct, "cold_replay": replay_ok,
            "unpriced_dispatches": view.unpriced_dispatches, "unresolved_dispatches": view.unresolved_dispatches,
            "native_tokens": token_report,
            "passed": correct and replay_ok and "read_file" in calls and "write_file" in calls
                      and result.reason == "stopped" and view.unresolved_dispatches == 0}


def sampling_smoke(endpoint, records: Path, settings: dict) -> dict:
    """Measure repeated request seeds through the real broker, without tool actions."""
    from behave.agent.model import ModelReply, ModelRequest
    from behave.agent.process_model import ProcessModelBackend, encode_model_outcome
    from behave.agent.prompting import render
    from behave.core.records import canonical_json
    from behave.serving.lifecycle import ServingFailure

    check = settings["sampling_check"]
    records.mkdir(parents=True)
    samples, by_seed = [], {}
    for index, seed in enumerate(check["seeds"]):
        selected = replace(endpoint, parameters={**check["parameters"], "seed": seed})
        request = ModelRequest(f"sampling-{index}", 0, 0,
            ({"role": "user", "content": render("fixtures/sampling_smoke")},), (),
            selected.model_policy(), settings["tool_attempt"]["request_timeout_seconds"])
        backend = ProcessModelBackend(selected.command())
        began = time.monotonic()
        try:
            result = backend.generate(request)
        finally:
            if not backend.cleanup_state.quiescent:
                raise ServingFailure("sampling-smoke-cleanup-" + backend.cleanup_state.value,
                                     cleanup_state=backend.cleanup_state)
        (records / f"{index}.request.json").write_bytes(canonical_json(request.as_dict()))
        (records / f"{index}.outcome.json").write_bytes(encode_model_outcome(request, result))
        ok = isinstance(result, ModelReply) and not result.calls and bool(result.content)
        if ok:
            by_seed.setdefault(seed, []).append(canonical_json(result.message()))
        samples.append({"seed": seed, "reply": ok, "elapsed_seconds": time.monotonic() - began,
                        "result": result.as_dict()})
        if not ok:
            break
    repeated = [outputs for outputs in by_seed.values() if len(outputs) > 1]
    reproducible = bool(repeated) and all(len(set(outputs)) == 1 for outputs in repeated)
    return {"samples": samples, "repeated_seed_equal": reproducible,
            "distinct_outputs": len({output for outputs in by_seed.values() for output in outputs}),
            "passed": bool(samples) and all(sample["reply"] for sample in samples) and reproducible}


def run(snapshot: Path, output: Path, runtime: str, *, native_tokens: bool = False, task_rollout: bool = False,
        weight_input: Path | None = None, weight_reference=None,
        binding_input: Path | None = None, binding_reference=None, settings: dict | None = None) -> int:
    if (weight_input is None) != (weight_reference is None):
        raise ValueError("weight input requires its pinned submission reference")
    if (binding_input is None) != (binding_reference is None):
        raise ValueError("binding input requires its pinned submission reference")
    from behave.core.paths import scratch_root
    from behave.runtime.records import write_json
    from behave.runtime.snapshots import source_command, stage

    scratch = Path(mkdtemp(prefix="behave-serving-", dir=scratch_root()))
    delegated = False
    try:
        source = scratch / "checkout"
        progress("staging", runtime=runtime)
        frozen = stage(snapshot, source)
        entry = scratch / "worker.json"
        write_json(entry, {
            "frozen": {"sha256": frozen["sha256"]}, "output": str(output), "runtime": runtime,
            "native_tokens": native_tokens, "task_rollout": task_rollout, "settings": settings,
            "weight_input": None if weight_input is None else str(weight_input),
            "weight_reference": weight_reference,
            "binding_input": None if binding_input is None else str(binding_input),
            "binding_reference": binding_reference,
        })
        command = source_command(source,
            "import json; from tools.model_service_job import _run_local; "
            "options=json.loads(Path(sys.argv[1]).read_text()); "
            "raise SystemExit(_run_local(source,source.parent,**options))", entry)
        # From this point only the worker knows whether native processes are
        # quiescent. The wrapper must not delete its inputs on interruption.
        delegated = True
        return subprocess.run(command, check=False).returncode
    finally:
        if not delegated:
            shutil.rmtree(scratch)
        elif scratch.exists():
            progress("worker-scratch-retained", path=str(scratch), destination=str(output))


def _run_local(source: Path, scratch: Path, frozen: dict, output: Path, runtime: str, *,
               native_tokens: bool = False, task_rollout: bool = False,
               weight_input: Path | None = None, weight_reference=None,
               binding_input: Path | None = None, binding_reference=None, settings: dict | None = None) -> int:
    """Own native processes, archive receipts, then release only quiescent scratch."""
    from model_task_smoke import smoke_config

    output = Path(output)
    weight_input = None if weight_input is None else Path(weight_input)
    binding_input = None if binding_input is None else Path(binding_input)
    session, staged_weights = None, None
    evidence, archived = None, False
    cleanup = None  # No native process exists before provisioning below.
    report = {"schema": "behave.model-service-check/2", "runtime": runtime,
              "passed": False, "cleanup_complete": None}
    try:
        from behave.core.artifacts import BlobRef
        from behave.core.lifecycle import CleanupState
        from behave.core.records import canonical_json
        from behave.serving import ServingSession
        from behave.serving.environment import InstallationMismatch, environment, headers, inspect_installation
        from behave.serving.lifecycle import ServingFailure

        cleanup = CleanupState.COMPLETE
        evidence = scratch / "evidence"
        evidence.mkdir()
        report["snapshot"] = frozen["sha256"]
        began = time.monotonic()
        phase = "binding"
        try:
            expected = None if binding_reference is None else BlobRef.from_dict(binding_reference)
            binding, spec, binding_data = read_binding(
                source / BINDING if binding_input is None else binding_input, runtime, expected)
            (evidence / "binding.json").write_bytes(binding_data)
            report["binding"] = BlobRef.of(binding_data).as_dict()
            report["serving"] = spec.identity
            phase = "smoke-settings"
            settings = smoke_config() if settings is None else settings
            report["smoke_settings"] = settings
            preparation_deadline = began + settings["job"]["preparation_seconds"]
            checkpoint = None
            if weight_input is not None:
                from model_weight_input import WeightInput

                phase = "weight-staging"
                progress(phase)
                weight_reference = BlobRef.from_dict(weight_reference)
                selected = WeightInput.read(weight_input, weight_reference)
                spec = selected.stage(spec)
                staged_weights = Path(spec.model_path)
                checkpoint = selected.publication
                report["weights"] = {"input": weight_reference.as_dict(), "publication": checkpoint.as_dict(),
                                     "checkpoint": selected.checkpoint.as_dict(), "component": selected.component,
                                     "serving": spec.identity}
            env = environment(spec, scratch, os.environ)
            phase = "installation"
            progress(phase, runtime=runtime, snapshot=frozen["sha256"])
            report["installation"] = inspect_installation(spec, scratch, env, deadline=preparation_deadline)
            phase = "headers"
            progress(phase)
            includes = headers(binding["headers"], scratch, env, deadline=preparation_deadline)
            env = environment(spec, scratch, os.environ, includes=includes)
            session = ServingSession(spec, scratch=scratch, environment=env, records=evidence / "service")
            phase = "starting"
            progress(phase, startup_seconds=spec.startup_seconds, lifetime_seconds=spec.lifetime_seconds)
            endpoint = session.start()
            if "sampling_check" in settings:
                phase = "sampling-check"
                progress(phase, elapsed_seconds=time.monotonic() - began)
                report["sampling"] = sampling_smoke(endpoint, evidence / "sampling", settings)
                session.check_alive()
                if not report["sampling"]["passed"]:
                    raise ServingFailure("sampling-check-failed")
            phase = "tool-smoke"
            progress(phase, elapsed_seconds=time.monotonic() - began)
            tool_endpoint = replace(endpoint, parameters=settings["tool_parameters"])
            report["smoke"] = tool_smoke(tool_endpoint, evidence / "attempt",
                native_spec=spec if native_tokens else None, checkpoint=checkpoint, settings=settings)
            session.check_alive()
            report["passed"] = report["smoke"]["passed"]
            if task_rollout and report["passed"]:
                from model_task_smoke import scored_rollout

                from behave.core.storage import DirectoryStore

                phase = "scored-rollout"
                progress(phase, elapsed_seconds=time.monotonic() - began)
                endpoint = replace(endpoint, parameters=settings["scored_parameters"])
                endpoint = native_endpoint(endpoint, spec, DirectoryStore(evidence / "provenance"), checkpoint=checkpoint)
                report["rollout"] = scored_rollout(endpoint, evidence / "rollout",
                    mask=int(uuid.uuid4().hex[:2], 16) or 1, settings=settings)
                session.check_alive()
                report["passed"] &= report["rollout"]["passed"]
        except Exception as error:  # noqa: BLE001 -- job boundary persists failure evidence before cleanup
            # No exception payload (which might contain provider/body data) in
            # operational status. Detailed nonsecret service logs are separate.
            report["passed"] = False
            report["failure"] = getattr(error, "code", type(error).__name__)
            report["failure_phase"] = phase
            if isinstance(error, ServingFailure):
                cleanup = CleanupState.combine(cleanup, error.cleanup_state)
            if isinstance(error, InstallationMismatch):
                # Only the two validated public version fields, never raw
                # exception payloads, environment variables or service output.
                report["installation_mismatch"] = {"expected": error.expected, "observed": error.observed}
        finally:
            if session is not None:
                progress("stopping")
                try:
                    stopped = session.close()
                    stopped_cleanup = (CleanupState.COMPLETE if stopped["cleanup_complete"] is True
                                       else CleanupState(stopped.get("cleanup_state", "unknown")))
                    cleanup = CleanupState.combine(cleanup, stopped_cleanup)
                    report["passed"] &= stopped["cleanup_complete"] and stopped["service_status"] == "cancelled"
                except Exception as error:  # noqa: BLE001 -- cleanup failure must invalidate acceptance
                    cleanup = CleanupState.combine(cleanup, CleanupState.UNKNOWN,
                        error.cleanup_state if isinstance(error, ServingFailure) else CleanupState.UNKNOWN)
                    report.update(passed=False,
                                  cleanup_failure=getattr(error, "code", type(error).__name__))
            report["cleanup_complete"] = cleanup.quiescent
            report["cleanup_state"] = cleanup.value
            report["passed"] &= cleanup.quiescent
            report["elapsed_seconds"] = time.monotonic() - began
            if not cleanup.quiescent:
                report["retained_scratch"] = str(scratch)
                if staged_weights is not None:
                    report["retained_weights"] = str(staged_weights)
            (evidence / "result.json").write_bytes(canonical_json(report) + b"\n")
            # Never copy the server's caches or the large model. Evidence was
            # created only by this coordinator, not by the candidate workspace.
            shutil.copytree(evidence, output)
            archived = True
        progress("finished", passed=report["passed"], elapsed_seconds=report["elapsed_seconds"])
        print(json.dumps(report, indent=2), flush=True)
        return 0 if report["passed"] else 1
    finally:
        # Never delete files out from under a possibly live model process.
        # These paths were created by this invocation, not supplied by a user.
        if (cleanup is None or cleanup.quiescent) and (session is None or report["cleanup_complete"] is True):
            if staged_weights is not None:
                shutil.rmtree(staged_weights)
            if evidence is None or archived:
                shutil.rmtree(scratch)
        # A completed process is not evidence of a completed archive. Keep the
        # only local receipt/logs on destination failure, and expose their path.
        if evidence is not None and not archived:
            progress("evidence-retained", path=str(evidence), destination=str(output))


if __name__ == "__main__":
    sys.path[:0] = [str(REPO), str(REPO / "src"), str(REPO / "tools")]
    if len(sys.argv) >= 5 and sys.argv[1] == "_run":
        internal = argparse.ArgumentParser()
        internal.add_argument("--native-tokens", action="store_true")
        internal.add_argument("--task-rollout", action="store_true")
        internal.add_argument("--weight-input", type=Path)
        internal.add_argument("--weight-sha256")
        internal.add_argument("--weight-size", type=int)
        internal.add_argument("--binding-input", type=Path)
        internal.add_argument("--binding-sha256")
        internal.add_argument("--binding-size", type=int)
        internal.add_argument("--smoke-settings", type=json.loads)
        options = internal.parse_args(sys.argv[5:])
        weight_ref = None
        if options.weight_input is not None:
            weight_ref = {"sha256": options.weight_sha256, "size_bytes": options.weight_size}
        elif options.weight_sha256 is not None or options.weight_size is not None:
            internal.error("weight reference requires --weight-input")
        binding_ref = None
        if options.binding_input is not None:
            binding_ref = {"sha256": options.binding_sha256, "size_bytes": options.binding_size}
        elif options.binding_sha256 is not None or options.binding_size is not None:
            internal.error("binding reference requires --binding-input")
        raise SystemExit(run(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4],
                             native_tokens=options.native_tokens or options.task_rollout,
                             task_rollout=options.task_rollout,
                             weight_input=options.weight_input, weight_reference=weight_ref,
                             binding_input=options.binding_input, binding_reference=binding_ref,
                             settings=options.smoke_settings))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", type=int)
    parser.add_argument("runtime", choices=("sglang", "vllm"))
    parser.add_argument("--native-tokens", action="store_true", help="also verify exact native tokens/log-probs across turns")
    parser.add_argument("--task-rollout", action="store_true", help="also run a scored synthetic solver rollout and training export")
    parser.add_argument("--weights", type=Path, help="explicit committed-weight input JSON; no latest/cached-weight fallback")
    parser.add_argument("--binding", type=Path, help="explicit local-serving-bindings/3 JSON; frozen before GPU reservation")
    parser.add_argument("--smoke-settings", type=Path, help="test-only settings JSON; copied into the submitted job")
    args = parser.parse_args()
    print(json.dumps(submit(args.job_id, args.runtime, native_tokens=args.native_tokens, task_rollout=args.task_rollout,
                            weights=args.weights, binding=args.binding, smoke_settings=args.smoke_settings), indent=2))
