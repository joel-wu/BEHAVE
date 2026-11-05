"""The frontend's oracle chain: native source <-> graph IR interpreter <-> encoding.

todo3 T3-11, reviews 17/18. The term-vs-graph differential is a **control-flow**
check: `GraphLowerer` subclasses `_Lowerer` and inherits its expression lowering, so
a fault in the shared implementation makes both sides wrong together. The oracle a
certificate can lean on has to stand outside the code it checks, and the only thing
here that does is the **source itself, executed natively** -- instrumented with the
same static site enumeration the lowering mints its goals from, so hits are
comparable by identifier.

The chain has three nodes and two edges this file owns, plus one edge held against
the exact provider:

    native source  <->  graph IR interpreter  <->  symbolic encoding

Two rules review 18 added after breaking the first version:

* **The comparison universe is the source's, never the lowering's.** Comparing the
  state projected through `proc.state` let a lowering that deleted a state variable
  grade its own homework: the variable moved natively, nothing compared it, the
  oracle agreed. The fields come from the source AST now.
* **Hits are part of the step.** Every Mark's :T/:F swapped -- all hits wrong --
  passed the output-and-state comparison. Native instrumented hits join the tuple.

Each edge is held two ways: exhaustive small-width agreement (every input pair of
the declared width, chained two-step traces, full semantics: state over the
source's fields, outputs, branch hits, exact error code), and mutation tests that
break one side -- including the shared base -- and require detection.
"""
import ast

import pytest

from behave.sim.symbolic import interp, ir
from behave.sim.behavior.goals import HOOK, LOOP_HOOK, goal_id, instrument_source
from behave.sim.behavior.lower_graph import GraphLowerer
from behave.sim.behavior.lowering import _Lowerer, lower_with_state_ranges
from behave.sim.behavior.native_values import typed_equal

from .test_lower_graph import CASES, _model

#: Native exception type -> the code the IR's guards halt with. Compared exactly,
#: not as bool(error): an IndexError lowered as a DivZero is a wrong program with a
#: truthy error on both sides. Every code in this table is triggered natively by a
#: model below.
_ERROR_CODES = {"ZeroDivisionError": "DivZero",
                "ValueError": "NegativeShift",
                "IndexError": "IndexError", "KeyError": "KeyError"}

#: The inputs are exhaustive over this width: every value, every ordered pair.
WIDTH = 3

#: Beyond the control-flow corpus: state that must survive between steps, array
#: state, and one model per error code in `_ERROR_CODES` -- review 18 found the
#: Index/KeyError rows of that table had no test that ever triggered them.
STATEFUL = {
    "accumulator": _model(['self.x = (self.x + a) & 0xFF',
                           'return {"y": self.x}'], reset="self.x = 0"),
    "array_state": _model(['i = a & 3', 'y = self.t[i]', 'self.t[i] = 1',
                           'return {"y": y}'], reset="self.t = [0, 0, 0, 0]"),
    "halts_div_zero": _model(['self.x = self.x + 1', 'y = 100 // a',
                              'return {"y": y}'], reset="self.x = 0"),
    "halts_negative_shift": _model(['y = 8 >> (a - 1)',
                                     'return {"y": y}']),
    "halts_index_error": _model(['y = self.t[a]', 'return {"y": y}'],
                                reset="self.t = [3, 4, 5, 6]"),
    "halts_key_error": ('TBL = {0: 5, 2: 7, 5: 9}\n'
                        'class M:\n'
                        '    def reset(self): pass\n'
                        '    def process(self, a):\n'
                        '        return {"y": TBL[a]}\n'),
    # Real transaction sequences (review 19): none, several, and a step that
    # decides between them -- the lowering supports all three shapes, and the
    # oracle's normalization crashed on two of them.
    "multi_output": _model(['return [{"y": a}, {"y": a + 1}]']),
    "empty_output": _model(['return []']),
    "zero_or_two": _model(['if a > 3:', '    return [{"y": a}, {"y": a + 1}]',
                           'return []']),
}


def _state_fields_of(source):
    """The state variables the **source** declares: every `self.X` a method writes.

    Independent of `proc.state` on purpose (review 18): a lowering that drops a
    state variable also drops it from `proc.state`, so a comparison projected
    through the lowering's own list is the audited object choosing its own
    universe.
    """
    fields = set()
    for node in ast.walk(ast.parse(source)):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                fields.add(target.attr)
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and isinstance(target.value.value, ast.Name)
                    and target.value.value.id == "self"):
                fields.add(target.value.attr)
    return sorted(fields)


def _branch_hits(hits):
    """The hits both sides can speak about: the enumerated structural goals.

    The IR additionally mints safety goals when a guard fails; the native run has
    no hook for those, and the failure itself is compared through the exact error
    code instead.
    """
    return {h for h in hits if not h.startswith("safety")}


def _native_trace(source, rows):
    """The source run as Python, instrumented with the same site enumeration the
    lowering mints goals from: [(state, [output] or None, branch hits, code)]."""
    tree, sites = instrument_source(source)
    step_hits = set()

    def hit(ordinal, value):
        step_hits.add(goal_id(sites[ordinal], bool(value)))
        return value

    def loop(ordinal, iterable):
        site, n = sites[ordinal], 0
        for item in iterable:
            if n == 0:
                step_hits.add(goal_id(site, True))
            n += 1
            yield item
        if n == 0:
            step_hits.add(goal_id(site, False))

    ns = {HOOK: hit, LOOP_HOOK: loop}
    exec(compile(tree, "behavior.py", "exec"), ns)              # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    fields = _state_fields_of(source)
    model = cls()
    model.reset()
    trace = []
    for row in rows:
        step_hits.clear()
        try:
            out = model.process(**row)
            code = ""
            # The step's transaction sequence, in the three shapes the source may
            # return them (review 19: `[dict(out)]` crashed on a list and turned
            # `[]` into `[{}]`): nothing -> no transactions, a mapping -> one,
            # a list of mappings -> that many, in that order.
            if out is None:
                outs = []
            elif isinstance(out, dict):
                outs = [dict(out)]
            else:
                outs = [dict(t) for t in out]
        except Exception as exc:                               # noqa: BLE001
            outs = None
            code = _ERROR_CODES.get(type(exc).__name__, type(exc).__name__)
        state = {f: (list(v) if isinstance(v, list) else v)
                 for f, v in ((f, getattr(model, f, None)) for f in fields)}
        trace.append((state, outs, _branch_hits(step_hits), code))
        if code:
            break
    return trace


def _ir_trace(proc, rows, fields):
    """The lowered program interpreted, projected onto the source's fields."""
    state, trace = None, []
    for row in rows:
        state, out, hits, err = interp.run_step(proc, row, state=state)
        kept = {f: (list(v) if isinstance(v, list) else v)
                for f, v in ((f, state.get(f)) for f in fields)}
        trace.append((kept, None if err else [dict(t) for t in out.transactions],
                      _branch_hits(hits), err))
        if err:
            break
    return trace


def _traces_agree(native, lowered):
    """Full step semantics: state over the source's fields, outputs (count and
    order included -- the list is compared, not a set of it), branch hits, and the
    exact error code."""
    if len(native) != len(lowered):
        return False
    for (n_state, n_out, n_hits, n_err), (l_state, l_out, l_hits, l_err) \
            in zip(native, lowered):
        if n_err != l_err:
            return False
        if not n_err and n_out != l_out:
            return False
        if n_hits != l_hits:
            return False
        if n_state != l_state:
            return False
    return True


def _agree(source, rows, lowerer=None):
    assert len(rows) >= 2 or _native_trace(source, rows)[-1][3], \
        "state propagation needs a second step to show"
    fields = _state_fields_of(source)
    proc = lower_with_state_ranges(source, input_widths=[WIDTH], lowerer=lowerer)
    return _traces_agree(_native_trace(source, rows),
                         _ir_trace(proc, rows, fields))


@pytest.mark.parametrize("name", sorted(CASES) + sorted(STATEFUL))
def test_native_and_lowering_agree_exhaustively_at_small_width(name):
    """Every value of the declared width, every ordered two-step pair: 64 traces
    per model, full semantics per step. Exhaustive rather than sampled, which is
    what lets this stand as the oracle edge rather than a spot check (todo3 T3-11
    acceptance; review 18 item 3).
    """
    source = {**CASES, **STATEFUL}[name]
    fields = _state_fields_of(source)
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    for first in range(1 << WIDTH):
        for second in range(1 << WIDTH):
            rows = [{"a": first}, {"a": second}]
            native = _native_trace(source, rows)
            lowered = _ir_trace(proc, rows, fields)
            assert _traces_agree(native, lowered), (name, first, second,
                                                    native, lowered)


def test_every_error_code_in_the_mapping_is_triggered_natively():
    """Review 18: DivZero, IndexError and KeyError were written into the mapping
    and only DivZero ever fired in a test. One model per code, and the code must
    arrive from a genuinely raised native exception matched by the IR's halt."""
    for name, value, code in (("halts_div_zero", 0, "DivZero"),
                              ("halts_negative_shift", 0, "NegativeShift"),
                              ("halts_index_error", 5, "IndexError"),
                              ("halts_key_error", 1, "KeyError")):
        source = STATEFUL[name]
        native = _native_trace(source, [{"a": value}])
        assert native[-1][3] == code, (name, native[-1])
        assert _agree(source, [{"a": value}, {"a": value}]), name


def test_the_comparison_itself_cannot_be_fooled():
    """Comparator sensitivity, one tampering per field of the tuple (review 18:
    transaction count and order included). Each variant of a genuine IR trace must
    fail the comparison against the native run."""
    source = STATEFUL["array_state"]
    fields = _state_fields_of(source)
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    rows = [{"a": 1}, {"a": 5}]
    native = _native_trace(source, rows)
    honest = _ir_trace(proc, rows, fields)
    assert _traces_agree(native, honest)

    def tampered(step, **replace):
        out = [list(t) for t in honest]
        state, outs, hits, err = out[step]
        row = {"state": state, "outs": outs, "hits": hits, "err": err, **replace}
        out[step] = (row["state"], row["outs"], row["hits"], row["err"])
        return [tuple(t) for t in out]

    doubled = tampered(1, outs=honest[1][1] + honest[1][1])
    assert not _traces_agree(native, doubled), "transaction count must matter"

    # A **pure** order swap: two different transactions exchanged -- same count,
    # same multiset -- on a model that genuinely emits several (review 19: the
    # old "order" tampering also changed count and values, so it proved nothing
    # about the order dimension).
    m_source = STATEFUL["multi_output"]
    m_fields = _state_fields_of(m_source)
    m_proc = lower_with_state_ranges(m_source, input_widths=[WIDTH])
    m_native = _native_trace(m_source, rows)
    m_honest = _ir_trace(m_proc, rows, m_fields)
    assert _traces_agree(m_native, m_honest)
    two = m_honest[1][1]
    assert len(two) == 2 and two[0] != two[1], two
    m_swapped = list(m_honest)
    m_swapped[1] = (m_honest[1][0], [two[1], two[0]], m_honest[1][2],
                    m_honest[1][3])
    assert not _traces_agree(m_native, m_swapped), \
        "a pure swap of two different transactions must matter"
    # The hit tampering runs on a model that certainly reaches a branch (the
    # array model's guard mints no structural hit on either side).
    b_source = CASES["conditional"]
    b_fields = _state_fields_of(b_source)
    b_proc = lower_with_state_ranges(b_source, input_widths=[WIDTH])
    b_native = _native_trace(b_source, rows)
    b_honest = _ir_trace(b_proc, rows, b_fields)
    assert _traces_agree(b_native, b_honest)
    flipped = {h[:-2] + (":F" if h.endswith(":T") else ":T")
               for h in b_honest[1][2]}
    assert flipped != b_honest[1][2], "the model must reach a branch to flip"
    b_tampered = list(b_honest)
    b_tampered[1] = (b_honest[1][0], b_honest[1][1], flipped, b_honest[1][3])
    assert not _traces_agree(b_native, b_tampered), "hit outcomes must matter"
    less_state = dict(honest[1][0])
    less_state["t"] = None
    assert not _traces_agree(native, tampered(1, state=less_state)), \
        "a dropped state field must matter"
    assert not _traces_agree(native, tampered(1, err="IndexError")), \
        "the exact error code must matter"


def _rewrite(cmd, fn):
    if isinstance(cmd, ir.Seq):
        return ir.Seq(_rewrite(cmd.first, fn), _rewrite(cmd.second, fn))
    if isinstance(cmd, ir.If):
        return ir.If(cmd.guard, _rewrite(cmd.then, fn), _rewrite(cmd.orelse, fn))
    if isinstance(cmd, ir.For):
        return ir.For(
            cmd.index, cmd.trip_count, _rewrite(cmd.body, fn), flow=cmd.flow)
    return fn(cmd)


def test_a_fault_in_the_shared_lowering_is_caught_by_the_native_oracle():
    """Review 17 P0-2, the reproduction itself. `GraphLowerer` inherits `expr` from
    `_Lowerer`, so mutating the **base** makes term and graph wrong together --
    `a+1` lowered as `a-1` -- and the term-vs-graph differential passes. The native
    oracle stands outside the shared code and catches it.
    """
    from unittest import mock

    from behave.sim.behavior.lowering import _Lowerer, lower_procedure

    real = _Lowerer.expr

    def flips_add(self, node):
        got = real(self, node)
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                and isinstance(got, ir.Op) and got.op == "add"):
            return ir.Op("sub", *got.args)
        return got

    source = STATEFUL["accumulator"]
    rows = [{"a": 1}, {"a": 1}]
    with mock.patch.object(_Lowerer, "expr", flips_add):
        assert not _agree(source, rows), \
            "the native oracle must catch a fault shared by both lowerings"
        term = lower_procedure(source, input_widths=[WIDTH], lowerer=_Lowerer)
        graph = lower_procedure(source, input_widths=[WIDTH])
        t = interp.run_step(term, rows[0])
        g = interp.run_step(graph, rows[0])
        assert (t[0], t[1].transactions, t[3]) == (g[0], g[1].transactions, g[3]), \
            "both sides wrong together -- the reason term-vs-graph is secondary"
    assert _agree(source, rows), "unmutated, the chain agrees"


def test_swapped_hit_outcomes_are_caught_by_the_native_oracle():
    """Review 18 P0-1's first counterexample: every Mark's :T/:F swapped -- all
    hits wrong -- passed while the tuple ignored hits. It must fail now."""
    from unittest import mock

    from behave.sim.behavior import lowering as lowering_module

    real = lowering_module.lower_with_state_ranges

    def swapped(*args, **kwargs):
        proc = real(*args, **kwargs)

        def flip(cmd):
            if isinstance(cmd, ir.Mark) and cmd.goal[-2:] in (":T", ":F"):
                return ir.Mark(cmd.goal[:-2] + (":F" if cmd.goal.endswith(":T")
                                                else ":T"))
            return cmd

        return proc.derived(body=_rewrite(proc.body, flip), graph=None)

    source = CASES["conditional"]
    with mock.patch.object(lowering_module, "lower_with_state_ranges", swapped):
        proc = swapped(source, input_widths=[WIDTH])
        rows = [{"a": 4}, {"a": 1}]
        assert not _traces_agree(_native_trace(source, rows),
                                 _ir_trace(proc, rows,
                                           _state_fields_of(source))), \
            "swapped hit outcomes must fail the oracle"


def test_a_deleted_state_variable_is_caught_by_the_native_oracle():
    """Review 18 P0-1's second counterexample: the IR drops state x's declaration
    and updates; natively x still moves. With the comparison universe taken from
    the source rather than `proc.state`, the deletion must fail the oracle."""
    from behave.sim.behavior import lowering as lowering_module

    # Write-only state, so the deleted declaration cannot crash the interpreter
    # on a read -- the mutation's only trace is the missing state itself, which is
    # precisely what a comparison universe taken from `proc.state` could not see.
    source = _model(['self.x = a', 'return {"y": a}'], reset="self.x = 0")
    proc = lowering_module.lower_with_state_ranges(source, input_widths=[WIDTH])
    rows = [{"a": 1}, {"a": 5}]
    assert _traces_agree(_native_trace(source, rows),
                         _ir_trace(proc, rows, _state_fields_of(source))), \
        "unmutated, the model agrees"

    def strip(cmd):
        if isinstance(cmd, ir.Assign) and cmd.var == "x":
            return ir.Skip()
        return cmd

    proc = proc.derived(
        state=[s for s in proc.state if s != "x"],
        body=_rewrite(proc.body, strip),
        graph=None,
    )
    assert not _traces_agree(_native_trace(source, rows),
                             _ir_trace(proc, rows, _state_fields_of(source))), \
        "a state variable deleted from the IR must fail the oracle"


def test_a_wrong_halt_code_is_caught_by_the_native_oracle():
    """bool(error) passed a halt with the wrong code; the exact-code comparison
    must not. The mutation rewrites the Fail commands' codes post-lowering while
    keeping their goals -- renaming the code at its source is caught earlier, by
    the lowering's own safety-goal reconciliation, which is defence in depth but
    not what this test is for.
    """
    from behave.sim.behavior import lowering as lowering_module

    source = STATEFUL["halts_div_zero"]
    proc = lowering_module.lower_with_state_ranges(source, input_widths=[WIDTH])
    rows = [{"a": 0}, {"a": 1}]
    fields = _state_fields_of(source)
    assert _traces_agree(_native_trace(source, rows),
                         _ir_trace(proc, rows, fields))

    def rename(cmd):
        if isinstance(cmd, ir.Fail) and cmd.code == "DivZero":
            return ir.Fail("IndexError", cmd.goal)
        return cmd

    proc = proc.derived(body=_rewrite(proc.body, rename), graph=None)
    assert not _traces_agree(_native_trace(source, rows),
                             _ir_trace(proc, rows, fields)), \
        "a wrong halt code must fail the oracle"


def test_a_broken_interpreter_is_caught_by_the_native_oracle():
    """The other direction of the native edge: the interpreter is also under test.
    Its `mul` flipped to subtraction must break agreement with the native run.

    The fixture changes only ``mul`` so transaction-count bookkeeping, which
    legitimately uses ``add``, remains well formed and the comparison isolates
    the intended semantic edge.
    """
    from dataclasses import replace
    from unittest import mock

    from behave.sim.symbolic import primitives

    broken = primitives.PrimitiveRegistry(
        primitives.REGISTRY.version,
        [replace(spec, denotation=lambda a, b: a - b)
         if spec.name == "mul" else spec
         for spec in primitives.REGISTRY.specs])
    source = _model(['return {"y": a * 2}'])
    with mock.patch.object(primitives, "REGISTRY", broken):
        assert not _agree(source, [{"a": 1}, {"a": 1}])
    assert _agree(source, [{"a": 1}, {"a": 1}])


