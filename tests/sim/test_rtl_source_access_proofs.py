"""Access proofs preserve correlations, input domains and executed prefixes."""

import pytest

from behave.sim.symbolic import ir, ranges, typecheck as types
from behave.sim.core.refusal import Refusal, Refused
from behave.sim.rtl import source_domains

pytestmark = pytest.mark.solver


def _procedure(body, state=("a",), locals_=("x", "y", "index", "value")):
    return ir.Procedure(state, [], locals_, [], body,
                        types={name: types.INTEGER for name in (*state, *locals_)})


def _obligation(access, index, extent):
    return {id(access): (index, 1, extent, Refusal("access-domain", "not proved in range"))}


@pytest.mark.parametrize("last,safe", [(0, True), (1, False)])
def test_correlated_alternatives_use_exact_query_without_changing_source(last, safe):
    access = ir.Assign("value", ir.Lit(7))
    body = ir._sequence([
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)),
              ir.Seq(ir.Assign("x", ir.Lit(0)), ir.Assign("y", ir.Lit(3))),
              ir.Seq(ir.Assign("x", ir.Lit(3)), ir.Assign("y", ir.Lit(last)))),
        ir.Assign("index", ir.Op("add", ir.Var("x"), ir.Var("y"))), access,
        ir.Mark("original-goal"),
    ])
    proc = _procedure(body)
    original = repr(proc.body)
    bounds = {"a": ranges.Range(0, 1)}
    if safe:
        source_domains.prove_source_domains(proc, bounds, _obligation(access, ir.Var("index"), 4))
    else:
        with pytest.raises(Refused) as got:
            source_domains.prove_source_domains(proc, bounds, _obligation(access, ir.Var("index"), 4))
        assert got.value.refusal.as_dict()["detail"]["domain_proof"] == "sat"
    assert repr(proc.body) == original
    assert ir.goals_of(proc.body) == {"original-goal"}
    assert bounds == {"a": ranges.Range(0, 1)}


def test_proof_does_not_replace_entry_domain_with_postcondition():
    access = ir.Assign("value", ir.Lit(7))
    body = ir.Seq(access, ir.Assign("a", ir.Lit(0)))
    proc = _procedure(body)
    bounds = {"a": ranges.Range(0, 9)}
    with pytest.raises(Refused) as got:
        source_domains.prove_source_domains(proc, bounds, _obligation(access, ir.Var("a"), 4))
    assert got.value.refusal.as_dict()["detail"]["domain_proof"] == "sat"
    assert bounds["a"] == ranges.Range(0, 9)


