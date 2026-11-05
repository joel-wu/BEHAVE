"""Pure parser for structural-site rows in pre-proc RTLIL.

The module intentionally depends only on the Python standard library.  The RTL tool
judge mounts this exact file and runs it before compiler output crosses into the
verifier; the host-side ``Site`` reader consumes the same row schema.
"""

from __future__ import annotations

import hashlib
import json
import re
from itertools import pairwise
from typing import NamedTuple

__all__ = [
    "RTLIL_SITES_VERSION",
    "pack_site_rows",
    "rtlil_action_projection",
    "rtlil_site_document",
    "rtlil_site_rows",
    "rtlil_source_projection",
    "unpack_site_rows",
]

RTLIL_SITES_VERSION = "rtlil-sites/9"
SOURCE_INITIAL_ATTRIBUTE = "behave_source_initial"


def pack_site_rows(rows):
    """Intern ordered switch labels without changing any occurrence or arm."""
    labels, indices, packed = [], {}, []

    def intern(values):
        key = tuple(values)
        if key not in indices:
            indices[key] = len(labels)
            labels.append(list(key))
        return indices[key]

    for source in rows:
        row = dict(source)
        row["siblings"] = intern(source["siblings"])
        row["activation_frames"] = [intern(frame["labels"])
                                    for frame in source["activation_frames"]]
        packed.append(row)
    return {"labels": labels, "sites": packed}


def unpack_site_rows(document):
    """Validate label references, then share their closed lists between rows.

    The receiving Site reader validates the remaining fields. Label lists retain
    order and multiplicity: neither a set nor a truncated prefix is equivalent.
    """
    if type(document) is not dict or set(document) != {"labels", "sites"}:
        raise ValueError("a compact site document has an invalid shape")
    labels, rows = document["labels"], document["sites"]
    if (type(labels) is not list or type(rows) is not list
            or any(type(values) is not list
                   or any(type(label) is not str for label in values)
                   for values in labels)):
        raise ValueError("a compact site document has invalid label sets")

    def read(index):
        if type(index) is not int or not 0 <= index < len(labels):
            raise ValueError("a compact site row has an invalid label reference")
        return labels[index]

    result = []
    for source in rows:
        if type(source) is not dict or "siblings" not in source \
                or type(source.get("activation_frames")) is not list:
            raise ValueError("a compact site row has an invalid shape")
        row = dict(source)
        row["siblings"] = read(source["siblings"])
        row["activation_frames"] = [{"labels": read(index)}
                                    for index in source["activation_frames"]]
        result.append(row)
    return result

_SRC = re.compile(r'^\s*attribute\s+\\src\s+"([^"]+)"')
_SWITCH = re.compile(r"^(\s*)switch\s+(.+?)\s*$")
_CASE = re.compile(r"^(\s*)case\s*(.*?)\s*$")
_PROCESS = re.compile(r"^\s*process\s+(\S+)")
_SYNC = re.compile(r"^\s*sync\s+(\S+)(?:\s+(.+?))?\s*$")
_MODULE = re.compile(r"^\s*module\s+(\S+)")
_END = re.compile(r"^(\s*)end\s*$")
_ASSIGN = re.compile(r"^(\s*)assign\s+(.+?)\s*$")
_SSA_WIRE = re.compile(r"\$[0-9]+(?=\\)")
_GENERATED_ORDINAL = re.compile(r"\$[0-9]+")
_INDEX = re.compile(r"\[[0-9]+(?::[0-9]+)?\]")
_NEXT_STATE_RANGE = re.compile(r"(?<=\w)\[[0-9]+:0\](?=\s|$|\})")


class ProcessSyncs(NamedTuple):
    phases: tuple[tuple[str, str, bool], ...]
    initial: bool


