"""One parent-side, solver-free preparation owner for isolated RTL jobs.

Search and Analysis share frozen compiler documents and task/catalogue bindings,
not solver answers. Native replay and feedback have their own invocation-local
owner, exposed through the existing context entrypoints. Clearing replay state
does not discard the prepared source or authorize reuse of native evidence by
exact Analysis.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from ..core.catalogue import CatalogueManifest
from ..core.refusal import Refusal, Refused

__all__ = ["RTLExactJobContext"]


class RTLExactJobContext:
    """Frozen job inputs plus the lazily prepared compiler source image."""

    __slots__ = (
        "_generation_policy",
        "_goal_capability", "_goal_policy", "_observation",
        "_replay", "_source_documents", "_task", "artifact",
        "_preparation_results", "_preparing", "_source_digest",
        "artifact_identity", "complete_catalogue",
        "contract", "frontend_catalogue", "generation_spec",
        "memory_environment", "schedule",
        "state_ownership", "workdir",
    )

    def __init__(self, artifact, contract, generation_spec, frontend_catalogue,
                 complete_catalogue, *, workdir, clock_schedule=None,
                 generation_policy=None):
        from ..core.contract import TaskContract
        from ..stimulus_set import GenerationSpec
        from .artifact import RTLArtifact

        if type(artifact) is not RTLArtifact:
            raise TypeError("an RTL exact job context needs an exact RTLArtifact")
        if type(contract) is not TaskContract:
            raise TypeError("an RTL exact job context needs an exact TaskContract")
        if type(generation_spec) is not GenerationSpec:
            raise TypeError("an RTL exact job context needs the frozen GenerationSpec")
        if type(frontend_catalogue) is not CatalogueManifest \
                or type(complete_catalogue) is not CatalogueManifest:
            raise TypeError("an RTL exact job context needs exact catalogue manifests")
        if generation_policy is None:
            from ..core.goal_provider import RTL_LOCAL_GUIDANCE_POLICY

            generation_policy = RTL_LOCAL_GUIDANCE_POLICY
        if type(generation_policy) is not str or not generation_policy:
            raise TypeError("an RTL generation context policy is nonempty text")
        if clock_schedule is not None:
            from ..core.clock_schedule import GlobalSchedule
            from .exact_product import stimulus_for_global_schedule

            if type(clock_schedule) is not GlobalSchedule:
                raise TypeError(
                    "an RTL exact clock schedule is an exact GlobalSchedule or absent")
            timings = {timing.name: timing for timing in clock_schedule.timings}
            declared = {clock.name: clock for clock in contract.clocks}
            if set(timings) != set(declared) or any(
                    timings[name].clock != declared[name].clock
                    or timings[name].reset != declared[name].reset
                    or timings[name].reset_active_low
                       != declared[name].reset_active_low
                    or timings[name].edge != declared[name].edge
                    for name in sorted(declared)):
                raise Refused(Refusal(
                    "rtl-atomic-exact-schedule-contract-mismatch",
                    "the exact schedule binds different clock/reset semantics than "
                    "the admitted task contract"))
            if len(clock_schedule.slots) != generation_spec.horizon:
                raise Refused(Refusal(
                    "rtl-atomic-exact-schedule-horizon-mismatch",
                    "the canonical GlobalSchedule must contain exactly the bounded "
                    "analysis horizon",
                    schedule_slots=len(clock_schedule.slots),
                    horizon=generation_spec.horizon))
            probe = stimulus_for_global_schedule(
                clock_schedule,
                {endpoint.name: () for endpoint in contract.inputs()},
                source="global-schedule exact validation")
            # Memory is a separately declared part of this domain. Validate the
            # schedule here; bind_memory_environment closes the product before
            # preparation, without inferring a memory image from scored inputs.
            faults = probe.faults_against(
                contract, require_memory_environment=False)
            if faults:
                raise Refused(Refusal(
                    "rtl-atomic-exact-schedule-outside-contract",
                    "the canonical GlobalSchedule is not a complete task-owned "
                    "environment schedule",
                    first=faults[0], count=len(faults)))
        self.artifact = artifact
        self.artifact_identity = artifact.identity
        self._observation = artifact.observation_binding
        self.contract = contract
        self.generation_spec = generation_spec
        self.frontend_catalogue = frontend_catalogue
        self.complete_catalogue = complete_catalogue
        self.schedule = clock_schedule
        self.memory_environment = None
        self.workdir = Path(workdir or artifact.workdir) / "isolated-exact"
        self.state_ownership = None
        self._preparation_results = {}
        self._preparing = set()
        self._task = None
        self._goal_policy = None
        self._goal_capability = None
        self._generation_policy = generation_policy
        from .exact_job_replay import RTLReplaySession

        self._replay = RTLReplaySession(self)
        self._source_documents = None
        self._source_digest = None

    @property
    def _source_layout(self):
        result = self._preparation_results.get("source-layout")
        return None if isinstance(result, Refusal) else result

    def _prepare_layout(self, stage, build):
        """Publish one complete attempt; cancellation cannot become a result.

        Completed refusals retain the existing no-retry policy. A caller may
        explicitly retry an interrupted attempt under a new owner/deadline;
        this function never renews a budget or retries work itself.
        """
        if stage in self._preparation_results:
            return self._preparation_results[stage]
        if stage in self._preparing:
            raise RuntimeError(f"RTL {stage} preparation is already in progress")
        self._preparing.add(stage)
        try:
            try:
                from .exact_job_layout import RTLGenerationLayout

                result = build()
                if type(result) not in (RTLGenerationLayout, Refusal):
                    raise TypeError("RTL preparation must return a layout or typed refusal")
            except Refused as exc:
                result = exc.refusal
            except Exception as exc:  # noqa: BLE001 - ancillary preparation boundary
                result = self.unavailable(stage, exc)
            self._preparation_results[stage] = result
            return result
        finally:
            # PreparationCancelled inherits BaseException: preserve its owner
            # and cleanup semantics, without publishing a half-complete product.
            self._preparing.remove(stage)

    @property
    def replay(self):
        """Invocation-local replay owner; not a prepared model or proof cache."""
        return self._replay

    def source_documents(self, *, timeout_s=None):
        """Share immutable compiler output across Search and Analysis consumers.

        Copies preserve compiler key order. Neither a consumer's mutations nor a
        failed compilation can change the retained source image. The enclosing
        phase still owns its deadline and the artifact/observer identity checks.
        """
        from .elaboration import source_documents

        self._check_artifact_identity()
        if self._source_documents is None:
            documents = source_documents(self.artifact,
                expected_identity=self.artifact_identity, timeout_s=timeout_s)
            body = json.dumps(documents, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._check_artifact_identity()
            self._source_documents = body
            return documents
        return json.loads(self._source_documents)

    def bind_memory_environment(self, environment):
        """Bind the exact initialization and ready schedule used by concrete replay."""

        from ..core.memory import MemoryEnvironment

        if not self.contract.memories:
            if environment is not None and environment.runs:
                raise Refused(Refusal(
                    "rtl-exact-memory-environment-unexpected",
                    "a memoryless task cannot bind an external-memory environment"))
            return
        if type(environment) is not MemoryEnvironment:
            raise Refused(Refusal(
                "rtl-exact-memory-environment-unbound",
                "an exact external-memory product needs the concrete run's exact "
                "initialization and finite ready schedule"))
        faults = environment.faults_against(
            self.contract.memories, self.generation_spec.horizon)
        if faults:
            raise Refused(Refusal(
                "rtl-exact-memory-environment-outside-contract",
                "the external-memory environment is outside the frozen task and "
                "horizon", first=faults[0], count=len(faults)))
        if self.memory_environment is not None and (
                self.memory_environment.digest != environment.digest
                or self.memory_environment.as_dict() != environment.as_dict()):
            raise Refused(Refusal(
                "rtl-exact-memory-environment-replaced",
                "an exact relation context cannot replace its bound memory run"))
        self.memory_environment = environment

    def bind_goal_policy(self, policy, capability=None, *, guidance=None):
        """Freeze the common policy; optionally attach its bounded-hit authority.

        Local guidance does not require a complete bounded-hit implementation.
        Guidance can use its registered source-query context, which need not cover
        task-observation goals. Analysis may attach bounded authority only when
        this context covers the full policy. Neither binding prepares a relation.
        """

        from ..core.goal_provider import (
            EXACT_BOUNDED_HIT,
            LOCAL_GUIDANCE,
            RTL_EXACT_BOUNDED_POLICY,
            GoalCapabilityUse,
            GoalPolicy,
        )

        if type(policy) is not GoalPolicy:
            raise TypeError("an RTL exact context binds the exact frozen GoalPolicy")
        if capability is not None and type(capability) is not GoalCapabilityUse:
            raise TypeError("an RTL exact context binds a GoalCapabilityUse")
        if guidance is not None:
            if type(guidance) is not GoalCapabilityUse:
                raise TypeError("RTL guidance binds a GoalCapabilityUse")
            if guidance.policy_digest != policy.digest():
                raise Refused(Refusal(
                    "rtl-exact-goal-policy-handoff-mismatch",
                    "the guidance capability belongs to a different GoalPolicy"))
            guidance.reconcile(
                LOCAL_GUIDANCE, self._generation_policy,
                self.complete_catalogue, tuple(guidance.targets))
        if capability is not None:
            if policy.manifest is not capability.source_catalogue \
                    or policy.digest() != capability.policy_digest:
                raise Refused(Refusal(
                    "rtl-exact-goal-policy-handoff-mismatch",
                    "the exact capability was not minted by this frozen GoalPolicy"))
            capability.reconcile(
                EXACT_BOUNDED_HIT, RTL_EXACT_BOUNDED_POLICY,
                policy.manifest, tuple(policy.manifest))
        if (guidance is None or capability is not None) \
                and self.complete_catalogue != policy.manifest:
            raise Refused(Refusal(
                "rtl-exact-goal-policy-catalogue-mismatch",
                "the exact relation context and goal policy name different complete "
                "catalogues"))
        if self._goal_policy is not None and (
                self._goal_policy is not policy
                or (capability is not None and self._goal_capability is not None
                    and self._goal_capability is not capability)):
            raise Refused(Refusal(
                "rtl-exact-goal-policy-replaced",
                "an exact relation context cannot replace its pre-generation goal "
                "policy handoff"))
        self._goal_policy = policy
        if capability is not None:
            self._goal_capability = capability

    @property
    def goal_policy(self):
        return self._goal_policy

    @property
    def goal_capability_use(self):
        return self._goal_capability

    def bind_admitted_task(self, task):
        """Attach the one admission authority used by post-score exact jobs."""

        from ..core.admission import AdmittedTask

        if type(task) is not AdmittedTask:
            raise TypeError("an RTL exact context binds the exact AdmittedTask")
        if task.contract is not self.contract \
                or task.realization.kind != "rtl" \
                or task.realization.identity != self.artifact_identity:
            raise Refused(Refusal(
                "rtl-exact-admission-context-mismatch",
                "the exact relation context and admitted-task handoff name different "
                "contracts, frontends or source bytes"))
        if self._task is not None and self._task is not task:
            raise Refused(Refusal(
                "rtl-exact-admission-context-replaced",
                "a prepared exact relation context cannot replace its immutable "
                "admitted-task handoff"))
        self._task = task

    def _check_artifact_identity(self):
        current = self.artifact.identity
        if current != self.artifact_identity:
            raise Refused(Refusal(
                "rtl-exact-source-changed-after-context-binding",
                "the RTL source changed after the exact relation context froze its "
                "artifact identity",
                frozen=self.artifact_identity, current=current))
        if self.artifact.observation_binding != self._observation:
            raise Refused(Refusal(
                "rtl-exact-observation-changed-after-context-binding",
                "the observer changed after the exact context was frozen"))
        return current

    @staticmethod
    def unavailable(stage, error):
        from ..job_isolation import IsolatedJobFailure
        from .tool_protocol import ToolIsolationError

        detail = getattr(error, "detail", "") or str(error)
        how = getattr(error, "how", type(error).__name__)
        # A dead session is still a failed provider, not a solver UNKNOWN or a
        # language-support claim. Only the transport's typed timeout qualifies.
        timed_out = isinstance(error, IsolatedJobFailure) and error.how == "timeout"
        evidence = ({"tool_executions": [item.as_dict() for item in error.executions]}
                    if isinstance(error, ToolIsolationError) else {})
        if isinstance(error, Refused):
            evidence["cause"] = error.refusal.as_dict()
        if getattr(error, "__notes__", ()):
            evidence["cleanup_notes"] = list(error.__notes__)
        return Refusal(
            f"rtl-exact-isolated-{stage}-{'timeout' if timed_out else 'unavailable'}",
            f"the isolated RTL exact {stage} " + (
                "exceeded its execution deadline" if timed_out else "did not return a valid answer")
            + f": {how}: {detail}",
            failure=str(how), detail=str(detail), **evidence)

    @staticmethod
    def _publish_image(path, body):
        """Atomically materialize the serialized source image below the run workdir."""

        if type(body) is not bytes or not body:
            raise ValueError("an RTL source image is nonempty exact bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.context-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                descriptor = -1
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _observation_image(self):
        self._check_artifact_identity()
        binding = self._observation
        if binding is not None:
            binding.check(self.artifact_identity, self.contract, self.frontend_catalogue)
        return None if binding is None else binding.as_dict()

    def _payload(self):
        # The worker mounts the serialized source image; the image itself
        # carries the state ownership it needs.
        self._check_artifact_identity()
        return {
            "relation_path": str(self.workdir / "source.json"),
            "artifact": self.artifact_identity,
            "observation": self._observation_image(),
            "contract": self.contract.as_dict(),
            "frontend_catalogue": self.frontend_catalogue.as_dict(),
            "complete_catalogue": self.complete_catalogue.as_dict(),
            "queue_bound": self.generation_spec.queue_bound,
            "reset_hold_cycles": self.generation_spec.reset_hold_cycles,
            "clock_schedule": (
                None if self.schedule is None else self.schedule.as_dict()),
            "memory_environment": (
                None if self.memory_environment is None
                else self.memory_environment.as_dict()),
            "state_ownership": None,
        }

    def source_payload(self):
        """The source image uses the existing read-only worker input mount."""
        if self._source_layout is None:
            raise RuntimeError("the RTL source construction is not prepared")
        return self._source_payload()

    def _source_payload(self):
        return {**self._payload(),
                "source_layout": True, "source_image_digest": self._source_digest}

    def prepare_source(self):
        self._check_artifact_identity()
        return self._prepare_layout("source-layout", self._build_source_layout)

    def _build_source_layout(self):
        """Prepare compiler-owned construction without the legacy SMT product.

        A successful layout is not an exact capability. Keep this preparation
        separate from the old circuit cache until query/admission migration is
        complete; neither a circuit image nor a native seed can supply it.
        """
        from ..analysis_budget import active_analysis
        from ..core.reset_monitor import reset_state_domains
        from ..worker import run_job_strict
        from .elaboration import SOURCE_IMAGE_VERSION
        from .exact_job_layout import decode_layout_reply

        budget = active_analysis()

        def allowance():
            return self.artifact.timeout_s if budget is None else budget.remaining(self.artifact.timeout_s)

        allowance()
        frontend = self.artifact.frontend_catalogue(expected_identity=self.artifact_identity)
        if frontend != self.frontend_catalogue:
            raise Refused(Refusal("rtl-exact-frontend-catalogue-mismatch",
                "source preparation needs the frozen native catalogue"))
        image = {"version": SOURCE_IMAGE_VERSION, "top": self.artifact.top_module,
                 **self.source_documents(timeout_s=allowance())}
        # A symbolic schedule retains every restoration carrier. A fixed
        # calendar can prove the monitor inactive before any RTL lowering;
        # do not make irrelevant state ownership a source prerequisite.
        reset_domains = reset_state_domains(self.contract,
            None if self.schedule is None else tuple(slot.event for slot in self.schedule.slots),
            global_events=self.schedule is not None)
        image["state_ownership"] = (self.artifact.state_ownership(
            self.contract, expected_identity=self.artifact_identity, timeout_s=allowance()).as_dict()
            if reset_domains else None)
        self._check_artifact_identity()
        # CST child order is syntax order. Sorting compiler-document keys
        # changes the token stream; bind the exact serialized bytes instead.
        body = json.dumps(image, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._source_digest = hashlib.sha256(body).hexdigest()
        self._publish_image(self.workdir / "source.json", body)
        reply = run_job_strict("rtl_generation_layout", self._source_payload(),
            timeout_s=allowance(), label="isolated RTL source construction layout")
        got = decode_layout_reply(reply)
        self._check_artifact_identity()
        allowance()
        return got

    def analysis_payload(self):
        """Complete source construction plus the immutable admission handoff."""

        if self._task is None:
            raise Refused(Refusal(
                "rtl-exact-admission-context-unbound",
                "post-score exact analysis requires the original admitted task"))
        return {
            **self.source_payload(),
            "admission": self._task.as_dict(),
            "admission_digest": self._task.handoff_identity,
        }

    def source_trace(self, stimulus, *, initial_state=None, startup_state=None, reuse=True):
        return self._replay.source_trace(stimulus, initial_state=initial_state,
                                         startup_state=startup_state, reuse=reuse)

    def selection_traces(self, stimuli, *, workers=None):
        return self._replay.selection_traces(stimuli, workers=workers)

    def _selection_state_ownership(self, stimuli):
        from ..core.reset_monitor import reset_schedule_scenarios

        if not any(clock.reset_restores is True for clock in self.contract.clocks):
            return None
        # A fixed schedule with no post-work restoration event has no state
        # equality to check. Avoid making inactive monitors a replay prerequisite.
        # Activated obligations retain the original complete ownership/capture.
        if not any(seed.replay_policy is not None
                   or reset_schedule_scenarios(self.contract, seed) for seed in stimuli):
            return None
        if self.state_ownership is None:
            self.state_ownership = self.artifact.state_ownership(
                self.contract, expected_identity=self.artifact_identity)
        return self.state_ownership

    def selection_feedback(self, stimulus, trace):
        return self._replay.selection_feedback(stimulus, trace)

    def selection_trace(self, stimulus):
        return self._replay.selection_trace(stimulus)
