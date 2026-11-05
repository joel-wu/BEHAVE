"""Execute external methods' own harnesses with the public isolated tool API."""

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from threading import Lock

from behave.agent.attempt import AttemptInterrupted
from behave.agent.cpu_resources import cpu_job
from behave.core.lifecycle import CleanupState
from behave.core.records import canonical_json
from behave.sim import api


class PipelineSandbox:
    def __init__(self, directory: Path, objects, *, cpu_pool=None, cpu_resources=None, cancelled=lambda: False):
        self.directory, self.objects = directory, objects
        self.environment = api.prepare_tool_environment("iverilog")
        self._lock = Lock()
        self.executions = []
        self.cpu_pool, self.cancelled = cpu_pool, cancelled
        self.cpu_resources = cpu_resources

    def execute(self, workspace: Path, command, *, outputs=(), mounts=()):
        if self.cancelled():
            raise AttemptInterrupted("external-workflow-cancelled")
        command = replace(command, environment=tuple({"TMPDIR": "/work", **dict(command.environment)}.items()))
        with self._lock:
            number = len(self.executions)
            self.executions.append(None)
        logs = self.directory / str(number)
        logs.mkdir(parents=True)
        with cpu_job(self.cpu_resources, cancelled=self.cancelled) as lease, ExitStack() as stack:
            pool = self.cpu_pool
            if lease is not None:
                # Pool/scope setup has not launched a tool process. A setup
                # exception must release, not quarantine, this CPU reservation.
                lease.cleanup = CleanupState.COMPLETE
                private = stack.enter_context(api.CpuPool(capacity=len(lease.cpus)))
                pool = private.endpoint
            if pool is not None:
                stack.enter_context(api.cpu_scope(pool, cancelled=self.cancelled))
            if lease is not None:
                lease.cleanup = CleanupState.UNKNOWN
            result = self.environment.execute(workspace, (api.ToolGroup((command,)),),
                                              outputs=outputs, read_only_mounts=mounts,
                                              output_root=logs)
            if lease is not None:
                lease.cleanup = (CleanupState.UNKNOWN if private.snapshot()["quarantined"]
                                 else CleanupState.COMPLETE)
        evidence = self.objects.put(canonical_json({**result.as_dict(), "invocation": command.image()}))
        self.executions[number] = (evidence,)
        if result.refusal is not None:
            raise AttemptInterrupted(result.refusal.code)
        if not result.commands:
            raise AttemptInterrupted("external-tool-command-unavailable")
        stdout = api.command_stream(result, command.identifier, "stdout")
        stderr = api.command_stream(result, command.identifier, "stderr")
        self.executions[number] = (evidence, self.objects.put(stdout), self.objects.put(stderr),
                                    *(self.objects.put(data) for data in result.outputs.values()))
        return result, stdout.decode(errors="replace"), stderr.decode(errors="replace")
