"""The simulator command boundary, ready for a later CLI to register.

TODO3 owns this handler and its typed request/result contract; TODO4 owns argument
parsing and the package entry point. The handler orchestrates the phase services
exported by :mod:`behave.sim.api`. It does not admit, generate, replay, compare,
or classify anything by itself.

One request reserves one explicit directory and opens the existing
:class:`~behave.sim.result_store.ResultStore` there.  The concrete result is decided and
persisted before optional bounded analysis starts.  Consequently a missing or broken
exact provider can change only the analysis field, never the score or its raw records.
"""

from __future__ import annotations

import enum
import hashlib
import json
from pathlib import Path

from . import analysis_service
from . import evaluation as evaluation_service
from . import generation as generation_service
from . import witness_pool
from .analysis_budget import AnalysisStopped, analysis_scope
from .analysis_report import analyse_after_decision
from .authority import CONCRETE, PARTIAL, REFUSED, Authoritative
from .baseline import BaselineSet, _materialize_baseline
from .core.admission import AdmittedTask
from .core.catalogue import CatalogueManifest
from .core.conditional_evidence import AnalysisPolicy
from .core.failure import FailureRaised
from .core.frozen import Frozen, FrozenMap
from .core.goal_provider import (
    GoalCapabilityUse,
    GoalPolicy,
)
from .core.outcome import AnalysisOutcome, ConcreteEvaluation
from .core.record_value import (
    FrozenValueMap,
    freeze_record_value,
    thaw_record_value,
)
from .core.reference import LoadedReference
from .core.refusal import Refusal, Refused
from .core.stimulus import canonical_json
from .cpu_admission import cpu_usage
from .evaluation_policy import verdict
from .generation import GeneratedStimulusSet
from .goal_policy import evaluation_goal_policy
from .result_store import CommittedDecision, ResultStore
from .scoring_policy import ScoringPolicy, active_scoring, scoring_scope
from .stimulus_set import GenerationSpec

__all__ = [
    "COMMAND_VERSION",
    "CommandRequest",
    "CommandResult",
    "CommandStatus",
    "analyse_command",
    "handle_command",
]


# Request identity binds admission, baseline, generation and goal policy.
# Persist the concrete decision before any optional analysis or pool reduction;
# those views cannot replace its evidence or score. Bump this version whenever
# an identical request can acquire different result semantics or record shape.
# Version 22 makes secondary-pool reduction independently opt-in and budgeted.
# v24 matches reset epochs and records explicitly canceled expected responses.
# v25 keeps the Driver's pending projection consistent with retaining resets.
# v26 binds explicit physical interface roles and composed protocol execution.
# v27 admits bounded AXI4-Lite channel assembly and its concrete obligations.
# v31 requires final external-memory agreement in addition to output matching.
# v32 exposes the committed scoring decision without a second evidence serialization.
# v42 keeps full native baseline goal feedback separate from query capabilities.
COMMAND_VERSION = "sim-command/52"


_COMMAND_REQUEST = object()
_COMMAND_RESULT = object()


class CommandStatus(enum.Enum):
    """The closed top-level outcomes of invoking the simulator handler.

    ``EVALUATED`` says the :class:`ConcreteEvaluation` was persisted; failures in later
    views or analysis remain diagnostics beside that result.  Artifact run failures are
    retained inside the evaluation and are not command-handler crashes.  ``REFUSED`` is
    a typed inability to run a requested phase.  ``INFRASTRUCTURE_FAILURE`` means the
    handler could not persist a completed evaluation (or failed before producing one).
    """

    EVALUATED = "evaluated"
    REFUSED = "refused"
    INFRASTRUCTURE_FAILURE = "infrastructure-failure"

    def __bool__(self):
        raise TypeError(
            "CommandStatus has no truth value; inspect the named status and the typed "
            "phase records")


def _type_name(value):
    return f"{type(value).__module__}.{type(value).__qualname__}"


def _field_image(value):
    """A total JSON image for a public identity field, including malformed ones."""

    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) in (tuple, list, set, frozenset):
        rows = [_field_image(item) for item in value]
        return sorted(rows, key=canonical_json) if isinstance(value, (set, frozenset)) \
            else rows
    return {"invalid_type": _type_name(value)}


