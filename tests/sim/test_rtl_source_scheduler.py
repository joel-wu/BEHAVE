"""Source-event state, shared IR encoding and independent native replay."""

import pytest

from behave.sim.symbolic import encoder, interp, ranges
from behave.sim.core.refusal import Refused
from behave.sim.engines import probing
from behave.sim.engines.rtl.source_query import query_source_interval
from behave.sim.rtl.shared_frontend import parse_observation_source
from behave.sim.rtl.source_plan import plan_source
from behave.sim.rtl.source_scheduler import lower_source_scheduler

from .test_rtl_observation_frontend import _installed
from .test_rtl_source_locations import _prepare_clocked_replay
from .test_rtl_source_query import _goal

pytestmark = [pytest.mark.eda("slang"), pytest.mark.solver]


def _lower(tmp_path, source, *, defer_domains=False, initialization=False):
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity)
    return lower_source_scheduler(source, ast, plan, sensitivity, filename="design.sv",
                                  defer_domains=defer_domains, initialization=initialization), plan


def _names(machine):
    names = {field.name: name for name, field in machine.program.fields.items()}
    assert len(names) == len(machine.program.fields)
    return names


def _state(machine, values, *, startup=False):
    names = _names(machine)
    return machine.boundary({names[name]: value for name, value in values.items()}, startup=startup)


def _step(machine, state, action, **pins):
    names = _names(machine)
    updated = {**state, **{names[name]: value for name, value in pins.items()}}
    inputs = {driven: updated[name] for name, driven in machine.drive_inputs}
    inputs["action"] = action
    post, outputs, marks, error = interp.run_step(machine.procedure, inputs, state)
    assert not error
    hits = {goal for mark, _, goal in machine.occurrences if mark in marks}
    return post, outputs.transactions, hits, inputs, marks


def _accepted(outputs):
    return len(outputs) == 1 and outputs[0]["accepted"] == 1


def _settle(machine, state):
    hits = set()
    # Fixture watchdog, not a scheduler budget or an encoding bound.
    for _ in range(30):
        active = next((i + 1 for i, flag in enumerate(machine.pending) if state[flag]), None)
        if active is None and any(state[write.valid] for write, _, _ in machine.queue_edges):
            active = machine.commit_action
        if active is None:
            return state, hits
        state, outputs, reached, _, _ = _step(machine, state, active)
        assert _accepted(outputs)
        hits.update(reached)
    pytest.fail("fixture did not settle")


def test_edge_flags_come_from_values_and_inputs_cannot_interrupt_work(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q,r);
      always @(posedge clk) q<=a;
      always @(posedge clk) r<=q;
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 3, "r": 0})
    state, outputs, hits, _, _ = _step(machine, state, 0, clk=1, a=5)
    assert _accepted(outputs) and not hits
    assert all(state[flag] for flag in machine.pending)
    interrupted, outputs, hits, _, _ = _step(machine, state, 0, clk=0, a=12)
    assert interrupted == state and outputs[0]["accepted"] == 0 and not hits
    # Either active order reads old q. A deferred commit is not yet legal.
    early, outputs, _, _, _ = _step(machine, state, machine.commit_action)
    assert early == state and outputs[0]["accepted"] == 0
    post, _ = _settle(machine, state)
    assert post[names["q"]] == 5 and post[names["r"]] == 3
    unchanged, _, _, _, _ = _step(machine, post, 0, clk=1, a=9)
    assert not any(unchanged[flag] for flag in machine.pending)


