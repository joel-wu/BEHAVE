"""Source failure events, not guesses from a simulator's exit status."""

from dataclasses import replace

import pytest

from behave.sim.rtl.native_events import prepare_declared_native_events
from behave.sim.rtl.source_bindings import bind_declared_source_events
from behave.sim.rtl.source_goals import manifest_of
from behave.sim.rtl.source_plan import plan_source

from .test_rtl_source_declarations import _documents


@pytest.mark.parametrize("native", [False, True])
def test_assertion_execution_does_not_depend_on_observation(tmp_path, native):
    from behave.sim.harness import generate_makefile

    generate_makefile(tmp_path, "M", native_events=native)
    assert (tmp_path / "Makefile").read_text().split().count("--assert") == 1


@pytest.mark.eda("slang")
@pytest.mark.parametrize("statement", [
    '$fatal(1, "bad: %d", a);', '$error("bad");', 'assert(a);',
    'assert(a) q=1; else q=0;', 'assert(a) else;',
])
def test_failure_has_a_source_bound_producer(tmp_path, statement):
    source = f"module M(input clk,a, output reg q); always @(posedge clk) {statement} endmodule"
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert len(plan.declarations.sites_for("artifact-failure")) == 1
    failures = [entry for entry in plan.declared_events
                if plan.declarations.sites[entry.site].schema == "artifact-failure"]
    assert [entry.outcome for entry in failures] == ["failure"]
    native = prepare_declared_native_events(
        plan, filename="design.sv", toolchain_sha256="1" * 64,
        execution_sha256="2" * 64, callback_limit=100)
    native.check_catalogue(manifest_of(plan.declarations, filename="design.sv"))
    from behave.sim.core.goal_provider import CONCRETE_HIT, EXACT_BOUNDED_HIT, LOCAL_GUIDANCE
    from behave.sim.rtl.goal_provider import goal_providers

    for provider in goal_providers(manifest_of(plan.declarations, filename="design.sv")):
        assert provider.has(CONCRETE_HIT)
        assert provider.has(LOCAL_GUIDANCE)
        # Exact queries use the source-event relation; the legacy wire-only
        # SourceEncoding remains insufficient (checked separately below).
        assert provider.has(EXACT_BOUNDED_HIT)


@pytest.mark.eda("slang")
@pytest.mark.parametrize("argument", ['a++', 'f(a)', '(a ? f(a) : 0)'])
def test_effectful_failure_arguments_have_a_post_evaluation_producer(tmp_path, argument):
    source = f'''module M(input clk, input [3:0] a);
      function automatic int f(input int v); $fatal(1,"inner"); f=v; endfunction
      always @(posedge clk) begin int b; b=a; $fatal(1,"outer %d", {argument.replace('a++', 'b++')}); end
    endmodule'''
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert plan.declarations.sites_for("artifact-failure")
    assert any(failure.post_arguments for failure in plan.declarations.failures)
    observed = plan.native().text
    assert '$swrite(' in observed
    assert '$fatal(1,"inner")' in observed


@pytest.mark.eda("slang")
@pytest.mark.parametrize('arguments', ['b++, b', 'b++, b++', 'f(a), a'])
def test_interfering_or_terminating_argument_order_is_not_invented(tmp_path, arguments):
    source = f'''module M(input clk, input [3:0] a);
      function automatic int f(input int v); $fatal(1,"inner"); f=v; endfunction
      always @(posedge clk) begin int b; b=a; $fatal(1,"%d %d",{arguments}); end
    endmodule'''
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert plan.declarations.sites_for("artifact-failure")
    with pytest.raises(ValueError, match="evaluation order"):
        plan.native()


@pytest.mark.eda("slang")
@pytest.mark.parametrize("delay", ["#0", "final"])
def test_deferred_assertions_do_not_acquire_immediate_sample_semantics(tmp_path, delay):
    source = f"module M(input clk,a); always @(posedge clk) assert {delay}(a); endmodule"
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert len(plan.declarations.sites_for("artifact-failure")) == 1
    with pytest.raises(ValueError, match="deferred assertions"):
        plan.native()


