"""Schedule projections never infer payload values or active reset-state equality."""

import itertools
import json
from types import SimpleNamespace

import pytest

from behave.sim import api
from behave.sim.core.goal_provider import EXACT_BOUNDED_HIT, LOCAL_GUIDANCE
from behave.sim.core.monitor import Monitor, monitor_for
from behave.sim.core.reset_monitor import reset_schedule_scenarios
from behave.sim.core.stimulus import AuditedTrace, Completion, SlotRecord
from behave.sim.engines import probing
from behave.sim.engines.rtl.generation import RTLGenerationProvider
from behave.sim.engines.rtl.schedule_monitor_query import ScheduleMonitorQuery
from behave.sim.search_policy import SearchBudget, search_scope
from tests.sim.test_protocol_adapters import needs_verilator
from tests.sim.test_settled_protocol import contract


def _contract(*, ratio=1, matching=api.Matching.ORDERED):
    image = contract().as_dict()
    image["endpoints"][0]["max_outstanding"] = 1
    image["endpoints"][0]["response_deadline"] = 1
    image["endpoints"][1]["answers_per_transaction"] = ratio
    image["endpoints"][1]["matching"] = matching.value
    return api.TaskContract.from_dict(image)


def _trace(c, inputs, outputs, **options):
    seed = api.reset_then(c, [(value,) for value in inputs], drain=0)
    trace = AuditedTrace(seed, records=tuple(
        SlotRecord(accepted={"in": (value,), "out": (output,)}, outputs={"out_y": output})
        for value, output in zip(inputs, outputs, strict=True)), **options)
    return seed, trace


def test_settled_capabilities_keep_all_declarations():
    monitor = Monitor(_contract())
    names = set()
    for provider in monitor.goal_providers():
        assert provider.binding(LOCAL_GUIDANCE) is not None
        assert provider.binding(EXACT_BOUNDED_HIT) is not None
        names.update(provider.catalogue)
    assert names == set(monitor.goals())
    assert names == {"monitor.input-completion", "monitor.output-without-transaction",
                     "monitor.outstanding-exceeded", "monitor.response-deadline"}


@pytest.mark.parametrize("ratio", (1, 2))
def test_every_local_goal_matches_exhaustive_small_payload_replays(ratio):
    c = _contract(ratio=ratio)
    seed, trace = _trace(c, (0,) * 6, (0,) * 6)
    query = ScheduleMonitorQuery(c, seed, trace)
    fixed_hits = set(query.hits)
    # All 2-bit input/output combinations for the selected second transaction;
    # include a positive outstanding/deadline control, not only all-false goals.
    for value, output in itertools.product(range(4), repeat=2):
        varied, observed = _trace(c, (0, value, 0, 0, 0, 0), (0, output, 0, 0, 0, 0))
        actual = Monitor(c).check(varied, observed)
        assert {(hit.goal, hit.slot) for hit in actual.hits} == fixed_hits
    for goal, start in itertools.product(query.goals, range(6)):
        answer = query.probe(SimpleNamespace(targets=(goal,)),
                             SimpleNamespace(start=start, stop=start + 1))
        hit = (goal, start) in fixed_hits
        assert answer.outcome is (probing.Outcome.SAT if hit else probing.Outcome.LOCAL_UNSAT)
        assert answer.scope is probing.Scope.RTL_LOCAL_HIT
        assert answer.candidate is (seed if hit else None)
        assert "not a global" in answer.detail
    assert bool(fixed_hits) is (ratio == 2)
    if ratio == 2:
        # One response per sample retires a two-response request every other
        # sample. The third request's first response is two samples late.
        assert fixed_hits == ({("monitor.outstanding-exceeded", i) for i in range(2, 6)}
                              | {("monitor.response-deadline", i) for i in (4, 5)})
        assert Monitor(c).check(seed, trace).unresolved  # terminal pending is not a local unknown


def test_independent_settled_interfaces_use_their_own_declared_response_counts():
    endpoints = []
    instances = []
    for name, ratio in (("left", 1), ("right", 2)):
        endpoints.extend((
            api.Endpoint(name + "_in", "in", [api.Field("x", api.FiniteType(8), name + "_x")],
                         api.settled("in"), None, kind=api.Kind.TRANSACTION, max_outstanding=1),
            api.Endpoint(name + "_out", "out", [api.Field("y", api.FiniteType(8), name + "_y")],
                         api.settled("out"), None, kind=api.Kind.TRANSACTION,
                         answers_per_transaction=ratio)))
        instances.append(api.ProtocolInstance(name, (name + "_in", name + "_out")))
    c = api.TaskContract(endpoints, (), protocol_instances=instances)
    seed = api.Stimulus({"left_in": [(0,)] * 3, "right_in": [(1,)] * 3},
                       [api.EventSlot(offers=("left_in", "right_in")) for _ in range(3)])
    trace = AuditedTrace(seed, records=[SlotRecord(
        accepted={"left_in": (0,), "right_in": (1,), "left_out": (4,), "right_out": (9,)},
        outputs={"left_y": 4, "right_y": 9}) for _ in range(3)])
    query = ScheduleMonitorQuery(c, seed, trace)
    assert set(query.hits) == {("monitor.outstanding-exceeded", 2)}
    assert {hit.endpoint for hit in Monitor(c).check(seed, trace).hits} == {"right_in"}