def _encoding_survey(source, *, prepared=None):
    """Every (state, goal) of a model through the exact provider, checked against
    the concrete run in **both directions**, with the outcome census returned.

    No outcome is silently skipped (review 18): UNKNOWN, UNSUPPORTED and
    OUTSIDE_DOMAIN on these deterministic small-width models are harness faults,
    not rows to step over.
    """
    from collections import Counter

    from behave.sim.engines import local_ir as exact
    from behave.sim.engines import probing

    from .test_exact_provider import build, every_input, reaches, states_of

    proc, env = build(source) if prepared is None else prepared
    goals = sorted(ir.goals_of(proc.body))
    assert goals
    seen = Counter()
    dishonest_sat = false_unsat = 0
    for state in states_of(proc, env):
        for goal in goals:
            got = exact.local_hit(proc, env, state, goal, timeout_ms=20_000)
            seen[got.outcome.name] += 1
            if got.outcome is probing.Outcome.SAT:
                if not reaches(proc, state, goal, got.candidate["a"]):
                    dishonest_sat += 1
            elif got.outcome is probing.Outcome.LOCAL_UNSAT:
                if any(reaches(proc, state, goal, v) for v in every_input()):
                    false_unsat += 1
            else:
                raise AssertionError(
                    f"outcome {got.outcome.name} for {goal}: nothing here may "
                    f"be silently skipped ({got.detail})")
    return seen, dishonest_sat, false_unsat


def test_the_encoding_agrees_with_the_run_in_both_directions():
    """The third edge, unmutated -- and the survey must actually exercise both
    directions: review 18 found the previous model's goals were all SAT, so the
    UNSAT branch (spelled with a nonexistent enum member, no less) was dead code.
    GATED's blocked state supplies genuine LOCAL_UNSAT rows.
    """
    from .test_exact_provider import GATED

    seen, dishonest_sat, false_unsat = _encoding_survey(GATED)
    assert seen["SAT"] > 0 and seen["LOCAL_UNSAT"] > 0, seen
    assert dishonest_sat == 0 and false_unsat == 0


@pytest.mark.solver
def test_a_broken_encoding_is_caught_in_each_direction():
    """One mutation per failure direction. `add -> sub` makes the solver name
    candidates the run does not follow (dishonest SAT); `add -> 0` makes a
    reachable goal unsatisfiable to the solver (false LOCAL_UNSAT). Each must be
    caught by the direction it breaks.
    """
    from unittest import mock

    from behave.sim.symbolic import encoder as encoder_module

    from .test_exact_provider import build

    source = _model(['z = a + 1', 'if z == 5:', '    y = 1', 'else:', '    y = 0',
                     'return {"y": y}'])
    prepared = build(source)
    _seen, dishonest, false_u = _encoding_survey(source, prepared=prepared)
    assert dishonest == 0 and false_u == 0

    real = encoder_module._Encoder._op

    def source_add(expression):
        return (isinstance(expression, ir.Op) and expression.op == "add"
                and len(expression.args) == 2
                and isinstance(expression.args[0], ir.Var)
                and expression.args[0].name == "a"
                and isinstance(expression.args[1], ir.Lit)
                and expression.args[1].value == 1)

    def flips_add(self, expression, rho):
        value = real(self, expression, rho)
        # Corrupt the source operation's encoded value, not the sealed IR or
        # its transaction-count bookkeeping. Replacing IR add with sub after
        # position planning correctly refuses an unplanned carrier, which is
        # a different check from this semantic fault-injection oracle.
        return value - 2 if source_add(expression) else value

    with mock.patch.object(encoder_module._Encoder, "_op", flips_add):
        _seen, dishonest, _false = _encoding_survey(source, prepared=prepared)
    assert dishonest > 0, "add->sub must produce a dishonest SAT"

    def zeroes_add(self, expression, rho):
        value = real(self, expression, rho)
        if source_add(expression):
            import z3

            return z3.BitVecVal(0, value.size())
        return value

    with mock.patch.object(encoder_module._Encoder, "_op", zeroes_add):
        _seen, _dishonest, false_u = _encoding_survey(source, prepared=prepared)
    assert false_u > 0, "add->0 must produce a false LOCAL_UNSAT"


def test_the_production_audit_walks_the_source_contract():
    """Review 19 P0-1. `model_audit` walked `proc.state` and stepped over missing
    attributes with `continue`, so the production audit had the exact blind spot
    review 18 closed in the test oracle: delete state x's IR declaration and
    updates and it returned []. The comparison universe is `proc.source_state`
    now -- the class's AST, frozen at lowering -- absent fields fall back to the
    initial value, and "absent" is a finding distinct from None.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = _model(['self.x = a', 'return {"y": a}'], reset="self.x = 0")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.source_state == ("x",), "the contract is frozen on the procedure"

    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(proc, cls, [[{"a": 1}, {"a": 5}]]) == []

    # The deletion, in review 20's hardest form: x leaves proc.state and its
    # updates leave the body, but initial["x"] stays, and every audited input
    # keeps the native value at that initial -- the configuration the implicit
    # initial fallback silently passed. "Absent" must be the finding regardless
    # of whether the values happen to coincide.
    def strip(cmd):
        if isinstance(cmd, ir.Assign) and cmd.var == "x":
            return ir.Skip()
        return cmd

    proc = proc.derived(
        state=[s for s in proc.state if s != "x"],
        body=_rewrite(proc.body, strip),
        graph=None,
    )
    assert proc.initial.get("x") == 0, "the fallback bait is deliberately kept"
    found = audit.model_audit(proc, cls, [[{"a": 0}, {"a": 0}]])
    assert found, "the deleted state variable must be a finding"
    assert any("the state x" in str(d) and "absent" in str(d) for d in found), \
        [str(d) for d in found]


def test_the_lowering_may_not_invent_state_outside_the_source():
    """The contract cuts both ways: a lowering whose state names something the
    source never writes is refused at lowering, by code."""
    from unittest import mock

    from behave.sim.behavior.lowering import LoweringError, lower_procedure

    source = _model(['self.x = a', 'return {"y": a}'], reset="self.x = 0")

    from behave.sim.behavior import extract

    real = extract.SourceModel.state_contract

    def narrowed(self):
        return tuple(f for f in real(self) if f != "x")

    with mock.patch.object(extract.SourceModel, "state_contract", narrowed):
        try:
            lower_procedure(source, input_widths=[WIDTH])
            raise AssertionError("state outside the contract must refuse")
        except LoweringError as exc:
            assert exc.code == "state-outside-the-source-contract"


def test_the_contract_survives_production_transforms():
    """Review 20 P0-1b. Slicing rebuilt the procedure through the constructor and
    hand-copied the fields it remembered, so `source_state` came out None and the
    audit silently reverted to `proc.state` after any slice. Every rebuild goes
    through `Procedure.derived` now, which carries all metadata and drops only by
    name.
    """
    from behave.sim.symbolic import slicing
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = CASES["conditional"]
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.source_state is not None and proc.representation is not None
    out = slicing.sliced(proc, set(ir.goals_of(proc.body)))
    assert out.source_state == proc.source_state
    assert out.representation == proc.representation
    assert out.graph is None, "the graph is dropped by name, not by omission"


def test_reset_only_state_is_a_recorded_constant_the_audit_holds():
    """A field only `reset` writes is represented as ("constant", value) --
    explicit, never an implicit initial fallback -- and a native run whose
    "constant" moves is a finding.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = _model(['self.x = a', 'return {"y": a}'],
                    reset="self.x = 0; self.idle = 7")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.representation["idle"] == ("constant", 7)

    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(proc, cls, [[{"a": 1}, {"a": 5}]]) == []

    class Drifts(cls):
        def process(self, a):
            self.idle = a
            return super().process(a)

    found = audit.model_audit(proc, Drifts, [[{"a": 3}]])
    assert any("the state idle" in str(d) for d in found), \
        [str(d) for d in found]


def test_an_authenticated_constant_must_not_move_natively():
    """Construction, not source syntax, records an exact reset-only value."""
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = _model(['self.x = a', 'return {"y": a}'],
                    reset="self.x = 0; self.k = int('6')")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.representation["k"] == ("constant", 6), \
        proc.representation

    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(proc, cls, [[{"a": 1}, {"a": 5}]]) == []

    class Moves(cls):
        def process(self, a):
            self.k = a
            return super().process(a)

    found = audit.model_audit(proc, Moves, [[{"a": 3}]])
    assert any("the state k" in str(d) for d in found), [str(d) for d in found]


def test_a_declared_step_field_without_entry_semantics_is_refused():
    """A fabricated step-state declaration must not invent an entry value.

    The lowering now carries every field in the step-state contract.  Adding a
    field that the source neither constructs nor defines on every normal path
    therefore reaches the entry-state check and is refused there.
    """
    from unittest import mock

    from behave.sim.behavior import extract
    from behave.sim.behavior.lowering import LoweringError, lower_procedure

    source = _model(['self.x = a', 'return {"y": a}'], reset="self.x = 0")
    real_all = extract.SourceModel.state_contract
    real_step = extract.SourceModel.step_state_contract

    def with_ghost(self):
        return tuple(sorted(set(real_all(self)) | {"ghost"}))

    def step_with_ghost(self):
        return tuple(sorted(set(real_step(self)) | {"ghost"}))

    with mock.patch.object(extract.SourceModel, "state_contract", with_ghost), \
         mock.patch.object(extract.SourceModel, "step_state_contract",
                           step_with_ghost):
        try:
            lower_procedure(source, input_widths=[WIDTH])
            raise AssertionError("an uninitialised step field must refuse")
        except LoweringError as exc:
            assert exc.code == "state-type-unresolved"


def test_the_audit_walks_transactions_in_both_directions():
    """Review 20 P1. The per-transaction walk iterated only the native fields, so
    a representation reporting an extra field passed. The union is walked, with
    "absent" named on either side."""
    from unittest import mock

    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = _model(['self.x = a', 'return {"y": a}'], reset="self.x = 0")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))

    real = interp._run_step

    def padded(proc_, semantics, inputs, state=None, watch=None):
        st, out, hits, err = real(proc_, semantics, inputs, state, watch)

        class _Out:
            def __init__(self):
                self.transactions = [
                    dict(t, smuggled=99) for t in out.transactions
                ]

        return st, _Out(), hits, err

    with mock.patch.object(interp, "_run_step", padded):
        found = audit.model_audit(proc, cls, [[{"a": 1}]])
    assert any("smuggled" in str(d) and "absent" in str(d) for d in found), \
        [str(d) for d in found]


def test_the_contract_is_sealed_against_reclassification():
    """Review 21 P0. The contract was a plain dict shared by reference through
    derived(), so a later pass could relabel a deleted field ("constant", 0) in
    place and reopen the deletion false pass. It is one immutable StateContract
    now: attribute writes raise, the dict view is a copy, keys must equal the
    fields exactly, lookups carry no default, and derived() refuses unknown
    override names (a typo used to be silently ignored).
    """
    import pytest

    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = _model(['self.x = a', 'return {"y": a}'], reset="self.x = 0")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])

    with pytest.raises(AttributeError, match="immutable"):
        proc.contract._pairs = ()
    view = proc.representation
    view["x"] = ("constant", 0)
    assert proc.contract.how("x") == ("state",), "the view is a copy"
    with pytest.raises(KeyError):
        proc.contract.how("ghost")
    with pytest.raises(TypeError, match="representaton"):
        proc.derived(representaton={})
    with pytest.raises(ValueError, match="classify exactly"):
        ir.StateContract(("x",), {"x": ("state",), "ghost": ("state",)})
    with pytest.raises(ValueError, match="unknown representation kind"):
        ir.StateContract(("x",), {"x": ("mystery",)})

    # And the review's exact reopening attempt, end to end: delete x from the
    # IR, try to relabel it, and the audit still reports the deletion.
    from behave.sim.behavior import audit

    def strip(cmd):
        if isinstance(cmd, ir.Assign) and cmd.var == "x":
            return ir.Skip()
        return cmd

    proc = proc.derived(
        state=[s for s in proc.state if s != "x"],
        body=_rewrite(proc.body, strip),
        graph=None,
    )
    with pytest.raises(AttributeError):
        proc.contract.__init__(("x",), {"x": ("constant", 0)})
    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    found = audit.model_audit(proc, cls, [[{"a": 0}, {"a": 0}]])
    assert any("absent" in str(d) for d in found), [str(d) for d in found]


def test_the_step_contract_follows_the_call_graph():
    """Review 21 P1. "Every method that is not reset" called a helper only reset
    reaches step-written and refused a correct model. The closures are
    reachability now: reset's helper writes are reset's, and the field becomes
    an authenticated constant rather than missing state."""
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = ("class M:\n"
              "    def init_fields(self):\n"
              "        self.idle = 7\n"
              "    def reset(self):\n"
              "        self.x = 0\n"
              "        self.init_fields()\n"
              "    def process(self, a):\n"
              "        self.x = a\n"
              "        return {'y': a}\n")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.contract.how("idle") == ("constant", 7)
    assert proc.contract.how("x") == ("state",)
    # And a step-side helper's writes are still the step's.
    from behave.sim.behavior.extract import source_model

    stepper = ("class M:\n"
               "    def bump(self, a):\n"
               "        self.x = a\n"
               "    def reset(self):\n"
               "        self.x = 0\n"
               "    def process(self, a):\n"
               "        self.bump(a)\n"
               "        return {'y': a}\n")
    model = source_model(stepper, "behavior.py")
    assert "x" in model.step_state_contract()


def test_construction_is_the_constant_authority_not_a_default():
    """A helper-read field is replaced only by an authenticated value."""
    from behave.sim.behavior import audit
    from behave.sim.behavior.extract import initial_by_construction
    from behave.sim.behavior.lowering import lower_with_state_ranges

    leaky = ("class M:\n"
             "    def reset(self):\n"
             "        self.x = 0\n"
             "        self.k = int('6')\n"
             "    def helper(self, a):\n"
             "        return a + self.k\n"
             "    def process(self, a):\n"
             "        self.x = a\n"
             "        return {'y': self.helper(a)}\n")
    diagnostic = lower_with_state_ranges(leaky, input_widths=[WIDTH])
    assert "k" not in diagnostic.state
    assert diagnostic.contract.how("k") == ("constant", 6)

    initial, unsupported = initial_by_construction(leaky)
    assert unsupported == {}
    exact = lower_with_state_ranges(
        leaky, input_widths=[WIDTH], read_initial=initial)
    assert exact.contract.how("k") == ("constant", 6)
    rows = [{"a": 3}, {"a": 5}]
    _state, out, _hits, error = interp.run_step(exact, rows[0])
    assert error == ""
    assert out.transactions == [{"y": 9}]
    ns = {}
    exec(leaky, ns)                                            # noqa: S102
    assert audit.model_audit(exact, ns["M"], [rows]) == []

    # The genuine article: k's value reaches the run through the initial state
    # of x, its name appears nowhere in the body, and the native run agrees --
    # the semantic use the review asked to see exercised.
    genuine = ("class M:\n"
               "    def reset(self):\n"
               "        self.k = int('6')\n"
               "    def process(self, a):\n"
               "        return {'y': a}\n")
    proc = lower_with_state_ranges(genuine, input_widths=[WIDTH])
    assert proc.contract.how("k") == ("constant", 6)
    ns = {}
    exec(genuine, ns)                                           # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(proc, cls, [[{"a": 1}, {"a": 5}]]) == []

    class Moves(cls):
        def process(self, a):
            self.k = a
            return super().process(a)

    found = audit.model_audit(proc, Moves, [[{"a": 3}]])
    assert any("the state k" in str(d) for d in found), [str(d) for d in found]


def test_the_contract_binding_is_write_once_and_not_derivable():
    """Review 22 P0. The immutable object was bound mutably: derived(contract=)
    swapped in a forged classification, and `proc.contract = None` reached the
    proc.state fallback -- both reopening the deletion false pass end to end.
    The binding accepts exactly one real StateContract and refuses everything
    after; derived() no longer knows a contract override at all.
    """
    import pytest

    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = _model(['self.x = a', 'return {"y": a}'], reset="self.x = 0")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])

    def strip(cmd):
        if isinstance(cmd, ir.Assign) and cmd.var == "x":
            return ir.Skip()
        return cmd

    proc = proc.derived(
        state=[s for s in proc.state if s != "x"],
        body=_rewrite(proc.body, strip),
        graph=None,
    )

    forged = ir.StateContract(("x",), {"x": ("constant", 0)})
    with pytest.raises(TypeError, match="contract"):
        proc.derived(body=proc.body, contract=forged)
    with pytest.raises(AttributeError, match="write-once"):
        proc.contract = forged
    with pytest.raises(AttributeError, match="write-once"):
        proc.contract = None
    clone = proc.derived()
    assert clone.contract is proc.contract

    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    for candidate in (proc, clone):
        found = audit.model_audit(candidate, cls, [[{"a": 0}, {"a": 0}]])
        assert any("absent" in str(d) for d in found), \
            "the deletion finding must survive every rebinding attempt"


def test_a_mutable_constant_cannot_drag_its_own_baseline():
    """A contract stores an immutable image, not the live reset object."""
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = _model(['self.x = a', 'return {"y": a}'],
                    reset="self.x = 0; self.k = [int('6')]")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.contract.how("k") == ("constant", [6])

    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(proc, cls, [[{"a": 1}, {"a": 5}]]) == []

    class MutatesInPlace(cls):
        def process(self, a):
            self.k[0] = a
            return super().process(a)

    found = audit.model_audit(proc, MutatesInPlace, [[{"a": 3}]])
    assert any("the state k" in str(d) for d in found), \
        [str(d) for d in found]


def test_constants_freeze_recursively_and_keep_their_types():
    """Review 22 P1. ('constant',) died on an IndexError, ('constant', 1, 2) was
    silently truncated, nested lists stayed mutable inside a "frozen" contract,
    and a genuine tuple constant thawed as a list -- so an honest run reported
    `[1, 2] against (1, 2)`. The freeze is recursive and type-tagged, the record
    is exactly two fields, and thawed copies share nothing with the contract.
    """
    import pytest

    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    for bad in (("constant",), ("constant", 1, 2)):
        with pytest.raises(ValueError, match="exactly"):
            ir.StateContract(("k",), {"k": bad})

    deep = ir.StateContract(("k",), {"k": ("constant", (1, [2, 3]))})
    thawed = deep.how("k")[1]
    assert thawed == (1, [2, 3]) and isinstance(thawed, tuple)
    thawed[1][0] = 99
    assert deep.how("k")[1] == (1, [2, 3]), "thawed copies share nothing"
    assert hash(deep), "frozen forms are hashable"

    source = _model(['self.x = a', 'return {"y": a}'],
                    reset="self.x = 0; self.k = (1, 2)")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.contract.how("k") == ("constant", (1, 2)), \
        "the source's tuple stays a tuple through fold and freeze"

    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(proc, cls, [[{"a": 1}, {"a": 5}]]) == [], \
        "an honest tuple constant is not a finding"

    class Drifts(cls):
        def process(self, a):
            self.k = (a, a)
            return super().process(a)

    found = audit.model_audit(proc, Drifts, [[{"a": 3}]])
    assert any("the state k" in str(d) for d in found)


def test_a_nested_function_cannot_shadow_the_call_graph():
    """Review 22 P1. The reachability walk rebuilt its method table with
    ast.walk over the whole class, so a same-name function nested inside an
    unrelated method shadowed the method `self.helper()` actually calls and
    emptied the step closure. The table is the class's parsed registry now.
    """
    from behave.sim.behavior.extract import source_model

    source = ("class M:\n"
              "    def helper(self):\n"
              "        self.x = 9\n"
              "    def unused(self):\n"
              "        def helper(self):\n"
              "            pass\n"
              "        return helper\n"
              "    def reset(self):\n"
              "        self.x = 0\n"
              "    def process(self, a):\n"
              "        self.helper()\n"
              "        return {'y': a}\n")
    model = source_model(source, "behavior.py")
    assert "x" in model.step_state_contract()


