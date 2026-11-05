"""Shared-label storage must preserve the full ordered RTL match semantics."""

import copy
import json

import pytest

from behave.sim.rtl._rtlil_sites import pack_site_rows, rtlil_site_rows, unpack_site_rows
from behave.sim.rtl.catalogue import Site, sites_from_json, sites_of, sites_to_json
from tests.support.rtl_site_oracle import occurrence_holds


def _wide_switch(arms):
    lines = ["module \\top", "  process $p", "    switch \\a"]
    for i in range(arms):
        lines += [f"      case 16'{i:016b}", "        assign \\y 1'1"]
    lines += ["    end", "    sync always", "      update \\y \\y", "  end", "end"]
    return "\n".join(lines)


def test_shared_labels_round_trip_occurrences_ids_and_hit_semantics():
    rows = rtlil_site_rows(_wide_switch(16))
    document = pack_site_rows(rows)
    assert unpack_site_rows(json.loads(json.dumps(document))) == rows
    sites = sites_of(_wide_switch(16))
    image = sites_to_json(sites)
    restored = sites_from_json(json.loads(json.dumps(image)))
    assert sites_to_json(restored) == image
    for site in sites:
        other = restored[site.target]
        assert other.as_json() == site.as_json()
        assert other.goal == site.goal
        assert other.match_id == site.match_id
        for value in range(18):
            assert occurrence_holds(other, {"a": value, "\\a": value}) == \
                occurrence_holds(site, {"a": value, "\\a": value})
    arms = [site for site in restored.values() if site.kind == "case-arm"]
    assert all(site.siblings is arms[0].siblings for site in arms)


def test_label_order_and_multiplicity_are_not_normalized_away():
    rows = [Site("m", "p", "\\a", "f:1", "1'1", 1, [],
                 siblings=("1'1", "1'x", "1'1", "default")).as_json()]
    assert unpack_site_rows(pack_site_rows(rows)) == rows
    assert pack_site_rows(rows)["labels"] == [["1'1", "1'x", "1'1", "default"]]


def test_decoded_shared_labels_cannot_be_mutated_through_the_wire_document():
    image = sites_to_json(sites_of(_wide_switch(4)))
    restored = sites_from_json(image)
    before = {key: site.as_json() for key, site in restored.items()}
    for labels in image["labels"]:
        labels[:] = ["forged"]
    assert {key: site.as_json() for key, site in restored.items()} == before


def test_wide_switch_storage_is_linear_not_a_label_copy_per_arm():
    sizes = []
    for arms in (128, 256, 512):
        rows = rtlil_site_rows(_wide_switch(arms))
        packed = pack_site_rows(rows)
        assert unpack_site_rows(packed) == rows
        sizes.append(len(json.dumps(packed)))
        # All written choices and the synthetic no-match remain present.
        assert len([row for row in rows if row["kind"] == "case-arm"]) == arms + 1
        assert len(packed["labels"]) == 2  # empty location labels + the full switch
    assert sizes[1] < 2.1 * sizes[0]
    assert sizes[2] < 2.1 * sizes[1]


@pytest.mark.parametrize("bad", [True, -1, 1000, None, "0", 0.0])
@pytest.mark.parametrize("field", ["siblings", "activation_frames"])
def test_invalid_label_references_are_refused(bad, field):
    packed = pack_site_rows(rtlil_site_rows(_wide_switch(2)))
    packed["sites"][0][field] = bad if field == "siblings" else [bad]
    with pytest.raises(ValueError, match="label reference"):
        unpack_site_rows(packed)


def test_circuit_diagnostic_rejects_forged_shared_label_images():
    image = sites_to_json(sites_of(_wide_switch(2)))
    forged = copy.deepcopy(image)
    forged["targets"][0] = "forged"
    with pytest.raises(ValueError, match="does not match"):
        sites_from_json(forged)
    broken = copy.deepcopy(image)
    broken["labels"][0] = [False]
    with pytest.raises(ValueError, match="label sets"):
        sites_from_json(broken)
    assert sites_from_json(None) is None
    assert sites_to_json({}) == {}
