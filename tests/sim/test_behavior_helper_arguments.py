"""Fixed Python helper signatures share planning, IR and native hit semantics."""

import ast
import inspect
from itertools import combinations, product

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import encoder, interp, ir
from behave.sim.behavior.extract import LoweringError, source_model
from behave.sim.behavior.instance import lower
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_procedure
from behave.sim.behavior.source_calls import CallBindingError, bind_helper_arguments
from behave.sim.behavior.source_plan import plan_source
from behave.sim.behavior.source_values import SCALAR
from behave.sim.core.schema import Authority, Endpoint, Reading, TransactionSchema


@pytest.mark.parametrize("lowerer", [GraphLowerer, _Lowerer])
@pytest.mark.parametrize("cancel", [False, True])
def test_activation_owns_lexical_state_and_restores_the_exact_caller(lowerer, cancel):
    class Cancelled(BaseException):
        pass

    low = lowerer([])
    root = low.activation
    root.scope = ast.parse('def process(a): pass').body[0]
    caller_value = ir.Lit(7)
    root.substitutions['self'] = caller_value
    root.substitution_contracts['self'] = 'caller-contract'
    root.hoisted.append(ir.Skip())
    root.defined.add('nested')
    root.renames['a'] = 'root_a'
    root.destination = object()
    retained = {name: getattr(root, name) for name in (
        'renames', 'defined', 'substitutions', 'substitution_contracts',
        'return_facts', 'hoisted', 'destination')}
    fn = ast.parse('def helper(a): pass').body[0]

    def call():
        with low._call_activation(fn, {'a': 'outer_a'}, 'outer-summary', object()) as outer:
            assert low.activation is outer and outer.parent is root
            assert outer.scope is fn and outer.summary == 'outer-summary'
            assert not outer.substitutions and not outer.substitution_contracts
            assert not outer.hoisted and not outer.defined and not outer.return_facts
            outer.substitutions['a'] = ir.Lit(2)
            outer.defined.add('child')
            outer.return_facts.append('outer-fact')
            # A repeated source owner still has a distinct activation; the
            # nearest frame supplies a closure's renamed physical binding.
            with low._call_activation(fn, {'a': 'inner_a'}, 'inner-summary', object()) as inner:
                assert tuple(inner.frames()) == (inner, outer, root)
                assert inner.root is root
                assert low._lexical_name(id(fn), 'a') == 'inner_a'
                assert low._lexical_name(id(root.scope), 'a') == 'root_a'
                assert not inner.return_facts and not inner.substitutions
                if cancel:
                    raise Cancelled()
            assert low.activation is outer
            assert low._lexical_name(id(fn), 'a') == 'outer_a'
            assert outer.return_facts == ['outer-fact']

    if cancel:
        with pytest.raises(Cancelled):
            call()
    else:
        call()
    assert low.activation is root
    assert all(getattr(root, name) is value for name, value in retained.items())
    assert set(root.substitutions) == {'self'}
    assert root.substitutions['self'] is caller_value
    assert root.defined == {'nested'} and root.return_facts == []
    assert lowerer([]).activation.substitutions == {}
    assert not set(vars(low)) & {
        '_rename', '_frame_scopes', 'scope_stack', '_summary_contexts',
        '_seen_defs', '_helper_return_facts', '_subst', '_subst_contracts',
        '_ret', '_hoisted'}


def _source(helper, expression, *, prefix="", method=False):
    body = "\n".join("    " + line for line in helper.splitlines()) if method else ""
    return prefix + ("" if method else helper + "\n") + f'''class M:
    def reset(self):
        self.x = 0
    def bump(self):
        self.x = (self.x + 1) & 15
        return self.x
{body}
    def process(self, a):
        return {{"y": ({expression}) & 65535}}
'''