def test_the_contract_survives_process_boundaries_identically():
    """Review 23 P0. __reduce__ serialised the already-frozen pairs, so every
    pickle round-trip froze them again: (1, 2) came back as
    ('tuple', (('atom', 1), ...)) and an honest model failed its own audit after
    one trip through the step audit's worker pool. The reduce goes through the
    thawed public form now; identity holds for scalars, lists, tuples, nesting,
    and a whole procedure.
    """
    import pickle

    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    values = (
        None, True, 1, 1.5, "mode", b"raw", bytearray(b"raw"),
        [1, 2], (1, 2), (1, [2, 3]), [(1,), [2]],
        {1, 2}, frozenset({1, 2}), {True: (1, [2])},
    )
    for value in values:
        contract = ir.StateContract(("k",), {"k": ("constant", value)})
        back = pickle.loads(pickle.dumps(contract))
        assert typed_equal(back.how("k")[1], value), \
            (value, back.how("k"))
        again = pickle.loads(pickle.dumps(back))
        assert again == contract, "round-trips are idempotent"

    source = _model(['self.x = a', 'return {"y": a}'],
                    reset="self.x = 0; self.k = (1, 2)")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    back = pickle.loads(pickle.dumps(proc))
    assert back.contract == proc.contract
    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(back, cls, [[{"a": 1}, {"a": 5}]]) == []


def test_every_exact_carrier_is_copied_and_opaque_values_are_reported():
    """Exact carriers are immutable contract values; opaque ones fail closed."""
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def build(literal):
        source = _model(['self.x = a', 'return {"y": a}'],
                        reset=f"self.x = 0; self.k = {literal}")
        proc = lower_with_state_ranges(source, input_widths=[WIDTH])
        assert proc.contract.how("k")[0] == "constant"
        ns = {}
        exec(source, ns)                                        # noqa: S102
        cls = next(v for v in ns.values()
                   if isinstance(v, type) and hasattr(v, "process"))
        return proc, cls

    for literal, mutate in (("{6}", "self.k.add(a)"),
                            ("bytearray([6])", "self.k[0] = a")):
        proc, cls = build(literal)
        assert audit.model_audit(proc, cls, [[{"a": 1}, {"a": 5}]]) == []
        ns = {"base": cls}
        exec(  # noqa: S102 - construct an adversarial class for this test
             "class Mutates(base):\n"
             "    def process(self, a):\n"
             f"        {mutate}\n"
             "        return super().process(a)\n", ns)
        found = audit.model_audit(proc, ns["Mutates"], [[{"a": 3}]])
        assert any("the state k" in str(d) for d in found), \
            (literal, [str(d) for d in found])

    # A value outside the closed carrier stays a folded obligation.  The audit
    # reports that it cannot take an independent baseline instead of comparing
    # an object with itself.
    opaque = ("class Custom:\n"
              "    pass\n"
              "class M:\n"
              "    def reset(self):\n"
              "        self.x = 0\n"
              "        self.k = Custom()\n"
              "    def process(self, a):\n"
              "        self.x = a\n"
              "        return {'y': a}\n")
    proc = lower_with_state_ranges(opaque, input_widths=[WIDTH])
    assert proc.contract.how("k") == ("folded",)
    ns = {}
    exec(opaque, ns)                                           # noqa: S102
    found = audit.model_audit(proc, ns["M"], [[{"a": 1}]])
    assert any("cannot snapshot" in str(d) for d in found), \
        [str(d) for d in found]


def test_the_constant_type_comes_from_the_same_fold_as_the_value():
    """Review 23 P1. A separate first-match AST reader recorded the first
    assignment's type while the fold kept the last one's value, so
    `self.k = (1, 2)` then `self.k = [1, 2]` produced a contract saying tuple
    against a native list -- a false finding on an honest model. Value and type
    come from one fold now (last top-level assignment wins, as in Python).
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = _model(['self.x = a', 'return {"y": a}'],
                    reset="self.x = 0; self.k = (1, 2); self.k = [1, 2]")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.contract.how("k") == ("constant", [1, 2])
    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(proc, cls, [[{"a": 1}, {"a": 5}]]) == []


def test_a_builtin_subclass_is_not_an_exact_constant_carrier():
    """Carrier membership uses exact types, never ``isinstance``."""
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    # The authenticated construction records the plain integer. A candidate
    # that substitutes a subclass is still outside the exact carrier.
    source = _model(['self.x = a', 'return {"y": a}'],
                    reset="self.x = 0; self.k = int(6)")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.contract.how("k") == ("constant", 6)
    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(proc, cls, [[{"a": 1}, {"a": 5}]]) == []

    class Box(int):
        pass

    class Boxy(list):
        pass

    for carrier in (lambda: Box(6), lambda: Boxy([6])):
        class Holds(cls):
            def reset(self, _make=carrier):
                super().reset()
                held = _make()
                held.tag = []
                self.k = held

            def process(self, a):
                self.k.tag.append(a)
                return super().process(a)

        found = audit.model_audit(proc, Holds, [[{"a": 1}, {"a": 2}]])
        assert any("the state k" in str(d) for d in found), \
            [str(d) for d in found]


def test_the_frozen_constant_distinguishes_bool_from_int():
    """Review 24 P0. Every scalar froze as ("atom", value) and True == 1 in
    Python, so a Boolean constant and an Integer constant had equal contracts
    and a wrong model storing the other type audited clean, nested included.
    The tag is the exact type now and the audit's constant comparison is
    typed.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    assert (ir.StateContract(("k",), {"k": ("constant", True)})
            != ir.StateContract(("k",), {"k": ("constant", 1)}))
    assert (ir.StateContract(("k",), {"k": ("constant", [True])})
            != ir.StateContract(("k",), {"k": ("constant", [1])}))

    for literal, wrong in (("True", "1"), ("[True]", "[1]")):
        source = _model(['self.x = a', 'return {"y": a}'],
                        reset=f"self.x = 0; self.k = {literal}")
        proc = lower_with_state_ranges(source, input_widths=[WIDTH])
        ns = {}
        exec(source, ns)                                        # noqa: S102
        cls = next(v for v in ns.values()
                   if isinstance(v, type) and hasattr(v, "process"))
        assert audit.model_audit(proc, cls, [[{"a": 1}]]) == [], literal

        wrong_source = source.replace(f"self.k = {literal}",
                                      f"self.k = {wrong}")
        ns2 = {}
        exec(wrong_source, ns2)                                 # noqa: S102
        cls2 = next(v for v in ns2.values()
                    if isinstance(v, type) and hasattr(v, "process"))
        found = audit.model_audit(proc, cls2, [[{"a": 1}]])
        assert any("the state k" in str(d) for d in found), \
            (literal, [str(d) for d in found])


def test_cyclic_or_aliased_folded_values_are_findings_not_recursions():
    """The audit accepts exactly the native-value tree carrier.

    A cycle cannot be copied, and a repeated mutable node cannot be encoded as
    a tree without losing its identity.  Both are therefore fail-closed.  Two
    equal but independent subtrees remain ordinary values and are copied.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.audit import _UNSNAPSHOTTABLE, _snapshot
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = _model(['self.x = a', 'return {"y": a}'],
                    reset="self.x = 0; v = []; v.append(v); self.k = v")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.contract.how("k") == ("folded",)
    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    found = audit.model_audit(proc, cls, [[{"a": 1}]])
    assert any("cannot snapshot" in str(d) for d in found), \
        [str(d) for d in found]

    # A shared mutable node has no faithful tree image.
    shared = [6]
    assert _snapshot([shared, shared]) is _UNSNAPSHOTTABLE

    # Equal but distinct subtrees remain inside the carrier.
    copied = _snapshot([[6], [6]])
    assert copied == [[6], [6]] and copied[0] is not copied[1]
    cyclic = []
    cyclic.append(cyclic)
    assert _snapshot(cyclic) is _UNSNAPSHOTTABLE
    assert _snapshot({"k": cyclic}) is _UNSNAPSHOTTABLE


def test_the_fold_reads_only_the_chosen_model_in_runtime_order():
    """Review 24 P1. The fold walked every __init__/reset in the module in
    source order, but the runtime always runs M.__init__ then M.reset and runs
    no other class at all: a reset written above __init__ lost to it, and a
    helper class at the bottom of the file overwrote the model's own values.
    The fold reads the parsed model's constructor then its reset now, and a
    later write it cannot read erases instead of being invisible.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.extract import _initial_state, source_model
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def check(source):
        proc = lower_with_state_ranges(source, input_widths=[WIDTH])
        assert proc.contract.how("k") == ("constant", [1, 2]), \
            proc.contract.how("k")
        ns = {}
        exec(source, ns)                                        # noqa: S102
        cls = next(v for v in ns.values()
                   if isinstance(v, type) and hasattr(v, "process"))
        assert audit.model_audit(proc, cls, [[{"a": 1}]]) == []

    check("class M:\n"
          "    def reset(self):\n"
          "        self.x = 0\n"
          "        self.k = [1, 2]\n"
          "    def __init__(self):\n"
          "        self.k = (1, 2)\n"
          "    def process(self, a):\n"
          "        self.x = a\n"
          "        return {'y': a}\n")
    check("class M:\n"
          "    def reset(self):\n"
          "        self.x = 0\n"
          "        self.k = [1, 2]\n"
          "    def process(self, a):\n"
          "        self.x = a\n"
          "        return {'y': a}\n"
          "class Helper:\n"
          "    def __init__(self):\n"
          "        self.k = (9, 9)\n")

    # `self.k[:] = <literal>` replaces every element outright and keeps the
    # container the prior entry says it is (the population's memory models
    # reset a constructor-built buffer exactly this way); a partial or unknown
    # slice write erases like any other unreadable write.
    sliced = ("class M:\n"
              "    def __init__(self):\n"
              "        self.k = [1, 2]\n"
              "        self.j = 6\n"
              "    def reset(self):\n"
              "        self.k[:] = [3, 4]\n"
              "        self.j = 0\n"
              "    def process(self, a):\n"
              "        return {'y': a}\n")
    got = _initial_state(source_model(sliced), typed=True)
    assert got["k"] == [3, 4] and type(got["k"]) is list
    partial = sliced.replace("self.k[:] = [3, 4]", "self.k[0] = 3")
    assert "k" not in _initial_state(source_model(partial))
    unknown = sliced.replace("self.k = [1, 2]\n        ", "")
    assert "k" not in _initial_state(source_model(unknown))

    # A later top-level write the fold cannot read erases the entry, in every
    # form the write takes: it is not fixed outright at the earlier value.
    for late in ("self.k = v", "self.k += 1", "self.k = self.j = 7",
                 "if v:\n            self.k = 9"):
        source = ("class M:\n"
                  "    def reset(self):\n"
                  "        v = 6\n"
                  "        self.j = 0\n"
                  "        self.k = 5\n"
                  f"        {late}\n"
                  "    def process(self, a):\n"
                  "        return {'y': a}\n")
        assert "k" not in _initial_state(source_model(source)), late


def test_the_isolation_seam_fails_closed_on_what_it_cannot_carry():
    """Review 25 P0. source_audit runs the model behind the isolation seam,
    whose old channel carried only ints and flat int arrays: every other
    carrier looked absent to the audit, and a contract field absent on both
    sides was skipped, so the direct audit refused a Box(int) while the
    production path passed it. The seam reports what it cannot carry now and
    the audit turns that into a finding rather than an agreement.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.isolated import isolated_model
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = ("class Box(int):\n"
              "    pass\n"
              "class M:\n"
              "    def reset(self):\n"
              "        box = Box(6)\n"
              "        box.tag = []\n"
              "        self.k = box\n"
              "    def process(self, a):\n"
              "        return {'y': a}\n")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.contract.how("k") == ("folded",)
    make = isolated_model(source, "behavior.py", cls="M")
    try:
        found = audit.model_audit(proc, make, [[{"a": 1}]], make.hits)
    finally:
        make.close()
    assert any("cannot carry" in str(d) for d in found), \
        [str(d) for d in found]


def test_the_seam_carries_exact_types_for_the_closed_carriers():
    """Review 25 P0. What the seam can carry it now carries exactly: an
    honest Boolean constant audits clean through it (the old channel dropped
    bools, which read as a false absence), and an Integer stored where the
    contract records a Boolean is a finding even across the process boundary.
    A finite dict crosses faithfully too.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.isolated import isolated_model
    from behave.sim.behavior.lowering import lower_with_state_ranges

    honest = ("class M:\n"
              "    def reset(self):\n"
              "        self.k = True\n"
              "        self.d = {True: 'v'}\n"
              "    def process(self, a):\n"
              "        return {'y': a}\n")
    proc = lower_with_state_ranges(honest, input_widths=[WIDTH])
    assert proc.contract.how("k") == ("constant", True)
    assert proc.contract.how("d") == ("constant", {True: "v"})
    make = isolated_model(honest, "behavior.py", cls="M")
    try:
        assert audit.model_audit(proc, make, [[{"a": 1}]], make.hits) == []
    finally:
        make.close()

    wrong = honest.replace("self.k = True", "self.k = 1")
    make2 = isolated_model(wrong, "behavior.py", cls="M")
    try:
        found = audit.model_audit(proc, make2, [[{"a": 1}]], make2.hits)
    finally:
        make2.close()
    assert any("the state k" in str(d) for d in found), \
        [str(d) for d in found]


def test_canonical_equality_reaches_keys_and_unordered_carriers():
    """Review 25 P0. The typed comparison recursed structurally but let dict
    keys and set members fall back to Python's ==, under which True == 1: a
    folded dict whose key moved from True to 1, or a set likewise, audited
    clean. Everything canonicalizes with its exact type now, keys and
    unordered members included.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    assert not typed_equal({True: "v"}, {1: "v"})
    assert not typed_equal({True}, {1})
    assert not typed_equal(frozenset({True}), {1})
    assert not typed_equal({(True, 2): 0}, {(1, 2): 0})
    assert typed_equal({(True, 2): 0}, {(True, 2): 0})
    assert typed_equal({True: [1]}, {True: [1]})

    for literal, mutate in (
            ("{True: 'v'}", "self.k.pop(True); self.k[1] = 'v'"),
            ("{True}", "self.k.discard(True); self.k.add(1)"),
            ("frozenset({True})", "self.k = frozenset({1})")):
        source = ("class M:\n"
                  "    def reset(self):\n"
                  f"        self.k = {literal}\n"
                  "    def process(self, a):\n"
                  "        return {'y': a}\n")
        proc = lower_with_state_ranges(source, input_widths=[WIDTH])
        assert proc.contract.how("k")[0] == "constant", literal
        ns = {}
        exec(source, ns)                                        # noqa: S102
        cls = next(v for v in ns.values()
                   if isinstance(v, type) and hasattr(v, "process"))
        assert audit.model_audit(proc, cls, [[{"a": 1}]]) == [], literal
        ns2 = {"base": cls}
        exec(  # noqa: S102 - construct an adversarial class for this test
             "class Mutates(base):\n"
             "    def process(self, a):\n"
             f"        {mutate}\n"
             "        return super().process(a)\n", ns2)
        found = audit.model_audit(proc, ns2["Mutates"], [[{"a": 1}]])
        assert any("the state k" in str(d) for d in found), \
            (literal, [str(d) for d in found])


def test_a_stable_nan_neither_moves_nor_hides():
    """Review 25 P1. Floats are in the closed carrier set but == said a NaN
    was not itself, so an honest model holding a stable NaN was a false
    finding. A float compares as its binary64 bit pattern now: a stable NaN
    is stable, and a NaN replaced by a number is still a finding.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    nan = float("nan")
    assert typed_equal(nan, nan)
    assert not typed_equal(nan, 1.0)
    assert not typed_equal(1.0, 1)

    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.k = float('nan')\n"
              "    def process(self, a):\n"
              "        return {'y': a}\n")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.contract.how("k")[0] == "constant"
    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(proc, cls, [[{"a": 1}]]) == []

    ns2 = {"base": cls}
    exec(  # noqa: S102 - construct an adversarial class for this test
         "class Mutates(base):\n"
         "    def process(self, a):\n"
         "        self.k = 1.0\n"
         "        return super().process(a)\n", ns2)
    found = audit.model_audit(proc, ns2["Mutates"], [[{"a": 1}]])
    assert any("the state k" in str(d) for d in found), \
        [str(d) for d in found]


def test_a_call_in_reset_erases_what_it_may_write():
    """Review 25 P1. The fold read only top-level assignments, so a helper
    the constructor or reset calls wrote fields the fold never saw: __init__
    setting x = 1 and reset calling set_x() folded x at 1 while the runtime
    leaves 2. A `self.m()` call erases m's reachable writes at its position;
    a call the registry cannot resolve erases everything held so far; and a
    method called on a field (`self.k.append(v)`) erases that field.
    """
    from behave.sim.behavior.extract import _initial_state, source_model

    helper = ("class M:\n"
              "    def __init__(self):\n"
              "        self.x = 1\n"
              "        self.y = 5\n"
              "    def set_x(self):\n"
              "        self.x = 2\n"
              "    def reset(self):\n"
              "        self.set_x()\n"
              "    def process(self, a):\n"
              "        self.x = a\n"
              "        return {'y': self.x}\n")
    got = _initial_state(source_model(helper))
    assert "x" not in got and got["y"] == 5, got

    unknown = helper.replace("self.set_x()", "self.somewhere_else()")
    assert _initial_state(source_model(unknown)) == {}

    refolded = helper.replace("    def process",
                              "        self.x = 3\n    def process")
    assert _initial_state(source_model(refolded))["x"] == 3

    mutated = ("class M:\n"
               "    def reset(self):\n"
               "        self.k = [1]\n"
               "        self.j = 4\n"
               "        self.k.append(2)\n"
               "    def process(self, a):\n"
               "        return {'y': a}\n")
    got2 = _initial_state(source_model(mutated))
    assert "k" not in got2 and got2["j"] == 4, got2

    # A constructor's own `self.reset()` is subsumed, not erased: the harness
    # calls reset last and the fold replays reset last, so the population's
    # memory-model shape (build the buffer in __init__, call reset() to fill
    # it, refill by whole-slice in reset) keeps its constant.
    memory = ("class M:\n"
              "    def __init__(self):\n"
              "        self.data = [0, 0]\n"
              "        self.reset()\n"
              "    def reset(self):\n"
              "        self.data[:] = [0, 0]\n"
              "    def process(self, a):\n"
              "        return {'y': a}\n")
    assert _initial_state(source_model(memory)) == {"data": [0, 0]}


