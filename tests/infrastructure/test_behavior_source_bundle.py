"""Differential tests execute only test-owned Python fixtures, never submissions."""

import ast
import importlib.util
import sys
from dataclasses import replace
from types import MappingProxyType

import pytest

from behave.sim.behavior.source_bundle import BehaviorSourceBundle, prepare_behavior_bundle
from behave.sim.core.failure import FailureRaised
from behave.sim.core.refusal import Refused


def bundle(helper, body="return h.f(a)", *, prefix="", extra=None):
    files = {"model.py": ("import helper as h\n" + prefix + "class M:\n    def process(self, a):\n        " +
                          body.replace("\n", "\n        ") + "\n").encode(), "helper.py": helper.encode()}
    return BehaviorSourceBundle({**files, **(extra or {})}, "model.py", "M")


def native(source, tmp_path, monkeypatch):
    for path, data in source.files.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
    monkeypatch.syspath_prepend(str(tmp_path))
    for module in ("helper", "other", "pkg", "pkg.leaf", "pkg.ops"):
        monkeypatch.delitem(sys.modules, module, raising=False)
    spec = importlib.util.spec_from_file_location("model", tmp_path / source.entry)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, source.model)()


def linked(source):
    prepared = prepare_behavior_bundle(source)
    namespace = {}
    exec(compile(prepared.source, prepared.file, "exec"), namespace)  # noqa: S102 - test-owned fixture
    return namespace[source.model](), prepared


@pytest.mark.parametrize("future", ["", "from __future__ import annotations\n"])
def test_unreferenced_files_do_not_change_closed_entry_semantics(future):
    source = future + """class M:
    def process(self, a):
        result = {}
        for i in range(3):
            result['out_' + str(i)] = a + i
        return result
"""
    single = BehaviorSourceBundle({"model.py": source.encode()}, "model.py", "M")
    captured = replace(single, files={**single.files,
                                     "unimported.py": b"not valid Python!",
                                     "spec.md": b"captured, not executed"})
    before, one = linked(single)
    after, many = linked(captured)
    assert one.source == many.source == source
    assert one.modules == many.modules == ("model.py",)
    assert one.bundle != many.bundle
    assert many.dependencies == {}
    for value in (0, -1, 1 << 80):
        assert after.process(value) == before.process(value)


@pytest.mark.parametrize("owner", ["M", "_M"])
def test_private_keys_survive_class_alias_linking(tmp_path, monkeypatch, owner):
    helper = f"""class {owner}:
    __K: int = 7
    def reset(self): self.__state = 0
    @staticmethod
    def __f(__value): return __value + 1
    def process(self, a):
        def __local(__value): return self.__f(__value)
        self.__state += __local(a)
        return {{'y': self.__state + self._M__K}}
"""
    source = BehaviorSourceBundle({
        "model.py": f"from helper import {owner} as Selected\n".encode(),
        "helper.py": helper.encode(),
    }, "model.py", "Selected")
    before = native(source, tmp_path, monkeypatch)
    after, _ = linked(source)
    before.reset()
    after.reset()
    for value in (0, 4, 255):
        assert after.process(value) == before.process(value)
        assert after._M__state == before._M__state


@pytest.mark.parametrize("body", [
    "return sum(h.f(i) for i in range(a) if i % 2)",
    "g = (h.f(i) for i in range(a))\na = 99\nreturn sum(g)",
    "g = (h.f(i) + a for i in range(a))\na = 99\nreturn sum(g)",
    "return sum(h.f(i + j) for i in range(a) for j in range(i) if j > 0)",
    "return sum(h + a for h in range(3)) + h.f(a)",
])
def test_linked_generator_preserves_scopes_and_lazy_execution(tmp_path, monkeypatch, body):
    source = bundle("K = 7\ndef f(a): return a + K\n", body)
    before = native(source, tmp_path, monkeypatch)
    after, prepared = linked(source)
    assert any(isinstance(node, ast.GeneratorExp) for node in ast.walk(ast.parse(prepared.source)))
    for value in (0, 1, 4):
        assert before.process(value) == after.process(value)


def test_registered_wildcard_library_import_links_like_python(tmp_path, monkeypatch):
    from behave.sim.behavior import fp_lib

    monkeypatch.setitem(sys.modules, "fp_lib", fp_lib)
    source = bundle("def f(a): return a\n", "return fp32_add(h.f(a), FP32_INF_POS)",
                    prefix="from fp_lib import *\n")
    before, (after, prepared) = native(source, tmp_path, monkeypatch), linked(source)
    assert not any(isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
                   for node in ast.walk(ast.parse(prepared.source)))
    for value in (0, 0x3F800000, 0x7F800000, 0xFF800000):
        assert before.process(value) == after.process(value)