def test_payload_sensitive_or_unfinished_runs_cannot_use_schedule_query():
    c = _contract()
    seed, trace = _trace(c, (0, 1), (0, 2), completion=Completion.INCOMPLETE)
    with pytest.raises(api.Refused, match="incomplete-boundary"):
        ScheduleMonitorQuery(c, seed, trace)
    c = _contract(matching=api.Matching.CONTENT)
    seed, trace = _trace(c, (0,), (0,))
    with pytest.raises(api.Refused, match="payload-dependent"):
        ScheduleMonitorQuery(c, seed, trace)
    c = _contract()
    seed, trace = _trace(c, (0,), (0,))
    query = ScheduleMonitorQuery(c, seed, trace)
    with pytest.raises(api.Refused, match="query-owner"):
        query.probe(SimpleNamespace(targets=("source.branch",)),
                    SimpleNamespace(start=0, stop=1))


@pytest.mark.parametrize("edge", ("rising", "falling"))
@pytest.mark.parametrize("ratio", (1, 2))
def test_clocked_projection_preserves_reset_and_declared_response_counts(edge, ratio):
    from tests.sim.test_sampled_protocol import _contract, _Doubling, _realization
    from tests.support.device_boundary import run_device

    image = _contract(edge=edge, deadline=1).as_dict()
    image["endpoints"][0]["max_outstanding"] = 1
    image["endpoints"][1]["answers_per_transaction"] = ratio
    c = api.TaskContract.from_dict(image)
    task = api.admit(c, _realization(c))
    controls = []
    for i in range(12):
        reset = i in (0, 1, 7, 8)
        controls.append(api.EventSlot(reset=reset, offers=() if reset else ("in",),
                                      controls={"rst": int(reset)}))
    fixed = None
    for value in range(4):
        seed = api.Stimulus({"in": [(value,)] * 8}, controls)
        trace = run_device(task, seed, _Doubling())
        query = ScheduleMonitorQuery(c, seed, trace)
        hits = set(query.hits)
        if fixed is None:
            fixed = hits
        assert hits == fixed
        for goal, start in itertools.product(query.goals, range(len(controls))):
            answer = query.probe(SimpleNamespace(targets=(goal,)),
                                 SimpleNamespace(start=start, stop=start + 1))
            assert answer.outcome is (probing.Outcome.SAT if (goal, start) in hits
                                      else probing.Outcome.LOCAL_UNSAT)
    assert bool(fixed) is (ratio == 2)


def test_multiclock_projection_does_not_absorb_reset_restoration():
    endpoints = []
    clocks = []
    for domain, ratio in (("a", 1), ("b", 2)):
        clocks.append(api.ClockDomain(domain, domain + "clk", domain + "rst",
                                      reset_restores=True, reset_hold_cycles=1))
        endpoints.extend((
            api.Endpoint(domain + "in", "in", [api.Field("x", api.FiniteType(2), domain + "x")],
                         api.sampled_edge("in"), domain, kind="transaction", max_outstanding=1),
            api.Endpoint(domain + "out", "out", [api.Field("y", api.FiniteType(2), domain + "y")],
                         api.sampled_edge("out"), domain, kind="transaction", answers_per_transaction=ratio)))
    c = api.TaskContract(endpoints, clocks)
    monitor = monitor_for(c)
    goals = monitor.payload_independent_goals()
    assert goals and goals == {g for g in monitor.goals() if not g.startswith("monitor.reset-")}
    pattern = [(('a', 'b'), ('a', 'b')), (('a',), ()), (('b',), ()),
               (('a', 'b'), ('a',)), (('a', 'b'), ()), (('b',), ('b',)),
               (('a', 'b'), ()), (('a',), ())]
    fixed = None
    for value in range(4):
        queues, slots, rows = {"ain": [], "bin": []}, [], []
        for ticks, resets in pattern:
            active = set(ticks) - set(resets)
            accepted, outputs = {}, {}
            for domain in active:
                queues[domain + "in"].append((value,))
                accepted.update({domain + "in": (value,), domain + "out": (3-value,)})
                outputs[domain + "y"] = 3-value
            slots.append(api.EventSlot(ticks=ticks, resets=resets,
                offers=tuple(sorted(domain + "in" for domain in active)),
                controls={domain + "rst": int(domain in resets) for domain in ('a', 'b')}))
            rows.append(SlotRecord(accepted=accepted, outputs=outputs))
        seed = api.Stimulus(queues, slots)
        trace = AuditedTrace(seed, records=rows)
        query = ScheduleMonitorQuery(c, seed, trace)
        scenarios = {"monitor.reset-restoration.a.scenario", "monitor.reset-restoration.b.scenario"}
        assert query.goals == goals | scenarios
        assert set(reset_schedule_scenarios(c, seed)) == {
            (hit.goal, hit.slot) for hit in monitor.check(seed, trace).hits if hit.goal in scenarios}
        hits = set(query.hits)
        if fixed is None:
            fixed = hits
        assert hits == fixed
        assert all(name in goals | scenarios for name, _ in hits)
        # Missing reset-state evidence cannot block independent count facts,
        # but it also cannot turn either restoration goal into a local answer.
        assert monitor.check(seed, trace).unresolved
        with pytest.raises(api.Refused, match="query-owner"):
            query.probe(SimpleNamespace(targets=("monitor.reset-restoration.a.violation",)),
                        SimpleNamespace(start=0, stop=len(slots)))


