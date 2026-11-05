"""How far this frontend gets with a model, as one of four answers.

M4. "Supported" is one boolean over a chain of quite different capabilities, and a
model that runs and instruments but does not bounded-encode is not unsupported: it still
scores and still takes concolic testing. What is not acceptable is the shape that
boolean produces -- a model called admitted and well formed, which then fails in the
middle of building a formula, so the failure reads as a defect of the model rather than
as a limit of this side.

The tiers are cumulative and each names what it buys:

  ``none``          the source is not in the admitted subset, or cannot be constructed
  ``concrete``      it constructs and a step runs, so it can be executed and compared
  ``instrumented``  its goals enumerate and a run reports them, so it can be scored
  ``bounded``       it lowers and encodes at a bound, so exact analysis can run

A model lands in exactly one: the highest gate it passes. Every gate is a question with
its own answer, so a model that stops is reported with the gate that stopped it rather
than with a traceback from wherever the chain happened to break.

The order is not arbitrary and is not a pipeline order either. It is by **what the tier
lets a reader do**, which is why `instrumented` sits below `bounded`: scoring needs the
goal set and a run, and asking a solver needs a formula, and the second is strictly more
than the first. Reporting the tier is what makes the population table a funnel rather
than a supported count with a footnote.
"""

TIERS = ("none", "concrete", "instrumented", "bounded")

__all__ = ["TIERS", "Tier", "tier_of"]


class Tier:
    """Which tier a model reached, and what stopped it going further."""

    __slots__ = ("detail", "name", "stopped_by")

    def __init__(self, name, stopped_by=None, detail=""):
        assert name in TIERS, name
        self.name = name
        #: The gate that did not open, or `None` at the top tier. Named rather than
        #: left to be inferred from an exception, which is the whole point: a model
        #: that does not encode has a reason and it is not "unsupported".
        self.stopped_by = stopped_by
        self.detail = detail

    @property
    def rank(self):
        return TIERS.index(self.name)

    def __ge__(self, other):
        return self.rank >= (other.rank if isinstance(other, Tier)
                             else TIERS.index(other))

    def as_dict(self):
        return {"tier": self.name, "stopped_by": self.stopped_by,
                "detail": self.detail}

    def __repr__(self):
        if self.stopped_by is None:
            return f"Tier({self.name})"
        return f"Tier({self.name}, stopped by {self.stopped_by})"


def tier_of(source, schema, file="behavior.py", bound=None, inputs=None):
    """The highest tier ``source`` reaches, and the gate that stopped it.

    ``inputs`` is a stimulus for the concrete gate; without one the gate asks only that
    the model constructs, since inventing a stimulus would be asking a question about
    the schema rather than about the model.

    Every gate is asked in the order above and the first failure is the answer. A gate
    is deliberately not allowed to be skipped when it cannot be asked: if constructing
    the model raises, the tier is `none`, because whether its goals enumerate is not a
    fact about a model nobody can build.
    """
    from . import goals
    from .admission import check as admission_check

    rejections = admission_check(source, file=file)
    if rejections:
        return Tier("none", "admission",
                    f"{len(rejections)} constructs are not admitted, the first "
                    f"being {rejections[0]}")

    concrete = _concrete(source, file, inputs)
    if concrete is not None:
        return Tier("none", "construction", concrete)

    try:
        catalogue = goals.goal_set(source, file=file)
        goals.instrument_source(source, file=file)
    except Exception as e:                                      # noqa: BLE001
        return Tier("concrete", "instrumentation", f"{type(e).__name__}: {e}")
    if not catalogue:
        # Not a failure. A step with no branch declares no coverage goals and is
        # instrumented as much as it can be, so this says so rather than pretending
        # the gate was not asked.
        pass

    instance, why = _lowered(source, schema, file, bound)
    if instance is None:
        return Tier("instrumented", "lowering", why)

    why = _encoded(instance, bound)
    if why is not None:
        return Tier("instrumented", "encoding", why)

    return Tier("bounded")


def _concrete(source, file, inputs):
    """Nothing if the model constructs and starts. Else why not.

    Through `initial_by_construction`, which constructs the model and runs `reset`,
    because that is what the harness does and therefore what "it starts" means. Merely
    making the class is not a gate at all: the first version of this asked only that,
    and since a class body with no decorators always builds, the gate could not fail
    and no model in the population ever landed here. A gate that cannot fail is a tier
    that means nothing.

    A stimulus is optional, and without one the step is not run: inventing one would
    ask a question about the schema rather than about the model.
    """
    from .extract import initial_by_construction
    from .isolated import isolated_model

    try:
        initial_by_construction(source, file)
    except Exception as e:                                      # noqa: BLE001
        return f"{type(e).__name__}: {e}"
    if inputs is None:
        return None
    try:
        isolated_model(source, file=file)().process(**inputs)
    except Exception as e:                                      # noqa: BLE001
        return f"a step raised {type(e).__name__}: {e}"
    return None


def _lowered(source, schema, file, bound):
    from . import instance as bi
    from .lowering import LoweringError

    try:
        return bi.lower(source, schema, file=file, bound=bound), None
    except LoweringError as e:
        return None, str(e.what)
    except Exception as e:                                      # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _encoded(instance, bound):
    """Nothing if a formula can be built. Else why not, from the plan where it can say.

    The encoder's own message is the reason and its ``kind`` is the attribution: a
    `range`, `shape` or `type` refusal is something the frontend left unanswered, and
    anything else is an operation with no primitive or a value too wide to build, which
    are T10's and M4's columns rather than a gap here.

    Read off the exception, and not by encoding a second time in collecting mode. Which
    column a refusal belongs in is wanted wherever a candidate is scored, and a second
    encoding is not free; `verify_encoding` is for wanting *all* the faults rather than
    the first, which is a reporting need and not one the verification path has. The
    attribution was available at the moment of the refusal all along and was being
    thrown away.

    An earlier version reported the *plan's* first fault as the cause, which was wrong
    in a way worth remembering: a conservative check's first complaint need not be the
    complaint, so the funnel would have named a subterm that had nothing to do with the
    failure. A reason that is plausible and wrong is worse than one that quotes the
    backend.
    """
    from ..symbolic.encoder import encode_step
    from ..symbolic.validation import EncodingError

    del bound                       # the attribution no longer needs a second pass
    try:
        encode_step(instance.proc, env=instance.env)
    except EncodingError as e:
        where = (f"the frontend left a {e.kind} unanswered" if e.kind
                 else "an operation or a capability, not a gap in the frontend")
        return f"{e} -- {where}"
    except Exception as e:                                      # noqa: BLE001
        return (f"{type(e).__name__}: {e} -- not an encoding refusal at all, so "
                f"neither the frontend nor the subset has been asked")
    return None
