"""Versioned goal-provider capabilities and declarative finite hit semantics.

The catalogue says *which* goals exist.  It does not say whether the build can
observe them concretely, use them for local generation, or encode them in an exact
bounded query.  Those are four independent capabilities; treating declaration as
proof of the other three is how an enabled but unsupported family silently entered a
denominator.

This module keeps that matrix outside the score.  A :class:`GoalPolicy` is frozen
before generation and derives one
:class:`~behave.sim.core.catalogue.CatalogueManifest` from its enabled providers.
Each consumer then requires only the capabilities it uses: concrete scoring needs
declaration and replay, bounded analysis additionally needs exact hit semantics, and
the release gate applies the schema's complete requirements. Disabled and
inapplicable schemas retain versioned reasons but contribute no goals. Incomplete
enumeration retains known instances and is reported separately from their per-goal
capabilities; it cannot pass release validation.

Task extensions use :class:`DeclarativeGoalSemantics`: finite truth-table predicates
and total finite-state monitors.  Each definition has one concrete evaluator and one
backend-neutral symbolic encoder over :class:`FiniteAlgebra`; a user does not supply
parallel trusted implementations.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from .catalogue import CatalogueManifest, catalogue_subset, merge_catalogues
from .finite_goal import (
    DeclarativeConcreteStep,
    DeclarativeGoalSemantics,
    DeclarativeSymbolicStep,
    FiniteAlgebra,
    FiniteMonitor,
    FiniteMonitorEncoding,
    FiniteMonitorStep,
    FiniteMonitorTransition,
    FinitePredicate,
    FinitePredicateEncoding,
    FiniteVariable,
    _canonical,
)
from .frozen import Frozen
from .goal_guidance import (
    DeclarativeGuidanceAdapter,
    DeclarativeGuidanceStep,
    DeclarativeGuidanceWitness,
)
from .refusal import Refusal, Refused

__all__ = [
    "BEHAVIOR_EXACT_BOUNDED_POLICY",
    "BEHAVIOR_LOCAL_GUIDANCE_POLICY",
    "CONCRETE_HIT",
    "CONCRETE_PHASE",
    "DECLARATION",
    "DISABLED",
    "ENABLED",
    "EXACT_ANALYSIS_PHASE",
    "EXACT_BOUNDED_HIT",
    "INAPPLICABLE",
    "INCOMPLETE",
    "LOCAL_GUIDANCE",
    "RELEASE_PHASE",
    "RTL_EXACT_BOUNDED_POLICY",
    "RTL_LOCAL_GUIDANCE_POLICY",
    "CapabilityAdapter",
    "CapabilityBinding",
    "DeclarativeConcreteStep",
    "DeclarativeGoalSemantics",
    "DeclarativeGuidanceAdapter",
    "DeclarativeGuidanceStep",
    "DeclarativeGuidanceWitness",
    "DeclarativeSymbolicStep",
    "FiniteAlgebra",
    "FiniteMonitor",
    "FiniteMonitorEncoding",
    "FiniteMonitorStep",
    "FiniteMonitorTransition",
    "FinitePredicate",
    "FinitePredicateEncoding",
    "FiniteVariable",
    "GoalCapabilityUse",
    "GoalPolicy",
    "GoalPolicySelection",
    "GoalProvider",
    "GoalProviderIdentity",
    "GoalProviderRegistry",
    "GoalSchemaDefinition",
    "VersionedReason",
    "standard_goal_schemas",
]


DECLARATION = "declaration"
CONCRETE_HIT = "concrete-hit"
LOCAL_GUIDANCE = "local-guidance"
EXACT_BOUNDED_HIT = "exact-bounded-hit"
CAPABILITIES = (
    DECLARATION,
    CONCRETE_HIT,
    LOCAL_GUIDANCE,
    EXACT_BOUNDED_HIT,
)

CONCRETE_PHASE = "concrete"
EXACT_ANALYSIS_PHASE = "exact-analysis"
RELEASE_PHASE = "release"
PHASES = (CONCRETE_PHASE, EXACT_ANALYSIS_PHASE, RELEASE_PHASE)

# Implementation identities shared by the capability matrix and the actual production
# providers.  They are not evidence by themselves: :meth:`GoalPolicy.bind_implementation`
# below creates the closed policy-to-provider handoff which makes the identity useful.
BEHAVIOR_LOCAL_GUIDANCE_POLICY = (
"behavior-isolated-exact-local/90:concrete-path-specialization+loop-extension-before-address-novelty+reached-state-write-continuations+writer-only-all-contexts+persistent-array-local-queries+preserved-memory-prefix+first-consumer-windows+shared-preparation-predicates+consumer-read-address-frontiers+incremental-observed-input-pool+prepared-state-before-new-setup+observed-writer-branch-diversity+array-setup-before-trigger-history+native-write-address-frontiers+source-cfg-state-preparation+definite-alternative-bindings+binding-closed-state-alternatives+replay-setup-contexts+source-ranked-state-transfer+backward-trigger-windows+attempted-state-contexts+nonrestricting-observations+sealed-source-loop-frontiers+live-dynamic-frontiers+trigger-anchored-setup+longest-native-prefix+prepared-context-first+dynamic-context-frontiers+diverse-observed-state-writers+transport-stable-preparation-plans+shared-immutable-affine-facts+compact-occurrence-components+diverse-observed-command-recipes+shared-baseline-writers+control-frontier-preparation+shared-loop-free-preparation+all-window-native-frontiers+joint-native-path-prefix+bound-frontier-definitions+demand-driven-guarded-reads+retained-planning-continuation+native-entry-first+payload-local-continuation+bound-preparation-recipes+native-writer-preparation+native-cell-ranges+bound-local-planning+requested-target-construction+initialize-before-fill+command-preparation-paths+dependency-first-families+ordered-state-dependencies+local-to-joint-continuation+position-dependent-queries+compact-query-plans+replay-residual+state-dependency-chains+occurrence-search+joint-state-windows+shared-window-prefixes+incremental-formula-registration+bound-input-plans+occurrence-field-widening+scope-owned-progress+cooperative-construction+"
    "coverage-before-safety+literal-false-selection+transaction-observations+operation-predicates+"
    "atomic-audited-slot+run-owned-memory+event-only-input+fixed-helper-binding+fixed-record-construction+audited-cone-carriers+representation-completion")
RTL_LOCAL_GUIDANCE_POLICY = (
    "rtl-exact-local/30:persistent-array-local-queries+joint-input-windows+retained-source-boundary+demand-driven-guarded-reads+retained-planning-continuation+native-cell-ranges+bound-local-planning+replay-residual+source-before-product+scope-owned-construction+cooperative-construction+constant-boolean-template-folding+source-dynamic-interval+schedule-monitor-projection+complete-product-state+"
    "registered-adapter-table+transaction-history+access-domain-proof+operation-predicates+audited-cone-carriers+representation-completion")
BEHAVIOR_EXACT_BOUNDED_POLICY = "behavior-bounded-hit/3"
RTL_EXACT_BOUNDED_POLICY = "rtl-task-bounded-hit/3"

ENABLED = "enabled"
DISABLED = "disabled"
INAPPLICABLE = "inapplicable"
INCOMPLETE = "incomplete"
DISPOSITIONS = (ENABLED, DISABLED, INAPPLICABLE, INCOMPLETE)

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*$")
_POLICY_VERSION = "goal-capability-policy/5"
_REGISTRY_TOKEN = object()


def _name(value, what):
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(
            f"{what} must be a stable lowercase dot/kebab identifier")
    return value


def _version(value, what):
    if type(value) is not int or value < 1:
        raise TypeError(f"{what} must be a positive exact integer")
    return value


def _policy(value, what="policy identity"):
    if type(value) is not str or not value or "/" not in value:
        raise ValueError(f"{what} must be a nonempty versioned identity")
    return value


class GoalProviderIdentity(Frozen):
    """Stable identity of one implementation of one goal schema."""

    __slots__ = ("name", "version")

    def __init__(self, name, version):
        self.name = _name(name, "goal-provider name")
        self.version = _version(version, "goal-provider version")
        self._freeze()

    @property
    def key(self):
        return f"{self.name}/v{self.version}"

    def as_dict(self):
        return {"name": self.name, "version": self.version}

    def __eq__(self, other):
        return type(other) is GoalProviderIdentity and self.key == other.key

    def __hash__(self):
        return hash((GoalProviderIdentity, self.key))


class CapabilityAdapter(Frozen):
    """Closed callable catalogue/occurrence adapter for one capability.

    A version string alone cannot establish that a provider has an adapter.  This
    record owns the provider's exact catalogue slice and checks both its public goal
    rows and stable occurrence relation when invoked.  Instances are made only while
    constructing a :class:`CapabilityBinding`; a policy subsequently requires that
    very registered instance rather than accepting a caller-echoed catalogue.
    """

    __slots__ = ("catalogue", "implementation", "kind")

    def __init_subclass__(cls, **kwargs):
        raise TypeError("CapabilityAdapter is a closed capability record")

    def __init__(self, kind, implementation, catalogue, *, _token=None):
        if _token is not _REGISTRY_TOKEN:
            raise TypeError("capability adapters are constructed by a binding")
        if type(kind) is not str or kind not in CAPABILITIES:
            raise ValueError(f"unknown goal capability {kind!r}")
        if type(catalogue) is not CatalogueManifest:
            raise TypeError("a capability adapter needs an exact CatalogueManifest")
        self.kind = kind
        self.implementation = _policy(
            implementation, "capability implementation identity")
        self.catalogue = catalogue
        self._freeze()

    def __call__(self, supplied):
        """Check one implementation view against goals *and* occurrences."""

        if type(supplied) is not CatalogueManifest:
            raise TypeError(
                "a capability adapter consumes an exact CatalogueManifest")
        exhaustive = self.kind != LOCAL_GUIDANCE
        goals = self.catalogue.reconcile(
            supplied, self.kind, exhaustive=exhaustive)
        self.catalogue.reconcile_occurrences(
            supplied.occurrences, self.kind, exhaustive=exhaustive)
        return goals

    def as_dict(self):
        return {
            "kind": self.kind,
            "implementation": self.implementation,
            "catalogue_digest": self.catalogue.digest(),
        }


class CapabilityBinding(Frozen):
    """One independently versioned capability bound to its real adapter."""

    __slots__ = ("adapter",)

    def __init__(self, kind, implementation, catalogue):
        self.adapter = CapabilityAdapter(
            kind, implementation, catalogue, _token=_REGISTRY_TOKEN)
        self._freeze()

    @property
    def kind(self):
        return self.adapter.kind

    @property
    def implementation(self):
        return self.adapter.implementation

    def as_dict(self):
        return self.adapter.as_dict()


class GoalCapabilityUse(Frozen):
    """One implementation bound to a slice of one frozen :class:`GoalPolicy`.

    A generation provider's ``policy`` string and target names are descriptive until
    the source policy checks them.  This record is created only by
    :meth:`GoalPolicy.bind_implementation`: it retains the exact source manifest, the
    registered capability view (including occurrences), and the selected target view.
    Production providers carry this record rather than reconstructing a capability
    from their own class attributes.
    """

    __slots__ = (
        "capability", "implementation", "policy_digest", "providers",
        "source_catalogue", "targets",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("GoalCapabilityUse is a closed policy handoff")

    def __init__(self, capability, implementation, policy_digest, providers,
                 source_catalogue, targets, *, _token=None):
        if _token is not _REGISTRY_TOKEN:
            raise TypeError("capability uses are created by GoalPolicy")
        if type(capability) is not str or capability not in CAPABILITIES:
            raise ValueError("a capability use names one registered capability")
        self.capability = capability
        self.implementation = _policy(
            implementation, "capability-use implementation")
        if type(policy_digest) is not str or len(policy_digest) != 64:
            raise TypeError("a capability use names its policy digest")
        if type(providers) is not tuple or any(
                type(item) is not str or not item for item in providers):
            raise TypeError("a capability use names its provider identities")
        if type(source_catalogue) is not CatalogueManifest:
            raise TypeError("a capability use retains one exact source catalogue")
        if type(targets) is not CatalogueManifest:
            raise TypeError("a capability use retains one exact target catalogue")
        if providers and not targets:
            raise TypeError(
                "a nonempty capability use retains a nonempty target catalogue")
        if not providers and (source_catalogue or targets):
            raise TypeError(
                "a provider-free capability use is the vacuous empty policy")
        self.policy_digest = policy_digest
        self.providers = providers
        self.source_catalogue = source_catalogue
        self.targets = targets
        self._freeze()

    def as_dict(self):
        return {
            "capability": self.capability,
            "implementation": self.implementation,
            "policy_digest": self.policy_digest,
            "providers": list(self.providers),
            "source_catalogue_digest": self.source_catalogue.digest(),
            "target_catalogue_digest": self.targets.digest(),
        }

    def reconcile(self, capability, implementation, source_catalogue, targets):
        """Check the live phase consumer against this exact frozen handoff."""

        if capability != self.capability \
                or implementation != self.implementation \
                or source_catalogue is not self.source_catalogue \
                or tuple(sorted(targets)) != tuple(self.targets):
            raise Refused(Refusal(
                "goal-provider-capability-use-changed",
                "the phase consumer's capability, implementation, source catalogue "
                "or targets differ from its frozen GoalPolicy handoff",
                expected=self.as_dict()))
        return self


class VersionedReason(Frozen):
    """Why a schema was deliberately disabled or genuinely inapplicable."""

    __slots__ = ("code", "detail", "policy")

    def __init__(self, code, policy, detail):
        self.code = _name(code, "goal-policy reason code")
        self.policy = _policy(policy, "goal-policy reason version")
        if type(detail) is not str or not detail:
            raise TypeError("a goal-policy reason needs a nonempty exact explanation")
        self.detail = detail
        self._freeze()

    def as_dict(self):
        return {"code": self.code, "policy": self.policy, "detail": self.detail}


class GoalSchemaDefinition(Frozen):
    """One versioned schema and its complete release-gate capabilities."""

    __slots__ = ("name", "required", "standard", "version")

    def __init__(self, name, version, *, required=None, standard=False):
        self.name = _name(name, "goal-schema name")
        self.version = _version(version, "goal-schema version")
        if type(standard) is not bool:
            raise TypeError("goal-schema standard must be an exact bool")
        self.standard = standard
        if required is None:
            required = (DECLARATION, CONCRETE_HIT, EXACT_BOUNDED_HIT)
            if standard and name != "schedule-interleaving":
                required += (LOCAL_GUIDANCE,)
        if type(required) not in (tuple, list):
            raise TypeError("goal-schema requirements must be an exact sequence")
        required = tuple(required)
        if any(type(item) is not str or item not in CAPABILITIES
               for item in required):
            raise ValueError("goal-schema requirements contain an unknown capability")
        if len(set(required)) != len(required):
            raise ValueError("a goal-schema capability is required twice")
        if DECLARATION not in required or CONCRETE_HIT not in required \
                or EXACT_BOUNDED_HIT not in required:
            raise ValueError(
                "every enabled schema requires declaration, concrete hit and exact "
                "bounded hit semantics")
        self.required = tuple(cap for cap in CAPABILITIES if cap in required)
        self._freeze()

    @property
    def key(self):
        return f"{self.name}/v{self.version}"

    def as_dict(self):
        return {
            "name": self.name,
            "version": self.version,
            "standard": self.standard,
            "required": list(self.required),
        }

    def __eq__(self, other):
        return type(other) is GoalSchemaDefinition and self.as_dict() == other.as_dict()

    def __hash__(self):
        return hash((GoalSchemaDefinition, self.key, self.required, self.standard))


_STANDARD_NAMES = (
    "location",
    "control-choice",
    "boolean-expression",
    "loop-activation",
    "value-observation",
    "operation-event",
    "state-activity",
    "sequence",
    "latency",
    "schedule-interleaving",
    "artifact-failure",
    "monitor-obligation",
)


def standard_goal_schemas():
    """Appendix Table 3, as immutable versioned schema definitions."""

    return tuple(GoalSchemaDefinition(name, 1, standard=True)
                 for name in _STANDARD_NAMES)


class GoalProvider(Frozen):
    """One schema provider with four independently visible capabilities."""

    __slots__ = (
        "capabilities", "catalogue", "guidance", "identity", "schema",
        "semantics",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("GoalProvider is a closed capability record")

    def __init__(self, identity, schema, catalogue, capabilities, semantics=None,
                 guidance=None, *, _token=None):
        if _token is not _REGISTRY_TOKEN:
            raise TypeError("use GoalProvider.builtin() or GoalProvider.declarative()")
        if type(identity) is not GoalProviderIdentity:
            raise TypeError("a goal provider needs an exact GoalProviderIdentity")
        if type(schema) is not GoalSchemaDefinition:
            raise TypeError("a goal provider needs an exact GoalSchemaDefinition")
        if type(catalogue) is not CatalogueManifest:
            raise TypeError("a goal provider needs an exact CatalogueManifest")
        if type(capabilities) not in (tuple, list) or any(
                type(item) is not CapabilityBinding for item in capabilities):
            raise TypeError("goal-provider capabilities need exact bindings")
        capabilities = tuple(capabilities)
        kinds = tuple(item.kind for item in capabilities)
        if len(set(kinds)) != len(kinds):
            raise ValueError("a goal-provider capability is bound twice")
        if semantics is not None and type(semantics) is not DeclarativeGoalSemantics:
            raise TypeError("goal-provider semantics must be declarative finite semantics")
        if guidance is not None and type(guidance) is not DeclarativeGuidanceAdapter:
            raise TypeError(
                "goal-provider guidance must be a DeclarativeGuidanceAdapter")
        if guidance is not None and (
                semantics is None or guidance.semantics is not semantics):
            raise Refused(Refusal(
                "goal-provider-guidance-semantics-mismatch",
                "declarative guidance is bound to a different semantic table",
                provider=identity.key))
        # A binding is a view of this declaration, not a free-standing manifest carrying
        # the same implementation label.  Concrete replay and exact encoding must cover
        # the complete provider slice.  Guidance may deliberately target a subset, but
        # unknown/stale goals and occurrence remapping remain errors.
        for binding in capabilities:
            view = binding.adapter.catalogue
            exhaustive = binding.kind != LOCAL_GUIDANCE
            catalogue.reconcile(view, binding.kind, exhaustive=exhaustive)
            catalogue.reconcile_occurrences(
                view.occurrences, binding.kind, exhaustive=exhaustive)
            if binding.kind == LOCAL_GUIDANCE and not view:
                raise Refused(Refusal(
                    "goal-provider-guidance-catalogue-empty",
                    "a local-guidance capability must target at least one goal from "
                    "its provider declaration",
                    provider=identity.key,
                ))
        self.identity = identity
        self.schema = schema
        self.catalogue = catalogue
        self.capabilities = tuple(sorted(capabilities, key=lambda item: item.kind))
        self.semantics = semantics
        self.guidance = guidance
        self._freeze()

    @classmethod
    def builtin(cls, identity, schema, catalogue, capabilities):
        """Record trusted built-in capabilities without inventing absent ones."""

        return cls(identity, schema, catalogue, capabilities, _token=_REGISTRY_TOKEN)

    @classmethod
    def declarative(cls, identity, schema, catalogue, semantics, *, guidance=None):
        """Build concrete and exact capabilities from one declarative semantics.

        Semantic guidance is independently optional for an extension.  Where it
        applies, the caller supplies a DeclarativeGuidanceAdapter derived from this
        same table.  The adapter validates reachability but cannot realize a task
        stimulus by itself; a production local-guidance claim additionally binds it
        through the common generation provider.  An enabled standard schema (except
        schedule/interleaving) without the semantic adapter still fails release-phase
        validation.
        """

        if type(semantics) is not DeclarativeGoalSemantics:
            raise TypeError("a declarative provider needs exact finite semantics")
        if type(catalogue) is not CatalogueManifest:
            raise TypeError("a declarative provider needs an exact catalogue")
        if set(catalogue) != set(semantics.goals):
            raise Refused(Refusal(
                "goal-provider-semantics-catalogue-mismatch",
                "declarative semantics and its goal declaration name different goals",
                declared=sorted(catalogue), semantic=list(semantics.goals)))
        if guidance is not None and type(guidance) is not DeclarativeGuidanceAdapter:
            raise TypeError(
                "declarative guidance must be a DeclarativeGuidanceAdapter")
        if guidance is not None and (
                guidance.semantics is not semantics
                or not set(guidance.catalogue).issubset(catalogue)):
            raise Refused(Refusal(
                "goal-provider-guidance-semantics-mismatch",
                "the declarative guidance adapter is not a view of this exact "
                "semantic table and catalogue",
                provider=identity.key))
        semantic_id = f"declarative-goal-semantics/{semantics.digest()}"
        bindings = [
            CapabilityBinding(
                DECLARATION, f"catalogue-manifest/{catalogue.digest()}", catalogue),
            CapabilityBinding(CONCRETE_HIT, semantic_id, catalogue),
            CapabilityBinding(EXACT_BOUNDED_HIT, semantic_id, catalogue),
        ]
        if guidance is not None:
            bindings.append(CapabilityBinding(
                LOCAL_GUIDANCE, guidance.implementation,
                guidance.catalogue))
        return cls(identity, schema, catalogue, bindings, semantics, guidance,
                   _token=_REGISTRY_TOKEN)

    def has(self, capability):
        return any(item.kind == capability for item in self.capabilities)

    def binding(self, capability):
        for item in self.capabilities:
            if item.kind == capability:
                return item
        return None

    def _declarative_capability(self, capability):
        if not self.has(capability):
            raise Refused(Refusal(
                "goal-provider-capability-unavailable",
                "the provider does not implement the requested declarative capability",
                provider=self.identity.key,
                capability=capability,
            ))
        if self.semantics is None:
            raise Refused(Refusal(
                "goal-provider-declarative-semantics-unavailable",
                "this built-in capability is not represented by the finite declarative "
                "extension table",
                provider=self.identity.key,
                capability=capability,
            ))
        return self.semantics

    def concrete_step(self, predicate_observations, monitor_states, monitor_events):
        """Consume this provider's canonical table as its concrete-hit adapter."""

        semantics = self._declarative_capability(CONCRETE_HIT)
        return semantics.concrete_step(
            predicate_observations, monitor_states, monitor_events)

    def symbolic_step(self, predicate_terms, monitor_states, monitor_events,
                      algebra: FiniteAlgebra):
        """Consume this provider's same canonical table as its exact-hit encoder."""

        semantics = self._declarative_capability(EXACT_BOUNDED_HIT)
        return semantics.symbolic_step(
            predicate_terms, monitor_states, monitor_events, algebra)

    def as_dict(self):
        return {
            "identity": self.identity.as_dict(),
            "schema": self.schema.as_dict(),
            "catalogue_digest": self.catalogue.digest(),
            "capabilities": [item.as_dict() for item in self.capabilities],
            "semantics_digest": (
                None if self.semantics is None else self.semantics.digest()),
            "guidance": (
                None if self.guidance is None else self.guidance.as_dict()),
        }


