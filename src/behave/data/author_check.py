"""Construction-role Bash Sim checks, using the shared comparison executor."""

import base64
import os
import stat
import threading
from contextlib import ExitStack
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from time import monotonic

from behave.agent.cpu_resources import cpu_job
from behave.agent.lifetime import current_window
from behave.agent.prompting import render
from behave.agent.workspace import Workspace
from behave.core import runtime_defaults as defaults
from behave.core.artifacts import BlobRef, FileData, FileRef
from behave.core.lifecycle import CleanupState
from behave.core.records import RecordError, canonical_json, fields, parse_json
from behave.core.storage import DirectoryStore, install_exclusive
from behave.task_execution.comparison import ComparisonInputs, compare_sources
from behave.task_execution.feedback import comparison_feedback
from behave.task_execution.sim_tool import workspace_source
from behave.task_execution.task_declaration import check_task_declaration


def check_guidance(prefix=".construction/", *, template="synthesis/task_check"):
    from behave.sim import api

    documents = {"sim_check.md": render(template).encode(),
                 "behavior_environment.json": canonical_json(api.behavior_source_environment())}
    return tuple(FileData(FileRef(prefix + name, BlobRef.of(data)), data)
                 for name, data in documents.items())


def comparison_inputs(packet, judge_policy, objects):
    row = fields(packet, {"behavior", "files", "contract", "run_policy"}
                 | (set(packet) & {"reference_behavior", "rtl", "replay_stimuli", "spec"}))
    if "rtl" in row and "reference_behavior" in row:
        raise ValueError("select either B/B or RTL/Behavior comparison")
    workspace = Workspace(objects)
    sources = []
    for item in row["files"]:
        item = fields(item, {"path", "data"})
        data = base64.b64decode(item["data"], validate=True)
        sources.append(FileData(FileRef(item["path"], BlobRef.of(data)), data))
    workspace.seed(tuple(sources))
    behavior = workspace_source(workspace, row["behavior"])
    reference = (workspace_source(workspace, row["reference_behavior"])
                 if "reference_behavior" in row else behavior)
    source = workspace_source(workspace, row["rtl"]) if "rtl" in row else behavior
    contract = base64.b64decode(row["contract"], validate=True)
    policy = b"null" if row["run_policy"] is None else base64.b64decode(row["run_policy"], validate=True)
    stimuli = row.get("replay_stimuli")
    return ComparisonInputs("rtl" if "rtl" in row else "behavior", source, reference,
        FileData(FileRef("contract.json", BlobRef.of(contract)), contract), policy, judge_policy,
        reference_visible=True,
        replay_stimuli=None if stimuli is None else base64.b64decode(stimuli, validate=True))