def test_the_audit_view_is_not_forgeable_by_a_candidate():
    """Review 26 P0. The faithful view was duck-typed on the method name
    `_audit_view`, so a model under audit could define one and lie about its
    own state: k really moved to 7 while the forged view said 6 forever, and
    the audit returned nothing. The proxy is recognized by exact type now; a
    candidate's `_audit_view` is ignored and its real attributes are read.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.lowering import lower_with_state_ranges

    source = ("class M:\n"
              "    def reset(self):\n"
              "        self.k = int(6)\n"
              "    def process(self, a):\n"
              "        return {'y': a}\n")
    proc = lower_with_state_ranges(source, input_widths=[WIDTH])
    assert proc.contract.how("k") == ("constant", 6)

    class Lies:
        def reset(self):
            self.k = 6

        def process(self, a):
            self.k = 7
            return {"y": a}

        def _audit_view(self):
            return {"k": 6}, {}

    found = audit.model_audit(proc, Lies, [[{"a": 1}]])
    assert any("the state k" in str(d) for d in found), \
        [str(d) for d in found]


def test_identity_sensitive_members_are_refused_not_attested():
    """Review 26 P0 and review 27 P0. A set holds two distinct NaN objects of
    one bit pattern and a dict binds them to different values -- observable
    semantics no value-level copy or canonical form preserves (the counts
    survived review 26's multisets, the bindings did not: swapping the values
    under two same-bit NaN keys still compared equal). A set member or dict
    key that holds a NaN anywhere is therefore refused, fail closed: the
    snapshot reports it and the canonical form compares it equal to nothing,
    itself included. Positional NaN -- in a list, a tuple, a dict's values,
    or a plain field -- stays faithful by bit pattern.
    """
    from behave.sim.behavior import audit
    from behave.sim.behavior.audit import _UNSNAPSHOTTABLE, _snapshot
    from behave.sim.behavior.lowering import lower_with_state_ranges

    a, b = float("nan"), float("nan")
    assert len({a, b}) == 2
    assert not typed_equal({a}, {a, b})
    assert not typed_equal({a: 0}, {a: 0, b: 0})
    assert not typed_equal({a: 0, b: 1}, {a: 1, b: 0})
    assert not typed_equal({a}, {b})
    assert not typed_equal({a}, {a})
    assert not typed_equal({(a, 1)}, {(a, 1)})
    assert _snapshot({a, b}) is _UNSNAPSHOTTABLE
    assert _snapshot({a: 0}) is _UNSNAPSHOTTABLE
    assert _snapshot({1: {a}}) is _UNSNAPSHOTTABLE
    # Positional NaN is faithful.
    assert typed_equal([a], [b])
    assert typed_equal({1: a}, {1: b})
    copied = _snapshot({1: a})
    assert type(copied) is dict and copied[1] != copied[1]

    for literal in ("{float('nan'), float('nan')}",
                    "{float('nan'): 0, float('nan'): 1}"):
        source = ("class M:\n"
                  "    def reset(self):\n"
                  f"        self.k = {literal}\n"
                  "    def process(self, a):\n"
                  "        return {'y': a}\n")
        proc = lower_with_state_ranges(source, input_widths=[WIDTH])
        assert proc.contract.how("k") == ("folded",), literal
        ns = {}
        exec(source, ns)                                        # noqa: S102
        cls = next(v for v in ns.values()
                   if isinstance(v, type) and hasattr(v, "process"))
        found = audit.model_audit(proc, cls, [[{"a": 1}]])
        assert any("cannot snapshot" in str(d) for d in found), \
            (literal, [str(d) for d in found])


def test_self_escaping_the_fold_clears_it():
    """Review 26 P1. `self.m()` was analyzed but a plain call taking self
    (`set_x(self)`), an alias (`s = self`) or a stored self were invisible,
    so the fold kept a value the escaped code overwrote. Bare self anywhere
    clears the fold, and from then on only literal self-assignments fold.
    """
    from behave.sim.behavior.extract import _initial_state, source_model

    escaped = ("def set_x(obj):\n"
               "    obj.x = 2\n"
               "class M:\n"
               "    def __init__(self):\n"
               "        self.x = 1\n"
               "    def reset(self):\n"
               "        set_x(self)\n"
               "    def process(self, a):\n"
               "        self.x = a\n"
               "        return {'y': self.x}\n")
    assert _initial_state(source_model(escaped)) == {}

    alias = escaped.replace("set_x(self)", "s = self\n        s.x = 2")
    assert _initial_state(source_model(alias)) == {}

    stored = escaped.replace("set_x(self)", "others = []\n"
                             "        others.append(self)")
    assert _initial_state(source_model(stored)) == {}

    # After the escape, a literal self-assignment still folds: the alias
    # cannot outrun a later top-level write the fold does read.
    refolds = escaped.replace("set_x(self)", "set_x(self)\n        self.k = 5")
    got = _initial_state(source_model(refolds))
    assert got == {"k": 5}, got

    # And a call that never takes self erases nothing.
    safe = ("class M:\n"
            "    def reset(self):\n"
            "        self.a = 5\n"
            "        n = len([1])\n"
            "        self.b = n\n"
            "    def process(self, a):\n"
            "        return {'y': a}\n")
    got2 = _initial_state(source_model(safe))
    assert got2["a"] == 5 and "b" not in got2, got2

    # A **field** escapes too (review 27): handed to a call, aliased to a
    # local, or the whole instance through self.__dict__. Each erases; a
    # later literal rebinding detaches the alias and may refold; and reading
    # one field into another self slot is not an escape.
    base = ("def mutate(q):\n"
            "    q.append(2)\n"
            "class M:\n"
            "    def reset(self):\n"
            "        self.k = [1]\n"
            "        {shape}\n"
            "    def process(self, a):\n"
            "        return {{'y': a}}\n")
    for shape in ("mutate(self.k)",
                  "q = self.k\n        mutate(q)",
                  "mutate(self.__dict__)"):
        got3 = _initial_state(source_model(base.format(shape=shape)))
        assert "k" not in got3, (shape, got3)
    refolds = base.format(shape="mutate(self.k)\n        self.k = [3]")
    assert _initial_state(source_model(refolds))["k"] == [3]
    slots = base.format(shape="self.j = self.k")
    assert _initial_state(source_model(slots))["k"] == [1]


def test_a_mutable_field_alias_in_construction_is_refused():
    """Review 28 P0. `self.x = self.t` leaves two names on one object and
    the IR has no aliases: initialized independently from construction
    state, a step writing x[0] moves the native t and not the IR's, so an
    audit whose stimuli never disturb the shared cells passes. The first
    half demonstrates that false pass on the alias-free IR the old lowering
    produced; the second asserts the lowering now refuses the binding,
    typed, while provably immutable bindings -- the population's scalar
    initializations -- still lower.
    """
    import pytest

    from behave.sim.behavior import audit
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    aliased = ("class M:\n"
               "    def reset(self):\n"
               "        self.t = [1, 2]\n"
               "        self.x = [0, 0]\n"
               "        self.x = self.t\n"
               "    def process(self, a):\n"
               "        self.x[0] = a\n"
               "        return {'y': self.t[0]}\n")
    # The alias-free twin: structurally what the old lowering believed.
    twin = aliased.replace("self.x = self.t", "self.x = [1, 2]")
    proc = lower_with_state_ranges(twin, input_widths=[WIDTH],
                                   read_initial={"t": [1, 2], "x": [1, 2]})
    ns = {}
    exec(aliased, ns)                                           # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and hasattr(v, "process"))
    assert audit.model_audit(proc, cls, [[{"a": 1}]]) == [], \
        "a=1 never disturbs the shared cells: the false pass this refusal exists for"
    assert audit.model_audit(proc, cls, [[{"a": 2}]]) != []

    with pytest.raises(LoweringError) as refused:
        lower_with_state_ranges(aliased, input_widths=[WIDTH],
                                read_initial={"t": [1, 2], "x": [1, 2]})
    assert refused.value.code == "state-aliased-in-construction"

    # Provably immutable bindings stay admitted: a scalar, a resolved
    # constant, a tuple of scalars, and a conditional between two of them.
    for line in ("self.x = self.s",
                 "self.x = self.u",
                 "self.x = self.s if a_flag else self.u"):
        source = ("a_flag = 1\n"
                  "class M:\n"
                  "    def reset(self):\n"
                  "        self.s = 5\n"
                  "        self.u = (1, 2)\n"
                  f"        {line}\n"
                  "    def process(self, a):\n"
                  "        return {'y': a}\n")
        lower_with_state_ranges(source, input_widths=[WIDTH])

    # A source no static proof reaches (a call result) whose ends are IR
    # scalars is admitted too: an IR scalar has value semantics exactly as
    # Python's immutable numbers do, which is the population's
    # `self.shift_q = self.seed` shape.
    scalar_state = ("class M:\n"
                    "    def __init__(self):\n"
                    "        self.seed = int('6')\n"
                    "    def reset(self):\n"
                    "        self.q = self.seed\n"
                    "    def process(self, a):\n"
                    "        self.q = (self.q + a) & 7\n"
                    "        return {'y': self.q}\n")
    lower_with_state_ranges(scalar_state, input_widths=[WIDTH])

    # And the local-alias detour is the same binding.
    detour = aliased.replace("self.x = self.t",
                             "q = self.t\n        self.x = q")
    with pytest.raises(LoweringError) as refused2:
        lower_with_state_ranges(detour, input_widths=[WIDTH],
                                read_initial={"t": [1, 2], "x": [1, 2]})
    assert refused2.value.code == "state-aliased-in-construction"


def test_every_binding_form_escapes_the_fold():
    """Review 28 P1. The field escape covered only plain Assign, so an
    annotated assignment, a walrus, and a for-target binding slipped it.
    Each binds the same alias and each erases now; For is the one the
    admitted grammar reaches, the others are covered ahead of it.
    """
    from behave.sim.behavior.extract import _initial_state, source_model

    base = ("def mutate(q):\n"
            "    q.append(2)\n"
            "class M:\n"
            "    def reset(self):\n"
            "        self.k = [1]\n"
            "        {shape}\n"
            "    def process(self, a):\n"
            "        return {{'y': a}}\n")
    for shape in ("q: list = self.k\n        mutate(q)",
                  "if (q := self.k):\n            pass",
                  "for q in [self.k]:\n            pass",
                  "for q in self.k:\n            pass"):
        got = _initial_state(source_model(base.format(shape=shape)))
        assert "k" not in got, (shape, got)


def test_the_alias_analysis_follows_real_execution_order():
    """Review 29. The occurrence-based check missed statement order and
    scope: a local rebound after carrying the alias hid it, a binding under
    an `if` was invisible because locals were tracked only at the top level,
    a helper bound fields the scan never read, a helper rewriting a scalar
    field into an array defeated the immutability proof -- and an alias the
    reset itself dissolved was refused although the step never sees it. The
    check is a transfer analysis in execution order now: constructor then
    reset, helpers inlined at their call sites, branches joined, rebinding
    releasing the alias, unresolved calls havocking fail closed.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def build(reset_body, extra=""):
        return ("class M:\n"
                + extra
                + "    def reset(self):\n"
                + "".join(f"        {line}\n" for line in reset_body)
                + "    def process(self, a):\n"
                  "        self.x[0] = a\n"
                  "        return {'y': self.t[0]}\n")

    refused = {
        "rebound local": build(["self.t = [1, 2]", "self.x = [0, 0]",
                                "q = self.t", "self.x = q", "q = 0"]),
        "alias inside a branch": build(
            ["self.t = [1, 2]", "self.x = [0, 0]",
             "if len(self.t) > 1:", "    q = self.t", "    self.x = q"]),
        "helper binds the fields": build(
            ["self.t = [1, 2]", "self.x = [0, 0]", "self.bind()"],
            extra="    def bind(self):\n        self.x = self.t\n"),
        "helper rewrites the scalar": build(
            ["self.t = 0", "self.x = [0, 0]", "self.helper()",
             "self.x = self.t"],
            extra="    def helper(self):\n        self.t = [1, 2]\n"),
        "unresolved call": build(
            ["self.t = [1, 2]", "self.x = [0, 0]", "self.somewhere()"]),
    }
    for label, source in refused.items():
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH],
                                    read_initial={"t": [1, 2], "x": [1, 2]})
        # A construction the sandbox cannot even run (self.somewhere()
        # raises) is refused as unverifiable; one that runs and shares is
        # refused as aliased. Both are typed and both are closed.
        assert caught.value.code in ("state-aliased-in-construction",
                                     "state-sharing-unverified"), \
            (label, caught.value.code)

    # An alias the reset dissolves never reaches the step, so it lowers.
    dissolved = ("class M:\n"
                 "    def __init__(self):\n"
                 "        self.t = [1, 2]\n"
                 "        self.x = self.t\n"
                 "    def reset(self):\n"
                 "        self.t = [1, 2]\n"
                 "        self.x = [1, 2]\n"
                 "    def process(self, a):\n"
                 "        self.x[0] = a\n"
                 "        return {'y': self.t[0]}\n")
    lower_with_state_ranges(dissolved, input_widths=[WIDTH],
                            read_initial={"t": [1, 2], "x": [1, 2]})


def test_the_alias_interpreter_respects_control_flow_and_call_semantics():
    """Review 30. Four holes in the interpreter itself: statements after a
    Return still ran (so unreachable code "released" an alias the runtime
    keeps); keyword arguments bound by position; a plain call was assumed to
    return an argument or something fresh, when it can store an alias in a
    global and hand it back; and loops stopped at four rounds, missing a
    five-stage propagation. Return/Break/Continue end their paths now and
    join at exits, keywords bind by name with defaults (starred forms
    havoc), a plain call is entered when it is module-level source and
    havocs unless it is a whitelisted pure builtin, and loops run to a real
    fixpoint with havoc at the engineering bound.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    tail = ("    def process(self, a):\n"
            "        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    refused = {
        "code after return": (
            "class M:\n"
            "    def bind(self):\n"
            "        self.x = self.t\n"
            "        return 0\n"
            "        self.x = [0, 0]\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "        self.bind()\n" + tail),
        "keywords bind by name": (
            "class M:\n"
            "    def bind(self, unused, source):\n"
            "        self.x = source\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "        self.bind(source=self.t, unused=0)\n" + tail),
        "a plain call can keep the alias": (
            "saved = None\n"
            "def keep(x):\n"
            "    global saved\n"
            "    saved = x\n"
            "def take():\n"
            "    return saved\n"
            "class M:\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "        keep(self.t)\n"
            "        self.x = take()\n" + tail),
        "five-stage loop propagation": (
            "class M:\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "        q1 = q2 = q3 = q4 = q5 = 0\n"
            "        for _ in range(5):\n"
            "            q5 = q4\n"
            "            q4 = q3\n"
            "            q3 = q2\n"
            "            q2 = q1\n"
            "            q1 = self.t\n"
            "        self.x = q5\n" + tail),
        "an unknown imported call havocs": (
            "from os.path import join\n"
            "class M:\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "        join('a', 'b')\n" + tail),
    }
    for label, source in refused.items():
        # The construction's own x survives untouched in the havoc case, so
        # the read initial must match what running it actually leaves.
        x0 = [0, 0] if label == "an unknown imported call havocs" else [1, 2]
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH],
                                    read_initial={"t": [1, 2], "x": x0})
        expected = (
            ("source-import-unapproved",)
            if label == "an unknown imported call havocs"
            else ("state-aliased-in-construction",
                  "state-sharing-unverified")
        )
        assert caught.value.code in expected, (label, caught.value.code)

    # Precision that must survive the closures: a return whose value is the
    # masked argument is entered and stays immutable (the lfsr's
    # to_unsigned), a keyword binding that lands on a scalar is admitted,
    # and a loop that converges early converges.
    admitted = (
        "def to_unsigned(v, b):\n"
        "    return v & ((1 << b) - 1)\n"
        "class M:\n"
        "    def __init__(self, seed=0):\n"
        "        self.seed = to_unsigned(v=seed, b=16)\n"
        "    def reset(self):\n"
        "        self.q = self.seed\n"
        "    def process(self, a):\n"
        "        self.q = (self.q + a) & 7\n"
        "        return {'y': self.q}\n")
    lower_with_state_ranges(admitted, input_widths=[WIDTH])


def test_the_alias_interpreter_has_no_backchannels():
    """Review 31. Five production false accepts in the interpreter's edges:
    a return value's side effects landed after the exit state was copied; a
    loop's else ran on the break path and "released" what a broken-out
    iteration keeps; a builtin contract applied to a shadowed name
    (min = keep); an attribute call on an untracked receiver (vault.keep /
    vault.take) was passed through instead of havocking; and `global` was a
    runtime statement, so a declaration inside a loop went unseen while
    Python scopes it lexically. Each shape must refuse; the defaults'
    definition-time scope and the tracked-receiver precision must survive.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    helpers = ("saved = None\n"
               "def keep(x):\n"
               "    global saved\n"
               "    saved = x\n"
               "    return 0\n"
               "def take():\n"
               "    return saved\n")
    tail = ("    def process(self, a):\n"
            "        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    refused = {
        "return value side effects": (
            helpers
            + "class M:\n"
              "    def stash(self):\n"
              "        return keep(self.t)\n"
              "    def reset(self):\n"
              "        self.t = [1, 2]\n"
              "        self.x = [0, 0]\n"
              "        self.stash()\n"
              "        self.x = take()\n" + tail),
        "loop else is not the break path": (
            "class M:\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "        for _ in range(1):\n"
            "            self.x = self.t\n"
            "            break\n"
            "        else:\n"
            "            self.x = [0, 0]\n" + tail),
        "a shadowed builtin is no builtin": (
            helpers
            + "class M:\n"
              "    def reset(self):\n"
              "        self.t = [1, 2]\n"
              "        self.x = [0, 0]\n"
              "        min = keep\n"
              "        min(self.t)\n"
              "        self.x = take()\n" + tail),
        "an untracked receiver is a backchannel": (
            "class Vault:\n"
            "    def keep(self, x):\n"
            "        self.held = x\n"
            "    def take(self):\n"
            "        return self.held\n"
            "vault = Vault()\n"
            "class M:\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "        vault.keep(self.t)\n"
            "        self.x = vault.take()\n" + tail),
        "global is lexical": (
            helpers
            + "class M:\n"
              "    def reset(self):\n"
              "        self.t = [1, 2]\n"
              "        self.x = [0, 0]\n"
              "        for _ in range(1):\n"
              "            global saved\n"
              "        saved = self.t\n"
              "        self.x = take()\n" + tail),
    }
    for label, source in refused.items():
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH],
                                    read_initial={"t": [1, 2], "x": [1, 2]})
        assert caught.value.code in ("state-aliased-in-construction",
                                     "state-sharing-unverified"), \
            (label, caught.value.code)

    # Precision that survives: a genuine builtin under no shadow, a tracked
    # field receiver, and a scalar default bound at definition scope.
    admitted = ("def mask(v, width=16):\n"
                "    return v & ((1 << width) - 1)\n"
                "class M:\n"
                "    def __init__(self, seed=0):\n"
                "        self.seed = mask(seed)\n"
                "        self.log = []\n"
                "        self.log.append(int('1'))\n"
                "    def reset(self):\n"
                "        self.q = self.seed\n"
                "    def process(self, a):\n"
                "        self.q = (self.q + a) & 7\n"
                "        return {'y': self.q}\n")
    lower_with_state_ranges(admitted, input_widths=[WIDTH])


