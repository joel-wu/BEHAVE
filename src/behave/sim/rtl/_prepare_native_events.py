"""Trusted tool-judge entry point; never invoked on host-submitted syntax trees."""

import subprocess
from pathlib import Path

from ..core.source_rules import SourceGoalRules
from ..core.record_value import canonical_json
from ..strict_json import strict_json_loads
from .elaboration import SOURCE_FILENAME, elaboration_script
from .native_events import prepare_declared_native_events, prepare_native_events
from .native_preparation import VERSION, _request, _sha
from .shared_frontend import compile_source, parse_observation_source
from .source_boundary_capture import observation_fields
from .source_plan import plan_source
from .source_triggers import check_source_triggers


def main() -> None:
    root = Path("/work")
    payload = (root / "request.json").read_bytes()
    request = _request(strict_json_loads(payload))
    original = (root / SOURCE_FILENAME).read_bytes()
    if _sha(original) != request["source"]:
        raise ValueError("source bytes differ from the frozen native preparation request")
    base = {"version": VERSION, "request": _sha(payload)}

    def refuse(code, detail):
        (root / "prepared.json").write_text(canonical_json({
            **base, "status": "refused", "code": code, "detail": detail}))

    def run(where, *argv):
        # Inherit the outer ToolCommand's captured streams, including on refusal.
        return subprocess.run(argv, cwd=where, check=False).returncode == 0

    observation = parse_observation_source(root, request["top"], run=run)
    if observation is None:
        refuse("source-parse-failed", "the locked frontend did not produce the selected source design")
        return
    try:
        parsed, sensitivity = observation
        cst, ast = parsed
        plan = plan_source(original.decode("utf-8"), cst, semantic_document=ast,
                           sensitivity_document=sensitivity,
                           filename=SOURCE_FILENAME,
                           enumerate_iterations=request["catalogue_kind"] != "source",
                           source_rules=SourceGoalRules.from_dict(request["source_rules"]))
        plan.native()
    except ValueError as exc:
        refuse("source-mapping-unavailable", str(exc))
        return
    # Source callbacks record actual simulator activations, including derived
    # signal edges and explicit any-change waits. They do not infer execution
    # from the Driver's top-level edge set. The action/RTLIL binding below does
    # need that finite trigger representation; keep its admission gate there.
    if request["catalogue_kind"] != "source" and request["allowed_triggers"] is not None:
        try:
            check_source_triggers(plan.declarations,
                                  {tuple(pair) for pair in request["allowed_triggers"]})
        except ValueError as exc:
            refuse("unscheduled-trigger", str(exc))
            return
    arguments = {"filename": SOURCE_FILENAME, "toolchain_sha256": request["toolchain"],
                 "execution_sha256": request["execution"], "callback_limit": request["callback_limit"],
                 "catalogue_sha256": request["catalogue"], "scope_root": request["scope_root"],
                 "source_schemas": request["source_schemas"]}
    if request["catalogue_kind"] == "source":
        try:
            events = prepare_declared_native_events(plan, **arguments)
        except ValueError as exc:
            refuse("source-mapping-unavailable", str(exc))
            return
        publish(root, base, plan, events, boundary_fields=observation_fields(ast),
                top=request["top"], run=run, document=ast)
        return
    try:
        plan.require_compiler_binding()
    except ValueError as exc:
        refuse("source-mapping-unavailable", str(exc))
        return
    relations = []
    for name, source in (("original", original.decode("utf-8")), ("annotated", plan.compiler.text)):
        where = root / name
        where.mkdir()
        (where / SOURCE_FILENAME).write_text(source, encoding="utf-8")
        script = elaboration_script(request['top']) + "write_rtlil relation.rtlil\n"
        (where / "frontend.ys").write_text(script)
        if not compile_source(where, request['top'], "frontend.ys", run=run,
                              parsed=parsed if name == "original" else None):
            refuse(name + "-compile-failed", "the locked compiler did not produce the relation")
            return
        relations.append((where / "relation.rtlil").read_text())
    # A fresh compiler invocation preserves autoidx and generated identities.
    # Reloading a saved design retains the first flatten's global allocator.
    scoped_script = elaboration_script(request['top'], scoped=True) + "write_rtlil scoped.rtlil\n"
    (root / "annotated/scoped.ys").write_text(scoped_script)
    if not compile_source(root / "annotated", request['top'], "scoped.ys", run=run):
        refuse("annotated-compile-failed", "the compiler did not produce the context witness")
        return
    try:
        events = prepare_native_events(
            plan, *relations, scoped=(root / "annotated/scoped.rtlil").read_text(),
            source_goals=request["catalogue_kind"] == "mixed", **arguments)
    except ValueError as exc:
        refuse("source-mapping-unavailable", str(exc))
        return
    publish(root, base, plan, events)


def publish(root, base, plan, events, *, boundary_fields=(), top=None, run=None, document=None):
    from .source_native_layout import prepare_paths
    from .source_ports import startup_input_aliases
    from ..core.refusal import Refused

    collector = events.collector_source().encode("utf-8")
    edits = plan.native(scoped=bool(events.bindings.coordinates))
    native = edits.text
    (root / "native.sv").write_bytes(native.encode("utf-8"))
    paths = prepare_paths(root, top, edits, boundary_fields, run=run)
    try:
        aliases = None if document is None else startup_input_aliases(document)
    except (Refused, ValueError):
        # Optional exact construction must not block ordinary native baseline.
        aliases = None
    (root / "collector.cpp").write_bytes(collector)
    (root / "prepared.json").write_text(canonical_json({
        **base, "status": "prepared", "plan": events.as_dict(),
        "boundary_fields": [list(pair) for pair in boundary_fields],
        "boundary_paths": paths,
        "startup_aliases": aliases,
        "native_sha256": _sha(native.encode("utf-8")), "collector_sha256": _sha(collector),
    }))


if __name__ == "__main__":
    main()