class AuthorCheckRelay:
    """One private, file-only ingress; no socket or network grant to the shell."""

    def __init__(self, *, objects, judge_policy, deadline, cancelled, executor=compare_sources, cpu_pool=None,
                 declaration_checker=check_task_declaration):
        self.deadline, self.cancelled = deadline, cancelled
        self.objects, self.judge_policy, self.executor = objects, judge_policy, executor
        self.cpu_pool = cpu_pool
        self.declaration_checker = declaration_checker
        self.artifacts = []
        self.cleanup_state = self.child_cleanup = CleanupState.COMPLETE
        self._stop, self._error = threading.Event(), None

    def __enter__(self):
        from behave.isolation.scratch import ScratchDirectory

        self._scratch = ScratchDirectory(prefix="behave-author-mailbox-", cleanup=lambda: self.cleanup_state)
        self.endpoint = self._scratch.name
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join(timeout=defaults.LOCAL_RPC_CLEANUP_SECONDS)
        self.cleanup_state = CleanupState.combine(self.child_cleanup,
            CleanupState.INCOMPLETE if self._thread.is_alive() else CleanupState.COMPLETE)
        self.cleanup_state.require_quiescent("author-check")
        self._scratch.cleanup()
        if self._error is not None:
            raise self._error

    def _cancelled(self):
        return (self._stop.is_set() or self.cancelled()
                or (self.deadline is not None and monotonic() >= self.deadline))

    def _serve(self):
        seen = set()
        try:
            root = Path(self.endpoint)
            while not self._cancelled():
                for path in sorted(root.iterdir()):
                    if not path.name.endswith(".request.json") or path.name in seen:
                        continue
                    seen.add(path.name)
                    # The candidate owns mailbox entries, never host paths.
                    # Do not follow its symlinks or block on a special file.
                    try:
                        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    except OSError:
                        continue
                    with os.fdopen(fd, "rb") as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            continue
                        data = stream.read()
                    reply = self.respond(data, cancelled=self._cancelled)
                    name = path.name.removesuffix(".request.json") + ".response.json"
                    install_exclusive(root, name, reply)
                self._stop.wait(defaults.LOCAL_RPC_POLL_SECONDS)
        except BaseException as error:  # noqa: BLE001 -- marshal thread failure to the lifetime owner
            self._error = error

    def respond(self, data, *, cancelled):
        from behave.isolation.scratch import ScratchDirectory

        try:
            packet = parse_json(data)
            inputs = comparison_inputs(packet, self.judge_policy, self.objects)
            spec = None if "spec" not in packet else base64.b64decode(packet["spec"], validate=True)
        except (RecordError, ValueError, TypeError, KeyError) as error:
            return canonical_json({"reply": {"matched": None, "status": "invalid_request",
                "summary": str(error)}, "files": []})
        identity, refs = inputs.stage(self.objects)
        declaration = None
        if spec is not None:
            self.child_cleanup = CleanupState.UNKNOWN
            declaration = self.declaration_checker(spec, inputs.contract.data, objects=self.objects,
                cpu_pool=self.cpu_pool, deadline=self.deadline, cancelled=cancelled)
            self.child_cleanup = declaration.cleanup
            self.artifacts.extend(declaration.closure)
            if declaration.score != 1:
                record = self.objects.put(canonical_json({"inputs": identity.as_dict(),
                    "result": declaration.as_dict(), "purpose": "task-interface-check"}))
                self.artifacts.extend((*refs, record))
                return self.feedback(inputs, declaration, record, declaration_failed=True)
        cleanup = CleanupState.COMPLETE
        with ScratchDirectory(prefix="behave-author-check-", cleanup=lambda: cleanup) as scratch:
            objects = DirectoryStore(scratch / "objects")
            workdir = scratch / "execution"
            workdir.mkdir()
            cleanup = self.child_cleanup = CleanupState.UNKNOWN
            result = self.executor(inputs, objects=objects, workdir=workdir,
                cancelled=lambda: cancelled() or (self.deadline is not None and monotonic() >= self.deadline),
                **({"cpu_pool": self.cpu_pool} if self.cpu_pool is not None else {}))
            cleanup = self.child_cleanup = result.cleanup
            self.objects.publish(objects, result.closure)
            record = self.objects.put(canonical_json({"inputs": identity.as_dict(), "result": result.as_dict()}))
            self.artifacts.extend((*refs, record, *result.closure))
        return self.feedback(inputs, result, record, declaration_checked=declaration is not None)

    def feedback(self, inputs, result, record, *, declaration_failed=False, declaration_checked=False):
        reply, reports = comparison_feedback(result, self.objects, prefix=f"sim_check/{record.sha256}")
        if declaration_failed:
            reply.update(purpose="task-interface-check", independent_reference=False)
            note = ("# Task interface check\n\n"
                    "The specification's RTL declaration did not pass the contract check. "
                    "No Behavior comparison or RTL functional test was run. This is feedback "
                    "about the supplied task interface, not a solver score.\n\n")
        elif inputs.source == inputs.reference_behavior:
            reply.update(purpose="behavior-execution-check", independent_reference=False)
            note = ("# Behavior execution check\n\n"
                    "This runs the submitted Behavior against itself through BEHAVE-Sim. A match checks concrete execution "
                    "under the declared interface, not intended-function correctness, independent B1/B2 "
                    "agreement or exact lowering/bounded analysis.\n\n")
        else:
            reply.update(purpose="source-comparison", reference_source="workspace")
            note = ("# Development comparison\n\n"
                    "This compares the selected workspace implementations through BEHAVE-Sim. "
                    "Agreement is not proof of intended-function correctness. This additional check "
                    "does not replace the coordinator's comparison of the submitted artifacts.\n\n")
        if declaration_checked:
            reply["declared_interface"] = "consistent"
            note += ("The specification's RTL declaration also passed the contract interface check. "
                     "That structural check does not establish functional correctness.\n\n")
        output = []
        for file in reports:
            content = note.encode() + file.data if file.reference.path == reply["report"] else file.data
            self.artifacts.append(self.objects.put(content))
            output.append({"path": file.reference.path, "data": base64.b64encode(content).decode("ascii")})
        return canonical_json({"reply": reply, "files": output})


def behavior_check_shell(runner, judge_policy, *, executor=compare_sources):
    """Keep the existing run_command tool, adding a per-command public Sim client."""
    tool = runner.tool()
    script = files("behave.data").joinpath("author_check_client.py")

    def execute(workspace, arguments):
        from behave.agent.commands import CommandRunner
        from behave.isolation import read_only

        window = current_window()
        cancelled = (lambda: False) if window is None else window.control.cancelled
        # One CPU lease covers the Bash call and its Sim work. Never acquire a
        # second slot while the waiting shell still holds the first one.
        with cpu_job(runner.cpu_endpoint) as lease:
            deadline = monotonic() + runner.template.limits.wall_seconds
            if window is not None and window.monotonic_deadline is not None:
                deadline = min(deadline, window.monotonic_deadline)
            relay = None
            try:
                with ExitStack() as resources:
                    pool = None
                    if lease is not None:
                        from behave.sim import api
                        pool = resources.enter_context(api.CpuPool(capacity=len(lease.cpus))).endpoint.as_dict()
                    relay = resources.enter_context(AuthorCheckRelay(objects=workspace.store, judge_policy=judge_policy,
                        deadline=deadline, cancelled=cancelled, executor=executor, cpu_pool=pool))
                    base = runner.template
                    root = next(root for root in base.writable if root.target == "/workspace")
                    configured = replace(base,
                        environment=(*base.environment, ("BEHAVE_SIM_CHECK_POLL_SECONDS", str(defaults.LOCAL_RPC_POLL_SECONDS))),
                        writable=(*base.writable, replace(root, target="/sim-check", source=relay.endpoint)),
                        read_only=(*base.read_only, read_only(str(script), "/tools/behave_sim_check.py", reuse_attestation=True)))
                    # CommandRunner inherits the current lease and records the
                    # actual shell cleanup receipt, including nonzero exits.
                    result = CommandRunner(configured, writable_workspace=True,
                        output_preview_bytes=runner.output_preview_bytes).execute(workspace, arguments)
            finally:
                if lease is not None and relay is not None:
                    lease.cleanup = CleanupState.combine(lease.cleanup, relay.cleanup_state)
        return replace(result, artifacts=tuple(dict.fromkeys((*result.artifacts, *relay.artifacts))))

    return replace(tool, handler=execute,
                   revision="construction-sim-shell/3:" + tool.revision + ":" + BlobRef.of(judge_policy).sha256)