def test_precision_is_earned_by_proof_or_the_interpreter_havocs():
    """Review 32. Six false accepts shared one root: the interpreter kept
    open-world defaults where precision must be earned. Bare self read as
    "immutable nothing", so q = self; q.bind() was a no-op call; a receiver
    contract keyed on tracking, not on what the object is, so two agent
    objects talked through class state; builtins read their arguments but
    never ran them (min(..., key=...), a call inside a comprehension); and
    name binding trusted the AST where the runtime differs (a module-level
    walrus shadowing a builtin, a decorated def whose callable is not its
    body). The boundary is now a whitelist: self is a first-class token
    every unsafe consumption of which havocs; method contracts exist only
    for receivers whose whole object graph the interpreter built from
    literals (or proven immutables with proven arguments); builtin
    contracts require no keywords and pure arguments, since int(param)
    dispatches __int__ and sorted(xs) dispatches __lt__; comprehensions
    containing calls, decorated defs, and every shadowed name havoc; and
    defaults are frozen at definition scope, with calls in them refused.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    keep = ("saved = None\n"
            "def keep(x):\n"
            "    global saved\n"
            "    saved = x\n"
            "    return 0\n"
            "def take():\n"
            "    return saved\n")
    tail = ("    def process(self, a):\n"
            "        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    body = ("        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n")
    refused = {
        "bare self through a local": (
            "class M:\n"
            "    def bind(self):\n"
            "        self.x = self.t\n"
            "    def reset(self):\n" + body
            + "        q = self\n"
              "        q.bind()\n" + tail),
        "agent objects talk through class state": (
            "class Vault:\n"
            "    held = None\n"
            "    def keep(self, v):\n"
            "        Vault.held = v\n"
            "    def take(self):\n"
            "        return Vault.held\n"
            "class M:\n"
            "    def __init__(self):\n"
            "        self.left = Vault()\n"
            "        self.right = Vault()\n"
            "    def reset(self):\n" + body
            + "        left = self.left\n"
              "        right = self.right\n"
              "        left.keep(self.t)\n"
              "        self.x = right.take()\n" + tail),
        "a builtin runs its callback": (
            keep
            + "def key(v):\n"
              "    keep(v)\n"
              "    return 0\n"
              "class M:\n"
              "    def reset(self):\n" + body
            + "        min([self.t], key=key)\n"
              "        self.x = take()\n" + tail),
        "a comprehension runs its calls": (
            keep
            + "class M:\n"
              "    def reset(self):\n" + body
            + "        _ = [keep(self.t) for _ in [0]]\n"
              "        self.x = take()\n" + tail),
        "a module walrus shadows a builtin": (
            keep
            + "(min := keep)\n"
              "class M:\n"
              "    def reset(self):\n" + body
            + "        min(self.t)\n"
              "        self.x = take()\n" + tail),
        "a decorated def is not its body": (
            keep
            + "def replace(fn):\n"
              "    return keep\n"
              "@replace\n"
              "def passthrough(v):\n"
              "    return v\n"
              "class M:\n"
              "    def reset(self):\n" + body
            + "        passthrough(self.t)\n"
              "        self.x = take()\n" + tail),
    }
    for label, source in refused.items():
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH],
                                    read_initial={"t": [1, 2], "x": [1, 2]})
        assert caught.value.code in ("state-aliased-in-construction",
                                     "state-sharing-unverified"), \
            (label, caught.value.code)

    # The precision the whitelist keeps: builtins over proven arguments,
    # methods on containers the interpreter built, module functions inlined
    # with frozen call-free defaults, and immutable-receiver methods.
    admitted = ("def mask(v, width=16):\n"
                "    return v & ((1 << width) - 1)\n"
                "class M:\n"
                "    def __init__(self, seed=0):\n"
                "        self.seed = mask(seed)\n"
                "        self.logw = (int('8') - 1).bit_length()\n"
                "        self.log = []\n"
                "        self.log.append(len([1, 2]))\n"
                "    def reset(self):\n"
                "        self.q = self.seed\n"
                "    def process(self, a):\n"
                "        self.q = (self.q + a) & 7\n"
                "        return {'y': self.q}\n")
    lower_with_state_ranges(admitted, input_widths=[WIDTH])


def test_implicit_dispatch_is_an_effect_and_bindings_are_not_builtins():
    """Review 33. Two roots. Expression evaluation answered values but not
    effects: comparison, hashing, iteration, truthiness, formatting,
    arithmetic, attribute and subscript access each dispatch a dunder of
    the operand's own class, so `evil == self.t` ran __eq__, set.add ran
    __hash__, iterating an opaque ran __iter__, a property ran on attribute
    access, and a call hid in an unevaluated index. Every such operation on
    an unproven value havocs now, subscripts evaluate their brackets,
    comprehensions are interpreted (iterable, filters, element, key hashes)
    rather than scanned, and container methods have one contract per
    method: append alone stores without looking at its argument. And every
    module def shadows a builtin of its name, decorated or not -- "bound in
    the module" and "safe to inline" are different sets, so @replace def
    min no longer impersonates the builtin.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    keep = ("saved = None\n"
            "def keep(x):\n"
            "    global saved\n"
            "    saved = x\n"
            "    return 0\n"
            "def take():\n"
            "    return saved\n")
    tail = ("    def process(self, a):\n"
            "        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    body = ("        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n")
    evil_eq = ("class Evil:\n"
               "    def __eq__(self, other):\n"
               "        keep(other)\n"
               "        return False\n"
               "    def __hash__(self):\n"
               "        return 0\n"
               "evil = Evil()\n")
    # The dunder smuggles a module-level list the reset also binds to t, so
    # each source really runs to completion with x is t (review 34: three of
    # these shapes had no native premise at all).
    smuggle = ("payload = [1, 2]\n"
               "saved = None\n"
               "def take():\n"
               "    return saved\n")
    body_p = ("        self.t = payload\n"
              "        self.x = [0, 0]\n")
    refused = {
        "comparison dispatches": (
            keep + evil_eq
            + "class M:\n"
              "    def reset(self):\n" + body
            + "        evil == self.t\n"
              "        self.x = take()\n" + tail),
        "hashing dispatches": (
            smuggle
            + "class Evil:\n"
              "    def __hash__(self):\n"
              "        global saved\n"
              "        saved = payload\n"
              "        return 0\n"
              "evil = Evil()\n"
              "class M:\n"
              "    def reset(self):\n" + body_p
            + "        bucket = set()\n"
              "        bucket.add(evil)\n"
              "        self.x = take()\n" + tail),
        "iteration dispatches": (
            smuggle
            + "class It:\n"
              "    def __iter__(self):\n"
              "        global saved\n"
              "        saved = payload\n"
              "        return iter([1])\n"
              "it = It()\n"
              "class M:\n"
              "    def reset(self):\n" + body_p
            + "        _ = [v for v in it]\n"
              "        self.x = take()\n" + tail),
        "the index is evaluated": (
            keep
            + "class M:\n"
              "    def reset(self):\n" + body
            + "        local = [0, 0]\n"
              "        local[keep(self.t)] = 1\n"
              "        self.x = take()\n" + tail),
        "attribute access dispatches": (
            smuggle
            + "class Evil:\n"
              "    @property\n"
              "    def value(self):\n"
              "        global saved\n"
              "        saved = payload\n"
              "        return 0\n"
              "evil = Evil()\n"
              "class M:\n"
              "    def reset(self):\n" + body_p
            + "        _ = evil.value\n"
              "        self.x = take()\n" + tail),
        "a decorated def shadows the builtin": (
            keep
            + "def replace(fn):\n"
              "    return keep\n"
              "@replace\n"
              "def min(v):\n"
              "    return v\n"
              "class M:\n"
              "    def reset(self):\n" + body
            + "        min(self.t)\n"
              "        self.x = take()\n" + tail),
    }
    # The smuggle shapes bind t to a module list, whose unknown length
    # trips an unrelated static gate in a full lowering -- the accidental
    # refusal soundness must not lean on -- so those three are asserted at
    # the hazard level, and the rest through the full gate.
    from behave.sim.behavior.extract import source_model

    from .alias_interpreter import constants_in_scope, reset_field_aliases

    hazard_level = {"hashing dispatches", "iteration dispatches",
                    "attribute access dispatches"}
    for label, source in refused.items():
        assert _native_aliases(source), label
        if label in hazard_level:
            model = source_model(source, "behavior.py")
            found = reset_field_aliases(model, constants_in_scope(model))
            assert ("t", "x") in found or ("x", "t") in found, (label, found)
            continue
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH],
                                    read_initial={"t": [1, 2], "x": [1, 2]})
        assert caught.value.code in ("state-aliased-in-construction",
                                     "state-sharing-unverified"), \
            (label, caught.value.code)

    # The precision that survives, and that the interpretation of
    # comprehensions improves: constants resolved inside them no longer
    # pollute two fields with one shared token.
    admitted = ("class M:\n"
                "    BLOCK = 2\n"
                "    def reset(self):\n"
                "        self.a = [[0 for _ in range(self.BLOCK)]\n"
                "                  for _ in range(self.BLOCK)]\n"
                "        self.b = [0 for _ in range(self.BLOCK)]\n"
                "        self.c = 5 if self.BLOCK > 1 else 6\n"
                "    def process(self, x):\n"
                "        return {'y': x}\n")
    lower_with_state_ranges(admitted, input_widths=[WIDTH])


def _native_aliases(source):
    """Run the source natively: construct, reset, and answer x is t."""
    ns = {}
    exec(source, ns)                                            # noqa: S102
    cls = next(v for v in ns.values()
               if isinstance(v, type) and v.__name__ == "M")
    inst = cls()
    inst.reset()
    return inst.x is inst.t


def test_expression_effects_have_paths_order_and_carriers():
    """Review 34. The evaluator executed effects eagerly on one state, so it
    had no notion of a path: `False and self.release()` ran the right side
    and dissolved an alias short-circuit keeps, an IfExp ran both arms in
    sequence, a chained comparison ran every comparator, a zero-iteration
    comprehension ran its body once, creating a generator ran its body, and
    a dict evaluated all keys before all values. Conditional subexpressions
    run on forks joined back now; eager comprehensions are loops with a
    zero-iteration path and their own target scope; a generator expression
    is an unevaluated computation, opaque; dicts interleave key, value; a
    for target binds only inside an iteration. And cont tokens carry their
    carrier: exactly list.append stores without observing its argument,
    while bytearray.append runs __index__ on it.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError, source_model
    from behave.sim.behavior.lowering import lower_with_state_ranges

    from .alias_interpreter import constants_in_scope, reset_field_aliases

    tail = ("    def process(self, a):\n"
            "        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    head = ("class M:\n"
            "    def release(self):\n"
            "        self.x = [0, 0]\n"
            "        return 0\n"
            "    def alias(self):\n"
            "        self.x = self.t\n"
            "        return 0\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = self.t\n")
    refused = {
        "short-circuit keeps the alias": head
        + "        False and self.release()\n" + tail,
        "only one arm runs": head
        + "        self.release() if False else 0\n" + tail,
        "a chained comparison stops": head
        + "        1 > 2 < self.release()\n" + tail,
        "a zero-iteration body never runs": head
        + "        _ = [self.release() for _ in []]\n" + tail,
        "creating a generator runs nothing": head
        + "        _ = (self.release() for _ in [0])\n" + tail,
        "dicts evaluate key, value, key, value": (
            "class M:\n"
            "    def release(self):\n"
            "        self.x = [0, 0]\n"
            "        return 0\n"
            "    def alias(self):\n"
            "        self.x = self.t\n"
            "        return 0\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "        _ = {0: self.release(), self.alias(): 1}\n" + tail),
    }
    for label, source in refused.items():
        assert _native_aliases(source), label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH],
                                    read_initial={"t": [1, 2], "x": [1, 2]})
        assert caught.value.code in ("state-aliased-in-construction",
                                     "state-sharing-unverified"), \
            (label, caught.value.code)

    # The remaining three are asserted at the hazard level: their full
    # lowering trips unrelated static-shape gates, which soundness must not
    # lean on (the reviewer's phrase: an accidental refusal).
    def hazards(source):
        model = source_model(source, "behavior.py")
        return reset_field_aliases(model, constants_in_scope(model))

    zero_for = ("class M:\n"
                "    def reset(self):\n"
                "        self.t = [1, 2]\n"
                "        q = self.t\n"
                "        for q in []:\n"
                "            pass\n"
                "        self.x = q\n"
                "    def process(self, a):\n"
                "        return {'y': a}\n")
    assert _native_aliases(zero_for)
    assert ("t", "x") in hazards(zero_for)

    comp_scope = zero_for.replace(
        "        for q in []:\n            pass\n",
        "        _ = [q for q in [0]]\n")
    assert _native_aliases(comp_scope)
    assert ("t", "x") in hazards(comp_scope)

    # The native premise first (review 35): __index__ smuggles the module
    # list the reset also binds to t, so the source really completes with
    # x is t, and the hazard is the closed false accept, not merely a
    # conservative havoc.
    byte_append = ("payload = [1, 2]\n"
                   "saved = None\n"
                   "def take():\n"
                   "    return saved\n"
                   "class Evil:\n"
                   "    def __index__(self):\n"
                   "        global saved\n"
                   "        saved = payload\n"
                   "        return 0\n"
                   "evil = Evil()\n"
                   "class M:\n"
                   "    def reset(self):\n"
                   "        self.t = payload\n"
                   "        bucket = bytearray(2)\n"
                   "        bucket.append(evil)\n"
                   "        self.x = take()\n"
                   "    def process(self, a):\n"
                   "        return {'y': a}\n")
    assert _native_aliases(byte_append)
    assert ("t", "x") in hazards(byte_append)

    # list.append keeps its contract, and the zero-path join still finds
    # the alias the loop body would have dissolved.
    keeps = ("other = None\n"
             "class M:\n"
             "    def reset(self):\n"
             "        self.log = []\n"
             "        self.log.append(other)\n"
             "        self.k = 5\n"
             "    def process(self, a):\n"
             "        return {'y': a}\n")
    assert hazards(keeps) == []


def test_paths_and_evaluation_order_are_pythons_own():
    """Review 35. Three residues of the same principle: a comprehension
    filter is a branch (false keeps its own effects and skips the rest,
    per level, with nested generators keeping their zero-yield paths); a
    call evaluates its callable expression before its arguments, so a
    receiver's effects come first; and a starred element iterates its
    operand. Each shape completes natively with x is t and must refuse.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError, source_model
    from behave.sim.behavior.lowering import lower_with_state_ranges

    from .alias_interpreter import constants_in_scope, reset_field_aliases

    tail = ("    def process(self, a):\n"
            "        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    filtered = ("class M:\n"
                "    def release(self):\n"
                "        self.x = [0, 0]\n"
                "        return 0\n"
                "    def alias(self):\n"
                "        self.x = self.t\n"
                "        return False\n"
                "    def reset(self):\n"
                "        self.t = [1, 2]\n"
                "        self.x = [0, 0]\n"
                "        _ = [self.release() for _ in [0] if self.alias()]\n"
                + tail)
    ordered = ("class M:\n"
               "    def release_container(self):\n"
               "        self.x = [0, 0]\n"
               "        return [0]\n"
               "    def alias_argument(self):\n"
               "        self.x = self.t\n"
               "        return 0\n"
               "    def reset(self):\n"
               "        self.t = [1, 2]\n"
               "        self.x = [0, 0]\n"
               "        self.release_container().append("
               "self.alias_argument())\n" + tail)
    for label, source in (("filter is a branch", filtered),
                          ("callable before arguments", ordered)):
        assert _native_aliases(source), label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH],
                                    read_initial={"t": [1, 2], "x": [1, 2]})
        assert caught.value.code in ("state-aliased-in-construction",
                                     "state-sharing-unverified"), \
            (label, caught.value.code)

    starred = ("payload = [1, 2]\n"
               "saved = None\n"
               "def take():\n"
               "    return saved\n"
               "class Evil:\n"
               "    def __iter__(self):\n"
               "        global saved\n"
               "        saved = payload\n"
               "        return iter([1])\n"
               "evil = Evil()\n"
               "class M:\n"
               "    def reset(self):\n"
               "        self.t = payload\n"
               "        self.x = [0, 0]\n"
               "        _ = [*evil]\n"
               "        self.x = take()\n"
               "    def process(self, a):\n"
               "        return {'y': a}\n")
    assert _native_aliases(starred)
    model = source_model(starred, "behavior.py")
    found = reset_field_aliases(model, constants_in_scope(model))
    assert ("t", "x") in found, found


def test_sharing_is_attested_by_the_real_construction():
    """Review 36. Eight rounds of counterexamples settled it: a static
    reading of agent-written Python cannot close the alias channel --
    evaluation order in write targets, unpacking's __iter__, helpers writing
    the caller's containers, module names bound to one object, __setattr__
    -- so authority moved to a runtime witness. The isolated child really
    constructs and really resets, each field's object graph is walked by
    reference identity, two IR state arrays reaching one mutable node is a
    typed refusal, an unwalkable field under an IR array is one too, and a
    construction that cannot run at all is refused outright. The static
    interpreter remains a diagnostic.
    """
    import pytest

    from behave.sim.behavior.construction import ConstructionUnavailable, construction_facts
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    tail = ("    def process(self, a):\n"
            "        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    helpers = ("class M:\n"
               "    def release(self):\n"
               "        self.x = [0, 0]\n"
               "        return 0\n"
               "    def alias(self):\n"
               "        self.x = self.t\n"
               "        return 0\n"
               "    def reset(self):\n"
               "        self.t = [1, 2]\n"
               "        self.x = self.t\n")
    for label, line in (("nested write targets run inner-first",
                         "a[self.release()][self.alias()] = 0"),
                        ("augassign evaluates target first",
                         "a[self.release()][0] += self.alias()")):
        source = helpers + "        a = [[0]]\n        " + line + "\n" + tail
        assert _native_aliases(source), label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH],
                                    read_initial={"t": [1, 2], "x": [1, 2]})
        assert caught.value.code in ("state-aliased-in-construction",
                                     "state-sharing-unverified"), \
            (label, caught.value.code)

    # The witness sees what no reading saw: a helper writing through the
    # caller's container, and two module names for one object.
    box = ("def put(box, value):\n"
           "    box[0] = value\n"
           "class M:\n"
           "    def reset(self):\n"
           "        self.t = [1, 2]\n"
           "        box = [None]\n"
           "        put(box, self.t)\n"
           "        self.x = box[0]\n"
           "    def process(self, a):\n"
           "        return {'y': a}\n")
    assert _native_aliases(box)
    sharing_facts = construction_facts(box, 'behavior.py', cls_name='M')
    shared, opaque = (sharing_facts.shared_pairs, sharing_facts.opaque_fields)
    assert ("t", "x") in shared and not opaque

    twins = ("payload = [1, 2]\n"
             "alias = payload\n"
             "class M:\n"
             "    def reset(self):\n"
             "        self.t = payload\n"
             "        self.x = alias\n"
             "    def process(self, a):\n"
             "        return {'y': a}\n")
    assert _native_aliases(twins)
    sharing_facts = construction_facts(twins, 'behavior.py', cls_name='M')
    shared2, _ = (sharing_facts.shared_pairs, sharing_facts.opaque_fields)
    assert ("t", "x") in shared2

    # A dunder the sandbox cannot host makes the construction unverifiable,
    # which is a refusal, never a pass.
    hooked = ("class M:\n"
              "    def __setattr__(self, name, value):\n"
              "        object.__setattr__(self, name, value)\n"
              "    def reset(self):\n"
              "        self.t = [1, 2]\n"
              "        self.x = [0, 0]\n"
              "    def process(self, a):\n"
              "        return {'y': a}\n")
    with pytest.raises(ConstructionUnavailable):
        construction_facts(hooked, 'behavior.py', cls_name='M')