@pytest.mark.eda("slang")
@pytest.mark.parametrize("delay", ["#0", "final"])
@pytest.mark.parametrize("placement", ["module", "generate"])
def test_static_deferred_assertions_remain_in_the_native_catalogue(tmp_path, delay, placement):
    statement = f"check_a: assert {delay}(a);"
    if placement == "generate":
        statement = "if(1) begin: checked " + statement + " end"
    source = f"module M(input a); {statement} endmodule"
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    assert len(plan.declarations.sites_for("artifact-failure")) == 1
    with pytest.raises(ValueError, match="deferred assertions"):
        plan.native()


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("body", [
    "q=a; assert(q++) else q+=10; q+=3;",
    "q=a; if(a[3]) assert(q++) else q+=10; q+=3;",
    "q=a; assert(a[0] && a[1]) q+=2; else q+=5; q+=3;",
    "q=a; assert(a) else begin end q+=3;",
    "q=a; assert(q++) else begin end q+=3;",
    "q=a; assert(a) else; q+=3;",
    "q=a; assert(q++) else; q+=3;",
])
def test_recovering_assertion_evaluates_once_and_keeps_original_actions(tmp_path, body):
    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_expressions import _driver

    source = f"module M(input clk,rst,input [3:0] a,output reg [7:0] q); always @(posedge clk) begin {body} end endmodule"
    cst, ast = _documents(tmp_path / "parse", source)
    plan = plan_source(source, cst, semantic_document=ast)
    collector = prepare_declared_native_events(plan, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=100)
    # An uninstrumented design has no DPI scope and never calls the collector.
    transport = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    plain = _trace(tmp_path / "plain", source, transport, _driver())
    native = _trace(tmp_path / "native", plan.native(scoped=True).text, collector, _driver(), native=True)
    catalogue = manifest_of(plan.declarations, filename="design.sv")
    failure, = [g for g in catalogue if catalogue[g].family == "artifact-failure"]
    for a, ((plain_q, _, _), (q, bits, _)) in enumerate(zip(plain, native, strict=True)):
        if "&&" in body:
            fails = a & 3 != 3
            expected = a + (5 if fails else 2) + 3
        elif "else begin end" in body or "else;" in body:
            fails = a == 0
            expected = a + ("q++" in body) + 3
        else:
            active = bool(a & 8) if "if(a[3])" in body else True
            fails = active and a == 0
            expected = a + active + 10 * fails + 3
        assert q == plain_q == expected, (a, native)
        hits = collector.bindings.decode({i for i in range(collector.bindings.event_count) if bits >> i & 1})
        assert (failure in hits.goals) == fails


@pytest.mark.eda("slang", "verilator", "make")
@pytest.mark.parametrize("action", ["", "q+=2;", "begin end"])
def test_absent_assertion_else_uses_default_error_even_with_pass_action(tmp_path, action):
    """IEEE 1800-2017 16.3: only an explicit else replaces default $error."""
    import subprocess

    from .test_rtl_combinational_events import _trace
    from .test_rtl_source_expressions import _driver

    source = ("module M(input clk,rst,input [3:0] a,output reg [7:0] q); "
              f"always @(posedge clk) begin q=a; assert(a) {action or ';'} q+=3; end endmodule")
    cst, ast = _documents(tmp_path / "parse", source)
    plan = plan_source(source, cst, semantic_document=ast)
    collector = prepare_declared_native_events(plan, filename="design.sv",
        toolchain_sha256="1"*64, execution_sha256="2"*64, callback_limit=100)
    transport = replace(collector, bindings=replace(collector.bindings, coordinates=()))
    for name, text, observer in (("plain", source, transport),
                                ("native", plan.native(scoped=True).text, collector)):
        with pytest.raises(subprocess.CalledProcessError) as stopped:
            _trace(tmp_path / name, text, observer, _driver(), native=name == "native")
        assert "'assert' failed" in stopped.value.stdout