@pytest.mark.parametrize("source", [
    bundle("K = 7\ndef f(a): return a + K\n", prefix="K = 99\n"),
    bundle("K = 7\ndef f(a):\n    def inner(K): return a + K\n    return inner(2) + K\n"),
    bundle("K = 7\ndef f(a): return a + K\n", "h = a + 3\nreturn h"),
    bundle("K = 7\ndef f(a): return a + K\n", "q = [h + a for h in range(3)]\nreturn sum(q) + h.f(a)"),
    bundle("K = 7\ndef f(a): return a + K\n", "def f(h): return h + a\nreturn f(2) + h.f(a)"),
    bundle("K = 7\ndef f(a): return a + K\n", "q = [h.f(a) for i in range(2) for j in range(i)]\nreturn q"),
    bundle("K = 7\ndef f(a): return a + K\n", "q = [(z := a + i) for i in range(2)]\nreturn z + sum(q)"),
    bundle("K = 7\ndef f(a): return a + K\n", "q = {i: h.f(a) for i in range(2)}\nreturn q[0]"),
    bundle("K = 7\ndef f(a): return a + K\n", "return h.f(a)", prefix="def f(a): return 33\n"),
    bundle("from other import f\n", extra={"other.py": b"K = 9\ndef f(a): return a + K\n"}),
    bundle("import other as o\nK = 2\ndef f(a): return o.f(a) + K\n",
           extra={"other.py": b"K = 9\ndef f(a): return a + K\n"}),
    bundle("A = [0, 0]\ndef f(a):\n    A[0] += a\n    return A[0]\n"),
    bundle("K = 7\ndef f(a=K): return a + K\n", "return h.f()"),
    bundle("class Helper: pass\ndef f(a): return a\n"),
    bundle("def f(a: int) -> int:\n    value: int = a\n    return value\n"),
    bundle("def f(a: int) -> dict[str, int] | None:\n    return {'y': a}\n"),
    bundle("def f(a):\n    value: Missing() = a\n    return value\n"),
    bundle("def f(a):\n    values = [a]\n    values[0]: Missing = a + 1\n    return values[0]\n"),
    bundle("def f(a): return a\n", "self.state: int = a + 1\nreturn self.state"),
    bundle("K: int = 7\ndef f(a): return a + K\n", prefix="K: int = 99\n"),
    bundle("K: int\nK = 7\ndef f(a): return a + K\n"),
    bundle("unused: int\ndef f(a): return a\n"),
    bundle("from other import K\nK: int\ndef f(a): return a + K\n",
           extra={"other.py": b"K = 7\n"}),
    bundle("K = 7\ndef f(a):\n    def inner(x: K = K) -> K: return x\n    return inner() + a\n"),
    bundle("from __future__ import annotations\ndef f(a: Missing()) -> list[Missing]: return a\n"),
    BehaviorSourceBundle({"model.py": b"from helper import K\nclass M:\n K: int = K\n empty: int\n def process(self, a: K) -> K: return a+self.K\n",
                          "helper.py": b"K = 7\n"}, "model.py", "M"),
    BehaviorSourceBundle({"model.py": b"import helper as h\nclass M:\n def process(self, a: h.Box) -> h.Box | None: return a\n",
                          "helper.py": b"class Box: pass\n"}, "model.py", "M"),
    BehaviorSourceBundle({"model.py": b"from helper import K\nclass M:\n K = K\n def process(self, a): return a+self.K\n",
                          "helper.py": b"K = 7\n"}, "model.py", "M"),
    BehaviorSourceBundle({"model.py": b"from pkg.ops import f\nclass M:\n def process(self, a): return f(a)\n",
                          "pkg/__init__.py": b'"package"\n',
                          "pkg/ops.py": b"from .leaf import f\n",
                          "pkg/leaf.py": b"K = 8\ndef f(a): return K+a\n"}, "model.py", "M"),
])
def test_behavior_linker_preserves_python_module_and_lexical_ownership(tmp_path, monkeypatch, source):
    before = native(source, tmp_path, monkeypatch)
    after, prepared = linked(source)
    assert [before.process(a) for a in (0, 1, 255)] == [after.process(a) for a in (0, 1, 255)]
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(ast.parse(prepared.source)))
    assert prepared.bundle == source.identity


