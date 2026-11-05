"""Public ablations: a branch on either side can reveal a baseline-missed bug.

These are deliberately constructed positive controls, not dataset accuracy
measurements. The unchanged public default baseline must miss the constant;
each isolated direction then receives the same declared inputs and time budget.
"""

import json
import pytest

from behave.sim import api

pytestmark = [pytest.mark.isolation, pytest.mark.eda]
RARE = 0x63815DB7


@pytest.mark.parametrize("origin", ("behavior", "rtl"))
@pytest.mark.parametrize("mode", ("baseline", "behavior", "rtl", "dual"))
def test_each_direction_finds_its_own_native_confirmed_counterexample(tmp_path, origin, mode):
    c = api.TaskContract([
        api.Endpoint("in", "in", [api.Field("a", api.FiniteType(32), "in_a")],
            api.settled("in"), None, kind=api.Kind.TRANSACTION),
        api.Endpoint("out", "out", [api.Field("y", api.FiniteType(32), "out_y")],
            api.settled("out"), None, kind=api.Kind.TRANSACTION, answers_per_transaction=1),
    ], [], protocol_instances=[api.ProtocolInstance("compute", ("in", "out"))])
    body = '        return {"out_y": (in_a * 2) & 0xffffffff}\n'
    if origin == "behavior":
        body = f'        if in_a == {RARE}:\n            return {{"out_y": 52}}\n' + body
    source = 'class M:\n    def reset(self): pass\n    def process(self, in_a):\n' + body
    reference = api.load_reference(api.ISOLATED_REFERENCE_ADAPTER,
        {"behavior.py": source.encode()}, config={"class": "M"})
    rtl_body = "out_y = in_a << 1;"
    if origin == "rtl":
        rtl_body = f"if (in_a == 32'd{RARE}) out_y = 32'd52; else " + rtl_body
    rtl = "module M(input [31:0] in_a, output reg [31:0] out_y); always_comb begin " + rtl_body + " end endmodule\n"
    path = tmp_path / "rtl.sv"
    path.write_text(rtl)
    baseline = api.build_baseline(c)
    assert all(value[0] != RARE for seed in baseline.stimuli
               for value in dict(seed.queues)["in"])
    spec = baseline.generation_spec
    task = api.admit(c, api.realization_of_rtl_source(
        rtl, "two-direction control", top="M", contract=c), reference=reference)
    with api.CpuPool(2) as cpu, api.cpu_scope(cpu.endpoint):
        handle = api.prepare_rtl(task, api.RTLConfig(path, "M", tmp_path / "rtl"), spec)
        providers = handle.generation_providers(reference, workdir=tmp_path / "search")
        selected = {"baseline": (), "behavior": providers[:1],
                    "rtl": providers[1:], "dual": providers}[mode]
        result = api.handle_command(handle.command_request(
            run_directory=tmp_path / "invocation", reference=reference,
            baseline=api.BaselinePolicy(), providers=selected,
            probe_budget=0 if mode == "baseline" else None,
            search_policy=None if mode == "baseline" else api.SearchPolicy(time_budget_s=30)))
        (tmp_path / "control_result.json").write_text(json.dumps(result.as_dict(), indent=2))
        assert result.status is api.CommandStatus.EVALUATED, result.as_dict()
        assert result.decision.authoritative, result.as_dict()
        detects = mode in (origin, "dual")
        assert result.decision.passed is not detects, result.as_dict()
        if detects:
            assert any(RARE in [value[0] for value in dict(seed.queues)["in"]]
                       for search in result.generated.searches for seed in search.generated)
            assert result.decision.as_dict()["evidence"]["mismatch_count"] > 0
        assert cpu.snapshot()["reserved"] == 0 and not cpu.snapshot()["quarantined"]