@pytest.mark.eda("slang", "verilator", "make", "iverilog", "vvp")
@pytest.mark.parametrize("actions", ["else;", "begin end else;", "q+=7; else;", "else begin end"])
def test_explicit_empty_assertion_action_agrees_with_independent_simulator(tmp_path, actions):
    from .test_rtl_source_activation_scheduling import _assert_entries

    source = ("module M(input [7:0] a,output reg [31:0] q=0); "
              f"always @(posedge a[0]) begin q=a>>1; assert(q++) {actions} q+=3; end endmodule")
    _assert_entries(tmp_path, source, (0, 1, 0, 3, 0, 5), ("q+=3;",))


@pytest.mark.eda("slang", "yosys")
def test_source_and_compiler_native_bindings_agree_on_retained_occurrences(tmp_path):
    from behave.sim.rtl.source_projection import project_source

    from .test_rtl_source_encoding import _prepared

    source = '''module Leaf(input clk,input [3:0] a,output reg [7:0] q);
      always @(posedge clk) if(a[0]) for(int i=0;i<2;i++) q<=a[1]?a:0;
    endmodule
    module M(input clk,rst,input [3:0] a,output [15:0] q);
      Leaf left(clk,a,q[7:0]); Leaf right(clk,~a,q[15:8]);
    endmodule'''
    plan, _, _, execution = _prepared(tmp_path, source, scoped=True)
    bindings = bind_declared_source_events(plan, filename="design.sv")
    assert bindings.coordinates == execution.native.coordinates
    assert bindings.projections == project_source(plan, execution.native, filename="design.sv").outcomes
    with pytest.raises(ValueError, match="no native producer"):
        bind_declared_source_events(replace(plan, declared_events=()), filename="design.sv")


@pytest.mark.eda("slang")
@pytest.mark.solver
def test_native_failure_support_does_not_claim_a_terminal_symbolic_relation(tmp_path):
    from behave.sim.core.refusal import Refusal
    from behave.sim.rtl.control_condition import ControlCondition
    from behave.sim.rtl.source_encoding import SourceEncoding

    source = 'module M(input clk,a); always @(posedge clk) assert(a); endmodule'
    cst, ast = _documents(tmp_path, source)
    plan = plan_source(source, cst, semantic_document=ast)
    encoding = SourceEncoding(plan, (ControlCondition(()),) * len(plan.events),
                              tuple(range(len(plan.events))))
    # The ordinary register relation would continue past a default assertion
    # failure. Refuse that relation, including its apparent later coverage.
    for event in range(len(plan.events)):
        result = encoding.predicate_for(event, object())
        assert type(result) is Refusal
        assert result.kind == "rtl-source-failure-transition-unencoded"


