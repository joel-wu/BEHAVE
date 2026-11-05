"""Annotated statements keep Python's runtime events, not the type expression's."""

import textwrap

import pytest

from behave.sim.behavior import instrumented
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import ScopeResolver, source_model
from behave.sim.behavior.lowering import LoweringError, _Lowerer, lower_with_state_ranges
from behave.sim.behavior.source_plan import plan_source
from behave.sim.behavior.step_alias import step_alias_hazard


def source(body, *, helpers="", reset="pass", module=""):
    return (module + "\nclass M:\n"
            + "    def reset(self):\n" + textwrap.indent(reset, "        ") + "\n"
            + textwrap.indent(textwrap.dedent(helpers), "    ")
            + "\n    def process(self, a):\n"
            + textwrap.indent(textwrap.dedent(body).strip(), "        ") + "\n")


def agrees(text, inputs=(0, 1, 7), *, lowerer=None):
    namespace = {}
    exec(compile(text, "behavior.py", "exec", dont_inherit=True), namespace)  # noqa: S102
    plain = namespace["M"]()
    plain.reset()
    prepared = plan_source(source_model(text), text)
    observed, _sites, hits = instrumented.load_source(text, goal_plan=prepared)
    native = observed["M"]()
    native.reset()
    proc = lower_with_state_ranges(text, input_widths=[3], lowerer=lowerer)
    state = None
    for value in inputs:
        hits.clear()
        expected = plain.process(value)
        assert native.process(value) == expected
        state, output, actual_hits, error = interp.run_step(proc, {"a": value}, state)
        assert not error
        assert output.transactions == [expected]
        assert actual_hits == set(hits)
    return prepared, proc


@pytest.mark.parametrize("lowerer", [None, _Lowerer])
@pytest.mark.parametrize("body", [
    "x: int = a + 1\nreturn {'y': x}",
    "x: bool = a + 1\nreturn {'y': x}",
    "a: int = a + 1\nreturn {'y': a}",
    "x: int\nx = a + 1\nreturn {'y': x}",
    "x = a + 1\nx: int\nreturn {'y': x}",
    "x: list[int] = [a, 2]\nx: list[int]\nx[0] += 1\nreturn {'y': x[0]}",
    "x: dict[str, int] = {'y': a}\nx: dict[str, int]\nreturn x",
    "x: int = 0\nfor i in range(3):\n    x: int = x + a\nreturn {'y': x}",
])
def test_annotated_values_share_assignment_lowering(body, lowerer):
    agrees(source(body), lowerer=lowerer)


def test_annotation_expressions_are_not_executed_or_declared_as_goals():
    text = source("""
        x: (self.unused(a) if a else (8 // 0)) = a + 1
        x: list[1 << -1]
        return {'y': x}
    """, helpers="""
        def unused(self, a):
            self.state = 99
            return 5 if a else 0
    """, reset="self.state = 0")
    prepared, _proc = agrees(text)
    assert len(prepared.manifest) == 3
    assert {goal.family for goal in prepared.manifest.values()} == {"location"}
    assert [site.kind for site in prepared.coverage_sites] == ["annassign", "annassign", "return"]
    assert not prepared.safety_sites


def test_unevaluated_annotation_is_not_the_same_as_an_unreachable_runtime_goal():
    text = source("""
        x: (1 // 0) = a
        if False:
            x = 2 // 0
        return {'y': x}
    """)
    prepared, _proc = agrees(text)
    assert len(prepared.safety_sites) == 1
    runtime_failure = prepared.safety_sites[0]
    assert text.splitlines()[runtime_failure.line - 1].strip() == "x = 2 // 0"
    assert {site.family for site in prepared.coverage_sites} == {"location", "decision"}


def test_annotation_preserves_helper_closure_and_state_updates():
    agrees(source("""
        x: int = a + 1
        def helper():
            y: int = x + self.total
            return y
        self.total: int = helper()
        return {'y': self.total}
    """, reset="self.total: int = 0"))


@pytest.mark.parametrize("target", ["self.missing", "self.buf[99]", "self.buf[0:1:0]"])
def test_annotation_only_target_neither_stores_nor_reads_the_final_item(target):
    text = source(f"{target}: int\nreturn {{'y': a}}", reset="self.buf = [0]")
    prepared, _proc = agrees(text)
    # Only the evaluated receiver's field read owes a site; the final item owes none.
    assert {site.kind for site in prepared.safety_sites} <= {"attributeerror"}
    assert "missing" not in source_model(text).state_contract()