class GoalPolicySelection(Frozen):
    """One schema decision; incomplete enumeration retains any known instances."""

    __slots__ = ("providers", "reason", "schema", "status")

    def __init__(self, schema, status, providers=(), reason=None):
        if type(schema) is not GoalSchemaDefinition:
            raise TypeError("a goal-policy selection needs an exact schema")
        if type(status) is not str or status not in DISPOSITIONS:
            raise ValueError(f"goal-policy status must be one of {DISPOSITIONS}")
        if type(providers) not in (tuple, list) or any(
                type(item) is not GoalProvider for item in providers):
            raise TypeError("goal-policy providers must be exact GoalProvider records")
        providers = tuple(sorted(providers, key=lambda item: item.identity.key))
        if len({item.identity for item in providers}) != len(providers):
            raise ValueError("a goal-policy provider is selected twice")
        if any(item.schema != schema for item in providers):
            raise ValueError("a goal-policy provider implements a different schema")
        if status == ENABLED:
            if reason is not None:
                raise ValueError("an enabled schema has no disablement reason")
            if not providers:
                raise ValueError("an enabled schema needs at least one provider")
        elif type(reason) is not VersionedReason:
            raise TypeError(
                "a disabled, inapplicable or incomplete schema needs a VersionedReason")
        self.schema = schema
        self.status = status
        self.providers = providers
        self.reason = reason
        self._freeze()

    @property
    def contributes(self):
        return self.status in (ENABLED, INCOMPLETE)

    def as_dict(self):
        return {
            "schema": self.schema.as_dict(),
            "status": self.status,
            # The frozen policy is also the capability report.  Retaining only provider
            # IDs here would say which implementation was selected and lose which of
            # the four independent capabilities that implementation actually supplied.
            "providers": [provider.as_dict() for provider in self.providers],
            "reason": None if self.reason is None else self.reason.as_dict(),
        }


