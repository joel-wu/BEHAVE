"""Automatic public search, not a hand-invoked query, reaches native categories."""

import json

import pytest

from behave.sim import api

from .test_rtl_source_query import _goal
from .test_rtl_source_scheduler import _lower


@pytest.mark.eda("slang")
@pytest.mark.solver
@pytest.mark.parametrize("bound", ["2", "a"])
def test_loop_proofs_are_requested_by_queries_not_native_catalogue(tmp_path, monkeypatch, bound):
    from behave.sim.rtl import source_loops
    from behave.sim.rtl.shared_frontend import parse_observation_source
    from behave.sim.rtl.source_goals import manifest_of
    from behave.sim.rtl.source_plan import plan_source
    from behave.sim.rtl.source_scheduler import lower_source_scheduler

    from .test_rtl_observation_frontend import _installed

    source = ("module M(input clk,input [3:0] a,output reg [3:0] y); "
              f"always @(posedge clk) for(int i=0;i<{bound};i++) y<=a; endmodule")
    (tmp_path / "design.sv").write_text(source)
    (cst, ast), sensitivity = parse_observation_source(tmp_path, "M", run=_installed)
    calls = []
    prove = source_loops.static_loop

    def counted(*args, **kwargs):
        calls.append(args[2])
        return prove(*args, **kwargs)

    monkeypatch.setattr(source_loops, "static_loop", counted)
    plan = plan_source(source, cst, semantic_document=ast, sensitivity_document=sensitivity,
                       filename="design.sv", enumerate_iterations=False)
    plan.native()
    declared = manifest_of(plan.declarations, filename="design.sv").as_dict()
    assert not calls and not plan.loops
    lower_source_scheduler(source, ast, plan, sensitivity, filename="design.sv")
    # The source loop's query-bound proof is shared, including a dynamic
    # finite input bound; native catalogue preparation requests none.
    assert len(calls) == 1
    assert manifest_of(plan.declarations, filename="design.sv").as_dict() == declared
    assert not plan.loops  # Native occurrence cardinality was not expanded.


@pytest.mark.cosim
@pytest.mark.parametrize("terminal,header", (
    (False, None), (True, None), ('error', None), ('default', None),
    ('pass', None), ('null', None), ('argument', None), ('inner-error', None),
    (False, "int i=0;i<a[1:0];i++"),
    (False, "int i=0;i<(a&3);i++"), (False, "logic [2:0] i=0;i<(a&3);i++"),
    (False, "while"), (False, "repeat"), (False, "do"),
    (False, "for-transfer"), (False, "while-transfer"),
    (False, "repeat-transfer"), (False, "do-transfer")),
    ids=("recovering", "terminal", "error", "default", "pass", "null", "argument", "inner-error", "runtime-loop", "masked-loop", "packed-loop", "while-loop",
         "repeat-loop", "do-loop", "for-transfer", "while-transfer", "repeat-transfer", "do-transfer"))