@pytest.mark.parametrize("input_valid,output_valid", ((True, False), (False, True), (True, True)))
def test_valid_only_signals_do_not_inherit_plain_sampling_projection(input_valid, output_valid):
    from tests.sim.test_sampled_protocol import _contract

    c = _contract(input_valid=input_valid, output_valid=output_valid)
    assert not Monitor(c).payload_independent_goals()


@pytest.mark.parametrize("later_reset", (False, True))
def test_reset_activation_projection_does_not_use_observed_state_equality(later_reset):
    from tests.sim.test_reset_restoration_monitor import _contract

    c = _contract()  # ready/valid; transfer counts cannot be projected
    flags = (True, True, False, later_reset, later_reset, False)
    seed = api.Stimulus({"in": ()}, [api.EventSlot(
        reset=reset, controls={"rst": int(reset), "out_ready": 0}) for reset in flags])
    scenario = "monitor.reset-restoration.clk.scenario"
    violation = "monitor.reset-restoration.clk.violation"
    interval = SimpleNamespace(start=2, stop=6)
    # Incomplete state, correct restoration and incorrect restoration all use
    # the identical schedule predicate. None certifies state equality here.
    for states in (None, (0, 0, 1, 0, 0, 1), (0, 0, 1, 1, 1, 1)):
        trace = AuditedTrace(seed, records=[SlotRecord(
            accepted={"in": None, "out": None},
            outputs={"in_ready": 0, "out_valid": 0, "y": 0},
            state={} if states is None else {"q": states[i]}) for i in range(6)])
        query = ScheduleMonitorQuery(c, seed, trace)
        assert reset_schedule_scenarios(c, seed) == (((scenario, 4),) if later_reset else ())
        answer = query.probe(SimpleNamespace(targets=(scenario,)), interval)
        assert answer.outcome is (probing.Outcome.SAT if later_reset else probing.Outcome.LOCAL_UNSAT)
        if later_reset:
            assert query.goals == {scenario}
            with pytest.raises(api.Refused, match="query-owner"):
                query.probe(SimpleNamespace(targets=(violation,)), interval)
        else:
            assert query.goals == {scenario, violation}
            assert query.probe(SimpleNamespace(targets=(violation,)), interval).outcome is probing.Outcome.LOCAL_UNSAT


