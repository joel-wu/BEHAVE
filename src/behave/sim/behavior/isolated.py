"""Behavior protocol hosted inside the declared OS isolation boundary.

Restricted builtins, guarded imports and source/runtime consistency checks remain
admission and fidelity defences.  Filesystem/process containment is instead owned by
``behave.isolation``: a minimal read-only mount profile, empty environment, denied
network namespace and PID-1 tree supervisor.  If that backend is unavailable this
module raises its typed capability refusal; it never falls back to a direct child.

The native reply is still an audit observation, not an authenticated source semantics.
Bounded conclusions come from admitted IR and retain frontend fidelity as an explicit
assumption.
"""

from __future__ import annotations

import io
import json
import math
import os
import select
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from behave.core import runtime_defaults as defaults
from behave.isolation import (
    Device,
    IsolationOutputLimit,
    IsolationSpec,
    IsolationUnavailable,
    ResourceLimits,
    SandboxProcess,
    WritableRoot,
    python_package,
    python_runtime,
    read_only,
    start,
)
from behave.sim.core.source_rules import EMPTY_SOURCE_RULES

from ..core.failure import Broken, CandidateFault, FailureRaised, RunFailure
from ..core.refusal import Refusal, Refused
from ..strict_json import strict_json_loads
from ..symbolic.native_values import DEFAULT_WIRE_NODES, NativeValueError, WireDecoder, WireEncoder

_MAX_PROTOCOL_BYTES = defaults.SIM_SOURCE_REPLY_BYTES
_TEST_WORKER_LAUNCHER = None

__all__ = [
    "IsolatedModel",
    "Limits",
    "facts_of",
    "hits_of",
    "isolated_model",
    "snapshot_of",
    "unsupported_of",
]


@contextmanager
def _testing_worker_launcher(launcher):
    """Explicit unit-test seam; production has no direct-launch fallback.

    The pytest configuration uses this only when its host demonstrably lacks the
    namespace capability.  It is a context-scoped callable rather than an environment
    switch, so deployed code cannot accidentally opt out of isolation.
    """
    global _TEST_WORKER_LAUNCHER
    previous = _TEST_WORKER_LAUNCHER
    _TEST_WORKER_LAUNCHER = launcher
    try:
        yield
    finally:
        _TEST_WORKER_LAUNCHER = previous


