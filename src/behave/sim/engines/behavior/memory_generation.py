"""Ordinary Behavior guidance bound to the selected run's external memory.

The source catalogue and endpoint signature do not depend on an initial image.
Lowering does: Select supplies that image and its real replay horizon. Reuse the
existing task boundary and native source/IR audit, not a memoryless surrogate.
"""

from ...behavior.goal_projection import literal_false_goals
from ...behavior.source_plan import plan_source
from ...behavior.task_boundary import lower_task_behavior, task_procedure_boundary
from ...core.refusal import Refusal, Refused
from ...core.stimulus import Stimulus
from ..local_ir import _LocalQueryPreparation


class MemoryGuidance:
    def __init__(self, source, file, contract, model, identity, bound):
        self.source, self.file, self.contract = source, file, contract
        self.cls, self.identity, self.bound = model.cls.name, identity, bound
        self.manifest = plan_source(model, source, file,
                                    source_rules=contract.source_rules).manifest
        boundary = task_procedure_boundary(source, file, contract, cls=self.cls)
        self.input_endpoints = tuple((row.endpoint, row.fields) for row in boundary.inputs)
        self._key = self._prepared = self._queries = None
        self._ruled_out = ()
        self._proved_questions = {}

    def frontend_catalogue_manifest(self):
        return self.manifest

    def prepare(self, seed):
        if seed.memory_environment is None:
            raise Refused(Refusal(
                "behavior-generation-memory-environment-missing",
                "ordinary memory guidance needs the selected replay's external memory image"))
        key = seed.memory_environment.as_dict(), len(seed.slots)
        if key != self._key:
            # Publish the context only after the existing boundary validates the
            # whole image. Retained active selections keep their own prior context.
            instance, boundary = lower_task_behavior(
                self.source, self.file, self.contract, self.bound, cls=self.cls,
                memory_environment=seed.memory_environment, memory_horizon=len(seed.slots),
                prove_outputs=False, local_guidance=True)
            self.manifest.reconcile(instance.frontend_catalogue_manifest(),
                                    "behavior-memory-generation")
            self._prepared = instance, boundary
            self._queries = _LocalQueryPreparation(instance.proc, instance.env)
            self._ruled_out = literal_false_goals(instance.proc)
            self._proved_questions = {}
            self._key = key
        return self._prepared

    def select(self, built, seed, trace, targets, *, verify_feedback=True, ruled_out=None):
        from .generation_job import SelectedTrace

        instance, boundary = self.prepare(Stimulus.from_dict(seed))
        bound = (*built[:4], instance, *built[5:7], boundary)
        return SelectedTrace(bound, seed, trace, targets, verify_feedback=verify_feedback,
                             ruled_out=self._ruled_out, preparation=self._queries,
                             proved_questions=self._proved_questions)
