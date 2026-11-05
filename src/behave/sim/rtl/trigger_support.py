"""The trigger universe represented by the task's scheduled RTL slots.

A declared edge can be inactive in a particular slot. An undeclared edge (or
an unproved hierarchical alias) is instead outside this observation profile;
absence from the slot's event set cannot prove that its source command missed.
"""

from .sigspec import rtlil_name


def scheduled_triggers(clocks, *, combinational=True):
    allowed = {("always", "")} if combinational else set()
    for clock in clocks:
        allowed.add(("posedge" if clock.edge == "rising" else "negedge", clock.clock))
        allowed.update((edge, clock.reset) for edge in ("posedge", "negedge"))
    return frozenset(allowed)


def driver_triggers(contract):
    """Edges occurring inside the Driver's control/clock settle intervals."""
    allowed = set(scheduled_triggers(contract.clocks))
    if not contract.clocks:
        # With no autonomous clock, declared input levels change during drive.
        allowed.update((edge, field.port) for endpoint in contract.inputs()
                       for field in endpoint.fields for edge in ("posedge", "negedge"))
    return frozenset(allowed)


def unsupported_triggers(site, allowed):
    return tuple(sorted({
        ("posedge" if edge == "rising" else "negedge" if edge == "falling" else edge,
         rtlil_name(name))
        for edge, name in site.scheduled_triggers
    } - allowed))