def _nested_job_launcher(child_program):
    """Start the model protocol below an already-established named job.

    A named job already runs inside the namespace/container boundary and owns the
    complete descendant process tree.  Attempting to create a second mount/user
    namespace from inside it is both unnecessary and unavailable on common kernels.
    The child remains a distinct interpreter (so submitted code cannot mutate the
    trusted lowering/solver process), while inheriting only the named job's scrubbed
    runtime paths and resource/container restrictions.

    Selection is guarded by the process-local :mod:`job_context` capability, never an
    environment switch a caller can forge.
    """

    environment = {
        name: os.environ[name]
        for name in (
            "PYTHONHOME", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE",
            "PYTHONHASHSEED", "TMPDIR",
        )
        if name in os.environ
    }
    # ``close_fds=False`` is safe only when every non-standard descriptor is
    # close-on-exec.  PEP 446 gives Python-created descriptors that property,
    # but native dependencies may open an inheritable descriptor.  Preserve
    # the parent's descriptor (it may belong to the solver), seal it at the
    # exec boundary, then verify the property we rely on.
    descriptors = []
    for name in os.listdir("/proc/self/fd"):
        try:
            descriptor = int(name)
        except ValueError:
            continue
        if descriptor > 2:
            descriptors.append(descriptor)
    for descriptor in descriptors:
        try:
            if os.get_inheritable(descriptor):
                os.set_inheritable(descriptor, False)
        except OSError:
            # The descriptor used to enumerate the directory may already be
            # closed by the time it appears in the returned name list.
            continue
    for descriptor in descriptors:
        try:
            inheritable = os.get_inheritable(descriptor)
        except OSError:
            continue
        if inheritable:
            raise RuntimeError(
                "the named job could not seal an ambient descriptor for exec")
    # Keep ``close_fds`` false deliberately.  The named-job target starts after
    # ``_linux_child._close_fds`` and Python creates later descriptors
    # non-inheritable, so the model receives only its three redirected standard
    # streams.  On the supported CPython/Linux runtime this also selects
    # ``posix_spawn``.  The default ``close_fds=True`` selects fork+exec: if a
    # bounded SAT query has already built a multi-gigabyte Z3 heap, the outer
    # aggregate-RSS supervisor observes that heap twice between fork and exec
    # and correctly kills the job even though the model child itself is tiny.
    # This child needs a new interpreter, not a copy of the solver process.
    return subprocess.Popen(
        [sys.executable, "-S", "-u", "-c", child_program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        close_fds=False,
    )


class Limits:
    """What one model's process is allowed, and for how long."""

    __slots__ = ("memory_bytes", "processes", "seconds", "wall_seconds")

    def __init__(self, seconds=defaults.SIM_SOURCE_CPU_SECONDS,
                 memory_bytes=defaults.SIM_SOURCE_RSS_BYTES,
                 wall_seconds=defaults.SIM_SOURCE_WALL_SECONDS,
                 processes=defaults.SIM_SOURCE_PROCESSES):
        #: CPU seconds per answer. A scoped session separately inherits its
        #: owner's remaining lifetime; standalone sessions keep this total cap.
        self.seconds = seconds
        #: Address space. An allocation past it fails as a `MemoryError` in the child,
        #: which is the model failing rather than the verifier dying.
        self.memory_bytes = memory_bytes
        #: Standalone reply/session wall limit. Under a scoring/search/analysis
        #: owner, transport waits share that owner's deadline instead. Host GC,
        #: decoding and pipe backpressure are not model CPU execution; the
        #: independently enforced per-answer CPU limit above remains in force.
        self.wall_seconds = wall_seconds
        self.processes = processes

    def as_dict(self):
        return {"cpu_seconds": self.seconds, "memory_bytes": self.memory_bytes,
                "wall_seconds": self.wall_seconds, "processes": self.processes}


def _session_wall_seconds(reply_seconds):
    """Reuse the owner's remaining lifetime; otherwise use the standalone cap."""
    from ..execution_control import remaining_timeout

    remaining = remaining_timeout(math.inf)
    return math.ceil(remaining) if math.isfinite(remaining) else reply_seconds


def _session_cpu_seconds(reply_seconds):
    """Bound total CPU by the owner, independently of each answer's allowance."""
    from ..execution_control import remaining_timeout

    remaining = remaining_timeout(math.inf)
    return max(1, math.ceil(remaining)) if math.isfinite(remaining) else reply_seconds


#: The child, as source, because it has to run before this package is importable in it
#: and because what it is allowed to do is easier to read in one piece than assembled
#: from a module it imports.
_CHILD = r'''
import json, math, os, resource, struct, sys

def _limit(cpu, session_cpu, mem, procs):
    # The namespace supervisor already owns the hard tree limit.  This local
    # defence may lower a limit but must never try to raise the supervisor's.
    _soft, _hard = resource.getrlimit(resource.RLIMIT_CPU)
    _hard = session_cpu if _hard == resource.RLIM_INFINITY else min(session_cpu, _hard)
    resource.setrlimit(resource.RLIMIT_CPU, (min(cpu, _hard), _hard))
    if mem:
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    # Zero bytes of file. A reachable `open` can still open, and can write nothing:
    # the write raises rather than the file appearing, which is what the escape did.
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (procs, procs))
    except (ValueError, OSError):
        pass

def _answer_cpu_budget(cpu):
    # RLIMIT_CPU is absolute process usage, not a duration from this call.
    # Only the soft limit moves; the owner-installed hard limit never does.
    usage = resource.getrusage(resource.RUSAGE_SELF)
    _soft, _hard = resource.getrlimit(resource.RLIMIT_CPU)
    deadline = math.ceil(usage.ru_utime + usage.ru_stime + cpu)
    if _hard != resource.RLIM_INFINITY:
        deadline = min(deadline, _hard)
    resource.setrlimit(resource.RLIMIT_CPU, (deadline, _hard))

_WRITE = set("wxa+")


def _refuse(event, args):
    # What an rlimit cannot see. The hook cannot be removed once installed and fires
    # before the operation rather than reporting it afterwards.
    if event in ("socket.socket", "socket.connect", "socket.bind",
                 "os.exec", "os.posix_spawn", "subprocess.Popen",
                 "os.system", "os.fork", "ctypes.dlopen", "ctypes.dlsym"):
        raise PermissionError(f"the model attempted {event}")
    if event == "open":
        # Opening to write, which the file-size limit alone does not stop. Measured:
        # with `RLIMIT_FSIZE` at zero the write fails and the file is still created
        # empty, and the first reading of that measurement was wrong because
        # `exists()` is true of a nought-byte file. An empty file appearing where a
        # model ran is still a model changing something outside itself, so the open is
        # refused and the limit stays as the second line under it.
        mode = args[1] if len(args) > 1 else ""
        if isinstance(mode, str) and set(mode) & _WRITE:
            raise PermissionError(f"the model attempted to open {args[0]!r} to write")
        if isinstance(mode, int) and mode & (os.O_WRONLY | os.O_RDWR
                                             | os.O_CREAT | os.O_APPEND):
            raise PermissionError(f"the model attempted to open {args[0]!r} to write")

def main():
    first = json.loads(sys.stdin.readline())
    _limit(first["cpu"], first["session_cpu"], first["memory"], first["processes"])
    sys.addaudithook(_refuse)
    # The model's own prints must not reach the protocol channel.
    out, sys.stdout = sys.stdout, sys.stderr
    sys.path[:0] = first["path"]

    def reply(**kw):
        out.write(json.dumps(kw) + "\n")
        out.flush()

    # **The recorded dependencies, executed from their own bytes, before the model.**
    #
    # Putting the materialised directory first on the path is not enough and was not:
    # `instrumented.load_source` calls `_allow_approved_imports`, which inserts the
    # verifier's own library at `sys.path[0]` -- ahead of it. So a bundle carrying its
    # own `fp_lib.py` had that file's digest in its identity and the central library's
    # code in its run. Measured with a helper returning 123: the run returned the central
    # library's answer.
    #
    # Registering them in `sys.modules` settles it wherever the path search would have
    # gone. What may be imported at all is unchanged -- the guarded `__import__` in the
    # model's namespace still admits only the approved names -- and this decides only
    # which bytes those names resolve to.
    import importlib.util

    for name, where in dict(first.get("recorded") or {}).items():
        spec = importlib.util.spec_from_file_location(name, where)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

    from behave.sim.behavior import instrumented
    from behave.core import runtime_defaults as _runtime_defaults
    _image_nodes = _runtime_defaults.SIM_SOURCE_NAMESPACE_NODES
    from behave.sim.behavior.native_values import (
        DEFAULT_WIRE_NODES as _CARRIER_NODES,
        NativeValueError as _NativeValueError,
        NativeValueLimit as _NativeValueLimit,
        ValueWireEncoder as _ValueWireEncoder,
        WireDecoder as _WireDecoder,
        WireEncoder as _WireEncoder,
    )
    from behave.sim.core.failure import CandidateFault as _CandidateFault
    from behave.sim.core.refusal import Refusal as _Refusal
    from behave.sim.behavior import extract as _extract

    try:
        _sm = _extract.source_model(first["source"], file=first["file"],
                                    cls=first.get("cls"),
                                    keyword_calls=True)
    except _extract.LoweringError as _error:
        if _error.code not in ("model-class-not-found", "step-method-missing"):
            raise
        # A slot reference is a concrete scoring capability, not a Behavior-IR
        # procedure.  It may therefore expose ``process_slot`` without pretending that
        # one accepted transaction is the whole step.  Exact Behavior analysis remains
        # unavailable for this shape until it has a corresponding IR frontend.
        _sm = _extract.source_model(first["source"], file=first["file"],
                                    cls=first.get("cls"), step="process_slot")
    if first["clock_domains"] is not None and None not in _sm.steps:
        _sm = _sm.for_domains(first["clock_domains"])
    from behave.sim.behavior.source_plan import plan_source as _plan_source
    from behave.sim.core.source_rules import SourceGoalRules as _SourceGoalRules

    _source_resolver = _extract.ScopeResolver(_sm)
    _goal_plan = _plan_source(
        _sm, first["source"], first["file"], resolver=_source_resolver,
        source_rules=_SourceGoalRules.from_dict(first["source_rules"]))
    import functools as _functools
    from behave.sim.behavior.namespace_metadata import (
        abc_registry as _abc_registry, annotation_caches as _annotation_caches,
        public_module_exports as _public_module_exports)

    _trusted_annotation_caches = _annotation_caches()
    _library_exports = _public_module_exports()
    try:
        namespace, _sites, hits = instrumented.load_source(
            first["source"], first["file"], _attribute_startup_failure=True,
            goal_plan=_goal_plan)
    except _CandidateFault as error:
        reply(startup_failure=error.refusal.as_dict())
        return
    cls = instrumented._model_class(namespace, _sm.cls.name)

    # **The certified subset is plain instance-backed fields on a frozen
    # class over a frozen module** (reviews 38-39). A class __dict__
    # descriptor forges what object.__getattribute__ returns;
    # __getattribute__/__getattr__/__setattr__/__delattr__ reroute reads and
    # writes; __slots__ moves fields out of the dict; a class-level data
    # descriptor makes a field read as whatever it likes; and none of it is
    # an invariant unless it is re-checked, since reset can install a
    # descriptor, swap a method for one that diverges only off-audit, or
    # mutate a module table the lowering folded as constant. So the class
    # tables are fingerprinted **before the constructor runs** and checked
    # after construction and after every call; the module images are
    # checked through construction, reset and the witness always, and after
    # every step too under strict mode (the certified audit and the
    # witness), while a reference adapter -- whose models may keep module
    # state across steps by design -- opts out of the per-step module check
    # alone (review 46). Descriptor rules are enforced on the frozen tables
    # with exact interpreter types, never with names a candidate can
    # spell.
    import ast as _ast
    import types as _pytypes

    def _plain_tables(klass):
        """Read plain storage classes without invoking instance attribute hooks."""
        tables = []
        for base in klass.__mro__:
            if base is object:
                continue
            table = dict(vars(base))
            for name in ("__getattribute__", "__getattr__", "__setattr__",
                         "__delattr__", "__slots__"):
                if name in table:
                    raise TypeError(f"the model class defines {name}")
            for name, value in table.items():
                if name in ("__dict__", "__weakref__"):
                    if type(value) not in (_pytypes.GetSetDescriptorType,
                                           _pytypes.MemberDescriptorType):
                        raise TypeError(f"the model class redefines {name}")
                    continue
                if type(value) is property:
                    continue  # Source-bound accessors are frozen below.
                if (hasattr(type(value), "__set__")
                        or hasattr(type(value), "__delete__")):
                    raise TypeError(
                        f"the model class defines a data descriptor {name}")
            tables.append(table)
        return tables

    # The mode decides what gets frozen at all (review 48: the module
    # image was built before the flag was read, so a reference model whose
    # module holds one opaque object was Broken at startup for a check
    # reference mode never makes).
    strict_module = bool(first.get("strict_module", True))
    snapshot_policy = first.get("snapshot_policy", "eager")
    if snapshot_policy not in ("eager", "on-demand"):
        raise ValueError("unknown isolated snapshot policy")

    _frozen_mro = cls.__mro__
    _frozen_tables = _plain_tables(cls)

    _kept_refs = []

    def _image_context():
        return {"budget": _image_nodes, "memo": {}, "active": set(),
                "nodes": []}

    def _data_image(value, depth=0, keep=True, context=None):
        """A structural image of a frozen binding, for change detection.

        Identity alone is a shallow freeze (review 40: a function object
        kept its identity while its __code__ was swapped), so a function's
        image carries its code, defaults, keyword defaults, closure cells
        and attribute dict; a class carries its MRO and its whole table; a
        module carries its bindings. A value with no image rule cannot be
        frozen, and refusing it is the point.
        """
        context = _image_context() if context is None else context
        context["budget"] -= 1
        if context["budget"] < 0:
            raise TypeError("the frozen namespace exceeds its node budget")
        if depth > 24:
            raise TypeError("a frozen binding nests too deeply to image")
        kind = type(value)
        if value is None or kind in (bool, int, str, bytes):
            return ("v", value)
        if kind is float:
            # ``repr`` identifies every NaN as ``nan`` and therefore misses
            # changes to its sign or payload.  The construction carrier and
            # the audit compare floats by their binary64 image; the namespace
            # invariant must use that same exact relation.
            return ("f", struct.pack("<d", value).hex())
        if kind is bytearray:
            return ("ba", bytes(value))
        identity = id(value)
        if identity in context["memo"]:
            memo_value, memo_image = context["memo"][identity]
            if memo_value is value:
                return memo_image
        # Images form a flat object graph.  Returning a recursively nested
        # tuple here looks compact, but comparing two images of a shared DAG
        # revisits the same child once per path and is exponential.  Reserve a
        # node before visiting its children and return only its reference;
        # the enclosing namespace image carries the node table once.
        reference = ("ref", len(context["nodes"]))
        context["nodes"].append(None)
        context["memo"][identity] = (value, reference)
        context["active"].add(identity)
        try:
            def child(item):
                return _data_image(
                    item, depth + 1, keep, context=context)

            def multiset(items):
                counts = {}
                for item in items:
                    counts[item] = counts.get(item, 0) + 1
                return frozenset(counts.items())

            if kind in (list, tuple):
                image = (kind.__name__, tuple(child(v) for v in value))
            elif kind in (set, frozenset):
                image = (kind.__name__, multiset(
                    child(v) for v in value))
            elif kind in (dict, _pytypes.MappingProxyType):
                image = (kind.__name__, multiset(
                    (child(k), child(v)) for k, v in value.items()))
            elif kind is _pytypes.GenericAlias:
                image = ("generic-alias", child(value.__origin__), child(value.__args__))
            elif kind is _pytypes.UnionType:
                image = ("union", child(value.__args__))
            elif kind is _functools._lru_cache_wrapper and value in _trusted_annotation_caches:
                image = ("annotation-cache", identity, child(value.__dict__),
                         child(value.cache_parameters()))
            elif kind in (staticmethod, classmethod):
                image = ("wrap", kind.__name__, child(value.__func__))
            elif kind is property:
                image = ("property", child(value.fget), child(value.fset), child(value.fdel))
            elif kind is _pytypes.FunctionType:
                if keep:
                    _kept_refs.append(value)
                    _kept_refs.append(value.__code__)
                cells = tuple(
                    (id(cell), child(cell.cell_contents))
                    for cell in (value.__closure__ or ()))
                image = (
                    "fn", id(value), id(value.__code__),
                    tuple(child(d) for d in (value.__defaults__ or ())),
                    child(value.__kwdefaults__ or {}), cells,
                    child(dict(value.__dict__)))
            elif kind in (_pytypes.BuiltinFunctionType,
                          _pytypes.BuiltinMethodType):
                if keep:
                    _kept_refs.append(value)
                image = ("bfn", id(value))
            elif kind in (_pytypes.MethodDescriptorType,
                          _pytypes.WrapperDescriptorType,
                          _pytypes.ClassMethodDescriptorType):
                # Native descriptors have immutable implementations. The owning
                # class/module binding is still imaged independently, so replacing
                # a descriptor changes the namespace image.
                if keep:
                    _kept_refs.append(value)
                image = ("native-descriptor", kind.__name__, id(value))
            elif isinstance(value, type):
                if keep:
                    _kept_refs.append(value)

                def member(name, entry):
                    if type(entry) in (_pytypes.GetSetDescriptorType,
                                       _pytypes.MemberDescriptorType):
                        return ("auto",)
                    if name == "_abc_impl":
                        registry = _abc_registry(value, entry)
                        if registry is not None:
                            return registry
                    return child(entry)

                table = tuple(
                    (name, member(name, entry))
                    for name, entry in sorted(vars(value).items())
                    if name not in ("__doc__",))
                image = ("cls", id(value),
                         tuple(id(base) for base in value.__mro__), table)
            elif kind is _pytypes.ModuleType:
                if keep:
                    _kept_refs.append(value)
                exports = _library_exports.get(value)
                image = ("mod", id(value), tuple(
                    (name, child(entry))
                    for name, entry in sorted(vars(value).items())
                    if (name in exports if exports is not None else not name.startswith("__"))))
            elif type(kind) is type and all(
                    vars(base).get("__new__", object.__new__) is object.__new__
                    for base in kind.__mro__):
                # Image Python allocation storage directly, without invoking
                # user hooks. A slot is read through its native descriptor and
                # keyed by declaring class, so shadowed slots remain distinct.
                fields = {}
                for base in kind.__mro__:
                    for name, descriptor in vars(base).items():
                        if name == "__dict__":
                            if type(descriptor) is not _pytypes.GetSetDescriptorType:
                                raise TypeError("a frozen object redefines its field dictionary")
                            fields["dict"] = descriptor.__get__(value, kind)
                        elif type(descriptor) is _pytypes.MemberDescriptorType:
                            try:
                                fields[(id(base), name)] = descriptor.__get__(value, kind)
                            except AttributeError:
                                pass  # An absent slot differs from any populated value.
                if keep:
                    _kept_refs.append(value)
                image = ("object", identity, child(kind), child(fields))
            else:
                raise TypeError(
                    f"a frozen binding holds a {kind.__name__}, which has "
                    "no image")
        finally:
            context["active"].discard(identity)
        # Hold the object as well as its id for the life of this image.  Some
        # children above are temporary dictionaries; retaining them prevents
        # CPython from reusing an id for a different object in the same pass.
        context["nodes"][reference[1]] = image
        return reference

    # The module names the lowering may fold: every top-level binding of the
    # source. Rebinding one, or mutating the data value it holds, changes
    # semantics the IR baked in (review 39: TABLE[0] = 9 in reset).
    # Every top-level binding, whatever binds it (review 40: def, class and
    # import were outside the universe, so f.__code__ = evil.__code__ went
    # unseen). A binding the imager refuses makes the whole construction
    # unverifiable, which is a refusal downstream.
    _module_names = set()
    for _node in _ast.parse(first["source"]).body if strict_module else ():
        if isinstance(_node, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                              _ast.ClassDef)):
            _module_names.add(_node.name)
        elif isinstance(_node, _ast.Import):
            for _alias in _node.names:
                _module_names.add(_alias.asname
                                  or _alias.name.split(".")[0])
        elif isinstance(_node, _ast.ImportFrom):
            for _alias in _node.names:
                if _alias.name == "*":
                    # Observe the actual imported namespace, including a captured
                    # library's public constants; '*' itself is not a binding.
                    _module_names.update(name for name in namespace if not name.startswith("_"))
                else:
                    _module_names.add(_alias.asname or _alias.name)
        else:
            for _sub in _ast.walk(_node):
                if isinstance(_sub, _ast.Name) and isinstance(_sub.ctx,
                                                              _ast.Store):
                    _module_names.add(_sub.id)
    _frozen_ns_keys = frozenset(namespace) if strict_module else frozenset()
    def _module_image(keep):
        """One flat image for all source-owned module bindings.

        A single graph preserves sharing between different bindings as well
        as within one binding.  Its flat node table also makes comparison
        linear in the number of reachable objects rather than in the number
        of paths through a shared DAG.
        """
        context = _image_context()
        roots = tuple(
            (name, _data_image(namespace[name], keep=keep, context=context))
            for name in sorted(_module_names) if name in namespace)
        return roots, tuple(context["nodes"])

    _frozen_module = _module_image(True)
    # The execution environment itself is semantics the lowering bakes in
    # (review 41: __builtins__["range"] swapped for a two-element list):
    # the guarded builtins table by entry identity, and the injected hook
    # bindings by identity -- their closures mutate by design (the hit
    # list), so they are not imaged deeper.
    if strict_module:
        _kept_refs.append(namespace.get("__builtins__"))
        _frozen_builtins = ("bins", tuple(sorted(
            (name, id(value))
            for name, value in dict(namespace.get("__builtins__")
                                    or {}).items())))
        _frozen_extra = {}
        for _extra in sorted(set(namespace) - set(_module_names)):
            if _extra == "__builtins__":
                continue
            _kept_refs.append(namespace[_extra])
            _frozen_extra[_extra] = id(namespace[_extra])
    else:
        _frozen_builtins = None
        _frozen_extra = {}

    def _invalid_runtime(why):
        from behave.sim.core.failure import CandidateFault as _CandidateFault
        from behave.sim.core.refusal import Refusal as _Refusal
        raise _CandidateFault(_Refusal("behavior-runtime-invariant", why))

    def _invariants(check_module=True, full=True):
        if type(model) is not cls:
            _invalid_runtime("the model changed its class")
        if not full:
            # reference mode (review 47): a reference model may keep class
            # and module state across steps by design; the proxy-safety
            # checks stay, the lowering-specific immutability does not
            return
        if cls.__mro__ != _frozen_mro:
            _invalid_runtime("the model class changed its bases")
        live = [dict(vars(k)) for k in cls.__mro__ if k is not object]
        for frozen, now in zip(_frozen_tables, live):
            if set(frozen) != set(now):
                _invalid_runtime("the model class table changed")
            for name, value in frozen.items():
                if now[name] is not value:
                    _invalid_runtime("the model class table changed")
        if not check_module:
            return
        if set(namespace) != _frozen_ns_keys:
            _invalid_runtime("the module bound new names")
        if _module_image(False) != _frozen_module:
            _invalid_runtime("the module bindings changed")
        now_builtins = ("bins", tuple(sorted(
            (k, id(v))
            for k, v in dict(namespace.get("__builtins__") or {}).items())))
        if now_builtins != _frozen_builtins:
            _invalid_runtime("the module builtins changed")
        if any(id(namespace.get(name)) != identity
               for name, identity in _frozen_extra.items()):
            _invalid_runtime("an injected module binding changed")

    def _raw_fields(check_module=True, full=True):
        """The instance's plain field table, read past every hook, on the
        re-verified frozen class.

        The class-table and module images are lowering semantics: the fold
        bakes both in, so certified mode (strict_module) re-checks them on
        every reply, key set included. A reference model is not lowered and
        may keep class or module state across steps by design (reviews 45
        and 47: the isolation test's accumulating model, `M.COUNT += a`),
        so reference mode keeps only the proxy-safety checks -- the
        pre-construction descriptor guard, the instance's exact type, and
        this dict-exactness read."""
        _invariants(check_module, full)
        raw = object.__getattribute__(model, "__dict__")
        if type(raw) is not dict:
            raise TypeError("the instance dict is not a dict")
        return raw

    config = dict(first.get("config") or {})
    try:
        model = cls(**config)
    except BaseException as error:
        reply(startup_failure=_Refusal(
            "candidate-construction-failed",
            "the candidate constructor raised before replay",
            **instrumented.source_failure_detail(error, first["file"])).as_dict())
        return
    try:
        _invariants(full=strict_module)
    except _CandidateFault as error:
        reply(runtime_failure=error.refusal.as_dict())
        return

    # **What the model can be asked, reported once.** The proxy in the parent serves the
    # model's attributes, and a method is an attribute: without this list a model that
    # defines `tick` looked to the parent like one that does not, and the reference
    # adapter's `getattr(model, "tick", None)` then advanced it on no slot at all. The
    # names are read off the object here, where the object is.
    CALLS = ("reset", "process", "process_slot", "tick", "start", "step")
    offers = [name for name in CALLS if callable(getattr(model, name, None))]

    # **What the parent would otherwise import the model to learn.** The class's name,
    # the parameters of `process`, and the per-domain handlers of a multi-clock model are
    # all read by `cosim` today, and reading them means executing the agent's module in
    # the verifier's process -- at import, before anything has decided whether this task
    # can be verified at all. They are facts about the source, so they are read here,
    # where the source already runs under limits and an audit hook.
    # **The facts come from the one class the lowering is about, not from a
    # namespace scan.** `source_model` chooses the model class and validates
    # its shape (single receiver named self in first position, no exotic
    # parameters); reading the facts off anything else lets an aliased empty
    # `Decoy = M` be picked and generated against with the wrong inputs
    # (review 50). `type(model).__name__` is mutable and `inspect.signature`
    # is forgeable, so neither is consulted; the binding name and each
    # step's parameters are the source's (reviews 49-50).
    _cls_name = _sm.cls.name
    import keyword as _keyword
    if not (type(_cls_name) is str and _cls_name.isidentifier()
            and not _keyword.iskeyword(_cls_name)):
        raise ValueError("the model class has no plain source binding name")
    # The chosen class must still be the runtime object the audit runs, and
    # its binding must not have been rebound at module level after the def
    # (`Decoy = M`, `M = something`): the runtime class carries the source
    # class's qualified name only when it is that class.
    if namespace.get(_cls_name) is not cls \
            or getattr(cls, "__qualname__", None) != _cls_name:
        raise ValueError(
            f"the class binding {_cls_name} does not name the model class "
            f"the lowering chose")

    from behave.sim.behavior.method_binding import (
        PROPERTY_ACCESSORS, class_member_name, descriptor_function, method_kind, method_parameters)

    _bare = _sm.steps.get(None)
    facts = {"cls_name": _cls_name,
             "process_inputs": (list(method_parameters(_bare))
                                if _bare is not None else []),
             "process_optional_inputs": (list(_sm.optional_inputs)
                                         if _bare is not None else []),
             "process_kwargs": _bare is not None and _bare.args.kwarg is not None,
             "domains": {}}
    for _domain, _step in _sm.steps.items():
        # ``SourceModel.steps`` is the authority for whether a handler is a
        # clock-domain step.  In particular, the concrete-reference fallback
        # parses ``process_slot`` as the one bare step; spelling alone must not
        # turn it into a fictitious domain named ``slot`` in only this copy of
        # the greeting metadata.
        if _domain is not None:
            facts["domains"][_domain] = {
                "method": _step.name,
                "inputs": list(method_parameters(_step))}

    _selected_step_names = frozenset(
        step.name for step in _sm.steps.values())

    def _check_selected_step_bindings():
        """The instance must still dispatch every selected source step.

        Python gives an instance field precedence over the non-data function
        descriptor on its class.  Without this check, ``self.process = f``
        makes native replay call ``f`` while lowering continues to translate
        the class body's ``process``.  Check after construction and after
        every reply (in particular after reset), using the guarded raw table
        rather than candidate attribute machinery.
        """
        raw = object.__getattribute__(model, "__dict__")
        if type(raw) is not dict:
            raise TypeError("the instance dict is not a dict")
        shadowed = sorted(_selected_step_names & set(raw))
        if shadowed:
            raise TypeError(
                "instance fields shadow selected source steps: "
                + ", ".join(shadowed))

    # A module-level statement can rebind a method after its class body ran
    # (`M.process = evil`), so the class the audit runs differs from the
    # source the lowering reads. Each method in the final parsed registry
    # must still be, at runtime, a function compiled *as* that method:
    # its code object's qualified name is set at compile time, is read-only,
    # and instrumentation preserves it, so a plain module function assigned
    # over the method (qualname "evil") is caught while a genuine method
    # (qualname "M.process") passes (review 49). Comparing bytecode fails
    # here because load_source instruments the tree.
    # The parser already resolves later class-body assignments/deletions.
    # Re-scanning every def would check dead bindings, e.g. a __repr__ def
    # followed by a lambda assignment, against the replacement Python runs.
    _methods = (list(_sm.steps.values()) + list(_sm.helpers.values())
                + [m for m in (_sm.reset, _sm.constructor) if m is not None])
    for _m in _methods:
        _runtime = cls.__dict__.get(class_member_name(_cls_name, _m.name))
        _function = descriptor_function(_runtime)
        _kind = method_kind(_m)
        if _kind in PROPERTY_ACCESSORS:
            _function = (getattr(_runtime, PROPERTY_ACCESSORS[_kind])
                         if type(_runtime) is property else None)
        _descriptor_ok = (type(_runtime) is staticmethod if _kind == "staticmethod"
                          else type(_runtime) is classmethod if _kind == "classmethod"
                          else type(_runtime) is property if _kind in PROPERTY_ACCESSORS
                          else type(_runtime) is _pytypes.FunctionType)
        _code = getattr(_function, "__code__", None)
        want = f"{_cls_name}.{_m.name}"
        _property_line = (min(node.lineno for node in (_m, *_m.decorator_list))
                          if _kind in PROPERTY_ACCESSORS else None)
        if not _descriptor_ok \
                or getattr(_code, "co_qualname", None) != want \
                or (_property_line is not None
                    and getattr(_code, "co_firstlineno", None) != _property_line):
            raise ValueError(
                f"the class method {_m.name} is not the function its class "
                f"body defines, so the audit and the lowering are not the "
                f"same code")

    # The runtime class must define *only* what its source body binds
    # (review 51: `M.__init__ = Evil.__init__` added a constructor the
    # source has none of, whose instance field shadowed a method the IR
    # still inlined). Every own member is either a name the class body
    # binds or one of the dunders Python fills in for any class.
    _src_bound = {class_member_name(_cls_name, name)
                  for name in _extract.class_bound_names(_sm.cls)}
    from behave.sim.behavior.class_binding import DATACLASS_HOOK as _DATACLASS_HOOK

    _generated = namespace[_DATACLASS_HOOK].tables.get(cls)
    if _generated is not None:
        if set(vars(cls)) != set(_generated) or any(
                vars(cls)[name] is not value for name, value in _generated.items()):
            raise ValueError("the dataclass bindings changed after the standard decorator ran")
        _src_bound = set(_generated)
    _auto = {"__module__", "__qualname__", "__doc__", "__dict__",
             "__weakref__", "__firstlineno__", "__static_attributes__",
             "__annotations__"}
    _extra = set(cls.__dict__) - _src_bound - _auto
    if _extra:
        raise ValueError(
            f"the runtime class carries members its source body does not "
            f"define ({', '.join(sorted(_extra))}), so it is not the class "
            f"the lowering reads")
    # And every name the source binds must still be there (review 51: a
    # missing member means the runtime class is not the source class); a
    # source class constant must still hold its source value (a module-level
    # `M.K = 2` over a body `K = 1` would leave the audit reading 2 while the
    # lowering folds 1).
    _missing = _src_bound - set(cls.__dict__)
    if _missing:
        raise ValueError(
            f"the runtime class is missing members its source body defines "
            f"({', '.join(sorted(_missing))})")
    # Consume the same owner- and program-point-aware declaration authority
    # as the lowerer.  A second class-constant evaluator would otherwise be
    # able to accept a runtime value different from the one folded into IR.
    _src_class_consts = _source_resolver.class_consts
    _rom_names = tuple(sorted(_source_resolver.tables))

    def _record_safety_failure(error):
        """Append the public goal of the native operation that actually failed."""

        goal = instrumented.attribute_safety_failure(
            error, _goal_plan.safety_sites)
        if goal is not None:
            hits.append(goal)
        return goal

    facts["rom_names"] = list(_rom_names)
    from behave.sim.behavior.admission import (
        calls_this_build_will_not_answer as _unanswerable_calls)
    facts["unanswerable_calls"] = _unanswerable_calls(first["source"])
    _owners = {}
    if len(_sm.steps) >= 2 and None not in _sm.steps:
        _owners = {field: set() for field in _sm.state_contract()}
        for _domain, _method in _sm.steps.items():
            for _field in _sm.reachable_state_writes([_method]):
                _owners.setdefault(_field, set()).add(_domain)
    facts["ownership"] = {
        field: sorted(domains) for field, domains in sorted(_owners.items())}
    for _name, _value in _src_class_consts.items():
        _runtime_v = cls.__dict__.get(class_member_name(_cls_name, _name))
        if type(_runtime_v) is not type(_value) or _runtime_v != _value:
            raise ValueError(
                f"the class constant {_name} is {_runtime_v!r} at runtime "
                f"but {_value} in the source it was lowered from")

    def snapshot(check_module=True, full=True):
        """What a step can carry, and for the rest, why it cannot.

        Both halves cross the channel, because the parent has no other way to tell an
        attribute the model does not have from one it has and this cannot represent. A
        Fixed arrays are recursive values: a nested list of integer leaves is
        carried without flattening, so its shape remains available to lowering.

        Two more halves for the audit (review 25): `carried` holds every
        attribute that has a faithful typed encoding, and `opaque` names the
        type of every one that does not, so the parent can fail closed on a
        contract field it cannot read instead of taking absence for agreement.
        The `state`/`why` pair is unchanged: it is the step-value channel the
        initial-state reader consumes, and it carries integers and recursively
        nested integer arrays.
        """
        _check_selected_step_bindings()
        _not_step_value = object()

        step_budget = [_CARRIER_NODES]

        def step_value(value, path=None):
            if step_budget[0] is not None:
                step_budget[0] -= 1
                if step_budget[0] < 0:
                    raise ValueError(
                        "the construction step value exceeds its node budget")
            if type(value) is int:
                return value
            if type(value) in (list, tuple):
                path = set() if path is None else path
                identity = id(value)
                if identity in path:
                    return _not_step_value
                path.add(identity)
                try:
                    parts = [step_value(item, path) for item in value]
                    if all(item is not _not_step_value for item in parts):
                        return parts
                finally:
                    path.discard(identity)
            return _not_step_value

        got, why, carried, opaque, memories = {}, {}, {}, {}, {}
        carry_encoder = _WireEncoder(_CARRIER_NODES)
        memory_fields = {
            interface.name for interface, _run in _memory_configuration}
        for name, value in _raw_fields(check_module, full).items():
            if name in memory_fields:
                from behave.sim.behavior.reference_memory import (
                    ReferenceMemory as _ReferenceMemory,
                    _audit_image as _memory_audit_image,
                )
                if type(value) is _ReferenceMemory:
                    memories[name] = _memory_audit_image(value)
                    continue
            try:
                enc = carry_encoder.encode(value)
            except _NativeValueLimit as exc:
                raise ValueError(str(exc)) from None
            except _NativeValueError:
                opaque[name] = type(value).__name__
            else:
                carried[name] = enc
            step = step_value(value)
            if isinstance(value, bool):
                why[name] = "a boolean, which is not a value a step carries"
            elif step is not _not_step_value:
                got[name] = step
            else:
                why[name] = type(value).__name__
        return got, why, carried, opaque, memories

    def _share():
        """Each field's reachable mutable objects, by reference identity.

        The construction has really run, so the object graph is the ground
        truth about sharing (review 36): two fields reaching one mutable
        node -- a list, set, dict or bytearray, by exact type -- are two
        names for one carrier, whatever source shape built them. Immutable
        containers are traversed but carry no identity of their own; a node
        outside the closed set makes the field opaque, since its graph
        cannot be walked faithfully.
        """
        budget = [_CARRIER_NODES]

        def walk(value, acc, path):
            if budget[0] is not None:
                budget[0] -= 1
                if budget[0] < 0:
                    raise ValueError(
                        "the construction object graph exceeds its node budget")
            kind = type(value)
            if value is None or kind in (bool, int, float, str, bytes):
                return True
            identity = id(value)
            if identity in path:
                # SolverIR arrays are finite trees, so a source cycle has no
                # faithful value representation.
                return False
            if kind in (list, set, dict, bytearray):
                # Two paths to one mutable node are observable sharing even
                # inside a single field.  Repeated immutable tuples are safe;
                # traverse them again so a mutable descendant, if any, is
                # still detected here.
                if identity in acc:
                    return False
                acc.add(identity)
            if kind is bytearray:
                return True
            if kind not in (list, tuple, set, frozenset, dict):
                return False
            path.add(identity)
            try:
                if kind is dict:
                    return all(
                        walk(key, acc, path) and walk(item, acc, path)
                        for key, item in value.items())
                return all(walk(item, acc, path) for item in value)
            finally:
                path.discard(identity)
        # One field-reading semantics for every consumer (review 38): the
        # guarded plain table, never the candidate's attribute protocol.
        field_report = {}
        module_report = {}

        def row(value):
            acc, path = set(), set()
            ok = walk(value, acc, path)
            return {"ids": sorted(acc), "opaque": not ok}

        for name, value in _raw_fields(
                strict_module, strict_module).items():
            field_report[name] = row(value)
        for name in _rom_names:
            module_report[name] = row(namespace[name])
        return {"fields": field_report, "modules": module_report}

    _atomic = None
    _memory_configuration = ()
    _memory_representation = "reference"

    def _apply_external_memory():
        """Install a fresh run's stimulus-owned memory image, once at binding."""

        if not _memory_configuration:
            return
        raw = _raw_fields(strict_module, strict_module)
        for interface, run in _memory_configuration:
            if _memory_representation == "reference":
                from behave.sim.behavior.reference_memory import ReferenceMemory

                raw[interface.name] = ReferenceMemory(interface, run)
            else:
                from behave.sim.core.memory import memory_junk_reader

                sparse = dict(run.initial)
                default = memory_junk_reader(interface.name, interface.data_width)
                raw[interface.name] = [
                    sparse[address] if address in sparse else default(address)
                    for address in range(interface.depth)
                ]

    def _configure_external_memory(value):
        nonlocal _memory_configuration, _memory_representation
        if type(value) is not dict or set(value) != {
                "interfaces", "environment", "horizon", "representation"}:
            raise ValueError("an external-memory binding has its complete fields")
        if type(value["interfaces"]) is not list \
                or type(value["horizon"]) is not int \
                or value["horizon"] < 0:
            raise ValueError("an external-memory binding has finite typed fields")
        from behave.sim.core.memory import MemoryEnvironment, MemoryInterface

        interfaces = tuple(
            MemoryInterface.from_dict(row) for row in value["interfaces"])
        environment = MemoryEnvironment.from_dict(value["environment"])
        representation = value["representation"]
        if representation not in ("reference", "array"):
            raise ValueError(
                "an external-memory binding names reference or array semantics")
        faults = environment.faults_against(interfaces, value["horizon"])
        if faults:
            raise ValueError(faults[0])
        _memory_configuration = tuple(
            (interface, environment.run(interface.name))
            for interface in interfaces)
        _memory_representation = representation
        _apply_external_memory()

    def _atomic_answer(operation):
        """Only attributed source faults may use the candidate-fault channel."""
        from behave.sim.core.failure import CandidateFault as _CandidateFault
        from behave.sim.core.refusal import Refused as _Refused
        try:
            return {"status": "ok", "value": operation()}
        except _CandidateFault as exc:
            return {"status": "candidate-fault", "refusal": exc.refusal.as_dict()}
        except _Refused as exc:
            return {"status": "refused", "refusal": exc.refusal.as_dict()}

    def _model_call(method, /, *args, **kwargs):
        """Only source callbacks may publish a native exception as their result."""
        try:
            # Method lookup belongs to the submitted object too. A missing
            # reset/handler is not a failure of the replay service transport.
            return getattr(model, method)(*args, **kwargs), ""
        except BaseException as error:
            _record_safety_failure(error)
            return None, type(error).__name__

    try:
        state, why, carried, opaque, memories = snapshot(
            strict_module, strict_module)
    except _CandidateFault as error:
        reply(runtime_failure=error.refusal.as_dict())
        return
    reply(ok=True, state=state, why=why, carried=carried, opaque=opaque,
          memories=memories, hits=[], offers=offers, facts=facts)
    for line in sys.stdin:
        _answer_cpu_budget(first["cpu"])
        try:
            call = json.loads(line)
        except ValueError:
            break
        what = call.get("call")
        try:
            if what == "reset":
                del hits[:]
                from behave.sim.behavior.reference_memory import preserve_external_memory

                with preserve_external_memory(
                        _raw_fields(strict_module, strict_module),
                        (interface.name for interface, _ in _memory_configuration)):
                    produced, raised = _model_call("reset")
            elif what == "process":
                produced, raised = _model_call("process", **call["inputs"])
            elif what == "configure_external_memory":
                try:
                    _configure_external_memory(call.get("binding"))
                except BaseException as error:
                    produced = {
                        "status": "failed",
                        "error": type(error).__name__,
                        "why": str(error),
                    }
                else:
                    produced = {"status": "ok"}
                raised = ""
            elif what == "share":
                produced, raised = _share(), ""
            elif what == "_observe_state":
                produced, raised = None, ""
            elif what == "atomic_init":
                def initialise_atomic():
                    nonlocal _atomic
                    from behave.sim.behavior.multiclock import (
                        DomainOwnership as _DomainOwnership,
                        NativeAtomicMachine as _NativeAtomicMachine,
                    )
                    ownership = _DomainOwnership.from_record(call["ownership"])
                    domains = call["domains"]
                    if (type(domains) is not list
                            or any(type(domain) is not str or not domain
                                   for domain in domains)
                            or domains != sorted(set(domains))):
                        raise ValueError(
                            "atomic domains are sorted unique nonempty strings")
                    source_domains = sorted(facts["domains"])
                    if domains != source_domains:
                        raise ValueError(
                            "atomic domains disagree with the selected source class")
                    _atomic = _NativeAtomicMachine(
                        cls, ownership, domains, model=model,
                        external_fields=tuple(interface.name for interface, _ in _memory_configuration),
                        hit_log=hits,
                        failure_attribution=_record_safety_failure,
                        invariant=lambda: _invariants(
                            check_module=strict_module,
                            full=strict_module))
                    return dict(_atomic.kernel.state)
                produced, raised = _atomic_answer(initialise_atomic), ""
            elif what == "atomic_init_source":
                def initialise_atomic_from_source():
                    """Derive ownership from the same selected source the child runs.

                    A frozen reference bundle deliberately exposes source *digests*,
                    not bytes a caller can re-interpret.  Atomic replay nevertheless
                    needs the source-owned write partition.  Deriving it here binds the
                    partition to the selected class and bytes already checked by this
                    isolated worker instead of asking the verifier to reopen a path.
                    """
                    nonlocal _atomic
                    from behave.sim.behavior.multiclock import (
                        DomainOwnership as _DomainOwnership,
                        NativeAtomicMachine as _NativeAtomicMachine,
                    )
                    domains = call["domains"]
                    if (type(domains) is not list
                            or any(type(domain) is not str or not domain
                                   for domain in domains)
                            or domains != sorted(set(domains))):
                        raise ValueError(
                            "atomic domains are sorted unique nonempty strings")
                    selected_model = _sm.for_domains(domains)
                    owners = {field: set() for field in selected_model.state_contract()}
                    for domain, method in selected_model.steps.items():
                        for field in selected_model.reachable_state_writes([method]):
                            owners.setdefault(field, set()).add(domain)
                    ownership = _DomainOwnership(owners)
                    _atomic = _NativeAtomicMachine(
                        cls, ownership, domains, model=model,
                        external_fields=tuple(interface.name for interface, _ in _memory_configuration),
                        hit_log=hits,
                        failure_attribution=_record_safety_failure,
                        invariant=lambda: _invariants(
                            check_module=strict_module,
                            full=strict_module))
                    return {
                        "state": dict(_atomic.kernel.state),
                        "ownership": ownership.as_record(),
                    }
                produced, raised = _atomic_answer(
                    initialise_atomic_from_source), ""
            elif what == "atomic_ready":
                def atomic_ready():
                    if _atomic is None:
                        raise ValueError("the atomic machine is not initialised")
                    return _atomic.ready(call["domain"])
                produced, raised = _atomic_answer(atomic_ready), ""
            elif what == "atomic_ready_observation":
                def atomic_ready_observation():
                    if _atomic is None:
                        raise ValueError("the atomic machine is not initialised")
                    ready, ready_hits = _atomic.ready_observation(call["domain"])
                    return {"ready": ready, "hits": list(ready_hits)}
                produced, raised = _atomic_answer(
                    atomic_ready_observation), ""
            elif what == "atomic_step":
                def atomic_step():
                    if _atomic is None:
                        raise ValueError("the atomic machine is not initialised")
                    from behave.sim.behavior.multiclock import (
                        DomainInvocation as _DomainInvocation,
                        _isolated_transition_record as _transition_record,
                    )
                    from behave.sim.core.clock_schedule import (
                        GlobalEventSlot as _GlobalEventSlot,
                    )
                    request = _WireDecoder(_CARRIER_NODES).decode(call["request"])
                    if (type(request) is not dict
                            or set(request) != {"slot", "invocations"}
                            or type(request["invocations"]) is not list):
                        raise ValueError("malformed atomic-step request")
                    slot = _GlobalEventSlot.from_dict(request["slot"])
                    invocations = tuple(
                        _DomainInvocation.from_record(row)
                        for row in request["invocations"])
                    return _transition_record(
                        _atomic.step(slot, invocations))
                produced, raised = _atomic_answer(atomic_step), ""
            elif what in offers:
                # Anything else the model itself offers, with the arguments the caller
                # sent. `tick()` and `start(state)` are the reference adapter's; a call
                # the model does not define is still refused below rather than invented.
                produced, raised = _model_call(what,
                    *list(call.get("args") or ()),
                    **dict(call.get("inputs") or {}))
            else:
                raise ValueError(f"no such call {what}")
        except _CandidateFault as error:
            reply(runtime_failure=error.refusal.as_dict())
            return
        except BaseException as error:
            # Request decoding, environment preparation and service bookkeeping
            # are not candidate execution. End this service without publishing a
            # forged source exception; the parent records a broken replay.
            raise RuntimeError("isolated replay service failed") from error
        # The persistent atomic machine has its own exact, versioned state
        # protocol.  Its steady-state replies carry only readiness or a
        # complete transition patch; publishing the generic model snapshot as
        # well would serialize large memories once or more on every slot.
        atomic_observation = what in {
            "atomic_ready", "atomic_ready_observation", "atomic_step"}
        atomic_call = atomic_observation or what in {"atomic_init", "atomic_init_source"}
        atomic_failed = atomic_call and produced["status"] != "ok"
        publish_snapshot = not (atomic_observation or atomic_failed) and (
            snapshot_policy == "eager" or what in {
                "reset", "configure_external_memory", "share", "_observe_state",
                "atomic_init", "atomic_init_source"})
        observation = {}
        try:
            if publish_snapshot:
                state, why, carried, opaque, memories = snapshot(
                    check_module=strict_module, full=strict_module)
                reply_hits = sorted(set(hits))
            else:
                # Omitting a redundant state image does not omit the guards or
                # the events produced by an ordinary model call.
                # An atomic failure is already attributed by its execution boundary.
                # Rechecking the rejected state would overwrite that original fault;
                # the failure reply publishes neither a state image nor a transition.
                if not atomic_failed:
                    _check_selected_step_bindings()
                    fields = _raw_fields(strict_module, strict_module)
                    if not atomic_observation:
                        # Attribute presence needs only the live field names. Reading
                        # an absent optional method must not serialize every RAM cell.
                        observation["fields"] = list(fields)
                state, why, carried, opaque, memories, reply_hits = (
                    {}, {}, {}, {}, {}, [] if atomic_call else sorted(set(hits)))
        except _CandidateFault as error:
            # Guard failures belong to the submitted model, not the service.
            # Publish no result/state/hits from the rejected call and terminate.
            reply(runtime_failure=error.refusal.as_dict())
            return
        reply(ok=True,
              # A process result is a task-level value, not persistent Python
              # state.  Repeated references therefore denote repeated equal values;
              # object identity is not part of an endpoint transaction.
              produced=_ValueWireEncoder(_CARRIER_NODES).encode(produced),
              raised=raised, state=state,
              why=why, carried=carried, opaque=opaque,
              memories=memories, hits=reply_hits, **observation)
        # Consumers observe a hit set per call, not a visit count or visit order.
        # The parent appends each call's set separately: an occurrence reached
        # again in a later step must still be reported in that later step.
        del hits[:]

main()
'''


class Timeout(Exception):
    """The child did not answer inside its wall clock."""


_SOURCE_FACTS_VERSION = "behavior-source-facts/14"


def _source_protocol_facts(source, file, cls, source_rules=EMPTY_SOURCE_RULES,
                           clock_domains=None):
    """Protocol metadata derived without executing candidate code.

    A reply written by the process that runs the candidate cannot authenticate
    facts about that candidate.  Class selection, signatures, domains and ROM
    names are syntax facts, so a named frontend judge derives them from the shared
    authority.  The verifier accepts a candidate-process greeting only when it agrees
    with that closed judge record.
    """
    from .extract import LoweringError, ScopeResolver, source_model
    from .method_binding import method_parameters
    from .source_syntax import parsed_source

    tree = parsed_source(source, file)
    try:
        model = source_model(
            tree, file=file, cls=cls, keyword_calls=True)
    except LoweringError as error:
        if (error.code not in
                ("model-class-not-found", "step-method-missing")):
            raise
        model = source_model(
            tree, file=file, cls=cls, step="process_slot")
    if clock_domains is not None and None not in model.steps:
        model = model.for_domains(clock_domains)
    bare = model.steps.get(None)
    domains = {}
    for domain, step in model.steps.items():
        if domain is not None:
            domains[domain] = {
                "method": step.name,
                "inputs": list(method_parameters(step)),
            }
    resolver = ScopeResolver(model)
    facts = {
        "cls_name": model.cls.name,
        "process_inputs": ([] if bare is None else
                           list(method_parameters(bare))),
        "process_optional_inputs": ([] if bare is None else list(model.optional_inputs)),
        "process_kwargs": bare is not None and bare.args.kwarg is not None,
        "domains": domains,
        "rom_names": sorted(resolver.tables),
        "unanswerable_calls": [],
        "ownership": {},
    }
    from .admission import calls_this_build_will_not_answer

    facts["unanswerable_calls"] = calls_this_build_will_not_answer(source)
    if len(model.steps) >= 2 and None not in model.steps:
        owners = {field: set() for field in model.state_contract()}
        for domain, method in model.steps.items():
            for field in model.reachable_state_writes([method]):
                owners.setdefault(field, set()).add(domain)
        facts["ownership"] = {
            field: sorted(domains) for field, domains in sorted(owners.items())}
    methods = ({step.name for step in model.steps.values()}
               | set(model.helpers))
    if model.reset is not None:
        methods.add(model.reset.name)
    offered = tuple(name for name in (
        "reset", "process", "process_slot", "tick", "start", "step")
        if name in methods)
    from .source_plan import plan_source

    frontend = plan_source(model, source, file, resolver=resolver, source_rules=source_rules).manifest
    return facts, offered, frontend


def _source_protocol_image(source, file="behavior.py", cls=None, source_rules=EMPTY_SOURCE_RULES,
                           clock_domains=None):
    """The worker-owned closed image of source-derived protocol metadata."""

    if type(source) is not str or type(file) is not str or not file \
            or cls is not None and (type(cls) is not str or not cls):
        raise TypeError(
            "Behavior source facts need source text, a file name and an optional class")
    from ..core.failure import CandidateFault

    try:
        facts, offered, frontend = _source_protocol_facts(
            source, file, cls, source_rules, clock_domains)
    except CandidateFault as exc:
        return {"version": _SOURCE_FACTS_VERSION, "source_failure": exc.refusal.as_dict()}
    domains = {
        name: {
            "method": row["method"],
            "inputs": list(row["inputs"]),
        }
        for name, row in sorted(dict(facts["domains"]).items())
    }
    return {
        "version": _SOURCE_FACTS_VERSION,
        "facts": {
            "cls_name": facts["cls_name"],
            "process_inputs": list(facts["process_inputs"]),
            "process_optional_inputs": list(facts["process_optional_inputs"]),
            "process_kwargs": facts["process_kwargs"],
            "domains": domains,
            "rom_names": sorted(facts["rom_names"]),
            "unanswerable_calls": list(facts["unanswerable_calls"]),
            "ownership": {
                field: list(domains)
                for field, domains in sorted(facts["ownership"].items())
            },
        },
        "offers": list(offered),
        "frontend_catalogue": frontend.as_dict(),
    }


def _decode_source_protocol_image(value):
    if type(value) is dict and set(value) == {"version", "source_failure"} \
            and value["version"] == _SOURCE_FACTS_VERSION:
        from ..core.failure import CandidateFault
        from ..core.refusal import Refusal

        failed = value["source_failure"]
        if type(failed) is not dict or set(failed) != {"kind", "why", "detail"} \
                or failed["kind"] not in ("behavior-source-syntax-error", "behavior-entry-method-missing") \
                or type(failed["why"]) is not str or not failed["why"] \
                or type(failed["detail"]) is not dict:
            raise ValueError("the source judge returned an invalid source failure")
        raise CandidateFault(Refusal(failed["kind"], failed["why"], **failed["detail"]))
    if type(value) is not dict or set(value) != {
            "version", "facts", "offers", "frontend_catalogue"} \
            or value.get("version") != _SOURCE_FACTS_VERSION:
        raise ValueError("an isolated Behavior source-facts reply has a closed version")
    facts, offers = value["facts"], value["offers"]
    if type(facts) is not dict or set(facts) != {
            "cls_name", "process_inputs", "process_optional_inputs", "process_kwargs", "domains", "rom_names",
            "unanswerable_calls", "ownership"} \
            or type(offers) is not list:
        raise TypeError("an isolated Behavior source-facts reply is complete")

    def names(items, what, *, sorted_unique=False):
        if type(items) is not list or any(
                type(name) is not str or not name for name in items):
            raise TypeError(f"Behavior source facts {what} are nonempty strings")
        got = tuple(items)
        if len(set(got)) != len(got) \
                or sorted_unique and got != tuple(sorted(got)):
            raise ValueError(
                f"Behavior source facts {what} are distinct"
                + (" and sorted" if sorted_unique else ""))
        return got

    cls_name = facts["cls_name"]
    if type(cls_name) is not str or not cls_name.isidentifier():
        raise TypeError("a Behavior source-facts class is one identifier")
    process_inputs = names(facts["process_inputs"], "process inputs")
    optional_inputs = names(facts["process_optional_inputs"], "optional process inputs")
    if not set(optional_inputs) <= set(process_inputs):
        raise ValueError("optional process inputs belong to the declared signature")
    if type(facts["process_kwargs"]) is not bool:
        raise TypeError("Behavior source facts keyword acceptance is Boolean")
    rom_names = names(facts["rom_names"], "ROM names", sorted_unique=True)
    unanswerable = names(
        facts["unanswerable_calls"], "unanswerable calls", sorted_unique=True)
    offered = names(offers, "offered methods")
    domains = facts["domains"]
    if type(domains) is not dict or list(domains) != sorted(domains) \
            or any(type(name) is not str or not name for name in domains):
        raise TypeError("Behavior source-facts domains are a sorted string mapping")
    decoded_domains = {}
    for name, row in domains.items():
        if type(row) is not dict or set(row) != {"method", "inputs"} \
                or type(row["method"]) is not str or not row["method"]:
            raise TypeError("a Behavior domain fact has one method and its inputs")
        decoded_domains[name] = {
            "method": row["method"],
            "inputs": list(names(row["inputs"], f"{name} inputs")),
        }
    ownership = facts["ownership"]
    if type(ownership) is not dict or list(ownership) != sorted(ownership) \
            or any(type(field) is not str or not field for field in ownership):
        raise TypeError(
            "Behavior source-facts ownership is a sorted string mapping")
    decoded_ownership = {}
    for field, domains_for_field in ownership.items():
        owned = names(
            domains_for_field, f"owners of {field}", sorted_unique=True)
        if not set(owned) <= set(decoded_domains):
            raise ValueError(
                "Behavior source-facts ownership names no declared domain")
        decoded_ownership[field] = list(owned)
    decoded = {
        "cls_name": cls_name,
        "process_inputs": list(process_inputs),
        "process_optional_inputs": list(optional_inputs),
        "process_kwargs": facts["process_kwargs"],
        "domains": decoded_domains,
        "rom_names": list(rom_names),
        "unanswerable_calls": list(unanswerable),
        "ownership": decoded_ownership,
    }
    from ..core.catalogue import CatalogueManifest

    frontend = CatalogueManifest.from_dict(value["frontend_catalogue"])
    canonical = {
        "version": _SOURCE_FACTS_VERSION,
        "facts": decoded,
        "offers": list(offered),
        "frontend_catalogue": frontend.as_dict(),
    }
    if canonical != value:
        raise ValueError("an isolated Behavior source-facts reply is not canonical")
    return decoded, offered, frontend


@lru_cache(maxsize=defaults.SIM_SOURCE_FACTS_CACHE_ENTRIES)
def _isolated_source_protocol_facts(source, file, cls, source_rules=EMPTY_SOURCE_RULES,
                                    clock_domains=None):
    """One cached judge answer for exact source bytes and class selection."""

    from ..worker import WorkerFailed, run_job_strict

    try:
        raw = run_job_strict(
            "behavior_source_facts",
            {"source": source, "file": file, "class": cls,
             "source_rules": source_rules.as_dict(), "clock_domains": clock_domains},
            timeout_s=defaults.SIM_SOURCE_FACTS_SECONDS, label="isolated Behavior source facts")
    except WorkerFailed as exc:
        raise Broken(
            f"the isolated Behavior source parser did not answer: {exc}",
            code=exc.code, detail=exc.detail) from None
    return _decode_source_protocol_image(raw)


def _trusted_source_protocol_facts(source, file, cls, source_rules=EMPTY_SOURCE_RULES,
                                   clock_domains=None):
    """Obtain source facts without parsing submitted Python in the verifier.

    A named job is the judge boundary.  When already inside such a job, the trusted
    parser may run locally; nesting another namespace has no semantic benefit and may
    be unavailable under the outer job's process policy.
    """

    from ..job_context import inside_named_job

    if inside_named_job():
        return _decode_source_protocol_image(
            _source_protocol_image(source, file, cls, source_rules, clock_domains))
    return _isolated_source_protocol_facts(source, file, cls, source_rules, clock_domains)


class IsolatedModel:
    """A proxy whose model calls execute in an isolated process.

    Calls carry new hit events. State snapshots are eager by default; reference
    execution requests them on observation instead of retransmitting RAM per step.
    Attribute reads and audits always refresh an invalidated snapshot.
    Source exceptions and service failures cross distinct validated boundaries."""

    def __init__(self, source, file="behavior.py", cls=None, limits=None,
                 path=(), config=None, recorded=None, strict_module=True,
                 snapshot_policy="eager", source_rules=EMPTY_SOURCE_RULES,
                 clock_domains=None):
        # Every attribute of this object is underscored, and that is not a style choice.
        # A model's own attributes are served by `__getattr__`, so any public name here
        # shadows a model attribute of the same name. The snapshot was called `.state`,
        # and 33 models in the population have a state variable called `state`, which is
        # what a state machine calls its state: `getattr(model, "state")` returned this
        # object's snapshot dict instead of the model's value, the lowering compared that
        # against what folding read, and 33 models were refused for a disagreement
        # between a number and a dictionary.
        #
        # What the parent needs is read through the module functions below, which take
        # the proxy and reach into `__dict__`. Then no name of mine can collide with a
        # name of the model's, whatever a future model calls its fields.
        if snapshot_policy not in ("eager", "on-demand"):
            raise ValueError("unknown isolated snapshot policy")
        self._snapshot_policy = snapshot_policy
        self._snapshot_valid = True
        self._limits = limits or Limits()
        self._hits: list = []
        self._state: dict = {}
        self._unsupported: dict = {}
        self._carried: dict = {}
        self._opaque: dict = {}
        self._memory_bindings: dict = {}
        self._memory_values: dict = {}
        self._offers: tuple = ()
        self._facts: dict = {}
        self._atomic_domains: tuple = ()
        self._source = source
        self._child = None
        trusted_facts, trusted_offers, _trusted_catalogue = \
            _trusted_source_protocol_facts(
            source, file, cls, source_rules, clock_domains)
        self._source_catalogue = _trusted_catalogue
        session_cpu = _session_cpu_seconds(self._limits.seconds)
        direct_test_launcher = _TEST_WORKER_LAUNCHER
        if direct_test_launcher is None:
            from ..job_context import inside_named_job

            if inside_named_job():
                direct_test_launcher = _nested_job_launcher
        if direct_test_launcher is None:
            binaries, runtime, masked = python_runtime()
            from behave.isolation.runtime_environment import runtime_environment

            with runtime_environment().activate():
                package = python_package(
                    str(Path(__file__).resolve().parents[2]),
                    "/runtime/behave/behave")
            mounted_paths = []
            path_mounts = []
            for index, where in enumerate(path or ()):
                if not where:
                    continue
                guest = f"/inputs/path/{index}"
                path_mounts.append(read_only(str(where), guest))
                mounted_paths.append(guest)
            recorded_guest = {}
            recorded_mounts = []
            for index, (name, where) in enumerate(
                    sorted(dict(recorded or {}).items())):
                guest = f"/inputs/recorded/{index}/{Path(where).name}"
                recorded_mounts.append(read_only(str(where), guest))
                recorded_guest[str(name)] = guest
            guest_python = binaries[0].target
            isolation_limits = ResourceLimits(
                cpu_seconds=session_cpu,
                rss_bytes=self._limits.memory_bytes,
                processes=self._limits.processes,
                file_bytes=defaults.SIM_SOURCE_FILE_BYTES,
                open_files=defaults.SIM_SOURCE_OPEN_FILES,
                stdout_bytes=defaults.SIM_SOURCE_STDOUT_BYTES,
                stderr_bytes=defaults.SIM_SOURCE_STDERR_BYTES,
                protocol_bytes=defaults.SIM_SOURCE_PROTOCOL_BYTES,
                wall_seconds=_session_wall_seconds(self._limits.wall_seconds),
            )
            spec = IsolationSpec(
                argv=(guest_python, "-S", "-u", "-c", _CHILD),
                binaries=binaries,
                read_only=(*runtime, package, *path_mounts, *recorded_mounts),
                writable=(WritableRoot("/work", defaults.SIM_SOURCE_WORK_BYTES),),
                masked=masked,
                devices=(Device("/dev/urandom", "/dev/urandom"),),
                environment=(
                    ("PYTHONHOME", "/runtime/python"),
                    ("PYTHONPATH", "/runtime/behave"),
                    ("PYTHONDONTWRITEBYTECODE", "1"),
                    ("PYTHONHASHSEED", "0"),
                ),
                limits=isolation_limits,
            )
        try:
            if direct_test_launcher is None:
                self._child = start(spec, text=True)
                protocol_paths = mounted_paths + ["/runtime/behave"]
                protocol_recorded = recorded_guest
            else:
                self._child = direct_test_launcher(_CHILD)
                protocol_paths = [str(p) for p in path if p]
                protocol_recorded = {
                    str(name): str(where)
                    for name, where in dict(recorded or {}).items()
                }
            import threading

            self._stderr_text = ""
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr, daemon=True)
            self._stderr_thread.start()
            # **``path`` first.** A caller that materialised the model's recorded
            # dependencies into a directory of its own needs them found *there* rather
            # than wherever a same-named module happens to sit in the parent's path --
            # otherwise the digest is over one `fp_lib.py` and another one executes.
            self._say({"source": source, "file": file, "cls": cls,
                       "source_rules": source_rules.as_dict(),
                       "clock_domains": clock_domains,
                       "config": dict(config or {}),
                       "strict_module": bool(strict_module),
                       "snapshot_policy": snapshot_policy,
                       # `module name -> file`, executed from those bytes before the
                       # model loads. Path order alone would put the verifier's own
                       # library first, whatever the caller recorded.
                       "recorded": protocol_recorded,
                       "cpu": self._limits.seconds,
                       "session_cpu": session_cpu,
                       "memory": self._limits.memory_bytes,
                       "processes": self._limits.processes,
                       "path": protocol_paths})
            self._accept_greeting(
                self._hear(startup=True), facts=trusted_facts, offers=trusted_offers)
        except BaseException as error:
            # JSON conversion and greeting validation are part of construction.
            # Once Popen succeeds, every exit from this constructor must reap it.
            self._end_child()
            if isinstance(error, IsolationUnavailable):
                reason = error.refusal
                raise FailureRaised(RunFailure.infrastructure(
                    "behavior-worker-launch",
                    Refusal(reason.code, reason.why, **dict(reason.detail)))) from None
            raise

    # ── the protocol ──

    def _drain_stderr(self):
        """Drain the bounded diagnostic channel so a writer cannot block the protocol."""
        child = self.__dict__.get("_child")
        if child is None or child.stderr is None:
            return
        try:
            self._stderr_text = child.stderr.read()
        except (IsolationOutputLimit, OSError, UnicodeError, ValueError):
            pass

    def _say(self, message):
        from ..execution_control import check_execution

        check_execution()
        child = self.__dict__.get("_child")
        if child is None:
            raise Broken("the isolated model is closed")
        self._pending_call = message.get('call', 'startup')
        try:
            child.stdin.write(json.dumps(message) + "\n")
            child.stdin.flush()
        except (AttributeError, BrokenPipeError, IsolationOutputLimit,
                OSError, ValueError):
            raise self._broken_transport() from None

    def _hear(self, *, startup=False):
        line = self._read_with_deadline()
        if not line:
            failure = self._broken_transport()
            if failure.code == "isolation-timed-out":
                # The session supervisor can close the pipe at the owner
                # deadline before poll itself times out. Only that confirmed
                # wall expiry can be a normal scoped stop; CPU/OOM/unknown EOF
                # retain their actual failure even when the owner also expired.
                from ..execution_control import check_execution

                try:
                    check_execution()
                except BaseException as interruption:  # noqa: BLE001 - close the owned process before unwinding
                    self._fail(interruption)
            self._fail(failure)
        try:
            got = strict_json_loads(line, what="the isolated-model reply")
        except (UnicodeError, ValueError, TypeError, OverflowError,
                RecursionError):
            self._malformed_reply("malformed isolated-model reply")
        if startup and type(got) is dict and set(got) == {"startup_failure"}:
            return got  # The startup decoder validates this closed failure channel.
        if type(got) is dict and "runtime_failure" in got:
            record = got["runtime_failure"]
            if (set(got) != {"runtime_failure"}
                    or type(record) is not dict
                    or set(record) not in ({"kind", "why"}, {"kind", "why", "detail"})
                    or record["kind"] != "behavior-runtime-invariant"
                    or type(record["why"]) is not str or not record["why"].strip()
                    or type(record.get("detail", {})) is not dict
                    or record.get("detail", {})):
                self._malformed_reply("malformed isolated runtime failure")
            self._fail(CandidateFault(Refusal(record["kind"], record["why"])))
        if (type(got) is not dict or got.get("ok") is not True
                or type(got.get("state")) is not dict
                or type(got.get("why")) is not dict
                or type(got.get("carried")) is not dict
                or type(got.get("opaque")) is not dict
                or type(got.get("memories")) is not dict
                or type(got.get("hits")) is not list
                or any(type(hit) is not str for hit in got["hits"])):
            self._malformed_reply("malformed isolated-model reply")
        return got

    def _commit_reply(self, got, *, reset_hits=False):
        """Publish one fully validated reply as the proxy's new snapshot."""
        memory_values = {}
        bindings = self.__dict__.get("_memory_bindings") or {}
        from .reference_memory import _from_audit_image

        for name, image in got["memories"].items():
            binding = bindings.get(name)
            if binding is None or binding[1] != "reference":
                self._malformed_reply(
                    "an isolated reply carried an undeclared memory image")
            try:
                memory_values[name] = _from_audit_image(image, binding[0])
            except (TypeError, ValueError):
                self._malformed_reply(
                    "an isolated reply carried a malformed memory image")
        occupied = (set(got["state"]) | set(got["why"])
                    | set(got["carried"]) | set(got["opaque"]))
        if occupied & set(memory_values):
            self._malformed_reply(
                "an isolated memory has two state representations")
        state = dict(got["state"])
        state.update(memory_values)
        self._state = state
        self._unsupported = got["why"]
        self._carried = got["carried"]
        self._opaque = got["opaque"]
        self._memory_values = memory_values
        self._field_names = frozenset(occupied | set(memory_values))
        self._snapshot_valid = True
        hits = self.__dict__.get("_hits")
        if hits is None:
            self._hits = list(got["hits"])
        else:
            if reset_hits:
                hits.clear()
            hits.extend(got["hits"])

    def _accept_greeting(self, greeting, *, facts=None, offers=None):
        """Validate and atomically publish the one startup reply.

        ``facts`` and ``offers`` are the judge-derived source facts used in
        production.  The child must agree with them, but its copy is never an
        authority and is not published before the comparison succeeds.
        """
        if type(greeting) is dict and set(greeting) == {"startup_failure"}:
            from ..core.failure import CandidateFault
            from ..core.refusal import Refusal

            try:
                record = greeting["startup_failure"]
                if type(record) is not dict or set(record) != {
                        "kind", "why", "detail"}:
                    raise ValueError("malformed startup failure")
                if record["kind"] not in {
                        "candidate-load-failed", "candidate-construction-failed"}:
                    raise ValueError("unknown startup failure")
                detail = record["detail"]
                if (type(record["why"]) is not str or not record["why"].strip()
                        or type(detail) is not dict or set(detail) != {"error", "message", "file", "line"}
                        or type(detail["error"]) is not str or not detail["error"]
                        or type(detail["message"]) is not str
                        or type(detail["file"]) is not str or not detail["file"]
                        or (detail["line"] is not None and (
                            type(detail["line"]) is not int or detail["line"] < 1))):
                    raise ValueError("malformed startup failure")
                failure = Refusal(record["kind"], record["why"], **detail)
            except (TypeError, ValueError, KeyError):
                self._malformed_reply("malformed isolated-model startup failure")
            raise CandidateFault(failure)
        expected = {"ok", "state", "why", "carried", "opaque", "memories",
                    "hits", "offers", "facts"}
        if (type(greeting) is not dict or set(greeting) != expected
                or type(greeting.get("offers")) is not list
                or any(type(name) is not str
                       for name in greeting["offers"])
                or type(greeting.get("facts")) is not dict
                or "raised" in greeting or "produced" in greeting):
            self._malformed_reply("malformed isolated-model greeting")
        child_offers = tuple(greeting["offers"])
        child_facts = dict(greeting["facts"])
        if ((offers is not None and child_offers != tuple(offers))
                or (facts is not None and child_facts != facts)):
            self._malformed_reply(
                "the isolated greeting disagrees with the trusted source "
                "metadata")
        self._commit_reply(greeting, reset_hits=True)
        self._offers = child_offers if offers is None else tuple(offers)
        self._facts = child_facts if facts is None else dict(facts)
        return greeting

    def _accept_call_reply(self, got, *, result_shape=None, result_decoder=None,
                           publish_snapshot=True, publish_hits=False, reset_hits=False,
                           shape_error="malformed isolated-model call reply"):
        """Validate the complete reply before publishing its observations."""
        decoded = self._decode_call_reply(
            got, result_shape=result_shape, result_decoder=result_decoder,
            observe_fields=publish_hits and not publish_snapshot,
            shape_error=shape_error)
        self._publish_call_reply(decoded, publish_snapshot=publish_snapshot,
                                 publish_hits=publish_hits, reset_hits=reset_hits)
        return decoded

    def _decode_call_reply(self, got, *, result_shape=None, result_decoder=None,
                           observe_fields=False,
                           shape_error="malformed isolated-model call reply"):
        """Decode a reply without changing state, hits or attribute presence."""
        expected = {"ok", "state", "why", "carried", "opaque", "memories",
                    "hits", "produced", "raised"}
        if observe_fields:
            expected.add("fields")
        if (type(got) is not dict or set(got) != expected
                or type(got.get("raised")) is not str
                or "produced" not in got):
            self._malformed_reply("malformed isolated-model call reply")
        if observe_fields and (
                type(got["fields"]) is not list
                or any(type(name) is not str for name in got["fields"])
                or len(set(got["fields"])) != len(got["fields"])):
            self._malformed_reply("malformed isolated-model field names")
        try:
            produced = WireDecoder(DEFAULT_WIRE_NODES).decode(
                got["produced"])
        except NativeValueError:
            self._malformed_reply(
                "malformed isolated-model result encoding")
        decoded = dict(got)
        decoded["produced"] = produced
        if result_shape is not None and not result_shape(produced):
            self._malformed_reply(shape_error)
        if result_decoder is not None:
            try:
                decoded["produced"] = result_decoder(produced)
            except (NativeValueError, Refused, TypeError, ValueError,
                    KeyError, OverflowError, RecursionError):
                self._malformed_reply(shape_error)
        return decoded

    def _publish_call_reply(self, decoded, *, publish_snapshot,
                            publish_hits=False, reset_hits=False):
        if publish_snapshot:
            self._commit_reply(decoded, reset_hits=reset_hits)
        else:
            if any(decoded[name] for name in
                   ("state", "why", "carried", "opaque", "memories")) \
                    or (not publish_hits and decoded["hits"]):
                self._malformed_reply(
                    "an observation-only reply carried a generic snapshot")
            if publish_hits:
                self._field_names = frozenset(decoded["fields"])
                self._snapshot_valid = False
                self._hits.extend(decoded["hits"])

    def _accept_model_reply(self, got):
        return self._accept_call_reply(
            got, publish_snapshot=(
                self.__dict__.get("_snapshot_policy", "eager") == "eager"),
            publish_hits=True)

    def _refresh_snapshot(self):
        """Observe the current state without executing or resetting the model."""
        if not self.__dict__.get("_snapshot_valid", True):
            self._say({"call": "_observe_state"})
            got = self._accept_call_reply(self._hear())
            if got["raised"] or got["produced"] is not None:
                self._malformed_reply("malformed isolated state observation")

    def _malformed_reply(self, message):
        """End a protocol-violating child and expose only the typed boundary."""
        self._fail(Broken(message))

    def _fail(self, failure):
        """Report the primary failure even if terminating its child also fails."""
        try:
            raise failure from None
        finally:
            self.close()

    def _read_with_deadline(self):
        """Read one framed reply under the owner or standalone wall deadline.

        This lane owns the pipe. Poll it directly instead of creating a thread
        per call: a ready reply must not be mistaken for a slow model merely
        because a competing trace decoder delayed that reader thread.
        """
        from ..execution_control import check_execution, remaining_timeout

        stream = self._child.stdout.buffer
        pending = self.__dict__.setdefault('_reply_pending', bytearray())
        poller = select.poll()
        poller.register(stream.fileno(), select.POLLIN)
        operation = self.__dict__.get('_pending_call', 'reply')
        timeout_message = (f"the model did not answer inside {self._limits.wall_seconds}s "
                           f"during {operation}")
        try:
            deadline = time.monotonic() + remaining_timeout(
                _session_wall_seconds(self._limits.wall_seconds))
            received = False
            while True:
                newline = pending.find(b'\n')
                size = len(pending) if newline < 0 else newline + 1
                if _MAX_PROTOCOL_BYTES is not None and size > _MAX_PROTOCOL_BYTES:
                    raise Broken("the child response exceeds the protocol budget",
                                 code="isolation-protocol-limit")
                if newline >= 0:
                    line = bytes(pending[:size])
                    del pending[:size]
                    return line
                if received and time.monotonic() >= deadline:
                    check_execution()
                    raise Timeout(timeout_message)
                # Drain bytes that are already ready even if this host lane was
                # descheduled. This does not grant the model another waiting period.
                if not poller.poll(math.ceil(1000 * max(0, deadline - time.monotonic()))):
                    check_execution()
                    raise Timeout(timeout_message)
                try:
                    chunk = stream.read1(io.DEFAULT_BUFFER_SIZE)
                except IsolationOutputLimit:
                    return b""  # _hear preserves the supervisor's resource attribution.
                if not chunk:
                    line = bytes(pending)
                    pending.clear()
                    if line:
                        raise Broken("the child response has no terminating newline",
                                     code="isolation-protocol-limit")
                    return b""
                pending.extend(chunk)
                received = True
        except BaseException:
            self._end_child()
            raise

    def _broken_transport(self):
        """Preserve supervisor-owned causes; never infer them from child prose."""
        child = self.__dict__.get("_child")
        code, detail = "", ""
        if type(child) is SandboxProcess:
            # EOF on the reply pipe can precede the supervisor's terminal receipt.
            # The namespace launcher may already have exited zero even when the
            # model timed out. Await that receipt using the SAME cleanup deadline;
            # neither the launcher code nor an extra waiting allowance is evidence.
            if child.final_status is None:
                deadline = self._cleanup_until()
                try:
                    child.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    pass
            status = child.final_status
            if status in {"cpu-limit", "memory-limit", "process-limit",
                          "output-limit", "protocol-limit", "file-limit",
                          "timed-out"}:
                code, detail = "isolation-" + status, status
            elif child.status_refusal is not None:
                code = child.status_refusal.code
                detail = child.status_refusal.why
        return Broken(self._why(), code=code, detail=detail)

    def _why(self):
        worker = self.__dict__.get("_stderr_thread")
        if worker is not None:
            # Diagnostics are best-effort, never another private waiting budget.
            worker.join(0)
        diagnostic = self.__dict__.get("_stderr_text", "").strip()
        child = self._child
        code = (None if type(child) is SandboxProcess and child.final_status is None
                else child.poll())
        if code is None:
            return "the child stopped answering"
        # A negative code is a signal, which is how a resource limit ends it: SIGKILL
        # or SIGXCPU for the CPU limit, SIGXFSZ for a write.
        import signal as sig

        if code < 0:
            try:
                name = sig.Signals(-code).name
            except ValueError:
                name = f"signal {-code}"
            reason = f"the child was ended by {name}"
        else:
            reason = f"the child exited with {code}"
        return reason if not diagnostic else f"{reason}: {diagnostic}"

    # ── what a model looks like ──

    def reset(self):
        """Reset the model and hit history; propagate source or service failures."""
        self._say({"call": "reset"})
        got = self._accept_call_reply(self._hear(), reset_hits=True)
        if got.get("raised"):
            raise _raised(got["raised"])
        expected = {
            name for name, (_interface, representation) in
            (self.__dict__.get("_memory_bindings") or {}).items()
            if representation == "reference"}
        if set(self.__dict__.get("_memory_values") or {}) != expected:
            self._malformed_reply(
                "the isolated model did not publish its reset memory state")

    def process(self, /, **inputs):
        self._say({"call": "process", "inputs": inputs})
        got = self._accept_model_reply(self._hear())
        if got.get("raised"):
            raise _raised(got["raised"])
        return got["produced"]

    def _bind_external_memory(
            self, memories, environment, horizon, *, representation="reference"):
        """Bind the model's memory fields to one exact stimulus environment."""

        from ..core.memory import MemoryEnvironment, MemoryInterface

        memories = tuple(memories)
        if any(type(memory) is not MemoryInterface for memory in memories):
            raise TypeError("external-memory binding needs MemoryInterface records")
        if type(environment) is not MemoryEnvironment:
            raise TypeError("external-memory binding needs one MemoryEnvironment")
        if type(horizon) is not int or horizon < 0:
            raise TypeError("external-memory binding needs a finite horizon")
        if representation not in ("reference", "array"):
            raise ValueError(
                "external-memory binding uses reference or array semantics")
        if len({memory.name for memory in memories}) != len(memories):
            raise ValueError("external-memory binding repeats an interface")
        previous_bindings = self.__dict__.get("_memory_bindings", {})
        previous_values = self.__dict__.get("_memory_values", {})
        self._memory_bindings = {
            memory.name: (memory, representation) for memory in memories}
        try:
            self._say({
                "call": "configure_external_memory",
                "binding": {
                    "interfaces": [memory.as_dict() for memory in memories],
                    "environment": environment.as_dict(),
                    "horizon": horizon,
                    "representation": representation,
                },
            })
            got = self._accept_call_reply(self._hear())
            if got.get("raised"):
                raise _raised(got["raised"])
            result = got["produced"]
            if (type(result) is not dict
                    or result.get("status") not in ("ok", "failed")):
                raise Broken("malformed isolated external-memory reply")
            if result["status"] == "failed":
                raise ValueError(
                    f"{result.get('error', 'Error')}: {result.get('why', '')}")
        except BaseException:
            self._memory_bindings = previous_bindings
            self._memory_values = previous_values
            raise

    @staticmethod
    def _atomic_envelope(value, decode_value):
        if type(value) is not dict or type(value.get("status")) is not str:
            raise ValueError("an atomic answer is not an exact status envelope")
        status = value["status"]
        if status == "ok":
            if set(value) != {"status", "value"}:
                raise ValueError("an atomic success has exactly status and value")
            return {"status": status, "value": decode_value(value["value"])}
        if status in {"refused", "candidate-fault"}:
            if set(value) != {"status", "refusal"}:
                raise ValueError("an atomic refusal has exactly status and refusal")
            refusal = value["refusal"]
            if (type(refusal) is not dict
                    or set(refusal) not in ({"kind", "why"},
                                           {"kind", "why", "detail"})
                    or type(refusal.get("kind")) is not str
                    or not refusal["kind"]
                    or type(refusal.get("why")) is not str
                    or not refusal["why"]
                    or ("detail" in refusal
                        and (type(refusal["detail"]) is not dict
                             or any(type(name) is not str or not name
                                    for name in refusal["detail"])))):
                raise ValueError("an atomic refusal record is malformed")
            return value
        raise ValueError("an atomic answer has an unknown status")

    def _atomic_call(self, request, decode_value, *, publish_snapshot=False):
        self._say(request)
        got = self._decode_call_reply(
            self._hear(),
            result_decoder=lambda value: self._atomic_envelope(
                value, decode_value),
            shape_error="malformed isolated atomic-model reply",
        )
        if got.get("raised"):
            self._malformed_reply("an atomic call used the ordinary exception channel")
        answer = got["produced"]
        if answer["status"] in {"refused", "candidate-fault"}:
            self._publish_call_reply(got, publish_snapshot=False)
            record = answer["refusal"]
            error = CandidateFault if answer["status"] == "candidate-fault" else Refused
            raise error(Refusal(
                record["kind"], record["why"],
                **dict(record.get("detail") or {})))
        self._publish_call_reply(got, publish_snapshot=publish_snapshot)
        return answer["value"]

    def atomic_init(self, ownership, domains):
        from .multiclock import DomainOwnership, _NativeState

        if type(ownership) is not DomainOwnership:
            raise TypeError("atomic_init needs exact DomainOwnership")
        if type(domains) not in (tuple, list) or any(
                type(domain) is not str or not domain for domain in domains):
            raise TypeError("atomic domains are exact nonempty strings")
        domains = list(domains)
        if domains != sorted(set(domains)):
            raise ValueError("atomic domains are sorted and unique")
        def initial_state(value):
            if (type(value) is not dict
                    or any(type(name) is not str or not name for name in value)):
                raise ValueError(
                    "an atomic initial state is a string-keyed exact mapping")
            return _NativeState(value)

        got = self._atomic_call({
            "call": "atomic_init",
            "ownership": ownership.as_record(),
            "domains": domains,
        }, initial_state, publish_snapshot=True)
        carried, opaque = self._audit_view()
        if opaque or got != _NativeState(carried):
            self._malformed_reply(
                "the atomic initial-state record disagrees with the worker snapshot")
        self._atomic_domains = tuple(domains)
        self._atomic_ownership = ownership
        self._atomic_state = got
        return got

    def atomic_init_from_source(self, domains):
        """Initialise atomic replay with ownership derived inside this worker.

        The reply carries the derived declaration back because validation of every
        later atomic successor is performed on both sides of the isolation boundary.
        """
        from .multiclock import DomainOwnership, _NativeState

        if type(domains) not in (tuple, list) or any(
                type(domain) is not str or not domain for domain in domains):
            raise TypeError("atomic domains are exact nonempty strings")
        domains = list(domains)
        if domains != sorted(set(domains)):
            raise ValueError("atomic domains are sorted and unique")

        def initial(value):
            if (type(value) is not dict
                    or set(value) != {"state", "ownership"}
                    or type(value["state"]) is not dict):
                raise ValueError(
                    "a source-derived atomic initial record is malformed")
            ownership = DomainOwnership.from_record(value["ownership"])
            return _NativeState(value["state"]), ownership

        state, ownership = self._atomic_call({
            "call": "atomic_init_source",
            "domains": domains,
        }, initial, publish_snapshot=True)
        carried, opaque = self._audit_view()
        if opaque or state != _NativeState(carried):
            self._malformed_reply(
                "the atomic initial-state record disagrees with the worker snapshot")
        self._atomic_domains = tuple(domains)
        self._atomic_ownership = ownership
        self._atomic_state = state
        return state, ownership

    def atomic_ready(self, domain):
        if type(domain) is not str or not domain:
            raise TypeError("an atomic readiness query needs a domain name")

        def boolean(value):
            if type(value) is not bool:
                raise ValueError("an atomic readiness result is not Boolean")
            return value

        return self._atomic_call(
            {"call": "atomic_ready", "domain": domain}, boolean)

    def atomic_ready_observation(self, domain):
        """Return one readiness value and the structural hits it executed."""

        if type(domain) is not str or not domain:
            raise TypeError("an atomic readiness query needs a domain name")

        def observation(value):
            if (type(value) is not dict
                    or set(value) != {"ready", "hits"}
                    or type(value["ready"]) is not bool
                    or type(value["hits"]) is not list
                    or any(type(hit) is not str or not hit
                           for hit in value["hits"])
                    or value["hits"] != sorted(set(value["hits"]))):
                raise ValueError(
                    "an atomic readiness observation is malformed")
            return value["ready"], tuple(value["hits"])

        return self._atomic_call({
            "call": "atomic_ready_observation", "domain": domain,
        }, observation)

    def atomic_step(self, slot, invocations=()):
        from ..core.clock_schedule import GlobalEventSlot
        from .multiclock import (
            DomainInvocation,
            _isolated_transition_from_record,
        )

        if type(slot) is not GlobalEventSlot:
            raise TypeError("an isolated atomic step needs GlobalEventSlot")
        if type(invocations) not in (tuple, list) or any(
                type(invocation) is not DomainInvocation
                for invocation in invocations):
            raise TypeError("atomic invocations are exact DomainInvocation records")
        request = {
            "slot": slot.as_dict(),
            "invocations": [item.as_record() for item in invocations],
        }
        encoded = WireEncoder(DEFAULT_WIRE_NODES).encode(request)
        def transition_record(value):
            transition = _isolated_transition_from_record(
                value, self._atomic_state, self._atomic_ownership)
            if transition.slot.as_dict() != slot.as_dict():
                raise ValueError(
                    "the atomic worker replied for a different event slot")
            if (set(slot.events) == set(self._atomic_domains)
                    and set(slot.resets) == set(self._atomic_domains)):
                expected = ("__all_reset__",)
            else:
                expected = tuple(sorted(
                    (set(slot.events) & set(slot.resets))
                    | {invocation.domain for invocation in invocations}))
            actual = tuple(effect.domain for effect in transition.effects)
            if actual != expected:
                raise ValueError(
                    "the atomic worker replied with another handler-effect set")
            return transition

        transition = self._atomic_call(
            {"call": "atomic_step", "request": encoded}, transition_record)
        self._atomic_state = transition.after
        return transition

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        # Offered methods need no state image. Other attributes use the live
        # field-name image from the last reply; fetch values only when present.
        if name in (self.__dict__.get("_offers") or ()):
            def call(*args, **inputs):
                self._say({"call": name, "args": list(args), "inputs": inputs})
                got = self._accept_model_reply(self._hear())
                if got.get("raised"):
                    raise _raised(got["raised"])
                return got["produced"]
            call.__name__ = name
            return call
        fields = self.__dict__.get("_field_names")
        if fields is not None and name not in fields:
            raise AttributeError(name)
        self._refresh_snapshot()
        state = self.__dict__.get("_state") or {}
        if name in state:
            return state[name]
        raise AttributeError(name)

    def _share_map(self):
        """Per-field reachable mutable object identities, from the child."""
        return self._share_image()["fields"]

    def _share_image(self):
        """Field and module sharing domains, kept distinct on the wire."""
        self._say({"call": "share"})
        def sharing_shape(produced):
            return (type(produced) is dict
                    and set(produced) == {"fields", "modules"}
                    and type(produced["fields"]) is dict
                    and type(produced["modules"]) is dict)

        got = self._accept_call_reply(
            self._hear(), result_shape=sharing_shape,
            shape_error="malformed sharing image")
        if got.get("raised"):
            raise _raised(got["raised"])
        return got["produced"]

    def _external_memory_result(self, interfaces):
        """Final cells from the protocol-owned channel, not candidate attributes."""
        from ..core.memory_result import MemoryResult

        self._refresh_snapshot()
        bindings = self.__dict__.get("_memory_bindings") or {}
        values = self.__dict__.get("_memory_values") or {}
        if (set(values) != {memory.name for memory in interfaces}
                or any(bindings.get(memory.name) != (memory, "reference")
                       for memory in interfaces)):
            raise Broken("the isolated model omitted a bound external-memory result")
        return MemoryResult((name, value.sparse_items())
                            for name, value in values.items()).validate(interfaces)

    def _audit_view(self):
        """The model's state as exact native values, and what could not cross.

        The audit used to read the proxy with `getattr`, which serves only the
        step-value channel (ints and flat int arrays), so every other carrier
        looked absent -- and a contract field absent on both sides is skipped,
        which turned "the isolation cannot carry this" into "the two agree"
        (review 25). This view decodes the child's typed encoding back to the
        exact values, and names the fields that have no faithful encoding so
        the audit can fail closed on them instead.
        """
        self._refresh_snapshot()
        values = dict(self.__dict__.get("_memory_values") or {})
        bad = dict(self.__dict__.get("_opaque") or {})
        decoder = WireDecoder(DEFAULT_WIRE_NODES)
        for name, enc in (self.__dict__.get("_carried") or {}).items():
            try:
                values[name] = decoder.decode(enc)
            except NativeValueError:
                bad[name] = "an encoding the parent could not decode"
        return values, bad

    def _cleanup_until(self):
        """Terminal receipt and reap share one existing runtime allowance."""
        deadline = self.__dict__.get("_cleanup_deadline")
        if deadline is None:
            deadline = time.monotonic() + defaults.SERVICE_CLEANUP_SECONDS
            self._cleanup_deadline = deadline
        return deadline

    def _end_child(self):
        """Reap one child and release its pipes within one lifetime deadline."""
        child = self.__dict__.get("_child")
        if child is None:
            return
        failure = sys.exception()
        deadline = self._cleanup_until()
        try:
            # Every completed call has already delivered its observations. There
            # is no final reply or model-side artifact to flush. Stop now so a
            # slow Python exit cannot consume the entire reap allowance first.
            if type(child) is SandboxProcess:
                # The namespace owner kills, reaps and closes its pipes. Do not
                # repeat those waits here or close a live reader's pipe first.
                child.close(deadline=deadline)
            else:
                # Explicit test launcher only; production always uses SandboxProcess.
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=max(0.0, deadline - time.monotonic()))
                for stream in (child.stdin, child.stdout, child.stderr):
                    if stream is not None:
                        stream.close()
            if self.__dict__.get("_child") is child:
                self._child = None
        except BaseException as cleanup:
            from ..cpu_admission import cpu_cleanup_unconfirmed

            cpu_cleanup_unconfirmed()
            if failure is None:
                raise FailureRaised(RunFailure.infrastructure(
                    "behavior-worker-cleanup", Refusal(
                        "behavior-worker-cleanup-unconfirmed",
                        "the isolated model's worker could not be reaped",
                        error=type(cleanup).__name__, message=str(cleanup)))) from cleanup
            # Keep the owner and original deadline available for a later reap.
            # The traceback carries
            # cleanup evidence without replacing the model/transport exception.
            failure.add_note("Isolated-model cleanup unconfirmed: "
                             + "".join(traceback.format_exception_only(cleanup)).rstrip())

    def close(self):
        self._end_child()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:                                       # noqa: BLE001, S110
            pass