@pytest.mark.cosim
@needs_verilator
@pytest.mark.parametrize("edge,gated", (("posedge", False), ("negedge", False), ("posedge", True)))
@pytest.mark.parametrize("restores", (False, True))
def test_native_clocked_projection_avoids_unrelated_datapath_preparation(tmp_path, edge, gated, restores):
    from behave.sim.rtl.artifact import RTLArtifact
    from tests.sim.test_sampled_protocol import _contract

    c = _contract(edge="rising" if edge == "posedge" else "falling")
    if restores:
        image = c.as_dict()
        image["clocks"][0]["reset_restores"] = True
        c = api.TaskContract.from_dict(image)
    rtl = tmp_path / "plain.sv"
    rtl.write_text(f"""module plain(input clk,rst,input [7:0] in_a,output reg [7:0] out_y);
      wire inner_clk = clk & in_a[0];
      always @({edge} {'inner_clk' if gated else 'clk'} or posedge rst)
        if(rst) out_y<=0; else out_y<=in_a+1;
    endmodule""")
    artifact = RTLArtifact(rtl, "plain", tmp_path / "sim")
    goals = set(Monitor(c).goals())
    provider = RTLGenerationProvider.from_artifact(artifact, c, api.GenerationSpec(5, 3, 2, 0),
        artifact.frontend_catalogue(), targetable_goals=tuple(sorted(goals)), workdir=tmp_path / "query")
    seed = api.reset_then(c, [(3,), (7,), (1,)], drain=0)
    selected = provider.select(seed)
    assert isinstance(selected, api.ProbeSelection), selected
    assert set().union(*(set(r.targets) for r in selected.requests)) == goals

    lease = SearchBudget(api.SearchPolicy(time_budget_s=180)).direction(1)
    with search_scope(lease), provider.search_scope(probe_budget=len(selected.requests)):
        for request in selected.requests:
            answer = provider.probe(seed, request)
            assert answer.outcome is probing.Outcome.LOCAL_UNSAT, answer.detail
    assert provider.catalogue_manifest().digest() == selected.catalogue_digest
    if gated and restores:
        # Initial-only reset is answerable. Changing the actual schedule to
        # activate restoration still needs the unavailable complete partition.
        active_seed = api.Stimulus(dict(seed.queues), (*seed.slots, *seed.slots[:2]))
        refused = provider.select(active_seed)
        assert isinstance(refused, api.Refusal)
        assert refused.kind == "rtl-state-clock-ownership-unnamed-state"


@pytest.mark.cosim
@needs_verilator
def test_native_monitor_queries_and_source_witness_share_one_catalogue(tmp_path):
    from behave.sim.rtl.artifact import RTLArtifact
    from behave.sim.rtl.source_goals import is_source_goal

    rtl = tmp_path / "settled.sv"
    rtl.write_text("""module settled(input wire [7:0] in_a, output reg [7:0] out_y);
        always_comb begin
            if (in_a == 8'd37) out_y = in_a ^ 8'd17;
            else out_y = in_a << 1;
        end
    endmodule
    """)
    c = _contract()
    artifact = RTLArtifact(rtl, "settled", tmp_path / "sim")
    catalogue = artifact.frontend_catalogue()
    provider = RTLGenerationProvider.from_artifact(
        artifact, c, api.GenerationSpec(2, 2, 0, 0), catalogue,
        workdir=tmp_path / "query")
    seed = api.reset_then(c, [(7,), (11,)], drain=0)
    selected = provider.select(seed)
    assert isinstance(selected, api.ProbeSelection), selected.as_dict()
    monitor_goals = set(Monitor(c).goals())
    assert not monitor_goals & set(selected.replay.hits)
    requests = [r for r in selected.requests if set(r.targets) <= monitor_goals]
    assert set().union(*(set(r.targets) for r in requests)) == monitor_goals

    answers = []
    lease = SearchBudget(api.SearchPolicy(time_budget_s=180)).direction(1)
    with search_scope(lease), provider.search_scope(probe_budget=len(requests) + 1):
        for request in requests:
            answer = provider.probe(seed, request)
            assert answer.outcome is probing.Outcome.LOCAL_UNSAT, answer.detail
            answers.append({"targets": list(request.targets), "outcome": answer.outcome.value,
                            "scope": answer.scope.value, "detail": answer.detail})
        source_request = next(r for r in selected.requests
                              if all(is_source_goal(catalogue[g]) for g in r.targets)
                              and set(r.targets) - set(selected.replay.hits))
        remaining = tuple(g for g in source_request.targets if g not in selected.replay.hits)
        answer = provider.probe(seed, source_request.with_targets(remaining))
    assert answer.outcome is probing.Outcome.SAT, answer.detail
    candidate = answer.candidate
    assert (37,) in dict(candidate.queues)["in"]
    confirmed = provider.select(candidate, targets=())
    assert isinstance(confirmed, api.ProbeSelection), confirmed.as_dict()
    assert answer.goal in confirmed.replay.hits
    assert candidate.slots == seed.slots
    assert all(not s.ticks and not s.controls for s in candidate.slots)
    assert selected.catalogue_digest == confirmed.catalogue_digest
    (tmp_path / "local-query.json").write_text(json.dumps({
        "catalogue": provider.catalogue_manifest().as_dict(),
        "baseline": selected.as_dict(), "monitor_queries": answers,
        "source_query": {"goal": answer.goal, "outcome": answer.outcome.value,
                         "scope": answer.scope.value, "solve_ms": answer.solve_ms},
        "candidate": candidate.as_dict(), "confirmed": confirmed.as_dict(),
    }, indent=2))
