"""The catalogue and trace domain have one canonical evidence identity."""

from __future__ import annotations

import math
from collections.abc import Mapping

import pytest

from behave.sim.core.catalogue import (
    COVERAGE,
    SAFETY,
    CatalogueManifest,
    DomainManifest,
    Goal,
    GoalId,
    GoalSchema,
    OccurrenceKey,
    catalogue_of,
)
from behave.sim.core.domain import TraceDomain
from behave.sim.core.instance import BoundedInstance
from behave.sim.core.outcome import ConcreteEvaluation
from behave.sim.core.refusal import Refused


def _goals():
    return (
        Goal(
            "branch.false", COVERAGE, family="decision", provider="behavior",
            event="false", policy="source-site/1",
            provenance={"source": "m.py", "line": 7}),
        Goal(
            "protocol.stable", SAFETY, family="protocol", provider="monitor",
            event="stable", policy="monitor/1", evidence="trace"),
    )


def _occurrences():
    return (
        (OccurrenceKey("behavior", ("process", 3, 0), "false"), "branch.false"),
        (OccurrenceKey("behavior", ("process", 9, 0), "false"), "branch.false"),
        (OccurrenceKey("monitor", ("sink", "out"), "stable"), "protocol.stable"),
    )


def test_owned_manifest_encoding_matches_the_canonical_carrier():
    from behave.sim.core.record_value import canonical_json

    catalogue = CatalogueManifest(_goals(), occurrences=_occurrences())
    assert catalogue._canonical == canonical_json(catalogue.as_dict())


def test_owned_manifest_still_rejects_an_unencodable_goal_identifier():
    with pytest.raises(TypeError, match="finite strict JSON"):
        CatalogueManifest((Goal("\ud800", COVERAGE),))


def test_occurrence_identity_is_serialized_once_not_for_every_lookup(monkeypatch):
    from behave.sim.core import catalogue

    keys = _occurrences()
    equal = tuple(OccurrenceKey(key.provider, key.path, key.role) for key, _ in keys)
    original, calls = catalogue._canonical, []

    def counted(value):
        calls.append(None)
        return original(value)

    monkeypatch.setattr(catalogue, '_canonical', counted)
    for _ in range(32):
        table = dict(keys)
        for key, other in zip(table, equal):
            assert key == other and table[other] == table[key]
            assert key.canonical == other.canonical
    assert not calls, len(calls)