CASES = [
    _source("def signed(v, width=4):\n    if v & (1 << (width - 1)):\n        return v - (1 << width)\n    return v",
            "signed(a) + 16"),
    _source("def mix(a, b=2, *, c=3):\n    return a + 10*b + 100*c",
            "mix(c=4, a=a)"),
    _source("def mix(a, /, b=2, *, c):\n    return a + 10*b + 100*c",
            "mix(a, c=3)"),
    _source("def mix(a, b, *, c=2):\n    return a + 10*b + 100*c",
            "mix(*(a, 3), c=4)"),
    _source("def mix(self, a, b=2):\n    return a + 10*b",
            "self.mix(b=self.bump(), a=self.x)", method=True),
    _source("def mix(self, a, b=2):\n    return a + 10*b",
            "self.mix(self.x, b=self.bump())", method=True),
    _source("def mix(self, a, b=2):\n    return a + 10*b",
            "self.mix(a=self.x, b=self.bump())", method=True),
    _source("def mix(self, a, b=2):\n    return a + 10*b",
            "self.mix(self.bump(), b=self.bump())", method=True),
    _source("def mix(self, a, b=2):\n    return a + 10*b",
            "self.mix(*(self.x,), b=self.bump())", method=True),
    _source("def mix(self, a, b=2):\n    return a + 10*b",
            "self.mix(self.mix(self.x, b=self.bump()), b=self.bump())", method=True),
    _source("def mix(a, b=3):\n    return a+b\nN = 11", "mix(a)", prefix="N = 3\n"),
    _source("def mix(a, b=N):\n    return a+b\nN = 11", "mix(a)", prefix="N = 3\n"),
    _source("N = 5\ndef mix(self, a, b=N):\n    return a+b\nN = 11",
            "self.mix(a)", method=True),
    _source("@staticmethod\ndef mix(a, *, b=3):\n    return a+b",
            "self.mix(a)", method=True),
    _source("def mix(self, a, /, b=3):\n    return a+b",
            "self.mix(a)", method=True),
    """class M:
    def reset(self):
        pass
    def process(self, a):
        def add(v, b=3):
            return v+b
        b = 13
        return {"y": add(a)}
""",
    _source("def mix(a, b, c, d, e):\n    return a + 16*b + 256*c + 4096*d + e",
            "mix(self.x, self.bump(), self.x, self.bump(), a)"),
    _source("def mix(a, b, *, c, d):\n    return a + 16*b + 256*c + 4096*d",
            "mix(*(self.x, self.bump()), d=self.bump(), c=self.x)"),
    _source("def len(self, v):\n    self.x = (self.x + v) & 15\n    return self.x",
            "sum((self.x, self.len(a), self.x, self.bump(), a))", method=True),
    _source("def read(self, v):\n    return self.identity(v)\n"
            "def identity(self, v):\n    return self.x + v",
            "sum((self.read(a), self.bump(), self.read(a), a))", method=True),
    _source("def mix(a, b, c):\n    return a + 16*b + 256*c",
            "mix(self.x, self.bump() if a & 1 else a, a)"),
]


def _schema():
    return TransactionSchema(
        [Endpoint("a", "input", 4), Endpoint("y", "output", 16)],
        authority=Authority.TASK, reading=Reading.ELABORATED)


@pytest.mark.solver
@pytest.mark.parametrize("lowerer", [GraphLowerer, _Lowerer], ids=["graph", "term"])
@pytest.mark.parametrize("source", CASES, ids=[
    "default-width", "keywords", "positional-only", "fixed-star",
    "keyword-effects", "positional-before-keyword-effect",
    "keyword-before-keyword-effect", "two-effectful-arguments",
    "star-before-keyword-effect", "nested-effectful-arguments", "literal-default",
    "module-definition-time", "class-definition-time", "static-method",
    "positional-only-method", "nested-literal-default",
    "interleaved-effects-and-pure-tail", "starred-and-keyword-effects",
    "shadowed-builtin-effect", "nested-pure-reads-before-write",
    "conditional-effect",
])
def test_fixed_helper_bindings_match_python_outputs_state_and_native_hits(source, lowerer):
    plan = plan_source(source_model(source), source)
    exact = lower(source, _schema(), bound=1, lowerer=lowerer)
    assert exact.frontend_catalogue_manifest() == plan.manifest
    namespace, _, native_hits = instrumented.load_source(source, goal_plan=plan)
    plain_namespace = {}
    exec(compile(source, "behavior.py", "exec"), plain_namespace)  # noqa: S102 -- trusted native oracle
    native, plain = namespace["M"](), plain_namespace["M"]()
    native.reset()
    plain.reset()
    state = None
    for a in range(16):
        native_hits.clear()
        expected = plain.process(a)
        assert native.process(a) == expected
        state, output, hits, error = interp.run_step(exact.proc, {"a": a}, state)
        assert not error
        assert output.buffers["y"] == [expected["y"]]
        assert hits == set(native_hits)


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('repetitions', [1, 16, 128])
def test_helper_renaming_allocates_once_per_distinct_binding(monkeypatch, lowerer, repetitions):
    low = lowerer(())
    source = ('def repeated(v):\n' + '    v = v + 1\n    scratch = v\n' * repetitions
              + '    return scratch\n')
    function = ast.parse(source).body[0]
    call = ast.parse('repeated(2)', mode='eval').body

    class BindingReached(Exception):
        pass

    def stop_at_binding(*args, **kwargs):
        raise BindingReached

    monkeypatch.setattr(low, '_bind_inline_argument', stop_at_binding)
    with pytest.raises(BindingReached):
        low._inline_scoped(call, function, ['v'], [(ir.Lit(2), call.args[0], SCALAR, None)], ())
    assert len(low.storage.temporaries) == 2  # v and scratch, not one slot per assignment.
    assert low.storage._next == 2