def test_activation_proof_does_not_derive_cross_step_invariants(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("an activation-domain query must use its declared entry ranges")

    monkeypatch.setattr(ranges, "fixpoint", unexpected)
    test_correlated_alternatives_use_exact_query_without_changing_source(0, True)
    test_proof_does_not_replace_entry_domain_with_postcondition()


def test_proof_deadline_is_checked_inside_formula_construction(monkeypatch):
    from types import SimpleNamespace

    from behave.sim.symbolic import encoder

    now = [0.0]
    monkeypatch.setattr(source_domains, "time", SimpleNamespace(monotonic=lambda: now[0]))
    original = encoder._Commands.expr

    def expire(self, *args, **kwargs):
        now[0] = 1.0
        return original(self, *args, **kwargs)

    monkeypatch.setattr(encoder._Commands, "expr", expire)
    access = ir.Assign("value", ir.Lit(7))
    with pytest.raises(Refused) as got:
        source_domains.prove_source_domains(
            _procedure(access), {"a": ranges.Range(0, 9)},
            _obligation(access, ir.Var("a"), 4), timeout_ms=500)
    assert got.value.refusal.detail["domain_proof"] == "unknown"


def test_failure_after_bad_access_does_not_hide_its_mark():
    access = ir.Assign("value", ir.Lit(7))
    proc = _procedure(ir.Seq(access, ir.Fail("assertion", "original-failure")))
    with pytest.raises(Refused) as got:
        source_domains.prove_source_domains(proc, {"a": ranges.Range(0, 9)},
                                            _obligation(access, ir.Var("a"), 4))
    assert got.value.refusal.as_dict()["detail"]["domain_proof"] == "sat"


def test_interval_success_does_not_construct_a_solver(monkeypatch):
    access = ir.Assign("value", ir.Lit(7))
    proc = _procedure(access)

    def unexpected(*args):
        pytest.fail("a sufficient interval proof must not start exact encoding")

    monkeypatch.setattr(source_domains, "_prove_exact", unexpected)
    source_domains.prove_source_domains(proc, {"a": ranges.Range(0, 3)},
                                        _obligation(access, ir.Var("a"), 4))


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("defer", [False, True])
def test_fixed_definition_mask_needs_no_symbolic_formula(monkeypatch, partial, defer):
    mask = ir.Var("x")
    defined = ir.Op("eq", ir.Op("and", mask, ir.Lit(255)), ir.Lit(255))
    check = ir.Assign("value", ir.Lit(7))
    write = (ir._sequence([ir.Assign("x", ir.Lit(15)),
                          ir.Assign("x", ir.Op("or", mask, ir.Lit(240)))])
             if partial else ir.Assign("x", ir.Lit(255)))
    proc = _procedure(ir.Seq(write, check))

    def unexpected(*args):
        pytest.fail("a fixed fully defined mask is already proved by interval transfer")

    monkeypatch.setattr(source_domains, "_prove_exact", unexpected)
    assert source_domains.prove_source_domains(proc, {"a": ranges.Range(0, 3)}, {},
        predicates={id(check): (defined, Refusal("definition", "missing bits"))},
        defer=defer) is None


@pytest.mark.parametrize("complete", [False, True])
def test_deferred_local_join_preserves_missing_return_arm(monkeypatch, complete):
    def unexpected(*args):
        pytest.fail("deferred domain transfer must not start an SMT query")

    monkeypatch.setattr(source_domains, "_prove_exact", unexpected)
    check = ir.Assign("value", ir.Lit(7))
    proc = _procedure(ir._sequence([
        ir.Assign("x", ir.Lit(0)),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)), ir.Assign("x", ir.Lit(255)),
              ir.Assign("x", ir.Lit(255)) if complete else ir.Skip()),
        check, ir.Mark("original-goal"),
    ]))
    original = repr(proc.body)
    pending = source_domains.prove_source_domains(proc, {"a": ranges.Range(0, 0)}, {},
        predicates={id(check): (ir.Op("eq", ir.Var("x"), ir.Lit(255)),
                               Refusal("definition", "missing return"))}, defer=True)
    assert bool(pending) is not complete
    assert repr(proc.body) == original
    assert ir.goals_of(proc.body) == {"original-goal"}


@pytest.mark.parametrize("position", ["before", "after", "none"])
def test_deferred_transfer_does_not_use_entry_ranges_or_future_writes(position):
    access = ir.Assign("value", ir.Lit(7))
    write = ir.Assign("a", ir.Lit(0))
    body = (ir.Seq(write, access) if position == "before" else
            ir.Seq(access, write) if position == "after" else access)
    bounds = {"a": ranges.Range(0, 0)}
    pending = source_domains.prove_source_domains(_procedure(body), bounds,
        _obligation(access, ir.Var("a"), 4), defer=True)
    assert bool(pending) is (position != "before")
    assert bounds == {"a": ranges.Range(0, 0)}


@pytest.mark.parametrize("trip_count,safe", [(4, True), (5, False)])
def test_deferred_checks_cover_every_loop_occurrence(trip_count, safe):
    access = ir.Assign("value", ir.Lit(7))
    proc = _procedure(ir.For("index", ir.Lit(trip_count), access))
    pending = source_domains.prove_source_domains(proc, {},
        _obligation(access, ir.Var("index"), 4), defer=True)
    assert bool(pending) is not safe


