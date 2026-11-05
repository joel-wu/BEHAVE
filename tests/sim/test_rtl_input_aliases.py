"""Compiler wiring proofs shared by event activation and expression readings."""

import json
from itertools import product

import pytest

from behave.sim.rtl._rtlil_sites import scalar_input_aliases
from behave.sim.rtl.catalogue import (
    Site,
    goal_id_of,
    occurrence_key_of,
    sites_from_json,
    sites_of,
    sites_to_json,
)



def _rtlil(*, links="  connect \\child.clk \\mid\n  connect \\mid \\clk\n",
           guard="\\child.clk"):
    return f"""module \\M
  wire input 1 \\clk
  wire input 2 \\rst
  wire \\mid
  wire \\child.clk
  wire \\child.rst
  wire output 3 \\q
  attribute \\src "design.sv:1.1-1.2"
  process $p
    attribute \\src "design.sv:2.1-2.2"
    switch {guard}
      case 1'1
        assign \\q 1'1
      case
        assign \\q 1'0
    end
    sync posedge \\child.clk
      update \\q \\q
  end
{links}end
"""


def test_scalar_wiring_proof_does_not_guess_or_follow_logic():
    assert scalar_input_aliases(_rtlil())["M"] == {
        r"\child.clk": r"\clk", r"\mid": r"\clk"}
    for links in (
        "",  # Similar names alone carry no proof.
        "  connect \\child.clk 1'1\n",
        "  connect \\child.clk \\mid\n  connect \\mid \\child.clk\n",
        "  connect \\child.clk \\clk\n  connect \\child.clk \\rst\n",
        "  connect \\child.clk \\clk\n  connect \\clk 1'0\n",
        "  connect \\child.clk { \\clk }\n",
        "  connect \\child.clk \\clk [0]\n",
        "  connect \\child.clk \\clk\n  connect \\child.clk [0] 1'0\n",
        "  cell $not $n\n    connect \\Y \\child.clk\n  end\n",
        ("  connect \\child.clk \\clk\n  cell $opaque $n\n"
         "    connect \\P \\child.clk\n  end\n"),
    ):
        assert r"\child.clk" not in scalar_input_aliases(_rtlil(links=links))["M"], links
    assert not scalar_input_aliases(_rtlil().replace(
        "wire \\mid", "wire width 2 \\mid"))["M"]
    # Module-local spellings cannot borrow another module's connection.
    second = _rtlil(links="").replace("module \\M", "module \\Other")
    assert not scalar_input_aliases(_rtlil() + second)["Other"]


def test_long_alias_chains_are_order_independent_and_not_recursively_expanded():
    count = 2000
    wires = "".join(f"  wire \\c{i}\n" for i in range(count))
    links = [f"  connect \\c{i} " + (f"\\c{i + 1}" if i + 1 < count else "\\clk") + "\n"
             for i in range(count)]
    forward = "module \\M\n  wire input 1 \\clk\n" + wires
    first = scalar_input_aliases(forward + "".join(links) + "end\n")
    reverse = scalar_input_aliases(forward + "".join(reversed(links)) + "end\n")
    assert first == reverse == {"M": {f"\\c{i}": r"\clk" for i in range(count)}}


def test_every_proved_alias_agrees_with_exhaustive_wiring_valuations():
    names = (r"\clk", r"\rst", r"\a", r"\b", r"\c")
    header = "module \\M\n  wire input 1 \\clk\n  wire input 2 \\rst\n"
    header += "".join(f"  wire {name}\n" for name in names[2:])
    witnessed = 0
    # Independent Boolean equations, not another traversal of the alias graph.
    for sources in product((*names, "1'0"), repeat=3):
        links = tuple(zip(names[2:], sources))
        text = header + "".join(f"  connect {left} {right}\n" for left, right in links) + "end\n"
        aliases = scalar_input_aliases(text)["M"]
        witnessed += bool(aliases)
        for values in product((0, 1), repeat=len(names)):
            valuation = dict(zip(names, values)) | {"1'0": 0}
            if all(valuation[left] == valuation[right] for left, right in links):
                assert all(valuation[alias] == valuation[root] for alias, root in aliases.items())
    assert witnessed > 0


def test_input_aliases_survive_transport_and_bind_occurrences_not_public_names():
    sites = sites_of(_rtlil())
    assert sites and all(site.input_aliases for site in sites)
    restored = sites_from_json(json.loads(json.dumps(sites_to_json(sites))))
    assert [s.as_json() for s in restored.values()] == [
        s.as_json() for s in sorted(sites, key=lambda s: s.target)]
    site = sites[0]
    row = site.as_json()
    row["input_aliases"] = []
    unproved = Site.from_json(row)
    assert goal_id_of(site) == goal_id_of(unproved)
    assert occurrence_key_of(site) != occurrence_key_of(unproved)
    assert site.triggers == (("posedge", r"\child.clk"),)
    assert site.scheduled_triggers == (("posedge", r"\clk"),)
    assert site.active_for((("rising", "clk"),))
    assert not site.active_for((("rising", "unrelated"),))
    for aliases in ([[r"\child.clk", r"\clk"]] * 2,
                    [[r"\child.clk", r"\child.clk"]],
                    [[r"\child.clk", r"\mid"], [r"\mid", r"\clk"]],
                    [[r"\child.clk [0]", r"\clk"]]):
        with pytest.raises(ValueError):
            Site.from_json({**site.as_json(), "input_aliases": aliases})

