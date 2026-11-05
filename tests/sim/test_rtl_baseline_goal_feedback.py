"""Public baseline feedback feeds the actual source Select/Probe/native loop."""

import json

import pytest

from behave.sim import api
from behave.sim.rtl.artifact import RTLArtifact


@pytest.mark.cosim
@pytest.mark.parametrize("wrong", [False, True])
@pytest.mark.parametrize(("clocked", "stateful"), [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("unavailable", [False, True])
def test_public_baseline_is_unchanged_and_source_search_confirms_new_hits(
        tmp_path, monkeypatch, wrong, clocked, stateful, unavailable, record_property):
    integer = api.FiniteType(32)
    contract = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", integer, "a")],
                     api.sampled_edge("in", valid="in_valid") if clocked else api.settled("in"),
                     "c" if clocked else None, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", integer, "y")],
                     api.sampled_edge("out", valid="out_valid") if clocked else api.settled("out"),
                     "c" if clocked else None, kind=api.Kind.TRANSACTION,
                     answers_per_transaction=1)],
        [api.ClockDomain("c", "clk", "rst")] if clocked else [],
        protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))])
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": b'class M:\n    def reset(self):\n        pass\n'
                        b'    def process(self,a):\n        return {"y":a}\n'},
        config={"class": "M"})
    predicate = "a==(prior ^32'h12345678)" if stateful else "a==32'h12345678"
    source = f'''module M(input [31:0] a,output reg [31:0] y);
      always_comb begin
        if(a==32'h12345678) y=a;
        else y=a ^ 32'd{int(wrong)};
      end
    endmodule'''
    if clocked:
        source = f'''module M(input clk,rst,in_valid,input [31:0] a,
            output reg out_valid,output reg [31:0] y);
          {"reg [31:0] prior;" if stateful else ""}
          always @(posedge clk) begin
            if(rst) begin out_valid<=0; y<=0; {"prior<=37;" if stateful else ""} end
            else begin
              out_valid<=in_valid;
              if(in_valid) begin
                {"prior<=a;" if stateful else ""}
                if({predicate}) y<=a;
                else y<=a ^ 32'd{int(wrong)};
              end
            end
          end
        endmodule'''
    path = tmp_path / 'design.sv'
    path.write_text(source)
    task = api.admit(contract, api.realization_of_rtl_source(source, top='M', contract=contract),
                     reference=reference)
    handle = api.prepare_rtl(task, api.RTLConfig(path, 'M', tmp_path / 'build'))
    catalogue = handle.frontend_catalogue
    declaration = catalogue.as_dict()
    from .test_rtl_source_query import _goal
    from .test_rtl_source_scheduler import _lower

    _machine, plan = _lower(tmp_path, source)
    choices = {_goal(source, plan, predicate)}
    assert len(choices) == 1
    baseline = api.build_baseline(contract, policy=api.BaselinePolicy(case_limit=3))
    assert all(seed.depth('in') == 100 for seed in baseline.stimuli)
    assert all(value != 0x12345678 for seed in baseline.stimuli
               for _, queue in seed.queues for value, in queue)

    from behave.sim.engines.rtl.generation import RTLGenerationProvider

    batches, captures, requests = [], [], []
    original = RTLArtifact._traces_per_process
    original_probe = RTLGenerationProvider.probe

    def record(artifact, bound, seeds, *args, **kwargs):
        seeds = tuple(seeds)
        batches.append(tuple(seed.identity_bytes for seed in seeds))
        captures.append(kwargs["_capture_query_boundaries"])
        return original(artifact, bound, seeds, *args, **kwargs)

    monkeypatch.setattr(RTLArtifact, '_traces_per_process', record)

    def probe(provider, seed, request):
        requests.append(request.targets)
        return original_probe(provider, seed, request)

    monkeypatch.setattr(RTLGenerationProvider, 'probe', probe)
    if unavailable:
        from behave.sim.core.refusal import Refusal, Refused

        def refuse_source(_artifact, *, expected_identity, timeout_s):
            raise Refused(Refusal("test-source-query-unavailable", "injected optional frontend failure"))

        monkeypatch.setattr('behave.sim.rtl.elaboration.source_documents', refuse_source)
    results = []
    for searching in (False, True):
        before = len(batches)
        result = api.handle_command(handle.command_request(
            run_directory=tmp_path / str(searching), baseline=baseline, reference=reference,
            providers=handle.generation_providers(reference, workdir=tmp_path / 'search')[1:],
            search_policy=api.SearchPolicy(time_budget_s=60 if searching else 0),
            run_analysis=False))
        (tmp_path / f'{searching}.json').write_text(json.dumps(result.as_dict(), indent=2))
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.decision.authoritative and result.decision.passed is not wrong
        assert result.evaluation.complete or (wrong and result.evaluation.failure_witness is not None)
        assert handle.frontend_catalogue.as_dict() == declaration
        runs = {run.stimulus.identity_bytes: run.trace for run in result.evaluation.results}
        baseline_traces = [runs[seed.identity_bytes] for seed in baseline.stimuli
                           if seed.identity_bytes in runs]
        assert all(trace.source_evidence is not None for trace in baseline_traces)
        assert all((trace.query_boundaries is not None) == searching for trace in baseline_traces)
        continuous = set(dict(dict(result.evaluation.telemetry)["replay_suite"])["continuous"])
        continuous_traces = [run.trace for run in result.evaluation.results
                             if run.stimulus.fingerprint in continuous]
        assert all(trace.source_evidence is not None and trace.query_boundaries is None
                   for trace in continuous_traces)
        if searching and stateful:
            from behave.sim.rtl.source_boundary_capture import boundaries_of

            assert any(sample["prior"] == 37 for trace in baseline_traces
                       for sample in boundaries_of(trace.query_boundaries).values())
        # Search must not piggyback on a reset-restoration declaration or change
        # the architectural state monitored for scoring.
        assert all(not trace.initial_state and all(not row.state for row in trace.records)
                   for trace in baseline_traces)
        hits = {goal for trace in baseline_traces for row in trace.records
                for goal, hit in row.hits if hit is True}
        native_hits = hits & set(catalogue)
        assert native_hits and not choices & hits
        for seed in baseline.stimuli:
            assert sum(seed.identity_bytes in batch for batch in batches[before:]) == 1
            # Continuous workloads can run before evaluate_generated; its call
            # stack is not the owner of the replay phase. Check actual baseline
            # membership here and continuous native records above instead.
            assert all(capture == searching for batch, capture in
                       zip(batches[before:], captures[before:], strict=True)
                       if seed.identity_bytes in batch)
        if searching and not wrong:
            search, = result.generated.searches
            feedback = search.as_dict()['telemetry']['baseline_coverage']
            assert set(catalogue) <= set(feedback['declared'])
            assert set(feedback['hits']) & set(catalogue) == native_hits
            assert choices <= set(feedback['unhit'])
            assert set(catalogue) <= set(feedback['targetable'])
            assert search.telemetry['probes'] > 0, search.as_dict()
            assert all(not native_hits.intersection(targets) for targets in requests)
            reached = {goal for run in result.evaluation.results
                       for row in run.trace.records for goal, hit in row.hits if hit is True}
            if unavailable:
                assert not choices & reached
            else:
                assert choices <= reached, search.as_dict()
            record_property('source_search', json.dumps({
                'clocked': clocked, 'stateful': stateful, 'injected_failure': unavailable,
                'baseline_stimuli': len(baseline.stimuli), 'baseline_depth': 100,
                'baseline_slots': sum(len(trace.records) for trace in baseline_traces),
                'query_boundaries': sum(len(trace.query_boundaries['samples']) for trace in baseline_traces),
                'source_goals': len(catalogue), 'baseline_source_hits': len(native_hits),
                'new_source_hits': len((reached & set(catalogue)) - native_hits),
                'probes': search.telemetry['probes'],
                'termination': search.telemetry['termination']}))
        results.append({seed.fingerprint: runs[seed.identity_bytes].as_dict()['records']
                        for seed in baseline.stimuli if seed.identity_bytes in runs})
    # A confirmed negative may stop dispatch with different in-flight tails.
    # Every trace actually run in both profiles must still agree byte-for-byte.
    common = results[0].keys() & results[1].keys()
    assert common
    assert all(results[0][key] == results[1][key] for key in common)
    if not wrong:
        assert results[0] == results[1]