@pytest.mark.parametrize("unknown_first", [False, True])
def test_deferred_unknown_inner_loop_cannot_reuse_a_safe_outer_iteration(unknown_first):
    access = ir.Assign("value", ir.Lit(7))
    body = ir._sequence([
        ir.If(ir.Op("eq", ir.Var("x"), ir.Lit(int(unknown_first))),
              ir.Assign("y", ir.Lit(1)), ir.Assign("y", ir.Var("a"))),
        ir.For("index", ir.Var("y"), access),
    ])
    proc = _procedure(ir.For("x", ir.Lit(2), body))
    pending = source_domains.prove_source_domains(proc, {"a": ranges.Range(1, 1)},
        _obligation(access, ir.Var("index"), 4), defer=True)
    assert id(access) in pending


def test_deferred_divisor_checks_retain_zero_on_any_path():
    access = ir.Assign("value", ir.Lit(7))
    for nonzero in (False, True):
        proc = _procedure(ir.Seq(
            ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)), ir.Assign("x", ir.Lit(1)),
                  ir.Assign("x", ir.Lit(2 if nonzero else 0))), access))
        pending = source_domains.prove_source_domains(proc, {}, {},
            divisors={id(access): (ir.Var("x"), Refusal("divisor", "zero"))}, defer=True)
        assert bool(pending) is not nonzero


def test_sized_copied_clamp_proves_access_without_a_solver(monkeypatch):
    condition = ir.Op("truth", ir.Op("uwrap", ir.Op("toint",
        ir.Op("gt", ir.Var("y"), ir.Lit(3))), ir.Lit(1)))
    access = ir.Assign("value", ir.Lit(7))
    proc = _procedure(ir._sequence([
        ir.Assign("x", ir.Var("a")), ir.Assign("y", ir.Var("x")),
        ir.If(condition, ir.Assign("x", ir.Lit(3)), ir.Skip()), access,
    ]))

    def unexpected(*args):
        pytest.fail("the executed clamp already proves the access domain")

    monkeypatch.setattr(source_domains, "_prove_exact", unexpected)
    source_domains.prove_source_domains(proc, {"a": ranges.Range(0, 15)},
                                       _obligation(access, ir.Var("x"), 4))


@pytest.mark.eda("slang", "verilator", "make")
def test_copied_source_clamp_preserves_native_outputs_and_hits(tmp_path, monkeypatch):
    from .test_rtl_source_ir import _native_agreement

    def unexpected(*args):
        pytest.fail("sized source clamp should not require a symbolic access proof")

    monkeypatch.setattr(source_domains, "_prove_exact", unexpected)
    source = """module M(input clk,input [3:0] a,output logic [7:0] q);
      always @(posedge clk) begin
        int x;
        logic [7:0] bits;
        x=a; if(x>3) x=3;
        bits={a,a}; q=bits[2*x+:2];
      end
    endmodule"""
    _native_agreement(tmp_path, source, initial={"x": -9, "bits": 255})


def test_predicate_discharge_uses_only_the_executed_program_point():
    predicate = ir.Op("eq", ir.Op("and", ir.Var("x"), ir.Lit(255)), ir.Lit(255))
    check = ir.Assign("value", ir.Lit(7))
    proc = _procedure(ir._sequence([
        ir.Assign("x", ir.Lit(15)),
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)), ir.Assign("x", ir.Lit(255)), ir.Skip()),
        check,
        ir.Assign("x", ir.Lit(255)),
    ]))
    with pytest.raises(Refused) as got:
        source_domains.prove_source_domains(proc, {"a": ranges.Range(0, 1)}, {},
            predicates={id(check): (predicate, Refusal("definition", "missing bits"))})
    assert got.value.refusal.detail["domain_proof"] == "sat"


def test_explicit_unlimited_solver_memory_is_supported(monkeypatch):
    from behave.sim.core import resources

    monkeypatch.setattr(resources, "solver_memory_mb", lambda: None)
    test_correlated_alternatives_use_exact_query_without_changing_source(0, True)