def test_behavior_bundle_identity_is_closed_and_deterministic():
    original = bundle("def f(a): return a\n", extra={"unused.txt": b"bound too"})
    prepared = prepare_behavior_bundle(original)
    assert prepare_behavior_bundle(replace(original, files=dict(reversed(list(original.files.items()))))) == prepared
    changed = replace(original, files={**original.files, "unused.txt": b"different"})
    assert prepare_behavior_bundle(changed).source != prepared.source
    assert prepared.as_dict()["modules"] == ["helper.py", "model.py"]
    assert isinstance(original.files, MappingProxyType)
    for bad in ("../helper.py", "/tmp/helper.py", "a/../../b.py"):
        with pytest.raises(ValueError):
            replace(original, files={**original.files, bad: b""})
    with pytest.raises(ValueError):
        replace(original, entry="missing.py")


@pytest.mark.parametrize("helper", [
    # A local declaration shadows a module value even without assignment.
    "K = 7\ndef f(a):\n    K: int\n    return K\n",
    # The same rule holds when the uninitialized cell is read by a closure.
    "K = 7\ndef f(a):\n    K: int\n    def inner(): return K\n    return inner()\n",
])
def test_behavior_linker_preserves_annotation_only_unbound_locals(tmp_path, monkeypatch, helper):
    source = bundle(helper)
    before = native(source, tmp_path, monkeypatch)
    after, _ = linked(source)
    with pytest.raises(NameError) as expected:
        before.process(0)
    with pytest.raises(type(expected.value)):
        after.process(0)


def test_behavior_linker_preserves_annotation_target_evaluation(tmp_path, monkeypatch):
    source = bundle("""def f(a):
    values = [a]
    def index():
        values[0] += 1
        return 0
    values[index()]: Missing()
    return values[0]
""")
    before = native(source, tmp_path, monkeypatch)
    after, _ = linked(source)
    assert before.process(7) == after.process(7) == 8


def test_behavior_linker_preserves_signature_and_class_annotation_scopes(tmp_path, monkeypatch):
    from behave.sim.behavior import instrumented

    source = BehaviorSourceBundle({
        "model.py": b"""from helper import K
class M:
    K: K = 9
    absent: int
    def process(self, a: K) -> dict[str, int]: return {'y': a}
""",
        "helper.py": b"K = 7\n",
    }, "model.py", "M")
    before = native(source, tmp_path, monkeypatch)
    after, prepared = linked(source)
    namespace, _sites, _hits = instrumented.load_source(prepared.source, file=prepared.file)
    assert namespace[prepared.model].process.__annotations__ == before.process.__annotations__
    assert type(before).__annotations__ == type(after).__annotations__ == {"K": 9, "absent": int}
    assert before.process.__annotations__ == after.process.__annotations__ == {
        "a": 9, "return": dict[str, int],
    }
    assert not hasattr(after, "absent")


def test_behavior_linker_keeps_future_annotations_module_local(tmp_path, monkeypatch):
    source = BehaviorSourceBundle({
        "model.py": b"""from __future__ import annotations
from helper import f
class M:
    state: Missing = 0
    def reset(self) -> None: self.state = 0
    def process(self, a: Missing()) -> dict[str, int]:
        self.state: AlsoMissing = self.state + f(a)
        return {'y': self.state}
""",
        "helper.py": b"def f(a: int) -> int:\n    value: Missing() = a\n    return value\n",
    }, "model.py", "M")
    before = native(source, tmp_path, monkeypatch)
    after, prepared = linked(source)
    namespace = {}
    exec(compile(prepared.source, prepared.file, "exec", dont_inherit=True), namespace)  # noqa: S102
    helper_name = next(linked for path, original, linked in prepared.symbols
                       if path == "helper.py" and original == "f")
    assert namespace[helper_name].__annotations__ == {"a": int, "return": int}
    assert before.process.__annotations__ == after.process.__annotations__ == {
        "a": "Missing()", "return": "dict[str, int]",
    }
    assert type(before).__annotations__ == type(after).__annotations__ == {"state": "Missing"}
    for a in (1, 2, 3):
        assert before.process(a) == after.process(a)
    before.reset()
    after.reset()
    assert before.process(4) == after.process(4) == {"y": 4}


@pytest.mark.parametrize("helper", [
    "def f(a: 1 // 0): return a\n",
    "value: 1 // 0 = 7\ndef f(a): return a + value\n",
    "class Helper:\n    value: 1 // 0 = 7\ndef f(a): return a\n",
])
def test_behavior_linker_does_not_erase_eager_annotation_failures(tmp_path, monkeypatch, helper):
    from behave.sim.behavior import instrumented

    source = bundle(helper)
    with pytest.raises(ZeroDivisionError):
        native(source, tmp_path, monkeypatch)
    with pytest.raises(ZeroDivisionError):
        linked(source)
    prepared = prepare_behavior_bundle(source)
    with pytest.raises(ZeroDivisionError):
        instrumented.load_source(prepared.source, file=prepared.file)