def test_annotation_only_target_evaluates_receiver_and_index_in_order():
    agrees(source("""
        self.base()[self.index()]: int
        return {'y': self.order}
    """, reset="self.order = 0\nself.buf = [1]", helpers="""
        def base(self):
            self.order = self.order * 10 + 1
            return self.buf
        def index(self):
            self.order = self.order * 10 + 2
            return 999
    """), inputs=(0,))


def test_annotation_does_not_discard_a_live_alias():
    text = source("""
        x = self.buf
        x: list[int]
        self.buf[0] = a
        return {'y': x[0]}
    """, reset="self.buf = [1]")
    model = source_model(text)
    assert step_alias_hazard(model, {"buf"}) is not None
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(text, input_widths=[3])
    assert caught.value.code == "array-assigned-by-reference"


def test_annotation_target_effect_cannot_erase_aliases_as_a_fictitious_store():
    text = source("""
        x = self.buf
        self.buf[0]: int
        self.buf[0] = a
        return {'y': x[0]}
    """, reset="self.buf = [1]")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(text, input_widths=[3])
    assert caught.value.code == "array-assigned-by-reference"


@pytest.mark.parametrize("body", [
    "x: int\nreturn {'y': x}",
    "if a:\n    x: int = 3\nreturn {'y': x}",
    "return {'y': x}\nx: int",
    "x: (z := int) = a\nreturn {'y': z}",
])
def test_annotation_does_not_initialize_a_lexical_local(body):
    text = source(body, module="x = 9\nz = 8")
    namespace = {}
    exec(compile(text, "behavior.py", "exec", dont_inherit=True), namespace)  # noqa: S102
    with pytest.raises(UnboundLocalError):
        namespace["M"]().process(0)
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(text, input_widths=[3])
    assert caught.value.code == "variable-may-be-unassigned"


def test_annotation_only_parameter_is_not_a_write():
    text = source("a: int\nself: M\nreturn {'y': a}")
    model = source_model(text)
    resolver = ScopeResolver(model)
    assert resolver.written_parameters(model.step) == frozenset()
    assert not resolver.scope_rebinds_self(model.step)
    agrees(text)


def test_annotation_only_nested_function_keeps_its_binding():
    agrees(source("""
        def f(x):
            return x + 1
        f: object
        return {'y': f(a)}
    """))


@pytest.mark.parametrize("value", ["", " = a"])
def test_annotation_walrus_declares_names_without_runtime_rebinding(value):
    text = source(f"""
        x: (self := 0){value}
        y: (a := 0){value}
        z: (f := 0){value}
        def f(b):
            return b + self.total
        return {{'y': f(a)}}
    """, reset="self.total = 2")
    model = source_model(text)
    resolver = ScopeResolver(model)
    assert not resolver.written_parameters(model.step)
    agrees(text)


def test_annotation_reference_is_not_a_call_before_a_nested_definition():
    agrees(source("""
        x: f(a) = a
        def f(b):
            return b + 1
        return {'y': f(x)}
    """))


def test_parenthesized_annotation_only_name_is_not_a_local_declaration():
    agrees(source("(x): int\nreturn {'y': x + a}", module="x = 9"))
    agrees(source("""
        def f(a):
            (x): int
            return x + a
        return {'y': f(a)}
    """, module="x = 9"))


def test_an_annotation_reference_does_not_keep_an_unused_alias_live_in_transfer():
    text = source("""
        unused = self.buf
        self.buf[0] = a
        annotation: unused
        return {'y': a}
    """, reset="self.buf = [0]")
    # Isolate liveness in the points-to authority. The older lowering-local
    # sharing guard still conservatively refuses even the unannotated form.
    assert step_alias_hazard(source_model(text), {"buf"}) is None


def test_admission_does_not_require_execution_rules_for_a_local_annotation():
    from behave.sim.behavior import admission

    text = source("x: UnknownType(a) = a + 1\nreturn {'y': x}")
    assert admission.check(text) == []
    assert admission.calls_this_build_will_not_answer(text) == []
    agrees(text)


def test_annotated_store_evaluates_rhs_before_target():
    agrees(source("""
        self.buf[self.index]: int = self.value(a)
        return {'y': self.buf[0] * 10 + self.buf[1]}
    """, reset="self.index = 0\nself.buf = [0, 0]", helpers="""
        def value(self, a):
            self.index = 1
            return a
    """), inputs=(3,))


