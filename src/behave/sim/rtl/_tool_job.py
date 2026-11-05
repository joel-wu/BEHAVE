"""Guest-side executor for one frozen RTL toolchain job.

This module is mounted as a file and executed by the pristine Python runtime in
``behave.isolation``.  It deliberately imports no BEHAVE module: the JSON job is
the complete command authority, ``/input/tree`` is the complete read-only input,
``/work`` holds command outputs, and ``/logs`` holds the captured streams.
"""

from __future__ import annotations

import base64
import concurrent.futures
import glob
import gzip
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path, PurePosixPath

_PROTOCOL = "rtl-tool-job/7"
_LOG_ROOT = Path("/logs")


def _relative(value: object, field: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{field} must be a nonempty path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError(f"{field} must be a normalized relative path")
    return value


def _bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _write_json(document) -> None:
    """Write the whole envelope, including short writes on unbuffered stdout.

    The guest runs with -u. A raw write can return fewer bytes than offered;
    TextIOWrapper/print does not retry that suffix. Large replay artifacts must
    not become truncated JSON merely because one write exceeds the OS limit.
    Iterating the encoder also avoids a second full envelope-sized string.
    """
    stream = sys.stdout.buffer
    for text in json.JSONEncoder(sort_keys=True, separators=(",", ":")).iterencode(document):
        remaining = memoryview(text.encode("utf-8"))
        while remaining:
            written = stream.write(remaining)
            if written is None or written <= 0:
                raise BrokenPipeError("the RTL tool protocol stream stopped accepting bytes")
            remaining = remaining[written:]
    stream.write(b"\n")
    stream.flush()


def _strict_json(payload: bytes, what: str, *, repeated_string_fields=()) -> object:
    """Decode hostile tool JSON inside the judge, not in the verifier."""

    def object_from_pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                if key in repeated_string_fields and type(value) is str and out[key] == value:
                    continue
                raise ValueError(f"{what} contains duplicate member {key!r}")
            out[key] = value
        return out

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"{what} contains a non-finite number")
        return result

    try:
        text = payload.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=object_from_pairs,
            parse_float=finite_float,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"{what} contains non-standard constant {value}")),
        )
    except (UnicodeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError(f"{what} is not one strict finite JSON value") from error


def _verilator_ports(payload: bytes, arguments: object, *, recursion_limit: int) -> bytes:
    """Reduce a Verilator syntax tree to its selected module's exact port rows."""

    if type(arguments) is not dict or set(arguments) != {"top"}:
        raise ValueError("verilator-ports/1 has invalid arguments")
    top = arguments["top"]
    if top is not None and (type(top) is not str or not top.isidentifier()):
        raise ValueError("verilator-ports/1 top is not an identifier")

    # Expression nesting is candidate-controlled.  The generous recursion limit is
    # confined to this disposable, memory-limited process; the verifier never parses
    # the raw tree.
    if type(recursion_limit) is not int or recursion_limit <= 0:
        raise ValueError("tool normalization recursion budget must be a positive integer")
    sys.setrecursionlimit(max(sys.getrecursionlimit(), recursion_limit))
    # Verilator emits redundant `name` strings for MEMBERDTYPE nodes. Accept
    # those identical strings at this compiler boundary, not conflicting names
    # or duplicate fields in the task/worker protocols and other tool formats.
    tree = _strict_json(payload, "the Verilator elaboration tree",
                        repeated_string_fields=("name",))
    types = {}
    modules = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if type(node) is dict:
            if node.get("addr") and "DTYPE" in str(node.get("type", "")):
                types[node["addr"]] = (node.get("range"), bool(node.get("signed")))
            if node.get("type") == "MODULE" \
                    and not str(node.get("name", "")).startswith("@"):
                modules.append(node)
            stack.extend(node.values())
        elif type(node) is list:
            stack.extend(node)
    if not modules:
        raise ValueError("the design declares no elaborated module")
    if top is None:
        levels = []
        for module in modules:
            level = module.get("level", 0) or 0
            if type(level) is not int or isinstance(level, bool) or level < 0:
                raise ValueError("an elaborated module has an invalid hierarchy level")
            levels.append((level, module))
        root_level = min(level for level, _module in levels)
        roots = [module for level, module in levels if level == root_level]
        if len(roots) != 1:
            names = sorted(str(module.get("name") or "") for module in roots)
            raise ValueError(
                "the design has no unique elaborated top module: "
                + ", ".join(names))
        chosen = roots[0]
    else:
        selected = [module for module in modules if module.get("name") == top]
        if len(selected) != 1:
            raise ValueError(f"the design does not select exactly one module {top!r}")
        chosen = selected[0]

    ports = []
    stack = [chosen]
    while stack:
        node = stack.pop()
        if type(node) is dict:
            if node.get("type") == "MODULE" and node is not chosen:
                continue
            if node.get("type") == "VAR" and node.get("isPrimaryIO") \
                    and node.get("direction") in {"INPUT", "OUTPUT", "INOUT"}:
                ports.append(node)
            stack.extend(node.values())
        elif type(node) is list:
            stack.extend(node)

    rows = []
    seen = set()
    for port in ports:
        name = port.get("name")
        direction = port.get("direction")
        if type(name) is not str or not name or name in seen:
            continue
        seen.add(name)
        span, signed = types.get(port.get("dtypep"), (None, False))
        if span is None:
            width = 1
        else:
            try:
                high, low = (int(part) for part in str(span).split(":"))
            except (TypeError, ValueError):
                raise ValueError(
                    f"port {name!r} has an unreadable range {span!r}") from None
            width = abs(high - low) + 1
        rows.append({
            "name": name,
            "direction": direction.lower(),
            "width": width,
            "signed": bool(signed),
        })
    rows.sort(key=lambda row: row["name"])
    chosen_name = chosen.get("name")
    if type(chosen_name) is not str or not chosen_name:
        raise ValueError("the selected elaborated module has no name")
    document = {
        "version": "verilator-ports/1",
        "top": chosen_name,
        "ports": rows,
    }
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _rtlil_sites(payload: bytes, arguments: object) -> bytes:
    """Reduce pre-proc RTLIL to closed control-site rows inside the judge."""

    if type(arguments) is not dict or arguments:
        raise ValueError("rtlil-sites/9 takes no arguments")
    parser_path = Path("/runtime/rtlil-sites.py")
    if not parser_path.is_file():
        parser_path = Path(__file__).with_name("_rtlil_sites.py")
    spec = importlib.util.spec_from_file_location(
        "_behave_rtlil_sites", parser_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("the frozen RTLIL site parser is unavailable")
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    if parser.RTLIL_SITES_VERSION != "rtlil-sites/9":
        raise RuntimeError("the frozen RTLIL site parser has the wrong version")
    rows = parser.rtlil_site_rows(payload)
    return json.dumps({
        "version": "rtlil-sites/9",
        **parser.pack_site_rows(rows),
    }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _yosys_top(arguments):
    if type(arguments) is not dict or set(arguments) != {"top"} \
            or type(arguments["top"]) is not str \
            or re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_$]*", arguments["top"]) is None:
        raise ValueError(
            "a Yosys output normalizer needs one identifier named top")
    return arguments["top"]


def _yosys_reducer(name):
    parser_path = Path("/runtime/yosys-" + name.replace("_", "-") + ".py")
    if not parser_path.is_file():
        parser_path = Path(__file__).with_name("_yosys_" + name + ".py")
    spec = importlib.util.spec_from_file_location(
        "_behave_yosys_" + name, parser_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("the frozen Yosys reducer is unavailable: " + name)
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    return parser


def _rtl_relation_metadata(payload: bytes, arguments: object, *, source_state=False) -> bytes:
    """Bind reduced storage/data-cone evidence to the same relation bytes."""
    top = _yosys_top(arguments)
    state = _yosys_reducer("state_ownership")
    data = _yosys_reducer("clock_data")
    before = _strict_json(Path("/work/state-before.json").read_bytes(),
                          "elaborated state before clock lowering")
    smt2 = payload.decode("utf-8")
    if source_state:
        after = _strict_json(Path("/work/state-after.json").read_bytes(),
                             "source storage after lowering")
        smt2 = state.source_state_smt2(before, after, smt2, top)
    return data.clock_data_smt2(before, smt2, top, storage_kind=state.storage_kind).encode("utf-8")


def _rtl_state_clock_ownership(payload: bytes, arguments: object) -> bytes:
    """Reduce flattened Yosys JSON to one closed state ownership record."""
    top = _yosys_top(arguments)
    parser = _yosys_reducer("state_ownership")
    document = parser.state_ownership_document(
        _strict_json(payload, "the flattened Yosys state document"),
        top)
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _verilator_coverage_points(payload: bytes, arguments: object) -> bytes:
    """Reduce Verilator's text format to closed, mergeable coverage points.

    A point identifier commits to the complete record except its hit count.  The
    verifier therefore can union batches without receiving source paths, hierarchy
    names, user comments, or any other free-form compiler output.  A source line is
    retained only for the one source mounted by this job.

    Coverage is diagnostic evidence, not a score gate.  Malformed records are ignored
    just as Verilator's old host-side reader ignored them; the returned document itself
    is nevertheless strict and closed.
    """

    if type(arguments) is not dict or arguments:
        raise ValueError("verilator-coverage-points/1 takes no arguments")
    # latin-1 is a total one-byte decoding.  Candidate-influenced names never leave
    # this process: they contribute only to a SHA-256 point identity.
    text = payload.decode("latin-1")
    lines = text.splitlines()
    field_re = re.compile(r"\x01([A-Za-z0-9_])\x02([^\x01']*)")
    source_re = re.compile(r"\x01f\x02(?:/work/)?rtl\.sv(?=\x01|$)")
    type_re = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
    points: dict[str, dict[str, object]] = {}
    for raw in lines:
        if not raw.startswith("C '"):
            continue
        quote = raw.rfind("'")
        if quote < 3:
            continue
        try:
            count = int(raw[quote + 1:].strip())
        except ValueError:
            continue
        body = raw[3:quote]
        fields = field_re.findall(body)
        values: dict[str, str] = {}
        duplicate = False
        for key, value in fields:
            if key in values:
                duplicate = True
                break
            values[key] = value
        if duplicate:
            continue
        raw_type = values.get("t")
        if not raw_type:
            continue
        point_type = raw_type if type_re.fullmatch(raw_type) else "other"
        source = values.get("f")
        line: int | None = None
        if source in {"rtl.sv", "/work/rtl.sv"}:
            try:
                candidate_line = int(values.get("l", ""))
            except ValueError:
                candidate_line = 0
            if 0 < candidate_line <= 0x7FFFFFFF:
                line = candidate_line
        normalized_body = source_re.sub("\x01f\x02rtl.sv", body)
        identifier = hashlib.sha256(
            normalized_body.encode("latin-1")).hexdigest()
        row = {
            "id": identifier,
            "type": point_type,
            "line": line,
            "covered": count > 0,
        }
        prior = points.get(identifier)
        if prior is None:
            points[identifier] = row
        elif prior["type"] == point_type and prior["line"] == line:
            prior["covered"] = bool(prior["covered"] or count > 0)
        else:
            # A cryptographic collision or inconsistent duplicate cannot certify a
            # point.  Dropping it is conservative for diagnostic coverage.
            points.pop(identifier, None)
    document = {
        "version": "verilator-coverage-points/1",
        "points": [points[key] for key in sorted(points)],
    }
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _normalize_output(kind: str, payload: bytes, arguments: object, *,
                      recursion_limit: int | None = None) -> bytes:
    if kind == "verilator-ports/1":
        return _verilator_ports(payload, arguments, recursion_limit=recursion_limit)
    if kind == "rtlil-sites/9":
        return _rtlil_sites(payload, arguments)
    if kind == "rtl-state-clock-ownership/1":
        return _rtl_state_clock_ownership(payload, arguments)
    if kind == "rtl-source-state/1":
        return _rtl_relation_metadata(payload, arguments, source_state=True)
    if kind == "rtl-clock-data/1":
        return _rtl_relation_metadata(payload, arguments)
    if kind == "verilator-coverage-points/1":
        return _verilator_coverage_points(payload, arguments)
    raise ValueError(f"unknown RTL output normalizer {kind!r}")


def _frame(hasher, payload: bytes) -> None:
    hasher.update(len(payload).to_bytes(8, "big"))
    hasher.update(payload)


def _tree_image(root: Path) -> tuple[bytes, str]:
    """A deterministic regular-file tree archive and its semantic digest."""

    digest = hashlib.sha256()
    _frame(digest, b"tree")
    entries: list[tuple[str, Path, os.stat_result]] = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in [*directories, *files]:
            path = current_path / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ValueError(f"declared output tree contains a symlink: {relative}")
            if path.is_dir():
                _frame(digest, b"dir")
                _frame(digest, relative.encode("utf-8"))
            elif path.is_file():
                payload = path.read_bytes()
                _frame(digest, b"file")
                _frame(digest, relative.encode("utf-8"))
                _frame(digest, b"executable" if info.st_mode & 0o111 else b"data")
                _frame(digest, payload)
            else:
                raise ValueError(
                    f"declared output tree contains a special file: {relative}")
            entries.append((relative, path, info))

    raw = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for relative, path, info in entries:
            row = tarfile.TarInfo(relative)
            row.uid = row.gid = 0
            row.uname = row.gname = ""
            row.mtime = 0
            if path.is_dir():
                row.type = tarfile.DIRTYPE
                row.mode = 0o755
                archive.addfile(row)
            else:
                payload = path.read_bytes()
                row.type = tarfile.REGTYPE
                row.mode = 0o755 if info.st_mode & 0o111 else 0o644
                row.size = len(payload)
                archive.addfile(row, io.BytesIO(payload))
    return raw.getvalue(), digest.hexdigest()


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class _CommandLog:
    """Keep a complete stream in parts below the guest's per-file limit."""

    def __init__(self, path: Path):
        self.path = path
        limit, _ = resource.getrlimit(resource.RLIMIT_FSIZE)
        self.limit = None if limit == resource.RLIM_INFINITY else limit
        if self.limit == 0:
            raise ValueError("command logs require a positive per-file size allowance")
        self.part = self.used = 0
        self.writer = path.open("wb")

    def write(self, data: bytes) -> None:
        remaining = memoryview(data)
        while remaining:
            if self.limit is not None and self.used == self.limit:
                self.writer.close()
                self.part += 1
                self.writer = self.path.with_name(
                    self.path.name + f".part-{self.part}").open("wb")
                self.used = 0
            count = (len(remaining) if self.limit is None
                     else min(len(remaining), self.limit - self.used))
            self.writer.write(remaining[:count])
            self.used += count
            remaining = remaining[count:]

    def close(self) -> None:
        self.writer.close()


def _command(row: object, log_name: str, cleanup_seconds: int) -> dict[str, object]:
    if type(row) is not dict or set(row) != {
        "id", "argv", "cwd", "environment", "timeout_s"
    }:
        raise ValueError("a tool command has an invalid shape")
    identifier = row["id"]
    argv = row["argv"]
    if type(identifier) is not str or not identifier:
        raise ValueError("a tool command needs an identifier")
    if (type(argv) is not list or not argv
            or any(type(item) is not str or "\x00" in item for item in argv)
            or not argv[0].startswith("/")):
        raise ValueError("tool argv must begin with an absolute guest executable")
    cwd = Path("/work") / _relative(row["cwd"], "command cwd")
    cwd.mkdir(parents=True, exist_ok=True)
    environment = row["environment"]
    if type(environment) is not dict or any(
        type(name) is not str or type(value) is not str
        for name, value in environment.items()
    ):
        raise ValueError("command environment must be a string mapping")
    timeout = row["timeout_s"]
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("command timeout must be a positive integer")

    deadline = time.monotonic() + timeout
    started = subprocess.Popen(
        argv,
        cwd=cwd,
        env={**os.environ, **environment},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    captured = {}
    timed_out = False
    failure = cleanup_error = None
    try:
        for name in ("stdout", "stderr"):
            captured[name] = _CommandLog(_LOG_ROOT / f"{log_name}.{name}")
        # A descendant may inherit a pipe after the leader exits. Nonblocking
        # drain shares the command deadline; closing a buffered reader in another
        # thread can instead wait forever on that thread's blocking read.
        with selectors.DefaultSelector() as poller:
            for name in captured:
                stream = getattr(started, name)
                os.set_blocking(stream.fileno(), False)
                poller.register(stream, selectors.EVENT_READ, name)
            while poller.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in poller.select(remaining):
                    chunk = os.read(key.fd, 64 << 10)
                    if not chunk:
                        poller.unregister(key.fileobj)
                        continue
                    captured[key.data].write(chunk)
        if not timed_out:
            # A command may close its pipes before exiting. Wait on the process
            # itself under the same deadline instead of polling an empty selector.
            try:
                started.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    except BaseException as error:
        failure = error
        raise
    finally:
        # Kill same-session leftovers even after normal leader exit. The enclosing
        # PID namespace owns descendants that created their own sessions.
        cleanup_deadline = time.monotonic() + cleanup_seconds
        cleanup_errors = []
        try:
            _kill_group(started)
            started.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
        except (OSError, subprocess.TimeoutExpired) as cleanup:
            cleanup_errors.append(f"{type(cleanup).__name__}: {cleanup}")
        for stream in (started.stdout, started.stderr, *captured.values()):
            try:
                stream.close()
            except OSError as cleanup:
                cleanup_errors.append(f"{type(cleanup).__name__}: {cleanup}")
        if cleanup_errors:
            detail = "; ".join(cleanup_errors)
            if failure is not None:
                failure.add_note(f"Tool command cleanup unconfirmed: {detail}")
            else:
                cleanup_error = detail
    result = {
        "id": identifier,
        "returncode": started.returncode,
        "timed_out": timed_out,
        "stdout_file": f"{log_name}.stdout",
        "stderr_file": f"{log_name}.stderr",
    }
    if cleanup_error is not None:
        result["cleanup_error"] = cleanup_error
    return result


def _group(row: object, group_index: int, cleanup_seconds: int) -> list[dict[str, object]]:
    if type(row) is not dict or set(row) != {
        "parallel", "commands", "when_executable_glob"
    }:
        raise ValueError("a command group has an invalid shape")
    parallel = row["parallel"]
    commands = row["commands"]
    condition = row["when_executable_glob"]
    if type(parallel) is not int or parallel <= 0:
        raise ValueError("group parallelism must be positive")
    if type(commands) is not list or not commands:
        raise ValueError("group commands must be a nonempty list")
    if condition is not None:
        condition = _relative(condition, "group condition")
        if not any(os.access(path, os.X_OK) for path in glob.glob(str(Path("/work") / condition))):
            return [{
                "id": command.get("id", "") if type(command) is dict else "",
                "skipped": "required executable was not built",
            } for command in commands]
    stopped = threading.Event()

    def execute(item):
        index, command = item
        if stopped.is_set():
            return {"id": command["id"], "skipped": "prior command cleanup unconfirmed"}
        try:
            result = _command(command, f"{group_index}-{index}", cleanup_seconds)
        except BaseException:
            stopped.set()
            raise
        if "cleanup_error" in result:
            stopped.set()
        return result

    if parallel == 1 or len(commands) < 2:
        return [execute(item) for item in enumerate(commands)]
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(parallel, len(commands))
    ) as pool:
        return list(pool.map(execute, enumerate(commands)))


def _output_limit(commands, name: str, used: int, limit: int) -> None:
    """Report resource exhaustion without publishing any partial payloads."""
    _write_json({
        "version": _PROTOCOL,
        "commands": commands,
        "output_limit": {
            "output": name, "limit_bytes": limit, "observed_bytes": used,
        },
    })


def _main() -> None:
    job = json.loads(Path("/input/job.json").read_text(encoding="utf-8"))
    if type(job) is not dict or set(job) != {
        "version", "groups", "outputs", "output_trees", "preserve_outputs",
        "normalizers", "protocol_bytes", "cleanup_seconds",
        "normalization_recursion_limit"
    } or job["version"] != _PROTOCOL:
        raise ValueError("the RTL tool job has an invalid shape or version")
    protocol_limit = job["protocol_bytes"]
    cleanup_seconds = job["cleanup_seconds"]
    if type(cleanup_seconds) is not int or cleanup_seconds <= 0:
        raise ValueError("tool cleanup budget must be a positive integer")
    recursion_limit = job["normalization_recursion_limit"]
    if type(recursion_limit) is not int or recursion_limit <= 0:
        raise ValueError("tool normalization recursion budget must be a positive integer")
    if protocol_limit is not None and (type(protocol_limit) is not int or protocol_limit <= 0):
        raise ValueError("tool output budgets must be positive integers")
    if (type(job["groups"]) is not list or type(job["outputs"]) is not list
            or type(job["output_trees"]) is not list
            or type(job["preserve_outputs"]) is not list
            or type(job["normalizers"]) is not list):
        raise ValueError("tool groups and outputs must be lists")

    output_names = [_relative(raw, "job output") for raw in job["outputs"]]
    if len(output_names) != len(set(output_names)):
        raise ValueError("job output paths must be unique")
    output_trees = [_relative(raw, "job output tree")
                    for raw in job["output_trees"]]
    if len(output_trees) != len(set(output_trees)) \
            or set(output_names) & set(output_trees):
        raise ValueError("job output files and trees must be unique and disjoint")
    preserved = {
        _relative(raw, "preserved job output")
        for raw in job["preserve_outputs"]
    }
    if len(preserved) != len(job["preserve_outputs"]) \
            or not preserved <= set(output_names):
        raise ValueError("preserved outputs must be unique declared outputs")
    normalizers = {}
    for row in job["normalizers"]:
        if type(row) is not dict or set(row) != {"output", "kind", "arguments"}:
            raise ValueError("an output normalizer has an invalid shape")
        output = _relative(row["output"], "normalizer output")
        if output not in output_names or output in normalizers \
                or type(row["kind"]) is not str or type(row["arguments"]) is not dict:
            raise ValueError("an output normalizer has an invalid declaration")
        normalizers[output] = (row["kind"], row["arguments"])

    source = Path("/input/tree")
    if source.exists():
        shutil.copytree(source, "/work", dirs_exist_ok=True, symlinks=False)
    # A declared output is the result of this invocation, never a file copied
    # from the input image.  Removing it before the first command makes a failed
    # compiler unable to certify stale output from an earlier run.
    for name in output_names:
        if name in preserved:
            continue
        path = Path("/work") / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    for name in output_trees:
        path = Path("/work") / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    commands = []
    for index, group in enumerate(job["groups"]):
        commands.extend(_group(group, index, cleanup_seconds))
        if any("cleanup_error" in row for row in commands):
            # Do not start another phase or publish files when a command's
            # lifetime is unconfirmed. Retain the execution ledger, not just
            # the secondary wait exception. The namespace owns final teardown.
            for later in job["groups"][index + 1:]:
                commands.extend({"id": row["id"], "skipped": "prior command cleanup unconfirmed"}
                                for row in later["commands"])
            _write_json({"version": _PROTOCOL, "commands": commands,
                         "cleanup_failed": True})
            return

    outputs: dict[str, str | None] = {}
    trees: dict[str, dict[str, str] | None] = {}
    used = 0
    for name in output_names:
        path = Path("/work") / name
        if not path.is_file() or path.is_symlink():
            outputs[name] = None
            continue
        payload = path.read_bytes()
        if name in normalizers:
            kind, arguments = normalizers[name]
            payload = _normalize_output(kind, payload, arguments,
                                        recursion_limit=recursion_limit)
        used += len(payload)
        if protocol_limit is not None and used > protocol_limit:
            _output_limit(commands, name, used, protocol_limit)
            return
        outputs[name] = _bytes(payload)
    for name in output_trees:
        path = Path("/work") / name
        if not path.is_dir() or path.is_symlink():
            trees[name] = None
            continue
        payload, digest = _tree_image(path)
        used += len(payload)
        if protocol_limit is not None and used > protocol_limit:
            _output_limit(commands, name, used, protocol_limit)
            return
        trees[name] = {"archive": _bytes(payload), "sha256": digest}
    _write_json({
        "version": _PROTOCOL,
        "commands": commands,
        "outputs": outputs,
        "trees": trees,
    })


if __name__ == "__main__":
    try:
        _main()
    except BaseException as error:  # the one protocol answer must name every failure
        _write_json({
            "version": _PROTOCOL,
            "error": f"{type(error).__name__}: {error}",
        })
        raise