_MODEL_EXCEPTION = object()


def is_model_exception(error):
    """Whether a validated worker reply reported this exception from the model."""
    return getattr(error, "_isolated_model_origin", None) is _MODEL_EXCEPTION


def _raised(name):
    """The exception the child reported, by name.

    Rebuilt rather than pickled, since what the audit compares is which failure it was
    and `encoder.EXCEPTION_CODES` keys on the name. An exception nobody named comes back
    as a plain one carrying the name.
    """
    got = getattr(__builtins__, name, None) if not isinstance(__builtins__, dict) \
        else __builtins__.get(name)
    error = (got("raised in the isolated model")
             if isinstance(got, type) and issubclass(got, Exception)
             else RuntimeError(name))
    # Keep the exact exception class for native/IR error-code comparison. The
    # origin token is created in the parent, never accepted from the child.
    error._isolated_model_origin = _MODEL_EXCEPTION
    return error


def facts_of(source, file="behavior.py", cls=None, limits=None, path=(),
             config=None, recorded=None):
    """What a model **is**, without importing it here.

    `cosim` needs three things off the class before it can generate anything: its name,
    the parameters of `process`, and the per-domain handlers where there are several.
    Reading them meant `load_behavior` -- an import of the agent's module into the
    verifier's process, executing whatever is at its top level, before admission has even
    decided the task can be verified. Nothing about those three facts requires that.

    One child, one greeting, closed immediately. The `Broken`/`Timeout` a model that will
    not start raises are the caller's to handle: a model that cannot come up is a fact
    about the task, not about this function.
    """
    # The facts are source-derived and the load-integrity and method-image
    # checks run regardless of the mode, so this needs no module image: a
    # reference model whose module holds an opaque object is read here for
    # its name and inputs without the strict freeze breaking it at startup
    # (review 49: production cosim went through the strict default and
    # Broke such a model before generating anything).
    model = IsolatedModel(source, file=file, cls=cls, limits=limits, path=path,
                          config=config, recorded=recorded, strict_module=False)
    try:
        return dict(model.__dict__.get("_facts") or {})
    finally:
        model.close()