def test_annotated_store_retains_the_existing_effectful_target_boundary():
    text = source("""
        self.buf[self.index()]: int = self.value(a)
        return {'y': self.order * 10 + self.buf[0]}
    """, reset="self.order = 0\nself.buf = [0]", helpers="""
        def value(self, a):
            self.order = self.order * 10 + 1
            return a
        def index(self):
            self.order = self.order * 10 + 2
            return 0
    """)
    for variant in (text, text.replace(": int =", " =")):
        namespace = {}
        exec(compile(variant, "behavior.py", "exec", dont_inherit=True), namespace)  # noqa: S102
        native = namespace['M']()
        native.reset()
        assert native.process(3) == {'y': 123}
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(variant, input_widths=[3])
        assert caught.value.code == "subscript-target-effectful"


def test_annotation_only_slice_evaluates_all_bounds_without_applying_it():
    agrees(source("""
        self.buf[self.tick(1):self.tick(2):self.tick(0)]: int
        return {'y': self.order}
    """, reset="self.order = 0\nself.buf = [0]", helpers="""
        def tick(self, a):
            self.order = self.order * 10 + a
            return a
    """), inputs=(3,))


def test_annotation_target_retains_inner_index_failures_but_not_a_final_access():
    text = source("self.buf[0][99]: int\nreturn {'y': a}", reset="self.buf = [[1]]")
    prepared, _proc = agrees(text)
    # buf[0] is read; [99] is not. Reading the field itself owes its own site.
    assert sorted(site.kind for site in prepared.safety_sites) == ["attributeerror", "indexerror"]


@pytest.mark.solver
@pytest.mark.parametrize("body", [
    "x: int = 1 if a else 2\nreturn {'y': x}",
    "x: (8 // 0) = [5, 6][a] // a\nreturn {'y': x}",
    "self.buf[8 // a]: int\nreturn {'y': a}",
    "x: int = 0\nfor i in range(a):\n    x: int = x + i\nreturn {'y': x}",
])
def test_annotation_native_ir_and_symbolic_hits_agree(body):
    z3 = pytest.importorskip("z3")
    from behave.sim.symbolic import encoder, plan, ranges

    text = source(body, reset="self.buf = [1]")
    prepared = plan_source(source_model(text), text)
    namespace, _sites, hits = instrumented.load_source(text, goal_plan=prepared)
    native = namespace["M"]()
    native.reset()
    proc = lower_with_state_ranges(text, input_widths=[2])
    env = {**proc.state_ranges, "a": ranges.Range(0, 3)}
    encoding_plan = plan.plan_encoding(proc, env)
    assert encoding_plan.complete, encoding_plan.unresolved
    encoded = encoder.encode_step(proc, env=env, plan=encoding_plan)
    for value in range(4):
        hits.clear()
        failure = None
        try:
            expected = native.process(value)
        except (ZeroDivisionError, IndexError) as error:
            failure = instrumented.attribute_safety_failure(error, prepared.safety_sites)
            assert failure is not None
        expected_hits = set(hits) | ({failure} if failure else set())
        _state, output, ir_hits, error = interp.run_step(proc, {"a": value})
        assert bool(error) == bool(failure)
        if failure is None:
            assert output.transactions == [expected]
        assert ir_hits == expected_hits
        solver = z3.Solver()
        solver.add(encoded.constraint, encoded.pre["a"] == value)
        assert solver.check() == z3.sat
        assignment = solver.model()
        assert z3.is_true(assignment.eval(encoded.ok)) == (failure is None)
        symbolic = {goal for goal, term in encoded.hits.items()
                    if z3.is_true(assignment.eval(term, model_completion=True))}
        assert symbolic == expected_hits


@pytest.mark.solver
def test_public_behavior_instance_accepts_annotations_and_audits_the_same_manifest():
    from behave.sim.core.audit import AuditCheckStatus

    from .test_encoding_plan import instance

    text = source("x: int = a + 1\nreturn {'y': x}")
    artifact = instance(text, bound=3)
    manifest = artifact.frontend_catalogue_manifest()
    assert [goal.schema.provenance['kind'] for goal in manifest.values()] == ["annassign", "return"]
    assert artifact.source_audit(([{'a': a} for a in (0, 1, 255)],)).status is AuditCheckStatus.AGREED