def process_syncs(rtlil: str) -> dict[tuple[str, str], ProcessSyncs]:
    """Read process sync phases, including whether each phase updates state.

    Source ownership comes from the common frontend, not the shape of temporary
    writes. The action flag also retains support for raw RTLIL's empty init
    scaffold. Explicit event triggers with no updates are still events.
    """
    phases = {}
    initial = {}
    pending_initial = False
    module = process = None
    current = None
    process_indent = -1
    for line in rtlil.splitlines():
        if line.strip() == "attribute \\" + SOURCE_INITIAL_ATTRIBUTE + " 1":
            pending_initial = True
            continue
        if got := _MODULE.match(line):
            module, process, current = got[1].lstrip("\\"), None, None
        elif got := _PROCESS.match(line):
            process, current = got[1], None
            process_indent = len(line) - len(line.lstrip())
            phases[(module, process)] = []
            initial[(module, process)] = pending_initial
        elif got := _END.match(line):
            if len(got[1]) <= process_indent:
                process, current = None, None
        elif process is not None:
            if got := _SYNC.match(line):
                current = [got[1], got[2] or "", False]
                phases[(module, process)].append(current)
            elif current is not None and line.strip().startswith(("update ", "memwr ")):
                current[2] = True
        if not line.strip().startswith("attribute "):
            pending_initial = False
    return {key: ProcessSyncs(tuple(tuple(phase) for phase in value), initial[key])
            for key, value in phases.items()}


def runtime_syncs(process: ProcessSyncs) -> tuple[tuple[str, str], ...]:
    """Omit initialization and its empty always scaffold, not real event bodies."""
    if process.initial:
        return ()
    phases = process.phases
    initialized = any(kind == "init" and writes for kind, _signal, writes in phases)
    return tuple((kind, signal) for kind, signal, writes in phases
                 if kind != "init" and not (kind == "always" and initialized and not writes))


def scalar_input_aliases(rtlil: str) -> dict[str, dict[str, str]]:
    """Prove whole-scalar input aliases from module-level RTLIL connections.

    Follow directed, unique connections to an undriven scalar input. Cells,
    expressions, slices, concatenations, constants, cycles and multiple drivers
    supply no such proof. This is a partial wiring analysis, not expression
    evaluation or a guess based on hierarchical names. Its result contains only
    terminal input names, so consumers never chase aliases during execution.
    """
    modules = {}
    module = None
    for line in rtlil.splitlines():
        fields = line.split()
        if not fields:
            continue
        if fields[0] == "module":
            module = fields[1].lstrip("\\")
            modules[module] = ({}, set(), {}, set())
        if module is None:
            continue
        wires, inputs, connections, written = modules[module]
        indent = len(line) - len(line.lstrip())
        if fields[0] == "wire" and indent == 2:
            name = fields[-1]
            width = int(fields[fields.index("width") + 1]) if "width" in fields else 1
            wires[name] = width
            if "input" in fields and "output" not in fields and width == 1:
                inputs.add(name)
        elif fields[0] == "connect" and indent == 2:
            left, right = _assignment_sigspecs(line.strip()[len("connect "):])
            # Even an unsupported write invalidates a previous simple binding.
            for name in _lhs_wires(left):
                connections.setdefault(name, []).append((left, right))
        elif fields[0] == "connect":
            # Direction is not present on a cell-port connection. Do not prove
            # an alias through a potentially driven port without that evidence.
            _port, value = _assignment_sigspecs(line.strip()[len("connect "):])
            written.update(_lhs_wires(value))
        elif fields[0] in {"assign", "update"}:
            left, _right = _assignment_sigspecs(line.strip().split(" ", 1)[1])
            written.update(_lhs_wires(left))

    result = {}
    for module, (wires, inputs, connections, written) in modules.items():
        roots = inputs - connections.keys() - written
        resolved = {name: name for name in roots}
        for start in connections:
            path, seen, name = [], set(), start
            while name not in resolved:
                if name in seen:
                    break
                seen.add(name)
                path.append(name)
                edges = connections.get(name, ())
                if wires.get(name) != 1 or name in written or len(edges) != 1:
                    break
                left, right = edges[0]
                if left != name or wires.get(right) != 1:
                    break
                name = right
            root = resolved.get(name)
            for alias in path:
                resolved[alias] = root
        result[module] = {alias: root for alias, root in resolved.items()
                          if root is not None and alias != root}
    return result