def test_combination_propagates_and_reactivates_after_a_later_write(tmp_path):
    source = """module M(input [3:0] a,output reg [3:0] q);
      logic [3:0] b;
      always_comb if(b==5) q=9; else q=0;
      always_comb b=a;
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"a": 0, "b": 0, "q": 0}, startup=True)
    state, _ = _settle(machine, state)
    state, _, _, _, _ = _step(machine, state, 0, a=5)
    assert sum(bool(state[flag]) for flag in machine.pending) == 1
    state, hits = _settle(machine, state)
    assert state[names["b"]] == 5 and state[names["q"]] == 9 and hits
    assert machine.event_bound == 4  # drive + producer + at most two consumer activations


def test_reconvergent_ready_path_can_change_events_without_changing_settled_state(tmp_path):
    source = '''module M(input clk,iv,ordy,output ir,ov,accepted);
      reg busy;
      wire consumed;
      assign consumed=ov & ordy;
      assign ir=~busy | consumed;
      assign ov=busy;
      assign accepted=iv & ir;
      always @(posedge clk) busy<=1'b1;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    target = _goal(source, plan, 'accepted=iv', event='entry', family='location')
    writers = {name: index + 1 for index, process in enumerate(machine.program.processes)
               for name, _immediate, _deferred in process.write_masks}
    state = _state(machine, {'clk': 0, 'iv': 1, 'ordy': 1, 'busy': 0,
                             'ov': 0, 'ir': 1, 'consumed': 0, 'accepted': 1})
    for action, pins in ((0, {'clk': 1}), (writers[names['busy']], {}),
                         (machine.commit_action, {})):
        state, outputs, _, _, _ = _step(machine, state, action, **pins)
        assert _accepted(outputs)
    outcomes = []
    for order in (('ir', 'accepted', 'ov', 'consumed', 'ir', 'accepted'),
                  ('ov', 'consumed', 'ir')):
        current, hits, ready_values = state, set(), []
        for name in order:
            current, outputs, reached, _, _ = _step(machine, current, writers[names[name]])
            assert _accepted(outputs)  # Every action must actually be enabled.
            hits.update(reached)
            ready_values.append(current[names['ir']])
        assert machine.boundary_is_quiet(current)
        outcomes.append((current, hits, ready_values))
    assert outcomes[0][0] == outcomes[1][0]
    assert outcomes[0][2] == [0, 0, 0, 0, 1, 1]
    assert outcomes[1][2] == [1, 1, 1]
    assert target in outcomes[0][1] and target not in outcomes[1][1]


@pytest.mark.parametrize("assignment", ["=", "<="])
def test_equal_settled_values_do_not_establish_equal_source_hits(tmp_path, assignment):
    source = f"""module M(input clk,input [1:0] d,output reg [1:0] q,r);
      always @(posedge clk) q {assignment} d;
      always @(posedge clk) begin
        if(q==1) r=1; else r=2;
        r=0;
      end
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    initial = _state(machine, {"clk": 0, "d": 0, "q": 0, "r": 0})
    driven, outputs, _, _, _ = _step(machine, initial, 0, clk=1, d=1)
    assert _accepted(outputs) and all(driven[flag] for flag in machine.pending)
    outcomes = []
    for order in ((1, 2), (2, 1)):
        state, hits = driven, set()
        for action in order:
            state, outputs, reached, _, _ = _step(machine, state, action)
            assert _accepted(outputs)
            hits.update(reached)
        state, reached = _settle(machine, state)
        hits.update(reached)
        assert machine.boundary_is_quiet(state)
        assert (state[names["q"]], state[names["r"]]) == (1, 0)
        outcomes.append((state, hits))
    assert outcomes[0][0] == outcomes[1][0]
    # Source-local SAT still needs native confirmation. The scheduler permits
    # both orders; equal quiescent states alone cannot grant exact native-hit
    # semantics to a whole-domain analyzer. NBA preserves the old-value read.
    assert (outcomes[0][1] == outcomes[1][1]) is (assignment == "<=")


