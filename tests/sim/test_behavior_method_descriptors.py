"""Standard helper descriptors retain Python binding and source-event identity."""

import pytest

from behave.sim import api
from behave.sim.symbolic import interp
from behave.sim.behavior.extract import LoweringError, ScopeResolver, source_model
from behave.sim.behavior.instrumented import attribute_safety_failure, load_source
from behave.sim.behavior.isolated import Broken, IsolatedModel, hits_of
from behave.sim.behavior.lowering import lower_procedure
from behave.sim.behavior.method_binding import class_member_name, method_kind, method_parameters
from behave.sim.behavior.source_plan import plan_source

SOURCE = """class M:
    K = 7
    def reset(self):
        self.K = 99
    @staticmethod
    def add(self, value):
        if self > 0:
            return self + value
        return value
    @classmethod
    def wrap(klass, value):
        return klass.add(value, klass.K)
    def process(self, a):
        return {'y': self.wrap(a)}
"""


@pytest.mark.parametrize("receiver", ("self", "M"))
@pytest.mark.parametrize("packaged", (False, True))
def test_source_linking_and_python_descriptor_binding(receiver, packaged):
    source = SOURCE.replace("self.wrap(a)", f"{receiver}.wrap(a)")
    selected, file = "M", "behavior.py"
    if packaged:
        prepared = api.prepare_behavior_bundle(api.BehaviorSourceBundle(
            {"entry.py": b"from model import M as Selected\n", "model.py": source.encode()},
            "entry.py", "Selected"))
        source, selected, file = prepared.source, prepared.model, prepared.file
    model = source_model(source, file, cls=selected)
    resolver = ScopeResolver(model)
    assert {fn.name for fn in resolver.reachable_functions((model.step,))} == {
        "process", "wrap", "add"}
    plan = plan_source(model, source, file, resolver=resolver)
    namespace, _, hits = load_source(source, file, goal_plan=plan)
    instance = namespace[selected]()
    instance.reset()
    assert instance.process(3) == {"y": 10}  # class K, not instance K=99
    assert instance.process(0) == {"y": 7}
    assert set(hits) == {goal for site in plan.coverage_sites for goal in site.goals}
    assert method_parameters(model.helpers["add"]) == ("self", "value")
    assert method_parameters(model.helpers["wrap"]) == ("value",)


@pytest.mark.parametrize("source", (
    SOURCE.replace("@staticmethod", "@custom"),
    SOURCE.replace("@staticmethod", "@staticmethod()"),
    SOURCE.replace("@staticmethod", "@classmethod\n    @staticmethod"),
    "staticmethod = lambda fn: fn\n" + SOURCE,
    SOURCE.replace("    K = 7", "    staticmethod = 3\n    K = 7"),
))
def test_custom_called_stacked_or_shadowed_decorators_are_not_builtin_descriptors(source):
    with pytest.raises(LoweringError) as error:
        source_model(source)
    assert error.value.code == "method-decorated"


def test_deleted_class_shadow_uses_the_module_builtin_again():
    source = SOURCE.replace("    K = 7", "    staticmethod = 3\n    del staticmethod\n    K = 7")
    assert method_kind(source_model(source).helpers["add"]) == "staticmethod"


def test_class_receiver_shadowing_is_lexical():
    source = SOURCE.replace("return klass.add(value, klass.K)",
                            "klass = value\n        return klass.add(1, 2)")
    model = source_model(source)
    resolver = ScopeResolver(model)
    assert not resolver.class_receiver(model.helpers["wrap"], "klass")
    assert {fn.name for fn in resolver.reachable_functions((model.step,))} == {
        "process", "wrap"}


def test_reset_keeps_the_declared_instance_calling_convention():
    with pytest.raises(LoweringError) as error:
        source_model(SOURCE.replace("    def reset(self):", "    @staticmethod\n    def reset():"))
    assert error.value.code == "method-entry-descriptor-unsupported"


