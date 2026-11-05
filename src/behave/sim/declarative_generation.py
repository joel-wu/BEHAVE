"""Task-specific realization of declarative guidance as candidate stimuli.

The declarative goal table can produce a semantic witness, but it cannot know how a
particular task stores that witness in endpoint queues.  This module is the narrow
bridge between those two facts.  A trusted task extension maps a witness to existing
queue fields and realizes one candidate; the common generation scheduler still owns
request validation, domain membership, mutation bounds, deduplication, provenance and
the later canonical replay.  Consequently this bridge can improve or waste search
effort, but it cannot publish a goal hit.
"""

from __future__ import annotations

from .core.frozen import Frozen
from .core.goal_provider import (
    LOCAL_GUIDANCE,
    DeclarativeGuidanceAdapter,
    DeclarativeGuidanceWitness,
    GoalPolicy,
    GoalProvider,
)
from .core.refusal import Refusal, Refused
from .core.stimulus import Stimulus
from .generation_protocol import (
    Outcome,
    ProbeRequest,
    ProbeResult,
    ProbeSelection,
    Scope,
    StimulusField,
    stimulus_bytes,
)
from .generation_cache import GenerationCacheTelemetry
from .generation_feedback import selection_targets

__all__ = [
    "DeclarativeCandidateMapping",
    "DeclarativeCandidateRealizer",
    "DeclarativeGenerationProvider",
]


class DeclarativeCandidateMapping(Frozen):
    """The finite part of one semantic witness that a task permits changing.

    The task extension identifies an occurrence and existing queue coordinates.  It
    does not provide a hit, a status, a new schedule, or an alternative catalogue.
    ``DeclarativeGenerationProvider`` turns this record into a ``ProbeRequest``; the
    common scheduler validates every coordinate against the seed before probing.
    """

    __slots__ = ("mutable_fields", "occurrence")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("DeclarativeCandidateMapping is a closed boundary record")

    def __init__(self, occurrence, mutable_fields):
        if type(occurrence) is not str or not occurrence.strip():
            raise TypeError(
                "a declarative candidate mapping needs a nonempty occurrence")
        if type(mutable_fields) not in (tuple, list) or not mutable_fields:
            raise TypeError(
                "a declarative candidate mapping needs a nonempty finite field list")
        fields = tuple(mutable_fields)
        if any(type(field) is not StimulusField for field in fields):
            raise TypeError(
                "a declarative candidate mapping contains exact StimulusField records")
        fields = tuple(sorted(
            fields,
            key=lambda field: (
                field.endpoint, field.transaction, field.field,
                field.source_position),
        ))
        if len({field.coordinate for field in fields}) != len(fields):
            raise ValueError(
                "a declarative candidate mapping names one queue field twice")
        self.occurrence = occurrence
        self.mutable_fields = fields
        self._freeze()

    def as_dict(self):
        return {
            "occurrence": self.occurrence,
            "mutable_fields": [field.as_dict() for field in self.mutable_fields],
        }


class DeclarativeCandidateRealizer(Frozen):
    """A versioned task extension from semantic witnesses to candidate stimuli.

    ``mapping`` chooses existing seed fields.  ``candidate`` may change only those
    fields and must return a complete :class:`Stimulus`.  Both callbacks are candidate
    generation, not hit evidence: their result is checked by the common scheduler and
    later replayed by the ordinary evaluation path.
    """

    __slots__ = ("_candidate", "_mapping", "implementation")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("DeclarativeCandidateRealizer is a closed public adapter")

    def __init__(self, implementation, mapping, candidate):
        if type(implementation) is not str or not implementation.strip() \
                or "/" not in implementation:
            raise TypeError(
                "a declarative candidate realizer needs a versioned identity")
        if not callable(mapping) or not callable(candidate):
            raise TypeError(
                "a declarative candidate realizer needs mapping and candidate callables")
        self.implementation = implementation
        self._mapping = mapping
        self._candidate = candidate
        self._freeze()

    def mapping(self, seed, witness):
        if type(seed) is not Stimulus \
                or type(witness) is not DeclarativeGuidanceWitness:
            raise TypeError(
                "declarative realization needs an exact seed and semantic witness")
        got = self._mapping(seed, witness)
        if isinstance(got, Refusal):
            return got
        if type(got) is not DeclarativeCandidateMapping:
            return Refusal(
                "declarative-generation-mapping-not-typed",
                "the task extension did not return an exact candidate mapping",
                got=type(got).__name__)
        return got

    def candidate(self, seed, witness, mapping):
        if type(seed) is not Stimulus \
                or type(witness) is not DeclarativeGuidanceWitness \
                or type(mapping) is not DeclarativeCandidateMapping:
            raise TypeError(
                "declarative realization needs exact seed, witness and mapping records")
        got = self._candidate(seed, witness, mapping)
        if isinstance(got, Refusal):
            return got
        if type(got) is not Stimulus:
            return Refusal(
                "declarative-generation-candidate-not-a-stimulus",
                "the task extension did not return a complete exact Stimulus",
                got=type(got).__name__)
        return got


