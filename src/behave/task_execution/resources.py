"""Scheduling adapters only; the wrapped backends retain all score semantics."""

from behave.agent.cpu_resources import cpu_job


class ScheduledJudge:
    def __init__(self, backend, resources):
        self.backend, self.resources = backend, resources
        self.identity = backend.identity

    def evaluate(self, request, objects):
        with cpu_job(self.resources, cancelled=request.cancelled) as lease:
            result = self.backend.evaluate(request, objects)
            lease.cleanup = result.cleanup
            return result


class ScheduledTestbench:
    def __init__(self, backend, resources):
        self.backend, self.resources = backend, resources
        self.identity = backend.identity

    def observe(self, request, objects):
        with cpu_job(self.resources, cancelled=request.cancelled) as lease:
            result = self.backend.observe(request, objects)
            lease.cleanup = result.cleanup
            return result