@pytest.mark.cosim
@pytest.mark.parametrize("statement,terminal,helpers", [
    ('$fatal(1,"intentional");', True, ""), ('$error("intentional");', True, ""),
    ('assert(0);', True, ""), ('assert(0) else;', False, ""),
    ('assert(0) q+=1;', True, ""),
    ('assert(0) else q+=1;', False, ""),
    ('check(q);', True, 'task automatic check(input [7:0] v); $fatal(1,"helper %d",v); endtask'),
    ('unique case(q) 255:; endcase', True, ''),
    ('priority case(q) 255:; endcase', True, ''),
    ("unique case(1'b0) 0:; 0:; default:; endcase", True, ''),
])
@pytest.mark.parametrize("phase", ["control", "clock"])
@pytest.mark.parametrize("atomic", [False, True])
def test_real_source_failure_keeps_prefix_and_attributes_only_observed_violation(
        tmp_path, monkeypatch, statement, terminal, helpers, phase, atomic, selected_startup=False):
    from behave.sim import api
    from behave.sim.core.clock_schedule import ClockTiming, GlobalEventSlot, GlobalSchedule
    from behave.sim.core.record_value import thaw_record_value
    from behave.sim.core.stimulus import AuditedTrace
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.native_observation import read_native_prefix
    from behave.sim.rtl.native_preparation import PreparedNativeEvents, prepare_source_observer
    from behave.sim.rtl.observation_binding import ObservationBinding, PreparedObservation

    guard = "rst" if phase == "control" else "!rst" if phase == "startup" else "q==2"
    edge = "posedge rst" if phase == "control" else "negedge clk" if phase == "advance" else "posedge clk"
    process = "always_comb" if phase == "startup" else f"always @({edge})"
    source = f'''module M(input clk,rst,out_ready{',bclk,brst' if atomic else ''},
                         output out_valid,output reg [7:0] y);
      reg [7:0] q=0;
      {helpers}
      assign out_valid=0;
      {process} begin
        if ({guard}) {statement}
        if (q==2) y=99; else y=7;
        q=q+1;
      end
    endmodule'''
    cst, ast = _documents(tmp_path / "parse", source)
    plan = plan_source(source, cst, semantic_document=ast)
    catalogue = manifest_of(plan.declarations, filename="design.sv")
    # Public preparation gets the original source, not test-written callbacks.
    # Supplying the source catalogue isolates failure attribution from the
    # independent catalogue-discovery test.
    prepared = prepare_source_observer(source, top="M", execution_sha256="2" * 64,
                                      callback_limit=100, catalogue=catalogue, scope_root="")
    assert type(prepared) is PreparedNativeEvents, prepared.as_dict()
    clocks = [api.ClockDomain("clk", "clk", "rst")]
    if atomic:
        clocks.append(api.ClockDomain("b", "bclk", "brst"))
    contract = api.TaskContract(
        clocks=clocks,
        endpoints=[api.Endpoint("out", api.Direction.OUT,
            [api.Field("y", api.FiniteType(8), "y")],
            api.ready_valid("out", "out_valid", "out_ready"), clock="clk", kind=api.Kind.TRANSACTION)])
    slots = [api.EventSlot(
        reset=(phase == "control" and i == 2),
        controls={"rst": int(phase == "control" and i == 2), "out_ready": 1}) for i in range(4)]
    schedule = None
    if atomic:
        slots = [api.EventSlot(ticks=("clk", "b"), resets=("clk", "b") if slot.reset else (),
                               controls={**dict(slot.controls), "brst": int(slot.reset)}) for slot in slots]
        schedule = GlobalSchedule(
            tuple(ClockTiming(c.name, 10, clock=c.clock, reset=c.reset) for c in clocks),
            tuple(GlobalEventSlot(i * 10, event=slot) for i, slot in enumerate(slots)))
    stimulus = api.Stimulus(slots=slots, clock_schedule=schedule)
    path = tmp_path / "M.sv"
    path.write_text(source)
    artifact = RTLArtifact(path, "M", tmp_path / "run")
    artifact._frontend_catalogue = catalogue
    artifact._observation = PreparedObservation(ObservationBinding(
        artifact.identity, contract.digest, catalogue.digest(), prepared.plan.identity,
        prepared.request_sha256), prepared)
    monkeypatch.setattr(artifact, "frontend_catalogue", lambda **_: catalogue)
    startup = None
    if selected_startup:
        from behave.sim.core.refusal import Refused
        from behave.sim.rtl.initial_state import RTLInitialState
        from behave.sim.rtl.startup_state import RTLStartupState, startup_pin_widths

        startup = RTLStartupState(RTLInitialState(artifact.identity, [('q', 8, 7)]),
                                  [(name, width, 0) for name, width in sorted(startup_pin_widths(contract).items())])
        with pytest.raises(Refused) as caught:
            artifact.exact_trace(contract, stimulus, observe_names=('q',), state_names=('q',),
                                 state_widths={'q': 8}, startup_state=startup)
        result = api.RunFailure.from_dict(thaw_record_value(caught.value.refusal.detail['failure']))
    else:
        result, = artifact.traces_per_process(contract, [stimulus], workers=1)
    failure, = [g for g in catalogue if catalogue[g].family == "artifact-failure"]
    if not terminal:
        assert type(result) is AuditedTrace, result
        assert len(result.records) == 4
        assert [dict(record.hits).get(failure, False) for record in result.records] == [False, False, True, False]
        if statement == 'assert(0) else;':
            null_start = source.index(statement) + len(statement) - 1
            null_entry, = [g for g in catalogue if catalogue[g].family == "location"
                          and dict(catalogue[g].provenance)["start"] == null_start]
            assert dict(result.records[2].hits)[null_entry] is True
        return
    assert type(result) is api.RunFailure, result
    assert result.origin is api.FailureOrigin.CANDIDATE, result.as_dict()
    assert result.disposition is api.FailureDisposition.SCORE_ZERO
    assert result.reason.kind == "rtl-source-failure"
    details = thaw_record_value(result.reason.detail)
    prefix = read_native_prefix(details["source_evidence"], stimulus, catalogue, expected_plan=prepared.plan)
    assert prefix.phase == phase
    assert len(prefix.completed) == (0 if phase == 'startup' else 2)
    assert 'replay_prefix_refusal' not in details, details.get('replay_prefix_refusal')
    replay = details['replay_prefix']
    assert len(replay['records']) == len(prefix.completed)
    assert replay['interrupted'] is True and replay['stimulus'] == stimulus.fingerprint
    assert (replay['pending_sample'] is None) == (phase in {'startup', 'control'})
    if selected_startup:
        assert replay['initialization'] == startup.as_dict()
        assert replay['state_widths'] == [['q', 8]]
        assert 'initial_state' not in replay  # No post-startup read took place.
    assert failure in prefix.pending.goals
    assert all(failure not in row.goals for row in prefix.completed)
    assert details["goals"] == [failure]
    if statement in {"assert(0);", "assert(0) else;"}:
        null_start = source.index(statement) + len(statement) - 1
        null_entry, = [g for g in catalogue if catalogue[g].family == "location"
                       and dict(catalogue[g].provenance)["start"] == null_start]
        # A failed condition enters an explicit null failure action, never
        # the null success action. Record that entry before backend shutdown.
        assert (null_entry in prefix.pending.goals) == ("else" in statement)
    suffix_goals = {g for g in catalogue
                    if dict(catalogue[g].provenance)["start"] == source.rindex("if (q==2)")}
    assert {(catalogue[g].family, catalogue[g].event) for g in suffix_goals} == {
        ("control-choice", "true"), ("control-choice", "false"), ("location", "entry")}
    assert not suffix_goals.intersection(prefix.pending.goals)


@pytest.mark.cosim
@pytest.mark.parametrize('phase', ['startup', 'advance'])
@pytest.mark.parametrize('atomic', [False, True])
def test_terminal_prefix_before_slots_and_at_clock_return(tmp_path, monkeypatch, phase, atomic):
    test_real_source_failure_keeps_prefix_and_attributes_only_observed_violation(
        tmp_path, monkeypatch, '$fatal(1,"intentional");', True, '', phase, atomic)


@pytest.mark.cosim
@pytest.mark.parametrize('atomic', [False, True])
def test_terminal_prefix_retains_successfully_installed_analysis_startup(tmp_path, monkeypatch, atomic):
    test_real_source_failure_keeps_prefix_and_attributes_only_observed_violation(
        tmp_path, monkeypatch, '$fatal(1,"intentional");', True, '', 'startup', atomic, selected_startup=True)