def test_public_search_confirms_structural_categories_and_keeps_local_unsat_goals(
        tmp_path, record_property, terminal, header):
    source = """module M(input clk,rst,in_valid,input [15:0] a,
      output reg out_valid,output reg [15:0] y);
      always @(posedge clk) begin
        if(rst) begin y<=0; out_valid<=0; end
        else begin
          out_valid<=in_valid;
          if(in_valid) begin
            y<=a;
            if(a==16'hc35b && a[0]) begin
              for(int i=0;i<2;i++) y <= a;
            end
            assert(a!=16'hc35b) else y<=a;
          end
        end
      end
    endmodule"""
    failure_form = terminal
    terminal = failure_form not in (False, 'null')
    if failure_form is not False:
        statement = {True: 'if(a==16\'hc35b) $fatal(1,"intentional");',
                     'error': 'if(a==16\'hc35b) $error("intentional");',
                     'default': 'assert(a!=16\'hc35b);',
                     'pass': 'assert(a!=16\'hc35b) y<=a;',
                     'null': 'assert(a!=16\'hc35b) else;',
                     'argument': 'if(a==16\'hc35b) $fatal(1,"value=%0d",evaluated());',
                     'inner-error': 'if(a==16\'hc35b) $fatal(1,"outer=%0d",evaluated());'}[failure_form]
        source = source.replace('assert(a!=16\'hc35b) else y<=a;', statement)
    if failure_form in {'argument', 'inner-error'}:
        action = 'if(a[0]) diagnostic=a+1; else diagnostic=a;'
        if failure_form == 'inner-error':
            action += '$error("inner");'
        source = source.replace('      always',
            '      reg [15:0] diagnostic; function automatic int evaluated; '
            + action + ' return diagnostic; endfunction\n      always', 1)
    loop_keyword = "for"
    transfers = header is not None and header.endswith('-transfer')
    if transfers:
        source = source.replace("always @(posedge clk)", "integer i; always @(posedge clk)")
        loop_keyword = header.split('-')[0]
        body = 'begin i++; if(i==1) continue; y <= a; break; end'
        loop = {'for': 'for(i=0;i<4;i++) begin if(i==0) continue; y <= a; break; end',
                'while': f'while(i<4) {body}', 'repeat': f'repeat(4) {body}',
                'do': f'do {body} while(i<4);'}[loop_keyword]
        source = source.replace("for(int i=0;i<2;i++) y <= a;", "begin i=0; " + loop + " end")
    elif header in {"while", "do"}:
        source = source.replace("always @(posedge clk)", "integer i; always @(posedge clk)")
        loop = ("while(i<(a&3)) begin y <= a; i++; end" if header == "while" else
                "do begin y <= a; i++; end while(i<(a&3));")
        source = source.replace("for(int i=0;i<2;i++) y <= a;", "begin i=0; " + loop + " end")
        loop_keyword = header
    elif header == "repeat":
        source = source.replace("for(int i=0;i<2;i++) y <= a;", "repeat(a&3) y <= a;")
        loop_keyword = header
    elif header is not None:
        source = source.replace("int i=0;i<2;i++", header)
    kind = api.FiniteType(16)
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", kind, "a")],
                     api.sampled_edge("in", valid="in_valid"), "c", kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", kind, "y")],
                     api.sampled_edge("out", valid="out_valid"), "c", kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1)], [api.ClockDomain("c", "clk", "rst")],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b'class M:\n    def reset(self):\n        pass\n'
                        b'    def process(self,a):\n        return {"y":a}\n'}, config={"class": "M"})
    path = tmp_path / "design.sv"
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top="M", contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "build"))
    _machine, plan = _lower(tmp_path, source)
    expected = {family: _goal(source, plan, text, event, family) for family, text, event in (
        ("location", "y <= a", "entry"),
        ("control-choice", "a==16'hc35b", "true"),
        ("boolean-expression", "a==16'hc35b", "true"),
        ("loop-activation", loop_keyword, "nonzero"),
        ("artifact-failure", "$fatal" if failure_form is True or failure_form == 'argument' else
         "$error" if failure_form in {'error', 'inner-error'} else "assert", "failure"))}
    if transfers:
        expected.update({transfer: _goal(source, plan, transfer, 'entry', 'location')
                         for transfer in ('break', 'continue')})
    zero_loop = _goal(source, plan, loop_keyword, "zero", "loop-activation")
    declared = handle.frontend_catalogue.as_dict()
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=3))
    result = api.handle_command(handle.command_request(
        run_directory=tmp_path / "result", baseline=baseline, reference=reference,
        providers=handle.generation_providers(reference, workdir=tmp_path / "search")[1:],
        # Explicit experiment cap; production still uses its shared time budget.
        search_policy=api.SearchPolicy(time_budget_s=60, max_probes=8), run_analysis=False))
    (tmp_path / "result.json").write_text(json.dumps(result.as_dict(), indent=2))
    assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
    assert result.decision.authoritative and not result.decision.passed
    if not terminal:
        assert result.decision.as_dict()["evidence"]["mismatch_count"] == 0
    assert handle.frontend_catalogue.as_dict() == declared
    search, = result.generated.searches
    before = set(search.telemetry["baseline_coverage"]["hits"])
    after = set(search.telemetry["coverage"]["hits"])
    native_hits = set()
    for run in result.evaluation.results:
        if run.trace is not None:
            native_hits.update(goal for row in run.trace.records for goal, hit in row.hits if hit is True)
        elif run.run_failure.observed is not None:
            native_hits.update(run.run_failure.observed.catalogue)
    assert after <= native_hits  # A solver result alone cannot advance the ledger.
    assert not set(expected.values()) & before
    assert set(expected.values()) <= after
    if failure_form == 'inner-error':
        outer_failure = _goal(source, plan, '$fatal', 'failure', 'artifact-failure')
        assert outer_failure in handle.frontend_catalogue
        assert outer_failure not in after | native_hits
    assert zero_loop in handle.frontend_catalogue and zero_loop not in after
    assert result.analysis is None  # No whole-domain unreachability claim.
    record_property("source_categories", json.dumps({
        "confirmed": sorted(expected), "baseline_hits": len(before), "new_hits": len(after - before),
        "probes": search.telemetry["probes"], "termination": search.telemetry["termination"],
        "retained_unhit": zero_loop}))
