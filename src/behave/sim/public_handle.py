"""Closed public Behavior and RTL execution handles.

Callers describe one admitted source and run policy with
:class:`BehaviorConfig` or :class:`RTLConfig`.  The implementation retains
the executable artifact only behind the relevant handle.  A command always supports
concrete replay and scoring; bounded analysis is an explicit optional branch whose
capability is bound only after the score is durable.  There is deliberately no public
registry or arbitrary factory hook.
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path

from behave.core import runtime_defaults as defaults

from .core.admission import AdmittedTask
from .core.catalogue import CatalogueManifest
from .core.frozen import Frozen
from .core.instance import BoundedInstance
from .core.record_value import FrozenValueMap, freeze_record_value, thaw_record_value
from .core.refusal import Refusal, Refused
from .stimulus_set import GenerationSpec

__all__ = [
    "BehaviorConfig",
    "BehaviorHandle",
    "RTLConfig",
    "RTLHandle",
]

_HANDLE_TOKEN = object()
_MAX_BEHAVIOR_SOURCE_BYTES = defaults.SIM_SOURCE_BYTES


def _positive_int(value, field):
    if type(value) is not int or value < 1:
        raise TypeError(f"{field} must be a positive exact integer")
    return value


def _behavior_source_snapshot(config):
    """Read one regular non-symlink source file once, without parsing it here."""

    if type(config) is not BehaviorConfig:
        raise TypeError("a Behavior source snapshot needs BehaviorConfig")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) \
        | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(config.behavior_path, flags)
    except OSError as exc:
        raise Refused(Refusal(
            "behavior-public-source-unavailable",
            "the Behavior source must be one readable non-symlink file",
            error=f"{type(exc).__name__}: {exc}")) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Refused(Refusal(
                "behavior-public-source-not-regular",
                "the Behavior source path does not name a regular file"))
        chunks = []
        remaining = _MAX_BEHAVIOR_SOURCE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(1 << 20, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > _MAX_BEHAVIOR_SOURCE_BYTES:
            raise Refused(Refusal(
                "behavior-public-source-too-large",
                "the Behavior source exceeds the finite worker protocol limit",
                limit=_MAX_BEHAVIOR_SOURCE_BYTES))
    finally:
        os.close(fd)
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise Refused(Refusal(
            "behavior-public-source-not-utf8",
            "the Behavior source is not UTF-8 text",
            error=str(exc))) from None


def _command_request_for_handle(
        *, task, artifact, generation_spec, frontend_catalogue,
        analysis_bound, exact, analysis_request, run_directory, baseline,
        providers, probe_budget, run_analysis, secondary_pool_budget, reference, feasibility_timeout_ms,
        query_timeout_ms, batch, goal_policy, search_policy=None, scoring_policy=None,
        analysis_wall_timeout_seconds=None, capture_analysis=False):
    """Enter the one score-first command used by both artifact kinds."""

    from .command import _command_request

    return _command_request(
        run_directory=run_directory,
        task=task,
        artifact=artifact,
        baseline=baseline,
        providers=providers,
        frontend_catalogue=frontend_catalogue,
        generation_spec=generation_spec,
        analysis_bound=analysis_bound,
        probe_budget=probe_budget,
        search_policy=search_policy,
        scoring_policy=scoring_policy,
        run_analysis=run_analysis,
        capture_analysis=capture_analysis,
        analysis_wall_timeout_seconds=analysis_wall_timeout_seconds,
        secondary_pool_budget=secondary_pool_budget,
        reference=reference,
        exact=exact,
        analysis_request=analysis_request,
        goal_policy=goal_policy,
        feasibility_timeout_ms=feasibility_timeout_ms,
        query_timeout_ms=query_timeout_ms,
        batch=batch,
    )


@contextmanager
def _replay_session_for_handle(handle):
    from .rtl.replay_build import replay_build_scope

    with replay_build_scope(handle._artifact):
        yield handle


def _replay_for_handle(handle, stimuli, reference, *, stop_on_failure=False,
                       capture_analysis=False):
    """The same explicit-pool authority boundary for both artifact kinds."""
    from .core.reference import LoadedReference, _require_task_reference
    from .core.stimulus import Stimulus
    from .evaluation import ReplayExecution, evaluate
    from .evaluation_policy import verdict
    from .replay_suite import ReplaySuite
    from .rtl.replay_build import replay_build_scope

    if type(stop_on_failure) is not bool:
        raise TypeError("stop_on_failure must be an exact Boolean")
    if type(capture_analysis) is not bool:
        raise TypeError("capture_analysis must be an exact Boolean")
    if type(stimuli) is not ReplaySuite and (type(stimuli) is not tuple or not stimuli
                                           or any(type(s) is not Stimulus for s in stimuli)):
        raise TypeError("replay requires a ReplaySuite or a nonempty immutable stimulus pool")
    if type(reference) is not LoadedReference:
        raise TypeError("replay requires the loaded reference capability")
    _require_task_reference(handle._task, reference)
    execution = ReplayExecution(
        handle._task, handle._artifact, reference, handle._goal_policy.manifest,
        capture_query_boundaries="history" if capture_analysis else True)
    with replay_build_scope(handle._artifact):
        evaluation = evaluate(handle._task, stimuli, handle._artifact, reference,
                              catalogue=handle._goal_policy.manifest,
                              stop_on_failure=stop_on_failure, _execution=execution)
    return verdict(evaluation)


def _generation_providers_for_handle(
        task, artifact, generation_spec, frontend_catalogue, reference, workdir, *,
        clock_schedule=None, enable_local_guidance=True, goal_policy=None):
    """Construct the same two Appendix A.3 directions for either artifact kind."""

    from .production_generation import production_generation_providers

    capabilities = production_generation_providers(
        task, artifact, reference, generation_spec, frontend_catalogue,
        workdir=workdir, clock_schedule=clock_schedule,
        enable_local_guidance=enable_local_guidance, defer=True, goal_policy=goal_policy)
    return capabilities.providers


class RTLConfig(Frozen):
    """Finite public source and tool configuration for one RTL handle.

    Run-domain and stimulus-construction fields come from the separately admitted
    :class:`GenerationSpec`.  ``run_policy`` may carry backend-specific finite options;
    repeating a generation field is accepted only when it has the same value.
    ``timeout_s`` bounds tool jobs, including standalone diagnostic replay.
    Scored replay uses the enclosing scoring deadline instead; there is no
    additional per-stimulus allowance. It is not a total command deadline.
    Solver query limits are set separately
    on the command. RTL tool supervision uses the runtime's envelope overhead
    and cleanup policy; neither limit changes the trace domain.

    ``workdir`` is the handle's artifact location, not an execution scratch
    override. Compiler/replay staging uses ``BEHAVE_SCRATCH_DIR`` (default
    ``/tmp``); the command's explicit ``run_directory`` owns durable results.
    ``profile_replay`` retains cocotb profiles under ``workdir/profiles``. It
    changes diagnostics and runtime overhead, not inputs or observation rules.
    """

    __slots__ = (
        "period_ns", "profile_replay", "rtl_path", "run_policy", "timeout_s",
        "top_module", "verify_every", "workdir",
    )

    def __init__(self, rtl_path, top_module, workdir, *, period_ns=10,
                 timeout_s=defaults.SIM_RTL_TOOL_SECONDS,
                 verify_every=False,
                 run_policy=None, profile_replay=defaults.SIM_RTL_PROFILE_REPLAY):
        if type(rtl_path) is not str and not isinstance(rtl_path, Path):
            raise TypeError("rtl_path must be an exact string or Path")
        if type(workdir) is not str and not isinstance(workdir, Path):
            raise TypeError("workdir must be an exact string or Path")
        if type(top_module) is not str or not top_module:
            raise TypeError("top_module must be a nonempty exact string")
        if type(verify_every) is not bool:
            raise TypeError("verify_every must be an exact bool")
        if type(profile_replay) is not bool:
            raise TypeError("profile_replay must be an exact bool")
        if run_policy is None:
            run_policy = {}
        if type(run_policy) is not dict:
            raise TypeError("run_policy must be an exact mapping")
        self.rtl_path = str(rtl_path)
        self.top_module = top_module
        self.workdir = str(workdir)
        self.period_ns = _positive_int(period_ns, "period_ns")
        self.timeout_s = _positive_int(timeout_s, "timeout_s")
        self.verify_every = verify_every
        self.profile_replay = profile_replay
        frozen_policy = freeze_record_value(
            run_policy, "RTLConfig.run_policy")
        if type(frozen_policy) is not FrozenValueMap:
            raise TypeError("run_policy must have one finite record image")
        self.run_policy = frozen_policy
        self._freeze()

    def as_dict(self):
        return {
            "rtl_path": self.rtl_path,
            "top_module": self.top_module,
            "workdir": self.workdir,
            "period_ns": self.period_ns,
            "timeout_s": self.timeout_s,
            "verify_every": self.verify_every,
            "profile_replay": self.profile_replay,
            "run_policy": thaw_record_value(self.run_policy),
        }


class BehaviorConfig(Frozen):
    """Immutable source and worker configuration for one Behavior handle.

    ``behavior_path`` is read once by :func:`api.prepare_behavior`.  The exact
    bytes are then retained privately by its job context, so changing or replacing the
    path cannot change a handle that has already been prepared.  ``file`` is the stable
    source-site label used by goal identifiers; it must equal the label supplied to
    :func:`api.realization_of_behavior_source` and never selects implementation code.
    ``model`` selects the same source class used for realization. When omitted,
    only the existing unambiguous-entry rule applies; helper order is not a selector.
    ``timeout_s`` bounds isolated frontend preparation. Complete bounded analysis
    uses its own public wall budget, shared by construction, queries and audits.
    """

    __slots__ = ("behavior_path", "file", "model", "timeout_s")

    def __init__(self, behavior_path, *, file="behavior.py",
                 timeout_s=defaults.SIM_BEHAVIOR_WORKER_SECONDS, model=None):
        if type(behavior_path) is not str and not isinstance(behavior_path, Path):
            raise TypeError("behavior_path must be an exact string or Path")
        if type(file) is not str or not file or "\x00" in file:
            raise TypeError("file must be a nonempty exact source label")
        self.behavior_path = str(behavior_path)
        self.file = file
        if model is not None and (type(model) is not str or not model.isidentifier()):
            raise TypeError("model must be absent or an exact Python identifier")
        self.model = model
        self.timeout_s = _positive_int(timeout_s, "timeout_s")
        self._freeze()

    def as_dict(self):
        return {
            "behavior_path": self.behavior_path,
            "file": self.file,
            "model": self.model,
            "timeout_s": self.timeout_s,
        }


class RTLHandle(Frozen):
    """Opaque public RTL artifact with an optional post-score analysis request."""

    __slots__ = (
        "_analysis_request", "_artifact", "_frontend_catalogue",
        "_generation_spec", "_goal_policy", "_task",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("RTLHandle is a closed public capability record")

    def __init__(self, task, artifact, generation_spec, frontend_catalogue,
                 analysis_request, *, _token=None):
        if _token is not _HANDLE_TOKEN:
            raise TypeError("use api.prepare_rtl()")
        if type(task) is not AdmittedTask:
            raise TypeError("an RTL handle needs the exact AdmittedTask")
        if generation_spec is not None and type(generation_spec) is not GenerationSpec:
            raise TypeError("generation_spec must be a GenerationSpec or absent")
        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError("an RTL handle needs the exact frontend manifest")
        from .rtl.analysis import RTLAnalysisRequest

        if analysis_request is not None and type(analysis_request) is not RTLAnalysisRequest:
            raise TypeError(
                "an RTL handle needs the closed post-score analysis request")
        if getattr(artifact, "identity", None) != task.realization.identity:
            raise Refused(Refusal(
                "rtl-public-exact-artifact-mismatch",
                "the public RTL source and admitted realization name different bytes",
                admitted=task.realization.identity,
                artifact=str(getattr(artifact, "identity", ""))))
        if analysis_request is not None and (analysis_request.task is not task \
                or analysis_request.artifact is not artifact \
                or analysis_request.generation_spec is not generation_spec \
                or analysis_request.frontend_catalogue is not frontend_catalogue):
            raise Refused(Refusal(
                "rtl-public-exact-request-mismatch",
                "the post-score exact request must retain this admitted task, RTL "
                "artifact, generation policy and frontend catalogue"))
        self._task = task
        self._artifact = artifact
        self._generation_spec = generation_spec
        self._frontend_catalogue = frontend_catalogue
        self._analysis_request = analysis_request
        from .goal_policy import evaluation_policy

        self._goal_policy = (evaluation_policy(task, frontend_catalogue)
                             if analysis_request is None else analysis_request.goal_policy)
        self._freeze()

    @property
    def frontend_catalogue(self):
        return self._frontend_catalogue

    @property
    def artifact_identity(self):
        return self._task.realization.identity

    def replay(self, stimuli, *, reference, stop_on_failure=False, capture_analysis=False):
        """Replay an explicit complete pool with the normal concrete score rule.

        Used by fixed-workload and common-final-suite consumers. This neither
        generates tests nor runs analysis, and cannot substitute a caller-defined
        catalogue or artifact. The returned authority retains the full evaluation;
        callers must persist it and must not read a score from partial evidence.
        ``stop_on_failure`` reuses scoring's checked-negative-witness stop;
        the full requested pool and all started results remain in the record.
        ``capture_analysis`` retains passive startup and slot history for later
        analysis; it does not run a solver or change the scoring rule.
        """
        return _replay_for_handle(self, stimuli, reference, stop_on_failure=stop_on_failure,
                                 capture_analysis=capture_analysis)

    def replay_session(self):
        """Reuse compilation across sequential replay calls on this handle.

        ``with handle.replay_session():`` owns the existing private build scope.
        Calls still start fresh simulator processes and independently check their
        entire requested pools. No trace, state, verdict or testing budget is
        cached or combined. Callers retain every result, including failures.
        The scope is context-local; it is not a cross-thread/process build cache.
        Ordinary replay and commands already own such a scope for one call.
        """
        return _replay_session_for_handle(self)

    def generation_providers(
            self, reference, *, workdir, clock_schedule=None,
            enable_local_guidance=True):
        """Build independent reference-Behavior and candidate-RTL searches.

        ``workdir`` stores reusable construction state and must not be the fresh
        invocation directory later passed to :meth:`command_request` or lie inside it.
        A score-only caller may set ``enable_local_guidance=False``; the two named
        directions remain in generation provenance, but no unused solver context is
        constructed.
        """

        return _generation_providers_for_handle(
            self._task, self._artifact, self._generation_spec,
            self._frontend_catalogue, reference, workdir,
            clock_schedule=clock_schedule,
            enable_local_guidance=enable_local_guidance,
            goal_policy=self._goal_policy)

    def command_request(self, *, run_directory, baseline, providers=(), probe_budget=None,
                        search_policy=None, scoring_policy=None,
                        run_analysis=False, capture_analysis=False,
                        secondary_pool_budget=0,
                        reference=None, feasibility_timeout_ms=None,
                        query_timeout_ms=None, batch=None, analysis_wall_timeout_seconds=None):
        """Build a score command with optional analysis and pool reduction.

        ``secondary_pool_budget`` limits greedy candidate checks; zero disables
        reduction. This work budget is independent of bounded analysis.
        With a zero search budget, ``providers`` may be omitted.
        Analysis requires an explicit ``analysis_wall_timeout_seconds``;
        omitted per-query limits inherit that phase's remaining budget.
        ``capture_analysis`` retains the same history without running analysis.
        """

        from .production_generation import reuse_rtl_request

        providers = tuple(providers)
        analysis_request = reuse_rtl_request(
            self._analysis_request, providers)
        return _command_request_for_handle(
            run_directory=run_directory,
            task=self._task,
            artifact=self._artifact,
            baseline=baseline,
            providers=providers,
            frontend_catalogue=self._frontend_catalogue,
            generation_spec=self._generation_spec,
            analysis_bound=(None if self._generation_spec is None else self._generation_spec.horizon),
            probe_budget=probe_budget,
            search_policy=search_policy,
            scoring_policy=scoring_policy,
            run_analysis=run_analysis,
            capture_analysis=capture_analysis,
            secondary_pool_budget=secondary_pool_budget,
            reference=reference,
            # The analysis environment is declared independently of the runtime
            # workload. Build its relation only after persisting the score; an
            # unsupported/unbound domain cannot rewrite that primary decision.
            exact=None,
            analysis_request=analysis_request,
            goal_policy=self._goal_policy,
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms,
            batch=batch,
            analysis_wall_timeout_seconds=analysis_wall_timeout_seconds,
        )

    def as_dict(self):
        return {
            "artifact": self.artifact_identity,
            "task_contract": self._task.contract.digest,
            "generation_spec": (None if self._generation_spec is None else self._generation_spec.as_dict()),
            "frontend_catalogue": self._frontend_catalogue.digest(),
            "analysis_request": (None if self._analysis_request is None else self._analysis_request.as_dict()),
        }


class BehaviorHandle(Frozen):
    """Opaque Behavior runner with an optional post-score analysis capability.

    Score-only preparation retains no lowering or solver capability.  When bounded
    analysis was explicitly prepared, ordinary and multi-clock sources retain an
    immutable post-score request; multi-clock binding additionally consumes the global
    schedule fixed by concrete evaluation.
    """

    __slots__ = (
        "_analysis_request", "_artifact", "_context", "_exact",
        "_frontend_catalogue", "_generation_spec", "_goal_policy", "_task",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("BehaviorHandle is a closed public capability record")

    def replay(self, stimuli, *, reference, stop_on_failure=False, capture_analysis=False):
        """Score an explicit complete pool, without generation or analysis.

        The returned authority contains the evaluation. Check ``authoritative``
        before reading ``passed``; verifier/infra/reference failures have no score.
        This is the same boundary as :meth:`RTLHandle.replay`.
        """
        return _replay_for_handle(self, stimuli, reference, stop_on_failure=stop_on_failure,
                                 capture_analysis=capture_analysis)

    def replay_session(self):
        """Same compile-only resource scope as :meth:`RTLHandle.replay_session`.

        Behavior instances and reference processes still restart for each run;
        this interface does not change the observation or scoring rules.
        """
        return _replay_session_for_handle(self)

    def __init__(self, task, context, artifact, generation_spec, frontend_catalogue,
                 exact=None, analysis_request=None, *, _token=None):
        if _token is not _HANDLE_TOKEN:
            raise TypeError("use api.prepare_behavior()")
        if type(task) is not AdmittedTask:
            raise TypeError("a Behavior handle needs the exact AdmittedTask")
        if generation_spec is not None and type(generation_spec) is not GenerationSpec:
            raise TypeError(
                "a Behavior handle needs the frozen GenerationSpec")
        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError(
                "a Behavior handle needs the exact frontend manifest")
        from .behavior.artifact import BehaviorArtifact
        from .behavior.atomic_analysis import BehaviorAtomicAnalysisRequest
        from .behavior.exact_service import (
            BehaviorExactJobContext,
            BehaviorIsolatedAnalysisInstance,
        )
        from .behavior.transaction_analysis import BehaviorAnalysisRequest

        if type(context) is not BehaviorExactJobContext:
            raise TypeError(
                "a Behavior handle needs its frozen isolated job context")
        if type(artifact) is not BehaviorArtifact:
            raise TypeError(
                "a Behavior handle needs its closed concrete runner")
        ordinary = (type(exact) is BoundedInstance
                    and analysis_request is None)
        score_only = exact is None and analysis_request is None
        lazy_ordinary = (
            exact is None and type(analysis_request) is BehaviorAnalysisRequest)
        atomic = (
            exact is None
            and type(analysis_request) is BehaviorAtomicAnalysisRequest)
        if not score_only and not ordinary and not lazy_ordinary and not atomic:
            raise TypeError(
                "a Behavior handle needs no analysis capability, one bound instance, "
                "or one closed post-score analysis request")
        if ordinary and (type(exact.instance) is not
                         BehaviorIsolatedAnalysisInstance
                         or exact.instance._context is not context):
            raise TypeError(
                "a bound Behavior handle needs its isolated job context")
        if (lazy_ordinary or atomic) and (
                analysis_request.task is not task
                or analysis_request.context is not context
                or analysis_request.generation_spec is not generation_spec
                or analysis_request.frontend_catalogue is not
                frontend_catalogue):
            raise Refused(Refusal(
                "behavior-public-analysis-request-mismatch",
                "the request must retain this admitted task, source context, "
                "generation policy and frontend catalogue"))
        if score_only and context.source_catalogue is not frontend_catalogue:
            raise Refused(Refusal(
                "behavior-public-source-catalogue-mismatch",
                "a score-only Behavior handle must retain the source catalogue "
                "frozen by its isolated source judge"))
        exact_artifact = (task.realization.identity
                          if score_only or lazy_ordinary or atomic else
                          exact.domain.artifact)
        if exact_artifact != task.realization.identity:
            raise Refused(Refusal(
                "behavior-public-exact-artifact-mismatch",
                "the public Behavior source and admitted realization name different "
                "transition systems",
                admitted=task.realization.identity,
                behavior=str(exact_artifact)))
        if ordinary and (exact.domain.task_contract != task.contract.digest
                         or exact.domain.bound != generation_spec.max_body_steps):
            raise Refused(Refusal(
                "behavior-public-exact-domain-mismatch",
                "the isolated Behavior capability must already be bound to this "
                "admitted task and transaction-step bound",
                admitted_contract=task.contract.digest,
                exact_contract=exact.domain.task_contract,
                transaction_bound=generation_spec.max_body_steps,
                exact_horizon=exact.domain.bound))
        self._task = task
        self._context = context
        self._artifact = artifact
        self._generation_spec = generation_spec
        self._frontend_catalogue = frontend_catalogue
        self._exact = exact
        self._analysis_request = analysis_request
        self._goal_policy = context.goal_policy
        self._freeze()

    @property
    def exact(self):
        """The isolated bounded capability accepted by :func:`api.analyse`."""

        if self._exact is not None:
            return self._exact
        from .behavior.atomic_analysis import BehaviorAtomicAnalysisRequest

        if type(self._analysis_request) is BehaviorAtomicAnalysisRequest:
            raise Refused(Refusal(
                "behavior-atomic-exact-schedule-unbound",
                "the atomic Behavior capability is bound after concrete evaluation "
                "fixes one global schedule; call bind(stimuli)"))
        if self._analysis_request is None:
            raise Refused(Refusal(
                "behavior-bounded-analysis-not-prepared",
                "this handle was prepared for concrete scoring only; prepare it "
                "with bounded_analysis=True before requesting bounded analysis"))
        raise Refused(Refusal(
            "behavior-exact-analysis-unbound",
            "the Behavior exact capability is constructed on explicit bind or after "
            "concrete scoring; call bind(stimuli)"))

    def bind(self, stimuli=()):
        """Return the bound capability for the concrete evaluation's stimuli."""

        if self._analysis_request is None:
            if self._exact is not None:
                return self._exact
            raise Refused(Refusal(
                "behavior-bounded-analysis-not-prepared",
                "this handle was prepared for concrete scoring only; prepare it "
                "with bounded_analysis=True before binding bounded analysis"))
        return self._analysis_request.bind(tuple(stimuli))

    @property
    def frontend_catalogue(self):
        return self._frontend_catalogue

    @property
    def artifact_identity(self):
        return self._task.realization.identity

    def generation_providers(
            self, reference, *, workdir, enable_local_guidance=True):
        """Build independent reference and candidate Behavior searches.

        ``workdir`` stores reusable construction state and must not be the fresh
        invocation directory later passed to :meth:`command_request` or lie inside it.
        A score-only caller may set ``enable_local_guidance=False``; the two named
        directions remain in generation provenance, but no unused solver context is
        constructed.
        """

        return _generation_providers_for_handle(
            self._task, self._artifact, self._generation_spec,
            self._frontend_catalogue, reference, workdir,
            enable_local_guidance=enable_local_guidance,
            goal_policy=self._goal_policy)

    def command_request(self, *, run_directory, baseline, providers=(), probe_budget=None,
                        search_policy=None, scoring_policy=None,
                        run_analysis=False, capture_analysis=False,
                        secondary_pool_budget=0,
                        reference=None, feasibility_timeout_ms=None,
                        query_timeout_ms=None, batch=None, analysis_wall_timeout_seconds=None):
        """Build a score command with optional analysis and pool reduction.

        ``secondary_pool_budget`` limits greedy candidate checks; zero disables
        reduction. This work budget is independent of bounded analysis.
        With a zero search budget, ``providers`` may be omitted.
        Analysis requires an explicit ``analysis_wall_timeout_seconds``;
        omitted per-query limits inherit that phase's remaining budget.
        ``capture_analysis`` retains the same history without running analysis.
        """

        return _command_request_for_handle(
            run_directory=run_directory,
            task=self._task,
            artifact=self._artifact,
            baseline=baseline,
            providers=providers,
            frontend_catalogue=self._frontend_catalogue,
            generation_spec=self._generation_spec,
            analysis_bound=(self._analysis_request.analysis_bound
                            if self._analysis_request is not None
                            else None if self._generation_spec is None
                            else self._generation_spec.max_body_steps),
            probe_budget=probe_budget,
            search_policy=search_policy,
            scoring_policy=scoring_policy,
            run_analysis=run_analysis,
            capture_analysis=capture_analysis,
            secondary_pool_budget=secondary_pool_budget,
            reference=reference,
            exact=self._exact,
            analysis_request=self._analysis_request,
            goal_policy=self._goal_policy,
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms,
            batch=batch,
            analysis_wall_timeout_seconds=analysis_wall_timeout_seconds,
        )

    def as_dict(self):
        out = {
            "artifact": self.artifact_identity,
            "task_contract": self._task.contract.digest,
            "generation_spec": (None if self._generation_spec is None else self._generation_spec.as_dict()),
            "frontend_catalogue": self._frontend_catalogue.digest(),
        }
        if self._exact is not None:
            out["exact_domain"] = self._exact.domain.digest()
        elif self._analysis_request is not None:
            out["analysis_request"] = self._analysis_request.as_dict()
        else:
            out["analysis"] = "not-prepared"
        return out


def _rtl_handle(task, artifact, generation_spec, frontend_catalogue,
                      analysis_request):
    return RTLHandle(
        task, artifact, generation_spec, frontend_catalogue, analysis_request,
        _token=_HANDLE_TOKEN)


def _behavior_handle(task, context, artifact, generation_spec,
                           frontend_catalogue,
                           exact=None, analysis_request=None):
    return BehaviorHandle(
        task, context, artifact, generation_spec, frontend_catalogue, exact,
        analysis_request,
        _token=_HANDLE_TOKEN)