@pytest.mark.parametrize("parameter", ("a", "self"))
@pytest.mark.parametrize("typed", (False, True))
def test_static_entry_preserves_every_explicit_input_and_ir_goal(parameter, typed):
    from behave.sim.behavior.signature import ProcedureSignature
    from behave.sim.core.contract import FiniteType

    source = f"""class M:
    def reset(self): pass
    @staticmethod
    def process({parameter}):
        if {parameter} > 3:
            return {{'y': {parameter} + 1}}
        return {{'y': 0}}
"""
    model = source_model(source)
    assert model.inputs == [parameter]
    plan = plan_source(model, source)
    namespace, _, hits = load_source(source, "behavior.py", goal_plan=plan)
    native = namespace["M"]()
    options = {"signature": ProcedureSignature(
        ((parameter, FiniteType(8)),), (("y", FiniteType(9)),))} if typed else {"input_widths": [8]}
    procedure = lower_procedure(source, outputs=["y"], **options)
    for value in (0, 3, 4, 255):
        hits.clear()
        _, output, observed, error = interp.run_step(procedure, {parameter: value})
        assert not error
        assert output.transactions == [native.process(**{parameter: value})]
        assert observed == set(hits)


@pytest.mark.parametrize("owner", ("M", "_M", "__M", "_"))
@pytest.mark.parametrize("name", ("__helper", "_helper", "__repr__"))
def test_class_member_spelling_matches_python(owner, name):
    namespace = {}
    exec(f"class {owner}:\n    def {name}(self): pass\n", namespace)  # noqa: S102 - test-owned fixture
    key = class_member_name(owner, name)
    function = namespace[owner].__dict__[key]
    assert function.__code__.co_qualname == f"{owner}.{name}"


PRIVATE_SOURCE = """class M:
    __K = 7
    def reset(self): pass
    @staticmethod
    def __helper(value):
        if value > 3:
            return value + 1
        return 0
    def process(self, a): return {'y': self.__helper(a) + self.__K}
"""


@pytest.mark.cosim
@pytest.mark.parametrize("owner", ("M", "_M", "_"))
@pytest.mark.parametrize("packaged", (False, True))
def test_isolated_private_helpers_constants_and_native_hits(owner, packaged):
    source = PRIVATE_SOURCE.replace("class M:", f"class {owner}:")
    file = "behavior.py"
    if packaged:
        bundle = api.BehaviorSourceBundle(
            {"entry.py": f"from model import {owner} as Selected\n".encode(),
             "model.py": source.encode()}, "entry.py", "Selected")
        if owner == "_":
            from behave.sim.core.refusal import Refused

            with pytest.raises(Refused, match="private-class-name"):
                api.prepare_behavior_bundle(bundle)
            return
        prepared = api.prepare_behavior_bundle(bundle)
        source, owner, file = prepared.source, prepared.model, prepared.file
    plan = plan_source(source_model(source, file), source, file)
    namespace, _, expected_hits = load_source(source, file, goal_plan=plan)
    native = namespace[owner]()
    procedure = lower_procedure(source, file=file, cls=owner, input_widths=[8], outputs=["y"])
    with IsolatedModel(source, file=file, cls=owner) as model:
        model.reset()
        for value in (0, 4):
            expected_hits.clear()
            hits_of(model).clear()
            expected = native.process(value)
            assert model.process(a=value) == expected
            assert set(hits_of(model)) == set(expected_hits)
            _, output, observed, error = interp.run_step(procedure, {"a": value})
            assert not error
            assert output.transactions == [expected]
            assert observed == set(expected_hits)


@pytest.mark.cosim
def test_private_helper_runtime_replacement_is_still_rejected():
    source = PRIVATE_SOURCE + "\ndef other(value): return 0\nM._M__helper = staticmethod(other)\n"
    with pytest.raises((Broken, ValueError)), IsolatedModel(source, cls="M"):
        pass