def test_alias_freedom_is_a_transition_invariant():
    """Review 37. The witness attests the state after reset; a step can mint
    the alias afterwards (self.x = ident(self.t) inlines to an array
    assignment the IR copies by value while Python shares), the witness
    cache keyed below semantic identity let call order flip an admission
    (__file__ reaches the program, and a bare hash has collisions), and the
    field table was read through the candidate's own attribute protocol.
    Now: an IR assignment binding one array to another is refused wherever
    either end is written in place; the cache key is the full source text,
    the file, the class and the protocol version; and the child reads
    __dict__ past __getattribute__, refusing anything but an exact dict.
    """
    import pytest

    from behave.sim.behavior.construction import ConstructionUnavailable, construction_facts
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    minted = ("def ident(v):\n"
              "    return v\n"
              "class M:\n"
              "    def reset(self):\n"
              "        self.t = [1, 2]\n"
              "        self.x = [0, 0]\n"
              "    def process(self, a):\n"
              "        if a == 0:\n"
              "            self.x = ident(self.t)\n"
              "        else:\n"
              "            self.x[0] = a\n"
              "        return {'y': self.t[0]}\n")
    sharing_facts = construction_facts(minted, 'behavior.py', cls_name='M')
    assert (sharing_facts.shared_pairs, sharing_facts.opaque_fields) == ((), ())
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(minted, input_widths=[WIDTH])
    assert caught.value.code == "array-assigned-by-reference"

    # Indistinguishable aliasing stays admitted: neither end is ever
    # written in place, so a value copy and a share never diverge.
    read_only = ("class M:\n"
                 "    def reset(self):\n"
                 "        self.tbl = [1, 2, 3, 4]\n"
                 "        self.acc = 0\n"
                 "    def process(self, a):\n"
                 "        row = self.tbl\n"
                 "        self.acc = (self.acc + row[a & 3]) & 15\n"
                 "        return {'y': self.acc}\n")
    lower_with_state_ranges(read_only, input_widths=[WIDTH])

    # The cache is keyed by full semantic identity: the same source under
    # two files is two constructions, whatever order they are asked in.
    filedep = ("class M:\n"
               "    def reset(self):\n"
               "        self.t = [1, 2]\n"
               "        self.x = [0, 0]\n"
               "        if __file__.endswith('b.py'):\n"
               "            self.x = self.t\n"
               "    def process(self, a):\n"
               "        self.x[0] = a\n"
               "        return {'y': self.t[0]}\n")
    from behave.sim.behavior.construction import clear_construction_cache

    clear_construction_cache()
    sharing_facts = construction_facts(filedep, 'a.py', cls_name='M')
    assert (sharing_facts.shared_pairs, sharing_facts.opaque_fields) == ((), ())
    sharing_facts = construction_facts(filedep, 'b.py', cls_name='M')
    assert (sharing_facts.shared_pairs, sharing_facts.opaque_fields) == ((('t', 'x'),), ())
    clear_construction_cache()
    sharing_facts = construction_facts(filedep, 'b.py', cls_name='M')
    assert (sharing_facts.shared_pairs, sharing_facts.opaque_fields) == ((('t', 'x'),), ())

    # A candidate that hooks the attribute protocol cannot pick the field
    # table the verifier sees: here the hook cannot even run under the
    # sandbox, and an unrunnable construction is a refusal, never a pass.
    hooked = ("class M:\n"
              "    def __getattribute__(self, name):\n"
              "        return object.__getattribute__(self, name)\n"
              "    def reset(self):\n"
              "        self.t = [1, 2]\n"
              "        self.x = [0, 0]\n"
              "    def process(self, a):\n"
              "        self.x[0] = a\n"
              "        return {'y': self.t[0]}\n")
    with pytest.raises(ConstructionUnavailable):
        construction_facts(hooked, 'behavior.py', cls_name='M')


def test_the_field_table_cannot_be_forged():
    """Review 38. The witness read the instance __dict__, but Python lets
    the class decide field semantics: a data descriptor made x a live view
    of t (native y=3 where the admitted IR computed y=1, a real divergence
    through the full production lowering), and a __get__-only stand-in
    under the __dict__ name forged the whole table object.__getattribute__
    returns. The certified subset is plain instance-backed fields now,
    guarded in the child before its first reply: a class that defines
    __getattribute__, __getattr__, __setattr__, __delattr__ or __slots__,
    redefines __dict__ or __weakref__, or carries any class-level data
    descriptor, cannot be constructed-and-witnessed, and every consumer --
    the witness, the snapshot, the audit view -- reads one guarded table.
    """
    import pytest

    from behave.sim.behavior.construction import ConstructionUnavailable, construction_facts
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    descriptor = ("class X:\n"
                  "    def __get__(self, obj, typ):\n"
                  "        return obj.t\n"
                  "    def __set__(self, obj, value):\n"
                  "        pass\n"
                  "class M:\n"
                  "    x = X()\n"
                  "    def reset(self):\n"
                  "        self.t = [1, 2]\n"
                  "        self.x = [0, 0]\n"
                  "    def process(self, a):\n"
                  "        self.x[0] = a\n"
                  "        return {'y': self.t[0]}\n")
    assert _native_aliases(descriptor)
    with pytest.raises(ConstructionUnavailable):
        construction_facts(descriptor, 'behavior.py', cls_name='M')
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(descriptor, input_widths=[WIDTH])
    assert caught.value.code == "state-sharing-unverified"

    forged = ("class D:\n"
              "    def __get__(self, obj, typ):\n"
              "        return {}\n"
              "class M:\n"
              "    __dict__ = D()\n"
              "    def reset(self):\n"
              "        self.t = [1, 2]\n"
              "        self.x = self.t\n"
              "    def process(self, a):\n"
              "        self.x[0] = a\n"
              "        return {'y': self.t[0]}\n")
    assert _native_aliases(forged)
    with pytest.raises(ConstructionUnavailable):
        construction_facts(forged, 'behavior.py', cls_name='M')

    # Plain classes pass the guard untouched: methods are __get__-only,
    # class constants are no descriptors, and the auto accessors stay.
    plain = ("class M:\n"
             "    WIDTH = 4\n"
             "    def helper(self):\n"
             "        return self.t[0]\n"
             "    def reset(self):\n"
             "        self.t = [1, 2]\n"
             "        self.x = [0, 0]\n"
             "    def process(self, a):\n"
             "        self.x[0] = a\n"
             "        return {'y': self.helper()}\n")
    sharing_facts = construction_facts(plain, 'behavior.py', cls_name='M')
    shared, opaque = (sharing_facts.shared_pairs, sharing_facts.opaque_fields)
    assert shared == () and opaque == ()


def test_the_class_and_module_are_frozen_invariants():
    """Review 39. Four ways past the guard: a class named getset_descriptor
    fooled a name comparison (exact interpreter types now); the guard ran
    once before reset, so reset installed a descriptor or swapped process
    for a twin diverging only off-audit (the class tables and MRO are
    fingerprinted before construction and re-checked after every call, and
    type(model) is cls); the module's own bindings were outside the
    universe, so reset mutated a table the lowering folded as constant
    (top-level bindings are frozen structurally); and the witness ran only
    for array state, leaving scalar models to the audit's sampled traces
    (it runs for every lowering now).
    """
    import pytest

    from behave.sim.behavior.construction import ConstructionUnavailable, construction_facts
    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    forged_name = ("class getset_descriptor:\n"
                   "    def __get__(self, obj, typ):\n"
                   "        return {}\n"
                   "class M:\n"
                   "    __dict__ = getset_descriptor()\n"
                   "    def reset(self):\n"
                   "        self.t = [1, 2]\n"
                   "        self.x = self.t\n"
                   "    def process(self, a):\n"
                   "        self.x[0] = a\n"
                   "        return {'y': self.t[0]}\n")
    assert _native_aliases(forged_name)
    with pytest.raises(ConstructionUnavailable):
        construction_facts(forged_name, 'behavior.py', cls_name='M')

    late_descriptor = ("class X:\n"
                       "    def __get__(self, obj, typ):\n"
                       "        return obj.t\n"
                       "    def __set__(self, obj, value):\n"
                       "        pass\n"
                       "class M:\n"
                       "    def reset(self):\n"
                       "        self.t = [1, 2]\n"
                       "        type(self).x = X()\n"
                       "        self.x = [0, 0]\n"
                       "    def process(self, a):\n"
                       "        self.x[0] = a\n"
                       "        return {'y': self.t[0]}\n")
    assert _native_aliases(late_descriptor)
    with pytest.raises(ConstructionUnavailable):
        construction_facts(late_descriptor, 'behavior.py', cls_name='M')

    swap = ("class M:\n"
            "    def reset(self):\n"
            "        self.__class__.process = self.__class__.other\n"
            "    def other(self, a):\n"
            "        return {'y': a + (a == 7)}\n"
            "    def process(self, a):\n"
            "        return {'y': a}\n")
    ns = {}
    exec(swap, ns)                                              # noqa: S102
    inst = ns["M"]()
    inst.reset()
    assert [inst.process(a=3)["y"], inst.process(a=7)["y"]] == [3, 8]
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(swap, input_widths=[WIDTH])
    assert caught.value.code in ("reflection-attribute-forbidden",
                                 "state-sharing-unverified")

    table = ("TABLE = {0: 1, 1: 2}\n"
             "class M:\n"
             "    def reset(self):\n"
             "        TABLE[0] = 9\n"
             "    def process(self, a):\n"
             "        return {'y': TABLE[0]}\n")
    ns2 = {}
    exec(table, ns2)                                            # noqa: S102
    inst2 = ns2["M"]()
    inst2.reset()
    assert inst2.process(a=0)["y"] == 9
    with pytest.raises(LoweringError) as caught2:
        lower_with_state_ranges(table, input_widths=[WIDTH])
    # The closed table contract owns this refusal; a later incidental gate is
    # not an acceptable substitute for proving the object read-only.
    assert caught2.value.code == "module-table-not-static"


def test_the_alias_analysis_has_pythons_control_semantics():
    """Review 40. Flattening if-arms in sequence let the untaken arm's
    fresh rebind erase the taken arm's alias, and a helper's returned field
    never entered the analysis; both shapes lowered while the IR diverged
    from the source at a=7. The analysis joins branches by union, runs
    loops to a fixpoint, and inlines helpers and module functions with
    their returns and side effects. And the freeze was shallow: a function
    kept its identity while its __code__ was swapped, on the class and on a
    module def alike, so images now carry code, defaults, closures and
    dicts, and every top-level binding -- def, class, import -- is in the
    universe.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_y(source, a):
        ns = {}
        exec(source, ns)                                        # noqa: S102
        inst = ns["M"]()
        inst.reset()
        return inst.process(a=a)["y"]

    branch = ("class M:\n"
              "    def reset(self):\n"
              "        self.t = [1, 2]\n"
              "        self.x = [0, 0]\n"
              "    def process(self, a):\n"
              "        if a:\n"
              "            self.x = self.t\n"
              "        else:\n"
              "            self.x = [0, 0]\n"
              "        self.x[0] = a\n"
              "        return {'y': self.t[0]}\n")
    helper_ret = ("class M:\n"
                  "    def take(self):\n"
                  "        return self.t\n"
                  "    def reset(self):\n"
                  "        self.t = [1, 2]\n"
                  "        self.x = [0, 0]\n"
                  "    def process(self, a):\n"
                  "        self.x = self.take()\n"
                  "        self.x[0] = a\n"
                  "        return {'y': self.t[0]}\n")
    for label, source in (("branch join", branch),
                          ("helper return", helper_ret)):
        assert native_y(source, 7) == 7, label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH])
        assert caught.value.code == "array-assigned-by-reference", \
            (label, caught.value.code)

    code_swap = ("class M:\n"
                 "    def reset(self):\n"
                 "        self.process.__func__.__code__ = "
                 "self.other.__func__.__code__\n"
                 "    def other(self, a):\n"
                 "        return {'y': a + (a == 7)}\n"
                 "    def process(self, a):\n"
                 "        return {'y': a}\n")
    fn_swap = ("def f(a):\n"
               "    return a\n"
               "def evil(a):\n"
               "    return a + (a == 7)\n"
               "class M:\n"
               "    def reset(self):\n"
               "        f.__code__ = evil.__code__\n"
               "    def process(self, a):\n"
               "        return {'y': f(a)}\n")
    for label, source in (("method code swap", code_swap),
                          ("module def code swap", fn_swap)):
        assert native_y(source, 7) == 8, label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH])
        assert caught.value.code in ("reflection-attribute-forbidden",
                                     "state-sharing-unverified"), \
            (label, caught.value.code)

    # The build-and-commit idiom survives the control-sensitive analysis:
    # every write precedes the bind, and the alias never outlives the step
    # with a second live name.
    commit = ("class M:\n"
              "    def reset(self):\n"
              "        self.pht = [0, 0, 0, 0]\n"
              "    def process(self, a):\n"
              "        new_pht = self.pht[:]\n"
              "        for i in range(4):\n"
              "            new_pht[i] = (new_pht[i] + a) & 3\n"
              "        self.pht = new_pht\n"
              "        return {'y': self.pht[a & 3]}\n")
    lower_with_state_ranges(commit, input_widths=[WIDTH])


def test_points_to_has_paths_exits_and_lexical_identity():
    """Reviews 41-42. Pairwise edges could not carry Python: an IfExp ran
    both arms in sequence, break and continue were no-ops so unreachable
    rebindings were analyzed, deleting one name's edges forgot the sharing
    the other two names kept, and one string served both self.x and a local
    x, so a shadowing local -- in the step or in a helper -- erased a
    field's sharing. The analysis is points-to now: allocation-site tokens,
    per-name sets, forked-and-joined environments for statements and
    conditional expressions alike, exit states for break/continue/return,
    and keys that carry lexical identity (("field", x) against
    ("local", frame, x), one frame per step or inlined call). The
    environment writes are refused statically, so __builtins__ cannot be
    rebound under the lowering either.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_y(source, a=7):
        ns = {}
        exec(source, ns)                                        # noqa: S102
        inst = ns["M"]()
        inst.reset()
        return inst.process(a=a)["y"]

    head = ("class M:\n"
            "    def bind(self):\n"
            "        self.x = self.t\n"
            "        return 0\n"
            "    def fresh(self):\n"
            "        self.x = [0, 0]\n"
            "        return 0\n"
            "    def take(self):\n"
            "        return self.t\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n")
    tail = ("        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    refused = {
        "an ifexp is a branch": head
        + "    def process(self, a):\n"
          "        self.bind() if a else self.fresh()\n" + tail,
        "break ends its path": head
        + "    def process(self, a):\n"
          "        for i in range(1):\n"
          "            self.x = self.take()\n"
          "            break\n"
          "            self.x = [0, 0]\n" + tail,
        "sharing survives a third name's rebind": head
        + "    def process(self, a):\n"
          "        if a:\n"
          "            x = self.t\n"
          "            y = x\n"
          "        else:\n"
          "            x = [0, 0]\n"
          "            y = [0, 0]\n"
          "        x = [0, 0]\n"
          "        y[0] = a\n"
          "        return {'y': self.t[0]}\n",
        "a step local does not shadow a field": head
        + "    def process(self, a):\n"
          "        self.x = self.take()\n"
          "        x = [0, 0]\n" + tail,
        "a helper local does not shadow a field": head
        + "    def shadow(self):\n"
          "        x = [0, 0]\n"
          "        return x[0]\n"
          "    def process(self, a):\n"
          "        self.x = self.take()\n"
          "        self.shadow()\n" + tail,
    }
    for label, source in refused.items():
        assert native_y(source) == 7, label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH])
        assert caught.value.code == "array-assigned-by-reference", \
            (label, caught.value.code)

    forbidden = ("def evil(n):\n"
                 "    return [0, 1]\n"
                 "class M:\n"
                 "    def reset(self):\n"
                 "        self.reset.__func__.__globals__[\n"
                 "            '__builtins__'\n"
                 "        ]['range'] = evil\n"
                 "    def process(self, a):\n"
                 "        y = 0\n"
                 "        for i in range(1):\n"
                 "            y = y + 1\n"
                 "        return {'y': y}\n")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(forbidden, input_widths=[WIDTH])
    assert caught.value.code == "reflection-attribute-forbidden"

    # A local that merely shares a field's name, with no aliasing at all,
    # stays admitted.
    benign = ("class M:\n"
              "    def reset(self):\n"
              "        self.t = [1, 2]\n"
              "        self.x = [0, 0]\n"
              "    def process(self, a):\n"
              "        x = [a, a]\n"
              "        self.x[0] = x[0]\n"
              "        return {'y': self.t[0]}\n")
    lower_with_state_ranges(benign, input_widths=[WIDTH])


def test_boolop_returns_an_operand_and_destructuring_is_elementwise():
    """Review 43. Two value-semantics holes: `a or b` returned an operand
    while the analysis minted a fresh token for it, and destructuring gave
    every target one shared set for the whole tuple. BoolOp forks its later
    operands and its value is the union of the operands' tokens now;
    destructuring evaluates the right side first and binds element-wise;
    and no unmodeled expression may claim freshness -- the default
    propagates its children's tokens. Alongside: an augmented assignment
    with any operator but += and *= is a scalar rebind (a sequence crashes
    under it), which is what keeps the population's inlined mask-and-shift
    helpers admitted, and an inlined body's allocation sites are distinct
    per call site.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_y(source, a=7):
        ns = {}
        exec(source, ns)                                        # noqa: S102
        inst = ns["M"]()
        inst.reset()
        return inst.process(a=a)["y"]

    head = ("class M:\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "    def process(self, a):\n")
    tail = ("        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    for label, line in (("or returns its operand",
                         "self.x = self.t or [0, 0]"),
                        ("destructuring is element-wise",
                         "self.x, dummy = self.t, [0, 0]")):
        source = head + "        " + line + "\n" + tail
        assert native_y(source) == 7, label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH])
        assert caught.value.code == "array-assigned-by-reference", \
            (label, caught.value.code)

    # The other side of each rule stays admitted: an element bound from the
    # fresh half of the tuple, and scalar helpers that mask with &= and
    # shift with >>= at several call sites.
    admitted = ("def mask16(v, b):\n"
                "    v &= (1 << b) - 1\n"
                "    v >>= 1\n"
                "    return v\n"
                "class M:\n"
                "    def reset(self):\n"
                "        self.t = [1, 2]\n"
                "        self.x = [0, 0]\n"
                "    def process(self, a):\n"
                "        dummy, self.x = self.t, [0, 0]\n"
                "        s0 = mask16(a, 8)\n"
                "        s1 = mask16(a, 4)\n"
                "        acc = s0\n"
                "        acc += s1\n"
                "        self.x[0] = acc & 3\n"
                "        return {'y': self.t[0]}\n")
    lower_with_state_ranges(admitted, input_widths=[WIDTH])


def test_effects_inside_fresh_containers_keep_their_paths():
    """Review 44. A fresh outer object does not make its inside
    unconditional: scanning containers for calls executed a short-circuited
    or zero-iteration effect that Python never runs, so the alias the taken
    path kept was "dissolved" by an untaken one. Container and subscript
    children run through the evaluator itself in Python's order now, and a
    comprehension inside an expression is the loop it is -- zero-iteration
    path, filter-false stops, nested generators' zero-yield stops, its own
    target scope.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError, source_model
    from behave.sim.behavior.lowering import lower_with_state_ranges
    from behave.sim.behavior.step_alias import step_alias_hazard

    head = ("class M:\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "    def fresh(self):\n"
            "        self.x = [0, 0]\n"
            "        return 0\n"
            "    def process(self, a):\n"
            "        self.x = self.t or [0, 0]\n")
    tail = ("        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")

    def native_y(source, a=7):
        ns = {}
        exec(source, ns)                                        # noqa: S102
        inst = ns["M"]()
        inst.reset()
        return inst.process(a=a)["y"]

    for label, line in (("zero-iteration comprehension",
                         "q = [self.fresh() for _ in []]"),
                        ("short-circuit inside a literal",
                         "q = [False and self.fresh()]")):
        source = head + "        " + line + "\n" + tail
        assert native_y(source) == 7, label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH])
        assert caught.value.code == "array-assigned-by-reference", \
            (label, caught.value.code)

    # The filter and nested-generator paths are pinned at the transfer
    # level: their full lowering trips an unrelated comprehension gate,
    # which soundness must not lean on.
    for label, line in (("filter-false keeps the alias",
                         "q = [self.fresh() for _ in [0] if a > 99]"),
                        ("nested zero-yield keeps the alias",
                         "q = [self.fresh() for i in [0] for j in []]")):
        source = head + "        " + line + "\n" + tail
        assert native_y(source) == 7, label
        model = source_model(source, "behavior.py")
        assert step_alias_hazard(model, {"t", "x"}) is not None, label

    # An unconditionally-run effect inside a container still dissolves:
    # the analysis must not become blind either.
    dissolving = head + "        q = [self.fresh()]\n" + tail
    assert native_y(dissolving) == 1
    lower_with_state_ranges(dissolving, input_widths=[WIDTH])