class GoalPolicy(Frozen):
    """One pre-generation capability matrix and its retained goal instances."""

    __slots__ = ("_canonical", "_digest", "manifest", "selections", "version")

    def __init__(self, version, selections, *, _token=None):
        if _token is not _REGISTRY_TOKEN:
            raise TypeError("GoalPolicy is frozen only through GoalProviderRegistry")
        self.version = _policy(version, "goal-policy version")
        self.selections = tuple(sorted(
            selections, key=lambda item: item.schema.key))
        components = []
        for selection in self.selections:
            if selection.reason is not None and selection.reason.policy != self.version:
                raise ValueError(
                    "schema disposition reason names a different goal policy")
            if not selection.contributes:
                continue
            for provider in selection.providers:
                # Declaration is what lets the provider contribute goals to this one
                # catalogue.  Concrete replay, exact bounded analysis and the release
                # gate consume strictly stronger capability views at their own phase.
                # Requiring all of them here used to make an ancillary-analysis gap
                # erase an otherwise valid concrete denominator before scoring.
                missing = (() if provider.has(DECLARATION) else (DECLARATION,))
                if missing:
                    raise Refused(Refusal(
                        "goal-provider-enabled-capability-missing",
                        "an enabled goal schema lacks a capability required by its "
                        "versioned definition",
                        schema=selection.schema.key,
                        provider=provider.identity.key,
                        phase="catalogue",
                        missing=list(missing)))
                if not provider.catalogue:
                    raise Refused(Refusal(
                        "goal-provider-enabled-catalogue-empty",
                        "a retained provider has no finite goal instances; omit the "
                        "provider and report schema applicability or enumeration "
                        "limits separately",
                        schema=selection.schema.key,
                        provider=provider.identity.key))
                components.append(provider.catalogue)
        self.manifest = merge_catalogues(*components)
        body = {
            "version": _POLICY_VERSION,
            "policy": self.version,
            "selections": [item.as_dict() for item in self.selections],
            "enumeration_complete": not self.incomplete_schemas,
            "catalogue_digest": self.manifest.digest(),
        }
        self._canonical = _canonical(body)
        self._digest = hashlib.sha256(self._canonical.encode("utf-8")).hexdigest()
        self._freeze()

    def digest(self):
        return self._digest

    @property
    def incomplete_schemas(self):
        """Known enumeration gaps, independent of how many goals were emitted."""
        return tuple(selection.schema.key for selection in self.selections
                     if selection.status == INCOMPLETE)

    def as_dict(self):
        return json.loads(self._canonical)

    def enabled_providers(self, capability=None):
        """Retained providers, including those of incompletely enumerated schemas."""

        if capability is not None and (
                type(capability) is not str or capability not in CAPABILITIES):
            raise ValueError("enabled-provider lookup names an unknown capability")
        return tuple(
            provider
            for selection in self.selections if selection.contributes
            for provider in selection.providers
            if capability is None or provider.has(capability))

    def validate(self, phase):
        """Require exactly the capabilities consumed by one production phase.

        Retained providers always remain in :attr:`manifest`.  A missing capability
        therefore refuses the phase that needs it, never removes the provider's goals
        or changes an already-computed concrete score.
        """

        if type(phase) is not str or phase not in PHASES:
            raise ValueError(f"unknown goal-policy validation phase {phase!r}")
        base = (DECLARATION, CONCRETE_HIT)
        for selection in self.selections:
            if not selection.contributes:
                continue
            if phase == CONCRETE_PHASE:
                required = base
            elif phase == EXACT_ANALYSIS_PHASE:
                required = base + (EXACT_BOUNDED_HIT,)
            else:
                required = selection.schema.required
            for provider in selection.providers:
                missing = tuple(capability for capability in required
                                if not provider.has(capability))
                if missing:
                    raise Refused(Refusal(
                        "goal-provider-enabled-capability-missing",
                        "an enabled goal provider lacks a capability required by the "
                        "current production phase",
                        schema=selection.schema.key,
                        provider=provider.identity.key,
                        phase=phase,
                        missing=list(missing)))
        if phase == RELEASE_PHASE and self.incomplete_schemas:
            raise Refused(Refusal(
                "goal-policy-catalogue-incomplete",
                "implemented goal instances do not establish complete schema enumeration",
                schemas=list(self.incomplete_schemas), phase=phase))
        return self

    def _enabled_provider(self, identity):
        if type(identity) is not GoalProviderIdentity:
            raise TypeError("capability reconciliation needs a provider identity")
        for selection in self.selections:
            if selection.contributes:
                for provider in selection.providers:
                    if provider.identity == identity:
                        return provider
        raise Refused(Refusal(
            "goal-provider-not-enabled",
            "capability evidence names a provider not enabled by this frozen policy",
            provider=identity.key))

    def reconcile(self, identity, capability, supplied):
        """Invoke one provider's registered adapter on its implementation view."""

        if type(capability) is not str or capability not in CAPABILITIES:
            raise ValueError("capability reconciliation names an unknown capability")
        provider = self._enabled_provider(identity)
        binding = provider.binding(capability)
        if binding is None:
            raise Refused(Refusal(
                "goal-provider-capability-unavailable",
                "the frozen provider does not implement the requested capability",
                provider=identity.key, capability=capability))
        if type(supplied) is not CapabilityAdapter:
            raise TypeError(
                "capability reconciliation requires the registered adapter record")
        if supplied is not binding.adapter:
            raise Refused(Refusal(
                "goal-provider-capability-adapter-mismatch",
                "capability evidence did not come from the adapter registered by "
                "the frozen provider",
                provider=identity.key,
                capability=capability,
                expected=binding.implementation,
                supplied=supplied.implementation,
            ))
        return supplied(supplied.catalogue)

    def reconcile_all(self, capability, supplied):
        """Require one capability declaration from every enabled provider.

        ``supplied`` is keyed by :class:`GoalProviderIdentity` and contains the exact
        adapter registered for that phase.  Deleting one producer's declaration,
        concrete emitter, local adapter, or exact encoder therefore fails at the
        provider boundary instead of being hidden by another provider's union.  A
        copied catalogue cannot stand in for an adapter.
        """

        if type(capability) is not str or capability not in CAPABILITIES:
            raise ValueError("capability reconciliation names an unknown capability")
        if type(supplied) is not dict:
            raise TypeError("capability rows must be an exact provider mapping")
        expected = {
            provider.identity: provider
            for selection in self.selections if selection.contributes
            for provider in selection.providers if provider.has(capability)
        }
        if any(type(identity) is not GoalProviderIdentity for identity in supplied):
            raise TypeError("capability rows use exact GoalProviderIdentity keys")
        unknown = sorted(
            identity.key for identity in set(supplied) - set(expected))
        if unknown:
            raise Refused(Refusal(
                "goal-provider-capability-unregistered",
                "capability rows name providers outside the frozen policy",
                providers=unknown))
        missing = sorted(identity.key for identity in set(expected) - set(supplied))
        if missing:
            raise Refused(Refusal(
                "goal-provider-capability-provider-missing",
                "an enabled provider supplied no capability row",
                capability=capability, providers=missing))
        return tuple((identity.key, self.reconcile(
            identity, capability, supplied[identity]))
            for identity in sorted(expected, key=lambda item: item.key))

    def bind_implementation(self, capability, implementation, source_catalogue,
                            targets=None, *, exhaustive=True, context_catalogue=None):
        """Bind one real phase implementation to its registered policy slice.

        ``source_catalogue`` is the complete declaration from which this provider
        selects.  ``targets`` is its finite selected goal set.  Matching a version
        string is insufficient: this method also reconciles every goal and stable
        occurrence against the adapters registered under that implementation.  The
        optional non-exhaustive form represents an explicit operator-selected subset;
        production defaults use the exhaustive form.

        A local query may execute a source component rather than the complete
        task product. ``context_catalogue`` names that component explicitly;
        it must retain every registered target and its occurrences. The policy
        and scoring denominator stay complete. Whole-domain consumers cannot
        use this local-context projection.
        """

        if type(capability) is not str or capability not in CAPABILITIES:
            raise ValueError("implementation binding names an unknown capability")
        implementation = _policy(
            implementation, "phase implementation identity")
        if type(source_catalogue) is not CatalogueManifest:
            raise TypeError("implementation binding needs the source CatalogueManifest")
        if source_catalogue != self.manifest:
            raise Refused(Refusal(
                "goal-provider-capability-source-catalogue-mismatch",
                "the phase implementation selects from a declaration different "
                "from its frozen goal policy",
                capability=capability, implementation=implementation,
                policy=self.manifest.digest(), supplied=source_catalogue.digest()))
        if type(exhaustive) is not bool:
            raise TypeError("implementation binding exhaustiveness is an exact bool")
        if context_catalogue is None:
            context_catalogue = source_catalogue
        else:
            if capability != LOCAL_GUIDANCE:
                raise ValueError("a component query context is only valid for local guidance")
            if type(context_catalogue) is not CatalogueManifest:
                raise TypeError("a local query context needs an exact CatalogueManifest")
            source_catalogue.reconcile(context_catalogue, "local-context", exhaustive=False)
            # Preserve all occurrences of retained goals, not merely a subset
            # of their locations that happens to appear somewhere in the policy.
            expected = catalogue_subset(source_catalogue, tuple(context_catalogue))
            expected.reconcile_occurrences(context_catalogue.occurrences, "local-context")

        matching = []
        for provider in self.enabled_providers(capability):
            binding = provider.binding(capability)
            if binding.implementation == implementation:
                matching.append((provider.identity, binding.adapter))
        if not matching:
            raise Refused(Refusal(
                "goal-provider-capability-implementation-unregistered",
                "the phase implementation is not registered by the frozen goal "
                "policy for this capability",
                capability=capability, implementation=implementation))
        registered = merge_catalogues(*(adapter.catalogue
                                        for _identity, adapter in matching))
        if targets is None:
            targets = tuple(registered)
        elif type(targets) not in (tuple, list):
            raise TypeError("implementation targets must be a materialized sequence")
        else:
            targets = tuple(targets)
        if any(type(goal) is not str or not goal for goal in targets):
            raise TypeError("implementation targets are nonempty exact strings")
        if len(set(targets)) != len(targets):
            raise Refused(Refusal(
                "goal-provider-capability-target-duplicate",
                "a phase implementation names one target more than once",
                capability=capability, implementation=implementation))
        unknown = sorted(set(targets) - set(registered))
        if unknown:
            raise Refused(Refusal(
                "goal-provider-capability-target-unregistered",
                "the phase implementation targets goals outside its registered "
                "capability view",
                capability=capability, implementation=implementation,
                goals=unknown))
        selected = catalogue_subset(source_catalogue, targets)
        if context_catalogue is not source_catalogue:
            context_catalogue.reconcile(selected, "local-context", exhaustive=False)
        registered.reconcile(selected, capability, exhaustive=exhaustive)
        registered.reconcile_occurrences(
            selected.occurrences, capability, exhaustive=exhaustive)
        if not selected:
            raise Refused(Refusal(
                "goal-provider-capability-target-empty",
                "an enabled phase implementation must select at least one registered "
                "goal",
                capability=capability, implementation=implementation))
        return GoalCapabilityUse(
            capability, implementation, self.digest(),
            tuple(identity.key for identity, _adapter in matching),
            context_catalogue, selected, _token=_REGISTRY_TOKEN)

    def bind_complete_implementation(self, capability, implementation,
                                     source_catalogue):
        """Bind one composite phase implementation to every registered adapter.

        A production exact provider may compose several independently registered
        encoders.  For example, RTL structural occurrences and finite task monitors
        deliberately have different semantic implementation identities, although one
        exact product executes both.  Selecting adapters merely because they share the
        product's version string would omit the latter.  This boundary instead invokes
        every enabled provider's registered adapter, including its occurrence view,
        and permits the composite implementation to claim the policy only when their
        union is the complete source catalogue.
        """

        if type(capability) is not str or capability not in CAPABILITIES:
            raise ValueError("complete binding names an unknown capability")
        implementation = _policy(
            implementation, "composite implementation identity")
        if type(source_catalogue) is not CatalogueManifest:
            raise TypeError(
                "complete binding needs the source CatalogueManifest")
        if capability == EXACT_BOUNDED_HIT:
            # Exact composition is permitted only after the policy has proved that
            # every enabled provider participates in the exact-analysis phase.  The
            # adapter union below then proves that the actual composite implementation
            # consumes those complete registered views.
            self.validate(EXACT_ANALYSIS_PHASE)
        if source_catalogue is not self.manifest:
            raise Refused(Refusal(
                "goal-provider-capability-source-policy-reconstructed",
                "a complete phase binding must retain the exact catalogue object "
                "owned by its frozen goal policy",
                capability=capability,
                implementation=implementation,
                policy=self.manifest.digest(),
                supplied=source_catalogue.digest()))

        enabled = self.enabled_providers()
        missing = tuple(
            provider.identity.key for provider in enabled
            if not provider.has(capability))
        if missing:
            raise Refused(Refusal(
                "goal-provider-complete-capability-provider-missing",
                "a composite phase implementation cannot cover the frozen policy "
                "because an enabled provider lacks the capability",
                capability=capability,
                implementation=implementation,
                providers=list(missing)))
        adapters = {
            provider.identity: provider.binding(capability).adapter
            for provider in enabled
        }
        self.reconcile_all(capability, adapters)
        registered = merge_catalogues(*(
            adapter.catalogue for adapter in adapters.values()))
        registered.reconcile(source_catalogue, capability, exhaustive=True)
        registered.reconcile_occurrences(
            source_catalogue.occurrences, capability, exhaustive=True)
        return GoalCapabilityUse(
            capability, implementation, self.digest(),
            tuple(sorted(identity.key for identity in adapters)),
            source_catalogue, source_catalogue, _token=_REGISTRY_TOKEN)


