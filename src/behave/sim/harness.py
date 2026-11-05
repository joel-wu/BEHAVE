"""Render build files shared by native replay and submitted testbenches.

Production replay uses RTLArtifact's isolated process and the shared core Driver.
This module owns generated files and compiler options, not protocol transitions,
transaction matching, goal semantics or score decisions.
"""

from __future__ import annotations

import json
import re

from behave.sim import PACKAGE_DIR

TEMPLATE_DIR = PACKAGE_DIR / "templates"
__all__ = [
    "TEMPLATE_DIR",
    "generate_makefile",
    "render_template",
]


def render_template(name, **context):
    """Render a generated-file template from ``behave/sim/templates``."""
    return (TEMPLATE_DIR / name).read_text().format(**context)


def generate_makefile(problem_dir, top_module, tb_module="replay_tb", *,
                      expose_internal=False, coverage=True, initial_state=False,
                      native_events=False, boundary_signals=None):
    """Generate a cocotb Makefile for one declared Verilator run.

    ``expose_internal`` is reserved for a replay carrying a closed frontend-owned
    state list.  It enables Verilator's documented VPI visibility boundary without
    rewriting the candidate RTL; ordinary scoring builds keep their smaller surface.
    ``boundary_signals`` names the compiler-declared storage used for optional
    source-query boundaries, including child modules. It exposes these names rather
    than every helper temporary and parameter. Explicit state audits retain the
    full visibility profile above. An explicit empty list needs ports only;
    None retains cocotb's default visibility for external user testbenches.
    ``coverage`` enables optional compiler line/toggle telemetry. The authoritative
    driver records its frozen structural catalogue directly without this cost.
    ``initial_state`` links the source-register adapter for analysis-only startup;
    its static source is included in the authenticated build workspace.
    ``native_events`` also preserves process boundaries so optimization cannot
    schedule the observer independently of the source body it records.
    """
    if not isinstance(top_module, str) or re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_$]*", top_module
    ) is None:
        raise ValueError("the RTL top module must be a simple identifier")
    if type(tb_module) is not str or not all(part.isidentifier() for part in tb_module.split(".")):
        raise ValueError("the cocotb test module must be a dotted Python module name")
    if type(expose_internal) is not bool:
        raise TypeError("internal RTL visibility is an exact Boolean")
    if type(coverage) is not bool:
        raise TypeError("Verilator coverage selection is an exact Boolean")
    if type(initial_state) is not bool:
        raise TypeError("source register initialization is an exact Boolean")
    if type(native_events) is not bool:
        raise TypeError("native source observation is an exact Boolean")
    native_args = ""
    if native_events:
        from .rtl.native_events import VERILATOR_FLAGS

        native_args = "EXTRA_ARGS += " + " ".join((*VERILATOR_FLAGS, "/work/collector.cpp")) + "\n"
    # The control-file matcher uses Verilator's encoded names for escaped HDL
    # identifiers. Keep their existing full visibility until we carry that
    # compiler-owned name mapping; never lose a requested observation handle.
    if any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name) is None
           for name in (boundary_signals or ())):
        expose_internal = True
    visibility = "EXTRA_ARGS += --public-flat-rw\n" if expose_internal else ""
    if boundary_signals is not None and not expose_internal:
        # Names come from elaborated storage, not identifier guesses in source.
        # Verilator matches declaration names (including generate scopes) here.
        # A query can read a child module's state; limiting the module pattern
        # to the top would omit those handles even with a correct physical path.
        controls = ["`verilator_config", 'public_flat_rw -module "*" -port "*"']
        controls.extend(
            f'public_flat_rw -module "*" -var {json.dumps(name)}'
            for name in sorted(set(boundary_signals)))
        (problem_dir / "visibility.vlt").write_text("\n".join(controls) + "\n")
        visibility = "EXTRA_ARGS += --no-public-flat-rw $(CURDIR)/visibility.vlt\n"
    makefile = render_template(
        "Makefile.tmpl",
        top_module=top_module,
        tb_module=tb_module,
        verilator_coverage=("EXTRA_ARGS += --coverage\n" if coverage else ""),
        verilator_visibility=visibility,
        verilator_initial_state=(
            "EXTRA_ARGS += /work/initial_state.cpp\n" if initial_state else ""),
        verilator_native_events=native_args,
    )
    mk_path = problem_dir / "Makefile"
    with open(mk_path, "w") as f:
        f.write(makefile)
