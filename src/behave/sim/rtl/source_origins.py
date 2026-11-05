"""Check compiler-origin annotations without renaming the original catalogue.

Compare the *whole* pre-proc RTLIL document, not just public goal counts. Only
our annotations and exactly mapped source-provenance coordinates may differ. Generated
names, actions, priority arms, triggers and every other attribute must agree.
This certificate establishes provenance retention, not native event binding.
"""

import re
from dataclasses import dataclass

from ._rtlil_sites import process_syncs, runtime_syncs
from .source_locations import SOURCE_ATTRIBUTE
from .source_plan import ORIGIN_ATTRIBUTE, SourcePlan

_ATTRIBUTE = re.compile(r'\s*attribute \\' + ORIGIN_ATTRIBUTE + r' "([^"\n]+)"\s*')
_LOCATION = re.compile(r'(.+):(\d+)\.(\d+)-(\d+)\.(\d+)')


@dataclass(frozen=True, slots=True)
class CompilerControl:
    origin: str
    module: str
    process: str
    kind: str
    ordinal: int
    src: str
    line: int


def compiler_origins(plan: SourcePlan, original: str, annotated: str,
                     *, filename: str) -> tuple[CompilerControl, ...]:
    """Require an unchanged relation/catalogue and bind every emitted control.

    Both compilations must use the same source filename and tool invocation.
    We intentionally do not repair renamed SSA wires or erase compiler changes.
    Multiple elaborated controls may retain the same source origin.
    """
    if type(plan) is not SourcePlan or any(type(text) is not str for text in
                                         (original, annotated, filename)) or not filename:
        raise TypeError("compiler origin validation requires a source plan and exact documents")
    if any("\n" in edit.text or "\r" in edit.text for edit in plan.compiler.insertions):
        raise ValueError("compiler origins must preserve line-based generated identifiers")
    known = {origin.key: origin for origin in plan.controls}
    normalized, controls, pending = [], [], None
    pending_source = ""
    triggers = process_syncs(annotated)
    module = process = ""
    for line in annotated.splitlines(keepends=True):
        attribute = _ATTRIBUTE.fullmatch(line)
        if attribute:
            key = attribute[1]
            if pending is not None or key not in known:
                raise ValueError("duplicate or unknown compiler origin")
            pending = key
            continue
        src = SOURCE_ATTRIBUTE.fullmatch(line)
        if src:
            locations = []
            for part in src["locations"].split("|"):
                match = _LOCATION.fullmatch(part)
                if not match or match[1] != filename:
                    raise ValueError("compiler source provenance is outside the source plan")
                if match.groups()[1:] == ("0", "0", "0", "0"):
                    # Yosys's explicit no-source sentinel for generated init work.
                    locations.append(part)
                    continue
                start = plan.compiler.source_position(int(match[2]), int(match[3]))
                end = plan.compiler.source_position(int(match[4]), int(match[5]))
                if start > end:
                    raise ValueError("reversed compiler source range")
                locations.append(f"{filename}:{start[0]}.{start[1]}-{end[0]}.{end[1]}")
            line = src["prefix"] + "|".join(locations) + src["suffix"]
            if src["name"] == "src":
                pending_source = "|".join(locations)
        normalized.append(line)
        stripped = line.strip()
        if stripped.startswith("attribute "):
            continue
        if stripped.startswith("module "):
            module, process = stripped.removeprefix("module ").lstrip("\\"), ""
        if stripped.startswith(("process ", "switch ")):
            kind = stripped.split()[0]
            if kind == "process":
                process = stripped.removeprefix("process ")
            if not module or not process:
                raise ValueError("an elaborated control has no explicit source origin")
            if pending is not None:
                origin = known[pending]
                expected = "process" if origin.kind.startswith("Always") or origin.kind == "InitialBlock" else "switch"
                if kind != expected:
                    raise ValueError("a compiler origin changed its control kind")
            controls.append(CompilerControl(pending or "", module, process, kind,
                                            len(controls), pending_source, len(normalized)))
            pending = None
        elif pending is not None:
            raise ValueError("compiler origin attached to an unsupported construct")
        pending_source = ""
    if pending is not None:
        raise ValueError("dangling compiler origin")
    if "".join(normalized) != original:
        raise ValueError("origin annotations changed the original RTLIL document")
    runtime = []
    for control in controls:
        sync = triggers[(control.module, control.process)]
        if sync.phases and not runtime_syncs(sync):
            continue  # Already checked byte-for-byte; not a runtime goal occurrence.
        if not sync.phases or not control.origin:
            raise ValueError("an elaborated runtime control has no explicit source origin")
        origin = known[control.origin]
        start, end = (plan.compiler.original_position(offset) for offset in origin.compiler_span)
        expected = f"{filename}:{start[0]}.{start[1]}-{end[0]}.{end[1]}"
        if expected not in control.src.split("|"):
            raise ValueError("compiler origin does not name that exact source control: "
                             f"{origin.key} expected {expected}, got {control.src}")
        runtime.append(control)
    return tuple(runtime)