@pytest.mark.isolation
@pytest.mark.parametrize("future", ["", "from __future__ import annotations\n"])
@pytest.mark.parametrize("local_annotation", ["", ": int"])
def test_annotated_bundle_uses_existing_concrete_and_lowering_boundaries(
        tmp_path, monkeypatch, future, local_annotation):
    from behave.sim.behavior import instrumented
    from behave.sim.symbolic import interp
    from behave.sim.behavior.lowering import lower_procedure

    source = BehaviorSourceBundle({
        "model.py": (future + """from helper import add
class M:
    def reset(self) -> None: pass
    def process(self, a: int) -> dict[str, int]:
        value = add(a)
        return {'y': value}
""").encode(),
        "helper.py": (future + """def add(a: int) -> int:
    result = a + 1
    return result
""").encode(),
    }, "model.py", "M")
    source = replace(source, files={name: data.replace(b"value =", f"value{local_annotation} =".encode())
                                   .replace(b"result =", f"result{local_annotation} =".encode())
                                   for name, data in source.files.items()})
    before = native(source, tmp_path, monkeypatch)
    after, prepared = linked(source)
    namespace, _sites, _hits = instrumented.load_source(prepared.source, file=prepared.file)
    concrete = namespace[prepared.model]()
    for a in (0, 1, 255):
        assert concrete.process(a) == before.process(a) == after.process(a)
    procedure = lower_procedure(prepared.source)
    for a in (0, 1, 255):
        _state, outputs, _hits, error = interp.run_step(procedure, {"a": a})
        assert not error
        assert outputs.transactions == [before.process(a)] == [after.process(a)]


@pytest.mark.parametrize("source,code", [
    (bundle("K=1\nK=2\ndef f(a): return K\n"), "binding-redefined"),
    (bundle("K=1\nK+=2\ndef f(a): return K\n"), "module-effects"),
    (bundle("K=[]\nK.append(2)\ndef f(a): return K[0]\n"), "module-effects"),
    (bundle("def f(a): return a\n", "return h"), "object-escape"),
    (bundle("def f(a): return a\n", "return h.f"), "object-escape"),
    (bundle("def f(a): return a\n", "h.f = 2\nreturn a"), "module-write"),
    (bundle("def f(a): return a\n", "return h.__dict__"), "reflection"),
    (bundle("def f(a): return a\n", "return globals()"), "global-unresolved"),
    (bundle("K=0\ndef f(a):\n global K\n K=a\n return K\n"), "dynamic-namespace"),
    (bundle("def f(a):\n import other\n return a\n"), "local-import"),
    (bundle("from model import M\ndef f(a): return a\n"), "import-cycle"),
    (bundle("import os\ndef f(a): return a\n"), "module-unresolved"),
    (bundle("from other import *\ndef f(a): return a\n"), "import-form"),
    (bundle("def f(a): return a\nimport other\n"), "module-unresolved"),
    (bundle("def f(a=print(3)): return a\n"), "definition-effects"),
    (bundle("def f(a: print(3)): return a\n"), "definition-effects"),
    (bundle("def f(_behave_link_0_K): return 2\n"), "reserved-name"),
    (bundle("def f(a): return __name__\n"), "reserved-name"),
    (bundle("def f(a):\n def inner(): return a\n return '%s' % inner\n"), "object-escape"),
    (bundle("def f(a): return a\n", "(g := h.f)(a)\nreturn '%s' % g"), "object-escape"),
    (bundle("def f(a): return a\n", "return [h.f][0](a)"), "object-escape"),
    (bundle("def f(a): yield a\n"), "dynamic-namespace"),
    (bundle("def f(a): return a\n", "try: return h.f(a)\nexcept Exception as e: return e.args"), "dynamic-namespace"),
])
def test_behavior_linker_refuses_semantics_it_cannot_preserve(source, code):
    with pytest.raises(Refused) as error:
        prepare_behavior_bundle(source)
    assert error.value.refusal.kind == "behavior-bundle-" + code


@pytest.mark.parametrize("helper", [b"def f(:\n", b"\xff\n", b"def f(a):\n return a\n break\n"])
def test_behavior_bundle_parser_failures_are_source_owned(helper):
    source = bundle("def f(a): return a\n")
    with pytest.raises(FailureRaised) as error:
        prepare_behavior_bundle(replace(source, files={**source.files, "helper.py": helper}))
    assert error.value.failure.origin.value == "candidate"


