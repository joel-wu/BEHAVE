"""Upstream role calls through the existing model loop and lossless journals."""

from dataclasses import replace
from pathlib import Path
from threading import Lock

from behave.agent.attempt import AttemptInterrupted, AttemptSpec
from behave.agent.journal import AttemptJournal
from behave.agent.loop import run_attempt
from behave.agent.model import ModelReply
from behave.agent.trajectory import CostLedger, trajectory
from behave.core.artifacts import read_verified
from behave.core.records import metadata, parse_json


class RoleCalls:
    """One workflow's calls, including rejected candidates and transport retries.

    Each logical call has its own journal, so upstream parallel sampling cannot
    interleave two role histories. Provider admission and retry rules are those
    of run_attempt. No tools, BEHAVE instructions, extra turns or cumulative
    token budget are added to the upstream prompt.
    """

    def __init__(self, directory: Path, objects, spec: AttemptSpec, backend_factory,
                 *, model_pool=None, control=None):
        self.directory, self.objects = directory, objects
        self.spec, self.backend_factory = spec, backend_factory
        self.model_pool, self.control = model_pool, control
        self._lock = Lock()
        self._counts = {}
        self.journals: list[AttemptJournal] = []

    def generate(self, role: str, messages: tuple[dict, ...], **sampling) -> ModelReply:
        with self._lock:
            number = self._counts.get(role, 0)
            self._counts[role] = number + 1
            journal = AttemptJournal(self.directory / role / str(number), self.objects)
            self.journals.append(journal)
        backend, policy = self.backend_factory(sampling)
        policy = dict(policy)
        # Individual role journals are separate calls, not separate physical
        # rollout sessions. Preserve one engine affinity for the whole task.
        policy["routing_key"] = self.spec.attempt_id
        policy["admission"] = None if self.model_pool is None else self.model_pool.policy.identity
        spec = replace(self.spec, attempt_id=f"{self.spec.attempt_id}:{role}:{number}",
                       role=role, max_turns=1, model_policy=metadata(policy))
        outcome = run_attempt(journal=journal, backend=backend, spec=spec,
                              initial_messages=messages, model_admission=self.model_pool,
                              control=self.control, runtime_turn_status=False)
        turns = trajectory(journal).turns
        if not turns:
            raise AttemptInterrupted(f"external-model-{outcome.reason}")
        last = turns[-1]
        return ModelReply.from_dict(parse_json(read_verified(self.objects, last.response)),
                                    raw=read_verified(self.objects, last.raw_response))

    def accounting(self) -> CostLedger:
        return CostLedger.combine(tuple(trajectory(journal).billing for journal in self.journals))

    def evidence(self):
        """Child heads retain exact requests/replies, hidden reasoning and usage."""
        return tuple(journal.seal() for journal in self.journals)