@pytest.mark.cosim
@pytest.mark.parametrize("kind, arguments, expression", (
    ("staticmethod", "a", "a + 7"),
    ("classmethod", "klass, a", "a + klass.K"),
))
def test_isolated_entry_descriptors_and_exact_capability(kind, arguments, expression):
    source = f"""class M:
    K = 7
    def reset(self): self.K = 99
    @{kind}
    def process({arguments}): return {{'y': {expression}}}
"""
    assert source_model(source).inputs == ["a"]
    with IsolatedModel(source, cls="M") as model:
        model.reset()
        assert model.process(a=3) == {"y": 10}
    if kind == "classmethod":
        with pytest.raises(LoweringError) as error:
            lower_procedure(source, input_widths=[8], outputs=["y"])
        assert error.value.code == "class-method-lowering-unsupported"


@pytest.mark.cosim
@pytest.mark.parametrize("parameter", ("self", "method"))
def test_task_input_names_do_not_collide_with_transport_receivers(parameter):
    source = f"""class M:
    def reset(self): pass
    @staticmethod
    def process({parameter}): return {{'y': {parameter} + 1}}
"""
    with IsolatedModel(source, cls="M") as model:
        model.reset()
        assert model.process(**{parameter: 3}) == {"y": 4}


def test_static_numeric_helper_lowers_without_dropping_a_parameter_named_self():
    source = """class M:
    def reset(self): pass
    @staticmethod
    def helper(self, other): return self + other
    @staticmethod
    def zero(): return 0
    def process(self, a): return {'y': self.helper(a, 3) + self.zero()}
"""
    procedure = lower_procedure(source, input_widths=[8], outputs=["y"])
    for value in (0, 1, 127, 255):
        _, output, _, error = interp.run_step(procedure, {"a": value})
        assert not error and output.transactions == [{"y": value + 3}]


def test_class_method_exact_limitation_does_not_block_source_admission():
    assert source_model(SOURCE).inputs == ["a"]
    with pytest.raises(LoweringError) as error:
        lower_procedure(SOURCE, input_widths=[8], outputs=["y"])
    assert error.value.code == "class-method-lowering-unsupported"


def test_static_helper_source_and_ir_observe_the_same_events_and_failure():
    source = """class M:
    def reset(self): pass
    @staticmethod
    def helper(self, value):
        if self > 3:
            return value + self
        return value // self
    def process(self, a): return {'y': self.helper(a, 10)}
"""
    plan = plan_source(source_model(source), source)
    namespace, _, hits = load_source(source, "behavior.py", goal_plan=plan)
    native = namespace["M"]()
    procedure = lower_procedure(source, input_widths=[8], outputs=["y"])
    for value in (1, 2, 4, 255, 0):
        hits.clear()
        _, output, observed, error = interp.run_step(procedure, {"a": value})
        if value:
            assert not error
            assert output.transactions == [native.process(value)]
        else:
            with pytest.raises(ZeroDivisionError) as failure:
                native.process(value)
            assert error == "DivZero"
            # Plain in-process instrumentation records structural events; the
            # isolated worker adds this shared traceback-based safety event.
            failed_goal = attribute_safety_failure(failure.value, plan.safety_sites)
            assert failed_goal is not None
            hits.append(failed_goal)
        assert observed == set(hits)


@pytest.mark.cosim
def test_isolated_execution_accepts_both_descriptors():
    with IsolatedModel(SOURCE, cls="M") as model:
        model.reset()
        assert model.process(a=3) == {"y": 10}
        assert model.process(a=0) == {"y": 7}


@pytest.mark.cosim
def test_changing_descriptor_kind_is_not_accepted_as_the_same_method():
    source = SOURCE + "\nM.wrap = staticmethod(M.wrap.__func__)\n"
    with pytest.raises((Broken, ValueError)), IsolatedModel(source, cls="M"):
        pass


def test_public_environment_reports_the_shared_descriptor_support():
    environment = api.behavior_source_environment()
    assert environment["method_decorators"] == ["classmethod", "property", "staticmethod"]
    assert set(environment["method_decorators"]) <= set(environment["builtins"])
    assert "helpers and task steps" in environment["description"]
    assert "reset/construction methods remain instance methods" in environment["description"]