def test_behavior_bundle_registered_dependency_is_not_replaceable():
    from behave.sim.behavior import LIBRARY_DIR
    source = bundle("from fp_lib import fp32_add as add\ndef f(a): return add(a, a)\n")
    prepared = prepare_behavior_bundle(source)
    assert prepared.dependencies["fp_lib.py"] == (LIBRARY_DIR / "fp_lib.py").read_bytes()
    with pytest.raises(Refused) as error:
        prepare_behavior_bundle(replace(source, files={**source.files, "fp_lib.py": b"def fp32_add(a,b): return 7"}))
    assert error.value.refusal.kind == "behavior-bundle-intrinsic-shadowed"
    with pytest.raises(Refused) as error:
        prepare_behavior_bundle(replace(source, files={**source.files, "fp_lib/__init__.py": b""}))
    assert error.value.refusal.kind == "behavior-bundle-intrinsic-shadowed"


@pytest.mark.parametrize("imports, pack", [
    ("import struct", "struct.pack"),
    ("import struct as fmt", "fmt.pack"),
    ("from struct import pack as encode", "encode"),
])
def test_behavior_linker_preserves_registered_native_library_calls(
        tmp_path, monkeypatch, imports, pack):
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.instrumented import load_source
    from behave.sim.behavior.lowering import lower_procedure

    source = bundle(imports + "\n" +
                    f"def f(a): return int.from_bytes({pack}('>f', float(a)), 'big')\n",
                    body="return {'y': h.f(a)}")
    before = native(source, tmp_path, monkeypatch)
    after, prepared = linked(source)
    namespace, _, _ = load_source(prepared.source, prepared.file)
    observed = namespace[prepared.model]()
    for value in (0, 1, 3, 255):
        assert before.process(value) == after.process(value) == observed.process(value)
    with pytest.raises(LoweringError) as error:
        lower_procedure(prepared.source, prepared.file, cls=prepared.model,
                        input_widths=[8], outputs=["y"])
    assert error.value.code == "source-import-unapproved"


@pytest.mark.parametrize("statement", [
    "return struct",
    "struct.pack = a\nreturn a",
    "return struct._clearcache()",
])
def test_native_module_linking_does_not_flatten_module_escape_or_mutation(statement):
    source = bundle("import struct\ndef f(a):\n    " +
                    statement.replace("\n", "\n    ") + "\n")
    with pytest.raises(Refused):
        prepare_behavior_bundle(source)


def test_native_module_linking_does_not_replace_a_captured_same_named_module():
    source = bundle("import struct\ndef f(a): return struct.pack('>I', a)\n",
                    extra={"struct.py": b"def pack(fmt, value): return b'wrong'\n"})
    with pytest.raises(Refused) as error:
        prepare_behavior_bundle(source)
    assert error.value.refusal.kind == "behavior-bundle-intrinsic-shadowed"


@pytest.mark.isolation
@pytest.mark.solver
def test_behavior_bundle_helper_hits_and_partial_failure_match_ir_and_encoding():
    from behave.sim.behavior import instance as bi
    from behave.sim.symbolic import interp, ir
    from behave.sim.core.audit import AuditCheckStatus
    from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema

    source = bundle("def f(a):\n if a: return 8 // (a - 1)\n return 5 // a\n", "return {'y': h.f(a)}")
    source = replace(source, files={**source.files, "model.py": source.files["model.py"].replace(
        b"class M:\n", b"class M:\n    def reset(self): pass\n")})
    prepared = prepare_behavior_bundle(source)
    schema = TransactionSchema([Endpoint("a", "input", 8), Endpoint("y", "output", 16)],
                               source="bundle fixture", authority=Authority.TASK, reading=Reading.ELABORATED)
    model = bi.lower(prepared.source, schema, file=prepared.file, bound=3)
    failures = {cmd.goal for cmd in ir.walk(model.proc.body) if isinstance(cmd, ir.Fail) and cmd.code == "DivZero"}
    assert len(failures) == 2
    first = interp.run_step(model.proc, {"a": 1})
    second = interp.run_step(model.proc, {"a": 0})
    assert first[3] == second[3] == "DivZero"
    assert len(first[2] & failures) == len(second[2] & failures) == 1
    assert first[2] & failures != second[2] & failures
    traces = (({"a": 2}, {"a": 1}), ({"a": 0},))
    assert model.source_audit(traces).status is AuditCheckStatus.AGREED
    assert model.audit(traces).status is AuditCheckStatus.AGREED