@pytest.mark.parametrize("answer", ["unknown", "exception"])
def test_unknown_and_solver_failure_do_not_admit_source(monkeypatch, answer):
    import z3

    class Unanswered:
        def add(self, *terms):
            pass

        def set(self, **settings):
            pass

        def check(self):
            if answer == "exception":
                raise z3.Z3Exception("forced backend failure")
            return z3.unknown

        def reason_unknown(self):
            return "forced unknown"

    monkeypatch.setattr(z3, "Solver", Unanswered)
    access = ir.Assign("value", ir.Lit(7))
    proc = _procedure(access)
    with pytest.raises(Refused) as got:
        source_domains.prove_source_domains(proc, {"a": ranges.Range(0, 9)},
                                            _obligation(access, ir.Var("a"), 4))
    assert got.value.refusal.as_dict()["detail"]["domain_proof"] == (
        "unknown" if answer == "unknown" else "unavailable")


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("high", [3, 4])
def test_correlated_packed_write_matches_native_or_refuses(tmp_path, high):
    from .test_rtl_source_ir import _lower, _native_agreement

    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      always @(posedge clk) begin
        int x,y;
        q=0;
        if(a[0]) begin x=3; y={high}-3; end else begin x=0; y=3; end
        q[x+y]=a[1];
      end
    endmodule"""
    if high == 3:
        _native_agreement(tmp_path, source, initial={"x": 17, "y": -8})
    else:
        with pytest.raises(Refused, match="packed-write-domain-unproved"):
            _lower(tmp_path, source)


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("high", [3, 4, 8])
@pytest.mark.parametrize("select", ["bits[x+y]", "bits[2*(x+y)+:2]", "bits[2*(x+y)+1-:2]"])
def test_correlated_packed_read_matches_native_or_refuses(tmp_path, high, select):
    from .test_rtl_source_ir import _lower, _native_agreement

    source = f"""module M(input clk,input [3:0] a,output logic [7:0] q);
      always @(posedge clk) begin
        int x,y;
        logic [7:0] bits;
        bits={{a,a}};
        if(a[0]) begin x=3; y={high}-3; end else begin x=0; y=3; end
        q={select};
      end
    endmodule"""
    if high == 3 or (select == "bits[x+y]" and high < 8):
        _native_agreement(tmp_path, source, initial={"x": 17, "y": -8, "bits": 255})
    else:
        with pytest.raises(Refused, match="packed-read-domain-unproved"):
            _lower(tmp_path, source)


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("guarded", [False, True])
def test_packed_read_requires_its_executed_index_to_be_nonnegative(tmp_path, guarded):
    from .test_rtl_source_ir import _lower, _native_agreement

    guard = "if (index >= 0)" if guarded else ""
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      always @(posedge clk) begin
        int index;
        index=-1; q=0;
        if(a[0]) index=2;
        {guard} q=a[index];
      end
    endmodule"""
    if guarded:
        _native_agreement(tmp_path, source, initial={"index": 99})
    else:
        with pytest.raises(Refused, match="packed-read-domain-unproved"):
            _lower(tmp_path, source)


@pytest.mark.eda("slang", "verilator", "make")
def test_packed_read_evaluates_effectful_selector_once(tmp_path):
    from .test_rtl_source_ir import _native_agreement

    source = """module M(input clk,input [3:0] a,output logic [3:0] q);
      always @(posedge clk) begin
        int index;
        index=0;
        q=a[index++]; q=q+index;
      end
    endmodule"""
    # A separate following read has a defined order; conflicting operands in
    # one expression must not gain an invented order from this translation.
    _native_agreement(tmp_path, source, initial={"index": 99})


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("width", [8, 7])
@pytest.mark.parametrize("update", ["count=count+2", "count+=2"])
def test_pending_run_and_write_count_stay_correlated_across_loop(tmp_path, width, update):
    from .test_rtl_source_ir import _lower, _native_agreement

    source = f"""module M(input clk,input [3:0] a,output logic [{width-1}:0] q);
      always @(posedge clk) begin
        int count,pending;
        count=0; pending=0; q=0;
        for(int i=0;i<4;i++) begin
          if(a[i]) begin
            pending++;
            if(i==3) begin q[count]=1; count++; pending=0; end
          end else begin
            if(pending>0) begin q[count]=1; count++; pending=0; end
            q[count+:2]=2; {update};
          end
        end
      end
    endmodule"""
    if width == 8:
        _native_agreement(tmp_path, source, initial={"count": 12, "pending": 13})
    else:
        with pytest.raises(Refused, match="packed-write-domain-unproved"):
            _lower(tmp_path, source)