def test_iteration_yields_the_elements_not_fresh_objects():
    """Review 45. What an expression points to and what iterating it yields
    are different questions: [self.t] is a fresh list whose one element is
    self.t itself, and binding the loop target fresh let take(row) alias x
    to t invisibly, in comprehensions and plain fors alike. Literals record
    their elements now, iteration and subscripting hand them out, and a
    state variable whose shape the type pass cannot carry is a typed
    refusal instead of an unexecutable procedure.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_y(source, a=7):
        ns = {}
        exec(source, ns)                                        # noqa: S102
        inst = ns["M"]()
        inst.reset()
        return inst.process(a=a)["y"]

    comp = ("class M:\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "    def take(self, row):\n"
            "        self.x = row\n"
            "        return 0\n"
            "    def process(self, a):\n"
            "        q = [self.take(row) for row in [self.t]]\n"
            "        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    loop = ("class M:\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "    def process(self, a):\n"
            "        for row in [self.t]:\n"
            "            self.x = row\n"
            "        self.x[0] = a\n"
            "        return {'y': self.t[0]}\n")
    for label, source in (("comprehension target", comp),
                          ("for target", loop)):
        assert native_y(source) == 7, label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH])
        assert caught.value.code == "array-assigned-by-reference", \
            (label, caught.value.code)

    nested = ("class M:\n"
              "    def reset(self):\n"
              "        self.t = [[1, 2], [3, 4]]\n"
              "        self.x = [0, 0]\n"
              "    def process(self, a):\n"
              "        self.t[0] = [a, a]\n"
              "        self.x[0] = a\n"
              "        return {'y': self.x[0]}\n")
    proc = lower_with_state_ranges(nested, input_widths=[WIDTH])
    state, output, _hits, error = interp.run_step(proc, {"a": 7})
    assert not error
    assert state["t"] == [[7, 7], [3, 4]]
    assert output.transactions == [{"y": 7}]

    # Iterating flat data stays free: elements of an int array alias
    # nothing.
    flat = ("class M:\n"
            "    def reset(self):\n"
            "        self.t = [1, 2]\n"
            "        self.x = [0, 0]\n"
            "    def process(self, a):\n"
            "        acc = 0\n"
            "        for v in [1, 2, 3]:\n"
            "            acc = acc + v\n"
            "        self.x[0] = acc & 3\n"
            "        return {'y': self.t[0]}\n")
    lower_with_state_ranges(flat, input_widths=[WIDTH])


def test_nested_array_aliases_are_checked_by_object_path():
    """A recursive value is sound only while each mutable object has one path."""
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    local_repeat = ("class M:\n"
                    "    def reset(self):\n"
                    "        pass\n"
                    "    def process(self, a):\n"
                    "        row = [1, 2]\n"
                    "        t = [row, row]\n"
                    "        t[0][0] = a\n"
                    "        return {'y': t[1][0]}\n")
    state_child = ("class M:\n"
                   "    def reset(self):\n"
                   "        self.t = [[1, 2], [3, 4]]\n"
                   "    def process(self, a):\n"
                   "        row = self.t[0]\n"
                   "        row[0] = a\n"
                   "        return {'y': self.t[0][0]}\n")
    inserted = ("class M:\n"
                "    def reset(self):\n"
                "        pass\n"
                "    def process(self, a):\n"
                "        row = [1, 2]\n"
                "        t = [[3, 4]]\n"
                "        t[0] = row\n"
                "        row[0] = a\n"
                "        return {'y': t[0][0]}\n")

    for source in (local_repeat, state_child, inserted):
        namespace = {}
        exec(source, namespace)                                 # noqa: S102
        model = namespace["M"]()
        model.reset()
        assert model.process(a=9)["y"] == 9
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH])
        assert caught.value.code == "array-assigned-by-reference"

    # One path to each row needs no topology in the IR: direct nested stores
    # remain ordinary value-array updates.
    direct = ("class M:\n"
              "    def reset(self):\n"
              "        self.t = [[1, 2], [3, 4]]\n"
              "    def process(self, a):\n"
              "        self.t[0][0] = a\n"
              "        return {'y': self.t[1][0]}\n")
    lower_with_state_ranges(direct, input_widths=[WIDTH])


def test_names_resolve_by_pythons_binding_rules():
    """Review 46. One flat FunctionDef namespace inlined another class's
    method for a bare call Python would NameError on, inlined a decorated
    def as its body, and lowered a module def named reversed as the
    builtin; and reversed(x), though a fresh iterator, yields x's elements.
    Bare calls resolve only to the final undecorated unconditional
    top-level def now, self.f only to the chosen model's methods, builtin
    spellings only where nothing rebinds them (a local rebinding is refused
    outright), and carrier builtins record their arguments' elements.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    other_class = ("class Utility:\n"
                   "    def helper(self, v):\n"
                   "        return v + 1\n"
                   "class M:\n"
                   "    def reset(self):\n"
                   "        self.s = 0\n"
                   "    def process(self, a):\n"
                   "        return {'y': helper(a)}\n")
    decorated = ("def deco(fn):\n"
                 "    def evil(v):\n"
                 "        return v\n"
                 "    return evil\n"
                 "@deco\n"
                 "def helper(v):\n"
                 "    return v + 1\n"
                 "class M:\n"
                 "    def reset(self):\n"
                 "        self.s = 0\n"
                 "    def process(self, a):\n"
                 "        return {'y': helper(a)}\n")
    shadowed = ("def reversed(xs):\n"
                "    return []\n"
                "class M:\n"
                "    def reset(self):\n"
                "        self.s = 0\n"
                "    def process(self, a):\n"
                "        y = 0\n"
                "        for v in reversed([1]):\n"
                "            y = y + 1\n"
                "        return {'y': y}\n")
    for label, source, code in (
            ("another class's method", other_class, "call-unlowered"),
            ("a decorated def", decorated, "call-unlowered")):
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH])
        assert caught.value.code == code, (label, caught.value.code)

    # The module helper shadows the builtin, but its reachable return contract
    # proves that it yields an empty sequence.  The shared sequence authority
    # can therefore lower the zero-trip loop rather than refusing it merely
    # because the iterable came through a call.
    ns = {}
    exec(shadowed, ns)                                            # noqa: S102
    inst = ns["M"]()
    inst.reset()
    assert inst.process(a=7)["y"] == 0
    proc = lower_with_state_ranges(shadowed, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 7})
    assert not err and out.transactions[0]["y"] == 0

    # A local *named* range that is never called is just a local (review 47
    # superseded review 46's blanket refusal): natively y == a, and the
    # lowering agrees.
    local_rebind = ("class M:\n"
                    "    def reset(self):\n"
                    "        self.s = 0\n"
                    "    def process(self, a):\n"
                    "        range = a\n"
                    "        return {'y': range}\n")
    ns = {}
    exec(local_rebind, ns)                                      # noqa: S102
    inst = ns["M"]()
    inst.reset()
    assert inst.process(a=7)["y"] == 7
    proc = lower_with_state_ranges(local_rebind, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 7})
    assert not err and out.transactions[0]["y"] == 7

    carrier = ("class M:\n"
               "    def reset(self):\n"
               "        self.t = [1, 2]\n"
               "        self.x = [0, 0]\n"
               "    def process(self, a):\n"
               "        for row in reversed([self.t]):\n"
               "            self.x = row\n"
               "        self.x[0] = a\n"
               "        return {'y': self.t[0]}\n")
    ns = {}
    exec(carrier, ns)                                           # noqa: S102
    inst = ns["M"]()
    inst.reset()
    assert inst.process(a=7)["y"] == 7
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(carrier, input_widths=[WIDTH])
    assert caught.value.code == "array-assigned-by-reference"

    # An unshadowed genuine helper still inlines, and an unshadowed real
    # reversed still lowers as the builtin.
    genuine = ("def twice(v):\n"
               "    return v + v\n"
               "class M:\n"
               "    def reset(self):\n"
               "        self.s = 0\n"
               "    def process(self, a):\n"
               "        y = 0\n"
               "        for v in reversed([1, 2]):\n"
               "            y = y + v\n"
               "        return {'y': (y + twice(a)) & 15}\n")
    lower_with_state_ranges(genuine, input_widths=[WIDTH])


def test_one_lexical_resolution_for_calls_loops_and_aliases():
    """Review 47. Call lowering, the loop and shape rules, and the alias
    authority consume one ScopeResolver instead of flat tables: a local or
    parameter shadowing a helper or builtin denies the resolution Python
    would deny, a nested def neither leaks across functions nor loses to a
    same-named module def, an instance attribute shadows the method, and a
    nested def's return feeds the alias analysis.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_of(source, a=7):
        ns = {}
        exec(source, ns)                                        # noqa: S102
        inst = ns["M"]()
        inst.reset()
        try:
            return inst.process(a=a)["y"]
        except Exception as exc:                                # noqa: BLE001
            return type(exc).__name__

    local_shadows_helper = ("def helper(v):\n"
                            "    return v + 1\n"
                            "class M:\n"
                            "    def reset(self):\n"
                            "        self.s = 0\n"
                            "    def process(self, a):\n"
                            "        helper = a\n"
                            "        return {'y': helper(a)}\n")
    param_named_range = ("class M:\n"
                         "    def reset(self):\n"
                         "        self.s = 0\n"
                         "    def process(self, range):\n"
                         "        y = 0\n"
                         "        for i in range(2):\n"
                         "            y = y + 1\n"
                         "        return {'y': y}\n")
    nested_leaks = ("class M:\n"
                    "    def reset(self):\n"
                    "        self.s = 0\n"
                    "    def one(self, v):\n"
                    "        def deep(x):\n"
                    "            return x + 1\n"
                    "        return deep(v)\n"
                    "    def process(self, a):\n"
                    "        return {'y': deep(a)}\n")
    attr_shadows_method = ("class M:\n"
                           "    def helper(self, v):\n"
                           "        return v + 1\n"
                           "    def reset(self):\n"
                           "        self.helper = 0\n"
                           "        self.s = 0\n"
                           "    def process(self, a):\n"
                           "        return {'y': self.helper(a)}\n")
    module_rebinds_len = ("len = 99\n"
                          "class M:\n"
                          "    def reset(self):\n"
                          "        self.s = 0\n"
                          "    def process(self, a):\n"
                          "        return {'y': len([a])}\n")
    for label, source, native, code in (
            ("local shadows module helper", local_shadows_helper,
             "TypeError", "call-unlowered"),
            # the parameter denies the builtin loop rule, and what remains
            # is an opaque call rather than Python's range builtin
            ("parameter named range", param_named_range, "TypeError",
             "call-unlowered"),
            ("nested helper leaks across functions", nested_leaks,
             "NameError", "call-unlowered"),
            ("instance attr shadows method", attr_shadows_method,
             "TypeError", "call-unlowered"),
            ("module rebinds len", module_rebinds_len, "TypeError",
             "call-unlowered")):
        assert native_of(source) == native, label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH])
        assert caught.value.code == code, (label, caught.value.code)

    # A nested def wins over a same-named module def, exactly as Python
    # resolves it: native picks the inner one, and so does the lowering.
    nested_wins = ("def pick(v):\n"
                   "    return 1\n"
                   "class M:\n"
                   "    def reset(self):\n"
                   "        self.s = 0\n"
                   "    def process(self, a):\n"
                   "        def pick(v):\n"
                   "            return 2\n"
                   "        return {'y': pick(a)}\n")
    assert native_of(nested_wins) == 2
    proc = lower_with_state_ranges(nested_wins, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 7})
    assert not err and out.transactions[0]["y"] == 2

    # A nested def's return carries what it returns: aliasing self.t
    # through it is seen by the alias authority, not hidden by the frame.
    nested_aliases = ("class M:\n"
                      "    def reset(self):\n"
                      "        self.t = [1, 2]\n"
                      "        self.x = [0, 0]\n"
                      "    def process(self, a):\n"
                      "        def take():\n"
                      "            return self.t\n"
                      "        self.x = take()\n"
                      "        self.x[0] = a\n"
                      "        return {'y': self.t[0]}\n")
    assert native_of(nested_aliases) == 7
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(nested_aliases, input_widths=[WIDTH])
    assert caught.value.code == "array-assigned-by-reference", \
        caught.value.code

    # A nested def calling its sibling resolves through the enclosing
    # function's scope, exactly as the closure does (three population
    # models write their fixed-point helpers this way) -- but never
    # across enclosing functions.
    sibling = ("class M:\n"
               "    def reset(self):\n"
               "        self.s = 0\n"
               "    def process(self, a):\n"
               "        def sat(v):\n"
               "            return v & 7\n"
               "        def mul(x):\n"
               "            return sat(x + x)\n"
               "        self.s = mul(a)\n"
               "        return {'y': self.s}\n")
    assert native_of(sibling, a=3) == 6
    proc = lower_with_state_ranges(sibling, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 3})
    assert not err and out.transactions[0]["y"] == 6
    cross = ("class M:\n"
             "    def reset(self):\n"
             "        self.s = 0\n"
             "    def other(self):\n"
             "        def deep(x):\n"
             "            return x + 1\n"
             "        return 0\n"
             "    def process(self, a):\n"
             "        def outer(v):\n"
             "            return deep(v)\n"
             "        return {'y': outer(a)}\n")
    assert native_of(cross) == "NameError"
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(cross, input_widths=[WIDTH])
    assert caught.value.code == "call-unlowered"


def test_nested_functions_execute_as_python_binds_them():
    """Review 48. A def is a statement: its binding happens where it
    stands (a call above it is Python's UnboundLocalError), its defaults
    and annotations compute at that moment, and its body's free names live
    in the enclosing *functions* -- while an import binds its aliases
    wherever it sits, a class statement rebinds a method's name, a rebound
    self is an ordinary local, and a comprehension target never leaves the
    comprehension.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_of(source, a=3):
        ns = {}
        exec(source, ns)                                        # noqa: S102
        inst = ns["M"]()
        try:
            inst.reset()
            return inst.process(a=a)["y"]
        except Exception as exc:                                # noqa: BLE001
            return type(exc).__name__

    closure = ("class M:\n"
               "    def reset(self):\n"
               "        self.s = 0\n"
               "    def process(self, a):\n"
               "        def f(b):\n"
               "            def g():\n"
               "                return b\n"
               "            return g()\n"
               "        return {'y': f(a + 1)}\n")
    assert native_of(closure) == 4
    proc = lower_with_state_ranges(closure, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 3})
    assert not err and out.transactions[0]["y"] == 4

    comp_target = ("def helper(v):\n"
                   "    return v + 1\n"
                   "class M:\n"
                   "    def reset(self):\n"
                   "        self.s = 0\n"
                   "    def process(self, a):\n"
                   "        q = 0\n"
                   "        for t in [helper for helper in [1, 2]]:\n"
                   "            q = q + t\n"
                   "        return {'y': (helper(a) + q) & 15}\n")
    assert native_of(comp_target) == 7
    proc = lower_with_state_ranges(comp_target, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 3})
    assert not err and out.transactions[0]["y"] == 7

    before_def = ("class M:\n"
                  "    def reset(self):\n"
                  "        self.s = 0\n"
                  "    def process(self, a):\n"
                  "        y = f()\n"
                  "        def f():\n"
                  "            return 4\n"
                  "        return {'y': y}\n")
    walrus_default = ("class M:\n"
                      "    def reset(self):\n"
                      "        self.s = 0\n"
                      "    def process(self, a):\n"
                      "        y = 0\n"
                      "        def f(v=(y := 1)):\n"
                      "            return v\n"
                      "        return {'y': y}\n")
    trailing_import = ("def helper(v):\n"
                       "    return v + 1\n"
                       "class M:\n"
                       "    def reset(self):\n"
                       "        self.s = 0\n"
                       "    def process(self, a):\n"
                       "        return {'y': helper(a)}\n"
                       "        from os.path import join as helper\n")
    class_rebind = ("class M:\n"
                    "    def reset(self):\n"
                    "        self.s = 0\n"
                    "    def process(self, a):\n"
                    "        return {'y': a}\n"
                    "    process = 7\n")
    self_rebound = ("class M:\n"
                    "    def helper(self, v):\n"
                    "        return v + 1\n"
                    "    def reset(self):\n"
                    "        self.s = 0\n"
                    "    def process(self, a):\n"
                    "        self = 0\n"
                    "        return {'y': self.helper(a)}\n")
    for label, source, native, code in (
            ("call before def", before_def, "UnboundLocalError",
             "call-unlowered"),
            ("walrus default", walrus_default, 1, "nested-def-effects"),
            ("trailing local import", trailing_import, "UnboundLocalError",
             "source-import-unapproved"),
            ("class statement rebinds process", class_rebind, "TypeError",
             "method-rebound"),
            ("self rebound", self_rebound, "AttributeError",
             "self-rebound")):
        assert native_of(source) == native, label
        with pytest.raises(LoweringError) as caught:
            lower_with_state_ranges(source, input_widths=[WIDTH])
        assert caught.value.code == code, (label, caught.value.code)


