"""External workflows see the same public RTL-only specification as the baseline."""

import re

from behave.data.spec_format import headings
from behave.task_execution.solver import solver_input_files


def question(task):
    files = solver_input_files(task, setting="rtl_only")
    spec = next(file.data.decode("utf-8") for file in files if file.reference.path == task.spec_path)
    # Upstream methods take text rather than a file-reading tool. Include public
    # supporting text instead of silently omitting information the baseline sees.
    return spec + "".join(f"\n\n## Public file: {file.reference.path}\n\n{file.data.decode('utf-8')}"
                          for file in files if file.reference.path != task.spec_path)


def rtl_interface(spec):
    """Read the task author's RTL declaration, never a private RTL implementation."""
    entries = headings(spec)
    lines = spec.splitlines()
    for index, (level, title, start) in enumerate(entries):
        if title.casefold() != "rtl interface":
            continue
        end = next((line - 1 for lev, _, line in entries[index + 1:] if lev <= level), len(lines))
        section = "\n".join(lines[start:end])
        blocks = re.findall(r"```(?:systemverilog|verilog|sv)\s*\n(.*?)\n```", section, flags=re.S)
        for block in blocks:
            match = re.search(r"\bmodule\s+([A-Za-z_][A-Za-z_0-9$]*)\b", block)
            if match is not None:
                # Published header-only examples end at ');'. Complete that
                # declaration for a compiler, without adding any implementation.
                return match.group(1), block if re.search(r"\bendmodule\b", block) else block + "\nendmodule\n"
    raise ValueError("task specification has no explicit Verilog RTL interface declaration")


def chipmate_interface(spec):
    """Compile the public header to literal widths for upstream's simple parser.

    Signedness still appears in the unchanged question. Cross-verification
    transports bit patterns; its header has no signed keyword, which that
    upstream parser does not understand. No fallback width or reference body.
    """
    from behave.sim import api

    top, source = rtl_interface(spec)
    realization = api.realization_of_rtl_source(source, "published public RTL interface", top=top)
    declarations = [f"{'input' if port.driven_by == 'environment' else 'output'} "
                    f"[{port.width - 1}:0] {port.name}" for port in realization.ports.values()]
    return top, "module TopModule(\n" + ",\n".join(declarations) + "\n); endmodule\n"
