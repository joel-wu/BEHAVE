"""One tool-judge compiler preparation for sites, relations and state ownership.

The submitted source stays the native simulator input. Only the temporary Yosys
input is normalized, with compiler provenance projected back to original byte
ranges. All callers use this entry point; there is no per-consumer fallback.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from behave.sim.core.source_rules import EMPTY_SOURCE_RULES, SourceGoalRules

from .bulk_initialization import VERSION as BULK_VERSION
from .bulk_initialization import initial_loops, normalize_initial_loops
from .constant_arrays import (
    VERSION,
    UnsupportedConstantArray,
    array_declarations,
    normalize_constant_arrays,
)
from .elaboration import SOURCE_FILENAME
from .source_locations import SOURCE_ATTRIBUTES
from .source_processes import VERSION as PROCESS_VERSION
from .source_processes import mark_initial_processes
from .source_replacements import SourceReplacements
from .source_returns import VERSION as RETURN_VERSION
from .source_returns import normalize_helper_returns
from .source_imports import VERSION as IMPORT_VERSION
from .source_imports import imports_in, normalize_package_imports
from .source_rules import (
    DIAGNOSTICS_FILE,
    REJECTION_EXIT,
    REJECTION_FILE,
    SLANG_ANALYSIS_REJECTION,
    TYPED_DIAGNOSTICS_FILE,
    SourceRuleRejected,
    from_compiler,
)

# Match the simulator's support for startup initialization of sequential storage.
# This does not permit multiple always_comb / always_ff drivers.
# Forward variable references follow the simulator's compatibility dialect.
# Apply this at every shared parse, including catalogue and symbolic preparation,
# rather than retrying individual submissions under different language rules.
# A ToolCommand owns one CPU permit. Parallelism belongs to the enclosing
# ToolGroup, not a parser pool sized from every CPU visible on the host.
# Same default as native compilation; explicit HDL time units still take
# precedence. A package need not invent a time scale just to be observed.
# An out-of-range select is a diagnostic, not a failed elaboration: native
# simulation retains its language-defined read/write behavior. Keep the
# warning and the original expression. Exact lowering must still prove its
# own definedness obligations; this does not authorize zero-filling its IR.
SLANG_FLAGS = ("--allow-dup-initial-drivers", "--allow-use-before-declare",
               "--timescale", "1ns/1ps", "--threads", "1",
               "-Wno-error=index-oob", "-Wno-error=range-oob")


def parse_observation_source(where, top, *, run):
    """One compiler invocation owns syntax, typed symbols and process reads."""
    if not run(where, "/tools/behave-rtl-frontend", *SLANG_FLAGS,
               "--top", top, "--cst-json", "frontend.cst.json",
               "--ast-json", "frontend.ast.json",
               "--sensitivity-json", "frontend.sensitivity.json", SOURCE_FILENAME):
        return None
    documents = tuple(json.loads((where / name).read_text()) for name in (
        "frontend.cst.json", "frontend.ast.json", "frontend.sensitivity.json"))
    cst, ast, sensitivity = documents
    return (cst, ast), sensitivity


def parse_source(where, top, *, run, typed=False):
    """Parse once at the preparation boundary; typed data is requested explicitly."""
    args = (("--top", top, "--ast-json", "frontend.ast.json", "--ast-json-source-info",
             "--ast-json-detailed-types") if typed else ("--parse-only",))
    # Complete source diagnostics belong to the result, not a count-based
    # execution quota. The enclosing tool job already owns resource limits.
    if not run(where, "/tools/slang", *SLANG_FLAGS, *args,
               "--error-limit=0", "--diag-json", DIAGNOSTICS_FILE,
               "--cst-json", "frontend.cst.json", SOURCE_FILENAME):
        return None
    cst = json.loads((where / "frontend.cst.json").read_text())
    ast = json.loads((where / "frontend.ast.json").read_text()) if typed else None
    return cst, ast


def declare_catalogue(where, top, *, run, source_rules=EMPTY_SOURCE_RULES):
    """Declare source events without building an optimized circuit or observer.

    Compiler type/instance resolution selects the source closure. Whether its
    events can subsequently be instrumented or encoded does not change this
    manifest, and is not tested by catalogue construction.
    """
    from .source_declarations import declare_source
    from .source_goals import manifest_of

    parsed = parse_source(where, top, run=run, typed=True)
    if parsed is None:
        return False
    source = (where / SOURCE_FILENAME).read_text(encoding="utf-8")
    declarations = declare_source(source, parsed[1], filename=SOURCE_FILENAME, source_rules=source_rules)
    (where / "source-goals.json").write_text(json.dumps(
        manifest_of(declarations, filename=SOURCE_FILENAME).as_dict()))
    return True


def compile_source(where, top, script, *, run, parsed=None, source_catalogue=False):
    """Run within the tool judge's existing process/resource/time boundary."""
    source_path = where / SOURCE_FILENAME
    source = source_path.read_bytes().decode("utf-8")
    parsed = parse_source(where, top, run=run, typed=source_catalogue) if parsed is None else parsed
    if parsed is None:
        return False
    cst, ast = parsed
    if imports_in(cst) and ast is None:
        typed = parse_source(where, top, run=run, typed=True)
        if typed is None:
            return False
        cst, ast = typed
    if source_catalogue:
        from .source_declarations import declare_source
        from .source_goals import DEFAULT_SOURCE_SCHEMAS, manifest_of

        declarations = declare_source(source, ast, filename=SOURCE_FILENAME)
        (where / "source-goals.json").write_text(json.dumps(
            manifest_of(declarations, filename=SOURCE_FILENAME,
                        schemas=DEFAULT_SOURCE_SCHEMAS).as_dict()))
    declarations = tuple(decl for tree in cst["syntaxTrees"]
                         for decl in array_declarations(tree["root"]))
    loops = tuple(loop for tree in cst["syntaxTrees"] for loop in initial_loops(tree["root"]))
    files = {}
    normalization = SourceReplacements(source, ())
    if declarations or loops:
        typed = ast is not None or run(
            where, "/tools/slang", *SLANG_FLAGS, "--top", top, "--ast-json", "frontend.ast.json",
            "--ast-json-source-info", "--ast-json-detailed-types",
            "--error-limit=0", "--diag-json", DIAGNOSTICS_FILE, SOURCE_FILENAME)
        if not typed and declarations:
            return False
        # Bulk initialization is optional: if its typed proof is unavailable,
        # the unchanged source still goes through the same Yosys compiler.
        # ROM syntax normalization, by contrast, requires its typed proof.
        if typed:
            try:
                if ast is None:
                    ast = json.loads((where / "frontend.ast.json").read_text())
                normalization = normalize_constant_arrays(source, cst, ast)
                bulk, files = normalize_initial_loops(source, cst, ast)
                normalization = SourceReplacements(source, tuple(sorted(
                    (*normalization.replacements, *bulk.replacements), key=lambda edit: edit.start)))
            except UnsupportedConstantArray as exc:
                print(f"rtl-constant-array-unsupported: {exc}", file=sys.stderr, flush=True)
                return False
    returns = normalize_helper_returns(source, cst)
    imports = normalize_package_imports(source, cst, ast)
    normalization = SourceReplacements(source, tuple(sorted(
        (*normalization.replacements, *returns.replacements, *imports.replacements),
        key=lambda edit: edit.start)))
    # The enclosing ToolExecution authenticates this closed manifest through its
    # command stdout, in addition to original inputs and the locked runtime image.
    print(json.dumps({"frontend": VERSION, "slang_flags": SLANG_FLAGS,
                      "source_processes": PROCESS_VERSION,
                      "bulk_initialization": BULK_VERSION,
                      "helper_returns": RETURN_VERSION,
                      "package_imports": IMPORT_VERSION,
                      "initial_images": {name: hashlib.sha256(data).hexdigest()
                                         for name, data in files.items()},
                      **normalization.manifest()}, sort_keys=True), flush=True)
    try:
        for name, data in files.items():
            (where / name).write_bytes(data)
        source_path.write_text(normalization.text)
        success = run(where, "/tools/yosys", "-Q", "-T", "-s", script)
        if success:
            # Project only outputs of this invocation; observer preparation also
            # keeps already-projected documents in the same private directory.
            commands = [line.split() for line in (where / script).read_text().splitlines()]
            for filename in (words[1] for words in commands
                             if len(words) == 2 and words[0] == "write_rtlil"):
                path = where / filename
                path.write_text(mark_initial_processes(
                    source, cst, normalization.rtlil(path.read_text())))
            # Ownership and the symbolic state mapper also consume src attributes.
            for filename in (words[1] for words in commands
                             if len(words) == 2 and words[0] == "write_json"):
                path = where / filename
                document = json.loads(path.read_text())

                def project(value):
                    if type(value) is dict:
                        attributes = value.get("attributes")
                        if type(attributes) is dict:
                            for name in SOURCE_ATTRIBUTES & attributes.keys():
                                if type(attributes[name]) is str:
                                    try:
                                        attributes[name] = normalization.locations(attributes[name])
                                    except ValueError as error:
                                        raise ValueError(f"cannot project {name}={attributes[name]!r}: {error}") from error
                        for child in value.values():
                            project(child)
                    elif type(value) is list:
                        for child in value:
                            project(child)

                project(document)
                path.write_text(json.dumps(document))
        return success
    finally:
        source_path.write_text(source)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("top")
    parser.add_argument("script", nargs="?")
    parser.add_argument("--source-catalogue", action="store_true")
    parser.add_argument("--declare-source", action="store_true")
    parser.add_argument("--observe-source", action="store_true")
    parser.add_argument("--source-rules", type=Path)
    args = parser.parse_args()
    if args.declare_source or args.observe_source:
        if args.script is not None or args.source_catalogue or (args.declare_source and args.observe_source):
            parser.error("source preparation selects one mode and no synthesis script")
    elif args.script is None:
        parser.error("circuit compilation requires a synthesis script")
    source_options = {}
    if args.source_rules is not None:
        if not args.declare_source:
            parser.error("source rules belong to the typed source catalogue")
        source_options["source_rules"] = SourceGoalRules.from_dict(
            json.loads(args.source_rules.read_text()))

    def run(where, *argv):
        completed = subprocess.run(argv, cwd=where, check=False)
        if argv[0] == "/tools/slang" and completed.returncode == SLANG_ANALYSIS_REJECTION:
            # The standard JSON client omits intrinsic diagnostic IDs. The
            # same pinned compiler library exposes them without constructing
            # source observations. Only its completed typed language evidence
            # can establish a source failure; an unknown error stays refused.
            checked = subprocess.run(("/tools/behave-rtl-frontend", *SLANG_FLAGS,
                "--top", args.top, "--error-limit=0", "--source-check-json",
                TYPED_DIAGNOSTICS_FILE, SOURCE_FILENAME), cwd=where, check=False)
            if checked.returncode == SLANG_ANALYSIS_REJECTION:
                rejected = from_compiler((where / TYPED_DIAGNOSTICS_FILE).read_bytes())
                if rejected is not None:
                    raise rejected
        return completed.returncode == 0

    try:
        success = (parse_observation_source(Path.cwd(), args.top, run=run) is not None
                   if args.observe_source else
                   declare_catalogue(Path.cwd(), args.top, run=run, **source_options) if args.declare_source else
                   compile_source(Path.cwd(), args.top, args.script, run=run,
                                  source_catalogue=args.source_catalogue))
    except UnsupportedConstantArray as exc:
        print(f"rtl-constant-array-unsupported: {exc}", file=sys.stderr, flush=True)
        success = False
    except SourceRuleRejected as exc:
        Path(REJECTION_FILE).write_text(json.dumps(exc.as_dict()))
        raise SystemExit(REJECTION_EXIT) from None
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