def test_occurrence_cached_image_keeps_exact_bytes_and_detached_views():
    import json
    import pickle

    path = ['process', -3, 0, 'quoted"\\\n', '\u03c1']
    key = OccurrenceKey('behavior', path, 'false')
    expected = json.dumps({'provider': 'behavior', 'path': path, 'role': 'false'},
                          sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    path.append('caller mutation')
    image = key.as_dict()
    image['path'].clear()
    image['provider'] = 'changed'
    assert key.canonical == expected
    other = pickle.loads(pickle.dumps(key))
    assert other == key and hash(other) == hash(key) and other.canonical == expected
    with pytest.raises(AttributeError):
        key._canonical = 'changed'


def test_occurrence_identity_distinguishes_each_component():
    key = OccurrenceKey('behavior', ('p', 1), 'true')
    others = (OccurrenceKey('rtl', ('p', 1), 'true'),
              OccurrenceKey('behavior', ('p', '1'), 'true'),
              OccurrenceKey('behavior', ('p', 1), 'false'))
    assert all(other != key and other.canonical != key.canonical for other in others)
    assert len({key, *others}) == 4


def test_manifest_order_round_trip_and_digest_are_canonical():
    first = CatalogueManifest(_goals(), occurrences=_occurrences())
    second = CatalogueManifest(
        tuple(reversed(_goals())), occurrences=tuple(reversed(_occurrences())))

    assert first == second
    assert first.canonical == second.canonical
    assert first.digest() == second.digest()
    assert len(first.digest()) == 64
    assert CatalogueManifest.from_dict(first.as_dict()) == first
    assert list(first) == ["branch.false", "protocol.stable"]
    assert first[GoalId("branch.false")].family == "decision"
    with pytest.raises(AttributeError):
        first._entries = ()


def test_goal_schema_is_typed_and_has_a_strict_json_identity():
    schema = GoalSchema(
        COVERAGE, family="decision", provider="behavior",
        provenance={"nested": {"values": [1, True]}})
    assert schema == GoalSchema(
        COVERAGE, provider="behavior", family="decision",
        provenance={"nested": {"values": [1, True]}})
    assert len(schema.digest()) == 64

    with pytest.raises(TypeError, match="goal ID"):
        GoalId(1)
    with pytest.raises(TypeError, match="goal ID"):
        GoalId(type("AmbiguousId", (str,), {})("branch.false"))
    with pytest.raises(TypeError, match="strict JSON"):
        GoalSchema(COVERAGE, provenance={"bad": "\ud800"})

    class HostileClass:
        def __eq__(self, _other):
            raise AssertionError("candidate equality code ran")

    with pytest.raises(ValueError, match="goal class"):
        GoalSchema(HostileClass())
    with pytest.raises(ValueError, match="goal class"):
        GoalSchema("maybe")
    with pytest.raises(TypeError, match="canonical JSON"):
        GoalSchema(COVERAGE, provenance={"bad": object()})
    with pytest.raises((TypeError, ValueError), match="non-finite"):
        GoalSchema(COVERAGE, provenance={"bad": math.inf})
    with pytest.raises(TypeError, match="non-string key"):
        GoalSchema(COVERAGE, provenance={1: "ambiguous"})

    class HostileMapping(Mapping):
        def __getitem__(self, _key):
            raise AssertionError("candidate mapping code ran")

        def __iter__(self):
            raise AssertionError("candidate mapping code ran")

        def __len__(self):
            raise AssertionError("candidate mapping code ran")

    with pytest.raises(TypeError, match="goal provenance"):
        GoalSchema(COVERAGE, provenance=HostileMapping())
    with pytest.raises(TypeError, match="exact mapping"):
        CatalogueManifest(HostileMapping())


def test_duplicate_conflicting_unknown_and_missing_declarations_fail_closed():
    goal = _goals()[0]
    with pytest.raises(Refused) as duplicate:
        CatalogueManifest((goal, goal))
    assert duplicate.value.refusal.kind == "catalogue-duplicate-goal"

    changed = Goal(
        goal.id, SAFETY, family=goal.family, provider=goal.provider,
        event=goal.event, policy=goal.policy, provenance=dict(goal.provenance))
    with pytest.raises(Refused) as conflict:
        CatalogueManifest((goal, changed))
    assert conflict.value.refusal.kind == "catalogue-conflicting-goal"

    declared = CatalogueManifest(_goals())
    with pytest.raises(Refused) as missing:
        declared.reconcile((_goals()[0],), "bounded")
    assert missing.value.refusal.kind == "catalogue-bounded-missing-goal"

    unknown = Goal("unknown", COVERAGE)
    with pytest.raises(Refused) as outside:
        declared.reconcile((unknown,), "guidance", exhaustive=False)
    assert outside.value.refusal.kind == "catalogue-guidance-unknown-goal"

    stale = Goal(
        goal.id, goal.cls, family="different", provider=goal.provider,
        event=goal.event, policy=goal.policy, provenance=dict(goal.provenance))
    with pytest.raises(Refused) as stale_error:
        declared.reconcile((stale,), "guidance", exhaustive=False)
    assert stale_error.value.refusal.kind == "catalogue-guidance-stale-goal"

    assert declared.reconcile((_goals()[0],), "guidance", exhaustive=False) == (
        "branch.false",)


def test_occurrence_reconciliation_does_not_merge_lowered_copies():
    manifest = CatalogueManifest(_goals(), occurrences=_occurrences())
    one_copy_missing = _occurrences()[1:]
    with pytest.raises(Refused) as missing:
        manifest.reconcile_occurrences(one_copy_missing, "bounded")
    assert missing.value.refusal.kind == "catalogue-bounded-missing-occurrence"

    wrong_goal = ((_occurrences()[0][0], "protocol.stable"),) + _occurrences()[1:]
    with pytest.raises(Refused) as stale:
        manifest.reconcile_occurrences(wrong_goal, "bounded")
    assert stale.value.refusal.kind == "catalogue-bounded-stale-occurrence"

    extra = OccurrenceKey("behavior", ("process", 99), "false")
    with pytest.raises(Refused) as unknown:
        manifest.reconcile_occurrences(
            _occurrences() + ((extra, "branch.false"),), "concrete")
    assert unknown.value.refusal.kind == "catalogue-concrete-unknown-occurrence"


def test_domain_manifest_binds_the_full_domain_and_catalogue_images():
    domain = TraceDomain(
        3, contract={"a": [0, 1]}, frontend="behavior/15",
        policy="source-site/1", artifact="artifact", task_contract="task")
    catalogue = CatalogueManifest(_goals(), occurrences=_occurrences())
    manifest = DomainManifest(domain, catalogue)

    assert len(manifest.digest()) == 64
    assert manifest.as_dict()["domain_digest"] == domain.digest()
    assert manifest.as_dict()["catalogue_digest"] == catalogue.digest()
    assert DomainManifest.from_dict(manifest.as_dict()) == manifest

    forged = manifest.as_dict()
    forged["catalogue_digest"] = "0" * 64
    with pytest.raises(ValueError, match="catalogue digest"):
        DomainManifest.from_dict(forged)


def test_domain_manifest_version_and_nested_images_are_strictly_authenticated():
    domain = TraceDomain(
        3, contract={"a": [0, 1]}, frontend="behavior/15",
        policy="source-site/1", artifact="artifact", task_contract="task")
    manifest = DomainManifest(
        domain, CatalogueManifest(_goals(), occurrences=_occurrences()))
    image = manifest.as_dict()

    assert image["version"] == "domain-manifest/1"
    assert DomainManifest.from_dict(image).canonical == manifest.canonical

    changed_domain = manifest.as_dict()
    changed_domain["domain"]["bound"] = 4
    with pytest.raises(ValueError, match="domain digest"):
        DomainManifest.from_dict(changed_domain)

    changed_catalogue = manifest.as_dict()
    changed_catalogue["catalogue"]["goals"][0]["family"] = "tampered"
    with pytest.raises(ValueError, match="catalogue digest"):
        DomainManifest.from_dict(changed_catalogue)

    wrong_version = manifest.as_dict()
    wrong_version["version"] = "domain-manifest/2"
    with pytest.raises(ValueError, match="unsupported version"):
        DomainManifest.from_dict(wrong_version)

    unknown = manifest.as_dict()
    unknown["unsigned"] = True
    with pytest.raises(ValueError, match="unknown or missing fields"):
        DomainManifest.from_dict(unknown)


def test_bounded_instance_keeps_diagnostic_domains_unbound_until_catalogue_binding():
    class Provider:
        catalogue = CatalogueManifest(_goals())

        def goals(self):
            return {goal_id: self.catalogue[goal_id] for goal_id in self.catalogue}

    domain = TraceDomain(1, frontend="test", artifact="artifact")
    catalogue = CatalogueManifest(_goals())
    provider = Provider()
    diagnostic = BoundedInstance(provider, domain)

    assert diagnostic.domain_manifest is None
    bound = diagnostic.bind_catalogue(catalogue)
    assert diagnostic.domain_manifest is None
    assert bound.domain_manifest == DomainManifest(domain, catalogue)
    assert bound.bind_catalogue(catalogue) is bound

    provider.catalogue = CatalogueManifest((_goals()[0],))
    assert set(diagnostic.goals()) == {"branch.false"}
    assert set(bound.goals()) == {"branch.false", "protocol.stable"}

    with pytest.raises(ValueError, match="another domain manifest"):
        bound.bind_catalogue(CatalogueManifest((_goals()[0],)))


def test_core_evaluation_retains_the_manifest_not_a_legacy_dict():
    class Contract:
        digest = "contract"

        @staticmethod
        def inputs():
            return ()

    class Realization:
        identity = "artifact"

        @staticmethod
        def as_dict():
            return {"identity": "artifact"}

    class Task:
        contract = Contract()
        realization = Realization()
        reference = "reference"

    manifest = CatalogueManifest(_goals(), occurrences=_occurrences())
    evaluation = ConcreteEvaluation(Task(), catalogue=manifest)

    assert evaluation.catalogue is manifest
    assert evaluation.as_dict()["catalogue_manifest"] == manifest.as_dict()
    assert evaluation.as_dict()["catalogue_digest"] == manifest.digest()
    restored = CatalogueManifest.from_dict(
        evaluation.as_dict()["catalogue_manifest"])
    assert restored == manifest
    assert restored.occurrences == manifest.occurrences
    domain = TraceDomain(1, frontend="test", artifact="artifact")
    assert DomainManifest.from_dict(
        DomainManifest(domain, restored).as_dict()).catalogue == manifest
    with pytest.raises(Refused, match="catalogue-legacy-mapping"):
        ConcreteEvaluation(Task(), catalogue={"g": COVERAGE})
    with pytest.raises(Refused, match="catalogue-legacy-mapping"):
        catalogue_of({"g": COVERAGE}, allow_legacy=False)