@pytest.mark.eda("verilator", "make")
def test_simultaneous_data_and_clock_proposal_needs_native_confirmation(tmp_path):
    from dataclasses import replace

    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_locations import _collector

    source = '''module M(input clk,input [3:0] a,output reg [3:0] q);
      wire push=a[0];
      always @(posedge clk) if(push) q<=1; else q<=0;
    endmodule'''
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "push": 0, "q": 0})
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    target = _goal(source, plan, "if(push)", event="false")
    pins = {names["clk"]: 1, names["a"]: 1}
    # A permitted scheduler order reads the old continuous-assignment value.
    # Keeping input values fixed makes the scheduling distinction explicit.
    answer = query_source_interval(machine, env, state, pins, (target,),
        mutable_inputs=(), event_bound=machine.event_bound, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.goal == target and answer.candidate == pins
    # A separate, quiescent data phase removes this opportunity. The physical
    # phases are part of the query, not a replaceable occurrence count.
    settled = query_source_interval(machine, env, state, {**pins, names["clk"]: 0},
        (target,), mutable_inputs=(), following_inputs=(pins,), event_bound=machine.event_bound,
        timeout_ms=30_000)
    assert settled.outcome is probing.Outcome.LOCAL_UNSAT, settled.detail

    collector, _ = _collector(plan)
    assert collector.bindings.event_count <= 64
    driver = '''
      if(behave_native_events_begin_v1(0)) return 1;
      dut.clk=0; dut.a=0; dut.eval();
      uint64_t startup_bits=0,startup_calls=0;
      if(behave_native_events_end_v1(0,&startup_bits,1,&startup_calls)) return 2;
      if(behave_native_events_begin_v1(1)) return 1;
      dut.a=1; dut.clk=1; dut.eval();
      uint64_t bits=0,calls=0;
      if(behave_native_events_end_v1(1,&bits,1,&calls)) return 2;
      std::cout<<unsigned(dut.q)<<" "<<bits<<" "<<calls<<"\\n";
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain, = _trace(tmp_path / "plain", source, flat, driver)
    native, = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, driver, native=True)
    assert plain[0] == native[0] == 1
    hits = collector.bindings.decode({event for event in range(collector.bindings.event_count)
                                     if native[1] >> event & 1}).goals
    assert target not in hits
    assert _goal(source, plan, "if(push)", event="true") in hits


def test_structural_bound_counts_nba_wakeups_but_does_not_claim_cyclic_termination(tmp_path):
    machine, _ = _lower(tmp_path, '''module M(input clk,input [3:0] a,output reg [3:0] y);
      reg [3:0] q;
      always @(posedge clk) q<=a;
      always @(q) y=q;
    endmodule''')
    assert machine.event_bound == 5  # drive, producer, commit, up to two consumer runs
    cyclic, _ = _lower(tmp_path, '''module M(input clk,output reg q);
      always @(posedge clk or posedge q) q<=q;
    endmodule''')
    assert cyclic.event_bound is None


@pytest.mark.parametrize("assignment", ["=", "<="])
def test_restored_value_still_wakes_consumer(tmp_path, assignment):
    source = f"""module M(input clk,input [3:0] a,output reg [3:0] q);
      logic [3:0] x;
      always @(posedge clk) begin x {assignment} a; x {assignment} 0; end
      always @(x) q=q+1;
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "x": 0, "q": 0})
    state, _, _, _, _ = _step(machine, state, 0, clk=1, a=5)
    state, hits = _settle(machine, state)
    assert state[names["x"]] == 0 and state[names["q"]] == 1 and hits


@pytest.mark.parametrize(("initial", "values", "expected"), [
    (0, "x<=1; x<=0;", (1, 1)),
    (1, "x<=0; x<=0;", (0, 1)),
    (0, "x<=1; x<=1;", (1, 0)),
    (1, "x<=1; x<=1;", (0, 0)),
])
def test_nba_direction_history_is_not_just_the_final_difference(tmp_path, initial, values, expected):
    source = f"""module M(input clk,output reg [3:0] up,down);
      logic x;
      always @(posedge clk) begin {values} end
      always @(posedge x) up=up+1;
      always @(negedge x) down=down+1;
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "x": initial, "up": 0, "down": 0})
    state, _, _, _, _ = _step(machine, state, 0, clk=1)
    state, _ = _settle(machine, state)
    assert (state[names["up"]], state[names["down"]]) == expected


@pytest.mark.parametrize("initial,body,expected", [
    (0, "x[0]<=1; x[0]<=0;", (0, 1, 1)),
    (0, "x[1]<=1; x[1]<=0;", (0, 0, 0)),
    (3, "x[1]<=0;", (1, 0, 0)),
    (0, "x[0]<=1; x=2;", (3, 1, 0)),
    (0, "x[1]<=1; x=1;", (3, 1, 0)),
])
def test_partial_nba_keeps_bitwise_edges_and_clears_pending_mask(tmp_path, initial, body, expected):
    source = f"""module M(input clk,output reg [3:0] up,down);
      logic [1:0] x;
      always @(posedge clk) begin {body} end
      always @(posedge x) up=up+1;
      always @(negedge x) down=down+1;
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "x": initial, "up": 0, "down": 0})
    state, _, _, _, _ = _step(machine, state, 0, clk=1)
    state, _ = _settle(machine, state)
    assert tuple(state[names[name]] for name in ("x", "up", "down")) == expected
    assert all(state[write.mask] == 0 for write, _, _ in machine.queue_edges)


def test_source_read_slice_not_unrelated_bits_controls_implicit_activation(tmp_path):
    source = "module M(input [3:0] a,output reg q); always_comb q=a[0]; endmodule"
    machine, _ = _lower(tmp_path, source)
    state = _state(machine, {"a": 0, "q": 0}, startup=True)
    state, _ = _settle(machine, state)
    state, _, _, _, _ = _step(machine, state, 0, a=2)
    assert not any(state[flag] for flag in machine.pending)
    state, _, _, _, _ = _step(machine, state, 0, a=3)
    assert all(state[flag] for flag in machine.pending)


@pytest.mark.parametrize("declaration", [
    "output wire [3:0] q); assign q=a;",
    "output wire [3:0] q); wire [3:0] n=a; assign q=n;",
    "output reg [3:0] q); always @* q=a;",
    "output reg [3:0] q); always @(a) q=a;",
])
def test_source_driver_forms_share_the_event_relation(tmp_path, declaration):
    machine, _ = _lower(tmp_path, "module M(input [3:0] a," + declaration + " endmodule")
    names = _names(machine)
    state = _state(machine, {name: 0 for name in names}, startup=True)
    state, _ = _settle(machine, state)
    state, _, _, _, _ = _step(machine, state, 0, a=5)
    state, hits = _settle(machine, state)
    assert state[names["q"]] == 5 and hits


@pytest.mark.eda("verilator", "make")
@pytest.mark.parametrize("control", ["@(a)", "@(a or b)", "@(posedge clk)",
                                     "@(negedge clk)", "@(a or posedge clk)"])
def test_explicit_event_startup_matches_original_and_observed_execution(tmp_path, control):
    from dataclasses import replace

    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_locations import _collector

    source = ("module M(input clk, input [3:0] a,b, output reg [3:0] q); "
              f"always {control} q=(a^b)+1; endmodule")
    machine, plan = _lower(tmp_path, source)
    collector, _ = _collector(plan)
    words = collector.word_count
    driver = f'''
      const unsigned pins[][3]={{{{0,0,0}},{{0,0,0}},{{0,2,0}},{{0,2,3}},{{1,2,3}},{{0,2,3}}}};
      for(unsigned i=0;i<6;++i) {{
        if(behave_native_events_begin_v1(i)) return 1;
        dut.clk=pins[i][0]; dut.a=pins[i][1]; dut.b=pins[i][2]; dut.eval();
        uint64_t bits[{words}]={{}},calls=0;
        if(behave_native_events_end_v1(i,bits,{words},&calls)) return 2;
        std::cout<<unsigned(dut.q);
        for(auto word:bits) std::cout<<" "<<word;
        std::cout<<" "<<calls<<"\\n";
      }}
    '''
    flat = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, flat, driver, event_words=words)
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector,
                    driver, native=True, event_words=words)
    state = _state(machine, {"clk": 0, "a": 0, "b": 0, "q": 0}, startup=True)
    outputs, observations = [], []
    for index, (clk, a, b) in enumerate(((0,0,0), (0,0,0), (0,2,0),
                                       (0,2,3), (1,2,3), (0,2,3))):
        if index:
            state, _, _, _, _ = _step(machine, state, 0, clk=clk, a=a, b=b)
        state, hits = _settle(machine, state)
        outputs.append(state[_names(machine)["q"]])
        observations.append(hits)
    native_hits = [collector.bindings.decode({event for event in range(collector.bindings.event_count)
                                              if row[1] >> event & 1}).goals for row in native]
    assert outputs == [row[0] for row in plain] == [row[0] for row in native]
    assert observations == native_hits
    assert bool(observations[0]) == (control in {"@(a)", "@(a or b)", "@(a or posedge clk)"})


def test_scalar_scheduler_encodes_the_same_transition_state_and_events(tmp_path):
    import z3

    source = """module M(input clk,input [2:0] a,output reg [2:0] q);
      logic [2:0] x;
      always @(posedge clk) begin x<=a; x<=0; end
      always @(x) if(a==5) q=7; else q=0;
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    enc = encoder.encode_step(proc, env=env)
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": 0})
    for action in (0, 1, machine.commit_action, 2):
        post, outputs, _, inputs, marks = _step(machine, state, action, clk=1, a=5)
        assert _accepted(outputs)
        solver = z3.Solver()
        solver.add(enc.constraint, *(enc.pre[name] == value for name, value in {**state, **inputs}.items()))
        assert solver.check() == z3.sat
        model = solver.model()
        for name, value in post.items():
            term = model.eval(enc.state_post[name], model_completion=True)
            assert (z3.is_true(term) if z3.is_bool(term) else term.as_long()) == value
        assert {mark for mark, term in enc.hits.items() if z3.is_true(model.eval(term))} == marks
        state = post


@pytest.mark.parametrize("width", [3, 16])
def test_signed_nba_history_keeps_finite_carriers_and_exact_transition_values(tmp_path, width):
    import z3

    from behave.sim.symbolic.step_template import StepTemplate

    source = f"""module M(input clk,input signed [{width-1}:0] a,
      output logic signed [{width-1}:0] q);
      always @(posedge clk) begin q<=a; q<=-a; end
    endmodule"""
    machine, _ = _lower(tmp_path, source)
    proc = machine.procedure
    env = ranges.fixpoint(proc, {**proc.state_ranges, **machine.input_ranges})
    assert set(proc.state) <= env.keys()
    assert ranges.unproved(proc, env) == []
    for write, rises, falls in machine.queue_edges:
        for name in (write.varied, write.mask, rises, falls):
            assert env[name] == ranges.Range(0, (1 << width) - 1)
    template = StepTemplate(proc, env)
    enc = template.encode()
    state = _state(machine, {"clk": 0, "a": 0, "q": -1})
    for value in (-(1 << (width-1)), -1, 0, (1 << (width-1))-1):
        for action, clock in ((0, 0), (0, 1), (1, 1), (machine.commit_action, 1)):
            post, outputs, _, inputs, marks = _step(machine, state, action, clk=clock, a=value)
            assert _accepted(outputs)
            solver = z3.Solver()
            solver.add(enc.constraint, *(enc.pre[name] == entry
                for name, entry in {**state, **inputs}.items()))
            assert solver.check() == z3.sat
            model = solver.model()
            for name, expected in post.items():
                # Compare using the value's actual carrier, including negative
                # integers, not a zero-extending host conversion.
                assert z3.is_true(model.eval(enc.state_post[name] == expected))
            assert {mark for mark, term in enc.hits.items()
                    if z3.is_true(model.eval(term))} == marks
            state = post


def test_interval_derives_one_carrier_plan_per_query_not_per_event(tmp_path, monkeypatch):
    from behave.sim.symbolic import ir, step_template
    from behave.sim.symbolic import validation

    source = '''module M(input [3:0] a,output reg [3:0] q);
      integer i;
      always_comb begin
        q=0;
        for(i=0;i<2;i=i+1) if(a==9) q=q+1;
      end
    endmodule'''
    machine, declarations = _lower(tmp_path, source)
    assert any(isinstance(command, ir.For) for command in ir.walk(machine.procedure.body))
    env = ranges.fixpoint(machine.procedure,
                         {**machine.procedure.state_ranges, **machine.input_ranges})
    names = _names(machine)
    state = _state(machine, {'a': 0, 'q': 0, 'i': 2})
    goal = _goal(source, declarations, 'a==9')
    validated_procedures, planned_procedures = [], []
    validate, derive = step_template._procedure_validation, step_template.plan_encoding

    def validated(*args, **kwargs):
        validated_procedures.append(args[0])
        return validate(*args, **kwargs)

    def derived(*args, **kwargs):
        planned_procedures.append(args[0])
        return derive(*args, **kwargs)

    monkeypatch.setattr(step_template, '_procedure_validation', validated)
    monkeypatch.setattr(validation, '_procedure_validation', validated)
    monkeypatch.setattr(step_template, 'plan_encoding', derived)
    for repeat in range(2):
        answer = query_source_interval(machine, env, state, {names['a']: 0}, (goal,),
            mutable_inputs=(names['a'],), following_inputs=({names['a']: 0},), event_bound=4,
            timeout_ms=30_000)
        assert answer.outcome is probing.Outcome.SAT, answer.detail
        assert answer.candidate[names['a']] == 9
        # The full source is checked before pruning, then the separately owned
        # projection is checked and planned once. Neither repeats per event.
        assert len(validated_procedures) == 2 * (repeat + 1)
        original, projected = validated_procedures[-2:]
        assert original is machine.procedure and projected is not original
        assert len(planned_procedures) == repeat + 1
        assert planned_procedures[-1] is projected


@pytest.mark.parametrize(("clock", "bound", "outcome"), [
    (1, 4, probing.Outcome.SAT),
    (1, 7, probing.Outcome.SAT),
    (1, 3, probing.Outcome.LOCAL_UNSAT),
    (0, 7, probing.Outcome.LOCAL_UNSAT),
])
def test_interval_query_derives_clock_and_nba_then_comb_activation(tmp_path, clock, bound, outcome):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      logic [3:0] x;
      always @(posedge clk) x<=a;
      always_comb if(x==9) q=13; else q=x;
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": 0})
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    target = _goal(source, plan, "x==9")
    answer = query_source_interval(machine, env, state, {names["clk"]: clock, names["a"]: 0},
        (target,), mutable_inputs=(names["a"],), event_bound=bound, timeout_ms=30_000)
    assert answer.outcome is outcome, answer.detail
    assert answer.scope is probing.Scope.RTL_LOCAL_HIT
    if outcome is probing.Outcome.SAT:
        assert answer.goal == target and answer.candidate == {names["clk"]: 1, names["a"]: 9}
        state, _, _, _, _ = _step(machine, state, 0, clk=1, a=answer.candidate[names["a"]])
        state, hits = _settle(machine, state)
        assert target in hits and state[names["q"]] == 13


@pytest.mark.parametrize("target", ["a==q", "a!=q"])
def test_query_keeps_one_payload_across_control_and_both_clock_edges(tmp_path, target):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q,y);
      always @(posedge clk) q<=a;
      always @(negedge clk) if(a==q) y=1; else y=2;
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 3, "y": 0})
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    goal = _goal(source, plan, "a==q", event="true" if target == "a==q" else "false")
    low = {names["clk"]: 0, names["a"]: 0}
    answer = query_source_interval(machine, env, state, low, (goal,), mutable_inputs=(names["a"],),
        following_inputs=({**low, names["clk"]: 1}, low), event_bound=3, timeout_ms=30_000)
    assert answer.outcome is (probing.Outcome.SAT if target == "a==q" else probing.Outcome.LOCAL_UNSAT)
    if answer.candidate is not None:
        for clock in (0, 1, 0):
            state, _, _, _, _ = _step(machine, state, 0, clk=clock, a=answer.candidate[names["a"]])
            state, hits = _settle(machine, state)
        assert goal in hits


@pytest.mark.parametrize("processes", [1, 2, 3, 4, 6])
def test_scheduler_selector_representation_and_unused_codes(tmp_path, processes):
    outputs = ",".join(f"y{i}" for i in range(processes))
    bodies = "\n".join(f"always_comb if(a==9) y{i}=a; else y{i}=0;" for i in range(processes))
    source = f"module M(input [3:0] a,output reg [3:0] {outputs}); {bodies} endmodule"
    machine, plan = _lower(tmp_path, source)
    state = _state(machine, {"a": 0, **{f"y{i}": 0 for i in range(processes)}})
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    maximum = (1 << dict(machine.procedure.signature.inputs)["action"].width) - 1
    for unused in range(machine.commit_action + 1, maximum + 1):
        post, outputs, hits, _, _ = _step(machine, state, unused)
        assert post == state and not _accepted(outputs) and not hits
    name = _names(machine)["a"]
    answer = query_source_interval(machine, env, state, {name: 0}, (_goal(source, plan, "a==9"),),
        mutable_inputs=(name,), event_bound=processes + 1, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    assert answer.candidate == {name: 9}


@pytest.mark.eda("verilator", "make")
def test_derived_source_events_match_original_and_instrumented_rtl(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [3:0] q);
      logic [3:0] x;
      initial begin q=0; x=0; end
      always @(posedge clk) x<=a;
      always_comb if(x==9) q=13; else q=x;
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    state = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": 0}, startup=True)
    state, _ = _settle(machine, state)
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    values = (0, 5, 9, 9, 1, 0)
    observed, native_hits = replay(values)
    expected, hits = [], []
    for value in values:
        state, _, _, _, _ = _step(machine, state, 0, clk=0, a=value)
        state, reached = _settle(machine, state)
        state, _, _, _, _ = _step(machine, state, 0, clk=1)
        state, at_edge = _settle(machine, state)
        expected.append(state[names["q"]])
        hits.append(reached | at_edge)
    assert expected == observed
    assert hits == native_hits
    # No hand-entered activation flags: changing the proposed data must cause
    # the clocked write, its deferred commit, then the combo goal occurrence.
    boundary = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": 0})
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    target = _goal(source, plan, "x==9")
    answer = query_source_interval(machine, env, boundary, {names["clk"]: 1, names["a"]: 0},
        (target,), mutable_inputs=(names["a"],), event_bound=4, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    _, confirmed = replay((answer.candidate[names["a"]],))
    assert target in confirmed[0]


@pytest.mark.eda("verilator", "make")
def test_signed_nba_search_witness_replays_with_the_original_event_hits(tmp_path):
    source = """module M(input clk,input [3:0] a,output reg [7:0] q);
      logic signed [3:0] x;
      initial begin q=0; x=0; end
      always @(posedge clk) begin x<=a; x<=-a; end
      always_comb if(x==-3) q=123; else q=0;
    endmodule"""
    machine, plan = _lower(tmp_path, source)
    names = _names(machine)
    boundary = _state(machine, {"clk": 0, "a": 0, "q": 0, "x": 0})
    env = ranges.fixpoint(machine.procedure, {**machine.procedure.state_ranges, **machine.input_ranges})
    target = _goal(source, plan, "x==-3")
    _, _, replay = _prepare_clocked_replay(tmp_path, source, plan)
    _, baseline = replay((0, 1, 2))
    assert all(target not in reached for reached in baseline)
    answer = query_source_interval(machine, env, boundary, {names["clk"]: 1, names["a"]: 0},
        (target,), mutable_inputs=(names["a"],), event_bound=4, timeout_ms=30_000)
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    value = answer.candidate[names["a"]]
    state, _, _, _, _ = _step(machine, boundary, 0, clk=1, a=value)
    state, hits = _settle(machine, state)
    observed, confirmed = replay((value,))
    assert value == 3 and observed == [state[names["q"]]] == [123]
    assert target in hits and target in confirmed[0]


def test_scheduler_does_not_alias_variable_instance_ports(tmp_path):
    source = """module C(input a,output logic q); always_comb q=a; endmodule
      module M(input a,output q); C c(a,q); endmodule"""
    machine, _ = _lower(tmp_path, source)
    fields = {cell.observation: name for name, cell in machine.program.fields.items()}
    assert fields["q"] != fields["c", "q"]
    assert sum(process.context is None for process in machine.program.processes) == 1


def test_conditional_event_is_not_misread_as_unconditional_edge(tmp_path):
    source = """module M(input clk,en,input [3:0] a,output reg [3:0] q);
      always @(posedge clk iff en) q<=a;
    endmodule"""
    with pytest.raises(Refused, match="trigger-unsupported"):
        _lower(tmp_path, source)