def snapshot_of(model):
    """The attributes the child could report, as of its last answer.

    A function rather than an attribute, and that is the whole lesson of the name
    collision this replaces: `__getattr__` serves the model's own attributes, so any
    public name on the proxy shadows a model field of that name. The snapshot was called
    `.state`, and 33 models in the population have a state variable called `state`.
    Reaching into `__dict__` from outside cannot collide with anything.
    """
    model._refresh_snapshot()
    return dict(model.__dict__.get("_state") or {})


def unsupported_of(model):
    """Why each attribute the child could not report could not be reported."""
    model._refresh_snapshot()
    return dict(model.__dict__.get("_unsupported") or {})


def hits_of(model):
    """The goal identifiers the instrumented model reached, as a live list.

    The list itself, not a copy, since `model_audit` is given one to `clear()` and read
    afterwards.
    """
    return model.__dict__.setdefault("_hits", [])


class _Hits:
    """The hit list of whichever model the factory built last.

    `model_audit` is handed one list for the whole run and constructs a model per trace,
    which in one process is right because the hits come from the loaded module and the
    module outlives the models. Here each model is a separate child with hits of its own,
    so a fixed list would be the first child's and would stay empty while the others ran.
    A view that follows the factory is what keeps `model_audit` unchanged.

    The audit clears the view before each step and reads that step's hits afterward.
    """

    __slots__ = ("_factory",)

    def __init__(self, factory):
        self._factory = factory

    def _live(self):
        model = self._factory.latest
        return hits_of(model) if model is not None else []

    def clear(self):
        self._live().clear()

    def __iter__(self):
        return iter(list(self._live()))

    def __len__(self):
        return len(self._live())

    def __repr__(self):
        return f"_Hits({list(self)})"