def test_proof_deadline_is_not_a_success():
    access = ir.Assign("value", ir.Lit(7))
    proc = _procedure(access)
    with pytest.raises(Refused) as got:
        source_domains.prove_source_domains(proc, {"a": ranges.Range(0, 9)},
                                            _obligation(access, ir.Var("a"), 4), timeout_ms=0)
    assert got.value.refusal.detail["domain_proof"] == "unknown"


def test_search_deadline_cancels_proof_instead_of_starting_another_budget(monkeypatch):
    from behave.sim.search_policy import SearchBudget, SearchPolicy, SearchStopped, search_scope

    access = ir.Assign("value", ir.Lit(7))
    proc = _procedure(access)

    def unexpected(*args):
        pytest.fail("expired search must not enter exact proof")

    monkeypatch.setattr(source_domains, "_prove_exact", unexpected)
    budget = SearchBudget(SearchPolicy(time_budget_s=0))
    with search_scope(budget.direction(1)), pytest.raises(SearchStopped):
        source_domains.prove_source_domains(proc, {"a": ranges.Range(0, 9)},
                                            _obligation(access, ir.Var("a"), 4))


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("operation", ["write", "read"])
def test_source_query_after_correlated_access_is_confirmed_by_native_replay(tmp_path, operation):
    from behave.sim.engines import probing
    from behave.sim.engines.rtl.source_query import query_source_interval

    from .test_rtl_source_locations import _prepare_clocked_replay
    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower, _names, _state

    access = "q[x+y]=a[1];" if operation == "write" else "q=a[x+y];"
    condition = "a==9" if operation == "write" else "q && a==9"
    source = f"""module M(input clk,input [3:0] a,output logic [3:0] q);
      always @(posedge clk) begin
        int x,y;
        q=0;
        if(a[0]) begin x=3; y=0; end else begin x=0; y=3; end
        {access}
        if({condition}) q=15;
      end
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    goal = _goal(source, plan, "a==9", "true", "control-choice")
    _, before = replay((0,))
    assert goal not in before[0]
    assert all(not mark.startswith("@source-domain:") for mark, _, _ in machine.occurrences)
    initial = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": 17, "y": -8})
    environment = ranges.fixpoint(machine.procedure,
                                  {**machine.procedure.state_ranges, **machine.input_ranges})
    answer = query_source_interval(
        machine, environment, initial, {names["clk"]: 0, names["a"]: 0}, [goal],
        mutable_inputs=(names["a"],), following_inputs=({names["clk"]: 1, names["a"]: 0},),
        event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer
    assert answer.candidate[names["a"]] == 9
    _, after = replay((answer.candidate[names["a"]],))
    assert goal in after[0]


def test_sat_refusal_identifies_the_access_that_actually_violates_its_domain():
    first, second = ir.Assign("value", ir.Lit(1)), ir.Assign("value", ir.Lit(2))
    proc = _procedure(ir._sequence([
        ir.If(ir.Op("eq", ir.Var("a"), ir.Lit(0)),
              ir.Seq(ir.Assign("x", ir.Lit(0)), ir.Assign("y", ir.Lit(3))),
              ir.Seq(ir.Assign("x", ir.Lit(3)), ir.Assign("y", ir.Lit(0)))),
        ir.Assign("index", ir.Op("add", ir.Var("x"), ir.Var("y"))), first, second,
    ]))
    obligations = {
        id(first): (ir.Var("index"), 1, 4, Refusal("first", "first domain")),
        id(second): (ir.Var("index"), 1, 3, Refusal("second", "second domain")),
    }
    with pytest.raises(Refused) as got:
        source_domains.prove_source_domains(proc, {"a": ranges.Range(0, 1)}, obligations)
    assert got.value.refusal.kind == "second"
    assert got.value.refusal.detail["domain_proof"] == "sat"