def _assignment_sigspecs(action: str) -> tuple[str, str]:
    """Split one RTLIL process assignment into its left and right SigSpecs."""

    if type(action) is not str or not action.strip():
        raise ValueError("an RTLIL action must be nonempty text")
    action = action.strip()
    if action.startswith("{"):
        depth = 0
        for index, character in enumerate(action):
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    right = action[index + 1:].strip()
                    if not right:
                        break
                    return action[:index + 1], right
        raise ValueError("an RTLIL aggregate assignment has no right-hand SigSpec")

    fields = action.split()
    if len(fields) < 2:
        raise ValueError("an RTLIL assignment has no right-hand SigSpec")
    end = 1
    while end < len(fields) and fields[end].startswith("["):
        end += 1
    if end == len(fields):
        raise ValueError("an RTLIL assignment has no right-hand SigSpec")
    return " ".join(fields[:end]), " ".join(fields[end:])


def rtlil_source_projection(text: str) -> str:
    """Remove only Yosys expansion ordinals and concrete unroll indices.

    This projection never decides whether two occurrences executed.  It gives the
    stable source-projected action name to which their independently retained
    occurrence keys
    are attached.
    """

    if type(text) is not str:
        raise TypeError("an RTLIL source projection requires exact text")
    projected = _SSA_WIRE.sub("", text)
    projected = _GENERATED_ORDINAL.sub("$*", projected)
    projected = _INDEX.sub("[*]", projected)
    return " ".join(projected.split())


def _hold_projection(text: str) -> str:
    projected = _SSA_WIRE.sub("", text)
    projected = _NEXT_STATE_RANGE.sub("", projected)
    return " ".join(projected.split())


def _lhs_wires(sigspec: str) -> tuple[str, ...]:
    # Escaped RTLIL names end at whitespace.  Automatic names begin with ``$`` and
    # likewise remain one token even when the source range inside them has brackets.
    return tuple(
        token for token in sigspec.replace("{", " ").replace("}", " ").split()
        if token.startswith(("\\", "$"))
    )


def rtlil_action_projection(action: str) -> str | None:
    """Return a source-projected action, or ``None`` for compiler work.

    Pre-proc RTLIL contains assignments to the design's named signals, but also the
    SSA machinery used to evaluate functions and procedural expressions.  Only an
    effective write to at least one design-visible signal is an RTL action site.  The
    discarded rows remain represented in the transition relation; they simply are
    not separate public coverage goals.
    """

    left, right = _assignment_sigspecs(action)
    wires = _lhs_wires(left)
    visible = False
    for wire in wires:
        base = _SSA_WIRE.sub("", wire)
        if base.startswith("\\") and "$func$" not in base:
            visible = True
    if not visible:
        return None
    # Yosys initializes every next-state wire from the current state before applying
    # guarded source writes.  That bookkeeping is not an executed source action.
    if _hold_projection(left) == _hold_projection(right):
        return None
    return rtlil_source_projection(action)


def rtlil_site_rows(payload: bytes | str) -> list[dict[str, object]]:
    """Return closed control-site rows from one pre-proc RTLIL document."""
    return _site_document(payload, include_regions=False)["sites"]


def rtlil_site_document(payload: bytes | str) -> dict[str, list[dict[str, object]]]:
    """Read sites and their control regions in the same parse.

    ``regions`` parallels ``sites``. Each entry names the process header and
    enclosing (switch header, arm label) pairs, including the site's own arm for
    a choice. Header numbers are one-based RTLIL lines, not source coordinates
    or public identifiers. They join to a byte-checked compiler-origin document.
    The ordinary catalogue reader does not allocate this optional side table.
    """
    return _site_document(payload, include_regions=True)


