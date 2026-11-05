"""What the task declares, as against what either artifact claims.

The schema is the task's. Both artifacts owe it: the behavior model is driven
through it and the design has to realise it, and neither of them defines it. That
is stated in `core/schema.py` and was not true of the code, because there was
nowhere to read the task's declaration from, so the behavior side read the widths
out of the candidate's header. A candidate declaring a port narrower than the task
then narrowed the contract, and narrowing the contract can only enlarge the set of
goals reported unreachable: every verdict was relative to an input space the thing
under test had chosen.

There is somewhere to read it from. The specification carries the interface, in a
fenced block under a heading, with the parameter defaults it is to be built with.
That is the declaration, and elaborating it gives the contract without asking the
candidate anything.

So the behavior side needs nothing from the RTL at all, which is the shape the
core boundary describes: this module reads the task, `rtl/elaborate.py` resolves
SystemVerilog whoever wrote it, and proving that a candidate's ports realise the
schema is the RTL frontend's job and is not this.
"""

from __future__ import annotations

import re

from .core.schema import InterfaceError

__all__ = ["interface_module_source", "interface_source", "schema_of_spec"]

#: A fenced SystemVerilog block under a heading that says it is the interface.
#: Anchored on the heading rather than on being the first block, since a
#: specification has other code in it and the interface is the one this wants.
_FENCED = re.compile(
    r"^#{1,6}\s*[^\n]*\bRTL\s+interface\b[^\n]*\n"
    r"(?:.*?)"
    r"^```(?:systemverilog|verilog|sv)?\s*\n(?P<body>.*?)^```",
    re.DOTALL | re.MULTILINE | re.IGNORECASE)


def interface_source(spec):
    """The SystemVerilog the specification declares as the interface.

    Refused rather than guessed at when it is not there. A task with no declared
    interface has no schema, and falling back to the candidate's header is the
    thing this module exists to stop.
    """
    found = _FENCED.search(spec)
    if found is None:
        raise InterfaceError(
            "the specification declares no RTL interface: expected a fenced "
            "block under a heading naming it")
    body = found.group("body")
    if "module" not in body:
        raise InterfaceError("the specification's interface block declares no "
                             "module")
    return body


def interface_module_source(spec):
    """Close a header-only task declaration without adding implementation logic."""
    declaration = interface_source(spec)
    if not re.search(r"\bendmodule\b", declaration):
        declaration += "\nendmodule\n"
    return declaration


def schema_of_spec(spec, name="the task"):
    """The contract the task declares, elaborated.

    Elaborated, so a width written over the parameters the declaration carries is
    a number here rather than a refusal. That is not a convenience: 91 designs in
    the population declare a port whose width is not a literal, and reading the
    task instead of the candidate would have replaced one unread contract with
    another.
    """
    from .rtl.elaborate import schema_of_module

    declaration = interface_module_source(spec)
    from .core.schema import Authority

    return schema_of_module(declaration, name,
                            source_note=f"declared by {name}",
                            authority=Authority.TASK)