class DeclarativeGenerationProvider(Frozen):
    """Expose one declarative provider through the common generation scheduler.

    The frozen ``GoalPolicy`` proves that the semantic guidance is the registered
    local-guidance adapter for this catalogue.  The task realizer only supplies the
    physical queue mapping.  Requests are ranked by the guidance adapter's canonical
    goal order; the extension cannot introduce a hidden worklist policy.
    """

    VERSION = "declarative-candidate-bridge/1"
    __slots__ = (
        "_catalogue", "_goal_capability", "_guidance", "_realizer",
        "_selected", "name", "policy", "targetable_goals",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("DeclarativeGenerationProvider is a closed public provider")

    def __init__(self, goal_policy, provider, realizer):
        if type(goal_policy) is not GoalPolicy:
            raise TypeError(
                "declarative generation needs the exact frozen GoalPolicy")
        if type(provider) is not GoalProvider:
            raise TypeError(
                "declarative generation needs an exact GoalProvider")
        if type(realizer) is not DeclarativeCandidateRealizer:
            raise TypeError(
                "declarative generation needs a DeclarativeCandidateRealizer")
        guidance = provider.guidance
        if type(guidance) is not DeclarativeGuidanceAdapter:
            raise Refused(Refusal(
                "declarative-generation-guidance-unavailable",
                "the selected goal provider has no executable declarative guidance",
                provider=provider.identity.key))
        binding = provider.binding(LOCAL_GUIDANCE)
        if binding is None or binding.implementation != guidance.implementation:
            raise Refused(Refusal(
                "declarative-generation-guidance-binding-mismatch",
                "the provider's executable guidance and capability row differ",
                provider=provider.identity.key))
        # Identity, adapter object, occurrences and enabled-policy membership are all
        # checked here.  A matching implementation string alone is not authority.
        goal_policy.reconcile(
            provider.identity, LOCAL_GUIDANCE, binding.adapter)
        targets = tuple(guidance.catalogue)
        capability = goal_policy.bind_implementation(
            LOCAL_GUIDANCE, guidance.implementation, goal_policy.manifest,
            targets, exhaustive=True)

        self._catalogue = goal_policy.manifest
        self._goal_capability = capability
        self._guidance = guidance
        self._realizer = realizer
        self._selected = {}
        self.targetable_goals = tuple(sorted(targets))
        # ``policy`` is the registered semantic-witness ordering policy.  The physical
        # task mapping and this bridge's semantics are retained separately in the
        # provider identity.
        self.policy = guidance.implementation
        self.name = (
            f"declarative/{self.VERSION}/{provider.identity.key}/"
            f"{realizer.implementation}")
        self._freeze()

    def catalogue_manifest(self):
        return self._catalogue

    def cache_telemetry(self):
        return GenerationCacheTelemetry()

    def goal_capability_use(self):
        return self._goal_capability

    def select(self, seed, *, targets=None):
        if type(seed) is not Stimulus:
            return Refusal(
                "declarative-generation-seed-not-a-stimulus",
                "declarative Select needs one complete exact Stimulus")
        digest = self._catalogue.digest()
        targets = selection_targets(self.targetable_goals, targets)
        requests = []
        selected = {}
        for rank, witness in enumerate(self._guidance.witnesses):
            if witness.goal not in targets:
                continue
            try:
                mapping = self._realizer.mapping(seed, witness)
            except Refused as exc:
                return exc.refusal
            except Exception as exc:  # noqa: BLE001 - trusted extension boundary
                return Refusal(
                    "declarative-generation-mapping-failed",
                    "the task extension could not map a semantic witness to its seed",
                    goal=witness.goal,
                    error=f"{type(exc).__name__}: {exc}")
            if isinstance(mapping, Refusal):
                return mapping
            request = ProbeRequest(
                (witness.goal,), occurrence=mapping.occurrence,
                mutable_fields=mapping.mutable_fields, rank=rank,
                catalogue_digest=digest)
            requests.append(request)
            selected[request.identity_bytes] = (request, witness, mapping)
        self._selected[stimulus_bytes(seed)] = selected
        return ProbeSelection(requests, digest)

    def probe(self, seed, request):
        if type(seed) is not Stimulus or type(request) is not ProbeRequest:
            return ProbeResult(
                Outcome.UNSUPPORTED, Scope.PATH_PREFIX,
                detail="declarative Probe needs exact seed and request records")
        selected = self._selected.get(stimulus_bytes(seed), {})
        row = selected.get(request.identity_bytes)
        if row is None or row[0].as_dict() != request.as_dict():
            return ProbeResult(
                Outcome.UNSUPPORTED, Scope.PATH_PREFIX,
                detail="the request was not selected for this exact seed")
        _selected_request, witness, mapping = row
        try:
            candidate = self._realizer.candidate(seed, witness, mapping)
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - trusted extension boundary
            raise Refused(Refusal(
                "declarative-generation-candidate-failed",
                "the task extension could not realize its selected semantic witness",
                goal=witness.goal,
                error=f"{type(exc).__name__}: {exc}")) from None
        if isinstance(candidate, Refusal):
            raise Refused(candidate)
        return ProbeResult(
            Outcome.SAT, Scope.PATH_PREFIX,
            goal=witness.goal, candidate=candidate,
            detail=("candidate realization of a replay-validated declarative "
                    "witness; canonical replay remains hit authority"))
