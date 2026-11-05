"""A target the relation cannot pose a query about, which was counted as a failed probe.

D5's remaining half was written as "a design choice with a cost either way, not
something a measurement settles". Half of that is right and half is not.

**The harness is a fact in the repo, not a preference.** The replay drives both kinds of
step the relation excludes: a framed stimulus asserts `rst` across three rising edges,
and every slot without an offer holds `in_valid` low. (The retired `replay_tb` template
also inserted a random 0 to 20 idle cycles; a stimulus now records its idle slots.) So
the relation represents a strictly narrower system than the one replayed and the one
verilator measures coverage on. Whether to *widen* it is the choice; whether the gap
exists is not.

**And the gap's size is a measurement.** `RelationDomain.admits` already answered "is
this site outside what the relation is about" and **nothing in production asked it** --
its only callers were tests. So a site behind reset was probed like any other, the
solver returned `unsat` correctly to a question that could not be posed, and the search
counted it as `local-unsat`, which reads as "no input reaches this site".

Over 824 designs and the 148622 goals `goal_catalogue_of` declares: **825 outside,
0.555%, every one behind reset, spread over 817 of 824 designs.** Rare per goal and all
but universal per design. `in_valid` costs zero declared goals, because a handshake is
usually combinational rather than a `switch`, and that is a fact about the catalogue and
not about the relation -- an idle step can still be the only route to some state, which
this does not bound.
"""
from behave.sim.rtl.catalogue import Site


def site(signal, outcome, activation=(), siblings=("1'1", "default")):
    return Site("m", "p", signal, "f.sv:1.1-1.2", outcome, 1 + len(activation),
                activation, siblings)


# ── the split ──

# ── and the result it produces is not an answer ──

# ── the harness really does drive what the relation excludes ──

def test_the_replay_harness_drives_both_excluded_steps():
    """Driven through the one Driver whose controls every replay puts on the pins,
    rather than asserted about it, because this is the fact that turns D5's remaining
    half from a preference into a gap."""
    from behave.sim.core.driver import Driver

    from .test_api_path import Doubling, contract, schedule

    c = contract(answers=1)
    stimulus = schedule([1, 2], c=c, drain=2)
    driver, device, driven = Driver(c, stimulus), Doubling(), []
    for index, slot in enumerate(stimulus.slots):
        controls, presented = driver.controls(index), driver.presentations(index)
        if slot.reset:
            device.reset()
        driver.observe(index, presented, device.settle(driver.present(index), controls))
        device.edge()
        driven.append((slot, controls))
    assert sum(controls["rst"] for _slot, controls in driven) >= 3, \
        "reset is held across several edges, so reset steps are replayed"
    idle = [controls for slot, controls in driven if not slot.reset and not slot.offers]
    assert idle and all(controls["in_valid"] == 0 for controls in idle), "idle steps are replayed"