@pytest.mark.parametrize('lowerer', [_Lowerer, GraphLowerer])
@pytest.mark.parametrize('repetitions', [1, 8])
def test_repeated_helper_assignments_keep_outputs_and_goal_catalogue(lowerer, repetitions):
    helper = ('def repeated(v):\n' + '    v = (v + 1) & 15\n    scratch = v\n' * repetitions
              + '    return scratch\n')
    source = _source(helper, 'repeated(a)')
    test_fixed_helper_bindings_match_python_outputs_state_and_native_hits(source, lowerer)


def test_sibling_capture_matches_every_suffix_without_repeated_effect_queries():
    low = object.__new__(_Lowerer)
    for length in range(8):
        for effects in product((False, True), repeat=length):
            queries, operations = [], []

            def effectful(index, queries=queries, effects=effects):
                queries.append(index)
                return effects[index]

            def evaluate(index, operations=operations):
                operations.append(("evaluate", index))
                return index

            def capture(index, operations=operations):
                operations.append(("capture", index))
                return index

            low._effectful = effectful
            low._capture = capture
            assert low._in_order(*range(length), lower=evaluate) == list(range(length))
            expected = []
            for index in range(length):
                expected.append(("evaluate", index))
                if any(effects[index + 1:]):
                    expected.append(("capture", index))
            assert operations == expected
            assert len(queries) == len(set(queries))
            assert 0 not in queries


def test_wide_pure_operands_are_classified_once_each():
    low = _Lowerer([])
    nodes = [ast.parse(f"a + {index}", mode="eval").body for index in range(128)]
    queries = []

    def effectful(node):
        queries.append(node)
        return _Lowerer._effectful(low, node)

    low._effectful = effectful
    assert low._in_order(*nodes, lower=lambda node: node) == nodes
    assert queries == list(reversed(nodes[1:]))


@pytest.mark.parametrize("lowerer", [GraphLowerer, _Lowerer], ids=["graph", "term"])
@pytest.mark.parametrize("failure", [None, "return-contract", "return-kind", "body"])
@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
def test_helper_frame_restores_caller_after_preparation_or_body_failure(lowerer, failure, nested):
    source = '''class M:
    def reset(self): pass
    def pair(self, value):
        return value == 0, value + 1
    def process(self, a):
        flag, value = self.pair(a)
        return {"y": value if flag else a}
'''
    if nested:
        source = source.replace("def process(self, a):", "def outer(self, a):")
        source = source.replace('return {"y": value if flag else a}',
                                'return value if flag else a')
        source += '    def process(self, a):\n        return {"y": self.outer(a)}\n'
    frames = []

    def image(low):
        fields = ("scope", "summary", "renames", "defined", "return_facts",
                  "hoisted", "destination", "substitutions", "substitution_contracts")
        return tuple((id(frame), tuple(id(getattr(frame, name)) for name in fields))
                     for frame in low.activation.frames())

    class Tracked(lowerer):
        def _inline_scoped(self, node, fn, *args, **kwargs):
            before = image(self)
            if failure == "return-kind" and fn.name == "pair":
                kwargs["result_kind"] = "invalid-test-kind"
            try:
                return super()._inline_scoped(node, fn, *args, **kwargs)
            finally:
                frames.append((fn.name, before, image(self)))

        def _planned_call_contract(self, node, *args, **kwargs):
            planned = super()._planned_call_contract(node, *args, **kwargs)
            return (SCALAR if failure == "return-contract"
                    and getattr(node.func, "attr", None) == "pair" else planned)

        def _inlined(self, node, fn, bind):
            if failure == "body" and fn.name == "pair":
                raise RuntimeError("injected-helper-body-failure")
            return super()._inlined(node, fn, bind)

    if failure is None:
        proc = lower_procedure(source, input_widths=[2], outputs=["y"], lowerer=Tracked)
        for value in range(4):
            _, output, _, error = interp.run_step(proc, {"a": value})
            assert not error
            assert output.transactions == [{"y": 1 if value == 0 else value}]
    else:
        exception = {"return-contract": LoweringError,
                     "return-kind": ValueError, "body": RuntimeError}[failure]
        with pytest.raises(exception) as caught:
            lower_procedure(source, input_widths=[2], outputs=["y"], lowerer=Tracked)
        if failure == "return-contract":
            assert caught.value.code == "helper-return-shape-disagrees"
        elif failure == "return-kind":
            assert "invalid-test-kind" in str(caught.value)
        else:
            assert str(caught.value) == "injected-helper-body-failure"
    assert {name for name, _, _ in frames} == ({"pair", "outer"} if nested else {"pair"})
    assert all(before == after for _, before, after in frames)


