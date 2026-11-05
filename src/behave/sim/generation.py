"""Appendix A.3's backend-neutral stimulus worklist (``alg:explore``).

The unit of this search is a complete :class:`~behave.sim.core.stimulus.Stimulus`,
not a backend state.  A provider selects local questions from a seed and probes one
question; this module owns everything that can affect which candidate stimuli survive:
the FIFO, request order, time/Probe budgets, validation, exact-byte identity,
deduplication, provenance, and termination.

Replay-guided providers first observe the complete baseline, then search only
remaining targets. A candidate's source replay updates a direction-local ledger;
a local solve alone never credits a hit. Final artifact evaluation owns scores
and published coverage; bounded analysis retains its own result algebra.
An already checked candidate counterexample stops further search, without
interpreting the source-guidance ledger itself as a score.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from contextlib import nullcontext
from itertools import pairwise

from .core.admission import AdmittedTask
from .core.catalogue import CatalogueManifest
from .core.frozen import Frozen, FrozenMap
from .core.goal_provider import (
    LOCAL_GUIDANCE,
    GoalCapabilityUse,
)
from .core.record_value import (
    FrozenValueMap,
    freeze_record_value,
    thaw_record_value,
)
from .core.refusal import Refusal, Refused
from .core.stimulus import Stimulus, canonical_json
from .generation_cache import GenerationCacheTelemetry
from .generation_feedback import SearchCoverage
from .generation_protocol import (
    GenerationProvider as GenerationProvider,
    GenerationExtensions,
    Outcome,
    ProbeRequest as ProbeRequest,
    ProbeResult,
    ProbeSelection as ProbeSelection,
    QueryPlan,
    QueryTemplate,
    Scope,
    StimulusField as StimulusField,
    stimulus_bytes as stimulus_bytes,
)
from .goal_policy import evaluation_manifest
from .execution_control import check_execution
from .search_policy import (
    SearchBudget, SearchFailed, SearchPolicy, SearchStopped, active_search,
    check_search_work, search_phase, search_query_scope, search_scope,
)
from .stimulus_set import GenerationSpec, stimulus_admissible

__all__ = [
    "GeneratedStimulusSet",
    "GenerationPortfolio",
    "GenerationProvider",
    "GenerationResult",
    "ProbeRequest",
    "ProbeSelection",
    "StimulusField",
    "UnavailableGenerationProvider",
    "combine_generation",
    "generate",
    "generate_independently",
    "generate_stimuli",
    "stimulus_bytes",
]

_GENERATED_SET = object()
_NO_PRESELECTION = object()
_PROVENANCE_FIELDS = {
    "provider", "policy", "seed", "request", "rank", "targets", "occurrence",
    "catalogue_digest", "mutable_fields", "scope", "duplicate",
}


def generate_stimuli(
    task: AdmittedTask,
    baseline: tuple[Stimulus, ...] | list[Stimulus],
    providers: tuple[GenerationProvider, ...] | list[GenerationProvider],
    *,
    frontend_catalogue: CatalogueManifest | None = None,
    catalogue: CatalogueManifest | None = None,
    generation_spec: GenerationSpec | None,
    probe_budget: int | None = None,
    search_policy: SearchPolicy | None = None,
    baseline_policy: str | None = None,
    _execution=None,
    _search_stop=None,
) -> GeneratedStimulusSet:
    """Generate a canonical stimulus set from independent searches of one baseline.

    The command supplies either its frozen catalogue or a frontend manifest to
    reconcile before searching. The returned set retains the admitted task,
    generation policy and producer provenance, but makes no replay-hit claim.
    """
    if type(task) is not AdmittedTask:
        raise Refused(
            Refusal(
                "generation-task-not-admitted",
                "public generation requires the immutable task/artifact pair produced by "
                "admit(), so providers cannot choose a different contract",
                got=type(task).__name__,
            )
        )
    if (frontend_catalogue is None) == (catalogue is None):
        raise TypeError(
            "generation needs exactly one of a frontend manifest to freeze or the "
            "complete CatalogueManifest already frozen by its command boundary"
        )
    if catalogue is None:
        catalogue = evaluation_manifest(task, frontend_catalogue)
    else:
        if type(catalogue) is not CatalogueManifest:
            raise TypeError("catalogue must be the exact pre-generation CatalogueManifest")
        # This is the carry-only path from CommandRequest.  The command has already
        # reconciled the frontend and task components; rebuilding either component here
        # would make generation a second declaration authority.
    # ``generate`` freezes each provider's source catalogue once for that independent
    # search and records the digest in its result.  Reading it here as a preflight too
    # would create a second, possibly different source authority.
    portfolio = generate_independently(
        baseline,
        providers,
        contract=task.contract,
        generation_spec=generation_spec,
        probe_budget=probe_budget,
        search_policy=search_policy,
        baseline_policy=baseline_policy,
        _execution=_execution,
        _search_stop=_search_stop,
    )
    return _bind_generated_set(task, portfolio, catalogue)


def _json_value(value):
    """Thaw ``FrozenMap``/``freeze`` output into an ordinary JSON-shaped value."""

    if isinstance(value, FrozenMap):
        return {key: _json_value(item) for key, item in value.items()}
    if type(value) is FrozenValueMap:
        return thaw_record_value(value)
    if isinstance(value, tuple):
        is_mapping = all(
            isinstance(item, tuple) and len(item) == 2
            and isinstance(item[0], str)
            for item in value)
        if is_mapping and len({item[0] for item in value}) == len(value):
            return {key: _json_value(item) for key, item in value}
        return [_json_value(item) for item in value]
    return value


class UnavailableGenerationProvider(Frozen):
    """One declared generation direction whose local capability could not bind.

    A provider failing to construct is not a reason to discard the common baseline or
    suppress the other independent search.  Keeping the direction as a provider makes
    that absence visible once in the scheduler telemetry, without replaying every
    baseline seed against the same missing capability. The empty manifest means only
    that this unavailable capability exposed no target declaration; it does not replace
    the evaluated artifact's independently frozen catalogue. When that declaration
    and a completed-record reader exist, retain both without claiming a solver.
    """

    __slots__ = (
        "_catalogue", "_observe", "_refusal_detail", "_refusal_kind", "_refusal_why",
        "_scope", "name", "policy", "replay_guided", "targetable_goals",
    )

    def __init_subclass__(cls, **kwargs):
        raise TypeError("UnavailableGenerationProvider is a closed boundary record")

    def __init__(self, name, policy, refusal, *, catalogue=None, observe=None,
                 scope=Scope.EXACT_LOCAL_HIT):
        if type(name) is not str or not name.strip():
            raise TypeError("an unavailable provider needs a nonempty exact name")
        if type(policy) is not str or not policy.strip():
            raise TypeError("an unavailable provider needs a nonempty exact policy")
        if type(refusal) is not Refusal:
            raise TypeError("an unavailable provider needs one exact Refusal")
        if catalogue is None:
            catalogue = CatalogueManifest(())
        if type(catalogue) is not CatalogueManifest:
            raise TypeError("an unavailable provider needs an exact CatalogueManifest")
        if not isinstance(scope, Scope):
            raise TypeError("an unavailable provider needs a local generation scope")
        if observe is not None and not callable(observe):
            raise TypeError("baseline feedback must be a completed-record reader")
        self.name = name
        self.policy = policy
        self._observe = observe
        self.replay_guided = observe is not None
        self.targetable_goals = ()
        self._catalogue = catalogue
        self._refusal_kind = refusal.kind
        self._refusal_why = refusal.why
        try:
            self._refusal_detail = freeze_record_value(
                dict(refusal.detail), "unavailable-provider refusal detail")
        except (TypeError, ValueError):
            # Refusal detail is diagnostic, not policy.  A capability boundary must
            # still be able to report an older provider whose detail predates the
            # strict record carrier; preserve a bounded description without retaining
            # that provider's live object graph.
            self._refusal_detail = freeze_record_value({
                "detail_unavailable": repr(refusal.detail),
            })
        self._scope = scope
        self._freeze()

    def catalogue_manifest(self):
        return self._catalogue

    def observe_many(self, seeds):
        """Read completed native records even when no query adapter is available."""
        return None if self._observe is None else self._observe(seeds, self._catalogue)

    def cache_telemetry(self):
        return GenerationCacheTelemetry()

    def select(self, seed, *, targets=None):
        if type(seed) is not Stimulus:
            raise TypeError("generation Select needs an exact Stimulus")
        return self.search_refusal()

    def search_refusal(self):
        return Refusal(
            self._refusal_kind, self._refusal_why,
            **thaw_record_value(self._refusal_detail))

    def probe(self, seed, request):
        if type(seed) is not Stimulus or type(request) is not ProbeRequest:
            raise TypeError("generation Probe needs typed seed and request records")
        return ProbeResult(
            Outcome.UNSUPPORTED, self._scope,
            detail=(f"{self._refusal_kind}: {self._refusal_why}"))


class GenerationResult(Frozen):
    """One independent Appendix A.3 ``alg:explore`` result.

    The baseline is retained separately from ``generated``.  Both appendix searches
    start with byte-identical baseline members, while their work queues, ``Seen`` sets,
    candidates, provenance, budgets and counters are constructed afresh by each call.
    """

    __slots__ = (
        "baseline", "baseline_policy", "contract", "generated",
        "generation_spec", "provenance", "provider", "telemetry",
    )

    def __init__(self, provider, baseline, generated, provenance, telemetry, *,
                 contract, generation_spec, baseline_policy=None):
        if type(contract) is not str or not contract:
            raise ValueError("a generation result needs a task-contract identity")
        if generation_spec is not None and type(generation_spec) is not GenerationSpec:
            raise TypeError("a generation result needs the frozen GenerationSpec")
        if type(provider) is not str or not provider.strip():
            raise Refused(Refusal(
                "generation-result-provider-invalid",
                "a generation result must name its nonempty stable provider"))
        if baseline_policy is not None \
                and (type(baseline_policy) is not str
                     or not baseline_policy.strip()):
            raise Refused(Refusal(
                "generation-result-baseline-policy-invalid",
                "a versioned baseline policy identity is a nonempty exact string",
                provider=provider))
        telemetry = dict(telemetry or {})
        policy = telemetry.get("provider_policy")
        if type(policy) is not str or not policy.strip():
            raise Refused(Refusal(
                "generation-result-policy-invalid",
                "a generation result must carry the provider's versioned ordering "
                "policy in its telemetry",
                provider=provider))
        baseline = tuple(baseline)
        generated = tuple(generated)
        members = baseline + generated
        if any(type(stimulus) is not Stimulus for stimulus in members):
            raise TypeError("a generation result contains only exact Stimulus records")
        for stimulus in members:
            expected = hashlib.sha256(stimulus_bytes(stimulus)).hexdigest()
            if stimulus.fingerprint != expected:
                raise ValueError(
                    "a generation result contains a stimulus whose fingerprint does "
                    "not match its canonical bytes")
        baseline_bytes = tuple(stimulus_bytes(item) for item in baseline)
        generated_bytes = tuple(stimulus_bytes(item) for item in generated)
        if baseline_bytes != tuple(sorted(baseline_bytes)) \
                or generated_bytes != tuple(sorted(generated_bytes)):
            raise Refused(Refusal(
                "generation-result-order-noncanonical",
                "a generation result records its baseline and generated members in "
                "canonical exact-byte order",
                provider=provider))
        if len(set(baseline_bytes + generated_bytes)) != len(members):
            raise Refused(Refusal(
                "generation-result-members-duplicate",
                "a generation result cannot count one exact stimulus twice",
                provider=provider))
        provenance = dict(provenance or {})
        member_fingerprints = {stimulus.fingerprint for stimulus in members}
        unknown = sorted(set(provenance) - member_fingerprints)
        if unknown:
            raise Refused(Refusal(
                "generation-result-provenance-unbound",
                "generation provenance names a stimulus outside this search result",
                provider=provider, fingerprints=unknown))
        missing = sorted(
            stimulus.fingerprint for stimulus in generated
            if stimulus.fingerprint not in provenance)
        if missing:
            raise Refused(Refusal(
                "generation-result-provenance-missing",
                "every generated stimulus needs the retained Probe row that explains "
                "how it entered the worklist",
                provider=provider, fingerprints=missing))
        for fingerprint, rows in provenance.items():
            if type(rows) not in (tuple, list) or not rows:
                raise Refused(Refusal(
                    "generation-result-provenance-malformed",
                    "a provenance member is a nonempty finite sequence of exact rows",
                    provider=provider, fingerprint=fingerprint))
            images = []
            retained = False
            for row in rows:
                if type(row) is not dict or set(row) != _PROVENANCE_FIELDS:
                    raise Refused(Refusal(
                        "generation-result-provenance-malformed",
                        "a provenance row has the complete fixed generation schema",
                        provider=provider, fingerprint=fingerprint))
                if row["provider"] != provider or row["policy"] != policy:
                    raise Refused(Refusal(
                        "generation-result-provenance-provider-mismatch",
                        "a provenance row must name the search and policy that owns it",
                        provider=provider, fingerprint=fingerprint))
                if row["seed"] not in member_fingerprints:
                    raise Refused(Refusal(
                        "generation-result-provenance-seed-unbound",
                        "a provenance row's seed must be a member visited by this "
                        "independent worklist",
                        provider=provider, fingerprint=fingerprint,
                        seed=row["seed"]))
                if type(row["request"]) is not str or len(row["request"]) != 64 \
                        or any(ch not in "0123456789abcdef"
                               for ch in row["request"]):
                    raise Refused(Refusal(
                        "generation-result-provenance-malformed",
                        "a provenance row names the full canonical request digest",
                        provider=provider, fingerprint=fingerprint))
                if type(row["rank"]) is not int or isinstance(row["rank"], bool) \
                        or row["rank"] < 0:
                    raise Refused(Refusal(
                        "generation-result-provenance-malformed",
                        "a provenance request rank is a nonnegative exact integer",
                        provider=provider, fingerprint=fingerprint))
                if type(row["targets"]) is not list or not row["targets"] \
                        or any(type(goal) is not str or not goal
                               for goal in row["targets"]) \
                        or len(set(row["targets"])) != len(row["targets"]):
                    raise Refused(Refusal(
                        "generation-result-provenance-malformed",
                        "a provenance row has a nonempty finite target set",
                        provider=provider, fingerprint=fingerprint))
                if type(row["occurrence"]) is not str or not row["occurrence"] \
                        or type(row["scope"]) is not str or not row["scope"]:
                    raise Refused(Refusal(
                        "generation-result-provenance-malformed",
                        "a provenance row names its occurrence and local probe scope",
                        provider=provider, fingerprint=fingerprint))
                digest = row["catalogue_digest"]
                if digest is not None and (
                        type(digest) is not str or len(digest) != 64
                        or any(ch not in "0123456789abcdef" for ch in digest)):
                    raise Refused(Refusal(
                        "generation-result-provenance-malformed",
                        "a provenance catalogue identity is null or a full digest",
                        provider=provider, fingerprint=fingerprint))
                fields = row["mutable_fields"]
                if type(fields) is not list or not fields:
                    raise Refused(Refusal(
                        "generation-result-provenance-malformed",
                        "a provenance row carries its nonempty mutable-field set",
                        provider=provider, fingerprint=fingerprint))
                try:
                    decoded = tuple(StimulusField.from_dict(field) for field in fields)
                except (TypeError, ValueError) as exc:
                    raise Refused(Refusal(
                        "generation-result-provenance-malformed",
                        "a provenance row contains a malformed mutable field",
                        provider=provider, fingerprint=fingerprint,
                        error=str(exc))) from None
                if len({field.coordinate for field in decoded}) != len(decoded):
                    raise Refused(Refusal(
                        "generation-result-provenance-malformed",
                        "a provenance row cannot name one mutable coordinate twice",
                        provider=provider, fingerprint=fingerprint))
                if type(row["duplicate"]) is not bool:
                    raise Refused(Refusal(
                        "generation-result-provenance-malformed",
                        "a provenance duplicate flag is an exact Boolean",
                        provider=provider, fingerprint=fingerprint))
                retained |= not row["duplicate"]
                images.append(canonical_json(row))
            if len(set(images)) != len(images):
                raise Refused(Refusal(
                    "generation-result-provenance-duplicate",
                    "one independent search records each Probe result once",
                    provider=provider, fingerprint=fingerprint))
            if fingerprint in {stimulus.fingerprint for stimulus in generated} \
                    and not retained:
                raise Refused(Refusal(
                    "generation-result-provenance-retention-missing",
                    "a newly generated stimulus needs a nonduplicate retention row",
                    provider=provider, fingerprint=fingerprint))
        self.provider = provider
        self.baseline_policy = baseline_policy
        self.contract = contract
        self.generation_spec = generation_spec
        self.baseline = baseline
        self.generated = generated
        self.provenance = FrozenMap(provenance)
        # Keep mappings distinct from sequences. A target table whose rows
        # happen to contain two goal IDs is still a sequence, not key/value pairs.
        self.telemetry = freeze_record_value(telemetry, "generation telemetry")
        self._freeze()

    @property
    def canonical_union(self):
        """The canonical baseline-plus-generated set for this search."""

        by_bytes = {stimulus_bytes(s): s for s in self.baseline}
        by_bytes.update((stimulus_bytes(s), s) for s in self.generated)
        return tuple(by_bytes[key] for key in sorted(by_bytes))

    def as_dict(self):
        return {
            "provider": self.provider,
            "baseline_policy": self.baseline_policy,
            "contract": self.contract,
            "generation_spec": None if self.generation_spec is None else {
                "identity": self.generation_spec.identity,
                **self.generation_spec.as_dict(),
            },
            "baseline": [s.fingerprint for s in self.baseline],
            "generated": [s.fingerprint for s in self.generated],
            "provenance": {key: [_json_value(row) for row in rows]
                           for key, rows in self.provenance.items()},
            "telemetry": _json_value(self.telemetry),
        }


class GenerationPortfolio(Frozen):
    """The canonical ``Xi_A`` from independent backend searches.

    Each member search retains its own queue, seen set and telemetry under the
    invocation's shared budget. This
    record owns only the paper-level union: one byte-identical baseline plus every
    generated stimulus, deduplicated by exact canonical bytes with all producer
    provenance retained. With search disabled there are no member searches and
    the union is exactly the baseline.
    """

    __slots__ = (
        "baseline", "baseline_policy", "contract", "generation_spec",
        "provenance", "searches", "stimuli",
    )

    def __init__(self, baseline, searches, stimuli, provenance, *, contract,
                 generation_spec, baseline_policy=None):
        if type(contract) is not str or not contract:
            raise ValueError("a generation portfolio needs a task-contract identity")
        if generation_spec is not None and type(generation_spec) is not GenerationSpec:
            raise TypeError("a generation portfolio needs the frozen GenerationSpec")
        if baseline_policy is not None \
                and (type(baseline_policy) is not str
                     or not baseline_policy.strip()):
            raise TypeError(
                "a generation portfolio baseline policy is null or a nonempty "
                "exact string")
        self.baseline = tuple(baseline)
        self.baseline_policy = baseline_policy
        self.searches = tuple(searches)
        self.stimuli = tuple(stimuli)
        self.provenance = FrozenMap(provenance)
        self.contract = contract
        self.generation_spec = generation_spec
        self._freeze()

    def as_dict(self):
        return {
            "contract": self.contract,
            "baseline_policy": self.baseline_policy,
            "generation_spec": None if self.generation_spec is None else {
                "identity": self.generation_spec.identity,
                **self.generation_spec.as_dict(),
            },
            "baseline": [stimulus.fingerprint for stimulus in self.baseline],
            "stimuli": [stimulus.fingerprint for stimulus in self.stimuli],
            "searches": [search.as_dict() for search in self.searches],
            "provenance": {
                key: [_json_value(row) for row in rows]
                for key, rows in self.provenance.items()
            },
        }


class GeneratedStimulusSet(Frozen):
    """The admitted task and the exact Appendix A.3 ``Xi_A`` generated for it.

    The public boundary carries these together so a caller cannot replace the task,
    artifact target, or generation policy between generation and concrete replay.
    ``GenerationPortfolio`` remains the backend-neutral search result; this record is
    the admitted run-level binding around it.
    """

    __slots__ = ("_replay_suite", "catalogue", "portfolio", "task")

    def __init__(self, task, portfolio, catalogue, *, _token=None):
        from .core.admission import AdmittedTask

        if _token is not _GENERATED_SET:
            raise TypeError(
                "a GeneratedStimulusSet is derived only by generate_stimuli() from "
                "its admitted task, searches and catalogue")
        if type(task) is not AdmittedTask:
            raise TypeError("a generated stimulus set needs an exact AdmittedTask")
        if type(portfolio) is not GenerationPortfolio:
            raise TypeError("a generated stimulus set needs a GenerationPortfolio")
        if type(catalogue) is not CatalogueManifest:
            raise TypeError(
                "a generated stimulus set needs the exact pre-generation "
                "CatalogueManifest")
        if portfolio.contract != task.contract.digest:
            raise Refused(Refusal(
                "generation-portfolio-task-mismatch",
                "the generated stimulus set names a different task contract from the "
                "admitted artifact it is being attached to",
                generated=portfolio.contract, admitted=task.contract.digest))
        rebuilt = (combine_generation(portfolio.searches) if portfolio.searches else
                   _baseline_portfolio(
                       portfolio.baseline, contract=task.contract,
                       generation_spec=portfolio.generation_spec,
                       baseline_policy=portfolio.baseline_policy))
        if rebuilt.as_dict() != portfolio.as_dict():
            raise Refused(Refusal(
                "generation-portfolio-not-derived-from-searches",
                "the public Xi_A must be exactly its canonical baseline plus recorded "
                "generated inputs; its stimuli or provenance were replaced"))
        for stimulus in portfolio.stimuli:
            faults = _member_faults(stimulus, task.contract, portfolio.generation_spec)
            if faults:
                raise Refused(Refusal(
                    "generation-portfolio-member-outside-domain",
                    "a member of the generated Xi_A is outside the admitted task and "
                    "frozen generation domain",
                    stimulus=stimulus.fingerprint, first=faults[0],
                    count=len(faults)))
        self.task = task
        self.portfolio = portfolio
        self.catalogue = catalogue
        self._replay_suite = None
        self._freeze()

    @property
    def baseline(self):
        return self.portfolio.baseline

    @property
    def baseline_policy(self):
        return self.portfolio.baseline_policy

    @property
    def generation_spec(self):
        return self.portfolio.generation_spec

    @property
    def provenance(self):
        return self.portfolio.provenance

    @property
    def searches(self):
        return self.portfolio.searches

    @property
    def stimuli(self):
        return self.portfolio.stimuli

    @property
    def replay_suite(self):
        """Derive this immutable pool's execution phases once, without replay.

        Scoring and its handoff checks share the same plan. Keep construction
        lazy so generation alone neither builds nor validates continuous runs.
        There is no cross-invocation cache or reuse of observations/decisions.
        """
        suite = self._replay_suite
        if suite is None and all(s.replay_policy is not None for s in self.stimuli):
            from .replay_suite import build_replay_suite

            suite = build_replay_suite(self.task.contract, self.stimuli, baseline=self.baseline)
            object.__setattr__(self, "_replay_suite", suite)
        return suite

    @property
    def search_summary(self):
        """Detached budget receipt; reading it performs no search or replay."""
        if not self.searches:
            return {"version": "search-budget-receipt/1", "policy": None,
                    "elapsed_s": 0.0, "stop_reason": "search-disabled",
                    "probes_started": 0, "probes_completed": 0,
                    "probes_cancelled": 0, "directions": []}
        directions = [dict(provider=s.provider, **_json_value(s.telemetry["search_budget"]))
                      for s in self.searches if "search_budget" in s.telemetry]
        if not directions:
            return None  # Explicit legacy per-direction count experiment.
        reasons = {row["stop_reason"] for row in directions}
        reason = next((r for r in ("scoring-budget-exhausted", "baseline-incomplete", "baseline-failed", "candidate-failed", "search-failed", "time-budget-exhausted", "probe-budget-exhausted",
                                  "provider-unavailable")
                       if r in reasons),
                      "goals-covered" if reasons == {"goals-covered"} else "queue-empty")
        return {"version": "search-budget-receipt/1", "policy": directions[0]["policy"],
                "elapsed_s": max(row["total_elapsed_s"] for row in directions),
                "stop_reason": reason,
                **{key: sum(row[key] for row in directions) for key in (
                    "probes_started", "probes_completed", "probes_cancelled")},
                "directions": directions}

    def as_dict(self):
        return {
            "task": self.task.as_dict(),
            "artifact": self.task.realization.identity,
            "catalogue_manifest": self.catalogue.as_dict(),
            "catalogue_digest": self.catalogue.digest(),
            "portfolio": self.portfolio.as_dict(),
        }

    def as_record_reference(self):
        """The phase handoff image when its command already owns the manifest.

        A command record carries the complete catalogue digest fixed before generation.
        Repeating the full manifest in every phase field is neither a second authority
        nor additional evidence; it only multiplies large structural catalogues.
        """
        return {
            "task": self.task.as_dict(),
            "artifact": self.task.realization.identity,
            "catalogue_digest": self.catalogue.digest(),
            "portfolio": self.portfolio.as_dict(),
        }


def _bind_generated_set(task, portfolio, catalogue):
    """Bind the one derived public generation handoff after all reconciliation."""

    return GeneratedStimulusSet(
        task, portfolio, catalogue, _token=_GENERATED_SET)


def combine_generation(searches):
    """Merge completed independent searches after verifying their common baseline."""
    if type(searches) not in (tuple, list) or not searches:
        raise Refused(Refusal(
            "generation-searches-empty",
            "the final stimulus set requires at least one completed independent "
            "generation search"))
    if any(type(search) is not GenerationResult for search in searches):
        raise Refused(Refusal(
            "generation-search-result-not-typed",
            "every search entering the final stimulus union must be an exact "
            "GenerationResult"))

    ordered_searches = tuple(sorted(
        searches,
        key=lambda search: (
            search.provider,
            str(dict(search.telemetry.items()).get("provider_policy", ""))),
    ))
    identities = [
        (search.provider,
         str(dict(search.telemetry.items()).get("provider_policy", "")))
        for search in ordered_searches
    ]
    if len(set(identities)) != len(identities):
        raise Refused(Refusal(
            "generation-search-identity-duplicate",
            "two searches claim the same provider and policy identity, so their "
            "independence and provenance cannot be distinguished",
            identities=[list(identity) for identity in identities]))

    baseline = ordered_searches[0].baseline
    if not baseline:
        raise Refused(Refusal(
            "generation-search-baseline-empty",
            "Appendix A.3 starts every independent search from the same nonempty "
            "canonical baseline"))
    contract = ordered_searches[0].contract
    generation_spec = ordered_searches[0].generation_spec
    baseline_policy = ordered_searches[0].baseline_policy
    baseline_bodies = tuple(stimulus_bytes(item) for item in baseline)
    for search in ordered_searches[1:]:
        if search.contract != contract:
            raise Refused(Refusal(
                "generation-search-contract-mismatch",
                "independent searches name different task contracts, so their "
                "stimuli are not members of one Xi_A",
                first=contract, other=search.contract, provider=search.provider))
        if ((None if search.generation_spec is None else search.generation_spec.identity)
                != (None if generation_spec is None else generation_spec.identity)):
            raise Refused(Refusal(
                "generation-search-spec-mismatch",
                "independent searches used different frozen generation policies, so "
                "their stimuli are not members of one Xi_A",
                first=None if generation_spec is None else generation_spec.identity,
                other=None if search.generation_spec is None else search.generation_spec.identity,
                provider=search.provider))
        if search.baseline_policy != baseline_policy:
            raise Refused(Refusal(
                "generation-search-baseline-policy-mismatch",
                "independent searches name different versioned baseline policies, "
                "so their common seed set has no single construction identity",
                first=baseline_policy, other=search.baseline_policy,
                provider=search.provider))
        if tuple(stimulus_bytes(item) for item in search.baseline) != baseline_bodies:
            raise Refused(Refusal(
                "generation-search-baseline-mismatch",
                "the independent searches did not start from the same canonical "
                "baseline, so their union is not Appendix A.3's Xi_A",
                providers=[ordered_searches[0].provider, search.provider]))

    by_bytes = {body: stimulus for body, stimulus in zip(baseline_bodies, baseline)}
    fingerprints = {stimulus.fingerprint: body
                    for body, stimulus in zip(baseline_bodies, baseline)}
    provenance = {}
    for search in ordered_searches:
        for stimulus in search.generated:
            body = stimulus_bytes(stimulus)
            prior = fingerprints.get(stimulus.fingerprint)
            if prior is not None and prior != body:
                raise Refused(Refusal(
                    "generation-stimulus-digest-collision",
                    "two searches supplied distinct canonical stimulus bytes under "
                    "one display fingerprint",
                    fingerprint=stimulus.fingerprint))
            fingerprints[stimulus.fingerprint] = body
            by_bytes.setdefault(body, stimulus)
        for fingerprint, rows in search.provenance.items():
            target = provenance.setdefault(fingerprint, [])
            for row in rows:
                plain = _json_value(row)
                if plain not in target:
                    target.append(plain)

    stimuli = tuple(by_bytes[body] for body in sorted(by_bytes))
    return GenerationPortfolio(
        baseline, ordered_searches, stimuli, provenance,
        contract=contract, generation_spec=generation_spec,
        baseline_policy=baseline_policy)


def validate_search_request(providers, *, probe_budget=None, search_policy=None):
    """Resolve one search request before either baseline execution or generation."""
    if type(providers) not in (tuple, list):
        raise TypeError("search providers are a materialized sequence")
    if probe_budget is not None and (type(probe_budget) is not int or probe_budget < 0):
        raise ValueError("probe_budget must be a nonnegative integer")
    if search_policy is not None and (type(search_policy) is not SearchPolicy
                                      or probe_budget is not None):
        raise ValueError("use the exact SearchPolicy or legacy probe_budget, not both")
    if search_policy is None and probe_budget is None:
        search_policy = SearchPolicy()
    disabled = (probe_budget == 0 or (search_policy is not None and
                (search_policy.time_budget_s == 0 or search_policy.max_probes == 0)))
    if not providers and not disabled:
        raise Refused(Refusal(
            "generation-providers-empty",
            "enabled search requires at least one generation direction"))
    return search_policy


def _baseline_portfolio(baseline, *, contract, generation_spec, baseline_policy):
    canonical = _baseline(baseline, contract, generation_spec)
    return GenerationPortfolio(
        canonical, (), canonical, {}, contract=contract.digest,
        generation_spec=generation_spec, baseline_policy=baseline_policy)


def generate_independently(baseline, providers, *, contract,
                           generation_spec: GenerationSpec | None,
                           probe_budget: int | None = None,
                           search_policy: SearchPolicy | None = None,
                           baseline_policy=None, _execution=None, _search_stop=None) -> GenerationPortfolio:
    """Independent goal worklists share a budget and the concrete failure stop."""

    search_policy = validate_search_request(
        providers, probe_budget=probe_budget, search_policy=search_policy)
    from .deferred_generation import DeferredGenerationProvider

    if active_search() is not None:
        raise ValueError("nested search schedulers cannot renew the active budget")
    # An explicitly supplied old count retains its historical experimental meaning.
    # New public calls use one shared policy, never a fresh budget per provider.
    if not providers:
        return _baseline_portfolio(
            baseline, contract=contract, generation_spec=generation_spec,
            baseline_policy=baseline_policy)
    budget = None if probe_budget is not None else SearchBudget(search_policy)
    canonical_baseline = _baseline(baseline, contract, generation_spec)
    searches = []
    for index, recipe in enumerate(providers):
        lease = None if budget is None else budget.direction(len(providers) - index)
        with search_scope(lease), (nullcontext() if lease is None else lease.resources):
            provider = recipe
            preparation_stop = _search_stop
            if preparation_stop is None and _execution is not None and _execution.has_failure_witness:
                preparation_stop = "candidate-failed"
            if type(recipe) is DeferredGenerationProvider:
                try:
                    if preparation_stop is not None:
                        raise SearchStopped(preparation_stop)
                    if probe_budget == 0 or (budget is not None and
                            budget.policy.max_probes is not None and
                            budget.probes >= budget.policy.max_probes):
                        raise SearchStopped("probe-budget-exhausted")
                    with search_phase("preparation"):
                        provider = recipe.prepare(execution=_execution, baseline=canonical_baseline)
                except SearchStopped as stop:
                    preparation_stop = stop.reason
                    provider = UnavailableGenerationProvider(
                        recipe.name, recipe.policy,
                        Refusal("generation-search-stopped", stop.reason))
                except Exception as exc:  # noqa: BLE001 - ancillary provider construction
                    refusal = _exception_refusal("preparation", recipe.name, exc)
                    provider = UnavailableGenerationProvider(recipe.name, recipe.policy, refusal)
                recipe.bind(provider)
            result = generate(
                canonical_baseline, provider, contract=contract,
                generation_spec=generation_spec,
                probe_budget=0 if preparation_stop else probe_budget,
                baseline_policy=baseline_policy, _preparation_stop=preparation_stop,
                _execution=_execution)
            searches.append(result)
    return combine_generation(searches)


def _record_refusal(telemetry, refusal, *, stop=True):
    counts = telemetry["refusals"]
    reasons = telemetry["refusal_why"]
    counts[refusal.kind] = counts.get(refusal.kind, 0) + 1
    reasons.setdefault(refusal.kind, refusal.why)
    telemetry["refusal_detail"].setdefault(
        refusal.kind, freeze_record_value(refusal.as_dict().get("detail", {}),
                                         "generation refusal detail"))
    lease = active_search()
    if lease is not None and (stop or lease.owner.policy.debug):
        # Unscoped failures stop the direction. A provider can explicitly report
        # a narrower unavailable capability; debug still stops at its first failure.
        # Explicit legacy Probe-count experiments retain their per-query policy.
        raise SearchFailed(refusal)


def _exception_refusal(stage, provider, exc):
    if isinstance(exc, Refused):
        return exc.refusal
    return Refusal("generation-" + stage + "-failed",
                   f"the search provider failed during {stage}",
                   provider=provider, error=f"{type(exc).__name__}: {exc}")


def _member_faults(stimulus, contract, spec):
    if stimulus.replay_policy is not None or spec is None:
        return stimulus.faults_against(contract)
    return stimulus_admissible(stimulus, contract, horizon=spec.horizon,
                              reset_hold_cycles=spec.reset_hold_cycles,
                              queue_bound=spec.queue_bound)


def _member_refusal(stimulus, contract, spec, *, role):
    if type(stimulus) is not Stimulus:
        return Refusal(
            f"generation-{role}-not-a-stimulus",
            "a generation worklist contains complete Stimulus records; this value has "
            "no canonical schedule, queue identity, or domain membership to validate",
            got=type(stimulus).__name__)
    faults = _member_faults(stimulus, contract, spec)
    if faults:
        return Refusal(
            f"generation-{role}-outside-domain",
            "this stimulus is not a member of the frozen generation domain",
            stimulus=stimulus.fingerprint, faults=faults)
    return None


def _baseline(baseline, contract, spec):
    if type(baseline) not in (tuple, list):
        raise Refused(Refusal(
            "generation-baseline-not-a-sequence",
            "the versioned mathematical set must be represented as an exact-byte-"
            "sorted finite sequence; a Python set can collapse a fingerprint collision "
            "before the scheduler sees both bodies",
            got=type(baseline).__name__))
    members = tuple(baseline)
    if not members:
        raise Refused(Refusal(
            "generation-baseline-empty",
            "Appendix A.3 starts from a nonempty versioned baseline; an empty queue "
            "would make generation vacuous"))
    pairs = []
    seen = set()
    fingerprints = {}
    for index, stimulus in enumerate(members):
        refusal = _member_refusal(stimulus, contract, spec, role="baseline")
        if refusal is not None:
            detail = dict(refusal.detail)
            detail.setdefault("index", index)
            raise Refused(Refusal(refusal.kind, refusal.why, **detail))
        body = stimulus_bytes(stimulus)
        expected = hashlib.sha256(body).hexdigest()
        if stimulus.fingerprint != expected:
            raise Refused(Refusal(
                "generation-baseline-fingerprint-inconsistent",
                "the baseline member's display fingerprint is not the digest of its "
                "exact canonical stimulus bytes",
                index=index, offered=stimulus.fingerprint,
                expected=expected))
        if body in seen:
            raise Refused(Refusal(
                "generation-baseline-duplicate",
                "the baseline is a set; one stimulus occurring twice would seed two "
                "worklist visits for one member",
                index=index, stimulus=stimulus.fingerprint))
        prior = fingerprints.get(stimulus.fingerprint)
        if prior is not None and prior != body:
            raise Refused(Refusal(
                "generation-stimulus-digest-collision",
                "two distinct canonical stimulus byte strings have one fingerprint; "
                "deduplicating by that display identity would merge different runs",
                fingerprint=stimulus.fingerprint))
        seen.add(body)
        fingerprints[stimulus.fingerprint] = body
        pairs.append((body, stimulus))
    canonical = tuple(stimulus for _, stimulus in sorted(pairs))
    if members != canonical:
        raise Refused(Refusal(
            "generation-baseline-not-canonical",
            "the baseline sequence is not ordered by the exact canonical stimulus "
            "bytes, so two callers could initialize different FIFO orders from one set",
            offered=[s.fingerprint for s in members],
            canonical=[s.fingerprint for s in canonical]))
    return canonical


def _request_refusal(seed, request, targetable, catalogue_digest="", *, _checked_fields=None):
    if type(request) not in (ProbeRequest, QueryTemplate):
        return Refusal(
            "generation-select-result-not-a-probe-request",
            "Select must return typed ProbeRequest records",
            got=type(request).__name__)
    if type(request) is ProbeRequest:
        expected_identity = hashlib.sha256(request.identity_bytes).hexdigest()
        if request.identity != expected_identity:
            return Refusal(
                "generation-request-identity-inconsistent",
                "the request's display identity is not the digest of its exact canonical "
                "request bytes",
                offered=request.identity, expected=expected_identity)
    if catalogue_digest and request.catalogue_digest != catalogue_digest:
        return Refusal(
            "generation-request-catalogue-mismatch",
            "a provider request names a different catalogue from the immutable "
            "manifest frozen before generation",
            request=request.identity, expected=catalogue_digest,
            got=request.catalogue_digest or None)
    if type(request.rank) is not int or isinstance(request.rank, bool) \
            or request.rank < 0:
        return Refusal(
            "generation-request-rank-invalid",
            "a request's policy rank is a nonnegative integer",
            request=request.identity, got=repr(request.rank))
    if not request.occurrence.strip():
        return Refusal(
            "generation-request-occurrence-empty",
            "a local query must identify the seed occurrence or schedule segment it is "
            "about", request=request.identity)
    if not request.targets or any(not target.strip() for target in request.targets):
        return Refusal(
            "generation-request-targets-empty",
            "Probe asks for at least one enabled generation target",
            request=request.identity)
    if len(set(request.targets)) != len(request.targets):
        return Refusal(
            "generation-request-targets-duplicate",
            "a target set contains each goal once",
            request=request.identity)
    unknown = sorted(set(request.targets) - targetable)
    if unknown:
        return Refusal(
            "generation-request-target-outside-adapter",
            "Select presented goals for which this provider declares no generation "
            "adapter", request=request.identity, unknown=unknown)
    if not request.mutable_fields:
        return Refusal(
            "generation-request-mutable-fields-empty",
            "Probe must name a nonempty finite set of stimulus fields it may solve",
            request=request.identity)

    # Only this Select's immutable seed owns these successful source-map checks.
    # Targets/ranks/catalogue above are still checked per question. A bad map is
    # never cached, so its refusal retains the actual offending request identity.
    if _checked_fields is not None and request.mutable_fields in _checked_fields:
        return None
    queues = {name: txns for name, txns in seed.queues}
    coordinates = set()
    source_of = {}
    for field in request.mutable_fields:
        if type(field) is not StimulusField:
            return Refusal(
                "generation-request-field-not-typed",
                "every mutable field is a StimulusField with a queue coordinate and "
                "source position", request=request.identity,
                got=type(field).__name__)
        if not field.endpoint:
            return Refusal(
                "generation-request-field-endpoint-empty",
                "a mutable queue field must name its endpoint",
                request=request.identity)
        for name, value in (("transaction", field.transaction),
                            ("field", field.field),
                            ("source_position", field.source_position)):
            if type(value) is not int or isinstance(value, bool) or value < 0:
                return Refusal(
                    "generation-request-source-map-invalid",
                    "transaction, field, and source positions are nonnegative integers",
                    request=request.identity, component=name, got=repr(value))
        if field.source_position >= len(seed.slots):
            return Refusal(
                "generation-request-source-map-outside-schedule",
                "a source position must name a slot in the seed's complete schedule",
                request=request.identity, endpoint=field.endpoint,
                source_position=field.source_position, slots=len(seed.slots))
        if field.endpoint not in seed.slots[field.source_position].offers:
            return Refusal(
                "generation-request-source-map-without-offer",
                "a queue field's release position must offer its endpoint",
                request=request.identity, endpoint=field.endpoint,
                source_position=field.source_position)
        txns = queues.get(field.endpoint)
        if txns is None or field.transaction >= len(txns) \
                or field.field >= len(txns[field.transaction]):
            return Refusal(
                "generation-request-field-outside-seed",
                "a mutable field must identify an existing finite field of this seed",
                request=request.identity, field=field.as_dict())
        if field.coordinate in coordinates:
            return Refusal(
                "generation-request-field-duplicate",
                "the same stimulus field appears twice in one mutable set",
                request=request.identity, field=field.as_dict())
        coordinates.add(field.coordinate)
        transaction = (field.endpoint, field.transaction)
        prior = source_of.setdefault(transaction, field.source_position)
        if prior != field.source_position:
            return Refusal(
                "generation-request-source-map-conflict",
                "fields of one queue transaction map to two release positions",
                request=request.identity, endpoint=field.endpoint,
                transaction=field.transaction, positions=sorted(
                    {prior, field.source_position}))

    for endpoint in sorted({key[0] for key in source_of}):
        rows = sorted((transaction, position)
                      for (name, transaction), position in source_of.items()
                      if name == endpoint)
        positions = [position for _, position in rows]
        if any(after <= before for before, after in pairwise(positions)):
            return Refusal(
                "generation-request-source-map-nonmonotone",
                "later queue transactions must map to later release positions",
                request=request.identity, endpoint=endpoint, map=rows)
    if _checked_fields is not None:
        _checked_fields.add(request.mutable_fields)
    return None


def _selection(seed, selected, targetable, telemetry, catalogue_digest="", *,
               scoped_refusal=None):
    if isinstance(selected, Refusal):
        telemetry["selection_refusals"] += 1
        _record_refusal(telemetry, selected, stop=scoped_refusal != selected)
        return ()
    if type(selected) is not ProbeSelection:
        refusal = Refusal(
            "generation-select-result-not-typed",
            "Select returns one closed ProbeSelection so even an empty answer records "
            "the source catalogue the provider actually used",
            got=type(selected).__name__)
        telemetry["selection_refusals"] += 1
        _record_refusal(telemetry, refusal)
        return ()
    if selected.catalogue_digest != catalogue_digest:
        raise Refused(Refusal(
            "generation-selection-catalogue-mismatch",
            "the provider selected under a different source catalogue from the "
            "manifest frozen before generation",
            expected=catalogue_digest, got=selected.catalogue_digest))
    telemetry["catalogue_confirmations"] += 1
    original_seed = seed.fingerprint
    if selected.recorded_seed is not None:
        from .core.stimulus import validate_resolved_request

        try:
            validate_resolved_request(selected.recorded_seed, seed)
        except (TypeError, ValueError) as exc:
            telemetry["selection_refusals"] += 1
            _record_refusal(telemetry, Refusal(
                "generation-selection-runtime-binding-mismatch", str(exc)))
            return ()
        seed = selected.recorded_seed
    if not set(selected.constant_false_goals) <= set(targetable):
        raise Refused(Refusal(
            "generation-selection-exclusion-outside-catalogue",
            "selection-local IR exclusions must belong to the frozen target capability"))
    if selected.constant_false_goals:
        telemetry["selection_constant_false"].append({
            "seed": original_seed, "recorded_seed": seed.fingerprint,
            "goals": list(selected.constant_false_goals)})
    selected = selected.requests

    if type(selected) is QueryPlan:
        valid = []
        checked_fields = set()
        for template in selected.templates:
            refusal = _request_refusal(seed, template, targetable, catalogue_digest,
                                       _checked_fields=checked_fields)
            if refusal is not None:
                if refusal.kind == "generation-request-catalogue-mismatch":
                    raise Refused(refusal)
                telemetry["request_refusals"] += 1
                _record_refusal(telemetry, refusal)
            else:
                valid.append(template)
        telemetry["requests_selected"] += len(valid)
        return selected if len(valid) == len(selected) else selected.retaining(valid)

    by_identity = {}
    identity_body = {}
    poisoned_identities = set()
    for request in selected:
        refusal = _request_refusal(
            seed, request, targetable, catalogue_digest)
        if refusal is not None:
            if refusal.kind == "generation-request-catalogue-mismatch":
                # This is not a bad local solve.  The provider is executing a request
                # under a different source declaration from the provider, so retaining
                # the request would publish one search with two source identities.
                raise Refused(refusal)
            telemetry["request_refusals"] += 1
            _record_refusal(telemetry, refusal)
            continue
        body = request.identity_bytes
        if request.identity in poisoned_identities:
            refusal = Refusal(
                "generation-request-digest-collision",
                "this request identity already denotes two distinct canonical byte "
                "strings, so no request under it is safe to probe",
                request=request.identity)
            telemetry["request_refusals"] += 1
            _record_refusal(telemetry, refusal)
            continue
        prior_body = identity_body.get(request.identity)
        if prior_body is not None and prior_body != body:
            # Poison the identity and retract the earlier request.  Probing either body
            # under one display ID would make provenance ambiguous.
            by_identity.pop(prior_body, None)
            poisoned_identities.add(request.identity)
            refusal = Refusal(
                "generation-request-digest-collision",
                "two different request byte strings have one canonical identity, so "
                "both requests are refused",
                request=request.identity)
            telemetry["request_refusals"] += 2
            _record_refusal(telemetry, refusal)
            continue
        identity_body[request.identity] = body
        prior = by_identity.get(body)
        if prior is None or request.rank < prior.rank:
            by_identity[body] = request
        if prior is not None:
            telemetry["duplicate_requests"] += 1

    ordered = tuple(sorted(by_identity.values(),
                           key=lambda request: (request.rank,
                                                request.identity_bytes)))
    telemetry["requests_selected"] += len(ordered)
    return ordered


def _candidate_refusal(seed, candidate, request, contract, spec):
    refusal = _member_refusal(candidate, contract, spec, role="candidate")
    if refusal is not None:
        return refusal

    # Everything outside queue payloads is fixed by the request.  Compare the records
    # directly, not selected projections: a provider changing one control, reset/tick,
    # memory policy, or initial-state policy has described a different local query.
    for name in (
            "slots", "clock_schedule", "memory_policy", "memory_environment",
            "replay_policy", "initial_state", "source"):
        before, after = getattr(seed, name), getattr(candidate, name)
        if name in ("clock_schedule", "memory_environment"):
            # Worker replies reconstruct these immutable records. Their Python
            # identities are not their values; all declared fields must agree.
            before = None if before is None else before.as_dict()
            after = None if after is None else after.as_dict()
        if after != before:
            return Refusal(
                "generation-candidate-mutates-nonselected-field",
                "Probe changed part of the complete stimulus that its request did not "
                "declare mutable", request=request.identity, component=name)

    seed_queues = {name: txns for name, txns in seed.queues}
    candidate_queues = {name: txns for name, txns in candidate.queues}
    if tuple(seed_queues) != tuple(candidate_queues):
        return Refusal(
            "generation-candidate-queue-shape-changed",
            "Probe added, removed, or renamed an endpoint queue",
            request=request.identity, before=list(seed_queues),
            after=list(candidate_queues))
    allowed = {field.coordinate for field in request.mutable_fields}
    changed = set()
    for endpoint, before in seed_queues.items():
        after = candidate_queues[endpoint]
        if len(before) != len(after):
            return Refusal(
                "generation-candidate-queue-shape-changed",
                "Probe changed the number of transactions in a queue",
                request=request.identity, endpoint=endpoint,
                before=len(before), after=len(after))
        for transaction, (left, right) in enumerate(zip(before, after)):
            if len(left) != len(right):
                return Refusal(
                    "generation-candidate-queue-shape-changed",
                    "Probe changed the number of finite fields in a transaction",
                    request=request.identity, endpoint=endpoint,
                    transaction=transaction, before=len(left), after=len(right))
            for field, (old, new) in enumerate(zip(left, right)):
                if old != new:
                    changed.add((endpoint, transaction, field))
    outside = sorted(changed - allowed)
    if outside:
        return Refusal(
            "generation-candidate-mutates-nonselected-field",
            "Probe changed queue fields outside the finite set its request named",
            request=request.identity,
            fields=[list(coordinate) for coordinate in outside])
    return None


def _provenance_row(provider, policy, seed, request, result, duplicate):
    return {
        "provider": str(provider),
        "policy": str(policy),
        "seed": seed.fingerprint,
        "request": request.identity,
        "rank": request.rank,
        "targets": list(request.targets),
        "occurrence": request.occurrence,
        "catalogue_digest": request.catalogue_digest or None,
        "mutable_fields": [field.as_dict() for field in request.mutable_fields],
        "scope": result.scope.value,
        "duplicate": bool(duplicate),
    }


def generate(baseline, provider: GenerationProvider, *, contract,
             generation_spec: GenerationSpec | None, probe_budget: int | None,
             baseline_policy=None, _preparation_stop=None, _execution=None) -> GenerationResult:
    """Run the worklist, closing optional provider resources on every exit."""
    from contextlib import ExitStack

    if probe_budget is None and active_search() is None:
        with search_scope(SearchBudget(SearchPolicy()).direction(1)):
            return generate(baseline, provider, contract=contract,
                            generation_spec=generation_spec, probe_budget=None,
                            baseline_policy=baseline_policy, _preparation_stop=_preparation_stop,
                            _execution=_execution)
    run = None
    extensions = GenerationExtensions.bind(provider)
    scope = extensions.search_scope
    runtime_seeds = any(stimulus.replay_policy is not None for stimulus in baseline)
    if runtime_seeds and not extensions.runtime_guidance:
        scope = None  # No solver context can consume an unresolved runtime schedule.
    stack = ExitStack()
    reason = _preparation_stop
    failure = None
    lease = active_search()
    if lease is not None:
        stack.callback(lease.resources.close)
    engine_cap = probe_budget
    if engine_cap is None and lease is not None and lease.owner.policy.max_probes is not None:
        engine_cap = max(0, lease.owner.policy.max_probes - lease.owner.probes)
    if lease is not None and lease.deadline is not None:
        # The shared scheduler enforces any Probe cap. A timed worker must not
        # impose a second exchange quota on on-demand seed selections.
        engine_cap = None

    def enter_scope():
        try:
            stack.enter_context(nullcontext() if scope is None else scope(probe_budget=engine_cap))
        except Exception as exc:
            raise SearchFailed(_exception_refusal("session", provider.name, exc)) from exc

    try:
        run = _SearchRun(baseline, provider, contract=contract,
                         generation_spec=generation_spec, probe_budget=probe_budget,
                         baseline_policy=baseline_policy, execution=_execution,
                         extensions=extensions)
        result = run.run(enter_scope=enter_scope, stop_reason=_preparation_stop)
    except SearchStopped as stop:
        reason = stop.reason
        if run is None:
            raise  # No search state exists: do not invent a valid partial result.
        if isinstance(stop, SearchFailed):
            failure = stop.refusal
    except Refused:
        # Scheduler identity/domain checks are not provider execution failures.
        raise
    except Exception as exc:
        if run is None:
            raise  # Invalid scheduler inputs are not an optional search failure.
        reason, failure = "search-failed", _exception_refusal("execution", provider.name, exc)
    finally:
        with search_phase("cleanup", cleanup=True):
            try:
                stack.close()
            except Exception as exc:
                if run is None:
                    raise
                reason, failure = "search-failed", _exception_refusal("cleanup", provider.name, exc)
    if reason is not None:
        run.telemetry["termination"] = reason
        run.telemetry["queue_remaining"] = len(run.queue)
        if failure is not None:
            run.telemetry["failure"] = freeze_record_value(failure.as_dict())
        result = run.result()
    # Finalize diagnostics after cleanup on every exit, including an ordinary
    # time/Probe budget stop. The worklist's early exits retain answers but do
    # not run its normal tail; keeping receipts there lost precisely the costly
    # or interrupted operations needed to diagnose search.
    telemetry = dict(result.telemetry.items())
    try:
        _provider_diagnostics(provider, telemetry, extensions)
    except Exception as exc:  # noqa: BLE001 - ancillary search diagnostics
        diagnostic = _exception_refusal("diagnostics", provider.name, exc)
        telemetry["diagnostic_failure"] = freeze_record_value(diagnostic.as_dict())
        if failure is None:
            failure = diagnostic
            telemetry["failure"] = freeze_record_value(failure.as_dict())
        reason = reason or "search-failed"
    telemetry["termination"] = reason or telemetry["termination"]
    lease = active_search()
    if lease is not None:
        # Preserve typed frozen mappings (notably GoalCapabilityUse). A JSON
        # round-trip through FrozenMap would turn them into untyped pair tuples.
        telemetry["search_budget"] = freeze_record_value(
            lease.report(telemetry["termination"]), "search budget receipt")
    result = GenerationResult(
        result.provider, result.baseline, result.generated,
        {key: [_json_value(row) for row in rows] for key, rows in result.provenance.items()},
        telemetry, contract=result.contract, generation_spec=result.generation_spec,
        baseline_policy=result.baseline_policy)
    if failure is not None and lease is not None and lease.owner.policy.debug:
        raise Refused(Refusal("generation-debug-stop", "debug mode stopped at a search failure",
                             cause=failure.as_dict(), search=result.as_dict()))
    return result


def _provider_diagnostics(provider, telemetry, extensions):
    try:
        cache_telemetry = provider.cache_telemetry()
    except Exception as exc:  # noqa: BLE001 - provider containment boundary
        raise Refused(Refusal(
            "generation-provider-cache-telemetry-unavailable",
            "a provider could not finalize its generation-cache diagnostics",
            provider=provider.name,
            error=f"{type(exc).__name__}: {exc}")) from None
    if type(cache_telemetry) is not GenerationCacheTelemetry:
        raise Refused(Refusal(
            "generation-provider-cache-telemetry-invalid",
            "a provider must finalize the exact GenerationCacheTelemetry record",
            provider=provider.name,
            got=type(cache_telemetry).__name__))
    telemetry["context_cache"] = freeze_record_value(
        cache_telemetry.as_dict(), "generation context-cache telemetry")
    execution_cost = extensions.execution_cost
    telemetry["execution_cost"] = freeze_record_value(
        None if execution_cost is None else execution_cost(),
        "generation execution cost")


class _SearchRun:
    """Coordinator-owned state for one independent source direction.

    The lifecycle wrapper owns cancellation and provider cleanup. This owner
    alone admits stimuli, records answers and publishes replay feedback. Seed
    generators retain their own pending questions, local proofs and unfinished
    construction, while the existing worklist owns lane scheduling.
    """

    def __init__(self, baseline, provider, *, contract, generation_spec,
                 probe_budget, baseline_policy, execution, extensions):
        self.baseline, self.provider = baseline, provider
        self.contract, self.generation_spec = contract, generation_spec
        self.probe_budget, self.baseline_policy = probe_budget, baseline_policy
        self.execution = execution
        self.extensions = extensions

        # Runtime replay has no user-supplied bounded-analysis domain. Its complete
        # stimuli still pass contract/schedule checks in _member_faults; a supplied
        # finite domain adds its own horizon/queue constraints there.
        if self.generation_spec is not None and type(self.generation_spec) is not GenerationSpec:
            raise Refused(Refusal(
                "generation-spec-not-frozen",
                "an explicit generation domain must be the admitted task's frozen GenerationSpec",
                got=type(self.generation_spec).__name__))
        if self.probe_budget is not None and (type(self.probe_budget) is not int or self.probe_budget < 0):
            raise Refused(Refusal(
                "generation-probe-budget-invalid",
                "a probe budget is a nonnegative integer number of Probe calls",
                got=repr(self.probe_budget)))

        self.provider_name = getattr(self.provider, "name", None)
        self.provider_policy = getattr(self.provider, "policy", None)
        if type(self.provider_name) is not str or not self.provider_name.strip():
            raise Refused(Refusal(
                "generation-provider-identity-invalid",
                "a provider must declare a nonempty stable T-to-A identity",
                got=repr(self.provider_name)))
        if type(self.provider_policy) is not str or not self.provider_policy.strip():
            raise Refused(Refusal(
                "generation-provider-policy-invalid",
                "a provider must declare the nonempty versioned policy that orders its "
                "requests", provider=self.provider_name,
                got=repr(self.provider_policy)))
        manifest_of = getattr(self.provider, "catalogue_manifest", None)
        if not callable(manifest_of):
            raise Refused(Refusal(
                "generation-provider-catalogue-missing",
                "a provider must expose the finite CatalogueManifest from which it selects "
                "targets",
                provider=self.provider_name))
        try:
            self.provider_catalogue = manifest_of()
        except Refused:
            raise
        except Exception as exc:  # noqa: BLE001 - provider containment boundary
            raise Refused(Refusal(
                "generation-provider-catalogue-unavailable",
                "a provider could not supply its frozen target catalogue",
                provider=self.provider_name,
                error=f"{type(exc).__name__}: {exc}")) from None
        if type(self.provider_catalogue) is not CatalogueManifest:
            raise Refused(Refusal(
                "generation-provider-catalogue-invalid",
                "a provider's target catalogue must be an exact CatalogueManifest",
                provider=self.provider_name, got=type(self.provider_catalogue).__name__))
        self.catalogue_digest = self.provider_catalogue.digest()
        offered_targets = getattr(self.provider, "targetable_goals", None)
        if type(offered_targets) not in (tuple, list, set, frozenset):
            raise Refused(Refusal(
                "generation-provider-targets-not-materialized",
                "a provider must declare its finite generation goal subset as a "
                "materialized collection",
                provider=self.provider_name, got=type(offered_targets).__name__))
        declared = tuple(str(goal) for goal in offered_targets)
        self.targetable = set(declared)
        replay_guided = self.extensions.replay_guided
        if len(self.targetable) != len(declared) or any(not goal.strip() for goal in declared):
            raise Refused(Refusal(
                "generation-provider-targets-invalid",
                "a provider's targetable goal subset contains distinct nonempty IDs",
                provider=self.provider_name))
        unknown_targets = sorted(self.targetable - set(self.provider_catalogue))
        if unknown_targets:
            raise Refused(Refusal(
                "generation-provider-target-outside-catalogue",
                "a provider may target only goals declared by its own frozen source "
                "catalogue",
                provider=self.provider_name, goals=unknown_targets,
                catalogue=self.catalogue_digest))
        capability_image = None
        capability_of = self.extensions.goal_capability_use
        if capability_of is not None:
            capability = capability_of()
            if capability is not None and type(capability) is not GoalCapabilityUse:
                raise Refused(Refusal(
                    "generation-provider-goal-capability-invalid",
                    "a provider exposing a GoalPolicy handoff must return the exact "
                    "GoalCapabilityUse record",
                    provider=self.provider_name))
            if capability is not None:
                capability.reconcile(
                    LOCAL_GUIDANCE, self.provider_policy, self.provider_catalogue, self.targetable)
                capability_image = freeze_record_value(capability.as_dict())

        self.baseline = _baseline(self.baseline, self.contract, self.generation_spec)
        baseline_bodies = tuple(stimulus_bytes(stimulus) for stimulus in self.baseline)
        baseline_digest = hashlib.sha256(b"".join(
            len(body).to_bytes(8, "big") + body for body in baseline_bodies)).hexdigest()
        self.queue = deque(self.baseline)
        self.seen = {body: stimulus for body, stimulus in zip(baseline_bodies, self.baseline)}
        self.fingerprints = {stimulus.fingerprint: body
                        for body, stimulus in zip(baseline_bodies, self.baseline)}
        self.generated = {}
        self.provenance = {}
        self.coverage = SearchCoverage(self.provider_catalogue, self.targetable) if replay_guided else None
        self.explores_contexts = self.extensions.explores_contexts


        self.telemetry = {
            # The source catalogue owns local-guidance targets independently of the
            # evaluated-artifact catalogue carried by ``GeneratedStimulusSet``. Version 8
            # retains a bounded diagnostic row for every budgeted Probe answer as well as
            # the provider's backend-neutral context-cache record.  Outcome counts alone
            # cannot distinguish a solver timeout from an unsupported encoding. Version 8
            # also retains preparation cost and the first structured refusal per kind.
            # Version 9 distinguishes a stopped capability from an exhausted queue.
            # Request-capability skips are not Probe calls or solver answers.
            # Versions14/15 retain complete query target sets once per direction.
            # Version 16 binds admitted SAT proposals to their own native feedback.
            "version": 16 if replay_guided else 14,
            "provider": self.provider_name,
            "provider_policy": self.provider_policy,
            "provider_targets": tuple(sorted(self.targetable)),
            "catalogue_digest": self.catalogue_digest or None,
            "goal_capability_use": capability_image,
            # A confirmation is an immutable Select result, including an empty result; it
            # is not a before/after observation of the provider's live capability.
            "catalogue_confirmations": 0,
            "baseline_members": len(self.baseline),
            "baseline_digest": baseline_digest,
            "probe_budget": self.probe_budget,
            "probes": 0,
            "seeds_selected": 0,
            "requests_selected": 0,
            "requests_deferred": 0,
            "requests_unavailable": 0,
            "requests_equivalent": 0,
            "requests_constant_false": 0,
            "request_targets_constant_false": 0,
            "selection_constant_false": [],
            "local_unsat_reuse": [],
            "duplicate_requests": 0,
            "selection_refusals": 0,
            "request_refusals": 0,
            "probe_refusals": 0,
            "candidate_refusals": 0,
            "outcomes": {outcome.value: 0 for outcome in Outcome},
            "answers": [],
            "target_sets": [],
            "candidates_retained": 0,
            "candidates_duplicate": 0,
            "queue_peak": len(self.queue),
            "refusals": {},
            "refusal_why": {},
            "refusal_detail": {},
            "provider_refusal": None,
        }
        self.target_set_ids = {}
        self.candidate_answers = {}
        if self.coverage is not None:
            self.telemetry.update(coverage=freeze_record_value(self.coverage.as_dict()), requests_covered=0)

    def run(self, *, enter_scope, stop_reason):
        if stop_reason is not None:
            raise SearchStopped(stop_reason)
        self.check_scoring_stop()
        check_search_work()
        if enter_scope is not None:
            enter_scope()

        if (self.targetable and any(stimulus.replay_policy is not None for stimulus in self.baseline)
                and not self.extensions.runtime_guidance):
            refusal = Refusal(
                "generation-runtime-seed-not-resolved",
                "this provider needs a recorded schedule before goal-guided search; "
                "runtime baseline requests are retained for concrete scoring")
            _record_refusal(self.telemetry, refusal, stop=False)
            self.telemetry.update(termination="provider-unavailable", queue_remaining=len(self.queue),
                             provider_refusal=freeze_record_value(refusal.as_dict()))
            return GenerationResult(self.provider_name, self.baseline, (), {}, self.telemetry,
                contract=self.contract.digest, generation_spec=self.generation_spec,
                baseline_policy=self.baseline_policy)

        self.prepare_baseline()
        self.check_scoring_stop()
        from .search_parallel import (
            run_seed_worklist, search_workers,
        )

        # A Probe count defines a reproducible search only in one fixed order.
        # Parallel lanes would start the capped Probes in timing order and then
        # cancel the in-flight remainder once the count is reached, so the
        # outcome would depend on the host CPU count. Time-only search stays
        # parallel.
        lease = active_search()
        counted = self.probe_budget is not None or (
            lease is not None and lease.owner.policy.max_probes is not None)
        self.workers = min(len(self.baseline) or 1, search_workers(
            self.provider, legacy=counted, _extensions=self.extensions))
        self.in_flight_targets = {}
        # Coordinator-owned allocation hints, not hits, solver answers or proofs.
        # Count a target family when a lane starts it, rather than counting each
        # prefix/probe slice. Other seeds can then try an unvisited family instead
        # of all workers repeatedly choosing the source's first group.
        self.family_visits = {}
        self.successor_targets = {}
        self.context_visits = {}
        self.telemetry["parallel_target_splits"] = 0
        self.telemetry["goal_family_starts"] = 0
        self.lane_extensions = {id(self.provider): self.extensions}

        run_seed_worklist(self.queue, self.provider, self.explore_seed, self.can_continue,
                          workers=self.workers, telemetry=self.telemetry, _extensions=self.extensions)

        self.telemetry["queue_remaining"] = len(self.queue)
        refusal = self.extensions.direction_refusal()
        if refusal is not None:
            self.telemetry["provider_refusal"] = freeze_record_value(
                refusal.as_dict(), "generation direction refusal")
            if refusal.kind not in self.telemetry["refusals"]:
                _record_refusal(self.telemetry, refusal, stop=False)
            self.telemetry["termination"] = "provider-unavailable"
        elif self.coverage is not None and not self.coverage.remaining and not self.explores_contexts:
            self.telemetry["termination"] = "goals-covered"
        else:
            self.telemetry["termination"] = (
                "probe-budget-exhausted"
                if (self.probe_budget is not None and self.telemetry["probes"] >= self.probe_budget
                    and (self.queue or self.telemetry["requests_deferred"]
                         or self.telemetry["worklist"]["suspended_at_stop"])) else "queue-empty")
        return self.result()

    def prepare_baseline(self):
        """Observe mandatory runs before probing; retain preselection per seed."""
        self.preselected = {}
        prepare_seeds = self.extensions.prepare_seeds
        select_many = self.extensions.select_many
        self.observe_many = self.extensions.observe_many
        lease = active_search()
        bulk_seeds = lease is None or lease.deadline is None
        available = self.extensions.direction_refusal() is None

        baseline_observed = False
        if self.coverage is not None and self.observe_many is not None:
            try:
                observation_seeds = self.baseline
                if (self.execution is not None and self.baseline
                        and all(seed.replay_policy is not None for seed in self.baseline)):
                    from .replay_suite import build_replay_suite

                    # The command already checked both mandatory phases. Continuous
                    # hits retire goals too, but keep their own record identity and
                    # do not enlarge the isolated seed queue or borrow another
                    # search direction's generated executions.
                    observation_seeds = build_replay_suite(self.contract, self.baseline).stimuli
                with search_phase("baseline-feedback"):
                    feedback = self.observe_many(observation_seeds)
                    if feedback is not None:
                        if len(feedback) != len(observation_seeds):
                            raise ValueError("baseline observation must answer each mandatory run")
                        for seed, row in zip(observation_seeds, feedback):
                            if isinstance(row, Refusal):
                                raise Refused(row)
                            self.coverage.observe(seed, row)
                        baseline_observed = True
            except Exception as exc:  # noqa: BLE001 - ancillary baseline observation
                _record_refusal(self.telemetry, _exception_refusal("observation", self.provider_name, exc))
            self.telemetry["coverage"] = freeze_record_value(self.coverage.as_dict())
            self.telemetry["baseline_coverage"] = freeze_record_value(self.coverage.as_dict())
        if available and self.coverage is not None and self.probe_budget != 0 and not baseline_observed:
            # No Probe is allowed before every baseline member has had its replay
            # opportunity. Check the shared deadline between seeds, including failures.
            if prepare_seeds is not None:
                try:
                    with search_phase("seed_replay"):
                        prepare_seeds(self.baseline)
                except Refused as exc:
                    self.preselected = {stimulus_bytes(seed): exc.refusal for seed in self.baseline}
                except Exception as exc:  # noqa: BLE001 - ancillary baseline replay
                    refusal = Refusal("generation-seed-preparation-failed",
                                       "baseline replay preparation failed",
                                       error=f"{type(exc).__name__}: {exc}")
                    self.preselected = {stimulus_bytes(seed): refusal for seed in self.baseline}
            elif select_many is not None:
                try:
                    with search_phase("seed_replay_select"):
                        selected_many = select_many(self.baseline)
                    if (type(selected_many) not in (tuple, list)
                            or len(selected_many) != len(self.baseline)):
                        raise Refused(Refusal(
                            "generation-select-batch-incomplete",
                            "batched Select must answer every baseline seed exactly once"))
                    self.preselected = {
                        stimulus_bytes(seed): self.observe_selection(seed, selected)
                        for seed, selected in zip(self.baseline, selected_many)
                    }
                except Refused as exc:
                    self.preselected = {stimulus_bytes(seed): exc.refusal for seed in self.baseline}
                except Exception as exc:  # noqa: BLE001 - ancillary baseline selection
                    refusal = Refusal("generation-select-batch-failed",
                                       "baseline Select did not return a complete batch",
                                       error=f"{type(exc).__name__}: {exc}")
                    self.preselected = {stimulus_bytes(seed): refusal for seed in self.baseline}
            for seed in self.baseline:
                check_search_work()
                if self.extensions.direction_refusal() is not None:
                    break
                if stimulus_bytes(seed) not in self.preselected:
                    self.preselected[stimulus_bytes(seed)] = self.observe_selection(seed)
            self.telemetry["baseline_coverage"] = freeze_record_value(self.coverage.as_dict())
        elif (self.coverage is None and available and bulk_seeds
              and self.probe_budget != 0 and prepare_seeds is not None):
            try:
                with search_phase("seed_replay"):
                    prepare_seeds(self.baseline)
            except Refused as exc:
                self.preselected = {stimulus_bytes(seed): exc.refusal for seed in self.baseline}
            except Exception as exc:  # noqa: BLE001 - provider containment boundary
                refusal = Refusal(
                    "generation-seed-preparation-failed",
                    "the provider could not prepare its baseline replay batch",
                    provider=self.provider_name, error=f"{type(exc).__name__}: {exc}")
                self.preselected = {stimulus_bytes(seed): refusal for seed in self.baseline}
        elif (self.coverage is None and available and bulk_seeds
              and self.probe_budget != 0 and select_many is not None):
            try:
                with search_phase("seed_replay_select"):
                    selected_many = select_many(self.baseline)
            except Refused as exc:
                selected_many = tuple(exc.refusal for _seed in self.baseline)
            except Exception as exc:  # noqa: BLE001 - provider containment boundary
                refusal = Refusal(
                    "generation-select-batch-failed",
                    "the provider could not select from the finite baseline batch",
                    provider=self.provider_name,
                    error=f"{type(exc).__name__}: {exc}")
                selected_many = tuple(refusal for _seed in self.baseline)
            if type(selected_many) not in (tuple, list) \
                    or len(selected_many) != len(self.baseline):
                refusal = Refusal(
                    "generation-select-batch-incomplete",
                    "a batched Select must answer every baseline seed exactly once",
                    provider=self.provider_name, seeds=len(self.baseline))
                selected_many = tuple(refusal for _seed in self.baseline)
            self.preselected = {
                stimulus_bytes(seed): selected
                for seed, selected in zip(self.baseline, selected_many)
            }

    def selection_targets(self):
        return self.targetable if self.explores_contexts else self.coverage.remaining

    def check_scoring_stop(self):
        if self.execution is not None and self.execution.has_failure_witness:
            raise SearchStopped("candidate-failed")

    def observe_feedback(self, seed, feedback):
        self.coverage.observe(seed, feedback)
        for answer in self.candidate_answers.get(seed.fingerprint, ()):
            answer["native_replay"] = self.coverage.confirmation(
                seed.fingerprint, self.telemetry["target_sets"][answer["target_set"]], answer["goal"])
        self.telemetry["coverage"] = freeze_record_value(self.coverage.as_dict())

    def observe_selection(self, seed, selected=_NO_PRESELECTION):
        """Observe first; eligibility for another query does not erase hits."""
        try:
            if selected is _NO_PRESELECTION:
                if self.observe_many is not None and seed.fingerprint not in self.coverage.replays:
                    with search_phase("seed_replay"):
                        feedback = self.observe_many((seed,))
                    if feedback is not None:
                        if len(feedback) != 1:
                            raise ValueError("source observation must answer the new seed")
                        row, = feedback
                        if isinstance(row, Refusal):
                            self.check_scoring_stop()
                            return row
                        self.observe_feedback(seed, row)
                        self.check_scoring_stop()
                        if not self.coverage.remaining and not self.explores_contexts:
                            return ProbeSelection((), self.catalogue_digest, replay=row)
                with search_phase("seed_replay_select"):
                    selected = self.provider.select(seed, targets=self.selection_targets())
            if isinstance(selected, Refusal):
                return selected
            if (type(selected) is not ProbeSelection
                    or selected.catalogue_digest != self.catalogue_digest):
                raise Refused(Refusal(
                    "generation-replay-selection-invalid",
                    "replay-guided Select must confirm its frozen source catalogue"))
            self.observe_feedback(seed, selected.replay)
            self.check_scoring_stop()
            return selected
        except Refused as exc:
            return exc.refusal
        except Exception as exc:  # noqa: BLE001 - source replay containment
            return Refusal("generation-replay-feedback-unavailable",
                            "source replay did not supply valid search feedback",
                            error=f"{type(exc).__name__}: {exc}")

    def family_priority(self, targets):
        return min((self.family_visits.get(goal, 0) for goal in targets
                    if goal in self.coverage.targets and goal not in self.coverage.hits),
                   default=float("inf"))

    def can_continue(self):
        self.check_scoring_stop()
        return ((self.coverage is None or self.coverage.remaining or self.explores_contexts)
                and (self.probe_budget is None or self.telemetry["probes"] < self.probe_budget)
                and self.extensions.direction_refusal() is None)

    def record_answer(self, seed, request, result, construction_seconds):
        """Record a typed local answer; this is not native execution evidence."""
        self.telemetry["outcomes"][result.outcome.value] += 1
        target_set = self.target_set_ids.get(request.targets)
        if target_set is None:
            target_set = len(self.telemetry["target_sets"])
            self.target_set_ids[request.targets] = target_set
            self.telemetry["target_sets"].append(list(request.targets))
        answer = {
            "seed": seed.fingerprint,
            "request": request.identity,
            "rank": request.rank,
            "occurrence": request.occurrence,
            "target_count": len(request.targets),
            "target_set": target_set,
            "construction_seconds": round(construction_seconds, 6),
            **result.as_dict(),
        }
        self.telemetry["answers"].append(answer)
        return answer

    def admit_candidate(self, seed, request, result, answer):
        """Atomically validate/deduplicate a proposal and retain its provenance.

        A duplicate can add provenance but cannot enter the queue again. The
        native replay that can retire goals remains a separate operation.
        """
        candidate = result.candidate
        refusal = _candidate_refusal(
            seed, candidate, request, self.contract, self.generation_spec)
        if refusal is not None:
            self.telemetry["candidate_refusals"] += 1
            _record_refusal(self.telemetry, refusal)
            return None

        body = stimulus_bytes(candidate)
        expected = hashlib.sha256(body).hexdigest()
        if candidate.fingerprint != expected:
            refusal = Refusal(
                "generation-candidate-fingerprint-inconsistent",
                "the candidate's display fingerprint is not the digest of its "
                "exact canonical stimulus bytes",
                offered=candidate.fingerprint, expected=expected)
            self.telemetry["candidate_refusals"] += 1
            _record_refusal(self.telemetry, refusal)
            return None
        duplicate = body in self.seen
        prior_body = self.fingerprints.get(candidate.fingerprint)
        if not duplicate and prior_body is not None and prior_body != body:
            refusal = Refusal(
                "generation-stimulus-digest-collision",
                "two distinct canonical stimulus byte strings have one fingerprint",
                fingerprint=candidate.fingerprint)
            self.telemetry["candidate_refusals"] += 1
            _record_refusal(self.telemetry, refusal)
            return None
        row = _provenance_row(
            self.provider_name, self.provider_policy, seed, request, result, duplicate)
        check_execution()  # Admission is atomic: never retain a late/partial candidate.
        if self.coverage is not None:
            answer["stimulus"] = candidate.fingerprint
            answer["native_replay"] = self.coverage.confirmation(
                candidate.fingerprint, request.targets, answer["goal"])
            self.candidate_answers.setdefault(candidate.fingerprint, []).append(answer)
        rows = self.provenance.setdefault(candidate.fingerprint, [])
        if row not in rows:
            rows.append(row)
        if duplicate:
            self.telemetry["candidates_duplicate"] += 1
            return None
        self.seen[body] = candidate
        self.fingerprints[candidate.fingerprint] = body
        self.generated[body] = candidate
        self.queue.append(candidate)
        self.telemetry["candidates_retained"] += 1
        self.telemetry["queue_peak"] = max(self.telemetry["queue_peak"], len(self.queue))
        return candidate, body

    def explore_seed(self, seed, lane):
        """One seed's continuation; its local proofs never escape this generator."""
        from .search_parallel import ExploreSuccessor, QueryComplete, QueryWork, partition_targets

        ports = self.lane_extensions.get(id(lane))
        if ports is None:
            ports = self.lane_extensions[id(lane)] = GenerationExtensions.bind(lane)
        preferred = self.successor_targets.pop(seed.fingerprint, frozenset())
        if ports.direction_refusal() is not None:
            return
        check_search_work()
        self.telemetry["seeds_selected"] += 1
        try:
            selected = self.preselected.pop(stimulus_bytes(seed), _NO_PRESELECTION)
            if self.workers > 1:
                # Selection belongs to this lane's session, not the parent's.
                selected = _NO_PRESELECTION
            if selected is _NO_PRESELECTION:
                if self.coverage is not None and self.workers == 1:
                    selected = self.observe_selection(seed)
                elif self.coverage is not None:
                    targets = tuple(self.selection_targets())
                    selected = yield ("seed_replay_select",
                                      lambda: lane.select(seed, targets=targets))
                    selected = self.observe_selection(seed, selected)
                else:
                    selected = yield ("select", lambda: lane.select(seed))
        except Refused as exc:
            selected = exc.refusal
        except Exception as exc:  # noqa: BLE001 - provider containment boundary
            selected = Refusal(
                "generation-select-failed",
                "the provider could not select local requests from this seed",
                provider=self.provider_name, seed=seed.fingerprint,
                error=f"{type(exc).__name__}: {exc}")
        self.check_scoring_stop()
        seed_refusal = ports.seed_refusal
        scoped_refusal = (seed_refusal(seed) if isinstance(selected, Refusal)
                          and seed_refusal is not None else None)
        requests = _selection(
            seed, selected, self.targetable, self.telemetry, self.catalogue_digest,
            scoped_refusal=scoped_refusal)
        planned = type(requests) is QueryPlan
        if planned and self.coverage is not None:
            # Retire covered ordinary questions before worklist interleaving.
            # Otherwise each already-empty family grants another context turn
            # before any live state-setup query can run.
            explorations = frozenset(requests.explorations)
            remaining = self.coverage.remaining
            live = []
            for item in requests.templates:
                targets = (item.targets if self.explores_contexts and item.rank in explorations else
                           tuple(goal for goal in item.targets if goal in remaining))
                if targets:
                    live.append(item if targets == item.targets else item.with_targets(targets))
                else:
                    self.telemetry["requests_covered"] += 1
            requests = requests.retaining(live)
        equivalent = ({key: group for group in (
            requests.equivalents if planned else selected.equivalent_requests) for key in group}
                      if requests else {})
        # Proofs belong to this one Select, not other seeds or query contexts.
        local_unsat = {}

        pending_requests = QueryWork(
            requests, selected.query_chains if requests and not planned else (),
            continuation_targets=preferred,
            context_visits=self.context_visits,
            priority=(lambda targets: (not preferred.intersection(targets, self.coverage.remaining),
                                       self.family_priority(targets))) if self.coverage is not None else None)
        started_families = set()
        construction_request, construction_seconds = None, 0.0
        completed_question = False
        request_index = None
        while pending_requests:
            if completed_question:
                if not self.can_continue():
                    if self.coverage is None or self.coverage.remaining:
                        self.telemetry["requests_deferred"] += len(pending_requests)
                    break
                # Routine fairness keeps related prefixes on this worker.
                # Confirmed new coverage below can instead start a successor
                # after a completed SAT, without dropping the parent's tail.
                if not pending_requests.continues(request_index):
                    yield QueryComplete()
                completed_question = False
            request_index, request = pending_requests.popleft()
            exploration = planned and request.rank in requests.explorations
            group = equivalent.get(request.rank if planned else request.identity)
            if self.coverage is not None and not (self.explores_contexts and exploration):
                request = self.coverage.restrict(request)
                if request is None:
                    self.telemetry["requests_covered"] += 1
                    continue
            query_targets = ports.query_targets
            if query_targets is not None:
                targets = tuple(query_targets(request.targets, seed=seed))
                if targets != request.targets:
                    check_search_work()
                    narrowed = request.with_targets(targets) if targets else None
                    self.telemetry["request_targets_constant_false"] += len(request.targets) - len(targets)
                    if narrowed is None:
                        self.telemetry["requests_constant_false"] += 1
                        continue
                    request = narrowed
            proof = local_unsat.get(group)
            if proof is not None and set(request.targets) <= proof[0]:
                check_search_work()
                self.telemetry["requests_equivalent"] += 1
                proof[1]["requests"].append(request.identity)
                continue
            if ports.direction_refusal() is not None:
                self.telemetry["requests_deferred"] += 1 + len(pending_requests)
                break
            seed_refusal = ports.seed_refusal
            if seed_refusal is not None:
                unavailable = seed_refusal(seed)
                if unavailable is not None:
                    _record_refusal(self.telemetry, unavailable, stop=False)
                    self.telemetry["requests_deferred"] += 1 + len(pending_requests)
                    break
            unavailable = ports.request_failure(seed, request)
            if unavailable is not None:
                self.telemetry["requests_unavailable"] += 1
                if unavailable.kind not in self.telemetry["refusals"]:
                    _record_refusal(self.telemetry, unavailable, stop=False)
                continue
            check_execution()
            if self.probe_budget is not None and self.telemetry["probes"] >= self.probe_budget:
                self.telemetry["requests_deferred"] += 1 + len(pending_requests)
                break
            continuing = (construction_request is not None
                          and request.is_restriction_of(construction_request))
            if self.coverage is not None and self.workers > 1 and not continuing and not exploration:
                # Independent state-context queries already give parallel
                # exploration. Splitting their frontier alternatives by static
                # goal restarts the same entry repeatedly before its joint
                # setup question; unlike ordinary queries these return no
                # per-goal verdict that needs partitioning.
                request, remainder = partition_targets(request, self.in_flight_targets, self.workers)
                if remainder is not None:
                    pending_requests.appendleft((request_index, remainder))
                    self.telemetry["parallel_target_splits"] += 1
            lease = active_search()
            if lease is not None:
                lease.start_probe()
            family = pending_requests.family[request_index]
            if self.coverage is not None and family not in started_families:
                started_families.add(family)
                # Reserve the complete family for scheduling even when this
                # Probe shares a target subset with other lanes. All remaining
                # targets/questions are still queued; this grants no evidence.
                row = requests.templates[family] if planned else requests[family]
                for goal in row.targets:
                    self.family_visits[goal] = self.family_visits.get(goal, 0) + 1
                self.telemetry["goal_family_starts"] += 1
            self.telemetry["probes"] += 1
            if (construction_request is None
                    or not request.is_restriction_of(construction_request)):
                construction_seconds = 0.0
            construction_request = request
            probe_seconds = 0.0
            self.in_flight_targets[id(lane)] = request.targets
            try:
                def probe(request=request, pending=pending_requests.shares(request_index)):
                    nonlocal probe_seconds
                    with search_query_scope(pending, construction_seconds=construction_seconds):
                        started = time.monotonic()
                        try:
                            return lane.probe(seed, request)
                        finally:
                            probe_seconds = time.monotonic() - started

                result = yield ("probe", probe)
                if lease is not None:
                    lease.probes_completed += 1
            except Refused as exc:
                if lease is not None:
                    lease.probes_completed += 1
                self.telemetry["probe_refusals"] += 1
                scoped = ports.request_failure(seed, request)
                _record_refusal(self.telemetry, exc.refusal, stop=scoped != exc.refusal)
                continue
            except Exception as exc:  # noqa: BLE001 - provider containment boundary
                if lease is not None:
                    lease.probes_completed += 1
                refusal = Refusal(
                    "generation-probe-failed",
                    "the provider failed while answering a selected local request",
                    provider=self.provider_name, request=request.identity,
                    error=f"{type(exc).__name__}: {exc}")
                self.telemetry["probe_refusals"] += 1
                _record_refusal(self.telemetry, refusal)
                continue
            finally:
                # The coordinator alone owns these hints. Neither a pending
                # SAT answer nor a cancelled worker is execution evidence.
                self.in_flight_targets.pop(id(lane), None)
            if type(result) is not ProbeResult:
                refusal = Refusal(
                    "generation-probe-result-not-typed",
                    "Probe must return the shared local ProbeResult algebra",
                    request=request.identity, got=type(result).__name__)
                self.telemetry["probe_refusals"] += 1
                _record_refusal(self.telemetry, refusal)
                continue
            if exploration and (result.goal is not None
                    or result.outcome in (Outcome.SAT, Outcome.LOCAL_UNSAT)
                    or (result.outcome is Outcome.PROPOSED and result.scope is not Scope.STATE_PREPARATION)):
                self.telemetry["probe_refusals"] += 1
                _record_refusal(self.telemetry, Refusal(
                    "generation-context-verdict-invalid",
                    "context exploration proposes inputs, never public goal verdicts",
                    request=request.identity, result=result.as_dict()))
                continue
            if result.goal is not None and str(result.goal) not in request.targets:
                refusal = Refusal(
                    "generation-probe-result-target-mismatch",
                    "a probe result names a goal outside the request it answered",
                    request=request.identity, goal=str(result.goal))
                self.telemetry["probe_refusals"] += 1
                _record_refusal(self.telemetry, refusal)
                continue

            answer = self.record_answer(seed, request, result, construction_seconds)
            if result.outcome is Outcome.CONSTRUCTING:
                # Resume this lane's exact unfinished question. Every slice
                # passes through the ordinary shared time/Probe accounting;
                # no retry of solver UNKNOWN or local UNSAT is implied.
                construction_seconds += probe_seconds
                pending_requests.appendleft((request_index, request))
                continue
            construction_request, construction_seconds = None, 0.0
            completed_question = True
            context_key = requests.contexts.get(request.rank) if planned and exploration else None
            if context_key is not None:
                self.context_visits[context_key] = self.context_visits.get(context_key, 0) + 1
                answer['context'] = context_key
            if (group is not None and result.outcome is Outcome.LOCAL_UNSAT
                    and result.scope is Scope.EXACT_LOCAL_HIT):
                reuse = {"seed": seed.fingerprint, "proof_request": request.identity,
                         "requests": []}
                self.telemetry["local_unsat_reuse"].append(reuse)
                local_unsat[group] = (frozenset(request.targets), reuse)
            if (result.outcome is Outcome.UNSUPPORTED and lease is not None
                    and lease.owner.policy.debug):
                _record_refusal(self.telemetry, Refusal(
                    "generation-probe-unsupported", "debug mode stopped at an unsupported local query",
                    answer=result.as_dict(), request=request.identity))
            if result.outcome not in (Outcome.SAT, Outcome.PROPOSED):
                continue
            admitted = self.admit_candidate(seed, request, result, answer)
            if admitted is None:
                continue
            candidate, body = admitted
            if self.coverage is not None:
                # A SAT model is not feedback. Replay before choosing another
                # request, so newly hit goals leave even an already-selected batch.
                previous_remaining = self.coverage.remaining
                check_execution()
                if self.workers == 1:
                    self.preselected[body] = self.observe_selection(candidate)
                else:
                    # Only this coordinator may replay and publish new evidence.
                    # The lane will Select if/when this new seed is dispatched.
                    with search_phase("seed_replay"):
                        feedback = self.observe_many((candidate,)) if self.observe_many is not None else None
                    if feedback is None:
                        self.observe_selection(candidate)
                    else:
                        if len(feedback) != 1:
                            raise ValueError("source observation must answer the new seed")
                        row, = feedback
                        if isinstance(row, Refusal):
                            self.check_scoring_stop()
                            _record_refusal(self.telemetry, row)
                        self.observe_feedback(candidate, row)
                    self.check_scoring_stop()
                preparation = (result.outcome is Outcome.PROPOSED
                               and candidate.fingerprint in self.coverage.replays)
                if ((self.coverage.remaining or self.explores_contexts)
                        and (previous_remaining - self.coverage.remaining or preparation)):
                    # New native coverage can preempt a completed query's chain.
                    # A preparation without new hits fairly rotates its child
                    # and parent at this completed query: it is neither coverage
                    # nor proof of state novelty, but need not wait for all the
                    # parent's larger questions before being explored.
                    # CONSTRUCTING never reaches this checkpoint; retained
                    # requests/proofs stay on the same worker for later resume.
                    # A new state continues the family that produced it before
                    # global visit balancing sends it to unrelated questions.
                    # This is ordering only: every old question/chain remains,
                    # and ordinary completed-chain handoffs still apply.
                    self.successor_targets[candidate.fingerprint] = (
                        frozenset(request.targets) & frozenset(self.selection_targets()))
                    # ExploreSuccessor already yields this completed question.
                    # On resume execute the next request; yielding QueryComplete
                    # again would let preparation descendants repeatedly pass
                    # the parent's waiting context query without any work.
                    completed_question = False
                    yield ExploreSuccessor(candidate,
                                           progress=bool(previous_remaining - self.coverage.remaining))

    def result(self):
        """Snapshot retained work on normal completion or a stopped search."""
        ordered = tuple(self.generated[body] for body in sorted(self.generated))
        return GenerationResult(
            self.provider_name, self.baseline, ordered, self.provenance, self.telemetry,
            contract=self.contract.digest, generation_spec=self.generation_spec,
            baseline_policy=self.baseline_policy)