class GoalProviderRegistry(Frozen):
    """Immutable registry of schema definitions and their provider implementations."""

    __slots__ = ("providers", "schemas")

    def __init__(self, schemas=(), providers=()):
        if type(schemas) not in (tuple, list) or any(
                type(item) is not GoalSchemaDefinition for item in schemas):
            raise TypeError("goal-provider registry schemas need exact definitions")
        if type(providers) not in (tuple, list) or any(
                type(item) is not GoalProvider for item in providers):
            raise TypeError("goal-provider registry providers need exact records")
        schemas = tuple(sorted(schemas, key=lambda item: item.key))
        providers = tuple(sorted(providers, key=lambda item: item.identity.key))
        if len({item.key for item in schemas}) != len(schemas):
            raise ValueError("a goal schema is registered twice")
        if len({item.identity for item in providers}) != len(providers):
            raise ValueError("a goal provider identity is registered twice")
        known = set(schemas)
        if any(provider.schema not in known for provider in providers):
            raise ValueError("a goal provider implements an unregistered schema")
        self.schemas = schemas
        self.providers = providers
        self._freeze()

    def with_schema(self, schema):
        return GoalProviderRegistry(self.schemas + (schema,), self.providers)

    def with_provider(self, provider):
        return GoalProviderRegistry(self.schemas, self.providers + (provider,))

    def provider(self, identity):
        if type(identity) is not GoalProviderIdentity:
            raise TypeError("goal-provider lookup needs an exact identity")
        for provider in self.providers:
            if provider.identity == identity:
                return provider
        raise Refused(Refusal(
            "goal-provider-version-unsupported",
            "this registry has no implementation of the requested provider version",
            provider=identity.key,
            supported=[item.identity.key for item in self.providers]))

    def freeze(self, version, selections):
        if type(selections) not in (tuple, list) or any(
                type(item) is not GoalPolicySelection for item in selections):
            raise TypeError("goal-policy selections need exact records")
        selections = tuple(selections)
        if len({item.schema for item in selections}) != len(selections):
            raise ValueError("a goal policy decides one schema twice")
        missing = sorted(schema.key for schema in set(self.schemas)
                         - {item.schema for item in selections})
        extra = sorted(item.schema.key for item in selections
                       if item.schema not in set(self.schemas))
        if missing or extra:
            raise Refused(Refusal(
                "goal-policy-schema-disposition-incomplete",
                "a frozen goal policy must explicitly decide every registered schema",
                missing=missing, unknown=extra))
        registered = {provider.identity: provider for provider in self.providers}
        for selection in selections:
            for provider in selection.providers:
                if registered.get(provider.identity) is not provider:
                    raise Refused(Refusal(
                        "goal-policy-provider-not-registered",
                        "a goal policy selected a provider record outside its registry",
                        provider=provider.identity.key))
        return GoalPolicy(version, selections, _token=_REGISTRY_TOKEN)


def builtin_schema_providers(
        catalogue: CatalogueManifest,
        schemas_by_goal: Mapping[str, GoalSchemaDefinition], *,
        name: str, version: int, capabilities: tuple[tuple[str, str], ...],
) -> tuple[GoalProvider, ...]:
    """Bind explicit frontend schema assignments without interpreting its syntax.

    The frontend owns each goal's schema; this shared operation only groups its
    frozen rows and preserves all occurrence mappings and capability identities.
    """
    groups = {}
    for goal in catalogue:
        groups.setdefault(schemas_by_goal[goal], []).append(goal)
    providers = []
    for schema, goals in sorted(groups.items(), key=lambda item: item[0].name):
        component = catalogue_subset(catalogue, goals)
        providers.append(GoalProvider.builtin(
            GoalProviderIdentity(f"{name}-{schema.name}", version),
            schema,
            component,
            tuple(CapabilityBinding(kind, implementation, component)
                  for kind, implementation in capabilities)))
    return tuple(providers)