class _Factory:
    """Something callable that gives a fresh model, and remembers the last one.

    `model_audit` does `model_cls()` per trace, so a factory is the shape it wants, and a
    fresh process per trace is what makes one trace's failure not the next trace's
    problem. Remembering the last is what `hits` needs.
    """

    __slots__ = (
        "cls",
        "config",
        "file",
        "latest",
        "limits",
        "source_rules",
        "path",
        "recorded",
        "source",
    )

    def __init__(self, source, file, cls, limits, path=(), config=None,
                 recorded=None, source_rules=EMPTY_SOURCE_RULES):
        self.source = source
        self.file = file
        self.cls = cls
        self.limits = limits
        self.path = tuple(path or ())
        self.config = dict(config or {})
        self.recorded = dict(recorded or {})
        self.source_rules = source_rules
        self.latest = None

    def __call__(self):
        if self.latest is not None:
            self.latest.close()
        self.latest = IsolatedModel(self.source, file=self.file, cls=self.cls,
                                    limits=self.limits, path=self.path,
                                    config=self.config, recorded=self.recorded,
                                    source_rules=self.source_rules)
        return self.latest

    @property
    def hits(self):
        return _Hits(self)

    def close(self):
        if self.latest is not None:
            self.latest.close()
            self.latest = None


def isolated_model(source, file="behavior.py", cls=None, limits=None, path=(),
                   config=None, recorded=None, source_rules=EMPTY_SOURCE_RULES):
    """A factory shaped like a model class, for a caller that constructs one.

    ``path`` is searched before the parent's own, for a caller that materialised the
    model's recorded dependencies somewhere of its own. ``config`` is what the class is
    constructed with, which for a reference is part of which model it is.
    """
    return _Factory(source, file, cls, limits, path=path, config=config,
                    recorded=recorded, source_rules=source_rules)
