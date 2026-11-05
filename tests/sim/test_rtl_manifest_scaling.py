"""Complete RTL declarations retain ordered matching without quadratic records."""

import hashlib
import json

import pytest

from behave.sim.core.catalogue import CatalogueManifest
from behave.sim.core.refusal import Refused
from behave.sim.rtl.case_match import match_identity, match_schema
from behave.sim.rtl.catalogue import (
    Site,
    _goal_catalogue_from_sites,
    goal_id_of,
    occurrence_key_of,
    sites_of,
    sites_to_json,
)
from tests.support.rtl_site_oracle import occurrence_holds


def _switch(arms):
    rows = ["module \\top", "  process $p", "    switch \\a"]
    for value in range(arms):
        rows.extend((f"      case 16'{value:016b}", "        assign \\y 1'1"))
    return "\n".join((*rows, "    end", "    sync always",
                      "      update \\y \\y", "  end", "end"))


def test_complete_manifest_size_scales_with_occurrences_not_arm_pairs():
    sizes = []
    for arms in (64, 128, 256):
        sites = sites_of(_switch(arms))
        goals, declared = _goal_catalogue_from_sites(sites)
        assert len(declared) == 2 * arms + 1
        assert len(goals) == arms + 2  # all choices, no-match and the shared action
        manifest = CatalogueManifest(tuple(goals.values()), occurrences=tuple(
            (occurrence_key_of(site), goal_id_of(site)) for site in declared.values()))
        assert CatalogueManifest.from_dict(manifest.as_dict()) == manifest
        sizes.append(len(manifest.canonical.encode()))
    assert sizes[1] < 2.3 * sizes[0], sizes
    assert sizes[2] < 2.3 * sizes[1], sizes


def _site(outcome, siblings):
    return Site("top", "$p", "\\a", "design.sv:1", outcome, 1, (),
                siblings=siblings, switch_id="one-switch")


def test_one_switch_cannot_disagree_on_arm_priority():
    labels = ("2'0-", "2'-1", "default")
    sites = [_site(label, labels) for label in labels]
    sites[1] = _site(labels[1], (labels[1], labels[0], labels[2]))
    assert occurrence_holds(sites[0], {"a": 1}) is True
    assert occurrence_holds(sites[1], {"a": 1}) is True  # incompatible priority, not two valid hits
    with pytest.raises(Refused) as got:
        _goal_catalogue_from_sites(sites)
    assert got.value.refusal.kind == "rtl-structural-switch-inconsistent"


def test_one_switch_cannot_hide_duplicate_arm_labels():
    labels = ("2'00", "2'01", "default")
    duplicate = (labels[0], labels[1], labels[1], labels[2])
    with pytest.raises(Refused) as got:
        _goal_catalogue_from_sites([_site(label, duplicate) for label in labels])
    assert got.value.refusal.kind == "rtl-structural-switch-inconsistent"


def test_site_writer_rejects_duplicate_occurrences_explicitly():
    first = _site("1'1", ("1'1", "default"))
    second = Site.from_json(first.as_json())
    with pytest.raises(ValueError, match="duplicate"):
        sites_to_json([first, second])


def test_complete_manifest_preserves_all_hit_and_occurrence_inputs():
    sites = sites_of(_switch(4))
    goals, declared = _goal_catalogue_from_sites(sites)
    choices = [site for site in declared.values() if site.kind == "case-arm"]
    for value in range(6):
        hits = [site for site in choices if occurrence_holds(site, {"a": value})]
        assert len(hits) == 1
        assert hits[0].outcome == (f"16'{value:016b}" if value < 4 else "no-match")
    for site in choices:
        assert site.goal in goals
        other = Site.from_json(json.loads(json.dumps(site.as_json())))
        assert other.target == site.target and other.goal == site.goal
        assert occurrence_key_of(other) == occurrence_key_of(site)


def test_match_identity_binds_the_complete_ordered_value_not_an_object_alias():
    labels = ("2'0-", "2'-1", "default")
    activation = (("1'1", "default"),)
    # Independent spelling of the previous occurrence suffix's exact byte image.
    payload = json.dumps([list(labels), [list(row) for row in activation]],
                         ensure_ascii=True, separators=(",", ":")).encode()
    short, complete = match_identity(labels, activation)
    assert short == hashlib.blake2s(payload, digest_size=6).hexdigest()
    assert complete == hashlib.sha256(payload).hexdigest()
    assert match_schema(labels[1], labels) == ["ordered-switch/1", {
        "match_sha256": match_identity(labels, ())[1], "arm": 1}]
    assert match_identity(labels[::-1], activation)[1] != complete
    assert match_identity(labels + (labels[0],), activation)[1] != complete
    assert match_identity(labels, (activation[0][::-1],))[1] != complete

    frame = {"labels": list(activation[0])}
    site = Site("m", "p", "\\a", "f:1", labels[0], 2, (("\\b", "1'1"),),
                siblings=labels, activation_frames=(frame,))
    before = occurrence_key_of(site)
    frame["labels"].reverse()
    assert occurrence_key_of(site) == before
    changed = Site.from_json(site.as_json() | {"activation_frames": [frame]})
    assert occurrence_key_of(changed) != before