def _site_document(payload, *, include_regions):

    if type(payload) is bytes:
        try:
            rtlil = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ValueError("the RTLIL document is not UTF-8") from error
    elif type(payload) is str:
        rtlil = payload
    else:
        raise TypeError("the RTLIL document must be bytes or text")

    sites: list[dict[str, object]] = []
    module = process = None
    pending_src = None
    # (indent, kind, signal_or_case, src, switch_record)
    stack: list[tuple[int, str, str, str | None, dict[str, object] | None]] = []
    switches: list[dict[str, object]] = []
    switch_counts: dict[tuple[object, ...], int] = {}
    action_counts: dict[tuple[object, ...], int] = {}
    process_triggers = process_syncs(rtlil)
    input_aliases = scalar_input_aliases(rtlil)
    process_line = None

    def region(enclosing):
        return {"process": process_line,
                "arms": [[outer[4]["line"], inner[2]] for outer, inner in enclosing]}

    for line_number, line in enumerate(rtlil.splitlines(), 1):
        got = _SRC.match(line)
        if got:
            pending_src = got.group(1)
            continue

        got = _MODULE.match(line)
        if got:
            module, process, stack = got.group(1).lstrip("\\"), None, []
            continue

        got = _PROCESS.match(line)
        if got:
            process, stack = got.group(1), []
            process_line = line_number
            continue

        got = _SYNC.match(line)
        if got and module is not None and process is not None:
            pending_src = None
            continue

        got = _SWITCH.match(line)
        if got:
            indent, signal = len(got.group(1)), got.group(2)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            switch_src = pending_src or "?"
            enclosing = [
                (outer, inner)
                for outer, inner in pairwise(stack)
                if outer[1] == "switch" and inner[1] == "case"
            ]
            parent = tuple(
                (str(outer[4]["id"]), inner[2])
                for outer, inner in enclosing
                if type(outer[4]) is dict
            )
            coordinate = (module, process, parent, signal, switch_src)
            ordinal = switch_counts.get(coordinate, 0)
            switch_counts[coordinate] = ordinal + 1
            identity_payload = json.dumps(
                [module, process, [list(pair) for pair in parent], signal,
                 switch_src, ordinal],
                ensure_ascii=True, separators=(",", ":"),
            ).encode("utf-8")
            own: dict[str, object] = {
                "id": hashlib.blake2s(identity_payload, digest_size=8).hexdigest(),
                "labels": [], "sites": [], "src": switch_src,
                "line": line_number,
            }
            switches.append(own)
            stack.append((indent, "switch", signal, pending_src, own))
            pending_src = None
            continue

        got = _CASE.match(line)
        if got:
            indent, label = len(got.group(1)), got.group(2) or "default"
            while stack and stack[-1][0] >= indent and stack[-1][1] == "case":
                stack.pop()
            switch = next(
                (frame for frame in reversed(stack) if frame[1] == "switch"),
                None,
            )
            if switch is not None and module is not None:
                enclosing = [
                    (outer, inner)
                    for outer, inner in pairwise(stack)
                    if outer[1] == "switch" and inner[1] == "case"
                ]
                row: dict[str, object] = {
                    "kind": "case-arm",
                    "module": module,
                    "process": process,
                    "signal": switch[2],
                    "switch_id": switch[4]["id"],
                    "src": pending_src or switch[3] or "?",
                    "outcome": label,
                    "depth": sum(frame[1] == "switch" for frame in stack),
                    "activation": [[outer[2], inner[2]]
                                   for outer, inner in enclosing],
                    "siblings": [],
                    "action": None,
                    # These live records receive later case labels.  They are replaced
                    # by closed JSON rows before this function returns.
                    "_activation_frames": [outer[4] for outer, _inner in enclosing],
                }
                if include_regions:
                    row["_region"] = region(enclosing)
                    row["_region"]["arms"].append([switch[4]["line"], label])
                sites.append(row)
                labels = switch[4]["labels"]
                produced = switch[4]["sites"]
                assert type(labels) is list and type(produced) is list
                labels.append(label)
                produced.append(row)
            stack.append((indent, "case", label, pending_src, None))
            pending_src = None
            continue

        got = _ASSIGN.match(line)
        if got and module is not None and process is not None:
            indent, action = len(got.group(1)), got.group(2)
            # Yosys emits an empty case-rule assignment when elaboration has removed
            # both sides.  It performs no action and therefore is not an execution
            # location.
            if action == "{ } { }":
                pending_src = None
                continue
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if rtlil_action_projection(action) is None:
                pending_src = None
                continue
            enclosing = [
                (outer, inner)
                for outer, inner in pairwise(stack)
                if outer[1] == "switch" and inner[1] == "case"
            ]
            parent = tuple(
                (str(outer[4]["id"]), inner[2])
                for outer, inner in enclosing
                if type(outer[4]) is dict
            )
            # RTLIL case-rule assignments do not carry their own attributes.  Their
            # exact elaborated identity is therefore the process, control path and
            # action text; ``src`` is only a readable projection inherited from the
            # nearest enclosing case/switch when one exists.
            source = pending_src or next(
                (frame[3] for frame in reversed(stack) if frame[3]), "?")
            coordinate = (module, process, parent, action, source)
            ordinal = action_counts.get(coordinate, 0)
            action_counts[coordinate] = ordinal + 1
            identity_payload = json.dumps(
                [module, process, [list(pair) for pair in parent], action,
                 source, ordinal],
                ensure_ascii=True, separators=(",", ":"),
            ).encode("utf-8")
            row = {
                "kind": "location",
                "module": module,
                "process": process,
                "signal": "1'1",
                "switch_id": hashlib.blake2s(
                    identity_payload, digest_size=8).hexdigest(),
                "src": source,
                "outcome": "entry",
                "depth": sum(frame[1] == "switch" for frame in stack),
                "activation": [[outer[2], inner[2]]
                               for outer, inner in enclosing],
                "siblings": [],
                "action": action,
                "_activation_frames": [outer[4] for outer, _inner in enclosing],
            }
            if include_regions:
                row["_region"] = region(enclosing)
            sites.append(row)
            pending_src = None
            continue

        got = _END.match(line)
        if got:
            indent = len(got.group(1))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            pending_src = None
            continue

        pending_src = None

    for switch in switches:
        labels = switch["labels"]
        produced = switch["sites"]
        assert type(labels) is list and type(produced) is list
        # RTLIL omits a `case` row for the path on which no explicit arm matches.
        # That path is nevertheless one finite outcome of a switch without a default,
        # and the structural provider must account for it in the same denominator as
        # the written arms.  Clone the switch's closed structural coordinates from one
        # real arm; only the outcome is synthetic.  A switch with no cases has no body
        # occurrence to reconcile and therefore contributes no structural goal.
        if produced and "default" not in labels:
            template = produced[0]
            no_match = {
                key: (list(value) if key in ("activation", "siblings") else value)
                for key, value in template.items()
                if key != "_activation_frames"
            }
            no_match["outcome"] = "no-match"
            no_match["src"] = switch["src"]
            no_match["_activation_frames"] = list(
                template.get("_activation_frames", ()))
            if include_regions:
                no_match["_region"] = {
                    "process": template["_region"]["process"],
                    "arms": [*template["_region"]["arms"][:-1], [switch["line"], "no-match"]],
                }
            labels.append("no-match")
            produced.append(no_match)
            sites.append(no_match)
        for row in produced:
            row["siblings"] = labels
    runtime_sites: list[dict[str, object]] = []
    for row in sites:
        key = (str(row["module"]), str(row["process"]))
        triggers = process_triggers.get(key, [])
        if not triggers or not triggers.phases:
            raise ValueError(
                f"RTLIL process {key[0]}.{key[1]} has structural actions but no "
                "closed sync trigger")
        runtime = runtime_syncs(triggers)
        # Initial blocks establish the trace's initial state; they are not execution
        # locations in any event slot.
        if runtime:
            row["triggers"] = [list(pair) for pair in runtime]
            reads = {name for signal in (row["signal"],
                     *(pair[0] for pair in row["activation"]),
                     *(pair[1] for pair in runtime)) for name in _lhs_wires(signal)}
            aliases = input_aliases.get(row["module"], {})
            row["input_aliases"] = [[name, aliases[name]]
                                    for name in sorted(reads & aliases.keys())]
            runtime_sites.append(row)

    regions = []
    for row in runtime_sites:
        if include_regions:
            regions.append(row.pop("_region"))
        frames = row.pop("_activation_frames")
        assert type(frames) is list
        row["activation_frames"] = [
            {"labels": frame["labels"]}
            for frame in frames
            if type(frame) is dict and type(frame.get("labels")) is list
        ]
    return {"sites": runtime_sites, "regions": regions}