@pytest.mark.solver
def test_default_helper_every_goal_query_has_native_witness_or_local_unsat():
    import z3

    source = CASES[0]
    plan = plan_source(source_model(source), source)
    exact = lower(source, _schema(), bound=1)
    namespace, _, hits = instrumented.load_source(source, goal_plan=plan)
    model = namespace["M"]()
    model.reset()
    oracle = []
    for a in range(16):
        hits.clear()
        model.process(a)
        oracle.append(frozenset(hits))
    encoded = encoder.encode_step(exact.proc, env=exact.env)
    solver = z3.Solver()
    solver.add(encoded.constraint, z3.ULE(encoded.pre["a"], 15))
    assert set(plan.manifest) <= encoded.hits.keys()
    for goal in plan.manifest:
        solver.push()
        solver.add(encoded.hits[goal])
        possible = any(goal in row for row in oracle)
        assert solver.check() == (z3.sat if possible else z3.unsat), goal
        if possible:
            a = solver.model().eval(encoded.pre["a"]).as_long()
            hits.clear()
            model.process(a)
            assert goal in hits
        solver.pop()


@pytest.mark.solver
@pytest.mark.parametrize("helper,call,code", [
    ("def f(a, b=2):\n    return a+b", "f(a, a=2)", "call-argument-form"),
    ("def f(a, /):\n    return a", "f(a=a)", "call-argument-form"),
    ("def f(a, *, b):\n    return a+b", "f(a)", "call-arity"),
    ("def f(a):\n    return a", "f(a, typo=1)", "call-argument-form"),
    ("def f(a, b=[]):\n    return a+len(b)", "f(a)", "call-default-unrepresented"),
])
def test_invalid_or_unrepresented_bindings_are_not_guessed(helper, call, code):
    with pytest.raises(LoweringError) as error:
        lower(_source(helper, call), _schema(), bound=1)
    assert error.value.code == code


def test_binding_reorders_values_only_after_the_caller_evaluated_them():
    function = ast.parse("def f(a, /, b=2, *, c=3): pass").body[0]
    names, bound = bind_helper_arguments(
        function, [10], [("c", 30), ("b", 20)],
        default=lambda name, node: node.value)
    assert names == ("a", "b", "c")
    assert bound == (10, 20, 30)


@pytest.mark.parametrize("signature", [
    "a, b=2, c=3", "a, /, b=2, *, c=3", "a=1, /, *, b, c=3",
    "*, a=1, b=2, c=3",
])
def test_fixed_signature_binder_matches_python_argument_binding(signature):
    source = f"def f({signature}): pass"
    namespace = {}
    exec(source, namespace)  # noqa: S102 -- fixed signature fixtures, Python binding oracle
    python = inspect.signature(namespace["f"])
    function = ast.parse(source).body[0]
    for length in range(5):
        positional = tuple(range(10, 10 + length))
        for count in range(5):
            for names in combinations(("a", "b", "c", "unknown"), count):
                keywords = dict(zip(names, range(20, 20 + count)))
                try:
                    expected = python.bind(*positional, **keywords)
                except TypeError:
                    with pytest.raises(CallBindingError):
                        bind_helper_arguments(
                            function, positional, keywords.items(),
                            default=lambda name, node: node.value)
                else:
                    expected.apply_defaults()
                    names, values = bind_helper_arguments(
                        function, positional, keywords.items(),
                        default=lambda name, node: node.value)
                    assert dict(zip(names, values)) == expected.arguments
