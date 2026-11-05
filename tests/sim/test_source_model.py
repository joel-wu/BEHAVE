"""Which class and which method are being verified, decided once.

The question was answered in three places by three rules, and the failure that makes it
worth fixing is silent: verifying a different program than the one the harness runs
produces no error, only a wrong answer. `_find_method` took the first `process` of an
`ast.walk` while Python binds the last, so a source with two was lowered as one and run
as the other.

Every refusal below costs nothing on the 836 problems, measured: none defines `process`
in two classes, none defines a relevant method twice, none has a base class, a
decorator, a conditionally defined method, or a step taking anything but plain
positional parameters. They are here for the task that has not been written yet.
"""
import pytest

from behave.sim.behavior.extract import LoweringError, source_model


def _model(body="        return {'y': a}\n", extra="", cls="M", bases="",
           decorators=""):
    return (f"{decorators}class {cls}{bases}:\n"
            "    def __init__(self):\n"
            "        self.n = 0\n"
            "    def reset(self):\n"
            "        self.n = 0\n"
            f"{extra}"
            "    def process(self, a):\n"
            f"{body}")


def test_it_reads_the_one_class_and_the_one_step():
    got = source_model(_model())
    assert got.cls.name == "M"
    assert got.step.name == "process"
    assert got.inputs == ["a"]
    assert got.reset is not None and got.constructor is not None
    assert got.domains == [None]
    assert got.certified_subset() == []


def test_a_helper_is_a_helper_and_the_step_is_not_one():
    got = source_model(_model(extra="    def pick(self, v):\n"
                                    "        return v + 1\n"))
    assert set(got.helpers) == {"pick"}


def test_a_second_definition_of_the_step_is_refused():
    """Python binds the second and this read the first, so the lowered program and the
    program the harness runs were different programs. Nothing refused it, and nothing
    could have noticed: the audit compares the two readings written here, and both were
    of the same wrong one."""
    src = _model() + ("    def process(self, a):\n"
                      "        return {'y': a + 1}\n")
    with pytest.raises(LoweringError, match="defined twice"):
        source_model(src)


def test_two_classes_defining_the_step_are_refused_unless_the_task_names_one():
    src = _model(cls="First") + "\n\n" + _model(cls="Second")
    with pytest.raises(LoweringError, match="classes define process"):
        source_model(src)
    assert source_model(src, cls="Second").cls.name == "Second"
    with pytest.raises(LoweringError, match="no top-level class named"):
        source_model(src, cls="Third")


def test_what_the_contract_does_not_cover_is_refused_with_its_site():
    for source, why in (
        (_model(bases="(Base)"), "base class"),
        (_model(decorators="@thing\n"), "is decorated"),
        (_model(extra="    @custom\n    def helper():\n        return 1\n"),
         "is decorated"),
        (_model(body="        return {'y': a}\n").replace(
            "    def process(self, a):", "    def process(self, a, *rest):"),
         r"\*args"),
        (_model().replace("    def process(self, a):",
                          "    def process(self, a, **kw):"), r"\*\*kwargs"),
        (_model().replace("    def process(self, a):",
                          "    def process(self, a, *, b=0):"),
         "keyword-only"),
    ):
        with pytest.raises(LoweringError, match=why):
            source_model(source)


def test_a_method_defined_under_a_condition_is_refused():
    src = ("class M:\n"
           "    def reset(self):\n"
           "        self.n = 0\n"
           "    if True:\n"
           "        def process(self, a):\n"
           "            return {'y': a}\n")
    with pytest.raises(LoweringError, match="under a\n?\\s*condition|condition"):
        source_model(src)


def test_defaulted_parameters_are_allowed_because_nothing_reads_them():
    """Every input comes from the schema, so a default is never used. One model in the
    population has them, and refusing it would be a coverage decision taken for the
    look of the signature."""
    got = source_model(_model().replace("    def process(self, a):",
                                        "    def process(self, a=0):"))
    assert got.inputs == ["a"]


# ── multi-clock: read here, not certified yet ──

MULTI = ("class Fifo:\n"
         "    def __init__(self):\n"
         "        self.n = 0\n"
         "    def reset(self):\n"
         "        self.n = 0\n"
         "    def process_w(self, in_w):\n"
         "        return {'y': in_w}\n"
         "    def process_r(self, in_r):\n"
         "        return {'y': in_r}\n")


def test_a_model_that_steps_once_per_clock_domain_is_read_rather_than_missed():
    """Six of the population are this shape: an async FIFO, an async FILO, an ethernet
    MII transmitter, a synchronizer. They were refused with "no process method", which
    says the wrong thing: the method is not missing, the model steps in two domains.

    Read here and refused by `certified_subset`, because multi-clock is intended. The
    theory already accommodates it, a step not being required to be a cycle, so what is
    missing is the instantiation in which a step is one global event slot and the three
    conditions it needs. Putting the refusal in the reading would mean recognising the
    shape twice when that instantiation is written.
    """
    got = source_model(MULTI)
    assert got.cls.name == "Fifo"
    assert got.domains == ["r", "w"]
    assert set(got.steps) == {"r", "w"}
    assert got.helpers == {}

    outside = got.certified_subset()
    assert len(outside) == 1
    assert "2 clock domains" in outside[0] and "event-slot" in outside[0]

    # Asking for "the" step of a model with two is refused rather than answered with
    # whichever came first.
    with pytest.raises(LoweringError, match="2 step methods"):
        got.step


def test_an_exact_step_does_not_turn_prefixed_helpers_into_clock_domains():
    src = MULTI + ("    def process(self, a):\n"
                   "        return {'y': a}\n")
    model = source_model(src)
    assert model.domains == [None]
    assert model.step.name == "process"
    assert {"process_w", "process_r"} <= model.helpers.keys()


def test_the_lowering_refuses_a_multi_clock_model_saying_why():
    from behave.sim.behavior.lowering import lower_procedure

    with pytest.raises(LoweringError, match="clock domains"):
        lower_procedure(MULTI)


# ── the class the audit runs is the class the lowering was about ──

def test_guessing_the_class_picks_the_first_and_the_rule_refuses_instead():
    """Two places decided which class by two rules, and the difference is what happens
    when the source is ambiguous rather than which one is right.

    `instrumented._model_class` takes the first class in the namespace with `reset` and
    something beginning with `process`, so on two candidates it silently picks one. The
    rule refuses, because which of two classes a task means is not something to guess.
    That is the value: not a better guess, no guess.

    The name is passed on the certification path for the other half of it. The lowering
    reads a class and the audit runs one, and if those were decided separately a mix-up
    would have the audit comparing the lowered program against a different class and
    reporting it as a fidelity failure, which is the one direction that reads as the
    artifact's fault.
    """
    from behave.sim.behavior import instrumented

    src = _model(cls="First") + "\n\n" + _model(cls="Second")
    with pytest.raises(LoweringError, match="classes define process"):
        source_model(src)

    namespace, _sites, _hits = instrumented.load_source(src, "behavior.py")
    assert instrumented._model_class(namespace).__name__ == "First", \
        "guessing takes whichever came first"
    # And with a name, whichever the task said, which is what the lowering passes.
    for named in ("First", "Second"):
        assert instrumented._model_class(namespace, named).__name__ == named
        assert source_model(src, cls=named).cls.name == named


# ── B17: the scoring path decides it by the same rule ──