def test_one_resolver_closes_the_review_49_false_accepts():
    """Review 49. The alias authority follows closure capture; a user
    method spelled like a builtin is effectful; a module constant does not
    fold under a shadowing parameter; a comprehension's target binds inside
    the comprehension; a nested function keeps a parameter spelled self;
    the inliner refuses keyword and unpacked arguments; and `del` unbinds a
    name at module and class scope. Every shape below diverges native/IR if
    admitted, so each must refuse (or, where legal, agree).
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_of(source, a=3):
        ns = {}
        try:
            exec(source, ns)                                    # noqa: S102
            inst = ns["M"]()
            inst.reset()
            if not callable(getattr(inst, "process", None)):
                return "no-process"
            return inst.process(a=a)["y"]
        except Exception as exc:                                # noqa: BLE001
            return type(exc).__name__

    def native_of_kw(source, **inputs):
        ns = {}
        exec(source, ns)                                        # noqa: S102
        inst = ns["M"]()
        inst.reset()
        return inst.process(**inputs)["y"]

    closure_alias = ("class M:\n"
                     "    def reset(self):\n"
                     "        self.t = [7, 0]\n"
                     "        self.x = [0, 0]\n"
                     "    def process(self, a):\n"
                     "        box = self.t\n"
                     "        def grab():\n"
                     "            return box\n"
                     "        self.x = grab()\n"
                     "        self.x[0] = a\n"
                     "        return {'y': self.t[0]}\n")
    comp_target_called = ("def helper(v):\n"
                          "    return v + 1\n"
                          "class M:\n"
                          "    def reset(self):\n"
                          "        self.s = 0\n"
                          "    def process(self, a):\n"
                          "        total = 0\n"
                          "        for v in [helper(a) for helper in [0]]:\n"
                          "            total = total + v\n"
                          "        return {'y': total}\n")
    comp_target_self = ("class M:\n"
                        "    def reset(self):\n"
                        "        self.x = 5\n"
                        "    def process(self, a):\n"
                        "        return {'y': [self.x for self in [0]]}\n")
    nested_self_param = ("class M:\n"
                         "    def reset(self):\n"
                         "        self.s = 0\n"
                         "    def process(self, a):\n"
                         "        def f(self):\n"
                         "            return 7\n"
                         "        return {'y': f()}\n")
    keyword_arg = ("class M:\n"
                   "    def reset(self):\n"
                   "        self.s = 0\n"
                   "    def f(self, a):\n"
                   "        return a\n"
                   "    def process(self, a):\n"
                   "        return {'y': self.f(a, x=7)}\n")
    del_module = ("def helper(v):\n"
                  "    return v + 1\n"
                  "del helper\n"
                  "class M:\n"
                  "    def reset(self):\n"
                  "        self.s = 0\n"
                  "    def process(self, a):\n"
                  "        return {'y': helper(a)}\n")
    del_class = ("class M:\n"
                 "    def reset(self):\n"
                 "        self.s = 0\n"
                 "    def process(self, a):\n"
                 "        return {'y': a}\n"
                 "    del process\n")
    for label, source in (
            ("closure captures aliasing local", closure_alias),
            ("comprehension target called", comp_target_called),
            ("comprehension target named self", comp_target_self),
            ("nested function parameter self", nested_self_param),
            ("keyword argument inlined", keyword_arg),
            ("del at module scope", del_module),
            ("del at class scope", del_class)):
        with pytest.raises(LoweringError):
            lower_with_state_ranges(source, input_widths=[WIDTH])

    # A parameter shadowing a module constant is the input, not the
    # constant: process(K=3) returns 3, and the lowering no longer folds
    # the module's K=1 (review 49).
    param_shadows_const = ("K = 1\n"
                           "class M:\n"
                           "    def reset(self):\n"
                           "        self.s = 0\n"
                           "    def process(self, K):\n"
                           "        return {'y': K}\n")
    assert native_of_kw(param_shadows_const, K=3) == 3
    proc = lower_with_state_ranges(param_shadows_const, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"K": 3})
    assert not err and out.transactions[0]["y"] == 3

    # A user method named like a builtin is effectful: self.x is captured
    # before the call that increments it, so the sum reads the old value.
    effectful_method = ("class M:\n"
                        "    def reset(self):\n"
                        "        self.x = 0\n"
                        "    def len(self):\n"
                        "        self.x = self.x + 1\n"
                        "        return 0\n"
                        "    def process(self, a):\n"
                        "        return {'y': self.x + self.len()}\n")
    assert native_of(effectful_method) == 0
    proc = lower_with_state_ranges(effectful_method, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 3})
    assert not err and out.transactions[0]["y"] == 0

    # A legal closure over an enclosing scalar, and a nested self-named
    # parameter actually passed, still lower and agree.
    legal_closure = ("class M:\n"
                     "    def reset(self):\n"
                     "        self.s = 0\n"
                     "    def process(self, a):\n"
                     "        b = a + 1\n"
                     "        def g():\n"
                     "            return b\n"
                     "        return {'y': g()}\n")
    assert native_of(legal_closure) == 4
    proc = lower_with_state_ranges(legal_closure, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 3})
    assert not err and out.transactions[0]["y"] == 4


def test_review_50_closes_the_remaining_native_ir_forks():
    """Review 50. A bare call is pure only if the resolver proves it a
    builtin; a comprehension target neither leaks into an inlined function
    nor loses an enclosing alias; a nested def is unavailable before its
    def statement has executed; and a positional-only parameter is refused.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_of(source, a=3):
        ns = {}
        try:
            exec(source, ns)                                    # noqa: S102
            inst = ns["M"]()
            inst.reset()
            return inst.process(a=a)["y"]
        except Exception as exc:                                # noqa: BLE001
            return type(exc).__name__

    bare_len = ("class M:\n"
                "    def reset(self):\n"
                "        self.x = 0\n"
                "    def process(self, a):\n"
                "        def len():\n"
                "            self.x = 1\n"
                "            return 0\n"
                "        return {'y': self.x + len()}\n")
    assert native_of(bare_len) == 0
    proc = lower_with_state_ranges(bare_len, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 3})
    assert not err and out.transactions[0]["y"] == 0

    comp_alias = ("class M:\n"
                  "    def reset(self):\n"
                  "        self.t = [7, 2]\n"
                  "        self.x = [0, 0]\n"
                  "    def process(self, a):\n"
                  "        box = self.t\n"
                  "        self.x = [box for k in [0]][0]\n"
                  "        self.x[0] = a\n"
                  "        return {'y': self.t[0]}\n")
    transitive_before = ("class M:\n"
                         "    def reset(self):\n"
                         "        self.s = 0\n"
                         "    def process(self, a):\n"
                         "        def g():\n"
                         "            return f()\n"
                         "        y = g()\n"
                         "        def f():\n"
                         "            return 4\n"
                         "        return {'y': y}\n")
    comp_before = ("class M:\n"
                   "    def reset(self):\n"
                   "        self.s = 0\n"
                   "    def process(self, a):\n"
                   "        q = [f(i) for i in range(1)]\n"
                   "        def f(i):\n"
                   "            return 4\n"
                   "        return {'y': q[0]}\n")
    posonly = ("class M:\n"
               "    def reset(self):\n"
               "        self.s = 0\n"
               "    def f(self, x, /):\n"
               "        return x\n"
               "    def process(self, a):\n"
               "        return {'y': self.f()}\n")
    renamed_recv = ("class M:\n"
                    "    def reset(self):\n"
                    "        self.s = 0\n"
                    "    def process(model, a):\n"
                    "        return {'y': a + 1}\n")
    for label, source in (
            ("comprehension frame keeps the enclosing alias", comp_alias),
            ("transitive call before def", transitive_before),
            ("comprehension call before def", comp_before),
            ("positional-only parameter", posonly),
            ("receiver not named self", renamed_recv)):
        with pytest.raises(LoweringError):
            lower_with_state_ranges(source, input_widths=[WIDTH])

    # A nested def called after its def statement, in the same order Python
    # binds it, still lowers and agrees.
    legal_order = ("class M:\n"
                   "    def reset(self):\n"
                   "        self.s = 0\n"
                   "    def process(self, a):\n"
                   "        def g():\n"
                   "            return f()\n"
                   "        def f():\n"
                   "            return 4\n"
                   "        return {'y': g()}\n")
    assert native_of(legal_order) == 4
    proc = lower_with_state_ranges(legal_order, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 3})
    assert not err and out.transactions[0]["y"] == 4


def test_review_51_closes_the_constant_and_closure_forks():
    """Review 51. The alias authority resolves a closure through its lexical
    enclosing frame, not the dynamic caller; a module constant carries its
    owner (a def rebinds it, a helper reads it past a caller's local); and a
    class constant comes from the chosen class only.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_of(source, a=3):
        ns = {}
        try:
            exec(source, ns)                                    # noqa: S102
            inst = ns["M"]()
            inst.reset()
            return inst.process(a=a)["y"]
        except Exception as exc:                                # noqa: BLE001
            return type(exc).__name__

    closure_comp = ("class M:\n"
                    "    def reset(self):\n"
                    "        self.t = [7, 2]\n"
                    "        self.x = [0, 0]\n"
                    "    def process(self, a):\n"
                    "        box = self.t\n"
                    "        def grab():\n"
                    "            return box\n"
                    "        self.x = [grab() for box in [0]][0]\n"
                    "        self.x[0] = a\n"
                    "        return {'y': self.t[0]}\n")
    closure_sibling = ("class M:\n"
                       "    def reset(self):\n"
                       "        self.t = [7, 2]\n"
                       "        self.x = [0, 0]\n"
                       "    def process(self, a):\n"
                       "        box = self.t\n"
                       "        def grab():\n"
                       "            return box\n"
                       "        def caller(box):\n"
                       "            return grab()\n"
                       "        self.x = caller(0)\n"
                       "        self.x[0] = a\n"
                       "        return {'y': self.t[0]}\n")
    def_rebinds = ("K = 1\n"
                   "def K():\n"
                   "    return 4\n"
                   "class M:\n"
                   "    def reset(self):\n"
                   "        self.s = 0\n"
                   "    def process(self, a):\n"
                   "        return {'y': K}\n")
    for label, source in (("closure via comprehension", closure_comp),
                          ("closure via sibling caller", closure_sibling),
                          ("a def rebinds a module constant", def_rebinds)):
        with pytest.raises(LoweringError):
            lower_with_state_ranges(source, input_widths=[WIDTH])

    # A helper reads the module constant even when the caller has a local of
    # that name: the constant carries its owner (the module), so the caller's
    # local shadows only in the caller.
    helper_const = ("K = 7\n"
                    "def helper():\n"
                    "    return K\n"
                    "class M:\n"
                    "    def reset(self):\n"
                    "        self.s = 0\n"
                    "    def process(self, a):\n"
                    "        K = 4\n"
                    "        return {'y': helper() + (K - K)}\n")
    assert native_of(helper_const) == 7
    proc = lower_with_state_ranges(helper_const, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 3})
    assert not err and out.transactions[0]["y"] == 7

    # A class constant comes from the chosen class, not another class that
    # happens to bind the same name.
    two_class = ("class Static:\n"
                 "    K = 1\n"
                 "class M:\n"
                 "    K = 2\n"
                 "    def reset(self):\n"
                 "        self.s = 0\n"
                 "    def process(self, a):\n"
                 "        return {'y': self.K}\n")
    assert native_of(two_class) == 2
    proc = lower_with_state_ranges(two_class, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 3})
    assert not err and out.transactions[0]["y"] == 2


def test_review_51_constant_owner_is_scoped_in_structural_analysis():
    """Review 51 (final). The owner-aware constant authority reaches the
    structural analysis too: a `range(N)` folds the model module's N, not an
    unrelated class's N, and a class-body augmented assignment is not a
    foldable constant."""
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_of(source, a=3):
        ns = {}
        try:
            exec(source, ns)                                    # noqa: S102
            inst = ns["M"]()
            inst.reset()
            return inst.process(a=a)["y"]
        except Exception as exc:                                # noqa: BLE001
            return type(exc).__name__

    # range(N) folds the model module's N=3, not Static.N=2.
    cross_class = ("N = 3\n"
                   "class Static:\n"
                   "    N = 2\n"
                   "class M:\n"
                   "    def reset(self):\n"
                   "        self.s = 0\n"
                   "    def process(self, a):\n"
                   "        y = 0\n"
                   "        for i in range(N):\n"
                   "            y = y + 1\n"
                   "        return {'y': y}\n")
    assert native_of(cross_class) == 3
    proc = lower_with_state_ranges(cross_class, input_widths=[WIDTH])
    _, out, _, err = interp.run_step(proc, {"a": 3})
    assert not err and out.transactions[0]["y"] == 3

    # A class body that augments a constant does not fold it (final binding).
    augmented = ("class M:\n"
                 "    K = 1\n"
                 "    K += 1\n"
                 "    def reset(self):\n"
                 "        self.s = 0\n"
                 "    def process(self, a):\n"
                 "        return {'y': self.K}\n")
    assert native_of(augmented) == 2
    with pytest.raises(LoweringError):
        lower_with_state_ranges(augmented, input_widths=[WIDTH])


def test_review_51_owner_aware_constant_authority():
    """Review 51 (rejected round). One owner-aware constant authority feeds
    the value fold AND the structural consumers (class-constant RHS, array
    shape, initial state, slice/loop bounds): a class constant folds its
    module's value not another class's, an array sized by a module constant
    is not shadowed by a same-named class attribute, a match-case rebinding
    disqualifies the constant, and a comprehension local shadows a slice
    bound. No shape lowers to an invalid Procedure -- each agrees or refuses.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_of(source, **inputs):
        ns = {}
        try:
            exec(source, ns)                                    # noqa: S102
            inst = ns["M"]()
            inst.reset()
            return inst.process(**inputs)["y"]
        except Exception as exc:                                # noqa: BLE001
            return type(exc).__name__

    def agree(source, val, **inputs):
        assert native_of(source, **inputs) == val
        proc = lower_with_state_ranges(
            source, input_widths=[WIDTH] * max(len(inputs), 1))
        _, out, _, err = interp.run_step(proc, inputs)
        assert not err and out.transactions[0]["y"] == val

    # class constant RHS folds the module's N, not Static's
    agree("N = 3\n"
          "class Static:\n"
          "    N = 2\n"
          "class M:\n"
          "    K = N\n"
          "    def reset(self):\n"
          "        self.s = 0\n"
          "    def process(self, a):\n"
          "        return {'y': self.K}\n", 3, a=0)

    # an array sized by the module N, not the same-named class attribute
    agree("N = 3\n"
          "class M:\n"
          "    N = 2\n"
          "    def reset(self):\n"
          "        self.buf = [0] * N\n"
          "    def process(self, a):\n"
          "        self.buf[a] = 1\n"
          "        return {'y': self.buf[a]}\n", 1, a=2)

    # a match-case rebinding disqualifies the constant; the bare value read
    # is refused rather than folded or crashing the interpreter
    match_rebind = ("K = 1\n"
                    "match 2:\n"
                    "    case K:\n"
                    "        pass\n"
                    "class M:\n"
                    "    def reset(self):\n"
                    "        self.s = 0\n"
                    "    def process(self, a):\n"
                    "        return {'y': K}\n")
    assert native_of(match_rebind, a=0) == 2
    with pytest.raises(LoweringError):
        lower_with_state_ranges(match_rebind, input_widths=[WIDTH])


def test_frontend_version_is_pinned():
    """Semantic frontend changes require an explicit evidence-version bump.

    Version 174 excludes nested declaration bodies from the enclosing function's
    source scan, including declarations directly in that function's body.
    Version 180 forgets pre-loop element facts of containers that a loop body stores into.
    Version 181 lets a loop reach its range fixed point although its target copies the
    index, and refines cast copies with the operands a recovered guard narrows.
    Version 182 replays a long loop that stores a loop-carried value into a container
    instead of planning it from one visit's stale element envelope.
    """
    from behave.sim.behavior.instance import FRONTEND

    assert FRONTEND == "behavior/182"


def test_review_52_owner_per_occurrence_and_namespaces():
    """Review 52. One owner-aware authority resolved per AST occurrence
    feeds every structural consumer (array shape, initial state, append and
    output capacity); a constructor parameter is that constructor's local,
    not the module's constant; module tables mutated after their literal are
    disqualified; a module value colliding with an instance-state field is
    refused; imported library constants fold in a class RHS; a class-body
    comprehension neither declares a step goal nor contributes a member.
    """
    import pytest

    from behave.sim.behavior.extract import LoweringError
    from behave.sim.behavior.lowering import lower_with_state_ranges

    def native_of(source, **inputs):
        import os
        import sys
        lib = os.path.join(os.path.dirname(__file__), "..", "..", "src",
                           "behave", "sim", "behavior")
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)
        ns = {}
        try:
            exec(source, ns)                                    # noqa: S102
            inst = ns["M"]()
            inst.reset()
            return inst.process(**inputs)["y"]
        except Exception as exc:                                # noqa: BLE001
            return type(exc).__name__

    def agree(source, val, **inputs):
        assert native_of(source, **inputs) == val
        proc = lower_with_state_ranges(
            source, input_widths=[WIDTH] * max(len(inputs), 1))
        _, out, _, err = interp.run_step(proc, inputs)
        assert not err and out.transactions[0]["y"] == val

    # append/output capacity sized by the module N, not the class attribute
    agree("N = 3\n"
          "class M:\n"
          "    N = 2\n"
          "    def reset(self):\n"
          "        self.s = 0\n"
          "    def process(self, a):\n"
          "        out = []\n"
          "        for i in range(N):\n"
          "            out.append(i)\n"
          "        return {'y': len(out)}\n", 3, a=0)

    # a class-body comprehension's local is not a member, and its goal is
    # not the step's; the step lowers trivially
    agree("class M:\n"
          "    xs = [i for i in range(3)]\n"
          "    def reset(self):\n"
          "        self.s = 0\n"
          "    def process(self, a):\n"
          "        return {'y': a}\n", 5, a=5)

    match_capture = ("match 2:\n"
                     "    case K:\n"
                     "        pass\n"
                     "class M:\n"
                     "    def reset(self):\n"
                     "        self.s = 0\n"
                     "    def process(self, a):\n"
                     "        return {'y': K}\n")
    with pytest.raises(LoweringError) as caught:
        lower_with_state_ranges(match_capture, input_widths=[WIDTH])
    assert caught.value.code == "name-not-a-value", caught.value.code

    # Module collections are compile-time references, whereas ``self.ARR`` is
    # an IR state variable.  Their source spelling may coincide without
    # joining their namespaces.
    collision = ("ARR = [7]\n"
                 "class M:\n"
                 "    def reset(self):\n"
                 "        self.ARR = [1]\n"
                 "    def process(self, a):\n"
                 "        return {'y': ARR[0] + 0 * self.ARR[0]}\n")
    agree(collision, 7, a=0)


@pytest.mark.parametrize("lowerer", (_Lowerer, GraphLowerer),
                         ids=("term", "graph"))
def test_recursive_record_option_and_sequence_pass_the_native_audit(lowerer):
    from behave.sim.behavior import audit

    source = _model([
        'row = ({"x": a, "cells": [a, a + 1]} if a else None)',
        'if row is None:',
        '    y = 7',
        'else:',
        '    y = row["x"] * 10 + row["cells"][1]',
        'return {"y": y}',
    ])
    namespace = {}
    exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
    proc = lower_with_state_ranges(
        source, input_widths=[WIDTH], lowerer=lowerer)

    assert audit.model_audit(
        proc, namespace["M"], [[{"a": 0}, {"a": 3}]]) == []


@pytest.mark.parametrize("lowerer", (_Lowerer, GraphLowerer),
                         ids=("term", "graph"))
def test_batch_carriers_follow_none_refinement_and_control_flow_join(lowerer):
    from behave.sim.behavior import audit

    present_first = _model([
        'row = ({"x": a} if a else None)',
        'if row is not None:',
        '    y = row["x"]',
        'else:',
        '    y = 7',
        'return {"y": y}',
    ])
    optional_list = _model([
        'rows = ([{"x": a}, {"x": a + 1}] if a else None)',
        'if rows is None:',
        '    y = 7',
        'else:',
        '    y = rows[1]["x"]',
        'return {"y": y}',
    ])
    joined = _model([
        'row = ({"x": a} if a else None)',
        'if a:',
        '    y = 1',
        'else:',
        '    y = 2',
        'if row is None:',
        '    y = 7',
        'else:',
        '    y = row["x"]',
        'return {"y": y}',
    ])

    for source in (present_first, optional_list, joined):
        namespace = {}
        exec(compile(source, "behavior.py", "exec"), namespace)  # noqa: S102
        proc = lower_with_state_ranges(
            source, input_widths=[WIDTH], lowerer=lowerer)
        assert audit.model_audit(
            proc, namespace["M"], [[{"a": 0}, {"a": 3}]]) == []