def _plain_image(value):
    """Thaw only the closed JSON carriers used by command identity snapshots."""

    if type(value) is FrozenValueMap:
        return thaw_record_value(value)
    if isinstance(value, FrozenMap):
        return {key: _plain_image(item) for key, item in value.items()}
    if type(value) is dict:
        return {key: _plain_image(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_plain_image(item) for item in value]
    return value


def _provider_image(provider):
    """The provider's declared identity, not a second validation of its semantics."""

    from .deferred_generation import DeferredGenerationProvider

    if type(provider) is DeferredGenerationProvider:
        return provider.command_image()
    name = getattr(provider, "name", None)
    policy = getattr(provider, "policy", None)
    targets = getattr(provider, "targetable_goals", None)
    if type(name) is not str or not name.strip():
        raise Refused(Refusal(
            "sim-command-provider-name-invalid",
            "a generation provider needs one nonempty stable name"))
    if type(policy) is not str or not policy.strip():
        raise Refused(Refusal(
            "sim-command-provider-policy-invalid",
            "a generation provider needs one nonempty versioned policy"))
    if type(targets) not in (tuple, list) or any(
            type(goal) is not str or not goal for goal in targets):
        raise Refused(Refusal(
            "sim-command-provider-targets-invalid",
            "a generation provider needs one finite string target set",
            provider=name))
    manifest_of = getattr(provider, "catalogue_manifest", None)
    if not callable(manifest_of):
        raise Refused(Refusal(
            "sim-command-provider-catalogue-missing",
            "a command provider must expose the frozen manifest from which it selects "
            "targets",
            provider=_field_image(getattr(provider, "name", None))))
    manifest = manifest_of()
    if type(manifest) is not CatalogueManifest:
        raise TypeError(
            "a generation provider returned a non-CatalogueManifest")
    capability_of = getattr(provider, "goal_capability_use", None)
    capability = None
    if callable(capability_of):
        capability = capability_of()
        if capability is not None and type(capability) is not GoalCapabilityUse:
            raise TypeError(
                "a provider GoalPolicy handoff must be a GoalCapabilityUse")
        capability = (None if capability is None
                      else freeze_record_value(capability.as_dict()))
    return {
        # Python class paths are implementation layout, not provider semantics.  A
        # compatibility import or internal module split must not change this record.
        "name": name,
        "policy": policy,
        "targetable_goals": list(targets),
        "source_catalogue": manifest.digest(),
        "goal_capability_use": capability,
    }


def _reconcile_generation_sources(command, generated):
    """Bind each completed search to the provider image frozen by the command.

    A before/after check of a live capability is not enough: it can expose catalogue A
    while the command is built, use B for one search, then expose A again.  ``generate``
    records the catalogue it actually used, so compare that immutable record directly.
    This concerns source guidance only; ``generated.catalogue`` remains the independent
    evaluated-artifact denominator.
    """

    expected = {}
    from .deferred_generation import DeferredGenerationProvider

    for provider, frozen in zip(command._providers, command._provider_images):
        image = _plain_image(frozen)
        if type(provider) is DeferredGenerationProvider:
            if image != _plain_image(provider.command_image()):
                raise ValueError("a deferred search changed its frozen source recipe")
            image = _plain_image(provider.prepared_image())
        key = (image["name"], image["policy"])
        if key in expected:
            raise Refused(Refusal(
                "sim-command-provider-identity-duplicate",
                "generation providers need distinct name/policy identities"))
        targets = image["targetable_goals"]
        if type(targets) is not list or any(type(goal) is not str for goal in targets):
            raise Refused(Refusal(
                "sim-command-provider-targets-invalid",
                "a frozen generation provider has no finite string target set",
                provider=repr(key)))
        expected[key] = {
            "catalogue": image["source_catalogue"],
            "targets": sorted(targets),
            "goal_capability_use": image["goal_capability_use"],
        }

    actual = {}
    for search in generated.searches:
        telemetry = dict(search.telemetry.items())
        key = (search.provider, telemetry.get("provider_policy"))
        if key in actual:
            raise Refused(Refusal(
                "sim-command-generation-source-identity-duplicate",
                "completed searches do not have distinct provider/policy identities"))
        actual[key] = {
            "catalogue": telemetry.get("catalogue_digest"),
            "targets": list(telemetry.get("provider_targets", ())),
            "goal_capability_use": _plain_image(
                telemetry.get("goal_capability_use")),
        }

    if set(actual) != set(expected):
        raise Refused(Refusal(
            "sim-command-generation-source-set-mismatch",
            "the completed independent searches differ from the providers frozen in "
            "the command",
            expected=[list(key) for key in sorted(expected)],
            actual=[list(key) for key in sorted(actual)]))
    for key in sorted(expected):
        if actual[key] != expected[key]:
            raise Refused(Refusal(
                "sim-command-generation-source-mismatch",
                "an independent search used a source catalogue or target set different "
                "from the provider frozen in the command",
                provider=list(key), expected=expected[key], actual=actual[key]))


def _require_generated_handoff(command, generated):
    """Reject a generated set not derived from this command's frozen handoff.

    ``GeneratedStimulusSet`` is closed, but a perfectly valid set can belong to a
    different admitted invocation.  Checking only its catalogue would let a replaced
    facade function replay that other task before :class:`CommandResult` notices the
    splice.  The concrete phase therefore starts only after all run-level owners match
    by identity, including the exact baseline from which independent searches began.
    """

    if type(generated) is not GeneratedStimulusSet:
        raise Refused(Refusal(
            "sim-command-generation-result-not-typed",
            "generation did not return the closed GeneratedStimulusSet record",
            got=type(generated).__name__))
    if generated.task is not command.task:
        raise Refused(Refusal(
            "sim-command-generation-task-mismatch",
            "generation returned a set for another admitted task"))
    if generated.generation_spec is not command.generation_spec:
        raise Refused(Refusal(
            "sim-command-generation-spec-mismatch",
            "generation returned a set for another frozen generation policy"))
    if generated.catalogue != command.catalogue:
        raise Refused(Refusal(
            "sim-command-generation-catalogue-mismatch",
            "generation returned a different catalogue from the one frozen in the "
            "simulator command",
            command=command.catalogue.digest(),
            generated=generated.catalogue.digest()))
    if generated.baseline != command.baseline.stimuli:
        raise Refused(Refusal(
            "sim-command-generation-baseline-mismatch",
            "generation did not begin from the exact frozen command baseline"))


def _require_concrete_handoff(command, generated, evaluation):
    """Bind the score candidate to the one validated generation record.

    This check precedes :func:`verdict` and persistence.  A coherent evaluation for an
    equal contract is still evidence about the wrong run if it carries another
    ``AdmittedTask`` or another member set.
    """

    if type(evaluation) is not ConcreteEvaluation:
        raise Refused(Refusal(
            "sim-command-concrete-result-not-typed",
            "concrete replay did not return the closed ConcreteEvaluation record",
            got=type(evaluation).__name__))
    if evaluation.task is not command.task:
        raise Refused(Refusal(
            "sim-command-concrete-task-mismatch",
            "concrete replay returned a score for another admitted task"))
    if evaluation.catalogue != command.catalogue:
        raise Refused(Refusal(
            "sim-command-evaluation-catalogue-mismatch",
            "concrete evaluation returned a different catalogue from the one "
            "frozen before generation",
            command=command.catalogue.digest(),
            evaluated=evaluation.catalogue.digest()))
    suite = generated.replay_suite
    expected = generated.stimuli if suite is None else suite.stimuli
    if evaluation.stimuli != expected:
        raise Refused(Refusal(
            "sim-command-concrete-stimuli-mismatch",
            "concrete replay did not score the generated set and its derived replay phases"))
    required = generated.baseline if suite is None else suite.required_stimuli
    if evaluation.required_stimuli != required:
        raise Refused(Refusal(
            "sim-command-concrete-baseline-mismatch",
            "concrete replay changed the mandatory baseline testing scope"))


def _exact_capability_image(exact):
    """Canonical identities the bound provider promises cover its semantics."""

    if exact is None:
        return None
    try:
        domain = exact.domain.digest()
        analysis = exact.analysis_identity()
        evidence = exact.evidence_identity()
        manifest_of = getattr(exact.instance, "catalogue_manifest", None)
        if not callable(manifest_of):
            raise Refused(Refusal(
                "sim-command-exact-catalogue-missing",
                "the bounded provider must expose the exact frozen manifest it "
                "encodes; reconstructing one from a goals mapping is a second "
                "catalogue authority"))
        manifest = manifest_of()
        if type(manifest) is not CatalogueManifest:
            raise TypeError("the exact provider returned a non-CatalogueManifest")
        catalogue = manifest.digest()
        capability_of = getattr(exact.instance, "goal_capability_use", None)
        policy_of = getattr(exact.instance, "goal_policy", None)
        capability = capability_of() if callable(capability_of) else None
        goal_policy = policy_of() if callable(policy_of) else None
        if capability is not None and type(capability) is not GoalCapabilityUse:
            raise TypeError("the exact provider returned an invalid goal capability")
        if goal_policy is not None and type(goal_policy) is not GoalPolicy:
            raise TypeError("the exact provider returned an invalid GoalPolicy")
        if (capability is None) != (goal_policy is None):
            raise Refused(Refusal(
                "sim-command-exact-goal-capability-incomplete",
                "an exact provider carries its GoalPolicy and bounded-hit capability "
                "together"))
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - exact-provider identity boundary
        raise Refused(Refusal(
            "sim-command-exact-capability-unavailable",
            "the bounded provider could not supply the stable identities needed to "
            "bind it to this simulator command",
            error=f"{type(exc).__name__}: {exc}")) from None
    if type(domain) is not str or not domain \
            or type(analysis) is not str or not analysis \
            or type(evidence) is not dict \
            or set(evidence) != {"representation", "encoding"} \
            or any(type(value) is not str or not value
                   for value in evidence.values()) \
            or type(catalogue) is not str or not catalogue:
        raise Refused(Refusal(
            "sim-command-exact-capability-identity-invalid",
            "the bounded provider must supply a nonempty domain and analysis "
            "identity, exact representation and encoding identities, and a finite "
            "catalogue manifest",
            domain=repr(domain), analysis=repr(analysis),
            evidence=repr(evidence), catalogue=repr(catalogue)))
    return {
        "domain": domain,
        "analysis": analysis,
        "evidence": dict(evidence),
        "catalogue": catalogue,
        "goal_policy": (None if goal_policy is None else goal_policy.digest()),
        "goal_capability_use": (
            None if capability is None else capability.as_dict()),
    }


def _goal_policy_for_command(task, artifact, frontend_catalogue, *, exact=None,
                             analysis_request=None, goal_policy=None):
    """Bind the artifact to one frozen policy and its canonical denominator."""

    bound = task.binds(artifact)
    if bound is not True:
        raise Refused(Refusal(
            "sim-command-artifact-binding-mismatch",
            "the command artifact must be exactly the candidate named by the "
            "immutable admitted task",
            admitted=task.realization.identity or None,
            artifact=str(getattr(artifact, "identity", "") or "") or None))
    artifact_kind = getattr(artifact, "kind", None)
    if type(artifact_kind) is not str \
            or artifact_kind != task.realization.kind:
        raise Refused(Refusal(
            "sim-command-artifact-kind-mismatch",
            "an artifact with the admitted identity but another execution kind is "
            "not the capability admission checked",
            admitted=task.realization.kind,
            artifact=repr(artifact_kind)))

    carried = None
    if exact is not None:
        policy_of = getattr(exact.instance, "goal_policy", None)
        carried = policy_of() if callable(policy_of) else None
    elif analysis_request is not None:
        carried = getattr(analysis_request, "goal_policy", None)
    if goal_policy is not None:
        if type(goal_policy) is not GoalPolicy:
            raise TypeError("a prepared command carries the frozen GoalPolicy")
        if carried is not None and carried is not goal_policy:
            raise Refused(Refusal(
                "sim-command-goal-policy-replaced",
                "the command and its analysis request must retain the same prepared goal policy"))
        policy = goal_policy
    elif carried is not None:
        if type(carried) is not GoalPolicy:
            raise TypeError("an exact command carries the frozen GoalPolicy")
        policy = carried
    else:
        policy = evaluation_goal_policy(
            task.contract, frontend_catalogue,
            artifact_kind=task.realization.kind)

    # Only the RTL artifact is re-elaborated here.  Looking for a method by name on an
    # arbitrary device would let candidate code invent a second catalogue authority.
    from .rtl.artifact import RTLArtifact

    if type(artifact) is not RTLArtifact:
        return policy
    try:
        elaborated = artifact.frontend_catalogue(
            expected_identity=task.realization.identity)
    except Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - frontend boundary
        raise Refused(Refusal(
            "sim-command-frontend-catalogue-unavailable",
            "the artifact could not elaborate the structural manifest that must be "
            "frozen before generation",
            error=f"{type(exc).__name__}: {exc}")) from None
    if type(elaborated) is not CatalogueManifest:
        raise TypeError("the artifact frontend returned a non-CatalogueManifest")
    if elaborated != frontend_catalogue:
        raise Refused(Refusal(
            "sim-command-frontend-catalogue-mismatch",
            "the supplied frontend manifest differs from the one elaborated from the "
            "admitted artifact",
            supplied=frontend_catalogue.digest(), elaborated=elaborated.digest()))
    return policy


def _require_command_reference(task, reference):
    """Reject a bundle other than the one already frozen by admission."""

    if reference is not None and type(reference) is not LoadedReference:
        raise TypeError("a simulator command reference is a LoadedReference bundle")
    if reference is not None and reference.digest != task.reference:
        raise Refused(Refusal(
            "sim-command-reference-binding-mismatch",
            "the command reference must be the exact bundle frozen by admission",
            admitted=task.reference or None, reference=reference.digest))


def _require_command_baseline(task, generation_spec, baseline):
    """Bind one constructed baseline to this command's immutable run inputs."""

    if type(baseline) is not BaselineSet:
        raise TypeError("a simulator command needs the exact BaselineSet")
    if baseline.contract != task.contract.digest:
        raise Refused(Refusal(
            "sim-command-baseline-contract-mismatch",
            "the versioned baseline was built for a different task contract",
            command=task.contract.digest, baseline=baseline.contract))
    if baseline.generation_spec is not None and (
            generation_spec is None or baseline.generation_spec.identity != generation_spec.identity
            or baseline.generation_spec.as_dict() != generation_spec.as_dict()):
        raise Refused(Refusal(
            "sim-command-baseline-generation-spec-mismatch",
            "the versioned baseline was built for a different generation policy",
            command=None if generation_spec is None else generation_spec.identity,
            baseline=baseline.generation_spec.identity))
    policy = baseline.policy.identity
    for fingerprint, rows in baseline.provenance:
        if any(row.get("baseline_policy") != policy or row.get("of") != policy
               for row in rows):
            raise Refused(Refusal(
                "sim-command-baseline-policy-mismatch",
                "baseline provenance names a different construction policy from "
                "the exact policy carried by the baseline",
                baseline=policy, stimulus=fingerprint))


class CommandRequest(Frozen):
    """One immutable request to the simulator's public vertical slice.

    The admitted task is the exact output of the public admission phase; a command cannot
    split it back into a contract and realization and ask the handler to admit a possibly
    different pair.  Live artifacts and providers are capabilities and cannot themselves
    be serialized.  They are retained in private slots beside their declared identity
    images, which :func:`handle_command` rechecks before doing work.  Public callers build
    this record through a closed artifact handle.  The private in-memory-device seam exists
    only for verifier tests; it is not a public way to run an arbitrary live artifact.
    """

    __slots__ = (
        "_analysis_request",
        "_analysis_request_image",
        "_artifact",
        "_artifact_image",
        "_exact",
        "_exact_image",
        "_provider_images",
        "_providers",
        "_record_audit",
        "_reference",
        "analysis_bound",
        "analysis_policy",
        "baseline",
        "catalogue",
        "capture_analysis",
        "frontend_catalogue",
        "generation_spec",
        "goal_policy",
        "identity",
        "probe_budget",
        "run_analysis",
        "run_directory",
        "scoring_policy",
        "search_policy",
        "secondary_pool_budget",
        "task",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("CommandRequest is a closed simulator-boundary record")

    def __init__(self, *, run_directory, task, artifact, baseline,
                 providers, frontend_catalogue, generation_spec, probe_budget=None,
                 search_policy=None, scoring_policy=None,
                 analysis_bound=None,
                 run_analysis=True, capture_analysis=False,
                 secondary_pool_budget=0,
                 reference=None, exact=None, analysis_request=None, goal_policy=None,
                 record_audit=None, feasibility_timeout_ms=None,
                 query_timeout_ms=None, batch=None, analysis_wall_timeout_seconds=None, _token=None):
        if _token is not _COMMAND_REQUEST:
            raise TypeError(
                "use a closed artifact handle's command_request(); "
                "CommandRequest cannot splice raw frontend capabilities")
        if type(task) is not AdmittedTask:
            raise TypeError("a simulator command needs the exact admitted task")
        if generation_spec is not None and type(generation_spec) is not GenerationSpec:
            raise TypeError("generation_spec must be a GenerationSpec or absent")
        if generation_spec is None and (run_analysis or analysis_bound is not None):
            raise ValueError("bounded analysis requires an explicit GenerationSpec")
        if generation_spec is not None:
            from .behavior.transaction_analysis import BehaviorAnalysisRequest

            transactions = type(analysis_request) is BehaviorAnalysisRequest
            limit = (generation_spec.queue_bound if transactions
                     else generation_spec.horizon)
            unit = "transaction-batch" if transactions else "schedule-slot"
            if type(analysis_bound) is not int or not 1 <= analysis_bound <= limit:
                raise ValueError(
                    f"a simulator command analysis bound is a positive {unit} "
                    f"count no larger than its declared limit ({limit})")
        if type(frontend_catalogue) is not CatalogueManifest:
            raise TypeError(
                "a simulator command needs the frontend's exact CatalogueManifest; "
                "a device with no structural goals supplies an explicit empty one")
        # Applicability is decided once from the admitted realization kind.  The exact
        # provider's already-frozen manifest is carried from here onward, so generation,
        # replay and analysis share one object rather than equal reconstructions.
        goal_policy = _goal_policy_for_command(
            task, artifact, frontend_catalogue, exact=exact,
            analysis_request=analysis_request, goal_policy=goal_policy)
        if type(goal_policy) is not GoalPolicy:
            raise TypeError("the command policy builder returned no GoalPolicy")
        complete_catalogue = goal_policy.manifest
        _require_command_reference(task, reference)
        if exact is not None and analysis_request is not None:
            raise ValueError(
                "a command cannot carry both a bound exact provider and a lazy "
                "analysis request")
        complete_catalogue = analysis_service.catalogue_from_exact(
            task, exact, analysis_bound, frontend_catalogue,
            complete_catalogue)
        complete_catalogue = analysis_service.catalogue_from_request(
            task, artifact, analysis_request, generation_spec, analysis_bound,
            frontend_catalogue, complete_catalogue)
        if record_audit is not None and not callable(record_audit):
            raise TypeError("record_audit must be callable or absent")
        if record_audit is not None and exact is None \
                and analysis_request is None:
            raise ValueError(
                "a command record audit is a capability of its exact provider; it "
                "cannot be supplied without that bound provider")
        _require_command_baseline(task, generation_spec, baseline)
        search_policy = generation_service.validate_search_request(
            providers, probe_budget=probe_budget, search_policy=search_policy)
        if type(run_analysis) is not bool:
            raise TypeError("run_analysis must be an exact bool")
        if type(capture_analysis) is not bool:
            raise TypeError("capture_analysis must be an exact bool")
        if type(secondary_pool_budget) is not int or secondary_pool_budget < 0:
            raise ValueError("secondary_pool_budget must be a nonnegative exact integer")
        if not run_analysis and any(value is not None for value in (
                feasibility_timeout_ms, query_timeout_ms, batch, record_audit,
                analysis_wall_timeout_seconds)):
            raise ValueError(
                "a score-only command cannot carry bounded-analysis options")

        where = Path(run_directory)
        if not where.is_absolute() or where == Path("/") or not where.name:
            raise ValueError(
                "run_directory must be an explicit absolute invocation directory")
        self.run_directory = str(where)
        self.task = task
        self._artifact = artifact
        self.analysis_bound = analysis_bound
        self.baseline = baseline
        self._providers = tuple(providers)
        self.generation_spec = generation_spec
        self.frontend_catalogue = frontend_catalogue
        self.goal_policy = goal_policy
        self.catalogue = complete_catalogue
        self.probe_budget = probe_budget
        self.search_policy = search_policy
        self.scoring_policy = ScoringPolicy() if scoring_policy is None else scoring_policy
        if type(self.scoring_policy) is not ScoringPolicy:
            raise TypeError("scoring_policy must be ScoringPolicy")
        self.run_analysis = run_analysis
        self.capture_analysis = run_analysis or capture_analysis
        self.secondary_pool_budget = secondary_pool_budget
        self._reference = reference
        self._exact = exact
        self._analysis_request = analysis_request
        self._record_audit = record_audit
        if analysis_wall_timeout_seconds is not None and (
                type(analysis_wall_timeout_seconds) is not int or analysis_wall_timeout_seconds < 1):
            raise ValueError("analysis wall time must be positive integer seconds")
        if run_analysis and analysis_wall_timeout_seconds is None:
            raise ValueError("run_analysis requires explicit analysis_wall_timeout_seconds")
        self.analysis_policy = AnalysisPolicy(
            feasibility_timeout_ms=feasibility_timeout_ms,
            query_timeout_ms=query_timeout_ms, batch=batch,
            artifact_timeout_ms=analysis_wall_timeout_seconds * 1000 if run_analysis else None)

        self._artifact_image = FrozenMap({
            "kind": _field_image(getattr(artifact, "kind", None)),
            "identity": _field_image(getattr(artifact, "identity", None)),
        })
        self._provider_images = tuple(
            FrozenMap(_provider_image(provider)) for provider in self._providers)
        self._exact_image = (None if exact is None else canonical_json(
            _exact_capability_image(exact)))
        self._analysis_request_image = (
            None if analysis_request is None
            else canonical_json(analysis_request.as_dict()))
        image = self._image(include_identity=False)
        self.identity = hashlib.sha256(
            canonical_json(image).encode("utf-8")).hexdigest()
        self._freeze()

    def _image(self, *, include_identity):
        artifact = _plain_image(self._artifact_image) \
            if hasattr(self, "_artifact_image") else {}
        providers = [_plain_image(item)
                     for item in getattr(self, "_provider_images", ())]
        out = {
            "version": COMMAND_VERSION,
            "run_directory": self.run_directory,
            "task": {
                "contract": self.task.contract.as_dict(),
                "admission": self.task.as_dict(),
            },
            "artifact": artifact,
            "reference": (
                self._reference.digest if self._reference is not None else None),
            "baseline": self.baseline.as_dict(),
            "generation_spec": None if self.generation_spec is None else {
                "identity": self.generation_spec.identity,
                **self.generation_spec.as_dict(),
            },
            "analysis_bound": self.analysis_bound,
            "frontend_catalogue": {
                "digest": self.frontend_catalogue.digest(),
            },
            "catalogue": {
                "digest": self.catalogue.digest(),
            },
            "goal_policy": {
                "digest": self.goal_policy.digest(),
                "policy": self.goal_policy.as_dict(),
            },
            "probe_budget": self.probe_budget,
            "search_policy": None if self.search_policy is None else self.search_policy.as_dict(),
            "scoring_policy": self.scoring_policy.as_dict(),
            "secondary_pool": {
                "requested": self.secondary_pool_budget > 0,
                "max_candidate_checks": self.secondary_pool_budget,
            },
            "providers": providers,
            "analysis": {
                "requested": self.run_analysis,
                "capture_history": self.capture_analysis,
                "exact": (None if self._exact_image is None else
                          json.loads(self._exact_image)),
                "request": (None if self._analysis_request_image is None else
                            json.loads(self._analysis_request_image)),
                # A custom audit is a live capability whose semantics the exact
                # provider's analysis identity promises to cover.  A Python
                # module/qualname is intentionally not recorded: it is descriptive
                # text, not evidence that callable code stayed immutable.
                "record_audit": (None if self._record_audit is None else
                                 "exact-provider-capability"),
                "policy": self.analysis_policy.as_dict(),
            },
        }
        if include_identity:
            out["identity"] = self.identity
        return out

    def as_dict(self):
        return self._image(include_identity=True)

    def references_unchanged(self):
        """Whether live capability references still carry their frozen identities."""

        artifact = {
            "kind": _field_image(getattr(self._artifact, "kind", None)),
            "identity": _field_image(getattr(self._artifact, "identity", None)),
        }
        providers = tuple(
            _plain_image(_provider_image(item)) for item in self._providers)
        return (artifact == _plain_image(self._artifact_image)
                and providers == tuple(
                    _plain_image(item) for item in self._provider_images))

    def exact_capability_unchanged(self):
        """Whether the live bounded provider still has the recorded semantics."""

        if self._exact is None:
            exact_unchanged = True
        else:
            try:
                current = _exact_capability_image(self._exact)
            except Refused:
                return False
            exact_unchanged = canonical_json(current) == self._exact_image
        request_unchanged = (
            self._analysis_request is None
            or canonical_json(self._analysis_request.as_dict())
               == self._analysis_request_image)
        return exact_unchanged and request_unchanged


def _command_request(*, run_directory, task, artifact, baseline, providers,
                     frontend_catalogue, generation_spec, probe_budget=None, search_policy=None,
                     scoring_policy=None,
                     analysis_bound=None,
                     run_analysis=True, capture_analysis=False,
                     secondary_pool_budget=0,
                     reference=None, exact=None, analysis_request=None, goal_policy=None,
                     record_audit=None,
                     feasibility_timeout_ms=None, query_timeout_ms=None, batch=None,
                     analysis_wall_timeout_seconds=None):
    """Internal capability assembly behind closed artifact handles and test seams."""

    baseline = _materialize_baseline(
        task.contract, generation_spec, baseline, realization=task.realization)
    if analysis_bound is None and generation_spec is not None:
        analysis_bound = generation_spec.horizon

    return CommandRequest(
        run_directory=run_directory, task=task, artifact=artifact,
        baseline=baseline, providers=providers,
        frontend_catalogue=frontend_catalogue,
        generation_spec=generation_spec, probe_budget=probe_budget, search_policy=search_policy,
        scoring_policy=scoring_policy,
        analysis_bound=analysis_bound,
        run_analysis=run_analysis,
        capture_analysis=capture_analysis,
        secondary_pool_budget=secondary_pool_budget,
        reference=reference, exact=exact, analysis_request=analysis_request,
        goal_policy=goal_policy,
        record_audit=record_audit,
        feasibility_timeout_ms=feasibility_timeout_ms,
        query_timeout_ms=query_timeout_ms, batch=batch,
        analysis_wall_timeout_seconds=analysis_wall_timeout_seconds,
        _token=_COMMAND_REQUEST)


class CommandResult(Frozen):
    """The typed result of one handler invocation, with each phase kept distinct."""

    __slots__ = (
        "_failure_image", "admitted", "analysis", "authority_kind", "command",
        "decision", "evaluation", "failure", "generated", "invocation", "record_path", "status",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("CommandResult is a closed simulator-boundary record")

    def __init__(self, command, status, *, admitted=None, generated=None,
                 evaluation=None, analysis=None, authority_kind="", failure=None,
                 invocation="", record_path="", decision=None, _token=None):
        if _token is not _COMMAND_RESULT:
            raise TypeError(
                "use handle_command(); CommandResult is a closed persisted-result "
                "record")
        if type(command) is not CommandRequest:
            raise TypeError("a command result names the exact CommandRequest")
        if type(status) is not CommandStatus:
            raise TypeError("a command result status is the exact CommandStatus enum")
        self.command = command
        self.status = status
        if admitted is not None and type(admitted) is not AdmittedTask:
            raise TypeError("the admission phase result must be an AdmittedTask")
        if generated is not None and type(generated) is not GeneratedStimulusSet:
            raise TypeError("the generation phase result must be GeneratedStimulusSet")
        if evaluation is not None and type(evaluation) is not ConcreteEvaluation:
            raise TypeError("the concrete phase result must be ConcreteEvaluation")
        if analysis is not None and type(analysis) is not AnalysisOutcome:
            raise TypeError("the analysis phase result must be AnalysisOutcome")
        if generated is not None and admitted is None:
            raise ValueError("a generation phase result requires its admission record")
        if evaluation is not None and generated is None:
            raise ValueError("a concrete phase result requires its generated stimulus set")
        if analysis is not None and evaluation is None:
            raise ValueError("an analysis phase result requires its concrete evaluation")
        if analysis is not None and not command.run_analysis:
            raise ValueError(
                "a score-only command result cannot carry bounded analysis")
        if admitted is not None and admitted is not command.task:
            raise ValueError(
                "a command result must carry the exact admission frozen in its "
                "CommandRequest")
        if generated is not None:
            if generated.task is not admitted:
                raise ValueError(
                    "a command result generation phase names a different admission")
            if generated.generation_spec is not command.generation_spec:
                raise ValueError(
                    "a command result generation phase names a different generation "
                    "policy")
            if generated.catalogue != command.catalogue:
                raise ValueError(
                    "a command result generation phase names a different catalogue")
        if evaluation is not None:
            _require_concrete_handoff(command, generated, evaluation)
        if analysis is not None and analysis.concrete != evaluation.fingerprint:
            raise ValueError(
                "a command result analysis phase names a different concrete result")
        if self.status is CommandStatus.EVALUATED and evaluation is None:
            raise ValueError("an evaluated command result carries its concrete evaluation")
        if self.status is CommandStatus.REFUSED and type(failure) is not Refusal:
            raise TypeError("a refused command result carries the typed Refusal")
        if self.status is CommandStatus.INFRASTRUCTURE_FAILURE \
                and type(failure) is not dict:
            raise TypeError("an infrastructure failure carries structured diagnostics")
        if self.status is CommandStatus.EVALUATED and failure is not None \
                and type(failure) is not dict:
            raise TypeError(
                "a post-score command failure is structured infrastructure telemetry; "
                "artifact failures belong to the ConcreteEvaluation")
        if type(authority_kind) is not str:
            raise TypeError("authority_kind is an exact outcome string")
        if authority_kind not in ("", CONCRETE, PARTIAL, REFUSED):
            raise ValueError("authority_kind is not a simulator authority outcome")
        if self.status is CommandStatus.EVALUATED \
                and authority_kind not in (CONCRETE, PARTIAL):
            raise ValueError(
                "an evaluated command names its persisted concrete or partial authority")
        if self.status is CommandStatus.REFUSED \
                and authority_kind not in ("", REFUSED):
            raise ValueError("a refused command cannot name a non-refusal authority")
        if self.status is CommandStatus.INFRASTRUCTURE_FAILURE \
                and authority_kind not in ("", PARTIAL):
            raise ValueError(
                "an infrastructure failure has no authority or a persisted partial one")
        if type(invocation) is not str or type(record_path) is not str:
            raise TypeError("invocation and record_path are exact strings")
        if bool(invocation) != bool(record_path):
            raise ValueError(
                "invocation and record_path are either both present or both absent")
        if record_path and not Path(record_path).is_absolute():
            raise ValueError("a command result record_path is absolute")
        if decision is not None:
            if type(decision) is not CommittedDecision:
                raise TypeError("a command decision is the committed result-store view")
            if (decision.invocation != invocation or decision.record_path != record_path
                    or decision.kind != authority_kind):
                raise ValueError("the command and committed decision name different records")
            if evaluation is not None \
                    and decision.evaluation_fingerprint != evaluation.fingerprint:
                raise ValueError("the committed decision names a different evaluation")
        self.decision = decision
        self.admitted = admitted
        self.generated = generated
        self.evaluation = evaluation
        self.analysis = analysis
        self.authority_kind = authority_kind
        failure_image = (failure.as_dict() if type(failure) is Refusal
                         else (failure or {}))
        frozen_failure = freeze_record_value(
            failure_image, "command-result.failure")
        if type(frozen_failure) is not FrozenValueMap:
            raise TypeError("a command result failure image is a mapping")
        self._failure_image = canonical_json(failure_image)
        self.failure = failure if type(failure) is Refusal else frozen_failure
        self.invocation = invocation
        self.record_path = record_path
        self._freeze()

    def as_dict(self):
        failure = thaw_record_value(
            freeze_record_value(json.loads(self._failure_image)))
        return {
            "version": COMMAND_VERSION,
            "command": self.command.identity,
            "catalogue_manifest": self.command.catalogue.as_dict(),
            "catalogue_digest": self.command.catalogue.digest(),
            "status": self.status.value,
            "invocation": self.invocation or None,
            "record_path": self.record_path or None,
            "authority_kind": self.authority_kind or None,
            "decision": self.decision.as_dict() if self.decision is not None else None,
            "admission": self.admitted.as_dict() if self.admitted else None,
            "generation": (
                self.generated.as_record_reference() if self.generated else None),
            "concrete": (
                self.evaluation.as_record_reference() if self.evaluation else None),
            "analysis": self.analysis.as_dict() if self.analysis else None,
            "failure": failure or None,
        }


def _claim_directory(command):
    where = Path(command.run_directory)
    try:
        where.mkdir()
    except FileExistsError:
        return Refusal(
            "sim-command-run-directory-exists",
            "one simulator command requires a new explicit invocation directory; "
            "reusing a directory could mix its records with an earlier invocation",
            run_directory=str(where))
    except OSError as exc:
        return Refusal(
            "sim-command-run-directory-unavailable",
            "the simulator could not reserve its explicit invocation directory",
            run_directory=str(where), error=f"{type(exc).__name__}: {exc}")
    return where


def _result(command, status, store=None, **fields):
    record_path = ""
    if store is not None:
        candidate = (store.problem_dir / "runs" / store.invocation / "result.json")
        if candidate.is_file():
            record_path = str(candidate)
    return CommandResult(
        command, status,
        invocation=store.invocation if record_path else "",
        record_path=record_path,
        decision=store.committed_decision() if record_path else None,
        _token=_COMMAND_RESULT,
        **fields)


def _annotate_after_score(store, diagnostic):
    """Attach a handler failure without entering the verdict state machine again."""

    fields = {"command_failure": dict(diagnostic)}
    store.commit(report_update=fields, progress=fields)


def _attach_report_fields(store, **fields):
    """Persist derived views together, beside the terminal authority."""

    store.commit(report_update=fields, progress=fields)


def _persistence_failure(phase, exc, *, preceding=None):
    diagnostic = {
        "kind": "sim-command-persistence-failure",
        "phase": phase,
        "error": f"{type(exc).__name__}: {exc}",
    }
    if preceding is not None:
        diagnostic["preceding"] = preceding
    return diagnostic


def _decide(store, authority, phase, *, preceding=None):
    """Persist one decision, returning structured failure instead of raising."""

    try:
        store.decided(authority)
    except Exception as exc:  # noqa: BLE001 - the persistence boundary itself
        return _persistence_failure(phase, exc, preceding=preceding)
    return None


def _annotate(store, diagnostic):
    """Best-effort terminal annotation; never erase the original failure."""

    try:
        _annotate_after_score(store, diagnostic)
    except Exception as exc:  # noqa: BLE001 - the persistence boundary itself
        return _persistence_failure(
            str(diagnostic.get("phase") or "annotation"), exc,
            preceding=dict(diagnostic))
    return diagnostic


def _standing_kind(store):
    """The persisted kind, or empty when no terminal authority can be read."""

    try:
        if not store.snapshot().terminal:
            return ""
        return str(store.standing_authority_summary().get("kind") or "")
    except Exception:  # noqa: BLE001 - diagnostics must not invent persistence
        return ""


def _require_unchanged_references(command, phase):
    if not command.references_unchanged():
        raise Refused(Refusal(
            "sim-command-capability-reference-changed",
            "the artifact or generation provider changed its declared identity "
            "after this immutable command was constructed",
            phase=phase))


def _require_unchanged_exact_capability(command, phase):
    if not command.exact_capability_unchanged():
        raise Refused(Refusal(
            "sim-command-exact-capability-changed",
            "the exact provider's domain, transition-system identity, finite "
            "representation, encoding or goal catalogue changed after this immutable "
            "command was constructed",
            phase=phase))


def _analysis_inputs(command, evaluation):
    """Check the frozen command, then delegate frontend selection to its service."""
    request = command._analysis_request
    if request is None:
        _require_unchanged_exact_capability(command, "analysis-capability-check")
        return command._exact, None, None
    if not command.exact_capability_unchanged():
        return None, Refusal(
            "sim-command-analysis-request-changed",
            "the frozen exact analysis request changed after the command was built"), None
    from .production_generation import reuse_rtl_request

    try:
        request = reuse_rtl_request(request, command._providers)
        return analysis_service.bind_request(
            request, task=command.task, analysis_bound=command.analysis_bound,
            frontend_catalogue=command.frontend_catalogue,
            catalogue=command.catalogue)
    except Refused as exc:
        return None, exc.refusal, None
    except Exception as exc:  # noqa: BLE001 - post-score exact boundary
        return None, Refusal(
            "sim-command-exact-construction-failed",
            "the post-score exact request raised before producing a bound "
            "capability",
            error=f"{type(exc).__name__}: {exc}"), None


def analyse_command(result, *, feasibility_timeout_ms=None, query_timeout_ms=None, batch=None,
                    wall_timeout_seconds=None):
    """Analyse a completed command without rerunning generation or concrete replay.

    The caller retains this ancillary outcome separately. The original command,
    persisted score and evaluation are unchanged; source lowering and all exact
    work remain owned by the same frontend capability prepared before generation.
    A Behavior handle must have been prepared with ``bounded_analysis=True``.
    """
    from .analysis_report import analyse_evaluation
    from .analysis_service import _coordinator_failure

    if type(result) is not CommandResult or result.status is not CommandStatus.EVALUATED:
        raise TypeError("analyse_command requires an evaluated CommandResult")
    command, evaluation = result.command, result.evaluation
    if wall_timeout_seconds is None and command.analysis_policy.artifact_timeout_ms is not None:
        wall_timeout_seconds = command.analysis_policy.artifact_timeout_ms / 1000
    try:
        with analysis_scope(wall_timeout_seconds):
            try:
                exact, refusal, request = _analysis_inputs(command, evaluation)
            except Refused as exc:
                exact, refusal, request = None, exc.refusal, None
            return analyse_evaluation(
                evaluation, exact=exact, exact_refusal=refusal, analysis_request=request,
                record_audit=command._record_audit, goal_policy=command.goal_policy,
                feasibility_timeout_ms=feasibility_timeout_ms,
                query_timeout_ms=query_timeout_ms, batch=batch)
    except (Exception, AnalysisStopped) as exc:  # noqa: BLE001 -- cannot replace the score
        return _coordinator_failure(evaluation, exc, feasibility_timeout_ms=feasibility_timeout_ms,
                                    query_timeout_ms=query_timeout_ms, batch=batch)


def handle_command(command):
    """Execute one typed simulator request solely through the public phase facade."""

    if type(command) is not CommandRequest:
        raise TypeError("handle_command() requires the exact CommandRequest type")
    from .rtl.replay_build import replay_build_scope

    with scoring_scope(command.scoring_policy), replay_build_scope(command._artifact):
        return _handle_command(command)


def _handle_command(command):
    claimed = _claim_directory(command)
    if type(claimed) is Refusal:
        return _result(command, CommandStatus.REFUSED, failure=claimed)

    store = ResultStore(claimed, progress={
        "command": command.as_dict(),
        "command_version": COMMAND_VERSION,
        "phase_reached": "reserved",
    })
    # Admission is an immutable input handoff, not a phase this handler is permitted to
    # repeat with a separately supplied contract and realization.
    admitted = command.task
    generated = evaluation = None
    baseline_evaluation = None
    # Query-boundary snapshots serve symbolic queries, not concrete scoring or
    # native goal hits. A score-only command with disabled search has no reader
    # for them. Keep them for search and explicit analysis; direct replay retains
    # its existing observation profile.
    capture_query_boundaries = "history" if command.capture_analysis else (
        command.probe_budget != 0 and (
            command.search_policy is None or (
                command.search_policy.time_budget_s != 0
                and command.search_policy.max_probes != 0)))
    execution = evaluation_service.ReplayExecution(
        admitted, command._artifact, command._reference, command.catalogue,
        capture_query_boundaries=capture_query_boundaries,
        # Continuous baseline contributes native hits but is not a search seed
        # (generation keeps the isolated seed queue). Avoid taking a potentially
        # large symbolic state image at every continuous transaction for no
        # consumer. Explicit analysis retains its full observation profile.
        query_boundary_phases=None if command.capture_analysis else ("isolated",))
    phase = "store-begin"
    try:
        store.begin()
        phase = "admission-handoff"
        store.record(phase_reached=phase, admission=admitted.as_dict())
        phase = "reference-check"
        _require_unchanged_references(command, phase)

        # Baseline is required scoring work, not a Select side effect. Complete it
        # outside the search clock; retained run assessments also serve final scoring.
        phase = "baseline-replay"
        store.record(phase_reached=phase)
        baseline_tests = command.baseline.stimuli
        if all(s.replay_policy is not None for s in baseline_tests):
            from .replay_suite import build_replay_suite

            baseline_tests = build_replay_suite(admitted.contract, baseline_tests)
        baseline_evaluation = execution.evaluate(baseline_tests, stop_on_failure=True)
        # Search adds tests; it cannot repair an established baseline failure.
        # A verifier/reference failure still establishes no candidate score.
        search_stop = None if baseline_evaluation.complete else "baseline-incomplete"
        if baseline_evaluation.budget_exhausted:
            search_stop = "scoring-budget-exhausted"
        if (not baseline_evaluation.passed
                and (baseline_evaluation.complete or baseline_evaluation.failure_witness is not None)
                and verdict(baseline_evaluation).authoritative):
            search_stop = "baseline-failed"
        store.record(baseline={
            "fingerprint": baseline_evaluation.fingerprint,
            "complete": baseline_evaluation.complete,
            "passed": baseline_evaluation.passed,
            "hits": list(baseline_evaluation.hits),
            "safety_hits": list(baseline_evaluation.safety_hits),
            "runs": len(baseline_evaluation.results),
            "unexecuted_stimuli": [s.fingerprint for s in baseline_evaluation.unexecuted],
            "failure_witness": baseline_evaluation.failure_witness,
            "execution_cost": baseline_evaluation.execution_cost,
        })

        phase = "generation"
        store.record(phase_reached=phase)
        candidate_generated = generation_service.generate_stimuli(
            admitted, command.baseline.stimuli, command._providers,
            catalogue=command.catalogue,
            generation_spec=command.generation_spec,
            probe_budget=command.probe_budget,
            search_policy=command.search_policy,
            baseline_policy=command.baseline.policy.identity,
            _execution=execution,
            _search_stop=search_stop)
        _require_generated_handoff(command, candidate_generated)
        generated = candidate_generated
        if generated.baseline_policy != command.baseline.policy.identity:
            raise Refused(Refusal(
                "sim-command-generation-baseline-policy-mismatch",
                "generation returned a baseline policy different from the exact "
                "policy frozen in the simulator command",
                command=command.baseline.policy.identity,
                generated=generated.baseline_policy))
        _reconcile_generation_sources(command, generated)
        _require_unchanged_references(command, phase)
        store.record(
            phase_reached=phase,
            generation=generated.as_record_reference())

        # Search is finished. New final-scoring runs have no local-query
        # consumer; native hits, protocol checks and comparisons remain on.
        # Keep the extra state image only for requested post-score analysis.
        execution.capture_query_boundaries = "history" if command.capture_analysis else False
        phase = "concrete-evaluation"
        candidate_evaluation = evaluation_service.evaluate_generated(
            generated, command._artifact, reference=command._reference,
            telemetry={"simulator_command": command.identity}, _execution=execution,
            stop_on_failure=True)
        _require_concrete_handoff(command, generated, candidate_evaluation)
        evaluation = candidate_evaluation
        _require_unchanged_references(command, phase)

        # No further scoring work. Give persistence/cleanup their own lifecycle;
        # optional analysis does not inherit an exhausted concrete-work deadline.
        active_scoring().finish()

        # This is the command boundary's point of no return.  Persist the evaluation
        # before deriving any optional view: a reducer that hangs or kills this process
        # must leave the completed score in this invocation's authoritative document.
        authority = verdict(evaluation)
        store.record(phase_reached=phase,
                     concrete_fingerprint=evaluation.fingerprint)
        persistence = _decide(store, authority, phase)
        if persistence is not None:
            if store.snapshot().terminal:
                return _result(
                    command, CommandStatus.EVALUATED, store,
                    admitted=admitted, generated=generated, evaluation=evaluation,
                    authority_kind=_standing_kind(store), failure=persistence)
            return _result(
                command, CommandStatus.INFRASTRUCTURE_FAILURE, store,
                admitted=admitted, generated=generated, evaluation=evaluation,
                authority_kind=_standing_kind(store), failure=persistence)

        phase = "secondary-witness-pool"
        pool_telemetry = (
            witness_pool.secondary_pool_report(
                evaluation, max_checks=command.secondary_pool_budget)
            if command.secondary_pool_budget else {"status": "not-requested"})
        pool_telemetry = {
            **pool_telemetry,
            "max_candidate_checks": command.secondary_pool_budget,
        }
        if not command.run_analysis:
            phase = "analysis-not-requested"
            try:
                _attach_report_fields(
                    store,
                    secondary_witness_pool=pool_telemetry,
                    analysis_status="not-requested",
                )
            except Exception as exc:  # noqa: BLE001 - post-score persistence boundary
                diagnostic = _persistence_failure(phase, exc)
                diagnostic = _annotate(store, diagnostic)
                return _result(
                    command, CommandStatus.EVALUATED, store,
                    admitted=admitted, generated=generated,
                    evaluation=evaluation, authority_kind=authority.kind,
                    failure=diagnostic)
            return _result(
                command, CommandStatus.EVALUATED, store,
                admitted=admitted, generated=generated, evaluation=evaluation,
                authority_kind=authority.kind)

        try:
            _attach_report_fields(
                store, secondary_witness_pool=pool_telemetry)
        except Exception as exc:  # noqa: BLE001 - post-score persistence boundary
            diagnostic = _persistence_failure(phase, exc)
            diagnostic = _annotate(store, diagnostic)
            return _result(
                command, CommandStatus.EVALUATED, store,
                admitted=admitted, generated=generated, evaluation=evaluation,
                authority_kind=authority.kind, failure=diagnostic)

        policy = command.analysis_policy
        try:
            with analysis_scope(None if policy.artifact_timeout_ms is None
                                else policy.artifact_timeout_ms / 1000):
                phase = "analysis-capability-check"
                exact, exact_refusal, direct_analysis_request = _analysis_inputs(command, evaluation)
                phase = "bounded-analysis"
                analysis = analyse_after_decision(
                    store, authority, exact=exact,
                    exact_refusal=exact_refusal,
                    analysis_request=direct_analysis_request,
                    record_audit=command._record_audit,
                    goal_policy=command.goal_policy,
                    feasibility_timeout_ms=policy.feasibility_timeout_ms,
                    query_timeout_ms=policy.query_timeout_ms, batch=policy.batch)
        except AnalysisStopped as exc:
            from .analysis_service import _coordinator_failure

            analysis = _coordinator_failure(evaluation, exc,
                feasibility_timeout_ms=policy.feasibility_timeout_ms,
                query_timeout_ms=policy.query_timeout_ms, batch=policy.batch)
            store.attach_analysis(analysis)
        except Refused:
            raise  # Preserve the existing typed post-decision refusal boundary.
        except Exception as exc:  # noqa: BLE001 - post-score coordination boundary
            diagnostic = {
                "kind": "sim-command-analysis-handoff-failed",
                "phase": phase,
                "error": f"{type(exc).__name__}: {exc}",
            }
            diagnostic = _annotate(store, diagnostic)
            return _result(
                command, CommandStatus.EVALUATED, store,
                admitted=admitted, generated=generated, evaluation=evaluation,
                authority_kind=authority.kind, failure=diagnostic)
        return _result(
            command, CommandStatus.EVALUATED, store,
            admitted=admitted, generated=generated, evaluation=evaluation,
            analysis=analysis, authority_kind=authority.kind)

    except Refused as exc:
        refusal = exc.refusal
        if (phase == "generation" and baseline_evaluation is not None
                and command.search_policy is not None and command.search_policy.debug):
            # A diagnostic stop has no final score. Keep the actual baseline
            # evidence, not merely a success flag or a now-unresolvable digest.
            store.record(baseline_evaluation=baseline_evaluation.as_dict())
        if store.snapshot().terminal:
            diagnostic = {
                "kind": "sim-command-post-decision-refusal",
                "phase": phase,
                "refusal": refusal.as_dict(),
            }
            diagnostic = _annotate(store, diagnostic)
            standing = store.standing_authority_summary()
            return _result(
                command, CommandStatus.EVALUATED, store,
                admitted=admitted, generated=generated, evaluation=evaluation,
                authority_kind=str(standing.get("kind") or ""),
                failure=diagnostic)
        authority = Authoritative(
            REFUSED, why=refusal.kind, evaluation=evaluation,
            refusals=(refusal,), telemetry={"command_phase": phase})
        persistence = _decide(
            store, authority, phase, preceding=refusal.as_dict())
        if persistence is not None:
            status = (CommandStatus.EVALUATED if store.snapshot().terminal
                      and evaluation is not None
                      else CommandStatus.INFRASTRUCTURE_FAILURE)
            return _result(
                command, status, store,
                admitted=admitted, generated=generated, evaluation=evaluation,
                authority_kind=_standing_kind(store), failure=persistence)
        return _result(
            command, CommandStatus.REFUSED, store,
            admitted=admitted, generated=generated, evaluation=evaluation,
            authority_kind=authority.kind, failure=refusal)
    except Exception as exc:  # noqa: BLE001 - outer command/tool boundary
        diagnostic = {
            "kind": "sim-command-infrastructure-failure",
            "phase": phase,
            "error": f"{type(exc).__name__}: {exc}",
        }
        if isinstance(exc, FailureRaised):
            diagnostic["run_failure"] = exc.failure.as_dict()
        resources = cpu_usage()
        if resources is not None:
            diagnostic["cpu_admission"] = resources
        if store.snapshot().terminal:
            diagnostic = _annotate(store, diagnostic)
            standing = store.standing_authority_summary()
            return _result(
                command, CommandStatus.EVALUATED, store,
                admitted=admitted, generated=generated, evaluation=evaluation,
                authority_kind=str(standing.get("kind") or ""),
                failure=diagnostic)
        authority = Authoritative(
            PARTIAL,
            why="the simulator command failed before it produced a complete result",
            evaluation=evaluation, telemetry={"command_failure": diagnostic})
        persistence = _decide(store, authority, phase, preceding=diagnostic)
        if persistence is not None:
            diagnostic = persistence
        return _result(
            command, CommandStatus.INFRASTRUCTURE_FAILURE, store,
            admitted=admitted, generated=generated, evaluation=evaluation,
            authority_kind=(authority.kind if persistence is None
                            else _standing_kind(store)),
            failure=diagnostic)
    finally:
        execution.release()
